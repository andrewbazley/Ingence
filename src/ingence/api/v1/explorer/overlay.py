"""``GET /api/v1/explorer/overlay``: a person's groups, exclusions and proposed relationships.

The Graph workspace's human layer. ``api/routes/overlay.py`` writes it as annotations in the
Record, and this route reads it from the Record as it stands, beside the run the page reads. There
are four states, and the route never merges them with the engine's own:

- ``recorded``: said and in the Record (a draft not yet saved is the page's, and never here);
- ``projected``: every entry it names is live in the run read, so every mode can draw it;
- ``confirmed``: a later run agrees, by its own evidence, where the run the statement was written
  against did not, and the files were observed again since. Examples are a proposed derivation
  the run asserts, a group whose members the rules read as one experiment unit, and an exclusion
  as below. The words say which run and why. Later is read from the runs' own parentage, never
  from a clock. The run read must descend from the statement's ``against_run_id``. The files count
  as observed again only where a run that observed them (a census walk, a dive or a listing, never
  an inference or a rebind) began after the statement was recorded. That is, the run's Record
  watermark reaches the statement's sequence, or, where it states none, its start stamp is later
  than the server's moment for the statement. An inference over the same census, a run published
  after a restore but begun before the statement, or the person's own teaching reads nothing the
  person had not. Otherwise the agreement's ``standing`` says why it is not a confirmation, and the
  statement stays ``projected``. The reasons are: it agreed when said, it agrees now over what was
  already observed, the run said against can no longer be read, or it is unknown whether this run
  is later because a run between them was removed;
- ``stale``: an entry it names is not in the run read (moved away and not followed, or gone).

An exclusion agrees only where the run repudiates what it names. That means a finding of the rule
that raises a name saying its contents are wrong or not to be used (``repudiated_by_name``, whose
terms are the Rulebook's own parameter, so a person widens or narrows them there). The finding is on
the entry's own folder or a folder above it within its experiment unit (where it is in none, its own
folder only). One exception applies: every occurrence of every word it counted is canceled in that
name by a word the Dictionary's quality kind declares cancels one (``negated_by``: ``not failed``),
which the words then say. An exclusion also agrees on content that the run itself chose not to read
(no bytes, a link with no target). The doubt rules read folder names only, never a file's. A theme
of the quality kind as such never agrees (the marks a person writes for work that came out well,
filesystem noise). The broader doubt rule's words never agree either (``use this one``,
``final final``: a person unsure which version to keep, often the one kept). Neither does a word
from far above, nor a file the dive's budget left unread, of which nothing is known to be wrong.

A statement is a person's, and a run agreeing is the engine's. Neither is written into the other.
A file that moved is read at its new place: a relation's or an exclusion's target through the
Record's own reading (``record/log.at_new_places``), a group's members through the same moves.
With ``?target=`` the response carries that statement's whole history, for the audit trail and
for reverting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import duckdb

from ingence import SCHEMA_VERSION
from ingence.api.routes.overlay import target_parts, topic_members
from ingence.api.v1 import V1Context, route
from ingence.api.v1.explorer.projection import connect as explorer_connection
from ingence.api.v1.explorer.wheel import _rows, _run_and_projection, explorer_projection
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one
from ingence.core.outcomes import ScanOutcome
from ingence.core.schema import RecordEventKind

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.api.v1.explorer.projection import ExplorerProjection
    from ingence.storage.catalog import Catalog

__all__ = ["REPUDIATING_RULES", "OverlayResponse", "overlay"]

#: The rules whose findings say a name repudiates what it names (its contents wrong, not to be
#: used): the one evidence an exclusion agrees with. Not the broader doubt rule, whose terms also
#: record a person unsure which version to keep (``use this one``), often the one kept.
REPUDIATING_RULES: tuple[str, ...] = ("repudiated_by_name",)
#: Content a run chose not to read by its own policy: no bytes, or a link with no target. Not
#: ``deferred``: a budget ran out there, and nothing is known to be wrong with the file.
UNREAD_BY_POLICY = ScanOutcome.SKIPPED_BY_POLICY.value

OverlayState = Literal["recorded", "projected", "confirmed", "stale"]


class OverlayMember(ApiModel):
    id: Identifier
    #: As the run read holds it now; None where the run holds no such live entry.
    name: str | None
    path: str | None
    #: ``experiment``, ``core`` and ``line`` are a topic's members of those kinds; a core is its
    #: words (``id`` its Record target), a line its words and label (``id`` a digest of the
    #: experiments it was added by) and neither is a place, so ``path`` is none.
    kind: Literal["file", "folder", "gone", "experiment", "core", "line"]
    #: As it was when the statement was written.
    name_then: str | None
    path_then: str | None


class Agreement(ApiModel):
    """Whether the run read agrees, by its own evidence, and in words."""

    agrees: bool
    words: str
    #: Whether the run read descends from the run the statement was written against (a later
    #: run in its line, read from the runs' own parentage, never from a clock).
    run_is_newer: bool | None
    #: Whether the same agreement already held at the run the statement was written against ("agreed
    #: when said"). None where that was not asked (the run read is no later run) or that run cannot
    #: be read. Only an agreement that did not hold then is a confirmation.
    held_when_said: bool | None = None
    #: Whether a run that observed the files (a census that walked the tree, or a dive) lies
    #: after the run the statement was written against, up to the run read; None where not asked.
    observed_since: bool | None = None
    #: Why an agreement is not a confirmation, in words; None where it is one or does not agree.
    standing: str | None = None


class HumanStatement(ApiModel):
    #: The Record target: ``overlay:<kind>:...``, the statement's identity.
    target: str
    kind: Literal["group", "exclude", "relate"]
    state: OverlayState
    actor: str
    at: str
    event_id: Identifier
    #: Earlier events on the same target (earlier versions and removals).
    earlier: Count
    members: list[OverlayMember]
    #: A group's (a topic's) name; a relation's kind; an exclusion's reason.
    name: str | None
    relation: str | None
    reason: str | None
    note: str | None
    agreement: Agreement | None


class OverlayEvent(ApiModel):
    event_id: Identifier
    actor: str
    at: str
    sequence: Count
    removed: bool
    #: What the event said, in words.
    said: str
    #: The event's own payload, as JSON text, so a revert can say exactly the same again.
    payload: str


@register("explorer_overlay")
class OverlayResponse(ApiModel):
    """``GET /api/v1/explorer/overlay``: every standing statement, its state, and a history."""

    schema_version: str
    run_id: Identifier
    run_started: str | None
    statements: list[HumanStatement]
    #: Standing statements by state; removed ones are not counted.
    counts: dict[str, Count]
    history: list[OverlayEvent]


def _said(kind: str, payload: Mapping[str, Any]) -> str:
    if payload.get("removed"):
        return {
            "group": "the topic withdrawn",
            "exclude": "set back in",
            "relate": "the relation withdrawn",
        }[kind]
    if kind == "group":
        held = (
            len(payload.get("members") or [])
            + len(payload.get("cores") or [])
            + len(payload.get("lines") or [])
        )
        return f"topic {payload.get('name')!r} of {held}"
    if kind == "exclude":
        return f"set aside: {payload.get('reason')}"
    return f"{str(payload.get('relation') or '').replace('_', ' ')}"


def _entries(
    connection: duckdb.DuckDBPyConnection, projection: ExplorerProjection, ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """The live files and folders among ``ids``, with what the agreement checks read."""
    if not ids:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for row in _rows(
        connection.execute(
            "SELECT entry_id, name, relative_path, 'file' AS kind, unit_id, exam, ancestors "  # noqa: S608
            f"FROM {projection.files} WHERE list_contains(?, entry_id) "
            "UNION ALL SELECT entry_id, name, relative_path, 'folder', "
            "CASE WHEN exp_claim_id IS NOT NULL THEN entry_id END, "
            "CASE WHEN files > 0 AND exam_set_aside = files THEN 'set_aside' END, ancestors "
            f"FROM {projection.folders} WHERE list_contains(?, entry_id)",
            [list(ids), list(ids)],
        )
    ):
        found[str(row["entry_id"])] = row
    # A folder that is no unit itself is in its nearest unit above it.
    above = sorted(
        {
            str(ancestor)
            for row in found.values()
            if row["kind"] == "folder" and not row["unit_id"]
            for ancestor in row["ancestors"] or ()
        }
    )
    if above:
        units = {
            str(entry)
            for (entry,) in connection.execute(
                f"SELECT entry_id FROM {projection.folders} "  # noqa: S608
                "WHERE list_contains(?, entry_id) AND exp_claim_id IS NOT NULL",
                [above],
            ).fetchall()
        }
        for row in found.values():
            if row["kind"] == "folder" and not row["unit_id"]:
                row["unit_id"] = next(
                    (str(a) for a in reversed(row["ancestors"] or ()) if str(a) in units), None
                )
    return found


@dataclass(frozen=True, slots=True)
class _Doubt:
    """A repudiating finding on a set-aside entry or a folder above it."""

    rule: str
    #: The entry it raised, and its name.
    carrier: str
    name: str
    raised: tuple[str, ...]
    #: The word canceling every occurrence of what it counted, where one does (``not``).
    negated_by: str | None


def _speaking_for(row: Mapping[str, Any], entry: str) -> list[str]:
    """The entries whose names speak for a set-aside entry, nearest first: itself and the folders
    above it up to its experiment unit's folder; where it is in no unit, itself and its folder."""
    chain = [entry, *reversed([str(a) for a in row.get("ancestors") or ()])]
    unit = row.get("unit_id")
    if unit and str(unit) in chain:
        return chain[: chain.index(str(unit)) + 1]
    return chain[:2]


