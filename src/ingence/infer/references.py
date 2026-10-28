"""A mentioned path read against the Index: resolved, outside the pool, or abstained with a reason.

Every rule of the resolution is here, in the order it applies:

1. A code include is never resolved against data. A ``#include`` or an ``import`` names code by a
   language's own lookup rules, which are not path rules. Resolving one against a data file that
   happens to share a name would be the worst false positive available.
2. Outside the pool is an answer, not a failure. A rooted path under no root of this pool names a
   place this Index does not hold, however often it recurs. That it might be this drive under
   another letter is a person's decision, never a guessed prefix mapping.
3. A rooted path under a root is looked for under that root, and only that root.
4. A relative path is looked for relative to the mentioning file's own folder first, then the
   pool's roots, never a working directory nobody recorded. ``..`` is applied lexically, as POSIX
   path syntax, never by following a link, and a path climbing out of its root is outside the pool.
   For a container member, its own folder is its folder inside the container, and then the
   container's folder.
5. A bare name resolves in its own folder only.
6. Several matches abstain, and say how many answered. The resolution never picks the nearest, the
   newest or the first.

A script's own file names are mentions with a direction. Each read or write call naming a file by
a literal name is recorded by the keyword extractor (``script.file_names``). These mentions are
resolved by the rules above for a rooted path, and otherwise within the script's own folder or
experiment. The search goes beside the script first (``in_folder``). It then goes by the file's
last name among the files of the experiment the script lies in (``by_name``, below the reference
rule's default floor, since a bare name is weak evidence). It never goes from a root of the pool or
by name across the pool. A script beneath no experiment looks in its own folder only.

Matching is on the stored spelling and never by opening anything: NFC, ``\\`` read as a separator,
and compared without case exactly where the root's filesystem ignores case. The Index is the whole
search space, so a resolution is reproducible from the Index alone.

The strength of a match is part of the answer. A match in the mentioning file's own folder, or a
rooted path matched exactly, is as strong as the observation that carried it. A match found only
from a root is circumstantial, because it depends on the user treating that root as the base.
Neither is a derivation: a mention is ``references``.
"""

from __future__ import annotations

