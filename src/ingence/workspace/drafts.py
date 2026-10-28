"""Drafts: every edit saved within a second, applied only when a person commits.

Each draft kind is one file, replaced atomically: the writer writes a sibling, flushes it and calls
``os.replace``, the protocol of the pointer's own ``storage/commit.write_pointer``. So a reader sees
the whole old draft or the whole new one, and a crash in the middle of a save loses at most the
last keystroke.

A save adds to the draft and never replaces it. The values a save sends are laid over the draft as
it stands, and a value leaves the draft only where the save names it in ``removed`` (the person put
it back as it was). So a save that sends nothing changes nothing. A page that changed view before
its timer fired, and found no field to read, cannot erase what was typed.

The kinds, and what committing one does:

- ``settings``: a partial ``Settings`` dict, holding only the fields a person changed. A commit
  writes it into ``settings.dashboard.yaml`` and validates it with ``settings.yaml`` beneath it.
- ``rule_parameters``: ``{rule_id: {parameter: value}}``. A commit writes it as a new project
  Rulebook revision of ``override`` changes (append-only).
- ``views``: page and graph view state. A commit never writes it. A view belongs to a person and
  not to the project, and stays a draft.
- ``notes``: unsent note text by target. Nothing happens until a note is sent, and a sent note is
  a Record event at once.

Saving and committing take turns. One lock, :data:`WRITING`, is held by a save and by a whole
commit, and a commit clears a draft only where the draft is still the one the commit read. Without
the lock, a save made while a commit ran would be neither committed nor kept.

A draft is validated before it is saved, and a save never refuses it. An edit that fails validation
(a negative worker count) is saved with its error beside it, so the page can show the error and
the person's typing is not lost. Only a commit refuses it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import write_atomically
from ingence.storage.layout import owned_path
from ingence.workspace import WORKSPACE_DIR, WORKSPACE_VERSION

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "COMMITTED_KINDS",
    "DRAFT_KINDS",
    "WRITING",
    "Draft",
    "clear_draft",
    "read_draft",
    "save_draft",
    "settings_errors",
    "uncommitted",
]

DRAFT_KINDS = ("settings", "rule_parameters", "views", "notes")

#: The kinds a commit applies; the others are a person's own and never "uncommitted".
COMMITTED_KINDS = ("settings", "rule_parameters")

#: Held by every save and by a whole commit, so the two take turns in this process.
WRITING = threading.RLock()


@dataclass
class Draft:
    """One kind of draft: its values, when it was last saved, and any validation errors."""

    kind: str
    values: dict[str, Any] = field(default_factory=dict)
    saved_at: str = ""
    errors: dict[str, str] = field(default_factory=dict)
    version: str = WORKSPACE_VERSION

    @property
    def empty(self) -> bool:
        return not self.values


def _path(project: Path, kind: str) -> Path:
    if kind not in DRAFT_KINDS:
        message = f"no draft kind {kind!r}; the kinds are {', '.join(DRAFT_KINDS)}"
        raise ValueError(message)
    return project / WORKSPACE_DIR / "drafts" / f"{kind}.json"


def read_draft(project: Path, kind: str) -> Draft:
    path = _path(project, kind)
    if not path.is_file():
        return Draft(kind=kind)
    loaded = json.loads(path.read_text("utf-8"))
    return Draft(
        kind=kind,
        values=dict(loaded.get("values", {})),
        saved_at=str(loaded.get("saved_at", "")),
        errors=dict(loaded.get("errors", {})),
        version=str(loaded.get("version", WORKSPACE_VERSION)),
    )


def settings_errors(project: Path, values: dict[str, Any]) -> dict[str, str]:
    """Each settings field that would fail ``Settings`` once laid over the files in force."""
    from pydantic import ValidationError

    from ingence.core.settings import Settings
    from ingence.workspace.settings_overlay import merged, read_layers

    try:
        Settings.model_validate(merged(read_layers(project).effective, values))
    except ValidationError as error:
        return {
            ".".join(str(part) for part in item["loc"]): str(item["msg"]) for item in error.errors()
        }
    return {}


def _key_path(removed: str | Sequence[str]) -> tuple[str, ...]:
    """A value's place in a draft: a dotted name (``scan.workers``) or its keys in order."""
    if isinstance(removed, str):
        return tuple(removed.split("."))
    return tuple(str(key) for key in removed)


