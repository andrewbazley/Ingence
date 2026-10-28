"""A listing as a root: an archive's file list indexed with no filesystem beneath it.

The census reads ``os.scandir`` and ``os.stat``, and an archive's listing has neither. A listing is
converted into one format (:mod:`ingence.listing.format`), declared as a root whose URI is
``listing:<archive>:<identifier>``, and read by ``ingence index`` from the project's own cache
through the census's replay seam, so every name is read, bound and characterised by the functions
a walk calls. Every fact a listing does not state is absent, never zero. Every fact it does state
is *declared*: a third party's, kept apart from what the engine observed and from what its user
said (``SourceScope.DECLARED``).

- :mod:`~ingence.listing.format`: the one format, its reader and writer.
- :mod:`~ingence.listing.sources`: the registry of archives whose listings the engine can fetch.
- :mod:`~ingence.listing.ingest`: a listing into Index entries and declared observations.
"""

from __future__ import annotations
