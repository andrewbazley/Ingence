"""The question sheet: what a person is asked, each answer settling a whole class.

The engine builds the sheet from one published run and writes it nowhere. The sheet assumes that
every grouping and relation is true. A person is asked only what the run cannot settle, and each
question settles a class. The sheet has five kinds of question, assembled in this order:

1. pack: each vocabulary pack the engine proposes (or a loaded one that reads nothing), from the
   Questions page's own rows, so its words are the page's: "Load the microscopy vocabulary pack?".
   The pack questions lead, because a loaded pack changes the words that every later question is
   asked in;
2. tied word: each word whose name stands between senses the Dictionary knows, as the page's own
   question, most files first. The sheet makes no guess at the sense from the words around the
   word, because such a guess disagreed with a person's reading too often. So a tie stays a
   person's question;
3. audit: one per class of classifying claims, a rule and the value it concluded, largest first,
   with three examples drawn from the class's key (``review/claim_classes.py``). An audit is
   answered on the class's own Record target, so one answer settles every claim in it;
4. derivation pattern: the proposed derivations reduced to their rule, the two extensions, the two
   stages and the name pattern with its varying parts marked (:func:`name_template`);
5. atypical pair: the pairs of a pattern seen too rarely to be a habit, or whose made file is older
   than what it was made from.

The order on the sheet is: every pack; the tied words (at most ``tied_most``); the audits covering
``audit_cover`` of the claims (at most ``audit_first``); the patterns covering ``pattern_cover`` of
the pairs (at most ``pattern_first``); the atypical pairs (at most ``atypical_most``); then the
remaining audits and patterns by how much each settles, up to the budget.

A question answered since the run stays on the sheet, with its answer, until the next run. A
decision is "waiting" when its sequence is above the Record watermark that the run read
(``run_watermark``). A question whose answer the run itself read leaves the sheet. So the sheet
that an answer is given on does not change under the person answering, and it does not refill its
budget with the next question. The answer shows on its own question (``answer``) until Re-run
reads it.

Audit classes are keyed by rule and value. This is the key that the Record's class target and a
claim's class handle use. The sheet leaves out the rules whose values are one per file (a time,
say), by the shape of what they concluded, as the claim samples leave them out. A class of one
file is not a class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.review.claim_classes import class_members, drawn_examples, summarised

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.core.settings import QuestionSettings
    from ingence.storage.catalog import Catalog

__all__ = [
    "TEMPLATE_MEANING",
    "drawn_examples",
    "name_template",
    "question_sheet",
]

#: The kinds, as the sheet spells them.
PACK, TIED, AUDIT, PATTERN, ATYPICAL = (
    "pack",
    "tied word",
    "audit",
    "derivation pattern",
    "atypical pair",
)


#: What ``{src}`` and ``#`` stand for in a name pattern, said beside every pattern's question.
TEMPLATE_MEANING = (
    "In the name, [source] stands for the name of the file it was made from, without its "
    "extension, and # for any number."
)

#: What is said of a made file's name that does not hold the name of the file it was made from.
NO_STEM_SAID = "Their names do not hold the name of the file they were made from."


def readable_template(template: str) -> tuple[str, bool]:
    """A name pattern as a person reads it, and whether the names hold their source's name.

    ``{src}`` is written ``[source]``, and the engine's ``(no stem)`` mark (a made name that does
    not hold its source's name) is taken out of the name; the second answer says it was there.
    """
    holds = not template.startswith(NO_STEM_MARK)
    name = template.removeprefix(NO_STEM_MARK).replace("{src}", "[source]")
    return name, holds


def _said(value: str) -> str:
    """A concluded value as the pages say it: the Dictionary's term, its underscores as spaces."""
    return value.replace("_", " ").strip()


_DIGITS = re.compile(r"\d+")
#: The mark a pattern starts with where the made name does not hold its source's name.
NO_STEM_MARK = "(no stem) "


