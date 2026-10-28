"""Reading a name: what the Dictionary made of it, and where the parts sat."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from ingence.core.ids import component_id, name_shape_id
from ingence.core.schema import (
    CLOCK_TIER_OF,
    BindingMethod,
    ClockTier,
    Component,
    DictionaryOrigin,
    Entry,
    EntryType,
    MatchQuality,
    ScanOutcome,
    SourceScope,
    TimeSource,
)
from ingence.core.tiers import ExtractionTier, ResolutionStatus
from ingence.dictionary.discover import (
    EXTENSION_CONSTRAINT,
    IDENTIFIER_CONSTRAINT,
    NUMBER_CONSTRAINT,
    read_name,
)
from ingence.dictionary.normalize import NORMALIZER_NAME, NORMALIZER_VERSION, Normaliser
from ingence.dictionary.store import (
    ORIGIN_PRECEDENCE,
    DictionaryStore,
    Section,
    Sense,
    origin_rank,
)

STORE = DictionaryStore.builtin()
NORMALISER = Normaliser(STORE)


def entry_named(name: str) -> Entry:
    pure = PurePosixPath(name)
    parts = pure.suffixes[-2:]
    compound = "".join(part.casefold() for part in parts) if len(parts) > 1 else ""
    return Entry(
        entry_id=f"entry_{name}",
        root_id="root_test",
        relative_path=name,
        name=name,
        entry_type=EntryType.FILE,
        extension=pure.suffix.casefold().removeprefix("."),
        compound_extension=compound.removeprefix("."),
        size_bytes=0,
        mode=33188,
        nlink=1,
        modified_ns=0,
        changed_ns=0,
        resolution_status=ResolutionStatus.UNEXAMINED,
        first_seen_run="run_a",
        last_seen_run="run_a",
        scan_outcome=ScanOutcome.METADATA_ONLY,
    )


def read(name: str, **kwargs):
    return read_name(
        entry_named(name), run_id="run_a", normaliser=NORMALISER, store=STORE, **kwargs
    )


# ---------------------------------------------------------------------------
# Components.
# ---------------------------------------------------------------------------


def test_a_repeated_term_is_one_component_counted_not_several_rows():
    """Nothing downstream would have caught the duplicates.

    Compaction sorts and does not deduplicate, the primary key is not enforced
    before a commit despite what its declaration says, and run comparison is
    set-valued, so three identical rows would have compared equal to one. The
    count has to be made here or it is not made at all.
    """
    reading = read("confocal_confocal_confocal.tif")
    confocal = [c for c in reading.components if c.normalized_value == "confocal"]
    assert len(confocal) == 1
    assert confocal[0].occurrences == 3


def test_the_occurrence_count_matches_the_number_of_slots_that_reference_it():
    """The count is recoverable from the shape, and the two must not disagree."""
    reading = read("plate_plate_a1_plate.tif")
    slots_per_component: dict[str, int] = {}
    for slot in reading.shape.slots:
        slots_per_component[slot.component_id] = slots_per_component.get(slot.component_id, 0) + 1
    for component in reading.components:
        assert component.occurrences == slots_per_component[component.component_id]


def binding_for(reading, token: str):
    """Return the binding of the component whose normalised value is this token."""
    (component,) = [c for c in reading.components if c.normalized_value == token]
    (binding,) = [b for b in reading.bindings if b.component_id == component.component_id]
    return component, binding


def test_a_component_carries_no_interpretation_at_all():
    """A read fact and a resolved sense never share a field, because of the record's shape.

    A token read off the filesystem and a sense resolved from it sit at different distances from the
    bytes, and a single tier column cannot describe both. So the component holds the token as read
    and the binding holds the sense. Stamping a Dictionary interpretation on the component with the
    tier ``LITERAL`` would claim it was as certain as a ``stat`` call.
    """
    assert not hasattr(Component, "dictionary_term_id")
    reading = read("confocal_stack.tif")
    assert all(c.extraction_tier is ExtractionTier.LITERAL for c in reading.components)
    assert all(b.extraction_tier is ExtractionTier.SEMANTIC for b in reading.bindings)


def test_a_recognised_term_carries_the_standing_of_the_sense_it_resolved_to():
    """The evidence travels with the binding, not just the verdict.

    The lookup computes how well the token matched and where the sense came from, and the binding
    keeps both. Without them no later rule could attenuate a Dictionary hit, because it could not
    see either.
    """
    _, binding = binding_for(read("confocal_stack.tif"), "confocal")
    sense = STORE.sense_for_term("confocal")
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    assert binding.term_id == STORE.term_id(sense)
    assert binding.quality is MatchQuality.EXACT
    assert binding.sense_origin is DictionaryOrigin.BUILTIN
    # The microscopy section's sense, level with the core.
    assert (
        binding.sense_origin_rank
        == origin_rank(sense)
        == ORIGIN_PRECEDENCE[DictionaryOrigin.BUILTIN]
    )
    assert binding.sense_digest == sense.digest


def test_an_ambiguous_atom_is_recorded_as_conflicting_rather_than_as_unknown():
    """Unknown and conflicting are different findings and stay different.

    ``corr`` really is both corrected and a correlation. Recording that as the same null as a token
    nobody has ever heard of loses the one piece of information a person could act on. Unknown and
    conflicting are different states, and the record keeps them apart.
    """
    _, binding = binding_for(read("CORR_notes.txt"), "corr")
    assert binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
    assert binding.term_id is None
    kinds = {alternative.theme_kind for alternative in binding.alternatives}
    assert kinds == {"stage", "statistic"}
    assert binding.alternative_count == len(binding.alternatives)


def test_a_bare_ms_is_a_millisecond():
    """The unit style reads ``ms`` as milliseconds, and no technique spells it."""
    _, binding = binding_for(read("pellan_ms_quolt.csv"), "ms")
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    assert (binding.term, binding.theme_kind) == ("millisecond", "unit")


def test_a_one_character_word_binds_no_unit_and_keeps_every_other_reading():
    """A lone letter is an initial, a panel or a channel far more often than a unit."""
    for name, letter in (
        ("kelt_S_vorn.csv", "s"),
        ("tamen_g_brisa_h.tif", "g"),
        ("tamen_g_brisa_h.tif", "h"),
    ):
        _, binding = binding_for(read(name), letter)
        assert binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE, (name, letter)
    _, statistic = binding_for(read("zeph_p_cv_mirdo.csv"), "p")
    assert (statistic.term, statistic.theme_kind) == ("p_value", "statistic")
    _, unit = binding_for(read("pellan_msec_quolt.csv"), "msec")
    assert unit.term == "millisecond"


def test_a_bare_number_binds_only_a_surface_spelled_exactly_as_it_is():
    """The six laser lines are reachable, and a digit reaches nothing else.

    Not looking a number up at all would leave every sense spelled as digits unreachable from a
    name. Matching as written is narrower. It admits a surface that really is written ``405``. It
    refuses the digits that reached a sense only by losing the punctuation that made them an index.
    """
    component, binding = binding_for(read("plate_405_kelt.tif"), "405")
    assert component.component_type == "number"
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    assert (binding.term, binding.theme_kind) == ("wavelength", "channel")
    assert binding.quality is MatchQuality.EXACT
    assert binding.constraint == NUMBER_CONSTRAINT

    for name in ("quolt_2_pellan.tif", "brisa_report (2).pdf"):
        component, binding = binding_for(read(name), "2")
        assert component.component_type == "number", name
        assert binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE, name
        assert binding.alternatives == ()
        assert binding.constraint == NUMBER_CONSTRAINT, name


def test_the_only_digit_surfaces_the_shipped_dictionary_spells_are_the_six_laser_lines():
    """The senses that matching a digit as written reaches, asserted rather than assumed.

    The repair is worth exactly this set. If a later section spells a sense in digits,
    this test says so before a name starts binding it.
    """
    digits = {
        surface for sense in STORE.senses() for surface in sense.surfaces if surface.isdigit()
    }
    assert digits == {"405", "488", "561", "594", "640", "647"}
    for surface in sorted(digits):
        hit = STORE.look_up(surface, minimum_quality=MatchQuality.EXACT)
        assert hit is not None and hit.sense.term == "wavelength", surface


def test_a_restricted_spelling_never_consults_a_weaker_index():
    """``minimum_quality`` bounds how a token may be spelled, not what it may mean."""
    assert STORE.look_up("2") is not None
    assert STORE.look_up("2", minimum_quality=MatchQuality.EXACT) is None
    folded = STORE.look_up("CONFOCAL", minimum_quality=MatchQuality.CASE_FOLDED)
    assert folded is not None and folded.quality is MatchQuality.CASE_FOLDED
    assert STORE.look_up("CONFOCAL", minimum_quality=MatchQuality.EXACT) is None


def _tied(order: tuple[str, ...]) -> DictionaryStore:
    """Return a store in which one surface belongs to two senses standing equal."""
    kinds = {"first_reading": "technique", "second_reading": "unit"}
    return DictionaryStore(
        Section(
            name=term,
            summary="",
            senses=(
                Sense(
                    term=term,
                    canonical=term,
                    theme_kind=kinds[term],
                    section=term,
                    surfaces=("qz",),
                ),
            ),
        )
        for term in order
    )


@pytest.mark.parametrize(
    "order", [("first_reading", "second_reading"), ("second_reading", "first_reading")]
)
def test_a_conflicting_binding_names_every_sense_it_could_not_choose_between(order):
    """A tie has no winner, so no tied sense may be the one left out.

    A hit holds one tied sense first only because it must hold one, and which
    sense that is follows the order sections loaded in. The binder dropped that
    sense from the conflict, so a two-way tie named one candidate, and a rule
    reading the dropped sense's axis never learned the token was contested.
    """
    store = _tied(order)
    reading = read_name(
        entry_named("qz_notes.txt"), run_id="run_a", normaliser=Normaliser(store), store=store
    )
    _, binding = binding_for(reading, "qz")
    assert binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
    assert {alternative.term_id for alternative in binding.alternatives} == {
        store.term_id(sense)
        for sense in (store.sense_for_term("first_reading"), store.sense_for_term("second_reading"))
    }
    assert binding.theme_kind is None


def test_a_token_nobody_has_heard_of_is_recorded_as_no_evidence():
    _, binding = binding_for(read("zzqqxx.txt"), "zzqqxx")
    assert binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert binding.term_id is None
    assert binding.alternatives == ()


def test_the_binder_can_be_switched_off_without_losing_the_tokens():
    """The switch behind deferring the Dictionary until it is needed."""
    reading = read("confocal_stack.tif", with_bindings=False)
    assert reading.bindings == ()
    assert reading.components


def test_an_identifier_is_never_looked_up_and_its_binding_says_it_declined():
    """A digest means nothing a Dictionary could hold. Its binding reads no evidence, as a
    number's binding reads none when the number is not looked up, and the marker says why."""
    reading = read("tamen_py311hb7e04d2_S0123456789012345.csv")
    identifiers = [c for c in reading.components if c.component_type == "identifier"]
    assert sorted(c.normalized_value for c in identifiers) == [
        "hb7e04d2",
        "s0123456789012345",
    ]
    for component in identifiers:
        (binding,) = [b for b in reading.bindings if b.component_id == component.component_id]
        assert binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE
        assert binding.constraint == IDENTIFIER_CONSTRAINT
    assert not [
        b for b in reading.bindings if b.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
    ]


