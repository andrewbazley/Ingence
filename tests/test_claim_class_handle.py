"""A class handle on every claim: "this is wrong" answered about the class.

A rule that read one file a way read every file of its class the same way. The class is all the
claims one rule made with one value, so a person saying one file's reading is wrong is judging the
class. Every claim serves its class handle (the Record's class target) and the class's summary
(the rule, the value, how many, three examples) before anyone commits. An answer on the handle is
one Record event on the class target, never one per file. The next inference reads that event for
every member. A class answered wrong has each member's claim published as conflicting, defeated
by the person's answer through the rule that reads it, and never removed.

Every name is invented; the person answering is an invented person.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.record.targets import class_target
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

PERSON = "zib (an invented person)"
MOMENT = "2026-10-08T01:00:00Z"
CENSUS = "run_20260101T000001Z_000000000001"
FIRST = "run_20260101T000002Z_000000000002"
AGAIN = "run_20260101T000003Z_000000000003"
#: The rule reading a file's format from its extension, and the class of forty it makes here.
FORMAT_RULE = "format_from_extension_unless_contradicted"
MEMBERS = 40
#: A dated folder, so a rule reads it as an experiment and the listing can be asked for it.
FOLDER = "20240301 kelt"


def _build(base: Path) -> Project:
    pool = base / "pool"
    for number in range(1, MEMBERS + 1):
        path = pool / FOLDER / f"vorn_{number:03d}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a,b\n1,2\n")
    for name in ("brisa_notes.txt", "quolt_notes.txt"):
        (pool / FOLDER / name).write_text("invented words\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=FIRST, started_at="t")
    return project


@pytest.fixture
def pool(tmp_path: Path) -> Project:
    return _build(tmp_path)


def _claims(project: Project, rule_id: str) -> list[dict[str, Any]]:
    with open_catalog(project.path) as catalog:
        cursor = catalog.query(
            "SELECT c.claim_id, c.object_reference AS value, c.resolution_status AS status, "
            "c.defeaters_json, e.name FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = ? ORDER BY e.name",
            [rule_id],
        )
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _handle(project: Project, claim_id: str) -> dict[str, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project.path, settled=None, job_running=False)
    status, body = dispatch(context, "claims/class", {"claim": [claim_id]})
    assert status == HTTPStatus.OK, body
    return body


def _answer(project: Project, target: str, word: str) -> object:
    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog, project.path)
        return decide_target(
            context, Decision(target=target, word=word), actor=PERSON, occurred_at=MOMENT
        )


def test_a_claim_names_its_class_of_forty_with_three_examples_before_anyone_commits(
    pool,
) -> None:
    claims = [claim for claim in _claims(pool, FORMAT_RULE) if claim["name"].endswith(".csv")]
    assert len(claims) == MEMBERS
    value = claims[0]["value"]
    assert {claim["value"] for claim in claims} == {value}
    handle = _handle(pool, claims[7]["claim_id"])
    assert handle["handle"] == class_target(FORMAT_RULE, value)
    assert handle["rule_id"] == FORMAT_RULE and handle["rule_version"]
    assert handle["value"] == value
    assert handle["size"] == MEMBERS
    assert len(handle["examples"]) == 3
    assert all(example.startswith(f"{FOLDER}/vorn_") for example in handle["examples"])
    assert [word["word"] for word in handle["words"]] == ["correct", "wrong", "cannot_say"]
    assert handle["in_force"] is None
    # Every claim of the class names the same handle and the same three.
    other = _handle(pool, claims[31]["claim_id"])
    assert other["handle"] == handle["handle"] and other["examples"] == handle["examples"]


def test_an_answer_on_the_handle_is_one_event_and_the_next_inference_reads_it_for_all(
    pool,
) -> None:
    claims = [claim for claim in _claims(pool, FORMAT_RULE) if claim["name"].endswith(".csv")]
    handle = _handle(pool, claims[0]["claim_id"])
    before = len(read_events(pool.path))
    event = _answer(pool, handle["handle"], "wrong")
    events = read_events(pool.path)
    assert len(events) == before + 1  # one event, on the class, never one per file
    assert event.target == handle["handle"]  # type: ignore[attr-defined]
    assert events[-1].target == handle["handle"]
    assert _handle(pool, claims[0]["claim_id"])["in_force"]["word"] == "wrong"

    run_inference(pool, run_id=AGAIN, started_at="t")
    after = [claim for claim in _claims(pool, FORMAT_RULE) if claim["name"].endswith(".csv")]
    assert len(after) == MEMBERS
    for claim in after:
        assert claim["status"] == "examined_conflicting", claim["name"]
        fired = [d for d in _defeaters(claim) if d["fired"]]
        assert fired, claim["name"]
        assert any(event.event_id in d["observations"] for d in fired)  # type: ignore[attr-defined]
    # The class keeps its handle and its size: a defeated claim is still the rule's.
    assert _handle(pool, after[0]["claim_id"])["size"] == MEMBERS
    # A class nobody answered stays as it was.
    notes = [claim for claim in _claims(pool, FORMAT_RULE) if claim["name"].endswith(".txt")]
    assert notes and all(claim["status"] == "resolved" for claim in notes)


def test_a_correct_answer_on_the_class_leaves_every_claim_standing(pool) -> None:
    claims = [claim for claim in _claims(pool, FORMAT_RULE) if claim["name"].endswith(".csv")]
    handle = _handle(pool, claims[0]["claim_id"])
    _answer(pool, handle["handle"], "correct")
    run_inference(pool, run_id=AGAIN, started_at="t")
    after = [claim for claim in _claims(pool, FORMAT_RULE) if claim["name"].endswith(".csv")]
    assert after and all(claim["status"] == "resolved" for claim in after)
    assert _handle(pool, after[0]["claim_id"])["in_force"]["word"] == "correct"


def _defeaters(claim: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    held = json.loads(claim["defeaters_json"] or "[]")
    return [item for item in held if isinstance(item, dict)]


def test_the_listing_of_an_experiment_names_each_claim_s_class_handle(pool) -> None:
    """The per-file listing (design 5) carries the handle beside each classifying claim."""
    from ingence.api.v1 import context_for, dispatch

    claims = [claim for claim in _claims(pool, FORMAT_RULE) if claim["name"].endswith(".csv")]
    handle = _handle(pool, claims[0]["claim_id"])["handle"]
    with open_catalog(pool.path) as catalog:
        found = catalog.query(
            "SELECT c.subject FROM claims c WHERE c.rule_id LIKE 'experiment_folder%' LIMIT 1"
        ).fetchone()
    assert found is not None, "the dated folder is read as an experiment"
    context = context_for(pool.path, settled=None, job_running=False)
    status, body = dispatch(context, "experiments/files", {"experiment": [str(found[0])]})
    assert status == HTTPStatus.OK, body
    handles = {
        claim["class_handle"]
        for row in body["rows"]
        for claim in row["claims"]
        if claim["rule_id"] == FORMAT_RULE and row["name"].endswith(".csv")
    }
    assert handles == {handle}


def test_an_answer_on_a_class_of_claims_waits_for_rerun_in_its_own_words(pool) -> None:
    """What waits for Re-run names a class of claims as one, never as a set of copies."""
    from ingence.workspace.state import answers_a_run_reads

    claims = [claim for claim in _claims(pool, FORMAT_RULE) if claim["name"].endswith(".csv")]
    handle = _handle(pool, claims[0]["claim_id"])
    _answer(pool, handle["handle"], "wrong")
    rows = [row for change in answers_a_run_reads(pool.path) for row in change.rows]
    assert rows, "the answer waits for the next inference"
    said = " ".join(rows)
    assert "copies" not in said
    # In plain words: the value's label, never the rule's identifier.
    assert FORMAT_RULE not in said and handle["value"].replace("_", " ") in said and "Wrong" in said
