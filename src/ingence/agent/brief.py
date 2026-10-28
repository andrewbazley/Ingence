"""``ingence export brief``: a short, private summary of a project for a program a person runs.

The brief is two files and a key: ``brief.md`` (for reading, or pasting), ``brief.json`` (the same
content, structured) and ``brief.aliases.json`` (alias to identifier, kept apart so neither brief
holds an entry identifier). Everything in it comes through the ``/api/v1`` routes read in-process
(``overview``, ``areas``, ``experiments``, ``themes``, ``topics``, ``goals``, ``assumptions``,
``checks``, ``manifest``), shaped to the level chosen (:mod:`ingence.agent.shape`), and the
finished text is read once more by the backstop.

The budget: a brief is written to a budget of tokens, approximated as characters divided by
four. The fixed lines (the opening, the counts, each section's heading and its count line) come
first. The item lines of the sections other than the experiments fill next, a bounded number
each and together at most half of what is left. The experiments take the rest. A section cut
short says how many more there are. At a large project (:data:`GROUPED_ABOVE_FILES` files or
:data:`GROUPED_ABOVE_EXPERIMENTS` experiments) the experiments are grouped by area, by theme and
by year rather than listed, so the brief does not grow with the collection.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from ingence import __version__
from ingence.agent.shape import Aliases, Level, Shaper, compact_json, level_named
from ingence.api import v1 as api_v1

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.project import Project

__all__ = [
    "BRIEF_FILES",
    "CHARACTERS_PER_TOKEN",
    "DEFAULT_BUDGET",
    "GROUPED_ABOVE_EXPERIMENTS",
    "GROUPED_ABOVE_FILES",
    "SENTENCE",
    "Brief",
    "Fetch",
    "build_brief",
    "in_process_fetch",
    "write_brief",
]

#: The one sentence about what the brief is for, at the top of ``brief.md``.
SENTENCE = (
    "Ingence uses no language model. It gives your assistant a summary at the level you choose, "
    "never your files' contents. Your assistant's answers enter a project only when you import "
    "them."
)

#: The budget in tokens, and how a token is approximated.
DEFAULT_BUDGET = 4000
CHARACTERS_PER_TOKEN = 4
#: Above either, the experiments are grouped rather than listed.
GROUPED_ABOVE_FILES = 200_000
GROUPED_ABOVE_EXPERIMENTS = 2_000
#: The most item lines a section other than the experiments gets.
SECTION_LINES = 25
#: The most rows the experiments route gives in one page, and the most pages a brief reads.
_PAGE = 500
_PAGES = 4
#: The files a brief writes.
BRIEF_FILES = ("brief.md", "brief.json", "brief.aliases.json")

#: What each level carries, said on the brief's first line.
_CARRIES: dict[str, str] = {
    "counts": "numbers only; no words of the data, no names, no paths, no file contents",
    "words": (
        "numbers, the Dictionary's words, your own topic and goal names, and local aliases such "
        "as E1; no names, no paths, no file contents"
    ),
    "names": (
        "numbers, words, local aliases, and the names of experiments and areas; no paths, no "
        "file contents"
    ),
    "paths": (
        "numbers, words, names, and paths relative to each data folder; no absolute paths, no "
        "file contents"
    ),
}

type Query = Mapping[str, Sequence[str]]
type Fetch = Callable[[str, Query], tuple[int, dict[str, Any]]]


def in_process_fetch(project: Path) -> Fetch:
    """A reader of the ``/api/v1`` routes in this process: no server runs, and nothing is
    written under the project (a themes projection built on the way stays in memory)."""
    from ingence.themes.projection import without_cache_writes

    context = api_v1.context_for(project, settled=None, job_running=False)

    def fetch(route: str, query: Query) -> tuple[int, dict[str, Any]]:
        with without_cache_writes():
            status, payload = api_v1.dispatch(context, route, query)
        return int(status), payload

    return fetch


@dataclass(frozen=True, slots=True)
class Brief:
    """A brief as built: its text, its data, and the key."""

    level: Level
    budget_tokens: int
    markdown: str
    data: dict[str, Any]
    aliases: Aliases
    #: How many names of the data the backstop had to replace (it should be none).
    replaced: int

    @property
    def json_text(self) -> str:
        return json.dumps(self.data, indent=1, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Reading the routes.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Read:
    """One route's shaped answer, or why there is none."""

    shaped: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __bool__(self) -> bool:
        return self.error is None


