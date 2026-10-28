"""Structural identity: same shape or schema, read from a header and no further.

Catches what byte identity cannot: the same table exported twice, the same array
saved by two tools, the same measurement re-serialised. Those files share no
bytes and are obviously related to a person looking at them.

Cheap on purpose. Everything here reads a header or a first line and stops.
A structural identity that had to read a whole file would compete with byte
identity while proving less, and the point of a cheap identity is to nominate
candidates that an expensive one then settles.

Normalisation is where the value is, and where the risk is. Two tables with
the same columns in a different order are the same schema. Two with a column
renamed are not. Every normalisation here is deliberate, narrow and listed, so a
signature never quietly merges two things that differ in a way somebody cares
about. Case is folded and surrounding whitespace removed, because those vary
between exports of one thing. Column order is preserved, because a reordered
table is a different table to anybody joining it.
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json, digest_text
from ingence.core.outcomes import IdentityScope
from ingence.extractors.delimited import detect_delimiter, normalise_column
from ingence.identity import IDENTITY_METHODS, IdentityResult

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["DIMENSION_COUNT", "ArraySignature", "TabularSchema"]

#: Bytes read to find a header. A header longer than this is not a header.
HEADER_BYTES = 64 * 1024

#: The extensions the table reader reads, so the digest is made for the files that also have a
#: column count (``extractors/tabular.py``).
DELIMITED = {".csv", ".tsv", ".tab"}

#: The observation key an array's number of dimensions is stored under.
DIMENSION_COUNT = "array.dimension_count"


class TabularSchema:
    """The ordered, normalised column names of a delimited table, and its delimiter.

    Order is kept. A reordered table is a different table to anybody joining it,
    and treating the two as one schema would silently merge groups a user would
    expect to be separate.

    The digest holds the names, their order and the delimiter, and nothing about the
    values. Column types are a guess over sampled rows, and a type in the digest would
    split one export in two on sampling noise. The delimiter is in because a table written
    with semicolons is read by a program that expects them.

    Version 2 reads the delimiter as the table reader does (``extractors/delimited.py``): a
    semicolon is a delimiter, and the most frequent candidate on the first line wins.
    """

    name = "tabular_schema"
    version = "2"
    substitutive = False

    def supports(self, context: ExtractionContext) -> bool:
        if context.extensions & DELIMITED:
            return True
        return context.media_type in {"text/csv", "text/tab-separated-values"}

    def compute(self, context: ExtractionContext) -> IdentityResult | None:
        try:
            with context.path.open("rb") as handle:
                head = handle.read(HEADER_BYTES)
        except OSError:
            return None
        if not head:
            return None
        try:
            text = head.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Not text under the encoding this method understands. Saying
            # nothing is more useful than a signature over mojibake.
            return None

        lines = text.splitlines()
        delimiter = detect_delimiter(lines[0] if lines else "", context.extensions)
        try:
            columns = next(csv.reader(io.StringIO(text), delimiter=delimiter))
        except (csv.Error, StopIteration):
            return None
        if len(columns) < 2:
            # One column is not a schema. Every line of every text file would
            # otherwise produce one, and they would all collide with each other.
            return None

        payload = {
            "columns": [normalise_column(column) for column in columns],
            "delimiter": delimiter,
        }
        return IdentityResult(
            method=self.name,
            method_version=self.version,
            scope=IdentityScope.HEADER,
            value=digest_text(canonical_json(payload)),
            bytes_read=len(head),
        )


class ArraySignature:
    """Shape and element type of a stored array, from its header alone.

    Implemented for the NumPy format because its header is a short, documented,
    plain-text dictionary at a fixed offset, which makes this genuinely a header
    read. Other array containers land with their extractors, where the format
    machinery already exists and does not need duplicating here.

    The digest holds the shape, the element type, the storage order and the number of
    dimensions. Version 2 records the number of dimensions inside the digest and beside it,
    as the count ``array.dimension_count``, so a rule can ask for arrays of a minimum number
    of dimensions without reading the shape back out of a digest. A scalar has none.
    """

    name = "array_signature"
    version = "2"
    substitutive = False

    def supports(self, context: ExtractionContext) -> bool:
        return ".npy" in context.extensions or context.media_type == "application/x-npy"

    def compute(self, context: ExtractionContext) -> IdentityResult | None:
        try:
            with context.path.open("rb") as handle:
                head = handle.read(HEADER_BYTES)
        except OSError:
            return None
        if not head.startswith(b"\x93NUMPY"):
            return None
        try:
            # Version at bytes 6 and 7, then a two or four byte header length,
            # then a Python literal dictionary. Read as text and parsed by
            # pattern rather than evaluated: this is untrusted content and
            # nothing in the engine executes what it finds.
            major = head[6]
            length_size = 2 if major == 1 else 4
            start = 8 + length_size
            header = head[start : start + 256].decode("latin-1")
        except (IndexError, UnicodeDecodeError):
            return None

        shape = _between(header, "'shape': (", ")")
        dtype = _between(header, "'descr': '", "'")
        order = _between(header, "'fortran_order': ", ",")
        if shape is None or dtype is None:
            return None
        lengths = [part.strip() for part in shape.split(",") if part.strip()]
        payload = {
            "shape": lengths,
            "dimensions": len(lengths),
            "dtype": dtype,
            "fortran_order": (order or "").strip(),
        }
        return IdentityResult(
            method=self.name,
            method_version=self.version,
            scope=IdentityScope.HEADER,
            value=digest_text(canonical_json(payload)),
            bytes_read=len(head),
            counts=((DIMENSION_COUNT, len(lengths)),),
        )


def _between(text: str, opening: str, closing: str) -> str | None:
    """Return the text between two markers, or None when either is absent."""
    start = text.find(opening)
    if start < 0:
        return None
    start += len(opening)
    end = text.find(closing, start)
    if end < 0:
        return None
    return text[start:end]


@IDENTITY_METHODS.register(
    "tabular_schema",
    version="2",
    summary="ordered normalised column names of a delimited table",
    attributes={"extensions": ".csv,.tsv,.tab"},
)
def _build_tabular() -> TabularSchema:
    return TabularSchema()


@IDENTITY_METHODS.register(
    "array_signature",
    version="2",
    summary="shape and element type of a stored array, from its header",
    attributes={"extensions": ".npy"},
)
def _build_array() -> ArraySignature:
    return ArraySignature()
