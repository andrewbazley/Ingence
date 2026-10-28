"""What one run says that a topic's members resolve against.

Four readings, each from the run and each only when a topic needs it:

- :class:`RunBase`: the run's experiments by the one definition (``experiment_units.run_units``:
  a folder the experiment rules concluded on, each file belonging to the highest such folder
  above it, nothing inside an installed program), and how many files each holds. Held in memory
  per run, because a published run never changes.
- :class:`EntryInfo` of the files and folders a topic names, live or not.
- the experiments' terms, for lattice cores: read by the lattice's own definition
  (``api/v1/groups``: the same words the Groupings page shows), through :func:`key_of_word`.
- the run's lines of work, from the themes projection, reached through the theme registry.

Nothing here writes, and nothing is a claim: this is the topics' projection, recomputed when read.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from ingence.experiment_units import run_units
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from ingence.themes.lines import BroadLine, Line

__all__ = [
    "LIVE_FILE",
    "EntryInfo",
    "LineReading",
    "RunBase",
    "RunView",
    "entry_infos",
    "files_of",
    "key_of_word",
    "load_view",
    "read_cores",
    "read_lines",
    "run_base",
]


#: What counts as a file of an experiment: every live entry of the filesystem that is not a
#: directory, a link included -- the rule the Experiments list (``api/v1/experiment_files``) and the
#: completeness figures (``completeness/counting``) count by, so a topic's "n files" is the sum of
#: the rows of the experiments it holds. (The themes projection counts regular files only.)
LIVE_FILE = "NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'"


def key_of_word(word: str) -> str:
    """The key a word is looked up by in an experiment's terms when no word map was read: the
    word casefolded, as the lattice's spellings are.

    A run read through :func:`read_cores` carries the lattice's own mapping (``core_ways``, from
    ``grouping/lattice_words.WordMap``, the one function that turns a spelling into the word the
    lattice sees under ``groups.words_as``), and a core's words are read through that. This is the
    fallback for a view built by hand, and the one place that says how a word is keyed without it.
    """
    return word.casefold()


type CoreWays = Callable[[Sequence[str]], Sequence[Sequence[str]]]


@dataclass(frozen=True)
class RunBase:
    """A run's experiments, their files, and the folders that place everything else."""

    run_id: str
    #: Experiment identifier (its folder's entry) to its folder's path below its root.
    experiments: Mapping[str, str]
    #: Each folder in an experiment to the highest experiment folder above it (or itself).
    units: Mapping[str, str]
    #: Every folder to its parent folder (None at a root).
    parents: Mapping[str, str | None]
    #: Files in each experiment: the files whose highest experiment it is.
    files: Mapping[str, int]
    #: Whether the run applied any rules. A census applies none, so it holds no experiments yet:
    #: that is unknown, never "none", and a topic read against it says so.
    rules_applied: bool = True


@dataclass(frozen=True)
class EntryInfo:
    """A live file or folder a topic names."""

    entry_id: str
    kind: Literal["file", "folder"]
    parent: str | None
    name: str
    path: str


@dataclass(frozen=True)
class LineReading:
    """A run's lines of work, and its broad lines."""

    lines: tuple[Line, ...]
    broad: tuple[BroadLine, ...]


@dataclass(frozen=True)
class RunView:
    """Everything a topic's members are read against in one run."""

    base: RunBase
    entries: Mapping[str, EntryInfo] = field(default_factory=dict)
    #: Each experiment's terms. It is None, with ``cores_why``, where they could not be read.
    core_terms: Mapping[str, frozenset[str]] | None = None
    cores_why: str | None = None
    lines: LineReading | None = None
    lines_why: str | None = None
    #: For a view of an earlier run: each entry now to the identifiers it had before it moved,
    #: and each identifier then to the one now, so a moved member and a moved experiment are
    #: followed. Empty for the run read now.
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    moved: Mapping[str, str] = field(default_factory=dict)
    #: A core's stored words to the ways the lattice reads them in this run (each a sorted tuple
    #: of words an experiment's terms must hold). None reads each word by :func:`key_of_word`.
    core_ways: CoreWays | None = None
    #: Each standing goal's experiments in this run, by goal identifier. It is None,
    #: with ``goals_why``, where the goals could not be read against the run.
    goals: Mapping[str, tuple[str, ...]] | None = None
    goals_why: str | None = None


_BASES: dict[tuple[str, str], RunBase] = {}
_BASES_KEPT = 4
_LOCK = threading.Lock()


