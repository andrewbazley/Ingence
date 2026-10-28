"""Revision 5, reading: what a content pass reads, in what order, and when it gives up on a
file or a root. This module declares no rules."""

from __future__ import annotations

__all__ = ["CHANGES", "RULES"]

#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = []
#: What this module changes in rules an earlier revision declared (supersede, override, enable).
CHANGES: list[dict[str, object]] = []
