"""The ``ingence`` command line entry point.

The verb set is fixed. A verb that is not implemented yet says so and exits non-zero. It does not
pretend to succeed.

Verbs:

    init    create a project in a directory
    index   build the Index over a data pool
    dive    deepen extraction within a granted budget
    infer   evaluate the Rulebook and publish its conclusions
    map     build projections and views (not implemented yet)
    see     open the Dashboard
    demo    make a sample laboratory, read it and open it
    rule    read the Rulebook
    teach   read and revise the Dictionary
    record  write decisions to the Record from a review sheet
    log     show the Change Log (not implemented yet)
    export  write exportable artifacts
    mcp     serve the project over MCP
    fix     diagnose a project and repair what is repairable
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from typer.core import TyperGroup

from ingence import SCHEMA_VERSION, __version__
from ingence.api.server import WEB_ROOT, serve
from ingence.cli.shareable import AsWritten, Placeholders, backstop_notice, plain_console
from ingence.core.destinations import (
    DestinationRefusedError,
    OutputExistsError,
    ReadOnlyFileError,
    check_output_file,
    check_output_folder,
    declared_roots,
    refuse_existing,
    replacing,
    typed_path,
    write_replacing,
)
from ingence.core.ids import run_id as make_run_id
from ingence.core.project import (
    NotAProjectError,
    Project,
    ProjectExistsError,
    RootUnreachableError,
    SettingsFileError,
    discover,
    refuse_unreachable_roots,
)
from ingence.core.schema import ExcludedNames, RecordEventKind
from ingence.core.settings import DictionarySettings, Settings
from ingence.core.versions import NewerSchemaError
from ingence.extractors import load_builtins as load_extractors
from ingence.extractors.base import EXTRACTORS
from ingence.infer.run import InferenceReport, run_inference
from ingence.jobs.locks import ProjectBusyError, publishing
from ingence.listing.format import parse_listing_uri
from ingence.rulebook.load import RuleDeclarationError
from ingence.scan.cancel import CancellationToken, cancel_on_signal
from ingence.scan.census import excluded_names, run_census
from ingence.scan.deepen import run_deepen
from ingence.scan.dry_run import dry_run
from ingence.scan.profiles import PROFILES, depth_named, depth_word
from ingence.scan.progress import ProgressReporter, ProgressSnapshot
from ingence.storage.catalog import compare_runs, describe_differences, open_catalog
from ingence.storage.commit import CommitError, read_pointer, staging_path
from ingence.storage.layout import RULEBOOK_DIR, ProjectLayoutError
from ingence.storage.own_files import filesystem_entry

if TYPE_CHECKING:
    from ingence.record.imported import ImportStatement

#: The verbs that are not available in this version: name to (one-line help, a number that nothing
#: reads). Adding a verb here adds it to the command line. The verb set itself is settled, so a new
#: verb is a decision and not an addition.
DEFERRED_VERBS: dict[str, tuple[str, int]] = {
    "map": ("build views", 7),
    "log": ("show the history of changes", 8),
}


class _RefusingGroup(TyperGroup):
    """The command line's own group: a Record or a run a newer build wrote is refused in words.

    No verb needs to catch it. A run or a decision stamped at a newer schema than this build
    reads is met deep inside whichever verb reads it (the current run's pointer, the Record's
    fragments), and :class:`NewerSchemaError` already says which and what to do; left to
    propagate it is a traceback with the words on its last line. Here it is said and the command
    ends with a status of 1, whatever verb met it and wherever it was met.
    """

    def invoke(self, ctx: Any) -> Any:  # the Context of the click typer runs on, whichever it is
        try:
            return super().invoke(ctx)
        except NewerSchemaError as refused:
            console.print(f"[red]{escape(str(refused))}[/red]")
            raise typer.Exit(code=1) from refused


app = typer.Typer(
    cls=_RefusingGroup,
    name="ingence",
    # The one-line description of the package, the same line the package metadata carries.
    help=(
        "Ingence is a read-only indexing and inference engine and local Dashboard for exploring "
        "scientific filesystems: it surveys what is there, and proposes how files relate using a "
        "Dictionary and a Rulebook you can read and edit."
    ),
    # ``ingence`` alone opens the Dashboard, the same as ``ingence see``. The help stays on
    # ``ingence --help`` (the root callback below).
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode="markdown",
)
console = Console()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_run_id() -> str:
    """Mint a run identifier.

    Unique per run rather than derived from the data, because two runs over
    unchanged input are two runs and collapsing them would destroy the time
    series the Change Log is built from.
    """
    return make_run_id(started_at=_now(), nonce=os.urandom(8).hex())


#: ``--project`` on every command that finds its project from the folder it runs in.
_PROJECT_HELP = (
    "The project folder. Without it, Ingence looks in this folder and the folders above it."
)

#: ``--wait`` on the three commands that publish runs.
_WAIT_HELP = (
    "If the Dashboard or another command is saving a scan in this project, wait for it to "
    "finish. Without it, this command stops."
)


#: ``--verbose`` on the commands that publish runs: the developer's detail.
_VERBOSE_HELP = "Also print the detail: each rule's counts, reuse reasons, timings."


def _plural(number: int, noun: str) -> str:
    """A count and its noun, the noun plural unless the count is one."""
    if number == 1:
        return f"1 {noun}"
    return f"{number:,} {noun[:-1] + 'ies' if noun.endswith('y') else noun + 's'}"


def _command(verb: str, project: Path | str) -> str:
    """A command a person copies: its path quoted for a shell and printed as written."""
    return escape(f"ingence {verb} -p {shlex.quote(str(project))}")


def _entries_said(total: int, parts: int) -> str:
    """A count of entries with the parts inside documents said apart.

    The count reads ``12 entries: 3 files and folders, 9 parts inside documents``, or ``3 entries``
    where there are none.
    """
    if not parts:
        return f"{total:,} items"
    return (
        f"{total:,} items: {total - parts:,} files and folders, "
        f"{_plural(parts, 'part')} inside documents"
    )


def _counted(pairs: Sequence[tuple[int, str]]) -> str:
    """Each non-zero count with its noun, joined: ``910 files, 60 folders, 1 link``."""
    return ", ".join(_plural(number, noun) for number, noun in pairs if number) or "nothing"


@contextlib.contextmanager
def _one_publisher(project: Path, command: str, *, wait: bool) -> Iterator[None]:
    """Hold the project's publish lock around a command's run, or stop saying who holds it."""
    held = contextlib.ExitStack()
    try:
        held.enter_context(
            publishing(
                project,
                command=command,
                wait=wait,
                on_wait=lambda said: console.print(f"[yellow]{escape(said)}[/yellow]"),
            )
        )
    except ProjectBusyError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    with held:
        yield


def _dry_run(
    project: Project,
    *,
    limit: int,
    seconds: float,
    report_path: Path | None,
    verbose: bool = False,
    overwrite: bool = False,
) -> None:
    """``index --dry-run``: each root's metadata, reported and written nowhere.

    The report is in words. It gives counts with their nouns, sizes in bytes that a person reads,
    and links by where they point. ``--verbose`` adds the listing times and the birth-time oddity.
    """
    # Asked before the walk, which can take long: a report named inside a folder the project
    # reads, or inside the project, is refused and nothing is walked.
    if report_path is not None:
        report_path = typed_path(report_path)
        _refuse_output_file(report_path, project, "index --dry-run")
        _refuse_existing([report_path], project, "index --dry-run", overwrite=overwrite)
    try:
        refuse_unreachable_roots(project.identity.roots, verb="a dry run")
    except RootUnreachableError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    settings = project.settings
    reports = []
    for declaration in project.identity.roots:
        if declaration.listing is not None:
            console.print(f"[bold]{declaration.root_uri}[/bold]: an archive's file list; skipped")
            continue
        report = dry_run(
            Path(declaration.mount_path),
            exclude_globs=tuple(settings.scan.exclude_globs),
            workers=settings.scan.workers,
            limit=limit,
            seconds=seconds,
            project=project.path,
            include_hidden=settings.scan.include_hidden,
        )
        reports.append(report)
        for line in _dry_run_lines(declaration.mount_path, report, verbose=verbose):
            console.print(line, highlight=False, soft_wrap=True)
    if report_path is None:
        console.print("Ingence wrote nothing and opened no file.")
        return
    try:
        write_replacing(report_path, json.dumps(reports, indent=1) + "\n")
    except OSError as exc:
        raise _output_not_written(report_path, "index --dry-run", exc) from exc
    # Said once the report is written, and of the folders read alone: the report is a file written.
    # Never wrapped, as the report's own lines are not: a long path broken across two lines is
    # copied with a space in it.
    console.print(
        "Ingence wrote nothing in your data folders and opened no file.",
        soft_wrap=True,
    )
    console.print(f"The report is at {report_path}.", soft_wrap=True)


#: Where a link points, said as a person would (``core/links.py`` names the shapes).
_LINK_SHAPES = {
    "network_share": "to another computer's share",
    "drive": "to a drive letter",
    "absolute": "written as a full path",
    "relative": "written from where it is",
}


def _size_words(size: int) -> str:
    """A size in the decimal units a file browser shows: ``72 bytes``, ``164 KB``, ``1.2 GB``."""
    if size < 1000:
        return _plural(size, "byte")
    for unit, scale in (("KB", 1e3), ("MB", 1e6), ("GB", 1e9), ("TB", 1e12)):
        if size < scale * 1000 or unit == "TB":
            shown = size / scale
            return f"{shown:.0f} {unit}" if shown >= 10 else f"{shown:.1f} {unit}"
    return f"{size} bytes"  # pragma: no cover - the loop always returns


def _dry_run_lines(where: str, report: dict[str, Any], *, verbose: bool) -> list[str]:
    """What a dry run of one root found, in plain words, one line a fact."""
    walk, files, links, kinds = report["walk"], report["files"], report["links"], report["kinds"]
    stopped = walk["stopped_by"]
    how = "walked whole" if stopped == "the whole tree" else f"stopped at {stopped}"
    rate = walk["entries_per_second"]
    found = _counted(
        (
            (kinds.get("file", 0), "file"),
            (kinds.get("directory", 0), "folder"),
            (kinds.get("symlink", 0), "link"),
            (kinds.get("other", 0), "other item"),
        )
    )
    lines = [
        f"{escape(where)} ({how})",
        f"  {walk['entries']:,} items in {walk['seconds']} s"
        + (f", {rate:,.0f} a second" if rate else "")
        + f": {found}",
    ]
    if files["count"]:
        years = ", ".join(
            f"{year} ({count:,})" for year, count in files["modified_by_year"].items()
        )
        empty = f"{files['zero_bytes']:,} empty" if files["zero_bytes"] else "none empty"
        lines.append(
            f"  files: {_size_words(files['total_bytes'])}, {empty}, last changed in {years}"
        )
    shapes = ", ".join(
        f"{count:,} {_LINK_SHAPES.get(shape, shape)}"
        for shape, count in sorted(links["written_as"].items(), key=lambda pair: -pair[1])
    )
    lines.append(f"  links: {shapes}" if links["count"] else "  links: none")
    if report.get("excluded_hidden"):
        lines.append(f"  left out, hidden names: {report['excluded_hidden']:,}")
    if report["excluded"]:
        lines.append(f"  left out, by the settings' exclusions: {report['excluded']:,}")
    lines += _left_out_lines(
        [(str(item["as"]), str(item["path"]), False) for item in report["left_out"]]
    )
    if report["unenterable_folders"]:
        lines.append(f"  could not enter {_plural(report['unenterable_folders'], 'folder')}")
    if report["errors"]:
        said = ", ".join(f"{name} {count:,}" for name, count in sorted(report["errors"].items()))
        lines.append(f"  could not read: {said}")
    if verbose:
        lines.append(
            f"  a listing's median {walk['listing_seconds_median']} s, the longest "
            f"{walk['listing_seconds_max']} s; {files['born_after_modified']:,} files born "
            f"after their last change"
        )
    return lines


def _open(project_path: Path | None) -> Project:
    """Open the named project, or find the one containing the working directory.

    A project one of whose own folders is a link is refused here, in words (``check_layout``),
    for every command: none deletes or replaces anything through it.
    """
    try:
        if project_path is not None:
            return Project.open(project_path)
        return discover(Path.cwd())
    except ProjectLayoutError as exc:
        typer.echo(f"ingence: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _refuse_output_file(out: Path, project: Project, verb: str) -> None:
    """Leave, saying why and where it may go, when a file a person names would be written inside
    a folder the project reads or inside the project (its exports folder apart)."""
    try:
        check_output_file(out, project=project.path, roots=declared_roots(project))
    except DestinationRefusedError as exc:
        typer.echo(f"ingence {verb}: refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _refuse_existing(
    paths: Sequence[Path], project: Project, verb: str, *, overwrite: bool
) -> None:
    """Leave, saying what is there, when a file a person names is already there and the command
    was not given ``--overwrite``; asked of every file before any is written."""
    try:
        refuse_existing(paths, project=project.path, overwrite=overwrite)
    except OutputExistsError as exc:
        typer.echo(f"ingence {verb}: refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc


#: The one option every command writing a file a person names takes to replace one already there.
_OVERWRITE_HELP = (
    "Replace a file that already has this name. Without it, Ingence writes nothing if a file "
    "or link is there. Ingence always replaces files in the project's projections/exports folder."
)


def _output_not_written(out: Path, verb: str, exc: OSError) -> typer.Exit:
    """Say that a file could not be written, and why, as the exit the caller raises."""
    if isinstance(exc, ReadOnlyFileError):
        said = f"could not write {out} (read-only)"
    else:
        said = f"could not write {out}: {exc.strerror or exc}"
    typer.echo(f"ingence {verb}: {said}", err=True)
    return typer.Exit(code=1)


# ---------------------------------------------------------------------------


#: What ``init --rules`` took. The option is retired: every project reads the same rules, so it
#: chooses nothing. The command still takes the option and says so, so that a command written for an
#: older engine runs.
RETIRED_INIT_RULES = ("revision-4", "shipped")


def domain_priors_named(named: Sequence[str]) -> tuple[str, ...]:
    """The shipped domains ``init --domain`` writes a prior for, checked against what ships.

    Every domain section loads in every project, so naming one loads nothing more. A named domain is
    a prior: a confirm on ``pack:<domain>`` in the answer log. The prior reads a spelling that
    several domains share as this domain's, where a folder's own files do not decide.
    """
    from ingence.dictionary.store import PACKS_DIRECTORY, load_sections

    shipped = {
        section.domain for section in load_sections(PACKS_DIRECTORY) if section.domain is not None
    }
    wanted = sorted({name.strip().casefold() for name in named})
    unknown = [name for name in wanted if name not in shipped]
    if unknown:
        message = f"no shipped domain pack {unknown[0]!r}; they are {', '.join(sorted(shipped))}"
        raise ValueError(message)
    return tuple(wanted)


@app.command("init")
def init(
    directory: Annotated[Path, typer.Argument(help="Where to create the project.")] = Path("."),
    root: Annotated[
        list[str] | None,
        typer.Option(
            "--root",
            "-r",
            help=(
                "A data folder to scan: a folder, or a public archive's file list written "
                "`listing:<archive>:<identifier>`. You can repeat it."
            ),
        ),
    ] = None,
    label: Annotated[
        list[str] | None,
        typer.Option("--label", "-l", help="A name for the matching data folder."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help=(
                "The project name, shown on every computer that opens the project. "
                "Without it, Ingence uses the project folder's name."
            ),
        ),
    ] = None,
    depth: Annotated[
        str | None,
        typer.Option("--depth", help="The reading depth: metadata (Quick), standard or deep."),
    ] = None,
    domain: Annotated[
        list[str] | None,
        typer.Option(
            "--domain",
            help=(
                "A shipped domain this project's data is mostly of (microscopy, "
                "biochemistry, ...), written as a prior: every domain's words load either way. "
                "May be repeated."
            ),
        ),
    ] = None,
    all_domains: Annotated[
        bool,
        typer.Option(
            "--all-domains",
            hidden=True,
            help="Retired: every shipped domain loads in every project.",
        ),
    ] = False,
    rules: Annotated[
        str | None,
        typer.Option("--rules", hidden=True, help="Retired: every project reads the same rules."),
    ] = None,
) -> None:
    """Create a project.

    Ingence saves what it finds in the project folder. Ingence never changes your files. The project
    folder can be anywhere, even inside a data folder: every scan leaves it out.

    ``--depth`` sets the reading depth of this project once. Use ``metadata`` (Quick) for a data
    folder on another person's drive: it opens no file.
    """
    # No rules file is written. The rules are the engine's own. ``--rules`` is retired, hidden, and
    # chooses nothing.
    if rules is not None and rules not in RETIRED_INIT_RULES:
        console.print("[red]--rules is retired: every project reads the same rules[/red]")
        raise typer.Exit(code=2)
    try:
        priors = domain_priors_named(domain or ())
        settings = Settings(
            **({"depth": depth_named(depth).value} if depth is not None else {}),
            dictionary=DictionarySettings(),
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    named = root or []
    listings = [value for value in named if parse_listing_uri(value) is not None]
    folders = [Path(value) for value in named if parse_listing_uri(value) is None]
    try:
        project = Project.create(
            directory,
            created_at=_now(),
            roots=folders,
            labels=label or [],
            settings=settings,
            listings=listings,
            name=name,
            # What a person told init is written whatever its value, such as the depth, when named.
            chosen={**({"depth": None} if depth is not None else {})},
        )
    except (ProjectExistsError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    # A domain that a person names is a prior, written into the answer log: a confirm on
    # ``pack:<domain>``, which a run reads as it reads every answer.
    if priors:
        from ingence.dictionary.packs.proposals import INIT_ACTOR
        from ingence.record.log import append_event
        from ingence.record.targets import pack_target

        for prior in priors:
            append_event(
                project.path,
                kind=RecordEventKind.CONFIRM,
                actor=INIT_ACTOR,
                occurred_at=_now(),
                target=pack_target(prior),
                against_run_id="",
                payload={"source": {"page": "init"}, "verdict": "accept"},
            )

    console.print(f"Project created at [bold]{escape(str(project.path))}[/bold]", soft_wrap=True)
    console.print(f"  name [bold]{escape(project.name)}[/bold]", soft_wrap=True)
    if rules is not None:
        console.print("  --rules is retired: every project reads the same rules")
    if all_domains:
        console.print("  --all-domains is retired: every shipped domain loads in every project")
    console.print(
        "Domain words: every shipped domain loads"
        + (f"; prior: {', '.join(priors)}" if priors else "")
    )
    for declaration in project.identity.roots:
        name = declaration.label or Path(declaration.mount_path).name
        how = (declaration.case_established_by or "").replace("_", " ")
        console.print(
            f"  data folder [bold]{escape(name)}[/bold]  {escape(str(declaration.mount_path))}  "
            f"(case {declaration.case_sensitivity.value}" + (f": {how})" if how else ")"),
            soft_wrap=True,
        )
    # A root may be offline or a share not mounted yet, so the project is made anyway. One plain
    # line per root that cannot be reached says that `index` will need it.
    for declaration in project.identity.roots:
        if declaration.listing is None and not Path(declaration.mount_path).is_dir():
            console.print(
                f"  [yellow]warning:[/yellow] Ingence cannot reach the data folder "
                f"{escape(str(declaration.mount_path))} now. It made the project. Connect the "
                "folder before you run `ingence index`.",
                soft_wrap=True,
            )
    if not project.identity.roots:
        console.print("  no data folder added yet; add one with [bold]--root[/bold]")
    else:
        console.print(f"  Next: {_command('index', project.path)}", highlight=False, soft_wrap=True)


@app.command("index")
def index(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Print no progress lines.")] = False,
    rebind: Annotated[
        bool,
        typer.Option(
            "--rebind",
            help=(
                "Read every saved name again with the current Dictionary and answer log. Ingence "
                "does not open your data folders: it uses the last file list."
            ),
        ),
    ] = False,
    spelling: Annotated[
        list[str] | None,
        typer.Option(
            "--spelling",
            help=(
                "With --rebind: read again only the names that could hold this spelling, and every "
                "folder name. Ingence keeps the results of every other name. You can repeat it."
            ),
        ),
    ] = None,
    dry_run_only: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Report what each data folder holds: counts, file types, sizes, years, extensions "
                "and links. Ingence opens no file and saves nothing."
            ),
        ),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", help="With --dry-run: stop after this many items (0: all)."),
    ] = 0,
    seconds: Annotated[
        float,
        typer.Option(
            "--seconds", help="With --dry-run: stop after this many seconds (0: no limit)."
        ),
    ] = 0.0,
    report_path: Annotated[
        Path | None,
        typer.Option("--report", help="With --dry-run: also write the report here, as JSON."),
    ] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help=_OVERWRITE_HELP)] = False,
    fetch: Annotated[
        bool,
        typer.Option(
            "--fetch",
            help=(
                "First download each archive's file list into the project, one request at a time. "
                "Ingence logs every request. Without it, Ingence uses the file list it downloaded "
                "before."
            ),
        ),
    ] = False,
    wait: Annotated[bool, typer.Option("--wait", help=_WAIT_HELP)] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help=_VERBOSE_HELP)] = False,
) -> None:
    """List the files in every data folder.

    Ingence reads names, sizes and dates, and opens no file. Ingence never changes your files. It
    records each error and goes on. You can stop it at any time with Ctrl-C. A stopped scan saves
    nothing, and the last scan stays current.

    ``--rebind`` reads the saved names again with your latest answers. It does not list the files
    again, so it works when your data folders are not connected.
    """
    try:
        project = _open(project_path)
    except NotAProjectError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not project.identity.roots:
        console.print(
            "[yellow]This project has no data folder, so there is nothing to scan.[/yellow]"
        )
        raise typer.Exit(code=1)
    if spelling and not rebind:
        console.print("[red]--spelling reads some names again, which only --rebind does.[/red]")
        raise typer.Exit(code=2)
    if fetch:
        if rebind or dry_run_only:
            console.print("[red]--fetch reads archives; --rebind and --dry-run read none.[/red]")
            raise typer.Exit(code=2)
        _fetch_listings(project)
    if dry_run_only:
        if rebind:
            console.print(
                "[red]--dry-run lists the data folders, and --rebind reads the saved file list. "
                "Use only one.[/red]"
            )
            raise typer.Exit(code=2)
        _dry_run(
            project,
            limit=limit,
            seconds=seconds,
            report_path=report_path,
            verbose=verbose,
            overwrite=overwrite,
        )
        return

    run = _new_run_id()
    started = _now()
    token = CancellationToken()
    reporter = ProgressReporter(
        ProgressSnapshot(run_id=run, stage="census"),
        path=staging_path(project.path, run).with_suffix(".progress.json"),
        project=project.path,
        token=token,
        on_update=None if quiet else _render,
    )

    # The extractors in force enter each file's processing fingerprint, so reuse sees an extractor
    # that changed and is not shadowed by what an older one produced. Without them, no fingerprint
    # would carry an extractor version at all.
    load_extractors()
    command = "ingence index" + (" --rebind" if rebind else "")
    try:
        with _one_publisher(project.path, command, wait=wait), cancel_on_signal(token), reporter:
            report = run_census(
                project,
                run_id=run,
                started_at=started,
                token=token,
                reporter=reporter,
                extractor_registry=EXTRACTORS,
                rebind=rebind,
                spellings=tuple(spelling) if spelling else None,
            )
    except (RootUnreachableError, ValueError) as exc:
        reporter.remove_snapshot()
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    reporter.remove_snapshot()

    if report.cancelled:
        console.print(
            f"\n[yellow]Stopped: {report.cancellation_reason}. "
            f"Ingence saved nothing, and the previous scan is still current.[/yellow]"
        )
        raise typer.Exit(code=130)

    failed = [outcome for outcome in report.invariants if not outcome.passed]
    if not report.published:
        console.print("\n[red]Ingence did not save this scan.[/red]")
        for outcome in failed:
            console.print(f"  [red]{outcome.invariant_id}[/red] {outcome.statement}")
            for example in outcome.examples[:5]:
                console.print(f"      {example}")
        raise typer.Exit(code=1)

    counts = report.counts
    # What was read, then what comes next. The reuse reasons and the members carried are the
    # developer's, and print with --verbose.
    kinds = _counted(
        (
            (counts.files, "file"),
            (counts.directories, "folder"),
            (counts.symlinks, "link"),
            (counts.other, "other item"),
        )
    )
    head = "Read the names of" if rebind else "Listed"
    again = " again" if rebind else ""
    console.print(f"\n{head} [bold]{counts.entries:,}[/bold] items{again} as run {run}: {kinds}")
    if verbose and counts.reuse:
        summary = ", ".join(
            f"{decision.value} {number}" for decision, number in sorted(counts.reuse.items())
        )
        console.print(f"  reuse: {summary}")
    if verbose and counts.carried:
        console.print(f"  {counts.carried} container members carried from the last run")
    if counts.tombstones:
        console.print(f"  {_plural(counts.tombstones, 'item')} gone since the last run")
    for line in _left_out_lines(
        [(path.recognised_as.value, path.relative_path, path.indexed) for path in report.recognised]
    ):
        console.print(line, highlight=False)
    for line in _exclusion_lines(excluded_names(counts)):
        console.print(line)
    if counts.errors:
        console.print(
            f"  [yellow]{_plural(counts.errors, 'error')}, each recorded; the scan went on[/yellow]"
        )
    # What it read of the names, each count saying what it counts.
    if project.settings.dictionary.tokenise_names:
        from ingence.scan.reading_figure import reading_figure, reading_said

        with open_catalog(project.path) as catalog:
            console.print(f"  {reading_said(reading_figure(catalog))}", highlight=False)
    # A command that a person copies is never broken across lines. The step that the README names
    # next depends on what ran. After the census, a dive opens the files. After re-reading the names
    # offline, which opens nothing, the rules run again ("index --rebind ..., then infer"). A
    # project made to read names and dates only never opens a file, so no dive follows.
    names_only = not rebind and not _depth_opens_files(project.settings.depth)
    following = "infer" if rebind or names_only else "dive"
    if following == "dive" and not _contents_read(project.path):
        # Said plainly, and only while it is true. A walk over a project whose files were read keeps
        # their readings and says nothing.
        console.print(
            "  The files' contents have not been read yet; the next command reads them.",
            highlight=False,
        )
    console.print(f"  Next: {_command(following, project.path)}", highlight=False, soft_wrap=True)
    if names_only:
        console.print(
            f"  This project reads names, sizes and dates only (depth "
            f"{depth_word(depth_named(project.settings.depth))}), so it reads no file contents."
        )


def _conclusions_said(from_rules: int, carried: int) -> str:
    """The run's conclusions as the Dashboard counts them, and what each part is.

    ``56 results (36 from the rules, 20 from the file list)``; the census's own are carried into
    every inference run, so they are in the Dashboard's figure and a rules-only figure would
    disagree with it.
    """
    if not carried:
        return _plural(from_rules, "result")
    return (
        f"{_plural(from_rules + carried, 'result')} "
        f"({from_rules:,} from the rules, {carried:,} from the file list)"
    )


def _contents_read(project: Path) -> bool:
    """Whether the current run holds any file whose contents were read."""
    with open_catalog(project) as catalog:
        row = catalog.query(
            "SELECT count(*) FROM entries WHERE scan_outcome IN "
            "('extracted', 'extracted_with_fallback')"
        ).fetchone()
    return bool(row and row[0])


def _depth_opens_files(depth: str) -> bool:
    """Whether a dive at this project's declared depth opens files (an unreadable depth does)."""
    try:
        return PROFILES[depth_named(depth)].opens_files
    except ValueError:
        return True


