"""Inference streamed by folder: what each rule reaches, the chunks, and when to stream.

A streamed pass assembles every entry's *names* once for the whole pool: the entry, its
components and its bindings, which is what the cross-folder rules read. It assembles every entry's
*observations*, the largest table a dive writes, a chunk of whole folders at a time, only for the
rules that read them and only while they read them. Everything else the pass does is the whole
pass's, in the whole pass's order, so a streamed run and a whole run publish the same rows: the
same claims, candidates, findings and failures, to the digit.

Every predicate's reach is declared here, and a rule whose predicate is not declared refuses to
stream. A rule that loses its reach silently is worse than a memory bound, so the table is a test.
A predicate registered without a line below makes a streamed run fall back to the whole pass and
say why, and a test fails until somebody declares what it reads. The reaches are:

- folder-local: one entry's own records (``entry``), or one folder's direct children with the
  folder itself and what lies beside it (``folder``). A chunk holds them.
- upward: what a folder rule raised on an ancestor folder (``installed_software_in_scope`` as a
  defeater), one verdict per folder, read before the first rule and handed down.
- cross-folder: an entry beside something the pass computes over the whole pool first
  (``attached``: neighbours, folder contents, archive links, a folder's dates, approved groups);
  a root (``root``) or the whole pool (``pool``); a pair or a candidate set within one class
  (``pair``); a class of a grouping (``group``, which no phase assembles). These read the pool's
  names, which a streamed pass holds whole, and never another entry's observations.
- the scan's own (``scan``): the census and the cascade run them, and inference never does.

Streaming does not bound the names projection, which is linear in the pool, because every
cross-folder rule reads names across folders. It also does not bound the claims that the pass
keeps as views for the later phases that read them. What it bounds is the observations, a chunk at
a time.

So streaming is off by default (``infer.streaming: never``). Measured, it lowered the peak on no
census, and the peak went up on a large generated census with every row identical. The peak on a
large pool is set by the rules reading a root or the whole pool. Projections of their own for the
seven such rules (five reading the pool, two a root) would make streaming worth turning on by
default.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from ingence.core.schema import EntryType
from ingence.rulebook.registry import PREDICATES, Reach, Reads, reach_declared

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from ingence.core.schema import Observation, Rule
    from ingence.core.settings import InferSettings
    from ingence.infer.subjects import EntryRecords

__all__ = [
    "REACH",
    "ChunkedObservations",
    "ChunkedSubjects",
    "Reach",
    "StreamingDecision",
    "decide_streaming",
    "folder_chunks",
    "physical_memory",
    "reads_observations",
    "unclassified",
]


#: Every predicate registered before streaming existed, by what it reads (from each predicate's
#: code). A predicate added since declares its reach where it is registered
#: (``declare(..., reach=...)``), or takes one line here. :func:`reach_of` reads this table first.
REACH: Mapping[str, Reach] = MappingProxyType(
    {
        "approved_experiment_group": Reach.ATTACHED,
        "approved_miscellaneous": Reach.ATTACHED,
        "archive_link_names_a_file": Reach.ATTACHED,
        "archive_named_but_unlisted": Reach.ENTRY,
        "authored_name_doubt": Reach.ENTRY,
        "candidates_are_bounded_per_subject": Reach.PAIR,
        "class_from_bound_sense": Reach.ENTRY,
        "class_from_enclosing_folder": Reach.ENTRY,
        "class_from_format_origin": Reach.ENTRY,
        "components_do_not_conflict": Reach.PAIR,
        "concerns_by_found_sense": Reach.PAIR,
        "contains_what_enumeration_listed": Reach.SCAN,
        "content_holds_a_phrase": Reach.ENTRY,
        "content_unreadable": Reach.ATTACHED,
        "copied_in_bulk": Reach.FOLDER,
        "copied_in_bulk_by_declared_clocks": Reach.ATTACHED,
        "copied_in_bulk_from_contents": Reach.ATTACHED,
        "declared_attribute_to_clarify": Reach.ENTRY,
        "declared_copy_window_exceeded": Reach.ATTACHED,
        "declared_digest_contradicted": Reach.ENTRY,
        "derivation_is_acyclic": Reach.PAIR,
        "derives_from_aggregation": Reach.GROUP,
        "derives_from_declared_reference": Reach.PAIR,
        "derives_from_name_family": Reach.PAIR,
        # A script's calls propose within a pair. The named-for grouping reads a value the pass
        # computes over the whole pool first.
        "derives_from_script_calls": Reach.PAIR,
        "derives_from_stage_transition": Reach.PAIR,
        "domain_pack_would_read": Reach.ROOT,
        "earliest_credible_time": Reach.ENTRY,
        "embeds_what_expansion_recovered": Reach.SCAN,
        "empty_among_data": Reach.ATTACHED,
        "endpoint_holds_an_eligible_role": Reach.PAIR,
        "equivalent_by_multi_signal_design": Reach.PAIR,
        "experiment_from_contents": Reach.ATTACHED,
        "experiment_from_dated_name": Reach.ATTACHED,
        "experiments_may_belong_together": Reach.POOL,
        "extension_contradicts_content": Reach.ENTRY,
        "failure_concentrated_in_scope": Reach.POOL,
        "format_from_archive_members": Reach.ENTRY,
        "format_from_extension": Reach.ENTRY,
        "group_below_expected_size": Reach.GROUP,
        "group_incomplete_against_workflow": Reach.GROUP,
        "group_resolution_deviation": Reach.FOLDER,
        "index_of_bound_label": Reach.ENTRY,
        "indexed_label_may_mark_replicates": Reach.ROOT,
        "installed_software_in_scope": Reach.FOLDER,
        "miscellaneous_files_to_approve": Reach.POOL,
        "name_date_neighbourhood": Reach.ATTACHED,
        "named_for_an_acquisition": Reach.ATTACHED,
        # A series and its table, and one name in sibling folders, read a value the pass computes
        # over the whole pool first, as the named-for grouping does.
        "named_for_a_series": Reach.ATTACHED,
        "naming_is_authored_or_emitted": Reach.ENTRY,
        "note_in_a_file_name": Reach.ENTRY,
        "object_kind_from_mode": Reach.SCAN,
        "one_name_in_sibling_folders": Reach.ATTACHED,
        "placeholder_from_entry_kind": Reach.ENTRY,
        "readings_conflict": Reach.ENTRY,
        "references_by_declared_path": Reach.PAIR,
        "relation_follows_the_clock": Reach.PAIR,
        "resolved_group_across_experiments": Reach.ATTACHED,
        "resolved_group_within_experiment": Reach.ATTACHED,
        # One file at two steps, a pair of a class.
        "later_step_of_same_file": Reach.PAIR,
        "same_component_values": Reach.ENTRY,
        # A recording's clock and the files that copied it, read on one entry and on a pair of a
        # class.
        "same_acquisition_clock": Reach.ENTRY,
        "derives_from_source_clock": Reach.PAIR,
        "same_composition": Reach.ENTRY,
        "same_content_digest": Reach.ENTRY,
        "same_declared_digest": Reach.ENTRY,
        "same_extracted_shape": Reach.ENTRY,
        "same_observed_metadata": Reach.ENTRY,
        "sense_from_name_spelling": Reach.ENTRY,
        "sense_from_name_tokens": Reach.ENTRY,
        # A name's standing parts, and the workflows in force, read on one entry.
        "stage_from_name_parts": Reach.ENTRY,
        "stage_in_workflow": Reach.ENTRY,
        "later_step_of_a_sibling": Reach.ENTRY,
        "software_outside_the_catalogue": Reach.POOL,
        "stated_count_may_mark_replicates": Reach.ENTRY,
        "structure_from_component_kind": Reach.ENTRY,
        "substitution_rests_on_content": Reach.PAIR,
        "succeeds_by_ordered_token": Reach.PAIR,
        "succession_stays_within_one_series": Reach.PAIR,
        "totals_from_enumeration": Reach.SCAN,
        "unbound_word_where_a_kind_stands": Reach.FOLDER,
        "unverified_substitution_candidate": Reach.GROUP,
        "vocabulary_unread_in_scope": Reach.FOLDER,
        # A format or writer read from one file's own contents or name, an area folder's kind from
        # its name and a folder's domain profile from its own observation are an entry's. The
        # spellings read as slips are a root's.
        "area_kind_from_folder_name": Reach.ENTRY,
        "derived_from_inner_format": Reach.ENTRY,
        "folder_domain_profile": Reach.ENTRY,
        "format_from_content": Reach.ENTRY,
        "spelling_read_as": Reach.ROOT,
        "writer_from_content_tags": Reach.ENTRY,
    }
)

#: What a predicate reads that holds an entry's observations: its own, or its records whole.
_OBSERVING = frozenset({Reads.OBSERVATIONS, Reads.RECORDS, Reads.ENTRY_OBSERVATIONS})


def reach_of(predicate: str) -> Reach | None:
    """Return what a predicate reaches: this table's line, else its registration's, else None."""
    listed = REACH.get(predicate)
    if listed is not None:
        return listed
    if predicate not in PREDICATES.names():
        return None
    return reach_declared(PREDICATES.declaration(predicate))


def unclassified(predicates: Iterable[str]) -> tuple[str, ...]:
    """Return the predicates whose reach nobody declared, in canonical order."""
    return tuple(sorted({name for name in predicates if reach_of(name) is None}))


def reads_observations(
    rule: Rule, shapes: Mapping[str, Reads], in_force: Mapping[str, Rule]
) -> bool:
    """Whether evaluating a rule on an entry reads the entry's observations, or a defeater does.

    A defeater is evaluated on the same entry's records, so a rule reading names whose defeater
    reads the content is handed the content too.
    """
    if shapes.get(rule.rule_id) in _OBSERVING:
        return True
    return any(shapes.get(name) in _OBSERVING for name in rule.defeated_by if name in in_force)


@dataclass(frozen=True, slots=True)
class StreamingDecision:
    """Whether one inference streams, and why, as its run record states it."""

    streamed: bool
    reason: str


#: Where a Linux container's memory limit is written (cgroup version 2 and version 1).
CGROUP_ROOT = Path("/sys/fs/cgroup")

#: A cgroup version 1 limit this large or larger is the kernel's way of writing "no limit".
_NO_CGROUP_LIMIT = 1 << 60


def physical_memory(cgroup_root: Path = CGROUP_ROOT) -> int | None:
    """The machine's memory in bytes, read at run time, or None where it cannot be read.

    The machine is measured and not assumed: ``sysconf`` on macOS and Linux, the system's own
    report on Windows, and in a Linux container the least of that and the container's limit. A
    container reports its host's memory. The limit comes from cgroup version 2's ``memory.max``
    (``max`` is no limit) or version 1's ``memory.limit_in_bytes`` (a huge value is no limit).
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        pages = size = -1
    machine: int | None = int(pages) * int(size) if pages > 0 and size > 0 else None
    if machine is None and sys.platform == "win32":  # pragma: no cover - the Windows lane
        machine = _windows_physical_memory()
    limit = _container_limit(cgroup_root)
    if limit is None:
        return machine
    return limit if machine is None else min(machine, limit)


