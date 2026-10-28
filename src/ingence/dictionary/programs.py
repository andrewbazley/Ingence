"""Programs and packages as the Dictionary knows them: the parts each lays out side by side.

The module recognises a program by its structure, never by its name. A copy of Fiji is a folder
holding ``jars``, ``plugins`` and ``luts`` side by side on every machine, because Fiji's packaging
made it so. A Python package is a ``setup.py`` or a ``pyproject.toml`` beside the code it packages,
whatever the package is called. Both are facts about packaging and neither is a fact about a
laboratory, so they are vocabulary: data that a person can read, switch off and add to, in the
files this module reads, and never a list in Python.

There are two kinds of layout, declared in two places.

- A package layout is a language's own packaging convention: how Python, R, MATLAB or Java lays out
  a package, and how a virtual or conda environment is marked. It ships with the core
  (``builtin/program_layouts.yaml``), because a laboratory's own analysis code is laid out this way
  as often as anybody's, and recognising it needs no knowledge of any program.
- A program layout names one scientific suite (Fiji, MATLAB, R and the rest) by the parts its
  installation always holds. It ships as a pack of its own (``packs/programs/``), so a person who
  wants none of the brand names switches the pack off like any domain and keeps the structural
  kinds. A laboratory's own program is declared the same way, in a pack of its own.

What a layout says: ``holds`` lists the parts a folder must hold directly, each met by a different
child. A part is a name, compared without case, or several names of which any one will do, and
``*`` stands for any run of characters. A name ending in ``/`` is a folder, and any other name is a
file, so ``jars/`` is never met by a file called ``jars``. ``named`` is what the folder's own name
must look like, for the conventions that live in a folder's name (MATLAB's ``@`` class folders).
``code`` names the files that are this kind's own code, read where a folder of them is shared as an
archive of its own name. A program's layout names at least two parts, because one part is a name,
and a name is what this module exists not to match.

Nothing here opens a file. The module matches a layout against the names and kinds that the census
already holds.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.schema import EntryType

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ingence.core.schema import Entry
    from ingence.dictionary.store import Section

__all__ = [
    "LAYOUTS_KEY",
    "FolderView",
    "ProgramLayout",
    "layouts_from",
    "program_layouts",
]

#: The key a section file lists its layouts under, beside (and apart from) its ``entries``.
LAYOUTS_KEY = "layouts"

#: The two kinds a layout may declare, by the key that names it: a named program, or a kind of
#: package. A program found in a folder is said before a package is, because it is the more
#: specific reading (a copy of R is also laid out as an R library).
_PROGRAM_KEY = "program"
_PACKAGE_KEY = "package"

#: What marks a part as a folder rather than a file.
_FOLDER_MARK = "/"

#: The fewest parts a program's layout may name, so that no program is ever recognised by one
#: name alone. A package's layout may name one, because a packaging file is itself the convention
#: (``pyvenv.cfg`` is written by nothing but a virtual environment).
MINIMUM_PROGRAM_PARTS = 2


@dataclass(frozen=True, slots=True)
class _Part:
    """One part a layout names: the names that will do, each a folder's or a file's."""

    #: Each name that meets the part, as it was declared, and whether it names a folder.
    names: tuple[tuple[str, bool], ...]

    @property
    def exact(self) -> bool:
        """Whether every name is spelled out, so a lookup by name answers it."""
        return not any(_is_pattern(name) for name, _ in self.names)

    def met_by(self, view: FolderView) -> tuple[Entry, ...]:
        """Every child of the folder that meets this part, in the folder's canonical order."""
        found: dict[str, Entry] = {}
        for name, folder in self.names:
            if not _is_pattern(name):
                found.update((child.entry_id, child) for child in view.named(name, folder=folder))
                continue
            pool = view.folders if folder else view.files
            wanted = name.casefold()
            found.update(
                (child.entry_id, child) for child in pool if _matches(child.name.casefold(), wanted)
            )
        return tuple(sorted(found.values(), key=_canonical))

    def spelled(self) -> str:
        """How the part is written in a sentence: ``jars/``, or ``setup.py or pyproject.toml``."""
        return " or ".join(f"{name}{_FOLDER_MARK if folder else ''}" for name, folder in self.names)


