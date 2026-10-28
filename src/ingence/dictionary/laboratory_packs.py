"""A pack a laboratory declares: vocabulary installed into a project and loaded by name.

A pack is a directory. It holds a ``pack.yaml`` manifest that names the pack, its version and a
one-line summary, and one or more section files in exactly the format the shipped sections use.
Installing a pack copies it into the project (``dictionary/packs/<name>/``) and names it in
``settings.yaml``. A run loads the packs its settings name, in that order, and nothing else.

A pack also travels as one file: a section file whose ``pack:`` block is its manifest
(:mod:`~ingence.dictionary.pack_manifest`). The install writes the manifest out as the pack's
``pack.yaml``, as it does for a directory. A pack also installs from an address, and only when a
person runs the command (``teach pack install https://...``). The engine contacts no address on its
own. The command says what it fetches and from where, waits a set time and reads a set size at most.
It installs only a file whose manifest names its schema and a digest that its words match. There is
no registry: an address is whatever a person names. Inactivations never travel, so a new download of
a dictionary means every word is active. Nothing here reads a list of inactivations in a file.

A pack adds vocabulary and decides nothing. Its senses carry the ``pack`` origin, which stands
level with the core. So a spelling that a pack and the core both claim is a tie that resolves to
neither and is reported, never a silent win. Only a person's answer, at ``USER``'s precedence,
chooses. Installing prints those collisions.

A run says which packs it read: the name, the version and a digest over the pack's own files,
beside the vocabulary's fingerprint. So reproducing a run means installing what it names.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import yaml

from ingence import __version__
from ingence.core.ids import digest_text
from ingence.core.schema import DictionaryOrigin
from ingence.dictionary.pack_manifest import (
    FIELDS,
    MANIFEST_KEY,
    Manifest,
    manifest_problems,
    read_manifest,
)
from ingence.storage.layout import DICTIONARY_DIR, STAGING_DIR, owned_path

if TYPE_CHECKING:
    from ingence.core.schema import PackInForce
    from ingence.dictionary.store import Section

__all__ = [
    "DOWNLOAD_BYTES",
    "DOWNLOAD_SECONDS",
    "MANIFEST",
    "InstalledPack",
    "PackError",
    "check_pack",
    "check_packs_folder",
    "fetch_pack",
    "in_force",
    "install_pack",
    "install_pack_from",
    "install_pack_text",
    "installed_packs",
    "is_address",
    "is_pack_name",
    "load_named_packs",
    "packs_directory",
    "read_pack",
    "remove_pack",
    "section_problems",
    "shipped_pack_names",
]

#: The file that makes a directory a pack.
MANIFEST = "pack.yaml"

#: The log of packs installed and removed on the Dictionary page, one JSON line each, with the
#: moment (``at``) and the act (``install`` or ``remove``). ``workspace.state`` reads it so that a
#: change waits for Re-run.
PACK_CHANGES = "workspace/pack_changes.jsonl"

#: A pack's name is a directory name and a settings entry, so it is spelled like an identifier.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
_NOT_NAME = re.compile(r"[^a-z0-9_\-]+")

#: The most a pack fetched from an address may weigh: 4 MB, as the Dictionary page installs,
#: well above any shipped pack (the largest is under 20 KB). ``--largest`` changes it.
DOWNLOAD_BYTES = 4 * 1024 * 1024
#: How long a fetch waits for the address to answer, in seconds. ``--timeout`` changes it.
DOWNLOAD_SECONDS = 30.0


class PackError(ValueError):
    """A directory is not a pack, or a pack cannot be installed, removed or loaded as asked."""


@dataclass(frozen=True, slots=True)
class InstalledPack:
    """One pack: what its manifest says, where it is, and a digest over its files."""

    name: str
    version: str
    summary: str
    #: A digest over every section file and the manifest, by relative path and content, in
    #: canonical order, so two copies of one pack digest alike wherever they sit.
    digest: str
    path: Path


def packs_directory(project: Path) -> Path:
    """Return where a project keeps the packs installed into it."""
    return project / DICTIONARY_DIR / "packs"


def read_pack(directory: Path) -> InstalledPack:
    """Read a pack's manifest and digest its files, refusing a directory that is not a pack."""
    manifest_path = directory / MANIFEST
    if not manifest_path.is_file():
        message = f"{directory} holds no {MANIFEST}, so it is not a pack"
        raise PackError(message)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    name = str(manifest.get("name", ""))
    if not _NAME.fullmatch(name):
        message = (
            f"{manifest_path}: a pack's name is lower-case letters, digits, '_' and '-', "
            f"and {name!r} is not"
        )
        raise PackError(message)
    sections = sorted(path for path in directory.rglob("*.yaml") if path.name != MANIFEST)
    if not sections:
        message = f"{directory} declares a pack and holds no section file"
        raise PackError(message)
    files = sorted(
        [manifest_path, *sections], key=lambda path: path.relative_to(directory).as_posix()
    )
    digest = digest_text(
        "\n".join(
            f"{path.relative_to(directory).as_posix()}\n{path.read_text(encoding='utf-8')}"
            for path in files
        )
    )
    return InstalledPack(
        name=name,
        version=str(manifest.get("version", "")),
        summary=str(manifest.get("summary", "")),
        digest=digest,
        path=directory,
    )


