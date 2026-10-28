"""A commit, and what waits for a re-run: the policy tables, and what counts as re-run.

Values are invented. A change waits whatever its size, and the badge on Re-run counts it.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from ingence.core.ids import run_id as make_run_id
from ingence.core.project import Project
from ingence.core.settings import Settings
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.workspace.checkpoints import commit, list_checkpoints, read_checkpoints
from ingence.workspace.drafts import read_draft, save_draft
from ingence.workspace.rerun import (
    SETTING_NEEDS,
    STEPS_A_RUN_DOES,
    Change,
    RunDone,
    Step,
    needs_of_settings,
    pending,
    still_needed,
)
from ingence.workspace.state import line_of_descent, pending_rerun, state, unreadable_line

BOUNDED = "candidate_generation_is_bounded"
RUN_BEGAN = "2031-03-01T10:00:00Z"
#: The current run, which did every step, and began before the commits the tests make.
CURRENT_RUN = (RunDone(started_at=RUN_BEGAN, steps=frozenset(Step)),)
BEFORE_THE_RUN = "2031-03-01T09:00:00+00:00"
AFTER_THE_RUN = "2031-03-02T09:00:00+00:00"
HAND = "# my own note, kept\nscan:\n  workers: 3\n"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("workspace")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "kelt_vorn_processed.csv").write_text("a,b\n3,4\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    (project.path / "settings.yaml").write_text(HAND, "utf-8")
    run_census(project, run_id="run_20310301T100000Z_111111111111", started_at=RUN_BEGAN)
    run_inference(
        Project.open(project.path),
        run_id="run_20310301T100100Z_222222222222",
        started_at="2031-03-01T10:01:00Z",
    )
    return project.path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def test_a_commit_leaves_settings_yaml_byte_identical_and_signs_the_value(project: Path) -> None:
    before = (project / "settings.yaml").read_bytes()
    save_draft(project, "settings", {"scan": {"workers": 9}})
    checkpoint = commit(project, actor="Ana", message="More threads\nplease", now=AFTER_THE_RUN)
    assert (project / "settings.yaml").read_bytes() == before
    assert Project.open(project).settings.scan.workers == 9
    written = (project / "settings.dashboard.yaml").read_text("utf-8")
    assert "# set by Ana on 2031-03-02: More threads please" in written
    assert read_draft(project, "settings").empty
    assert checkpoint.changed_settings == ("scan.workers",)
    assert checkpoint.settings["scan"]["workers"] == 9
    assert list_checkpoints(project) == [checkpoint]


def test_checkpoints_are_appended_never_rewritten(project: Path) -> None:
    save_draft(project, "settings", {"scan": {"workers": 9}})
    first = commit(project, actor="Ana", message="one", now=AFTER_THE_RUN)
    path = project / "workspace" / "checkpoints" / "000001.json"
    kept = path.read_bytes()
    save_draft(project, "settings", {"scan": {"workers": 10}})
    second = commit(project, actor="Ana", message="two", now=AFTER_THE_RUN)
    assert path.read_bytes() == kept
    assert (second.sequence, second.parent) == (2, first.checkpoint_id)


def test_a_rule_parameter_commit_appends_a_revision_and_needs_the_rules_applied(
    project: Path,
) -> None:
    save_draft(project, "rule_parameters", {BOUNDED: {"maximum_candidates": 30}})
    checkpoint = commit(project, actor="Ana", message="fewer pairs", now=AFTER_THE_RUN)
    assert (project / "rulebook" / "project-0001.yaml").is_file()
    assert checkpoint.changed_rules == (BOUNDED,)
    assert checkpoint.needs == ("inference",)
    assert checkpoint.project_revision == 1


@pytest.mark.parametrize(
    ("draft", "needs"),
    [
        ({"scan": {"workers": 9}}, ()),
        ({"scan": {"max_depth": 4}}, ("walk", "inference")),
        ({"extraction": {"enabled": True}}, ("dive", "inference")),
        ({"dictionary": {"tokenise_names": False}}, ("rebind", "inference")),
        (
            {"scan": {"max_depth": 4}, "dictionary": {"tokenise_names": False}},
            ("walk", "rebind", "inference"),
        ),
    ],
)
def test_a_commit_says_which_steps_it_needs_in_the_order_they_run(
    project: Path, draft: dict, needs: tuple[str, ...]
) -> None:
    save_draft(project, "settings", draft)
    assert commit(project, actor="Ana", message="m", now=AFTER_THE_RUN).needs == needs


def test_a_commit_refuses_what_it_cannot_apply_and_leaves_nothing_half_written(
    project: Path,
) -> None:
    with pytest.raises(ValueError, match="nothing is drafted"):
        commit(project, actor="Ana", message="m")
    save_draft(project, "settings", {"scan": {"workers": 9}})
    with pytest.raises(ValueError, match="names who"):
        commit(project, actor=" ", message="m")
    # A good setting beside a refused rule parameter: neither is applied.
    save_draft(project, "rule_parameters", {BOUNDED: {"maximum_candidates": "many"}})
    with pytest.raises(ValueError, match=BOUNDED):
        commit(project, actor="Ana", message="m")
    assert not (project / "settings.dashboard.yaml").exists()
    assert not list((project / "rulebook").glob("*.yaml"))
    assert list_checkpoints(project) == []
    assert not read_draft(project, "settings").empty


def test_a_commit_after_the_run_waits_and_one_before_it_does_not(project: Path) -> None:
    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="old", now=BEFORE_THE_RUN)
    assert pending_rerun(project) is None
    save_draft(project, "settings", {"dictionary": {"tokenise_names": True}})
    commit(project, actor="Ana", message="new", now=AFTER_THE_RUN)
    waiting = pending_rerun(project)
    assert waiting is not None
    assert [step.value for step in waiting.steps] == ["rebind", "inference"]
    assert len(waiting.commits) == 1
    header = state(project)
    assert header["uncommitted"] == {"settings": 0, "rule_parameters": 0}
    assert header["pending_rerun"]["count"] == 1


def test_every_field_of_the_settings_has_a_row_in_the_policy_table() -> None:
    """A setting added later cannot be committed without somebody saying what it needs run."""
    for name in Settings.model_fields:
        assert name in SETTING_NEEDS, f"{name} needs a row in rerun.SETTING_NEEDS"
    for key in SETTING_NEEDS:
        group, _, field = key.partition(".")
        assert group in Settings.model_fields
        if field:
            assert field in type(getattr(Settings(), group)).model_fields
    # A setting no row names is assumed to need the most, never the least.
    assert needs_of_settings(["invented.setting"]) == (Step.WALK, Step.INFERENCE)


def _waiting(count: int, needs: tuple[str, ...] = ("inference",)) -> list[Change]:
    return [
        Change(
            key=f"c{number}",
            kind="setting",
            rows=("kelt_rule's settings",),
            made=AFTER_THE_RUN,
            needs=tuple(Step(need) for need in needs),
        )
        for number in range(count)
    ]


def test_a_change_waits_whatever_its_size_and_the_line_counts_it() -> None:
    """There is no alert threshold: the badge counts what waits, one change or twelve."""
    one = pending(_waiting(1), CURRENT_RUN)
    many = pending(_waiting(12), CURRENT_RUN)
    assert one is not None
    assert many is not None
    assert (one.count, many.count) == (1, 12)
    assert one.banner() == "1 setting since the last scan."
    assert many.banner() == "12 settings since the last scan."
    for gone in ("alert", "because", "effect"):
        assert not hasattr(many, gone)


def test_re_run_asks_for_every_name_where_a_setting_changed_how_names_are_read() -> None:
    names = pending(_waiting(1, ("rebind", "inference")), CURRENT_RUN)
    rules = pending(_waiting(1), CURRENT_RUN)
    assert names is not None
    assert rules is not None
    assert names.request() == (("rebind", "inference"), ())
    assert rules.request() == (("inference",), ())
    walk = pending(_waiting(1, ("walk", "rebind", "inference")), CURRENT_RUN)
    assert walk is not None
    assert [step.reads_the_share for step in walk.steps] == [True, False, False]


def test_the_header_asks_often_so_where_nothing_waits_the_catalog_is_never_opened(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The abstention path is the hot one: the header reads this on every page load and save."""
    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="old", now=BEFORE_THE_RUN)

    def refuse(*_: object, **__: object) -> None:
        message = "the run's catalog was opened to learn that nothing waits"
        raise AssertionError(message)

    monkeypatch.setattr("ingence.storage.catalog.open_catalog", refuse)
    assert pending_rerun(project) is None
    assert state(project)["pending_rerun"] is None


