"""Read-side views of engine-written records: the same fields in a sixth of the memory.

The memory cost of an inference is the record object, not validating it. A pydantic instance
carries about 1,150 bytes beyond the data it holds, and ``model_construct`` allocates all of it
too. A slotted type holding the same sixteen fields costs 217 bytes against 1,369. Across the
records that an inference assembles, that is about 1.26 GiB of a 4.27 GiB peak.

So the read path hands rules a view, and the write path keeps the model. A view is a frozen,
slotted dataclass with exactly the record's fields, in the record's order, holding exactly the
values the validated record held. The code builds the record, copies it into its view and drops it,
so every value a rule reads is one that :meth:`~pydantic.BaseModel.model_validate` produced.
Validation stays. It turns a stored string back into an enumeration member and a JSON column back
into nested records, and doing that by hand would be a second decoder to keep in step with the
first. Only the record instance that outlived the validation goes.

A view is its record for every ``isinstance`` test. Predicates narrow what they are handed that way,
and a view that failed the test would read as a record that is not there. The predicate would
abstain, and that abstention would look exactly like a verdict. Pydantic's models answer
``isinstance`` with the plain type check, which ignores a registered virtual subclass and consults
an object's ``__class__``. So a view reports its record as its ``__class__``, the mechanism a proxy
uses and the only one that reaches every check without touching a predicate. ``type(view)`` is still
the view's own type.

A view is its record for nothing else. It has no ``model_dump`` and no ``model_copy``, so a view
can never be handed back to a writer. That is the boundary, and it is why only the types that an
inference reads and never writes are viewed.

"""

from __future__ import annotations

import dataclasses
import sys
from typing import TYPE_CHECKING, Any, cast

from ingence.storage.index_store import record_from_row

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.core.schema import Record

__all__ = ["view_from_row", "view_of", "view_type"]

_VIEWS: dict[type[Record], type] = {}


def _reported_as(record: type[Record]) -> property:
    """Return the ``__class__`` a view of one record type reports: the record type itself."""

    def reported(_view: object) -> type[Record]:
        return record

    return property(reported)


def view_type(record: type[Record]) -> type:
    """Return the slotted view type of one record type, made once."""
    found = _VIEWS.get(record)
    if found is None:
        found = dataclasses.make_dataclass(
            f"{record.__name__}View",
            [(name, Any) for name in record.model_fields],
            frozen=True,
            slots=True,
            namespace={"__class__": _reported_as(record)},
        )
        found.__doc__ = (
            f"A read-side view of :class:`{record.__name__}`: its fields and values, and nothing "
            f"else."
        )
        _VIEWS[record] = found
    return found


def _shared(value: Any) -> Any:
    """Return one copy of a repeated string, and any other value as it is.

    A row's strings are new objects every time, so an entry's identifier is held once on the
    entry and again on each of its components and bindings, and a run identifier once per row:
    measured on a dived drive census, about a quarter of the peak was the values the views hold.
    Interning keeps one copy of each equal string, which changes no value and no comparison.
    """
    return sys.intern(value) if type(value) is str else value


def view_of[RecordType: Record](record: RecordType) -> RecordType:
    """Return a record's view, holding the very values the record holds.

    Typed as the record for the predicates that read it, which a view satisfies for every
    attribute and every ``isinstance`` test, and for nothing a writer would ask of it. Equal
    strings are shared between views, which an equal string cannot tell.
    """
    fields = type(record).model_fields
    return cast(
        "RecordType",
        view_type(type(record))(*(_shared(getattr(record, name)) for name in fields)),
    )


def view_from_row[RecordType: Record](
    row: Mapping[str, Any], record: type[RecordType]
) -> RecordType:
    """Rebuild one row as its record, validated as the write path left it, and keep the view."""
    return view_of(record_from_row(row, record))
