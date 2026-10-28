"""Withdraw and restore (schema 1.22): going back to an earlier run deletes nothing.

A restore withdraws the decisions made since the run it returns to, in one ``withdraw`` event. A
later restore withdraws that withdrawal, which brings back exactly what it withdrew. A restore, then
a later restore, brings every decision back, and the Record only grew. Only a person goes back, only
to the exact preview they confirmed, and only this one module builds a withdrawal. Every name is
invented.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import (
    AppendError,
    append_event,
    in_force,
    read_events,
    sequences_present,
    withdrawn_ids,
)
from ingence.scan.census import run_census
from ingence.storage.commit import read_pointer
from ingence.workspace.restore import RestoreRefusedError, restore, restore_plan

SOURCE = Path(__file__).resolve().parent.parent / "src" / "ingence"
R0 = "run_20260101T000001Z_111111111111"
R1 = "run_20260101T000002Z_222222222222"
R2 = "run_20260101T000004Z_444444444444"
R3 = "run_20260101T000006Z_666666666666"


def _define(path: Path, word: str, at: str, against: str) -> str:
    return append_event(
        path,
        kind=RecordEventKind.DEFINE,
        actor="a person",
        occurred_at=at,
        target=f"term:{word}",
        against_run_id=against,
        payload={"answer": f"{word}_reagent", "theme_kind": "reagent", "raised": word},
    ).event_id


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Three inference runs with a decision between each: R1, d1, R2, d2, R3."""
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n")
    (pool / "kelt" / "brisa_trell.txt").write_text("x")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id=R0, started_at="2026-01-01T00:00:01Z")
    run_inference(made, run_id=R1, started_at="2026-01-01T00:00:02Z")
    _define(made.path, "zib", "2026-01-01T00:00:03Z", R1)
    run_inference(made, run_id=R2, started_at="2026-01-01T00:00:04Z")
    _define(made.path, "brisa", "2026-01-01T00:00:05Z", R2)
    run_inference(made, run_id=R3, started_at="2026-01-01T00:00:06Z")
    return made.path


def _targets(path: Path) -> set[str]:
    return {event.target for event in read_events(path, held=False)}


def test_a_restore_then_a_later_restore_brings_every_decision_back(project: Path) -> None:
    everything_before = read_events(project, held=False, withdrawn=True)
    assert _targets(project) == {"term:zib", "term:brisa"}

    back = restore_plan(project, R1)
    # A run records a watermark when a durable job makes it. Without one, the decisions made before
    # the run began stand in for it. Either gives the same decisions here.
    assert back.watermark_from in (
        "the run's own record",
        "the decisions made before the run began",
    )
    assert back.set_aside == {"define": 2}
    restore(project, run_id=R1, actor="a person", previewed=back.digest, now="2026-01-01T00:00:07Z")
    assert read_pointer(project).run_id == R1
    assert _targets(project) == set()

    forward = restore_plan(project, R3)
    assert forward.brought_back == {"define": 2}
    restore(
        project, run_id=R3, actor="a person", previewed=forward.digest, now="2026-01-01T00:00:08Z"
    )
    assert read_pointer(project).run_id == R3
    assert _targets(project) == {"term:zib", "term:brisa"}

    # The Record only grew: every earlier event is still there, byte for byte, and two were added.
    everything_after = read_events(project, held=False, withdrawn=True)
    assert everything_after[: len(everything_before)] == everything_before
    assert [event.kind for event in everything_after[len(everything_before) :]] == [
        RecordEventKind.WITHDRAW,
        RecordEventKind.WITHDRAW,
    ]
    assert sequences_present(project) == tuple(range(1, len(everything_after) + 1))


def test_a_restore_to_the_middle_sets_aside_only_what_came_after(project: Path) -> None:
    plan = restore_plan(project, R2)
    assert plan.set_aside == {"define": 1}
    restore(project, run_id=R2, actor="a person", previewed=plan.digest, now="2026-01-01T00:00:07Z")
    assert _targets(project) == {"term:zib"}


