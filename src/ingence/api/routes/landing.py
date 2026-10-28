"""The landing page's reads and writes, before a project exists or before its first run.

``ingence see`` lands here when no project is open or the project has no run yet. An open
Dashboard reaches it at ``/landing`` ("Open another project"). The module provides these:

- The state (:func:`landing_state`): the project this server reads, if any, and whether it has a
  run. It also gives the projects opened on this machine (``api/recent.py``), the years a name's
  dates fall in by default, and where a new project is proposed.
- A folder checked (:func:`check_folder`): whether the folder is there and is a folder, its
  top-level folders by name, and a count of its direct children. It never counts everything
  beneath, which is the census's work, and the page says so. A folder that is an Ingence
  project, or lies inside one, is refused as a root in words.
- A project made (:func:`create_project`): the folder to read, a name (with none, the project's
  own folder names it), where the project is written (``~/Ingence Projects/<name>`` is proposed
  and editable, and any place is allowed), the years, and whether the first reading opens files.
  With ``read`` and ``who`` in the same call ("Read it now"), making the project and starting its
  census is one act. Without ``read``, nothing is read until the first-run route is asked.
  Opening files is a parameter of that request only. It decides whether the job includes a dive,
  and the project's settings keep nothing about it.
- A project renamed (:func:`rename_project`): its name is written to its own ``project.json``, so
  any computer that opens it shows the name and not the folder's. The name is also given when the
  project is made.
- The settings asked before a first run (:func:`save_first_run_settings`): the years, written to
  the project's settings as ``init`` writes what a person told it.
- A first run's progress (:func:`first_run_progress`): the job ledger's status, with the counts
  that the census, the dive or inference has written to its snapshot so far. These are the
  census's folders and files found and the bytes they hold, the dive's files opened, and
  inference's rules gone through of the rules it will go through. The page shows counts, elapsed
  time and the heartbeat, and never a percentage, because nothing knows the total until the
  census ends. Inference knows its total, the rules, from the start, so it is the one step that
  says "of".

Nothing here writes to a root, and nothing here starts work. The server's route starts the first
run on a person's click (``DefinitionApplier.submit_first_run``).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import write_atomically

if TYPE_CHECKING:
    from ingence.api.apply import DefinitionApplier

__all__ = [
    "CARRY_FILE",
    "DEFAULT_YEARS",
    "DEPTH_WORDS",
    "FOLDERS_SHOWN",
    "PROJECTS_PARENT",
    "LandingRefusedError",
    "carry_choices",
    "carry_state",
    "carry_words",
    "check_folder",
    "check_project_folder",
    "create_project",
    "depth_from",
    "dive_asked",
    "dive_from",
    "existing_project",
    "first_run_progress",
    "landing_state",
    "proposed_project_folder",
    "rename_project",
    "save_depth",
    "save_first_run_settings",
    "time_limit_from",
    "years_from",
]

#: Where a new project is proposed, under the person's home folder: a folder of its own on the
#: person's own disk, which is the best place for it.
PROJECTS_PARENT = "Ingence Projects"

#: How many top-level folders a check names. The rest are counted.
FOLDERS_SHOWN = 40

#: The earliest and latest year a person may name as a date in a name (a typing guard only).
_YEAR_FLOOR, _YEAR_CEILING = 1900, 2200

#: The years the landing page proposes as dates: one default with the engine's own
#: (``dictionary.years``, 2013 to the present). The present year extends as the years pass,
#: where a number fixed on the day the project was made would not.
DEFAULT_YEARS: tuple[int, str] = (2013, "present")


class LandingRefusedError(ValueError):
    """What a person asked of the landing page was not done, and the message says why."""


def _expanded(text: str) -> Path:
    return Path(os.path.expanduser(text.strip())).resolve()


def _project_holding(path: Path) -> Path | None:
    """The Ingence project ``path`` is, or lies inside, by the layout a census recognizes one by."""
    from ingence.core.project import _is_a_project

    for candidate in (path, *path.parents):
        if _is_a_project(candidate):
            return candidate
    return None


def check_folder(text: str | None) -> dict[str, Any]:
    """Say whether a typed path is a folder a project can read, and what is directly in it.

    ``refusal`` is the sentence the page shows where it cannot be a root, else None. Only the
    folder's own listing is read (``os.scandir`` of that one folder); nothing beneath is opened
    or counted.
    """
    typed = (text or "").strip()
    said: dict[str, Any] = {"typed": typed, "refusal": None}
    if not typed:
        return {**said, "refusal": "Type or paste the path of a folder."}
    path = _expanded(typed)
    said["path"] = str(path)
    if not path.exists():
        return {
            **said,
            "exists": False,
            "refusal": "Not readable: nothing is at this path on this computer.",
        }
    if not path.is_dir():
        return {
            **said,
            "exists": True,
            "is_folder": False,
            "refusal": "Not readable: this is a file. Choose a data folder.",
        }
    said.update(exists=True, is_folder=True)
    holder = _project_holding(path)
    if holder is not None:
        sentence = (
            "This folder is an Ingence project. Choose the data folder it scans."
            if holder == path
            else f"This folder is inside the Ingence project at {holder}. Choose a data folder "
            "outside it."
        )
        said["refusal"] = sentence
    folders: list[str] = []
    files = links = others = hidden = 0
    try:
        with os.scandir(path) as listing:
            for entry in listing:
                if entry.name.startswith("."):
                    hidden += 1
                    continue
                if entry.is_symlink():
                    links += 1
                elif entry.is_dir(follow_symlinks=False):
                    folders.append(entry.name)
                elif entry.is_file(follow_symlinks=False):
                    files += 1
                else:
                    others += 1
    except OSError as error:
        why = str(error.strerror or error)
        return {
            **said,
            "refusal": said["refusal"] or f"Not readable: {why[:1].lower()}{why[1:]} on {path}.",
        }
    folders.sort(key=str.casefold)
    return {
        **said,
        "folders": folders[:FOLDERS_SHOWN],
        "folders_count": len(folders),
        "files_count": files,
        "links_count": links,
        "other_count": others,
        "hidden_count": hidden,
    }


def proposed_project_folder(name: str | None, *, home: Path | None = None) -> str:
    """Where a project named ``name`` is proposed: ``~/Ingence Projects/<name>``."""
    base = (home if home is not None else Path.home()) / PROJECTS_PARENT
    cleaned = _folder_name(name or "") or "My project"
    return str(base / cleaned)


def _folder_name(name: str) -> str:
    """A name a folder can carry on every system: what a person typed, with the separators and
    the characters Windows refuses taken out, and no leading dot."""
    kept = "".join(" " if character in '/\\:*?"<>|' else character for character in name)
    return " ".join(kept.split()).lstrip(".").strip()


#: What the place says when a project already written there reads the folder being asked about, and
#: when it reads another (the page offers "Open it instead" only for the first).
ALREADY_HERE = "A project is already saved here."
ANOTHER_HERE = "A project for another data folder is already saved here. Choose another place."


def existing_project(text: str | None) -> str | None:
    """The path of the project already written at ``text``, or None. Only the project's own
    layout is looked at."""
    typed = (text or "").strip()
    if not typed:
        return None
    path = _expanded(typed)
    return str(path) if _project_holding(path) == path else None


def project_reads_root(project: str, root: str) -> bool:
    """Whether the project at ``project`` reads the folder ``root``, among the folders it declares.

    A project that cannot be opened reads nothing as far as this can tell: it is not offered to be
    opened in place of a new one.
    """
    from ingence.core.project import Project

    try:
        declared = Project.open(Path(project)).identity.roots
    except Exception:  # a project that cannot be read is not the one being asked for
        return False
    wanted = str(_expanded(root))
    return any(str(Path(item.mount_path).resolve()) == wanted for item in declared)


def check_project_folder(text: str | None, root: str | None = None) -> str | None:
    """Say why a project cannot be written at ``text``, or None where it can.

    The function refuses these: no path; a folder that is already a project (the sentence says
    whether it reads ``root``, where that is given); a folder inside another project (its runs
    would read it as its own); and a folder that holds names a project writes
    (``Project.create`` says which). A project may be written anywhere else, inside the root it
    reads included.
    """
    typed = (text or "").strip()
    if not typed:
        return "Choose where to save the project."
    path = _expanded(typed)
    holder = _project_holding(path)
    if holder is not None:
        if holder != path:
            return (
                f"{path} is inside the Ingence project at {holder}. "
                "Save the new project outside it."
            )
        if root is not None and not project_reads_root(str(path), root):
            return ANOTHER_HERE
        return ALREADY_HERE
    if path.exists() and not path.is_dir():
        return f"{path} is a file. A project is a folder."
    return None


def free_project_folder(proposed: str, root: str | None = None) -> str:
    """``proposed``, or the first of ``<proposed> 2``, ``<proposed> 3`` ... that does not hold a
    project reading another folder (two folders of one name, read by two projects).

    A place holding a project that reads ``root`` is kept: the page offers to open it.
    """
    for number in range(1, 100):
        candidate = proposed if number == 1 else f"{proposed} {number}"
        if check_project_folder(candidate, root) != ANOTHER_HERE:
            return candidate
    return proposed


def years_from(value: Any) -> tuple[int, int | str]:
    """The years a name's dates fall in, from ``[first, last]``, ``last`` a year or ``present``."""
    if not isinstance(value, list | tuple) or len(value) != 2:
        message = "the years are a first year and a last year (or 'present')"
        raise LandingRefusedError(message)
    first_said, last_said = value
    try:
        first = int(first_said)
    except (TypeError, ValueError) as error:
        message = f"the first year is a year, and {first_said!r} was given"
        raise LandingRefusedError(message) from error
    last: int | str
    if str(last_said).strip().casefold() in ("present", "this year", ""):
        last = "present"
    else:
        try:
            last = int(last_said)
        except (TypeError, ValueError) as error:
            message = f"the last year is a year or 'present', and {last_said!r} was given"
            raise LandingRefusedError(message) from error
    for year in (first, last):
        if isinstance(year, int) and not _YEAR_FLOOR <= year <= _YEAR_CEILING:
            message = f"Use a year from {_YEAR_FLOOR} to {_YEAR_CEILING}"
            raise LandingRefusedError(message)
    if isinstance(last, int) and first > last:
        message = f"the first year ({first}) is after the last ({last})"
        raise LandingRefusedError(message)
    return first, last


