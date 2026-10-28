"""Reading one published run into the files a design grid is drawn from.

Three inputs, all conclusions the run already holds, nothing opened and nothing written:

- the condition classes: the resolved claims of the replicate grouping
  (``groups.replicates_of_one_condition``), whose key a file shares with the files that repeat its
  condition in its folder;
- each file's ordered name parts: the run's recorded name layouts (``name_shapes``), each part
  with its component (a kind, a value as normalised and as written, a quantity's magnitude and
  dimension) and the kind of the sense the Dictionary bound it to;
- the experiments, by the one definition completeness and themes share
  (:mod:`ingence.experiment_units`): the highest folder a rule classed as an experiment's above a
  file, the files beneath installed programs counted apart.

Also a file's stage (the stage rules' resolved claims, ``search/scale.py``'s list) and whether the
run found its output empty (``content_unreadable``'s findings, ``search/tags.py``'s reading).
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING

import pyarrow as pa

from ingence.design.grid import DesignFile, NamePart
from ingence.experiment_units import run_units
from ingence.grouping.identifiers import date_spans
from ingence.search.scale import DEFAULT_SCALE
from ingence.search.tags import DEFAULT_UNREADABLE_RULE

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from ingence.storage.catalog import Catalog

__all__ = ["GROUPING_PREDICATE", "ExperimentFiles", "experiment_files_of", "name_parts_of"]

#: The grouping whose claims are the condition classes.
GROUPING_PREDICATE = "groups.replicates_of_one_condition"

_FILES_TABLE = "design_files"
_CONTENT_TIERS = frozenset({"instrument", "document"})


class ExperimentFiles:
    """One experiment's folder and the files read from the run, and the files inside programs."""

    __slots__ = ("experiment_id", "files", "folder", "programs", "root_id")

    def __init__(
        self,
        experiment_id: str,
        root_id: str,
        folder: str,
        files: list[DesignFile],
        programs: int,
    ) -> None:
        self.experiment_id = experiment_id
        self.root_id = root_id
        self.folder = folder
        self.files = files
        self.programs = programs


@functools.cache
def _is_a_word_of_kind(letters: str, kind: str) -> bool:
    """Whether the shipped Dictionary reads these letters as a sense of that theme kind."""
    from ingence.dictionary.store import DictionaryStore

    hit = DictionaryStore.builtin().look_up(letters, permitted_kinds=frozenset({kind}))
    return hit is not None


#: What may stand between a date word and its day and still be one date: nothing, or the
#: punctuation a date is written with (``12Oct``, ``Oct-12``, ``Oct.12``). An underscore or a space
#: parts two things (``Monday_001`` is a day of the week and a counter).
_DATE_JOINS = frozenset({"", "-", "."})


def _is_date_part(part: NamePart) -> bool:
    """A month's or weekday's word, as the Dictionary reads it."""
    return part.kind in ("word", "date") and part.sense == "date"


_LABEL_LETTERS = re.compile(r"^[^\W\d_]+")


def name_parts_of(
    slots: Sequence[dict[str, object]],
    components: dict[str, tuple[object, ...]],
    name: str,
) -> tuple[NamePart, ...]:
    """A name's parts in order. A date or a clock is never a condition, however it is written:

    - a clock ``14-32-05`` is tokenised as three numbers, and a part that lies inside a span the
      name writes a date or a clock in has that kind;
    - a label whose letters the Dictionary reads as a month or a weekday (``Oct12``) is a date;
    - a number glued to such a word (``12Oct``, ``Oct-12``, ``Monday12``: nothing, or a hyphen
      or a dot, between them) is a date's day, and never one across an underscore or a space;
    - a label the Dictionary reads as a time point (``tp1``) stays a condition and says so.
    """
    stamps = date_spans(name)
    parts: list[NamePart] = []
    joins: list[str] = []
    for slot in sorted(slots, key=lambda item: int(str(item["ordinal"]))):
        component = components.get(str(slot["component_id"]))
        if component is None:
            return ()
        kind, value, raw, magnitude, dimension = component
        sense = slot.get("theme_kind")
        start, end = int(str(slot.get("start", -1))), int(str(slot.get("end", -1)))
        inside = next(
            (what for first, last, what in stamps if start >= first and 0 <= end <= last), None
        )
        shown = str(raw if raw is not None else value)
        read = str(kind)
        if inside is not None and read in ("number", "word"):
            read = inside
        elif (
            read == "indexed"
            and (found := _LABEL_LETTERS.match(shown)) is not None
            and _is_a_word_of_kind(found.group(), "date")
        ):
            read = "date"
        elif (
            read == "indexed"
            and sense is None
            and (found := _LABEL_LETTERS.match(shown)) is not None
            and _is_a_word_of_kind(found.group(), "timepoint")
        ):
            sense = "timepoint"
        joins.append(str(slot.get("separator") or ""))
        parts.append(
            NamePart(
                ordinal=int(str(slot["ordinal"])),
                kind=read,
                value=str(value),
                shown=shown,
                sense=None if sense is None else str(sense),
                magnitude=None if magnitude is None else float(str(magnitude)),
                dimension=None if dimension is None else str(dimension),
            )
        )
    for place, part in enumerate(parts):
        if part.kind != "number":
            continue
        before = place > 0 and joins[place] in _DATE_JOINS and _is_date_part(parts[place - 1])
        after = (
            place + 1 < len(parts)
            and joins[place + 1] in _DATE_JOINS
            and _is_date_part(parts[place + 1])
        )
        if before or after:
            parts[place] = replace(part, kind="date")
    return tuple(parts)


