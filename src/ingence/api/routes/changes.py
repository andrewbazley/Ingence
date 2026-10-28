"""What changed between two runs of one project, read from both and written nowhere.

A person who reviews a run wants to know what the last change to the engine, the Dictionary or
their own answers did. Which words now bind differently? Which conclusions came and went? What do
the flags raise now that they did not before? Every run is kept and every run can be opened
(``storage/catalog.open_catalog`` with a run named). So the answer is two reads and a difference,
and nothing is stored.

A run is chosen by what it is built from, never by its profile. An inference writes the profile of
the run it read (``infer/run.py``: "the Index this run carries was built under the previous run's
depth profile"). So a profile says how the Index was walked and not whether rules ran. A run's
kind is read from what it holds: rules evaluated make an inference. The run's parent is named
beside it, so a person can pair two inferences over one census, or an inference with the one
before it, and know which is which.

The page makes four comparisons. Each gives counts first and a few examples after:

- Bindings moved: a name's component bound to another sense, or to none, grouped by the kind of
  component and, for an extension, by its spelling. The comparison also lists components that are
  new and gone.
- Claims gained and lost, by rule. A claim is its rule, subject, value and standing, so a new
  version of a rule that concludes the same thing is no change.
- Findings raised and cleared, by rule. A finding is its rule, subject and what it raised.
- Candidates gained and lost, by rule. A pair is its rule, its two ends and its kind.

Each comparison opens with one plain sentence of what it amounts to, such as "412 more
conclusions, 3 fewer; most of it format_from_extension (+412)". Beside the sentence are the
answers written to the Record between the two runs, with a before-and-after count for each kind
of record. The tables by rule follow for whoever wants them.

:func:`timeline` gives one timeline of runs, with each answer marked where it took effect. It
lists every run by its name, oldest first. Under each run are the answers that first took effect
in it: written after the run before it began, and no later than it began. It also lists the
answers made while a person was reading the run, and the one comparison the run opens. Answers
written since the latest run began are said to be waiting for the next one. A run on the timeline
opens the two-run comparison. Nothing here writes.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING, Any

from ingence.api.routes.decide import PAGES
from ingence.api.routes.overlay import is_topic_edit
from ingence.api.run_names import project_run_names
from ingence.core.schema import RecordEventKind
from ingence.record.imported import imported_from
from ingence.record.log import read_events
from ingence.record.verdicts import kept_line, shown_on
from ingence.storage.catalog import open_catalog, sql_path_list
from ingence.storage.commit import read_pointer, run_path
from ingence.storage.layout import RUNS_DIR, TABLES

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.storage.catalog import Catalog

__all__ = ["EXAMPLES", "changes", "runs", "timeline"]

#: How many examples each category shows. The counts are exact.
EXAMPLES = 5

#: How many answers the timeline lists under each run. The counts are exact.
TIMELINE_EXAMPLES = 25

#: A claim as an example reads it: its value, and "in doubt" where a defeater left it conflicting.
_STANDING = (
    "coalesce(object_reference, '') || CASE resolution_status WHEN 'resolved' THEN '' "
    "WHEN 'examined_conflicting' THEN ' (in doubt)' ELSE ' (' || resolution_status || ')' END"
)

#: The tables a comparison reads from the earlier run, as views prefixed ``before_``.
_COMPARED = (
    "entries",
    "components",
    "dictionary_bindings",
    "claims",
    "findings",
    "candidates",
)


def _rows(catalog: Catalog, sql: str, parameters: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = catalog.query(sql, parameters or [])
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _described(profile: str, holds_rules: bool, parent: str | None) -> str:
    """A run's kind in plain words, from what it holds and the run it was built from (named)."""
    if holds_rules:
        return f"rules applied to {parent}" if parent else "rules applied"
    if profile == "rebind":
        return "names read again"
    if profile == "census":
        return "census: folders walked, names read"
    return f"dive ({profile}): file contents read"


