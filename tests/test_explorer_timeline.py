"""The Graph workspace's Timeline: every time with its source, nothing merged.

On the explorer fixture (``fixtures/explorer_project.py``, invented names and dates): three dated
experiments whose raw files carry instrument clocks and whose outputs were written days later, a
pool dated a year before, folders named for their days, and published derivations that fork and
join.
"""

from __future__ import annotations

import datetime as dt
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


def _ask(
    project: Path, path: str = "explorer/timeline", **query: Any
) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(v)] for name, v in query.items()})


def _timeline(project: Path, **query: Any) -> dict[str, Any]:
    status, body = _ask(project, **query)
    assert status == HTTPStatus.OK, body
    return body


def _summary(explorer: ExplorerProject) -> str:
    return next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))


def test_activity_counts_every_file_once_per_source_dated_undated_or_outside(explorer) -> None:
    body = _timeline(explorer.project, reading="activity")
    activity = body["activity"]
    assert activity is not None
    edges = activity["bins"]["edges"]
    assert edges == sorted(edges)
    assert activity["bins"]["unit"] in ("hour", "day", "week", "month", "quarter", "year")
    for key in ("best", "earliest", "event", "modified", "created", "name"):
        for index, track in enumerate(activity["tracks"]):
            placed = sum(activity["counts"][key][index])
            total = placed + activity["undated"][key][index] + activity["outside"][key][index]
            assert total == track["files"], (key, track["name"])
    files = sum(track["files"] for track in activity["tracks"])
    status, wheel = _ask(explorer.project, "explorer/wheel")
    assert status == HTTPStatus.OK
    assert files == wheel["totals"]["files"]


def test_sources_are_told_apart_and_a_named_date_is_context(explorer) -> None:
    body = _timeline(explorer.project, reading="activity")
    dated = body["dated"]
    raw_files = [path for path in explorer.ids if path.endswith(".nd2")]
    # Only the instruments' files hold a content clock.
    assert dated["event"] == len(raw_files)
    # Every file has a modification time; not every file has a date in its name.
    files = sum(track["files"] for track in body["activity"]["tracks"])
    assert dated["modified"] == files
    assert dated["name"] < files
    named = _timeline(explorer.project, reading="activity", source="name")
    assert any("never a creation time" in note for note in named["notes"])
    meanings = {source["key"]: source["meaning"] for source in body["sources"]}
    assert "never its creation time" in meanings["name"]


def test_a_window_narrows_the_bins_and_counts_the_rest_as_outside(explorer) -> None:
    whole = _timeline(explorer.project, reading="activity")
    start = dt.datetime(2024, 3, 1, tzinfo=dt.UTC)
    end = dt.datetime(2024, 4, 1, tzinfo=dt.UTC)
    narrowed = _timeline(
        explorer.project, reading="activity", **{"from": start.isoformat(), "to": end.isoformat()}
    )
    assert narrowed["window"] is not None
    assert narrowed["activity"]["bins"]["unit"] in ("hour", "day", "week")
    outside = sum(narrowed["activity"]["outside"]["best"])
    assert outside > sum(whole["activity"]["outside"]["best"])


def test_a_bin_s_files_are_exactly_the_files_route_s_window(explorer) -> None:
    body = _timeline(explorer.project, reading="activity")
    activity = body["activity"]
    edges = activity["bins"]["edges"]
    checked = 0
    for index, track in enumerate(activity["tracks"]):
        if track["kind"] != "folder":
            continue
        for place, value in enumerate(activity["counts"]["best"][index]):
            if not value:
                continue
            status, page = _ask(
                explorer.project,
                "explorer/files",
                folder=track["id"],
                scope="beneath",
                source="best",
                limit=500,
                **{"from": edges[place], "to": edges[place + 1]},
            )
            assert status == HTTPStatus.OK, page
            assert page["total"] == value
            checked += 1
    assert checked


