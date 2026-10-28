"""Tables with the same columns, and tables whose columns contain another's.

Every name and value is invented (``fixtures/same_columns_pool.py``). The respect is "same columns":
the respect names column names, their order and the delimiter, and the tests hold that it never
reaches an experiment.

1. One delimiter decision for the table reader and the column digest: a semicolon file and a tab
   file read alike by both.
2. The digests: the array signature records its number of dimensions. The table digest holds names,
   order and delimiter and no column type.
3. The predicate keys a file on a stored digest and abstains in words: a file no dive read, a file
   read that holds no table, a table below the floor, two readings that disagree.
4. On a pool: the rule's classes, the classes of the stored digests and the projection's classes are
   the same set. The inclusion lists name the added and the missing columns. The setting switches
   the inclusion index off. The summary appears in the quality report.
5. No grouping claim crosses into an experiment: every other claim of a run is the same whether or
   not the columns rules can group anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from fixtures.revision_4 import tune
from fixtures.same_columns_pool import (
    HEADER,
    INFERENCE,
    LATE,
    TABLES,
    build_project,
)
from ingence.columns import column_engine, columns_of_file, same_column_summary
from ingence.columns.stored import PROJECTION_VERSION, column_projection
from ingence.core.ids import canonical_json, digest_text
from ingence.core.outcomes import IdentityScope, ValueKind
from ingence.core.project import Project
from ingence.core.schema import Observation
from ingence.core.tiers import ResolutionStatus
from ingence.extractors.base import ExtractionContext
from ingence.extractors.delimited import detect_delimiter, normalise_column
from ingence.extractors.tabular import TabularExtractor
from ingence.identity import IDENTITY_METHODS, load_builtins
from ingence.infer.run import run_inference
from ingence.rulebook.predicates import load_builtins as load_predicates
from ingence.rulebook.registry import PREDICATES, PredicateNotEvaluableError
from ingence.storage.catalog import open_catalog

load_builtins()
load_predicates()

COLUMNS = "same_column_schema"
ARRAY = "same_array_shape"


# ---------------------------------------------------------------------------
# 1. One delimiter decision.
# ---------------------------------------------------------------------------


def _context(path: Path) -> ExtractionContext:
    return ExtractionContext(
        entry_id="e",
        path=path,
        relative_path=path.name,
        extensions=frozenset({path.suffix}),
        size_bytes=path.stat().st_size,
    )


@pytest.mark.parametrize(
    ("line", "extension", "expected"),
    [
        ("a,b,c", ".csv", ","),
        ("a;b;c", ".csv", ";"),
        ("a\tb\tc", ".csv", "\t"),
        ("a\tb\tc", ".txt", "\t"),
        # A tab-delimited extension decides before the line is read.
        ("a,b,c", ".tsv", "\t"),
        ("a,b,c", ".tab", "\t"),
        # A comma inside quotes is not a delimiter.
        ('"cells, counted";"dose, nM"', ".csv", ";"),
        # A tie settles to the comma, then the tab, as it always did; no delimiter is a comma.
        ("a,b;c", ".csv", ","),
        ("single", ".csv", ","),
        ("", ".csv", ","),
    ],
)
def test_the_delimiter_is_decided_by_extension_then_by_the_most_frequent_candidate(
    line: str, extension: str, expected: str
) -> None:
    assert detect_delimiter(line, {extension}) == expected


@pytest.mark.parametrize(
    ("name", "text", "delimiter"),
    [
        ("semicolon.csv", ";".join(HEADER) + "\n1;HeLa;DMSO;5\n", ";"),
        ("tabs.tsv", "\t".join(HEADER) + "\n1\tHeLa\tDMSO\t5\n", "\t"),
        # A tab file named for a comma: the line decides, and both readers agree.
        ("tabs_as_csv.csv", "\t".join(HEADER) + "\n1\tHeLa\tDMSO\t5\n", "\t"),
        ("commas.csv", ",".join(HEADER) + "\n1,HeLa,DMSO,5\n", ","),
        # The case where two readers could disagree: a comma inside one column's name on a
        # tab-delimited line.
        ("tabs_with_comma.csv", "well\tcells, counted\ttreatment\n1\t5\tDMSO\n", "\t"),
    ],
)
def test_the_table_reader_and_the_column_digest_read_one_delimiter(
    tmp_path: Path, name: str, text: str, delimiter: str
) -> None:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    context = _context(path)

    read = TabularExtractor().extract(context)
    readings = {reading.key: reading.value for reading in read.readings}
    assert readings["table.delimiter"] == delimiter

    digest = IDENTITY_METHODS.get("tabular_schema").compute(context)
    assert digest is not None
    names = json.loads(str(readings["table.column_names"]))
    assert digest.value == digest_text(
        canonical_json({"columns": [normalise_column(n) for n in names], "delimiter": delimiter})
    )
    assert readings["table.column_count"] == len(names)


def test_a_semicolon_file_is_a_table_and_its_digest_differs_from_the_comma_file(
    tmp_path: Path,
) -> None:
    """A semicolon header is more than one column, so it has a digest and a table."""
    method = IDENTITY_METHODS.get("tabular_schema")
    commas = tmp_path / "commas.csv"
    commas.write_text(",".join(HEADER) + "\n", encoding="utf-8")
    semicolons = tmp_path / "semicolons.csv"
    semicolons.write_text(";".join(HEADER) + "\n", encoding="utf-8")
    one, other = method.compute(_context(commas)), method.compute(_context(semicolons))
    assert one is not None
    assert other is not None
    assert one.value != other.value


# ---------------------------------------------------------------------------
# 2. The digests.
# ---------------------------------------------------------------------------


def test_the_table_digest_holds_names_order_and_delimiter_and_no_type(tmp_path: Path) -> None:
    method = IDENTITY_METHODS.get("tabular_schema")
    assert method.version == "2"
    integers = tmp_path / "integers.csv"
    integers.write_text("well,cell_count\nA1,12\nA2,13\n", encoding="utf-8")
    words = tmp_path / "words.csv"
    words.write_text("well,cell_count\nA1,many\nA2,few\n", encoding="utf-8")
    one, other = method.compute(_context(integers)), method.compute(_context(words))
    assert one is not None
    assert other is not None
    # Types differ (integer against text) and the digest does not.
    assert one.value == other.value
    assert one.value == digest_text(
        canonical_json({"columns": ["well", "cell_count"], "delimiter": ","})
    )


@pytest.mark.parametrize(("shape", "dimensions"), [((4, 4), 2), ((2, 4, 4), 3), ((8,), 1), ((), 0)])
def test_the_array_signature_records_its_number_of_dimensions(
    tmp_path: Path, shape: tuple[int, ...], dimensions: int
) -> None:
    method = IDENTITY_METHODS.get("array_signature")
    assert method.version == "2"
    path = tmp_path / "array.npy"
    np.save(path, np.zeros(shape, dtype=np.float32))
    result = method.compute(_context(path))
    assert result is not None
    assert result.scope is IdentityScope.HEADER
    assert result.counts == (("array.dimension_count", dimensions),)


def test_two_arrays_of_one_shape_share_a_signature_and_one_of_another_rank_does_not(
    tmp_path: Path,
) -> None:
    method = IDENTITY_METHODS.get("array_signature")
    values = {}
    for name, shape in (("a", (4, 4)), ("b", (4, 4)), ("c", (2, 4, 4)), ("d", (4, 4, 1))):
        path = tmp_path / f"{name}.npy"
        np.save(path, np.zeros(shape, dtype=np.float32))
        result = method.compute(_context(path))
        assert result is not None
        values[name] = result.value
    assert values["a"] == values["b"]
    assert len({values["a"], values["c"], values["d"]}) == 3


# ---------------------------------------------------------------------------
# 3. The predicate, on stored observations.
# ---------------------------------------------------------------------------


def _observation(
    key: str,
    *,
    text: str | None = None,
    integer: int | None = None,
    version: str = "1",
    identity: str | None = None,
    name: str | None = None,
) -> Observation:
    return Observation(
        observation_id=name or f"o:{key}:{version}:{text}:{integer}",
        entry_id="e",
        run_id="r",
        observer=identity or "dive",
        observer_version=version,
        observed_at="t",
        key=key,
        value_kind=ValueKind.INTEGER if integer is not None else ValueKind.TEXT,
        value_text=text,
        value_integer=integer,
        identity_method=identity,
        identity_scope=IdentityScope.HEADER if identity else None,
    )


READ = _observation("type.media_type", text="text/csv")
COLUMN_RULE = {"minimum_columns": 2}
ARRAY_RULE = {"minimum_dimensions": 2}


def _table(digest: str = "d1", count: int = 4, version: str = "2") -> list[Observation]:
    return [
        READ,
        _observation(
            "identity.tabular_schema", text=digest, version=version, identity="tabular_schema"
        ),
        _observation("table.column_count", integer=count),
    ]


def _array(digest: str = "s1", dimensions: int = 2, version: str = "2") -> list[Observation]:
    return [
        READ,
        _observation(
            "identity.array_signature", text=digest, version=version, identity="array_signature"
        ),
        _observation("array.dimension_count", integer=dimensions),
    ]


def test_files_with_one_stored_digest_share_a_class_and_another_digest_is_another_class() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    one = predicate.evaluate(_table("d1"), COLUMN_RULE)
    again = predicate.evaluate(_table("d1", count=7), COLUMN_RULE)
    other = predicate.evaluate(_table("d2"), COLUMN_RULE)
    assert one is not None
    assert again is not None
    assert other is not None
    assert one.key == again.key
    assert one.key != other.key
    # The key names the respect and nothing wider.
    assert json.loads(one.key)["respect"] == "columns"
    assert "experiment" not in one.key


def test_the_two_respects_never_share_a_class() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    table = predicate.evaluate(_table("same"), COLUMN_RULE)
    array = predicate.evaluate(_array("same"), ARRAY_RULE)
    assert table is not None
    assert array is not None
    assert table.key != array.key


def test_the_floor_is_read_from_the_stored_count() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    assert predicate.evaluate(_table(count=2), COLUMN_RULE) is not None
    assert predicate.evaluate(_table(count=1), COLUMN_RULE) is None
    assert predicate.evaluate(_table(count=1), {"minimum_columns": 1}) is not None
    assert predicate.evaluate(_array(dimensions=1), ARRAY_RULE) is None
    assert predicate.evaluate(_array(dimensions=3), ARRAY_RULE) is not None


def test_a_file_no_dive_read_is_unexamined_and_says_so() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    assert predicate.evaluate([], COLUMN_RULE) is None
    said = predicate.explain_abstention([], COLUMN_RULE)
    assert said.status is ResolutionStatus.UNEXAMINED
    assert "no dive read this file" in said.reason


def test_a_file_read_that_holds_no_table_says_so_without_calling_it_unexamined() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    said = predicate.explain_abstention([READ], COLUMN_RULE)
    assert said.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert "found no column names" in said.reason
    assert "found no array shape" in predicate.explain_abstention([READ], ARRAY_RULE).reason


def test_a_digest_without_its_count_is_unexamined_for_the_floor() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    held = [item for item in _table() if item.key != "table.column_count"]
    assert predicate.evaluate(held, COLUMN_RULE) is None
    said = predicate.explain_abstention(held, COLUMN_RULE)
    assert said.status is ResolutionStatus.UNEXAMINED
    assert "no number of columns was recorded" in said.reason


def test_a_table_below_the_floor_says_how_many_columns_the_rule_needs() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    said = predicate.explain_abstention(_table(count=1), COLUMN_RULE)
    assert said.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert "fewer than 2 columns" in said.reason
    assert (
        "fewer than 2 dimensions"
        in predicate.explain_abstention(_array(dimensions=1), ARRAY_RULE).reason
    )


def test_two_readings_of_one_version_that_disagree_are_conflicting_not_a_class() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    held = [
        *_table("d1"),
        _observation(
            "identity.tabular_schema", text="d9", version="2", identity="tabular_schema", name="o2"
        ),
    ]
    assert predicate.evaluate(held, COLUMN_RULE) is None
    said = predicate.explain_abstention(held, COLUMN_RULE)
    assert said.status is ResolutionStatus.EXAMINED_CONFLICTING


def test_an_older_version_beside_a_newer_one_is_not_a_disagreement() -> None:
    """A dive under a newer engine writes beside the older reading; the newest is read."""
    predicate = PREDICATES.get("same_extracted_shape")
    older = _observation(
        "identity.tabular_schema", text="old", version="1", identity="tabular_schema", name="o1"
    )
    held = [*_table("new", version="2"), older]
    found = predicate.evaluate(held, COLUMN_RULE)
    assert found is not None
    assert json.loads(found.key)["digest"] == "new"
    assert "method_version" not in json.loads(found.key)


def test_a_table_read_by_version_one_and_one_read_by_version_two_share_a_class() -> None:
    """One header stores one digest under both versions, so a project dived by two builds groups."""
    predicate = PREDICATES.get("same_extracted_shape")
    old = predicate.evaluate(_table("same", version="1"), COLUMN_RULE)
    new = predicate.evaluate(_table("same", version="2"), COLUMN_RULE)
    assert old is not None
    assert new is not None
    assert old.key == new.key


def test_an_array_digest_of_each_version_stays_apart_because_the_content_differs(
    tmp_path: Path,
) -> None:
    """Version 2 puts the dimension count in the digest, so a version 1 digest never meets it."""
    path = tmp_path / "array.npy"
    np.save(path, np.zeros((4, 4), dtype=np.float32))
    result = IDENTITY_METHODS.get("array_signature").compute(_context(path))
    assert result is not None
    version_one = digest_text(
        canonical_json({"shape": ["4", "4"], "dtype": "<f4", "fortran_order": "False"})
    )
    assert result.value != version_one


def test_a_count_that_is_no_number_is_unexamined_and_says_so() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    held = [item for item in _table() if item.key != "table.column_count"] + [
        _observation("table.column_count", text="four")
    ]
    assert predicate.evaluate(held, COLUMN_RULE) is None
    said = predicate.explain_abstention(held, COLUMN_RULE)
    assert said.status is ResolutionStatus.UNEXAMINED
    assert "is not a number" in said.reason


def test_a_floor_of_one_is_said_in_the_singular() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    said = predicate.explain_abstention(_table(count=0), {"minimum_columns": 1})
    assert "fewer than 1 column" in said.reason
    assert "1 columns" not in said.reason


def test_a_rule_naming_neither_floor_or_both_is_not_evaluable_and_says_which_it_needs() -> None:
    predicate = PREDICATES.get("same_extracted_shape")
    for parameters in ({}, {**COLUMN_RULE, **ARRAY_RULE}):
        with pytest.raises(
            PredicateNotEvaluableError, match="minimum_columns or minimum_dimensions"
        ):
            predicate.evaluate(_table(), parameters)


# ---------------------------------------------------------------------------
# 4. On a pool.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pool(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return build_project(tmp_path_factory.mktemp("same_columns"))


def _entry_ids(project: Project) -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query("SELECT relative_path, entry_id FROM entries").fetchall()
    return {str(path): str(identifier) for path, identifier in rows}


def _claimed_classes(project: Project, rule: str) -> set[frozenset[str]]:
    """The classes a rule published, as sets of paths (a class of one is not published)."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule],
        ).fetchall()
    grouped: dict[str, set[str]] = {}
    for path, key in rows:
        grouped.setdefault(str(key), set()).add(str(path))
    return {frozenset(members) for members in grouped.values()}