def experiment_files_of(
    catalog: Catalog,
    *,
    only: Collection[str] | None = None,
    stage_rules: Sequence[str] = DEFAULT_SCALE.stage_rules,
) -> list[ExperimentFiles]:
    """Every experiment of the catalog's run with its files, in path order.

    ``only`` limits the experiments to those identifiers (the experiment folder's entry). The
    rest are not read. A file is in the experiment its folder belongs to. A file in a folder
    inside an installed program is counted apart, under the experiment that holds the program.
    """
    run = run_units(catalog)
    wanted = None if only is None else frozenset(only)
    experiments = sorted(
        {unit for unit in run.units.values() if wanted is None or unit in wanted},
        key=lambda unit: (run.roots[unit], run.paths[unit], unit),
    )
    if not experiments:
        return []

    # A program's folder belongs to the experiment around it, for the count of what is left out.
    program_of: dict[str, str] = {}
    for folder in sorted(run.programs):
        current = run.parents.get(folder)
        seen = 0
        while current is not None and current not in run.units and seen < 10_000:
            current = run.parents.get(current)
            seen += 1
        if current is not None and current in run.units:
            program_of[folder] = run.units[current]

    held = frozenset(experiments)
    files = catalog.query(
        "SELECT entry_id, parent_entry_id, name, event_time_ns, event_time_tier FROM entries "
        "WHERE NOT is_deleted AND entry_kind = 'filesystem' AND entry_type <> 'directory' "
        "ORDER BY entry_id"
    ).fetchall()
    in_experiment: dict[str, str] = {}
    names: dict[str, str] = {}
    clocks: dict[str, int] = {}
    programs: dict[str, int] = dict.fromkeys(experiments, 0)
    for entry_id, parent, name, clock, tier in files:
        if parent is None:
            continue
        beside = str(parent)
        unit = run.units.get(beside)
        if unit is not None and unit in held:
            in_experiment[str(entry_id)] = unit
            names[str(entry_id)] = str(name)
            if clock is not None and str(tier) in _CONTENT_TIERS:
                clocks[str(entry_id)] = int(clock)
            continue
        inside = program_of.get(beside)
        if inside is not None and inside in held:
            programs[inside] += 1
    if not in_experiment:
        return [
            ExperimentFiles(unit, run.roots[unit], run.paths[unit], [], programs[unit])
            for unit in experiments
        ]

    catalog.connection.register(
        _FILES_TABLE, pa.table({"entry_id": pa.array(sorted(in_experiment), type=pa.string())})
    )
    try:
        classes = {
            str(subject): str(key)
            for subject, key in catalog.query(
                f"SELECT c.subject, c.object_reference FROM claims c JOIN {_FILES_TABLE} f "  # noqa: S608
                "ON f.entry_id = c.subject WHERE c.predicate = ? AND c.resolution_status = "
                "'resolved' ORDER BY c.subject",
                [GROUPING_PREDICATE],
            ).fetchall()
        }
        order = {rule: place for place, rule in enumerate(stage_rules)}
        stages: dict[str, tuple[int, str]] = {}
        if stage_rules:
            marks = ", ".join("?" for _ in stage_rules)
            for subject, rule, stage in catalog.query(
                f"SELECT c.subject, c.rule_id, c.object_reference FROM claims c "  # noqa: S608
                f"JOIN {_FILES_TABLE} f ON f.entry_id = c.subject "
                f"WHERE c.rule_id IN ({marks}) AND c.resolution_status = 'resolved' "
                "AND c.object_reference IS NOT NULL",
                list(stage_rules),
            ).fetchall():
                held_stage = (order[str(rule)], str(stage))
                if str(subject) not in stages or held_stage < stages[str(subject)]:
                    stages[str(subject)] = held_stage
        empty = {
            str(subject)
            for (subject,) in catalog.query(
                f"SELECT DISTINCT x.subject FROM findings x JOIN {_FILES_TABLE} f "  # noqa: S608
                "ON f.entry_id = x.subject WHERE x.rule_id = ? "
                "AND list_contains(x.raised, 'empty_output')",
                [DEFAULT_UNREADABLE_RULE],
            ).fetchall()
        }
        layouts = {
            str(entry): json.loads(str(slots))
            for entry, slots in catalog.query(
                f"SELECT s.entry_id, s.slots_json FROM name_shapes s JOIN {_FILES_TABLE} f "  # noqa: S608
                "ON f.entry_id = s.entry_id WHERE s.source_scope = 'name' ORDER BY s.entry_id"
            ).fetchall()
        }
        components = {
            str(row[0]): row[1:]
            for row in catalog.query(
                f"SELECT c.component_id, c.component_type, c.normalized_value, c.raw_value, "  # noqa: S608
                f"c.magnitude, c.dimension FROM components c JOIN {_FILES_TABLE} f "
                "ON f.entry_id = c.entry_id WHERE c.source_scope = 'name'"
            ).fetchall()
        }
    finally:
        catalog.connection.unregister(_FILES_TABLE)

    by_experiment: dict[str, list[DesignFile]] = {unit: [] for unit in experiments}
    for entry_id in sorted(in_experiment):
        slots = layouts.get(entry_id)
        by_experiment[in_experiment[entry_id]].append(
            DesignFile(
                entry_id=entry_id,
                name=names[entry_id],
                class_key=classes.get(entry_id),
                stage=stages[entry_id][1] if entry_id in stages else None,
                empty=entry_id in empty,
                clock_ns=clocks.get(entry_id),
                parts=name_parts_of(slots, components, names[entry_id]) if slots else (),
            )
        )
    return [
        ExperimentFiles(unit, run.roots[unit], run.paths[unit], by_experiment[unit], programs[unit])
        for unit in experiments
    ]
