"""One output folder per item is one experiment's items, not one experiment each.

A tool that writes one output folder per item (per movie, per cell, per well) leaves a folder
holding many sibling folders that each hold the same few files (a tracks table, a settings file)
under names that share their words. Those item folders are parts of one run, and the folder
holding the run's output folder is the experiment. An item folder's name is the item's, and often
carries a date, so the name rule would read each one as an experiment of its own.

The negatives: a folder of a few genuinely different dated experiments stays that many
experiments. So do dated folders of different words that happen to hold the same two files (groups
are made of words), and a few repeated days of one condition holding the same files (fewer than
the setting asks, so the groups can still propose them).

Every name is invented.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes.groupings import experiment_folders
from ingence.core.project import Project
from ingence.evaluation.generate import STEMS
from ingence.infer.folder_contents import (
    MINIMUM_ALIKE_ITEMS,
    MOST_FOLDERS_A_DATE,
    alike_shape,
    name_dates,
    name_words,
    series_of_days,
    series_remainder,
)
from ingence.infer.run import run_inference
from ingence.rulebook.revision_4 import REVISION
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.api.routes.groupings import Experiments


#: Dates the tokeniser reads (``d0318`` it does not), so each item folder's name holds a date and a
#: word, which would make the name rule read each one as an experiment.
_ITEM_DATES = ("230318", "230319", "230322", "230405")
_ITEM_LETTERS = "ABCDEFGH"
_ITEMS = 240


def _item_names(count: int) -> list[str]:
    """``230318_A_kelt003``-like names: one family, its letters and numbers varying."""
    return [
        f"{_ITEM_DATES[number % 4]}_{_ITEM_LETTERS[number % 8]}_kelt{number + 3:03d}"
        for number in range(count)
    ]


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for item in _item_names(_ITEMS):
        _write(pool / "pilot3" / "movies" / item / "tracks.csv", f"t,{item}\n")
        _write(pool / "pilot3" / "movies" / item / "params.txt", f"item {item}\n")
    # One folder's view settings, as an operating system leaves them: no part of the run's shape.
    _write(pool / "pilot3" / "movies" / _item_names(1)[0] / ".DS_Store")
    # Item folders whose files carry the item's own name: the same files once it is stood in.
    for number in range(40):
        item = f"2304{10 + number % 9:02d}_{_ITEM_LETTERS[number % 8]}_vorn{number:02d}"
        _write(pool / "pilot4" / "cells" / item / f"{item}_tracks.csv")
        _write(pool / "pilot4" / "cells" / item / "settings.txt")
    # Five genuinely different dated experiments: their own files, their own words.
    for folder, word, count in (
        ("2023-01-04_zeph", "zeph", 2),
        ("2023-02-11_kelt", "kelt", 3),
        ("2023-03-02_vorn", "vorn", 1),
        ("2023-04-19_brisa", "brisa", 4),
        ("2023-05-27_quolt", "quolt", 2),
    ):
        for number in range(1, count + 1):
            _write(pool / "trials" / folder / f"{word}_{number:03d}.nd2")
            _write(pool / "trials" / folder / f"{word}_{number:03d}_tracks.csv")
        _write(pool / "trials" / folder / f"{word}_notes.txt")
    # Dated folders of different words that happen to hold the same two files: separate.
    words = ("trell", "sorn", "dask", "morv", "gisk", "pell", "zib", "tamen", "fenlo", "harnu")
    words += ("jorvel", "kasto", "lumbri", "nesko")
    for number, word in enumerate(words):
        _write(pool / "days" / f"2024-06-{number + 2:02d}_{word}" / "tracks.csv")
        _write(pool / "days" / f"2024-06-{number + 2:02d}_{word}" / "params.txt")
    # Five repeated days of one condition holding the same files: fewer than the setting asks.
    for day in (3, 4, 5, 9, 10):
        _write(pool / "repeats" / f"2024-09-{day:02d}_orvid" / "tracks.csv")
        _write(pool / "repeats" / f"2024-09-{day:02d}_orvid" / "params.txt")
    return pool


@pytest.fixture(scope="module")
def experiments(tmp_path_factory: pytest.TempPathFactory) -> Experiments:
    base = tmp_path_factory.mktemp("per-item-folders")
    pool = _pool(base)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(created.path) as catalog:
        return experiment_folders(catalog)


def test_many_item_folders_of_one_run_are_one_experiment(experiments: Experiments) -> None:
    beneath = [path for path in experiments.by_path if path.startswith("pilot3")]
    assert beneath == ["pilot3"]
    assert experiments.folders[experiments.by_path["pilot3"]]["kind"] == "analysis"
    for item in _item_names(_ITEMS):
        # ``of`` answers the experiment's entry. Its path is read back for the check.
        assert experiments.path_of(experiments.of(f"pilot3/movies/{item}/tracks.csv")) == "pilot3"


def test_item_folders_whose_files_carry_the_item_s_name_are_one_experiment(
    experiments: Experiments,
) -> None:
    beneath = [path for path in experiments.by_path if path.startswith("pilot4")]
    assert beneath == ["pilot4"]


def test_five_genuinely_different_dated_experiments_stay_five(experiments: Experiments) -> None:
    trials = sorted(path for path in experiments.by_path if path.startswith("trials/"))
    assert trials == [
        "trials/2023-01-04_zeph",
        "trials/2023-02-11_kelt",
        "trials/2023-03-02_vorn",
        "trials/2023-04-19_brisa",
        "trials/2023-05-27_quolt",
    ]


def test_dated_folders_of_different_words_holding_the_same_files_stay_experiments(
    experiments: Experiments,
) -> None:
    days = [path for path in experiments.by_path if path.startswith("days/")]
    assert len(days) == 14


def test_a_few_repeated_days_holding_the_same_files_stay_experiments(
    experiments: Experiments,
) -> None:
    repeats = [path for path in experiments.by_path if path.startswith("repeats/")]
    assert len(repeats) == 5


# Series of days, words, a run beside another, subfolders.


def _weekdays(start: dt.date, count: int) -> list[dt.date]:
    days: list[dt.date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def _series_pool(base: Path) -> Path:
    pool = base / "pool"
    # Three weeks of one daily protocol: one condition, the same two files every day.
    for day in _weekdays(dt.date(2024, 6, 3), 15):
        _write(pool / "daily" / f"{day:%Y-%m-%d}_kelt" / "params.txt", f"p {day}\n")
        _write(pool / "daily" / f"{day:%Y-%m-%d}_kelt" / "data.csv", f"d,{day}\n")
    # The same, its folders named by a date alone.
    for day in _weekdays(dt.date(2024, 6, 3), 15):
        _write(pool / "dateonly" / f"{day:%Y%m%d}" / "params.txt", f"p {day}\n")
        _write(pool / "dateonly" / f"{day:%Y%m%d}" / "data.csv", f"d,{day}\n")
    # Two conditions on alternating days, twelve days each.
    for number, day in enumerate(_weekdays(dt.date(2024, 1, 8), 24)):
        word = ("brisa", "zeph")[number % 2]
        _write(pool / "twoconds" / f"{day:%Y-%m-%d}_{word}" / "params.txt", "p\n")
        _write(pool / "twoconds" / f"{day:%Y-%m-%d}_{word}" / "results.csv", "r\n")
    # A daily protocol written with a month's word, across two months.
    for day in _weekdays(dt.date(2024, 6, 17), 15):
        _write(pool / "monthword" / f"{day:%d%b%Y}_quolt" / "params.txt", "p\n")
        _write(pool / "monthword" / f"{day:%d%b%Y}_quolt" / "data.csv", "d\n")
    # Dated experiments of different words, each one movie's analysis from one template.
    for number, word in enumerate(("zeph", "kelt", "vorn", "brisa", "quolt", "sorn", "trell")):
        movie = f"{word}movie{number + 1:02d}"
        _write(pool / "tmpl" / "acquired" / f"{movie}.nd2", f"raw {movie}\n" * 30)
        folder = pool / "tmpl" / "analysis" / f"2023-0{number + 1}-1{number}_{word}"
        _write(folder / f"Traj_{movie}_tracks.csv", f"t,{movie}\n")
        _write(folder / f"Plot_{movie}_tracks.png", f"p {movie}\n")
    # Two runs' per-movie outputs, their movies kept in one folder beside them.
    for run, word in (("brisa run", "kelt"), ("zeph run", "vorn")):
        for number in range(1, 13):
            item = f"{word}movie{number:02d}"
            _write(pool / "tworuns" / "acquired" / f"{item}.nd2", f"raw {item}\n" * 30)
            _write(pool / "tworuns" / run / "movies" / item / f"Traj_{item}_tracks.csv", "t\n")
            _write(pool / "tworuns" / run / "movies" / item / f"Plot_{item}_tracks.png", "p\n")
    # A daily series whose plate letter changes each day (single letters set aside).
    for number, day in enumerate(_weekdays(dt.date(2024, 9, 2), 15)):
        folder = pool / "dletter" / f"{day:%Y%m%d}_{_ITEM_LETTERS[number % 8]}_brisa"
        _write(folder / "params.txt", f"p{day}")
        _write(folder / "data.csv", f"d{day}")
    # Five days, three replicate folders a day.
    for day in _weekdays(dt.date(2024, 7, 1), 5):
        for rep in (1, 2, 3):
            folder = pool / "dayreps" / f"{day:%Y-%m-%d}_vorn_rep{rep}"
            _write(folder / "params.txt", f"p{day}{rep}")
            _write(folder / "data.csv", f"d{day}{rep}")
    # A daily series counting its days beside the date.
    for number, day in enumerate(_weekdays(dt.date(2024, 6, 3), 15), start=1):
        folder = pool / "dcount" / f"{day:%Y%m%d}_kelt_d{number:02d}"
        _write(folder / "params.txt", f"p{day}")
        _write(folder / "data.csv", f"d{day}")
    # A run's thirty items, all made on one day.
    for number in range(30):
        folder = pool / "oneday" / "movies" / f"230318_quolt{number:03d}"
        _write(folder / "tracks.csv", f"t{number}")
        _write(folder / "params.txt", f"p{number}")
    # Item folders each holding a small folder of plots.
    for number in range(40):
        item = f"2303{10 + number % 9:02d}_{_ITEM_LETTERS[number % 8]}_kelt{number:03d}"
        base_folder = pool / "pilot5" / "movies" / item
        _write(base_folder / "tracks.csv", f"t,{item}\n")
        _write(base_folder / "params.txt", f"p {item}\n")
        _write(base_folder / "plots" / "trace.png", f"png {item}\n")
    return pool


@pytest.fixture(scope="module")
def series_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Experiments, dict[str, str]]:
    base = tmp_path_factory.mktemp("series-of-days")
    created = Project.create(base / "project", created_at="t", roots=[_series_pool(base)])
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(created.path) as catalog:
        structure = dict(
            catalog.query(
                "SELECT e.relative_path, c.object_reference FROM claims c "
                "JOIN entries e ON e.entry_id = c.subject "
                "WHERE c.rule_id = 'experiment_folder_from_contents' "
                "AND c.resolution_status = 'resolved'"
            ).fetchall()
        )
        return experiment_folders(catalog), structure


@pytest.fixture(scope="module")
def series(series_run: tuple[Experiments, dict[str, str]]) -> Experiments:
    return series_run[0]


def _under(experiments: Experiments, top: str) -> list[str]:
    return sorted(path for path in experiments.by_path if path.split("/")[0] == top)


def test_a_weeks_long_daily_series_of_one_condition_stays_one_experiment_a_day(
    series: Experiments,
) -> None:
    """Folders whose names differ only by a date are days, never a run's items."""
    assert len(_under(series, "daily")) == 15
    assert "daily" not in series.by_path


