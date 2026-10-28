"""A person's display labels, written to the Record through its one way in.

The Graph workspace lets a person give any file or folder a display label. A label is a human
annotation shown beside the entry's own name in every mode. It never replaces the name. A label is
one ``annotate`` event on ``label:<entry_id>`` (``record/targets.py``), with the label in its
payload beside the entry's name and path as they were when it was written. Removing a label is one
more event with no label, so the history stays whole. Nothing here writes an entry, an observation
or a source file, and nothing in inference reads a label.

The page gathers a session's edits, shows them for preview, and sends them in one request through
the answer route (``POST /api/review/answer`` with ``labels``), guarded as every write is. The
route checks every label in a batch before it writes any. The whole batch is refused, in words, if
it holds any of these: an entry the current run does not hold, an empty or overlong label, a note
that is not text, is overlong or holds a control character (checked as the layer's notes are), a
control character in a label, or an entry named twice. The author is trimmed, so one person is one
author. The events are then appended one by one under the Record's lock, and the response lists
each one written.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Any

from ingence.api.routes.overlay import NOTE_LIMIT
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import RecordEventKind
from ingence.record.log import append_event
from ingence.record.targets import label_target
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent

__all__ = ["LABEL_LIMIT", "LabelRefusedError", "write_labels"]

#: The longest label, in characters: a display name, not a note.
LABEL_LIMIT = 200
#: The most labels one batch may write.
BATCH_LIMIT = 500


class LabelRefusedError(ValueError):
    """A batch of labels refused before anything was written, in words."""


def _clean(value: Any, entry_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        message = f"the label for {entry_id} is not text"
        raise LabelRefusedError(message)
    text = unicodedata.normalize("NFC", value).strip()
    if not text:
        message = f"the label for {entry_id} is empty; send null to remove a label"
        raise LabelRefusedError(message)
    if len(text) > LABEL_LIMIT:
        message = f"the label for {entry_id} is {len(text)} characters; {LABEL_LIMIT} at most"
        raise LabelRefusedError(message)
    if any(unicodedata.category(character) == "Cc" for character in text):
        message = f"the label for {entry_id} holds a control character"
        raise LabelRefusedError(message)
    return text


def _note(value: Any, entry_id: str) -> str | None:
    """A label's note, checked as the layer's notes are (``overlay._text``); None for none."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        message = f"the note for {entry_id} is not text"
        raise LabelRefusedError(message)
    text = unicodedata.normalize("NFC", value).strip()
    if len(text) > NOTE_LIMIT:
        message = f"the note for {entry_id} is {len(text)} characters; {NOTE_LIMIT} at most"
        raise LabelRefusedError(message)
    if any(unicodedata.category(character) == "Cc" for character in text):
        message = f"the note for {entry_id} holds a control character"
        raise LabelRefusedError(message)
    return text


def write_labels(
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    labels: Sequence[Mapping[str, Any]],
) -> list[RecordEvent]:
    """Check every label in the batch, then append one annotation per label."""
    actor = actor.strip()
    if not actor:
        message = "a label states who wrote it"
        raise LabelRefusedError(message)
    if not labels:
        message = "the batch holds no label"
        raise LabelRefusedError(message)
    if len(labels) > BATCH_LIMIT:
        message = f"a batch holds at most {BATCH_LIMIT} labels; this one holds {len(labels)}"
        raise LabelRefusedError(message)
    wanted: list[tuple[str, str | None, str | None]] = []
    seen: set[str] = set()
    for item in labels:
        entry_id = str(item.get("entry_id") or "").strip()
        if not entry_id:
            message = "every label names its item (entry_id)"
            raise LabelRefusedError(message)
        if entry_id in seen:
            message = f"{entry_id} is labeled twice in one batch"
            raise LabelRefusedError(message)
        seen.add(entry_id)
        wanted.append(
            (entry_id, _clean(item.get("label"), entry_id), _note(item.get("note"), entry_id))
        )
    with open_catalog(project) as catalog:
        row = catalog.query("SELECT run_id FROM run LIMIT 1").fetchone()
        if row is None or not row[0]:
            message = "the project has no published run to label items of"
            raise LabelRefusedError(message)
        run_id = str(row[0])
        held = {
            str(entry): (str(name), str(path or ""))
            for entry, name, path in catalog.query(
                "SELECT entry_id, name, relative_path FROM entries WHERE NOT is_deleted "
                "AND list_contains(?, entry_id)",
                [[entry for entry, _, _ in wanted]],
            ).fetchall()
        }
    missing = [entry for entry, _, _ in wanted if entry not in held]
    if missing:
        message = f"the current run holds no live item {', '.join(missing[:5])}"
        raise LabelRefusedError(message)
    written: list[RecordEvent] = []
    for entry_id, text, note in wanted:
        name, path = held[entry_id]
        payload: dict[str, Any] = {
            "label": text,
            "name": name,
            "path": path,
            "source": "graph workspace",
        }
        if text is None:
            payload["cleared"] = True
        if note:
            payload["note"] = note
        written.append(
            append_event(
                project,
                kind=RecordEventKind.ANNOTATE,
                actor=actor,
                occurred_at=occurred_at,
                target=label_target(entry_id),
                against_run_id=run_id,
                payload=payload,
                scope=PROJECT_SCOPE,
            )
        )
    return written
