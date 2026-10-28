"""Every shipped domain section loads in every project.

The core and a person's answers load either way; a domain a person vetoed (a refusal on
``pack:<domain>`` in the answer log) is left out. The settings ``dictionary.domain_packs`` and
``domain_packs_named`` stay declared and are read by nothing. Every name here is invented.
"""

from __future__ import annotations

import pytest
import yaml

from ingence.core.project import Project
from ingence.dictionary.store import DictionaryStore


def _project(tmp_path, *, veto: object = (), keep: object = None, **dictionary: object) -> Project:
    """A project; ``veto`` names domains refused, ``keep`` the only domains not refused."""
    from ingence.core.schema import RecordEventKind
    from ingence.dictionary.store import PACKS_DIRECTORY, load_sections
    from ingence.record.log import append_event
    from ingence.record.targets import pack_target

    pool = tmp_path / "pool"
    pool.mkdir(parents=True)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    if dictionary:
        settings = yaml.safe_load((project.path / "settings.yaml").read_text())
        settings.setdefault("dictionary", {}).update(dictionary)
        (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    refused = set(veto)  # type: ignore[call-overload]
    if keep is not None:
        shipped = {s.domain for s in load_sections(PACKS_DIRECTORY) if s.domain}
        refused |= shipped - set(keep)  # type: ignore[call-overload]
    for domain in sorted(refused):
        append_event(
            project.path,
            kind=RecordEventKind.REJECT,
            actor="kelt",
            occurred_at="2026-01-01T00:00:00Z",
            target=pack_target(domain),
            against_run_id="",
            payload={"verdict": "refuse"},
        )
    return Project.open(project.path)


def _domains(store: DictionaryStore) -> set[str]:
    return {section.domain for section in store.sections if section.domain}


def test_by_default_every_shipped_domain_pack_loads(tmp_path) -> None:
    project = _project(tmp_path)
    assert _domains(DictionaryStore.for_project(project.path)) == _domains(
        DictionaryStore.builtin()
    )


def test_a_vetoed_domain_s_words_are_left_out_and_every_other_loads(tmp_path) -> None:
    project = _project(tmp_path, veto=["biochemistry"])
    store = DictionaryStore.for_project(project.path)
    assert _domains(store) == _domains(DictionaryStore.builtin()) - {"biochemistry"}
    assert store.look_up("raw") is not None, "the core loads either way"
    assert store.look_up("western") is None, "a vetoed domain's vocabulary does not"


def test_naming_domains_in_the_settings_loads_nothing_less(tmp_path) -> None:
    """The settings are declared and read by nothing: an older project's list changes nothing."""
    project = _project(tmp_path, domain_packs="named", domain_packs_named=["microscopy"])
    store = DictionaryStore.for_project(project.path)
    assert _domains(store) == _domains(DictionaryStore.builtin())
    assert store.look_up("western") is not None


def test_vetoing_every_domain_leaves_the_core(tmp_path) -> None:
    project = _project(tmp_path, keep=[])
    store = DictionaryStore.for_project(project.path)
    assert _domains(store) == set()
    assert len(store) < len(DictionaryStore.builtin())


# ---------------------------------------------------------------------------
# A pack adds spellings to a type the core declares.
# ---------------------------------------------------------------------------

#: A type the core declares with the spellings anything writes, and declares origins for none.
_CORE_HALF = """
section: kinds
theme_kind: technique
entries:
  - {term: zib_format, theme_kind: format, surfaces: [zib, zibb], extension_only: [zibb],
     origins: {}}
  - {term: vorn_word, surfaces: [vorn]}
"""

#: A pack's entry adding its field's own spellings, and who writes them, to the same type.
_PACK_HALF = """
section: keltish
domain: keltish
theme_kind: technique
entries:
  - {term: zib_format, extends: true, theme_kind: format, surfaces: [kelt, zib, keltx],
     extension_only: [kelt], origins: {kelt: {vendor: Kelt, written_by: instrument}}}
  - {term: kelt_word, surfaces: [keltword]}
"""

#: The same type written whole, in the core, and the pack without it: what the two halves
#: loaded together must be, to the character.
_CORE_WHOLE = """
section: kinds
theme_kind: technique
entries:
  - {term: zib_format, theme_kind: format, surfaces: [zib, zibb, kelt, keltx],
     extension_only: [zibb, kelt],
     origins: {kelt: {vendor: Kelt, written_by: instrument}}}
  - {term: vorn_word, surfaces: [vorn]}
"""
_PACK_WITHOUT = """
section: keltish
domain: keltish
theme_kind: technique
entries:
  - {term: kelt_word, surfaces: [keltword]}
"""


def _sections(tmp_path, name: str, text: str):
    from ingence.dictionary.store import load_sections

    directory = tmp_path / name
    directory.mkdir()
    (directory / f"{name}.yaml").write_text(text)
    return load_sections(directory)


def test_a_pack_s_spellings_of_a_core_type_fold_into_it_wherever_the_pack_loads(tmp_path) -> None:
    """Loaded together, the two halves are the type written whole: one sense, one identifier,
    one digest and one fingerprint, whichever loads first (the packs load ahead of the core)."""
    core = _sections(tmp_path, "core", _CORE_HALF)
    pack = _sections(tmp_path, "pack", _PACK_HALF)
    whole = DictionaryStore(
        (*_sections(tmp_path, "whole", _CORE_WHOLE), *_sections(tmp_path, "without", _PACK_WITHOUT))
    )
    for order in ((*pack, *core), (*core, *pack)):
        store = DictionaryStore(order)
        (sense,) = [sense for sense in store.senses() if sense.term == "zib_format"]
        assert sense.surfaces == ("zib", "zibb", "kelt", "keltx"), "the core's spellings first"
        assert sense.section == "kinds" and sense.domain is None, "filed where the type is"
        assert sense.properties == whole.sense_for_term("zib_format").properties
        assert "extends" not in sense.properties
        assert sense.digest == whole.sense_for_term("zib_format").digest
        assert store.records() == whole.records()
        assert store.fingerprint() == whole.fingerprint()
        assert store.look_up("kelt", permitted_kinds=frozenset({"format"})).sense is sense
        assert {section.domain for section in store.sections} == {None, "keltish"}


def test_without_the_pack_the_core_s_half_of_the_type_is_the_type(tmp_path) -> None:
    store = DictionaryStore(_sections(tmp_path, "core", _CORE_HALF))
    sense = store.sense_for_term("zib_format")
    assert sense is not None and sense.surfaces == ("zib", "zibb")
    assert sense.properties["origins"] == {}, "declared, and naming no writer"
    assert store.look_up("kelt", permitted_kinds=frozenset({"format"})) is None


def test_the_core_s_half_declares_origins_and_names_no_writer(tmp_path) -> None:
    """What a rule reading origins is handed: the type is there with nothing in it, which is
    not the same as a sense that declares none."""
    from ingence.infer.run import format_origins

    # The file-type table's formats are laid over the senses', so read this type's entry.
    core = format_origins(DictionaryStore(_sections(tmp_path, "core", _CORE_HALF)))
    assert core["zib_format"] == {}
    together = (*_sections(tmp_path, "pack", _PACK_HALF), *_sections(tmp_path, "c2", _CORE_HALF))
    assert format_origins(DictionaryStore(together))["zib_format"] == {"kelt": "instrument"}


def test_an_extension_whose_type_is_not_loaded_stands_as_that_type(tmp_path) -> None:
    store = DictionaryStore(_sections(tmp_path, "pack", _PACK_HALF))
    sense = store.sense_for_term("zib_format")
    assert sense is not None and sense.surfaces == ("kelt", "zib", "keltx")
    assert sense.section == "keltish" and "extends" not in sense.properties


@pytest.mark.parametrize(
    ("entry", "refusal"),
    [
        ("{term: zib_format, extends: true, surfaces: [kelt]}", "is a technique"),
        (
            "{term: zib_format, extends: true, theme_kind: format, surfaces: [kelt], "
            "canonical: Kelt image}",
            "renames it",
        ),
        (
            "{term: zib_format, extends: true, theme_kind: format, surfaces: [zibb], "
            "extension_only: true}",
            "declares extension_only",
        ),
    ],
)
def test_an_extension_that_would_redecide_its_type_is_refused(tmp_path, entry, refusal) -> None:
    """An extension adds spellings; a different kind, a new name or a contradicting property
    is a mistake in one of the two files, and folding it would let load order decide."""
    pack = _sections(
        tmp_path,
        "pack",
        f"section: keltish\ndomain: keltish\ntheme_kind: technique\nentries:\n  - {entry}\n",
    )
    with pytest.raises(ValueError, match=refusal):
        DictionaryStore((*pack, *_sections(tmp_path, "core", _CORE_HALF)))


def test_two_halves_disagreeing_about_who_writes_a_spelling_are_refused(tmp_path) -> None:
    core = _sections(
        tmp_path,
        "core",
        "section: kinds\nentries:\n"
        "  - {term: zib_format, theme_kind: format, surfaces: [zib, kelt],\n"
        "     origins: {kelt: {vendor: Kelt, written_by: software}}}\n",
    )
    with pytest.raises(ValueError, match="declares origins for kelt differently"):
        DictionaryStore((*_sections(tmp_path, "pack", _PACK_HALF), *core))


# ---------------------------------------------------------------------------
# What the core reads with no domain pack named.
# ---------------------------------------------------------------------------

#: A spelling of each kind of file the core must read -- images, tables, text, PDFs and Office
#: documents -- with video and JSON beside them, and the core type it must name.
_CORE_KINDS = {
    "tif": "image_format",
    "tiff": "image_format",
    "ome.tif": "image_format",
    "png": "image_format",
    "jpg": "image_format",
    "jpeg": "image_format",
    "gif": "image_format",
    "bmp": "image_format",
    "svg": "image_format",
    "csv": "tabular_format",
    "tsv": "tabular_format",
    "xlsx": "tabular_format",
    "xls": "tabular_format",
    "ods": "tabular_format",
    "txt": "plain_text",
    "md": "markdown",
    "json": "structured_format",
    "xml": "xml_document",
    "yaml": "yaml_document",
    "pdf": "portable_document",
    "docx": "rich_document",
    "doc": "rich_document",
    "rtf": "rich_document",
    "pptx": "slide_deck_format",
    "ppt": "slide_deck_format",
    "mp4": "video_format",
    "avi": "video_format",
}

#: Spellings an instrument writes as it acquires, which arrive with the microscopy pack.
_INSTRUMENT_SPELLINGS = ("nd2", "czi", "lif", "lsm", "oib", "fli", "lifext", "cr2")


def test_the_core_alone_knows_images_tables_text_pdfs_and_office_documents(tmp_path) -> None:
    from ingence.dictionary.file_types import file_type_scope

    store = DictionaryStore.for_project(_project(tmp_path, keep=[]).path)
    named = {spelling: file_type_scope(store, spelling) for spelling in _CORE_KINDS}
    assert {spelling: scope.value if scope else None for spelling, scope in named.items()} == (
        _CORE_KINDS
    )
    assert all(store.sense_for_term(term).domain is None for term in set(_CORE_KINDS.values()))


def test_an_instrument_s_spelling_of_an_image_arrives_with_the_microscopy_pack(tmp_path) -> None:
    """One image type whichever domains load; its instrument spellings, and who writes
    them, only where microscopy is not vetoed."""
    from ingence.dictionary.file_types import file_type_scope
    from ingence.infer.run import format_origins

    alone = DictionaryStore.for_project(_project(tmp_path / "a", keep=[]).path)
    with_microscopy = DictionaryStore.for_project(
        _project(tmp_path / "b", keep=["microscopy"]).path
    )
    for spelling in _INSTRUMENT_SPELLINGS:
        assert file_type_scope(alone, spelling) is None, spelling
        assert file_type_scope(with_microscopy, spelling) == file_type_scope(alone, "tif")
    from ingence.dictionary.recorded import format_origins as senses_origins

    assert senses_origins(alone) == {"image_format": {}}, "declared, and naming no writer"
    # The file-type table says who writes an instrument's format whichever domains load.
    assert format_origins(alone)["image_format"]["nd2"] == "instrument"
    assert format_origins(with_microscopy)["image_format"]["nd2"] == "instrument"
    everything = DictionaryStore.builtin().sense_for_term("image_format")
    assert with_microscopy.sense_for_term("image_format") == everything


def test_every_spelling_with_a_writer_arrives_with_a_domain_pack_and_joins_a_core_type() -> None:
    """The rule the move follows, over what ships: the core's formats name only spellings
    anything writes, and a shipped pack's `extends` entry always lands on a core type, so it
    never stands as a second type of the same name."""
    from ingence.dictionary.store import (
        BUILTIN_DIRECTORY,
        EXTENDS,
        ORIGINS,
        PACKS_DIRECTORY,
        load_sections,
    )

    core = {
        sense.term: sense
        for section in load_sections(BUILTIN_DIRECTORY)
        for sense in section.senses
    }
    for sense in core.values():
        assert not sense.properties.get(ORIGINS), f"{sense.term} names a writer in the core"
    extensions = [
        sense
        for section in load_sections(PACKS_DIRECTORY)
        for sense in section.senses
        if sense.properties.get(EXTENDS) is True
    ]
    assert extensions, "the microscopy pack's image spellings"
    shipped = {
        sense.term: sense
        for section in load_sections(PACKS_DIRECTORY)
        for sense in section.senses
        if sense.properties.get(EXTENDS) is not True
    }
    for sense in extensions:
        assert sense.term in core or sense.term in shipped, sense.term
        if sense.term in core:
            assert not set(sense.surfaces) & set(core[sense.term].surfaces), sense.term


#: The core types of the kinds of file that must be core: images, tables, text, PDFs and Office
#: documents.
_NAMED_KINDS = (
    "image_format",
    "tabular_format",
    "plain_text",
    "structured_format",
    "portable_document",
    "rich_document",
    "slide_deck_format",
)


def test_every_reader_of_the_listed_kinds_comes_with_the_base_install() -> None:
    """An extractor whose declared spellings include one of these kinds must need nothing an
    optional extra installs, or a base install reads a PDF, a workbook or a legacy Office
    document as bytes and strings only."""
    import re
    import tomllib
    from pathlib import Path

    from ingence.dictionary.store import BUILTIN_DIRECTORY, load_sections
    from ingence.extractors import load_builtins
    from ingence.extractors.base import EXTRACTORS

    manifest = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    base = {
        re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].casefold().replace("-", "_")
        for requirement in manifest["project"]["dependencies"]
    }
    spellings = {
        f".{surface.casefold()}"
        for section in load_sections(BUILTIN_DIRECTORY)
        for sense in section.senses
        if sense.term in _NAMED_KINDS
        for surface in sense.surfaces
    }
    load_builtins()
    readers = {}
    for name in EXTRACTORS.names():
        declared = EXTRACTORS.declaration(name)
        extensions = set(declared.attributes.get("extensions", "").casefold().split(","))
        if extensions & spellings:
            readers[name] = {module.casefold() for module in declared.requires}
    assert {"document", "spreadsheet", "compound", "office", "tabular", "text", "tiff"} <= set(
        readers
    )
    assert {name: needs - base for name, needs in readers.items() if needs - base} == {}


