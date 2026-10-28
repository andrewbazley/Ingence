"""Build the experiment map's edges: how two experiments connect, and why.

The map is a projection of nodes and typed edges with evidence. It is recomputed from a published
run and is never a claim. Each experiment is a node (its label, area, line of work, first and last
day, how complete, how many repeats). Two experiments are joined by every kind of overlap the run
shows:

- ``shared_words``: words of the Dictionary's kinds that say what an experiment is about
  (:data:`ABOUT_KINDS`: a condition, a reagent, a subject, a sample, a culture, a time point),
  in both folders' names or their acquisitions' names;
- ``workflow``: the same workflow in force (``completeness/workflow_steps.py``);
- ``instrument``: the same instrument's format among their recorded files;
- ``repeat_of``: the same group of repeats (``grouping/repeats.py``);
- ``copy``: a file of one byte for byte a file of the other, by the run's copy classes;
- ``made_from``: a derivation the run proposed or published from a file of one into a file of the
  other.

The weight is the cosine similarity of the two experiments' features
(:func:`~ingence.grouping.experiment_links.similarities`). Each kind's evidence is a feature,
weighed by how few experiments hold it: a word two experiments share weighs most, and a workflow
every experiment follows weighs little. Nothing is cut as common here, so an edge is drawn
wherever one kind holds, and the weight says how much it is worth. Lines of work colour the nodes,
read from the themes that the completeness rows carry.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING

import pyarrow as pa

from ingence.completeness.workflow_steps import steps_reached
from ingence.experiment_units import experiment_times, run_units
from ingence.grouping.experiment_links import similarities
from ingence.grouping.repeats import experiment_signatures, repeat_groups

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.completeness import ExperimentFound
    from ingence.storage.catalog import Catalog

__all__ = ["ABOUT_KINDS", "KINDS", "Edge", "Node", "connections"]

#: The Dictionary kinds whose words say what an experiment is about.
ABOUT_KINDS: frozenset[str] = frozenset(
    {"condition", "reagent", "subject", "sample", "culture", "timepoint"}
)
#: The kinds of edge, in the order a page lists them.
KINDS: tuple[str, ...] = (
    "shared_words",
    "workflow",
    "instrument",
    "repeat_of",
    "copy",
    "made_from",
)
#: The rules whose classes are copies by content.
COPY_RULES: tuple[str, ...] = ("identical_by_content_digest", "copies_by_sampled_digest")

_UNITS_TABLE = "connections_units"

_WORDS_SQL = f"""
WITH
own AS (
    SELECT experiment_id, folder_id AS entry_id FROM {_UNITS_TABLE}
    WHERE folder_id = experiment_id
),
acquired AS (
    SELECT DISTINCT u.experiment_id, e.entry_id
    FROM entries e
    JOIN {_UNITS_TABLE} u ON u.folder_id = e.parent_entry_id
    JOIN claims c ON c.subject = e.entry_id AND c.resolution_status = 'resolved'
        AND c.predicate LIKE 'classifies.%stage%' AND c.object_reference = 'acquisition'
    WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'
),
sources AS (SELECT * FROM own UNION SELECT * FROM acquired)
SELECT DISTINCT s.experiment_id, c.normalized_value
FROM sources s
JOIN components c ON c.entry_id = s.entry_id AND c.component_type = 'word'
JOIN dictionary_bindings b ON b.component_id = c.component_id
    AND b.resolution_status = 'resolved' AND list_contains($kinds, b.theme_kind)
