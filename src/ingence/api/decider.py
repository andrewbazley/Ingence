"""Who is deciding on this machine: asked once, kept outside every project, never guessed.

User decisions are recorded, and Ingence needs no login system. The Dashboard logs who made every
decision, and the person does not type a name in each time.

A name kept in the browser would be asked for again in every browser and on every port that a server
binds, because a browser keeps what a page stores per origin, and an origin includes the port. So
the engine keeps the name once per person per machine, in a small file in the person's own
configuration directory:

- macOS: ``~/Library/Application Support/Ingence/decider.json``;
- Windows: ``%APPDATA%\\Ingence\\decider.json``;
- elsewhere: ``$XDG_CONFIG_HOME/ingence/decider.json``, or ``~/.config/ingence/decider.json``;
- anywhere, ``INGENCE_CONFIG_HOME`` names the directory instead. A test uses it to point at a
  temporary directory and never at the real one.

The file lies outside every project and outside every root. The engine still writes a decision to
the project's Record, attributed to this name. The name itself belongs to the person on the machine
and not to any one project, so it is not in one. The engine refuses to write it into a root that the
project indexes (:func:`refuse_inside_roots`), because nothing the engine writes lands in the data
that it reads.

The name is never guessed. Nothing here reads the operating system's login name, an environment
variable that names a user, or anything else that a name could be inferred from. The name is what a
person typed, or there is none, and where there is none a page asks. Every write still states its
actor (the server's ``_actor``). A page sends this name with each decision. So a decision made by a
command-line client, or by a person who changes the name for one decision, says so itself.

:func:`machine_decider` is the one small function that a route calls to learn the name. It returns
None and never a guess.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import write_atomically

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "CONFIG_HOME_VARIABLE",
    "DECIDER_FILE",
    "MAX_NAME_LENGTH",
    "Decider",
    "DeciderRefusedError",
    "decider_path",
    "machine_decider",
    "read_decider",
    "refusal_for",
    "refuse_inside_roots",
    "user_config_directory",
    "write_decider",
]

#: The environment variable naming the configuration directory outright. Tests set it.
CONFIG_HOME_VARIABLE = "INGENCE_CONFIG_HOME"

#: The file holding who decides on this machine, inside the configuration directory.
DECIDER_FILE = "decider.json"

#: The longest name that the engine keeps. A name is a person's name or initials. The limit keeps a
#: pasted paragraph out of every event's actor, where it would repeat once per decision.
MAX_NAME_LENGTH = 120

#: The version of the file's shape, so that a later change to it is readable as one.
_FILE_VERSION = 1


class DeciderRefusedError(ValueError):
    """The name was not stored, and the message says why."""


@dataclass(frozen=True, slots=True)
class Decider:
    """Who decides on this machine, when that was said, and where the name is kept."""

    name: str
    set_at: str
    stored_in: Path

    def as_payload(self) -> dict[str, Any]:
        return {"decider": self.name, "set_at": self.set_at, "stored_in": str(self.stored_in)}


def user_config_directory(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the person's own configuration directory for Ingence, by the platform's convention.

    Every input is a parameter, so a test can name each platform's answer without being on it.
    This function creates nothing. :func:`write_decider` creates the directory when it first
    writes.
    """
    environ = os.environ if environ is None else environ
    named = (environ.get(CONFIG_HOME_VARIABLE) or "").strip()
    if named:
        return Path(named)
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Ingence"
    if platform.startswith("win"):
        roaming = (environ.get("APPDATA") or "").strip()
        return (Path(roaming) if roaming else home / "AppData" / "Roaming") / "Ingence"
    xdg = (environ.get("XDG_CONFIG_HOME") or "").strip()
    return (Path(xdg) if xdg else home / ".config") / "ingence"


def decider_path(config_home: Path | None = None) -> Path:
    """Return where the name is kept: ``config_home`` where given, else the person's directory."""
    return (config_home if config_home is not None else user_config_directory()) / DECIDER_FILE