import json
import posixpath
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.core.ids import CaseSensitivity
from ingence.core.schema import EntryKind, EntryType
from ingence.core.suffixes import is_extension
from ingence.extractors.mentions import (
    ABSOLUTE,
    BARE,
    CODE_INCLUDE,
    MARKUP,
    RELATIVE,
    lexical_shape,
)
from ingence.infer.script_outputs import READ, SCRIPT_FILE_NAMES_KEY, WRITTEN
from ingence.scan.mention_shapes import (
    ABSOLUTE_FOREIGN,
    ABSOLUTE_LOCAL,
    SHAPES_KEY,
    UNCLASSIFIED,
    PoolRoot,
    placed,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import Entry, Root

__all__ = [
    "ABSTENTIONS",
    "BY_NAME",
    "BY_PREFIX",
    "CODE_INCLUDE_NOT_RESOLVED",
    "FROM_ROOT",
    "IN_FOLDER",
    "NOT_A_PATH",
    "NO_MATCH",
    "OUTCOMES",
    "OUTSIDE_POOL",
    "POOL_COLUMNS",
    "PREFIX_TARGET",
    "REFERENCES_KEY",
    "RESOLVED",
    "ROOTED",
    "SCRIPT_FILE_NAMES_KEY",
    "SEVERAL_MATCHES",
    "Mention",
    "PoolPaths",
    "PrefixMapping",
    "Resolution",
    "map_foreign",
    "mentions_of",
    "prefix_mappings_in_force",
    "resolve",
    "resolve_all",
    "tally_resolutions",
]

#: The observation key a mention's paths are recorded under, beside :data:`SHAPES_KEY`.
REFERENCES_KEY = "references.paths"

#: The entry fields a pool is built from: the eight :class:`PoolPaths` and :func:`resolve` read,
#: the four more an entry record requires, and the two a container's member must carry to be one
#: (where it sits and what made it). A reader building a pool from the Index selects these and no
#: other columns of an entry. The stored column of a structured field is its name with ``_json``.
POOL_COLUMNS: tuple[str, ...] = (
    "entry_id",
    "root_id",
    "relative_path",
    "name",
    "parent_entry_id",
    "entry_kind",
    "entry_type",
    "is_deleted",
    "first_seen_run",
    "last_seen_run",
    "locator_chain",
    "transformation",
)

#: Resolved in the mentioning file's own folder (or, for a member, its container's).
IN_FOLDER = "in_folder"
#: A rooted path under a root of this pool, matched exactly under that root.
ROOTED = "rooted"
#: Found only from a root of the pool, which is circumstantial: it rests on the user treating
#: that root as the base.
FROM_ROOT = "from_root"
#: Found only by its last component's name, which one entry in the pool carries. This is a
#: different and weaker rule. A relative path whose folder moved still names its file, and a
#: workspace's references are mostly of that kind. The match is circumstantial at best, and a
#: rule's floor decides whether it counts.
BY_NAME = "by_name"
#: The ways a mention resolves, strongest first.
#: Another machine's path, under a prefix a person said is one of this pool's roots. It resolves as
#: a path under that root.
BY_PREFIX = "by_prefix"
RESOLVED = (IN_FOLDER, ROOTED, FROM_ROOT, BY_PREFIX, BY_NAME)

OUTSIDE_POOL = "outside_pool"
SEVERAL_MATCHES = "several_matches"
NO_MATCH = "no_match"
CODE_INCLUDE_NOT_RESOLVED = "code_include_not_resolved"
#: A stored mention that an earlier harvest took for a path and that is not one: a number after a
#: slash (``Comp-FL1-A/1.0000``, whose last suffix is not an extension by the census's rule) or
#: markup (``"/></a:b></"``, which no path is spelled with). It is a fifth abstention, beside the
#: four the resolution rules above give. Counted as ``no_match`` or ``outside_pool``, such mentions
#: would read as thousands of missing files and foreign paths.
NOT_A_PATH = "not_a_path"
#: The abstentions, each with one meaning: the four the resolution rules give, and ``not_a_path``.
ABSTENTIONS = (OUTSIDE_POOL, SEVERAL_MATCHES, NO_MATCH, CODE_INCLUDE_NOT_RESOLVED, NOT_A_PATH)
#: Every outcome, in the order a tally lists them.
OUTCOMES = (*RESOLVED, *ABSTENTIONS)


@dataclass(frozen=True, slots=True)
class Mention:
    """One path one extractor found mentioned in one entry, with its shape."""

    entry_id: str
    observer: str
    path: str
    #: The placed shape recorded beside it, or ``unclassified`` where an earlier engine recorded
    #: none. Such a mention is resolved by its spelling's shape and counted apart.
    shape: str
    #: The observation that recorded the path, which anything resting on the mention cites.
    observation_id: str = ""
    #: Whether a script's call reads or writes the file: ``read``, ``written``, or empty for a
    #: path found in a file's text, which says neither.
    direction: str = ""


@dataclass(frozen=True, slots=True)
class Resolution:
    """What one mention came to: an entry and how firmly, or an abstention and why."""

    mention: Mention
    outcome: str
    #: The entry the mention names, where it resolved.
    target_id: str | None = None
    #: How many entries answered, which a ``several_matches`` abstention names.
    answered: int = 0


def _spelled(path: str) -> str:
    """A mention as the comparison reads it: NFC, forward slashes, no trailing separator."""
    return unicodedata.normalize("NFC", path).replace("\\", "/").rstrip("/")


def _lexical(joined: str) -> str | None:
    """Apply ``.`` and ``..`` as POSIX path syntax; None where the path climbs out of its base."""
    parts: list[str] = []
    for part in joined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


@dataclass
class PoolPaths:
    """Every entry of a run, findable by its path under its root, and each member by its path
    inside its container.

    The pool is built once per inference from the entries the Index holds. It is keyed as the
    root's filesystem compares names: NFC always, and case folded exactly where the root's record
    says the filesystem ignores case. So UNKNOWN keeps two spellings apart, which is the direction
    that can be recovered from.
    """

    roots: Mapping[str, Root]
    #: What a person said other machines' prefixes are, longest first.
    prefixes: tuple[PrefixMapping, ...] = ()
    #: Each file's experiment folder, by entry identifier, where one lies above it. A script's bare
    #: file names are looked for by name there.
    experiments: Mapping[str, str] = field(default_factory=dict)
    _by_path: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    _by_name: dict[str, list[str]] = field(default_factory=dict)
    _inside: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    _entries: dict[str, Entry] = field(default_factory=dict)
    _insensitive: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        entries: Iterable[Entry],
        roots: Mapping[str, Root],
        prefixes: Iterable[PrefixMapping] = (),
        *,
        experiments: Mapping[str, str] | None = None,
    ) -> PoolPaths:
        """Return the pool's paths, from the run's entries and its roots' records."""
        pool = cls(
            roots=roots,
            prefixes=tuple(sorted(prefixes, key=lambda mapping: -len(mapping.foreign))),
            experiments=dict(experiments or {}),
        )
        pool._insensitive = {
            root_id: root.case_sensitivity is CaseSensitivity.INSENSITIVE
            for root_id, root in roots.items()
        }
        for entry in entries:
            if entry.is_deleted:
                continue
            pool._entries[entry.entry_id] = entry
            if entry.entry_kind is EntryKind.DERIVED and entry.parent_entry_id is not None:
                key = (entry.parent_entry_id, pool._key(entry.root_id, _spelled(entry.name)))
                pool._inside.setdefault(key, []).append(entry.entry_id)
                continue
            key = (entry.root_id, pool._key(entry.root_id, _spelled(entry.relative_path)))
            pool._by_path.setdefault(key, []).append(entry.entry_id)
            if entry.entry_type is not EntryType.DIRECTORY:
                named = pool._key(entry.root_id, unicodedata.normalize("NFC", entry.name))
                pool._by_name.setdefault(named, []).append(entry.entry_id)
        return pool

    def _key(self, root_id: str, path: str) -> str:
        return path.casefold() if self._insensitive.get(root_id, False) else path

    def entry(self, entry_id: str) -> Entry | None:
        return self._entries.get(entry_id)

    def under(self, root_id: str, relative: str) -> list[str]:
        """Return the entries at one path under one root: none, one, or several."""
        return sorted(self._by_path.get((root_id, self._key(root_id, relative)), ()))

    def named(self, name: str) -> list[str]:
        """Return every file in the pool carrying one name, wherever it is.

        Folded only where every root ignores case, so a name is never merged across a root that
        tells two spellings apart.
        """
        folded = bool(self._insensitive) and all(self._insensitive.values())
        return sorted(self._by_name.get(name.casefold() if folded else name, ()))

    def inside(self, container_id: str, inner: str) -> list[str]:
        """Return the members of one container at one path inside it."""
        container = self._entries.get(container_id)
        root_id = container.root_id if container is not None else ""
        return sorted(self._inside.get((container_id, self._key(root_id, inner)), ()))

    def named_within(self, name: str, folder_id: str, *, ending: str = "") -> list[str]:
        """Return every file carrying one name beneath one folder, under that folder's root, and
        whose path ends with ``ending`` (a name and the folders it lies in) where one is given."""
        folder = self._entries.get(folder_id)
        if folder is None:
            return []
        base = _spelled(folder.relative_path)
        prefix = "" if base in ("", ".") else self._key(folder.root_id, base) + "/"
        found = []
        for entry_id in self.named(name):
            entry = self._entries[entry_id]
            if entry.root_id != folder.root_id:
                continue
            path = self._key(entry.root_id, _spelled(entry.relative_path))
            if not path.startswith(prefix):
                continue
            tail = self._key(entry.root_id, ending) if ending else ""
            if tail and path != tail and not path.endswith("/" + tail):
                continue
            found.append(entry_id)
        return sorted(found)

    def pool_roots(self) -> tuple[tuple[str, PoolRoot], ...]:
        """Return each root with its spelling as a rooted mention is placed against it."""
        return tuple(
            (
                root_id,
                PoolRoot(
                    path=_spelled(root.mount_path),
                    case_insensitive=self._insensitive.get(root_id, False),
                ),
            )
            for root_id, root in sorted(self.roots.items())
            if root.mount_path
        )


