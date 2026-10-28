"""The Dictionary's readings of methods, phrases and organisms written in prose.

The name reader must not misread prose in ways that reach the theme labels. Cryo-EM is not
electron microscopy, and its derivatives' ``cryo`` is not cryopreservation. A phrase is not read
as a method by its first half (``phase separation``, ``plasma membrane``, ``calcium channel``,
``X-ray tomography``, ``single cell``). ``electron microscopy`` written out is not read as two
loose words. Model organisms beyond the nine shipped are not read in pieces. Each class is tested
over its grammar, with invented words around the spellings (``kelt``, ``zeph``, ``vorn``). The
spellings are the vocabulary's own.
"""

from __future__ import annotations

import pytest

from ingence.core.schema import Entry, EntryType
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import read_name
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore

STORE = DictionaryStore.builtin()
READER = Normaliser(STORE)


def _read(text: str) -> list[tuple[str, str | None]]:
    """Each word component of ``text`` read as a folder's name: its spelling and the term it binds
    (None where it binds nothing)."""
    entry = Entry(
        entry_id="entry_kelt",
        root_id="root_kelt",
        relative_path=text,
        name=text,
        entry_type=EntryType.DIRECTORY,
        first_seen_run="run_kelt",
        last_seen_run="run_kelt",
    )
    reading = read_name(entry, run_id="run_kelt", normaliser=READER, store=STORE, with_shape=False)
    by_component = {binding.component_id: binding for binding in reading.bindings}
    found: list[tuple[str, str | None]] = []
    for component in reading.components:
        if component.component_type != "word":
            continue
        binding = by_component.get(component.component_id)
        resolved = binding is not None and binding.resolution_status is ResolutionStatus.RESOLVED
        found.append((component.normalized_value, binding.term if resolved else None))
    return found


def _one(text: str) -> str | None:
    """The term ``text`` binds as one word, failing where it reads as more than one."""
    words = _read(text)
    assert len(words) == 1, (text, words)
    return words[0][1]


def _term(spelling: str) -> str:
    hit = STORE.look_up(spelling)
    assert hit is not None, spelling
    return hit.sense.term


def _canonical(term: str | None) -> str | None:
    sense = STORE.sense_for_term(term) if term else None
    return sense.canonical if sense is not None else None


def _terms(text: str) -> set[str | None]:
    return {term for _, term in _read(text)}


# -- 1. CryoEM is its own sense, and its derivatives theirs

CRYO_EM = (
    "CryoEM",
    "cryo-EM",
    "cryo EM",
    "Cryo_EM",
    "cryoem",
    "Cryo-EM",
    "cryo-electron microscopy",
    "cryo electron microscopy",
    "electron cryo-microscopy",
)

#: Each derivative's canonical name and its spellings.
DERIVATIVES = {
    "CryoET": (
        "cryo-ET",
        "cryoET",
        "CryoET",
        "cryo-electron tomography",
        "cryo electron tomography",
        "cryo-electron tomograms",
        "electron cryo-tomography",
    ),
    "CryoFIB": ("cryo-FIB", "cryoFIB", "cryo-FIB-SEM", "cryo FIB SEM"),
    "CryoCLEM": ("cryo-CLEM", "cryoCLEM", "cryo CLEM", "cryo-correlative"),
}


@pytest.mark.parametrize("written", CRYO_EM)
def test_every_spelling_of_cryo_em_binds_one_sense_named_cryoem(written: str) -> None:
    term = _one(written)
    assert _canonical(term) == "CryoEM", written
    assert term == _term("cryoem")


def test_cryo_em_is_narrower_than_electron_microscopy_which_keeps_sem_and_fib_sem() -> None:
    # (``SEM`` alone is the core's standard error of the mean.)
    electron = {_one(written) for written in ("FIB-SEM", "fibsem", "electron microscopy")}
    assert len(electron) == 1
    assert electron != {_term("cryoem")}
    assert _canonical(next(iter(electron))) != "CryoEM"


@pytest.mark.parametrize(("name", "spellings"), sorted(DERIVATIVES.items()))
def test_each_cryo_derivative_binds_its_own_sense(name: str, spellings: tuple[str, ...]) -> None:
    for written in spellings:
        term = _one(written)
        assert _canonical(term) == name, (written, term)
        assert term not in {_term("cryoem"), _term("cryopreservation")}, written


# -- 2. cryo alone is not cryopreservation


