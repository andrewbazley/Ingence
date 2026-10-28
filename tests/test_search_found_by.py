"""A search result says how it was found.

Beside the results, the search lists the words that come with the query and the months the matched
units fall in.

How a result was found, in this order of strength:

1. the spelling: a name holds the word as typed;
2. a Dictionary sense: a name holds a different spelling of the sense the word names;
3. starts with: a word in a name begins with what was typed;
4. plain name: what was typed stands inside a name, not at a word's start.

A result found more than one way names the strongest and lists the others. The words that come with
the query are scored by pointwise mutual information over unit documents (each name belongs to its
unit: its experiment, analysis folder or program, a figure of its own, else the folder it is in),
with a minimum count so a word seen once does not lead. The by-month strip counts the matched units
by the month of their earliest believed time, the undated counted apart.

Every name here is invented: ``zeph``, ``vantrel``, ``quorl``, ``pelm``, ``brisa``.
"""

from __future__ import annotations

import datetime as dt
import math
import os
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes import search as routes
from ingence.api.routes.decide import define_word
from ingence.core.project import Project
from ingence.scan.census import run_census
from ingence.search.expand import FOUND_BY_ORDER, FoundBy
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
REBIND = "run_20260101T000002Z_222222222222"

#: The four matched folders, each holding the query one way, and the month planted on its files.
MATCHED = {
    "zeph brisa": ("2025-03-10", ["quorl_pelm_1.txt", "quorl_pelm_2.txt", "zephyrine_1.txt"]),
    "vantrel brisa": ("2025-03-20", ["quorl_pelm_1.txt", "skerv_pelm_1.txt"]),
    "zephyrine brisa": ("2025-05-02", ["quorl_pelm_1.txt"]),
    "kozeph brisa": (None, ["pelm_1.txt"]),
}
#: Folders the query does not reach: ``pelm`` is everywhere, ``quorl`` once.
OTHERS = {
    "vell one": ["pelm_1.txt", "quorl_1.txt"],
    "vell two": ["pelm_1.txt"],
    "vell three": ["pelm_1.txt"],
    "vell four": ["pelm_1.txt"],
    "vell five": ["pelm_1.txt"],
    "vell six": ["pelm_1.txt"],
}
#: A time no clock is believed at: before the engine's first credible year.
NO_CLOCK = "1975-06-01"


def _stamp(path: Path, day: str) -> None:
    moment = dt.datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp()
    os.utime(path, (moment, moment))


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for folder, (day, names) in MATCHED.items():
        for name in names:
            path = pool / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
            _stamp(path, day or NO_CLOCK)
    for folder, names in OTHERS.items():
        for name in names:
            path = pool / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
            _stamp(path, "2024-01-15")
    # Two undated units the query reaches only by a word in a file's name.
    for folder, name in (("vell seven", "zeph_note.txt"), ("vell eight", "zeph_memo.txt")):
        late = pool / folder / name
        late.parent.mkdir(parents=True, exist_ok=True)
        late.write_text("x")
        _stamp(late, NO_CLOCK)
    return pool


def _define(project: Project, word: str, *, at: str) -> None:
    with open_catalog(project.path) as catalog:
        define_word(
            catalog,
            project.path,
            actor="a person",
            occurred_at=at,
            word=word,
            answer="zeph_way",
            theme_kind="technique",
        )


def _build(base: Path) -> Project:
    """The pool censused, ``zeph`` and ``vantrel`` taught as one sense, and the names read again."""
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    run_census(created, run_id=CENSUS, started_at="t")
    # ``zeph`` and ``vantrel`` are two spellings of one sense, as a person said.
    _define(created, "zeph", at="2026-01-01T00:00:01Z")
    _define(created, "vantrel", at="2026-01-01T00:00:02Z")
    run_census(created, run_id=REBIND, started_at="t", rebind=True)
    return created


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return _build(tmp_path_factory.mktemp("found_by"))