def _container_limit(cgroup_root: Path) -> int | None:
    """The memory limit a Linux container's cgroup states, or None where it states none."""
    for written in (cgroup_root / "memory.max", cgroup_root / "memory" / "memory.limit_in_bytes"):
        try:
            text = written.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if text.isdigit() and 0 < int(text) < _NO_CGROUP_LIMIT:
            return int(text)
    return None


def _windows_physical_memory() -> int | None:  # pragma: no cover - Windows only
    class _Status(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = _Status()
    status.length = ctypes.sizeof(_Status)
    windll = getattr(ctypes, "windll", None)
    if windll is None or not windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.total_physical)


_GIB = 1024**3


def decide_streaming(
    settings: InferSettings,
    *,
    entries: int,
    predicates: Iterable[str],
    physical: int | None,
) -> StreamingDecision:
    """Decide whether one inference streams, from the setting, the pool and the machine.

    ``auto`` streams where the projected peak exceeds the machine's physical memory less the
    margin. The projected peak is the entries times the per-entry cost the setting carries,
    measured. A predicate whose reach is undeclared refuses streaming wherever the pass would have
    streamed, and the reason names it. ``never`` never streams.
    """
    if settings.streaming == "never":
        return StreamingDecision(False, "infer.streaming is never")
    missing = unclassified(predicates)
    refusal = (
        f"no reach is declared for {', '.join(missing)}, so the pass runs whole" if missing else ""
    )
    if settings.streaming == "always":
        if refusal:
            return StreamingDecision(False, f"infer.streaming is always, but {refusal}")
        return StreamingDecision(True, "infer.streaming is always")
    projected = entries * settings.projected_bytes_per_entry
    if physical is None:
        return StreamingDecision(
            False, "auto: the machine's physical memory could not be read, so the pass runs whole"
        )
    available = physical * (1.0 - settings.memory_margin_fraction)
    figures = (
        f"{entries} entries at {settings.projected_bytes_per_entry} bytes each project "
        f"{projected / _GIB:.2f} GiB against {physical / _GIB:.2f} GiB of physical memory less "
        f"a margin of {settings.memory_margin_fraction:.0%} ({available / _GIB:.2f} GiB)"
    )
    if projected <= available:
        return StreamingDecision(False, f"auto: {figures}")
    if refusal:
        return StreamingDecision(False, f"auto: {figures}; {refusal}")
    return StreamingDecision(True, f"auto: {figures}")


