"""Stored answers on ``pack:<domain>`` are read by the question they answered.

A yes to "load the X pack" or ``init --domain``'s confirm is a prior. A no to "load the X pack" or
a refusal naming no question is a veto. A 0.3.3 answer on "keep the X pack" or "stand the X pack
over the core" is neither. Every name here is invented."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.dictionary.packs.proposals import INIT_ACTOR, domain_priors, vetoed_domains
from ingence.dictionary.store import DictionaryStore
from ingence.record.log import append_event
from ingence.record.targets import pack_target

LOAD = "question:domain_pack_would_read_this_pool:load the {domain} pack"
KEEP = "question:domain_pack_would_read_this_pool:keep the {domain} pack"
STAND = "question:domain_pack_would_read_this_pool:stand the {domain} pack over the core"


def _project(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    pool.mkdir()
    return Project.create(tmp_path / "project", created_at="t", roots=[pool]).path


def _answer(
    project: Path, domain: str, kind: RecordEventKind, question: str | None, actor: str = "kelt"
) -> None:
    payload = {"verdict": "accept" if kind is RecordEventKind.CONFIRM else "refuse"}
    if question is not None:
        payload["question"] = question.format(domain=domain)
    append_event(
        project,
        kind=kind,
        actor=actor,
        occurred_at="2026-01-01T00:00:00Z",
        target=pack_target(domain),
        against_run_id="",
        payload=payload,
    )


@pytest.mark.parametrize(
    ("question", "actor", "prior"),
    [
        (LOAD, "kelt", True),
        (KEEP, "kelt", False),
        (STAND, "kelt", False),
        (None, INIT_ACTOR, True),
        (None, "kelt", False),
    ],
)
def test_which_confirms_are_priors(
    tmp_path: Path, question: str | None, actor: str, prior: bool
) -> None:
    project = _project(tmp_path)
    _answer(project, "microscopy", RecordEventKind.CONFIRM, question, actor)
    assert (domain_priors(project) == frozenset({"microscopy"})) is prior
    assert vetoed_domains(project) == frozenset()


@pytest.mark.parametrize(
    ("question", "veto"),
    [(LOAD, True), (KEEP, False), (STAND, False), (None, True)],
)
def test_which_rejects_are_vetoes(tmp_path: Path, question: str | None, veto: bool) -> None:
    project = _project(tmp_path)
    _answer(project, "biochemistry", RecordEventKind.REJECT, question)
    assert (vetoed_domains(project) == frozenset({"biochemistry"})) is veto
    assert domain_priors(project) == frozenset()
    store = DictionaryStore.for_project(project)
    assert (store.look_up("western") is None) is veto


def test_a_stand_over_the_core_refusal_leaves_the_domain_loaded(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _answer(project, "microscopy", RecordEventKind.REJECT, STAND)
    store = DictionaryStore.for_project(project)
    assert store.look_up("confocal") is not None
    assert vetoed_domains(project) == frozenset()


def test_init_writes_its_prior_with_its_own_actor_and_no_question(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ingence.cli.main import app
    from ingence.record.log import read_events

    pool = tmp_path / "pool"
    pool.mkdir()
    result = CliRunner().invoke(
        app, ["init", str(tmp_path / "project"), "--root", str(pool), "--domain", "microscopy"]
    )
    assert result.exit_code == 0, result.output
    (event,) = [e for e in read_events(tmp_path / "project") if e.target.startswith("pack:")]
    assert (event.kind, event.actor, event.target) == (
        RecordEventKind.CONFIRM,
        INIT_ACTOR,
        "pack:microscopy",
    )
    assert domain_priors(tmp_path / "project") == frozenset({"microscopy"})
