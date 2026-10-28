"""What the Dashboard asks the engine for, as plain data.

Every function here takes an open :class:`Catalog` and returns JSON-serialisable values. Nothing
here interprets. A view renders what the Index and the claims already say, and computes no
conclusion of its own.

The :func:`folder` view answers one question, and its shape follows from that question. On real
laboratory folders, many files were placed at the acquisition stage and almost none of them share a
name identity with the file processed from them, so nothing pairs them. A person has to look at
whether they should be paired by their folder, by an identity that leaves numbers out, or by
something else. :func:`folder` is the view that shows it: every entry of one folder beside the class
key each rule computed for it, so two files in different classes show which component separated
them.

Every query is bounded. A view fetches a folder, never a project.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ingence.storage.own_files import filesystem_entry

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ingence.storage.catalog import Catalog

__all__ = [
    "candidates",
    "entry",
    "folder",
    "inventory",
    "project",
    "set_aside",
]

#: How many rows any one view will return. A hairball is an admission that the
#: summarisation failed, and an unbounded query is how one arrives.
LIMIT = 500

#: The filesystem's entries, never a part recovered from inside a file. The inventory and the
#: project's count of entries read these, so a workbook is one file of its folder and its charts
#: and properties are not more (``storage/own_files.py``).
_OWN_ENTRIES = f"(SELECT * FROM entries WHERE {filesystem_entry()})"

#: The grouping rules whose class key a folder view shows beside each entry. Read from the
#: claims rather than declared, so a project's own revisions appear without editing this.
_GROUPING = "groups."

#: ``constraint`` is a reserved word in DuckDB's parser, so a binding's marker is quoted in
#: every query and handed on under a name a view can read. The marker says which slot of the
#: name licensed the lookup's restriction. That separates an extension's binding from a name
#: token's, and a person needs it to read a format claim.


def _rows(catalog: Catalog, sql: str, parameters: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = catalog.query(sql, parameters or [])
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _loads(value: object) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def project(catalog: Catalog) -> dict[str, Any]:
    """What this project is, how much of it there is, and which run is being read."""
    counts = {
        # The only value interpolated is a literal from the tuple beside it. Every value a
        # request can influence is bound as a parameter, here and in every query below.
        name: _rows(catalog, f"SELECT count(*) AS n FROM {table}")[0]["n"]
        for name, table in (
            ("entries", _OWN_ENTRIES),
            ("claims", "claims"),
            ("candidates", "candidates"),
            ("findings", "findings"),
            ("rule_failures", "rule_failures"),
        )
    }
    roots = _rows(catalog, "SELECT root_id, root_uri FROM roots ORDER BY root_id")
    rules = _rows(
        catalog,
        """
        SELECT rule_id, rule_version, count(*) AS claims
        FROM claims GROUP BY rule_id, rule_version ORDER BY claims DESC, rule_id, rule_version
        """,
    )
    return {"counts": counts, "roots": roots, "rules": rules, "run": _run_named(catalog)}


def _run_named(catalog: Catalog) -> dict[str, Any] | None:
    """The run these pages read, named as a person reads it ("Run 8, September 24, ...")."""
    from ingence.api.run_names import project_run_names

    if catalog.run_directory is None:
        return None
    return project_run_names(catalog.project).get(catalog.run_directory.name)


def inventory(catalog: Catalog, *, limit: int = 20) -> dict[str, Any]:
    """What is here, and what is unexplained. This is the landing screen.

    The second half is not an appendix. A tool that displays only what it knows feels authoritative
    and is untrustworthy. A tool that shows the boundary of its knowledge is the opposite, and that
    is the honest expression of the abstention principle. On real laboratory folders the unexplained
    half is the larger one, with more bindings that read no evidence than resolved ones. A landing
    screen that showed only the resolved count would lie by omission.

    The three absence states stay apart. ``unexamined``, ``examined_no_evidence`` and
    ``examined_conflicting`` mean different things, and a coverage figure computed over their
    union is not a coverage figure. Nothing here sums them.

    The view is available the instant the census finishes. Every figure but the last two comes
    from the Index alone, and the inference sections are empty and not absent before a rule has
    run.
    """
    return {
        "here": {
            "by_kind": _tally(catalog, _OWN_ENTRIES, "entry_type"),
            "by_extension": _tally(
                catalog, _OWN_ENTRIES, "extension", limit=limit, skip_blank=True
            ),
            "by_depth": _rows(
                catalog,
                f"SELECT depth AS value, count(*) AS n FROM {_OWN_ENTRIES} "
                "GROUP BY depth ORDER BY depth",
            ),
            "by_scan_outcome": _tally(catalog, _OWN_ENTRIES, "scan_outcome"),
            "bytes": _rows(
                catalog,
                f"SELECT sum(size_bytes) AS total, max(size_bytes) AS largest FROM {_OWN_ENTRIES}",
            )[0],
        },
        "unexplained": {
            "entries": _tally(catalog, _OWN_ENTRIES, "resolution_status"),
            "bindings": _tally(catalog, "dictionary_bindings", "resolution_status"),
            "scan_errors": _tally(catalog, "scan_errors", "failure_class"),
            "rule_failures": _tally(catalog, "rule_failures", "failure_class"),
            "links": _tally(catalog, _OWN_ENTRIES, "symlink_state"),
        },
        "concluded": {
            "claims": _tally(catalog, "claims", "resolution_status"),
            "by_tier": _tally(catalog, "claims", "inference_tier"),
            "candidates": _tally(catalog, "candidates", "decision_tier"),
            "findings": _tally(catalog, "findings", "rule_id", limit=limit),
        },
        "set_aside": set_aside(catalog),
    }


def set_aside(catalog: Catalog) -> dict[str, Any]:
    """What this run left out, and what it set aside, each with the reason and the counts.

    The two are different things, kept apart:

    - Left out, from the run record that the census wrote: names the walk never counted, per
      root, per reason, with the pattern that did it. Nothing was read and nothing is in the
      Index, so this is the only place where the names can be seen at all. This view gives each
      name's reason and the pattern that matched it, so a person can see why it was left out.
    - Set aside, from what a folder rule raised: every folder that a rule read as a program's,
      with how many entries lie beneath the outermost of them. Installed software is a scope,
      never an exclusion, and the person is shown why a region was set aside. Those entries are
      indexed and interpreted. What a rule beneath one says is marked and not hidden.
    """
    excluded: list[dict[str, Any]] = []
    columns = {str(row["column_name"]) for row in _rows(catalog, "DESCRIBE SELECT * FROM run")}
    if "excluded_json" in columns:
        for row in _rows(catalog, "SELECT excluded_json FROM run"):
            excluded.extend(_loads(row["excluded_json"]) or [])
    raised = _rows(
        catalog,
        """
        SELECT f.rule_id, f.raised, f.reason, e.relative_path, e.entry_id
        FROM findings f
        JOIN entries e ON 'folder:' || e.entry_id = f.subject
        WHERE f.subject_kind = 'scope' AND f.rule_id LIKE '%installed_software%'
        ORDER BY e.relative_path
        """,
    )
    folders = [
        {
            "folder": row["entry_id"],
            "path": row["relative_path"],
            "rule_id": row["rule_id"],
            "raised": _loads(row["raised"]),
            "reason": row["reason"],
        }
        for row in raised
    ]
    outermost = _outermost(str(item["path"] or "") for item in folders)
    beneath = 0
    if outermost:
        beneath = _rows(
            catalog,
            f"""
            SELECT count(*) AS n FROM entries e
            WHERE {filesystem_entry("e")} AND EXISTS (
                SELECT 1 FROM (SELECT unnest(?::VARCHAR[]) AS region) p
                WHERE e.relative_path = p.region OR e.relative_path LIKE p.region || '/%'
            )
            """,
            [outermost],
        )[0]["n"]
    return {
        "left_out": excluded,
        "folders": folders,
        "outermost": outermost,
        "entries_beneath": beneath,
    }


def _outermost(paths: Iterable[str]) -> list[str]:
    """Return the paths none of the others contains, so a nested region is counted once.

    A program's tree raises each of its own folders that is program-shaped, so 59 findings can
    describe 3 programs. A count of what was set aside must say 3.
    """
    ordered = sorted(path for path in paths if path)
    return [
        path
        for index, path in enumerate(ordered)
        if not any(path.startswith(f"{other}/") for other in ordered[:index])
    ]


def _tally(
    catalog: Catalog,
    table: str,
    column: str,
    *,
    limit: int | None = None,
    skip_blank: bool = False,
) -> list[dict[str, Any]]:
    """Count one column of one table. The table and column are literals, never a request's."""
    where = f"WHERE {column} <> ''" if skip_blank else ""
    bound = f"LIMIT {int(limit)}" if limit else ""
    try:
        return _rows(
            catalog,
            f"SELECT {column} AS value, count(*) AS n FROM {table} {where} "
            f"GROUP BY {column} ORDER BY n DESC, value {bound}",
        )
    except Exception:
        # A table a run never wrote, or a column an older run's records lack. An empty
        # tally is the truthful answer, and it keeps the landing screen usable on a
        # project that has only been censused.
        return []