def runs(project: Path) -> dict[str, Any]:
    """Every run of the project, oldest first, each with its name, kind, parent and counts.

    A run's name and number come from ``run_names``: "Run 3, September 23, 1:00 PM EDT". The
    number orders the runs by when each began, and the identifier is kept beside it.
    """
    directory = project / RUNS_DIR
    pointer = read_pointer(project)
    current = pointer.run_id if pointer is not None else None
    held: list[dict[str, Any]] = []
    for run_directory in sorted(directory.iterdir()) if directory.is_dir() else []:
        if not run_directory.is_dir():
            continue
        with open_catalog(project, run_id=run_directory.name) as catalog:
            record = _rows(catalog, "SELECT run_id, parent_run_id, profile, started_at FROM run")
            counts = {
                name: catalog.count(name)
                for name in ("entries", "claims", "findings", "candidates", "rules")
            }
        held.append(
            {"run_id": run_directory.name, "record": record[0] if record else {}, "counts": counts}
        )
    names = project_run_names(
        project, started_at={item["run_id"]: item["record"].get("started_at") for item in held}
    )
    found: list[dict[str, Any]] = []
    for item in held:
        record, counts = item["record"], item["counts"]
        profile = str(record.get("profile") or "census")
        parent = record.get("parent_run_id")
        parent_name = names.get(str(parent)) if parent else None
        found.append(
            {
                "run_id": item["run_id"],
                "number": names[item["run_id"]]["number"],
                "label": names[item["run_id"]]["label"],
                "began": names[item["run_id"]]["began"],
                "parent_run_id": parent,
                "parent_label": parent_name["label"] if parent_name else None,
                "profile": profile,
                "started_at": record.get("started_at"),
                "inference": counts["rules"] > 0,
                "described": _described(
                    profile,
                    counts["rules"] > 0,
                    _short(parent_name["label"]) if parent_name else parent,
                ),
                "current": item["run_id"] == current,
                "counts": {name: value for name, value in counts.items() if name != "rules"},
            }
        )
    found.sort(key=lambda run: int(run["number"]))
    return {"runs": found, "current": current, "pair": _default_pair(found, current)}


def _short(label: str) -> str:
    """A run's name without its time, "Run 3", for a sentence naming a run beside another."""
    return label.split(",", 1)[0]


def _default_pair(found: list[dict[str, Any]], current: str | None) -> dict[str, str | None]:
    """The two runs a page opens on: the current one, and the inference before it.

    Chosen by what each run is and what it was built from: the latest earlier run that applied
    rules, or failing that the current run's own parent. "Earlier" is by the runs' numbers,
    which order them by when each began.
    """
    after = next((run for run in found if run["run_id"] == current), None)
    if after is None:
        return {"before": None, "after": None}
    earlier = [
        run
        for run in found
        if run["inference"] and run["run_id"] != after["run_id"] and run["number"] < after["number"]
    ]
    before = earlier[-1]["run_id"] if earlier else after["parent_run_id"]
    return {"before": before, "after": after["run_id"]}


def _before_views(catalog: Catalog, project: Path, before: str) -> None:
    """Make the earlier run's tables readable beside the later's, as ``before_<table>``."""
    for spec in TABLES:
        if spec.name not in _COMPARED or spec.spans_runs:
            continue
        fragments = sorted((run_path(project, before) / spec.directory).glob("*.parquet"))
        if fragments:
            statement = (
                f"CREATE OR REPLACE VIEW before_{spec.name} AS "  # noqa: S608
                f"SELECT * FROM read_parquet({sql_path_list(fragments)}, union_by_name = true)"
            )
        else:
            statement = (
                f"CREATE OR REPLACE VIEW before_{spec.name} AS "  # noqa: S608
                f"SELECT * FROM {spec.name} WHERE false"
            )
        catalog.query(statement)


