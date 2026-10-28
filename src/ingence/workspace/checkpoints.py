"""A commit: the drafts applied, and one small record of what the project believed then.

A checkpoint records the four inputs from which every interpretation can be reproduced: the Index,
the Rulebook, the Dictionary and the Record. It stores which run was current (the Index), the
effective Rulebook's digest and project revision, the Dictionary's fingerprint, the Record's
highest sequence number, and the settings in force. That is everything needed to say what the
project believed at that moment. A restore (``restore``) reads it, so nothing has to be
reconstructed.

A checkpoint is stored append-only, as one small JSON file in ``workspace/checkpoints/``. The file
is named by the checkpoint's sequence and created exclusively (``O_EXCL``, the Record's own
locking primitive). A checkpoint is never rewritten.

:func:`commit` does the following:

1. It refuses an empty commit, a draft that carries an error, and an author who is not named.
2. It checks both drafts before it writes either: the settings laid over the files in force as
   one ``Settings``, and each rule parameter composed against the Rulebook. So a refusal leaves
   nothing half applied.
3. It lays the settings into ``settings.dashboard.yaml`` and appends a project Rulebook revision
   for the rule parameters. ``settings.yaml`` is never opened for writing: a commit leaves it
   byte-identical.
4. It records the checkpoint, with what the commit needs run as steps (``rerun.Step``), and clears
   the applied drafts.

A commit reads what it changed from its drafts against the last checkpoint, never from the files
before and after the write. A commit stopped after the settings were written and before its
checkpoint leaves the new value in force and no record of it. If the next commit compared the files,
it would find nothing changed and need nothing run. Measured against the last checkpoint's settings
(or, before the first checkpoint, ``settings.yaml`` alone), the same drafts need the same steps
however many times they are committed.

Committing does not run anything: a commit that silently started a two-hour dive would be a trap.
The Re-run button counts what waits, and a person starts it (``rerun``). Opening a session starts
nothing either.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ingence.core.ids import canonical_json, digest_text
from ingence.workspace import WORKSPACE_DIR, WORKSPACE_VERSION
from ingence.workspace.drafts import WRITING, clear_draft, read_draft, settings_errors
from ingence.workspace.rerun import Step, needs_of_settings
from ingence.workspace.revisions import check_overrides, overrides_of, write_revision
from ingence.workspace.settings_overlay import (
    flatten,
    read_layers,
    write_dashboard_settings,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["Checkpoint", "commit", "list_checkpoints", "read_checkpoints"]


@dataclass(frozen=True)
class Checkpoint:
    """What the project believed at one moment: its four inputs and its settings."""

    checkpoint_id: str
    sequence: int
    created_at: str
    actor: str
    message: str
    #: The Index: the run the pointer named.
    run_id: str | None
    #: The Record: every event up to and including this sequence is in force at this checkpoint.
    record_sequence: int
    #: The Rulebook: its digest and its last project revision.
    rulebook_digest: str
    project_revision: int
    #: The Dictionary: the composed vocabulary's fingerprint.
    dictionary_fingerprint: str
    #: The settings in force (``settings.yaml`` with the Dashboard's file laid over it), and the
    #: Dashboard's file alone.
    settings: dict[str, Any]
    dashboard_settings: dict[str, Any]
    #: What this commit changed: the drafted settings (dotted names) whose value in force differs
    #: from the last checkpoint's, and the rules whose parameters moved.
    changed_settings: tuple[str, ...] = field(default=())
    changed_rules: tuple[str, ...] = field(default=())
    #: What must run before the current run reflects it: ``rerun.Step`` values, in order.
    needs: tuple[str, ...] = field(default=())
    parent: str | None = None
    version: str = WORKSPACE_VERSION


def _directory(project: Path) -> Path:
    return project / WORKSPACE_DIR / "checkpoints"


#: A checkpoint's file is named by its sequence in six digits. Nothing else in the folder is a
#: checkpoint: a shared drive can put a ``._000001.json`` beside each file, and reading every
#: ``*.json`` would stop the Dashboard opening on the first of those.
_CHECKPOINT_NAME = re.compile(r"(\d{6})\.json")


def _numbered(project: Path) -> list[tuple[int, Path]]:
    """Every name in the folder that is a checkpoint's, by sequence, readable or not."""
    directory = _directory(project)
    if not directory.is_dir():
        return []
    found = []
    for path in directory.iterdir():
        named = _CHECKPOINT_NAME.fullmatch(path.name)
        if named is not None:
            found.append((int(named.group(1)), path))
    return sorted(found)


def read_checkpoints(project: Path) -> tuple[list[Checkpoint], list[str]]:
    """Every checkpoint that can be read, oldest first, and the file names of those that cannot.

    A checkpoint that cannot be read is left out and named, and never stops the read. A file emptied
    by a write that was stopped, or damaged, would otherwise stop the header, the session's start
    and every commit after it.
    """
    readable: list[Checkpoint] = []
    unreadable: list[str] = []
    for _, path in _numbered(project):
        try:
            loaded = json.loads(path.read_text("utf-8"))
            if not isinstance(loaded, dict):
                message = "a checkpoint is an object"
                raise TypeError(message)
            for name in ("needs", "changed_settings", "changed_rules"):
                loaded[name] = tuple(loaded.get(name, ()))
            readable.append(Checkpoint(**loaded))
        except (OSError, ValueError, TypeError):
            unreadable.append(path.name)
    return readable, unreadable


def list_checkpoints(project: Path) -> list[Checkpoint]:
    """Every checkpoint that can be read, oldest first (:func:`read_checkpoints` names the rest)."""
    return read_checkpoints(project)[0]


