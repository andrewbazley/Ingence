"""Which experiments belong together: a deterministic clustering that proposes and never concludes.

The question is "do these two experiments go together", at a higher scale than replicate files.
Automatic grouping proposes named groups, and it should not produce hundreds of them. The
proposal starts at the highest level where a group appears. Everything under that level inherits
approval as grouped if a person approves the top level as a group. Groups do not have to
distinguish experimental conditions within an experiment, only between experiments. Groups
require organisational and positional context.

The clustering reads one row per experiment (a folder that a rule concluded on) and, for each
experiment, a set of features. The features are:

- the words its files' names use, counted once per experiment (a folder of five thousand frames
  says ``tif`` once);
- the words of the experiment folder's own name, as a feature of their own kind (a folder's name
  counts);
- the names of the files it holds, so two experiments holding the same files (a backup, a copy, a
  reanalysis) share them;
- the folder it sits in, as positional context (two experiments in one month's folder are
  neighbours).

The clustering weighs each feature by how few experiments hold it (inverse document frequency,
smoothed). A word that two experiments share weighs most, because a rare shared word is what
links experiments. A feature held by one experiment links nothing and is left
out of its vector. A feature held by more than ``common_share`` of the experiments is cut and not
weighed, because it is everywhere and would enumerate every pair. The cut never applies to a
feature held by no more experiments than one experiment's neighbourhood (itself and its
``neighbours`` closest). In a pool of nine, the share alone would cut what three days of one
condition share, although the same days in a pool of thirty-six survive. Without the exception, a
small pool would never propose its replicate days.

The clustering groups as follows. It takes the cosine similarity between experiments. An edge
exists where each experiment is among the other's ``neighbours`` closest and their similarity
reaches ``minimum_similarity``. The groups are the connected components. A component larger than
``maximum_group_size`` is cut again at a higher similarity until none is. There is no random
restart and no seed. One census gives one set of groups, and every edge can be shown with the
features it rests on, of which there must be at least ``minimum_shared``.

The clustering gives every proposed group, largest and tightest first, each named by the
features most of its members share, rarest first. The number of groups is not capped by default.
The Dashboard pages them and shows no more than 50 at once. ``maximum_groups`` stays a switch, and
the function returns the count of groups beyond a cap, so nothing is silently dropped where one is
set. A proposal is a question and never a claim. A person who approves a proposal at the
experiments' level approves everything beneath each member, and a rule turns that into a
grouping."""

from __future__ import annotations

import math
import re
from bisect import bisect_right, insort
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.grouping.identifiers import masked

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "DEFAULT_POLICY",
    "POLICIES",
    "ProposedGroup",
    "experiment_rows",
    "experiment_units",
    "propose_groups",
    "similarities",
]

#: How the folders two experiment rules concluded on become one set of experiments.
POLICIES = ("outermost_evidence", "structure_then_name", "name_first")

#: A name comes first, better a dated name, and then what the folder holds. On a set of
#: five folders, measured against a known answer written from names, ``name_first`` was
#: strictly best on three, tied on one and behind on one. ``outermost_evidence`` stays a choice.
DEFAULT_POLICY = "name_first"


def _above(path: str) -> list[str]:
    """Every folder strictly above a path: ``a/b/c`` lies under ``a`` and ``a/b``."""
    return [path[:index] for index, character in enumerate(path) if character == "/"]


def _beneath_any(paths: Iterable[str]) -> set[str]:
    """Every path some path lies strictly beneath."""
    return {above for path in paths for above in _above(path)}


def _outermost(paths: set[str]) -> set[str]:
    """The paths no other of them lies above.

    The function walks each path's own folders. Comparing every path with every other grows with
    the square of the number of experiments on a generated pool, whereas a path has only as many
    folders as its depth.
    """
    return {path for path in paths if not any(above in paths for above in _above(path))}


