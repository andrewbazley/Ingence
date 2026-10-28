"""The BioStudies API's studies (the BioImage Archive's among them) as listings.

The API's shapes, as read from public studies:

- ``GET <API>/<accno>/info`` returns ``httpLink``, ``ftpLink``, ``globusLink``, ``isPublic``,
  ``released`` and ``modified`` (epoch milliseconds, of the *study*, never of a file), ``relPath``
  and ``hasZippedFolders``.
- ``GET <API>/<accno>`` returns the submission: ``accno``, ``attributes``, and a ``section`` tree
  whose sections name their files through an attribute ``{"name": "File List", "value":
  "<list>.json"}``, or hold them inline under ``files``.
- A file list is JSON at ``<httpLink>/Files/<list>``: an array of
  ``{"path", "size", "type", "attributes": [{"name", "value"}]}``. It has no checksum and no time.
  The depositor's attributes are free text.
- A file's bytes are at ``<httpLink>/Files/<path>``. The web server's ``Last-Modified`` on it is
  when the archive wrote its copy (a study released days later carries files written before it).
  Its ``ETag`` is the server's own (size and time in hex) and is not a digest of the content.

What the conversion states (:mod:`ingence.listing.format`): each file's path, declared size and
attributes as written; the archive's write time as a clock of meaning ``archive_write`` only where
``read_archive_clocks`` asked the server for each file's headers; no digest; and a declared link
``made_from`` where an annotation names its source image (``Source image association``, the
archive's own convention for a mask and the image it was drawn on, under any of the spellings
depositors use), resolved to a listed path where the name matches one exactly or matches one file's
name alone. A placeholder in that column (``-``, ``n/a``, ``none``, ``null``, blank) is never a
link. It stays an attribute, and the listing's notes say how many were set aside.

A file whose headers answer that its address holds no file (404 or 410 not found, or an address
that could not be read as one, checked before anything is asked) is listed all the same. Its
address is stated broken, with when and by the ``HEAD`` that found it (kept in
``raw/addresses.json``). Such a file does not stop the study's fetch. A permanent redirect (301,
308) is followed by the client and recorded by what its target answers: a file moved for good that
still answers is not broken, and one moved to nothing answers not found. A redirect the client
gives up following (a loop), a server's own failure, and anything raised once a server was asked
still stop the fetch, recorded against the study as the client raised them. None of them says
anything about the address.

A study too large to read whole is refused, in words. A response weighing more than
``ListingSettings.max_listing_bytes`` is closed unread past the limit and kept nowhere, and a
study listing more than ``max_listing_files`` files is stopped before its listing is converted or
written. Either is a failed fetch recorded against the study, naming the limit and the setting.

Every response is kept under the listing's ``raw/`` folder, so the conversion can be re-run offline
and a second fetch compared with the first.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ingence.core.destinations import write_atomically
from ingence.listing.format import (
    AddressState,
    ClockMeaning,
    DeclaredAddress,
    DeclaredClock,
    DeclaredLink,
    LinkRelation,
    Listing,
    ListingHeader,
    ListingRow,
    address_state_of,
    answered_state_of,
    is_placeholder,
    parse_instant,
    write_listing,
)
from ingence.listing.http import ResponseTooLargeError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ingence.core.settings import ListingSettings
    from ingence.listing.http import PoliteClient

__all__ = ["API", "BioStudiesSource", "convert"]

API = "https://www.ebi.ac.uk/biostudies/api/v1/studies"
#: Every spelling that depositors use for the attribute by which an annotation file names the image
#: it was made from. REMBI's convention is "Source image association". A measurement over 36
#: studies found REMBI's own spelling, and ``source_image`` in two studies whose 122 values all
#: name a listed file. The spellings are compared folded: case and everything but letters and
#: digits set aside. They are a converter's knowledge of an archive's conventions, never a
#: rule's.
MADE_FROM_ATTRIBUTES = frozenset({"sourceimageassociation", "sourceimage"})
CONVERTER = "ingence.listing.biostudies"
#: 2: a source image is read under every spelling in ``MADE_FROM_ATTRIBUTES``.
#: 3: a file whose headers answer that its address holds no file is stated so.
#: 4: a placeholder (``-``, ``n/a``, ``none``, ``null``, blank) is never a link, and the
#: placeholders set aside are counted.
CONVERTER_VERSION = "4"
RAW_DIR = "raw"
#: Where the broken addresses a fetch's ``HEAD`` requests found are kept, by path.
ADDRESSES_FILE = "addresses.json"
#: The reading that finds a broken address in a fetch.
_HEAD = "HEAD"


class BioStudiesSource:
    """Fetch a BioStudies study's info, submission and file lists, and convert them."""

    archive = "biostudies"
    version = CONVERTER_VERSION

    def fetch(
        self,
        identifier: str,
        directory: Path,
        *,
        client: PoliteClient,
        settings: ListingSettings,
    ) -> Listing:
        raw = directory / RAW_DIR
        raw.mkdir(parents=True, exist_ok=True)
        from datetime import UTC, datetime

        fetched_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        sources: list[str] = []

        def keep(name: str, url: str) -> Any:
            try:
                answer = client.request(url, max_bytes=settings.max_listing_bytes)
            except ResponseTooLargeError as error:
                message = (
                    f"{identifier} is too large to read whole: {error} Raise "
                    "listing.max_listing_bytes to read it."
                )
                raise ResponseTooLargeError(message) from error
            write_atomically(raw / name, answer.body)
            sources.append(url)
            return json.loads(answer.body)

        info = keep("info.json", f"{API}/{identifier}/info")
        submission = keep("study.json", f"{API}/{identifier}")
        link = str(info.get("httpLink", "")).rstrip("/")
        for _, file_list in sorted(set(_file_lists(submission.get("section", {})))):
            keep(f"filelist__{file_list.replace('/', '__')}", _file_url(link, file_list))
        listed = _count_files(submission, raw)
        if listed > settings.max_listing_files:
            message = (
                f"{identifier} lists {listed:,} files, more than the {settings.max_listing_files:,}"
                " allowed; its listing was not built. Raise listing.max_listing_files to read it."
            )
            raise ResponseTooLargeError(message)
        heads: dict[str, str | None] = {}
        broken: dict[str, dict[str, object]] = {}
        if settings.read_archive_clocks:
            listing = convert(identifier, raw, fetched_at=fetched_at, sources=tuple(sources))
            for row in listing.rows:
                if row.locator is None:
                    continue
                if not _readable(row.locator):
                    # Checked before anything is asked: no server heard of it.
                    broken[row.path] = _broken(AddressState.MALFORMED, None)
                    continue
                try:
                    answer = client.request(row.locator, method="HEAD")
                except urllib.error.HTTPError as error:
                    state = answered_state_of(error.code)
                    if state is None:
                        raise
                    # The answer's connection is let go here, its status being all that is kept.
                    error.close()
                    broken[row.path] = _broken(state, error.code)
                    continue
                heads[row.path] = answer.last_modified
            write_atomically(raw / "heads.json", json.dumps(heads, indent=1, sort_keys=True))
            write_atomically(raw / ADDRESSES_FILE, json.dumps(broken, indent=1, sort_keys=True))
        write_atomically(
            raw / "fetched.json",
            json.dumps({"fetched_at": fetched_at, "sources": sources}, indent=1),
        )
        listing = convert(identifier, raw, fetched_at=fetched_at, sources=tuple(sources))
        write_listing(listing, directory)
        return listing