def folder_chunks(
    records: Sequence[EntryRecords],
    entries_per_chunk: int,
    positions: Iterable[int] | None = None,
) -> tuple[tuple[int, ...], ...]:
    """Return the pool's entries, by position, as chunks of whole folders.

    A chunk holds complete folders: every entry directly in one folder lies in one chunk, so a rule
    reading a folder's children finds all of them together. The folders are taken in the order of
    their paths, so a chunk holds neighbouring folders and a subtree tends to stay whole. Folders
    are gathered until the next would carry the chunk past ``entries_per_chunk``. A folder holding
    more than that is a chunk of its own. Entries in no folder (a root, a member promoted from a
    container) are one group of their own, first. The chunking is deterministic, so two runs chunk
    alike.

    ``positions`` chunks only those entries (a streamed pass chunks the entries with observations).
    """
    by_parent: dict[str | None, list[int]] = {}
    for position in range(len(records)) if positions is None else sorted(positions):
        by_parent.setdefault(records[position].entry.parent_entry_id, []).append(position)
    path_of = {
        held.entry.entry_id: held.entry.relative_path
        for held in records
        if held.entry.entry_type is EntryType.DIRECTORY
    }
    order = sorted(
        by_parent,
        key=lambda parent: (parent is not None, path_of.get(parent or "", ""), parent or ""),
    )
    chunks: list[tuple[int, ...]] = []
    current: list[int] = []
    for parent in order:
        members = by_parent[parent]
        if current and len(current) + len(members) > entries_per_chunk:
            chunks.append(tuple(current))
            current = []
        current.extend(members)
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


