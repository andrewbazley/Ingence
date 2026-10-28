"""The workspace over HTTP, the re-run through the applier (its engine stubbed), and the close.

A real re-run reads and rewrites a whole census, so where a test needs one to happen the two
engine calls the applier makes are replaced by recorders. What is tested is which steps are
asked for, in what order, and which are refused.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ingence.api import apply as apply_module
from ingence.api.server import DashboardServer
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.workspace.checkpoints import commit
from ingence.workspace.close_prompt import ASKS, ask_before_closing
from ingence.workspace.drafts import read_draft, save_draft
from ingence.workspace.state import pending_rerun

BOUNDED = "candidate_generation_is_bounded"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("workspace_routes")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "brisa_notes.txt").write_text("hello")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(
        project, run_id="run_20200101T000001Z_111111111111", started_at="2020-01-01T00:00:01Z"
    )
    run_inference(
        Project.open(project.path),
        run_id="run_20200101T000002Z_222222222222",
        started_at="2020-01-01T00:00:02Z",
    )
    return project.path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


class _Recorder:
    """Stands in for the two engine calls a re-run makes, and remembers how it was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def census(self, project: Any, **kwargs: Any) -> None:
        self.calls.append(("rebind", kwargs))

    def inference(self, project: Any, **kwargs: Any) -> None:
        self.calls.append(("inference", kwargs))


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(apply_module, "run_census", recorder.census)
    monkeypatch.setattr(apply_module, "run_inference", recorder.inference)
    # A stub does not cross a process: the job runs on a thread of the server here, through the
    # same ledger, supervisor and worker code as a job in a process of its own.
    monkeypatch.setattr(apply_module.DefinitionApplier, "default_executor", "thread")
    return recorder


