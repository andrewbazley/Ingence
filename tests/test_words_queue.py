"""``GET /api/v1/words/queue``: the words a person can teach, most files first.

The behaviour: the queue lists, from the run's own tables and the Dictionary as it loads, the
words the engine is unsure of, most files first. Its meter counts the unknown word parts the rows
cover, and a read writes nothing. Built on the invented pool of ``fixtures/words_pool.py``, whose
plant is written before the expectations:

- ``vorn`` is in every name (32 word parts); ``quolt`` 6, ``quolts`` 3, ``quoltan`` 3;
  ``fennowic`` 3 and ``fennowik`` 3; ``zib`` and ``kelt`` 4 each; ``confokal`` 3; and ``dana``,
  ``pool`` and ``whitfield`` once each, in the folders above the experiments.
- ``cluster`` is contested, ``confocal`` is bound by a pack the project has not accepted, and
  ``zibKelt`` is a spelling a capital divides.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.words_pool import build_words_project, wait_for_a_later_second
from ingence.api.v1 import V1Context, dispatch, load_routes
from ingence.api.v1.words_queue import QueueWords
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import RecordEventKind
from ingence.record.log import append_event
from ingence.storage.commit import read_pointer

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

load_routes()

#: The word parts each word covers in the plant, which the rows must add up from.
PARTS = {
    "vorn": 32,
    "quolt": 6,
    "kelt": 4,
    "zib": 4,
    "cluster": 3,
    "confocal": 3,
    "confokal": 3,
    "fennowic": 3,
    "fennowik": 3,
    "quolts": 3,
    "quoltan": 3,
    "dana": 1,
    "pool": 1,
    "whitfield": 1,
}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_words_project(tmp_path_factory.mktemp("words_queue"))


@pytest.fixture(scope="module")
def uninferred(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_words_project(tmp_path_factory.mktemp("words_queue_census"), infer=False)


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def ask(project: Path, **query: str) -> dict[str, Any]:
    run = read_pointer(project).run_id  # type: ignore[union-attr]
    context = V1Context(project=project, run_current=run, run_pinned=run, job_running=False)
    status, body = dispatch(
        context, "words/queue", {name: [value] for name, value in query.items()}
    )
    assert status == 200, body
    QueueWords.model_validate(body)
    return body


def say(project: Path, target: str, kind: RecordEventKind, payload: Mapping[str, Any]) -> None:
    run = read_pointer(project).run_id  # type: ignore[union-attr]
    append_event(
        project,
        kind=kind,
        actor="Dana Whitfield",
        occurred_at="2026-01-03T00:00:00Z",
        target=target,
        against_run_id=run,
        payload=dict(payload),
        scope=PROJECT_SCOPE,
    )


def words_of(body: dict[str, Any]) -> list[str]:
    return [row["word"] for row in body["rows"]]


def snapshot(project: Path) -> dict[str, tuple[int, int]]:
    """Every file under the project, with its size and its time, as a read must leave them."""
    found: dict[str, tuple[int, int]] = {}
    for folder, _, names in os.walk(project):
        for name in names:
            path = os.path.join(folder, name)
            info = os.stat(path)
            found[os.path.relpath(path, project)] = (info.st_size, info.st_mtime_ns)
    return found


# -- rows, order and the meter -----------------------------------------------------------------


def test_the_rows_are_the_words_the_engine_is_unsure_of_most_files_first(project: Path) -> None:
    body = ask(project)
    rows = body["rows"]
    # Files, most first; the word breaks a tie, so the order is stable. A word a shipped domain
    # section binds (confocal) is understood and not listed.
    assert [(row["files"], row["word"]) for row in rows] == sorted(
        ((row["files"], row["word"]) for row in rows), key=lambda item: (-item[0], item[1])
    )
    assert "confocal" not in words_of(body)
    assert words_of(body)[:3] == ["vorn", "quolt", "kelt"]
    shown = set(words_of(body))
    assert {"vorn", "quolt", "cluster", "fennowic", "zibkelt"} <= shown
    # confokal is read as confocal, marked corrected: understood, not listed.
    assert "confokal" not in shown
    assert body["total"] == len(rows) == 13 and body["left_out"] == 0
    first = rows[0]
    assert (first["word"], first["files"], first["occurrences"]) == ("vorn", 32, 32)
    assert first["examples"] == ["vorn"]
    assert first["target"] == "term:vorn"
    assert first["answered"] is None and first["waiting"] is False
    # The folders holding its names: ten experiments' folder names sit in the person's folder, and
    # two instrument files sit in the first experiment.
    assert first["where"][0] == {"path": "Dana_Whitfield", "files": 10}
    assert {"path": "Dana_Whitfield/20240301 quolt vorn", "files": 2} in first["where"]
    assert first["where_not_shown"] == 0


def test_every_state_is_one_the_row_can_be_filtered_by(project: Path) -> None:
    body = ask(project)
    states = {row["word"]: row["state"] for row in body["rows"]}
    assert states["vorn"] == states["quolt"] == states["fennowic"] == "unknown"
    assert states["cluster"] == "contested"
    assert "confocal" not in states  # understood: every domain's words load
    assert "confokal" not in states  # read as confocal, corrected
    assert states["fennowik"] == "variant"
    assert states["quolts"] == "variant"
    assert states["zibkelt"] == "glued"
    assert body["by_state"] == {
        "contested": 1,
        "glued": 1,
        "unknown": 9,
        "variant": 2,
    }


def test_the_meter_adds_the_unknown_word_parts_by_hand(project: Path) -> None:
    body = ask(project)
    rows = body["rows"]
    assert body["reading"]["words"]["unknown"] == body["unknown_parts"] == 61
    assert "61 unknown" in body["reading"]["said"]
    running = 0
    for row in rows:
        counted = PARTS.get(row["word"], 0)
        # A contested or pack word's parts are not unknown ones; a divided spelling's are its
        # pieces', which have rows of their own.
        covers = row["state"] not in ("contested", "pack", "glued")
        assert row["unknown_parts"] == (counted if covers else 0), row["word"]
        running += row["unknown_parts"]
        assert row["covered"] == running, row["word"]
    # Every unknown word part is covered by the rows together: a count, over its denominator.
    assert rows[-1]["covered"] == body["unknown_parts"]


def test_each_row_carries_its_experiments_counted_as_the_themes_count_them(project: Path) -> None:
    rows = {row["word"]: row for row in ask(project)["rows"]}
    assert rows["vorn"]["experiments"] == 10
    assert rows["quolt"]["experiments"] == 2
    assert rows["quolts"]["experiments"] == 1
    # Folders above the experiments belong to none: that is zero experiments, and a count.
    assert rows["dana"]["experiments"] == 0
    # A divided spelling is counted as the words are, from the names that carry it: None means
    # one thing only, that the run concluded no experiment.
    assert rows["zibkelt"]["experiments"] == 1


def test_a_divided_spelling_counts_the_times_the_names_write_it_and_invents_no_number(
    tmp_path: Path,
) -> None:
    """Its occurrences are the times the names write it whole (a name writing it twice counts
    twice), never the number of names standing in for them."""
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    pool = tmp_path / "pool" / "Dana_Whitfield" / "20240501 first"
    for name in ("zibKelt_vorn_01.nd2", "zibKelt_zibKelt_02.nd2", "zib_03.nd2", "kelt_04.nd2"):
        pool.mkdir(parents=True, exist_ok=True)
        (pool / name).write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[tmp_path / "pool"])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    row = next(row for row in ask(project.path)["rows"] if row["word"] == "zibkelt")
    assert (row["state"], row["files"], row["occurrences"]) == ("glued", 2, 3)
    assert row["experiments"] is None  # a census alone concluded no experiment


def test_where_no_experiment_was_concluded_the_count_is_none_not_zero(uninferred: Path) -> None:
    rows = ask(uninferred)["rows"]
    assert rows and all(row["experiments"] is None for row in rows)


def test_a_row_carries_its_senses_and_what_a_contested_word_ties_on(project: Path) -> None:
    rows = {row["word"]: row for row in ask(project)["rows"]}
    assert rows["vorn"]["senses"] == []
    tied = rows["cluster"]["senses"]
    assert {sense["role"] for sense in tied} == {"tied"}
    assert {sense["term"] for sense in tied} == {"workstation", "clustering"}
    shown = {row["word"]: row for row in ask(project, understood="show")["rows"]}
    (pack,) = shown["confocal"]["senses"]
    assert (pack["role"], pack["origin"], pack["pack"]) == ("in_force", "builtin", "microscopy")


def test_a_divided_spelling_carries_how_the_case_reading_read_it(project: Path) -> None:
    row = next(row for row in ask(project)["rows"] if row["word"] == "zibkelt")
    glued = row["glued"]
    assert row["target"] == "split:zibkelt" and glued["target"] == "split:zibkelt"
    assert (glued["written"], glued["pieces"]) == ("zibKelt", ["zib", "kelt"])
    assert (glued["whole_files"], glued["alone_files"]) == (3, [1, 1])
    assert (glued["reading"], glued["tier"], glued["asked"]) == ("whole", "unit", False)
    assert "3 names carry zibKelt" in glued["because"]
    assert row["examples"] == ["zibKelt"]
    # Equally many files each, so the spelling breaks the tie.
    assert [(v["spelling"], v["reason"], v["files"]) for v in row["variants"]] == [
        ("kelt", "glued", 4),
        ("zib", "glued", 4),
    ]


# -- variants, as the queue offers them -----------------------------------------------------------


def test_a_word_is_offered_beside_its_variants_with_why_and_how_many_files(project: Path) -> None:
    rows = {row["word"]: row for row in ask(project)["rows"]}
    (plural,) = rows["quolt"]["variants"]
    assert (plural["spelling"], plural["reason"], plural["reason_said"]) == (
        "quolts",
        "plural",
        "a plural of it",
    )
    assert plural["files"] == 3 and plural["distance"] is None
    assert plural["distinct_target"] == "distinct:quolt+quolts" and plural["offer"] == "same"
    # A slip the resolver corrects (confokal) is not a row.
    assert plural["known_as"] is None and plural["known_kind"] is None
    assert rows["fennowik"]["of"] == "fennowic"


def test_a_pair_called_unrelated_is_not_offered_again(project: Path) -> None:
    assert "fennowik" in {
        v["spelling"]
        for r in ask(project)["rows"]
        if r["word"] == "fennowic"
        for v in r["variants"]
    }
    say(project, "distinct:fennowic+fennowik", RecordEventKind.REJECT, {"verdict": "unrelated"})
    rows = {row["word"]: row for row in ask(project)["rows"]}
    assert rows["fennowic"]["variants"] == [] and rows["fennowik"]["variants"] == []
    assert rows["fennowik"]["state"] == "unknown" and rows["fennowik"]["of"] is None


# -- filters and paging ---------------------------------------------------------------------------


def test_filters_by_state_kind_and_folder(project: Path) -> None:
    assert words_of(ask(project, state="slip")) == []  # confokal is corrected
    assert sorted(words_of(ask(project, state="variant"))) == ["fennowik", "quolts"]
    # confokal is read as confocal, corrected: understood too.
    assert words_of(ask(project, kind="technique", understood="show")) == ["confocal", "confokal"]
    folder = "Dana_Whitfield/20240301 quolt vorn"
    below = ask(project, folder=folder)
    # The counts are of the names below the folder: a folder's name and its two files.
    assert [(row["word"], row["files"]) for row in below["rows"]] == [("quolt", 3), ("vorn", 3)]
    # The chips count the rows after every filter but the state.
    assert ask(project, state="slip")["by_state"] == ask(project)["by_state"]


def test_under_a_folder_the_meters_denominator_is_the_folders_own(project: Path) -> None:
    """``covered`` counts the unknown word parts below the folder, so the number it is over is
    those too: the last row covers all of it, not a small share of the whole run's."""
    whole = ask(project)
    below = ask(project, folder="Dana_Whitfield/20240301 quolt vorn")
    assert whole["unknown_parts"] == 61
    assert below["unknown_parts"] == sum(row["unknown_parts"] for row in below["rows"]) == 6
    assert below["rows"][-1]["covered"] == below["unknown_parts"]
    # The reading figure is always the run's.
    assert below["reading"] == whole["reading"]


