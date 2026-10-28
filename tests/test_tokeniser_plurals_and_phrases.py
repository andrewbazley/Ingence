"""Tokeniser 13: the name reader's one bump for plurals, glued words, phrases, slips and rounds.

Every class is tested over its grammar, never over a name that revealed it, and every name here is
invented, except the spellings given as examples of a class (``Pseudomonas``, ``elegans``,
``masks``, ``cells``, ``channelhoechst``, the spellings of cryo-EM and of the model organisms,
``unknwon``, ``filamemt``, ``CyroEM``, ``cel``).

A stored project read by tokeniser 12 needs one whole rebind. A partial one is refused in words
naming both readers, ``ingence fix`` names it, and the run record states reader 13.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.dictionary.normalize import NORMALIZER_VERSION, AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

STORE = DictionaryStore.builtin()
READER = Normaliser(STORE, years=(2013, 2026))


def _census(project: Project, run: int, **options: object) -> str:
    report = run_census(
        Project.open(project.path),
        run_id=f"run_20260101T00000{run}Z_{str(run) * 12}",
        started_at=f"2026-01-01T00:00:0{run}Z",
        rebind=run > 1,
        **options,
    )
    assert report.published
    return report.run_id


def _pool(tmp_path: Path, names: dict[str, list[str]] | list[str]) -> Project:
    """A project over a pool holding each name (a folder's names under its key, ``""`` the top)."""
    pool = tmp_path / "pool"
    pool.mkdir()
    folders = names if isinstance(names, dict) else {"": names}
    for folder, held in folders.items():
        where = pool / folder if folder else pool
        where.mkdir(parents=True, exist_ok=True)
        for name in held:
            (where / name).write_text("a")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool])


def _bindings(project: Project) -> dict[tuple[str, str], tuple[str, str | None, str | None]]:
    """Each component's binding by its entry's name and the component's raw text: the status, the
    term and the constraint."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.raw_value, b.resolution_status, b.term, b.constraint "
            "FROM dictionary_bindings b JOIN components c ON c.component_id = b.component_id "
            "JOIN entries e ON e.entry_id = b.entry_id"
        ).fetchall()
    return {(name, raw): (status, term, constraint) for name, raw, status, term, constraint in rows}


def _atoms(name: str, reader: Normaliser = READER) -> list[tuple[str, str]]:
    return [(atom.text, atom.kind.value) for atom in reader.atoms(name)]


# -- the version: one whole rebind -----------------------------------------------------------------


def test_the_version_names_this_bump() -> None:
    # 14 since 0.4.0: the 2Pl fix, the clock on the event time, spaced quantities.
    assert NORMALIZER_VERSION == "14"  # 14 since 0.4.0
    assert READER.version == "14"


def test_a_partial_rebind_is_refused_across_readers_12_and_13(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project read by tokeniser 12 needs one whole rebind; the run record then states 13."""
    import ingence.dictionary.normalize as normalize

    project = _pool(tmp_path, ["zeph_masks_007.txt", "kelt_vorn.txt"])
    with monkeypatch.context() as patched:
        patched.setattr(normalize, "NORMALIZER_VERSION", "12")
        _census(project, 1)
    with pytest.raises(ValueError, match="tokeniser version 12 and this engine reads them as 14"):
        _census(project, 2, spellings=("kelt",))
    _census(project, 3)
    with open_catalog(project.path) as catalog:
        readers = catalog.query("SELECT DISTINCT normalizer_version FROM components").fetchall()
        (stated,) = catalog.query("SELECT normalizer_version FROM run").fetchone()
    assert (readers, stated) == ([("14",)], "14")