def _fetch_listings(project: Project) -> None:
    """Fetch every listing root's listing, say what happened, and stop where none can be read."""
    from ingence.listing.fetch import fetch_listing_roots

    outcome = fetch_listing_roots(project)
    for uri, rows in outcome.fetched:
        console.print(f"  fetched {uri}: {rows} files listed")
    for uri, why in outcome.left:
        console.print(f"  [yellow]{uri}: {why}[/yellow]")
    for uri, why in outcome.failed:
        console.print(f"  [red]{uri}: {why}[/red]")
    console.print(
        f"  {outcome.requests} requests, {outcome.bytes_received} bytes received; "
        f"each listing's requests are logged beside it"
    )


#: What each of Ingence's own things is called where a walk says it left one out.
_OWN_THINGS = {
    "this_project": "this project",
    "project": "another Ingence project",
    "run": "an Ingence run",
    "installation": "Ingence itself",
}


def _left_out_lines(found: Sequence[tuple[str, str, bool]]) -> list[str]:
    """Say which of Ingence's own files a walk left out, or read as data, one line per kind.

    ``found`` holds each recognized path's kind, path and whether it was indexed anyway. The
    project's own names are many where its folder is a root; they are said on one line.
    """
    grouped: dict[tuple[str, bool], list[str]] = {}
    for kind, path, indexed in found:
        grouped.setdefault((kind, indexed), []).append(path)
    lines: list[str] = []
    for (kind, indexed), paths in sorted(grouped.items()):
        how = "read as data" if indexed else "left out"
        # Escaped: a folder may be named like the console's markup (``[/old]``).
        said = ", ".join(escape(path) for path in sorted(paths))
        lines.append(f"  {how}, {_OWN_THINGS.get(kind, kind)}: {said}")
    return lines


