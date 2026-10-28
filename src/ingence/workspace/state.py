"""What the header shows: how much is uncommitted, and every change that waits for a re-run.

Both are recomputed every time they are asked, from the drafts, the checkpoints, the Record and
the current run. Nothing here is stored, so there is nothing to fall out of step. Every change that
changes a reading goes on one list. :func:`changes_since` reads every such change, of every kind,
and ``rerun.pending`` keeps those that no run has followed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ingence.workspace.checkpoints import list_checkpoints, read_checkpoints
from ingence.workspace.drafts import uncommitted
from ingence.workspace.rerun import (
    STEPS_A_RUN_DOES,
    Change,
    Pending,
    RunDone,
    Step,
    instant,
    kind_of_run,
    needs_of_settings,
    pending,
)

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable
    from pathlib import Path

    from ingence.core.schema import RecordEvent

__all__ = [
    "answers_a_run_reads",
    "changes_since",
    "line_of_descent",
    "pending_rerun",
    "state",
    "unreadable_line",
    "waiting_line",
]


#: The run-record columns a run's kind is read from.
_RUN_COLUMNS = (
    "run_id",
    "parent_run_id",
    "started_at",
    "profile",
    "rebind_spellings_json",
    "components_json",
    "record_watermark",
)

#: A published run never changes, so each run's reading is kept for the life of the process, by
#: its folder. The header asks on every page load and every save, and a project's line of descent
#: is read once and not every time. A run is a few names, so nothing is dropped.
_RUNS_READ: dict[str, tuple[RunDone, str | None]] = {}


def _read_run(run_directory: Path) -> tuple[RunDone, str | None] | None:
    """A run's start, the steps it did, its watermark and its parent, from its run record alone.

    This costs about 2 ms. Opening the run's catalog costs two orders of magnitude more (about
    210 ms on a development machine), so the catalog is never opened to say what waits. The result
    is None where the run record cannot be read.
    """
    import json

    import pyarrow.parquet as pq

    key = str(run_directory)
    if key in _RUNS_READ:
        return _RUNS_READ[key]
    parts = sorted((run_directory / "run").glob("*.parquet"))
    read: tuple[RunDone, str | None] | None = None
    try:
        present = set(pq.read_schema(parts[0]).names) if parts else set()
        rows = pq.read_table(
            parts, columns=[name for name in _RUN_COLUMNS if name in present]
        ).to_pylist()
        row = rows[0] if rows else {}
        components = json.loads(row.get("components_json") or "[]")
        spellings = json.loads(row.get("rebind_spellings_json") or "null")
    except (OSError, ValueError):
        rows = []
    if rows:
        kind = kind_of_run(
            profile=str(row.get("profile") or ""),
            whole_rebind=spellings is None,
            applied_rules=isinstance(components, list)
            and any(
                isinstance(item, dict) and item.get("kind") == "predicate" for item in components
            ),
        )
        read = (
            RunDone(
                started_at=str(row.get("started_at") or ""),
                steps=STEPS_A_RUN_DOES[kind],
                run_id=str(row.get("run_id") or run_directory.name),
                # What a partial rebind read again, so it settles a change to those words.
                spellings=frozenset(str(word).casefold() for word in spellings)
                if kind == "partial rebind" and isinstance(spellings, list)
                else None,
                # The Record it read, so a decision it read is known followed (schema 1.22).
                watermark=int(row["record_watermark"])
                if isinstance(row.get("record_watermark"), int)
                else None,
            ),
            row.get("parent_run_id"),
        )
    if read is not None:
        _RUNS_READ[key] = read
    return read


def line_of_descent(project: Path, *, back_to: dt.datetime | None = None) -> list[RunDone]:
    """The current run and each run it descends from, oldest first.

    The line is followed back through each run's parent until a run that began before ``back_to``,
    a run that cannot be read, or the first run. A run that began before ``back_to`` can have done
    nothing for a change made then, because it read the Record before the change was in it. A run
    in the same second is followed past: its watermark may show that it read a decision made in
    that second.
    """
    from ingence.storage.commit import read_pointer, run_path

    pointer = read_pointer(project)
    found: list[RunDone] = []
    seen: set[str] = set()
    next_run = pointer.run_id if pointer is not None else None
    while next_run and next_run not in seen:
        seen.add(next_run)
        read = _read_run(run_path(project, next_run))
        if read is None:
            break
        run, parent = read
        found.append(run)
        began = instant(run.started_at)
        if back_to is not None and began is not None and began < back_to:
            break
        next_run = parent
    return found[::-1]


def pending_rerun(project: Path) -> Pending | None:
    """Every change waiting for a re-run, or None where none is or the project has no run yet."""
    from ingence.storage.commit import read_pointer

    if read_pointer(project) is None:
        return None
    changes = changes_since(project)
    if not changes:
        return None
    times = [instant(change.made) for change in changes]
    # The runs that began before every change can have done nothing for any of them. A change
    # whose time cannot be read waits whatever runs there are, so it sets no limit.
    earliest = None if None in times else min(when for when in times if when is not None)
    return pending(changes, line_of_descent(project, back_to=earliest))


def changes_since(project: Path) -> list[Change]:
    """Every change that changes a reading, whether or not a run has followed it yet.

    The changes are a commit's settings, the latest answer on every target that the names or the
    rules read, and each pack installed or removed on the Dictionary page. ``rerun.pending`` keeps
    those that wait. No pack waits to be accepted. Every domain section loads from the first reading
    of the names, so nothing of that kind waits.
    """
    return [
        *_commits(project),
        *answers_a_run_reads(project),
        *packs_changed_on_the_page(project),
    ]


def _commits(project: Path) -> list[Change]:
    """Each commit that needs something run, as a setting per value it changed that needs it."""
    found: list[Change] = []
    for item in list_checkpoints(project):
        if not item.needs:
            continue
        rows = [name for name in item.changed_settings if needs_of_settings([name])]
        rows += [f"{rule}'s settings" for rule in item.changed_rules]
        found.append(
            Change(
                key=item.checkpoint_id,
                kind="setting",
                rows=tuple(rows) or (item.message,),
                made=item.created_at,
                needs=tuple(Step(need) for need in item.needs),
            )
        )
    return found


#: A person's answer that the next run reads: its kind, its words, and the steps that read it.
#: A pack changes how names are read. A group's approval and a folder's answer are read by the
#: rules. Each waits, as a commit does, until a run begun after it has done its steps. The table is
#: keyed by the target's prefix, so a new kind of answer is one row. The answers about words, and
#: the approval of a proposed group on its question, are read below (:func:`_word_change`,
#: :func:`_proposal_change`).
_ANSWERS_A_RUN_READS: dict[str, tuple[str, str, tuple[Step, ...]]] = {
    "pack": ("pack", "{} words", (Step.REBIND, Step.INFERENCE)),
    "group": ("answer", "the group {}", (Step.INFERENCE,)),
    "miscellaneous": ("answer", "a folder's miscellaneous files", (Step.INFERENCE,)),
    "program": ("answer", "a program folder", (Step.INFERENCE,)),
    # The next run leaves the experiment off "no analysis found" (or puts it back).
    "analysed_elsewhere": ("answer", "the experiment {}", (Step.INFERENCE,)),
}

#: What each answer above says, where "accepted" and "refused" are not its words: a person said an
#: experiment is analysed elsewhere, or no longer says so.
_SAID_AS: dict[str, tuple[str, str]] = {
    "analysed_elsewhere": (
        "marked as analyzed elsewhere",
        "no longer marked as analyzed elsewhere",
    ),
}

#: Answers named by the folder their event recorded (``payload["path"]``), not by the entry their
#: target holds: a person reads a folder's path, never its identifier.
_NAMED_BY_PATH = frozenset({"analysed_elsewhere"})

#: What a person declared that the rules read: a workflow (``record/workflow.py``) and where
#: another machine's folder lies in a root (``infer/references.py``), each a ``define`` on its
#: target. The table is keyed like the one above.
_DECLARED_A_RUN_READS: dict[str, tuple[str, str, tuple[Step, ...]]] = {
    "workflow": ("answer", "the workflow {} added", (Step.INFERENCE,)),
    "prefix": ("answer", "another computer's folder {} matched", (Step.INFERENCE,)),
}

#: What the dive reads: whether an extension's files are worth reading (``worth_reading`` in
#: ``record/verdicts.py``), and its words by the kind stored. A dive reads the share, so the page
#: gives its commands.
_WORTH_READING_SAID = {
    "confirm": "yes",
    "reject": "no",
    "annotate": "never",
}

#: Answers on a set of copies and on a proposed pair. Inference reads a copies answer and any pair
#: answer (``inference_reads`` in ``record/answers.py``), so each waits for the rules like the
#: answers above, said as the page shows the word.
_COPIES_AND_PAIRS = {"class": "a set of copies", "candidate": "a suggested pair"}

#: What the names are read again and the rules applied again for, where a word's meaning moved.
_NAMES_THEN_RULES = (Step.REBIND, Step.INFERENCE)


def _every_pack(project: Path) -> bool:
    """Whether the project loads every shipped domain: always. The setting ``domain_packs`` stays
    declared and nothing reads it."""
    from ingence.storage.layout import PROJECT_FILE

    return (project / PROJECT_FILE).is_file()


def _loads_the_pack(event: RecordEvent) -> bool:
    """Whether a pack answer is a yes to "load the X pack": an acceptance, which changes how the
    names are read wherever it is given (a "keep" on a project loading every pack changes nothing).
    """
    from ingence.rulebook.predicates.packs import pack_value

    try:
        question = json.loads(event.payload or "{}").get("question")
    except (ValueError, AttributeError):
        return False
    domain = event.target.partition(":")[2]
    return isinstance(question, str) and question.rsplit(":", 1)[-1] == pack_value(domain)


def answers_a_run_reads(project: Path) -> list[Change]:
    """The latest answer on each target a run reads, each a change with its kind and words.

    Every answer about what a word means waits for Re-run: an answer to a word's question, a word
    defined, and a sense or a spelling turned off or back on. So does the approval of experiments as
    one group on its question (``question:<proposing rule>:<group>``), which the rules read.

    An answer taken back waits too. A later decision that a run does not read (``cannot_say``)
    displaces one it did read (``latest_by_target`` in ``record/log.py``), so the reading changes
    from the moment it was displaced. A note decides nothing and displaces nothing.
    """
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import read_events

    held: list[frozenset[str]] = []

    def proposals() -> frozenset[str]:
        if not held:
            held.append(proposals_the_rules_read(project))
        return held[0]

    latest: dict[str, RecordEvent] = {}
    refused_before: set[str] = set()
    read_before: dict[str, Change] = {}
    displaced_by: dict[str, RecordEvent] = {}
    for event in sorted(read_events(project), key=lambda item: (item.sequence, item.event_id)):
        if event.kind in (RecordEventKind.NOTE, RecordEventKind.WITHDRAW):
            continue
        before = latest.get(event.target)
        latest[event.target] = event
        if event.kind is RecordEventKind.REJECT:
            refused_before.add(event.target)
        if _read_change(event, proposals) is not None:
            displaced_by.pop(event.target, None)
        elif before is not None and (earlier := _read_change(before, proposals)) is not None:
            read_before[event.target] = earlier
            displaced_by[event.target] = event
    found: list[Change] = []
    every: list[bool] = []
    # In the order they were made, so a kind's rows read as they were given.
    for target, event in sorted(latest.items(), key=lambda item: item[1].sequence):
        if target.startswith("pack:") and event.kind is RecordEventKind.CONFIRM:
            # Keeping a pack changes nothing when the project already loads every pack. No names
            # wait to be read again for it, unless it was refused before.
            if not every:
                every.append(_every_pack(project))
            if every[0] and target not in refused_before and not _loads_the_pack(event):
                continue
        change = _read_change(event, proposals)
        if change is None and target in displaced_by:
            taken_back = displaced_by[target]
            earlier = read_before[target]
            change = replace(
                earlier,
                key=f"answer:{taken_back.sequence}",
                rows=tuple(f"{row} (undone)" for row in earlier.rows),
                made=taken_back.occurred_at,
                sequence=taken_back.sequence,
            )
        if change is not None:
            found.append(change)
    found.extend(_answers_taken_back_one_by_one(project, proposals))
    return found


def _answers_taken_back_one_by_one(
    project: Path, proposals: Callable[[], frozenset[str]]
) -> list[Change]:
    """Each answer a person withdrew on its own, as a change waiting for Re-run.

    A withdrawal waits like any answer: the reading an answer gave stays in the run until the names
    and the rules are read again without it. The answer is no longer in ``read_events``, so its
    change is made from the answer itself, said as taken back, and placed by the withdrawal's own
    sequence. An answer that no run ever read leaves nothing to wait for, because the highest
    watermark in the line of descent never reached it. Withdrawing it cancels it, and the badge
    goes down.
    """
    from ingence.record.log import answer_withdrawals

    taken = answer_withdrawals(project)
    if not taken:
        return []
    read_to = max(
        (run.watermark for run in line_of_descent(project) if run.watermark is not None),
        default=None,
    )
    found: list[Change] = []
    for withdrawal, answer in taken:
        if read_to is not None and answer.sequence > read_to:
            continue
        change = _read_change(answer, proposals)
        if change is None:
            continue
        found.append(
            replace(
                change,
                key=f"answer:{withdrawal.sequence}",
                rows=tuple(f"{row} (undone)" for row in change.rows),
                made=withdrawal.occurred_at,
                sequence=withdrawal.sequence,
            )
        )
    return found


def _recorded_path(event: RecordEvent) -> str | None:
    """The folder path an answer's event recorded, or None."""
    import json

    try:
        said = json.loads(event.payload or "{}")
    except ValueError:
        return None
    path = said.get("path") if isinstance(said, dict) else None
    return str(path) if path else None


