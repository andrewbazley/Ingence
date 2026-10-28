"""Paths mentioned in content, recorded as the strings they are and never resolved.

This module is not an extractor. Two extractors share it so that they mean the same thing by a
reference. The text extractor harvests mentions from a file's bytes and the
notebook extractor from a notebook's cells. Both feed ``ExtractorResult.references``,
which the proposing predicates ``derives_from_declared_reference`` and
``references_by_declared_path`` are declared to consume. Two patterns would make
what counts as a reference depend on which extractor happened to read the file,
and a rule reading the field has no way to tell them apart.

A mention is an observation, and resolution is an inference. Whether a string
names anything in the pool is a question over the whole Index, and it belongs to
the inference step. Recording the string is what lets the engine later say
"this script claims to write that file, and that file does not exist", which is a
finding rather than an error.

The pattern is deliberately conservative. It takes a string
with a separator and an extension, or a quoted absolute path. A bare name in the
same folder, ``read_csv("plate1.csv")``, is not a mention here: in code a
quoted token with a dot is a module path, a version or an encoding as often as it
is a file, and every false mention becomes a spurious "that file is missing"
finding once references resolve. Reading a call's arguments is what separates
them, and that needs language-aware parsing rather than a pattern.

Each mention carries the shape the extractor can read off its own line. On real laboratory folders,
most mentions are ``#include`` and ``import`` lines inside a bundled program, and most of the rest
are absolute paths that name other machines. Resolution built on such mentions would resolve the
wrong thing well, so a mention says what kind it is before anything resolves it: a line that brings
in code, an absolute path, a relative one, or a bare name. Whether an absolute path lies under a
root of this pool is not something the bytes can say, and the deepening pass decides it against the
project's roots.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ingence.core.suffixes import is_extension

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "ABSOLUTE",
    "BARE",
    "CODE_INCLUDE",
    "MARKUP",
    "MAX_MENTIONS",
    "RELATIVE",
    "lexical_shape",
    "mentioned_paths",
    "mentioned_paths_and_shapes",
]

#: The shapes a mention's own bytes can give it, strongest first. Where one path
#: occurs in several shapes in one file, the strongest is the one kept.
#:
#: - ``code_include``: on a line that brings in code (see ``_BRINGS_IN_CODE``), which is
#:   never a statement about data, whatever it names.
#: - ``absolute``: rooted, quoted or not. A quoted one is recorded whole. An unquoted
#:   one is recorded without its leading separator, which the pattern cannot take, and
#:   is still absolute.
#: - ``relative``: a separator, no root.
#: - ``bare``: no separator. The pattern finds none, and the shape is kept so that a change
#:   to the pattern is not a schema change.
CODE_INCLUDE = "code_include"
ABSOLUTE = "absolute"
RELATIVE = "relative"
BARE = "bare"
_STRENGTH = {CODE_INCLUDE: 0, ABSOLUTE: 1, RELATIVE: 2, BARE: 3}

#: A line that brings in code: C's and Objective-C's ``#include`` and ``#import``, an
#: ``import`` statement and Python's ``from ... import``, ``@import``, ``require(...)``
#: and ``require '...'``, R's ``source(...)``, MATLAB's ``addpath(...)``, and LaTeX's
#: ``\\input`` and ``\\include``. Language syntax rather than any laboratory's words, read
#: from the start of the line the mention sits on.
_BRINGS_IN_CODE = re.compile(
    rb"""^\s*(?:#\s*(?:include|import)\b|import\s|from\s+[\w.]+\s+import\b|@import\b"""
    rb"""|\\(?:input|include)\s*\{"""
    rb"""|.*?\brequire\s*(?:\(|['"])|.*?\bsource\s*\(|.*?\baddpath\s*\()"""
)

#: How many distinct mentions one file may record. The first ones in canonical
#: order, so two runs over one file record the same subset.
MAX_MENTIONS = 200

