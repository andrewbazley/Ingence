"""A project: the directory the engine writes into, and how it is opened.

A project is a directory, not a database. Everything the engine knows about a data pool lives
inside it, and it can be moved, copied, archived or deleted with ordinary filesystem tools.
Nothing outside it is touched: the data pool is never written to. A project may live anywhere,
inside a root it reads included, or as that root itself. Every census leaves out the project it
writes (``scan/recognise.py``), so the engine's own output never becomes part of what it observes.

Two files sit at the top and they are deliberately different in kind.
``project.json`` is machine-written identity and nobody should hand-edit it.
``settings.yaml`` is the tunables, meant to be read and edited by a person.
"""

from __future__ import annotations

import json
import os
import platform
import stat as stat_module
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from ingence import SCHEMA_VERSION, __version__
from ingence.core.destinations import write_atomically
from ingence.core.ids import CaseSensitivity, canonical_json, root_id
from ingence.core.settings import DASHBOARD_SETTINGS_FILE, SETTINGS_FILE, Settings
from ingence.listing.format import HEADER_FILE, ListingError, parse_listing_uri
from ingence.storage.layout import (
    CURRENT_POINTER,
    DICTIONARY_DIR,
    LISTINGS_DIR,
    PROJECT_FILE,
    PROJECT_FOLDERS,
    RECORD_DIR,
    RULEBOOK_DIR,
    RUNS_DIR,
    check_layout,
    is_project_name,
    spelled_otherwise,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "CASE_FROM_A_FLIPPED_NAME",
    "CASE_FROM_A_PAIR",
    "CASE_OF_A_LISTING",
    "CASE_UNSETTLED",
    "PROJECT_NAME_LONGEST",
    "NotAProjectError",
    "Project",
    "ProjectExistsError",
    "ProjectIdentity",
    "RootDeclaration",
    "RootUnreachableError",
    "SettingsFileError",
    "discover",
    "establish_case_sensitivity",
    "is_listing_root",
    "project_name",
    "read_settings_file",
    "refuse_unreachable_roots",
    "unreachable_roots",
]


class NotAProjectError(Exception):
    """The named directory is not an Ingence project."""


class ProjectExistsError(Exception):
    """A project already exists at the named directory."""


class SettingsFileError(ValueError):
    """A settings file that cannot be read, named, with what to do about it."""


class RootUnreachableError(Exception):
    """A verb that reads the data pool was asked to run while a declared root cannot be reached.

    Raised before anything is staged, so nothing is written and the current run stays current.
    """


class RootDeclaration(BaseModel):
    """One indexed root, as the project records it.

    The logical identifier is separate from the mount path, so a project survives relocation. Move
    a data pool and only ``mount_path`` changes. Every entry identifier in every past run stays
    valid.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_id: str
    #: The stable logical name. Defaults to the URI of the path at declaration
    #: time, which is a reasonable name, and can be set to anything durable.
    root_uri: str
    mount_path: str
    label: str | None = None
    case_sensitivity: CaseSensitivity = CaseSensitivity.UNKNOWN
    #: How the label was established, read-only, from the root's own names: one of
    #: :data:`CASE_FROM_A_PAIR`, :data:`CASE_FROM_A_FLIPPED_NAME` or :data:`CASE_UNSETTLED`. It is
    #: None for a root declared before this field existed. Such a root keeps the project
    #: directory's label, because the label decides every entry identifier the project has
    #: written.
    case_established_by: str | None = None

    @property
    def listing(self) -> tuple[str, str] | None:
        """The archive and identifier of a listing root, or None for a folder."""
        return parse_listing_uri(self.root_uri)


def is_listing_root(declaration: RootDeclaration) -> bool:
    """Whether a root is an archive's listing, read from its cache, rather than a folder."""
    return declaration.listing is not None


#: How a listing root's case label was established: an archive's paths are strings it compares
#: exactly, so two paths differing in case are two files. Keeping them apart is the recoverable
#: choice.
CASE_OF_A_LISTING = "a_listing_compares_paths_exactly"


