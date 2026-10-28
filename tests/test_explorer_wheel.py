"""The Graph workspace's Wheel: a folder's shape, counted exactly, never materialised.

On the explorer fixture (``fixtures/explorer_project.py``, invented names): three dated experiments
with raw, processed and analysis folders, notes, an archive, a dense scratch folder with one file
removed before a second census, a deep nest, and an archive link; published derivations that fork
and join, two mentions, a withdrawn edge, proposed reference pairs, and one experiment confirmed by
a person in the Record.
"""

from __future__ import annotations

import shutil
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
    return dispatch(context, path, {name: [str(value)] for name, value in query.items()})


def _wheel(project: Path, **query: Any) -> dict[str, Any]:
    status, body = _ask(project, "explorer/wheel", **query)
    assert status == HTTPStatus.OK, body
    return body


def _live_files(explorer: ExplorerProject, under: str = "") -> set[str]:
    from ingence.storage.catalog import open_catalog

    with open_catalog(explorer.project) as catalog:
        rows = catalog.query(
            "SELECT relative_path FROM entries WHERE NOT is_deleted AND entry_type <> 'directory' "
            "AND entry_kind = 'filesystem'"
        ).fetchall()
    paths = {str(row[0]) for row in rows}
    return {path for path in paths if not under or path.startswith(f"{under}/")}


def test_every_live_file_is_counted_once_at_the_perimeter(explorer) -> None:
    body = _wheel(explorer.project)
    files = _live_files(explorer)
    assert body["totals"]["files"] == len(files)
    assert sum(body["bins"]["n"]) == len(files)
    assert body["sectors"][0]["id"] == body["focus"]["id"]
    assert body["sectors"][0]["ring"] == 0
    # Every category count of every measure adds up to the sector's files.
    for sector in body["sectors"]:
        for measure, counts in sector["counts"].items():
            assert sum(counts) == sector["files"], (sector["name"], measure)


def test_a_file_removed_before_the_last_census_is_never_counted(explorer) -> None:
    from fixtures.explorer_project import GONE

    body = _wheel(explorer.project)
    assert GONE not in _live_files(explorer)
    scratch = next(sector for sector in body["sectors"] if sector["name"] == "scratch")
    assert scratch["files"] == len(_live_files(explorer, "scratch"))
    status, found = _ask(explorer.project, "explorer/files", folder=scratch["id"], limit=500)
    assert status == HTTPStatus.OK
    assert GONE not in {row["path"] for row in found["files"]}


def test_the_measures_state_their_question_denominator_and_unknown(explorer) -> None:
    from ingence.api.v1.explorer.categories import MEASURES

    body = _wheel(explorer.project)
    assert [measure["key"] for measure in body["measures"]] == [m.key for m in MEASURES]
    for measure in body["measures"]:
        assert measure["question"].endswith("?")
        assert "files beneath" in measure["denominator"]
        assert measure["unknown"] in {category["key"] for category in measure["categories"]}
    # A stage is never called analysed: the analysis category says it is a reading.
    stage = next(measure for measure in body["measures"] if measure["key"] == "stage")
    analysis = next(category for category in stage["categories"] if category["key"] == "analysis")
    assert "not evidence" in analysis["meaning"]


def test_the_projection_writes_only_the_categories_the_measures_declare(explorer) -> None:
    import duckdb

    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer.categories import MEASURES
    from ingence.api.v1.explorer.projection import read_projection

    _wheel(explorer.project)
    projection = read_projection(explorer.project, PUBLISHED_RUN)
    assert projection is not None
    connection = duckdb.connect()
    for measure in MEASURES:
        written = {
            row[0]
            for row in connection.execute(
                f"SELECT DISTINCT {measure.key} FROM {projection.files}"
            ).fetchall()
        }
        assert written <= set(measure.category_keys()), (measure.key, written)


def _format_predicate(explorer: ExplorerProject, path: str) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(explorer.project) as catalog:
        found = catalog.query(
            "SELECT min(predicate) FROM claims WHERE subject = ? "
            "AND predicate LIKE 'classifies.format%'",
            [explorer.ids[path]],
        ).fetchone()
    assert found is not None and found[0], path
    return str(found[0])


