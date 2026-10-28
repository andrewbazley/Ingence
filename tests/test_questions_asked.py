"""Questions in a scientist's words, and the experiments Ask returns for them.

The questions are generated from a sample collection's own record of what it invented. The
experiments Ask returns are scored per tier against the experiments carrying the conditions asked
about. Every name is invented."""

from __future__ import annotations

import json

import pytest

from ingence.evaluation.key import AnswerKey, Question, QuestionsFamily
from ingence.evaluation.questions import (
    FORMS,
    TIERS,
    generate_questions,
    places_of,
    questions_summary,
    score_questions,
)


def _unscored() -> dict:
    """A tiny collection's record: two projects, four experiments, one stained."""
    designs = {
        "p/kelt vorn 1": ["control", "zib"],
        "p/kelt vorn 2": ["control", "brisa"],
        "p/kelt vorn 3": ["zib"],
        "q/tarn mox 1": ["untreated", "zib"],
    }
    return {
        "sections": {
            "projects": {
                "entries": [
                    {
                        "cell_line": "kelt",
                        "target": "vorn",
                        "treatments": ["zib", "brisa"],
                        "control": "ctrl",
                        "vehicle": None,
                        "stain": "Hoechst",
                        "techniques": ["widefield"],
                        "owner": "p",
                        "experiments": ["p/kelt vorn 1", "p/kelt vorn 2", "p/kelt vorn 3"],
                        "subgroups": {"brisa": ["p/kelt vorn 3"]},
                    },
                    {
                        "cell_line": "tarn",
                        "target": "mox",
                        "treatments": ["zib"],
                        "control": "untreated",
                        "vehicle": None,
                        "stain": None,
                        "techniques": ["tirf"],
                        "owner": "q",
                        "experiments": ["q/tarn mox 1"],
                    },
                ]
            },
            "designs": {
                "entries": [
                    {
                        "folder": folder,
                        "outcome": "complete",
                        "cells": [
                            {
                                "levels": [["treatment", level]],
                                "replicates": 1,
                                "raw_files": 1,
                            }
                            for level in levels
                        ],
                    }
                    for folder, levels in designs.items()
                ]
            },
            "analysis_elsewhere": {
                "entries": [{"experiment": "p/kelt vorn 1", "analysis": "p/analysis/kelt vorn 1"}]
            },
            "backup_folder_copies": {
                "entries": [
                    {
                        "original": "p/kelt vorn 2",
                        "copy": "p/backup/vorn 2 old",
                        "files": 3,
                        "of": 3,
                    }
                ]
            },
            "random_duplicates": {
                "entries": [
                    ["p/kelt vorn 3/raw/zib_001.nd2", "q/stray/zib_001.nd2"],
                    ["q/tarn mox 1/raw/zib_002.nd2", "q/elsewhere/zib_002.nd2"],
                ]
            },
        }
    }


def test_questions_are_drawn_in_the_four_forms_with_the_key_s_experiments() -> None:
    family = generate_questions(_unscored(), seed=1, count=8)
    assert 1 <= len(family.questions) <= 8
    forms = {question.form for question in family.questions}
    assert forms <= set(FORMS) and {"treatment", "compare"} <= forms
    by_text = {question.text: question for question in family.questions}
    # A treatment question expects the project's experiments whose design holds the treatment.
    assert by_text["Does zib change vorn in kelt?"].expected == ("p/kelt vorn 1", "p/kelt vorn 3")
    # A folder named with a treatment carries it, whatever its design's cells say.
    assert by_text["Does brisa change vorn in kelt?"].expected == ("p/kelt vorn 2", "p/kelt vorn 3")
    # A compare question reads every control word the Dictionary reads as one sense as one
    # condition: ctrl and untreated, compared with zib, expect both projects' experiments.
    compare = [q for q in family.questions if q.form == "compare" and "zib" in q.about]
    assert compare
    for question in compare:
        if "brisa" not in question.about:
            assert set(question.expected) == {"p/kelt vorn 1", "q/tarn mox 1"}
    # A class question expects the stained project's experiments.
    stained = [q for q in family.questions if q.form == "class"]
    assert len(stained) == 1
    assert "nuclear stain" in stained[0].text and len(stained[0].expected) == 3
    # A misspelt question keeps its source's experiments and says the slip.
    misspelt = [q for q in family.questions if q.form == "misspelt"]
    assert misspelt
    for question in misspelt:
        assert " written " in question.about[-1]
    # The same seed draws the same questions.
    again = generate_questions(_unscored(), seed=1, count=8)
    assert [q.text for q in again.questions] == [q.text for q in family.questions]


