"""``ingence fix --shareable``: the report with the names of the data written as placeholders.

``ingence fix`` prints the project's path, the path of a root it cannot reach, run identifiers
(which carry a time), the exclusion patterns a person wrote and, with ``--verify``, sample rows.
Pasted into an email, those name a laboratory's folders and people. The shareable report is the
same report with each of them written as a placeholder, so it can be sent to the developer.

======================  ==========================================================
What the report prints  What the shareable report prints
======================  ==========================================================
the project's path      ``<project>``
a root's path           ``<root 1>``, ``<root 2>``, in the order the project lists them
a path under a root     ``<root 1>/...``, the rest of the path left out
a pattern a person      ``<pattern 1>``, ``<pattern 2>``; the patterns every project
wrote                   starts with are engine settings and stay as they are
a run                   ``<run 1>``, ``<run 2>``, oldest first
``--verify`` rows       left out, the count of differing rows kept
======================  ==========================================================

The replacement is made on the report's values, before a line is formatted. ``AsWritten`` and
``Placeholders`` below are the one object ``fix`` asks for every value that came from the data; no
regular expression is run over the printed text to find a name. Without ``--shareable`` the object
returns every value as it came, so the report is the one it always was, to the byte.

A backstop reads the finished text. Every spelling of the project's path, of each root, of each
run and of each pattern a person wrote is searched for once more, and any survivor is replaced and
counted (``Placeholders.replaced``). A report in which it replaced something says so on its last
line. A test proves the count is zero on a fixture whose every name is invented, which shows that
the first pass caught everything.

The backstop does not search for a root, a project or a pattern that is too short to search
without damaging the report's own words (a root written ``/``, a pattern of one letter). It also
does not search for a name that is only part of a root's path (a root's last folder, on its own).
The first pass does not print either.
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from rich.console import Console

from ingence.core.settings import ScanSettings

if TYPE_CHECKING:
    import io
    from collections.abc import Callable, Sequence

    from ingence.core.project import Project
    from ingence.core.schema import ExcludedNames

#: How the project's path is written.
PROJECT = "<project>"
#: Spellings shorter than this are not searched for in finished text: a root written ``/`` would
#: turn every slash into a placeholder.
SHORTEST_PATH = 4
#: A pattern with fewer letters and digits than this is not searched for in finished text.
SHORTEST_PATTERN = 3
#: A terminal wide enough that nothing the report prints is split over two lines.
WIDTH = 10_000

#: A placeholder, so the backstop never reads inside one (a pattern a person wrote as ``root``).
_PLACEHOLDER = re.compile(r"(<(?:project|root \d+|pattern \d+|run \d+)>(?:/\.\.\.)?)")
#: What follows a root's path when the path goes on beneath it and nothing quotes the path: up to
#: the next space.
_BENEATH = re.compile(r"[/\\][^\s'\"<>]*")


class AsWritten:
    """The report without ``--shareable``: every value is returned as it came.

    ``ingence fix`` asks this object for every value that came from the data. The shareable
    report asks :class:`Placeholders` the same questions.
    """

    #: Whether the report is the shareable one (its ``--verify`` sample rows are left out).
    shareable = False

    def opened(self, project: Project) -> None:
        """Called once, with the project the report is about, as soon as it is open."""

    def project(self, path: Path | str) -> str:
        """The project's path."""
        return str(path)

    def root(self, path: str) -> str:
        """A root's path, as the project declares it."""
        return path

    def pattern(self, text: str) -> str:
        """Something a person typed: an exclusion pattern, a value in a settings file."""
        return text

    def run(self, run_id: str) -> str:
        """A run's identifier."""
        return run_id

    def message(self, text: str) -> str:
        """A message the operating system or the engine wrote, which can hold a path."""
        return text

    def exclusions(self, excluded: Sequence[ExcludedNames]) -> Sequence[ExcludedNames]:
        """What a census left out, with each record's pattern as it is to be printed."""
        return excluded

    def typed(self) -> Callable[[str], str] | None:
        """How a rule file's typed values are written, or None to write them as they are."""
        return None