def test_a_format_read_in_conflict_is_its_own_kind_never_the_settled_one(
    explorer, tmp_path
) -> None:
    """A format read in conflict is its own kind, never the settled one. Two cases of the class:
    a file's format claim published as conflicting (its defeater fired), and a second format
    claim reading another family. Each is the Kind measure's conflicting category, at the problem
    level, and its card says the reading is in conflict and names every format read."""
    from fixtures.explorer_project import with_published
    from ingence.api.v1.explorer.categories import kind_families

    mask = next(path for path in explorer.ids if path.endswith("_01_mask.tif"))
    quant = next(path for path in explorer.ids if path.endswith("_01_quant.csv"))
    settled = next(path for path in explorer.ids if path.endswith("_02_mask.tif"))
    predicate = _format_predicate(explorer, mask)
    # The image whose contents were read has a format from its contents, the table not, so
    # each file's own format claim is copied.
    quant_predicate = _format_predicate(explorer, quant)
    text_format = kind_families()["text"][0]
    project = with_published(
        explorer,
        tmp_path,
        claims=((mask, predicate, None, True), (quant, quant_predicate, text_format, False)),
    )
    body = _wheel(project)
    kind = next(measure for measure in body["measures"] if measure["key"] == "kind")
    conflicting = next(item for item in kind["categories"] if item["key"] == "conflicting")
    assert conflicting["level"] == "problem"
    keys = [item["key"] for item in kind["categories"]]
    totals = dict(zip(keys, body["totals"]["counts"]["kind"], strict=True))
    assert totals["conflicting"] == 2
    for path, read in ((mask, 1), (quant, 2)):
        status, card = _ask(project, "explorer/entry", id=explorer.ids[path])
        assert status == HTTPStatus.OK, card
        assert card["kind"] == "conflicting", path
        assert card["categories"]["kind"] == "conflicting", path
        assert card["format_conflicting"] is True, path
        assert len(card["formats"]) == read, (path, card["formats"])
    status, card = _ask(project, "explorer/entry", id=explorer.ids[settled])
    assert status == HTTPStatus.OK, card
    assert card["kind"] == "image"
    assert card["format_conflicting"] is False
    assert card["formats"] == [card["format"]]


def test_stage_and_format_are_read_by_the_value_a_rule_concludes_never_its_name(
    explorer, tmp_path
) -> None:
    """Stage and format claims are read by value, not by their rule's name.

    A person's stage rule named ``processed_from_folder`` is found. A rule whose name starts with
    ``format`` counts as a format only when it concludes a format sense. A stage or a format
    sense of the run's Dictionary counts, whatever the rule is called."""
    from fixtures.explorer_project import AddedClaim, with_published

    table = next(path for path in explorer.ids if path.endswith("_01_quant.csv"))
    note = next(path for path in explorer.ids if path.startswith("notes/"))
    predicate = _format_predicate(explorer, table)
    project = with_published(
        explorer,
        tmp_path,
        claims=(
            AddedClaim(table, predicate, value="plain words", rule="format_note_from_name"),
            AddedClaim(note, predicate, value="processed", rule="processed_from_folder"),
        ),
    )
    status, card = _ask(project, "explorer/entry", id=explorer.ids[table])
    assert status == HTTPStatus.OK, card
    assert card["kind"] == "table"
    assert card["format_conflicting"] is False
    assert card["formats"] == ["tabular_format"]
    status, card = _ask(project, "explorer/entry", id=explorer.ids[note])
    assert status == HTTPStatus.OK, card
    assert card["categories"]["stage"] == "processing"
    assert "processed" not in card["formats"]


def test_content_read_by_the_dive_and_an_archive_link_are_told_apart(explorer) -> None:
    body = _wheel(explorer.project)
    exam = next(measure for measure in body["measures"] if measure["key"] == "exam")
    keys = [category["key"] for category in exam["categories"]]
    totals = dict(zip(keys, body["totals"]["counts"]["exam"], strict=True))
    assert totals["read"] > 0
    if explorer.link_made:
        assert totals["elsewhere"] == 1
        link = explorer.ids["links/far_scan.tif"]
        status, card = _ask(explorer.project, "explorer/entry", id=link)
        assert status == HTTPStatus.OK
        assert card["is_archive_link"] is True
        assert card["categories"]["exam"] == "elsewhere"


