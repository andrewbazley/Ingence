"""What kind of unit each folder is, and which unit every entry belongs to.

A search does not show the smallest folder that covers what was typed. It shows the unit that a
match belongs to. The projection build chooses the unit once per run, from what the run already
concluded. So a result is a high-level, self-contained unit, and a search never floods the page with
files. Final-stage files, such as GraphPad and Adobe Illustrator files, and analysis folders appear
beside the experiments with a lower rank.

The unit kinds are:

- ``experiment``: a folder that the engine concluded is an experiment. Two rules name them in
  ``experiment_rules``. ``experiment_folder_from_contents`` concludes by what a folder holds.
  ``experiment_folder_from_dated_name`` concludes by a dated name. The policy that the run's
  clustering rule carries (or ``policy`` here) makes their folders one set, exactly as the
  Groupings page does (:func:`~ingence.grouping.experiment_links.experiment_units`). Two fallbacks
  stand only where a run holds no conclusion of those rules. The first reads the contents'
  structure: the lowest folder that holds files at an early stage and, beneath it, files at a later
  one. The second reads the name: a folder whose own name carries a day's date and another word,
  read from the run's own tokens.
- ``analysis``: a folder that the structure rule concluded holds only files named for acquisitions
  kept elsewhere. Or a folder that is not an experiment and whose staged files are mostly at an
  analysis or pooled stage.
- ``program``: the outermost folder that a rule read as an installed program's. Everything beneath
  it belongs to it, so a search never lists a program's own files one by one.
- ``product``: a file made for people to read, such as a figure, a plot, a presentation, a Prism
  project, an Illustrator document or a pooled result. It is its own unit only where it belongs to
  no experiment or analysis folder. Inside one, the file is listed on that unit's card.
- ``folder``: anything else. The smallest-covering-folder rule applies to it.

Every entry's home is the nearest unit above it, with a program before an experiment before an
analysis folder. So a match inside ``Exp/results/`` lifts to ``Exp`` and is explained there
("matched inside results/"), and a match inside ``Fiji.app`` lifts to the program. Homes are a
table in the projection, so a query joins to them and never walks the tree.

Nothing here is a claim. A unit kind is a rendering choice. It derives from claims that the run
published and from the run's own tokens, the projection recomputes it, and the card shows it with
its basis ("an experiment: raw and analysed files beneath it"). Where the run holds any conclusion
of the experiment rules, the build does not consult the fallbacks at all, for any folder. A folder
that the rules did not conclude on is then an analysis folder, a program, or a plain folder, and
the card says which basis placed it.

The vocabulary stays in the Dictionary. "This kind of file is made for people to read" is a
``product: true`` attribute on the senses themselves (figure, plot, presentation, the office and
vector-graphics documents), as ``program: true`` marks a format that is a program.
:func:`scale_from_dictionary` reads the terms from there, so a laboratory's own pack can add one
with a line of YAML and a veto removes the lines. A file that the rules place at the start of the
workflow is never a product, whatever its name says.

A quality mark works the same way. It is a sense of the ``quality`` kind that carries
``mark: good``. The search reads it from a folder's own name only. It sits beside the doubt words
and never among them, because the doubt rules count only the terms they name. A word that the sense
names (``negated_by``) cancels it when the word stands directly before it, as in ``not good``.
Search ranks a marked unit above an equal one. It never says that an analysis is good, only that its
name says so.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ingence.dictionary.normalize import is_month_sense
from ingence.storage.catalog import sql_path

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ingence.dictionary.store import DictionaryStore
    from ingence.storage.catalog import Catalog

__all__ = [
    "DEFAULT_SCALE",
    "MONTH",
    "UNIT_KIND_ORDER",
    "ScaleParameters",
    "UnitKind",
    "dated_name",
    "day_precise",
    "scale_from_dictionary",
    "word_reading",
    "write_units",
]


class UnitKind(StrEnum):
    """What a search result stands for."""

    EXPERIMENT = "experiment"
    ANALYSIS = "analysis"
    PRODUCT = "product"
    FOLDER = "folder"
    PROGRAM = "program"


#: The order in which kinds rank when everything else is equal. Experiments come first. An
#: installed program comes last, because a person rarely searches for one.
UNIT_KIND_ORDER: tuple[UnitKind, ...] = (
    UnitKind.EXPERIMENT,
    UnitKind.ANALYSIS,
    UnitKind.PRODUCT,
    UnitKind.FOLDER,
    UnitKind.PROGRAM,
)


@dataclass(frozen=True, slots=True)
class ScaleParameters:
    """Every choice that the unit kinds rest on, each a parameter."""

    #: The experiment rules. The first concludes by what a folder holds. The second concludes by a
    #: folder's name. The third carries an approved group's claims onto files. The card's
    #: ``approved_groups`` count uses the third, and it never places a unit. The conclusions of
    #: these rules outrank both fallbacks.
    experiment_rules: tuple[str, ...] = (
        "experiment_folder_from_contents",
        "experiment_folder_from_dated_name",
        "experiments_approved_together",
    )
    #: The experiment policy that makes the folders of the first two rules one set. None reads the
    #: policy that the run's clustering rule carries, else the engine's default. The Groupings page
    #: reads it the same way.
    policy: str | None = None
    #: Rules whose resolved claims place a file at a workflow stage. The list holds the shipped
    #: Rulebook's stage rules and those of later revisions, which replace some shipped rules. So the
    #: search reads a project on any of them. ``tags`` and the ``stage:`` filter read the same list.
    stage_rules: tuple[str, ...] = (
        "acquisition_stage_from_format",
        "pipeline_stage_from_name",
        "later_stage_from_name",
        "stage_inherited_from_folder",
        "analysis_stage_from_derived_sense",
        "stage_from_name",
        "raw_data_from_acquisition_format",
        # A stage read from a file's own tags, and an export read from a name that ends in two
        # extensions.
        "stage_from_writer_tags",
        "export_stage_from_inner_format",
    )
    #: Stages that mean "the data as it came off the instrument" and "something made from it".
    #: They are Dictionary terms of the ``stage`` kind. The declared transitions give this order,
    #: and the build reads it from there.
    early_stages: tuple[str, ...] = ("raw", "acquisition")
    late_stages: tuple[str, ...] = ("processed", "analysis", "pooled")
    analysis_stages: tuple[str, ...] = ("analysis", "pooled")
    #: A folder that is not an experiment is an analysis folder where at least this share of its
    #: staged files are at an analysis stage and at least ``analysis_minimum`` files are staged.
    analysis_share: float = 0.8
    analysis_minimum: int = 2
    #: Dictionary terms whose binding makes a file a product. These are the senses that carry
    #: ``product: true``, and :func:`scale_from_dictionary` fills the list in. An empty list means
    #: that no term makes a product, which is what a veto of the product senses looks like.
    product_terms: tuple[str, ...] = ()
    #: Dictionary terms of the quality kind marked ``mark: good``. A folder whose own name binds one
    #: is marked good. An empty list means that no unit is marked.
    good_terms: tuple[str, ...] = ()
    #: The spellings of those terms, and the words that cancel the mark when one stands directly
    #: before it (``not good``). The Dictionary declares the words as ``negated_by``.
    good_surfaces: tuple[str, ...] = ()
    good_negations: tuple[str, ...] = ()
    #: Stages whose files are products too. The ``pooled`` stage holds ``final`` results and
    #: meta-analyses.
    product_stages: tuple[str, ...] = ("pooled",)
    #: A SQL ``LIKE`` pattern for the rule that finds installed programs. The search matches the
    #: scope findings as the Dashboard's Programs page does.
    program_rule_like: str = "%installed_software%"
    #: Whether the two fallbacks (structure, then a dated name) stand where a run holds no
    #: conclusion of the experiment rules. With this off, such a run has no experiment units.
    use_fallbacks: bool = True
    #: Flag rules whose findings on a unit's own name mark the unit doubted. A doubted unit ranks
    #: below an equal one and the page shows it with its doubt. Doubt never hides a unit.
    doubt_rules: tuple[str, ...] = ("doubt_in_a_folder_name", "repudiated_by_name")


DEFAULT_SCALE = ScaleParameters()


#: What :func:`dated_name` reads a word bound to one of the Dictionary's months as.
MONTH = "month"


def word_reading(theme_kind: str | None, canonical: str | None) -> str | None:
    """Return what a bound word is to :func:`dated_name`.

    The result is :data:`MONTH` for one of the Dictionary's month senses
    (:func:`~ingence.dictionary.normalize.is_month_sense`, as the tokeniser reads month words).
    Otherwise it is the word's theme kind. So no other date-kind word, such as ``today``, ``am`` or
    a weekday, is taken for a month.
    """
    return MONTH if is_month_sense(theme_kind, canonical) else theme_kind


def dated_name(components: Sequence[tuple[str, str | None, float | None]]) -> bool:
    """Return whether a folder's own name carries a day's date and another word.

    ``components`` are the name's (component type, reading, magnitude) triples, as the run stored
    them. A word's reading is the value of :func:`word_reading`. A day's date is a date component at
    day precision, such as ``2031-09-23`` or ``17Jun2031``. In a run read by an older tokeniser, a
    day's date is a month word beside a day number: ``17Jun2031`` read as ``17``, ``Jun`` and
    ``2031``, with the Dictionary binding ``Jun`` to a month. Another word is any word that is not
    a month, including another date-kind word. ``2031-09-23_saturday`` is dated, and
    ``saturday_17_kelt`` is not. A name of a year or a month alone (``2031``, ``Jun2031``) is a
    calendar folder that holds experiments. It is not an experiment.
    """
    day_date = any(kind == "date" and reading is None for kind, reading, _ in components)
    month_word = any(kind == "word" and reading == MONTH for kind, reading, _ in components)
    day_number = any(
        kind == "number" and magnitude is not None and 1 <= magnitude <= 31
        for kind, _, magnitude in components
    )
    other_word = any(kind == "word" and reading != MONTH for kind, reading, _ in components)
    return other_word and (day_date or (month_word and day_number))


def _marks(values: Sequence[str]) -> str:
    return ", ".join("?" for _ in values) or "NULL"


def _named_experiments(
    catalog: Catalog, staging: Path, *, day_precision: Callable[[str], bool]
) -> None:
    """Write the folders whose own name is dated to ``dated.parquet`` for the SQL below."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    # The run's own month senses decide. A word is a month where its sense is one of the
    # Dictionary's months, and never because its theme kind is a date's.
    canonical = dict(catalog.query("SELECT term_id, canonical FROM dictionary_entries").fetchall())
    rows = catalog.query(
        f"""
        SELECT t.entry_id, t.component_type, t.token, t.theme_kind, t.magnitude, t.term_id
        FROM {sql_path(staging / "terms.parquet")} t
        WHERE t.entry_type = 'directory' AND t.source = 'name'
        ORDER BY t.entry_id
        """  # noqa: S608
    ).fetchall()
    by_folder: dict[str, list[tuple[str, str | None, float | None]]] = {}
    for entry_id, component_type, token, theme_kind, magnitude, term_id in rows:
        # A date component is kept only at day precision. A month alone is a calendar folder.
        if component_type == "date" and not day_precision(str(token)):
            continue
        reading = (
            None if component_type == "date" else word_reading(theme_kind, canonical.get(term_id))
        )
        by_folder.setdefault(str(entry_id), []).append((str(component_type), reading, magnitude))
    dated = sorted(folder for folder, found in by_folder.items() if dated_name(found))
    pq.write_table(pa.table({"folder_id": dated}), staging / "dated.parquet")