class ProjectIdentity(BaseModel):
    """Machine-written identity of a project. Not for hand editing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    #: Version of the engine that created the project. A project created by a
    #: newer engine is refused by an older one rather than misread.
    created_by: str
    created_at: str
    roots: tuple[RootDeclaration, ...] = ()
    #: What the project is called: the name a person gave it, when it was made or since
    #: (:meth:`Project.rename`). It travels with the project, so another computer shows the name
    #: and not the folder's. It is None where nobody gave one, and then the file holds no ``name``
    #: at all (:func:`_write_identity`). A build that predates the field reads such a project and
    #: shows its folder's name.
    name: str | None = None


#: The longest name a project is given.
PROJECT_NAME_LONGEST = 200


def project_name(text: str | None, *, folder: str = "") -> str:
    """The name a project is given: ``text`` with its whitespace made single spaces, else
    ``folder``. Refused where it is longer than :data:`PROJECT_NAME_LONGEST` characters."""
    said = " ".join((text or "").split())
    if len(said) > PROJECT_NAME_LONGEST:
        message = f"a project's name is at most {PROJECT_NAME_LONGEST} characters"
        raise ValueError(message)
    return said or folder


#: Two names in one folder differing only in case: the filesystem keeps them apart.
CASE_FROM_A_PAIR = "two_names_differing_only_in_case"
#: A name met in the root, looked up with its case flipped, reported the same file.
CASE_FROM_A_FLIPPED_NAME = "flipped_name_is_the_same_file"
#: Nothing read settled it, and the label is UNKNOWN, which keeps files apart.
CASE_UNSETTLED = "unsettled"


def _listed(directory: Path) -> list[tuple[str, bool]]:
    """Return a folder's names and whether each is a folder, without following a link."""
    with os.scandir(directory) as found:
        return [(item.name, item.is_dir(follow_symlinks=False)) for item in found]


def establish_case_sensitivity(
    root: Path,
    *,
    max_directories: int = 32,
    list_names: Callable[[Path], list[tuple[str, bool]]] = _listed,
    stat: Callable[[Path], os.stat_result] = os.lstat,
) -> tuple[CaseSensitivity, str]:
    """Establish whether a root's filesystem tells names apart by case, writing nothing.

    Nothing writes to a root, so an experiment that writes a probe file and looks for it in
    another case cannot run here. The evidence is what reading the root already gives, taken in
    this order:

    - Two names in one folder that differ only in case prove SENSITIVE: the filesystem is holding
      two files an insensitive one could not.
    - A name met in the root, looked up with its case flipped, that reports the same file (the
      same device and inode) proves INSENSITIVE. Only an ASCII name is flipped, so the flip is a
      case change and never a normalisation.
    - Otherwise the label is UNKNOWN, which keeps two spellings apart. This is the recoverable
      direction, because merging two files that are not one cannot be undone
      (``canonical_relative_path``).

    The function returns the label and how it was established. ``max_directories`` bounds how many
    folders are read, breadth first, looking for a pair. The first folder holding a letter settles
    an insensitive root, so the bound costs only a sensitive root with no pair near the top.
    ``list_names`` and ``stat`` are the two reads. They are parameters so that each way the label
    can be established is testable on any platform.
    """
    queue = [root]
    read = 0
    flipped_once = False
    while queue and read < max_directories:
        directory = queue.pop(0)
        read += 1
        try:
            listed = sorted(list_names(directory))
        except OSError:
            continue
        spelled: dict[str, str] = {}
        for name, _ in listed:
            key = unicodedata.normalize("NFC", name).casefold()
            if key in spelled and spelled[key] != name:
                return CaseSensitivity.SENSITIVE, CASE_FROM_A_PAIR
            spelled.setdefault(key, name)
        names = {name for name, _ in listed}
        for name, _ in listed:
            flipped = name.swapcase()
            if flipped_once or not name.isascii() or flipped == name or flipped in names:
                continue
            try:
                original = stat(directory / name)
                other = stat(directory / flipped)
            except FileNotFoundError:
                # A case-sensitive lookup. It suggests the root is sensitive but does not prove
                # it, and it never shows the root is insensitive, so only a pair can settle
                # this root.
                flipped_once = True
                continue
            except OSError:
                continue
            flipped_once = True
            if original.st_ino and (original.st_dev, original.st_ino) == (
                other.st_dev,
                other.st_ino,
            ):
                return CaseSensitivity.INSENSITIVE, CASE_FROM_A_FLIPPED_NAME
        queue.extend(directory / name for name, is_folder in listed if is_folder)
    return CaseSensitivity.UNKNOWN, CASE_UNSETTLED


