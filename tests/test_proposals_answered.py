"""A vocabulary pack and a person's miscellaneous files, accepted or refused.

Both are raised as questions, and the answer is read on a Record target of each's own
(``pack:<domain>``, ``miscellaneous:<entry_id>``). The Dashboard offers accept and refuse on the
question's row and writes the answer there through the one answer route. The question rows are
built here as the engine's predicates spell them. Every name is invented.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from ingence.api.routes import decide as decide_routes
from ingence.api.routes.pages import _decorate
from ingence.api.routes.proposals import proposal_of, shipped_domains
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("proposals")
    pool = base / "pool"
    (pool / "brisa_keep").mkdir(parents=True)
    (pool / "brisa_keep" / "trell.txt").write_text("a\n")
    (pool / "brisa_keep" / "zib.txt").write_text("b\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project.path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def _decide(project: Path, body: dict[str, Any]) -> Any:
    with open_catalog(project) as catalog:
        context = decide_routes.DecisionContext(catalog, project)
        return decide_routes.decide_target(
            context,
            decide_routes.Decision.from_body(body),
            actor="vorn",
            occurred_at="2026-09-29T00:00:00Z",
        )


def test_a_question_names_the_proposal_and_the_target_its_answer_goes_to(project: Path) -> None:
    assert "microscopy" in shipped_domains()
    with open_catalog(project) as catalog:
        pack = proposal_of(
            catalog, {"predicate": "domain_pack_would_read", "raised": "load the microscopy pack"}
        )
        kept = proposal_of(
            catalog,
            {
                "predicate": "miscellaneous_files_to_approve",
                "raised": "brisa_keep: miscellaneous files",
            },
        )
        folder = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'brisa_keep'"
        ).fetchone()[0]
        # Neither a pack that does not ship, nor a folder the run lacks, nor another question.
        assert (
            proposal_of(
                catalog, {"predicate": "domain_pack_would_read", "raised": "load the zib pack"}
            )
            is None
        )
        assert (
            proposal_of(
                catalog,
                {
                    "predicate": "miscellaneous_files_to_approve",
                    "raised": "nowhere: miscellaneous files",
                },
            )
            is None
        )
        assert (
            proposal_of(catalog, {"predicate": "other", "raised": "load the microscopy pack"})
            is None
        )
    assert pack is not None
    assert kept is not None
    assert pack["target"] == "pack:microscopy"
    assert kept["target"] == f"miscellaneous:{folder}"
    assert [word["word"] for word in pack["words"]] == ["accept", "refuse"]


def test_accept_and_refuse_are_a_confirm_and_a_reject_on_the_proposal_s_target(
    project: Path,
) -> None:
    accepted = _decide(project, {"target": "pack:microscopy", "word": "accept", "note": "yes"})
    assert (accepted.kind.value, accepted.target) == ("confirm", "pack:microscopy")
    with open_catalog(project) as catalog:
        folder = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'brisa_keep'"
        ).fetchone()[0]
    refused = _decide(project, {"target": f"miscellaneous:{folder}", "word": "refuse"})
    assert refused.kind.value == "reject"
    assert json.loads(refused.payload)["source"]["page"] == "review"
    # The row's decision in force is the one on the proposal's target.
    row = {
        "target": "question:domain_pack_would_read_this_pool:load the microscopy pack",
        "answers_at": "pack:microscopy",
    }
    _decorate([row], project)
    assert row["in_force"]["word"] in ("confirm", "accept")


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"target": "pack:zib", "word": "accept"}, "no vocabulary pack"),
        ({"target": "miscellaneous:entry_nothing", "word": "accept"}, "no folder"),
        ({"target": "pack:microscopy", "word": "maybe"}, "not an answer here"),
        ({"target": "pack:microscopy"}, "accept or refuse"),
    ],
)
def test_what_cannot_be_answered_is_refused_and_nothing_written(
    project: Path, body: dict[str, Any], said: str
) -> None:
    before = len(read_events(project))
    with pytest.raises(decide_routes.AnswerRefusedError, match=said):
        _decide(project, body)
    assert len(read_events(project)) == before


def test_the_question_file_asks_a_proposal_on_its_own_target_with_its_own_words(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The question file carries a proposal's own target, not its question target, so an answer
    imported from it is written where the engine reads it."""
    from ingence.api.routes import pages
    from ingence.api.routes.proposals import PROPOSAL_WORDS
    from ingence.api.v1 import context_for
    from ingence.api.v1.question_file import _questions

    proposal_row = {
        "target": "question:domain_pack_would_read_this_pool:load the microscopy pack",
        "answers_at": "pack:microscopy",
        "proposal": {
            "kind": "pack",
            "target": "pack:microscopy",
            "names": "microscopy",
            "words": PROPOSAL_WORDS["pack"],
        },
        "raised": "load the microscopy pack",
        "words": [{"word": "cannot_say", "meaning": "counted, and read as neither"}],
    }
    other_row = {"target": "question:zib:vorn", "raised": "vorn", "words": []}
    monkeypatch.setattr(pages, "page", lambda *args, **kwargs: {"rows": [proposal_row, other_row]})
    asked = _questions(context_for(project, settled=None, job_running=False), 10)
    assert [row["target"] for row in asked] == ["pack:microscopy", "question:zib:vorn"]
    assert [word["word"] for word in asked[0]["words"]] == ["accept", "refuse"]
