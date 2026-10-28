"""Areas: the folders at the area level, and what each folder stands for.

An area is the folder ``themes.area_level`` levels below a root. This is the number that the
themes' ``person_level`` also reads: ``experiment_units.person_of`` and :func:`area_of` cut the
same path the same way. The area's kind is what the folder is named for: a person, an
instrument, a date, a project or a plain folder (:class:`~ingence.core.schema.AreaKind`). A
page says "by person", "by instrument" or "by year" from the kind, and "by folder" where the
engine could read nothing more.

The kind is read from the name and assumed until a person says (``POST /api/areas/kind``,
written on the Record target ``area:<entry_id>``). The reading is one pure function,
:func:`guess_area_kind`. So the rule in the Rulebook (``revision_5/areas.py``, through the
``area_kind_from_folder_name`` predicate), the readouts and the ``/api/v1/areas`` route all read
a folder the same way. The route answers from the file list before any rule has run.

- ``date`` where the folder's name holds a year or a date (``2019``, ``2021-03``, ``20210305``);
- ``instrument`` where a word of the name is an instrument's spelling (the ``instruments`` list
  of the shipped file-type table), or where one instrument file type dominates the folder;
- ``person`` only where the name is an actor the answer log holds, or a person said so;
- ``folder`` otherwise. ``project`` is never guessed: a person says it.

Every example name here is invented.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.schema import AreaKind

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.storage.catalog import Catalog

__all__ = [
    "AREA_RULE_ID",
    "KIND_WORDS",
    "AreaGuess",
    "AreaRow",
    "actors_of",
    "ambiguous_instrument_spellings",
    "answered_area_kinds",
    "area_id",
    "area_kinds_of_run",
    "area_of",
    "area_rows",
    "claimed_area_kinds",
    "dominant_instrument_type",
    "guess_area_kind",
    "instrument_spellings",
    "leading_kind",
    "name_words",
    "refused_area_entries",
    "write_area_kind",
]

#: The rule that assumes an area's kind (``rulebook/revision_5/areas.py``).
AREA_RULE_ID = "area_kind_from_folder_name"

#: A year on its own (1990 to 2039) or a date written as digits, with or without separators.
_YEAR = re.compile(r"(?<!\d)(?:19[9]\d|20[0-3]\d)(?!\d)")
_DATE = re.compile(
    r"(?<!\d)(?:19[9]\d|20[0-3]\d)[-_.]?(?:0[1-9]|1[0-2])(?:[-_.]?(?:0[1-9]|[12]\d|3[01]))?(?!\d)"
)
#: How a folder's name is cut into words for the instrument match: on anything but letters
#: and digits. Only whole words match. A spelling inside a longer word (``quorlin2``,
#: ``requorlin``) is not a match, so an instrument's short spelling never fires inside another
#: word.
_WORDS = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class AreaGuess:
    """What a folder's name was read as, and why, in words a page can show."""

    kind: AreaKind
    reason: str
    #: Whether the engine read it (assumed) rather than a person saying so.
    assumed: bool = True


def area_of(folder: str, level: int | None) -> str | None:
    """The area a folder lies in: the folder ``level`` levels below its root, by its name.

    The same cut as ``experiment_units.person_of``: ``folder`` is a path relative to its root
    (``.`` for the root itself). The area is its ``level``-th part, or None where ``level`` is
    blank or the folder lies at or above that level.
    """
    if not level:
        return None
    parts = [part for part in folder.strip("/").split("/") if part and part != "."]
    return parts[level - 1] if len(parts) >= level else None


def area_id(root_id: str, name: str) -> str:
    """The identifier of an area a page refers to it by: its root and its folder's name."""
    return f"area:{root_id}:{name}"


def name_words(name: str) -> tuple[str, ...]:
    """The words of a folder's name, case folded, for the instrument match."""
    return tuple(word.casefold() for word in _WORDS.findall(name))