def test_a_daily_series_whose_plate_letter_changes_each_day_stays_one_experiment_a_day(
    series: Experiments,
) -> None:
    """What a family's words set aside (a single letter) never makes days a run's items."""
    assert len(_under(series, "dletter")) == 15


def test_replicate_folders_over_five_days_stay_experiments(series: Experiments) -> None:
    """Three replicates a day over five days: days of one series, never one run's items."""
    assert len(_under(series, "dayreps")) == 15


def test_a_series_counting_its_days_beside_the_date_stays_one_experiment_a_day(
    series: Experiments,
) -> None:
    """Nearly every folder a date of its own (each date held by at most two): days."""
    assert len(_under(series, "dcount")) == 15


def test_a_run_s_items_all_made_on_one_day_are_one_run(series: Experiments) -> None:
    assert _under(series, "oneday") == ["oneday"]


def test_two_conditions_on_alternating_days_stay_experiments(series: Experiments) -> None:
    assert len(_under(series, "twoconds")) == 24


def test_folders_named_by_a_date_alone_are_no_run_s_items(series: Experiments) -> None:
    """A family made of no words is a set of dates, not a run: nothing is concluded."""
    assert _under(series, "dateonly") == []


def test_a_month_s_word_is_no_word_of_a_family(series: Experiments) -> None:
    assert len(_under(series, "monthword")) == 15