class _Reader:
    def __init__(self, fetch: Fetch, shaper: Shaper) -> None:
        self._fetch = fetch
        self._shaper = shaper

    def read(self, route: str, query: Query | None = None) -> _Read:
        try:
            status, payload = self._fetch(route, query or {})
        except Exception as error:  # a route that raised: said in words, never a traceback
            words = self._shaper.error_message("internal_error", f"{type(error).__name__}: {error}")
            return _Read(error=words)
        shaped = self._shaper.shape(route, payload)
        if "error" in shaped:
            message = str(shaped["error"].get("message") or shaped["error"].get("code"))
            if status == HTTPStatus.OK:
                message = f"not available ({message})"
            return _Read(error=message)
        return _Read(shaped=shaped)

    def experiments(self, *, budget_chars: int) -> tuple[_Read, int]:
        """The experiments, as many pages as the budget could list, and how many there are."""
        first = self.read(
            "experiments", {"sort": ["date"], "order": ["asc"], "limit": [str(_PAGE)]}
        )
        if not first:
            return first, 0
        rows = list(first.shaped.get("rows") or [])
        matching = int(first.shaped.get("matching") or len(rows))
        pages = 1
        # A listed experiment takes at least 40 characters: more rows than fit are never read.
        while len(rows) < matching and pages < _PAGES and len(rows) * 40 < budget_chars:
            page = self.read(
                "experiments",
                {
                    "sort": ["date"],
                    "order": ["asc"],
                    "limit": [str(_PAGE)],
                    "offset": [str(len(rows))],
                },
            )
            if not page or not page.shaped.get("rows"):
                break
            rows.extend(page.shaped["rows"])
            pages += 1
        first.shaped["rows"] = rows
        return first, matching


# ---------------------------------------------------------------------------
# The sections.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Section:
    title: str
    #: Lines always written (the count line, a note that a route did not answer).
    must: list[str] = field(default_factory=list)
    #: Lines written while the budget allows.
    items: list[str] = field(default_factory=list)
    #: What the items are, for "and N more <noun>".
    noun: str = "items"
    #: Lines written before the items when at least one item is written (a sub-heading).
    lead: list[str] = field(default_factory=list)
    #: The most items this section writes.
    cap: int = SECTION_LINES
    #: How many items were written, after assembly.
    written: int = 0