def _read_change(event: RecordEvent, proposals: Callable[[], frozenset[str]]) -> Change | None:
    """The change one decision makes to what a run reads, or None where no run reads it."""
    from ingence.core.schema import RecordEventKind

    decided = (RecordEventKind.CONFIRM, RecordEventKind.REJECT)
    prefix, _, rest = event.target.partition(":")
    if prefix in _COPIES_AND_PAIRS:
        return _copies_or_pair_change(event, prefix)
    if prefix in _ANSWERS_A_RUN_READS:
        if event.kind not in decided:
            return None
        kind, named, steps = _ANSWERS_A_RUN_READS[prefix]
        said, unsaid = _SAID_AS.get(prefix, ("accepted", "refused"))
        verdict = said if event.kind is RecordEventKind.CONFIRM else unsaid
        if prefix in _NAMED_BY_PATH:
            rest = _recorded_path(event) or rest
        return _change(event, kind, f"{named.format(rest)} {verdict}", steps)
    if prefix in _DECLARED_A_RUN_READS:
        if event.kind is not RecordEventKind.DEFINE:
            return None
        kind, named, steps = _DECLARED_A_RUN_READS[prefix]
        return _change(event, kind, named.format(rest), steps)
    if prefix == "extension":
        word = _WORTH_READING_SAID.get(event.kind.value)
        if word is None or not rest:
            return None
        return _change(
            event, "answer", f"read contents of {rest} files: {word}", (Step.DIVE, Step.INFERENCE)
        )
    if prefix == "split":
        return _split_change(event, rest)
    if prefix == "question" and event.kind in decided:
        return _proposal_change(event, rest, proposals())
    return _word_change(event, prefix, rest)


