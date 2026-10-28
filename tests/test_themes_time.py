"""Themes over time, the year profile, who else and the undated, over the planted pool.

A theme's years, its words' succession, a year's profile and the people who wrote the same words are
read from each experiment's theme, year, words and person, by stated rules, and reproduce what was
planted. Every word and person is invented (``fixtures/theme_pool.py``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import (
    HANDED_FROM,
    HANDED_TO,
    PLANT,
    SENSES,
    UNDATED,
    theme_documents,
)
from ingence.core.settings import ThemeSettings
from ingence.themes import themes_engine
from ingence.themes.time import (
    substitutes,
    theme_timelines,
    undated,
    who_else,
    year_profile,
)

SETTINGS = ThemeSettings()


@pytest.fixture(scope="module")
def read():
    plant = theme_documents()
    result = themes_engine().compute(plant.documents, senses=SENSES, settings=SETTINGS)
    placed = {u: a.theme_id for u, a in result.assignments.items() if a.theme_id is not None}
    words = {u: frozenset(ws) for u, ws in plant.documents.items()}
    display = {w: SENSES.get(w, w).replace("_", " ") for ws in words.values() for w in ws}
    labels = {s.theme_id: s.label for s in result.themes}
    broad = {b.broad_id: b.label for b in result.broad}
    broad_of = {s.theme_id: s.broad_id for s in result.themes}
    timelines = theme_timelines(
        placed, labels, broad, broad_of, plant.years, words, display, plant.people, SETTINGS
    )
    theme_of_plant = {plant.planted[u]: t for u, t in placed.items()}
    return plant, placed, words, display, labels, timelines, theme_of_plant


def test_each_theme_begins_and_ends_in_its_planted_years(read) -> None:
    _, _, _, _, _, timelines, theme_of_plant = read
    by_theme = {row["theme"]: row for row in timelines}
    for planted, (_, (first, last), _) in PLANT.items():
        row = by_theme[theme_of_plant[planted]]
        assert (row["first"], row["last"]) == (first, last), planted
        assert sum(int(n) for n in row["by_year"].values()) + row["undated"] == row["experiments"]


def test_going_and_direction(read) -> None:
    _, _, _, _, _, timelines, theme_of_plant = read
    by_theme = {row["theme"]: row for row in timelines}
    stopped = by_theme[theme_of_plant["t0"]]
    assert stopped["going"] is False and stopped["direction"] == "stopped"
    new = by_theme[theme_of_plant["t5"]]
    assert new["going"] is True and new["direction"] == "new"
    for row in timelines:
        assert row["direction"] in {"rising", "falling", "steady", "new", "stopped"}


def test_a_word_succeeded_by_another_is_a_succession_and_a_substitute(read) -> None:
    plant, placed, words, display, _, timelines, theme_of_plant = read
    row = {r["theme"]: r for r in timelines}[theme_of_plant["t4"]]
    chain = [step["word"] for step in row["succession"]]
    assert chain == [HANDED_FROM, HANDED_TO]
    pairs = substitutes(placed, plant.years, words, display, SETTINGS)
    assert any(p["earlier"] == HANDED_FROM and p["later"] == HANDED_TO for p in pairs)


def test_the_year_profile_reproduces_planted_beginnings_and_ends(read) -> None:
    plant, placed, words, display, labels, _, theme_of_plant = read
    story = year_profile(placed, labels, plant.years, words, display, SETTINGS)
    for planted, (_, (first, last), _) in PLANT.items():
        label = labels[theme_of_plant[planted]]
        assert label in story[str(first)]["began"], planted
        if str(last + 1) in story:
            assert label in story[str(last + 1)]["last_seen_year_before"], planted
    # The year a theme peaks over its usual share names it among the most.
    assert any(story[y]["most"] for y in story)
    assert all(lift < 1 for y in story for _, _, lift in story[y]["under"])


def test_a_line_handed_on_between_two_people(read) -> None:
    _, _, _, _, _, timelines, theme_of_plant = read
    row = {r["theme"]: r for r in timelines}[theme_of_plant["t4"]]
    assert row["shared"] is True
    owners = PLANT["t4"][2]
    assert [(h["earlier"], h["later"]) for h in row["handed_on"]] == [owners]


def test_who_else_finds_a_word_two_people_share(read) -> None:
    plant, _, words, display, _, _, _ = read
    found = who_else(
        sorted(plant.documents),
        plant.years,
        words,
        display,
        frozenset(SENSES),
        plant.people,
        {u: u for u in plant.documents},
        SETTINGS,
    )
    shared = {row["word"]: row for row in found["shared_words"]}
    hoben = SENSES["hoben"].replace("_", " ")
    assert hoben in shared
    assert set(shared[hoben]["people"]) == set(PLANT["t4"][2])
    assert shared[hoben]["sense"] is True
    people = {row["person"]: row for row in found["people"]}
    assert set(people) == {"kipper", "tovald", "zandri"}
    assert people["kipper"]["first"] == 2019


def test_the_undated_are_listed(read) -> None:
    plant, *_ = read
    assert undated(sorted(plant.documents), plant.years) == sorted(UNDATED)


def test_words_handed_on_are_found_without_a_square_of_the_vocabulary() -> None:
    """Words handed on are found with sparse matrices, a band of rows at a time.

    Over 6,000 experiments the dense word-by-word matrices took 38 seconds and 865 MB. The sparse
    form gives the same pairs in a bounded memory.
    """
    import random
    import tracemalloc

    chooser = random.Random(1)
    syllables = ("ka", "lo", "mi", "nu", "pe", "ro", "si", "tu", "va", "ze", "bo", "di", "fu")
    vocabulary = sorted({"".join(chooser.choice(syllables) for _ in range(4)) for _ in range(9000)})
    vocabulary = vocabulary[:4000]
    weights = [1 / (rank + 1) for rank in range(len(vocabulary))]
    units = [f"u{number:05d}" for number in range(6000)]
    words = {u: frozenset(chooser.choices(vocabulary, weights, k=15)) for u in units}
    years = {u: 2015 + chooser.randrange(10) for u in units}
    themes = {u: chooser.randrange(40) for u in units}
    display = {w: w for w in vocabulary}
    tracemalloc.start()
    try:
        substitutes(themes, years, words, display, SETTINGS)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 150 * 1024 * 1024, peak
