"""The supervised subprocess path, exercised against inputs that fight back.

An untested safety mechanism is not a safety mechanism, which is the whole argument for this file.
The three fixtures here hang, allocate without bound and die without warning, and each one is a case
a thread cannot be rescued from. The supervisor is the only thing that can rescue them.

Every fixture is synthetic and minimal, one bug class each. A regression is written as the smallest
case of its class rather than as the file that revealed it.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from pathlib import Path

import pytest

from fixtures.hostile import HOSTILE_SUFFIX, register_all
from ingence.core.settings import Settings
from ingence.executors.supervisor import _MEMORY_SIGNALS as _MEMORY_SIGNALS_NOW
from ingence.executors.supervisor import (
    Supervisor,
    address_space_limit_enforced,
    describe_platform,
    live_child_count,
    terminate_all_children,
)
from ingence.extractors.base import EXTRACTORS, ExtractionContext, Isolation, isolation_of
from ingence.scan.cancel import CancellationToken
from ingence.scan.deepen import supervisor_for

#: Short enough that the suite stays quick, long enough that a loaded machine
#: spawning an interpreter does not trip it. Spawn costs about 0.4 s here.
TIMEOUT = 3.0

#: Well above anything a correct extraction wants and well below what the
#: hostile fixture reaches in a second, so the ceiling is what stops it.
MEMORY = 400 << 20


def _where_is(thread: threading.Thread) -> str:
    """Return a live stack for one thread, or say why there is none.

    **Because "it did not finish" is a symptom and a stack is a diagnosis.** The
    supervisor's kill path crosses a process boundary, a pipe and a watchdog
    timer, and a thread that failed to return could be waiting in any of them.
    Guessing which cost this project three sessions on a neighbouring problem.

    Read through ``sys._current_frames`` rather than by arming faulthandler,
    because this wants one thread's stack at one moment inside an assertion
    message, and faulthandler writes every thread to a file descriptor.
    """
    frame = sys._current_frames().get(thread.ident or -1)
    if frame is None:
        return "no live frame: the thread is not running"
    return "".join(traceback.format_stack(frame))


def _registered_children() -> list[object]:
    """Return the live-child registry's contents, captured now.

    The contents are captured before cancelling rather than read after it, because cancellation
    empties the registry: it kills what it finds and then takes it off a list of what is running.
    Reading afterwards therefore says "none registered" whatever happened to the processes, which is
    exactly the question being asked.
    """
    from ingence.executors import supervisor as module

    with module._LIVE_LOCK:
        return list(module._LIVE)


def _describe(children: list[object]) -> str:
    """Say whether each of these processes is actually dead, and how it died.

    This one fact separates two explanations, and it decides whether a change would fix the problem
    or cover it. A parent still waiting on a pipe whose child is dead is a pipe-semantics problem,
    and bounding the parent's wait solves it. A parent waiting on a child that is still running is a
    kill that did not land, and bounding the wait would make this test pass while leaving a user on
    Windows unable to stop an extraction with Ctrl-C.
    """
    if not children:
        return "none were registered"
    return "; ".join(
        f"pid={child.pid} alive={child.is_alive()} exitcode={child.exitcode!r}"  # type: ignore[attr-defined]
        for child in children
    )


def _with_extraction(**changes: object) -> Settings:
    """Return settings with extraction fields changed. Settings are frozen."""
    base = Settings()
    return base.model_copy(update={"extraction": base.extraction.model_copy(update=changes)})


@pytest.fixture(scope="module", autouse=True)
def _fixtures_registered() -> object:
    """Register the hostile fixtures for this module only, then take them out.

    The scope is the module and not global because one of them never returns and ``supports`` is
    consulted on every file the deepening pass touches. A permanently hanging extractor left in the
    registry turns every unrelated test into a timeout.
    """
    names = register_all()
    yield names
    for name in names:
        EXTRACTORS.unregister(name)


@pytest.fixture(autouse=True)
def _no_child_outlives_its_test() -> object:
    """Fail the test that leaks a child, rather than the one that follows it.

    When the cancellation test leaves a worker thread stuck, that thread never reaches the
    ``finally`` that discards its child from the registry, so the registry stays dirty. The next
    test then asserts nothing survives a completed run, finds the previous test's child, and reports
    that a well-behaved extractor left a child behind. That report is untrue and points at the wrong
    code.

    Cleaning up after each test rather than before puts the blame in the right place: the guilty
    test fails, and the one after it starts clean and passes if it deserves to.
    """
    yield
    stragglers = terminate_all_children()
    assert stragglers == 0, (
        f"{stragglers} supervised child(ren) outlived this test. Every path "
        f"through the supervisor is supposed to reap its own."
    )


@pytest.fixture
def context(tmp_path: Path) -> ExtractionContext:
    target = tmp_path / f"subject{HOSTILE_SUFFIX}"
    target.write_bytes(b"hello")
    return ExtractionContext(
        entry_id="entry-1",
        path=target,
        relative_path=target.name,
        extensions=frozenset({HOSTILE_SUFFIX}),
        size_bytes=5,
    )


@pytest.fixture
def supervisor() -> Supervisor:
    return Supervisor(timeout_seconds=TIMEOUT, memory_bytes=MEMORY)


# ---------------------------------------------------------------------------
# The three hostile fixtures.
# ---------------------------------------------------------------------------


def test_an_extractor_that_hangs_is_killed_and_recorded(
    supervisor: Supervisor, context: ExtractionContext
) -> None:
    """A block that never returns is bounded by the parent's clock, not by hope."""
    started = time.monotonic()
    outcome = supervisor.run("hostile_hang", context)
    elapsed = time.monotonic() - started

    assert outcome.killed
    assert outcome.reason == "timeout"
    assert outcome.result.outcome.value == "content_error"
    assert not outcome.result.succeeded
    # Bounded by the ceiling rather than by the fixture, which would never stop.
    assert elapsed < TIMEOUT * 3


