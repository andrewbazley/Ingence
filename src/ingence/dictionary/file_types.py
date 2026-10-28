"""File types: the one table every kind of file is declared in, and the scope a type names.

The table (``file_types.yaml``) is the one source for what an extension means. Without it, an
extension would be declared in five places that drift: the core's formats section, each pack's
format senses, type detection's signatures and hints, each content reader's own extension set, and
the census's suffix grammar. Instead, the Dictionary generates its format senses from the table's
rows (a section file says ``file_types: from_table`` where the rows belong). Type detection takes
its signatures and extension hints from the table, and a test holds every reader's extension set
to it. One row is one format in plain words. Several rows share a family, which is the Dictionary
term their extensions bind to. The families, their spellings and their order never move: term
identifiers, stored bindings, answers and ``file_type:`` scopes are made from them. An identity
test holds the generated senses to the ones that the section files declared before the table
existed, to the byte.

Beside the formats the table lists the writers (programs found in a file's tags, each acquisition,
analysis or figure), the instruments (names a folder is called after, read only to guess an area's
kind) and the identifiers (public archives' accessions the tokeniser keeps whole). None of those is
a word of the Dictionary. A function here reads each one.

A file type is a scope. A fifth :class:`~ingence.core.ids.ScopeKind` joins the four regions,
because the correction a person reaches for first is not about a place. "In my ``.fcs`` files,
``FSC`` is forward scatter" is a statement about a kind of file anywhere in the pool. The scope
value is the term an extension binds to through the extension slot (``image_format``, never
``tif`` and separately ``tiff``). So one statement covers every spelling of the type, and a type
the Dictionary does not know cannot be scoped to at all. Only a ``format`` sense may name a type.
The extension slot already applies the same restriction to binding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ingence.core.ids import Scope, ScopeKind, canonical_json, digest_text

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ingence.core.schema import Entry
    from ingence.dictionary.store import DictionaryStore

#: The only kind of sense an extension may resolve to, and so the only kind a file type
#: may name. A parameter rather than a literal buried in a function, because it states a
#: policy and policies are argued about. Declared here rather than in the binder, because the
#: scope and the extension slot are the same restriction and one of them holding a second copy is
#: how the two drift apart.
EXTENSION_KINDS = frozenset({"format"})

__all__ = [
    "CAN_BE_RAW",
    "CATEGORIES",
    "CORE_SECTION",
    "EXTENSION_KINDS",
    "FILE_TYPES_KEY",
    "FROM_TABLE",
    "TABLE_PATH",
    "WRITER_KINDS",
    "Container",
    "Family",
    "FileType",
    "FileTypeRow",
    "FileTypeTable",
    "FormatSignature",
    "IdentifierPattern",
    "Instrument",
    "SignatureSpec",
    "Writer",
    "declared_compounds",
    "extension_domains",
    "file_type_of",
    "file_type_scope",
    "file_type_table",
    "identifiers",
    "instrument_spellings",
    "instruments",
    "known_file_types",
    "writers",
]

#: Where the table lives, beside this module.
TABLE_PATH = Path(__file__).with_name("file_types.yaml")

#: The key a section file sets, and the one value it takes, to say its format senses are the
#: table's rows naming that section.
FILE_TYPES_KEY = "file_types"
FROM_TABLE = "from_table"

#: The core's section of formats: the rows that name no domain belong to it.
CORE_SECTION = "formats"

#: What a row's ``category`` may be.
CATEGORIES = frozenset({"instrument", "general", "program", "container", "archive", "document"})
#: What a row's ``can_be_raw`` may be.
CAN_BE_RAW = frozenset({"yes", "no", "evidence"})
#: What a writer may be marked.
WRITER_KINDS = frozenset({"acquisition", "analysis", "figure"})
#: Who a pack may declare writes a spelling.
_WRITTEN_BY = frozenset({"instrument", "software"})

#: A spelling: lower-case letters and digits, a dot inside a compound, a hyphen, and the
#: underscore of a placeholder spelling no filename carries (``yaml_format``).
_EXTENSION = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")
_TERM = re.compile(r"^[a-z][a-z0-9_]*$")
_HEX = re.compile(r"^(?:[0-9A-Fa-f]{2})+$")
_FAMILY_KEYS = frozenset(
    {"canonical", "ambiguous_with", "program", "product", "declares_origins", "also_a_word"}
)
_ROW_KEYS = frozenset(
    {
        "name",
        "family",
        "extensions",
        "category",
        "can_be_raw",
        "media_type",
        "signatures",
        "built_on",
        "domains",
        "writers",
        "also_a_word",
        "wraps",
        "written_by",
        "vendor",
        "accepts_pack",
    }
)


class FileTypeTableError(ValueError):
    """The table says something the loader cannot read, said in words at the row."""


@dataclass(frozen=True, slots=True)
class FormatSignature:
    """Magic bytes marking a format, at an offset."""

    label: str
    magic: bytes
    offset: int = 0


@dataclass(frozen=True, slots=True)
class SignatureSpec:
    """One signature as type detection reads it: its bytes, its media type and the extensions
    consistent with it (the owner's and every format built on the owner), each with its dot."""

    label: str
    magic: bytes
    offset: int
    media_type: str
    extensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileTypeRow:
    """One format, in plain words: the one row of the table."""

    name: str
    extensions: tuple[str, ...]
    category: str
    can_be_raw: str
    #: The Dictionary term the extensions bind to. It is None for a format that no term names.
    family: str | None = None
    media_type: str | None = None
    signatures: tuple[FormatSignature, ...] = ()
    #: The key of the container the format is written on, whose signature its bytes carry.
    built_on: str | None = None
    domains: tuple[str, ...] = ()
    writers: tuple[str, ...] = ()
    #: The extensions that are ordinary words too, bound after a dot only. It is True when every
    #: extension binds after a dot only.
    also_a_word: tuple[str, ...] | bool = ()
    wraps: bool = False
    written_by: str | None = None
    vendor: str | None = None
    accepts_pack: bool = False

    @property
    def key(self) -> str:
        """The row's first extension, which is how another row names it in ``built_on``."""
        return self.extensions[0]

    @property
    def spelled_after_a_dot(self) -> tuple[str, ...]:
        """The row's extensions that bind after a dot only: all of them, or the ones listed."""
        if isinstance(self.also_a_word, tuple):
            return self.also_a_word
        return self.extensions if self.also_a_word else ()

    @property
    def section(self) -> str:
        """The Dictionary section the row's family is declared in: its first domain's pack, or
        the core's formats section."""
        return self.domains[0] if self.domains else CORE_SECTION


@dataclass(frozen=True, slots=True)
class Container:
    """A general-purpose structure formats are written on, that no format of its own is named
    for: it owns signatures and the rows built on it carry them."""

    key: str
    name: str
    media_type: str
    signatures: tuple[FormatSignature, ...]


@dataclass(frozen=True, slots=True)
class Family:
    """What belongs to a family's sense and not to one of its formats."""

    term: str
    canonical: str | None = None
    ambiguous_with: tuple[str, ...] = ()
    program: bool = False
    product: bool = False
    #: Whether the family's core sense declares origins even where no row names a writer
    #: (``origins: {}``): the image type, where anything writes a TIFF.
    declares_origins: bool = False
    #: The after-a-dot list in the order the sense had it, where the rows' order differs.
    also_a_word: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class Writer:
    """A program found in a file's tags, and what a file it wrote is."""

    name: str
    kind: str
    spellings: tuple[str, ...]
    vendor: str | None = None


@dataclass(frozen=True, slots=True)
class Instrument:
    """An instrument a laboratory names a folder after."""

    name: str
    kind: str
    spellings: tuple[str, ...]
    vendor: str | None = None
    #: Also an ordinary English or biology word: a folder named that one word alone is never read
    #: as the instrument's. Beside other words it still is.
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class IdentifierPattern:
    """A public archive's accession, as a pattern over one whole chunk of a name."""

    name: str
    pattern: str


@dataclass(frozen=True)
class FileTypeTable:
    """The table as read: its rows and lists, and what each consumer takes from them."""

    rows: tuple[FileTypeRow, ...]
    families: Mapping[str, Family]
    containers: tuple[Container, ...]
    writers: tuple[Writer, ...]
    instruments: tuple[Instrument, ...]
    identifiers: tuple[IdentifierPattern, ...]
    #: A digest of the whole document, so a run can say which table it read.
    digest: str
    _by_extension: Mapping[str, FileTypeRow] = field(default_factory=dict, repr=False)

    # -- rows ---------------------------------------------------------------

    def row_for(self, extension: str) -> FileTypeRow | None:
        """Return the row one spelling belongs to, given with or without its dot, any case."""
        return self._by_extension.get(extension.strip().lstrip(".").casefold())

    def rows_of(self, family: str) -> tuple[FileTypeRow, ...]:
        """Return a family's rows, in the table's order."""
        return tuple(row for row in self.rows if row.family == family)

    def extensions(self) -> frozenset[str]:
        """Every extension the table knows, without a dot."""
        return frozenset(self._by_extension)

    def sections(self) -> tuple[str, ...]:
        """The Dictionary sections the rows' families are declared in, in first-seen order."""
        seen: dict[str, None] = {}
        for row in self.rows:
            if row.family is not None:
                seen.setdefault(row.section, None)
        return tuple(seen)

    def can_be_raw(self, extension: str) -> str | None:
        """Whether a file of this spelling is recorded data: ``yes``, ``no``, ``evidence``."""
        row = self.row_for(extension)
        return row.can_be_raw if row is not None else None

    def category_of(self, extension: str) -> str | None:
        """What kind of thing a file of this spelling is, or None for a spelling not here."""
        row = self.row_for(extension)
        return row.category if row is not None else None

    # -- the Dictionary -------------------------------------------------------

    def format_entries(self, section: str) -> list[dict[str, Any]]:
        """Return the format senses of one section as the entries its file would have declared.

        One entry per family among the section's rows, in the rows' order: the family's
        spellings in row order, its after-a-dot list, the flags that belong to the sense, and
        the origins map the rows' writers make. A pack's half of a family the core also holds
        is an extension of the core's sense (``extends: true``, folded by the store), carrying
        spellings and origins only.
        """
        groups: dict[str, list[FileTypeRow]] = {}
        for row in self.rows:
            if row.family is not None and row.section == section:
                groups.setdefault(row.family, []).append(row)
        entries: list[dict[str, Any]] = []
        for term, rows in groups.items():
            declared = self.families[term]
            at_home = section == CORE_SECTION or not any(
                row.family == term and row.section == CORE_SECTION for row in self.rows
            )
            entry: dict[str, Any] = {"term": term}
            if at_home and declared.canonical:
                entry["canonical"] = declared.canonical
            if not at_home:
                entry["extends"] = True
            entry["theme_kind"] = "format"
            entry["surfaces"] = [extension for row in rows for extension in row.extensions]
            after_a_dot = _after_a_dot(rows, declared if at_home else None)
            if after_a_dot is True or after_a_dot:
                entry["extension_only"] = after_a_dot
            if at_home:
                if declared.ambiguous_with:
                    entry["ambiguous_with"] = list(declared.ambiguous_with)
                if declared.program:
                    entry["program"] = True
                if declared.product:
                    entry["product"] = True
            if any(row.wraps for row in rows):
                entry["wraps"] = True
            origins: dict[str, dict[str, Any]] = {}
            for row in rows:
                if row.written_by is None:
                    continue
                for extension in row.extensions:
                    origin: dict[str, Any] = {}
                    if row.vendor:
                        origin["vendor"] = row.vendor
                    origin["written_by"] = row.written_by
                    if row.accepts_pack:
                        origin["accepts_pack"] = True
                    origins[extension] = origin
            if origins or (at_home and declared.declares_origins):
                entry["origins"] = origins
            entries.append(entry)
        return entries

    # -- type detection -----------------------------------------------------

    def signatures(self) -> tuple[SignatureSpec, ...]:
        """Every signature, with the extensions its content is consistent with: the owning
        row's, and those of every row built on the owner. Rows first, in table order, then the
        containers."""
        built: dict[str, list[str]] = {}
        for row in self.rows:
            if row.built_on is not None:
                built.setdefault(row.built_on, []).extend(row.extensions)
        owners: list[tuple[str, str | None, tuple[FormatSignature, ...], tuple[str, ...]]] = [
            (row.key, row.media_type, row.signatures, row.extensions)
            for row in self.rows
            if row.signatures
        ]
        owners.extend(
            (container.key, container.media_type, container.signatures, ())
            for container in self.containers
        )
        specs: list[SignatureSpec] = []
        for key, media_type, signatures, own in owners:
            extensions = tuple(f".{extension}" for extension in (*own, *built.get(key, ())))
            specs.extend(
                SignatureSpec(
                    label=signature.label,
                    magic=signature.magic,
                    offset=signature.offset,
                    media_type=media_type or "application/octet-stream",
                    extensions=extensions,
                )
                for signature in signatures
            )
        return tuple(specs)

    def extension_hints(self) -> dict[str, str]:
        """Extension (with its dot) to the media type detection reports where nothing stronger
        spoke: every row that states one."""
        return {
            f".{extension}": row.media_type
            for row in self.rows
            if row.media_type is not None
            for extension in row.extensions
        }

    def family_for_media_type(self, media_type: str) -> str | None:
        """The family of the row owning a signature of this media type, or None where the type
        is a container's, no row's, or a row's with no family."""
        for row in self.rows:
            if row.signatures and row.media_type == media_type:
                return row.family
        return None

    # -- the lists ----------------------------------------------------------

    def writers_named(self, text: str) -> tuple[Writer, ...]:
        """Every writer whose spelling the text holds as a word of its own, without regard to
        case: ``ZEN`` is named by ``ZEN blue 3.1`` and not by ``frozen lens``."""
        folded = text.casefold()
        return tuple(
            writer
            for writer in self.writers
            if any(
                re.search(rf"(?<![a-z0-9]){re.escape(spelling)}(?![a-z0-9])", folded)
                for spelling in writer.spellings
            )
        )

    def writer_named(self, text: str) -> Writer | None:
        """The one writer a tag's text names, or the one with the longest spelling where it
        names several; None where it names none."""
        named = self.writers_named(text)
        if not named:
            return None
        folded = text.casefold()
        return max(
            named,
            key=lambda writer: max(
                len(spelling) for spelling in writer.spellings if spelling in folded
            ),
        )

    def identifier_patterns(self) -> tuple[str, ...]:
        """The accession patterns, as written."""
        return tuple(identifier.pattern for identifier in self.identifiers)


def _after_a_dot(
    rows: Iterable[FileTypeRow], declared: Family | None
) -> tuple[str, ...] | list[str] | bool:
    """The sense's ``extension_only``: True where every row says ``all``, else the rows' lists in
    row order, or the family's own list where it declares one."""
    listed = list(rows)
    if declared is not None and declared.also_a_word is not None:
        return list(declared.also_a_word)
    if listed and all(row.also_a_word is True for row in listed):
        return True
    found: list[str] = []
    for row in listed:
        found.extend(row.spelled_after_a_dot)
    return found


# ---------------------------------------------------------------------------
# Reading the table.
# ---------------------------------------------------------------------------


def _refuse(where: str, message: str) -> FileTypeTableError:
    return FileTypeTableError(f"file_types.yaml, {where}: {message}")


def _strings(raw: object, *, where: str, what: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise _refuse(where, f"{what} is a list of words, not {raw!r}")
    return tuple(raw)


def _signatures_from(raw: object, *, where: str) -> tuple[FormatSignature, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _refuse(where, "signatures is a list")
    found: list[FormatSignature] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("label"), str):
            raise _refuse(where, "each signature carries a label, hex magic and an offset")
        magic = str(item.get("magic", ""))
        if not _HEX.match(magic):
            raise _refuse(where, f"signature {item['label']!r} has magic {magic!r}, not hex")
        offset = item.get("offset", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise _refuse(where, f"signature {item['label']!r} has an offset that is no count")
        found.append(FormatSignature(str(item["label"]), bytes.fromhex(magic), offset))
    return tuple(found)


def _family_from(term: str, raw: object) -> Family:
    where = f"family {term!r}"
    if not _TERM.match(term):
        raise _refuse(where, "a family is a Dictionary term: lower-case words joined by _")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _refuse(where, "a family is a mapping")
    unexpected = sorted(set(raw) - _FAMILY_KEYS)
    if unexpected:
        raise _refuse(where, f"carries {', '.join(unexpected)}, which nothing reads")
    also = raw.get("also_a_word")
    return Family(
        term=term,
        canonical=str(raw["canonical"]) if raw.get("canonical") else None,
        ambiguous_with=_strings(raw.get("ambiguous_with"), where=where, what="ambiguous_with"),
        program=raw.get("program") is True,
        product=raw.get("product") is True,
        declares_origins=raw.get("declares_origins") is True,
        also_a_word=_strings(also, where=where, what="also_a_word") if also is not None else None,
    )


def _row_from(raw: object, index: int) -> FileTypeRow:
    where = f"row {index + 1}"
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]:
        raise _refuse(where, "a row is a mapping with a name")
    where = f"row {raw['name']!r}"
    unexpected = sorted(set(raw) - _ROW_KEYS)
    if unexpected:
        raise _refuse(where, f"carries {', '.join(unexpected)}, which nothing reads")
    extensions = _strings(raw.get("extensions"), where=where, what="extensions")
    if not extensions:
        raise _refuse(where, "a row names at least one extension")
    for extension in extensions:
        if not _EXTENSION.match(extension):
            raise _refuse(where, f"{extension!r} is not an extension: lower-case, no dot first")
    category = str(raw.get("category", ""))
    if category not in CATEGORIES:
        raise _refuse(where, f"category is one of {', '.join(sorted(CATEGORIES))}")
    can_be_raw = raw.get("can_be_raw")
    can_be_raw = {True: "yes", False: "no"}.get(can_be_raw, can_be_raw)  # type: ignore[arg-type]
    if can_be_raw not in CAN_BE_RAW:
        raise _refuse(where, f"can_be_raw is one of {', '.join(sorted(CAN_BE_RAW))}")
    also_raw = raw.get("also_a_word")
    also: tuple[str, ...] | bool
    if also_raw in (None, False):
        also = ()
    elif also_raw in ("all", True):
        also = True
    else:
        also = _strings(also_raw, where=where, what="also_a_word")
        if set(also) - set(extensions):
            raise _refuse(where, "also_a_word names extensions of its own row")
    written_by = raw.get("written_by")
    if written_by is not None and written_by not in _WRITTEN_BY:
        raise _refuse(where, f"written_by is one of {', '.join(sorted(_WRITTEN_BY))}")
    if written_by is None and (raw.get("vendor") or raw.get("accepts_pack")):
        raise _refuse(where, "vendor and accepts_pack go with written_by")
    media_type = raw.get("media_type")
    if media_type is not None and (not isinstance(media_type, str) or "/" not in media_type):
        raise _refuse(where, f"media_type {media_type!r} is not a type/subtype")
    family = raw.get("family")
    if family is not None and not (isinstance(family, str) and _TERM.match(family)):
        raise _refuse(where, f"family {family!r} is not a Dictionary term")
    return FileTypeRow(
        name=str(raw["name"]),
        extensions=extensions,
        category=category,
        can_be_raw=str(can_be_raw),
        family=family,
        media_type=media_type,
        signatures=_signatures_from(raw.get("signatures"), where=where),
        built_on=str(raw["built_on"]) if raw.get("built_on") else None,
        domains=_strings(raw.get("domains"), where=where, what="domains"),
        writers=_strings(raw.get("writers"), where=where, what="writers"),
        also_a_word=also,
        wraps=raw.get("wraps") is True,
        written_by=str(written_by) if written_by else None,
        vendor=str(raw["vendor"]) if raw.get("vendor") else None,
        accepts_pack=raw.get("accepts_pack") is True,
    )


def _container_from(raw: object, index: int) -> Container:
    where = f"container {index + 1}"
    if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
        raise _refuse(where, "a container is a mapping with a key, a name and a media type")
    where = f"container {raw['key']!r}"
    media_type = raw.get("media_type")
    if not isinstance(media_type, str) or "/" not in media_type:
        raise _refuse(where, "a container states its media type")
    signatures = _signatures_from(raw.get("signatures"), where=where)
    if not signatures:
        raise _refuse(where, "a container owns at least one signature")
    return Container(str(raw["key"]), str(raw.get("name", raw["key"])), media_type, signatures)


def _named_from(
    raw: object, index: int, *, what: str, kinds: frozenset[str] | None
) -> tuple[str, str, tuple[str, ...], str | None]:
    where = f"{what} {index + 1}"
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]:
        raise _refuse(where, f"a {what} is a mapping with a name")
    where = f"{what} {raw['name']!r}"
    kind = str(raw.get("kind", ""))
    if kinds is not None and kind not in kinds:
        raise _refuse(where, f"kind is one of {', '.join(sorted(kinds))}")
    if not kind:
        raise _refuse(where, "says what kind of thing it is")
    spellings = _strings(raw.get("spellings"), where=where, what="spellings")
    if not spellings or any(spelling != spelling.casefold() for spelling in spellings):
        raise _refuse(where, "spellings are lower-case and at least one")
    vendor = raw.get("vendor")
    return str(raw["name"]), kind, spellings, str(vendor) if vendor else None


def _identifier_from(raw: object, index: int) -> IdentifierPattern:
    where = f"identifier {index + 1}"
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        raise _refuse(where, "an identifier is a mapping with a name and a pattern")
    pattern = raw.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise _refuse(f"identifier {raw['name']!r}", "carries a pattern")
    try:
        re.compile(pattern)
    except re.error as error:
        raise _refuse(f"identifier {raw['name']!r}", f"pattern does not compile: {error}") from None
    return IdentifierPattern(str(raw["name"]), pattern)


def table_from(document: Mapping[str, Any]) -> FileTypeTable:
    """Build the table from a parsed document, refusing in words whatever cannot be read."""
    families = {
        str(term): _family_from(str(term), raw)
        for term, raw in (document.get("families") or {}).items()
    }
    rows = tuple(_row_from(raw, index) for index, raw in enumerate(document.get("formats") or ()))
    containers = tuple(
        _container_from(raw, index) for index, raw in enumerate(document.get("containers") or ())
    )
    writer_rows = tuple(
        Writer(*_named_from(raw, index, what="writer", kinds=WRITER_KINDS))
        for index, raw in enumerate(document.get("writers") or ())
    )
    instrument_rows = tuple(
        Instrument(
            *_named_from(raw, index, what="instrument", kinds=None),
            ambiguous=bool(raw.get("ambiguous", False)) if isinstance(raw, dict) else False,
        )
        for index, raw in enumerate(document.get("instruments") or ())
    )
    identifier_rows = tuple(
        _identifier_from(raw, index) for index, raw in enumerate(document.get("identifiers") or ())
    )
    _check_table(rows, families, containers, writer_rows, instrument_rows)
    by_extension = {extension: row for row in rows for extension in row.extensions}
    return FileTypeTable(
        rows=rows,
        families=families,
        containers=containers,
        writers=writer_rows,
        instruments=instrument_rows,
        identifiers=identifier_rows,
        digest=digest_text(canonical_json(_plain(document))),
        _by_extension=by_extension,
    )


def _plain(value: object) -> object:
    """A YAML value as JSON holds it."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _check_table(
    rows: tuple[FileTypeRow, ...],
    families: Mapping[str, Family],
    containers: tuple[Container, ...],
    writer_rows: tuple[Writer, ...],
    instrument_rows: tuple[Instrument, ...],
) -> None:
    """Refuse what the rows say about each other that cannot be so."""
    names = [row.name for row in rows]
    if len(set(names)) != len(names):
        doubled = sorted({name for name in names if names.count(name) > 1})
        raise _refuse("rows", f"two rows are named {', '.join(doubled)}")
    seen: dict[str, str] = {}
    for row in rows:
        for extension in row.extensions:
            if extension in seen:
                raise _refuse(
                    f"row {row.name!r}", f"{extension!r} is also row {seen[extension]!r}'s"
                )
            seen[extension] = row.name
    writer_names = {writer.name for writer in writer_rows}
    if len(writer_names) != len(writer_rows):
        raise _refuse("writers", "two writers share a name")
    if len({instrument.name for instrument in instrument_rows}) != len(instrument_rows):
        raise _refuse("instruments", "two instruments share a name")
    keys = {container.key for container in containers}
    if len(keys) != len(containers):
        raise _refuse("containers", "two containers share a key")
    keys |= {row.key for row in rows if row.signatures}
    if any(container.key in seen for container in containers):
        raise _refuse("containers", "a container's key is also an extension")
    with_rows = {row.family for row in rows if row.family is not None}
    for term in families:
        if term not in with_rows:
            raise _refuse(f"family {term!r}", "no row belongs to it")
    for row in rows:
        where = f"row {row.name!r}"
        if row.family is not None and row.family not in families:
            raise _refuse(where, f"family {row.family!r} is not in the families map")
        if row.built_on is not None and row.built_on not in keys:
            raise _refuse(where, f"built_on {row.built_on!r} names no container or signed row")
        if row.built_on is not None and row.built_on == row.key:
            raise _refuse(where, "a row is not built on itself")
        if row.signatures and row.media_type is None:
            raise _refuse(where, "a row owning a signature states its media type")
        for writer in row.writers:
            if writer not in writer_names:
                raise _refuse(where, f"writer {writer!r} is not in the writers list")
        for domain in row.domains:
            if not _TERM.match(domain):
                raise _refuse(where, f"domain {domain!r} is not a pack's name")
    for term, declared in families.items():
        family_rows = [row for row in rows if row.family == term]
        sections = {row.section for row in family_rows}
        if declared.also_a_word is not None:
            if len(sections) != 1:
                raise _refuse(f"family {term!r}", "an own also_a_word list needs one section")
            spelled = {extension for row in family_rows for extension in row.spelled_after_a_dot}
            if set(declared.also_a_word) != spelled:
                raise _refuse(f"family {term!r}", "its also_a_word list and its rows' disagree")
        if len(sections) > 1 and CORE_SECTION not in sections:
            raise _refuse(f"family {term!r}", "a family in two packs has no core sense to extend")


#: The C loader where the installed PyYAML has one: the table is read at import by type
#: detection, and the pure loader took a quarter of a second to the C loader's thirtieth.
_LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@cache
def file_type_table() -> FileTypeTable:
    """The shipped table, read once."""
    document = yaml.load(TABLE_PATH.read_text(encoding="utf-8"), Loader=_LOADER) or {}  # noqa: S506
    if not isinstance(document, dict):
        raise _refuse("the file", "is a mapping")
    return table_from(document)


def writers() -> tuple[Writer, ...]:
    """The programs found in files' tags, each marked acquisition, analysis or figure."""
    return file_type_table().writers


def instruments() -> tuple[Instrument, ...]:
    """The instruments a laboratory names folders after."""
    return file_type_table().instruments


def identifiers() -> tuple[IdentifierPattern, ...]:
    """The public archives' accessions the tokeniser keeps whole."""
    return file_type_table().identifiers


# ---------------------------------------------------------------------------
# File types as a scope.
# ---------------------------------------------------------------------------


@cache
def extension_domains() -> dict[str, frozenset[str]]:
    """Each extension that belongs to a domain, casefolded, with the domains it belongs to.

    One function reads both sources, so a caller never chooses between them. It reads the file-type
    table (``dictionary/file_types.yaml``) where the tree holds one, each row of its ``formats``
    list with its ``extensions`` and ``domains``. Otherwise it reads the shipped domain sections'
    format senses as declared, before an extension folds into the core's type (an ND2 image is
    microscopy's, a FASTQ file sequencing's, an ABF trace electrophysiology's). An extension that
    only the universal sections spell belongs to no domain and is absent.
    """
    import yaml

    from ingence.dictionary.store import PACKS_DIRECTORY, load_sections

    found: dict[str, set[str]] = {}
    table = Path(__file__).with_name("file_types.yaml")
    if table.is_file():
        document = yaml.safe_load(table.read_text(encoding="utf-8")) or {}
        for raw in document.get("formats", ()) or ():
            if not isinstance(raw, dict):
                continue
            domains = raw.get("domains") or ()
            for spelling in raw.get("extensions") or ():
                found.setdefault(str(spelling).casefold(), set()).update(str(d) for d in domains)
        return {spelling: frozenset(domains) for spelling, domains in sorted(found.items())}
    for section in load_sections(PACKS_DIRECTORY):
        for sense in section.senses:
            if sense.theme_kind not in EXTENSION_KINDS:
                continue
            listed = sense.properties.get("domains")
            domains = {sense.domain} if sense.domain else set()
            if isinstance(listed, (list, tuple)):
                domains.update(str(item) for item in listed if item)
            if not domains:
                continue
            for spelling in sense.surfaces:
                found.setdefault(spelling.casefold(), set()).update(domains)
    return {spelling: frozenset(domains) for spelling, domains in sorted(found.items())}


@dataclass(frozen=True, slots=True)
class FileType:
    """One kind of file a person may scope a statement to."""

    #: The Dictionary term, which is the scope's value.
    term: str
    #: How to say it in a sentence.
    canonical: str
    #: Every extension that binds to it, casefolded and without a dot.
    spellings: tuple[str, ...]
    #: Which section declares it, and the field it belongs to where it has one.
    section: str
    domain: str | None = None

    @property
    def scope(self) -> Scope:
        """Return the scope that names this type."""
        return Scope(ScopeKind.FILE_TYPE, self.term)


def known_file_types(store: DictionaryStore) -> tuple[FileType, ...]:
    """Return every file type the composed Dictionary distinguishes, in term order.

    Derived from the vocabulary rather than listed beside it, so a row in the table is the
    whole of adding a type and nothing can fall out of step with it.
    """
    types = [
        FileType(
            term=sense.term,
            canonical=sense.canonical,
            spellings=tuple(sorted({surface.casefold() for surface in sense.surfaces})),
            section=sense.section,
            domain=sense.domain,
        )
        for sense in store.senses()
        if sense.theme_kind in EXTENSION_KINDS
    ]
    return tuple(sorted(types, key=lambda kind: kind.term))


def file_type_scope(store: DictionaryStore, spelling: str) -> Scope | None:
    """Return the scope one spelling of a file type names, or None if it names none.

    Accepts what a person types -- ``.TIF``, ``tif``, ``image_format`` -- and answers
    with the type's own term, so two people scoping a statement to the same kind of
    file write the same scope. ``None`` where the Dictionary knows no such type, which
    the caller reports rather than inventing a type from the spelling: a scope whose
    value nothing recognises would silently apply to nothing.
    """
    wanted = spelling.strip().lstrip(".").casefold()
    if not wanted:
        return None
    hit = store.look_up(wanted, permitted_kinds=EXTENSION_KINDS)
    if hit is not None:
        return Scope(ScopeKind.FILE_TYPE, hit.sense.term)
    for kind in known_file_types(store):
        if kind.term.casefold() == wanted:
            return kind.scope
    return None


def file_type_of(store: DictionaryStore, entry: Entry) -> Scope | None:
    """Return the file type one entry is of, or None where its extension names none.

    The compound extension is preferred where the Dictionary knows it, which is the
    same choice :func:`~ingence.dictionary.discover.split_extension` makes and for the
    same reason: ``archive.tar.gz`` is one format spelled in two parts, and
    ``entry.schema.json`` is a convention wrapped around a JSON file.
    """
    for suffix in (entry.compound_extension, entry.extension):
        if not suffix:
            continue
        scope = file_type_scope(store, suffix)
        if scope is not None:
            return scope
    return None


def declared_compounds(store: DictionaryStore) -> frozenset[str]:
    """Return every file type's spelling written in two parts, such as ``ome.tif``.

    The census admits a compound extension where both halves are extensions *or*
    where the Dictionary declares the whole spelling, and this is that declaration read
    back: a format's own surfaces holding a dot, casefolded and without a leading one. Data
    rather than a list in the rule, so a laboratory's pack declares its own conventions and
    nothing in the census names one.
    """
    return frozenset(
        spelling
        for kind in known_file_types(store)
        for spelling in kind.spellings
        if "." in spelling.strip(".")
    )


def instrument_spellings(store: DictionaryStore) -> frozenset[str]:
    """Return every format spelling the vocabulary declares an instrument writes.

    The function reads each format sense's ``origins``: a spelling, casefolded, whose
    ``written_by`` is ``instrument``. A dive's order reads the result to let an instrument's own
    spelling lead its family's turns. A sense that declares no origins contributes nothing.
    """
    from ingence.dictionary.store import ORIGINS, WRITTEN_BY

    found: set[str] = set()
    for sense in store.senses():
        if sense.theme_kind not in EXTENSION_KINDS:
            continue
        declared = sense.properties.get(ORIGINS)
        if not isinstance(declared, dict):
            continue
        for spelling, origin in declared.items():
            if isinstance(origin, dict) and origin.get(WRITTEN_BY) == "instrument":
                found.add(str(spelling).casefold())
    return frozenset(found)
