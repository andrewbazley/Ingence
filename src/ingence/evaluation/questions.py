"""Questions in a scientist's words, answered with experiments, and measured.

From a sample collection's own record of what it invented (the unscored key's ``projects`` and
``designs``: each project's cell line, target, treatments and stain, each experiment's design),
:func:`generate_questions` writes questions in the forms a scientist writes, each with the
experiments carrying the conditions asked about:

- treatment: "Does <treatment> change <target> in <cell line>?": the project's experiments
  whose design holds that treatment;
- compare: "Which experiments compare <A> and <B>?": every experiment whose design holds both
  levels (a treatment and the project's control word, or two treatments);
- class: the same with a class word in place of a member ("a nuclear stain" for DAPI or
  Hoechst): the project's experiments, where the project stains with one;
- misspelt: a treatment or compare question with one letter dropped from a word of five
  letters or more.

:func:`score_questions` asks each question of a project's run through the Ask reading and counts,
per tier (named; named and associated; all three), the experiments returned against the key's:
precision (right of returned) and recall (right of expected), each with a 95% Wilson interval, over
every question (micro) and by form. The pass criteria are a named precision of at least 0.9 and a
named recall of at least 0.7. The associated tier ships on only where it at least doubles the named
tier's recall at a precision of at least 0.5.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.evaluation.key import Question, QuestionsFamily
from ingence.evaluation.sample_lab_design import read_designs

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "FORMS",
    "PASS",
    "QUESTIONS_PER_POOL",
    "Places",
    "ask_project",
    "generate_questions",
    "measure_questions",
    "places_of",
    "questions_summary",
    "score_questions",
]

QUESTIONS_PER_POOL = 30
FORMS = ("treatment", "compare", "class", "misspelt")
#: The pass criteria: named precision and recall, and what the associated tier must add.
PASS = {
    "named_precision": 0.9,
    "named_recall": 0.7,
    "associated_recall_factor": 2.0,
    "associated_precision": 0.5,
}
#: The class words the shipped Dictionary lists members for, and the members the collection writes.
CLASS_WORDS: dict[str, tuple[str, ...]] = {"nuclear stain": ("DAPI", "Hoechst")}
#: The control words the shipped Dictionary reads as one sense (``process.yaml``'s control).
CONTROL_SENSE = frozenset({"ctrl", "ctl", "control", "controls", "untreated", "mock", "sham"})
TIERS = ("named", "named+associated", "named+associated+same_theme")


def _projects(unscored: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(unscored["sections"]["projects"]["entries"])


def _levels(design: Any) -> set[str]:
    """The treatment levels an experiment's design holds (``control`` as written in the key)."""
    return {
        level for cell in design.cells for factor, level in cell.levels if factor == "treatment"
    }


def _misspell(word: str, rng: random.Random) -> str | None:
    """One letter dropped from inside a word of five letters or more, or None."""
    if len(word) < 5 or not word.isalpha():
        return None
    index = rng.randrange(1, len(word) - 1)
    return word[:index] + word[index + 1 :]