def test_an_extractor_that_outgrows_its_ceiling_is_stopped_and_named(
    supervisor: Supervisor, context: ExtractionContext
) -> None:
    """Memory exhaustion is reported as memory, not as an anonymous death.

    A memory ceiling is a limit a user can raise and a crash is not, so collapsing them would send
    the user to the wrong remedy.
    """
    outcome = supervisor.run("hostile_memory", context)

    assert outcome.killed
    assert outcome.reason == "memory"
    assert outcome.result.outcome.value == "content_error"
    assert "memory" in (outcome.result.error_message or "").casefold()


def test_an_extractor_that_exceeds_its_wall_clock_is_stopped(
    supervisor: Supervisor, context: ExtractionContext
) -> None:
    """Honest work that takes too long is stopped by the ceiling that says so.

    Distinct from the hang: this one is making progress and would finish. What
    ends it is the limit, and the record must say the limit ended it.
    """
    outcome = supervisor.run("hostile_slow", context)

    assert outcome.reason == "timeout"
    assert str(TIMEOUT).rstrip("0").rstrip(".") in (outcome.result.error_message or "")


def test_a_child_that_dies_without_speaking_is_recorded_rather_than_raised(
    supervisor: Supervisor, context: ExtractionContext
) -> None:
    """A segfaulting parser is a fact about a file, never an exception here."""
    outcome = supervisor.run("hostile_crash", context)

    assert outcome.reason == "crash"
    assert outcome.result.outcome.value == "content_error"
    assert "139" in (outcome.result.error_message or "")


# ---------------------------------------------------------------------------
# The control, without which the tests above only prove it kills everything.
# ---------------------------------------------------------------------------


def test_an_isolated_extractor_returns_its_readings_across_the_boundary(
    supervisor: Supervisor, context: ExtractionContext
) -> None:
    """The ordinary case survives the trip, values intact."""
    outcome = supervisor.run("hostile_fine", context)

    assert not outcome.killed
    assert outcome.reason is None
    assert outcome.result.succeeded
    readings = {reading.key: reading.value for reading in outcome.result.readings}
    assert readings == {"hostile.size": 5, "hostile.entry": "entry-1"}


def test_a_child_is_never_stopped_for_memory_its_parent_once_held(
    supervisor: Supervisor, context: ExtractionContext
) -> None:
    """A child's ceiling is on the child, whatever its parent held before starting it.

    Linux carries a process's peak resident size across the exec that starts a spawned child, so a
    watch reading ``ru_maxrss`` there would stop every child of a parent that had once held more
    than the child's ceiling, before the child did anything. A suite's own process grows past the
    ceiling these tests set, so on Linux such a watch would stop these tests' children in a full run
    and not when each runs alone. The parent here holds more than the ceiling on purpose, and the
    control must still succeed.
    """
    ballast = b"\x01" * (MEMORY + (64 << 20))
    outcome = supervisor.run("hostile_fine", context)
    del ballast

    assert outcome.reason is None
    assert outcome.result.succeeded


