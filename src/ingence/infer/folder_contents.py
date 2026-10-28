"""What each folder holds, counted from names and formats alone.

An experiment folder is recognised by the structure of what it holds before its name is read. That
structure can be counted from names and formats, without knowing what any word means.

So the pass counts, for every folder, facts that a rule can read without knowing what any word
means:

- An acquisition is a file (or an archive's link standing for one) whose extension the Dictionary
  declares as written by an instrument as it acquires. This is the origin that the
  acquisition-stage rule already reads.
- A file named for an acquisition is any other file whose name holds an acquisition's name whole,
  its parts in order (``Drift_<movie>_results.csv``, ``Traj_<movie>.nd2.csv``,
  ``MAX_<movie>.tif``). This is the way analysis tools name what they write. The pass reads the
  name as parts cut at separators, never as words.
- Where that material is bound together: how much of what lies beneath a folder the own files of
  the folders above it also hold or are named for, how much its parent holds directly, and whether
  the folder's subfolders holding material are linked to one another by the acquisition names they
  share. One experiment split into ``raw``, ``tiff`` and ``analysis`` folders looks like that. A
  month folder of separate experiments does not.

These are facts, not conclusions. Every figure here is a count over names and declared formats.
Which folder is an experiment is the rule's to say (``experiment_from_contents``). A predicate
reading this shape is handed counts about its folder and its parent, never another folder's
records, on the siblings shape's precedent.

An acquisition's name is used for matching only where it can mean one thing: at least
``minimum_key_characters`` letters and digits, one of them a letter. So ``01.nd2`` or ``2045.nd2``
is an acquisition and never a name another file is matched on.

Cost. The work is linear in the pool: one pass over files to recognise acquisitions and index their
names by first part, one to match every other file against that index at each of its parts, and one
over folders deepest first to sum what lies beneath.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ingence.core.schema import EntryType
from ingence.infer.subjects import FolderContents
from ingence.listing.format import AddressState

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from ingence.infer.subjects import EntryRecords

__all__ = [
    "ACQUISITION_WRITER",
    "MINIMUM_ALIKE_ITEMS",
    "MINIMUM_ITEMS",
    "MINIMUM_KEY_CHARACTERS",
    "MOST_FOLDERS_A_DATE",
    "acquisition_spellings",
    "alike_shape",
    "broken_addresses",
    "declared_copy_times",
    "name_dates",
    "name_parts",
    "name_words",
    "series_of_days",
    "series_remainder",
    "with_folder_contents",
]

#: Who writes a spelling that begins a workflow, as the Dictionary declares it.
ACQUISITION_WRITER = "instrument"

#: The fewest letters and digits an acquisition's name needs to be matched on. The rule's parameter
#: of the same name is passed through, and this is its declared default.
MINIMUM_KEY_CHARACTERS = 6

#: The fewest subfolders, each one item's outputs from one template, that make one run's outputs.
#: The rule's parameter of the same name is passed through. The value is unmeasured. Long runs of
#: such subfolders occur in practice, and five is the fewest that is plainly a batch and not a
#: handful of analyses.
MINIMUM_ITEMS = 5

#: The fewest sibling subfolders holding the same files that make one run's per-item outputs. A tool
#: writing one folder per item (per movie, per cell, per well) writes the same few files into each,
#: such as a tracks table and a settings file, named alike or after the item. It names the folders
#: after the items, so their names share their words. This is weaker evidence than one template
#: naming acquisitions, because repeated days of one condition can hold the same files by habit. So
#: it has its own number. The number was measured twice on generated trees
#: (``tests/test_per_item_output_folders.py::test_the_default_lies_above_every_chance_agreement``).
#: The trees held 400 seeded parent folders of 2 to 80 dated experiment folders, made one by one
#: (one or two words each, repeated over several days, holding only common file names or files named
#: with their words). They were compared with 100 seeded per-item runs of 10 to 400 folders, one in
#: twenty short a file. First, with a condition repeated over at most six days, the most siblings
#: sharing words and files by chance was 10 and the fewest of a run's items 19, so the number is 11.
#: Then, with a condition repeated over as many as forty days, series of days read as days
#: (:func:`series_of_days`) and a run's movies made on one to five days, no two siblings agreed by
#: chance, and the fewest of a run's items was 27. The second measurement gives no lower bound, so
#: the default is 11, from the first.
MINIMUM_ALIKE_ITEMS = 11

#: The most sibling folders on one date that still read as a series of days. A daily series holds
#: one folder a day, or two where a day was done again. A run's items share a few days of
#: acquisition, tens to a day. The value is unmeasured.
MOST_FOLDERS_A_DATE = 2

#: The shape's stand-ins: the folder's own name where a file's name holds it whole, and each run
#: of digits, so ``<item>_tracks.csv`` and ``frame_0001.tif`` read alike in every item's folder.
ITEM_NAME_MARK = "*"
NUMBER_MARK = "#"

# The two signatures an item's folder is read by: its kind, its name's words, what it holds.
_TEMPLATE = "template"
_ALIKE = "alike"
_Signature = tuple[str, tuple[str, ...], frozenset[tuple[str, ...]]]
#: The fewest letters a family's word holds: one letter is a well's row or a side, not a word.
_LEAST_WORD = 2
_LETTERS = re.compile(r"[^\W\d_]+")

_SEPARATORS = re.compile(r"[^0-9a-z]+")
_LETTER = re.compile(r"[a-z]")
_DIGITS = re.compile(r"[0-9]+")


def acquisition_spellings(origins: Mapping[str, Mapping[str, str]], writer: str) -> frozenset[str]:
    """Every spelling, casefolded, that the Dictionary declares as written by ``writer``."""
    return frozenset(
        spelling
        for spelled in origins.values()
        for spelling, who in spelled.items()
        if who == writer
    )


def name_parts(name: str, extension: str | None) -> tuple[str, ...]:
    """A name's parts, casefolded, cut at every run of non-alphanumerics, its extension off."""
    folded = name.casefold()
    suffix = f".{extension.casefold()}" if extension else ""
    if suffix and folded.endswith(suffix) and len(folded) > len(suffix):
        folded = folded[: -len(suffix)]
    return tuple(part for part in _SEPARATORS.split(folded) if part)


