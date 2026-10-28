"""``/api/v1``: the Dashboard's versioned, typed boundary.

The older routes (``/api/...``) keep working unchanged, and a page moves to ``/api/v1`` route by
route. The ``/api/v1`` routes add a contract: every response is a declared model
(:mod:`ingence.api.v1.models`). The models are exported as JSON Schema beside the core's and
checked by a contract test. The page's TypeScript declarations are generated from them.

A route is a function ``(context, query) -> ApiModel`` in :data:`ROUTES`, keyed by the path after
``/api/v1/``. It raises :class:`ApiError` to refuse, with a status, a code and words. Anything else
it raises is a server error, sent in the same shape. Every read route serves one run: the run the
context pins (:attr:`V1Context.run_pinned`). That is the last published run, or the settled run
while a job that will publish a newer run is running. A response never mixes two runs.
"""

from __future__ import annotations

import datetime as dt
import importlib
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from ingence import SCHEMA_VERSION, __version__
from ingence.api.v1.models import ApiModel, ErrorDetail, ErrorResponse, VersionResponse
from ingence.core.provenance import engine_commit
from ingence.storage.commit import read_pointer

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

__all__ = [
    "DECLARED",
    "PREFIX",
    "ROUTES",
    "ROUTE_MODULES",
    "ApiError",
    "V1Context",
    "context_for",
    "dispatch",
    "load_routes",
    "route",
]

PREFIX = "/api/v1/"


