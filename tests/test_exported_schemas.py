"""The checked-in schema exports match the models they were generated from.

The exports are the artifact an independent implementation reads. If they drift
from the models, the contract published and the contract enforced are two
different contracts, and only one of them is tested.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ingence.core.schema import PERSISTED_RECORDS

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIRECTORY = REPOSITORY_ROOT / "schemas" / "v1"


def test_the_exports_are_up_to_date():
    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "tools" / "export_schemas.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_every_persisted_record_is_exported():
    exported = {path.name for path in SCHEMA_DIRECTORY.glob("*.schema.json")}
    assert len(exported) == len(PERSISTED_RECORDS)


def test_the_relation_export_carries_all_eight_signatures():
    document = json.loads((SCHEMA_DIRECTORY / "relations.json").read_text(encoding="utf-8"))
    assert len(document["primitives"]) == 8
    assert len(document["specializations"]) == 33


def test_the_invariant_export_carries_all_ten():
    document = json.loads((SCHEMA_DIRECTORY / "invariants.json").read_text(encoding="utf-8"))
    assert len(document["invariants"]) == 10


def test_the_storage_export_describes_every_column():
    document = json.loads((SCHEMA_DIRECTORY / "storage.json").read_text(encoding="utf-8"))
    assert len(document["tables"]) == len(PERSISTED_RECORDS)
    for table in document["tables"]:
        assert table["columns"]
        assert table["sort_by"]
