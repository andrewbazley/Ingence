"""Extractors built to misbehave, so containment is tested rather than asserted.

Each extractor is synthetic, minimal and one bug class. It is not a real PDF that happens to be
slow, and not a file from anybody's laboratory. Its entire body is the failure mode, so the test
says what it is testing and cannot pass for the wrong reason. The morphology and matching
regressions follow the same discipline, applied to a safety property instead of to a parser.

Each extractor declares ``Isolation.SUBPROCESS``. A safety mechanism that is not tested is not a
safety mechanism, and the only way to test this one is to hand it something that genuinely will
not stop.
"""

from __future__ import annotations

import multiprocessing
import time
from typing import TYPE_CHECKING

from ingence.extractors.base import EXTRACTORS, ExtractorResult, Isolation, Reading

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

#: The only extension these extractors claim. The claim is narrow on purpose. An extractor whose
#: ``supports`` returns true for everything runs on everything the moment it is registered, and
#: one of these hangs forever. A broad registration would turn every unrelated deepening test into
#: a timeout.
HOSTILE_SUFFIX = ".hostile"

__all__ = [
    "HOSTILE_SUFFIX",
    "REWRITE_SUFFIX",
    "CrashingExtractor",
    "HangingExtractor",
    "HungryExtractor",
    "NoisyExtractor",
    "RewritingExtractor",
    "SlowExtractor",
    "SlowInProcessExtractor",
    "WellBehavedExtractor",
    "register_all",
    "register_in_process",
]


class HangingExtractor:
    """Blocks forever without consuming CPU, the way a stalled read does.

    The case a thread cannot be rescued from. ``time.sleep`` in a loop rather
    than a busy wait, because a busy wait would also be stopped by anything that
    could interrupt bytecode, and this must not be.
    """

    name = "hostile_hang"
    version = "1"
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return HOSTILE_SUFFIX in context.extensions

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        del context
        while True:
            time.sleep(3600)


#: What the hungry fixture allocates at a time.
_CHUNK = 16 << 20

#: How long it pauses between chunks.
#:
#: The climb has to be slower than the layer watching it. Otherwise the test measures scheduling
#: luck and not the ceiling. The self-watch polls every 50 ms, so a loop that reaches a 400 MB
#: ceiling in two polls passes or fails on how fast the machine fills memory. Such a loop passed on
#: one machine and failed on a CI macOS runner, which makes it a flaky test and not a real signal.
#:
#: At 10 ms a chunk, the ceiling is crossed around poll five and the bound below around poll
#: twenty-five. That is a deliberate margin. The pause also limits the allocation rate, which
#: makes the fixture survivable on a platform that has no ceiling at all.
_PAUSE_SECONDS = 0.01

#: What it will allocate before it gives up on being stopped.
#:
#: The bound does not weaken the test. Every ceiling in the engine is far below it, so a working
#: ceiling never reaches it. A ceiling that does not fire fails the test's own assertion instead
#: of taking the machine down. The parent's wall clock still catches the case where nothing else
#: does, because reaching this bound takes longer than a second and the clock is three.
#:
#: An unbounded loop is not safe on Windows, which has neither the kernel limit nor a self-watch.
#: There the loop would allocate for the whole wall clock and leave the runner paging. A fixture
#: that only the tested mechanism can stop would punish whichever platform lacks that mechanism.
MEMORY_CEILING_BOUND = 2 << 30


class HungryExtractor:
    """Allocates until something stops it, or until it has clearly not been.

    Held in a list so nothing is collected, and in chunks so the failure is a
    steady climb into the ceiling rather than one allocation the allocator can
    refuse cheaply. The pause between chunks is what makes the climb steady
    enough for a watcher to act on rather than fast enough to race.
    """

    name = "hostile_memory"
    version = "1"
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return HOSTILE_SUFFIX in context.extensions

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        del context
        held: list[bytes] = []
        while len(held) * _CHUNK < MEMORY_CEILING_BOUND:
            held.append(b"\0" * _CHUNK)
            time.sleep(_PAUSE_SECONDS)
        # Reached only where no ceiling stopped it. Returned rather than raised,
        # so the supervising test sees an ordinary success and fails on the
        # assertion that it should have been killed, which names the real problem.
        return ExtractorResult(extractor=self.name, extractor_version=self.version)


class SlowExtractor:
    """Works honestly and takes longer than it is allowed to.

    Distinct from the hang: this one is making progress and would finish. It is
    the case where the ceiling, rather than a defect, is what ends the work, and
    the record must say the ceiling ended it.
    """

    name = "hostile_slow"
    version = "1"
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return HOSTILE_SUFFIX in context.extensions

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        del context
        total = 0
        for _ in range(200):
            time.sleep(0.5)
            total += 1
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            readings=(Reading(key="hostile.iterations", value=total),),
        )