#: The depth a first run reads at: the page's three words and the profile each names
#: (``scan/profiles.py``).
DEPTH_WORDS: dict[str, str] = {"quick": "census", "standard": "standard", "deep": "deep"}


def depth_from(value: Any) -> str | None:
    """The depth profile a request names (``quick``, ``standard`` or ``deep``), or None where it
    names none; refused in words otherwise."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().casefold() in DEPTH_WORDS:
        return DEPTH_WORDS[value.strip().casefold()]
    message = f"the depth is one of {', '.join(DEPTH_WORDS)}"
    raise LandingRefusedError(message)


def time_limit_from(value: Any) -> float | None:
    """A deep reading's time limit in seconds, or None where none; refused in words otherwise."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        message = "the time limit is a number of seconds above zero"
        raise LandingRefusedError(message)
    return float(value)


def dive_asked(body: dict[str, Any]) -> bool:
    """Whether a first run opens files: ``dive`` where the request says. Otherwise it is on at
    any depth but ``quick``. An absent ``dive`` means on at standard depth."""
    if body.get("dive") is not None:
        return dive_from(body.get("dive"))
    return depth_from(body.get("depth")) != "census"


def save_depth(project_path: Path, body: dict[str, Any]) -> None:
    """Write the depth a request names, and a deep reading's time limit, to the project's
    settings where they differ from what the project holds."""
    from ingence.core.project import Project

    depth = depth_from(body.get("depth"))
    limit = time_limit_from(body.get("time_limit_seconds"))
    if depth is None and limit is None:
        return
    opened = Project.open(project_path)
    settings = opened.settings
    changed = False
    if depth is not None and settings.depth != depth:
        settings = settings.model_copy(update={"depth": depth})
        changed = True
    if limit is not None and depth == "deep" and settings.extraction.total_seconds != limit:
        extraction = settings.extraction.model_copy(update={"total_seconds": limit})
        settings = settings.model_copy(update={"extraction": extraction})
        changed = True
    if changed:
        opened.save_settings(settings)


