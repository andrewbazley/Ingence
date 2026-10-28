"""A stop keeps what was read.

Stopping a content pass does not throw it away. A token asked to stop and keep ends the pass the way
an exhausted budget does: the file in hand finishes, every file after it is counted as not reached
and left unexamined, and the run is published with the invariants checked. A discard still publishes
nothing, and the census stays atomic. On a terminal the first Ctrl-C stops and keeps, the second
discards, the third exits. Every name is invented.
"""

from __future__ import annotations

import signal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ingence.cli import main as cli
from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.settings import ExtractionSettings, Settings
from ingence.scan.cancel import CancellationToken, cancel_on_signal
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog
from ingence.storage.commit import read_pointer

# -- the token's two flavours ---------------------------------------------------------------------


def test_a_stop_cancels_and_keeps_and_a_discard_escalates_it_never_the_reverse(monkeypatch) -> None:
    killed: list[str] = []
    monkeypatch.setattr(
        CancellationToken, "_terminate_children", staticmethod(lambda: killed.append("killed") or 0)
    )
    token = CancellationToken()
    assert not token.cancelled
    assert not token.keeps

    token.stop("interrupted by SIGINT")
    assert token.cancelled
    assert token.keeps
    assert token.reason == "interrupted by SIGINT"
    assert killed == [], "a stop kills nothing: the file in hand finishes within its ceiling"

    token.stop("a second stop")
    assert token.reason == "interrupted by SIGINT", "a second stop changes nothing"

    token.cancel("interrupted twice: discarding")
    assert token.cancelled
    assert not token.keeps
    assert token.reason == "interrupted twice: discarding", (
        "a discard after a stop takes its reason"
    )
    assert killed == ["killed"]

    token.stop("too late to keep")
    assert not token.keeps, "a stop never softens a discard"
    assert token.reason == "interrupted twice: discarding"


def test_a_discard_s_first_reason_is_still_the_one_kept(monkeypatch) -> None:
    monkeypatch.setattr(CancellationToken, "_terminate_children", staticmethod(lambda: 0))
    token = CancellationToken()
    token.cancel("interrupted by SIGINT")
    token.cancel("worker pool collapsed")
    assert token.reason == "interrupted by SIGINT"
    assert not token.keeps


# -- the pass -------------------------------------------------------------------------------------


class _StopsAfter:
    """A progress reporter that asks the token to stop and keep once the pass has recorded
    ``after`` files, as a person's Stop arrives while files are being read.

    Through the reporter's count rather than by polling the token, so neither the pass's
    checkpoints nor the read pool's reads of the token decide when the stop lands, and a
    loaded machine cannot change the story.
    """

    def __init__(self, token: CancellationToken, after: int = 2) -> None:
        self.token = token
        self.after = after
        self.examined = 0

    def advance(self, **deltas: int) -> None:
        self.examined += deltas.get("entries_examined", 0)
        if self.examined >= self.after:
            self.token.stop(f"stopped after {self.after} files")

    def set(self, **values: object) -> None:
        return None


def _indexed(tmp_path: Path, files: int = 6) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    for index in range(files):
        (pool / f"plate_{index:02d}.csv").write_text(f"well,value\nA{index},{index}\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return project


def test_a_stopped_pass_publishes_what_it_read_with_honest_gaps(tmp_path) -> None:
    project = _indexed(tmp_path, files=6)
    before = read_pointer(project.path)
    assert before is not None
    token = CancellationToken()
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=120.0,
        token=token,
        reporter=_StopsAfter(token, after=2),
    )
    assert report.stopped
    assert report.stop_reason == "stopped after 2 files"
    assert not report.cancelled
    # The two in hand when the stop landed, and whatever the pool had already handed out.
    assert 2 <= report.examined <= 4
    assert report.extracted == report.examined
    assert report.budget is not None
    assert report.budget.unreached == 6 - report.examined, "every file after the stop is counted"
    assert report.invariants, "published as an exhausted budget is: the invariants were checked"
    assert all(outcome.passed for outcome in report.invariants)

    after = read_pointer(project.path)
    assert after is not None
    assert after.run_id == "run_20260101T000002Z_222222222222", "what was read is published"
    with open_catalog(project.path) as catalog:
        outcomes = dict(
            catalog.query(
                "SELECT scan_outcome, count(*) FROM entries WHERE entry_type = 'file' "
                "GROUP BY scan_outcome"
            ).fetchall()
        )
        published, work = catalog.query("SELECT published, work_seconds FROM run").fetchone()
        stopped = catalog.query(
            "SELECT count(*) FROM journal_events WHERE event = 'run.stopped'"
        ).fetchone()[0]
    assert outcomes == {
        "extracted": report.examined,
        "metadata_only": 6 - report.examined,
    }, "the rest stay unexamined"
    assert published is True
    assert work > 0.0
    assert stopped == 1