#: What each kind of entry is called in a line a person reads (the final line of ``index`` says
#: folders and links, so a left-out count does too).
_ITEM_NOUN = {"directory": "folder", "symlink": "link", "other": "other item"}


def _exclusion_lines(excluded: Sequence[ExcludedNames]) -> list[str]:
    """Say what a census left out on purpose, summed over its roots.

    The output is one line per reason and pattern, by type. An excluded folder was not walked, so
    what it holds is not counted. The line says so, so that a folder count is not read as a count of
    its contents.
    """
    totals: dict[tuple[str, str | None], Counter[str]] = {}
    for item in excluded:
        totals.setdefault((item.reason.value, item.pattern), Counter())[item.entry_type.value] += (
            item.count
        )
    labels = {"hidden": "hidden names", "max_depth": "below the depth limit"}
    lines: list[str] = []
    for (reason, pattern), by_type in sorted(
        totals.items(), key=lambda pair: (pair[0][0], pair[0][1] or "")
    ):
        label = labels.get(reason, pattern or reason)
        parts = ", ".join(
            _plural(number, _ITEM_NOUN.get(kind, kind)) for kind, number in sorted(by_type.items())
        )
        walked = " (folders not opened)" if by_type.get("directory") else ""
        lines.append(f"  left out, {label}: {parts}{walked}")
    return lines


def _names_reader_version(project: Project) -> str:
    """The tokenizer version a census of this project reads names with, its settings included."""
    from ingence.dictionary.normalize import Normaliser, reading_options

    return Normaliser(None, **reading_options(project.settings.dictionary)).version


def _last_census_exclusions(project: Project) -> tuple[str, list[ExcludedNames]] | None:
    """Return the most recent census run and what it left out, or None if none records it."""
    runs = sorted((p.name for p in (project.path / "runs").iterdir() if p.is_dir()), reverse=True)
    for run in runs:
        with open_catalog(project.path, run_id=run) as catalog:
            columns = {row[0] for row in catalog.query("DESCRIBE SELECT * FROM run").fetchall()}
            if "excluded_json" not in columns:
                continue
            row = catalog.query("SELECT profile, excluded_json FROM run").fetchone()
        if row is None or row[0] != "census":
            continue
        return run, [ExcludedNames.model_validate(item) for item in json.loads(row[1])]
    return None


def _render(snapshot: ProgressSnapshot) -> None:
    """Print a single progress line. Counts, never an invented percentage."""
    console.print(
        f"  {snapshot.entries_seen:,} items  {snapshot.directories_seen:,} folders opened"
        f"  {snapshot.entries_per_second:.0f}/s  {snapshot.current}",
        end="\r",
        highlight=False,
    )


def _output_is_a_terminal() -> bool:
    """Whether what the command prints goes to a terminal, by the output's own ``isatty``.

    Not rich's ``is_terminal``: that says whether colour may be shown, and ``FORCE_COLOR`` and
    ``TTY_COMPATIBLE`` switch it on for a pipe.
    """
    try:
        return bool(console.file.isatty())
    except (AttributeError, ValueError, OSError):
        return False


#: How often a dive on a terminal says how far it has got, in seconds.
DIVE_NOTE_SECONDS = 30.0


class _DiveNotes:
    """The lines a dive prints on a terminal: one at the start, then how far it has got."""

    def __init__(self) -> None:
        self.total: int | None = None
        self._last = time.monotonic()
        self._began = self._last

    def start(self, files: int, granted_seconds: float | None) -> None:
        """Called once, when the dive knows how many files it will try and how long it may take."""
        self.total = files
        self._began = self._last = time.monotonic()
        if not files:
            return
        limit = (
            f"a time limit of {granted_seconds:,.0f} seconds"
            if granted_seconds is not None
            else "no time limit"
        )
        console.print(
            f"Reading up to {files:,} {'file' if files == 1 else 'files'}, with {limit}. "
            f"Ingence prints a line every {DIVE_NOTE_SECONDS:.0f} seconds.",
            highlight=False,
        )

    def update(self, snapshot: ProgressSnapshot) -> None:
        """The reporter's callback: one line each time the interval has passed."""
        if self.total is None or not self.total or snapshot.finished:
            return
        now = time.monotonic()
        if now - self._last < DIVE_NOTE_SECONDS:
            return
        self._last = now
        console.print(
            f"  {snapshot.entries_examined:,} of {self.total:,} files tried so far "
            f"({now - self._began:.0f} s)",
            highlight=False,
        )