def _key(parts: tuple[str, ...], minimum: int) -> tuple[str, ...] | None:
    joined = "".join(parts)
    return parts if len(joined) >= minimum and _LETTER.search(joined) else None


def _template(parts: tuple[str, ...], key: tuple[str, ...], extension: str) -> tuple[str, ...]:
    """A name's parts with the one acquisition name it holds stood in for, and its extension."""
    for position in range(len(parts) - len(key) + 1):
        if parts[position : position + len(key)] == key:
            return (*parts[:position], "*", *parts[position + len(key) :], f".{extension}")
    return (*parts, f".{extension}")


def alike_shape(
    folder_name: str, files: Sequence[tuple[str, str | None]]
) -> frozenset[tuple[str, ...]]:
    """The files a folder holds, as one item's folder among a run's: what siblings share.

    ``files`` are the folder's files, each a name and its extension. Each name is cut into parts
    (:func:`name_parts`), with the folder's own name stood in where the file's name holds it whole
    (``ITEM_NAME_MARK``), every run of digits read as one number (``NUMBER_MARK``), and its
    extension kept. A hidden name (beginning with a dot, the operating system's own files such as
    a folder's view settings) is left out, as one folder's and not the run's.
    """
    own = name_parts(folder_name, None)
    shape: set[tuple[str, ...]] = set()
    for name, extension in files:
        if name.startswith("."):
            continue
        parts = name_parts(name, extension)
        if own:
            for position in range(len(parts) - len(own) + 1):
                if parts[position : position + len(own)] == own:
                    parts = (*parts[:position], ITEM_NAME_MARK, *parts[position + len(own) :])
                    break
        read = tuple(_DIGITS.sub(NUMBER_MARK, part) for part in parts)
        shape.add((*read, f".{(extension or '').casefold()}"))
    return frozenset(shape)


