"""What the Dashboard draws, the engine serves.

No number or word on a page is computed anywhere the engine did not compute it. The engine serves
three things instead of leaving them to the Years view: an experiment's name read into parts with
the dates left out (by the census's recorded layout, never a second parser), the busiest days of an
experiment's files, and the words two lines of work share. Every name and word is invented
(``fixtures/theme_pool.py``).
"""

from __future__ import annotations

import datetime as dt
import os
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import build_theme_project
from ingence.api import v1
from ingence.busy_days import busiest_days, day_of, days_words
from ingence.experiment_names import shown_name
from ingence.themes.overlap import shared_words

if TYPE_CHECKING:
    from pathlib import Path

EPOCH = dt.date(1970, 1, 1)


def _day(year: int, month: int, day: int) -> int:
    return (dt.date(year, month, day) - EPOCH).days


# ---------------------------------------------------------------------------
# The busiest days: the rule, as a pure function.
# ---------------------------------------------------------------------------


def test_most_files_on_one_day_are_that_day() -> None:
    days = [_day(2026, 3, 2)] * 30 + [_day(2026, 6, 1)] * 4 + [_day(2026, 9, 9)] * 2
    found = busiest_days(days)
    assert found is not None
    assert (found.first_day, found.last_day) == (dt.date(2026, 3, 2), dt.date(2026, 3, 2))
    assert (found.files, found.dated_files, found.percent) == (30, 36, 83)


def test_the_run_extends_over_neighbors_holding_five_percent_each() -> None:
    # 40 dated files: a neighbor needs 2 or more to join.
    days = (
        [_day(2026, 3, 1)] * 1
        + [_day(2026, 3, 2)] * 5
        + [_day(2026, 3, 3)] * 20
        + [_day(2026, 3, 4)] * 3
        + [_day(2026, 3, 5)] * 2
        + [_day(2026, 3, 7)] * 2
        + [_day(2026, 5, 1)] * 7
    )
    found = busiest_days(days)
    assert found is not None
    assert (found.first_day, found.last_day) == (dt.date(2026, 3, 2), dt.date(2026, 3, 5))
    assert found.files == 30 and found.dated_files == 40


def test_there_is_no_busiest_run_where_the_files_are_few_close_or_spread() -> None:
    assert busiest_days([_day(2026, 3, 2)] * 18 + [_day(2026, 6, 2)]) is None  # twenty dated needed
    assert busiest_days([_day(2026, 3, 2)] * 15 + [_day(2026, 3, 20)] * 10) is None  # 18 days apart
    spread = [_day(2026, 1, 1) + 7 * index for index in range(30)]  # one file a week
    assert busiest_days(spread) is None  # no run holds half
    assert busiest_days([]) is None


def test_files_with_no_time_are_in_neither_number() -> None:
    days = [_day(2026, 3, 2)] * 30 + [_day(2026, 6, 1)] * 6 + [None] * 50
    found = busiest_days(days)
    assert found is not None and (found.files, found.dated_files) == (30, 36)


def test_the_percent_rounds_half_up_and_a_run_of_exactly_half_counts() -> None:
    # 25 of 50 is exactly half; 5 of 8 is 62.5 and rounds up to 63.
    days = [_day(2026, 3, 2)] * 25 + [_day(2026, 3, 2) + 40 + 3 * i for i in range(25)]
    found = busiest_days(days)
    assert found is not None and found.percent == 50
    from ingence.busy_days import BusyDays

    assert BusyDays(dt.date(2026, 1, 1), dt.date(2026, 1, 1), 5, 8).percent == 63


def test_a_tie_for_the_busiest_day_goes_to_the_earlier_day() -> None:
    days = [_day(2026, 3, 2)] * 12 + [_day(2026, 3, 20)] * 12 + [_day(2026, 6, 1)] * 2
    found = busiest_days(days, floor=0.5, min_share_percent=40)
    assert found is not None and found.first_day == dt.date(2026, 3, 2) == found.last_day


def test_a_day_is_the_utc_day_of_a_time() -> None:
    assert day_of("2026-03-02T23:30:00-05:00") == _day(2026, 3, 3)  # 04:30 UTC the next day
    assert day_of("2026-03-02T10:00:00.123456789Z") == _day(2026, 3, 2)
    assert day_of("2026-03-02") == _day(2026, 3, 2)
    assert day_of("not a time") is None and day_of(None) is None and day_of("") is None


def test_the_run_in_words() -> None:
    first = dt.date(2026, 3, 2)
    assert days_words(first, first) == "2 Mar 2026"
    assert days_words(first, dt.date(2026, 3, 4)) == "2 to 4 Mar 2026"
    assert days_words(dt.date(2026, 2, 28), dt.date(2026, 3, 2)) == "28 Feb to 2 Mar 2026"
    assert days_words(dt.date(2025, 12, 30), dt.date(2026, 1, 2)) == "30 Dec 2025 to 2 Jan 2026"


# ---------------------------------------------------------------------------
# The words two lines share, as a pure function.
# ---------------------------------------------------------------------------