def _n(value: Any) -> str:
    """A count with thousands separators, a share as a percentage, or a missing value as a dash."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _share(value: Any) -> str:
    return f"{round(float(value) * 100)}%" if isinstance(value, int | float) else "-"


def _day(value: Any) -> str | None:
    """The day of a date or a zoned time, or None."""
    if not isinstance(value, str) or len(value) < 4:
        return None
    return value[:10]


def _span(first: Any, last: Any) -> str:
    """``2023-01-09 to 2024-09-27``, one date where both are the same, or ``undated``."""
    start, end = _day(first), _day(last)
    if start is None and end is None:
        return "undated"
    if start == end or end is None:
        return str(start)
    if start is None:
        return str(end)
    return f"{start} to {end}"


def _quoted(text: Any) -> str:
    return '"' + str(text).replace('"', "'") + '"'


def _label(item: Mapping[str, Any], *keys: str) -> str | None:
    """The first of the keys the item has a text for."""
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class _Builder:
    """Build every section from the shaped reads, in the words of one level."""

    def __init__(self, level: Level, reader: _Reader, budget_chars: int) -> None:
        self.level = level
        self.rank = ("counts", "words", "names", "paths").index(level)
        self.reader = reader
        self.budget_chars = budget_chars
        self.data: dict[str, Any] = {}
        self.sections: list[_Section] = []

    @property
    def words(self) -> bool:
        return self.rank >= 1

    @property
    def names(self) -> bool:
        return self.rank >= 2

    @property
    def paths(self) -> bool:
        return self.rank >= 3

    # -- the sections, in the order the brief shows them ----------------------------------------

    def build(self) -> None:
        overview = self.reader.read("overview")
        self.sections.append(self._counts(overview))
        self.sections.append(self._areas(overview))
        counts = overview.shaped.get("counts") or {}
        grouped = (
            int(counts.get("files") or 0) >= GROUPED_ABOVE_FILES
            or int(counts.get("experiments") or 0) >= GROUPED_ABOVE_EXPERIMENTS
        )
        themes = self.reader.read("themes", {"limit": ["0"]})
        if grouped:
            self.sections.append(self._experiments_grouped(overview, themes))
        else:
            self.sections.append(self._experiments_listed())
        self.sections.append(self._themes(overview, themes))
        self.sections.append(self._topics_and_goals())
        self.sections.append(self._assumptions())
        self.sections.append(self._checks())
        self.sections.append(self._not_read(overview))
        self.sections.append(self._changes())

    def _counts(self, overview: _Read) -> _Section:
        section = _Section("Project counts", noun="lines")
        if not overview:
            section.must.append(f"- Overview: {overview.error}.")
            self.data["overview"] = {"error": overview.error}
            return section
        shaped = overview.shaped
        self.data["overview"] = shaped
        counts = shaped.get("counts") or {}
        section.must.append(
            f"- Experiments: {_n(counts.get('experiments'))}; files: {_n(counts.get('files'))}; "
            f"areas: {_n(counts.get('areas'))}."
        )
        dates = shaped.get("date_range") or {}
        basis = f" (from {dates['basis']})" if self.words and dates.get("basis") else ""
        section.must.append(f"- Dates: {_span(dates.get('first'), dates.get('last'))}{basis}.")
        types = shaped.get("file_types") or []
        if types:
            parts = []
            for item in types[:8]:
                said = f"{_n(item.get('files'))} ({_share(item.get('share'))})"
                label = _label(item, "label", "type_id") if self.words else None
                parts.append(f"{label} {said}" if label else said)
            section.must.append(f"- File types ({len(types)}): " + ", ".join(parts) + ".")
        done = shaped.get("completeness") or {}
        if done:
            section.must.append(
                f"- Completeness: {_n(done.get('experiments_complete'))} experiments complete, "
                f"{_n(done.get('experiments_partial'))} partial, "
                f"{_n(done.get('experiments_raw_only'))} raw only, "
                f"{_n(done.get('experiments_analysis_only'))} analysis only; "
                f"{_n(done.get('raw_with_analysis'))} of {_n(done.get('raw_files'))} raw files "
                f"followed by an analysis."
            )
        repeats = shaped.get("repeats") or {}
        if repeats:
            section.must.append(
                f"- Repeats: {_n(repeats.get('seen_once'))} experiments seen once, "
                f"{_n(repeats.get('repeated'))} in {_n(repeats.get('groups'))} groups of repeats."
            )
        section.must.append(
            f"- Assumptions: {_n(shaped.get('assumptions_count'))}; open questions: "
            f"{_n(shaped.get('checks_count'))}."
        )
        return section

    def _areas(self, overview: _Read) -> _Section:
        section = _Section("Areas", noun="areas")
        areas = list(overview.shaped.get("areas") or []) if overview else []
        if not overview:
            section.must.append(f"- Areas: {overview.error}.")
            self.data["areas"] = {"error": overview.error}
            return section
        section.must.append(f"- Areas: {len(areas)} (the folders at the area level).")
        for area in areas:
            section.items.append("- " + self._area_line(area))
        self.data["areas"] = areas
        return section

    def _area_line(self, area: Mapping[str, Any]) -> str:
        said = f"{_n(area.get('experiments'))} experiments, {_n(area.get('files'))} files"
        if not self.words:
            return said
        kind = str(area.get("kind") or "folder")
        assumed = ", assumed" if area.get("kind_assumed") else ""
        name = f" {_quoted(area['name'])}" if self.names and area.get("name") else ""
        return f"{area.get('id', 'A?')}{name} ({kind}{assumed}): {said}"

    def _experiments_listed(self) -> _Section:
        section = _Section("Experiments", noun="experiments", cap=10**9)
        read, matching = self.reader.experiments(budget_chars=self.budget_chars)
        if not read:
            section.must.append(f"- Experiments: {read.error}.")
            self.data["experiments"] = {"error": read.error}
            return section
        shaped = read.shaped
        summary = shaped.get("summary") or {}
        rows = list(shaped.get("rows") or [])
        section.must.append(
            f"- Experiments: {_n(matching)}; files in them: {_n(summary.get('files'))}, "
            f"outside every experiment: {_n(summary.get('files_outside'))}; "
            f"raw files: {_n(summary.get('raw_acquisitions'))}, "
            f"followed by an analysis: {_n(summary.get('followed'))}."
        )
        if self.words and shaped.get("counted_as"):
            section.must.append(f"- Counted as: {shaped['counted_as']}")
        if self.words:
            section.lead.append(
                "- Each line: alias, dates, theme, files by stage (the chain read), completeness, "
                "repeats."
            )
        for row in rows:
            section.items.append("- " + self._experiment_line(row))
        self.data["experiments"] = {
            "summary": summary,
            "matching": matching,
            "rows": rows,
            **(
                {"counted_as": shaped["counted_as"]}
                if self.words and shaped.get("counted_as")
                else {}
            ),
        }
        return section

    def _experiment_line(self, row: Mapping[str, Any]) -> str:
        chain = " > ".join(
            f"{item.get('key')} {_n(item.get('files'))}" if self.words else _n(item.get("files"))
            for item in row.get("by_stage") or []
            if item.get("files")
        )
        followed = row.get("followed") or {}
        done = f"followed {_n(followed.get('n'))} of {_n(followed.get('of'))} raw"
        repeats = row.get("repeats") or {}
        repeated = f"repeats {_n(repeats.get('n'))}" if repeats.get("n") else "seen once"
        span = _span(row.get("date"), row.get("last_file_time"))
        if not self.words:
            return (
                f"{span}: files {_n(row.get('files'))}; by stage {chain or '-'}; {done}; {repeated}"
            )
        theme = row.get("theme_label") or row.get("theme")
        state = str(row.get("state") or "unknown")
        head = str(row.get("id") or "E?")
        if self.names:
            name = _label(row, "shown_name", "name")
            if name:
                head += " " + _quoted(name)
            area = row.get("area")
            if area:
                head += f" in {_quoted(area)}"
        if self.paths and row.get("folder"):
            head += f" at {row['folder']}"
        kinds = ", ".join(
            f"{item.get('label') or item.get('key')} {_n(item.get('files'))}"
            for item in (row.get("by_kind") or [])[:4]
            if item.get("files")
        )
        return (
            f"{head} ({span}): theme {theme or '-'}; files {_n(row.get('files'))}"
            f"{': ' + chain if chain else ''}{'; kinds ' + kinds if kinds else ''}; "
            f"{state}, {done}; {repeated}"
        )

    def _experiments_grouped(self, overview: _Read, themes: _Read) -> _Section:
        section = _Section("Experiments (grouped)", noun="groups", cap=3 * SECTION_LINES)
        counts = overview.shaped.get("counts") or {}
        section.must.append(
            f"- Experiments: {_n(counts.get('experiments'))} in {_n(counts.get('files'))} files: "
            f"too many to list, so they are grouped by area, by theme and by year."
        )
        by_area = [
            f"- by area: {self._area_line(area)}"
            for area in overview.shaped.get("areas") or []
            if area.get("experiments")
        ]
        by_theme = []
        years: dict[str, int] = {}
        if themes:
            for theme in themes.shaped.get("themes") or []:
                by_theme.append(f"- by theme: {self._theme_line(theme)}")
                for year, count in (theme.get("by_year") or {}).items():
                    years[str(year)] = years.get(str(year), 0) + int(count)
        else:
            for theme in overview.shaped.get("themes_top") or []:
                by_theme.append(f"- by theme: {self._theme_top_line(theme)}")
        by_year = [
            f"- by year: {year}: {_n(count)} experiments" for year, count in sorted(years.items())
        ]
        if not by_year:
            by_year = ["- by year: not available (no themes were computed, so no year count)."]
        section.items = [
            *by_area[:SECTION_LINES],
            *by_theme[:SECTION_LINES],
            *by_year[:SECTION_LINES],
        ]
        self.data["experiments"] = {
            "grouped": True,
            "experiments": counts.get("experiments"),
            "files": counts.get("files"),
            "by_year": years,
        }
        return section

    def _theme_top_line(self, theme: Mapping[str, Any]) -> str:
        said = f"{_n(theme.get('experiments'))} experiments"
        if not self.words:
            return said
        return f"{theme.get('id', 'T?')} {theme.get('label') or '(no public words)'}: {said}"

    def _theme_line(self, theme: Mapping[str, Any]) -> str:
        said = (
            f"{_n(theme.get('experiments'))} experiments, years {_n(theme.get('first'))} to "
            f"{_n(theme.get('last'))}, stability {_n(theme.get('stability'))}"
        )
        if not self.words:
            return f"theme {_n(theme.get('theme'))}: {said}"
        label = theme.get("label") or "(no public words)"
        direction = theme.get("direction")
        return (
            f"theme {_n(theme.get('theme'))} {label}: {said}"
            f"{', ' + str(direction) if direction else ''}"
        )

    def _themes(self, overview: _Read, themes: _Read) -> _Section:
        section = _Section("Themes", noun="themes")
        if themes:
            summary = themes.shaped.get("summary") or {}
            items = list(themes.shaped.get("themes") or [])
            section.must.append(
                f"- Themes: {_n(summary.get('themes'))}; experiments placed "
                f"{_n(summary.get('placed'))}, unplaced {_n(summary.get('unplaced'))}, undated "
                f"{_n(summary.get('undated'))}."
            )
            section.items = ["- " + self._theme_line(theme) for theme in items]
            self.data["themes"] = {"summary": summary, "themes": items}
            return section
        top = list(overview.shaped.get("themes_top") or []) if overview else []
        section.must.append(f"- Themes: {themes.error}; the leading themes of the overview follow.")
        section.items = ["- " + self._theme_top_line(theme) for theme in top]
        self.data["themes"] = {"error": themes.error, "themes_top": top}
        return section

    def _topics_and_goals(self) -> _Section:
        section = _Section("Topics and goals", noun="topics and goals")
        topics = self.reader.read("topics", {"suggestions": ["0"]})
        goals = self.reader.read("goals")
        if topics:
            items = list(topics.shaped.get("topics") or [])
            section.must.append(f"- Topics (yours): {len(items)}.")
            for topic in items:
                section.items.append("- " + self._topic_line(topic))
            self.data["topics"] = items
        else:
            section.must.append(f"- Topics: {topics.error}.")
            self.data["topics"] = {"error": topics.error}
        if goals:
            items = list(goals.shaped.get("goals") or [])
            section.must.append(f"- Goals (yours): {len(items)}.")
            for goal in items:
                section.items.append("- " + self._goal_line(goal))
            self.data["goals"] = items
        else:
            section.must.append(f"- Goals: {goals.error}.")
            self.data["goals"] = {"error": goals.error}
        return section

    def _topic_line(self, topic: Mapping[str, Any]) -> str:
        said = (
            f"{_n(topic.get('experiments_count'))} experiments of "
            f"{_n(topic.get('experiments_out_of'))}"
        )
        if not self.words:
            return f"topic: {said}"
        complete = "complete" if topic.get("complete") else "not complete in this run"
        return f"{topic.get('id', 'K?')} {_quoted(topic.get('name', ''))}: {said}; {complete}"

    def _goal_line(self, goal: Mapping[str, Any]) -> str:
        summary = goal.get("summary") or {}
        said = (
            f"{_n(summary.get('experiments'))} matches ({_n(summary.get('named'))} named, "
            f"{_n(summary.get('associated'))} associated, {_n(summary.get('same_theme'))} same "
            f"theme), {_span(summary.get('first'), summary.get('last'))}; raw with analysis "
            f"{_n(summary.get('raw_with_analysis'))} of {_n(summary.get('of'))}"
        )
        if not self.words:
            return f"goal: {said}"
        matches = ", ".join(
            f"{match.get('experiment_id')} ({match.get('tier')})"
            for match in (goal.get("matches") or [])[:12]
        )
        return (
            f"{goal.get('id', 'G?')} {_quoted(goal.get('text', ''))}: {said}"
            f"{'; matches ' + matches if matches else ''}"
        )

    def _assumptions(self) -> _Section:
        section = _Section("Assumptions", noun="assumptions")
        read = self.reader.read("assumptions")
        if not read:
            section.must.append(f"- Assumptions: {read.error}.")
            self.data["assumptions"] = {"error": read.error}
            return section
        items = list(read.shaped.get("assumptions") or [])
        section.must.append(
            f"- Assumptions: {_n(read.shaped.get('total'))} (each a claim the engine made from "
            f"the evidence it had, reversible by one answer)."
        )
        for item in items:
            said = f"{_n(item.get('count'))} files"
            if self.words:
                said = f"{item.get('id', 'S?')} ({item.get('kind')}, {item.get('tier')}): {said}"
            if self.names and item.get("text"):
                said += f": {item['text']}"
            if self.paths and item.get("examples"):
                said += (
                    " (for example "
                    + ", ".join(str(example.get("label")) for example in item["examples"][:2])
                    + ")"
                )
            section.items.append("- " + said)
        self.data["assumptions"] = {"total": read.shaped.get("total"), "assumptions": items}
        return section

    def _checks(self) -> _Section:
        section = _Section("Open questions", noun="questions")
        read = self.reader.read("checks")
        if not read:
            section.must.append(f"- Open questions: {read.error}.")
            self.data["checks"] = {"error": read.error}
            return section
        items = list(read.shaped.get("checks") or [])
        section.must.append(
            f"- Open questions: {_n(read.shaped.get('total'))} (each settled by one answer in "
            f"the Dashboard; nothing here answers one)."
        )
        for item in items:
            said = f"settles {_n(item.get('settles_n'))} {item.get('settles_unit') or 'items'}"
            if self.words:
                options = ", ".join(
                    str(option.get("value")) for option in item.get("options") or []
                )
                said = f"{item.get('id', 'Q?')} ({item.get('kind')}): {said}"
                if options:
                    said += f"; answers: {options}"
            if self.names and item.get("title"):
                said += f": {item['title']}"
            section.items.append("- " + said)
        self.data["checks"] = {"total": read.shaped.get("total"), "checks": items}
        return section

    def _not_read(self, overview: _Read) -> _Section:
        section = _Section("What was not read", noun="lines")
        if not overview:
            section.must.append(f"- Reading: {overview.error}.")
            self.data["not_read"] = {"error": overview.error}
            return section
        coverage = overview.shaped.get("coverage") or {}
        done = overview.shaped.get("completeness") or {}
        counts = overview.shaped.get("counts") or {}
        section.must.append(
            f"- Files read: {_n(coverage.get('read'))} of {_n(counts.get('files'))}; described "
            f"from a read file of the same series: {_n(coverage.get('represented'))}; neither: "
            f"{_n(coverage.get('not_reached'))}."
        )
        section.must.append(f"- Files whose contents were not read: {_n(done.get('not_read'))}.")
        self.data["not_read"] = {"coverage": coverage, "not_read": done.get("not_read")}
        return section

    def _changes(self) -> _Section:
        section = _Section("Changed since the last run", noun="sentences")
        read = self.reader.read("manifest")
        if not read:
            section.must.append(f"- Changes: {read.error}.")
            self.data["changes"] = {"error": read.error}
            return section
        shaped = read.shaped
        decisions = list(shaped.get("decisions") or [])
        rules = list(shaped.get("rules") or [])
        claims = list(shaped.get("claims") or [])
        pairs = list(shaped.get("pairs") or [])
        section.must.append(
            f"- Decisions that took effect: {len(decisions)}; rules changed: {len(rules)}; "
            f"readings that moved: {len(claims)}; pairings that moved: {len(pairs)}."
        )
        sentences: list[str] = []
        if self.words:
            sentences.extend(str(rule.get("said")) for rule in rules if rule.get("said"))
            quality = shaped.get("quality") or {}
            sentences.extend(str(line) for line in quality.get("said") or [])
        if self.names:
            sentences.extend(str(line) for line in shaped.get("said") or [])
        section.items = [f"- {line}" for line in sentences]
        self.data["changes"] = {
            "decisions": len(decisions),
            "rules": rules,
            "claims": claims,
            "pairs": pairs,
            "said": sentences,
        }
        return section


# ---------------------------------------------------------------------------
# Assembly within the budget.
# ---------------------------------------------------------------------------


def _assemble(opening: list[str], sections: list[_Section], budget_chars: int) -> str:
    """The brief's text: the fixed lines, then the items while the budget allows."""
    fixed = sum(len(line) + 1 for line in opening)
    for section in sections:
        fixed += len(f"## {section.title}") + 2
        fixed += sum(len(line) + 1 for line in section.must)
    remaining = budget_chars - fixed
    # Every section but the experiments first (the largest cap is the experiments'), then the
    # experiments take what is left. The other sections' items share at most half of what the
    # fixed lines leave, so long rule sentences never crowd every experiment out.
    others = remaining // 2
    ordered = sorted(sections, key=lambda section: section.cap)
    for section in ordered:
        bounded = section.cap <= SECTION_LINES
        room = min(remaining, others) if bounded else remaining
        spent = sum(len(line) + 1 for line in section.lead)
        count = 0
        # The "and N more" line is reserved before the loop, at its longest (N as large as the
        # list), so a cut section's closing line never overruns the budget.
        reserve = len(_more(len(section.items), section.noun)) + 1 if section.items else 0
        for line in section.items[: section.cap]:
            cost = len(line) + 1
            if spent + cost + reserve > room:
                break
            spent += cost
            count += 1
        if count == 0:
            spent = 0
        section.written = count
        if count < len(section.items):
            spent += len(_more(len(section.items) - count, section.noun)) + 1
        remaining -= spent
        if bounded:
            others -= spent
    lines = list(opening)
    for section in sections:
        lines.append("")
        lines.append(f"## {section.title}")
        lines.extend(section.must)
        if section.written:
            lines.extend(section.lead)
            lines.extend(section.items[: section.written])
        if section.written < len(section.items):
            lines.append(_more(len(section.items) - section.written, section.noun))
    return "\n".join(lines) + "\n"


