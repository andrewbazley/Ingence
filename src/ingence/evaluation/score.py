"""The engine's figures against an answer key, family by family and rule by rule.

How a pool is scored. A temporary project is created over the pool with every shipped
domain pack (``init --all-domains``), the pool is indexed and dived at the ``deep`` depth (copies
by content and references need the bytes read), and the Rulebook is evaluated once per column:
the shipped rules alone, and the shipped rules with each project revision a column names. The
census and the dive run once and each column infers on its own copy of that project, so every
column reads one Index. The run is read through the engine's own storage (``open_catalog``),
never by parsing what a command printed.

What is counted, and never summed into one score. For a value family, per path: right,
wrong (a different value; a value where the key says there is none; none where the key has a
value), no claim (unknown or unexamined; the engine's own tallies split them) and conflicting (the
rule examined and found two answers), each counted apart; for a pair family, per pair: right,
wrong, the engine's own "no" (a blocked candidate, a finding a fired defeater set aside), missed
and conflicting. Where the key marks something undecidable, a conclusion, a "no", a conflict and
silence are each counted apart from the rest. Precision, recall and coverage carry a Wilson
interval; calibration is the same counts split by the tier the engine gave. The Review
queue's size is the number of questions it puts to a person.

What cannot be counted here: the decisions a person resolves per minute of review, which needs a
person and a clock; the private half measures it. The report says so rather than inventing a figure.

Teaching the engine's words (:func:`measure_teaching`) tests the hypothesis that
a first round of answers about words (what a word means, which spellings are one thing) sharpens the
groupings. An oracle teacher answers the first ``k`` words of a Words queue from the answer key's
words family (:func:`teach_words`), authored as the key and written to a scratch project's Record as
a person's answers are; the project then re-runs as the Dashboard's Re-run does (a rebind, then
inference); and each ``k`` is read for the figures the scorer already counts and for the reading
figure, the themes' agreement with the key's lines of work and their stability across seeds, the
experiments no theme placed, the key's lattice cores found whole, and the experiments found by
their contents.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence import SCHEMA_VERSION, __version__
from ingence.core.destinations import write_atomically
from ingence.core.provenance import engine_commit
from ingence.evaluation.key import FAMILIES, PairFamily, ValueFamily
from ingence.infer.workflow_suggest import STAGE_ORDER
from ingence.record.precision import Z_95, wilson_interval

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from ingence.core.project import Project
    from ingence.evaluation.key import AnswerKey

__all__ = [
    "APPROVED_SUFFIX",
    "APPROVER",
    "CLUSTERING_RULE",
    "CONCLUDED",
    "CONFLICTING",
    "EMPTY_READS_AS",
    "LATTICE_RULE",
    "PAGE_STATES",
    "PAIR_ANSWERS",
    "PAIR_RULES",
    "REJECTED",
    "REPORT_FORMAT",
    "STAGE_READS_AS",
    "VALUE_RULES",
    "WORDS_AS_CHOICES",
    "ColumnRun",
    "KeyPlacementError",
    "Reading",
    "RunReadings",
    "Teaching",
    "WordAnswer",
    "WordsRow",
    "cores_of",
    "evaluate",
    "group_readings",
    "key_lines",
    "lattice_terms",
    "made_by_a_rewrite",
    "measure_teaching",
    "measure_teaching_arms",
    "pairs_of",
    "pinned_figures",
    "rows_of_queue",
    "run_columns",
    "score_key",
    "score_pairs",
    "score_project",
    "score_values",
    "set_aside",
    "summary",
    "taught_summary",
    "teach_words",
    "together_pairs",
    "words_queue",
    "words_queue_rows",
    "words_rows",
]

REPORT_FORMAT = "ingence-evaluation-report"
REPORT_VERSION = 1
#: What a measurement of teaching words is called in its report.
TEACHING_FORMAT = "ingence-teaching-measurement"
TEACHING_VERSION = 1

CONCLUDED = "concluded"
CONFLICTING = "conflicting"
#: The engine's own "no": what a pair reading's tier says when the engine refused the pair.
REJECTED_STATE = "rejected"
#: The tiers that are a refusal: a candidate a constraint blocked (written as rejected, with the
#: conflict recorded), and a finding a fired defeater set aside.
REJECTED = frozenset({"blocked", "set_aside"})
#: How many paths or pairs each outcome lists as examples in the report.
EXAMPLES = 5
#: The row that reads every rule of a family together.
EVERY_RULE = "every rule"

#: The engine's stage senses, read as the workflow's three words (raw, processed,
#: analysis). An instrument format's ``acquisition`` is the raw data. Every other sense is itself.
STAGE_READS_AS = {"acquisition": "raw"}
#: The engine's stage senses read as the key's three words for a chain: where data enters is raw,
#: what is made ready is processed, and everything after is analysis.
CHAIN_READS_AS = {
    "acquisition": "raw",
    "preprocessed": "processed",
    "intermediate": "processed",
    "processed": "processed",
    "quantification": "analysis",
    "analysis": "analysis",
    "pooled": "analysis",
    "summary": "analysis",
    "report": "analysis",
    "export": "analysis",
}
#: What ``content_unreadable`` raises about an empty file, read as the key's two words: an empty
#: file untouched for long with nothing beside it is an empty file on its own.
EMPTY_READS_AS = {"empty_and_old": "empty"}

#: Where each value family's readings come from: claims or findings of these rules, or the census's
#: own reading of a name (``tokeniser``), which is literal and carries no rule.
VALUE_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "stage": (
        "claims",
        (
            "stage_from_name",
            "pipeline_stage_from_name",
            "acquisition_stage_from_format",
            "analysis_stage_from_derived_sense",
            "stage_inherited_from_folder",
            # Revision 5: the stage a file's own tags give it, and an export read from
            # a name ending in two extensions.
            "stage_from_writer_tags",
            "export_stage_from_inner_format",
        ),
    ),
    "experiment_folder": (
        "claims",
        ("experiment_folder_from_contents", "experiment_folder_from_dated_name"),
    ),
    "program": ("findings", ("installed_software_in_folder",)),
    "empty_file": ("findings", ("content_unreadable",)),
    "contradicted_extension": (
        "findings",
        ("extension_contradicts_content", "content_contradicts_extension"),
    ),
    "name_date": ("components", ("tokeniser",)),
    # A projection over the run, never a claim: the stages each experiment's
    # files reached (``completeness/workflow_steps.py``), the format rule's ``acquisition`` read
    # as the key's ``raw``.
    "workflow": ("projection", ("workflow_steps",)),
}

#: The rule whose findings are the clustering's proposed groups, one value per group.
CLUSTERING_RULE = "experiments_may_belong_together"
#: The rule whose findings carry the group lattice's questions.
LATTICE_RULE = "group_resolution_deviation"
#: How a lattice question is spelled (``lattice_value`` in ``grouping/resolutions.py``):
#: ``a group: <terms>`` asks about a most general set, ``a group: <terms> + <added>`` about
#: what a derivation adds once its parents are approved. A term never holds a space.
_LATTICE_MARK = "a group: "
_LATTICE_ADDS = " + "

#: Where each pair family's pairs come from: the classes grouping rules form (members of one class
#: pair up), the groups a flagging rule proposes, or every candidate and published edge of a
#: primitive, whichever rule proposed it.
PAIR_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "replicate": ("classes", ("replicates_of_one_condition", "same_experimental_condition")),
    # Copies by content only: agreeing metadata is agreement, not a copy, and two files of
    # one name, size and time are counted among the outputs no family scores.
    "copy": ("classes", ("identical_by_content_digest", "copies_by_sampled_digest")),
    # The clustering's proposals, and the group lattice's questions: each
    # set of terms it asks about is a proposed group of every experiment holding the set.
    "group": ("finding_groups", (CLUSTERING_RULE, LATTICE_RULE)),
    "reference": ("edges", ("references",)),
    "derivation": ("edges", ("derives",)),
    # A projection, the groups of repeats (``grouping/repeats.py``), each
    # group every pair of its members.
    "repeats": ("projection", ("repeats",)),
}


@dataclass(frozen=True)
class Reading:
    """What one rule said about one path: a value (``None``: that there is none), or a conflict."""

    value: str | None
    state: str
    tier: str


# ---------------------------------------------------------------------------
# The arithmetic.
# ---------------------------------------------------------------------------


def _proportion(successes: int, trials: int) -> dict[str, Any]:
    interval = wilson_interval(successes, trials, z=Z_95)
    return {
        "successes": successes,
        "trials": trials,
        "low": None if interval is None else round(interval[0], 4),
        "high": None if interval is None else round(interval[1], 4),
    }


def score_values(family: ValueFamily, readings: Mapping[str, Reading]) -> dict[str, Any]:
    """Count one rule's readings against a value family.

    Precision is over every answer that could be wrong: a value the engine concluded, and its saying
    there is none where the key has a value (a folder read as acquisitions where the key says
    experiment is an error). Its saying there is none where the key says none is right, and outside
    precision. Recall and coverage are over the paths the key gives a value. No claim (unknown or
    unexamined), a conflict, and any answer on an undecidable path are each counted apart, and none
    of them is an error.
    """
    counts: Counter[str] = Counter()
    by_tier: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)
    undecidable = set(family.undecidable)
    ignored = set(family.ignored)

    def note(outcome: str, path: str, said: str | None = None, wanted: str | None = None) -> None:
        counts[outcome] += 1
        if outcome != "correct" and len(examples[outcome]) < EXAMPLES:
            examples[outcome].append(path if said is None else f"{path}: {said} (key: {wanted})")

    for path, reading in sorted(readings.items()):
        if path in ignored:
            counts["ignored"] += 1
        elif path in undecidable:
            if reading.state == CONCLUDED:
                note("undecidable_decided", path, reading.value or "none", "undecidable")
            else:
                note("undecidable_conflicting", path)
        elif path not in family.expected:
            counts["outside"] += 1
    for path, wanted in sorted(family.expected.items()):
        held = readings.get(path)
        if held is None:
            if wanted is not None:
                note("no_claim", path)
            continue
        reading = held
        if reading.state != CONCLUDED:
            if wanted is not None:
                note("conflicting", path)
            continue
        if reading.value is None:
            if wanted is not None:
                note("none_where_value", path, "none", wanted)
                by_tier[reading.tier]["wrong"] += 1
            continue
        if wanted is None:
            note("wrong_where_none", path, reading.value, wanted)
            by_tier[reading.tier]["wrong"] += 1
        elif reading.value == wanted:
            note("correct", path)
            by_tier[reading.tier]["correct"] += 1
        else:
            note("wrong_value", path, reading.value, wanted)
            by_tier[reading.tier]["wrong"] += 1
    expected = family.expected_count()
    wrong = counts["wrong_value"] + counts["wrong_where_none"] + counts["none_where_value"]
    answered = counts["correct"] + counts["wrong_value"] + counts["none_where_value"]
    silent = len(undecidable) - counts["undecidable_decided"] - counts["undecidable_conflicting"]
    return {
        "expected": expected,
        "concluded": counts["correct"] + counts["wrong_value"] + counts["wrong_where_none"],
        "correct": counts["correct"],
        "wrong": wrong,
        "wrong_value": counts["wrong_value"],
        "wrong_where_none": counts["wrong_where_none"],
        "none_where_value": counts["none_where_value"],
        "no_claim": counts["no_claim"],
        "conflicting": counts["conflicting"],
        "undecidable": len(undecidable),
        "undecidable_decided": counts["undecidable_decided"],
        "undecidable_conflicting": counts["undecidable_conflicting"],
        "undecidable_no_claim": silent,
        "outside": counts["outside"],
        "ignored": counts["ignored"],
        "precision": _proportion(counts["correct"], counts["correct"] + wrong),
        "recall": _proportion(counts["correct"], expected),
        "coverage": _proportion(answered, expected),
        "by_tier": {tier: _tier_counts(by_tier[tier]) for tier in sorted(by_tier)},
        "examples": {outcome: examples[outcome] for outcome in sorted(examples)},
    }


def _tier_counts(counted: Counter[str]) -> dict[str, int]:
    return {"correct": counted["correct"], "wrong": counted["wrong"]}


def _canonical(pair: tuple[str, str], *, directed: bool) -> tuple[str, str]:
    first, second = pair
    return pair if directed or first <= second else (second, first)


def score_pairs(family: PairFamily, found: Mapping[tuple[str, str], str]) -> dict[str, Any]:
    """Count one rule's pairs, each with the tier the engine gave it, against a pair family.

    A pair whose tier is in ``REJECTED`` (a blocked candidate, written as rejected with its
    conflict recorded; a finding set aside by a fired defeater) is the engine's own "no": right
    where the key has no such pair, an error where it has one. A pair at ``CONFLICTING`` is counted
    apart from right, wrong and no claim. Precision is over the pairs the engine proposed and the
    "no"s the key contradicts.
    """
    expected = set(family.expected)
    undecidable = set(family.undecidable)
    ignored = set(family.ignored)
    named = {path for pair in (*expected, *undecidable) for path in pair} | set(family.scope)
    counts: Counter[str] = Counter()
    by_tier: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    proposed: set[tuple[str, str]] = set()
    for raw, tier in sorted(found.items()):
        pair = _canonical(raw, directed=family.directed)
        if pair in seen or pair[0] == pair[1]:
            continue
        seen.add(pair)
        said = REJECTED_STATE if tier in REJECTED else tier if tier == CONFLICTING else CONCLUDED
        if ignored.intersection(pair):
            counts["ignored"] += 1
        elif pair in undecidable:
            outcome = {
                CONCLUDED: "undecidable_decided",
                REJECTED_STATE: "undecidable_rejected",
                CONFLICTING: "undecidable_conflicting",
            }[said]
            counts[outcome] += 1
            examples[outcome].append(pair)
        elif not family.complete and pair not in expected and not set(pair) <= named:
            counts["outside"] += 1
        elif said == CONFLICTING:
            counts["conflicting"] += 1
            examples["conflicting"].append(pair)
        elif said == REJECTED_STATE:
            outcome = "rejected_where_pair" if pair in expected else "rejected_right"
            counts[outcome] += 1
            if pair in expected:
                by_tier[tier]["wrong"] += 1
                examples[outcome].append(pair)
        elif pair in expected:
            counts["correct"] += 1
            by_tier[tier]["correct"] += 1
            proposed.add(pair)
        else:
            counts["wrong"] += 1
            by_tier[tier]["wrong"] += 1
            examples["wrong"].append(pair)
    examples["missed"] = sorted(expected - proposed)
    joined = " -> " if family.directed else " ~ "
    decided = (
        counts["undecidable_decided"]
        + counts["undecidable_rejected"]
        + counts["undecidable_conflicting"]
    )
    return {
        "expected": len(expected),
        "concluded": counts["correct"] + counts["wrong"],
        "correct": counts["correct"],
        "wrong": counts["wrong"],
        "rejected_right": counts["rejected_right"],
        "rejected_where_pair": counts["rejected_where_pair"],
        "conflicting": counts["conflicting"],
        "missed": len(expected) - counts["correct"],
        "undecidable": len(undecidable),
        "undecidable_decided": counts["undecidable_decided"],
        "undecidable_rejected": counts["undecidable_rejected"],
        "undecidable_conflicting": counts["undecidable_conflicting"],
        "undecidable_no_claim": len(undecidable) - decided,
        "outside": counts["outside"],
        "ignored": counts["ignored"],
        "precision": _proportion(
            counts["correct"],
            counts["correct"] + counts["wrong"] + counts["rejected_where_pair"],
        ),
        "recall": _proportion(counts["correct"], len(expected)),
        "by_tier": {tier: _tier_counts(by_tier[tier]) for tier in sorted(by_tier)},
        "examples": {
            outcome: [joined.join(pair) for pair in pairs[:EXAMPLES]]
            for outcome, pairs in sorted(examples.items())
            if pairs
        },
    }


def pairs_of(
    groups: Mapping[str, Iterable[str]], tier: str, *, conflicting: Collection[str] = ()
) -> dict[tuple[str, str], str]:
    """Every pair within each group at ``tier``, and at ``CONFLICTING`` where either member's own
    claim conflicts (a conflict is not a conclusion and not silence)."""
    found: dict[tuple[str, str], str] = {}
    for members in groups.values():
        ordered = sorted(set(members))
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                torn = first in conflicting or second in conflicting
                found[(first, second)] = CONFLICTING if torn else tier
    return found


def set_aside(defeaters_json: str | None) -> bool:
    """Whether a finding carries a defeater that fired: the Review queue sets it aside, and the
    evaluation reads it as the engine's own "no" rather than as something it raised."""
    if not defeaters_json:
        return False
    return any(
        isinstance(defeater, dict) and defeater.get("fired") is True
        for defeater in json.loads(defeaters_json)
    )


