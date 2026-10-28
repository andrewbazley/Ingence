"""The Dictionary: what a string denotes, and what it refuses to claim."""

from __future__ import annotations

import pytest

from ingence.core.ids import PROJECT_SCOPE, Scope, ScopeKind, dictionary_term_id
from ingence.core.schema import DictionaryOrigin
from ingence.dictionary.normalize import (
    _UNIT_JOINERS,
    NORMALIZER_VERSION,
    AtomKind,
    Normaliser,
    split_words,
)
from ingence.dictionary.store import (
    DictionaryStore,
    MatchQuality,
    Section,
    Sense,
    Support,
)

STORE = DictionaryStore.builtin()
NORMALISER = Normaliser(STORE)


# ---------------------------------------------------------------------------
# What ships.
# ---------------------------------------------------------------------------


def test_the_shipped_dictionary_is_substantial():
    """Exhaustiveness is free when no entry licenses a conclusion alone.

    A dictionary is only a burden when a wrong entry causes a wrong inference
    somebody must then hunt down. Since an entry only contributes corroborated
    evidence, an irrelevant one never fires and is never seen.
    """
    assert len(STORE) > 500
    assert len(STORE.sections) >= 15


def test_universal_sections_carry_no_domain():
    """The sections that work on an unfamiliar corpus are the undomained ones."""
    universal = {section.name for section in STORE.sections if section.domain is None}
    assert {"units", "process", "statistics", "negative_lexicon"} <= universal


def test_domain_sections_declare_their_field():
    domains = {section.domain for section in STORE.sections if section.domain}
    assert {"microscopy", "sequencing", "biochemistry", "clinical"} <= domains


def test_nothing_shipped_has_an_undeclared_collision():
    """Two senses of one kind sharing a spelling is one idea entered twice.

    Where a collision is real it is declared in the section file, which turns it
    from a mistake into documentation. This is the audit that keeps the shipped
    vocabulary honest as it grows.
    """
    assert STORE.undeclared_ambiguities() == ()


def test_bar_is_the_pressure_unit_alone():
    """``bar`` is not a keyboard-mash surface that ties a unit with a doubt term."""
    hit = STORE.look_up("bar")
    assert hit is not None
    assert (hit.sense.term, hit.sense.theme_kind, hit.alternatives) == ("bar", "unit", ())


def test_the_three_least_trustworthy_doubt_terms_are_low_strength():
    """The three least trustworthy doubt terms are low strength.

    Each counts only beside another doubt term, or asks a person."""
    low = {
        sense.term
        for sense in STORE.senses()
        if sense.theme_kind == "quality" and sense.properties.get("strength") == "low"
    }
    assert low == {"keyboard_mash", "questionable", "quarantine"}


def test_declared_ambiguities_are_reported_rather_than_resolved():
    """A bare "sim" is a simulation or structured illumination. Nothing decides."""
    hit = STORE.look_up("sim")
    assert hit is not None
    assert hit.ambiguous
    assert {sense.term for sense in hit.alternatives} == {"structured_illumination"}


# ---------------------------------------------------------------------------
# Case, which matters more here than almost anywhere.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "term"),
    [
        ("nM", "nanomolar"),
        ("nm", "nanometre"),
        ("mM", "millimolar"),
        ("mm", "millimetre"),
        ("M", "molar"),
        ("m", "metre"),
        ("S", "siemens"),
        ("s", "second"),
    ],
)
def test_case_sensitive_unit_symbols_are_not_folded_together(token, term):
    """nM and nm differ by nine orders of magnitude, and silently so."""
    hit = STORE.look_up(token)
    assert hit is not None
    assert hit.sense.term == term
    assert hit.quality is MatchQuality.EXACT


def test_words_still_match_regardless_of_case():
    hit = STORE.look_up("CONFOCAL")
    assert hit is not None
    assert hit.sense.term == "confocal"
    assert hit.quality is MatchQuality.CASE_FOLDED


def test_punctuation_is_stripped_only_as_a_last_resort():
    hit = STORE.look_up("time.lapse")
    assert hit is not None
    assert hit.quality is MatchQuality.LOOSE


# ---------------------------------------------------------------------------
# Compounding.
# ---------------------------------------------------------------------------


def test_one_hit_is_weak_and_never_more():
    """Enough to propose, never enough to assert."""
    assert STORE.resolve(["western"]).support is Support.WEAK


def test_nothing_recognised_is_none_not_weak():
    assert STORE.resolve(["qqqqzzz"]).support is Support.NONE


def test_hits_compound_across_distinct_senses():
    """Three independent facts about a file are three pieces of evidence."""
    resolution = STORE.resolve(["confocal", "dapi", "nM"])
    assert resolution.support is Support.STRONG
    assert len(resolution.distinct_kinds) == 3


def test_repeating_one_sense_compounds_nothing():
    """The same fact seen three times is one fact.

    Without this, the most verbose filename in a project always wins, which is
    the opposite of what anybody wants.
    """
    once = STORE.resolve(["final"])
    thrice = STORE.resolve(["final", "FINAL", "final"])
    assert once.support is thrice.support is Support.WEAK


def test_a_few_hits_beat_none():
    """Relegating two recognised terms to the standing of none throws away evidence."""
    levels = [
        STORE.resolve([]).support,
        STORE.resolve(["western"]).support,
        STORE.resolve(["western", "nM"]).support,
        STORE.resolve(["western", "nM", "rep"]).support,
    ]
    assert levels == [Support.NONE, Support.WEAK, Support.MODERATE, Support.STRONG]


def test_an_ambiguous_hit_counts_for_less():
    """It is evidence of something, and the engine cannot say what."""
    clear = STORE.resolve(["confocal"])
    murky = STORE.resolve(["sim"])
    assert murky.tiebreak < clear.tiebreak


# ---------------------------------------------------------------------------
# Precedence.
# ---------------------------------------------------------------------------


def test_a_user_sense_shadows_a_shipped_one_without_destroying_it():
    """The correction people actually want to make."""
    store = DictionaryStore.builtin()
    store.add(
        Section(
            name="local",
            summary="",
            origin=DictionaryOrigin.USER,
            senses=(
                Sense(
                    term="house_style_confocal",
                    canonical="house style",
                    theme_kind="technique",
                    section="local",
                    surfaces=("confocal",),
                    origin=DictionaryOrigin.USER,
                ),
            ),
        )
    )
    hit = store.look_up("confocal")
    assert hit is not None
    assert hit.sense.term == "house_style_confocal"
    # The shipped sense is shadowed, not deleted.
    assert any(sense.term == "confocal" for sense in DictionaryStore.builtin().senses())


def _section(name: str, *, origin: DictionaryOrigin, term: str, surface: str, kind: str) -> Section:
    """One section holding one sense, for testing what beats what."""
    return Section(
        name=name,
        summary="",
        origin=origin,
        senses=(
            Sense(
                term=term,
                canonical=term,
                theme_kind=kind,
                section=name,
                surfaces=(surface,),
                origin=origin,
            ),
        ),
    )


def _contested(*sections: Section) -> DictionaryStore:
    store = DictionaryStore()
    for section in sections:
        store.add(section)
    return store


def test_a_pack_does_not_outrank_the_core_and_the_collision_is_reported() -> None:
    """Precedence is a winner and not a tiebreak.

    Above the core, a pack decides a collision silently and shadows what shipped. Level with the
    core, the collision ties, resolves to neither and says so. That leaves a person something to
    settle."""
    store = _contested(
        _section(
            "units", origin=DictionaryOrigin.BUILTIN, term="ms_core", surface="zq", kind="unit"
        ),
        _section(
            "spectrometry",
            origin=DictionaryOrigin.PACK,
            term="ms_pack",
            surface="zq",
            kind="technique",
        ),
    )
    hit = store.look_up("zq")
    assert hit is not None
    assert hit.ambiguous
    assert {hit.sense.term, *(other.term for other in hit.alternatives)} == {"ms_core", "ms_pack"}
    assert hit.shadowed == ()