class CrashingExtractor:
    """Dies without returning, the way a segfaulting C parser does.

    ``os._exit`` rather than an exception, because an exception is caught by the
    child's own handler and reported cleanly. This is the case where there is
    nobody left in the child to report anything and the parent has to read the
    death itself.
    """

    name = "hostile_crash"
    version = "1"
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return HOSTILE_SUFFIX in context.extensions

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        del context
        import os

        os._exit(139)


class WellBehavedExtractor:
    """Isolated and entirely ordinary. The control.

    Without it the tests establish only that the supervisor kills things, which
    is easy to get right by killing everything.
    """

    name = "hostile_fine"
    version = "1"
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return HOSTILE_SUFFIX in context.extensions

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            readings=(
                Reading(key="hostile.size", value=context.size_bytes),
                Reading(key="hostile.entry", value=context.entry_id),
            ),
            bytes_read=0,
        )


class NoisyExtractor:
    """Reads the file, and on the way says a great deal about it: a parser library's habit.

    Logs a warning through a library's own logger a hundred times, once differently, and
    raises a Python warning once, then returns an ordinary reading. What it says must
    reach the run's journal, bounded, and never the terminal.
    """

    name = "hostile_noisy"
    version = "1"
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return HOSTILE_SUFFIX in context.extensions

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        import logging
        import warnings

        library = logging.getLogger("noisy.library")
        for _ in range(100):
            library.warning("ignoring a wrong pointing object")
        library.warning("a font nobody installed")
        warnings.warn("palette images should be converted", UserWarning, stacklevel=1)
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            readings=(Reading(key="hostile.size", value=context.size_bytes),),
        )


class SlowInProcessExtractor:
    """Declares it runs in process, and then takes a minute, as a medium text file can.

    This is the one fixture here that does not declare a boundary, because it tests the boundary
    that the engine imposes anyway on content too large to trust in process. It sleeps a minute
    rather than forever, so a failure of that boundary costs a test a minute and never hangs the
    suite. It returns a reading, so a run that let it finish says so plainly.

    Kept out of :func:`register_all`: a test that registers the others and permits
    every extractor must not meet a minute-long one on small content.
    """

    name = "hostile_slow_in_process"
    version = "1"
    isolation = Isolation.IN_PROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return HOSTILE_SUFFIX in context.extensions

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        del context
        for _ in range(120):
            time.sleep(0.5)
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            readings=(Reading(key="hostile.finished", value=True),),
        )


#: The one extension the rewriting fixture claims, apart from the others', so a test registering
#: every fixture never has one of its files changed under it.
REWRITE_SUFFIX = ".rewritten"


class RewritingExtractor:
    """Changes the file it is reading, once: another program writing to it mid-read.

    Appends to the file where it still holds only its first line, then returns an ordinary
    reading of what it saw. The first pass reads a file that changed under it; the next finds
    the file settled and reads it as any other.
    """

    name = "hostile_rewrite"
    version = "1"
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return REWRITE_SUFFIX in context.extensions

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        held = context.path.read_bytes()
        if held.count(b"\n") <= 1:
            with context.path.open("ab") as handle:
                handle.write(b"a second line, written while the first was read\n")
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            readings=(Reading(key="hostile.lines", value=held.count(b"\n")),),
            bytes_read=len(held),
        )


_CLASSES = (
    HangingExtractor,
    HungryExtractor,
    NoisyExtractor,
    SlowExtractor,
    CrashingExtractor,
    WellBehavedExtractor,
    RewritingExtractor,
)


def register_all() -> tuple[str, ...]:
    """Register every fixture and return their names. Idempotent."""
    registered = []
    for cls in _CLASSES:
        registered.append(cls.name)
        if cls.name in EXTRACTORS.names():
            continue
        EXTRACTORS.register(
            cls.name,
            version=cls.version,
            summary=f"Test fixture: {cls.__doc__.splitlines()[0] if cls.__doc__ else cls.name}",
        )(cls)
    return tuple(registered)


def register_in_process() -> tuple[str, ...]:
    """Register the in-process fixture and return its name. Idempotent."""
    cls = SlowInProcessExtractor
    if cls.name not in EXTRACTORS.names():
        EXTRACTORS.register(
            cls.name, version=cls.version, summary="Test fixture: in process, and a minute long"
        )(cls)
    return (cls.name,)


# These extractors are registered at import only in a spawned child. The child imports this
# module to find them and has no opportunity to call anything first, so the registration must
# happen at import. In the parent it must not, because pytest imports every test module during
# collection. An import-time registration would put a permanently hanging extractor into the
# registry before the first unrelated test ran. The parent calls :func:`register_all` from a
# fixture instead.
if multiprocessing.parent_process() is not None:  # pragma: no cover - child only
    register_all()
    register_in_process()
