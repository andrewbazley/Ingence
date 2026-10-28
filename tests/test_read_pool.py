"""The read pool: files read in parallel, written in spend order.

A pool of workers reads what one reader read, in the order it was asked, and one bad file costs one
file. Each worker is spawned once and runs the whole per-file read. The parent hands out work in
order and is the only writer. A worker that hangs is killed at the file's deadline, a worker that
dies is replaced, and the file is recorded with the stage it was in. Every name here is invented.
The hostile readers are the fixtures the supervisor is tested with, ported to the pool.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from fixtures.hostile import HOSTILE_SUFFIX, register_all
from ingence.core.schema import Entry, EntryType, ScanOutcome
from ingence.core.settings import ExtractionSettings, Settings
from ingence.executors.pool import (
    SHARE_WORKER_CAP,
    ReadContext,
    ReadPool,
    ReadTask,
    SerialReader,
    auto_workers,
    extra_modules,
    worker_count,
)
from ingence.executors.supervisor import live_child_count, terminate_all_children
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS
from ingence.scan.cancel import CancellationToken

GIB = 1 << 30


@pytest.fixture(scope="module", autouse=True)
def _fixtures_registered() -> object:
    """The hostile readers, registered for this module only (see ``test_supervisor``)."""
    names = register_all()
    yield names
    for name in names:
        EXTRACTORS.unregister(name)


@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test() -> object:
    yield
    stragglers = terminate_all_children()
    assert stragglers == 0, f"{stragglers} read worker(s) outlived this test"


# -- sizing ---------------------------------------------------------------------------------------


def test_auto_is_the_least_of_the_cores_and_half_the_memory_capped_on_a_share() -> None:
    extraction = ExtractionSettings(subprocess_memory_bytes=GIB)
    assert auto_workers(extraction, cpu_count=16, physical_memory=64 * GIB) == 16
    assert auto_workers(extraction, cpu_count=16, physical_memory=8 * GIB) == 4
    assert auto_workers(extraction, cpu_count=16, physical_memory=64 * GIB, on_a_share=True) == (
        SHARE_WORKER_CAP
    )
    assert auto_workers(extraction, cpu_count=4, physical_memory=1 * GIB) == 1, "never below one"
    assert auto_workers(extraction, cpu_count=6, physical_memory=None) == 6, "no memory reading"
    unlimited = ExtractionSettings(subprocess_memory_bytes=None)
    assert auto_workers(unlimited, cpu_count=6, physical_memory=2 * GIB) == 6


def test_a_number_in_the_settings_is_the_number() -> None:
    assert worker_count(ExtractionSettings(workers=3)) == 3
    assert worker_count(ExtractionSettings(workers=3), on_a_share=True) == 3, "a person's number"
    assert worker_count(ExtractionSettings(workers="auto")) >= 1


def test_the_modules_a_worker_imports_are_the_ones_outside_the_engine() -> None:
    load_builtins()
    modules = extra_modules()
    assert "fixtures.hostile" in modules
    assert not any(module.startswith("ingence.") for module in modules)


# -- reading --------------------------------------------------------------------------------------


def _entry(path: Path, number: int) -> Entry:
    stat = path.stat()
    return Entry(
        entry_id=f"entry_{number:03d}",
        root_id="root_x",
        relative_path=path.name,
        name=path.name,
        entry_type=EntryType.FILE,
        extension=path.suffix.lstrip(".").casefold(),
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


def _context(**extraction: object) -> ReadContext:
    load_builtins()
    return ReadContext(
        settings=Settings(extraction=ExtractionSettings(enabled=True, **extraction)),  # type: ignore[arg-type]
        run_id="run_20260101T000002Z_222222222222",
        observed_at="t",
        extractors=tuple(extraction.get("adapters", ()) or ()),  # type: ignore[arg-type]
        extra_modules=extra_modules(),
    )


def _tasks(paths: list[Path], *, deadline: float = 30.0) -> list[ReadTask]:
    return [
        ReadTask(
            sequence=number, entry=_entry(path, number), path=str(path), deadline_seconds=deadline
        )
        for number, path in enumerate(paths)
    ]


def _plates(base: Path, count: int) -> list[Path]:
    base.mkdir(parents=True, exist_ok=True)
    paths = []
    for number in range(count):
        path = base / f"plate_{number:02d}.csv"
        path.write_text(
            "well,value\n" + "".join(f"A{row},{row * number}\n" for row in range(1, 40))
        )
        paths.append(path)
    return paths


def test_results_come_back_in_the_order_the_tasks_were_given_whatever_the_worker(tmp_path) -> None:
    paths = _plates(tmp_path / "pool", 12)
    tasks = _tasks(paths)
    with ReadPool(_context(), workers=4, memory_bytes=GIB) as pool:
        results = list(pool.read(iter(tasks)))
        assert pool.size == 4
    assert [result.task.sequence for result in results] == list(range(12))
    assert {result.outcome.outcome for result in results} == {ScanOutcome.EXTRACTED}
    assert len({result.worker for result in results}) > 1, "more than one worker read"
    assert all(result.killed_for is None for result in results)
    assert pool.notes == []
    assert live_child_count() == 0, "the pool reaps its own workers"


def test_the_serial_reader_reads_the_same_files_the_same_way(tmp_path) -> None:
    paths = _plates(tmp_path / "pool", 4)
    serial = list(SerialReader(_context()).read(iter(_tasks(paths))))
    with ReadPool(_context(), workers=2, memory_bytes=GIB) as pool:
        pooled = list(pool.read(iter(_tasks(paths))))
    assert [r.task.sequence for r in serial] == [r.task.sequence for r in pooled]
    for one, other in zip(serial, pooled, strict=True):
        assert one.outcome.outcome is other.outcome.outcome
        assert sorted(o.observation_id for o in one.outcome.observations) == sorted(
            o.observation_id for o in other.outcome.observations
        )


def test_one_hostile_file_kills_one_worker_and_the_rest_are_read(tmp_path) -> None:
    """The hanging reader, ported: killed at the file's deadline, recorded with its stage."""
    pool_dir = tmp_path / "pool"
    paths = _plates(pool_dir, 3)
    hostile = pool_dir / f"stalls{HOSTILE_SUFFIX}"
    hostile.write_bytes(b"x" * 64)
    paths.insert(1, hostile)
    tasks = _tasks(paths, deadline=2.0)
    started = time.monotonic()
    with ReadPool(
        _context(adapters=("hostile_hang", "generic")), workers=2, memory_bytes=GIB
    ) as pool:
        results = list(pool.read(iter(tasks)))
        assert pool.timeouts == 1
        assert pool.deaths == 0
        assert pool.size == 2, "a killed worker is replaced, the pool is not smaller"
    elapsed = time.monotonic() - started
    assert elapsed < 60.0, f"the pass took {elapsed:.0f}s: the deadline did not stop the reader"
    assert [result.task.sequence for result in results] == [0, 1, 2, 3]
    killed = results[1]
    assert killed.killed_for == "timeout"
    assert killed.stage == "extract.hostile_hang"
    assert killed.outcome.outcome is ScanOutcome.CONTENT_ERROR
    (error,) = killed.outcome.errors
    assert error.operation == "extract.hostile_hang"
    assert error.error_type == "WorkerTimeoutError"
    assert "2.0 s" in error.message
    assert [result.outcome.outcome for result in results if result is not killed] == [
        ScanOutcome.EXTRACTED
    ] * 3
    assert [note["event"] for note in pool.notes] == ["worker.killed"]
    assert live_child_count() == 0


