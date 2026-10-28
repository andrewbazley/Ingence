"""The agent interface: a short, private, structured view of a project.

Ingence uses no language model. What it offers a program a person runs beside it is a view of a
project shaped to one of four levels, and read-only answers to its queries, by two doors:

- ``ingence export brief`` writes ``brief.md`` and ``brief.json`` (:mod:`ingence.agent.brief`);
- ``ingence mcp`` answers read-only tools over standard input and output
  (:mod:`ingence.agent.mcp`).

Both read the project through the ``/api/v1`` routes in-process, and everything they say passes
through the one shaper (:mod:`ingence.agent.shape`), which works on the values first and then
reads the finished text once more. Nothing of a person's answers enters a project this way: an
answer comes in only through ``ingence record import``.
"""

from __future__ import annotations

from ingence.agent.shape import LEVELS, Aliases, Level, Shaper, Vocabulary, level_named

__all__ = ["LEVELS", "Aliases", "Level", "Shaper", "Vocabulary", "level_named"]