def experiment_units(
    structure: Mapping[str, str], named: Iterable[str], *, policy: str = DEFAULT_POLICY
) -> list[str]:
    """The experiments, from the folders the structure rule and the name rule concluded on.

    ``structure`` maps a folder's path to what the structure rule concluded (``experiment``,
    ``acquisitions`` or ``analysis``); ``named`` is the dated-name rule's folders.

    - ``structure_then_name``: the structure rule's folders, and a named folder wherever none of
      them lies on its path. Where the two overlap, what a folder holds wins over its name.
    - ``outermost_evidence``: on each path, the outermost folder either rule concluded on, except
      a named folder holding two or more complete experiments by structure. Such a folder holds
      experiments and is not one itself (a dated backup of many). Structure still speaks first
      where names are silent or misleading. A dated title above raw data split into one folder per
      condition binds that data, which structure alone cannot see because the condition folders
      share no file names.
    - ``name_first`` (the default): every named folder, and elsewhere the outermost structure
      folder on each path that no named folder lies on. A known answer written from names
      judges this policy kindly, because the answer rests on the same names.
    """
    if policy not in POLICIES:
        message = f"no experiment policy {policy!r}; the policies are {', '.join(POLICIES)}"
        raise ValueError(message)
    names = set(named)
    if policy == "structure_then_name":
        placed = set(structure)
        holding = _beneath_any(placed)
        kept = set(placed)
        kept |= {
            n
            for n in names
            if n not in placed and n not in holding and not any(a in placed for a in _above(n))
        }
        return sorted(kept)
    if policy == "name_first":
        holding = _beneath_any(names)
        rest = {
            s
            for s in structure
            if s not in names and s not in holding and not any(a in names for a in _above(s))
        }
        return sorted(names | _outermost(rest))
    complete = [path for path, kind in structure.items() if kind == "experiment"]
    beneath: dict[str, int] = defaultdict(int)
    for path in complete:
        for above in _above(path):
            beneath[above] += 1
    containers = {n for n in names if beneath.get(n, 0) >= 2}
    evidence = (set(structure) | names) - containers
    return sorted(_outermost(evidence))


_NOT_ALPHANUMERIC = re.compile(r"[^0-9a-z]+")
_LETTER = re.compile(r"[a-z]")


def experiment_rows(
    experiments: Mapping[str, tuple[Iterable[str], str]],
    files: Iterable[tuple[str, Iterable[str], str]],
    *,
    minimum_word_characters: int = 3,
    minimum_name_characters: int = 8,
    dates_of: Mapping[str, Sequence[str]] | None = None,
    replicate_labels: Iterable[str] = (),
) -> dict[str, frozenset[str]]:
    """Each experiment's features, from its folder's name words and parent and its files' names.

    ``experiments`` maps a key to the words of the folder's own name and the path of the folder it
    sits in; ``files`` gives, for each file beneath an experiment, its experiment's key, the words
    its name was cut into, and its name. Features are prefixed by kind -- ``w:`` a word in a file's
    name, ``f:`` a word in the experiment folder's name, ``n:`` a file's whole name without its
    extension and without what it counts (letters and digits only; ``dates_of`` gives a name's
    dates as the tokeniser stored them, ``replicate_labels`` the labels the run binds to the
    replicate kind), ``p:`` the folder the experiment sits in -- so a word in a
    folder's name and the same word in a file's name are two pieces of evidence.
    """
    rows: dict[str, set[str]] = {}
    for key, (words, parent) in experiments.items():
        features = {f"f:{w}" for w in words if len(w) >= minimum_word_characters}
        features.add(f"p:{parent}")
        rows[key] = features
    for key, words, name in files:
        if key not in rows:
            continue
        found = rows[key]
        found.update(f"w:{w}" for w in words if len(w) >= minimum_word_characters)
        # Never what the name counts. The one classifier leaves out a name's dates, clocks and
        # counters, so two experiments holding ``img_001`` and ``img_002`` share no name for it.
        # Groups never rest on dates or replicate numbers.
        counted = masked(
            name,
            marks=False,
            dates=(dates_of or {}).get(name),
            replicate_labels=replicate_labels,
        )
        stem = counted.rsplit(".", 1)[0] if "." in counted else counted
        joined = _NOT_ALPHANUMERIC.sub("", stem)
        if len(joined) >= minimum_name_characters and _LETTER.search(joined):
            found.add(f"n:{joined}")
    return {key: frozenset(features) for key, features in rows.items()}