def _stored_times(project: Path) -> dict[str, dict[str, dt.datetime]]:
    """Each live file's times as the run stores them, parsed in Python and never by the SQL the
    routes place with: the earliest credible time's text, and the census's nanoseconds."""
    import json

    from ingence.storage.catalog import open_catalog

    def instant(nanoseconds: int) -> dt.datetime:
        return dt.datetime(1970, 1, 1, tzinfo=dt.UTC) + dt.timedelta(
            microseconds=nanoseconds // 1000
        )

    held: dict[str, dict[str, dt.datetime]] = {}
    with open_catalog(project) as catalog:
        for entry_id, modified, event, event_source in catalog.query(
            "SELECT entry_id, modified_ns, event_time_ns, event_time_source FROM entries "
            "WHERE NOT is_deleted AND entry_kind = 'filesystem' AND entry_type <> 'directory'"
        ).fetchall():
            times = held.setdefault(str(entry_id), {})
            if modified is not None:
                times["modified"] = instant(int(modified))
            if event is not None and (event_source or "") != "filename":
                times["event"] = instant(int(event))
        for subject, value in catalog.query(
            "SELECT subject, object_reference FROM claims "
            "WHERE predicate = 'classifies.earliest_credible_time'"
        ).fetchall():
            at = dt.datetime.fromisoformat(str(json.loads(value)["at"]).replace("Z", "+00:00"))
            if str(subject) in held:
                earlier = held[str(subject)].get("earliest")
                held[str(subject)]["earliest"] = at if earlier is None else min(at, earlier)
    for times in held.values():
        best = times.get("earliest") or times.get("event") or times.get("modified")
        if best is not None:
            times["best"] = best
    return held


