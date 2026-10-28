"""The Dictionary page: the engine's vocabulary, where it came from, and a person's own.

The page lets users view and edit entries (adding terms and surfaces), manage dictionary packs,
and export their personal dictionary.

What it reads. The page reads every sense that the project's Dictionary holds, grouped by its
term, each with its spellings and where it came from. A sense comes from one of these:

- the core (``dictionary/builtin/``);
- a shipped domain pack (``dictionary/packs/<domain>/``), loaded as the project's settings and a
  person's accepted proposals say;
- a laboratory's installed pack (``teach pack install``);
- a person's own answers (a ``USER`` sense read from the Record).

The page lists inactive senses and spellings too, grayed, with who inactivated them and when. The
run's bindings give how many names in the run each term binds, in one grouped count.

What it writes, and how. Nothing here writes to the Record except through its one way in: the
server's answer route (``/api/review/answer``) hands a ``{"dictionary": ...}`` body to
:func:`write`, on the precedent of the Graph workspace's labels (``labels.py``). Every event has
the shape that the engine already reads:

- A spelling added to a sense, or a new sense, is the event that a word's definition is
  (``term:<spelling>``, ``assign`` naming a sense the Dictionary holds, ``define`` naming a new
  one, the kind in the payload). The Dictionary reads it as a ``USER`` sense at precedence 40,
  and the applier re-binds the spelling at once.
- Inactivating a sense or one spelling of one sense is a ``reject`` on ``sense:<term>`` or
  ``surface:<term>:<spelling>``, and bringing it back is a ``confirm`` on the same target
  (``dictionary/inactive.py``). This adds no new event kind, leaves the schema unchanged, and
  deletes nothing.
- A shipped domain pack is accepted or refused on ``pack:<domain>`` by the answer route's own
  proposal path (``decide.py``), exactly as the Questions page does.

A laboratory's pack is installed and removed through a workspace write
(``/api/dictionary/pack``). This is because a pack is files (a pack directory and the settings
naming it, which ``teach pack install`` writes) and never a decision in the Record. A pack adds
vocabulary and decides nothing, and the page says so.

Export is the pack section of ``teach export-answers``, with the person's inactivations listed
beside it. A pack cannot inactivate, so loading the file inactivates nothing.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import yaml

from ingence.api.routes.decide import AnswerRefusedError
from ingence.core.destinations import open_appending
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import DictionaryOrigin, RecordEventKind
from ingence.dictionary.laboratory_packs import PACK_CHANGES
from ingence.record.log import append_event, latest_by_target, read_events
from ingence.record.questions import TARGET_PREFIX, TERM_PREFIX, term_target
from ingence.record.targets import (
    PACK_PREFIX,
    SURFACE_PREFIX,
    sense_target,
    surface_target,
)
from ingence.storage.layout import owned_path

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.dictionary.inactive import Inactivation, Inactivations
    from ingence.dictionary.store import Section, Sense
    from ingence.storage.catalog import Catalog

__all__ = [
    "LIMIT",
    "ORIGINS",
    "PACK_BODY_BYTES",
    "PACK_LOG",
    "PACK_ROUTE",
    "PACK_TOO_LARGE",
    "REMOVED_PACKS",
    "DictionaryRefusedError",
    "add_spelling",
    "answer_on",
    "composed_senses",
    "export",
    "inactivate",
    "install_from_text",
    "listing",
    "packs",
    "reactivate",
    "read",
    "remove",
    "taught_by",
    "term_detail",
    "write",
]

#: Fifty rows a page, as on every other page.
LIMIT = 50

#: Where a sense came from, as the page names and colors it.
ORIGINS = ("core", "domain", "laboratory", "yours")

#: The workspace write that installs and removes a laboratory's pack (``api/server.py``).
PACK_ROUTE = "/api/dictionary/pack"

#: How large a pack file the page installs. The file travels inside the write's body, and a
#: decision's body has a 64 KB ceiling, which would refuse a laboratory's larger pack. Four
#: megabytes is well above any shipped section (the largest is under 20 KB) and still a ceiling
#: on what a loopback page may send.
PACK_BODY_BYTES = 4 * 1024 * 1024
PACK_TOO_LARGE = (
    "a pack file up to 4 MB installs here; a larger one installs with `ingence teach pack install`"
)

#: Who installed or removed a pack, and when. A pack is files and never a decision in the
#: Record, so each change is one line of the workspace's own pack log, appended and never
#: rewritten, naming the person, the moment, the pack and its digest. Each line waits for Re-run as
#: a pack answer does (``workspace.state``).
PACK_LOG = PACK_CHANGES

#: Where a removed pack's files are kept. Removing stops the pack loading and moves the
#: project's copy here, named with the moment, rather than deleting what may be its only copy. A
#: run that loaded the pack names its digest, and reproducing that run needs the files.
REMOVED_PACKS = "dictionary/removed-packs"

#: What the page says each act writes, once, where a person may read it.
PAGE_SOURCE = {"page": "dictionary"}

#: A term a person names is one word, as every shipped term is: no space and no colon (a target
#: spells a spelling after its term and a colon).
_TERM = re.compile(r"^[^\s:]+$")
_PACK_NAME = re.compile(r"[^a-z0-9_\-]+")


class DictionaryRefusedError(AnswerRefusedError):
    """A Dictionary write was refused; the message says why in words the page shows."""


# ----------------------------------------------------------------------------- what is held


@dataclass(frozen=True, slots=True)
class Held:
    """One sense as one origin holds it."""

    term: str
    theme_kind: str
    surfaces: tuple[str, ...]
    #: ``core``, ``domain``, ``laboratory`` or ``yours``.
    origin: str
    #: The section, the domain, the pack's name, or ``answer``.
    source: str
    scope: str = "project"
    #: For a person's own: whether it named a new sense (define) or one held (assign).
    defined: bool = False


@lru_cache(maxsize=1)
def _shipped() -> tuple[Section, ...]:
    """The shipped sections, read once per process: they are files of the installed engine."""
    from ingence.dictionary.store import load_shipped_sections

    return load_shipped_sections()


def _settings(project: Path) -> Any:
    from ingence.core.project import Project
    from ingence.storage.layout import PROJECT_FILE

    if not (project / PROJECT_FILE).is_file():
        return None
    return Project.open(project).settings.dictionary


def _loaded_domains(project: Path, settings: Any) -> frozenset[str] | None:
    """The shipped domains the project loads: every one but a vetoed domain. None where every one
    loads."""
    from ingence.dictionary.packs.proposals import vetoed_domains
    from ingence.dictionary.store import PACKS_DIRECTORY, load_sections

    if settings is None:
        return None
    vetoed = vetoed_domains(project)
    if not vetoed:
        return None
    return frozenset(
        section.domain
        for section in load_sections(PACKS_DIRECTORY)
        if section.domain and section.domain not in vetoed
    )


def composed_senses(project: Path, *, fold: bool = False) -> tuple[tuple[Held, Sense], ...]:
    """Every sense the project's Dictionary holds, each as the page lists it and as the engine
    holds it, by origin and before any inactivation.

    The same composition :meth:`DictionaryStore.for_project` makes, kept apart by origin so a
    page can say where each spelling came from. With ``fold``, a shipped extension is folded into
    the sense it extends, as the website's reference writes the shipped words (the Copy button's
    text must be the same text).
    """
    from ingence.dictionary.answers import answered_sections
    from ingence.dictionary.laboratory_packs import installed_packs
    from ingence.dictionary.store import fold_extensions, load_sections

    settings = _settings(project)
    domains = _loaded_domains(project, settings)
    found: list[tuple[Held, Sense]] = []
    for section in fold_extensions(_shipped()) if fold else _shipped():
        if section.domain and domains is not None and section.domain not in domains:
            continue
        for sense in section.senses:
            found.append(
                (
                    Held(
                        term=sense.term,
                        theme_kind=sense.theme_kind,
                        surfaces=sense.surfaces,
                        origin="domain" if section.domain else "core",
                        source=section.domain or section.name,
                    ),
                    sense,
                )
            )
    named = tuple(settings.packs) if settings is not None else ()
    installed = {pack.name: pack for pack in installed_packs(project)}
    for name in named:
        pack = installed.get(name)
        if pack is None:
            continue
        for section in load_sections(pack.path, origin=DictionaryOrigin.PACK):
            for sense in section.senses:
                found.append(
                    (
                        Held(
                            term=sense.term,
                            theme_kind=sense.theme_kind,
                            surfaces=sense.surfaces,
                            origin="laboratory",
                            source=pack.name,
                        ),
                        sense,
                    )
                )
    for section in answered_sections(project):
        for sense in section.senses:
            found.append(
                (
                    Held(
                        term=sense.term,
                        theme_kind=sense.theme_kind,
                        surfaces=sense.surfaces,
                        origin="yours",
                        source="answer",
                        scope=sense.scope.token,
                        defined=bool(sense.properties.get("defined")),
                    ),
                    sense,
                )
            )
    return tuple(found)


def held_senses(project: Path) -> tuple[Held, ...]:
    """Every sense the project's Dictionary holds, by origin, before any inactivation."""
    return tuple(held for held, _ in composed_senses(project))