@dataclass(frozen=True, slots=True)
class PrefixMapping:
    """A person's word that another machine's folder is one of this pool's roots.

    ``foreign`` is the other machine's prefix as a path spells it (a drive letter and folders, or a
    mounted volume's name and folders). ``root_id`` is the root it is. ``under`` is the folder
    inside the root that it names, where it is not the root itself.
    """

    foreign: str
    root_id: str
    under: str = ""


#: The target prefix of a person's prefix mapping in the Record.
PREFIX_TARGET = "prefix"


def _folded_path(path: str) -> str:
    """A path as a prefix is compared: forward slashes, no trailing one, case folded."""
    return path.replace("\\", "/").rstrip("/").casefold()


def map_foreign(path: str, prefixes: Sequence[PrefixMapping]) -> tuple[str, str] | None:
    """The root and the path under it that another machine's path names, by a person's mapping.

    Compared with forward slashes and case folded, because another machine's spelling of its own
    folders is what the mapping records, and a drive letter's case is nobody's decision. The
    longest mapping that holds the path wins. None where no mapping holds it, or where the rest
    climbs out of the root.
    """
    folded = _folded_path(path)
    written = path.replace("\\", "/")
    for mapping in prefixes:
        prefix = _folded_path(mapping.foreign)
        if folded != prefix and not folded.startswith(prefix + "/"):
            continue
        rest = written[len(prefix) :].lstrip("/")
        joined = f"{mapping.under.strip('/')}/{rest}" if mapping.under.strip("/") else rest
        relative = _lexical(joined)
        return None if relative is None else (mapping.root_id, relative)
    return None