def test_dated_folders_of_different_words_from_one_template_stay_named_experiments(
    series: Experiments,
) -> None:
    """The dated-name rule stops on an item's folder only where its family shares its words."""
    assert len([path for path in _under(series, "tmpl") if path.startswith("tmpl/analysis/")]) == 7


def test_two_runs_beside_their_movies_keep_their_own_output_folders(
    series_run: tuple[Experiments, dict[str, str]],
) -> None:
    """The run's output folder stays its own unit where the folder above is bound further up: the
    folder holding the movies and both runs is the experiment, each run's outputs an analysis of
    their own."""
    structure = {path: kind for path, kind in series_run[1].items() if path.startswith("tworuns")}
    assert structure == {
        "tworuns": "experiment",
        "tworuns/brisa run/movies": "analysis",
        "tworuns/zeph run/movies": "analysis",
    }


def test_item_folders_each_holding_a_folder_of_plots_are_one_experiment(
    series: Experiments,
) -> None:
    assert _under(series, "pilot5") == ["pilot5"]


def test_an_item_folder_s_files_read_alike_with_its_name_and_numbers_stood_in() -> None:
    first = alike_shape(
        "230318_A_kelt003",
        [("230318_A_kelt003_tracks.csv", "csv"), ("frame_0001.tif", "tif"), (".DS_Store", None)],
    )
    second = alike_shape(
        "230405_D_kelt117",
        [("230405_D_kelt117_tracks.csv", "csv"), ("frame_0040.tif", "tif")],
    )
    assert first == second == {("*", "tracks", ".csv"), ("frame", "#", ".tif")}