def _gained_and_lost(
    catalog: Catalog, columns: str, table: str, *, examples: int, value: str
) -> dict[str, Any]:
    """Rows of one table present in one run and not the other, by rule, with examples."""
    by_rule: dict[str, dict[str, Any]] = {}
    totals = {"gained": 0, "lost": 0}
    for way, later, earlier, names in (
        ("gained", table, f"before_{table}", "entries"),
        ("lost", f"before_{table}", table, "before_entries"),
    ):
        rows = _rows(
            catalog,
            f"""
            WITH moved AS (
                SELECT {columns} FROM {later} EXCEPT SELECT {columns} FROM {earlier}
            ),
            named AS (
                SELECT m.*, coalesce(e.name, m.subject_id) AS shown,
                       row_number() OVER (
                           PARTITION BY m.rule_id
                           ORDER BY coalesce(e.name, m.subject_id), m.subject_id
                       ) AS pick,
                       count(*) OVER (PARTITION BY m.rule_id) AS n
                FROM moved m
                LEFT JOIN {names} e
                       ON e.entry_id = regexp_replace(m.subject_id, '^folder:', '')
            )
            SELECT rule_id, n, shown, {value} AS said FROM named WHERE pick <= ?
            ORDER BY n DESC, rule_id, pick
            """,  # noqa: S608 - table and column names are this module's constants
            [examples],
        )
        for row in rows:
            held = by_rule.setdefault(
                str(row["rule_id"]),
                {"rule_id": row["rule_id"], "gained": 0, "lost": 0, "examples": []},
            )
            held[way] = int(row["n"])
            held["examples"].append({"way": way, "name": row["shown"], "said": row["said"]})
        totals[way] = sum(int(held[way]) for held in by_rule.values())
    ordered = sorted(
        by_rule.values(), key=lambda held: (-(held["gained"] + held["lost"]), held["rule_id"])
    )
    return {**totals, "by_rule": ordered}


def changes(
    project: Path, *, before: str | None, after: str | None, examples: int = EXAMPLES
) -> dict[str, Any]:
    """Return what changed from ``before`` to ``after``: bindings, claims, findings, pairs.

    Both default to the pair :func:`runs` proposes. Two runs that are one run, or a run the
    project does not hold, are refused, because a comparison of nothing with itself reads as
    "nothing changed" and is not an answer.
    """
    listed = runs(project)
    before = before or listed["pair"]["before"]
    after = after or listed["pair"]["after"]
    known = {run["run_id"]: run for run in listed["runs"]}
    if not before or not after or before not in known or after not in known:
        message = "a comparison names two runs this project holds"
        raise LookupError(message)
    if before == after:
        message = "a comparison names two different runs; a run compared with itself says nothing"
        raise LookupError(message)
    examples = max(1, min(int(examples), 50))
    with open_catalog(project, run_id=after) as catalog:
        _before_views(catalog, project, before)
        found = {
            "before": known[before],
            "after": known[after],
            "answers": _answers_between(project, known[before], known[after]),
            "bindings": _bindings(catalog, examples=examples),
            "claims": _gained_and_lost(
                catalog,
                "rule_id, subject AS subject_id, object_reference, resolution_status",
                "claims",
                examples=examples,
                value=_STANDING,
            ),
            "findings": _renamed(
                _gained_and_lost(
                    catalog,
                    "rule_id, subject AS subject_id, "
                    "array_to_string(list_sort(raised), ', ') AS raised_text",
                    "findings",
                    examples=examples,
                    value="raised_text",
                ),
                {"gained": "raised", "lost": "cleared"},
            ),
            "candidates": _gained_and_lost(
                catalog,
                "rule_id, source_id AS subject_id, target_id, specialization",
                "candidates",
                examples=examples,
                value="'to ' || target_id",
            ),
        }
    found["said"] = said_plainly(found)
    return found


def _instant(spelled: object) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(spelled).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)


def _answers_between(
    project: Path, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, int] | None:
    """The decisions and notes written to the Record after one run began and before the other,
    and the edits of a person's topics apart from both (``edited``: an annotation decides nothing).

    None where either run's start is not known, rather than a count over a window nobody can
    state. Between two runs means after the earlier began and no later than the later began:
    an answer written while a run was being made may or may not be in it, and it is counted with
    the run it could have reached.
    """
    start, end = _instant(before.get("began")), _instant(after.get("began"))
    if start is None or end is None:
        return None
    if end < start:
        start, end = end, start
    decided = noted = edited = 0
    for event in read_events(project, withdrawn=True):
        when = _instant(event.occurred_at)
        if when is None or not (start < when <= end):
            continue
        if is_topic_edit(event):
            # A person's edit of a topic decides nothing: counted apart, as the manifest does.
            edited += 1
        elif event.kind is RecordEventKind.NOTE:
            noted += 1
        else:
            decided += 1
    return {"decided": decided, "noted": noted, "edited": edited}


