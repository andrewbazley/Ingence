"""The readouts: the views a run is read through, built once after inference and kept.

A readout is everything a page shows about a run that is not the run itself. The readouts
are the themes and their map, the search projection, the Explorer projection, the Overview
(the whole payload of ``GET /api/v1/overview``), the assumptions list (``GET /api/v1/assumptions``)
and the whole ranked list of questions (``GET /api/v1/questions/ranked`` before its page is cut,
which ``GET /api/v1/checks`` reads too). Each readout is a function of one published run, the
project's Dictionary and the answer log at the run's watermark. No readout is a conclusion, and
inference reads none of them. Deleting ``projections/`` loses nothing but the seconds it takes to
build them again.

Every job ends with a readouts step (``jobs/worker.py``, the ``views`` phase), and ``ingence infer``
does the same. After inference publishes and before the ledger's ``finish`` moves the pin,
:func:`build_readouts` saves each view under ``projections/readouts/<run_id>/``. Each view is keyed
by the run's own Dictionary fingerprint (:func:`readout_key`), so an answer written later does not
rebuild them. A re-run does rebuild them, and reads the answer then. Pages keep showing the previous
run's readouts until the new ones exist, because the pin moves only when the job finishes.

Every route reads a stored readout. A run made by a build before 0.4.0, or by ``ingence index``
alone, has none. :func:`readout` then builds the one asked for and stores it, once, so an older
project still answers. A server can also warm the readouts in the background (:func:`warm`).

The ranked questions are keyed by the questions settings, not by the Dictionary. The list is read
from the run and the answer log, and the answers given since it was built are read over it
(``api/v1/questions_ranked.with_answers``). A word's meaning or a refused pack moves the
Dictionary's fingerprint. If the Dictionary keyed the list, the first answer to the checks, which
lead with words, would throw the list away (:func:`ranked_key`).

Every name in the docstrings and tests is invented.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

from ingence import SCHEMA_VERSION
from ingence.core.destinations import write_atomically
from ingence.storage.layout import owned_path

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from ingence.scan.cancel import CancellationToken
    from ingence.scan.progress import ProgressReporter
    from ingence.storage.catalog import Catalog

__all__ = [
    "NOT_READY",
    "READOUTS_DIR",
    "READOUTS_VERSION",
    "STEPS",
    "assumptions_readout",
    "build_readouts",
    "overview_readout",
    "questions_ranked_readout",
    "ranked_key",
    "read_readout",
    "readout",
    "readout_key",
    "readouts_path",
    "warm",
    "warming",
    "warming_payload",
    "with_area_answers",
    "write_readout",
]

#: The version of the stored readout format. It changes when a readout's fields or meaning change,
#: and the reader never reads an older file.
READOUTS_VERSION = "2"
READOUTS_DIR = "readouts"
_PROJECTIONS = "projections"

#: The views built, in order: each counts one step of "Preparing views: n of 7".
STEPS: tuple[str, ...] = (
    "themes",
    "map",
    "search",
    "assumptions",
    "overview",
    "questions_ranked",
    "explorer",
)

#: The words of a rule statement that make its claim an assumption: a claim at the heuristic tier
#: whose statement says what it assumed.
_ASSUMED_WORDS = ("assumed", "assumes", "assumption")
#: The tier whose claims are assumptions: the engine's heuristic tier is ``abductive`` (the best
#: reading of the evidence, retractable), the weakest a rule can conclude at.
_ASSUMED_TIER = "abductive"
#: How many examples an assumption lists.
_EXAMPLES = 3

_LOCK = threading.Lock()
_BUILDING: dict[str, threading.Lock] = {}

#: The words a page shows while a run's readouts are being warmed. A run made by ``ingence index``
#: alone has no readouts until the server builds them.
NOT_READY = "This is the last finished scan. The new one is not ready yet."
#: The runs whose readouts a server is building in the background now, by project.
_WARMING: dict[str, str] = {}


# -- where a readout lives --------------------------------------------------------------------


def readouts_path(project: Path, run_id: str) -> Path:
    """Where a run's readouts are kept."""
    return project / _PROJECTIONS / READOUTS_DIR / run_id


