"""The TIFF reader at version 2: who wrote the file, as readings.

Every TIFF here is built from the format's structure by ``fixtures.instrument_files`` with invented
values: the writer's tags, an Exif directory, a vendor's private tags, and the OME, ImageJ and
plate-reader descriptions a reader meets. Nothing here is a conclusion. Each tag becomes a reading
keyed plainly, for the rules that say which program made a file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.instrument_files import tiff_file
from ingence.core.outcomes import ScanOutcome
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext, ExtractorResult

load_builtins()

SOFTWARE = 305
MAKE = 271
MODEL = 272
ARTIST = 315
HOST = 316
DOCUMENT = 269
PAGE = 285

_OME = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06" Creator="vornscope 4.2">'
    '<Instrument ID="Instrument:0"><Microscope Manufacturer="Vorn" Model="Kelt 7"/>'
    '<Objective ID="Objective:0" Model="Plan 63x/1.40" NominalMagnification="63" LensNA="1.4"/>'
    "</Instrument>"
    '<Image ID="Image:0"><AcquisitionDate>2026-03-04T18:00:00</AcquisitionDate>'
    '<Pixels ID="Pixels:0"><Channel ID="Channel:0" Name="DAPI"/>'
    '<Channel ID="Channel:1" Name="GFP &amp; friends"/><Channel ID="Channel:2"/></Pixels>'
    "</Image></OME>"
)


def _extract(tmp_path: Path, data: bytes, name: str = "stack.tif") -> ExtractorResult:
    path = tmp_path / name
    path.write_bytes(data)
    context = ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=name,
        extensions=frozenset({".tif"}),
        size_bytes=len(data),
        media_type="image/tiff",
        limits=None,
    )
    return EXTRACTORS.get("tiff").extract(context)  # type: ignore[arg-type]


def _readings(result: ExtractorResult) -> dict[str, object]:
    assert result.outcome is ScanOutcome.EXTRACTED
    return {reading.key: reading.value for reading in result.readings}


def test_the_reader_is_at_version_2() -> None:
    assert EXTRACTORS.get("tiff").version == "2"
    assert EXTRACTORS.declaration("tiff").version == "2"


def test_the_writers_text_tags_become_readings_keyed_plainly(tmp_path) -> None:
    readings = _readings(
        _extract(
            tmp_path,
            tiff_file(
                text_tags={
                    SOFTWARE: "vornscope 4.2.1",
                    MAKE: "Vorn",
                    MODEL: "Kelt 7",
                    ARTIST: "a. person",
                    HOST: "scope-pc-3",
                    DOCUMENT: "plate_a01",
                    PAGE: "field 12",
                }
            ),
        )
    )
    assert readings["image.software"] == "vornscope 4.2.1"
    assert readings["image.make"] == "Vorn"
    assert readings["image.model"] == "Kelt 7"
    assert readings["image.artist"] == "a. person"
    assert readings["image.host_computer"] == "scope-pc-3"
    assert readings["image.document_name"] == "plate_a01"
    assert readings["image.page_name"] == "field 12"
    assert "image.writer_fingerprint" not in readings
    assert "image.vendor_tags" not in readings


def test_a_tiff_without_those_tags_records_none_of_them(tmp_path) -> None:
    readings = _readings(_extract(tmp_path, tiff_file(pages=2)))
    assert not any(key.startswith("image.ome_") for key in readings)
    assert {"image.software", "image.make", "image.exif_make", "image.imagej_version"}.isdisjoint(
        readings
    )
    assert readings["image.page_count"] == 2


def test_a_tag_is_bounded_and_an_empty_one_is_nothing(tmp_path) -> None:
    readings = _readings(
        _extract(tmp_path, tiff_file(text_tags={SOFTWARE: "x" * 1000, MAKE: "  "}))
    )
    assert readings["image.software"] == "x" * 256
    assert "image.make" not in readings


def test_the_exif_make_and_model_are_read_from_their_own_directory(tmp_path) -> None:
    readings = _readings(
        _extract(tmp_path, tiff_file(text_tags={MAKE: "Vorn"}, exif={MAKE: "Vorn", MODEL: "K-7"}))
    )
    assert readings["image.make"] == "Vorn"
    assert readings["image.exif_make"] == "Vorn"
    assert readings["image.exif_model"] == "K-7"


def test_the_private_tags_are_a_writers_fingerprint_and_the_vendors_are_named(tmp_path) -> None:
    readings = _readings(
        _extract(
            tmp_path,
            tiff_file(
                private_tags={
                    51123: b'{"Summary": {}}',
                    33628: bytes(8),
                    34361: b"[Acquisition]\n",
                    40000: b"x",
                }
            ),
        )
    )
    assert readings["image.writer_fingerprint"] == "33628,34361,40000,51123"
    assert readings["image.vendor_tags"] == "fluoview,metamorph,micromanager"


def test_an_imagej_tiff_names_its_version(tmp_path) -> None:
    readings = _readings(_extract(tmp_path, tiff_file(imagej_info="sLensName = invented\n")))
    assert readings["image.description_kind"] == "imagej"
    assert readings["image.imagej_version"] == "1.54f"
    assert readings["image.writer_fingerprint"] == "50838,50839"


def test_ome_xml_names_its_creator_instrument_objective_and_channels(tmp_path) -> None:
    readings = _readings(_extract(tmp_path, tiff_file(description=_OME)))
    assert readings["image.description_kind"] == "ome-xml"
    assert readings["image.ome_acquisition_date"] == "2026-03-04T18:00:00"
    assert readings["image.ome_creator"] == "vornscope 4.2"
    assert readings["image.ome_instrument"] == "Vorn Kelt 7"
    assert readings["image.ome_objective"] == "Plan 63x/1.40"
    assert readings["image.ome_channel_names"] == "DAPI, GFP & friends"


def test_an_objective_without_a_model_is_said_by_its_magnification_and_aperture(tmp_path) -> None:
    ome = _OME.replace(' Model="Plan 63x/1.40"', "")
    readings = _readings(_extract(tmp_path, tiff_file(description=ome)))
    assert readings["image.ome_objective"] == "63x 1.4"


def test_a_plate_readers_xml_description_is_read_as_xml_and_concludes_nothing(tmp_path) -> None:
    xml = '<?xml version="1.0"?><PlateRead Reader="vornread 2"><Well>A01</Well></PlateRead>'
    readings = _readings(_extract(tmp_path, tiff_file(description=xml)))
    assert readings["image.description_kind"] == "xml"
    assert not any(key.startswith("image.ome_") for key in readings)


def test_the_fixture_with_the_new_tags_is_a_tiff_the_reference_reader_accepts(tmp_path) -> None:
    tifffile = pytest.importorskip("tifffile")
    path = tmp_path / "tagged.tif"
    path.write_bytes(
        tiff_file(
            text_tags={SOFTWARE: "vornscope", MAKE: "Vorn"},
            private_tags={51123: b'{"Summary": {}}'},
            exif={MAKE: "Vorn", MODEL: "K-7"},
            date_time="2026:03:04 06:00:00",
        )
    )
    with tifffile.TiffFile(path) as tif:
        tags = tif.pages[0].tags
        assert tags["Software"].value == "vornscope"
        assert tags["Make"].value == "Vorn"
        assert tags["DateTime"].value == "2026:03:04 06:00:00"
        assert 51123 in tags
        assert "Vorn" in str(tags["ExifTag"].value)
