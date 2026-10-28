"""Topics: what a person's named sets of groupings come to in a run.

A topic is a person's name for several of the engine's groupings taken together: experiments,
lattice cores (groups of shared words) and lines of work (the themes' lines), with the files and
folders a person's "group" always held. It is built on the person's groups, not beside them. It
uses the same Record target (``overlay:group:<id>``, ``api/routes/overlay``), read under the word
a person uses.

A topic is an annotation and a projection, never a claim. It changes what the views show and
never a claim's standing. Nothing in inference, the review, the sheets or the themes reads a
topic. What its members resolve to in a run is computed here when read, from the published run and
the Record as it stands, and never stored (the experiments' counts and the lines of work it reads
are held in memory per run, as the themes projection is). Removing the component means deleting
this package, its routes module's line in ``api/v1/__init__.py`` and the manifest's reader line.

The topics are an optional component. The engine reaches them only through
:data:`TOPIC_ENGINES`. The functions another part of the engine calls are :func:`resolve`
and :func:`topic_drift`.

Read ``reading`` for the members, ``model`` for what is returned and ``runview`` for what is read
from a run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ingence.core.registry import Registry
from ingence.topics.model import (
    Change,
    Drift,
    ExperimentInfo,
    MemberReading,
    Overlap,
    Suggestion,
    TopicReading,
    TopicResolution,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.topics.runview import RunView

__all__ = [
    "TOPIC_ENGINES",
    "Change",
    "Drift",
    "ExperimentInfo",
    "MemberReading",
    "NoWatermarkError",
    "Overlap",
    "Suggestion",
    "TopicEngine",
    "TopicReading",
    "TopicResolution",
    "resolve",
    "topic_drift",
    "topics_engine",
]

#: The engine registered by default.
DEFAULT_ENGINE = "record_projection"
#: Characters of a broad line's words listed beside a suggestion (display length).
SUGGESTED_WORDS = 6


class NoWatermarkError(LookupError):
    """A run states no Record watermark, so the topics it was made with are not known."""


class TopicEngine(Protocol):
    """Reads a person's topics against a published run."""

    version: str

    def resolve(
        self,
        project: Path,
        run_id: str,
        *,
        compared: str | None = None,
        suggestions: bool = True,
        up_to: int | None = None,
    ) -> TopicResolution:
        """Every topic against the run, with drift against ``compared`` (a run identifier).
        ``up_to`` reads the topics as the Record stood at a watermark, else as it stands."""
        ...


#: Every topic engine, reachable only through here.
TOPIC_ENGINES: Registry[TopicEngine] = Registry("topic_engine")


def _moves_back(moved: dict[str, str]) -> dict[str, tuple[str, ...]]:
    """Each entry now to the identifiers it had before a move, oldest name first."""
    back: dict[str, list[str]] = {}
    for old, new in sorted(moved.items()):
        back.setdefault(new, []).append(old)
    return {new: tuple(olds) for new, olds in back.items()}


class _RecordProjection:
    """Topics from the Record's groups, resolved against the run's experiments, the lattice's
    terms and the themes' lines."""

    version = "1"

    def resolve(
        self,
        project: Path,
        run_id: str,
        *,
        compared: str | None = None,
        suggestions: bool = True,
        up_to: int | None = None,
    ) -> TopicResolution:
        from ingence.api.routes.overlay import topics_standing
        from ingence.record.log import moved_entries
        from ingence.topics.reading import overlaps, read_topic
        from ingence.topics.runview import load_view

        topics = topics_standing(project, up_to=up_to)
        kinds = {member.kind for topic in topics for member in topic.members}
        entries = sorted({m.entry for topic in topics for m in topic.members if m.entry})
        view = load_view(
            project,
            run_id,
            entries=entries,
            cores="core" in kinds,
            lines=suggestions or "line" in kinds,
            goals="goal" in kinds,
        )
        earlier = None
        drift_why: str | None = None
        earlier_base = None
        if topics:
            if compared is None:
                drift_why = "This is the first scan: there is nothing before it to compare."
            else:
                try:
                    moved = moved_entries(project)
                    before = load_view(
                        project,
                        compared,
                        entries=entries,
                        cores="core" in kinds,
                        lines="line" in kinds,
                        goals="goal" in kinds,
                        earlier_ids=_moves_back(moved),
                        moved=moved,
                    )
                except Exception:  # a run removed or unreadable: said, never a crash
                    before = None
                    drift_why = "The scan it is compared with can no longer be read."
                if before is not None and not before.base.rules_applied:
                    # A census applied no rules: it holds no experiments yet, which is unknown.
                    drift_why = (
                        "The scan it is compared with applied no rules, so it holds no "
                        "experiments to compare."
                    )
                elif before is not None:
                    earlier = (before, compared)
                    earlier_base = before.base
        readings = [read_topic(topic, view, earlier=earlier) for topic in topics]
        held = {reading.topic.topic_id: reading.experiments for reading in readings}
        names = {reading.topic.topic_id: reading.topic.name for reading in readings}
        overlapping = overlaps(held, names)
        everywhere: dict[str, int] = {}
        for experiments in held.values():
            for experiment in experiments:
                everywhere[experiment] = everywhere.get(experiment, 0) + 1
        final = tuple(
            _with_overlap(
                reading,
                overlapping.get(reading.topic.topic_id, []),
                sum(1 for e in reading.experiments if everywhere[e] > 1),
            )
            for reading in readings
        )
        suggested, suggestions_why = _suggest(view, held) if suggestions else ((), None)
        mentioned: set[str] = set()
        for reading in final:
            mentioned.update(reading.experiments)
            if reading.drift is not None:
                mentioned.update(change.experiment for change in reading.drift.left)
                mentioned.update(change.experiment for change in reading.drift.joined)
        for suggestion in suggested:
            mentioned.update(suggestion.experiments)
        infos: dict[str, ExperimentInfo] = {}
        for experiment in sorted(mentioned):
            if experiment in view.base.experiments:
                infos[experiment] = ExperimentInfo(
                    experiment=experiment,
                    folder=view.base.experiments[experiment],
                    files=view.base.files.get(experiment, 0),
                )
            elif earlier_base is not None and experiment in earlier_base.experiments:
                infos[experiment] = ExperimentInfo(
                    experiment=experiment,
                    folder=earlier_base.experiments[experiment],
                    files=None,
                    in_run=False,
                )
        return TopicResolution(
            run_id=run_id,
            compared_run_id=compared if earlier is not None else None,
            drift_why=drift_why,
            topics=final,
            experiments=infos,
            run_experiments=len(view.base.experiments) if view.base.rules_applied else None,
            suggestions=suggested,
            suggestions_why=suggestions_why,
        )