def test_the_rule_declares_the_measured_default() -> None:
    (rule,) = [
        rule for rule in REVISION["rules"] if rule["id"] == "experiment_folder_from_contents"
    ]
    (declared,) = [p for p in rule["parameters"] if p["name"] == "minimum_alike_items"]
    assert declared["default"] == MINIMUM_ALIKE_ITEMS
    (most,) = [p for p in rule["parameters"] if p["name"] == "most_folders_a_date"]
    assert most["default"] == MOST_FOLDERS_A_DATE


# The measurement the default rests on: seeded, so it gives the same figures every time.
_COMMON = (
    "tracks.csv", "params.txt", "settings.txt", "notes.txt", "results.csv", "summary.xlsx",
    "log.txt", "config.json", "analysis.m", "plot.png", "mask.tif", "readme.txt",
)  # fmt: skip
_SUFFIXES = ("tracks", "results", "summary", "plot", "mask")


def _read_as_items(folders: list[tuple[str, list[tuple[str, str, str]], list[Any]]]) -> int:
    """The most siblings the engine's reading would take for one run's items: one family of
    words holding the same files, unless they are a series of days."""
    groups: dict[tuple[Any, ...], list[tuple[tuple[str, ...], tuple[str, ...]]]] = {}
    for name, components, files in folders:
        key = (name_words(components), alike_shape(name, files))
        groups.setdefault(key, []).append((series_remainder(components), name_dates(components)))
    # A folder alone agrees with nothing.
    agreeing = [members for members in groups.values() if len(members) > 1]
    return max((len(members) for members in agreeing if not series_of_days(members)), default=0)


