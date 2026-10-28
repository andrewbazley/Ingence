"""A pack's manifest: its name, version, domain, author, licence, the schema it needs, its digest.

Every shipped pack carries a manifest, and so does a laboratory's pack that ``teach export-answers``
writes. ``teach pack install <file or address>`` reads it.

The manifest lives in a ``pack:`` block at the top of the pack's section file. So a pack is one
file that travels whole (from a colleague, from the Dictionary page's export, or from an address),
and one read says what it is. A laboratory's pack installed as a directory keeps its ``pack.yaml``
beside its sections, which may name the same fields. Installing a single file writes that
``pack.yaml`` from the block.

The digest is over the pack's words, not over its bytes. It covers each section file's content as
read, without its ``pack:`` block, in canonical JSON, in the order the files sort. A comment or a
reflowed line changes nothing a run reads, so it changes no digest. A changed, added or removed
spelling does. The engine refuses a pack whose words do not match its digest. It also refuses a
pack that needs a newer schema than this engine reads. Nothing of a refused pack is installed.

A manifest never carries a decision. A pack adds vocabulary and decides nothing, and no field here
changes what a run concludes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ingence import SCHEMA_VERSION
from ingence.core.ids import canonical_json, digest_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "FIELDS",
    "MANIFEST_KEY",
    "Manifest",
    "manifest_problems",
    "read_manifest",
    "schema_problem",
    "with_manifest",
    "words_digest",
]

#: The key a section file's manifest sits under.
MANIFEST_KEY = "pack"

#: What a manifest says, in the order it is written.
FIELDS = ("name", "version", "domain", "author", "licence", "schema", "digest")


@dataclass(frozen=True, slots=True)
class Manifest:
    """What a pack says about itself. Empty text where a field is not given."""

    name: str
    version: str
    domain: str = ""
    author: str = ""
    licence: str = ""
    #: The schema the pack needs, ``major.minor``.
    schema: str = ""
    #: :func:`words_digest` over the pack's section files.
    digest: str = ""
    #: False where the schema was written as a number (``schema: 1.30`` reads as 1.3).
    schema_quoted: bool = True


def _plain(value: object) -> object:
    """A YAML value as JSON can hold it: a date or a time the loader made is written as text."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def words_digest(documents: Sequence[Mapping[str, Any]]) -> str:
    """Return the digest of a pack's words: each section document without its manifest block."""
    return digest_text(
        canonical_json(
            [
                _plain({key: value for key, value in document.items() if key != MANIFEST_KEY})
                for document in documents
            ]
        )
    )


def read_manifest(block: object) -> Manifest | None:
    """Return the manifest a ``pack:`` block or a ``pack.yaml`` holds, or None if not a map."""
    if not isinstance(block, dict):
        return None

    def said(key: str) -> str:
        value = block.get(key)
        return "" if value is None else str(value)

    written = block.get("schema")
    return Manifest(
        **{key: said(key) for key in FIELDS},
        schema_quoted=not isinstance(written, int | float) or isinstance(written, bool),
    )


def _version(text: str) -> tuple[int, int] | None:
    parts = text.strip().split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def schema_problem(needed: str, *, reads: str = SCHEMA_VERSION) -> str | None:
    """Why this engine cannot read a pack needing a schema, in words; None where it can.

    The pack needs the same major version and a minor no newer than this engine's. A schema adds
    fields compatibly within a major version, so a pack written for an older minor loads, and one
    written for a newer minor may use what this engine does not know.
    """
    wanted, held = _version(needed), _version(reads)
    if wanted is None:
        return f"its manifest names schema {needed!r}, which is not a version such as {reads}"
    if held is None or wanted[0] != held[0] or wanted > held:
        newer = held is not None and wanted > held
        return f"it needs schema {needed}, and this Ingence reads {reads}" + (
            ": update Ingence to install it" if newer else ""
        )
    return None


def manifest_problems(
    manifest: Manifest, documents: Sequence[Mapping[str, Any]], *, required: bool = False
) -> list[str]:
    """What is wrong with a manifest against the pack's words, in plain words; empty where nothing.

    A digest and a schema are checked where the manifest names them, and required where the
    pack came from an address, since a download that says neither cannot be checked.
    """
    problems: list[str] = []
    if required:
        missing = [key for key in ("schema", "digest") if not getattr(manifest, key)]
        if missing:
            problems.append(
                f"its manifest names no {' and no '.join(missing)}, so what arrived cannot be "
                "checked"
            )
    if manifest.schema and not manifest.schema_quoted:
        problems.append(
            f"its manifest's schema reads as the number {manifest.schema}; write it in quotes, "
            f'such as "{SCHEMA_VERSION}"'
        )
    elif manifest.schema:
        found = schema_problem(manifest.schema)
        if found is not None:
            problems.append(found)
    if manifest.digest:
        held = words_digest(documents)
        if held != manifest.digest:
            problems.append(
                f"its words do not match its digest (the manifest says {manifest.digest[:12]}, "
                f"the words are {held[:12]}): it changed after it was written, or arrived "
                "broken; where you changed it yourself, delete its digest line"
            )
    return problems


def with_manifest(document: Mapping[str, Any], manifest: Manifest) -> dict[str, Any]:
    """Return a section document with its manifest block first, the digest and schema filled in."""
    words = {key: value for key, value in document.items() if key != MANIFEST_KEY}
    block = {
        "name": manifest.name,
        "version": manifest.version,
        **({"domain": manifest.domain} if manifest.domain else {}),
        "author": manifest.author,
        "licence": manifest.licence,
        "schema": manifest.schema or SCHEMA_VERSION,
        "digest": words_digest([words]),
    }
    return {MANIFEST_KEY: block, **words}
