"""``GET /api/v1/explorer/plan``: what a saved reorganization plan would do, checked, never done.

The reorganization planner. A plan (``api/routes/plans.py``) is a list of moves, each an entry and
the folder it would go into, kept in the workspace. For each move this route reads the following,
without writing anything anywhere:

- Where it would go: its path now and its path after, and whether it would cross roots.
- What goes with it: for a folder, every file and folder beneath, and their size.
- What would refuse it: the entry or the folder not in the run read, a folder moved into itself, a
  name already in the destination, and two moves putting one name into one folder. A name is
  compared as the root compares names. A root read as case-insensitive refuses ``Data`` beside
  ``data``, and so does a root whose case was never settled, since sensitive is the safe guess for
  identity and the unsafe one for a collision.
- How the moves meet: the plan is read whole before any move is checked. A move of what another
  move already carries (``overlap``), a move into a folder another move takes away (``destination``:
  it would not land where the plan says), and moves that together put a folder inside itself
  (``cycle``) are refused, never ordered for the person. A plan carried out by hand must mean one
  thing in any order. A move refused on its own account (gone, or into itself) carries nothing, so
  it refuses no other. The totals count what moves once, over the union of what the moves carry.
- Links that may break: files that name it by path (the run's published mentions into it), files
  inside it naming files outside by path, and links whose target lies inside it. A relative target
  is read against the link's own folder, as the census reads it, and a folder is compared with its
  separator, so ``raw`` never holds ``rawer``. Every link is resolved once per check, and each move
  asks for a range of the sorted targets, never a scan.
- The disk, read-only: whether the source is still as the census found it (a file's size and
  modification time), whether the folders involved can be written, and, across roots, whether
  there is room. Each is ``unknown`` where the root is not mounted here or refuses the read, never
  guessed.
- The Record: how many decisions name it or what is beneath it, which the Record reads at the new
  place once a census confirms the move (``record/log.at_new_places``).
- A readable diff: each folder touched, what it holds before and after.

Most Dashboard routes read only the project and its runs. Two touch a root's own path:
``reachability`` checks that each root answers, and the landing page lists a folder that a person
types. This route reads more of the disk beneath a root, and only reads it: whether a root is
mounted, a source's size and modification time, whether a folder can be written, and a disk's free
room. Nothing is opened for its content and nothing is written. A read that the system refuses is
"unknown", never a server error. Every such read is guarded for ``OSError``, because on Python 3.12
``Path.is_dir`` raises on ``EPERM`` and ``EACCES`` rather than answering False.

Applying is unavailable, and the response says why. Nothing here or anywhere moves a file. A move
on disk must be a supervised job that journals each step, checks before and after, and can undo or
repair a partial move after a crash. That protocol is designed and not built.
"""

from __future__ import annotations

import os
import shutil
import unicodedata
from bisect import bisect_left, bisect_right
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import duckdb
import pyarrow as pa

from ingence import SCHEMA_VERSION
from ingence.api.routes.plans import latest_plan, list_plans
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.explorer.projection import connect as explorer_connection
from ingence.api.v1.explorer.wheel import _rows, _run_and_projection
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import integer, one

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["APPLY_UNAVAILABLE", "PlanCheck", "PlansResponse", "check_plan"]

Outcome = Literal["ok", "warn", "refuse", "unknown"]

#: What every page says in place of an apply button, until the safe protocol is built.
APPLY_UNAVAILABLE = (
    "Applying a plan is not available in this build. A move on disk has to be a supervised job "
    "that journals every step, checks the source before and the result after, and can undo or "
    "repair a move left half done by a crash; that protocol is designed and not built. Export "
    "the plan to carry it out by hand: the next census finds the moves, and the Record follows "
    "each moved file to its new place."
)
#: How many names a folder's diff lists before it counts the rest, and links a move lists.
DIFF_NAMES = 40
LINKS_LISTED = 6


class PlanCheckItem(ApiModel):
    #: ``exists``, ``into_itself``, ``overlap``, ``destination``, ``cycle``, ``already_there``,
    #: ``collision``, ``roots``, ``space``, ``permission``, ``source``, ``links``, ``decisions``.
    check: str
    outcome: Outcome
    words: str


class PlanLink(ApiModel):
    entry_id: Identifier
    name: str
    path: str
    why: str


