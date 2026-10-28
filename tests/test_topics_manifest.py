"""A run's manifest says what a person's topics did, and names a topic's edits as edits.

Two inferences over the themes' invented pool, with a topic made, renamed and changed between them,
and a new acquisition in one experiment that carries a core's words. Held here:

- the manifest names which experiments joined (or left) each topic with the reason, in a sentence
  with its count and what it is out of;
- a topic's edits are said as edits ("You renamed ...") and are not among the answers that took
  effect: a topic decides nothing, and no run reads it;
- where a person made no topic nothing is said of topics; where the reader is not available or
  fails, the manifest says topics were not computed and why, only if there are topics;
- the reader is reached through its registry, and the response is its declared model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes.manifest import TOPIC_READERS, manifest
from ingence.api.routes.overlay import prepare_overlay, write_prepared

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.topic_project import TopicProject

pytest.importorskip("sklearn", reason="the fixture project draws themes")

ACTOR = "a. person"


def _write(project: Path, at: str, *edits: dict[str, Any]) -> list[Any]:
    prepared = prepare_overlay(project, actor=ACTOR, occurred_at=at, edits=list(edits))
    return write_prepared(project, prepared, actor=ACTOR, occurred_at=at)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[TopicProject, str, str]:
    """The project with its second run: (project, first run, second run)."""
    from fixtures.topic_project import build_topic_project, rerun

    made = build_topic_project(tmp_path_factory.mktemp("manifest_topics"))
    stained = made.ids["t3_00"]
    [first] = _write(
        made.project,
        "2026-10-10T09:00:00Z",
        {
            "kind": "topic",
            "name": "Stains",
            "members": [{"kind": "core", "terms": ["droska", "miravo"]}],
        },
    )
    group_id = first.target.rsplit(":", 1)[1]
    _write(made.project, "2026-10-10T09:05:00Z", {"kind": "topic", "id": group_id, "name": "Dyes"})
    _write(
        made.project,
        "2026-10-10T09:10:00Z",
        {
            "kind": "topic",
            "id": group_id,
            "name": "Dyes",
            "members": [
                {"kind": "core", "terms": ["droska", "miravo"]},
                {"kind": "experiment", "id": made.ids["t0_00"]},
            ],
            "note": "for the spring",
        },
    )
    [other] = _write(
        made.project,
        "2026-10-10T09:15:00Z",
        {
            "kind": "topic",
            "name": "Short lived",
            "members": [{"kind": "experiment", "id": made.ids["t1_00"]}],
        },
    )
    _write(
        made.project,
        "2026-10-10T09:20:00Z",
        {"kind": "topic", "id": other.target.rsplit(":", 1)[1], "remove": True},
    )
    second = rerun(made, {"t3_00": ["droska_miravo_extra_01.nd2"]})
    assert stained in made.ids.values()
    return made, made.infer, second


def test_the_manifest_says_which_experiments_joined_a_topic_and_why(built: Any) -> None:
    made, first, second = built
    found = manifest(made.project, second, viewer=ACTOR)
    assert found["compared_run_id"] == first
    section = found["topics"]
    assert section["computed"] is True and section["why_not"] is None
    [row] = [r for r in section["topics"] if r["name"] == "Dyes"]
    assert row["compared"] is True and row["complete"] is True
    assert [(c["experiment"], c["folder"], c["reasons"]) for c in row["joined"]] == [
        (made.ids["t3_00"], made.folders["t3_00"], ["now carries the words droska, miravo"])
    ]
    assert row["left"] == [] and row["unread"] == []
    sentence = "1 experiment joined “Dyes”: it now carries the words droska, miravo."
    assert row["said"] == [sentence]
    assert sentence in found["said"]
    # The count is a count of the experiments the topic now holds, out of the run's.
    assert row["experiments"] == len(_carrying(made, "droska", "miravo")) + 1 + 1


def _carrying(made: Any, *words: str) -> set[str]:
    return {
        made.ids[unit]
        for unit, written in made.plant.documents.items()
        if unit in made.ids and set(words) <= set(written)
    }


def test_a_topics_edits_are_said_as_edits_and_are_not_answers(built: Any) -> None:
    made, _, second = built
    found = manifest(made.project, second, viewer=ACTOR)
    # None of the events of the layer is an answer that took effect.
    assert found["decisions"] == []
    assert [e["what"] for e in found["topic_edits"]] == [
        "made",
        "changed",
        "changed",
        "made",
        "withdrew",
    ]
    said = [e["said"] for e in found["topic_edits"]]
    assert said == [
        "You made the topic “Stains” with 1 member.",
        "You changed the topic “Dyes”: renamed from “Stains”.",
        "You changed the topic “Dyes”: 1 member added, its note changed.",
        "You made the topic “Short lived” with 1 member.",
        "You withdrew the topic “Short lived”.",
    ]
    assert all(sentence in found["said"] for sentence in said)
    # Someone else's edit is named by who made it, not as "You".
    other = manifest(made.project, second, viewer="someone else")
    assert other["topic_edits"][0]["said"].startswith(f"{ACTOR} made the topic")
    # Nothing in the manifest calls an edit an answer.
    assert not any("answer" in sentence and "topic" in sentence for sentence in found["said"])
    # A topic withdrawn between the two runs has left nothing to say of drift.
    assert [r["name"] for r in found["topics"]["topics"]] == ["Dyes"]


def test_a_run_before_any_topic_says_nothing_of_topics(built: Any) -> None:
    made, first, _ = built
    found = manifest(made.project, first, viewer=ACTOR)
    assert found["topic_edits"] == []
    assert found["topics"] == {"computed": True, "why_not": None, "topics": [], "said": []}
    assert not any("topic" in sentence.lower() for sentence in found["said"])


def test_where_the_reader_fails_or_is_off_topics_are_said_not_computed(built: Any) -> None:
    made, _, second = built

    def failing(project: Path, run: str, compared: str | None) -> list[dict[str, Any]]:
        message = "the runs could not be read"
        raise RuntimeError(message)

    found = manifest(made.project, second, viewer=ACTOR, topics=failing)
    assert found["topics"]["computed"] is False
    assert "the runs could not be read" in found["topics"]["why_not"]
    assert "Topics: topics not computed." in found["said"]
    # No reader asked for.
    none = manifest(made.project, second, viewer=ACTOR, topics=None)
    assert none["topics"]["computed"] is False
    assert none["topics"]["why_not"] == "no topic reader was asked for"
    # The registry: turned off, the manifest says so and does not fail.
    assert "topics" in TOPIC_READERS.names()
    TOPIC_READERS.set_enabled("topics", enabled=False)
    try:
        off = manifest(made.project, second, viewer=ACTOR)
    finally:
        TOPIC_READERS.set_enabled("topics", enabled=True)
    assert off["topics"]["computed"] is False and off["topics"]["why_not"]


def test_the_manifest_route_answers_its_declared_model_with_topics(built: Any) -> None:
    from ingence.api.v1 import context_for, dispatch
    from ingence.api.v1.manifest import ManifestResponse

    made, _, second = built
    context = context_for(made.project, settled=None, job_running=False)
    status, body = dispatch(context, "manifest", {"run": [second], "viewer": [ACTOR]})
    assert status == 200, body
    parsed = ManifestResponse.model_validate(body)
    assert [edit.what for edit in parsed.topic_edits][:2] == ["made", "changed"]
    assert parsed.topics.computed is True
    assert [topic.name for topic in parsed.topics.topics] == ["Dyes"]
    assert parsed.topics.topics[0].joined[0].reasons == ["now carries the words droska, miravo"]


def test_the_manifest_reads_the_topics_the_run_was_made_with_not_todays(
    built: Any, tmp_path: Path
) -> None:
    """A topic withdrawn after the run is part of what the run did: its drift is still said."""
    import shutil

    from ingence.api.routes.overlay import topics_standing

    made, _, second = built
    copy = tmp_path / "project"
    shutil.copytree(made.project, copy)
    [dyes] = [t for t in topics_standing(copy) if t.name == "Dyes"]
    _write(copy, "2026-10-12T09:00:00Z", {"kind": "topic", "id": dyes.topic_id, "remove": True})
    assert topics_standing(copy) == ()
    found = manifest(copy, second, viewer=ACTOR)
    [row] = found["topics"]["topics"]
    assert row["name"] == "Dyes" and len(row["joined"]) == 1
    assert "1 experiment joined “Dyes”" in found["topics"]["said"][0]


def test_a_run_that_states_no_watermark_cannot_say_what_its_topics_were(
    built: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.api.routes.manifest import _topics
    from ingence.topics import NoWatermarkError, topic_drift

    made, _, second = built

    def never(project: Path, run: str, compared: str | None) -> list[dict[str, Any]]:
        raise AssertionError("a reader of topics was asked about a run with no watermark")

    said = _topics(made.project, second, None, never, None, None)
    assert said["computed"] is False
    assert "no Record watermark" in said["why_not"]
    assert said["said"] == ["Topics: topics not computed."]
    # Where the Record holds no topic at all there is nothing to say, watermark or not.
    nothing = tmp_empty_project_topics()
    assert nothing["computed"] is True and nothing["said"] == []

    class Stated:
        def __init__(self, row: Any) -> None:
            self.row = row

        def __enter__(self) -> Stated:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def query(self, *_sql: Any) -> Stated:
            return self

        def fetchone(self) -> Any:
            return self.row

    monkeypatch.setattr("ingence.storage.catalog.open_catalog", lambda *a, **k: Stated(None))
    with pytest.raises(NoWatermarkError, match="no Record watermark"):
        topic_drift(made.project, second, None)
    monkeypatch.setattr("ingence.storage.catalog.open_catalog", lambda *a, **k: Stated((None,)))
    with pytest.raises(NoWatermarkError, match="topics it was made with are not known"):
        topic_drift(made.project, second, None)


def tmp_empty_project_topics() -> dict[str, Any]:
    """The manifest's topics part for a Record that holds no topic, a run stating no watermark."""
    import tempfile
    from pathlib import Path as FilePath

    from ingence.api.routes.manifest import _topics
    from ingence.core.project import Project

    with tempfile.TemporaryDirectory() as folder:
        pool = FilePath(folder) / "pool"
        pool.mkdir()
        (pool / "a.txt").write_text("x")
        project = Project.create(FilePath(folder) / "project", created_at="t", roots=[pool])
        return _topics(project.path, "run_x", None, None, None, None)