def test_fix_names_a_project_read_by_another_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ingence fix`` says the stored names were read by tokeniser 12, and what to run, once."""
    from typer.testing import CliRunner

    import ingence.dictionary.normalize as normalize
    from ingence.cli.main import app

    project = _pool(tmp_path, ["zeph_masks_007.txt"])
    with monkeypatch.context() as patched:
        patched.setattr(normalize, "NORMALIZER_VERSION", "12")
        _census(project, 1)
    said = " ".join(CliRunner().invoke(app, ["fix", "-p", str(project.path)]).output.split())
    assert "Ingence read these names with name reader 12, and this version uses 14" in said
    assert "ingence index --rebind" in said
    _census(project, 2)
    again = " ".join(CliRunner().invoke(app, ["fix", "-p", str(project.path)]).output.split())
    assert "read by tokenizer" not in again


def _taught(*words: tuple[str, str]) -> DictionaryStore:
    """The shipped vocabulary and a section of invented words, each ``(term, surface)``."""
    from ingence.dictionary.store import Section, Sense, load_shipped_sections

    senses = tuple(
        Sense(
            term=term, canonical=term, theme_kind="subject", section="taught", surfaces=(surface,)
        )
        for term, surface in words
    )
    return DictionaryStore((*load_shipped_sections(), Section("taught", "", senses)))


# -- 1. plurals never split a word wrongly ---------------------------------------------------------


def test_a_plural_binds_its_singulars_sense_where_the_singular_is_a_dictionary_word(
    tmp_path: Path,
) -> None:
    """``masks`` and ``cells`` resolve to mask and cell; the component keeps the name's spelling
    (an observation), and only the binding reads it as the singular (an interpretation)."""
    project = _pool(tmp_path, {"masks": ["zeph_007.tif"], "": ["cells_07.tif", "kelt_boxes.txt"]})
    _census(project, 1)
    found = _bindings(project)
    mask = STORE.look_up("mask")
    cell = STORE.look_up("cell")
    assert mask is not None and cell is not None
    assert found[("masks", "masks")][:2] == ("resolved", mask.sense.term)
    assert found[("cells_07.tif", "cells")][:2] == ("resolved", cell.sense.term)
    # A plural binds as a free word of the name: no marker, so every rule reading names reads it.
    assert found[("masks", "masks")][2] is None


def test_an_unknown_plural_stays_as_written(tmp_path: Path) -> None:
    """``zephs``: its singular is no Dictionary word, so it is the unknown word it was."""
    project = _pool(tmp_path, ["zephs_01.txt"])
    _census(project, 1)
    assert _bindings(project)[("zephs_01.txt", "zephs")][:2] == ("examined_no_evidence", None)


def test_latin_names_never_lose_their_final_s(tmp_path: Path) -> None:
    """``pseudomonas_growth_03.csv`` binds no ``pseudomona``; ``elegans_L4_02.tif`` no ``elegan``.

    Neither stem is a Dictionary word, so neither word is read as a plural.
    """
    project = _pool(tmp_path, ["pseudomonas_growth_03.csv", "elegans_L4_02.tif"])
    _census(project, 1)
    found = _bindings(project)
    assert found[("pseudomonas_growth_03.csv", "pseudomonas")][:2] == ("examined_no_evidence", None)
    assert all((term or "") != "pseudomona" for _, term, _ in found.values())
    assert all((term or "") != "elegan" for _, term, _ in found.values())


def test_an_ending_no_english_plural_has_never_folds_even_onto_a_word() -> None:
    """``-us``, ``-is`` and ``-ss`` end a Latin name or a singular, never a plural: with ``zorbu``,
    ``kelti`` and ``vornas`` taught as words, ``zorbus``, ``keltis`` and ``vornass`` stay unknown,
    while ``brisas`` folds onto a taught ``brisa``."""
    store = _taught(
        ("zorbu", "zorbu"), ("kelti", "kelti"), ("vornas", "vornas"), ("brisa", "brisa")
    )
    for word in ("zorbus", "keltis", "vornass"):
        assert store.look_up_plural(word) is None, word
    found = store.look_up_plural("brisas")
    assert found is not None and found.sense.term == "brisa"
    assert "us" in store.plural_keeps() and "is" in store.plural_keeps()


def test_a_plural_never_folds_onto_a_unit_or_a_short_word() -> None:
    """``mins`` is no plural of a unit's spelling here (units are the quantity reader's), and a
    word under four letters is never read as a plural (``ns`` and ``ms`` are units)."""
    assert STORE.look_up_plural("mins") is None
    assert STORE.look_up_plural("ems") is None


# -- 2. glued known words divide -------------------------------------------------------------------


def test_a_run_glued_from_two_dictionary_words_divides_and_each_binds() -> None:
    """``channelhoechst`` is ``channel`` and ``hoechst``, each piece carrying the whole spelling."""
    atoms = READER.atoms("channelhoechst_03")
    assert [(atom.text, atom.case_whole) for atom in atoms[:2]] == [
        ("channel", "channelhoechst"),
        ("hoechst", "channelhoechst"),
    ]
    assert atoms[2].kind is AtomKind.NUMBER


def test_a_census_binds_both_halves_of_a_glued_spelling(tmp_path: Path) -> None:
    project = _pool(tmp_path, ["channelhoechst_03.tif", "channelhoechst_04.tif"])
    _census(project, 1)
    found = _bindings(project)
    channel, stain = STORE.look_up("channel"), STORE.look_up("hoechst")
    assert channel is not None and stain is not None
    assert found[("channelhoechst_03.tif", "channel")][:2] == ("resolved", channel.sense.term)
    assert found[("channelhoechst_03.tif", "hoechst")][:2] == ("resolved", stain.sense.term)


def test_a_persons_one_word_keeps_a_glued_spelling_whole(tmp_path: Path) -> None:
    """The case reading's route: an answer that the spelling is one word binds it whole."""
    from ingence.core.schema import RecordEventKind
    from ingence.dictionary.case_reading import case_target
    from ingence.dictionary.discover import CASE_WHOLE_CONSTRAINT
    from ingence.record.log import append_event

    project = _pool(tmp_path, ["channelhoechst_03.tif"])
    _census(project, 1)
    append_event(
        project.path,
        target=case_target("channelhoechst"),
        kind=RecordEventKind.ASSIGN,
        payload={"reading": "whole"},
        actor="kelt",
        occurred_at="2026-01-01T00:00:02Z",
        against_run_id="run_20260101T000001Z_111111111111",
    )
    _census(project, 3)
    found = _bindings(project)
    assert found[("channelhoechst_03.tif", "channel")] == (
        "examined_no_evidence",
        None,
        CASE_WHOLE_CONSTRAINT,
    )


