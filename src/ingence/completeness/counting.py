"""Counting one run's experiments: files, raw acquisitions, what follows them.

The counts read only the run's own conclusions (``classes.yaml`` says which). A stage, a format and
an experiment folder are resolved claims. What follows a raw acquisition is a derivation that the
run proposed or published, or a file that a rule says is named for it. Nothing here reads a file,
the Record or a setting, and nothing is written.

The counts are built with SQL over the catalog's views, as the explorer projection is. The one
Python walk is over folders, to give each folder the highest experiment folder above it (folders
are few beside files). One query joined to that map counts every file.

Where an earlier toolkit used shortcuts of its own, the engine's claims stand in for them, and
each is said where it is made:

- An experiment is the highest folder above a file that a rule resolved as an experiment's
  (``ingence.experiment_units``, the one definition that themes share). A folder that the
  toolkit's own unit rule (``fold``) made an experiment is not one. Raw data under no experiment
  is counted apart.
- Following by name is the engine's ``groups.named_for_an_acquisition``: a file whose name holds
  an acquisition's name whole. It is not the toolkit's search for a six-character stem anywhere in
  the project.
- A follower is an analysis file or a results table. It is never a copy of the recording or the
  same recording in another format: ``raw/zeph_007.nd2`` is followed by
  ``tracks_zeph_007.nd2 - C=0-1.csv`` and never by ``raw/zeph_007.tif``. A file whose name, less
  its last extension, is the acquisition's follows nothing. A derivation a person confirmed
  counts whatever its names say, because the same-name rule is a guess about proposals and names,
  and a person's answer outranks it.
- Files beneath an installed program are not counted, whether or not they lie inside an
  experiment. The one definition decides this (``experiment_units.run_units``).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

import pyarrow as pa
import yaml

from ingence.experiment_units import run_units

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ingence.experiment_units import RunUnits
    from ingence.storage.catalog import Catalog

__all__ = [
    "Classes",
    "Counted",
    "FolderUnit",
    "classes",
    "count_run",
]


@dataclass(frozen=True, slots=True)
class Classes:
    """Which conclusions put a file in each class, as ``classes.yaml`` says."""

    raw_acquisition_stages: tuple[str, ...]
    analysis_stages: tuple[str, ...]
    not_result_stages: tuple[str, ...]
    result_table_formats: tuple[str, ...]


@functools.cache
def classes() -> Classes:
    """The classes ``classes.yaml`` declares, read once."""
    text = resources.files("ingence.completeness").joinpath("classes.yaml").read_text("utf-8")
    loaded = yaml.safe_load(text) or {}

    def listed(name: str) -> tuple[str, ...]:
        return tuple(sorted({str(value) for value in loaded.get(name) or ()}))

    return Classes(
        raw_acquisition_stages=listed("raw_acquisition_stages"),
        analysis_stages=listed("analysis_stages"),
        not_result_stages=listed("not_result_stages"),
        result_table_formats=listed("result_table_formats"),
    )


@dataclass(frozen=True, slots=True)
class FolderUnit:
    """An experiment folder: its entry, its root and its path under the root."""

    entry_id: str
    root_id: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class Counted:
    """What one experiment holds, as the run concluded it; ``unit`` None for files under none."""

    unit: FolderUnit | None
    files: int
    raw_acquisitions: int
    followed: int
    derived_files: int
    analysis_files: int
    result_tables: int


_UNITS_TABLE = "completeness_units"
_PROGRAMS_TABLE = "completeness_programs"
_CONFIRMED_TABLE = "completeness_confirmed"

#: Every live file, its experiment, its classes, and what follows each raw acquisition, counted
#: per experiment (a null experiment: files in none). The lists are passed as parameters and never
#: spelled into the text.
_COUNT_SQL = f"""
WITH
live AS (
    SELECT e.entry_id, e.parent_entry_id,
           regexp_replace(lower(e.name), '[.][^.]*$', '') AS stem
    FROM entries e
    WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'
      AND coalesce(e.parent_entry_id, '') NOT IN (SELECT folder_id FROM {_PROGRAMS_TABLE})
),
staged AS (
    SELECT subject AS entry_id,
           bool_or(list_contains($raw, object_reference)) AS at_acquisition,
           bool_or(list_contains($analysis, object_reference)) AS at_analysis,
           bool_or(list_contains($not_result, object_reference)) AS at_raw_stage
    FROM claims
    WHERE predicate LIKE 'classifies.%stage%' AND resolution_status = 'resolved'
    GROUP BY subject
),
tabled AS (
    SELECT DISTINCT subject AS entry_id FROM claims
    WHERE predicate LIKE 'classifies.format%' AND resolution_status = 'resolved'
      AND list_contains($tables, object_reference)
),
classed AS (
    SELECT l.entry_id, l.stem, u.experiment_id,
           coalesce(s.at_acquisition AND NOT s.at_analysis, false) AS raw,
           coalesce(s.at_analysis, false) AS analysis,
           t.entry_id IS NOT NULL
               AND (coalesce(s.at_analysis, false) OR NOT coalesce(s.at_raw_stage, false))
               AS results_table
    FROM live l
    LEFT JOIN {_UNITS_TABLE} u ON u.folder_id = l.parent_entry_id
    LEFT JOIN staged s ON s.entry_id = l.entry_id
    LEFT JOIN tabled t ON t.entry_id = l.entry_id
),
pairs AS (
    SELECT source_id AS acquisition, target_id AS follower, false AS confirmed FROM candidates
    WHERE primitive = 'derives'
    UNION
    SELECT source_id, target_id, false FROM assertions
    WHERE decision_status = 'asserted' AND primitive = 'derives'
    UNION
    SELECT json_extract_string(object_reference, '$.acquisition'), subject, false FROM claims
    WHERE predicate = 'groups.named_for_an_acquisition' AND resolution_status = 'resolved'
    UNION
    SELECT acquisition, follower, true FROM {_CONFIRMED_TABLE}
),
followed AS (
    SELECT DISTINCT a.experiment_id, p.acquisition, p.follower
    FROM pairs p
    JOIN classed a ON a.entry_id = p.acquisition AND a.raw
    JOIN classed f ON f.entry_id = p.follower AND NOT f.raw
        AND (f.analysis OR f.results_table)
    WHERE p.acquisition <> p.follower AND (p.confirmed OR f.stem <> a.stem)
),
per_file AS (
    SELECT experiment_id, count(*) AS files,
           count(*) FILTER (WHERE raw) AS raw_acquisitions,
           count(*) FILTER (WHERE analysis) AS analysis_files,
           count(*) FILTER (WHERE results_table) AS result_tables
    FROM classed GROUP BY experiment_id
),
per_follower AS (
    SELECT experiment_id, count(DISTINCT acquisition) AS followed,
           count(DISTINCT follower) AS derived_files
    FROM followed GROUP BY experiment_id
)
SELECT f.experiment_id, f.files, f.raw_acquisitions, coalesce(w.followed, 0),
       coalesce(w.derived_files, 0), f.analysis_files, f.result_tables
