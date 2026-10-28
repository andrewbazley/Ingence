"""A crop that copied its recording's acquisition clock is made from that recording.

Every name is invented, and the files are built from the formats' structure.

Two recordings of one experiment, each with its own clock; a crop cut from each, named for the
cells and not the recording, carrying the source clock an image-analysis program copies; and a
plain image carrying no clock. The run groups each recording with its crop by the instant,
proposes the crop as made from the recording, and leaves the plain image alone.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.settings import ExtractionSettings
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.instrument_files import julian_day, nd2_file, tiff_file

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"
INFER = "run_20260101T000003Z_333333333333"
FOLDER = "mira/2024-02-05_kelt_dapi"
FIRST = dt.datetime(2024, 2, 5, 14, 30, 11, tzinfo=dt.UTC)
SECOND = dt.datetime(2024, 2, 5, 15, 2, 48, tzinfo=dt.UTC)


def _info(moment: dt.datetime) -> str:
    return f"dTimeAbsolute = {julian_day(moment)}\nsLensName = invented\n"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("e4_source_clock")
    pool = base / "pool" / FOLDER
    (pool / "raw").mkdir(parents=True)
    (pool / "analysis").mkdir(parents=True)
    (pool / "raw" / "kelt_dapi_001.nd2").write_bytes(nd2_file(julian_day=julian_day(FIRST)))
    (pool / "raw" / "kelt_dapi_002.nd2").write_bytes(nd2_file(julian_day=julian_day(SECOND)))
    (pool / "analysis" / "cells_cropped.tif").write_bytes(tiff_file(imagej_info=_info(FIRST)))
    (pool / "analysis" / "cells_cropped_two.tif").write_bytes(tiff_file(imagej_info=_info(SECOND)))
    (pool / "analysis" / "overview_plain.tif").write_bytes(tiff_file())
    created = Project.create(base / "project", created_at="t", roots=[base / "pool"])
    created = created.save_settings(
        created.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(created, run_id=CENSUS, started_at="t")
    run_deepen(created, run_id=DIVE, started_at="t", profile="deep")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def test_a_crop_carrying_its_movies_clock_is_made_from_the_movie(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        classes = catalog.query(
            "SELECT c.object_reference, string_agg(e.name, ', ' ORDER BY e.name) FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'same_source_clock' AND c.resolution_status = 'resolved' "
            "GROUP BY 1 ORDER BY 2"
        ).fetchall()
        proposed = catalog.query(
            "SELECT s.name, t.name FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id "
            "WHERE c.rule_id = 'derivation_from_source_clock' ORDER BY 1, 2"
        ).fetchall()
    grouped = sorted(members for _, members in classes)
    assert grouped == [
        "cells_cropped.tif, kelt_dapi_001.nd2",
        "cells_cropped_two.tif, kelt_dapi_002.nd2",
    ]
    assert [(str(s), str(t)) for s, t in proposed] == [
        ("kelt_dapi_001.nd2", "cells_cropped.tif"),
        ("kelt_dapi_002.nd2", "cells_cropped_two.tif"),
    ]


def test_two_recordings_of_one_instant_name_no_source() -> None:
    from ingence.rulebook.predicates.clocks import DerivesFromSourceClock

    class _Entry:
        def __init__(self, entry_id: str) -> None:
            self.entry_id = entry_id
            self.parent_entry_id = "folder"

    class _Observation:
        def __init__(self, key: str, value: int) -> None:
            self.key = key
            self.value_integer = value
            self.observation_id = f"obs_{key}_{value}"

    class _Claim:
        predicate = "classifies.acquisition_stage_from_format"
        resolution_status = "resolved"
        object_reference = "acquisition"

    class _Records:
        def __init__(self, entry_id: str, key: str, recording: bool) -> None:
            self.entry = _Entry(entry_id)
            self.observations = (_Observation(key, 1_000_000_000),)
            self.claims = (_Claim(),) if recording else ()

    rule = DerivesFromSourceClock()
    one = _Records("entry_a", "image.acquired_ns", True)
    two = _Records("entry_b", "image.acquired_ns", True)
    crop = _Records("entry_c", "image.source_acquired_ns", False)
    assert rule.pairs_within("", [one, crop], {}) == {"entry_a": ("entry_c",)}
    assert rule.pairs_within("", [one, two, crop], {}) == {}, "a tie abstains"
    assert rule.pairs_within("", [crop], {}) == {}, "no recording, no source"