@dataclass(frozen=True, slots=True)
class _Reading:
    """What one run says that an agreement reads: live entries, lines, doubt, and policy."""

    live: dict[str, dict[str, Any]]
    asserted: frozenset[tuple[str, str, str]]
    #: For an entry set aside: the nearest repudiating finding on it or a folder above it within
    #: its experiment unit, standing or (failing any) canceled by its name.
    doubted: dict[str, _Doubt]
    #: Entries set aside whose content the run chose not to read (a folder: every file beneath).
    unread: frozenset[str]


def _negations(project: Path) -> frozenset[str]:
    """The words the Dictionary's quality kind declares cancel a word standing after them."""
    from ingence.dictionary.store import DictionaryStore

    return frozenset(
        str(word).casefold()
        for sense in DictionaryStore.for_project(project).senses()
        if sense.theme_kind == "quality"
        for word in (sense.properties.get("negated_by") or ())
    )


def _doubted(
    project: Path,
    catalog: Catalog,
    live: Mapping[str, Mapping[str, Any]],
    excluded: Sequence[str],
) -> dict[str, _Doubt]:
    """Each set-aside entry's nearest repudiating finding among the names that speak for it.

    A finding whose every counted word is negated at every occurrence in its name (``not
    failed``) is no evidence, whatever the rule raised: the rule counts a word, and a person
    negating it said the opposite. It is kept, marked, so the words can say so.
    """
    nearest = {entry: _speaking_for(live[entry], entry) for entry in excluded}
    carriers = sorted({carrier for chain in nearest.values() for carrier in chain})
    if not carriers:
        return {}
    findings = catalog.query(
        "SELECT regexp_replace(subject, '^folder:', '') AS carrier, rule_id, raised, inputs "
        "FROM findings WHERE list_contains(?, rule_id) "
        "AND list_contains(?, regexp_replace(subject, '^folder:', '')) "
        "ORDER BY carrier, rule_id",
        [list(REPUDIATING_RULES), carriers],
    ).fetchall()
    if not findings:
        return {}
    raised_on = sorted({str(row[0]) for row in findings})
    names = dict(
        catalog.query(
            "SELECT entry_id, name FROM entries WHERE list_contains(?, entry_id)", [raised_on]
        ).fetchall()
    )
    inputs = sorted({str(binding) for row in findings for binding in row[3] or ()})
    spelled = (
        dict(
            catalog.query(
                "SELECT b.binding_id, coalesce(c.raw_value, c.normalized_value, b.term, '') "
                "FROM dictionary_bindings b "
                "LEFT JOIN components c ON c.component_id = b.component_id "
                "WHERE list_contains(?, b.binding_id)",
                [inputs],
            ).fetchall()
        )
        if inputs
        else {}
    )
    from ingence.dictionary.quality_marks import negated_throughout

    negations = _negations(project)
    held: dict[str, list[_Doubt]] = {}
    for carrier, rule, raised, bindings in findings:
        name = str(names.get(carrier) or "")
        spellings = [str(spelled.get(str(binding)) or "") for binding in bindings or ()]
        cancelling = [negated_throughout(name, spelling, negations) for spelling in spellings]
        negated = cancelling[0] if spellings and all(cancelling) else None
        held.setdefault(str(carrier), []).append(
            _Doubt(
                rule=str(rule),
                carrier=str(carrier),
                name=name,
                raised=tuple(str(term) for term in raised or ()),
                negated_by=negated,
            )
        )
    found: dict[str, _Doubt] = {}
    for entry, chain in nearest.items():
        on_chain = [doubt for carrier in chain for doubt in held.get(carrier, ())]
        chosen = next((doubt for doubt in on_chain if doubt.negated_by is None), None)
        if chosen is None and on_chain:
            chosen = on_chain[0]
        if chosen is not None:
            found[entry] = chosen
    return found