FROM per_file f
LEFT JOIN per_follower w ON w.experiment_id IS NOT DISTINCT FROM f.experiment_id
ORDER BY f.experiment_id NULLS LAST
"""  # noqa: S608 -- the one name spelled in is this module's constant


def count_run(
    catalog: Catalog,
    confirmed: Iterable[tuple[str, str]] = (),
    run: RunUnits | None = None,
) -> list[Counted]:
    """Every experiment of the catalog's run, counted, then the files in none (``unit`` None).

    ``confirmed`` holds the derivations a person confirmed, as (acquisition, follower). A person's
    answer outranks a naming guess, so the same-name rule never leaves one out.

    ``run`` is :func:`~ingence.experiment_units.run_units` of the catalog where the caller holds it
    already, so the folders are walked once for a request.

    Experiments in path order (root, then path), each the highest experiment folder above some
    folder; one holding no live file is listed with nothing counted, never left out.
    """
    held = classes()
    run = run if run is not None else run_units(catalog)
    described = {
        folder: FolderUnit(entry_id=folder, root_id=run.roots[folder], relative_path=path)
        for folder, path in run.paths.items()
    }
    units, programs = run.units, run.programs
    confirmed_pairs = sorted(set(confirmed))
    table = pa.table(
        {
            "folder_id": pa.array(list(units), type=pa.string()),
            "experiment_id": pa.array(list(units.values()), type=pa.string()),
        }
    )
    catalog.connection.register(_UNITS_TABLE, table)
    catalog.connection.register(
        _PROGRAMS_TABLE, pa.table({"folder_id": pa.array(sorted(programs), type=pa.string())})
    )
    catalog.connection.register(
        _CONFIRMED_TABLE,
        pa.table(
            {
                "acquisition": pa.array([pair[0] for pair in confirmed_pairs], type=pa.string()),
                "follower": pa.array([pair[1] for pair in confirmed_pairs], type=pa.string()),
            }
        ),
    )
    try:
        rows = catalog.connection.execute(
            _COUNT_SQL,
            {
                "raw": list(held.raw_acquisition_stages),
                "analysis": list(held.analysis_stages),
                "not_result": list(held.not_result_stages),
                "tables": list(held.result_table_formats),
            },
        ).fetchall()
    finally:
        catalog.connection.unregister(_UNITS_TABLE)
        catalog.connection.unregister(_PROGRAMS_TABLE)
        catalog.connection.unregister(_CONFIRMED_TABLE)
    counted = {
        (None if row[0] is None else str(row[0])): Counted(
            unit=None if row[0] is None else described[str(row[0])],
            files=int(row[1]),
            raw_acquisitions=int(row[2]),
            followed=int(row[3]),
            derived_files=int(row[4]),
            analysis_files=int(row[5]),
            result_tables=int(row[6]),
        )
        for row in rows
    }
    # Only the highest experiment above each file is an experiment: a folder classed inside
    # another is part of it and never a row of its own.
    ordered = sorted(
        (described[unit] for unit in set(units.values())),
        key=lambda unit: (unit.root_id, unit.relative_path, unit.entry_id),
    )
    listed = [
        counted.get(unit.entry_id)
        or Counted(
            unit=unit,
            files=0,
            raw_acquisitions=0,
            followed=0,
            derived_files=0,
            analysis_files=0,
            result_tables=0,
        )
        for unit in ordered
    ]
    outside = counted.get(None) or Counted(
        unit=None,
        files=0,
        raw_acquisitions=0,
        followed=0,
        derived_files=0,
        analysis_files=0,
        result_tables=0,
    )
    return [*listed, outside]