def test_an_unregistered_name_is_a_content_error_not_a_crash(
    supervisor: Supervisor, context: ExtractionContext
) -> None:
    """Asking for something that is not there fails the file, never the run."""
    outcome = supervisor.run("no_such_extractor", context)

    assert outcome.result.outcome.value == "content_error"
    assert outcome.result.extractor_version == "unknown"


def test_the_parent_stops_waiting_when_its_child_dies_without_speaking() -> None:
    """The wait ends on the parent's own clock, not on the pipe noticing.

    This is the minimal synthetic case of a class that only shows on one platform. A parent that sat
    in ``recv``, which waits forever, would depend on the dying child's end of the pipe to break it.
    On Windows the child dies (exit code -15 from the kill) and the read never completes, so
    cancellation reaches the child and never the thread waiting for it.

    The test uses a connection that is never ready and a process that reports itself dead, so it
    fails on every platform if the wait goes back to being unbounded, rather than only on the one
    that revealed it.
    """
    from ingence.executors.supervisor import _await_payload

    class NeverReady:
        """A pipe that stays silent, as a broken one on Windows did."""

        def poll(self, timeout: float | None = None) -> bool:
            del timeout
            return False

        def recv(self) -> object:
            raise AssertionError("nothing was ever ready to read")

    class Dead:
        """A child that has already gone."""

        def is_alive(self) -> bool:
            return False

    started = time.monotonic()
    assert _await_payload(NeverReady(), Dead(), threading.Event()) is None  # type: ignore[arg-type]
    assert time.monotonic() - started < 2.0, "the parent waited on the pipe rather than the child"


def test_the_parent_stops_waiting_when_its_clock_expires() -> None:
    """The other way out: the watchdog fired and the child is still running."""
    from ingence.executors.supervisor import _await_payload

    class NeverReady:
        def poll(self, timeout: float | None = None) -> bool:
            del timeout
            return False

        def recv(self) -> object:
            raise AssertionError("nothing was ever ready to read")

    class Immortal:
        def is_alive(self) -> bool:
            return True

    expired = threading.Event()
    expired.set()
    started = time.monotonic()
    assert _await_payload(NeverReady(), Immortal(), expired) is None  # type: ignore[arg-type]
    assert time.monotonic() - started < 2.0


def test_a_child_that_answered_just_before_dying_is_still_heard() -> None:
    """Losing that payload would report a successful extraction as a crash."""
    from ingence.executors.supervisor import _await_payload

    class SpokeThenDied:
        def __init__(self) -> None:
            self.reads = 0

        def poll(self, timeout: float | None = None) -> bool:
            # Silent on the timed look, ready on the zero-wait look the parent
            # takes once it sees the child has gone.
            return timeout == 0

        def recv(self) -> object:
            self.reads += 1
            return ("ok", "the answer")

    class Dead:
        def is_alive(self) -> bool:
            return False

    assert _await_payload(SpokeThenDied(), Dead(), threading.Event()) == ("ok", "the answer")  # type: ignore[arg-type]


def test_the_supervisor_never_waits_on_a_pipe_without_a_deadline() -> None:
    """The structural half, on the precedent of the Rulebook's own syntax check.

    The three tests above pin what the bounded wait does. This test pins that the supervisor uses
    it: a call to ``recv`` inside ``run`` waits forever, and waiting forever on a pipe whose writer
    has been killed is a wait that never ends on Windows. Restoring that line would make those three
    tests pass and the engine wrong.
    """
    import inspect

    from ingence.executors.supervisor import Supervisor

    body = inspect.getsource(Supervisor.run)
    assert "recv(" not in body, (
        "Supervisor.run waits on the pipe directly; the parent's ceiling must "
        "not depend on the child's death reaching it"
    )
    assert "_await_payload(" in body


def test_a_child_that_sent_nonsense_is_a_failure_rather_than_an_exception() -> None:
    """The supervising thread is the one place in the module that must not raise.

    Everything the supervisor stops is supposed to become a recorded content
    error and let the run finish, so a malformed message cannot be allowed to
    unpack.
    """
    from ingence.executors.supervisor import _await_payload

    class SaidSomethingOdd:
        def poll(self, timeout: float | None = None) -> bool:
            del timeout
            return True

        def recv(self) -> object:
            return "not a two-part message"

    class Alive:
        def is_alive(self) -> bool:
            return True

    assert _await_payload(SaidSomethingOdd(), Alive(), threading.Event()) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cancellation.
