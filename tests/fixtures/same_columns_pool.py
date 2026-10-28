"""An invented pool of tables and arrays for the same-columns tests.

Every name and value is invented. The tables are the shapes the respect has to tell apart: one
header in two folders, a header with an added column, the same names with another delimiter, a
header only, a table of one column, and arrays of one shape, of another number of dimensions and
of one dimension.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ingence.core.project import Project
from ingence.core.settings import ExtractionSettings
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"
LATER_CENSUS = "run_20260101T000003Z_333333333333"
INFERENCE = "run_20260101T000004Z_444444444444"

HEADER = ["well", "cell_line", "treatment", "cell_count"]

#: Each table's path under the pool, and its text.
TABLES: dict[str, str] = {
    # One header in two folders.
    "plate_one/HeLa_DMSO_quant.csv": ",".join(HEADER) + "\nA1,HeLa,DMSO,1204\nA2,HeLa,DMSO,1180\n",
    "plate_two/HeLa_taxol_quant.csv": ",".join(HEADER) + "\nA1,HeLa,taxol,611\nA2,HeLa,taxol,598\n",
    # The same header with a column added: its columns contain the first two tables'.
    "plate_three/HeLa_nocodazole_quant.csv": (
        ",".join([*HEADER, "mean_area"]) + "\nA1,HeLa,nocodazole,402,81.5\n"
    ),
    # The same names with semicolons, and with tabs: not the same columns, the same names.
    "plate_four/HeLa_DMSO_quant_semicolon.csv": ";".join(HEADER) + "\nA1;HeLa;DMSO;1190\n",
    "plate_four/HeLa_DMSO_quant_tabs.tsv": "\t".join(HEADER) + "\nA1\tHeLa\tDMSO\t1175\n",
    # A header and no rows: the template the others were filled from.
    "templates/HeLa_quant_template.csv": ",".join(HEADER) + "\n",
    # The same names in capitals, with spaces around them: the same columns after normalising.
    "plate_five/HeLa_etoposide_quant.csv": (
        " Well , Cell_Line ,TREATMENT, cell_count \nA1,HeLa,etoposide,577\n"
    ),
    # One column: not a table of columns.
    "notes/HeLa_passage_list.csv": "passage\n12\n13\n",
    # Another table whose columns contain none of the above.
    "plate_six/HeLa_viability.csv": "well,viability\nA1,0.93\nA2,0.88\n",
}

#: The arrays: two of one shape, one of three dimensions, one of one dimension.
ARRAYS: dict[str, tuple[int, ...]] = {
    "stacks/HeLa_DMSO_mask.npy": (4, 4),
    "stacks/HeLa_taxol_mask.npy": (4, 4),
    "stacks/HeLa_DMSO_series.npy": (2, 4, 4),
    "stacks/HeLa_trace.npy": (8,),
}

#: A table added after the dive, so a later census sees it and no dive read it.
LATE = "plate_seven/HeLa_cisplatin_quant.csv"
LATE_TEXT = ",".join(HEADER) + "\nA1,HeLa,cisplatin,540\n"


def write_pool(pool: Path) -> None:
    """Write the tables and arrays under ``pool``."""
    for relative, text in TABLES.items():
        target = pool / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for relative, shape in ARRAYS.items():
        target = pool / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        np.save(target, np.zeros(shape, dtype=np.float32))


def build_project(base: Path, *, late: bool = True) -> Project:
    """Census, dive, then (with ``late``) a table the dive never read, and an inference run."""
    pool = base / "pool"
    write_pool(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id=CENSUS, started_at="t")
    run_deepen(project, run_id=DIVE, started_at="t", profile="standard")
    if late:
        target = pool / LATE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(LATE_TEXT, encoding="utf-8")
        run_census(project, run_id=LATER_CENSUS, started_at="t")
    run_inference(project, run_id=INFERENCE, started_at="t")
    return project
