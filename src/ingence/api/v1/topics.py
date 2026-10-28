"""``GET /api/v1/topics``: a person's topics and what each comes to in the run.

A topic is a person's name for several of the engine's groupings taken together: experiments,
lattice cores (groups of shared words), lines of work, and the files and folders a person's
"group" always held. It is the same statement as a group in the Record (``overlay:group:<id>``).
A person writes and edits it through the overlay (``POST /api/review/answer`` with ``overlay``
edits), and this route reads it.

The route reads what the topics' members resolve to in the run read (``ingence.topics``): each
topic's experiments and files, the members that resolve to nothing (listed, never dropped), the
experiments two topics share, and what drifted since the run before (an experiment that came to
carry a core's words, a line whose experiments changed), each with its reason.

A topic is an annotation and a projection, never a claim. It changes what the views show, never a
claim's standing, and nothing in inference reads it. Nothing is written here.

Parameters: ``run`` (a published run), ``topic`` (one topic by identifier), ``limit`` and
``offset`` (page each topic's experiments, and the files with ``files=1``; 100 by default, 0
leaves only the identifiers), ``files`` (with ``topic``: that topic's files, a page), ``compare``
(a run to read drift against; ``none`` reads no drift), ``suggestions`` (``0`` leaves out the broad
lines offered as places to start; reading them reads the themes).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import boolean, integer, one, run_named

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.topics.model import MemberReading, TopicReading

__all__ = ["TopicsResponse"]

#: Experiments (and files) one topic lists at most unless ``limit`` says otherwise.
PAGE = 100
PAGE_LIMIT = 5000


class TopicLineNow(ApiModel):
    """A line of work as the run reads it, found by the experiments it was added by."""

    label: str
    words: list[str]
    #: The words it was added with.
    words_added_with: list[str]
    #: True where its words are not the words it was added with: the line has drifted, though it
    #: is the same line by its experiments.
    drifted: bool
    #: How many of the experiments it was added by the line holds now, of how many.
    kept: Count
    added_by: Count


class TopicMemberItem(ApiModel):
    """One member of a topic and what it resolves to in the run."""

    #: Its identifier within the topic: an entry's own, ``group:<words>`` for a core,
    #: ``line:<digest>`` for a line.
    key: str
    kind: Literal["entry", "experiment", "core", "line", "goal"]
    #: The name of a file, folder or experiment, the words of a core, or the label or words of a
    #: line.
    name: str
    path: str | None
    #: A core's words.
    terms: list[str]
    #: ``resolved``, ``nothing`` (the run holds no experiment it names, ``why`` says it) or
    #: ``not_computed`` (the reading it needs is not available, ``why`` says it).
    state: Literal["resolved", "nothing", "not_computed"]
    #: Whether the run holds the file, folder or experiment (false: moved away or gone).
    in_run: bool
    #: Experiments it resolves to. It is None where it could not be read, never 0.
    experiments: Count | None
    experiment_ids: list[Identifier]
    why: str | None
    line: TopicLineNow | None


class TopicExperimentItem(ApiModel):
    id: Identifier
    #: The experiment's folder below its root.
    folder: str
    #: Files whose highest experiment it is. It is None for an experiment this run no longer holds.
    files: Count | None
    in_run: bool
    #: The members of the topic that resolve to it, by key.
    through: list[str]


class TopicFileItem(ApiModel):
    id: Identifier
    name: str
    path: str
    experiment: Identifier


class TopicOverlapItem(ApiModel):
    topic: Identifier
    name: str
    #: Experiments both topics hold, and which.
    experiments: Count
    experiment_ids: list[Identifier]


class TopicChangeItem(ApiModel):
    experiment: Identifier
    folder: str
    #: Why, in words (the member that explains it).
    reasons: list[str]


class TopicDriftItem(ApiModel):
    """What changed in a topic between the compared run and this one, read the same way in both."""

    since_run_id: Identifier
    joined: list[TopicChangeItem]
    left: list[TopicChangeItem]
    #: Members that could not be read in one of the two runs, by key.
    unread: list[str]
    #: The changes in sentences.
    said: list[str]


class TopicItem(ApiModel):
    id: Identifier
    #: The Record target it is written on (``overlay:group:<id>``).
    target: str
    name: str
    note: str | None
    made_by: str
    made_at: str
    edited_by: str
    edited_at: str
    #: Earlier statements on the target (edits, a withdrawal and its undoing).
    earlier: Count
    members: list[TopicMemberItem]
    #: Experiments the topic holds in the run (the union of its members'), and out of how many.
    #: None where no member was read (a run that applied no rules, a reading not available): the
    #: topic's experiments are then unknown, never 0, and ``said`` says it was not read.
    experiments_count: Count | None
    experiments_out_of: Count | None
    experiment_ids: list[Identifier]
    #: A page of them (``limit``, ``offset``), and how many are beyond it.
    experiments: list[TopicExperimentItem]
    experiments_not_shown: Count
    #: Files of those experiments (an experiment's files are the files whose highest experiment it
    #: is): the same rule the Years view and completeness count by.
    files_count: Count | None
    #: False where a member could not be read: the counts are then the readable members' at least.
    complete: bool
    #: Members that resolve to nothing, and members that could not be read, by key.
    nothing: list[str]
    not_computed: list[str]
    overlaps: list[TopicOverlapItem]
    #: Of the topic's experiments, how many another topic holds too.
    shared_experiments: Count
    #: None where there is no earlier run to compare with (``drift_why`` says).
    drift: TopicDriftItem | None
    said: str


class SuggestionItem(ApiModel):
    """A broad line of work offered as a place to start a topic. Never made by itself."""

    broad: Count
    label: str
    words: list[str]
    experiment_ids: list[Identifier]
    experiments: Count
    #: How many of its experiments a topic already holds.
    in_topics: Count
    said: str


@register("topics")
class TopicsResponse(ApiModel):
    """``GET /api/v1/topics``: every topic, what its members resolve to, and what drifted."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    #: Every experiment of the run: what each topic's count is out of. None where the run applied
    #: no rules (a census holds no experiments yet): unknown, never 0.
    run_experiments: Count | None
    #: The run drift is read against, and why there is none, in words.
    compared_run_id: Identifier | None
    drift_why: str | None
    topics: list[TopicItem]
    #: With ``files=1`` and ``topic=``: a page of that topic's files, and how many are beyond it.
    files_of: Identifier | None = None
    files: list[TopicFileItem] = Field(default_factory=list)
    files_not_shown: Count = 0
    suggestions: list[SuggestionItem]
    #: Why there are no suggestions, in words, where the lines of work could not be read.
    suggestions_why: str | None


