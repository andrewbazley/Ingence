"""The archives whose listings the engine can fetch, reachable only through a registry.

A listing source fetches one listing from its archive into a directory and converts it into the one
format (:mod:`ingence.listing.format`); ``ingence index --fetch`` reaches it by the archive's name
in a listing root's URI and never by importing it. An archive with no source registered can still
be indexed: a converter outside the engine writes its listing into the project's cache, and
``index`` reads it there, so a new archive is a converter and not an engine change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ingence.core.registry import Registry

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.settings import ListingSettings
    from ingence.listing.format import Listing
    from ingence.listing.http import PoliteClient

__all__ = ["LISTING_SOURCES", "ListingSource", "load_builtins"]


class ListingSource(Protocol):
    """One archive's fetcher and converter."""

    #: The archive's short name, as a listing root's URI spells it.
    archive: str
    version: str

    def fetch(
        self,
        identifier: str,
        directory: Path,
        *,
        client: PoliteClient,
        settings: ListingSettings,
    ) -> Listing:
        """Fetch the listing into ``directory`` (its raw responses under ``raw/``), convert it,
        write the converted listing beside them, and return it."""
        ...


#: Every listing source, reachable only through here.
LISTING_SOURCES: Registry[ListingSource] = Registry("listing_source")


@LISTING_SOURCES.register(
    "biostudies",
    version="1",
    summary="The BioStudies API and its file lists (the BioImage Archive's studies)",
)
def _build_biostudies() -> ListingSource:
    from ingence.listing.biostudies import BioStudiesSource

    return BioStudiesSource()


def load_builtins() -> None:
    """Load nothing: the built-in sources register when this module is imported.

    The function exists for symmetry with the other registries.
    """
