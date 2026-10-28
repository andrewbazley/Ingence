"""Contents are read before inference in every first run.

The class: a first run delivers what the files hold, not only their names. The content step of the
Dashboard's first run is on unless the page says ``false``: the standard depth, the standard
budget's minutes on the wall clock, one file of every series before any series gets a second,
through the read pool. ``ingence index`` says the contents are not read yet and names the command
that reads them. Every name is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ingence.api.apply import DefinitionApplier
from ingence.api.routes import landing
from ingence.cli.main import app
from ingence.core.project import Project
from ingence.storage.catalog import open_catalog
from ingence.storage.commit import read_pointer

PATIENCE = 180.0


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_kelt"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2, 3):
            (pool / folder / f"zib_glorbex_{index:02d}.csv").write_text(f"a,b\n{index},{index}\n")
        (pool / folder / "quolt notes.txt").write_text("brisa and vorn\n")
    return pool


# -- the switch -----------------------------------------------------------------------------------


def test_an_absent_switch_is_on_and_an_explicit_false_is_off() -> None:
    assert landing.dive_from(None) is True
    assert landing.dive_from(True) is True
    assert landing.dive_from(False) is False
    assert landing.dive_from({"enabled": None}) is True
    assert landing.dive_from({"enabled": False}) is False
    with pytest.raises(landing.LandingRefusedError, match="on or off"):
        landing.dive_from("false")


# -- the job --------------------------------------------------------------------------------------


def test_a_first_run_s_content_step_is_standard_on_the_wall_clock_and_series_first(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool]).path
    applier = DefinitionApplier(project, executor="thread")
    try:
        applier.start()
        applier.submit_first_run(dive=True)
        assert applier.wait(PATIENCE)
        said = applier.status()
    finally:
        applier.stop()
    assert said["state"] == "done", said
    assert said["operations"] == ["census", "dive", "inference"]
    assert said["dive_files_read"] >= 2
    pointer = read_pointer(project)
    assert pointer is not None
    with open_catalog(project, run_id=said["dive_run"]) as catalog:
        profile, granted, workers, wall, work = catalog.query(
            "SELECT profile, deepening_budget_granted_seconds, workers, wall_seconds, "
            "work_seconds FROM run"
        ).fetchone()
        started = json.loads(
            catalog.query(
                "SELECT detail FROM journal_events WHERE event = 'run.started'"
            ).fetchone()[0]
        )
        ordered = json.loads(
            catalog.query(
                "SELECT detail FROM journal_events WHERE event = 'spend.ordered'"
            ).fetchone()[0]
        )
    assert profile == "standard"
    assert granted is None, "the wall clock is the budget: no work grant on top of it"
    assert started["total_seconds"] == 900.0
    assert ordered["order"] == "series_first"
    assert workers >= 1
    assert wall is not None and work is not None


def test_an_explicit_false_still_reads_names_and_dates_only(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "vorn-project", created_at="t", roots=[pool]).path
    applier = DefinitionApplier(project, executor="thread")
    try:
        applier.start()
        applier.submit_first_run(dive=landing.dive_from(False))
        assert applier.wait(PATIENCE)
        said = applier.status()
    finally:
        applier.stop()
    assert said["operations"] == ["census", "inference"]


# -- the command line -----------------------------------------------------------------------------


def test_index_says_the_contents_are_not_read_yet_and_names_the_dive(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    made = tmp_path / "project"
    runner = CliRunner()
    done = runner.invoke(app, ["init", str(made), "--root", str(pool)])
    assert done.exit_code == 0, done.output
    done = runner.invoke(app, ["index", "-p", str(made), "--quiet"])
    assert done.exit_code == 0, done.output
    said = " ".join(done.output.split())
    assert "contents have not been read yet" in said
    assert "ingence dive" in said
    assert said.index("not been read yet") < said.index("Next:")
    # Once the contents were read, a walk keeps their readings and says so no more.
    done = runner.invoke(app, ["dive", "-p", str(made), "--budget", "60"])
    assert done.exit_code == 0, done.output
    done = runner.invoke(app, ["index", "-p", str(made), "--quiet"])
    assert done.exit_code == 0, done.output
    said = " ".join(done.output.split())
    assert "not been read yet" not in said
    assert "ingence dive" in said


# -- the route ------------------------------------------------------------------------------------


def test_the_route_reads_an_absent_switch_as_on(tmp_path: Path, monkeypatch) -> None:
    """The page sends ``dive: false`` today; one that stops sending it gets the dive."""
    import urllib.request

    from ingence.api.server import WRITE_HEADER, DashboardServer

    monkeypatch.setattr(DefinitionApplier, "default_executor", "thread")
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool]).path
    server = DashboardServer(project, config_home=tmp_path / "config", warm_search=False)
    server.start()
    try:
        request = urllib.request.Request(
            server.url.rstrip("/") + "/api/landing/first-run",
            data=json.dumps({"years": [2014, "present"]}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", WRITE_HEADER: "1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            assert response.status == 201
            started = json.loads(response.read())
        assert "dive" in started["operations"], started
        assert server.applier is not None
        assert server.applier.wait(PATIENCE)
        assert server.applier.status()["operations"] == ["census", "dive", "inference"]
    finally:
        server.stop()