def _member_name(member: Any, reading: MemberReading) -> str:
    if member.kind == "core":
        return " ".join(member.terms)
    if member.kind == "goal":
        return str(member.goal_id or member.key)
    if member.kind == "line":
        return member.label or " · ".join(member.words) or "a line of work"
    return reading.name or ""


def _line_item(member: Any, reading: MemberReading) -> TopicLineNow | None:
    if member.kind != "line" or reading.state != "resolved" or reading.line_now is None:
        return None
    return TopicLineNow(
        label=reading.line_now,
        words=list(reading.line_words_now),
        words_added_with=list(member.words),
        drifted=reading.line_drifted,
        kept=reading.line_kept or 0,
        added_by=reading.line_added_by or 0,
    )


def _topic_item(
    reading: TopicReading,
    infos: Mapping[str, Any],
    *,
    run_experiments: int | None,
    limit: int,
    offset: int,
) -> TopicItem:
    from ingence.topics.said import drift_said, topic_said

    topic = reading.topic
    by_key = {member.key: member for member in topic.members}
    through: dict[str, list[str]] = {}
    for member_reading in reading.members:
        for experiment in member_reading.experiments:
            through.setdefault(experiment, []).append(member_reading.key)
    shown = reading.experiments[offset : offset + limit]
    drift = None
    if reading.drift is not None:

        def changes(changed: Sequence[Any]) -> list[TopicChangeItem]:
            return [
                TopicChangeItem(
                    experiment=change.experiment,
                    folder=infos[change.experiment].folder if change.experiment in infos else "",
                    reasons=list(change.reasons),
                )
                for change in changed
            ]

        drift = TopicDriftItem(
            since_run_id=reading.drift.since_run_id,
            joined=changes(reading.drift.joined),
            left=changes(reading.drift.left),
            unread=list(reading.drift.unread),
            said=drift_said(reading),
        )
    return TopicItem(
        id=topic.topic_id,
        target=topic.target,
        name=topic.name,
        note=topic.note,
        made_by=topic.made_by,
        made_at=topic.made_at,
        edited_by=topic.edited_by,
        edited_at=topic.edited_at,
        earlier=topic.earlier,
        members=[
            TopicMemberItem(
                key=member_reading.key,
                kind=member_reading.kind,
                name=_member_name(by_key[member_reading.key], member_reading),
                path=member_reading.path,
                terms=list(by_key[member_reading.key].terms),
                state=member_reading.state,
                in_run=member_reading.in_run,
                experiments=None
                if member_reading.state == "not_computed"
                else len(member_reading.experiments),
                experiment_ids=list(member_reading.experiments),
                why=member_reading.why,
                line=_line_item(by_key[member_reading.key], member_reading),
            )
            for member_reading in reading.members
        ],
        experiments_count=len(reading.experiments) if reading.read else None,
        experiments_out_of=run_experiments if reading.read else None,
        experiment_ids=list(reading.experiments),
        experiments=[
            TopicExperimentItem(
                id=experiment,
                folder=infos[experiment].folder,
                files=infos[experiment].files,
                in_run=infos[experiment].in_run,
                through=through.get(experiment, []),
            )
            for experiment in shown
            if experiment in infos
        ],
        experiments_not_shown=max(0, len(reading.experiments) - offset - len(shown)),
        files_count=reading.files if reading.read else None,
        complete=reading.complete,
        nothing=list(reading.nothing),
        not_computed=list(reading.not_computed),
        overlaps=[
            TopicOverlapItem(
                topic=overlap.topic_id,
                name=overlap.name,
                experiments=len(overlap.experiments),
                experiment_ids=list(overlap.experiments),
            )
            for overlap in reading.overlaps
        ],
        shared_experiments=reading.shared,
        drift=drift,
        said=topic_said(reading, run_experiments),
    )