def _search(project: Project, text: str, **more: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return routes.search(catalog, project.path, text=text, **more)


def _by_path(found: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["path"]: row for row in found["results"]}


def test_the_ways_are_ordered_spelling_sense_starts_with_plain_name() -> None:
    assert FOUND_BY_ORDER[:4] == (
        FoundBy.SPELLING,
        FoundBy.SENSE,
        FoundBy.STARTS_WITH,
        FoundBy.PLAIN_NAME,
    )


def test_each_result_says_how_it_was_found_and_they_are_ordered_so(project: Project) -> None:
    found = _search(project, "zeph")
    rows = _by_path(found)
    assert rows["zeph brisa"]["found_by"] == "spelling"
    assert rows["vantrel brisa"]["found_by"] == "sense"
    assert rows["zephyrine brisa"]["found_by"] == "starts_with"
    # The word as typed is found, so a name holding it only inside a longer word is not offered.
    assert "kozeph brisa" not in rows
    order = [row["path"] for row in found["results"] if row["path"] in MATCHED]
    assert order == ["zeph brisa", "vantrel brisa", "zephyrine brisa"]
    # The ranking shows the way as one of its parts, and says it in words.
    assert rows["vantrel brisa"]["ranked_by"]["found_by"] == "sense"
    assert "zeph_way" in rows["vantrel brisa"]["found_by_said"]
    # Each reason a card gives says its way, with the spelling the name holds.
    reasons = {(line["found_by"], line["matched"]) for line in rows["vantrel brisa"]["why"]}
    assert ("sense", "vantrel") in reasons


def test_a_result_found_more_than_one_way_names_the_strongest_and_lists_the_others(
    project: Project,
) -> None:
    rows = _by_path(_search(project, "zeph"))
    # ``zeph brisa`` holds the word as typed in its own name and at a word's start in a file's
    # (``zephyrine_1.txt``): the spelling, and starts-with beside it. Each entry is counted by its
    # strongest way, so the folder's own name, which a sense also reaches, adds no other way.
    assert rows["zeph brisa"]["found_by"] == "spelling"
    assert rows["zeph brisa"]["also_found_by"] == ["starts_with"]
    assert rows["zephyrine brisa"]["found_by"] == "starts_with"
    assert rows["zephyrine brisa"]["also_found_by"] == []
    assert rows["vantrel brisa"]["also_found_by"] == []
    assert rows["zeph brisa"]["found_by_word"] == [{"word": "zeph", "found_by": "spelling"}]


def test_an_excluded_word_excludes_only_as_written_never_by_a_piece_of_a_name(
    project: Project,
) -> None:
    paths = set(_by_path(_search(project, "brisa -zeph")))
    assert "zeph brisa" not in paths
    # ``kozeph`` and ``zephyrine`` hold the word only inside them: not excluded by it.
    assert {"kozeph brisa", "zephyrine brisa"} <= paths


def test_the_words_that_come_with_the_query_are_scored_by_pmi_with_a_minimum_count(
    project: Project,
) -> None:
    found = _search(project, "zeph")
    words = {row["word"]: row for row in found["co_occurring_words"]}
    # Five units hold the query (three folders by their names, two by a file's name); quorl is in
    # three of them and in one unit beside.
    with open_catalog(project.path) as catalog:
        documents = routes_documents(catalog, project)
    quorl = words["quorl"]
    assert (quorl["units"], quorl["units_in_pool"]) == (3, 4)
    assert quorl["pmi"] == pytest.approx(math.log2(documents * 3 / (5 * 4)), abs=1e-3)
    # brisa is in three matched units and one beside, as quorl is: level, and first by its word.
    assert [row["word"] for row in found["co_occurring_words"][:2]] == ["brisa", "quorl"]
    # skerv is seen once: below the minimum, so it never leads, however rare.
    assert "skerv" not in words
    # The query's own spellings are not words that come with it.
    assert not {"zeph", "vantrel", "zephyrine"} & set(words)
    # pelm is everywhere: no information, never above a word that is not.
    assert words.get("pelm", {"pmi": 0.0})["pmi"] < quorl["pmi"]


def routes_documents(catalog: Any, project: Project) -> int:
    """The unit documents the projection counted, as its meta records them."""
    from ingence.search.projection import read_projection

    assert catalog.run_directory is not None
    built = read_projection(project.path, catalog.run_directory.name)
    assert built is not None
    return int(built.meta["unit_documents"])


def test_the_strip_counts_matched_units_by_month_and_the_undated_apart(project: Project) -> None:
    found = _search(project, "zeph")
    strip = found["by_month"]
    assert strip["units"] == 5
    assert strip["undated"] == 2
    assert strip["months"] == [
        {"month": "2025-03", "units": 2},
        {"month": "2025-04", "units": 0},
        {"month": "2025-05", "units": 1},
    ]
    assert strip["by"] == "own_time"


def test_a_page_of_one_still_counts_every_matched_unit(project: Project) -> None:
    found = _search(project, "zeph", limit=1)
    assert len(found["results"]) == 1
    assert found["by_month"]["units"] == 5


def test_the_typed_route_sends_how_each_was_found_and_what_is_beside_the_results(
    project: Project,
) -> None:
    from ingence.api import v1

    context = v1.context_for(project.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "search", {"q": ["zeph"]})
    assert status == 200, payload
    rows = {row["path"]: row for row in payload["results"]}
    assert rows["zephyrine brisa"]["found_by"] == "starts_with"
    assert rows["zephyrine brisa"]["also_found_by"] == []
    assert rows["zeph brisa"]["found_by_word"] == [{"word": "zeph", "found_by": "spelling"}]
    assert payload["by_month"]["undated"] == 2
    assert payload["co_occurring_words"][0]["word"] == "brisa"


def test_the_minimum_count_is_a_setting(project: Project) -> None:
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE

    written = project.path / DASHBOARD_SETTINGS_FILE
    assert not written.exists()
    written.write_text("search:\n  co_occurring_minimum: 1\n", encoding="utf-8")
    try:
        words = {row["word"] for row in _search(project, "zeph")["co_occurring_words"]}
    finally:
        written.unlink()
    # skerv, seen in one matched unit, is listed once one is enough.
    assert "skerv" in words
    assert "skerv" not in {row["word"] for row in _search(project, "zeph")["co_occurring_words"]}


def test_a_word_found_only_inside_files_is_never_said_to_be_in_a_name() -> None:
    from ingence.search.engine import _found_by_said
    from ingence.search.expand import Concept

    concept = Concept(index=0, typed="zeph", kind="word", matches=())
    assert _found_by_said(FoundBy.SPELLING, concept) == "a name holds 'zeph' as typed"
    assert _found_by_said(FoundBy.SPELLING, concept, in_content=True) == (
        "a file's content holds 'zeph' as typed"
    )


# -- a word found only inside longer words never outranks the word as typed -------------------

#: Words holding ``cell`` inside them, each naming a dated folder of its own.
INSIDE_WORDS = ["excellent", "miscellaneous", "cellar", "parcel", "cellpose", "cellist"]


@pytest.fixture(scope="module")
def inside(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("inside")
    pool = base / "pool"
    for name in ("kelt_1.txt", "kelt_2.txt", "kelt_3.txt"):
        (pool / "cell counts").mkdir(parents=True, exist_ok=True)
        (pool / "cell counts" / name).write_text("x")
    for day, word in enumerate(INSIDE_WORDS, start=10):
        folder = pool / f"shelf {word[:3]}" / f"2025-03-{day} {word} vorn"
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(3):
            (folder / f"brisa_{index}.txt").write_text("x")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    return created


@pytest.mark.parametrize("text", ["cell", "cell kelt"])
def test_the_folder_named_by_the_word_as_typed_is_the_first_result(
    inside: Project, text: str
) -> None:
    found = _search(inside, text)
    assert found["results"][0]["path"] == "cell counts"
    assert found["results"][0]["found_by"] == "spelling"
    # Words holding it only inside them are not offered where the word as typed is found.
    assert not any(row.get("found_by") == "plain_name" for row in found["results"])


def test_plain_name_is_offered_only_where_nothing_else_found_the_word(project: Project) -> None:
    rows = _by_path(_search(project, "ozep"))
    # Nothing holds ``ozep`` as written, as a sense or at a word's start: inside a name, then.
    assert rows["kozeph brisa"]["found_by"] == "plain_name"
    assert rows["kozeph brisa"]["found_by_said"] == "'ozep' stands inside a name"


# -- the order of results a search already found stays ----------------------------------------


@pytest.fixture(scope="module")
def plural(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("plural")
    pool = base / "pool"
    for name in ("kelt_1.txt", "kelt_2.txt"):
        (pool / "plate notes").mkdir(parents=True, exist_ok=True)
        (pool / "plate notes" / name).write_text("x")
    experiment = pool / "shelf" / "2025-03-10 plates vorn"
    experiment.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        (experiment / f"brisa_{index}.txt").write_text("x")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    return created


def test_a_plural_bound_to_the_sense_keeps_experiments_first(plural: Project) -> None:
    """``plates`` and ``plate`` are one sense: the word itself, so the experiment leads the plain
    folder, and only the finer way breaks ties after.
    """
    found = _search(plural, "plate")
    first = found["results"][0]
    assert first["path"] == "shelf/2025-03-10 plates vorn"
    assert first["unit_kind"] == "experiment"
    assert first["ranked_by"]["found_by_step"] == "word"
    assert first["found_by"] == "sense"


def test_the_coarse_step_comes_before_the_kind_and_the_finer_way_after_the_name() -> None:
    from ingence.search.rank import DEFAULT_ORDER

    assert DEFAULT_ORDER[:5] == (
        "concepts_covered",
        "found_by_step",
        "unit_kind",
        "concepts_named",
        "found_by",
    )


def test_a_three_letter_word_is_found_inside_longer_words_only_where_the_setting_says(
    project: Project,
) -> None:
    """The default is four letters. Three cost seconds on a large census, and the box asks after
    three letters typed. A person may set three.
    """
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE, SearchSettings

    assert SearchSettings().inside_min_length == 4
    assert _search(project, "zep")["total_units"] == 0
    written = project.path / DASHBOARD_SETTINGS_FILE
    written.write_text("search:\n  inside_min_length: 3\n", encoding="utf-8")
    try:
        found = _search(project, "zep")
    finally:
        written.unlink()
    # Four folders begin a name's word with it, each as broadly: a series of four, so two are cards
    # and two fold into their parent's. Which is which is the tie the next test holds. Here, every
    # one of them is found, and found by a word's start.
    assert _ranked_paths(found) == ["vell eight", "vell seven", "zeph brisa", "zephyrine brisa"]
    assert {row["found_by"] for row in found["results"]} == {"starts_with"}


def _ranked_paths(found: dict[str, Any]) -> list[str]:
    """Every unit a page holds, in its order: each card's path, then the units folded into it."""
    order: list[str] = []
    for row in found["results"]:
        if row["path"] is not None:
            order.append(row["path"])
        order.extend(str(member["name"]) for member in row["folded"])
    return order


def test_two_builds_of_one_pool_show_the_same_units_in_the_same_order(tmp_path: Path) -> None:
    """A tie is ordered by what every build of the pool shares (the unit's path), never by an
    identifier, which differs with where the pool sits: what a fold keeps in view is one choice."""
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE

    orders = []
    for place in ("first", "second"):
        (tmp_path / place).mkdir()
        built = _build(tmp_path / place)
        (built.path / DASHBOARD_SETTINGS_FILE).write_text(
            "search:\n  inside_min_length: 3\n", encoding="utf-8"
        )
        found = _search(built, "zep")
        orders.append([(row["path"], row["position"]) for row in found["results"]])
        orders.append(_ranked_paths(found))
    assert orders[0] == orders[2]
    assert orders[1] == orders[3] == ["vell eight", "vell seven", "zeph brisa", "zephyrine brisa"]


def test_a_tie_at_the_candidate_cut_keeps_the_units_first_by_path(project: Project) -> None:
    """More units matched than are ranked: among equals, the ones kept are first by path.

    The pool's own folder is a candidate too (a match in a loose file counts within two levels
    above it) and its path sorts first; the smallest covering folder then sets it aside.
    """
    from ingence.search import engine

    with open_catalog(project.path) as catalog:
        normaliser, store = routes._reader(catalog, project.path)
        found = engine.search(
            catalog,
            project.path,
            "zep",
            normaliser=normaliser,
            store=store,
            max_candidates=3,
            inside_min_length=3,
        )
    assert [row["path"] for row in found["results"]] == ["vell eight", "vell seven"]


def test_two_roots_holding_one_path_order_by_their_place_in_the_project(tmp_path: Path) -> None:
    """Two roots may each hold ``zeph brisa``: the tie is ordered by the root's place in the
    project's list of roots, which every build shares, before any identifier."""
    orders = []
    for place in ("first", "second", "third"):
        base = tmp_path / place
        roots = []
        for root, name in (("north", "kelt_1.txt"), ("south", "kelt_2.txt")):
            folder = base / root / "zeph brisa"
            folder.mkdir(parents=True)
            (folder / name).write_text("x")
            roots.append(base / root)
        built = Project.create(base / "project", created_at="t", roots=roots)
        run_census(built, run_id=CENSUS, started_at="t")
        with open_catalog(built.path) as catalog:
            held = dict(
                catalog.query(
                    "SELECT e.entry_id, r.mount_path FROM entries e JOIN roots r USING (root_id)"
                ).fetchall()
            )
        found = _search(built, "zeph")
        orders.append([held[row["folder_id"]].rsplit("/", 1)[-1] for row in found["results"]])
    assert orders == [["north", "south"]] * 3


def test_a_tie_reads_a_path_in_one_unicode_form() -> None:
    """A name read decomposed and the same name read precomposed order alike against another."""
    from ingence.search.rank import UnitEvidence, rank

    def unit(folder_id: str, path: str) -> UnitEvidence:
        return UnitEvidence(
            folder_id=folder_id,
            covered=frozenset({0}),
            named=frozenset({0}),
            content_only=frozenset(),
            best_route={},
            folder_frequency={0: 1},
            path=path,
        )

    decomposed = "zéph brisa"
    precomposed = "zéph brisa"
    other = unit("entry_b", "zfal brisa")
    orders = [
        [
            ranked.evidence.folder_id
            for ranked in rank([unit("entry_a", spelled), other], folders_in_pool=10)
        ]
        for spelled in (decomposed, precomposed)
    ]
    assert orders[0] == orders[1] == ["entry_b", "entry_a"]