# ---------------------------------------------------------------------------


def test_cancelling_a_token_kills_every_live_child(
    context: ExtractionContext,
) -> None:
    """Ctrl-C reaches the child, as the supervisor module's docstrings say.

    The interruption point that matters is the one inside an extraction, since every other point is
    already covered by the cooperative check between entries. A child left running here would be a
    child a user cannot stop.
    """
    patient = Supervisor(timeout_seconds=120.0, memory_bytes=MEMORY)
    outcome: list[object] = []

    # The thread is a daemon thread. It blocks in the supervisor's `recv` until the child it is
    # watching dies. Where the kill does not reach the child, it never returns. A non-daemon thread
    # would then hold `threading._shutdown` after every test had already reported, turning
    # diagnosable failures into a job that runs until somebody cancels it. A daemon thread leaves
    # the assertion below as the signal, which is what a failing test is supposed to be.
    worker = threading.Thread(
        target=lambda: outcome.append(patient.run("hostile_hang", context)),
        daemon=True,
    )
    worker.start()
    try:
        deadline = time.monotonic() + 30.0
        while live_child_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert live_child_count() == 1, "the child never started"

        killed = _registered_children()
        CancellationToken().cancel("interrupted by SIGINT")

        worker.join(30.0)
        assert not worker.is_alive(), (
            f"cancellation did not reach the child; the worker is stuck at:\n"
            f"{_where_is(worker)}"
            f"the children it killed: {_describe(killed)}"
        )
    finally:
        terminate_all_children()
        worker.join(30.0)

    assert live_child_count() == 0
    # Far short of the 120-second ceiling: it was cancellation that ended it.
    assert outcome, "the supervised call produced nothing"


def test_no_children_survive_a_completed_run(
    supervisor: Supervisor, context: ExtractionContext
) -> None:
    """Every path through the supervisor reaps its child, including the bad ones."""
    for name in ("hostile_fine", "hostile_crash", "hostile_hang"):
        supervisor.run(name, context)
        assert live_child_count() == 0, f"{name} left a child behind"


# ---------------------------------------------------------------------------
# The declaration, and the switch.
# ---------------------------------------------------------------------------


def test_isolation_defaults_to_in_process_for_an_extractor_that_says_nothing() -> None:
    """A new extractor is safe to write without remembering this exists."""

    class Silent:
        name = "silent"
        version = "1"

    assert isolation_of(Silent()) is Isolation.IN_PROCESS


def test_the_shipped_extractors_declare_what_they_need() -> None:
    """Only the format parsers pay for a process.

    The test checks that the archive reader runs in a subprocess, and the text, table and generic
    readers run in the engine's own process. It reads this from the extractors that can be built
    here, which is how the deepening pass chooses them too. A registered extractor whose dependency
    or server is missing is unavailable with its reason recorded, and building it to read one
    attribute would raise exactly that reason.
    """
    from ingence.extractors import load_builtins

    load_builtins()
    declared = {
        name: isolation_of(EXTRACTORS.get(name)) for name in EXTRACTORS.names(available_only=True)
    }

    assert declared["archive"] is Isolation.SUBPROCESS
    assert declared["text"] is Isolation.IN_PROCESS
    assert declared["tabular"] is Isolation.IN_PROCESS
    assert declared["generic"] is Isolation.IN_PROCESS


def test_switching_isolation_off_removes_the_supervisor_entirely() -> None:
    """A feature flag, not a branch, so the two paths can be measured against each other."""
    assert supervisor_for(Settings()) is not None

    off = _with_extraction(use_subprocess_isolation=False)
    assert supervisor_for(off) is None


def test_the_supervisor_carries_the_settings_it_was_given() -> None:
    """The ceilings a run chose are the ceilings its children run under."""
    settings = _with_extraction(
        subprocess_timeout_seconds=12.5,
        subprocess_memory_bytes=7 << 20,
    )

    supervisor = supervisor_for(settings)
    assert supervisor is not None
    assert supervisor.timeout_seconds == 12.5
    assert supervisor.memory_bytes == 7 << 20
    # One worker until an extraction throughput measurement says otherwise.
    assert supervisor.workers == 1


# ---------------------------------------------------------------------------
# What the platform actually does, rather than what it advertises.
# ---------------------------------------------------------------------------


