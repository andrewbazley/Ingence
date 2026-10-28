"""Find the repeats of each experiment: the same experiment done again on another day.

Each experiment gets a signature. It holds the words of the experiment folder's own name, read as
the clustering rule reads them (``experiment_terms`` with ``term_source`` ``folder_name``: every
word not bound to a kind that says how rather than what, with digits and short words left out). It
also holds the formats that the experiment's instruments wrote. These are the formats in which the
format rule (``acquisition_stage_from_format``) concluded acquisitions, and never any other stage
claim. Experiments with the same signature that began on different days repeat each other.

The result is a projection, recomputed from a published run. It is never a claim and never a
question. It fills ``repeats`` on ``/api/v1/experiments`` and on the overview, and names the
repeats in an experiment's story. The file types that the members hold are reported on the group
and are no part of the key. An analysis table or a database written beside the recordings of one
repeat and not another is no reason to tell the repeats apart.

Experiments repeat only on different days. Two folders of one day that share a signature are parts
of one experiment, or one experiment split in two, and are not a repeat. A group of repeats holds
two or more start days. An experiment that nothing dates is in no group, because an unknown date
is not a day.

The module reads the run's resolved claims and the Index. It reads no file and writes nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyarrow as pa

from ingence.experiment_units import experiment_times, run_units

if TYPE_CHECKING:
    from ingence.experiment_units import RunUnits
    from ingence.infer.subjects import EntryRecords
    from ingence.storage.catalog import Catalog

__all__ = [
    "RepeatGroup",
    "Signature",
    "experiment_signatures",
    "experiment_types",
    "repeat_groups",
    "repeats_counted",
]

#: The Dictionary kinds whose words say how a file was made, when, or which one it is, never
#: what the experiment is about: left out of a signature (the themes' "how" kinds, with the
#: stage and replicate words the brief names).
HOW_KINDS: frozenset[str] = frozenset(
    {
        "channel",
        "connective",
        "count",
        "dataset",
        "date",
        "experiment",
        "file_role",
        "format",
        "identifier",
        "measurement",
        "part",
        "process",
        "project",
        "quality",
        "quantifier",
        "replicate",
        "stage",
        "statistic",
        "status",
        "unit",
        "version",
    }
)

#: The rule whose conclusions say a file is data as an instrument wrote it.
ACQUISITION_RULE = "acquisition_stage_from_format"
#: The fewest characters a word needs to count.
MINIMUM_WORD_CHARACTERS = 3

_UNITS_TABLE = "repeats_units"
_FOLDERS_TABLE = "repeats_folders"

_TYPES_SQL = f"""
SELECT u.experiment_id,
       lower(coalesce(nullif(e.compound_extension, ''), nullif(e.extension, ''))) AS kind,
       bool_or(c.claim_id IS NOT NULL) AS recorded
FROM entries e
JOIN {_UNITS_TABLE} u ON u.folder_id = e.parent_entry_id
LEFT JOIN claims c ON c.subject = e.entry_id AND c.resolution_status = 'resolved'
    AND c.rule_id = $acquisition_rule
WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'
  AND coalesce(nullif(e.compound_extension, ''), nullif(e.extension, '')) IS NOT NULL
GROUP BY 1, 2 ORDER BY 1, 2
"""  # noqa: S608 -- the one name spelled in is this module's constant


@dataclass(frozen=True, slots=True)
class Signature:
    """What makes two experiments the same experiment: its folder's words and its instruments."""

    words: tuple[str, ...]
    instruments: tuple[str, ...]

    @property
    def key(self) -> str:
        """The group identifier every experiment of this signature shares."""
        digest = hashlib.blake2b(
            json.dumps([self.words, self.instruments]).encode("utf-8"), digest_size=6
        ).hexdigest()
        return f"rep_{digest}"


@dataclass(frozen=True, slots=True)
class RepeatGroup:
    """Experiments that repeat each other, and what they share."""

    group: str
    signature: Signature
    #: The experiments, by entry, each with its start day, earliest first.
    experiments: tuple[tuple[str, str], ...]
    #: The file types the members hold, every member's together: reported, no part of the key.
    types: tuple[str, ...] = ()

    @property
    def members(self) -> tuple[str, ...]:
        return tuple(experiment for experiment, _ in self.experiments)


def _registered(catalog: Catalog, run: RunUnits) -> None:
    folders = sorted(run.units)
    catalog.connection.register(
        _UNITS_TABLE,
        pa.table(
            {
                "folder_id": pa.array(folders, type=pa.string()),
                "experiment_id": pa.array([run.units[f] for f in folders], type=pa.string()),
            }
        ),
    )