def test_the_collision_a_pack_would_have_decided_is_the_ms_case_itself() -> None:
    """Installing a spectrometry pack must not make ``ms`` mass spectrometry silently.

    Such a silent change is the failure that precedence is meant to cure, so this is a tripwire."""
    store = _contested(
        _section(
            "units", origin=DictionaryOrigin.BUILTIN, term="millisecond", surface="ms", kind="unit"
        ),
        _section(
            "spectrometry",
            origin=DictionaryOrigin.PACK,
            term="mass_spectrometry",
            surface="ms",
            kind="technique",
        ),
    )
    hit = store.look_up("ms")
    assert hit is not None and hit.ambiguous
    assert {hit.sense.term, *(other.term for other in hit.alternatives)} == {
        "millisecond",
        "mass_spectrometry",
    }


def test_a_user_sense_beats_both_the_core_and_a_pack_and_shadows_neither_away() -> None:
    """A pack is not a decision, it is more vocabulary. A person is a decision, and it is the
    one thing that settles a tie rather than adding to it."""
    store = _contested(
        _section(
            "units", origin=DictionaryOrigin.BUILTIN, term="ms_core", surface="zq", kind="unit"
        ),
        _section(
            "spectrometry",
            origin=DictionaryOrigin.PACK,
            term="ms_pack",
            surface="zq",
            kind="technique",
        ),
        _section(
            "local", origin=DictionaryOrigin.USER, term="ms_here", surface="zq", kind="technique"
        ),
    )
    hit = store.look_up("zq")
    assert hit is not None
    assert hit.sense.term == "ms_here"
    assert not hit.ambiguous
    assert {sense.term for sense in hit.shadowed} == {"ms_core", "ms_pack"}


def test_a_pack_still_adds_vocabulary_the_core_does_not_have() -> None:
    """Level precedence changes what happens where they disagree, and nothing else. A pack
    exists to say what the core cannot, and that is untouched."""
    store = _contested(
        _section(
            "units", origin=DictionaryOrigin.BUILTIN, term="ms_core", surface="zq", kind="unit"
        ),
        _section(
            "spectrometry",
            origin=DictionaryOrigin.PACK,
            term="only_here",
            surface="zzx",
            kind="technique",
        ),
    )
    hit = store.look_up("zzx")
    assert hit is not None
    assert (hit.sense.term, hit.ambiguous) == ("only_here", False)


def test_the_precedence_table_ranks_a_person_above_everything_and_the_rest_level() -> None:
    """The precedence table itself, because what it decides is numbers.

    A person's answer is the only thing that wins. The core, a pack and a discovered proposal
    stand level, so a collision among any of them is reported as a tie rather than won by
    whichever was loaded with the larger number.
    """
    from ingence.dictionary.store import ORIGIN_PRECEDENCE

    assert ORIGIN_PRECEDENCE[DictionaryOrigin.USER] == 40
    level = {
        ORIGIN_PRECEDENCE[origin]
        for origin in (
            DictionaryOrigin.BUILTIN,
            DictionaryOrigin.PACK,
            DictionaryOrigin.DISCOVERED,
        )
    }
    assert level == {ORIGIN_PRECEDENCE[DictionaryOrigin.BUILTIN]}
    assert ORIGIN_PRECEDENCE[DictionaryOrigin.USER] > max(level)


def test_a_discovered_proposal_colliding_with_the_core_is_a_tie() -> None:
    """A proposal induced from one corpus does not outrank vocabulary somebody published."""
    store = _contested(
        _section(
            "file_roles",
            origin=DictionaryOrigin.BUILTIN,
            term="shipped_sense",
            surface="zzq",
            kind="file_role",
        ),
        _section(
            "induced",
            origin=DictionaryOrigin.DISCOVERED,
            term="induced_sense",
            surface="zzq",
            kind="file_role",
        ),
    )
    hit = store.look_up("zzq")
    assert hit is not None
    assert hit.ambiguous
    assert hit.shadowed == ()


def test_senses_convert_to_the_persisted_record_type():
    records = STORE.records()
    assert len(records) == len(STORE)
    assert all(record.term_id.startswith("term_") for record in records)


# ---------------------------------------------------------------------------
# Morphology.
# ---------------------------------------------------------------------------


def test_a_dense_name_splits_into_atoms_rather_than_shattering():
    """The case the whole layer exists for, wherever the name separates its parts.

    Glued, it is one word. A run of letters and digits that a person wrote without a separator
    is read and asked about whole. The split reading is kept under the ``split`` setting, and
    this test pins it.
    """
    for name, normaliser in (
        ("SubX_800ngmL_2.5h", NORMALISER),
        ("SubX800ngmL2.5h", Normaliser(STORE, glued_runs="split")),
    ):
        atoms = normaliser.atoms(name)
        quantities = [atom for atom in atoms if atom.kind is AtomKind.QUANTITY]
        assert [atom.unit for atom in quantities] == ["nanogram_per_millilitre", "hour"], name
        assert quantities[0].magnitude == 800.0
        assert quantities[1].magnitude == 2.5
    (whole,) = NORMALISER.atoms("SubX800ngmL2.5h")
    assert (whole.kind, whole.normalised) == (AtomKind.WORD, "subx800ngml2.5h")


def test_the_longest_unit_wins_but_exact_case_wins_a_tie():
    """Both halves matter and they pull against each other."""
    assert NORMALISER.quantities("800ngmL")[0].unit == "nanogram_per_millilitre"
    assert NORMALISER.quantities("500nM")[0].unit == "nanomolar"
    assert NORMALISER.quantities("500nm")[0].unit == "nanometre"


def test_a_one_character_unit_symbol_is_never_reached_by_folding():
    """Words fold freely and symbols must not: the minimal case of the class.

    Folded, the ampere symbol is the letter a and the farad symbol is the letter f, so every
    digit followed by one letter would become a quantity: a course code read as farads, a
    section label as degrees, a hexadecimal fragment as a current. One identifier alone could
    produce six spurious quantities.

    The test is written over the grammar rather than over particular names. The class is any
    one-character symbol, and the boundary is length rather than which symbols happen to be in
    the table.
    """
    assert NORMALISER.quantities("6A")[0].unit == "ampere"
    assert not NORMALISER.quantities("6a")
    assert not NORMALISER.quantities("21f")
    assert not NORMALISER.quantities("72b")


def test_a_longer_unit_surface_still_folds():
    """The other half, and the reason the refusal is bounded by length.

    A compound surface differing only in case is a real spelling of a real unit,
    and refusing it would read a mass where a mass concentration was written.
    Folding earns its place everywhere except at one character, where there is
    nothing left of the symbol to distinguish it from an ordinary letter.
    """
    assert NORMALISER.quantities("800NGML")[0].unit == "nanogram_per_millilitre"


def test_a_magnitude_that_is_not_finite_is_not_a_quantity():
    """An infinite magnitude is a value nothing downstream can compare.

    A run of digits carrying what looks like an exponent overflows, and the
    engine stored the overflow in the column every grouping rule reads. Refused
    at the one place a quantity is built, and the digits fall back to being read
    as the number they are.
    """
    atoms = NORMALISER.atoms("1e400d")
    assert not [atom for atom in atoms if atom.kind is AtomKind.QUANTITY]
    assert [atom for atom in atoms if atom.kind is AtomKind.NUMBER]