#: Paths and path-like strings mentioned in content: something with a separator
#: and an extension, or a quoted absolute path.
#:
#: This pattern is the definition of a mention, and it is not used to search. Run as one
#: pattern it is quadratic in the length of a run of path characters, because its unquoted
#: alternative can start at every character of a run and retry the whole rest of the run from
#: each. 8,000 such characters cost a second, and a 100,000-character run, such as a long hex or
#: identifier line in a script, costs minutes. :func:`_mentions` finds exactly what
#: ``_REFERENCE.findall`` finds, in time linear in the content. A test holds the two equal over
#: thousands of generated strings, so this pattern stays as the definition that both are held to.
_REFERENCE = re.compile(
    rb"""(?:['"]([A-Za-z]:[\\/][^'"\n]{1,240})['"])"""
    rb"""|(?:['"](/[^'"\n]{1,240})['"])"""
    rb"""|([A-Za-z0-9_.\-]+(?:[/\\][A-Za-z0-9_.\-]+)+\.[A-Za-z0-9]{1,8})"""
)

#: The two quoted alternatives of ``_REFERENCE``, in its order, tried where a quote is.
_QUOTED = re.compile(rb"""['"]([A-Za-z]:[\\/][^'"\n]{1,240})['"]|['"](/[^'"\n]{1,240})['"]""")

#: Where a mention can begin: a quote, or a whole run of path characters holding a
#: separator, which is the only place the unquoted alternative can match. The run is
#: anchored at its first character, so no character inside it is tried twice.
_CANDIDATE = re.compile(
    rb"""['"]|(?<![A-Za-z0-9_.\-/\\])[A-Za-z0-9_.\-/\\]*[/\\][A-Za-z0-9_.\-/\\]*"""
)

_QUOTES = frozenset(b"'\"")
_SEPARATORS = frozenset(b"/\\")
_EXTENSION_CHARACTERS = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
_LONGEST_EXTENSION = 8


def mentioned_paths(payload: bytes) -> tuple[str, ...]:
    """Return the distinct paths mentioned in some bytes, sorted and bounded."""
    return mentioned_paths_and_shapes(payload)[0]