def dive_from(value: Any) -> bool:
    """Whether the first reading opens files, from ``true``/``false`` or ``{"enabled": ...}``.

    ``None`` (not said) is on. A first run then reads inside the files before it infers, at the
    standard depth, within its minutes on the wall clock and one file of every series first. A
    page that wants names and dates only says ``false``. The function refuses anything else in
    words and does not read it as truthy: ``"false"`` is not a switch left on.
    """
    if isinstance(value, dict):
        value = value.get("enabled")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    message = "opening files is on or off"
    raise LandingRefusedError(message)


#: Where a new project keeps the words it was asked to bring in until it has a run to write them
#: against: a file of Record events (``record/imported.py``), beside the files an import keeps.
CARRY_FILE = "imports/carried-words.json"


def carry_choices(
    config_home: Path | None = None, *, exclude: Path | None = None
) -> list[dict[str, Any]]:
    """The earlier projects a person can bring their words in from.

    These are the projects that the landing page already lists (``describe_recent``) and that hold
    at least one answer about words in force for the whole project. Each comes with how many such
    answers it holds, and how many were kept below one folder and so cannot travel. The result is
    None where there are none, and the form then offers nothing. The project being shown is never
    offered to itself.
    """
    from ingence.api.recent import describe_recent
    from ingence.record.imported import carried_words

    offered: list[dict[str, Any]] = []
    held = _expanded(str(exclude)) if exclude is not None else None
    for item in describe_recent(config_home):
        path = str(item.get("path") or "")
        if not path or not item.get("exists") or (held is not None and _expanded(path) == held):
            continue
        try:
            words = carried_words(Path(path))
        except Exception:  # noqa: S112 (a project whose Record cannot be read offers nothing)
            continue
        if len(words):
            offered.append(
                {
                    "path": path,
                    "name": item.get("name") or Path(path).name,
                    "words": len(words),
                    "left_out": sum(words.left_out.values()),
                }
            )
    return offered