def test_two_spellings_of_one_quantity_normalise_identically():
    """Nothing downstream can group these until something here says so."""
    assert (
        NORMALISER.quantities("500nM")[0].normalised == NORMALISER.quantities("0.5uM")[0].normalised
    )


def test_temperature_applies_its_offset_after_scaling():
    """The one dimension that is not a simple multiple."""
    assert NORMALISER.quantities("37C")[0].base_value == pytest.approx(310.15)
    assert NORMALISER.quantities("98.6degF")[0].base_value == pytest.approx(310.15, abs=0.1)


def test_a_case_boundary_divides_two_words_and_a_short_piece_stays_with_its_neighbour():
    """A capital inside a word marks a boundary where it joins two words.

    The capital is part of the word where one side is a letter or two. So 'Sub' and 'AB' are
    one term, and so are 'He' and 'La'. ``SubXTreated`` does not read as three pieces with
    ``X`` alone.

    The test is written over the grammar of case-cut pieces, with invented runs."""
    assert split_words("brisaQUOLT") == ["brisa", "QUOLT"]
    assert split_words("QUOLTBrisa") == ["QUOLT", "Brisa"]
    assert split_words("ZoKa") == ["ZoKa"]
    assert split_words("PelQR") == ["PelQR"]
    assert split_words("mZorb") == ["mZorb"]
    assert split_words("VornXKelt") == ["VornX", "Kelt"]
    assert split_words("SubXTreated") == ["SubX", "Treated"]
    assert split_words("RNASeq") == ["RNA", "Seq"]
    assert split_words("plain") == ["plain"]
    # The shortest piece is a parameter, never a constant buried in the function.
    assert split_words("ZoKa", minimum=2) == ["Zo", "Ka"]


def test_a_label_with_a_trailing_number_stays_one_atom():
    """Well A1 split apart loses the pairing, and the pairing is the content."""
    atoms = {atom.text: atom for atom in NORMALISER.atoms("plate1_A1_pos03")}
    assert atoms["A1"].kind is AtomKind.INDEXED
    assert atoms["A1"].label == "a"
    assert atoms["A1"].index == 1


@pytest.mark.parametrize("stem", ["summary_N=3", "summary_n=3", "summary_(N=3)", "summary_N3"])
def test_an_equals_sign_joins_a_label_to_its_number(stem):
    """A person stating a count writes ``N=3``, and split at the sign it is a lone letter beside a
    lone number. All four spellings reach the one atom ``N3``."""
    indexed = [atom for atom in NORMALISER.atoms(stem) if atom.kind is AtomKind.INDEXED]
    assert len(indexed) == 1
    assert (indexed[0].label, indexed[0].index) == ("n", 3)
    assert indexed[0].normalised == "n3"


def test_an_underscore_still_separates_a_label_from_its_number():
    """``_`` is a separator, removed before a chunk reaches the pattern.

    Joining across one would make ``rep_2`` and ``plate_1`` indexed atoms too, which would change
    how every name is cut."""
    for stem in ("summary_n_3", "kelt_rep_2", "kelt_plate_1"):
        assert [atom for atom in NORMALISER.atoms(stem) if atom.kind is AtomKind.INDEXED] == []


def test_the_equals_sign_joins_only_a_whole_chunk():
    """The pattern is anchored at both ends, so a decimal count and two joins in one run
    are left alone rather than half-read."""
    for stem in ("ph=7.4_buffer", "a=1b=2"):
        assert [atom for atom in NORMALISER.atoms(stem) if atom.kind is AtomKind.INDEXED] == []


def test_the_normaliser_version_moved_with_the_joiner_and_again_with_identifiers():
    """Tokenisation produces different output for unchanged input, which is what the version
    exists to say.

    A component carries the version in its identity, so a re-scan produces new components
    rather than silently rewriting old ones."""
    assert NORMALIZER_VERSION == "14"  # 14 since 0.4.0


def _invented_compound_store() -> DictionaryStore:
    return DictionaryStore(
        (
            Section(
                name="invented",
                summary="",
                senses=(
                    Sense(
                        term="vorn_kelt_method",
                        canonical="vorn kelt",
                        theme_kind="technique",
                        section="invented",
                        surfaces=("vorn-kelt",),
                    ),
                ),
            ),
        )
    )


def test_a_term_the_dictionary_spells_in_parts_is_one_word_however_a_name_joins_them():
    """A surface spelled with an underscore, a hyphen or a space can be reached from a name.

    The tokeniser divides at exactly those characters, so such a surface would be unreachable
    otherwise. The shipped Dictionary holds some four hundred and fifty. Underscores read as
    spaces, and a hyphen joins before it divides."""
    normaliser = Normaliser(_invented_compound_store())
    for name in ("tamen_vorn_kelt_3", "tamen_Vorn-Kelt_3", "tamen_VornKelt_3", "tamen vorn kelt 3"):
        words = [atom.normalised for atom in normaliser.atoms(name) if atom.kind is AtomKind.WORD]
        assert words == ["tamen", "vorn kelt"], name
    # Parts inside a longer word stay apart. A run of joiners is one joiner, so
    # ``tamen_vorn__kelt`` is the term.
    words = [
        atom.normalised
        for atom in normaliser.atoms("tamen_vornkeltish")
        if atom.kind is AtomKind.WORD
    ]
    assert "vorn kelt" not in words
    doubled = [
        a.normalised for a in normaliser.atoms("tamen_vorn__kelt") if a.kind is AtomKind.WORD
    ]
    assert doubled == ["tamen", "vorn kelt"]


def test_a_shipped_term_in_parts_is_reached_from_a_name():
    atoms = NORMALISER.atoms("plate3_western_blot_2")
    words = [atom for atom in atoms if atom.kind is AtomKind.WORD]
    assert [(atom.text, atom.normalised) for atom in words] == [
        ("plate", "plate"),
        ("western_blot", "western blot"),
    ]


# ---------------------------------------------------------------------------
# Identifiers, and the one-character unit that ends its chunk.
#
# Each case is written over its identifier's own grammar with invented values: a conda build
# string, a hexadecimal digest, RFC 4122's layout, the DOI system's reserved example prefix
# 10.5555, a publisher item identifier's length, and a restriction enzyme's naming.
# ---------------------------------------------------------------------------


def _kinds(name: str) -> list[tuple[str, str]]:
    return [(atom.kind.value, atom.text) for atom in NORMALISER.atoms(name)]


def test_a_build_hash_is_one_identifier_and_what_precedes_it_reads_as_before():
    assert _kinds("py311hb7e04d2") == [("indexed", "py311"), ("identifier", "hb7e04d2")]
    assert _kinds("vorn_h9c1a4f0") == [("word", "vorn"), ("identifier", "h9c1a4f0")]
    # Seven hexadecimal digits of one kind after an h are not a digest.
    assert ("identifier", "habcdefa") not in _kinds("habcdefa")


def test_a_hexadecimal_digest_is_one_identifier_and_a_word_or_a_counter_is_not():
    assert _kinds("e3b0c44298fc1c14") == [("identifier", "e3b0c44298fc1c14")]
    assert _kinds("4f2a9c7") == [("identifier", "4f2a9c7")]
    # Two blocks alternate once: a word with a number, or a number with a suffix.
    for name in ("decade1", "cafe1234", "1234abcd", "abcdefab", "20230415"):
        assert all(kind != "identifier" for kind, _ in _kinds(name)), name