def run_base(project: Path, run_id: str) -> RunBase:
    """The run's experiments and their files, held in memory for the few runs a page moves
    between (a published run's tables never change)."""
    key = (str(project.resolve()), run_id)
    with _LOCK:
        held = _BASES.get(key)
    if held is not None:
        return held
    import pyarrow as pa

    with open_catalog(project, run_id=run_id) as catalog:
        applied = catalog.count("rules") > 0
        shared = run_units(catalog)
        folders = sorted(shared.units)
        counted: dict[str, int] = {}
        if folders:
            table = "topic_unit_map"
            catalog.connection.register(
                table,
                pa.table(
                    {
                        "folder_id": pa.array(folders, type=pa.string()),
                        "experiment_id": pa.array(
                            [shared.units[folder] for folder in folders], type=pa.string()
                        ),
                    }
                ),
            )
            try:
                rows = catalog.connection.execute(
                    f"SELECT u.experiment_id, count(*) FROM entries e JOIN {table} u "  # noqa: S608
                    "ON u.folder_id = e.parent_entry_id "
                    f"WHERE {LIVE_FILE} GROUP BY 1"
                ).fetchall()
            finally:
                catalog.connection.unregister(table)
            counted = {str(experiment): int(count) for experiment, count in rows}
    experiments = sorted(set(shared.units.values()))
    built = RunBase(
        run_id=run_id,
        experiments={experiment: shared.paths.get(experiment, "") for experiment in experiments},
        units=dict(sorted(shared.units.items())),
        parents=dict(sorted(shared.parents.items())),
        files={experiment: counted.get(experiment, 0) for experiment in experiments},
        rules_applied=applied,
    )
    with _LOCK:
        _BASES[key] = built
        while len(_BASES) > _BASES_KEPT:
            _BASES.pop(next(iter(_BASES)))
    return built


