"""Recorded data, standing stage words, and one file at two steps.

Three kinds of case, each the minimal synthetic case, every name invented:

- a read file (``.fastq``, ``.fq``) is recorded data and sits at the acquisition stage, through
  the one function that says who writes a spelling; a FASTA (a reference, an assembly) does not;
- ``raw`` is a stage only where it stands alone as a part of a name: inside a word
  (``rawSignal``), after the first dot where a tool writes its suffixes (``crop.raw.bam``), or on
  an archive bundle (``..._RAW.tar``) it is not;
- one file at two processing steps (``X.bam`` and ``X.aligned.bam``; ``X_raw`` and
  ``X_processed``) is no replicate of itself: the replicates rule keeps the two apart and the
  step is proposed as a derivation instead, the earlier step the source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"
FOLDER = "vorn/2024-03-04_kelt_dapi"

#: The pool: one dated experiment folder holding every case.
NAMES: dict[str, str] = {
    "kelt_001.bam": "x",
    "kelt_001.aligned.bam": "x",
    "kelt_001_raw.nd2": "x",
    "kelt_001_processed.nd2": "x",
    "kelt_002_raw.nd2": "x",
    "kelt_002_processed.nd2": "x",
    "rawSignal.tif": "x",
    "crop.raw.bam": "x",
    "zeph_RAW.tar": "x",
    "zeph-RAW.tif": "x",
    "zeph_raw.nd2": "x",
    "reads_R1.fastq": "@r\nACGT\n+\nIIII\n",
    "reads_R1.fq": "x",
    "genome.fasta": ">a\nACGT\n",
}


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("e4_stages")
    pool = base / "pool"
    for name, text in NAMES.items():
        path = pool / FOLDER / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def _stages(project: Project) -> dict[str, set[tuple[str, str]]]:
    """Each file's resolved stage claims, as (rule, stage)."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.rule_id, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.predicate LIKE 'classifies.%stage%' AND c.resolution_status = 'resolved'"
        ).fetchall()
    found: dict[str, set[tuple[str, str]]] = {name: set() for name in NAMES}
    for name, rule, stage in rows:
        found[str(name)].add((str(rule), str(stage)))
    return found


def _classes(project: Project, rule: str) -> list[set[str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.object_reference, e.name FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule],
        ).fetchall()
    by_key: dict[str, set[str]] = {}
    for key, name in rows:
        by_key.setdefault(str(key), set()).add(str(name))
    return [members for members in by_key.values() if len(members) > 1]