def _check_run(context: V1Context, named: str) -> None:
    """A run named by ``compare`` must be a published run of this project."""
    from ingence.api.v1.shaping import _RUN_SPELLING
    from ingence.storage.commit import manifest_path

    if not _RUN_SPELLING.match(named) or not manifest_path(context.project, named).is_file():
        message = f"no published run of this project is called {named}"
        raise ApiError(
            message, code="unknown_run", status=HTTPStatus.NOT_FOUND, parameter="compare"
        )


def compared_run_of(project: Any, run_id: str) -> str | None:
    """The run drift is read against: the latest earlier run of the run's own kind, else the run
    it was built from (the run manifest's rule, ``api/routes/changes``)."""
    from ingence.api.routes.changes import _compared_with, runs

    listed = runs(project)["runs"]
    this = next((run for run in listed if run["run_id"] == run_id), None)
    return _compared_with(listed, this) if this is not None else None


@route("topics", model=TopicsResponse)
def topics(context: V1Context, query: Mapping[str, Sequence[str]]) -> TopicsResponse:
    """Every topic against the pinned run; drift against the run before; suggestions."""
    from ingence.core.registry import ComponentNotAvailableError
    from ingence.topics import topics_engine
    from ingence.topics.runview import files_of, load_view

    run_id, _ = run_named(context, query)
    if run_id is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    limit = integer(query, "limit", default=PAGE, low=0, high=PAGE_LIMIT)
    offset = integer(query, "offset", default=0, low=0, high=10**9)
    wanted = (one(query, "topic") or "").strip() or None
    want_files = boolean(query, "files")
    if want_files and wanted is None:
        message = "files=1 names the topic whose files are listed (topic=)"
        raise ApiError(message, parameter="topic")
    suggestions = (one(query, "suggestions") or "1").strip() not in ("0", "false", "no")
    compare = (one(query, "compare") or "").strip()
    if compare and compare != "none":
        _check_run(context, compare)
    try:
        engine = topics_engine()
    except ComponentNotAvailableError as error:
        raise ApiError(
            str(error), code="component_unavailable", status=HTTPStatus.SERVICE_UNAVAILABLE
        ) from error
    # The run before is looked for only where a topic stands to be compared (it opens every run).
    from ingence.api.routes.overlay import topics_standing

    compared = (
        None
        if compare == "none" or not topics_standing(context.project)
        else (compare or compared_run_of(context.project, run_id))
    )
    found = engine.resolve(context.project, run_id, compared=compared, suggestions=suggestions)
    if wanted is not None and all(reading.topic.topic_id != wanted for reading in found.topics):
        message = f"no topic {wanted} stands in this project"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="topic")
    chosen = [
        reading for reading in found.topics if wanted is None or reading.topic.topic_id == wanted
    ]
    listed: list[TopicFileItem] = []
    beyond = 0
    if want_files and chosen:
        held = chosen[0].experiments
        view = load_view(context.project, run_id)
        counted = chosen[0].files
        rows = files_of(context.project, view, held, limit=limit, offset=offset)
        listed = [
            TopicFileItem(
                id=row["entry_id"], name=row["name"], path=row["path"], experiment=row["experiment"]
            )
            for row in rows
        ]
        beyond = max(0, counted - offset - len(listed))
    from ingence.topics.said import plural

    return TopicsResponse(
        schema_version=SCHEMA_VERSION,
        run_id=found.run_id,
        run_experiments=found.run_experiments,
        compared_run_id=found.compared_run_id,
        drift_why=found.drift_why,
        topics=[
            _topic_item(
                reading,
                found.experiments,
                run_experiments=found.run_experiments,
                limit=limit,
                offset=offset,
            )
            for reading in chosen
        ],
        files_of=chosen[0].topic.topic_id if want_files and chosen else None,
        files=listed,
        files_not_shown=beyond,
        suggestions=[
            SuggestionItem(
                broad=suggestion.broad,
                label=suggestion.label,
                words=list(suggestion.words),
                experiment_ids=list(suggestion.experiments),
                experiments=len(suggestion.experiments),
                in_topics=suggestion.in_topics,
                said=(
                    f"{plural(len(suggestion.experiments), 'experiment')}; "
                    f"{suggestion.in_topics:,} already in a topic."
                ),
            )
            for suggestion in found.suggestions
        ],
        suggestions_why=found.suggestions_why,
    )