def _state(project: Path) -> dict[str, Any]:
    """The project's current four inputs and settings, read, never written."""
    from ingence.dictionary.store import DictionaryStore
    from ingence.record.log import sequences_present
    from ingence.rulebook import project_rulebook
    from ingence.storage.commit import read_pointer

    rulebook = project_rulebook(project)
    pointer = read_pointer(project)
    layers = read_layers(project)
    present = sequences_present(project)
    return {
        "run_id": pointer.run_id if pointer else None,
        "record_sequence": max(present) if present else 0,
        "rulebook_digest": rulebook.digest,
        "project_revision": rulebook.project_revision,
        "dictionary_fingerprint": DictionaryStore.for_project(project).fingerprint(),
        "settings": layers.effective,
        "dashboard_settings": layers.dashboard,
    }


def _write(project: Path, fields: dict[str, Any]) -> Checkpoint:
    """Record a checkpoint under the next sequence no file holds, readable or not.

    The sequence is numbered from every checkpoint name in the folder, so a file that cannot be
    read still holds its number. Where another writer takes the number first, the next one is
    tried. Each try takes a higher number than the last, so the loop ends.
    """
    directory = _directory(project)
    directory.mkdir(parents=True, exist_ok=True)
    sequence = 0
    while True:
        taken = [number for number, _ in _numbered(project)]
        sequence = max([sequence, *taken]) + 1
        existing = list_checkpoints(project)
        body = {
            **fields,
            "sequence": sequence,
            "parent": existing[-1].checkpoint_id if existing else None,
        }
        body["checkpoint_id"] = "checkpoint_" + digest_text(canonical_json(body))[:24]
        checkpoint = Checkpoint(**body)
        try:
            descriptor = os.open(
                directory / f"{sequence:06d}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
            )
        except FileExistsError:
            continue
        break
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(checkpoint), sort_keys=True, indent=2, default=list))
        handle.flush()
        os.fsync(handle.fileno())
    return checkpoint


def _in_force(values: dict[str, Any]) -> dict[str, Any]:
    """Every setting's value in force over ``values``, defaults included, by dotted name."""
    from pydantic import ValidationError

    from ingence.core.settings import Settings

    try:
        return flatten(Settings.model_validate(values).model_dump(mode="json"))
    except ValidationError:
        return flatten(values)


def _in_force_at_last_checkpoint(project: Path) -> dict[str, Any]:
    """The settings in force when the last commit was recorded, or, before any, the hand file's."""
    recorded = list_checkpoints(project)
    if recorded:
        return _in_force(recorded[-1].settings)
    return _in_force(read_layers(project).hand)


def _changed(
    drafted: dict[str, Any], before: dict[str, Any], after: dict[str, Any]
) -> tuple[str, ...]:
    """The drafted settings (dotted names) whose value in force differs between two readings."""
    return tuple(sorted(key for key in flatten(drafted) if before.get(key) != after.get(key)))


def commit(project: Path, *, actor: str, message: str, now: str | None = None) -> Checkpoint:
    """Apply the settings and rule-parameter drafts, and record a checkpoint.

    Holds :data:`drafts.WRITING` throughout, so a draft saved meanwhile waits for it and is kept,
    and two commits never run at once.
    """
    with WRITING:
        return _commit(project, actor=actor, message=message, now=now)


def _commit(project: Path, *, actor: str, message: str, now: str | None) -> Checkpoint:
    who = " ".join(actor.split())
    if not who:
        text = "a commit names who is committing"
        raise ValueError(text)
    said = " ".join(message.split()) or "Changes"
    settings_draft = read_draft(project, "settings")
    parameter_draft = read_draft(project, "rule_parameters")
    if settings_draft.empty and parameter_draft.empty:
        text = "nothing is drafted, so there is nothing to commit"
        raise ValueError(text)
    # Both drafts are checked afresh, against the files as they stand now, before either is written.
    errors = {
        **settings_errors(project, settings_draft.values),
        **check_overrides(project, parameter_draft.values),
    }
    if errors:
        text = "; ".join(f"{name}: {why}" for name, why in sorted(errors.items()))
        raise ValueError(f"the draft holds values that would not be accepted ({text})")

    before = _in_force_at_last_checkpoint(project)
    stamp = now or datetime.now(tz=UTC).isoformat(timespec="seconds")
    if not settings_draft.empty:
        write_dashboard_settings(
            project,
            settings_draft.values,
            provenance=dict.fromkeys(
                flatten(settings_draft.values), f"set by {who} on {stamp[:10]}: {said}"
            ),
        )
    changed_rules = tuple(overrides_of(parameter_draft.values))
    if changed_rules:
        write_revision(
            project, parameter_draft.values, summary=f"Dashboard commit by {who}: {said}"
        )
    after = _state(project)
    changed = _changed(settings_draft.values, before, _in_force(after["settings"]))
    needs = needs_of_settings(changed)
    if changed_rules and Step.INFERENCE not in needs:
        needs = (*needs, Step.INFERENCE)
    checkpoint = _write(
        project,
        {
            **after,
            "created_at": stamp,
            "actor": who,
            "message": said,
            "changed_settings": changed,
            "changed_rules": changed_rules,
            "needs": tuple(step.value for step in needs),
            "version": WORKSPACE_VERSION,
        },
    )
    clear_draft(project, "settings", if_unchanged=settings_draft)
    clear_draft(project, "rule_parameters", if_unchanged=parameter_draft)
    return checkpoint