def _without(values: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """``values`` with the value at ``keys`` taken out, and any mapping it leaves empty."""
    if not keys or keys[0] not in values:
        return values
    head, rest = keys[0], keys[1:]
    kept = dict(values)
    if not rest:
        del kept[head]
        return kept
    inner = kept[head]
    if not isinstance(inner, dict):
        return values
    trimmed = _without(inner, rest)
    if trimmed:
        kept[head] = trimmed
    else:
        del kept[head]
    return kept


def save_draft(
    project: Path,
    kind: str,
    values: dict[str, Any],
    *,
    removed: Sequence[str | Sequence[str]] = (),
    now: str | None = None,
) -> Draft:
    """Lay ``values`` over one kind's draft, take out ``removed``, and save it atomically.

    The whole draft is checked after the change, and its errors are saved beside it. A save
    that sends no value and removes none changes nothing and writes nothing.
    """
    with WRITING:
        return _save(project, kind, values, removed=removed, now=now)


def _save(
    project: Path,
    kind: str,
    values: dict[str, Any],
    *,
    removed: Sequence[str | Sequence[str]],
    now: str | None,
) -> Draft:
    from ingence.workspace.settings_overlay import merged

    # Check that the path to the project's own drafts folder holds no link, before anything is read
    # or written there (``storage.layout.owned_path``).
    path = owned_path(project, _path(project, kind))
    current = read_draft(project, kind)
    if not values and not removed:
        return current
    combined = merged(current.values, values)
    for place in removed:
        combined = _without(combined, _key_path(place))
    errors: dict[str, str] = {}
    if kind == "settings":
        errors = settings_errors(project, combined)
    elif kind == "rule_parameters":
        from ingence.workspace.revisions import check_overrides

        errors = check_overrides(project, combined)
    draft = Draft(
        kind=kind,
        values=combined,
        saved_at=now or datetime.now(tz=UTC).isoformat(timespec="seconds"),
        errors=errors,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(
        path,
        json.dumps(
            {
                "version": draft.version,
                "kind": kind,
                "saved_at": draft.saved_at,
                "values": combined,
                "errors": errors,
            },
            sort_keys=True,
            indent=2,
        ),
    )
    return draft


def clear_draft(project: Path, kind: str, *, if_unchanged: Draft | None = None) -> bool:
    """Remove one kind's draft after a commit applied it; say whether it was removed.

    With ``if_unchanged``, only where the draft is still the one the commit read (the same values,
    saved at the same moment): a save made since, even by another process, is kept.
    """
    with WRITING:
        path = owned_path(project, _path(project, kind))
        if if_unchanged is not None:
            now = read_draft(project, kind)
            if (now.values, now.saved_at) != (if_unchanged.values, if_unchanged.saved_at):
                return False
        path.unlink(missing_ok=True)
        return True


def uncommitted(project: Path) -> dict[str, int]:
    """How many edits each committed kind holds: the number on the Commit button."""
    counts: dict[str, int] = {}
    for kind in COMMITTED_KINDS:
        draft = read_draft(project, kind)
        if kind == "rule_parameters":
            # A value that is not a rule's parameters is counted, so its error is seen.
            counts[kind] = sum(
                len(values) if isinstance(values, dict) else 1 for values in draft.values.values()
            )
        else:
            counts[kind] = _leaf_count(draft.values)
    return counts


def _leaf_count(values: Any) -> int:
    if isinstance(values, dict):
        return sum(_leaf_count(value) for value in values.values())
    return 1
