"""The core's quality figures: what a run found, read, understood of names, concluded and cost.

Every figure is a count taken from the run's own stored tables, in the way the catalogue
(``catalogue.yaml``) says in one line, or a number the pass counted exactly and passed on (an
inference's tallies of rule outcomes). Nothing is estimated and nothing is a model's. A figure the
run's pass does not reach is not run, which is said, never zero. A figure the engine does not
record is not recorded, which is said, never a guess.

The counts are over entries that are on the file system and not deleted (``entry_kind =
'filesystem'``) unless a figure says it counts parts, which a dive recovered from inside files.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ingence.core.outcomes import ScanOutcome
from ingence.core.schema import EntryType, ExclusionReason, RecognisedAs
from ingence.identity import digest_methods
from ingence.quality.catalogue import CORE_CATALOGUE, catalogue, load_catalogue
from ingence.scan.reading_figure import reading_figure
from ingence.storage.catalog import census_facts

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ingence.core.schema import QualityFigure
    from ingence.quality.context import QualityContext
    from ingence.storage.catalog import Catalog

__all__ = [
    "COLLECTOR_FIGURES",
    "QUALITY_TIME_FIGURE",
    "READ_FACET_OF",
    "SUMMARIES_FIGURE",
    "UNLISTED_OPERATIONS",
    "summarise",
]

#: The scan-error operations that say a folder could not be listed: the census's own set
#: (``scan.census``), repeated here so this module does not import the walk. A test holds the two
#: equal.
UNLISTED_OPERATIONS: tuple[str, ...] = ("lstat", "scandir")

#: Where each stored outcome of a file's content reading is counted. A test holds this complete
#: over :class:`~ingence.core.outcomes.ScanOutcome`, so a new outcome cannot go uncounted.
READ_FACET_OF: dict[ScanOutcome, str] = {
    ScanOutcome.EXTRACTED: "read",
    ScanOutcome.EXTRACTED_WITH_FALLBACK: "read",
    ScanOutcome.DEFERRED: "budget",
    ScanOutcome.SKIPPED_BY_POLICY: "policy",
    ScanOutcome.SKIPPED_BY_SETTINGS: "settings",
    ScanOutcome.CONTENT_ERROR: "unreadable",
    ScanOutcome.CHANGED_DURING_READ: "changed",
    ScanOutcome.METADATA_ONLY: "not_reached",
}

_FILES = "entry_kind = 'filesystem' AND entry_type = 'file' AND NOT is_deleted"
_LIVE = "entry_kind = 'filesystem' AND NOT is_deleted"
_FILES_OF_F = "f.entry_kind = 'filesystem' AND f.entry_type = 'file' AND NOT f.is_deleted"

#: Which of a run's entry types is which of the kinds the catalogue names.
_KIND_OF: dict[str, str] = {
    EntryType.FILE.value: "files",
    EntryType.DIRECTORY.value: "folders",
    EntryType.SYMLINK.value: "links",
    EntryType.OTHER.value: "other",
}

_TIME = "%Y-%m-%dT%H:%M:%SZ"


def summarise(context: QualityContext) -> Iterator[QualityFigure]:
    """State every core figure the run's pass reaches. The catalogue's other figures are not run."""
    catalog = context.catalog
    emit = {
        "found.entries": _entries,
        "found.bytes": _bytes,
        "found.sizes_missing": _sizes_missing,
        "found.left_out": _left_out,
        "found.own_files": _own_files,
        "found.folders_not_listed": _folders_not_listed,
        "found.folders_counted": _folders_counted,
        "found.parts": _parts,
        "read.files_read": _files_read,
        "read.files_not_read": _files_not_read,
        "read.digests": _digests,
        "read.files_with_parts": _files_with_parts,
        "read.errors": _errors,
        "names.parts": _name_parts,
        "names.words": _name_words,
        "conclusions.claims": _claims,
        "conclusions.claims_standing": _claims_standing,
        "conclusions.candidates": _candidates,
        "conclusions.abstentions": _abstentions,
        "conclusions.findings": _findings,
        "conclusions.questions": _questions,
        "cost.wall_time": _wall_time,
        "cost.dive_budget": _dive_budget,
        "cost.peak_memory": _peak_memory,
    }
    # The collector states a summary it could not read. No function of the core's does.
    owned = set(load_catalogue(CORE_CATALOGUE).figures) - COLLECTOR_FIGURES
    if set(emit) != owned:
        message = f"the core summary and its catalogue disagree on {sorted(set(emit) ^ owned)}"
        raise RuntimeError(message)
    for figure, function in emit.items():
        if not context.reaches(figure):
            continue
        # One figure that cannot be counted is only itself not recorded (a stored value the
        # reader does not know, a table that will not open): the rest of the core's figures stand.
        try:
            stated = list(function(context, catalog))
        except Exception as error:
            stated = [context.not_recorded(figure, f"could not be counted: {type(error).__name__}")]
        yield from stated


#: The figure the collector writes for a registered summary whose catalogue could not be read.
SUMMARIES_FIGURE = "quality.summaries_not_recorded"

#: The figure the collector writes for the time each summary took.
QUALITY_TIME_FIGURE = "cost.quality_time"

#: The core's catalogue names these, and the collector states them: no function of the core's does.
COLLECTOR_FIGURES = frozenset({SUMMARIES_FIGURE, QUALITY_TIME_FIGURE})


def _one(catalog: Catalog, sql: str, parameters: Any = None) -> int:
    row = catalog.query(sql, parameters).fetchone()
    return int(row[0] or 0) if row else 0


def _files_found(catalog: Catalog) -> int:
    return _one(catalog, f"SELECT count(*) FROM entries WHERE {_FILES}")  # noqa: S608


def _folders_found(catalog: Catalog) -> int:
    return _one(
        catalog,
        f"SELECT count(*) FROM entries WHERE {_LIVE} AND entry_type = 'directory'",  # noqa: S608
    )


def _entries(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    held = dict(
        catalog.query(
            f"SELECT entry_type, count(*) FROM entries WHERE {_LIVE} GROUP BY 1"  # noqa: S608
        ).fetchall()
    )
    total = sum(int(number) for number in held.values())
    for kind, facet in _KIND_OF.items():
        yield context.counted(
            "found.entries", facet=facet, count=int(held.get(kind, 0)), total=total
        )


def _bytes(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    yield context.counted(
        "found.bytes",
        count=_one(catalog, f"SELECT sum(size_bytes) FROM entries WHERE {_FILES}"),  # noqa: S608
    )


def _sizes_missing(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    yield context.counted(
        "found.sizes_missing",
        count=_one(
            catalog,
            f"SELECT count(*) FROM entries WHERE {_FILES} AND size_bytes IS NULL",  # noqa: S608
        ),
        total=_files_found(catalog),
    )


def _names_met(catalog: Catalog) -> int:
    """Entries found, plus names left out, plus names recognised as the engine's and not indexed."""
    excluded, recognised = census_facts(catalog)
    return (
        _one(catalog, f"SELECT count(*) FROM entries WHERE {_LIVE}")  # noqa: S608
        + sum(item.count for item in excluded)
        + sum(1 for path in recognised if not path.indexed)
    )