def _folder_records(catalog: Catalog, experiments: list[str]) -> list[EntryRecords]:
    """The experiment folders' own records (entry, components, bindings), as the pass holds them,
    so the clustering rule's reading of a folder's words is the one used here."""
    from ingence.core.schema import Component, DictionaryBinding, Entry
    from ingence.infer.records import batched_rows
    from ingence.infer.subjects import EntryRecords
    from ingence.storage.views import view_from_row

    catalog.connection.register(
        _FOLDERS_TABLE, pa.table({"entry_id": pa.array(experiments, type=pa.string())})
    )
    try:
        entries = {
            str(row["entry_id"]): view_from_row(row, Entry)
            for row in batched_rows(
                catalog,
                f"SELECT e.* FROM entries e JOIN {_FOLDERS_TABLE} f ON f.entry_id = e.entry_id",  # noqa: S608
            )
        }
        components: dict[str, list[Component]] = {}
        for row in batched_rows(
            catalog,
            f"SELECT c.* FROM components c JOIN {_FOLDERS_TABLE} f ON f.entry_id = c.entry_id "  # noqa: S608
            "ORDER BY c.component_id",
        ):
            components.setdefault(str(row["entry_id"]), []).append(view_from_row(row, Component))
        bindings: dict[str, list[DictionaryBinding]] = {}
        for row in batched_rows(
            catalog,
            f"SELECT b.* FROM dictionary_bindings b JOIN {_FOLDERS_TABLE} f "  # noqa: S608
            "ON f.entry_id = b.entry_id ORDER BY b.binding_id",
        ):
            bindings.setdefault(str(row["entry_id"]), []).append(
                view_from_row(row, DictionaryBinding)
            )
    finally:
        catalog.connection.unregister(_FOLDERS_TABLE)
    return [
        EntryRecords(
            entry=entries[unit],
            components=tuple(components.get(unit, ())),
            bindings=tuple(bindings.get(unit, ())),
        )
        for unit in experiments
        if unit in entries
    ]


def experiment_signatures(catalog: Catalog, run: RunUnits | None = None) -> dict[str, Signature]:
    """Each experiment's signature, by its folder's entry."""
    from ingence.rulebook.predicates.experiments import experiment_terms

    held = run if run is not None else run_units(catalog)
    experiments = sorted(set(held.units.values()))
    if not experiments:
        return {}
    records = _folder_records(catalog, experiments)
    terms, _, _ = experiment_terms(
        records,
        {
            "term_source": "folder_name",
            "ignored_theme_kinds": sorted(HOW_KINDS),
            "minimum_word_characters": MINIMUM_WORD_CHARACTERS,
            "acquisition_rules": [ACQUISITION_RULE],
        },
        units={unit: unit for unit in experiments},
        folders={record.entry.entry_id: record for record in records},
    )
    _registered(catalog, held)
    try:
        type_rows = catalog.connection.execute(
            _TYPES_SQL, {"acquisition_rule": ACQUISITION_RULE}
        ).fetchall()
    finally:
        catalog.connection.unregister(_UNITS_TABLE)
    instruments: dict[str, set[str]] = {unit: set() for unit in experiments}
    for unit, kind, recorded in type_rows:
        if recorded:
            instruments.setdefault(str(unit), set()).add(str(kind))
    return {
        unit: Signature(
            words=tuple(sorted(terms.get(unit, frozenset()))),
            instruments=tuple(sorted(instruments[unit])),
        )
        for unit in experiments
    }


def experiment_types(catalog: Catalog, run: RunUnits | None = None) -> dict[str, tuple[str, ...]]:
    """Each experiment's file types, for reporting on a group of repeats."""
    held = run if run is not None else run_units(catalog)
    experiments = sorted(set(held.units.values()))
    if not experiments:
        return {}
    _registered(catalog, held)
    try:
        type_rows = catalog.connection.execute(
            _TYPES_SQL, {"acquisition_rule": ACQUISITION_RULE}
        ).fetchall()
    finally:
        catalog.connection.unregister(_UNITS_TABLE)
    types: dict[str, set[str]] = {unit: set() for unit in experiments}
    for unit, kind, _ in type_rows:
        types.setdefault(str(unit), set()).add(str(kind))
    return {unit: tuple(sorted(kinds)) for unit, kinds in types.items()}


def repeat_groups(catalog: Catalog, run: RunUnits | None = None) -> dict[str, RepeatGroup]:
    """Each experiment in a group of repeats, to its group; an experiment in none is absent.

    A group is two or more experiments of one signature with two or more distinct start days;
    an experiment whose signature holds no word is in none (nothing says what it is about).
    """
    held = run if run is not None else run_units(catalog)
    signatures = experiment_signatures(catalog, held)
    dates = experiment_times(catalog, held)
    types = experiment_types(catalog, held)
    by_signature: dict[Signature, list[tuple[str, str]]] = {}
    for unit, signature in signatures.items():
        day = dates.get(unit, (None, None))[0]
        if day is None or not signature.words:
            continue
        by_signature.setdefault(signature, []).append((day, unit))
    found: dict[str, RepeatGroup] = {}
    for signature, members in by_signature.items():
        if len(members) < 2 or len({day for day, _ in members}) < 2:
            continue
        ordered = tuple((unit, day) for day, unit in sorted(members))
        group = RepeatGroup(
            group=signature.key,
            signature=signature,
            experiments=ordered,
            types=tuple(sorted({kind for unit, _ in ordered for kind in types.get(unit, ())})),
        )
        for unit, _ in ordered:
            found[unit] = group
    return found


def repeats_counted(groups: dict[str, RepeatGroup], experiments: int) -> tuple[int, int, int]:
    """Experiments seen once, experiments repeated, and groups of repeats, for an overview."""
    repeated = len(groups)
    return experiments - repeated, repeated, len({group.group for group in groups.values()})
