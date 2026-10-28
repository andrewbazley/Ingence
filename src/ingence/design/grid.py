"""One experiment's design grid, from its files' names: the pure part.

What the grid is. The files of an experiment that share one name layout, read as a table: each
varying part of the names that is a condition (a drug, a time, a dose) is a factor. Each
combination of one level per factor is a cell. A cell holds the files of that combination, and
its replicates are how many times the combination was repeated (the counting part of the name).
Nothing is read but the names, the condition classes the grouping rule drew, and the run's
conclusions (a file's stage, whether its output is empty).

A layout is the parts of a name that may be conditions, in order: a date, a clock, an extension,
a stage word and a replicate label are set aside, so ``a_x_2mM_take1.nd2`` and
``a_x_2mM_take1_processed.tif`` are one layout. The experiment's most common layout is drawn. The
files of another (a control with no dose, say) are left out and counted with their reason.

What is never a factor (groups are made of words): a date, a clock, an extension, a version,
and a part the Dictionary reads as a stage, a replicate, a count or a connective. A bare number
that counts with its siblings (``001``, ``002``) or an indexed label bound to a replicate
(``rep2``) is the replicate axis. One that does not count (``488``, ``561``) may name a
condition and is a factor.

Parts that go together (:func:`_merged`): within one layout, two parts whose levels pair one
to one, over at least ``merge_minimum_observations`` files a level and with at most
``merge_exception_share`` of the files off the pairing, are one factor (``GFP`` always beside
``488``). The exceptions are counted and named by count ("one exception in 14"), and no chance
test is implied: the parts are said to go together, never to be dependent.

Crossed or nested for each pair of factors (:func:`_relation`): crossed where every
combination of the two was found, nested where each level of one lies under one level of the
other (a dose under its drug), paired where the levels match one to one, and otherwise partly
crossed with the number of combinations not found.

Not found is completeness's wording (never "missing"): a combination of the factors' levels
that no file of the experiment holds. It is listed only where the design is not nested, because in
a nested design the combinations outside the nesting are not expected, and only up to
``cells_listed_at_most`` combinations.

Pure: given the files' parts, it reads no catalog and writes nothing, so every rule above is
tested on invented names.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.api.name_patterns import name_pattern
from ingence.dictionary.normalize import split_indexed
from ingence.grouping.identifiers import slot_kind

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "NOT_A_CONDITION",
    "REASONS",
    "Cell",
    "Coverage",
    "DesignFile",
    "ExperimentDesign",
    "Factor",
    "FactorPair",
    "GridParameters",
    "GridQuestion",
    "Ladder",
    "Level",
    "MergeNote",
    "NamePart",
    "OtherLayout",
    "build_design",
]

#: The kinds of sense whose words are never a condition: each has a place of its own (a stage is
#: counted by stage, a replicate is the replicate axis), or says nothing a design compares. The
#: chips of a result card hide the same kinds (``search/tags.py``).
NOT_A_CONDITION = frozenset(
    {
        "stage", "replicate", "date", "version", "identifier", "unit", "format", "file_role",
        "count", "read_structure", "position", "connective",
    }
)  # fmt: skip

#: Component types that are never a factor, whatever they hold.
NEVER_FACTORS = frozenset({"date", "clock", "extension", "version", "identifier"})

#: The reasons a file is left out of a grid, as stored, with the words a page shows.
REASONS: dict[str, str] = {
    "no_condition_class": "no condition class: the replicate grouping did not class its name",
    "no_name_layout": "the run recorded no layout for its name",
    "another_layout": "another layout: its name is laid out unlike the grid's",
    "program": "inside an installed program",
    "no_condition_part": "its name has no part that could be a condition",
    "no_factor": "the grid's layout has no part that varies",
    "no_files": "no file of the experiment was found",
}

#: How many other layouts an experiment names.
OTHER_LAYOUTS_NAMED = 5

#: How many names ``name_pattern`` is shown, at most (it aligns every name against the rest).
PATTERN_NAMES = 200

#: The states a cell has.
OCCUPIED = "occupied"
NOT_FOUND = "not found"
EMPTY_OUTPUT = "empty output"


@dataclass(frozen=True, slots=True)
class GridParameters:
    """How a grid is drawn: the settings ``design.*`` hold, as arguments."""

    merge_parts: bool = True
    #: Files a level needs before its part may be merged with another.
    merge_minimum_observations: int = 2
    #: The share of an experiment's files that may be off a pairing of two parts and the parts
    #: still go together (one in 14 is about 7 per cent).
    merge_exception_share: float = 0.1
    #: The most combinations listed as not found. A larger product is counted and not listed.
    cells_listed_at_most: int = 2000
    #: The most levels a part may have and still be a condition: a part that takes more values
    #: than this is an identifier or a frame number, and is left out.
    factor_levels_at_most: int = 50
    #: Whether a file no condition class holds is drawn if its name has a recorded layout. The
    #: grouping publishes no class for a condition with one file (an entry alone under its key
    #: gets nothing), so with this off every single-replicate condition is left out and its cell
    #: reads "not found" though its file exists. With it on, files of scripts, figures and
    #: documents whose names merely share a layout are drawn too, and make grids of their own.
    #: Off by default: the classes gate the grid.
    include_unclassed: bool = False
    #: The stages at which a file is the data as it came off the instrument: a cell whose files
    #: past those stages are all empty outputs has an empty output, however full its raw data.
    early_stages: tuple[str, ...] = ("acquisition", "raw")


@dataclass(frozen=True, slots=True)
class NamePart:
    """One part of a name, as the run read it: its place, its kind and its value."""

    ordinal: int
    #: The component type: ``word``, ``quantity``, ``number``, ``indexed``, ``date``,
    #: ``extension``...
    kind: str
    #: The normalised value (levels are told apart by it).
    value: str
    #: The value as written.
    shown: str
    #: The kind of the sense the Dictionary bound the part to (``reagent``, ``stage``), or None.
    sense: str | None = None
    magnitude: float | None = None
    dimension: str | None = None


@dataclass(frozen=True, slots=True)
class DesignFile:
    """One file of an experiment, as the grid reads it."""

    entry_id: str
    name: str
    #: The condition class the replicate grouping gave it (its key), or None.
    class_key: str | None
    #: Its stage, as the run concluded it, or None.
    stage: str | None
    #: Whether the run found its output empty.
    empty: bool
    #: Its name's parts in the order the name wrote them. Empty where no layout was recorded.
    parts: tuple[NamePart, ...] = ()
    #: The file's own clock (an instrument's or a document's), in nanoseconds, or None.
    clock_ns: int | None = None


@dataclass(frozen=True, slots=True)
class Level:
    """One value a factor takes, and how many files carry it."""

    value: str
    shown: str
    files: int


@dataclass(frozen=True, slots=True)
class Ladder:
    """Levels that rise by a constant ratio or a constant difference: a label on the levels,
    never an order in time (a dose is a condition)."""

    kind: str
    step: float


@dataclass(frozen=True, slots=True)
class MergeNote:
    """A part merged into a factor: where it stood, and how well the pairing held."""

    position: int
    kind: str
    name: str
    #: Files the pairing was read over, and how many were off it.
    observations: int
    exceptions: int
    #: The names of up to three files off the pairing.
    examples: tuple[str, ...] = ()

    @property
    def said(self) -> str:
        if not self.exceptions:
            return f"always together over {self.observations} files"
        count = "one exception" if self.exceptions == 1 else f"{self.exceptions} exceptions"
        return f"{count} in {self.observations}"


@dataclass(frozen=True, slots=True)
class Factor:
    """A part of the names that is a condition: where it stands, what it is, its levels."""

    #: ``p<position>``, or the positions joined by ``+`` for parts that go together.
    factor_id: str
    #: The positions (1-based, in the name) of the part and of any part merged into it.
    positions: tuple[int, ...]
    #: The component type of the leading part.
    kind: str
    #: The part kind in ``name_pattern``'s words: ``variant``, ``quantity``, ``number``.
    part_kind: str
    #: What a page calls it: the dimension of a quantity, the sense of a word, else its kind.
    name: str
    levels: tuple[Level, ...]
    #: ``numeric`` where the levels are numbers in one dimension (in numeric order), else
    #: ``alphabetical``.
    order: str
    ladder: Ladder | None = None
    merged: tuple[MergeNote, ...] = ()


@dataclass(frozen=True, slots=True)
class Cell:
    """One combination of levels, one level per factor (by index into the factor's levels)."""

    levels: tuple[int, ...]
    files: int
    #: Files by stage (``unstated`` where the run concluded none).
    stages: tuple[tuple[str, int], ...]
    #: How many times the combination was repeated. It is 0 where it was not found.
    replicates: int
    state: str


@dataclass(frozen=True, slots=True)
class FactorPair:
    """How two factors lie against each other."""

    first: str
    second: str
    #: ``crossed``, ``nested``, ``paired`` or ``partly crossed``.
    relation: str
    #: For ``nested``: the factor the other lies in.
    nested_in: str | None
    #: Combinations of the two found, and combinations their levels allow.
    found: int
    possible: int


@dataclass(frozen=True, slots=True)
class GridQuestion:
    """A question for a person: a combination every other of which was found."""

    kind: str
    #: The combination, one level as written per factor.
    combination: tuple[str, ...]
    question: str


@dataclass(frozen=True, slots=True)
class Coverage:
    """The files of an experiment in its grid, and the ones left out, with why."""

    files: int
    in_grid: int
    left_out: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class OtherLayout:
    """Files of an experiment laid out unlike its grid's: how they are written, and how many."""

    pattern: str
    files: int


@dataclass(frozen=True, slots=True)
class ExperimentDesign:
    """One experiment's design, or why it has none."""

    experiment_id: str
    root_id: str
    folder: str
    factors: tuple[Factor, ...]
    cells: tuple[Cell, ...]
    pairs: tuple[FactorPair, ...]
    findings: tuple[GridQuestion, ...]
    coverage: Coverage
    #: Why there is no grid, in words. Empty where there is one.
    reason: str = ""
    #: The code of that reason (a key of :data:`REASONS`). Empty where there is a grid.
    reason_code: str = ""
    #: The layout's names written once, and what each varying part is.
    pattern: str = ""
    pattern_kinds: tuple[str, ...] = ()
    #: Whether combinations not found are listed among the cells, and if not why.
    not_found_listed: bool = False
    not_found_said: str = ""
    #: Combinations found over combinations the levels allow. It is None where a nested design makes
    #: the product no count of what was expected.
    fill: float | None = None
    #: Parts merged into a factor, in all.
    merged_parts: int = 0
    #: Positions counted as the replicate axis.
    replicate_positions: tuple[int, ...] = ()
    #: The layouts left out, the largest first (at most :data:`OTHER_LAYOUTS_NAMED`).
    other_layouts: tuple[OtherLayout, ...] = ()

    @property
    def has_grid(self) -> bool:
        return bool(self.factors)

    @property
    def crossed_pairs(self) -> int:
        return sum(1 for pair in self.pairs if pair.relation == "crossed")


@dataclass(frozen=True, slots=True)
class _Position:
    """A place in the layout."""

    ordinal: int
    kind: str


# ---------------------------------------------------------------------------
# The layout and its positions.
# ---------------------------------------------------------------------------


def _layout_of(file: DesignFile) -> str:
    """A name's layout: the kind of each part in order, a quantity with its dimension."""
    return "|".join(
        f"{part.kind}:{part.dimension}" if part.kind == "quantity" and part.dimension else part.kind
        for part in file.parts
    )


def _spelling(files: Sequence[DesignFile], ordinal: int) -> str:
    """How a level is written: the most common spelling among its files, then the least."""
    written = Counter(file.parts[ordinal].shown for file in files)
    return min(written, key=lambda shown: (-written[shown], shown))


def _number_of(part: NamePart) -> float | None:
    """What the part's value is as a number: a quantity's magnitude, a bare number, an index."""
    if part.kind == "quantity":
        return part.magnitude
    if part.kind == "number":
        try:
            return float(int(part.value))
        except ValueError:
            return None
    if part.kind == "indexed":
        split = split_indexed(part.value)
        return None if split is None else float(split[1])
    return None


def _part_role(part: NamePart) -> str | None:
    """``skip`` or ``replicate`` for a part that is never a condition, whatever it holds; None
    for a part that may be one. Known from the part alone: a date, a clock, an extension, a
    version or an identifier. A word or label bound to a stage, a count or the like (a stage word
    is counted by stage, and what a replicate label counts is the replicate axis)."""
    if part.kind in NEVER_FACTORS:
        return "skip"
    if part.kind in ("word", "indexed") and part.sense in NOT_A_CONDITION:
        return "replicate" if part.sense == "replicate" else "skip"
    if part.kind == "indexed":
        split = split_indexed(part.value)
        if split is not None and split[0] in _REPLICATE_LABELS:
            return "replicate"
    return None


def _role(position: _Position, files: Sequence[DesignFile]) -> str:
    """``factor`` or ``replicate``, for one place of the core layout. A number that counts with
    its siblings is the replicate axis, written alone (``001``, ``002``) or glued to a label
    (``exp1``, ``s2``): written padded to a width, or no larger than twice how many there are (the
    engine's own test, :func:`~ingence.grouping.identifiers.slot_kind`). A label the Dictionary
    reads as a time point (``tp1``) is a condition, as ``2h`` is, and stays a factor. ``488`` and
    ``561`` do not count and may name a condition, as a quantity with its unit does."""
    if position.kind == "indexed" and files[0].parts[position.ordinal].sense == "timepoint":
        return "factor"  # a time point is a condition, as 2h is
    if position.kind in ("number", "indexed"):
        written = {file.parts[position.ordinal].shown for file in files}
        digits = {found.group(1) for shown in written if (found := _TRAILING_DIGITS.search(shown))}
        if len(digits) == len(written) and slot_kind(sorted(digits)) == "counter":
            return "replicate"
    return "factor"


_TRAILING_DIGITS = re.compile(r"(\d+)\s*$")


#: Indexed labels that count repeats whatever the Dictionary says (``r2``).
_REPLICATE_LABELS = frozenset({"r"})


def _part_kind(kind: str, senses: set[str | None]) -> str:
    """The part's kind in ``name_pattern``'s words."""
    if kind == "quantity":
        return "quantity"
    if kind == "number":
        return "number"
    return "variant"


def _factor_name(kind: str, parts: Sequence[NamePart]) -> str:
    """What a page calls a factor: a quantity's dimension, a word's sense, else the kind."""
    if kind == "quantity":
        dimensions = {part.dimension for part in parts if part.dimension}
        if len(dimensions) == 1:
            return str(next(iter(dimensions))).replace("_", " ")
        return "quantity"
    senses = {part.sense for part in parts if part.sense}
    if len(senses) == 1 and not any(part.sense is None for part in parts):
        return str(next(iter(senses))).replace("_", " ")
    return {"word": "factor", "number": "number", "indexed": "label"}.get(kind, kind)


def _levels_of(
    files: Sequence[DesignFile], ordinal: int
) -> tuple[tuple[Level, ...], str, dict[str, int]]:
    """A position's levels in their order, how they are ordered, and each value's place."""
    by_value: dict[str, list[DesignFile]] = {}
    for file in files:
        by_value.setdefault(file.parts[ordinal].value, []).append(file)
    numbers = {value: _number_of(members[0].parts[ordinal]) for value, members in by_value.items()}
    kind = files[0].parts[ordinal].kind
    same_label = True
    if kind == "indexed":
        labels = {split[0] for value in by_value if (split := split_indexed(value)) is not None}
        same_label = len(labels) == 1
    dimensions = {members[0].parts[ordinal].dimension for members in by_value.values()}
    numeric = (
        kind in ("quantity", "number", "indexed")
        and all(number is not None for number in numbers.values())
        and same_label
        and (kind != "quantity" or len(dimensions) == 1)
    )
    if numeric:
        ordered = sorted(by_value, key=lambda value: (numbers[value] or 0.0, value))
    else:
        ordered = sorted(
            by_value, key=lambda value: (_spelling(by_value[value], ordinal).casefold(), value)
        )
    levels = tuple(
        Level(value=value, shown=_spelling(by_value[value], ordinal), files=len(by_value[value]))
        for value in ordered
    )
    return levels, "numeric" if numeric else "alphabetical", {v: i for i, v in enumerate(ordered)}


def _ladder(files: Sequence[DesignFile], ordinal: int, levels: Sequence[Level]) -> Ladder | None:
    """The constant ratio or difference between numeric levels, or None (three levels at least)."""
    if len(levels) < 3:
        return None
    by_value = {file.parts[ordinal].value: file.parts[ordinal] for file in files}
    numbers = [_number_of(by_value[level.value]) for level in levels]
    if any(number is None for number in numbers):
        return None
    values = [float(number) for number in numbers if number is not None]
    if all(value > 0 for value in values):
        ratios = [after / before for before, after in itertools.pairwise(values)]
        if all(math.isclose(ratio, ratios[0], rel_tol=1e-6) for ratio in ratios):
            return Ladder(kind="ratio", step=ratios[0])
    steps = [after - before for before, after in itertools.pairwise(values)]
    if all(math.isclose(step, steps[0], rel_tol=1e-6, abs_tol=1e-12) for step in steps):
        return Ladder(kind="difference", step=steps[0])
    return None


# ---------------------------------------------------------------------------
# Parts that go together.
# ---------------------------------------------------------------------------


def _pairing(
    files: Sequence[DesignFile], head: int, other: int, parameters: GridParameters
) -> tuple[dict[str, str], int] | None:
    """The one-to-one pairing of two positions' levels and its exceptions, or None."""
    together: dict[str, Counter[str]] = {}
    alone: Counter[str] = Counter()
    for file in files:
        first, second = file.parts[head].value, file.parts[other].value
        together.setdefault(first, Counter())[second] += 1
        alone[second] += 1
    if len(together) != len(alone) or len(together) < 2:
        return None
    if any(sum(row.values()) < parameters.merge_minimum_observations for row in together.values()):
        return None
    if any(count < parameters.merge_minimum_observations for count in alone.values()):
        return None
    pairing = {
        first: min(row, key=lambda value: (-row[value], value)) for first, row in together.items()
    }
    if len(set(pairing.values())) != len(pairing):
        return None
    exceptions = len(files) - sum(together[first][second] for first, second in pairing.items())
    if exceptions > math.floor(parameters.merge_exception_share * len(files)):
        return None
    return pairing, exceptions


# ---------------------------------------------------------------------------
# The relation of two factors.
# ---------------------------------------------------------------------------


def _relation(
    first: Factor,
    second: Factor,
    found: set[tuple[int, int]],
) -> FactorPair:
    possible = len(first.levels) * len(second.levels)
    under_first: dict[int, set[int]] = {}
    under_second: dict[int, set[int]] = {}
    for one, two in found:
        under_first.setdefault(one, set()).add(two)
        under_second.setdefault(two, set()).add(one)
    nested_in: str | None = None
    if len(found) == possible:
        relation = "crossed"
    elif all(len(parents) == 1 for parents in under_second.values()) and any(
        len(children) > 1 for children in under_first.values()
    ):
        relation, nested_in = "nested", first.factor_id
    elif all(len(parents) == 1 for parents in under_first.values()) and any(
        len(children) > 1 for children in under_second.values()
    ):
        relation, nested_in = "nested", second.factor_id
    elif all(len(parents) == 1 for parents in under_first.values()) and all(
        len(parents) == 1 for parents in under_second.values()
    ):
        relation = "paired"
    else:
        relation = "partly crossed"
    return FactorPair(
        first=first.factor_id,
        second=second.factor_id,
        relation=relation,
        nested_in=nested_in,
        found=len(found),
        possible=possible,
    )


# ---------------------------------------------------------------------------
# The grid.
# ---------------------------------------------------------------------------


def _replicates(
    files: Sequence[DesignFile],
    counters: Sequence[int],
    extras: dict[str, tuple[tuple[str, ...], str, tuple[int, ...]]],
) -> int:
    """How many times a combination was repeated: the distinct counts of the replicate axis (the
    replicate labels and the bare numbers that count), in the format (extension) holding most;
    the files themselves where the names count nothing."""
    by_format: dict[str, set[object]] = {}
    for index, file in enumerate(files):
        labels, extension, _ = extras[file.entry_id]
        counted = labels + tuple(file.parts[place].value for place in counters)
        by_format.setdefault(extension, set()).add(counted if counted else index)
    return max((len(keys) for keys in by_format.values()), default=0)


def _state(files: Sequence[DesignFile], early: Sequence[str]) -> str:
    """``empty output`` where every file made after the acquisition is an empty output (or, with
    no such file, every file is one). Otherwise ``occupied``."""
    made = [file for file in files if file.stage not in early]
    judged = made or list(files)
    return EMPTY_OUTPUT if judged and all(file.empty for file in judged) else OCCUPIED


def _stages(files: Sequence[DesignFile]) -> tuple[tuple[str, int], ...]:
    counted = Counter(file.stage or "unstated" for file in files)
    return tuple(sorted(counted.items()))


def build_design(
    experiment_id: str,
    root_id: str,
    folder: str,
    files: Sequence[DesignFile],
    *,
    programs: int = 0,
    parameters: GridParameters = GridParameters(),  # noqa: B008 -- a frozen value
) -> ExperimentDesign:
    """One experiment's design from its files (``programs``: files left out inside a program)."""
    total = len(files) + programs
    left: Counter[str] = Counter()
    if programs:
        left["program"] = programs
    classed: list[DesignFile] = []
    #: Files no condition class holds: they may fill a cell of the grid the classed files make,
    #: and never choose its layout or create a factor, a level or a cell.
    loose: list[DesignFile] = []
    for file in files:
        if file.class_key is None and not parameters.include_unclassed:
            if file.parts:
                loose.append(file)
            else:
                left["no_name_layout"] += 1
        elif not file.parts:
            left["no_name_layout"] += 1
        else:
            classed.append(file)

    def none(code: str, reason: str = "", in_grid: int = 0) -> ExperimentDesign:
        if loose:
            left["no_condition_class"] += len(loose)
        return ExperimentDesign(
            experiment_id=experiment_id,
            root_id=root_id,
            folder=folder,
            factors=(),
            cells=(),
            pairs=(),
            findings=(),
            coverage=Coverage(files=total, in_grid=in_grid, left_out=tuple(sorted(left.items()))),
            reason=reason or f"no file of the experiment could be drawn: {REASONS[code]}",
            reason_code=code,
        )

    # A layout is the parts that may be conditions, in order: a date, a clock, an extension,
    # a stage word and a replicate label are set aside (the replicate labels count repeats), so
    # ``a_x_2mM_take1.nd2`` and ``a_x_2mM_take1_processed.tif`` are one layout.
    extras: dict[str, tuple[tuple[str, ...], str, tuple[int, ...]]] = {}

    def core_of(file: DesignFile) -> DesignFile | None:
        """The file as the grid reads it: only the parts that may be conditions."""
        core = tuple(part for part in file.parts if _part_role(part) is None)
        if not core:
            return None
        counted_labels = tuple(part.value for part in file.parts if _part_role(part) == "replicate")
        label_places = tuple(part.ordinal for part in file.parts if _part_role(part) == "replicate")
        extension = next((part.value for part in file.parts if part.kind == "extension"), "")
        extras[file.entry_id] = (counted_labels, extension, label_places)
        return DesignFile(file.entry_id, file.name, file.class_key, file.stage, file.empty, core)

    cores: list[DesignFile] = []
    for file in classed:
        made = core_of(file)
        if made is None:
            left["no_condition_part"] += 1
        else:
            cores.append(made)
    if not cores:
        if loose:
            left["no_condition_class"] += len(loose)
            loose = []
        dominant = min(left, key=lambda code: (-left[code], code)) if left else "no_files"
        return none(dominant)
    layouts = Counter(_layout_of(file) for file in cores)
    chosen = min(layouts, key=lambda key: (-layouts[key], key))
    layout_files = sorted((f for f in cores if _layout_of(f) == chosen), key=lambda f: f.entry_id)
    if len(cores) > len(layout_files):
        left["another_layout"] = len(cores) - len(layout_files)
    elsewhere: dict[str, list[str]] = {}
    for file in cores:
        layout_key = _layout_of(file)
        if layout_key != chosen:
            elsewhere.setdefault(layout_key, []).append(file.name)
    other_layouts = tuple(
        OtherLayout(pattern=name_pattern(sorted(names)[:PATTERN_NAMES]).pattern, files=len(names))
        for _, names in sorted(elsewhere.items(), key=lambda item: (-len(item[1]), item[0]))[
            :OTHER_LAYOUTS_NAMED
        ]
    )
    first = layout_files[0]
    positions = [_Position(ordinal=place, kind=part.kind) for place, part in enumerate(first.parts)]
    roles = {position.ordinal: _role(position, layout_files) for position in positions}
    counters = [o for o, role in roles.items() if role == "replicate"]

    def seen_at(place: int) -> int:
        """Where a place of the core layout stands in the name as written (1-based)."""
        return first.parts[place].ordinal + 1

    # The factors: positions with two or more levels.
    candidates: list[int] = []
    for ordinal, role in roles.items():
        distinct = len({f.parts[ordinal].value for f in layout_files})
        if role == "factor" and 2 <= distinct <= parameters.factor_levels_at_most:
            candidates.append(ordinal)
    if not candidates:
        left["no_factor"] += len(layout_files)
        return none("no_factor", "no part of the main layout's names varies as a condition")

    # Parts that go together.
    absorbed: dict[int, list[tuple[int, dict[str, str], int]]] = {}
    taken: set[int] = set()
    if parameters.merge_parts:
        for head in candidates:
            if head in taken:
                continue
            for other in candidates:
                if other <= head or other in taken or other in absorbed:
                    continue
                pairing = _pairing(layout_files, head, other, parameters)
                if pairing is not None:
                    absorbed.setdefault(head, []).append((other, pairing[0], pairing[1]))
                    taken.add(other)
    heads = [ordinal for ordinal in candidates if ordinal not in taken]

    factors: list[Factor] = []
    places: list[dict[str, int]] = []
    for head in heads:
        levels, order, place = _levels_of(layout_files, head)
        parts = [f.parts[head] for f in layout_files]
        merged: list[MergeNote] = []
        shown = {level.value: level.shown for level in levels}
        labels = dict(shown)
        for other, matched, exceptions in absorbed.get(head, []):
            other_parts = [f.parts[other] for f in layout_files]
            by_other: dict[str, list[DesignFile]] = {}
            for f in layout_files:
                by_other.setdefault(f.parts[other].value, []).append(f)
            other_shown = {value: _spelling(members, other) for value, members in by_other.items()}
            for value in labels:
                labels[value] = f"{labels[value]} + {other_shown[matched[value]]}"
            examples = tuple(
                sorted(
                    f.name
                    for f in layout_files
                    if matched[f.parts[head].value] != f.parts[other].value
                )
            )[:3]
            merged.append(
                MergeNote(
                    position=seen_at(other),
                    kind=layout_files[0].parts[other].kind,
                    name=_factor_name(layout_files[0].parts[other].kind, other_parts),
                    observations=len(layout_files),
                    exceptions=exceptions,
                    examples=examples,
                )
            )
        levels = tuple(
            Level(value=level.value, shown=labels[level.value], files=level.files)
            for level in levels
        )
        kind = layout_files[0].parts[head].kind
        factors.append(
            Factor(
                factor_id="+".join(
                    f"p{seen_at(p)}" for p in (head, *(o for o, _, _ in absorbed.get(head, [])))
                ),
                positions=(seen_at(head), *(seen_at(o) for o, _, _ in absorbed.get(head, []))),
                kind=kind,
                part_kind=_part_kind(kind, {p.sense for p in parts}),
                name=_factor_name(kind, parts),
                levels=levels,
                order=order,
                ladder=_ladder(layout_files, head, levels) if order == "numeric" else None,
                merged=tuple(merged),
            )
        )
        places.append(place)

    # Cells.
    members: dict[tuple[int, ...], list[DesignFile]] = {}
    for file in layout_files:
        key = tuple(places[index][file.parts[head].value] for index, head in enumerate(heads))
        members.setdefault(key, []).append(file)
    # Files no condition class holds fill the cell their name gives, where its layout is the
    # grid's and each level is one the grid has: they never make a level or a cell of their own.
    placed = 0
    for file in loose:
        made = core_of(file)
        if made is None or _layout_of(made) != chosen:
            continue
        if any(made.parts[head].value not in places[index] for index, head in enumerate(heads)):
            continue
        members.setdefault(
            tuple(places[index][made.parts[head].value] for index, head in enumerate(heads)), []
        ).append(made)
        placed += 1
    if len(loose) > placed:
        left["no_condition_class"] += len(loose) - placed
    cells: list[Cell] = []
    for key in sorted(members):
        held = members[key]
        cells.append(
            Cell(
                levels=key,
                files=len(held),
                stages=_stages(held),
                replicates=_replicates(held, counters, extras),
                state=_state(held, parameters.early_stages),
            )
        )

    # Crossed or nested.
    pairs: list[FactorPair] = []
    for one, two in itertools.combinations(range(len(factors)), 2):
        found = {(key[one], key[two]) for key in members}
        pairs.append(_relation(factors[one], factors[two], found))
    nested = any(pair.relation == "nested" for pair in pairs)
    product = math.prod(len(factor.levels) for factor in factors)
    occupied = len(members)
    fill = None if nested or len(factors) < 2 else occupied / product

    # Combinations not found.
    listed, said = False, ""
    findings: list[GridQuestion] = []
    if len(factors) >= 2:
        if nested:
            said = "the design is nested, so combinations outside the nesting are not expected"
        elif product > parameters.cells_listed_at_most:
            said = f"{product} combinations are too many to list"
        else:
            listed = True
            for key in itertools.product(*(range(len(f.levels)) for f in factors)):
                if key not in members:
                    cells.append(
                        Cell(levels=key, files=0, stages=(), replicates=0, state=NOT_FOUND)
                    )
            cells.sort(key=lambda cell: cell.levels)
            absent = [cell for cell in cells if cell.state == NOT_FOUND]
            if len(absent) == 1 and product >= 4:
                combination = tuple(
                    factors[index].levels[level].shown
                    for index, level in enumerate(absent[0].levels)
                )
                names = ", ".join(f.name for f in factors)
                findings.append(
                    GridQuestion(
                        kind="combination_not_found",
                        combination=combination,
                        question=(
                            f"Combination not found: {' / '.join(combination)}. Every other "
                            f"combination of {names} was found. Was it not run, or is it "
                            "somewhere the engine did not read?"
                        ),
                    )
                )
    pattern = name_pattern([f.name for f in layout_files[:PATTERN_NAMES]])
    return ExperimentDesign(
        experiment_id=experiment_id,
        root_id=root_id,
        folder=folder,
        factors=tuple(factors),
        cells=tuple(cells),
        pairs=tuple(pairs),
        findings=tuple(findings),
        coverage=Coverage(
            files=total, in_grid=len(layout_files) + placed, left_out=tuple(sorted(left.items()))
        ),
        pattern=pattern.pattern,
        pattern_kinds=pattern.dimensions,
        not_found_listed=listed,
        not_found_said=said,
        fill=fill,
        merged_parts=sum(len(factor.merged) for factor in factors),
        replicate_positions=tuple(
            sorted({seen_at(o) for o in counters} | {o + 1 for o in extras[first.entry_id][2]})
        ),
        other_layouts=other_layouts,
    )