def _copies_or_pair_change(event: RecordEvent, prefix: str) -> Change | None:
    """A copies answer or a pair answer, where inference reads it, in the page's own word."""
    import json

    from ingence.record.answers import inference_reads
    from ingence.record.verdicts import shown_on

    if not inference_reads(event):
        return None
    said = json.loads(event.payload or "{}")
    word = str(said.get("verdict") or "") if isinstance(said, dict) else ""
    shown = shown_on(word, event.target) if word else event.kind.value
    if isinstance(said, dict) and said.get("of") == "claims":
        # A class of claims answered on its handle is said as what it is about, in plain words:
        # the value as the pages say it, and the rule's own sentence where the answer carries it
        # (the answer route reads the plain-words table).
        value = str(said.get("value") or "").replace("_", " ")
        rule = str(said.get("rule_said") or "").strip()
        about = f'every file read as "{value}"' + (f" ({rule.rstrip('.')})" if rule else "")
        return _change(event, "answer", f"{about}: {shown}", (Step.INFERENCE,))
    return _change(event, "answer", f"{_COPIES_AND_PAIRS[prefix]}: {shown}", (Step.INFERENCE,))


def _change(
    event: RecordEvent,
    kind: str,
    row: str,
    needs: tuple[Step, ...],
    spellings: frozenset[str] = frozenset(),
) -> Change:
    return Change(
        key=f"answer:{event.sequence}",
        kind=kind,
        rows=(row,),
        made=event.occurred_at,
        needs=needs,
        spellings=spellings,
        sequence=event.sequence,
    )