def folder(catalog: Catalog, folder_id: str, *, limit: int = LIMIT) -> dict[str, Any]:
    """One folder's entries, each beside its components, its claims and its class keys.

    The class key is the point. A grouping claim stores the key it was placed in, and for a name
    rule that key is the components it kept. Two files that a person would call a pair, but that
    sit in different classes, differ by exactly the components that the two keys do not share.
    Showing both keys says which component did it without anybody guessing.
    """
    parent = _rows(catalog, "SELECT * FROM entries WHERE entry_id = ?", [folder_id])
    children = _rows(
        catalog,
        """
        SELECT entry_id, name, entry_type, extension, size_bytes, relative_path
        FROM entries WHERE parent_entry_id = ? ORDER BY name LIMIT ?
        """,
        [folder_id, limit],
    )
    identifiers = [child["entry_id"] for child in children]
    if not identifiers:
        return {"folder": parent[0] if parent else None, "entries": []}

    # One "?" per entry, and the identifiers themselves are bound rather than interpolated.
    # Every `f"""` below interpolates this run of placeholders and nothing else (S608).
    placeholders = ", ".join("?" for _ in identifiers)
    claims = _rows(
        catalog,
        f"""
        SELECT subject, rule_id, rule_version, predicate, object_reference,
               inference_tier, resolution_status, inputs
        FROM claims WHERE subject IN ({placeholders})
        """,
        list(identifiers),
    )
    components = _rows(
        catalog,
        f"""
        SELECT entry_id, component_type, normalized_value, occurrences
        FROM components WHERE entry_id IN ({placeholders}) ORDER BY entry_id, component_type
        """,
        list(identifiers),
    )
    bindings = _rows(
        catalog,
        f"""
        SELECT entry_id, component_id, term, theme_kind, quality,
               "constraint" AS slot_constraint, resolution_status, alternative_count
        FROM dictionary_bindings WHERE entry_id IN ({placeholders})
        """,
        list(identifiers),
    )

    by_entry: dict[str, dict[str, Any]] = {
        child["entry_id"]: {**child, "claims": [], "classes": {}, "components": [], "bindings": []}
        for child in children
    }
    for claim in claims:
        record = by_entry.get(str(claim["subject"]))
        if record is None:
            continue
        if str(claim["predicate"]).startswith(_GROUPING):
            record["classes"][claim["rule_id"]] = {
                "key": _loads(claim["object_reference"]),
                "status": claim["resolution_status"],
            }
        else:
            record["claims"].append({**claim, "inputs": _loads(claim["inputs"])})
    for component in components:
        record = by_entry.get(str(component["entry_id"]))
        if record is not None:
            record["components"].append(component)
    for binding in bindings:
        record = by_entry.get(str(binding["entry_id"]))
        if record is not None:
            record["bindings"].append(binding)

    return {
        "folder": parent[0] if parent else None,
        "entries": list(by_entry.values()),
        "set_aside_by": _set_aside_here(catalog, folder_id, parent[0] if parent else None),
    }