def test_a_glued_run_divides_only_into_long_enough_words_and_by_one_best_division() -> None:
    store = _taught(
        ("keltvorn", "keltvorn"),
        ("kelt", "kelt"),
        ("vorn", "vorn"),
        ("zib", "zib"),
        ("keltz", "keltz"),
        ("orbvan", "orbvan"),
        ("zorbvan", "zorbvan"),
        ("vornzi", "vornzi"),
        ("keltvo", "keltvo"),
        ("rnzi", "rnzi"),
    )
    reader = Normaliser(store)
    # The whole is a Dictionary word: no division.
    assert [atom.text for atom in reader.atoms("keltvorn")] == ["keltvorn"]
    # A half under the minimum (four letters): no division.
    assert [atom.text for atom in reader.atoms("zibkelt")] == ["zibkelt"]
    # Two divisions: the one whose shorter half is longest (keltz and orbvan over kelt, zorbvan).
    assert [atom.text for atom in reader.atoms("keltzorbvan")] == ["keltz", "orbvan"]
    # Two divisions as good as each other: no division, and the word is asked about whole.
    assert [atom.text for atom in reader.atoms("keltvornzi")] == ["keltvornzi"]
    # Neither half a word: no division.
    assert [atom.text for atom in reader.atoms("brisaquill")] == ["brisaquill"]


def test_the_divider_is_a_setting_recorded_in_the_version() -> None:
    off = Normaliser(STORE, glued_words="whole")
    assert [atom.text for atom in off.atoms("channelhoechst")] == ["channelhoechst"]
    assert off.version == "14-glued_words_whole"
    longer = Normaliser(STORE, glued_half_minimum=8)
    assert [atom.text for atom in longer.atoms("channelhoechst")] == ["channelhoechst"]
    assert longer.version == "14-glued_half_minimum_8"


