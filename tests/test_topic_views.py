"""Topics on the views, the engine's half.

A topic is a person's statement in the Record and a projection on the views, never a claim. Two
things the views need from the engine are held here, on the Graph workspace's invented fixture:

- Export files (CSV): ``GET /api/v1/topics/export`` sends one topic's files as CSV text (path, name,
  stage, stage state, experiment, experiment identifier) for the page to save as a download. Nothing
  is written inside the data pool or the project. A stage the rules did not conclude is a blank cell
  and "not found" (never a stage). A cell a spreadsheet would read as a formula is written as text.
  A topic that does not stand is refused in words.
- The ``topic:`` search filter: ``kelt topic:"a name"`` keeps the units that are, or lie inside, an
  experiment of the person's topic, read from the Record when the search runs. A topic can be named
  by its words (ignoring case) or by its identifier. ``-topic:`` excludes. A value that names no
  topic is said in ``refused`` and left out, never silently taken for "nothing".
"""

from __future__ import annotations

import csv
import io
import shutil
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.v1 import context_for, dispatch, load_routes
from ingence.api.v1.topic_export import (
    BYTE_ORDER_MARK,
    COLUMNS,
    csv_cell,
    csv_text,
    export_file_name,
    root_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject

SAID_AT = "2026-06-01T10:00:00Z"
STAGES = {"found", "conflicting", "not found"}


@pytest.fixture
def made(explorer_project: ExplorerProject, tmp_path: Path) -> Iterator[tuple[Path, Any]]:
    """A copy of the explorer project, and the routes' context over it."""
    load_routes()
    copy = tmp_path / "project"
    shutil.copytree(explorer_project.project, copy)
    yield copy, context_for(copy, settled=None, job_running=False)


def _say_topic(project: Path, name: str, members: list[dict[str, Any]]) -> str:
    """One topic written through the overlay's own path; its identifier."""
    from ingence.api.routes.overlay import prepare_overlay, write_prepared

    prepared = prepare_overlay(
        project,
        actor="a. person",
        occurred_at=SAID_AT,
        edits=[{"kind": "topic", "name": name, "members": members}],
    )
    written = write_prepared(project, prepared, actor="a. person", occurred_at=SAID_AT)
    return written[0].target.split(":")[2]


def _walk(root: Path) -> list[tuple[str, int]]:
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*"))


def _export(context: Any, topic: str) -> dict[str, Any]:
    status, body = dispatch(context, "topics/export", {"topic": [topic]})
    assert status == 200, body
    return body


def test_the_csv_names_every_file_of_the_topic_with_its_stage_and_experiment(
    explorer_project: ExplorerProject, made
) -> None:
    from ingence.storage.catalog import open_catalog

    project, context = made
    first, second, _third = explorer_project.experiments
    ids = explorer_project.ids
    topic = _say_topic(
        project,
        "Imaging series",
        [{"kind": "experiment", "id": ids[first]}, {"kind": "experiment", "id": ids[second]}],
    )
    served = _export(context, topic)
    # A spreadsheet reads the file's encoding from its first character.
    assert served["text"].startswith(BYTE_ORDER_MARK)
    rows = list(csv.DictReader(io.StringIO(served["text"].removeprefix(BYTE_ORDER_MARK))))
    assert list(rows[0]) == list(COLUMNS)
    assert served["files"] == len(rows) > 0
    assert served["experiments"] == 2
    assert served["complete"] is True
    assert served["topic_name"] == "Imaging series"
    assert served["file_name"] == "ingence-topic-imaging-series.csv"
    # The files are the topic's experiments' own, and each row says which experiment holds it.
    assert {row["experiment_id"] for row in rows} == {ids[first], ids[second]}
    assert {row["experiment"] for row in rows} == {first, second}
    assert all(row["path"].startswith((f"{first}/", f"{second}/")) for row in rows)
    assert [row["path"] for row in rows] == sorted(row["path"] for row in rows)
    # The same files the topics route counts, from the same rule.
    status, topics = dispatch(context, "topics", {"limit": ["0"], "suggestions": ["0"]})
    assert status == 200
    assert topics["topics"][0]["files_count"] == len(rows)
    # A stage is a value the rules concluded or it is not found; never a guess.
    assert {row["stage_state"] for row in rows} <= STAGES
    for row in rows:
        assert (row["stage"] != "") == (row["stage_state"] != "not found")
    assert served["with_stage"] == sum(1 for row in rows if row["stage_state"] != "not found")
    assert any(row["stage_state"] == "found" for row in rows)
    # What a person needs to read the file is said, the apostrophe's reason included.
    said = " ".join(served["notes"])
    assert "leading apostrophe" in said and "never runs it as a formula" in said
    assert "byte order mark" in said and "root column" in said
    # Each file says which root it is below, by name and identifier.
    with open_catalog(project) as catalog:
        roots = {
            str(root): (label, uri)
            for root, uri, label in catalog.query(
                "SELECT root_id, root_uri, label FROM roots"
            ).fetchall()
        }
    assert {row["root_id"] for row in rows} <= set(roots)
    for row in rows:
        label, uri = roots[row["root_id"]]
        assert row["root"] == root_name(label, str(uri)) != ""


def test_the_stage_states_of_the_csv_are_the_experiments_lists_own(
    explorer_project: ExplorerProject, made
) -> None:
    """One reading of a claim's kind (``claim_reading``) serves both: for each experiment of the
    topic, the files the CSV calls found or conflicting are the files the Experiments list
    counts as stage read or stage conflicting."""
    project, context = made
    ids = explorer_project.ids
    members = [{"kind": "experiment", "id": ids[name]} for name in explorer_project.experiments]
    topic = _say_topic(project, "All three", members)
    rows = list(
        csv.DictReader(io.StringIO(_export(context, topic)["text"].removeprefix(BYTE_ORDER_MARK)))
    )
    status, listed = dispatch(context, "experiments", {"limit": ["500"]})
    assert status == 200, listed
    by_experiment = {row["id"]: row["characterized"] for row in listed["rows"]}
    assert set(by_experiment) >= {ids[name] for name in explorer_project.experiments}
    for name in explorer_project.experiments:
        mine = [row for row in rows if row["experiment_id"] == ids[name]]
        counted = by_experiment[ids[name]]
        assert len(mine) == counted["files"] > 0
        assert sum(1 for row in mine if row["stage_state"] == "found") == counted["stage_read"]
        assert (
            sum(1 for row in mine if row["stage_state"] == "conflicting")
            == counted["stage_conflicting"]
        )


def test_exporting_writes_nothing_in_the_data_pool_or_the_project(
    explorer_project: ExplorerProject, made
) -> None:
    project, context = made
    first = explorer_project.experiments[0]
    topic = _say_topic(
        project, "Only one", [{"kind": "experiment", "id": explorer_project.ids[first]}]
    )
    before = (_walk(project), _walk(explorer_project.pool))
    _export(context, topic)
    assert (_walk(project), _walk(explorer_project.pool)) == before


def test_a_topic_that_does_not_stand_and_a_missing_name_are_refused_in_words(made) -> None:
    _project, context = made
    status, body = dispatch(context, "topics/export", {"topic": ["hgnosuchtopic"]})
    assert status == 404
    assert body["error"]["code"] == "not_found"
    assert "no topic hgnosuchtopic stands" in body["error"]["message"]
    status, body = dispatch(context, "topics/export", {})
    assert status == 400
    assert body["error"]["parameter"] == "topic"


def test_a_withdrawn_topic_cannot_be_exported(explorer_project: ExplorerProject, made) -> None:
    from ingence.api.routes.overlay import prepare_overlay, write_prepared

    project, context = made
    first = explorer_project.experiments[0]
    topic = _say_topic(
        project, "Short lived", [{"kind": "experiment", "id": explorer_project.ids[first]}]
    )
    prepared = prepare_overlay(
        project,
        actor="a. person",
        occurred_at="2026-06-01T11:00:00Z",
        edits=[{"kind": "topic", "id": topic, "remove": True}],
    )
    write_prepared(project, prepared, actor="a. person", occurred_at="2026-06-01T11:00:00Z")
    status, body = dispatch(context, "topics/export", {"topic": [topic]})
    assert status == 404, body


def test_a_topic_larger_than_the_limit_lists_the_first_files_and_says_how_many_are_left(
    explorer_project: ExplorerProject, made
) -> None:
    project, context = made
    first, second, _third = explorer_project.experiments
    ids = explorer_project.ids
    topic = _say_topic(
        project,
        "Both of them",
        [{"kind": "experiment", "id": ids[first]}, {"kind": "experiment", "id": ids[second]}],
    )
    whole = _export(context, topic)
    status, cut = dispatch(context, "topics/export", {"topic": [topic], "limit": ["3"]})
    assert status == 200, cut
    rows = list(csv.DictReader(io.StringIO(cut["text"].removeprefix(BYTE_ORDER_MARK))))
    assert len(rows) == cut["files"] == 3
    assert cut["truncated"] is True
    assert cut["files_not_listed"] == whole["files"] - 3 > 0
    assert cut["notes"][0].startswith(
        f"This file lists the first 3 of the topic's {whole['files']:,} files, in path order."
    )
    assert f"The other {cut['files_not_listed']:,} are not listed" in cut["notes"][0]
    # They are the same first files, in the same order.
    first_three = list(csv.DictReader(io.StringIO(whole["text"].removeprefix(BYTE_ORDER_MARK))))[:3]
    assert [row["path"] for row in rows] == [row["path"] for row in first_three]
    # A limit as large as the topic cuts nothing, and none below one is read.
    status, all_of_it = dispatch(
        context, "topics/export", {"topic": [topic], "limit": [str(whole["files"])]}
    )
    assert status == 200 and all_of_it["truncated"] is False
    status, refused = dispatch(context, "topics/export", {"topic": [topic], "limit": ["0"]})
    assert status == 400 and refused["error"]["parameter"] == "limit"


def test_a_root_is_named_by_its_label_else_by_the_last_part_of_its_address() -> None:
    assert root_name("Bench two", "file:///data/pool") == "Bench two"
    assert root_name(None, "/data/lab%20pool/") == "lab pool"
    assert root_name(None, "listing:archive:S-ABC1") == "S-ABC1"


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t"])
def test_a_cell_a_spreadsheet_would_read_as_a_formula_is_written_as_text(lead: str) -> None:
    assert csv_cell(f"{lead}cmd") == f"'{lead}cmd"
    assert csv_cell("plain/path.csv") == "plain/path.csv"
    row = dict.fromkeys(COLUMNS, "x")
    row["path"] = f"{lead}danger/file.csv"
    made_text = csv_text([row])
    assert made_text.startswith(BYTE_ORDER_MARK)
    parsed = list(csv.DictReader(io.StringIO(made_text.removeprefix(BYTE_ORDER_MARK))))
    assert parsed[0]["path"] == f"'{lead}danger/file.csv"


def test_the_download_is_named_by_the_topic_in_plain_letters() -> None:
    assert (
        export_file_name("Cell lines: HeLa & HEK293") == "ingence-topic-cell-lines-hela-hek293.csv"
    )
    assert export_file_name("???") == "ingence-topic-topic.csv"
    assert len(export_file_name("a" * 300)) < 80


def _results(context: Any, text: str) -> tuple[list[str], list[str]]:
    status, body = dispatch(context, "search", {"q": [text]})
    assert status == 200, body
    return [row["path"] for row in body["results"]], body["refused"]


def test_the_topic_filter_keeps_what_lies_inside_the_topics_experiments(
    explorer_project: ExplorerProject, made
) -> None:
    project, context = made
    first = explorer_project.experiments[0]
    stem = first.split("_")[1]
    topic = _say_topic(
        project, "Brisa Series", [{"kind": "experiment", "id": explorer_project.ids[first]}]
    )
    everywhere, refused = _results(context, stem)
    assert refused == []
    assert any(not path.startswith(first) for path in everywhere), (
        "the stem is found outside it too"
    )
    inside, refused = _results(context, f'{stem} topic:"brisa series"')
    assert refused == []
    assert inside
    assert all(path == first or path.startswith(f"{first}/") for path in inside)
    # Named by its identifier, and by its words in another case.
    assert _results(context, f"{stem} topic:{topic}")[0] == inside
    assert _results(context, f'{stem} topic:"BRISA SERIES"')[0] == inside
    # Excluded, it keeps the rest and none of the topic's.
    outside, _ = _results(context, f'{stem} -topic:"brisa series"')
    assert outside
    assert not any(path == first or path.startswith(f"{first}/") for path in outside)
    assert sorted(inside + outside) == sorted(everywhere)


def test_a_topic_value_that_names_no_topic_is_said_and_left_out(
    explorer_project: ExplorerProject, made
) -> None:
    _project, context = made
    stem = explorer_project.experiments[0].split("_")[1]
    everywhere, _ = _results(context, stem)
    found, refused = _results(context, f"{stem} topic:nothingofthatname")
    assert found == everywhere
    assert refused == ["topic:nothingofthatname: no collection of yours is called that"]


def test_a_topic_that_holds_no_experiment_says_so_where_nothing_matches(
    explorer_project: ExplorerProject, made
) -> None:
    project, context = made
    stem = explorer_project.experiments[0].split("_")[1]
    # A folder no experiment rule concluded on: the topic stands, and comes to no experiment.
    _say_topic(project, "Notes only", [{"kind": "experiment", "id": explorer_project.ids["notes"]}])
    status, body = dispatch(context, "search", {"q": [f'{stem} topic:"notes only"']})
    assert status == 200, body
    assert body["results"] == []
    assert body["refused"] == []
    assert any(
        "The collection notes only holds no experiment in this scan, so no result is inside it."
        in warning
        for warning in body["warnings"]
    ), body["warnings"]


def test_the_edits_the_pages_send_add_and_take_out_one_member_and_keep_every_other(
    explorer_project: ExplorerProject, made
) -> None:
    """The shapes the Add to topic chooser, the Topics page and the Graph workspace send: an edit
    of a topic that stands names only what it adds (``add``) or takes out (``remove_members``, by
    the key the route serves), and a rename names no member; the groups of words, the lines of work
    and the marks of experiments it holds are carried over as they were."""
    from ingence.api.routes.overlay import (
        OverlayRefusedError,
        line_key,
        prepare_overlay,
        topics_standing,
        write_prepared,
    )

    project, _context = made
    first, second, third = (explorer_project.ids[name] for name in explorer_project.experiments)
    topic = _say_topic(
        project,
        "A mix of kinds",
        [
            {"kind": "experiment", "id": first},
            {"kind": "core", "terms": ["dapi", "gfp"]},
            {
                "kind": "line",
                "experiments": [first, second],
                "words": ["dapi", "gfp"],
                "label": "x",
            },
        ],
    )

    def say(edit: dict[str, Any], at: str) -> None:
        prepared = prepare_overlay(
            project,
            actor="a. person",
            occurred_at=at,
            edits=[{"kind": "topic", "id": topic, **edit}],
        )
        write_prepared(project, prepared, actor="a. person", occurred_at=at)

    def held() -> dict[str, str]:
        (standing,) = [t for t in topics_standing(project) if t.topic_id == topic]
        return {member.key: member.kind for member in standing.members}

    whole = {
        first: "experiment",
        "group:dapi gfp": "core",
        line_key([first, second]): "line",
    }
    assert held() == whole
    # A rename names no member, and every kind of member stays.
    say({"name": "Renamed"}, "2026-06-01T11:00:00Z")
    assert held() == whole
    # An add names only what is added.
    say({"add": [{"kind": "experiment", "id": third}]}, "2026-06-01T12:00:00Z")
    assert held() == {**whole, third: "experiment"}
    # An add of what is held changes nothing.
    say({"add": [{"kind": "core", "terms": ["gfp", "DAPI"]}]}, "2026-06-01T13:00:00Z")
    assert held() == {**whole, third: "experiment"}
    # A member is taken out by the key the route serves, the others stay as they were.
    say(
        {"remove_members": ["group:dapi gfp", line_key([first, second])]},
        "2026-06-01T14:00:00Z",
    )
    assert held() == {first: "experiment", third: "experiment"}
    # The last member cannot be taken out: the topic is withdrawn instead.
    say({"remove_members": [first]}, "2026-06-01T15:00:00Z")
    with pytest.raises(OverlayRefusedError, match="would hold no member"):
        say({"remove_members": [third]}, "2026-06-01T16:00:00Z")


def test_the_line_key_the_page_computes_is_the_one_the_server_serves() -> None:
    """The Add to topic chooser knows a line of work already in a topic by computing the key the
    server serves it under (``topic_kit.js`` ``lineKey``); this holds the server's end, and
    ``tests/web_js/topic_line_key.test.mjs`` the page's, to one value."""
    from ingence.api.routes.overlay import line_key

    assert line_key(["entry_a", "entry_b"]) == "line:456065eaa676"
    assert line_key(["entry_a"]) == "line:feca9834a05a"


def test_a_topic_filter_is_read_from_the_record_each_time_it_is_asked(
    explorer_project: ExplorerProject, made
) -> None:
    project, context = made
    first, second, _third = explorer_project.experiments
    stem = second.split("_")[1]
    _say_topic(project, "Moving", [{"kind": "experiment", "id": explorer_project.ids[first]}])
    held, _ = _results(context, f'{stem} topic:"moving"')
    assert not any(path.startswith(second) for path in held)
    # The topic is edited to hold the second experiment instead: the next search reads that.
    from ingence.api.routes.overlay import prepare_overlay, topics_standing, write_prepared

    topic_id = topics_standing(project)[0].topic_id
    prepared = prepare_overlay(
        project,
        actor="a. person",
        occurred_at="2026-06-01T12:00:00Z",
        edits=[
            {
                "kind": "topic",
                "id": topic_id,
                "members": [{"kind": "experiment", "id": explorer_project.ids[second]}],
            }
        ],
    )
    write_prepared(project, prepared, actor="a. person", occurred_at="2026-06-01T12:00:00Z")
    moved, _ = _results(context, f'{stem} topic:"moving"')
    assert moved
    assert all(path == second or path.startswith(f"{second}/") for path in moved)


def test_the_topic_filter_is_one_of_the_filters_the_query_reads() -> None:
    from ingence.search.query import FILTER_NAMES

    assert "topic" in FILTER_NAMES