def test_only_a_person_goes_back(project: Path) -> None:
    plan = restore_plan(project, R1)
    for engine_name in ("SYSTEM", "Ingence", " engine "):
        with pytest.raises(RestoreRefusedError, match="not a person"):
            restore(project, run_id=R1, actor=engine_name, previewed=plan.digest)
    assert read_pointer(project).run_id == R3


def test_a_preview_that_no_longer_holds_is_not_restored(project: Path) -> None:
    plan = restore_plan(project, R1)
    _define(project, "trell", "2026-01-01T00:00:07Z", R3)
    with pytest.raises(RestoreRefusedError, match="look at the preview again"):
        restore(project, run_id=R1, actor="a person", previewed=plan.digest)
    assert _targets(project) == {"term:zib", "term:brisa", "term:trell"}


def test_the_generic_writer_is_no_back_door(project: Path) -> None:
    first = read_events(project, held=False)[0].event_id
    for payload in (
        {"withdrawn": [first]},
        {"withdrawn": [first], "restored_to": R1},
        {"withdrawn": [], "restored_to": R1, "previewed": "x"},
        {"withdrawn": ["rec_nothing"], "restored_to": R1, "previewed": "x"},
    ):
        with pytest.raises(AppendError):
            append_event(
                project,
                kind=RecordEventKind.WITHDRAW,
                actor="a person",
                occurred_at="2026-01-01T00:00:09Z",
                target=f"restore:{R1}",
                against_run_id=R3,
                payload=payload,
            )


def test_withdrawing_a_withdrawal_brings_back_what_it_withdrew(project: Path) -> None:
    events = read_events(project, held=False)
    plan = restore_plan(project, R1)
    restore(project, run_id=R1, actor="a person", previewed=plan.digest, now="2026-01-01T00:00:07Z")
    everything = read_events(project, held=False, withdrawn=True)
    assert withdrawn_ids(everything) == {event.event_id for event in events}
    assert in_force(everything) == ()
    # The History page reads everything; replay reads what is in force.
    assert len(read_events(project, held=False, withdrawn=True)) == len(events) + 1
    assert read_events(project) == ()


def test_only_the_restore_module_and_the_one_answer_module_build_a_withdrawal() -> None:
    """There are two ways to withdraw, and only two.

    Going back to an earlier run is one. Withdrawing one answer carried in is the other, in
    ``workspace/withdraw.py``, checked again by the Record's one writer
    (``tests/test_withdraw_answer.py``).
    """
    builders = []
    for path in sorted(SOURCE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(
            r"kind\s*=\s*RecordEventKind\.WITHDRAW|RecordEventKind\(\s*[\"']withdraw", text
        ):
            builders.append(path.relative_to(SOURCE).as_posix())
    assert builders == ["workspace/restore.py", "workspace/withdraw.py"]


def test_the_page_previews_then_restores_through_the_guarded_route(project: Path) -> None:
    import json
    import urllib.request

    from ingence.api.server import WRITE_HEADER, DashboardServer

    server = DashboardServer(project, port=0)
    server.start()
    try:
        base = server.url.rstrip("/")
        with urllib.request.urlopen(f"{base}/api/v1/restore/preview?run={R1}") as response:
            shown = json.loads(response.read())
        assert shown["set_aside"] == {"define": 2}
        body = json.dumps({"run": R1, "actor": "a person", "previewed": shown["digest"]}).encode()
        request = urllib.request.Request(
            f"{base}/api/workspace/restore",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", WRITE_HEADER: "1"},
        )
        with urllib.request.urlopen(request) as response:
            done = json.loads(response.read())
        assert done["restored_to"] == R1
        assert done["withdrawn"] == 2
    finally:
        server.stop()
    assert read_pointer(project).run_id == R1


def test_a_restore_takes_the_settings_from_before_the_run_and_keeps_notes(tmp_path: Path) -> None:
    """A setting committed while the first run was current, then the second.

    Restoring the first run puts the setting back (the checkpoint before that run began, not the one
    naming it), withdraws the decision made after it, and keeps a note written after it, since a
    note decides nothing.
    """
    from ingence.workspace.checkpoints import commit
    from ingence.workspace.drafts import save_draft
    from ingence.workspace.settings_overlay import read_layers

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id=R0, started_at="2026-01-01T00:00:01Z")
    run_inference(made, run_id=R1, started_at="2026-01-01T00:00:02Z")
    save_draft(made.path, "settings", {"scan": {"workers": 3}})
    commit(made.path, actor="a person", message="three workers", now="2026-01-01T00:00:03Z")
    _define(made.path, "zib", "2026-01-01T00:00:03Z", R1)
    append_event(
        made.path,
        kind=RecordEventKind.NOTE,
        actor="a person",
        occurred_at="2026-01-01T00:00:03Z",
        target="term:zib",
        against_run_id=R1,
        payload={"text": "an invented note"},
    )
    run_inference(made, run_id=R2, started_at="2026-01-01T00:00:04Z")
    plan = restore_plan(made.path, R1)
    assert plan.settings == {"scan.workers": [3, 8]}
    assert plan.set_aside == {"define": 1}
    restore(
        made.path, run_id=R1, actor="a person", previewed=plan.digest, now="2026-01-01T00:00:05Z"
    )
    assert read_layers(made.path).dashboard == {}
    assert read_pointer(made.path).run_id == R1
    kinds = [event.kind for event in read_events(made.path, held=False)]
    assert kinds == [RecordEventKind.NOTE]


