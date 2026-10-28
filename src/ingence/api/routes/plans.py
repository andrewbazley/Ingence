"""A person's reorganization plans, kept in the workspace and never applied here.

This module supports the reorganization planner. A person drags a file, a folder or a group of
theirs onto another folder in the Graph workspace, and the page keeps the moves as a draft. Saving
keeps the plan as a file in the project's workspace (``workspace/``), beside drafts and
checkpoints. A plan is a human artifact. It is never written to the Record or to the source.
``api/v1/explorer/plan`` reads what a plan would do against the run and, read-only, the disk.

Layout, one folder per plan and one file per version, so a plan's history is kept whole::

    workspace/plans/<plan id>/<version>.json

Each version is written whole to a temporary name and published under its number with a hard
link, which fails where that number is taken. So a reader never sees half a plan, and two saves at
once never overwrite each other: the later takes the next number. A new plan's identifier carries
a random part, so one name saved twice in one second is two plans. A version holds the plan's
name, who saved it and when, the run it was made against, and its moves (an entry, the folder it
would go into). It holds nothing else.

Ingence does not apply a plan. A move on disk would need a supervised job that journals each
step, checks the source before and the result after, and can undo or repair a partial move after a
crash. That protocol does not exist, so this module and every page say that application is
unavailable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import unicodedata
from typing import TYPE_CHECKING, Any

from ingence.storage.layout import owned_path
from ingence.workspace import WORKSPACE_DIR

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "MOVE_LIMIT",
    "PLANS_DIR",
    "PlanRefusedError",
    "latest_plan",
    "list_plans",
    "plan_versions",
    "save_plan",
]

PLANS_DIR = "plans"
#: The most moves one plan may hold, and the longest name and note.
MOVE_LIMIT = 5000
NAME_LIMIT = 120
NOTE_LIMIT = 1000
PLAN_FORMAT = 1

_PLAN_ID = re.compile(r"^plan-[0-9a-f]{12}$")


class PlanRefusedError(ValueError):
    """A plan refused before anything was written, in words."""


def _plans(project: Path) -> Path:
    return project / WORKSPACE_DIR / PLANS_DIR


def _text(value: Any, what: str, limit: int, *, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            message = f"{what} is empty"
            raise PlanRefusedError(message)
        return None
    if not isinstance(value, str):
        message = f"{what} is not text"
        raise PlanRefusedError(message)
    text = unicodedata.normalize("NFC", value).strip()
    if len(text) > limit:
        message = f"{what} is {len(text)} characters; {limit} at most"
        raise PlanRefusedError(message)
    if any(unicodedata.category(character) == "Cc" for character in text):
        message = f"{what} holds a control character"
        raise PlanRefusedError(message)
    return text


def plan_versions(project: Path, plan_id: str) -> list[int]:
    if not _PLAN_ID.match(plan_id):
        return []
    folder = _plans(project) / plan_id
    if not folder.is_dir():
        return []
    return sorted(
        int(path.stem) for path in folder.glob("*.json") if path.stem.isdigit() and _written(path)
    )


def _written(path: Path) -> bool:
    """Whether a version is there to read.

    An empty file is a number reserved by a save that is still moving its version into place. The
    function skips it until the version lands."""
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def latest_plan(project: Path, plan_id: str, version: int | None = None) -> dict[str, Any] | None:
    """A plan's latest version (or the one asked for), or None where there is no such plan."""
    versions = plan_versions(project, plan_id)
    if not versions:
        return None
    chosen = version if version in versions else versions[-1]
    held: dict[str, Any] = json.loads(
        (_plans(project) / plan_id / f"{chosen}.json").read_text("utf-8")
    )
    held["version"] = chosen
    held["versions"] = versions
    return held


def list_plans(project: Path) -> list[dict[str, Any]]:
    """Every plan's latest version, newest first, without its moves."""
    folder = _plans(project)
    found: list[dict[str, Any]] = []
    if not folder.is_dir():
        return found
    for path in folder.iterdir():
        held = latest_plan(project, path.name) if path.is_dir() else None
        if held is None:
            continue
        found.append(
            {
                "plan_id": held["plan_id"],
                "name": held["name"],
                "actor": held["actor"],
                "saved_at": held["saved_at"],
                "run_id": held["run_id"],
                "moves": len(held["moves"]),
                "version": held["version"],
            }
        )
    return sorted(found, key=lambda item: item["saved_at"], reverse=True)


