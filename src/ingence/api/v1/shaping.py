"""What every typed ``/api/v1`` route shares: reading a request, the pinned catalog, safe values.

The routes used most (search, folder, entry, questions, runs, workspace, rules) answer from the
same functions the older routes call (``api/routes/*.py``). Each shapes that result into its
declared model field by field. A top-level field that the older route adds later does not reach
``/api/v1`` until a model declares it. The exception is what a model declares as plain JSON
(``JsonValue``: a search result's tags and ranking, a question row, a claim's inputs). Inside
those, whatever the older route sends passes through untyped, so the contract does not catch a
change there.

Values that a browser cannot hold are sent as something it can. A census's times are nanoseconds
since the epoch, above 2**53, and an inode or a device number can be too. A time is sent as an
ISO 8601 string with its zone (:func:`time_from_ns`), and an inode or a device as a string. A
time the engine recorded as text is sent as a zoned time where it states a zone, and otherwise
as ``None`` beside the text as recorded (:func:`zoned_or_none`), never re-read as local time.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from contextlib import contextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from ingence.api.v1 import ApiError, V1Context
from ingence.storage.catalog import open_catalog
from ingence.storage.commit import manifest_path

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from ingence.storage.catalog import Catalog

__all__ = [
    "boolean",
    "catalog_of",
    "integer",
    "json_value",
    "one",
    "pinned_catalog",
    "run_named",
    "run_required",
    "text_or_none",
    "time_from_ns",
    "zoned_or_none",
]


def one(query: Mapping[str, Sequence[str]], name: str) -> str | None:
    """The first value a request gives a parameter, or None."""
    values = query.get(name) or []
    return values[0] if values else None


def integer(
    query: Mapping[str, Sequence[str]], name: str, *, default: int, low: int, high: int
) -> int:
    """A whole-number parameter within a range, refused in words otherwise."""
    text = one(query, name)
    if text is None or text == "":
        return default
    try:
        value = int(text)
    except ValueError:
        message = f"{name} must be a whole number from {low} to {high}; {text!r} is not one"
        raise ApiError(message, parameter=name) from None
    if not low <= value <= high:
        message = f"{name} must be from {low} to {high}; {value} is outside that"
        raise ApiError(message, parameter=name)
    return value


def boolean(query: Mapping[str, Sequence[str]], name: str, *, true_word: str = "true") -> bool:
    """A yes-or-no parameter: true, 1, yes or the route's own word, or their opposites."""
    text = (one(query, name) or "").strip().lower()
    if text in ("true", "1", "yes", "on", true_word):
        return True
    if text in ("", "false", "0", "no", "off"):
        return False
    message = f"{name} is true or false; {text!r} is neither"
    raise ApiError(message, parameter=name)


@contextmanager
def pinned_catalog(context: V1Context) -> Iterator[tuple[Catalog, str]]:
    """The catalog of the run every read is pinned to, and that run's identifier."""
    if context.run_pinned is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    with open_catalog(context.project, run_id=context.run_pinned) as catalog:
        yield catalog, context.run_pinned


def run_required(context: V1Context, *, current_too: bool = False) -> str:
    """The run a route answers for, refused as a conflict where there is none.

    The result is the pinned run. With ``current_too``, it is the current run as well, for the
    routes that answer during a scan (``areas``, ``goals``), which take the newest run where the
    pin is not yet set. No run at all is refused with ``no_published_run`` and status 409, the
    status that the contract fixes for a question asked before the project has anything to answer
    it from.
    """
    run_id = (context.run_current or context.run_pinned) if current_too else context.run_pinned
    if run_id is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.CONFLICT)
    return run_id


#: How a run identifier is spelled; anything else is refused before a path is built from it.
_RUN_SPELLING = re.compile(r"^run_[0-9A-Za-z]+_[0-9a-f]+$")


def run_named(context: V1Context, query: Mapping[str, Sequence[str]]) -> tuple[str | None, bool]:
    """The run a read serves, and whether it is the pinned one.

    No ``run`` parameter: the run reads are pinned to. A ``run`` parameter: that run, which must
    be a published run of this project (a manifest in its directory), so a page that drew one
    run can read the rest of what it shows from the same run.
    """
    named = one(query, "run")
    if not named:
        return context.run_pinned, True
    if not _RUN_SPELLING.match(named) or not manifest_path(context.project, named).is_file():
        message = f"no published run of this project is called {named}"
        raise ApiError(message, code="unknown_run", status=HTTPStatus.NOT_FOUND, parameter="run")
    return named, named == context.run_pinned


@contextmanager
def catalog_of(
    context: V1Context, query: Mapping[str, Sequence[str]]
) -> Iterator[tuple[Catalog, str]]:
    """The catalog of the run a request names, or of the pinned run, and that run's identifier."""
    run_id, _ = run_named(context, query)
    if run_id is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    with open_catalog(context.project, run_id=run_id) as catalog:
        yield catalog, run_id


def time_from_ns(value: Any) -> str | None:
    """Nanoseconds since the epoch as ISO 8601 in UTC, to the nanosecond; None for none."""
    if value is None:
        return None
    seconds, nanoseconds = divmod(int(value), 1_000_000_000)
    moment = dt.datetime(1970, 1, 1, tzinfo=dt.UTC) + dt.timedelta(seconds=seconds)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{nanoseconds:09d}Z"


def zoned_or_none(value: Any) -> str | None:
    """A recorded time that states its zone, as recorded; None where it states none."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def text_or_none(value: Any) -> str | None:
    """A value as a string, None kept as None: for identifiers a browser must not round."""
    return None if value is None else str(value)


def json_value(value: Any) -> JsonValue:
    """A stored value as plain JSON: a JSON string read, anything else passed through JSON."""
    if isinstance(value, str):
        try:
            loaded: JsonValue = json.loads(value)
        except ValueError:
            return value
        return loaded
    return json.loads(json.dumps(value, default=str))  # type: ignore[no-any-return]