# ---------------------------------------------------------------------------
# Reading a run.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunReadings:
    """What one published run concluded, for every family the key has, rule by rule."""

    values: dict[str, dict[str, dict[str, Reading]]]
    pairs: dict[str, dict[str, dict[tuple[str, str], str]]]
    groups: dict[str, dict[str, dict[str, tuple[str, ...]]]]
    questions: dict[str, int]
    #: What the Questions page counts beside its rows: questions set aside because every finding
    #: behind them carries a fired defeater, and those asked on the Groupings page instead.
    beside_questions: dict[str, int]
    outputs: dict[str, int]


def _mapped(family: str, rule: str, value: str | None) -> str | None:
    if value is None:
        return None
    if family == "stage":
        return STAGE_READS_AS.get(value, value)
    if family == "experiment_folder":
        # The structure rule also names what a folder is when it is no experiment.
        return "experiment" if value == "experiment" else None
    if family == "program":
        return "program"
    if family == "empty_file":
        return EMPTY_READS_AS.get(value, value)
    return value


def _subject_path(subject: str, paths: Mapping[str, str]) -> str | None:
    return paths.get(subject.removeprefix("folder:"))


def lattice_terms(value: str) -> tuple[str, ...] | None:
    """The whole set a lattice question asks about, in canonical order, or None for another value.

    Reads the spelling that ``lattice_value`` writes. Any other value of the deviation rule
    (a word an experiment adds to or misses from an approved core) is not a proposed group.
    """
    if not value.startswith(_LATTICE_MARK):
        return None
    base, _, added = value[len(_LATTICE_MARK) :].partition(_LATTICE_ADDS)
    terms = {term.casefold() for term in (*base.split(), *added.split())}
    return tuple(sorted(terms)) or None


FindingRow = tuple[object, object, "Iterable[object]", bool]


def group_readings(
    findings: Iterable[FindingRow], paths: Mapping[str, str]
) -> tuple[dict[str, dict[tuple[str, str], str]], dict[str, dict[str, tuple[str, ...]]]]:
    """The group family's proposals, rule by rule: pairs, and each proposed group's members.

    - The clustering's findings: one value per group, raised on each member's folder.
    - The lattice's findings: each ``a group: ...`` value names a set of terms, and its group is
      every experiment whose terms contain the set, read from the sets each folder raises (a
      folder raising a derivation holds its parents' terms too). Other values of that rule are no
      proposal.

    A folder whose finding a fired defeater set aside is refused with the others set aside for the
    same group (the engine's own "no"); a pair with one member set aside stays unanswered. The rule
    of the lattice is read only where it raised a lattice question, so a run without the lattice
    reads only the clustering.
    """
    pairs: dict[str, dict[tuple[str, str], str]] = {}
    named: dict[str, dict[str, tuple[str, ...]]] = {}
    kept: dict[str, dict[str, list[str]]] = {CLUSTERING_RULE: defaultdict(list)}
    refused: dict[str, dict[str, list[str]]] = {CLUSTERING_RULE: defaultdict(list)}
    held_terms: dict[bool, dict[str, set[str]]] = {False: defaultdict(set), True: defaultdict(set)}
    asked: dict[bool, set[tuple[str, ...]]] = {False: set(), True: set()}
    for subject, rule, raised, aside in findings:
        path = _subject_path(str(subject), paths)
        if path is None:
            continue
        if rule == CLUSTERING_RULE:
            (refused if aside else kept)[rule][str(next(iter(raised)))].append(path)
        elif rule == LATTICE_RULE:
            for value in raised:
                terms = lattice_terms(str(value))
                if terms is not None:
                    held_terms[aside][path].update(terms)
                    asked[aside].add(terms)
    for aside, sets in asked.items():
        for terms in sets:
            members = [path for path, held in held_terms[aside].items() if set(terms) <= held]
            (refused if aside else kept).setdefault(LATTICE_RULE, defaultdict(list))[
                _LATTICE_MARK + " ".join(terms)
            ] = members
    for rule in sorted(kept.keys() | refused.keys()):
        pairs[rule] = {
            **pairs_of(refused.get(rule, {}), "set_aside"),
            **pairs_of(kept.get(rule, {}), "raised"),
        }
        named[rule] = {name: tuple(sorted(members)) for name, members in kept.get(rule, {}).items()}
    return pairs, named


