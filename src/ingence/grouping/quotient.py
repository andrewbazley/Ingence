"""The quotient: the classes a grouping rule's keys place entries in.

A grouping is an equivalence relation, and a key function states one without comparing
anything. Two entries stand in the relation exactly where their keys are equal, so the
classes are the sets of entries sharing a key. Forming them takes one pass over the entries
and no comparison of every pair. That keeps grouping linear on a pool where comparing pairs
would not finish.

A class of one groups nothing. An entry alone under its key is unlike every other entry in
that respect, which is a different fact from belonging to a group. A claim of membership in
a group of one would be a claim that nothing corroborates. So the function returns only
classes that reach a minimum size, and the minimum is two unless a caller says otherwise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["classes_of"]


def classes_of[Member](
    keyed: Iterable[tuple[str, Member]], *, minimum_members: int = 2
) -> dict[str, tuple[Member, ...]]:
    """Return the classes holding at least ``minimum_members`` members, by key.

    Keys come back in canonical order and members in the order they arrived, which
    the caller makes canonical, so iterating the result is deterministic however the
    entries were keyed.
    """
    members: dict[str, list[Member]] = {}
    for key, member in keyed:
        members.setdefault(key, []).append(member)
    return {
        key: tuple(members[key]) for key in sorted(members) if len(members[key]) >= minimum_members
    }
