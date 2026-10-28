"""The graph page's fixture project.

The engine builds the project. The engine's steps are a census, a second census after one file is
removed (so the Index holds a tombstone), a dive that reads a script's mentions, and an inference
under revision 4, which proposes the pairs a script names. The fixture then writes what inference
does not produce as records into a new run: a published derivation, a published reference whose
target is not an entry, and a withdrawn edge. The run is staged and committed exactly as the engine
publishes one (``FragmentWriter``, ``compact_run``, ``build_manifest``, ``commit_run``). Invented
names only.

The pool::

    trell/                    the parent folder
      kelt_raw.csv            a child file
      kelt_processed.csv      a child file, published as derived from kelt_raw.csv
      zib_old.txt             removed before the second census: a tombstone
    vorn/
      run_kelt.py             names both kelt files, and kelt_missing.csv, which is nowhere
    zorn/
      brisa_notes.txt         words and no path: read by a reader that looks for paths
      zorn_scan.bin           bytes no reader that looks for paths reads
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

CENSUS_RUN = "run_20260101T000001Z_111111111111"
#: 2020-01-01T00:00:00Z, in seconds since 1970: before every run here.
LONG_AGO = 1_577_836_800
SECOND_CENSUS_RUN = "run_20260101T000002Z_222222222222"
DIVE_RUN = "run_20260101T000003Z_333333333333"
INFER_RUN = "run_20260101T000004Z_444444444444"
#: The run the fixture publishes on top of the inference, holding what a run does not produce.
PUBLISHED_RUN = "run_20260101T000005Z_555555555555"
#: A later run, published by a test that needs the published run to change.
LATER_RUN = "run_20260101T000006Z_666666666666"

#: The identifier the fixture's unresolved published reference names: no entry carries it.
UNRESOLVED_TARGET = "unresolved:kelt_missing.csv"


@dataclass(frozen=True)
class GraphProject:
    project: Path
    pool: Path
    #: Entry identifiers by the fixture's names.
    ids: dict[str, str]
    #: The published derivation's edge identifier.
    derivation_edge: str
    #: The published reference whose target is not an entry.
    unresolved_edge: str
    #: The withdrawn edge, which must never be drawn.
    withdrawn_edge: str
    #: The candidate the derivation was promoted from.
    promoted_candidate: str


def _write_pool(pool: Path) -> None:
    (pool / "trell").mkdir(parents=True)
    (pool / "vorn").mkdir()
    (pool / "trell" / "kelt_raw.csv").write_text("well,value\nA1,1\n")
    (pool / "trell" / "kelt_processed.csv").write_text("well,value\nA1,2\n")
    # The raw file is an hour older than the processed one. Both are older than the runs that
    # observe them, because a time after its run is set aside. A pair's direction comes from its
    # times.
    for turn, name in enumerate(("kelt_raw.csv", "kelt_processed.csv"), start=1):
        stamp = LONG_AGO + turn * 3600
        os.utime(pool / "trell" / name, (stamp, stamp))
    (pool / "trell" / "zib_old.txt").write_text("gone soon\n")
    (pool / "zorn").mkdir()
    (pool / "zorn" / "brisa_notes.txt").write_text("plain words and nothing else\n")
    (pool / "zorn" / "zorn_scan.bin").write_bytes(bytes(range(256)) * 16)
    (pool / "vorn" / "run_kelt.py").write_text(
        'SOURCE = "../trell/kelt_raw.csv"\n'
        'TARGET = "../trell/kelt_processed.csv"\n'
        'LOST = "../trell/kelt_missing.csv"\n'
    )


def publish_with(
    project: Path, run_id: str, extra: list[object], *, started_at: str | None = None
) -> None:
    """Publish a new run: the current run's tables, plus ``extra`` records, the engine's way.

    The run record is written again under the new identifier with the current run as its
    parent, and with ``started_at`` where one is given. Nothing in the current run changes,
    because run directories are immutable.
    """
    from ingence.core.schema import RunRecord
    from ingence.storage.commit import (
        build_manifest,
        commit_run,
        read_pointer,
        run_path,
        staging_path,
    )
    from ingence.storage.compact import compact_run
    from ingence.storage.index_store import FragmentWriter, read_table, record_from_row
    from ingence.storage.layout import MANIFEST_FILE, TABLES, table_for

    pointer = read_pointer(project)
    assert pointer is not None
    current = run_path(project, pointer.run_id)
    staging = staging_path(project, run_id)
    staging.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(current, staging, ignore=shutil.ignore_patterns(MANIFEST_FILE, "journal"))

    run_spec = table_for(RunRecord)
    rows = read_table(current / run_spec.directory, run_spec).to_pylist()
    shutil.rmtree(staging / run_spec.directory)
    records = [
        record_from_row(row, RunRecord).model_copy(
            update={
                "run_id": run_id,
                "parent_run_id": pointer.run_id,
                **({"started_at": started_at} if started_at else {}),
            }
        )
        for row in rows
    ]
    by_table: dict[str, list[object]] = {run_spec.name: list(records)}
    for record in extra:
        by_table.setdefault(table_for(type(record)).name, []).append(record)  # type: ignore[arg-type]
    for name, held in by_table.items():
        spec = next(spec for spec in TABLES if spec.name == name)
        writer = FragmentWriter(spec, staging / spec.directory)
        # A name of its own, so it sits beside the copied fragments rather than over them.
        writer._fragments = 90_000  # a fragment number no copied fragment carries
        writer.extend(held, now=time.monotonic())  # type: ignore[arg-type]
        writer.close(now=time.monotonic())
    compact_run(staging, TABLES)
    manifest = build_manifest(staging, run_id=run_id, engine_version="fixture", created_at="t")
    commit_run(project, manifest=manifest, updated_at="t")


def build_graph_project(base: Path) -> GraphProject:
    """Build the fixture under ``base``: engine runs, then the published additions."""
    from fixtures.one_folder_pairs import allow_pairs_in_one_folder
    from fixtures.revision_4 import tune, write_revision
    from ingence.core.ids import Scope, ScopeKind
    from ingence.core.project import Project
    from ingence.core.relations import Primitive, ReferenceResolution
    from ingence.core.schema import Assertion, DecisionStatus
    from ingence.core.settings import ExtractionSettings
    from ingence.core.tiers import InferenceTier
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog

    pool = base / "pool"
    _write_pool(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    # The raw and processed pair lie in one folder. The identity rule leaves them as siblings, and
    # their names share one word beside the stage. Only their identity proposes the one made from
    # the other, never their stages.
    allow_pairs_in_one_folder(project.path)
    tune(project.path, "minimum_components", 1, rules=("same_name_identity_within_experiment",))
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id=CENSUS_RUN, started_at="t")
    (pool / "trell" / "zib_old.txt").unlink()
    run_census(project, run_id=SECOND_CENSUS_RUN, started_at="t")
    run_deepen(project, run_id=DIVE_RUN, started_at="t")
    run_inference(project, run_id=INFER_RUN, started_at="t")

    with open_catalog(project.path) as catalog:
        ids = {
            str(name): str(entry_id)
            for entry_id, name in catalog.query(
                "SELECT entry_id, name FROM entries ORDER BY entry_id"
            ).fetchall()
        }
        promoted = catalog.query(
            "SELECT * FROM candidates WHERE primitive = 'derives' AND source_id = ? "
            "AND target_id = ? ORDER BY candidate_id LIMIT 1",
            [ids["kelt_raw.csv"], ids["kelt_processed.csv"]],
        ).fetchone()
        columns = [column[0] for column in catalog.query("DESCRIBE candidates").fetchall()]
        root_id = catalog.query("SELECT root_id FROM roots LIMIT 1").fetchone()[0]
    assert promoted is not None, "no rule in force proposes the derivation the fixture promotes"
    candidate = dict(zip(columns, promoted, strict=True))
    scope = Scope(ScopeKind.ROOT, str(root_id))

    derivation = Assertion(
        edge_id="edge_fixture_derivation",
        run_id=PUBLISHED_RUN,
        source_id=ids["kelt_raw.csv"],
        target_id=ids["kelt_processed.csv"],
        primitive=Primitive.DERIVES,
        specialization=candidate["specialization"],
        rule_id=str(candidate["rule_id"]),
        rule_version=str(candidate["rule_version"]),
        scope=scope,
        inference_tier=InferenceTier.DEFEASIBLE,
        decision_status=DecisionStatus.ASSERTED,
        candidate_id=str(candidate["candidate_id"]),
    )
    unresolved = Assertion(
        edge_id="edge_fixture_unresolved_reference",
        run_id=PUBLISHED_RUN,
        source_id=ids["run_kelt.py"],
        target_id=UNRESOLVED_TARGET,
        primitive=Primitive.REFERENCES,
        rule_id=str(candidate["rule_id"]),
        rule_version=str(candidate["rule_version"]),
        scope=scope,
        reference_resolution=ReferenceResolution.UNRESOLVED,
    )
    withdrawn = Assertion(
        edge_id="edge_fixture_withdrawn",
        run_id=PUBLISHED_RUN,
        source_id=ids["kelt_processed.csv"],
        target_id=ids["kelt_raw.csv"],
        primitive=Primitive.DERIVES,
        rule_id=str(candidate["rule_id"]),
        rule_version=str(candidate["rule_version"]),
        scope=scope,
        decision_status=DecisionStatus.WITHDRAWN,
    )
    publish_with(project.path, PUBLISHED_RUN, [derivation, unresolved, withdrawn])
    return GraphProject(
        project=project.path,
        pool=pool,
        ids=ids,
        derivation_edge=derivation.edge_id,
        unresolved_edge=unresolved.edge_id,
        withdrawn_edge=withdrawn.edge_id,
        promoted_candidate=str(candidate["candidate_id"]),
    )
