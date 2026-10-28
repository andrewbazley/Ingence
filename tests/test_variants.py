"""The spellings that may be the same word, one source at a time (the Words section).

A word is offered beside the spellings that may be the same word, each with why and how many files
hold it, and a pair a person called unrelated is never offered again. The six sources each have a
switch and each a test here, on words invented for the purpose. The ordering, the number shown and
the filter for unrelated pairs are tested across them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.core.settings import SpellingSettings, VariantSettings
from ingence.dictionary.spelling import SpellingIndex
from ingence.dictionary.store import DictionaryStore
from ingence.dictionary.variants import (
    GluedSpelling,
    RunWord,
    VariantIndex,
    distinct_pairs,
    variants_of,
)
from ingence.record.log import append_event
from ingence.record.targets import distinct_target

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ingence.dictionary.variants import Variant


def rw(word: str, files: int = 3, **more: Any) -> RunWord:
    return RunWord(word, files, occurrences=files, **more)


def words(*found: RunWord) -> dict[str, RunWord]:
    return {item.word: item for item in found}


@pytest.fixture(scope="module")
def known() -> SpellingIndex:
    """The shipped Dictionary's spellings, indexed once with the default limits."""
    spelling = SpellingSettings()
    return SpellingIndex(
        (
            (sense.term, surface)
            for sense in DictionaryStore.builtin().senses()
            for surface in sense.surfaces
        ),
        minimum=spelling.minimum_letters,
        edits=spelling.edits,
        candidates=spelling.candidates,
    )


def of(
    word: str,
    run: Mapping[str, RunWord],
    known: SpellingIndex | None = None,
    *,
    pieces: Mapping[str, GluedSpelling] | None = None,
    distinct: frozenset[tuple[str, str]] = frozenset(),
    **switches: Any,
) -> tuple[Variant, ...]:
    settings = VariantSettings(**switches)
    return VariantIndex(
        run,
        settings=settings,
        spelling=SpellingSettings(),
        known=known,
        glued=pieces,
        distinct=distinct,
    ).of(word)


