"""Editing a topic the way the Graph workspace does, and by adding and removing members.

The workspace sends every member id the overlay served back as a plain string when a person renames
a topic or adds or removes a file. A core is served as ``group:<words>`` and a line as
``line:<digest>``. Held here:

- replaying the workspace's rename and add-a-file edits on a topic that holds a core, a line, an
  experiment and a file keeps every member as it was, a core and a line included, and an id that was
  an experiment stays an experiment; a ``group:`` or ``line:`` key the topic does not hold refuses
  the batch;
- ``add`` and ``remove_members`` on a topic that stands: each is one event whose payload says the
  topic's whole member list; the members already held are carried over unchecked, so a file since
  gone never blocks an add or a removal; a new member is checked as a new topic's are; an unknown
  member to remove, a whole member list beside them, and a topic left with no member refuse the
  batch in words and write nothing;
- the names and paths a carried member was said with are kept at its current place after a move.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes.overlay import (
    OverlayRefusedError,
    prepare_overlay,
    topics_standing,
    write_prepared,
)
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

    return build_topic_project(tmp_path_factory.mktemp("topic_edits"))


@pytest.fixture
def project(built: TopicProject, tmp_path: Path) -> Path:
    copy = tmp_path / "project"
    shutil.copytree(built.project, copy)
    return copy


def _write(project: Path, *edits: dict[str, Any]) -> list[Any]:
    prepared = prepare_overlay(project, actor=ACTOR, occurred_at=SAID_AT, edits=list(edits))
    return write_prepared(project, prepared, actor=ACTOR, occurred_at=SAID_AT)


def _group_events(project: Path) -> list[Any]:
    return [e for e in read_events(project, held=False) if e.target.startswith("overlay:group:")]


def _entry(project: Path, relative_path: str) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        found = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = ?", [relative_path]
        ).fetchone()
    assert found is not None, relative_path
    return str(found[0])


def _a_file_in(built: TopicProject, unit: str, index: int = 0) -> str:
    folder = built.folders[unit]
    names = sorted(p.name for p in (built.pool / folder).iterdir())
    return _entry(built.project, f"{folder}/{names[index]}")


def _served_ids(project: Path, group_id: str) -> list[str]:
    """What the Graph workspace holds for a topic: every member id the overlay served, in order."""
    from ingence.api.v1 import context_for, dispatch

    status, body = dispatch(
        context_for(project, settled=None, job_running=False), "explorer/overlay", {}
    )
    assert status == 200, body
    [statement] = [s for s in body["statements"] if s["target"] == f"overlay:group:{group_id}"]
    return [member["id"] for member in statement["members"]]


def _topic_with_every_kind(built: TopicProject, project: Path) -> tuple[str, dict[str, Any]]:
    [event] = _write(
        project,
        {
            "kind": "topic",
            "name": "Everything",
            "note": "kept",
            "members": [
                {"kind": "experiment", "id": built.ids["t0_00"]},
                {"kind": "core", "terms": ["droska", "miravo"]},
                {
                    "kind": "line",
                    "experiments": [built.ids["t2_00"], built.ids["t2_01"]],
                    "words": ["pellit", "miravo", "droska"],
                    "label": "pellit miravo droska",
                },
                _a_file_in(built, "t1_00"),
            ],
        },
    )
    return event.target.rsplit(":", 1)[1], json.loads(event.payload)


def test_the_workspace_s_rename_keeps_a_core_a_line_and_an_experiment(
    built: TopicProject, project: Path
) -> None:
    group_id, first = _topic_with_every_kind(built, project)
    served = _served_ids(project, group_id)
    assert "group:droska miravo" in served and any(i.startswith("line:") for i in served)
    # Exactly what the workspace sends: the served ids, plain strings, and the new name.
    [renamed] = _write(
        project, {"kind": "group", "id": group_id, "name": "Renamed", "members": served}
    )
    after = json.loads(renamed.payload)
    assert after["name"] == "Renamed" and after["note"] == "kept"
    for key in ("members", "experiments", "cores", "lines"):
        assert after[key] == first[key], key
    assert after["experiments"] == [built.ids["t0_00"]]
    [topic] = topics_standing(project)
    assert sorted(m.kind for m in topic.members) == ["core", "entry", "experiment", "line"]
    [line] = [m for m in topic.members if m.kind == "line"]
    assert line.words == ("droska", "miravo", "pellit") and line.label == "pellit miravo droska"
    assert len(_group_events(project)) == 2


def test_the_workspace_s_add_a_file_and_remove_a_file_keep_the_rest(
    built: TopicProject, project: Path
) -> None:
    group_id, first = _topic_with_every_kind(built, project)
    served = _served_ids(project, group_id)
    more = _a_file_in(built, "t1_01")
    [added] = _write(
        project, {"kind": "group", "id": group_id, "name": "Everything", "members": [*served, more]}
    )
    after = json.loads(added.payload)
    assert more in after["members"]
    assert after["cores"] == first["cores"] and after["lines"] == first["lines"]
    assert after["experiments"] == first["experiments"]
    # Removing the file it had: the same, with that file gone, and the core and line still held.
    file_id = _a_file_in(built, "t1_00")
    left = [i for i in _served_ids(project, group_id) if i != file_id]
    [dropped] = _write(
        project, {"kind": "group", "id": group_id, "name": "Everything", "members": left}
    )
    final = json.loads(dropped.payload)
    assert file_id not in final["members"] and more in final["members"]
    assert final["cores"] == first["cores"] and final["lines"] == first["lines"]
    assert final["experiments"] == first["experiments"]


def test_a_key_the_topic_does_not_hold_refuses_the_batch(
    built: TopicProject, project: Path
) -> None:
    group_id, _ = _topic_with_every_kind(built, project)
    served = _served_ids(project, group_id)
    for stranger in ("group:never held", "line:0123456789ab"):
        with pytest.raises(OverlayRefusedError, match="names no item"):
            _write(
                project,
                {
                    "kind": "group",
                    "id": group_id,
                    "name": "Everything",
                    "members": [*served, stranger],
                },
            )
    # A key of another topic is no key of this one.
    [other] = _write(
        project,
        {
            "kind": "topic",
            "name": "Other",
            "members": [{"kind": "core", "terms": ["keltan", "brisol"]}],
        },
    )
    with pytest.raises(OverlayRefusedError, match="names no item"):
        _write(
            project,
            {
                "kind": "group",
                "id": group_id,
                "name": "Everything",
                "members": [*served, "group:brisol keltan"],
            },
        )
    assert other.target != group_id
    assert len(_group_events(project)) == 2


def test_a_line_whose_experiments_are_gone_does_not_stop_a_rename_by_the_workspace(
    built: TopicProject, project: Path
) -> None:
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    append_event(
        project,
        kind=RecordEventKind.ANNOTATE,
        actor=ACTOR,
        occurred_at=SAID_AT,
        target="overlay:group:hgline",
        against_run_id=built.infer,
        payload={
            "overlay": "group",
            "name": "Old line",
            "members": [built.ids["t0_00"]],
            "lines": [{"experiments": ["entry_since_gone"], "words": ["alpha"], "label": None}],
            "names": {"entry_since_gone": "gone_folder"},
        },
        scope=PROJECT_SCOPE,
    )
    served = _served_ids(project, "hgline")
    [renamed] = _write(project, {"kind": "group", "id": "hgline", "name": "New", "members": served})
    after = json.loads(renamed.payload)
    assert after["lines"][0]["experiments"] == ["entry_since_gone"]
    assert after["names"]["entry_since_gone"] == "gone_folder"


# ---------------------------------------------------------------------------------------------
# add and remove_members.
# ---------------------------------------------------------------------------------------------


def _topic_holding_a_gone_file(built: TopicProject, project: Path) -> str:
    """A topic with a live file, a core, a line, and a file the run does not hold."""
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    live = _a_file_in(built, "t1_00")
    append_event(
        project,
        kind=RecordEventKind.ANNOTATE,
        actor=ACTOR,
        occurred_at=SAID_AT,
        target="overlay:group:hgstale",
        against_run_id=built.infer,
        payload={
            "overlay": "group",
            "name": "Stale",
            "members": sorted([live, "entry_since_gone"]),
            "cores": [["droska", "miravo"]],
            "lines": [
                {
                    "experiments": [built.ids["t2_00"], built.ids["t2_01"]],
                    "words": ["droska", "miravo", "pellit"],
                    "label": None,
                }
            ],
            "names": {"entry_since_gone": "scan.tif", live: "kept.nd2"},
            "paths": {"entry_since_gone": "old/scan.tif", live: "elsewhere/kept.nd2"},
        },
        scope=PROJECT_SCOPE,
    )
    return "hgstale"


def test_add_carries_what_is_held_unchecked_and_asks_the_run_only_of_the_new(
    built: TopicProject, project: Path
) -> None:
    group_id = _topic_holding_a_gone_file(built, project)
    live = _a_file_in(built, "t1_00")
    new_file = _a_file_in(built, "t1_01")
    before = len(_group_events(project))
    [added] = _write(
        project,
        {
            "kind": "topic",
            "id": group_id,
            "add": [new_file, {"kind": "experiment", "id": built.ids["t3_00"]}],
        },
    )
    assert len(_group_events(project)) == before + 1, "one event"
    payload = json.loads(added.payload)
    # The whole member list, as every event says it.
    assert payload["name"] == "Stale"
    assert payload["members"] == sorted([live, "entry_since_gone", new_file, built.ids["t3_00"]])
    assert payload["experiments"] == [built.ids["t3_00"]]
    assert payload["cores"] == [["droska", "miravo"]] and len(payload["lines"]) == 1
    # The words a held member was said with stay; the new members' are set down.
    assert payload["names"]["entry_since_gone"] == "scan.tif"
    assert payload["names"][live] == "kept.nd2"
    assert new_file in payload["names"] and new_file in payload["paths"]
    [topic] = topics_standing(project)
    assert sorted(m.kind for m in topic.members) == [
        "core",
        "entry",
        "entry",
        "entry",
        "experiment",
        "line",
    ]
    # A new member the run does not hold refuses the batch; one already held does not.
    with pytest.raises(OverlayRefusedError, match="holds no live item entry_not_here"):
        _write(project, {"kind": "topic", "id": group_id, "add": ["entry_not_here"]})
    [again] = _write(project, {"kind": "topic", "id": group_id, "add": ["entry_since_gone", live]})
    assert json.loads(again.payload)["members"] == payload["members"]
    # A core or a line may be added; a plain string equal to a held key is already held.
    [more] = _write(
        project,
        {
            "kind": "topic",
            "id": group_id,
            "add": [
                {"kind": "core", "terms": ["nuntle", "gavish"]},
                "group:droska miravo",
            ],
        },
    )
    assert json.loads(more.payload)["cores"] == [["droska", "miravo"], ["gavish", "nuntle"]]


def test_remove_members_takes_one_member_by_the_id_the_overlay_served(
    built: TopicProject, project: Path
) -> None:
    group_id = _topic_holding_a_gone_file(built, project)
    live = _a_file_in(built, "t1_00")
    served = _served_ids(project, group_id)
    line_key = next(i for i in served if i.startswith("line:"))
    # The gone file: removable, though the run holds nothing by that id.
    [no_file] = _write(
        project, {"kind": "topic", "id": group_id, "remove_members": ["entry_since_gone"]}
    )
    payload = json.loads(no_file.payload)
    assert payload["members"] == [live]
    assert "entry_since_gone" not in payload.get("names", {})
    assert payload["names"][live] == "kept.nd2"
    assert payload["cores"] == [["droska", "miravo"]] and len(payload["lines"]) == 1
    # The core by its key, and the line by its key: one event each.
    before = len(_group_events(project))
    [no_core] = _write(
        project, {"kind": "topic", "id": group_id, "remove_members": ["group:droska miravo"]}
    )
    assert "cores" not in json.loads(no_core.payload)
    [no_line] = _write(project, {"kind": "topic", "id": group_id, "remove_members": [line_key]})
    assert "lines" not in json.loads(no_line.payload)
    assert len(_group_events(project)) == before + 2
    # The last member cannot be taken away: that is withdrawing the topic.
    with pytest.raises(OverlayRefusedError, match="would hold no member"):
        _write(project, {"kind": "topic", "id": group_id, "remove_members": [live]})
    assert [t.name for t in topics_standing(project)] == ["Stale"]


def test_add_and_remove_in_one_edit_are_one_event_and_the_refusals_are_whole(
    built: TopicProject, project: Path
) -> None:
    group_id = _topic_holding_a_gone_file(built, project)
    live = _a_file_in(built, "t1_00")
    new_file = _a_file_in(built, "t1_01")
    before = len(_group_events(project))
    [event] = _write(
        project,
        {
            "kind": "topic",
            "id": group_id,
            "name": "Both",
            "add": [new_file],
            "remove_members": ["entry_since_gone"],
        },
    )
    assert len(_group_events(project)) == before + 1
    payload = json.loads(event.payload)
    assert payload["name"] == "Both"
    assert payload["members"] == sorted([live, new_file])
    for edit, expected in (
        ({"remove_members": ["entry_never_held"]}, "is no member of the topic"),
        ({"remove_members": []}, "list of the members to remove"),
        ({"members": [live], "add": [new_file]}, "not both"),
    ):
        with pytest.raises(OverlayRefusedError, match=expected):
            _write(
                project,
                {"kind": "topic", "name": "Rest of the batch", "members": [live]},
                {"kind": "topic", "id": group_id, **edit},
            )
    with pytest.raises(OverlayRefusedError, match="names the topic"):
        _write(project, {"kind": "topic", "add": [new_file]})
    with pytest.raises(OverlayRefusedError, match="names no members"):
        _write(project, {"kind": "topic", "id": group_id, "remove": True, "add": [new_file]})
    # Nothing of the refused batches was written, the good edit before them included.
    assert len(_group_events(project)) == before + 1
    # A withdrawn topic is brought back by naming it all, not by adding to it.
    _write(project, {"kind": "topic", "id": group_id, "remove": True})
    with pytest.raises(OverlayRefusedError, match="was withdrawn"):
        _write(project, {"kind": "topic", "id": group_id, "add": [new_file]})


def test_names_and_paths_are_kept_at_the_place_a_member_has_moved_to(
    built: TopicProject, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename keeps the words a member was said with under the identifier it has now, so a
    reader looking the member up where it is finds them."""
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.record import log
    from ingence.record.log import append_event

    append_event(
        project,
        kind=RecordEventKind.ANNOTATE,
        actor=ACTOR,
        occurred_at=SAID_AT,
        target="overlay:group:hgmoved",
        against_run_id=built.infer,
        payload={
            "overlay": "group",
            "name": "Moved",
            "members": ["entry_old_place"],
            "names": {"entry_old_place": "scan.tif"},
            "paths": {"entry_old_place": "a/scan.tif"},
        },
        scope=PROJECT_SCOPE,
    )
    monkeypatch.setattr(
        log, "moved_entries", lambda _project: {"entry_old_place": "entry_new_place"}
    )
    [renamed] = _write(project, {"kind": "topic", "id": "hgmoved", "name": "Moved again"})
    payload = json.loads(renamed.payload)
    assert payload["members"] == ["entry_new_place"]
    assert payload["names"] == {"entry_new_place": "scan.tif"}
    assert payload["paths"] == {"entry_new_place": "a/scan.tif"}
    [topic] = topics_standing(project)
    assert topic.names == {"entry_new_place": "scan.tif"}