# -- What counts as re-run -------------------------------------------------------------------------

MADE = "2031-03-05T12:00:00+00:00"


def _run(second: int, kind: str) -> RunDone:
    return RunDone(started_at=f"2031-03-05T12:00:{second:02d}Z", steps=STEPS_A_RUN_DOES[kind])


@pytest.mark.parametrize(
    ("needs", "runs", "left"),
    [
        # A word question reads a few names again and applies the rules: no walk, no dive.
        (
            ("walk", "dive", "inference"),
            [_run(1, "partial rebind"), _run(2, "inference")],
            ("walk", "dive", "inference"),
        ),
        # Rules applied before the dive that the commit needs do not count.
        (
            ("walk", "dive", "inference"),
            [_run(1, "census"), _run(2, "inference")],
            ("dive", "inference"),
        ),
        (
            ("walk", "dive", "inference"),
            [_run(1, "census"), _run(2, "dive"), _run(3, "inference")],
            (),
        ),
        # A dive before the walk read the files the old walk found.
        (
            ("walk", "dive", "inference"),
            [_run(1, "dive"), _run(2, "census"), _run(3, "inference")],
            ("dive", "inference"),
        ),
        # A census reads every name as it walks.
        (("rebind", "inference"), [_run(1, "census"), _run(2, "inference")], ()),
        (("rebind", "inference"), [_run(1, "rebind"), _run(2, "inference")], ()),
        (
            ("rebind", "inference"),
            [_run(1, "partial rebind"), _run(2, "inference")],
            ("rebind", "inference"),
        ),
        # A run in the same second as the commit, or before it, did nothing for it.
        (("inference",), [_run(0, "inference")], ("inference",)),
        (
            ("inference",),
            [RunDone("2031-03-05T11:59:59Z", STEPS_A_RUN_DOES["inference"])],
            ("inference",),
        ),
        (("inference",), [_run(1, "inference")], ()),
    ],
)
def test_a_step_is_done_only_by_a_run_that_did_it_after_the_commit(
    needs: tuple[str, ...], runs: list[RunDone], left: tuple[str, ...]
) -> None:
    assert tuple(step.value for step in still_needed(needs, MADE, runs)) == left


