"""The landing page's counters, and a project that carries its name.

What the first run's page says while it works is the engine's own count, never a count the page
works out, and never a name or a number from the wrong place:

- the census counts the folders and the files it has found, and the bytes those files hold, as it
  goes, and they end as the counts its run record carries;
- inference counts the rules it has gone through of the rules it will go through;
- the landing page's progress carries all of it for the step in hand;
- a project has a name of its own, written to ``project.json`` when it is made (the folder's name
  where none is given), read from there by any computer that opens it, and changeable.

Every name is invented, and every folder is made by the test.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ingence import SCHEMA_VERSION
from ingence.api.apply import DefinitionApplier
from ingence.api.recent import describe_project, name_of, remember_project
from ingence.api.routes import landing
from ingence.api.server import WRITE_HEADER, WRITE_ROUTES, DashboardServer
from ingence.core.project import PROJECT_NAME_LONGEST, Project
from ingence.jobs.worker import progress_snapshot_path
from ingence.scan.census import run_census
from ingence.scan.progress import ProgressReporter, ProgressSnapshot

PATIENCE = 120.0
CENSUS = "run_20261013T000001Z_aaaaaaaaaaaa"
INFERENCE = "run_20261013T000002Z_bbbbbbbbbbbb"


def _pool(base: Path) -> Path:
    """Folders inside folders, files of known sizes, and a file at the top."""
    pool = base / "pool"
    for outer in ("2024-03-05_kelt", "2024-03-06_kelt"):
        for inner in ("raw", "raw/second", "analysis"):
            folder = pool / outer / inner
            folder.mkdir(parents=True)
            (folder / "zib_glorbex_01.csv").write_text("a,b\n1,2\n")
            (folder / "zib_glorbex_02.csv").write_text("a,b\n1,2\n3,4\n")
    (pool / "tamen notes.txt").write_text("twelve bytes")
    return pool


def _reporter(run: str, stage: str, path: Path | None = None) -> ProgressReporter:
    return ProgressReporter(ProgressSnapshot(run_id=run, stage=stage), path=path)


# -- the census counts as it goes -------------------------------------------------------------


def test_the_census_counts_its_folders_files_and_bytes_and_they_end_as_its_run_record_has_them(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    reporter = _reporter(CENSUS, "census")
    report = run_census(project, run_id=CENSUS, started_at="t", reporter=reporter)
    seen = reporter.read()
    counts = report.counts
    assert counts.directories >= 8 and counts.files == 13
    assert (seen.folders_found, seen.files_found, seen.bytes_seen) == (
        counts.directories,
        counts.files,
        counts.bytes_seen,
    )
    # The bytes are the files' own, counted from the folders the test made.
    written = sum(path.stat().st_size for path in pool.rglob("*") if path.is_file())
    assert seen.bytes_seen == written
    # ``entries_seen`` counts listings finished, and a folder can take more than one. It is not the
    # number of folders found (here 12 listings of 9 folders), which is why ``directories_seen`` is
    # kept apart.
    assert seen.entries_seen == counts.entries
    assert seen.directories_seen >= counts.directories


def test_the_snapshot_file_carries_the_new_counts(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    written = progress_snapshot_path(project.path, CENSUS)
    reporter = _reporter(CENSUS, "census", written)
    run_census(project, run_id=CENSUS, started_at="t", reporter=reporter)
    reporter.emit()
    held = json.loads(written.read_text(encoding="utf-8"))
    assert held["files_found"] == 13 and held["folders_found"] >= 8 and held["bytes_seen"] > 0
    # What the landing page reads of it.
    seen = landing._snapshot(project.path, CENSUS)
    assert seen is not None
    assert seen["files_found"] == 13 and seen["bytes_seen"] == held["bytes_seen"]
    assert "rules_total" in seen and seen["rules_total"] is None


# -- inference counts its rules ---------------------------------------------------------------


def test_inference_counts_the_rules_it_goes_through_of_the_rules_it_will_go_through(
    tmp_path: Path,
) -> None:
    from ingence.infer.run import run_inference

    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    reporter = _reporter(INFERENCE, "inferring")
    assert reporter.read().rules_total is None and reporter.read().rules_run == 0
    run_inference(project, run_id=INFERENCE, started_at="t", reporter=reporter)
    seen = reporter.read()
    assert seen.rules_total is not None and seen.rules_total >= 50
    # Every rule is gone through once, the ones passed over included: the whole is reached.
    assert seen.rules_run == seen.rules_total


def test_a_pass_with_no_one_watching_counts_nothing_and_changes_nothing(tmp_path: Path) -> None:
    from ingence.infer.run import run_inference
    from ingence.storage.commit import read_pointer

    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFERENCE, started_at="t")
    pointer = read_pointer(project.path)
    assert pointer is not None and pointer.run_id == INFERENCE


# -- the landing page's progress -------------------------------------------------------------


class _Applier:
    """An applier that is in the middle of one step, as the ledger would say."""

    def __init__(self, status: dict[str, Any]) -> None:
        self._status = status

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def jobs(self, limit: int) -> list[Any]:
        del limit
        return []


def test_the_progress_of_each_step_carries_that_step_s_counts(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool]).path
    census = _reporter(CENSUS, "census", progress_snapshot_path(project, CENSUS))
    census.advance(entries_seen=40, folders_found=7, files_found=33, bytes_seen=1234)
    census.emit()
    inference = _reporter(INFERENCE, "inferring", progress_snapshot_path(project, INFERENCE))
    inference.set(rules_total=120)
    inference.advance(rules_run=30)
    inference.emit()
    now = landing.first_run_progress(
        project, _Applier({"state": "running", "phase": "census", "census_run": CENSUS})
    )
    assert now["seen"]["folders_found"] == 7 and now["seen"]["bytes_seen"] == 1234
    later = landing.first_run_progress(
        project, _Applier({"state": "running", "phase": "inferring", "inferring_run": INFERENCE})
    )
    assert (later["seen"]["rules_run"], later["seen"]["rules_total"]) == (30, 120)


def test_a_first_run_hands_inference_a_reporter_and_removes_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingence.api.apply as apply

    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool]).path
    held: dict[str, Any] = {}
    real = apply.run_inference

    def watched(*args: Any, **kwargs: Any) -> Any:
        report = real(*args, **kwargs)
        reporter = kwargs.get("reporter")
        held["handed"] = reporter is not None
        if reporter is not None:
            reporter.emit()
            held["file"] = json.loads(reporter.path.read_text(encoding="utf-8"))
            held["path"] = reporter.path
        return report

    monkeypatch.setattr(apply, "run_inference", watched)
    applier = DefinitionApplier(project, executor="thread")
    try:
        applier.start()
        applier.submit_first_run(dive=False)
        assert applier.wait(PATIENCE)
        assert applier.status()["state"] == "done"
    finally:
        applier.stop()
    assert held["handed"] is True
    assert held["file"]["rules_total"] and held["file"]["rules_run"] == held["file"]["rules_total"]
    assert not held["path"].exists(), "the snapshot goes with the run published"


# -- a project carries its name ---------------------------------------------------------------


def test_a_project_holds_a_name_only_when_a_person_gave_one(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path)
    plain = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool])
    # Nobody named it: the folder names it, and the file holds no name at all, so a build that
    # does not know the field still reads it.
    assert plain.name == "kelt-project"
    assert "name" not in json.loads((plain.path / "project.json").read_text())
    named = Project.create(
        tmp_path / "other", created_at="t", roots=[pool], name="  Kelt   study of cells "
    )
    assert named.name == "Kelt study of cells"
    assert json.loads((named.path / "project.json").read_text())["name"] == "Kelt study of cells"
    # Read back from the file by whoever opens it, wherever the folder lies.
    moved = (tmp_path / "other").rename(tmp_path / "elsewhere")
    assert Project.open(moved).name == "Kelt study of cells"


def test_init_names_the_project_or_the_folder_does(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ingence.cli.main import app

    pool = _pool(tmp_path)
    runner = CliRunner()
    named = runner.invoke(
        app, ["init", str(tmp_path / "first"), "--root", str(pool), "--name", "Kelt study"]
    )
    assert named.exit_code == 0, named.output
    assert "Kelt study" in named.output
    assert Project.open(tmp_path / "first").name == "Kelt study"
    plain = runner.invoke(app, ["init", str(tmp_path / "second"), "--root", str(pool)])
    assert plain.exit_code == 0, plain.output
    assert Project.open(tmp_path / "second").name == "second"
    assert "name" not in json.loads((tmp_path / "second" / "project.json").read_text())
    long = runner.invoke(
        app,
        ["init", str(tmp_path / "third"), "--root", str(pool), "--name", "k" * 201],
    )
    assert long.exit_code == 1 and "at most" in long.output
    assert not (tmp_path / "third").exists()


def test_a_name_too_long_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    with pytest.raises(ValueError, match="at most"):
        Project.create(
            tmp_path / "long", created_at="t", roots=[pool], name="k" * (PROJECT_NAME_LONGEST + 1)
        )
    assert not (tmp_path / "long").exists()


def test_a_project_made_before_names_shows_its_folder_s_name_or_the_one_this_machine_kept(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path)
    made = Project.create(tmp_path / "old-project", created_at="t", roots=[pool])
    # As an earlier build left it: schema 1.22, which had no names.
    identity = json.loads((made.path / "project.json").read_text())
    assert "name" not in identity
    (made.path / "project.json").write_text(json.dumps({**identity, "schema_version": "1.22"}))
    assert Project.open(made.path).name == "old-project"
    home = tmp_path / "config"
    assert name_of(made.path, home) == "old-project"
    remember_project(made.path, opened_at="t", name="Old study", config_home=home)
    assert name_of(made.path, home) == "Old study"
    assert describe_project(made.path, listed_name="Old study")["name"] == "Old study"
    # A name in the project's own file wins over the one this machine kept.
    made_again = Project.open(made.path).rename("Kelt study")
    assert name_of(made_again.path, home) == "Kelt study"
    # And the file, holding a field only schema 1.23 has, says it is a 1.23 file.
    assert json.loads((made.path / "project.json").read_text())["schema_version"] == SCHEMA_VERSION
    assert describe_project(made.path, listed_name="Old study")["name"] == "Kelt study"


def test_the_landing_page_makes_and_renames_a_project_by_its_name(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    made = landing.create_project(
        {"root": str(pool), "name": "Kelt study", "project": str(tmp_path / "projects" / "kelt")},
        created_at="2026-10-13T00:00:00Z",
    )
    # The name the person typed, not the folder's: the folder is made safe for every system.
    assert Project.open(made).name == "Kelt study"
    again = landing.rename_project(made, "  Kelt   cells  ")
    assert Project.open(again).name == "Kelt cells"
    assert (made / "settings.yaml").is_file(), "only the name changes"
    with pytest.raises(landing.LandingRefusedError, match="Give the project a name"):
        landing.rename_project(made, "   ")
    with pytest.raises(landing.LandingRefusedError, match="at most"):
        landing.rename_project(made, "k" * (PROJECT_NAME_LONGEST + 1))
    with pytest.raises(landing.LandingRefusedError, match="not an Ingence project"):
        landing.rename_project(tmp_path / "nowhere", "kelt")


def _post(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", WRITE_HEADER: "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_rename_route_is_a_named_guarded_write_and_changes_only_the_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "/api/landing/rename" in WRITE_ROUTES
    monkeypatch.setattr(DefinitionApplier, "default_executor", "thread")
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "kelt", created_at="t", roots=[pool], name="Kelt").path
    server = DashboardServer(None, config_home=tmp_path / "config", warm_search=False)
    server.start()
    try:
        base = server.url.rstrip("/")
        status, said = _post(
            base + "/api/landing/rename", {"project": str(project), "name": "Vorn"}
        )
        assert status == 200, said
        assert said["project"]["name"] == "Vorn"
        assert Project.open(project).name == "Vorn"
        status, said = _post(base + "/api/landing/rename", {"project": str(project), "name": ""})
        assert status == 400 and "Give the project a name" in said["error"]
        status, said = _post(base + "/api/landing/rename", {"name": "Vorn"})
        assert status == 400 and "which project" in said["error"]
        assert Project.open(project).name == "Vorn"
    finally:
        server.stop()