def installed_packs(project: Path) -> tuple[InstalledPack, ...]:
    """Return every pack installed into a project, by name, whether or not a run loads it."""
    root = packs_directory(project)
    if not root.is_dir():
        return ()
    return tuple(
        read_pack(directory)
        for directory in sorted(root.iterdir())
        if directory.is_dir() and (directory / MANIFEST).is_file()
    )


def load_named_packs(
    project: Path, names: tuple[str, ...]
) -> tuple[tuple[InstalledPack, ...], tuple[Section, ...]]:
    """Return the packs a run's settings name, in that order, and their sections at ``pack`` origin.

    The function refuses a named pack that is not installed, by name. A run that silently read less
    vocabulary than its settings say would answer differently from what the settings file promises.
    This is the loader's own reason for refusing a revision that names a retired predicate.
    """
    from ingence.dictionary.store import load_sections

    found = {pack.name: pack for pack in installed_packs(project)}
    missing = [name for name in names if name not in found]
    if missing:
        message = (
            f"settings name pack(s) {', '.join(missing)}, which are not installed in "
            f"{packs_directory(project)}; install them with 'ingence teach pack install', or "
            f"remove them from dictionary.packs"
        )
        raise PackError(message)
    packs = tuple(found[name] for name in names)
    # The manifest is YAML too and loads as a section holding no sense, which adds nothing.
    sections = tuple(
        section
        for pack in packs
        for section in load_sections(pack.path, origin=DictionaryOrigin.PACK)
        if section.senses
    )
    return packs, sections


def section_problems(document: object, where: str) -> list[str]:
    """What is wrong with one section file's entries, in plain words; empty where nothing is.

    The check runs before a pack is installed. The loader turns whatever it is given into
    spellings, so a bare word for the spellings would become one spelling per letter, and a number
    would stop every read of the Dictionary, including the page a person would remove the pack
    from. Each problem names the entry, by its place and its term.
    """
    if not isinstance(document, dict):
        return [f"{where} is not a section: it holds no named entries"]
    problems: list[str] = []
    if "theme_kind" in document and not isinstance(document["theme_kind"], str):
        problems.append(f"{where}: its kind is a word, and it reads {document['theme_kind']!r}")
    entries = document.get("entries")
    # A section may hold program layouts alone, as the core's program_layouts.yaml does: no
    # entries, and layouts the loader reads.
    layouts = document.get("layouts")
    if isinstance(entries, list) and not entries and isinstance(layouts, list) and layouts:
        return problems
    if not isinstance(entries, list) or not entries:
        return [*problems, f"{where} lists no entries"]
    for index, raw in enumerate(entries, start=1):
        if not isinstance(raw, dict):
            problems.append(f"{where}, entry {index} is not a term with its spellings")
            continue
        term = raw.get("term")
        if not isinstance(term, str) or not term.strip():
            problems.append(f"{where}, entry {index} has no term")
            continue
        label = f"{where}, entry {index} ({term})"
        surfaces = raw.get("surfaces")
        if surfaces is not None and not isinstance(surfaces, list):
            problems.append(
                f"{label}: its spellings are a list in brackets, such as [{term}, {term}s], "
                f"and they read {surfaces!r}"
            )
        elif surfaces is not None:
            # Every shipped spelling is text. A wavelength written without quotes reads as a
            # number, so a number in the list is said as what to write instead.
            for item in surfaces:
                if not isinstance(item, str):
                    problems.append(
                        f'{label}: spellings are text: write {item!r} as "{item}"'
                        if isinstance(item, int | float) and not isinstance(item, bool)
                        else f"{label}: spellings are text, and one reads {item!r}"
                    )
                elif not item.strip():
                    problems.append(f"{label}: a spelling is empty")
        for key, said in (("theme_kind", "its kind"), ("canonical", "its canonical name")):
            if key in raw and not isinstance(raw[key], str):
                problems.append(f"{label}: {said} is a word, and it reads {raw[key]!r}")
    return problems