def generate_questions(
    unscored: Mapping[str, Any] | Path, *, seed: int, count: int = QUESTIONS_PER_POOL
) -> QuestionsFamily:
    """``count`` questions drawn with ``seed`` from a collection's unscored key, about a quarter
    of each form, each with the experiments the key says carry the conditions asked about."""
    loaded: Mapping[str, Any] = (
        unscored if isinstance(unscored, Mapping) else json.loads(unscored.read_text("utf-8"))
    )
    rng = random.Random(f"ingence-questions:{seed}")  # noqa: S311
    projects = _projects(loaded)
    designs = {design.folder: design for design in read_designs(dict(loaded))}
    by_folder = {folder: project for project in projects for folder in project["experiments"]}
    # An experiment the projects do not list (one moved after it was made) belongs to the
    # project whose cell line and target its folder's own name carries.
    for folder in designs:
        if folder in by_folder:
            continue
        name = folder.rsplit("/", 1)[-1].casefold()
        for project in projects:
            if project["cell_line"].casefold() in name and project["target"].casefold() in name:
                by_folder[folder] = project
                break
    in_name: dict[str, set[str]] = {}
    for project in projects:
        for drug, folders in (project.get("subgroups") or {}).items():
            for folder in folders:
                in_name.setdefault(folder, set()).add(str(drug))

    def levels_of(folder: str) -> set[str]:
        """The treatment levels an experiment carries: its design's cells and the treatment its
        folder's own name was written with."""
        return _levels(designs[folder]) | in_name.get(folder, set())

    def written(project: Mapping[str, Any], level: str) -> str:
        """A level as the names write it: the project's control word for ``control``."""
        return str(project["control"]) if level == "control" else level

    def compared(project: Mapping[str, Any], level: str) -> str:
        """A level as a question compares it: every control word the Dictionary reads as one
        sense (ctrl, control, untreated, mock) is one condition, as the engine reads it; a
        vehicle, a wild type or a solvent written as the control stays its own word."""
        word = written(project, level)
        return "control" if word.casefold() in CONTROL_SENSE else word

    treatment_pool: list[Question] = []
    for project in projects:
        for drug in project["treatments"]:
            # An experiment carries the treatment where its design has a cell for it, or its
            # folder's own name was written with it (the collection's subgroups).
            expected = tuple(
                sorted(
                    folder
                    for folder, owner in by_folder.items()
                    if owner is project and folder in designs and drug in levels_of(folder)
                )
            )
            if not expected:
                continue
            treatment_pool.append(
                Question(
                    text=f"Does {drug} change {project['target']} in {project['cell_line']}?",
                    form="treatment",
                    expected=expected,
                    about=(drug, project["target"], project["cell_line"]),
                )
            )
    compare_pool: list[Question] = []
    seen_pairs: set[tuple[str, str]] = set()
    for folder in designs:
        owner = by_folder.get(folder)
        if owner is None:
            continue
        levels = sorted(levels_of(folder))
        if len(levels) < 2:
            continue
        for index, first in enumerate(levels):
            for second in levels[index + 1 :]:
                pair = (written(owner, first), written(owner, second))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                wanted = {compared(owner, first), compared(owner, second)}
                expected = tuple(
                    sorted(
                        other
                        for other in designs
                        if other in by_folder
                        and wanted
                        <= {compared(by_folder[other], level) for level in levels_of(other)}
                    )
                )
                if expected:
                    compare_pool.append(
                        Question(
                            text=f"Which experiments compare {pair[0]} and {pair[1]}?",
                            form="compare",
                            expected=expected,
                            about=pair,
                        )
                    )
    class_pool: list[Question] = []
    for project in projects:
        stain = project.get("stain")
        for class_word, members in CLASS_WORDS.items():
            if stain not in members:
                continue
            expected = tuple(sorted(project["experiments"]))
            if expected:
                class_pool.append(
                    Question(
                        text=(
                            f"Which experiments use a {class_word} with {project['target']} "
                            f"in {project['cell_line']}?"
                        ),
                        form="class",
                        expected=expected,
                        about=(class_word, project["target"], project["cell_line"]),
                    )
                )
    for pool in (treatment_pool, compare_pool, class_pool):
        rng.shuffle(pool)
    share = count // 4
    chosen: list[Question] = [
        *treatment_pool[:share],
        *compare_pool[:share],
        *class_pool[:share],
    ]
    misspelt: list[Question] = []
    for source in [*treatment_pool[share:], *compare_pool[share:]]:
        if len(chosen) + len(misspelt) >= count:
            break
        word = next((w for w in source.about if _misspell(w, rng) is not None), None)
        if word is None:
            continue
        slipped = _misspell(word, rng)
        assert slipped is not None
        misspelt.append(
            Question(
                text=source.text.replace(word, slipped, 1),
                form="misspelt",
                expected=source.expected,
                about=(*source.about, f"{word} written {slipped}"),
            )
        )
    chosen.extend(misspelt)
    # Fill to ``count`` from what is left, where the forms ran short.
    rest = [q for q in [*treatment_pool, *compare_pool, *class_pool] if q not in chosen]
    chosen.extend(rest[: max(0, count - len(chosen))])
    return QuestionsFamily(
        questions=tuple(chosen[:count]),
        describes={
            "seed": seed,
            "asked_for": count,
            "by_form": {form: sum(1 for q in chosen[:count] if q.form == form) for form in FORMS},
        },
    )