class Placeholders(AsWritten):
    """The shareable report's values: each name of the data is a numbered placeholder.

    Numbers are given once and kept, so ``<run 2>`` is the same run wherever it is printed. A root
    is numbered in the order the project lists its roots. A pattern is numbered in the order of the
    project's exclusion setting, or in the order it is met if the setting does not hold it. A run is
    numbered oldest first.
    """

    shareable = True

    def __init__(self, asked: Path | None) -> None:
        # ``asked`` is the project directory as the person named it, or None (the working
        # directory is searched): the message that no project is there holds it, before any
        # project is open.
        self._project_spellings: set[str] = set()
        self._roots: dict[str, int] = {}
        self._patterns: dict[str, int] = {}
        self._runs: dict[str, int] = {}
        self._defaults = frozenset(ScanSettings().exclude_globs)
        #: How many names the backstop replaced after the first pass (it should be none).
        self.replaced = 0
        if asked is None:
            with contextlib.suppress(OSError):
                self._add_project(Path.cwd())
        else:
            self._add_project(asked)

    # -- what the project holds ------------------------------------------------------------------

    def opened(self, project: Project) -> None:
        self._add_project(project.path)
        for number, declaration in enumerate(project.identity.roots, start=1):
            spellings = {declaration.mount_path, declaration.root_uri}
            parts = urlparse(declaration.root_uri)
            if parts.scheme == "file":
                spellings.add(unquote(parts.path))
            for spelling in spellings:
                for each in _spellings(spelling):
                    self._roots.setdefault(each, number)
        for text in project.settings.scan.exclude_globs:
            self.pattern(text)
        # A run's identifier starts with its time, so sorting them puts the oldest first. The
        # runs an interrupted command left in staging are runs too.
        runs: set[str] = set()
        if (project.path / "runs").is_dir():
            runs.update(item.name for item in (project.path / "runs").iterdir() if item.is_dir())
        if (project.path / ".tmp").is_dir():
            runs.update(
                item.name.removesuffix(".partial")
                for item in (project.path / ".tmp").glob("*.partial")
            )
        for name in sorted(runs):
            self.run(name)

    def _add_project(self, path: Path) -> None:
        """Remember the project's path in every absolute spelling it can be printed in.

        A relative spelling (``-p data``) is never remembered: no message prints one, and a word
        such as ``data`` would be replaced wherever the report uses it. Nothing here may raise:
        a symlink loop or an unknown home folder makes ``resolve`` and ``expanduser`` fail, and
        the report then goes on with the spellings that could be made.
        """
        spelled = [path]
        with contextlib.suppress(OSError, RuntimeError):
            spelled.append(path.expanduser())
        with contextlib.suppress(OSError, RuntimeError):
            spelled.append(path.expanduser().resolve())
        for each in spelled:
            if each.is_absolute():
                self._project_spellings.update(_spellings(str(each)))

    # -- the values ------------------------------------------------------------------------------

    def project(self, path: Path | str) -> str:
        self._project_spellings.update(_spellings(str(path)))
        return PROJECT

    def root(self, path: str) -> str:
        for each in _spellings(path):
            if each in self._roots:
                return f"<root {self._roots[each]}>"
        number = max(self._roots.values(), default=0) + 1
        for each in _spellings(path):
            self._roots[each] = number
        return f"<root {number}>"

    def pattern(self, text: str) -> str:
        if text in self._defaults:
            return text
        return f"<pattern {self._patterns.setdefault(text, len(self._patterns) + 1)}>"

    def run(self, run_id: str) -> str:
        return f"<run {self._runs.setdefault(run_id, len(self._runs) + 1)}>"

    def message(self, text: str) -> str:
        return self._without_paths(text)[0]

    def exclusions(self, excluded: Sequence[ExcludedNames]) -> Sequence[ExcludedNames]:
        return [
            item.model_copy(update={"pattern": self.pattern(item.pattern)})
            if item.pattern is not None
            else item
            for item in excluded
        ]

    def typed(self) -> Callable[[str], str] | None:
        return self.pattern

    # -- the backstop ----------------------------------------------------------------------------

    def backstop(self, text: str) -> str:
        """The finished report with anything left of the project, a root or a pattern replaced.

        Counts what it replaced in :attr:`replaced`.
        """
        for step in (self._without_paths, self._without_runs, self._without_patterns):
            # Split again before each step, so a placeholder an earlier step wrote is never read.
            pieces = _PLACEHOLDER.split(text)
            for index in range(0, len(pieces), 2):
                pieces[index], count = step(pieces[index])
                self.replaced += count
            text = "".join(pieces)
        return text

    def _without_paths(self, text: str) -> tuple[str, int]:
        """``text`` with the project's path and each root's path replaced, and how many were."""
        written: dict[str, str] = {
            **dict.fromkeys(self._project_spellings, PROJECT),
            **{each: f"<root {number}>" for each, number in self._roots.items()},
        }
        needles = sorted((each for each in written if len(each) >= SHORTEST_PATH), key=len)
        if not needles:
            return text, 0
        longest_first = "|".join(re.escape(each) for each in reversed(needles))
        found = re.compile(rf"({longest_first})(?!\w)")
        pieces: list[str] = []
        position = 0
        count = 0
        for match in found.finditer(text):
            if match.start() < position:
                continue  # inside a path that is already replaced
            end = _end_of_path(text, match.start(), match.end())
            pieces.append(text[position : match.start()])
            pieces.append(written[match.group(1)] + ("/..." if end > match.end() else ""))
            position = end
            count += 1
        pieces.append(text[position:])
        return "".join(pieces), count

    def _without_runs(self, text: str) -> tuple[str, int]:
        written = {
            each: f"<run {number}>"
            for each, number in self._runs.items()
            if len(each) >= SHORTEST_PATH
        }
        return _replaced(text, written, bounded=False)

    def _without_patterns(self, text: str) -> tuple[str, int]:
        written = {
            each: f"<pattern {number}>"
            for each, number in self._patterns.items()
            if sum(character.isalnum() for character in each) >= SHORTEST_PATTERN
        }
        return _replaced(text, written, bounded=True)