def test_a_uuid_is_one_identifier_claimed_before_a_date_can_take_its_first_group():
    """Its first group can be eight decimal digits, which is what a compact date looks like."""
    kinds = _kinds("20231015-9d8e-4f7a-b6c5-1a2b3c4d5e6f_scan")
    assert kinds == [("identifier", "20231015-9d8e-4f7a-b6c5-1a2b3c4d5e6f"), ("word", "scan")]


def test_a_doi_prefix_is_an_identifier_and_not_a_decimal():
    kinds = _kinds("10.5555_vorn-2019")
    assert kinds[0] == ("identifier", "10.5555")
    assert ("number", "10.5555") not in kinds
    # A decimal that is not followed by a suffix's first character stays a number.
    assert _kinds("ratio_10.5555") == [("word", "ratio"), ("number", "10.5555")]


def test_an_accession_or_an_article_identifier_is_one_identifier():
    for name in ("S0123456789012345", "S01234567X8901234", "AB12345678", "Q1234567X"):
        assert _kinds(name) == [("identifier", name)], name
    # A counter anybody typed is shorter, and three check characters are not a checksum.
    for name in ("IMG00012", "DSCN1234567", "AB1234XXX5"):
        assert all(kind != "identifier" for kind, _ in _kinds(name)), name


def test_a_plasmid_digested_by_an_enzyme_is_one_identifier():
    for name in ("pZQ220xBamHI", "118xHindIII", "pVL42xEcoRI", "pVL42xBglII"):
        assert _kinds(name) == [("identifier", name)], name
    # An objective's magnification is not an enzyme.
    assert ("identifier", "20xObjective") not in _kinds("20xObjective")


def test_a_one_character_unit_makes_a_quantity_only_where_it_ends_its_chunk():
    """``20x`` and ``2.5h`` were written as measures; the ``d`` in ``12d7`` and the ``x`` in
    ``3x3`` sit inside a longer run, and reading them as days or fold is what made fragments
    into questions. A longer symbol stands where it is."""
    split = Normaliser(STORE, glued_runs="split")
    assert NORMALISER.quantities("20x")[0].unit in ("fold", "magnification")
    assert NORMALISER.quantities("2.5h")[0].unit == "hour"
    assert split.quantities("tamen2h")[0].unit == "hour"
    for name in ("3x3", "k12d7", "40xZeta", "vorn5sQ"):
        assert NORMALISER.quantities(name) == (), name
        assert split.quantities(name) == (), name
    assert [atom.unit for atom in split.quantities("vorn500nm2hr")] == ["nanometre", "hour"]
    # A glued run beginning with a letter is one word.
    assert NORMALISER.words("tamen2h") == ("tamen2h",)
    assert NORMALISER.words("vorn500nm2hr") == ("vorn500nm2hr",)


# ---------------------------------------------------------------------------
# Dates.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["2026-01-04_western", "20260104_western", "exp_2026.01.04_final", "2026_01_04"],
)
def test_a_date_survives_the_separators_inside_it(name):
    """Split on its hyphens, a date is three unrelated numbers."""
    dates = [atom for atom in NORMALISER.atoms(name) if atom.kind is AtomKind.DATE]
    assert len(dates) == 1
    assert dates[0].normalised == "2026-01-04"


def test_an_unresolvable_date_reports_both_readings():
    """Guessing here would be wrong for eleven months of every twelve."""
    atom = next(item for item in NORMALISER.atoms("01/02/2026") if item.kind is AtomKind.DATE)
    assert atom.ambiguous
    assert set(atom.alternatives) == {"2026-01-02", "2026-02-01"}
    assert atom.normalised == "2026-01-02 or 2026-02-01"  # no value chosen (tokeniser 10)


def test_a_date_that_can_only_be_read_one_way_is_not_ambiguous():
    atom = next(item for item in NORMALISER.atoms("13/02/2026") if item.kind is AtomKind.DATE)
    assert not atom.ambiguous
    assert atom.normalised == "2026-02-13"


def test_a_version_marker_is_one_atom():
    atoms = {atom.kind: atom for atom in NORMALISER.atoms("results_v3")}
    assert atoms[AtomKind.VERSION].normalised == "v3"
    assert atoms[AtomKind.VERSION].index == 3


def test_a_declared_unit_ambiguity_reaches_the_atom():
    """ "63x" is sixty-three fold or sixty-three times magnification."""
    atom = NORMALISER.quantities("63x_objective")[0]
    assert atom.ambiguous
    assert atom.alternatives == ("magnification",)


def test_two_names_describing_one_condition_normalise_to_the_same_quantities():
    """The payoff. Neither name shares a character with the other's numbers.

    Word atoms legitimately differ: ``SubX`` splits at its camel-case boundary
    and ``subx`` does not, because a case boundary is real evidence about how
    somebody wrote a name. It is the quantities that must agree.
    """
    first = [atom.normalised for atom in NORMALISER.quantities("SubX_500nM_24h")]
    second = [atom.normalised for atom in NORMALISER.quantities("subx-0.5uM-1440min")]
    assert first == second == ["5e-07 molar", "86400 second"]


# ---------------------------------------------------------------------------
# The bucketed matcher, and the obligation that makes it safe.
# ---------------------------------------------------------------------------

#: Every documented adversarial case, plus the ones a corpus assembled by hand
#: does not think to include. The micro sign is the example of the second kind:
#: ``"µ".casefold()`` is ``"μ"``, a different codepoint, and
#: ``units.yaml`` carries both spellings for most units but not for
#: ``microgram_per_microlitre``. A corpus without it passes green while a
#: bucketing change silently loses every fold match on that unit.
ADVERSARIAL = (
    "nM",
    "nm",
    "mM",
    "mm",
    "800ngmL",
    "500ngmL",
    "NGML",
    "63x",
    "A1",
    "500ML",
    "500mL",
    "500Ml",
    "2026-01-04_",
    "2026-01-04_plate",
    "µM",
    "μM",
    "µg/µL",
    "μg/μL",
    "500µg/µL",
    "500μg/μL",
    "ug/mL",
    "ug_mL",
    "ug-mL",
    "ug:mL",
    "ug.mL",
    "ugmL",
    "SubX800ngmL2.5h",
    "plate_A1_2026-01-04_63x.tif",
)


#: Magnitudes a filename actually carries, crossed with every unit surface. An
#: integer, a decimal, an exponent and a leading zero, because each reaches a
#: different branch of the number pattern.
MAGNITUDES = ("500", "0.5", "1e-9", "007")

#: The characters that reach a name where a compound unit was written. A slash cannot appear
#: in a POSIX basename, because the kernel reads it as a path separator. So ``ug/mL`` arrives as
#: one of these, and every one of them also divides atoms. Crossing the joiners with the surfaces
#: pins that collision.
JOINERS = (*tuple(_UNIT_JOINERS), "")


def _probes():
    """Every surface, cased four ways and suffixed, plus the corpus.

    Generated over the grammar, never harvested from a laboratory. The morphology and matching
    defects that this corpus catches are facts about SI compound units, ISO 8601, Python word
    boundaries, microplate notation or POSIX path syntax. None is a fact about anybody's data.
    A corpus generated over the grammar is exhaustive where a harvested one is merely sampled,
    and it can be published.
    """
    seen = set(ADVERSARIAL)
    for unit in NORMALISER._units:
        surface = unit.surface
        seen.update(
            (
                surface,
                surface.upper(),
                surface.lower(),
                surface.title(),
                surface + "x",
                surface + "1",
            )
        )
    return sorted(seen)