def read_run(project: Path, *, within: str = "", root_id: str | None = None) -> RunReadings:
    """Read a project's current run through the engine's storage, for every family.

    ``within`` names a folder of the root the key's paths are relative to (a person keys one
    folder of a large root); what lies outside it is left out, and paths are read relative to it.
    ``root_id`` keeps one root's entries: two roots can each hold one relative path, so a key is
    about one root, and a project of several is scored one root at a time (``score_project``).
    """
    from ingence.api.routes import pages
    from ingence.storage.catalog import open_catalog

    values: dict[str, dict[str, dict[str, Reading]]] = {}
    pairs: dict[str, dict[str, dict[tuple[str, str], str]]] = {}
    groups: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {}
    with open_catalog(project) as catalog:
        prefix = f"{within.strip('/')}/" if within.strip("/") else ""
        paths = {
            str(entry): str(path).removeprefix(prefix)
            for entry, path, root in catalog.query(
                "SELECT entry_id, relative_path, root_id FROM entries"
            ).fetchall()
            if str(path).startswith(prefix)
            and str(path) != prefix.rstrip("/")
            and (root_id is None or str(root) == root_id)
        }
        claims = catalog.query(
            "SELECT subject, rule_id, object_reference, resolution_status, inference_tier "
            "FROM claims ORDER BY rule_id, subject, object_reference"
        ).fetchall()
        findings = [
            (subject, rule, raised, set_aside(defeaters))
            for subject, rule, raised, defeaters in catalog.query(
                "SELECT subject, rule_id, raised, defeaters_json FROM findings "
                "ORDER BY rule_id, subject"
            ).fetchall()
        ]
        edges = catalog.query(
            "SELECT source_id, target_id, primitive, rule_id, decision_tier FROM candidates "
            "UNION ALL SELECT source_id, target_id, primitive, rule_id, inference_tier "
            "FROM assertions WHERE rule_id NOT LIKE 'census.%' "
            # In one order, so the rules of a family are read, and said, in the same order every
            # run (a parallel read of the union returns its rows in any order).
            "ORDER BY rule_id, source_id, target_id, primitive"
        ).fetchall()
        dates = catalog.query(
            "SELECT entry_id, normalized_value FROM components WHERE component_type = 'date'"
        ).fetchall()
        outputs = Counter(str(rule) for _, rule, *_ in claims)
        outputs.update(str(rule) for _, rule, _, _ in findings)
        outputs.update(str(rule) for *_, rule, _ in edges)
        questions = pages.page(catalog, project, "questions", limit=sys.maxsize)
        # The two projections (workflow and repeats), read from the same catalog.
        from ingence.completeness.workflow_steps import stages_reached
        from ingence.experiment_units import run_units
        from ingence.grouping.repeats import repeat_groups

        units = run_units(catalog)
        chains: dict[str, str] = {}
        for unit, stages in stages_reached(catalog, units).items():
            unit_path = paths.get(unit)
            if unit_path is not None:
                words: list[str] = []
                for stage in STAGE_ORDER:
                    word = CHAIN_READS_AS.get(stage, stage)
                    if stage in stages and word not in words:
                        words.append(word)
                chains[unit_path] = ">".join(words)
        repeated: dict[tuple[str, str], str] = {}
        for group in {g.group: g for g in repeat_groups(catalog, units).values()}.values():
            group_paths: list[str] = []
            for member in group.members:
                member_path = paths.get(member)
                if member_path is not None:
                    group_paths.append(member_path)
            for left, right in combinations(sorted(group_paths), 2):
                repeated[(left, right)] = "projection"
    by_rule_questions = Counter(str(row.get("rule_id")) for row in questions.get("rows", []))
    beside = {name: int(questions.get(name) or 0) for name in ("set_aside", "asked_on_groupings")}

    for family, (source, rules) in VALUE_RULES.items():
        found: dict[str, dict[str, Reading]] = {rule: {} for rule in rules}
        if source == "claims":
            for subject, rule, value, status, tier in claims:
                if rule in found and (path := _subject_path(str(subject), paths)) is not None:
                    if status == "resolved":
                        mapped = _mapped(family, rule, None if value is None else str(value))
                        found[rule][path] = Reading(mapped, CONCLUDED, str(tier))
                    elif status == "examined_conflicting":
                        found[rule][path] = Reading(None, CONFLICTING, str(tier))
        elif source == "projection":
            found["workflow_steps"] = {
                path: Reading(chain, CONCLUDED, "projection") for path, chain in chains.items()
            }
        elif source == "findings":
            for subject, rule, raised, aside in findings:
                if rule in found and (path := _subject_path(str(subject), paths)) is not None:
                    # A finding a fired defeater set aside is the engine's own "no".
                    value = None if aside else _mapped(family, rule, str(next(iter(raised))))
                    found[rule][path] = Reading(
                        value, CONCLUDED, "set_aside" if aside else "raised"
                    )
        else:
            stated: dict[str, set[str]] = defaultdict(set)
            for entry, value in dates:
                if (path := paths.get(str(entry))) is not None:
                    stated[path].add(str(value))
            for path, spelled in stated.items():
                one = len(spelled) == 1
                found["tokeniser"][path] = Reading(
                    next(iter(spelled)) if one else None,
                    CONCLUDED if one else CONFLICTING,
                    "literal",
                )
        values[family] = found

    for family, (source, rules) in PAIR_RULES.items():
        pair_found: dict[str, dict[tuple[str, str], str]] = {}
        named: dict[str, dict[str, tuple[str, ...]]] = {}
        if source == "projection":
            pair_found["repeats"] = dict(repeated)
        elif source in {"classes", "finding_groups"}:
            for rule in rules:
                members: dict[str, list[str]] = defaultdict(list)
                torn: set[str] = set()
                if source == "classes":
                    for subject, rule_id, value, status, _ in claims:
                        path = _subject_path(str(subject), paths)
                        if rule_id != rule or not value or not path:
                            continue
                        if status in {"resolved", "examined_conflicting"}:
                            members[str(value)].append(path)
                        if status == "examined_conflicting":
                            torn.add(path)
                    tier = "class"
                else:
                    pair_found, named = group_readings(findings, paths)
                    break
                pair_found[rule] = pairs_of(members, tier, conflicting=torn)
                named[rule] = {name: tuple(sorted(found)) for name, found in members.items()}
        else:
            for source_id, target_id, primitive, rule, tier in edges:
                if primitive not in rules:
                    continue
                first = paths.get(str(source_id))
                second = paths.get(str(target_id))
                if first is not None and second is not None:
                    pair_found.setdefault(str(rule), {})[(first, second)] = str(tier)
        pairs[family] = pair_found
        groups[family] = named

    return RunReadings(
        values=values,
        pairs=pairs,
        groups=groups,
        questions=dict(sorted(by_rule_questions.items())),
        beside_questions=beside,
        outputs=dict(sorted(outputs.items())),
    )


def _together(by_rule: Iterable[Mapping[str, Reading]]) -> dict[str, Reading]:
    """Every rule's readings of a family as one: agreeing values stand, two values conflict."""
    said: dict[str, list[Reading]] = defaultdict(list)
    for found in by_rule:
        for path, reading in found.items():
            said[path].append(reading)
    together: dict[str, Reading] = {}
    for path, readings in said.items():
        concluded = {reading.value for reading in readings if reading.state == CONCLUDED}
        tiers = sorted({reading.tier for reading in readings})
        if len(concluded) == 1:
            together[path] = Reading(next(iter(concluded)), CONCLUDED, "/".join(tiers))
        else:
            together[path] = Reading(None, CONFLICTING, "/".join(tiers))
    return together


def together_pairs(
    by_rule: Iterable[Mapping[tuple[str, str], str]], *, directed: bool
) -> dict[tuple[str, str], str]:
    """Every rule's pairs as one: a pair every rule proposed or every rule refused stands, and
    a pair one rule proposed and another refused, or any rule found conflicting, conflicts."""
    said: dict[tuple[str, str], list[str]] = defaultdict(list)
    for found in by_rule:
        for pair, tier in found.items():
            said[_canonical(pair, directed=directed)].append(tier)
    together: dict[tuple[str, str], str] = {}
    for pair, tiers in said.items():
        refused = [tier in REJECTED for tier in tiers]
        if CONFLICTING in tiers or (any(refused) and not all(refused)):
            together[pair] = CONFLICTING
        elif all(refused):
            together[pair] = min(tiers)  # one refusing tier, so it still reads as a "no"
        else:
            together[pair] = "/".join(sorted(set(tiers)))
    return together


class KeyPlacementError(ValueError):
    """A key the private half will not score where it is, or without saying which root it names."""


def _root_for(key_path: Path, project: Path, root: str | None) -> tuple[str | None, str | None]:
    """Refuse a key saved inside any of the project's roots, and pick the root the key describes.

    Returns the root's identifier and its label or folder name, or ``(None, None)`` for a project
    of one root named by nobody.
    """
    from ingence.core.project import Project

    declared = Project.open(project).identity.roots
    placed = key_path.expanduser().resolve()
    for declaration in declared:
        mount = Path(declaration.mount_path)
        if mount.is_absolute() and placed.is_relative_to(mount.resolve()):
            message = (
                f"the key {placed} is saved inside the root {mount} the project indexes; a key is "
                "what a person knows, never part of what the engine observes: keep it beside the "
                "root, not in it"
            )
            raise KeyPlacementError(message)
    named = {
        name: declaration
        for declaration in declared
        for name in {declaration.label, Path(declaration.mount_path).name}
        if name
    }
    if root is not None:
        chosen = named.get(root)
        if chosen is None:
            message = f"no root named {root!r}; the project's roots are {', '.join(sorted(named))}"
            raise KeyPlacementError(message)
        return chosen.root_id, root
    if len(declared) > 1:
        message = (
            f"this project indexes {len(declared)} roots, and two roots can each hold one relative "
            f"path: name the root the key describes with --root ({', '.join(sorted(named))})"
        )
        raise KeyPlacementError(message)
    return None, None


def score_project(
    key_path: Path, project: Path, *, within: str = "", root: str | None = None
) -> dict[str, Any]:
    """Score a project's current run against a key: the private half, on a folder already indexed.

    Nothing is run: the project's own last inference is what is scored, so the runner's figures
    are those of the run it already made. The report says which run and which root it read. A key
    saved inside any of the project's roots is refused, and so is a project of several roots
    unless ``root`` names one (by its label or its folder's name).
    """
    from ingence.evaluation.key import load_key
    from ingence.storage.catalog import open_catalog

    root_id, root_name = _root_for(key_path, project, root)
    key = load_key(key_path)
    with open_catalog(project) as catalog:
        run = catalog.query("SELECT run_id FROM run").fetchone()
    return {
        "report_format": REPORT_FORMAT,
        "report_version": REPORT_VERSION,
        "method": {**method(isolate=None), "steps": ["the project's current run, as it stands"]},
        "key": dict(key.describes),
        "columns": {
            project.name: {
                "revisions": [],
                "run": None if run is None else str(run[0]),
                "root": root_name,
                "within": within,
                **score_key(key, read_run(project, within=within, root_id=root_id)),
            }
        },
    }


def _count_groups(
    row: dict[str, Any],
    family: PairFamily,
    proposed: Iterable[Iterable[str]],
    *,
    count: bool,
    given: Collection[tuple[str, ...]] = (),
) -> None:
    """Add to a pair row the key's named groups found whole, and how many groups were proposed.

    A group is its set of members, so one set two rules proposed is one group. A named group the
    scorer approved itself (``given``) is counted apart, never as found.
    """
    engine = {frozenset(members) for members in proposed if len(set(members)) > 1}
    if count:
        row["groups_proposed"] = len(engine)
    if family.named:
        # A group named by its words, found whole: the pairs say how much of it.
        approved = {tuple(sorted(terms)) for terms in given}
        asked = {
            name: members
            for name, members in family.named.items()
            if tuple(sorted(name.split())) not in approved
        }
        row["named_groups"] = len(asked)
        row["named_groups_matched"] = sum(
            1 for members in asked.values() if frozenset(members) in engine
        )
        if given:
            row["named_groups_given"] = len(family.named) - len(asked)