def test_the_platform_description_measures_enforcement_rather_than_presence() -> None:
    """``RLIMIT_AS`` exists on macOS and does nothing there.

    So the run record must not read the attribute's presence as the property's
    truth. This asserts the probe agrees with itself and that the second layer
    is available wherever the first is not, which together are what make the
    ceiling real on every platform rather than on one.
    """
    description = describe_platform()

    assert description["start_method"] == "spawn"
    assert description["address_space_limit_enforced"] == address_space_limit_enforced()
    assert isinstance(description["self_memory_watch"], bool)
    assert description["address_space_limit_enforced"] or description["self_memory_watch"], (
        "neither memory layer is available on this platform; only the wall clock is left"
    )


def test_a_hostile_extractor_claims_nothing_it_was_not_pointed_at(
    context: ExtractionContext, tmp_path: Path
) -> None:
    """A registered extractor runs on everything it claims, so claims must be narrow.

    Recorded as a test rather than as a comment because the failure mode is
    silent and expensive: a hanging extractor that claims every file turns every
    scan into a timeout, and nothing in the registry prevents one being written.
    """
    ordinary = ExtractionContext(
        entry_id="entry-2",
        path=tmp_path / "ordinary.csv",
        relative_path="ordinary.csv",
        extensions=frozenset({".csv"}),
        size_bytes=0,
    )
    for name in ("hostile_hang", "hostile_memory", "hostile_slow", "hostile_crash"):
        extractor = EXTRACTORS.get(name)
        assert extractor.supports(context)
        assert not extractor.supports(ordinary)


# ---------------------------------------------------------------------------
# What a platform does not have.
# ---------------------------------------------------------------------------


def test_the_memory_signal_set_is_built_only_from_signals_this_platform_defines() -> None:
    """Naming a signal that does not exist everywhere must not break importing the engine.

    The bug class is a module-level constant built from platform-conditional attributes, and it is
    written here as the smallest case of that class rather than as the platform that revealed it.
    ``SIGBUS`` and ``SIGKILL`` are absent on Windows, so the frozenset comprehension would raise at
    import time. The deepening pass imports the supervisor and the CLI imports the deepening pass,
    so ``ingence --version`` would fail before printing anything.

    The test hands the builder a stub shaped like Windows, so it fails on every platform if the
    defensive lookup is removed, rather than only on the one that cannot run it.

    The test does not reload the module. Substituting ``sys.modules["signal"]`` and reloading would
    leave every other module holding the previous ``Supervisor`` class and the previous
    ``_LIVE_CHILDREN`` registry. A later cancellation would then wait on children registered where
    nothing is looking. A test must not be able to hang the suite it is part of.
    """
    import signal as real_signal

    from ingence.executors.supervisor import memory_signals

    class _Windows:
        """A signal module without SIGBUS or SIGKILL."""

        SIGSEGV = real_signal.SIGSEGV

    class _Nothing:
        """A signal module without any of them."""

    assert memory_signals(_Windows()) == frozenset({int(real_signal.SIGSEGV)})
    assert memory_signals(_Nothing()) == frozenset()
    # On this platform, whatever it is, the real set is a subset of the names.
    assert memory_signals() == _MEMORY_SIGNALS_NOW


def test_every_memory_message_names_memory_and_says_which_layer_stopped_it() -> None:
    """Three routes reach one reason, and each message names memory and the layer that stopped the
    child.

    The two ceilings stay apart because they imply different remedies, so which one acted is the
    fact a reader needs. A reason column reading ``memory`` beside a message reading
    ``killed by signal 9`` makes the reader translate.
    """
    from ingence.executors.supervisor import _MEMORY_EXIT, _MEMORY_SIGNALS

    supervisor = Supervisor()
    engine_ceiling = supervisor._message_for(_MEMORY_EXIT)
    assert "memory" in engine_ceiling.casefold()
    assert "stopped itself" in engine_ceiling

    # Empty on Windows, which is the correct answer there and not a degraded one:
    # multiprocessing reports a Windows exit code, never a negative signal number.
    for number in _MEMORY_SIGNALS:
        operating_system = supervisor._message_for(-number)
        assert "memory" in operating_system.casefold()
        assert "operating system" in operating_system

    # An ordinary crash must not claim memory, or the partition stops routing.
    assert "memory" not in supervisor._message_for(3).casefold()