def name_words(
    components: Iterable[tuple[str, str, str]], left_out: Collection[str] = ()
) -> tuple[str, ...]:
    """A folder name's words, as item folders of one run share them.

    ``components`` are the name's components as the tokeniser read them, each its type, its raw
    text and its normalised value. ``left_out`` are the normalised words bound to a sense that
    groups are never made of (a date's, a replicate's). The words are its words and the labels of
    its indexed parts (``kelt`` of ``kelt003``), two letters or more, casefolded, each once and
    sorted. Dates, clocks, numbers, amounts and single letters (a well's row) are set aside, so
    ``230318_A_kelt003`` and ``230405_D_kelt117`` both say ``kelt`` and ``17Jun2024_quolt``
    says ``quolt``, its month's word being part of its date.
    """
    found: set[str] = set()
    for kind, _raw, value in components:
        if kind == "word":
            word = value.casefold()
        elif kind == "indexed":
            word = value.casefold().rstrip("0123456789")
        else:
            continue
        if len(word) >= _LEAST_WORD and word not in left_out and _LETTERS.fullmatch(word):
            found.add(word)
    return tuple(sorted(found))


def series_remainder(components: Iterable[tuple[str, str, str]]) -> tuple[str, ...]:
    """What a name says beside what counts, in the order it says it.

    The components are those :func:`name_words` keeps, with the same things set aside (dates,
    clocks, numbers, amounts, single letters; a caller leaves out words bound to a date's or a
    replicate's sense). The result holds each word, casefolded, and each indexed part whole
    (``kelt003`` as ``kelt3``). So two items of one run counted ``kelt003`` and ``kelt017`` say
    different things, and two days of one series written ``20240902_A_brisa`` and
    ``20240903_B_brisa`` say the same.
    """
    kept: list[str] = []
    for kind, _raw, value in components:
        folded = value.casefold()
        if (
            kind == "word" and len(folded) >= _LEAST_WORD and _LETTERS.fullmatch(folded)
        ) or kind == "indexed":
            kept.append(folded)
    return tuple(kept)


def name_dates(components: Iterable[tuple[str, str, str]]) -> tuple[str, ...]:
    """The dates a name holds, as the tokeniser normalised them."""
    return tuple(value for kind, _raw, value in components if kind == "date" and value)


def series_of_days(
    members: Sequence[tuple[tuple[str, ...], tuple[str, ...]]],
    *,
    most_a_date: int = MOST_FOLDERS_A_DATE,
) -> bool:
    """Whether siblings, each its :func:`series_remainder` and :func:`name_dates`, are a series of
    days and not one run's items.

    Every sibling is dated, on more than one day (a run's items made on one day are never days).
    Either they all say the same beside what counts, or nearly every one is on a date of its own:
    no date is held by more than ``most_a_date`` of them (a series counting its days, ``_d07``,
    beside the date). A run's items share a few days of acquisition, many to a day, and are not
    caught.
    """
    if not members or any(not dates for _, dates in members):
        return False
    held = Counter(dates for _, dates in members)
    if len(held) < 2:
        return False
    if len({remainder for remainder, _ in members}) == 1:
        return True
    return max(held.values()) <= most_a_date


def _stood_in(parts: tuple[str, ...], name: str) -> tuple[str, ...]:
    """Parts with the item folder's own name stood in, where they hold it whole."""
    own = name_parts(name, None)
    if own:
        for position in range(len(parts) - len(own) + 1):
            if parts[position : position + len(own)] == own:
                return (*parts[:position], ITEM_NAME_MARK, *parts[position + len(own) :])
    return parts


