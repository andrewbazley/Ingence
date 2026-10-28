"""What an experiment is, and whose, for every projection that counts by experiment.

Completeness (``ingence.completeness``) and themes (``ingence.themes``) share this one definition.
They count the same experiments and the same people, so the two pages never disagree about what
an experiment is.

- An experiment is a folder that the experiment rules concluded on, made one set by the
  clustering rule's own ``policy`` (:func:`policy_experiments`: by default a dated folder name
  first, then what a folder holds). Each file belongs to the highest such folder above it. A
  folder inside another is part of it and never an experiment of its own. A file under none
  belongs to no experiment. The set is not every folder that a rule resolved, whatever the
  policy said. A person's folder that a rule read as binding the experiments beneath it (they
  share the names their acquisitions carry) would hold them all as one, and the themes and the
  files page would count different numbers. A folder concluded by a rule of the project's own,
  beyond the two, is weighed by the policy with the structure rule's folders.
  The files page places a file by this rule as well. So a list of experiments and one
  experiment's files agree where one experiment folder lies inside another, or beneath a
  program. The page's files are the files of :func:`folders_of_experiment`.
- A person is the folder ``themes.person_level`` levels below the experiment's root (1: the
  first level, one folder per member), or nobody where the setting is blank or the experiment
  lies at or above that level. A person is never read from names.
- An installed program's folders belong to no experiment. A folder that the engine flags as a
  program's (``installed_software*``) and everything beneath it belong to no experiment, inside
  one or not, so no projection counts a program's files.

The module reads a run's resolved claims. Nothing here reads a file or writes anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.grouping.experiment_links import DEFAULT_POLICY
from ingence.grouping.experiment_links import experiment_units as units_for_policy

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.storage.catalog import Catalog

__all__ = [
    "EXPERIMENT_GROUPS_PREDICATE",
    "EXPERIMENT_PREDICATES",
    "NAME_DATE_PREDICATES",
    "RunUnits",
    "counted_as",
    "counted_folders",
    "experiment_dates",
    "experiment_folders",
    "experiment_times",
    "folders_of_experiment",
    "highest_experiment",
    "person_of",
    "policy_experiments",
    "policy_said",
    "program_folders",
    "rule_parameters",
    "run_units",
]

#: The predicate proposing groups of experiments. Read by predicate, never by a rule's name, so a
#: laboratory's own rule on it is read the same way.
EXPERIMENT_GROUPS_PREDICATE = "experiments_may_belong_together"

#: The two experiment rules, where a run holds no clustering rule to read them from.
_DEFAULT_EXPERIMENT_RULES = ("experiment_folder_from_contents", "experiment_folder_from_dated_name")

#: The predicates naming an experiment's folder (the two experiment rules, and any later one).
EXPERIMENT_PREDICATES = "classifies.experiment_folder%"

#: The date claims an experiment's date is read from: a date in a folder's or a file's name.
NAME_DATE_PREDICATES = (
    "classifies.date_neighbourhood_of_a_file",
    "classifies.date_neighbourhood_of_a_folder",
)
#: The claim of the earliest time a file's own records allow, the date where no name gives one.
EARLIEST_PREDICATE = "classifies.earliest_credible_time"


def experiment_folders(catalog: Catalog) -> frozenset[str]:
    """Every folder a rule of the catalog's run resolved as an experiment's, by its entry."""
    rows = catalog.query(
        "SELECT DISTINCT subject FROM claims WHERE predicate LIKE ? "
        "AND resolution_status = 'resolved'",
        [EXPERIMENT_PREDICATES],
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


def rule_parameters(catalog: Catalog, predicate: str) -> tuple[str | None, dict[str, object]]:
    """The first rule on ``predicate`` in the run, and its parameters' values as declared.

    Read from the run's own rules table, where each parameter is stored with its default
    serialized, decoded as the renderer decodes it. The result is None, with no values, where no
    rule reads it.
    """
    found = catalog.query(
        "SELECT rule_id, parameters_json FROM rules WHERE predicate = ? ORDER BY rule_id LIMIT 1",
        [predicate],
    ).fetchall()
    if not found:
        return None, {}
    values: dict[str, object] = {}
    for declared in json.loads(found[0][1] or "[]"):
        try:
            values[str(declared["name"])] = json.loads(declared.get("default"))
        except (TypeError, ValueError):
            values[str(declared["name"])] = declared.get("default")
    return str(found[0][0]), values


def _policy_in_force(catalog: Catalog) -> tuple[list[str], str]:
    """The two experiment rules and the policy the clustering rule of the run declares."""
    _, parameters = rule_parameters(catalog, EXPERIMENT_GROUPS_PREDICATE)
    listed = parameters.get("experiment_rules")
    rules = (
        [str(rule) for rule in listed]
        if isinstance(listed, list) and len(listed) == 2
        else list(_DEFAULT_EXPERIMENT_RULES)
    )
    return rules, str(parameters.get("policy") or DEFAULT_POLICY)


def policy_experiments(catalog: Catalog) -> dict[str, dict[str, str]]:
    """The folders the experiment rules concluded on, made one set by the policy in force.

    The set is keyed by the folder's entry identifier. An experiment is its folder's entry, never
    its path, so a folder renamed or moved between two runs stays the experiment that a Record
    answer or a topic names. Each value carries ``entry_id`` again, ``path`` (the folder below its
    root, for showing), ``name`` and ``kind``: what the structure rule concluded (``experiment``,
    ``acquisitions``, ``analysis``), or ``named`` for the dated-name rule's. The policy itself
    still weighs the folders by where they nest, which is a matter of paths, and that is the one
    place a path decides anything here.

    A rule of the project's own on an experiment's folder (any other rule concluding
    ``classifies.experiment_folder*``) is evidence of what a folder holds, as the structure rule
    is. So the policy weighs it with that rule's folders. A copy of the structure rule under
    another name changes nothing. Counting its folders beside the policy's would bring back the
    person's folder that holds the experiments beneath it.

    Every route that counts experiments reads this one set: the themes, the completeness figures,
    the Graph ladders and the files page.
    """
    rules, policy = _policy_in_force(catalog)
    concluded = catalog.query(
        "SELECT c.rule_id, c.object_reference AS kind, e.entry_id, e.relative_path, e.name "
        "FROM claims c JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.resolution_status = 'resolved' AND c.object_reference IS NOT NULL "
        "AND e.entry_type = 'directory' AND NOT e.is_deleted "
        "AND (c.rule_id IN (?, ?) OR c.predicate LIKE ?) "
        "ORDER BY e.relative_path, c.rule_id",
        [*rules, EXPERIMENT_PREDICATES],
    ).fetchall()
    structure: dict[str, str] = {}
    named: list[str] = []
    others: dict[str, str] = {}
    held: dict[str, dict[str, str]] = {}
    for rule_id, kind, entry_id, path, name in concluded:
        held[str(path)] = {"entry_id": str(entry_id), "path": str(path), "name": str(name)}
        if rule_id == rules[0]:
            structure[str(path)] = str(kind)
        elif rule_id == rules[1]:
            named.append(str(path))
        else:
            others.setdefault(str(path), str(kind))
    for path, kind in others.items():
        structure.setdefault(path, kind)
    try:
        units = units_for_policy(structure, named, policy=policy)
    except ValueError:
        units = units_for_policy(structure, named, policy=DEFAULT_POLICY)
    return {
        held[path]["entry_id"]: {**held[path], "kind": structure.get(path, "named")}
        for path in units
        if path in held
    }


#: What each policy does where the two rules' folders overlap, in a sentence a page can show.
_POLICY_SAID = {
    "name_first": "Where their folders overlap, a dated folder name wins over what a folder holds.",
    "structure_then_name": "Where their folders overlap, what a folder holds wins over its name.",
    "outermost_evidence": "Where their folders overlap, the outermost folder wins.",
}


def policy_said(policy: str) -> str:
    """What a policy does where the two rules' folders overlap, as a sentence.

    A policy this build has no sentence for is named, in its own words.
    """
    return _POLICY_SAID.get(
        policy,
        f"Where their folders overlap, the policy called {policy.replace('_', ' ')} decides.",
    )


def counted_folders(catalog: Catalog) -> frozenset[str]:
    """Every folder the run counts as an experiment, by its entry: before nesting and programs.

    The folders of :func:`policy_experiments`, the one set the themes, the completeness figures,
    the Graph workspace's ladders and the files page all read.
    """
    return frozenset(policy_experiments(catalog))


def counted_as(catalog: Catalog) -> str:
    """What an experiment is in this run, in sentences a page can show beside a count.

    The themes, the completeness figures, the list of experiments and an experiment's files all
    count by the highest folder and leave out an installed program's. All read the same set of
    folders.
    """
    _, policy = _policy_in_force(catalog)
    return (
        "An experiment is a folder the experiment rules concluded on. "
        f"{policy_said(policy)} "
        "A folder inside another experiment is part of it. "
        "A folder inside an installed program is not an experiment."
    )


def highest_experiment(
    parents: Mapping[str, str | None], experiments: frozenset[str]
) -> dict[str, str]:
    """Each folder's experiment: the highest experiment folder above it, or itself.

    ``parents`` maps every folder to its parent folder (None at a root). A folder under no
    experiment is left out, so the experiments are exactly ``set(result.values())``.
    """
    found: dict[str, str | None] = {}

    def unit(folder: str) -> str | None:
        trail: list[str] = []
        current: str | None = folder
        while current is not None and current not in found:
            trail.append(current)
            current = parents.get(current)
        above = found.get(current) if current is not None else None
        # Root first: the highest experiment on the way down is the one every folder below takes.
        for step in reversed(trail):
            if above is None and step in experiments:
                above = step
            found[step] = above
        return found[folder]

    for folder in sorted(parents):
        unit(folder)
    return {folder: unit_id for folder, unit_id in sorted(found.items()) if unit_id is not None}


def person_of(folder: str, level: int | None) -> str | None:
    """Whose experiment a folder is: the folder ``level`` levels below its root (its name), or
    None where ``level`` is blank or the folder lies at or above that level.

    ``folder`` is the experiment folder's path relative to its root (``.`` for the root itself);
    the folder at depth ``level`` is its ``level``-th part.
    """
    if not level:
        return None
    parts = [part for part in folder.strip("/").split("/") if part and part != "."]
    return parts[level - 1] if len(parts) > level else None


#: The flags naming an installed program's folder (``installed_software_in_folder`` and kin), as
#: the Programs page reads them: a scope finding about ``folder:<entry_id>``.
_PROGRAM_SQL = (
    "SELECT DISTINCT replace(subject, 'folder:', '') FROM findings "
    "WHERE subject_kind = 'scope' AND rule_id LIKE '%installed_software%' "
    "AND subject LIKE 'folder:%'"
)


def _beneath(parents: Mapping[str, str | None], tops: frozenset[str]) -> frozenset[str]:
    """Every folder that is one of ``tops`` or lies beneath one."""
    held: dict[str, bool] = {}

    def inside(folder: str) -> bool:
        trail: list[str] = []
        current: str | None = folder
        while current is not None and current not in held:
            if current in tops:
                held[current] = True
                break
            trail.append(current)
            current = parents.get(current)
        answer = held.get(current, False) if current is not None else False
        for step in trail:
            held[step] = answer
        return held[folder]

    return frozenset(folder for folder in sorted(parents) if inside(folder))


def program_folders(catalog: Catalog, parents: Mapping[str, str | None]) -> frozenset[str]:
    """Every folder the catalog's run flags as an installed program's, and every folder beneath."""
    flagged = frozenset(str(row[0]) for row in catalog.query(_PROGRAM_SQL).fetchall())
    return _beneath(parents, flagged)


@dataclass(frozen=True, slots=True)
class RunUnits:
    """One run's folders and their experiments, by the one definition."""

    #: Every live folder's path below its root, its root, and its parent folder (None at a root).
    paths: dict[str, str]
    roots: dict[str, str]
    parents: dict[str, str | None]
    #: Each folder in an experiment, to the highest experiment folder above it (or itself). A
    #: folder in none, or in a program, is left out. The experiments are its values.
    units: dict[str, str]
    #: The folders of installed programs, and every folder beneath them.
    programs: frozenset[str]