def test_no_single_word_of_the_shipped_dictionary_divides() -> None:
    """The false-split measure: every single word the shipped Dictionary spells, read alone."""
    from ingence.dictionary.normalize import glued_division

    words = sorted(
        {
            surface
            for sense in STORE.senses()
            if sense.theme_kind != "unit"
            for surface in sense.surfaces
            if surface.isalpha()
        }
    )
    divided = [word for word in words if glued_division(word, knows=READER.knows) is not None]
    assert len(words) > 1000
    assert divided == []


# -- 3. phrases and spellings as one sense, everywhere ---------------------------------------------

#: Every way a name writes cryo-EM: one sense, in a folder's name and a file's.
CRYO_EM = (
    "CryoEM",
    "cryo-EM",
    "cryo EM",
    "Cryo_EM",
    "cryoem",
    "cryo-electron microscopy",
    "Cryo.EM",
)

#: The model organisms by their binomial and short forms, each written as a name writes it: the
#: term each must bind (looked up by its binomial) and the spellings.
ORGANISMS = {
    "escherichia coli": ("E. coli", "E.coli", "E_coli", "Ecoli", "Escherichia_coli", "e-coli"),
    "saccharomyces cerevisiae": ("S_cerevisiae", "S.cerevisiae", "budding_yeast", "cerevisiae"),
    "schizosaccharomyces pombe": ("S. pombe", "Spombe", "fission-yeast", "pombe"),
    "caenorhabditis elegans": ("C_elegans", "C. elegans", "celegans", "elegans"),
    "drosophila melanogaster": ("D_melanogaster", "drosophila", "fruit_flies"),
    "danio rerio": ("D.rerio", "zebrafish", "Danio_rerio"),
    "mus musculus": ("M_musculus", "mouse", "mice", "Mus musculus"),
    "arabidopsis thaliana": ("A_thaliana", "A. thaliana", "arabidopsis", "thale_cress"),
    "homo sapiens": ("H_sapiens", "Homo_sapiens", "hsapiens"),
}


def _term_of(spelling: str) -> str:
    hit = STORE.look_up(spelling)
    assert hit is not None, spelling
    return hit.sense.term


def test_every_spelling_of_cryo_em_is_one_sense_in_folders_and_files(tmp_path: Path) -> None:
    # Numbered, because a folder named ``CryoEM_set`` and one named ``cryoem_set`` are one folder
    # on a file system that ignores case.
    folders = {
        f"set{index}_{written}": [f"kelt_{written}_03.mrc"] for index, written in enumerate(CRYO_EM)
    }
    project = _pool(tmp_path, folders)
    _census(project, 1)
    found = _bindings(project)
    expected = _term_of("cryoem")
    for index, written in enumerate(CRYO_EM):
        assert found[(f"set{index}_{written}", written)][:2] == ("resolved", expected), written
        assert found[(f"kelt_{written}_03.mrc", written)][:2] == ("resolved", expected), written


def test_a_phrase_binds_only_with_its_words_adjacent_and_in_order() -> None:
    """``single particle`` and ``phase plate``: one atom across ``_``, ``-``, a space or a dot; two
    words apart, or the other way round, are the words they are."""
    for name in ("single_particle_04", "single-particle_04", "single particle 04", "phase.plate_2"):
        words = [atom.text for atom in READER.atoms(name) if atom.kind is AtomKind.WORD]
        assert len(words) == 1, name
        assert STORE.look_up(words[0]) is not None, name
    for name in ("particle_single_04", "single_zeph_particle", "plate_phase_2"):
        assert len([a for a in READER.atoms(name) if a.kind is AtomKind.WORD]) >= 2, name
    assert _term_of("single particle") != _term_of("phase plate")


