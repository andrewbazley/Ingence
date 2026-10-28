"""``GET /api/v1/experiments/files``: one experiment's files, each with what the engine found.

A person judging an experiment needs every file in it with everything the engine found about that
file, each value with where it came from. So this route is a projection of one published run, read
after inference and writing nothing. Each file row holds these parts:

- Its clocks, each with its source, never merged: the filesystem's modification time, its birth
  time where the filesystem records one, a clock read from the content (with the source the dive
  recorded, and the extractors that read the file), and a date read from its name or its folder's.
  The row also holds the time the engine believes: the earliest credible time a rule concluded,
  with the rule, its version and the clock it chose.
- Its size, extension, format, stage and role, each a value a rule concluded (the claims below).
- Its claims, each with its rule and version, its evidence (the records it read and its support)
  and its defeaters, fired or not.
- The derivation pairs proposed or published for it, either way round, each with its rule and
  support. Each acquisition also lists the files derived from it.
- The copies it belongs to: each set a copy rule formed, by what evidence. A set is identical where
  a whole-file digest proved the bytes equal, and holds supposed copies otherwise.

An experiment is a folder the experiment rules concluded on, made one set by the clustering rule's
policy. Its files are the filesystem's own entries that the one definition gives it
(``ingence.experiment_units.folders_of_experiment``): the highest experiment folder above a file,
and none beneath an installed program. A document's internal parts are never files here. The list
of experiments (``/api/v1/experiments``), the completeness figures and the themes count the same
files for the same experiment. A folder inside another experiment is part of it, and asking for it
by itself is refused in words.

The rows are paged, in path order, because an experiment can hold thousands of files. Every value
is what the engine found. An empty list is what the rules concluded in what was read. It is never
a statement that the file has none, and the response says so in its ``notes``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, JsonValue

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import integer, json_value, one, pinned_catalog, time_from_ns

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = [
    "COPY_PREDICATES",
    "LIMIT",
    "OTHERS_LISTED",
    "BusyDaysItem",
    "ClaimReading",
    "ExperimentFilesResponse",
    "claim_reading",
    "experiment_files",
]

#: The most files one page lists, and how many a page lists unless it asks. A file's row carries
#: its claims and pairs, so a page of 500 is already a large answer.
LIMIT = 500
DEFAULT_LIMIT = 100

#: How many of a copy set's other members a row names; the set's size is always counted whole.
#: A set of copies can hold every thumbnail of a pool, and a row naming them all is no row.
OTHERS_LISTED = 20

#: The predicates of the rules forming copies of one file, read by predicate so a project's own
#: rule on one is read alike: a content digest (whole or sampled), the census's metadata, a digest
#: an archive declared, a person's answer, an archive's link. A rule on any other predicate that
#: proves byte equality (the ``identical`` primitive) is a copy rule as well.
COPY_PREDICATES: tuple[str, ...] = (
    "same_content_digest",
    "same_observed_metadata",
    "same_declared_digest",
    "answered_copies",
    "archive_link_names_a_file",
)

#: What each theme kind's claims are read as: a classification whose value is a sense of that kind
#: in the run's Dictionary (``api/v1/explorer/projection.py``'s rule), or, in a run whose
#: Dictionary holds none of the kind, one whose predicate is spelled so.
_KINDS: dict[str, tuple[str, str]] = {
    "format": ("format", "classifies.format%"),
    "stage": ("stage", "classifies.%stage%"),
    "role": ("file_role", "classifies.%role%"),
}
#: What the last run did with a file whose content it read (``ScanOutcome``): the files a dive
#: opened and read, counted beside the experiment's files.
READ_OUTCOMES = frozenset({"extracted", "extracted_with_fallback"})
EARLIEST_PREDICATE = "classifies.earliest_credible_time"
NAME_DATE_PREDICATE = "classifies.date_neighbourhood_of_a_file"
#: The observers that read an entry without reading its content.
_NOT_CONTENT = ("census", "census.organisation", "deepen")

NOTES = (
    "Every list says what the run's rules concluded in what was read: an empty one is not found "
    "there, never that the file has none.",
    "Each clock is kept with its source and none is merged with another; the believed time is "
    "the earliest credible time a rule concluded, and says which clock it chose.",
    "A date read from a name is context, never when the file was made.",
)


# ---------------------------------------------------------------------------
# The response models.
# ---------------------------------------------------------------------------


class FileClock(ApiModel):
    """One time the run holds for a file, from one source, as precisely as it is known."""

    #: ``modified``, ``created`` (the filesystem's birth time), ``event`` (a clock read from the
    #: content) or ``name`` (a day or a span of days read from its name or its folder's).
    source: Literal["modified", "created", "event", "name"]
    #: The engine's own word for where it came from (``mtime``, ``birthtime``, ``instrument``,
    #: ``folder``...).
    detail: str
    #: ISO 8601 with its zone for an instant; a day (``2026-01-02``) for a name's date.
    at: str
    until: str | None
    precision: Literal["instant", "day", "range"]
    #: For a content clock: the extractors that read the file's content. The census records
    #: which source the clock came from, not which of them gave it.
    read_by: list[str]


class BelievedTime(ApiModel):
    """The time the engine believes: the earliest credible time a rule concluded, and why."""

    at: str
    #: The clock the rule chose (``birth``, ``modified``, ``content``...), in the rule's words.
    source: str
    rule_id: str
    rule_version: str
    #: Published as conflicting (a defeater fired): shown, never passed as settled.
    conflicting: bool
    why: str


class FileDefeater(ApiModel):
    rule_id: str
    rule_version: str
    reason: str
    #: True where the defeating evidence is present; false records what would defeat it.
    fired: bool
    records: list[str]


class FileSupport(ApiModel):
    name: str
    family: str
    value: JsonValue
    records: list[str]


class FileClaim(ApiModel):
    claim_id: Identifier
    predicate: str
    #: The value as the rule wrote it: a sense, a key, or structured JSON as text.
    value: str | None
    rule_id: str
    rule_version: str
    tier: str
    status: str
    #: The records the rule read.
    evidence: list[str]
    support: list[FileSupport]
    defeaters: list[FileDefeater]
    #: For a classifying claim with a value, its class handle: the Record target an answer on
    #: every claim this rule made with this value is written on (``GET /api/v1/claims/class``).
    class_handle: str | None = None
    #: The answer in force on that class, where a person gave one.
    class_answer: ClassAnswer | None = None


class DerivationPair(ApiModel):
    """One pair a rule proposed or a run published, seen from this file."""

    candidate_id: Identifier | None
    edge_id: Identifier | None
    rule_id: str
    rule_version: str
    #: ``made_from``: this file was made from the other; ``made_into``: the other from this one.
    direction: Literal["made_from", "made_into"]
    other_id: Identifier
    other_name: str | None
    other_path: str | None
    tier: str
    #: Whether the run publishes it as a derivation (an asserted edge), or only proposes it.
    published: bool
    support: list[FileSupport]


class CopyMember(ApiModel):
    id: Identifier
    name: str | None
    path: str | None


class CopyGroup(ApiModel):
    """One set of copies a rule formed that holds this file."""

    rule_id: str
    rule_version: str
    #: ``identical`` where a whole-file digest proved the bytes equal (an entailed claim).
    #: ``supposed copies`` where a rule could only suppose it.
    kind: Literal["identical", "supposed copies"]
    #: What the rule compared, in plain words, and the key it formed the set by.
    evidence: str
    key: str
    status: str
    members: Count
    others: list[CopyMember]


class ReadValue(ApiModel):
    """One value a rule read for a file, and whether it is published as conflicting."""

    value: str
    #: True where a defeater fired on the claim: shown, never passed as settled.
    conflicting: bool


class ClassAnswer(ApiModel):
    """The decision in force on the class a claim belongs to (its handle), as it reaches a file."""

    word: str
    #: The file was not among those the class held when the answer was given: it joined later,
    #: and the answer reaches it as it does the rest.
    joined_after: bool


class ExperimentFile(ApiModel):
    id: Identifier
    name: str
    path: str
    size_bytes: Count | None
    extension: str
    #: What the last run did with the file: ``extracted`` and ``extracted_with_fallback`` mean a
    #: dive opened it and read its content. ``metadata_only`` means that nothing inside it was
    #: opened. The others (``deferred``, ``content_error``, ``skipped_by_policy``...) are the
    #: engine's own words for a file it did not read (``core/outcomes.py``).
    scan_outcome: str = "metadata_only"
    #: The values a rule concluded, each a claim below, each marked where it is conflicting.
    format: list[ReadValue]
    stage: list[ReadValue]
    role: list[ReadValue]
    clocks: list[FileClock]
    believed: BelievedTime | None
    #: The believed time, or what the rules concluded instead, in words.
    believed_said: str
    claims: list[FileClaim]
    derivations: list[DerivationPair]
    copies: list[CopyGroup]


class DerivedFile(ApiModel):
    id: Identifier
    name: str | None
    path: str | None
    rule_id: str
    published: bool


class AcquisitionItem(ApiModel):
    """One acquisition on the page, and the files the run proposes or publishes as made from it."""

    id: Identifier
    name: str
    path: str
    derived: list[DerivedFile]


class BusyDaysItem(ApiModel):
    """The run of days that holds most of the experiment's dated files (``busy_days``)."""

    first_day: str = Field(description="The first day of the run, YYYY-MM-DD (UTC).")
    last_day: str = Field(
        description="The last day of the run, YYYY-MM-DD (UTC); may equal the first."
    )
    said: str = Field(description="The run in words: 2 Mar 2026, 2 to 4 Mar 2026.")
    files: Count = Field(description="Dated files written on those days.")
    dated_files: Count = Field(
        description="All the experiment's files that have a time (believed, else modified)."
    )
    share: float = Field(description="files over dated_files, from 0 to 1.")
    percent: Count = Field(description="The share as a whole percent, rounded half up.")


