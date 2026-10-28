"""The projects opened on this machine, for the landing page.

The list is small and lives in the person's own configuration directory (``recent_projects.json``,
beside ``decider.json``). ``api/decider.py`` says where that directory is on each platform, and
``INGENCE_CONFIG_HOME`` moves it, which is how a test keeps it apart. The list holds each project's
folder and when ``ingence see`` or the landing page last opened it, newest first. It keeps nothing
about what a project holds, because a project can change or be deleted between openings. The
landing page shows each project's roots, how many runs it has, when the last one was, and whether it
is there at all. The page reads these from the project's own files when it asks
(:func:`describe_recent`). It reads small files only: the project's identity, its run folders'
names and its pointer. No catalog is opened and no root is touched.

The list is never a decision and never in a project. Losing the file loses the list and nothing
else. A file that cannot be read is an empty list, and the next opening writes it again.

The list is never in a folder that the project reads. A person may read a folder that holds this
one, such as their whole home folder. The list is then not written, and the project is not
remembered. :func:`remember_project` raises :class:`RecentNotKeptError`, whose message says why in
words, for the caller to say once. The same judgment applies as for every file that the engine
writes on request. It goes by what the system reaches and not by spelling
(``core/destinations.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ingence.api.decider import user_config_directory
from ingence.core.destinations import write_atomically

__all__ = [
    "RECENT_FILE",
    "RECENT_KEPT",
    "RecentNotKeptError",
    "describe_project",
    "describe_recent",
    "name_of",
    "read_recent",
    "recent_path",
    "remember_project",
]

#: The file's name, inside the configuration directory.
RECENT_FILE = "recent_projects.json"

#: The number of projects that the list keeps, newest first. A laboratory member has a handful of
#: projects, and the landing page shows them all.
RECENT_KEPT = 20

_FILE_VERSION = 1


class RecentNotKeptError(ValueError):
    """The project was not added to the list, and the message says why, in words."""


def recent_path(config_home: Path | None = None) -> Path:
    """Return where the list is kept: ``config_home`` where given, else the person's directory."""
    return (config_home if config_home is not None else user_config_directory()) / RECENT_FILE


