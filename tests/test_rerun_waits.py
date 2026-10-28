"""Every change waits for Re-run, on one list, and nothing starts by itself.

A run never starts without alerting the user. Opening a project whose changes no run has followed
says so and offers a re-run now or later. Rebinds, and other choices the user makes while using
the dashboard, are put on one list. They are not applied until the user starts a rescan or
rebind. A word's definition takes effect "After the re-run". A badge on Re-run and a summary by
kind show the list.

Where a test needs a re-run to happen, the two engine calls the applier makes are replaced by
recorders, and what is tested is what was asked for. Every name, and every person, is invented.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ingence.api import apply as apply_module
from ingence.api.routes.dictionary import inactivate
from ingence.api.server import WRITE_HEADER, DashboardServer
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.jobs.ledger import JobLedger
from ingence.record.log import append_event
from ingence.record.questions import question_target, term_target
from ingence.record.targets import group_target, pack_target, sense_target, surface_target
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.workspace.checkpoints import commit
from ingence.workspace.drafts import save_draft
from ingence.workspace.rerun import STEPS_A_RUN_DOES, RunDone, still_needed
from ingence.workspace.state import pending_rerun, state, waiting_line

CENSUS = "run_20200101T000001Z_111111111111"
INFERENCE = "run_20200101T000002Z_222222222222"
#: A moment after both runs and before any run a test starts now.
MOMENT = "2026-01-01T00:00:30Z"
PERSON = "ines"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("rerun_waits")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn3q_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "brisa_plate_notes.txt").write_text("hello")
    (pool / "tamen").mkdir()
    (pool / "tamen" / "zeph_vorn3q_01.csv").write_text("a,b\n3,4\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="2020-01-01T00:00:01Z")
    run_inference(Project.open(project.path), run_id=INFERENCE, started_at="2020-01-01T00:00:02Z")
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
    monkeypatch.setattr(apply_module.DefinitionApplier, "default_executor", "thread")
    return recorder


def _event(project: Path, kind: RecordEventKind, target: str, **payload: Any) -> None:
    append_event(
        project,
        kind=kind,
        actor=PERSON,
        occurred_at=MOMENT,
        target=target,
        against_run_id=INFERENCE,
        payload=payload or None,
    )


def _post(server: DashboardServer, route: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        server.url.rstrip("/") + route,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", WRITE_HEADER: "1"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def _get(server: DashboardServer, route: str) -> dict[str, Any]:
    with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
        return json.loads(response.read())


def _summary(project: Path) -> list[tuple[str, int, str]]:
    waiting = pending_rerun(project)
    assert waiting is not None
    return [(line["kind"], line["count"], line["words"]) for line in waiting.summary()]


# -- No run starts by itself when the Dashboard starts ---------------------------------------------


def test_opening_a_project_with_changes_waiting_starts_nothing_and_says_so_in_one_line(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ingence.api import server as server_module

    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="Names off", now=MOMENT)
    _event(project, RecordEventKind.CONFIRM, group_target(["kelt", "vorn3q"]))

    def closed() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server_module, "_until_ctrl_c", closed)
    monkeypatch.setattr(server_module, "_answers_can_be_typed", lambda: False)
    server_module.serve(project, port=0, open_browser=False)
    printed = capsys.readouterr().out.splitlines()
    assert "Waiting for Re-run: 1 answer and 1 setting." in printed
    assert not any("Re-running" in line for line in printed)
    assert JobLedger(project).recent() == [], "no job was asked for"


def test_the_command_line_says_nothing_where_nothing_waits(project: Path) -> None:
    assert waiting_line(project) is None


# -- One list of what waits ------------------------------------------------------------------------


def _write_one_of_each(project: Path) -> None:
    save_draft(project, "settings", {"dictionary": {"tokenise_names": False}})
    commit(project, actor="Ana", message="Names off", now=MOMENT)
    _event(
        project,
        RecordEventKind.ASSIGN,
        question_target("vocabulary_ambiguous", "kelt"),
        answer="kelt_buffer",
        theme_kind="reagent",
        raised="kelt",
        rule_id="vocabulary_ambiguous",
    )
    _event(project, RecordEventKind.CONFIRM, group_target(["kelt", "vorn3q"]))
    _event(
        project,
        RecordEventKind.DEFINE,
        term_target("vorn3q"),
        answer="vorn three q",
        theme_kind="subject",
        raised="vorn3q",
    )
    _event(project, RecordEventKind.REJECT, sense_target("plate"))
    _event(project, RecordEventKind.CONFIRM, surface_target("brisa_stain", "brisa"))
    # Refused, not accepted: on a project loading every pack, keeping a pack never refused changes
    # no reading (the keep question), so only a refusal waits for names to be read again.
    _event(project, RecordEventKind.REJECT, pack_target("microscopy"))


def test_every_change_that_changes_a_reading_is_on_one_list_by_kind(project: Path) -> None:
    _write_one_of_each(project)
    assert _summary(project) == [
        ("answer", 2, "2 answers"),
        ("word defined", 1, "1 word defined"),
        ("word off", 1, "1 word turned off"),
        ("word on", 1, "1 word turned back on"),
        ("pack", 1, "1 Dictionary change"),
        ("setting", 1, "1 setting"),
    ]
    waiting = pending_rerun(project)
    assert waiting is not None
    assert waiting.count == 7
    assert waiting.banner() == (
        "2 answers, 1 word defined, 1 word turned off, 1 word turned back on, "
        "1 Dictionary change and 1 setting since the last scan."
    )
    rows = {line["kind"]: line["rows"] for line in waiting.summary()}
    assert rows["answer"] == ['"kelt" means kelt_buffer', "the group kelt vorn3q accepted"]
    assert rows["word defined"] == ['"vorn3q" means vorn three q']
    assert rows["word off"] == ["plate"]
    assert rows["word on"] == ['"brisa" as brisa_stain']
    assert rows["pack"] == ["microscopy words refused"]
    assert rows["setting"] == ["dictionary.tokenise_names"]


def test_the_header_reads_a_count_and_a_summary_and_no_alert(project: Path) -> None:
    """The alert line and its three thresholds are retired: a change waits whatever its size."""
    _write_one_of_each(project)
    payload = state(project)["pending_rerun"]
    assert payload["count"] == 7
    assert [line["words"] for line in payload["summary"]][:2] == ["2 answers", "1 word defined"]
    for retired in ("alert", "because", "effect", "runs_by_itself"):
        assert retired not in payload


def test_a_word_defined_or_turned_off_on_the_answer_route_starts_nothing_and_waits(
    project: Path, engine: _Recorder
) -> None:
    with DashboardServer(project, port=0) as server:
        written = _post(
            server,
            "/api/review/answer",
            {"actor": PERSON, "word": "vorn3q", "answer": "vorn three q", "theme_kind": "subject"},
        )
        assert written["target"] == "term:vorn3q"
        assert "applying" not in written
        assert _get(server, "/api/applying")["phase"] == "idle"
        waiting = _get(server, "/api/workspace/state")["pending_rerun"]
        assert [line["words"] for line in waiting["summary"]] == ["1 word defined"]
        _post(
            server,
            "/api/review/answer",
            {"actor": PERSON, "dictionary": "inactivate", "term": "plate"},
        )
        assert _get(server, "/api/applying")["phase"] == "idle"
        waiting = _get(server, "/api/workspace/state")["pending_rerun"]
        assert [line["words"] for line in waiting["summary"]] == [
            "1 word defined",
            "1 word turned off",
        ]
    assert engine.calls == []
    assert JobLedger(project).recent() == []


def test_rerun_reads_again_only_the_names_holding_the_words_where_only_words_changed(
    project: Path, engine: _Recorder
) -> None:
    _event(
        project,
        RecordEventKind.DEFINE,
        term_target("vorn3q"),
        answer="vorn three q",
        theme_kind="subject",
        raised="vorn3q",
    )
    with DashboardServer(project, port=0) as server:
        started = _post(server, "/api/workspace/rerun", {})
        assert started["started"] == ["rebind", "inference"]
        assert server.applier.wait(30)
    assert [name for name, _ in engine.calls] == ["rebind", "inference"]
    assert engine.calls[0][1]["rebind"] is True
    assert engine.calls[0][1]["spellings"] == ("vorn3q",)


def test_rerun_reads_every_name_where_a_word_was_turned_off(
    project: Path, engine: _Recorder
) -> None:
    """A partial rebind refuses an inactivation, so every name is asked for at once."""
    _event(
        project,
        RecordEventKind.DEFINE,
        term_target("vorn3q"),
        answer="vorn three q",
        theme_kind="subject",
        raised="vorn3q",
    )
    with open_catalog(project) as catalog:
        inactivate(catalog, project, actor=PERSON, occurred_at=MOMENT, term="plate")
    with DashboardServer(project, port=0) as server:
        assert _post(server, "/api/workspace/rerun", {})["started"] == ["rebind", "inference"]
        assert server.applier.wait(30)
    assert [name for name, _ in engine.calls] == ["rebind", "inference"]
    assert "spellings" not in engine.calls[0][1]


def test_a_defined_word_is_applied_by_rerun_and_then_waits_no_more(project: Path) -> None:
    """The real engine, in a worker process: the partial rebind binds it, inference follows."""
    with DashboardServer(project, port=0) as server:
        _post(
            server,
            "/api/review/answer",
            {"actor": PERSON, "word": "vorn3q", "answer": "vorn three q", "theme_kind": "subject"},
        )
        assert pending_rerun(project) is not None
        _post(server, "/api/workspace/rerun", {})
        assert server.applier.wait(timeout=120)
        status = _get(server, "/api/applying")
        # Only the names holding it, where the stored names allow a partial rebind (these were
        # read in another year, so every name is read again: jobs/worker.py's fallback).
        assert status["phase"] == "done" and status["spellings"] == ["vorn3q"]
    assert pending_rerun(project) is None


@pytest.mark.parametrize(
    ("spellings", "run_spellings", "left"),
    [
        # A partial rebind that read the word's names did the rebind for it.
        (frozenset({"vorn3q"}), frozenset({"vorn3q", "kelt"}), ()),
        # One that read other names did not.
        (frozenset({"vorn3q"}), frozenset({"kelt"}), ("rebind", "inference")),
        # A change to every name is never done by a partial one.
        (frozenset(), frozenset({"vorn3q"}), ("rebind", "inference")),
    ],
)
def test_a_partial_rebind_does_the_rebind_only_for_the_words_it_read(
    spellings: frozenset[str], run_spellings: frozenset[str], left: tuple[str, ...]
) -> None:
    runs = [
        RunDone("2031-03-05T12:00:01Z", STEPS_A_RUN_DOES["partial rebind"], "a", run_spellings),
        RunDone("2031-03-05T12:00:02Z", STEPS_A_RUN_DOES["inference"], "b"),
    ]
    found = still_needed(("rebind", "inference"), "2031-03-05T12:00:00Z", runs, spellings=spellings)
    assert tuple(step.value for step in found) == left


# -- Answers only the rules read -------------------------------------------------------------------

#: A one-rule project revision whose rule reads approvals of an invented proposing rule's groups.
_APPROVALS_READ = """\
project_revision: 1
summary: A rule reading approved groups, for a test.
rules:
- id: ines_groups_approved
  version: '1'
  predicate: approved_experiment_group
  effect: groups
  primitive: equivalent
  respect: approved_experiment_group
  tier: defeasible
  level: file
  defeated_by: []
  statement: Files are one group when {proposal_rule} proposed it and a person approved.
  rationale: Invented for a test.
  parameters:
  - name: proposal_rule
    type: string
    default: kelt_proposals
    description: The rule whose proposals a person approves.
