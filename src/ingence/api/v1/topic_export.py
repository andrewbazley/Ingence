"""``GET /api/v1/topics/export``: one topic's files as CSV text a person saves.

A topic's page offers "Export files (CSV)": every file of the topic's experiments, with its
path, the root it lies below, the stage the run's rules concluded for it and the experiment it
belongs to. The route makes the CSV and sends it as text, and the page hands it to the browser as
a download. So nothing is written inside a root or inside the project. The engine writes no export
anywhere, as with the Groupings and History exports.

Which files, and which experiment each belongs to, is ``ingence.topics``'s: an experiment's files
are the files whose highest experiment it is (the rule the Years view and completeness count by),
read in path order. The stage of a file is read as the Experiments list reads it
(``experiment_files.claim_reading``, the one reading of a claim's kind): the value a resolved
claim concluded, a blank cell when none was found (never a statement that the file has none),
and a state column that tells a stage found from one only published as conflicting from one not
found. The CSV's counts of each state are therefore the Experiments list's own, by construction.

The file opens correctly in a spreadsheet. It begins with a UTF-8 byte order mark, so a name with
an accent or a micro sign is read as written. A cell that a spreadsheet would read as a formula is
written as text. The ``root`` column says which root a path is below (a path is relative to its
root, and a project can read several).

The file has a size limit: ``limit`` files (50,000 unless asked for fewer or more, at most a
million), in path order. Where the topic holds more, the file says how many it lists of how many
and the response says it is truncated. So the text that goes back in one response is never
unbounded.

The export makes no claim. The topic is a person's annotation, and inference reads nothing here.
"""

from __future__ import annotations

import csv
import io
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import integer, one, run_named

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.api.v1.experiment_files import ClaimReading
    from ingence.storage.catalog import Catalog

__all__ = [
    "BYTE_ORDER_MARK",
    "COLUMNS",
    "DEFAULT_LIMIT",
    "TopicExportResponse",
    "csv_cell",
    "export_file_name",
]

#: The columns of the CSV, in order.
COLUMNS: tuple[str, ...] = (
    "path",
    "root",
    "name",
    "stage",
    "stage_state",
    "experiment",
    "experiment_id",
    "root_id",
)
#: Files read from the run at a time (a topic can hold many thousands), and ids per claims query.
PAGE = 5000
CLAIM_CHUNK = 4000
#: Files one export lists unless ``limit`` says otherwise, and the most it may be asked for.
DEFAULT_LIMIT = 50_000
LIMIT_MOST = 1_000_000
#: The UTF-8 byte order mark a spreadsheet reads the file's encoding from.
BYTE_ORDER_MARK = "\ufeff"
#: A cell that begins with one of these is read by a spreadsheet as a formula; it is written with
#: a leading apostrophe so that it opens as the text it is.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
#: Characters of the topic's name kept in the file's name.
NAME_KEPT = 40

NOTES: tuple[str, ...] = (
    "A blank stage means the rules found none in what was read, not that the file has none.",
    "stage_state is found (a rule found a stage), conflicting (the results were saved as "
    "conflicting and none settled it) or not found.",
    "A cell that begins with = + - @ is written with a leading apostrophe, so that a "
    "spreadsheet opens it as the text it is and never runs it as a formula. A file name that "
    "begins with one of these characters therefore shows an apostrophe before it.",
    "A path is below the data folder named in the root column; root_id says which data folder "
    "when two data folders share a name.",
    "The file begins with a byte order mark, so a spreadsheet reads names with accents and "
    "symbols as they are written.",
)