def _checked_name(name: str) -> str:
    spelled = " ".join(str(name).split())
    if not spelled:
        message = "a name says who is deciding; an empty one attributes decisions to nobody"
        raise DeciderRefusedError(message)
    if len(spelled) > MAX_NAME_LENGTH:
        message = f"a name is at most {MAX_NAME_LENGTH} characters; it is written with every event"
        raise DeciderRefusedError(message)
    if any(not character.isprintable() for character in spelled):
        message = "a name is written as it reads; it holds a character that does not print"
        raise DeciderRefusedError(message)
    return spelled


def read_decider(config_home: Path | None = None) -> Decider | None:
    """Return who decides on this machine, or None.

    The result is None where nobody has said, or where the file cannot be read. An unreadable file
    gives None and not an error, because the page's answer to "nobody is known" is to ask. Asking is
    also the right answer to a file that somebody broke.
    """
    path = decider_path(config_home)
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(held, dict):
        return None
    try:
        name = _checked_name(str(held.get("decider") or ""))
    except DeciderRefusedError:
        return None
    return Decider(name=name, set_at=str(held.get("set_at") or ""), stored_in=path)


def write_decider(name: str, *, set_at: str, config_home: Path | None = None) -> Decider:
    """Keep ``name`` as who decides on this machine, replacing any earlier name, and return it.

    The function writes the file whole or not at all, by moving a temporary file into place. So a
    reader never sees half a name. It replaces an earlier name and does not keep it. The decisions
    made under the earlier name keep it in the Record, which is where the engine keeps who made each
    decision for good.
    """
    spelled = _checked_name(name)
    path = decider_path(config_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": _FILE_VERSION, "decider": spelled, "set_at": set_at}
    write_atomically(path, json.dumps(document, indent=1) + "\n")
    return Decider(name=spelled, set_at=set_at, stored_in=path)


def refuse_inside_roots(path: Path, roots: Iterable[str]) -> None:
    """Refuse a configuration file that would land inside a root that the project indexes.

    The function compares the paths as spelled. It never opens or lists a root, because a root may
    be a share that is not mounted, and asking the filesystem about it would reach for it.
    """
    spelled = PurePath(os.path.abspath(path))
    for root in roots:
        declared = PurePath(root)
        if spelled == declared or declared in spelled.parents:
            message = (
                f"{path} is inside {root}, a root this project indexes, and the engine writes "
                f"nothing into the data it reads. Set {CONFIG_HOME_VARIABLE} to a directory "
                "outside it."
            )
            raise DeciderRefusedError(message)


def refusal_for(
    name: str, roots: Iterable[str] = (), *, config_home: Path | None = None
) -> str | None:
    """Return why ``name`` cannot be kept as who decides on this machine, in words, or None.

    The function writes nothing. It refuses an empty, too long or unprintable name. It refuses a
    name that ends ``_agent``, which marks the answers of an imported file as an agent's
    (``record/imported.py``), so a person's own never read as one. It also refuses a file that
    would land inside a root that the project indexes (``roots``, compared as spelled).
    """
    from ingence.record.imported import AGENT_SUFFIX

    try:
        spelled = _checked_name(name)
        if spelled.casefold().endswith(AGENT_SUFFIX):
            message = (
                f"a name ending {AGENT_SUFFIX} marks answers an agent wrote; a person's own "
                "name is recorded with their decisions"
            )
            raise DeciderRefusedError(message)
        refuse_inside_roots(decider_path(config_home), roots)
    except DeciderRefusedError as refused:
        return str(refused)
    return None


def machine_decider(project: Path | None = None, *, config_home: Path | None = None) -> str | None:
    """Return the name of whoever decides on this machine, or None. The result is never a guess.

    A route makes this one call. ``project`` is accepted for callers that pass it, and it does not
    change the answer. The name is the person's on this machine, the same for every project.
    """
    del project
    found = read_decider(config_home)
    return found.name if found is not None else None