def name_template(source_name: str, target_name: str) -> str:
    """A made file's name as a pattern: the source's stem as ``{src}``, every number as ``#``.

    The source's name lower-cased without its last extension is its stem; where the made file's
    lower-cased name holds it, each place is marked ``{src}``, and otherwise the pattern says
    ``(no stem)``. Then each run of digits is ``#``. ``zeph_007.nd2`` and ``tracks_zeph_007.csv``
    give ``tracks_{src}.csv``; with ``summary_2.xlsx``, ``(no stem) summary_#.xlsx``.
    """
    stem = source_name.lower()
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    made = target_name.lower()
    templated = made.replace(stem, "{src}") if stem and stem in made else f"{NO_STEM_MARK}{made}"
    return _DIGITS.sub("#", templated)


def _cumulative_count(sizes: Sequence[int], cover: float) -> int:
    """How many of ``sizes`` (largest first) it takes to cover ``cover`` of their sum.

    The count is the number of sizes whose cumulative share stays below ``cover``, plus one, and
    never more than the number of sizes.
    """
    total = sum(sizes)
    if not total:
        return 0
    held = 0
    count = 0
    for size in sizes:
        held += size
        if held / total < cover:
            count += 1
    return min(count + 1, len(sizes))


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


#: What a file at the top of a root is said to lie in, as the Dashboard's folder filters say it.
TOP_LEVEL = "(top level)"


def tops_of(paths: Iterable[str], *, folders: bool = False) -> list[str]:
    """The top-level folders that a set of paths lie in, sorted.

    The top-level folder is the first component of each path. A path with no folder is listed
    under :data:`TOP_LEVEL`. With ``folders``, such a path is a folder at the top and is its own
    top-level folder.
    """
    found: set[str] = set()
    for path in paths:
        spelled = str(path)
        if "/" in spelled:
            found.add(spelled.split("/", 1)[0])
        elif folders and spelled:
            found.add(spelled)
        else:
            found.add(TOP_LEVEL)
    return sorted(found)


def _counted(count: int, kinds: set[bool]) -> str:
    """How many a class holds, said as files, folders, or both, by what its members are."""
    if kinds == {True}:
        return _plural(count, "folder", "folders")
    if kinds == {False}:
        return _plural(count, "file", "files")
    return f"{count} files and folders"


@dataclass
class _Question:
    kind: str
    question: str
    settles: str
    settles_n: int
    examples: list[str]
    key: str
    target: str | None = None
    rule_id: str | None = None
    value: str | None = None
    size: int = 0
    template: str | None = None
    #: What the question rests on, in plain words: the rule's sentence, or what a pattern's marks
    #: stand for.
    meaning: str = ""
    #: For an audit: whether an answer can retract the class's readings. It is false for a proven
    #: reading.
    retractable: bool | None = None
    #: The version of the rule the question rests on, in the run (None for a question of no rule).
    rule_version: str | None = None
    #: For a tied word: the meanings the Dictionary knows for it, as the page offers them.
    senses: list[dict[str, Any]] | None = None
    #: The answer given since the run, still waiting for Re-run to read it; None otherwise.
    answer: dict[str, Any] | None = None
    #: The top-level folders that every file of the class lies in, not only the examples drawn. A
    #: page's folder filter reads this list. It is empty where the question's own list holds the
    #: folders (a pack's or a tied word's, which the Questions page's queue counts over every file).
    tops: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_row(self, number: int) -> dict[str, Any]:
        return {
            "n": number,
            "kind": self.kind,
            "question": self.question,
            "settles": self.settles,
            "settles_n": self.settles_n,
            "examples": list(self.examples),
            "key": self.key,
            "target": self.target,
            "rule_id": self.rule_id,
            "value": self.value,
            "size": self.size,
            "template": self.template,
            "meaning": self.meaning,
            "retractable": self.retractable,
            "rule_version": self.rule_version,
            "senses": self.senses,
            "answer": self.answer,
            "tops": list(self.tops),
        }


