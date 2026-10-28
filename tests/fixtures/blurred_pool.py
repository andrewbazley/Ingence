"""A generated pool whose lines of work blur into one another.

Three people, each with a line of their own words, written over words every line shares, so that
the seeds do not all keep each line whole: some line is below the boundary and the other seeds
split it with a neighbor. Real words in invented folders (``<person>/<YYYYMMDD> <words>/``), a
result table in each, dated inside one year.
"""

from __future__ import annotations

import datetime as dt
import os
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: Words every line writes some of.
SHARED = ("actin", "tubulin", "dapi", "gfp", "confocal", "lysate", "kinase", "gel")
#: Each line's own words, and whose line it is.
LINES: dict[str, tuple[str, tuple[str, ...]]] = {
    "dana_whitfield": ("hela", ("mitosis", "nocodazole", "spindle")),
    "sam_okafor": ("hek293", ("transfection", "western", "lipofectamine")),
    "lee_tanaka": ("dmso", ("washout", "marker", "vehicle")),
}
PER_LINE = 16
#: How often a line writes each of its own words.
OWN_SHARE = 0.4


def blurred_documents(seed: int = 5) -> dict[str, tuple[str, list[str]]]:
    """``{unit: (person, words)}``: four shared words and whichever of the line's own it wrote."""
    chooser = random.Random(seed)
    made: dict[str, tuple[str, list[str]]] = {}
    for person, (first, own) in LINES.items():
        for number in range(PER_LINE):
            held = [w for w in own if chooser.random() < OWN_SHARE]
            made[f"{person}_{number:02d}"] = (person, [*chooser.sample(SHARED, 4), first, *held])
    return made


#: The header every experiment's results table carries, and the one table with a header of its own.
HEADER = "well,cell_line,treatment,cell_count"
ONE_OF_A_KIND = "well,viability"
#: The experiment whose folder holds two more tables with the same header, a table with no twin, and
#: a table added after the dive.
BUSY = "dana_whitfield_00"
#: An experiment with raw acquisitions and no table beside them: no analysis found in it.
NO_ANALYSIS = "lee_tanaka_00"
LATE_TABLE = "late_results.csv"


def write_blurred_pool(root: Path, seed: int = 5) -> dict[str, str]:
    """The documents as folders; returns each unit's experiment folder below ``root``."""
    folders: dict[str, str] = {}
    for order, (unit, (person, words)) in enumerate(sorted(blurred_documents(seed).items())):
        day = 1 + order % 27
        number = int(unit.rsplit("_", 1)[1])
        folder = root / person / f"2023{(number % 12) + 1:02d}{day:02d} {' '.join(words[:3])}"
        folders[unit] = folder.relative_to(root).as_posix()
        rest = words[3:] or words[:1]
        moment = dt.datetime(2023, (number % 12) + 1, day, 10, tzinfo=dt.UTC).timestamp()
        stem = "_".join(rest)
        files = {f"{stem}_{n:02d}.nd2": f"{unit} {n}\n" for n in range(1, 4)}
        if unit != NO_ANALYSIS:
            files[f"{stem}_results.csv"] = f"{HEADER}\nA1,HeLa,DMSO,{number}\n"
        if unit == BUSY:
            files[f"{stem}_plate_two.csv"] = f"{HEADER}\nA1,HeLa,taxol,7\n"
            files[f"{stem}_plate_three.csv"] = f"{HEADER}\nA1,HeLa,nocodazole,9\n"
            files[f"{stem}_viability.csv"] = f"{ONE_OF_A_KIND}\nA1,0.93\n"
        for name, text in files.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            os.utime(path, (moment, moment))
    return folders


BLURRED_CENSUS_RUN = "run_20260101T000001Z_111111111111"
BLURRED_DIVE_RUN = "run_20260101T000002Z_222222222222"
BLURRED_LATER_RUN = "run_20260101T000003Z_333333333333"
BLURRED_INFER_RUN = "run_20260101T000004Z_444444444444"


def build_blurred_project(base: Path) -> Path:
    """The pool written under ``base/pool``, indexed, dived (the tables' columns are read) and
    inferred by the engine under revision 4; one table is added after the dive, so a later census
    sees it and no dive opened it. Returns the project's path."""
    from fixtures.revision_4 import write_revision
    from ingence.api import v1
    from ingence.core.project import Project
    from ingence.core.settings import ExtractionSettings
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen

    pool = base / "pool"
    pool.mkdir(parents=True)
    folders = write_blurred_pool(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    write_revision(project.path)
    run_census(project, run_id=BLURRED_CENSUS_RUN, started_at="t")
    run_deepen(project, run_id=BLURRED_DIVE_RUN, started_at="t", profile="standard")
    late = pool / folders[BUSY] / LATE_TABLE
    late.write_text(f"{HEADER}\nA1,HeLa,etoposide,11\n", encoding="utf-8")
    moment = dt.datetime(2023, 12, 1, 10, tzinfo=dt.UTC).timestamp()
    os.utime(late, (moment, moment))
    run_census(project, run_id=BLURRED_LATER_RUN, started_at="t")
    run_inference(project, run_id=BLURRED_INFER_RUN, started_at="t")
    # The lines of work are drawn when first read and kept beside the run: read them once here, so
    # a page opened on the copy does not wait for them.
    context = v1.context_for(project.path, settled=None, job_running=False)
    v1.dispatch(context, "themes", {})
    return project.path