def _count_files(submission: dict[str, Any], raw: Path) -> int:
    """How many files a study's kept file lists and inline lists hold, before any is converted."""
    section = submission.get("section", {}) or {}
    total = sum(1 for _ in _inline_files(section))
    for _, file_list in sorted(set(_file_lists(section))):
        kept = raw / f"filelist__{file_list.replace('/', '__')}"
        if kept.is_file():
            listed = json.loads(kept.read_text("utf-8"))
            total += len(listed) if isinstance(listed, list) else 0
    return total


def _broken(state: AddressState, status: int | None) -> dict[str, object]:
    """A broken address as a fetch keeps it: what it answered, when, and the reading."""
    from datetime import UTC, datetime

    found: dict[str, object] = {
        "state": state.value,
        "at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "found_by": _HEAD,
    }
    if status is not None:
        found["status"] = status
    return found


def _readable(address: str) -> bool:
    """Whether an address can be read as one before it is asked: a web scheme, a host, a port
    that is a number where one is written, and no space or control character."""
    if any(character.isspace() or ord(character) < 32 for character in address):
        return False
    try:
        parts = urllib.parse.urlsplit(address)
        parts.port  # noqa: B018 - raises where the port is no number
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def _address(kept: object) -> DeclaredAddress | None:
    """A kept broken address as the format states it, or None where it cannot be read as one."""
    if not isinstance(kept, dict):
        return None
    at = parse_instant(str(kept.get("at", "")))
    status = kept.get("status")
    try:
        state = AddressState(str(kept.get("state", "")))
    except ValueError:
        return None
    if at is None or (status is not None and not isinstance(status, int)):
        return None
    if status is not None and address_state_of(status) is not state:
        # A kept status contradicting its state (only a hand-edited file holds one).
        return None
    return DeclaredAddress(
        state=state, at_ns=at, found_by=str(kept.get("found_by") or _HEAD), status=status
    )


