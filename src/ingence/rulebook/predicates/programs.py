"""Software outside the catalog, recognized by its structure and asked about.

The catalog covers installed programs. Other software can be recognized by its folder structure
and raised as a question, and the answer "this is not a program" must be possible.

A folder is asked about ("Is this folder a program?") where its direct children show at least
``minimum_evidence`` of three kinds of a program's structure:

- a layout: a subfolder named as packaging names a program's parts (``bin``, ``lib``,
  ``plugins``: ``layout_folder_names``);
- compiled code: a file with a compiled object's, a library's or a header's extension
  (``program_extensions``), never a script a person wrote;
- a program's papers: a license or a version file (``marker_names``, compared on the name
  without its extension).

The folder is not asked about if a layout that the Dictionary declares matches it or any folder
above it. Such a layout is a named program's parts, a kind of package's packaging, or the
vocabulary's own catalog, handed by the pass. What the catalog knows is not asked about.

A folder that a person answered is not asked again. The answer is the per-folder program answer
(``program:<entry_id>``). The answer "not a program" takes the folder out of program scope, and
"a program" puts it in (``installed_software_in_scope`` v4).

The predicate raises a question and never a claim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.schema import Entry, EntryType, RuleEffect
from ingence.dictionary.programs import FolderView
from ingence.rulebook.registry import Abstention, Raised, Reads, VocabularyRead, declare

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.dictionary.programs import ProgramLayout
    from ingence.infer.subjects import EntryRecords
    from ingence.rulebook.registry import Predicate

__all__ = ["PROGRAM_QUESTION", "SoftwareOutsideTheCatalogue", "program_question"]

#: What the question asks, of one folder: its own question, answered on its own target
#: (``program:<entry_id>``), so each folder's answer reaches the Record.
PROGRAM_QUESTION = "is this folder a program?"


def program_question(path: str) -> str:
    """The question one folder raises, naming it."""
    return f"{path}: {PROGRAM_QUESTION}"


def _listed(parameters: Mapping[str, object], name: str) -> frozenset[str]:
    value = parameters.get(name)
    if isinstance(value, (list, tuple)):
        return frozenset(str(item).casefold().removeprefix(".") for item in value)
    return frozenset()


class SoftwareOutsideTheCatalogue:
    """A folder laid out like a program's that nothing in the catalog names: asked about."""

    name = "software_outside_the_catalogue"
    version = "1"

    def __init__(self) -> None:
        self._source: object = None
        self._children: dict[str | None, list[Entry]] = {}
        self._parents: dict[str, str | None] = {}
        self._names: dict[str, str] = {}
        self._catalogued: dict[str, bool] = {}

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the question on a folder with enough of a program's structure, else None."""
        holder: EntryRecords | None = getattr(subject, "holder", None)
        if holder is None:
            return None
        if getattr(holder, "not_a_program", False) or getattr(holder, "a_program", False):
            return None  # answered
        entries = [
            records.entry
            for records in getattr(subject, "entries", ())
            if isinstance(records.entry, Entry)
        ]
        evidence = self._evidence(entries, parameters)
        minimum = int(str(parameters.get("minimum_evidence", 2)))
        if len(evidence) < minimum:
            return None
        layouts: Sequence[ProgramLayout] = tuple(getattr(subject, "layouts", ()) or ())
        self._index(subject)
        if self._under_catalogue(holder.entry.entry_id, layouts, parameters):
            return None
        cited = sorted({entry_id for _, found in evidence for entry_id in found})[:5]
        return Raised(
            values=(program_question(holder.entry.relative_path),),
            inputs=tuple(cited),
            reason=(
                "laid out like a program's, and no program or package the Dictionary knows "
                "matches it: " + "; ".join(kind for kind, _ in evidence)
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that the folder shows too little of a program's structure, or is catalogued."""
        return Abstention(
            reason="too little of a program's structure, a catalogued program, or answered"
        )

    def _evidence(
        self, entries: Sequence[Entry], parameters: Mapping[str, object]
    ) -> list[tuple[str, tuple[str, ...]]]:
        folders = _listed(parameters, "layout_folder_names")
        extensions = _listed(parameters, "program_extensions")
        markers = _listed(parameters, "marker_names")
        layout = tuple(
            entry.entry_id
            for entry in entries
            if entry.entry_type is EntryType.DIRECTORY and entry.name.casefold() in folders
        )
        code = tuple(
            entry.entry_id
            for entry in entries
            if entry.entry_type is EntryType.FILE
            and (entry.extension or "").casefold() in extensions
        )
        papers = tuple(
            entry.entry_id
            for entry in entries
            if entry.entry_type is EntryType.FILE
            and entry.name.casefold().rsplit(".", 1)[0] in markers
        )
        found = []
        if layout:
            found.append(("a program's layout folders", layout))
        if code:
            found.append(("compiled code", code))
        if papers:
            found.append(("a license or a version file", papers))
        return found

    def _index(self, subject: object) -> None:
        source = getattr(subject, "pool", None) or ()
        if source is self._source:
            return
        children: dict[str | None, list[Entry]] = {}
        parents: dict[str, str | None] = {}
        names: dict[str, str] = {}
        for records in source:
            entry = getattr(records, "entry", None)
            if not isinstance(entry, Entry):
                continue
            children.setdefault(entry.parent_entry_id, []).append(entry)
            if entry.entry_type is EntryType.DIRECTORY:
                parents[entry.entry_id] = entry.parent_entry_id
                names[entry.entry_id] = entry.name
        self._source, self._children, self._parents, self._names = source, children, parents, names
        self._catalogued = {}

    def _under_catalogue(
        self,
        folder: str,
        layouts: Sequence[ProgramLayout],
        parameters: Mapping[str, object],
    ) -> bool:
        """Whether the folder, or one above it, is a program or package the catalog declares."""
        suffixes = _listed(parameters, "bundle_name_suffixes")
        cursor: str | None = folder
        while cursor is not None:
            if cursor not in self._catalogued:
                own = self._names.get(cursor, "").casefold()
                view = FolderView.of(own, self._children.get(cursor, ()))
                self._catalogued[cursor] = any(
                    layout.met_in(view) is not None for layout in layouts
                ) or any(own.endswith(suffix) and own != suffix for suffix in suffixes)
            if self._catalogued[cursor]:
                return True
            cursor = self._parents.get(cursor)
        return False


@declare(
    "software_outside_the_catalogue",
    version="1",
    summary="Ask whether a folder laid out like a program's that nothing catalogued names is one",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=(
        "bundle_name_suffixes",
        "layout_folder_names",
        "marker_names",
        "minimum_evidence",
        "program_extensions",
    ),
    support_families=("filesystem_fact",),
    vocabulary=(VocabularyRead.PROGRAM_LAYOUTS,),
)
def _build_software_outside_the_catalogue() -> Predicate:
    return SoftwareOutsideTheCatalogue()
