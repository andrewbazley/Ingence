"""A word that is a slip from a Dictionary spelling is read as that spelling, marked corrected.

Every name, word and person here is invented. The Dictionary words are the shipped ones.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from ingence.core.schema import DictionaryOrigin, Entry, EntryType, MatchQuality, ScanOutcome
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import (
    SPELLING_CONSTRAINT,
    read_name,
    spelling_constraint,
    spelling_read_as,
)
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import (
    CORRECTION_NEVER_ONTO,
    DictionaryStore,
    Section,
    Sense,
    spelling_denylist,
)

STORE = DictionaryStore.builtin()
NORMALISER = Normaliser(STORE)


def _entry(name: str) -> Entry:
    pure = PurePosixPath(name)
    return Entry(
        entry_id=f"entry_{name}",
        root_id="root_test",
        relative_path=name,
        name=name,
        entry_type=EntryType.FILE,
        extension=pure.suffix.casefold().removeprefix("."),
        compound_extension="",
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


def _bound(name: str, word: str):
    reading = read_name(_entry(name), run_id="run_a", normaliser=NORMALISER, store=STORE)
    (component,) = [c for c in reading.components if c.raw_value == word]
    (binding,) = [b for b in reading.bindings if b.component_id == component.component_id]
    return binding


@pytest.mark.parametrize(
    ("slip", "term", "spelling"),
    [
        ("hoescht", "nuclear_stain", "hoechst"),  # two edits, seven letters, the same letters
        ("confocol", "confocal", "confocal"),  # one edit
        ("widefeild", "epifluorescence", "wide-field"),  # two edits, the same letters
    ],
)
def test_a_slip_of_a_shipped_spelling_binds_to_it_marked_corrected(
    slip: str, term: str, spelling: str
) -> None:
    binding = _bound(f"cells_{slip}_01.tif", slip)
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    # The marker carries the Dictionary spelling the word was read as.
    assert (binding.term, binding.quality, binding.constraint) == (
        term,
        MatchQuality.CORRECTED,
        spelling_constraint(spelling),
    )
    assert spelling_read_as(binding.constraint) == spelling
    assert spelling_read_as(SPELLING_CONSTRAINT) is None


@pytest.mark.parametrize(
    "word",
    [
        "strain",  # the deny-list: an English word beside stain
        "slice",  # the deny-list: beside slide
        "TIRFS",  # all capitals at five letters: an acronym, never a slip
        "zorbit",  # a slip of nothing
        "confocal",  # a Dictionary word is no slip
        "pixl",  # four letters: too short
    ],
)
def test_what_is_never_read_as_a_slip(word: str) -> None:
    binding = _bound(f"cells_{word}_01.tif", word)
    assert binding.quality is not MatchQuality.CORRECTED
    assert spelling_read_as(binding.constraint) is None


def test_the_deny_list_is_plain_english_and_loads_as_no_section() -> None:
    from ingence.dictionary.store import BUILTIN_DIRECTORY, load_sections

    listed = spelling_denylist()
    assert len(listed) > 100 and {"alone", "block"} <= listed  # one edit from clone, lock
    assert all(word.isalpha() and word.islower() for word in listed)
    assert all(section.name != "unnamed" for section in load_sections(BUILTIN_DIRECTORY))


def test_a_second_candidate_within_one_extra_edit_refuses_the_correction() -> None:
    kelt = Section(
        name="kelt",
        summary="",
        senses=(
            Sense("zorbital", "zorbital", "technique", "kelt", ("zorbital",)),
            Sense("zorbitol", "zorbitol", "reagent", "kelt", ("zorbitol",)),
            Sense("brisanet", "brisanet", "technique", "kelt", ("brisanet",)),
        ),
    )
    store = DictionaryStore((kelt,))
    assert store.correct("zorbitul") is None  # zorbital and zorbitol are both one edit away
    found = store.correct("brisanot")
    assert found is not None and found.sense.term == "brisanet"
    assert found.quality is MatchQuality.CORRECTED and found.token == "brisanot"
    assert store.corrected_to["brisanot"] == "brisanet"


def test_two_edits_are_read_only_with_the_same_letters_in_another_order() -> None:
    """Two edits are read only with the same letters in another order.

    The looser test, the same first two letters and the same last letter, admits too many English
    words.
    """
    kelt = Section(
        name="kelt",
        summary="",
        senses=(Sense("zorbital", "zorbital", "technique", "kelt", ("zorbital",)),),
    )
    store = DictionaryStore((kelt,))
    assert store.correct("zorbitla") is not None  # the same letters, two swapped
    assert store.correct("zorbetol") is None  # two edits, the first two and last letters alike


def test_bench_words_are_in_the_deny_list() -> None:
    listed = spelling_denylist()
    assert {"marker", "freeze", "crops", "merges"} <= listed


def test_a_unit_date_doubt_status_format_or_version_word_is_never_a_correction() -> None:
    assert {"format", "version"} == CORRECTION_NEVER_ONTO
    # "minuts" is a slip of "minute"? The unit's spellings are min, minute, minutes: a plural's
    # singular is never a slip, and a unit is never folded onto.
    for word in ("minuts", "monthe", "finale", "parquett"):
        found = STORE.correct(word)
        assert found is None or found.sense.theme_kind not in (
            STORE.plural_excluded_kinds() | CORRECTION_NEVER_ONTO
        ), (word, found)


def test_a_pair_a_person_called_unrelated_is_never_corrected_again() -> None:
    store = DictionaryStore.builtin()
    assert store.correct("confocol") is not None
    store.distinct = frozenset({("confocal", "confocol")})
    assert store.correct("confocol") is None


def test_a_project_s_unrelated_pairs_reach_the_run_s_dictionary(tmp_path) -> None:
    from ingence.core.project import Project
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.record.targets import distinct_target

    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    append_event(
        project.path,
        kind=RecordEventKind.REJECT,
        actor="kelt",
        occurred_at="2026-01-01T00:00:00Z",
        target=distinct_target("confocol", "confocal"),
        against_run_id="",
        payload={"verdict": "reject"},
    )
    store = DictionaryStore.for_project(project.path)
    assert store.distinct == frozenset({("confocal", "confocol")})
    assert store.correct("confocol") is None


def test_the_rule_raises_each_corrected_spelling_once_with_its_count() -> None:
    from ingence.rulebook.predicates.spellings import SpellingReadAs, read_as

    class Held:
        def __init__(self, name: str) -> None:
            self.entry = _entry(name)
            reading = read_name(self.entry, run_id="run_a", normaliser=NORMALISER, store=STORE)
            self.components = reading.components
            self.bindings = reading.bindings

    class Scope:
        entries = (Held("a_hoescht_1.tif"), Held("b_hoescht_2.tif"), Held("c_confocol_3.tif"))

    raised = SpellingReadAs().evaluate(Scope(), {"minimum_files": 1})
    assert raised is not None
    assert raised.values == (read_as("confocol", "confocal"), read_as("hoescht", "hoechst"))
    assert "hoescht is read as hoechst in 2 files" in raised.reason
    assert len(raised.inputs) == 3
    assert SpellingReadAs().evaluate(Scope(), {"minimum_files": 3}) is None


def test_a_rule_whose_floor_is_loose_refuses_a_corrected_binding() -> None:
    from ingence.core.schema import match_quality_meets as quality_meets

    assert not quality_meets(MatchQuality.CORRECTED, MatchQuality.LOOSE)
    assert not quality_meets(MatchQuality.CORRECTED, MatchQuality.EXACT)
    assert quality_meets(MatchQuality.CORRECTED, MatchQuality.CORRECTED)


def test_a_person_s_own_spelling_is_no_slip_of_a_shipped_one() -> None:
    """A word a person defined is a Dictionary word, and is read as itself."""
    own = Section(
        name="answers",
        summary="",
        origin=DictionaryOrigin.USER,
        senses=(
            Sense(
                "confocol_dish",
                "confocol_dish",
                "vessel",
                "answers",
                ("confocol",),
                origin=DictionaryOrigin.USER,
            ),
        ),
    )
    store = DictionaryStore((*DictionaryStore.builtin().sections, own))
    hit = store.look_up("confocol")
    assert hit is not None and hit.sense.term == "confocol_dish"
    assert store.correct("confocol") is None


def test_an_unrelated_answer_from_the_finding_stops_the_correction(tmp_path) -> None:
    """The round trip: the finding names the pair, the answer route writes
    ``distinct:hoescht+hoechst`` through ``_decide_distinct``, and the next reading corrects
    nothing.
    """
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.core.project import Project
    from ingence.record.targets import distinct_target
    from ingence.rulebook.predicates.spellings import read_as
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "cells_hoescht_01.tif").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    with open_catalog(project.path) as catalog:
        ((constraint,),) = catalog.query(
            'SELECT b."constraint" FROM dictionary_bindings b JOIN components c '
            "USING (component_id) WHERE c.normalized_value = 'hoescht'"
        ).fetchall()
        matched = spelling_read_as(constraint)
        assert matched == "hoechst"
        assert read_as("hoescht", matched) == "hoescht is read as hoechst"
        event = decide_target(
            DecisionContext(catalog, project.path),
            Decision(target=distinct_target("hoescht", matched), word="unrelated"),
            actor="kelt",
            occurred_at="2026-01-02T00:00:00Z",
        )
    assert event.target == "distinct:hoechst+hoescht"
    store = DictionaryStore.for_project(project.path)
    assert store.distinct == frozenset({("hoechst", "hoescht")})
    assert store.correct("hoescht") is None
