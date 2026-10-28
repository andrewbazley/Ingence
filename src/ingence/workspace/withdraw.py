"""Withdrawing one answer about a word, by a person at the Dashboard.

This is the second way to make a withdrawal, beside going back to an earlier run
(:mod:`ingence.workspace.restore`). It is the narrowest way. An answer carried in from another
project is withdrawable one by one (``record/imported.py``), and that needs a withdrawal that
names one answer and not everything since a run. A person asks for it from the Words section (and
from the History of a word). Nobody else asks for it: the engine never withdraws, and an import
writes answers and never withdrawals.

The answer is named by its sequence and its digest as the page read it
(:func:`~ingence.record.log.answer_digest`), so a stale page cannot withdraw something else. Only
an answer about a word can be named (:data:`~ingence.record.log.WITHDRAWABLE_PREFIXES`): what a
word means, how a spelling that a capital divides reads, two spellings called unrelated, and a
sense or one spelling of it turned off. The Record's one writer
(:func:`~ingence.record.log.append_event`) checks all of this again. It refuses in words a note, a
withdrawal, an answer about anything else, and an answer already withdrawn.

Nothing is deleted. The answer stays in the Record and in every export, and replay skips it
(``read_events(withdrawn=True)`` still shows it). Withdrawing the latest answer on a word leaves
the answer before it, if there is one, in force again, as replaying the Record without it does.
A withdrawal is a decision like any other, so it waits for Re-run behind its badge
(``workspace/state.py`` counts it) and a run's watermark places it. Going back to an earlier run
still works over it: a restore that returns to a time before the answer withdraws the withdrawal
too, which brings the answer back (:func:`~ingence.record.log.withdrawn_ids`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.schema import RecordEvent, RecordEventKind
from ingence.record.log import WithdrawRefusedError, append_event, read_events
from ingence.storage.commit import read_pointer

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["withdraw_answer"]


def withdraw_answer(
    project: Path, *, actor: str, sequence: int, digest: str, occurred_at: str
) -> RecordEvent:
    """Withdraw the answer at ``sequence`` as ``actor``, if it is still the answer ``digest`` names.

    Written against the run the project's pointer names now. Raises
    :class:`~ingence.record.log.WithdrawRefusedError` in words for a project with no run, an
    answer the Record does not hold under that digest (``stale``), and every other refusal the
    Record's writer makes (:func:`~ingence.record.log._check_answer_withdrawal`).
    """
    pointer = read_pointer(project)
    if pointer is None:
        message = "this project has no run yet, so there is no answer to withdraw"
        raise WithdrawRefusedError(message)
    everything = read_events(project, held=False, withdrawn=True, moved=False)
    found = next((event for event in everything if event.sequence == sequence), None)
    if found is None:
        message = (
            "the Record holds no answer with that sequence: the page is out of date, so look at "
            "it again before withdrawing"
        )
        raise WithdrawRefusedError(message, stale=True)
    return append_event(
        project,
        kind=RecordEventKind.WITHDRAW,
        actor=actor,
        occurred_at=occurred_at,
        # Under the answer's own target, so the Record's history of a word shows the withdrawal
        # beside the answer it took back.
        target=found.target,
        against_run_id=pointer.run_id,
        payload={
            "withdrawn": [found.event_id],
            "answer": {"sequence": sequence, "digest": digest},
        },
        scope=found.scope,
    )
