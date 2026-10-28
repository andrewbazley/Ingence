"""The coverage view: what the engine has not read yet, and why, each row with its control.

The page answers four questions that a person asks of a pool before trusting anything the engine
concludes about it:

1. What was set aside: names the census left out, and regions that a rule read as an installed
   program's. The page reads them from :func:`~ingence.api.routes.views.set_aside`, the same
   answer the Inventory gives, so the two cannot disagree.
2. What the budget reached, by the family a dive spends on. For each file type the page shows its
   files, how many a dive has opened, and the extensions that nobody has taught the engine, each
   with what a person said about reading them. An extension row carries the control that answers
   it.
3. Which words bind to nothing, ranked by how many files carry them. These words are the
   vocabulary a person could teach.
4. Why a clock is missing: of the files with no clock from their content, how many no dive has
   opened, and how many were opened and gave none.

Above the words is the reading figure (:mod:`ingence.scan.reading_figure`). It counts the word
parts that were understood, unknown and contested. Beside them it counts the numbers, labels,
dates and other parts read as such. Each count says what it counts.

The page counts what a person's settings set aside apart from what cannot be read.
``reached.set_aside_by_settings`` is the files that a dive left unread because the project's
extraction settings exclude their extension, include only others, or limit their size. Each file
carries its reason, read against the settings as they stand now. A file that the settings no
longer leave out shows as "no longer set aside", and the next dive reads it. ``reached.unreadable``
is content with nothing to read (no bytes, a link with no target). A file that is set aside is not
opened.

Nothing here decides anything. Every figure is a count over the current run's own tables. The one
write that a row offers goes through the one route that writes.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from ingence.api.routes.views import set_aside
from ingence.core.outcomes import ScanOutcome, TimeSource
from ingence.core.project import Project
from ingence.core.schema import Entry
from ingence.dictionary.file_types import file_type_of
from ingence.dictionary.store import DictionaryStore
from ingence.record.verdicts import WORTH_READING, shown_word, worth_reading
from ingence.scan.reading_figure import reading_figure, reading_said
from ingence.scan.reuse import policy_refusal
from ingence.storage.index_store import record_from_row
from ingence.storage.own_files import filesystem_entry

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = ["LIMIT", "UNREAD", "coverage"]

#: How many rows each ranked table shows. The rest are counted, never dropped silently.
LIMIT = 40

#: The outcomes that mean no dive has opened the file yet. A file that a person's settings set
#: aside is one of them.
UNREAD = frozenset({"metadata_only", "deferred", "skipped_by_settings"})

#: What a file a dive set aside by the settings reads as where the settings no longer leave it out.
NO_LONGER_SET_ASIDE = "no longer set aside: the next dive reads it"

#: The clocks read out of a file's content, which a dive is the only way to get.
_CONTENT_CLOCKS = frozenset({TimeSource.INSTRUMENT, TimeSource.DOCUMENT})


def coverage(
    catalog: Catalog, project: Path, *, limit: int = LIMIT, answered: bool = False
) -> dict[str, Any]:
    """Return the four coverage tables for the current run.

    An extension whose worth reading somebody answered leaves the list unless ``answered`` asks
    for it. The count of those extensions is in ``unbound_extensions_answered``. The counts of
    files and of what a dive opened are over every extension either way.
    """
    rows = (
        catalog.query(
            "SELECT * FROM entries WHERE entry_type = 'file' AND NOT is_deleted "  # noqa: S608
            f"AND {filesystem_entry()}"
        )
        .to_arrow_table()
        .to_pylist()
    )
    files = [record_from_row(row, Entry) for row in rows]
    store = DictionaryStore.for_project(project)
    said = worth_reading(project)
    typed: dict[tuple[str, str], str | None] = {}

    def family(entry: Entry) -> str | None:
        key = (entry.compound_extension or "", entry.extension or "")
        if key not in typed:
            scope = file_type_of(store, entry)
            typed[key] = None if scope is None else scope.value
        return typed[key]

    by_type: dict[str, Counter[str]] = {}
    unbound: dict[str, Counter[str]] = {}
    clocks: Counter[str] = Counter()
    by_settings: Counter[str] = Counter()
    unreadable = 0
    extraction = Project.open(project).settings.extraction
    for entry in files:
        if entry.scan_outcome is ScanOutcome.SKIPPED_BY_SETTINGS:
            refused = policy_refusal(
                extensions=frozenset(
                    value for value in (entry.extension, entry.compound_extension) if value
                ),
                size_bytes=entry.size_bytes or 0,
                include_extensions=extraction.include_extensions,
                exclude_extensions=extraction.exclude_extensions,
                max_file_bytes=extraction.max_file_bytes,
            )
            by_settings[refused or NO_LONGER_SET_ASIDE] += 1
        elif entry.scan_outcome is ScanOutcome.SKIPPED_BY_POLICY:
            unreadable += 1
        opened = entry.scan_outcome.value not in UNREAD
        kind = family(entry)
        tally = by_type.setdefault(kind or "", Counter())
        tally["files"] += 1
        tally["opened"] += opened
        if kind is None:
            spelled = unbound.setdefault((entry.extension or "").casefold(), Counter())
            spelled["files"] += 1
            spelled["opened"] += opened
        source = entry.event_time_source
        if source in _CONTENT_CLOCKS:
            clocks["a clock from its content"] += 1
        elif source is TimeSource.FILENAME:
            clocks["a clock from its name only"] += 1
        elif not opened:
            clocks["no clock, and no dive has opened it"] += 1
        else:
            clocks["no clock, and opening it gave none"] += 1

    types: list[dict[str, Any]] = [
        {"file_type": kind or "(no type)", "files": tally["files"], "opened": tally["opened"]}
        for kind, tally in sorted(by_type.items(), key=lambda item: (-item[1]["files"], item[0]))
    ]
    extensions: list[dict[str, Any]] = [
        {
            "extension": spelled,
            "files": tally["files"],
            "opened": tally["opened"],
            "worth_reading": said.get(spelled),
        }
        for spelled, tally in sorted(unbound.items(), key=lambda item: (-item[1]["files"], item[0]))
    ]
    waiting = extensions if answered else [row for row in extensions if not row["worth_reading"]]
    words = catalog.query(
        """
        SELECT c.normalized_value AS word, count(DISTINCT b.entry_id) AS files
        FROM dictionary_bindings b
        JOIN components c ON c.component_id = b.component_id
        WHERE b.resolution_status = 'examined_no_evidence' AND c.component_type = 'word'
        GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT ?
        """,
        [limit],
    ).fetchall()
    figure = reading_figure(catalog)
    return {
        "set_aside": set_aside(catalog),
        "reached": {
            "files": len(files),
            "opened": sum(1 for entry in files if entry.scan_outcome.value not in UNREAD),
            "set_aside_by_settings": sum(by_settings.values()),
            "set_aside_by_settings_because": dict(sorted(by_settings.items())),
            "unreadable": unreadable,
            "file_types": types[:limit],
            "file_types_not_shown": max(0, len(types) - limit),
        },
        "unbound_extensions": waiting[:limit],
        "unbound_extensions_not_shown": max(0, len(waiting) - limit),
        "unbound_extensions_answered": len(extensions) - len(waiting),
        "answered_shown": answered,
        "unbound_words": [{"word": word, "files": count} for word, count in words],
        # What the run read of the names, said with what it counts.
        "reading": figure,
        "reading_said": reading_said(figure),
        "clocks": dict(sorted(clocks.items())),
        # What each worth-reading word shows as, from the one label table, so the Coverage page
        # and the Extensions page show one word each.
        "worth_labels": {word: shown_word(word) for word in WORTH_READING},
    }