def _send(server: DashboardServer, route: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        server.url.rstrip("/") + route,
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        error.close()
        return error.code, payload


def _read(server: DashboardServer, route: str) -> dict[str, Any]:
    with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
        return json.loads(response.read())


def test_a_draft_a_commit_and_the_header_state_go_over_the_wire(project: Path) -> None:
    (project / "settings.yaml").write_text("# mine\nscan:\n  workers: 3\n")
    kept = (project / "settings.yaml").read_bytes()
    with DashboardServer(project, port=0) as server:
        status, saved = _send(
            server,
            "/api/workspace/draft",
            {"kind": "settings", "values": {"dictionary": {"tokenise_names": False}}},
        )
        assert (status, saved["errors"]) == (201, {})
        header = _read(server, "/api/workspace/state")
        assert header["uncommitted"] == {"settings": 1, "rule_parameters": 0}
        assert header["pending_rerun"] is None
        preview = _read(server, "/api/workspace/commit-preview")
        assert preview["steps"] == ["rebind", "inference"]
        assert _send(server, "/api/workspace/commit", {"message": "m"})[0] == 400
        status, committed = _send(
            server, "/api/workspace/commit", {"actor": "Ana", "message": "Names off"}
        )
        assert status == 201
        assert committed["checkpoint"]["needs"] == ["rebind", "inference"]
        header = _read(server, "/api/workspace/state")
        assert header["uncommitted"] == {"settings": 0, "rule_parameters": 0}
        waiting = header["pending_rerun"]
        assert waiting["count"] == 1
        assert [line["words"] for line in waiting["summary"]] == ["1 setting"]
        assert [step["reads_the_share"] for step in waiting["steps"]] == [False, False]
    assert (project / "settings.yaml").read_bytes() == kept


def test_a_bad_draft_is_answered_with_its_error_and_kept(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, saved = _send(
            server, "/api/workspace/draft", {"kind": "settings", "values": {"scan": {"workers": 0}}}
        )
        assert status == 201
        assert "scan.workers" in saved["errors"]
        rows = {row["name"]: row for row in _read(server, "/api/workspace/settings")["settings"]}
        assert rows["scan.workers"]["error"]
        assert rows["scan.workers"]["draft"] == 0
        assert _send(server, "/api/workspace/draft", {"kind": "settings", "values": 5})[0] == 400
        assert _send(server, "/api/workspace/draft", {"kind": "moods", "values": {}})[0] == 400


def test_an_empty_save_over_the_wire_keeps_the_draft_and_a_named_value_leaves_it(
    project: Path,
) -> None:
    """The page's timer fired after Settings had gone and sent ``{}``: the draft stays whole."""
    with DashboardServer(project, port=0) as server:
        draft = "/api/workspace/draft"
        _send(server, draft, {"kind": "settings", "values": {"scan": {"workers": 5}}})
        _send(server, draft, {"kind": "settings", "values": {"depth": "deep"}})
        assert _send(server, draft, {"kind": "settings", "values": {}})[0] == 201
        assert read_draft(project, "settings").values == {"scan": {"workers": 5}, "depth": "deep"}
        status, _ = _send(
            server, draft, {"kind": "settings", "values": {}, "removed": ["scan.workers"]}
        )
        assert status == 201
        assert read_draft(project, "settings").values == {"depth": "deep"}
        assert _send(server, draft, {"kind": "settings", "removed": "depth"})[0] == 400
        assert _read(server, "/api/workspace/state")["uncommitted"]["settings"] == 1


def test_the_settings_page_says_where_each_value_came_from(project: Path) -> None:
    (project / "settings.yaml").write_text("scan:\n  workers: 3\n")
    save_draft(project, "settings", {"depth": "deep"})
    commit(project, actor="Ana", message="deeper")
    with DashboardServer(project, port=0) as server:
        rows = {row["name"]: row for row in _read(server, "/api/workspace/settings")["settings"]}
    assert (rows["scan.workers"]["value"], rows["scan.workers"]["source"]) == (3, "settings.yaml")
    assert (rows["depth"]["value"], rows["depth"]["source"]) == ("deep", "dashboard")
    assert rows["hash.mode"]["source"] == "default"
    # "In force" is an editable field: a list is typed as a comma list.
    assert rows["scan.exclude_globs"]["editable"] is True
    assert rows["scan.exclude_globs"]["kind"] == "list"
    assert rows["scan.follow_symlinks"]["kind"] == "boolean"


def test_a_rules_only_commit_waits_for_the_rules_alone_and_names_the_rule(project: Path) -> None:
    save_draft(project, "rule_parameters", {BOUNDED: {"maximum_candidates": 30}})
    commit(project, actor="Ana", message="fewer pairs")
    waiting = pending_rerun(project)
    assert waiting is not None
    assert [step.value for step in waiting.steps] == ["inference"]
    assert waiting.summary()[0]["rows"] == [f"{BOUNDED}'s settings"]


def test_the_rerun_button_reads_names_then_rules_on_the_applier(
    project: Path, engine: _Recorder
) -> None:
    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="Names off")
    with DashboardServer(project, port=0) as server:
        status, started = _send(server, "/api/workspace/rerun", {})
        assert (status, started["started"]) == (201, ["rebind", "inference"])
        assert server.applier.wait(30)
        state = _read(server, "/api/workspace/state")["applying"]
        assert sorted(state["rerun"]) == ["inference", "rebind"]
    assert [name for name, _ in engine.calls] == ["rebind", "inference"]
    assert engine.calls[0][1]["rebind"] is True
    assert "spellings" not in engine.calls[0][1]  # every name, not a partial rebind


def test_a_rules_only_rerun_reads_no_names_again(project: Path, engine: _Recorder) -> None:
    save_draft(project, "rule_parameters", {BOUNDED: {"maximum_candidates": 30}})
    commit(project, actor="Ana", message="fewer pairs")
    with DashboardServer(project, port=0) as server:
        assert _send(server, "/api/workspace/rerun", {})[1]["started"] == ["inference"]
        assert server.applier.wait(30)
    assert [name for name, _ in engine.calls] == ["inference"]


def test_a_walk_or_a_dive_is_never_started_and_the_command_is_given(
    project: Path, engine: _Recorder
) -> None:
    save_draft(project, "settings", {"extraction": {"enabled": True}})
    commit(project, actor="Ana", message="read inside files")
    with DashboardServer(project, port=0) as server:
        status, refused = _send(server, "/api/workspace/rerun", {})
        assert status == 201
        assert refused["started"] == []
        assert refused["needs_the_share"] == ["dive"]
        assert refused["commands"] == ["ingence dive", "ingence infer"]
    assert engine.calls == []


def test_nothing_waiting_starts_nothing(project: Path, engine: _Recorder) -> None:
    with DashboardServer(project, port=0) as server:
        assert _send(server, "/api/workspace/rerun", {})[1]["started"] == []
    assert engine.calls == []


def test_a_session_starts_nothing_by_itself_and_says_what_waits(
    project: Path, engine: _Recorder
) -> None:
    """A session starts nothing by itself: the line says what waits, and Re-run applies it."""
    from ingence.api import server as server_module

    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="Names off")
    said: list[str] = []
    with DashboardServer(project, port=0) as server:
        assert server_module.say_what_waits(project, say=said.append) == (
            "Waiting for Re-run: 1 setting."
        )
        assert server.applier.jobs() == []
    assert said == ["Waiting for Re-run: 1 setting."]
    assert engine.calls == []


