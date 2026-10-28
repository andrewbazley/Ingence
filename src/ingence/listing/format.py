"""One listing format for every archive: what an archive states about each file, and nothing more.

A listing is a third party's statement. An archive's list of its files says, for each, a path
and whatever else the archive chose to publish: a size, a time, a checksum, the depositor's own
free-text attributes, a note that one file was made from another. None of it was read from the bytes
by this engine and none of it was said by the person using it, so every field here is *declared*
(``SourceScope.DECLARED`` once indexed), and the format keeps each statement beside what it means:

- A declared clock carries its meaning, because the times archives publish are rarely the
  data's. A web server's ``Last-Modified`` on an archive's file is when the archive wrote its
  copy. An object store's upload time is when the file was uploaded. Only some archives publish
  the depositor's own modification time. :class:`ClockMeaning` names each, and
  :attr:`ClockMeaning.kind` says which are copies. A rule reads only a clock whose meaning it
  understands.
- A declared digest carries its method (``md5``, ``sha256``), and only a value that the archive
  says is a digest of the content is one. A web server's ``ETag`` is not, and an object store's
  ``ETag`` is an MD5 only where the file was uploaded in one part. The converter decides that,
  never the engine.
- A declared link carries its relation (made from, same as, refers to) and the attribute or
  field that said so, so a scored run can withhold every link and use them as the answer key.
- A path that the archive lists as a link (a symbolic link or an archive's stand-in) carries its
  text as written (``link_target``). Where the listing says how large the file behind it is, the
  path also carries that size and the field that said it (``target_size``,
  ``target_size_said_by``). The size is the file's size, never the link's own.
- A file whose address answered no file carries what the address answered (:class:`AddressState`:
  not found, moved, or an address that could not be read as one), when, the reading that found it
  and the status it answered (``address``). The file is still listed and still an entry. A broken
  address says nothing about whether the file exists elsewhere.

Each archive's fetcher converts into this format, and nothing else reads an archive's own shape. A
later archive adds a converter, not an engine change. On disk a listing is a directory holding
``listing.json`` (the header) and ``rows.jsonl`` (one file per line, in path order), both canonical
JSON, so two conversions of one response are the same bytes.

Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import write_atomically, writing_atomically
from ingence.core.ids import canonical_json, digest_text

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "ADDRESS_STATUSES",
    "HEADER_FILE",
    "LINK_PLACEHOLDERS",
    "LISTING_FORMAT",
    "LISTING_FORMAT_VERSION",
    "LISTING_SCHEME",
    "ROWS_FILE",
    "AddressState",
    "ClockKind",
    "ClockMeaning",
    "DeclaredAddress",
    "DeclaredClock",
    "DeclaredDigest",
    "DeclaredLink",
    "LinkRelation",
    "Listing",
    "ListingError",
    "ListingHeader",
    "ListingRow",
    "address_state_of",
    "answered_state_of",
    "folders_of",
    "is_placeholder",
    "listing_digest",
    "parse_instant",
    "parse_listing_uri",
    "read_listing",
    "root_uri_of",
    "write_listing",
]

LISTING_FORMAT = "ingence-listing"
#: Bumped whenever a field changes meaning or disappears. A reader refuses a version it does not
#: know.
LISTING_FORMAT_VERSION = "1"
#: The scheme of a listing root's ``root_uri``: ``listing:<archive>:<identifier>``.
LISTING_SCHEME = "listing"
HEADER_FILE = "listing.json"
ROWS_FILE = "rows.jsonl"


#: What a declared link's target never is: the values a depositor writes in a
#: link's column to say there is none, compared whole, with case folded and surrounding space set
#: aside. Blank is one too. Without this rule, a study's ``-`` in its source-image column would
#: become a declared link, on every row, to a file named ``-``. The values count as placeholders
#: only where the value names no listed path: a listed file called ``NA`` is still a link's
#: target, which the converter and the ingest check. This is the format's own definition of "no
#: value". It lives beside the format rather than in a converter, so every converter and the
#: census's ingest refuse the same ones.
LINK_PLACEHOLDERS = frozenset({"-", "n/a", "na", "none", "null", "nan", "#n/a"})


def is_placeholder(text: str) -> bool:
    """Whether a link's written target says there is no target (:data:`LINK_PLACEHOLDERS`)."""
    folded = text.strip().casefold()
    return not folded or folded in LINK_PLACEHOLDERS