def _proportion(successes: int, trials: int) -> dict[str, Any]:
    from ingence.evaluation.score import _proportion as proportion

    return proportion(successes, trials)


@dataclass(frozen=True)
class Places:
    """What the key knows of folders and files that stand for an experiment elsewhere."""

    #: A folder that is part of an experiment though it lies elsewhere (an analysis folder in
    #: the member's analysis folder, raw data in another member's folder, a reanalysis of
    #: another member's data), mapped to the experiment it belongs to.
    of_experiment: Mapping[str, str] = field(default_factory=dict)
    #: A folder that is a copy of an experiment's folder (a backup), mapped to the original.
    copies_of: Mapping[str, str] = field(default_factory=dict)
    #: A file copied from somewhere, mapped to the file it was copied from.
    copied_files: Mapping[str, str] = field(default_factory=dict)


def places_of(unscored: Mapping[str, Any] | Path) -> Places:
    """What the key says of folders and files standing elsewhere for an experiment: an answer
    naming a place of an expected experiment names the experiment; an answer naming a copy of
    one, or a folder holding a file copied from one, is a copy, counted apart."""
    loaded: Mapping[str, Any] = (
        unscored if isinstance(unscored, Mapping) else json.loads(unscored.read_text("utf-8"))
    )
    sections = loaded["sections"]

    def entries(name: str) -> list[Any]:
        return list(sections.get(name, {}).get("entries", ()))

    of_experiment: dict[str, str] = {}
    for entry in entries("analysis_elsewhere"):
        of_experiment[str(entry["analysis"])] = str(entry["experiment"])
    for entry in entries("data_in_another_members_folder"):
        of_experiment[str(entry["data_in"])] = str(entry["folder"])
    for entry in entries("analysis_in_another_members_folder"):
        of_experiment[str(entry["folder"])] = str(entry["data_in"])
    copies_of = {
        str(entry["copy"]): str(entry["original"]) for entry in entries("backup_folder_copies")
    }
    copied_files: dict[str, str] = {}
    for name in ("copies_by_name", "random_duplicates", "near_copies", "outputs_written_twice"):
        for entry in entries(name):
            if isinstance(entry, list) and len(entry) >= 2:
                original = str(entry[0])
                for copy in entry[1:]:
                    copied_files[str(copy)] = original
    return Places(of_experiment=of_experiment, copies_of=copies_of, copied_files=copied_files)


def _beneath(path: str, folders: set[str]) -> str | None:
    """The folder of ``folders`` a path lies beneath, the deepest, or None."""
    held = path
    while "/" in held:
        held = held.rsplit("/", 1)[0]
        if held in folders:
            return held
    return None