@register("topics_export")
class TopicExportResponse(ApiModel):
    """The CSV of one topic's files, as text, with its name and what it holds."""

    api_version: str = "1.0"
    schema_version: str
    run_id: Identifier
    topic: Identifier
    topic_name: str
    #: The file's name for the download, without a date (the page adds it).
    file_name: str
    #: The whole CSV, with its header row.
    text: str
    #: Files listed (rows beneath the header), and the experiments they belong to.
    files: Count
    experiments: Count
    #: Of those files, how many have a stage found.
    with_stage: Count
    #: False where a member of the topic could not be read: the files are then those of the members
    #: that could be, at least.
    complete: bool
    #: True where the file lists only the first ``limit`` files, and how many are not listed.
    truncated: bool
    files_not_listed: Count
    notes: list[str]


def csv_cell(value: str) -> str:
    """A cell that a spreadsheet would read as a formula, written as the text it is."""
    return f"'{value}" if value.startswith(FORMULA_STARTS) else value


def export_file_name(name: str) -> str:
    """The download's name: ``ingence-topic-<the topic's name in plain letters>.csv``."""
    plain = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:NAME_KEPT].strip("-")
    return f"ingence-topic-{plain or 'topic'}.csv"


def _stages(
    catalog: Catalog, ids: Sequence[str], reading: ClaimReading
) -> dict[str, tuple[tuple[str, ...], bool]]:
    """Each file's stage values the run read, and whether they are in conflict only.

    The reading is the Experiments list's own (``experiment_files.claim_reading``, the one reading
    of a claim's kind that both routes use), so a stage here is a stage there by construction. A
    classifying claim is a stage where its value is a sense of the stage kind in the run's
    Dictionary, or, in a run whose Dictionary holds none, where its predicate is spelled as a
    stage's. Only a resolved claim settles a stage. A file with none resolved and a claim published
    as conflicting is in conflict (and its values are those claims'). A file that no stage claim
    touches has no entry (not found).
    """
    from ingence.api.v1.experiment_files import _claims, _like

    by_spelling = "stage" in reading.by_spelling
    terms = reading.terms["stage"]
    pattern = reading.patterns["stage"]
    found: dict[str, tuple[tuple[str, ...], bool]] = {}
    for start in range(0, len(ids), CLAIM_CHUNK):
        for entry, held in _claims(catalog, list(ids[start : start + CLAIM_CHUNK])).items():
            settled: set[str] = set()
            disputed: set[str] = set()
            for claim in held:
                value = claim["object_reference"]
                predicate = str(claim["predicate"])
                if value is None or not predicate.startswith("classifies."):
                    continue
                if not (str(value) in terms or (by_spelling and _like(predicate, pattern))):
                    continue
                if claim["resolution_status"] == "resolved":
                    settled.add(str(value))
                elif claim["resolution_status"] == "examined_conflicting":
                    disputed.add(str(value))
            if settled:
                found[entry] = (tuple(sorted(settled)), False)
            elif disputed:
                found[entry] = (tuple(sorted(disputed)), True)
    return found


def root_name(label: str | None, uri: str) -> str:
    """What a root is called in the file: the label a person gave it, else the last part of its
    address (the archive's identifier for a listing)."""
    if label:
        return label
    from urllib.parse import unquote

    tail = uri.rstrip("/").rsplit(":" if uri.startswith("listing:") else "/", 1)[-1]
    return unquote(tail) or uri


def _roots_of(catalog: Catalog, ids: Sequence[str]) -> dict[str, str]:
    """The root each entry lies below."""
    found: dict[str, str] = {}
    for start in range(0, len(ids), CLAIM_CHUNK):
        chunk = list(ids[start : start + CLAIM_CHUNK])
        for entry, root in catalog.query(
            "SELECT entry_id, root_id FROM entries WHERE list_contains(?::VARCHAR[], entry_id)",
            [chunk],
        ).fetchall():
            found[str(entry)] = str(root)
    return found