"""


def test_approving_experiments_as_one_group_waits_for_the_rules(project: Path) -> None:
    """A confirm on the proposing rule's question, which inference reads, waits for the rules."""
    (project / "rulebook").mkdir(exist_ok=True)
    (project / "rulebook" / "project-0001.yaml").write_text(_APPROVALS_READ, encoding="utf-8")
    _event(project, RecordEventKind.CONFIRM, question_target("kelt_proposals", "kelt vorn3q"))
    # A confirm on a question no rule reads the answer of changes no reading.
    _event(project, RecordEventKind.CONFIRM, question_target("vocabulary_ambiguous", "brisa"))
    waiting = pending_rerun(project)
    assert waiting is not None
    assert [step.value for step in waiting.steps] == ["inference"]
    assert _summary(project) == [("answer", 1, "1 answer")]
    assert waiting.summary()[0]["rows"] == ["the group kelt vorn3q accepted"]


def test_an_answer_only_the_rules_read_is_on_the_list_whatever_its_size(project: Path) -> None:
    """Such an answer counts on the list although its measured effect is 0."""
    _event(project, RecordEventKind.REJECT, group_target(["zeph", "vorn3q"]))
    waiting = pending_rerun(project)
    assert waiting is not None
    assert waiting.count == 1
    assert waiting.banner() == "1 answer since the last scan."
    assert state(project)["pending_rerun"]["count"] == 1