def _set_aside_here(
    catalog: Catalog, folder_id: str, parent: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """What a rule said about this folder as a region, and about the folders above it.

    Installed software is a scope, and the person is shown why a region was set aside. So a
    folder view says it in the one place a person is looking at that folder, and not only in a
    queue. It names the folder that said it, which may be an ancestor: a program's headers sit
    three folders below the layout that gave it away.
    """
    path = str(parent.get("relative_path") or "") if parent else ""
    raised = _rows(
        catalog,
        """
        SELECT f.rule_id, f.reason, e.relative_path
        FROM findings f JOIN entries e ON 'folder:' || e.entry_id = f.subject
        WHERE f.subject_kind = 'scope' AND f.rule_id LIKE '%installed_software%'
        """,
    )
    found = []
    for row in raised:
        above = str(row["relative_path"] or "")
        if above and (above == path or path.startswith(f"{above}/")):
            found.append(
                {
                    "rule_id": row["rule_id"],
                    "reason": row["reason"],
                    "path": above,
                    "itself": above == path,
                }
            )
    del folder_id
    return sorted(found, key=lambda item: str(item["path"]))


def entry(catalog: Catalog, entry_id: str) -> dict[str, Any]:
    """One entry, and every rule that said anything about it.

    This is ``explain-edge`` given a face: a claim, the rule that licensed it, that rule's version
    and tier, and the records it consumed. Where a rule could not decide, its failure appears
    beside the claims, because the shape of what the engine does not know is part of what it is
    honest to show.
    """
    rows = _rows(catalog, "SELECT * FROM entries WHERE entry_id = ?", [entry_id])
    if not rows:
        return {"entry": None}
    return {
        "entry": rows[0],
        "claims": [
            {**claim, "inputs": _loads(claim["inputs"])}
            for claim in _rows(
                catalog,
                """
                SELECT rule_id, rule_version, predicate, object_reference, inference_tier,
                       resolution_status, inputs, defeaters_json
                FROM claims WHERE subject = ? ORDER BY rule_id
                """,
                [entry_id],
            )
        ],
        "failures": _rows(
            catalog,
            """
            SELECT rule_id, rule_version, failure_class, reason, inputs
            FROM rule_failures WHERE subject = ? ORDER BY rule_id
            """,
            [entry_id],
        ),
        "findings": _rows(
            catalog,
            """
            SELECT rule_id, rule_version, raised, reason, inputs
            FROM findings WHERE subject = ? ORDER BY rule_id
            """,
            [entry_id],
        ),
        "components": _rows(
            catalog,
            """
            SELECT component_id, component_type, normalized_value, raw_value, occurrences
            FROM components WHERE entry_id = ? ORDER BY component_type, normalized_value
            """,
            [entry_id],
        ),
        "bindings": _rows(
            catalog,
            """
            SELECT component_id, term, theme_kind, quality,
                   "constraint" AS slot_constraint, resolution_status, alternative_count
            FROM dictionary_bindings WHERE entry_id = ?
            """,
            [entry_id],
        ),
    }


def candidates(catalog: Catalog, *, limit: int = LIMIT) -> list[dict[str, Any]]:
    """Every pair a proposing rule licensed, with both ends named, and what a verdict reads.

    A candidate is not a claim, and the Dashboard must not let it read as one. It is a pair that a
    rule thought worth a person's attention, at a decision tier, and nothing has decided it.

    A person who judges a pair reads its names, its folder and the two ends' stages. So each end
    carries the stages that its published claims conclude: every resolved classifying claim whose
    value is a sense on the ``stage`` axis in the run's own vocabulary. This names no rule, so it
    reads whatever rules a Rulebook places stages with.
    """
    return _rows(
        catalog,
        """
        WITH stage_senses AS (
            SELECT DISTINCT term FROM dictionary_entries WHERE theme_kind = 'stage'
        ),
        stages AS (
            SELECT c.subject,
                   array_to_string(list_sort(list(DISTINCT c.object_reference)), ' / ') AS stage
            FROM claims c
            WHERE c.predicate LIKE 'classifies.%' AND c.resolution_status = 'resolved'
              AND c.object_reference IN (SELECT term FROM stage_senses)
            GROUP BY c.subject
        )
        SELECT c.candidate_id, c.rule_id, c.rule_version, c.primitive, c.specialization,
               c.decision_tier, c.source_id, c.target_id,
               s.name AS source_name, s.relative_path AS source_path,
               s.parent_entry_id AS source_folder,
               t.name AS target_name, t.relative_path AS target_path,
               t.parent_entry_id AS target_folder,
               f.relative_path AS folder_path,
               ss.stage AS source_stage, ts.stage AS target_stage
        FROM candidates c
        JOIN entries s ON s.entry_id = c.source_id
        JOIN entries t ON t.entry_id = c.target_id
        LEFT JOIN entries f ON f.entry_id = s.parent_entry_id
        LEFT JOIN stages ss ON ss.subject = c.source_id
        LEFT JOIN stages ts ON ts.subject = c.target_id
        ORDER BY c.rule_id, s.name, c.candidate_id
        LIMIT ?
        """,
        [limit],
    )
