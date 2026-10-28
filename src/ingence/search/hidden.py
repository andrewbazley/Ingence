"""Remove a result from similar searches, without losing it.

A person who searched ``SubX FRAP`` and was shown a folder they know is not what they meant can say
so once. The next search about the same things then leaves that folder out.

What is stored, and where. Each act is one line, appended to ``workspace/search_hidden.jsonl`` in
the project. This is the Dashboard's workspace, beside drafts and checkpoints. The line never goes
to the Record. Hiding a result is a preference about what a person wants to see. It is not a
decision about what the data are, so it changes no claim and no inference reads it. A later
``unhide`` line reverses a hide. The file is never rewritten, so what was hidden and when stays
readable.

What "similar" means. A hidden result carries the concepts of the search it was hidden from. A
concept is each typed word's sense where the word bound one, and otherwise its folded spelling. So
hiding from ``FRAP`` also hides from ``photobleaching`` where both bind the same sense. A later
search leaves the result out where the two searches' concepts overlap by at least ``similar_at`` of
their union. This is Jaccard similarity, with one half as the default. ``SubX FRAP`` and
``SubX FRAP V2`` are similar. ``FRAP droplets`` is not. The threshold is a parameter and has not
been measured.

Nothing disappears silently. The response counts what it left out and why, and one click shows it
again. A hidden result is folded and never deleted. An answered row that leaves a review page and
comes back when asked for follows the same standard.

Who. Each line names the person that the page says is deciding, so on a shared machine one person's
hiding need not be another's. The default applies everyone's hides to every search.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.destinations import open_appending
from ingence.storage.layout import owned_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

__all__ = [
    "DEFAULT_SIMILAR_AT",
    "HIDDEN_FILE",
    "Hidden",
    "concept_keys",
    "hidden_for",
    "hide",
    "read_hidden",
    "similarity",
    "unhide",
]

#: The path where hides are kept, relative to the project. It lies beside the Dashboard's drafts.
HIDDEN_FILE = "workspace/search_hidden.jsonl"

#: Two searches are similar where their concepts overlap by at least this share of their union.
DEFAULT_SIMILAR_AT = 0.5


@dataclass(frozen=True, slots=True)
class Hidden:
    """One result hidden from searches like one search."""

    unit_id: str
    concepts: frozenset[str]
    actor: str
    at: str
    #: The query as typed. The list that a person reads when showing hidden results again uses it.
    typed: str = ""


def concept_keys(readings: Iterable[tuple[str | None, str]]) -> frozenset[str]:
    """One key per concept: ``sense:<term_id>`` where the word bound, else ``word:<folded>``.

    ``readings`` are (term id or None, typed spelling) pairs, one per non-excluded concept.
    """
    keys: set[str] = set()
    for term_id, typed in readings:
        keys.add(f"sense:{term_id}" if term_id else f"word:{typed.casefold()}")
    return frozenset(keys)


def similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Return the Jaccard similarity of two concept sets, or zero where either is empty."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _append(project: Path, line: dict[str, object]) -> None:
    # The path must lie in the project's own workspace, with no link on the way and no link at the
    # file itself.
    path = owned_path(project, HIDDEN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_appending(path) as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hide(
    project: Path, unit_id: str, concepts: frozenset[str], *, actor: str, typed: str = ""
) -> Hidden:
    """Hide one result from searches similar to one search.

    The function refuses a hide that names nobody as the actor.
    """
    who = actor.strip()
    if not who:
        message = "hiding a result names who hid it"
        raise ValueError(message)
    if not concepts:
        message = "a search with no concepts has nothing to be similar to"
        raise ValueError(message)
    at = _now()
    _append(
        project,
        {
            "act": "hide",
            "unit_id": unit_id,
            "concepts": sorted(concepts),
            "actor": who,
            "at": at,
            "typed": typed,
        },
    )
    return Hidden(unit_id=unit_id, concepts=concepts, actor=who, at=at, typed=typed)


def unhide(project: Path, unit_id: str, concepts: frozenset[str], *, actor: str) -> None:
    """Show a result again for searches like one it was hidden from."""
    _append(
        project,
        {
            "act": "unhide",
            "unit_id": unit_id,
            "concepts": sorted(concepts),
            "actor": actor.strip(),
            "at": _now(),
        },
    )


def read_hidden(project: Path) -> list[Hidden]:
    """Return every hide still in force, oldest first.

    A hide stands until an unhide of it follows. The reader skips a line that it cannot read and
    never stops on one. This file is a convenience, and a damaged line should cost one hidden
    result and not the search page.
    """
    path = project / HIDDEN_FILE
    if not path.is_file():
        return []
    standing: dict[tuple[str, frozenset[str]], Hidden] = {}
    for raw in path.read_text("utf-8").splitlines():
        try:
            line = json.loads(raw)
            key = (str(line["unit_id"]), frozenset(str(c) for c in line["concepts"]))
        except (ValueError, KeyError, TypeError):
            continue
        if line.get("act") == "unhide":
            standing.pop(key, None)
        elif line.get("act") == "hide":
            standing[key] = Hidden(
                unit_id=key[0],
                concepts=key[1],
                actor=str(line.get("actor", "")),
                at=str(line.get("at", "")),
                typed=str(line.get("typed", "")),
            )
    return list(standing.values())


def hidden_for(
    concepts: frozenset[str],
    hides: Sequence[Hidden],
    *,
    similar_at: float = DEFAULT_SIMILAR_AT,
    actor: str | None = None,
) -> dict[str, Hidden]:
    """Return the results that a search with these concepts leaves out, by unit.

    Each result comes with the hide that left it out.

    With ``actor`` given, only that person's hides apply. Without it, everyone's hides apply.
    """
    found: dict[str, Hidden] = {}
    for item in hides:
        if actor is not None and item.actor != actor:
            continue
        if similarity(concepts, item.concepts) >= similar_at:
            found.setdefault(item.unit_id, item)
    return found