def _more(count: int, noun: str) -> str:
    return f"- ... and {count:,} more {noun} (over the budget)."


def _cut_data(data: dict[str, Any], sections: list[_Section]) -> dict[str, Any]:
    """The data with each list cut to what the text listed, so both say the same."""
    written = {section.title: section.written for section in sections}
    cut = dict(data)
    experiments = cut.get("experiments")
    if isinstance(experiments, dict) and isinstance(experiments.get("rows"), list):
        rows = experiments["rows"]
        listed = written.get("Experiments", len(rows))
        cut["experiments"] = {
            **experiments,
            "rows": rows[:listed],
            "not_listed": len(rows) - listed,
        }
    for key, title in (
        ("areas", "Areas"),
        ("assumptions", "Assumptions"),
        ("checks", "Open questions"),
        ("themes", "Themes"),
    ):
        value = cut.get(key)
        kept = written.get(title)
        if kept is None:
            continue
        if isinstance(value, list):
            cut[key] = value[:kept]
        elif isinstance(value, dict):
            for inner in ("assumptions", "checks", "themes", "themes_top"):
                if isinstance(value.get(inner), list):
                    cut[key] = {**value, inner: value[inner][:kept]}
    return cut


# ---------------------------------------------------------------------------
# Building and writing.
# ---------------------------------------------------------------------------