def _separate_experiments(seed: int, *, longest: int = 40, counted: bool = False) -> int:
    """The most sibling folders read as one run's items among dated experiments made one by one.

    One parent of 2 to 80 dated experiment folders: conditions of one or two invented words, each
    repeated over one to ``longest`` days, holding the same files every day by habit (only common
    file names, or files named with their words). Conditions may share words by chance.
    ``counted`` names each day's folder with the day's number too (``20240603_kelt_d07``).
    """
    rng = random.Random(seed)
    siblings = rng.randint(2, 80)
    folders: list[tuple[str, list[tuple[str, str, str]], list[Any]]] = []
    start = dt.date(2021, 1, 4)
    while len(folders) < siblings:
        words = rng.sample(STEMS[:20], rng.randint(1, 2))
        days = rng.randint(1, longest)
        habit: list[tuple[str, str | None]]
        if rng.random() < 0.5:
            habit = [
                (name, name.rsplit(".", 1)[1]) for name in rng.sample(_COMMON, rng.randint(1, 3))
            ]
        else:
            stem = "_".join(words)
            chosen = rng.sample(_SUFFIXES, rng.randint(1, 2))
            habit = [
                (f"{stem}_{number:03d}_{suffix}.csv", "csv")
                for number in range(1, rng.randint(1, 4) + 1)
                for suffix in chosen
            ]
        day = start + dt.timedelta(days=rng.randint(0, 900))
        for offset in range(days):
            date = f"{day + dt.timedelta(days=offset):%Y%m%d}"
            components = [("date", date, date), *(("word", word, word) for word in words)]
            name = f"{date}_{'_'.join(words)}"
            if counted:
                components.append(("indexed", f"d{offset + 1:02d}", f"d{offset + 1}"))
                name += f"_d{offset + 1:02d}"
            folders.append((name, components, habit))
    return _read_as_items(folders[:siblings])


def _per_item_run(seed: int) -> int:
    """The most item folders read as one run's in a run of 10 to 400 made on one to five days,
    one in twenty short a file."""
    rng = random.Random(10_000 + seed)
    word = rng.choice(STEMS)
    patterns = rng.sample(
        [
            ("tracks.csv", "csv"),
            ("params.txt", "txt"),
            ("{item}_tracks.csv", "csv"),
            ("frame_{number:04d}.tif", "tif"),
            ("settings.json", "json"),
        ],
        rng.randint(1, 3),
    )
    folders: list[tuple[str, list[tuple[str, str, str]], list[Any]]] = []
    # A run's movies come from a few days of acquisition, many to a day.
    sessions = [
        f"23{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}" for _ in range(rng.randint(1, 5))
    ]
    for number in range(rng.randint(10, 400)):
        letter = _ITEM_LETTERS[rng.randint(0, 7)]
        date = rng.choice(sessions)
        name = f"{date}_{letter}_{word}{number:03d}"
        components = [
            ("date", date, date),
            ("word", letter, letter.casefold()),
            ("indexed", f"{word}{number:03d}", f"{word}{number}"),
        ]
        held: list[tuple[str, str | None]] = []
        for pattern, extension in patterns:
            if "{number" in pattern:
                held += [(pattern.format(number=k), extension) for k in range(rng.randint(5, 40))]
            else:
                held.append((pattern.format(item=name), extension))
        if rng.random() < 0.05 and len(held) > 1:
            held = held[1:]
        folders.append((name, components, held))
    return _read_as_items(folders)


def test_the_default_lies_above_every_chance_agreement() -> None:
    """Measured, not chosen: the figures written beside ``MINIMUM_ALIKE_ITEMS``.

    Conditions are repeated over as many as forty days, the days are read as days, and a run's
    movies are made on one to five days. The most sibling folders read as one run's items among
    separate experiments is 0, and the smallest run read as items is 27.
    """
    chance = max(_separate_experiments(seed) for seed in range(400))
    smallest_run = min(_per_item_run(seed) for seed in range(100))
    counted = max(_separate_experiments(seed, counted=True) for seed in range(400))
    assert (chance, smallest_run) == (0, 27)
    # A series whose names count its days too (``_d07``) beside the date: each day on a date of
    # its own, so days.
    assert counted == 0
    assert chance < MINIMUM_ALIKE_ITEMS <= smallest_run