def _stage_carry(project_path: Path, sources: list[Path], names: dict[str, str]) -> None:
    """Keep the words of the chosen projects, first-named first, for when the project has a run."""
    from ingence.record.imported import CarriedWords, carried_words, record_file

    answers = []
    taken: set[str] = set()
    left: dict[str, int] = {}
    for source in sources:
        found = carried_words(source, source=names[str(source)])
        for name, count in found.left_out.items():
            left[name] = left.get(name, 0) + count
        for answer in found.answers:
            if answer.target not in taken:
                taken.add(answer.target)
                answers.append(answer)
    staged = project_path / CARRY_FILE
    staged.parent.mkdir(parents=True, exist_ok=True)
    document = record_file(
        CarriedWords(answers=tuple(answers), left_out=left),
        source=", ".join(names[str(source)] for source in sources),
    )
    write_atomically(staged, json.dumps(document, indent=1, sort_keys=True) + "\n")


def carry_state(project: Path | None) -> dict[str, Any] | None:
    """What a project was asked to bring in, and whether it has: None where it was asked nothing.

    ``staged`` is how many answers wait to be written (they are written once the project has a
    run), ``left_out`` how many were kept below one folder and stayed behind, and ``applied`` how
    many were written (None until then).
    """
    if project is None:
        return None
    path = project / CARRY_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    done = document.get("applied")
    return {
        "from": str(document.get("from") or ""),
        "staged": len(document.get("events") or []),
        "left_out": sum(int(count) for count in (document.get("left_out") or {}).values()),
        "applied": int(done["written"]) if isinstance(done, dict) else None,
    }


def carry_words(project: Path, body: dict[str, Any], *, occurred_at: str) -> dict[str, Any]:
    """Write the words that a new project was asked to bring in, now that it has a run.

    ``body`` states ``actor``: the person bringing them in, who is the author of every event
    written (the way Import says who wrote a file). The events are imported events
    (``record/imported.py``). Each says it is imported from the project it was given in, and each
    is withdrawn by itself. A target that already holds an answer keeps it. The call is done once:
    a second call writes nothing and says what the first did. It is refused in words where the
    project has no run yet.
    """
    from ingence.record.imported import (
        ImportRefusedError,
        apply_carried,
        read_record_file,
        stated,
    )

    path = project / CARRY_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        message = "this project was asked to bring no words in"
        raise LandingRefusedError(message) from error
    if isinstance(document.get("applied"), dict):
        return {**document["applied"], "again": True}
    try:
        statement = stated(body.get("actor"))
        words = read_record_file(document)
        report = apply_carried(
            project,
            words,
            statement=statement,
            imported_from_name=str(document.get("from") or "an earlier project"),
            occurred_at=occurred_at,
        )
    except ImportRefusedError as error:
        raise LandingRefusedError(str(error)) from error
    done = {
        "written": len(report.written),
        "kept": len(report.kept),
        "refused": report.refused,
        "left_out": sum(words.left_out.values()),
        "author": statement.author,
    }
    document["applied"] = done
    write_atomically(path, json.dumps(document, indent=1, sort_keys=True) + "\n")
    return done