def build_brief(
    project: Project,
    *,
    level: Level = "words",
    budget_tokens: int = DEFAULT_BUDGET,
    fetch: Fetch | None = None,
    shaper: Shaper | None = None,
) -> Brief:
    """Build the brief of a project at a level, within a budget of tokens.

    ``fetch`` reads the routes (in-process by default); ``shaper`` shapes them (the project's
    own by default). Both are parameters so a test can feed a synthetic project.
    """
    level = level_named(level)
    if budget_tokens < 100:
        message = f"the budget is in tokens, at least 100; {budget_tokens} is too small"
        raise ValueError(message)
    shaper = shaper if shaper is not None else Shaper.for_project(project, level)
    reader = _Reader(fetch if fetch is not None else in_process_fetch(project.path), shaper)
    budget_chars = budget_tokens * CHARACTERS_PER_TOKEN
    builder = _Builder(level, reader, budget_chars)
    builder.build()
    opening = [
        f"# Ingence brief at level {level}: {_CARRIES[level]}.",
        SENTENCE,
        f"Engine ingence {__version__}; budget {budget_tokens:,} tokens.",
    ]
    if level != "counts":
        opening.append(
            "Aliases such as E1 (an experiment), A1 (an area), T1 (a theme), G1 (a goal), K1 (a "
            "topic), S1 (an assumption) and Q1 (a question) are local to this brief; the key is "
            "in brief.aliases.json beside it, never in it."
        )
    markdown = shaper.finish(_assemble(opening, builder.sections, budget_chars))
    data = {
        "level": level,
        "budget_tokens": budget_tokens,
        "engine_version": __version__,
        "carries": _CARRIES[level],
        "sections": _cut_data(builder.data, builder.sections),
    }
    data = json.loads(shaper.finish(compact_json(data)))
    return Brief(
        level=level,
        budget_tokens=budget_tokens,
        markdown=markdown,
        data=data,
        aliases=shaper.aliases,
        replaced=shaper.replaced,
    )


def write_brief(brief: Brief, directory: Path) -> tuple[Path, Path, Path]:
    """Write ``brief.md``, ``brief.json`` and ``brief.aliases.json`` into a folder."""
    directory.mkdir(parents=True, exist_ok=True)
    markdown, data, key = (directory / name for name in BRIEF_FILES)
    markdown.write_text(brief.markdown, encoding="utf-8")
    data.write_text(brief.json_text, encoding="utf-8")
    brief.aliases.write(key)
    return markdown, data, key