def score_key(
    key: AnswerKey, readings: RunReadings, *, given: Collection[tuple[str, ...]] = ()
) -> dict[str, Any]:
    """Score one run's readings against every family the key answers.

    ``given`` names the key's groups (by their terms) the scorer approved in the run's project:
    the group rows count them apart from the named groups the engine found.
    """
    families: dict[str, Any] = {}
    for name in FAMILIES:
        family = key.families.get(name)
        if family is None:
            continue
        rows: dict[str, Any] = {}
        if isinstance(family, ValueFamily):
            by_rule = readings.values.get(name, {})
            for rule, found in by_rule.items():
                rows[rule] = score_values(family, found)
            if len(by_rule) > 1:
                rows[EVERY_RULE] = score_values(family, _together(by_rule.values()))
        else:
            pairs_by_rule = readings.pairs.get(name, {})
            groups_by_rule = readings.groups.get(name, {})
            # Where two rules propose groups (the clustering and the lattice), each row says how
            # many groups it proposed, and the row reading both counts a set both proposed once.
            several = name == "group" and len(groups_by_rule) > 1
            for rule, found_pairs in pairs_by_rule.items():
                rows[rule] = score_pairs(family, found_pairs)
                proposed = groups_by_rule.get(rule)
                if proposed is not None:
                    _count_groups(rows[rule], family, proposed.values(), count=several, given=given)
            if len(pairs_by_rule) > 1:
                every = together_pairs(pairs_by_rule.values(), directed=family.directed)
                rows[EVERY_RULE] = score_pairs(family, every)
                if several:
                    every_group = [m for found in groups_by_rule.values() for m in found.values()]
                    _count_groups(rows[EVERY_RULE], family, every_group, count=True, given=given)
        families[name] = {"expected": family.expected_count(), "rules": rows}
    scored = {rule for _, rules in (*VALUE_RULES.values(), *PAIR_RULES.values()) for rule in rules}
    return {
        "families": families,
        "questions": {
            "total": sum(readings.questions.values()),
            "by_rule": readings.questions,
            **readings.beside_questions,
        },
        "unscored_outputs": {
            rule: count for rule, count in readings.outputs.items() if rule not in scored
        },
    }


# ---------------------------------------------------------------------------
# Running the engine over a pool.
# ---------------------------------------------------------------------------


def _run_id(step: int) -> str:
    from ingence.core.ids import run_id

    return run_id(started_at=f"2026-01-01T00:00:{step:02d}Z", nonce=f"evaluation-{step}")


def _index_and_dive(
    pool: Path, project_path: Path, *, isolate: bool
) -> tuple[Project, dict[str, float]]:
    from ingence.core.project import Project
    from ingence.core.settings import DictionarySettings, ExtractionSettings, Settings
    from ingence.extractors import load_builtins
    from ingence.extractors.base import EXTRACTORS
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen

    settings = Settings(
        dictionary=DictionarySettings(domain_packs="all"),
        extraction=ExtractionSettings(enabled=True, use_subprocess_isolation=isolate),
    )
    load_builtins()
    project = Project.create(
        project_path, created_at="2026-01-01T00:00:00Z", roots=[pool], settings=settings
    )
    seconds: dict[str, float] = {}
    started = time.perf_counter()
    census = run_census(project, run_id=_run_id(1), started_at="t", extractor_registry=EXTRACTORS)
    seconds["index"] = round(time.perf_counter() - started, 2)
    if not census.published:
        message = "the census of the generated pool was not published"
        raise RuntimeError(message)
    started = time.perf_counter()
    run_deepen(project, run_id=_run_id(2), started_at="t", profile="deep")
    seconds["dive"] = round(time.perf_counter() - started, 2)
    return project, seconds


@dataclass(frozen=True)
class ColumnRun:
    """One column's project, the seconds each step took, and the engine's own tallies."""

    project: Path
    seconds: dict[str, float]
    tallies: dict[str, dict[str, int]]
    proposals: dict[str, dict[str, Any]]
    #: The key's cores approved in this column's project before its last inference, if any.
    approved: tuple[tuple[str, ...], ...] = ()


_TALLIED = ("evaluated", "concluded", "no_evidence", "starved", "unexamined", "conflicting")


def _engine_account(report: Any) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, Any]]]:
    """What the inference said of each scored rule: how it decided, and what it drew and proposed.

    The report inference returns, never its printed table. Only the rules a family reads are
    kept, and a proposing rule only where it names an index to draw within.
    """
    scored = {rule for _, rules in (*VALUE_RULES.values(), *PAIR_RULES.values()) for rule in rules}
    tallies = {
        tally.rule_id: {name: int(getattr(tally, name)) for name in _TALLIED}
        for tally in (*report.tallies, *report.groupings, *report.flags)
        if tally.rule_id in scored
        and not tally.not_evaluable
        # The lattice's rule is read by the group family only where it raised something (it
        # also raises words added to an approved core, which the family does not read).
        and (tally.rule_id != LATTICE_RULE or tally.findings)
    }
    proposals = {
        proposal.rule_id: {
            "within": proposal.within,
            "pairs": proposal.pairs,
            "supported": proposal.supported,
            "circumstantial": proposal.circumstantial,
            "blocked": proposal.blocked,
            "blocked_by": dict(sorted(proposal.blocked_by.items())),
        }
        for proposal in report.proposals
        if proposal.within is not None and not proposal.not_evaluable
    }
    return dict(sorted(tallies.items())), dict(sorted(proposals.items()))


#: What a column's name gains when the key's cores are approved in it.
APPROVED_SUFFIX = ", key's cores approved"
#: Who approves the key's cores in such a column: the answer key, named as itself, in the column's
#: own scratch project and nowhere else.
APPROVER = "answer_key"


def cores_of(named: Mapping[str, Iterable[str]]) -> tuple[tuple[str, ...], ...]:
    """The key's most general named groups: each term set no other named set lies inside."""
    sets = {tuple(sorted(name.split())) for name in named}
    return tuple(
        sorted(terms for terms in sets if not any(set(other) < set(terms) for other in sets))
    )