def _word_change(event: RecordEvent, prefix: str, rest: str) -> Change | None:
    """A change to what a word means: an answer naming its meaning, or a sense turned off or on.

    A meaning named (an answer to its question, or a word defined) changes the names holding that
    word, so only those are read again (the partial rebind). A sense or a spelling turned off or
    back on changes the vocabulary itself, which a partial rebind refuses, so it asks for every
    name read again.
    """
    from ingence.core.schema import RecordEventKind
    from ingence.record.questions import NAMING_KINDS
    from ingence.record.targets import surface_named_by

    if prefix in ("question", "term") and event.kind in NAMING_KINDS:
        try:
            said = json.loads(event.payload or "{}")
        except (TypeError, ValueError):
            return None
        raised = str(said.get("raised") or "").strip() if isinstance(said, dict) else ""
        meaning = str(said.get("answer") or "").strip() if isinstance(said, dict) else ""
        if not raised or not meaning:
            return None
        kind = "answer" if prefix == "question" else "word defined"
        row = f'"{raised}" means {meaning}'
        return _change(event, kind, row, _NAMES_THEN_RULES, frozenset({raised.casefold()}))
    if prefix == "term" and event.kind is RecordEventKind.REJECT:
        # A person's "not a word" waits like any other answer. The word's definition, if it had
        # one, is taken back (``latest_by_target`` lets the rejection displace it), so the names
        # holding it are read again and the rules applied again.
        word = rest.partition(" below ")[0].strip()
        if not word:
            return None
        return _change(
            event,
            "answer",
            f'"{word}" is not a word',
            _NAMES_THEN_RULES,
            frozenset({word.casefold()}),
        )
    if prefix not in ("sense", "surface") or event.kind not in (
        RecordEventKind.CONFIRM,
        RecordEventKind.REJECT,
    ):
        return None
    kind = "word off" if event.kind is RecordEventKind.REJECT else "word on"
    if prefix == "sense":
        return _change(event, kind, rest, _NAMES_THEN_RULES)
    named = surface_named_by(f"{prefix}:{rest}")
    if named is None:
        return None
    term, spelling = named
    return _change(event, kind, f'"{spelling}" as {term}', _NAMES_THEN_RULES)