def _never_group_words(held: EntryRecords) -> set[str]:
    """The components of a name bound to a sense groups are never made of."""
    from ingence.core.schema import DictionaryBinding
    from ingence.core.tiers import ResolutionStatus
    from ingence.grouping.identifiers import NOT_GROUP_WORD_KINDS

    return {
        binding.component_id
        for binding in held.bindings
        if isinstance(binding, DictionaryBinding)
        and binding.resolution_status is ResolutionStatus.RESOLVED
        and binding.theme_kind in NOT_GROUP_WORD_KINDS
    }


def _named_for(
    parts: tuple[str, ...], index: Mapping[str, list[tuple[str, ...]]]
) -> set[tuple[str, ...]]:
    """The acquisition names held whole, parts in order, somewhere in this name's parts."""
    found: set[tuple[str, ...]] = set()
    for position, part in enumerate(parts):
        for key in index.get(part, ()):
            if parts[position : position + len(key)] == key:
                found.add(key)
    return found


def _largest_linked(children: Sequence[str], keys: Mapping[str, set[tuple[str, ...]]]) -> int:
    """The most subfolders, among those given, linked to one another by an acquisition's name."""
    parent = {child: child for child in children}

    def root(child: str) -> str:
        while parent[child] != child:
            parent[child] = parent[parent[child]]
            child = parent[child]
        return child

    first: dict[tuple[str, ...], str] = {}
    for child in children:
        for key in keys.get(child, ()):
            if key in first:
                a, b = root(first[key]), root(child)
                if a != b:
                    parent[b] = a
            else:
                first[key] = child
    sizes: dict[str, int] = defaultdict(int)
    for child in children:
        sizes[root(child)] += 1
    return max(sizes.values(), default=0)


def declared_copy_times(catalog: Any) -> dict[str, int]:
    """The earliest copy's arrival a listing declared for each file, by entry.

    The function reads the ``declared.clock.<meaning>`` observations whose meaning is a copy's (an
    archive's write, an upload). It never reads a modification a listing declared, because that is
    the file's own time, and no rule reads a declared one.
    """
    from ingence.listing.format import ClockKind, ClockMeaning, parse_instant

    found: dict[str, int] = {}
    rows = catalog.query(
        "SELECT entry_id, key, value_structure FROM observations "
        "WHERE starts_with(key, 'declared.clock.') ORDER BY observation_id"
    ).fetchall()
    for entry, key, structure in rows:
        try:
            meaning = ClockMeaning(str(key).removeprefix("declared.clock."))
        except ValueError:
            continue
        if meaning.kind is not ClockKind.COPY or not structure:
            continue
        for said in json.loads(structure).get("said", ()):
            at = parse_instant(str(said.get("at", "")))
            if at is not None:
                found[str(entry)] = min(at, found.get(str(entry), at))
    return found


#: The order a folder's broken addresses are said in: the states' own, not-found first.
_STATE_ORDER = {state.value: position for position, state in enumerate(AddressState)}


def broken_addresses(catalog: Any) -> dict[str, str]:
    """What each file's address last answered, by entry, where it answered no file.

    The function reads the ``declared.address.broken`` observations, a listing's statement or a
    remote reading's answer alike. Where two say different things, it keeps the latest reading's
    (by when it was found, then by state). A file whose bytes a remote reading read is left out:
    its address answered then, and what is raised is the files left unread.
    """
    from ingence.listing.format import parse_instant
    from ingence.listing.ingest import BROKEN_ADDRESS_KEY

    rows = catalog.query(
        "SELECT entry_id, value_structure FROM observations WHERE key = ? ORDER BY observation_id",
        [BROKEN_ADDRESS_KEY],
    ).fetchall()
    if not rows:
        # Almost every pool: nothing was found broken, and the remote readings are not read.
        return {}
    read = {
        str(entry)
        for (entry,) in catalog.query(
            "SELECT DISTINCT entry_id FROM observations "
            "WHERE starts_with(observer, 'remote.') AND starts_with(key, 'identity.')"
        ).fetchall()
    }
    latest: dict[str, tuple[int, str]] = {}
    for entry, structure in rows:
        if str(entry) in read or not structure:
            continue
        stated = json.loads(structure)
        try:
            state = AddressState(str(stated.get("state", "")))
        except ValueError:
            continue
        found = (parse_instant(str(stated.get("at", ""))) or 0, state.value)
        latest[str(entry)] = max(found, latest.get(str(entry), found))
    return {entry: state for entry, (_, state) in sorted(latest.items())}


