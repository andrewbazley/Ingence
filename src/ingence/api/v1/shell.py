"""``/api/v1/shell``, ``/reachability`` and ``/reporting``: what the Dashboard's frame says
about the project.

Three small routes, each read by the page's header, footer or states, and each shaped field by
field:

- ``shell``: the project's name (and whether a person gave it), where it is written (for Rename),
  when its current run was published, whether any rule has run in that run, and what the census
  counted. "Read, not yet inferred" is the engine's own test: a run whose ``rules`` table is
  empty has had no inference over it (``textword/questions.py`` asks the same).
- ``reachability``: whether each root the project reads can be reached now.
  The check is a ``stat`` of the root's own path and nothing beneath it (``unreachable_roots``,
  the check ``index`` and ``dive`` make before they stage anything), and it never waits long: a
  share that has gone away can hold a ``stat`` for minutes, so each root is asked in a thread of
  its own and a root that has not answered in :data:`ANSWER_SECONDS` is said not to have.
- ``reporting``: what "Report a problem" cannot learn in the browser: the computer and the
  Python the engine runs on, and the repository's new-issue page where the package's own
  metadata names an ``Issues`` address. Where it names none, a report goes by email.

Nothing here reads inside a root, and nothing is a name from the data: the route that names a
root's path serves it to the page of the person who owns the machine, and the report text the
page builds never takes it.
"""

from __future__ import annotations

import platform
import threading
from http import HTTPStatus
from importlib import metadata
from typing import TYPE_CHECKING, Literal

from ingence.api.v1 import ApiError, V1Context, now, route
from ingence.api.v1.models import ApiModel, Count, ZonedTime, register
from ingence.api.v1.shaping import pinned_catalog, zoned_or_none
from ingence.core.project import NotAProjectError, Project, RootDeclaration, unreachable_roots
from ingence.storage.catalog import open_catalog
from ingence.storage.own_files import filesystem_entry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "ANSWER_SECONDS",
    "ReachabilityResponse",
    "ReportingResponse",
    "ShellResponse",
    "project_identifier",
]

#: How long a root may take to answer a ``stat`` before it is said not to have. A mounted share
#: answers in milliseconds; one that has gone away can hold the call for minutes.
ANSWER_SECONDS = 4.0

#: The package's own project URL label for the repository's issue tracker. It is present in the
#: metadata only once the repository is public.
ISSUES_LABEL = "issues"


class ShellCensus(ApiModel):
    """What the census of the current run counted: its own files and its own folders."""

    files: Count
    folders: Count


@register("shell")
class ShellResponse(ApiModel):
    """``GET /api/v1/shell``: the project's name, its current run's standing and its census."""

    api_version: Literal["1.0"] = "1.0"
    #: A stable identifier of the project, never its path. The project's own ``project_id`` from
    #: ``project.json`` where it has one, else a digest of its creation record (who made it, when,
    #: and its roots as declared).
    project_id: str
    #: What the project is called: the name in its ``project.json``, else its folder's.
    project_name: str
    #: True where a person gave the name; False where it is the folder's own.
    name_given: bool
    #: Where the project is written on this machine (the one place Rename needs it).
    project_path: str
    #: When the current run finished (its own record's time, not the pointer's, which a going back
    #: to an earlier run moves), where there is one.
    run_finished_at: ZonedTime | None
    #: Whether any rule has been evaluated in the run the page reads. False with a census is
    #: "read, not yet inferred".
    inferred: bool
    #: What the run's census counted; None where the project has no run yet.
    census: ShellCensus | None


class RootReach(ApiModel):
    """One root, and whether it could be reached when it was last asked."""

    #: The root's label, where it has one.
    label: str | None
    #: The folder's path; for an archive's listing, the listing's name.
    path: str
    #: ``folder`` (read where it sits) or ``listing`` (an archive's listing, read from its cache).
    kind: Literal["folder", "listing"]
    reachable: bool
    #: Why not, in words, where it cannot be reached.
    why: str | None


@register("reachability")
class ReachabilityResponse(ApiModel):
    """``GET /api/v1/reachability``: whether the roots the project reads can be reached now."""

    api_version: Literal["1.0"] = "1.0"
    #: True when every root can be reached. A project with no root at all is reachable.
    root_reachable: bool
    #: When the roots were asked.
    last_checked: ZonedTime
    roots: list[RootReach]
    #: When the current run finished: what the page says is still readable.
    run_finished_at: ZonedTime | None