def _approve(project: Path, cores: Sequence[tuple[str, ...]], run_id: str) -> None:
    """Confirm each core as a person answers a group question: ``group:<terms>`` in the Record."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.record.targets import group_target

    for terms in cores:
        append_event(
            project,
            kind=RecordEventKind.CONFIRM,
            actor=APPROVER,
            occurred_at="2026-01-01T00:00:30Z",
            target=group_target(terms),
            against_run_id=run_id,
            payload={
                "terms": sorted(terms),
                "basis": "the answer key's core, approved by the evaluation to show what lies "
                "beneath it; a scratch project's Record, never a person's",
            },
        )


def run_columns(
    pool: Path,
    workdir: Path,
    columns: Mapping[str, Sequence[Path]],
    *,
    isolate: bool = False,
    approve: Mapping[str, Sequence[tuple[str, ...]]] | None = None,
) -> dict[str, ColumnRun]:
    """Index and dive ``pool`` once, then infer each column on its own copy of the project.

    ``columns`` maps a column's name to the project revision files it adds to the shipped rules
    (none for the shipped column). ``approve`` names columns whose projects, after their first
    inference, take a confirmation of each of the given cores as :data:`APPROVER` and infer again,
    so the questions asked beneath approved cores (the lattice's derivations) become visible.
    """
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.storage.layout import RULEBOOK_DIR

    base, shared = _index_and_dive(pool, workdir / "indexed", isolate=isolate)
    done: dict[str, ColumnRun] = {}
    for name, revisions in columns.items():
        target = workdir / f"column-{name}"
        shutil.copytree(base.path, target)
        rulebook = target / RULEBOOK_DIR
        rulebook.mkdir(exist_ok=True)
        for revision in revisions:
            shutil.copyfile(revision, rulebook / revision.name)
        project = Project.open(target)
        started = time.perf_counter()
        report = run_inference(project, run_id=_run_id(3), started_at="t")
        cores = (approve or {}).get(name)
        if cores and not report.cancelled:
            _approve(target, cores, _run_id(3))
            report = run_inference(project, run_id=_run_id(4), started_at="t")
        if report.cancelled:
            message = f"the {name} column's inference was cancelled"
            raise RuntimeError(message)
        seconds = {**shared, "infer": round(time.perf_counter() - started, 2)}
        tallies, proposals = _engine_account(report)
        done[name] = ColumnRun(target, seconds, tallies, proposals, tuple(cores or ()))
    return done


def evaluate(
    key: AnswerKey,
    pool: Path,
    workdir: Path,
    columns: Mapping[str, Sequence[Path]],
    *,
    isolate: bool = False,
    approve_cores_in: Collection[str] = (),
) -> dict[str, Any]:
    """Run the engine over ``pool`` in ``workdir`` and score every column against ``key``.

    ``approve_cores_in`` names columns to run a second time with the key's cores approved (off by
    default, and never in the pinned run): each becomes a column of its own, named with
    ``APPROVED_SUFFIX``, beside the column as it stands. Its group rows count the named groups
    beneath the cores apart from the cores the scorer gave; its pairs include what was given.
    """
    group = key.families.get("group")
    cores = cores_of(group.named) if isinstance(group, PairFamily) else ()
    every = dict(columns)
    approve: dict[str, Sequence[tuple[str, ...]]] = {}
    for name in approve_cores_in:
        every[f"{name}{APPROVED_SUFFIX}"] = columns[name]
        approve[f"{name}{APPROVED_SUFFIX}"] = cores
    started = time.perf_counter()
    ran = run_columns(pool, workdir, every, isolate=isolate, approve=approve)
    report_columns: dict[str, Any] = {}
    for name, column in ran.items():
        report_columns[name] = {
            "revisions": [path.name for path in every[name]],
            "seconds": column.seconds,
            **(
                {"approved": [" ".join(core) for core in column.approved]}
                if column.approved
                else {}
            ),
            **score_key(key, read_run(column.project), given=column.approved),
            "engine_tallies": column.tallies,
            "proposals": column.proposals,
        }
    described = method(isolate=isolate)
    if approve:
        described["approval"] = (
            f"columns ending {APPROVED_SUFFIX!r} ran again after the scorer confirmed each of the "
            f"key's cores (its most general named groups) as {APPROVER!r} in that column's own "
            "project, the way a person answers a group question; their group rows count those "
            "cores apart as given, and their pairs include what was given"
        )
    return {
        "report_format": REPORT_FORMAT,
        "report_version": REPORT_VERSION,
        "method": described,
        "key": dict(key.describes),
        "seconds": round(time.perf_counter() - started, 2),
        "columns": report_columns,
    }


# ---------------------------------------------------------------------------
# Teaching the engine's words: an oracle that answers from the key.
# ---------------------------------------------------------------------------

#: The word kinds of the Words queue: the tokens the Dictionary matched nothing for, or matched in
#: two ways at odds. ``unexamined`` is a warning that the binder never looked, not a word to teach.
_QUEUED_OUTCOMES = frozenset({"examined_no_evidence", "examined_conflicting"})
#: Where the oracle's answers are dated (a scratch project's Record, one second apart).
_TAUGHT_AT = "2026-01-01T01:{minute:02d}:{second:02d}Z"
#: What the oracle writes as the source of an answer (the Record keeps who and where).
TAUGHT_SOURCE = "answer key (evaluation)"


#: The shapes of a spelling that are the word itself as the word list writes it (the case is the
#: token's own casing): an answer about them is an answer about an original word.
_SAME_WORD_SHAPES = frozenset({"original", "case"})


def made_by_a_rewrite(shapes: Iterable[str]) -> bool:
    """Whether a spelling was made by a rewrite (a slip, initials, a code...) and is not the
    original word; the two are a split, never both."""
    return any(shape not in _SAME_WORD_SHAPES for shape in shapes)


@dataclass(frozen=True)
class WordsRow:
    """One row of a Words queue: a word and the spellings the page offers beside it.

    A person answers a row as a batch: what the word means, and which of the spellings beside it
    are the same thing. The queue the Dashboard serves (``GET /api/v1/words/queue``) gives each
    row's word and its ``variants``; :func:`rows_of_queue` reads that into rows.
    """

    word: str
    spellings: tuple[str, ...] = ()
    #: For a spelling a capital divides (``zibKelt``), which the page asks about as "one word or
    #: separate words": the spelling as the names write it. None for any other row.
    glued: str | None = None
    #: The row's state as the page names it (``unknown``, ``slip``, ``pack``...), where the queue
    #: served one. Not part of what a row is: two rows of one word are equal whatever their state.
    state: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class WordAnswer:
    """One answer the oracle wrote: a word, what it was made to mean, and how the key knows it."""

    word: str
    #: ``assign`` names a sense the Dictionary (or an earlier answer of this round) holds.
    #: ``define`` makes a new one. ``split`` says a spelling a capital divides reads as separate
    #: words (its ``term`` and ``theme_kind`` are then empty).
    event: str
    term: str
    theme_kind: str
    #: How the answered spelling was made (``original``, ``slip``, ``code``...).
    shapes: tuple[str, ...]
    #: For a spelling answered beside its row's word as the same thing (an assign naming the
    #: row's word's term): that word. None for the row's own word and for a spelling the key calls
    #: a different thing.
    same_as: str | None = None


@dataclass(frozen=True)
class Teaching:
    """What one round of teaching did: the rows looked at, the words answered, and the rest."""

    k: int
    #: Rows of the queue looked at (each a word and the spellings beside it).
    looked_at: int
    #: Rows with at least one answer written.
    rows_answered: int
    answered: tuple[WordAnswer, ...]
    #: Why each word (a row's word or a spelling beside it) not answered was left: ``reason ->
    #: how many``.
    skipped: dict[str, int]

    def to_json(self) -> dict[str, Any]:
        rewritten = sum(1 for a in self.answered if made_by_a_rewrite(a.shapes))
        return {
            "k": self.k,
            "looked_at": self.looked_at,
            "rows_answered": self.rows_answered,
            "answered": len(self.answered),
            "answered_words": [answer.word for answer in self.answered],
            "answered_same_thing": sum(1 for a in self.answered if a.same_as is not None),
            "answered_by_event": dict(sorted(Counter(a.event for a in self.answered).items())),
            "answered_spellings_made_by_a_rewrite": rewritten,
            "answered_original_words": len(self.answered) - rewritten,
            "skipped": dict(sorted(self.skipped.items())),
        }


def _as_row(item: WordsRow | str) -> WordsRow:
    return item if isinstance(item, WordsRow) else WordsRow(str(item))


#: The states of the rows the Words page lists by default (``understood`` rows are hidden until
#: asked for): the words the engine is unsure of, and ``pack`` rows, a word a shipped pack binds
#: that the project has not accepted. A person meets every one of them, so each uses a place.
#: However, a ``pack`` row is answered by accepting the pack, which is not a meaning for the
#: word, so the oracle writes nothing for it.
PAGE_STATES = ("unknown", "contested", "glued", "slip", "variant", "pack")
#: What the oracle says of a row it looked at and could not answer with a meaning.
_PACK_SKIP = "a shipped pack binds it (the answer there is to accept the pack)"


def rows_of_queue(
    served: Mapping[str, Any], *, states: Collection[str] | None = None
) -> tuple[WordsRow, ...]:
    """Read the rows of ``GET /api/v1/words/queue`` as served (its JSON) into queue rows: each row's
    ``word``, the ``spelling`` of each of its ``variants``, and, for a spelling a capital divides,
    the spelling as written (``glued.written``).

    ``states`` keeps only the rows in those states (all, by default). The oracle does not take the
    page's offer (``same`` or ``kind``) for an answer: the key says what each spelling is. Nothing
    here imports the route, so the plug-in is a function of the response alone. The route serves a
    word as the census stores it (case folded, a separator as the tokeniser reads it) and a variant
    as another word of the run; :func:`teach_words` reads any other spelling the way the key's are
    read, so a capital, a separator or a code's number does not hide a word from the key.
    """
    rows: list[WordsRow] = []
    for row in served.get("rows") or ():
        if states is not None and row.get("state") not in states:
            continue
        glued = row.get("glued") or {}
        rows.append(
            WordsRow(
                str(row["word"]),
                tuple(str(variant["spelling"]) for variant in row.get("variants") or ()),
                str(glued["written"]) if glued.get("written") else None,
                None if row.get("state") is None else str(row["state"]),
            )
        )
    return tuple(rows)


def words_queue_rows(
    project: Path,
    *,
    states: Collection[str] | None = PAGE_STATES,
    most: int = 5000,
) -> tuple[WordsRow, ...]:
    """The Dashboard's Words queue for a project, as rows: ``GET /api/v1/words/queue`` asked of
    the project's current run, page by page, and read by :func:`rows_of_queue`.

    The route is dispatched in process (nothing is served and nothing is written: a read of the
    queue writes nothing). Its default filters stand: answered and understood words are hidden. Only
    the rows in ``states`` are kept (:data:`PAGE_STATES` by default: every row the page lists);
    ``most`` caps the rows read.
    """
    from ingence.api.v1 import V1Context, dispatch, load_routes
    from ingence.storage.commit import read_pointer

    load_routes()
    pointer = read_pointer(project)
    if pointer is None:
        message = "the project has no published run to ask the Words queue of"
        raise RuntimeError(message)
    context = V1Context(
        project=project, run_current=pointer.run_id, run_pinned=pointer.run_id, job_running=False
    )
    served: list[Mapping[str, Any]] = []
    offset = 0
    while offset < most:
        status, body = dispatch(context, "words/queue", {"limit": ["500"], "offset": [str(offset)]})
        if status != 200:
            message = f"the Words queue answered {status}: {body}"
            raise RuntimeError(message)
        page = list(body.get("rows") or ())
        served.extend(page)
        offset += len(page)
        if not page or offset >= int(body.get("total") or 0):
            break
    return rows_of_queue({"rows": served}, states=states)


def words_queue(project: Path, *, limit: int = 1_000_000) -> tuple[str, ...]:
    """The run's words a person has not yet told the engine the meaning of, in the order a Words
    queue offers them: the files carrying a word, most first, then its occurrences, then the word.

    The default queue, built from :meth:`~ingence.storage.catalog.Catalog.unbound_tokens`
    (what ``ingence teach unknown`` lists): the tokens read as words that the Dictionary matched
    nothing for, or matched twice at odds. A function that serves the Dashboard's Words queue
    (``GET /api/v1/words/queue``) can take its place: any callable taking a project's folder and
    returning the ordered words, passed as ``queue`` to :func:`measure_teaching`.
    """
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        rows = catalog.unbound_tokens(limit=limit)
    ordered: list[str] = []
    seen: set[str] = set()
    for row in rows:
        token = str(row["token"])
        queued = row["component_type"] == "word" and row["outcome"] in _QUEUED_OUTCOMES
        if queued and token not in seen:
            seen.add(token)
            ordered.append(token)
    return tuple(ordered)


def words_rows(project: Path) -> tuple[WordsRow, ...]:
    """The default queue as rows: each unbound word of :func:`words_queue` alone, with no
    spellings beside it. The Dashboard's queue offers the spellings."""
    return tuple(WordsRow(word) for word in words_queue(project))


#: What a person may answer for a spelling of a name of two words (initials, two words run
#: together): the name as one thing of its own (``own``), the sense of its first word (``first``),
#: or, where the Words page asks about a spelling a capital divides, "separate words" (``split``).
PAIR_ANSWERS = ("own", "first", "split")
#: The settings of ``groups.words_as`` a measurement may run under.
WORDS_AS_CHOICES = ("terms", "spellings")
#: The ``split:`` answer's two readings, as the Words page writes them (``api/routes/decide.py``).
SPLIT_READING = ("pieces", "separate words")


class _Reader:
    """Reads a spelling as the tokeniser reads a name, in the project's Dictionary.

    The key's spellings and the queue's words meet here: both are read the way the census read the
    names, so a capital (``HELA``), a separator (``cos 7``) or a code's number (``tax-3``, whose
    word is ``tax``) does not hide a word from the key.
    """

    def __init__(self, project: Path) -> None:
        from ingence.dictionary.normalize import Normaliser
        from ingence.dictionary.store import DictionaryStore

        self.store = DictionaryStore.for_project(project)
        self._normaliser = Normaliser(self.store)
        self._read: dict[str, str | None] = {}

    def word(self, spelling: str) -> str | None:
        """The one word ``spelling`` reads as (a number beside it is a sample's number), or None
        where it reads as no word, as two, or as a word beside a label such as ``rab5``."""
        if spelling not in self._read:
            from ingence.core.schema import Entry, EntryType
            from ingence.dictionary.discover import read_name

            entry = Entry(
                entry_id=f"entry_{spelling}",
                root_id="root_key",
                relative_path=spelling,
                name=spelling,
                entry_type=EntryType.FILE,
                extension="",
                compound_extension="",
                first_seen_run="run_key",
                last_seen_run="run_key",
            )
            reading = read_name(
                entry,
                run_id="run_key",
                normaliser=self._normaliser,
                store=self.store,
                with_shape=False,
                with_bindings=False,
            )
            parts = [c for c in reading.components if c.component_type != "number"]
            ok = len(parts) == 1 and parts[0].component_type == "word"
            self._read[spelling] = str(parts[0].normalized_value) if ok else None
        return self._read[spelling]


def _token_meanings(
    reader: _Reader, key: AnswerKey, pairs: str = "own"
) -> tuple[dict[str, Any], set[str]]:
    """What the key says each word the engine reads means, and the words with two meanings.

    A spelling in the key is read by the engine's own tokeniser (:class:`_Reader`): only a
    spelling that reads as one word, and at most a number beside it, is something a person can
    be asked about (a hyphenated code reads as its letters and a number, so the letters are the
    word; a name of two words, or a word beside a label such as ``rab5``, has no one word to
    answer). With ``pairs`` ``first``, a name of two words is given its first word's term.
    """
    found: dict[str, Any] = {}
    torn: set[str] = set()
    if key.words is None:
        return found, torn
    for spelling, meaning in sorted(key.words.spellings.items()):
        if pairs == "first" and meaning.parts:
            term, kind = meaning.parts[0]
            meaning = replace(meaning, term=term, theme_kind=kind, parts=())
        word = reader.word(spelling)
        if word is None:
            continue
        held = found.get(word)
        if held is not None and (held.term, held.theme_kind) != (meaning.term, meaning.theme_kind):
            torn.add(word)
        elif held is None:
            found[word] = meaning
        else:
            found[word] = replace(meaning, shapes=tuple(sorted({*held.shapes, *meaning.shapes})))
    return found, torn


