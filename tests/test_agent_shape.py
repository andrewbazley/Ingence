"""The privacy shaper: four levels, aliases, and no survivor.

Every name here is invented. The sample collection that the zero-survivor test reads is the
shipped one (``ingence demo``): invented members, invented folders.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from fixtures.agent_sample import (
    TOPIC_NAME,
    SampleProject,
    build_sample_project,
    checkable_names,
    names_of_the_pool,
    survivors,
)
from ingence.agent.shape import (
    LEVELS,
    POLICY,
    Aliases,
    Shaper,
    Vocabulary,
    compact_json,
    level_named,
)
from ingence.api import v1 as api_v1
from ingence.api.v1 import examples
from ingence.cli.shareable import Placeholders

_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2}([T ].*)?)?)?$")
_WORD = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*")
_RUN = "run_20260101T000002Z_222222222222"


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    return Vocabulary.shipped()


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> SampleProject:
    return build_sample_project(tmp_path_factory.mktemp("agent_shape"))


def _examples() -> dict[str, dict[str, Any]]:
    """The invented answers of the stub routes, as the routes send them."""
    return {
        "overview": examples.overview_example(_RUN).model_dump(mode="json"),
        "areas": examples.areas_example(_RUN).model_dump(mode="json"),
        "assumptions": examples.assumptions_example(_RUN).model_dump(mode="json"),
        "checks": examples.checks_example(_RUN, limit=10).model_dump(mode="json"),
        "goals": examples.goals_example(_RUN, phase=None).model_dump(mode="json"),
        "ask": examples.ask_example(_RUN, "timecourse with analysis").model_dump(mode="json"),
        "experiments/story": examples.experiment_story_example(
            _RUN, "entry_2a7f10c4e9b1"
        ).model_dump(mode="json"),
    }


def _leaves(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    return [value]


def _texts(value: Any) -> list[str]:
    return [leaf for leaf in _leaves(value) if isinstance(leaf, str)]


# -- the levels --------------------------------------------------------------------------------


def test_the_levels_are_four_least_first_and_a_wrong_one_is_refused_in_words() -> None:
    assert LEVELS == ("counts", "words", "names", "paths")
    assert level_named(" Words ") == "words"
    with pytest.raises(ValueError, match="no level 'everything'; the levels are counts, words"):
        level_named("everything")


def test_counts_sends_numbers_and_dates_only(vocabulary: Vocabulary) -> None:
    shaper = Shaper("counts", vocabulary=vocabulary)
    for route, payload in _examples().items():
        shaped = shaper.shape(route, payload)
        assert shaped, route
        for leaf in _leaves(shaped):
            assert not isinstance(leaf, bool), (route, leaf)
            if isinstance(leaf, str):
                assert _DATE.match(leaf), (route, leaf)
            else:
                assert leaf is None or isinstance(leaf, int | float), (route, leaf)
    assert len(shaper.aliases) == 0  # an alias is a word: none at counts


def test_words_adds_aliases_closed_words_and_public_words_but_no_name(
    vocabulary: Vocabulary,
) -> None:
    shaper = Shaper("words", vocabulary=vocabulary)
    overview = shaper.shape("overview", _examples()["overview"])
    areas = overview["areas"]
    assert [area["id"] for area in areas] == ["A1", "A2", "A3", "A4"]
    assert all("name" not in area for area in areas)
    assert areas[0]["kind"] == "instrument" and areas[1]["kind_assumed"] is True
    # A theme's label is made of the Dictionary's meanings only; the goal is the person's own.
    assert overview["themes_top"][0]["label"] == "timecourse imaging"
    assert overview["goals"][0]["text"] == "HeLa timecourses with analysis"
    assert overview["goals"][0]["id"] == "G1"
    assert overview["file_types"][0] == {
        "type_id": "nd2",
        "label": "ND2 image",
        "category": "instrument",
        "files": 1180,
        "share": 0.47,
    }
    text = compact_json(overview)
    assert "entry_" not in text and "Confocal" not in text and "Plate reader" not in text


def test_a_label_with_a_word_outside_the_dictionary_loses_that_word_at_words_only() -> None:
    vocabulary = Vocabulary(["imaging", "timecourse"])
    payload = {"themes_top": [{"id": "theme_1", "label": "kelt imaging 2024", "experiments": 3}]}
    at_words = Shaper("words", vocabulary=vocabulary).shape("overview", payload)
    assert at_words["themes_top"][0]["label"] == "imaging 2024"
    at_names = Shaper("names", vocabulary=vocabulary).shape("overview", payload)
    assert at_names["themes_top"][0]["label"] == "kelt imaging 2024"
    nothing = {"themes_top": [{"id": "theme_2", "label": "kelt vorn", "experiments": 1}]}
    assert Shaper("words", vocabulary=vocabulary).shape("overview", nothing)["themes_top"] == [
        {"id": "T1", "label": None, "experiments": 1}
    ]


def test_names_adds_names_and_sentences_and_paths_adds_paths(vocabulary: Vocabulary) -> None:
    given = _examples()
    at_words = Shaper("words", vocabulary=vocabulary).shape("assumptions", given["assumptions"])
    at_names = Shaper("names", vocabulary=vocabulary).shape("assumptions", given["assumptions"])
    at_paths = Shaper("paths", vocabulary=vocabulary).shape("assumptions", given["assumptions"])
    first_words, first_names, first_paths = (
        shaped["assumptions"][0] for shaped in (at_words, at_names, at_paths)
    )
    assert "text" not in first_words and first_words["examples"] == []
    assert first_names["text"] == "Files ending .nd2 are microscope images."
    assert first_names["examples"] == []
    assert first_paths["examples"][0] == {
        "label": "Confocal/2024-03-12 HeLa DMSO timecourse/raw/t001.nd2"
    }
    # An entry's identifier is never sent, at any level; how to reject is not a read.
    for shaped in (at_words, at_names, at_paths):
        assert "entry_" not in compact_json(shaped)
        assert "reject" not in shaped["assumptions"][0]
    areas = Shaper("names", vocabulary=vocabulary).shape("overview", given["overview"])["areas"]
    names = [area["name"] for area in areas]
    assert names == ["Confocal", "Imaging 2024", "Plate reader", "Shared"]


def test_an_assumptions_why_part_never_reaches_the_brief(vocabulary: Vocabulary) -> None:
    from ingence.readouts import WHY_SEPARATOR

    given = _examples()["assumptions"]
    given["assumptions"][0]["text"] = (
        f"Read as log: 5 files. Placed by the flow.{WHY_SEPARATOR}The full rule."
    )
    for level in ("names", "paths"):
        shaped = Shaper(level, vocabulary=vocabulary).shape("assumptions", given)
        text = shaped["assumptions"][0]["text"]
        assert text == "Read as log: 5 files. Placed by the flow."
        assert "full rule" not in compact_json(shaped) and "\n" not in text


def test_no_folder_path_from_a_check_s_examples_reaches_names_or_words(
    vocabulary: Vocabulary,
) -> None:
    from ingence.api.v1.checks import _check
    from test_checks_questions import _ranked_question

    check = _check(_ranked_question()).model_dump(mode="json")
    payload = {"total": 1, "checks": [check]}
    for level in ("words", "names"):
        shaped = Shaper(level, vocabulary=vocabulary).shape("checks", payload)
        assert "exp_A/" not in compact_json(shaped), level
        assert "/" not in " ".join(c.get("text", "") for c in shaped["checks"]), level


def test_a_field_the_policy_does_not_know_is_dropped_at_every_level(
    vocabulary: Vocabulary,
) -> None:
    payload = _examples()["overview"]
    payload["surprise"] = "a field another track adds later"
    payload["areas"][0]["surprise_too"] = "kelt"
    payload["counts"]["notes"] = ["vorn"]
    for level in LEVELS:
        shaped = Shaper(level, vocabulary=vocabulary).shape("overview", payload)
        text = compact_json(shaped)
        assert "surprise" not in text and "kelt" not in text and "vorn" not in text, level


def test_a_date_is_a_day_a_year_or_a_zoned_time_and_nothing_with_words_after_it() -> None:
    shaper = Shaper("counts", vocabulary=Vocabulary())
    for text in ("2024", "2024-03", "2024-03-12", "2024-03-12T10:00:00Z", "2024-03-12 10:00:00"):
        assert shaper.shape("overview", {"date_range": {"first": text}}) == {
            "date_range": {"first": text}
        }, text
    for text in ("2024-03-12 kelt vorn", "2024-03-12T", "kelt", "20240312x"):
        assert shaper.shape("overview", {"date_range": {"first": text}}) == {"date_range": {}}


def test_a_closed_field_holding_a_word_of_the_data_is_dropped_below_names() -> None:
    vocabulary = Vocabulary(["nd2"])
    payload = {
        "checks": [
            {
                "id": "c1",
                "kind": "file_type",
                "settles_unit": "files",
                "default": {"value": "kelt vorn"},
                "options": [{"value": "yes"}, {"value": "kelt"}, {"value": "raw_only"}],
            }
        ]
    }
    at_words = Shaper("words", vocabulary=vocabulary).shape("checks", payload)["checks"][0]
    assert at_words["kind"] == "file_type" and at_words["settles_unit"] == "files"
    assert at_words["default"] == {}
    assert at_words["options"] == [{"value": "yes"}, {"value": "raw_only"}]
    at_names = Shaper("names", vocabulary=vocabulary).shape("checks", payload)["checks"][0]
    assert at_names["default"] == {"value": "kelt vorn"}
    files = {"rows": [{"extension": "nd2"}, {"extension": "kelt"}]}
    shaped = Shaper("words", vocabulary=vocabulary).shape("experiments/files", files)
    assert shaped["rows"] == [{"extension": "nd2"}]


def test_a_topics_member_terms_are_filtered_and_an_experiments_through_never_goes_out() -> None:
    payload = {
        "topics": [
            {
                "id": "t1",
                "name": "My own topic",
                "members": [{"kind": "core", "terms": ["kelt", "imaging"], "state": "nothing"}],
                "experiments": [{"id": "entry_a", "files": 3, "through": ["entry_a", "entry_b"]}],
            }
        ]
    }
    for level in ("words", "names", "paths"):
        shaped = Shaper(level, vocabulary=Vocabulary(["imaging"])).shape("topics", payload)
        topic = shaped["topics"][0]
        assert "through" not in topic["experiments"][0], level
        assert "entry_" not in compact_json(shaped), level
    at_words = Shaper("words", vocabulary=Vocabulary(["imaging"])).shape("topics", payload)
    assert at_words["topics"][0]["members"][0]["terms"] == ["imaging"]
    at_names = Shaper("names", vocabulary=Vocabulary(["imaging"])).shape("topics", payload)
    assert at_names["topics"][0]["members"][0]["terms"] == ["kelt", "imaging"]


def test_a_server_errors_words_are_its_type_below_names_and_read_by_the_backstop(
    tmp_path: Path,
) -> None:
    placeholders = Placeholders(tmp_path / "invented project")
    placeholders.root("/invented/pool")
    quoted = f"FileNotFoundError: no such file {tmp_path / 'invented project' / 'x'}"
    refused = {"api_version": "1.0", "error": {"code": "server_error", "message": quoted}}
    for level in ("counts", "words"):
        shaper = Shaper(level, vocabulary=Vocabulary(), placeholders=placeholders)
        assert shaper.shape("overview", refused)["error"] == {
            "code": "server_error",
            "message": "FileNotFoundError",
        }
    shaper = Shaper("names", vocabulary=Vocabulary(), placeholders=placeholders)
    said = shaper.shape("overview", refused)["error"]["message"]
    assert said == "FileNotFoundError: no such file <project>/..."
    # A route's own refusal keeps its words at every level, read by the backstop too.
    own = {"error": {"code": "unknown_run", "message": "no run under /invented/pool/x"}}
    assert (
        Shaper("counts", vocabulary=Vocabulary(), placeholders=placeholders).shape("overview", own)[
            "error"
        ]["message"]
        == "no run under <root 1>/..."
    )


def test_every_route_of_the_policy_is_a_route_the_api_declares() -> None:
    api_v1.load_routes()
    assert set(POLICY) <= set(api_v1.ROUTES)


def test_an_error_answer_passes_at_every_level_in_the_engines_words(
    vocabulary: Vocabulary,
) -> None:
    refused = {"api_version": "1.0", "error": {"code": "no_published_run", "message": "index it"}}
    for level in LEVELS:
        assert Shaper(level, vocabulary=vocabulary).shape("overview", refused) == {
            "error": {"code": "no_published_run", "message": "index it"}
        }


def test_a_route_the_shaper_does_not_know_is_refused() -> None:
    with pytest.raises(ValueError, match="knows no route 'entries'"):
        Shaper("words", vocabulary=Vocabulary()).shape("entries", {"rows": []})


# -- the aliases -------------------------------------------------------------------------------


def test_aliases_are_given_once_in_order_of_first_sight_and_the_key_maps_back(
    tmp_path: Path,
) -> None:
    aliases = Aliases()
    assert aliases.alias("E", "entry_aaa") == "E1"
    assert aliases.alias("E", "entry_bbb") == "E2"
    assert aliases.alias("E", "entry_aaa") == "E1"
    assert aliases.alias("A", "entry_aaa") == "A1"  # a family of its own
    assert aliases.identifier("e2") == "entry_bbb" and aliases.identifier("E9") is None
    assert aliases.is_alias("E12") and not aliases.is_alias("X1") and not aliases.is_alias("E")
    assert aliases.as_mapping() == {
        "experiment": {"E1": "entry_aaa", "E2": "entry_bbb"},
        "area": {"A1": "entry_aaa"},
    }
    aliases.write(tmp_path / "brief.aliases.json")
    assert json.loads((tmp_path / "brief.aliases.json").read_text()) == aliases.as_mapping()


def test_one_experiment_keeps_one_alias_across_routes(vocabulary: Vocabulary) -> None:
    shaper = Shaper("words", vocabulary=vocabulary)
    given = _examples()
    story = shaper.shape("experiments/story", given["experiments/story"])
    ask = shaper.shape("ask", given["ask"])
    goals = shaper.shape("goals", given["goals"])
    first = story["experiment_id"]
    assert first == "E1"
    identifier = shaper.aliases.identifier(first)
    for shaped in (ask, goals):
        for leaf in _texts(shaped):
            assert leaf != identifier
    # The same identifier, wherever it is met again, is the same alias.
    assert shaper.aliases.alias("E", "entry_2a7f10c4e9b1") == "E1"


# -- the vocabulary and the backstop -----------------------------------------------------------


def test_the_vocabulary_keeps_public_words_numbers_and_the_persons_own_words() -> None:
    vocabulary = Vocabulary(["Imaging", "plate reader"])
    assert vocabulary.knows("imaging") and vocabulary.knows("IMAGING")
    assert vocabulary.knows("2024") and not vocabulary.knows("kelt")
    assert vocabulary.knows("kelt", own=frozenset({"kelt"}))
    assert vocabulary.filter_label("Kelt imaging 2024 reader") == "imaging 2024 reader"
    assert vocabulary.filter_label("kelt vorn") is None
    assert vocabulary.filter_words(["kelt", "imaging", 3]) == ["imaging"]
    assert vocabulary.knows("instrument")  # the engine's closed vocabulary is public


def test_the_shipped_vocabulary_is_large_and_holds_no_user_word(vocabulary: Vocabulary) -> None:
    assert len(vocabulary) > 1000


def test_the_backstop_reads_the_finished_text_for_a_root_or_the_project(tmp_path: Path) -> None:
    placeholders = Placeholders(tmp_path / "invented project")
    placeholders.root("/invented/pool")
    shaper = Shaper("paths", vocabulary=Vocabulary(), placeholders=placeholders)
    finished = shaper.finish(f"files under /invented/pool/kelt and {tmp_path / 'invented project'}")
    assert finished == "files under <root 1>/... and <project>"
    assert shaper.replaced == 2
    assert Shaper("paths", vocabulary=Vocabulary()).finish("as it was") == "as it was"


# -- the zero-survivor test, on the sample collection ------------------------------------------


def _every_route_shaped(sample: SampleProject, level: str) -> tuple[str, int]:
    """Every route of the policy answered by the sample project, shaped and finished, as one
    text; and how many names the backstop had to replace."""
    context = api_v1.context_for(sample.project.path, settled=None, job_running=False)
    shaper = Shaper.for_project(sample.project, level)  # type: ignore[arg-type]
    _, experiments = api_v1.dispatch(context, "experiments", {"limit": ["5"]})
    first = str(experiments["rows"][0]["id"])
    queries: dict[str, dict[str, list[str]]] = {
        "ask": {"q": ["nuclear stain with analysis"]},
        "experiments/story": {"id": [first]},
        "experiments/files": {"experiment": [first]},
        "themes": {"limit": ["50"]},
    }
    pieces = []
    for route in POLICY:
        _, payload = api_v1.dispatch(context, route, queries.get(route, {}))
        pieces.append(compact_json(shaper.shape(route, payload)))
    return shaper.finish("\n".join(pieces)), shaper.replaced


@pytest.mark.parametrize("level", ["counts", "words"])
def test_no_file_folder_or_person_name_of_the_pool_survives_at_counts_or_words(
    sample: SampleProject, level: str
) -> None:
    names, people = names_of_the_pool(sample)
    vocabulary = Vocabulary.for_project(sample.project.path)
    routes_text, replaced = _every_route_shaped(sample, level)
    checked = checkable_names(names, vocabulary) | checkable_names(people, Vocabulary())
    assert len(checked) > 500 and len(people) >= 10
    assert replaced == 0  # the first pass caught everything: the backstop found nothing
    assert survivors(routes_text, checked) == []
    # The fixture's topic (an entry, an experiment and a core member) was read and shaped.
    assert (TOPIC_NAME in routes_text) == (level == "words")
    # The names left out of the check are single public words.
    assert all(len(_WORD.findall(name)) <= 1 for name in names - checked if len(name) >= 3)


def test_at_paths_the_pools_folders_do_go_out_but_never_an_absolute_path(
    sample: SampleProject,
) -> None:
    routes_text, replaced = _every_route_shaped(sample, "paths")
    assert replaced == 0
    assert str(sample.pool) not in routes_text
    assert str(sample.project.path) not in routes_text
    names, _ = names_of_the_pool(sample)
    checked = checkable_names(names, Vocabulary.for_project(sample.project.path))
    assert survivors(routes_text, checked)  # a relative path is what the level is for