def readout_key(project: Path) -> str:
    """What a run's readouts are keyed by.

    The key is a digest of this module's version, the Dictionary's fingerprint and the themes
    settings (the area level among them).
    """
    from ingence.core.project import Project
    from ingence.themes.projection import project_dictionary

    _, fingerprint = project_dictionary(project)
    themes = Project.open(project).settings.themes
    text = json.dumps(
        {
            "version": READOUTS_VERSION,
            "dictionary": fingerprint,
            "themes": themes.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.blake2b(text.encode(), digest_size=10).hexdigest()


def ranked_key(project: Path) -> str:
    """What a run's ranked questions are keyed by.

    The key is a digest of this module's version and the questions settings. The sheet's budget,
    tied words and atypical pairs set how many questions there are, and its covers set their
    order. The key does not use the Dictionary (see the module docstring).
    """
    from ingence.core.project import Project

    questions = Project.open(project).settings.questions
    text = json.dumps(
        {"version": READOUTS_VERSION, "questions": questions.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.blake2b(text.encode(), digest_size=10).hexdigest()


def _key_of(project: Path, name: str) -> str:
    return ranked_key(project) if name == "questions_ranked" else readout_key(project)


def _file(project: Path, run_id: str, name: str, key: str) -> Path:
    return readouts_path(project, run_id) / f"{name}-{key}.json"


def read_readout(project: Path, run_id: str, name: str) -> dict[str, Any] | None:
    """The stored readout ``name`` of a run under the Dictionary in force, or None.

    The ranked questions are read under the questions settings in force instead.
    """
    path = _file(project, run_id, name, _key_of(project, name))
    if not path.is_file():
        return None
    with contextlib.suppress(OSError, ValueError):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and loaded.get("readouts_version") == READOUTS_VERSION:
            return loaded
    return None


def write_readout(project: Path, run_id: str, name: str, payload: Mapping[str, Any]) -> bool:
    """Store a readout whole. Return False where the project cannot be written.

    A readout is a cache, so a failed write is not a fault.
    """
    text = json.dumps(
        {**payload, "readouts_version": READOUTS_VERSION}, sort_keys=True, ensure_ascii=False
    )
    try:
        path = owned_path(project, _file(project, run_id, name, _key_of(project, name)))
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomically(path, text)
    except OSError:
        return False
    return True


def readout(project: Path, run_id: str, name: str) -> dict[str, Any]:
    """The stored readout, or, where a run has none, the one built now and stored.

    A run has none when a build before 0.4.0 made it, or when ``ingence index`` alone made it.
    The build runs once per run and name at a time.

    A readout holds the route's ``payload`` (the response model's fields and nothing else) beside
    what the step noted of it (the Overview's ``stubs``, the assumptions' ``classes``).
    """
    held = read_readout(project, run_id, name)
    if held is not None:
        return held
    with _LOCK:
        building = _BUILDING.setdefault(f"{project.resolve()}::{run_id}::{name}", threading.Lock())
    with building:
        held = read_readout(project, run_id, name)
        if held is not None:
            return held
        built = _BUILDERS[name](project, run_id)
        write_readout(project, run_id, name, built)
        return {**built, "readouts_version": READOUTS_VERSION}


# -- the step ---------------------------------------------------------------------------------


def build_readouts(
    project: Path,
    run_id: str,
    *,
    reporter: ProgressReporter | None = None,
    token: CancellationToken | None = None,
    explorer: bool = True,
) -> dict[str, Any]:
    """Build and store every readout of a published run, in :data:`STEPS`' order.

    ``reporter`` is told how many views there are and counts each one done (``views_done`` of
    ``views_total``). ``token`` stops the step between views. The function returns what was
    built: the seconds each view took, and what was skipped with the reason. A view that cannot be
    built (the themes extra is missing, say) is skipped and reported, and the job does not fail.
    The pages build the view on request.
    """
    steps = [step for step in STEPS if explorer or step != "explorer"]
    if reporter is not None:
        reporter.set(views_total=len(steps), views_done=0)
    built: dict[str, Any] = {"run_id": run_id, "seconds": {}, "skipped": {}}
    for step in steps:
        if token is not None and token.cancelled:
            built["stopped"] = True
            break
        began = time.perf_counter()
        try:
            _STEP_BUILDERS[step](project, run_id)
        except Exception as error:  # a view is a cache, so its absence is reported and not fatal
            built["skipped"][step] = f"{type(error).__name__}: {error}"
        else:
            built["seconds"][step] = round(time.perf_counter() - began, 2)
        if reporter is not None:
            reporter.advance(views_done=1)
    return built


def _build_themes(project: Path, run_id: str) -> None:
    from ingence.themes.projection import themes_projection

    themes_projection(project, run_id)


def _build_map(project: Path, run_id: str) -> None:
    from ingence.themes.projection import themes_map

    themes_map(project, run_id)


def _build_search(project: Path, run_id: str) -> None:
    from ingence.api.routes.search import build
    from ingence.storage.catalog import open_catalog

    with open_catalog(project, run_id=run_id) as catalog:
        build(catalog, project)


def _build_explorer(project: Path, run_id: str) -> None:
    from ingence.api.v1.explorer.wheel import explorer_projection

    explorer_projection(project, run_id)


def _build_overview(project: Path, run_id: str) -> None:
    write_readout(project, run_id, "overview", overview_readout(project, run_id))


def _build_assumptions(project: Path, run_id: str) -> None:
    write_readout(project, run_id, "assumptions", assumptions_readout(project, run_id))


def _build_questions_ranked(project: Path, run_id: str) -> None:
    write_readout(project, run_id, "questions_ranked", questions_ranked_readout(project, run_id))


_STEP_BUILDERS: dict[str, Callable[[Path, str], None]] = {
    "themes": _build_themes,
    "map": _build_map,
    "search": _build_search,
    "overview": _build_overview,
    "assumptions": _build_assumptions,
    "questions_ranked": _build_questions_ranked,
    "explorer": _build_explorer,
}


def warm(project: Path, run_id: str, *, token: CancellationToken | None = None) -> bool:
    """Build a run's readouts in the background of a server, and say whether anything was built.

    The function builds only where the run has no readouts, as for a run made by ``ingence index``
    alone or by a build before 0.4.0. While it runs, :func:`warming` names the run and
    ``/api/applying`` carries :data:`NOT_READY`. A request for a readout in the meantime waits on
    the same lock and reads what this function built, so nothing is built twice. ``token`` stops it
    between views, as when a server stops.
    """
    if read_readout(project, run_id, "overview") is not None:
        return False
    key = str(project.resolve())
    with _LOCK:
        if _WARMING.get(key) == run_id:
            return False
        _WARMING[key] = run_id
    try:
        build_readouts(project, run_id, token=token)
    finally:
        with _LOCK:
            if _WARMING.get(key) == run_id:
                del _WARMING[key]
    return True


def warming(project: Path) -> str | None:
    """The run whose readouts a server is building now for this project, or None."""
    with _LOCK:
        return _WARMING.get(str(project.resolve()))


def warming_payload(project: Path) -> dict[str, Any]:
    """What ``/api/applying`` says of the readouts: the run being warmed and the words.

    Both fields are None when nothing is being warmed.
    """
    run_id = warming(project)
    return {"warming": run_id, "said": NOT_READY if run_id is not None else None}


# -- the overview -----------------------------------------------------------------------------

#: What a file type's category is in the contract's words, from the file-type table's.
_CATEGORIES = frozenset({"instrument", "general", "program", "container", "archive", "document"})
#: The Overview's fields that this readout leaves empty, and the words said of each in the readout.
STUB_FIELDS: dict[str, str] = {
    "goals": "goals: left empty, because the Overview readout does not read goals",
    "checks_count": "checks: left at 0, because the Overview readout does not count checks",
}


def _file_types(catalog: Catalog, files: int) -> list[dict[str, Any]]:
    """The run's file types by share, largest first, labelled from the shipped table."""
    from ingence.dictionary.file_types import file_type_table

    by_extension: dict[str, tuple[str, str]] = {}
    for row in file_type_table().rows:
        for extension in row.extensions:
            by_extension.setdefault(extension.casefold(), (row.name, row.category))
    counted = catalog.query(
        "SELECT lower(coalesce(compound_extension, '')), lower(coalesce(extension, '')), count(*) "
        "FROM entries WHERE NOT is_deleted AND entry_type = 'file' GROUP BY 1, 2"
    ).fetchall()
    types: Counter[tuple[str, str, str]] = Counter()
    for compound, extension, count in counted:
        spelled = str(compound or extension or "")
        found = by_extension.get(spelled) or by_extension.get(str(extension or ""))
        if found is None:
            label = spelled if spelled else "no extension"
            category = "general"
            type_id = spelled or "(none)"
        else:
            label, category = found
            type_id = spelled or "(none)"
        if category not in _CATEGORIES:
            category = "general"
        types[(type_id, label, category)] += int(count)
    return [
        {
            "type_id": type_id,
            "label": label,
            "category": category,
            "files": count,
            "share": round(count / files, 4) if files else 0.0,
        }
        for (type_id, label, category), count in sorted(
            types.items(), key=lambda item: (-item[1], item[0][0])
        )
    ]


def overview_readout(project: Path, run_id: str) -> dict[str, Any]:
    """``GET /api/v1/overview``'s payload for one run, as far as the run can fill it.

    Filled from the run: the counts, the date range, the areas, the file types, completeness,
    the repeats, the leading themes, the assumptions count and the coverage. Left at their empty
    values and named in ``stubs``: the goals and the checks count (:data:`STUB_FIELDS`).
    """
    from ingence.api.v1.overview import coverage_of
    from ingence.areas import area_rows
    from ingence.completeness import completeness_of_run
    from ingence.core.project import Project
    from ingence.experiment_units import run_units
    from ingence.grouping.repeats import repeat_groups, repeats_counted
    from ingence.storage.catalog import open_catalog

    settings = Project.open(project).settings
    level = settings.themes.area_level
    with open_catalog(project, run_id=run_id) as catalog:
        counted = catalog.query(
            "SELECT count(*) FROM entries WHERE NOT is_deleted AND entry_type = 'file'"
        ).fetchone()
        files = int(counted[0]) if counted else 0
        file_types = _file_types(catalog, files)
        areas = area_rows(catalog, project, level)
        # Repeats: experiments with one signature on different start days.
        units = run_units(catalog)
        seen_once, repeated, groups = repeats_counted(
            repeat_groups(catalog, units), len(set(units.units.values()))
        )
    # Coverage: files read, files described from a read file of their series, and files not reached.
    coverage = coverage_of(project, run_id).model_dump(mode="json")
    completeness = completeness_of_run(project, run_id)
    experiments = completeness.experiments
    dates = sorted(item.date for item in experiments if item.date)
    by_area: Counter[str] = Counter()
    for item in experiments:
        from ingence.areas import area_id, area_of

        name = area_of(item.folder, level)
        if name is not None:
            by_area[area_id(item.root_id, name)] += 1
    complete = partial = raw_only = analysis_only = 0
    for item in experiments:
        raw = item.raw_acquisitions
        analysed = item.analysis_files + item.result_tables
        if raw > 0 and item.followed >= raw:
            complete += 1
        elif raw > 0 and item.followed > 0:
            partial += 1
        elif raw > 0:
            raw_only += 1
        elif analysed > 0:
            analysis_only += 1
    themes_top = _themes_top(project, run_id)
    # The assumptions step runs before the Overview step (``STEPS``), so read them where stored.
    assumptions = read_readout(project, run_id, "assumptions") or assumptions_readout(
        project, run_id
    )
    payload = {
        "api_version": "1.0",
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "counts": {"experiments": len(experiments), "files": files, "areas": len(areas)},
        "date_range": {
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
            "basis": (
                "each experiment's date: the earliest date a name gives for any of its files, "
                "else the earliest credible time of any of them"
            ),
        },
        "areas": [
            {
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "kind_assumed": row.kind_assumed,
                "experiments": by_area.get(row.id, 0),
                "files": row.files,
            }
            for row in sorted(
                areas, key=lambda row: (-by_area.get(row.id, 0), -row.files, row.name)
            )
        ],
        "file_types": file_types,
        "completeness": {
            "raw_files": sum(item.raw_acquisitions for item in experiments),
            "raw_with_analysis": sum(item.followed for item in experiments),
            "experiments_complete": complete,
            "experiments_partial": partial,
            "experiments_raw_only": raw_only,
            "experiments_analysis_only": analysis_only,
            "not_read": coverage["not_reached"],
        },
        "repeats": {"seen_once": seen_once, "repeated": repeated, "groups": groups},
        "themes_top": themes_top,
        "goals": [],
        "assumptions_count": int(assumptions["payload"]["total"]),
        "checks_count": 0,
        "coverage": coverage,
    }
    return {
        "payload": payload,
        "stubs": dict(STUB_FIELDS),
        # Each area folder's entry. Every route reads a person's answer written later
        # (``area:<entry_id>``) over the stored kind (``areas.area_kinds_of_run``).
        "areas_by_entry": {row.entry_id: row.id for row in areas},
    }


def with_area_answers(project: Path, stored: Mapping[str, Any]) -> dict[str, Any]:
    """The Overview payload with a person's answers on the areas' kinds read over the stored kinds.

    The Overview then says what ``/api/v1/areas`` says.
    """
    from ingence.areas import area_kinds_of_run

    payload = dict(stored.get("payload") or {})
    kinds = area_kinds_of_run(project, str(payload.get("run_id") or ""))
    payload["areas"] = [
        {**row, "kind": kinds[row["id"]][0], "kind_assumed": kinds[row["id"]][1]}
        if isinstance(row, dict) and row.get("id") in kinds
        else row
        for row in payload.get("areas") or []
    ]
    return payload


def _themes_top(project: Path, run_id: str, *, most: int = 5) -> list[dict[str, Any]]:
    """The leading themes, largest first, from the themes projection.

    The list is empty where the projection cannot be built (the themes extra is missing). That is
    not a failure.
    """
    try:
        from ingence.themes.projection import themes_projection

        built = themes_projection(project, run_id)
    except Exception:
        return []
    themes = built.get("themes") or []
    ranked = sorted(
        (item for item in themes if isinstance(item, dict)),
        key=lambda item: (-int(item.get("experiments") or 0), str(item.get("label") or "")),
    )
    return [
        {
            "id": f"theme:{item.get('theme')}",
            "label": str(item.get("label") or ""),
            "experiments": int(item.get("experiments") or 0),
        }
        for item in ranked[:most]
    ]


# -- the assumptions --------------------------------------------------------------------------

#: What kind of thing each assuming rule assumes, by a word of its identifier. The area rule is
#: named outright.
_KIND_WORDS: tuple[tuple[str, str], ...] = (
    ("area", "area_kind"),
    ("writer", "writer"),
    ("written", "writer"),
    ("spelling", "spelling"),
    ("workflow", "workflow"),
    ("stage", "stage"),
    ("repeat", "repeat"),
    ("domain", "domain"),
    ("pack", "domain"),
)


def assumption_kind(rule_id: str) -> str:
    """What kind of assumption a rule's claims are, from the rule's identifier."""
    from ingence.areas import AREA_RULE_ID

    if rule_id == AREA_RULE_ID:
        return "area_kind"
    for word, kind in _KIND_WORDS:
        if word in rule_id:
            return kind
    return "file_type"


def _assuming_rules(catalog: Catalog) -> dict[str, tuple[str, str]]:
    """Each rule at the heuristic tier whose statement says it assumed: its tier and sentence."""
    from ingence.core.schema import Rule
    from ingence.rulebook.render import render_rule
    from ingence.storage.index_store import record_from_row

    found: dict[str, tuple[str, str]] = {}
    rows = catalog.query(
        "SELECT * FROM rules WHERE tier = ? ORDER BY rule_id, version", [_ASSUMED_TIER]
    ).to_arrow_table()
    for row in rows.to_pylist():
        statement = str(row.get("statement") or "")
        if not any(word in statement.casefold() for word in _ASSUMED_WORDS):
            continue
        with contextlib.suppress(Exception):
            statement = render_rule(record_from_row(row, Rule))
        found[str(row["rule_id"])] = (str(row.get("tier") or _ASSUMED_TIER), statement)
    return found


def assumptions_readout(project: Path, run_id: str) -> dict[str, Any]:
    """``GET /api/v1/assumptions``'s payload for one run.

    The payload lists every class of claims that a rule at the heuristic tier made, when the rule's
    statement says it assumed. The classes are grouped by rule and value, most files first. Each
    has the Record answer that reverses it: the class handle that an answer on the class is
    written on, as ``GET /api/v1/claims/class`` reads it.
    """
    from ingence.review.claim_classes import class_members, summarised
    from ingence.storage.catalog import open_catalog

    items: list[dict[str, Any]] = []
    classes: dict[str, dict[str, str]] = {}
    with open_catalog(project, run_id=run_id) as catalog:
        rules = _assuming_rules(catalog)
        for rule_id, (tier, statement) in rules.items():
            for (rule, value), members in class_members(catalog, rule_id=rule_id).items():
                held = summarised(
                    rule,
                    value,
                    [(member.claim_id, member.subject, member.path) for member in members],
                    rule_version="",
                    predicate="",
                )
                paths = {member.subject: member.path for member in members}
                examples = [
                    {"entry_id": subject, "label": path}
                    for subject, path in sorted(paths.items(), key=lambda item: item[1])
                    if path in held.examples
                ][:_EXAMPLES]
                items.append(
                    {
                        "id": held.target,
                        "kind": assumption_kind(rule),
                        "text": _assumption_text(value, statement, held.size, rule),
                        "count": held.size,
                        "examples": examples,
                        "reject": {"route": "/api/assumptions/reject", "body": {"id": held.target}},
                        "tier": tier,
                    }
                )
                classes[held.target] = {"rule_id": rule, "value": value}
    items.sort(key=lambda item: (-int(item["count"]), str(item["id"])))
    return {
        "payload": {
            "api_version": "1.0",
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "total": len(items),
            "assumptions": items,
        },
        # Each class handle's rule and value, so that the reject route can write the answer on it.
        "classes": classes,
    }


#: What separates an assumption's one sentence from the rule's full statement in its ``text``. The
#: page shows the statement behind a "Why" disclosure. The field stays one string.
WHY_SEPARATOR = "\n\nWhy: "


def _assumption_text(value: str, statement: str, count: int = 0, rule_id: str = "") -> str:
    """One plain sentence: what was read as what, how many files, and the rule's plain sentence.

    The rule's full statement follows after :data:`WHY_SEPARATOR`.
    """
    from ingence.api.assumption_words import rule_first_sentence

    read = str(value).replace("_", " ")
    files = f" {count:,} file{'' if count == 1 else 's'}" if count else ""
    head = f"Read as {read}:{files}." if files else f"Read as {read}."
    sentence = rule_first_sentence(rule_id, statement)
    said = f"{head} {sentence}" if sentence else head
    full = statement.strip()
    return f"{said}{WHY_SEPARATOR}{full}" if full else said


def questions_ranked_readout(project: Path, run_id: str) -> dict[str, Any]:
    """The whole ranked list of ``GET /api/v1/questions/ranked`` for one run.

    The list is whole because no page has been cut from it. It comes from
    ``api/v1/questions_ranked.ranked_readout``. ``checks`` reads it too.
    """
    from ingence.api.v1.questions_ranked import ranked_readout

    return ranked_readout(project, run_id)


_BUILDERS: dict[str, Callable[[Path, str], dict[str, Any]]] = {
    "overview": overview_readout,
    "assumptions": assumptions_readout,
    "questions_ranked": questions_ranked_readout,
}


def stored_names() -> Sequence[str]:
    """The readouts kept as JSON here (the themes, map, search and Explorer have their own)."""
    return tuple(_BUILDERS)