def teach_words(
    project: Path,
    key: AnswerKey,
    queue: Sequence[WordsRow | str],
    k: int,
    *,
    against_run_id: str | None = None,
    count_skipped: bool = True,
    pairs: str = "own",
) -> Teaching:
    """Answer the first ``k`` rows of ``queue`` from the key's words family, as the key.

    A row is a word and the spellings the Words page offers beside it (a plain string is a row with
    none). Each answer is the event a person's answer to a word is (``term:<word>`` in the Record,
    the payload ``define_word`` writes), appended directly as :func:`_approve` appends a core's
    confirmation, and authored :data:`APPROVER` (``answer_key``). A row is answered as the page
    answers it, a batch: the word first, then each spelling beside it that the key covers. A word
    the key reads as a spelling of a word the Dictionary holds is an ``assign`` to that word's term;
    a word the Dictionary lacks (a code, or a word nobody has defined) is a ``define`` of the term
    the key gives it with its axis. A spelling the key calls the same thing as the row's word is an
    ``assign`` naming the row's word's term, as the page writes "the same thing". A spelling to
    which the key gives a term of its own is answered with that term. A word the key does not cover
    is skipped and counted, as are a word the Dictionary already places, a word the key gives two
    meanings, a term the Dictionary holds on another axis, and a word already answered in this
    round. Nothing is written for them. A row whose word an earlier row answered is not looked at
    and uses no place: the page hides an answered word, and the queue lists a word and a slip of it
    as two rows. A ``pack`` row uses a place and gets no answer: a person meets it, and what it asks
    (to accept a shipped pack) is not a meaning.

    Words are matched as the tokeniser reads them: a word or spelling the queue serves in any
    other form (a capital, a separator, a code's number) is read as the key's spellings are, so it
    finds its meaning.

    ``k`` counts the rows looked at: a row nothing in it is covered still uses a place, as a
    person's page of ``k`` rows does. With ``count_skipped=False`` it counts the rows with an
    answer instead. ``pairs`` says what the oracle answers for a spelling of a name of two words
    (initials, two words run together), which no single word's sense is: ``own`` makes the name a
    sense of its own, ``first`` gives it the sense of the name's first word, and ``split`` answers
    "separate words" where the page asks it (the row is a spelling a capital divides: a
    ``split:<spelling>`` answer, as the page writes it) and leaves the name alone where the page
    offers no way to divide it. Nothing runs: the project must be re-run (:func:`measure_teaching`
    does) for the answers to bind.
    """
    if pairs not in PAIR_ANSWERS:
        message = f"pairs is one of {', '.join(PAIR_ANSWERS)}, not {pairs!r}"
        raise ValueError(message)
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.record.questions import term_target
    from ingence.record.targets import split_target
    from ingence.storage.catalog import open_catalog

    reader = _Reader(project)
    meanings, torn = _token_meanings(reader, key, "first" if pairs == "first" else "own")
    store = reader.store
    with open_catalog(project) as catalog:
        row = catalog.query("SELECT run_id FROM run").fetchone()
        run_id = against_run_id or (str(row[0]) if row else "")
        # As define_word counts it: the names carrying the word, whatever kind of part it is.
        carried = {
            str(word): int(files)
            for word, files in catalog.query(
                "SELECT lower(normalized_value), count(DISTINCT entry_id) "
                "FROM components GROUP BY 1"
            ).fetchall()
        }
    if not run_id:
        message = "the project has no published run for the oracle to answer against"
        raise RuntimeError(message)

    def found(spelled: str) -> str:
        """The word a queue spelling is, as the key's words are: itself if the key has it, else
        what the tokeniser reads it as."""
        folded = spelled.casefold()
        if folded in meanings or folded in torn:
            return folded
        return reader.word(spelled) or folded

    answered: list[WordAnswer] = []
    skipped: Counter[str] = Counter()
    defined: dict[str, str] = {}  # term -> axis, made by an earlier answer of this round
    done: set[str] = set()
    looked = rows_answered = 0
    for item in queue:
        if looked >= k and count_skipped:
            break
        if rows_answered >= k and not count_skipped:
            break
        head = _as_row(item)
        head_word = found(head.word)
        if head_word in done:
            # The page hides a word once it is answered: a row for a word an earlier row's batch
            # answered (the queue lists a word and its slip as two rows) is not there to look at.
            skipped["already answered in this round"] += 1
            continue
        looked += 1
        if head.state == "pack":
            skipped[_PACK_SKIP] += 1
            continue
        head_meaning = meanings.get(head_word)
        written_before = len(answered)
        for place, spelled in enumerate((head.word, *head.spellings)):
            word = found(spelled)
            meaning = meanings.get(word)
            second = len(answered)
            at = _TAUGHT_AT.format(minute=second // 60, second=second % 60)
            if word in done:
                skipped["already answered in this round"] += 1
            elif word in torn:
                skipped["the key gives it two meanings"] += 1
            elif meaning is None:
                skipped["not in the key"] += 1
            elif store.look_up(word) is not None:
                skipped["the Dictionary already places it"] += 1
            elif pairs == "split" and meaning.parts:
                # A name of two words: where the page asks about it as a spelling a capital divides,
                # the answer is "separate words"; where it does not, there is nothing to say.
                if place or not head.glued:
                    skipped["the page offers no way to divide it"] += 1
                    continue
                append_event(
                    project,
                    kind=RecordEventKind.ASSIGN,
                    actor=APPROVER,
                    occurred_at=at,
                    target=split_target(head.word),
                    against_run_id=run_id,
                    payload={
                        "reading": SPLIT_READING[0],
                        "answer": SPLIT_READING[1],
                        "written": head.glued,
                        "source": TAUGHT_SOURCE,
                        "note": f"the answer key: {word} is {meaning.means}, two words",
                    },
                )
                done.add(word)
                answered.append(WordAnswer(word, "split", "", "", meaning.shapes))
            else:
                held = store.sense_for_term(meaning.term)
                axis = held.theme_kind if held is not None else defined.get(meaning.term)
                if axis is not None and axis != meaning.theme_kind:
                    skipped["its term is on another axis"] += 1
                    continue
                kind = "assign" if axis is not None else "define"
                append_event(
                    project,
                    kind=RecordEventKind.ASSIGN if kind == "assign" else RecordEventKind.DEFINE,
                    actor=APPROVER,
                    occurred_at=at,
                    target=term_target(word),
                    against_run_id=run_id,
                    payload={
                        "raised": word,
                        "answer": meaning.term,
                        "theme_kind": meaning.theme_kind,
                        "as_shown": {"files": carried.get(word, 0)},
                        "source": TAUGHT_SOURCE,
                        "note": f"the answer key: {word} means {meaning.means}",
                    },
                )
                defined.setdefault(meaning.term, meaning.theme_kind)
                done.add(word)
                same = place > 0 and head_meaning is not None and head_meaning.term == meaning.term
                answered.append(
                    WordAnswer(
                        word,
                        kind,
                        meaning.term,
                        meaning.theme_kind,
                        meaning.shapes,
                        head_word if same else None,
                    )
                )
        if len(answered) > written_before:
            rows_answered += 1
    return Teaching(k, looked, rows_answered, tuple(answered), dict(sorted(skipped.items())))


def key_lines(key: AnswerKey) -> dict[str, str]:
    """Each experiment folder the key names, and the line of work it belongs to.

    A line of work is a connected set of the key's group family (a project's experiments, and the
    smaller sets a third word adds, lie inside it); an experiment in no group is a line of its own,
    named for itself.
    """
    folders = key.families.get("experiment_folder")
    group = key.families.get("group")
    experiments = (
        sorted(path for path, value in folders.expected.items() if value == "experiment")
        if isinstance(folders, ValueFamily)
        else []
    )
    parent = {path: path for path in experiments}

    def root(path: str) -> str:
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    if isinstance(group, PairFamily):
        for first, second in group.expected:
            if first in parent and second in parent:
                parent[root(first)] = root(second)
    return {path: root(path) for path in experiments}


def _theme_figures(project: Path, run_id: str, key: AnswerKey) -> dict[str, Any]:
    """The themes of a run against the key's lines of work: agreement, stability, who is out."""
    from sklearn.metrics import adjusted_mutual_info_score

    from ingence.themes.projection import themes_projection

    built = themes_projection(project, run_id)
    lines = key_lines(key)
    rows = built["experiments"]
    unplaced = Counter(str(row["unplaced_reason"]) for row in rows if row["theme"] is None)
    known = [row for row in rows if row["folder"] in lines]

    def agreement(labels: list[tuple[str, str]]) -> dict[str, Any]:
        if len(labels) < 2 or len({line for line, _ in labels}) < 2:
            return {"ami": None, "experiments": len(labels)}
        score = adjusted_mutual_info_score([a for a, _ in labels], [b for _, b in labels])
        return {"ami": round(float(score), 4), "experiments": len(labels)}

    placed = [
        (lines[row["folder"]], str(row["theme"])) for row in known if row["theme"] is not None
    ]
    every = [
        (
            lines[row["folder"]],
            str(row["theme"]) if row["theme"] is not None else f"alone:{row['folder']}",
        )
        for row in known
    ]
    summary = built["summary"]
    return {
        "experiments_in_themes": len(rows),
        "experiments_in_key": len(lines),
        "experiments_in_both": len(known),
        "themes": len(built["themes"]),
        "unplaced": {
            "count": sum(unplaced.values()),
            "of": len(rows),
            "by_reason": dict(sorted(unplaced.items())),
        },
        "ami_placed": agreement(placed),
        "ami_every": agreement(every),
        "stability": {
            "seed_ami": summary.get("seed_ami"),
            "seed_nmi": summary.get("seed_nmi"),
            "seeds": len(summary.get("seeds", [])),
        },
    }


def _grouping_figures(
    readings: RunReadings, key: AnswerKey, scored: Mapping[str, Any]
) -> dict[str, Any]:
    """The key's lattice cores found whole, and the experiments found by their contents."""
    group = key.families.get("group")
    proposed = {
        frozenset(members)
        for rule_groups in readings.groups.get("group", {}).values()
        for members in rule_groups.values()
        if len(set(members)) > 1
    }
    cores = cores_of(group.named) if isinstance(group, PairFamily) else ()
    members_of = (
        {tuple(sorted(name.split())): frozenset(found) for name, found in group.named.items()}
        if isinstance(group, PairFamily)
        else {}
    )
    found = sum(1 for terms in cores if members_of[terms] in proposed)
    by_rule = scored["families"].get("experiment_folder", {}).get("rules", {})

    def of(rule: str) -> dict[str, Any]:
        row = by_rule.get(rule)
        if row is None:
            return {"concluded": 0, "correct": 0, "expected": 0}
        return {
            "concluded": row["concluded"],
            "correct": row["correct"],
            "expected": row["expected"],
        }

    return {
        "lattice_cores": {"found_whole": found, "of": len(cores), "groups_proposed": len(proposed)},
        "experiments_by_contents": of("experiment_folder_from_contents"),
        "experiments_by_dated_name": of("experiment_folder_from_dated_name"),
    }


def _figures(project: Path, key: AnswerKey) -> tuple[dict[str, Any], dict[str, Any]]:
    """The scorer's counts for a run and the figures beside them (:func:`measure_teaching`)."""
    from ingence.grouping.lattice_words import run_words_as
    from ingence.scan.reading_figure import reading_figure
    from ingence.storage.catalog import open_catalog

    readings = read_run(project)
    scored = score_key(key, readings)
    with open_catalog(project) as catalog:
        figure = reading_figure(catalog)
        row = catalog.query("SELECT run_id FROM run").fetchone()
        words_as = run_words_as(catalog)
    run_id = str(row[0]) if row else ""
    beside = {
        # Which words the run made its groups of, as the run's own journal says it.
        "groups_words_as": words_as,
        "reading": figure,
        "themes": _theme_figures(project, run_id, key) if run_id else None,
        **_grouping_figures(readings, key, scored),
    }
    return scored, beside


def _teaching_methods() -> dict[str, str]:
    """How each figure beside the scorer's counts is made (stated in the report, one line each)."""
    return {
        "teaching": (
            "an oracle answers the first k rows of the Words queue (a word and the spellings "
            "offered beside it) from the key's words family, authored as the key, in a scratch "
            "project's Record: a spelling the key calls the same thing as its row's word is an "
            "assign naming the word's term; k counts the rows looked at (a row the key does not "
            "cover uses a place); the project then re-runs as the Dashboard's Re-run does: a "
            "rebind of the stored census, then inference"
        ),
        "reading": (
            "scan/reading_figure.py over the published run: word parts of names (occurrences, "
            "files and folders alike) that are understood (bound to a Dictionary sense), unknown "
            "or contested"
        ),
        "themes.ami_placed": (
            "adjusted mutual information (scikit-learn) between each experiment's theme and the "
            "key's line of work (a connected set of the key's group family), over the "
            "experiments the key names that a theme placed"
        ),
        "themes.ami_every": (
            "the same, with each experiment no theme placed counted as a theme of its own"
        ),
        "themes.stability": (
            "the themes route's seed agreement: AMI between the themes drawn with seed 0 and with "
            "each other seed, averaged (summary.seed_ami), NMI beside it"
        ),
        "themes.unplaced": (
            "experiments in no theme, out of all the themes were drawn from, by the reason the "
            "themes give"
        ),
        "lattice_cores": (
            "the key's cores (its most general named groups of the group family) that any grouping "
            "rule proposed with exactly the key's members, out of the key's cores"
        ),
        "experiments_by_contents": (
            "the experiment-folder family's rows for the rule that reads a folder's contents and "
            "the rule that reads a dated name: concluded, right, and the folders the key expects"
        ),
    }


def measure_teaching(
    key: AnswerKey,
    pool: Path,
    workdir: Path,
    ks: Sequence[int],
    *,
    queue: Callable[[Path], Sequence[WordsRow | str]] = words_queue_rows,
    count_skipped: bool = True,
    pairs: str = "own",
    words_as: Sequence[str] = ("terms",),
    isolate: bool = False,
    label: str = "",
    save: Path | None = None,
    keep_projects: bool = True,
) -> dict[str, Any]:
    """Index and dive ``pool`` once, then for each ``k`` teach the first ``k`` rows and re-run.

    Each ``k`` is a column: a copy of the indexed project, taught (``k`` = 0 teaches nothing), then
    re-run the way the Dashboard's Re-run does (a rebind of the stored census, then inference),
    the same path for every ``k`` so the columns differ by the answers alone. The queue is
    asked once, of the project as it stands before any answer, so the rows taught at ``k`` = 10
    are the first ten of those taught at ``k`` = 25. ``queue`` is a callable taking the project's
    folder and returning the ordered rows (a word and the spellings beside it; a plain word is a
    row with none): :func:`words_queue_rows` by default, the Dashboard's own Words queue
    (``GET /api/v1/words/queue``) asked of the project, and :func:`words_rows` for the plain list of
    unbound words that offers no spellings. ``words_as`` names the ``groups.words_as`` settings to
    measure (``terms``, the engine's default, reads a word the Dictionary binds as its term in the
    groups; ``spellings`` as the spelling): each is run for every ``k`` from the one index, and
    with more than one the columns head with the setting (``spellings k=10``). The setting each run
    was bound by is read from the run's own journal into the column (``groups_words_as``).
    ``label`` heads each column's name (``"tier off "`` gives ``tier off k=10``), for a report that
    holds several collections' columns (:func:`measure_teaching_arms`).

    Resumable (``save``): each finished column is written to ``save`` as its own JSON file, the
    queue as asked is written beside them, and the indexed project is marked done; a run started
    again over the same ``save`` reuses all three, so only the columns missing are made (the
    columns are deterministic, so a column made later equals the one a whole run would have made).
    With ``keep_projects`` false a column's project is removed once its file is written, which
    keeps a long measurement within the room it needs.

    Each column carries the scorer's counts (``families``, ``questions``...), the reading figure,
    the themes' figures, the lattice cores and the experiments found by contents, and what the
    oracle did. Nothing here reads anywhere but the pool and the project's own folder.
    """
    from ingence.core.project import Project
    from ingence.core.settings import GroupSettings
    from ingence.extractors import load_builtins
    from ingence.extractors.base import EXTRACTORS
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.layout import RULEBOOK_DIR

    started = time.perf_counter()
    for setting in words_as:
        if setting not in WORDS_AS_CHOICES:
            message = f"words_as is one of {', '.join(WORDS_AS_CHOICES)}, not {setting!r}"
            raise ValueError(message)
    slug = label.strip().replace(" ", "-")
    columns: dict[str, Any] = {}
    marker = workdir / "indexed.done.json"
    queue_file = workdir / "queue.json"
    if save is not None:
        save.mkdir(parents=True, exist_ok=True)

    def file_of(name: str) -> Path | None:
        return None if save is None else save / (name.replace(" ", "_").replace("=", "") + ".json")

    def wanted() -> list[tuple[str, str, int]]:
        # Smallest k first, both settings of each: a run cut short has each setting at its k.
        return [
            (f"{label}{setting} k={k}" if len(words_as) > 1 else f"{label}k={k}", setting, k)
            for k in sorted(set(ks))
            for setting in words_as
        ]

    def made(name: str) -> bool:
        saved = file_of(name)
        return saved is not None and saved.is_file()

    todo = [item for item in wanted() if not made(item[0])]
    base: Project | None = None
    shared: dict[str, float] = {}
    ordered: tuple[WordsRow, ...] = ()
    if todo or save is None or not queue_file.is_file():
        if marker.is_file() and (workdir / "indexed").is_dir():
            base = Project.open(workdir / "indexed")
            shared = {name: float(value) for name, value in json.loads(marker.read_text()).items()}
        else:
            shutil.rmtree(workdir / "indexed", ignore_errors=True)
            base, shared = _index_and_dive(pool, workdir / "indexed", isolate=isolate)
            if save is not None:
                write_atomically(marker, json.dumps(shared))
        load_builtins()
    if queue_file.is_file():
        ordered = tuple(
            WordsRow(row["word"], tuple(row["spellings"]), row["glued"], row["state"])
            for row in json.loads(queue_file.read_text(encoding="utf-8"))
        )
    else:
        assert base is not None
        ordered = tuple(_as_row(item) for item in queue(base.path))
        if save is not None:
            write_atomically(
                queue_file,
                json.dumps(
                    [
                        {
                            "word": r.word,
                            "spellings": list(r.spellings),
                            "glued": r.glued,
                            "state": r.state,
                        }
                        for r in ordered
                    ]
                ),
            )
    for name, setting, k in wanted():
        saved = file_of(name)
        if saved is not None and saved.is_file():
            columns[name] = json.loads(saved.read_text(encoding="utf-8"))
            continue
        assert base is not None
        target = workdir / "-".join(
            part for part in ("column", slug, setting if len(words_as) > 1 else "", f"k{k}") if part
        )
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(base.path, target)
        (target / RULEBOOK_DIR).mkdir(exist_ok=True)
        project = Project.open(target)
        if project.settings.groups.words_as != setting:
            grouped = GroupSettings.model_validate({"words_as": setting})
            project = project.save_settings(project.settings.model_copy(update={"groups": grouped}))
        taught = teach_words(target, key, ordered, k, count_skipped=count_skipped, pairs=pairs)
        moment = time.perf_counter()
        rebound = run_census(
            project,
            run_id=_run_id(5),
            started_at="t",
            extractor_registry=EXTRACTORS,
            rebind=True,
        )
        if not rebound.published:
            message = f"the {name} column's rebind was not published"
            raise RuntimeError(message)
        report = run_inference(project, run_id=_run_id(6), started_at="t")
        if report.cancelled:
            message = f"the {name} column's inference was cancelled"
            raise RuntimeError(message)
        seconds = {**shared, "rerun": round(time.perf_counter() - moment, 2)}
        tallies, proposals = _engine_account(report)
        scored, beside = _figures(target, key)
        column = {
            "revisions": [],
            "seconds": seconds,
            "taught": taught.to_json(),
            **scored,
            **beside,
            "engine_tallies": tallies,
            "proposals": proposals,
        }
        # Through its own JSON, so a column made now and one read back later are the same value.
        columns[name] = json.loads(json.dumps(column))
        if saved is not None:
            write_atomically(saved, json.dumps(columns[name]))
        if not keep_projects:
            shutil.rmtree(target, ignore_errors=True)
    return {
        "report_format": TEACHING_FORMAT,
        "report_version": TEACHING_VERSION,
        "method": {
            **method(isolate=isolate),
            "steps": [
                "init --all-domains",
                "index",
                "dive --depth deep",
                "per setting and k: the oracle's answers, index --rebind, infer",
            ],
            "figures": _teaching_methods(),
            "teaching": {
                "pair_answers": pairs,
                "k_counts": "rows looked at" if count_skipped else "rows answered",
                "queue": getattr(queue, "__name__", "a function of the project"),
            },
            "groups": {"words_as": list(words_as)},
        },
        "key": dict(key.describes),
        "queue": {
            "rows": len(ordered),
            "by_state": dict(sorted(Counter(row.state or "unstated" for row in ordered).items())),
            "spellings_beside": sum(len(row.spellings) for row in ordered),
            "first": [row.word for row in ordered[:30]],
            "first_rows": [
                {"word": row.word, "spellings": list(row.spellings)} for row in ordered[:30]
            ],
        },
        "seconds": round(time.perf_counter() - started, 2),
        "columns": columns,
    }


def measure_teaching_arms(
    arms: Mapping[str, tuple[AnswerKey, Path]],
    workdir: Path,
    ks: Sequence[int],
    **options: Any,
) -> dict[str, Any]:
    """Measure several collections, each by :func:`measure_teaching`, in one report.

    ``arms`` maps a label to its key and pool; the first arm is the report's own (its label is
    usually empty, so its columns are ``k=0``, ``k=10``), and every other arm's columns head with
    its label (``tier off k=10``). The arm of a tier-off collection (the same tier and seed with
    no word rewritten, its key holding the original words alone; see
    ``generate_sample_lab(vocabulary_originals=True)``) shows how much room teaching has to help:
    what the groupings are before any rewrite, and what teaching the original words alone does,
    beside what teaching a rewritten collection does. ``options`` go to every arm.
    """
    reports: dict[str, dict[str, Any]] = {}
    for label, (key, pool) in arms.items():
        slug = label.strip().replace(" ", "-") or "main"
        reports[label] = measure_teaching(key, pool, workdir / slug, ks, label=label, **options)
    merged = dict(reports[next(iter(reports))])
    merged["columns"] = {
        name: column for report in reports.values() for name, column in report["columns"].items()
    }
    merged["seconds"] = round(sum(float(report["seconds"]) for report in reports.values()), 2)
    merged["arms"] = {
        label.strip() or "main": {
            "key": report["key"],
            "queue": report["queue"],
            "seconds": report["seconds"],
        }
        for label, report in reports.items()
    }
    return merged


def _figure_cell(value: Any) -> str:
    return "-" if value is None else str(value)


def taught_summary(report: Mapping[str, Any]) -> str:
    """The teaching measurement as text: a row per figure, a column per k, each figure's method."""
    columns = report["columns"]
    names = list(columns)
    rows: list[tuple[str, list[str]]] = []

    def row(label: str, read: Callable[[Mapping[str, Any]], Any]) -> None:
        cells: list[str] = []
        for name in names:
            try:
                cells.append(_figure_cell(read(columns[name])))
            except (KeyError, TypeError):
                cells.append("-")
        rows.append((label, cells))

    def of_total(found: Any, total: Any) -> str:
        return f"{found}/{total}"

    row("groups made of (groups.words_as)", lambda c: c["groups_words_as"])
    row("rows looked at", lambda c: c["taught"]["looked_at"])
    row("rows with an answer", lambda c: c["taught"]["rows_answered"])
    row("words answered", lambda c: c["taught"]["answered"])
    row(
        "  of them, spellings a rewrite made",
        lambda c: c["taught"]["answered_spellings_made_by_a_rewrite"],
    )
    row("  of them, original words", lambda c: c["taught"]["answered_original_words"])
    row("words answered as the same thing", lambda c: c["taught"]["answered_same_thing"])
    row("word parts read", lambda c: c["reading"]["words"]["read"])
    row("  understood", lambda c: c["reading"]["words"]["understood"])
    row("  unknown", lambda c: c["reading"]["words"]["unknown"])
    row("  contested", lambda c: c["reading"]["words"]["contested"])
    row("theme AMI vs key lines (placed)", lambda c: c["themes"]["ami_placed"]["ami"])
    row("theme AMI vs key lines (every)", lambda c: c["themes"]["ami_every"]["ami"])
    row("theme stability (AMI across seeds)", lambda c: c["themes"]["stability"]["seed_ami"])
    row("themes", lambda c: c["themes"]["themes"])
    row(
        "experiments no theme placed",
        lambda c: of_total(c["themes"]["unplaced"]["count"], c["themes"]["unplaced"]["of"]),
    )
    row(
        "lattice cores found whole",
        lambda c: of_total(c["lattice_cores"]["found_whole"], c["lattice_cores"]["of"]),
    )
    row("groups proposed", lambda c: c["lattice_cores"]["groups_proposed"])
    row(
        "experiments by contents (right/expected)",
        lambda c: of_total(
            c["experiments_by_contents"]["correct"], c["experiments_by_contents"]["expected"]
        ),
    )
    row(
        "experiments by dated name (right/expected)",
        lambda c: of_total(
            c["experiments_by_dated_name"]["correct"], c["experiments_by_dated_name"]["expected"]
        ),
    )
    row("questions for a person", lambda c: c["questions"]["total"])
    width = max(len(label) for label, _ in rows)
    across = max(14, *(len(name) for name in names)) + 1
    head = "".join(f"{name:>{across}}" for name in names)
    lines = [f"{'':<{width}}{head}"]
    lines.extend(
        f"{label:<{width}}" + "".join(f"{cell:>{across}}" for cell in cells)
        for label, cells in rows
    )
    lines.append("")
    lines.append("How each figure is made:")
    lines.extend(f"  {name}: {how}" for name, how in report["method"]["figures"].items())
    skipped = {name: columns[name]["taught"]["skipped"] for name in names}
    lines.append("")
    lines.append("Words left alone, by reason: " + json.dumps(skipped, sort_keys=True))
    return "\n".join(lines) + "\n"


def method(*, isolate: bool | None) -> dict[str, Any]:
    """How the figures were made, stated beside them."""
    from ingence.dictionary.normalize import NORMALIZER_VERSION

    return {
        "engine": {
            "version": __version__,
            "commit": engine_commit(),  # the run record's own source
            "schema": SCHEMA_VERSION,
            "tokeniser": NORMALIZER_VERSION,
        },
        "steps": ["init --all-domains", "index", "dive --depth deep", "infer (per column)"],
        "settings": {"extraction.enabled": True, "extraction.use_subprocess_isolation": isolate},
        "value_rules": {family: list(rules) for family, (_, rules) in VALUE_RULES.items()},
        "pair_rules": {family: list(rules) for family, (_, rules) in PAIR_RULES.items()},
        "stage_reads_as": STAGE_READS_AS,
        "empty_reads_as": EMPTY_READS_AS,
        "proportions": "counts, with a Wilson score interval at 95% (z = 1.96), as rule show -p",
        "abstention": (
            "no claim (unknown or unexamined; engine_tallies splits them), conflicting, and any "
            "answer on what the key marks undecidable are counted apart from right and wrong, "
            "and none is an error; the engine's own no (a blocked candidate, a finding a fired "
            "defeater set aside) is right where the key has none and wrong where it has one"
        ),
        "precision": (
            "right of every answer the key can contradict: each value or pair concluded, and each "
            "no where the key has a value or a pair"
        ),
        "not_measured": (
            "decisions resolved per minute of review: it needs a person and a clock, and the "
            "private half measures it"
        ),
    }


# ---------------------------------------------------------------------------
# What a person reads, and what a test pins.
# ---------------------------------------------------------------------------


def _interval(proportion: Mapping[str, Any]) -> str:
    if not proportion["trials"]:
        return "-"
    return (
        f"{proportion['successes']}/{proportion['trials']} "
        f"[{proportion['low']:.2f}, {proportion['high']:.2f}]"
    )


NO_CLAIM = "no claim (unknown or unexamined; engine_tallies splits them)"


def _said(row: Mapping[str, Any]) -> bool:
    """Whether a rule answered anything the key can score, right, wrong, refused or torn."""
    return any(
        row.get(name)
        for name in (
            "concluded",
            "wrong",
            "rejected_right",
            "conflicting",
            "undecidable_decided",
            "undecidable_rejected",
            "undecidable_conflicting",
        )
    )


def _row_line(rule: str, row: Mapping[str, Any]) -> str:
    if "missed" in row:  # a pair family
        wrong = row["wrong"] + row["rejected_where_pair"]
        parts = [f"right {row['correct']}", f"wrong {wrong}"]
        if wrong:
            parts[-1] += (
                f" (proposed {row['wrong']}, refused where the key has a pair "
                f"{row['rejected_where_pair']})"
            )
        if row["rejected_right"]:
            parts.append(f"refused rightly {row['rejected_right']}")
        parts.append(f"missed {row['missed']}")
        undecided = (
            f"decided {row['undecidable_decided']}, refused {row['undecidable_rejected']}, "
            f"conflicting {row['undecidable_conflicting']}, no claim {row['undecidable_no_claim']}"
        )
    else:
        parts = [f"right {row['correct']}", f"wrong {row['wrong']}"]
        if row["wrong"]:
            parts[-1] += (
                f" (another value {row['wrong_value']}, a value where the key has none "
                f"{row['wrong_where_none']}, none where the key has a value "
                f"{row['none_where_value']})"
            )
        parts.append(f"no claim {row['no_claim']}")
        undecided = (
            f"decided {row['undecidable_decided']}, conflicting {row['undecidable_conflicting']}, "
            f"no claim {row['undecidable_no_claim']}"
        )
    if row.get("conflicting"):
        parts.append(f"conflicting {row['conflicting']}")
    if row["undecidable"]:
        parts.append(f"of {row['undecidable']} undecidable: {undecided}")
    if "groups_proposed" in row:
        parts.append(f"groups proposed {row['groups_proposed']}")
    if "named_groups" in row:
        parts.append(
            f"named groups found whole {row['named_groups_matched']} of {row['named_groups']}"
        )
    return (
        f"    {rule}: {', '.join(parts)}; precision {_interval(row['precision'])}, "
        f"recall {_interval(row['recall'])}"
    )


def summary(report: Mapping[str, Any], *, times: bool = True) -> str:
    """A short text summary: per column, per family, one line per rule that answered anything.

    ``times=False`` leaves out the seconds each column took, so two runs of the same collection
    on the same engine write the same bytes (the summary holds no path and no moment otherwise).
    """
    engine = report["method"]["engine"]
    lines = [
        f"Evaluation of ingence {engine['version']} at {engine['commit'] or 'an unknown commit'} "
        f"(schema {engine['schema']}, tokeniser {engine['tokeniser']}), key {report['key']}",
        "Counts, never one score. precision = right of every answer the key can contradict, "
        "recall = right of expected, each with a 95% Wilson interval; "
        f"{NO_CLAIM} and conflicting are not errors.",
    ]
    for column, body in report["columns"].items():
        tallies = body.get("engine_tallies", {})
        lines.append("")
        lines.append(
            f"== {column} ({', '.join(body['revisions']) or 'the rules in force'}); "
            f"{body['questions']['total']} questions for a person"
            f" ({body['questions'].get('asked_on_groupings', 0)} more asked on the Groupings page,"
            f" {body['questions'].get('set_aside', 0)} set aside)"
            + (f"; seconds {body['seconds']}" if times and "seconds" in body else "")
        )
        if body.get("approved"):
            lines.append(
                f"  the key's cores approved as {APPROVER} before this run: "
                + "; ".join(body["approved"])
            )
        for family, scored in body["families"].items():
            lines.append(f"  {family}: {scored['expected']} expected")
            quiet: list[str] = []
            unpublished: list[str] = []
            for rule, row in scored["rules"].items():
                if _said(row):
                    lines.append(_row_line(rule, row))
                elif tallies.get(rule, {}).get("concluded"):
                    unpublished.append(rule)
                else:
                    quiet.append(rule)
            if unpublished:
                lines.append(
                    "    concluded, and published nothing this family reads: "
                    + ", ".join(unpublished)
                )
            if quiet:
                lines.append(f"    said nothing this family reads: {', '.join(quiet)}")
            if not scored["rules"]:
                lines.append("    no rule published anything this family reads")
        for rule, drawn in body.get("proposals", {}).items():
            lines.append(
                f"  {rule} drew {drawn['pairs']} pairs within {drawn['within']}: "
                f"{drawn['circumstantial']} circumstantial, {drawn['supported']} supported, "
                f"{drawn['blocked']} blocked"
            )
    return "\n".join(lines) + "\n"


def pinned_figures(report: Mapping[str, Any]) -> dict[str, Any]:
    """The report's counts alone: no times, no commit, no intervals (they follow from counts)."""

    def counts(row: Mapping[str, Any]) -> dict[str, Any]:
        kept: dict[str, Any] = {}
        for name, value in row.items():
            if isinstance(value, int):
                kept[name] = value
            elif name in {"precision", "recall", "coverage"}:
                kept[name] = [value["successes"], value["trials"]]
            elif name == "by_tier":
                kept[name] = value
        return kept

    return {
        column: {
            "questions": body["questions"],
            "engine_tallies": body.get("engine_tallies", {}),
            "proposals": body.get("proposals", {}),
            "families": {
                family: {rule: counts(row) for rule, row in scored["rules"].items()}
                for family, scored in body["families"].items()
            },
        }
        for column, body in report["columns"].items()
    }


def dumps(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=1, sort_keys=False, ensure_ascii=False) + "\n"