@pytest.mark.parametrize(
    ("target", "payload", "row"),
    [
        (
            "workflow:bench",
            {"name": "bench", "rungs": ["raw", "processed", "analysis"]},
            "the workflow bench added",
        ),
        (
            "prefix:/mnt/kelt_share",
            {"root_id": "root_zeph"},
            "another computer's folder /mnt/kelt_share matched",
        ),
    ],
)
def test_a_workflow_declared_or_a_prefix_placed_waits_for_the_rules(
    project: Path, target: str, payload: dict[str, Any], row: str
) -> None:
    """Inference reads both from the Record (``record/workflow.py``, ``infer/references.py``)."""
    _event(project, RecordEventKind.DEFINE, target, **payload)
    waiting = pending_rerun(project)
    assert waiting is not None
    assert [step.value for step in waiting.steps] == ["inference"]
    assert waiting.summary() == [{"kind": "answer", "count": 1, "words": "1 answer", "rows": [row]}]


def test_an_answer_taken_back_waits_as_the_answer_did(project: Path) -> None:
    """A "cannot say" after a meaning or an accept displaces it (``record/log.py``), so what the
    names or the rules read changes. The list says so, in the answer's own words."""
    _event(
        project,
        RecordEventKind.ASSIGN,
        question_target("vocabulary_ambiguous", "kelt"),
        answer="kelt_buffer",
        theme_kind="reagent",
        raised="kelt",
        rule_id="vocabulary_ambiguous",
    )
    _event(project, RecordEventKind.CONFIRM, group_target(["kelt", "vorn3q"]))
    _event(project, RecordEventKind.ANNOTATE, question_target("vocabulary_ambiguous", "kelt"))
    _event(project, RecordEventKind.ANNOTATE, group_target(["kelt", "vorn3q"]))
    # A note decides nothing, so it takes nothing back.
    _event(project, RecordEventKind.NOTE, term_target("vorn3q"), note="ask wren")
    waiting = pending_rerun(project)
    assert waiting is not None
    assert waiting.summary() == [
        {
            "kind": "answer",
            "count": 2,
            "words": "2 answers",
            "rows": [
                '"kelt" means kelt_buffer (undone)',
                "the group kelt vorn3q accepted (undone)",
            ],
        }
    ]
    assert waiting.request() == (("inference",), ("kelt",))


