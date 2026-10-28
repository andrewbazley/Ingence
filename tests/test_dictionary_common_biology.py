"""Common public biology words in the shipped packs.

Common, public scientific vocabulary ships in the pack where each word belongs: widely used cell
lines, drugs and reagents, stains, fluorescent proteins and well-known proteins, never a person, a
private construct or strain name, a project code, an internal abbreviation or a date. Each is a
sense with a public accession in ``ontology_ids`` where one exists. These tests prove that the words
bind in the file names people write, that BiP and GRP78 are one sense, that no word is a tie with
another sense, and that the text the reference pages copy for them can be pasted into a person's own
pack without making one.

Every file name here is invented.
"""

from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from ingence.core.schema import Entry, EntryType
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import read_name
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore, fold_extensions, load_shipped_sections

STORE = DictionaryStore.builtin()
READER = Normaliser(STORE)

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPOSITORY_ROOT / "tools" / "docs"


def _terms(name: str) -> set[str]:
    """The terms a file name's components bind to, as the name reader binds them."""
    entry = Entry(
        entry_id="entry_name",
        root_id="root_pool",
        relative_path=name,
        name=name,
        entry_type=EntryType.FILE,
        first_seen_run="run_one",
        last_seen_run="run_one",
    )
    reading = read_name(entry, run_id="run_one", normaliser=READER, store=STORE, with_shape=False)
    return {
        binding.term
        for binding in reading.bindings
        if binding.resolution_status is ResolutionStatus.RESOLVED and binding.term
    }


# ---------------------------------------------------------------------------
# The words bind in the names people write
# ---------------------------------------------------------------------------

