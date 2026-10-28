"""A topic is a group read under a new word: its members, its edits, its events.

The test uses the themes' invented pool, indexed and inferred by the engine
(``fixtures.topic_project``). Held here:

- a group of files and folders is written and read unchanged, and reads as a topic with entries
  only;
- a topic may hold an experiment, a core followed by its words and a line of work added by its
  experiments, each member saying its kind in the payload;
- a kind of member the engine does not know, a core with no word, a word with a space and a line
  added by nothing refuse the whole batch in words, and nothing is written;
- rename, remove a member and withdraw are one event each, and an edit that leaves the members out
  keeps them (a member since gone does not stop a rename);
- the edit's kind may say ``topic`` or ``group`` and writes the same target;
- inference never reads a topic: the claims, assertions and findings of an inference are those of
  the same inference over a copy that holds none.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes.overlay import (
    OverlayRefusedError,
    prepare_overlay,
    standing_layer,
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

    return build_topic_project(tmp_path_factory.mktemp("topics"))


@pytest.fixture
def project(built: TopicProject, tmp_path: Path) -> Path:
    copy = tmp_path / "project"
    shutil.copytree(built.project, copy)
    return copy


def _entry(project: Path, relative_path: str) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        found = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = ?", [relative_path]
        ).fetchone()
    assert found is not None, relative_path
    return str(found[0])


def _write(project: Path, *edits: dict[str, Any], at: str = SAID_AT) -> list[Any]:
    prepared = prepare_overlay(project, actor=ACTOR, occurred_at=at, edits=list(edits))
    return write_prepared(project, prepared, actor=ACTOR, occurred_at=at)


def _group_events(project: Path) -> list[Any]:
    return [e for e in read_events(project, held=False) if e.target.startswith("overlay:group:")]


def _a_file_in(built: TopicProject, unit: str) -> str:
    folder = built.folders[unit]
    return _entry(built.project, f"{folder}/{next(iter(sorted(_names(built, unit))))}")


def _names(built: TopicProject, unit: str) -> list[str]:
    return sorted(p.name for p in (built.pool / built.folders[unit]).iterdir())


def test_a_group_of_entries_is_written_and_read_exactly_as_before(
    built: TopicProject, project: Path
) -> None:
    file_id = _a_file_in(built, "t0_00")
    folder_id = built.ids["t0_01"]
    [event] = _write(
        project, {"kind": "group", "name": "the brisa series", "members": [file_id, folder_id]}
    )
    payload = json.loads(event.payload)
    # The payload of an old group is unchanged: no field of a kind it does not hold.
    assert set(payload) == {"overlay", "name", "members", "names", "paths", "via"}
    assert payload["members"] == sorted([file_id, folder_id])
    assert event.target.startswith("overlay:group:")
    layer = standing_layer(project)
    [(group_id, name, members)] = layer.groups
    assert (name, members) == ("the brisa series", tuple(sorted([file_id, folder_id])))
    [topic] = topics_standing(project)
    assert topic.topic_id == group_id and topic.name == name
    assert [m.kind for m in topic.members] == ["entry", "entry"]
    assert (topic.made_by, topic.edited_by, topic.earlier) == (ACTOR, ACTOR, 0)


def test_a_topic_holds_experiments_cores_and_lines_and_each_member_says_its_kind(
    built: TopicProject, project: Path
) -> None:
    anchors = [built.ids["t2_00"], built.ids["t2_01"]]
    [event] = _write(
        project,
        {
            "kind": "topic",
            "name": "Stains",
            "note": "the ones to compare",
            "members": [
                {"kind": "experiment", "id": built.ids["t0_00"]},
                {"kind": "core", "terms": ["Miravo", "droska"]},
                {"kind": "line", "experiments": anchors, "words": ["pellit", "miravo", "droska"]},
                _a_file_in(built, "t1_00"),
            ],
        },
    )
    payload = json.loads(event.payload)
    # ``members`` keeps holding entry identifiers (a move is followed); the rest say their kind.
    assert payload["experiments"] == [built.ids["t0_00"]]
    assert built.ids["t0_00"] in payload["members"]
    assert payload["cores"] == [["droska", "miravo"]]
    [line] = payload["lines"]
    assert line["experiments"] == sorted(anchors)
    assert line["words"] == ["droska", "miravo", "pellit"]
    # The anchors are checked against the run and their words are kept as they were said.
    assert set(payload["names"]) >= set(anchors)
    [topic] = topics_standing(project)
    assert sorted(m.kind for m in topic.members) == ["core", "entry", "experiment", "line"]
    assert topic.note == "the ones to compare"
    core = next(m for m in topic.members if m.kind == "core")
    assert core.key == "group:droska miravo" and core.terms == ("droska", "miravo")


def test_a_kind_of_member_the_engine_does_not_know_refuses_the_whole_batch_in_words(
    built: TopicProject, project: Path
) -> None:
    good = {"kind": "group", "name": "fine", "members": [built.ids["t0_00"]]}
    for member, expected in (
        ({"kind": "study", "id": built.ids["t0_00"]}, "no kind of member"),
        ({"kind": "core", "terms": []}, "a list of words with at least one"),
        ({"kind": "core", "terms": [" "]}, "empty or has a space"),
        ({"kind": "core", "terms": ["group:x"]}, "with a colon"),
        ({"kind": "core", "terms": ["bell\x07"]}, "control character"),
        ({"kind": "line", "experiments": []}, "names the experiments it was added by"),
        ({"kind": "line", "experiments": ["entry_not_in_this_run"]}, "holds no live item"),
        ({"kind": "experiment"}, "names no item"),
        (7, "an item id or an object"),
    ):
        with pytest.raises(OverlayRefusedError, match=expected) as caught:
            _write(
                project,
                {"kind": "exclude", "entry": built.ids["t0_01"], "reason": "a good edit"},
                good,
                {"kind": "topic", "name": "bad", "members": [member]},
            )
        if isinstance(member, dict) and member.get("kind") == "study":
            assert "entry, experiment, core, line" in str(caught.value)
            assert "nothing was written" in str(caught.value)
    assert _group_events(project) == []
    assert [e for e in read_events(project, held=False) if e.target.startswith("overlay:")] == []
    # An edit of a kind the engine does not know names the kinds a person may use, topic first.
    with pytest.raises(OverlayRefusedError, match="no kind of edit: topic \\(or group\\), exclude"):
        _write(project, {"kind": "hue", "entry": built.ids["t0_01"]})


def test_rename_remove_a_member_and_withdraw_are_one_event_each(
    built: TopicProject, project: Path
) -> None:
    first, second = built.ids["t0_00"], built.ids["t0_01"]
    [made] = _write(
        project,
        {
            "kind": "topic",
            "name": "Before",
            "members": [
                {"kind": "experiment", "id": first},
                {"kind": "experiment", "id": second},
                {"kind": "core", "terms": ["keltan", "brisol"]},
            ],
        },
    )
    group_id = made.target.rsplit(":", 1)[1]
    # A rename that leaves the members out keeps them: one event.
    [renamed] = _write(project, {"kind": "topic", "id": group_id, "name": "After"})
    kept = json.loads(renamed.payload)
    assert kept["name"] == "After"
    assert kept["members"] == sorted([first, second])
    assert kept["cores"] == [["brisol", "keltan"]]
    assert len(_group_events(project)) == 2
    # Removing one member is an edit naming the others: one event.
    [removed_one] = _write(
        project,
        {
            "kind": "topic",
            "id": group_id,
            "name": "After",
            "members": [
                {"kind": "experiment", "id": first},
                {"kind": "core", "terms": ["keltan", "brisol"]},
            ],
        },
    )
    assert json.loads(removed_one.payload)["members"] == [first]
    assert len(_group_events(project)) == 3
    [topic] = topics_standing(project)
    assert [m.kind for m in topic.members] == ["experiment", "core"]
    assert topic.earlier == 2
    # Withdrawn: one event, and the topic no longer stands; the history stays.
    [withdrawn] = _write(project, {"kind": "group", "id": group_id, "remove": True})
    assert json.loads(withdrawn.payload) == {
        "overlay": "group",
        "removed": True,
        "via": "graph workspace",
    }
    assert topics_standing(project) == ()
    assert len(_group_events(project)) == 4
    # A topic nobody made cannot be edited, and the words say topic.
    with pytest.raises(OverlayRefusedError, match="no topic hgnobody was made"):
        _write(project, {"kind": "topic", "id": "hgnobody", "name": "x", "members": [first]})
    # Bringing back a withdrawn topic names everything again.
    with pytest.raises(OverlayRefusedError, match="was withdrawn"):
        _write(project, {"kind": "topic", "id": group_id, "name": "Back"})
    [back] = _write(project, {"kind": "topic", "id": group_id, "name": "Back", "members": [first]})
    assert json.loads(back.payload)["name"] == "Back"
    assert [t.name for t in topics_standing(project)] == ["Back"]


def test_a_rename_is_not_stopped_by_a_member_since_gone(built: TopicProject, project: Path) -> None:
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    live = built.ids["t0_00"]
    append_event(
        project,
        kind=RecordEventKind.ANNOTATE,
        actor=ACTOR,
        occurred_at=SAID_AT,
        target="overlay:group:hgstale",
        against_run_id=built.infer,
        payload={
            "overlay": "group",
            "name": "Old",
            "members": sorted([live, "entry_since_gone"]),
            "names": {"entry_since_gone": "scan.tif"},
            "paths": {"entry_since_gone": "old/scan.tif"},
        },
        scope=PROJECT_SCOPE,
    )
    with pytest.raises(OverlayRefusedError, match="holds no live item"):
        _write(
            project,
            {
                "kind": "topic",
                "id": "hgstale",
                "name": "Old",
                "members": [live, "entry_since_gone"],
            },
        )
    [renamed] = _write(project, {"kind": "topic", "id": "hgstale", "name": "New"})
    payload = json.loads(renamed.payload)
    assert "entry_since_gone" in payload["members"]
    assert payload["names"] == {"entry_since_gone": "scan.tif"}
    [topic] = topics_standing(project)
    assert topic.name == "New"


def test_the_overlay_reads_a_topic_with_its_members_kinds(
    built: TopicProject, project: Path
) -> None:
    from ingence.api.v1 import context_for, dispatch

    _write(
        project,
        {
            "kind": "topic",
            "name": "Stains",
            "members": [
                {"kind": "experiment", "id": built.ids["t0_00"]},
                {"kind": "core", "terms": ["droska", "miravo"]},
                {"kind": "line", "experiments": [built.ids["t2_00"]], "words": ["pellit"]},
            ],
        },
        {
            "kind": "group",
            "name": "plain pair",
            "members": [built.ids["t1_00"], built.ids["t1_01"]],
        },
    )
    context = context_for(project, settled=None, job_running=False)
    status, body = dispatch(context, "explorer/overlay", {})
    assert status == 200, body
    by_name = {item["name"]: item for item in body["statements"]}
    topic = by_name["Stains"]
    kinds = {member["kind"] for member in topic["members"]}
    assert kinds == {"experiment", "core", "line"}
    core = next(m for m in topic["members"] if m["kind"] == "core")
    assert core["name"] == "droska miravo" and core["path"] is None
    # A topic over experiments spans units by its nature: the rules have nothing to agree with.
    assert topic["agreement"] is None and topic["state"] == "projected"
    # An old group of two folders is read as it was: its agreement is asked.
    plain = by_name["plain pair"]
    assert {member["kind"] for member in plain["members"]} == {"folder"}
    assert plain["agreement"] is not None
    history = dispatch(context, "explorer/overlay", {"target": [topic["target"]]})[1]["history"]
    assert history[0]["said"] == "topic 'Stains' of 3"


def test_the_arrangement_by_a_persons_topics_is_labeled_topics() -> None:
    from ingence.api.v1.explorer.arrange import axis_named

    axis = axis_named("h.group")
    assert axis.label == "Your collections"
    assert axis.none_label == "in none of your collections"
    assert "collection" in axis.question and "group" not in axis.question


def test_an_inference_never_reads_a_topic(
    built: TopicProject, project: Path, tmp_path: Path
) -> None:
    """Experiments, a core, a line and a file in a topic, then an inference: its claims,
    assertions and findings are exactly those of the same inference over a copy holding none."""
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.storage.catalog import open_catalog

    control = tmp_path / "control"
    shutil.copytree(built.project, control)
    _write(
        project,
        {
            "kind": "topic",
            "name": "Stains",
            "members": [
                {"kind": "experiment", "id": built.ids["t0_00"]},
                {"kind": "core", "terms": ["droska", "miravo"]},
                {"kind": "line", "experiments": [built.ids["t2_00"]], "words": ["pellit"]},
                _a_file_in(built, "t1_00"),
            ],
        },
    )

    def published(where: Path) -> dict[str, list[tuple[Any, ...]]]:
        with open_catalog(where) as catalog:
            return {
                "claims": catalog.query(
                    "SELECT subject, predicate, object_reference, rule_id, rule_version, "
                    "inference_tier, resolution_status FROM claims ORDER BY ALL"
                ).fetchall(),
                "assertions": catalog.query(
                    "SELECT source_id, target_id, primitive, rule_id, rule_version, "
                    "decision_status FROM assertions ORDER BY ALL"
                ).fetchall(),
                "findings": catalog.query(
                    "SELECT rule_id, rule_version, subject, raised, inputs, reason "
                    "FROM findings ORDER BY ALL"
                ).fetchall(),
            }

    again = "run_20260101T000009Z_eeeeeeeeeeee"
    run_inference(Project.open(project), run_id=again, started_at="t")
    run_inference(Project.open(control), run_id=again, started_at="t")
    with_topic, without = published(project), published(control)
    assert all(without[table] for table in ("claims", "findings"))
    for table in ("claims", "assertions", "findings"):
        assert with_topic[table] == without[table], table


def test_no_code_that_draws_a_conclusion_reads_the_persons_layer() -> None:
    """Inference, the rulebook, the review and the themes never read an ``overlay:`` target or a
    topic: no file under them names the layer, its readers or the topics package."""
    from pathlib import Path as FilePath

    import ingence

    root = FilePath(ingence.__file__).parent
    sources = [root / "experiment_units.py"]
    for folder in ("rulebook", "infer", "themes", "completeness", "dictionary", "grouping"):
        sources += sorted((root / folder).rglob("*.py"))
    names = (
        "overlay:",
        "routes.overlay",
        "standing_layer",
        "topics_standing",
        "ingence.topics",
        "from ingence import topics",
    )
    reading = [
        source.relative_to(root).as_posix()
        for source in sources
        if any(word in source.read_text(encoding="utf-8") for word in names)
    ]
    assert reading == []


def test_a_topic_with_a_core_and_a_line_arranges_the_wheel_by_its_resolved_experiments(
    built: TopicProject, project: Path
) -> None:
    """The Wheel's arrangement by a person's topics counts the files of the experiments a core is
    followed to and a line is found as, beside those a topic names directly."""
    from ingence.api.v1 import context_for, dispatch

    carrying = {
        unit
        for unit, written in built.plant.documents.items()
        if unit in built.ids and {"droska", "miravo"} <= set(written)
    }
    named = built.ids["t0_00"]
    _write(
        project,
        {
            "kind": "topic",
            "name": "Followed",
            "members": [
                {"kind": "core", "terms": ["droska", "miravo"]},
                {"kind": "experiment", "id": named},
            ],
        },
    )
    context = context_for(project, settled=None, job_running=False)
    status, wheel = dispatch(context, "explorer/wheel", {"arrange": ["h.group"]})
    assert status == 200, wheel
    ring = {s["name"]: s["files"] for s in wheel["sectors"] if s["ring"] == 1}
    status, themes = dispatch(context, "themes", {})
    files = {row["id"]: row["files"] for row in themes["experiments"]}
    expected = sum(files[built.ids[unit]] for unit in carrying) + files[named]
    assert ring["Followed"] == expected > 0
    assert ring["in none of your collections"] == wheel["totals"]["files"] - expected


def test_a_line_s_and_a_core_s_words_may_be_phrases_but_a_core_s_hold_no_colon(
    built: TopicProject, project: Path
) -> None:
    """A line's label and a core's terms are made of the Dictionary's words, which are often
    phrases ("nuclear stain", "wild type"); a core is written on ``group:<words>``, which a colon
    would break."""
    [event] = _write(
        project,
        {
            "kind": "topic",
            "name": "Phrases",
            "members": [
                {
                    "kind": "line",
                    "experiments": [built.ids["t2_00"]],
                    "words": ["Nuclear  Stain", "wild type", "nocodazole"],
                },
                {"kind": "core", "terms": ["Wild  Type", "nocodazole"]},
            ],
        },
    )
    payload = json.loads(event.payload)
    # Casefolded, one space inside a phrase, sorted.
    [line] = payload["lines"]
    assert line["words"] == ["nocodazole", "nuclear stain", "wild type"]
    assert payload["cores"] == [["nocodazole", "wild type"]]
    for refused, match in (
        (["wild: type"], "with a colon"),
        ([""], "empty or has a space"),
    ):
        with pytest.raises(OverlayRefusedError, match=match):
            _write(
                project,
                {"kind": "topic", "name": "Core", "members": [{"kind": "core", "terms": refused}]},
            )


def test_the_wheel_says_when_a_cores_or_lines_experiments_could_not_be_resolved(
    built: TopicProject, project: Path
) -> None:
    """Not read is not none: the arrangement says so instead of counting the files under "in none
    of your topics" in silence, and resolves against the run the workspace pinned."""
    from ingence.api.v1 import context_for, dispatch
    from ingence.themes import THEME_ENGINES

    _write(
        project,
        {
            "kind": "topic",
            "name": "Followed",
            "members": [
                {"kind": "line", "experiments": [built.ids["t2_00"]], "words": ["pellit"]},
                {"kind": "experiment", "id": built.ids["t0_00"]},
            ],
        },
    )
    context = context_for(project, settled=None, job_running=False)

    def message(**query: str) -> str:
        status, wheel = dispatch(
            context,
            "explorer/wheel",
            {"arrange": ["h.group"], **{key: [value] for key, value in query.items()}},
        )
        assert status == 200, wheel
        return str(wheel["caps"]["message"])

    assert "not read" not in message()
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        said = message()
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    assert "Some of your collections were not read for this view" in said
    assert "counted under “in none of your collections”" in said
    # The run the workspace pinned: a census applied no rules, so nothing is resolved against it.
    census = message(run=built.census)
    assert "applied no rules" in census
    # A Wheel that does not arrange by topics carries no such sentence.
    status, plain = dispatch(context, "explorer/wheel", {})
    assert status == 200 and "not read" not in plain["caps"]["message"]