def test_understood_words_are_shown_on_request_so_they_can_be_confirmed_too(project: Path) -> None:
    say(
        project,
        "pack:microscopy",
        RecordEventKind.CONFIRM,
        {"question": "load the microscopy pack"},
    )
    assert "confocal" not in words_of(ask(project))
    body = ask(project, understood="show")
    row = next(row for row in body["rows"] if row["word"] == "confocal")
    assert row["state"] == "understood" and row["unknown_parts"] == 0
    assert row["senses"][0]["pack"] == "microscopy"
    assert body["understood_filter"] == "show"


def test_paging_keeps_the_meter_running_across_pages(project: Path) -> None:
    everything = ask(project)["rows"]
    second = ask(project, limit="4", offset="4")
    assert [row["word"] for row in second["rows"]] == [row["word"] for row in everything[4:8]]
    assert (second["total"], second["offset"], second["limit"], second["left_out"]) == (13, 4, 4, 5)
    # The first row of the second page is covered by every row before it, on the first page too.
    assert [row["covered"] for row in second["rows"]] == [row["covered"] for row in everything[4:8]]
    first = second["rows"][0]
    assert (
        first["covered"]
        == everything[3]["covered"] + first["unknown_parts"]
        > first["unknown_parts"]
    )


def test_a_filter_or_a_page_that_is_not_one_is_refused_in_words(project: Path) -> None:
    run = read_pointer(project).run_id  # type: ignore[union-attr]
    context = V1Context(project=project, run_current=run, run_pinned=run, job_running=False)
    for query in ({"state": ["maybe"]}, {"answered": ["some"]}, {"limit": ["0"]}):
        status, body = dispatch(context, "words/queue", query)
        assert status == 400, query
        assert body["error"]["code"] == "bad_parameter"