ORDER BY 1, 2
"""  # noqa: S608 -- the one name spelled in is this module's constant

_COPIES_SQL = f"""
SELECT c.object_reference, list(DISTINCT u.experiment_id)
FROM claims c
JOIN entries e ON e.entry_id = c.subject AND NOT e.is_deleted
JOIN {_UNITS_TABLE} u ON u.folder_id = e.parent_entry_id
WHERE list_contains($rules, c.rule_id) AND c.resolution_status = 'resolved'
GROUP BY 1 HAVING count(DISTINCT u.experiment_id) > 1
"""  # noqa: S608 -- the one name spelled in is this module's constant

_MADE_FROM_SQL = f"""
SELECT us.experiment_id, ut.experiment_id, count(*)
FROM (
    SELECT source_id, target_id FROM candidates WHERE primitive = 'derives'
    UNION
    SELECT source_id, target_id FROM assertions
    WHERE decision_status = 'asserted' AND primitive = 'derives'
) d
JOIN entries s ON s.entry_id = d.source_id
JOIN entries t ON t.entry_id = d.target_id
JOIN {_UNITS_TABLE} us ON us.folder_id = s.parent_entry_id
JOIN {_UNITS_TABLE} ut ON ut.folder_id = t.parent_entry_id
WHERE us.experiment_id <> ut.experiment_id
GROUP BY 1, 2
"""  # noqa: S608 -- the one name spelled in is this module's constant


@dataclass(frozen=True, slots=True)
class Node:
    """One experiment on the map."""

    experiment_id: str
    label: str
    area: str | None
    area_kind: str | None
    theme: str | None
    start: str | None
    end: str | None
    completeness: float | None
    repeats: int


@dataclass(frozen=True, slots=True)
class Evidence:
    """Why an edge is drawn, for one kind."""

    kind: str
    words: tuple[str, ...] = ()
    count: int | None = None
    #: What the count counts: files, words or experiments.
    unit: str | None = None
    #: For a made_from edge, which experiment the other was made from, and which was made.
    from_id: str | None = None
    to_id: str | None = None


@dataclass(frozen=True, slots=True)
class Edge:
    """Two experiments that overlap, every way they do, and how strongly."""

    a: str
    b: str
    kinds: tuple[str, ...]
    weight: float
    evidence: tuple[Evidence, ...]


@dataclass
class _Overlaps:
    words: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    workflow: dict[tuple[str, str], str] = field(default_factory=dict)
    instrument: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    repeat: set[tuple[str, str]] = field(default_factory=set)
    copies: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    made_from: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    #: The direction of each made_from edge: the source experiment and the target, by the
    #: larger count where derivations run both ways.
    direction: dict[tuple[str, str], tuple[str, str, int]] = field(default_factory=dict)


def _pair(first: str, second: str) -> tuple[str, str]:
    return (first, second) if first < second else (second, first)


def connections(
    catalog: Catalog, found: Sequence[ExperimentFound], labels: Mapping[str, str] | None = None
) -> tuple[list[Node], list[Edge]]:
    """The map's nodes and edges for the catalog's run.

    ``found`` is the run's completeness rows (one per experiment, with its line of work and
    its share of raw files followed); ``labels`` each experiment's shown name, where the caller
    holds them, else the folder's own name.
    """
    units = run_units(catalog)
    experiments = sorted(set(units.units.values()))
    if not experiments:
        return [], []
    times = experiment_times(catalog, units)
    groups = repeat_groups(catalog, units)
    steps = steps_reached(catalog, units)
    signatures = experiment_signatures(catalog, units)
    folders = sorted(units.units)
    catalog.connection.register(
        _UNITS_TABLE,
        pa.table(
            {
                "folder_id": pa.array(folders, type=pa.string()),
                "experiment_id": pa.array([units.units[f] for f in folders], type=pa.string()),
            }
        ),
    )
    try:
        word_rows = catalog.connection.execute(
            _WORDS_SQL, {"kinds": sorted(ABOUT_KINDS)}
        ).fetchall()
        copy_rows = catalog.connection.execute(_COPIES_SQL, {"rules": list(COPY_RULES)}).fetchall()
        made_rows = catalog.connection.execute(_MADE_FROM_SQL).fetchall()
    finally:
        catalog.connection.unregister(_UNITS_TABLE)
    about: dict[str, set[str]] = {unit: set() for unit in experiments}
    for unit, word in word_rows:
        about.setdefault(str(unit), set()).add(str(word))
    held = _Overlaps()
    features: dict[str, set[str]] = {unit: set() for unit in experiments}
    by_word: dict[str, list[str]] = defaultdict(list)
    for unit in experiments:
        for word in about[unit]:
            features[unit].add(f"w:{word}")
            by_word[word].append(unit)
        for kind in signatures[unit].instruments:
            features[unit].add(f"i:{kind}")
        if unit in steps:
            features[unit].add(f"wf:{steps[unit].workflow_id}")
        if unit in groups:
            features[unit].add(f"rep:{groups[unit].group}")
    for word, holders in by_word.items():
        for first, second in combinations(sorted(holders), 2):
            held.words[_pair(first, second)].add(word)
    by_workflow: dict[str, list[str]] = defaultdict(list)
    for unit, step in steps.items():
        by_workflow[step.workflow_id].append(unit)
    for workflow_id, holders in by_workflow.items():
        for first, second in combinations(sorted(holders), 2):
            held.workflow[_pair(first, second)] = workflow_id
    by_instrument: dict[str, list[str]] = defaultdict(list)
    for unit in experiments:
        for kind in signatures[unit].instruments:
            by_instrument[kind].append(unit)
    for kind, holders in by_instrument.items():
        for first, second in combinations(sorted(holders), 2):
            held.instrument[_pair(first, second)].add(kind)
    for group in {g.group: g for g in groups.values()}.values():
        for first, second in combinations(sorted(group.members), 2):
            held.repeat.add(_pair(first, second))
    for key, holders in copy_rows:
        members = sorted(str(unit) for unit in holders)
        for first, second in combinations(members, 2):
            held.copies[_pair(first, second)] += 1
            features[first].add(f"c:{key}")
            features[second].add(f"c:{key}")
    for source, target, count in made_rows:
        pair = _pair(str(source), str(target))
        held.made_from[pair] += int(count)
        if int(count) > held.direction.get(pair, ("", "", 0))[2]:
            held.direction[pair] = (str(source), str(target), int(count))
        features[pair[0]].add(f"m:{pair[0]}>{pair[1]}")
        features[pair[1]].add(f"m:{pair[0]}>{pair[1]}")
    rows = {unit: frozenset(found_features) for unit, found_features in features.items()}
    weights = similarities(rows, common_share=1.0, floor=len(rows) + 1)
    pairs = (
        set(held.words)
        | set(held.workflow)
        | set(held.instrument)
        | held.repeat
        | set(held.copies)
        | set(held.made_from)
    )
    edges: list[Edge] = []
    for pair in sorted(pairs):
        kinds: list[str] = []
        evidence: list[Evidence] = []
        if pair in held.words:
            words = tuple(sorted(held.words[pair]))
            kinds.append("shared_words")
            evidence.append(Evidence("shared_words", words, len(words), "words"))
        if pair in held.workflow:
            step = steps[pair[0]]
            kinds.append("workflow")
            evidence.append(
                Evidence(
                    "workflow",
                    tuple(step.expected),
                    len(by_workflow[step.workflow_id]),
                    "experiments",
                )
            )
        if pair in held.instrument:
            kinds_held = tuple(sorted(held.instrument[pair]))
            kinds.append("instrument")
            evidence.append(Evidence("instrument", kinds_held, len(kinds_held), "words"))
        if pair in held.repeat:
            kinds.append("repeat_of")
            evidence.append(Evidence("repeat_of", (), None, "experiments"))
        if pair in held.copies:
            kinds.append("copy")
            evidence.append(Evidence("copy", (), held.copies[pair], "files"))
        if pair in held.made_from:
            kinds.append("made_from")
            source, target, _ = held.direction[pair]
            evidence.append(
                Evidence("made_from", (), held.made_from[pair], "files", source, target)
            )
        weight = weights.get(pair, (0.0, ()))[0]
        edges.append(
            Edge(
                a=pair[0],
                b=pair[1],
                kinds=tuple(kinds),
                weight=round(min(1.0, max(0.0, weight)), 4),
                evidence=tuple(evidence),
            )
        )
    by_id = {row.experiment_id: row for row in found}
    nodes = [
        Node(
            experiment_id=unit,
            label=(labels or {}).get(unit) or units.paths.get(unit, unit).rsplit("/", 1)[-1],
            area=by_id[unit].person if unit in by_id else None,
            area_kind="person" if unit in by_id and by_id[unit].person else None,
            theme=by_id[unit].theme_label if unit in by_id else None,
            start=times.get(unit, (None, None))[0],
            end=times.get(unit, (None, None))[1],
            completeness=by_id[unit].share if unit in by_id else None,
            repeats=len(groups[unit].members) if unit in groups else 0,
        )
        for unit in experiments
    ]
    return nodes, edges