def _split_change(event: RecordEvent, spelling: str) -> Change | None:
    """A person's reading of a spelling a capital divides, one word or its pieces.

    The name reader reads it (``dictionary/case_reading.py``), so the names holding the spelling
    are read again, then the rules. The wording comes from the reading the engine takes from the
    event.
    """
    from ingence.dictionary.case_reading import Reading, answered_cases

    reading = answered_cases([event]).get(spelling.strip().casefold())
    if reading is None or not spelling:
        return None
    said = "one word" if reading is Reading.WHOLE else "separate words"
    return _change(
        event,
        "answer",
        f'"{spelling}" read as {said}',
        _NAMES_THEN_RULES,
        frozenset({spelling.casefold()}),
    )


def _proposal_change(event: RecordEvent, rest: str, asked: frozenset[str]) -> Change | None:
    """An accept or a refusal of a proposed group on its question, where the rules read it."""
    from ingence.core.schema import RecordEventKind

    rule, _, group = rest.partition(":")
    if rule not in asked or not group:
        return None
    verdict = "accepted" if event.kind is RecordEventKind.CONFIRM else "refused"
    return _change(event, "answer", f"the group {group} {verdict}", (Step.INFERENCE,))


#: The proposing rules each Rulebook reads approvals of, by the project's rulebook folder as
#: listed (names, sizes, times): composing a Rulebook costs about 300 ms, and the header asks
#: on every page load and every save.
_PROPOSALS_READ: dict[tuple[object, ...], frozenset[str]] = {}