#: A name and the terms it must bind. The second spells thapsigargin out: the short form `tg` is
#: also transgenic, so it is not shipped.
NAMES = [
    # Cell lines, in the spellings Cellosaurus lists for them.
    ("HeLa_DMSO_001.tif", {"hela", "dimethyl_sulfoxide"}),
    ("hek293_thapsigargin_2h.csv", {"hek293", "thapsigargin"}),
    ("U2OS-DAPI.nd2", {"u2os", "nuclear_stain"}),
    ("HEK 293_blot.tif", {"hek293", "immunoblot"}),
    ("HEK-293_blot.tif", {"hek293", "immunoblot"}),
    ("HEK_293_lysate.csv", {"hek293"}),
    ("HEK293T_cells.csv", {"hek293", "cell"}),
    ("HEK-293T_cells.csv", {"hek293", "cell"}),
    ("HEK293T_GFP.tif", {"hek293", "fluorescent_protein"}),
    ("U-2_OS_mito.tif", {"u2os"}),
    ("U2-OS_run.tif", {"u2os"}),
    ("COS7_mito.tif", {"cos7"}),
    ("COS-7_mito.tif", {"cos7"}),
    ("cos_7_membrane.tif", {"cos7"}),
    ("CHO_cells.csv", {"cho"}),
    ("CHO cells_confluent.tif", {"cho"}),
    ("CHO-K1_clone.tif", {"cho"}),
    ("chinese_hamster_ovary_stable.csv", {"cho"}),
    # Drugs and reagents.
    ("HeLa_CHX_4h.csv", {"hela", "cycloheximide"}),
    ("cycloheximide_chase.csv", {"cycloheximide"}),
    ("tunicamycin_8h.csv", {"tunicamycin"}),
    ("nocodazole_arrest.tif", {"nocodazole"}),
    ("thapsigargin_8h.csv", {"thapsigargin"}),
    ("dimethyl_sulfoxide_stock.csv", {"dimethyl_sulfoxide"}),
    # Stains and dyes in their own classes still bind.
    ("DAPI_stack.tif", {"nuclear_stain"}),
    ("Hoechst_33342_stain.tif", {"nuclear_stain"}),
    ("Hoechst33258.tif", {"nuclear_stain"}),
    ("MitoTracker_red.tif", {"organelle_tracker"}),
    ("mitotracker_deep_red.tif", {"organelle_tracker"}),
    ("LysoTracker_green.tif", {"organelle_tracker"}),
    ("phalloidin_488.tif", {"organelle_tracker"}),
    # Proteins and fluorescent proteins.
    ("BiP_blot.tif", {"bip", "immunoblot"}),
    ("GRP78_blot.tif", {"bip", "immunoblot"}),
    ("grp-78_er.tif", {"bip"}),
    ("HSPA5_knockdown.csv", {"bip", "knockdown"}),
    ("calnexin_er.tif", {"calnexin"}),
    ("GFP_calnexin.tif", {"fluorescent_protein", "calnexin"}),
    ("EGFP_hela.tif", {"fluorescent_protein", "hela"}),
    ("sfGFP_HEK293.czi", {"fluorescent_protein", "hek293"}),
    ("mEGFP_cos7.tif", {"fluorescent_protein", "cos7"}),
    ("mCherry_u2os.tif", {"fluorescent_protein", "u2os"}),
    ("HaloTag_TMR.tif", {"fluorescent_protein", "synthetic_dye"}),
    # The second list: reagents.
    ("BSA_block.csv", {"bovine_serum_albumin"}),
    ("CaCl2_2mM.csv", {"calcium_chloride"}),
    ("KCl_150mM.csv", {"potassium_chloride"}),
    ("DTT_1mM.csv", {"dithiothreitol"}),
    ("SDS_lysis.csv", {"sodium_dodecyl_sulfate"}),
    ("Tween20_wash.csv", {"polysorbate_20"}),
    ("Tween-20_wash.csv", {"polysorbate_20"}),
    ("polysorbate_20_wash.csv", {"polysorbate_20"}),
    ("PEG8k_pellet.csv", {"polyethylene_glycol"}),
    ("PEG-8000_pellet.csv", {"polyethylene_glycol"}),
    ("PEG8000_pellet.csv", {"polyethylene_glycol"}),
    ("PEG4k_pellet.csv", {"polyethylene_glycol"}),
    ("dextran_70kDa.tif", {"dextran"}),
    ("saponin_perm.tif", {"saponin"}),
    ("proteinase_K_digest.csv", {"proteinase_k"}),
    ("protK_digest.csv", {"proteinase_k"}),
    ("ATP-gamma-S.csv", {"atp_gamma_s"}),
    ("ATPgammaS.csv", {"atp_gamma_s"}),
    ("ATPgS_run.csv", {"atp_gamma_s"}),
    ("insulin_stim.csv", {"insulin"}),
    ("histamine_ionomycin.csv", {"histamine", "ionomycin"}),
    ("cholecystokinin_assay.csv", {"cholecystokinin"}),
    ("oligomycin_seahorse.csv", {"oligomycin"}),
    ("novobiocin_gyrase.csv", {"novobiocin"}),
    # Tags, signals and promoters.
    ("His6_GFP.tif", {"his_tag", "fluorescent_protein"}),
    ("6xHis_GFP.tif", {"his_tag", "fluorescent_protein"}),
    ("6xHis-tag.csv", {"his_tag"}),
    ("HisTag_purification.csv", {"his_tag"}),
    ("Myc_tag_GFP.tif", {"myc_tag", "fluorescent_protein"}),
    ("Myc-tag_blot.tif", {"myc_tag", "immunoblot"}),
    ("GST_pulldown.tif", {"glutathione_s_transferase"}),
    ("GFP-KDEL_HeLa.tif", {"kdel", "fluorescent_protein", "hela"}),
    ("CMV_promoter_GFP.tif", {"cmv_promoter", "fluorescent_protein"}),
    ("pCMV_GFP.tif", {"cmv_promoter", "fluorescent_protein"}),
    ("UbC_promoter_GFP.tif", {"ubc_promoter", "fluorescent_protein"}),
    # Methods and labels.
    ("HILO_cells.nd2", {"highly_inclined_illumination", "cell"}),
    ("SMT_tracks.csv", {"single_particle_tracking"}),
    ("single_molecule_tracking_run.csv", {"single_particle_tracking"}),
    ("FRAP_recovery.csv", {"photobleaching_recovery"}),
    ("fluorescence_recovery_after_photobleaching.csv", {"photobleaching_recovery"}),
    ("quantum_dot_605.tif", {"quantum_dot"}),
    ("Qdot_655.tif", {"quantum_dot"}),
    ("rheology_condensate.csv", {"rheology"}),
]