def instrument_spellings() -> frozenset[str]:
    """Every spelling of every instrument that the shipped file-type table lists.

    One function holds the list, so a change to the list is made in one place.
    """
    from ingence.dictionary.file_types import instruments

    return frozenset(
        spelling.casefold()
        for instrument in instruments()
        for spelling in (instrument.name, *instrument.spellings)
    )


def ambiguous_instrument_spellings() -> frozenset[str]:
    """The spellings the table marks ``ambiguous``: also ordinary English or biology words, so a
    folder named that one word alone is never read as the instrument's."""
    from ingence.dictionary.file_types import instruments

    return frozenset(
        spelling.casefold()
        for instrument in instruments()
        if instrument.ambiguous
        for spelling in (instrument.name, *instrument.spellings)
    )


def guess_area_kind(
    name: str,
    *,
    actors: Collection[str] = (),
    instruments: Collection[str] | None = None,
    ambiguous: Collection[str] | None = None,
    dominant_instrument_type: str | None = None,
) -> AreaGuess:
    """Read what a folder's name stands for, in the order the kinds are decided.

    ``actors`` are the names the answer log holds (a person's folder is read as a person's only
    where the name is one of them); ``instruments`` the spellings to match, whole words only
    (the shipped list where None), and ``ambiguous`` those of them that are also ordinary words,
    which match only beside other words (the table's where None); ``dominant_instrument_type``
    the file type that dominates the folder where one instrument's own format does, read by the
    caller from the file list.
    """
    cleaned = name.strip()
    folded = cleaned.casefold()
    if _DATE.search(cleaned) or _YEAR.search(cleaned):
        return AreaGuess(AreaKind.DATE, "the folder's name holds a year or a date")
    spellings = instrument_spellings() if instruments is None else frozenset(instruments)
    doubtful = ambiguous_instrument_spellings() if ambiguous is None else frozenset(ambiguous)
    words = name_words(cleaned)
    matched = [
        word for word in words if word in spellings and (word not in doubtful or len(words) > 1)
    ]
    if matched:
        return AreaGuess(
            AreaKind.INSTRUMENT, f"the folder's name matches the instrument {matched[0]}"
        )
    if dominant_instrument_type:
        return AreaGuess(
            AreaKind.INSTRUMENT,
            f"most of the folder's files are of one instrument's type, {dominant_instrument_type}",
        )
    known = {actor.strip().casefold() for actor in actors if actor and actor.strip()}
    if folded in known:
        return AreaGuess(AreaKind.PERSON, "the folder's name is a person who answered here")
    return AreaGuess(AreaKind.FOLDER, "the folder's name says nothing more")


def actors_of(events: Iterable[object]) -> frozenset[str]:
    """The actors of the answer log's events, case folded (for the person kind)."""
    return frozenset(
        str(actor).strip().casefold()
        for actor in (getattr(event, "actor", None) for event in events)
        if actor and str(actor).strip()
    )


def dominant_instrument_type(counts: Mapping[str, int], *, share: float = 0.5) -> str | None:
    """The instrument file type that holds at least ``share`` of a folder's typed files, or None.

    ``counts`` maps each instrument file type's name to its files in the folder, beside a key
    ``""`` for every other file. One type at or over the share dominates.
    """
    total = sum(counts.values())
    if total <= 0:
        return None
    best = max(
        ((name, count) for name, count in counts.items() if name),
        key=lambda item: item[1],
        default=None,
    )
    if best is None or best[1] / total < share:
        return None
    return best[0]


@dataclass(frozen=True, slots=True)
class AreaRow:
    """One area as a run holds it: the folder, its kind and how it was read, and its files."""

    id: str
    entry_id: str
    root_id: str
    name: str
    kind: str
    kind_assumed: bool
    reason: str
    files: int