def test_the_model_organisms_are_recognised_whole(tmp_path: Path) -> None:
    names = [f"zeph_{written}_07.tif" for spellings in ORGANISMS.values() for written in spellings]
    project = _pool(tmp_path, {"": names, "E. coli": ["kelt_01.tif"]})
    _census(project, 1)
    found = _bindings(project)
    for binomial, spellings in ORGANISMS.items():
        expected = _term_of(binomial)
        for written in spellings:
            assert found[(f"zeph_{written}_07.tif", written)][:2] == ("resolved", expected), written
    assert found[("E. coli", "E. coli")][:2] == ("resolved", _term_of("escherichia coli"))


def test_a_binomial_written_apart_or_reversed_is_no_organism() -> None:
    organisms = {_term_of(binomial) for binomial in ORGANISMS}
    for name in ("coli_E_07", "E_zeph_coli", "sapiens_kelt_Homo", "rerio_D"):
        for atom in READER.atoms(name):
            hit = STORE.look_up(atom.text) if atom.kind is AtomKind.WORD else None
            assert hit is None or hit.sense.term not in organisms, (name, atom.text)


# -- 4. misspellings suggested, never bound --------------------------------------------------------


def _spellings(store: DictionaryStore, **settings: int):
    from ingence.dictionary.spelling import SpellingIndex

    return SpellingIndex(
        ((sense.term, surface) for sense in store.senses() for surface in sense.surfaces),
        **settings,
    )


def test_a_slip_of_a_long_dictionary_word_raises_its_candidates() -> None:
    """``unknwon`` (two letters swapped), ``filamemt`` (one changed), ``CyroEM`` (swapped) and
    ``flamemt`` (one dropped, one changed: two edits) each raise the word they are a slip of."""
    index = _spellings(_taught(("unknown", "unknown"), ("filament", "filament")))
    assert [found.spelling for found in index.candidates("unknwon")][:1] == ["unknown"]
    assert [found.spelling for found in index.candidates("filamemt")][:1] == ["filament"]
    assert [found.spelling for found in index.candidates("flamemt")][:1] == ["filament"]
    cryo = index.candidates("CyroEM")
    assert cryo and cryo[0].spelling == "cryo-em" and cryo[0].terms == (_term_of("cryoem"),)
    assert (cryo[0].edits, index.candidates("flamemt")[0].edits) == (1, 2)


def test_a_short_word_raises_none() -> None:
    """``cel``: no Dictionary word of six letters or more is two edits from a three-letter word."""
    index = _spellings(STORE)
    assert index.candidates("cel") == ()
    assert index.candidates("ratoi") == ()  # nor a slip of a word under six letters (``ratio``)


def test_the_thresholds_are_settings() -> None:
    from ingence.core.settings import DictionarySettings

    spelling = DictionarySettings().spelling
    assert (spelling.edits, spelling.minimum_letters, spelling.candidates) == (2, 6, 3)
    store = _taught(("filament", "filament"))
    assert _spellings(store, edits=1).candidates("flamemt") == ()
    assert _spellings(store, minimum=9).candidates("filamemt") == ()
    taught = _taught(("zorbelt", "zorbelt"), ("zorbeln", "zorbeln"), ("zorbels", "zorbels"))
    assert len(_spellings(taught, candidates=2).candidates("zorbelx")) == 2


# -- 5. R2 is a round or a replicate, not a version ------------------------------------------------


def test_a_letter_r_and_a_number_is_an_indexed_label_never_a_version() -> None:
    for name in ("zeph_R2_kelt", "zeph_r2_kelt", "zeph_R12"):
        kinds = {atom.text: atom.kind for atom in READER.atoms(name)}
        counter = next(text for text in kinds if text.lower().startswith("r"))
        assert kinds[counter] is AtomKind.INDEXED, name
    # A version keeps its longer markers and its dotted numbers.
    for name in ("zeph_rev2", "zeph_v2", "zeph_r1.2"):
        assert any(atom.kind is AtomKind.VERSION for atom in READER.atoms(name)), name