def _bound(project: Path, term: str) -> int:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        row = catalog.query(
            "SELECT count(*) FROM dictionary_bindings WHERE term = ? AND source_scope = 'name' "
            "AND resolution_status = 'resolved'",
            [term],
        ).fetchone()
    return int(row[0])


def test_a_pack_installed_or_removed_on_its_page_waits_and_rerun_reads_names_again(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Dictionary page's install and removal wait for Re-run as a pack does (names read
    again, then the rules), and Re-run binds the pack's words."""
    from ingence.api.routes.dictionary import install_from_text, remove

    monkeypatch.setattr(apply_module.DefinitionApplier, "default_executor", "thread")
    assert pending_rerun(project) is None
    section = "section: brisa_words\nentries:\n  - {term: brisa_stain, surfaces: [brisa]}\n"
    # Installed at a moment before the Re-run below: a run begun in the same second as a change
    # is not counted as after it, and the test would wait on the clock.
    with monkeypatch.context() as moment:
        moment.setattr("ingence.record.log.now", lambda: "2020-06-01T00:00:00Z")
        name = install_from_text(project, "brisa_words.yaml", section, actor="kelt")["installed"]
    waiting = pending_rerun(project)
    assert waiting is not None
    assert [step.value for step in waiting.steps] == ["rebind", "inference"]
    assert waiting.answers == (f"words from {name} added",)
    assert _bound(project, "brisa_stain") == 0
    with DashboardServer(project, port=0) as server:
        assert _send(server, "/api/workspace/rerun", {})[1]["started"] == ["rebind", "inference"]
        assert server.applier.wait(60)
    assert _bound(project, "brisa_stain") >= 1
    assert pending_rerun(project) is None
    remove(project, name, actor="kelt")
    again = pending_rerun(project)
    assert again is not None
    assert again.answers == (f"words from {name} removed",)
    assert [step.value for step in again.steps] == ["rebind", "inference"]


def test_the_applier_refuses_a_step_that_reads_the_share(project: Path) -> None:
    with DashboardServer(project, port=0) as server, pytest.raises(ValueError, match="re-runs"):
        server.applier.submit_rerun(["dive"])


def _answers(*replies: str):
    told = iter(replies)
    return lambda _question: next(told)


def test_closing_asks_every_time_and_remembers_nothing(project: Path) -> None:
    said: list[str] = []
    assert ask_before_closing(project, actor="Ana", ask=_answers(), say=said.append) == "none"
    save_draft(project, "settings", {"scan": {"workers": 5}})
    # No: the drafts stay, and the next close asks again.
    assert ask_before_closing(project, actor="Ana", ask=_answers("n"), say=said.append) == "kept"
    assert not read_draft(project, "settings").empty
    before = sorted(p.name for p in project.rglob("*") if p.is_file())
    assert ask_before_closing(project, actor="Ana", ask=_answers("N"), say=said.append) == "kept"
    assert sorted(p.name for p in project.rglob("*") if p.is_file()) == before
    # "always" and "never" are not answers: unreadable, asked again, then the drafts stay.
    replies = _answers("always", "never", "x", "y")
    assert ask_before_closing(project, actor="Ana", ask=replies, say=said.append) == "kept"
    assert not read_draft(project, "settings").empty
    # Yes commits, under the name given.
    assert (
        ask_before_closing(project, actor="Ana", ask=_answers("y"), say=said.append) == "committed"
    )
    assert read_draft(project, "settings").empty
    assert Project.open(project).settings.scan.workers == 5
    assert ASKS == 3