def test_a_fragment_inside_a_run_is_no_longer_a_tie_and_a_measure_standing_alone_still_is():
    """The ``d`` in a hash stood between day and dots per inch, and a person was asked which."""
    conflicting = [
        b
        for b in read("vorn_12d7qx.csv").bindings
        if b.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
    ]
    assert conflicting == []
    _, binding = binding_for(read("scan_63x.tif"), "63 fold")
    assert binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING


def test_a_term_in_parts_binds_as_one_component():
    """``western_blot`` is one component bound to the technique, not two words each bound."""
    reading = read("plate3_western_blot_2.tif")
    component, binding = binding_for(reading, "western blot")
    assert component.raw_value == "western_blot"
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    assert binding.theme_kind == "technique"
    assert not [c for c in reading.components if c.normalized_value in ("western", "blot")]


def test_a_hyphen_after_a_quantity_keeps_it_with_the_word_it_modifies():
    """``3mM-Kelt`` is a quantity on a subject: the two slots share a fragment.

    A hyphen joins a quantity to the subject it modifies. Written with an underscore, or
    anywhere else, a hyphen divides as a space does."""
    joined = read("vorn_3mM-Kelt_2.csv").shape
    apart = read("vorn_3mM_Kelt_2.csv").shape
    assert joined is not None and apart is not None
    assert [(slot.component_type, slot.group) for slot in joined.slots] == [
        ("word", 0),
        ("quantity", 1),
        ("word", 1),
        ("number", 2),
        ("extension", 3),
    ]
    assert [(slot.component_type, slot.group) for slot in apart.slots] == [
        ("word", 0),
        ("quantity", 1),
        ("word", 2),
        ("number", 3),
        ("extension", 4),
    ]


