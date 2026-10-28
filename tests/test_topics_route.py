"""``GET /api/v1/topics``: what a person's topics come to in the run.

The test uses the themes' invented pool, indexed and inferred by the engine
(``fixtures.topic_project``). The expected experiments are written from the plant (which invented
words each experiment's names carry), never read back from the code under test. Held here:

- a topic over two cores and a line resolves to the union of their experiments, and its files are
  the experiments' files by the one rule themes and completeness count by;
- a member that resolves to nothing is listed with why; a member that could not be read is named as
  unread and its count is none, never 0;
- a re-run where an experiment comes to carry a core's words: the topic gains it and the drift says
  why; the compared run can be chosen or turned off;
- overlap between two topics is counted; the lists are paged; the files of one topic are listed
  behind a parameter;
- the broad lines of work are offered as places to start and nothing is made by itself;
- the response is its declared model, and a disabled component is said in the one error shape.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes.overlay import prepare_overlay, write_prepared
from ingence.api.v1 import context_for, dispatch
from ingence.api.v1.models import RESPONSE_MODELS
from ingence.record.log import read_events

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.topic_project import TopicProject

pytest.importorskip("sklearn", reason="the fixture project draws themes")

SAID_AT = "2026-06-01T10:00:00Z"
ACTOR = "a. person"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> TopicProject:
    from fixtures.topic_project import build_topic_project

    return build_topic_project(tmp_path_factory.mktemp("topics_route"))


@pytest.fixture
def project(built: TopicProject, tmp_path: Path) -> Path:
    copy = tmp_path / "project"
    shutil.copytree(built.project, copy)
    return copy


def _write(project: Path, *edits: dict[str, Any]) -> list[Any]:
    prepared = prepare_overlay(project, actor=ACTOR, occurred_at=SAID_AT, edits=list(edits))
    return write_prepared(project, prepared, actor=ACTOR, occurred_at=SAID_AT)


def _get(project: Path, path: str = "topics", **query: str) -> dict[str, Any]:
    context = context_for(project, settled=None, job_running=False)
    status, body = dispatch(context, path, {key: [value] for key, value in query.items()})
    assert status == 200, body
    return body  # type: ignore[no-any-return]


def _carrying(built: TopicProject, *words: str) -> set[str]:
    """The experiments whose names carry every word, from the plant: the expected answer."""
    wanted = set(words)
    return {
        built.ids[unit]
        for unit, written in built.plant.documents.items()
        if unit in built.ids and wanted <= set(written)
    }


def _themes_files(project: Path) -> dict[str, int]:
    body = _get(project, "themes")
    return {row["id"]: row["files"] for row in body["experiments"]}


def _topic(body: dict[str, Any], name: str) -> dict[str, Any]:
    return next(topic for topic in body["topics"] if topic["name"] == name)


def test_a_topic_over_two_cores_and_a_line_is_the_union_with_files_by_the_shared_rule(
    built: TopicProject, project: Path
) -> None:
    themes = _get(project, "themes")
    first, second = _carrying(built, "droska", "miravo"), _carrying(built, "nuntle", "gavish")
    assert first and second and first.isdisjoint(second)
    other_line = next(
        t for t in themes["themes"] if "hoben" in t["label"] or "cadrom" in t["label"]
    )
    others = sorted(r["id"] for r in themes["experiments"] if r["theme"] == other_line["theme"])
    _write(
        project,
        {
            "kind": "topic",
            "name": "Stains and probes",
            "members": [
                {"kind": "core", "terms": ["droska", "miravo"]},
                {"kind": "core", "terms": ["nuntle", "gavish"]},
                {
                    "kind": "line",
                    "experiments": others[:2],
                    "words": other_line["label"].split(" \u00b7 "),
                },
            ],
        },
    )
    body = _get(project, suggestions="0")
    [topic] = body["topics"]
    expected = first | second | set(others)
    assert set(topic["experiment_ids"]) == expected
    assert topic["experiments_count"] == len(expected)
    assert topic["experiments_out_of"] == body["run_experiments"] == 66
    # Files by the shared rule: the same count the themes projection makes for each experiment.
    counted = _themes_files(project)
    assert topic["files_count"] == sum(counted[e] for e in expected) > 0
    by_key = {m["key"]: m for m in topic["members"]}
    assert set(by_key["group:droska miravo"]["experiment_ids"]) == first
    assert by_key["group:droska miravo"]["experiments"] == len(first)
    [line_member] = [m for m in topic["members"] if m["kind"] == "line"]
    assert set(line_member["experiment_ids"]) == set(others)
    assert line_member["line"]["kept"] == 2 and line_member["line"]["added_by"] == 2
    assert line_member["line"]["drifted"] is False
    assert topic["nothing"] == [] and topic["not_computed"] == [] and topic["complete"] is True
    assert topic["said"] == (f"{len(expected)} of 66 experiments, {topic['files_count']} files.")


def test_a_folder_above_experiments_resolves_to_those_beneath_and_a_file_to_its_experiment(
    built: TopicProject, project: Path
) -> None:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        person = str(
            catalog.query("SELECT entry_id FROM entries WHERE relative_path = 'kipper'").fetchone()[
                0
            ]
        )
        file_row = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path LIKE ? AND entry_type = 'file'",
            [f"{built.folders['t5_00']}/%"],
        ).fetchone()
    _write(
        project,
        {
            "kind": "topic",
            "name": "Mixed ways in",
            "members": [person, str(file_row[0])],
        },
    )
    body = _get(project, suggestions="0")
    topic = _topic(body, "Mixed ways in")
    folder_member = next(m for m in topic["members"] if m["key"] == person)
    beneath = {
        built.ids[unit]
        for unit, folder in built.folders.items()
        if unit in built.ids and folder.startswith("kipper/")
    }
    assert set(folder_member["experiment_ids"]) == beneath
    file_member = next(m for m in topic["members"] if m["key"] == str(file_row[0]))
    assert file_member["experiment_ids"] == [built.ids["t5_00"]]
    assert set(topic["experiment_ids"]) == beneath | {built.ids["t5_00"]}


def test_a_member_resolving_to_nothing_is_listed_and_an_unread_one_is_not_nothing(
    built: TopicProject, project: Path
) -> None:
    from ingence.themes import THEME_ENGINES

    person = built.folders["t0_00"].split("/")[0]
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        person_id = str(
            catalog.query(
                "SELECT entry_id FROM entries WHERE relative_path = ?", [person]
            ).fetchone()[0]
        )
    _write(
        project,
        {
            "kind": "topic",
            "name": "Some of it",
            "members": [
                {"kind": "core", "terms": ["nosuchword", "norother"]},
                {"kind": "experiment", "id": person_id},
                {"kind": "experiment", "id": built.ids["t0_00"]},
                {"kind": "line", "experiments": [built.ids["t1_00"]], "words": ["quolt"]},
            ],
        },
    )
    body = _get(project, suggestions="0")
    topic = _topic(body, "Some of it")
    by_kind = {(m["kind"], m["state"]): m for m in topic["members"]}
    nothing = [m for m in topic["members"] if m["state"] == "nothing"]
    assert {m["key"] for m in nothing} == {"group:norother nosuchword", person_id}
    assert all(m["why"] and m["experiments"] == 0 for m in nothing)
    assert "carries all of its words" in by_kind[("core", "nothing")]["why"]
    assert "does not count the folder as an experiment" in by_kind[("experiment", "nothing")]["why"]
    assert set(topic["nothing"]) == {m["key"] for m in nothing}
    assert topic["experiment_ids"] != []
    # With the themes turned off the line cannot be read: it is unread, its count is none.
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        unread = _topic(_get(project, suggestions="0"), "Some of it")
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    [line_member] = [m for m in unread["members"] if m["kind"] == "line"]
    assert line_member["state"] == "not_computed" and line_member["experiments"] is None
    assert line_member["why"]
    assert unread["not_computed"] == [line_member["key"]] and unread["complete"] is False
    assert line_member["key"] not in unread["nothing"]
    assert "could not be read" in unread["said"]


def test_a_re_run_where_an_experiment_comes_to_carry_a_cores_words_gains_it_and_says_why(
    tmp_path: Path,
) -> None:
    from fixtures.topic_project import build_topic_project, rerun

    built = build_topic_project(tmp_path)
    core = {"kind": "core", "terms": ["droska", "miravo"]}
    _write(built.project, {"kind": "topic", "name": "Stains", "members": [core]})
    before = _get(built.project, suggestions="0")
    before_topic = _topic(before, "Stains")
    assert set(before_topic["experiment_ids"]) == _carrying(built, "droska", "miravo")
    # The first run read: there is no earlier inference, so drift is read against its census.
    stray = built.ids["t3_00"]
    assert stray not in before_topic["experiment_ids"]
    # A new acquisition in a t3 experiment carries both words; nothing else changes.
    again = rerun(built, {"t3_00": ["droska_miravo_extra_01.nd2"]})
    after = _get(built.project, suggestions="0")
    assert after["run_id"] == again
    topic = _topic(after, "Stains")
    assert stray in topic["experiment_ids"]
    assert topic["experiments_count"] == before_topic["experiments_count"] + 1
    drift = topic["drift"]
    assert drift["since_run_id"] == built.infer == after["compared_run_id"]
    assert [(c["experiment"], c["reasons"]) for c in drift["joined"]] == [
        (stray, ["now carries the words droska, miravo"])
    ]
    assert drift["left"] == [] and drift["unread"] == []
    [change] = drift["joined"]
    assert change["folder"] == built.folders["t3_00"]
    assert drift["said"] == [
        "1 experiment joined “Stains”: it now carries the words droska, miravo."
    ]
    # The compared run can be chosen, and turned off.
    chosen = _get(built.project, suggestions="0", compare=built.infer)
    assert _topic(chosen, "Stains")["drift"]["since_run_id"] == built.infer
    # A census applied no rules, so it can be chosen and holds nothing to compare.
    census = _get(built.project, suggestions="0", compare=built.census)
    assert _topic(census, "Stains")["drift"] is None and "applied no rules" in census["drift_why"]
    off = _get(built.project, suggestions="0", compare="none")
    assert _topic(off, "Stains")["drift"] is None and off["compared_run_id"] is None
    context = context_for(built.project, settled=None, job_running=False)
    status, refused = dispatch(context, "topics", {"compare": ["run_20200101T000000Z_abc"]})
    assert status == 404 and refused["error"]["code"] == "unknown_run"


def test_overlap_between_two_topics_is_counted_and_the_lists_are_paged(
    built: TopicProject, project: Path
) -> None:
    wide = _carrying(built, "droska", "miravo") | _carrying(built, "keltan")
    narrow = _carrying(built, "droska", "miravo") | _carrying(built, "nuntle")
    shared = wide & narrow
    assert shared
    _write(
        project,
        {
            "kind": "topic",
            "name": "Wide",
            "members": [
                {"kind": "core", "terms": ["droska", "miravo"]},
                {"kind": "core", "terms": ["keltan"]},
            ],
        },
        {
            "kind": "topic",
            "name": "Narrow",
            "members": [
                {"kind": "core", "terms": ["droska", "miravo"]},
                {"kind": "core", "terms": ["nuntle"]},
            ],
        },
    )
    body = _get(project, suggestions="0", limit="2", offset="1")
    first, second = _topic(body, "Wide"), _topic(body, "Narrow")
    [overlap] = first["overlaps"]
    assert overlap["topic"] == second["id"] and overlap["experiments"] == len(shared)
    assert set(overlap["experiment_ids"]) == shared
    assert first["shared_experiments"] == len(shared)
    assert second["overlaps"][0]["topic"] == first["id"]
    # Paged: two experiments from the second on, and the rest counted; the ids stay whole.
    assert len(first["experiments"]) == 2
    assert first["experiments_not_shown"] == len(wide) - 3
    assert len(first["experiment_ids"]) == len(wide)
    only = _get(project, suggestions="0", topic=first["id"], limit="0")
    assert [t["id"] for t in only["topics"]] == [first["id"]]
    assert only["topics"][0]["experiments"] == []
    # The files of one topic are behind a parameter, a page at a time.
    counted = _themes_files(project)
    paged = _get(project, suggestions="0", topic=first["id"], files="1", limit="7")
    assert paged["files_of"] == first["id"] and len(paged["files"]) == 7
    assert paged["files_not_shown"] == sum(counted[e] for e in wide) - 7
    assert {row["experiment"] for row in paged["files"]} <= wide
    context = context_for(project, settled=None, job_running=False)
    assert dispatch(context, "topics", {"files": ["1"]})[0] == 400
    assert dispatch(context, "topics", {"topic": ["hgnobody"]})[0] == 404


def test_the_broad_lines_are_offered_and_nothing_is_made_by_itself(
    built: TopicProject, project: Path
) -> None:
    before = len(read_events(project, held=False))
    body = _get(project)
    assert body["topics"] == []
    assert body["suggestions"] and body["suggestions_why"] is None
    first = body["suggestions"][0]
    assert first["experiments"] == len(first["experiment_ids"]) > 0
    assert first["in_topics"] == 0
    assert len(read_events(project, held=False)) == before
    # Once a topic holds some of them the suggestion says how many.
    _write(
        project,
        {
            "kind": "topic",
            "name": "Started",
            "members": [{"kind": "experiment", "id": first["experiment_ids"][0]}],
        },
    )
    again = _get(project)
    assert next(s for s in again["suggestions"] if s["broad"] == first["broad"])["in_topics"] == 1
    assert _get(project, suggestions="0")["suggestions"] == []


def test_the_response_is_its_declared_model_and_a_disabled_component_is_said(
    built: TopicProject, project: Path
) -> None:
    from ingence.topics import TOPIC_ENGINES

    _write(
        project,
        {
            "kind": "topic",
            "name": "One",
            "members": [{"kind": "experiment", "id": built.ids["t0_00"]}],
        },
    )
    body = _get(project)
    RESPONSE_MODELS["topics"].model_validate(body)
    TOPIC_ENGINES.set_enabled("record_projection", enabled=False)
    try:
        context = context_for(project, settled=None, job_running=False)
        status, refused = dispatch(context, "topics", {})
    finally:
        TOPIC_ENGINES.set_enabled("record_projection", enabled=True)
    assert status == 503 and refused["error"]["code"] == "component_unavailable"


def test_a_census_applied_no_rules_so_what_it_holds_is_unknown_not_nothing(
    built: TopicProject, project: Path
) -> None:
    _write(
        project,
        {
            "kind": "topic",
            "name": "Over a census",
            "members": [{"kind": "experiment", "id": built.ids["t0_00"]}],
        },
    )
    body = _get(project, suggestions="0", run=built.census)
    census = _topic(body, "Over a census")
    # Unknown is not zero at the topic level either: a census holds no experiments yet.
    assert body["run_experiments"] is None
    assert census["experiments_count"] is None and census["files_count"] is None
    assert census["experiments_out_of"] is None
    assert census["said"] == "Not read: this run applied no rules, so it holds no experiments yet."
    [member] = census["members"]
    assert member["state"] == "not_computed" and member["experiments"] is None
    assert "applied no rules" in member["why"]
    assert census["nothing"] == [] and census["complete"] is False
    # Drift against a census says it cannot be read, and is none, never "everything joined".
    body = _get(project, suggestions="0", compare=built.census)
    assert _topic(body, "Over a census")["drift"] is None
    assert body["compared_run_id"] is None and "applied no rules" in body["drift_why"]


def test_a_line_whose_label_changes_keeps_its_experiments_in_the_topic(
    built: TopicProject, project: Path
) -> None:
    """The engine reads fewer words for every line (a setting), so each line has another label
    and another key; a line added by its experiments is the same line and says its words drifted."""
    from ingence.core.project import Project

    themes = _get(project, "themes")
    line = next(t for t in themes["themes"] if "hoben" in t["label"].split(" · "))
    held = sorted(r["id"] for r in themes["experiments"] if r["theme"] == line["theme"])
    _write(
        project,
        {
            "kind": "topic",
            "name": "A line",
            "members": [
                {"kind": "line", "experiments": held[:3], "words": line["label"].split(" · ")}
            ],
        },
    )
    before = _topic(_get(project, suggestions="0"), "A line")["members"][0]
    assert before["line"]["drifted"] is False and set(before["experiment_ids"]) == set(held)
    opened = Project.open(project)
    opened.save_settings(
        opened.settings.model_copy(
            update={"themes": opened.settings.themes.model_copy(update={"label_words": 2})}
        )
    )
    relabeled = next(t for t in _get(project, "themes")["themes"] if t["theme"] == line["theme"])
    assert relabeled["target"] != line["target"], "the key must have changed"
    after = _topic(_get(project, suggestions="0"), "A line")["members"][0]
    assert after["state"] == "resolved"
    assert set(after["experiment_ids"]) == set(held)
    assert after["line"]["drifted"] is True
    assert len(after["line"]["words"]) == 2 and len(after["line"]["words_added_with"]) == 3
    assert (after["line"]["kept"], after["line"]["added_by"]) == (3, 3)


def test_a_topic_counts_an_experiments_files_as_the_experiments_list_does_a_link_included(
    tmp_path: Path,
) -> None:
    """The Experiments list and completeness count every live entry that is not a directory, so a
    link in an experiment is one of its files; a topic's count is the sum of those rows."""
    import os

    from fixtures.topic_project import build_topic_project, rerun
    from ingence.storage.catalog import open_catalog

    built = build_topic_project(tmp_path)
    experiment = built.ids["t3_00"]
    _write(
        built.project,
        {
            "kind": "topic",
            "name": "With a link",
            "members": [{"kind": "experiment", "id": experiment}],
        },
    )
    before = _topic(_get(built.project, suggestions="0"), "With a link")["files_count"]
    folder = built.pool / built.folders["t3_00"]
    target = next(path for path in sorted(folder.iterdir()) if path.name.endswith("_results.csv"))
    os.symlink(target.name, folder / "results_link.csv")
    rerun(built, {})
    with open_catalog(built.project) as catalog:
        kinds = dict(
            catalog.query(
                "SELECT name, entry_type FROM entries WHERE name = 'results_link.csv'"
            ).fetchall()
        )
    assert kinds and kinds["results_link.csv"] != "file", kinds
    body = _get(built.project, suggestions="0")
    topic = _topic(body, "With a link")
    assert topic["files_count"] == before + 1
    # The Experiments list says the same of the same experiment.
    context = context_for(built.project, settled=None, job_running=False)
    status, listed = dispatch(context, "experiments/files", {"experiment": [experiment]})
    assert status == 200, listed
    assert listed["files"] == topic["files_count"]
    assert topic["experiments"][0]["files"] == listed["files"]
    # And the topic's own page of files lists the link among them.
    page = _get(built.project, suggestions="0", topic=topic["id"], files="1", limit="50")
    assert "results_link.csv" in {row["name"] for row in page["files"]}
    assert len(page["files"]) + page["files_not_shown"] == topic["files_count"]
