"""Fetching each listing root's listing into the project's cache: ``ingence index --fetch``.

This is the one step of a listing root that uses the network, and it runs only when asked. Each
listing root whose archive has a registered source is fetched through it into
``<project>/listings/<archive>/<identifier>/``, every request logged in that folder's
``raw/requests.jsonl``. A root whose archive has none is left to a converter outside the engine
and said so. A refusal (429, 503) stops that archive for the rest of the fetch. Any other failure
is recorded against its root and the others are fetched. What was fetched is read offline by the
census afterwards, like any cached listing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import open_appending
from ingence.core.project import is_listing_root
from ingence.core.registry import ComponentNotAvailableError
from ingence.listing.http import ArchiveRefusedError, PoliteClient
from ingence.listing.sources import LISTING_SOURCES
from ingence.storage.layout import owned_path

if TYPE_CHECKING:
    from ingence.core.project import Project

__all__ = ["FetchOutcome", "fetch_listing_roots"]

REQUEST_LOG = "requests.jsonl"


@dataclass
class FetchOutcome:
    """What ``--fetch`` did, root by root, in words a person can act on."""

    fetched: list[tuple[str, int]] = field(default_factory=list)
    left: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    requests: int = 0
    bytes_received: int = 0


def fetch_listing_roots(project: Project) -> FetchOutcome:
    """Fetch every listing root's listing whose archive has a registered source."""
    settings = project.settings.listing
    outcome = FetchOutcome()
    clients: dict[str, PoliteClient] = {}
    for declaration in project.identity.roots:
        if not is_listing_root(declaration) or declaration.listing is None:
            continue
        archive, identifier = declaration.listing
        directory = Path(declaration.mount_path)
        if archive not in LISTING_SOURCES:
            outcome.left.append(
                (
                    declaration.root_uri,
                    f"no source is registered for {archive!r}; a converter writes its listing "
                    f"to {directory}",
                )
            )
            continue
        try:
            source = LISTING_SOURCES.get(archive)
        except ComponentNotAvailableError as error:
            outcome.failed.append((declaration.root_uri, str(error)))
            continue
        # The project's own listings folder, nothing on the way a link, and never a link at the
        # log itself (``owned_path``, ``open_appending``).
        log_path = owned_path(project.path, directory / "raw" / REQUEST_LOG)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def log(entry: dict[str, Any], target: Path = log_path) -> None:
            with open_appending(target) as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

        client = clients.setdefault(
            archive,
            PoliteClient(user_agent=settings.user_agent, pause_seconds=settings.pause_seconds),
        )
        client.log = log
        try:
            listing = source.fetch(identifier, directory, client=client, settings=settings)
        except ArchiveRefusedError as error:
            outcome.failed.append((declaration.root_uri, f"the archive said to stop: {error}"))
            continue
        except (OSError, ValueError, HTTPException) as error:
            # A failed fetch is data: it is recorded against its root, and the others
            # are fetched. A narrower ``except`` would let a malformed URL's ``ValueError`` through
            # and stop every root.
            outcome.failed.append((declaration.root_uri, f"{type(error).__name__}: {error}"))
            continue
        outcome.fetched.append((declaration.root_uri, len(listing.rows)))
    for client in clients.values():
        outcome.requests += client.requests
        outcome.bytes_received += client.bytes_received
    return outcome