def _stored_classes(
    project: Project, digest_key: str, count_key: str, floor: int
) -> set[frozenset[str]]:
    """The classes of the stored digests, straight from the observations (no engine code)."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, d.value_text FROM observations d "
            "JOIN observations n ON n.entry_id = d.entry_id AND n.key = ? "
            "JOIN entries e ON e.entry_id = d.entry_id "
            "WHERE d.key = ? AND n.value_integer >= ?",
            [count_key, digest_key, floor],
        ).fetchall()
    grouped: dict[str, set[str]] = {}
    for path, digest in rows:
        grouped.setdefault(str(digest), set()).add(str(path))
    return {frozenset(members) for members in grouped.values()}


ONE_HEADER = frozenset(
    {
        "plate_one/HeLa_DMSO_quant.csv",
        "plate_two/HeLa_taxol_quant.csv",
        "templates/HeLa_quant_template.csv",
        "plate_five/HeLa_etoposide_quant.csv",
    }
)
SAME_ARRAY = frozenset({"stacks/HeLa_DMSO_mask.npy", "stacks/HeLa_taxol_mask.npy"})


def test_the_rule_groups_one_header_across_folders_and_normalises_case_and_spaces(
    pool: Project,
) -> None:
    classes = _claimed_classes(pool, COLUMNS)
    # The header in two folders, a header-only template and a header typed in capitals with
    # spaces round its names: one class. Nothing else is grouped with it.
    assert classes == {ONE_HEADER}


def test_an_added_column_the_other_delimiters_and_a_single_column_make_no_class_of_their_own(
    pool: Project,
) -> None:
    grouped = set().union(*_claimed_classes(pool, COLUMNS))
    for path in (
        "plate_three/HeLa_nocodazole_quant.csv",
        "plate_four/HeLa_DMSO_quant_semicolon.csv",
        "plate_four/HeLa_DMSO_quant_tabs.tsv",
        "notes/HeLa_passage_list.csv",
        LATE,
    ):
        assert path not in grouped


def test_arrays_of_one_shape_are_a_class_and_another_number_of_dimensions_is_not(
    pool: Project,
) -> None:
    classes = _claimed_classes(pool, ARRAY)
    assert classes == {SAME_ARRAY}
    grouped = set().union(*classes)
    assert "stacks/HeLa_DMSO_series.npy" not in grouped  # three dimensions
    assert "stacks/HeLa_trace.npy" not in grouped  # one dimension, below the rule's floor of two


def test_the_rules_classes_equal_the_classes_of_the_stored_digests_exactly(pool: Project) -> None:
    """Reproduction: the rule adds nothing to, and loses nothing from, the stored digests."""
    stored = _stored_classes(pool, "identity.tabular_schema", "table.column_count", 2)
    assert {members for members in stored if len(members) >= 2} == _claimed_classes(pool, COLUMNS)
    arrays = _stored_classes(pool, "identity.array_signature", "array.dimension_count", 2)
    assert {members for members in arrays if len(members) >= 2} == _claimed_classes(pool, ARRAY)

    # And the projection the file page reads is the same set, singletons included.
    built = column_projection(pool.path, INFERENCE, pool.settings.columns)
    paths = {identifier: path for path, identifier in _entry_ids(pool).items()}
    projected = {
        frozenset(paths[member] for member in members) for members in built.classes.values()
    }
    assert projected == stored


def test_the_journal_counts_the_file_no_dive_read_as_unexamined(pool: Project) -> None:
    with open_catalog(pool.path) as catalog:
        rows = catalog.query(
            "SELECT detail FROM journal_events WHERE event = 'rule.evaluated' "
            "AND run_id = ? AND detail LIKE ?",
            [INFERENCE, f'%"rule_id":"{COLUMNS}"%'],
        ).fetchall()
    assert len(rows) == 1
    tally = json.loads(rows[0][0])
    assert tally["unexamined"] == 1
    assert tally["classes"] == 1
    assert tally["grouped"] == len(ONE_HEADER)
    # The tables of one column and the files that are no table are in no class.
    assert tally["claims"] == len(ONE_HEADER)


def test_every_table_of_the_header_in_two_folders_shows_the_others_as_the_same_columns(
    pool: Project,
) -> None:
    ids = _entry_ids(pool)
    found = columns_of_file(pool.path, INFERENCE, ids["plate_one/HeLa_DMSO_quant.csv"])
    assert found.read
    assert found.names == tuple(HEADER)
    assert found.count == len(HEADER)
    assert found.delimiter == ","
    assert found.same_columns_total == 3
    assert {item.table.path for item in found.same_columns} == ONE_HEADER - {
        "plate_one/HeLa_DMSO_quant.csv"
    }


def test_inclusion_names_the_added_column_and_the_same_names_written_another_way(
    pool: Project,
) -> None:
    ids = _entry_ids(pool)
    found = columns_of_file(pool.path, INFERENCE, ids["plate_one/HeLa_DMSO_quant.csv"])
    inside = {item.table.path: item for item in found.contained_in}
    assert set(inside) == {
        "plate_three/HeLa_nocodazole_quant.csv",
        "plate_four/HeLa_DMSO_quant_semicolon.csv",
        "plate_four/HeLa_DMSO_quant_tabs.tsv",
    }
    assert inside["plate_three/HeLa_nocodazole_quant.csv"].added == ("mean_area",)
    assert not inside["plate_three/HeLa_nocodazole_quant.csv"].same_names
    for other in (
        "plate_four/HeLa_DMSO_quant_semicolon.csv",
        "plate_four/HeLa_DMSO_quant_tabs.tsv",
    ):
        assert inside[other].same_names
        assert inside[other].added == ()
    assert found.contained_in_total == 3
    assert found.contains == ()


def test_the_larger_table_lists_the_tables_whose_columns_it_contains_and_what_they_lack(
    pool: Project,
) -> None:
    ids = _entry_ids(pool)
    found = columns_of_file(pool.path, INFERENCE, ids["plate_three/HeLa_nocodazole_quant.csv"])
    assert found.contained_in_total == 0
    held = {item.table.path: item for item in found.contains}
    assert set(held) == ONE_HEADER | {
        "plate_four/HeLa_DMSO_quant_semicolon.csv",
        "plate_four/HeLa_DMSO_quant_tabs.tsv",
    }
    assert {item.missing for item in found.contains} == {("mean_area",)}
    assert found.contains_total == 6


def test_a_table_of_two_columns_is_in_no_inclusion_list_under_the_default_floor(
    pool: Project,
) -> None:
    ids = _entry_ids(pool)
    found = columns_of_file(pool.path, INFERENCE, ids["plate_six/HeLa_viability.csv"])
    assert found.read
    assert found.contained_in_total == 0
    assert found.contains_total == 0


def test_a_file_that_is_no_table_is_answered_with_its_reason_in_words(pool: Project) -> None:
    ids = _entry_ids(pool)
    for path, said in (
        (LATE, "no column names were stored"),
        ("notes/HeLa_passage_list.csv", "no column names were stored"),
        ("stacks/HeLa_DMSO_mask.npy", "no column names were stored"),
    ):
        found = columns_of_file(pool.path, INFERENCE, ids[path])
        assert not found.read
        assert found.why_not is not None
        assert said in found.why_not
        assert found.same_columns == ()


def test_the_setting_switches_the_inclusion_index_off_and_the_same_columns_stay(
    pool: Project,
) -> None:
    ids = _entry_ids(pool)
    off = pool.settings.columns.model_copy(update={"inclusion": False})
    found = columns_of_file(pool.path, INFERENCE, ids["plate_one/HeLa_DMSO_quant.csv"], off)
    assert not found.inclusion_on
    assert found.contained_in == ()
    assert found.contained_in_total == 0
    assert found.same_columns_total == 3
    built = column_projection(pool.path, INFERENCE, off)
    assert built.node_sets == []
    assert built.postings == {}


def test_the_inclusion_floor_is_a_setting(pool: Project) -> None:
    ids = _entry_ids(pool)
    low = pool.settings.columns.model_copy(update={"inclusion_minimum_columns": 2})
    # The two-column table is indexed, and nothing contains it: its columns are not a subset of
    # anything's.
    found = columns_of_file(pool.path, INFERENCE, ids["plate_six/HeLa_viability.csv"], low)
    assert found.contained_in_total == 0
    # And the four-column tables are not changed by it.
    again = columns_of_file(pool.path, INFERENCE, ids["plate_one/HeLa_DMSO_quant.csv"], low)
    assert again.contained_in_total == 3


def test_the_summary_for_the_quality_report_counts_classes_files_and_folders(pool: Project) -> None:
    summary = same_column_summary(pool.path, INFERENCE)
    assert summary.classes == 1
    assert summary.tables_in_classes == len(ONE_HEADER)
    assert summary.classes_spanning_folders == 1
    # The denominators: the tables whose columns were read, and the delimited tables by extension.
    assert summary.tables_counted == 8
    assert summary.tables_in_run == len(TABLES) + 1  # the pool's tables and the late one
    assert summary.tables_counted < summary.tables_in_run


def test_a_class_in_one_folder_does_not_span_folders() -> None:
    from ingence.columns.stored import ColumnProjection, Table, summary_of
    from ingence.core.settings import ColumnSettings

    built = ColumnProjection(run_id="r", settings=ColumnSettings())
    for index, folder in enumerate(("f", "f", "g", "g", "h", "h")):
        digest = "d" if index < 4 else "e"
        built.tables[f"t{index}"] = Table(
            f"t{index}", f"{folder}/t{index}.csv", folder, digest, 4, None, None
        )
    built.classes = {"d": ["t0", "t1", "t2", "t3"], "e": ["t4", "t5"]}
    summary = summary_of(built)
    assert summary.classes == 2
    assert summary.classes_spanning_folders == 1
    assert summary.tables_in_classes == 6


def test_the_index_is_reached_through_the_registry_and_can_be_disabled(pool: Project) -> None:
    from ingence.columns import COLUMN_INDEXES
    from ingence.core.registry import ComponentNotAvailableError

    assert column_engine().version == "1"
    ids = _entry_ids(pool)
    COLUMN_INDEXES.set_enabled("stored_columns", enabled=False)
    try:
        with pytest.raises(ComponentNotAvailableError):
            columns_of_file(pool.path, INFERENCE, ids["plate_one/HeLa_DMSO_quant.csv"])
    finally:
        COLUMN_INDEXES.set_enabled("stored_columns", enabled=True)
    assert PROJECTION_VERSION == "1"


# ---------------------------------------------------------------------------
# 5. No grouping claim crosses into an experiment.
# ---------------------------------------------------------------------------


def _other_claims(project: Project, run_id: str) -> set[tuple[Any, ...]]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        rows = catalog.query(
            "SELECT c.subject, c.rule_id, c.predicate, CAST(c.object_reference AS VARCHAR) "
            "FROM claims c WHERE c.rule_id NOT IN (?, ?)",
            [COLUMNS, ARRAY],
        ).fetchall()
    return {tuple(row) for row in rows}


def test_no_other_claim_changes_whether_or_not_the_columns_rules_can_group(
    tmp_path: Path,
) -> None:
    """The experiments, stages and families of a run are the same with the two rules silenced."""
    project = build_project(tmp_path)
    grouped = _other_claims(project, INFERENCE)
    assert _claimed_classes(project, COLUMNS), "the rules grouped something to begin with"
    tune(project.path, "minimum_columns", 1000, rules=(COLUMNS,))
    tune(project.path, "minimum_dimensions", 16, rules=(ARRAY,))
    silent = "run_20260101T000005Z_555555555555"
    run_inference(project, run_id=silent, started_at="t")
    assert _claimed_classes(project, COLUMNS) == set()
    assert _other_claims(project, silent) == grouped


def test_the_rules_say_same_columns_and_never_the_same_experiment() -> None:
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.render import render_rule
    from ingence.rulebook.resolve import effective_rulebook

    rules = {rule.rule_id: rule for rule in effective_rulebook(load_revisions()).rules}
    columns = rules[COLUMNS]
    assert columns.version == "2"
    assert "same columns" in render_rule(columns)
    assert "types" not in columns.statement.casefold().replace("kinds of values", "")
    # The respect is "columns"; the rule groups under no experiment primitive.
    assert columns.specialization == "same_schema_as"
    assert "same experiment" not in columns.statement.casefold()
    assert "nothing about which experiment" in columns.rationale
    assert rules[ARRAY].version == "2"


# ---------------------------------------------------------------------------
# 6. One floor, and a project dived by two builds.
# ---------------------------------------------------------------------------


def test_the_page_follows_the_rules_floor_so_the_two_never_disagree(tmp_path: Path) -> None:
    project = build_project(tmp_path, late=False)
    ids = _entry_ids(project)
    before = columns_of_file(project.path, INFERENCE, ids["plate_one/HeLa_DMSO_quant.csv"])
    assert before.same_columns_total == 3
    # A person raises the rule's floor and not the page's setting.
    tune(project.path, "minimum_columns", 5, rules=(COLUMNS,))
    assert project.settings.columns.minimum_columns == 2
    after = columns_of_file(project.path, INFERENCE, ids["plate_one/HeLa_DMSO_quant.csv"])
    assert not after.read
    assert after.why_not is not None
    assert "fewer than 5 columns" in after.why_not
    # The rule, run again at that floor, groups none of the four-column tables either.
    run_inference(project, run_id="run_20260101T000006Z_666666666666", started_at="t")
    assert _claimed_classes(project, COLUMNS) == set()
    built = column_projection(project.path, INFERENCE)
    assert [members for members in built.classes.values() if len(members) >= 2] == []


def test_a_project_dived_by_two_builds_groups_a_header_across_both(tmp_path: Path) -> None:
    """Tables an older build signed and a table signed by today's build make one class.

    The next dive signs the older tables again, because a dive reads again what an older version of
    its reader read. After it every table holds today's signature and the class is one.
    """
    from fixtures.same_columns_pool import CENSUS, DIVE, HEADER, LATER_CENSUS, write_pool
    from ingence.core.settings import ExtractionSettings
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen

    pool_path = tmp_path / "pool"
    write_pool(pool_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool_path])
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    method = IDENTITY_METHODS.get("tabular_schema")
    run_census(project, run_id=CENSUS, started_at="t")
    method.version = "1"
    try:
        run_deepen(project, run_id=DIVE, started_at="t", profile="standard")
    finally:
        method.version = "2"
    new = pool_path / "plate_eight" / "HeLa_cisplatin_quant.csv"
    new.parent.mkdir()
    new.write_text(",".join(HEADER) + "\nA1,HeLa,cisplatin,540\n", encoding="utf-8")
    run_census(project, run_id=LATER_CENSUS, started_at="t")
    later = "run_20260101T000003Z_3333333333aa"
    run_deepen(project, run_id=later, started_at="t", profile="standard")
    run_inference(project, run_id=INFERENCE, started_at="t")

    with open_catalog(project.path) as catalog:
        versions = {
            str(path): str(version)
            for path, version in catalog.query(
                "SELECT e.relative_path, o.observer_version FROM observations o "
                "JOIN entries e ON e.entry_id = o.entry_id WHERE o.key = 'identity.tabular_schema'"
            ).fetchall()
        }
    assert versions["plate_one/HeLa_DMSO_quant.csv"] == "2"
    assert versions["plate_eight/HeLa_cisplatin_quant.csv"] == "2"

    joined = ONE_HEADER | {"plate_eight/HeLa_cisplatin_quant.csv"}
    assert _claimed_classes(project, COLUMNS) == {joined}
    ids = _entry_ids(project)
    page = columns_of_file(project.path, INFERENCE, ids["plate_eight/HeLa_cisplatin_quant.csv"])
    assert {item.table.path for item in page.same_columns} == ONE_HEADER