def entry_infos(project: Path, run_id: str, ids: Sequence[str]) -> dict[str, EntryInfo]:
    """The live files and folders among ``ids`` in the run; an entry the run does not hold (moved
    away, gone) is left out, which is how a member resolving to nothing is told."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    with open_catalog(project, run_id=run_id) as catalog:
        rows = catalog.query(
            "SELECT entry_id, entry_type, parent_entry_id, name, relative_path FROM entries "
            "WHERE NOT is_deleted AND list_contains(?, entry_id)",
            [wanted],
        ).fetchall()
    return {
        str(entry): EntryInfo(
            entry_id=str(entry),
            kind="folder" if kind == "directory" else "file",
            parent=None if parent is None else str(parent),
            name=str(name),
            path=str(path or ""),
        )
        for entry, kind, parent, name, path in rows
    }


def _lattice_reading(
    catalog: Any, project: Path, run_id: str
) -> tuple[dict[str, frozenset[str]], Callable[[Sequence[str]], Sequence[Sequence[str]]]]:
    """The one place the topics reach into the lattice page's private helpers.

    The experiments' terms and the lattice's mapping of a core's stored words, read the way the
    Groupings page reads them: with the run's own resolving rule's parameters (or the defaults
    where the run holds none), in the words the run was bound in (``run_words_as``, the public
    reading of what the run's journal says). Imported here, outside any ``try``, so a helper that
    is renamed fails loudly and not as "could not be read". When the lattice reading is made
    public, this function is the only one to change.
    """
    from ingence.api.v1.groups import _rules_in_run, _term_sets, _word_map
    from ingence.grouping.lattice_words import run_words_as
    from ingence.rulebook.predicates.experiments import (
        RESOLUTION_PREDICATES,
        resolution_parameters,
    )

    words_as = run_words_as(catalog)
    rules = _rules_in_run(catalog)
    readers = [
        values
        for predicate, values in (rules[rule] for rule in sorted(rules))
        if predicate in RESOLUTION_PREDICATES
    ]
    declared = next((values for values in readers if "term_source" in values), {})
    proposal = rules.get(str(declared.get("proposal_rule", "")))
    parameters = resolution_parameters(declared, proposal[1] if proposal else None)
    held, _labels, _never = _term_sets(catalog, project, run_id, parameters, words_as)
    return held, _word_map(catalog, project, run_id, words_as).answer


def read_cores(
    project: Path, run_id: str, base: RunBase
) -> tuple[dict[str, frozenset[str]] | None, CoreWays | None, str | None]:
    """Each experiment's terms by the lattice's own definition, the way a core's words are read in
    this run, or why they could not be read.

    The words an experiment carries are the ones the Groupings page shows
    (:func:`_lattice_reading`), in the words the run was bound in. Only experiments of ``base``
    are returned: a folder the lattice reads that the run counts inside another experiment, or
    inside a program, is the larger one's. A core's words are read through the lattice's one
    mapping (``WordMap.answer``): under ``terms`` a spelling a core was followed by is the term
    it is bound to, and under ``spellings`` a term is the spellings the names write.
    """
    import duckdb

    from ingence.core.project import NotAProjectError
    from ingence.rulebook.registry import PredicateNotEvaluableError

    try:
        with open_catalog(project, run_id=run_id) as catalog:
            held, mapping = _lattice_reading(catalog, project, run_id)
    except (
        PredicateNotEvaluableError,
        NotAProjectError,
        duckdb.Error,
        OSError,
        ValueError,
    ) as error:
        # What a page can meet: a rule the lattice cannot read, a project that is gone, a damaged
        # table. Said in words. Anything else is a defect and is not hidden.
        return None, None, f"the words experiments carry could not be read: {type(error).__name__}"
    experiments = set(base.experiments)
    # A folder the lattice reads that the run places inside a larger experiment adds its terms to
    # that one's.
    folded: dict[str, set[str]] = {}
    for folder, terms in held.items():
        owner = base.units.get(folder)
        if owner is not None and owner in experiments:
            folded.setdefault(owner, set()).update(terms)
    carried = {experiment: frozenset(terms) for experiment, terms in sorted(folded.items())}
    return carried, mapping, None


def read_lines(project: Path, run_id: str) -> tuple[LineReading | None, str | None]:
    """The run's lines of work and broad lines from the themes projection, or why they could not
    be read, in words. Reached through the theme registry. Never raises for what a page meets."""
    from ingence.core.project import NotAProjectError
    from ingence.core.registry import ComponentNotAvailableError

    try:
        from ingence.themes import themes_engine
        from ingence.themes.lines import broad_lines_of, lines_of

        built = themes_engine().projection(project, run_id)
    except ComponentNotAvailableError as error:
        from ingence.theme_words import unavailable_in_words

        return None, unavailable_in_words(error)
    except (FileNotFoundError, NotAProjectError):
        return None, f"run {run_id} is not a published run of this project"
    except Exception as error:  # an optional projection: topics are answered without it
        return None, f"lines of work could not be read: {type(error).__name__}"
    return LineReading(lines=lines_of(built), broad=broad_lines_of(built)), None


def files_of(
    project: Path, view: RunView, experiments: Sequence[str], *, limit: int, offset: int
) -> list[dict[str, str]]:
    """A page of the files of the given experiments, in path order: the files whose highest
    experiment each is (the rule the Years view and completeness count by)."""
    import pyarrow as pa

    wanted = set(experiments)
    folders = sorted(folder for folder, owner in view.base.units.items() if owner in wanted)
    if not folders or limit <= 0:
        return []
    table = "topic_page_map"
    with open_catalog(project, run_id=view.base.run_id) as catalog:
        catalog.connection.register(
            table,
            pa.table(
                {
                    "folder_id": pa.array(folders, type=pa.string()),
                    "experiment_id": pa.array(
                        [view.base.units[folder] for folder in folders], type=pa.string()
                    ),
                }
            ),
        )
        try:
            rows = catalog.connection.execute(
                "SELECT e.entry_id, e.name, e.relative_path, u.experiment_id "  # noqa: S608
                f"FROM entries e JOIN {table} u ON u.folder_id = e.parent_entry_id "
                f"WHERE {LIVE_FILE} "
                "ORDER BY e.relative_path, e.entry_id LIMIT ? OFFSET ?",
                [limit, offset],
            ).fetchall()
        finally:
            catalog.connection.unregister(table)
    return [
        {
            "entry_id": str(entry),
            "name": str(name),
            "path": str(path or ""),
            "experiment": str(owner),
        }
        for entry, name, path, owner in rows
    ]


def load_view(
    project: Path,
    run_id: str,
    *,
    entries: Sequence[str] = (),
    cores: bool = False,
    lines: bool = False,
    goals: bool = False,
    earlier_ids: Mapping[str, tuple[str, ...]] | None = None,
    moved: Mapping[str, str] | None = None,
) -> RunView:
    """The run read for the members a topic holds: its experiments always; the entries named;
    the cores' words and the lines of work only when asked for (each costs a reading).

    ``earlier_ids`` (entry now to the identifiers it had before moving) and ``moved`` (the other
    way) are given for a view of an earlier run, so what moved is followed."""
    base = run_base(project, run_id)
    named = sorted({*entries, *(old for olds in (earlier_ids or {}).values() for old in olds)})
    view = RunView(
        base=base,
        entries=entry_infos(project, run_id, named),
        aliases=dict(earlier_ids or {}),
        moved=dict(moved or {}),
    )
    if cores:
        terms, ways, why = read_cores(project, run_id, base)
        view = replace(view, core_terms=terms, core_ways=ways, cores_why=why)
    if lines:
        reading, why = read_lines(project, run_id)
        view = replace(view, lines=reading, lines_why=why)
    if goals:
        from ingence.goals import goal_experiments

        found, goals_why = goal_experiments(project, run_id)
        view = replace(view, goals=None if goals_why else found, goals_why=goals_why)
    return view