def _unread(
    catalog: Catalog,
    projection: ExplorerProjection,
    live: Mapping[str, Mapping[str, Any]],
    excluded: Sequence[str],
) -> frozenset[str]:
    """The set-aside entries whose content the run chose not to read by its own policy."""
    files = [entry for entry in excluded if live[entry]["kind"] == "file"]
    folders = [entry for entry in excluded if live[entry]["kind"] == "folder"]
    found: set[str] = set()
    if files:
        found |= {
            str(entry)
            for (entry,) in catalog.query(
                "SELECT entry_id FROM entries WHERE list_contains(?, entry_id) "
                "AND scan_outcome = ?",
                [files, UNREAD_BY_POLICY],
            ).fetchall()
        }
    if folders:
        for folder, files_beneath, unread in catalog.query(
            "SELECT a.folder, count(*), count(*) FILTER (WHERE e.scan_outcome = ?) "  # noqa: S608
            f"FROM (SELECT entry_id, unnest(ancestors) AS folder FROM {projection.files}) a "
            "JOIN entries e ON e.entry_id = a.entry_id "
            "WHERE list_contains(?, a.folder) GROUP BY a.folder",
            [UNREAD_BY_POLICY, folders],
        ).fetchall():
            if files_beneath and unread == files_beneath:
                found.add(str(folder))
    return frozenset(found)