def test_a_worker_that_dies_is_recorded_against_its_file_and_replaced(tmp_path) -> None:
    pool_dir = tmp_path / "pool"
    paths = _plates(pool_dir, 3)
    crashing = pool_dir / f"dies{HOSTILE_SUFFIX}"
    crashing.write_bytes(b"x" * 64)
    paths.insert(2, crashing)
    with ReadPool(
        _context(adapters=("hostile_crash", "generic")), workers=2, memory_bytes=GIB
    ) as pool:
        results = list(pool.read(iter(_tasks(paths))))
        assert pool.deaths == 1
        assert pool.timeouts == 0
    assert [result.task.sequence for result in results] == [0, 1, 2, 3]
    died = results[2]
    assert died.killed_for == "crash"
    assert died.stage == "extract.hostile_crash"
    assert died.outcome.outcome is ScanOutcome.CONTENT_ERROR
    assert died.outcome.errors[0].error_type == "WorkerCrashError"
    assert all(r.outcome.outcome is ScanOutcome.EXTRACTED for r in results if r is not died)
    assert live_child_count() == 0


def test_a_worker_that_outgrows_its_ceiling_is_stopped_and_the_file_says_memory(tmp_path) -> None:
    pool_dir = tmp_path / "pool"
    paths = _plates(pool_dir, 2)
    hungry = pool_dir / f"eats{HOSTILE_SUFFIX}"
    hungry.write_bytes(b"x" * 64)
    paths.append(hungry)
    with ReadPool(
        _context(adapters=("hostile_memory", "generic")), workers=1, memory_bytes=256 << 20
    ) as pool:
        results = list(pool.read(iter(_tasks(paths))))
    stopped = results[2]
    assert stopped.killed_for == "memory"
    assert stopped.outcome.errors[0].error_type == "WorkerMemoryError"
    assert "memory" in stopped.outcome.errors[0].message
    assert results[0].outcome.outcome is ScanOutcome.EXTRACTED
    assert pool.memory_deaths == 1 or pool.size == 1
    assert live_child_count() == 0