@register("reporting")
class ReportingResponse(ApiModel):
    """``GET /api/v1/reporting``: what a problem report states that only the engine knows."""

    api_version: Literal["1.0"] = "1.0"
    #: The computer the engine runs on, as a person says it: ``macOS 14.6 (arm64)``.
    computer: str
    #: The Python the engine runs on: ``3.12.6``.
    python: str
    #: The repository's new-issue page, where the package's metadata names one; None otherwise.
    new_issue: str | None


# --------------------------------------------------------------------------------------- shell


def _run_finished(context: V1Context) -> str | None:
    """When the run the pointer names finished, from that run's own record; None where it states
    no zoned time. The pointer's ``updated_at`` is when the pointer moved, which a going back to an
    earlier run changes: it is not when the run it names was made."""
    if context.run_current is None:
        return None
    with open_catalog(context.project, run_id=context.run_current) as catalog:
        row = catalog.query("SELECT ended_at, started_at FROM run").fetchone()
    if not row:
        return None
    return zoned_or_none(row[0]) or zoned_or_none(row[1])


def project_identifier(opened: Project) -> str:
    """The project's stable identifier: its own, where ``project.json`` carries one, else a digest
    of its creation record. Never its path."""
    from ingence.core.ids import canonical_json, digest_text

    identity = opened.identity
    own = getattr(identity, "project_id", None)
    if isinstance(own, str) and own.strip():
        return own.strip()
    record = identity.model_dump(mode="json")
    record.pop("name", None)  # a rename changes nothing of what the project is
    return "project_" + digest_text(canonical_json(record))[:16]


@route("shell", model=ShellResponse)
def shell(context: V1Context, query: Mapping[str, Sequence[str]]) -> ShellResponse:
    """The project's name and the standing of its current run."""
    del query
    try:
        opened = Project.open(context.project)
    except NotAProjectError as error:
        raise ApiError(str(error), code="not_found", status=HTTPStatus.NOT_FOUND) from error
    given = opened.identity.name
    finished = _run_finished(context)
    inferred = False
    census: ShellCensus | None = None
    if context.run_pinned is not None:
        with pinned_catalog(context) as (catalog, _run):
            # The one interpolated value is a constant condition (``own_files.filesystem_entry``).
            counted = (
                "SELECT entry_type, count(*) FROM entries "  # noqa: S608
                f"WHERE {filesystem_entry()} GROUP BY entry_type"
            )
            kinds = dict(catalog.query(counted).fetchall())
            ruled = catalog.query("SELECT count(*) FROM rules").fetchone()
        inferred = bool(ruled and ruled[0])
        census = ShellCensus(
            files=int(kinds.get("file", 0)), folders=int(kinds.get("directory", 0))
        )
    return ShellResponse(
        project_id=project_identifier(opened),
        project_name=given or context.project.name,
        name_given=bool(given),
        project_path=str(context.project),
        run_finished_at=finished,
        inferred=inferred,
        census=census,
    )


# ---------------------------------------------------------------------------------- reachability

#: A root whose ``stat`` has not come back, by its path: asked again, it is not asked a second
#: time while the first is still waiting, so a share that has gone away holds one thread, not one
#: for every look at the page.
_WAITING: dict[str, threading.Thread] = {}
_WAITING_GUARD = threading.Lock()


def _why_not(declaration: RootDeclaration, wait: float) -> str | None:
    """Why one root cannot be reached, in words, or None where it can. Never raises."""
    key = declaration.mount_path
    answered: list[str | None] = []

    def ask() -> None:
        found = unreachable_roots([declaration])
        answered.append(found[0][1] if found else None)

    with _WAITING_GUARD:
        before = _WAITING.get(key)
        if before is not None and before.is_alive():
            return f"it has not answered for some time (asked again after {wait:g} seconds)"
        thread = threading.Thread(target=ask, name="ingence-root-stat", daemon=True)
        _WAITING[key] = thread
        thread.start()
    thread.join(wait)
    if thread.is_alive():
        return f"it did not answer within {wait:g} seconds"
    with _WAITING_GUARD:
        if _WAITING.get(key) is thread:
            del _WAITING[key]
    return answered[0] if answered else None