def test_a_commit_whose_time_cannot_be_read_waits_for_everything() -> None:
    assert still_needed(("rebind", "inference"), "whenever", [_run(1, "census")]) == (
        Step.REBIND,
        Step.INFERENCE,
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _next_second() -> str:
    """Now, in a second later than every stamp made before: a run stamped with it began after a
    commit made earlier, since the stamps are to the second and a tie is not after."""
    time.sleep(1.02 - time.time() % 1)
    return _now()


def _run_id(stamp: str) -> str:
    return make_run_id(started_at=stamp, nonce=os.urandom(8).hex())


@pytest.fixture(scope="module")
def made_now(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project whose runs are stamped when they run, so a commit made later falls after them."""
    base = tmp_path_factory.mktemp("workspace_now")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "zib_notes.txt").write_text("hello")
    project = Project.create(base / "project", created_at=_now(), roots=[pool])
    run_census(project, run_id=_run_id(_now()), started_at=_now())
    run_inference(Project.open(project.path), run_id=_run_id(_now()), started_at=_now())
    return project.path


@pytest.fixture
def now_project(made_now: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(made_now, target)
    return target


def _waiting_steps(project: Path) -> list[str]:
    waiting = pending_rerun(project)
    return [step.value for step in waiting.steps] if waiting is not None else []


def test_a_word_question_does_not_settle_a_commit_that_needs_a_walk_and_a_dive(
    now_project: Path,
) -> None:
    """Committing a depth and hidden files is not cleared by answering one word question, whose
    job reads a few names again and applies the rules."""
    save_draft(now_project, "settings", {"depth": "deep", "scan": {"include_hidden": True}})
    assert commit(now_project, actor="Ana", message="m").needs == ("walk", "dive", "inference")
    stamp = _next_second()
    opened = Project.open(now_project)
    run_census(opened, run_id=_run_id(stamp), started_at=stamp, rebind=True, spellings=["vorn"])
    run_inference(Project.open(now_project), run_id=_run_id(_now()), started_at=_now())
    assert _waiting_steps(now_project) == ["walk", "dive", "inference"]
    # A person walks and dives; what is left reads only the project, and then nothing is.
    run_census(Project.open(now_project), run_id=_run_id(_now()), started_at=_now())
    assert _waiting_steps(now_project) == ["dive", "inference"]
    run_deepen(Project.open(now_project), run_id=_run_id(_now()), started_at=_now())
    assert _waiting_steps(now_project) == ["inference"]
    run_inference(Project.open(now_project), run_id=_run_id(_now()), started_at=_now())
    assert pending_rerun(now_project) is None
    assert [run.steps for run in line_of_descent(now_project)][-3:] == [
        STEPS_A_RUN_DOES["census"],
        STEPS_A_RUN_DOES["dive"],
        STEPS_A_RUN_DOES["inference"],
    ]


def test_a_commit_made_while_a_rerun_read_the_names_still_waits(now_project: Path) -> None:
    """A dictionary commit made while a re-run is reading names is not marked done by that
    re-run, which had read the old value."""
    rereading_began = _now()
    commit_made = _next_second()
    save_draft(now_project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(now_project, actor="Ana", message="names off", now=commit_made)
    # The re-run under way when the commit was made publishes after it, and the rules follow it.
    opened = Project.open(now_project)
    run_census(opened, run_id=_run_id(rereading_began), started_at=rereading_began, rebind=True)
    stamp = _next_second()
    run_inference(Project.open(now_project), run_id=_run_id(stamp), started_at=stamp)
    assert _waiting_steps(now_project) == ["rebind", "inference"]


class _Killed(BaseException):
    """The process stopped: nothing after it runs, as with a kill."""


def test_a_commit_stopped_before_its_checkpoint_needs_the_same_steps_when_made_again(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the settings are in force with no checkpoint, committing again still finds the
    change and keeps the re-run it needs, rather than finding nothing changed and losing the
    re-run for good."""
    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})

    def killed(*_: object, **__: object) -> None:
        raise _Killed

    monkeypatch.setattr("ingence.workspace.checkpoints._write", killed)
    with pytest.raises(_Killed):
        commit(project, actor="Ana", message="names off", now=AFTER_THE_RUN)
    monkeypatch.undo()
    assert Project.open(project).settings.dictionary.tokenise_names is False
    assert list_checkpoints(project) == []
    again = commit(project, actor="Ana", message="names off", now=AFTER_THE_RUN)
    assert again.changed_settings == ("dictionary.tokenise_names",)
    assert again.needs == ("rebind", "inference")
    assert pending_rerun(project) is not None