def _most(section: dict[str, Any], gained: str, lost: str) -> str:
    """Which rule most of a change is, as a clause: "; most of it rule (+412, -3)"."""
    if not section["by_rule"]:
        return ""
    top = section["by_rule"][0]
    moved = int(top[gained]) + int(top[lost])
    if moved * 2 < int(section[gained]) + int(section[lost]) or len(section["by_rule"]) == 1:
        prefix = "all of it" if len(section["by_rule"]) == 1 else "the most in"
    else:
        prefix = "most of it"
    signs = ", ".join(
        part
        for part in (
            f"+{int(top[gained]):,}" if top[gained] else "",
            f"-{int(top[lost]):,}" if top[lost] else "",
        )
        if part
    )
    return f"; {prefix} {top['rule_id']} ({signs})"


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def said_plainly(found: dict[str, Any]) -> list[str]:
    """What a comparison amounts to, one plain sentence per kind of change, the largest first.

    Every figure is one the comparison already counted; nothing here computes a change of its
    own. A kind with nothing changed is said once, at the end, rather than left out, so an empty
    comparison reads as "nothing changed" and not as a page that failed.
    """
    said: list[tuple[int, str]] = []
    unchanged: list[str] = []
    before, after = found["before"]["counts"], found["after"]["counts"]
    entries = int(after.get("entries", 0)) - int(before.get("entries", 0))
    if entries:
        said.append(
            (
                abs(entries),
                f"{abs(entries):,} {'more' if entries > 0 else 'fewer'} "
                f"{'item' if abs(entries) == 1 else 'items'} in the index.",
            )
        )
    bindings = found["bindings"]
    readers = bindings.get("readers") or {}
    reread = bool(readers.get("before")) and readers.get("before") != readers.get("after")
    if bindings["moved"] or bindings["new"] or bindings["gone"]:
        moved, new, gone = (int(bindings[key]) for key in ("moved", "new", "gone"))
        if reread and not moved:
            sentence = (
                "The name reader changed between these runs (version "
                f"{', '.join(readers['before'])} before, {', '.join(readers['after'])} after), "
                "so every word's reading was made again and none can be matched word for word: "
                f"{new:,} readings now, {gone:,} before"
            )
            if not entries:
                sentence += ". The names themselves are what they were"
        elif moved:
            sentence = f"{_plural(moved, 'word')} in names now read differently"
            if new or gone:
                sentence += f"; {new:,} new and {gone:,} gone"
        else:
            sentence = (
                f"{new:,} words in names are new and {gone:,} gone; none still there reads "
                "differently"
            )
        said.append((max(moved, new, gone), sentence + "."))
    else:
        unchanged.append("words")
    for key, noun, gained, lost, up, down in (
        ("claims", "conclusion", "gained", "lost", "more", "fewer"),
        ("findings", "flag", "raised", "cleared", "raised", "cleared"),
        ("candidates", "proposed pair", "gained", "lost", "more", "fewer"),
    ):
        section = found[key]
        more, fewer = int(section[gained]), int(section[lost])
        if not more and not fewer:
            unchanged.append(noun + "s")
            continue
        counted = ", ".join(
            part
            for part in (
                f"{more:,} {up}" if more else "",
                f"{fewer:,} {down}" if fewer else "",
            )
            if part
        )
        said.append(
            (more + fewer, f"{noun.capitalize()}s: {counted}{_most(section, gained, lost)}.")
        )
    said.sort(key=lambda item: -item[0])
    sentences = [sentence for _, sentence in said]
    answers = found.get("answers")
    if answers is not None:
        edited = int(answers.get("edited", 0))
        if answers["decided"] or answers["noted"] or edited:
            counts = [
                _plural(int(answers[key]), noun)
                for key, noun in (("decided", "answer"), ("noted", "note"))
                if answers[key]
            ]
            if edited:
                # Edits of a person's topics are no answers: said apart, as the manifest does.
                counts.append(_plural(edited, "edit of topics", "edits of topics"))
            written = (
                " and ".join(counts)
                if len(counts) < 3
                else ", ".join(counts[:-1]) + f", and {counts[-1]}"
            )
            sentences.append(f"Written to the Record between the two runs: {written}.")
        else:
            sentences.append("Nothing was written to the Record between the two runs.")
    if unchanged:
        listed = ", ".join(unchanged[:-1]) + (" or " if len(unchanged) > 1 else "") + unchanged[-1]
        sentences.append(f"No change in {listed}.")
    return sentences


