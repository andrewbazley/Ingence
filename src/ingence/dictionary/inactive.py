"""What a person inactivated in the Dictionary, read back out of the Record.

Nothing in the Dictionary is deleted, because an inactive entry says more than an absent one. A
person inactivates a sense (``sense:<term>``) or one spelling of one sense
(``surface:<term>:<spelling>``) with a ``reject`` on that target, and brings it back with a later
``confirm``, through the Record's one way in. The latest decision on each target is the one in force
(:func:`~ingence.record.log.latest_by_target`), and every earlier one stays readable. That is how
the page says "inactive since <date>, by <name>".

The Dictionary a run consults reads the inactivations (:meth:`DictionaryStore.for_project`), exactly
where it reads a person's answers, so a conclusion can be made again from the same inputs in both
cases. A run records the Record's watermark before it reads either. Its vocabulary fingerprint
(``dictionary_hash``) changes with every inactivation in force, since the senses it hashes lose what
was inactivated. Replaying the Record to the watermark reproduces the run. The shipped files are
never edited. An inactivation is applied to the composed vocabulary, never to a section file.

An inactivation takes effect with the next reading of every name. It changes a sense that no answer
names, so the partial rebind refuses it, as it refuses any change but a person's answers. The
applier's worker reads every name again instead. Carrying the other names' readings past an
inactivation would also carry them past whatever else had changed the same sense (a pack
installed, an engine upgrade), under a fingerprint that said otherwise.

- An inactive spelling binds nothing for that sense. It leaves the sense's surfaces, so every
  consumer of the vocabulary (the lookup, the unit and format tables, search) stops seeing it.
  The same holds for the sense's spellings that differ from it only in case (``FPS`` and ``fps``).
  Another sense's spelling is untouched, so a name may still bind that sense by its case-folded
  match.
- An inactive sense is offered nowhere. It leaves the vocabulary whole, whatever its origin: the
  core's, a pack's or a person's own answers that name it.

A sense whose every spelling is inactive stays a sense (it can still be named in an answer) and
binds nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from ingence.core.schema import RecordEventKind
from ingence.record.log import latest_by_target, read_events
from ingence.record.targets import SENSE_PREFIX, surface_named_by

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.dictionary.store import Section

__all__ = [
    "INACTIVE_MARK",
    "Inactivation",
    "Inactivations",
    "inactivations_in_force",
    "inactive_meanings",
    "inactive_refusal",
    "without_inactive",
]

#: How an inactivation reads in the meanings the applier compares before and after a write
#: (``api/apply.py``), so a spelling whose state changed is re-bound at once.
INACTIVE_MARK = "inactive"


@dataclass(frozen=True, slots=True)
class Inactivation:
    """One sense or one spelling a person inactivated, and the event that did it."""

    term: str
    #: The spelling, or None where the whole sense is inactive.
    spelling: str | None
    actor: str
    occurred_at: str
    event_id: str
    sequence: int
    #: The spellings the inactivation stopped binding when it was written: the one spelling, or
    #: every surface the sense held then. Read by the applier to re-bind them at once.
    spellings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Inactivations:
    """Everything inactive in one project's Dictionary, by sense and by spelling."""

    senses: dict[str, Inactivation] = field(default_factory=dict)
    #: Keyed by the term and the spelling case folded. One spelling of one sense covers the forms
    #: of it that differ only in case. The lookup falls back to a case-folded match, so without this
    #: key ``FPS`` inactivated beside ``fps`` would still bind.
    surfaces: dict[tuple[str, str], Inactivation] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.senses or self.surfaces)

    def sense(self, term: str) -> Inactivation | None:
        """The inactivation in force on a whole sense, or None."""
        return self.senses.get(term)

    def surface(self, term: str, spelling: str) -> Inactivation | None:
        """The inactivation in force on one spelling of one sense, or None."""
        return self.surfaces.get((term, spelling.casefold()))

    def as_payload(self) -> list[dict[str, object]]:
        """Every inactivation in canonical order, as a page or an export states it."""
        found = [*self.senses.values(), *self.surfaces.values()]
        return [
            {
                "term": item.term,
                "spelling": item.spelling,
                "actor": item.actor,
                "occurred_at": item.occurred_at,
                "event_id": item.event_id,
            }
            for item in sorted(found, key=lambda item: (item.term, item.spelling or ""))
        ]


