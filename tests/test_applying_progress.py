"""``/api/applying`` since 0.4.0: every phase with a denominator where one is known, the rate and
the time left once enough is done, the contract's phase words beside the ledger's, and the landing
page's depth. Every name is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingence.api.apply import PHASE_OF, phase_counts, progress_keys, status_from_ledger
from ingence.api.routes import landing
from ingence.core.project import Project
from ingence.jobs import JobState
from ingence.jobs.ledger import JobLedger
from ingence.jobs.worker import progress_snapshot_path


def test_the_phase_words_are_the_contract_s() -> None:
    assert PHASE_OF == {
        "census": "listing",
        "dive": "reading",
        "binding": "names",
        "inferring": "rules",
        "views": "views",
    }
    labels = {phase: progress_keys(phase)["phase_label"] for phase in PHASE_OF.values()}
    assert labels == {
        "listing": "Listing files",
        "reading": "Reading contents",
        "names": "Reading names",
        "rules": "Applying rules",
        "views": "Preparing views",
    }
    assert progress_keys("done", ended=True)["phase_label"] == "Done"
    assert progress_keys("failed", ended=True)["phase_label"] == "Did not finish"
    assert progress_keys("cancelled", ended=True)["phase_label"] == "Stopped"


def test_counts_rate_and_time_left_come_from_the_snapshot() -> None:
    early = progress_keys(
        "rules",
        ("inference",),
        running=True,
        counts={"rules_run": 2, "rules_total": 100, "elapsed_seconds": 4.0},
    )
    assert early["unit"] == "rules" and (early["done"], early["total"]) == (2, 100)
    assert early["rate_per_s"] == 0.5
    assert early["seconds_left"] is None, "nothing is estimated before 5% is done"
    assert early["can_stop"] is True and early["stop_keeps"] is False
    later = progress_keys(
        "rules",
        ("inference",),
        running=True,
        counts={"rules_run": 25, "rules_total": 100, "elapsed_seconds": 10.0},
    )
    assert later["seconds_left"] == 30.0 and later["rate_per_s"] == 2.5
    listing = progress_keys(
        "listing", ("census", "inference"), running=True, counts={"files_found": 1204}
    )
    assert listing["unit"] == "files" and listing["done"] == 1204 and listing["total"] is None
    reading = progress_keys("reading", ("census", "dive", "inference"), running=True, counts={})
    from ingence.jobs import STOP_KEEPS

    assert STOP_KEEPS is True, "a Stop while reading contents keeps what was read"
    assert reading["stop_keeps"] is True and reading["can_stop"] is True
    assert progress_keys("rules", ("inference",), running=False)["can_stop"] is False
    views = progress_keys(
        "views", ("inference",), running=True, counts={"views_done": 3, "views_total": 6}
    )
    assert views["unit"] == "views" and (views["done"], views["total"]) == (3, 6)
    assert views["step"] == 2 and views["steps"] == 2


def test_the_status_reads_the_running_phase_s_snapshot(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    (pool / "a").mkdir(parents=True)
    (pool / "a" / "x.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    ledger = JobLedger(project.path)
    job = ledger.enqueue(steps=["inference"], record_sequence=None, base_run=None)
    claimed = ledger.claim_next(pinned=None)
    assert claimed is not None and claimed.job_id == job.job_id
    run_id = "run_20260101T000009Z_cccccccccccc"
    assert ledger.heartbeat(
        job.job_id, attempt=1, phase="inferring", progress={"inferring_run": run_id}
    )
    snapshot = progress_snapshot_path(project.path, run_id)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps(
            {"stage": "inferring", "rules_run": 50, "rules_total": 100, "elapsed_seconds": 20.0}
        )
    )
    status = status_from_ledger(ledger)
    assert status["phase"] == "rules" and status["phase_legacy"] == "inferring"
    assert status["phase_label"] == "Applying rules"
    assert (status["done"], status["total"], status["unit"]) == (50, 100, "rules")
    assert status["seconds_left"] == 20.0 and status["rate_per_s"] == 2.5
    assert status["can_stop"] is True and "waiting_for" not in status
    assert phase_counts(project.path, "rules", {"inferring_run": run_id}) is not None
    assert phase_counts(project.path, "rules", {}) is None
    ledger.finish(job.job_id, attempt=1, state=JobState.DONE, produced_run=run_id)
    ended = status_from_ledger(ledger)
    assert ended["phase"] == "done" and ended["can_stop"] is False and ended["done"] is None


def test_the_landing_page_reads_the_depth_and_proposes_2013(tmp_path: Path) -> None:
    assert landing.DEFAULT_YEARS == (2013, "present")
    assert landing.depth_from("quick") == "census" and landing.depth_from("deep") == "deep"
    assert landing.depth_from(None) is None
    with pytest.raises(landing.LandingRefusedError):
        landing.depth_from("bottomless")
    with pytest.raises(landing.LandingRefusedError):
        landing.time_limit_from(0)
    # An absent dive means on at any depth but quick; a dive said wins.
    assert landing.dive_asked({}) is True
    assert landing.dive_asked({"depth": "standard"}) is True
    assert landing.dive_asked({"depth": "quick"}) is False
    assert landing.dive_asked({"depth": "deep", "dive": False}) is False
    assert landing.dive_asked({"dive": {"enabled": True}}) is True
    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    landing.save_depth(project.path, {"depth": "deep", "time_limit_seconds": 90})
    settings = Project.open(project.path).settings
    assert settings.depth == "deep" and settings.extraction.total_seconds == 90.0
    landing.save_depth(project.path, {"depth": "quick"})
    assert Project.open(project.path).settings.depth == "census"