def mentioned_paths_and_shapes(payload: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the distinct paths mentioned, sorted and bounded, and each one's shape.

    The two tuples are parallel. A path's shape is the strongest of the shapes its
    occurrences in these bytes have, so a header named once in an ``#include`` and once
    in a comment is a code include.
    """
    shapes: dict[str, str] = {}
    lines: dict[int, bool] = {}
    # Find where a mention's line begins incrementally. Searching back to the start of the
    # payload for every mention is quadratic on a file whose content is one line, such as
    # minified markup or a long JSON document. On real laboratory folders, the search back took
    # most of the text extractor's time. Mentions arrive in the order they stand, so each search
    # only needs to cover the bytes since the last one.
    line_start = 0
    searched_to = 0
    for start, match, rooted in _mentions_at(payload):
        path = match.decode("utf-8", "replace")
        if not _is_a_path(path, rooted=rooted):
            continue
        if start < searched_to:
            line_start, searched_to = payload.rfind(b"\n", 0, start) + 1, start
        else:
            newline = payload.rfind(b"\n", searched_to, start)
            if newline != -1:
                line_start = newline + 1
            searched_to = start
        if line_start not in lines:
            line_end = payload.find(b"\n", start)
            line = payload[line_start : line_end if line_end != -1 else len(payload)]
            lines[line_start] = _BRINGS_IN_CODE.match(line) is not None
        if lines[line_start]:
            shape = CODE_INCLUDE
        elif rooted:
            shape = ABSOLUTE
        else:
            shape = lexical_shape(path)
        held = shapes.get(path)
        if held is None or _STRENGTH[shape] < _STRENGTH[held]:
            shapes[path] = shape
    paths = tuple(sorted(shapes))[:MAX_MENTIONS]
    return paths, tuple(shapes[path] for path in paths)


#: Characters no path is written with and markup always is: neither is legal in a Windows file
#: name, and a quoted "rooted path" holding one is the tail of one element and the head of the
#: next (``"/></a:b></``) in a file whose markup is on one line.
MARKUP = frozenset("<>")


def _is_a_path(path: str, *, rooted: bool) -> bool:
    """Whether a harvested string is a path at all, by two rules of what paths are spelled with.

    A rooted mention holding markup is markup. An unrooted one must end in an extension by the
    census's own rule. On real laboratory folders, most rooted mentions that read as another
    machine's paths were markup, and nearly all of a cytometry workspace's mentions were numbers.
    """
    if rooted:
        return not (MARKUP & set(path))
    return _ends_in_an_extension(path)


def _ends_in_an_extension(path: str) -> bool:
    """Whether an unrooted mention's last suffix is an extension by the census's rule.

    A string with a separator and digits after its last dot is a number, not a path. A cytometry
    workspace writes gate and ratio expressions such as ``Comp-FL1-A/1.0000`` by the thousand, and
    read as relative paths they would fill the bounded list of a file's mentions before its real
    data references and resolve to nothing, which reads as a missing file. One rule decides what an
    extension is for the census and for this harvest, so the two cannot disagree. A rooted mention
    is quoted and needs no extension.
    """
    last = path.replace("\\", "/").rsplit("/", 1)[-1]
    return "." in last and is_extension(last.rsplit(".", 1)[-1])


def lexical_shape(path: str) -> str:
    """Return the shape a path's own spelling gives it, with no line to read beside it.

    For a reference an extractor reports without a shape, such as a relationship an
    Office package declares: rooted, relative or bare, by its first characters.
    """
    if path.startswith(("/", "\\", "~")) or re.match(r"[A-Za-z]:[\\/]", path):
        return ABSOLUTE
    if "/" in path or "\\" in path:
        return RELATIVE
    return BARE


def _mentions(payload: bytes) -> Iterator[bytes]:
    """Yield every string ``_REFERENCE.findall`` would, in its order, in linear time."""
    for _, match, _ in _mentions_at(payload):
        yield match


def _mentions_at(payload: bytes) -> Iterator[tuple[int, bytes, bool]]:
    """Yield each mention ``_REFERENCE.findall`` would find: where it starts, it, and if rooted.

    The scan ``findall`` makes, made directly. At a quote only the quoted
    alternatives can match, and a match skips everything inside it: both are rooted,
    a drive or a leading separator. Elsewhere only the unquoted alternative can, and
    only inside a run of path characters that holds a separator, which
    :func:`_unquoted` reads once, whole; one of its mentions is rooted where a
    separator stands before its first character.
    """
    position = 0
    while True:
        found = _CANDIDATE.search(payload, position)
        if found is None:
            return
        start = found.start()
        if payload[start] in _QUOTES:
            quoted = _QUOTED.match(payload, start)
            if quoted is None:
                position = start + 1
                continue
            group = 1 if quoted.group(1) else 2
            yield quoted.start(group), quoted.group(group), True
            position = quoted.end()
            continue
        for offset, match, rooted in _unquoted(found.group()):
            yield start + offset, match, rooted
        position = found.end()


def _unquoted(run: bytes) -> Iterator[tuple[int, bytes, bool]]:
    """Yield the unquoted mentions in one run of path characters, left to right.

    What the pattern finds, worked out rather than searched for. A run splits into
    *chains*: segments of name characters joined by single separators, a doubled
    separator ending a chain. From a chain's first character the pattern takes the
    first segment whole, then every further segment greedily, and gives characters
    back from the right until a dot followed by a letter or digit ends an extension.
    So its match runs from the chain's start to the rightmost such dot in the last
    segment after the first that holds one, with up to eight characters after it.
    Nothing later in the chain can match once that has: every segment after the one
    it ended in holds no such dot. A chain with none after its first segment holds no
    mention at all, from any of its characters.
    """
    length = len(run)
    index = 0
    while index < length:
        while index < length and run[index] in _SEPARATORS:
            index += 1
        if index >= length:
            return
        chain_start = index
        segments: list[tuple[int, int]] = []
        while True:
            segment_start = index
            while index < length and run[index] not in _SEPARATORS:
                index += 1
            segments.append((segment_start, index))
            if index + 1 < length and run[index + 1] not in _SEPARATORS:
                index += 1
                continue
            break
        for segment_start, segment_end in reversed(segments[1:]):
            dot = _extension_dot(run, segment_start, segment_end)
            if dot is None:
                continue
            end = dot + 1
            while (
                end < segment_end
                and end - dot <= _LONGEST_EXTENSION
                and run[end] in _EXTENSION_CHARACTERS
            ):
                end += 1
            yield chain_start, run[chain_start:end], chain_start > 0
            break


def _extension_dot(run: bytes, start: int, end: int) -> int | None:
    """Return the rightmost dot in a segment that ends an extension, or None.

    A dot counts where at least one character precedes it in the segment and a
    letter or digit follows it, which is the pattern's ``+`` before ``\\.`` and its
    ``[A-Za-z0-9]{1,8}`` after.
    """
    dot = run.rfind(b".", start + 1, end - 1)
    while dot != -1:
        if run[dot + 1] in _EXTENSION_CHARACTERS:
            return dot
        dot = run.rfind(b".", start + 1, dot)
    return None