def test_published_derivations_mentions_and_proposals_are_counted_apart(explorer) -> None:
    body = _wheel(explorer.project)
    totals = body["totals"]
    # The withdrawn edge is not published.
    assert totals["published_derivations"] == len(explorer.derivations)
    # The unresolved mention is published too: a mention whose target is no entry.
    assert totals["published_mentions"] == len(explorer.mentions) + 1
    assert totals["proposed_pairs"] > 0
    assert "published" in totals["provenance"]

    def category(path: str) -> str:
        status, card = _ask(explorer.project, "explorer/entry", id=explorer.ids[path])
        assert status == HTTPStatus.OK
        return str(card["categories"]["prov"])

    derived = {path for pair in explorer.derivations for path in pair}
    for path in derived:
        assert category(path) == "derivation", path
    script = explorer.mentions[0][0]
    # The script only mentions files: a mention is never "made from".
    assert script not in derived
    assert category(script) == "reference"


def test_experiment_units_supported_confirmed_and_decided_now(explorer, tmp_path) -> None:
    from fixtures.explorer_project import PERSON, PUBLISHED_RUN
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    body = _wheel(explorer.project)
    experiment = body["totals"]["experiment"]
    assert experiment["units"] == len(explorer.experiments)
    assert experiment["confirmed"] == 1
    folder = explorer.ids[explorer.confirmed_folder]
    confirmed = next(s for s in body["sectors"] if s["id"] == folder)
    assert confirmed["experiment"]["status"] == "confirmed"
    assert confirmed["experiment"]["decided_by"] == PERSON
    other = next(s for s in body["sectors"] if s["id"] == explorer.ids[explorer.experiments[1]])
    assert other["experiment"]["status"] == "supported"
    assert other["experiment"]["decided_by"] is None

    # A person's rejection shows on the next request, with no new run: on a copy of the project.
    copy = tmp_path / "copy"
    shutil.copytree(explorer.project, copy)
    claim = other["experiment"]["claim_id"]
    append_event(
        copy,
        kind=RecordEventKind.REJECT,
        actor=PERSON,
        occurred_at="2026-01-03T10:00:00Z",
        target=f"claim:{claim}",
        against_run_id=PUBLISHED_RUN,
    )
    after = _wheel(copy)
    rejected = next(s for s in after["sectors"] if s["id"] == other["id"])
    assert rejected["experiment"]["status"] == "rejected"
    assert after["totals"]["experiment"]["rejected"] == 1
    assert after["run_id"] == body["run_id"]


def test_folders_beyond_the_caps_are_gathered_with_exact_sums(explorer) -> None:
    body = _wheel(explorer.project, rings=2, max_sectors=8)
    assert len(body["sectors"]) <= 8  # a "more" sector takes one of the eight
    more = [sector for sector in body["sectors"] if sector["kind"] == "more"]
    assert more, "the caps gathered nothing"
    caps = body["caps"]
    assert caps["folders_gathered"] >= sum(sector["gathered"] for sector in more)
    assert caps["folders_in_rings"] == caps["folders_gathered"] + sum(
        1 for sector in body["sectors"][1:] if sector["kind"] == "folder"
    )
    # Every file is still counted, drawn alone or gathered.
    assert sum(body["bins"]["n"]) == body["totals"]["files"]
    ring_one = [s for s in body["sectors"] if s["ring"] == 1]
    direct = body["sectors"][0]["direct_files"]
    assert sum(s["files"] for s in ring_one) + direct == body["totals"]["files"]


def test_the_sector_cap_is_a_cap_and_the_bins_keep_their_stated_bound(explorer) -> None:
    """A parent with gathered children adds no "more" sector once room has run out, and the room
    counts a ring's sectors once. Every sector holding a file takes a bin whatever the cap.
    So the sectors never pass ``max_sectors``, and the bins never pass ``max_bins`` plus one for
    each sector holding a file, the bound the response states. Every file is still counted
    once."""
    whole = _wheel(explorer.project, rings=8, min_angle=0)
    folders = whole["caps"]["folders_in_rings"]
    for cap in (8, 9, 10, 12, 16, folders, folders + 1):
        body = _wheel(explorer.project, rings=8, min_angle=0, max_sectors=cap, max_bins=1)
        sectors = body["sectors"]
        assert len(sectors) <= cap, (cap, len(sectors))
        holding = sum(1 for sector in sectors if sector["files"])
        assert len(body["bins"]["n"]) <= 1 + holding, cap
        assert "one more for each wedge holding a file" in body["caps"]["message"]
        assert sum(body["bins"]["n"]) == body["totals"]["files"]
        drawn = sum(1 for sector in sectors[1:] if sector["kind"] == "folder")
        assert body["caps"]["folders_gathered"] == folders - drawn
    # With room for every folder and the centre, every folder is drawn alone.
    roomy = _wheel(explorer.project, rings=8, min_angle=0, max_sectors=folders + 1)
    assert not [sector for sector in roomy["sectors"] if sector["kind"] == "more"]
    assert len(roomy["sectors"]) == folders + 1
    # The arranged Wheel keeps the same cap: by folders, four levels deep.
    for cap in (8, 9, 12):
        body = _wheel(
            explorer.project, arrange="folder,folder,folder,folder", min_angle=0, max_sectors=cap
        )
        assert len(body["sectors"]) <= cap, (cap, len(body["sectors"]))
        assert sum(body["bins"]["n"]) == body["totals"]["files"]