@app.command("dive")
def dive(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    depth: Annotated[
        str | None,
        typer.Option(
            "--depth",
            "--profile",
            help=(
                "The reading depth: metadata, standard or deep. Without it, Ingence uses the "
                "project's depth."
            ),
        ),
    ] = None,
    budget: Annotated[
        float | None,
        typer.Option("--budget", help="The time limit for reading contents, in seconds."),
    ] = None,
    wait: Annotated[bool, typer.Option("--wait", help=_WAIT_HELP)] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help=_VERBOSE_HELP)] = False,
) -> None:
    """Read the contents of files, within a time limit.

    Ingence never changes your files. The time limit applies only to reading contents. Reaching it
    never makes the scan fail: the files Ingence did not reach stay "not read yet". Run the command
    again to go on from there.

    Without ``--depth``, Ingence uses the project's reading depth, which ``init --depth`` sets. The
    scan records which depth it used.
    """
    try:
        opened = _open(project_path)
    except NotAProjectError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        chosen = depth_named(depth if depth is not None else opened.settings.depth)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not PROFILES[chosen].opens_files:
        # The facts, said in the word the person used (``metadata``, not the profile's own name);
        # going deeper is possible, and the folder's owner decides it.
        console.print(
            f"[yellow]This project reads names, sizes and dates only (depth "
            f"{depth_word(chosen)}), so `ingence dive` reads no contents. To read contents, use "
            f"--depth standard or --depth deep, if the folder's owner agrees.[/yellow]"
            if depth is None
            else f"[yellow]Depth {escape(depth.strip())} reads names, sizes and dates only, so it "
            f"reads no contents. To read contents, use --depth standard or --depth deep, "
            f"if the folder's owner agrees.[/yellow]"
        )
        raise typer.Exit(code=1)

    run = _new_run_id()
    token = CancellationToken()
    # On a terminal only (the census's progress line is the model): a line when the dive starts and
    # one every half minute, so a long dive is not a blank screen. Nothing where output is piped.
    notes = _DiveNotes() if _output_is_a_terminal() else None
    reporter = (
        ProgressReporter(
            ProgressSnapshot(run_id=run, stage="dive"),
            token=token,
            on_update=notes.update,
        )
        if notes is not None
        else None
    )
    # The Ctrl-C ladder: the first press stops and keeps what was read, the second discards it, the
    # third exits.
    with (
        _one_publisher(opened.path, "ingence dive", wait=wait),
        cancel_on_signal(token, keep_first=True),
    ):
        try:
            with reporter if reporter is not None else contextlib.nullcontext():
                report = run_deepen(
                    opened,
                    run_id=run,
                    started_at=_now(),
                    profile=chosen.value,
                    budget_seconds=budget,
                    token=token,
                    reporter=reporter,
                    announce=notes.start if notes is not None else None,
                )
        except (CommitError, RootUnreachableError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

    # A cancelled dive says so and ends with a status of 1, as infer does. Otherwise it would print
    # "Read N files ... as run X" for a run it had abandoned and end with 0, and a script, and a
    # person, would take a Ctrl-C for a dive that finished.
    if report.cancelled:
        console.print(
            f"\n[yellow]Stopped: {report.cancellation_reason or 'stopped'}. "
            "Ingence saved nothing, and the previous scan is still current.[/yellow]"
        )
        raise typer.Exit(code=1)
    if report.stopped:
        # Stopped and kept: what was read is a published run, said below as any dive's is, with the
        # files not reached counted.
        console.print(
            f"\n[yellow]Stopped: {report.stop_reason or 'stopped'}. What was read so far is "
            "published; the files not reached stay unexamined and the next dive goes on from "
            "there.[/yellow]"
        )

    # What was read, what needs the person, and what comes next. Why each file was chosen, the
    # digest, the budget spent and the publish timings print with --verbose.
    failed = f", {report.failed:,} could not be read" if report.failed else ""
    # Files whose stat failed before anything was read are "not read yet" and not failed, and are
    # said apart.
    unreachable = f", {report.not_reached:,} could not be reached" if report.not_reached else ""
    # A file with no content to read (empty, or its content elsewhere) is said, not hidden.
    empty = f", {report.skipped:,} with no content to read" if report.skipped else ""
    unreached = report.budget.unreached if report.budget is not None else 0
    if not report.examined and not unreached:
        # Every file was read before, or set aside by the settings (said below).
        console.print(f"Nothing left to read, as run {run}")
    else:
        # Files read again because the reader for their type changed, said apart from the rest so a
        # person can tell why a dive over a pool already read took time.
        again = (
            f", {report.reader_newer_read:,} read again because the reader for their type changed"
            if report.reader_newer_read
            else ""
        )
        console.print(
            f"Read [bold]{report.extracted:,}[/bold] "
            f"{'file' if report.extracted == 1 else 'files'} "
            f"({report.bytes_read / 1e6:.1f} MB) as run {run}{again}{failed}{unreachable}{empty}"
        )
    if verbose:
        console.print(
            f"  examined {report.examined}: {report.extracted} extracted, {report.skipped} "
            f"skipped by policy, {report.failed} failed, {report.bytes_read / 1e6:.1f} MB read"
        )
    if report.settings_upgraded:
        console.print(
            "  [yellow]settings.yaml: hash.mode was none, an older init's default; it is now need "
            "(write none there to hash nothing)[/yellow]"
        )
    if report.set_aside_by_settings:
        # Files that the project's extraction settings left unread, apart from the above.
        aside = ", ".join(
            f"{count} {reason}" for reason, count in sorted(report.set_aside_by_settings.items())
        )
        console.print(f"  not read, because of the settings: {aside}")
    if report.unnamed_said:
        console.print(f"  [yellow]{report.unnamed_said}[/yellow]")
    if report.roots_on_shares:
        # A root on a network share, left unhashed as hash.skip_remote_filesystems says (set by the
        # settings or proposed by the depth).
        kinds = ", ".join(
            f"{count} on {kind}" for kind, count in sorted(report.roots_on_shares.items())
        )
        console.print(
            f"  on a network share: {kinds}; {report.unhashed_on_shares} files read there "
            f"without a digest ({report.unhashed_on_shares_because or 'need hashes a share'})"
        )
    if verbose and report.chosen_because:
        # Why each file was read, and the digest that the pass computed and who chose it.
        why = ", ".join(
            f"{count} {reason}" for reason, count in sorted(report.chosen_because.items())
        )
        console.print(f"  chosen because: {why}")
    if verbose and report.reader_newer_by:
        # Which readers changed, and how many files each one chose.
        readers = ", ".join(
            f"{name} {count}" for name, count in sorted(report.reader_newer_by.items())
        )
        alone = (
            f" ({report.signed_again} of them by their signature alone)"
            if report.signed_again
            else ""
        )
        console.print(
            f"  reader_newer: files whose reading came from an older version of a reader, by "
            f"reader: {readers}; {report.reader_newer_read} of "
            f"{report.chosen_because.get('reader_newer', 0)} read in this dive{alone}"
        )
    if verbose and report.hash_mode is not None:
        chose = {
            "settings": "as the settings say",
            "profile": "the depth's preset",
            "need": "what a comparison needs",
        }
        console.print(
            f"  digest: {report.hash_mode} ({chose.get(str(report.hash_mode_set_by), '')})"
            + (f", {report.digested_by_need} read whole by need" if report.digested_by_need else "")
            + (
                f", {report.nominated_by_need} above the whole-digest ceiling left nominated"
                if report.nominated_by_need
                else ""
            )
            + (f", {report.digested_alone} digested alone" if report.digested_alone else "")
        )
    if report.changed_during_read:
        console.print(
            f"  [yellow]{report.changed_during_read} changed while Ingence read them. Ingence kept "
            "no results for them, and the next `ingence dive` reads them again.[/yellow]"
        )
    if verbose and report.read_despite:
        # Separate from "skipped by policy", which means there was nothing to read.
        said = ", ".join(f"{count} '{word}'" for word, count in sorted(report.read_despite.items()))
        console.print(f"  read after the rest of their turn, as a person answered: {said}")
    if report.programs_found:
        # The folders the Rulebook defers beneath, and what the dive did there.
        done = (
            "left unread, as the settings say"
            if report.left_beneath_programs
            else "read after every other file"
        )
        console.print(
            f"  {report.files_beneath_programs} files beneath {report.programs_found} "
            f"program folder{'' if report.programs_found == 1 else 's'}: {done}"
        )
    # Two clocks: the work granted, and the wall clock that the depth or the settings put on the
    # whole pass. The one that ran out is the one named.
    work_granted = report.budget.granted_seconds if report.budget is not None else None
    wall_granted = report.budget.wall_seconds if report.budget is not None else None
    if report.budget is not None and (
        report.stopped or work_granted is not None or wall_granted is not None
    ):
        if verbose:
            clocks = []
            if work_granted is not None:
                clocks.append(
                    f"{report.budget.spent_seconds:.1f}s of {work_granted:.0f}s of reading spent"
                )
            if wall_granted is not None:
                clocks.append(
                    f"{report.wall_seconds or 0.0:.0f}s of {wall_granted:.0f}s on the clock"
                )
            console.print(f"  budget: {', '.join(clocks)}")
            console.print(f"  workers: {report.workers}")
        if report.budget.unreached:
            if report.stopped and report.budget.exhausted_by is None:
                limit = "before the stop"
            elif report.budget.exhausted_by == "wall" and wall_granted is not None:
                limit = f"within the {wall_granted:.0f}-second limit on the whole read"
            else:
                limit = f"within the {work_granted or 0:.0f}-second limit"
            console.print(
                f"  [yellow]{report.budget.unreached:,} items not reached {limit}. The "
                "next `ingence dive` goes on from there.[/yellow]"
            )
    if verbose and report.publish_seconds:
        phases = ", ".join(
            f"{phase} {seconds:.1f}s" for phase, seconds in report.publish_seconds.items()
        )
        console.print(f"  publish: {phases}")
    console.print(f"  Next: {_command('infer', opened.path)}", highlight=False, soft_wrap=True)


@app.command("see")
def see(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=(
                "The project folder to open. Without it, Ingence looks in this folder and the "
                "folders above it. If it finds none, the page asks."
            ),
        ),
    ] = None,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help=(
                "The port for the page. The default, 0, picks a free port. Ingence prints the "
                "address when it starts."
            ),
        ),
    ] = 0,
    open_browser: Annotated[
        bool,
        typer.Option(
            "--open/--no-open",
            help="Open the page in your browser (the default), or only print its address.",
        ),
    ] = True,
) -> None:
    """Open the Dashboard in your web browser.

    The Dashboard is a web page that Ingence serves from this computer. Only this computer can open
    it. Its address starts with http://127.0.0.1: and ends with a port number. The command prints
    the address and opens it in your browser. The page needs no internet connection.

    Ingence opens the project named with --project. Without it, Ingence uses the project in this
    folder, or in the nearest folder above it. If there is none, the page lets you open an earlier
    project or make a new one. A project with no scan yet shows what it will read. It starts when
    you click **Start scan**.

    To stop the Dashboard, press Ctrl-C in this terminal. Closing the browser tab does not stop it.
    If you have saved changes that are not committed, it asks whether to commit them first.

    Ingence never changes your files. It writes your answers to the answer log in the project
    folder. In your settings folder, it saves the list of recent projects and your name. A project
    made from the landing page gets its own new folder. Answers and changed settings wait for
    **Re-run**. Nothing runs until you click it.

    The Dashboard works when your data folders are not connected, because it reads only the project.
    Without them, it cannot open files or check digests, and it says so.

    For example, "ingence see --project ~/ingence-projects/hela-screen --port 8765" opens that
    project on port 8765. "ingence see --no-open" prints the address and opens nothing.
    """
    if not WEB_ROOT.is_dir():
        console.print(f"[red]The Dashboard's files are missing:[/red] {WEB_ROOT}")
        raise typer.Exit(code=1)
    # No project found is the landing page and not an error. A project named with -p that is not one
    # is still refused by name, since that is a path somebody typed wrong.
    if project_path is None:
        try:
            found: Path | None = discover(Path.cwd()).path
        except NotAProjectError:
            found = None
        except (SettingsFileError, ProjectLayoutError) as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(code=1) from exc
    else:
        found = _open_or_exit(project_path).path
    if found is not None:
        from ingence.record.log import read_events

        # A Record a newer build wrote is refused before anything is served: the Dashboard would
        # otherwise start and fail on its first page (the current run's pointer is read, and
        # refused, by ``serve`` itself). The read is the one every page makes, and is held.
        read_events(found)
    # A project with no run yet opens on the landing page, which reads it on a person's click.
    serve(found, port=port, open_browser=open_browser)


@app.command("demo")
def demo(
    folder: Annotated[
        Path, typer.Argument(help="A folder to make. It must not exist yet, or it must be empty.")
    ] = Path("ingence-demo"),
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open a browser at the address.")
    ] = True,
) -> None:
    """Make a sample laboratory in a new folder, scan it and open the Dashboard.

    The new folder holds about 500 invented files in `pool`, with their answers, and a project that
    scans them. Ingence only reads `pool`. It writes the folder it makes, and the list of recent
    projects when the Dashboard opens.
    """
    from ingence.cli.demo import run_demo

    raise typer.Exit(code=run_demo(folder, open_browser=open_browser))


def _build_readouts_said(project: Path, run: str) -> None:
    """Build the run's readouts (``ingence.readouts``) and say what was skipped."""
    from ingence.readouts import build_readouts

    try:
        built = build_readouts(project, run)
    except Exception as exc:  # the views are a cache: said, never a failed infer
        console.print(f"[yellow]The views were not prepared: {escape(str(exc))}[/yellow]")
        return
    for view, why in sorted(built.get("skipped", {}).items()):
        console.print(f"[yellow]The {view} view was not prepared: {escape(str(why))}[/yellow]")


@app.command("infer")
def infer(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    wait: Annotated[bool, typer.Option("--wait", help=_WAIT_HELP)] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help=_VERBOSE_HELP)] = False,
) -> None:
    """Apply the rules to the current scan and save the results.

    Ingence reads only the project, never your data folders. It applies every rule to each file and
    folder that the rule is about. When a rule cannot decide, Ingence records that against the rule.
    Some rules raise checks for you to answer in the Dashboard. ``--verbose`` prints what each rule
    found.
    """
    opened = _open_or_exit(project_path)
    run = _new_run_id()
    token = CancellationToken()
    with _one_publisher(opened.path, "ingence infer", wait=wait), cancel_on_signal(token):
        try:
            report = run_inference(opened, run_id=run, started_at=_now(), token=token)
        except CommitError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        except RuleDeclarationError as exc:
            console.print(f"[red]The rules do not load:[/red] {exc}")
            raise typer.Exit(code=1) from exc

    if report.cancelled:
        console.print(
            "[yellow]Stopped. Ingence saved nothing, and the previous scan is still "
            "current.[/yellow]"
        )
        raise typer.Exit(code=1)
    # The readouts of the run just published: built here as the Dashboard's job builds them after
    # inference, so a page opening on this run finds its views made.
    _build_readouts_said(opened.path, run)

    # What was concluded, what waits for the person, and where to look. Each rule's counts, and why
    # a rule published nothing, print with --verbose.
    asked = _asked_in_run(opened.path)
    # "Raised", not "waiting": answers are not read here (the Dashboard counts what waits). One
    # figure that is the Dashboard's and `fix`'s: the census's own conclusions, carried into this
    # run, count with the rules'; the line says what each part is.
    said = (
        [_conclusions_said(report.claims, report.carried_claims)]
        if report.claims or report.carried_claims
        else []
    )
    if asked is None:
        said.append("checks could not be counted")
    elif asked:
        by_page = _asked_by_page(opened.path)
        said.append(_questions_raised(sum(by_page.values()) or asked, by_page))
    if report.candidates:
        said.append(_plural(report.candidates, "suggested pair"))
    concluded = ", ".join(said) or "nothing"
    over = f"[bold]{report.entries:,}[/bold] items"
    if report.parts:
        over += (
            f" ({report.entries - report.parts:,} files and folders, "
            f"{_plural(report.parts, 'part')} inside documents)"
        )
    console.print(f"Applied the rules to {over} as run {run}:")
    console.print(f"  {concluded}")
    # A rule that the engine cannot run yet is not a failure of the person's data. Every reason
    # counted here is the engine's (not built, not implemented, no index): one line.
    unable = sum(
        1 for tally in (*report.tallies, *report.groupings, *report.flags) if tally.not_evaluable
    ) + sum(1 for proposal in report.proposals if proposal.not_evaluable or proposal.within is None)
    if unable:
        console.print(
            f"  {_plural(unable, 'rule')} {'is' if unable == 1 else 'are'} "
            "not available in this version (--verbose says which)"
        )
    console.print(
        f"  See the results and answer checks in the Dashboard: {_command('see', opened.path)}",
        highlight=False,
        soft_wrap=True,
    )
    _say_retired_rules(opened.path)
    if verbose:
        _infer_detail(report, run)


def _say_retired_rules(project: Path) -> None:
    """Say once, where the Rulebook loads, that an older project's rules file is no longer read."""
    from ingence.rulebook.retired import read_retired, sentence

    found = read_retired(project / RULEBOOK_DIR)
    if found is None:
        return
    console.print(
        f"  {escape(sentence(found))}. To move it aside: "
        f"{_command('fix --set-aside-old-rules', project)}",
        highlight=False,
        soft_wrap=True,
    )


def _asked_in_run(project: Path) -> int | None:
    """How many questions the current run raises.

    The result is None where they could not be counted. That is said and is never a silent zero. No
    pack is proposed, so no line names a pack.
    """
    import duckdb

    with open_catalog(project) as catalog:
        if not catalog.has_run:
            return 0
        try:
            (asked,) = catalog.query(
                "SELECT count(*) FROM (SELECT DISTINCT f.rule_id, u.value "
                "FROM findings f, unnest(f.raised) AS u(value))"
            ).fetchone() or (0,)
        except duckdb.Error:
            return None
    return int(asked)


def _asked_by_page(project: Path) -> dict[str, int]:
    """The questions the current run raises, counted by the Dashboard page that asks each.

    Counted from the queue the Questions page is built from, by the page's own rule
    (:func:`~ingence.api.routes.pages.raised_by_page`): a question about experiments that may
    belong together is asked on the Groupings page, one about an installed program, or raised only
    inside one, on the Programs page. Empty where they could not be counted that way.
    """
    import duckdb

    from ingence.api.routes.pages import raised_by_page

    with open_catalog(project) as catalog:
        if not catalog.has_run:
            return {}
        try:
            return raised_by_page(catalog)
        except duckdb.Error:
            return {}


def _questions_raised(asked: int, by_page: dict[str, int]) -> str:
    """``3 questions raised``, and where the Dashboard asks them when that is not the Questions
    page alone: ``1 question raised, on the Groupings page`` or ``3 questions raised: 1 on the
    Questions page, 2 on the Groupings page``."""
    raised = f"{_plural(asked, 'check')} raised"
    asked_on = [
        (page, by_page[page])
        for page in ("Questions", "Groupings", "Programs")
        if by_page.get(page)
    ]
    if not asked_on or [page for page, _ in asked_on] == ["Questions"]:
        return raised
    if len(asked_on) == 1:
        return f"{raised}, on the {asked_on[0][0]} page"
    return f"{raised}: " + ", ".join(f"{count:,} on the {page} page" for page, count in asked_on)