def test_the_family_travels_in_the_key_and_reads_back_the_same() -> None:
    family = generate_questions(_unscored(), seed=2, count=4)
    key = AnswerKey(families={}, questions=family)
    read = AnswerKey.from_json(json.loads(key.dumps()))
    assert read.questions is not None
    assert [q.to_json() for q in read.questions.questions] == [
        q.to_json() for q in family.questions
    ]
    assert AnswerKey.from_json(json.loads(AnswerKey(families={}).dumps())).questions is None


def test_scoring_counts_per_tier_cumulatively_with_places_and_copies_apart() -> None:
    family = QuestionsFamily(
        questions=(
            Question(text="a", form="treatment", expected=("p/kelt vorn 1", "p/kelt vorn 3")),
            Question(text="b", form="compare", expected=("q/tarn mox 1",)),
        ),
        describes={"by_form": {"treatment": 1, "compare": 1, "class": 0, "misspelt": 0}},
    )
    answers = {
        # The analysis folder kept elsewhere counts as its experiment. A folder of the same
        # name elsewhere, a backup folder, and a folder holding a file copied from an expected
        # experiment are copies, apart. A folder holding a file copied from an experiment that
        # is not expected is wrong, as an unrelated folder is.
        "a": {
            "named": [
                "p/analysis/kelt vorn 1",
                "backup/kelt vorn 3",
                "q/stray",
                "q/elsewhere",
                "q/other",
            ],
            "associated": ["p/kelt vorn 3"],
            "same_theme": [],
        },
        "b": {
            "named": ["p/backup/vorn 2 old"],
            "associated": [],
            "same_theme": ["q/tarn mox 1"],
        },
    }
    places = places_of(_unscored())
    assert places.copies_of == {"p/backup/vorn 2 old": "p/kelt vorn 2"}
    assert places.copied_files == {
        "q/stray/zib_001.nd2": "p/kelt vorn 3/raw/zib_001.nd2",
        "q/elsewhere/zib_002.nd2": "q/tarn mox 1/raw/zib_002.nd2",
    }
    report = score_questions(family, answers, places=places)
    named = report["tiers"]["named"]["all"]
    # Right: kelt vorn 1 (by its analysis place). Wrong: q/elsewhere (its copied file's original
    # is not an expected experiment's) and q/other. Copies apart: backup/kelt vorn 3, q/stray.
    assert named["precision"] == {"successes": 1, "trials": 4, **_interval(named["precision"])}
    assert named["recall"]["successes"] == 1 and named["recall"]["trials"] == 3
    assert named["copies"] == 2
    with_associated = report["tiers"]["named+associated"]["all"]
    assert with_associated["recall"]["successes"] == 2
    everything = report["tiers"][TIERS[-1]]["all"]
    assert everything["recall"]["successes"] == 3 and everything["recall"]["trials"] == 3
    assert report["pass"]["named_passes"] is False
    # The associated tier doubled the named recall (1 of 3 to 2 of 3) but at a precision of 2 of
    # 5 (two wrong answers stand in the named tier): below 0.5, so the tier does not ship.
    with_associated = report["tiers"]["named+associated"]["all"]
    assert with_associated["precision"]["successes"] == 2
    assert with_associated["precision"]["trials"] == 5
    assert report["pass"]["associated_ships"] is False
    text = questions_summary(report)
    assert "named" in text and "denominators" in text and "copies apart 2" in text


def _interval(held: dict) -> dict:
    return {"low": held["low"], "high": held["high"]}


@pytest.mark.parametrize("tier", ["S"])
def test_the_sample_collection_records_its_projects(tmp_path, tier: str) -> None:
    from ingence.evaluation.sample_lab import UNSCORED_SECTIONS, generate_sample_lab

    assert "projects" in UNSCORED_SECTIONS
    collection = generate_sample_lab(tmp_path / "lab", tier=tier, seed=3)
    body = json.loads(collection.unscored_path.read_text())
    entries = body["sections"]["projects"]["entries"]
    assert entries
    experiments = set(body["sections"]["designs"]["entries"][0].keys())
    assert "folder" in experiments
    folders = {entry["folder"] for entry in body["sections"]["designs"]["entries"]}
    for project in entries:
        assert project["cell_line"] and project["target"] and project["treatments"]
        assert project["control"] and set(project["experiments"]) <= folders
    family = generate_questions(body, seed=1)
    assert len(family.questions) == 30
    assert all(question.expected for question in family.questions)
