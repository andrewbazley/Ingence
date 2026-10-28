"""A generated pool for the Words section: invented words in dated experiments.

Ten experiments of one invented person, each a dated folder holding two instrument files named
for the words below. Every word is invented or one the shipped Dictionary is known to hold, and
the plant is written before the expectations are:

- ``vorn`` is in every name: the unknown word with the most files.
- ``quolt`` (two experiments), ``quolts`` and ``quoltan`` (one each): a word, its plural and a
  longer form.
- ``fennowic`` and ``fennowik`` (one experiment each): two unknown words one letter apart.
- ``zibKelt`` (one experiment): a spelling a capital divides, beside ``zib`` and ``kelt`` alone.
- ``cluster`` (one experiment): a spelling two shipped senses claim level.
- ``confocal`` (one experiment): a word the Dictionary understands, and ``confokal`` (one
  experiment): a slip of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["EXPERIMENTS", "build_words_project", "wait_for_a_later_second", "write_words_pool"]

#: A dated folder's words, then the stem of the two files it holds.
EXPERIMENTS: tuple[tuple[str, str], ...] = (
    ("20240301 quolt vorn", "quolt_vorn"),
    ("20240302 quolt vorn", "quolt_vorn"),
    ("20240303 quolts vorn", "quolts_vorn"),
    ("20240304 fennowic vorn", "fennowic_vorn"),
    ("20240305 fennowik vorn", "fennowik_vorn"),
    ("20240306 zibKelt vorn", "zibKelt_vorn"),
    ("20240307 cluster vorn", "cluster_vorn"),
    ("20240308 confocal vorn", "confocal_vorn"),
    ("20240309 confokal vorn", "confokal_vorn"),
    ("20240310 quoltan vorn", "quoltan_vorn"),
)

CENSUS = "run_20260101T000001Z_111111111111"
INFERRED = "run_20260101T000002Z_222222222222"


def write_words_pool(base: Path) -> Path:
    """Write the pool under ``base`` and return its root."""
    pool = base / "pool"
    for folder, stem in EXPERIMENTS:
        for number in (1, 2):
            path = pool / "Dana_Whitfield" / folder / f"{stem}_{number:02d}.nd2"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    # The pieces of the divided spelling, written alone in two more names.
    for alone in ("zib_vorn_09.nd2", "kelt_vorn_09.nd2"):
        path = pool / "Dana_Whitfield" / "20240306 zibKelt vorn" / alone
        path.write_text("x")
    return pool


def build_words_project(base: Path, *, infer: bool = True) -> Path:
    """The pool read: a census, and (by default) the rules applied. Returns the project path."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    project = Project.create(base / "project", created_at="t", roots=[write_words_pool(base)])
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    if infer:
        run_inference(
            Project.open(project.path), run_id=INFERRED, started_at="2026-01-01T00:00:02Z"
        )
    return project.path


def wait_for_a_later_second(project: Path, *, patience: float = 10.0) -> str:
    """Wait until the clock the Record stamps its decisions by reads later than the last one, and
    return that instant: a run begun then has begun after every decision written so far.

    The Record stamps whole seconds, and a run in a decision's own second is not known to have
    followed it, so a test that writes an answer and then reads it must start its run after the
    second the answer was written in. This waits on that state (usually it has already passed)
    and not for a fixed time.
    """
    import time

    from ingence.record import log as record_log
    from ingence.workspace.rerun import instant

    stamped = [
        when
        for event in record_log.read_events(project, held=False, withdrawn=True)
        if (when := instant(event.occurred_at)) is not None
    ]
    last = max(stamped, default=None)
    deadline = time.monotonic() + patience
    while True:
        now = record_log.now()
        moment = instant(now)
        if last is None or (moment is not None and moment > last):
            return now
        if time.monotonic() > deadline:
            message = f"the clock did not pass {last.isoformat()} within {patience:g} s"
            raise TimeoutError(message)
        time.sleep(0.02)