def _quantity_probes():
    """Every unit surface behind every magnitude, joined every way a name joins.

    The cross the plan asked for, and the reason it is worth its size: the
    compound-unit defect was invisible to a corpus of bare surfaces because it
    only appears when a number, a joiner and two unit fragments meet.
    """
    seen: set[str] = set()
    for unit in NORMALISER._units:
        for magnitude in MAGNITUDES:
            seen.add(f"{magnitude}{unit.surface}")
            for joiner in JOINERS:
                if joiner and joiner in unit.surface:
                    continue
                seen.add(f"{magnitude}{joiner}{unit.surface}")
    return sorted(seen)


def test_the_generated_corpus_covers_the_grammar_rather_than_a_laboratory():
    """A tripwire, not a target, and large enough to be one.

    **Never iterated against until the output looks right**, which is the
    feedback loop that produces overfitting. It pins current behaviour so a
    refactor cannot change it silently, and every case in it is a shape the
    grammar admits rather than a filename somebody wrote.
    """
    surfaces = _probes()
    quantities = _quantity_probes()

    assert len(surfaces) > 1_500
    assert len(quantities) > 5_000
    # Generated, so nothing in it can be a fact about one laboratory. Every
    # probe is a unit surface, a magnitude, a joiner, or a documented adversarial
    # case, and the assertion is that the two sets are built the same way twice.
    assert _probes() == surfaces
    assert _quantity_probes() == quantities


def test_the_buckets_never_change_an_answer_for_a_quantity():
    """The bucketing equivalence, crossed with magnitudes and joiners.

    The bare-surface version below establishes that the filter is lossless on a
    surface. This establishes it where the defect actually was: a number, a
    joiner and a compound unit, which is the shape no corpus of bare surfaces
    contains.
    """
    mismatched = []
    for probe in _quantity_probes():
        atoms = NORMALISER.atoms(probe)
        quantities = [atom for atom in atoms if atom.kind is AtomKind.QUANTITY]
        # Whatever it reads, it must read the same way twice, and it must never
        # produce a quantity whose span runs past the string it came from.
        assert NORMALISER.atoms(probe) == atoms, probe
        for atom in quantities:
            if atom.start + len(atom.text) > len(probe):
                mismatched.append((probe, atom.text, atom.start))
    assert not mismatched


def test_the_buckets_never_change_an_answer():
    """The fast path is a filter, and a filter may only save work.

    The fast path and the full lookup are two implementations of one contract, and the test
    asserts that they correspond. The bucketed lookup narrows 358 candidates to a few dozen,
    and this is what establishes that the narrowing is lossless rather than
    assuming it from the registration rule.
    """
    mismatched = []
    for probe in _probes():
        fast = NORMALISER.match_unit(probe)
        full = NORMALISER._best_unit(probe, NORMALISER._units)
        if (fast is None) != (full is None):
            mismatched.append((probe, fast, full))
        elif fast is not None and full is not None and fast[1]["term"] != full[1]["term"]:
            mismatched.append((probe, fast[1]["term"], full[1]["term"]))
    assert not mismatched


@pytest.mark.parametrize(
    ("token", "term"),
    [
        # A lowercase-initial surface against an uppercase input. Registering
        # "mL" only under "m" while probing only the raw first character loses
        # this, and "500ML" is an ordinary filename rather than an edge case.
        ("500ML", "millilitre"),
        # The micro sign folds to Greek mu, so a bucket probed on the raw
        # character alone misses every unit spelled with the other one.
        ("500µM", "micromolar"),
        ("500μM", "micromolar"),
    ],
)
def test_a_fold_that_changes_the_first_character_still_matches(token, term):
    atoms = NORMALISER.atoms(token)
    assert [atom.unit for atom in atoms if atom.kind is AtomKind.QUANTITY] == [term]


def test_a_folded_match_reports_the_length_it_actually_consumed():
    """``casefold`` is not length-preserving, and the slice must not assume it is.

    ``ß`` folds to ``ss`` and ``İ`` folds to two characters, so a surface
    matched by folding can have consumed fewer characters of the original than
    it has itself. Almost certainly never true of a real scientific filename,
    which is exactly why it is written down as tested rather than as assumed.
    """
    store = DictionaryStore(
        (
            Section(
                name="synthetic",
                summary="",
                senses=(
                    Sense(
                        term="strassen",
                        canonical="strassen",
                        theme_kind="unit",
                        section="synthetic",
                        surfaces=("ssz",),
                        properties={"dimension": "length", "base": "metre", "factor": 1.0},
                    ),
                ),
            ),
        )
    )
    normaliser = Normaliser(store)
    # One character of input, "ß", folds to the two characters "ss".
    assert normaliser.consumed("ßz", "ssz") == 2
    (atom,) = normaliser.atoms("5ßz")
    assert atom.kind is AtomKind.QUANTITY
    assert atom.text == "5ßz"


@pytest.mark.parametrize(
    "name",
    ["500ug_mL", "500ug-mL", "500ug:mL", "500ug.mL", "500ugmL", "500ug/mL"],
)
def test_a_compound_unit_survives_the_joiner_a_filename_forced_on_it(name):
    """A mass concentration is a mass concentration however it had to be spelled.

    A person writes ``ug/mL``. A filename cannot hold a slash: the kernel reads
    it as a path separator, and macOS stores one typed in Finder as a colon. So
    what reaches a name is ``ug_mL`` or ``ug-mL``, and reading those as a bare
    mass would repeat the ``800ngmL`` bug in a different form.
    """
    (atom,) = NORMALISER.atoms(name)
    assert atom.kind is AtomKind.QUANTITY
    assert atom.dimension == "mass_concentration"
    assert atom.base_value == pytest.approx(0.5)


def test_a_joiner_is_only_crossed_when_it_buys_a_longer_unit():
    """Reserving a span that ordinary scanning already reads correctly is a regression.

    ``500ng_control`` is a mass and a word. The joiner-crossing pass must decline
    it, because the match stops at the first fragment and reserving that would
    change a name that tokenises correctly today for no gain.
    """
    atoms = NORMALISER.atoms("500ng_control")
    assert [atom.kind for atom in atoms] == [AtomKind.QUANTITY, AtomKind.WORD]
    assert atoms[0].unit == "nanogram"
    assert atoms[1].normalised == "control"


def test_a_term_identifier_is_cached_per_scope_not_per_term():
    """The cache key must be everything the value depends on.

    Two senses of one term at different scopes have different identifiers, so a
    cache keyed on the term alone returns whichever was computed first. Which one
    that is depends on the order the filesystem handed back names, which makes it
    a reproducibility failure rather than a stale value. Dormant while every
    shipped sense is project-scoped; this is what stops it waking up.
    """
    folder = Scope(ScopeKind.FOLDER, "plates/day1")
    store = DictionaryStore()
    senses = {
        scope: Sense(
            term="confocal",
            canonical="confocal",
            theme_kind="technique",
            section="synthetic",
            surfaces=("confocal",),
            scope=scope,
        )
        for scope in (PROJECT_SCOPE, folder)
    }
    # Project scope first, so a term-keyed cache would return its identifier for
    # both. Order matters here and that is the whole point.
    builtin = DictionaryOrigin.BUILTIN.value
    assert store.term_id(senses[PROJECT_SCOPE]) == dictionary_term_id(
        term="confocal", scope=PROJECT_SCOPE, origin=builtin
    )
    assert store.term_id(senses[folder]) == dictionary_term_id(
        term="confocal", scope=folder, origin=builtin
    )
    assert store.term_id(senses[PROJECT_SCOPE]) != store.term_id(senses[folder])


# ---------------------------------------------------------------------------
# Scope filtering: which senses are candidates at all.
# ---------------------------------------------------------------------------