class ChunkedObservations:
    """The chunks of a streamed pass and each chunk's observations, loaded as a rule reaches them.

    One object serves the whole pass and is shared by every rule reading observations. The chunks
    hold only entries that have observations, so a census with none loads nothing. The last chunk
    loaded is kept until another is asked for. Each full reading of the pool takes the chunks in
    the opposite order to the one before, so a rule starts where the rule before it finished, and a
    pool of one chunk is loaded once. What is never held is more than one chunk's observations.

    The object is keyed by entry as well as by position. ``ids`` are the pool's entry identifiers in
    the order of its subjects. Every chunk's position must name one, and no position may lie in two
    chunks. The positions no chunk holds are the entries with nothing to load.
    """

    def __init__(
        self,
        chunks: Sequence[Sequence[int]],
        load: Callable[[Sequence[str]], Mapping[str, Sequence[Observation]]],
        *,
        ids: Sequence[str],
    ) -> None:
        placed: set[int] = set()
        for chunk in chunks:
            for position in chunk:
                if not 0 <= position < len(ids):
                    message = f"a chunk holds position {position}, which names no entry"
                    raise ValueError(message)
                if position in placed:
                    message = f"position {position} lies in more than one chunk"
                    raise ValueError(message)
                placed.add(position)
        self.chunks = chunks
        self.ids = ids
        #: The positions of the entries no chunk holds, which have no observations to load.
        self.unobserved = tuple(position for position in range(len(ids)) if position not in placed)
        self._load = load
        self._held: tuple[int, Mapping[str, Sequence[Observation]]] | None = None
        self._forward = True
        #: How many chunks were loaded, for the run's journal and for a test.
        self.loads = 0

    def order(self) -> list[int]:
        """The chunks' order for the next full reading of the pool."""
        order = list(range(len(self.chunks)))
        if not self._forward:
            order.reverse()
        self._forward = not self._forward
        return order

    def observations(
        self, index: int, entry_ids: Sequence[str]
    ) -> Mapping[str, Sequence[Observation]]:
        """Return one chunk's observations by entry, loading them unless they are the ones held."""
        if self._held is not None and self._held[0] == index:
            return self._held[1]
        self._held = None
        found = self._load(entry_ids)
        self._held = (index, found)
        self.loads += 1
        return found

    def release(self) -> None:
        """Let the chunk held go."""
        self._held = None


class ChunkedSubjects:
    """The pool's subjects, handed out with their observations a chunk at a time.

    What a streamed pass hands a rule reading observations in place of the pool: the entries with
    no observations as they are, then each chunk's entries with their observations attached. It
    yields the whole pool, each subject once, so a rule sees every subject it would have seen.
    Evaluating an entry reads only that entry's records, so the order changes nothing a rule
    concludes. What the object never holds is every entry's observations at once.
    """

    def __init__(self, subjects: Sequence[EntryRecords], observed: ChunkedObservations) -> None:
        self._subjects = subjects
        self._observed = observed

    def __len__(self) -> int:
        return len(self._subjects)

    def _at(self, position: int) -> EntryRecords:
        """The subject at a position, refused unless it is the entry the chunks placed there."""
        expected = self._observed.ids
        if len(self._subjects) != len(expected) or (
            self._subjects[position].entry.entry_id != expected[position]
        ):
            message = (
                "the subjects no longer line up with the chunks they are read through: "
                f"position {position} is not {expected[position]!r}"
            )
            raise ValueError(message)
        return self._subjects[position]

    def __iter__(self) -> Iterator[EntryRecords]:
        from dataclasses import replace

        for position in self._observed.unobserved:
            yield self._at(position)
        for index in self._observed.order():
            held = [self._at(position) for position in self._observed.chunks[index]]
            found = self._observed.observations(index, [records.entry.entry_id for records in held])
            for records in held:
                observed = found.get(records.entry.entry_id)
                yield replace(records, observations=tuple(observed)) if observed else records
            del held, found