def test_a_pair_is_listed_once_only_if_it_shares_a_word_in_the_first_lines_spelling() -> None:
    themes = [
        {"theme": 2, "words": ["Beta", "gamma", "delta"]},
        {"theme": 1, "words": ["alpha", "BETA", "gamma"]},
        {"theme": 3, "words": ["omega"]},
    ]
    pairs = shared_words(themes)
    assert pairs == [
        {"theme_a": 1, "theme_b": 2, "words": ["BETA", "gamma"], "shared": 2, "of_smaller": 3}
    ]
    assert shared_words([]) == [] and shared_words([{"theme": 1, "words": []}]) == []


# ---------------------------------------------------------------------------
# A name's shown form.
# ---------------------------------------------------------------------------


def test_the_shown_name_is_built_from_the_recorded_separators() -> None:
    # Each piece: the separator the tokenizer recorded before the part, the part, whether hidden.
    kept = (("", "HeLa", False), ("_", "20260304", True), ("_", "timecourse", False))
    assert shown_name("HeLa_20260304_timecourse", kept) == "HeLa timecourse"
    # A date first, and the dot the name wrote after it: neither reaches the shown name.
    dotted = (("", "2022.02.10", True), (".", "HepG2", False), ("_", "actin", False))
    assert shown_name("2022.02.10.HepG2_actin", dotted) == "HepG2 actin"
    # A date inside brackets takes no empty brackets with it.
    bracketed = (("", "NIH3T3", False), (" ", "EGFR", False), (" (", "2023-09-05", True))
    assert shown_name("NIH3T3 EGFR (2023-09-05)", bracketed) == "NIH3T3 EGFR"
    # Two parts the name wrote glued together stay glued.
    glued = (("", "zib", False), ("", "Kelt", False), ("_", "20260304", True))
    assert shown_name("zibKelt_20260304", glued) == "zibKelt"
    # Nothing left, or nothing to build from: the name as written.
    assert shown_name("20260304", (("", "20260304", True),)) == "20260304"
    assert shown_name("HeLa-v2", ()) == "HeLa-v2"


def test_a_name_the_engine_read_shows_no_stray_dot_or_bracket(tmp_path: Path) -> None:
    from ingence.core.project import Project
    from ingence.experiment_names import experiment_names
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    names = {
        "2022.02.10.HepG2_actin": "HepG2 actin",
        "NIH3T3 EGFR (2023-09-05)": "NIH3T3 EGFR",
        "zibKelt_20260304": "zibKelt",
        "HeLa_20260304_timecourse": "HeLa timecourse",
        "20260304": "20260304",
    }
    pool = tmp_path / "pool"
    for name in names:
        (pool / name).mkdir(parents=True)
        (pool / name / "a.csv").write_text("x\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_aaaaaaaaaaaa", started_at="t")
    with open_catalog(project.path) as catalog:
        ids = dict(
            catalog.query(
                "SELECT name, entry_id FROM entries WHERE entry_type = 'directory'"
            ).fetchall()
        )
        read = experiment_names(catalog, [ids[name] for name in names])
    assert {name: read[ids[name]].shown for name in names} == names


# ---------------------------------------------------------------------------
# The routes, on a pool the engine read.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Any:
    project, plant = build_theme_project(tmp_path_factory.mktemp("serve_busy"))
    return v1.context_for(project, settled=None, job_running=False), plant


def _get(context: Any, path: str, **query: Any) -> dict[str, Any]:
    status, payload = v1.dispatch(context, path, {k: [str(v)] for k, v in query.items()})
    assert status == HTTPStatus.OK, payload
    return payload


def test_themes_and_completeness_serve_the_name_the_census_read(served: Any) -> None:
    context, plant = served
    themes = _get(context, "themes")
    completeness = _get(context, "completeness")
    dated = [u for u in plant.documents if plant.years[u] is not None]
    undated = [u for u in plant.documents if plant.years[u] is None]
    by_folder = {row["folder"]: row for row in themes["experiments"]}
    for unit in dated:
        row = by_folder[plant.folders[unit]]
        written = plant.folders[unit].rsplit("/", 1)[-1]
        date_part, words = written.split(" ", 1)
        assert row["name"] == written and row["name_read"] is True
        assert row["shown_name"] == words, row
        kinds = [(part["text"], part["kind"], part["hidden"]) for part in row["name_parts"]]
        assert kinds[0] == (date_part, "date", True)
        assert all(kind == "word" and not hidden for _, kind, hidden in kinds[1:])
    for unit in undated:
        row = by_folder[plant.folders[unit]]
        assert row["shown_name"] == row["name"]
        assert not any(part["hidden"] for part in row["name_parts"])
    # The completeness route says the same of the same folders, themes or none.
    shown = {row["folder"]: row["shown_name"] for row in completeness["experiments"]}
    assert shown == {row["folder"]: row["shown_name"] for row in themes["experiments"]}