def test_deeper_folders_are_counted_in_the_outermost_ring(explorer) -> None:
    body = _wheel(explorer.project, rings=2)
    assert body["caps"]["folders_deeper"] > 0
    deep = next(sector for sector in body["sectors"] if sector["name"] == "deep")
    assert deep["files"] == len(_live_files(explorer, "deep"))


def test_a_bin_names_exactly_the_files_the_files_route_pages(explorer) -> None:
    body = _wheel(explorer.project, max_bins=40)
    bins = body["bins"]
    assert len(bins["n"]) <= 40 + len(body["sectors"])
    kinds = next(m for m in body["measures"] if m["key"] == "kind")["categories"]
    for place in range(len(bins["n"])):
        sector = body["sectors"][bins["sector"][place]]
        if sector["kind"] != "folder":
            continue
        scope = "direct" if sector["leaf"] == "direct" else "beneath"
        status, page = _ask(
            explorer.project,
            "explorer/files",
            folder=sector["id"],
            scope=scope,
            offset=bins["start"][place],
            limit=bins["n"][place],
        )
        assert status == HTTPStatus.OK
        assert len(page["files"]) == bins["n"][place]
        dominant = kinds[bins["dominant"]["kind"][place]]["key"]
        share = sum(1 for row in page["files"] if row["kind"] == dominant) / bins["n"][place]
        assert round(100 * share) == bins["purity"]["kind"][place]


def test_a_file_card_lists_every_time_with_its_source(explorer) -> None:
    first = explorer.experiments[0]
    raw = next(path for path in explorer.ids if path.startswith(f"{first}/raw/"))
    status, card = _ask(explorer.project, "explorer/entry", id=explorer.ids[raw])
    assert status == HTTPStatus.OK
    kinds = {time["kind"]: time for time in card["times"]}
    assert "modified" in kinds
    assert kinds["event"]["source"] == "instrument"
    # A date read from a folder's name is a day, never an instant, and says it is context.
    if "name_date" in kinds:
        assert kinds["name_date"]["precision"] in ("day", "range")
        assert "never" in kinds["name_date"]["note"]
    assert [crumb["name"] for crumb in card["ancestors"]][-2:] == [first, "raw"]


def test_two_dates_in_a_name_are_never_merged_into_one_span(explorer, tmp_path) -> None:
    """A file holding two named dates does not show the least "from", the greatest "to" and the
    least source across its claims, a span nobody read. One date is shown whole, from one claim,
    and the card says the name holds more than one."""
    import json

    from fixtures.explorer_project import AddedClaim, with_published

    predicate = "classifies.date_neighbourhood_of_a_file"
    first = explorer.experiments[0]
    summary = next(path for path in explorer.ids if path.startswith(f"{first}/analysis/"))
    status, card = _ask(explorer.project, "explorer/entry", id=explorer.ids[summary])
    assert status == HTTPStatus.OK, card
    own = next(time for time in card["times"] if time["kind"] == "name_date")
    later = json.dumps(
        {"folder": "a later name", "from": "2099-01-01", "source": "name", "to": "2099-01-02"},
        sort_keys=True,
    )
    project = with_published(explorer, tmp_path, claims=(AddedClaim(summary, predicate, later),))
    status, card = _ask(project, "explorer/entry", id=explorer.ids[summary])
    assert status == HTTPStatus.OK, card
    shown = next(time for time in card["times"] if time["kind"] == "name_date")
    assert (shown["at"], shown["until"], shown["source"]) == (
        own["at"],
        own["until"],
        own["source"],
    )
    assert "one of 2 dates" in shown["note"]


