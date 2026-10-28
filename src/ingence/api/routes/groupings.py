"""The Groupings page's levels: experiments first, then what lies inside each.

Grouping has levels. Automatic grouping proposes named groups, and a person answers at the
highest level where a group appears. Everything under that level inherits the approval as grouped
if the top level is approved as a group. Groups distinguish experiments, not conditions within
one. Fine details, such as groups of analysis files nested in folders, need less attention than
the decision whether whole experiments are grouped together.

So the page reads three things from a run, and this module supplies the first:

1. The experiments (:func:`experiment_folders`): the folders that the two experiment rules
   concluded on. The clustering rule's own ``policy``
   (:func:`~ingence.grouping.experiment_links.experiment_units`) makes them one set. Each file is
   placed in the experiment whose folder holds it (:meth:`Experiments.of`).
2. The proposed groups of experiments. The clustering rule raises, on each member experiment's
   folder, the name of the group it is proposed into. So a group's members are the folders that
   raise one value, and the Review queue asks each value once (``pages``). The finding's own
   reason says what links them (:func:`shared_by`).
3. The groups that a grouping rule formed. The page asks about them only where no experiment
   holds them. Inside an experiment the page shows them under it, for information
   (``pages._class_rows``).

Nothing here writes, and nothing concludes. This module reads what the rules concluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.experiment_units import (
    EXPERIMENT_GROUPS_PREDICATE,
    policy_experiments,
    rule_parameters,
)

if TYPE_CHECKING:
    from ingence.storage.catalog import Catalog

__all__ = [
    "EXPERIMENT_GROUPS_PREDICATE",
    "Experiments",
    "experiment_folders",
    "rule_parameters",
    "shared_by",
]


@dataclass
class Experiments:
    """The run's experiments, by their folders' entries, and which experiment holds a given path.

    The experiments are keyed by entry identifier, and the path is kept beside each for showing.
    Every projection that counts experiments keys them the same way
    (:func:`~ingence.experiment_units.policy_experiments`).
    """

    #: entry id -> {"entry_id", "path", "name", "kind"}: ``kind`` is what the structure rule
    #: concluded (``experiment``, ``acquisitions``, ``analysis``), or ``named`` for the
    #: dated-name rule's. ``path`` the folder below its root.
    folders: dict[str, dict[str, str]] = field(default_factory=dict)
    #: ``by_path`` as last made, with the folders it was made from. :meth:`of` is asked once per
    #: file of every set on the Groupings page. Making the map on every call would make that page's
    #: build grow with files times experiments (minutes on a large run).
    _paths: tuple[dict[str, dict[str, str]], int, dict[str, str]] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def by_path(self) -> dict[str, str]:
        """Each experiment folder's path to its entry identifier (made once while the folders
        stay the same mapping of the same size)."""
        held = self._paths
        if held is None or held[0] is not self.folders or held[1] != len(self.folders):
            made = {item["path"]: entry_id for entry_id, item in self.folders.items()}
            self._paths = held = (self.folders, len(self.folders), made)
        return held[2]

    def of(self, path: str) -> str | None:
        """The experiment holding ``path``, by its entry: itself where it is one, else the
        nearest experiment folder above it; None where none holds it."""
        paths = self.by_path
        parts = [part for part in str(path).split("/") if part]
        for depth in range(len(parts), 0, -1):
            candidate = "/".join(parts[:depth])
            if candidate in paths:
                return paths[candidate]
        return None

    def path_of(self, entry_id: str | None) -> str | None:
        """An experiment's folder below its root, for showing; None where it is no experiment."""
        if entry_id is None:
            return None
        held = self.folders.get(entry_id)
        return held["path"] if held is not None else None


def experiment_folders(catalog: Catalog) -> Experiments:
    """The folders the two experiment rules concluded on, made one set by the policy in force.

    The set the themes and the completeness figures count as well
    (:func:`~ingence.experiment_units.run_units`).
    """
    return Experiments(folders=policy_experiments(catalog))


def shared_by(reason: str) -> list[str]:
    """What links a proposed group, from its finding's reason: "... sharing a, b: ...".

    The clustering rule writes which features name the group into each member's reason; a reason
    in any other shape says nothing here rather than something guessed.
    """
    _, found, rest = str(reason or "").partition(" sharing ")
    if not found:
        return []
    named, colon, _ = rest.partition(": ")
    if not colon:
        return []
    return [word.strip() for word in named.split(", ") if word.strip() and word.strip() != "files"]