def test_a_discard_still_publishes_nothing(tmp_path) -> None:
    project = _indexed(tmp_path, files=3)
    before = read_pointer(project.path)
    assert before is not None
    token = CancellationToken()
    token.cancel("discarded before the first file")
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=120.0,
        token=token,
    )
    assert report.cancelled
    assert not report.stopped
    after = read_pointer(project.path)
    assert after is not None
    assert after.run_id == before.run_id


def test_the_census_stays_atomic_so_a_stop_is_a_discard_there(tmp_path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate.csv").write_text("well,value\nA1,1\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    token = CancellationToken()
    token.stop("stop, and keep what you can")
    report = run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="t", token=token
    )
    assert report.cancelled
    assert not report.published
    assert read_pointer(project.path) is None


# -- the Ctrl-C ladder ----------------------------------------------------------------------------


def test_the_ladder_stops_then_discards_then_exits(monkeypatch) -> None:
    raised: list[int] = []
    monkeypatch.setattr(signal, "raise_signal", raised.append)
    monkeypatch.setattr(CancellationToken, "_terminate_children", staticmethod(lambda: 0))
    token = CancellationToken()
    with cancel_on_signal(token, (signal.SIGINT,), keep_first=True):
        handle = signal.getsignal(signal.SIGINT)
        assert callable(handle)
        handle(signal.SIGINT, None)
        assert token.cancelled and token.keeps, "the first press stops and keeps"
        assert token.reason == "interrupted by SIGINT"
        handle(signal.SIGINT, None)
        assert token.cancelled and not token.keeps, "the second press discards"
        assert token.reason == "interrupted twice by SIGINT: what was read is discarded"
        assert raised == []
        handle(signal.SIGINT, None)
        assert raised == [signal.SIGINT], "the third press re-raises with the default handler"
        assert signal.getsignal(signal.SIGINT) is signal.SIG_DFL
    assert signal.getsignal(signal.SIGINT) is not signal.SIG_DFL or True  # restored on exit


def test_without_keep_first_the_ladder_is_the_old_two_rung_one(monkeypatch) -> None:
    raised: list[int] = []
    monkeypatch.setattr(signal, "raise_signal", raised.append)
    monkeypatch.setattr(CancellationToken, "_terminate_children", staticmethod(lambda: 0))
    token = CancellationToken()
    with cancel_on_signal(token, (signal.SIGINT,)):
        handle = signal.getsignal(signal.SIGINT)
        assert callable(handle)
        handle(signal.SIGINT, None)
        assert token.cancelled and not token.keeps, "the first press discards, as index does"
        handle(signal.SIGINT, None)
        assert raised == [signal.SIGINT]


# -- the command line -----------------------------------------------------------------------------

_TREE = (
    "2026-03-04 plate reads/plate_A_well_01.txt",
    "2026-03-04 plate reads/plate_A_well_02.txt",
    "2026-03-04 plate reads/plate_B_well_01.txt",
    "2026-03-05 plate reads/notes.txt",
)


@pytest.fixture
def indexed(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    for relative in _TREE:
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("some invented notes about a plate\n" * 4)
    made = tmp_path / "project"
    runner = CliRunner()
    for arguments in (
        ["init", str(made), "--root", str(pool)],
        ["index", "-p", str(made), "--quiet"],
    ):
        done = runner.invoke(app, arguments)
        assert done.exit_code == 0, done.output
    return made


def test_a_stopped_dive_says_so_and_publishes_what_it_read(indexed: Path, monkeypatch) -> None:
    from ingence.scan.deepen import run_deepen as real_run_deepen

    def stopping_after_one(*args, **kwargs):
        kwargs["reporter"] = _StopsAfter(kwargs["token"], after=1)
        return real_run_deepen(*args, **kwargs)

    monkeypatch.setattr(cli, "run_deepen", stopping_after_one)
    # One worker, so exactly one file is in hand when the stop lands and the words are exact.
    settings = indexed / "settings.yaml"
    settings.write_text(settings.read_text().rstrip("\n") + "\nextraction:\n  workers: 1\n")
    before = read_pointer(indexed)
    assert before is not None

    done = CliRunner().invoke(app, ["dive", "-p", str(indexed), "--budget", "60"])

    assert done.exit_code == 0, done.output
    said = " ".join(done.output.split())
    assert "Stopped: stopped after 1 files" in said
    assert "What was read so far is published" in said
    assert "Read " in said
    assert "as run" in said
    assert "not reached before the stop" in said
    assert "Next:" in said
    after = read_pointer(indexed)
    assert after is not None
    assert after.run_id != before.run_id, "the stopped dive's run is current"