def run_units(catalog: Catalog) -> RunUnits:
    """The experiments of the catalog's run: every live folder, its experiment, the programs."""
    folders = catalog.query(
        "SELECT entry_id, parent_entry_id, root_id, relative_path FROM entries "
        "WHERE NOT is_deleted AND entry_type = 'directory' AND entry_kind = 'filesystem' "
        "ORDER BY entry_id"
    ).fetchall()
    paths = {str(entry): str(path) for entry, _, _, path in folders}
    parents = {
        str(entry): (str(parent) if parent is not None and str(parent) in paths else None)
        for entry, parent, _, _ in folders
    }
    programs = program_folders(catalog, parents)
    # A program's folder is no experiment's: what lies beneath it belongs to none.
    experiments = (counted_folders(catalog) & frozenset(paths)) - programs
    units = {
        folder: unit
        for folder, unit in highest_experiment(parents, experiments).items()
        if folder not in programs
    }
    return RunUnits(
        paths=paths,
        roots={str(entry): str(root) for entry, _, root, _ in folders},
        parents=parents,
        units=units,
        programs=programs,
    )


def folders_of_experiment(run: RunUnits, experiment_id: str) -> list[str]:
    """Every folder the one definition gives to an experiment: its own and each one beneath it.

    A file is the experiment's when its folder is one of these. A folder inside a program, or
    inside a nested experiment folder that the highest rule folds into this one, is as the
    definition decides: :attr:`RunUnits.units` is the only place that is decided. Sorted.
    """
    return sorted(folder for folder, unit in run.units.items() if unit == experiment_id)