def test_a_closed_terminal_leaves_the_drafts_and_a_yes_without_a_name_commits_nothing(
    project: Path,
) -> None:
    save_draft(project, "settings", {"scan": {"workers": 5}})

    def closed(_question: str) -> str:
        raise EOFError

    assert ask_before_closing(project, actor="Ana", ask=closed, say=lambda _: None) == "kept"
    assert ask_before_closing(project, actor="", ask=_answers("y"), say=lambda _: None) == "kept"
    assert not read_draft(project, "settings").empty


class _InterruptedError(Exception):
    """What this test's Ctrl-C raises: a press that got through fails the test, not the run."""


@pytest.mark.skipif(sys.platform == "win32", reason="Ctrl-C is sent as a POSIX signal here")
def test_a_second_ctrl_c_while_closing_cannot_stop_the_commit_halfway(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commit writes the settings first and its record last, and a second Ctrl-C between the
    two must not leave the change in force with no record of what it needed."""
    from ingence.workspace import close_prompt

    def interrupted(*_: object) -> None:
        raise _InterruptedError

    real = close_prompt.commit

    def pressed_again_while_committing(*args: Any, **kwargs: Any) -> Any:
        os.kill(os.getpid(), signal.SIGINT)
        return real(*args, **kwargs)

    monkeypatch.setattr(close_prompt, "commit", pressed_again_while_committing)
    save_draft(project, "settings", {"scan": {"workers": 5}})
    before = signal.signal(signal.SIGINT, interrupted)
    try:
        closed = ask_before_closing(project, actor="Ana", ask=_answers("y"), say=lambda _: None)
        # What Ctrl-C did before the commit, it does again after it.
        assert signal.getsignal(signal.SIGINT) is interrupted
    finally:
        signal.signal(signal.SIGINT, before)
    assert closed == "committed"
    assert Project.open(project).settings.scan.workers == 5
    assert read_draft(project, "settings").empty


def test_nothing_found_at_a_sessions_start_stops_the_dashboard_opening(
    project: Path, monkeypatch: pytest.MonkeyPatch, engine: _Recorder
) -> None:
    """One checkpoint file that cannot be read does not stop ``ingence see`` before the page
    opens."""
    from ingence.api import server as server_module

    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="Names off")
    folder = project / "workspace" / "checkpoints"
    (folder / "._000001.json").write_bytes(b"\x00\x05\x16\x07")
    (folder / "000002.json").write_text("{ half a fil")
    said: list[str] = []
    with DashboardServer(project, port=0) as server:
        line = server_module.say_what_waits(project, say=said.append)
        assert line == "Waiting for Re-run: 1 setting."
        assert _read(server, "/api/workspace/state")["unreadable_checkpoints"] == ["000002.json"]
        assert len(said) == 2
        assert "000002.json" in said[0]

        def broken(*_: object, **__: object) -> str:
            message = "the run's record is damaged"
            raise OSError(message)

        monkeypatch.setattr(server_module.workspace_state, "waiting_line", broken)
        said.clear()
        assert server_module.say_what_waits(project, say=said.append) is None
    assert len(said) == 2
    assert "000002.json" in said[0]
    assert said[1].startswith("Could not check what waits for Re-run")
    assert "the Dashboard opens anyway" in said[1]


def test_with_no_terminal_nothing_is_asked_and_the_drafts_stay(project: Path) -> None:
    save_draft(project, "settings", {"scan": {"workers": 5}})
    said: list[str] = []

    def never(_question: str) -> str:
        message = "nobody can answer here"
        raise AssertionError(message)

    closed = ask_before_closing(project, actor="Ana", ask=never, say=said.append, interactive=False)
    assert closed == "kept"
    assert said == [
        "1 change is saved but not committed. With no terminal to answer in, nothing was asked: "
        "it stays saved, and the next session opens with it."
    ]
    assert not read_draft(project, "settings").empty


def test_closing_stops_serving_before_it_asks(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The server stops serving before the close asks its question, so the page cannot write
    while the question waits."""
    from ingence.api import server as server_module

    save_draft(project, "settings", {"scan": {"workers": 5}})

    def ctrl_c() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server_module, "_until_ctrl_c", ctrl_c)
    monkeypatch.setattr(server_module, "_answers_can_be_typed", lambda: False)
    serving_when_asked: list[bool] = []
    asking = server_module.ask_before_closing

    def ask_and_look(where: Path, **kwargs: Any) -> str:
        address = re.search(r"Dashboard at (\S+)", capsys.readouterr().out)
        assert address is not None
        try:
            with urllib.request.urlopen(address.group(1) + "api/workspace/state", timeout=5):
                serving_when_asked.append(True)
        except OSError:
            serving_when_asked.append(False)
        return asking(where, **kwargs)

    monkeypatch.setattr(server_module, "ask_before_closing", ask_and_look)
    server_module.serve(project, port=0, open_browser=False)
    assert serving_when_asked == [False]
    assert "saved but not committed" in capsys.readouterr().out
    assert not read_draft(project, "settings").empty