@dataclass(frozen=True, slots=True)
class Project:
    """An open project directory."""

    path: Path
    identity: ProjectIdentity
    settings: Settings

    # -- lifecycle --------------------------------------------------------

    @staticmethod
    def create(
        path: Path,
        *,
        created_at: str,
        roots: Sequence[Path] = (),
        settings: Settings | None = None,
        labels: Sequence[str | None] = (),
        listings: Sequence[str] = (),
        chosen: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> Project:
        """Create a project directory and everything it needs.

        ``name`` is what the project is called. It is written to ``project.json`` only where a
        person gave one. A project with none shows its folder's name.

        ``chosen`` names the settings a person chose at creation (``init``'s own options), as a
        mapping shaped like the settings file. Each is written whatever its value, so a later
        engine's changed default never reaches it. Everything else equal to a default is left out
        of the file.

        The method refuses to overwrite an existing project, and never writes into a person's own
        file or folder that happens to carry a name a project writes (a data folder called
        ``runs``, a ``settings.yaml`` of theirs). A folder becomes a project's only where none of
        its names is taken, or taken by an empty folder. A project may be made anywhere, inside a
        root it reads included. The census leaves it out.
        """
        path = path.expanduser().resolve()
        # Said before anything is written: a name too long leaves no half-made project.
        called = project_name(name) or None
        if _is_a_project(path):
            message = f"a project already exists at {path}"
            raise ProjectExistsError(message)
        taken = _names_taken(path)
        if taken:
            named = "a name" if len(taken) == 1 else "names"
            message = (
                f"{path} already holds {', '.join(taken)}, {named} a project writes; a project "
                f"there would write into {'it' if len(taken) == 1 else 'them'}. Choose a new or "
                f"empty folder for the project."
            )
            raise ProjectExistsError(message)

        resolved_roots = [root.expanduser().resolve() for root in roots]

        path.mkdir(parents=True, exist_ok=True)
        # Every folder a project writes is made now. A folder made later, where first written,
        # could meet a person's folder of its name made in the meantime, and the engine would
        # write among their files.
        for directory in sorted(PROJECT_FOLDERS):
            (path / directory).mkdir(exist_ok=True)
        # Checked once its folders are made and before project.json is written, so a project
        # refused here is never left behind half made.
        check_layout(path)

        # Each root's case label is its own, established read-only from its names. Using the
        # project directory's label for every root would decide entry identity wrongly: a
        # case-sensitive share indexed from an insensitive project would merge two files.
        declarations = tuple(
            RootDeclaration(
                root_id=root_id(root.as_uri()),
                root_uri=root.as_uri(),
                mount_path=str(root),
                label=label,
                case_sensitivity=case,
                case_established_by=how,
            )
            for root, label in zip(
                resolved_roots,
                [*labels, *([None] * len(resolved_roots))],
                strict=False,
            )
            for case, how in (establish_case_sensitivity(root),)
        )
        # A listing is a root too: ``listing:<archive>:<identifier>``. Its listing is cached in
        # the project and read by the census through the replay seam.
        declarations += tuple(_listing_declaration(path, uri) for uri in listings)
        identity = ProjectIdentity(
            created_by=__version__,
            created_at=created_at,
            roots=declarations,
            name=called,
        )
        resolved_settings = settings or Settings()
        _write_identity(path, identity)
        _write_settings(path, resolved_settings, kept=chosen)
        return Project(path=path, identity=identity, settings=resolved_settings)

    @staticmethod
    def open(path: Path) -> Project:
        """Open an existing project, refusing one written by a newer schema, and one whose own
        folders are links (``storage.layout.check_layout``): every command opens the project, so
        none deletes or replaces anything through one."""
        path = path.expanduser().resolve()
        identity_file = path / PROJECT_FILE
        if not identity_file.is_file():
            message = f"{path} is not an Ingence project; no {PROJECT_FILE} found"
            raise NotAProjectError(message)
        identity = _read_identity(identity_file.read_text(encoding="utf-8"), path)
        check_layout(path)

        # A project whose settings file was deleted still opens, on defaults.
        # Losing tunables is an inconvenience. Refusing to open a project
        # because of it would be a much larger one.
        loaded = read_settings_file(path / SETTINGS_FILE)
        # The Dashboard's own file is laid over the hand-edited one. A value committed on the
        # Parameters page wins, and the Dashboard never writes ``settings.yaml``.
        dashboard = read_settings_file(path / DASHBOARD_SETTINGS_FILE)
        if dashboard:
            from ingence.workspace.settings_overlay import merged

            loaded = merged(loaded, dashboard)
        settings = Settings.model_validate(loaded)
        return Project(path=path, identity=identity, settings=settings)

    @property
    def name(self) -> str:
        """What the project is called: the name in ``project.json``, else its folder's."""
        return self.identity.name or self.path.name

    def rename(self, name: str) -> Project:
        """Give the project a new name, written to ``project.json``, and return it reopened.

        Refused where the name is empty or too long. Nothing else in the project changes.
        """
        said = project_name(name)
        if not said:
            message = "Give the project a name."
            raise ValueError(message)
        # The file now holds a field that schema 1.23 added, so it says it is a 1.23 file.
        renamed = self.identity.model_copy(update={"name": said, "schema_version": SCHEMA_VERSION})
        _write_identity(self.path, renamed)
        return Project(path=self.path, identity=renamed, settings=self.settings)

    # -- roots ------------------------------------------------------------

    def root(self, root_identifier: str) -> RootDeclaration:
        """Return one declared root by its logical identifier."""
        for declaration in self.identity.roots:
            if declaration.root_id == root_identifier:
                return declaration
        message = f"no root {root_identifier!r} is declared in this project"
        raise KeyError(message)

    def save_settings(self, settings: Settings) -> Project:
        """Persist replacement settings and return the reopened project.

        Where the Dashboard has a settings file, what the method writes to ``settings.yaml`` is the
        hand file's values with only what this call *changed* laid on. A value the Dashboard
        committed is never copied into the file a person edits.
        """
        written = settings
        hand_before = read_settings_file(self.path / SETTINGS_FILE)
        if (self.path / DASHBOARD_SETTINGS_FILE).is_file():
            hand = read_settings_file(self.path / SETTINGS_FILE)
            written = Settings.model_validate(
                _with_changes(
                    Settings.model_validate(hand).model_dump(mode="json"),
                    self.settings.model_dump(mode="json"),
                    settings.model_dump(mode="json"),
                )
            )
        _write_settings(self.path, written, kept=hand_before)
        return Project(path=self.path, identity=self.identity, settings=settings)

    # -- description ------------------------------------------------------

    def platform_record(self) -> dict[str, str]:
        """Return the platform facts stamped into every run record."""
        return {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "case_sensitivity": (
                self.identity.roots[0].case_sensitivity.value
                if self.identity.roots
                else CaseSensitivity.UNKNOWN.value
            ),
        }


def unreachable_roots(
    roots: Sequence[RootDeclaration],
) -> tuple[tuple[RootDeclaration, str], ...]:
    """Return each root that cannot be reached now, with why, reading the root's own entry only.

    Two verbs need a root: a walk and a dive read the data pool. ``index --rebind``, ``infer``,
    ``record``, ``rule``, ``teach`` and the Dashboard read the project alone and never reach for
    one. The two verbs that read the pool make this check before they stage anything. The check
    calls ``stat`` on each root's own path and on nothing beneath it.

    Without the check, an absent root gives a misleading run. A walk would state every file of the
    root deleted (its one scan error, then a tombstone for each entry the last census held). A
    dive would record each file it tried as a failed reading of content that was simply out of
    reach. Unmounted is not deleted, and unreachable is not unreadable. "Unexamined" is a state
    of its own, apart from "deleted" and "failed".

    The check has one limit. A mountpoint left behind as an empty folder is a folder, and a walk
    of it would state it empty. A share is re-read offline with ``index --rebind`` instead.
    """
    found: list[tuple[RootDeclaration, str]] = []
    for declaration in roots:
        if is_listing_root(declaration):
            # A listing root is reached through its cache, which the project holds: it is
            # there once fetched, and nothing on the archive's server is asked.
            if not (Path(declaration.mount_path) / HEADER_FILE).is_file():
                found.append(
                    (declaration, "its listing has not been fetched: run 'ingence index --fetch'")
                )
            continue
        try:
            result = os.stat(declaration.mount_path)
        except FileNotFoundError:
            found.append((declaration, "it does not exist: not mounted, moved or removed"))
            continue
        except OSError as error:
            found.append((declaration, f"it cannot be read: {error.strerror or error}"))
            continue
        if not stat_module.S_ISDIR(result.st_mode):
            found.append((declaration, NOT_A_FOLDER))
    return tuple(found)


#: Why :func:`unreachable_roots` names a root that is there and is not a folder. The root is
#: reached and wrong, where every other reason it gives is a root out of reach now. ``ingence fix``
#: reports the first as a problem and the rest as unchecked.
NOT_A_FOLDER = "it is not a folder"


def refuse_unreachable_roots(roots: Sequence[RootDeclaration], *, verb: str) -> None:
    """Raise :class:`RootUnreachableError`, naming each root and why, where any cannot be reached.

    ``verb`` says what needed the root, in the words the message opens with ("A walk reads every
    declared root"), so the refusal says both what could not run and what still can.
    """
    missing = unreachable_roots(roots)
    if not missing:
        return
    named = "; ".join(
        f"{declaration.label or Path(declaration.mount_path).name} at "
        f"{declaration.mount_path}: {why}"
        for declaration, why in missing
    )
    message = (
        f"{verb}, and {len(missing)} of {len(roots)} cannot be reached ({named}). Nothing was "
        "written, and the current run is still current. Mount the root and run again; "
        "'ingence index --rebind', 'infer', 'record', 'see' and 'rule' work from the project "
        "alone and need no root."
    )
    raise RootUnreachableError(message)


def _listing_declaration(
    project_path: Path, uri: str, *, label: str | None = None
) -> RootDeclaration:
    """A listing root's declaration: its cache inside the project, its paths compared exactly."""
    named = parse_listing_uri(uri)
    if named is None:
        message = f"{uri!r} names no listing: write listing:<archive>:<identifier>"
        raise ListingError(message)
    archive, identifier = named
    return RootDeclaration(
        root_id=root_id(uri),
        root_uri=uri,
        mount_path=str(project_path.expanduser().resolve() / LISTINGS_DIR / archive / identifier),
        label=label,
        case_sensitivity=CaseSensitivity.SENSITIVE,
        case_established_by=CASE_OF_A_LISTING,
    )


def discover(start: Path) -> Project:
    """Find the project containing a path, searching upward from it.

    The search walks up and does not require an exact directory, so a command run three levels
    inside a project finds it. Version control systems have taught people to expect this.
    """
    current = start.expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / PROJECT_FILE).is_file():
            return Project.open(candidate)
    message = f"no Ingence project found at {start} or in any parent directory"
    raise NotAProjectError(message)