def taught_by(project: Path) -> dict[str, dict[str, str]]:
    """Who taught each term a person's own answer names, and when, by term (the newest answer).

    An answer in force that names a term (an assignment or a definition, to a question or to a
    word no rule asked about) is a person saying what a spelling means, and the Record keeps who
    said it. The shipped words have no such person: they are not here.
    """
    events = read_events(project)
    in_force = {event.event_id for event in latest_by_target(events).values()}
    found: dict[str, dict[str, str]] = {}
    for event in sorted(events, key=lambda each: each.sequence):
        if event.event_id not in in_force:
            continue
        if not event.target.startswith((f"{TERM_PREFIX}:", f"{TARGET_PREFIX}:")):
            continue
        if event.kind not in (RecordEventKind.ASSIGN, RecordEventKind.DEFINE):
            continue
        term = str(_payload(event).get("answer") or "").strip()
        if term:
            found[term] = {"by": event.actor, "at": event.occurred_at}
    return found


def _by_term(held: tuple[Held, ...]) -> dict[str, list[Held]]:
    grouped: dict[str, list[Held]] = {}
    for item in held:
        grouped.setdefault(item.term, []).append(item)
    return grouped


_COUNTS: dict[tuple[str, str], dict[str, int]] = {}


def _names_bound(catalog: Catalog, project: Path) -> dict[str, int] | None:
    """How many names in the run each term binds, from the run's name bindings (a dive's content
    bindings are not names); None where the run holds none to count. Counted once per run and
    kept, since a run's bindings never change."""
    row = catalog.query("SELECT run_id FROM run").fetchone()
    run_id = str(row[0]) if row and row[0] else ""
    if not run_id:
        return None
    key = (str(project), run_id)
    if key not in _COUNTS:
        try:
            rows = catalog.query(
                "SELECT term, count(DISTINCT entry_id) FROM dictionary_bindings WHERE term IS NOT "
                "NULL AND resolution_status = 'resolved' AND source_scope = 'name' GROUP BY term"
            ).fetchall()
        except Exception:
            return None
        if len(_COUNTS) > 8:
            _COUNTS.clear()
        _COUNTS[key] = {str(term): int(count) for term, count in rows}
    return _COUNTS[key]


