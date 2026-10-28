"""Arranging the Graph workspace by what names say, not only by folders.

On the explorer fixture (``fixtures/explorer_project.py``, invented names), whose three experiment
folders say a technique, a condition and a reagent: ``confocal wt gfp``, ``tirf ko``, and
``lsm wt dapi`` (``lsm`` another spelling of ``confocal``). Every count is checked against the
fixture's own files, and every arrangement still counts each file exactly once.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


def _ask(project: Path, path: str, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    asked = {
        name: [str(item) for item in value] if isinstance(value, list) else [str(value)]
        for name, value in query.items()
    }
    return dispatch(context, path, asked)


def _ok(project: Path, path: str, **query: Any) -> dict[str, Any]:
    status, body = _ask(project, path, **query)
    assert status == HTTPStatus.OK, body
    return body


def _files_under(explorer: ExplorerProject, words: tuple[str, ...]) -> set[str]:
    """Live files whose path passes through an experiment folder whose name holds every word."""
    from ingence.storage.catalog import open_catalog

    with open_catalog(explorer.project) as catalog:
        rows = catalog.query(
            "SELECT relative_path FROM entries WHERE NOT is_deleted AND entry_type <> 'directory' "
            "AND entry_kind = 'filesystem'"
        ).fetchall()
    return {
        str(path)
        for (path,) in rows
        if any(all(word in part.split("_") for word in words) for part in str(path).split("/"))
    }


def _ring(body: dict[str, Any], ring: int) -> list[dict[str, Any]]:
    return [sector for sector in body["sectors"] if sector["ring"] == ring]


def test_arranged_by_technique_then_condition_counts_every_file_once(explorer) -> None:
    body = _ok(explorer.project, "explorer/wheel", arrange="t.technique,t.condition")
    total = body["totals"]["files"]
    assert sum(body["bins"]["n"]) == total
    first = _ring(body, 1)
    assert sum(sector["files"] for sector in first) == total
    by_name = {sector["name"]: sector for sector in first}
    # confocal and lsm are one sense: both experiments' files are in the one sector.
    confocal = _files_under(explorer, ("confocal",)) | _files_under(explorer, ("lsm",))
    assert by_name["confocal"]["files"] == len(confocal)
    # Two techniques in one name are shown as the pair, never as one of them.
    assert by_name["knockout + total internal reflection"]["files"] == len(
        _files_under(explorer, ("tirf", "ko"))
    )
    assert by_name["no technique named"]["files"] == total - len(confocal) - len(
        _files_under(explorer, ("tirf", "ko"))
    )
    assert by_name["no technique named"]["value"] == ""
    wild = [sector for sector in _ring(body, 2) if sector["parent"] == by_name["confocal"]["id"]]
    assert [sector["name"] for sector in wild] == ["wild type"]
    assert body["arrangement"]["axes"][0]["key"] == "t.technique"
    assert body["arrangement"]["axes"][1]["family"] == "names"


def test_arranged_by_spelling_tells_two_spellings_of_one_sense_apart(explorer) -> None:
    body = _ok(explorer.project, "explorer/wheel", arrange="s.technique")
    names = {sector["name"]: sector["files"] for sector in _ring(body, 1)}
    assert names["confocal"] == len(_files_under(explorer, ("confocal",)))
    assert names["lsm"] == len(_files_under(explorer, ("lsm",)))
    assert names["ko + tirf"] == len(_files_under(explorer, ("tirf", "ko")))


def test_a_value_card_says_how_its_sense_is_spelled_and_where_it_is_said(explorer) -> None:
    card = _ok(explorer.project, "explorer/value", axis="t.technique", value="confocal")
    confocal = _files_under(explorer, ("confocal",)) | _files_under(explorer, ("lsm",))
    assert card["files"] == len(confocal)
    assert {item["text"] for item in card["spellings"]} == {"confocal", "lsm"}
    # The fixture writes the technique in folder names only: every file inherits it.
    assert card["own_name"] == 0
    assert card["inherited"] == len(confocal)
    # One of the two experiments is confirmed by a person in the Record.
    assert card["experiment"]["units"] == 2
    assert card["experiment"]["confirmed"] == 1
    assert sum(holder["files"] for holder in card["holders"]) == len(confocal)


def test_a_filtered_page_of_files_lists_exactly_a_value_sectors_files(explorer) -> None:
    wheel = _ok(explorer.project, "explorer/wheel", arrange="t.technique")
    root = wheel["focus"]["id"]
    page = _ok(
        explorer.project,
        "explorer/files",
        folder=root,
        scope="beneath",
        where="t.technique=confocal",
        limit=500,
    )
    confocal = _files_under(explorer, ("confocal",)) | _files_under(explorer, ("lsm",))
    assert {row["path"] for row in page["files"]} == confocal
    assert page["total"] == len(confocal)
    others = _ok(
        explorer.project,
        "explorer/files",
        folder=root,
        scope="beneath",
        **{"not": "t.technique=confocal"},
        limit=500,
    )
    assert others["total"] == wheel["totals"]["files"] - len(confocal)


def test_a_filtered_wheel_is_the_folder_tree_of_the_matching_files(explorer) -> None:
    body = _ok(explorer.project, "explorer/wheel", where="t.condition=wild_type")
    wild = _files_under(explorer, ("wt",))
    assert body["totals"]["files"] == len(wild)
    assert sum(body["bins"]["n"]) == len(wild)
    assert {sector["name"] for sector in _ring(body, 1)} == {
        folder for folder in explorer.experiments if "_wt_" in folder
    }
    assert body["arrangement"]["where"][0]["label"] == "wild type"
    assert body["arrangement"]["files_beneath"] > len(wild)


def test_folder_levels_arranged_match_the_folder_wheel(explorer) -> None:
    plain = _ok(explorer.project, "explorer/wheel", rings=2)
    arranged = _ok(explorer.project, "explorer/wheel", arrange="folder,folder")
    plain_first = {sector["name"]: sector["files"] for sector in _ring(plain, 1)}
    arranged_first = {sector["name"]: sector["files"] for sector in _ring(arranged, 1)}
    assert arranged_first == plain_first
    plain_second = sorted((s["name"], s["files"]) for s in _ring(plain, 2) if s["kind"] == "folder")
    arranged_second = sorted(
        (s["name"], s["files"]) for s in _ring(arranged, 2) if s["kind"] == "value"
    )
    assert arranged_second == plain_second
    # A folder's own files stay at its sector, as in the folder Wheel: no "directly in" ring.
    assert not any(s["name"].startswith("files directly in") for s in arranged["sectors"])
    assert sum(arranged["bins"]["n"]) == plain["totals"]["files"]


def test_axes_offered_say_how_much_each_would_arrange(explorer) -> None:
    body = _ok(explorer.project, "explorer/axes")
    axes = {item["axis"]["key"]: item for item in body["axes"]}
    for key in ("folder", "unit", "kind", "stage", "exam", "prov", "year", "month"):
        assert key in axes
    technique = axes["t.technique"]
    assert technique["files_with_value"] == len(
        _files_under(explorer, ("confocal",))
        | _files_under(explorer, ("lsm",))
        | _files_under(explorer, ("tirf", "ko"))
    )
    assert technique["senses"] == 3  # confocal, total internal reflection, knockout
    assert technique["spellings"] == 4  # confocal, lsm, tirf, ko
    assert axes["s.technique"]["values"] == 3
    assert axes["t.reagent"]["examples"][0]["label"] in ("fluorescent protein", "nuclear stain")
    assert axes["unit"]["values"] == len(explorer.experiments)


def test_themes_are_read_from_names_only(explorer) -> None:
    """Every spelling a file carries is written in its own name or a folder's name above it."""
    import duckdb

    from ingence.api.v1.explorer.wheel import explorer_projection
    from ingence.storage.catalog import open_catalog

    projection, _ = explorer_projection(
        explorer.project, _ok(explorer.project, "explorer/wheel")["run_id"]
    )
    with open_catalog(explorer.project) as catalog:
        paths = dict(
            catalog.query(
                "SELECT entry_id, lower(relative_path) FROM entries WHERE NOT is_deleted"
            ).fetchall()
        )
    connection = duckdb.connect()
    try:
        rows = connection.execute(f"SELECT entry_id, spellings FROM {projection.themes}").fetchall()
    finally:
        connection.close()
    assert rows
    for entry_id, spellings in rows:
        for spelling in spellings:
            assert spelling in paths[entry_id], (paths[entry_id], spelling)