def shipped_pack_names() -> frozenset[str]:
    """The names of the domain packs that ship with the engine, which no laboratory pack takes."""
    from ingence.dictionary.store import PACKS_DIRECTORY

    return frozenset(path.name for path in PACKS_DIRECTORY.iterdir() if path.is_dir())


def check_pack(source: Path) -> InstalledPack:
    """Read a pack whole before it is installed, refusing in words what cannot be loaded.

    Its manifest, every section file's entries (:func:`section_problems`), and a composition of
    the shipped vocabulary with the pack's sections made the way a run makes it; a name a shipped
    domain pack has is refused too.
    """
    from ingence.dictionary.store import DictionaryStore, load_sections, load_shipped_sections

    pack = read_pack(source)
    if pack.name in shipped_pack_names():
        message = f"{pack.name!r} is the name of a pack that ships with Ingence; choose another"
        raise PackError(message)
    problems: list[str] = []
    documents: list[Any] = []
    for path in sorted(
        (path for path in source.rglob("*.yaml") if path.name != MANIFEST),
        key=lambda path: path.relative_to(source).as_posix(),
    ):
        where = path.relative_to(source).as_posix()
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as error:
            problems.append(f"{where} is not YAML: {error}")
            continue
        problems.extend(section_problems(document, where))
        documents.append(document)
    if problems:
        raise PackError("; ".join(problems))
    # The manifest, where it names a schema or a digest: a pack that needs a newer schema, or whose
    # words are not the ones its digest was written over, is refused.
    manifest = read_manifest(yaml.safe_load((source / MANIFEST).read_text(encoding="utf-8")) or {})
    if manifest is not None:
        found = manifest_problems(manifest, documents)
        if found:
            raise PackError(f"{pack.name} is not installed: " + "; ".join(found))
    try:
        DictionaryStore(
            (*load_shipped_sections(), *load_sections(source, origin=DictionaryOrigin.PACK))
        )
    except Exception as error:
        message = f"{pack.name} cannot be read as a pack: {error}"
        raise PackError(message) from error
    return pack


def install_pack(project: Path, source: Path) -> InstalledPack:
    """Copy a pack into a project and name it in the project's settings; return what was installed.

    The function refuses to replace an installed pack of the same name whose files differ, because
    that is a new version and should say so. Reinstalling the same files does nothing. It reads the
    pack whole first (:func:`check_pack`), so nothing is copied or named where the pack cannot be
    loaded.
    """
    from ingence.core.project import Project

    pack = check_pack(source)
    target = check_packs_folder(project) / pack.name
    if target.exists():
        present = read_pack(target)
        if present.digest != pack.digest:
            message = (
                f"a different pack named {pack.name!r} is already installed (version "
                f"{present.version!r}); remove it first to install version {pack.version!r}"
            )
            raise PackError(message)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    opened = Project.open(project)
    loaded = opened.settings.dictionary.packs
    if pack.name not in loaded:
        opened.save_settings(
            opened.settings.model_copy(
                update={
                    "dictionary": opened.settings.dictionary.model_copy(
                        update={"packs": (*loaded, pack.name)}
                    )
                }
            )
        )
    return read_pack(target)


def is_address(source: str) -> bool:
    """Whether a source names a web address rather than a file or a folder on this machine."""
    return source.strip().lower().startswith(("https://", "http://"))