# ---------------------------------------------------------------------------
# Writing. Both files are replaced atomically, for the same reason the run
# pointer is: a reader must see the whole previous file or the whole new one.
# ---------------------------------------------------------------------------


#: What to do about each settings file that cannot be read, said beside its name.
_WHEN_UNREADABLE = {
    SETTINGS_FILE: "Correct it by hand, or delete it and the engine's defaults apply.",
    DASHBOARD_SETTINGS_FILE: (
        "It holds the settings committed on the Dashboard's Engine page, in Settings; delete it "
        "and the values in settings.yaml apply again."
    ),
}


def read_settings_file(path: Path) -> dict[str, Any]:
    """A settings file as a mapping. A missing or empty one is an empty mapping.

    A file that cannot be read is refused by name, with what to do about it
    (:class:`SettingsFileError`). Every command opens the project, so a file the reader refuses
    stops them all, and the message is the one place a person learns which file it is.
    """
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        why = str(error).splitlines()[0] if str(error) else type(error).__name__
        advice = _WHEN_UNREADABLE.get(path.name, "Correct it, or delete it.")
        message = f"The settings file {path} cannot be read ({why}). {advice}"
        raise SettingsFileError(message) from error
    if not isinstance(loaded, dict):
        return {}
    if path.name == SETTINGS_FILE:
        # A hand file an older engine wrote is read as this engine's. Its ``hash.mode: none`` was
        # the default that ``init`` wrote, never a person's choice.
        from ingence.core.settings import upgraded

        return upgraded(loaded)
    return loaded