def _derivations(project: Project, rule: str) -> set[tuple[str, str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT s.name, t.name FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id "
            "WHERE c.primitive = 'derives' AND c.rule_id = ?",
            [rule],
        ).fetchall()
    return {(str(source), str(target)) for source, target in rows}


# ---------------------------------------------------------------------------
# A read file is recorded data.
# ---------------------------------------------------------------------------


def test_a_read_file_is_an_acquisition_and_a_fasta_is_not(project: Project) -> None:
    stages = _stages(project)
    acquisition = ("acquisition_stage_from_format", "acquisition")
    assert acquisition in stages["reads_R1.fastq"]
    assert acquisition in stages["reads_R1.fq"]
    assert stages["genome.fasta"] == set(), "a reference or an assembly declares no writer"


def test_the_one_function_says_a_read_spelling_is_an_instruments() -> None:
    from ingence.dictionary.recorded import ACQUISITION_WRITER, recorded_origins
    from ingence.dictionary.store import DictionaryStore

    origins = recorded_origins(DictionaryStore.builtin())
    assert origins["read_format"] == {"fastq": ACQUISITION_WRITER, "fq": ACQUISITION_WRITER}
    # The core's image type still declares origins and names no writer.
    assert "image_format" in origins


def test_the_file_types_table_lays_over_the_senses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The file types table marks a type ``can_be_raw``: every spelling it lists is then an
    instrument's, and a family the table knows declares its origins even where it names no
    writer. A table that cannot be read leaves the senses' origins standing."""
    from ingence.dictionary import file_types, recorded
    from ingence.dictionary.store import DictionaryStore

    store = DictionaryStore.builtin()
    origins = recorded.recorded_origins(store)
    assert origins["read_format"]["fastq"] == "instrument"
    assert origins["read_format"]["fq"] == "instrument"
    assert "fasta" not in origins["read_format"], "a reference or an assembly declares no writer"
    assert "tif" not in origins["image_format"], "anything writes a TIFF: evidence decides"

    def refused() -> file_types.FileTypeTable:
        raise file_types.FileTypeTableError("no table")

    monkeypatch.setattr(recorded, "file_type_table", refused)
    assert recorded.recorded_origins(store) == recorded.format_origins(store)


# ---------------------------------------------------------------------------
# Raw stands alone as a name part, or it is not a stage.
# ---------------------------------------------------------------------------


def test_raw_inside_a_word_after_a_dot_or_on_a_bundle_is_no_stage(project: Project) -> None:
    stages = _stages(project)
    raw = ("pipeline_stage_from_name", "raw")
    assert raw in stages["zeph_raw.nd2"], "a standing part cut on an underscore"
    assert raw in stages["zeph-RAW.tif"], "a standing part cut on a dash, in any case"
    assert raw in stages["kelt_001_raw.nd2"]
    assert not stages["rawSignal.tif"], "raw inside another word is no stage"
    assert not stages["crop.raw.bam"], "raw after the first dot is a tool's suffix, no stage"
    assert not stages["zeph_RAW.tar"], "an archive bundle is named for what it holds"


@pytest.mark.parametrize(
    ("name", "extension", "parts"),
    [
        ("kelt_raw.nd2", "nd2", {"kelt", "raw"}),
        ("zeph-RAW.tif", "tif", {"zeph", "raw"}),
        ("raw data.tif", "tif", {"raw", "data"}),
        ("rawSignal.tif", "tif", {"rawsignal"}),
        ("crop.raw.bam", "bam", {"crop"}),
        ("kelt_001.ome.tif", "ome.tif", {"kelt", "001"}),
        ("RAW", None, {"raw"}),
        # A dot inside a number, a version or a dotted date never cuts.
        ("kelt_2.5mM_raw.nd2", "nd2", {"kelt", "2.5mm", "raw"}),
        ("kelt_v1.2_raw.tif", "tif", {"kelt", "v1.2", "raw"}),
        ("01.03.2024_raw.tif", "tif", {"01.03.2024", "raw"}),
        # Brackets, a comma, a plus and an ampersand separate too.
        ("kelt (raw).tif", "tif", {"kelt", "raw"}),
        ("kelt[raw],zeph+vorn&quolt.tif", "tif", {"kelt", "raw", "zeph", "vorn", "quolt"}),
        # Only the dotted piece adjoining the extension is a tool's suffix.
        ("raw.kelt.tif", "tif", {"raw"}),
        ("kelt.raw", None, {"kelt", "raw"}),
        ("crop.raw.filtered.bam", "bam", {"crop", "raw"}),
    ],
)
def test_the_standing_parts_of_a_name(name: str, extension: str | None, parts: set[str]) -> None:
    from ingence.rulebook.predicates.workflows import standing_parts

    assert standing_parts(name, extension) == frozenset(parts)


# ---------------------------------------------------------------------------
# One file at two processing steps is not a repeat.
# ---------------------------------------------------------------------------


def test_a_sibling_at_a_later_step_is_no_replicate_of_the_file_it_was_made_from(
    project: Project,
) -> None:
    """The replicates key leaves stage words out (a movie saved with the step word and one saved
    without are one condition), and a file whose name is a sibling's plus a stage word is left
    out of the class by the defeater."""
    classes = _classes(project, "replicates_of_one_condition")
    assert {"kelt_001_raw.nd2", "kelt_002_raw.nd2"} <= next(
        members for members in classes if "kelt_001_raw.nd2" in members
    ), "two raw movies stay replicates"
    for members in classes:
        assert not {"kelt_001.bam", "kelt_001.aligned.bam"} <= members
        assert "kelt_001.aligned.bam" not in members, "a later step of its sibling"
    with open_catalog(project.path) as catalog:
        fired = catalog.query(
            "SELECT e.name FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'later_step_of_a_sibling' AND c.resolution_status = 'resolved' "
            "ORDER BY 1"
        ).fetchall()
    assert [str(row[0]) for row in fired] == ["kelt_001.aligned.bam"]


def test_two_later_steps_beside_each_other_are_the_known_loss(tmp_path: Path) -> None:
    """R1.aligned.bam and R2.aligned.bam beside R1.bam and R2.bam: both are later steps, so
    neither is a replicate of the other, which the rule's rationale states."""
    pool = tmp_path / "pool"
    for name in ("zeph_001.bam", "zeph_002.bam", "zeph_001.aligned.bam", "zeph_002.aligned.bam"):
        path = pool / FOLDER / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    classes = _classes(created, "replicates_of_one_condition")
    assert {"zeph_001.bam", "zeph_002.bam"} in classes
    assert not any("aligned" in name for members in classes for name in members)


def test_another_replicate_with_the_step_word_is_no_later_step(tmp_path: Path) -> None:
    """``kelt_002_analysis.csv`` beside ``kelt_003.csv`` is another replicate written with the
    step word, not the same file twice: the names differ in their numbers, so the defeater
    does not fire and the two stay replicates of one condition."""
    pool = tmp_path / "pool"
    for name in ("kelt_002_analysis.csv", "kelt_003.csv", "kelt_004_analysis.csv"):
        path = pool / FOLDER / "results" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a,b\n1,2\n")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    classes = _classes(created, "replicates_of_one_condition")
    assert {"kelt_002_analysis.csv", "kelt_003.csv", "kelt_004_analysis.csv"} in classes
    with open_catalog(created.path) as catalog:
        fired = catalog.query(
            "SELECT count(*) FROM claims WHERE rule_id = 'later_step_of_a_sibling' "
            "AND resolution_status = 'resolved'"
        ).fetchone()
    assert fired is not None and fired[0] == 0


def test_the_later_step_is_proposed_as_made_from_the_earlier(project: Project) -> None:
    steps = _classes(project, "same_file_at_two_steps")
    assert {"kelt_001.bam", "kelt_001.aligned.bam"} in steps
    assert {"kelt_001_raw.nd2", "kelt_001_processed.nd2"} in steps
    assert _derivations(project, "same_file_later_step") == {
        ("kelt_001.bam", "kelt_001.aligned.bam"),
        ("kelt_001_raw.nd2", "kelt_001_processed.nd2"),
        ("kelt_002_raw.nd2", "kelt_002_processed.nd2"),
    }


def test_two_names_reaching_the_same_stage_are_not_ordered() -> None:
    from ingence.rulebook.predicates.workflows import _earlier

    order = ["raw", "processed", "analysis"]
    assert _earlier(frozenset(), frozenset({"aligned"}), order)
    assert _earlier(frozenset({"raw"}), frozenset({"processed"}), order)
    assert not _earlier(frozenset({"processed"}), frozenset({"raw"}), order)
    assert not _earlier(frozenset({"raw"}), frozenset({"raw"}), order)
    assert not _earlier(frozenset({"raw"}), frozenset(), order)
    assert not _earlier(frozenset({"zorp"}), frozenset({"quolt"}), order), "unlisted stages"