def _scoped_section(name: str, scope: Scope) -> Section:
    """One synthetic section holding one sense of ``confocal`` at one scope."""
    return Section(
        name=name,
        summary="synthetic",
        senses=(
            Sense(
                term=f"confocal_{name}",
                canonical=name,
                theme_kind="technique",
                section=name,
                surfaces=("confocal",),
                scope=scope,
            ),
        ),
    )


def test_every_shipped_sense_is_project_scoped():
    """The premise the fast path and the no-change claim both rest on.

    Stated as a test rather than believed, because the filter below is a no-op
    exactly while this holds and stops being one the moment it does not.
    """
    kinds = {sense.scope.kind for section in STORE.sections for sense in section.senses}
    assert kinds == {ScopeKind.PROJECT, ScopeKind.FILE_TYPE}
    # The sequencing pack declares read one and read two's ``r1`` and ``r2`` for the read format
    # alone (``file_type``); no other shipped sense is narrower than the project.
    typed = {
        (sense.term, sense.scope.value, sense.surfaces)
        for section in STORE.sections
        for sense in section.senses
        if sense.scope.kind is ScopeKind.FILE_TYPE
    }
    assert typed == {("read_one", "read_format", ("r1",)), ("read_two", "read_format", ("r2",))}


def test_scope_filtering_changes_no_answer_on_the_shipped_dictionary():
    """A project-scoped sense binds at every query scope, so today nothing moves.

    The filter is a correctness trap being closed, not a coverage change, and
    this is what says so.
    """
    for scope in (PROJECT_SCOPE, Scope(ScopeKind.FOLDER, "plates/day1")):
        hit = STORE.look_up("confocal", scope=scope)
        assert hit is not None
        assert hit.sense.scope == PROJECT_SCOPE


def test_a_folder_scoped_sense_does_not_bind_outside_its_folder():
    """A scope parameter is applied, not accepted and discarded.

    If the parameter were threaded through the call sites and then discarded, the first
    folder-scoped sense would bind everywhere and nothing would fail.
    """
    folder = Scope(ScopeKind.FOLDER, "plates/day1")
    store = DictionaryStore([_scoped_section("local", folder)])

    assert store.look_up("confocal", scope=folder) is not None
    assert store.look_up("confocal", scope=Scope(ScopeKind.FOLDER, "plates/day2")) is None
    assert store.look_up("confocal", scope=PROJECT_SCOPE) is None


def test_a_folder_scoped_sense_reaches_beneath_its_folder():
    """ "In this folder, that word means this" is about a subtree.

    A user correcting a term for one experiment folder means it for the raw and
    analysis folders inside it too, and a filter that stopped at the directory
    listing would make them declare it once per level.
    """
    folder = Scope(ScopeKind.FOLDER, "plates/day1")
    store = DictionaryStore([_scoped_section("local", folder)])

    assert store.look_up("confocal", scope=Scope(ScopeKind.FOLDER, "plates/day1/raw")) is not None
    # Boundary, not prefix. ``day10`` is a sibling of ``day1`` and not inside it.
    assert store.look_up("confocal", scope=Scope(ScopeKind.FOLDER, "plates/day10")) is None


def test_a_narrower_sense_still_wins_where_both_apply():
    """Filtering runs before precedence and must not displace it.

    Inside the folder both senses are candidates, and the folder-scoped one wins
    on precedence exactly as SCOPE_PRECEDENCE says. Outside it, only the wider
    one is a candidate at all, which is the filter rather than the precedence.
    """
    folder = Scope(ScopeKind.FOLDER, "plates/day1")
    store = DictionaryStore(
        [_scoped_section("wide", PROJECT_SCOPE), _scoped_section("local", folder)]
    )

    inside = store.look_up("confocal", scope=folder)
    assert inside is not None
    assert inside.sense.scope == folder

    outside = store.look_up("confocal", scope=PROJECT_SCOPE)
    assert outside is not None
    assert outside.sense.scope == PROJECT_SCOPE


def test_a_root_scoped_sense_against_a_folder_query_refuses_rather_than_guesses():
    """The one pair a Scope cannot answer, and it says so.

    A root scope names a root identifier and a folder scope names a path
    relative to a root, so nothing in either value says whether the folder is
    inside the root. Admitting or denying silently is the same trap as discarding a scope. Refusing
    is the only answer that cannot be quietly wrong.
    """
    store = DictionaryStore([_scoped_section("rooted", Scope(ScopeKind.ROOT, "root-abc"))])

    with pytest.raises(ValueError, match="not computable from the scopes alone"):
        store.look_up("confocal", scope=Scope(ScopeKind.FOLDER, "plates/day1"))

    # The same sense at the same root is decidable, and decided.
    assert store.look_up("confocal", scope=Scope(ScopeKind.ROOT, "root-abc")) is not None
    assert store.look_up("confocal", scope=Scope(ScopeKind.ROOT, "root-xyz")) is None


def test_a_collision_audit_says_which_are_reported_and_which_are_won() -> None:
    """:meth:`ambiguities` says which spellings are contested; this says what the lookup does
    about each. That is the distinction that precedence turns on."""
    store = _contested(
        _section(
            "units", origin=DictionaryOrigin.BUILTIN, term="ms_core", surface="zq", kind="unit"
        ),
        _section(
            "spectrometry",
            origin=DictionaryOrigin.PACK,
            term="ms_pack",
            surface="zq",
            kind="technique",
        ),
        _section(
            "local", origin=DictionaryOrigin.USER, term="zx_here", surface="zx", kind="technique"
        ),
        _section(
            "units", origin=DictionaryOrigin.BUILTIN, term="zx_core", surface="zx", kind="unit"
        ),
    )
    found = {item.surface: item for item in store.collisions()}
    assert set(found) == {"zq", "zx"}

    # Level precedence: reported, resolving to neither, with nothing shadowed.
    assert found["zq"].reported
    assert {sense.term for sense in found["zq"].senses} == {"ms_core", "ms_pack"}
    assert found["zq"].shadowed == ()

    # Unequal precedence: won outright, and the loser kept so it can be named.
    assert not found["zx"].reported
    assert found["zx"].winner.term == "zx_here"
    assert {sense.term for sense in found["zx"].shadowed} == {"zx_core"}


def test_the_shipped_vocabulary_decides_no_collision_but_the_core_s_over_a_pack() -> None:
    """A tripwire for precedence: the core wins over a shipped pack by default.

    Every contested spelling in what ships is reported as a tie, or is the core's over a shipped
    pack, whose sense is kept as the one it shadowed. Nothing else is answered on anybody's
    behalf."""
    contested = DictionaryStore.builtin().collisions()
    assert contested
    assert [
        item.surface
        for item in contested
        if not item.reported
        and not (item.winner.domain is None and all(sense.domain for sense in item.shadowed))
    ] == []


def test_a_collision_across_two_theme_kinds_needs_no_declaration() -> None:
    """A spinning disk and a standard deviation really do both spell ``sd``, and that is
    information rather than an error. Only a collision inside one kind must be declared, and
    the audit reads that from the one method that decides it."""
    store = _contested(
        _section(
            "units", origin=DictionaryOrigin.BUILTIN, term="zq_unit", surface="zq", kind="unit"
        ),
        _section(
            "statistics",
            origin=DictionaryOrigin.BUILTIN,
            term="zq_stat",
            surface="zq",
            kind="statistic",
        ),
    )
    assert store.undeclared_ambiguities() == ()
    assert [item.declared for item in store.collisions()] == [True]


