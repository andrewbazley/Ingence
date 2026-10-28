"""Ask, engine side: question words as Dictionary data, a class word widened to its members, and a
question answered with experiments in three tiers, each saying why. Every name is invented; the pool
is the themes' planted one."""

from __future__ import annotations

import pytest

from ingence.api.v1 import context_for, dispatch
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.search.ask import DEFAULT_DROPPED_KINDS, unpunctuated
from ingence.search.expand import CLASS_HOW, SENSE_HOW, MatchRoute, expand
from ingence.search.query import (
    MAX_CONCEPTS,
    QUESTION_KIND,
    class_term,
    is_question_word,
    parse_query,
)

STORE = DictionaryStore.builtin()
NORMALISER = Normaliser(STORE)


class _NoPool:
    """A vocabulary that spells nothing: only the Dictionary's routes remain."""

    def entries_with(self, token: str) -> int:
        return 0

    def near(self, token: str, *, distance: int) -> tuple[str, ...]:
        return ()

    def containing(self, piece: str) -> tuple[str, ...]:
        return ()

    def holding(self, text: str) -> bool:
        return False

    def beginning(self, text: str) -> bool:
        return False


# ---------------------------------------------------------------------------
# Question words.
# ---------------------------------------------------------------------------


def test_question_words_are_a_shipped_section_kept_aside_from_binding() -> None:
    """The section is read by the search alone (``binds: false``): no name's binding moves."""
    from ingence.dictionary.normalize import Normaliser

    section = STORE.aside("question_words")
    assert section is not None and not section.binds
    assert {sense.theme_kind for sense in section.senses} == {QUESTION_KIND}
    words = STORE.aside_surfaces("question_words")
    assert {"does", "which", "between", "effect", "compare", "how", "me", "is", "it"} <= words
    assert all(is_question_word(word, STORE) for word in words)
    # Nothing of it is indexed, recorded or part of the vocabulary's fingerprint.
    assert not any(sense.section == "question_words" for sense in STORE.senses())
    assert not any(record.section == "question_words" for record in STORE.records())
    for word in ("does", "which", "me", "is", "it", "will", "has", "effect", "compare"):
        hit = STORE.look_up(word)
        assert hit is None or hit.sense.section != "question_words", word
    # A name holding question words binds nothing new: each word reads as it did before.
    atoms = list(Normaliser(STORE).atoms("me_is_it_2024"))
    for atom in atoms:
        hit = STORE.look_up(atom.text)
        assert hit is None or hit.sense.section != "question_words", atom.text


def test_a_search_drops_question_words_and_they_take_no_word_of_the_budget() -> None:
    parsed = parse_query("which kelt does compare between vorn", normaliser=NORMALISER, store=STORE)
    assert [term.text for term in parsed.terms] == ["kelt", "vorn"]
    assert parsed.dropped == ("which", "does", "compare", "between")
    # Twelve words of substance and three question words: nothing refused.
    many = " ".join(f"w{index}x" for index in range(MAX_CONCEPTS))
    parsed = parse_query(f"does {many} which how", normaliser=NORMALISER, store=STORE)
    assert len(parsed.terms) == MAX_CONCEPTS and parsed.refused == ()


def test_as_a_question_a_function_word_is_dropped_whatever_binds_it() -> None:
    # ``in`` is an inch in a name; in a question it only places the words around it.
    plain = parse_query("kelt in vorn", normaliser=NORMALISER, store=STORE)
    assert [term.text for term in plain.terms] == ["kelt", "in", "vorn"]
    asked = parse_query("kelt in vorn", normaliser=NORMALISER, store=STORE, question=True)
    assert [term.text for term in asked.terms] == ["kelt", "vorn"] and "in" in asked.dropped
    # A word of a kind that says how (``change``, a statistic) is dropped from a question when the
    # caller says so; the words of what the question is about stay.
    asked = parse_query(
        "does kelt change vorn",
        normaliser=NORMALISER,
        store=STORE,
        question=True,
        dropped_kinds=DEFAULT_DROPPED_KINDS,
    )
    assert [term.text for term in asked.terms] == ["kelt", "vorn"]
    assert asked.dropped == ("does", "change")


def test_a_question_s_own_punctuation_is_not_a_word_s() -> None:
    assert unpunctuated("Does kelt change vorn?") == "Does kelt change vorn"
    assert unpunctuated("kelt, vorn; brisa: at 2.5h.") == "kelt vorn brisa at 2.5h"
    assert unpunctuated("v1.2 and 0.5uM") == "v1.2 and 0.5uM"