def test_a_declared_unit_ambiguity_binds_nothing():
    """``63x`` is sixty-three fold or sixty-three times magnification."""
    component, binding = binding_for(read("scan_63x.tif"), "63 fold")
    assert component.raw_value == "63x"
    assert binding.method is BindingMethod.UNIT_TABLE
    assert binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
    # The unit the table holds is one of the readings, and is named as one.
    named = {alternative.term_id for alternative in binding.alternatives}
    assert STORE.term_id(STORE.sense_for_term(component.unit)) in named
    assert len(named) >= 2


# ---------------------------------------------------------------------------
# The extension, established before anything else is read.
# ---------------------------------------------------------------------------


def test_an_extension_binds_only_to_a_format():
    """The orthogonal cross-check, as a constraint rather than a heuristic.

    ``md`` was bound to molecular dynamics in thirty-one of thirty-two cases on real laboratory
    folders, every one of them a Markdown file. The fix stops reading a file's type as an ordinary
    word and keeps the Dictionary entry.
    """
    component, binding = binding_for(read("ARCHITECTURE.md"), "md")
    assert component.component_type == "extension"
    assert binding.constraint == EXTENSION_CONSTRAINT
    assert binding.theme_kind == "format"
    assert STORE.sense_for_term("markdown") is not None


def test_the_same_token_outside_the_extension_slot_is_the_word_and_not_the_format():
    """The slot decides outright: outside the extension slot, ``md`` is the word.

    Molecular dynamics is a reading of ``MD`` among a name's words only, and markdown is reachable
    after a dot and nowhere else. Both senses tying and resolving to neither would be the cost of
    shipping a format's spelling as though it were a word.
    """
    _, binding = binding_for(read("System_MD"), "md")
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    assert binding.term == "molecular_dynamics"
    assert binding.alternatives == ()

    # And the extension still reaches the format, which is the half that must not move.
    _, extension = binding_for(read("ARCHITECTURE.md"), "md")
    assert extension.term == "markdown"