BOUNDED = "candidate_generation_is_bounded"


def _maximum(path: Path) -> object:
    from ingence.rulebook import project_rulebook
    from ingence.rulebook.render import value_in_force

    rule = project_rulebook(path).by_id(BOUNDED)
    assert rule is not None
    spec = next(item for item in rule.parameters if item.name == "maximum_candidates")
    return value_in_force(spec)


def test_a_restore_reverts_the_rule_parameters_committed_since_and_deletes_no_revision(
    tmp_path: Path,
) -> None:
    """Restoring a run returns the rule parameters committed since, as an appended project
    revision.

    Restoring the later run brings them back the same way, and no revision file is ever removed or
    rewritten.
    """
    from ingence.storage.layout import RULEBOOK_DIR
    from ingence.workspace.checkpoints import commit
    from ingence.workspace.drafts import save_draft

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id=R0, started_at="2026-01-01T00:00:01Z")
    run_inference(made, run_id=R1, started_at="2026-01-01T00:00:02Z")
    was = _maximum(made.path)
    tuned = 7 if was != 7 else 9
    save_draft(made.path, "rule_parameters", {BOUNDED: {"maximum_candidates": tuned}})
    commit(made.path, actor="a person", message="fewer candidates", now="2026-01-01T00:00:03Z")
    run_inference(made, run_id=R2, started_at="2026-01-01T00:00:04Z")
    assert _maximum(made.path) == tuned
    revisions = sorted((made.path / RULEBOOK_DIR).glob("*.yaml"))
    written = {path.name: path.read_bytes() for path in revisions}

    back = restore_plan(made.path, R1)
    assert back.rules_reverted == {BOUNDED: {"maximum_candidates": [tuned, was]}}
    assert back.rules_kept == ()
    restore(
        made.path, run_id=R1, actor="a person", previewed=back.digest, now="2026-01-01T00:00:05Z"
    )
    assert _maximum(made.path) == was
    after = sorted((made.path / RULEBOOK_DIR).glob("*.yaml"))
    assert len(after) == len(revisions) + 1
    assert all(
        (made.path / RULEBOOK_DIR / name).read_bytes() == body for name, body in written.items()
    )

    forward = restore_plan(made.path, R2)
    assert forward.rules_reverted == {BOUNDED: {"maximum_candidates": [was, tuned]}}
    restore(
        made.path, run_id=R2, actor="a person", previewed=forward.digest, now="2026-01-01T00:00:06Z"
    )
    assert _maximum(made.path) == tuned