def test_a_run_that_read_the_answer_settles_it_whatever_the_clock_says(project: Path) -> None:
    """A run records the Record's watermark it read (schema 1.22). A run in the same second as
    an answer, or with a clock behind it, that read the answer has done what the answer needs."""
    _event(
        project,
        RecordEventKind.DEFINE,
        term_target("vorn3q"),
        answer="vorn three q",
        theme_kind="subject",
        raised="vorn3q",
    )
    assert pending_rerun(project) is not None
    # Both runs stamped with the answer's own second, the moment a clock cannot order.
    opened = Project.open(project)
    run_census(opened, run_id="run_20260101T000030Z_333333333333", started_at=MOMENT, rebind=True)
    run_inference(
        Project.open(project), run_id="run_20260101T000030Z_444444444444", started_at=MOMENT
    )
    assert pending_rerun(project) is None
    # An answer after both waits again, though the runs' clock reads no earlier.
    _event(project, RecordEventKind.REJECT, sense_target("plate"))
    waiting = pending_rerun(project)
    assert waiting is not None
    assert [line["words"] for line in waiting.summary()] == ["1 word turned off"]


@pytest.mark.parametrize(
    ("watermark", "left"),
    [(7, ()), (6, ("inference",)), (None, ("inference",))],
)
def test_the_watermark_decides_where_the_clock_cannot(
    watermark: int | None, left: tuple[str, ...]
) -> None:
    began = "2031-03-05T12:00:00Z"
    runs = [RunDone(began, STEPS_A_RUN_DOES["inference"], "a", watermark=watermark)]
    found = still_needed(("inference",), "2031-03-05T12:00:00Z", runs, sequence=7)
    assert tuple(step.value for step in found) == left


