"""OOXML documents and presentations, read with the standard library and nothing else.

Every package here is built by hand from its parts, so each test says which part
it is about and cannot pass because some application happened to write a file a
particular way.
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

from ingence.core.outcomes import CLOCK_TIER_OF, ClockTier, ScanOutcome, TimeSource
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext
from ingence.extractors.clocks import parse_w3cdtf
from ingence.extractors.limits import Limits
from ingence.extractors.office import _slides_in_order

load_builtins()

TRANSITIONAL = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "document": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "slide": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
}
STRICT = {
    "w": "http://purl.oclc.org/ooxml/wordprocessingml/main",
    "a": "http://purl.oclc.org/ooxml/drawingml/main",
    "p": "http://purl.oclc.org/ooxml/presentationml/main",
    "r": "http://purl.oclc.org/ooxml/officeDocument/relationships",
    "document": "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
    "slide": "http://purl.oclc.org/ooxml/officeDocument/relationships/slide",
}
PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
CORE_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
)
EXTENDED_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
)
HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"


def _relationships(*entries: tuple[str, str, str, bool]) -> str:
    body = "".join(
        f'<Relationship Id="{identifier}" Type="{kind}" Target="{target}"'
        + (' TargetMode="External"' if external else "")
        + "/>"
        for identifier, kind, target, external in entries
    )
    return (
        f'<?xml version="1.0"?><Relationships xmlns="{PACKAGE_RELATIONSHIPS}">'
        f"{body}</Relationships>"
    )


def _core(created: str | None = "2026-03-04T09:30:00Z", **values: str) -> str:
    fields = "".join(
        f"<dc:{key}>{value}</dc:{key}>" for key, value in values.items() if key != "keywords"
    )
    if "keywords" in values:
        fields += f"<cp:keywords>{values['keywords']}</cp:keywords>"
    if created is not None:
        fields += f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
    return (
        '<?xml version="1.0"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"{fields}</cp:coreProperties>"
    )


def _package(path: Path, parts: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return path


def _docx(
    path: Path,
    body: str,
    *,
    names: dict[str, str] = TRANSITIONAL,
    core: str | None = None,
    document_relationships: str | None = None,
) -> Path:
    parts: dict[str, str | bytes] = {
        "_rels/.rels": _relationships(
            ("rId1", names["document"], "word/document.xml", False),
            ("rId2", CORE_RELATIONSHIP, "docProps/core.xml", False),
            ("rId3", EXTENDED_RELATIONSHIP, "docProps/app.xml", False),
        ),
        "word/document.xml": (
            f'<?xml version="1.0"?><w:document xmlns:w="{names["w"]}">'
            f"<w:body>{body}</w:body></w:document>"
        ),
        "docProps/core.xml": core
        if core is not None
        else _core(title="Plate layout", creator="A. Author"),
        "docProps/app.xml": (
            '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            "<Application>SyntheticWriter</Application></Properties>"
        ),
    }
    if document_relationships is not None:
        parts["word/_rels/document.xml.rels"] = document_relationships
    return _package(path, parts)


def _paragraph(text: str, w: str = "w") -> str:
    return f"<{w}:p><{w}:r><{w}:t>{text}</{w}:t></{w}:r></{w}:p>"


def _context(path: Path, extension: str, *, limits: Limits | None = None) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({extension}),
        size_bytes=path.stat().st_size,
        limits=limits,
    )


def _extract(path: Path, extension: str = ".docx", *, limits: Limits | None = None):
    return EXTRACTORS.get("office").extract(_context(path, extension, limits=limits))


def _readings(result: object) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Documents.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("names", [TRANSITIONAL, STRICT], ids=["transitional", "strict"])
def test_a_document_yields_text_structure_and_a_tier_two_clock(
    tmp_path: Path, names: dict[str, str]
) -> None:
    """Both spellings of every namespace, because a reader of one reads nothing of the other."""
    table = f"<w:tbl><w:tr><w:tc>{_paragraph('cell')}</w:tc></w:tr></w:tbl>"
    path = _docx(
        tmp_path / "protocol.docx",
        _paragraph("Methods") + table + _paragraph("Results"),
        names=names,
    )
    result = _extract(path)

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["document.paragraph_count"] == 3
    assert readings["document.table_count"] == 1
    assert readings["document.character_count"] == len("Methods") + len("cell") + len("Results") + 3
    assert readings["document.text_bounded"] is False
    assert readings["document.title"] == "Plate layout"
    assert readings["document.author"] == "A. Author"
    assert readings["document.creator"] == "SyntheticWriter"
    assert result.event_time_source is TimeSource.DOCUMENT
    assert CLOCK_TIER_OF[result.event_time_source] is ClockTier.DOCUMENT
    assert result.event_time_ns == parse_w3cdtf("2026-03-04T09:30:00Z")[0]  # type: ignore[index]


def test_tab_stops_are_layout_not_text(tmp_path: Path) -> None:
    """A tab stop and a tab share an element name, and only one of them is in the text.

    Counting every ``w:tab`` would add a character for each tab stop a paragraph defines.
    """
    body = (
        '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/><w:tab w:val="left" w:pos="1440"/>'
        "</w:tabs></w:pPr><w:r><w:t>ab</w:t><w:tab/><w:t>cd</w:t></w:r></w:p>"
    )
    readings = _readings(_extract(_docx(tmp_path / "tabs.docx", body)))
    assert readings["document.character_count"] == len("ab") + 1 + len("cd") + 1


def test_deleted_text_and_field_codes_are_not_what_a_reader_sees(tmp_path: Path) -> None:
    body = (
        "<w:p><w:del><w:r><w:delText>removed</w:delText></w:r></w:del>"
        "<w:r><w:instrText>PAGE</w:instrText></w:r><w:r><w:t>kept</w:t></w:r></w:p>"
    )
    readings = _readings(_extract(_docx(tmp_path / "tracked.docx", body)))
    assert readings["document.character_count"] == len("kept") + 1


def test_a_missing_offset_is_utc_and_a_date_short_of_a_day_abstains(tmp_path: Path) -> None:
    """The W3CDTF reading of dates (``parse_w3cdtf``), applied to the package that defines them."""
    naive = _extract(
        _docx(tmp_path / "naive.docx", _paragraph("x"), core=_core("2026-03-04T09:30:00"))
    )
    assert naive.event_time_ns == parse_w3cdtf("2026-03-04T09:30:00Z")[0]  # type: ignore[index]

    partial = _extract(_docx(tmp_path / "partial.docx", _paragraph("x"), core=_core("2026-03")))
    assert partial.event_time_ns is None
    assert _readings(partial)["document.created_unparsed"] == "2026-03"


def test_a_document_with_no_core_properties_offers_no_clock(tmp_path: Path) -> None:
    path = _package(
        tmp_path / "bare.docx",
        {
            "_rels/.rels": _relationships(
                ("rId1", TRANSITIONAL["document"], "word/document.xml", False)
            ),
            "word/document.xml": (
                f'<w:document xmlns:w="{TRANSITIONAL["w"]}">'
                f"<w:body>{_paragraph('x')}</w:body></w:document>"
            ),
        },
    )
    result = _extract(path)
    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_ns is None


def test_an_empty_document_records_the_absence_of_text(tmp_path: Path) -> None:
    readings = _readings(_extract(_docx(tmp_path / "empty.docx", "")))
    assert readings["document.text_absent"] is True


def test_a_declared_external_file_is_a_reference_and_a_web_address_is_not(tmp_path: Path) -> None:
    """A linked workbook is a declared reference, written by the application, not guessed."""
    relationships = _relationships(
        ("rId5", HYPERLINK, "file:///C:/data/plate_1.xlsx", True),
        ("rId6", HYPERLINK, "https://example.org/protocol", True),
        ("rId7", HYPERLINK, "mailto:someone@example.org", True),
        (
            "rId8",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
            "media/image1.png",
            False,
        ),
    )
    path = _docx(tmp_path / "linked.docx", _paragraph("x"), document_relationships=relationships)
    result = _extract(path)
    assert result.references == ("file:///C:/data/plate_1.xlsx",)
    assert _readings(result)["document.external_reference_count"] == 1


# ---------------------------------------------------------------------------
# Presentations.
# ---------------------------------------------------------------------------


def _pptx(path: Path, slides: dict[str, str], order: list[str]) -> Path:
    names = TRANSITIONAL
    listed = "".join(
        f'<p:sldId id="{256 + index}" r:id="rId{slide}"/>' for index, slide in enumerate(order)
    )
    parts: dict[str, str | bytes] = {
        "_rels/.rels": _relationships(("rId1", names["document"], "ppt/presentation.xml", False)),
        "ppt/presentation.xml": (
            f'<p:presentation xmlns:p="{names["p"]}" xmlns:r="{names["r"]}">'
            f"<p:sldIdLst>{listed}</p:sldIdLst></p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": _relationships(
            *(
                (f"rId{slide}", names["slide"], f"slides/slide{slide}.xml", False)
                for slide in slides
            )
        ),
    }
    for slide, text in slides.items():
        parts[f"ppt/slides/slide{slide}.xml"] = (
            f'<p:sld xmlns:p="{names["p"]}" xmlns:a="{names["a"]}">'
            "<p:cSld><p:spTree><p:sp><p:txBody>"
            f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )
    return _package(path, parts)


def test_a_presentation_counts_its_slides_and_reads_them(tmp_path: Path) -> None:
    path = _pptx(tmp_path / "deck.pptx", {"1": "Intro", "10": "Summary"}, order=["10", "1"])
    result = _extract(path, ".pptx")

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["document.slide_count"] == 2
    assert readings["document.slides_sampled"] == 2
    assert readings["document.character_count"] == len("Intro") + len("Summary") + 2


def test_slides_are_read_in_presentation_order_not_member_order() -> None:
    """``slide10.xml`` sorts before ``slide2.xml``, and nobody reading the deck sees it that way."""
    relationships = [
        ("rId2", "slide", "ppt/slides/slide2.xml", False),
        ("rId10", "slide", "ppt/slides/slide10.xml", False),
        ("rId11", "slideLayout", "ppt/slideLayouts/slideLayout1.xml", False),
    ]
    assert _slides_in_order(relationships, ["rId2", "rId10"]) == [
        "ppt/slides/slide2.xml",
        "ppt/slides/slide10.xml",
    ]
    assert _slides_in_order(relationships, ["rId10", "rId2", "rId99"]) == [
        "ppt/slides/slide10.xml",
        "ppt/slides/slide2.xml",
    ]


# ---------------------------------------------------------------------------
# Bounds, refusals and the files that are not what they claim.
# ---------------------------------------------------------------------------


def test_a_part_declaring_a_document_type_is_refused_before_any_entity_is_read(
    tmp_path: Path,
) -> None:
    """OPC forbids a DTD in any part, and every entity-expansion attack needs one.

    A billion laughs, refused at the declaration rather than survived by a limit.
    """
    entities = ['<!ENTITY lol0 "lol">']
    for level in range(1, 10):
        expansion = f"&lol{level - 1};" * 10
        entities.append(f'<!ENTITY lol{level} "{expansion}">')
    hostile = (
        f'<?xml version="1.0"?><!DOCTYPE lolz [{"".join(entities)}]>'
        f'<w:document xmlns:w="{TRANSITIONAL["w"]}"><w:body><w:p><w:r>'
        "<w:t>&lol9;</w:t></w:r></w:p></w:body></w:document>"
    )
    rebuilt = _package(
        tmp_path / "hostile.docx",
        {
            "_rels/.rels": _relationships(
                ("rId1", TRANSITIONAL["document"], "word/document.xml", False)
            ),
            "word/document.xml": hostile,
        },
    )

    started = time.monotonic()
    result = _extract(rebuilt)
    assert time.monotonic() - started < 5
    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["document.part_refused"] is True
    assert "document.character_count" not in readings
    assert "document type" in result.warnings[0]


def test_decompression_stops_at_the_byte_ceiling_and_says_so(tmp_path: Path) -> None:
    """A declared size cannot talk past the bound, because the bound is on bytes produced."""
    path = _docx(tmp_path / "long.docx", _paragraph("x" * 50_000))
    result = _extract(path, limits=Limits(max_bytes_read=4096))
    assert result.bytes_read <= 4096
    assert _readings(result)["document.text_bounded"] is True


def test_text_stops_at_the_character_budget_and_says_so(tmp_path: Path) -> None:
    path = _docx(tmp_path / "wordy.docx", _paragraph("y" * 500) * 3)
    readings = _readings(_extract(path, limits=Limits(max_text_chars=100)))
    assert readings["document.character_count"] == 100
    assert readings["document.text_bounded"] is True
    assert "document.text_absent" not in readings


def test_an_encrypted_document_is_left_to_the_compound_extractor(tmp_path: Path) -> None:
    """Office stores a password-protected package inside compound storage, not a ZIP."""
    path = tmp_path / "locked.docx"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 1016)
    result = _extract(path)
    assert result.outcome is ScanOutcome.SKIPPED_BY_POLICY
    assert "compound" in result.warnings[0]


def test_a_zip_that_is_not_an_office_package_becomes_a_content_error(tmp_path: Path) -> None:
    """Attempted and not readable as claimed, as an impostor workbook is for spreadsheets."""
    path = _package(tmp_path / "impostor.docx", {"readme.txt": "not a document\n"})
    assert _extract(path).outcome is ScanOutcome.CONTENT_ERROR


def test_a_corrupt_package_fails_that_file_and_nothing_else(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.docx"
    path.write_bytes(b"PK\x03\x04 this is not a real archive")
    result = _extract(path)
    assert result.outcome is ScanOutcome.CONTENT_ERROR
    assert result.error_type


def test_workbooks_stay_with_the_spreadsheet_extractor() -> None:
    extractor = EXTRACTORS.get("office")
    for extension in (".xlsx", ".xlsm", ".xls"):
        context = ExtractionContext(
            entry_id="entry-2",
            path=Path(f"table{extension}"),
            relative_path=f"table{extension}",
            extensions=frozenset({extension}),
            size_bytes=0,
        )
        assert not extractor.supports(context)


def test_an_isolated_extraction_answers_across_the_process_boundary(tmp_path: Path) -> None:
    from ingence.executors.supervisor import Supervisor

    path = _docx(tmp_path / "boundary.docx", _paragraph("across"))
    supervised = Supervisor(timeout_seconds=60.0).run("office", _context(path, ".docx"))
    assert not supervised.contained
    assert supervised.result.outcome is ScanOutcome.EXTRACTED
    assert supervised.result.event_time_ns == _extract(path).event_time_ns