def prefix_mappings_in_force(project: Path) -> tuple[PrefixMapping, ...]:
    """What a person said other machines' prefixes are, from the project's own Record.

    A ``define`` event about ``prefix:<the other machine's prefix>`` naming ``root_id`` (and
    ``under`` where it is a folder inside the root); the latest in force for each prefix.
    """
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import latest_by_target, read_events

    found: list[PrefixMapping] = []
    for target, event in sorted(latest_by_target(read_events(project)).items()):
        if not target.startswith(f"{PREFIX_TARGET}:") or event.kind is not RecordEventKind.DEFINE:
            continue
        said = json.loads(event.payload) if isinstance(event.payload, str) else dict(event.payload)
        if said.get("root_id"):
            found.append(
                PrefixMapping(
                    foreign=target.split(":", 1)[1],
                    root_id=str(said["root_id"]),
                    under=str(said.get("under") or ""),
                )
            )
    return tuple(found)


def mentions_of(
    observations: Iterable[tuple[str, str, str, str, str | None]],
) -> tuple[Mention, ...]:
    """Return every mention a run's observations hold, in canonical order.

    ``observations`` holds ``(observation_id, entry_id, observer, key, value_structure)`` for
    the two keys a mention is recorded under. A path is one mention per entry and observer; its
    shape is the one recorded beside it, or ``unclassified`` where none was.
    """
    paths: dict[tuple[str, str], list[str]] = {}
    carried_by: dict[tuple[str, str], str] = {}
    shapes: dict[tuple[str, str], dict[str, str]] = {}
    called: list[Mention] = []
    for observation_id, entry_id, observer, key, value in observations:
        if not value:
            continue
        if key == REFERENCES_KEY:
            paths[(entry_id, observer)] = list(json.loads(value))
            carried_by[(entry_id, observer)] = observation_id
        elif key == SHAPES_KEY:
            shapes[(entry_id, observer)] = dict(json.loads(value))
        elif key == SCRIPT_FILE_NAMES_KEY:
            named = dict(json.loads(value))
            called.extend(
                Mention(
                    entry_id=entry_id,
                    observer=observer,
                    path=str(path),
                    shape=_called_shape(str(path)),
                    observation_id=observation_id,
                    direction=direction,
                )
                for direction in (READ, WRITTEN)
                for path in sorted(set(named.get(direction, ())))
            )
    found = [
        Mention(
            entry_id=entry_id,
            observer=observer,
            path=path,
            shape=shapes.get((entry_id, observer), {}).get(path, UNCLASSIFIED),
            observation_id=carried_by[(entry_id, observer)],
        )
        for (entry_id, observer), held in sorted(paths.items())
        for path in sorted(set(held))
    ]
    return tuple(
        sorted(
            (*found, *called),
            key=lambda mention: (
                mention.entry_id,
                mention.observer,
                mention.direction,
                mention.path,
            ),
        )
    )