def test_workers_are_recycled_after_their_share_of_files(tmp_path) -> None:
    paths = _plates(tmp_path / "pool", 7)
    with ReadPool(_context(), workers=1, memory_bytes=GIB, recycle_after=3) as pool:
        results = list(pool.read(iter(_tasks(paths))))
        assert pool.recycled >= 2
    assert [result.task.sequence for result in results] == list(range(7))
    assert all(result.outcome.outcome is ScanOutcome.EXTRACTED for result in results)
    assert [note["event"] for note in pool.notes] == ["worker.recycled"] * pool.recycled
    assert live_child_count() == 0


def test_a_pool_whose_workers_cannot_start_stops_the_pass_and_reads_nothing_here(tmp_path) -> None:
    """Never in this process without a boundary: the pass is asked to stop and keep what was read,
    with the reason, and the rest is counted as not handed out."""
    paths = _plates(tmp_path / "pool", 3)
    context = ReadContext(
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
        run_id="r",
        observed_at="t",
        extra_modules=("no_such_module_for_a_reader",),
    )
    token = CancellationToken()
    handed: list[int] = []

    def tasks():
        for task in _tasks(paths):
            if token.cancelled:
                handed.append(-task.sequence - 1)
                continue
            handed.append(task.sequence)
            yield task

    with ReadPool(context, workers=2, memory_bytes=GIB, token=token) as pool:
        results = list(pool.read(tasks()))
        assert pool.fallen_back is not None
    assert results == [], "nothing was read here"
    assert token.cancelled and token.keeps
    assert "no read worker could be started" in (token.reason or "")
    assert any(note["event"] == "pool.fell_back" for note in pool.notes)
    # No worker was ever ready, so nothing was pulled before the stop. The generator was then run
    # to its end, so the caller counted every file as not handed out.
    assert len(handed) == 3 and all(number < 0 for number in handed)
    assert live_child_count() == 0


# -- the token ------------------------------------------------------------------------------------


def test_a_discard_hands_out_nothing_and_kills_every_worker(tmp_path) -> None:
    paths = _plates(tmp_path / "pool", 4)
    token = CancellationToken()
    with ReadPool(_context(), workers=2, memory_bytes=GIB, token=token) as pool:
        assert live_child_count() == 2
        token.cancel("interrupted by SIGINT")
        results = list(pool.read(iter(_tasks(paths))))
    assert results == []
    assert live_child_count() == 0


def test_a_stop_hands_out_nothing_more_and_keeps_what_the_workers_finish(tmp_path) -> None:
    paths = _plates(tmp_path / "pool", 6)
    token = CancellationToken()
    handed: list[int] = []

    def tasks():
        for task in _tasks(paths):
            if token.cancelled:
                return
            handed.append(task.sequence)
            yield task
            if task.sequence == 1:
                token.stop("stopped after two")

    with ReadPool(_context(), workers=2, memory_bytes=GIB, token=token) as pool:
        results = list(pool.read(tasks()))
    assert handed == [0, 1]
    assert [result.task.sequence for result in results] == [0, 1], "what was in hand is kept"
    assert all(result.outcome.outcome is ScanOutcome.EXTRACTED for result in results)
    assert live_child_count() == 0


def test_auto_starts_no_more_workers_than_the_files_warrant_and_a_number_stands() -> None:
    extraction = ExtractionSettings(workers="auto")
    assert worker_count(extraction, files=10) == 1
    assert worker_count(extraction, files=65) == min(2, worker_count(extraction))
    assert worker_count(extraction, files=100_000) == worker_count(extraction)
    assert worker_count(ExtractionSettings(workers=5), files=10) == 5, "a person's number stands"