def _is_a_project(path: Path) -> bool:
    """Whether a folder holds an Ingence project, by the layout a census recognises one by.

    ``project.json`` and ``runs``, so spelled, beside either ``settings.yaml`` and ``CURRENT`` or
    the ``rulebook``, ``dictionary`` and ``record`` folders a project is made with. Anything less
    is a person's (a web project's ``project.json``, or a lookalike with ``project.json``,
    ``settings.yaml`` and ``runs``). Such a folder is refused as names taken and not called a
    project.
    """
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    folders = {name for name in names if (path / name).is_dir()}
    return (
        PROJECT_FILE in names
        and RUNS_DIR in folders
        and (
            {SETTINGS_FILE, CURRENT_POINTER} <= names
            or {RULEBOOK_DIR, DICTIONARY_DIR, RECORD_DIR} <= folders
        )
    )


def _names_taken(path: Path) -> list[str]:
    """The names a project writes that a folder already holds as something of its own, as spelled.

    An empty folder is nobody's work (an interrupted create, or one made ready for the project),
    so it is not taken. A name that differs from a project's only by case is taken even when empty
    where the disk ignores case (macOS's and Windows' by default). The project would write into it
    under the person's spelling, which the census compares exactly and would read as data. The
    function asks the disk by reading, never by writing: whether the project's spelling reaches
    the person's entry.
    """
    try:
        listed = sorted(os.listdir(path))
    except OSError:
        return []
    taken: list[str] = []
    for name in listed:
        where = path / name
        # A temporary name of a project's own file counts (``.project.json.incoming``, and the
        # random ones): the project would be made over whatever a person keeps under it.
        if is_project_name(name):
            if where.is_dir() and not where.is_symlink() and not any(where.iterdir()):
                continue
            taken.append(name)
        elif spelled_otherwise(path, name) is not None:
            taken.append(name)
    return taken