def topic_rows(
    project: Any,
    run_id: str,
    catalog: Catalog,
    experiments: Sequence[str],
    folders: Mapping[str, str],
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, str]]:
    """The files of these experiments, in path order, as the CSV's rows (strings): every one, or
    the first ``limit`` where there are more."""
    from ingence.api.v1.experiment_files import claim_reading
    from ingence.topics.runview import files_of, load_view

    view = load_view(project, run_id)
    reading = claim_reading(catalog)
    names = {
        str(root): root_name(None if label is None else str(label), str(uri))
        for root, uri, label in catalog.query(
            "SELECT root_id, root_uri, label FROM roots"
        ).fetchall()
    }
    rows: list[dict[str, str]] = []
    offset = 0
    while len(rows) < limit:
        page = files_of(
            project, view, experiments, limit=min(PAGE, limit - len(rows)), offset=offset
        )
        if not page:
            break
        stages = _stages(catalog, [row["entry_id"] for row in page], reading)
        roots = _roots_of(catalog, [row["entry_id"] for row in page])
        for row in page:
            values, conflicting = stages.get(row["entry_id"], ((), False))
            root = roots.get(row["entry_id"], "")
            rows.append(
                {
                    "path": row["path"],
                    "root": names.get(root, ""),
                    "root_id": root,
                    "name": row["name"],
                    "stage": "; ".join(values),
                    "stage_state": (
                        "conflicting" if conflicting else "found" if values else "not found"
                    ),
                    "experiment": folders.get(row["experiment"], ""),
                    "experiment_id": row["experiment"],
                }
            )
        offset += len(page)
        if len(page) < min(PAGE, limit - len(rows) + len(page)):
            break
    return rows


def csv_text(rows: Sequence[Mapping[str, str]]) -> str:
    """The CSV: a byte order mark, a header, then a line per row, each cell guarded against
    reading as a formula."""
    out = io.StringIO()
    out.write(BYTE_ORDER_MARK)
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([csv_cell(row[column]) for column in COLUMNS])
    return out.getvalue()


@route("topics/export", model=TopicExportResponse)
def topic_export(context: V1Context, query: Mapping[str, Sequence[str]]) -> TopicExportResponse:
    """One topic's files as CSV text, read against the pinned run (or ``run``)."""
    from ingence.core.registry import ComponentNotAvailableError
    from ingence.storage.catalog import open_catalog
    from ingence.topics import topics_engine

    run_id, _ = run_named(context, query)
    if run_id is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    wanted = (one(query, "topic") or "").strip()
    limit = integer(query, "limit", default=DEFAULT_LIMIT, low=1, high=LIMIT_MOST)
    if not wanted:
        message = "a topic export names the topic (topic=)"
        raise ApiError(message, parameter="topic")
    try:
        engine = topics_engine()
    except ComponentNotAvailableError as error:
        raise ApiError(
            str(error), code="component_unavailable", status=HTTPStatus.SERVICE_UNAVAILABLE
        ) from error
    found = engine.resolve(context.project, run_id, compared=None, suggestions=False)
    reading = next((item for item in found.topics if item.topic.topic_id == wanted), None)
    if reading is None:
        message = f"no topic {wanted} stands in this project"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="topic")
    folders = {key: info.folder for key, info in found.experiments.items()}
    with open_catalog(context.project, run_id=run_id) as catalog:
        rows = topic_rows(
            context.project, run_id, catalog, reading.experiments, folders, limit=limit
        )
    truncated = len(rows) >= limit and reading.files > len(rows)
    left = reading.files - len(rows) if truncated else 0
    said = list(NOTES)
    if truncated:
        said.insert(
            0,
            f"This file lists the first {len(rows):,} of the topic's {reading.files:,} files, in "
            f"path order. The other {left:,} are not listed; make a smaller topic to export them.",
        )
    return TopicExportResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        topic=reading.topic.topic_id,
        topic_name=reading.topic.name,
        file_name=export_file_name(reading.topic.name),
        text=csv_text(rows),
        files=len(rows),
        experiments=len(reading.experiments),
        with_stage=sum(1 for row in rows if row["stage_state"] != "not found"),
        complete=reading.complete,
        truncated=truncated,
        files_not_listed=left,
        notes=said,
    )