@pytest.mark.skipif(sys.platform == "win32", reason="Ctrl-C is sent as a POSIX signal here")
@pytest.mark.parametrize("started", ["in the foreground", "with SIGINT ignored"])
def test_ctrl_c_with_no_terminal_closes_and_keeps_the_drafts(project: Path, started: str) -> None:
    """``ingence see`` started with its input a pipe that stays open and silent does not print a
    question that nobody can answer and then keep serving after Ctrl-C.

    A process a script starts in its background inherits SIGINT ignored. Python then installs no
    handler, and the server must still stop on Ctrl-C. Started that way here, whatever the way
    this test itself was started."""
    import ingence
    from ingence.readouts import warm

    save_draft(project, "settings", {"scan": {"workers": 5}})
    # A server warms a run with no readouts as it starts. It is warmed here first, so the child has
    # no views to build while the interrupt arrives, under any load.
    warm(project, "run_20200101T000001Z_111111111111")
    source = str(Path(ingence.__file__).resolve().parent.parent)
    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            (
                "import signal; signal.signal(signal.SIGINT, signal.SIG_IGN); "
                if "ignored" in started
                else ""
            )
            + "import sys; from pathlib import Path; from ingence.api.server import serve; "
            "serve(Path(sys.argv[1]), open_browser=False)",
            str(project),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([source, os.environ.get("PYTHONPATH", "")]),
        },
    )
    try:
        assert child.stdout is not None
        first = child.stdout.readline()
        assert first.startswith("Dashboard at "), first
        child.send_signal(signal.SIGINT)
        # Bounded: a question waiting on the silent pipe is the failure this test is for. Ninety
        # seconds, above a clean stop's own bound (the job supervisor joins its thread for up to
        # sixty): at thirty, CI's loaded macOS runner timed out a stop that was only slow.
        child.wait(timeout=90)
        said = child.stdout.read()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)
        assert child.stdin is not None
        child.stdin.close()
        if child.stdout is not None:
            child.stdout.close()
    assert "saved but not committed" in said
    assert "[y]es" not in said
    assert not read_draft(project, "settings").empty
    with pytest.raises(OSError):
        urllib.request.urlopen(first.split(" at ", 1)[1].strip(), timeout=5)