def _infer_detail(report: InferenceReport, run: str) -> None:
    """``infer --verbose``: each rule's counts, and why a rule published or proposed nothing."""
    console.print(
        f"Evaluated [bold]{len(report.tallies)}[/bold] classifying, "
        f"[bold]{len(report.groupings)}[/bold] grouping and "
        f"[bold]{len(report.flags)}[/bold] flagging rules over "
        f"{report.entries} items as run {run}"
    )
    notes: list[str] = []
    table = _tally_table(
        ("concluded", "no evidence", "starved", "unexamined", "conflicting", "claims")
    )
    for tally in report.tallies:
        if tally.not_evaluable:
            notes.append(f"  {tally.rule_id}: not evaluable: {tally.not_evaluable}")
            continue
        table.add_row(
            tally.rule_id,
            str(tally.concluded),
            str(tally.no_evidence),
            str(tally.starved),
            str(tally.unexamined),
            str(tally.conflicting),
            str(tally.claims),
        )
        if tally.not_claim_ready:
            notes.append(f"  {tally.rule_id} publishes no conclusion: {tally.not_claim_ready}")
    console.print(table)
    groups = _tally_table(
        ("keyed", "no key", "unexamined", "conflicting", "classes", "grouped", "largest", "claims")
    )
    for tally in report.groupings:
        if tally.not_evaluable:
            notes.append(f"  {tally.rule_id}: not evaluable: {tally.not_evaluable}")
            continue
        groups.add_row(
            tally.rule_id,
            str(tally.concluded),
            str(tally.no_evidence + tally.starved),
            str(tally.unexamined),
            str(tally.conflicting),
            str(tally.classes),
            str(tally.grouped),
            str(tally.largest),
            str(tally.claims),
        )
        if tally.not_claim_ready:
            notes.append(f"  {tally.rule_id} publishes no conclusion: {tally.not_claim_ready}")
    if groups.row_count:
        console.print(groups)
    proposals = _tally_table(
        ("within", "pairs", "supported", "circumstantial", "blocked", "truncated")
    )
    unindexed: list[str] = []
    for proposal in report.proposals:
        if proposal.not_evaluable:
            notes.append(f"  {proposal.rule_id}: not evaluable: {proposal.not_evaluable}")
        elif proposal.within is None:
            unindexed.append(proposal.rule_id)
        elif proposal.not_ready:
            notes.append(f"  {proposal.rule_id} proposes nothing: {proposal.not_ready}")
        else:
            proposals.add_row(
                proposal.rule_id,
                proposal.within,
                str(proposal.pairs),
                str(proposal.supported),
                str(proposal.circumstantial),
                str(proposal.blocked),
                str(proposal.truncated),
            )
            for constraint, number in sorted(proposal.blocked_by.items()):
                notes.append(f"  {proposal.rule_id}: {constraint} blocked {number}")
    if proposals.row_count:
        console.print(proposals)
    if unindexed:
        notes.append(
            f"  {len(unindexed)} proposing rules name no set of files to draw pairs from, "
            f"so they propose nothing: {', '.join(unindexed)}"
        )
    flags = _tally_table(("raised", "no evidence", "starved", "unexamined", "conflicting"))
    for tally in report.flags:
        if tally.not_evaluable:
            notes.append(f"  {tally.rule_id}: not evaluable: {tally.not_evaluable}")
            continue
        flags.add_row(
            tally.rule_id,
            str(tally.findings),
            str(tally.no_evidence),
            str(tally.starved),
            str(tally.unexamined),
            str(tally.conflicting),
        )
    if flags.row_count:
        console.print(flags)
    console.print(
        f"  {report.claims} conclusions published, "
        f"{report.carried_claims} carried from the census, "
        f"{report.failures} failures recorded against rules unable to decide, "
        f"{report.candidates} pairs proposed, {report.findings} findings raised"
    )
    if report.streaming_reason:
        # Whether the pass streamed by folder, and why, as its run record says.
        held = "streamed by folder" if report.streamed else "the whole pool at once"
        console.print(f"  held {held}: {report.streaming_reason}", highlight=False)
    for note in notes:
        console.print(note, highlight=False)


def _tally_table(headings: tuple[str, ...]) -> Table:
    """Return a table with a rule column and one right-aligned column per count."""
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("rule", justify="left")
    for heading in headings:
        table.add_column(heading, justify="right")
    return table


@app.command("fix")
def fix(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    verify: Annotated[
        bool,
        typer.Option("--verify", help="Compare the last 2 runs and report any difference."),
    ] = False,
    set_aside_old_rules: Annotated[
        bool,
        typer.Option(
            "--set-aside-old-rules",
            help=(
                "Move an older project's unused rules file into rulebook/retired/. Ingence keeps "
                "your choices from it as the project's own rules."
            ),
        ),
    ] = False,
    shareable: Annotated[
        bool,
        typer.Option(
            "--shareable",
            help=(
                "Print the report with names replaced, so you can paste it into an email. The "
                "project, each data folder, each exclusion pattern and each run become "
                "`<project>`, `<root 1>`, `<pattern 1>` and `<run 1>`. Ingence leaves out the "
                "sample rows of --verify. Values you typed in an older project's rules file also "
                "become `<pattern 1>`."
            ),
        ),
    ] = False,
) -> None:
    """Check a project and report problems.

    By default, Ingence only reports and changes nothing. The most common problem is a staging
    folder that a stopped scan left behind. It is safe to delete, and the report says so.
    """
    if not shareable:
        _diagnose(
            project_path,
            verify=verify,
            set_aside_old_rules=set_aside_old_rules,
            names=AsWritten(),
            out=console,
        )
        return
    # The shareable report is written to a buffer and checked before anything is shown
    # (``cli/shareable.py``): the report's values are replaced as it is made, and the finished text
    # is searched once more for the names it should no longer hold.
    names = Placeholders(project_path)
    buffer = io.StringIO()
    code = 0
    try:
        _diagnose(
            project_path,
            verify=verify,
            set_aside_old_rules=set_aside_old_rules,
            names=names,
            out=plain_console(buffer),
        )
    except typer.Exit as stop:
        code = stop.exit_code
    except Exception as exc:
        # A traceback and an error's own text hold paths and names; the class is enough to say
        # what stopped.
        buffer.write(f"  ingence fix stopped before it finished: {type(exc).__name__}\n")
        code = 1
    text = names.backstop(buffer.getvalue())
    if names.replaced:
        text += backstop_notice(names.replaced) + "\n"
    typer.echo(text, nl=False)
    if code:
        raise typer.Exit(code=code)