_DATES_TABLE = "experiment_dates_units"


def experiment_dates(catalog: Catalog, run: RunUnits | None = None) -> dict[str, str | None]:
    """Each experiment's date: :func:`experiment_times`' first half."""
    return {unit: date for unit, (date, _) in experiment_times(catalog, run).items()}


def experiment_times(
    catalog: Catalog, run: RunUnits | None = None
) -> dict[str, tuple[str | None, str | None]]:
    """Each experiment's date and the last time a file in it was written, as ``(date, last)``.

    The one rule of an experiment's date, used by the themes projection and by completeness
    (so the Years view dates an experiment the same way whether or not themes can be drawn): the
    earliest date a name gives for its folder or for a file directly in a folder of it, else the
    earliest believed time of a file in it (the first ten characters), else None (unknown, never
    a guess). ``last`` is the latest believed time of a file in it. Pass ``run`` where the caller
    holds :func:`run_units` already. Read from the run's resolved claims. Nothing is written.
    """
    import pyarrow as pa

    held = run if run is not None else run_units(catalog)
    experiments = sorted(set(held.units.values()))
    times: dict[str, tuple[str | None, str | None]] = dict.fromkeys(experiments, (None, None))
    if not experiments:
        return times
    folders = sorted(held.units)
    catalog.connection.register(
        _DATES_TABLE,
        pa.table(
            {
                "folder_id": pa.array(folders, type=pa.string()),
                "experiment_id": pa.array([held.units[f] for f in folders], type=pa.string()),
            }
        ),
    )
    try:
        named = catalog.connection.execute(
            f"""
            SELECT u.experiment_id, min(json_extract_string(c.object_reference, '$.from'))
            FROM claims c
            JOIN entries e ON e.entry_id = c.subject AND NOT e.is_deleted
            JOIN {_DATES_TABLE} u
              ON u.folder_id = CASE WHEN e.entry_type = 'directory' THEN e.entry_id
                                    ELSE e.parent_entry_id END
            WHERE list_contains($named, c.predicate) AND c.resolution_status = 'resolved'
              AND e.entry_type IN ('directory', 'file')
            GROUP BY 1
            """,  # noqa: S608 -- the one name spelled in is this module's constant
            {"named": list(NAME_DATE_PREDICATES)},
        ).fetchall()
        believed = catalog.connection.execute(
            f"""
            SELECT u.experiment_id, min(json_extract_string(c.object_reference, '$.at')),
                   max(json_extract_string(c.object_reference, '$.at'))
            FROM claims c
            JOIN entries e ON e.entry_id = c.subject AND NOT e.is_deleted
                          AND e.entry_type = 'file'
            JOIN {_DATES_TABLE} u ON u.folder_id = e.parent_entry_id
            WHERE c.predicate = $earliest AND c.resolution_status = 'resolved'
            GROUP BY 1
            """,  # noqa: S608 -- the one name spelled in is this module's constant
            {"earliest": EARLIEST_PREDICATE},
        ).fetchall()
    finally:
        catalog.connection.unregister(_DATES_TABLE)
    earliest = {str(unit): str(at) for unit, at, _ in believed if at}
    latest = {str(unit): str(at) for unit, _, at in believed if at}
    first = {str(unit): str(date) for unit, date in named if date}
    for unit in experiments:
        believed_first = earliest.get(unit)
        times[unit] = (
            first.get(unit) or (believed_first[:10] if believed_first else None),
            latest.get(unit),
        )
    return times