class ListingError(ValueError):
    """A listing that cannot be read as this format, with what is wrong and where."""


class ClockKind(StrEnum):
    """What a declared time is a time *of*: the data's last change, or a copy's arrival."""

    #: The file's content was last changed then, as the depositor's own filesystem recorded it.
    MODIFICATION = "modification"
    #: A copy of the file arrived somewhere then. Says nothing about when the content was made.
    COPY = "copy"


class ClockMeaning(StrEnum):
    """Who stated a declared time, and what event it marks."""

    #: The depositor's own modification time, carried by the archive (DANDI's ``blobDateModified``).
    DEPOSITOR_MODIFIED = "depositor_modified"
    #: When the archive wrote its copy (a web server's ``Last-Modified`` on the archive's file).
    ARCHIVE_WRITE = "archive_write"
    #: When the file was uploaded to the archive (an object store's ``LastModified``).
    UPLOAD = "upload"

    @property
    def kind(self) -> ClockKind:
        return ClockKind.MODIFICATION if self is ClockMeaning.DEPOSITOR_MODIFIED else ClockKind.COPY


class LinkRelation(StrEnum):
    """What a listing says one file is to another."""

    #: This file was made from the target (a mask from its source image, a derivative from raw
    #: data).
    MADE_FROM = "made_from"
    #: This file holds the same content as the target, as the archive states it.
    SAME_AS = "same_as"
    #: This file mentions or points at the target.
    REFERENCES = "references"


class AddressState(StrEnum):
    """What a listed file's address answered, where it answered no file."""

    #: The server said nothing is at the address (404 not found, 410 gone).
    NOT_FOUND = "not_found"
    #: The server said what was at the address has moved for good (301, 308), as a listing may
    #: state it from a reading that did not follow the redirect. The engine's own readings
    #: follow one and record what its target answers (:func:`answered_state_of`), so they never
    #: record this.
    MOVED = "moved"
    #: The address cannot be read as one, so nothing was asked of any server.
    MALFORMED = "malformed"


#: The statuses that say an address answers no file, by what they say, as a listing may state
#: them. A temporary redirect (302, 303, 307) is how many archives serve a file from a store
#: elsewhere, so it is not a broken address. A server's own failure (500) or a refusal (429, 503)
#: says nothing about the address at all. HTTP's own meanings, not a measured threshold. A permanent
#: redirect (301, 308) is followed by the engine's own readings and recorded by what its target
#: answers: a file moved for good that still answers is not broken, and one moved to nothing
#: answers not found (:func:`answered_state_of`).
ADDRESS_STATUSES: Mapping[int, AddressState] = MappingProxyType(
    {
        301: AddressState.MOVED,
        308: AddressState.MOVED,
        404: AddressState.NOT_FOUND,
        410: AddressState.NOT_FOUND,
    }
)


def address_state_of(status: int | None) -> AddressState | None:
    """What a status says of the address it answered, or None where it says it holds a file or
    says nothing about it."""
    return None if status is None else ADDRESS_STATUSES.get(status)


def answered_state_of(status: int | None) -> AddressState | None:
    """What a status says of an address that the engine read, following redirects: not found, or
    None.

    The engine's web client follows a permanent redirect, so the status it reports is its target's;
    a redirect's status reaching it means the client gave up following (a loop, or too many
    redirects), which is a failure reaching the file, never "moved".
    """
    state = address_state_of(status)
    return state if state is AddressState.NOT_FOUND else None


