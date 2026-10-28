"""Going back to an earlier run, deleting nothing (schema 1.22).

"Restore this run" returns the project to what it believed when a past run was made. That run
becomes current again, the Dashboard's settings file takes the values in force then, and every
decision made since is withdrawn by one ``withdraw`` event that names them and is appended to the
Record. Nothing is deleted: the withdrawn decisions stay in the Record, in every export and on the
History page, and replay skips them. Restoring a later run is itself a restore. Its withdrawal
names the earlier withdrawal, which brings back exactly what that one withdrew. So a restore and
then a later restore bring every decision back, and the Record only grew.

The engine never withdraws on its own. So:

- this module is the only one that builds a ``withdraw`` event (a test reads the source tree);
- :func:`restore` refuses an actor that is not a person (the engine's own names), because going
  back in time is a person's act;
- :func:`restore` writes only the exact preview the person confirmed (:attr:`RestorePlan.digest`).
  A decision appended or a setting committed since changes the digest, and the preview is shown
  again;
- ``record.log.append_event`` refuses a withdrawal that names no preview.

A run's Record watermark (``RunRecord.record_watermark``, schema 1.22) says which decisions the
run read. A run published before 1.22 has none. Then the latest checkpoint recorded at that run
gives the watermark, and failing that the decisions made before the run began give it (an
approximation, flagged in the preview).

The rule parameters return too. The Rulebook's parameters that the run used are compared with
those in force, and one appended project revision of ``override`` changes returns every parameter
that differs. That is the only change the Rulebook's append-only rule allows. No revision file is
removed or rewritten, so restoring the later run returns the parameters the same way. An override
cannot return a rule that exists on only one side, or whose earlier value its parameter now
refuses. The preview names such a rule (``rules_kept``) and it stays as it is.

The run itself says which parameters it used. The Rulebook is rebuilt at each prefix of the
project's revisions, and the highest prefix whose digest is the run's own ``rulebook_hash`` is the
one the run used. A revision written by hand between a commit and the run counts. The shipped
Rulebook's digest alone cannot tell an inference made before the project had revisions of its own
from a census or a dive, which always cite the shipped Rulebook. For that digest the checkpoints
say which revision was in force then: the checkpoint before the run, or the one the first commit
after it started from. Where no prefix gives the run's digest (a revision file edited since, or
another engine's shipped rules), what the run used cannot be said. The rules that differ from the
checkpoints' estimate are then named in ``rules_kept`` and nothing is returned. An earlier engine
version recorded the rules that are now the shipped revision 4 as the project's revision 1. A run
made then has its Rulebook rebuilt that way from the retired file, where it stands or where
``ingence fix`` set it aside, so restoring it returns its parameters as for any other run.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.core.ids import canonical_json, digest_text
from ingence.core.schema import RecordEvent, RecordEventKind
from ingence.record.log import append_event, in_force, read_events
from ingence.record.targets import restore_target
from ingence.storage.commit import (
    CurrentPointer,
    Manifest,
    manifest_path,
    read_pointer,
    run_path,
    write_pointer,
)
from ingence.workspace.drafts import WRITING
from ingence.workspace.settings_overlay import flatten, read_layers, write_dashboard_settings

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "NOT_A_PERSON",
    "RestorePlan",
    "RestoreRefusedError",
    "restore",
    "restore_plan",
]

#: Names that are never a person going back in time: the engine's own.
NOT_A_PERSON = frozenset({"ingence", "engine", "system"})


class RestoreRefusedError(ValueError):
    """A restore that is not written, and why, in words a person reads."""


@dataclass(frozen=True)
class RestorePlan:
    """What restoring one run would change, computed and written nowhere."""

    #: The run that becomes current, and the run current now.
    run_id: str
    current_run_id: str | None
    #: The highest Record sequence the run read, and how that was known.
    watermark: int
    watermark_from: str
    #: The events the withdrawal names: decisions made since, and earlier withdrawals whose
    #: effect this restore undoes.
    withdraw: tuple[str, ...]
    #: Decisions set aside and brought back, counted by kind.
    set_aside: dict[str, int]
    brought_back: dict[str, int]
    #: Dashboard settings that change back: dotted name -> [value now, value then].
    settings: dict[str, list[Any]]
    #: The checkpoint whose settings are restored, where one was recorded at that run.
    checkpoint_id: str | None
    #: Rules whose parameters differ from the run's and cannot be returned by an override (the
    #: rule is on one side only, or its parameter now refuses the earlier value): left as they are.
    rules_kept: tuple[str, ...] = field(default=())
    #: Rule parameters that return: rule -> parameter -> [value now, value then].
    rules_reverted: dict[str, dict[str, list[Any]]] = field(default_factory=dict)
    #: A digest of everything above: what the person confirms, and what :func:`restore` checks.
    digest: str = ""

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


def _run_facts(project: Path, run_id: str) -> tuple[int | None, str | None, str | None]:
    """The run's Record watermark (None before 1.22), its start and the digest of the Rulebook it
    recorded, read from its ``run`` table.

    Only these two columns are read, so a run written before a column existed reads as well as
    one written today.
    """
    import pyarrow.parquet as pq

    directory = run_path(project, run_id) / "run"
    fragments = sorted(directory.glob("*.parquet")) if directory.is_dir() else []
    if not fragments:
        message = f"no published run {run_id!r} in this project"
        raise RestoreRefusedError(message)
    for fragment in fragments:
        table = pq.read_table(fragment)
        names = set(table.column_names)
        for row in table.to_pylist():
            if row.get("run_id") != run_id:
                continue
            watermark = row.get("record_watermark") if "record_watermark" in names else None
            started = row.get("started_at")
            book = row.get("rulebook_hash")
            return (
                int(watermark) if watermark is not None else None,
                str(started) if started else None,
                str(book) if book else None,
            )
    message = f"the run table of {run_id!r} does not describe it"
    raise RestoreRefusedError(message)


def _instant(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.UTC)


def _watermark(
    project: Path, run_id: str, everything: tuple[RecordEvent, ...]
) -> tuple[int, str, Any]:
    """The highest sequence the run read, how that was known, and the settings' checkpoint then.

    The checkpoint is the latest one recorded before the run began. A commit records the run
    current when it was made and the settings after its change, so a checkpoint naming the run
    describes the first change made after it, never the run itself. Its Record sequence is
    likewise later than the run, so it never stands in for a watermark.
    """
    from ingence.workspace.checkpoints import list_checkpoints

    recorded, started, _ = _run_facts(project, run_id)
    began = _instant(started)
    before_run = [
        checkpoint
        for checkpoint in list_checkpoints(project)
        if began is not None
        and (made := _instant(checkpoint.created_at)) is not None
        and made <= began
    ]
    checkpoint = before_run[-1] if before_run else None
    if recorded is not None:
        return recorded, "the run's own record", checkpoint
    if began is None:
        message = f"run {run_id!r} states no start, so which decisions it read cannot be said"
        raise RestoreRefusedError(message)
    before = [
        event.sequence
        for event in everything
        if (moment := _instant(event.occurred_at)) is not None and moment <= began
    ]
    return max(before, default=0), "the decisions made before the run began", checkpoint


def _kinds(events: list[RecordEvent]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for event in events:
        counted[event.kind.value] = counted.get(event.kind.value, 0) + 1
    return dict(sorted(counted.items()))


def _withdrawal_for(
    everything: tuple[RecordEvent, ...], target: set[str], *, withdrawn_by: str
) -> tuple[str, ...]:
    """The events one new withdrawal must name so that exactly ``target`` is in force.

    Decisions in force that are not in the target are named; so are withdrawals in force whose
    effect removed something the target holds (withdrawing a withdrawal brings back what it
    withdrew). Whatever that brings back beyond the target is named too, until the result is the
    target exactly.
    """
    current = {event.event_id for event in in_force(everything)}
    by_id = {event.event_id: event for event in everything}
    named = set(current - target)
    for event in everything:
        if event.kind is not RecordEventKind.WITHDRAW:
            continue
        detail = json.loads(event.payload or "{}")
        if set(map(str, detail.get("withdrawn", ()))) & (target - current):
            named.add(event.event_id)
    for _ in range(len(everything) + 1):
        trial = RecordEvent(
            event_id=withdrawn_by,
            sequence=max((event.sequence for event in everything), default=0) + 1,
            kind=RecordEventKind.WITHDRAW,
            actor="preview",
            occurred_at="2026-01-01T00:00:00Z",
            scope=everything[0].scope,
            target="restore:preview",
            against_run_id="preview",
            payload=canonical_json({"withdrawn": sorted(named)}),
        )
        result = {event.event_id for event in in_force((*everything, trial))}
        extra = result - target
        if not extra:
            if target - result:
                missing = sorted(target - result)
                message = f"no withdrawal brings back {', '.join(missing[:3])}"
                raise RestoreRefusedError(message)
            break
        named |= extra
    return tuple(sorted(named, key=lambda name: by_id[name].sequence if name in by_id else 0))


def restore_plan(project: Path, run_id: str) -> RestorePlan:
    """What restoring ``run_id`` would change: computed from the project, written nowhere."""
    pointer = read_pointer(project)
    everything = read_events(project, held=False, withdrawn=True, moved=False)
    watermark, known_from, checkpoint = _watermark(project, run_id, everything)
    # A note decides nothing, so a restore never withdraws one: it stays on its page.
    deciding = tuple(event for event in everything if event.kind is not RecordEventKind.NOTE)
    then = {event.event_id for event in in_force([e for e in deciding if e.sequence <= watermark])}
    now = {event.event_id for event in in_force(deciding)}
    withdraw = (
        _withdrawal_for(deciding, then, withdrawn_by="preview") if deciding and then != now else ()
    )
    by_id = {event.event_id: event for event in everything}
    settings: dict[str, list[Any]] = {}
    from ingence.workspace.checkpoints import _in_force, list_checkpoints

    layers = read_layers(project)
    # What the Dashboard's file held when the run was made: the checkpoint's, or nothing where no
    # commit came before the run (the file is written only by commits).
    then_dashboard = checkpoint.dashboard_settings if checkpoint is not None else {}
    then_effective = (
        flatten(checkpoint.settings) if checkpoint is not None else _in_force(layers.hand)
    )
    current = flatten(layers.effective)
    for name in sorted(set(flatten(layers.dashboard)) | set(flatten(then_dashboard))):
        if current.get(name) != then_effective.get(name):
            settings[name] = [current.get(name), then_effective.get(name)]
    later = [
        c
        for c in list_checkpoints(project)
        if checkpoint is None or c.sequence > checkpoint.sequence
    ]
    used = _revision_used(project, _run_facts(project, run_id)[2])
    estimate = _revision_then(checkpoint, later)
    reverted: dict[str, dict[str, list[Any]]]
    if used == _UNSAID:
        # What the run used cannot be said: name what differs, and return nothing.
        differing, unreturnable = _rule_parameters(project, estimate)
        reverted, kept = {}, tuple(sorted({*differing, *unreturnable}))
    else:
        reverted, kept = _rule_parameters(project, estimate if used == _SHIPPED else used)
    plan = RestorePlan(
        run_id=run_id,
        current_run_id=pointer.run_id if pointer else None,
        watermark=watermark,
        watermark_from=known_from,
        withdraw=withdraw,
        set_aside=_kinds([by_id[e] for e in sorted(now - then)]),
        brought_back=_kinds([by_id[e] for e in sorted(then - now)]),
        settings=settings,
        checkpoint_id=checkpoint.checkpoint_id if checkpoint is not None else None,
        rules_kept=kept,
        rules_reverted=reverted,
    )
    body = plan.as_payload()
    body.pop("digest")
    body["record_sequences"] = sorted(event.sequence for event in everything)
    return RestorePlan(**{**plan.as_payload(), "digest": digest_text(canonical_json(body))[:32]})


def restore(
    project: Path, *, run_id: str, actor: str, previewed: str, now: str | None = None
) -> dict[str, Any]:
    """Restore ``run_id`` exactly as the preview the person confirmed says; return what was done.

    Refuses a name that is not a person's, a preview that no longer holds, and a restore that
    would change nothing. Holds the workspace's writing lock throughout, so a commit waits for it.
    """
    who = " ".join(actor.split())
    if not who:
        message = "a restore names the person going back"
        raise RestoreRefusedError(message)
    if who.casefold() in NOT_A_PERSON:
        message = f"{who} is not a person; only a person goes back to an earlier run"
        raise RestoreRefusedError(message)
    stamp = now or dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    with WRITING:
        plan = restore_plan(project, run_id)
        if plan.digest != previewed:
            message = (
                "the project changed since that preview was shown (a decision or a setting); "
                "look at the preview again before restoring"
            )
            raise RestoreRefusedError(message)
        if (
            not plan.withdraw
            and not plan.settings
            and not plan.rules_reverted
            and plan.current_run_id == run_id
        ):
            message = f"{run_id} is current and nothing would change"
            raise RestoreRefusedError(message)
        returned = {
            rule_id: {name: values[1] for name, values in parameters.items()}
            for rule_id, parameters in plan.rules_reverted.items()
        }
        if returned:
            # Checked before anything is written, as a commit checks its draft: a revision the
            # Rulebook would refuse stops the restore with nothing half applied.
            from ingence.workspace.revisions import check_overrides

            refused = check_overrides(project, returned)
            if refused:
                said = "; ".join(f"{rule}: {why}" for rule, why in sorted(refused.items()))
                message = f"the rule parameters of that run cannot be returned ({said})"
                raise RestoreRefusedError(message)
        if plan.settings:
            # Written first, and validated as it is written: a settings file that would not load
            # stops the restore before anything reaches the Record.
            from ingence.workspace.checkpoints import list_checkpoints

            chosen = next(
                (c for c in list_checkpoints(project) if c.checkpoint_id == plan.checkpoint_id),
                None,
            )
            write_dashboard_settings(
                project,
                chosen.dashboard_settings if chosen is not None else {},
                provenance=dict.fromkeys(
                    plan.settings, f"restored by {who} on {stamp[:10]} to {run_id}"
                ),
                replace=True,
            )
        revision: int | None = None
        if returned:
            from ingence.workspace.revisions import write_revision

            revision = write_revision(
                project,
                returned,
                summary=f"Restore by {who} on {stamp[:10]} to {run_id}: parameters as they were",
            )
        written: RecordEvent | None = None
        if plan.withdraw:
            written = append_event(
                project,
                kind=RecordEventKind.WITHDRAW,
                actor=who,
                occurred_at=stamp,
                target=restore_target(run_id),
                against_run_id=plan.current_run_id or run_id,
                payload={
                    "withdrawn": list(plan.withdraw),
                    "restored_to": run_id,
                    "previewed": plan.digest,
                    "watermark": plan.watermark,
                    "watermark_from": plan.watermark_from,
                },
            )
        if plan.current_run_id != run_id:
            manifest = Manifest.model_validate_json(
                manifest_path(project, run_id).read_text(encoding="utf-8")
            )
            write_pointer(
                project,
                CurrentPointer(run_id=run_id, manifest_digest=manifest.digest(), updated_at=stamp),
            )
    return {
        "restored_to": run_id,
        "previous_run": plan.current_run_id,
        "withdrawal": written.event_id if written is not None else None,
        "withdrawn": len(plan.withdraw),
        "settings_restored": sorted(plan.settings),
        "rules_kept": list(plan.rules_kept),
        "rules_reverted": sorted(plan.rules_reverted),
        "revision": revision,
    }


#: What :func:`_revision_used` answers where only the shipped Rulebook's digest is the run's, and
#: where no prefix of the project's revisions gives it.
_SHIPPED = -1
_UNSAID = -2


def _revision_used(project: Path, recorded: str | None) -> int:
    """The highest project revision whose rebuilt Rulebook is the one the run recorded.

    ``_SHIPPED`` where only the shipped Rulebook's is (the checkpoints then say which revision was
    in force), and ``_UNSAID`` where none is, or the run recorded none.
    """
    if recorded is None:
        return _UNSAID
    from ingence.rulebook.load import RuleDeclarationError, load_project_revisions, load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook
    from ingence.storage.layout import RULEBOOK_DIR

    load_builtins()
    shipped = load_revisions()
    existing = load_project_revisions(project / RULEBOOK_DIR)
    for number in sorted({item.number for item in existing}, reverse=True):
        try:
            book = effective_rulebook(
                shipped, project=tuple(item for item in existing if item.number <= number)
            )
        except RuleDeclarationError:
            continue
        if book.digest == recorded:
            return number
    if effective_rulebook(shipped).digest == recorded:
        return _SHIPPED
    return _before_the_fold(project, recorded, shipped=shipped, existing=existing)


def _before_the_fold(
    project: Path, recorded: str, *, shipped: tuple[Any, ...], existing: tuple[Any, ...]
) -> int:
    """The revision used by a run made while the retired file was revision 1, or ``_UNSAID``.

    An earlier engine version composed the rules now shipped as revision 4 as the project's
    revision 1, the retired file, after revisions 0 to 3. Its runs recorded that Rulebook's digest
    (rules at ``3+d1``, the copy rule at ``1+p1``). This function rebuilds it the same way, from
    the file where it stands or where ``ingence fix`` set it aside, with each later revision of the
    project's own in turn. A number found is the same number now: the retired file's choices
    compose at its number, and every later revision keeps its own.
    """
    from ingence.rulebook.load import RuleDeclarationError, load_project_revision
    from ingence.rulebook.resolve import effective_rulebook
    from ingence.rulebook.retired import FILE_NAME, SET_ASIDE_DIRECTORY, SHIPPED_BEFORE
    from ingence.storage.layout import RULEBOOK_DIR

    earlier = tuple(revision for revision in shipped if revision.number <= SHIPPED_BEFORE)
    if effective_rulebook(earlier).digest == recorded:
        return _SHIPPED
    directory = project / RULEBOOK_DIR
    held = next(
        (
            path
            for path in (directory / FILE_NAME, directory / SET_ASIDE_DIRECTORY / FILE_NAME)
            if path.is_file()
        ),
        None,
    )
    if held is None:
        return _UNSAID
    try:
        retired = load_project_revision(held)
    except RuleDeclarationError:
        return _UNSAID
    later = [item for item in existing if item.number > retired.number]
    numbers: set[int] = {retired.number, *(int(item.number) for item in later)}
    for number in sorted(numbers, reverse=True):
        try:
            book = effective_rulebook(
                earlier, project=(retired, *(item for item in later if item.number <= number))
            )
        except RuleDeclarationError:
            continue
        if book.digest == recorded:
            return number
    return _UNSAID


def _revision_then(checkpoint: Any, later: list[Any]) -> int | None:
    """The project revision in force when the run was made, or None where it cannot be said.

    The checkpoint before the run records it. With none, the first commit after the run started
    from it: that commit's revision, less the one it appended where it changed a rule. With no
    commit at all nothing was committed, and nothing is returned.
    """
    if checkpoint is not None:
        return int(checkpoint.project_revision)
    if not later:
        return None
    first = later[0]
    return int(first.project_revision) - (1 if first.changed_rules else 0)


def _rule_parameters(
    project: Path, revision: int | None
) -> tuple[dict[str, dict[str, list[Any]]], tuple[str, ...]]:
    """Each rule parameter that differs between ``revision`` and now, as [now, then], and the
    rules whose difference an override cannot return."""
    if revision is None:
        return {}, ()
    from ingence.rulebook import project_rulebook
    from ingence.rulebook.load import load_project_revisions, load_revisions
    from ingence.rulebook.render import value_in_force
    from ingence.rulebook.resolve import effective_rulebook
    from ingence.storage.layout import RULEBOOK_DIR

    now = project_rulebook(project)
    existing = load_project_revisions(project / RULEBOOK_DIR)
    then = effective_rulebook(
        load_revisions(), project=tuple(item for item in existing if item.number <= revision)
    )

    def values(rule: Any) -> dict[str, Any]:
        return {spec.name: value_in_force(spec) for spec in rule.parameters}

    earlier = {rule.rule_id: values(rule) for rule in then}
    current = {rule.rule_id: values(rule) for rule in now}
    reverted: dict[str, dict[str, list[Any]]] = {}
    kept: set[str] = set()
    for rule_id in sorted(set(earlier) | set(current)):
        before, after = earlier.get(rule_id), current.get(rule_id)
        if before is None or after is None:
            if before != after and (before or after):
                kept.add(rule_id)
            continue
        changed = {
            name: [after.get(name), before[name]]
            for name in sorted(before)
            if name in after and after[name] != before[name]
        }
        if set(before) != set(after):
            kept.add(rule_id)
        if changed:
            reverted[rule_id] = changed
    if reverted:
        from ingence.workspace.revisions import check_overrides

        returned = {
            rule_id: {name: pair[1] for name, pair in parameters.items()}
            for rule_id, parameters in reverted.items()
        }
        for rule_id in check_overrides(project, returned):
            kept.add(rule_id)
            reverted.pop(rule_id, None)
    return reverted, tuple(sorted(kept))