def _said(item: Inactivation | None) -> dict[str, str] | None:
    if item is None:
        return None
    return {"since": item.occurred_at, "by": item.actor, "event_id": item.event_id}


def _row(
    term: str,
    items: list[Held],
    inactive: Inactivations,
    counts: dict[str, int] | None,
    taught: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    kinds = sorted({item.theme_kind for item in items})
    yours = any(item.origin == "yours" for item in items)
    origins: list[dict[str, str]] = []
    for item in items:
        pair = {"origin": item.origin, "source": item.source}
        if pair not in origins:
            origins.append(pair)
    surfaces: dict[str, list[str]] = {}
    for item in items:
        for surface in item.surfaces:
            held = surfaces.setdefault(surface, [])
            if item.origin not in held:
                held.append(item.origin)
    return {
        "term": term,
        "kind": kinds[0] if kinds else "",
        "kinds": kinds,
        "origins": origins,
        "yours": yours,
        # Who taught it and when, where a person's answer names it (the Record keeps who said it).
        "taught": (taught or {}).get(term)
        if any(item.origin == "yours" for item in items)
        else None,
        "surfaces": [
            {
                "spelling": spelling,
                "origins": held,
                "inactive": _said(inactive.surface(term, spelling)),
            }
            for spelling, held in surfaces.items()
        ],
        "inactive": _said(inactive.sense(term)),
        "names": None if counts is None else counts.get(term, 0),
    }


def _matches(row: dict[str, Any], text: str) -> int | None:
    """How closely a row answers a search: 0 exactly, 1 by its start, 2 inside; None not at all."""
    if not text:
        return 2
    words = [row["term"].casefold()] + [s["spelling"].casefold() for s in row["surfaces"]]
    if text in words:
        return 0
    if any(word.startswith(text) for word in words):
        return 1
    if any(text in word for word in words):
        return 2
    return None


def _all_rows(catalog: Catalog, project: Path) -> tuple[list[dict[str, Any]], bool]:
    from ingence.dictionary.inactive import inactivations_in_force

    inactive = inactivations_in_force(project)
    counts = _names_bound(catalog, project)
    taught = taught_by(project)
    rows = [
        _row(term, items, inactive, counts, taught)
        for term, items in _by_term(held_senses(project)).items()
    ]
    return rows, counts is not None


def _origin_keeps(row: dict[str, Any], origin: str) -> bool:
    if not origin:
        return True
    if origin == "inactive":
        return row["inactive"] is not None or any(s["inactive"] for s in row["surfaces"])
    return any(item["origin"] == origin for item in row["origins"])


def listing(
    catalog: Catalog,
    project: Path,
    *,
    text: str | None = None,
    origin: str | None = None,
    kind: str | None = None,
    order: str | None = None,
    limit: int = LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of the vocabulary: every term matching a spelling or a term, by origin and kind.

    Ordered by how closely a row answers the search, then by how many names each binds (most
    first), then by term; ``order=term`` orders by term alone. The counts beside the filters are
    over the rows the search keeps, so a chip says what choosing it would show.
    """
    rows, counted = _all_rows(catalog, project)
    wanted = (text or "").strip().casefold()
    searched = [
        (closeness, row) for row in rows if (closeness := _matches(row, wanted)) is not None
    ]
    by_origin = dict.fromkeys((*ORIGINS, "inactive"), 0)
    by_kind: dict[str, int] = {}
    for _, row in searched:
        for name in by_origin:
            if _origin_keeps(row, name):
                by_origin[name] += 1
        for each in row["kinds"]:
            by_kind[each] = by_kind.get(each, 0) + 1
    kept = [
        (closeness, row)
        for closeness, row in searched
        if _origin_keeps(row, origin or "") and (not kind or kind in row["kinds"])
    ]
    if order == "term":
        kept.sort(key=lambda pair: pair[1]["term"].casefold())
    else:
        kept.sort(key=lambda pair: (pair[0], -(pair[1]["names"] or 0), pair[1]["term"].casefold()))
    start = max(0, offset)
    return {
        "rows": [row for _, row in kept[start : start + max(1, limit)]],
        "listed": len(kept),
        "searched": len(searched),
        "offset": start,
        "limit": limit,
        "terms": len(rows),
        "spellings": sum(len(row["surfaces"]) for row in rows),
        "yours": sum(1 for row in rows if row["yours"]),
        "origins": by_origin,
        "kinds": sorted(by_kind.items(), key=lambda pair: (-pair[1], pair[0])),
        "counted": counted,
    }


# ----------------------------------------------------------------------------- one term


def _payload(event: RecordEvent) -> dict[str, Any]:
    try:
        found = json.loads(event.payload)
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _what(event: RecordEvent, payload: dict[str, Any], term: str) -> str:
    """What one event did to a term, in a few words."""
    target = event.target
    if target == sense_target(term):
        return "inactivated" if event.kind is RecordEventKind.REJECT else "brought back"
    if target.startswith(f"{SURFACE_PREFIX}:{term}:"):
        spelling = target.split(":", 2)[2]
        verb = "inactivated" if event.kind is RecordEventKind.REJECT else "brought back"
        return f"{verb} the spelling {spelling}"
    raised = str(payload.get("raised") or "")
    if event.kind is RecordEventKind.DEFINE:
        return f"defined {raised} as a new meaning"
    return f"added the spelling {raised}"


def _history(project: Path, term: str) -> list[dict[str, Any]]:
    events = read_events(project)
    in_force = {event.event_id for event in latest_by_target(events).values()}
    found: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda event: event.sequence, reverse=True):
        payload = _payload(event)
        about_sense = event.target == sense_target(term) or event.target.startswith(
            f"{SURFACE_PREFIX}:{term}:"
        )
        answered = (
            event.target.startswith((f"{TERM_PREFIX}:", f"{TARGET_PREFIX}:"))
            and event.kind in (RecordEventKind.ASSIGN, RecordEventKind.DEFINE)
            and str(payload.get("answer") or "") == term
        )
        if not (about_sense or answered):
            continue
        found.append(
            {
                "sequence": event.sequence,
                "kind": event.kind.value,
                "actor": event.actor,
                "occurred_at": event.occurred_at,
                "target": event.target,
                "what": _what(event, payload, term),
                "in_force": event.event_id in in_force,
                "note": payload.get("note"),
            }
        )
    return found


def term_detail(catalog: Catalog, project: Path, term: str) -> dict[str, Any]:
    """One term: each origin's sense, every spelling and its state, the names it binds, a few of
    them, and every decision a person made about it, newest first."""
    from ingence.dictionary.inactive import inactivations_in_force

    pairs = [(held, sense) for held, sense in composed_senses(project) if held.term == term]
    items = [held for held, _ in pairs]
    if not items:
        message = f"no sense {term!r} in this project's Dictionary"
        raise LookupError(message)
    inactive = inactivations_in_force(project)
    counts = _names_bound(catalog, project)
    examples: list[str] = []
    if counts is not None and counts.get(term):
        examples = [
            str(name)
            for (name,) in catalog.query(
                "SELECT DISTINCT e.name FROM dictionary_bindings b JOIN entries e "
                "ON e.entry_id = b.entry_id WHERE b.term = ? AND b.resolution_status = 'resolved' "
                "AND b.source_scope = 'name' ORDER BY e.name LIMIT 5",
                [term],
            ).fetchall()
        ]
    return {
        **_row(term, items, inactive, counts, taught_by(project)),
        "senses": [
            {
                "origin": held.origin,
                "source": held.source,
                "theme_kind": held.theme_kind,
                "surfaces": list(held.surfaces),
                "scope": held.scope,
                "defined": held.defined,
                # Meanings the Dictionary itself says this sense can be confused with (declared).
                "ambiguous_with": sorted(sense.declared_ambiguity),
            }
            for held, sense in pairs
        ],
        "examples": examples,
        "counted": counts is not None,
        "history": _history(project, term),
    }


# ----------------------------------------------------------------------------- packs


def _proposed(catalog: Catalog) -> dict[str, str]:
    """The domain packs the run proposes loading, each with the words its question raised."""
    try:
        rows = catalog.query(
            "SELECT DISTINCT unnest(raised) FROM findings WHERE rule_id IN "
            "(SELECT rule_id FROM rules WHERE predicate = 'domain_pack_would_read')"
        ).fetchall()
    except Exception:
        return {}
    found: dict[str, str] = {}
    for (raised,) in rows:
        # A pack proposed, or under every pack one asked about because it reads nothing.
        matched = re.fullmatch(r"(?:load|keep) the (.+) pack", str(raised))
        if matched:
            found[matched.group(1)] = str(raised)
    return found


def packs(catalog: Catalog, project: Path) -> dict[str, Any]:
    """The shipped domain packs (loaded or not, proposed or answered) and a laboratory's own.

    The function reads the Record once for the whole answer (:func:`one_reading`).
    """
    from ingence.dictionary.packs.proposals import one_reading

    with one_reading():
        return _packs(catalog, project)


def _packs(catalog: Catalog, project: Path) -> dict[str, Any]:
    from ingence.dictionary.laboratory_packs import installed_packs
    from ingence.dictionary.packs.proposals import domain_priors, pack_answers
    from ingence.dictionary.store import load_sections

    settings = _settings(project)
    domains = _loaded_domains(project, settings)
    answers = pack_answers(project)
    proposed = _proposed(catalog)
    shipped: dict[str, dict[str, Any]] = {}
    for section in _shipped():
        if not section.domain:
            continue
        entry = shipped.setdefault(
            section.domain, {"domain": section.domain, "senses": 0, "spellings": 0}
        )
        entry["senses"] += len(section.senses)
        entry["spellings"] += sum(len(sense.surfaces) for sense in section.senses)
    # Every domain loads, level with the core. A confirm is a prior for the domain and a refusal
    # is a veto. Nothing is accepted by default or kept under the core.
    priors = domain_priors(project) if settings is not None else frozenset()
    for domain, entry in shipped.items():
        answer = answers.get(domain)
        entry["loaded"] = domains is None or domain in domains
        entry["why"] = (
            "refused: its words are left out"
            if answer is not None and answer.kind is RecordEventKind.REJECT
            else "loaded, and a prior: a word shared between domains reads as this domain's "
            "where a folder's files do not decide"
            if domain in priors
            else "loaded: every domain's words load"
        )
        entry["accepted"] = domain in priors
        entry["not_over_the_core"] = False
        entry["answer"] = (
            {
                "word": "accept" if answer.kind is RecordEventKind.CONFIRM else "refuse",
                "by": answer.actor,
                "at": answer.occurred_at,
            }
            if answer is not None
            and answer.kind in (RecordEventKind.CONFIRM, RecordEventKind.REJECT)
            else None
        )
        entry["proposed"] = proposed.get(domain)
        entry["idle"] = False
        entry["target"] = f"{PACK_PREFIX}:{domain}"
    loaded_names = tuple(settings.packs) if settings is not None else ()
    laboratory = []
    for pack in installed_packs(project):
        sections = load_sections(pack.path, origin=DictionaryOrigin.PACK)
        laboratory.append(
            {
                "name": pack.name,
                "version": pack.version,
                "summary": pack.summary,
                "digest": pack.digest,
                "loaded": pack.name in loaded_names,
                "senses": sum(len(section.senses) for section in sections),
            }
        )
    return {
        "mode": "all",
        "domain": sorted(shipped.values(), key=lambda entry: entry["domain"]),
        "laboratory": laboratory,
        "collisions": list(
            _collisions(
                None if domains is None else tuple(sorted(domains)),
                tuple(
                    (pack.name, pack.digest, str(pack.path))
                    for pack in installed_packs(project)
                    if pack.name in loaded_names
                ),
            )
        ),
        # The one rule of standing, in plain words.
        "standing_rule": STANDING_RULE,
        "accepted": sorted(priors),
    }


#: The one rule of standing, in plain words, said on the Dictionary page.
STANDING_RULE = (
    "Every shipped domain's words load, level with the core and with each other, unless you "
    "refuse a domain. Where two of them spell one word differently, the folder's own files decide "
    "which is read when they lean far enough one way; otherwise the core's meaning is read and "
    "marked assumed, or the word is a tie, asked once."
)


@lru_cache(maxsize=8)
def _collisions(
    domains: tuple[str, ...] | None,
    laboratory: tuple[tuple[str, str, str], ...],
) -> tuple[dict[str, Any], ...]:
    """Every spelling the core and a loaded domain both hold, and which meaning a name takes.

    By the vocabulary alone, a name takes neither meaning. Every shipped section stands level, so
    the page says "neither" and the run reads each such word by its folder's files, or as the core's
    meaning marked assumed. A laboratory's own pack stands level too. There is one row per spelling,
    so a word is said once however many files spell it. The function reads the vocabulary that the
    project loads, before any answer, and keeps the result per set of loaded packs, since the page
    asks on every search.
    """
    from pathlib import Path

    from ingence.dictionary.store import DictionaryStore, _binds_outside_an_extension, load_sections

    sections = [
        section
        for section in _shipped()
        if not section.domain or domains is None or section.domain in domains
    ]
    named: dict[str, str] = {}
    for name, _, path in laboratory:
        for section in load_sections(Path(path), origin=DictionaryOrigin.PACK):
            named[section.name] = name
            sections.append(section)

    def core_s(sense: Sense) -> bool:
        return sense.origin is DictionaryOrigin.BUILTIN and not sense.domain

    def lab_s(sense: Sense) -> bool:
        return sense.origin is DictionaryOrigin.PACK

    def shipped_s(sense: Sense) -> bool:
        return sense.origin is DictionaryOrigin.BUILTIN and bool(sense.domain)

    def meets(meeting: list[Sense]) -> bool:
        # The core and any pack; or a laboratory's pack and a shipped one, which it outranks.
        # Two shipped packs alone are a tie between equals.
        has_core = any(core_s(sense) for sense in meeting)
        return (has_core and any(not core_s(sense) for sense in meeting)) or (
            any(lab_s(sense) for sense in meeting) and any(shipped_s(sense) for sense in meeting)
        )

    found: list[dict[str, Any]] = []
    for contested in DictionaryStore(sections).collisions():
        # Only where the two can meet: in a word, among the senses a word may take, or after a
        # dot, among the formats. A format spelled only after a dot never meets a word (``md``).
        met = [
            meeting
            for meeting in (
                [
                    sense
                    for sense in contested.senses
                    if _binds_outside_an_extension(sense, contested.surface)
                ],
                [sense for sense in contested.senses if sense.theme_kind == "format"],
            )
            if meets(meeting)
        ]
        if not met:
            continue
        core = [sense for sense in met[0] if core_s(sense)]
        others = [sense for sense in met[0] if not core_s(sense)]
        # Ranked by the one place where precedence is applied, as every lookup is.
        winner, tied, _ = DictionaryStore._winner(met[0])
        found.append(
            {
                "spelling": contested.surface,
                "used": "neither" if tied else "laboratory" if lab_s(winner) else "core",
                "accepted": None,
                "core": [{"term": sense.term, "kind": sense.theme_kind} for sense in core],
                "packs": [
                    {
                        "pack": named.get(sense.section, sense.section)
                        if sense.origin is DictionaryOrigin.PACK
                        else sense.domain or sense.section,
                        "term": sense.term,
                        "kind": sense.theme_kind,
                        "laboratory": sense.origin is DictionaryOrigin.PACK,
                    }
                    for sense in others
                ],
            }
        )
    return tuple(found)


# ----------------------------------------------------------------------------- export


def export(project: Path) -> dict[str, Any]:
    """The person's own dictionary as a pack section: what they added and answered, and what
    they inactivated, listed for a reader (a pack cannot inactivate)."""
    from ingence.dictionary.answers import pack_section_from_answers

    name = _PACK_NAME.sub("-", project.name.casefold()).strip("-_") or "project"
    section = f"{name}-dictionary"
    found = pack_section_from_answers(project, section=section, inactive=True)
    return {
        "filename": f"{section}.yaml",
        "text": found.text,
        "exported": found.exported,
        "inactive": found.inactive,
        "left_out": dict(found.left_out),
    }


# ----------------------------------------------------------------------------- writing


def _current_run(catalog: Catalog) -> str:
    row = catalog.query("SELECT run_id FROM run").fetchone()
    if not row or not row[0]:
        message = "the project has no run yet, so there is nothing to decide against"
        raise DictionaryRefusedError(message)
    return str(row[0])


def _actor(actor: str) -> str:
    named = " ".join(str(actor or "").split())
    if not named:
        message = "a decision names who made it; one by nobody is not attributable"
        raise DictionaryRefusedError(message)
    return named


def _one_word(project: Path, spelling: str) -> None:
    """Refuse a spelling the names' reader would read as more than one piece."""
    from ingence.dictionary.normalize import Normaliser
    from ingence.dictionary.store import DictionaryStore

    settings = _settings(project)
    # Glued words are read whole. A person who teaches a spelling that the reader would divide
    # into two known words (``channelhoechst``) teaches it as one, which is the remedy for a
    # division that the laboratory does not mean.
    reader = Normaliser(
        DictionaryStore.for_project(project),
        glued_words="whole",
        **({"glued_runs": settings.glued_runs} if settings is not None else {}),
    )
    pieces = [atom.text for atom in reader.atoms(spelling)]
    if len(pieces) != 1 or pieces[0] != spelling:
        shown = ", ".join(pieces) or "nothing"
        message = (
            f"names are read as pieces, and {spelling!r} reads as {len(pieces)} ({shown}); "
            "a spelling is one piece"
        )
        raise DictionaryRefusedError(message)


def answer_on(project: Path, spelling: str) -> dict[str, str] | None:
    """The person's answer in force on a spelling's target, or None: what an add would replace.

    There is one answer per spelling, by the Record's target contract:
    ``term:<spelling casefolded>``, the latest in force. So ``nM`` and ``nm`` are one answer, and a
    spelling added to a second meaning leaves the first. The page says so before a person commits.
    """
    event = latest_by_target(read_events(project)).get(term_target(spelling))
    if event is None or event.kind not in (RecordEventKind.ASSIGN, RecordEventKind.DEFINE):
        return None
    payload = _payload(event)
    return {
        "spelling": str(payload.get("raised") or spelling),
        "means": str(payload.get("answer") or ""),
        "by": event.actor,
        "at": event.occurred_at,
    }


def add_spelling(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    spelling: str,
    term: str,
    theme_kind: str | None = None,
    note: str | None = None,
    replace: bool = False,
) -> RecordEvent:
    """Add a spelling to a sense the Dictionary holds, or a new sense with its first spelling.

    The event a word's definition is (``term:<spelling>``): an ``assign`` naming a sense held, on
    its own kind, or a ``define`` naming a new one with the kind given. The Dictionary reads it as
    a ``USER`` sense at precedence 40, so the spelling binds that sense wherever it stands, and
    the applier re-binds it at once. The spelling is kept as written: ``raised`` is the surface
    the person's sense holds, so ``nM`` stays a spelling apart from ``nm``.
    """
    from ingence.dictionary.inactive import inactive_refusal

    who = _actor(actor)
    spelled = spelling.strip()
    named = term.strip()
    if not spelled:
        message = "a spelling is needed"
        raise DictionaryRefusedError(message)
    if not named:
        message = "the meaning it spells is needed"
        raise DictionaryRefusedError(message)
    grouped = _by_term(held_senses(project))
    items = grouped.get(named)
    kinds = sorted({item.theme_kind for item in items}) if items else []
    axis = (theme_kind or "").strip()
    if items:
        if axis and axis not in kinds:
            message = (
                f"{named!r} is a meaning of the kind {', '.join(kinds)}, and {axis!r} was given; "
                "choose that kind, or another name for a new meaning"
            )
            raise DictionaryRefusedError(message)
        axis = axis or kinds[0]
        why = inactive_refusal(project, spelling=spelled, term=named)
        if why is not None:
            raise DictionaryRefusedError(why)
        if any(spelled in item.surfaces for item in items):
            message = f"{spelled!r} is already a spelling of {named}"
            raise DictionaryRefusedError(message)
    else:
        if not _TERM.match(named):
            message = f"a new meaning's name is one word, with no space or colon: {named!r} is not"
            raise DictionaryRefusedError(message)
        if not axis:
            message = f"{named!r} is a new meaning, so say what kind of thing it is"
            raise DictionaryRefusedError(message)
    _one_word(project, spelled)
    held = answer_on(project, spelled)
    if held is not None and (held["means"], held["spelling"]) != (named, spelled) and not replace:
        message = (
            f"{held['spelling']} already means {held['means']} ({held['by']}'s answer of "
            f"{held['at'][:10]}); adding it here would replace that answer"
        )
        raise DictionaryRefusedError(message)
    return append_event(
        project,
        kind=RecordEventKind.ASSIGN if items else RecordEventKind.DEFINE,
        actor=who,
        occurred_at=occurred_at,
        target=term_target(spelled),
        against_run_id=_current_run(catalog),
        payload={
            "raised": spelled,
            "answer": named,
            "theme_kind": axis,
            "source": dict(PAGE_SOURCE),
            **({"note": note} if note else {}),
        },
        scope=PROJECT_SCOPE,
    )


def _spellings_of(items: list[Held]) -> list[str]:
    return sorted({surface for item in items for surface in item.surfaces})


def _inactivation_target(
    project: Path, term: str, spelling: str | None
) -> tuple[str, list[str], Inactivation | None]:
    from ingence.dictionary.inactive import inactivations_in_force

    named = term.strip()
    items = _by_term(held_senses(project)).get(named)
    if not items:
        message = f"no sense {named!r} in this project's Dictionary"
        raise DictionaryRefusedError(message)
    inactive = inactivations_in_force(project)
    if spelling is None or not spelling.strip():
        return sense_target(named), _spellings_of(items), inactive.sense(named)
    spelled = spelling.strip()
    held = _spellings_of(items)
    if spelled not in held:
        message = f"{spelled!r} is not a spelling of {named}"
        raise DictionaryRefusedError(message)
    # One spelling covers its case twins in this sense: the event already in force, whichever
    # form it named, is the one that a bringing back answers.
    found = inactive.surface(named, spelled)
    written = found.spelling if found is not None and found.spelling else spelled
    twins = [each for each in held if each.casefold() == spelled.casefold()]
    return surface_target(named, written), twins, found


def inactivate(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    term: str,
    spelling: str | None = None,
    note: str | None = None,
) -> RecordEvent:
    """Inactivate a sense, or one spelling of it: a ``reject`` on its target, nothing deleted."""
    who = _actor(actor)
    target, spellings, held = _inactivation_target(project, term, spelling)
    if held is not None:
        message = f"already inactive, since {held.occurred_at} by {held.actor}"
        raise DictionaryRefusedError(message)
    return _write_state(
        catalog, project, RecordEventKind.REJECT, who, occurred_at, target, spellings, note
    )


def reactivate(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    term: str,
    spelling: str | None = None,
    note: str | None = None,
) -> RecordEvent:
    """Bring back what was inactivated: a ``confirm`` on the same target."""
    who = _actor(actor)
    target, spellings, held = _inactivation_target(project, term, spelling)
    if held is None:
        message = "not inactive, so there is nothing to bring back"
        raise DictionaryRefusedError(message)
    return _write_state(
        catalog, project, RecordEventKind.CONFIRM, who, occurred_at, target, spellings, note
    )


def _write_state(
    catalog: Catalog,
    project: Path,
    kind: RecordEventKind,
    actor: str,
    occurred_at: str,
    target: str,
    spellings: list[str],
    note: str | None,
) -> RecordEvent:
    return append_event(
        project,
        kind=kind,
        actor=actor,
        occurred_at=occurred_at,
        target=target,
        against_run_id=_current_run(catalog),
        payload={
            "spellings": spellings,
            "source": dict(PAGE_SOURCE),
            **({"note": note} if note else {}),
        },
        scope=PROJECT_SCOPE,
    )


#: The acts :func:`write` takes, each a function above.
ACTS = ("add", "inactivate", "reactivate")


def write(
    catalog: Catalog, project: Path, body: dict[str, Any], *, occurred_at: str
) -> RecordEvent:
    """One write from the page, through the answer route: add, inactivate or reactivate."""
    act = str(body.get("dictionary") or "")
    if act not in ACTS:
        message = (
            f"{act!r} is not something the Dictionary section of Engine does: {', '.join(ACTS)}"
        )
        raise DictionaryRefusedError(message)
    actor = str(body.get("actor") or "")
    term = str(body.get("term") or "")
    note = str(body["note"]) if body.get("note") else None
    if act == "add":
        return add_spelling(
            catalog,
            project,
            actor=actor,
            occurred_at=occurred_at,
            spelling=str(body.get("spelling") or ""),
            term=term,
            theme_kind=str(body.get("theme_kind") or "") or None,
            note=note,
            replace=bool(body.get("replace")),
        )
    spelling = str(body.get("spelling") or "") or None
    chosen = inactivate if act == "inactivate" else reactivate
    return chosen(
        catalog,
        project,
        actor=actor,
        occurred_at=occurred_at,
        term=term,
        spelling=spelling,
        note=note,
    )


# ----------------------------------------------------------------------------- a laboratory's packs


def _log_pack(project: Path, act: str, pack: Any, actor: str) -> None:
    from ingence.record.log import now

    # The project's own workspace, nothing on the way a link, and never a link at the file itself.
    path = owned_path(project, PACK_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "act": act,
        "pack": pack.name,
        "version": pack.version,
        "digest": pack.digest,
        "actor": actor,
        "at": now(),
    }
    with open_appending(path) as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def install_from_text(project: Path, filename: str, text: str, *, actor: str) -> dict[str, Any]:
    """Install one pack file as a laboratory pack of the project, as ``teach pack install`` does.

    The file is a section in the shipped format (an export from this page is one), with its
    manifest in a ``pack:`` block where it has one. The function checks the file and refuses it in
    words if it has a newer schema or words that do not match the digest. It wraps the file as a
    pack and installs it (:func:`~ingence.dictionary.laboratory_packs.install_pack_text`). It
    refuses a pack of that name that holds other files, as the command refuses it. A list of
    inactivations in the file is read back and never applied, because a new pack means every
    word is active.
    """
    from ingence.dictionary.laboratory_packs import PackError, install_pack_text

    who = _actor(actor)
    if not _plain_name(filename):
        message = (
            f"{filename!r} is not a plain name: a file's name, with no folder and no dot first"
        )
        raise DictionaryRefusedError(message)
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        message = f"{filename} is not YAML: {error}"
        raise DictionaryRefusedError(message) from error
    raw = str((document or {}).get("section") or "") if isinstance(document, dict) else ""
    if raw and not _plain_name(raw):
        message = f"the section's name {raw!r} is not a plain name: no folder and no dot first"
        raise DictionaryRefusedError(message)
    try:
        pack = install_pack_text(project, filename, text)
    except PackError as error:
        raise DictionaryRefusedError(str(error)) from error
    _log_pack(project, "install", pack, who)
    inactive = document.get("inactive")
    return {
        "installed": pack.name,
        "version": pack.version,
        "senses": len(document["entries"]),
        "inactive_not_applied": len(inactive) if isinstance(inactive, list) else 0,
    }


def _plain_name(name: str) -> bool:
    """A name with no folder in it and no dot first: what a pack or its file may be called."""
    spelled = name.strip()
    return (
        bool(spelled)
        and not any(mark in spelled for mark in ("/", "\\", "\x00"))
        and (not spelled.startswith("."))
    )


def remove(project: Path, name: str, *, actor: str) -> dict[str, Any]:
    """Stop loading a laboratory's pack and keep the project's copy under :data:`REMOVED_PACKS`.

    ``teach pack remove`` stops it loading too; here the files are moved aside rather than
    deleted, and who removed it, and when, is a line of the pack log.
    """
    from ingence.dictionary.laboratory_packs import (
        PackError,
        check_packs_folder,
        installed_packs,
        packs_directory,
        remove_pack,
    )
    from ingence.record.log import now

    who = _actor(actor)
    named = name.strip()
    try:
        check_packs_folder(project)
    except PackError as error:
        raise DictionaryRefusedError(str(error)) from error
    found = {pack.name: pack for pack in installed_packs(project)}
    if named not in found:
        message = f"no pack {named!r} is installed in this project"
        raise DictionaryRefusedError(message)
    # The project's own folder for removed packs, nothing on the way to it a link, before a pack
    # is moved into it.
    base = owned_path(project, REMOVED_PACKS, f"{named}-{now().replace(':', '')}")
    kept, count = base, 1
    # Two removals in one second keep two folders side by side.
    while kept.exists():
        count += 1
        kept = base.with_name(f"{base.name}-{count}")
    kept.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(packs_directory(project) / named), str(owned_path(project, kept)))
    remove_pack(project, named)
    _log_pack(project, "remove", found[named], who)
    return {"removed": named, "kept_in": str(kept.relative_to(project))}


def pack_write(project: Path, body: dict[str, Any]) -> dict[str, Any]:
    """The workspace write at :data:`PACK_ROUTE`: ``{"install": {filename, text}}`` or
    ``{"remove": name}``."""
    actor = str(body.get("actor") or "")
    if isinstance(body.get("install"), dict):
        found = body["install"]
        return install_from_text(
            project,
            str(found.get("filename") or "pack.yaml"),
            str(found.get("text") or ""),
            actor=actor,
        )
    if body.get("remove"):
        return remove(project, str(body["remove"]), actor=actor)
    message = "a pack write installs a file ({install: {filename, text}}) or removes one by name"
    raise DictionaryRefusedError(message)


# ----------------------------------------------------------------------------- reading


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


def _number(query: dict[str, list[str]], name: str, default: int) -> int:
    text = _first(query, name)
    try:
        return max(0, int(text)) if text is not None else default
    except ValueError:
        return default


def read(
    catalog: Catalog, project: Path, parts: list[str], query: dict[str, list[str]]
) -> dict[str, Any]:
    """``GET /api/dictionary[/term|/packs|/export]``."""
    if not parts:
        return listing(
            catalog,
            project,
            text=_first(query, "q"),
            origin=_first(query, "origin"),
            kind=_first(query, "kind"),
            order=_first(query, "order"),
            limit=min(_number(query, "limit", LIMIT), 500),
            offset=_number(query, "offset", 0),
        )
    if parts == ["term"]:
        return term_detail(catalog, project, _first(query, "term") or "")
    if parts == ["packs"]:
        return packs(catalog, project)
    if parts == ["answer"]:
        return {"answer": answer_on(project, _first(query, "spelling") or "")}
    if parts == ["export"]:
        return export(project)
    message = f"nothing at /api/dictionary/{'/'.join(parts)}"
    raise LookupError(message)