def reach_of(roots: Sequence[RootDeclaration], *, wait: float = ANSWER_SECONDS) -> list[RootReach]:
    """Each root, reached or not. Folders are asked together, so a slow one costs ``wait`` once."""
    results: dict[int, str | None] = {}

    def one(at: int, declaration: RootDeclaration) -> None:
        results[at] = _why_not(declaration, wait)

    threads = [
        threading.Thread(target=one, args=(at, declaration), daemon=True)
        for at, declaration in enumerate(roots)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(wait + 1.0)
    reached: list[RootReach] = []
    for at, declaration in enumerate(roots):
        listing = declaration.listing is not None
        why = results.get(at, "it did not answer in time")
        reached.append(
            RootReach(
                label=declaration.label,
                path=declaration.root_uri if listing else declaration.mount_path,
                kind="listing" if listing else "folder",
                reachable=why is None,
                why=why,
            )
        )
    return reached


@route("reachability", model=ReachabilityResponse)
def reachability(context: V1Context, query: Mapping[str, Sequence[str]]) -> ReachabilityResponse:
    """Whether the roots the project reads can be reached now."""
    del query
    try:
        opened = Project.open(context.project)
    except NotAProjectError as error:
        raise ApiError(str(error), code="not_found", status=HTTPStatus.NOT_FOUND) from error
    reached = reach_of(opened.identity.roots)
    return ReachabilityResponse(
        root_reachable=all(each.reachable for each in reached),
        last_checked=now(),
        roots=reached,
        run_finished_at=_run_finished(context),
    )


# ------------------------------------------------------------------------------------ reporting


#: The macOS release each Darwin kernel's major version belongs to: 20 to 24 are macOS 11 to 15 (the
#: major version is the kernel's less nine), and 25 is macOS 26.
_DARWIN_FIRST_MAC, _DARWIN_LAST_MAC = 20, 24
_DARWIN_OF_MACOS_26 = 25


def macos_from_darwin(release: str) -> str | None:
    """The macOS major version a Darwin kernel release belongs to (``23.6.0`` is ``14``), or None
    for a kernel this table does not know. For a Python built against an old SDK, whose
    ``platform.mac_ver()`` says ``10.16`` for every release from macOS 11 on."""
    head = release.split(".", 1)[0]
    if not head.isdigit():
        return None
    major = int(head)
    if _DARWIN_FIRST_MAC <= major <= _DARWIN_LAST_MAC:
        return str(major - 9)
    if major == _DARWIN_OF_MACOS_26:
        return "26"
    return None


def computer_said() -> str:
    """The computer as a person says it, from the standard library's own facts and nothing of
    its name: ``macOS 14.6 (arm64)``, ``Windows 11 (AMD64)``, ``Linux 6.8.0 (x86_64)``.

    On macOS, ``platform.mac_ver()`` says ``10.16`` for a Python built against an old SDK, whatever
    the release; then the version comes from the Darwin kernel's (``macos_from_darwin``), as its
    major number only, because the kernel does not state the minor one.
    """
    system = platform.system()
    if system == "Darwin":
        version = platform.mac_ver()[0]
        if not version or version.startswith("10.16"):
            version = macos_from_darwin(platform.release()) or ""
        said = f"macOS {version}".strip()
    elif system == "Windows":
        said = f"Windows {platform.release()}"
    else:
        said = f"{system or 'Unknown system'} {platform.release()}".strip()
    machine = platform.machine()
    return f"{said} ({machine})" if machine else said


def issues_address() -> str | None:
    """The repository's issue tracker, from the package's own metadata: set only once the
    repository is public, so None until launch."""
    try:
        lines = metadata.metadata("ingence").get_all("Project-URL") or []
    except metadata.PackageNotFoundError:
        return None
    for line in lines:
        label, _, address = str(line).partition(",")
        if label.strip().lower() == ISSUES_LABEL and address.strip().startswith("https://"):
            return address.strip()
    return None


def new_issue_address() -> str | None:
    """The new-issue page of the public repository, or None while it is not public."""
    issues = issues_address()
    return f"{issues.rstrip('/')}/new" if issues else None


@route("reporting", model=ReportingResponse)
def reporting(context: V1Context, query: Mapping[str, Sequence[str]]) -> ReportingResponse:
    """The computer, the Python and, where the metadata names one, the new-issue page."""
    del context, query
    return ReportingResponse(
        computer=computer_said(), python=platform.python_version(), new_issue=new_issue_address()
    )