def fetch_pack(
    address: str,
    *,
    timeout: float = DOWNLOAD_SECONDS,
    largest: int = DOWNLOAD_BYTES,
    chunk: int = 64 * 1024,
) -> tuple[str, str, str]:
    """Fetch one pack file from an address; return its file name, its text and where it ended.

    This runs only when a person runs the command (the engine contacts no address on its own).
    Nothing else calls it. The whole fetch takes ``timeout`` seconds at most and reads ``largest``
    bytes at most. A socket's own wait would let a server that sends a byte at a time hold the fetch
    far longer, so the function keeps its own deadline. It refuses in words an address that does not
    answer, answers with an error, sends more, or is too slow. It returns the address it ended at,
    after any redirect, so the command can say it.
    """
    import time

    # Imported here, never with the module: every census reads this module, and a census loads no
    # network client (``test_the_census_imports_no_network_client`` holds this).
    import urllib.error
    import urllib.parse
    import urllib.request

    parsed = urllib.parse.urlsplit(address.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        message = f"{address} is not a web address (https://...)"
        raise PackError(message)
    request = urllib.request.Request(  # noqa: S310 -- the scheme is checked above
        address.strip(), headers={"User-Agent": f"ingence/{__version__}"}
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            final = str(response.geturl() or address.strip())
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > largest:
                message = f"{address} is larger than {largest:,} bytes, the most a pack may be"
                raise PackError(message)
            body = b""
            while len(body) <= largest:
                # One deadline covers the whole fetch. Each wait is given only the time left, and
                # ``read1`` returns what has arrived rather than a whole chunk, so a server
                # trickling bytes cannot hold the command past its deadline.
                left = timeout - (time.monotonic() - started)
                if left <= 0:
                    message = f"{address} did not finish within {timeout:g} s"
                    raise PackError(message)
                raw = getattr(getattr(response, "fp", None), "raw", None)
                sock = getattr(raw, "_sock", None)
                if sock is not None:
                    sock.settimeout(left)
                piece = response.read1(chunk)
                if not piece:
                    break
                body += piece
    except PackError:
        raise
    except urllib.error.HTTPError as error:
        message = (
            f"{address} could not be fetched: the address answered {error.code} {error.reason}"
        )
        raise PackError(message) from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        message = f"{address} could not be fetched: {reason}"
        raise PackError(message) from error
    if len(body) > largest:
        message = f"{address} is larger than {largest:,} bytes, the most a pack may be"
        raise PackError(message)
    name = PurePosixPath(urllib.parse.unquote(urllib.parse.urlsplit(final).path)).name
    try:
        return name or "pack.yaml", body.decode("utf-8"), final
    except UnicodeDecodeError as error:
        message = f"{address} is not a text file, so it is not a pack"
        raise PackError(message) from error


def install_pack_text(
    project: Path, filename: str, text: str, *, fetched: bool = False
) -> InstalledPack:
    """Install one pack file (a section, its manifest in a ``pack:`` block) into a project.

    The function wraps the file as a pack directory, with the manifest written out as ``pack.yaml``
    beside the section, and installs it as :func:`install_pack` installs one. The function checks
    the manifest. It refuses in words a schema newer than this engine's, or words that do not match
    the digest, and installs nothing. ``fetched`` (an address) requires the manifest to name both. A
    file without a manifest is named by its section, as the Dictionary page installs one.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        message = f"{filename} is not YAML: {error}"
        raise PackError(message) from error
    problems = section_problems(document, filename)
    if problems:
        raise PackError("; ".join(problems))
    manifest = read_manifest(document.get(MANIFEST_KEY)) or (
        Manifest(name="", version="") if fetched else None
    )
    if manifest is not None:
        found = manifest_problems(manifest, [document], required=fetched)
        if found:
            raise PackError(f"{filename} is not installed: " + "; ".join(found))
    raw = (manifest.name if manifest is not None else "") or str(
        document.get("section") or filename.rsplit(".", 1)[0]
    )
    name = _NOT_NAME.sub("-", raw.casefold()).strip("-_")[:64] or "pack"
    if manifest is not None and manifest.name and manifest.name != name:
        message = (
            f"{filename}: a pack's name is lower-case letters, digits, '_' and '-', "
            f"and {manifest.name!r} is not"
        )
        raise PackError(message)
    written: dict[str, object] = {
        "name": name,
        "version": (manifest.version if manifest is not None else "")
        or str(document.get("version") or "1"),
        "summary": str(document.get("summary") or f"Installed from {filename}"),
    }
    if manifest is not None:
        written.update(
            {key: getattr(manifest, key) for key in FIELDS[2:] if getattr(manifest, key)}
        )
    # The project's own staging, nothing on the way to it a link, before it is made or removed.
    staging = owned_path(project, STAGING_DIR, f"pack-{uuid.uuid4().hex}")
    source = staging / name
    source.mkdir(parents=True)
    try:
        (source / MANIFEST).write_text(
            yaml.safe_dump(written, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        # Beside the manifest, never over it: a pack named ``pack`` keeps its words apart.
        words = f"{name}.yaml" if f"{name}.yaml" != MANIFEST else "words.yaml"
        (source / words).write_text(text, encoding="utf-8")
        return install_pack(project, source)
    finally:
        shutil.rmtree(owned_path(project, staging), ignore_errors=True)


def install_pack_from(
    project: Path,
    source: str,
    *,
    timeout: float = DOWNLOAD_SECONDS,
    largest: int = DOWNLOAD_BYTES,
) -> InstalledPack:
    """Install a pack from a folder, a file, or an address, whichever ``source`` names."""
    if is_address(source):
        filename, text, _ = fetch_pack(source, timeout=timeout, largest=largest)
        return install_pack_text(project, filename, text, fetched=True)
    path = Path(source).expanduser()
    if path.is_dir():
        return install_pack(project, path.resolve())
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            message = f"{path.name} is not a text file, so it is not a pack"
            raise PackError(message) from error
        return install_pack_text(project, path.name, text)
    message = f"{source} is neither a file, a folder nor a web address (https://...)"
    raise PackError(message)


def check_packs_folder(project: Path) -> Path:
    """Return the project's packs folder, once it is the project's own; else refuse in words.

    A link a person made at ``dictionary`` or ``dictionary/packs`` that leads out of the project
    (into the data the project reads, say) makes every pack-shaped folder there look installed.
    Nothing is installed into or removed from a folder that is not, by its real path,
    ``<the project's real path>/dictionary/packs``.
    """
    folder = packs_directory(project)
    own = os.path.join(os.path.realpath(project), DICTIONARY_DIR, "packs")
    if os.path.realpath(folder) != own:
        message = (
            f"{folder} leads to {os.path.realpath(folder)} through a link, not to a folder inside "
            "this project, so no pack is installed or removed there; nothing was changed. "
            "Remove the link to use packs in this project."
        )
        raise PackError(message)
    return folder


def is_pack_name(name: str) -> bool:
    """Whether ``name`` can be a pack's name: the rule an installed pack's name keeps."""
    return _NAME.fullmatch(name) is not None


def remove_pack(project: Path, name: str) -> None:
    """Stop loading a pack and delete the project's own copy of it.

    The copy is the project's, inside the project directory the engine writes to; the pack's
    source, wherever it came from, is never touched. The function takes only a name that an
    installed pack could have (a settings entry that is a path is refused, so it can name no
    folder). It also refuses a copy that is a link, or whose real place is not inside the
    project's packs folder. In every refusal, nothing is changed or deleted.
    """
    from ingence.core.project import Project

    if not is_pack_name(name):
        message = (
            f"{name!r} is not a pack's name: a name is lower-case letters, digits, '_' and '-'"
        )
        raise PackError(message)
    folder = check_packs_folder(project)
    target = folder / name
    if target.is_symlink() or (
        target.exists() and Path(os.path.realpath(target)).parent != Path(os.path.realpath(folder))
    ):
        message = (
            f"{target} is a link or lies outside the project's packs folder, not a copy the "
            "project holds; nothing was removed"
        )
        raise PackError(message)
    opened = Project.open(project)
    loaded = opened.settings.dictionary.packs
    if name in loaded:
        opened.save_settings(
            opened.settings.model_copy(
                update={
                    "dictionary": opened.settings.dictionary.model_copy(
                        update={"packs": tuple(item for item in loaded if item != name)}
                    )
                }
            )
        )
    if target.is_dir() and (target / MANIFEST).is_file():
        shutil.rmtree(owned_path(project, DICTIONARY_DIR, "packs", name))


def in_force(store: object) -> tuple[PackInForce, ...]:
    """Return what a run records of the packs a store loaded: name, version and digest."""
    from ingence.core.schema import PackInForce

    return tuple(
        PackInForce(name=pack.name, version=pack.version, digest=pack.digest)
        for pack in getattr(store, "packs", ())
    )