def create_project(
    body: dict[str, Any], *, created_at: str, config_home: Path | None = None
) -> Path:
    """Make a project from the landing page's form, refusing in words what cannot be made.

    ``body`` holds these keys:

    - ``root``: the folder to read.
    - ``name``: the project's name. With none, the project's own folder names it.
    - ``project``: where the project is written. The proposed folder is the default.
    - ``years``: the years for dates. The default runs from 2013 to the present.
    - ``dive``: whether the first reading opens files. It is checked here and kept nowhere. Where it
      is absent, the first run opens files at every depth but ``quick`` (:func:`dive_asked`).
    - ``carry``: earlier projects to bring the words a person taught in from. The default is
      none. Each must be one that :func:`carry_choices` offers. The new project keeps their
      answers until it has a run to write them against (:func:`carry_words`).

    The project is written as ``ingence init <project> --root <root>`` writes it: the years as
    the person said them, whatever their value. The function refuses ``dive`` if it is not on or
    off, and keeps it nowhere, for two reasons. The dive belongs to the request that reads the
    project, and the first run takes it from there. Also, a settings file that holds a key an
    older build does not know is one that build cannot open. The function reads no root beyond its
    own listing.
    """
    from ingence.core.project import Project, ProjectExistsError, project_name
    from ingence.core.settings import DictionarySettings, Settings

    checked = check_folder(str(body.get("root") or ""))
    if checked["refusal"]:
        raise LandingRefusedError(checked["refusal"])
    root = Path(checked["path"])
    typed_name = str(body.get("name") or "").strip()
    # A project given no name is named by its folder, and the folder proposed follows the folder
    # it reads (the page's "pool, unless you give one").
    named = _folder_name(typed_name) if typed_name else ""
    if typed_name and not named:
        message = "Give the project a name."
        raise LandingRefusedError(message)
    try:
        # The name the project carries is what was typed, with its spaces made single; only the
        # folder proposed from it is made safe for every system.
        called = project_name(typed_name) if typed_name else None
    except ValueError as error:
        raise LandingRefusedError(str(error)) from error
    where = str(body.get("project") or "").strip() or proposed_project_folder(named or root.name)
    refusal = check_project_folder(where, str(root))
    if refusal is not None:
        raise LandingRefusedError(refusal)
    project_path = _expanded(where)
    years = years_from(body.get("years") or list(DEFAULT_YEARS))
    dive_from(body.get("dive"))  # refused before anything is written, though kept nowhere
    depth = depth_from(body.get("depth"))
    limit = time_limit_from(body.get("time_limit_seconds"))
    sources, names = _carry_sources(body.get("carry"), config_home)
    # Every shipped domain section loads: the page names no pack.
    settings = Settings(dictionary=DictionarySettings(years=years))
    if depth is not None:
        settings = settings.model_copy(update={"depth": depth})
    if limit is not None and depth == "deep":
        settings = settings.model_copy(
            update={"extraction": settings.extraction.model_copy(update={"total_seconds": limit})}
        )
    try:
        project = Project.create(
            project_path,
            created_at=created_at,
            roots=[root],
            name=called,
            settings=Settings.model_validate(settings.model_dump(mode="json")),
            # What a person told the page is written whatever its value, as init does.
            chosen={"dictionary": {"years": None}},
        )
    except ProjectExistsError as error:
        raise LandingRefusedError(str(error)) from error
    except OSError as error:
        message = f"Ingence could not save the project in {project_path}: {error.strerror or error}"
        raise LandingRefusedError(message) from error
    if sources:
        try:
            _stage_carry(project.path, sources, names)
        except OSError as error:
            message = f"Ingence could not save the words for the project: {error.strerror or error}"
            raise LandingRefusedError(message) from error
    return project.path