def test_an_isolated_child_never_imports_the_record_schema() -> None:
    """The shrink that made a pool unnecessary, pinned so it cannot be undone quietly.

    Every import of ``core.schema`` on the extractor path is an enum and never a model. Reaching
    them through ``core.schema`` makes a spawned child import Pydantic and define every record type:
    0.408 s against 0.096 s for the rest of the path together. The four enums live in
    ``core.outcomes``, which removes three quarters of the cost of isolation without pooling
    anything.

    The failure mode is silent, which is why this is a test. One import added to
    ``extractors/base.py`` that reaches a model puts 0.3 s back on every isolated extraction and
    nothing breaks. The only symptom is a slower scan, on a path that only runs for files an
    extractor declared dangerous.

    The test measures in a fresh interpreter rather than in this one, because the test process has
    already imported most of the engine and would report success whatever the import graph said.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import ingence.executors.supervisor\n"
        "from ingence.extractors import load_builtins\n"
        "load_builtins()\n"
        "reached = sorted(m for m in ('ingence.core.schema', 'pydantic') if m in sys.modules)\n"
        "print(','.join(reached))\n"
    )
    # Timed out rather than trusted. A subprocess call with no ceiling inside a
    # test is a hang by construction, and this one starts an interpreter whose
    # whole purpose is to import things, on a platform where that is slower.
    finished = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert finished.stdout.strip() == "", (
        f"an isolated child imports {finished.stdout.strip()}; every enum the "
        f"extractor path needs lives in ingence.core.outcomes, which imports "
        f"nothing from the engine"
    )


def _reimported_by_a_child(directory: Path, entry_point: str) -> list[str]:
    """Return what the command line brought into a child that re-ran a launcher for this entry.

    The launcher is written the way an installer writes one, and started by its path, which is
    what a child of a console script is told to re-create.
    """
    import subprocess

    module, _, function = entry_point.partition(":")
    command_line_only = (
        "duckdb",
        "ingence.cli.main",
        "ingence.core.schema",
        "pyarrow",
        "pydantic",
        "rich",
        "typer",
    )
    directory.mkdir()
    launcher = directory / "ingence"
    launcher.write_text(
        "import sys\n"
        f"from {module} import {function}\n"
        "\n"
        "def report(sender):\n"
        f"    sender.send([name for name in {command_line_only!r} if name in sys.modules])\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    import multiprocessing\n"
        "    spawn = multiprocessing.get_context('spawn')\n"
        "    receiver, sender = spawn.Pipe(duplex=False)\n"
        "    child = spawn.Process(target=report, args=(sender,))\n"
        "    child.start()\n"
        "    sender.close()\n"
        "    print(','.join(receiver.recv()))\n"
        "    child.join()\n",
        encoding="utf-8",
    )
    # Timed out for the reason the test above gives: an interpreter started to import things.
    finished = subprocess.run(
        [sys.executable, str(launcher)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return [name for name in finished.stdout.strip().split(",") if name]


def test_a_child_of_the_console_script_never_imports_the_command_line(tmp_path: Path) -> None:
    """A spawned child of the console script never imports the command line.

    A spawned child re-runs its parent's main module before it does any work, and a console script's
    main module is a launcher whose top level imports the entry point ``pyproject.toml`` declares.
    An entry point declared as ``ingence.cli.main:main`` would put Typer, Rich, Pydantic, DuckDB and
    PyArrow into every isolated extraction of a real run, 0.5 s each, and nothing would fail: the
    only symptom would be a slower scan.

    The same launcher naming the command line itself must report it, or this test has stopped
    measuring anything. A Python that no longer re-ran the main module in a child would pass the
    first assertion vacuously, and the second says so.
    """
    import tomllib

    declared = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["scripts"]["ingence"]

    assert _reimported_by_a_child(tmp_path / "declared", declared) == [], (
        f"a child of the ingence console script imports what {declared} imports; the entry "
        f"point must live in a module that imports nothing at module scope, which is what "
        f"ingence.cli.launch is for"
    )
    assert "ingence.cli.main" in _reimported_by_a_child(
        tmp_path / "control", "ingence.cli.main:main"
    ), "a child no longer re-runs its parent's launcher, so the assertion above proves nothing"


def test_the_light_module_imports_nothing_from_the_engine() -> None:
    """Its whole contract, asserted rather than left to a docstring.

    ``core.outcomes`` exists so that a spawned child can reach four enums without
    dragging in the record schema. One engine import here re-links the two and
    undoes the measurement it was created for.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "ingence" / "core" / "outcomes.py"
    offending = [
        line.strip()
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from ")) and "ingence" in line
    ]
    assert offending == []
