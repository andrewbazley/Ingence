"""A person's answer that one experiment's analysis is elsewhere.

"No analysis found" says what the engine did not find, never that nothing exists. "Analysed
elsewhere" is a person's answer to it: the analysis of this experiment is somewhere the engine did
not read. It is a Record event like every answer. It is written through the one answer route
(``api/routes/decide.decide_target``) under the person's name (the machine's decider), on its own
target (``analysed_elsewhere:<entry_id>``, ``record/targets.py``):

- It uses an existing decision kind, with a target of its own. ``confirm`` says the analysis is
  elsewhere, and ``reject`` takes that back. There is no new event kind, so no schema bump: the
  kinds say "what was raised is right" and "it is not", which is exactly what a person says here.
- The run reads it at its Record watermark, so the run can be made again from the Record it read.
  The Dashboard never runs anything by itself. So an answer leaves its experiment off "no analysis
  found" at the next run and never before, and the page shows it waiting for the re-run meanwhile.
- It is undone like any decision: answered the other way, or withdrawn by going back to an earlier
  run (schema 1.22), which the Record's replay honours.

Nothing here writes. Reading is :func:`elsewhere_answers`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.schema import RecordEventKind
from ingence.record.verdicts import shown_word

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.storage.catalog import Catalog

__all__ = [
    "ELSEWHERE",
    "ELSEWHERE_WORDS",
    "NOT_ELSEWHERE",
    "ElsewhereSaid",
    "elsewhere_answers",
    "experiment_folder_of",
    "run_watermark",
]

ELSEWHERE = "elsewhere"
NOT_ELSEWHERE = "not_elsewhere"

#: The words a page offers, and the Record event each becomes.
ELSEWHERE_WORDS: tuple[dict[str, str], ...] = (
    {
        "word": ELSEWHERE,
        "label": shown_word(ELSEWHERE),
        "meaning": "the analysis of this experiment is somewhere the engine did not read",
        "kind": RecordEventKind.CONFIRM.value,
    },
    {
        "word": NOT_ELSEWHERE,
        "label": shown_word(NOT_ELSEWHERE),
        "meaning": "take that back: its analysis is not known to be elsewhere",
        "kind": RecordEventKind.REJECT.value,
    },
)


@dataclass(frozen=True, slots=True)
class ElsewhereSaid:
    """What a person said of one experiment, as the run read it and as the Record stands now."""

    experiment_id: str
    #: Who said its analysis is elsewhere, when, and in which event.
    actor: str
    occurred_at: str
    event_id: str
    #: Whether the run read the answer (it was in force at the run's watermark): only then does
    #: the experiment leave "no analysis found".
    read_by_run: bool
    #: Whether the answer is in force in the Record now. Differs from ``read_by_run`` only while
    #: a change waits for the next re-run.
    said_now: bool
    #: Who took it back since the run, where somebody did.
    taken_back_by: str | None = None

    @property
    def said(self) -> str:
        """The words a page shows beside the flag."""
        said = f"{self.actor} said its analysis is elsewhere ({self.occurred_at[:10]})"
        if self.read_by_run and self.said_now:
            return (
                f"{said}; this run read the answer, so it is not listed as having no analysis "
                "found."
            )
        if self.said_now:
            return f"{said}; it leaves the list at the next re-run."
        by = f" by {self.taken_back_by}" if self.taken_back_by else ""
        return f"{said}; taken back{by} since this run, it returns to the list at the next re-run."


def run_watermark(catalog: Catalog) -> int | None:
    """The Record sequence the catalog's run read up to; None for a run that records none."""
    import duckdb

    try:
        row = catalog.query("SELECT record_watermark FROM run LIMIT 1").fetchone()
    except duckdb.Error:  # a run written before the column existed
        return None
    return int(row[0]) if row is not None and row[0] is not None else None


def _latest(events: tuple[RecordEvent, ...]) -> dict[str, RecordEvent]:
    from ingence.record.log import latest_by_target
    from ingence.record.targets import experiment_analysed_elsewhere

    return {
        experiment: event
        for target, event in latest_by_target(events).items()
        if (experiment := experiment_analysed_elsewhere(target)) is not None
    }


def elsewhere_answers(project: Path, watermark: int | None) -> dict[str, ElsewhereSaid]:
    """Every experiment a person said is analysed elsewhere, by its folder's entry: as the run
    read the Record (at ``watermark``; a run recording none read no answer) and as it stands now.

    An experiment answered neither then nor now is left out.
    """
    from ingence.record.log import read_at, read_events

    now = _latest(read_events(project))
    if watermark is None:
        then: dict[str, RecordEvent] = {}
    else:
        with read_at(project, watermark):
            then = _latest(read_events(project))
    found: dict[str, ElsewhereSaid] = {}
    for experiment in sorted(set(now) | set(then)):
        was = then.get(experiment)
        is_now = now.get(experiment)
        read = was is not None and was.kind is RecordEventKind.CONFIRM
        said = is_now is not None and is_now.kind is RecordEventKind.CONFIRM
        if not read and not said:
            continue
        shown = is_now if said else was
        if shown is None:  # pragma: no cover - one of the two confirms
            continue
        found[experiment] = ElsewhereSaid(
            experiment_id=experiment,
            actor=shown.actor,
            occurred_at=shown.occurred_at,
            event_id=shown.event_id,
            read_by_run=read,
            said_now=said,
            taken_back_by=is_now.actor if read and not said and is_now is not None else None,
        )
    return found


def experiment_folder_of(catalog: Catalog, entry_id: str) -> str | None:
    """The folder of an experiment the catalog's run found, or None.

    An experiment is a folder that the experiment rules' policy chose, under no other such folder
    (``ingence.experiment_units``). A person answers only for an experiment the page could have
    shown, so this is the same set that the page counts. Reading every folder a rule resolved would
    refuse an answer about a person's dated folder that the page counts as an experiment, because
    the person's folder above it would also count as one.
    """
    from ingence.experiment_units import run_units

    found = run_units(catalog)
    return found.paths[entry_id] if entry_id in set(found.units.values()) else None