# -- what a person has said -----------------------------------------------------------------------


def test_an_answered_word_leaves_the_queue_and_comes_back_on_request_with_who_and_waiting(
    project: Path,
) -> None:
    say(
        project,
        "term:quolt",
        RecordEventKind.DEFINE,
        {"raised": "quolt", "answer": "quolt_assay", "theme_kind": "reagent"},
    )
    assert "quolt" not in words_of(ask(project))
    assert words_of(ask(project, answered="only")) == ["quolt"]
    shown = next(row for row in ask(project, answered="show")["rows"] if row["word"] == "quolt")
    answer = shown["answered"]
    assert (answer["actor"], answer["kind"], answer["target"]) == (
        "Dana Whitfield",
        "define",
        "term:quolt",
    )
    assert answer["said"] == "means quolt_assay (reagent)" and answer["term"] == "quolt_assay"
    # It is read by the next run, not this one: it waits, and the loaded Dictionary shows it.
    assert shown["waiting"] is True
    assert [(s["term"], s["origin"]) for s in shown["senses"]] == [("quolt_assay", "user")]
    assert ask(project)["waiting"] == 1


def test_a_not_a_word_and_a_divided_spelling_s_answer_are_answers_too(project: Path) -> None:
    say(project, "term:vorn", RecordEventKind.REJECT, {"raised": "vorn", "verdict": "not_a_word"})
    say(project, "split:zibkelt", RecordEventKind.ASSIGN, {"reading": "pieces"})
    body = ask(project, answered="only")
    said = {row["word"]: row["answered"]["said"] for row in body["rows"]}
    assert said == {"vorn": "not a word", "zibkelt": "separate words"}
    glued = next(row for row in body["rows"] if row["word"] == "zibkelt")["glued"]
    assert (glued["reading"], glued["tier"]) == ("pieces", "answered")