def save_plan(project: Path, body: Mapping[str, Any], *, saved_at: str) -> dict[str, Any]:
    """``POST /api/workspace/plan``: keep a plan, a new version where it names one saved before.

    ``{plan_id?, name, note?, actor, run_id, moves: [{entry_id, to_folder_id}]}``. Only the plan's
    own shape is checked here; what its moves would do is read by the plan check.
    """
    actor = _text(body.get("actor"), "who saved the plan", 200, required=True)
    name = _text(body.get("name"), "a plan's name", NAME_LIMIT, required=True)
    note = _text(body.get("note"), "a plan's note", NOTE_LIMIT, required=False)
    run_id = str(body.get("run_id") or "").strip()
    if not run_id.startswith("run_"):
        message = "a plan names the run it was made against (run_id)"
        raise PlanRefusedError(message)
    moves = body.get("moves")
    if not isinstance(moves, list) or not moves:
        message = "a plan holds at least one move"
        raise PlanRefusedError(message)
    if len(moves) > MOVE_LIMIT:
        message = f"a plan holds at most {MOVE_LIMIT} moves; this one holds {len(moves)}"
        raise PlanRefusedError(message)
    kept: list[dict[str, str]] = []
    seen: set[str] = set()
    for move in moves:
        entry = str((move or {}).get("entry_id") or "").strip()
        folder = str((move or {}).get("to_folder_id") or "").strip()
        if not entry.startswith("entry_") or not folder.startswith("entry_"):
            message = "every move names an item (entry_id) and a folder (to_folder_id)"
            raise PlanRefusedError(message)
        if entry in seen:
            message = f"{entry} is moved twice in one plan"
            raise PlanRefusedError(message)
        seen.add(entry)
        kept.append({"entry_id": entry, "to_folder_id": folder})
    plan_id = str(body.get("plan_id") or "").strip()
    if plan_id and not _PLAN_ID.match(plan_id):
        message = f"{plan_id!r} is not a plan's identifier"
        raise PlanRefusedError(message)
    kept_in: Path | None = None
    if not plan_id:
        # A new plan: its words, its moment and a random part, in a folder made exclusively.
        while kept_in is None:
            said = f"{actor}\x1f{saved_at}\x1f{name}\x1f{secrets.token_hex(8)}"
            plan_id = f"plan-{hashlib.sha256(said.encode()).hexdigest()[:12]}"
            _plans(project).mkdir(parents=True, exist_ok=True)
            try:
                (_plans(project) / plan_id).mkdir()
            except FileExistsError:
                continue
            kept_in = _plans(project) / plan_id
    versions = plan_versions(project, plan_id)
    version = (versions[-1] + 1) if versions else 1
    held = {
        "format": PLAN_FORMAT,
        "plan_id": plan_id,
        "name": name,
        "note": note,
        "actor": actor,
        "saved_at": saved_at,
        "run_id": run_id,
        "moves": kept,
        # Said in the file itself, for whoever reads it outside the Dashboard.
        "applied": False,
        "said": "A plan only: nothing on disk was moved and nothing in the Record changed.",
    }
    # The project's own plans folder, nothing on the way to it a link, before a version is moved in.
    kept_in = owned_path(project, kept_in if kept_in is not None else _plans(project) / plan_id)
    kept_in.mkdir(parents=True, exist_ok=True)
    version = _publish(kept_in, held, version)
    return {"plan_id": plan_id, "version": version, "moves": len(kept)}


def _publish(folder: Path, held: dict[str, Any], version: int) -> int:
    """Write a version whole, then give it the first free number from ``version`` on.

    Published with a hard link, which refuses a name already taken, so a save that listed the
    versions before another writer's landed takes the next number and never overwrites it. Where
    the filesystem makes no hard links, the number is reserved with an exclusive create (an empty
    file, which readers skip) and the whole version moved onto it, so no reader sees half a plan.
    """
    staging = folder / f".{version}.{secrets.token_hex(4)}.json.writing"
    written = json.dumps(held, indent=2, sort_keys=True)
    staging.write_text(written, "utf-8")
    try:
        while True:
            final = folder / f"{version}.json"
            try:
                os.link(staging, final)
            except FileExistsError:
                version += 1
                continue
            except OSError:
                try:
                    os.close(os.open(final, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                except FileExistsError:
                    version += 1
                    continue
                os.replace(staging, final)
            return version
    finally:
        staging.unlink(missing_ok=True)