#: The digest methods a listing may declare, spelled as ``hashlib`` spells them.
DIGEST_METHODS = frozenset({"md5", "sha1", "sha256", "sha512", "blake3"})
_HEX = re.compile(r"^[0-9a-f]+$")
_DIGEST_LENGTHS = {"md5": 32, "sha1": 40, "sha256": 64, "sha512": 128}
_ARCHIVE_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class DeclaredClock:
    """A time a listing states for a file, with its meaning and the field that stated it."""

    meaning: ClockMeaning
    #: Nanoseconds since the epoch, UTC.
    at_ns: int
    #: The listing's own name for the field (``Last-Modified``, ``LastModified``).
    said_by: str

    def as_json(self) -> dict[str, Any]:
        return {"meaning": self.meaning.value, "at": _instant(self.at_ns), "said_by": self.said_by}


@dataclass(frozen=True, slots=True)
class DeclaredDigest:
    """A digest of the file's whole content, as the listing states it."""

    method: str
    #: Lower-case hexadecimal.
    value: str
    said_by: str

    def as_json(self) -> dict[str, Any]:
        return {"method": self.method, "value": self.value, "said_by": self.said_by}


@dataclass(frozen=True, slots=True)
class DeclaredLink:
    """A relation the listing states between this file and another, and who said so."""

    relation: LinkRelation
    #: The target as the listing names it: a path in the same listing, or, for a file in another
    #: listing, ``listing:<archive>:<identifier>/<path>``. Kept as written. Resolving it is a
    #: reader's.
    target: str
    said_by: str

    def as_json(self) -> dict[str, Any]:
        return {"relation": self.relation.value, "target": self.target, "said_by": self.said_by}


@dataclass(frozen=True, slots=True)
class DeclaredAddress:
    """What a listed file's address answered when it was read, where it answered no file.

    When and how are part of the statement. An address that answered *not found* once may
    answer later, and a reader weighs a statement by its date and by the reading that made it.
    """

    state: AddressState
    #: When the address was read: nanoseconds since the epoch, UTC.
    at_ns: int
    #: The reading that found it, in the converter's words (``HEAD``, ``GET``, a remote reading's
    #: method).
    found_by: str
    #: The status the address answered. It is None where the address is malformed and nothing was
    #: asked.
    status: int | None = None

    def as_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {"state": self.state.value}
        if self.status is not None:
            found["status"] = self.status
        found["at"] = _instant(self.at_ns)
        found["found_by"] = self.found_by
        return found


@dataclass(frozen=True, slots=True)
class ListingRow:
    """One file as a listing declares it. Nothing here was observed on a filesystem."""

    #: Relative, POSIX-separated, as the archive spells it (never normalised here).
    path: str
    size_bytes: int | None = None
    clocks: tuple[DeclaredClock, ...] = ()
    digests: tuple[DeclaredDigest, ...] = ()
    #: The depositor's or the archive's attributes, name and value, in the listing's order.
    attributes: tuple[tuple[str, str], ...] = ()
    links: tuple[DeclaredLink, ...] = ()
    #: Where the file's bytes may be fetched, where the archive says (a URL). Never read by a rule.
    locator: str | None = None
    #: The text of a link that the archive lists at this path, as written: the path is
    #: a link, never followed, and its own size is ``size_bytes`` where stated.
    link_target: str | None = None
    #: The size the listing declares for the file the link stands for, and the field that said it.
    #: Only beside a ``link_target``.
    target_size_bytes: int | None = None
    target_size_said_by: str | None = None
    #: What the file's address answered, where it answered no file. The file is listed all the
    #: same, and its address is what is broken.
    address: DeclaredAddress | None = None

    def as_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {"path": self.path}
        if self.size_bytes is not None:
            found["size"] = self.size_bytes
        if self.clocks:
            found["clocks"] = [clock.as_json() for clock in self.clocks]
        if self.digests:
            found["digests"] = [digest.as_json() for digest in self.digests]
        if self.attributes:
            found["attributes"] = [[name, value] for name, value in self.attributes]
        if self.links:
            found["links"] = [link.as_json() for link in self.links]
        if self.locator is not None:
            found["locator"] = self.locator
        if self.link_target is not None:
            found["link_target"] = self.link_target
        if self.target_size_bytes is not None:
            found["target_size"] = self.target_size_bytes
            found["target_size_said_by"] = self.target_size_said_by or "size"
        if self.address is not None:
            found["address"] = self.address.as_json()
        return found