def test_a_meaning_kept_below_a_folder_is_the_word_s_answer_only_where_nothing_wider_is(
    project: Path,
) -> None:
    folder = "Dana_Whitfield"
    below = {"raised": "vorn", "answer": "vorn_everywhere", "theme_kind": "reagent"}
    say(project, f"term:vorn below {folder}", RecordEventKind.DEFINE, below)
    row = next(r for r in ask(project, answered="only")["rows"] if r["word"] == "vorn")
    assert row["answered"]["below"] == folder
    say(project, "term:vorn", RecordEventKind.DEFINE, {**below, "answer": "vorn_project"})
    row = next(r for r in ask(project, answered="only")["rows"] if r["word"] == "vorn")
    assert (row["answered"]["below"], row["answered"]["term"]) == (None, "vorn_project")


# -- a read writes nothing ------------------------------------------------------------------------


def test_a_read_writes_nothing_to_the_project(project: Path) -> None:
    say(
        project,
        "term:quolt",
        RecordEventKind.DEFINE,
        {"raised": "quolt", "answer": "a", "theme_kind": "reagent"},
    )
    before = snapshot(project)
    for query in ({}, {"understood": "show", "answered": "show"}, {"state": "glued"}):
        ask(project, **query)
    assert snapshot(project) == before


# -- the sources that need the run's names read together ------------------------------------------


