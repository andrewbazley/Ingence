"""An archive link a listing states, and the size it declares for the file behind it.

Without a size, an archive's link is matched to the real file it stands for by name and path alone,
since a link states no size. A listing can say more: that a path is a link (its text as written)
and how large the file behind it is. That size is declared, never an entry column, because the
link's own size is not the file's. It is carried as ``declared.size.target`` from the listing's
archive, and the copy rule reads it. A real file of another size is not the link's copy, and a
match on an agreeing size says so and cites the statement. Every archive, host, path and size here
is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, Observation, SymlinkState, ValueKind
from ingence.grouping.archive_links import (
    NAME_AND_PATH,
    NAME_PATH_AND_SIZE,
    with_archived_copies,
)
from ingence.infer.subjects import EntryRecords
from ingence.listing.format import (
    Listing,
    ListingError,
    ListingHeader,
    ListingRow,
    read_listing,
    write_listing,
)
from ingence.listing.ingest import TARGET_SIZE_KEY
from ingence.rulebook.registry import Reads
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

_URI = "listing:kelt:K-0002"
_LINK = "\\??\\UNC\\vorn-host\\zib\\trell\\brisa_07.nd2"


def _header() -> ListingHeader:
    return ListingHeader(
        archive="kelt",
        identifier="K-0002",
        fetched_at="2026-01-02T03:04:05Z",
        converter="test",
        converter_version="1",
    )


_ROWS = [
    ListingRow(path="copies/trell/brisa_07.nd2", size_bytes=900),
    ListingRow(
        path="live/brisa_07.nd2",
        link_target=_LINK,
        target_size_bytes=900,
        target_size_said_by="Size",
    ),
]


def test_a_listed_link_and_its_target_size_round_trip(tmp_path: Path) -> None:
    write_listing(Listing(_header(), _ROWS), tmp_path)
    again = read_listing(tmp_path)
    (link,) = [row for row in again.rows if row.link_target is not None]
    assert (link.link_target, link.target_size_bytes, link.target_size_said_by) == (
        _LINK,
        900,
        "Size",
    )
    assert link.size_bytes is None


def test_a_target_size_without_a_link_is_refused(tmp_path: Path) -> None:
    (tmp_path / "rows").mkdir()
    write_listing(Listing(_header(), [ListingRow(path="kelt.nd2")]), tmp_path / "rows")
    rows = tmp_path / "rows" / "rows.jsonl"
    rows.write_text(json.dumps({"path": "kelt.nd2", "target_size": 900}) + "\n")
    with pytest.raises(ListingError, match="link"):
        read_listing(tmp_path / "rows")


def _listing_project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "project", created_at="t", listings=[_URI])
    write_listing(Listing(_header(), _ROWS), Path(project.identity.roots[0].mount_path))
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:00Z"
    ).published
    return project


def test_the_census_carries_the_size_declared_and_never_as_the_links_own(tmp_path: Path) -> None:
    project = _listing_project(tmp_path)
    with open_catalog(project.path) as catalog:
        (entry,) = catalog.query(
            "SELECT entry_id, entry_type, size_bytes, symlink_target, symlink_state FROM entries "
            "WHERE relative_path = 'live/brisa_07.nd2'"
        ).fetchall()
        declared = catalog.query(
            "SELECT entry_id, observer, source_scope, value_kind, value_integer FROM observations "
            "WHERE key = ?",
            [TARGET_SIZE_KEY],
        ).fetchall()
    assert entry[1:] == ("symlink", None, _LINK, "outside_root")
    assert declared == [(entry[0], "listing.kelt", "declared", "integer", 900)]


def _held(entry_id: str, path: str, *, size: int | None = None, link: str | None = None):
    entry = Entry(
        entry_id=entry_id,
        root_id="root_a",
        relative_path=path,
        name=path.rsplit("/", 1)[-1],
        entry_type=EntryType.SYMLINK if link else EntryType.FILE,
        size_bytes=size,
        symlink_target=link,
        symlink_state=SymlinkState.OUTSIDE_ROOT if link else SymlinkState.NOT_A_LINK,
        first_seen_run="r",
        last_seen_run="r",
    )
    return EntryRecords(entry=entry)


def _declared_size(entry_id: str, size: int) -> Observation:
    return Observation(
        observation_id="obs_size",
        entry_id=entry_id,
        run_id="r",
        observer="listing.kelt",
        observer_version="1",
        observed_at="t",
        key=TARGET_SIZE_KEY,
        value_kind=ValueKind.INTEGER,
        value_integer=size,
    )


def test_the_copy_rule_reads_the_declared_size_and_says_so() -> None:
    real = _held("real", "copies/trell/brisa_07.nd2", size=900)
    link = _held("link", "live/brisa_07.nd2", link=_LINK)
    # With no size declared, name and path match.
    plain = {held.entry.entry_id: held for held in with_archived_copies([real, link])}
    assert (plain["link"].archived_copy_of, plain["link"].archived_copy_basis) == (
        "real",
        NAME_AND_PATH,
    )
    # A size that agrees is said, and the statement is cited.
    agreeing = {
        held.entry.entry_id: held
        for held in with_archived_copies(
            [real, link], declared_sizes={"link": _declared_size("link", 900)}
        )
    }
    assert agreeing["link"].archived_copy_basis == NAME_PATH_AND_SIZE
    assert agreeing["link"].archived_copy_evidence == ("obs_size",)
    # A size that disagrees: the file of that name and path is not the link's copy.
    other = {
        held.entry.entry_id: held
        for held in with_archived_copies(
            [real, link], declared_sizes={"link": _declared_size("link", 901)}
        )
    }
    assert other["link"].archived_copy_of is None
    assert other["real"].archived_copy_of is None


def test_a_refusal_by_a_declared_size_is_said_and_cites_the_statement() -> None:
    """A declared size decides nothing silently: the link left unmatched says the listing
    declares another size, and the rule's abstention names it and cites the statement."""
    from ingence.rulebook.predicates.copies import ArchiveLinkNamesAFile

    real = _held("real", "copies/trell/brisa_07.nd2", size=900)
    link = _held("link", "live/brisa_07.nd2", link=_LINK)
    other = {
        held.entry.entry_id: held
        for held in with_archived_copies(
            [real, link], declared_sizes={"link": _declared_size("link", 901)}
        )
    }
    assert "901" in other["link"].archived_copy_refused
    subject = other["link"].narrowed(Reads.ARCHIVED_COPY)
    abstention = ArchiveLinkNamesAFile().explain_abstention(subject, {})
    assert "901" in abstention.reason
    assert "obs_size" in abstention.inputs