@dataclass(frozen=True, slots=True)
class ListingHeader:
    """What the listing as a whole is, where it came from, and what converted it."""

    #: The archive's short name, lower case (``biostudies``, ``openneuro``).
    archive: str
    #: The archive's identifier for this listing (an accession, a dataset, a snapshot).
    identifier: str
    #: When the archive was asked, ISO 8601 UTC to the second.
    fetched_at: str
    #: The converter that wrote this format, and its version.
    converter: str
    converter_version: str
    #: Where the listing was read from (URLs), in the order they were asked.
    sources: tuple[str, ...] = ()
    #: The listing's own statements about the whole (a study's title, its release date), as text.
    attributes: tuple[tuple[str, str], ...] = ()
    #: Anything the converter needs a reader to know, in plain words (what a clock is, what was
    #: left out and why). Never read by a rule.
    notes: tuple[str, ...] = ()

    @property
    def root_uri(self) -> str:
        return root_uri_of(self.archive, self.identifier)

    def as_json(self) -> dict[str, Any]:
        return {
            "format": LISTING_FORMAT,
            "format_version": LISTING_FORMAT_VERSION,
            "archive": self.archive,
            "identifier": self.identifier,
            "root_uri": self.root_uri,
            "fetched_at": self.fetched_at,
            "converter": self.converter,
            "converter_version": self.converter_version,
            "sources": list(self.sources),
            "attributes": [[name, value] for name, value in self.attributes],
            "notes": list(self.notes),
        }


@dataclass
class Listing:
    header: ListingHeader
    rows: list[ListingRow] = field(default_factory=list)


def root_uri_of(archive: str, identifier: str) -> str:
    """The ``root_uri`` a listing root is declared by: ``listing:<archive>:<identifier>``."""
    if not _ARCHIVE_NAME.match(archive):
        message = f"an archive's name is lower-case letters, digits, '-' and '_': {archive!r}"
        raise ListingError(message)
    if not identifier or "/" in identifier or identifier != identifier.strip():
        message = f"a listing's identifier holds no '/' and no surrounding space: {identifier!r}"
        raise ListingError(message)
    return f"{LISTING_SCHEME}:{archive}:{identifier}"


def parse_listing_uri(uri: str) -> tuple[str, str] | None:
    """The archive and identifier a listing root's URI names, or None where it names no listing."""
    scheme, _, rest = uri.partition(":")
    if scheme != LISTING_SCHEME or ":" not in rest:
        return None
    archive, _, identifier = rest.partition(":")
    try:
        root_uri_of(archive, identifier)
    except ListingError:
        return None
    return archive, identifier


def _instant(nanoseconds: int) -> str:
    moment = dt.datetime.fromtimestamp(nanoseconds // 1_000_000_000, tz=dt.UTC)
    fraction = nanoseconds % 1_000_000_000
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{text}.{fraction:09d}Z" if fraction else f"{text}Z"


def _nanoseconds(text: str) -> int:
    """An ISO 8601 instant with an offset or a ``Z`` as nanoseconds since the epoch."""
    cleaned = text.strip()
    whole, _, fraction = cleaned.replace("Z", "+00:00").partition(".")
    zone = ""
    if fraction:
        digits = re.match(r"^\d+", fraction)
        zone = fraction[digits.end() :] if digits else fraction
        fraction = digits.group(0) if digits else ""
    moment = dt.datetime.fromisoformat(whole + zone)
    if moment.tzinfo is None:
        message = f"a declared time must say its zone: {text!r}"
        raise ListingError(message)
    seconds = int(moment.timestamp())
    return seconds * 1_000_000_000 + int((fraction + "000000000")[:9] or 0)


def parse_instant(text: str) -> int | None:
    """A declared time as nanoseconds since the epoch, or None where it cannot be read as one."""
    try:
        return _nanoseconds(text)
    except (ListingError, ValueError):
        return None


def _check_path(path: str, seen: set[str], where: str) -> None:
    pure = PurePosixPath(path)
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or str(pure) != path
    ):
        message = f"{where}: a listed path is relative, POSIX-separated and plain: {path!r}"
        raise ListingError(message)
    if path in seen:
        message = f"{where}: {path!r} is listed twice"
        raise ListingError(message)
    seen.add(path)


