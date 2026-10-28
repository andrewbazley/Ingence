"""The one plain sentence an assumption states for its rule.

The assumptions readout (``ingence.readouts``) builds each assumption's line from the rule's
plain sentence. The table of plain words is read only inside ``ingence.api``, so the readout asks
this function and never opens the table itself.
"""

from __future__ import annotations

import re

from ingence.api.plain_words import rule_sentence

__all__ = ["rule_first_sentence"]


def rule_first_sentence(rule_id: str, fallback: str = "") -> str:
    """Return the first sentence of the rule's plain sentence, without the rule's identifier handle.

    Where the rule has no plain sentence, return the first sentence of ``fallback``, the rule's
    own statement.
    """
    text = (rule_sentence(rule_id) if rule_id else None) or fallback
    flat = re.sub(r"\s*\([a-z0-9_]+\)\.?$", ".", " ".join(text.split()))
    found = re.match(r"(.+?[.!?])(?:\s|$)", flat)
    return found.group(1) if found else flat
