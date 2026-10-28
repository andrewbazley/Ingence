"""Place a mention's shape against the pool it was found in, and tally a run's shapes.

An extractor reads a mention's shape off its own line: a line that brings in code, a
rooted path, a relative one, or a bare name (``extractors/mentions.py``). The bytes cannot
say whether a rooted path lies under a root of this pool. Resolution depends on that
distinction. A path under a root can be looked for. A path naming another machine is an
answer in itself, "outside the pool", however often it recurs. So the deepening pass,
which holds the project's roots, places each rooted mention as local or foreign. Nothing
here resolves anything.

The test is lexical. A mention is local when, spelled with forward slashes and in NFC, it
is a root's own path or lies beneath one. The comparison ignores case where the root's
filesystem ignores it. A Windows path written on another machine for this very drive under
another letter counts as foreign by that test.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.schema import MentionTally
from ingence.extractors.mentions import ABSOLUTE, lexical_shape

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "ABSOLUTE_FOREIGN",
    "ABSOLUTE_LOCAL",
    "SHAPES_KEY",
    "UNCLASSIFIED",
    "PoolRoot",
    "placed",
    "pool_roots",
    "shapes_of",
    "tally",
]

#: The observation key a mention's shapes are recorded under, beside ``references.paths``:
#: one per extractor per entry, a canonical JSON object from each path to its shape.
SHAPES_KEY = "references.shapes"

ABSOLUTE_LOCAL = "absolute_local"
ABSOLUTE_FOREIGN = "absolute_foreign"

#: What a tally calls a mention an earlier engine recorded without any shape: never
#: guessed at, and counted apart so a tally over a mixed run says how much it cannot say.
UNCLASSIFIED = "unclassified"

_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True, slots=True)
class PoolRoot:
    """One root of the pool, spelled as a mention is compared against it."""

    path: str
    case_insensitive: bool


def _spelled(path: str) -> str:
    """A path in NFC with forward slashes and no trailing one, as the comparison reads it."""
    return unicodedata.normalize("NFC", path).replace("\\", "/").rstrip("/")


def pool_roots(declarations: Iterable[object]) -> tuple[PoolRoot, ...]:
    """Return the pool's roots as the comparison reads them, from the project's declarations."""
    roots = []
    for declaration in declarations:
        mount = str(getattr(declaration, "mount_path", ""))
        sensitivity = getattr(declaration, "case_sensitivity", None)
        insensitive = str(getattr(sensitivity, "value", sensitivity)) == "insensitive"
        if mount:
            roots.append(PoolRoot(path=_spelled(mount), case_insensitive=insensitive))
    return tuple(roots)


def placed(path: str, lexical: str, roots: Sequence[PoolRoot]) -> str:
    """Return a mention's shape once placed against the pool: a rooted one as local or foreign.

    A rooted mention recorded without its leading separator, which the pattern cannot
    take when the path is not quoted, is read with one.
    """
    if lexical != ABSOLUTE:
        return lexical
    rooted = path if path.startswith(("/", "\\")) or _DRIVE.match(path) else "/" + path
    candidate = _spelled(rooted)
    for root in roots:
        left, right = (
            (candidate.casefold(), root.path.casefold())
            if root.case_insensitive
            else (candidate, root.path)
        )
        if left == right or left.startswith(right + "/"):
            return ABSOLUTE_LOCAL
    return ABSOLUTE_FOREIGN


def shapes_of(
    references: Sequence[str], lexical: Sequence[str], roots: Sequence[PoolRoot]
) -> dict[str, str]:
    """Return each reference's placed shape, taking its spelling's shape where none was read."""
    read = lexical if lexical else [lexical_shape(reference) for reference in references]
    return {
        reference: placed(reference, shape, roots)
        for reference, shape in zip(references, read, strict=True)
    }


def tally(
    mentions: Iterable[tuple[str, str, str | None, str | None]],
    extension_of: Mapping[str, str],
) -> tuple[MentionTally, ...]:
    """Return a run's mentions counted by the mentioning file's extension and by shape.

    ``mentions`` holds one item per observation of ``references.paths``: the entry, the
    observer, the paths as recorded, and the shapes recorded beside them, or none. A
    path is counted once per entry and observer; a file is counted once per shape it
    mentions anything in. Canonically ordered, so two runs' tallies compare.
    """
    counted: dict[tuple[str, str], int] = defaultdict(int)
    files: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entry_id, _observer, paths_json, shapes_json in mentions:
        paths = json.loads(paths_json) if paths_json else []
        shapes = json.loads(shapes_json) if shapes_json else {}
        extension = extension_of.get(entry_id, "")
        for path in paths:
            shape = shapes.get(path, UNCLASSIFIED)
            counted[(extension, shape)] += 1
            files[(extension, shape)].add(entry_id)
    return tuple(
        MentionTally(
            extension=extension,
            shape=shape,
            mentions=counted[(extension, shape)],
            files=len(files[(extension, shape)]),
        )
        for extension, shape in sorted(counted)
    )