def test_siblings_counting_r1_r2_r3_are_an_indexed_label_bound_through_no_sense(
    tmp_path: Path,
) -> None:
    """``zeph_R1_kelt.tif`` to ``zeph_R3_kelt.tif`` in one folder: no version, an indexed label,
    and no sense met through its whole text (r squared, a read of a sequencing pair)."""
    from ingence.dictionary.discover import UNBOUND_LABEL_CONSTRAINT

    names = [f"zeph_R{index}_kelt.tif" for index in (1, 2, 3)]
    project = _pool(tmp_path, {"counted": names})
    _census(project, 1)
    found = _bindings(project)
    for index, name in enumerate(names, start=1):
        assert found[(name, f"R{index}")] == (
            "examined_no_evidence",
            None,
            UNBOUND_LABEL_CONSTRAINT,
        ), name
    with open_catalog(project.path) as catalog:
        kinds = catalog.query(
            "SELECT DISTINCT component_type FROM components WHERE raw_value LIKE 'R_'"
        ).fetchall()
    assert kinds == [("indexed",)]


def test_a_lone_r1_and_r2_are_equally_non_committal(tmp_path: Path) -> None:
    """``zeph_R1.tif`` and ``zeph_R2.tif`` alone are indexed labels bound through no sense, as
    counted ones are.

    No sense the Dictionary ranks lower is moved back beside the one it ranks higher.
    """
    from ingence.dictionary.discover import UNBOUND_LABEL_CONSTRAINT

    project = _pool(tmp_path, {"one": ["zeph_R1.tif"], "two": ["zeph_R2.tif"]})
    _census(project, 1)
    found = _bindings(project)
    for name, label in (("zeph_R1.tif", "R1"), ("zeph_R2.tif", "R2")):
        assert found[(name, label)] == ("examined_no_evidence", None, UNBOUND_LABEL_CONSTRAINT)


def test_a_sequencing_read_file_keeps_its_read_senses_counted_or_not(tmp_path: Path) -> None:
    """In a file whose type is a sequencing read format, ``R1`` and ``R2`` are read one and read two
    through the pack's own file-type scope, three siblings or not.
    """
    names = [f"S1_R{index}_001.fastq.gz" for index in (1, 2, 3)] + ["S2_R2_001.fq"]
    project = _pool(tmp_path, {"run": names})
    _census(project, 1)
    found = _bindings(project)
    assert found[("S1_R1_001.fastq.gz", "R1")][:2] == ("resolved", "read_one")
    assert found[("S1_R2_001.fastq.gz", "R2")][:2] == ("resolved", "read_two")
    assert found[("S2_R2_001.fq", "R2")][:2] == ("resolved", "read_two")
    assert found[("S1_R3_001.fastq.gz", "R3")][:2] == ("examined_no_evidence", None)


def test_the_read_senses_are_declared_for_the_read_format_as_data() -> None:
    from ingence.core.ids import ScopeKind

    scoped = [
        sense.scope
        for sense in STORE.senses()
        if any(surface in ("r1", "r2") for surface in sense.surfaces)
        and sense.term in ("read_one", "read_two")
    ]
    assert len(scoped) == 2
    assert all(
        (scope.kind, scope.value) == (ScopeKind.FILE_TYPE, "read_format") for scope in scoped
    )


def test_a_persons_answer_on_a_round_label_still_wins() -> None:
    """A person who said what ``R2`` means is read: an answer wins (USER), anywhere."""
    from ingence.core.schema import DictionaryOrigin
    from ingence.dictionary.discover import _look_up
    from ingence.dictionary.store import PROJECT_SCOPE, Section, Sense, load_shipped_sections

    answered = Sense(
        term="round",
        canonical="round",
        theme_kind="replicate",
        section="answers",
        surfaces=("R2",),
        origin=DictionaryOrigin.USER,
    )
    store = DictionaryStore(
        (
            *load_shipped_sections(),
            Section("answers", "", (answered,), origin=DictionaryOrigin.USER),
        )
    )
    (atom,) = [a for a in Normaliser(store).atoms("zeph_R2") if a.kind is AtomKind.INDEXED]
    hit, _ = _look_up(atom, store=store, scope=PROJECT_SCOPE)
    assert hit is not None and hit.sense.origin is DictionaryOrigin.USER
    assert hit.alternatives == ()