def _read(
    project: Path,
    run_id: str,
    projection: ExplorerProjection,
    ids: Sequence[str],
    excluded: Sequence[str],
    pairs: Sequence[tuple[str, str]],
) -> _Reading:
    """What one run says of the entries the standing statements name."""
    from ingence.storage.catalog import open_catalog

    connection = explorer_connection()
    try:
        live = _entries(connection, projection, ids)
    finally:
        connection.close()
    held = [entry for entry in excluded if entry in live]
    asserted: set[tuple[str, str, str]] = set()
    with open_catalog(project, run_id=run_id) as catalog:
        if pairs:
            for source, target, primitive in catalog.query(
                "SELECT source_id, target_id, primitive FROM assertions "
                "WHERE decision_status = 'asserted' AND primitive IN ('derives', 'references') "
                "AND list_contains(?, source_id)",
                [sorted({source for source, _ in pairs})],
            ).fetchall():
                asserted.add((str(source), str(target), str(primitive)))
        doubted = _doubted(project, catalog, live, held) if held else {}
        unread = _unread(catalog, projection, live, held) if held else frozenset()
    return _Reading(live=live, asserted=frozenset(asserted), doubted=doubted, unread=unread)


def _observed(record: Mapping[str, Any]) -> bool:
    """Whether a run observed the files: a census walk (it names no parent, whether or not it
    read names), a dive (it states a budget granted or spent: an unbounded dive grants none), or
    a listing fetched from an archive (a ``remote`` run stating no Rulebook). Never a rebind, and
    never an inference, which reads what they observed (and copies its parent's profile)."""
    profile = str(record.get("profile") or "")
    if profile == "rebind":
        return False
    dived = (
        record.get("deepening_budget_granted_seconds") is not None
        or record.get("deepening_budget_spent_seconds") is not None
    )
    listed = profile == "remote" and record.get("rulebook_hash") is None
    return dived or listed or not record.get("parent_run_id")