def _left_out(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    excluded, _ = census_facts(catalog)
    total = _names_met(catalog)
    for reason in ExclusionReason:
        yield context.counted(
            "found.left_out",
            facet=reason.value,
            count=sum(item.count for item in excluded if item.reason is reason),
            total=total,
        )


def _own_files(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    _, recognised = census_facts(catalog)
    total = _names_met(catalog)
    for kind in RecognisedAs:
        yield context.counted(
            "found.own_files",
            facet=kind.value,
            count=sum(1 for path in recognised if path.recognised_as is kind and not path.indexed),
            total=total,
        )


def _folders_not_listed(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    yield context.counted(
        "found.folders_not_listed",
        count=_one(
            catalog,
            "SELECT count(DISTINCT relative_path) FROM scan_errors "
            "WHERE operation IN (SELECT unnest(?::VARCHAR[]))",
            [list(UNLISTED_OPERATIONS)],
        ),
        total=_folders_found(catalog),
    )


def _folders_counted(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    yield context.counted(
        "found.folders_counted",
        count=_one(
            catalog,
            "SELECT count(DISTINCT subject) FROM claims "
            "WHERE predicate = 'closed_world.direct_file_count'",
        ),
        total=_folders_found(catalog),
    )


def _parts(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    yield context.counted(
        "found.parts",
        count=_one(
            catalog,
            "SELECT count(*) FROM entries WHERE entry_kind = 'derived' AND NOT is_deleted",
        ),
    )


def _outcomes(catalog: Catalog) -> dict[str, int]:
    """Files found, by where their stored outcome is counted."""
    counted = dict.fromkeys(dict.fromkeys(READ_FACET_OF.values()), 0)
    for outcome, number in catalog.query(
        f"SELECT scan_outcome, count(*) FROM entries WHERE {_FILES} GROUP BY 1"  # noqa: S608
    ).fetchall():
        counted[READ_FACET_OF[ScanOutcome(outcome)]] += int(number)
    return counted


def _files_read(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    yield context.counted(
        "read.files_read", count=_outcomes(catalog)["read"], total=_files_found(catalog)
    )


def _files_not_read(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    held = _outcomes(catalog)
    total = _files_found(catalog)
    for facet in catalogue().figures["read.files_not_read"].facets:
        yield context.counted("read.files_not_read", facet=facet, count=held[facet], total=total)


def _digests(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    methods = digest_methods()
    whole = sorted(name for name, kind in methods.items() if kind == "whole")
    sampled = sorted(name for name, kind in methods.items() if kind == "sampled")
    row = catalog.query(
        f"""
        WITH files AS (SELECT entry_id FROM entries WHERE {_FILES}),
        held AS (
            SELECT o.entry_id,
                   bool_or(o.identity_method IN (SELECT unnest(?::VARCHAR[]))) AS has_whole,
                   bool_or(o.identity_method IN (SELECT unnest(?::VARCHAR[]))) AS has_sampled
            FROM observations o JOIN files f ON f.entry_id = o.entry_id
            WHERE o.identity_method IS NOT NULL
            GROUP BY 1
        )
        SELECT
            count(*) FILTER (WHERE has_whole),
            count(*) FILTER (WHERE has_sampled AND NOT has_whole)
        FROM held
        """,  # noqa: S608
        [whole, sampled],
    ).fetchone()
    with_whole, with_sampled = (int(value or 0) for value in (row or (0, 0)))
    total = _files_found(catalog)
    for facet, number in (
        ("whole", with_whole),
        ("sampled", with_sampled),
        ("none", total - with_whole - with_sampled),
    ):
        yield context.counted("read.digests", facet=facet, count=number, total=total)


def _files_with_parts(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    yield context.counted(
        "read.files_with_parts",
        count=_one(
            catalog,
            "SELECT count(DISTINCT d.parent_entry_id) FROM entries d "  # noqa: S608
            f"JOIN entries f ON f.entry_id = d.parent_entry_id AND {_FILES_OF_F} "
            "WHERE d.entry_kind = 'derived' AND NOT d.is_deleted",
        ),
        total=_files_found(catalog),
    )


def _errors(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    held = dict(
        catalog.query(
            f"""
            SELECT e.failure_class, count(DISTINCT e.entry_id)
            FROM scan_errors e JOIN entries f ON f.entry_id = e.entry_id
            WHERE {_FILES_OF_F}
            GROUP BY 1
            """  # noqa: S608
        ).fetchall()
    )
    total = _files_found(catalog)
    for facet in catalogue().figures["read.errors"].facets:
        yield context.counted(
            "read.errors", facet=facet, count=int(held.get(facet, 0)), total=total
        )


#: The kinds of name part the catalogue names. Any other the tokeniser writes is counted as other.
_NAME_KINDS = (
    "word",
    "number",
    "indexed",
    "version",
    "date",
    "quantity",
    "identifier",
    "extension",
)


def _name_parts(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    read = reading_figure(catalog)
    held: dict[str, int] = {"word": int(read["words"]["read"])}
    for kind, number in read["also"].items():
        key = kind if kind in _NAME_KINDS else "other"
        held[key] = held.get(key, 0) + int(number)
    total = int(read["parts"])
    for facet in catalogue().figures["names.parts"].facets:
        yield context.counted("names.parts", facet=facet, count=held.get(facet, 0), total=total)


def _name_words(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    words = reading_figure(catalog)["words"]
    for facet in catalogue().figures["names.words"].facets:
        yield context.counted(
            "names.words", facet=facet, count=int(words[facet]), total=int(words["read"])
        )


def _claims(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    rows = catalog.query(
        """
        SELECT coalesce(r.primitive, r.effect, 'unlisted') AS kind, c.inference_tier, count(*)
        FROM claims c
        LEFT JOIN rules r ON r.rule_id = c.rule_id AND r.version = c.rule_version
        WHERE coalesce(r.effect, '') <> 'enumerates'
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetchall()
    total = sum(int(number) for _, _, number in rows)
    yield context.counted("conclusions.claims", count=total)
    for kind, tier, number in rows:
        yield context.counted(
            "conclusions.claims", facet=f"{kind}/{tier}", count=int(number), total=total
        )


def _claims_standing(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    rows = catalog.query(
        """
        SELECT c.resolution_status, count(*)
        FROM claims c
        LEFT JOIN rules r ON r.rule_id = c.rule_id AND r.version = c.rule_version
        WHERE coalesce(r.effect, '') <> 'enumerates'
        GROUP BY 1
        """
    ).fetchall()
    held = {str(status): int(number) for status, number in rows}
    total = sum(held.values())
    named = {
        "resolved": held.pop("resolved", 0),
        "conflicting": held.pop("examined_conflicting", 0),
    }
    for facet, number in {**named, **dict(sorted(held.items()))}.items():
        yield context.counted("conclusions.claims_standing", facet=facet, count=number, total=total)


def _candidates(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    rows = catalog.query(
        "SELECT primitive, decision_tier, count(*) FROM candidates GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    total = sum(int(number) for _, _, number in rows)
    yield context.counted("conclusions.candidates", count=total)
    for primitive, tier, number in rows:
        yield context.counted(
            "conclusions.candidates", facet=f"{primitive}/{tier}", count=int(number), total=total
        )


#: The pass's own tallies, by the catalogue's facet and the stored name of the outcome.
RULE_OUTCOMES = ("evaluated", "unknown", "unexamined", "conflicting", "starved")


def _abstentions(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    tallied = context.extras.get("rule_outcomes")
    if not isinstance(tallied, dict) or "evaluated" not in tallied:
        yield context.not_recorded(
            "conclusions.abstentions",
            "the inference pass did not pass on its counts of rule outcomes",
        )
        return
    total = int(tallied["evaluated"])
    for facet in catalogue().figures["conclusions.abstentions"].facets:
        yield context.counted(
            "conclusions.abstentions", facet=facet, count=int(tallied.get(facet, 0)), total=total
        )


def _findings(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    held = dict(catalog.query("SELECT subject_kind, count(*) FROM findings GROUP BY 1").fetchall())
    total = sum(int(number) for number in held.values())
    for facet in catalogue().figures["conclusions.findings"].facets:
        yield context.counted(
            "conclusions.findings", facet=facet, count=int(held.get(facet, 0)), total=total
        )


def _questions(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    yield context.counted(
        "conclusions.questions",
        count=_one(
            catalog,
            "SELECT count(*) FROM (SELECT DISTINCT f.rule_id, u.value "
            "FROM findings f, unnest(f.raised) AS u(value))",
        ),
    )


def _stamp(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), _TIME).replace(tzinfo=UTC)
    except ValueError:
        return None


def _run_row(catalog: Catalog) -> dict[str, Any]:
    cursor = catalog.query(
        "SELECT started_at, ended_at, deepening_budget_granted_seconds, "
        "deepening_budget_spent_seconds FROM run"
    )
    row = cursor.fetchone()
    names = ("started_at", "ended_at", "granted", "spent")
    return dict(zip(names, row, strict=True)) if row else {}


def _wall_time(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    held = _run_row(catalog)
    began, ended = _stamp(held.get("started_at")), _stamp(held.get("ended_at"))
    if began is None or ended is None:
        yield context.not_recorded("cost.wall_time", "the run record holds no start or end stamp")
        return
    yield context.counted(
        "cost.wall_time", duration_ns=max(0, int((ended - began).total_seconds())) * 1_000_000_000
    )


def _dive_budget(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    if context.phase != "dive":
        yield context.not_run("cost.dive_budget")
        return
    held = _run_row(catalog)
    for facet, key in (("granted", "granted"), ("spent", "spent")):
        seconds = held.get(key)
        if seconds is None:
            yield context.not_recorded(
                "cost.dive_budget", "this run did no content work, so it has no budget", facet=facet
            )
        else:
            yield context.counted(
                "cost.dive_budget", facet=facet, duration_ns=round(float(seconds) * 1e9)
            )


def _peak_memory(context: QualityContext, catalog: Catalog) -> Iterable[QualityFigure]:
    """The engine's own peak, which only the pass publishing the run can read: no table holds it.

    Taken by the engine's own reading (``executors.supervisor._resident_bytes``) at the end of
    the phase and handed on as ``peak_resident_bytes``. Where the platform cannot read it, or the
    run is stated again from its tables afterward, it is not recorded.
    """
    peak = context.extras.get("peak_resident_bytes")
    if isinstance(peak, int) and peak >= 0:
        yield context.counted("cost.peak_memory", peak_bytes=peak)
    else:
        yield context.not_recorded(
            "cost.peak_memory", "the platform cannot read it, or it was not read when the run ended"
        )
