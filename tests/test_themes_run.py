"""A run's themes: the engine reads the planted pool as folders and files.

A published run's experiments are documents of the words the engine read in their folder and file
names (senses where bound, unknown words kept by frequency and co-appearance, "how" words left out
by Dictionary kind). Their themes are a projection cached by the run and the settings, never a
claim, and they recover the plant. Every word and person is invented (``fixtures/theme_pool.py``).
Two planted words are written as shipped Dictionary surfaces.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import (
    ENGINE_SURFACES,
    OUTLIERS,
    PLANT,
    THEME_INFER_RUN,
    UNDATED,
    build_theme_project,
)
from ingence.core.settings import ThemeSettings
from ingence.themes import theme_assignments, themes_engine
from ingence.themes.projection import _MEMORY, projection_path

if TYPE_CHECKING:
    from pathlib import Path

INFER = THEME_INFER_RUN


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build_theme_project(tmp_path_factory.mktemp("themes_run"))


def _by_folder(project: Path) -> dict[str, dict]:
    assigned = theme_assignments(project, INFER)
    return {row["folder"]: row | {"id": unit} for unit, row in assigned.items()}


def _ami(left, right) -> float:
    from sklearn.metrics import adjusted_mutual_info_score

    return float(adjusted_mutual_info_score(left, right))


def test_every_planted_experiment_is_a_unit_and_the_themes_recover_the_plant(built) -> None:
    project, plant = built
    rows = _by_folder(project)
    assert set(rows) == set(plant.folders.values())
    planted = [u for u, t in plant.planted.items() if t != "none"]
    placed = [u for u in planted if rows[plant.folders[u]]["theme_id"] is not None]
    assert len(placed) >= len(planted) - 2
    truth = [plant.planted[u] for u in placed]
    found = [str(rows[plant.folders[u]]["theme_id"]) for u in placed]
    assert _ami(truth, found) >= 0.9


def test_labels_name_a_bound_word_by_its_sense(built) -> None:
    from ingence.dictionary.store import DictionaryStore

    project, plant = built
    rows = _by_folder(project)
    store = DictionaryStore.builtin()
    for planted, word in (("t3", "ostrel"), ("t5", "troskel")):
        sense = store.look_up(ENGINE_SURFACES[word]).sense.term.replace("_", " ")
        unit = next(u for u, t in plant.planted.items() if t == planted)
        label = rows[plant.folders[unit]]["label"]
        assert label.split(" · ")[0] == sense, (planted, label)
    # The sense's canonical name, as the Dictionary writes it.
    unit = next(u for u, t in plant.planted.items() if t == "t1")
    assert rows[plant.folders[unit]]["label"].split(" · ")[0] == "TrackMate"
    # The surface written is not the name shown where the sense's name differs from it.
    themes = themes_engine().projection(project, INFER)["themes"]
    assert "widefield" not in json.dumps([[t["label"], t["words"]] for t in themes])


def test_how_words_are_left_out_by_their_kind(built) -> None:
    project, _ = built
    built_projection = themes_engine().projection(project, INFER)
    words = {w for row in built_projection["themes"] for w in row["words"]}
    # "results" binds to a stage and "nd2"/"csv" are extensions: none is a theme word.
    assert not words & {"results", "result", "analysis", "nd2", "csv"}


def test_the_unplaced_and_the_undated_are_listed(built) -> None:
    project, plant = built
    built_projection = themes_engine().projection(project, INFER)
    folder_of = {row["id"]: row["folder"] for row in built_projection["experiments"]}
    unplaced = {folder_of[u] for u in built_projection["unplaced"]}
    assert {plant.folders[u] for u in OUTLIERS} <= unplaced
    undated = {folder_of[u] for u in built_projection["undated"]}
    assert undated == {plant.folders[u] for u in UNDATED}
    assert built_projection["summary"]["unplaced"] == len(built_projection["unplaced"])


def test_the_year_profile_reproduces_planted_beginnings(built) -> None:
    project, plant = built
    built_projection = themes_engine().projection(project, INFER)
    rows = _by_folder(project)
    story = built_projection["story"]
    for planted, (_, (first, last), _) in PLANT.items():
        unit = next(u for u, t in plant.planted.items() if t == planted and u not in UNDATED)
        label = rows[plant.folders[unit]]["label"]
        theme = next(t for t in built_projection["themes"] if t["label"] == label)
        assert (theme["first"], theme["last"]) == (first, last), planted
        assert label in story[str(first)]["began"]


def test_who_else_by_person_folder(built) -> None:
    project, _ = built
    found = themes_engine().projection(project, INFER)["who_else"]
    assert {row["person"] for row in found["people"]} == {"kipper", "tovald", "zandri"}
    shared = {row["word"]: row for row in found["shared_words"]}
    assert set(shared["hoben"]["people"]) == set(PLANT["t4"][2])


def test_the_projection_is_cached_by_run_and_settings_and_is_reproducible(built) -> None:
    project, _ = built
    first = themes_engine().projection(project, INFER)
    cached = sorted(projection_path(project, INFER).glob("*.json"))
    assert len(cached) == 1
    text = cached[0].read_text(encoding="utf-8")
    # Built again from nothing: byte for byte the same.
    _MEMORY.clear()
    shutil.rmtree(projection_path(project, INFER))
    again = themes_engine().projection(project, INFER)
    assert again == first
    assert sorted(projection_path(project, INFER).glob("*.json"))[0].read_text("utf-8") == text
    # Other settings, another projection beside it; the first is kept.
    other = themes_engine().projection(project, INFER, ThemeSettings(min_theme_size=4))
    assert other["settings_digest"] != first["settings_digest"]
    assert len(sorted(projection_path(project, INFER).glob("*.json"))) == 2


def test_themes_write_nothing_into_the_run(built) -> None:
    from ingence.storage.commit import run_path

    project, _ = built
    before = sorted(p.name for p in run_path(project, INFER).rglob("*"))
    _MEMORY.clear()
    themes_engine().projection(project, INFER, ThemeSettings(seeds=3))
    assert sorted(p.name for p in run_path(project, INFER).rglob("*")) == before


def test_export_writes_the_experiment_and_theme_tables_as_csv(built, tmp_path) -> None:
    import csv

    from typer.testing import CliRunner

    from ingence.cli.main import app

    project, plant = built
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app, ["export", "themes", "--project", str(project), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    with (out / "themes_experiments.csv").open(encoding="utf-8", newline="") as handle:
        experiments = list(csv.DictReader(handle))
    with (out / "themes.csv").open(encoding="utf-8", newline="") as handle:
        themes = list(csv.DictReader(handle))
    assert len(experiments) == len(plant.documents)
    assert {"id", "folder", "year", "theme", "theme_label", "broad_theme", "broad_label"} <= set(
        experiments[0]
    )
    assert len(themes) == len(PLANT)
    assert {"theme", "label", "experiments", "first", "last", "going", "direction"} <= set(
        themes[0]
    )
    # A kind the verb does not know is refused, and says which it knows.
    refused = CliRunner().invoke(app, ["export", "nothing", "--project", str(project)])
    assert refused.exit_code != 0
    assert "themes" in refused.output


def test_the_dictionary_is_composed_again_only_when_what_it_is_made_of_changes(tmp_path) -> None:
    from ingence.themes.projection import _dictionary_of

    class Store:
        def __init__(self, number: int) -> None:
            self.number = number

        def fingerprint(self) -> str:
            return f"f{self.number}"

    composed: list[int] = []

    def compose(project):
        composed.append(1)
        return Store(len(composed))

    (tmp_path / "record").mkdir()
    (tmp_path / "settings.yaml").write_text("a: 1\n")
    assert _dictionary_of(tmp_path, compose)[1] == "f1"
    assert _dictionary_of(tmp_path, compose)[1] == "f1"
    assert len(composed) == 1
    # A person's answer lands in the Record: composed again.
    (tmp_path / "record" / "answer.parquet").write_bytes(b"x")
    assert _dictionary_of(tmp_path, compose)[1] == "f2"
    assert len(composed) == 2


def _export(project, out, *more):
    from typer.testing import CliRunner

    from ingence.cli.main import app

    return CliRunner().invoke(
        app, ["export", "themes", "--project", str(project), "--out", str(out), *more]
    )


def test_export_never_writes_into_a_root_or_the_project_s_own_folders(built) -> None:
    """An export never writes into a data root or the project's own folders, wherever ``--out``
    points.

    An export into a declared root (source data, which Ingence never changes) or into the project's
    runs, staging, Record, Rulebook or Dictionary is refused, and nothing is written there.
    """
    project, _ = built
    pool = project.parent / "pool"
    before = sorted(p.relative_to(pool).as_posix() for p in pool.rglob("*"))
    for out in (
        pool / "kipper" / "exported",
        pool,
        project / "runs" / "x",
        project / ".tmp",
        project / "record",
        project / "rulebook" / "x",
        project / "dictionary",
    ):
        result = _export(project, out)
        assert result.exit_code != 0, out
        assert "refused" in result.output
    assert sorted(p.relative_to(pool).as_posix() for p in pool.rglob("*")) == before
    assert not (project / "runs" / "x").exists()


def test_export_follows_a_dotdot_after_a_link_as_the_system_does(built, tmp_path) -> None:
    """``<elsewhere>/lnk`` leads to a folder inside the pool, so ``<elsewhere>/lnk/..`` is the pool.

    The destination is not normalized by spelling first, which would judge it a place beside the
    link.
    """
    project, _ = built
    pool = project.parent / "pool"
    inside = next(path for path in sorted(pool.iterdir()) if path.is_dir())
    link = tmp_path / "lnk"
    try:
        link.symlink_to(inside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")
    before = sorted(p.relative_to(pool).as_posix() for p in pool.rglob("*"))
    for out in (link / ".." / "exported_here", link / ".." / inside.name / "deeper"):
        result = _export(project, out)
        assert result.exit_code != 0, out
        assert "refused" in result.output
    assert sorted(p.relative_to(pool).as_posix() for p in pool.rglob("*")) == before
    assert not (pool / "exported_here").exists()


def test_export_expands_a_tilde_once_for_the_check_and_the_write(
    built, tmp_path, monkeypatch
) -> None:
    """``--out=~/folder`` reaches the command unexpanded. The check judged the home folder while
    the write made a folder named ``~`` in the working directory, here inside the pool."""
    project, _ = built
    pool = project.parent / "pool"
    inside = next(path for path in sorted(pool.iterdir()) if path.is_dir())
    before = sorted(p.relative_to(pool).as_posix() for p in pool.rglob("*"))
    monkeypatch.chdir(inside)
    for home, allowed in ((tmp_path / "home", True), (inside, False)):
        home.mkdir(exist_ok=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        result = _export(project, "~/exported_here")
        assert (result.exit_code == 0) is allowed, result.output
    assert (tmp_path / "home" / "exported_here" / "themes.csv").is_file()
    assert not (inside / "~").exists()
    assert sorted(p.relative_to(pool).as_posix() for p in pool.rglob("*")) == before


def test_export_refuses_an_exports_folder_that_is_a_link_into_the_root(built, tmp_path) -> None:
    """The nearest-wins rule took a link at ``projections/exports`` for the exports folder, so an
    export, with or without ``--out``, wrote into the folder the link led to inside the pool."""
    from typer.testing import CliRunner

    from ingence.cli.main import app

    project, _ = built
    pool = project.parent / "pool"
    inside = next(path for path in sorted(pool.iterdir()) if path.is_dir())
    exports = project / "projections" / "exports"
    held = tmp_path / "exports_held"
    if exports.exists():
        exports.rename(held)  # the other tests' exports, put back after
    before = sorted(p.relative_to(pool).as_posix() for p in pool.rglob("*"))
    try:
        try:
            exports.symlink_to(inside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this system does not let a test make a symbolic link")
        for out in (exports / "mine", None):
            arguments = ["export", "themes", "--project", str(project)]
            if out is not None:
                arguments += ["--out", str(out)]
            result = CliRunner().invoke(app, arguments)
            assert result.exit_code != 0, (out, result.output)
            assert "refused" in result.output
        assert sorted(p.relative_to(pool).as_posix() for p in pool.rglob("*")) == before
    finally:
        if exports.is_symlink():
            exports.unlink()
        if held.exists():
            held.rename(exports)


def test_export_does_not_write_through_a_second_name_of_an_indexed_file(built, tmp_path) -> None:
    """A folder an earlier export marked, whose table a person replaced by a hard link to an
    indexed file: the next export, given ``--overwrite`` (a folder outside the project is never
    written again without it), replaces the table and leaves the indexed file as it was."""
    import os

    project, _ = built
    pool = project.parent / "pool"
    indexed = next(path for path in sorted(pool.rglob("*")) if path.is_file())
    kept = indexed.read_bytes()
    ours = tmp_path / "ours"
    assert _export(project, ours).exit_code == 0
    (ours / "themes.csv").unlink()
    try:
        os.link(indexed, ours / "themes.csv")
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a hard link")
    assert _export(project, ours).exit_code == 2
    assert indexed.read_bytes() == kept
    assert _export(project, ours, "--overwrite").exit_code == 0
    assert indexed.read_bytes() == kept
    assert (ours / "themes.csv").read_bytes() != kept


def test_export_leaves_everything_as_it_was_when_a_table_is_not_writable(built, tmp_path) -> None:
    """A shell redirect to a read-only file fails; an export replaced it. A table that exists and
    cannot be written is refused before any table is written: the other table, the marker and the
    read-only one are as they were, and the command says which and exits 1."""
    import os

    project, _ = built
    ours = tmp_path / "ours"
    assert _export(project, ours).exit_code == 0
    locked = ours / "themes.csv"
    locked.chmod(0o444)
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("this account can write a read-only file (the super-user can)")
        names = sorted(path.name for path in ours.iterdir())
        before = {
            name: ((ours / name).read_bytes(), (ours / name).stat().st_mtime_ns) for name in names
        }
        result = _export(project, ours, "--overwrite")
        assert result.exit_code == 1, result.output
        assert f"ingence export: could not write {locked} (read-only)" in " ".join(
            result.output.split()
        )
        assert "Traceback" not in result.output
        assert sorted(path.name for path in ours.iterdir()) == names  # nothing left beside them
        assert {
            name: ((ours / name).read_bytes(), (ours / name).stat().st_mtime_ns) for name in names
        } == before
        # Writable again, the next export goes through.
        locked.chmod(0o644)
        assert _export(project, ours, "--overwrite").exit_code == 0
    finally:
        locked.chmod(0o644)


def test_export_never_overwrites_a_file_it_did_not_write(built, tmp_path) -> None:
    project, _ = built
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    (theirs / "themes.csv").write_text("a person's own table\n")
    result = _export(project, theirs)
    assert result.exit_code != 0
    assert (theirs / "themes.csv").read_text() == "a person's own table\n"
    # A folder the export made is written again when asked to (``--overwrite``), never without
    # it; an empty one is taken.
    ours = tmp_path / "ours"
    assert _export(project, ours).exit_code == 0
    again = _export(project, ours)
    assert again.exit_code == 2 and "--overwrite" in " ".join(again.output.split())
    assert _export(project, ours, "--overwrite").exit_code == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _export(project, empty).exit_code == 0


def test_experiment_themes_gives_each_experiment_its_label(built) -> None:
    """Each experiment's theme label, for the run's manifest: None where unplaced."""
    from ingence.themes import experiment_themes

    project, _ = built
    labels = experiment_themes(project, INFER)
    assigned = theme_assignments(project, INFER)
    assert labels == {unit: row["label"] for unit, row in assigned.items()}
    assert sum(label is None for label in labels.values()) == len(
        themes_engine().projection(project, INFER)["unplaced"]
    )


def test_export_refuses_a_root_or_the_project_written_in_another_case(built) -> None:
    """The guard compares paths by the folders' identity on disk, never by their spelling.

    Compared as typed, on a disk that ignores case ``POOL/fresh_sub`` would write into the data
    root, and ``RUNS/x`` into the project.
    """
    project, _ = built
    pool = project.parent / "pool"
    if not (project.parent / "POOL").exists():
        pytest.skip("this disk tells names apart by case")
    before = sorted(p.name for p in pool.rglob("*"))
    for out in (
        project.parent / "POOL" / "fresh_sub",
        project / "RUNS" / "x",
        project / "Record" / "x",
    ):
        result = _export(project, out)
        assert result.exit_code != 0, out
        assert "refused" in result.output
    assert sorted(p.name for p in pool.rglob("*")) == before
    assert not (pool / "fresh_sub").exists()


def test_export_refuses_any_project_folder_but_its_exports(built) -> None:
    project, _ = built
    for out in (
        project / "jobs" / "x",
        project / "workspace" / "x",
        project / "imports" / "x",
        project / "listings" / "x",
        project / "projections" / "search" / "x",
        project / "a_new_top_folder" / "x",
        project,
    ):
        result = _export(project, out)
        assert result.exit_code != 0, out
        assert not out.exists() or out == project, out
    assert _export(project, project / "projections" / "exports" / "mine").exit_code == 0


def test_the_first_export_into_the_exports_folder_is_taken(built) -> None:
    """With no ``projections/exports`` yet, the guard must not take the nearest existing folder
    (``projections``, inside the project) and refuse the first export into it.

    The test removes the folder first, so it cannot pass only because an earlier test made it.
    """
    import shutil

    project, _ = built
    shutil.rmtree(project / "projections" / "exports", ignore_errors=True)
    assert _export(project, project / "projections" / "exports" / "first").exit_code == 0
    assert (project / "projections" / "exports" / "first" / "themes.csv").is_file()


def test_two_simultaneous_requests_build_the_projection_once(built, monkeypatch) -> None:
    """Two requests at once do not each spend the whole build: the second waits for the first's
    result.
    """
    import threading
    import time

    from ingence.themes import projection as projection_module

    project, _ = built
    calls: list[int] = []
    original = projection_module.build_projection

    def counted(*args, **kwargs):
        calls.append(1)
        time.sleep(0.5)
        return original(*args, **kwargs)

    monkeypatch.setattr(projection_module, "build_projection", counted)
    settings = ThemeSettings(seeds=5)
    _MEMORY.clear()
    for cached in projection_path(project, INFER).glob("*.json"):
        cached.unlink()
    answers: list[dict] = []
    threads = [
        threading.Thread(
            target=lambda: answers.append(themes_engine().projection(project, INFER, settings))
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    assert len(answers) == 2 and answers[0] == answers[1]


def test_experiment_themes_or_reason_never_raises_for_what_a_manifest_meets(built) -> None:
    """For the run's manifest: themes, or why they were not computed, for a run that is not
    published and for a missing component.

    Unknown is not absent.
    """
    from ingence.themes import THEME_ENGINES, experiment_themes, experiment_themes_or_reason

    project, _ = built
    labels, reason = experiment_themes_or_reason(project, INFER)
    assert reason is None and labels == experiment_themes(project, INFER)
    labels, reason = experiment_themes_or_reason(project, "run_20990101T000000Z_ffffffffffff")
    assert labels is None and "run_20990101T000000Z_ffffffffffff" in reason
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        labels, reason = experiment_themes_or_reason(project, INFER)
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    assert labels is None and "switched off" in reason


def test_export_refuses_a_link_into_a_root_or_the_project(built, tmp_path) -> None:
    """A symbolic link to a folder inside a data root (or the project's runs), with a new folder
    below it, does not pass the guard.

    The guard must not climb only the link's own parents. The folders above the link's target are
    compared too, and a refused export makes nothing.
    """
    project, _ = built
    pool = project.parent / "pool"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "link_into_root").symlink_to(pool / "kipper", target_is_directory=True)
    (elsewhere / "link_into_runs").symlink_to(project / "runs", target_is_directory=True)
    exports = project / "projections" / "exports"
    made_before = exports.exists()
    for out in (elsewhere / "link_into_root" / "new", elsewhere / "link_into_runs" / "x"):
        result = _export(project, out)
        assert result.exit_code != 0, out
        assert "refused" in result.output
    assert not (pool / "kipper" / "new").exists()
    assert not (project / "runs" / "x").exists()
    assert exports.exists() == made_before


def test_experiment_themes_or_reason_says_any_failure_in_words(built, monkeypatch) -> None:
    """Themes are an optional projection: a manifest never fails on them, whatever goes wrong
    (a damaged table raised the reader's own error)."""
    from ingence.themes import experiment_themes_or_reason
    from ingence.themes import projection as projection_module

    project, _ = built

    def broken(*args, **kwargs):
        message = "a damaged table"
        raise RuntimeError(message)

    monkeypatch.setattr(projection_module, "build_projection", broken)
    _MEMORY.clear()
    settings = ThemeSettings(seeds=6)
    for cached in projection_path(project, INFER).glob("*.json"):
        cached.unlink()
    labels, reason = experiment_themes_or_reason(project, INFER, settings)
    assert labels is None
    assert "RuntimeError" in reason and "a damaged table" in reason