def _address_from(stated: object, where: str) -> DeclaredAddress | None:
    """A broken address as a row states it, refused where it contradicts itself or omits when or
    by what reading it was found."""
    if stated is None:
        return None
    if not isinstance(stated, dict):
        message = f"{where}: a broken address is stated as an object: {stated!r}"
        raise ListingError(message)
    try:
        state = AddressState(str(stated.get("state")))
    except ValueError as error:
        message = f"{where}: a broken address names one of {[s.value for s in AddressState]}"
        raise ListingError(message) from error
    status = stated.get("status")
    if status is not None and (not isinstance(status, int) or isinstance(status, bool)):
        message = f"{where}: a broken address's status is a whole number: {status!r}"
        raise ListingError(message)
    if state is AddressState.MALFORMED and status is not None:
        message = f"{where}: a malformed address was never asked, so it answered no status"
        raise ListingError(message)
    if status is not None and address_state_of(status) is not state:
        message = f"{where}: a broken address stated {state.value} answered status {status}"
        raise ListingError(message)
    if not stated.get("at") or not stated.get("found_by"):
        message = f"{where}: a broken address says when it was found and by what reading"
        raise ListingError(message)
    return DeclaredAddress(
        state=state,
        at_ns=_nanoseconds(str(stated["at"])),
        found_by=str(stated["found_by"]),
        status=status,
    )


def _row_from(item: Mapping[str, Any], where: str) -> ListingRow:
    size = item.get("size")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        message = f"{where}: a declared size is a whole number of bytes: {size!r}"
        raise ListingError(message)
    clocks = []
    for clock in item.get("clocks", ()):
        try:
            meaning = ClockMeaning(clock["meaning"])
        except (KeyError, ValueError) as error:
            message = f"{where}: a declared clock names one of {[m.value for m in ClockMeaning]}"
            raise ListingError(message) from error
        clocks.append(
            DeclaredClock(
                meaning=meaning, at_ns=_nanoseconds(str(clock["at"])), said_by=str(clock["said_by"])
            )
        )
    digests = []
    for digest in item.get("digests", ()):
        method, value = str(digest.get("method", "")), str(digest.get("value", ""))
        expected = _DIGEST_LENGTHS.get(method)
        if method not in DIGEST_METHODS or not _HEX.match(value):
            message = f"{where}: a declared digest is a known method and lower-case hex: {digest!r}"
            raise ListingError(message)
        if expected is not None and len(value) != expected:
            message = f"{where}: a {method} digest is {expected} hex digits, not {len(value)}"
            raise ListingError(message)
        digests.append(DeclaredDigest(method=method, value=value, said_by=str(digest["said_by"])))
    links = []
    for link in item.get("links", ()):
        try:
            relation = LinkRelation(link["relation"])
        except (KeyError, ValueError) as error:
            message = f"{where}: a declared link names one of {[r.value for r in LinkRelation]}"
            raise ListingError(message) from error
        links.append(
            DeclaredLink(
                relation=relation, target=str(link["target"]), said_by=str(link["said_by"])
            )
        )
    attributes = tuple((str(name), str(value)) for name, value in item.get("attributes", ()))
    locator = item.get("locator")
    link_target = item.get("link_target")
    target_size = item.get("target_size")
    if target_size is not None:
        if link_target is None:
            message = f"{where}: a target size is declared only for a path listed as a link"
            raise ListingError(message)
        if not isinstance(target_size, int) or isinstance(target_size, bool) or target_size < 0:
            message = f"{where}: a declared target size is a whole number of bytes: {target_size!r}"
            raise ListingError(message)
    return ListingRow(
        path=str(item["path"]),
        size_bytes=size,
        clocks=tuple(sorted(clocks, key=lambda c: (c.meaning.value, c.at_ns, c.said_by))),
        digests=tuple(sorted(digests, key=lambda d: (d.method, d.value, d.said_by))),
        attributes=attributes,
        links=tuple(sorted(links, key=lambda k: (k.relation.value, k.target, k.said_by))),
        locator=None if locator is None else str(locator),
        link_target=None if link_target is None else str(link_target),
        target_size_bytes=target_size,
        target_size_said_by=(
            None if target_size is None else str(item.get("target_size_said_by") or "size")
        ),
        address=_address_from(item.get("address"), where),
    )