def _compared_with(found: list[dict[str, Any]], run: dict[str, Any]) -> str | None:
    """The run a run on the timeline is compared with: the latest earlier run of its own kind.

    An inference is compared with the latest earlier inference, as the page's own default pair
    is chosen; a census, a dive or a rebind with the latest earlier run that applied no rules
    either, because comparing names read again with a run holding conclusions says only that
    the conclusions are not there. Failing both, the run it was built from.
    """
    earlier = [
        other
        for other in found
        if other["inference"] == run["inference"] and other["number"] < run["number"]
    ]
    if earlier:
        return str(earlier[-1]["run_id"])
    parent = run.get("parent_run_id")
    return str(parent) if parent else None


def _said_of(event: RecordEvent) -> dict[str, Any]:
    """One answer as the timeline lists it: who, when, what about, and what they said."""
    try:
        payload = json.loads(event.payload or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    held = payload.get("source")
    source: dict[str, Any] = held if isinstance(held, dict) else {}
    word = (
        payload.get("verdict")
        or payload.get("worth_reading")
        or payload.get("answer")
        or event.kind.value
    )
    return {
        "sequence": event.sequence,
        "actor": event.actor,
        "occurred_at": event.occurred_at,
        "kind": event.kind.value,
        "target": event.target,
        "word": word,
        # What a page shows for the word ("Can't say" for cannot_say), from the one label table.
        "label": shown_on(str(word), event.target),
        "reading": payload.get("reading"),
        # A term kept from a note has no note of its own: what was kept is said instead.
        "note": payload.get("note") or kept_line(payload),
        # Where it was said: the page that wrote it, a review sheet it was imported from, or,
        # for an event that says neither, the page its kind of row is decided on.
        "page": _page_shown(
            source.get("page")
            or ("a review sheet" if source.get("sheet") else None)
            or PAGES.get(event.target.partition(":")[0], event.target.partition(":")[0])
        ),
        "imported_from": imported_from(source),
        "imported_by": source.get("imported_by"),
        "against_run_id": event.against_run_id,
    }


#: Page names the Record stores that a person reads under another name. The stored word never
#: changes (an answer written under it stays as written). Only what is shown does.
_PAGE_SHOWN = {"claim samples": "conclusion samples"}


def _page_shown(stored: str) -> str:
    return _PAGE_SHOWN.get(stored, stored)


def _landing() -> dict[str, Any]:
    return {"decided": 0, "noted": 0, "edited": 0, "by_actor": {}, "by_page": {}, "answers": []}


def _land(into: dict[str, Any], event: RecordEvent, *, examples: int) -> None:
    if is_topic_edit(event):
        # An edit of a person's topic is no answer: counted apart, and listed with none.
        into["edited"] += 1
        return
    said = _said_of(event)
    into["noted" if event.kind is RecordEventKind.NOTE else "decided"] += 1
    into["by_actor"][event.actor] = into["by_actor"].get(event.actor, 0) + 1
    into["by_page"][said["page"]] = into["by_page"].get(said["page"], 0) + 1
    if len(into["answers"]) < examples:
        into["answers"].append(said)


def timeline(project: Path, *, examples: int = TIMELINE_EXAMPLES) -> dict[str, Any]:
    """Every run, oldest first, each with the answers that first took effect in it.

    An answer takes effect in the first run that began at or after it was written, whatever kind
    of run that is. A census and a rebind read the answers' meanings into the names, a dive reads
    what is worth reading, and an inference replays every decision. An answer written after the
    latest run began is waiting for the next run. A run whose beginning is unknown takes none, and
    an answer written at an instant nobody can read is counted apart.

    A person's edit of a topic is not an answer. It decides nothing, and no run reads it. The
    edit is counted apart (``edited``) and appears in none of the other counts, as in the run
    manifest.

    Each run carries its answers counted, decided and noted, by who and by page, with up to
    ``examples`` of them listed. It also carries how many answers were made while a person was
    reading it (``made_here``, from each event's run). It carries ``compare_with``, the run its
    comparison opens against: the latest earlier run of its own kind. For an inference that is
    the latest earlier inference, as the page's own default pair is chosen.
    """
    listed = runs(project)
    found = listed["runs"]
    starts = [(str(run["run_id"]), _instant(run.get("began"))) for run in found]
    landed = {run_id: _landing() for run_id, _ in starts}
    waiting = _landing()
    unplaced = 0
    made: dict[str, int] = {}
    for event in read_events(project, withdrawn=True):
        if not is_topic_edit(event):
            made[event.against_run_id] = made.get(event.against_run_id, 0) + 1
        when = _instant(event.occurred_at)
        if when is None:
            unplaced += 1
            continue
        landing = next(
            (run_id for run_id, began in starts if began is not None and when <= began), None
        )
        _land(landed[landing] if landing is not None else waiting, event, examples=examples)
    return {
        "runs": [
            {
                **run,
                "took_effect": landed[str(run["run_id"])],
                "made_here": made.get(str(run["run_id"]), 0),
                "compare_with": _compared_with(found, run),
            }
            for run in found
        ],
        "waiting": waiting,
        "unplaced": unplaced,
        "current": listed["current"],
        "pair": listed["pair"],
    }


def _renamed(counted: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    """The same counts under the words a finding takes: raised and cleared, not gained and lost."""
    renamed = {names.get(key, key): value for key, value in counted.items()}
    for held in renamed["by_rule"]:
        for old, new in names.items():
            held[new] = held.pop(old)
        for example in held["examples"]:
            example["way"] = names.get(example["way"], example["way"])
    return renamed


def _bindings(catalog: Catalog, *, examples: int) -> dict[str, Any]:
    """Name components bound differently, new and gone, grouped by kind and extension spelling."""
    moved_sql = """
        WITH a AS (SELECT component_id, entry_id, term, resolution_status FROM dictionary_bindings),
             b AS (SELECT component_id, term, resolution_status FROM before_dictionary_bindings),
             moved AS (
                 SELECT a.component_id, a.entry_id,
                        coalesce(b.term, b.resolution_status) AS was,
                        coalesce(a.term, a.resolution_status) AS now
                 FROM a JOIN b USING (component_id)
                 WHERE a.term IS DISTINCT FROM b.term
                    OR a.resolution_status IS DISTINCT FROM b.resolution_status
             )
    """
    # Both queries below extend the constant above and interpolate nothing a request gives.
    groups = _rows(
        catalog,
        moved_sql  # noqa: S608
        + """
        SELECT CASE WHEN c.component_type = 'extension'
                    THEN 'extension .' || c.normalized_value ELSE c.component_type END AS grouped,
               count(*) AS n
        FROM moved m JOIN components c ON c.component_id = m.component_id
        GROUP BY 1 ORDER BY n DESC, grouped
        """,
    )
    shown = _rows(
        catalog,
        moved_sql  # noqa: S608
        + """
        SELECT c.normalized_value AS token, c.component_type, m.was, m.now, e.name
        FROM moved m
        JOIN components c ON c.component_id = m.component_id
        LEFT JOIN entries e ON e.entry_id = m.entry_id
        ORDER BY c.component_type, c.normalized_value, e.name
        LIMIT ?
        """,
        [examples],
    )
    new, gone = (
        catalog.query(
            f"SELECT count(*) FROM (SELECT component_id FROM {later} "  # noqa: S608
            f"EXCEPT SELECT component_id FROM {earlier})"
        ).fetchone()
        for later, earlier in (
            ("dictionary_bindings", "before_dictionary_bindings"),
            ("before_dictionary_bindings", "dictionary_bindings"),
        )
    )
    # A component's identity holds the version of the reader that read the name. So across a new
    # reader every word is "new" and "gone" by identity although nothing in the names changed.
    # The page says that the reader changed instead.
    readers = {
        side: sorted(
            str(row[0])
            for row in catalog.query(
                f"SELECT DISTINCT normalizer_version FROM {table}"  # noqa: S608
            ).fetchall()
            if row[0] is not None
        )
        for side, table in (("before", "before_components"), ("after", "components"))
    }
    return {
        "readers": readers,
        "moved": sum(int(row["n"]) for row in groups),
        "new": int(new[0]) if new else 0,
        "gone": int(gone[0]) if gone else 0,
        "by_group": [{"group": row["grouped"], "n": int(row["n"])} for row in groups],
        "examples": shown,
    }