def test_cryo_beside_its_microscopy_half_is_that_phrase_never_cryopreservation() -> None:
    cryopreservation = _term("cryopreservation")
    for text in ("kelt cryo-EM zeph", "kelt cryo-electron micrographs", "cryo-ET of vorn"):
        assert cryopreservation not in _terms(text), text
    # ``cryo-electron`` before any other word is cryo-EM's first half.
    assert _canonical(_read("cryo-electron kelt")[0][1]) == "CryoEM"


def test_cryo_alone_binds_nothing_but_cryopreservation_keeps_its_own_spellings() -> None:
    assert _one("cryo") is None
    assert _terms("kelt_cryo_zeph") == {None}
    cryopreservation = _term("cryopreservation")
    for written in ("cryopreserved", "cryopreservation", "cryoprotectant", "cryovial"):
        assert _one(written) == cryopreservation, written


# -- 3. a phrase wins over the method its first half names


def test_phase_separation_is_no_phase_contrast() -> None:
    phase_contrast = _term("phase-contrast")
    for written in ("phase separation", "liquid-liquid phase separation", "LLPS"):
        term = _one(written)
        assert term is not None and term != phase_contrast, written
    assert _one("phase-contrast") == phase_contrast


def test_plasma_membrane_is_no_blood_product() -> None:
    term = _one("plasma membrane")
    assert term is not None and term != _term("plasma")


def test_calcium_alone_is_no_calcium_imaging() -> None:
    calcium_imaging = _term("calcium imaging")
    for text in ("calcium channel", "kelt calcium zeph", "calcium-bound vorn", "Ca2+ channel"):
        assert calcium_imaging not in _terms(text), text
    # ``GCaMP`` written so is divided by the reader at its capitals (``gca``, ``mp``) before the
    # Dictionary is asked, a reader's matter; written in one case, or with its version, it binds.
    for written in ("calcium imaging", "Ca-imaging", "gcamp", "GCaMP6f", "jGCaMP8", "Fluo-4"):
        assert _one(written) == calcium_imaging, written


def test_x_ray_methods_are_no_clinical_imaging() -> None:
    clinical = _term("radiograph")
    assert _one("X-ray crystallography") == _term("crystallography")
    for written in ("X-ray tomography", "micro-CT", "microCT", "cryo-soft X-ray tomography"):
        term = _one(written)
        assert term is not None and term != clinical, written
    # Clinical radiography keeps its own spellings.
    assert _one("x-ray") == clinical


def test_single_cell_and_single_molecule_alone_are_no_method() -> None:
    single_cell_rna = _term("scrnaseq")
    localisation = _term("smlm")
    for text in ("single cell", "single-cell kelt", "single molecule", "single-molecule vorn"):
        assert not _terms(text) & {single_cell_rna, localisation}, text
    assert _one("single-cell RNA sequencing") == single_cell_rna
    assert _one("single-cell RNA-seq") == single_cell_rna
    assert _one("single-molecule localization microscopy") == localisation
    assert _one("single-molecule localisation") == localisation


def test_frames_alone_are_no_time_series() -> None:
    time_series = "time_series"  # the microscopy pack's axis (the core's ``timeseries`` differs)
    assert STORE.sense_for_term(time_series) is not None
    for text in ("movie frames", "kelt_frames", "frame_kelt"):
        assert time_series not in _terms(text), text


# -- 4. a microscopy written out is its method


def test_a_microscopy_written_out_binds_its_method() -> None:
    electron = _term("fibsem")
    for written in (
        "electron microscopy",
        "scanning electron microscopy",
        "electron tomography",
    ):
        assert _one(written) == electron, written
    assert _one("fluorescence microscopy") == _term("epifluorescence")
    assert _one("light microscopy") is not None


# -- 5. model organisms beyond the nine, never split