def read_recent(config_home: Path | None = None) -> list[dict[str, str]]:
    """Return the projects opened here, newest first, each as ``{"path", "opened_at"}``.

    The result is empty where the file is missing or cannot be read.
    """
    try:
        held = json.loads(recent_path(config_home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    listed = held.get("projects") if isinstance(held, dict) else None
    if not isinstance(listed, list):
        return []
    return [
        {
            "path": str(item["path"]),
            "opened_at": str(item.get("opened_at") or ""),
            **({"name": str(item["name"])} if item.get("name") else {}),
        }
        for item in listed
        if isinstance(item, dict) and item.get("path")
    ]


def _refuse_inside_roots(path: Path, project: Path) -> None:
    """Raise :class:`RecentNotKeptError` where the list's file would lie in a root of ``project``.

    The identity walk decides, as it does for every file that the engine writes on request
    (``core/destinations.py``). It goes by what the system reaches. So another letter case, a link,
    or a ``..`` after one is the folder it is. A project that cannot be opened has no roots to ask.
    """
    from ingence.core.destinations import declared_roots, where_refused
    from ingence.core.project import Project

    try:
        opened = Project.open(project)
        roots = declared_roots(opened)
    except Exception:  # said by describe_project when the landing page lists it
        return
    refusal = where_refused(path, project=opened.path, roots=roots)
    if refusal is not None and refusal.kind == "root":
        message = (
            f"This project is not added to the list of recent projects: the list is kept in "
            f"{path}, which is inside {refusal.root}, a folder this project reads, and Ingence "
            f"writes nothing into the folders it reads. Set INGENCE_CONFIG_HOME to a folder "
            f"outside it to keep the list."
        )
        raise RecentNotKeptError(message)


def remember_project(
    project: Path,
    *,
    opened_at: str,
    name: str | None = None,
    config_home: Path | None = None,
) -> list[dict[str, str]]:
    """Put ``project`` first in the list, as opened at ``opened_at``, and return the list.

    ``name`` is what a person called the project on the landing page, kept with it. A project made
    before projects carried a name holds none of its own, and its folder's name stands in where
    none was given. The function writes the file whole or not at all, by moving a temporary file
    into place. A project already listed moves to the top and does not appear twice, and it keeps
    the name it had. The function raises :class:`RecentNotKeptError` and writes nothing where the
    file would lie inside a root that the project reads.
    """
    path = recent_path(config_home)
    _refuse_inside_roots(path, project)
    spelled = str(project.expanduser().resolve())
    held = read_recent(config_home)
    before = next((item for item in held if item["path"] == spelled), {})
    kept = [item for item in held if item["path"] != spelled]
    called = name or before.get("name")
    first = {"path": spelled, "opened_at": opened_at, **({"name": called} if called else {})}
    listed = [first, *kept][:RECENT_KEPT]
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": _FILE_VERSION, "projects": listed}
    write_atomically(path, json.dumps(document, indent=1) + "\n")
    return listed


def describe_project(path: Path, *, listed_name: str | None = None) -> dict[str, Any]:
    """Return what the landing page says of one project, read from its small files.

    ``name`` is the name in the project's own ``project.json``. A project made before projects
    were named has none there. It shows ``listed_name``, the name that this machine's list holds
    for it, or else its folder's name, and ``named_by_folder`` says that it is the folder's.

    ``exists`` is False where the folder is gone or is no longer a project, and the page says so.
    ``runs`` counts the run folders published. ``last_run_at`` is when the current run was
    published (the pointer's own time), or None where it has none.
    """
    from ingence.core.project import NotAProjectError, Project
    from ingence.storage.commit import read_pointer
    from ingence.storage.layout import RUNS_DIR

    said: dict[str, Any] = {"path": str(path), "name": path.name, "named_by_folder": True}
    try:
        opened = Project.open(path)
        said["name"] = opened.identity.name or listed_name or path.name
        # A project that nobody named shows its folder's name and says so. The card offers Rename.
        said["named_by_folder"] = not (opened.identity.name or listed_name)
    except NotAProjectError:
        return {**said, "exists": False, "why": "This folder is no longer an Ingence project."}
    except Exception as error:  # the page says that a project cannot be read and does not break
        return {**said, "exists": False, "why": f"It could not be read: {error}"}
    roots = [
        {"label": root.label, "path": root.mount_path, "listing": root.listing is not None}
        for root in opened.identity.roots
    ]
    try:
        runs = sum(
            1
            for item in (path / RUNS_DIR).iterdir()
            if item.is_dir() and not item.name.startswith(".")
        )
    except OSError:
        runs = None
    try:
        pointer = read_pointer(path)
    except Exception:  # the Dashboard says so when it opens a pointer that a newer engine wrote
        pointer = None
    return {
        **said,
        "exists": True,
        "roots": roots,
        "runs": runs,
        "has_run": pointer is not None,
        "last_run_at": pointer.updated_at if pointer is not None else None,
    }


def describe_recent(config_home: Path | None = None) -> list[dict[str, Any]]:
    """Return every project in the list, newest first, each described.

    :func:`describe_project` describes each project.
    """
    return [
        {
            **describe_project(Path(item["path"]), listed_name=item.get("name")),
            "opened_at": item["opened_at"],
        }
        for item in read_recent(config_home)
    ]


def name_of(project: Path, config_home: Path | None = None) -> str:
    """Return what the project is called.

    The name is the one in the project's own ``project.json``, which any computer that opens the
    project reads. Otherwise it is the name given to the project on this machine's list, or else
    its folder's name. A project made before projects carried a name has none in its file, and it
    keeps the name that this machine's list holds for it.
    """
    from ingence.core.project import NotAProjectError, Project

    try:
        named = Project.open(project).identity.name
    except (NotAProjectError, OSError, ValueError):
        named = None
    if named:
        return named
    spelled = str(project.expanduser().resolve())
    for item in read_recent(config_home):
        if item["path"] == spelled and item.get("name"):
            return item["name"]
    return project.name