def _folded(name: str) -> str:
    return "".join(character for character in name.casefold() if character.isalnum())


def _file_url(link: str, path: str) -> str:
    """A listed file's URL: its path percent-encoded, since depositors name files with spaces."""
    return f"{link}/Files/{quote(path, safe='/')}"


def _file_lists(section: dict[str, Any], path: str = "") -> Iterator[tuple[str, str]]:
    """Every (section path, file list name) in a submission's section tree."""
    here = f"{path}/{section.get('type', '')}".strip("/")
    for attribute in section.get("attributes", []) or []:
        if isinstance(attribute, dict) and attribute.get("name") == "File List":
            yield here, str(attribute.get("value"))
    for child in section.get("subsections", []) or []:
        # A subsection is an object or a list of objects (the API's heterogeneous encoding).
        for item in child if isinstance(child, list) else [child]:
            if isinstance(item, dict):
                yield from _file_lists(item, here)


def _inline_files(section: dict[str, Any], path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    """Every file a section lists inline, with its section's path."""
    here = f"{path}/{section.get('type', '')}".strip("/")
    for item in section.get("files", []) or []:
        for record in item if isinstance(item, list) else [item]:
            if isinstance(record, dict):
                yield here, record
    for child in section.get("subsections", []) or []:
        for item in child if isinstance(child, list) else [child]:
            if isinstance(item, dict):
                yield from _inline_files(item, here)


def _attributes(record: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(attribute.get("name", "")), str(attribute.get("value", "")))
        for attribute in record.get("attributes", []) or []
        if isinstance(attribute, dict)
    )


def _archive_write(value: str | None) -> tuple[DeclaredClock, ...]:
    if not value:
        return ()
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return ()
    if moment.tzinfo is None:
        return ()
    return (
        DeclaredClock(
            meaning=ClockMeaning.ARCHIVE_WRITE,
            at_ns=int(moment.timestamp()) * 1_000_000_000,
            said_by="Last-Modified",
        ),
    )


