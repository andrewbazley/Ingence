"""Revision 5, readouts: the readouts an overview, a goal and a check are counted from, each a
conclusion a page can cite. This module declares no rules."""

from __future__ import annotations

__all__ = ["CHANGES", "RULES"]

#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = []
#: What this module changes in rules an earlier revision declared (supersede, override, enable).
CHANGES: list[dict[str, object]] = []