def test_a_drafted_value_already_in_force_changes_nothing_and_needs_nothing(project: Path) -> None:
    # settings.yaml already says three workers, and the Dashboard already says names off.
    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="names off", now=AFTER_THE_RUN)
    save_draft(
        project, "settings", {"scan": {"workers": 3}, "dictionary": {"tokenise_names": False}}
    )
    same = commit(project, actor="Ana", message="the same again", now=AFTER_THE_RUN)
    assert (same.changed_settings, same.needs) == ((), ())


def test_a_file_that_is_not_a_checkpoint_or_cannot_be_read_is_left_out_and_named(
    project: Path,
) -> None:
    """A ``._000001.json`` a shared drive put beside a checkpoint, or a checkpoint emptied by a
    stopped write, does not stop the Dashboard opening."""
    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    first = commit(project, actor="Ana", message="one", now=AFTER_THE_RUN)
    folder = project / "workspace" / "checkpoints"
    (folder / "._000001.json").write_bytes(b"\x00\x05\x16\x07 not a checkpoint")
    (folder / "000002.json").write_text("")
    assert list_checkpoints(project) == [first]
    assert read_checkpoints(project) == ([first], ["000002.json"])
    header = state(project)
    assert header["unreadable_checkpoints"] == ["000002.json"]
    assert header["pending_rerun"] is not None
    assert "000002.json" in (unreadable_line(project) or "")
    # The next commit takes the next number no file holds, and follows the last it can read.
    save_draft(project, "settings", {"scan": {"workers": 4}})
    third = commit(project, actor="Ana", message="three", now=AFTER_THE_RUN)
    assert (third.sequence, third.parent) == (3, first.checkpoint_id)
    assert (folder / "000003.json").is_file()