@dataclass(frozen=True, slots=True)
class ProposedGroup:
    """Experiments proposed to belong together, with what names the group and what links it."""

    #: The experiments, by the key each row was given, in canonical order.
    members: tuple[str, ...]
    #: The features most members share, rarest first: what the group is called.
    name: tuple[str, ...]
    #: Every edge inside the group: its two ends, their similarity, and the features they share.
    edges: tuple[tuple[str, str, float, tuple[str, ...]], ...]

    @property
    def tightness(self) -> float:
        """The mean similarity of the group's edges."""
        return sum(edge[2] for edge in self.edges) / len(self.edges) if self.edges else 0.0


def _weights(
    rows: Mapping[str, frozenset[str]], common_share: float, floor: int = 2
) -> dict[str, float]:
    held: dict[str, int] = defaultdict(int)
    for features in rows.values():
        for feature in features:
            held[feature] += 1
    count = len(rows)
    ceiling = max(floor, int(common_share * count))
    return {
        feature: math.log((1 + count) / (1 + times)) + 1.0
        for feature, times in held.items()
        if 2 <= times <= ceiling
    }


def similarities(
    rows: Mapping[str, frozenset[str]], *, common_share: float = 0.25, floor: int = 2
) -> dict[tuple[str, str], tuple[float, tuple[str, ...]]]:
    """Cosine similarity of every pair of rows sharing a weighted feature, with what they share.

    A feature held by more than ``common_share`` of the rows, and by more than ``floor`` of them,
    is left out of the pairs it would make (it is everywhere), which keeps the pairs enumerated to
    those something rare links.
    """
    weight = _weights(rows, common_share, floor)
    norms = {
        key: math.sqrt(sum(weight[f] ** 2 for f in features if f in weight))
        for key, features in rows.items()
    }
    holders: dict[str, list[str]] = defaultdict(list)
    for key in sorted(rows):
        for feature in rows[key]:
            if feature in weight:
                holders[feature].append(key)
    shared: dict[tuple[str, str], list[str]] = defaultdict(list)
    for feature, keys in holders.items():
        for index, first in enumerate(keys):
            for second in keys[index + 1 :]:
                shared[(first, second)].append(feature)
    result: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
    for pair, features in shared.items():
        dot = sum(weight[f] ** 2 for f in features)
        denominator = norms[pair[0]] * norms[pair[1]]
        if denominator:
            ordered = tuple(sorted(features, key=lambda f: (-weight[f], f)))
            result[pair] = (dot / denominator, ordered)
    return result