@pytest.mark.parametrize(("name", "expected"), NAMES)
def test_a_common_biology_word_binds_to_its_sense_in_the_names_people_write(
    name: str, expected: set[str]
) -> None:
    assert expected <= _terms(name), name


def test_a_phrase_still_wins_over_the_word_it_holds() -> None:
    """``SDS-PAGE`` is the gel, ``DMSO control`` the vehicle and ``Tween 20`` one reagent."""
    assert "electrophoresis" in _terms("SDS_PAGE_gel.png")
    assert "sodium_dodecyl_sulfate" not in _terms("SDS_PAGE_gel.png")
    assert "vehicle" in _terms("DMSO_control.csv")
    assert "dimethyl_sulfoxide" not in _terms("DMSO_control.csv")
    assert "calcium_imaging" in _terms("calcium_imaging_hela.tif")


def test_bip_and_grp78_are_one_sense_with_two_spellings() -> None:
    first, second = STORE.look_up("BiP"), STORE.look_up("GRP78")
    assert first is not None and second is not None
    assert first.sense.term == second.sense.term == "bip"
    assert first.sense.canonical == "BiP"
    assert {"bip", "grp78", "hspa5"} <= set(first.sense.surfaces)


# ---------------------------------------------------------------------------
# Where each word lives, and its source
# ---------------------------------------------------------------------------

#: Each new sense, the section its pack file declares and the kind it carries.
HOMES = {
    **dict.fromkeys(
        ("hela", "hek293", "u2os", "cos7", "cho", "bip", "calnexin"), ("biochemistry", "subject")
    ),
    **dict.fromkeys(
        (
            "dimethyl_sulfoxide",
            "thapsigargin",
            "tunicamycin",
            "cycloheximide",
            "nocodazole",
            "bovine_serum_albumin",
            "calcium_chloride",
            "potassium_chloride",
            "dithiothreitol",
            "sodium_dodecyl_sulfate",
            "polysorbate_20",
            "polyethylene_glycol",
            "dextran",
            "saponin",
            "proteinase_k",
            "atp_gamma_s",
            "insulin",
            "histamine",
            "cholecystokinin",
            "ionomycin",
            "oligomycin",
            "novobiocin",
            "his_tag",
            "myc_tag",
            "glutathione_s_transferase",
            "kdel",
            "cmv_promoter",
            "ubc_promoter",
        ),
        ("biochemistry", "reagent"),
    ),
    "quantum_dot": ("microscopy", "reagent"),
    "highly_inclined_illumination": ("microscopy", "technique"),
    "rheology": ("spectrometry", "technique"),
}

#: The pattern of the accession each public database writes.
ACCESSIONS = {
    "Cellosaurus": re.compile(r"^Cellosaurus:CVCL_[0-9A-Z]{4}$"),
    "ChEBI": re.compile(r"^ChEBI:[0-9]+$"),
    "PubChem": re.compile(r"^PubChem:[0-9]+$"),
    "UniProt": re.compile(r"^UniProt:([A-NR-Z][0-9]|[OPQ][0-9])[A-Z0-9]{3}[0-9]$"),
}

#: The database each kind of word is taken from, by term: cell lines from Cellosaurus, compounds
#: from ChEBI, proteins from UniProt. A tag, a signal, a promoter, a method or a label names its
#: public page in the pack file's comment instead (it has no accession).
SOURCED_FROM = {
    **dict.fromkeys(("hela", "hek293", "u2os", "cos7", "cho"), "Cellosaurus"),
    **dict.fromkeys(
        (
            "dimethyl_sulfoxide",
            "thapsigargin",
            "tunicamycin",
            "cycloheximide",
            "nocodazole",
            "calcium_chloride",
            "potassium_chloride",
            "dithiothreitol",
            "sodium_dodecyl_sulfate",
            "polysorbate_20",
            "polyethylene_glycol",
            "dextran",
            "saponin",
            "atp_gamma_s",
            "histamine",
            "ionomycin",
            "oligomycin",
            "novobiocin",
        ),
        "ChEBI",
    ),
    **dict.fromkeys(
        (
            "bip",
            "calnexin",
            "bovine_serum_albumin",
            "proteinase_k",
            "insulin",
            "cholecystokinin",
            "glutathione_s_transferase",
        ),
        "UniProt",
    ),
}