def _counts(expected: set[str], returned: set[str], places: Places) -> tuple[int, int, int, int]:
    """Right, returned, expected and copies: an answer that is a place of an expected experiment
    counts as it; an answer that is a copy of one (a backup folder, a folder of the same name
    elsewhere, or a folder holding a file copied from an expected experiment) is a copy,
    counted apart and in neither precision nor recall."""
    named_as = {folder.rsplit("/", 1)[-1] for folder in expected}
    taken: set[str] = set()
    copies = 0
    for folder in returned:
        resolved = places.of_experiment.get(folder, folder)
        if resolved in expected:
            taken.add(resolved)
            continue
        if (
            places.copies_of.get(folder) in expected
            or folder.rsplit("/", 1)[-1] in named_as
            or any(
                copy.startswith(folder + "/") and _beneath(original, expected) is not None
                for copy, original in places.copied_files.items()
            )
        ):
            # A backup of an expected experiment, a folder of its name elsewhere, or a folder
            # holding a file copied from an expected experiment: a copy, apart. A folder holding
            # a file copied from anywhere else is answered on its own words, right or wrong.
            copies += 1
            continue
        taken.add(folder)
    right = len(expected & taken)
    return right, len(taken), len(expected), copies


def score_questions(
    family: QuestionsFamily,
    answers: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    places: Places | None = None,
) -> dict[str, Any]:
    """Count the experiments returned per tier against each question's expected ones.

    ``answers`` maps a question's text to ``{"named": [folders], "associated": [...],
    "same_theme": [...]}``, the folders relative to the root. Tiers are cumulative: named; named
    and associated; all three. Precision is right of returned and recall right of expected, over
    every question (micro) and by form, each with a 95% Wilson interval.
    """
    by_tier: dict[str, dict[str, list[int]]] = {
        tier: {"all": [0, 0, 0, 0], **{form: [0, 0, 0, 0] for form in FORMS}} for tier in TIERS
    }
    rows: list[dict[str, Any]] = []
    for question in family.questions:
        answered = answers.get(question.text, {})
        cumulative: set[str] = set()
        row: dict[str, Any] = {
            "text": question.text,
            "form": question.form,
            "expected": len(question.expected),
        }
        for tier, part in zip(TIERS, ("named", "associated", "same_theme"), strict=True):
            cumulative |= set(answered.get(part, ()))
            right, returned, expected, copies = _counts(
                set(question.expected), cumulative, places or Places()
            )
            for bucket in ("all", question.form):
                tally = by_tier[tier][bucket]
                tally[0] += right
                tally[1] += returned
                tally[2] += expected
                tally[3] += copies
            row[tier] = {"right": right, "returned": returned, "copies": copies}
        rows.append(row)
    tiers: dict[str, Any] = {}
    for tier in TIERS:
        tiers[tier] = {}
        for bucket, (right, returned, expected, copies) in by_tier[tier].items():
            tiers[tier][bucket] = {
                "precision": _proportion(right, returned),
                "recall": _proportion(right, expected),
                "copies": copies,
            }

    def _rate(tier: str, which: str) -> float | None:
        held = tiers[tier]["all"][which]
        return None if not held["trials"] else held["successes"] / held["trials"]

    named_p, named_r = _rate("named", "precision"), _rate("named", "recall")
    with_p, with_r = _rate("named+associated", "precision"), _rate("named+associated", "recall")
    associated_ships = (
        named_r is not None
        and with_r is not None
        and with_p is not None
        and with_r >= PASS["associated_recall_factor"] * named_r
        and with_p >= PASS["associated_precision"]
    )
    return {
        "questions": len(family.questions),
        "by_form": dict(family.describes.get("by_form") or {}),
        "tiers": tiers,
        "pass": {
            "named_precision": named_p,
            "named_recall": named_r,
            "named_passes": (
                named_p is not None
                and named_r is not None
                and named_p >= PASS["named_precision"]
                and named_r >= PASS["named_recall"]
            ),
            "associated_precision": with_p,
            "associated_recall": with_r,
            "associated_ships": bool(associated_ships),
            "thresholds": dict(PASS),
        },
        "rows": rows,
    }