def _diagnose(
    project_path: Path | None,
    *,
    verify: bool,
    set_aside_old_rules: bool,
    names: AsWritten,
    out: Console,
) -> None:
    """Print the diagnosis of a project to ``out``, every value from the data asked of ``names``.

    ``names`` is what makes the report shareable: :class:`AsWritten` returns each value as it
    came, so the report is the one ``fix`` printed before the option existed, and
    :class:`Placeholders` returns a placeholder for each name of the data.
    """
    try:
        project = _open(project_path)
    except NotAProjectError as exc:
        out.print(f"[red]{names.message(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    names.opened(project)

    out.print(f"Project [bold]{names.project(project.path)}[/bold]")
    out.print(
        f"  data format {project.identity.schema_version}, created by {project.identity.created_by}"
    )
    out.print(f"  {_plural(len(project.identity.roots), 'data folder')}")

    # A root out of reach is unchecked and not a fault. A diagnosis reads the project, so an
    # unmounted share or an unplugged disk is said and every other check still runs. Each root's own
    # entry is asked about once and nothing beneath it is read (``unreachable_roots``). A root that
    # is there and is not a folder is a problem.
    from ingence.core.project import NOT_A_FOLDER, unreachable_roots

    problems = 0
    for declaration, why in unreachable_roots(project.identity.roots):
        if why == NOT_A_FOLDER:
            out.print(
                f"  [red]data folder {escape(names.root(declaration.mount_path))}: "
                "not a folder[/red]",
                soft_wrap=True,
            )
            problems += 1
            continue
        out.print(
            f"  [yellow]data folder {escape(names.root(declaration.mount_path))}: cannot be "
            "reached now; "
            f"not checked[/yellow] ({escape(names.message(why))})",
            soft_wrap=True,
        )

    pointer = read_pointer(project.path)
    if pointer is None:
        out.print("  [yellow]no scan saved yet[/yellow]")
    else:
        out.print(f"  current run {names.run(pointer.run_id)}, updated {pointer.updated_at}")
        with open_catalog(project.path) as catalog:
            counted = catalog.counts()
            held = catalog.query(
                f"SELECT count(*) FROM entries WHERE NOT {filesystem_entry()}"  # noqa: S608
            ).fetchone()
            parts = int(held[0]) if held else 0
            for name, number in counted.items():
                if not number:
                    continue
                if name == "entries":
                    # The parts a dive recovered from inside documents, said apart.
                    out.print(f"    {_entries_said(number, parts)}")
                else:
                    # The Dashboard's word for the figure it shows in its header, so the two agree
                    # on what is counted (every conclusion in the run, the census's included).
                    out.print(f"    {'results' if name == 'claims' else name}: {number}")
            readers = sorted(
                str(row[0])
                for row in catalog.query(
                    "SELECT DISTINCT normalizer_version FROM components"
                ).fetchall()
            )
        # Names read by another tokenizer: a partial rebind refuses to mix two readings in one run,
        # so the stored names take one whole rebind first. This is said and never run.
        reading_now = _names_reader_version(project)
        if readers and readers != [reading_now]:
            out.print(
                f"  [yellow]Ingence read these names with name reader {', '.join(readers)}, and "
                f"this version uses {reading_now}[/yellow]. Run `ingence index --rebind` once to "
                "read every name again."
            )

    left_out = _last_census_exclusions(project)
    if left_out is not None:
        census_run, excluded = left_out
        if excluded:
            out.print(f"  file list {names.run(census_run)} left out, on purpose:")
            for line in _exclusion_lines(names.exclusions(excluded)):
                out.print(f"  {line}")
        else:
            out.print(f"  file list {names.run(census_run)} left nothing out")

    # A former default written as if chosen: earlier versions of ``init`` wrote every default, and a
    # written value is never overruled. So a default that a later engine changed never reaches an
    # older project. This is said and never changed, because somebody may have meant it.
    from ingence.core.project import read_settings_file
    from ingence.core.settings import SETTINGS_FILE, written_former_defaults

    for dotted, value, now in written_former_defaults(
        read_settings_file(project.path / SETTINGS_FILE)
    ):
        out.print(
            f"  [yellow]settings.yaml holds {dotted}: {value}[/yellow]. An older ingence init "
            f"wrote this default, and the default is {now}. If nobody chose it, delete the "
            "line."
        )

    # An older project's rules file is no longer read, because its rules are the engine's own. This
    # is said, and the file is moved into rulebook/retired/ only when asked and never deleted. The
    # choices a person made in it are written as the project's own rules.
    from ingence.rulebook.retired import read_retired, sentence, set_aside

    found = read_retired(project.path / RULEBOOK_DIR)
    if found is not None and set_aside_old_rules:
        try:
            moved = set_aside(project.path)
        except OSError as exc:
            said = escape(names.message(str(exc)))
            out.print(f"  [red]the older rules file was not moved:[/red] {said}")
            raise typer.Exit(code=1) from exc
        if moved is not None:
            where, written = moved
            out.print(
                f"  moved {RULEBOOK_DIR}/{found.path.name} to "
                f"{escape(str(where.relative_to(project.path)))}"
                + (
                    f"; your choices in it are now {RULEBOOK_DIR}/project-{written:04d}.yaml"
                    if written is not None
                    else ""
                ),
                soft_wrap=True,
            )
    elif found is not None:
        out.print(
            f"  [yellow]{escape(sentence(found, typed=names.typed()))}[/yellow]. To move it aside: "
            f"{_command('fix --set-aside-old-rules', names.project(project.path))}",
            highlight=False,
            soft_wrap=True,
        )
    elif set_aside_old_rules:
        out.print("  no older rules file to move")

    abandoned = sorted((project.path / ".tmp").glob("*.partial"))
    for path in abandoned:
        staged = names.run(path.name.removesuffix(".partial"))
        out.print(
            f"  [yellow]staging folder left by a stopped scan:[/yellow] {staged}.partial "
            f"(safe to delete)"
        )
    # Counted, never named (a name holds a run's): Explore removes only the staging it marked.
    from ingence.api.v1.explorer.projection import is_marked_staging

    unmarked = [
        path
        for path in sorted((project.path / ".tmp").glob("explorer-*"))
        if path.is_dir() and not path.is_symlink() and not is_marked_staging(project.path, path)
    ]
    if unmarked:
        out.print(
            f"  [yellow]{len(unmarked)} Explore staging folder"
            f"{'' if len(unmarked) == 1 else 's'} in .tmp with no build's mark[/yellow] (never "
            "removed automatically; one an older engine left is safe to delete while no Ingence "
            "command or Dashboard is running)"
        )

    runs = sorted(p.name for p in (project.path / "runs").iterdir() if p.is_dir())
    if verify:
        if len(runs) < 2:
            out.print("  [yellow]fewer than 2 runs; nothing to compare[/yellow]")
        else:
            versions = []
            for run_name in (runs[-2], runs[-1]):
                with open_catalog(project.path, run_id=run_name) as catalog:
                    row = catalog.query("SELECT schema_version FROM run").fetchone()
                versions.append(row[0] if row else "unknown")
            if versions[0] != versions[1]:
                out.print(
                    f"  across data formats {versions[0]} and {versions[1]}: the version stamps "
                    f"are compared as equal, and every other value as it is"
                )
            earlier, later = names.run(runs[-2]), names.run(runs[-1])
            differences = compare_runs(project.path, runs[-2], runs[-1])
            if differences:
                out.print(f"  [red]runs {earlier} and {later} differ[/red]")
                detail = describe_differences(project.path, runs[-2], runs[-1])
                for table, number in sorted(differences.items()):
                    out.print(f"    {table}: {number} differing rows")
                    # The sample rows quote values from the data; a shareable report says how
                    # many it leaves out.
                    shown = detail.get(table, [])[:5]
                    if names.shareable:
                        if shown:
                            out.print(
                                f"      {_plural(len(shown), 'sample row')} left out of a "
                                f"shareable report"
                            )
                        continue
                    for line in shown:
                        out.print(f"      {line}")
                problems += 1
            else:
                out.print(f"  runs {earlier} and {later} give the same results")

    if problems:
        raise typer.Exit(code=1)


rule_app = typer.Typer(
    help="Show the rules.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
)
app.add_typer(rule_app, name="rule")


@rule_app.command("show")
def rule_show(
    revision: Annotated[
        int | None,
        typer.Option(
            "--revision", "-r", help="Show the rules at this revision instead of the latest."
        ),
    ] = None,
    project_path: Annotated[
        Path | None,
        typer.Option("--project", "-p", help="Include the changes your project made to the rules."),
    ] = None,
) -> None:
    """Print the rules in force, in plain English.

    This command changes nothing. Each rule reads with its current values, so each sentence says
    what Ingence will do. Ingence also prints the digest that identifies this set of rules. To
    change a rule, use Advanced > Rules in the Dashboard.

    With ``--project``, Ingence adds the changes your project made to the rules. It also prints what
    people answered about each rule's results. These are counts of confirmed, rejected and annotated
    results, with a 95% range for the confirmed share.
    """
    from ingence.rulebook.load import load_project_revisions, load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.render import render_rulebook
    from ingence.rulebook.resolve import effective_rulebook
    from ingence.storage.layout import RULEBOOK_DIR

    load_builtins()
    project = None if project_path is None else _open_or_exit(project_path)
    try:
        own = () if project is None else load_project_revisions(project.path / RULEBOOK_DIR)
        book = effective_rulebook(load_revisions(), at=revision, project=own)
    except RuleDeclarationError as exc:
        console.print(f"[red]The rules do not load:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(render_rulebook(book, revision_asked=revision is not None))
    if project is not None:
        _print_precision(project, {rule.rule_id: rule.version for rule in book.rules})


def _print_precision(project: Project, versions: dict[str, str]) -> None:
    """Print what the project's Record says about each rule's output, with the method."""
    from ingence.record.log import read_events
    from ingence.record.precision import precision_by_rule

    figures = precision_by_rule(read_events(project.path), versions)
    console.print("")
    console.print("What people answered about each rule's results, from this project's answer log:")
    if not figures:
        console.print("  nothing yet: no answer is about a rule's results")
        return
    for rule_id, figure in figures.items():
        console.print(f"  {rule_id} v{figure.rule_version}: {figure.sentence()}")
    console.print(
        "  (decisions in force only; confirm, reject and annotate; one version; annotations "
        "counted and left out of the share; the range is a 95% interval for the confirmed share)"
    )


record_app = typer.Typer(
    help="Add answers to the answer log.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
)
app.add_typer(record_app, name="record")


@record_app.command("import")
def record_import(
    sheet: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="The sheet, as CSV."),
    ],
    actor: Annotated[
        str | None,
        typer.Option(
            "--actor",
            help="Your name, as the author unless --written-by or --agent-generated says "
            "otherwise. Without it, Ingence uses the name saved on this computer. If there is "
            "none, you must give it.",
        ),
    ] = None,
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    rule: Annotated[
        str | None,
        typer.Option(
            "--rule", help="The rule a class key or a term belongs to, where more than one could."
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="Check every row, blank rows included, and write nothing."),
    ] = False,
    written_by: Annotated[
        str | None,
        typer.Option(
            "--written-by",
            help="Who wrote the sheet, where not you: each answer is theirs, imported by you.",
        ),
    ] = None,
    agent_generated: Annotated[
        bool,
        typer.Option(
            "--agent-generated",
            help="An agent wrote the sheet: once you confirm you reviewed it, each answer is "
            "recorded as `<your name>_agent`.",
        ),
    ] = False,
    reviewed: Annotated[
        bool,
        typer.Option("--reviewed", help="Confirm, without being asked, that you reviewed it."),
    ] = False,
) -> None:
    """Add the answers in a review sheet to the answer log.

    Ingence checks each row against the current scan. It refuses a row whose target the scan does
    not hold, and rows that disagree about one target. It counts each refusal by reason. A blank row
    changes nothing, and Ingence never writes the same answer twice. So you can import a sheet again
    each time you add to it.

    You say who wrote the sheet: you (the default), another person (--written-by), or an agent
    (--agent-generated). Ingence saves an agent's answers as `<your name>_agent`. It does so only
    after you confirm that you reviewed every answer. Sheets about file extensions and about folders
    import too.
    """
    from ingence.api.decider import machine_decider
    from ingence.record import log as record_log
    from ingence.record.imported import ImportRefusedError, agent_author, stated
    from ingence.record.sheets import SheetRefusedError, import_sheet

    # The name set on this machine, where there is one: typed once, in the Dashboard, and never
    # guessed. It is said aloud, so nobody's name is used without it showing.
    if not check and not (actor or "").strip():
        actor = machine_decider()
        if actor:
            console.print(
                f"Saving answers as {actor}, the name saved on this computer. "
                "Use --actor to change it."
            )
    if not check and not (actor or "").strip():
        console.print(
            "[red]Each answer needs a name.[/red] Give yours with --actor, or use --check to test "
            "the sheet without writing. No name is saved on this computer yet; the Dashboard asks "
            "for it once."
        )
        raise typer.Exit(code=2)
    author, imported = (actor or "").strip(), None
    statement = None
    if not check:
        if agent_generated and not reviewed and not written_by:
            reviewed = typer.confirm(
                f"Did you review every answer in {sheet.name}? Ingence saves them as "
                f"{agent_author(author)}'s answers.",
                default=False,
            )
            if not reviewed:
                console.print("Nothing was written.")
                raise typer.Exit(code=1)
        try:
            statement = stated(
                author, written_by=written_by, agent_generated=agent_generated, reviewed=reviewed
            )
        except ImportRefusedError as exc:
            console.print(f"[red]Nothing was written:[/red] {exc}.")
            raise typer.Exit(code=2) from exc
        author, imported = statement.author, statement.source()
        if author != statement.importer:
            console.print(f"Answers written by {author}, imported by {statement.importer}.")
    opened = _open_or_exit(project_path)
    if sheet.suffix.lower() == ".json":
        _import_record_events(opened.path, sheet, statement=statement, check=check)
        return
    with open_catalog(opened.path) as catalog:
        try:
            report = import_sheet(
                catalog,
                opened.path,
                sheet,
                actor=author,
                occurred_at=record_log.now(),
                rule_id=rule,
                check=check,
                imported=imported,
            )
        except SheetRefusedError as exc:
            console.print(f"[red]The sheet is refused:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    decided = "would be written" if check else "written"
    console.print(
        f"{report.sheet}: a {report.kind} sheet, {report.rows} rows, "
        f"against {report.against_run_id}" + (" (checked, nothing written)" if check else "")
    )
    console.print(f"  {report.in_run} of {report.rows} rows name a target the current run holds")
    refused = sum(report.refused.values())
    console.print(
        f"  {decided} {report.written}, already recorded {report.already}, "
        f"left blank {report.blank}, refused {refused}"
    )
    if report.withheld:
        console.print(
            f"  [yellow]withheld {report.withheld}: the sheet says two things about a term, "
            "so none of it was written[/yellow]"
        )
    if report.noted:
        kept = "would be kept" if check else "kept"
        console.print(f"  notes {kept} apart from any verdict: {report.noted}")
    for reason, count in sorted(report.refused.items(), key=lambda item: (-item[1], item[0])):
        example = report.examples.get(reason)
        console.print(f"    {count} refused: {reason}" + (f" ({example})" if example else ""))


def _import_record_events(
    project: Path, sheet: Path, *, statement: ImportStatement | None, check: bool
) -> None:
    """Write a file of Record events (``teach export-answers --as-record``) as imported events.

    Each answer is written as the statement's author (the person importing, or the person they
    say wrote the file), saying it is imported from the project it was given in, against the
    project's current run. An answer whose target already holds a decision keeps it. With --check
    nothing is written and the counts are said.
    """
    from ingence.record import log as record_log
    from ingence.record.imported import ImportRefusedError, apply_carried, read_record_file

    try:
        words = read_record_file(sheet.read_text(encoding="utf-8"))
    except (ImportRefusedError, OSError, UnicodeDecodeError) as exc:
        console.print(f"[red]The file is refused:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if check:
        held = record_log.latest_by_target(record_log.read_events(project))
        kept = sum(1 for answer in words.answers if answer.target in held)
        console.print(
            f"{sheet.name}: a file of answer log events, {len(words)} answers (checked, nothing "
            f"written)\n  would be written {len(words) - kept}, already answered {kept}"
        )
        return
    assert statement is not None  # a write states who is importing (record_import)
    try:
        report = apply_carried(
            project,
            words,
            statement=statement,
            imported_from_name=sheet.name,
            occurred_at=record_log.now(),
        )
    except ImportRefusedError as exc:
        console.print(f"[red]Nothing was written:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"{sheet.name}: a file of answer log events, {len(words)} answers\n"
        f"  written {len(report.written)}, already answered {len(report.kept)}, "
        f"refused {len(report.refused)}"
    )
    for refused in report.refused:
        console.print(f"    refused {refused['target']}: {refused['error']}")


teach_app = typer.Typer(
    help="Show and change the Dictionary.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
)
app.add_typer(teach_app, name="teach")


@teach_app.command("export-answers")
def teach_export_answers(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the file here instead of in the terminal."),
    ] = None,
    section: Annotated[
        str, typer.Option("--section", help="The name the exported section carries.")
    ] = "taught",
    author: Annotated[
        str, typer.Option("--author", help="Who the pack names as its author (else who answered).")
    ] = "",
    licence: Annotated[
        str, typer.Option("--license", "--licence", help="The license the pack is shared under.")
    ] = "not stated",
    as_record: Annotated[
        bool,
        typer.Option(
            "--as-record",
            help="Write the words you taught as answer log events instead. These hold what words "
            "mean, how spellings read, unrelated spellings, and meanings turned off. On another "
            "computer, `ingence record import` or **Import** in the Dashboard reads the file.",
        ),
    ] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite", help=_OVERWRITE_HELP)] = False,
) -> None:
    """Write the answers in the project's answer log as a pack file.

    The file holds what each word means, with who answered and when. It uses the format of the
    shipped word files. So the words a person taught stay usable after the project is archived. A
    pack adds words only. Ingence leaves out answers that apply to one folder only, and counts them.

    With --as-record, Ingence writes answer log events instead. These also hold how a spelling
    reads, unrelated spellings, and meanings turned off. `ingence record import FILE --project
    OTHER` adds them to another project's answer log. --section, --author and --license do not
    apply.
    """
    from ingence.dictionary.answers import pack_section_from_answers

    opened = _open_or_exit(project_path)
    if out is not None:
        out = typed_path(out)
        _refuse_output_file(out, opened, "teach export-answers")
        _refuse_existing([out], opened, "teach export-answers", overwrite=overwrite)
    if as_record:
        from ingence.api.recent import name_of
        from ingence.record.imported import carried_words, record_file

        called = name_of(opened.path)
        words = carried_words(opened.path, source=called)
        text = json.dumps(record_file(words, source=called), indent=1, sort_keys=True) + "\n"
        if out is None:
            typer.echo(text, nl=False)
        else:
            try:
                write_replacing(out, text)
            except OSError as exc:
                raise _output_not_written(out, "teach export-answers", exc) from exc
        left = ", ".join(f"{count} {why}" for why, count in sorted(words.left_out.items()))
        typer.echo(
            f"{len(words)} answers exported as answer log events"
            + (f"; left out: {left}" if left else ""),
            err=True,
        )
        return
    exported = pack_section_from_answers(
        opened.path, section=section, author=author, licence=licence
    )
    if out is None:
        typer.echo(exported.text, nl=False)
    else:
        try:
            write_replacing(out, exported.text)
        except OSError as exc:
            raise _output_not_written(out, "teach export-answers", exc) from exc
    left = ", ".join(f"{count} {why}" for why, count in sorted(exported.left_out.items()))
    typer.echo(
        f"{exported.exported} meanings exported" + (f"; left out: {left}" if left else ""),
        err=True,
    )


pack_app = typer.Typer(
    help="Install, list and remove packs: files of words that a laboratory shares.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
)
teach_app.add_typer(pack_app, name="pack")


@pack_app.command("install")
def teach_pack_install(
    source: Annotated[
        str,
        typer.Argument(help="A pack file, a folder holding pack.yaml, or a web address."),
    ],
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Seconds to wait for a web address.")
    ] = 30.0,
    largest: Annotated[
        int, typer.Option("--largest", help="The most bytes fetched from a web address.")
    ] = 4 * 1024 * 1024,
) -> None:
    """Install a pack into the project. Ingence reads it from the next scan on.

    A pack can come from a file, a folder or a web address. Ingence goes online only when you ask:
    it downloads an address now, because you ran this command. It installs the pack only if its
    manifest names a format this version reads, and its digest matches its words.

    A pack adds words only. A laboratory's pack ranks above the core words if the project has
    accepted a shipped pack. Then it wins any spelling that both define. Otherwise it ranks level
    with the core words, and you settle each spelling that both define. This command lists those
    ties. `teach collisions --decided` lists the rest.
    """
    from ingence.dictionary.laboratory_packs import (
        PackError,
        fetch_pack,
        install_pack_from,
        install_pack_text,
        is_address,
    )
    from ingence.dictionary.store import DictionaryStore

    opened = _open_or_exit(project_path)
    try:
        if is_address(source):
            shown = source.rstrip("/").rsplit("/", 1)[-1] or source
            console.print(
                f"Fetching {shown} from {source} "
                f"(at most {largest / 1024 / 1024:g} MB, waiting {timeout:g} s)"
            )
            filename, text, final = fetch_pack(source, timeout=timeout, largest=largest)
            if final != source.strip():
                console.print(f"  fetched from {final}")
            pack = install_pack_text(opened.path, filename, text, fetched=True)
        else:
            pack = install_pack_from(opened.path, source)
    except PackError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"Installed [bold]{pack.name}[/bold] {pack.version}  ({pack.digest[:12]})")
    contested = DictionaryStore.for_project(opened.path).collisions()
    ties = [
        item
        for item in contested
        if item.reported
        and any(sense.origin.value == "pack" for sense in (item.winner, *item.tied))
    ]
    if ties:
        console.print(
            f"  [yellow]The pack and the Dictionary now both define {len(ties)} "
            f"{'spelling' if len(ties) == 1 else 'spellings'}. "
            f"'ingence teach collisions -p' lists them; answer each one to settle it.[/yellow]"
        )
    console.print("  it takes effect at the next scan")