def _sense(term: str) -> Any:
    sense = STORE.sense_for_term(term)
    assert sense is not None, term
    return sense


@pytest.mark.parametrize(("term", "home"), sorted(HOMES.items()))
def test_each_new_word_sits_in_the_pack_it_belongs_to_with_the_kind_its_neighbours_have(
    term: str, home: tuple[str, str]
) -> None:
    sense = _sense(term)
    assert (sense.section, sense.theme_kind) == home
    assert sense.domain == home[0]


@pytest.mark.parametrize(("term", "database"), sorted(SOURCED_FROM.items()))
def test_each_cell_line_compound_and_protein_names_its_public_accession(
    term: str, database: str
) -> None:
    ids = _sense(term).ontology_ids
    assert ids, term
    assert any(identifier.startswith(f"{database}:") for identifier in ids), (term, ids)
    for identifier in ids:
        prefix = identifier.split(":", 1)[0]
        assert prefix in ACCESSIONS, (term, identifier)
        assert ACCESSIONS[prefix].match(identifier), (term, identifier)


def test_the_accessions_say_nothing_about_binding() -> None:
    """An accession names a sense elsewhere, so the Dictionary's fingerprint ignores it."""
    assert _sense("hela").ontology_ids == ("Cellosaurus:CVCL_0030",)
    stripped = DictionaryStore(
        tuple(
            replace(
                section,
                senses=tuple(replace(sense, ontology_ids=()) for sense in section.senses),
            )
            for section in load_shipped_sections()
        )
    )
    assert stripped.fingerprint() == STORE.fingerprint()


def test_no_new_spelling_holds_a_four_digit_year() -> None:
    surfaces = [spelling for term in HOMES for spelling in _sense(term).surfaces]
    assert surfaces
    for spelling in surfaces:
        assert not re.search(r"\b(19|20)\d{2}\b", spelling), spelling


# ---------------------------------------------------------------------------
# No new tie
# ---------------------------------------------------------------------------

#: Spellings added to senses that already shipped, by their term.
ADDED_TO_CLASSES = {
    "nuclear_stain": ("hoechst 33342", "hoechst 33258", "bisbenzimide"),
    "fluorescent_protein": ("sfgfp", "megfp", "halo tag"),
    "synthetic_dye": ("tmr", "tetramethylrhodamine"),
    "photobleaching_recovery": ("fluorescence recovery after photobleaching",),
    "single_particle_tracking": ("smt", "single particle tracking", "single molecule tracking"),
}


def _added_spellings() -> list[tuple[str, str]]:
    """Every spelling of a new sense and every spelling added to an existing one, with its term."""
    added = [(term, spelling) for term in HOMES for spelling in _sense(term).surfaces]
    added.extend(
        (term, spelling) for term, spellings in ADDED_TO_CLASSES.items() for spelling in spellings
    )
    return added


def test_the_shipped_dictionary_has_no_undeclared_collision_with_the_new_words() -> None:
    assert STORE.undeclared_ambiguities() == ()


@pytest.mark.parametrize(("term", "spelling"), _added_spellings())
def test_no_new_spelling_is_a_tie_with_another_sense(term: str, spelling: str) -> None:
    hit = STORE.look_up(spelling)
    assert hit is not None, spelling
    assert not hit.ambiguous, (spelling, hit.sense.term, [other.term for other in hit.alternatives])
    assert hit.sense.term == term, spelling