_STAMP = re.compile(r"^run_([0-9]{8}T[0-9]{6}Z)_")
_MOMENT = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")


def _stamp(run_id: str) -> str | None:
    """The start stamp a run identifier leads with, or None where it leads with none."""
    found = _STAMP.match(run_id)
    return found.group(1) if found else None


def _moment_stamp(moment: str) -> str | None:
    """A moment the engine wrote (``2026-01-01T00:00:01Z``) as a run identifier's stamp is
    written (``core.ids.run_id``: its letters and digits), or None where it is no such moment."""
    stamp = "".join(character for character in moment if character.isalnum())
    return stamp if _MOMENT.match(stamp) else None


@dataclass(frozen=True, slots=True)
class _Line:
    """The run read and the runs it descends from, by each run record's own parent."""

    #: Nearest first, the run read first: each run, whether it observed the files (None for a
    #: run named as a parent whose directory is gone), and its Record watermark.
    runs: tuple[tuple[str, bool | None, int | None], ...]
    #: Whether the walk stopped at a removed run, so what lies beyond it is unknown.
    broken: bool

    def after(
        self, said_against: str, *, sequence: int, said_at: str
    ) -> tuple[bool | None, bool | None]:
        """Whether the run read is later than ``said_against`` (None: unknown), and whether a run
        begun after the statement observed the files.

        The run read is later where ``said_against`` is in the line. It is also later where the
        line begins at a census walk (which names no parent: it observes afresh) that began after
        ``said_against``. The walk's start is read from the identifiers, which lead with the
        engine's own start stamp so that runs sort in time (``core.ids.run_id``), never a moment a
        client sent. The answer is unknown where the line broke at a removed run before reaching
        either. It is never unknown for the run read itself.

        A run counts as observed since only if it began after the statement was recorded. That means
        its Record watermark (the highest sequence it read) reaches the statement's, or, where it
        states none, its start stamp is later than the moment the server gave the statement. A
        census begun before the statement and published after it observed nothing the person had not
        seen, and does not count.
        """
        ids = [run for run, _, _ in self.runs]
        if said_against == ids[0]:
            return False, None
        if said_against in ids[1:]:
            since = self.runs[: ids.index(said_against)]
        elif self.broken:
            return None, None
        else:
            began, said = _stamp(ids[-1]), _stamp(said_against)
            if began is None or said is None:
                return None, None
            if began <= said:
                return False, None
            since = self.runs
        spoken = _moment_stamp(said_at)
        return True, any(
            bool(observed) and _began_after(run, watermark, sequence, spoken)
            for run, observed, watermark in since
        )


def _began_after(run: str, watermark: int | None, sequence: int, spoken: str | None) -> bool:
    """Whether a run began after a statement: by its Record watermark, else by its start stamp;
    False where neither can say (never counted as new evidence on a guess)."""
    if watermark is not None:
        return watermark >= sequence
    began = _stamp(run)
    return began is not None and spoken is not None and began > spoken