def _spellings(event: RecordEvent) -> tuple[str, ...]:
    try:
        payload = json.loads(event.payload)
    except (TypeError, ValueError):
        return ()
    held = payload.get("spellings") if isinstance(payload, dict) else None
    if not isinstance(held, list):
        return ()
    return tuple(sorted({str(item) for item in held if isinstance(item, str) and item.strip()}))


def _named_by(target: str) -> tuple[str, str | None] | None:
    """The term, and the spelling or None, an inactivation's target names; None for any other."""
    sense_prefix = f"{SENSE_PREFIX}:"
    if target.startswith(sense_prefix):
        term = target[len(sense_prefix) :]
        return (term, None) if term else None
    return surface_named_by(target)


def inactivations_in_force(
    project: Path, events: Sequence[RecordEvent] | None = None
) -> Inactivations:
    """Return every sense and spelling whose latest decision in the Record inactivates it.

    ``events`` is the Record already read, where a caller has it; otherwise it is read (held
    between reads in one process, so a page and the vocabulary pay for it once).
    """
    read = read_events(project) if events is None else events
    senses: dict[str, Inactivation] = {}
    surfaces: dict[tuple[str, str], Inactivation] = {}
    for target, event in sorted(latest_by_target(read).items()):
        named = _named_by(target)
        if named is None or event.kind is not RecordEventKind.REJECT:
            continue
        term, spelling = named
        found = Inactivation(
            term=term,
            spelling=spelling,
            actor=event.actor,
            occurred_at=event.occurred_at,
            event_id=event.event_id,
            sequence=event.sequence,
            spellings=(spelling,) if spelling is not None else _spellings(event),
        )
        if spelling is None:
            senses[term] = found
        else:
            surfaces[(term, spelling.casefold())] = found
    return Inactivations(senses=senses, surfaces=surfaces)


def without_inactive(sections: Iterable[Section], inactive: Inactivations) -> tuple[Section, ...]:
    """Return the sections with every inactive sense left out and every inactive spelling taken
    out of its sense; sections holding nothing inactive are returned as they were."""
    listed = tuple(sections)
    if not inactive:
        return listed
    kept: list[Section] = []
    for section in listed:
        senses = []
        changed = False
        for sense in section.senses:
            if sense.term in inactive.senses:
                changed = True
                continue
            surfaces = tuple(
                surface
                for surface in sense.surfaces
                if (sense.term, surface.casefold()) not in inactive.surfaces
            )
            if surfaces != sense.surfaces:
                changed = True
                sense = replace(sense, surfaces=surfaces)
            senses.append(sense)
        kept.append(replace(section, senses=tuple(senses)) if changed else section)
    return tuple(kept)


def inactive_meanings(project: Path) -> dict[str, set[str]]:
    """Each spelling an inactivation in force stops binding, casefolded, with what stopped it.

    Merged into what the applier compares before and after a write (``api/apply.py``), so an
    inactivation or a reactivation re-binds the names holding those spellings at once, through
    the partial rebind, as a definition does.
    """
    found: dict[str, set[str]] = {}
    inactive = inactivations_in_force(project)
    for item in (*inactive.senses.values(), *inactive.surfaces.values()):
        for spelling in item.spellings:
            found.setdefault(spelling.casefold(), set()).add(
                f"{INACTIVE_MARK}|{item.term}|{item.spelling or ''}"
            )
    return found


def inactive_refusal(project: Path, *, spelling: str, term: str) -> str | None:
    """Why an answer naming ``term`` for ``spelling`` would do nothing, or None where it would.

    A later answer that names what a person inactivated is refused, never written and ignored.
    The answer route's writers and the Dictionary page's own add say the same words, and the person
    brings the sense or spelling back on the Dictionary page first.
    """
    held = inactivations_in_force(project)
    named = term.strip()
    sense = held.sense(named)
    if sense is not None:
        return (
            f"{named} is inactive since {sense.occurred_at[:10]}; bring it back in the "
            "Dictionary section of Engine first"
        )
    found = held.surface(named, spelling.strip())
    if found is not None:
        return (
            f"{spelling.strip()} as {named} is inactive since {found.occurred_at[:10]}; bring it "
            "back in the Dictionary section of Engine first"
        )
    return None