def by_reason(found: tuple[Variant, ...]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for variant in found:
        grouped.setdefault(variant.reason, []).append(variant.spelling)
    return grouped


# -- 1. slips toward a known word -----------------------------------------------------------------


def test_a_slip_of_a_word_the_dictionary_knows_is_offered_with_its_distance(
    known: SpellingIndex,
) -> None:
    run = words(rw("confokal", 4), rw("confocal", 9, term="confocal"))
    (variant,) = of("confokal", run, known)
    assert (variant.spelling, variant.reason, variant.distance) == ("confocal", "slip", 1)
    assert variant.reason_said == "one letter apart"
    assert variant.known_as == "confocal"
    assert (variant.files, variant.offer) == (9, "same")
    # A word the Dictionary holds is no slip of anything.
    assert of("confocal", run, known) == ()


def test_a_known_word_the_run_does_not_write_is_still_offered_with_no_files(
    known: SpellingIndex,
) -> None:
    (variant,) = of("confokal", words(rw("confokal", 4)), known)
    assert (variant.spelling, variant.files) == ("confocal", 0)


def test_the_slip_toward_a_known_word_has_its_own_switch(known: SpellingIndex) -> None:
    assert of("confokal", words(rw("confokal", 4)), known, slips=False) == ()


# -- 2. slips among the run's own unknown words ---------------------------------------------------


def test_two_unknown_words_one_letter_apart_are_offered_to_each_other() -> None:
    run = words(rw("fennowic", 5), rw("fennowik", 2))
    (first,) = of("fennowic", run)
    (second,) = of("fennowik", run)
    assert (first.spelling, first.reason, first.distance, first.files) == ("fennowik", "slip", 1, 2)
    assert (second.spelling, second.files) == ("fennowic", 5)
    assert second.known_as is None


def test_two_letters_apart_say_so_and_come_after_one() -> None:
    run = words(rw("fennowic", 5), rw("fennowik", 2), rw("fennowix", 1), rw("fennuwix", 1))
    found = of("fennowic", run)
    assert [(v.spelling, v.distance) for v in found] == [
        ("fennowik", 1),
        ("fennowix", 1),
        ("fennuwix", 2),
    ]
    assert found[-1].reason_said == "two letters apart"


def test_a_word_a_slip_of_a_word_that_is_known_is_not_compared_to_the_unknown_ones() -> None:
    run = words(rw("fennowic", 5), rw("fennowik", 2, term="fennowik_probe"))
    assert of("fennowic", run) == ()  # the other is understood: the Dictionary side offers it


def test_slips_among_unknown_words_have_their_own_switch() -> None:
    run = words(rw("fennowic", 5), rw("fennowik", 2))
    assert of("fennowic", run, slips_among_unknown=False) == ()
    assert of("fennowic", run, slips=False) != ()  # the other source, still on


def test_short_words_are_not_compared() -> None:
    run = words(rw("quolt", 5), rw("quilt", 2))  # five letters: two edits from too many
    assert of("quolt", run) == ()


# -- 3. case and separators -----------------------------------------------------------------------


def test_two_spellings_alike_once_separators_are_taken_out_are_offered() -> None:
    run = words(rw("hek-293x", 6), rw("hek293x", 2), rw("hek 293x", 1))
    found = of("hek293x", run)
    assert by_reason(found) == {"separators": ["hek-293x", "hek 293x"]}
    assert found[0].reason_said == "the same word with other separators"


def test_two_spellings_alike_but_for_capitals_are_offered_as_other_capitals() -> None:
    run = words(rw("Brisal", 6), rw("brisal", 2))
    (variant,) = of("brisal", run)
    assert (variant.spelling, variant.reason) == ("Brisal", "case")
    assert variant.reason_said == "the same word in other capitals"


def test_case_and_separators_have_their_own_switch() -> None:
    run = words(rw("hek-293x", 6), rw("hek293x", 2))
    assert of("hek293x", run, case_and_separators=False, slips_among_unknown=False) == ()


def test_only_unknown_spellings_are_compared_by_their_separators() -> None:
    run = words(rw("hek-293x", 6, term="hek293x"), rw("hek293x", 2))
    assert of("hek293x", run, slips_among_unknown=False) == ()


# -- 4. plural and singular -----------------------------------------------------------------------


def test_a_word_and_its_plural_are_offered_to_each_other() -> None:
    run = words(rw("quolt", 6), rw("quolts", 2))
    (plural,) = of("quolt", run)
    (singular,) = of("quolts", run)
    assert (plural.spelling, plural.reason, plural.reason_said) == (
        "quolts",
        "plural",
        "a plural of it",
    )
    assert (singular.spelling, singular.reason, singular.reason_said) == (
        "quolt",
        "singular",
        "its singular",
    )
    assert plural.distance is None


def test_a_plural_is_not_also_offered_as_a_slip() -> None:
    run = words(rw("fennowic", 6), rw("fennowics", 2))  # eight letters, one edit apart
    assert by_reason(of("fennowic", run)) == {"plural": ["fennowics"]}


def test_a_word_whose_own_plural_is_present_is_the_singular_and_a_sense_is_no_plural() -> None:
    run = words(rw("tamen", 6), rw("tamens", 2), rw("tamenses", 1))
    # ``tamens`` has a plural of its own here, so it is a singular and folds onto nothing.
    assert by_reason(of("tamens", run, slips_among_unknown=False)) == {"plural": ["tamenses"]}
    assert by_reason(of("tamenses", run, slips_among_unknown=False)) == {"singular": ["tamens"]}
    assert of("tamen", run, slips_among_unknown=False) == ()
    bound = words(rw("quolt", 6), rw("quolts", 2, term="quolt_sample"))
    assert of("quolt", bound, slips_among_unknown=False) == ()


def test_plurals_have_their_own_switch() -> None:
    run = words(rw("quolt", 6), rw("quolts", 2))
    assert of("quolt", run, plurals=False) == ()


# -- 5. glued -------------------------------------------------------------------------------------


def test_the_pieces_of_a_divided_spelling_are_offered_with_their_counts() -> None:
    glued = {"zibkelt": GluedSpelling("zibKelt", ("zib", "Kelt"))}
    run = words(rw("zibkelt", 3), rw("zib", 7), rw("kelt", 2))
    found = of("zibkelt", run, pieces=glued)
    assert [(v.spelling, v.reason, v.files) for v in found] == [
        ("zib", "glued", 7),
        ("kelt", "glued", 2),
    ]
    assert found[0].reason_said == "its pieces"
    (joined,) = of("zib", run, pieces=glued)
    assert (joined.spelling, joined.reason, joined.files) == ("zibkelt", "glued_in", 3)
    assert joined.reason_said == "it joined to another word"


def test_glued_has_its_own_switch() -> None:
    glued = {"zibkelt": GluedSpelling("zibKelt", ("zib", "Kelt"))}
    run = words(rw("zibkelt", 3), rw("zib", 7), rw("kelt", 2))
    assert of("zibkelt", run, pieces=glued, glued=False) == ()
    assert of("zib", run, pieces=glued, glued=False) == ()


# -- 6. initials ----------------------------------------------------------------------------------


def test_capital_initials_and_the_word_they_stand_for_are_offered_where_both_are_there() -> None:
    run = words(
        rw("bovine serum albumin", 5, term="bovine_serum_albumin"),
        rw("bsa", 3, raw=("BSA",)),
    )
    (spelled,) = of("bsa", run)
    assert (spelled.spelling, spelled.reason) == ("bovine serum albumin", "spelled_out")
    assert spelled.reason_said == "what its initials stand for"
    (initials,) = of("bovine serum albumin", run)
    assert (initials.spelling, initials.reason, initials.reason_said) == (
        "bsa",
        "initials",
        "its initials",
    )


def test_initials_are_offered_only_where_the_run_writes_both_and_in_capitals() -> None:
    lower = words(
        rw("bovine serum albumin", 5, term="bovine_serum_albumin"), rw("bsa", 3, raw=("bsa",))
    )
    assert of("bsa", lower) == ()
    absent = words(rw("bsa", 3, raw=("BSA",)))
    assert of("bsa", absent) == ()
    too_long = words(
        rw("bovine serum albumin of a cell line used often", 5, term="long"),
        rw("bsaoacluo", 3, raw=("BSAOACLUO",)),
    )
    assert of("bsaoacluo", too_long) == ()


def test_initials_have_their_own_switch() -> None:
    run = words(rw("bovine serum albumin", 5, term="b"), rw("bsa", 3, raw=("BSA",)))
    assert of("bsa", run, initials=False) == ()


# -- 7. same slot ---------------------------------------------------------------------------------


def _slot(
    word: str, names: set[int], neighbors: set[str], places: set[str], files: int = 4
) -> RunWord:
    return rw(
        word,
        files,
        names=frozenset(names),
        neighbors=frozenset(neighbors),
        places=frozenset(places),
    )


COMPANY = {"dapi", "gfp", "plate"}


def test_two_spellings_that_take_each_other_s_place_are_offered_as_different_things_first() -> None:
    run = words(
        _slot("wtl", {1, 2}, COMPANY, {"lab/e1"}),
        _slot("kol", {3, 4}, COMPANY, {"lab/e2"}),
    )
    found = of("wtl", run, slips_among_unknown=False)
    (variant,) = found
    assert (variant.spelling, variant.reason, variant.offer) == ("kol", "same_slot", "kind")
    assert variant.reason_said == "they take each other's place"
    assert of("kol", run, slips_among_unknown=False)[0].spelling == "wtl"


def test_spellings_in_one_name_together_are_not_in_each_other_s_place() -> None:
    run = words(
        _slot("wtl", {1, 2}, COMPANY, {"lab/e1"}),
        _slot("kol", {2, 4}, COMPANY, {"lab/e1"}),
    )
    assert of("wtl", run, slips_among_unknown=False) == ()


def test_too_few_words_in_common_or_folders_far_apart_offer_nothing() -> None:
    few = words(
        _slot("wtl", {1}, {"dapi", "gfp"}, {"lab/e1"}),
        _slot("kol", {3}, {"dapi"}, {"lab/e2"}),
    )
    assert of("wtl", few, slips_among_unknown=False) == ()
    far = words(
        _slot("wtl", {1}, COMPANY, {"lab/one/e1"}),
        _slot("kol", {3}, COMPANY, {"other/two/e2"}),
    )
    assert of("wtl", far, slips_among_unknown=False) == ()
    same_folder = words(
        _slot("wtl", {1}, COMPANY, {"lab/e1"}),
        _slot("kol", {3}, COMPANY, {"lab/e1"}),
    )
    assert [v.spelling for v in of("wtl", same_folder, slips_among_unknown=False)] == ["kol"]


def test_a_word_in_nearly_every_name_says_nothing_about_a_place() -> None:
    hub = rw("vorn", 500)  # in 500 names: more than the setting allows a shared word
    run = words(
        hub,
        _slot("wtl", {1}, {"vorn", "plate"}, {"lab/e1"}),
        _slot("kol", {3}, {"vorn", "plate"}, {"lab/e2"}),
    )
    assert of("wtl", run, slips_among_unknown=False) == ()  # one shared word is left; two needed


def test_words_beside_a_spelling_count_at_their_distance() -> None:
    near = {"+1:dapi", "+2:gfp"}
    run = words(
        _slot("wtl", {1, 2}, near, {"lab/e1"}),
        _slot("kol", {3, 4}, near, {"lab/e2"}),
        # The same words, but one place further on: not the same slot.
        _slot("shifted", {5, 6}, {"+2:dapi", "+3:gfp"}, {"lab/e2"}),
    )
    assert [v.spelling for v in of("wtl", run, slips_among_unknown=False)] == ["kol"]


def test_same_slot_has_its_own_switch_and_needs_the_names_it_reads() -> None:
    run = words(
        _slot("wtl", {1, 2}, COMPANY, {"lab/e1"}),
        _slot("kol", {3, 4}, COMPANY, {"lab/e2"}),
    )
    assert of("wtl", run, slips_among_unknown=False, same_slot=False) == ()
    bare = words(rw("wtl", 4), rw("kol", 4))
    assert of("wtl", bare, slips_among_unknown=False) == ()


# -- across the sources ---------------------------------------------------------------------------


def test_a_pair_a_person_called_unrelated_is_never_offered_by_any_source(
    known: SpellingIndex,
) -> None:
    run = words(
        _slot("fennowic", {1, 2}, COMPANY, {"lab/e1"}),
        _slot("fennowik", {3, 4}, COMPANY, {"lab/e2"}),
        rw("confokal", 2),
    )
    pair = frozenset({("fennowic", "fennowik"), ("confocal", "confokal")})
    assert of("fennowic", run, known, distinct=pair) == ()
    assert of("fennowik", run, known, distinct=pair) == ()
    assert of("confokal", run, known, distinct=pair) == ()
    # The other direction of the same pair is the same pair.
    assert of("fennowik", run, known, distinct=frozenset({("fennowic", "fennowik")})) == ()


def test_one_source_keeps_a_pair_another_has_lost_and_the_nearest_reason_is_kept() -> None:
    run = words(
        _slot("fennowic", {1, 2}, COMPANY, {"lab/e1"}),
        _slot("fennowik", {3, 4}, COMPANY, {"lab/e2"}),
    )
    (variant,) = of("fennowic", run)  # a slip and a same-slot pair: one spelling, once
    assert (variant.spelling, variant.reason, variant.distance) == ("fennowik", "slip", 1)


def test_the_nearest_come_first_then_the_most_files_and_the_number_shown_is_a_setting() -> None:
    run = words(
        rw("fennowic", 1),
        rw("fennowik", 2),
        rw("fennowix", 9),
        rw("fennuwix", 20),
        rw("fennowics", 30),
    )
    everything = of("fennowic", run, shown=10)
    assert [(v.spelling, v.reason) for v in everything] == [
        ("fennowics", "plural"),
        ("fennowix", "slip"),
        ("fennowik", "slip"),
        ("fennuwix", "slip"),
    ]
    assert [v.spelling for v in of("fennowic", run, shown=2)] == ["fennowics", "fennowix"]
    assert VariantSettings().shown == 5


def test_the_function_form_builds_what_the_index_does(known: SpellingIndex) -> None:
    run = words(rw("confokal", 4))
    found = variants_of(
        "confokal", run_words=run, store=DictionaryStore.builtin(), settings=VariantSettings()
    )
    assert [(v.spelling, v.reason) for v in found] == [("confocal", "slip")]
    assert found == of("confokal", run, known)


# -- the pairs a person called unrelated, read from the Record ------------------------------------


def test_the_pairs_called_unrelated_are_the_rejections_in_force(tmp_path: Path) -> None:
    from ingence.scan.census import run_census

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "a.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")

    def say(kind: RecordEventKind, first: str, second: str, at: int) -> None:
        append_event(
            project.path,
            kind=kind,
            actor="Dana Whitfield",
            occurred_at=f"2026-01-02T00:00:0{at}Z",
            target=distinct_target(first, second),
            against_run_id="run_20260101T000001Z_111111111111",
            payload={"spellings": sorted((first, second))},
            scope=PROJECT_SCOPE,
        )

    say(RecordEventKind.REJECT, "fennowik", "fennowic", 1)
    say(RecordEventKind.REJECT, "quolt", "quolts", 2)
    assert distinct_pairs(project.path) == {("fennowic", "fennowik"), ("quolt", "quolts")}
    # A confirm on the same target takes the answer back.
    say(RecordEventKind.CONFIRM, "quolts", "quolt", 3)
    assert distinct_pairs(project.path) == {("fennowic", "fennowik")}
