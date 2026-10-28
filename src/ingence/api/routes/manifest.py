"""A run's manifest: what changed from the run it was built from, in plain sentences.

The Changes page compares any two runs, with a table by rule (``changes.py``). A manifest is
narrower and plainer: it compares one run against its parent (the run it was built from, as its
run record names it). It says the difference as sentences that a person reads in order, each with
its number and what it is out of:

- The decisions that took effect in it: every Record event in force at the run's watermark with a
  sequence above the parent's. A run reads the Record as it stood at its watermark, with a
  withdrawn answer left out, so these are exactly the answers that this run read and its parent
  did not. Each comes with what changed where it was about: "Your answer 'not a program' on
  tools/ took effect: the folder itself and 41 of 41 files beneath it read differently now." A
  note is never one, because it decides nothing. A going back to an earlier run is said as what
  it withdrew: "1 answer withdrawn when you went back to Run 2."
- The rules that changed: a rule new, gone, switched on or off, at another version, or with other
  parameters.
- The readings that moved, by rule: "12 of 340 files now read as analysis (they were
  acquisition): rule stage from folder words, version 3.", "3 of 340 files no longer read as raw:
  ...". A rule whose readings moved more ways than ``values_named`` is said in one sentence ("41
  of 340 files read differently by rule ..., 37 different readings"). A time or a measurement is
  a value per file, and naming each would make a list and not a sentence.
- The flags raised and cleared, by rule, as the readings are.
- The pairs proposed and no longer proposed, by rule.
- The themes that experiments joined, read through :data:`THEME_READERS` (the themes package,
  reached through the registry and imported lazily). Where none is available, the themes part
  says "themes not computed".
- The topics that a person made: which experiments joined or left each, with the member that
  explains it ("2 experiments joined \u201cNuclear counts\u201d: each now carries the words
  ..."), read through :data:`TOPIC_READERS` the same way. The manifest also says the edits that a
  person made to their topics, as edits ("You renamed the topic ..."), and never as answers. A
  topic is an annotation: it decides nothing and no run reads it, so it is not among the
  decisions that took effect.

No language model writes these sentences. Each one is a template filled from counts that the two
runs' tables give. Nothing is written: the module reads two runs and makes one difference. A
reading is a rule's claims about one subject, its value and its standing ("in doubt" where a
defeater left it conflicting), so a new version of a rule that concludes the same thing is no
change.

A decision's change is traced only where its target names a place: an entry, a folder or a
program folder (the files beneath it whose readings moved), or a word (the files whose names carry
it). Any other answer is listed as taking effect, without a count, and says so. An answer is never
credited with a change that it cannot be traced to. Where an answer and a rule change touch the
same files, both are credited. The counts say what moved where, not why.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from ingence.core.registry import ComponentNotAvailableError, Registry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.storage.catalog import Catalog

__all__ = [
    "THEME_READERS",
    "TOPIC_READERS",
    "VALUES_NAMED",
    "ThemeReader",
    "TopicReader",
    "manifest",
]

#: How many different readings of one rule a manifest names before it says them as one sentence.
#: The limit is three. A stage or a role moves between a few values, and three sentences of one
#: rule are still read. A time or a measurement moves to a value per file. The limit is a
#: parameter and not a measured value.
VALUES_NAMED = 3

#: How many sentences of readings moved a manifest says before counting the rest in one.
SENTENCES_SHOWN = 25

#: A reader of a run's themes: each experiment's theme label (None for an experiment in no
#: theme), or None where the run has none.
type ThemeReader = Callable[[Path, str], Mapping[str, str | None] | None]

#: Theme readers, reached through the registry. The themes of a run are an optional component
#: (``ingence.themes``), never imported by name. A manifest reads the first available.
THEME_READERS: Registry[ThemeReader] = Registry("theme_reader")

#: The function the themes package offers: ``(project, run_id, settings=None) -> {experiment:
#: label or None}``. One name, so the contract is one line to change.
THEMES_FUNCTION = "experiment_themes"


@THEME_READERS.register(
    "themes",
    version="1",
    summary="each experiment's theme label, from the themes package",
    requires=("ingence.themes",),
)
def _themes_package() -> ThemeReader:
    import importlib

    held = getattr(importlib.import_module("ingence.themes"), THEMES_FUNCTION)
    reader: ThemeReader = held
    return reader


#: A reader of a run's topics: for each topic its name and what joined or left it since the
#: compared run, or None where the run has none. Arguments: the project, the run, the compared run.
type TopicReader = Callable[[Path, str, str | None], list[dict[str, Any]] | None]

#: Topic readers, reached through the registry as the theme readers are. The topics of a run are
#: an optional component (``ingence.topics``), never imported by name. A manifest reads the first
#: available.
TOPIC_READERS: Registry[TopicReader] = Registry("topic_reader")

#: The function the topics package offers: ``(project, run_id, compared_run_id) -> [row]``.
TOPICS_FUNCTION = "topic_drift"


@TOPIC_READERS.register(
    "topics",
    version="1",
    summary="what joined or left each topic since the compared run, from the topics package",
    requires=("ingence.topics",),
)
def _topics_package() -> TopicReader:
    import importlib

    held = getattr(importlib.import_module("ingence.topics"), TOPICS_FUNCTION)
    reader: TopicReader = held
    return reader


def _default_topics() -> tuple[TopicReader | None, str | None]:
    """The first available topic reader, or None and why none is."""
    reasons: list[str] = []
    for name in TOPIC_READERS.names(enabled_only=True):
        try:
            return TOPIC_READERS.get(name), None
        except ComponentNotAvailableError as error:
            reasons.append(str(error))
    return None, "; ".join(reasons) or "no topic reader is registered"


def _default_themes() -> tuple[ThemeReader | None, str | None]:
    """The first available theme reader, or None and why none is."""
    reasons: list[str] = []
    for name in THEME_READERS.names(enabled_only=True):
        try:
            return THEME_READERS.get(name), None
        except ComponentNotAvailableError as error:
            reasons.append(str(error))
    return None, "; ".join(reasons) or "no theme reader is registered"


def _rows(catalog: Catalog, sql: str, parameters: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = catalog.query(sql, parameters or [])
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


#: The version marks that a person's change of a rule leaves (``rulebook/resolve.py``'s ``+p``).
#: Every other mark (``+d``, ``+s``) is the engine's own book and says nothing a person needs.
_PERSON_MARK = "p"


def version_said(version: str) -> str:
    """A rule's version as a person reads it: ``2+p1`` is "version 2 with a person's change".

    The marks after the number are how the Rulebook keeps a rule's revisions apart. A sentence
    says only whether a person changed the rule, so that every page shows a rule's version the same
    way.
    """
    base, *marks = str(version).split("+")
    changed = any(mark.startswith(_PERSON_MARK) for mark in marks)
    return f"version {base}" + (" with a person's change" if changed else "")


def _cited(rule_id: str, version: str | None = None) -> str:
    """A rule as a sentence names it: its plain words first, then its name and version.

    The sentence never uses the identifier: the name is its words, and the version is said
    plainly. A name that holds a word no sentence carries (``group_resolution_deviation``: the
    plain table's ``never_shown``) is left out, and the plain words and the version stand.
    """
    from ingence.api.plain_words import rule_sentence

    # Only the rule's first sentence: a rule's plain words run to two or three short sentences,
    # and a quotation inside a longer sentence holds one.
    whole = (rule_sentence(rule_id) or "").strip()
    plain = re.split(r"(?<=[.!?])\s+(?=[A-Z])", whole, maxsplit=1)[0].rstrip(".")
    name = _rule_name(rule_id)
    named = ", ".join(part for part in (name, version_said(version) if version else "") if part)
    if plain:
        return f"the rule \u201c{plain}\u201d" + (f" ({named})" if named else "")
    return f"the rule {named}" if named else "a rule"


def _rule_words(rule_id: str) -> str:
    """A rule's identifier as words: ``pipeline_stage_from_name`` is "pipeline stage from name"."""
    return rule_id.replace("_", " ")


def _rule_name(rule_id: str) -> str:
    """A rule's name as a sentence shows it: its words, or nothing where they hold a word that
    no sentence carries."""
    from ingence.api.plain_words import name_shown_in_a_sentence

    return _rule_words(rule_id) if name_shown_in_a_sentence(rule_id) else ""


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def _noun(entry_type: str | None, count: int) -> str:
    if entry_type == "file":
        return "file" if count == 1 else "files"
    if entry_type == "directory":
        return "folder" if count == 1 else "folders"
    return "subject" if count == 1 else "subjects"


#: A reading as a sentence shows it: the claim's value, and "in doubt" where a defeater left it
#: conflicting (the Changes page's own spelling).
_READING = (
    "coalesce(object_reference, '') || CASE resolution_status WHEN 'resolved' THEN '' "
    "WHEN 'examined_conflicting' THEN ' (in doubt)' ELSE ' (' || resolution_status || ')' END"
)

#: Every (rule, subject) whose reading differs between the two runs, with its entry's kind.
_MOVED_SQL = f"""
WITH now AS (
    SELECT rule_id, subject, max(rule_version) AS version,
           string_agg(DISTINCT {_READING}, ' | ' ORDER BY {_READING}) AS reading
    FROM claims GROUP BY rule_id, subject
),
was AS (
    SELECT rule_id, subject, max(rule_version) AS version,
           string_agg(DISTINCT {_READING}, ' | ' ORDER BY {_READING}) AS reading
    FROM before_claims GROUP BY rule_id, subject
),
moved AS (
    SELECT coalesce(n.rule_id, w.rule_id) AS rule_id, coalesce(n.subject, w.subject) AS subject,
           w.reading AS was, n.reading AS now, n.version AS version, w.version AS was_version
    FROM now n FULL OUTER JOIN was w ON n.rule_id = w.rule_id AND n.subject = w.subject
    WHERE n.reading IS DISTINCT FROM w.reading
)
SELECT m.*, regexp_replace(m.subject, '^folder:', '') AS entry_id,
       coalesce(e.entry_type, b.entry_type) AS entry_type
FROM moved m
LEFT JOIN entries e ON e.entry_id = regexp_replace(m.subject, '^folder:', '')
LEFT JOIN before_entries b ON b.entry_id = regexp_replace(m.subject, '^folder:', '')
"""  # noqa: S608 - this module's constants only


#: Every (rule, subject) whose flags differ between the two runs: raised, cleared or changed.
_FLAGGED_SQL = """
WITH now AS (
    SELECT rule_id, subject, max(rule_version) AS version,
           string_agg(DISTINCT array_to_string(list_sort(raised), ', '), ' | ') AS reading
    FROM findings GROUP BY rule_id, subject
),
was AS (
    SELECT rule_id, subject, max(rule_version) AS version,
           string_agg(DISTINCT array_to_string(list_sort(raised), ', '), ' | ') AS reading
    FROM before_findings GROUP BY rule_id, subject
),
moved AS (
    SELECT coalesce(n.rule_id, w.rule_id) AS rule_id, coalesce(n.subject, w.subject) AS subject,
           w.reading AS was, n.reading AS now, n.version AS version, w.version AS was_version
    FROM now n FULL OUTER JOIN was w ON n.rule_id = w.rule_id AND n.subject = w.subject
    WHERE n.reading IS DISTINCT FROM w.reading
)
SELECT m.*, regexp_replace(m.subject, '^folder:', '') AS entry_id,
       coalesce(e.entry_type, b.entry_type) AS entry_type
FROM moved m
LEFT JOIN entries e ON e.entry_id = regexp_replace(m.subject, '^folder:', '')
LEFT JOIN before_entries b ON b.entry_id = regexp_replace(m.subject, '^folder:', '')
"""


def _run_row(catalog: Catalog) -> dict[str, Any]:
    found = _rows(catalog, "SELECT * FROM run LIMIT 1")
    return found[0] if found else {}


def _watermark(row: Mapping[str, Any]) -> int | None:
    held = row.get("record_watermark")
    return int(held) if held is not None else None


def _totals(catalog: Catalog) -> dict[str | None, int]:
    """How many entries of each kind the run holds: what a sentence's count is out of."""
    return {
        str(kind): int(count)
        for kind, count in catalog.query(
            "SELECT entry_type, count(*) FROM entries WHERE NOT is_deleted GROUP BY 1"
        ).fetchall()
    }


#: How a reading marks a standing a defeater left in doubt (``_READING``).
_IN_DOUBT = " (in doubt)"


def _shown(reading: str | None) -> str | None:
    """A reading as a sentence names it, or None where it is a structure no sentence can name.

    A stage, a role or a format is a word and is named. A reading that is a list or a record (a
    name family's components, a time with its source) is said as "read differently" instead,
    because a sentence that quoted it would not be one a reader can follow.
    """
    if reading is None:
        return None
    text = reading.strip()
    if text.startswith(("[", "{")) or len(text) > 60:
        return None
    # A value is a word the Dictionary spells with underscores (``image_format``): said as words.
    return text.replace("_", " ")


def _standing_only(was: str | None, now: str | None) -> str | None:
    """Where only a reading's standing moved: "no longer in doubt" or "now in doubt", else None."""
    if was is None or now is None:
        return None
    if was.endswith(_IN_DOUBT) and was.removesuffix(_IN_DOUBT) == now:
        return "are no longer in doubt"
    if now.endswith(_IN_DOUBT) and now.removesuffix(_IN_DOUBT) == was:
        return "are now in doubt"
    return None


#: The one sentence for a rule whose readings moved more ways than a manifest names.
_MANY = "{counted} read differently by {cited} ({readings} different readings)."


def _template(was: str | None, now: str | None, *, flags: bool) -> tuple[str, str]:
    """The way a pair of readings moved, and the sentence saying it, its counts left to fill."""
    standing = _standing_only(was, now)
    named_was, named_now = _shown(was), _shown(now)
    if standing is not None:
        return "standing", "{counted} " + standing + ", by {cited}."
    if flags:
        if was is None:
            return "raised", (
                "{counted} newly flagged {now}, by {cited}."
                if named_now
                else "{counted} newly flagged, by {cited}."
            )
        if now is None:
            return "cleared", (
                "{counted} no longer flagged {was}, by {cited}."
                if named_was
                else "{counted} no longer flagged, by {cited}."
            )
        return "changed", "{counted} flagged differently, by {cited}."
    if was is None:
        return "gained", (
            "{counted} now read as {now}, by {cited}."
            if named_now
            else "{counted} newly read by {cited}."
        )
    if now is None:
        return "lost", (
            "{counted} no longer read as {was}, by {cited}."
            if named_was
            else "{counted} no longer read by {cited}."
        )
    if named_was and named_now:
        return "changed", "{counted} now read as {now} (they were {was}), by {cited}."
    return "changed", "{counted} read differently by {cited}."


def _readings_moved(
    moved: list[dict[str, Any]],
    totals: Mapping[str | None, int],
    *,
    versions: Mapping[str, str],
    values_named: int,
    flags: bool = False,
) -> list[dict[str, Any]]:
    """The readings (or flags) that moved, grouped by rule and by what they were and are.

    Each rule is cited at its version in this run (``versions``), at its last version where it no
    longer runs.
    """
    by_rule: dict[str, list[dict[str, Any]]] = {}
    for row in moved:
        by_rule.setdefault(str(row["rule_id"]), []).append(row)
    groups: list[dict[str, Any]] = []
    for rule_id, rows in sorted(by_rule.items()):
        version = versions.get(rule_id) or next(
            (str(row["was_version"]) for row in rows if row["was_version"]), ""
        )
        cited = _cited(rule_id, version)
        pairs: dict[tuple[str | None, str | None, str | None], int] = {}
        for row in rows:
            moved_key = (row["was"], row["now"], row["entry_type"])
            pairs[moved_key] = pairs.get(moved_key, 0) + 1
        readings = {(was, now) for was, now, _ in pairs}
        # Readings a sentence cannot tell apart (two structures both settled, say) are one
        # sentence: each pair of readings is said by a template, and equal sentences add up.
        said_as: dict[tuple[str, str, str | None, str | None, str | None], int] = {}
        for (was, now, kind), count in pairs.items():
            way, template = _template(was, now, flags=flags)
            said_key = (
                way,
                template,
                kind,
                was if "{was}" in template else None,
                now if "{now}" in template else None,
            )
            said_as[said_key] = said_as.get(said_key, 0) + count
        # More different sentences than ``values_named``: one sentence for the rule, by kind.
        if len({said[:2] + said[3:] for said in said_as}) > values_named:
            collapsed: dict[tuple[str, str, str | None, str | None, str | None], int] = {}
            for (_, _, kind, _, _), count in said_as.items():
                many_key = ("changed", _MANY, kind, None, None)
                collapsed[many_key] = collapsed.get(many_key, 0) + count
            said_as = collapsed
        for (way, template, kind, was, now), count in said_as.items():
            total = totals.get(kind, count)
            counted = f"{count:,} of {total:,} {_noun(kind, total)}"
            groups.append(
                {
                    "rule_id": rule_id,
                    "rule": _rule_words(rule_id),
                    "version": version,
                    "way": way,
                    "was": was if "{was}" in template else None,
                    "now": now if "{now}" in template else None,
                    "count": count,
                    "out_of": total,
                    "said": template.format(
                        counted=counted,
                        cited=cited,
                        was=_shown(was),
                        now=_shown(now),
                        readings=f"{len(readings):,}",
                    ),
                }
            )
    groups.sort(key=lambda group: (-int(group["count"]), group["rule_id"], group["said"]))
    return groups


def _parameters(raw: Any) -> dict[str, Any]:
    """A rule row's parameters as ``{name: value}``."""
    try:
        held = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    found: dict[str, Any] = {}
    for spec in held or ():
        if isinstance(spec, dict) and "name" in spec:
            found[str(spec["name"])] = spec.get("default")
    return found


def _held(row: Mapping[str, Any]) -> Any:
    """A rule row's parameters, stored as JSON under ``parameters_json``."""
    return row.get("parameters_json", row.get("parameters"))


def _loaded(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return value


def _parameter_said(name: str, before: Any, after: Any) -> str:
    """One parameter's change: a list's values taken out and added, else its value then and now."""
    earlier, later = _loaded(before), _loaded(after)
    if isinstance(earlier, list) and isinstance(later, list):
        taken = [str(value) for value in earlier if value not in later]
        added = [str(value) for value in later if value not in earlier]
        parts = []
        if taken:
            parts.append(f"{', '.join(taken[:5])}{' and more' if len(taken) > 5 else ''} taken out")
        if added:
            parts.append(f"{', '.join(added[:5])}{' and more' if len(added) > 5 else ''} added")
        if parts:
            return f"{name}: {'; '.join(parts)}"
        return f"{name}: the same values in another order"
    return f"{name} now {_value_said(after)} (was {_value_said(before)})"


def _value_said(value: Any) -> str:
    """A parameter's value in a sentence: itself where short, its size where a list."""
    try:
        loaded = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        loaded = value
    if isinstance(loaded, list):
        return _plural(len(loaded), "value")
    text = json.dumps(loaded) if not isinstance(loaded, str) else loaded
    return text if len(text) <= 40 else text[:37] + "..."


def _rules_changed(catalog: Catalog) -> list[dict[str, Any]]:
    """The rules new, gone, switched, at another version, or with other parameters."""
    now = {row["rule_id"]: row for row in _rows(catalog, "SELECT * FROM rules")}
    try:
        was = {row["rule_id"]: row for row in _rows(catalog, "SELECT * FROM before_rules")}
    except Exception:  # a parent that held no rules table: every rule is new
        was = {}
    if not was:
        return []
    found: list[dict[str, Any]] = []
    for rule_id in sorted(set(now) | set(was)):
        later, earlier = now.get(rule_id), was.get(rule_id)
        named = _capitalised(_cited(rule_id))
        if earlier is None and later is not None:
            change = "new"
            said = f"{named} is new in this run, {version_said(later['version'])}."
        elif later is None and earlier is not None:
            change, said = "gone", f"{named} is no longer applied."
        elif later is not None and earlier is not None:
            if bool(later["enabled"]) != bool(earlier["enabled"]):
                change = "switched"
                said = f"{named} is switched {'on' if later['enabled'] else 'off'}."
            else:
                before, after = _parameters(_held(earlier)), _parameters(_held(later))
                differing = sorted(
                    name for name in set(before) | set(after) if before.get(name) != after.get(name)
                )
                if not differing and later["version"] == earlier["version"]:
                    continue
                was_said, now_said = (
                    version_said(str(earlier["version"])),
                    version_said(str(later["version"])),
                )
                by_person = now_said.endswith("change") and not was_said.endswith("change")
                parts = [
                    _parameter_said(name, before.get(name), after.get(name)) for name in differing
                ]
                tail = f"; it is now {now_said}" if now_said != was_said else ""
                change = "parameters" if differing else "version"
                if differing:
                    said = (
                        f"{named} changed{' by a person' if by_person else ''}: "
                        f"{'; '.join(parts)}{tail}."
                    )
                else:
                    said = f"{named} changed from {was_said} to {now_said}."
        else:
            continue
        found.append(
            {
                "rule_id": rule_id,
                "rule": _rule_words(rule_id),
                "version": str((later or earlier or {}).get("version") or ""),
                "was_version": str(earlier["version"]) if earlier is not None else None,
                "change": change,
                "said": said,
            }
        )
    return found


def _pairs_moved(catalog: Catalog) -> list[dict[str, Any]]:
    """Pairs proposed and no longer proposed, by rule, out of the rule's pairs in this run."""
    columns = "rule_id, source_id, target_id, specialization"
    rows = _rows(
        catalog,
        f"""
        WITH gained AS (SELECT {columns} FROM candidates EXCEPT SELECT {columns}
                        FROM before_candidates),
             lost AS (SELECT {columns} FROM before_candidates EXCEPT SELECT {columns}
                      FROM candidates),
             held AS (SELECT rule_id, count(*) AS n FROM candidates GROUP BY rule_id)
        SELECT r.rule_id, coalesce(g.n, 0) AS gained, coalesce(l.n, 0) AS lost,
               coalesce(h.n, 0) AS held
        FROM (SELECT rule_id FROM gained UNION SELECT rule_id FROM lost) r
        LEFT JOIN (SELECT rule_id, count(*) AS n FROM gained GROUP BY 1) g USING (rule_id)
        LEFT JOIN (SELECT rule_id, count(*) AS n FROM lost GROUP BY 1) l USING (rule_id)
        LEFT JOIN held h USING (rule_id)
        ORDER BY gained + lost DESC, r.rule_id
        """,  # noqa: S608 - this module's constants only
    )
    found: list[dict[str, Any]] = []
    for row in rows:
        gained, lost, held = int(row["gained"]), int(row["lost"]), int(row["held"])
        parts = []
        if gained:
            parts.append(f"{gained:,} of its {_plural(held, 'pair')} newly proposed")
        if lost:
            parts.append(f"{_plural(lost, 'pair')} no longer proposed")
        found.append(
            {
                "rule_id": row["rule_id"],
                "rule": _rule_words(str(row["rule_id"])),
                "gained": gained,
                "lost": lost,
                "out_of": held,
                "said": f"{_capitalised(_cited(str(row['rule_id'])))}: {', '.join(parts)}.",
            }
        )
    return found


def _answer_word(event: RecordEvent) -> str:
    """What an answer said, as a page shows it ("not a program", "Related, not copies")."""
    from ingence.record.verdicts import shown_on

    try:
        payload = json.loads(event.payload or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    word = (
        payload.get("verdict")
        or payload.get("word")
        or payload.get("label")
        or payload.get("answer")
        or payload.get("worth_reading")
        or event.kind.value
    )
    shown = shown_on(str(word), event.target)
    return shown if shown != str(word) else str(word).replace("_", " ")


#: The target prefixes that name one entry: the answer is traced to the files beneath it.
_PLACE_PREFIXES = ("entry", "folder", "program", "miscellaneous", "label")


def _traced(
    catalog: Catalog, event: RecordEvent, moved_entries: set[str]
) -> tuple[str, str | None, bool | None, int | None, int | None]:
    """Where an answer was about and what moved there.

    Returns (place, what the place is: ``folder``, ``file`` or ``word``, itself moved, files
    moved, files); ``None`` counts where the target names no place the run holds.
    """
    prefix, _, subject = event.target.partition(":")
    if prefix in _PLACE_PREFIXES and subject.startswith("entry_"):
        rows = _rows(
            catalog,
            """
            WITH RECURSIVE beneath(entry_id) AS (
                SELECT entry_id FROM entries WHERE entry_id = ?
                UNION ALL
                SELECT e.entry_id FROM entries e JOIN beneath b ON e.parent_entry_id = b.entry_id
            )
            SELECT e.entry_id, e.entry_type, e.relative_path
            FROM beneath b JOIN entries e USING (entry_id)
            """,
            [subject],
        )
        if not rows:
            return "a place this run does not hold", None, None, None, None
        own = next(row for row in rows if row["entry_id"] == subject)
        place = str(own["relative_path"])
        if own["entry_type"] == "file":
            return place, "file", subject in moved_entries, None, None
        files = [row["entry_id"] for row in rows if row["entry_type"] == "file"]
        return (
            place,
            "folder",
            subject in moved_entries,
            sum(1 for entry in files if entry in moved_entries),
            len(files),
        )
    if prefix == "term" and subject:
        rows = _rows(
            catalog,
            "SELECT DISTINCT c.entry_id FROM components c JOIN entries e USING (entry_id) "
            "WHERE e.entry_type = 'file' AND lower(c.normalized_value) = lower(?)",
            [subject],
        )
        files = [str(row["entry_id"]) for row in rows]
        return (
            f"the word {subject}",
            "word",
            None,
            sum(1 for entry in files if entry in moved_entries),
            len(files),
        )
    return _place_words(catalog, event), None, None, None, None


def _target_words(target: str) -> str:
    """A target no place names, as a person reads it: its kind and what it names, no identifier."""
    prefix, _, subject = target.partition(":")
    kind = prefix.replace("_", " ")
    if subject.startswith("entry_") or subject.startswith("run_"):
        return f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind}"
    return f"{kind} {subject.replace('_', ' ')}".strip()


def _place_words(catalog: Catalog, event: RecordEvent) -> str:
    """Where an answer that names no folder or file was about, in words a person reads.

    An experiment's analysis (``analysed_elsewhere:<entry>``) is "the analysis of" its folder; a
    class of conclusions (``class:<rule>:<digest>``) is "every conclusion" one rule read as the
    value the answer was given on. Any other target is its kind and what it names.
    """
    from ingence.record.targets import ANALYSED_ELSEWHERE_PREFIX, CLASS_PREFIX

    prefix, _, subject = event.target.partition(":")
    if prefix == ANALYSED_ELSEWHERE_PREFIX:
        found = catalog.query(
            "SELECT relative_path FROM entries WHERE entry_id = ?", [subject]
        ).fetchone()
        return f"the analysis of {found[0]}" if found else "an experiment's analysis"
    if prefix == CLASS_PREFIX:
        try:
            payload = json.loads(event.payload or "{}")
        except ValueError:
            payload = {}
        value = payload.get("value") if isinstance(payload, dict) else None
        if isinstance(value, str) and value and len(value) <= 60:
            return f"every conclusion read as \u201c{value.replace('_', ' ')}\u201d by one rule"
        return "every conclusion of one class"
    return _target_words(event.target)


def _effect_said(
    opening: str, kind: str | None, itself: bool | None, moved: int | None, files: int | None
) -> str:
    """What an answer changed, in a sentence, from what :func:`_traced` found."""
    if kind == "file":
        return (
            f"{opening}: the file has new results now."
            if itself
            else f"{opening}; the file reads as it did."
        )
    if kind == "word" and files is not None and moved is not None:
        if not moved:
            return (
                f"{opening}; none of the {_plural(files, 'file')} whose names carry it reads "
                "differently."
            )
        return (
            f"{opening}: {moved:,} of {_plural(files, 'file')} whose names carry it have new "
            "results now."
        )
    if kind == "folder" and files is not None and moved is not None:
        parts = (["the folder itself"] if itself else []) + [
            f"{moved:,} of {_plural(files, 'file')} beneath it"
        ]
        if not itself and not moved:
            return f"{opening}; nothing in it reads differently."
        return f"{opening}: {' and '.join(parts)} have new results now."
    return f"{opening}; what it changed is not traced to it here."


def _decisions(
    catalog: Catalog,
    project: Path,
    *,
    after: int | None,
    up_to: int | None,
    moved_entries: set[str],
    viewer: str | None,
) -> list[dict[str, Any]]:
    """The answers this run read and its parent did not, each traced.

    The function reads the Record as the run read it: every event in force at the run's
    watermark (``read_at``; a withdrawn answer is left out, as the run left it out) whose sequence
    is above the parent's watermark. A note is never an answer that took effect, because it
    decides nothing, so the function leaves it out. :func:`_withdrawals` says a withdrawal.
    """
    from ingence.api.routes.overlay import is_topic_edit
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import read_at, read_events

    if up_to is None:
        return []
    floor = after or 0
    with read_at(project, up_to):
        in_force = read_events(project)
    found: list[dict[str, Any]] = []
    for event in in_force:
        if not floor < event.sequence <= up_to:
            continue
        if event.kind in (RecordEventKind.NOTE, RecordEventKind.WITHDRAW):
            continue
        if is_topic_edit(event):
            # An edit of a person's topic decides nothing and no run reads it: said as an edit.
            continue
        answer = _answer_word(event)
        who = "Your" if viewer and event.actor == viewer else f"{event.actor}'s"
        place, kind, itself, moved, files = _traced(catalog, event, moved_entries)
        # A label is an annotation shown beside a name, never a decision (``record/targets.py``).
        noun = "label" if event.target.startswith("label:") else "answer"
        opening = f"{who} {noun} {answer!r} on {place} took effect"
        found.append(
            {
                "sequence": event.sequence,
                "actor": event.actor,
                "occurred_at": event.occurred_at,
                "kind": event.kind.value,
                "target": event.target,
                "place": place,
                "answer": answer,
                "itself_changed": itself,
                "files_changed": moved,
                "files_beneath": files,
                "said": _effect_said(opening, kind, itself, moved, files),
            }
        )
    return found


def _withdrawals(
    project: Path,
    *,
    after: int | None,
    up_to: int | None,
    labels: Mapping[str, str],
    viewer: str | None,
) -> list[dict[str, Any]]:
    """Each going back to an earlier run between the two watermarks, said plainly.

    "1 answer withdrawn when you went back to Run 2.": how many decisions it withdrew, and the
    run by its name, never its identifier or the target the Record files it under.
    """
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import read_at, read_events

    if up_to is None:
        return []
    floor = after or 0
    with read_at(project, up_to):
        everything = read_events(project, withdrawn=True)
    kinds = {event.event_id: event.kind for event in everything}
    found: list[dict[str, Any]] = []
    for event in everything:
        if event.kind is not RecordEventKind.WITHDRAW or not floor < event.sequence <= up_to:
            continue
        try:
            payload = json.loads(event.payload or "{}")
        except ValueError:
            payload = {}
        held = payload.get("withdrawn") if isinstance(payload, dict) else None
        ids = [str(item) for item in held or ()]
        notes = sum(1 for item in ids if kinds.get(item) is RecordEventKind.NOTE)
        answers = len(ids) - notes
        restored = payload.get("restored_to") if isinstance(payload, dict) else None
        label = labels.get(str(restored)) if restored else None
        run = label.split(",", 1)[0] if label else "an earlier run"
        who = "you" if viewer and event.actor == viewer else event.actor
        # One answer withdrawn on its own went back to no run.
        alone = isinstance(payload, dict) and "answer" in payload and not restored
        counted = (
            " and ".join(
                part
                for part in (
                    _plural(answers, "answer") if answers else "",
                    _plural(notes, "note") if notes else "",
                )
                if part
            )
            or "nothing"
        )
        found.append(
            {
                "sequence": event.sequence,
                "actor": event.actor,
                "occurred_at": event.occurred_at,
                "answers": answers,
                "notes": notes,
                "went_back_to": "" if alone else run,
                "said": (
                    f"{counted} withdrawn by {who}."
                    if alone
                    else f"{counted} withdrawn when {who} went back to {run}."
                ),
            }
        )
    return found


def _topic_edits(
    project: Path, *, after: int | None, up_to: int | None, viewer: str | None
) -> list[dict[str, Any]]:
    """The edits of a person's topics between the two watermarks, each said as an edit.

    An edit is never an answer, because a topic is an annotation. One event is one edit (made,
    renamed, its members changed, its note changed, withdrawn, brought back), said against the
    topic as the event before it left it. The function reads the Record as the run read it.
    """
    from ingence.api.routes.overlay import target_parts, topic_members
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import read_at, read_events

    if up_to is None:
        return []
    floor = after or 0
    with read_at(project, up_to):
        in_force = read_events(project)
    previous: dict[str, dict[str, Any]] = {}
    found: list[dict[str, Any]] = []
    for event in sorted(in_force, key=lambda item: (item.sequence, item.event_id)):
        parsed = target_parts(event.target)
        if parsed is None or parsed[0] != "group" or event.kind is not RecordEventKind.ANNOTATE:
            continue
        try:
            payload = json.loads(event.payload or "{}")
        except ValueError:
            payload = {}
        before = previous.get(event.target)
        previous[event.target] = payload
        if not floor < event.sequence <= up_to:
            continue
        who = "You" if viewer and event.actor == viewer else event.actor
        now_name = str(payload.get("name") or "")
        was_name = str((before or {}).get("name") or "")
        held = {m.key for m in topic_members(payload, {})} if not payload.get("removed") else set()
        had = (
            {m.key for m in topic_members(before, {})}
            if before and not before.get("removed")
            else set()
        )
        if payload.get("removed"):
            what = "withdrew"
            said = f"{who} withdrew the topic \u201c{was_name or 'a topic'}\u201d."
        elif before is None or before.get("removed"):
            what = "made" if before is None else "brought back"
            verb = "made" if before is None else "brought back"
            said = (
                f"{who} {verb} the topic \u201c{now_name}\u201d with "
                f"{_plural(len(held), 'member')}."
            )
        else:
            clauses = []
            if now_name != was_name:
                clauses.append(f"renamed from \u201c{was_name}\u201d")
            if held - had:
                clauses.append(f"{_plural(len(held - had), 'member')} added")
            if had - held:
                clauses.append(f"{_plural(len(had - held), 'member')} removed")
            if (payload.get("note") or None) != (before.get("note") or None):
                clauses.append("its note changed")
            what = "changed"
            said = (
                f"{who} changed the topic \u201c{now_name}\u201d"
                + (f": {', '.join(clauses)}" if clauses else "")
                + "."
            )
        found.append(
            {
                "sequence": event.sequence,
                "actor": event.actor,
                "occurred_at": event.occurred_at,
                "target": event.target,
                "topic_id": parsed[1][0],
                "name": now_name or was_name,
                "what": what,
                "members": len(held),
                "said": said,
            }
        )
    return found


def _topics(
    project: Path,
    run_id: str,
    compared: str | None,
    reader: TopicReader | None,
    why_not: str | None,
    watermark: int | None,
) -> dict[str, Any]:
    """Experiments that joined or left a person's topics between the compared run and this one.

    Each topic is read the same way in both runs, so what is said is the runs' difference: "2
    experiments joined \u201cNuclear counts\u201d: each now carries the words ...". Where a person
    made no topic, nothing is said. The topics are those that the Record held at the run's
    ``watermark`` (a topic made or withdrawn after the run is not part of what it did). A run that
    states no watermark cannot say what its topics were, so where the Record holds any topic at
    all they are not computed. A reader that fails or is not available leaves topics not
    computed, with its reason in ``why_not``. An unknown is said to be unknown and never left
    absent.
    """
    from ingence.api.routes.overlay import topic_events_exist, topics_standing

    def not_computed(reason: str) -> dict[str, Any]:
        return {
            "computed": False,
            "why_not": reason,
            "topics": [],
            "said": ["Topics: topics not computed."],
        }

    if watermark is None:
        if topic_events_exist(project):
            return not_computed(
                "this run states no Record watermark, so the topics it was made with are not known"
            )
        return {"computed": True, "why_not": None, "topics": [], "said": []}
    if not topics_standing(project, up_to=watermark):
        return {"computed": True, "why_not": None, "topics": [], "said": []}
    if reader is None:
        return not_computed(why_not or "no topic reader is available")
    try:
        rows = list(reader(project, run_id, compared) or [])
    except Exception as error:  # an optional component failing is said, never a crash
        return not_computed(f"the topic reader could not read the runs: {error}")
    said: list[str] = []
    for row in rows:
        said.extend(str(sentence) for sentence in row.get("said") or ())
    return {"computed": True, "why_not": None, "topics": rows, "said": said}


def _themes(
    project: Path,
    run_id: str,
    parent: str | None,
    reader: ThemeReader | None,
    why_not: str | None,
) -> dict[str, Any]:
    """Experiments that joined or left a theme between the earlier run and this one.

    An experiment in no theme is never a theme (the themes package gives it ``None``). It is
    counted apart, "1 experiment is in no theme now (1 moved out of a theme)", and a theme's count
    is out of the experiments placed in a theme. A reader that fails (an extra not installed, a
    run it cannot read) leaves themes not computed, with its reason in ``why_not``.
    """

    def not_computed(reason: str) -> dict[str, Any]:
        return {
            "computed": False,
            "joined": [],
            "why_not": reason,
            "said": ["Themes: themes not computed."],
        }

    if reader is None or parent is None:
        return not_computed(why_not or "this run has no earlier run to compare with")
    try:
        later = dict(reader(project, run_id) or {})
        earlier = dict(reader(project, parent) or {})
    except Exception as error:  # an optional component failing is said, never a crash
        return not_computed(f"the theme reader could not read the runs: {error}")
    if not later and not earlier:
        return not_computed("the theme reader found no themes in either run")
    joined: dict[str, int] = {}
    left: dict[str, int] = {}
    for experiment, label in later.items():
        if label is not None and earlier.get(experiment) != label:
            joined[str(label)] = joined.get(str(label), 0) + 1
    for experiment, label in earlier.items():
        if label is not None and later.get(experiment) != label:
            left[str(label)] = left.get(str(label), 0) + 1
    placed = sum(1 for label in later.values() if label is not None)
    unplaced = [experiment for experiment, label in later.items() if label is None]
    moved_out = sum(1 for experiment in unplaced if earlier.get(experiment) is not None)
    said = [
        f"{count:,} of {_plural(placed, 'experiment')} joined theme {label}."
        for label, count in sorted(joined.items(), key=lambda item: (-item[1], item[0]))
    ]
    said += [
        f"{count:,} {'experiment' if count == 1 else 'experiments'} left theme {label}."
        for label, count in sorted(left.items(), key=lambda item: (-item[1], item[0]))
        if label not in joined
    ]
    if unplaced:
        said.append(
            f"{_plural(len(unplaced), 'experiment')} {'is' if len(unplaced) == 1 else 'are'} in "
            "no theme now" + (f" ({moved_out:,} moved out of a theme)" if moved_out else "") + "."
        )
    return {
        "computed": True,
        "why_not": None,
        "joined": [
            {"theme": label, "experiments": count, "out_of": placed}
            for label, count in sorted(joined.items(), key=lambda item: (-item[1], item[0]))
        ],
        "said": said or ["No experiment changed theme."],
    }


def _labels(project: Path) -> dict[str, str]:
    from ingence.api.run_names import project_run_names

    return {run: str(named.get("label")) for run, named in project_run_names(project).items()}


_NOT_GIVEN: Any = object()


def _quality(project: Path, run_id: str, compared: str | None) -> dict[str, Any]:
    """The run's quality figures beside the compared run's.

    Every row is a count with the whole it is out of, or a duration, with the compared run's value
    beside it and the change. A run that an older engine wrote stored none, and the row says so.
    The rows come from the two runs' stored figures. Nothing is recomputed and nothing is
    estimated.
    """
    from ingence.quality.compare import (
        asdict,
        compare_quality,
        figures_of,
        version_changes,
        versions_of,
    )

    now = figures_of(project, run_id)
    if not now:
        return {
            "available": False,
            "why_not": "This run was made by an engine that kept no quality figures.",
            "phase": None,
            "rows": [],
            "said": [],
            "versions": [],
            "versions_said": None,
        }
    was = figures_of(project, compared) if compared else None
    why: str | None = None
    if compared and not was:
        why = (
            "The run it is compared with was made by an engine that kept no quality figures, "
            "so nothing is set beside these."
        )
    rows = compare_quality(now, was if was or not compared else [])
    versions: list[dict[str, str]] = []
    versions_said: str | None = None
    if compared:
        versions, versions_said = version_changes(
            versions_of(project, run_id), versions_of(project, compared)
        )
    return {
        "available": True,
        "why_not": why,
        "phase": now[0].phase,
        "rows": [asdict(row) for row in rows],
        "said": [row.said for row in rows if row.changed and not row.volatile],
        "versions": versions,
        "versions_said": versions_said,
    }


def manifest(
    project: Path,
    run_id: str | None = None,
    *,
    viewer: str | None = None,
    themes: ThemeReader | None = _NOT_GIVEN,
    topics: TopicReader | None = _NOT_GIVEN,
    values_named: int = VALUES_NAMED,
    sentences_shown: int = SENTENCES_SHOWN,
) -> dict[str, Any]:
    """Return one run's manifest: what changed from its parent, in sentences with their counts.

    ``run_id`` defaults to the run the project's pointer names. ``viewer`` is who is reading: an
    answer of theirs is "Your answer", anyone else's is named. ``themes`` is the theme reader;
    left out, the first available in :data:`THEME_READERS`; ``None``, none. ``topics`` is the topic
    reader, chosen the same way from :data:`TOPIC_READERS`.
    """
    from ingence.api.routes.changes import _before_views, _compared_with, runs
    from ingence.storage.catalog import open_catalog
    from ingence.storage.commit import read_pointer

    if run_id is None:
        pointer = read_pointer(project)
        if pointer is None:
            message = "this project has no published run"
            raise LookupError(message)
        run_id = pointer.run_id
    reader, why_not = _default_themes() if themes is _NOT_GIVEN else (themes, None)
    if themes is None:
        why_not = "no theme reader was asked for"
    topic_reader, topics_why_not = _default_topics() if topics is _NOT_GIVEN else (topics, None)
    if topics is None:
        topics_why_not = "no topic reader was asked for"
    labels = _labels(project)
    # Compared with the run before it of its own kind (the Changes timeline's rule): an
    # inference with the inference before it, a census or a rebind with the one before it, and
    # the run it was built from where there is none. A rebind's parent is the inference it
    # followed, and comparing names read again with conclusions says only that the conclusions
    # are absent.
    listed = runs(project)["runs"]
    this = next((run for run in listed if run["run_id"] == run_id), None)
    compared = _compared_with(listed, this) if this is not None else None
    with open_catalog(project, run_id=run_id) as catalog:
        own = _run_row(catalog)
        parent = own.get("parent_run_id") or None
        compared = compared or parent
        watermark = _watermark(own)
        base: dict[str, Any] = {
            "run_id": run_id,
            "run_label": labels.get(run_id),
            "parent_run_id": parent,
            "parent_label": labels.get(str(parent)) if parent else None,
            "compared_run_id": compared,
            "compared_label": labels.get(str(compared)) if compared else None,
            "watermark": {"from": None, "to": watermark},
        }
        if compared is None:
            decisions = _decisions(
                catalog, project, after=0, up_to=watermark, moved_entries=set(), viewer=viewer
            )
            went_back = _withdrawals(
                project, after=0, up_to=watermark, labels=labels, viewer=viewer
            )
            edits = _topic_edits(project, after=0, up_to=watermark, viewer=viewer)
            said = ["This is the first run: there is nothing before it to compare."]
            return {
                **base,
                "said": said
                + [decision["said"] for decision in decisions]
                + [withdrawal["said"] for withdrawal in went_back]
                + [edit["said"] for edit in edits],
                "decisions": decisions,
                "withdrawals": went_back,
                "topic_edits": edits,
                "rules": [],
                "claims": [],
                "claims_not_said": 0,
                "flags": [],
                "pairs": [],
                "themes": _themes(project, run_id, None, reader, why_not),
                "topics": _topics(project, run_id, None, topic_reader, topics_why_not, watermark),
                "quality": _quality(project, run_id, None),
            }
        with open_catalog(project, run_id=str(compared)) as earlier:
            parent_watermark = _watermark(_run_row(earlier))
        _before_views(catalog, project, str(compared))
        _rules_view(catalog, project, str(compared))
        moved = _rows(catalog, _MOVED_SQL)
        flagged = _rows(catalog, _FLAGGED_SQL)
        totals = _totals(catalog)
        versions = {
            str(rule): str(version)
            for rule, version in catalog.query("SELECT rule_id, version FROM rules").fetchall()
        }
        moved_entries = {str(row["entry_id"]) for row in (*moved, *flagged)}
        decisions = _decisions(
            catalog,
            project,
            after=parent_watermark,
            up_to=watermark,
            moved_entries=moved_entries,
            viewer=viewer,
        )
        went_back = _withdrawals(
            project, after=parent_watermark, up_to=watermark, labels=labels, viewer=viewer
        )
        edits = _topic_edits(project, after=parent_watermark, up_to=watermark, viewer=viewer)
        rules = _rules_changed(catalog)
        claims = _readings_moved(moved, totals, versions=versions, values_named=values_named)
        flags = _readings_moved(
            flagged, totals, versions=versions, values_named=values_named, flags=True
        )
        pairs = _pairs_moved(catalog)
    themed = _themes(project, run_id, str(compared), reader, why_not)
    topical = _topics(project, run_id, str(compared), topic_reader, topics_why_not, watermark)
    shown_claims = claims[:sentences_shown]
    said = []
    if compared != parent:
        named = (labels.get(str(compared)) or "an earlier run").split(",", 1)[0]
        said.append(f"Compared with {named}, the run before it of its own kind.")
    if not (decisions or rules or claims or flags or pairs or edits):
        said.append("Nothing changed from the run before: no answer, rule, reading or pair.")
    said += [decision["said"] for decision in decisions]
    said += [withdrawal["said"] for withdrawal in went_back]
    said += [edit["said"] for edit in edits]
    said += [rule["said"] for rule in rules]
    said += [group["said"] for group in shown_claims]
    if len(claims) > len(shown_claims):
        rest = claims[len(shown_claims) :]
        said.append(
            f"And {sum(int(group['count']) for group in rest):,} more readings moved, in "
            f"{_plural(len({group['rule_id'] for group in rest}), 'rule')}."
        )
    said += [flag["said"] for flag in flags[:sentences_shown]]
    said += [pair["said"] for pair in pairs]
    said += themed["said"]
    said += topical["said"]
    return {
        **base,
        "watermark": {"from": parent_watermark, "to": watermark},
        "said": said,
        "decisions": decisions,
        "withdrawals": went_back,
        "topic_edits": edits,
        "rules": rules,
        "claims": claims,
        "claims_not_said": max(0, len(claims) - len(shown_claims)),
        "flags": flags,
        "pairs": pairs,
        "themes": themed,
        "topics": topical,
        "quality": _quality(project, run_id, str(compared)),
    }


def _rules_view(catalog: Catalog, project: Path, before: str) -> None:
    """The parent's rules table beside the run's, as ``before_rules``."""
    from ingence.storage.catalog import sql_path_list
    from ingence.storage.commit import run_path

    fragments = sorted((run_path(project, before) / "rules").glob("*.parquet"))
    if fragments:
        catalog.query(
            "CREATE OR REPLACE VIEW before_rules AS "  # noqa: S608
            f"SELECT * FROM read_parquet({sql_path_list(fragments)}, union_by_name = true)"
        )
    else:
        catalog.query("CREATE OR REPLACE VIEW before_rules AS SELECT * FROM rules WHERE false")
