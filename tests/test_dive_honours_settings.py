"""A dive honours a person's extraction settings.

``extraction.include_extensions``, ``exclude_extensions`` and ``max_file_bytes`` are a person's
deliberate configuration. A person who excluded an extension, or set a size limit, does not have
such a file opened. The dive sets the file aside unread, records it as skipped with its own
reason, and counts it apart from content that cannot be read (``skipped_by_policy``). A person's
``no`` and ``never`` answers on the Coverage page are review verdicts and only order a dive's
spend.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ingence.api.routes.coverage import coverage
from ingence.core.project import Project
from ingence.core.schema import ScanOutcome
from ingence.core.settings import ExtractionSettings, Settings
from ingence.scan.census import run_census
from ingence.scan.deepen import (
    ABOVE_SIZE_LIMIT,
    EXTENSION_EXCLUDED,
    EXTENSION_NOT_INCLUDED,
    run_deepen,
)
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

_CENSUS = "run_20260101T000001Z_111111111111"
_DIVE = "run_20260101T000002Z_222222222222"
_AGAIN = "run_20260101T000003Z_333333333333"


def _pool(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn.csv").write_text("sample,dose\n1,5\n2,6\n")
    (pool / "kelt" / "zib.md").write_text("# Brisa\n\nSome prose about the trell runs.\n" * 30)
    (pool / "kelt" / "empty.txt").write_text("")
    return pool


def _dived(tmp_path: Path, *, large: bool = False, **extraction: object) -> tuple[Project, object]:
    pool = _pool(tmp_path)
    if large:  # larger than the standard depth's sample, so a deep pass would read it again
        (pool / "kelt" / "trell.log").write_text("brisa kelt vorn zib\n" * 230_000)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(**extraction)),  # type: ignore[arg-type]
    )
    run_census(project, run_id=_CENSUS, started_at="t")
    report = run_deepen(project, run_id=_DIVE, started_at="t", budget_seconds=60.0)
    return project, report


def _with_extraction(project: Project, **changed: object) -> Project:
    """The project with its settings file saying ``changed`` of extraction, as a person edits it."""
    settings = project.settings
    return project.save_settings(
        settings.model_copy(update={"extraction": settings.extraction.model_copy(update=changed)})
    )


def _outcomes(project: Project) -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT name, scan_outcome FROM entries WHERE entry_type = 'file'"
        ).fetchall()
    return {str(name): str(outcome) for name, outcome in rows}


def _set_aside_rows(project: Project) -> list[dict[str, object]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT relative_path, detail FROM journal_events WHERE event = 'entry.set_aside' "
            "ORDER BY sequence"
        ).fetchall()
    return [{"relative_path": path, **json.loads(detail)} for path, detail in rows]


def test_an_excluded_extension_is_set_aside_unread_with_its_reason(tmp_path: Path) -> None:
    project, report = _dived(tmp_path, exclude_extensions=(".CSV",))
    outcomes = _outcomes(project)
    assert outcomes["vorn.csv"] == ScanOutcome.SKIPPED_BY_SETTINGS.value
    assert outcomes["zib.md"] == ScanOutcome.EXTRACTED.value
    assert report.set_aside_by_settings == {EXTENSION_EXCLUDED: 1}  # type: ignore[attr-defined]
    # Counted apart from content that cannot be read: the empty file is that, and only that.
    assert outcomes["empty.txt"] == ScanOutcome.SKIPPED_BY_POLICY.value
    assert report.skipped == 1  # type: ignore[attr-defined]
    (row,) = _set_aside_rows(project)
    assert row["because"] == EXTENSION_EXCLUDED == "the settings exclude this extension"
    assert row["relative_path"] == "kelt/vorn.csv"


def test_a_file_above_the_size_limit_is_set_aside_unread(tmp_path: Path) -> None:
    project, report = _dived(tmp_path, max_file_bytes=200)
    outcomes = _outcomes(project)
    assert outcomes["zib.md"] == ScanOutcome.SKIPPED_BY_SETTINGS.value
    assert outcomes["vorn.csv"] == ScanOutcome.EXTRACTED.value
    assert report.set_aside_by_settings == {ABOVE_SIZE_LIMIT: 1}  # type: ignore[attr-defined]
    assert ABOVE_SIZE_LIMIT == "above the settings' size limit"


def test_an_include_list_sets_aside_every_other_extension(tmp_path: Path) -> None:
    project, report = _dived(tmp_path, include_extensions=("md",))
    outcomes = _outcomes(project)
    assert outcomes["zib.md"] == ScanOutcome.EXTRACTED.value
    assert outcomes["vorn.csv"] == ScanOutcome.SKIPPED_BY_SETTINGS.value
    # Set aside by the settings before any reading, the empty file too: the settings speak first.
    assert report.set_aside_by_settings == {EXTENSION_NOT_INCLUDED: 2}  # type: ignore[attr-defined]


def test_a_file_set_aside_is_read_once_the_setting_is_lifted(tmp_path: Path) -> None:
    project, _ = _dived(tmp_path, exclude_extensions=("csv",))
    project = _with_extraction(project, exclude_extensions=())
    report = run_deepen(project, run_id=_AGAIN, started_at="t", budget_seconds=60.0)
    assert _outcomes(project)["vorn.csv"] == ScanOutcome.EXTRACTED.value
    assert report.set_aside_by_settings == {}  # type: ignore[attr-defined]


def test_a_read_file_newly_excluded_keeps_its_reading(tmp_path: Path) -> None:
    """A deeper pass does not reopen a file the settings now exclude; what it read stands."""
    project, _ = _dived(tmp_path, large=True)
    assert _outcomes(project)["trell.log"] == ScanOutcome.EXTRACTED.value
    with open_catalog(project.path) as catalog:
        before = catalog.query(
            "SELECT count(*) FROM observations o JOIN entries e USING (entry_id) "
            "WHERE e.name = 'trell.log'"
        ).fetchone()
    project = _with_extraction(project, exclude_extensions=("log",))
    report = run_deepen(project, run_id=_AGAIN, started_at="t", budget_seconds=60.0, profile="deep")
    assert report.set_aside_by_settings == {EXTENSION_EXCLUDED: 1}  # type: ignore[attr-defined]
    assert report.chosen_because.get("deeper", 0) == 0  # type: ignore[attr-defined]
    assert _outcomes(project)["trell.log"] == ScanOutcome.EXTRACTED.value
    with open_catalog(project.path) as catalog:
        after = catalog.query(
            "SELECT count(*) FROM observations o JOIN entries e USING (entry_id) "
            "WHERE e.name = 'trell.log'"
        ).fetchone()
    assert after == before


def test_the_coverage_route_counts_the_settings_apart_from_unreadable_content(
    tmp_path: Path,
) -> None:
    project, _ = _dived(tmp_path, exclude_extensions=("csv",))
    with open_catalog(project.path) as catalog:
        reached = coverage(catalog, project.path)["reached"]
    assert reached["files"] == 3
    assert reached["set_aside_by_settings"] == 1
    assert reached["set_aside_by_settings_because"] == {EXTENSION_EXCLUDED: 1}
    assert reached["unreadable"] == 1
    # Set aside unread is not opened; unreadable content was looked at and found empty.
    assert reached["opened"] == 2


def test_the_coverage_list_of_untyped_extensions_counts_a_set_aside_file_unopened(
    tmp_path: Path,
) -> None:
    """The Coverage view's list of untyped extensions counts a file that the settings set aside
    unread as unopened, though it is not ``metadata_only`` or ``deferred``."""
    from ingence.api.routes.pages import _extension_rows

    pool = _pool(tmp_path)
    (pool / "kelt" / "brisa.qqz").write_text("trell\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(exclude_extensions=("qqz",))),
    )
    run_census(project, run_id=_CENSUS, started_at="t")
    run_deepen(project, run_id=_DIVE, started_at="t", budget_seconds=60.0)
    assert _outcomes(project)["brisa.qqz"] == ScanOutcome.SKIPPED_BY_SETTINGS.value
    with open_catalog(project.path) as catalog:
        rows, _ = _extension_rows(catalog, project.path, None)
    (row,) = [row for row in rows if row["extension"] == "qqz"]
    assert (row["files"], row["opened"]) == (1, 0)


# -- digests and container members under the extraction filters --------------------------------


def _probe_pool(tmp_path: Path, **settings: object) -> Project:
    import zipfile

    from ingence.core.settings import HashSettings

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    body = b"vorn,trell\n" + b"1,2\n" * 400
    (pool / "kelt" / "a.csv").write_bytes(body)
    (pool / "kelt" / "b.csv").write_bytes(body)  # the same bytes as a.csv
    (pool / "kelt" / "note.md").write_text("# Brisa\n" * 20)
    with zipfile.ZipFile(pool / "kelt" / "bundle.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("inner.csv", "vorn,trell\n1,2\n3,4\n")
        archive.writestr("inner.md", "# Zib\n" * 3000)  # large once extracted, small packed
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(
            hash=HashSettings(**settings.pop("hashing", {})),  # type: ignore[arg-type]
            extraction=ExtractionSettings(**settings),  # type: ignore[arg-type]
        ),
    )
    run_census(project, run_id=_CENSUS, started_at="t")
    run_deepen(project, run_id=_DIVE, started_at="t", budget_seconds=60.0)
    return project


def _methods(project: Project) -> dict[str, tuple[str, set[str]]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, e.scan_outcome, "
            "list(DISTINCT o.identity_method) FILTER (WHERE o.identity_method IS NOT NULL) "
            "FROM entries e LEFT JOIN observations o USING (entry_id) "
            "WHERE e.entry_type = 'file' GROUP BY ALL"
        ).fetchall()
    return {str(name): (str(outcome), set(methods or ())) for name, outcome, methods in rows}


def test_a_file_the_settings_set_aside_still_takes_the_digests_need_asks(tmp_path: Path) -> None:
    """The extraction filters govern reading; the hash filters govern digests. Two files of one
    content set aside by an excluded extension still take the digests that compare them."""
    project = _probe_pool(tmp_path, exclude_extensions=("csv",))
    methods = _methods(project)
    for name in ("a.csv", "b.csv"):
        outcome, digests = methods[name]
        assert outcome == ScanOutcome.SKIPPED_BY_SETTINGS.value
        assert digests == {"blake3_quick", "blake3_whole"}, (name, digests)


def test_a_file_the_settings_set_aside_takes_a_person_s_mode_digest(tmp_path: Path) -> None:
    from ingence.core.settings import HashMode

    project = _probe_pool(tmp_path, exclude_extensions=("csv",), hashing={"mode": HashMode.FULL})
    methods = _methods(project)
    assert methods["a.csv"] == (ScanOutcome.SKIPPED_BY_SETTINGS.value, {"blake3_whole"})


def test_a_container_member_the_settings_exclude_is_set_aside_unread(tmp_path: Path) -> None:
    """A member of an archive that the settings exclude is set aside unread.

    It is judged by its own extension and its extracted size, as a file on disk is."""
    project = _probe_pool(tmp_path, exclude_extensions=("csv",))
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, e.scan_outcome, count(o.key) FILTER (WHERE o.key LIKE 'tabular%') "
            "FROM entries e LEFT JOIN observations o USING (entry_id) "
            "WHERE e.entry_kind = 'derived' GROUP BY ALL"
        ).fetchall()
    found = {str(name): (str(outcome), int(read)) for name, outcome, read in rows}
    assert found["inner.csv"] == (ScanOutcome.SKIPPED_BY_SETTINGS.value, 0)
    assert found["inner.md"][0] == ScanOutcome.EXTRACTED.value


def test_a_container_member_above_the_size_limit_is_set_aside_unread(tmp_path: Path) -> None:
    project = _probe_pool(tmp_path, max_file_bytes=1000)
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT name, scan_outcome FROM entries WHERE entry_kind = 'derived'"
        ).fetchall()
    found = {str(name): str(outcome) for name, outcome in rows}
    # The archive is a few hundred bytes; inner.md is 3,000 short lines once extracted.
    assert found["inner.md"] == ScanOutcome.SKIPPED_BY_SETTINGS.value
    assert found["inner.csv"] == ScanOutcome.EXTRACTED.value