def test_a_name_with_no_recorded_layout_is_shown_as_written_and_says_so(tmp_path: Path) -> None:
    from ingence.api.v1.names import name_fields
    from ingence.core.project import Project
    from ingence.core.settings import Settings
    from ingence.experiment_names import experiment_names
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "20260304 keltan vornish").mkdir(parents=True)
    (pool / "20260304 keltan vornish" / "keltan_01.nd2").write_text("x\n")
    for recorded in (True, False):
        project = Project.create(
            tmp_path / f"project-{recorded}",
            created_at="t",
            roots=[pool],
            settings=Settings.model_validate({"dictionary": {"record_name_shapes": recorded}}),
        )
        run_census(
            project, run_id=f"run_20260101T00000{int(recorded)}Z_aaaaaaaaaaaa", started_at="t"
        )
        with open_catalog(project.path) as catalog:
            [folder] = catalog.query(
                "SELECT entry_id FROM entries WHERE entry_type = 'directory' AND name = ?",
                ["20260304 keltan vornish"],
            ).fetchone()
            held = experiment_names(catalog, [folder, "no-such-entry"])
            fields = name_fields(catalog, [(folder, "kipper/20260304 keltan vornish")])
        # An identifier the run holds nothing for is not guessed at.
        assert set(held) == {folder}
        if recorded:
            assert held[folder].read and held[folder].shown == "keltan vornish"
            assert [piece.hidden for piece in held[folder].parts] == [True, False, False]
        else:
            # Unexamined, not guessed: no parts, and the name as it was written.
            assert held[folder].read is False and held[folder].parts == ()
            assert held[folder].shown == "20260304 keltan vornish"
            assert fields[folder]["name_read"] is False and fields[folder]["name_parts"] == []


def test_overlap_lists_each_pair_that_shares_a_word_once(served: Any) -> None:
    context, _ = served
    overlap = _get(context, "themes/overlap")
    themes = _get(context, "themes")["themes"]
    assert overlap["themes"] == len(themes)
    seen = set()
    words = {t["theme"]: [w.casefold() for w in t["words"]] for t in themes}
    for pair in overlap["pairs"]:
        key = (pair["theme_a"], pair["theme_b"])
        assert pair["theme_a"] < pair["theme_b"] and key not in seen
        seen.add(key)
        assert pair["words"] and pair["shared"] == len(pair["words"])
        for word in pair["words"]:
            assert word.casefold() in words[pair["theme_a"]]
            assert word.casefold() in words[pair["theme_b"]]
    # Every pair NOT listed shares nothing.
    for a in words:
        for b in words:
            if a < b and (a, b) not in seen:
                assert not set(words[a]) & set(words[b])


# ---------------------------------------------------------------------------
# The busiest days, on the route.
# ---------------------------------------------------------------------------

SITTINGS = (
    # (month, day, hour, files): most of the experiment is written on 2 and 3 March.
    (3, 2, 10, 24),
    (3, 3, 14, 6),
    (6, 1, 9, 5),
    (9, 9, 9, 3),
)
THIRTY_EIGHT = sum(count for *_, count in SITTINGS)


def _write_sittings(base: Path) -> tuple[Path, str]:
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    pool = base / "pool"
    folder = pool / "kipper" / "20260301 keltan vornish"
    folder.mkdir(parents=True)
    index = 0
    for month, day, hour, count in SITTINGS:
        moment = dt.datetime(2026, month, day, hour, tzinfo=dt.UTC).timestamp()
        for _ in range(count):
            index += 1
            path = folder / f"keltan_{index:03d}.nd2"
            path.write_text(f"file {index}\n")
            os.utime(path, (moment, moment))
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_aaaaaaaaaaaa", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_bbbbbbbbbbbb", started_at="t")
    return project.path, "kipper/20260301 keltan vornish"


def test_the_files_route_serves_the_busiest_days_of_every_file_on_every_page(
    tmp_path: Path,
) -> None:
    path, folder = _write_sittings(tmp_path)
    context = v1.context_for(path, settled=None, job_running=False)
    experiment = next(
        row["id"] for row in _get(context, "themes")["experiments"] if row["folder"] == folder
    )
    # Every page says the same, however the files are paged: it is a fact about all of them.
    first = _get(context, "experiments/files", experiment=experiment, limit=5, offset=0)
    last = _get(context, "experiments/files", experiment=experiment, limit=5, offset=30)
    assert first["busy_days"] == last["busy_days"]
    busy = first["busy_days"]
    # 24 + 6 = 30 of 38 dated files lie on 2 and 3 March; 5 on 1 June are 13 percent, which is
    # enough to join a run only if adjacent, and they are not.
    assert busy["first_day"] == "2026-03-02" and busy["last_day"] == "2026-03-03"
    assert busy["said"] == "2 to 3 Mar 2026"
    assert (busy["files"], busy["dated_files"]) == (30, 38)
    assert busy["share"] == pytest.approx(30 / 38) and busy["percent"] == 79
    assert first["files"] == THIRTY_EIGHT