class ExperimentHeader(ApiModel):
    id: Identifier
    #: Its folder, relative to its root.
    folder: str
    name: str
    #: What the structure rule read it as (``experiment``, ``acquisitions``, ``analysis``), or
    #: ``named`` where the dated-name rule alone concluded it.
    kind: str
    #: What an experiment is in this run, in sentences a page can show beside a count. Every route
    #: that counts experiments says the same.
    counted_as: str = ""


@register("experiment_files")
class ExperimentFilesResponse(ApiModel):
    """``GET /api/v1/experiments/files``: one experiment's files, a page at a time."""

    schema_version: str
    run_id: Identifier
    experiment: ExperimentHeader
    #: Every file in the experiment, however many a page lists.
    files: Count
    #: Of those files, how many a dive opened and read (whatever page is listed).
    opened_files: Count = 0
    offset: Count
    limit: Count
    rows: list[ExperimentFile]
    acquisitions: list[AcquisitionItem]
    notes: list[str]
    #: The busiest days of all the experiment's files, not of this page's (the same on every
    #: page): the run of days around the day with the most files, extended while a neighbor holds
    #: at least 5 percent of the dated files, kept if it holds at least half. Null where the files
    #: are too few (under 20 dated), lie within 30 days of each other, or have no such run: the
    #: whole span is then the window. A file's day is the UTC day of its believed time, else its
    #: modification time; a file with neither is in neither number.
    busy_days: BusyDaysItem | None = None