class PlanMoveCheck(ApiModel):
    entry_id: Identifier
    kind: Literal["file", "folder", "gone"]
    name: str
    from_path: str
    to_folder_id: Identifier
    to_folder_path: str
    to_path: str
    crosses_roots: bool
    #: What moves with it: itself and, for a folder, everything beneath.
    files: Count
    folders: Count
    bytes: Count
    decisions: Count
    links: list[PlanLink]
    checks: list[PlanCheckItem]
    #: The worst of its checks: refuse, then warn, then unknown, then ok.
    outcome: Outcome


class PlanFolderDiff(ApiModel):
    folder_id: Identifier
    path: str
    before: Count
    after: Count
    added: list[str]
    removed: list[str]


class PlanApply(ApiModel):
    available: bool
    why: str


@register("explorer_plan")
class PlanCheck(ApiModel):
    """``GET /api/v1/explorer/plan?id=``: one saved plan, each move checked, and the diff."""

    schema_version: str
    plan_id: str
    version: Count
    versions: list[Count]
    name: str
    note: str | None
    actor: str
    saved_at: str
    #: The run the plan was made against, and the run it was checked against now.
    run_made_against: Identifier
    run_read: Identifier
    moves: list[PlanMoveCheck]
    diff: list[PlanFolderDiff]
    totals: dict[str, Count]
    apply: PlanApply
    message: str


class PlanSummary(ApiModel):
    plan_id: str
    name: str
    actor: str
    saved_at: str
    run_id: Identifier
    moves: Count
    version: Count


@register("explorer_plans")
class PlansResponse(ApiModel):
    """``GET /api/v1/explorer/plans``: every saved plan, newest first."""

    schema_version: str
    plans: list[PlanSummary]
    apply: PlanApply


_ORDER: dict[str, int] = {"refuse": 3, "warn": 2, "unknown": 1, "ok": 0}


def _folded(text: str) -> str:
    return text.casefold()


def _as_written(text: str) -> str:
    return text


def _worst(checks: Sequence[PlanCheckItem]) -> Outcome:
    return max((item.outcome for item in checks), key=lambda outcome: _ORDER[outcome], default="ok")


def _roots(project: Path) -> dict[str, tuple[Path, str]]:
    """Each root: where it is mounted, and how it compares names (``sensitive``,
    ``insensitive``, or ``unknown`` where its case was never settled)."""
    from ingence.core.project import Project

    found: dict[str, tuple[Path, str]] = {}
    for declaration in Project.open(project).identity.roots:
        reading = str(getattr(declaration.case_sensitivity, "value", declaration.case_sensitivity))
        found[declaration.root_id] = (Path(declaration.mount_path), reading)
    return found


#: What a collision's words add for a root that folds names, by the root's case reading.
_FOLDED_BECAUSE = {
    "insensitive": " (this root compares names without case)",
    "sensitive": "",
}
_FOLDED_UNKNOWN = " (compared without case: this root's case is not known)"


def _on_disk(roots: Mapping[str, tuple[Path, str]], root_id: str, relative: str) -> Path | None:
    """Where an entry of a root is on this machine, or None where the root is not mounted here
    or the system refuses to say (a refused read is unknown, never an error)."""
    held = roots.get(root_id)
    if held is None:
        return None
    try:
        if not held[0].is_dir():
            return None
    except OSError:
        return None
    return held[0] if relative in ("", ".") else held[0] / relative


#: The words for a disk read the system refused.
_REFUSED = "reading the disk here was refused"


def _stands_alone(live: Mapping[str, Mapping[str, Any]], entry: str, folder: str) -> bool:
    """Whether a move could be carried out on its own: both ends live, into a folder not itself
    and not beneath itself."""
    row, into = live.get(entry), live.get(folder)
    if row is None or into is None or into.get("kind") != "folder":
        return False
    return not (
        row["kind"] == "folder" and (folder == entry or entry in (into.get("ancestors") or []))
    )