def proposals_the_rules_read(project: Path) -> frozenset[str]:
    """The rules whose proposals the rules in force read a person's approval of.

    Every rule in force that reads approved groups and whose ``proposal_rule`` names one is
    included: an accept on ``question:<that rule>:<group>`` is what it reads (``infer/run.py``).
    A Rulebook that does not load reads nothing, and inference would not run either.
    """
    from ingence.storage.layout import RULEBOOK_DIR

    directory = project / RULEBOOK_DIR
    try:
        listed = tuple(
            sorted(
                (path.name, path.stat().st_size, path.stat().st_mtime_ns)
                for path in directory.iterdir()
            )
        )
    except OSError:
        listed = ()
    key = (str(directory), listed)
    if key not in _PROPOSALS_READ:
        _PROPOSALS_READ[key] = _proposals_read(project)
    return _PROPOSALS_READ[key]


def _proposals_read(project: Path) -> frozenset[str]:
    from ingence.infer.evaluate import parameters_in_force
    from ingence.rulebook import project_rulebook
    from ingence.rulebook.registry import PREDICATES, Reads, UndeclaredParameterError, reads_of

    try:
        book = project_rulebook(project)
    except Exception:  # a Rulebook that does not load: the header still opens
        return frozenset()
    named: set[str] = set()
    for rule in book.rules:
        if reads_of(PREDICATES.declaration(rule.predicate)) is not Reads.APPROVED_GROUP:
            continue
        # Only a rule whose predicate declares a proposal rule names one. The group resolutions
        # read approvals too, and declare none.
        try:
            named.add(str(parameters_in_force(rule).get("proposal_rule") or ""))
        except UndeclaredParameterError:
            continue
    return frozenset(named) - {""}


def packs_changed_on_the_page(project: Path) -> list[Change]:
    """Each laboratory pack installed or removed on the Dictionary page, as a change.

    A pack changes how names are read. The page writes ``settings.yaml`` and a line of the pack log,
    never a checkpoint or a Record event, so without this function the change would never wait and
    Re-run would never read the names again. Each line waits, as a pack answer does, for the names
    read again and then the rules, until a run begun after it has done both. A line that cannot be
    read is left out, as an unreadable checkpoint is.
    """
    from ingence.dictionary.laboratory_packs import PACK_CHANGES

    path = project / PACK_CHANGES
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found: list[Change] = []
    for number, text in enumerate(lines, start=1):
        try:
            line = json.loads(text)
        except ValueError:
            continue
        if not isinstance(line, dict) or line.get("act") not in ("install", "remove"):
            continue
        done = "added" if line["act"] == "install" else "removed"
        found.append(
            Change(
                key=f"pack-change:{number}",
                kind="pack",
                rows=(f"words from {line.get('pack')} {done}",),
                made=line.get("at"),
                needs=_NAMES_THEN_RULES,
            )
        )
    return found


def state(project: Path) -> dict[str, Any]:
    """The header's reading: the uncommitted count per kind, what waits for a re-run, and the
    checkpoint files that could not be read and were left out."""
    waiting = pending_rerun(project)
    return {
        "uncommitted": uncommitted(project),
        "pending_rerun": waiting.as_payload() if waiting is not None else None,
        "unreadable_checkpoints": read_checkpoints(project)[1],
    }


def unreadable_line(project: Path) -> str | None:
    """One plain line naming the checkpoint files that could not be read, or None."""
    names = read_checkpoints(project)[1]
    if not names:
        return None
    records = "saved commit" if len(names) == 1 else "saved commits"
    return (
        f"Ingence could not read {len(names)} {records} in workspace/checkpoints "
        f"({', '.join(names)}) and left {'it' if len(names) == 1 else 'them'} out. "
        f"So it does not know what Re-run must apply for {'it' if len(names) == 1 else 'them'}."
    )


def waiting_line(project: Path) -> str | None:
    """The one line a session opens with where changes wait (it starts nothing), or None.

    Nothing starts by itself: Re-run on the Dashboard applies what waits, when the person chooses.
    """
    waiting = pending_rerun(project)
    if waiting is None:
        return None
    return f"Waiting for Re-run: {waiting.banner().removesuffix(' since the last scan.')}."