def test_the_changes_timeline_counts_topic_edits_apart_from_answers(built: Any) -> None:
    """The run's Changes entry and its manifest agree on how many answers it had."""
    from ingence.api.routes import changes as change_routes

    made, _, second = built
    found = change_routes.timeline(made.project)
    landings = [run["took_effect"] for run in found["runs"]] + [found["waiting"]]
    assert sum(landing["edited"] for landing in landings) == 5
    assert sum(landing["decided"] for landing in landings) == 0
    assert all(landing["answers"] == [] and landing["by_actor"] == {} for landing in landings)
    assert all(run["made_here"] == 0 for run in found["runs"])
    # The manifest says the same of the same run: no answer took effect in it.
    assert manifest(made.project, second, viewer=ACTOR)["decisions"] == []


def test_the_comparison_of_two_runs_says_topic_edits_apart_from_answers(
    built: Any, tmp_path: Path
) -> None:
    import shutil

    from ingence.api.routes import changes as change_routes

    made, first, second = built
    copy = tmp_path / "project"
    shutil.copytree(made.project, copy)
    # Between the two inferences (their identifiers lead with their start): an edit and a note.
    runs = {run["run_id"]: run for run in change_routes.runs(copy)["runs"]}
    assert runs[first]["began"] and runs[second]["began"]
    _write(
        copy,
        "2026-01-01T00:00:03Z",
        {
            "kind": "topic",
            "name": "Between",
            "members": [{"kind": "experiment", "id": made.ids["t0_01"]}],
        },
    )
    compared = change_routes.changes(copy, before=first, after=second)
    assert compared["answers"] == {"decided": 0, "noted": 0, "edited": 1}
    assert "1 edit of topics" in " ".join(compared["said"])
    assert "Nothing was written to the Record between the two runs." not in compared["said"]