@pack_app.command("list")
def teach_pack_list(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
) -> None:
    """List the packs installed in the project, and whether a scan reads each."""
    from ingence.dictionary.laboratory_packs import installed_packs

    opened = _open_or_exit(project_path)
    loaded = opened.settings.dictionary.packs
    found = installed_packs(opened.path)
    if not found:
        console.print("No pack is installed in this project.")
        return
    for pack in found:
        state = "loaded" if pack.name in loaded else "installed, not loaded"
        console.print(f"{pack.name} {pack.version}  {pack.digest[:12]}  {state}  {pack.summary}")


@pack_app.command("remove")
def teach_pack_remove(
    name: Annotated[str, typer.Argument(help="The pack's name.")],
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
) -> None:
    """Stop reading a pack and delete the project's copy. The pack's own file stays as it is."""
    from ingence.dictionary.laboratory_packs import (
        MANIFEST,
        PackError,
        is_pack_name,
        packs_directory,
        remove_pack,
    )

    opened = _open_or_exit(project_path)
    if not is_pack_name(name):
        if name in opened.settings.dictionary.packs:
            said = (
                f"{opened.path / 'settings.yaml'} names {name!r} as a pack to load, and that is "
                "not a pack's name (a name is lower-case letters, digits, '_' and '-'), so "
                "nothing was changed. Edit settings.yaml and take it out of the list under "
                "dictionary: packs."
            )
        else:
            said = (
                f"{name!r} is not a pack's name (a name is lower-case letters, digits, '_' and "
                "'-'), so nothing was changed. `ingence teach pack list` says which are installed."
            )
        typer.echo(f"ingence teach pack remove: {said}", err=True)
        raise typer.Exit(code=1)
    # A pack is in this project where a copy of it is installed or the settings name it to load;
    # only the names the project holds count, so a name that is not one changes nothing at all.
    folder = packs_directory(opened.path)
    copies = (
        {item.name for item in folder.iterdir() if (item / MANIFEST).is_file()}
        if folder.is_dir()
        else set()
    )
    if name not in copies and name not in opened.settings.dictionary.packs:
        typer.echo(
            f"ingence teach pack remove: no pack named {name!r} is installed in this project, "
            "so nothing was changed. `ingence teach pack list` says which are.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        remove_pack(opened.path, name)
    except PackError as exc:
        typer.echo(f"ingence teach pack remove: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    console.print(f"Removed {name}. Ingence stops reading it at the next scan.")


@teach_app.command("vocabulary")
def teach_vocabulary(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Rows to show.")] = 40,
) -> None:
    """Show the Dictionary words found in this project's names, by kind.

    This command changes nothing. To change what a word means, answer its check, or use the
    Dictionary in the Dashboard.
    """
    catalog_rows = _vocabulary_rows(project_path, "recognised", limit)
    if not catalog_rows:
        console.print(
            "[yellow]Nothing recognized. If this project has no scan yet, run `ingence index` "
            "first.[/yellow]"
        )
        raise typer.Exit(code=1)
    table = Table(title="Recognized in this project's names", box=None, pad_edge=False)
    numeric = ("items", "occurrences")
    for column in ("kind", "term", "canonical", "origin", "items", "occurrences"):
        table.add_column(column, justify="right" if column in numeric else "left")
    for row in catalog_rows:
        table.add_row(
            str(row["theme_kind"]),
            str(row["term"]),
            str(row["canonical"]),
            str(row["origin"]),
            str(row["entries"]),
            str(row["occurrences"]),
        )
    console.print(table)
    coverage = _coverage(project_path)
    if coverage:
        console.print(
            f"\n{coverage['fired']} of {coverage['shipped']} Dictionary meanings appear in this "
            f"project's names; {coverage['unused']} do not."
        )
        console.print(
            "[dim]One project uses only part of the Dictionary. Compare this figure across "
            "projects with different data.[/dim]"
        )


@teach_app.command("unknown")
def teach_unknown(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Rows to show.")] = 40,
) -> None:
    """Show the repeated words that match nothing in the Dictionary.

    Ingence works this out from the file list each time, and saves nothing. So nothing builds up
    that nobody reviewed.

    Each word has one of 3 outcomes. "no_evidence": the Dictionary does not know the word.
    "conflicting": the Dictionary has the word more than once. Say that the word is ambiguous, or
    drop the weaker spelling. "unexamined": Ingence has not read the word yet.

    Words are listed by how many files and folders hold them. A word in 40 names says more than a
    word 40 times in one name.
    """
    catalog_rows = _vocabulary_rows(project_path, "unbound", limit)
    if not catalog_rows:
        console.print(
            "[yellow]No unknown words. If this project has no scan yet, run `ingence index` "
            "first.[/yellow]"
        )
        raise typer.Exit(code=1)
    table = Table(title="Not resolved to a Dictionary term", box=None, pad_edge=False)
    numeric = ("items", "occurrences")
    for column in ("outcome", "kind", "word", "items", "occurrences", "as written"):
        table.add_column(column, justify="right" if column in numeric else "left")
    for row in catalog_rows:
        outcome = str(row["outcome"]).removeprefix("examined_")
        table.add_row(
            f"{outcome} ({row['constraint']})" if row["constraint"] else outcome,
            str(row["component_type"]),
            str(row["token"]),
            str(row["entries"]),
            str(row["occurrences"]),
            str(row["example"]),
        )
    console.print(table)


@teach_app.command("collisions")
def teach_collisions(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help="The project folder. Without it, Ingence reads only the shipped Dictionary.",
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Rows to show.")] = 40,
    decided_only: Annotated[
        bool,
        typer.Option("--decided", help="Show only collisions where one meaning wins."),
    ] = False,
) -> None:
    """Show spellings that have more than one meaning, and how Ingence settles each.

    When 2 meanings rank level, Ingence chooses neither. It names both, and a rule gives no result.
    When one ranks higher, it wins, and Ingence says nothing about the other.

    Look at decided collisions first. In each one, Ingence answered a question for you without
    asking it. ``--decided`` shows only those. Use it after you install a pack, to see where the
    pack disagrees with the core words.

    This command changes nothing. It reads the Dictionary only, so it needs no scan. With
    ``--project``, it also reads the answers in that project's answer log.
    """
    from ingence.core.schema import DictionaryOrigin
    from ingence.dictionary.store import DictionaryStore

    if project_path is not None:
        # A project this build cannot read is said in words, as every verb that opens one does.
        _open_or_exit(project_path)
    store = (
        DictionaryStore.builtin()
        if project_path is None
        else DictionaryStore.for_project(project_path)
    )
    found = store.collisions()
    reported = [item for item in found if item.reported]
    # A person who decides a collision is the mechanism working: they were asked and answered. Every
    # shipped section stands level, so a spelling that the universal vocabulary and a domain share
    # is a reported tie here, read by the folder's files or as the universal sense, marked assumed.
    # A collision decided by anything else is one that nobody was ever asked about.
    settled = [
        item for item in found if not item.reported and item.winner.origin is DictionaryOrigin.USER
    ]
    decided = [
        item
        for item in found
        if not item.reported and item.winner.origin is not DictionaryOrigin.USER
    ]
    shown = [*decided, *settled] if decided_only else found
    if not shown:
        console.print(
            "[green]No collision is decided: every contested spelling is reported as a tie.[/green]"
            if decided_only
            else "[yellow]No spelling in this vocabulary is claimed twice.[/yellow]"
        )
        return

    table = Table(title="Spellings with more than one meaning", box=None, pad_edge=False)
    for column in ("surface", "outcome", "claimed by", "declared"):
        table.add_column(column)
    for item in shown[:limit]:
        claimants = ", ".join(
            f"{sense.term} ({sense.theme_kind}, {sense.origin.value})" for sense in item.senses
        )
        if item.reported:
            outcome = "reported"
        elif item.winner.origin is DictionaryOrigin.USER:
            outcome = "settled by a person"
        else:
            outcome = f"[yellow]decided by {item.winner.origin.value}[/yellow]"
        table.add_row(item.surface, outcome, claimants, "yes" if item.declared else "[red]no[/red]")
    console.print(table)
    if len(shown) > limit:
        console.print(f"[dim]{len(shown) - limit} more not shown.[/dim]")
    console.print(
        f"\n{len(found)} contested spellings: {len(reported)} reported as a tie, "
        f"{len(settled)} settled by a person, {len(decided)} decided by neither."
    )
    if decided:
        console.print(
            "[yellow]Ingence decided these collisions by rank, without asking anyone. Answer each "
            "one to record who decided, and where."
            "[/yellow]"
        )


@teach_app.command("shapes")
def teach_shapes(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Rows to show.")] = 40,
    out: Annotated[
        Path | None, typer.Option("--out", help="Also write every shape to this CSV file.")
    ] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help=_OVERWRITE_HELP)] = False,
) -> None:
    """Show the shapes of the names in this project, and how much the Dictionary matches.

    A shape (skeleton) is a name with its content taken out. A known word becomes its kind, and an
    unknown part becomes a placeholder. Separators and order stay. The shapes show how names are
    built, and how much of them the Dictionary covers.

    Shapes hold no gene, protein, clone or project code. So you can keep them safely when the names
    themselves are private.

    Below the shapes, Ingence counts the word parts it understood, did not know, or found contested.
    It also counts the numbers, labels and dates it read. These counts do not say whether each match
    is right.

    Ingence saves none of this in the project, so nothing builds up that nobody reviewed. ``--out``
    writes it where you ask.
    """
    from ingence.scan.reading_figure import reading_figure, reading_said

    project = _open_or_exit(project_path)
    if out is not None:
        out = typed_path(out)
        _refuse_output_file(out, project, "teach shapes")
        _refuse_existing([out], project, "teach shapes", overwrite=overwrite)
    with open_catalog(project.path) as catalog:
        rows = catalog.shape_corpus(limit=limit)
        figure = reading_figure(catalog)
        everything = catalog.shape_corpus(limit=1_000_000) if out else []

    if not rows:
        console.print(
            "[yellow]No shapes recorded. If this project has no scan yet, run `ingence index` "
            "first.[/yellow]"
        )
        raise typer.Exit(code=1)

    table = Table(title="Name shapes in this project", box=None, pad_edge=False)
    for column in ("items", "slots", "skeleton"):
        table.add_column(column, justify="right" if column != "skeleton" else "left")
    for row in rows:
        table.add_row(str(row["entries"]), str(row["slot_count"]), str(row["skeleton"]))
    console.print(table)

    # The reading in counts that say what they count, so the contested and the unknown are never
    # counted together.
    console.print(f"\n{reading_said(figure)}", highlight=False)

    if out is not None:
        import csv

        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            with replacing(out, newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=list(everything[0]) if everything else []
                )
                writer.writeheader()
                writer.writerows(everything)
        except OSError as exc:
            raise _output_not_written(out, "teach shapes", exc) from exc
        console.print(f"[dim]{len(everything):,} shapes written to {out}[/dim]")