def test_a_draft_saved_while_a_commit_runs_is_kept(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save made while a commit runs is not lost: the commit does not clear the draft that the
    save wrote after the commit began."""
    from ingence.workspace import checkpoints as checkpoints_module

    save_draft(project, "settings", {"scan": {"workers": 6}})
    writing = threading.Event()
    real = checkpoints_module.write_dashboard_settings

    def slow(*args: object, **kwargs: object) -> object:
        written = real(*args, **kwargs)  # type: ignore[arg-type]
        writing.set()
        # Long enough for a save that did not wait for the commit to land inside it.
        time.sleep(0.4)
        return written

    monkeypatch.setattr(checkpoints_module, "write_dashboard_settings", slow)
    committing = threading.Thread(
        target=commit, args=(project,), kwargs={"actor": "Ana", "message": "m"}
    )
    committing.start()
    assert writing.wait(30)
    save_draft(project, "settings", {"depth": "deep"})
    committing.join(30)
    assert not committing.is_alive()
    assert Project.open(project).settings.scan.workers == 6
    assert read_draft(project, "settings").values == {"depth": "deep"}


def test_two_commits_at_once_make_one_checkpoint_and_one_plain_refusal(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two commits cannot take one checkpoint number."""
    from ingence.workspace import checkpoints as checkpoints_module

    save_draft(project, "settings", {"scan": {"workers": 6}})
    real = checkpoints_module._state

    def slow(where: Path) -> dict[str, object]:
        # Long enough for both commits to read the drafts before either writes, were they not
        # to take turns.
        time.sleep(0.3)
        return real(where)

    monkeypatch.setattr(checkpoints_module, "_state", slow)
    outcomes: list[str] = []

    def one() -> None:
        try:
            commit(project, actor="Ana", message="m")
        except ValueError as error:
            outcomes.append(f"refused: {error}")
        except Exception as error:  # any other failure is what this test is for
            outcomes.append(f"failed: {type(error).__name__}: {error}")
        else:
            outcomes.append("committed")

    both = [threading.Thread(target=one) for _ in range(2)]
    for thread in both:
        thread.start()
    for thread in both:
        thread.join(30)
    assert sorted(outcomes) == [
        "committed",
        "refused: nothing is drafted, so there is nothing to commit",
    ]
    assert [checkpoint.sequence for checkpoint in list_checkpoints(project)] == [1]
