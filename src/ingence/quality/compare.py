"""A run's quality figures beside the run before it, as rows and as sentences.

The run manifest says what changed in a run from the run it is compared with. This adds how the
run's quality figures stand beside that run's: each figure's count and what it is out of, the
earlier run's, and the change, and one sentence with its counts for every figure that moved.

The module gives counts only, never a bare percentage. A page draws a share as a bar from the
count and its whole, and nothing here is a rate. Absence is kept in its states. A figure that one
of the runs does not reach or does not record is said as that, and is never taken as zero. A
breakdown value that one run lacks, where the figure is counted in both, is zero in it. This is
because a breakdown that lists only what it found (the claims by relation and tier) lists nothing
for what was not there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ingence.core.schema import QualityFigure, QualityState
from ingence.quality.catalogue import catalogue, reached
from ingence.quality.publish import stored
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

__all__ = [
    "QualityRow",
    "compare_quality",
    "figures_of",
    "quantity_said",
    "version_changes",
    "versions_of",
]

NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class QualityRow:
    """One figure of one run beside the compared run's value of it."""

    section: str
    figure: str
    facet: str
    label: str
    unit: str
    method: str
    state: str
    count: int | None
    total: int | None
    seconds: float | None
    was_state: str | None
    was_count: int | None
    was_total: int | None
    was_seconds: float | None
    #: Count (or seconds) now minus then, where both are counted. It is ``None`` otherwise.
    change: float | None
    #: True where the count, what it is out of, or the duration differs from the compared run's.
    changed: bool
    #: True for a figure of the machine and the moment (a duration, a peak memory): shown in
    #: the table, and left out of the sentences about what moved.
    volatile: bool
    said: str
    #: Why the figure is not recorded, in the pass's words. Empty otherwise.
    note: str
    #: The compared run's note on the same figure.
    was_note: str


def figures_of(project: Path, run_id: str) -> list[QualityFigure]:
    """The figures a run stored, or none for a run an older engine wrote."""
    with open_catalog(project, run_id=run_id) as catalog:
        return stored(catalog)


def versions_of(project: Path, run_id: str) -> dict[str, str]:
    """The versions that wrote a run, read from its record and never copied into the figures:
    the engine, its commit, the schema, the name reader, the Rulebook and Dictionary digests and
    the packs loaded. A version the record does not state is the empty string."""
    with open_catalog(project, run_id=run_id) as catalog:
        row = catalog.query(
            "SELECT engine_version, engine_commit, schema_version, normalizer_version, "
            "rulebook_hash, dictionary_hash, packs_json FROM run"
        ).fetchone()
        # A run that read no names states no reader. The components it carries still say which
        # one read them.
        carried = catalog.query(
            "SELECT string_agg(DISTINCT normalizer_version, ';' ORDER BY normalizer_version) "
            "FROM components"
        ).fetchone()
    engine, commit, schema, reader, rulebook, dictionary, packs = row or (None,) * 7
    reader = reader or (carried[0] if carried else None)
    loaded = json.loads(packs) if packs else []
    return {
        "engine_version": str(engine or ""),
        "engine_commit": str(commit or ""),
        "schema_version": str(schema or ""),
        "normalizer_version": str(reader or ""),
        "rulebook_hash": str(rulebook or ""),
        "dictionary_hash": str(dictionary or ""),
        "packs": ";".join(str(item.get("name", "")) for item in loaded),
    }


def version_changes(now: dict[str, str], was: dict[str, str]) -> tuple[list[dict[str, str]], str]:
    """The versions of two runs side by side, and one sentence on whether they differ.

    Said so a reader knows whether a figure moved because the pool changed or because the engine,
    its vocabulary or its rules did: the same versions on both sides leave the pool.
    """
    words = catalogue().versions
    rows = [
        {"name": name, "label": label, "now": now.get(name, ""), "was": was.get(name, "")}
        for name, label in words.items()
    ]
    differing = [row["label"] for row in rows if row["now"] != row["was"]]
    if differing:
        return rows, f"Not the same versions as the run before it: {', '.join(differing)}."
    return rows, "The same versions as the run before it: " + ", ".join(words.values()) + "."


def _seconds_said(seconds: float) -> str:
    whole = round(seconds)
    if whole < 60:
        return f"{whole:,} s"
    minutes, rest = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes:,} min {rest:,} s" if rest else f"{minutes:,} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours:,} h {minutes:,} min" if minutes else f"{hours:,} h"