def _lineage(project: Path, run_id: str) -> _Line:
    """Walk the run read's parents. A removed run is named by its child and read no further:
    whether a statement said against a run beyond it is older is then unknown, never "no"."""
    import pyarrow.parquet as pq

    from ingence.storage.layout import RUNS_DIR

    runs: list[tuple[str, bool | None, int | None]] = []
    seen: set[str] = set()
    current = run_id
    while current and current not in seen:
        seen.add(current)
        files = sorted((project / RUNS_DIR / current / "run").glob("*.parquet"))
        rows = pq.read_table(files[0]).to_pylist() if files else []
        if not rows:
            runs.append((current, None, None))
            return _Line(runs=tuple(runs), broken=True)
        watermark = rows[0].get("record_watermark")
        runs.append((current, _observed(rows[0]), None if watermark is None else int(watermark)))
        current = str(rows[0].get("parent_run_id") or "")
    return _Line(runs=tuple(runs), broken=False)


def overlay(context: V1Context, query: Mapping[str, Sequence[str]]) -> OverlayResponse:
    from ingence.record.log import moved_entries, read_events
    from ingence.storage.catalog import open_catalog

    run_id, _, projection, _ = _run_and_projection(context, query)
    wanted = (one(query, "target") or "").strip()
    moved = moved_entries(context.project)
    latest: dict[str, tuple[Any, dict[str, Any], int]] = {}
    history: list[OverlayEvent] = []
    for event in read_events(context.project):
        parsed = target_parts(event.target)
        if parsed is None or event.kind is not RecordEventKind.ANNOTATE:
            continue
        payload = json.loads(event.payload or "{}")
        if parsed[0] == "group":
            for key in ("members", "experiments"):
                if payload.get(key):
                    payload[key] = [moved.get(str(member), str(member)) for member in payload[key]]
        earlier = latest[event.target][2] + 1 if event.target in latest else 0
        latest[event.target] = (event, payload, earlier)
        if wanted and event.target == wanted:
            history.append(
                OverlayEvent(
                    event_id=event.event_id,
                    actor=event.actor,
                    at=event.occurred_at,
                    sequence=event.sequence,
                    removed=bool(payload.get("removed")),
                    said=_said(parsed[0], payload),
                    payload=json.dumps(
                        {k: v for k, v in payload.items() if k not in ("written_at",)},
                        sort_keys=True,
                    ),
                )
            )
    standing = [(target, *held) for target, held in latest.items() if not held[1].get("removed")]
    ids = sorted(
        {
            str(member)
            for target, _, payload, _ in standing
            for member in (
                payload.get("members")
                or [
                    part
                    for part in (target_parts(target) or ("", []))[1]
                    if part.startswith("entry_")
                ]
            )
        }
    )
    with open_catalog(context.project, run_id=run_id) as catalog:
        started = catalog.query("SELECT started_at FROM run LIMIT 1").fetchone()
        run_started = str(started[0]) if started and started[0] else None
    relations = [
        (payload.get("source"), payload.get("target"), payload.get("relation"))
        for target, _, payload, _ in standing
        if target.startswith("overlay:relate:")
    ]
    pairs = [(s, t) for s, t, r in relations if r in ("derives", "references") and s and t]
    excluded = sorted(
        {
            (target_parts(target) or ("", [""]))[1][0]
            for target, _, _, _ in standing
            if target.startswith("overlay:exclude:")
        }
    )
    reading = _read(context.project, run_id, projection, ids, excluded, pairs)
    live = reading.live
    lineage = _lineage(context.project, run_id)
    # The runs each agreeing statement was written against, read once each where the run read
    # descends from it: whether the agreement held then decides agrees from confirms.
    earlier_readings: dict[str, _Reading | None] = {}
    statements: list[HumanStatement] = []
    for target, event, payload, earlier in standing:
        kind, parts = target_parts(target) or ("", [])
        names = payload.get("names") or {}
        paths = payload.get("paths") or {}
        experiments = {str(member) for member in payload.get("experiments") or []}
        member_ids = (
            [str(member) for member in payload.get("members") or []]
            if kind == "group"
            else [parts[0]]
            if kind == "exclude"
            else [parts[0], parts[1]]
        )
        members = []
        for member in member_ids:
            row = live.get(member)
            # A moved member keeps the words it was written with, read at its first place.
            then = next(
                (key for key, value in moved.items() if value == member and key in names),
                member,
            )
            members.append(
                OverlayMember(
                    id=member,
                    name=str(row["name"]) if row else None,
                    path=str(row["relative_path"] or "") if row else None,
                    kind=("experiment" if member in experiments else row["kind"])
                    if row
                    else "gone",
                    name_then=names.get(member) or names.get(then),
                    path_then=paths.get(member) or paths.get(then),
                )
            )
        stale = any(member.kind == "gone" for member in members)
        # Cores and lines are not places a run holds or lacks: they are listed, never stale.
        for typed in topic_members(payload, moved) if kind == "group" else ():
            if typed.kind in ("core", "line"):
                members.append(
                    OverlayMember(
                        id=typed.key,
                        name=" ".join(typed.terms)
                        if typed.kind == "core"
                        else typed.label or " \u00b7 ".join(typed.words) or None,
                        path=None,
                        kind=typed.kind,
                        name_then=None,
                        path_then=None,
                    )
                )
        # A topic over experiments, cores or lines spans experiment units by its nature: the
        # rules have nothing to agree with (an old group of files is read as it always was).
        agreement = (
            None
            if stale
            or (kind == "group" and (experiments or payload.get("cores") or payload.get("lines")))
            else _agreement(kind, payload, member_ids, reading)
        )
        said_against = str(event.against_run_id or "")
        newer, observed = lineage.after(
            said_against, sequence=int(event.sequence), said_at=str(event.occurred_at)
        )
        held_then: bool | None = None
        standing_words: str | None = None
        if agreement is not None and agreement.agrees:
            if newer is None:
                standing_words = (
                    "whether this run is later than the one it was said against is unknown: a "
                    "run between them was removed"
                )
            elif not newer:
                standing_words = "this is the run it was said against, or not a later one"
            else:
                if said_against not in earlier_readings:
                    earlier_readings[said_against] = _read_earlier(
                        context.project, said_against, ids, excluded, pairs
                    )
                then_reading = earlier_readings[said_against]
                if then_reading is None:
                    standing_words = "the run it was said against can no longer be read"
                elif not all(member in then_reading.live for member in member_ids):
                    standing_words = (
                        "an item it names was not in the run it was said against (moved or "
                        "added since)"
                    )
                else:
                    agreed_then = _agreement(kind, payload, member_ids, then_reading)
                    held_then = agreed_then is not None and agreed_then.agrees
                    standing_words = (
                        "agreed when said"
                        if held_then
                        else "agrees now, over what was already observed: no run begun since it "
                        "was said observed the files again"
                        if not observed
                        else None
                    )
        confirmed = bool(
            agreement is not None and agreement.agrees and newer and held_then is False and observed
        )
        if agreement is not None:
            agreement = agreement.model_copy(
                update={
                    "run_is_newer": newer,
                    "held_when_said": held_then,
                    "observed_since": observed if agreement.agrees and newer else None,
                    "standing": standing_words,
                }
            )
        state: OverlayState = "stale" if stale else "confirmed" if confirmed else "projected"
        statements.append(
            HumanStatement(
                target=target,
                kind=kind,
                state=state,
                actor=event.actor,
                at=event.occurred_at,
                event_id=event.event_id,
                earlier=earlier,
                members=members,
                name=payload.get("name"),
                relation=payload.get("relation"),
                reason=payload.get("reason"),
                note=payload.get("note"),
                agreement=agreement,
            )
        )
    # Every standing statement is recorded; those every mode can draw are projected, a confirmed
    # one among them; a stale one names an entry the run read does not hold.
    counts = {
        "recorded": len(statements),
        "projected": sum(1 for item in statements if item.state in ("projected", "confirmed")),
        "confirmed": sum(1 for item in statements if item.state == "confirmed"),
        "stale": sum(1 for item in statements if item.state == "stale"),
    }
    return OverlayResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        run_started=run_started,
        statements=statements,
        counts=counts,
        history=history,
    )


