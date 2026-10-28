"""The whole comparison as one plain dict: what the History page's notice and ``ingence log`` show.

The entry point is :func:`compare_organisation`. Two runs (or two folders of one run) go in, and
one report comes out. Nothing is written to a run, a project or the Record. The comparison is two
steps that a page may take apart. :func:`measure` matches the files and judges the organisation
(seconds on a large census). The two runs never change, so its answer can be held.
:func:`report_of` reads the Record's decisions against it (milliseconds, and done again whenever
the Record grows).

``api/routes/reorganisation.py`` calls it in two ways:

- For a run and the census before it (one project), the comparison's first line, ``notice``, opens
  the History page for that run. It never stops inference and never waits for an answer, because
  a reorganisation may be the reason someone runs Ingence.
- On demand, from the same page's run picker, between two projects (the same files on another
  drive or another machine), or between two folders of one census (a backup beside its source).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import duckdb

from ingence.compare import COMPARE_VERSION
from ingence.compare.carry import (
    DEFAULT_MINIMUM_SHARE,
    DecisionBasis,
    Follow,
    carried_decisions,
    decision_basis,
    folder_lines,
    moved_file_decisions,
)
from ingence.compare.fingerprints import DEFAULT_MTIME_GRAIN_NS, SIZE_BLIND_TIERS, TIERS
from ingence.compare.match import Keying, MatchSummary, Side, attach_both, match_files
from ingence.compare.structure import Structure, compare_structure, iter_matched_pairs
from ingence.compare.verdict import (
    DEFAULT_THRESHOLDS,
    FIRST_AND_SECOND,
    HISTORY,
    NAMED_IN_NOTICE,
    NOTICED,
    Context,
    Sides,
    Thresholds,
    Verdict,
    birth_evidence,
    notice,
    read_context,
    read_verdict,
)
from ingence.storage.connection import connect as duckdb_connection

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ingence.core.schema import RecordEvent

__all__ = ["LISTED", "Measured", "compare_organisation", "measure", "report_of", "sides_of"]

#: How many named folders a report lists under "split" and "merged" (``listed``). The counts are
#: exact.
LISTED = 50


def _roots(connection: duckdb.DuckDBPyConnection, left: Side, right: Side) -> dict[str, Any]:
    rows = {
        side: connection.execute(
            f"SELECT root_id, mount_path, case_sensitivity FROM {side}_roots"  # noqa: S608
        ).fetchall()
        for side in ("l", "r")
    }
    return {
        "same_root_id": bool({row[0] for row in rows["l"]} & {row[0] for row in rows["r"]}),
        "same_mount_path": bool({row[1] for row in rows["l"]} & {row[1] for row in rows["r"]}),
        # One census read twice at one folder is the same tree; two folders of it are not.
        "same_place": left.under == right.under,
    }


def sides_of(left: Side, right: Side) -> Sides:
    """How the notice names the two sides: a folder by its path, one project's two readings as
    the earlier and the later (the History page), two projects as the first and the second."""
    if left.under or right.under:
        return Sides(
            f"'{left.under}'" if left.under else FIRST_AND_SECOND.first,
            f"'{right.under}'" if right.under else FIRST_AND_SECOND.second,
        )
    if left.project.resolve() == right.project.resolve():
        return HISTORY
    return FIRST_AND_SECOND


@dataclass(frozen=True)
class Measured:
    """One comparison's organisation part, measured once: everything but the decisions.

    Held by a page per (project, earlier run, later run): the two runs never change, so neither
    does this. :func:`report_of` reads the Record's decisions against it in milliseconds.
    """

    left: Side
    right: Side
    keying: Keying
    summary: MatchSummary
    structure: Structure
    verdict: Verdict
    verdict_because: list[str]
    context: Context
    context_because: list[str]
    copy_side: str | None
    basis: DecisionBasis
    thresholds: Thresholds = DEFAULT_THRESHOLDS
    mtime_grain_ns: int = DEFAULT_MTIME_GRAIN_NS
    listed: int = LISTED
    named_in_notice: int = NAMED_IN_NOTICE


def measure(
    left: Side,
    right: Side,
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    mtime_grain_ns: int = DEFAULT_MTIME_GRAIN_NS,
    listed: int = LISTED,
    named_in_notice: int = NAMED_IN_NOTICE,
) -> Measured:
    """Match the two sides' files, measure the organisation, and judge it: the costly part.

    ``left`` is the earlier (or original) side and ``right`` the later (or the suspected copy).
    ``mtime_grain_ns`` is the grain modification and birth times are compared at; ``listed`` how
    many split and merged folders the report lists by name, and ``named_in_notice`` how many the
    notice names.
    """
    from ingence.storage.commit import run_path

    connection = duckdb_connection()
    try:
        keying = attach_both(connection, left, right)
        summary = match_files(connection, mtime_grain_ns=mtime_grain_ns)
        roots = _roots(connection, left, right)
        # Streamed twice rather than listed once.
        structure = compare_structure(iter_matched_pairs(connection))
        verdict, verdict_because = read_verdict(summary, structure, thresholds=thresholds)
        births = birth_evidence(connection, grain_ns=mtime_grain_ns)
        context, context_because, copy_side = read_context(
            roots, births, structure, thresholds=thresholds
        )
        basis = decision_basis(
            connection,
            iter_matched_pairs(connection),
            earlier_run=run_path(left.project, left.run_id),
        )
    finally:
        connection.close()
    return Measured(
        left=left,
        right=right,
        keying=keying,
        summary=summary,
        structure=structure,
        verdict=verdict,
        verdict_because=verdict_because,
        context=context,
        context_because=context_because,
        copy_side=copy_side,
        basis=basis,
        thresholds=thresholds,
        mtime_grain_ns=mtime_grain_ns,
        listed=listed,
        named_in_notice=named_in_notice,
    )


def report_of(
    measured: Measured,
    events: Iterable[RecordEvent] = (),
    *,
    minimum_share: float = DEFAULT_MINIMUM_SHARE,
) -> dict[str, Any]:
    """The whole report: the measured comparison, and where the files under each decision went.

    ``events`` are the Record's decisions (``record.log.read_events``); they are read, never
    written. Mount paths and run identifiers appear in the result: the caller shows a person the
    run's name, never these.
    """
    held = tuple(events)
    left, right = measured.left, measured.right
    summary, structure, verdict = measured.summary, measured.structure, measured.verdict
    thresholds, keying, listed = measured.thresholds, measured.keying, measured.listed
    result = carried_decisions(measured.basis, held, minimum_share=minimum_share)
    about_files = moved_file_decisions(measured.basis, held) if held else 0
    followed = sum(1 for item in result.carried if item.follow is Follow.FOLLOWS)
    scattered = sum(1 for item in result.carried if item.follow is Follow.SCATTERED)
    mixed = sum(1 for item in result.carried if item.follow is Follow.MIXED)
    fates: dict[str, int] = {}
    for fate in structure.folders:
        fates[fate.fate] = fates.get(fate.fate, 0) + 1
    return {
        "compare_version": COMPARE_VERSION,
        "left": {
            "project": str(left.project),
            "run_id": left.run_id,
            "label": left.label,
            "under": left.under,
        },
        "right": {
            "project": str(right.project),
            "run_id": right.run_id,
            "label": right.label,
            "under": right.under,
        },
        "verdict": verdict.value,
        "verdict_because": measured.verdict_because,
        "context": measured.context.value,
        "context_because": measured.context_because,
        "copy_side": measured.copy_side,
        "thresholds": asdict(thresholds),
        "paths_compared": {
            "case_merged_on_every_root": keying.every_root,
            "case_merged_on_roots": list(keying.folded),
            "root_in_path": keying.by_root,
        },
        "minimum_share": minimum_share,
        "files": summary.files,
        "mtime_grain_ns": measured.mtime_grain_ns,
        "matched_by_tier": summary.matched,
        # Each side that states no sizes at all, and how many files were matched without them, by
        # a tier that keys on no size (a weaker kind of match, never proof of the same bytes, and
        # nothing is carried across it).
        "states_no_sizes": summary.states_no_sizes,
        "matched_without_size": summary.matched_without_size,
        "size_blind_tiers": [tier.value for tier in TIERS if tier in SIZE_BLIND_TIERS],
        "ambiguous_by_tier": summary.ambiguous,
        "unmatched": summary.unmatched,
        # Each side's unmatched files, apart: at the same path with another size or time, held as
        # identical copies that could not be told apart, and not on the other side at all.
        "changed": summary.changed,
        "not_told_apart": summary.not_told_apart,
        "absent": summary.absent,
        "paths_kept": structure.paths_kept,
        "names_kept": structure.names_kept,
        "matched": structure.matched,
        "rewrites": [
            {"from": old or "(root)", "to": new or "(root)", "files": count}
            for old, new, count in structure.rewrites
        ],
        "levels": structure.levels,
        "folder_fates": fates,
        "folders_split": [asdict(f) for f in structure.folders if f.fate == "split"][:listed],
        "folders_merged": [{"folder": f, "from_folders": n} for f, n in structure.merged[:listed]],
        "decisions": [item.as_payload() for item in result.carried],
        "decisions_followed": followed,
        "decisions_scattered": scattered,
        "decisions_mixed": mixed,
        "decisions_without_evidence": result.no_evidence,
        "decisions_about_moved_files": about_files,
        # A few sentences, information first. The History page shows them while the difference
        # stands, and they block nothing.
        "notice": notice(
            verdict,
            measured.context,
            structure=structure,
            summary=summary,
            sides=sides_of(left, right),
            copy_side=measured.copy_side,
            followed=followed,
            scattered=scattered,
            mixed=mixed,
            thresholds=thresholds,
            named_in_notice=measured.named_in_notice,
        ),
        # The page opens on it where a folder moved, wherever a decision's folder is gone and its
        # files were found elsewhere, whatever the verdict, and wherever files were matched without
        # their sizes, which the notice says.
        "noticed": verdict in NOTICED
        or bool(followed or scattered or mixed or summary.matched_without_size),
        # One line on the folder's own page, keyed by the folder's place as read.
        "folder_lines": folder_lines(result.carried),
    }


def compare_organisation(
    left: Side,
    right: Side,
    *,
    events: Iterable[RecordEvent] = (),
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    minimum_share: float = DEFAULT_MINIMUM_SHARE,
    mtime_grain_ns: int = DEFAULT_MTIME_GRAIN_NS,
    listed: int = LISTED,
    named_in_notice: int = NAMED_IN_NOTICE,
) -> dict[str, Any]:
    """Match, measure, judge, and say where the files under each folder decision went: one
    :func:`measure` and one :func:`report_of` (see each for the arguments)."""
    measured = measure(
        left,
        right,
        thresholds=thresholds,
        mtime_grain_ns=mtime_grain_ns,
        listed=listed,
        named_in_notice=named_in_notice,
    )
    return report_of(measured, events, minimum_share=minimum_share)
