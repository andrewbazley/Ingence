"""Office packages built by hand from their parts, for tests that index and dive them.

A workbook, a word-processing document and a presentation, each the smallest package the Open
Packaging Conventions allow plus the parts a test names: the core properties (``dcterms:created``
and ``dcterms:modified``), the extended properties, and a chart or a picture so a package holds
more than one part beneath a folder of its own. Built from the format's structure with invented
values rather than saved by a library, because a library writes the properties it defaults to and
these tests must choose every one. Every word here is invented.
"""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["core_part", "document", "presentation", "workbook"]

_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_SPREADSHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SLIDES = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_EXTENDED = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"


def core_part(*, created: str | None, modified: str | None, title: str = "kelt plan") -> str:
    """A core-properties part stating the given creation and modification stamps."""
    stamps = ""
    if created is not None:
        stamps += f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
    if modified is not None:
        stamps += f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
        'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{title}</dc:title><dc:creator>Vorn Brisa</dc:creator>{stamps}"
        "</cp:coreProperties>"
    )


def _app_part(version: str = "16.0300") -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Properties xmlns="{_EXTENDED}">'
        f"<Application>Zephwriter</Application><AppVersion>{version}</AppVersion></Properties>"
    )


def _relationships(*entries: tuple[str, str, str]) -> str:
    body = "".join(
        f'<Relationship Id="{identifier}" Type="{kind}" Target="{target}"/>'
        for identifier, kind, target in entries
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_PACKAGE}">'
        f"{body}</Relationships>"
    )


def _content_types(main: tuple[str, str], *overrides: tuple[str, str]) -> str:
    parts = (
        main,
        ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
        (
            "/docProps/app.xml",
            "application/vnd.openxmlformats-officedocument.extended-properties+xml",
        ),
        *overrides,
    )
    body = "".join(f'<Override PartName="{name}" ContentType="{kind}"/>' for name, kind in parts)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{_TYPES}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        f"{body}</Types>"
    )


def _package_relationships(main: str) -> str:
    return _relationships(
        ("rId1", f"{_OFFICE}/officeDocument", main),
        ("rId2", f"{_PACKAGE}/metadata/core-properties", "docProps/core.xml"),
        ("rId3", f"{_OFFICE}/extended-properties", "docProps/app.xml"),
    )


def _write(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return path


def workbook(path: Path, *, created: str | None, modified: str | None, charts: int = 2) -> Path:
    """A workbook of one sheet holding ``charts`` charts, each a part of its own."""
    chart_parts = {
        f"xl/charts/chart{number}.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><c:chartSpace xmlns:c="{_CHART}">'
            "<c:chart/></c:chartSpace>"
        )
        for number in range(1, charts + 1)
    }
    parts = {
        "[Content_Types].xml": _content_types(
            (
                "/xl/workbook.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
            ),
            (
                "/xl/worksheets/sheet1.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            ),
        ),
        "_rels/.rels": _package_relationships("xl/workbook.xml"),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{_SPREADSHEET}"'
            f' xmlns:r="{_OFFICE}"><sheets><sheet name="kelt" sheetId="1" r:id="rId1"/>'
            "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": _relationships(
            ("rId1", f"{_OFFICE}/worksheet", "worksheets/sheet1.xml")
        ),
        "xl/worksheets/sheet1.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{_SPREADSHEET}">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>vorn</t></is></c>'
            '<c r="B1"><v>7</v></c></row></sheetData></worksheet>'
        ),
        **chart_parts,
        "docProps/core.xml": core_part(created=created, modified=modified),
        "docProps/app.xml": _app_part(),
    }
    return _write(path, parts)


def document(path: Path, *, created: str | None, modified: str | None) -> Path:
    """A word-processing document of two paragraphs, with a settings part beside its body."""
    parts = {
        "[Content_Types].xml": _content_types(
            (
                "/word/document.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            ),
        ),
        "_rels/.rels": _package_relationships("word/document.xml"),
        "word/document.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_WORD}"><w:body>'
            "<w:p><w:r><w:t>The kelt plan, second pass.</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Brisa wells three to nine.</w:t></w:r></w:p>"
            "</w:body></w:document>"
        ),
        "word/settings.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><w:settings xmlns:w="{_WORD}"/>'
        ),
        "docProps/core.xml": core_part(created=created, modified=modified),
        "docProps/app.xml": _app_part(),
    }
    return _write(path, parts)


def presentation(path: Path, *, created: str | None, modified: str | None) -> Path:
    """A presentation of two slides, each a part of its own."""
    slides = {
        f"ppt/slides/slide{number}.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="{_SLIDES}"'
            f' xmlns:a="{_DRAWING}"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>'
            f"<a:t>Zeph slide {number}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree>"
            "</p:cSld></p:sld>"
        )
        for number in (1, 2)
    }
    parts = {
        "[Content_Types].xml": _content_types(
            (
                "/ppt/presentation.xml",
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation.main+xml",
            ),
        ),
        "_rels/.rels": _package_relationships("ppt/presentation.xml"),
        "ppt/presentation.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="{_SLIDES}"'
            f' xmlns:r="{_OFFICE}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
            '<p:sldId id="257" r:id="rId2"/></p:sldIdLst></p:presentation>'
        ),
        "ppt/_rels/presentation.xml.rels": _relationships(
            ("rId1", f"{_OFFICE}/slide", "slides/slide1.xml"),
            ("rId2", f"{_OFFICE}/slide", "slides/slide2.xml"),
        ),
        **slides,
        "docProps/core.xml": core_part(created=created, modified=modified),
        "docProps/app.xml": _app_part(),
    }
    return _write(path, parts)