def test_a_compound_extension_is_split_whole():
    """``archive.tar.gz`` must not leave ``tar`` behind to be read as a word."""
    reading = read("archive.tar.gz")
    extensions = [c for c in reading.components if c.component_type == "extension"]
    assert [c.normalized_value for c in extensions] == ["tar.gz"]
    assert "tar" not in {c.normalized_value for c in reading.components}


def test_a_component_drawn_from_a_name_says_so_in_its_scope_and_its_tier():
    for component in read("confocal.tif").components:
        assert component.source_scope is SourceScope.NAME
        assert component.extraction_tier is ExtractionTier.LITERAL
        assert component.normalizer == NORMALIZER_NAME
        assert component.normalizer_version == NORMALIZER_VERSION


def test_scope_is_part_of_a_component_identifier():
    """Two scopes, one token, one entry: two identifiers, not a silent collapse."""
    shared = {
        "entry": "entry_x",
        "component_type": "word",
        "normalized_value": "confocal",
        "normalizer": NORMALIZER_NAME,
        "normalizer_version": NORMALIZER_VERSION,
    }
    from_name = component_id(**shared, source_scope=SourceScope.NAME.value)
    from_content = component_id(**shared, source_scope=SourceScope.CONTENT.value)
    assert from_name != from_content


def test_an_empty_name_yields_nothing_rather_than_an_empty_shape():
    reading = read_name(entry_named(""), run_id="run_a", normaliser=NORMALISER, store=STORE)
    assert reading.components == ()
    assert reading.shape is None


# ---------------------------------------------------------------------------
# Shapes.
# ---------------------------------------------------------------------------


def test_a_skeleton_abstracts_content_away_and_keeps_layout():
    """What the shape corpus needs, and what it must not contain.

    A recognised term becomes its theme kind, an unrecognised run becomes a
    placeholder, and the separators stay. Nothing that survives is a gene, a
    clone or a project code.
    """
    assert read("plate_A1_2026-01-04.tif").shape.skeleton == "{vessel}_{INDEXED}_{DATE}.{format}"


def differing_slots(first: str, second: str) -> tuple[list[int], list]:
    """Return which slots two names differ at, and the first name's slots.

    A slot is compared through its component, never by its component identifier. A component
    identifier includes the entry it was drawn from, so two entries never share one even for the
    same token, and comparing identifiers across entries reports every slot as differing. The join
    is the one the design intends: content lives in one record, position in another, and a rule that
    asks for both joins them. This is the whole of what the series rule has to do, and a shape
    record that could not support it would be the wrong record.
    """
    left, right = read(first), read(second)
    values = {
        component.component_id: (component.component_type, component.normalized_value)
        for reading in (left, right)
        for component in reading.components
    }
    ordinals = [
        a.ordinal
        for a, b in zip(left.shape.slots, right.shape.slots, strict=True)
        if values[a.component_id] != values[b.component_id]
    ]
    return ordinals, list(left.shape.slots)


def test_two_names_built_to_one_template_share_a_skeleton_and_differ_in_one_slot():
    """The specification test for this record, traced without writing the rule.

    Files in one folder sharing a skeleton and differing at exactly one slot are
    a series, and the differing slot names which kind. A record that could not
    express this would be the wrong record, and finding that out later costs
    another schema bump.
    """
    first = read("sample_confocal_rep1.tif").shape
    second = read("sample_confocal_rep2.tif").shape
    assert first.skeleton == second.skeleton
    assert first.skeleton_hash == second.skeleton_hash

    differing, slots = differing_slots("sample_confocal_rep1.tif", "sample_confocal_rep2.tif")
    assert len(differing) == 1
    # An indexed atom is a replicate series. The extension is split off first, so the indexed
    # pattern sees a whole separator-free chunk. Without that, the atom would shatter into a word
    # and a bare number, because ".tif" would be part of the chunk. The rule reads the label and the
    # index.
    assert slots[differing[0]].component_type == "indexed"


@pytest.mark.parametrize(
    ("first", "second", "kind"),
    [
        ("run_2026-01-04.csv", "run_2026-01-05.csv", "date"),
        ("dose_10nM_plate.csv", "dose_100nM_plate.csv", "quantity"),
    ],
)
def test_the_differing_slot_says_which_kind_of_series_it_is(first, second, kind):
    assert read(first).shape.skeleton == read(second).shape.skeleton
    differing, slots = differing_slots(first, second)
    assert len(differing) == 1
    assert slots[differing[0]].component_type == kind


def test_comparing_slots_by_identifier_alone_would_report_every_slot_as_differing():
    """The trap the join exists to avoid, written down so nobody rediscovers it.

    A component identifier includes its entry, which is right: a component is a
    fact about one entry. It also means the identifier is useless for asking
    whether two entries hold the same thing, and a rule written the obvious way
    would find every folder to be a series of nothing.
    """
    left, right = read("sample_rep1.tif").shape, read("sample_rep2.tif").shape
    naive = [
        a.ordinal
        for a, b in zip(left.slots, right.slots, strict=True)
        if a.component_id != b.component_id
    ]
    assert len(naive) == len(left.slots)