def with_folder_contents(
    records: Sequence[EntryRecords],
    origins: Mapping[str, Mapping[str, str]],
    *,
    minimum_key_characters: int = MINIMUM_KEY_CHARACTERS,
    minimum_items: int = MINIMUM_ITEMS,
    minimum_alike_items: int = MINIMUM_ALIKE_ITEMS,
    most_folders_a_date: int = MOST_FOLDERS_A_DATE,
    writer: str = ACQUISITION_WRITER,
    declared_copies: Mapping[str, int] | None = None,
    broken: Mapping[str, str] | None = None,
) -> tuple[EntryRecords, ...]:
    """Attach to every folder what it holds (:class:`FolderContents`), and to nothing else.

    ``declared_copies`` holds, by file, the earliest time a listing declared that a copy of it
    arrived. It is counted per folder beside the births, and read as a copy's time only.
    ``broken`` holds, by file, what its address last answered where it answered no file
    (:func:`broken_addresses`). It is counted per folder, by state.
    ``minimum_items`` and ``minimum_alike_items`` are how many sibling item folders of one
    template, or holding the same files, make one run's per-item outputs.
    """
    spellings = acquisition_spellings(origins, writer)
    folders: dict[str, EntryRecords] = {}
    parent_of: dict[str, str | None] = {}
    children: dict[str | None, list[str]] = defaultdict(list)
    files: list[tuple[str | None, EntryRecords]] = []
    for held in records:
        entry = held.entry
        if entry.entry_type is EntryType.DIRECTORY:
            folders[entry.entry_id] = held
            parent_of[entry.entry_id] = entry.parent_entry_id
            children[entry.parent_entry_id].append(entry.entry_id)
        elif entry.entry_type in (EntryType.FILE, EntryType.SYMLINK):
            files.append((entry.parent_entry_id, held))

    # The copy's mark, per folder: files with a birth and a modification, and the births of those
    # born after their modification (a copy keeps the modification and is born when it lands).
    dated_here: dict[str | None, int] = defaultdict(int)
    late_births: dict[str | None, list[int]] = defaultdict(list)
    files_here: dict[str | None, int] = defaultdict(int)
    copies_here: dict[str | None, list[int]] = defaultdict(list)
    broken_here: dict[str | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Every file's name and extension, per folder: what an item's folder holds.
    listed_here: dict[str | None, list[tuple[str, str | None]]] = defaultdict(list)
    for folder, held in files:
        entry = held.entry
        answered = (broken or {}).get(entry.entry_id)
        if answered is not None:
            broken_here[folder][answered] += 1
        listed_here[folder].append((entry.name, entry.extension))
        if entry.entry_type is EntryType.FILE:
            files_here[folder] += 1
            arrived = (declared_copies or {}).get(entry.entry_id)
            if arrived is not None:
                copies_here[folder].append(int(arrived))
        if (
            entry.entry_type is EntryType.FILE
            and entry.created_ns is not None
            and entry.modified_ns is not None
            and str(entry.created_time_source) == "birthtime"
        ):
            dated_here[folder] += 1
            if entry.created_ns > entry.modified_ns:
                late_births[folder].append(int(entry.created_ns))

    acquisitions_here: dict[str | None, int] = defaultdict(int)
    acquired_here: dict[str | None, set[tuple[str, ...]]] = defaultdict(set)
    index: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    others: list[tuple[str | None, tuple[str, ...], str]] = []
    for folder, held in files:
        entry = held.entry
        extension = (entry.extension or "").casefold()
        parts = name_parts(entry.name, entry.extension)
        if extension and extension in spellings:
            acquisitions_here[folder] += 1
            key = _key(parts, minimum_key_characters)
            if key is not None and key not in acquired_here[folder]:
                acquired_here[folder].add(key)
        else:
            others.append((folder, parts, extension))
    for keys in acquired_here.values():
        for key in keys:
            if key not in index[key[0]]:
                index[key[0]].append(key)

    named_here: dict[str | None, int] = defaultdict(int)
    named_keys_here: dict[str | None, set[tuple[str, ...]]] = defaultdict(set)
    named_files: list[tuple[str | None, set[tuple[str, ...]]]] = []
    # Each named file's name with the acquisition's parts stood in for, per folder: the template
    # one tool wrote it from, where it names exactly one acquisition.
    templates_here: dict[str | None, set[tuple[str, ...]]] = defaultdict(set)
    unnamed_here: dict[str | None, int] = defaultdict(int)
    for folder, parts, extension in others:
        found = _named_for(parts, index)
        if found:
            named_here[folder] += 1
            named_keys_here[folder].update(found)
            named_files.append((folder, found))
            if len(found) == 1:
                templates_here[folder].add(_template(parts, next(iter(found)), extension))
        else:
            unnamed_here[folder] += 1

    # Deepest first, so a folder's subfolders are summed before it.
    def depth(folder: str) -> int:
        return folders[folder].entry.depth or 0

    order = sorted(folders, key=depth, reverse=True)
    acquisitions_beneath: dict[str, int] = {}
    named_beneath: dict[str, int] = {}
    acquired_beneath: dict[str, set[tuple[str, ...]]] = {}
    held_beneath: dict[str, set[tuple[str, ...]]] = {}
    for folder in order:
        acquisitions_beneath[folder] = acquisitions_here.get(folder, 0) + sum(
            acquisitions_beneath[child] for child in children.get(folder, ())
        )
        named_beneath[folder] = named_here.get(folder, 0) + sum(
            named_beneath[child] for child in children.get(folder, ())
        )
        acquired = set(acquired_here.get(folder, ()))
        kept = acquired | named_keys_here.get(folder, set())
        for child in children.get(folder, ()):
            acquired |= acquired_beneath[child]
            kept |= held_beneath[child]
        acquired_beneath[folder] = acquired
        held_beneath[folder] = kept

    # A file named for an acquisition counts as its own folder's where the acquisition lies beneath
    # it.
    named_own: dict[str, int] = defaultdict(int)
    for folder, found in named_files:
        cursor = folder
        while cursor is not None and cursor in folders:
            if found & acquired_beneath[cursor]:
                named_own[cursor] += 1
            cursor = parent_of.get(cursor)

    def has_material(child: str) -> bool:
        return bool(acquisitions_beneath[child] or named_beneath[child])

    # One run's outputs, one subfolder per item. There are two kinds of evidence.
    # One template: a subfolder holding no acquisition, nothing unnamed and no subfolder with
    # material, whose files all name one acquisition, is one item's. Its signature is the set of
    # templates its files were written from.
    # The same files: a subfolder holding files and no acquisition and no subfolder at all. Its
    # signature is what it holds read alike (:func:`alike_shape`) beside its name's words
    # (:func:`name_words`). So a tool's ``tracks.csv`` and ``params.txt`` in each of many folders
    # named for its items are one shape, and folders named by different words are never items of
    # one run (groups are made of words).
    # The first is the stronger evidence, so each counts against its own minimum. Every set of
    # siblings reaching its minimum is a run's items.
    # An item's subfolders are part of its shape. A tool writing a folder of plots into each item's
    # folder writes the same one into each. So a folder holding files and folders of files, with
    # nothing beneath it an acquisition, is read as all it holds, each subfolder's files under the
    # subfolder's name read alike.
    def shape_of(folder: str) -> frozenset[tuple[str, ...]]:
        name = folders[folder].entry.name
        found = set(alike_shape(name, listed_here.get(folder, ())))
        for child in children.get(folder, ()):
            if children.get(child):
                return frozenset()  # a deeper tree is a run's folder, not an item's
            if folders[child].entry.name.startswith("."):
                continue  # a hidden folder, as a hidden file, is one folder's
            under = "/" + "_".join(
                _DIGITS.sub(NUMBER_MARK, part)
                for part in _stood_in(name_parts(folders[child].entry.name, None), name)
            )
            found.update(
                (under, *element) for element in alike_shape(name, listed_here.get(child, ()))
            )
        return frozenset(found)

    read: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {}

    def family(folder: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """A folder's name words, what its name says beside what counts, and its dates, read
        once. A word bound to a date's or a replicate's sense is set aside."""
        if folder not in read:
            held = folders[folder]
            never = _never_group_words(held)
            components = [
                (str(c.component_type), str(c.raw_value or ""), str(c.normalized_value or ""))
                for c in held.components
                if c.component_id not in never
            ]
            read[folder] = (
                name_words(components),
                series_remainder(components),
                name_dates(components),
            )
        return read[folder]

    signature: dict[str, _Signature] = {}
    for folder in folders:
        if (
            acquisitions_here.get(folder, 0) == 0
            and unnamed_here.get(folder, 0) == 0
            and named_here.get(folder, 0) > 0
            and not any(has_material(child) for child in children.get(folder, ()))
            and len(named_keys_here.get(folder, ())) == 1
            and templates_here.get(folder)
        ):
            signature[folder] = (_TEMPLATE, (), frozenset(templates_here[folder]))
        elif (
            acquisitions_beneath[folder] == 0
            and (listed_here.get(folder) or children.get(folder))
            and (shape := shape_of(folder))
        ):
            signature[folder] = (_ALIKE, family(folder)[0], shape)
    needed = {_TEMPLATE: minimum_items, _ALIKE: minimum_alike_items}
    members: dict[tuple[str | None, _Signature], list[str]] = defaultdict(list)
    for folder, shaped in signature.items():
        members[(parent_of.get(folder), shaped)].append(folder)

    def days(group: Sequence[str]) -> bool:
        """Siblings read as a series of days, never a run's items (:func:`series_of_days`)."""
        return series_of_days(
            [(family(folder)[1], family(folder)[2]) for folder in group],
            most_a_date=most_folders_a_date,
        )

    # Never a data pool's own root: roots side by side are no run's items.
    one_item: set[str] = set()
    for (parent_folder, shaped), group in members.items():
        if parent_folder in folders and len(group) >= needed[shaped[0]] and not days(group):
            one_item.update(group)
    # Its family: an item whose siblings among the run's items share its name's words, as many as
    # the kind asks (one template's items need not share them). Only such an item's dated name is
    # not an experiment's.
    in_family: dict[tuple[str | None, _Signature, tuple[str, ...]], int] = defaultdict(int)
    for folder in one_item:
        in_family[(parent_of.get(folder), signature[folder], family(folder)[0])] += 1
    one_of_a_family = {
        folder
        for folder in one_item
        if in_family[(parent_of.get(folder), signature[folder], family(folder)[0])]
        >= needed[signature[folder][0]]
    }
    item_children: dict[str | None, int] = defaultdict(int)
    for folder in sorted(one_item):
        item_children[parent_of.get(folder)] += 1
    # The item folders beneath each folder, deepest first: a folder holding a run's per-item
    # outputs holds material, whether or not any of it names an acquisition.
    items_beneath: dict[str, int] = {}
    for folder in order:
        items_beneath[folder] = sum(
            items_beneath[child] + (child in one_item) for child in children.get(folder, ())
        )

    def holding(child: str) -> bool:
        return has_material(child) or items_beneath[child] > 0

    def whole(child: str) -> bool:
        return bool(acquisitions_beneath[child] and named_beneath[child])

    grouped: dict[str | None, tuple[int, int, int]] = {}
    for folder in [*folders, None]:
        holders = [child for child in children.get(folder, ()) if holding(child)]
        grouped[folder] = (
            len(holders),
            _largest_linked(holders, held_beneath) if len(holders) > 1 else len(holders),
            sum(1 for child in holders if whole(child)),
        )

    def dated(held: EntryRecords) -> bool:
        kinds = {component.component_type for component in held.components}
        return "date" in kinds and "word" in kinds

    # What the own files of every folder above hold or are named for, handed down shallowest first.
    # A folder's own files count only where they are its material: at least as many as its
    # subfolders holding material, so a stray movie at the top of fifty experiments is not any
    # folder's material.
    direct_above: dict[str, frozenset[tuple[str, ...]]] = {}
    for folder in reversed(order):
        parent = parent_of.get(folder)
        if parent is None or parent not in folders:
            direct_above[folder] = frozenset()
            continue
        material = acquisitions_here.get(parent, 0) + named_here.get(parent, 0)
        keeps = material > 0 and material >= grouped[parent][0]
        own = (
            (acquired_here.get(parent, set()) | named_keys_here.get(parent, set()))
            if keeps
            else set()
        )
        inherited = direct_above.get(parent, frozenset())
        direct_above[folder] = inherited | own if own else inherited

    dated_folder = {folder: dated(held) for folder, held in folders.items()}
    contents: dict[str, FolderContents] = {}
    # Shallowest first, so each folder is handed its parent's contents.
    for folder in reversed(order):
        held = folders[folder]
        parent = parent_of.get(folder)
        above_direct = direct_above.get(folder, frozenset())
        ancestors = 0
        cursor = parent
        while cursor is not None and cursor in folders:
            ancestors += dated_folder[cursor]
            cursor = parent_of.get(cursor)
        counted = grouped[folder]
        above = grouped.get(parent, (0, 0, 0))
        contents[folder] = FolderContents(
            entry=held.entry,
            acquisitions_here=acquisitions_here.get(folder, 0),
            named_here=named_here.get(folder, 0),
            acquisitions_beneath=acquisitions_beneath[folder],
            named_beneath=named_beneath[folder],
            named_beneath_own=named_own.get(folder, 0),
            shared_above=len(held_beneath[folder] & above_direct) if above_direct else 0,
            holding_children=counted[0],
            linked_children=counted[1],
            whole_children=counted[2],
            parent_material_here=(
                acquisitions_here.get(parent, 0) + named_here.get(parent, 0)
                if parent in folders
                else 0
            ),
            parent_holding_children=above[0],
            parent_linked_children=above[1],
            parent_whole_children=above[2],
            item_children=item_children.get(folder, 0),
            one_item_of_many=folder in one_item,
            one_item_of_a_family=folder in one_of_a_family,
            item_holding_children=sum(
                1 for child in children.get(folder, ()) if child in item_children
            ),
            items_beneath=items_beneath[folder],
            name_dated=dated_folder[folder],
            parent=contents.get(parent) if parent is not None else None,
            dated_ancestors=ancestors,
            dated_files_here=dated_here.get(folder, 0),
            born_late_here=len(late_births.get(folder, ())),
            late_births_first_ns=min(late_births[folder]) if late_births.get(folder) else None,
            late_births_last_ns=max(late_births[folder]) if late_births.get(folder) else None,
            files_here=files_here.get(folder, 0),
            declared_copies_here=len(copies_here.get(folder, ())),
            declared_copies_first_ns=min(copies_here[folder]) if copies_here.get(folder) else None,
            declared_copies_last_ns=max(copies_here[folder]) if copies_here.get(folder) else None,
            broken_addresses_here=sum(broken_here[folder].values()) if folder in broken_here else 0,
            broken_address_states=(
                tuple(sorted(broken_here[folder].items(), key=lambda pair: _STATE_ORDER[pair[0]]))
                if folder in broken_here
                else ()
            ),
        )
    return tuple(
        replace(held, contents=contents[held.entry.entry_id])
        if held.entry.entry_id in contents
        else held
        for held in records
    )