# ---------------------------------------------------------------------------
# Class expansion.
# ---------------------------------------------------------------------------


def test_a_class_phrase_is_read_as_one_concept_naming_the_sense_s_members() -> None:
    assert class_term(["nuclear", "stain"], STORE) == "nuclear_stain"
    assert class_term(["nuclear"], STORE) is None
    assert class_term(["and", "or"], STORE) is None
    parsed = parse_query("which nuclear stain with kelt", normaliser=NORMALISER, store=STORE)
    assert [term.text for term in parsed.terms] == ["nuclear stain", "kelt"]
    concepts = expand(parsed, store=STORE, vocabulary=_NoPool())
    stain = concepts[0]
    sense_matches = [m for m in stain.matches if m.route is MatchRoute.SENSE]
    assert [m.term for m in sense_matches] == ["nuclear_stain"]
    (expansion,) = stain.expansions
    assert expansion.how == CLASS_HOW and {"dapi", "hoechst"} <= set(expansion.to)
    assert stain.known


def test_a_member_typed_is_widened_to_the_sense_s_other_spellings_and_labelled() -> None:
    parsed = parse_query("dapi", normaliser=NORMALISER, store=STORE)
    (concept,) = expand(parsed, store=STORE, vocabulary=_NoPool())
    (expansion,) = concept.expansions
    assert expansion.how == SENSE_HOW and "hoechst" in expansion.to and "dapi" not in expansion.to


def test_a_word_nothing_knows_is_unknown() -> None:
    parsed = parse_query("zyxxqvt", normaliser=NORMALISER, store=STORE)
    (concept,) = expand(parsed, store=STORE, vocabulary=_NoPool())
    assert not concept.known and concept.expansions == ()


# ---------------------------------------------------------------------------
# The route, on the planted pool.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    from fixtures.topic_project import build_topic_project

    return build_topic_project(tmp_path_factory.mktemp("ask"))


def _ask(built, q: str) -> dict:
    context = context_for(built.project, settled=None, job_running=False)
    status, body = dispatch(context, "ask", {"q": [q]})
    assert status == 200, body
    return body


def _carrying(built, *words: str) -> set[str]:
    from fixtures.theme_pool import ENGINE_SURFACES

    wanted = {ENGINE_SURFACES.get(word, word) for word in words}
    return {
        built.ids[unit]
        for unit, written in built.plant.documents.items()
        if unit in built.ids and wanted <= {ENGINE_SURFACES.get(w, w) for w in written}
    }


def test_named_experiments_carry_every_word_of_the_question_and_say_how(built) -> None:
    body = _ask(built, "Does keltan change vornish?")
    assert body["read"] == {
        "kept": ["keltan", "vornish"],
        "dropped": ["Does", "change"],
        "unknown": [],
    }
    named = {row["experiment_id"] for row in body["experiments"] if row["score_tier"] == "named"}
    assert named == _carrying(built, "keltan", "vornish")
    for row in body["experiments"]:
        if row["score_tier"] == "named":
            assert {m["word"] for m in row["matched"]} == {"keltan", "vornish"}
            for matched in row["matched"]:
                assert matched["how"].endswith(("in its name", "in a file's name"))
            assert row["dates"]["first"] is None or len(row["dates"]["first"]) == 10
    # The named experiments come first, those naming the words in their own folder before those
    # naming them in a file.
    tiers = [row["score_tier"] for row in body["experiments"]]
    assert tiers == sorted(tiers, key=["named", "associated", "same_theme"].index)


def test_an_unknown_word_is_said_and_not_required(built) -> None:
    body = _ask(built, "which experiments compare keltan and zzqxv?")
    assert body["read"]["unknown"] == ["zzqxv"] and "keltan" in body["read"]["kept"]
    named = {row["experiment_id"] for row in body["experiments"] if row["score_tier"] == "named"}
    assert named == _carrying(built, "keltan")


def _with_associated_tier(built, on: bool):
    from ingence.core.project import Project

    project = Project.open(built.project)
    settings = project.settings
    project.save_settings(
        settings.model_copy(
            update={"search": settings.search.model_copy(update={"associated_tier": on})}
        )
    )