def test_names_that_are_not_a_series_do_not_share_a_skeleton():
    """A shape group has to be a real pre-filter or every folder is one series."""
    assert read("sample_confocal_rep1.tif").shape.skeleton != read("notes.txt").shape.skeleton


def test_a_shape_is_identified_by_its_entry_scope_and_normaliser():
    reading = read("anything.tif")
    assert reading.shape.name_shape_id == name_shape_id(
        entry="entry_anything.tif",
        source_scope=SourceScope.NAME.value,
        normalizer=NORMALIZER_NAME,
        normalizer_version=NORMALIZER_VERSION,
    )


def test_every_slot_points_at_a_component_that_was_emitted():
    reading = read("subjectA_westernblot_500ug_mL_24h_rep2_B3.tif")
    emitted = {component.component_id for component in reading.components}
    assert {slot.component_id for slot in reading.shape.slots} <= emitted
    assert reading.shape.slot_count == len(reading.shape.slots)


def test_position_can_be_switched_off_without_losing_content():
    reading = read("plate_A1.tif", with_shape=False)
    assert reading.shape is None
    assert reading.components


# ---------------------------------------------------------------------------
# Occurrences: addressing this spelling, not this value.
# ---------------------------------------------------------------------------


def test_a_slot_slices_its_own_occurrence_out_of_the_name():
    """``name[start:end]`` is this occurrence's exact text, for every slot."""
    name = "plate_A1_2026-01-04_63x.tif"
    shape = read(name).shape
    assert shape is not None
    for slot in shape.slots:
        assert name[slot.start : slot.end], f"slot {slot.ordinal} addresses nothing"
    sliced = [name[slot.start : slot.end] for slot in shape.slots]
    assert sliced == ["plate", "A1", "2026-01-04", "63x", "tif"]


def test_two_equivalent_occurrences_are_told_apart_by_their_slots():
    """Two spellings of one quantity stay addressable apart.

    ``40min`` and ``2400s`` are the same quantity written two ways, so they normalise to one value
    and share one component row. That row keeps the first spelling and nothing else. Without the end
    offset nothing could recover ``2400s``: the engine could address the value and not the
    occurrence, and every layer above it needs the occurrence.
    """
    name = "treated_40min_control_2400s.tif"
    reading = read(name)
    shape = reading.shape
    assert shape is not None

    quantities = [slot for slot in shape.slots if slot.component_type == "quantity"]
    assert len(quantities) == 2
    assert len({slot.component_id for slot in quantities}) == 1, "one value, two occurrences"

    spellings = [name[slot.start : slot.end] for slot in quantities]
    assert spellings == ["40min", "2400s"]

    # The component kept only the first, which is the whole reason the slot has
    # to be able to slice the name.
    component = next(c for c in reading.components if c.component_id == quantities[0].component_id)
    assert component.raw_value == "40min"
    assert component.occurrences == 2


def test_slots_are_grouped_by_the_fragment_they_were_written_in():
    """Adjacent atoms share a group; a separator starts a new one."""
    shape = read("plate_A1_2026-01-04.tif").shape
    assert shape is not None
    groups = [slot.group for slot in shape.slots]
    assert groups == sorted(groups), "groups must not go backwards"
    assert len(set(groups)) > 1, "a name with separators has more than one fragment"


def test_no_slot_carries_a_role():
    """Roles are declared and unpopulated, and this test keeps it true.

    A role is a relationship inside one interpretation and there are no interpretations. Asserting
    it here means that code which starts assigning roles has to delete a test that says why not,
    rather than quietly filling a field nobody remembered was reserved.
    """
    for name in ("plate_A1_2026-01-04_63x.tif", "treated_40min_control.csv", "raw_v2.txt"):
        shape = read(name).shape
        assert shape is not None
        assert all(slot.role is None for slot in shape.slots)


# ---------------------------------------------------------------------------
# The event clock a filename supplies.
# ---------------------------------------------------------------------------


def test_one_unambiguous_date_becomes_a_tier_three_event_clock():
    reading = read("acquisition_2026-01-04_63x.tif")
    assert reading.event_time_source is TimeSource.FILENAME
    assert CLOCK_TIER_OF[reading.event_time_source] is ClockTier.NAME
    assert reading.event_time_ns == 1767484800000000000


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("span_2026-01-04_to_2026-01-08.csv", "two dates and nothing says which is the event"),
        ("monthly_2026-01.csv", "a month is not an instant"),
        ("plate_01-02-2026.csv", "day and month could swap, so the reader refuses to choose"),
        ("no_date_at_all.csv", "there is nothing to read"),
    ],
)
def test_the_engine_abstains_rather_than_guessing_an_event_time(name: str, why: str):
    reading = read(name)
    assert reading.event_time_ns is None, why
    assert reading.event_time_source is TimeSource.UNAVAILABLE, why


