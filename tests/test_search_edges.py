"""Search's edges, each the minimal case of a class of edge.

A filter on a query matching nothing. A date typed as a person types one, or unreadably. A date
compared against the earliest credible time rather than a clock the rule set aside. An excluded
filter with a quoted value. ``in:`` over a product. A result brought back from a similar search. A
card's span that sets aside what the time rule set aside. A good mark a name cancels. A raw file
whose name holds a product word. ``stage:`` read from the stage rules alone. The candidates kept by
how much of the query they cover. A "why" that never gives a reason the ranking did not count. First
searches arriving together. Every name is invented.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from fixtures.revision_4 import write_revision
from ingence.api.routes import search as routes
from ingence.core.project import Project
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.search import engine
from ingence.search.query import parse_query, read_day
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

#: 1 June 1985, before any time the earliest-credible-time rule believes.
_LONG_AGO = 486_432_000


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for name in ("kelt_vorn_001.nd2", "kelt_vorn_002.nd2", "Drift_kelt_vorn_001.tif"):
        _write(pool / "20200105 kelt droplet" / name)
    _write(pool / "20200105 kelt droplet" / "figures" / "kelt_droplet_figure.pdf")
    for name in ("kelt_vorn_001.nd2", "kelt_vorn_002.nd2", "Drift_kelt_vorn_001.tif"):
        _write(pool / "20200112 kelt droplet repeat" / name)
    for folder in ("kelt analysis good", "kelt analysis not good"):
        for number in ("001", "002"):
            _write(pool / folder / f"Traj_zib_kelt_{number}.csv")
    for index in range(3):
        _write(pool / "loose" / f"kelt_summary_plot_{index}.pdf")
    _write(pool / "raw" / "figure_kelt_001.nd2")
    old = pool / "20200112 kelt droplet repeat" / "kelt_vorn_old.nd2"
    _write(old)
    os.utime(old, (_LONG_AGO, _LONG_AGO))
    return pool


def _now_run(tail: str) -> str:
    """A run stamped when it runs, so a file born today is never "born after" the run that observes
    it.

    The case appears on Windows. So the earliest-credible-time rule concludes.
    """
    import datetime as dt

    return f"run_{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}Z_{tail * 12}"


def _index(created: Project) -> Project:
    write_revision(created.path)
    run_census(created, run_id=_now_run("1"), started_at="t")
    run_inference(created, run_id=_now_run("2"), started_at="t")
    return created


def _project(base: Path) -> Project:
    return _index(Project.create(base / "project", created_at="t", roots=[_pool(base)]))


def _search(project: Project, text: str, **more: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return routes.search(catalog, project.path, text=text, **more)


def _paths(found: dict[str, Any]) -> list[str | None]:
    return [row["path"] for row in found["results"]]


def test_a_filter_on_a_query_matching_nothing_finds_nothing(tmp_path: Path) -> None:
    found = _search(_project(tmp_path), "qqqzzz ext:tif")
    assert found["results"] == []


def test_a_date_is_read_as_a_person_types_one_and_an_unreadable_one_is_said(
    tmp_path: Path,
) -> None:
    assert read_day("2031") == "2031-01-01"
    assert read_day("2031-06") == "2031-06-01"
    assert read_day("2031/06/30") == "2031-06-30"
    assert read_day("2031-13-01") is None
    assert read_day("yesterday") is None
    normaliser = Normaliser(DictionaryStore.builtin())
    parsed = parse_query("kelt after:yesterday has:colour -in:", normaliser=normaliser)
    assert parsed.filters == ()
    assert len(parsed.refused) == 3
    project = _project(tmp_path)
    assert _search(project, "kelt after:2031-13-01")["refused"]
    assert _search(project, "kelt after:2031")["results"] == []


def test_a_date_filter_reads_the_earliest_credible_time_never_a_clock_set_aside(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        assert catalog.query(
            "SELECT count(*) FROM claims WHERE rule_id = 'earliest_credible_time'"
        ).fetchone()[0]
    # The one file dated 1985 has no credible time, so nothing here came before 2000.
    assert _search(project, "kelt before:2000")["results"] == []
    assert "20200112 kelt droplet repeat" in _paths(_search(project, "kelt after:2000"))


def test_without_a_time_rule_a_files_own_time_is_read_from_the_first_credible_year(
    tmp_path: Path,
) -> None:
    created = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    run_census(created, run_id=_now_run("1"), started_at="t")
    run_inference(created, run_id=_now_run("2"), started_at="t")
    assert _search(created, "kelt before:2000")["results"] == []


def test_a_cards_span_sets_aside_what_the_time_rule_set_aside(tmp_path: Path) -> None:
    found = _search(_project(tmp_path), "kelt droplet repeat")
    (card,) = [row for row in found["results"] if row["path"] == "20200112 kelt droplet repeat"]
    first = card["tags"]["span"]["first_day"]
    assert first is None or first >= "1990-01-01"


def test_an_excluded_filter_takes_a_quoted_value(tmp_path: Path) -> None:
    normaliser = Normaliser(DictionaryStore.builtin())
    parsed = parse_query('kelt -in:"kelt droplet"', normaliser=normaliser)
    assert [(item.name, item.value, item.negated) for item in parsed.filters] == [
        ("in", "kelt droplet", True)
    ]
    assert parsed.phrases == ()
    paths = _paths(_search(_project(tmp_path), 'kelt -in:"kelt droplet"'))
    assert paths
    assert not any(path and "kelt droplet" in path for path in paths)


def test_in_keeps_a_product_by_the_folder_it_sits_in(tmp_path: Path) -> None:
    found = _search(_project(tmp_path), "summary plot in:loose")
    assert found["results"]
    assert all(row["unit_kind"] in ("product", "products") for row in found["results"])


def test_a_result_hidden_from_one_search_comes_back_from_a_similar_one(tmp_path: Path) -> None:
    project = _project(tmp_path)
    first = _search(project, "kelt droplet")
    target = next(row for row in first["results"] if row["path"] == "20200112 kelt droplet repeat")
    routes.hide(
        project.path,
        {"unit_id": target["folder_id"], "concept_keys": first["concept_keys"], "actor": "one"},
    )
    similar = _search(project, "kelt droplet vorn", show_hidden=True)
    shown = next(row for row in similar["results"] if row["folder_id"] == target["folder_id"])
    assert shown["hidden_from_similar"]
    assert similar["hidden"]["units"][0]["concepts"]
    answer = routes.unhide(
        project.path,
        {"unit_id": target["folder_id"], "concept_keys": similar["concept_keys"], "actor": "two"},
    )
    assert answer["hides_undone"] == 1
    assert "20200112 kelt droplet repeat" in _paths(_search(project, "kelt droplet"))


def test_a_name_that_cancels_the_good_mark_is_not_marked(tmp_path: Path) -> None:
    found = _search(_project(tmp_path), "kelt analysis")
    marked = {row["path"]: row["marked_good"] for row in found["results"] if row["path"]}
    assert marked.get("kelt analysis good") is True
    assert marked.get("kelt analysis not good") is False


def test_a_raw_file_whose_name_holds_a_product_word_is_data_not_a_product(tmp_path: Path) -> None:
    found = _search(_project(tmp_path), "figure kelt")
    assert all(
        not (row["unit_kind"] == "product" and row["path"] == "raw/figure_kelt_001.nd2")
        for row in found["results"]
    )


def test_stage_reads_the_stage_rules_alone_as_the_dictionary_spells_a_stage(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    raw = _paths(_search(project, "kelt stage:raw"))
    assert "20200105 kelt droplet" in raw
    assert _search(project, "kelt stage:figure")["results"] == []


def test_the_candidates_kept_are_those_covering_most_of_the_query(tmp_path: Path) -> None:
    project = _project(tmp_path)
    normaliser = Normaliser(DictionaryStore.builtin())
    with open_catalog(project.path) as catalog:
        found = engine.search(
            catalog,
            project.path,
            "kelt droplet repeat",
            normaliser=normaliser,
            store=DictionaryStore.builtin(),
            max_candidates=1,
        )
    assert _paths(found) == ["20200112 kelt droplet repeat"]
    assert found["warnings"]


def test_why_gives_no_reason_the_ranking_did_not_count(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    _write(pool / "shelf" / "kelt_here.txt")
    _write(pool / "shelf" / "four" / "five" / "six" / "zib_far.txt")
    created = _index(Project.create(tmp_path / "project", created_at="t", roots=[pool]))
    found = _search(created, "kelt zib")
    for row in found["results"]:
        if row["path"] == "shelf":
            assert "zib" in row["missing"]
            assert all(line["typed"] != "zib" for line in row["why"])


def test_first_searches_arriving_together_all_answer(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(lambda text: _search(project, text), ["kelt", "vorn", "zib"]))
    assert all("results" in answer for answer in answers)
