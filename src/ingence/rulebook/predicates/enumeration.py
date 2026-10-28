"""Predicates for the scan layer's own rules: what enumeration and expansion establish.

The census and the cascade call these predicates where they compute the fact. The inference pass
never calls them. Each predicate is handed what the scan layer found, in the form that
:attr:`~ingence.rulebook.registry.Reads.ENUMERATION` names: a directory's listing or its
counts, an object's mode, or a member recovered from a container.

A subject of the wrong type is refused and not abstained on. Abstaining would drop a containment
edge or a count without a trace, and the census could not tell a verdict of nothing from a
defect in what it handed over.
"""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

from ingence.core.relations import ExpansionState
from ingence.core.schema import RuleEffect
from ingence.infer.subjects import DirectoryCounts, Listing
from ingence.rulebook.registry import DirectoryTotals, Embedding, Predicate, Reads, declare

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ContainsWhatEnumerationListed",
    "EmbedsWhatExpansionRecovered",
    "ObjectKindFromMode",
    "TotalsFromEnumeration",
]


def _refused(name: str, wanted: str, subject: object) -> str:
    return f"{name} is handed {wanted}, and was handed {type(subject).__name__}"


class ContainsWhatEnumerationListed:
    """A directory contains every entry enumeration listed directly inside it."""

    name = "contains_what_enumeration_listed"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> tuple[str, ...]:
        """Return every entry the listing holds, in canonical order, and never the directory.

        Enumeration either found an entry inside the directory or it did not, so there is
        nothing to weigh: the verdict is the listing, less a directory listed inside itself,
        which no filesystem returns and no edge may draw.
        """
        del parameters
        if not isinstance(subject, Listing):
            raise TypeError(_refused(self.name, "a directory's listing", subject))
        return tuple(sorted({entry for entry in subject.entries if entry != subject.directory}))


class TotalsFromEnumeration:
    """A directory holds what was counted inside it, and everything its subdirectories hold."""

    name = "totals_from_enumeration"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> DirectoryTotals:
        """Return one directory's direct counts and its totals beneath it.

        Legitimate only because ``contains`` is observationally complete once enumeration
        finishes: a subdirectory absent from the counts is not there, rather than unread.
        """
        del parameters
        if not isinstance(subject, DirectoryCounts):
            raise TypeError(_refused(self.name, "a directory's counts", subject))
        children = subject.children
        return DirectoryTotals(
            direct_file_count=subject.files,
            direct_directory_count=subject.directories,
            recursive_file_count=subject.files
            + sum(child.recursive_file_count for child in children),
            recursive_directory_count=subject.directories
            + sum(child.recursive_directory_count for child in children),
            recursive_size_bytes=subject.size_bytes
            + sum(child.recursive_size_bytes for child in children),
        )


#: The kinds of object the schema records as ``other``, by the ``stat`` test naming each. A
#: test this platform's ``stat`` module lacks is skipped rather than assumed false.
_OBJECT_KINDS: tuple[tuple[str, str], ...] = (
    ("S_ISFIFO", "fifo"),
    ("S_ISSOCK", "socket"),
    ("S_ISCHR", "character_device"),
    ("S_ISBLK", "block_device"),
    ("S_ISDOOR", "door"),
)


class ObjectKindFromMode:
    """The precise kind of an object that is not a file, a directory or a link, from its mode."""

    name = "object_kind_from_mode"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> str | None:
        """Return the kind a mode names, or None for a file, a directory or a link.

        Those three are the schema's own entry types and need no reading beside them. A mode
        no test names is ``unknown``, recorded rather than dropped.
        """
        del parameters
        if isinstance(subject, bool) or not isinstance(subject, int):
            raise TypeError(_refused(self.name, "an object's mode", subject))
        if stat.S_ISLNK(subject) or stat.S_ISDIR(subject) or stat.S_ISREG(subject):
            return None
        for test_name, kind in _OBJECT_KINDS:
            test = getattr(stat, test_name, None)
            if test is not None and test(subject):
                return kind
        return "unknown"


#: The narrower relation the catalog names for each kind of container a member came out of.
_SPECIALIZATIONS: dict[str, str] = {
    "zip": "archive_member",
    "tar": "archive_member",
    "gzip": "stream_of",
    "bzip2": "stream_of",
    "xz": "stream_of",
}


class EmbedsWhatExpansionRecovered:
    """A container embeds each member opening it recovered, and says why expansion stopped."""

    name = "embeds_what_expansion_recovered"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> Embedding:
        """Return how the container holds one recovered member.

        A member either came out of the container or it did not, so the edge is entailed.
        What varies is the catalog's name for the kind of container, where it has one, and
        the state expansion stopped in, which is complete where the extractor stated none.
        """
        del parameters
        transformation = getattr(subject, "transformation", None)
        if not isinstance(transformation, str):
            raise TypeError(_refused(self.name, "a member recovered from a container", subject))
        stated = getattr(subject, "expansion_state", None)
        return Embedding(
            specialization=_SPECIALIZATIONS.get(transformation),
            expansion_state=ExpansionState.COMPLETE if stated is None else ExpansionState(stated),
        )


@declare(
    "contains_what_enumeration_listed",
    version="1",
    summary="Draw a directory's containment from what enumeration listed inside it",
    effect=RuleEffect.ENUMERATES,
    reads=Reads.ENUMERATION,
)
def _build_contains_what_enumeration_listed() -> Predicate:
    return ContainsWhatEnumerationListed()


@declare(
    "totals_from_enumeration",
    version="1",
    summary="Count what a directory holds, directly and beneath it",
    effect=RuleEffect.ENUMERATES,
    reads=Reads.ENUMERATION,
)
def _build_totals_from_enumeration() -> Predicate:
    return TotalsFromEnumeration()


@declare(
    "object_kind_from_mode",
    version="1",
    summary="Read the precise kind of an object that is not a file, a directory or a link",
    effect=RuleEffect.ENUMERATES,
    reads=Reads.ENUMERATION,
)
def _build_object_kind_from_mode() -> Predicate:
    return ObjectKindFromMode()


@declare(
    "embeds_what_expansion_recovered",
    version="1",
    summary="Draw a container's embedding of a member recovered from it",
    effect=RuleEffect.ENUMERATES,
    reads=Reads.ENUMERATION,
)
def _build_embeds_what_expansion_recovered() -> Predicate:
    return EmbedsWhatExpansionRecovered()