def _write_identity(path: Path, identity: ProjectIdentity) -> None:
    held = identity.model_dump(mode="json")
    if held.get("name") is None:
        # Nobody named it: no field, so a build that does not know the field still reads it.
        held.pop("name", None)
    write_atomically(path / PROJECT_FILE, canonical_json(held))


def _with_changes(
    hand: dict[str, Any], before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """The hand file's mapping with each leaf that differs between ``before`` and ``after`` set."""
    merged = dict(hand)
    for key, value in after.items():
        if isinstance(value, dict) and isinstance(before.get(key), dict):
            merged[key] = _with_changes(
                merged[key] if isinstance(merged.get(key), dict) else {}, before[key], value
            )
        elif before.get(key) != value:
            merged[key] = value
    return merged


#: A key a mapping does not hold, told apart from one holding ``None``.
_ABSENT = object()


def _written_values(
    values: dict[str, Any], defaults: dict[str, Any], kept: dict[str, Any] | None
) -> dict[str, Any]:
    """The values a settings file holds: those differing from the defaults, and every key kept.

    A default is never written as if somebody chose it. If ``init`` wrote every default into the
    file, a later engine would read a value nobody chose as a person's, and a default that engine
    changed would never reach the project. A key the file already held (``kept``, the hand file as
    it was) stays written, whatever its value, because a person may have written the default on
    purpose.
    """
    written: dict[str, Any] = {}
    for key, value in values.items():
        default = defaults.get(key, _ABSENT)
        held = kept.get(key, _ABSENT) if kept is not None else _ABSENT
        if isinstance(value, dict) and isinstance(default, dict):
            inner = _written_values(value, default, held if isinstance(held, dict) else None)
            if inner or held is not _ABSENT:
                written[key] = inner if isinstance(held, dict) or held is _ABSENT else value
        elif value != default or held is not _ABSENT:
            written[key] = value
    return written


def _write_settings(path: Path, settings: Settings, *, kept: dict[str, Any] | None = None) -> None:
    """Write ``settings.yaml``: what differs from this engine's defaults, and what a person wrote.

    ``kept`` is the hand file as it was before this write (``None`` for a new project): each of its
    keys stays written. The schema version is always written, since an older file is read by it.
    """
    header = (
        "# Ingence engine settings.\n"
        "#\n"
        "# These govern how the engine runs, never what it concludes. The test is:\n"
        "# does changing this alter what the engine believes, or only how fast it\n"
        "# gets there? Interpretive commitments live in the Rulebook instead.\n"
        "#\n"
        "# Only what differs from the engine's defaults is written here: every setting\n"
        "# not written takes the default of the engine that reads it. Write a setting\n"
        "# here to hold its value. Delete this file and the engine uses its defaults.\n\n"
    )
    values = settings.model_dump(mode="json")
    written = _written_values(values, Settings().model_dump(mode="json"), kept)
    written["schema_version"] = values.get("schema_version", SCHEMA_VERSION)
    body = yaml.safe_dump(written, sort_keys=True, default_flow_style=False)
    write_atomically(path / SETTINGS_FILE, header + body)


def _refuse_future_schema(found: str, path: Path, made_by: str | None = None) -> None:
    """Refuse a project written by a newer schema than this engine understands.

    Reading forward is guesswork. An older engine cannot know what a field it has never heard of
    means, and quietly ignoring it would produce a subtly wrong answer instead of an error.
    Refusing is the honest failure.
    """
    try:
        found_major = int(found.split(".")[0])
    except ValueError:
        return  # not a version at all: the file's own fields are checked next
    if found_major > int(SCHEMA_VERSION.split(".")[0]):
        message = (
            f"the project at {path} was made{_by(made_by)} at schema version {found}, and "
            f"this engine understands {SCHEMA_VERSION}. Upgrade Ingence to open it."
        )
        raise NotAProjectError(message)


def _by(made_by: object) -> str:
    """`` by Ingence 0.4.0`` where the file says which build made the project (``created_by``:
    the build that first wrote it, which is not always the build that wrote it last), else
    nothing."""
    return f" by Ingence {made_by}" if isinstance(made_by, str) and made_by else ""


def _read_identity(text: str, path: Path) -> ProjectIdentity:
    """Read ``project.json``, refusing in words what this engine cannot read.

    The file's own version is read before its fields are checked. A project a newer build wrote
    may hold a field this engine has never heard of, and the identity model forbids those. If the
    fields were checked first, the person would get a traceback, and the version that would
    explain it would never be reached. So ``schema_version`` and ``created_by`` come out of the
    raw file first. A newer major schema is refused. Any other failure to read the fields (an
    unknown field a newer minor version added, a wrong type, a file that is not JSON) is a refusal
    that names the build that wrote the project and says to upgrade. The person never has to read
    an exception.
    """
    try:
        raw = json.loads(text)
    except ValueError as error:
        message = (
            f"the {PROJECT_FILE} of the project at {path} cannot be read as JSON ({error}). "
            "If it was edited by hand, restore it; otherwise upgrade Ingence and try again."
        )
        raise NotAProjectError(message) from error
    if not isinstance(raw, dict):
        message = (
            f"the {PROJECT_FILE} of the project at {path} is not a record this engine knows. "
            "Upgrade Ingence to open it, or restore the file if it was edited by hand."
        )
        raise NotAProjectError(message)
    found = raw.get("schema_version")
    made_by = raw.get("created_by")
    if isinstance(found, str):
        _refuse_future_schema(found, path, made_by if isinstance(made_by, str) else None)
    try:
        return ProjectIdentity.model_validate(raw)
    except ValidationError as error:
        unknown: list[str] = []
        unreadable: list[str] = []
        missing: list[str] = []
        for item in error.errors():
            where = ".".join(str(part) for part in item["loc"]) or "the file"
            kinds = {"extra_forbidden": unknown, "missing": missing}
            kinds.get(item["type"], unreadable).append(where)
        parts = []
        if unknown:
            many = len(set(unknown)) > 1
            parts.append(
                f"the {'fields' if many else 'field'} {', '.join(sorted(set(unknown)))} "
                f"{'are not ones' if many else 'is not one'} it knows"
            )
        if missing:
            parts.append(f"{', '.join(sorted(set(missing)))} is missing")
        if unreadable:
            many = len(set(unreadable)) > 1
            parts.append(
                f"{', '.join(sorted(set(unreadable)))} "
                f"{'are not values' if many else 'is not a value'} it can read"
            )
        wrote = _by(made_by) or " by another build of Ingence"
        said = f" at schema version {found}" if isinstance(found, str) else ""
        message = (
            f"the project at {path} was made{wrote}{said}, and this engine (Ingence "
            f"{__version__}, schema version {SCHEMA_VERSION}) cannot read it: {'; '.join(parts)}. "
            "Upgrade Ingence to open it, or restore the file if it was edited by hand."
        )
        raise NotAProjectError(message) from error