def _carry_sources(value: Any, config_home: Path | None) -> tuple[list[Path], dict[str, str]]:
    """The projects asked to bring words in from, each one the page offered; refused in words."""
    if value in (None, False, []):
        return [], {}
    if not isinstance(value, list):
        message = "the projects to bring words in from are a list"
        raise LandingRefusedError(message)
    offered = {str(_expanded(item["path"])): item for item in carry_choices(config_home)}
    sources: list[Path] = []
    names: dict[str, str] = {}
    for typed in value:
        key = str(_expanded(str(typed)))
        if key not in offered:
            message = f"{typed} is not a project you taught words in that this computer lists"
            raise LandingRefusedError(message)
        if key not in names:
            sources.append(Path(key))
            names[key] = str(offered[key]["name"])
    return sources, names


def rename_project(path: Path, name: str) -> Path:
    """Give the project at ``path`` a new name, written to its own ``project.json``.

    Refused in words where the folder is no project, or the name is empty or too long. Only the
    project's name changes: its folder keeps its name, and nothing it reads is touched.
    """
    from ingence.core.project import NotAProjectError, Project

    try:
        opened = Project.open(_expanded(str(path)))
    except NotAProjectError as error:
        message = "This folder is not an Ingence project."
        raise LandingRefusedError(message) from error
    try:
        return opened.rename(name).path
    except ValueError as error:
        raise LandingRefusedError(str(error)) from error
    except OSError as error:
        message = f"Ingence could not save the name: {error.strerror or error}"
        raise LandingRefusedError(message) from error


def save_first_run_settings(project_path: Path, body: dict[str, Any]) -> None:
    """Write the years a person gave before a first run to the project's settings, where they
    differ from what the project holds. Opening files is not a setting: the run reads it from the
    request."""
    from ingence.core.project import Project

    if body.get("years") is None:
        return
    years = years_from(body["years"])
    opened = Project.open(project_path)
    if tuple(opened.settings.dictionary.years) == years:
        return
    dictionary = opened.settings.dictionary.model_copy(update={"years": years})
    opened.save_settings(opened.settings.model_copy(update={"dictionary": dictionary}))


def _project_summary(project: Path, config_home: Path | None) -> dict[str, Any]:
    from ingence.api.recent import describe_project, read_recent
    from ingence.core.project import Project

    spelled = str(project.expanduser().resolve())
    listed = next(
        (item.get("name") for item in read_recent(config_home) if item["path"] == spelled), None
    )
    said = describe_project(project, listed_name=listed)
    if not said.get("exists"):
        return said
    from ingence.scan.profiles import PROFILES, apply_profile, depth_named

    settings = Project.open(project).settings
    first, last = settings.dictionary.years
    # How long a dive at the project's depth may spend opening files, as ``ingence dive`` with
    # no ``--budget`` grants (None: until every file is opened); and whether it opens any.
    depth = depth_named(settings.depth)
    granted = apply_profile(settings, depth).extraction.deepening_budget_seconds
    return {
        **said,
        "years": [first, last],
        "depth": settings.depth,
        "dive_opens_files": PROFILES[depth].opens_files,
        "dive_budget_seconds": granted,
    }


def landing_state(
    project: Path | None, *, config_home: Path | None = None, applier: Any = None
) -> dict[str, Any]:
    """What the landing page opens on: the project read here, the projects opened before, the
    defaults it proposes, and whether a first run is under way."""
    from ingence import __version__
    from ingence.api.recent import describe_recent

    this_year = dt.datetime.now(dt.UTC).year
    state: dict[str, Any] = {
        "version": __version__,
        "project": _project_summary(project, config_home) if project is not None else None,
        "recent": describe_recent(config_home),
        "projects_parent": str(Path.home() / PROJECTS_PARENT),
        "default_years": list(DEFAULT_YEARS),
        "this_year": this_year,
        # The earlier projects whose words can be brought into a new one. With none, the form
        # offers nothing.
        "carry": carry_choices(config_home, exclude=project),
        "carried": carry_state(project),
    }
    if project is not None and applier is not None:
        state["job"] = first_run_progress(project, applier)
    return state


