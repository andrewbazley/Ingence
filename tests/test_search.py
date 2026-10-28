"""Search over what a run understood: units, not files, ranked by a tuple.

Search shows high-level, self-contained units and does not flood the user with files. When a folder
best represents a result, its contents stay closed unless the user asks. Analyses and final-stage
files rank with less priority than experimental folders, and a result can be removed from similar
searches.

Held here, over invented names (``kelt``, ``vorn``, ``zib``, the usual invented words):

- the pieces that need no run (the query's reading, expansion, ranking, folding, capping, hiding);
- a built projection over a small pool: experiments are the folders the rules concluded on, then
  analysis folders, files made to be read, installed programs. A match is lifted to its unit, a
  unit's files stay closed, and nothing is a percentage;
- the defaults: a hide is for everyone, with who recorded it. A product is a sense carrying
  ``product: true``. ``good`` is a quality mark read from a folder's own name and never a claim.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.routes import search as routes
from ingence.core.project import Project
from ingence.dictionary.normalize import AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.search.expand import MatchRoute, variants_of
from ingence.search.hidden import concept_keys, hidden_for, hide, read_hidden, similarity, unhide
from ingence.search.projection import PoolVocabulary, projection_is_current, read_projection
from ingence.search.query import parse_query
from ingence.search.rank import UnitEvidence, UnknownDimensionError, rank
from ingence.search.scale import (
    DEFAULT_SCALE,
    MONTH,
    UnitKind,
    dated_name,
    scale_from_dictionary,
)
from ingence.search.units import (
    DEFAULT_MAX_PRODUCTS,
    FolderFacts,
    HitRow,
    evidence_from_rows,
    present,
    smallest_covering,
)
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def normaliser() -> Normaliser:
    return Normaliser(DictionaryStore.builtin())


# -- query -------------------------------------------------------------------------------


def test_a_query_is_read_with_the_census_tokeniser(normaliser: Normaliser) -> None:
    parsed = parse_query("SubX 800ngmL 2.5h", normaliser=normaliser)
    kinds = [term.atom.kind for term in parsed.terms]
    assert AtomKind.QUANTITY in kinds
    assert parsed.normalizer_version == normaliser.version


def test_a_number_spaced_from_its_unit_is_rejoined(normaliser: Normaliser) -> None:
    spaced = parse_query("500 nM", normaliser=normaliser)
    glued = parse_query("500nM", normaliser=normaliser)
    assert [t.atom.normalised for t in spaced.terms] == [t.atom.normalised for t in glued.terms]


def test_filters_phrases_and_exclusions_are_separated(normaliser: Normaliser) -> None:
    parsed = parse_query('zib "kelt vorn" ext:nd2 -pooled after:2026-01', normaliser=normaliser)
    assert {(f.name, f.value) for f in parsed.filters} == {("ext", "nd2"), ("after", "2026-01")}
    assert parsed.phrases and len(parsed.phrases[0]) == 2
    assert any(term.excluded for term in parsed.terms)


def test_an_unknown_filter_is_read_as_words_not_dropped(normaliser: Normaliser) -> None:
    parsed = parse_query("SubX:GFP", normaliser=normaliser)
    assert not parsed.filters
    assert parsed.terms


def test_function_words_drop_only_where_nothing_binds_them(normaliser: Normaliser) -> None:
    parsed = parse_query("FRAP of kelt", normaliser=normaliser, store=DictionaryStore.builtin())
    assert "of" in parsed.dropped


def test_a_pasted_filename_is_bounded(normaliser: Normaliser) -> None:
    parsed = parse_query(
        " ".join(f"word{i}x" for i in range(30)), normaliser=normaliser, max_concepts=5
    )
    assert len(parsed.concepts) == 5
    assert parsed.refused


# -- expansion ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("plates", ("plate",)), ("assay", ("assays",)), ("dishes", ("dish",)), ("ms", ())],
)
def test_variants_are_spellings_never_stems(typed: str, expected: tuple[str, ...]) -> None:
    assert variants_of(typed) == expected


def test_a_near_spelling_is_verified_not_only_nominated() -> None:
    vocabulary = PoolVocabulary({"abxcd": 3, "abcyd": 2, "keltz": 4, "kelt": 9})
    assert vocabulary.near("abxcd", distance=1) == ()
    assert vocabulary.near("keltx", distance=1) == ("keltz",)


# -- units and ranking (no run needed) ---------------------------------------------------


def _facts(folder_id: str, parent: str | None, ancestors: tuple[str, ...]) -> FolderFacts:
    return FolderFacts(
        folder_id=folder_id,
        parent_id=parent,
        name=folder_id,
        relative_path=folder_id,
        depth=len(ancestors),
        subtree_files=10,
        ancestors=ancestors,
    )


def test_the_smallest_covering_folder_hides_a_parent_that_adds_nothing() -> None:
    facts = {
        "top": _facts("top", None, ()),
        "top/a": _facts("top/a", "top", ("top",)),
    }
    rows = [
        HitRow("top", 0, MatchRoute.TOKEN, named=False, content_only=False, folder_frequency=2),
        HitRow("top", 1, MatchRoute.TOKEN, named=False, content_only=False, folder_frequency=2),
        HitRow("top/a", 0, MatchRoute.TOKEN, named=True, content_only=False, folder_frequency=2),
        HitRow("top/a", 1, MatchRoute.TOKEN, named=False, content_only=False, folder_frequency=2),
    ]
    kept = smallest_covering(evidence_from_rows(rows, facts=facts), facts=facts)
    assert [unit.folder_id for unit in kept] == ["top/a"]


def test_the_ranking_is_a_tuple_and_refuses_an_unknown_dimension() -> None:
    covered_both = UnitEvidence(
        "a",
        frozenset({0, 1}),
        frozenset(),
        frozenset(),
        {0: MatchRoute.TOKEN, 1: MatchRoute.TOKEN},
        {0: 5, 1: 5},
    )
    named_one = UnitEvidence(
        "b", frozenset({0}), frozenset({0}), frozenset(), {0: MatchRoute.SENSE}, {0: 5}
    )
    ordered = rank([named_one, covered_both], folders_in_pool=100)
    assert [unit.evidence.folder_id for unit in ordered] == ["a", "b"]
    assert ordered[0].dimensions["concepts_covered"] == 2
    with pytest.raises(UnknownDimensionError):
        rank([covered_both], folders_in_pool=100, order=["relevance"])


def _series(count: int, *, covered: int = 1) -> tuple[dict[str, FolderFacts], list[UnitEvidence]]:
    facts = {"p": _facts("p", None, ())}
    evidence = []
    concepts = frozenset(range(covered))
    for index in range(count):
        folder = f"p/s{index}"
        facts[folder] = _facts(folder, "p", ("p",))
        evidence.append(
            UnitEvidence(
                folder,
                concepts,
                concepts,
                frozenset(),
                dict.fromkeys(concepts, MatchRoute.TOKEN),
                dict.fromkeys(concepts, 5),
            )
        )
    return facts, evidence


def test_a_series_of_equal_siblings_keeps_its_best_in_view_and_folds_the_rest() -> None:
    facts, evidence = _series(5)
    cards = present(rank(evidence, folders_in_pool=50), facts=facts, fold_at=4, fold_keep=2)
    assert [card.group_parent for card in cards] == [None, None, "p"]
    assert len(cards[2].folded) == 3


def test_the_prototype_s_fold_is_one_setting_away() -> None:
    facts, evidence = _series(5)
    cards = present(rank(evidence, folders_in_pool=50), facts=facts, fold_at=4, fold_keep=0)
    assert len(cards) == 1 and cards[0].group_parent == "p" and len(cards[0].folded) == 5


def test_a_better_match_is_never_folded_among_worse_ones() -> None:
    facts, evidence = _series(6)
    facts["p/best"] = _facts("p/best", "p", ("p",))
    both = frozenset({0, 1})
    evidence.append(
        UnitEvidence(
            "p/best", both, both, frozenset(), dict.fromkeys(both, MatchRoute.TOKEN),
            dict.fromkeys(both, 5),
        )
    )  # fmt: skip
    cards = present(rank(evidence, folders_in_pool=50), facts=facts, fold_at=4, fold_keep=0)
    assert cards[0].unit.evidence.folder_id == "p/best" and not cards[0].folded


def _unit(folder_id: str, kind: UnitKind, *, covered: int = 1, good: bool = False) -> UnitEvidence:
    concepts = frozenset(range(covered))
    return UnitEvidence(
        folder_id,
        concepts,
        frozenset(),
        frozenset(),
        dict.fromkeys(concepts, MatchRoute.TOKEN),
        dict.fromkeys(concepts, 5),
        kind=kind,
        good=good,
    )


def test_an_experiment_ranks_above_a_product_covering_as_much() -> None:
    ordered = rank(
        [_unit("figure", UnitKind.PRODUCT), _unit("exp", UnitKind.EXPERIMENT)], folders_in_pool=9
    )
    assert [unit.evidence.folder_id for unit in ordered] == ["exp", "figure"]
    assert ordered[0].dimensions["unit_kind"] == "experiment"


def test_a_product_covering_more_still_ranks_above_an_experiment() -> None:
    ordered = rank(
        [_unit("exp", UnitKind.EXPERIMENT), _unit("figure", UnitKind.PRODUCT, covered=2)],
        folders_in_pool=9,
    )
    assert ordered[0].evidence.folder_id == "figure"


def test_a_doubted_unit_ranks_below_its_equal_and_is_still_shown() -> None:
    doubted = UnitEvidence(
        "bad", frozenset({0}), frozenset(), frozenset(), {0: MatchRoute.TOKEN}, {0: 5},
        kind=UnitKind.EXPERIMENT, doubted=True,
    )  # fmt: skip
    ordered = rank([doubted, _unit("plain", UnitKind.EXPERIMENT)], folders_in_pool=9)
    assert [unit.evidence.folder_id for unit in ordered] == ["plain", "bad"]


def test_a_unit_named_good_ranks_above_an_equal_and_below_a_better_match() -> None:
    marked = _unit("marked", UnitKind.ANALYSIS, good=True)
    plain = _unit("plain", UnitKind.ANALYSIS)
    ordered = rank([plain, marked], folders_in_pool=9)
    assert [unit.evidence.folder_id for unit in ordered] == ["marked", "plain"]
    assert ordered[0].dimensions["marked_good"] == "named good"
    # A mark is a tie-break among equals: covering more of the query still outranks it.
    wider = _unit("wider", UnitKind.ANALYSIS, covered=2)
    assert rank([marked, wider], folders_in_pool=9)[0].evidence.folder_id == "wider"


def test_an_experiment_is_not_shadowed_by_a_subfolder_covering_the_same() -> None:
    facts = {
        "exp": FolderFacts("exp", None, "exp", "exp", 0, 9, kind=UnitKind.EXPERIMENT),
        "exp/results": FolderFacts("exp/results", "exp", "results", "exp/results", 1, 4, ("exp",)),
    }
    evidence = {
        "exp": _unit("exp", UnitKind.EXPERIMENT),
        "exp/results": _unit("exp/results", UnitKind.FOLDER),
    }
    kept = smallest_covering(evidence, facts=facts)
    assert {unit.folder_id for unit in kept} == {"exp", "exp/results"}


def test_products_past_the_cap_fold_into_one_card() -> None:
    facts = {
        f"plot{index}": FolderFacts(
            f"plot{index}", f"dir{index}", f"plot{index}.pdf", f"dir{index}/plot{index}.pdf", 1, 1,
            (f"dir{index}",), kind=UnitKind.PRODUCT, made_as="plot",
        )
        for index in range(8)
    }  # fmt: skip
    ranked = rank(
        [_unit(f"plot{index}", UnitKind.PRODUCT) for index in range(8)], folders_in_pool=9
    )
    cards = present(ranked, facts=facts, max_products=3)
    assert len(cards) == 4
    assert cards[-1].more_products and len(cards[-1].folded) == 5


@pytest.mark.parametrize(
    ("components", "expected"),
    [
        # A day's date and a word.
        ([("date", None, None), ("word", None, None)], True),
        # A day number, a month word, a year and a word.
        ([("number", None, 4.0), ("word", MONTH, None), ("number", None, 2044.0),
          ("word", None, None)], True),
        # A month and a year is a calendar folder, not an experiment.
        ([("word", MONTH, None), ("number", None, 2044.0)], False),
        # A date and no other word.
        ([("date", None, None)], False),
    ],
)  # fmt: skip
def test_a_dated_name_needs_a_day_and_another_word(
    components: list[tuple[str, str | None, float | None]], expected: bool
) -> None:
    assert dated_name(components) is expected


# -- hiding ------------------------------------------------------------------------------


def test_similar_searches_share_half_their_concepts() -> None:
    first = concept_keys([("photobleaching_recovery", "FRAP"), (None, "SubX")])
    assert (
        similarity(first, concept_keys([("photobleaching_recovery", "FLIP"), (None, "subx")])) == 1
    )
    assert similarity(first, concept_keys([(None, "vorn"), ("photobleaching_recovery", "x")])) < 0.5


def test_a_hidden_result_stays_hidden_until_shown_again(tmp_path: Path) -> None:
    concepts = concept_keys([("photobleaching_recovery", "FRAP"), (None, "SubX")])
    hide(tmp_path, "folder-1", concepts, actor="tester", typed="SubX FRAP")
    assert set(hidden_for(concepts, read_hidden(tmp_path))) == {"folder-1"}
    unrelated = concept_keys([(None, "vorn")])
    assert not hidden_for(unrelated, read_hidden(tmp_path))
    unhide(tmp_path, "folder-1", concepts, actor="tester")
    assert not read_hidden(tmp_path)


def test_a_hide_by_nobody_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="who hid it"):
        hide(tmp_path, "folder-1", frozenset({"word:kelt"}), actor="  ")


def test_a_hide_is_for_everyone_unless_the_scope_says_a_person(tmp_path: Path) -> None:
    concepts = concept_keys([(None, "kelt")])
    hide(tmp_path, "folder-1", concepts, actor="first")
    hides = read_hidden(tmp_path)
    assert set(hidden_for(concepts, hides)) == {"folder-1"}
    assert hidden_for(concepts, hides, actor="second") == {}
    assert hidden_for(concepts, hides, actor="first")["folder-1"].actor == "first"


# -- the dictionary's part (its lines are tested in test_search_vocabulary) --------------


def test_the_parameters_are_read_from_the_dictionary_and_a_veto_leaves_them_empty() -> None:
    scale = scale_from_dictionary(DictionaryStore.builtin())
    assert {"figure", "plot", "presentation", "illustrator_document"} <= set(scale.product_terms)
    assert scale.good_terms == ("good",)
    bare = scale_from_dictionary(DictionaryStore())
    assert bare.product_terms == () and bare.good_terms == ()


# -- a built projection over a pool ------------------------------------------------------


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pool(base: Path) -> Path:
    pool = base / "pool"
    droplet = pool / "20200105 kelt droplet"
    for name in (
        "kelt_vorn_001.nd2",
        "kelt_vorn_002.nd2",
        "Drift_kelt_vorn_001.tif",
        "kelt_vorn_001_results.csv",
        "Drift_kelt_vorn_002.tif",
    ):
        _write(droplet / name)
    _write(droplet / "figures" / "kelt_droplet_figure.pdf")
    repeat = pool / "20200112 kelt droplet repeat"
    for name in ("kelt_vorn_001.nd2", "kelt_vorn_002.nd2", "Drift_kelt_vorn_001.tif"):
        _write(repeat / name)
    # The movies the three analysis folders below were made from.
    zib = pool / "20200119 zib session"
    for name in ("zib_kelt_001.nd2", "zib_kelt_002.nd2", "Drift_zib_kelt_001.tif"):
        _write(zib / name)
    for folder in ("kelt analysis good", "kelt analysis retry", "kelt analysis failed"):
        for number in ("001", "002"):
            _write(pool / folder / f"Traj_zib_kelt_{number}.csv")
    for index in range(8):
        _write(pool / "loose" / f"kelt_summary_plot_{index}.pdf")
    _write(pool / "toolbox" / "bin" / "kelt_helper.txt")
    _write(pool / "toolbox" / "lib" / "kelt_lib.txt")
    _write(pool / "toolbox" / "README")
    _write(pool / "notes" / "plan.txt")
    return pool


def _project(base: Path) -> Project:
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return created


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """One project the read-only tests share (a search builds a projection beside the run)."""
    return _project(tmp_path_factory.mktemp("search"))


def _search(project: Project, text: str, **more: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return routes.search(catalog, project.path, text=text, **more)


def _paths(found: dict[str, Any]) -> list[str | None]:
    return [row["path"] for row in found["results"]]


def test_the_experiments_are_the_folders_the_rules_concluded_on(shared: Project) -> None:
    with open_catalog(shared.path) as catalog:
        built = routes.build(catalog, shared.path)
    counts = built["meta"]["units"]
    assert counts["experiments_from_rules"] >= 3
    assert counts["experiment"] >= 3
    assert counts["program"] == 1
    with open_catalog(shared.path) as catalog:
        units = dict(
            catalog.query(
                f"SELECT f.relative_path, u.unit_kind FROM "
                f"'{built_path(shared) / 'units.parquet'}' u JOIN "
                f"'{built_path(shared) / 'folders.parquet'}' f USING (folder_id)"
            ).fetchall()
        )
    assert units["20200105 kelt droplet"] == "experiment"
    assert units["20200112 kelt droplet repeat"] == "experiment"
    assert units["kelt analysis good"] == "analysis"
    assert units["toolbox"] == "program"
    assert units["notes"] == "folder"


def built_path(project: Project) -> Path:
    with open_catalog(project.path) as catalog:
        assert catalog.run_directory is not None
        found = read_projection(project.path, catalog.run_directory.name)
    assert found is not None
    return found.path


def test_a_search_names_units_and_their_files_stay_closed(shared: Project) -> None:
    found = _search(shared, "kelt droplet")
    assert set(_paths(found)[:2]) == {"20200105 kelt droplet", "20200112 kelt droplet repeat"}
    assert all(row["contents"] == "closed" for row in found["results"])
    # The figure inside the experiment is listed on the experiment's card, not a card of its own.
    assert not any("figures" in (row["path"] or "") for row in found["results"])
    first = next(row for row in found["results"] if row["path"] == "20200105 kelt droplet")
    assert first["unit_kind"] == "experiment"
    assert first["basis"]
    assert first["tags"]["products"] == {"figure": 1}
    assert first["covered"] and not first["missing"]


def test_a_match_inside_a_program_lifts_to_the_program_and_lists_no_file(shared: Project) -> None:
    found = _search(shared, "helper")
    assert _paths(found) == ["toolbox"]
    assert found["results"][0]["unit_kind"] == "program"


def test_nothing_is_a_percentage_and_the_order_is_a_shown_tuple(shared: Project) -> None:
    found = _search(shared, "kelt zib")
    text = json.dumps(found)
    assert "percent" not in text and "score" not in text and "confidence" not in text
    for row in found["results"]:
        assert set(row["ranked_by"]) == {
            "concepts_covered",
            "unit_kind",
            "concepts_named",
            "found_by_step",
            "found_by",
            "weakest_route",
            "marked_good",
            "doubted",
            "content_only",
        }
    assert found["results"][0]["ranked_by"]["concepts_covered"] == 2


def test_a_folder_named_good_ranks_above_an_equal_and_is_never_called_good(shared: Project) -> None:
    found = _search(shared, "kelt analysis")
    analyses = [row for row in found["results"] if row["unit_kind"] == "analysis"]
    order = [row["path"] for row in analyses]
    assert order.index("kelt analysis good") < order.index("kelt analysis retry")
    assert order.index("kelt analysis retry") < order.index("kelt analysis failed")
    marked = {row["path"]: row for row in analyses}
    assert marked["kelt analysis good"]["marked_good"] is True
    assert marked["kelt analysis retry"]["marked_good"] is False
    assert marked["kelt analysis failed"]["doubted"] is True
    assert "good" not in marked["kelt analysis retry"]["ranked_by"].values()


def test_products_on_their_own_are_capped_and_the_rest_fold_into_one_card(shared: Project) -> None:
    found = _search(shared, "summary plot")
    kinds = [row["unit_kind"] for row in found["results"]]
    assert kinds.count("product") == DEFAULT_MAX_PRODUCTS
    assert kinds.count("products") == 1
    overflow = next(row for row in found["results"] if row["unit_kind"] == "products")
    assert overflow["folded_count"] == 3


def test_a_hidden_result_is_left_out_of_similar_searches_and_counted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    found = _search(project, "kelt droplet")
    target = next(row for row in found["results"] if row["path"] == "20200112 kelt droplet repeat")
    routes.hide(
        project.path,
        {
            "unit_id": target["folder_id"],
            "concept_keys": found["concept_keys"],
            "actor": "tester",
            "typed": "kelt droplet",
        },
    )
    again = _search(project, "kelt droplet")
    assert "20200112 kelt droplet repeat" not in _paths(again)
    assert again["hidden"]["count"] == 1
    assert again["hidden"]["units"][0]["by"] == "tester"
    # Hidden for everyone on the project, so another person's search leaves it out too.
    assert "20200112 kelt droplet repeat" not in _paths(_search(project, "droplet kelt", actor="x"))
    shown = _search(project, "kelt droplet", show_hidden=True)
    assert "20200112 kelt droplet repeat" in _paths(shown)
    assert next(r for r in shown["results"] if r["path"] == "20200112 kelt droplet repeat")[
        "hidden_from_similar"
    ]
    # A dissimilar search is untouched.
    assert "20200112 kelt droplet repeat" in _paths(_search(project, "repeat"))
    routes.unhide(
        project.path,
        {"unit_id": target["folder_id"], "concept_keys": found["concept_keys"], "actor": "tester"},
    )
    assert "20200112 kelt droplet repeat" in _paths(_search(project, "kelt droplet"))


def test_a_person_scope_hides_only_for_the_person_who_hid_it(tmp_path: Path) -> None:
    project = _project(tmp_path)
    found = _search(project, "kelt droplet")
    target = found["results"][0]
    routes.hide(
        project.path,
        {"unit_id": target["folder_id"], "concept_keys": found["concept_keys"], "actor": "one"},
    )
    mine = _search(project, "kelt droplet", actor="one", hidden_scope="person")
    theirs = _search(project, "kelt droplet", actor="two", hidden_scope="person")
    assert target["path"] not in _paths(mine)
    assert target["path"] in _paths(theirs)


def test_a_filter_keeps_the_units_holding_such_a_file(shared: Project) -> None:
    found = _search(shared, "kelt ext:tif")
    paths = _paths(found)
    assert "20200105 kelt droplet" in paths
    assert "20200119 zib session" in paths
    assert "kelt analysis good" not in paths


def test_a_search_writes_nothing_into_the_run(shared: Project) -> None:
    def walk() -> dict[str, str]:
        return {
            str(path.relative_to(shared.path)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ("runs", "record")
            for path in sorted((shared.path / folder).rglob("*"))
            if path.is_file()
        }

    before = walk()
    _search(shared, "kelt droplet")
    with open_catalog(shared.path) as catalog:
        routes.suggest(catalog, shared.path, prefix="ke")
    assert walk() == before


def test_a_projection_is_rebuilt_when_the_dictionary_or_the_choices_change(shared: Project) -> None:
    _search(shared, "kelt")
    with open_catalog(shared.path) as catalog:
        assert catalog.run_directory is not None
        projection = read_projection(shared.path, catalog.run_directory.name)
    assert projection is not None
    scale = scale_from_dictionary(DictionaryStore.builtin())
    fingerprint = projection.meta["dictionary_hash"]
    assert projection_is_current(projection, shared.path, dictionary_hash=fingerprint, scale=scale)
    assert not projection_is_current(
        projection, shared.path, dictionary_hash="another", scale=scale
    )
    assert not projection_is_current(
        projection, shared.path, dictionary_hash=fingerprint, scale=DEFAULT_SCALE
    )


def test_the_suggestions_are_the_pool_s_words_most_used_first(shared: Project) -> None:
    _search(shared, "kelt")
    with open_catalog(shared.path) as catalog:
        found = routes.suggest(catalog, shared.path, prefix="ke")
    assert found["words"][0]["token"] == "kelt"
    assert json.dumps(found)


def test_a_container_does_not_cover_a_query_by_adding_up_matches_far_beneath_it(
    tmp_path: Path,
) -> None:
    """A top-level folder is not an answer because unrelated experiments far below it each match a
    word.

    A container of many experiments that the rules did not all place must not "cover" every typed
    word by summing matches deep in different subtrees. Otherwise it outranks the experiments
    themselves.
    """
    pool = _pool(tmp_path)
    _write(pool / "shelf" / "one" / "two" / "three" / "kelt_note.txt")
    _write(pool / "shelf" / "four" / "five" / "six" / "zib_note.txt")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    unbounded = _search(created, "kelt zib", fallback_reach=99)
    assert "shelf" in _paths(unbounded)
    bounded = _search(created, "kelt zib")
    assert "shelf" not in _paths(bounded)
