"""The response-model convention of ``/api/v1``.

Every ``/api/v1`` route answers with one of these models. Each model's JSON Schema is exported
beside the core's (``schemas/api/v1/``, by ``tools/export_schemas.py``), and the Dashboard's
TypeScript declarations are generated from it. Three checks hold the contract. A response that
drifts from its model fails a contract test. A model that drifts from its export fails the export
check. A page that assumes a field the declarations do not hold fails ``tsc``.

The conventions, each held by a type below rather than by care:

- Identifiers are strings. An entry, a run, an edge or an event is named by a string; a
  number that names something is sent as a string, because JavaScript's numbers lose integers
  above 2**53 and an identifier that silently changes is worse than none (:data:`Identifier`).
- Counts are integers a browser can hold exactly (:data:`Count`): non-negative and at most
  2**53 - 1, refused at the server otherwise.
- Times are ISO 8601 strings with their zone (:data:`ZonedTime`): ``2026-09-28T21:30:00Z``
  or ``...-04:00``, never a bare local time and never epoch nanoseconds, which exceed a browser's
  integers.
- A model forbids fields it does not declare, so a route cannot quietly send more than its
  contract says.
- An error has one shape (:class:`ErrorResponse`): a code a program can branch on and words
  a person can read.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

__all__ = [
    "API_VERSION",
    "RESPONSE_MODELS",
    "ApiModel",
    "AreaKindName",
    "AssumptionKindName",
    "Count",
    "ErrorDetail",
    "ErrorResponse",
    "Identifier",
    "JobPhaseName",
    "MatchTierName",
    "OverlapEdgeKindName",
    "VersionResponse",
    "ZonedTime",
    "register",
]

#: The API's own version, apart from the schema's and the package's. A field added compatibly
#: moves the minor part; anything a client relies on changing moves the major part and the path.
API_VERSION = "1.0"

#: The largest integer a browser's number holds exactly.
MAX_SAFE_INTEGER = 2**53 - 1


def _zoned(value: str) -> str:
    """Accept an ISO 8601 time only where it states its zone."""
    text = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        message = f"{value!r} is not an ISO 8601 time"
        raise ValueError(message) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        message = f"{value!r} states no zone; every time the API sends carries one"
        raise ValueError(message)
    return text


def _identifier(value: str) -> str:
    if not value:
        message = "an identifier is a non-empty string"
        raise ValueError(message)
    return value


#: A name for something: always a string, never a number.
Identifier = Annotated[str, AfterValidator(_identifier)]
#: A count a browser holds exactly.
Count = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
#: A moment, as ISO 8601 with its zone.
ZonedTime = Annotated[str, AfterValidator(_zoned)]


# The closed vocabularies of the contract. Each is the values of an enum in ``core/schema.py`` or
# ``jobs/__init__.py`` spelled as a ``Literal``, so the exported schema and the page's declarations
# close over them. A test holds the two spellings equal.

#: What an area's folder stands for (``AreaKind``).
AreaKindName = Literal["person", "instrument", "date", "project", "folder"]
#: What kind of thing the engine assumed without asking (``AssumptionKind``).
AssumptionKindName = Literal[
    "file_type", "writer", "domain", "spelling", "area_kind", "workflow", "stage", "repeat"
]
#: How well an experiment matches a question or a goal: named by its words, associated with
#: them, or in the same theme as one that is.
MatchTierName = Literal["named", "associated", "same_theme"]
#: Why two experiments overlap.
OverlapEdgeKindName = Literal[
    "shared_words", "workflow", "instrument", "repeat_of", "copy", "made_from"
]
#: Where a job is, in the contract's words.
JobPhaseName = Literal["listing", "reading", "names", "rules", "views"]


class ApiModel(BaseModel):
    """The base of every ``/api/v1`` response: frozen, and refusing undeclared fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ErrorDetail(ApiModel):
    #: A code a program branches on: ``bad_parameter``, ``not_found``, ``no_published_run``,
    #: ``server_error`` and the like.
    code: str
    #: Words a person reads.
    message: str
    #: The parameter at fault, where one was.
    parameter: str | None = None


class ErrorResponse(ApiModel):
    """Every ``/api/v1`` refusal, whatever its status."""

    api_version: Literal["1.0"] = "1.0"
    error: ErrorDetail


class VersionResponse(ApiModel):
    """``GET /api/v1/version``: the handshake a page makes before anything else.

    A page built for another API version, or a schema it was not built for, can say so rather
    than misreading what it is sent.
    """

    api_version: Literal["1.0"] = "1.0"
    #: The record contract the engine writes (``SCHEMA_VERSION``).
    schema_version: str
    #: The package version.
    engine_version: str
    #: The engine's commit, where it can be read (``core/provenance.py``).
    engine_commit: str | None
    #: The run every read route serves: the last published run, pinned while a job runs.
    run_pinned: Identifier | None
    #: The run the project's pointer names now. Differs from ``run_pinned`` only while a job
    #: that will publish a newer run is running.
    run_current: Identifier | None
    #: Whether a job that will publish a newer run is queued or running.
    job_running: bool
    #: When the server answered.
    answered_at: ZonedTime


#: Every response model of ``/api/v1``, by the name its exported schema takes. A route's model
#: is added here with :func:`register`; the export and the contract test read this.
RESPONSE_MODELS: dict[str, type[ApiModel]] = {}


def register[ModelType: type[ApiModel]](name: str) -> Callable[[ModelType], ModelType]:
    """Add a response model to :data:`RESPONSE_MODELS` under ``name`` (a class decorator)."""

    def add(model: ModelType) -> ModelType:
        if name in RESPONSE_MODELS and RESPONSE_MODELS[name] is not model:
            message = f"two /api/v1 response models are named {name}"
            raise ValueError(message)
        RESPONSE_MODELS[name] = model
        return model

    return add


RESPONSE_MODELS["error"] = ErrorResponse
RESPONSE_MODELS["version"] = VersionResponse