class ApiError(Exception):
    """A refusal a route states: a status, a code a program branches on, and words."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "bad_parameter",
        status: HTTPStatus = HTTPStatus.BAD_REQUEST,
        parameter: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = ErrorDetail(code=code, message=message, parameter=parameter)


@dataclass(frozen=True, slots=True)
class V1Context:
    """What a route may read: the project, and which run every read serves."""

    project: Path
    #: The run the pointer names now.
    run_current: str | None
    #: The run every read route serves: ``run_current``, or the settled run while a job runs.
    run_pinned: str | None
    #: Whether a job that will publish a newer run is queued or running.
    job_running: bool


type Route = Callable[[V1Context, Mapping[str, Sequence[str]]], ApiModel]

#: Every ``/api/v1`` route, by its path after the prefix.
ROUTES: dict[str, Route] = {}
#: The response model each route declares. A route answering anything else is a server error,
#: so a response cannot drift from the contract its exported schema publishes.
DECLARED: dict[str, type[ApiModel]] = {}

#: The modules that hold ``/api/v1`` routes. They are imported once before the first request and by
#: the schema export, so each registers its routes and its response models. To remove a route
#: module, delete it and its line here.
ROUTE_MODULES: tuple[str, ...] = (
    "ingence.api.v1.jobs",
    "ingence.api.v1.restore",
    "ingence.api.v1.question_file",
    # The graph, and the routes used most.
    "ingence.api.v1.graph",
    "ingence.api.v1.entries",
    "ingence.api.v1.search",
    "ingence.api.v1.review",
    # A word's meaning and its correction, and group resolutions.
    "ingence.api.v1.words",
    "ingence.api.v1.groups",
    # The Graph workspace's Wheel, Lineage and Timeline.
    "ingence.api.v1.explorer.wheel",
    "ingence.api.v1.explorer.items",
    "ingence.api.v1.explorer.lineage",
    # The lineage with name families collapsed, and its quotient graph.
    "ingence.api.v1.explorer.families",
    "ingence.api.v1.explorer.timeline",
    "ingence.api.v1.explorer.labels",
    "ingence.api.v1.explorer.axes",
    "ingence.api.v1.explorer.overlay",
    "ingence.api.v1.explorer.plan",
    # Completeness per experiment, and "no analysis found".
    "ingence.api.v1.completeness",
    # One experiment's files, each with what the engine found about it.
    "ingence.api.v1.experiment_files",
    # The question sheet, as the engine's own output.
    "ingence.api.v1.question_sheet",
    # Every question of the sheet and the queue, ranked and grouped.
    "ingence.api.v1.questions_ranked",
    # A claim's class handle, answered on the class.
    "ingence.api.v1.claim_class",
    # A run's manifest: what changed from its parent run, in sentences.
    "ingence.api.v1.manifest",
    # What experiments are about (themes), over time, and who else works on them.
    "ingence.api.v1.themes",
    # The design grid of each experiment, from its files' names.
    "ingence.api.v1.design",
    # A table's columns, and the tables with the same columns.
    "ingence.api.v1.columns",
    # The levels a project's rows climb, and the word for each.
    "ingence.api.v1.ladder",
    # The frame's own facts: the project's name, whether a root can be reached, and what a
    # problem report states.
    "ingence.api.v1.shell",
    # What the Copy buttons of a rule's card and a word's card copy.
    "ingence.api.v1.copy_text",
    # The experiments the engine found, how complete each is, and how its files were tied to it
    # (Explore > Experiments).
    "ingence.api.v1.experiments",
    # The words to teach, most files first.
    "ingence.api.v1.words_queue",
    # A person's topics and what each comes to in the run.
    "ingence.api.v1.topics",
    # One topic's files as CSV text, for the page to save.
    "ingence.api.v1.topic_export",
    # The overview, assumptions, checks, overlap, an experiment's story, workflows, Ask, areas and
    # goals. Their invented examples, in ``api/v1/examples.py``, stay for the tests.
    "ingence.api.v1.overview",
    "ingence.api.v1.assumptions",
    "ingence.api.v1.checks",
    "ingence.api.v1.overlap",
    "ingence.api.v1.experiment_story",
    "ingence.api.v1.workflows",
    "ingence.api.v1.ask",
    "ingence.api.v1.areas",
    "ingence.api.v1.goals",
)


def load_routes() -> None:
    """Import every module in :data:`ROUTE_MODULES`, registering its routes and models."""
    for name in ROUTE_MODULES:
        importlib.import_module(name)


def route(path: str, *, model: type[ApiModel]) -> Callable[[Route], Route]:
    """Register a function as the ``/api/v1/<path>`` route answering ``model`` (a decorator)."""

    def add(function: Route) -> Route:
        if path in ROUTES:
            message = f"two /api/v1 routes at {path}"
            raise ValueError(message)
        ROUTES[path] = function
        DECLARED[path] = model
        return function

    return add


def context_for(project: Path, *, settled: str | None, job_running: bool) -> V1Context:
    """The context of one request: the current run, and the run reads are pinned to."""
    pointer = read_pointer(project)
    current = pointer.run_id if pointer is not None else None
    return V1Context(
        project=project,
        run_current=current,
        run_pinned=settled or current,
        job_running=job_running,
    )


def dispatch(
    context: V1Context, path: str, query: Mapping[str, Sequence[str]]
) -> tuple[HTTPStatus, dict[str, Any]]:
    """Answer one ``/api/v1`` request: a status and the response model's JSON."""
    load_routes()
    handler = ROUTES.get(path.strip("/"))
    if handler is None:
        refusal = ErrorResponse(
            error=ErrorDetail(code="not_found", message=f"nothing at {PREFIX}{path.strip('/')}")
        )
        return HTTPStatus.NOT_FOUND, refusal.model_dump(mode="json")
    try:
        answered = handler(context, query)
        declared = DECLARED[path.strip("/")]
        if not isinstance(answered, declared):
            message = (
                f"{PREFIX}{path.strip('/')} declares {declared.__name__} and answered "
                f"{type(answered).__name__}"
            )
            raise TypeError(message)
    except ApiError as error:
        return error.status, ErrorResponse(error=error.detail).model_dump(mode="json")
    except Exception as error:  # An unexpected error is sent in the error shape, not dropped.
        detail = ErrorDetail(code="server_error", message=f"{type(error).__name__}: {error}")
        return HTTPStatus.INTERNAL_SERVER_ERROR, ErrorResponse(error=detail).model_dump(mode="json")
    # By alias: one evidence field is spelled ``from`` in the contract, a Python keyword.
    return HTTPStatus.OK, answered.model_dump(mode="json", by_alias=True)


def now() -> str:
    """This moment as an ISO 8601 string with its zone, UTC."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@route("version", model=VersionResponse)
def version(context: V1Context, query: Mapping[str, Sequence[str]]) -> VersionResponse:
    """The handshake: the API's version, the schema, the engine and the run reads serve."""
    del query
    return VersionResponse(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        engine_commit=engine_commit(),
        run_pinned=context.run_pinned,
        run_current=context.run_current,
        job_running=context.job_running,
        answered_at=now(),
    )
