"""The byte writers of the sample collection: documents, tables and instrument headers.

Each is built from its format's structure with invented values. What is held here is that a
reader of the format opens what was written, and that the same inputs write the same bytes.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import struct
import zipfile

import pytest

from ingence.evaluation import documents as writers

MOMENT = dt.datetime(2021, 3, 4, 10, 15, tzinfo=dt.UTC)


def test_a_pdf_opens_in_a_reader_and_carries_its_text_and_its_date() -> None:
    pypdf = pytest.importorskip("pypdf")
    data = writers.pdf_bytes(["first (line)", "second"], title="a title", moment=MOMENT)

    reader = pypdf.PdfReader(io.BytesIO(data))

    assert len(reader.pages) == 1
    assert "first (line)" in reader.pages[0].extract_text()
    assert reader.metadata["/CreationDate"] == "D:20210304101500Z"
    assert data == writers.pdf_bytes(["first (line)", "second"], title="a title", moment=MOMENT)


def test_a_workbook_opens_and_a_package_is_the_same_bytes_every_time() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    rows = [["well", "signal"], ["A01", 2.5], ["A02", 7]]
    data = writers.xlsx_bytes(rows, sheet="plate", creator="Zavok", moment=MOMENT)

    sheet = openpyxl.load_workbook(io.BytesIO(data)).active

    assert (sheet["A2"].value, sheet["B2"].value, sheet["B3"].value) == ("A01", 2.5, 7)
    assert data == writers.xlsx_bytes(rows, sheet="plate", creator="Zavok", moment=MOMENT)


@pytest.mark.parametrize(
    "build",
    [
        lambda: writers.docx_bytes(["one", "two"], title="t", creator="Zavok", moment=MOMENT),
        lambda: writers.pptx_bytes(["one", "two"], title="t", creator="Zavok", moment=MOMENT),
        lambda: writers.xlsx_bytes([["a"]], sheet="s", creator="Zavok", moment=MOMENT),
    ],
)
def test_an_office_package_stores_its_parts_dated_by_the_moment_given_and_made_on_unix(
    build: object,
) -> None:
    data = build()  # type: ignore[operator]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        for info in archive.infolist():
            assert info.date_time[:5] == (2021, 3, 4, 10, 15)
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.create_system == 3
        core = archive.read("docProps/core.xml").decode()
    assert "2021-03-04T10:15:00Z" in core
    assert "Zavok" in core


def test_a_notebook_is_never_executed() -> None:
    nbformat = pytest.importorskip("nbformat")
    data = writers.notebook_bytes([("markdown", "# a title\n"), ("code", "x = 1\nprint(x)\n")])

    notebook = nbformat.reads(data.decode(), as_version=4)

    assert [cell.cell_type for cell in notebook.cells] == ["markdown", "code"]
    assert notebook.cells[1].outputs == []
    assert notebook.cells[1].execution_count is None


def test_a_tiff_stack_holds_every_page_and_its_description() -> None:
    pil = pytest.importorskip("PIL.Image")
    pages = [bytes([index]) * 8 for index in range(3)]
    data = writers.tiff_stack_bytes(pages, description="ImageJ=1.53\nimages=3\n")

    image = pil.open(io.BytesIO(data))

    assert image.n_frames == 3
    assert "images=3" in image.tag_v2[270]
    with pytest.raises(ValueError, match="at least one page"):
        writers.tiff_stack_bytes([])


def test_a_png_decodes_to_its_pixels() -> None:
    pil = pytest.importorskip("PIL.Image")
    image = pil.open(io.BytesIO(writers.png_bytes(bytes(range(6)))))
    image.load()

    assert image.size == (6, 1)
    assert list(image.tobytes()) == list(range(6))


def test_a_header_shaped_like_a_czi_is_not_a_readable_czi() -> None:
    data = writers.czi_header_bytes(b"token")

    assert data[:10] == b"ZISRAWFILE"
    allocated, used = struct.unpack_from("<qq", data, 16)
    assert used == 80
    assert len(data) == 32 + allocated
    # The directory and metadata positions lie past the end of the file: nothing is there to read.
    directory, metadata = struct.unpack_from("<qq", data, 32 + 52)
    assert directory > len(data)
    assert metadata > len(data)
    assert data != writers.czi_header_bytes(b"other")


def test_a_compound_file_with_an_empty_root_opens() -> None:
    olefile = pytest.importorskip("olefile")
    data = writers.thumbs_db_bytes(b"abcdefgh")

    opened = olefile.OleFileIO(io.BytesIO(data))

    assert len(data) == 1536
    assert opened.listdir() == []
    assert data != writers.thumbs_db_bytes(b"12345678")


def test_a_matlab_data_file_holds_its_header_and_no_variable() -> None:
    data = writers.mat_file_bytes(MOMENT)

    assert len(data) == 128
    assert data.startswith(b"MATLAB 5.0 MAT-file")
    assert b"Thu Mar  4 10:15:00 2021" in data
    assert data[124:] == b"\x00\x01IM"


def test_the_files_an_editor_and_a_finder_leave_are_small_and_different_each_time() -> None:
    assert len(writers.office_lock_bytes("Zavok", b"a")) == 162
    assert writers.office_lock_bytes("Zavok", b"a")[1:6] == b"Zavok"
    assert writers.office_lock_bytes("Zavok", b"a") != writers.office_lock_bytes("Zavok", b"b")
    assert writers.ds_store_bytes(b"a")[:8] == b"\x00\x00\x00\x01Bud1"
    assert writers.ds_store_bytes(b"a") != writers.ds_store_bytes(b"b")


def test_a_notebook_is_json_with_sorted_keys_so_its_bytes_are_stable() -> None:
    data = writers.notebook_bytes([("code", "x = 1\n")])

    assert json.loads(data)["nbformat"] == 4
    assert data == writers.notebook_bytes([("code", "x = 1\n")])