@pytest.mark.parametrize("name", ["tag_29991231_x.tif", "b_2300-06-15.csv"])
def test_a_date_no_instant_can_hold_is_no_clock(name: str):
    """A calendar date after 2262 (or before 1677) is a code, and a time the Index cannot store.

    A census stopped on a name that spelled such a date, which was read as a clock in nanoseconds
    too large for the column.
    """
    reading = read(name)
    assert reading.event_time_ns is None
    assert reading.event_time_source is TimeSource.UNAVAILABLE


def test_the_unit_matcher_falls_back_on_nothing_the_engine_actually_asks_it() -> None:
    """The linear fallback, once 40 per cent of tokenisation, costs nothing on every corpus.

    The fallback once cost 40 per cent of tokenisation on a quantity-dense pool. The cost went
    because the file extension was the one bucket miss every name had, and the extension is split
    off before anything is tokenised.

    The counter is the only thing holding that true. The fallback still fires if something calls the
    normaliser with an extension still attached, because a digit followed by letters that is not a
    unit is exactly a replicate index against a suffix. If a caller appears that has not split the
    extension, the 40 per cent returns silently, and a silent cost is the one nobody looks for.
    """
    normaliser = Normaliser(STORE)
    before = normaliser._fallbacks

    corpus = (
        # Quantity-dense, the shape that cost 40 per cent.
        "500nM_40min_63x_2.5h.tif",
        "sample_10ug_per_mL_37C.csv",
        # Replicate and figure indexes, the pattern that triggers a fallback when a suffix is still
        # attached.
        "sample_rep1.csv",
        "figure2.png",
        "run_007.nd2",
        # No extension at all.
        "plate_layout_day3",
        # Ordinary prose-ish names.
        "final_analysis_summary.xlsx",
        "2026-01-15_confocal_hela.md",
    )
    for name in corpus:
        read_name(
            entry_named(name),
            run_id="run_a",
            normaliser=normaliser,
            store=STORE,
        )

    assert normaliser._fallbacks == before, (
        "the unit matcher fell back to a full scan of every surface; the engine's "
        "own path splits the extension off first, so a caller that does not has "
        "appeared and the 40 per cent slowdown has come back silently"
    )


def test_the_pooled_stage_takes_two_surfaces_another_sense_already_spells() -> None:
    """``final`` and ``AAA`` sit on the pooled stage, and this records what they do.

    The test gives a measurement on real data something to compare against.

    ``final`` ties, because a version sense already spells it exactly, and a tie resolves to
    neither: a file named ``_final`` loses the version reading it had and gains no stage.
    ``AAA`` does not tie, because case separates it: the Dictionary matches exactly before
    it folds, so ``AAA`` reaches the pooled stage. A short run of one repeated letter is
    not read as a keyboard mash, so ``aaa`` folds to ``AAA``.
    """
    final = STORE.look_up("final")
    assert final is not None and final.ambiguous
    assert {final.sense.term, *(sense.term for sense in final.alternatives)} == {"pooled", "final"}

    upper = STORE.look_up("AAA")
    assert upper is not None and not upper.ambiguous
    assert (upper.sense.term, upper.quality) == ("pooled", MatchQuality.EXACT)

    lower = STORE.look_up("aaa")
    assert lower is not None and not lower.ambiguous
    assert (lower.sense.term, lower.quality) == ("pooled", MatchQuality.CASE_FOLDED)


def test_a_word_meaning_two_stages_resolves_to_neither_and_says_so() -> None:
    """``merged`` is a merged image and merged results, and the engine says which it cannot pick.

    A declared ambiguity is how the Dictionary reports one rather than choosing. The cost
    is that those names get no stage claim, and that cost is the honest half of the answer.
    """
    for surface in ("merged", "combined"):
        hit = STORE.look_up(surface)
        assert hit is not None and hit.ambiguous, surface
        standing = {hit.sense.term, *(sense.term for sense in hit.alternatives)}
        assert standing == {"pooled", "merged"}, surface
    unambiguous = STORE.look_up("pooled")
    assert unambiguous is not None and unambiguous.sense.term == "pooled"


# ---------------------------------------------------------------------------
# An indexed atom's label, which is step 1 of the replicate structure.
# ---------------------------------------------------------------------------


def _bound(reading, *, value: str):
    """The binding on the component whose normalised value is ``value``."""
    components = {component.component_id: component for component in reading.components}
    for binding in reading.bindings:
        if components[binding.component_id].normalized_value == value:
            return binding
    present = [component.normalized_value for component in reading.components]
    message = f"no component normalised to {value!r} in {present}"
    raise AssertionError(message)


def test_an_indexed_labels_replicate_sense_binds() -> None:
    """``rep1`` is a replicate: the binder reads its label and its index apart.

    Looking up ``rep1`` whole finds nothing. The label carries the meaning, and the number
    beside it says which one."""
    binding = _bound(read("kelt_rep1_raw.csv"), value="rep1")
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    assert binding.term == "replicate"
    assert binding.theme_kind == "replicate"


def test_the_two_letter_replicate_labels_bind() -> None:
    """``br`` and ``tr`` bind as biological and technical replicates. Counted apart, never
    summed: three technical replicates of one dish is n = 1."""
    assert _bound(read("kelt_br2_raw.csv"), value="br2").term == "biological_replicate"
    assert _bound(read("kelt_tr3_raw.csv"), value="tr3").term == "technical_replicate"


