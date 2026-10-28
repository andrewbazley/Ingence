"""What a person's answers on ``pack:<domain>`` mean, given that every domain section loads.

Every shipped domain section loads in every project, level with the universal sections. So the
engine never proposes a pack and never adds one by default. The answers a person gave on
``pack:<domain>`` (:func:`~ingence.record.targets.pack_target`) keep meaning:

- A confirm ("load the X pack", or ``init --domain X``) is a prior for that domain. Where a
  folder's own files do not say which domain a shared spelling is read in, the prior does
  (:class:`~ingence.dictionary.discover.DomainBinder`).
- A reject is a veto. The domain's sections are left out of the project's Dictionary
  (:meth:`~ingence.dictionary.store.DictionaryStore.for_project`), whoever wrote the reject and
  whenever.

The settings ``dictionary.domain_packs``, ``domain_packs_named`` and ``files_to_accept_a_pack``
stay declared and are read by nothing (as the retired year span is), so a project written by an
earlier engine still opens.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ingence.core.schema import RecordEventKind

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = [
    "INIT_ACTOR",
    "PACK_RULE",
    "PackAnswer",
    "domain_priors",
    "one_reading",
    "pack_answers",
    "vetoed_domains",
]


#: The rule whose question an answer on ``pack:<domain>`` was given to before 0.4.0, still named
#: in an answer's payload (``question``) so an older engine reads the answer as it did.
PACK_RULE = "domain_pack_would_read_this_pool"


@dataclass(frozen=True, slots=True)
class PackAnswer:
    """The latest answer on one domain: its kind, the question it answered, the run it was in."""

    kind: RecordEventKind
    #: The value of the question it answered (``load the X pack``, ``keep the X pack``), or None
    #: where its payload names none.
    question: str | None
    against_run_id: str | None
    actor: str = ""
    occurred_at: str = ""


#: The Record read once per call of :func:`one_reading`, kept by project.
_ONE_READING: ContextVar[dict[str, Any] | None] = ContextVar("pack_answers", default=None)


@contextmanager
def one_reading() -> Iterator[None]:
    """Read the Record's pack answers once for everything asked inside (a page's request)."""
    token = _ONE_READING.set({})
    try:
        yield
    finally:
        _ONE_READING.reset(token)


def pack_answers(project: Path) -> dict[str, PackAnswer]:
    """The latest answer on each domain, by domain, with the question it answered."""
    held = _ONE_READING.get()
    key = str(project)
    if held is not None and key in held:
        return dict(held[key])
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.targets import PACK_PREFIX

    prefix = f"{PACK_PREFIX}:"
    found: dict[str, PackAnswer] = {}
    for target, event in latest_by_target(read_events(project)).items():
        if not target.startswith(prefix):
            continue
        try:
            payload = json.loads(event.payload or "{}")
        except ValueError:
            payload = {}
        question = payload.get("question") if isinstance(payload, dict) else None
        found[target[len(prefix) :]] = PackAnswer(
            kind=event.kind,
            question=str(question).rsplit(":", 1)[-1] if question else None,
            against_run_id=event.against_run_id,
            actor=event.actor,
            occurred_at=event.occurred_at,
        )
    if held is not None:
        held[key] = found
    return dict(found)


#: The actor ``init --domain`` writes its prior as, with no question.
INIT_ACTOR = "init"


def _load_question(domain: str) -> str:
    """The question whose yes is a prior and whose no is a veto: "load the X pack"."""
    return f"load the {domain} pack"


def domain_priors(project: Path) -> frozenset[str]:
    """The domains a person said to load, by the latest answer on ``pack:<domain>``.

    A prior only for a yes to "load the X pack", or ``init --domain``'s own confirm (which
    names no question): a 0.3.3 yes to "keep the X pack" kept a pack that read nothing and says
    nothing about the project's field, and a "stand the X pack over the core" answer was about
    standing, which is retired. A prior, not a loading: everything loads. Where a folder's files
    do not decide which domain a shared spelling is read in, a prior for exactly one of the
    contending domains does.
    """
    return frozenset(
        name
        for name, answer in pack_answers(project).items()
        if answer.kind is RecordEventKind.CONFIRM
        and (
            answer.question == _load_question(name)
            or (answer.question is None and answer.actor == INIT_ACTOR)
        )
    )


def vetoed_domains(project: Path) -> frozenset[str]:
    """The domains a person refused: left out of the Dictionary, by the latest answer.

    A veto only for a no to "load the X pack", or a refusal naming no question (an older
    sheet's plain refuse): a 0.3.3 no to "stand the X pack over the core" kept the pack loaded and
    took away its standing, which is retired, and a no to "keep the X pack" stopped loading a pack
    that read nothing, which everything now reads. Neither leaves a domain's words out.
    """
    return frozenset(
        name
        for name, answer in pack_answers(project).items()
        if answer.kind is RecordEventKind.REJECT
        and (answer.question == _load_question(name) or answer.question is None)
    )