def test_a_workspace_write_that_fails_is_answered_and_never_drops_the_connection(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure other than a refusal does not drop the connection."""
    from ingence.api.routes import workspace as workspace_routes

    def failing(*_: object, **__: object) -> dict[str, Any]:
        message = "the disk is full"
        raise OSError(message)

    monkeypatch.setattr(workspace_routes, "write", failing)
    with DashboardServer(project, port=0) as server:
        status, answer = _send(server, "/api/workspace/commit", {"actor": "Ana"})
    assert status == 500
    assert "the disk is full" in answer["error"]


def test_ctrl_c_while_the_session_opens_is_a_close_like_any_other(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C pressed while the session is still opening does not escape past the question."""
    from ingence.api import server as server_module

    save_draft(project, "settings", {"scan": {"workers": 5}})

    def pressed(*_: object, **__: object) -> list[str]:
        raise KeyboardInterrupt

    monkeypatch.setattr(server_module, "say_what_waits", pressed)
    monkeypatch.setattr(server_module, "_answers_can_be_typed", lambda: False)
    try:
        server_module.serve(project, port=0, open_browser=False)
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C while the session opened escaped past the question")
    assert "saved but not committed" in capsys.readouterr().out
    assert not read_draft(project, "settings").empty


def test_the_session_start_and_the_close_say_what_happens_in_plain_words(
    project: Path, engine: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session start prints no step names. The close does not say "1 changed value is saved
    as drafts", does not ask about "today's changes" (a draft can be days old), and does not end
    in a traceback where a saved value would not be accepted."""
    from ingence.api import server as server_module

    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="Names off")
    said: list[str] = []
    server_module.say_what_waits(project, say=said.append)
    assert said == ["Waiting for Re-run: 1 setting."]
    said.clear()
    save_draft(project, "settings", {"scan": {"workers": 0}})
    questions: list[str] = []

    def yes(question: str) -> str:
        questions.append(question)
        return "y"

    assert ask_before_closing(project, actor="Ana", ask=yes, say=said.append) == "kept"
    assert said[0] == "1 change is saved but not committed."
    assert questions == ["Commit it before closing? [y]es / [n]o: "]
    assert said[1].startswith("Nothing was committed: ")
    assert "scan.workers" in said[1]
    assert not read_draft(project, "settings").empty
    for line in [*said, *questions]:
        assert "today" not in line
        assert "drafts" not in line
    # Two changes are "them", one is "it", in every line.
    save_draft(project, "settings", {"depth": "deep"})
    said.clear()
    questions.clear()
    assert ask_before_closing(project, actor="Ana", ask=_answers("n"), say=said.append) == "kept"
    assert said == [
        "2 changes are saved but not committed.",
        "They stay saved but not committed, and the next session opens with them.",
    ]
    said.clear()
    closed = ask_before_closing(project, actor="Ana", ask=yes, say=said.append, interactive=False)
    assert closed == "kept"
    assert said == [
        "2 changes are saved but not committed. With no terminal to answer in, nothing was "
        "asked: they stay saved, and the next session opens with them."
    ]
    assert questions == []


def test_a_stopping_server_stops_its_readouts_warm_between_views(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The readouts warm is stopped between two views and waited for: a server stopped
    while it builds leaves nothing building, and the process never ends inside one."""
    import threading
    import time

    import ingence.readouts as readouts

    begun = threading.Event()
    built: list[str] = []

    def slow(project_path: Path, run_id: str) -> None:
        built.append(run_id)
        begun.set()
        time.sleep(0.5)

    monkeypatch.setattr(readouts, "_STEP_BUILDERS", dict.fromkeys(readouts.STEPS, slow))
    server = DashboardServer(project, port=0, warm_search=False)
    server.start()
    try:
        assert begun.wait(60), "the warm never began"
    finally:
        server.stop()
    assert len(built) < len(readouts.STEPS)
    assert server._readouts is not None and not server._readouts.is_alive()


def test_a_ctrl_c_during_the_opening_check_is_a_close_and_never_swallowed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DuckDB reports a Ctrl-C that arrives during one of its queries as its own error. The
    opening check treats that error as the close it was, so ``ingence see`` stops instead of
    serving on after Ctrl-C. Any other error is still said in one line."""
    from ingence.api import server as server_module
    from ingence.workspace import state as workspace_state

    def interrupted(_: Path) -> str:
        raise RuntimeError("Query interrupted")

    monkeypatch.setattr(workspace_state, "waiting_line", interrupted)
    said: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        server_module.say_what_waits(project, say=said.append)
    assert said == []

    def broken(_: Path) -> str:
        raise RuntimeError("something else")

    monkeypatch.setattr(workspace_state, "waiting_line", broken)
    assert server_module.say_what_waits(project, say=said.append) is None
    assert len(said) == 1 and "something else" in said[0]