def test_a_label_binds_only_where_the_whole_text_reached_nothing() -> None:
    """The ordering that keeps 91 shipped readings intact.

    ``c1`` is a shipped surface of the channel sense, ``p1`` of passage and ``q1`` of the
    first quartile. Ninety-one shipped surfaces are index-shaped; trying the label first, or
    instead, would replace every one of them.
    """
    assert _bound(read("kelt_c1_raw.tif"), value="c1").term == "channel"
    assert _bound(read("kelt_p1_raw.tif"), value="p1").term == "passage"
    assert _bound(read("kelt_q1_raw.csv"), value="q1").term == "quartile"


def test_a_label_binds_nothing_outside_the_permitted_kinds() -> None:
    """A label outside the permitted kinds binds nothing.

    Unrestricted, a label binds ``a`` to the ampere by folding case, ``p`` to the p-value,
    ``s`` to the second and ``c`` to the Celsius degree, so a well ``A1`` would be a current
    and a position ``p03`` a statistic. What a restricted slot records is what it *may*
    mean, not what it probably means.
    """
    for name, value in (
        ("kelt_a1_raw.tif", "a1"),
        ("kelt_p03_raw.tif", "p3"),
        ("kelt_s2_raw.tif", "s2"),
    ):
        binding = _bound(read(name), value=value)
        assert binding.resolution_status is not ResolutionStatus.RESOLVED, name


def test_a_version_marker_is_not_an_indexed_atom_at_all() -> None:
    """``V1`` never reaches the label path.

    The normaliser reads ``v``, ``r``, ``rev`` and ``build`` followed by a number as a *version*
    atom before the indexed pattern is tried, so ``V1`` is a version marker and the label lookup
    never sees it. The engine is not entitled to decide whether ``V`` means version, variant, volume
    or replicate, and may only notice the shape and ask. The outcome comes from that order, not from
    the label restriction: unrestricted, the label ``v`` binds the version sense outright.
    """
    reading = read("kelt_v1_raw.tif")
    versioned = [
        component
        for component in reading.components
        if component.normalized_value == "v1" and component.component_type == "version"
    ]
    assert versioned, [
        (component.normalized_value, component.component_type) for component in reading.components
    ]
    assert _bound(reading, value="v1").resolution_status is not ResolutionStatus.RESOLVED


def test_a_label_binding_names_the_name_slot_and_what_restricted_it() -> None:
    """A marker is ``<slot>:<restriction>``, and a label is one of the name's own tokens exactly as
    a bare number is, so there is no third slot."""
    from ingence.dictionary.discover import INDEX_LABEL_CONSTRAINT

    binding = _bound(read("kelt_rep1_raw.csv"), value="rep1")
    assert binding.constraint == INDEX_LABEL_CONSTRAINT
    assert INDEX_LABEL_CONSTRAINT.split(":", 1)[0] == "name"


def test_a_whole_text_binding_records_no_restriction() -> None:
    """Because it was looked up unrestricted, and saying otherwise would file it wrongly."""
    assert _bound(read("kelt_c1_raw.tif"), value="c1").constraint is None


@pytest.mark.parametrize("name", ["pooled_N=3_summary.csv", "pooled_n=3_summary.csv"])
def test_a_stated_count_binds_on_a_kind_of_its_own(name) -> None:
    """``N=3`` says how many replicates something rests on, which is a cardinality, and every sense
    on the replicate axis is an ordinal. On one axis the rule that reads a position would conclude
    "this file is stated-replicate-count number three"."""
    binding = _bound(read(name), value="n3")
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    assert binding.term == "stated_replicate_count"
    assert binding.theme_kind == "count"


def test_a_count_label_records_its_own_marker() -> None:
    """The marker names the kind that bound, because two kinds are admitted and a rule
    reading it must be able to tell a cardinality from an ordinal."""
    from ingence.dictionary.discover import COUNT_LABEL_CONSTRAINT, INDEX_LABEL_CONSTRAINT

    assert _bound(read("pooled_N=3_summary.csv"), value="n3").constraint == COUNT_LABEL_CONSTRAINT
    assert _bound(read("kelt_rep1_raw.csv"), value="rep1").constraint == INDEX_LABEL_CONSTRAINT


def test_a_label_that_bound_nothing_names_the_restriction_and_not_one_of_its_kinds() -> None:
    """Two kinds are tried and neither answered, so naming one of them would say the other
    was never tried. The marker is still recorded, because a token that abstained under a
    restriction is not a token nobody has ever heard of."""
    from ingence.dictionary.discover import UNBOUND_LABEL_CONSTRAINT

    binding = _bound(read("kelt_zz1_raw.csv"), value="zz1")
    assert binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert binding.constraint == UNBOUND_LABEL_CONSTRAINT