def test_a_restore_whose_parameters_already_hold_writes_no_revision(tmp_path: Path) -> None:
    from ingence.storage.layout import RULEBOOK_DIR
    from ingence.workspace.checkpoints import commit
    from ingence.workspace.drafts import save_draft

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id=R0, started_at="2026-01-01T00:00:01Z")
    run_inference(made, run_id=R1, started_at="2026-01-01T00:00:02Z")
    was = _maximum(made.path)
    save_draft(made.path, "rule_parameters", {BOUNDED: {"maximum_candidates": was}})
    commit(made.path, actor="a person", message="the same", now="2026-01-01T00:00:03Z")
    _define(made.path, "zib", "2026-01-01T00:00:03Z", R1)
    run_inference(made, run_id=R2, started_at="2026-01-01T00:00:04Z")
    plan = restore_plan(made.path, R1)
    assert plan.rules_reverted == {}
    before = sorted((made.path / RULEBOOK_DIR).glob("*.yaml"))
    restore(
        made.path, run_id=R1, actor="a person", previewed=plan.digest, now="2026-01-01T00:00:05Z"
    )
    assert sorted((made.path / RULEBOOK_DIR).glob("*.yaml")) == before


def test_a_restore_returns_the_parameters_the_run_used_not_the_checkpoints(
    tmp_path: Path,
) -> None:
    """A revision written by hand after a commit and before the run.

    The run used it, so restoring the run returns its value, not the checkpoint's, and never undoes
    it.
    """
    import yaml

    from ingence.storage.layout import RULEBOOK_DIR
    from ingence.workspace.checkpoints import commit
    from ingence.workspace.drafts import save_draft

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id=R0, started_at="2026-01-01T00:00:01Z")
    save_draft(made.path, "rule_parameters", {BOUNDED: {"maximum_candidates": 5}})
    commit(made.path, actor="a person", message="five", now="2026-01-01T00:00:01Z")
    folder = made.path / RULEBOOK_DIR
    number = max(int(path.stem.split("-")[1]) for path in folder.glob("project-*.yaml")) + 1
    (folder / f"project-{number:04d}.yaml").write_text(
        yaml.safe_dump(
            {
                "project_revision": number,
                "summary": "by hand",
                "changes": [{"override": BOUNDED, "parameters": {"maximum_candidates": 6}}],
            },
            sort_keys=False,
        )
    )
    run_inference(made, run_id=R1, started_at="2026-01-01T00:00:02Z")
    assert _maximum(made.path) == 6
    save_draft(made.path, "rule_parameters", {BOUNDED: {"maximum_candidates": 9}})
    commit(made.path, actor="a person", message="nine", now="2026-01-01T00:00:03Z")
    run_inference(made, run_id=R2, started_at="2026-01-01T00:00:04Z")

    plan = restore_plan(made.path, R1)
    assert plan.rules_reverted == {BOUNDED: {"maximum_candidates": [9, 6]}}
    assert plan.rules_kept == ()


def test_a_run_whose_rulebook_no_revision_rebuilds_changes_no_parameter(tmp_path: Path) -> None:
    """Where no prefix of the project's revisions gives the Rulebook the run recorded (its files
    edited since, or another engine's shipped rules), what the run used cannot be said: the rules
    that differ are named and nothing is returned."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ingence.storage.commit import run_path
    from ingence.workspace.checkpoints import commit
    from ingence.workspace.drafts import save_draft

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id=R0, started_at="2026-01-01T00:00:01Z")
    run_inference(made, run_id=R1, started_at="2026-01-01T00:00:02Z")
    save_draft(made.path, "rule_parameters", {BOUNDED: {"maximum_candidates": 7}})
    commit(made.path, actor="a person", message="seven", now="2026-01-01T00:00:03Z")
    run_inference(made, run_id=R2, started_at="2026-01-01T00:00:04Z")
    # R1's record, as though another engine had written it: a Rulebook nothing here rebuilds.
    for fragment in sorted((run_path(made.path, R1) / "run").glob("*.parquet")):
        table = pq.read_table(fragment)
        index = table.column_names.index("rulebook_hash")
        table = table.set_column(
            index, "rulebook_hash", pa.array(["rulebook_elsewhere"] * table.num_rows)
        )
        pq.write_table(table, fragment)
    plan = restore_plan(made.path, R1)
    assert plan.rules_reverted == {}
    assert plan.rules_kept == (BOUNDED,)