def _read_earlier(
    project: Path,
    run_id: str,
    ids: Sequence[str],
    excluded: Sequence[str],
    pairs: Sequence[tuple[str, str]],
) -> _Reading | None:
    """What a run a statement was written against says, or None where it can no longer be read
    (removed on a person's word): then nothing is said to be newly confirmed."""
    from ingence.storage.commit import run_path

    if not run_path(project, run_id).is_dir():
        return None
    try:
        projection, _ = explorer_projection(project, run_id)
        return _read(project, run_id, projection, ids, excluded, pairs)
    except (LookupError, OSError, duckdb.Error):
        return None


def _agreement(
    kind: str, payload: Mapping[str, Any], members: Sequence[str], reading: _Reading
) -> Agreement | None:
    """What a run says of a statement, by its own evidence; None where it cannot say."""
    live = reading.live
    if kind == "relate":
        relation = str(payload.get("relation") or "")
        source, target = members[0], members[1]
        if relation in ("derives", "references"):
            agrees = (source, target, relation) in reading.asserted
            return Agreement(
                agrees=agrees,
                words=(
                    f"the run asserts this {relation} line"
                    if agrees
                    else f"the run asserts no {relation} line from the one to the other"
                ),
                run_is_newer=None,
            )
        if relation == "same_experiment":
            units = {live[member].get("unit_id") for member in members}
            agrees = len(units) == 1 and None not in units
            return Agreement(
                agrees=agrees,
                words=(
                    "the rules read both as in one experiment unit"
                    if agrees
                    else "the rules do not read both as in one experiment unit"
                ),
                run_is_newer=None,
            )
        return None
    if kind == "group":
        if len(members) < 2:
            # A group of one says nothing the run could agree or disagree with.
            return None
        units = {live[member].get("unit_id") for member in members}
        agrees = len(units) == 1 and None not in units
        return Agreement(
            agrees=agrees,
            words=(
                "the rules read every member as in one experiment unit"
                if agrees
                else "the rules do not read the members as one experiment unit"
            ),
            run_is_newer=None,
        )
    if kind == "exclude":
        member = members[0]
        doubt = reading.doubted.get(member)
        if doubt is not None and doubt.negated_by is None:
            where = "it" if doubt.carrier == member else f"the folder {doubt.name!r} above it"
            return Agreement(
                agrees=True,
                words=f"the run's rule {doubt.rule} repudiates {where}, for "
                f"{', '.join(doubt.raised)}",
                run_is_newer=None,
            )
        if member in reading.unread:
            return Agreement(
                agrees=True,
                words=(
                    "the run chose not to read its content (no bytes to read, or a link with no "
                    "target)"
                ),
                run_is_newer=None,
            )
        if doubt is not None:
            where = "it" if doubt.carrier == member else f"the folder {doubt.name!r} above it"
            return Agreement(
                agrees=False,
                words=f"the run's rule {doubt.rule} raised {where} for "
                f"{', '.join(doubt.raised)}, but its name negates it everywhere it is written "
                f"({doubt.negated_by!r} before it)",
                run_is_newer=None,
            )
        return Agreement(
            agrees=False,
            words=(
                "no rule of the run repudiates it or a folder above it within its experiment "
                "(the rules read folder names, not file names; a folder further above its "
                "experiment is not read for it)"
            ),
            run_is_newer=None,
        )
    return None


@route("explorer/overlay", model=OverlayResponse)
def overlay_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> OverlayResponse:
    return overlay(context, query)