def test_an_earlier_run_is_read_whole_and_said_not_to_be_pinned(explorer) -> None:
    from fixtures.explorer_project import INFER_RUN

    body = _wheel(explorer.project, run=INFER_RUN)
    assert body["run_id"] == INFER_RUN
    assert body["pinned"]["is_pinned_run"] is False
    # The earlier run published no derivation: its own count says so.
    assert body["totals"]["published_derivations"] == 0


def test_the_projection_is_rebuilt_whole_where_deleted(explorer, tmp_path) -> None:
    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer.projection import projection_path

    copy = tmp_path / "copy"
    shutil.copytree(explorer.project, copy)
    first = _wheel(copy)
    assert projection_path(copy, PUBLISHED_RUN).is_dir()
    shutil.rmtree(projection_path(copy, PUBLISHED_RUN))
    again = _wheel(copy)
    assert again["projection"]["built_now"] is True
    for held in ("sectors", "bins", "totals"):
        assert again[held] == first[held]


def test_refusals_are_said_in_words(explorer) -> None:
    raw = next(path for path in explorer.ids if path.endswith(".nd2"))
    status, body = _ask(explorer.project, "explorer/wheel", focus=explorer.ids[raw])
    assert status == HTTPStatus.BAD_REQUEST
    assert body["error"]["code"] == "not_a_folder"
    status, body = _ask(explorer.project, "explorer/wheel", focus="entry_nothing")
    assert status == HTTPStatus.NOT_FOUND
    for query in ({"rings": "0"}, {"max_bins": "-1"}, {"size": "pixels"}, {"min_angle": "x"}):
        status, body = _ask(explorer.project, "explorer/wheel", **query)
        assert status == HTTPStatus.BAD_REQUEST, query
        assert body["error"]["parameter"] == next(iter(query))
    status, body = _ask(explorer.project, "explorer/wheel", run="run_x_00")
    assert status == HTTPStatus.NOT_FOUND


def test_the_wheel_reads_the_store_with_the_source_gone_and_no_network(
    explorer, tmp_path, monkeypatch
) -> None:
    """The projection is built here too, with the source gone. A copy that held the projection
    earlier tests built would never run the build without the root. A Python socket is refused;
    DuckDB's own sockets are not Python's, so every explorer connection is opened with its extension
    autoinstall off, and nothing it lacks is fetched."""
    import socket

    from ingence.api.v1.explorer.projection import connect

    copy = tmp_path / "copy"
    shutil.copytree(explorer.project, copy)
    shutil.rmtree(copy / "projections")

    def refuse(*_: object, **__: object) -> None:
        message = "the Wheel opened a network connection"
        raise AssertionError(message)

    monkeypatch.setattr(socket.socket, "connect", refuse)
    moved = explorer.pool.with_name("pool-away")
    explorer.pool.rename(moved)
    try:
        body = _wheel(copy)
        assert body["projection"]["built_now"] is True
        assert body["totals"]["files"] > 0
    finally:
        moved.rename(explorer.pool)
    connection = connect()
    try:
        setting = connection.execute(
            "SELECT current_setting('autoinstall_known_extensions')"
        ).fetchone()
    finally:
        connection.close()
    assert setting == (False,)


def test_while_a_job_runs_every_mode_reads_the_settled_run_and_says_so(explorer) -> None:
    """A published run stays in view while recomputation is pending: each mode serves the run
    settled before the job, names it, and says a job is running."""
    from fixtures.explorer_project import INFER_RUN, PUBLISHED_RUN
    from ingence.api.v1 import context_for, dispatch

    context = context_for(explorer.project, settled=INFER_RUN, job_running=True)
    assert context.run_current == PUBLISHED_RUN
    summary = next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))
    for path, query in (
        ("explorer/wheel", {}),
        ("explorer/lineage", {"focus": [explorer.ids[summary]]}),
        ("explorer/timeline", {"reading": ["activity"]}),
    ):
        status, body = dispatch(context, path, query)
        assert status == HTTPStatus.OK, (path, body)
        assert body["run_id"] == INFER_RUN, path
        assert body["pinned"]["job_running"] is True, path
        assert "settled run" in body["pinned"]["message"], path