# ---------------------------------------------------------------------------
# Reading.
# ---------------------------------------------------------------------------


def _rows(catalog: Catalog, sql: str, parameters: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = catalog.query(sql, list(parameters))
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _loaded(text: Any) -> Any:
    if not isinstance(text, str) or not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _records(value: Any) -> list[str]:
    return [str(item) for item in value or ()]


def _support(text: Any) -> list[FileSupport]:
    held = _loaded(text)
    if not isinstance(held, list):
        return []
    return [
        FileSupport(
            name=str(item.get("name") or ""),
            family=str(item.get("family") or ""),
            value=json_value(item.get("value")),
            records=_records(item.get("observations")),
        )
        for item in held
        if isinstance(item, dict)
    ]


def _defeaters(text: Any) -> list[FileDefeater]:
    held = _loaded(text)
    if not isinstance(held, list):
        return []
    return [
        FileDefeater(
            rule_id=str(item.get("rule_id") or ""),
            rule_version=str(item.get("rule_version") or ""),
            reason=str(item.get("reason") or ""),
            fired=bool(item.get("fired")),
            records=_records(item.get("observations")),
        )
        for item in held
        if isinstance(item, dict)
    ]


def _experiment(catalog: Catalog, experiment_id: str) -> tuple[ExperimentHeader, list[str]]:
    """The experiment named, and the folders that hold its files; refused where it is none.

    The one definition places the files (``experiment_units.run_units``). The folders are the
    experiment's own and every folder the definition gives it. So an experiment folder that lies
    inside another is refused here (its files are the outer one's), and so is one inside a program.
    """
    from ingence.experiment_units import (
        counted_as,
        folders_of_experiment,
        policy_experiments,
        run_units,
    )

    held = policy_experiments(catalog).get(experiment_id)
    run = run_units(catalog)
    if held is not None:
        path = held["path"]
        outer = run.units.get(experiment_id)
        if outer != experiment_id:
            where = (
                f"it lies inside the experiment {run.paths[outer]!r}, whose files these are"
                if outer is not None
                else "it lies inside an installed program"
            )
            message = f"the folder {path!r} is not an experiment of this run: {where}"
            raise ApiError(
                message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="experiment"
            )
        header = ExperimentHeader(
            id=experiment_id,
            folder=path,
            name=held["name"],
            kind=held["kind"],
            counted_as=counted_as(catalog),
        )
        return header, folders_of_experiment(run, experiment_id)
    message = (
        f"no folder this run's rules read as an experiment has the identifier {experiment_id!r}"
    )
    raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="experiment")