@pytest.fixture(scope="module")
def company(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Names that hold initials, and two spellings that stand in each other's place."""
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("words_company")
    pool = base / "pool" / "Dana_Whitfield"
    plan = {
        "20240401 first": [
            "african_clawed_frog_01.csv",
            "african_clawed_frog_02.csv",
            "ACF_03.csv",
        ],
        "20240402 second": ["ostrel_dapi_gfp_01.csv", "ostrel_dapi_gfp_02.csv"],
        "20240403 third": ["gavish_dapi_gfp_01.csv", "gavish_dapi_gfp_02.csv"],
    }
    for folder, names in plan.items():
        for name in names:
            path = pool / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    project = Project.create(base / "project", created_at="t", roots=[base / "pool"])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return project.path


def test_initials_and_the_word_they_stand_for_are_offered_where_the_run_writes_both(
    company: Path,
) -> None:
    rows = {row["word"]: row for row in ask(company, understood="show")["rows"]}
    (spelled,) = rows["acf"]["variants"]
    assert (spelled["spelling"], spelled["reason"]) == ("african clawed frog", "spelled_out")
    assert spelled["reason_said"] == "what its initials stand for"
    (initials,) = rows["african clawed frog"]["variants"]
    assert (initials["spelling"], initials["reason"]) == ("acf", "initials")
    assert rows["african clawed frog"]["state"] == "understood"
    # No experiment was concluded in a census alone: the count is None, not zero.
    assert rows["acf"]["experiments"] is None


def test_two_spellings_taking_each_other_s_place_are_offered_as_different_things_first(
    company: Path,
) -> None:
    rows = {row["word"]: row for row in ask(company)["rows"]}
    (other,) = [v for v in rows["ostrel"]["variants"] if v["reason"] == "same_slot"]
    assert (other["spelling"], other["offer"]) == ("gavish", "kind")
    assert other["reason_said"] == "they take each other's place"
    assert [v["spelling"] for v in rows["gavish"]["variants"] if v["reason"] == "same_slot"] == [
        "ostrel"
    ]


def test_each_source_is_switched_off_by_its_own_setting(company: Path) -> None:
    from ingence.core.project import Project
    from ingence.core.settings import VariantSettings

    project = Project.open(company)
    held = project.settings
    try:
        off = VariantSettings(same_slot=False, initials=False)
        project.save_settings(
            held.model_copy(
                update={"dictionary": held.dictionary.model_copy(update={"variants": off})}
            )
        )
        rows = {row["word"]: row for row in ask(company)["rows"]}
        assert rows["acf"]["variants"] == [] and rows["ostrel"]["variants"] == []
    finally:
        project.save_settings(held)


def test_a_run_that_recorded_no_name_shapes_still_answers_without_them(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Where a person switched ``dictionary.record_name_shapes`` off, no word's place is known and
    no divided spelling is found; the rest of the queue is what it was."""
    from fixtures.words_pool import write_words_pool
    from ingence.core.project import Project
    from ingence.core.settings import DictionarySettings, Settings
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("words_no_shapes")
    project = Project.create(
        base / "project",
        created_at="t",
        roots=[write_words_pool(base)],
        settings=Settings(dictionary=DictionarySettings(record_name_shapes=False)),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    body = ask(project.path)
    assert body["rows"][0]["word"] == "vorn"
    assert "zibkelt" not in words_of(body)
    assert all(v["reason"] != "same_slot" for row in body["rows"] for v in row["variants"])


def test_the_word_itself_in_other_capitals_offers_no_pair_to_call_unrelated() -> None:
    from ingence.api.v1.words_queue import _variant_item
    from ingence.dictionary.variants import Variant

    same = Variant("Brisal", 3, 3, "case", "the same word in other capitals")
    assert _variant_item(same, "brisal") is None
    other = Variant("quolts", 3, 3, "plural", "a plural of it")
    item = _variant_item(other, "quolt")
    assert item is not None and item.distinct_target == "distinct:quolt+quolts"


def test_a_word_a_person_answered_stays_listed_once_a_run_understands_it(project: Path) -> None:
    """Understood because they said what it means: their answers are listed, and can be withdrawn,
    whether or not the engine is sure of the word now."""
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.record import log as record_log
    from ingence.scan.census import run_census

    say(
        project,
        "term:quolt",
        RecordEventKind.DEFINE,
        {"raised": "quolt", "answer": "quolt_assay", "theme_kind": "reagent"},
    )
    run_census(
        Project.open(project),
        run_id="run_20260101T000003Z_333333333333",
        started_at=wait_for_a_later_second(project),
        rebind=True,
    )
    run_inference(
        Project.open(project),
        run_id="run_20260101T000004Z_444444444444",
        started_at=record_log.now(),
    )
    rows = {row["word"]: row for row in ask(project, answered="only")["rows"]}
    assert rows["quolt"]["state"] == "understood"
    assert rows["quolt"]["waiting"] is False and rows["quolt"]["answered"]["withdrawable"] is True
    # Unanswered words the engine understands stay out of the default queue.
    assert "quolt" not in words_of(ask(project))
    assert "quolt" in words_of(ask(project, answered="show"))