#: Each organism's binomial and how a text writes it: abbreviated, by one of its words, by its
#: common name.
ORGANISMS = {
    "bacillus subtilis": ("Bacillus subtilis", "B. subtilis", "B_subtilis", "subtilis"),
    "plasmodium falciparum": (
        "Plasmodium falciparum",
        "P. falciparum",
        "Pfalciparum",
        "falciparum",
    ),
    "xenopus laevis": ("Xenopus laevis", "X. laevis", "laevis", "African clawed frog"),
    "xenopus tropicalis": ("Xenopus tropicalis", "X. tropicalis", "tropicalis"),
    "rattus norvegicus": ("Rattus norvegicus", "R. norvegicus", "rat", "rats"),
    "dictyostelium discoideum": ("Dictyostelium discoideum", "D. discoideum", "dictyostelium"),
    "chlamydomonas reinhardtii": ("Chlamydomonas reinhardtii", "C. reinhardtii", "chlamydomonas"),
    "tetrahymena thermophila": ("Tetrahymena thermophila", "T. thermophila", "tetrahymena"),
    "neurospora crassa": ("Neurospora crassa", "N. crassa", "neurospora"),
    "caulobacter crescentus": ("Caulobacter crescentus", "C. crescentus", "caulobacter"),
    "gallus gallus": ("Gallus gallus", "G. gallus", "chicken"),
    "toxoplasma gondii": ("Toxoplasma gondii", "T. gondii", "toxoplasma"),
    "trypanosoma brucei": ("Trypanosoma brucei", "T. brucei", "brucei"),
    "nicotiana benthamiana": ("Nicotiana benthamiana", "N. benthamiana", "benthamiana"),
    "zea mays": ("Zea mays", "Z. mays", "maize"),
    "oryzias latipes": ("Oryzias latipes", "O. latipes", "medaka"),
    "schmidtea mediterranea": ("Schmidtea mediterranea", "S. mediterranea", "schmidtea"),
    "staphylococcus aureus": ("Staphylococcus aureus", "S. aureus", "S_aureus", "aureus"),
    "mycobacterium tuberculosis": (
        "Mycobacterium tuberculosis",
        "M. tuberculosis",
        "Mtuberculosis",
    ),
    "bos taurus": ("Bos taurus", "B. taurus", "taurus", "cattle"),
    "helicobacter pylori": ("Helicobacter pylori", "H. pylori", "Hpylori", "pylori"),
    "triticum aestivum": ("Triticum aestivum", "T. aestivum", "aestivum", "wheat"),
}


@pytest.mark.parametrize(("binomial", "spellings"), sorted(ORGANISMS.items()))
def test_a_model_organism_is_one_sense_however_written(
    binomial: str, spellings: tuple[str, ...]
) -> None:
    expected = _term(binomial)
    assert _canonical(expected) == binomial.capitalize()
    for written in spellings:
        assert _one(written) == expected, written
        assert _read(f"kelt {written} zeph")[1] == (_read(written)[0][0], expected), written


def test_the_added_organisms_are_each_their_own_sense() -> None:
    assert len({_term(binomial) for binomial in ORGANISMS}) == len(ORGANISMS)


# -- 6. a pack's specialised sense of a generic word is not read everywhere


def test_a_generic_word_and_its_plural_are_no_protocol_step_outside_its_phrases() -> None:
    """``segment`` is an ordinary noun of a tracking or an imaging file's name (a track's segments,
    a segmented outline's); the electrophysiology pack's protocol step keeps its own spellings and
    its phrases, never the bare word or the plural tokeniser 13 folds onto it."""
    protocol_step = _term("episode")
    for name in ("zeph_track_segments.csv", "kelt_segment_04.csv", "vorn_segments"):
        assert protocol_step not in _terms(name), name
    for written in ("sweep segment", "protocol_segment", "sweep-segments", "sweeps", "episode"):
        assert _one(written) == protocol_step, written


# -- 7. a spelling taken from one sense falls to no other ------------------------------------------


def _sequencing_terms(text: str) -> set[str]:
    found = set()
    for term in _terms(text):
        sense = STORE.sense_for_term(term) if term else None
        if sense is not None and sense.domain == "sequencing":
            found.add(term)
    return found


def test_single_alone_binds_no_sequencing_sense() -> None:
    """``single`` beside ``cell`` or ``molecule`` is no single-end read: the read structure keeps
    its own spellings (``single-end``, ``singleend``, ``se``)."""
    for name in (
        "kelt_single_cell_track.csv",
        "brisa_single_molecule_02.tif",
        "zeph_singlecell_02.tif",
    ):
        assert not _sequencing_terms(name), name
    single_end = _term("single-end")
    for written in ("single-end", "singleend", "single_end"):
        assert _one(written) == single_end, written


