"""Refuse what a newer engine wrote, with a clear message.

An older engine refuses a newer Record or run and does not misread it. A minor version bump adds a
field or an enum value that a newer reader understands. An older reader cannot know what a value
it has never seen means. The Record and the current-run pointer are checked before anything is
validated, so the message says what happened and what to do. Without the check, a 1.21 engine that
meets a 1.22 ``withdraw`` event stops on "input should be 'confirm', ...", which says nothing about
versions.

The project file itself is refused only on a major difference (``core/project.py``). An engine of
any later version opens a project that an earlier engine created. The pieces inside the project
carry the minor version that a reader must understand.
"""

from __future__ import annotations

from ingence import SCHEMA_VERSION

__all__ = ["NewerSchemaError", "is_newer", "refuse_newer", "version_tuple"]


class NewerSchemaError(RuntimeError):
    """Something in a project was written by a newer schema than this engine reads."""


def version_tuple(version: str) -> tuple[int, ...]:
    """Return ``"1.22"`` as ``(1, 22)``.

    A malformed version reads as ``(0,)``, which is never newer.
    """
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except (AttributeError, ValueError):
        return (0,)


def is_newer(found: str, *, than: str = SCHEMA_VERSION) -> bool:
    """Whether ``found`` is a later schema than ``than``, compared numerically."""
    return version_tuple(found) > version_tuple(than)


def refuse_newer(found: str, *, what: str) -> None:
    """Raise :class:`NewerSchemaError` where ``found`` is newer than this engine's schema.

    ``what`` names the thing in words that a person reads, such as "the Record at <path> (decision
    12)", where 12 is the decision's place in the Record.
    """
    if is_newer(found):
        message = (
            f"{what} was written at schema {found}, and this engine reads schema "
            f"{SCHEMA_VERSION} and earlier. It is refused rather than misread: upgrade Ingence "
            "to the version that wrote it, or open a copy made before it was written."
        )
        raise NewerSchemaError(message)