# -- Reads of split spellings, extensions and committed depths -------------------------------------


def test_a_divided_spelling_read_one_way_waits_for_its_names_then_the_rules(project: Path) -> None:
    """``split:<spelling>`` is read by the name reader (``dictionary/case_reading.py``)."""
    _event(project, RecordEventKind.CONFIRM, "split:zibKelt", reading="whole")
    waiting = pending_rerun(project)
    assert waiting is not None
    assert waiting.summary() == [
        {"kind": "answer", "count": 1, "words": "1 answer", "rows": ['"zibKelt" read as one word']}
    ]
    assert waiting.request() == (("inference",), ("zibkelt",))


def test_an_extension_worth_reading_waits_for_a_dive_and_says_its_commands(
    project: Path, engine: _Recorder
) -> None:
    """``extension:<ext>`` is read by the dive, which reads the share: its commands are said."""
    _event(project, RecordEventKind.REJECT, "extension:tif")
    waiting = pending_rerun(project)
    assert waiting is not None
    assert waiting.summary()[0]["rows"] == ["read contents of tif files: no"]
    assert [step.value for step in waiting.steps] == ["dive", "inference"]
    with DashboardServer(project, port=0) as server:
        said = _post(server, "/api/workspace/rerun", {})
    assert said["started"] == []
    assert said["needs_the_share"] == ["dive"]
    assert said["commands"] == ["ingence dive", "ingence infer"]
    assert engine.calls == []


def test_rerun_applies_what_it_can_while_a_change_waits_for_the_share(
    project: Path, engine: _Recorder
) -> None:
    """A committed depth does not hold every answer back: the rest is applied, and the command
    for the share's part is said."""
    save_draft(project, "settings", {"depth": "deep"})
    commit(project, actor="Ana", message="deeper", now=MOMENT)
    _event(
        project,
        RecordEventKind.DEFINE,
        term_target("vorn3q"),
        answer="vorn three q",
        theme_kind="subject",
        raised="vorn3q",
    )
    with DashboardServer(project, port=0) as server:
        said = _post(server, "/api/workspace/rerun", {})
        assert server.applier.wait(30)
    assert said["started"] == ["rebind", "inference"]
    assert said["needs_the_share"] == ["dive"]
    assert said["commands"] == ["ingence dive", "ingence infer"]
    assert [name for name, _ in engine.calls] == ["rebind", "inference"]
    assert engine.calls[0][1]["spellings"] == ("vorn3q",)