def answered_area_kinds(project: Path) -> dict[str, str]:
    """Each area folder a person said the kind of, by its entry, from the answer log."""
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.targets import AREA_PREFIX

    answered: dict[str, str] = {}
    for target, event in latest_by_target(read_events(project)).items():
        prefix, _, entry_id = target.partition(":")
        if prefix != AREA_PREFIX or not entry_id or event.kind.value == "withdraw":
            continue
        try:
            said = json.loads(event.payload or "{}")
        except (TypeError, ValueError):
            continue
        kind = (said if isinstance(said, dict) else {}).get("kind")
        if isinstance(kind, str) and kind in {item.value for item in AreaKind}:
            answered[entry_id] = kind
    return answered


def claimed_area_kinds(catalog: Catalog) -> dict[str, str]:
    """Each area folder's kind as the area rule claimed it in this run, by its entry."""
    rows = catalog.query(
        "SELECT subject, object_reference FROM claims WHERE rule_id = ? "
        "AND object_reference IS NOT NULL AND resolution_status = 'resolved'",
        [AREA_RULE_ID],
    ).fetchall()
    return {str(subject): str(value) for subject, value in rows}


def refused_area_entries(project: Path, catalog: Catalog) -> frozenset[str]:
    """The area folders whose class of area-rule claims a person answered was read wrongly
    (the class handle ``class:<rule>:<digest of the value>``, as ``/api/v1/claims/class`` answers
    it): each is a plain folder, not assumed, and never guessed again."""
    from ingence.record.answers import answers_read
    from ingence.review.claim_classes import claim_class_target

    try:
        refused = answers_read(_events(project)).refused_claim_classes()  # type: ignore[arg-type]
    except Exception:
        return frozenset()
    if not refused:
        return frozenset()
    rows = catalog.query(
        "SELECT subject, object_reference FROM claims WHERE rule_id = ? "
        "AND object_reference IS NOT NULL",
        [AREA_RULE_ID],
    ).fetchall()
    return frozenset(
        str(subject)
        for subject, value in rows
        if claim_class_target(AREA_RULE_ID, str(value)) in refused
    )


def area_rows(catalog: Catalog, project: Path, level: int | None) -> list[AreaRow]:
    """Every area of the catalog's run with its kind: a person's answer first, then the area
    rule's claim where the run holds one, else the name read now (:func:`guess_area_kind`).

    From the file list alone, so it answers as soon as a walk ends: the folders ``level``
    below a root, the files beneath each, and the instrument file types among them.
    """
    if not level:
        return []
    from ingence.dictionary.file_types import file_type_table

    instrument_types: dict[str, str] = {}
    for row in file_type_table().rows:
        if row.category == "instrument":
            for extension in row.extensions:
                instrument_types.setdefault(extension.casefold(), row.name)
    folders = catalog.query(
        "SELECT entry_id, root_id, relative_path, name FROM entries WHERE NOT is_deleted "
        "AND entry_type = 'directory' AND entry_kind = 'filesystem' AND depth = ? "
        "ORDER BY root_id, relative_path",
        [int(level)],
    ).fetchall()
    answered = answered_area_kinds(project)
    claimed = claimed_area_kinds(catalog)
    refused = refused_area_entries(project, catalog)
    actors = actors_of(_events(project))
    rows: list[AreaRow] = []
    for entry_id, root_id, relative_path, name in folders:
        prefix = str(relative_path).rstrip("/") + "/"
        typed = catalog.query(
            "SELECT lower(coalesce(extension, '')), count(*) FROM entries WHERE NOT is_deleted "
            "AND entry_type = 'file' AND root_id = ? AND starts_with(relative_path, ?) GROUP BY 1",
            [root_id, prefix],
        ).fetchall()
        files = sum(int(count) for _, count in typed)
        counts: dict[str, int] = {"": 0}
        for extension, count in typed:
            found = instrument_types.get(str(extension))
            counts[found or ""] = counts.get(found or "", 0) + int(count)
        entry = str(entry_id)
        if entry in answered:
            kind, assumed, reason = answered[entry], False, "a person said so"
        elif entry in refused:
            # A refused kind is not guessed again. The folder is a plain folder, and the kind is not
            # marked assumed.
            kind, assumed, reason = AreaKind.FOLDER.value, False, "a person refused the reading"
        elif entry in claimed:
            kind, assumed, reason = claimed[entry], True, "read by the area rule"
        else:
            guess = guess_area_kind(
                str(name),
                actors=actors,
                dominant_instrument_type=dominant_instrument_type(counts),
            )
            kind, assumed, reason = guess.kind.value, True, guess.reason
        rows.append(
            AreaRow(
                id=area_id(str(root_id), str(name)),
                entry_id=entry,
                root_id=str(root_id),
                name=str(name),
                kind=kind,
                kind_assumed=assumed,
                reason=reason,
                files=files,
            )
        )
    rows.sort(key=lambda row: (-row.files, row.name))
    return rows


