"""Themes: the same words under other identifiers give the same themes.

A theme result depends on what the documents say, never on what the units are called. Unit
identifiers include a pool's absolute path, so a folder that is moved or copied gets different
identifiers. The themes must not change with them. If the embedding's rows followed the identifiers,
a moved folder could get another resolution and another number of themes.
"""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import SENSES, theme_documents
from ingence.themes import themes_engine

TRIALS = 12


def _renamed(trial: int) -> tuple[dict[str, str], dict[str, list[str]]]:
    plant = theme_documents()
    new = {
        unit: hashlib.blake2b(f"{trial}:{unit}".encode(), digest_size=8).hexdigest()
        for unit in plant.documents
    }
    return new, {new[unit]: words for unit, words in plant.documents.items()}


def _shape(result, names: dict[str, str]):
    """The result in the original names: each unit's theme and the resolution picked."""
    back = {new: old for old, new in names.items()}
    return (
        result.resolution,
        len(result.themes),
        result.placement_seed,
        {
            back[unit]: (a.theme_id, a.broad_id, a.unplaced_reason)
            for unit, a in result.assignments.items()
        },
        [(s.label, s.units, s.stability) for s in result.themes],
    )


def test_renaming_the_units_does_not_change_the_themes() -> None:
    plant = theme_documents()
    identity = {unit: unit for unit in plant.documents}
    reference = _shape(themes_engine().compute(plant.documents, senses=SENSES), identity)
    for trial in range(TRIALS):
        names, documents = _renamed(trial)
        found = _shape(themes_engine().compute(documents, senses=SENSES), names)
        assert found == reference, trial