def test_a_rerun_stopped_by_a_close_waits_for_a_person_at_the_next_opening(
    project: Path, engine: _Recorder
) -> None:
    """A Re-run queued or interrupted when the Dashboard closed does not start by itself when the
    next one opens. The page is told it stopped, and Re-run (or Re-run now) starts it."""
    _event(project, RecordEventKind.CONFIRM, group_target(["kelt", "vorn3q"]))
    ledger = JobLedger(project)
    ledger.enqueue(steps=("inference",), record_sequence=1, base_run=INFERENCE)
    with DashboardServer(project, port=0) as server:
        said = " ".join(server.applier.reconciled)
        assert "A re-run stopped when the Dashboard last closed" in said
        assert "job_" not in said, "said in words, never by a job's identifier"
        assert not server.applier.wait(timeout=3), "nothing started by itself"
        assert engine.calls == []
        status = _get(server, "/api/applying")
        assert status["phase"] == "stopped" and status["stopped"] is True
        assert _get(server, "/api/workspace/state")["applying"]["stopped"] is True
        _post(server, "/api/workspace/rerun", {})
        assert server.applier.wait(timeout=30)
    assert [name for name, _ in engine.calls] == ["inference"]
    assert len(ledger.recent()) == 1, "Re-run joined the stopped job"


def test_opening_says_what_waits_once_in_plain_words(project: Path) -> None:
    """The one line says what waits, and no line counts Record sequences that no job holds."""
    from ingence.api import server as server_module
    from ingence.jobs import JobState

    # A ledger an earlier job left, done. The line counting Record sequences is said only where
    # a ledger exists.
    ledger = JobLedger(project)
    job = ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFERENCE)
    claimed = ledger.claim_next(pinned=INFERENCE)
    assert claimed is not None
    ledger.finish(job.job_id, attempt=claimed.attempts, state=JobState.DONE)
    _event(
        project,
        RecordEventKind.DEFINE,
        term_target("vorn3q"),
        answer="vorn three q",
        theme_kind="subject",
        raised="vorn3q",
    )
    said: list[str] = []
    with DashboardServer(project, port=0) as server:
        assert not any("sequence" in line for line in server.applier.reconciled)
        server_module.say_what_waits(project, say=said.append)
    assert said == ["Waiting for Re-run: 1 word defined."]


def test_closing_with_a_commit_says_it_waits_for_rerun(project: Path) -> None:
    """The next session starts nothing, so the close never promises it does."""
    from ingence.workspace.close_prompt import ask_before_closing

    save_draft(project, "settings", {"scan": {"workers": 3}})
    said: list[str] = []
    ask_before_closing(project, actor="Ana", ask=lambda _question: "y", say=said.append)
    assert "Committed. It waits for Re-run." in said
    assert not any("next session starts" in line for line in said)


# -- The hold never strands a person's Re-run ------------------------------------------------------


def _headers(server: DashboardServer, route: str) -> dict[str, str]:
    with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
        return {key.lower(): value for key, value in response.headers.items()}


def test_a_stopped_rerun_pins_no_page_to_an_older_run_and_rerun_starts_it(
    project: Path, engine: _Recorder
) -> None:
    """A held job does not keep every page on the run from before the close, and Re-run releases
    it where only a change needing the share waits. The steps: a job a close left, a run made in
    a terminal meanwhile, then an answer only a dive reads, and Re-run."""
    ledger = JobLedger(project)
    job = ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFERENCE)
    newer = "run_20260101T000031Z_555555555555"
    run_inference(Project.open(project), run_id=newer, started_at="2026-01-01T00:00:31Z")
    with DashboardServer(project, port=0) as server:
        assert server.applier.stopped()
        said = _headers(server, "/api/project")
        assert said.get("x-ingence-run-pinned") in (None, newer), "the newest run is read"
        assert said.get("x-ingence-job-running") == "false"
        _event(project, RecordEventKind.REJECT, "extension:tif")
        answer = _post(server, "/api/workspace/rerun", {})
        assert answer["needs_the_share"] == ["dive"]
        assert not server.applier.stopped(), "Re-run started the stopped job"
        assert server.applier.wait(timeout=30)
    assert JobLedger(project).get(job.job_id).state.value == "done"  # type: ignore[union-attr]