def test_timeline_rows_by_an_axis_count_every_file(explorer) -> None:
    body = _ok(explorer.project, "explorer/timeline", by="t.technique")
    tracks = body["activity"]["tracks"]
    kinds = {track["name"]: track["kind"] for track in tracks}
    assert kinds["confocal"] == "value"
    assert kinds["no technique named"] == "none"
    files = sum(track["files"] for track in tracks)
    wheel = _ok(explorer.project, "explorer/wheel")
    assert files == wheel["totals"]["files"]
    filtered = _ok(explorer.project, "explorer/timeline", by="units", where="t.technique=confocal")
    assert sum(track["files"] for track in filtered["activity"]["tracks"]) == len(
        _files_under(explorer, ("confocal",)) | _files_under(explorer, ("lsm",))
    )


def test_refusals_name_the_parameter(explorer) -> None:
    for path, query, parameter in (
        ("explorer/wheel", {"arrange": "colour"}, "arrange"),
        ("explorer/wheel", {"arrange": "t.Technique"}, "arrange"),
        ("explorer/wheel", {"arrange": "kind,stage,exam,prov,year"}, "arrange"),
        ("explorer/wheel", {"where": "t.technique"}, "where"),
        ("explorer/timeline", {"by": "t.no such"}, "by"),
        ("explorer/value", {"axis": "hue", "value": "x"}, "axis"),
        ("explorer/value", {"axis": "kind"}, "value"),
    ):
        status, body = _ask(explorer.project, path, **query)
        assert status == HTTPStatus.BAD_REQUEST, (path, query, body)
        assert body["error"]["parameter"] == parameter, (path, query, body)


