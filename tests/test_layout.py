"""The in-memory contract and the on-disk contract agree, field for field.

Pydantic models and Arrow schemas are written separately on purpose. This is the
test that makes that safe: drift between them fails here rather than producing a
column nobody reads or a field nobody stores.
"""

from __future__ import annotations

import pytest

from ingence.core.schema import PERSISTED_RECORDS
from ingence.storage.layout import JSON_SUFFIX, TABLES, table_for


def test_every_persisted_record_has_a_table():
    declared = {spec.model for spec in TABLES}
    assert declared == set(PERSISTED_RECORDS)


def test_table_names_and_models_are_unique():
    assert len({spec.name for spec in TABLES}) == len(TABLES)
    assert len({spec.model for spec in TABLES}) == len(TABLES)


@pytest.mark.parametrize("spec", TABLES, ids=lambda spec: spec.name)
def test_every_column_corresponds_to_a_model_field(spec):
    fields = set(spec.model.model_fields)
    for column in spec.schema.names:
        base = column[: -len(JSON_SUFFIX)] if column.endswith(JSON_SUFFIX) else column
        assert base in fields, f"{spec.name}.{column} has no field on {spec.model.__name__}"


@pytest.mark.parametrize("spec", TABLES, ids=lambda spec: spec.name)
def test_every_model_field_has_a_column(spec):
    columns = set(spec.schema.names)
    for field in spec.model.model_fields:
        assert field in columns or f"{field}{JSON_SUFFIX}" in columns, (
            f"{spec.model.__name__}.{field} has no column in {spec.name}"
        )


@pytest.mark.parametrize("spec", TABLES, ids=lambda spec: spec.name)
def test_primary_key_and_sort_columns_exist(spec):
    columns = set(spec.schema.names)
    for column in (*spec.primary_key, *spec.sort_by):
        assert column in columns


@pytest.mark.parametrize("spec", TABLES, ids=lambda spec: spec.name)
def test_every_table_is_sorted_before_it_is_written(spec):
    """Determinism by construction. An unsorted write differs on every run."""
    assert spec.sort_by


def test_lookup_by_model_round_trips():
    for spec in TABLES:
        assert table_for(spec.model) is spec