# -- further cases ---------------------------------------------------------------------------------


def test_a_run_never_divides_after_a_word_forming_prefix() -> None:
    """A prefix forms one word with what follows (``Micrographs``, ``metaphase``, ``transform``,
    ``polyphase``), and the Dictionary lists the prefixes.

    A taught prefix-like word glued to a taught word still divides where it is not a listed prefix.
    """
    for name in ("Micrographs_03", "metaphase_cells_03", "affine_transform_02", "polyphase_7"):
        word = next(atom for atom in READER.atoms(name) if atom.kind is AtomKind.WORD)
        assert word.case_whole is None, name
    assert {"micro", "meta", "trans", "poly", "photo", "macro", "ortho"} <= STORE.word_prefixes()
    taught = _taught(("zorb", "zorb"), ("keltvorn", "keltvorn"), ("metazorb", "metazorb"))
    reader = Normaliser(taught)
    assert [atom.text for atom in reader.atoms("zorbkeltvorn")] == ["zorb", "keltvorn"]
    assert [atom.text for atom in reader.atoms("metakeltvorn")] == ["metakeltvorn"]


def test_a_divided_word_can_be_taught_whole(tmp_path: Path) -> None:
    """The Dictionary page reads a spelling to teach with glued words whole.

    So a word the reader divides (``channelhoechst``) or might (``metaphase``) is taught as one.
    """
    from ingence.api.routes.dictionary import add_spelling

    project = _pool(tmp_path, ["metaphase_zeph_01.tif", "channelhoechst_02.tif"])
    _census(project, 1)
    for spelling in ("metaphase", "channelhoechst"):
        with open_catalog(project.path) as catalog:
            written = add_spelling(
                catalog,
                project.path,
                actor="kelt",
                occurred_at="2026-01-01T00:00:05Z",
                spelling=spelling,
                term=f"zorb_{spelling}",
                theme_kind="stage",
            )
        assert written.target == f"term:{spelling}"


def test_a_plural_never_folds_onto_a_date_a_doubt_word_or_a_status() -> None:
    """``temps``, ``odds``, ``bins`` and ``checks`` are not doubt words, and ``mays`` (``Zea mays``)
    and ``Mars`` are not months.

    The kinds a plural never folds onto are the Dictionary's list, and nouns still fold (``masks``,
    ``cells``).
    """
    for word in ("temps", "odds", "bins", "checks", "mays", "Mars"):
        assert STORE.look_up_plural(word) is None, word
    assert {"date", "quality", "status", "unit"} <= STORE.plural_excluded_kinds()
    for word in ("masks", "cells"):
        assert STORE.look_up_plural(word) is not None, word


def test_a_word_in_capitals_ending_in_a_capital_s_is_never_a_plural() -> None:
    """``SIMS`` is an acronym (secondary ion mass spectrometry), not two SIMs."""
    assert STORE.look_up_plural("SIMS") is None
    assert STORE.look_up_plural("Masks") is not None


def test_the_least_length_of_a_plural_is_a_setting() -> None:
    from ingence.core.settings import DictionarySettings

    assert DictionarySettings().plural_minimum == 4
    assert STORE.look_up_plural("masks", minimum=6) is None
    assert Normaliser(STORE, plural_minimum=6).version == "14-plural_minimum_6"


def test_a_phrase_in_the_plural_and_across_doubled_separators_is_one_sense(tmp_path: Path) -> None:
    """A plural folds a phrase's last word (``phase_plates``, ``single_particles``), and a run of
    separators is one separator (``single__particle``, ``single - particle``).

    Words apart or reversed are still the words they are.
    """
    names = [
        "zeph_phase_plates_2.tif",
        "zeph_single_particles_03.tif",
        "zeph_single__particle_04.tif",
        "zeph_single - particle 05.tif",
    ]
    project = _pool(tmp_path, names)
    _census(project, 1)
    found = _bindings(project)
    plate, particle = _term_of("phase plate"), _term_of("single particle")
    assert found[(names[0], "phase_plates")][:2] == ("resolved", plate)
    assert found[(names[1], "single_particles")][:2] == ("resolved", particle)
    assert found[(names[2], "single__particle")][:2] == ("resolved", particle)
    assert found[(names[3], "single - particle")][:2] == ("resolved", particle)
    assert len([a for a in READER.atoms("particles_single_2") if a.kind is AtomKind.WORD]) == 2