def test_with_no_domain_named_a_dive_reads_and_hashes_each_kind_by_its_core_type(tmp_path) -> None:
    """End to end: the census binds each file to a core type, and a dive spends its budget by
    those types and hashes every file, with no domain pack loaded."""
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog

    project = _project(tmp_path, keep=[])
    # Every file digested, as the depth's preset says (``need`` is read from the depth;
    # by comparison, files of sizes nothing else has need no digest).
    project = project.save_settings(
        project.settings.model_copy(
            update={"hash": project.settings.hash.model_copy(update={"need_from": "depth"})}
        )
    )
    pool = tmp_path / "pool"
    (pool / "kelt.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (pool / "kelt.tif").write_bytes(b"II*\x00" + b"\x00" * 64)
    (pool / "vorn.csv").write_text("sample,dose\n1,5\n")
    (pool / "vorn.txt").write_text("A note about zib.\n")
    (pool / "vorn.json").write_text('{"zib": 1}\n')
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        kinds = dict(
            catalog.query(
                "SELECT e.name, b.term FROM entries e JOIN dictionary_bindings b USING (entry_id) "
                "WHERE b.\"constraint\" = 'extension:format' AND b.resolution_status = 'resolved'"
            ).fetchall()
        )
        families = {
            row[0]
            for row in catalog.query(
                "SELECT json_extract_string(detail, '$.family') FROM journal_events "
                "WHERE event = 'entry.examined'"
            ).fetchall()
        }
        hashed = {
            row[0]
            for row in catalog.query(
                "SELECT e.name FROM observations o JOIN entries e USING (entry_id) "
                "WHERE o.key = 'identity.blake3_quick'"
            ).fetchall()
        }
    assert kinds == {
        "kelt.png": "image_format",
        "kelt.tif": "image_format",
        "vorn.csv": "tabular_format",
        "vorn.txt": "plain_text",
        "vorn.json": "structured_format",
    }
    assert families == {"image_format", "tabular_format", "plain_text", "structured_format"}
    assert hashed == set(kinds)


def test_a_spelling_a_pack_adds_to_another_packs_sense_folds_into_it(tmp_path) -> None:
    """The microscopy pack adds ``traj`` to the trajectory sense the computational
    pack declares. Every pack loaded, it is that one sense and its spellings are unchanged;
    microscopy named alone, the spelling still binds to the type its term names."""
    everything = DictionaryStore.builtin()
    assert everything.look_up("traj") is not None
    assert everything.sense_for_term("trajectory").surfaces.count("traj") == 1

    project = _project(tmp_path, keep=["microscopy"])
    named = DictionaryStore.for_project(project.path)
    found = named.look_up("traj")
    assert found is not None
    assert named.sense_for_term("trajectory") is not None
