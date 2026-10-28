"""The privacy shaper: what a ``/api/v1`` answer becomes at each of four levels.

A person chooses how much of a project leaves it:

==========  ====================================================================================
``counts``  numbers only (counts, shares, dates)
``words``   adds the Dictionary's meanings and word classes (the public vocabulary), theme labels
            made of those meanings only, the person's own topic and goal names, the engine's
            closed vocabularies, and local aliases (``E1``, ``E2``...) for experiments
``names``   adds experiment display names, area names and words outside the Dictionary (the
            engine's sentences about a folder)
``paths``   adds paths relative to each data folder, and file names
==========  ====================================================================================

These never go out, at any level: file contents, content-derived words outside the
Dictionary, absolute paths, mounts, user and host names, answer-log notes, deciders' names, entry
identifiers (an unsalted hash of a root and a path), and the stored tables themselves.

The shaper works on values first. It knows each route's fields (:data:`POLICY`, an
allow-list: a field it does not know is dropped, so a field added to a route later cannot leak
through it) and keeps, filters, aliases or drops each by the level. Then the finished text is
read once more by the shareable report's backstop (:class:`ingence.cli.shareable.Placeholders`),
which replaces any spelling of the project's path, of a root, of a run or of a pattern a person
wrote. The alias key (alias to identifier) is kept apart (:class:`Aliases`) and written beside an
export, never inside it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ingence.cli.shareable import Placeholders

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ingence.core.project import Project

__all__ = [
    "LEVELS",
    "NEVER",
    "POLICY",
    "Aliases",
    "Level",
    "Shaper",
    "Vocabulary",
    "compact_json",
    "level_named",
]

#: The four levels, least first.
LEVELS: tuple[str, ...] = ("counts", "words", "names", "paths")
type Level = Literal["counts", "words", "names", "paths"]
_RANK: dict[str, int] = {name: rank for rank, name in enumerate(LEVELS)}


def level_named(text: str) -> Level:
    """The level a person named, refused in words otherwise."""
    name = text.strip().lower()
    if name not in _RANK:
        message = f"no level {text!r}; the levels are {', '.join(LEVELS)}"
        raise ValueError(message)
    return name  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# The rules a field can be kept by.
# ---------------------------------------------------------------------------

#: A rule's name and the least level at which the field is kept at all.
_LEAST: dict[str, str] = {
    "number": "counts",  # an integer or a float (never a boolean)
    "date": "counts",  # YYYY-MM-DD, a year, or a zoned time
    "years": "counts",  # a map of year to count
    "closed": "words",  # a value of a closed vocabulary, or a flag
    "engine": "words",  # the engine's own words, with no name of the data in them
    "own": "words",  # the person's own words: a goal, a topic's name, a question asked
    "vocab": "words",  # a label of data words: at words, only the public words of it
    "words": "words",  # a list of words, filtered the same way
    "name": "names",  # a display name of the data: a folder's own name, a person's
    "sentence": "names",  # the engine's sentence about a folder, which may quote its name
    "path": "paths",  # a path relative to its root, or a file name
}

#: Fields that never go out: a decider's name, an answer-log note, an entry identifier.
NEVER = "never"
#: Error codes whose message is an exception's own words, which can quote a path.
_INTERNAL_CODES = frozenset({"server_error", "internal_error"})

#: What the shaper reads as a date at ``counts``: a day, a year, or a zoned time.
_DATE = re.compile(
    r"^\d{4}(-\d{2}(-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?)?)?$"
)
#: A word of a label, for the vocabulary filter.
_WORD = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*")

#: The policy, route by route, as ``field.path number`` lines. ``[]`` is an item of a list.
#: ``alias:X`` gives the identifier a local alias of family X (kept from ``words`` up). A field
#: not written here is dropped at every level.
_POLICY_TEXT: dict[str, str] = {
    "overview": """
        counts.experiments number
        counts.files number
        counts.areas number
        date_range.first date
        date_range.last date
        date_range.basis engine
        areas[].id alias:A
        areas[].name name
        areas[].kind closed
        areas[].kind_assumed closed
        areas[].experiments number
        areas[].files number
        file_types[].type_id closed
        file_types[].label engine
        file_types[].category closed
        file_types[].files number
        file_types[].share number
        completeness.raw_files number
        completeness.raw_with_analysis number
        completeness.experiments_complete number
        completeness.experiments_partial number
        completeness.experiments_raw_only number
        completeness.experiments_analysis_only number
        completeness.not_read number
        repeats.seen_once number
        repeats.repeated number
        repeats.groups number
        themes_top[].id alias:T
        themes_top[].label vocab
        themes_top[].experiments number
        goals[].id alias:G
        goals[].text own
        goals[].matches number
        assumptions_count number
        checks_count number
        coverage.read number
        coverage.represented number
        coverage.not_reached number
    """,
    "areas": """
        areas[].id alias:A
        areas[].name name
        areas[].kind closed
        areas[].kind_assumed closed
        areas[].files number
    """,
    "experiments": """
        counted_as engine
        pairing_assumption engine
        summary.experiments number
        summary.files number
        summary.files_outside number
        summary.raw_acquisitions number
        summary.followed number
        summary.no_analysis_found number
        summary.judged number
        matching number
        offset number
        limit number
        rows[].id alias:E
        rows[].folder path
        rows[].name name
        rows[].shown_name name
        rows[].person name
        rows[].area name
        rows[].area_kind closed
        rows[].repeats.n number
        rows[].repeats.group alias:R
        rows[].workflow.id alias:W
        rows[].workflow.reached closed
        rows[].workflow.expected[] closed
        rows[].date date
        rows[].last_file_time date
        rows[].found_by closed
        rows[].found_by_kind closed
        rows[].found_by_said sentence
        rows[].files number
        rows[].by_stage[].key closed
        rows[].by_stage[].label engine
        rows[].by_stage[].files number
        rows[].by_kind[].key closed
        rows[].by_kind[].label engine
        rows[].by_kind[].files number
        rows[].raw_acquisitions number
        rows[].analysis_files number
        rows[].result_tables number
        rows[].derived_files number
        rows[].has_analysis closed
        rows[].state closed
        rows[].state_said sentence
        rows[].state_why sentence
        rows[].no_analysis_found closed
        rows[].followed.raw_found number
        rows[].followed.followed number
        rows[].followed.by_confirmed number
        rows[].followed.assumed number
        rows[].followed.share number
        rows[].followed.said sentence
        rows[].followed.n number
        rows[].followed.of number
        rows[].characterized.files number
        rows[].characterized.stage_read number
        rows[].characterized.stage_conflicting number
        rows[].characterized.role_read number
        rows[].characterized.role_conflicting number
        rows[].theme vocab
        rows[].theme_label vocab
        rows[].topics[].id alias:K
        rows[].topics[].name own
        people[].value name
        people[].label name
        people[].experiments number
        lines[].value vocab
        lines[].label vocab
        lines[].experiments number
        states[].value closed
        states[].label engine
        states[].experiments number
        themes_computed closed
        themes_said engine
        topics[].id alias:K
        topics[].name own
        topics[].experiments number
        topics[].read closed
        topics[].complete closed
        topics[].said sentence
        topics_read closed
        topics_said engine
        topic_filter_said sentence
    """,
    "experiments/story": """
        experiment_id alias:E
        days[].date date
        days[].stage closed
        days[].files number
        days[].types[] closed
        workflow.id alias:W
        workflow.reached closed
        workflow.expected[] closed
        repeats[] alias:E
        made_from[].experiment_id alias:E
    """,
    "experiments/files": """
        experiment.id alias:E
        experiment.folder path
        experiment.name name
        experiment.kind closed
        experiment.counted_as engine
        files number
        opened_files number
        offset number
        limit number
        rows[].name path
        rows[].path path
        rows[].size_bytes number
        rows[].extension closed
        rows[].scan_outcome closed
        rows[].format[].value vocab
        rows[].format[].conflicting closed
        rows[].stage[].value vocab
        rows[].stage[].conflicting closed
        rows[].role[].value vocab
        rows[].role[].conflicting closed
        notes[] sentence
    """,
    "themes": """
        summary.experiments number
        summary.experiments_counted_as engine
        summary.placed number
        summary.unplaced number
        summary.undated number
        summary.themes number
        summary.broad_themes number
        summary.vocabulary number
        summary.distinct_documents number
        themes[].theme number
        themes[].label vocab
        themes[].words[] words
        themes[].senses[] words
        themes[].broad_theme number
        themes[].broad vocab
        themes[].experiments number
        themes[].stability number
        themes[].core_experiments number
        themes[].first number
        themes[].peak number
        themes[].last number
        themes[].going closed
        themes[].direction closed
        themes[].undated number
        themes[].by_year years
        themes[].examples[] name
        themes[].person_label own
        broad[].broad_theme number
        broad[].label vocab
        broad[].words[] words
        broad[].themes[] number
        broad[].experiments number
        experiments[].id alias:E
        experiments[].folder path
        experiments[].name name
        experiments[].shown_name name
        experiments[].date date
        experiments[].last_file_time date
        experiments[].year number
        experiments[].files number
        experiments[].bytes number
        experiments[].theme number
        experiments[].theme_label vocab
        experiments[].broad_theme number
        experiments[].broad_label vocab
        experiments[].unplaced_reason engine
        experiments[].person name
        experiments[].area name
        experiments[].area_kind closed
        experiments[].words[] words
        experiments_not_shown number
        unplaced_note engine
    """,
    "goals": """
        goals[].id alias:G
        goals[].text own
        goals[].read.kept[] own
        goals[].read.dropped[] own
        goals[].read.unknown[] own
        goals[].expanded[].word own
        goals[].expanded[].to[] words
        goals[].expanded[].how engine
        goals[].matches[].experiment_id alias:E
        goals[].matches[].label name
        goals[].matches[].tier closed
        goals[].matches[].why sentence
        goals[].summary.experiments number
        goals[].summary.named number
        goals[].summary.associated number
        goals[].summary.same_theme number
        goals[].summary.first date
        goals[].summary.last date
        goals[].summary.raw_with_analysis number
        goals[].summary.of number
        goals[].summary.repeated_groups number
        goals[].summary.seen_once number
    """,
    "assumptions": """
        total number
        assumptions[].id alias:S
        assumptions[].kind closed
        assumptions[].text sentence
        assumptions[].count number
        assumptions[].examples[].label path
        assumptions[].tier closed
    """,
    "checks": """
        total number
        checks[].id alias:Q
        checks[].kind closed
        checks[].title sentence
        checks[].text sentence
        checks[].default.value closed
        checks[].default.text sentence
        checks[].settles_n number
        checks[].settles_unit closed
        checks[].options[].value closed
        checks[].options[].text sentence
    """,
    "manifest": """
        watermark_from number
        watermark_to number
        said[] sentence
        decisions[].sequence number
        decisions[].occurred_at date
        decisions[].kind closed
        decisions[].answer name
        decisions[].itself_changed closed
        decisions[].files_changed number
        decisions[].files_beneath number
        decisions[].said sentence
        withdrawals[].sequence number
        withdrawals[].occurred_at date
        withdrawals[].answers number
        withdrawals[].notes number
        withdrawals[].said sentence
        rules[].rule_id closed
        rules[].rule engine
        rules[].version closed
        rules[].was_version closed
        rules[].change closed
        rules[].said engine
        claims[].rule_id closed
        claims[].rule engine
        claims[].version closed
        claims[].way closed
        claims[].was vocab
        claims[].now vocab
        claims[].count number
        claims[].out_of number
        claims[].said sentence
        claims_not_said number
        flags[].rule_id closed
        flags[].rule engine
        flags[].version closed
        flags[].way closed
        flags[].was vocab
        flags[].now vocab
        flags[].count number
        flags[].out_of number
        flags[].said sentence
        pairs[].rule_id closed
        pairs[].rule engine
        pairs[].gained number
        pairs[].lost number
        pairs[].out_of number
        pairs[].said sentence
        themes.computed closed
        themes.why_not engine
        themes.joined[].theme vocab
        themes.joined[].experiments number
        themes.joined[].out_of number
        themes.said[] sentence
        topics.computed closed
        topics.why_not engine
        topics.topics[].topic_id alias:K
        topics.topics[].name own
        topics.topics[].experiments number
        topics.topics[].complete closed
        topics.topics[].compared closed
        topics.topics[].joined[].experiment alias:E
        topics.topics[].joined[].folder path
        topics.topics[].joined[].reasons[] sentence
        topics.topics[].left[].experiment alias:E
        topics.topics[].left[].folder path
        topics.topics[].left[].reasons[] sentence
        topics.topics[].unread[] sentence
        topics.topics[].not_computed[] sentence
        topics.topics[].said[] sentence
        topics.said[] sentence
        topic_edits[].sequence number
        topic_edits[].occurred_at date
        topic_edits[].topic_id alias:K
        topic_edits[].name own
        topic_edits[].what closed
        topic_edits[].members number
        topic_edits[].said sentence
        quality.available closed
        quality.why_not engine
        quality.rows[].section closed
        quality.rows[].figure closed
        quality.rows[].facet closed
        quality.rows[].label engine
        quality.rows[].unit closed
        quality.rows[].state closed
        quality.rows[].count number
        quality.rows[].total number
        quality.rows[].was_count number
        quality.rows[].was_total number
        quality.rows[].change number
        quality.rows[].changed closed
        quality.said[] engine
    """,
    "topics": """
        run_experiments number
        topics[].id alias:K
        topics[].name own
        topics[].made_at date
        topics[].edited_at date
        topics[].earlier number
        topics[].members[].kind closed
        topics[].members[].name name
        topics[].members[].path path
        topics[].members[].terms[] words
        topics[].members[].state closed
        topics[].members[].in_run closed
        topics[].members[].experiments number
        topics[].members[].why engine
        topics[].members[].line.label vocab
        topics[].members[].line.words[] words
        topics[].members[].line.words_added_with[] own
        topics[].members[].line.drifted closed
        topics[].members[].line.kept number
        topics[].members[].line.added_by number
        topics[].experiments_count number
        topics[].experiments_out_of number
        topics[].experiments[].id alias:E
        topics[].experiments[].folder path
        topics[].experiments[].files number
        topics[].experiments[].in_run closed
        topics[].experiments[].through[] never
        topics[].experiments_not_shown number
        topics[].files_count number
        topics[].complete closed
        topics[].nothing[] sentence
        topics[].not_computed[] sentence
        topics[].overlaps[].topic alias:K
        topics[].overlaps[].name own
        topics[].overlaps[].experiments number
        topics[].shared_experiments number
        topics[].drift.joined[].experiment alias:E
        topics[].drift.joined[].folder path
        topics[].drift.joined[].reasons[] sentence
        topics[].drift.left[].experiment alias:E
        topics[].drift.left[].folder path
        topics[].drift.left[].reasons[] sentence
        topics[].drift.unread[] sentence
        topics[].drift.said[] sentence
        topics[].said sentence
        suggestions[].broad number
        suggestions[].label vocab
        suggestions[].words[] words
        suggestions[].experiments number
        suggestions[].in_topics number
        suggestions[].said sentence
        suggestions_why engine
    """,
    "ask": """
        q own
        read.kept[] own
        read.dropped[] own
        read.unknown[] own
        expanded[].word own
        expanded[].to[] words
        expanded[].how engine
        filters.years[] number
        filters.types[] closed
        filters.areas[] name
        experiments[].experiment_id alias:E
        experiments[].label name
        experiments[].score_tier closed
        experiments[].matched[].word words
        experiments[].matched[].how engine
        experiments[].dates.first date
        experiments[].dates.last date
        experiments[].completeness number
        same_theme[].experiment_id alias:E
        same_theme[].via_theme vocab
    """,
    "completeness": """
        experiments[].id alias:E
        experiments[].folder path
        experiments[].name name
        experiments[].shown_name name
        experiments[].person name
        experiments[].files number
        experiments[].raw_acquisitions number
        experiments[].derived_files number
        experiments[].raw_followed number
        experiments[].share_of_raw_followed_by_analysis number
        experiments[].analysis_files number
        experiments[].result_tables number
        experiments[].has_analysis closed
        experiments[].theme vocab
        experiments[].theme_label vocab
        experiments[].date date
        experiments[].said sentence
        people[].key name
        people[].experiments number
        people[].experiments_with_raw number
        people[].raw_acquisitions number
        people[].raw_followed number
        people[].share_of_raw_followed_by_analysis number
        people[].analysis_files number
        people[].result_tables number
        people[].no_analysis_found number
        people[].analysed_elsewhere number
        themes[].key vocab
        themes[].experiments number
        themes[].experiments_with_raw number
        themes[].raw_acquisitions number
        themes[].raw_followed number
        themes[].share_of_raw_followed_by_analysis number
        themes[].analysis_files number
        themes[].result_tables number
        themes[].no_analysis_found number
        themes[].analysed_elsewhere number
        themes_computed closed
        themes_said engine
        outside.files number
        outside.raw_acquisitions number
        outside.raw_followed number
        outside.share_of_raw_followed_by_analysis number
        outside.said sentence
        settings.judged_from_acquisitions number
        settings.listed_below_share number
        settings.own_analysis_files number
        settings.own_result_tables number
        settings.forgotten_at_least number
        settings.stopped_below number
    """,
}


def _parse(text: str) -> dict[str, str]:
    rules: dict[str, str] = {}
    for line in text.strip().splitlines():
        path, rule = line.split()
        if rule != NEVER and not rule.startswith("alias:") and rule not in _LEAST:
            message = f"no rule {rule!r} for {path}"
            raise ValueError(message)
        rules[path] = rule
    return rules


#: Every route's field policy, by route, by dotted path.
POLICY: dict[str, dict[str, str]] = {route: _parse(text) for route, text in _POLICY_TEXT.items()}

#: The paths that lead to a kept field, so a walk knows which containers to enter.
_CONTAINERS: dict[str, frozenset[str]] = {}
for _route, _rules in POLICY.items():
    _leading: set[str] = set()
    for _path in _rules:
        _parts = _path.split(".")
        # A list is entered by its own name, and each of its items by the name with ``[]``.
        _prefixes = [".".join(_parts[:_stop]) for _stop in range(1, len(_parts) + 1)]
        for _prefix in _prefixes:
            if _prefix.endswith("[]"):
                _leading.add(_prefix[:-2])
            if _prefix != _path:
                _leading.add(_prefix)
    _CONTAINERS[_route] = frozenset(_leading)

#: The engine's closed vocabularies: the words of the ``/api/v1`` enums and the class words the
#: routes use, which no laboratory's data wrote.
CLOSED_WORDS: frozenset[str] = frozenset(
    {
        "person",
        "instrument",
        "date",
        "project",
        "folder",  # area kinds
        "file_type",
        "writer",
        "domain",
        "spelling",
        "area_kind",
        "workflow",
        "stage",
        "repeat",
        "named",
        "associated",
        "same_theme",  # match tiers
        "listing",
        "reading",
        "names",
        "rules",
        "views",  # job phases
        "general",
        "program",
        "container",
        "archive",
        "document",  # file type categories
        "both",
        "raw_only",
        "analysis_only",
        "neither",  # completeness states
        "raw",
        "analysis",
        "processed",
        "acquisition",
        "acquisitions",
        "other",
        "conflicting",
        "none",
        "unknown",
        "unexamined",
        "listed",
        "answered",
        "structure",
        "dated_name",
        "rising",
        "falling",
        "steady",
        "new",
        "stopped",
        "undated",
        "image",
        "images",
        "table",
        "tables",
        "script",
        "scripts",
        "text",
        "data",
        "file",
        "files",
        "experiment",
        "experiments",
        "theme",
        "themes",
        "complete",
        "partial",
        # The engine's own words, which a rule's name or a figure's label says.
        "contents",
        "content",
        "read",
        "found",
        "counted",
        # Tier words, answers, units, ways, outcomes, states: the values a closed field takes.
        "defeasible",
        "abductive",
        "inductive",
        "deductive",
        "heuristic",
        "observed",
        "confirmed",
        "assumed",
        "declared",
        "resolved",
        "examined",
        "nothing",
        "not_computed",
        "yes",
        "no",
        "cannot_say",
        "folders",
        "pairs",
        "items",
        "gained",
        "lost",
        "changed",
        "made",
        "brought back",
        "withdrew",
        "extracted",
        "extracted_with_fallback",
        "metadata_only",
        "deferred",
        "content_error",
        "skipped_by_policy",
        "skipped_by_settings",
        "changed_during_read",
        "conclusions",
        "cost",
        "not_run",
        "not_recorded",
        "true",
        "false",
        "entry",
        "core",
        "line",
        "topic",
        "goal",
        "question",
        "area",
    }
)


# ---------------------------------------------------------------------------
# The public vocabulary.
# ---------------------------------------------------------------------------


class Vocabulary:
    """The words that may leave a project at the ``words`` level: the Dictionary's meanings.

    Built from the shipped Dictionary and the packs a project accepted, never from what a person
    taught the project (those answers can spell a laboratory's own names) and never from words
    the engine discovered in the data.
    """

    def __init__(self, words: Iterable[str] = ()) -> None:
        # A spelling of several words (``plate reader``) is known whole and word by word.
        known: set[str] = set()
        for spelling in (*words, *CLOSED_WORDS):
            if spelling:
                known.add(spelling.casefold())
                known.update(word.casefold() for word in _WORD.findall(spelling))
        self._words: frozenset[str] = frozenset(known)

    @classmethod
    def for_project(cls, project: Path) -> Vocabulary:
        """The vocabulary of a project: what ships, and the packs it accepted."""
        from ingence.core.schema import DictionaryOrigin
        from ingence.dictionary.store import DictionaryStore

        store = DictionaryStore.for_project(project)
        return cls(_public_words(store.senses(), DictionaryOrigin))

    @classmethod
    def shipped(cls) -> Vocabulary:
        """The shipped vocabulary alone, for a check that needs no project."""
        from ingence.core.schema import DictionaryOrigin
        from ingence.dictionary.store import DictionaryStore

        return cls(_public_words(DictionaryStore.builtin().senses(), DictionaryOrigin))

    def __len__(self) -> int:
        return len(self._words)

    def knows(self, word: str, own: frozenset[str] = frozenset()) -> bool:
        """Whether a word may be said: a meaning, a number, or one of the person's own."""
        folded = word.casefold()
        return folded in self._words or folded in own or word.isdigit()

    def is_closed(self, text: str) -> bool:
        """Whether a value of a closed field is made of known words only (``raw_only``,
        ``file_type``, ``2024``), so a word of the data in such a field is caught."""
        if text.casefold() in self._words:
            return True
        words = _WORD.findall(text)
        return bool(words) and all(self.knows(word) for word in words)

    def filter_label(self, text: str, own: frozenset[str] = frozenset()) -> str | None:
        """A label with only its public words kept, in order, or None where none is public."""
        kept = [word for word in _WORD.findall(text) if self.knows(word, own)]
        return " ".join(kept) if kept else None

    def filter_words(self, words: Iterable[str], own: frozenset[str] = frozenset()) -> list[str]:
        """The words of a list that are public, in order."""
        return [word for word in words if isinstance(word, str) and self.knows(word, own)]


def _public_words(senses: Iterable[Any], origins: Any) -> set[str]:
    public = {origins.BUILTIN, origins.PACK}
    words: set[str] = set()
    for sense in senses:
        if sense.origin not in public:
            continue
        for spelling in (sense.term, sense.canonical, *sense.surfaces):
            if not spelling:
                continue
            words.add(spelling)
            words.update(_WORD.findall(spelling))
        words.add(sense.theme_kind)
        words.add(sense.section)
    return words


# ---------------------------------------------------------------------------
# The aliases.
# ---------------------------------------------------------------------------

#: What each alias family names.
FAMILIES: dict[str, str] = {
    "E": "experiment",
    "A": "area",
    "T": "theme",
    "G": "goal",
    "K": "topic",
    "S": "assumption",
    "Q": "question",
    "W": "workflow",
    "R": "repeat group",
}
_ALIAS = re.compile(r"^([A-Z])(\d+)$")


class Aliases:
    """Local aliases for identifiers: ``E1``, ``E2``... given in the order they are first met.

    The key (alias to identifier) is written beside an export, never inside it, so a brief holds
    no entry identifier, and a person can still find the experiment an alias names.
    """

    def __init__(self) -> None:
        self._by_identifier: dict[str, dict[str, str]] = {family: {} for family in FAMILIES}
        self._by_alias: dict[str, str] = {}

    def alias(self, family: str, identifier: str) -> str:
        """The alias of an identifier in a family, made on first sight."""
        known = self._by_identifier[family]
        found = known.get(identifier)
        if found is None:
            found = f"{family}{len(known) + 1}"
            known[identifier] = found
            self._by_alias[found] = identifier
        return found

    def identifier(self, alias: str) -> str | None:
        """The identifier an alias names, or None for an alias never given."""
        return self._by_alias.get(alias.strip().upper())

    def is_alias(self, text: str) -> bool:
        """Whether a text is spelled like an alias (a family letter and a number)."""
        match = _ALIAS.match(text.strip().upper())
        return match is not None and match.group(1) in FAMILIES

    def __len__(self) -> int:
        return len(self._by_alias)

    def as_mapping(self) -> dict[str, dict[str, str]]:
        """The key: each family's aliases, alias to identifier, for the file beside an export."""
        return {
            FAMILIES[family]: {alias: identifier for identifier, alias in known.items()}
            for family, known in self._by_identifier.items()
            if known
        }

    def write(self, path: Path) -> None:
        """Write the key as JSON."""
        path.write_text(json.dumps(self.as_mapping(), indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# The shaper.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Walk:
    """What one shaping call carries down the tree."""

    route: str
    rank: int
    own: frozenset[str]


class Shaper:
    """Shape a route's answer to a level: values first, then the finished text."""

    def __init__(
        self,
        level: Level,
        *,
        vocabulary: Vocabulary,
        aliases: Aliases | None = None,
        placeholders: Placeholders | None = None,
    ) -> None:
        self.level: Level = level_named(level)
        self.rank = _RANK[self.level]
        self.vocabulary = vocabulary
        self.aliases = aliases if aliases is not None else Aliases()
        self._placeholders = placeholders

    @classmethod
    def for_project(
        cls, project: Project, level: Level, *, aliases: Aliases | None = None
    ) -> Shaper:
        """A shaper for a project: its vocabulary, and a backstop that knows its paths."""
        placeholders = Placeholders(project.path)
        placeholders.opened(project)
        return cls(
            level,
            vocabulary=Vocabulary.for_project(project.path),
            aliases=aliases,
            placeholders=placeholders,
        )

    def allows(self, rule: str) -> bool:
        """Whether a field kept by ``rule`` is kept at this level."""
        if rule == NEVER:
            return False
        least = "words" if rule.startswith("alias:") else _LEAST[rule]
        return self.rank >= _RANK[least]

    # -- values ----------------------------------------------------------------------------------

    def shape(
        self, route: str, payload: Mapping[str, Any], *, own_words: Iterable[str] = ()
    ) -> dict[str, Any]:
        """A route's answer with only what the level allows, aliases in place of identifiers.

        ``own_words`` are words the person (or the asker) wrote in this request, which may be
        said back at the ``words`` level. An error answer (``error.code`` and ``error.message``)
        passes at every level: the engine's own refusal words.
        """
        if "error" in payload and isinstance(payload["error"], Mapping):
            error = payload["error"]
            code = str(error.get("code", "error"))
            message = self.error_message(code, error.get("message"))
            return {"error": {"code": code, "message": message}}
        if route not in POLICY:
            message = f"the shaper knows no route {route!r}"
            raise ValueError(message)
        if route == "assumptions" and isinstance(payload.get("assumptions"), list):
            # An assumption's text may carry the rule's full statement after the Dashboard's Why
            # separator. Only its one sentence goes out in the brief.
            from ingence.readouts import WHY_SEPARATOR

            payload = {
                **payload,
                "assumptions": [
                    {**item, "text": str(item["text"]).split(WHY_SEPARATOR, 1)[0]}
                    if isinstance(item, Mapping) and isinstance(item.get("text"), str)
                    else item
                    for item in payload["assumptions"]
                ],
            }
        walk = _Walk(route=route, rank=self.rank, own=frozenset(w.casefold() for w in own_words))
        shaped = self._dict(payload, "", walk)
        return shaped if isinstance(shaped, dict) else {}

    def error_message(self, code: str, message: Any) -> str:
        """What an error's words become: below ``names``, a server error (the engine's own
        exception, whose words can quote a path) is said by its exception's type name and its
        code only. At every level the words are read by the backstop."""
        text = str(message or "")
        if code in _INTERNAL_CODES and self.rank < _RANK["names"]:
            text = text.split(":", 1)[0].strip() or code
        return self.finish(text)

    def _dict(self, value: Mapping[str, Any], at: str, walk: _Walk) -> dict[str, Any]:
        rules = POLICY[walk.route]
        containers = _CONTAINERS[walk.route]
        out: dict[str, Any] = {}
        for key, item in value.items():
            path = f"{at}.{key}" if at else key
            if path in rules:
                kept, shaped = self._leaf(rules[path], item, path, walk)
                if kept:
                    out[key] = shaped
            elif path in containers:
                shaped_container = self._container(item, path, walk)
                if shaped_container is not None:
                    out[key] = shaped_container
        return out

    def _container(self, value: Any, path: str, walk: _Walk) -> Any:
        if isinstance(value, Mapping):
            return self._dict(value, path, walk)
        if isinstance(value, list):
            rules = POLICY[walk.route]
            item_path = f"{path}[]"
            if item_path in rules:
                if not self.allows(rules[item_path]):
                    return None  # a list nothing of which goes out is left out whole
                items = []
                for item in value:
                    kept, shaped = self._leaf(rules[item_path], item, item_path, walk)
                    if kept:
                        items.append(shaped)
                return items
            shaped_items = [self._container(item, item_path, walk) for item in value]
            # An item nothing of which is kept at this level carries nothing: left out.
            return [item for item in shaped_items if item not in (None, {}, [])]
        return None

    def _leaf(self, rule: str, value: Any, path: str, walk: _Walk) -> tuple[bool, Any]:
        """Whether a leaf is kept, and what it becomes."""
        if not self.allows(rule):
            return False, None
        if value is None:
            return True, None
        if rule.startswith("alias:"):
            family = rule.split(":", 1)[1]
            return True, self.aliases.alias(family, str(value))
        if rule == "number":
            if isinstance(value, bool) or not isinstance(value, int | float):
                return False, None
            return True, value
        if rule == "date":
            text = str(value)
            return (True, text) if _DATE.match(text) else (False, None)
        if rule == "years":
            if not isinstance(value, Mapping):
                return False, None
            return True, {
                str(year): count
                for year, count in value.items()
                if _DATE.match(str(year)) and isinstance(count, int) and not isinstance(count, bool)
            }
        if rule == "closed":
            if isinstance(value, bool | int | float):
                return True, value
            text = str(value)
            if walk.rank >= _RANK["names"] or self.vocabulary.is_closed(text):
                return True, text
            return False, None  # a word of the data in a closed field: not below names
        if rule in ("engine", "own", "name", "sentence", "path"):
            return True, value if isinstance(value, bool | int | float) else str(value)
        if rule == "vocab":
            text = str(value)
            if walk.rank >= _RANK["names"]:
                return True, text
            return True, self.vocabulary.filter_label(text, walk.own)
        if rule == "words":
            if isinstance(value, str):
                if walk.rank >= _RANK["names"]:
                    return True, value
                return (True, value) if self.vocabulary.knows(value, walk.own) else (False, None)
            if isinstance(value, list):
                if walk.rank >= _RANK["names"]:
                    return True, [str(word) for word in value]
                return True, self.vocabulary.filter_words(value, walk.own)
            return False, None
        return False, None  # pragma: no cover - every rule is handled above

    # -- the finished text -----------------------------------------------------------------------

    def finish(self, text: str) -> str:
        """The finished text read once more: the project's path, roots, runs and patterns
        replaced wherever one survived (the backstop)."""
        if self._placeholders is None:
            return text
        return self._placeholders.backstop(text)

    @property
    def replaced(self) -> int:
        """How many names of the data the backstop had to replace (it should be none)."""
        return 0 if self._placeholders is None else self._placeholders.replaced


def compact_json(value: Any) -> str:
    """A value as the shortest JSON, keys as given."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