def test_a_theme_kind_no_axis_can_be_named_by_is_counted_and_breaks_nothing(
    explorer, tmp_path
) -> None:
    """A kind spelled with a space or a hyphen (a person's definition, a laboratory's pack) is
    set aside and counted in words, and every other axis is still offered."""
    import shutil

    import duckdb

    from ingence.api.v1.explorer.wheel import explorer_projection

    project = tmp_path / "project"
    shutil.copytree(explorer.project, project)
    run_id = _ok(project, "explorer/wheel")["run_id"]
    projection, _ = explorer_projection(project, run_id)
    themes = projection.path / "themes.parquet"
    widened = tmp_path / "themes.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            f"COPY (SELECT * FROM read_parquet('{themes.as_posix()}') UNION ALL "
            "SELECT * REPLACE ('cell line' AS theme_kind) "
            f"FROM read_parquet('{themes.as_posix()}') WHERE theme_kind = 'technique' "
            "UNION ALL SELECT * REPLACE ('cell-line' AS theme_kind) "
            f"FROM read_parquet('{themes.as_posix()}') WHERE theme_kind = 'reagent') "
            f"TO '{widened.as_posix()}' (FORMAT PARQUET)"
        )
        technique, reagent = (
            connection.execute(
                f"SELECT count(*) FROM read_parquet('{themes.as_posix()}') WHERE theme_kind = ?",
                [kind],
            ).fetchone()[0]
            for kind in ("technique", "reagent")
        )
    finally:
        connection.close()
    shutil.move(widened, themes)

    body = _ok(project, "explorer/axes")
    keys = {item["axis"]["key"] for item in body["axes"]}
    assert {"folder", "t.technique", "s.technique", "t.reagent"} <= keys
    assert not [key for key in keys if " " in key or "-" in key]
    assert {item["theme_kind"]: item["files"] for item in body["unarranged"]} == {
        "cell line": technique,
        "cell-line": reagent,
    }
    assert all("cannot" in item["words"] for item in body["unarranged"])