def area_kinds_of_run(project: Path, run_id: str) -> dict[str, tuple[str, bool]]:
    """Each area's kind and whether it is assumed, by the area's identifier, for one run: from
    the stored Overview readout where the run has one (built after inference), else read from
    the run now (the file list, the rule's claims and the answers)."""
    from ingence.readouts import read_readout

    held = read_readout(project, run_id, "overview")
    if held is not None:
        rows = held.get("payload", {}).get("areas") or []
        kinds = {
            str(row["id"]): (str(row["kind"]), bool(row["kind_assumed"]))
            for row in rows
            if isinstance(row, dict) and "id" in row
        }
        # A person's answer given since the readouts were made wins. The readout key does not
        # follow the answer log, so the answers are read over the stored kinds, and every route
        # says what ``/api/v1/areas`` says.
        by_entry = held.get("areas_by_entry") or {}
        for entry_id, kind in answered_area_kinds(project).items():
            area = by_entry.get(entry_id)
            if isinstance(area, str) and area in kinds:
                kinds[area] = (kind, False)
        return kinds
    from ingence.core.project import Project
    from ingence.storage.catalog import open_catalog

    level = Project.open(project).settings.themes.area_level
    with open_catalog(project, run_id=run_id) as catalog:
        return {row.id: (row.kind, row.kind_assumed) for row in area_rows(catalog, project, level)}


#: The word for one area of each kind, and for several (the ladder's level word).
KIND_WORDS: dict[str, tuple[str, str]] = {
    "person": ("person", "people"),
    "instrument": ("instrument", "instruments"),
    "date": ("year", "years"),
    "project": ("project", "projects"),
    "folder": ("folder", "folders"),
}


def leading_kind(kinds: Iterable[str]) -> str | None:
    """The kind most of the areas are, or None where there are no areas."""
    from collections import Counter

    counted = Counter(kind for kind in kinds if kind)
    if not counted:
        return None
    return max(sorted(counted), key=lambda kind: counted[kind])


def write_area_kind(project: Path, entry_id: str, kind: str, *, actor: str) -> RecordEvent:
    """Write a person's answer on what an area's folder stands for: one ``confirm`` on
    ``area:<entry_id>`` with the kind in its payload, against the current run (the run during a
    scan, where the pin is not yet set; refused in words where there is no run at all)."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event, now
    from ingence.record.targets import area_target
    from ingence.storage.commit import read_pointer

    if kind not in {item.value for item in AreaKind}:
        message = f"kind is one of {', '.join(item.value for item in AreaKind)}"
        raise ValueError(message)
    pointer = read_pointer(project)
    if pointer is None:
        message = "this project has no run yet; an answer is written against a run"
        raise ValueError(message)
    return append_event(
        project,
        kind=RecordEventKind.CONFIRM,
        actor=actor,
        occurred_at=now(),
        target=area_target(entry_id),
        against_run_id=pointer.run_id,
        payload={"kind": kind},
    )


def _events(project: Path) -> tuple[object, ...]:
    from ingence.record.log import read_events

    try:
        return tuple(read_events(project))
    except Exception:  # a project with no answer log yet has no actors
        return ()
