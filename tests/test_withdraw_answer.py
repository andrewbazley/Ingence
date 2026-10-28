"""One answer about a word, withdrawn on its own.

A person can take back one answer about a word, an imported one included, without going back to an
earlier run. The Record keeps everything, and nothing else can be withdrawn this way. What is
checked:

1. A person names the answer by its sequence and its digest as the page read it. The answer stays in
   the Record (``read_events(withdrawn=True)`` shows it and the withdrawal), replay skips it, and
   the word is as if it was never answered.
2. The withdrawal waits for Re-run like any answer, and the badge counts it. An answer no run ever
   read leaves nothing to wait for.
3. Refused in words: an answer about anything but a word, a note, a withdrawal, an answer already
   withdrawn, a digest the Record does not hold (a page out of date), and a request that does not
   name an answer. The Record's one writer checks all of it, so a caller of the generic writer
   cannot build a withdrawal the route would refuse.
4. Going back to an earlier run still works over a withdrawn answer, and brings it back.
5. The queue says what a page needs to withdraw an answer: its sequence, its digest, whether it may
   be withdrawn.

Every name is invented (``fixtures/words_pool.py``).
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from fixtures.words_pool import build_words_project, wait_for_a_later_second
from ingence.api import v1
from ingence.api.server import ANSWER_ROUTE, DashboardServer
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.dictionary.store import DictionaryStore
from ingence.infer.run import run_inference
from ingence.record import log as record_log
from ingence.record.imported import apply_carried, carried_words, stated
from ingence.record.log import (
    AppendError,
    WithdrawRefusedError,
    answer_digest,
    answer_withdrawals,
    append_event,
    read_events,
    withdrawable,
)
from ingence.scan.census import run_census
from ingence.storage.commit import read_pointer
from ingence.workspace.restore import restore, restore_plan
from ingence.workspace.state import pending_rerun
from ingence.workspace.withdraw import withdraw_answer

v1.load_routes()
SOURCE = Path(__file__).resolve().parent.parent / "src" / "ingence"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_words_project(tmp_path_factory.mktemp("withdraw_built"))


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def _post(server: DashboardServer, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        server.url.rstrip("/") + ANSWER_ROUTE, method="POST", data=json.dumps(body).encode()
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Ingence-Write", "1")
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return int(error.code), json.load(error)
        finally:
            error.close()


def _answer(server: DashboardServer, word: str, meaning: str) -> None:
    status, body = _post(
        server,
        {
            "actor": "Dana Whitfield",
            "target": f"term:{word}",
            "answer": meaning,
            "theme_kind": "reagent",
        },
    )
    assert status == 201, body


def _named(project: Path, target: str) -> dict[str, Any]:
    """The answer as a page reads it from the queue: its sequence and its digest."""
    run = read_pointer(project).run_id  # type: ignore[union-attr]
    context = v1.V1Context(project=project, run_current=run, run_pinned=run, job_running=False)
    status, body = v1.dispatch(context, "words/queue", {"answered": ["only"], "limit": ["500"]})
    assert status == 200, body
    found = [row["answered"] for row in body["rows"] if row["answered"]["target"] == target]
    assert len(found) == 1, (target, [row["word"] for row in body["rows"]])
    return {"sequence": found[0]["sequence"], "digest": found[0]["digest"]}


def _withdraw(server: DashboardServer, named: dict[str, Any], actor: str = "Sam Okafor") -> Any:
    return _post(server, {"actor": actor, "withdraw": named})


def _read_again(project: Path, number: int = 3) -> None:
    """A run that reads the Record at its watermark: the names again, then the rules.

    Begun after everything written so far (a run in a change's own second has not followed it),
    by the clock the Record's own events are stamped with.
    """
    stamp = wait_for_a_later_second(project)
    run_census(
        Project.open(project),
        run_id=f"run_20260101T00000{number}Z_{str(number) * 12}",
        started_at=stamp,
        rebind=True,
    )
    run_inference(
        Project.open(project),
        run_id=f"run_20260101T00001{number}Z_{str(number + 1) * 12}",
        started_at=record_log.now(),
    )


# -- 1. one answer withdrawn, and the Record keeps everything -------------------------------------


def test_a_person_withdraws_one_answer_and_it_stays_in_the_record(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        _answer(server, "quolts", "quolts_assay")
        named = _named(project, "term:quolt")
        status, written = _withdraw(server, named)
    assert status == 201, written
    assert (written["kind"], written["target"], written["actor"]) == (
        "withdraw",
        "term:quolt",
        "Sam Okafor",
    )
    # Replay skips the answer and the withdrawal; the other answer is untouched.
    assert {event.target for event in read_events(project)} == {"term:quolts"}
    store = DictionaryStore.for_project(project)
    assert store.look_up("quolt") is None
    hit = store.look_up("quolts")
    assert hit is not None and hit.sense.term == "quolts_assay"
    # Nothing was deleted: the History reads everything.
    everything = read_events(project, held=False, withdrawn=True)
    assert [event.kind for event in everything] == [
        RecordEventKind.DEFINE,
        RecordEventKind.DEFINE,
        RecordEventKind.WITHDRAW,
    ]
    answer, _, withdrawal = everything
    payload = json.loads(withdrawal.payload)
    assert payload["withdrawn"] == [answer.event_id]
    assert payload["answer"] == named
    assert answer_digest(answer) == named["digest"] and answer.sequence == named["sequence"]
    assert [(w.sequence, a.sequence) for w, a in answer_withdrawals(project)] == [
        (withdrawal.sequence, answer.sequence)
    ]


def test_the_word_is_unanswered_again_in_the_queue(project: Path) -> None:
    run = read_pointer(project).run_id  # type: ignore[union-attr]
    context = v1.V1Context(project=project, run_current=run, run_pinned=run, job_running=False)
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        named = _named(project, "term:quolt")
        _, hidden = v1.dispatch(context, "words/queue", {})
        assert "quolt" not in {row["word"] for row in hidden["rows"]}
        assert _withdraw(server, named)[0] == 201
    _, shown = v1.dispatch(context, "words/queue", {})
    row = next(row for row in shown["rows"] if row["word"] == "quolt")
    assert row["answered"] is None and row["waiting"] is False
    _, only = v1.dispatch(context, "words/queue", {"answered": ["only"]})
    assert "quolt" not in {row["word"] for row in only["rows"]}


def test_an_answer_carried_in_from_another_project_is_withdrawn_like_any(
    built: Path, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    shutil.copytree(built, source)
    with DashboardServer(source, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        _answer(server, "quolts", "quolts_assay")
    here = tmp_path / "here"
    shutil.copytree(built, here)
    report = apply_carried(
        here,
        carried_words(source, source="Lab notebook"),
        statement=stated("Sam Okafor"),
        imported_from_name="Lab notebook",
        occurred_at="2026-01-02T00:00:00Z",
    )
    assert len(report.written) == 2
    with DashboardServer(here, port=0) as server:
        named = _named(here, "term:quolt")
        status, written = _withdraw(server, named)
    assert status == 201, written
    assert {event.target for event in read_events(here)} == {"term:quolts"}
    carried = [e for e in read_events(here, withdrawn=True) if e.kind is RecordEventKind.DEFINE]
    assert {json.loads(e.payload)["source"]["imported_from"] for e in carried} == {"Lab notebook"}


# -- 2. it waits for Re-run -----------------------------------------------------------------------


def test_the_withdrawal_of_an_answer_a_run_read_waits_for_rerun_and_is_counted(
    project: Path,
) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
    _read_again(project)  # a run reads the answer
    before = pending_rerun(project)
    assert not [c for c in (before.changes if before else ()) if c.kind == "word defined"]
    with DashboardServer(project, port=0) as server:
        status, written = _withdraw(server, _named(project, "term:quolt"))
    assert status == 201, written
    waiting = pending_rerun(project)
    assert waiting is not None
    (change,) = [c for c in waiting.changes if c.kind == "word defined"]
    assert change.rows == ('"quolt" means quolt_assay (undone)',)
    assert change.sequence == written["sequence"]
    assert waiting.count == (before.count if before else 0) + 1
    # The next run reads the Record at its watermark, withdrawal included: nothing waits for it.
    _read_again(project, 4)
    after = pending_rerun(project)
    assert not [c for c in (after.changes if after else ()) if c.kind == "word defined"]


def test_an_answer_no_run_read_leaves_nothing_to_wait_for_when_it_is_withdrawn(
    project: Path,
) -> None:
    base = pending_rerun(project)
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        waiting = pending_rerun(project)
        assert waiting is not None
        assert waiting.count == (base.count if base else 0) + 1
        assert _withdraw(server, _named(project, "term:quolt"))[0] == 201
    cancelled = pending_rerun(project)
    assert (cancelled.count if cancelled else 0) == (base.count if base else 0)


# -- 3. what is refused, in words -----------------------------------------------------------------


def _event(project: Path, target: str) -> Any:
    (found,) = [e for e in read_events(project, withdrawn=True) if e.target == target]
    return found


def test_only_an_answer_about_a_word_is_withdrawn_this_way(project: Path) -> None:
    run = read_pointer(project).run_id  # type: ignore[union-attr]
    append_event(
        project,
        kind=RecordEventKind.CONFIRM,
        actor="Dana Whitfield",
        occurred_at="2026-01-03T00:00:00Z",
        target="pack:microscopy",
        against_run_id=run,
        payload={"question": "load the microscopy pack"},
    )
    pack = _event(project, "pack:microscopy")
    assert not withdrawable(pack)
    with DashboardServer(project, port=0) as server:
        status, refused = _withdraw(
            server, {"sequence": pack.sequence, "digest": answer_digest(pack)}
        )
    assert status == 400
    assert "only an answer about a word" in refused["error"]
    assert [e.target for e in read_events(project)] == ["pack:microscopy"]


def test_a_note_and_a_withdrawal_are_no_answers(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, _ = _post(
            server, {"actor": "Dana Whitfield", "target": "term:vorn", "note": "ask Priya Raman"}
        )
        assert status == 201
        note = _event(project, "term:vorn")
        assert note.kind is RecordEventKind.NOTE and not withdrawable(note)
        status, refused = _withdraw(
            server, {"sequence": note.sequence, "digest": answer_digest(note)}
        )
        assert status == 400 and "not an answer" in refused["error"]
        _answer(server, "quolt", "quolt_assay")
        assert _withdraw(server, _named(project, "term:quolt"))[0] == 201
    withdrawal = next(
        e for e in read_events(project, withdrawn=True) if e.kind is RecordEventKind.WITHDRAW
    )
    assert withdrawal.target == "term:quolt"  # under the answer's own target
    with DashboardServer(project, port=0) as server:
        status, refused = _withdraw(
            server, {"sequence": withdrawal.sequence, "digest": answer_digest(withdrawal)}
        )
    assert status == 400 and "not an answer" in refused["error"]


def test_an_answer_already_withdrawn_is_refused(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        named = _named(project, "term:quolt")
        assert _withdraw(server, named)[0] == 201
        status, refused = _withdraw(server, named)
    assert status == 400 and "already withdrawn" in refused["error"]
    assert len([e for e in read_events(project, withdrawn=True) if e.kind.value == "withdraw"]) == 1


def test_an_answer_a_later_answer_replaced_is_refused_as_out_of_date(project: Path) -> None:
    """Withdrawing a replaced answer would change no reading, while the badge counted it as taken
    back: only the answer in force on its word can be named."""
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        replaced = _named(project, "term:quolt")
        append_event(
            project,
            kind=RecordEventKind.DEFINE,
            actor="Dana Whitfield",
            occurred_at="2026-01-04T00:00:00Z",
            target="term:quolt",
            against_run_id=read_pointer(project).run_id,  # type: ignore[union-attr]
            payload={"raised": "quolt", "answer": "quolt_stain", "theme_kind": "reagent"},
        )
        standing = _named(project, "term:quolt")
        assert standing["sequence"] != replaced["sequence"]
        status, refused = _withdraw(server, replaced)
        assert status == 409, refused
        assert "replaced" in refused["error"] and "look at it again" in refused["error"]
        assert answer_withdrawals(project) == ()
        # The answer in force goes; the one before it is in force again, and can then be named.
        assert _withdraw(server, standing)[0] == 201
        assert _withdraw(server, replaced)[0] == 201
    assert read_events(project) == ()
    assert len(answer_withdrawals(project)) == 2


def test_two_clicks_on_one_answer_write_one_withdrawal(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        named = _named(project, "term:quolt")
    clickers = 4
    together = threading.Barrier(clickers)

    def click(who: str) -> Any:
        together.wait()
        try:
            return withdraw_answer(
                project,
                actor=who,
                sequence=named["sequence"],
                digest=named["digest"],
                occurred_at="2026-01-05T00:00:00Z",
            )
        except WithdrawRefusedError as refused:
            return refused

    with ThreadPoolExecutor(clickers) as pool:
        results = list(pool.map(click, [f"Sam Okafor {number}" for number in range(clickers)]))
    refused = [found for found in results if isinstance(found, WithdrawRefusedError)]
    assert len(results) - len(refused) == 1
    assert all("already withdrawn" in str(found) for found in refused)
    assert len(answer_withdrawals(project)) == 1


def test_the_writer_checks_a_withdrawal_with_the_records_lock_held(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []
    real = record_log._check_answer_withdrawal

    def watching(where: Path, detail: Any) -> None:
        seen.append((record_log.record_directory(where) / record_log.LOCK_FILE).exists())
        real(where, detail)

    monkeypatch.setattr(record_log, "_check_answer_withdrawal", watching)
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        assert _withdraw(server, _named(project, "term:quolt"))[0] == 201
    assert seen == [True]


def test_a_page_out_of_date_cannot_withdraw_something_else(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        named = _named(project, "term:quolt")
        # The digest is not the answer's: a conflict, and nothing is withdrawn.
        status, refused = _withdraw(server, {**named, "digest": "0" * 32})
        assert status == 409 and "out of date" in refused["error"]
        # No such sequence.
        status, refused = _withdraw(server, {"sequence": 9999, "digest": named["digest"]})
        assert status == 409
        # The digest of another answer does not name this one.
        _answer(server, "quolts", "quolts_assay")
        other = _named(project, "term:quolts")
        status, _ = _withdraw(server, {"sequence": named["sequence"], "digest": other["digest"]})
        assert status == 409
    assert {e.target for e in read_events(project)} == {"term:quolt", "term:quolts"}


def test_a_request_that_names_no_answer_and_one_that_names_nobody_are_refused(
    project: Path,
) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        named = _named(project, "term:quolt")
        for body in (
            {"actor": "Sam Okafor", "withdraw": "all of them"},
            {"actor": "Sam Okafor", "withdraw": {"sequence": "3", "digest": named["digest"]}},
            {"actor": "Sam Okafor", "withdraw": {"sequence": True, "digest": named["digest"]}},
            {"actor": "Sam Okafor", "withdraw": {"sequence": named["sequence"]}},
            {"actor": "", "withdraw": named},
        ):
            status, refused = _post(server, body)
            assert status == 400, (body, refused)
    assert {e.target for e in read_events(project)} == {"term:quolt"}


def test_the_generic_writer_checks_a_withdrawal_of_one_answer_itself(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        _answer(server, "quolts", "quolts_assay")
    run = read_pointer(project).run_id  # type: ignore[union-attr]
    first = _event(project, "term:quolt")
    second = _event(project, "term:quolts")

    def write(payload: dict[str, Any]) -> None:
        append_event(
            project,
            kind=RecordEventKind.WITHDRAW,
            actor="a person",
            occurred_at="2026-01-04T00:00:00Z",
            target=first.target,
            against_run_id=run,
            payload=payload,
        )

    named = {"sequence": first.sequence, "digest": answer_digest(first)}
    for payload in (
        {"withdrawn": [first.event_id], "answer": {**named, "digest": "x"}},
        {"withdrawn": [second.event_id], "answer": named},  # names another answer's event
        {"withdrawn": [first.event_id, second.event_id], "answer": named},
        {"withdrawn": [], "answer": named},
        {"withdrawn": [first.event_id], "answer": "the first"},
    ):
        with pytest.raises(AppendError):
            write(payload)
    with pytest.raises(WithdrawRefusedError) as stale:
        write({"withdrawn": [first.event_id], "answer": {**named, "digest": "x"}})
    assert stale.value.stale is True
    # Without naming an answer it is still the restore's alone.
    with pytest.raises(AppendError, match=r"restored_to|previewed"):
        write({"withdrawn": [first.event_id]})
    assert len(read_events(project, held=False, withdrawn=True)) == 2
    write({"withdrawn": [first.event_id], "answer": named})
    assert {e.target for e in read_events(project)} == {"term:quolts"}


def test_only_the_restore_and_the_one_answer_module_build_a_withdrawal() -> None:
    builders = []
    for path in sorted(SOURCE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(
            r"kind\s*=\s*RecordEventKind\.WITHDRAW|RecordEventKind\(\s*[\"']withdraw", text
        ):
            builders.append(path.relative_to(SOURCE).as_posix())
    assert builders == ["workspace/restore.py", "workspace/withdraw.py"]


# -- 4. going back still works --------------------------------------------------------------------


R0 = "run_20260101T000001Z_111111111111"
R1 = "run_20260101T000002Z_222222222222"
R2 = "run_20260101T000004Z_444444444444"
R3 = "run_20260101T000006Z_666666666666"


def _define(path: Path, word: str, at: str, against: str) -> None:
    append_event(
        path,
        kind=RecordEventKind.DEFINE,
        actor="a person",
        occurred_at=at,
        target=f"term:{word}",
        against_run_id=against,
        payload={"answer": f"{word}_reagent", "theme_kind": "reagent", "raised": word},
    )


def test_going_back_to_an_earlier_run_still_works_and_brings_a_withdrawn_answer_back(
    tmp_path: Path,
) -> None:
    from ingence.workspace.withdraw import withdraw_answer

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
    zib = _event(made.path, "term:zib")
    withdrawn = withdraw_answer(
        made.path,
        actor="Sam Okafor",
        sequence=zib.sequence,
        digest=answer_digest(zib),
        occurred_at="2026-01-01T00:00:07Z",
    )
    assert {e.target for e in read_events(made.path, held=False)} == {"term:brisa"}
    # Back to the run made after the first answer: the answer was in force then, so it returns,
    # and the later one is set aside, in one restore that names the withdrawal.
    plan = restore_plan(made.path, R2)
    assert withdrawn.event_id in plan.withdraw
    restore(
        made.path, run_id=R2, actor="a person", previewed=plan.digest, now="2026-01-01T00:00:08Z"
    )
    pointer = read_pointer(made.path)
    assert pointer is not None and pointer.run_id == R2
    assert {e.target for e in read_events(made.path, held=False)} == {"term:zib"}
    # The Record only grew: the answer, the withdrawal and the restore's own are all still there.
    kinds = [e.kind for e in read_events(made.path, held=False, withdrawn=True)]
    assert kinds == [
        RecordEventKind.DEFINE,
        RecordEventKind.DEFINE,
        RecordEventKind.WITHDRAW,
        RecordEventKind.WITHDRAW,
    ]


# -- 5. what the queue gives a page ---------------------------------------------------------------


def test_the_queue_names_each_answer_by_its_sequence_and_digest_and_says_which_can_go(
    project: Path,
) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        status, _ = _post(
            server, {"actor": "Dana Whitfield", "target": "term:vorn", "word": "not_a_word"}
        )
        assert status == 201
        status, _ = _post(
            server, {"actor": "Dana Whitfield", "target": "split:zibkelt", "word": "pieces"}
        )
        assert status == 201
    run = read_pointer(project).run_id  # type: ignore[union-attr]
    context = v1.V1Context(project=project, run_current=run, run_pinned=run, job_running=False)
    _, body = v1.dispatch(context, "words/queue", {"answered": ["only"]})
    by_word = {row["word"]: row["answered"] for row in body["rows"]}
    assert set(by_word) == {"quolt", "vorn", "zibkelt"}
    for word, target in (
        ("quolt", "term:quolt"),
        ("vorn", "term:vorn"),
        ("zibkelt", "split:zibkelt"),
    ):
        answered = by_word[word]
        event = _event(project, target)
        assert answered["sequence"] == event.sequence
        assert answered["digest"] == answer_digest(event)
        assert answered["withdrawable"] is True
    # A question answered with a meaning is not withdrawn on its own: the queue says so.
    from ingence.textword.questions import RULE_ID

    append_event(
        project,
        kind=RecordEventKind.DEFINE,
        actor="Dana Whitfield",
        occurred_at="2026-01-03T00:00:00Z",
        target=f"question:{RULE_ID}:fennowic",
        against_run_id=run,
        payload={
            "raised": "fennowic",
            "answer": "fennowic_probe",
            "theme_kind": "reagent",
            "rule_id": RULE_ID,
        },
    )
    _, again = v1.dispatch(context, "words/queue", {"answered": ["only"]})
    asked = next(row["answered"] for row in again["rows"] if row["word"] == "fennowic")
    assert asked["target"].startswith("question:") and asked["withdrawable"] is False


def test_the_run_manifest_says_a_single_withdrawal_plainly(project: Path) -> None:
    """Said as a withdrawal by the person, never as "went back to a run" (it went back to none)."""
    from ingence.api.routes.manifest import manifest

    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
    _read_again(project, 3)
    with DashboardServer(project, port=0) as server:
        assert _withdraw(server, _named(project, "term:quolt"))[0] == 201
    _read_again(project, 4)
    after = read_pointer(project)
    assert after is not None
    found = manifest(project, after.run_id, viewer="Sam Okafor")
    [alone] = found["withdrawals"]
    assert alone["said"] == "1 answer withdrawn by you."
    assert alone["went_back_to"] == "" and alone["answers"] == 1
    assert alone["said"] in found["said"]


def test_a_withdrawn_answer_is_not_carried_on_to_another_project(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        _answer(server, "quolt", "quolt_assay")
        _answer(server, "quolts", "quolts_assay")
        assert {a.target for a in carried_words(project).answers} == {"term:quolt", "term:quolts"}
        assert _withdraw(server, _named(project, "term:quolt"))[0] == 201
    assert {a.target for a in carried_words(project).answers} == {"term:quolts"}