def _open_or_exit(project_path: Path | None) -> Any:
    """Open a project or leave, saying which it could not find or which settings file it could
    not read, and what to do about that file."""
    try:
        return _open(project_path)
    except (NotAProjectError, SettingsFileError) as exc:
        # A reader's message can hold brackets, which are the console's own markup.
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc


def _coverage(project_path: Path | None) -> dict[str, int]:
    """Return how much of the Dictionary this pool used, or nothing."""
    try:
        project = _open(project_path)
    except NotAProjectError:
        return {}
    with open_catalog(project.path) as catalog:
        return catalog.vocabulary_coverage()


def _vocabulary_rows(project_path: Path | None, which: str, limit: int) -> list[dict[str, Any]]:
    """Open the project's catalog and run one of the two vocabulary queries."""
    try:
        project = _open(project_path)
    except NotAProjectError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    with open_catalog(project.path) as catalog:
        if which == "recognised":
            return catalog.recognised_vocabulary(limit=limit)
        return catalog.unbound_tokens(limit=limit)


#: What ``export`` can write, by kind: the themes tables, the quality figures and the brief. A kind
#: is a name here and a function that writes it. The verb refuses any other and says which it knows.
EXPORT_KINDS = ("themes", "quality", "brief")

#: The level that a brief or an MCP session is shaped to, and what the levels are.
_LEVEL_HELP = (
    "What leaves the project: counts (numbers only), words (adds the Dictionary's words, your "
    "own topic and goal names as you typed them, and local aliases such as E1), names (adds "
    "experiment and area "
    "names), or paths (adds paths relative to each data folder, and file names). Never file "
    "contents, absolute paths, your notes or deciders' names."
)
#: The one sentence of each help text that says who the view is for.
_BRIEF_HELP_SENTENCE = 'The kind "brief" writes a short private summary for your assistant.'
_MCP_HELP_SENTENCE = "Serve the project to your assistant over MCP on standard input and output."


@app.command("export")
def export(
    kind: Annotated[str, typer.Argument(help=f"What to write: {', '.join(EXPORT_KINDS)}.")],
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            help=_PROJECT_HELP,
        ),
    ] = None,
    run: Annotated[
        str | None, typer.Option("--run", help="A published run; else the current one.")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help=(
                "The folder to write into; else `projections/exports/<kind>/<run>` in the project. "
                "For the kind brief, `-` prints brief.md instead of writing files."
            ),
        ),
    ] = None,
    level: Annotated[str, typer.Option("--level", help=f"For the kind brief: {_LEVEL_HELP}")] = (
        "words"
    ),
    budget: Annotated[
        int,
        typer.Option(
            "--budget",
            help="For the kind brief: the most tokens the brief takes (about four characters "
            "each); the experiments are cut first.",
        ),
    ] = 4000,
    every_run: Annotated[
        bool,
        typer.Option("--all", help="For the kind quality: every published run, not one."),
    ] = False,
    project_name: Annotated[
        str | None,
        typer.Option(
            "--project-name",
            "--label",
            help="For the kind quality: the project's name in the table's first column; "
            "else the neutral word 'project'.",
        ),
    ] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help=_OVERWRITE_HELP)] = False,
) -> None:
    """Write tables for use in other tools.

    `themes` writes each experiment's theme and each theme's years as 2 CSV tables
    (themes_experiments.csv and themes.csv). `quality` writes the quality figures of one scan, or of
    every scan with --all, as quality.csv and quality.json. Each figure comes with its method.
    `quality` also writes quality_runs.csv, with the versions that made each scan. Tables from 2
    projects have the same columns, with the project first, so you can join them. The project is
    named "project" unless --project-name says otherwise.

    The kind "brief" writes a short private summary for your assistant.
    It writes brief.md and brief.json at the level chosen with --level, within the --budget.
    It also writes brief.aliases.json (alias to identifier) beside them. The aliases never go
    inside the brief. Ingence uses no language model, and nothing of the files' contents is
    written.
    """
    if kind not in EXPORT_KINDS:
        typer.echo(
            f"ingence export: no kind {kind!r}; it writes {', '.join(EXPORT_KINDS)}.", err=True
        )
        raise typer.Exit(code=2)
    try:
        project = _open(project_path)
    except NotAProjectError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if kind == "quality":
        _export_quality(
            project,
            run=run,
            out=out,
            every_run=every_run,
            project_name=project_name,
            overwrite=overwrite,
        )
        return
    if kind == "brief":
        _export_brief(project, level=level, budget=budget, out=out, overwrite=overwrite)
        return
    from ingence.core.registry import ComponentNotAvailableError
    from ingence.theme_words import unavailable_in_words
    from ingence.themes import themes_engine

    pointer = read_pointer(project.path)
    run_id = run or (pointer.run_id if pointer is not None else None)
    if run_id is None:
        typer.echo(
            "ingence export: this project has no scan yet. Run `ingence index` first.", err=True
        )
        raise typer.Exit(code=1)
    try:
        engine = themes_engine()
    except ComponentNotAvailableError as exc:
        typer.echo(f"ingence export: {unavailable_in_words(exc)}", err=True)
        raise typer.Exit(code=1) from exc
    # Beside the run's other projections: rebuilt from the run, never a store of its own.
    directory = (
        typed_path(out)
        if out is not None
        else project.path / "projections" / "exports" / kind / run_id
    )
    roots = [Path(root.mount_path) for root in project.identity.roots if root.listing is None]
    try:
        engine.check_destination(directory, project=project.path, roots=roots, overwrite=overwrite)
    except ValueError as exc:
        typer.echo(f"ingence export: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    built = engine.projection(project.path, run_id, project.settings.themes)
    try:
        experiments_file, themes_file = engine.write_tables(built, directory)
    except OSError as exc:
        # A file that is not writable is found before any is written; any other failure names
        # the file it stopped at, and each table is whole or not there.
        raise _output_not_written(Path(exc.filename or directory), "export", exc) from exc
    summary = built["summary"]
    typer.echo(
        f"{summary['experiments']} experiments, {summary['themes']} themes "
        f"({summary['unplaced']} experiments in none) from run {run_id}:\n"
        f"  {experiments_file}\n  {themes_file}"
    )


def _export_quality(
    project: Project,
    *,
    run: str | None,
    out: Path | None,
    every_run: bool,
    project_name: str | None,
    overwrite: bool = False,
) -> None:
    """``export quality``: a run's, or every run's, quality figures as CSV and JSON.

    The files go into the project's exports folder, or into a folder that the person names. That
    folder must lie outside every root and outside the project. The engine refuses any other place
    and writes nothing.
    """
    from ingence.quality.export import (
        NEUTRAL_PROJECT_NAME,
        published_runs,
        quality_rows,
        run_rows,
        table_paths,
        write_tables,
    )

    if every_run and run is not None:
        typer.echo("ingence export: --all and --run name different things; give one.", err=True)
        raise typer.Exit(code=2)
    pointer = read_pointer(project.path)
    published = published_runs(project.path)
    if not published or pointer is None:
        typer.echo(
            "ingence export: this project has no scan yet. Run `ingence index` first.", err=True
        )
        raise typer.Exit(code=1)
    chosen = published if every_run else [run or pointer.run_id]
    missing = [name for name in chosen if name not in published]
    if missing:
        typer.echo(f"ingence export: no published run {missing[0]!r} in this project.", err=True)
        raise typer.Exit(code=2)
    # Neutral by default: the project's folder name is a person's choice of words and can name
    # a laboratory, so a table that may be shared names the project only when asked to.
    name = project_name or NEUTRAL_PROJECT_NAME
    directory = (
        typed_path(out)
        if out is not None
        else project.path
        / "projections"
        / "exports"
        / "quality"
        / ("all" if every_run else chosen[0])
    )
    try:
        check_output_folder(directory, project=project.path, roots=declared_roots(project))
    except DestinationRefusedError as exc:
        typer.echo(f"ingence export: refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _refuse_existing(table_paths(directory), project, "export", overwrite=overwrite)
    rows = quality_rows(project.path, chosen, name=name)
    try:
        figures_file, json_file, runs_file = write_tables(
            directory, rows, run_rows(project.path, chosen, name=name)
        )
    except OSError as exc:
        raise _output_not_written(Path(exc.filename or directory), "export", exc) from exc
    typer.echo(
        f"{len(rows):,} quality figures of {_plural(len(chosen), 'run')}:\n"
        f"  {figures_file}\n  {json_file}\n  {runs_file}"
    )


def _export_brief(
    project: Project, *, level: str, budget: int, out: Path | None, overwrite: bool
) -> None:
    """``export brief``: the project's summary for a program a person runs.

    The files go into the project's exports folder, or into a folder that the person names outside
    every root and outside the project. ``--out -`` prints ``brief.md`` and writes nothing. The
    alias key goes beside the brief and never inside it. With ``-`` there is no beside, so no alias
    key is written.
    """
    from ingence.agent.brief import BRIEF_FILES, build_brief, write_brief
    from ingence.agent.shape import level_named

    try:
        chosen = level_named(level)
    except ValueError as exc:
        typer.echo(f"ingence export: {exc}.", err=True)
        raise typer.Exit(code=2) from exc
    if budget < 100:
        typer.echo("ingence export: --budget is in tokens, at least 100.", err=True)
        raise typer.Exit(code=2)
    pointer = read_pointer(project.path)
    if pointer is None:
        typer.echo(
            "ingence export: this project has no scan yet. Run `ingence index` first.", err=True
        )
        raise typer.Exit(code=1)
    to_stdout = out is not None and str(out) == "-"
    directory = (
        project.path / "projections" / "exports" / "brief" / pointer.run_id
        if out is None or to_stdout
        else typed_path(out)
    )
    if not to_stdout:
        try:
            check_output_folder(directory, project=project.path, roots=declared_roots(project))
        except DestinationRefusedError as exc:
            typer.echo(f"ingence export: refused: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        _refuse_existing(
            [directory / name for name in BRIEF_FILES], project, "export", overwrite=overwrite
        )
    brief = build_brief(project, level=chosen, budget_tokens=budget)
    if brief.replaced:
        typer.echo(backstop_notice(brief.replaced), err=True)
    if to_stdout:
        typer.echo(brief.markdown, nl=False)
        return
    try:
        written = write_brief(brief, directory)
    except OSError as exc:
        raise _output_not_written(Path(exc.filename or directory), "export", exc) from exc
    tokens = len(brief.markdown) // 4
    typer.echo(
        f"brief at level {chosen}: about {tokens:,} tokens of a budget of {budget:,}, "
        f"{len(brief.aliases)} aliases:\n  " + "\n  ".join(str(path) for path in written)
    )


@app.command("mcp")
def mcp(
    project_path: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-p",
            help=_PROJECT_HELP,
        ),
    ] = None,
    level: Annotated[str, typer.Option("--level", help=_LEVEL_HELP)] = "words",
) -> None:
    """Serve the project to your assistant over MCP on standard input and output.

    The tools are read-only. Each answers from the project's last run and is shaped to the level
    chosen with --level. The tools are project_overview, list_experiments, get_experiment, ask,
    completeness, themes, topics_and_goals, assumptions, open_questions, and experiment_files at
    the level paths only. No tool writes, no file's contents are sent, and Ingence uses no
    language model. The command runs until its input closes. A program should start it, and a
    person should not run it in a terminal.
    """
    from ingence.agent.mcp import serve
    from ingence.agent.shape import level_named

    try:
        chosen = level_named(level)
    except ValueError as exc:
        typer.echo(f"ingence mcp: {exc}.", err=True)
        raise typer.Exit(code=2) from exc
    try:
        project = _open(project_path)
    except (NotAProjectError, SettingsFileError) as exc:
        # Standard output is the protocol's: a refusal goes to standard error.
        typer.echo(f"ingence mcp: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if read_pointer(project.path) is None:
        typer.echo(
            "ingence mcp: this project has no scan yet. Run `ingence index` first.", err=True
        )
        raise typer.Exit(code=1)
    raise typer.Exit(code=serve(project, chosen))


def _register_deferred(verb: str, help_text: str) -> None:
    """Attach one placeholder command for a verb that has not landed yet."""

    def command() -> None:
        typer.echo(
            f"ingence {verb}: not available in this version.",
            err=True,
        )
        raise typer.Exit(code=2)

    said = f"{help_text[:1].upper()}{help_text[1:]} (not available in this version)."
    command.__doc__ = said
    app.command(name=verb, help=said)(command)


for _verb, (_help_text, _) in DEFERRED_VERBS.items():
    _register_deferred(_verb, _help_text)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    *,
    version: bool = typer.Option(
        False, "--version", help="Show the package and schema versions, and the build's commit."
    ),
) -> None:
    """Hold the global options. Running ``ingence`` alone opens the Dashboard."""
    if version:
        from ingence.core.provenance import engine_commit

        commit = engine_commit()
        build = f", build {commit[:12]}" if commit else ""
        typer.echo(f"ingence {__version__} (schema {SCHEMA_VERSION}{build})")
        raise typer.Exit(code=0)
    if ctx.invoked_subcommand is None:
        # No verb: the Dashboard, as ``ingence see`` with its defaults: the project found from the
        # current folder, a free port, the browser opened. ``ingence --help`` still prints the help.
        see()


def main() -> None:
    """Console script entry point declared in ``pyproject.toml``."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