@dataclass(frozen=True, slots=True)
class ProgramLayout:
    """One program or kind of package, and the parts it lays out side by side."""

    #: What a folder laid out this way is raised as: ``Fiji``, ``Python package``.
    name: str
    #: Whether the layout names a program rather than a kind of package.
    names_a_program: bool
    #: The parts the folder must hold directly, each met by a different child.
    parts: tuple[_Part, ...]
    #: What the folder's own name must look like, where the convention lives there.
    named: str | None = None
    #: This kind's own code files, read where a folder of them is shared as an archive of its
    #: own name.
    code: tuple[str, ...] = ()
    #: The section that declared it, which a finding's reason may name.
    section: str = ""
    #: How a page names a folder laid out this way, where its name with an article would read
    #: wrongly (``published code``, never "a published code"). A page's wording changes no
    #: reading, so the vocabulary's fingerprint leaves it out.
    said_as: str | None = None

    def met_in(self, view: FolderView) -> tuple[Entry, ...] | None:
        """Return the children meeting every part, one each, or None where the folder falls short.

        A different child meets each part (a ``setup.py`` is not both the packaging file and the
        module beside it). The search uses augmenting paths over at most as many candidates per part
        as there are parts. A part with that many candidates can always be met, whatever the others
        took, so the cap loses no match. The folder's canonical order makes the chosen children the
        same every run. The cheap refusals come first: the folder's own name, then the parts
        spelled out in full, looked up rather than scanned.
        """
        if self.named is not None and not _matches(view.own_name, self.named.casefold()):
            return None
        width = len(self.parts)
        candidates: list[tuple[Entry, ...]] = []
        for part in sorted(self.parts, key=lambda part: not part.exact):
            met = part.met_by(view)
            if not met:
                return None
            candidates.append(met[:width])
        assigned: dict[str, int] = {}

        def place(index: int, seen: set[str]) -> bool:
            for child in candidates[index]:
                if child.entry_id in seen:
                    continue
                seen.add(child.entry_id)
                holder = assigned.get(child.entry_id)
                if holder is None or place(holder, seen):
                    assigned[child.entry_id] = index
                    return True
            return False

        for index in range(len(candidates)):
            if not place(index, set()):
                return None
        chosen = {entry.entry_id: entry for met in candidates for entry in met}
        return tuple(sorted((chosen[entry_id] for entry_id in assigned), key=_canonical))

    def code_in(self, view: FolderView) -> tuple[Entry, ...]:
        """The folder's files that are this kind's own code, in canonical order."""
        if not self.code:
            return ()
        wanted = tuple(pattern.casefold() for pattern in self.code)
        return tuple(
            child
            for child in view.files
            if any(_matches(child.name.casefold(), pattern) for pattern in wanted)
        )

    def spelled(self) -> str:
        """The layout in a sentence's words, for a finding's reason."""
        held = ", ".join(part.spelled() for part in self.parts)
        return f"a folder named {self.named} holding {held}" if self.named else held

    def as_payload(self) -> dict[str, object]:
        """The layout in canonical form, which the vocabulary's fingerprint covers."""
        return {
            "name": self.name,
            "names_a_program": self.names_a_program,
            "parts": [part.spelled() for part in self.parts],
            "named": self.named,
            "code": list(self.code),
            "section": self.section,
        }


@dataclass(frozen=True, slots=True)
class FolderView:
    """One folder's own name and direct children, indexed once for every layout matched against it.

    Built once per folder and read by every layout, so a folder of five thousand files is sorted
    and indexed once and not once per program the vocabulary knows.
    """

    #: The folder's own name, casefolded.
    own_name: str
    #: Its direct children that are folders, and those that are not, each in canonical order.
    folders: tuple[Entry, ...]
    files: tuple[Entry, ...]
    #: Every child by its casefolded name and whether it is a folder.
    by_name: Mapping[tuple[str, bool], tuple[Entry, ...]]

    @classmethod
    def of(cls, own_name: str, children: Iterable[Entry]) -> FolderView:
        """Index a folder's children, canonical by name and then identifier."""
        ordered = sorted(children, key=_canonical)
        by_name: dict[tuple[str, bool], list[Entry]] = {}
        folders: list[Entry] = []
        files: list[Entry] = []
        for child in ordered:
            is_folder = child.entry_type is EntryType.DIRECTORY
            (folders if is_folder else files).append(child)
            by_name.setdefault((child.name.casefold(), is_folder), []).append(child)
        return cls(
            own_name=own_name.casefold(),
            folders=tuple(folders),
            files=tuple(files),
            by_name={key: tuple(value) for key, value in by_name.items()},
        )

    def named(self, name: str, *, folder: bool) -> tuple[Entry, ...]:
        """The children spelled exactly so, without case, of the kind asked for."""
        return self.by_name.get((name.casefold(), folder), ())