def test_the_domain_sections_ship_as_packs_in_waiting_and_nothing_moved() -> None:
    """Each domain section sits in a directory of its own, as a pack waiting to be accepted.

    Each is still loaded by default, at the core's origin and ahead of the universal sections."""
    from ingence.dictionary.store import (
        BUILTIN_DIRECTORY,
        PACKS_DIRECTORY,
        load_sections,
        load_shipped_sections,
    )

    universal = load_sections(BUILTIN_DIRECTORY)
    packed = load_sections(PACKS_DIRECTORY)
    assert not any(section.domain for section in universal)
    assert all(section.domain for section in packed)
    assert {path.parent.name for path in PACKS_DIRECTORY.rglob("*.yaml")} == {
        section.domain for section in packed
    }
    assert all(section.origin is DictionaryOrigin.BUILTIN for section in packed)
    assert [section.name for section in load_shipped_sections()] == [
        *(section.name for section in packed),
        *(section.name for section in universal),
    ]


# ---------------------------------------------------------------------------
# The dictionary freeze: file types as a scope, and a format that is also a word.
# ---------------------------------------------------------------------------


def test_a_format_spelled_like_a_word_binds_after_a_dot_and_not_before_one() -> None:
    """``EXTENSION_ONLY``, which is what lets the real extension ship at all.

    ``.log`` is a format and ``log`` is a process; before the flag, shipping the format
    made the word tie with it and bind to neither, so sixteen formats were spelled
    ``raw_binary`` and ``mat_file`` and the real extension bound to nothing.
    """
    from ingence.dictionary.file_types import EXTENSION_KINDS

    as_extension = STORE.look_up("log", permitted_kinds=EXTENSION_KINDS)
    assert as_extension is not None
    assert (as_extension.sense.term, as_extension.ambiguous) == ("log_file", False)

    as_word = STORE.look_up("log")
    assert as_word is not None
    assert as_word.sense.theme_kind != "format"
    assert "log_file" not in {as_word.sense.term, *(other.term for other in as_word.alternatives)}


def test_every_extension_met_in_development_that_names_a_format_is_reachable() -> None:
    """A tripwire: a spelling that no file name carries is not a file type.

    A spelling that only a collision-avoidance placeholder reaches binds to nothing on real
    names, so each of these must be reachable.
    """
    from ingence.dictionary.file_types import EXTENSION_KINDS

    for extension in ("mat", "nc", "ods", "arrow", "so", "raw", "h", "cc", "fcs", "mrc", "pdb"):
        hit = STORE.look_up(extension, permitted_kinds=EXTENSION_KINDS)
        assert hit is not None, extension
        assert hit.sense.theme_kind in EXTENSION_KINDS


def test_a_file_type_scope_names_the_type_rather_than_one_spelling_of_it() -> None:
    """``.TIF`` and ``tif`` are one type, so two people scoping to it write one scope."""
    from ingence.dictionary.file_types import file_type_scope, known_file_types

    tagged = file_type_scope(STORE, ".TIF")
    assert tagged is not None
    assert tagged.kind is ScopeKind.FILE_TYPE
    assert file_type_scope(STORE, "tif") == tagged
    assert file_type_scope(STORE, "zorbex") is None
    assert tagged.value in {kind.term for kind in known_file_types(STORE)}


def test_a_sense_scoped_to_a_file_type_binds_only_in_a_file_of_that_type() -> None:
    """The scope, where the four regions could not reach: a kind, not a place."""
    from ingence.dictionary.file_types import file_type_scope

    of_type = file_type_scope(STORE, "csv")
    assert of_type is not None
    store = DictionaryStore()
    store.add(
        Section(
            name="answers",
            summary="",
            senses=(
                Sense(
                    term="forward_scatter",
                    canonical="forward scatter",
                    theme_kind="measurement",
                    section="answers",
                    surfaces=("zqx",),
                    origin=DictionaryOrigin.USER,
                    scope=of_type,
                ),
            ),
        )
    )
    assert store.look_up("zqx", scope=of_type) is not None
    assert store.look_up("zqx") is None
    assert store.look_up("zqx", scope=Scope(ScopeKind.FILE_TYPE, "elsewhere")) is None
    # A question naming several places admits a sense scoped to any one of them, and an
    # entry always sits in several: a folder, a root, and a kind of file.
    asked = (Scope(ScopeKind.FOLDER, "plates/day1"), of_type)
    assert store.look_up("zqx", scope=asked) is not None


def test_a_narrower_place_wins_where_both_apply() -> None:
    """A person pointing at a folder beats the same person's rule about a kind of file."""
    from ingence.dictionary.file_types import file_type_scope
    from ingence.dictionary.store import SCOPE_PRECEDENCE

    of_type = file_type_scope(STORE, "csv")
    assert of_type is not None
    assert SCOPE_PRECEDENCE[ScopeKind.FOLDER] > SCOPE_PRECEDENCE[ScopeKind.FILE_TYPE]
    in_folder = Scope(ScopeKind.FOLDER, "plates/day1")
    store = DictionaryStore()
    for term, scope in (("by_type", of_type), ("by_folder", in_folder)):
        store.add(
            Section(
                name=term,
                summary="",
                senses=(
                    Sense(
                        term=term,
                        canonical=term,
                        theme_kind="measurement",
                        section=term,
                        surfaces=("zqx",),
                        origin=DictionaryOrigin.USER,
                        scope=scope,
                    ),
                ),
            )
        )
    hit = store.look_up("zqx", scope=(in_folder, of_type))
    assert hit is not None
    assert (hit.sense.term, hit.ambiguous) == ("by_folder", False)


def test_the_vocabulary_fingerprint_moves_when_a_property_moves() -> None:
    """Two vocabularies that answer differently are not the same Dictionary.

    The properties carry a unit's dimension, a doubt term's strength and whether a sense binds
    outside an extension. If the digest left them out, a change that moved thousands of bindings
    would leave the hash that identifies the vocabulary exactly where it was.
    """
    from ingence.dictionary.store import EXTENSION_ONLY

    def one(**properties: object) -> DictionaryStore:
        store = DictionaryStore()
        store.add(
            Section(
                name="formats",
                summary="",
                senses=(
                    Sense(
                        term="zorbex_format",
                        canonical="zorbex",
                        theme_kind="format",
                        section="formats",
                        surfaces=("zqx",),
                        properties=properties,
                    ),
                ),
            )
        )
        return store

    assert one().fingerprint() != one(**{EXTENSION_ONLY: True}).fingerprint()


def test_a_format_spelling_that_means_nothing_else_still_reads_as_a_word() -> None:
    """A restriction applies per surface, not per sense.

    Restricting whole senses would cost many resolved word bindings on real names:
    ``export_csv_final`` means the format it spells, and so do ``npy`` and ``mat``. Only a spelling
    that is already another sense has anything to gain.
    """
    from ingence.dictionary.file_types import EXTENSION_KINDS

    for spelling, term in (("csv", "tabular_format"), ("npy", "array_format")):
        anywhere = STORE.look_up(spelling)
        assert anywhere is not None and anywhere.sense.term == term, spelling
        in_slot = STORE.look_up(spelling, permitted_kinds=EXTENSION_KINDS)
        assert in_slot is not None and in_slot.sense.term == term, spelling


