"""An answered row stays only while its answer waits for Re-run.

An answered row stays in its place, with its answer, until the next run reads the answer. Then the
row leaves its tab. Every page of questions asks for ``answered=waiting``: the answers the run has
not read. The test answers one row of every kind on the invented pool of ``test_review_pages``,
runs the rules again and asks each page again. It checks three things:

1. Before the run reads the answer, a row stays: on Conclusion samples, Groupings, Proposed pairs,
   Programs and the ranked questions, with its answer, and the answer says it waits for Re-run.
2. After the run has read the answer, the row is gone from each of them. It is still served where
   the answered rows are asked for (``answered=show``): nothing is deleted, and a person can still
   ask.
3. The ranked route counts what it left out (``answered_left_out``), and its summary says how
   many answered questions it serves.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes import pages
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.infer.run import run_inference
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.project import Project

PERSON = "Dana Whitfield"
RERUN = "run_20260101T000003Z_333333333333"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    from test_review_pages import _project

    return _project(tmp_path)


def _rows(project: Path, kind: str, **mode: bool) -> list[dict[str, Any]]:
    with open_catalog(project) as catalog:
        found = pages.page(catalog, project, kind, limit=1_000_000, **mode)
    return list(found["rows"])


def _programs(project: Path, **mode: bool) -> list[dict[str, Any]]:
    with open_catalog(project) as catalog:
        return list(pages.programs(catalog, project, **mode)["programs"])


def _ranked(project: Path, **query: str) -> dict[str, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    status, body = dispatch(
        context,
        "questions/ranked",
        {name: [value] for name, value in {"limit": "1000", **query}.items()},
    )
    assert status == HTTPStatus.OK, body
    return dict(body)


def _answer(project: Path, target: str, word: str, moment: str) -> None:
    with open_catalog(project) as catalog:
        decide_target(
            DecisionContext(catalog, project),
            Decision(target=target, word=word),
            actor=PERSON,
            occurred_at=moment,
        )


def _targets(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["target"]) for row in rows}


def test_an_answered_row_stays_until_the_run_reads_it_and_then_leaves(project: Project) -> None:
    path = project.path
    claim = next(row for row in _rows(path, "claims", answered=True) if not row.get("grouped_by"))
    grouping = next(row for row in _rows(path, "classes", answered=True) if not row.get("proposal"))
    pair = _rows(path, "candidates", answered=True)[0]
    program = next(row for row in _programs(path, answered=True) if row.get("target"))
    ranked = _ranked(path, answered="show")["questions"]
    # No pack question is asked, so a question about a file's contents stands in.
    contents = next(q for q in ranked if q["kind"] == "question")
    chosen = {
        "claims": (claim["target"], "correct"),
        "classes": (grouping["target"], "one_thing"),
        "candidates": (pair["target"], "unrelated"),
        "programs": (program["target"], "correct"),
        "ranked": (contents["target"], "placeholder"),
    }
    for index, (target, word) in enumerate(chosen.values()):
        _answer(path, target, word, f"2026-10-13T12:0{index}:00Z")

    def listed(mode: dict[str, bool]) -> dict[str, set[str]]:
        found = {
            kind: _targets(_rows(path, kind, **mode))
            for kind in ("claims", "classes", "candidates")
        }
        found["programs"] = _targets(_programs(path, **mode))
        asked = "waiting" if mode.get("waiting_only") else "show"
        found["ranked"] = {q["key"] for q in _ranked(path, answered=asked)["questions"]}
        return found

    waiting = {"answered": True, "waiting_only": True}
    shown = {"answered": True}
    # 1. The run has not read these answers: every row stays, and the ranked answer says so.
    before = listed(waiting)
    for kind, (target, _) in chosen.items():
        assert target in before[kind], kind
    held = next(
        q for q in _ranked(path, answered="waiting")["questions"] if q["key"] == contents["key"]
    )
    assert held["answer"]["waits_for_rerun"] is True and held["answered"] is True
    # Left out by default.
    assert claim["target"] not in _targets(_rows(path, "claims"))

    # 2. The rules run again and read them: each row is gone from the waiting pages...
    run_inference(project, run_id=RERUN, started_at="2026-01-01T00:00:03Z")
    after = listed(waiting)
    for kind, (target, _) in chosen.items():
        assert target not in after[kind], kind
    # ...and the rows still there are still served to a page that asks for every answered row.
    for kind in ("claims", "classes", "candidates"):
        every = _targets(_rows(path, kind, **shown))
        assert chosen[kind][0] in every, kind
    # 3. The ranked route counts what it left out, and what it serves of the answered.
    again = _ranked(path, answered="waiting")
    every = _ranked(path, answered="show")
    assert again["summary"]["answered"] == 0
    assert again["summary"]["answered_left_out"] == every["summary"]["answered"]
    assert every["summary"]["answered"] >= 0


def test_a_new_answer_after_a_run_waits_again_and_a_bad_word_is_refused(project: Project) -> None:
    from ingence.api.v1 import context_for, dispatch

    path = project.path
    claim = next(row for row in _rows(path, "claims", answered=True) if not row.get("grouped_by"))
    _answer(path, claim["target"], "correct", "2026-10-13T12:00:00Z")
    run_inference(project, run_id=RERUN, started_at="2026-01-01T00:00:03Z")
    assert claim["target"] not in _targets(_rows(path, "claims", answered=True, waiting_only=True))
    # Said again after the run, it is above the run's watermark: the row is back, answered anew.
    _answer(path, claim["target"], "wrong", "2026-10-13T13:00:00Z")
    back = {
        str(row["target"]): row for row in _rows(path, "claims", answered=True, waiting_only=True)
    }
    assert claim["target"] in back
    assert back[claim["target"]]["in_force"]["word"] == "wrong"
    context = context_for(path, settled=None, job_running=False)
    status, body = dispatch(context, "questions/ranked", {"answered": ["sometimes"]})
    assert status == HTTPStatus.BAD_REQUEST
    assert body["error"]["parameter"] == "answered"