def _page_rows(catalog: Catalog, project: Path, watermark: int | None) -> list[dict[str, Any]]:
    """The Questions page's rows that a sheet is drawn from, in the page's order.

    The rows are the questions a person is asked, and those answered since the run (waiting for
    Re-run), which stay with their answer. A row whose answer the run itself read is the page's
    "answered" row, and it leaves the sheet. With no watermark (a run that recorded none), every
    answered row leaves the sheet.
    """
    from ingence.api.routes import pages

    found = pages.page(catalog, project, "questions", limit=1_000_000, answered=True)
    kept: list[dict[str, Any]] = []
    for row in found.get("rows") or []:
        held = row.get("in_force")
        if held and (watermark is None or int(held.get("sequence") or 0) <= watermark):
            continue
        kept.append(row)
    return kept


def _waiting(held: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """An answer in force, as a sheet row carries it while it waits for Re-run.

    "Can't say" is served as ``cannot_say`` with its label (``Other``), whatever the question.
    Where it is the decision in force (a tied word's or an audit's), its event is an annotation
    that carries no verdict word. The event's own kind (``annotate``) is not a word a person said.
    """
    from ingence.record.verdicts import shown_word

    if not held:
        return None
    word = str(held.get("word") or "")
    label = str(held.get("label") or word)
    if held.get("kind") == "annotate" and word == "annotate":
        word, label = "cannot_say", shown_word("cannot_say")
    return {
        "word": word,
        "label": label,
        "actor": str(held.get("actor") or ""),
        "occurred_at": str(held.get("occurred_at") or ""),
        "sequence": int(held.get("sequence") or 0),
        "note": held.get("note") or None,
        "waits_for_rerun": True,
    }


def _pack_examples(catalog: Catalog, key: str, spellings: Sequence[str]) -> list[str]:
    """Three files, drawn by the question's key, whose names carry a spelling the pack would read.

    The spellings are those the question lists (the unread ones the pack would read here), found
    among the name parts the census recorded; folders are not offered, because the question counts
    files. The same three every time for one run.
    """
    wanted = sorted({spelling.casefold() for spelling in spellings if spelling})
    if not wanted:
        return []
    found = catalog.query(
        "SELECT DISTINCT e.relative_path FROM components c JOIN entries e USING (entry_id) "
        "WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory' "
        "AND c.source_scope = 'name' AND (lower(c.normalized_value) IN "
        "(SELECT unnest(?::VARCHAR[])) OR lower(c.raw_value) IN (SELECT unnest(?::VARCHAR[]))) "
        "ORDER BY e.relative_path LIMIT 5000",
        [wanted, wanted],
    ).fetchall()
    return drawn_examples(key, (str(path) for (path,) in found))


def _relative_paths(catalog: Catalog, subjects: Sequence[str]) -> dict[str, str]:
    """Each subject's path relative to its root: the way an audit's examples are written."""
    wanted = sorted({subject for subject in subjects if subject})
    if not wanted:
        return {}
    return {
        str(subject): str(path)
        for subject, path in catalog.query(
            "SELECT s.subject, e.relative_path FROM (SELECT unnest(?::VARCHAR[]) AS subject) s "
            "JOIN entries e ON e.entry_id = regexp_replace(s.subject, '^folder:', '')",
            [wanted],
        ).fetchall()
    }


def _packs(catalog: Catalog, rows: Sequence[Mapping[str, Any]]) -> list[_Question]:
    found: list[_Question] = []
    for row in rows:
        proposal = row.get("proposal") or {}
        if proposal.get("kind") != "pack":
            continue
        files = int(proposal.get("files") or row.get("entries_affected") or 0)
        spellings = [str(item) for item in proposal.get("spellings") or ()]
        key = str(proposal["target"])
        found.append(
            _Question(
                kind=PACK,
                question=str(proposal.get("question") or row.get("heading") or ""),
                settles="files whose names the pack would read",
                settles_n=files,
                # Files, not the pack's spellings: what a person is shown is what would be read.
                examples=_pack_examples(catalog, key, spellings),
                key=key,
                target=key,
                rule_id=str(row.get("rule_id") or "") or None,
                value=str(proposal.get("names") or "") or None,
                size=files,
                answer=_waiting(row.get("in_force")),
            )
        )
    return found


def _subjects_of(row: Mapping[str, Any]) -> list[str]:
    """The identifiers of the files a question row samples: its subjects, else its files'."""
    named = [str(subject) for subject in row.get("subjects") or () if subject]
    return named or [
        str(item["id"]) for item in row.get("subject_files") or () if item and item.get("id")
    ]


def _tied(catalog: Catalog, rows: Sequence[Mapping[str, Any]], most: int) -> list[_Question]:
    candidates = [
        row
        for row in rows
        if row.get("about_a_word")
        and len([s for s in row.get("senses") or () if s.get("term")]) >= 2
    ]
    # Examples are paths relative to the root, as an audit's are.
    relative = _relative_paths(
        catalog, [subject for row in candidates for subject in _subjects_of(row)]
    )
    found: list[_Question] = []
    for row in candidates:
        offered_senses = [sense for sense in row.get("senses") or () if sense.get("term")]
        senses = [str(sense.get("term")) for sense in offered_senses]
        word = str(row.get("raised") or "")
        affected = int(row.get("entries_affected") or 0)
        offered = ", ".join(senses[:-1]) + f" or {senses[-1]}"
        target = str(row.get("target") or "")
        paths = [relative[subject] for subject in _subjects_of(row) if subject in relative]
        found.append(
            _Question(
                kind=TIED,
                question=f'What does "{word}" mean here: {offered}?',
                settles="files whose names hold the word",
                settles_n=affected,
                examples=drawn_examples(target, paths),
                key=target,
                target=target,
                rule_id=str(row.get("rule_id") or "") or None,
                value=word,
                size=affected,
                senses=[
                    {"term": str(sense["term"]), "theme_kind": sense.get("theme_kind") or None}
                    for sense in offered_senses
                ],
                answer=_waiting(row.get("in_force")),
            )
        )
    found.sort(key=lambda question: (-question.settles_n, question.key))
    return found[:most]


def _a_structure(value: str) -> bool:
    """Whether a concluded value is a structure (a JSON object or list).

    A structure is never a class to audit. A time or a date neighbourhood belongs to one file,
    and the sheet asks about values alone.
    """
    import json

    if value[:1] not in ("{", "["):
        return False
    try:
        return isinstance(json.loads(value), (dict, list))
    except ValueError:
        return False


#: What a proven class's question says beside it.
PROVEN = "This reading is proven, not supposed: an answer is recorded but cannot change it."


def _retractable_rules(catalog: Catalog) -> frozenset[str]:
    """The rules whose tier lets a person's answer retract their claims."""
    from ingence.review.claim_classes import RETRACTABLE_TIERS

    return frozenset(
        str(rule_id)
        for rule_id, tier in catalog.query("SELECT rule_id, tier FROM rules").fetchall()
        if str(tier) in RETRACTABLE_TIERS
    )


def _rule_versions(catalog: Catalog) -> dict[str, str]:
    """Each rule's version in the run: the version a question's answer is about."""
    return {
        str(rule_id): str(version)
        for rule_id, version in catalog.query("SELECT rule_id, version FROM rules").fetchall()
    }


def _answers(
    project: Path, watermark: int | None
) -> tuple[frozenset[str], dict[str, dict[str, Any]]]:
    """The targets a decision is in force on, split by whether the run read it.

    The first is every target whose decision the run read. Its sequence is at or below the
    watermark, or, with no watermark, any decision counts. Its class leaves the sheet. The second
    is the answers given since, by target, each as a sheet row carries it. They stay on the sheet.
    """
    from ingence.api.routes import pages
    from ingence.record.log import latest_by_target, read_events

    now = latest_by_target(read_events(project))
    read = {
        target for target, event in now.items() if watermark is None or event.sequence <= watermark
    }
    waiting = {
        target: shown
        for target, event in now.items()
        if target not in read and (shown := _waiting(pages._in_force(event))) is not None
    }
    return frozenset(read), waiting


def _audits(
    catalog: Catalog,
    read: frozenset[str] = frozenset(),
    waiting: Mapping[str, dict[str, Any]] | None = None,
) -> list[_Question]:
    """One question per class of claims the run did not read an answer on, largest first.

    A class answered since the run (``waiting``) stays, with its answer.

    The members come from ``review/claim_classes.class_members``, so the handle and inference
    count the same files. A rule whose values are one per file (a time) is not a class to ask
    about, and neither is a value that is a structure.
    """
    from ingence.api.routes import pages
    from ingence.review.claim_classes import program_regions

    per_file = set(pages._per_file_rules(catalog, program_regions(catalog)))
    versions = {
        (str(rule), str(value)): (str(version), str(predicate))
        for rule, value, version, predicate in catalog.query(
            "SELECT rule_id, object_reference, max(rule_version), max(predicate) FROM claims "
            "WHERE predicate LIKE 'classifies.%' AND object_reference IS NOT NULL "
            "GROUP BY rule_id, object_reference"
        ).fetchall()
    }
    retractable = _retractable_rules(catalog)
    found: list[_Question] = []
    for (rule_id, value), members in class_members(catalog).items():
        if rule_id in per_file or _a_structure(value):
            continue
        version, predicate = versions.get((rule_id, value), ("", ""))
        held = summarised(
            rule_id,
            value,
            [(member.claim_id, member.subject, member.path) for member in members],
            rule_version=version,
            predicate=predicate,
            retractable=rule_id in retractable,
        )
        if held.target in read:
            continue
        counted = _counted(held.size, {member.is_folder for member in members})
        found.append(
            _Question(
                kind=AUDIT,
                question=f'{counted} read as "{_said(value)}": right?',
                settles="claims",
                settles_n=held.size,
                examples=list(held.examples),
                key=held.target,
                target=held.target,
                rule_id=rule_id,
                value=value,
                size=held.size,
                meaning="" if held.retractable else PROVEN,
                retractable=held.retractable,
                answer=(waiting or {}).get(held.target),
                tops=sorted(
                    {
                        top
                        for member in members
                        for top in tops_of([member.path], folders=member.is_folder)
                    }
                ),
            )
        )
    found.sort(key=lambda question: (-question.settles_n, question.key))
    return found


@dataclass(frozen=True, slots=True)
class _Pair:
    rule_id: str
    source: str
    target: str
    pattern: tuple[str, str, str, str]
    target_older: bool


def _stages(catalog: Catalog) -> dict[str, str]:
    """Each file's stage as one word, by the stage senses in the run's Dictionary.

    The word is the file's resolved stage value. A file with several stage values gets them joined
    with ``+``. The reading matches the Explorer projection's.
    """
    rows = catalog.query(
        """
        SELECT c.subject, list_sort(list(DISTINCT c.object_reference)) AS stages
        FROM claims c
        WHERE c.predicate LIKE 'classifies.%' AND c.resolution_status = 'resolved'
          AND c.object_reference IN (
              SELECT term FROM dictionary_entries WHERE theme_kind = 'stage')
        GROUP BY c.subject
        """
    ).fetchall()
    return {str(subject): "+".join(str(stage) for stage in stages) for subject, stages in rows}


def _pairs(catalog: Catalog) -> list[_Pair]:
    """Every proposed derivation whose two ends are live files outside installed programs."""
    from ingence.api.routes import pages

    regions = pages._regions(catalog)
    rows = catalog.query(
        """
        SELECT c.rule_id, c.source_id, c.target_id, s.name AS source_name,
               t.name AS target_name, s.relative_path AS source_path,
               t.relative_path AS target_path,
               lower(coalesce(s.extension, '')) AS source_extension,
               lower(coalesce(t.extension, '')) AS target_extension
        FROM candidates c
        JOIN entries s ON s.entry_id = c.source_id AND NOT s.is_deleted
        JOIN entries t ON t.entry_id = c.target_id AND NOT t.is_deleted
        WHERE c.primitive = 'derives'
        ORDER BY c.rule_id, s.relative_path, t.relative_path, c.candidate_id
        """
    ).fetchall()
    stages = _stages(catalog)
    times = {
        str(subject): str(at)
        for subject, at in catalog.query(
            "SELECT subject, min(json_extract_string(object_reference, '$.at')) FROM claims "
            "WHERE predicate = 'classifies.earliest_credible_time' "
            "AND resolution_status = 'resolved' GROUP BY subject"
        ).fetchall()
        if at
    }
    found: list[_Pair] = []
    for row in rows:
        (rule_id, source, target, source_name, target_name, source_path, target_path) = row[:7]
        if any(
            str(path) == region or str(path).startswith(region + "/")
            for region in regions
            for path in (source_path, target_path)
        ):
            continue
        source_at, target_at = times.get(str(source)), times.get(str(target))
        found.append(
            _Pair(
                rule_id=str(rule_id),
                source=str(source_path),
                target=str(target_path),
                pattern=(
                    str(rule_id),
                    f"{row[7] or '(none)'}->{row[8] or '(none)'}",
                    f"{stages.get(str(source), '(no stage)')}->"
                    f"{stages.get(str(target), '(no stage)')}",
                    name_template(str(source_name), str(target_name)),
                ),
                target_older=bool(source_at and target_at and target_at < source_at),
            )
        )
    return found


def _patterns(pairs: Sequence[_Pair]) -> tuple[list[_Question], dict[tuple[str, ...], int]]:
    grouped: dict[tuple[str, str, str, str], list[_Pair]] = {}
    for pair in pairs:
        grouped.setdefault(pair.pattern, []).append(pair)
    found: list[_Question] = []
    support: dict[tuple[str, ...], int] = {}
    for pattern, held in grouped.items():
        rule_id, extensions, stages, template = pattern
        support[pattern] = len(held)
        source_extension, target_extension = extensions.split("->", 1)
        source_stage, target_stage = stages.split("->", 1)
        key = "pattern:" + "|".join(pattern)
        name, holds = readable_template(template)
        found.append(
            _Question(
                kind=PATTERN,
                question=(
                    f'Are the {target_extension} files named "{name[:60]}" made from the '
                    f"{source_extension} files ({_said(source_stage)} to {_said(target_stage)})? "
                    f"{_plural(len(held), 'pair', 'pairs')}."
                    + ("" if holds else f" {NO_STEM_SAID}")
                ),
                settles="pairs",
                settles_n=len(held),
                examples=drawn_examples(key, (f"{p.source} -> {p.target}" for p in held)),
                key=key,
                rule_id=rule_id,
                value=template,
                size=len(held),
                template=template,
                meaning=TEMPLATE_MEANING,
                tops=tops_of(path for p in held for path in (p.source, p.target)),
            )
        )
    found.sort(key=lambda question: (-question.settles_n, question.key))
    return found, support


def _atypical(pairs: Sequence[_Pair], parameters: QuestionSettings) -> list[_Question]:
    threshold = max(parameters.atypical_floor, parameters.atypical_share * len(pairs))
    grouped: dict[tuple[str, str, str, str], list[_Pair]] = {}
    for pair in pairs:
        grouped.setdefault(pair.pattern, []).append(pair)
    found: list[_Question] = []
    for pattern, held in grouped.items():
        rare = len(held) <= threshold
        odd = [pair for pair in held if rare or pair.target_older]
        if not odd:
            continue
        older = any(pair.target_older for pair in odd)
        why = (
            "the made file is older than what it was made from"
            if older
            else f"a pattern seen in no more than {int(threshold)} pairs"
        )
        key = "atypical:" + "|".join(pattern)
        asked = "is this pair real" if len(odd) == 1 else f"are these {len(odd)} pairs real"
        name, holds = readable_template(pattern[3])
        found.append(
            _Question(
                kind=ATYPICAL,
                question=(
                    f'Made files named "{name[:60]}": {why}; {asked}?'
                    + ("" if holds else f" {NO_STEM_SAID}")
                ),
                settles="pairs",
                settles_n=len(odd),
                examples=drawn_examples(key, (f"{p.source} -> {p.target}" for p in odd)),
                key=key,
                rule_id=pattern[0],
                value=pattern[3],
                size=len(odd),
                template=pattern[3],
                meaning=TEMPLATE_MEANING,
                tops=tops_of(path for p in odd for path in (p.source, p.target)),
                extra={"pattern": "pattern:" + "|".join(pattern)},
            )
        )
    found.sort(key=lambda question: (-question.settles_n, question.key))
    return found


def question_sheet(
    catalog: Catalog, project: Path, parameters: QuestionSettings | None = None
) -> dict[str, Any]:
    """The sheet of one run: its questions numbered in order, and what answering them settles."""
    from ingence.completeness.elsewhere import run_watermark
    from ingence.core.settings import QuestionSettings

    held = parameters or QuestionSettings()
    watermark = run_watermark(catalog)
    rows = _page_rows(catalog, project, watermark)
    packs = _packs(catalog, rows)
    tied = _tied(catalog, rows, held.sheet_tied_words)
    read, waiting = _answers(project, watermark)
    audits = _audits(catalog, read, waiting)
    pairs = _pairs(catalog)
    patterns, _ = _patterns(pairs)
    atypical = _atypical(pairs, held)
    classes_needed = _cumulative_count([q.settles_n for q in audits], held.audit_cover)
    patterns_needed = _cumulative_count([q.settles_n for q in patterns], held.pattern_cover)
    first_audits = audits[: min(held.audit_first, classes_needed)]
    first_patterns = patterns[: min(held.pattern_first, patterns_needed)]
    rest = sorted(
        [*audits[len(first_audits) :], *patterns[len(first_patterns) :]],
        key=lambda question: (-question.settles_n, question.kind, question.key),
    )
    ordered = [
        *packs,
        *tied,
        *first_audits,
        *first_patterns,
        *atypical[: held.atypical_most],
        *rest,
    ][: held.sheet_budget]
    versions = _rule_versions(catalog)
    for question in ordered:
        if question.rule_id:
            question.rule_version = versions.get(question.rule_id)
    on_sheet_audits = [q for q in ordered if q.kind == AUDIT]
    classifying = sum(q.settles_n for q in audits)
    claims_settled = sum(q.settles_n for q in on_sheet_audits)
    settled_by_pattern: dict[str, int] = {}
    for question in ordered:
        if question.kind == PATTERN:
            pattern = question.key
        elif question.kind == ATYPICAL:
            pattern = str(question.extra.get("pattern"))
        else:
            continue
        settled_by_pattern[pattern] = max(settled_by_pattern.get(pattern, 0), question.settles_n)
    pairs_settled = min(sum(settled_by_pattern.values()), len(pairs))
    return {
        "questions": [question.as_row(number) for number, question in enumerate(ordered, 1)],
        "summary": {
            "questions": len(ordered),
            "answered_waiting": sum(q.answer is not None for q in ordered),
            "packs": sum(q.kind == PACK for q in ordered),
            "tied": sum(q.kind == TIED for q in ordered),
            "audits": len(on_sheet_audits),
            "patterns": sum(q.kind == PATTERN for q in ordered),
            "atypical": sum(q.kind == ATYPICAL for q in ordered),
            "classifying_claims": classifying,
            "claims_settled": claims_settled,
            "claims_settled_share": round(claims_settled / classifying, 4) if classifying else 0.0,
            "audit_classes_total": len(audits),
            "classes_for_95pc": classes_needed,
            "pairs": len(pairs),
            "pairs_settled": pairs_settled,
            "pairs_settled_share": round(pairs_settled / len(pairs), 4) if pairs else 0.0,
            "patterns_total": len(patterns),
            "patterns_for_90pc": patterns_needed,
        },
    }