def test_every_format_spelling_that_is_already_another_sense_is_extension_only() -> None:
    """The freeze's rule, pinned over the whole shipped vocabulary.

    A format's spelling is reachable outside an extension unless the same spelling
    already means something else, in which case it is not: two senses of one spelling at
    one precedence tie and bind to neither, so shipping the format would take the word's
    reading away. Auditing it here rather than trusting each entry is what stops the next
    format added from quietly costing a word.

    There are no exceptions. ``MD`` can mean molecular dynamics, as long as extension surfaces
    still recognise ``.md`` and ``MD`` files as extensions and the molecular-dynamics sense is
    restricted to title-level meaning. So the audit expects an empty set, and a new exception has
    to be argued for rather than inherited.
    """
    from ingence.dictionary.store import EXTENSION_ONLY

    holders: dict[str, set[str]] = {}
    for sense in STORE.senses():
        for surface in sense.surfaces:
            holders.setdefault(surface.casefold(), set()).add(sense.theme_kind)

    undeclared: set[tuple[str, str]] = set()
    for sense in STORE.senses():
        if sense.theme_kind != "format":
            continue
        declared = sense.properties.get(EXTENSION_ONLY) or ()
        spelled = (
            {surface.casefold() for surface in sense.surfaces}
            if declared is True
            else {str(surface).casefold() for surface in declared}
        )
        for surface in sense.surfaces:
            key = surface.casefold()
            if holders[key] - {"format"} and key not in spelled:
                undeclared.add((sense.term, key))

    assert undeclared == set()


def test_a_format_spelled_like_an_ordinary_word_is_extension_only_too() -> None:
    """The second half of the rule, and the Dictionary's silence does not settle it.

    ``step``, ``class``, ``properties``, ``java``, ``arrow``, ``jar``, ``mod`` and ``mat`` are words
    before they are formats, and the shipped vocabulary happens to hold none of them as words -- so
    the collision audit above says nothing about them. Without the rule, many names holding "step"
    would read as STEP CAD exchange, a claim about a protocol step that nothing would catch but a
    person reading the output. A format whose spelling is a word is reachable after a dot and
    nowhere else, whether or not anything else claims the spelling.
    """
    for spelling in ("step", "class", "properties", "java", "arrow", "jar", "mod", "mat"):
        assert STORE.look_up(spelling) is None, spelling
    # And the vendor formats keep every word they collide with: a two-letter token in a filename is
    # initials far more often than a vendor's format.
    for spelling, word in (("m", "metre"), ("mp", "two_photon"), ("model", "fit")):
        hit = STORE.look_up(spelling)
        assert hit is not None and hit.sense.term == word, spelling

    # And the converse still holds: a spelling that is a format's name and nothing else
    # is read as a format wherever it appears.
    for spelling in ("csv", "npy", "pdb", "cxx", "fsa", "ttf"):
        hit = STORE.look_up(spelling)
        assert hit is not None and hit.sense.theme_kind == "format", spelling


# ---------------------------------------------------------------------------
# Decisions on specific spellings, pinned.
# ---------------------------------------------------------------------------


def test_a_time_lapse_is_an_image_stack() -> None:
    """``time-lapse`` means an image stack.

    A time-lapse could mean ``movie`` (a file role) or ``time_series`` (an acquisition axis). Two
    senses of one spelling at one precedence tie, and the spelling would resolve to neither. A
    time-lapse is both of those things and is first of all a stack of images, so it says that and
    nothing more. A laboratory that means the axis teaches it once, and a ``USER`` sense outranks
    this.
    """
    hit = STORE.look_up("time-lapse")
    assert hit is not None
    assert (hit.sense.term, hit.sense.theme_kind, hit.ambiguous) == (
        "image_stack",
        "acquisition",
        False,
    )
    assert STORE.look_up("timelapse").sense.term == "image_stack"
    assert STORE.look_up("stack").sense.term == "image_stack"
    # And neither of the two senses it could have meant claims the spelling.
    for term in ("movie", "time_series"):
        sense = STORE.sense_for_term(term)
        assert sense is not None
        assert "timelapse" not in {surface.casefold() for surface in sense.surfaces}, term


def test_md_is_markdown_after_a_dot_and_molecular_dynamics_before_one() -> None:
    """``md`` is Markdown after a dot and molecular dynamics before one."""
    from ingence.dictionary.file_types import EXTENSION_KINDS

    in_slot = STORE.look_up("md", permitted_kinds=EXTENSION_KINDS)
    assert in_slot is not None and in_slot.sense.term == "markdown"
    as_word = STORE.look_up("md")
    assert as_word is not None
    assert (as_word.sense.term, as_word.ambiguous) == ("molecular_dynamics", False)


def test_the_analysis_stage_is_one_category() -> None:
    """``analysis_output`` is folded into ``analysis``.

    A separate ``analysis_output`` stage would earn many claims, few of them on files that also
    claim ``analysis``. Folding the two keeps those files staged rather than unstaged. The stage
    assignment is not a central aspect of a file's identity, and two stages a person cannot tell
    apart spend attention without buying anything.
    """
    assert STORE.sense_for_term("analysis_output") is None
    for spelling in ("results", "result", "output", "out", "derived", "res"):
        hit = STORE.look_up(spelling)
        assert hit is not None, spelling
        assert hit.sense.term == "analysis", spelling


def test_the_vendor_formats_are_each_their_own_type() -> None:
    """Each vendor format ships as its own type and is scopeable, so they are not one bucket.

    Images are the exception: an image is one type, and an image spelling's vendor is its
    origin, not a type of its own -- so ``fli`` is scoped as an image, and an ND2 file is
    named by its extension where a person wants to speak of ND2 files alone.
    """
    from ingence.dictionary.file_types import file_type_scope

    for spelling, term in (
        ("fli", "image_format"),
        ("lifext", "image_format"),
        ("nd2", "image_format"),
        ("ab1", "capillary_trace"),
        ("wsp", "flowjo_workspace"),
        ("pzfx", "prism_project"),
        (".dna", "snapgene_plasmid"),
    ):
        scope = file_type_scope(STORE, spelling)
        assert scope is not None, spelling
        assert scope.value == term, spelling


def test_an_image_is_one_type_and_a_proprietary_spelling_carries_its_origin() -> None:
    """The proprietary file types limit an image's origins.

    There is one ``image_format``; every proprietary spelling declares its vendor and whether an
    instrument writes it as it acquires or software writes it afterwards; a generic spelling
    declares none, because anything writes a TIFF. The audit is over the shipped vocabulary:
    a spelling that declares an origin must be one of the sense's own surfaces, and its writer
    one of the two words a rule can name.
    """
    from ingence.dictionary.store import ORIGINS, WRITTEN_BY

    assert STORE.sense_for_term("vendor_image_format") is None
    assert STORE.sense_for_term("lambert_liflim_image") is None
    assert STORE.sense_for_term("leica_lasx_extension") is None
    image = STORE.sense_for_term("image_format")
    assert image is not None
    origins = image.properties[ORIGINS]
    assert {
        spelling for spelling, origin in origins.items() if origin[WRITTEN_BY] == "instrument"
    } >= {"nd2", "czi", "lif", "lsm", "oib", "fli", "lifext"}
    assert origins["ims"][WRITTEN_BY] == "software"
    assert "tif" not in origins and "png" not in origins

    for sense in STORE.senses():
        declared = sense.properties.get(ORIGINS)
        if declared is None:
            continue
        assert sense.theme_kind == "format", sense.term
        surfaces = {surface.casefold() for surface in sense.surfaces}
        for spelling, origin in declared.items():
            assert str(spelling).casefold() in surfaces, (sense.term, spelling)
            assert origin[WRITTEN_BY] in {"instrument", "software"}, (sense.term, spelling)
            assert str(origin.get("vendor") or "").strip(), (sense.term, spelling)


def test_no_two_dictionary_entries_share_an_identifier() -> None:
    """A term's identifier is its term, scope and origin, so two sections giving one term two senses
    wrote two rows under one primary key (a run status and a doubt word were both ``failed``).
    Every entry the shipped vocabulary holds has an identifier of its own."""
    from collections import Counter

    from ingence.dictionary.store import DictionaryStore

    counted = Counter(record.term_id for record in DictionaryStore.builtin().records())
    assert [term_id for term_id, n in counted.items() if n > 1] == []