def ask_project(
    project: Path, family: QuestionsFamily, *, associated_tier: bool = True
) -> dict[str, dict[str, list[str]]]:
    """Ask every question of a project's current run, through the Ask reading the route uses,
    and answer each with the experiment folders (relative to the root) at each tier."""
    from ingence.api.routes import search as search_routes
    from ingence.api.v1 import context_for
    from ingence.api.v1.asking import run_experiments
    from ingence.core.project import Project
    from ingence.search.ask import DEFAULT_DROPPED_KINDS, ask
    from ingence.search.query import DROPPED_KINDS
    from ingence.storage.catalog import open_catalog

    context = context_for(project, settled=None, job_running=False)
    if context.run_pinned is None:
        message = "the project has no published run to ask"
        raise LookupError(message)
    settings = Project.open(project).settings
    answers: dict[str, dict[str, list[str]]] = {}
    with open_catalog(project, run_id=context.run_pinned) as catalog:
        known = run_experiments(context, catalog, context.run_pinned)
        folders = {eid: held.folder for eid, held in known.by_id.items()}
        normaliser, store = search_routes._reader(catalog, project)
        for question in family.questions:
            reading = ask(
                catalog,
                project,
                question.text,
                normaliser=normaliser,
                store=store,
                experiments=known.asked,
                dictionary_hash=store.fingerprint(),
                inside_min_length=settings.search.inside_min_length,
                associated_tier=associated_tier,
                dropped_kinds=(
                    frozenset(settings.themes.how_kinds) | DROPPED_KINDS | DEFAULT_DROPPED_KINDS
                ),
            )
            answers[question.text] = {
                part: sorted(
                    folders[item.experiment.experiment_id]
                    for item in getattr(reading, part)
                    if item.experiment.experiment_id in folders
                )
                for part in ("named", "associated", "same_theme")
            }
    return answers


def measure_questions(
    unscored: Path, project: Path, *, seed: int, count: int = QUESTIONS_PER_POOL
) -> tuple[QuestionsFamily, dict[str, Any]]:
    """Generate the questions from a collection's unscored key, ask them of a project's run and
    score the answers: the family and the report."""
    from ingence import SCHEMA_VERSION, __version__
    from ingence.core.provenance import engine_commit

    family = generate_questions(unscored, seed=seed, count=count)
    answers = ask_project(project, family)
    scored = score_questions(family, answers, places=places_of(unscored))
    report = {
        "format": "ingence-questions-measurement",
        "format_version": 1,
        "method": {
            "engine": {
                "version": __version__,
                "commit": engine_commit(),
                "schema": SCHEMA_VERSION,
            },
            "questions": family.describes,
            "project": str(project),
        },
        **scored,
        "answers": answers,
    }
    return family, report


def _said(held: Mapping[str, Any]) -> str:
    if not held["trials"]:
        return "none"
    rate = held["successes"] / held["trials"]
    return (
        f"{held['successes']}/{held['trials']} = {rate:.2f} [{held['low']:.2f}, {held['high']:.2f}]"
    )


def questions_summary(report: Mapping[str, Any]) -> str:
    """A short text summary of a questions report."""
    forms = ", ".join(f"{n} {form}" for form, n in report["by_form"].items())
    lines = [
        f"T8: {report['questions']} questions ({forms}), scored per tier, cumulative; "
        "precision = right of returned, recall = right of expected, each with a 95% Wilson "
        "interval; denominators shown.",
    ]
    for tier, buckets in report["tiers"].items():
        lines.append(f"== {tier}")
        for bucket, held in buckets.items():
            precision, recall = _said(held["precision"]), _said(held["recall"])
            copies = f"  copies apart {held['copies']}" if held.get("copies") else ""
            lines.append(f"  {bucket:10} precision {precision}  recall {recall}{copies}")
    verdict = report["pass"]
    lines.append(
        f"named passes (precision >= {PASS['named_precision']}, recall >= {PASS['named_recall']}): "
        f"{'yes' if verdict['named_passes'] else 'no'}"
    )
    lines.append(
        f"associated ships (recall at least {PASS['associated_recall_factor']}x named at precision "
        f">= {PASS['associated_precision']}): {'on' if verdict['associated_ships'] else 'off'}"
    )
    return "\n".join(lines) + "\n"
