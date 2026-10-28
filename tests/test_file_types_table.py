"""The file-type table: the one source every kind of file is declared in.

The Dictionary's format senses are generated from it, type detection reads its signatures and
hints from it, and every content reader's extension set is held to it. The identity tests build
a Dictionary from the senses the section files declared before the table
(``fixtures/format_senses_before_the_table.yaml``) beside one from the table, and compare the
senses and the bindings of a few thousand names to the byte. Every name here is invented.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest
import yaml

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import DictionaryOrigin, Entry, EntryType, ScanOutcome
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import read_name
from ingence.dictionary.file_types import (
    CAN_BE_RAW,
    CATEGORIES,
    CORE_SECTION,
    FILE_TYPES_KEY,
    FROM_TABLE,
    WRITER_KINDS,
    FileTypeTableError,
    declared_compounds,
    file_type_table,
    identifiers,
    instrument_spellings,
    instruments,
    table_from,
    writers,
)
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import (
    BUILTIN_DIRECTORY,
    PACKS_DIRECTORY,
    DictionaryStore,
    Section,
    _section_from,
    load_shipped_sections,
)
from ingence.evaluation.sample_lab import _build
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS
from ingence.extractors.detect import EXTENSION_HINTS, SIGNATURES
from ingence.scan.census import extension_of

FIXTURE = Path(__file__).parent / "fixtures" / "format_senses_before_the_table.yaml"
RUN = "run_identity"

TABLE = file_type_table()


# ------------------------------------------------------------------------ the two Dictionaries


def _without_vendors(sections: tuple[Section, ...]) -> tuple[Section, ...]:
    """The sections with every origin's vendor string taken out: the fixture carries none,
    because nothing binds on one, and the comparison is made without them on both sides."""
    stripped = []
    for section in sections:
        senses = []
        for sense in section.senses:
            origins = sense.properties.get("origins")
            if isinstance(origins, dict):
                properties = dict(sense.properties)
                properties["origins"] = {
                    spelling: {key: value for key, value in origin.items() if key != "vendor"}
                    for spelling, origin in origins.items()
                }
                sense = replace(sense, properties=properties)
            senses.append(sense)
        stripped.append(replace(section, senses=tuple(senses)))
    return tuple(stripped)


def _sections_before_the_table() -> tuple[Section, ...]:
    """The shipped sections as the files declared them before the table: each file read with
    its ``file_types`` marker taken out and the frozen format entries put back."""
    frozen = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    sections = []
    for directory in (PACKS_DIRECTORY, BUILTIN_DIRECTORY):
        for path in sorted(directory.rglob("*.yaml")):
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if "section" not in document and "entries" not in document:
                continue  # E3: a list beside the sections (the spelling deny-list)
            document.pop(FILE_TYPES_KEY, None)
            document["entries"] = [
                *(document.get("entries") or ()),
                *frozen.get(document["section"], ()),
            ]
            sections.append(
                _section_from(document, origin=DictionaryOrigin.BUILTIN, scope=PROJECT_SCOPE)
            )
    return tuple(sections)


@pytest.fixture(scope="module")
def before() -> DictionaryStore:
    return DictionaryStore(_without_vendors(_sections_before_the_table()))


@pytest.fixture(scope="module")
def after() -> DictionaryStore:
    return DictionaryStore(_without_vendors(load_shipped_sections()))


def test_the_generated_senses_are_the_ones_the_section_files_declared(before, after) -> None:
    """Term for term, spelling for spelling, property for property: the whole vocabulary's
    fingerprint is the same, and so is each format sense."""
    assert after.fingerprint() == before.fingerprint()
    old = {sense.term: sense for sense in before.senses() if sense.theme_kind == "format"}
    new = {sense.term: sense for sense in after.senses() if sense.theme_kind == "format"}
    assert set(new) == set(old)
    for term, sense in old.items():
        assert new[term] == sense, term
    assert len(old) == 106
    assert declared_compounds(after) == declared_compounds(before)
    assert instrument_spellings(after) == instrument_spellings(before)


def test_the_frozen_fixture_holds_every_family_and_nothing_else() -> None:
    frozen = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    terms = {entry["term"] for entries in frozen.values() for entry in entries}
    assert terms == set(TABLE.families)
    assert set(frozen) == set(TABLE.sections())


# --------------------------------------------------------------------------- binding identity


def _entry(path: str, declared: frozenset[str]) -> Entry:
    name = PurePosixPath(path).name
    extension, compound = extension_of(name, declared)
    return Entry(
        entry_id=f"entry_{path}",
        root_id="root_identity",
        relative_path=path,
        name=name,
        entry_type=EntryType.FILE,
        extension=extension,
        compound_extension=compound,
        size_bytes=0,
        mode=33188,
        nlink=1,
        modified_ns=0,
        changed_ns=0,
        resolution_status=ResolutionStatus.UNEXAMINED,
        first_seen_run=RUN,
        last_seen_run=RUN,
        scan_outcome=ScanOutcome.METADATA_ONLY,
    )


def _invented_names() -> list[str]:
    """A few hundred names covering every extension the table and the old senses know: each
    spelling after a dot, in capitals, as a word in the stem beside itself, and compounded."""
    names: list[str] = []
    spellings = sorted(TABLE.extensions())
    for index, extension in enumerate(spellings):
        names.append(f"plate_a{index % 12 + 1:02d}.{extension}")
        names.append(f"Scan_{index:03d}.{extension.upper()}")
        names.append(f"{extension}_notes_copy.{extension}")
    names.extend(
        [
            "traj_001.nd2.csv",
            "volume_07.nii.gz",
            "table_day3.csv.gz",
            "bundle_2024.tar.gz",
            "cells_01.ome.tif",
            "frame_12.png.bak",
            "no_extension_here",
            "ENCFF001ABC_signal.bigwig",
            "SRR12345678_1.fastq",
        ]
    )
    return names


def _readings(store: DictionaryStore, names: list[str]) -> list[list[dict[str, object]]]:
    declared = declared_compounds(store)
    normaliser = Normaliser(store)
    rows: list[list[dict[str, object]]] = []
    for path in names:
        reading = read_name(_entry(path, declared), run_id=RUN, normaliser=normaliser, store=store)
        rows.append(
            [
                *(component.model_dump() for component in reading.components),
                *([reading.shape.model_dump()] if reading.shape is not None else []),
                *(binding.model_dump() for binding in reading.bindings),
            ]
        )
    return rows


def test_every_name_binds_to_the_byte_as_it_did_before_the_table() -> None:
    """The sample collection's names and a few hundred invented ones, bound with the senses the
    files declared and with the generated ones: the same component, shape and binding rows."""
    drive = _build("S", 1)
    names = sorted(drive.files) + sorted(drive.folders) + _invented_names()
    before = DictionaryStore(_sections_before_the_table())
    after = DictionaryStore.builtin()
    old = _readings(before, names)
    new = _readings(after, names)
    assert len(names) >= 1000
    rows = sum(len(rows) for rows in old)
    assert rows >= 5000
    for path, old_rows, new_rows in zip(names, old, new, strict=True):
        assert new_rows == old_rows, path


# ---------------------------------------------------------------------------------- the table


def test_the_table_reads_every_row_in_bounds() -> None:
    assert len(TABLE.rows) >= 200
    assert len(TABLE.families) == 106
    assert len(TABLE.digest) == 32
    for row in TABLE.rows:
        assert row.category in CATEGORIES, row.name
        assert row.can_be_raw in CAN_BE_RAW, row.name
        assert row.family is None or row.family in TABLE.families
    assert TABLE.sections()[0] == CORE_SECTION
    assert TABLE.row_for(".TIF") is TABLE.row_for("tif")
    assert TABLE.row_for("nothing_of_the_kind") is None


@pytest.mark.parametrize(
    ("extension", "category", "can_be_raw"),
    [
        ("tif", "general", "evidence"),
        ("ome.tif", "general", "evidence"),
        ("nd2", "instrument", "yes"),
        ("czi", "instrument", "yes"),
        ("lif", "instrument", "yes"),
        ("oib", "instrument", "yes"),
        ("oif", "instrument", "yes"),
        ("lsm", "instrument", "yes"),
        ("vsi", "instrument", "yes"),
        ("abf", "instrument", "yes"),
        ("fastq", "instrument", "yes"),
        ("fasta", "general", "no"),
        ("csv", "general", "evidence"),
        ("py", "program", "no"),
        ("zip", "archive", "no"),
        ("h5", "container", "evidence"),
        ("docx", "document", "no"),
    ],
)
def test_what_the_table_says_a_kind_of_file_is(extension, category, can_be_raw) -> None:
    assert TABLE.category_of(extension) == category
    assert TABLE.can_be_raw(extension) == can_be_raw


def test_every_section_the_table_names_takes_its_formats_from_the_table() -> None:
    """A family the table declares reaches the shipped Dictionary only through a section file
    saying ``file_types: from_table``; every one does."""
    shipped = {sense.term for section in load_shipped_sections() for sense in section.senses}
    assert set(TABLE.families) <= shipped
    for directory in (PACKS_DIRECTORY, BUILTIN_DIRECTORY):
        for path in sorted(directory.rglob("*.yaml")):
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if "section" not in document and "entries" not in document:
                continue  # E3: a list beside the sections (the spelling deny-list)
            default_kind = document.get("theme_kind", "term")
            declares_a_format = any(
                raw.get("theme_kind", default_kind) == "format"
                for raw in document.get("entries") or ()
            )
            assert not declares_a_format, f"{path.name} declares a format outside the table"
            if document["section"] in TABLE.sections():
                assert document.get(FILE_TYPES_KEY) == FROM_TABLE, path.name


def test_a_section_the_table_has_no_rows_for_is_refused() -> None:
    with pytest.raises(ValueError, match="no row naming this section"):
        _section_from(
            {"section": "vorn_words", FILE_TYPES_KEY: FROM_TABLE},
            origin=DictionaryOrigin.BUILTIN,
            scope=PROJECT_SCOPE,
        )
    with pytest.raises(ValueError, match="only a shipped section"):
        _section_from(
            {"section": CORE_SECTION, FILE_TYPES_KEY: FROM_TABLE},
            origin=DictionaryOrigin.PACK,
            scope=PROJECT_SCOPE,
        )


def _document(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "families": {"zorb_format": {"canonical": "zorb data"}},
        "formats": [
            {
                "name": "zorb data",
                "family": "zorb_format",
                "extensions": ["zorb"],
                "category": "instrument",
                "can_be_raw": "yes",
            }
        ],
    }
    document.update(changes)
    return document


def test_a_table_that_contradicts_itself_is_refused_in_words() -> None:
    with pytest.raises(FileTypeTableError, match="also row"):
        table_from(
            _document(
                formats=[
                    *_document()["formats"],  # type: ignore[misc]
                    {
                        "name": "zorb data again",
                        "extensions": ["zorb"],
                        "category": "general",
                        "can_be_raw": "no",
                    },
                ]
            )
        )
    with pytest.raises(FileTypeTableError, match="no row belongs"):
        table_from(_document(families={"zorb_format": {}, "kelt_format": {}}))
    with pytest.raises(FileTypeTableError, match="built_on"):
        table_from(_document(formats=[{**_document()["formats"][0], "built_on": "kelt"}]))  # type: ignore[index]
    with pytest.raises(FileTypeTableError, match="category"):
        table_from(_document(formats=[{**_document()["formats"][0], "category": "thing"}]))  # type: ignore[index]
    with pytest.raises(FileTypeTableError, match="writer"):
        table_from(_document(formats=[{**_document()["formats"][0], "writers": ["Vorn"]}]))  # type: ignore[index]


def test_the_senses_a_small_table_generates_are_shaped_like_a_section_entry() -> None:
    table = table_from(
        _document(
            families={"zorb_format": {"canonical": "zorb data", "program": True}},
            formats=[
                {
                    "name": "zorb data",
                    "family": "zorb_format",
                    "extensions": ["zorb", "zrb"],
                    "category": "program",
                    "can_be_raw": "no",
                    "also_a_word": ["zrb"],
                    "written_by": "software",
                    "vendor": "Vorn",
                },
                {
                    "name": "zorb data in a pack",
                    "family": "zorb_format",
                    "extensions": ["zorbx"],
                    "category": "program",
                    "can_be_raw": "no",
                    "domains": ["kelt"],
                },
            ],
        )
    )
    assert table.format_entries(CORE_SECTION) == [
        {
            "term": "zorb_format",
            "canonical": "zorb data",
            "theme_kind": "format",
            "surfaces": ["zorb", "zrb"],
            "extension_only": ["zrb"],
            "program": True,
            "origins": {
                "zorb": {"vendor": "Vorn", "written_by": "software"},
                "zrb": {"vendor": "Vorn", "written_by": "software"},
            },
        }
    ]
    assert table.format_entries("kelt") == [
        {"term": "zorb_format", "extends": True, "theme_kind": "format", "surfaces": ["zorbx"]}
    ]


# ------------------------------------------------------------------------------ type detection


def test_detection_reads_its_signatures_and_hints_from_the_table() -> None:
    specs = TABLE.signatures()
    assert [(s.label, s.magic, s.offset, s.media_type, s.extensions) for s in SIGNATURES] == [
        (s.label, s.magic, s.offset, s.media_type, s.extensions) for s in specs
    ]
    assert TABLE.extension_hints() == EXTENSION_HINTS
    assert len(SIGNATURES) >= 30
    for signature in SIGNATURES:
        assert signature.extensions, signature.label
        assert all(extension.startswith(".") for extension in signature.extensions)
    by_label = {signature.label: signature for signature in SIGNATURES}
    assert set(by_label["zip"].extensions) >= {".zip", ".docx", ".xlsx", ".pptx", ".jar", ".whl"}
    assert set(by_label["ole_compound"].extensions) >= {".doc", ".xls", ".ppt", ".oib", ".zvi"}
    assert set(by_label["tiff_little_endian"].extensions) >= {".tif", ".ome.tif", ".lsm", ".stk"}
    assert set(by_label["hdf5"].extensions) >= {".h5", ".hdf5", ".mat", ".ims", ".nwb"}
    assert EXTENSION_HINTS[".nd2"] == "application/x-nikon-nd2"
    assert EXTENSION_HINTS[".csv"] == "text/csv"
    assert ".bin" not in EXTENSION_HINTS
    assert ".env" not in EXTENSION_HINTS


def test_every_signature_belongs_to_a_row_or_a_container() -> None:
    labels = {signature.label for row in TABLE.rows for signature in row.signatures} | {
        signature.label for container in TABLE.containers for signature in container.signatures
    }
    assert {signature.label for signature in SIGNATURES} == labels


# ---------------------------------------------------------------------------- the readers' sets


def _reader_sets() -> dict[str, frozenset[str]]:
    from ingence.extractors.archive import STREAM_EXTENSIONS, TAR_EXTENSIONS, ZIP_EXTENSIONS
    from ingence.extractors.compound import COMPOUND_EXTENSIONS
    from ingence.extractors.delimited import TAB_EXTENSIONS
    from ingence.extractors.document import DOCUMENT_EXTENSIONS
    from ingence.extractors.image_header import HEADER_EXTENSIONS
    from ingence.extractors.keywords import _CODE_EXTENSIONS
    from ingence.extractors.lambert_fli import FLI_EXTENSIONS
    from ingence.extractors.leica_lif import LIF_EXTENSIONS
    from ingence.extractors.nikon_nd2 import ND2_EXTENSIONS
    from ingence.extractors.notebook import NOTEBOOK_EXTENSIONS
    from ingence.extractors.office import OFFICE_EXTENSIONS
    from ingence.extractors.script_calls import SCRIPT_LANGUAGES
    from ingence.extractors.spreadsheet import SPREADSHEET_EXTENSIONS
    from ingence.extractors.tabular import DELIMITED_EXTENSIONS
    from ingence.extractors.text import TEXT_EXTENSIONS
    from ingence.extractors.tiff import TIFF_EXTENSIONS

    return {
        "archive.zip": frozenset(ZIP_EXTENSIONS),
        "archive.tar": frozenset(TAR_EXTENSIONS),
        "archive.stream": frozenset(STREAM_EXTENSIONS),
        "compound": frozenset(COMPOUND_EXTENSIONS),
        "delimited": frozenset(TAB_EXTENSIONS),
        "document": frozenset(DOCUMENT_EXTENSIONS),
        "image_header": frozenset(HEADER_EXTENSIONS),
        "keywords": frozenset(_CODE_EXTENSIONS),
        "lambert_fli": frozenset(FLI_EXTENSIONS),
        "leica_lif": frozenset(LIF_EXTENSIONS),
        "nikon_nd2": frozenset(ND2_EXTENSIONS),
        "notebook": frozenset(NOTEBOOK_EXTENSIONS),
        "office": frozenset(OFFICE_EXTENSIONS),
        "script_calls": frozenset(SCRIPT_LANGUAGES),
        "spreadsheet": frozenset(SPREADSHEET_EXTENSIONS),
        "tabular": frozenset(DELIMITED_EXTENSIONS),
        "text": frozenset(TEXT_EXTENSIONS),
        "tiff": frozenset(TIFF_EXTENSIONS),
    }


def test_every_content_reader_claims_only_extensions_the_table_knows() -> None:
    """A reader's own set, and what it registers as the extensions it claims, are rows of the
    table: a spelling a reader opens that the table does not know is a drift."""
    load_builtins()
    known = {f".{extension}" for extension in TABLE.extensions()}
    for reader, extensions in _reader_sets().items():
        assert extensions <= known, (reader, sorted(extensions - known))
    for name in EXTRACTORS.names():
        claimed = EXTRACTORS.declaration(name).attributes.get("extensions", "")
        extensions = {extension for extension in claimed.split(",") if extension}
        assert extensions <= known, (name, sorted(extensions - known))


# --------------------------------------------------------------------------------- the lists


def test_the_writers_are_marked_and_spelled() -> None:
    listed = writers()
    assert {writer.kind for writer in listed} == WRITER_KINDS
    assert {writer.name for writer in listed} >= {"NIS-Elements", "ZEN", "ImageJ", "Fiji"}
    assert TABLE.writer_named("ImageJ 1.54f").name == "ImageJ"
    assert TABLE.writer_named("NIS-Elements AR 5.21").kind == "acquisition"
    assert TABLE.writer_named("nothing anybody wrote") is None
    # A spelling is matched as a word of its own: ZEN is not named by frozen.
    assert TABLE.writer_named("frozen lens 2") is None
    assert TABLE.writer_named("zen") is not None
    named = TABLE.writers_named("ImageJ 1.54f saved after Micro-Manager 2.0")
    assert {writer.name for writer in named} == {"ImageJ", "Micro-Manager"}
    names = {writer.name for writer in listed}
    assert all(writer in names for row in TABLE.rows for writer in row.writers)


def test_the_instruments_are_spelled_lower_case_and_say_what_they_are() -> None:
    listed = instruments()
    assert {instrument.name for instrument in listed} >= {"Cytation", "EVOS", "Nikon", "Zeiss"}
    for instrument in listed:
        assert instrument.kind and instrument.spellings
        assert all(spelling == spelling.casefold() for spelling in instrument.spellings)


def test_the_identifiers_are_patterns_that_match_whole_accessions() -> None:
    patterns = {identifier.name: re.compile(identifier.pattern) for identifier in identifiers()}
    assert len(patterns) == 7
    examples = [
        "ENCFF001ABC",
        "GSE12345",
        "GSM1",
        "GPL570",
        "SRR12345678",
        "ERX1",
        "DRP2",
        "PRJNA12345",
        "PRJEB1",
        "EMPIAR-10001",
        "S-BIAD123",
    ]
    for example in examples:
        assert any(pattern.fullmatch(example) for pattern in patterns.values()), example