def test_a_stopped_rerun_whose_census_is_current_keeps_the_pages_on_its_base(
    project: Path,
) -> None:
    """One exception: where the held job had published its census, the census holds no
    inference yet, so the pages keep reading the run the job began from."""
    from ingence.jobs import JobState

    ledger = JobLedger(project)
    job = ledger.enqueue(spellings=("vorn3q",), record_sequence=0, base_run=INFERENCE)
    claimed = ledger.claim_next(pinned=INFERENCE)
    assert claimed is not None
    census = "run_20260101T000032Z_666666666666"
    run_census(Project.open(project), run_id=census, started_at="2026-01-01T00:00:32Z", rebind=True)
    ledger.heartbeat(
        job.job_id,
        attempt=claimed.attempts,
        bound_run=census,
        progress={"bound_run": census, "binding_run": census},
    )
    ledger.interrupt(job.job_id, reason="the server died", retry_limit=2, cause="gone")
    assert ledger.get(job.job_id).state is JobState.QUEUED  # type: ignore[union-attr]
    with DashboardServer(project, port=0) as server:
        assert server.applier.stopped()
        assert _headers(server, "/api/project").get("x-ingence-run-pinned") == INFERENCE


def test_a_rerun_on_a_second_dashboard_releases_a_stopped_job(
    project: Path, engine: _Recorder
) -> None:
    """The hold does not live in the memory of the server holding the jobs, so a Re-run pressed on
    a second Dashboard serving the same project releases the job."""
    _event(project, RecordEventKind.CONFIRM, group_target(["kelt", "vorn3q"]))
    JobLedger(project).enqueue(steps=("inference",), record_sequence=0, base_run=INFERENCE)
    with DashboardServer(project, port=0) as first, DashboardServer(project, port=0) as second:
        assert first.applier.stopped() and second.applier.stopped()
        _post(second, "/api/workspace/rerun", {})
        assert not first.applier.stopped()
        assert first.applier.wait(timeout=30)
    assert [name for name, _ in engine.calls] == ["inference"]


def test_a_crash_mid_rerun_is_said_in_one_line_at_the_next_opening(project: Path) -> None:
    """A server that died mid-job leaves one opening line, which does not name the job."""
    ledger = JobLedger(project)
    ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFERENCE)
    assert ledger.claim_next(pinned=INFERENCE) is not None
    with DashboardServer(project, port=0) as server:
        said = server.applier.reconciled
    assert said == [
        "A re-run stopped when the Dashboard last closed (it was interrupted while starting); "
        "Re-run starts it again."
    ]


@pytest.mark.parametrize(
    ("kind", "target", "verdict", "row"),
    [
        (
            RecordEventKind.REJECT,
            "class:copies:kelt_vorn",
            "not_copies",
            "a set of copies: Unrelated",
        ),
        (RecordEventKind.CONFIRM, "class:copies:kelt_vorn", "copies", "a set of copies: Copies"),
        (
            RecordEventKind.REJECT,
            "candidate:zeph_brisa",
            "unrelated",
            "a suggested pair: Unrelated",
        ),
    ],
)
def test_an_answer_on_copies_or_a_pair_waits_for_the_rules(
    project: Path, kind: RecordEventKind, target: str, verdict: str, row: str
) -> None:
    """Inference reads a copies answer and any pair answer (``record/answers.py``), so each waits
    for Re-run like the other answers the rules read."""
    _event(project, kind, target, verdict=verdict)
    waiting = pending_rerun(project)
    assert waiting is not None
    assert [step.value for step in waiting.steps] == ["inference"]
    assert waiting.summary() == [{"kind": "answer", "count": 1, "words": "1 answer", "rows": [row]}]


def test_an_answer_on_a_group_no_run_reads_does_not_wait(project: Path) -> None:
    """A class answered "One group" is read by precision and the pages, never by inference."""
    _event(project, RecordEventKind.CONFIRM, "class:family:kelt_vorn", verdict="one_thing")
    assert pending_rerun(project) is None