def _moment_of(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def _second(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_every_file_is_counted_in_the_bin_its_stored_time_falls_in(explorer) -> None:
    """The bins against the stored times, read apart from the SQL that places them: a time moved
    by the server's zone lands in another bin, or outside, and this sees it."""
    import bisect

    stored = _stored_times(explorer.project)
    for source in ("earliest", "best", "modified"):
        body = _timeline(explorer.project, reading="activity", source=source)
        activity = body["activity"]
        edges = [_moment_of(edge) for edge in activity["bins"]["edges"]]
        expected = [0] * (len(edges) - 1)
        for times in stored.values():
            moment = times.get(source)
            if moment is not None and edges[0] <= moment < edges[-1]:
                expected[bisect.bisect_right(edges, moment) - 1] += 1
        counted = [sum(column) for column in zip(*activity["counts"][source], strict=True)]
        assert counted == expected, source
        held = [times[source] for times in stored.values() if source in times]
        assert body["extent"]["start"] == _second(min(held)), source
        assert body["extent"]["end"] == _second(max(held)), source


def _in_zone(zone: str, project: Path, path: str, **query: Any) -> dict[str, Any]:
    """Ask one route in a fresh interpreter whose host zone is ``zone`` (DuckDB takes its zone
    from the host when a connection opens, so the zone must be the process's from the start)."""
    import json
    import os
    import subprocess
    import sys

    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from ingence.api.v1 import context_for, dispatch\n"
        "context = context_for(Path(sys.argv[1]), settled=None, job_running=False)\n"
        "query = {name: [value] for name, value in json.loads(sys.argv[3]).items()}\n"
        "status, body = dispatch(context, sys.argv[2], query)\n"
        "print(json.dumps({'status': int(status), 'body': body}))\n"
    )
    environment = {**os.environ, "TZ": zone, "PYTHONPATH": os.pathsep.join(sys.path)}
    done = subprocess.run(
        [sys.executable, "-c", script, str(project), path, json.dumps(query)],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    answered: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    assert answered["status"] == HTTPStatus.OK, answered
    return dict(answered["body"])


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="Windows gives DuckDB its own zone setting, not the TZ variable",
)
@pytest.mark.parametrize("zone", ["Asia/Tokyo", "America/New_York"])
def test_an_earliest_credible_time_is_placed_where_it_is_stored_in_any_host_zone(
    explorer, zone: str
) -> None:
    """A server east or west of UTC must not place every earliest credible time by its own
    offset. The Activity's extent and a bin's page of files both hold the stored instant."""
    stored = _stored_times(explorer.project)
    earliest = {entry: times["earliest"] for entry, times in stored.items() if "earliest" in times}
    first = min(earliest.values())
    body = _in_zone(
        zone, explorer.project, "explorer/timeline", reading="activity", source="earliest"
    )
    assert body["extent"]["start"] == _second(first)
    # The files route's window, in that zone, finds the files stored at that instant.
    at_first = {entry for entry, moment in earliest.items() if moment == first}
    page = _in_zone(
        zone,
        explorer.project,
        "explorer/files",
        folder=body["focus"]["id"],
        scope="beneath",
        source="earliest",
        limit=500,
        **{"from": _second(first), "to": _second(first + dt.timedelta(seconds=1))},
    )
    assert {row["id"] for row in page["files"]} == at_first


#: Explorer modules allowed to open DuckDB bare. The set is empty: every module opens through
#: ``projection.connect``.
_NOT_YET_THROUGH_CONNECT: frozenset[str] = frozenset()


def test_every_explorer_connection_opens_through_the_one_in_utc() -> None:
    """Any explorer connection opened with the host's zone reads a zoned time in the server's wall
    clock. Every module opens through ``projection.connect``."""
    from pathlib import Path

    import ingence.api.v1.explorer as package

    folder = Path(package.__file__).parent
    bare = {
        path.name
        for path in folder.glob("*.py")
        if path.name != "projection.py" and "duckdb.connect(" in path.read_text("utf-8")
    }
    assert bare <= _NOT_YET_THROUGH_CONNECT, sorted(bare - _NOT_YET_THROUGH_CONNECT)
    from ingence.api.v1.explorer.projection import connect

    connection = connect()
    try:
        # Where the time-zone extension is on the machine, its zone is UTC; where it is not,
        # DuckDB's core reads a zoned time in UTC, and no zone is set.
        placed = connection.execute(
            "SELECT TRY_CAST(TRY_CAST('2023-04-20T09:00:00+02:00' AS TIMESTAMPTZ) AS TIMESTAMP)"
        ).fetchone()
    finally:
        connection.close()
    assert placed is not None
    assert placed[0] == dt.datetime(2023, 4, 20, 7, 0)


def test_the_thread_places_each_file_by_the_chosen_source_and_invents_nothing(explorer) -> None:
    summary = explorer.ids[_summary(explorer)]
    body = _timeline(explorer.project, reading="thread", focus=summary, depth=4)
    thread = body["thread"]
    assert thread is not None
    derived = {path for pair in explorer.derivations for path in pair}
    paths = {entry: path for path, entry in explorer.ids.items()}
    assert {paths[node["id"]] for node in thread["nodes"]} == derived
    assert all(node["placed"] is not None for node in thread["nodes"])
    # Under the content clock only the instruments' files are placed; the rest are undated.
    clocked = _timeline(explorer.project, reading="thread", focus=summary, depth=4, source="event")
    placed = {paths[node["id"]] for node in clocked["thread"]["nodes"] if node["placed"]}
    assert placed and all(path.endswith(".nd2") for path in placed)
    assert set(clocked["thread"]["undated"]) == {
        node["id"] for node in clocked["thread"]["nodes"] if not node["placed"]
    }
    # A file's marks keep one per source, and an instrument's clock says it is one.
    raw = next(node for node in clocked["thread"]["nodes"] if paths[node["id"]].endswith("_01.nd2"))
    sources = [mark["source"] for mark in raw["marks"]]
    assert len(sources) == len(set(sources))
    event = next(mark for mark in raw["marks"] if mark["source"] == "event")
    assert event["detail"] == "instrument"
    assert event["precision"] == "instant"


def test_a_time_published_as_conflicting_is_placed_and_flagged(explorer, tmp_path) -> None:
    """An earliest credible time published as conflicting is not placed by "best available" in
    silence. Its placed mark says so; the others do not."""
    from fixtures.explorer_project import with_published

    summary = _summary(explorer)
    predicate = "classifies.earliest_credible_time"
    project = with_published(explorer, tmp_path, claims=((summary, predicate, None, True),))
    for source in ("best", "earliest"):
        body = _timeline(
            project, reading="thread", focus=explorer.ids[summary], depth=1, source=source
        )
        nodes = {node["id"]: node for node in body["thread"]["nodes"]}
        placed = nodes[explorer.ids[summary]]["placed"]
        assert placed["source"] == "earliest"
        assert placed["conflicting"] is True
        others = [node["placed"] for key, node in nodes.items() if key != explorer.ids[summary]]
        assert others and not any(mark and mark["conflicting"] for mark in others)


def test_the_thread_counts_undated_files_never_a_gathered_mark(explorer) -> None:
    """ "N of M drawn have no time" must not count an aggregate standing for several files, or a
    folder focus, as one undated file."""
    raw_one = explorer.mentions[0][1]
    body = _timeline(
        explorer.project,
        reading="thread",
        focus=explorer.ids[raw_one],
        direction="down",
        depth=1,
        fan=2,
        source="event",
    )
    thread = body["thread"]
    kinds = {node["id"]: node["kind"] for node in thread["nodes"]}
    assert "aggregate" in kinds.values()
    assert all(kinds[identifier] == "file" for identifier in thread["undated"])
    files = sum(1 for kind in kinds.values() if kind == "file")
    said = [note for note in body["notes"] if "have no time" in note]
    if thread["undated"]:
        assert said[0].startswith(f"{len(thread['undated']):,} of {files:,} files drawn")
    else:
        assert not said
    assert any("stands for several files" in note for note in body["notes"])


def test_runs_are_listed_apart_and_the_one_read_is_marked(explorer) -> None:
    from fixtures.explorer_project import INFER_RUN, PUBLISHED_RUN

    body = _timeline(explorer.project, reading="activity")
    read = [run for run in body["runs"] if run["is_read"]]
    assert [run["run_id"] for run in read] == [PUBLISHED_RUN]
    earlier = _timeline(explorer.project, reading="activity", run=INFER_RUN)
    assert [run["run_id"] for run in earlier["runs"] if run["is_read"]] == [INFER_RUN]
    assert earlier["pinned"]["is_pinned_run"] is False


def test_rows_can_be_the_experiment_units_beneath(explorer) -> None:
    body = _timeline(explorer.project, reading="activity", by="units")
    folders = [track for track in body["activity"]["tracks"] if track["kind"] == "folder"]
    assert {track["name"] for track in folders} == set(explorer.experiments)
    assert all(track["unit_status"] != "none" for track in folders)
    named = [track for track in folders if track["name_from"]]
    assert named, "a dated experiment folder's own named day is its row's context"


def test_a_unit_inside_a_unit_is_counted_once_in_the_outermost_row(explorer, tmp_path) -> None:
    """With one experiment's analysis folder read as a unit too, the parent's row must not say more
    files than it counted, and the rest must add up. Rows by units are the outermost units; every
    row's files, dated, undated and outside, come from one placement."""
    from fixtures.explorer_project import AddedClaim, with_published

    first = explorer.experiments[0]
    project = with_published(
        explorer,
        tmp_path,
        claims=(
            AddedClaim(first, _experiment_predicate(explorer, first), onto=f"{first}/analysis"),
        ),
    )
    body = _timeline(project, reading="activity", by="units")
    activity = body["activity"]
    folders = [track for track in activity["tracks"] if track["kind"] == "folder"]
    assert {track["name"] for track in folders} == set(explorer.experiments)
    paths = [track["path"] for track in folders]
    assert not [a for a in paths for b in paths if a != b and a.startswith(f"{b}/")]
    for key in ("best", "earliest", "modified", "name"):
        for index, track in enumerate(activity["tracks"]):
            assert track["files"] >= 0
            counted = sum(activity["counts"][key][index])
            counted += activity["undated"][key][index] + activity["outside"][key][index]
            assert counted == track["files"], (key, track["name"])
    status, wheel = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK
    assert sum(track["files"] for track in activity["tracks"]) == wheel["totals"]["files"]


def _experiment_predicate(explorer: ExplorerProject, path: str) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(explorer.project) as catalog:
        found = catalog.query(
            "SELECT min(predicate) FROM claims WHERE subject = ? "
            "AND predicate LIKE 'classifies.experiment_folder%'",
            [explorer.ids[path]],
        ).fetchone()
    assert found is not None and found[0], path
    return str(found[0])


def test_refusals_are_said_in_words(explorer) -> None:
    for query, parameter in (
        ({"reading": "weekly"}, "reading"),
        ({"source": "rumour"}, "source"),
        ({"from": "2024-01-01T00:00:00Z"}, "to"),
        ({"from": "2024-02-01T00:00:00Z", "to": "2024-01-01T00:00:00Z"}, "to"),
        ({"by": "colour"}, "by"),
    ):
        status, body = _ask(explorer.project, **query)
        assert status == HTTPStatus.BAD_REQUEST, query
        assert body["error"]["parameter"] == parameter, query
    status, body = _ask(
        explorer.project, reading="activity", focus=explorer.ids[_summary(explorer)]
    )
    assert status == HTTPStatus.BAD_REQUEST
    assert body["error"]["code"] == "not_a_folder"