def test_the_associated_tier_is_off_unless_switched_on_and_then_says_why(built) -> None:
    """The associated tier ships off.

    Switched on, an associated experiment is not a named one, and it holds a word that comes
    with the question's."""
    settings_file = built.project / "settings.yaml"
    before = settings_file.read_text() if settings_file.exists() else None
    try:
        body = _ask(built, "keltan vornish")
        assert all(row["score_tier"] != "associated" for row in body["experiments"])
        _with_associated_tier(built, True)
        body = _ask(built, "keltan vornish")
        associated = [row for row in body["experiments"] if row["score_tier"] == "associated"]
        named = {
            row["experiment_id"] for row in body["experiments"] if row["score_tier"] == "named"
        }
        for row in associated:
            assert row["experiment_id"] not in named
            assert row["matched"] and all("comes with" in m["how"] for m in row["matched"])
        _with_associated_tier(built, False)
        body = _ask(built, "keltan vornish")
        assert all(row["score_tier"] != "associated" for row in body["experiments"])
    finally:
        if before is None:
            settings_file.unlink(missing_ok=True)
        else:
            settings_file.write_text(before)


def test_a_blank_or_overlong_question_is_refused_in_words(built) -> None:
    context = context_for(built.project, settled=None, job_running=False)
    status, body = dispatch(context, "ask", {"q": ["   "]})
    assert status == 400 and body["error"]["parameter"] == "q"
    status, body = dispatch(context, "ask", {"q": ["x" * 501]})
    assert status == 400 and "500" in body["error"]["message"]


# ---------------------------------------------------------------------------
# The filters read from a question (item 7).
# ---------------------------------------------------------------------------


def test_a_year_in_the_question_narrows_the_experiments_to_that_year(built) -> None:
    body = _ask(built, "keltan vornish in 2019")
    assert body["filters"]["years"] == [2019]
    assert body["filters"]["types"] is None and body["filters"]["areas"] is None
    assert "2019" in body["read"]["kept"] and "2019" not in body["read"]["unknown"]
    named = {row["experiment_id"] for row in body["experiments"] if row["score_tier"] == "named"}
    dated = {
        built.ids[unit]
        for unit, year in built.plant.years.items()
        if unit in built.ids and year == 2019
    }
    assert named == _carrying(built, "keltan", "vornish") & dated
    for row in body["experiments"]:
        if row["score_tier"] == "named":
            assert {m["word"] for m in row["matched"]} == {"keltan", "vornish"}
            assert row["dates"]["first"] is not None and row["dates"]["first"].startswith("2019")


def test_an_area_in_the_question_keeps_the_experiments_beneath_it(built) -> None:
    body = _ask(built, "keltan vornish kipper")
    assert body["filters"]["areas"] == ["kipper"]
    named = {row["experiment_id"] for row in body["experiments"] if row["score_tier"] == "named"}
    beneath = {
        built.ids[unit]
        for unit, folder in built.folders.items()
        if unit in built.ids and folder.split("/", 1)[0] == "kipper"
    }
    assert named == _carrying(built, "keltan", "vornish") & beneath


def test_a_file_type_word_is_a_filter_only_where_the_run_holds_such_files(built) -> None:
    body = _ask(built, "which images show keltan?")
    assert body["filters"]["types"] == ["image"]
    assert "images" in body["read"]["kept"]
    named = {row["experiment_id"] for row in body["experiments"] if row["score_tier"] == "named"}
    assert named == _carrying(built, "keltan")
    for row in body["experiments"]:
        if row["score_tier"] == "named":
            assert {m["word"] for m in row["matched"]} == {"keltan"}
    # A type word the run's files do not spell is no filter: it is a file-role word, which a
    # question drops as it drops every word that says how rather than what.
    body = _ask(built, "which movies show keltan?")
    assert body["filters"]["types"] is None
    assert "movies" in body["read"]["dropped"] and body["read"]["kept"] == ["keltan"]


def test_a_question_waits_while_another_build_holds_the_search_projection(built) -> None:
    """A question asked while the readouts' search view is building does not read a removed file.

    A question waits for the one lock that a search and the readouts' build hold, and answers once
    the lock is free. Without the wait, the question would read a staging file that the build
    had just removed, and the route would answer with a 500."""
    import threading

    from ingence.api.routes import search as search_routes

    answered: list[int] = []

    def asked() -> None:
        status, _ = dispatch(
            context_for(built.project, settled=None, job_running=False), "ask", {"q": ["kelt"]}
        )
        answered.append(int(status))

    lock = search_routes._one_at_a_time(built.project)
    with lock:
        thread = threading.Thread(target=asked)
        thread.start()
        thread.join(1.0)
        assert thread.is_alive() and answered == [], "the question did not wait for the lock"
    thread.join(120)
    assert answered == [200]