def test_a_terminal_s_interrupt_never_reaches_a_worker_with_a_file_in_hand(tmp_path) -> None:
    """Ctrl-C reaches every process in the terminal's group. A worker ignores it, because the
    parent's token is the only authority (and the parent kills with SIGKILL, which nothing
    refuses). Otherwise the file in hand would be reported as a KeyboardInterrupt, and a
    stop-and-keep run would publish it as a content error."""
    import random
    import signal
    import threading

    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    words = random.Random(7)
    large = pool_dir / "long_notes.txt"
    large.write_text(
        "".join(
            " ".join(words.choice(("plate", "well", "read", "dose", "frame")) for _ in range(12))
            + "\n"
            for _ in range(120_000)
        )
    )
    results: list = []
    with ReadPool(_context(), workers=1, memory_bytes=GIB) as pool:
        reader = threading.Thread(
            target=lambda: results.extend(pool.read(iter(_tasks([large], deadline=120.0)))),
            daemon=True,
        )
        reader.start()
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline and not any(w.ready for w in pool._workers.values()):
            time.sleep(0.05)
        (worker,) = pool._workers.values()
        assert worker.ready and worker.process.pid is not None
        time.sleep(0.3)  # the file is in hand: a megabyte of text takes the reader seconds
        os.kill(worker.process.pid, signal.SIGINT)
        os.kill(worker.process.pid, signal.SIGTERM)
        reader.join(120.0)
        assert not reader.is_alive()
    (result,) = results
    assert result.killed_for is None, result.outcome.errors
    assert result.outcome.outcome is ScanOutcome.EXTRACTED
    assert result.outcome.errors == []
    assert live_child_count() == 0


def test_a_digest_task_killed_at_its_deadline_is_a_recorded_failure_of_that_file(tmp_path) -> None:
    """Through the pass: the digest-only loop writes the failure and counts it, whether the
    parent killed the worker at the deadline or the file's own clock refused first."""
    from ingence.core.project import Project
    from ingence.core.settings import HashMode, HashSettings
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog

    pool_dir = tmp_path / "pool"
    paths = _plates(pool_dir, 2)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool_dir],
        settings=Settings(
            extraction=ExtractionSettings(enabled=True, workers=1),
            hash=HashSettings(mode=HashMode.NONE),
        ),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    first = run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
    )
    assert first.extracted == 2
    # Now every file lacks the digest the mode asks for, and the digest-only loop reads them
    # with a clock that has already run out, so the worker is killed at the stage it is in.
    hurried = Project.open(project.path)
    hurried.save_settings(
        hurried.settings.model_copy(
            update={
                "extraction": hurried.settings.extraction.model_copy(
                    update={"file_seconds": 1e-6, "subprocess_timeout_per_gigabyte_seconds": 0.0}
                ),
                "hash": HashSettings(mode=HashMode.FULL),
            }
        )
    )
    second = run_deepen(
        Project.open(project.path),
        run_id="run_20260101T000003Z_333333333333",
        started_at="t",
        budget_seconds=60.0,
    )
    assert second.chosen_because.get("digest") == 2
    assert second.failed == 2, "each digest task's failure is counted"
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT error_type FROM scan_errors WHERE run_id = ?",
            ["run_20260101T000003Z_333333333333"],
        ).fetchall()
    # Killed by the parent at the deadline, or refused by the file's own clock first: either is
    # written against the file and never swallowed.
    assert len(rows) == 2 and all(
        row[0] in ("WorkerTimeoutError", "FileSecondsExceeded") for row in rows
    )
    assert live_child_count() == 0
    del paths


def test_a_worker_says_whether_the_kernel_took_its_memory_limit(tmp_path) -> None:
    """Two layers, as the supervisor has: the self-watch everywhere, and the kernel's
    address-space limit where the platform enforces it, asked for at the ceiling plus the warmed
    worker's baseline. Linux takes it, macOS refuses, and the message says which."""
    import sys

    paths = _plates(tmp_path / "pool", 1)
    with ReadPool(_context(), workers=1, memory_bytes=GIB) as pool:
        results = list(pool.read(iter(_tasks(paths))))
        assert pool.kernel_limit is not None
        if sys.platform.startswith("linux"):
            assert pool.kernel_limit is True
        else:
            assert pool.kernel_limit is False
    assert results[0].outcome.outcome is ScanOutcome.EXTRACTED