def _with_overlap(reading: TopicReading, overlapped: list[Overlap], shared: int) -> TopicReading:
    from dataclasses import replace

    return replace(reading, overlaps=tuple(overlapped), shared=shared)


def _suggest(
    view: RunView, held: dict[str, tuple[str, ...]]
) -> tuple[tuple[Suggestion, ...], str | None]:
    """The run's broad lines offered as places to start a topic: never made by themselves."""
    if view.lines is None:
        return (), view.lines_why or "The themes were not read."
    taken = {experiment for experiments in held.values() for experiment in experiments}
    found = tuple(
        Suggestion(
            broad=broad.broad,
            label=broad.label,
            words=broad.words[:SUGGESTED_WORDS],
            experiments=tuple(sorted(broad.experiments)),
            in_topics=len(broad.experiments & taken),
        )
        for broad in view.lines.broad
        if broad.experiments
    )
    return found, None


@TOPIC_ENGINES.register(
    DEFAULT_ENGINE,
    version="1",
    summary="A person's topics resolved against a run's experiments, cores and lines of work",
)
def _build_record_projection() -> TopicEngine:
    return _RecordProjection()


def topics_engine(name: str = DEFAULT_ENGINE) -> TopicEngine:
    """The topic engine of that name. Raises ``ComponentNotAvailableError`` saying why not."""
    return TOPIC_ENGINES.get(name)


def resolve(
    project: Path,
    run_id: str,
    *,
    compared: str | None = None,
    suggestions: bool = True,
    up_to: int | None = None,
) -> TopicResolution:
    """Every topic read against a published run (``run_id``), with drift against ``compared``
    (a run identifier; none reads no drift). Computed when read, never stored, never a claim."""
    return topics_engine().resolve(
        project, run_id, compared=compared, suggestions=suggestions, up_to=up_to
    )


def topic_drift(project: Path, run_id: str, compared: str | None) -> list[dict[str, Any]]:
    """What the run's manifest says of each topic: ``[{topic_id, name, experiments, complete,
    compared, joined, left, unread, not_computed, said}]``, ``joined`` and ``left`` listing
    ``{experiment, folder, reasons}`` and ``said`` the sentences.

    Raises ``ComponentNotAvailableError`` where the topics component is disabled, and
    ``NoWatermarkError`` where the run states no Record watermark (a manifest then says topics were
    not computed: the topics it was made with are not known)."""
    from ingence.storage.catalog import open_catalog
    from ingence.topics.said import drift_said

    # The topics as that run read the Record (its watermark): a manifest says what the topics
    # did in the run, not what a person made after it.
    with open_catalog(project, run_id=run_id) as catalog:
        stated = catalog.query("SELECT record_watermark FROM run LIMIT 1").fetchone()
    watermark = None if stated is None or stated[0] is None else int(stated[0])
    if watermark is None:
        message = (
            "this run states no Record watermark, so the topics it was made with are not known"
        )
        raise NoWatermarkError(message)
    found = resolve(project, run_id, compared=compared, suggestions=False, up_to=watermark)

    def listed(changes: tuple[Change, ...]) -> list[dict[str, Any]]:
        return [
            {
                "experiment": change.experiment,
                "folder": found.experiments[change.experiment].folder
                if change.experiment in found.experiments
                else "",
                "reasons": list(change.reasons),
            }
            for change in changes
        ]

    rows: list[dict[str, Any]] = []
    for reading in found.topics:
        drift = reading.drift
        rows.append(
            {
                "topic_id": reading.topic.topic_id,
                "name": reading.topic.name,
                # None where no member was read (a census): unknown, never 0.
                "experiments": len(reading.experiments) if reading.read else None,
                "complete": reading.complete,
                "compared": drift is not None,
                "joined": listed(drift.joined) if drift else [],
                "left": listed(drift.left) if drift else [],
                "unread": list(drift.unread) if drift else [],
                "not_computed": list(reading.not_computed),
                "said": drift_said(reading),
            }
        )
    return rows