def _members(catalog: Catalog, folders: Sequence[str]) -> list[dict[str, Any]]:
    """Every file of the experiment, in path order: the filesystem's own entries in the folders
    the one definition gives it. A document's parts are not files."""
    return _rows(
        catalog,
        """
        SELECT e.entry_id, e.name, e.relative_path, e.size_bytes, e.extension,
               e.modified_ns, e.created_ns, e.created_time_source, e.event_time_ns,
               e.event_time_source, e.scan_outcome
        FROM entries e
        WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'
          AND e.parent_entry_id IN (SELECT unnest(?::VARCHAR[]))
        ORDER BY e.relative_path, e.entry_id
        """,
        [list(folders)],
    )


def _kinds_of(catalog: Catalog, values: Iterable[str]) -> dict[str, set[str]]:
    """Which of format, stage and role each concluded value is, by the run's Dictionary."""
    wanted = sorted(set(values))
    senses = _rows(
        catalog,
        "SELECT DISTINCT term, theme_kind FROM dictionary_entries "
        "WHERE term IN (SELECT unnest(?::VARCHAR[]))",
        [wanted],
    )
    kinds: dict[str, set[str]] = {}
    by_theme = {theme: name for name, (theme, _) in _KINDS.items()}
    for row in senses:
        name = by_theme.get(str(row["theme_kind"]))
        if name is not None:
            kinds.setdefault(str(row["term"]), set()).add(name)
    return kinds


def _dictionary_holds(catalog: Catalog) -> set[str]:
    """The theme kinds of ``_KINDS`` the run's Dictionary holds a sense of."""
    themes = [theme for theme, _ in _KINDS.values()]
    return {
        str(row[0])
        for row in catalog.query(
            "SELECT DISTINCT theme_kind FROM dictionary_entries "
            "WHERE theme_kind IN (SELECT unnest(?::VARCHAR[]))",
            [themes],
        ).fetchall()
    }


@dataclass(frozen=True, slots=True)
class ClaimReading:
    """How the files page reads a classifying claim as a format, a stage or a role.

    A claim is a reading of a kind where its value is a sense of that kind in the run's Dictionary
    (``terms``). For a kind that the run's Dictionary holds no sense of at all (``by_spelling``), a
    claim is a reading where its predicate is spelled as ``patterns`` says. Kinds are ``format``,
    ``stage`` and ``role``, as ``_KINDS`` names them.
    """

    terms: dict[str, frozenset[str]]
    by_spelling: frozenset[str]
    patterns: dict[str, str]


def claim_reading(catalog: Catalog) -> ClaimReading:
    """The reading the files page takes of a claim's kind, for the routes that count by it.

    The list of experiments (``/api/v1/experiments``) counts a file's stage, role and kind by this
    one reading, so its counts agree with the files page's, which the Years card draws.
    """
    by_theme = {theme: name for name, (theme, _) in _KINDS.items()}
    terms: dict[str, set[str]] = {name: set() for name in _KINDS}
    for row in _rows(
        catalog,
        "SELECT DISTINCT term, theme_kind FROM dictionary_entries "
        "WHERE theme_kind IN (SELECT unnest(?::VARCHAR[]))",
        [sorted(by_theme)],
    ):
        terms[by_theme[str(row["theme_kind"])]].add(str(row["term"]))
    held = _dictionary_holds(catalog)
    return ClaimReading(
        terms={name: frozenset(found) for name, found in terms.items()},
        by_spelling=frozenset(name for name, (theme, _) in _KINDS.items() if theme not in held),
        patterns={name: pattern for name, (_, pattern) in _KINDS.items()},
    )


def _like(predicate: str, pattern: str) -> bool:
    """SQL ``LIKE`` with ``%`` alone, as the patterns of ``_KINDS`` are written."""
    import fnmatch

    return fnmatch.fnmatchcase(predicate, pattern.replace("%", "*"))