def _rule_experiments(catalog: Catalog, staging: Path, *, parameters: ScaleParameters) -> int:
    """Write ``experiments.parquet``: the folders that the experiment rules concluded, as one set.

    The policy in force makes the structure rule's folders and the name rule's folders one set, the
    way the Groupings page makes them (:func:`experiment_units`). A search and the page that a
    person answered on then agree about what an experiment is. Each row carries what the structure
    rule concluded (``experiment``, ``acquisitions`` or ``analysis``), or ``named`` for the name
    rule's alone. The function returns the number of rows written. Zero means that the run holds no
    such conclusion, and the fallbacks may stand.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ingence.api.routes.groupings import EXPERIMENT_GROUPS_PREDICATE, rule_parameters
    from ingence.grouping.experiment_links import DEFAULT_POLICY, experiment_units

    # The run's own clustering rule names the experiment rules where it declares them, as the
    # Groupings page reads them. So a revision that renames a rule moves search and Groupings
    # together. Otherwise the parameter's pair of rules applies.
    _, clustering = rule_parameters(catalog, EXPERIMENT_GROUPS_PREDICATE)
    declared = clustering.get("experiment_rules")
    structure_rule, name_rule = (
        (str(declared[0]), str(declared[1]))
        if isinstance(declared, list) and len(declared) == 2
        else parameters.experiment_rules[:2]
    )
    concluded = catalog.query(
        """
        SELECT c.rule_id, c.object_reference, e.entry_id, e.relative_path
        FROM claims c JOIN entries e ON e.entry_id = c.subject
        WHERE c.rule_id IN (?, ?) AND c.resolution_status = 'resolved'
          AND c.object_reference IS NOT NULL AND e.entry_type = 'directory' AND NOT e.is_deleted
        ORDER BY e.relative_path
        """,
        [structure_rule, name_rule],
    ).fetchall()
    structure: dict[str, str] = {}
    named: list[str] = []
    identifier: dict[str, str] = {}
    for rule_id, kind, entry_id, path in concluded:
        identifier[str(path)] = str(entry_id)
        if rule_id == structure_rule:
            structure[str(path)] = str(kind)
        else:
            named.append(str(path))
    policy = parameters.policy
    if policy is None:
        policy = str(clustering.get("policy") or DEFAULT_POLICY)
    try:
        kept = experiment_units(structure, named, policy=policy)
    except ValueError:
        kept = experiment_units(structure, named)
    rows = [(identifier[path], structure.get(path, "named")) for path in kept if path in identifier]
    pq.write_table(
        pa.table({"folder_id": [r[0] for r in rows], "kind": [r[1] for r in rows]}),
        staging / "experiments.parquet",
    )
    return len(rows)


def _units_sql(
    parameters: ScaleParameters, staging: Path, *, fallbacks: bool
) -> tuple[str, list[Any]]:
    """Return the SQL and its values for one row per folder: its unit kind and the basis for it."""
    closure = sql_path(staging / "closure.parquet")
    folders = sql_path(staging / "folders.parquet")
    dated = sql_path(staging / "dated.parquet")
    experiments = sql_path(staging / "experiments.parquet")
    terms = sql_path(staging / "terms.parquet")
    stages = parameters.stage_rules
    early, late, analysed = (
        parameters.early_stages,
        parameters.late_stages,
        parameters.analysis_stages,
    )
    doubt_rules = parameters.doubt_rules
    good_terms = parameters.good_terms
    sql = f"""
    WITH staged AS (
        SELECT DISTINCT cl.subject AS entry_id, cl.object_reference AS stage
        FROM claims cl
        WHERE cl.rule_id IN ({_marks(stages)}) AND cl.resolution_status = 'resolved'
    ),
    beneath AS (
        SELECT c.ancestor_id AS folder_id,
               bool_or(s.stage IN ({_marks(early)})) AS early,
               bool_or(s.stage IN ({_marks(late)})) AS late,
               count(DISTINCT s.entry_id) FILTER (WHERE s.stage IN ({_marks(analysed)}))
                   AS analysed,
               count(DISTINCT s.entry_id) AS staged
        FROM {closure} c JOIN staged s ON s.entry_id = c.entry_id
        GROUP BY 1
    ),
    mixed AS (SELECT folder_id FROM beneath WHERE early AND late),
    by_structure AS (
        -- The lowest folders that hold both: none of the folder's child folders holds both.
        SELECT m.folder_id FROM mixed m
        WHERE NOT EXISTS (
            SELECT 1 FROM mixed inner_m JOIN {folders} f ON f.folder_id = inner_m.folder_id
            WHERE f.parent_id = m.folder_id
        )
    ),
    by_rule AS (SELECT folder_id FROM {experiments} WHERE kind <> 'analysis'),
    analysed_by_rule AS (SELECT folder_id FROM {experiments} WHERE kind = 'analysis'),
    programs AS (
        SELECT DISTINCT substr(fi.subject, 8) AS folder_id
        FROM findings fi
        WHERE fi.subject_kind = 'scope' AND fi.rule_id LIKE ?
          AND starts_with(fi.subject, 'folder:')
    ),
    outermost_programs AS (
        SELECT p.folder_id FROM programs p
        WHERE NOT EXISTS (
            SELECT 1 FROM {closure} c JOIN programs above ON above.folder_id = c.ancestor_id
            WHERE c.entry_id = p.folder_id
        )
    ),
    doubted AS (
        SELECT DISTINCT CASE WHEN starts_with(fi.subject, 'folder:') THEN substr(fi.subject, 8)
                             ELSE fi.subject END AS folder_id
        FROM findings fi WHERE fi.rule_id IN ({_marks(doubt_rules)})
    ),
    good AS (
        -- A folder's own name carries a word of the quality kind marked good, not cancelled by a
        -- word standing directly before it ("not good"). A mark, not a claim.
        SELECT DISTINCT t.entry_id AS folder_id FROM {terms} t
        JOIN {folders} f ON f.folder_id = t.entry_id
        WHERE t.entry_type = 'directory' AND t.source = 'name'
          AND t.resolution_status = 'resolved' AND t.term IN ({_marks(good_terms)})
          AND NOT regexp_matches(lower(f.name), ?)
    ),
    kinded AS (
        SELECT f.folder_id,
               CASE
                   WHEN f.folder_id IN (SELECT folder_id FROM outermost_programs) THEN 'program'
                   WHEN f.folder_id IN (SELECT folder_id FROM by_rule) THEN 'experiment'
                   WHEN f.folder_id IN (SELECT folder_id FROM analysed_by_rule) THEN 'analysis'
                   WHEN ? AND f.folder_id IN (SELECT folder_id FROM by_structure) THEN 'experiment'
                   WHEN ? AND f.folder_id IN (SELECT folder_id FROM {dated}) THEN 'experiment'
                   WHEN b.staged >= ? AND b.analysed >= ? * b.staged THEN 'analysis'
                   ELSE 'folder'
               END AS unit_kind,
               CASE
                   WHEN f.folder_id IN (SELECT folder_id FROM outermost_programs)
                       THEN 'an installed program'
                   WHEN f.folder_id IN (SELECT folder_id FROM by_rule)
                       THEN 'the rules concluded it is an experiment'
                   WHEN f.folder_id IN (SELECT folder_id FROM analysed_by_rule)
                       THEN 'the rules concluded it holds analyses of files kept elsewhere'
                   WHEN ? AND f.folder_id IN (SELECT folder_id FROM by_structure)
                       THEN 'raw and later-stage files beneath it'
                   WHEN ? AND f.folder_id IN (SELECT folder_id FROM {dated})
                       THEN 'its name carries a day''s date and another word'
                   WHEN b.staged >= ? AND b.analysed >= ? * b.staged
                       THEN 'most of its staged files are analyses'
                   ELSE NULL
               END AS basis,
               coalesce(b.early, false) AS early,
               coalesce(b.late, false) AS late,
               coalesce(b.staged, 0) AS staged,
               coalesce(b.analysed, 0) AS analysed
        FROM {folders} f LEFT JOIN beneath b ON b.folder_id = f.folder_id
    )
    SELECT k.*, k.folder_id IN (SELECT folder_id FROM doubted) AS doubted,
           k.folder_id IN (SELECT folder_id FROM good) AS good
    FROM kinded k ORDER BY k.folder_id
    """  # noqa: S608
    values: list[Any] = [
        *stages,
        *early,
        *late,
        *analysed,
        parameters.program_rule_like,
        *doubt_rules,
        *good_terms,
        _negated_pattern(parameters),
        fallbacks,
        fallbacks,
        parameters.analysis_minimum,
        parameters.analysis_share,
        fallbacks,
        fallbacks,
        parameters.analysis_minimum,
        parameters.analysis_share,
    ]
    return sql, values


def _homes_sql(staging: Path) -> str:
    """Return the SQL for every entry's home unit.

    The home is a program, else the nearest experiment, else the nearest analysis folder.
    """
    closure = sql_path(staging / "closure.parquet")
    units = sql_path(staging / "units.parquet")
    return f"""
    WITH reach AS (
        SELECT entry_id, entry_id AS ancestor_id, 0 AS distance FROM entries WHERE NOT is_deleted
        UNION ALL
        SELECT entry_id, ancestor_id, distance FROM {closure}
    ),
    ranked AS (
        SELECT r.entry_id, u.folder_id AS home_id, u.unit_kind AS home_kind, r.distance,
               row_number() OVER (
                   PARTITION BY r.entry_id
                   ORDER BY CASE u.unit_kind WHEN 'program' THEN 0 WHEN 'experiment' THEN 1
                                             ELSE 2 END,
                            r.distance
               ) AS place
        FROM reach r JOIN {units} u ON u.folder_id = r.ancestor_id
        WHERE u.unit_kind IN ('program', 'experiment', 'analysis')
    )
    SELECT entry_id, home_id, home_kind, distance FROM ranked WHERE place = 1
    ORDER BY entry_id
    """  # noqa: S608


def _negated_pattern(parameters: ScaleParameters) -> str:
    """Return a pattern that finds a cancelling word directly before a good mark in a name.

    Where the Dictionary declares no cancelling word, the pattern matches nothing.
    """
    import re

    if not parameters.good_surfaces or not parameters.good_negations:
        return "(?!)"
    negations = "|".join(re.escape(word.casefold()) for word in parameters.good_negations)
    surfaces = "|".join(re.escape(word.casefold()) for word in parameters.good_surfaces)
    return rf"(^|[^a-z0-9])({negations})[ _.\-]*({surfaces})([^a-z0-9]|$)"


def _products_sql(parameters: ScaleParameters, staging: Path) -> tuple[str, list[Any]]:
    """Return the SQL and its values for files made for people to read.

    Each file comes with what made it a product and its home unit, if any.
    """
    terms = sql_path(staging / "terms.parquet")
    homes = sql_path(staging / "homes.parquet")
    product_terms, product_stages = parameters.product_terms, parameters.product_stages
    stage_rules = parameters.stage_rules
    sql = f"""
    WITH by_term AS (
        SELECT entry_id, min(term) AS made_as FROM {terms}
        WHERE entry_type = 'file' AND resolution_status = 'resolved'
          AND term IN ({_marks(product_terms)})
        GROUP BY entry_id
    ),
    by_stage AS (
        SELECT cl.subject AS entry_id, min(cl.object_reference) AS made_as FROM claims cl
        WHERE cl.resolution_status = 'resolved' AND cl.rule_id IN ({_marks(stage_rules)})
          AND cl.object_reference IN ({_marks(product_stages)})
        GROUP BY cl.subject
    ),
    made AS (
        SELECT entry_id, made_as FROM by_term
        UNION ALL
        SELECT entry_id, made_as FROM by_stage WHERE entry_id NOT IN (SELECT entry_id FROM by_term)
    )
    SELECT m.entry_id, e.parent_entry_id AS parent_id, e.name, e.relative_path, e.size_bytes,
           m.made_as, h.home_id, h.home_kind,
           EXISTS (SELECT 1 FROM findings fi WHERE fi.subject = m.entry_id
                   AND fi.rule_id IN ({_marks(parameters.doubt_rules)})) AS doubted
    FROM made m JOIN entries e ON e.entry_id = m.entry_id AND e.entry_type = 'file'
    LEFT JOIN {homes} h ON h.entry_id = m.entry_id
    WHERE h.home_kind IS DISTINCT FROM 'program'
      -- A file the rules place at the start of the workflow is data, whatever its name says.
      -- ``figure_01.nd2`` is an acquisition and not a figure.
      AND NOT EXISTS (
          SELECT 1 FROM claims cl WHERE cl.subject = m.entry_id
            AND cl.resolution_status = 'resolved' AND cl.rule_id IN ({_marks(stage_rules)})
            AND cl.object_reference IN ({_marks(parameters.early_stages)})
      )
    ORDER BY m.entry_id
    """  # noqa: S608
    return sql, [
        *product_terms,
        *stage_rules,
        *product_stages,
        *parameters.doubt_rules,
        *stage_rules,
        *parameters.early_stages,
    ]


def scale_from_dictionary(
    store: DictionaryStore, base: ScaleParameters = DEFAULT_SCALE
) -> ScaleParameters:
    """Return ``base`` with the product and quality-mark terms that the Dictionary declares.

    A product is a sense that carries ``product: true``. A mark is a sense of the ``quality`` kind
    that carries ``mark: good``. The function reads them in term order, so two runs of one
    Dictionary give one parameter list. The projection records the list in its ``meta.json``.
    """
    products = sorted({sense.term for sense in store.senses() if sense.properties.get("product")})
    marks = [
        sense
        for sense in store.senses()
        if sense.theme_kind == "quality" and sense.properties.get("mark") == "good"
    ]
    return replace(
        base,
        product_terms=tuple(products),
        good_terms=tuple(sorted({sense.term for sense in marks})),
        good_surfaces=tuple(sorted({str(s) for sense in marks for s in sense.surfaces})),
        good_negations=tuple(
            sorted(
                {
                    str(word)
                    for sense in marks
                    for word in (sense.properties.get("negated_by") or ())
                }
            )
        ),
    )


def day_precise(token: str) -> bool:
    """Return whether a date component names a day: one day, or a day and a month that could swap.

    The value of a swapped date names both readings, such as ``2031-03-04 or 2031-04-03``. Each
    reading is a day.
    """
    return all(part.count("-") == 2 for part in token.split(" or "))


def write_units(
    catalog: Catalog,
    staging: Path,
    *,
    parameters: ScaleParameters = DEFAULT_SCALE,
    day_precision: Callable[[str], bool] | None = None,
) -> dict[str, int]:
    """Write ``dated``, ``units``, ``homes`` and ``products`` beside the projection's tables.

    ``staging`` already holds ``terms``, ``closure`` and ``folders``. The function returns row
    counts by kind, for the projection's ``meta.json`` and for the build's report.
    """

    from_rules = _rule_experiments(catalog, staging, parameters=parameters)
    fallbacks = parameters.use_fallbacks and from_rules == 0
    if fallbacks:
        _named_experiments(catalog, staging, day_precision=day_precision or day_precise)
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq

        pq.write_table(
            pa.table({"folder_id": pa.array([], pa.string())}), staging / "dated.parquet"
        )
    sql, values = _units_sql(parameters, staging, fallbacks=fallbacks)
    catalog.query(
        f"COPY ({sql}) TO {sql_path(staging / 'units.parquet')} (FORMAT PARQUET)",
        values,
    )
    catalog.query(
        f"COPY ({_homes_sql(staging)}) TO {sql_path(staging / 'homes.parquet')} (FORMAT PARQUET)"
    )
    sql, values = _products_sql(parameters, staging)
    catalog.query(
        f"COPY ({sql}) TO {sql_path(staging / 'products.parquet')} (FORMAT PARQUET)",
        values,
    )
    counted = catalog.query(
        f"SELECT unit_kind, count(*) FROM {sql_path(staging / 'units.parquet')} "  # noqa: S608
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    counts = {str(kind): int(number) for kind, number in counted}
    row = catalog.query(
        f"SELECT count(*), count(*) FILTER (WHERE home_id IS NULL) "  # noqa: S608
        f"FROM {sql_path(staging / 'products.parquet')}"
    ).fetchone()
    counts["experiments_from_rules"] = from_rules
    counts["products"] = int(row[0]) if row else 0
    counts["products_on_their_own"] = int(row[1]) if row else 0
    return counts