def quantity_said(
    count: int | None,
    total: int | None,
    seconds: float | None,
    unit: str,
    *,
    memory: bool = False,
) -> str | None:
    """A figure's value in words: ``480 of 508 files``, ``1,200 bytes``, ``2 min 5 s``."""
    if seconds is not None:
        return _seconds_said(seconds)
    if count is None:
        return None
    if memory:
        return f"{count / MEBIBYTE:,.0f} MiB"
    if total is None:
        return f"{count:,} {unit}"
    return f"{count:,} of {total:,} {unit}"


#: The figures that are of the machine and the moment whatever state they are in.
VOLATILE_FIGURES = frozenset(
    {"cost.wall_time", "cost.dive_budget", "cost.peak_memory", "cost.quality_time"}
)

#: The figure whose count is the engine's peak memory, said in mebibytes.
MEMORY_FIGURE = "cost.peak_memory"
MEBIBYTE = 1024 * 1024


def _heading(figure: str, facet: str) -> str:
    definition = catalogue().figures[figure]
    if not facet:
        return definition.label
    return f"{definition.label} ({catalogue().facet_words(figure, facet)})"


def _value(row: QualityFigure | None) -> tuple[int | None, int | None, float | None]:
    """A counted row's count (or peak memory, in bytes), what it is out of, and its seconds."""
    if row is None or row.state is not QualityState.COUNTED:
        return None, None, None
    seconds = None if row.duration_ns is None else row.duration_ns / NS
    count = row.count if row.count is not None else row.peak_bytes
    return count, row.total, seconds


def _order(figure: str, facet: str) -> tuple[int, int, int, str]:
    figures = list(catalogue().figures)
    sections = list(catalogue().sections)
    definition = catalogue().figures[figure]
    return (sections.index(definition.section), figures.index(figure), int(facet != ""), facet)


def _sentence(
    heading: str,
    unit: str,
    now: tuple[int | None, int | None, float | None],
    was: tuple[int | None, int | None, float | None] | None,
    change: float | None,
    *,
    memory: bool = False,
) -> str:
    here = quantity_said(*now, unit, memory=memory)
    if was is None or was == (None, None, None):
        return f"{heading}: {here}."
    before = quantity_said(*was, unit, memory=memory)
    if change is None or change == 0:
        return f"{heading}: {here}, from {before}."
    if now[2] is not None:
        shown = _seconds_said(abs(change))
    elif memory:
        shown = f"{abs(change) / MEBIBYTE:,.0f} MiB"
    else:
        shown = f"{abs(round(change)):,}"
    way = "up" if change > 0 else "down"
    return f"{heading}: {here}, {way} {shown} from {before}."


def _lost_sentence(heading: str, row: QualityFigure, before: str | None) -> str:
    """A figure the compared run counted and this run's pass reaches but did not record: said,
    never dropped. (A figure the pass does not reach is no change and has no sentence.)"""
    now = "not recorded" + (f" ({row.note})" if row.note else "")
    return f"{heading}: {now}, though the run before it counted {before}."


def _volatile(here: QualityFigure, earlier: QualityFigure | None) -> bool:
    """Whether a figure is of the machine and the moment: a duration or a peak memory."""
    return (
        any(
            row is not None and (row.duration_ns is not None or row.peak_bytes is not None)
            for row in (here, earlier)
        )
        or here.figure in VOLATILE_FIGURES
    )


def _not_reached(figure: str, rows: Iterable[QualityFigure]) -> bool:
    """Whether the run's pass does not reach a figure: its phase is before the one the figure is
    known from, or the run's own row for it says not run."""
    held = [row for row in rows if row.figure == figure]
    if any(row.state is QualityState.NOT_RUN for row in held):
        return True
    phases = {row.phase for row in held if row.phase}
    return bool(phases) and not all(
        reached(catalogue().figures[figure].known_from, phase) for phase in phases
    )


def _moved(
    now: tuple[int | None, int | None, float | None],
    was: tuple[int | None, int | None, float | None],
) -> bool:
    """Whether a figure moved: its count or its duration differs, or what it is out of does while
    something is counted (400 of 500 read and then 400 of 600 is a change; 0 of 500 and 0 of 600
    says the same thing twice)."""
    if now[0] != was[0] or now[2] != was[2]:
        return True
    return now[1] != was[1] and bool(now[0])


def _whole_of(rows: Iterable[QualityFigure], figure: str) -> int | None:
    """What a figure's breakdown values are out of in one run, from its other rows: the whole a
    row states, else the count of the row that holds the whole itself."""
    held = [row for row in rows if row.figure == figure and row.state is QualityState.COUNTED]
    for row in held:
        if row.total is not None:
            return row.total
    for row in held:
        if row.facet == "" and row.count is not None:
            return row.count
    return None