def _claims(catalog: Catalog, ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    found = _rows(
        catalog,
        """
        SELECT claim_id, subject, predicate, object_reference, inference_tier, rule_id,
               rule_version, inputs, support_json, defeaters_json, resolution_status
        FROM claims WHERE subject IN (SELECT unnest(?::VARCHAR[]))
        ORDER BY subject, predicate, rule_id, object_reference, claim_id
        """,
        [list(ids)],
    )
    held: dict[str, list[dict[str, Any]]] = {}
    for row in found:
        held.setdefault(str(row["subject"]), []).append(row)
    return held


def _clocks(
    row: Mapping[str, Any], named: Mapping[str, Any] | None, readers: Sequence[str]
) -> list[FileClock]:
    """Every time the run holds for one file, one per source, none merged."""
    held: list[FileClock] = []
    if row.get("modified_ns") is not None:
        held.append(
            FileClock(
                source="modified",
                detail="mtime",
                at=str(time_from_ns(row["modified_ns"])),
                until=None,
                precision="instant",
                read_by=[],
            )
        )
    if row.get("created_ns") is not None:
        held.append(
            FileClock(
                source="created",
                detail=str(row.get("created_time_source") or "birthtime"),
                at=str(time_from_ns(row["created_ns"])),
                until=None,
                precision="instant",
                read_by=[],
            )
        )
    source = str(row.get("event_time_source") or "")
    if row.get("event_time_ns") is not None and source != "filename":
        held.append(
            FileClock(
                source="event",
                detail=source or "content",
                at=str(time_from_ns(row["event_time_ns"])),
                until=None,
                precision="instant",
                read_by=list(readers),
            )
        )
    if named is not None and named.get("from"):
        ranged = bool(named.get("to")) and named.get("to") != named.get("from")
        held.append(
            FileClock(
                source="name",
                detail=str(named.get("source") or "name"),
                at=str(named["from"]),
                until=str(named["to"]) if ranged else None,
                precision="range" if ranged else "day",
                read_by=[],
            )
        )
    elif row.get("event_time_ns") is not None and source == "filename":
        held.append(
            FileClock(
                source="name",
                detail="filename",
                at=str(time_from_ns(row["event_time_ns"])),
                until=None,
                precision="instant",
                read_by=[],
            )
        )
    return held


def _believed(claims: Sequence[Mapping[str, Any]]) -> tuple[BelievedTime | None, str]:
    """The earliest credible time a rule concluded about the file, and the words saying it."""
    concluded: list[BelievedTime] = []
    for claim in claims:
        if claim["predicate"] != EARLIEST_PREDICATE:
            continue
        said = _loaded(claim["object_reference"])
        if not isinstance(said, dict) or not said.get("at"):
            continue
        source = str(said.get("source") or "")
        conflicting = claim["resolution_status"] == "examined_conflicting"
        concluded.append(
            BelievedTime(
                at=str(said["at"]),
                source=source,
                rule_id=str(claim["rule_id"]),
                rule_version=str(claim["rule_version"]),
                conflicting=conflicting,
                why=(
                    f"the earliest credible time {claim['rule_id']} (version "
                    f"{claim['rule_version']}) read from the clocks the census and the dive "
                    f"recorded, here its {source or 'chosen'} time"
                    + (
                        ", published as conflicting: a rule that withdraws it fired"
                        if conflicting
                        else ""
                    )
                ),
            )
        )
    if not concluded:
        return None, "no earliest credible time was concluded from what was read"
    chosen = min(concluded, key=lambda item: (item.conflicting, item.at, item.rule_id))
    return chosen, f"{chosen.at}, its {chosen.source or 'chosen'} time"


def _readers(catalog: Catalog, ids: Sequence[str]) -> dict[str, list[str]]:
    """The extractors that read each file's content."""
    held: dict[str, list[str]] = {}
    for entry_id, observer in catalog.query(
        "SELECT DISTINCT entry_id, observer FROM observations "
        "WHERE entry_id IN (SELECT unnest(?::VARCHAR[])) "
        "AND observer NOT IN (SELECT unnest(?::VARCHAR[])) ORDER BY entry_id, observer",
        [list(ids), list(_NOT_CONTENT)],
    ).fetchall():
        held.setdefault(str(entry_id), []).append(str(observer))
    return held


def _pairs(catalog: Catalog, ids: Sequence[str]) -> list[dict[str, Any]]:
    """Every derivation proposed or published with an end among ``ids``, each once."""
    proposed = _rows(
        catalog,
        """
        SELECT c.candidate_id, a.edge_id, c.rule_id, c.rule_version, c.source_id, c.target_id,
               c.decision_tier AS tier, c.support_json, a.edge_id IS NOT NULL AS published
        FROM candidates c
        LEFT JOIN assertions a ON a.candidate_id = c.candidate_id
             AND a.decision_status = 'asserted' AND a.primitive = 'derives'
        WHERE c.primitive = 'derives'
          AND (c.source_id IN (SELECT unnest(?::VARCHAR[]))
               OR c.target_id IN (SELECT unnest(?::VARCHAR[])))
        """,
        [list(ids), list(ids)],
    )
    published = _rows(
        catalog,
        """
        SELECT NULL AS candidate_id, a.edge_id, a.rule_id, a.rule_version, a.source_id,
               a.target_id, a.inference_tier AS tier, a.support_json, TRUE AS published
        FROM assertions a
        WHERE a.primitive = 'derives' AND a.decision_status = 'asserted'
          AND (a.candidate_id IS NULL
               OR a.candidate_id NOT IN (SELECT candidate_id FROM candidates))
          AND (a.source_id IN (SELECT unnest(?::VARCHAR[]))
               OR a.target_id IN (SELECT unnest(?::VARCHAR[])))
        """,
        [list(ids), list(ids)],
    )
    pairs = proposed + published
    pairs.sort(
        key=lambda pair: (
            str(pair["source_id"]),
            str(pair["target_id"]),
            str(pair["rule_id"]),
            str(pair["candidate_id"] or ""),
            str(pair["edge_id"] or ""),
        )
    )
    return pairs


def _entries(catalog: Catalog, ids: Iterable[str]) -> dict[str, tuple[str, str]]:
    return {
        str(entry_id): (str(name), str(path))
        for entry_id, name, path in catalog.query(
            "SELECT entry_id, name, relative_path FROM entries "
            "WHERE entry_id IN (SELECT unnest(?::VARCHAR[]))",
            [sorted(set(ids))],
        ).fetchall()
    }


def _copy_rules(catalog: Catalog) -> dict[str, dict[str, str]]:
    """The run's rules forming copies of one file, by identifier, with what each is."""
    return {
        str(row["rule_id"]): {
            "predicate": str(row["predicate"] or ""),
            "primitive": str(row["primitive"] or ""),
            "tier": str(row["tier"] or ""),
            "statement": str(row["statement"] or ""),
        }
        for row in _rows(
            catalog,
            "SELECT rule_id, predicate, primitive, tier, statement FROM rules "
            "WHERE effect = 'groups' AND (predicate IN (SELECT unnest(?::VARCHAR[])) "
            "OR primitive = 'identical')",
            [list(COPY_PREDICATES)],
        )
    }


def _copies(
    catalog: Catalog, claims: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, list[CopyGroup]]:
    """The sets of copies each file belongs to, each with its size and its other members."""
    from ingence.api.plain_words import rule_sentence

    rules = _copy_rules(catalog)
    keys = sorted(
        {
            (str(claim["rule_id"]), str(claim["object_reference"]))
            for held in claims.values()
            for claim in held
            if str(claim["rule_id"]) in rules
            and str(claim["predicate"]).startswith("groups.")
            and claim["object_reference"] is not None
        }
    )
    if not keys:
        return {}
    members: dict[tuple[str, str], list[str]] = {}
    wanted = set(keys)
    for rule_id, key, subject in catalog.query(
        "SELECT rule_id, object_reference, subject FROM claims "
        "WHERE predicate LIKE 'groups.%' "
        "AND resolution_status IN ('resolved', 'examined_conflicting') "
        "AND rule_id IN (SELECT unnest(?::VARCHAR[])) "
        "AND object_reference IN (SELECT unnest(?::VARCHAR[])) "
        "ORDER BY rule_id, object_reference, subject",
        [sorted({rule for rule, _ in keys}), sorted({key for _, key in keys})],
    ).fetchall():
        if (str(rule_id), str(key)) in wanted:
            members.setdefault((str(rule_id), str(key)), []).append(str(subject))
    shown = _entries(
        catalog, {subject for held in members.values() for subject in held[: OTHERS_LISTED + 1]}
    )
    groups: dict[str, list[CopyGroup]] = {}
    for subject, held in claims.items():
        for claim in held:
            spelled = (str(claim["rule_id"]), str(claim["object_reference"]))
            if spelled not in members:
                continue
            rule = rules[spelled[0]]
            proven = rule["primitive"] == "identical" and rule["tier"] == "entailed"
            others = [other for other in members[spelled] if other != subject]
            groups.setdefault(subject, []).append(
                CopyGroup(
                    rule_id=spelled[0],
                    rule_version=str(claim["rule_version"]),
                    kind="identical" if proven else "supposed copies",
                    evidence=rule_sentence(spelled[0]) or rule["statement"] or rule["predicate"],
                    key=spelled[1],
                    status=str(claim["resolution_status"]),
                    members=len(members[spelled]),
                    others=[
                        CopyMember(
                            id=other,
                            name=shown.get(other, (None, None))[0],
                            path=shown.get(other, (None, None))[1],
                        )
                        for other in others[:OTHERS_LISTED]
                    ],
                )
            )
    return groups


def _read_values(held: Mapping[str, bool]) -> list[ReadValue]:
    return [ReadValue(value=value, conflicting=held[value]) for value in sorted(held)]


def _handle_of(claim: Mapping[str, Any]) -> str | None:
    """A classifying claim's class handle (``review/claim_classes.claim_class_target``)."""
    from ingence.review.claim_classes import claim_class_target

    if not str(claim["predicate"]).startswith("classifies.") or claim["object_reference"] is None:
        return None
    return claim_class_target(str(claim["rule_id"]), str(claim["object_reference"]))


def _class_answer(
    handle: str | None, entry_id: str, answers: Mapping[str, tuple[str, frozenset[str] | None]]
) -> ClassAnswer | None:
    if handle is None or handle not in answers:
        return None
    word, members = answers[handle]
    return ClassAnswer(word=word, joined_after=members is not None and entry_id not in members)


def _class_answers(project: Path | None) -> dict[str, tuple[str, frozenset[str] | None]]:
    """The answer in force on each class of claims, with the files it was given about."""
    if project is None:
        return {}
    from ingence.record.answers import CLAIMS_CLASS
    from ingence.record.log import latest_by_target, read_events

    found: dict[str, tuple[str, frozenset[str] | None]] = {}
    for target, event in latest_by_target(read_events(project)).items():
        said = _loaded(event.payload)
        if not isinstance(said, dict) or said.get("of") != CLAIMS_CLASS:
            continue
        shown = said.get("members")
        found[target] = (
            str(said.get("verdict") or event.kind.value),
            frozenset(str(item) for item in shown) if isinstance(shown, list) else None,
        )
    return found


#: The busiest days already worked out, by project, run and experiment: a run is never changed
#: once published, and an experiment's files are read page by page.
_BUSY: dict[tuple[str, str, str], BusyDaysItem | None] = {}
_BUSY_KEPT = 256
_NS_PER_DAY = 86_400 * 1_000_000_000


def _believed_days(catalog: Catalog, ids: Sequence[str]) -> dict[str, int | None]:
    """The UTC day of each file's believed time, for the files with one."""
    from ingence.busy_days import day_of

    held: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(
        catalog,
        "SELECT subject, predicate, object_reference, rule_id, rule_version, resolution_status "
        "FROM claims WHERE predicate = ? AND subject IN (SELECT unnest(?::VARCHAR[])) "
        "ORDER BY subject, rule_id",
        [EARLIEST_PREDICATE, list(ids)],
    ):
        held.setdefault(str(row["subject"]), []).append(row)
    found: dict[str, int | None] = {}
    for subject, claims in held.items():
        believed, _ = _believed(claims)
        found[subject] = day_of(believed.at) if believed is not None else None
    return found


def _busy_days(
    catalog: Catalog,
    run_id: str,
    experiment_id: str,
    every: Sequence[Mapping[str, Any]],
    project: Path | None,
) -> BusyDaysItem | None:
    """The busiest days of every file of the experiment (:mod:`ingence.busy_days`)."""
    from ingence.busy_days import busiest_days, days_words

    key = (str(catalog.run_directory or project), run_id, experiment_id)
    if key in _BUSY:
        return _BUSY[key]
    believed = _believed_days(catalog, [str(row["entry_id"]) for row in every])
    days: list[int | None] = []
    for row in every:
        day = believed.get(str(row["entry_id"]))
        if day is None and row.get("modified_ns") is not None:
            day = int(row["modified_ns"]) // _NS_PER_DAY
        days.append(day)
    found = busiest_days(days)
    shown = (
        None
        if found is None
        else BusyDaysItem(
            first_day=found.first_day.isoformat(),
            last_day=found.last_day.isoformat(),
            said=days_words(found.first_day, found.last_day),
            files=found.files,
            dated_files=found.dated_files,
            share=found.files / found.dated_files,
            percent=found.percent,
        )
    )
    while len(_BUSY) >= _BUSY_KEPT:
        _BUSY.pop(next(iter(_BUSY)))
    _BUSY[key] = shown
    return shown


def experiment_files(
    catalog: Catalog,
    run_id: str,
    experiment_id: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    project: Path | None = None,
) -> ExperimentFilesResponse:
    """One page of one experiment's files, each with what the run found about it."""
    from ingence.api.v1.explorer.categories import stage_families

    answers = _class_answers(project)
    header, folders = _experiment(catalog, experiment_id)
    every = _members(catalog, folders)
    page = every[offset : offset + limit]
    ids = [str(row["entry_id"]) for row in page]
    claims = _claims(catalog, ids)
    readers = _readers(catalog, ids)
    held_kinds = _dictionary_holds(catalog)
    kinds = _kinds_of(
        catalog,
        (
            str(claim["object_reference"])
            for held in claims.values()
            for claim in held
            if claim["object_reference"] is not None
        ),
    )
    pairs = _pairs(catalog, ids)
    ends = _entries(
        catalog, {str(p["source_id"]) for p in pairs} | {str(p["target_id"]) for p in pairs}
    )
    copies = _copies(catalog, claims)
    acquisition_values = set(stage_families().get("acquisition", ()))

    rows: list[ExperimentFile] = []
    acquisitions: list[AcquisitionItem] = []
    for row in page:
        entry_id = str(row["entry_id"])
        held = claims.get(entry_id, [])
        concluded: dict[str, dict[str, bool]] = {name: {} for name in _KINDS}
        named: dict[str, Any] | None = None
        for claim in held:
            value = claim["object_reference"]
            predicate = str(claim["predicate"])
            if predicate == NAME_DATE_PREDICATE and named is None:
                said = _loaded(value)
                named = said if isinstance(said, dict) else None
            if value is None or not predicate.startswith("classifies."):
                continue
            for name, (theme, pattern) in _KINDS.items():
                by_value = name in kinds.get(str(value), set())
                by_name = theme not in held_kinds and _like(predicate, pattern)
                if by_value or by_name:
                    conflicting = claim["resolution_status"] == "examined_conflicting"
                    held_value = concluded[name].get(str(value), False)
                    concluded[name][str(value)] = held_value or conflicting
        believed, said = _believed(held)
        own_pairs: list[DerivationPair] = []
        derived: list[DerivedFile] = []
        for pair in pairs:
            source, target = str(pair["source_id"]), str(pair["target_id"])
            if entry_id not in (source, target):
                continue
            made_into = source == entry_id
            other = target if made_into else source
            other_name, other_path = ends.get(other, (None, None))
            own_pairs.append(
                DerivationPair(
                    candidate_id=pair["candidate_id"],
                    edge_id=pair["edge_id"],
                    rule_id=str(pair["rule_id"]),
                    rule_version=str(pair["rule_version"]),
                    direction="made_into" if made_into else "made_from",
                    other_id=other,
                    other_name=other_name,
                    other_path=other_path,
                    tier=str(pair["tier"] or ""),
                    published=bool(pair["published"]),
                    support=_support(pair["support_json"]),
                )
            )
            if made_into:
                derived.append(
                    DerivedFile(
                        id=other,
                        name=other_name,
                        path=other_path,
                        rule_id=str(pair["rule_id"]),
                        published=bool(pair["published"]),
                    )
                )
        rows.append(
            ExperimentFile(
                id=entry_id,
                name=str(row["name"]),
                path=str(row["relative_path"]),
                size_bytes=row["size_bytes"],
                extension=str(row["extension"] or "").lower(),
                scan_outcome=str(row.get("scan_outcome") or "metadata_only"),
                format=_read_values(concluded["format"]),
                stage=_read_values(concluded["stage"]),
                role=_read_values(concluded["role"]),
                clocks=_clocks(row, named, readers.get(entry_id, [])),
                believed=believed,
                believed_said=said,
                claims=[
                    FileClaim(
                        claim_id=str(claim["claim_id"]),
                        predicate=str(claim["predicate"]),
                        value=claim["object_reference"],
                        rule_id=str(claim["rule_id"]),
                        rule_version=str(claim["rule_version"]),
                        tier=str(claim["inference_tier"] or ""),
                        status=str(claim["resolution_status"] or ""),
                        evidence=_records(claim["inputs"]),
                        support=_support(claim["support_json"]),
                        defeaters=_defeaters(claim["defeaters_json"]),
                        class_handle=_handle_of(claim),
                        class_answer=_class_answer(_handle_of(claim), entry_id, answers),
                    )
                    for claim in held
                ],
                derivations=own_pairs,
                copies=copies.get(entry_id, []),
            )
        )
        if set(concluded["stage"]) & acquisition_values:
            acquisitions.append(
                AcquisitionItem(
                    id=entry_id,
                    name=str(row["name"]),
                    path=str(row["relative_path"]),
                    derived=derived,
                )
            )
    return ExperimentFilesResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        experiment=header,
        files=len(every),
        opened_files=sum(1 for row in every if str(row.get("scan_outcome")) in READ_OUTCOMES),
        offset=offset,
        limit=limit,
        rows=rows,
        acquisitions=acquisitions,
        notes=list(NOTES),
        busy_days=_busy_days(catalog, run_id, experiment_id, every, project),
    )


@route("experiments/files", model=ExperimentFilesResponse)
def experiment_files_route(
    context: V1Context, query: Mapping[str, Sequence[str]]
) -> ExperimentFilesResponse:
    named = (one(query, "experiment") or "").strip()
    if not named:
        message = "name the experiment by its folder's identifier: experiment=<entry id>"
        raise ApiError(message, parameter="experiment")
    offset = integer(query, "offset", default=0, low=0, high=10_000_000)
    limit = integer(query, "limit", default=DEFAULT_LIMIT, low=1, high=LIMIT)
    with pinned_catalog(context) as (catalog, run_id):
        return experiment_files(
            catalog, run_id, named, offset=offset, limit=limit, project=context.project
        )