def _called_shape(path: str) -> str:
    """A called name's shape by its spelling; a rooted one is placed against the pool later."""
    shape = lexical_shape(path)
    return UNCLASSIFIED if shape == ABSOLUTE else shape


def _answer(mention: Mention, found: Sequence[str], outcome: str) -> Resolution | None:
    """Return the resolution one lookup gives, or None where it found nothing."""
    if not found:
        return None
    if len(found) > 1:
        return Resolution(mention=mention, outcome=SEVERAL_MATCHES, answered=len(found))
    return Resolution(mention=mention, outcome=outcome, target_id=found[0], answered=1)


#: What a lookup beside the mentioning file found when the path climbed out of its root.
_CLIMBED_OUT: tuple[str, ...] = ("..",)


def _beside(pool: PoolPaths, entry: Entry, spelled: str) -> tuple[str, ...]:
    """Return the entries a relative spelling names beside the mentioning file.

    A member looks in its own folder inside its container first, then in its container's
    folder on disk. Empty where nothing answered, and :data:`_CLIMBED_OUT` where the path
    left its root, which is outside the pool rather than unmatched.
    """
    if entry.entry_kind is EntryKind.DERIVED and entry.parent_entry_id is not None:
        inner = _lexical(posixpath.join(posixpath.dirname(_spelled(entry.name)), spelled))
        if inner is not None:
            found = pool.inside(entry.parent_entry_id, inner)
            if found:
                return tuple(found)
        container = pool.entry(entry.parent_entry_id)
        if container is None:
            return ()
        entry = container
    folder = posixpath.dirname(_spelled(entry.relative_path))
    relative = _lexical(posixpath.join(folder, spelled))
    if relative is None:
        return _CLIMBED_OUT
    return tuple(pool.under(entry.root_id, relative))