def write_listing(listing: Listing, directory: Path) -> Path:
    """Write the header and the rows, in path order, as canonical JSON, and return the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    rows = sorted(listing.rows, key=lambda row: row.path)
    for row in rows:
        _check_path(row.path, seen, listing.header.root_uri)
    write_atomically(directory / HEADER_FILE, canonical_json(listing.header.as_json()) + "\n")
    with writing_atomically(directory / ROWS_FILE, newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row.as_json()) + "\n")
    return directory


def read_listing(directory: Path) -> Listing:
    """Read and check a listing written by :func:`write_listing` or any converter into it."""
    header_path = directory / HEADER_FILE
    if not header_path.is_file():
        message = f"no listing at {directory}: {HEADER_FILE} is missing"
        raise ListingError(message)
    raw = json.loads(header_path.read_text("utf-8"))
    if raw.get("format") != LISTING_FORMAT:
        message = f"{header_path} is not an {LISTING_FORMAT} header"
        raise ListingError(message)
    if raw.get("format_version") != LISTING_FORMAT_VERSION:
        message = (
            f"{header_path} is format version {raw.get('format_version')!r}; this engine reads "
            f"version {LISTING_FORMAT_VERSION}"
        )
        raise ListingError(message)
    header = ListingHeader(
        archive=str(raw["archive"]),
        identifier=str(raw["identifier"]),
        fetched_at=str(raw["fetched_at"]),
        converter=str(raw["converter"]),
        converter_version=str(raw["converter_version"]),
        sources=tuple(str(source) for source in raw.get("sources", ())),
        attributes=tuple((str(n), str(v)) for n, v in raw.get("attributes", ())),
        notes=tuple(str(note) for note in raw.get("notes", ())),
    )
    stated = raw.get("root_uri")
    if stated is not None and stated != header.root_uri:
        message = f"{header_path} says {stated!r} and names {header.root_uri!r}"
        raise ListingError(message)
    listing = Listing(header=header)
    seen: set[str] = set()
    rows_path = directory / ROWS_FILE
    if rows_path.is_file():
        with rows_path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                where = f"{rows_path.name} line {number}"
                row = _row_from(json.loads(line), where)
                _check_path(row.path, seen, where)
                listing.rows.append(row)
    listing.rows.sort(key=lambda row: row.path)
    return listing


def listing_digest(directory: Path) -> str:
    """One digest of a listing as stored, for a run record to say which listing it read."""
    parts: Sequence[str] = [
        (directory / name).read_text("utf-8") if (directory / name).is_file() else ""
        for name in (HEADER_FILE, ROWS_FILE)
    ]
    return digest_text("\n".join(parts))


def folders_of(paths: Iterable[str]) -> list[str]:
    """Every folder a listing's file paths imply, parents first, in canonical order."""
    found: set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while str(parent) not in {".", ""}:
            found.add(str(parent))
            parent = parent.parent
    return sorted(found, key=lambda folder: (folder.count("/"), folder))