def test_a_frame_or_a_segment_with_its_counter_is_a_word_and_a_number_and_no_method() -> None:
    """``frame`` and ``segment`` are plain words of their own, so a counter glued to one splits off
    as after any word the Dictionary knows, and neither binds a time series or a protocol step."""
    from ingence.dictionary.normalize import AtomKind

    methods = {"time_series", _term("episode")}
    for name, word in (("zeph_frame0001.tif", "frame"), ("kelt_segment3.abf", "segment")):
        atoms = [(atom.normalised, atom.kind) for atom in READER.atoms(name)]
        assert (word, AtomKind.WORD) in atoms, (name, atoms)
        assert any(kind is AtomKind.NUMBER for _, kind in atoms), (name, atoms)
        assert not _terms(name) & methods, name
    for name in ("vorn_frames_02.tif", "kelt_segments_track.csv"):
        assert not _terms(name) & methods, name


def test_x_ray_tomography_written_short_is_no_clinical_imaging() -> None:
    tomography = _term("x-ray tomography")
    for name in ("kelt_Xray_tomo.tif", "brisa_Xray_tomogram_03.tif"):
        assert _terms(name) & {tomography, _term("radiograph")} == {tomography}, name
    # "X-ray CT" is written alike for a specimen and a patient: the two packs tie, nothing binds.
    assert not _terms("kelt_xray_CT_02.tif") & {tomography, _term("radiograph")}


# -- 8. the cryo spellings every text writes, and TEM its own sense

#: Each spelling and the canonical name of the sense it binds.
CRYO_SPELLINGS = {
    "cryogenic electron microscopy": "CryoEM",
    "cryogenic-EM": "CryoEM",
    "cryo-electron microscope": "CryoEM",
    "cryoelectron microscopy": "CryoEM",
    "cryo-TEM": "CryoEM",
    "cryoTEM": "CryoEM",
    "cryo-transmission electron microscopy": "CryoEM",
    "cryomicrographs": "CryoEM",
    "cryo-SEM": "CryoEM",
    "cryoSEM": "CryoEM",
    "cryo-electron tomographic": "CryoET",
    "cryo-transmission electron tomograms": "CryoET",
    "cryo serial FIB": "CryoFIB",
    "cryo-FIB-SEM serial": "CryoFIB",
    "cryo-PFIB": "CryoFIB",
    "cryo-plasma FIB": "CryoFIB",
    "cryo soft X-ray": "X-ray tomography",
    "cryo-soft-X-ray": "X-ray tomography",
}


@pytest.mark.parametrize(("written", "name"), sorted(CRYO_SPELLINGS.items()))
def test_each_cryo_spelling_binds_its_cryo_sense_whole(written: str, name: str) -> None:
    assert _canonical(_one(written)) == name, written
    assert _canonical(_read(f"kelt_{written}_02")[1][1]) == name, written


def test_transmission_electron_microscopy_is_its_own_sense_under_electron_microscopy() -> None:
    transmission = _one("transmission electron microscopy")
    assert transmission is not None
    assert _canonical(transmission) == "transmission electron microscopy"
    for written in ("TEM", "tem", "transmission electron microscope", "transmission-EM"):
        assert _one(written) == transmission, written
    others = {_one(written) for written in ("electron microscopy", "scanning electron microscopy")}
    assert others == {_term("fibsem")} and transmission not in others
    assert transmission not in {_term("cryoem"), _term("cryo-tem")}


# -- 9. a bare "single" says how many, never what


def test_single_alone_is_a_word_no_theme_reads() -> None:
    """``single`` before a word it does not form a phrase with (``single molecule``, ``single
    cell``) names no subject: it is known, bound to a kind the theme labels leave out, and binds
    no method."""
    from ingence.core.settings import ThemeSettings

    how = set(ThemeSettings().how_kinds)
    for name in ("kelt_single_molecule_02", "vorn single zeph", "single"):
        found = {term for written, term in _read(name) if written == "single"}
        assert len(found) == 1 and None not in found, (name, found)
        sense = STORE.sense_for_term(next(iter(found)))
        assert sense is not None and sense.theme_kind in how, (name, sense)
    assert _one("single particle") == _term("single particle")
    # Grouping still reads it: no connecting word, which grouping leaves out.
    assert STORE.sense_for_term(_one("single")).theme_kind != "connective"


def test_cryogenic_electron_tomography_written_out_is_cryo_et() -> None:
    """``cryogenic electron tomography`` (and its tomograms) is not shown in pieces in a label."""
    tomography = _term("cryo-electron tomography")
    for written in (
        "cryogenic electron tomography",
        "cryogenic electron tomogram",
        "cryogenic electron tomograms",
        "cryogenic electron tomographs",
    ):
        assert _one(written) == tomography, written