def _zero_like(row: QualityFigure, whole: int | None) -> QualityFigure:
    """The same breakdown value counted as nothing, for a run that lists none of it, out of the
    whole that run's other rows of the figure state."""
    return QualityFigure(
        run_id="",
        phase=row.phase,
        figure=row.figure,
        facet=row.facet,
        state=QualityState.COUNTED,
        count=0 if row.count is not None else None,
        total=whole if row.count is not None and row.total is not None else None,
        duration_ns=0 if row.duration_ns is not None else None,
        peak_bytes=0 if row.peak_bytes is not None else None,
    )


def _counted_figures(rows: Iterable[QualityFigure]) -> set[str]:
    return {row.figure for row in rows if row.state is QualityState.COUNTED}


def compare_quality(
    now: Sequence[QualityFigure], was: Sequence[QualityFigure] | None
) -> list[QualityRow]:
    """Every figure the run states, beside the compared run's, in the catalogue's order.

    ``was`` is ``None`` where no run is compared with, and an empty sequence where the compared
    run stored none (an older engine wrote it). Either way nothing is taken as an earlier zero.
    """
    known = catalogue()
    now_rows = list(now)
    here_by = {(row.figure, row.facet): row for row in now if row.figure in known}
    was_by = {(row.figure, row.facet): row for row in (was or ()) if row.figure in known}
    counted_now, counted_was = _counted_figures(here_by.values()), _counted_figures(was_by.values())
    rows: list[QualityRow] = []
    for figure, facet in sorted(set(here_by) | set(was_by), key=lambda key: _order(*key)):
        here, earlier = here_by.get((figure, facet)), was_by.get((figure, facet))
        both = figure in counted_now and figure in counted_was
        if here is None and earlier is not None and both:
            here = _zero_like(earlier, _whole_of(here_by.values(), figure))
        if earlier is None and here is not None and both:
            earlier = _zero_like(here, _whole_of(was_by.values(), figure))
        if (
            here is None
            and earlier is not None
            and earlier.state is QualityState.COUNTED
            and figure not in counted_now
        ):
            # A breakdown that this run's pass does not reach is not run, as the figure's own row
            # says. It is not a breakdown the run failed to record, and it is no change. Only a
            # figure the pass reaches and the run left out is not recorded.
            if _not_reached(figure, now_rows):
                continue
            here = QualityFigure(
                run_id="",
                phase="",
                figure=figure,
                facet=facet,
                state=QualityState.NOT_RECORDED,
                note="this run states no value for it",
            )
        if here is None:
            continue
        definition = known.figures[figure]
        now_value = _value(here)
        had = was is not None and earlier is not None and earlier.state is QualityState.COUNTED
        was_value = _value(earlier) if had else (None, None, None)
        counted = here.state is QualityState.COUNTED
        change: float | None = None
        if counted and had:
            if now_value[2] is not None and was_value[2] is not None:
                change = now_value[2] - was_value[2]
            elif now_value[0] is not None and was_value[0] is not None:
                change = now_value[0] - was_value[0]
        heading = _heading(figure, facet)
        memory = figure == MEMORY_FIGURE
        lost = here.state is QualityState.NOT_RECORDED and had
        if counted:
            said = _sentence(
                heading,
                definition.unit,
                now_value,
                was_value if had else None,
                change,
                memory=memory,
            )
        elif lost:
            said = _lost_sentence(
                heading,
                here,
                quantity_said(*was_value, definition.unit, memory=memory),
            )
        else:
            said = ""
        rows.append(
            QualityRow(
                section=definition.section,
                figure=figure,
                facet=facet,
                label=heading,
                unit=definition.unit,
                method=definition.method,
                state=here.state.value,
                count=now_value[0],
                total=now_value[1],
                seconds=now_value[2],
                was_state=None if earlier is None else earlier.state.value,
                was_count=was_value[0],
                was_total=was_value[1],
                was_seconds=was_value[2],
                change=change,
                changed=lost or (counted and had and _moved(now_value, was_value)),
                volatile=_volatile(here, earlier),
                said=said,
                note=here.note,
                was_note="" if earlier is None else earlier.note,
            )
        )
    return rows


def sentences(rows: Iterable[QualityRow]) -> list[str]:
    """One sentence for each figure that moved from the compared run's, in reading order."""
    return [row.said for row in rows if row.changed]


def asdict(row: QualityRow) -> dict[str, Any]:
    """A row as the manifest serves it."""
    return {name: getattr(row, name) for name in QualityRow.__slots__}