def _canonical(entry: Entry) -> tuple[str, str]:
    return entry.name, entry.entry_id


def _is_pattern(name: str) -> bool:
    return "*" in name or "?" in name


def _matches(name: str, pattern: str) -> bool:
    """Whether a casefolded name meets a casefolded name or pattern (``*`` any run of characters).

    Brackets are read as themselves: a folder called ``[old]`` is a name, and a layout never
    declares a character class.
    """
    if not _is_pattern(pattern):
        return name == pattern
    return fnmatch.fnmatchcase(name, pattern.replace("[", "[[]"))


def _part_from(raw: object, *, where: str) -> _Part:
    """Read one declared part: a name, or a list of names any one of which will do."""
    spelled = [raw] if isinstance(raw, str) else raw
    if (
        not isinstance(spelled, list)
        or not spelled
        or not all(isinstance(name, str) and name.strip(_FOLDER_MARK).strip() for name in spelled)
    ):
        message = f"{where}: a part is a name or a list of names, and {raw!r} is neither"
        raise ValueError(message)
    return _Part(
        names=tuple((name.rstrip(_FOLDER_MARK), name.endswith(_FOLDER_MARK)) for name in spelled)
    )


def layouts_from(
    raw: object, *, section: str, minimum_program_parts: int = MINIMUM_PROGRAM_PARTS
) -> tuple[ProgramLayout, ...]:
    """Read a section file's ``layouts`` list, refusing anything that is not one.

    The function refuses a bad layout rather than skipping it, as a rule does: a layout with a typo
    would recognise nothing, or everything, and nobody would see it happen. It refuses a program's
    layout that names fewer than :data:`MINIMUM_PROGRAM_PARTS` parts, because one part is a name. A
    rule's own parameter may declare fewer, as a person's choice about their own program.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        message = f"section {section!r}: {LAYOUTS_KEY!r} must be a list"
        raise ValueError(message)
    found: list[ProgramLayout] = []
    for index, item in enumerate(raw):
        where = f"section {section!r}, layout {index}"
        if not isinstance(item, dict):
            message = f"{where}: a layout is a mapping"
            raise ValueError(message)
        kinds = [key for key in (_PROGRAM_KEY, _PACKAGE_KEY) if key in item]
        if len(kinds) != 1 or not str(item[kinds[0]]).strip():
            message = f"{where}: a layout names one {_PROGRAM_KEY} or one {_PACKAGE_KEY}"
            raise ValueError(message)
        holds = item.get("holds")
        if not isinstance(holds, list) or not holds:
            message = f"{where}: a layout says what the folder holds"
            raise ValueError(message)
        parts = tuple(_part_from(part, where=where) for part in holds)
        names_a_program = kinds[0] == _PROGRAM_KEY
        named = item.get("named")
        if names_a_program and len(parts) < minimum_program_parts:
            message = (
                f"{where}: a program's layout names at least {minimum_program_parts} parts, so "
                "that no program is recognised by a name alone"
            )
            raise ValueError(message)
        code = item.get("code") or []
        if not isinstance(code, list) or not all(isinstance(pattern, str) for pattern in code):
            message = f"{where}: 'code' is a list of names"
            raise ValueError(message)
        said_as = item.get("said_as")
        if said_as is not None and not (isinstance(said_as, str) and said_as.strip()):
            message = f"{where}: 'said_as' is a phrase"
            raise ValueError(message)
        found.append(
            ProgramLayout(
                name=str(item[kinds[0]]).strip(),
                names_a_program=names_a_program,
                parts=parts,
                named=str(named) if named else None,
                code=tuple(code),
                section=section,
                said_as=said_as.strip() if said_as else None,
            )
        )
    return tuple(found)


def program_layouts(sections: Iterable[Section]) -> tuple[ProgramLayout, ...]:
    """Return every layout the vocabulary's sections declare: the programs first, then packages.

    The order is canonical: by name within each kind, then by the section that declared it. So a
    folder that meets two layouts names them the same way every run. A caller hands in a store's
    ``sections``, so a project that switched the programs pack off reads none of its layouts.
    """
    layouts = [layout for section in sections for layout in section.layouts]
    return tuple(
        sorted(
            layouts,
            key=lambda layout: (
                not layout.names_a_program,
                layout.name,
                layout.section,
                [part.spelled() for part in layout.parts],
            ),
        )
    )