def test_a_common_word_that_names_an_organism_elsewhere_is_no_organism(tmp_path: Path) -> None:
    """``fly``, ``worm`` and ``human`` are too often other words (a fly-through, a worm gear, a
    human annotation) to be an organism alone.
    """
    organisms = {_term_of(binomial) for binomial in ORGANISMS}
    names = ["fly_through_03.mp4", "human_annotation_02.csv", "worm_gear_01.txt", "flies_04.tif"]
    project = _pool(tmp_path, names)
    _census(project, 1)
    for (name, _), (_, term, _) in _bindings(project).items():
        assert term not in organisms, name


def test_a_partial_rebind_names_the_readers_before_the_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project read by tokeniser 12 with another vocabulary is refused in words naming the
    readers, the change that needs the whole rebind first.
    """
    import ingence.dictionary.normalize as normalize
    from ingence.dictionary.store import DictionaryStore as Store

    project = _pool(tmp_path, ["zeph_masks_007.txt", "kelt_vorn.txt"])
    every = Store.records
    with monkeypatch.context() as patched:
        patched.setattr(normalize, "NORMALIZER_VERSION", "12")
        patched.setattr(Store, "records", lambda self, **kw: every(self, **kw)[1:])
        _census(project, 1)
    with pytest.raises(ValueError, match="tokeniser version 12 and this engine reads them as 14"):
        _census(project, 2, spellings=("kelt",))


def test_a_partial_rebind_is_refused_across_resolvers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingence.dictionary.discover as discover

    project = _pool(tmp_path, ["zeph_masks_007.txt", "kelt_vorn.txt"])
    with monkeypatch.context() as patched:
        patched.setattr(discover, "RESOLVER_VERSION", "8")
        _census(project, 1)
    with pytest.raises(ValueError, match="resolver version 8 and this engine binds them as 10"):
        _census(project, 2, spellings=("kelt",))


def test_the_vocabulary_fingerprint_moves_with_the_readers_lists() -> None:
    """A pack changing only a list the reader reads is a changed vocabulary."""
    from ingence.dictionary.store import Section, load_shipped_sections

    shipped = load_shipped_sections()
    base = DictionaryStore(shipped).fingerprint()
    for listed in ("word_prefixes", "plural_keeps", "plural_never_onto", "word_endings"):
        extra = Section("zorb_lists", "", (), **{listed: ("zorb",)})
        assert DictionaryStore((*shipped, extra)).fingerprint() != base, listed


def test_only_a_cut_after_a_listed_prefix_is_refused() -> None:
    """A run beginning with a prefix's letters still divides where its left half is a whole word
    (``microscope``, ``metadata``) and no listed prefix.

    ``micro|graph`` and ``meta|phase`` stay refused.
    """
    reader = Normaliser(_taught(("zorbel", "zorbel")))
    for whole, left in (("microscopezorbel", "microscope"), ("metadatazorbel", "metadata")):
        assert [atom.text for atom in reader.atoms(whole)] == [left, "zorbel"], whole
    for name in ("Micrographs", "metaphase", "transform"):
        assert [atom.text for atom in reader.atoms(name)] == [name], name


def test_a_phrase_in_the_plural_never_folds_onto_a_kind_a_plural_never_folds_onto() -> None:
    """``do_not_uses`` is not a plural of the doubt words ``do not use``."""
    from ingence.dictionary.discover import _look_up
    from ingence.dictionary.store import PROJECT_SCOPE

    (atom,) = [a for a in READER.atoms("zeph_do_not_uses") if a.normalised == "do not use"]
    hit, _ = _look_up(atom, store=STORE, scope=PROJECT_SCOPE)
    assert hit is None