def _end_of_path(text: str, begins: int, ends: int) -> int:
    """Where the path that begins at ``begins`` and is known up to ``ends`` goes on to.

    A path in quotes (an operating system's message writes one so) goes on to the closing quote,
    spaces included; any other goes on to the next space.
    """
    if ends >= len(text) or text[ends] not in "/\\":
        return ends
    quote = text[begins - 1] if begins and text[begins - 1] in "'\"" else None
    if quote is not None:
        stops = [at for at in (text.find(quote, ends), text.find("\n", ends)) if at >= 0]
        return min(stops) if stops else len(text)
    beneath = _BENEATH.match(text, ends)
    return beneath.end() if beneath else ends


def _replaced(text: str, written: dict[str, str], *, bounded: bool) -> tuple[str, int]:
    """``text`` with each key replaced by its value, longest first, and how many were replaced.

    ``bounded`` keeps a key from matching inside a longer word (a pattern ``old`` in ``older``).
    """
    if not written:
        return text, 0
    alternatives = "|".join(re.escape(each) for each in sorted(written, key=len, reverse=True))
    found = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)" if bounded else f"(?:{alternatives})")
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return written[match.group(0)]

    return found.sub(replace, text), count


def _spellings(text: str) -> set[str]:
    """A name as written and in both Unicode normal forms, with a trailing slash taken off.

    A file system keeps one normal form and a program may print the other.
    """
    stripped = text.rstrip("/\\") or text
    return {
        spelling
        for each in (text, stripped)
        for spelling in (
            each,
            unicodedata.normalize("NFC", each),
            unicodedata.normalize("NFD", each),
        )
        if spelling
    }


def plain_console(buffer: io.StringIO) -> Console:
    """A console that writes to ``buffer``: plain text, no colors, no emoji, no line split."""
    return Console(
        file=buffer,
        width=WIDTH,
        color_system=None,
        force_terminal=False,
        highlight=False,
        emoji=False,
        legacy_windows=False,
    )


def backstop_notice(count: int) -> str:
    """The last line of a report in which the backstop had to replace something."""
    return (
        f"  note: {count} name(s) of the data were still in this report after it was written "
        f"and were replaced; please tell the developer"
    )
