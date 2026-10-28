"""The domain profile of a folder, recorded as a finding that asks nothing.

Every shipped domain section loads in every project, as one dictionary. So no pack is proposed
or accepted. While a run reads the names, it reads which domain each folder's files lean to. Three
things count, and each file is counted once per domain.

* The domain of the file type. An ND2 image is microscopy's, and a FASTQ file is sequencing's.
* A word that only one domain spells, such as a nuclear stain or a blot.
* A tied word whose neighbours in a name template share a domain.

The census stores the profile beside the folder as an observation
(:data:`~ingence.dictionary.discover.DOMAIN_PROFILE_KEY`). This rule raises the profile in plain
words, so a page can say "82% of this folder's files are microscope images". The reading of the
words shared between domains is then a stated assumption that a person can see and reverse.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ingence.core.schema import EntryType, RuleEffect
from ingence.dictionary.discover import DOMAIN_PROFILE_KEY, profile_in_words
from ingence.rulebook.registry import Abstention, Raised, Reads, declare

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.rulebook.registry import Predicate

__all__ = ["FolderDomainProfile"]


class FolderDomainProfile:
    """A folder is raised with the domain profile the file list read for it."""

    name = "folder_domain_profile"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the folder's profile, in words, resting on the observation that holds it."""
        entry = getattr(subject, "entry", None)
        if entry is None or entry.entry_type is not EntryType.DIRECTORY:
            return None
        for observation in getattr(subject, "observations", ()):
            if observation.key != DOMAIN_PROFILE_KEY or not observation.value_structure:
                continue
            try:
                profile = json.loads(observation.value_structure)
            except ValueError:
                continue
            if not isinstance(profile, dict):
                continue
            said = profile_in_words(profile)
            return Raised(values=(said,), inputs=(observation.observation_id,), reason=said)
        return None

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no profile was read for this entry: a file, or a folder with no voting file."""
        entry = getattr(subject, "entry", None)
        if entry is not None and entry.entry_type is not EntryType.DIRECTORY:
            return Abstention(reason="a profile is a folder's, and this is a file")
        return Abstention(reason="no file in this folder reads as any domain's")


@declare(
    "folder_domain_profile",
    version="1",
    summary="Raise a folder with the domain profile the file list read for it, asking nothing",
    effect=RuleEffect.FLAGS,
    reads=Reads.RECORDS,
)
def _build_folder_domain_profile() -> Predicate:
    return FolderDomainProfile()
