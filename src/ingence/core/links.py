"""What a link's text says about where its content lies, read from the text alone.

These functions read nothing but the string they are given. The census uses them to record the
characterisation, and the rules use them to read a link's place. Nothing here touches the
filesystem, so a link to another server is characterised on the first walk. Any rebind of the
stored census characterises it again, offline.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ingence.core.suffixes import is_extension

__all__ = ["characterise_link_target", "is_network_share"]

#: A link target written as a Windows network share, in any of the spellings a share's text takes:
#: the extended ``\\?\UNC\``, the object namespace ``\??\UNC\`` (which smbfs shows as
#: ``/??/UNC/``), and the plain ``\\server`` or ``//server``. What follows is the server, the
#: share and the path within it.
_NETWORK_SHARE = re.compile(
    r"^(?:[\\/]{2}\?[\\/]UNC[\\/]|[\\/]\?\?[\\/]UNC[\\/]|\\\\(?!\?)|//(?!\?))(?P<rest>.+)$",
    re.IGNORECASE,
)
_DRIVE = re.compile(r"^(?:[\\/]{2}\?[\\/]|[\\/]\?\?[\\/])?(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")


def characterise_link_target(target: str) -> dict[str, str | None]:
    """What a link's text says about where its content lies, read from the text alone.

    The function characterises an archive link in full and never opens it. A laboratory server
    can archive a file that nobody has opened for a long time. The file moves to another server
    and a link is left in its place, written as that server's path. The link's text says which
    server and share hold the file, the folder it sits in there, and its name. The name's
    extension is bound as its format like any other, so an archived ND2 still counts as an ND2.

    ``written_as`` is ``network_share`` for another server's share, ``drive`` for a drive
    letter, ``absolute`` or ``relative`` otherwise. Nothing here touches the filesystem.
    """
    spelled = target.strip()
    server: str | None = None
    share: str | None = None
    inside: list[str]
    network = _NETWORK_SHARE.match(spelled)
    drive = None if network else _DRIVE.match(spelled)
    if network:
        parts = [part for part in re.split(r"[\\/]+", network.group("rest")) if part]
        written_as = "network_share"
        server = parts[0] if parts else None
        share = parts[1] if len(parts) > 1 else None
        inside = parts[2:]
    elif drive:
        written_as = "drive"
        share = f"{drive.group('drive').upper()}:"
        inside = [part for part in re.split(r"[\\/]+", drive.group("rest")) if part]
    else:
        written_as = "absolute" if spelled.startswith(("/", "\\")) else "relative"
        inside = [part for part in re.split(r"[\\/]+", spelled) if part]
    name = inside[-1] if inside else None
    suffix = PurePosixPath(name).suffix.casefold().removeprefix(".") if name else ""
    return {
        "written_as": written_as,
        "server": server,
        "share": share,
        "folder": "/".join(inside[:-1]) or None,
        "name": name,
        "extension": suffix if suffix and is_extension(suffix) else None,
    }


def is_network_share(target: str) -> bool:
    """Whether a link's text names another server's share: the form an archive's link takes."""
    return bool(_NETWORK_SHARE.match(target.strip()))