def test_the_short_spellings_that_stay_are_nobody_else_s_word() -> None:
    """The short spellings that ship (``chx``, ``smt``, ``tmr``, ``peg``, ``gst``, ``sds``,
    ``dtt``, ``bsa``) were each checked against the whole shipped Dictionary."""
    taken = {
        "chx": "cycloheximide",
        "smt": "single_particle_tracking",
        "tmr": "synthetic_dye",
        "peg": "polyethylene_glycol",
        "gst": "glutathione_s_transferase",
        "sds": "sodium_dodecyl_sulfate",
        "dtt": "dithiothreitol",
        "bsa": "bovine_serum_albumin",
    }
    for spelling, term in taken.items():
        hit = STORE.look_up(spelling)
        assert hit is not None and not hit.ambiguous, spelling
        assert hit.sense.term == term, spelling


#: Short spellings that a laboratory may mean otherwise are not shipped bare: transgenic, the gene,
#: an assay kit, a virus, a university's initials, a surname, the element, a plate number.
NOT_SHIPPED_BARE = ("tg", "myc", "cck", "cmv", "ubc", "cho", "calcium", "293t", "293 t")

#: Names in which those words mean something else (invented). None may bind a new sense.
OTHER_MEANINGS = [
    "WT_vs_Tg_hippocampus.csv",
    "Tg_mouse3_cortex.tif",
    "APP_tg_slice2.tif",
    "5xFAD_Tg_vs_nonTg.csv",
    "Tg(kdrl-GFP)_fish3.tif",
    "c-Myc_overexpression.csv",
    "MYC_ChIP_seq.bam",
    "myc_knockdown.csv",
    "CCK-8_assay.csv",
    "CMV_infected_fibroblasts.tif",
    "UBC_campus_meeting.pdf",
    "Cho_lab_meeting.pptx",
    "Cho_JH_notes.docx",
    "calcium_traces_cell2.csv",
    "calcium_spikes.csv",
    "scan_293_T.tif",
    "well_293_t.csv",
]


@pytest.mark.parametrize("spelling", NOT_SHIPPED_BARE)
def test_a_short_spelling_with_another_common_meaning_is_not_a_word_of_the_packs(
    spelling: str,
) -> None:
    assert STORE.look_up(spelling) is None, spelling


@pytest.mark.parametrize("name", OTHER_MEANINGS)
def test_a_name_where_a_short_word_means_something_else_binds_none_of_the_new_senses(
    name: str,
) -> None:
    assert not _terms(name) & set(HOMES), name


# ---------------------------------------------------------------------------
# Pasted unchanged, a copied entry makes no new tie
# ---------------------------------------------------------------------------

pytestmark_copy = pytest.mark.skipif(
    not (TOOLS / "reference_copy.py").is_file(),
    reason="the documentation generators are not part of this install",
)


@pytestmark_copy
def test_the_text_the_reference_copies_for_the_new_words_pastes_in_without_a_tie(
    tmp_path: Path,
) -> None:
    """A reader copies a word's entry from the catalogue and pastes it into a pack of their own:
    the same spellings are then in two places, and none may become a tie."""
    sys.path.insert(0, str(TOOLS))
    try:
        from reference_copy import word_entry_text  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(TOOLS))
    from ingence.core.project import Project
    from ingence.dictionary.laboratory_packs import install_pack_text

    terms = {*HOMES, *ADDED_TO_CLASSES}
    senses = [
        sense
        for section in fold_extensions(load_shipped_sections())
        for sense in section.senses
        if sense.term in terms
    ]
    assert {sense.term for sense in senses} == terms
    text = "\n".join(
        [
            "pack:",
            "  name: my-laboratory",
            '  version: "1"',
            "  author: A. Person",
            "  licence: MIT",
            "section: my-laboratory",
            "theme_kind: technique",
            "summary: Words copied from the reference.",
            "entries:",
            *[word_entry_text(sense) for sense in senses],
            "",
        ]
    )
    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    install_pack_text(project.path, "my-laboratory.yaml", text)
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {}).update(packs=["my-laboratory"])
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))

    pasted = DictionaryStore.for_project(project.path)

    assert pasted.undeclared_ambiguities() == ()
    for term, spelling in _added_spellings():
        hit = pasted.look_up(spelling)
        assert hit is not None, spelling
        assert not hit.ambiguous, (spelling, [other.term for other in hit.alternatives])
        assert hit.sense.term == term, spelling
