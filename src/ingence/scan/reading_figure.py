"""Count how much of the names a run read, in counts that say what they count.

A share with no unit cannot say what it is a share of: words, or every part of a name, and whether
a date counts as unknown. So wherever the engine states its reading (the census's closing lines,
``ingence teach shapes``, the Coverage page) it states this figure, in counts:

- The word parts (tokens read as words). Of them, how many were understood (bound to a Dictionary
  sense), unknown (looked up, and no sense holds them), contested (two senses or more stand level
  and nothing separates them) and, where a run did not look words up, not looked up. These sum to
  the words read.
- Beside them, the parts read as something other than a word, each by its kind: numbers, replicate
  or index labels, versions, dates, quantities with a unit, identifiers (hashes, accessions) and
  extensions. They are read as what they are, so they are neither understood nor unknown.

Words and the rest sum to the parts read. Every count is of occurrences in names. A word that a
name writes twice counts twice. The counts cover the published run's name parts, files and folders
alike. This is the denominator that :meth:`~ingence.storage.catalog.Catalog.unbound_rate` calls
"word occurrences".

The figure states no share. A percentage of a count that names its unit adds nothing, and a
percentage without the unit hides what it measures. Nothing here decides anything. It is a count
over the Index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ingence.storage.catalog import Catalog

__all__ = ["ALSO_READ", "reading_figure", "reading_said"]

#: The parts read as something other than a word, by component kind, in the order they are said,
#: with how each is said in the plural and in the singular.
ALSO_READ: tuple[tuple[str, str, str], ...] = (
    ("number", "numbers", "number"),
    ("indexed", "replicate or index labels", "replicate or index label"),
    ("version", "versions", "version"),
    ("date", "dates", "date"),
    ("quantity", "quantities with a unit", "quantity with a unit"),
    ("identifier", "identifiers (hashes, accessions)", "identifier (a hash, an accession)"),
    ("extension", "extensions", "extension"),
)

#: How each binding status of a word is counted.
_WORD_STATUS = {
    "resolved": "understood",
    "examined_no_evidence": "unknown",
    "examined_conflicting": "contested",
    "unexamined": "not_looked_up",
}


def reading_figure(catalog: Catalog) -> dict[str, Any]:
    """Return the published run's reading: the parts read, the words by outcome, the rest by kind.

    ``{"parts": n, "words": {"read", "understood", "unknown", "contested", "not_looked_up"},
    "also": {kind: n}}``, where ``also`` holds only the kinds some name has; a kind the tokeniser
    writes that :data:`ALSO_READ` does not name is counted under its own name, so nothing read is
    left out of the sum. Empty counts where nothing is indexed.
    """
    words = {"read": 0, "understood": 0, "unknown": 0, "contested": 0, "not_looked_up": 0}
    also: dict[str, int] = {}
    if catalog.has_run:
        rows = catalog.query(
            """
            SELECT c.component_type, coalesce(b.resolution_status, 'unexamined') AS status,
                   sum(c.occurrences) AS parts
            FROM components c
            LEFT JOIN dictionary_bindings b ON b.component_id = c.component_id
            WHERE c.source_scope = 'name'
            GROUP BY 1, 2
            """
        ).fetchall()
        for kind, status, parts in rows:
            count = int(parts or 0)
            if kind == "word":
                words["read"] += count
                words[_WORD_STATUS.get(str(status), "not_looked_up")] += count
            else:
                also[str(kind)] = also.get(str(kind), 0) + count
    rank = {kind: at for at, (kind, _, _) in enumerate(ALSO_READ)}
    ordered = {
        kind: also[kind]
        for kind in sorted(also, key=lambda kind: (rank.get(kind, len(rank)), kind))
        if also[kind]
    }
    return {"parts": words["read"] + sum(ordered.values()), "words": words, "also": ordered}


def reading_said(figure: dict[str, Any]) -> str:
    """Say a reading figure in plain words, each count with its unit (:func:`reading_figure`).

    "Words: 12,400 read; 4,170 understood (a Dictionary meaning), 7,900 unknown, 330 tied.
    Also read: 3,100 numbers, 900 replicate or index labels, 1,050 dates. 17,450 parts of names in
    all."
    """
    words = figure["words"]
    said = (
        f"Words: {words['read']:,} read; {words['understood']:,} understood (a Dictionary "
        f"meaning), {words['unknown']:,} unknown, {words['contested']:,} tied"
    )
    if words.get("not_looked_up"):
        said += f", {words['not_looked_up']:,} not looked up"
    said += "."
    named = {kind: (plural, single) for kind, plural, single in ALSO_READ}
    also = [
        f"{count:,} {named.get(kind, (kind, kind))[0 if count != 1 else 1]}"
        for kind, count in figure["also"].items()
        if count
    ]
    if also:
        said += f" Also read: {', '.join(also)}."
    return said + f" {figure['parts']:,} parts of names in all."