def test_the_kinds_a_label_may_bind_are_derived_from_the_markers_they_record() -> None:
    """One set declared twice is one set an edit can move by half. The kinds are read off the marker
    map, so a kind admitted without a marker cannot exist."""
    from ingence.dictionary.discover import INDEX_LABEL_CONSTRAINT_OF, INDEX_LABEL_KINDS

    assert frozenset(INDEX_LABEL_CONSTRAINT_OF) == INDEX_LABEL_KINDS
    assert all(marker.startswith("name:") for marker in INDEX_LABEL_CONSTRAINT_OF.values())


def test_a_bare_n_word_is_the_ambiguity_it_is() -> None:
    """``n`` is a sample size and a replicate count, and the notation is the same one. The
    restriction on an index label separates them; an unrestricted word has nothing to
    separate them by, and choosing would invent a fact."""
    binding = _bound(read("sample_n_3.csv"), value="n")
    assert binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
    assert binding.alternative_count == 2


def test_the_resolver_version_moved_with_the_widened_kinds_and_again_with_identifiers() -> None:
    """Resolution reaches a different sense for unchanged input and an unchanged Dictionary, which
    is what this version exists to say. It is not the normaliser's. Version 6 widened the kinds a
    label binds. Version 7 declines identifiers, and the normaliser moved with it because
    identifiers are its reading. The normaliser moved again for case and compound terms, glued runs
    and escaped spaces. Version 8 reads a spelling a capital divides once per run, whole or as
    pieces."""
    from ingence.dictionary.discover import RESOLVER_VERSION

    # Resolver version 10: a spelling two domains hold differently is read from the folder's files.
    assert RESOLVER_VERSION == "10"
    assert (
        NORMALIZER_VERSION == "14"
    )  # Normaliser version 14: the 2Pl fix, the clock, spaced quantities


def test_a_name_is_read_against_the_kind_of_file_it_is() -> None:
    """The file-type scope reaches the binder.

    A person's answer about their ``.csv`` files is a statement about a kind of file
    anywhere in the pool, and the binder admits it on a ``.csv`` and nowhere else. The
    binding still records the region the question was asked about; the sense that won
    carries the type in its own scope, which is what keeps the two legible apart.
    """
    from ingence.core.schema import DictionaryOrigin
    from ingence.dictionary.file_types import file_type_scope
    from ingence.dictionary.store import DictionaryStore, Section, Sense

    store = DictionaryStore.builtin()
    of_type = file_type_scope(store, "csv")
    assert of_type is not None
    store.add(
        Section(
            name="answers",
            summary="",
            senses=(
                Sense(
                    term="zorbex_channel",
                    canonical="zorbex channel",
                    theme_kind="channel",
                    section="answers",
                    surfaces=("zqx",),
                    origin=DictionaryOrigin.USER,
                    scope=of_type,
                ),
            ),
        )
    )

    def term_for(name: str) -> str | None:
        reading = read_name(
            entry_named(name), run_id="run_a", normaliser=Normaliser(store), store=store
        )
        bindings = {
            component.component_id: component
            for component in reading.components
            if component.normalized_value == "zqx"
        }
        for binding in reading.bindings:
            if binding.component_id in bindings:
                return binding.term
        return None

    assert term_for("zqx_plate.csv") == "zorbex_channel"
    assert term_for("zqx_plate.tif") is None
    assert term_for("zqx_plate") is None


def test_a_name_is_read_against_the_folder_it_is_in() -> None:
    """A folder-scoped answer binds.

    "In this folder, that word means this" is stored by the Record and honoured by the
    Dictionary's own precedence. The census passes each entry's folder scope to the binder, so
    such a sense is a candidate. Passing it costs almost nothing on real laboratory names, and
    it moves no other binding.

    A folder scope reaches inward, so naming the entry's own folder admits what was declared for any
    folder above it.
    """
    from ingence.core.ids import Scope, ScopeKind
    from ingence.core.schema import DictionaryOrigin
    from ingence.dictionary.store import DictionaryStore, Section, Sense

    store = DictionaryStore.builtin()
    store.add(
        Section(
            name="answers",
            summary="",
            senses=(
                Sense(
                    term="zorbex_channel",
                    canonical="zorbex channel",
                    theme_kind="channel",
                    section="answers",
                    surfaces=("zqx",),
                    origin=DictionaryOrigin.USER,
                    scope=Scope(ScopeKind.FOLDER, "plates/day1"),
                ),
            ),
        )
    )

    def term_for(path: str) -> str | None:
        entry = entry_named(path.rsplit("/", 1)[-1]).model_copy(update={"relative_path": path})
        reading = read_name(entry, run_id="r", normaliser=Normaliser(store), store=store)
        wanted = {
            component.component_id
            for component in reading.components
            if component.normalized_value == "zqx"
        }
        for binding in reading.bindings:
            if binding.component_id in wanted:
                return binding.term
        return None

    assert term_for("plates/day1/zqx_sample.tif") == "zorbex_channel"
    # And beneath it, because a folder scope is a statement about a subtree.
    assert term_for("plates/day1/repeat/zqx_sample.tif") == "zorbex_channel"
    # And nowhere else.
    assert term_for("plates/day2/zqx_sample.tif") is None
    assert term_for("zqx_sample.tif") is None
