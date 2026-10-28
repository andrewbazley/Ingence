"""The chips a result card shows: what the experiment varied, how, how often, and in what state.

A result is a folder with small tags that represent what conditions were involved. Every chip is a
count over what the run already bound, so a chip is always one click from the binding that made it.
A word that the run could not bind is shown as what it is: an unexplained word, in grey, with the
control that teaches it. The card never dresses such a word as a condition.

For one unit (a folder), the tags come from its own name and its members' names:

- title: the folder's name as written, with its date and version lifted out into chips of their
  own. So ``20260923 SubX WT ATP-ADP kelt FRAP pooled good replicate V2`` reads as "SubX WT ATP-ADP
  kelt FRAP pooled good replicate", 23 Sep 2026, v2.
- chips by theme kind: reagent, condition, technique, sample and the rest. Each chip is marked
  ``named`` (in the folder's own name), ``shared`` (in nearly every member's name) or ``varying``
  (in some members). The varying chips are the conditions that the experiment compared. This is the
  reading of ``infer/folder_families.py``, done in SQL here.
- unexplained: recurring words that are bound to nothing, in grey, each with the number of
  files that carry it.
- replicates: indexed labels bound to a replicate sense (``rep1..rep3``), stated counts
  (``N=3``, shown as "stated" and never as a count of files), and the bare word.
- stages: files per workflow stage, from the stage rules' resolved claims.
- file types and time span.
- a quality mark's chip says it is one: a chip whose sense carries a ``mark`` (``good``, or the
  poor mark a person writes when the work looks unwell) holds the mark and what it means in plain
  words. The chip is read-only. It is never a doubt, and the search leaves nothing out and ranks
  nothing down for it.
- products: the final-stage files that a unit holds (figures, plots, presentations, Prism and
  Illustrator documents, pooled results), counted and a few named. An experiment's card then says
  "2 figures, 1 Prism project" without listing its files.
- out of reach: the number of the unit's files that are archive links to another server and the
  number that are empty outputs, from the findings of ``content_unreadable``. A replicate that is an
  empty output is marked as such in the badge, because an empty replicate is not a data point. An
  example is "rep1-rep4 (rep3 empty)".
- time span in days, never seconds: the earliest credible time where the run concluded one, and
  the file's own times otherwise. The span leaves out times outside a plausible span, such as a file
  "from 1969" or a time after the run, and says how many it left out. The span's lower bound is the
  time rule's ``earliest_credible_year``, or ``DEFAULT_PLAUSIBLE_FROM`` where no rule is in force.
  Its upper bound is the run's own start.

The computation is bounded. The search computes tags for the cards on one page (``limit``) and never
for every candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.dictionary.normalize import AtomKind, split_indexed
from ingence.dictionary.quality_marks import cancelled_by
from ingence.search.scale import DEFAULT_SCALE
from ingence.storage.own_files import filesystem_entry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.dictionary.normalize import Normaliser
    from ingence.dictionary.quality_marks import QualityMark
    from ingence.search.projection import Projection
    from ingence.storage.catalog import Catalog

__all__ = [
    "DEFAULT_CHIP_KINDS",
    "DEFAULT_HIDDEN_KINDS",
    "DEFAULT_STAGE_RULES",
    "OWN_CLOCK_SQL",
    "UnitTags",
    "readable_title",
    "tags_for",
]

#: Theme kinds shown as chips, in the order a card shows them. What was varied and applied comes
#: first, how it was measured next, and bookkeeping last. Theme kinds are the Dictionary's axes, so
#: this list is a display preference and a parameter, not vocabulary.
DEFAULT_CHIP_KINDS: tuple[str, ...] = (
    "reagent", "condition", "sample", "subject", "culture", "technique", "instrument",
    "acquisition", "channel", "measurement", "timepoint", "process", "status", "quality",
    "software", "experiment", "session", "dataset",
)  # fmt: skip

#: Theme kinds never shown as a condition chip. Each has a place of its own on the card, or says
#: nothing that a person scanning results needs.
DEFAULT_HIDDEN_KINDS: tuple[str, ...] = (
    "date", "version", "identifier", "unit", "format", "file_role", "replicate", "count",
    "read_structure", "position", "connective", "question",
)  # fmt: skip

#: The rules whose resolved claims name a file's workflow stage. This is a parameter, because the
#: stage rules change with the Rulebook.
DEFAULT_STAGE_RULES: tuple[str, ...] = DEFAULT_SCALE.stage_rules

#: A member word that at least this share of a folder's files carry is ``shared`` and not
#: ``varying``. The value has not been measured.
DEFAULT_SHARED_SHARE = 0.9

#: The rule whose claims hold a file's earliest credible time as ``{"at": ..., "source": ...}``.
DEFAULT_TIME_RULE = "earliest_credible_time"
#: A file's own time, where no rule reads time. It is a clock that the file's content states (an
#: instrument's or a document's), else its modification time. It is never the date that its name
#: spells, because that date describes a neighbourhood and not the file's clock. This is SQL over
#: ``entries e``.
OWN_CLOCK_SQL = (
    "coalesce(CASE WHEN e.event_time_source IN ('instrument', 'document') "
    "THEN e.event_time_ns END, e.modified_ns)"
)

#: The flag rule naming how a file is out of reach (``archive_link``, ``empty_output``, ...).
DEFAULT_UNREADABLE_RULE = "content_unreadable"

#: The rule that carries an approved group of experiments onto every file beneath its members.
DEFAULT_APPROVED_RULE = "experiments_approved_together"

#: Times before this day are not a clock that a person would believe. This is the engine's own
#: first credible year (the ``earliest_credible_year`` of ``earliest_credible_time``). The search
#: reads the run's own rule and passes its year. This value applies where no rule is in force.
DEFAULT_PLAUSIBLE_FROM = "1990-01-01"

#: The number of product files that a card names. The card counts the rest.
PRODUCTS_NAMED = 3


@dataclass
class UnitTags:
    """Everything a result card shows beside its title."""

    folder_id: str
    title: str
    dates: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    chips: list[dict[str, Any]] = field(default_factory=list)
    unexplained: list[dict[str, Any]] = field(default_factory=list)
    replicates: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, int] = field(default_factory=dict)
    file_types: list[dict[str, Any]] = field(default_factory=list)
    span: dict[str, Any] = field(default_factory=dict)
    files: int = 0
    #: Final-stage files beneath the unit: counts by what made each one, and a few by name.
    products: dict[str, int] = field(default_factory=dict)
    products_named: list[dict[str, Any]] = field(default_factory=list)
    #: Files out of reach, by how (``archive_link``, ``empty_output``, ``empty_and_old``).
    unreachable: dict[str, int] = field(default_factory=dict)
    #: The number of groups, approved by a person as belonging together, that hold files beneath
    #: this unit (``experiments_approved_together``). The card shows what a person said and infers
    #: nothing here.
    approved_groups: int = 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "folder_id": self.folder_id,
            "title": self.title,
            "dates": self.dates,
            "versions": self.versions,
            "chips": self.chips,
            "unexplained": self.unexplained,
            "replicates": self.replicates,
            "replicate_summary": _replicate_summary(self.replicates),
            "stages": self.stages,
            "file_types": self.file_types,
            "span": self.span,
            "files": self.files,
            "products": self.products,
            "products_named": self.products_named,
            "unreachable": self.unreachable,
            "approved_groups": self.approved_groups,
        }


def readable_title(name: str, normaliser: Normaliser) -> tuple[str, list[str], list[str]]:
    """Return a folder name with its dates and versions lifted out, and those lifted.

    The function cuts by the atoms' own positions, so everything else stays exactly as the person
    wrote it, with capitals and hyphens. Where nothing would be left, it returns the name whole.
    """
    atoms = normaliser.atoms(name)
    dates = [atom.normalised for atom in atoms if atom.kind is AtomKind.DATE]
    versions = [atom.text for atom in atoms if atom.kind is AtomKind.VERSION]
    kept = list(name)
    for atom in atoms:
        if atom.kind in (AtomKind.DATE, AtomKind.VERSION):
            for position in range(atom.start, atom.start + len(atom.text)):
                if 0 <= position < len(kept):
                    kept[position] = " "
    title = " ".join("".join(kept).replace("_", " ").split()).strip(" -")
    return (title or name), dates, versions


def _rows(catalog: Catalog, sql: str, parameters: Sequence[Any]) -> list[dict[str, Any]]:
    cursor = catalog.query(sql, list(parameters))
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _replicate_summary(replicates: dict[str, Any]) -> str:
    """Return plain words for the replicate badge: "3 indexed (rep1-rep3)", "N=3 stated".

    A replicate whose files are all empty outputs is named as empty. The badge does not count it
    among the replicates that hold data, because an empty replicate is not a data point.
    """
    parts: list[str] = []
    empty = replicates.get("empty", {})
    for label, indexes in sorted(replicates.get("indexed", {}).items()):
        ordered = sorted(indexes)
        if ordered:
            span = (
                f"{label}{ordered[0]}"
                if len(ordered) == 1
                else f"{label}{ordered[0]}-{label}{ordered[-1]}"
            )
            hollow = sorted(set(empty.get(label, ())) & set(ordered))
            if hollow:
                named = ", ".join(f"{label}{index}" for index in hollow)
                parts.append(f"{len(ordered) - len(hollow)} with data of {len(ordered)} ({span}; "
                             f"{named} empty)")  # fmt: skip
            else:
                parts.append(f"{len(ordered)} indexed ({span})")
    for stated in sorted(set(replicates.get("stated", []))):
        parts.append(f"N={stated} stated in a name")
    if replicates.get("words"):
        parts.append("named as replicates")
    return "; ".join(parts)


def tags_for(
    catalog: Catalog,
    projection: Projection,
    *,
    units: Sequence[str],
    normaliser: Normaliser,
    chip_kinds: Sequence[str] = DEFAULT_CHIP_KINDS,
    hidden_kinds: Sequence[str] = DEFAULT_HIDDEN_KINDS,
    stage_rules: Sequence[str] = DEFAULT_STAGE_RULES,
    shared_share: float = DEFAULT_SHARED_SHARE,
    chips_per_kind: int = 4,
    time_rule: str = DEFAULT_TIME_RULE,
    unreadable_rule: str = DEFAULT_UNREADABLE_RULE,
    approved_rule: str = DEFAULT_APPROVED_RULE,
    plausible_from: str = DEFAULT_PLAUSIBLE_FROM,
    plausible_until: str | None = None,
    time_rule_ran: bool = True,
    quality: Mapping[str, QualityMark] | None = None,
) -> dict[str, UnitTags]:
    """Return the tags of each unit, by unit identifier, in seven bounded queries.

    ``plausible_until`` is the last believable day. It is the run's own day, where the caller knows
    it. Without it, only the lower bound applies. ``time_rule_ran`` says that the run holds the time
    rule's claims. A file that the rule set aside then has no time on the card, and the card reads a
    file's own time only where no rule reads time.
    """
    if not units:
        return {}
    marks = ", ".join("?" for _ in units)
    terms = projection.table("terms")
    closure = projection.table("closure")
    folders = projection.table("folders")
    products = projection.table("products")

    facts = {
        row["folder_id"]: row
        for row in _rows(
            catalog,
            f"""
            SELECT folder_id, name, subtree_files FROM {folders} WHERE folder_id IN ({marks})
            UNION ALL
            SELECT entry_id, name, 1 FROM {products} WHERE entry_id IN ({marks})
            """,  # noqa: S608
            [*units, *units],
        )
    }
    found: dict[str, UnitTags] = {}
    for folder_id in units:
        known = facts.get(folder_id, {})
        title, dates, versions = readable_title(str(known.get("name") or ""), normaliser)
        found[folder_id] = UnitTags(
            folder_id=folder_id,
            title=title,
            dates=dates,
            versions=versions,
            files=int(known.get("subtree_files") or 0),
        )

    # Words: the folder's own name (distance 0), and its members' names beneath it.
    words = _rows(
        catalog,
        f"""
        WITH placed AS (
            SELECT entry_id AS unit, entry_id, 0 AS distance FROM {folders} f
                JOIN (SELECT DISTINCT entry_id FROM {terms}) t ON t.entry_id = f.folder_id
                WHERE f.folder_id IN ({marks})
            UNION ALL
            SELECT ancestor_id AS unit, entry_id, distance FROM {closure}
                WHERE ancestor_id IN ({marks})
        )
        SELECT p.unit, t.component_type, t.token, min(t.raw_value) AS shown, t.term, t.theme_kind,
               t.resolution_status, bool_or(p.distance = 0) AS named,
               count(DISTINCT CASE WHEN p.distance > 0 THEN t.entry_id END) AS files,
               count(DISTINCT CASE WHEN p.distance > 0 AND t.entry_id IN (
                   SELECT subject FROM findings
                   WHERE rule_id = ? AND list_contains(raised, 'empty_output')
               ) THEN t.entry_id END) AS empty_files
        FROM placed p JOIN {terms} t ON t.entry_id = p.entry_id
        WHERE t.source IN ('name', 'note')
        GROUP BY p.unit, t.component_type, t.token, t.term, t.theme_kind, t.resolution_status
        """,  # noqa: S608
        [*units, *units, unreadable_rule],
    )
    rank_of_kind = {kind: index for index, kind in enumerate(chip_kinds)}
    hidden = frozenset(hidden_kinds)
    for row in words:
        tags = found.get(row["unit"])
        if tags is None:
            continue
        total = max(1, tags.files)
        where = (
            "named"
            if row["named"]
            else ("shared" if row["files"] >= shared_share * total else "varying")
        )
        kind = row["theme_kind"]
        if row["component_type"] == "indexed" and kind in ("replicate", "count"):
            split = split_indexed(row["token"])
            if split is not None:
                label, index = split
                if kind == "count":
                    tags.replicates.setdefault("stated", []).append(index)
                else:
                    tags.replicates.setdefault("indexed", {}).setdefault(label, set()).add(index)
                    if row.get("files") and row.get("empty_files") == row["files"]:
                        tags.replicates.setdefault("empty", {}).setdefault(label, set()).add(index)
            continue
        if kind == "replicate":
            tags.replicates["words"] = True
            continue
        if row["resolution_status"] == "resolved" and kind and kind not in hidden:
            chip: dict[str, Any] = {
                "label": row["shown"],
                "term": row["term"],
                "kind": kind,
                "where": where,
                "files": row["files"],
                "order": rank_of_kind.get(kind, len(rank_of_kind)),
            }
            held = (quality or {}).get(str(row["term"]))
            if held is not None:
                chip.update(mark=held.mark, said=held.said)
                if where == "named":
                    # Only the unit's own name is read for a word that cancels the mark ("not
                    # sick").
                    chip["cancelled_by"] = cancelled_by(
                        str(facts.get(row["unit"], {}).get("name") or ""),
                        str(row["shown"]),
                        held.negated_by,
                    )
            tags.chips.append(chip)
        elif (
            row["component_type"] == "word"
            and row["resolution_status"] == "examined_no_evidence"
            and (row["named"] or row["files"] >= 2)
        ):
            tags.unexplained.append(
                {"word": row["shown"], "token": row["token"], "where": where, "files": row["files"]}
            )

    for tags in found.values():
        tags.chips.sort(
            key=lambda chip: (
                chip["order"],
                chip["where"] != "named",
                -chip["files"],
                chip["label"],
            )
        )
        kept: list[dict[str, Any]] = []
        per_kind: dict[str, int] = {}
        for chip in tags.chips:
            if per_kind.get(chip["kind"], 0) < chips_per_kind or chip["where"] == "named":
                kept.append(chip)
                per_kind[chip["kind"]] = per_kind.get(chip["kind"], 0) + 1
        tags.chips = kept
        tags.unexplained.sort(
            key=lambda word: (word["where"] != "named", -word["files"], word["word"])
        )
        tags.unexplained = tags.unexplained[:6]
        for part in ("indexed", "empty"):
            if part in tags.replicates:
                tags.replicates[part] = {
                    label: sorted(indexes) for label, indexes in tags.replicates[part].items()
                }

    rule_marks = ", ".join("?" for _ in stage_rules)
    if stage_rules:
        for row in _rows(
            catalog,
            f"""
            SELECT c.ancestor_id AS unit, cl.object_reference AS stage,
                   count(DISTINCT cl.subject) AS files
            FROM {closure} c JOIN claims cl ON cl.subject = c.entry_id
            JOIN entries e ON e.entry_id = c.entry_id AND {filesystem_entry("e")}
            WHERE c.ancestor_id IN ({marks}) AND cl.rule_id IN ({rule_marks})
              AND cl.resolution_status = 'resolved'
            GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
            """,  # noqa: S608
            [*units, *stage_rules],
        ):
            if row["unit"] in found and row["stage"]:
                found[row["unit"]].stages[str(row["stage"])] = int(row["files"])

    # File types, per unit.
    for row in _rows(
        catalog,
        f"""
        SELECT c.ancestor_id AS unit, e.extension, count(*) AS files
        FROM {closure} c JOIN entries e ON e.entry_id = c.entry_id
        WHERE c.ancestor_id IN ({marks}) AND e.entry_type = 'file'
          AND {filesystem_entry("e")}
        GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
        """,  # noqa: S608
        units,
    ):
        tags = found.get(row["unit"])
        if tags is not None and len(tags.file_types) < 6:
            tags.file_types.append(
                {"extension": row["extension"] or "(none)", "files": row["files"]}
            )

    # The time span, in days. It uses the earliest credible time where the run concluded one, the
    # file's own times otherwise, and nothing outside the plausible span.
    bounds = [plausible_from, plausible_until or "9999-12-31"]
    for row in _rows(
        catalog,
        f"""
        WITH reach AS (
            SELECT ancestor_id AS unit, entry_id FROM {closure} WHERE ancestor_id IN ({marks})
            UNION ALL
            SELECT entry_id, entry_id FROM {products} WHERE entry_id IN ({marks})
        ),
        credible AS (
            SELECT cl.subject AS entry_id,
                   TRY_CAST(json_extract_string(cl.object_reference, '$.at') AS TIMESTAMP) AS seen
            FROM claims cl
            WHERE cl.rule_id = ? AND cl.resolution_status = 'resolved'
        ),
        timed AS (
            SELECT r.unit,
                   coalesce(
                       cr.seen,
                       CASE WHEN NOT ? THEN
                           make_timestamp({OWN_CLOCK_SQL} // 1000)
                       END
                   ) AS seen
            FROM reach r JOIN entries e ON e.entry_id = r.entry_id AND e.entry_type = 'file'
                AND {filesystem_entry("e")}
            LEFT JOIN credible cr ON cr.entry_id = r.entry_id
        )
        SELECT unit,
               min(CAST(seen AS DATE)) FILTER (WHERE seen BETWEEN CAST(? AS TIMESTAMP)
                                                           AND CAST(? AS TIMESTAMP)) AS first_day,
               max(CAST(seen AS DATE)) FILTER (WHERE seen BETWEEN CAST(? AS TIMESTAMP)
                                                           AND CAST(? AS TIMESTAMP)) AS last_day,
               count(*) FILTER (WHERE seen IS NOT NULL AND NOT seen BETWEEN CAST(? AS TIMESTAMP)
                                                                     AND CAST(? AS TIMESTAMP))
                   AS set_aside
        FROM timed GROUP BY unit
        """,  # noqa: S608
        [*units, *units, time_rule, time_rule_ran, *bounds, *bounds, *bounds],
    ):
        tags = found.get(row["unit"])
        if tags is None:
            continue
        first, last = row["first_day"], row["last_day"]
        tags.span = {
            "first_day": str(first) if first is not None else None,
            "last_day": str(last) if last is not None else None,
            "days": (last - first).days + 1 if first is not None and last is not None else None,
            "set_aside": int(row["set_aside"] or 0),
        }

    # Products beneath the unit, counted by what made each one, and a few named.
    for row in _rows(
        catalog,
        f"""
        SELECT p.home_id AS unit, p.made_as, count(*) AS files,
               list(p.name ORDER BY p.size_bytes DESC NULLS LAST, p.name)[1:{PRODUCTS_NAMED}]
                   AS named
        FROM {products} p WHERE p.home_id IN ({marks})
        GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
        """,  # noqa: S608
        units,
    ):
        tags = found.get(row["unit"])
        if tags is None:
            continue
        tags.products[str(row["made_as"])] = int(row["files"])
        for name in row["named"] or []:
            if len(tags.products_named) < PRODUCTS_NAMED:
                tags.products_named.append({"name": name, "made_as": row["made_as"]})

    # Out of reach: archive links and empty outputs beneath the unit.
    for row in _rows(
        catalog,
        f"""
        SELECT c.ancestor_id AS unit, how, count(DISTINCT fi.subject) AS files
        FROM {closure} c
        JOIN (SELECT subject, unnest(raised) AS how FROM findings WHERE rule_id = ?) fi
            ON fi.subject = c.entry_id
        WHERE c.ancestor_id IN ({marks})
        GROUP BY 1, 2 ORDER BY 1, 2
        """,  # noqa: S608
        [unreadable_rule, *units],
    ):
        tags = found.get(row["unit"])
        if tags is not None:
            tags.unreachable[str(row["how"])] = int(row["files"])

    # Groups a person approved as belonging together, with files beneath the unit.
    for row in _rows(
        catalog,
        f"""
        SELECT c.ancestor_id AS unit, count(DISTINCT cl.object_reference) AS groups
        FROM {closure} c JOIN claims cl ON cl.subject = c.entry_id
        WHERE c.ancestor_id IN ({marks}) AND cl.rule_id = ? AND cl.resolution_status = 'resolved'
        GROUP BY 1
        """,  # noqa: S608
        [*units, approved_rule],
    ):
        tags = found.get(row["unit"])
        if tags is not None:
            tags.approved_groups = int(row["groups"])
    return found