def _snapshot(project: Path, run_id: Any) -> dict[str, Any] | None:
    """What the census or dive has seen so far, from its snapshot file, or None where none is."""
    from ingence.jobs.worker import progress_snapshot_path

    if not isinstance(run_id, str) or not run_id:
        return None
    try:
        held = json.loads(progress_snapshot_path(project, run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(held, dict):
        return None
    wanted = (
        "stage",
        "directories_seen",
        "entries_seen",
        "entries_examined",
        "folders_found",
        "files_found",
        "bytes_seen",
        "errors",
        "rules_run",
        "rules_total",
        "elapsed_seconds",
        "entries_per_second",
        "total_entries",
        "finished",
    )
    return {name: held[name] for name in wanted if name in held}


def _seconds_since(stamp: Any) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        began = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round(max(0.0, (dt.datetime.now(dt.UTC) - began).total_seconds()), 1)


def _counters(said: dict[str, Any]) -> dict[str, Any]:
    """The four figures the page draws while a first reading runs, each the engine's own count.

    Folders found, files found and bytes seen are the census's. They come from its snapshot while
    it runs, and from what the finished census wrote to the job once it has ended. Rules run and
    the rules in all are inference's, once it runs. A count that is not yet known is None, never
    0, and nothing here is a percentage, because the census does not know its total until it
    ends.
    """
    seen = said.get("seen") or {}
    counters: dict[str, Any] = {
        "folders_found": None,
        "files_found": None,
        "bytes_seen": None,
        "rules_run": None,
        "rules_total": None,
    }
    if said.get("phase") == "census":
        for name in ("folders_found", "files_found", "bytes_seen"):
            counters[name] = seen.get(name)
    else:
        counters["folders_found"] = said.get("census_folders")
        counters["files_found"] = said.get("census_files")
        counters["bytes_seen"] = said.get("census_bytes")
    if said.get("phase") == "inferring":
        counters["rules_run"] = seen.get("rules_run")
        counters["rules_total"] = seen.get("rules_total")
    return counters


_ERROR_KIND = re.compile(r"^([A-Z]\w*(?:Error|Exception)): ")


def first_run_progress(project: Path, applier: DefinitionApplier) -> dict[str, Any]:
    """The first run's job as the landing page follows it: the ledger's status, the snapshot's
    counts for the phase in hand, and how long the job has run."""
    status = applier.status()
    said: dict[str, Any] = dict(status)
    error = status.get("error")
    if isinstance(error, str):
        # The worker writes "<the error's kind>: <its words>"; the page says the words and keeps
        # the kind behind a "Why" (an identifier is never the main sentence).
        found = _ERROR_KIND.match(error)
        said["error_words"] = error[found.end() :] if found else error
        said["error_kind"] = found.group(1) if found else None
    # The landing page reads the ledger's phase words (``census``, ``dive``, ``inferring``).
    # ``/api/applying`` sends the contract's phase words in ``phase`` and the ledger's beside
    # them. This page's state keeps the ledger's, which its script reads.
    phase = status.get("phase_legacy", status.get("phase"))
    said["phase"] = phase
    if phase == "census":
        said["seen"] = _snapshot(project, status.get("census_run"))
    elif phase == "dive":
        said["seen"] = _snapshot(project, status.get("dive_run"))
    elif phase == "inferring":
        said["seen"] = _snapshot(project, status.get("inferring_run"))
    said["counters"] = _counters(said)
    job_id = status.get("job_id")
    if job_id:
        for job in applier.jobs(50):
            if job.job_id == job_id:
                said["started_at"] = job.started_at
                said["job_seconds"] = (
                    _seconds_since(job.started_at) if job.ended_at is None else None
                )
                break
    return said


#: The most goals a landing page's form may carry at once.
GOALS_AT_CREATE = 20


def write_goals(project: Path, goals: object) -> list[dict[str, str]] | None:
    """Write the goals that a landing page's form carried into the new project's workspace.

    ``goals`` is a list of sentences, or None where the form carried none. A malformed list is
    refused in words before anything is written."""
    if goals is None:
        return None
    if not isinstance(goals, list) or not all(isinstance(item, str) for item in goals):
        message = "goals is a list of sentences"
        raise LandingRefusedError(message)
    texts = [" ".join(item.split()) for item in goals if item.strip()]
    if len(texts) > GOALS_AT_CREATE:
        message = f"at most {GOALS_AT_CREATE} goals are written when a project is made"
        raise LandingRefusedError(message)
    from ingence.goals import TEXT_MAXIMUM, write_goal

    for text in texts:
        if len(text) > TEXT_MAXIMUM:
            message = f"a goal is at most {TEXT_MAXIMUM} characters"
            raise LandingRefusedError(message)
    return [{"id": goal.id, "text": goal.text} for goal in (write_goal(project, t) for t in texts)]