def _components(members: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    parent = {member: member for member in members}

    def root(member: str) -> str:
        while parent[member] != member:
            parent[member] = parent[parent[member]]
            member = parent[member]
        return member

    for first, second in edges:
        a, b = root(first), root(second)
        if a != b:
            parent[max(a, b)] = min(a, b)
    grouped: dict[str, list[str]] = defaultdict(list)
    for member in members:
        grouped[root(member)].append(member)
    return [sorted(group) for group in grouped.values()]


def _distinct(features: Iterable[str]) -> set[str]:
    """What features say, whatever their kind: a word in a folder's name and in a file's is one.

    An edge rests on ``minimum_shared`` different things. Counting by kind would let one word that
    both folders and their files carry pass a minimum meant to refuse one word in common. That
    would link a folder of one reagent's salts to that reagent's droplets once the common-word cut
    stopped hiding the word in a small pool.
    """
    # A feature written without its kind (a caller's own words) is what it says.
    return {feature.split(":", 1)[-1] for feature in features}


def _mutual_closest(
    rows: Mapping[str, frozenset[str]],
    *,
    neighbours: int,
    common_share: float,
    minimum_shared: int,
    floor: int = 2,
) -> dict[tuple[str, str], tuple[float, tuple[str, ...]]]:
    """The pairs whose ends are each among the other's closest, with their similarity and features.

    The function holds only each experiment's closest few. The proposal reads the edges both ends
    agree on, and holding every pair that shares a weighted feature grows with the square of the
    number of experiments. On generated pools, a few thousand experiments gave millions of pairs
    and gigabytes of memory. The function still scores each pair once, exactly as
    :func:`similarities` scores it, with the features it shares summed in the same order, so every
    similarity is the same number. It keeps a pair only while the pair stands among either end's
    ``neighbours`` closest, with ties broken by name.
    """
    weight = _weights(rows, common_share, floor)
    norms = {
        key: math.sqrt(sum(weight[f] ** 2 for f in features if f in weight))
        for key, features in rows.items()
    }
    holders: dict[str, list[str]] = defaultdict(list)
    for key in sorted(rows):
        for feature in rows[key]:
            if feature in weight:
                holders[feature].append(key)
    order = {feature: index for index, feature in enumerate(holders)}
    closest: dict[str, list[tuple[float, str]]] = defaultdict(list)

    def keep(key: str, similarity: float, other: str) -> None:
        held = closest[key]
        insort(held, (-similarity, other))
        if len(held) > neighbours:
            held.pop()

    for key in sorted(rows):
        own = sorted((f for f in rows[key] if f in weight), key=order.__getitem__)
        shared: dict[str, list[str]] = defaultdict(list)
        for feature in own:
            keys = holders[feature]
            for other in keys[bisect_right(keys, key) :]:
                shared[other].append(feature)
        for other, features in shared.items():
            if len(_distinct(features)) < minimum_shared:
                continue
            denominator = norms[key] * norms[other]
            if not denominator:
                continue
            similarity = sum(weight[f] ** 2 for f in features) / denominator
            keep(key, similarity, other)
            keep(other, similarity, key)
    top = {key: {other for _, other in held} for key, held in closest.items()}
    mutual: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
    for key in sorted(top):
        for other in sorted(top[key]):
            if other <= key or key not in top.get(other, set()):
                continue
            features = sorted(
                (f for f in rows[key] if f in weight and f in rows[other]), key=order.__getitem__
            )
            similarity = sum(weight[f] ** 2 for f in features) / (norms[key] * norms[other])
            mutual[(key, other)] = (
                similarity,
                tuple(sorted(features, key=lambda f: (-weight[f], f))),
            )
    return mutual


def propose_groups(
    rows: Mapping[str, frozenset[str]],
    *,
    neighbours: int = 3,
    minimum_similarity: float = 0.2,
    maximum_group_size: int = 8,
    maximum_groups: int | None = None,
    common_share: float = 0.25,
    minimum_shared: int = 2,
) -> tuple[list[ProposedGroup], int]:
    """Return the proposed groups, largest and tightest first, and how many were beyond the cap.

    An edge rests on at least ``minimum_shared`` features that the two experiments share, counted
    by what they say (a word in a folder's name and in a file's is one). One word in common, such
    as two folders both called a test, is not evidence that two experiments belong together.
    """
    # One experiment's neighbourhood is never common: a feature held by no more experiments than
    # an experiment and its closest can hold is what a group shares. Only each experiment's closest
    # few are held, which gives the same mutual edges in bounded memory.
    floor = neighbours + 1
    mutual = _mutual_closest(
        rows,
        neighbours=neighbours,
        common_share=common_share,
        minimum_shared=minimum_shared,
        floor=floor,
    )

    def cut(members: list[str], floor: float) -> list[list[str]]:
        """Components of the mutual edges at or above ``floor``; a large one cut again higher."""
        inside = set(members)
        edges = [
            pair
            for pair, (similarity, _) in mutual.items()
            if similarity >= floor and pair[0] in inside and pair[1] in inside
        ]
        result: list[list[str]] = []
        for group in _components(members, edges):
            if len(group) < 2:
                continue
            if len(group) > maximum_group_size and floor < 1.0:
                result.extend(cut(group, min(1.0, floor * 1.25 + 0.01)))
            else:
                result.append(group)
        return result

    groups = cut(sorted(rows), minimum_similarity)
    proposed: list[ProposedGroup] = []
    weight = _weights(rows, common_share, floor)
    for group in groups:
        inside = set(group)
        edges = tuple(
            (pair[0], pair[1], round(similarity, 4), features[:6])
            for pair, (similarity, features) in sorted(mutual.items())
            if pair[0] in inside and pair[1] in inside
        )
        counts: dict[str, int] = defaultdict(int)
        for member in group:
            for feature in rows[member]:
                if feature in weight:
                    counts[feature] += 1
        half = max(2, math.ceil(len(group) / 2))
        naming = sorted(
            (feature for feature, times in counts.items() if times >= half),
            key=lambda feature: (-weight[feature], feature),
        )
        proposed.append(ProposedGroup(members=tuple(group), name=tuple(naming[:4]), edges=edges))
    proposed.sort(key=lambda g: (-len(g.members), -g.tightness, g.members))
    if not maximum_groups:
        return proposed, 0
    return proposed[:maximum_groups], max(0, len(proposed) - maximum_groups)