def _disk_checks(
    source_disk: Path, target_mount: Path, row: Mapping[str, Any]
) -> list[PlanCheckItem]:
    """Read-only: can the folders be written, and is the source as the census found it? A read
    the system refuses makes both unknown."""
    try:
        writable = os.access(source_disk.parent, os.W_OK) and os.access(target_mount, os.W_OK)
        present = source_disk.exists() or source_disk.is_symlink()
        stat = source_disk.lstat() if present and row["kind"] == "file" else None
    except OSError:
        return [
            PlanCheckItem(
                check="permission",
                outcome="unknown",
                words=f"{_REFUSED}: who may write in these folders is not known",
            ),
            PlanCheckItem(
                check="source",
                outcome="unknown",
                words=f"{_REFUSED}: whether the source changed is unknown",
            ),
        ]
    checks = [
        PlanCheckItem(
            check="permission",
            outcome="ok" if writable else "refuse",
            words="both folders can be written by this account"
            if writable
            else "a folder involved cannot be written by this account",
        )
    ]
    if not present:
        checks.append(
            PlanCheckItem(
                check="source",
                outcome="refuse",
                words="it is no longer on disk where the census found it",
            )
        )
    elif stat is not None:
        same = stat.st_size == int(row["bytes"] or 0) and (
            row.get("modified_ns") is None or stat.st_mtime_ns == int(row["modified_ns"])
        )
        checks.append(
            PlanCheckItem(
                check="source",
                outcome="ok" if same else "warn",
                words="as the census found it"
                if same
                else "changed on disk since the census: check it again before moving",
            )
        )
    else:
        checks.append(
            PlanCheckItem(
                check="source", outcome="ok", words="the folder is where the census found it"
            )
        )
    return checks


def _landed(link: str, target: str) -> str:
    """Where a link's target lands, read from its text as the census reads it: a relative target
    joined to the link's own folder, both normalized lexically, never followed on disk."""
    joined = target if os.path.isabs(target) else os.path.join(os.path.dirname(link), target)
    return os.path.normpath(unicodedata.normalize("NFC", joined))


class _Landings:
    """Every link of the run with where it lands, sorted once, so a move asks for a range."""

    def __init__(
        self, links: Sequence[tuple[Any, ...]], roots: Mapping[str, tuple[Path, str]]
    ) -> None:
        held: list[tuple[str, tuple[str, str, str, str]]] = []
        for link_id, name, relative, target, root_id in links:
            mount = roots.get(str(root_id))
            if mount is None:
                continue
            where = _landed(str(mount[0] / str(relative or "")), str(target))
            held.append((where, (str(link_id), str(name), str(relative or ""), str(target))))
        self._exact = sorted(held)
        self._folded = sorted((where.casefold(), link) for where, link in held)
        self._keys = {
            False: [where for where, _ in self._exact],
            True: [where for where, _ in self._folded],
        }

    def inside(self, folder: Path, *, folded: bool) -> list[tuple[str, str, str, str]]:
        """The links landing on ``folder`` or anywhere beneath it (compared with a separator)."""
        held = self._folded if folded else self._exact
        keys = self._keys[folded]
        here = os.path.normpath(unicodedata.normalize("NFC", str(folder)))
        here = here.casefold() if folded else here
        beneath = here.rstrip(os.sep) + os.sep
        after = beneath[:-1] + chr(ord(os.sep) + 1)
        found = [
            *range(bisect_left(keys, here), bisect_right(keys, here)),
            *range(bisect_left(keys, beneath), bisect_left(keys, after)),
        ]
        return [held[index][1] for index in found]