def resolve(mention: Mention, pool: PoolPaths) -> Resolution:
    """Return what one mention comes to, by the rules in their order."""
    entry = pool.entry(mention.entry_id)
    if entry is None:
        return Resolution(mention=mention, outcome=NO_MATCH)
    shape = mention.shape
    roots = pool.pool_roots()
    if shape == UNCLASSIFIED:
        shape = placed(mention.path, lexical_shape(mention.path), [root for _, root in roots])
    if shape == CODE_INCLUDE:
        return Resolution(mention=mention, outcome=CODE_INCLUDE_NOT_RESOLVED)
    if MARKUP & set(mention.path):
        return Resolution(mention=mention, outcome=NOT_A_PATH)
    if shape == ABSOLUTE_FOREIGN:
        mapped = map_foreign(mention.path, pool.prefixes)
        if mapped is not None:
            mapped_root, under_it = mapped
            by_prefix = _answer(mention, pool.under(mapped_root, under_it), BY_PREFIX)
            return by_prefix or Resolution(mention=mention, outcome=NO_MATCH)
        return Resolution(mention=mention, outcome=OUTSIDE_POOL)
    spelled = _spelled(mention.path)
    if shape == ABSOLUTE_LOCAL:
        rooted = spelled if spelled.startswith("/") else "/" + spelled
        for root_id, root in roots:
            left, right = (
                (rooted.casefold(), root.path.casefold())
                if root.case_insensitive
                else (rooted, root.path)
            )
            if left == right or left.startswith(right + "/"):
                relative = _lexical(rooted[len(root.path) :])
                if relative is None:
                    return Resolution(mention=mention, outcome=OUTSIDE_POOL)
                answered = _answer(mention, pool.under(root_id, relative), ROOTED)
                return answered or Resolution(mention=mention, outcome=NO_MATCH)
        return Resolution(mention=mention, outcome=OUTSIDE_POOL)
    if shape not in (RELATIVE, BARE):
        return Resolution(mention=mention, outcome=NO_MATCH)
    last = spelled.rsplit("/", 1)[-1]
    if "." in last and not is_extension(last.rsplit(".", 1)[-1]):
        return Resolution(mention=mention, outcome=NOT_A_PATH)
    local = _beside(pool, entry, spelled)
    if local == _CLIMBED_OUT:
        return Resolution(mention=mention, outcome=OUTSIDE_POOL)
    answered_here = _answer(mention, local, IN_FOLDER)
    if answered_here is not None:
        return answered_here
    if mention.direction:
        return _in_experiment(mention, spelled, entry, pool)
    if shape == BARE:
        return _by_name(mention, spelled, pool)
    found: set[str] = set()
    for root_id, _root in roots:
        relative = _lexical(spelled)
        if relative is None:
            continue
        found.update(pool.under(root_id, relative))
    answered = _answer(mention, sorted(found), FROM_ROOT)
    return answered or _by_name(mention, spelled, pool)


def _in_experiment(mention: Mention, spelled: str, entry: Entry, pool: PoolPaths) -> Resolution:
    """A script's called name, found by its last name among its own experiment's files.

    The result is ``by_name``, the weakest rung, where exactly one file of the experiment carries
    the name. It is nothing where the script lies beneath no experiment, whose own folder was the
    only place to look.
    """
    experiment = pool.experiments.get(entry.entry_id)
    named = [part for part in spelled.split("/") if part not in ("", ".", "..")]
    if experiment is None or not named:
        return Resolution(mention=mention, outcome=NO_MATCH)
    # Where the path names folders, the file found lies under them:
    # ``../../vorn_b/kelt_counts.csv`` names no ``kelt_counts.csv`` elsewhere in the experiment.
    found = pool.named_within(named[-1], experiment, ending="/".join(named))
    answered = _answer(mention, found, BY_NAME)
    return answered or Resolution(mention=mention, outcome=NO_MATCH)


def _by_name(mention: Mention, spelled: str, pool: PoolPaths) -> Resolution:
    """The weakest rung: the file the pool holds under the path's last name, if exactly one."""
    last = spelled.rsplit("/", 1)[-1]
    answered = _answer(mention, pool.named(last), BY_NAME) if last else None
    return answered or Resolution(mention=mention, outcome=NO_MATCH)


def resolve_all(mentions: Iterable[Mention], pool: PoolPaths) -> tuple[Resolution, ...]:
    """Return every mention's resolution, in the mentions' canonical order."""
    return tuple(resolve(mention, pool) for mention in mentions)


def tally_resolutions(
    resolutions: Iterable[Resolution], extension_of: Mapping[str, str]
) -> tuple[tuple[str, str, str, int, int], ...]:
    """Return resolutions counted by the mentioning file's extension, the shape and the outcome.

    Each row is ``(extension, shape, outcome, mentions, files)``, canonically ordered, so two
    runs' tallies compare and a run report has the figure without a query.
    """
    counted: Counter[tuple[str, str, str]] = Counter()
    files: dict[tuple[str, str, str], set[str]] = {}
    for resolution in resolutions:
        mention = resolution.mention
        key = (extension_of.get(mention.entry_id, ""), mention.shape, resolution.outcome)
        counted[key] += 1
        files.setdefault(key, set()).add(mention.entry_id)
    return tuple(
        (extension, shape, outcome, counted[(extension, shape, outcome)], len(files[key]))
        for key in sorted(counted)
        for extension, shape, outcome in (key,)
    )
