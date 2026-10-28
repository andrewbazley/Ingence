#!/usr/bin/env python3
"""Emit the frozen record contract as JSON Schema under ``schemas/v1/``.

The exit gate for the schema freeze is that two people could implement against
the contract independently. Python type annotations are not that artifact:
reading them requires the same language, the same library, and a willingness to
follow imports. These files are.

Generated from the Pydantic models, which are authoritative, and checked into the
repository so a change to a record type shows up as a diff in review rather than
being discovered by whoever regenerates them next. A test asserts the checked-in
files match what the models produce, so drift fails the test.

Run with ``--check`` to verify without writing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The checkout this script lives in is the one it exports. An editable install points at one
# checkout. Run from a second worktree, or from the commit hook there, the imports below would
# read the other checkout's models and call this one's exports stale.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ingence import SCHEMA_VERSION
from ingence.core.ids import canonical_json
from ingence.core.invariants import INVARIANTS
from ingence.core.relations import PRIMITIVE_PROPERTIES, SPECIALIZATION_CATALOG, Primitive
from ingence.core.schema import PERSISTED_RECORDS
from ingence.storage.layout import TABLES

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIRECTORY = REPOSITORY_ROOT / "schemas" / f"v{SCHEMA_VERSION.split('.')[0]}"
#: The Dashboard's typed boundary: one JSON Schema per ``/api/v1`` response model, beside the
#: core's, from which the page's TypeScript declarations are generated.
API_DIRECTORY = REPOSITORY_ROOT / "schemas" / "api" / "v1"


def _snake(name: str) -> str:
    """Return a class name as a file name: ``DictionaryEntry`` to ``dictionary_entry``."""
    out: list[str] = []
    for index, character in enumerate(name):
        if character.isupper() and index:
            out.append("_")
        out.append(character.lower())
    return "".join(out)


def record_schemas() -> dict[str, str]:
    """Return one JSON Schema document per persisted record type."""
    return {
        f"{_snake(record.__name__)}.schema.json": canonical_json(record.model_json_schema())
        for record in PERSISTED_RECORDS
    }


def relations_document() -> str:
    """Return the primitive signatures and the specialization catalog as data.

    Published because an implementation that does not know which primitives are
    symmetric cannot compute an edge identifier that agrees with this one.
    """
    return canonical_json(
        {
            "schema_version": SCHEMA_VERSION,
            "primitives": [
                {
                    "name": primitive.value,
                    "directed": props.directed,
                    "symmetric": props.symmetric,
                    "transitive": props.transitive,
                    "acyclic": props.acyclic,
                    "order_shape": props.order_shape.value,
                    "observationally_complete": props.observationally_complete,
                    "substitutive": props.substitutive,
                    "forms_forest": props.forms_forest,
                    "closure_meaningful": props.closure_meaningful,
                    "transitivity_scoped_to_respect": props.transitivity_scoped_to_respect,
                    "requires_respect": props.requires_respect,
                    "requires_expansion_state": props.requires_expansion_state,
                    "total_within_series": props.total_within_series,
                    "permits_cycles": props.permits_cycles,
                    "weighted": props.weighted,
                    "definition": props.definition,
                    "identity_condition": props.identity_condition,
                    "maps_to": list(props.maps_to),
                }
                for primitive in Primitive
                for props in (PRIMITIVE_PROPERTIES[primitive],)
            ],
            "specializations": [
                {
                    "name": entry.name,
                    "parent": entry.parent.value,
                    "respect": entry.respect,
                    "additional": list(entry.additional),
                    "definition": entry.definition,
                    "origin": entry.origin.value,
                    "enabled": entry.enabled,
                }
                for entry in SPECIALIZATION_CATALOG
            ],
        }
    )


def invariants_document() -> str:
    """Return the publication invariants as data, so they can be reimplemented."""
    return canonical_json(
        {
            "schema_version": SCHEMA_VERSION,
            "invariants": [
                {
                    "id": invariant.invariant_id,
                    "statement": invariant.statement,
                    "primitives": [p.value for p in invariant.primitives],
                    "differential": invariant.differential,
                }
                for invariant in INVARIANTS
            ],
        }
    )


def storage_document() -> str:
    """Return the on-disk table declarations, including column types."""
    return canonical_json(
        {
            "schema_version": SCHEMA_VERSION,
            "tables": [
                {
                    "name": spec.name,
                    "directory": spec.directory,
                    "record": spec.model.__name__,
                    "spans_runs": spec.spans_runs,
                    "primary_key": list(spec.primary_key),
                    "sort_by": list(spec.sort_by),
                    "columns": [
                        {
                            "name": field.name,
                            "type": str(field.type),
                            "nullable": field.nullable,
                        }
                        for field in spec.schema
                    ],
                }
                for spec in TABLES
            ],
        }
    )


def documents() -> dict[str, str]:
    """Return every file that belongs under the schema directory."""
    files = record_schemas()
    files["relations.json"] = relations_document()
    files["invariants.json"] = invariants_document()
    files["storage.json"] = storage_document()
    return files


def api_documents() -> dict[str, str]:
    """Return one JSON Schema per ``/api/v1`` response model, by the name it is registered under.

    Every route module is imported first, so each has registered its models.
    """
    from ingence.api.v1 import load_routes
    from ingence.api.v1.models import RESPONSE_MODELS

    load_routes()
    return {
        f"{name}.schema.json": canonical_json(model.model_json_schema(mode="serialization"))
        for name, model in sorted(RESPONSE_MODELS.items())
    }


def _stale(expected: dict[str, str], directory: Path) -> list[str]:
    """Name every file under ``directory`` that differs from, or is missing from, ``expected``."""
    found: list[str] = []
    for name, content in sorted(expected.items()):
        path = directory / name
        if not path.is_file() or path.read_text(encoding="utf-8").rstrip("\n") != content:
            found.append(f"out of date: {directory.name}/{name}")
    found.extend(
        f"no longer generated: {directory.name}/{path.name}"
        for path in sorted(directory.glob("*.json"))
        if path.name not in expected
    )
    return found


def main(argv: list[str]) -> int:
    """Write or verify the exported schema files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the checked-in files match the models, without writing",
    )
    args = parser.parse_args(argv[1:])

    targets = ((SCHEMA_DIRECTORY, documents()), (API_DIRECTORY, api_documents()))
    if args.check:
        stale = [line for directory, expected in targets for line in _stale(expected, directory)]
        if stale:
            for line in stale:
                print(line, file=sys.stderr)
            print(
                "\nRegenerate with: python tools/export_schemas.py",
                file=sys.stderr,
            )
            return 1
        return 0

    for directory, expected in targets:
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in sorted(expected.items()):
            (directory / name).write_text(content + "\n", encoding="utf-8")
        print(f"wrote {len(expected)} files to {directory.relative_to(REPOSITORY_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