def convert(
    identifier: str, raw: Path, *, fetched_at: str, sources: tuple[str, ...] = ()
) -> Listing:
    """The listing from a study's kept responses alone: the offline half of a fetch."""
    info = json.loads((raw / "info.json").read_text("utf-8"))
    submission = json.loads((raw / "study.json").read_text("utf-8"))
    heads_path = raw / "heads.json"
    heads: dict[str, str | None] = (
        json.loads(heads_path.read_text("utf-8")) if heads_path.is_file() else {}
    )
    addresses_path = raw / ADDRESSES_FILE
    addresses: dict[str, object] = (
        json.loads(addresses_path.read_text("utf-8")) if addresses_path.is_file() else {}
    )
    link = str(info.get("httpLink", "")).rstrip("/")
    records: list[tuple[str, str, dict[str, Any]]] = []
    section_tree = submission.get("section", {}) or {}
    for section, file_list in sorted(set(_file_lists(section_tree))):
        kept = raw / f"filelist__{file_list.replace('/', '__')}"
        if not kept.is_file():
            continue
        for record in json.loads(kept.read_text("utf-8")):
            if isinstance(record, dict):
                records.append((section, file_list, record))
    for section, record in _inline_files(section_tree):
        records.append((section, "", record))

    rows: dict[str, ListingRow] = {}
    folders_listed = 0
    repeated = 0
    for section, file_list, record in records:
        path = str(record.get("path", "")).strip("/")
        if not path:
            continue
        kind = str(record.get("type", "file"))
        attributes = [*_attributes(record), ("biostudies.section", section)]
        if file_list:
            attributes.append(("biostudies.file_list", file_list))
        if kind != "file":
            folders_listed += 1
            attributes.append(("biostudies.type", kind))
        size = record.get("size")
        if path in rows:
            repeated += 1
            continue
        rows[path] = ListingRow(
            path=path,
            size_bytes=int(size) if isinstance(size, int | float) and size >= 0 else None,
            clocks=_archive_write(heads.get(path)),
            attributes=tuple(attributes),
            locator=_file_url(link, path) if link else None,
            address=_address(addresses.get(path)),
        )
    by_name: dict[str, list[str]] = {}
    for path in rows:
        by_name.setdefault(PurePosixPath(path).name, []).append(path)
    # A placeholder is never a link: the value stays an attribute as written, and
    # how many were set aside is said in the notes.
    placeholders = 0
    for path, row in list(rows.items()):
        links = []
        for name, value in row.attributes:
            if _folded(name) not in MADE_FROM_ATTRIBUTES:
                continue
            written = value.strip()
            # A placeholder only where it names no listed path.
            if is_placeholder(value) and written not in rows and not by_name.get(written):
                placeholders += 1
                continue
            target = (
                written
                if written in rows
                else (by_name[written][0] if len(by_name.get(written, [])) == 1 else written)
            )
            links.append(DeclaredLink(relation=LinkRelation.MADE_FROM, target=target, said_by=name))
        if links:
            rows[path] = ListingRow(
                path=row.path,
                size_bytes=row.size_bytes,
                clocks=row.clocks,
                digests=row.digests,
                attributes=row.attributes,
                links=tuple(links),
                locator=row.locator,
                address=row.address,
            )

    study_attributes = tuple(
        (str(attribute.get("name", "")), str(attribute.get("value", "")))
        for attribute in submission.get("attributes", []) or []
        if isinstance(attribute, dict)
    )
    for key in ("released", "modified"):
        if isinstance(info.get(key), int):
            study_attributes += ((f"biostudies.{key}_ms", str(info[key])),)
    notes = [
        "BioStudies file lists state a path, a size and the depositor's attributes; no checksum "
        "and no time.",
        "A file's Last-Modified is when the archive wrote its copy, read only where each file's "
        "headers were asked for; its ETag is the web server's, not a digest.",
    ]
    if folders_listed:
        notes.append(f"{folders_listed} listed entries are not files (their type is kept).")
    unanswered = sum(row.address is not None for row in rows.values())
    if unanswered:
        notes.append(
            f"{unanswered} listed files have addresses whose headers answered not found, or "
            "addresses that could not be read as ones; they are listed, with their addresses "
            "stated broken."
        )
    if repeated:
        notes.append(f"{repeated} paths were listed more than once; the first is kept.")
    if placeholders:
        notes.append(
            f"{placeholders} values in a link's column were placeholders (such as '-', 'n/a' or "
            "blank) and were not read as links; each is kept as its attribute."
        )
    header = ListingHeader(
        archive="biostudies",
        identifier=identifier,
        fetched_at=fetched_at,
        converter=CONVERTER,
        converter_version=CONVERTER_VERSION,
        sources=sources,
        attributes=study_attributes,
        notes=tuple(notes),
    )
    return Listing(header=header, rows=sorted(rows.values(), key=lambda row: row.path))