def _entries(
    connection: duckdb.DuckDBPyConnection, projection: Any, ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if not ids:
        return found
    for row in _rows(
        connection.execute(
            "SELECT entry_id, name, relative_path, root_id, folder_id AS parent_id, ancestors, "  # noqa: S608
            "'file' AS kind, bytes, modified_ns, 1 AS files, 0 AS folders "
            f"FROM {projection.files} WHERE list_contains(?, entry_id) "
            "UNION ALL SELECT entry_id, name, relative_path, root_id, parent_id, ancestors, "
            "'folder', bytes, NULL, files, folders "
            f"FROM {projection.folders} WHERE list_contains(?, entry_id)",
            [list(ids), list(ids)],
        )
    ):
        found[str(row["entry_id"])] = row
    return found


def check_plan(context: V1Context, query: Mapping[str, Sequence[str]]) -> PlanCheck:
    from ingence.record.log import read_events
    from ingence.storage.catalog import open_catalog

    plan_id = (one(query, "id") or "").strip()
    if not plan_id:
        raise ApiError("id names the plan to check", parameter="id")
    version = integer(query, "version", default=0, low=0, high=10**6) or None
    held = latest_plan(context.project, plan_id, version)
    if held is None:
        message = f"this project keeps no plan {plan_id}"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")
    run_id, _, projection, _ = _run_and_projection(context, query)
    roots = _roots(context.project)
    moves = [(str(m["entry_id"]), str(m["to_folder_id"])) for m in held["moves"]]
    connection = explorer_connection()
    try:
        wanted = sorted({entry for entry, _ in moves} | {folder for _, folder in moves})
        live = _entries(connection, projection, wanted)
        # What each move carries: itself and, for a folder, every entry beneath it; and each
        # carried entry's kind and size, so the totals count what moves once.
        carried: dict[str, set[str]] = {}
        sizes: dict[str, tuple[str, int]] = {}
        for entry, _ in moves:
            carried[entry] = {entry}
            row = live.get(entry)
            if row is not None:
                sizes[entry] = (
                    str(row["kind"]),
                    int(row["bytes"] or 0) if row["kind"] == "file" else 0,
                )
            if row is not None and row["kind"] == "folder":
                for beneath, kind, size in connection.execute(
                    f"SELECT entry_id, 'file', bytes FROM {projection.files} "  # noqa: S608
                    "WHERE list_contains(ancestors, ?) "
                    f"UNION ALL SELECT entry_id, 'folder', 0 FROM {projection.folders} "
                    "WHERE list_contains(ancestors, ?)",
                    [entry, entry],
                ).fetchall():
                    carried[entry].add(str(beneath))
                    sizes[str(beneath)] = (str(kind), int(size or 0))
        # The destinations' children as they are, by name.
        destinations = sorted({folder for _, folder in moves})
        children: dict[str, list[tuple[str, str]]] = {folder: [] for folder in destinations}
        for parent, entry, name in connection.execute(
            f"SELECT folder_id, entry_id, name FROM {projection.files} "  # noqa: S608
            "WHERE list_contains(?, folder_id) UNION ALL "
            f"SELECT parent_id, entry_id, name FROM {projection.folders} "
            "WHERE list_contains(?, parent_id)",
            [destinations, destinations],
        ).fetchall():
            children.setdefault(str(parent), []).append((str(entry), str(name)))
    finally:
        connection.close()

    every_carried = set().union(*carried.values()) if carried else set()
    with open_catalog(context.project, run_id=run_id) as catalog:
        catalog.connection.register(
            "plan_carried",
            pa.table({"id": pa.array(sorted(every_carried), pa.string())}),
        )
        mentions = catalog.query(
            "SELECT a.source_id, a.target_id, e.name, e.relative_path FROM assertions a "
            "JOIN entries e ON e.entry_id = a.source_id WHERE a.decision_status = 'asserted' "
            "AND a.primitive = 'references' AND (a.target_id IN (SELECT id FROM plan_carried) "
            "OR a.source_id IN (SELECT id FROM plan_carried))"
        ).fetchall()
        links = catalog.query(
            "SELECT entry_id, name, relative_path, symlink_target, root_id FROM entries "
            "WHERE NOT is_deleted AND symlink_target IS NOT NULL AND symlink_target <> ''"
        ).fetchall()
        claims = {
            str(claim): str(subject)
            for claim, subject in catalog.query(
                "SELECT claim_id, subject FROM claims "
                "WHERE subject IN (SELECT id FROM plan_carried)"
            ).fetchall()
        }
    events = read_events(context.project)
    landings = _Landings(links, roots)

    checked: list[PlanMoveCheck] = []
    arriving: dict[str, list[str]] = {}
    for entry, folder in moves:
        arriving.setdefault(folder, []).append(entry)
    # The plan read whole first: which moves could be carried out on their own, and so carry
    # what they move; a move refused on its own account moves nothing and refuses no other.
    viable = [
        index for index, (entry, folder) in enumerate(moves) if _stands_alone(live, entry, folder)
    ]

    def carrier(held: str, besides: int) -> int | None:
        """Another move of the plan that carries ``held`` with it, or None."""
        return next(
            (index for index in viable if index != besides and held in carried[moves[index][0]]),
            None,
        )

    def in_a_cycle(index: int) -> bool:
        """Whether following the plan's moves from this one's destination comes back inside it."""
        entry, folder = moves[index]
        seen: set[int] = set()
        other = carrier(folder, index)
        while other is not None and other not in seen:
            seen.add(other)
            landing = moves[other][1]
            if landing in carried[entry]:
                return True
            other = carrier(landing, index)
        return False

    for index, (entry, folder) in enumerate(moves):
        row = live.get(entry)
        into = live.get(folder)
        checks: list[PlanCheckItem] = []
        name = str(row["name"]) if row else entry
        if row is None or into is None or into.get("kind") != "folder":
            checks.append(
                PlanCheckItem(
                    check="exists",
                    outcome="refuse",
                    words=(
                        "the run read holds no such file or folder"
                        if row is None
                        else "the destination is not a folder of the run read"
                    ),
                )
            )
            checked.append(
                PlanMoveCheck(
                    entry_id=entry,
                    kind="gone" if row is None else row["kind"],
                    name=name,
                    from_path=str(row["relative_path"]) if row else "",
                    to_folder_id=folder,
                    to_folder_path=str(into["relative_path"]) if into else "",
                    to_path="",
                    crosses_roots=False,
                    files=0,
                    folders=0,
                    bytes=0,
                    decisions=0,
                    links=[],
                    checks=checks,
                    outcome="refuse",
                )
            )
            continue
        from_path = str(row["relative_path"])
        folder_path = "" if str(into["relative_path"]) in ("", ".") else str(into["relative_path"])
        to_path = f"{folder_path}/{name}" if folder_path else name
        crosses = str(row["root_id"]) != str(into["root_id"])
        # Names are compared as written only where the root is recorded case-sensitive: for a
        # collision, folding is the safe guess where the case is not known.
        reading = roots.get(str(into["root_id"]), (Path(), "unknown"))[1]
        fold = _as_written if reading == "sensitive" else _folded
        moved = carried[entry]
        if row["kind"] == "folder" and (folder == entry or entry in (into.get("ancestors") or [])):
            checks.append(
                PlanCheckItem(
                    check="into_itself", outcome="refuse", words="a folder cannot go inside itself"
                )
            )
        # How this move meets the plan's others (the plan read whole).
        above = carrier(entry, index)
        if above is not None:
            carrier_name = str(live[moves[above][0]]["name"])
            checks.append(
                PlanCheckItem(
                    check="overlap",
                    outcome="refuse",
                    words=(
                        f"another move in this plan moves {carrier_name!r}, which holds it: "
                        "whichever goes first, the other's source is no longer where the plan "
                        "says; keep one of the two"
                    ),
                )
            )
        if in_a_cycle(index):
            checks.append(
                PlanCheckItem(
                    check="cycle",
                    outcome="refuse",
                    words=(
                        "with another move in this plan it would put a folder inside itself "
                        "(the moves form a cycle)"
                    ),
                )
            )
        else:
            taken = carrier(folder, index)
            if taken is not None:
                taken_name = str(live[moves[taken][0]]["name"])
                checks.append(
                    PlanCheckItem(
                        check="destination",
                        outcome="refuse",
                        words=(
                            f"the folder it would go into moves with {taken_name!r} in another "
                            "move of this plan, so it would not land where the plan says; move "
                            "it in a plan of its own once that one is done"
                        ),
                    )
                )
        if str(row["parent_id"]) == folder:
            checks.append(
                PlanCheckItem(
                    check="already_there",
                    outcome="warn",
                    words="it is already in that folder: nothing would move",
                )
            )
        clashes = [
            n
            for e, n in children.get(folder, [])
            if fold(n) == fold(name) and e != entry and e not in moved
        ]
        twins = [
            other
            for other in arriving.get(folder, [])
            if other != entry and fold(str(live.get(other, {}).get("name", ""))) == fold(name)
        ]
        if clashes:
            checks.append(
                PlanCheckItem(
                    check="collision",
                    outcome="refuse",
                    words=f"{clashes[0]!r} is already in that folder"
                    + _FOLDED_BECAUSE.get(reading, _FOLDED_UNKNOWN),
                )
            )
        elif twins:
            checks.append(
                PlanCheckItem(
                    check="collision",
                    outcome="refuse",
                    words=f"another move in this plan puts a {name!r} into the same folder",
                )
            )
        else:
            checks.append(
                PlanCheckItem(
                    check="collision", outcome="ok", words="no name in that folder is the same"
                )
            )
        # Size and what goes with it.
        files = int(row["files"] or 0)
        folders = int(row["folders"] or 0) + (1 if row["kind"] == "folder" else 0)
        size = int(row["bytes"] or 0)
        # Across roots a move is a copy and a removal; within one it is a rename.
        target_mount = _on_disk(roots, str(into["root_id"]), folder_path or ".")
        if crosses:
            checks.append(
                PlanCheckItem(
                    check="roots",
                    outcome="warn",
                    words="it would cross roots: a copy, then the original removed, not a rename",
                )
            )
            if target_mount is None:
                checks.append(
                    PlanCheckItem(
                        check="space",
                        outcome="unknown",
                        words=(
                            "the destination's root is not mounted here, or refused a read: "
                            "its room is unknown"
                        ),
                    )
                )
            else:
                try:
                    free: int | None = shutil.disk_usage(target_mount).free
                except OSError:
                    free = None
                checks.append(
                    PlanCheckItem(
                        check="space",
                        outcome="unknown" if free is None else "ok" if free > size else "refuse",
                        words=f"{_REFUSED}: the destination's room is unknown"
                        if free is None
                        else "there is room for it at the destination"
                        if free > size
                        else "the destination's disk has not the room for it",
                    )
                )
        else:
            checks.append(
                PlanCheckItem(
                    check="space",
                    outcome="ok",
                    words="a move within one root is a rename and needs no room",
                )
            )
        # The disk, read-only: can the folders be written, and is the source as the census found it?
        source_disk = _on_disk(roots, str(row["root_id"]), from_path)
        if source_disk is None or target_mount is None:
            checks.append(
                PlanCheckItem(
                    check="permission",
                    outcome="unknown",
                    words=(
                        "a root is not mounted here, or refused a read, so who may write in it "
                        "is not known"
                    ),
                )
            )
            checks.append(
                PlanCheckItem(
                    check="source",
                    outcome="unknown",
                    words=(
                        "the source's root is not mounted here, or refused a read: whether it "
                        "changed is unknown"
                    ),
                )
            )
        else:
            checks.extend(_disk_checks(source_disk, target_mount, row))
        # Links that may break.
        listed: list[PlanLink] = []
        naming_in = [m for m in mentions if str(m[1]) in moved and str(m[0]) not in moved]
        naming_out = [m for m in mentions if str(m[0]) in moved and str(m[1]) not in moved]
        root_disk = _on_disk(roots, str(row["root_id"]), from_path)
        pointing = (
            []
            if root_disk is None
            else [
                link
                for link in landings.inside(
                    root_disk, folded=roots[str(row["root_id"])][1] != "sensitive"
                )
                if str(link[0]) not in moved
            ]
        )
        for source, _, link_name, link_path in naming_in[:LINKS_LISTED]:
            listed.append(
                PlanLink(
                    entry_id=str(source),
                    name=str(link_name),
                    path=str(link_path or ""),
                    why="names it by path",
                )
            )
        for source, _, link_name, link_path in naming_out[: max(0, LINKS_LISTED - len(listed))]:
            listed.append(
                PlanLink(
                    entry_id=str(source),
                    name=str(link_name),
                    path=str(link_path or ""),
                    why="inside it, names a file outside by path",
                )
            )
        for link_id, link_name, link_path, _ in pointing[: max(0, LINKS_LISTED - len(listed))]:
            listed.append(
                PlanLink(
                    entry_id=str(link_id),
                    name=str(link_name),
                    path=str(link_path or ""),
                    why="a link whose target lies inside it",
                )
            )
        breaking = len(naming_in) + len(naming_out) + len(pointing)
        checks.append(
            PlanCheckItem(
                check="links",
                outcome="warn" if breaking else "ok",
                words=(
                    f"{breaking} mention{'' if breaking == 1 else 's'} or link"
                    f"{'' if breaking == 1 else 's'} name it or what it holds by path, and may "
                    "need editing"
                    if breaking
                    else "no published mention or link names it by path"
                ),
            )
        )
        # The Record: decisions naming what moves, which follow a move a census confirms.
        decisions = sum(
            1
            for event in events
            if any(part in moved for part in event.target.split(":"))
            or (event.target.startswith("claim:") and claims.get(event.target[6:]) in moved)
        )
        checks.append(
            PlanCheckItem(
                check="decisions",
                outcome="ok",
                words=(
                    f"{decisions} decision{'' if decisions == 1 else 's'} in the Record name it "
                    "or what it holds; the Record reads them at the new place once a census "
                    "confirms the move"
                    if decisions
                    else "no decision in the Record names it"
                ),
            )
        )
        checked.append(
            PlanMoveCheck(
                entry_id=entry,
                kind=row["kind"],
                name=name,
                from_path=from_path,
                to_folder_id=folder,
                to_folder_path=folder_path,
                to_path=to_path,
                crosses_roots=crosses,
                files=files,
                folders=folders,
                bytes=size,
                decisions=decisions,
                links=listed,
                checks=checks,
                outcome=_worst(checks),
            )
        )

    # The diff: each folder touched, what leaves it and what arrives.
    touched: dict[str, dict[str, Any]] = {}
    for move in checked:
        if move.kind == "gone":
            continue
        source_row = live.get(move.entry_id) or {}
        parent = str(source_row.get("parent_id") or "")
        for folder_id in (parent, move.to_folder_id):
            if folder_id and folder_id not in touched:
                touched[folder_id] = {"added": [], "removed": []}
        if parent != move.to_folder_id:
            touched.setdefault(parent, {"added": [], "removed": []})["removed"].append(move.name)
            touched[move.to_folder_id]["added"].append(move.name)
    connection = explorer_connection()
    try:
        counts = {
            str(folder): (str(path or ""), int(direct_files or 0) + int(direct_folders or 0))
            for folder, path, direct_files, direct_folders in connection.execute(
                "SELECT entry_id, relative_path, direct_files, direct_folders "  # noqa: S608
                f"FROM {projection.folders} WHERE list_contains(?, entry_id)",
                [sorted(touched)],
            ).fetchall()
        }
    finally:
        connection.close()
    diff = [
        PlanFolderDiff(
            folder_id=folder,
            path=counts.get(folder, ("", 0))[0],
            before=counts.get(folder, ("", 0))[1],
            after=counts.get(folder, ("", 0))[1] + len(change["added"]) - len(change["removed"]),
            added=sorted(change["added"])[:DIFF_NAMES],
            removed=sorted(change["removed"])[:DIFF_NAMES],
        )
        for folder, change in sorted(
            touched.items(), key=lambda item: counts.get(item[0], ("", 0))[0]
        )
        if change["added"] or change["removed"]
    ]
    refused = sum(1 for move in checked if move.outcome == "refuse")
    warned = sum(1 for move in checked if move.outcome == "warn")
    # What moves, counted once: a file inside a folder another move carries is one file.
    moving = [sizes[held] for held in sorted(every_carried) if held in sizes]
    totals = {
        "moves": len(checked),
        "files": sum(1 for kind, _ in moving if kind == "file"),
        "folders": sum(1 for kind, _ in moving if kind == "folder"),
        "bytes": sum(size for _, size in moving),
        "refused": refused,
        "warnings": warned,
        "decisions": sum(move.decisions for move in checked),
    }
    message = (
        f"{len(checked)} move{'' if len(checked) == 1 else 's'}: {refused} refused, {warned} to "
        "look at before moving. Nothing was moved and nothing in the Record changed."
    )
    if held["run_id"] != run_id:
        message += (
            f" The plan was made against {held['run_id']} and is checked against {run_id}, the "
            "run read now."
        )
    return PlanCheck(
        schema_version=SCHEMA_VERSION,
        plan_id=held["plan_id"],
        version=int(held["version"]),
        versions=[int(v) for v in held["versions"]],
        name=str(held["name"]),
        note=held.get("note"),
        actor=str(held["actor"]),
        saved_at=str(held["saved_at"]),
        run_made_against=str(held["run_id"]),
        run_read=run_id,
        moves=checked,
        diff=diff,
        totals=totals,
        apply=PlanApply(available=False, why=APPLY_UNAVAILABLE),
        message=message,
    )


@route("explorer/plan", model=PlanCheck)
def plan_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> PlanCheck:
    return check_plan(context, query)


@route("explorer/plans", model=PlansResponse)
def plans_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> PlansResponse:
    del query
    return PlansResponse(
        schema_version=SCHEMA_VERSION,
        plans=[PlanSummary(**item) for item in list_plans(context.project)],
        apply=PlanApply(available=False, why=APPLY_UNAVAILABLE),
    )
