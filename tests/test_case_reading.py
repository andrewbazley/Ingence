"""A spelling a capital divides is read whole or as its pieces, once per spelling for the run.

A glued name that a laboratory uses as a unit is one word. Two ordinary words joined at a capital
are two. The Dictionary's whole spelling is its first guess, and a person's answer wins. Every
name here is invented.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.core.schema import RecordEvent, RecordEventKind
from ingence.dictionary.case_reading import (
    CaseCounts,
    CaseEvidence,
    Reading,
    answered_cases,
    case_target,
    decide_case,
)
from ingence.dictionary.discover import CASE_WHOLE_CONSTRAINT
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.record.log import append_event
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

STORE = DictionaryStore.builtin()
READER = Normaliser(STORE, years=(2013, 2026))


def _evidence(whole: int, alone: tuple[int, ...]) -> CaseEvidence:
    return CaseEvidence(
        written="zibKelt",
        pieces=("zib", "Kelt"),
        whole_names=whole,
        alone=alone,
        partners=(1,) * len(alone),
    )


def test_a_spelling_written_as_often_as_its_rarest_piece_alone_is_one_word() -> None:
    reading = decide_case(_evidence(40, (3, 900)), whole_known=False)
    assert (reading.reading, reading.tier) == (Reading.WHOLE, "unit")


def test_two_words_each_written_alone_more_often_are_two() -> None:
    reading = decide_case(_evidence(2, (30, 11)), whole_known=False)
    assert (reading.reading, reading.tier) == (Reading.PIECES, "words")


def test_a_piece_never_written_alone_keeps_the_spelling_whole() -> None:
    assert decide_case(_evidence(1, (0, 50)), whole_known=False).reading is Reading.WHOLE


def test_the_ratio_is_a_setting() -> None:
    assert decide_case(_evidence(40, (30, 900)), whole_known=False).reading is Reading.WHOLE
    assert (
        decide_case(_evidence(40, (30, 900)), whole_known=False, whole_over_alone=2.0).reading
        is Reading.PIECES
    )


def test_the_dictionarys_whole_spelling_is_the_first_guess_and_asks_when_the_counts_disagree() -> (
    None
):
    agrees = decide_case(_evidence(40, (3, 900)), whole_known=True)
    disagrees = decide_case(_evidence(2, (30, 11)), whole_known=True)
    assert (agrees.reading, agrees.tier, agrees.ask) == (Reading.WHOLE, "dictionary", False)
    assert (disagrees.reading, disagrees.ask) == (Reading.WHOLE, True)
    assert "2 names carry zibKelt" in disagrees.because


@pytest.mark.parametrize("answer", [Reading.WHOLE, Reading.PIECES])
def test_a_persons_answer_wins(answer: Reading) -> None:
    for whole_known in (True, False):
        reading = decide_case(_evidence(2, (30, 11)), whole_known=whole_known, answered=answer)
        assert (reading.reading, reading.tier) == (answer, "answered")


def test_the_counts_are_names_carrying_each_word() -> None:
    counts = CaseCounts()
    for name in ["zibKelt_1", "zibKelt_zibKelt_2", "zib_3", "Kelt kelt", "brisa_zibkelt"]:
        counts.observe(READER.atoms(name))
    (evidence,) = counts.evidence().values()
    # Glued in two names, written as one word in a third; each piece counted once per name.
    assert (evidence.written, evidence.pieces) == ("zibKelt", ("zib", "Kelt"))
    assert (evidence.whole_names, evidence.alone) == (3, (1, 1))


def _event(target: str, kind: RecordEventKind, sequence: int, payload: str = "{}") -> RecordEvent:
    return RecordEvent.model_construct(target=target, kind=kind, payload=payload, sequence=sequence)


def test_a_split_target_says_whole_or_pieces_the_latest_winning() -> None:
    """``split:<spelling>``, its payload's ``reading`` (or its kind, merge or split) the answer."""
    assert case_target("zibKelt") == "split:zibkelt"
    events = [
        _event(case_target("vornZib"), RecordEventKind.ASSIGN, 0, json.dumps({"reading": "whole"})),
        _event(case_target("kelZor"), RecordEventKind.ASSIGN, 0, json.dumps({"reading": "pieces"})),
        _event(case_target("zibKelt"), RecordEventKind.MERGE, 1),
        _event(case_target("brisaVorn"), RecordEventKind.MERGE, 2),
        _event(case_target("brisaVorn"), RecordEventKind.SPLIT, 3),
        _event(
            case_target("keltZib"), RecordEventKind.ASSIGN, 4, json.dumps({"answer": "one word"})
        ),
        _event("term:zib", RecordEventKind.MERGE, 5),
    ]
    assert answered_cases(events) == {
        "vornzib": Reading.WHOLE,
        "kelzor": Reading.PIECES,
        "zibkelt": Reading.WHOLE,
        "brisavorn": Reading.PIECES,
        "keltzib": Reading.WHOLE,
    }


def _pool(tmp_path: Path, names: list[str]) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    for name in names:
        (pool / name).write_text("a")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool])


def _pieces_bound(project: Project, name: str) -> list[tuple[str, str | None, str | None]]:
    with open_catalog(project.path) as catalog:
        return sorted(
            catalog.query(
                "SELECT c.raw_value, b.constraint, b.term FROM dictionary_bindings b "
                "JOIN components c ON c.component_id = b.component_id "
                "JOIN entries e ON e.entry_id = b.entry_id "
                "WHERE e.name = ? AND c.component_type = 'word'",
                [name],
            ).fetchall()
        )


def _census(project: Project, run: int = 1) -> None:
    assert run_census(
        Project.open(project.path),
        run_id=f"run_20260101T00000{run}Z_{run}{run}{run}{run}{run}{run}{run}{run}{run}{run}{run}{run}",
        started_at=f"2026-01-01T00:00:0{run}Z",
    ).published


def test_the_census_reads_a_unit_whole_and_two_words_as_two(tmp_path: Path) -> None:
    names = [f"zibKelt_{index}.txt" for index in range(4)]
    names += ["brisaVorn_1.txt"] + [f"brisa_{index}.txt" for index in range(3)]
    names += [f"Vorn_{index}.txt" for index in range(3)] + ["zib_9.txt"]
    project = _pool(tmp_path, names)
    _census(project)
    assert {constraint for _, constraint, _ in _pieces_bound(project, "zibKelt_0.txt")} == {
        CASE_WHOLE_CONSTRAINT
    }
    assert {constraint for _, constraint, _ in _pieces_bound(project, "brisaVorn_1.txt")} == {None}


def test_a_spelling_the_dictionary_knows_whole_binds_its_sense_on_every_piece(
    tmp_path: Path,
) -> None:
    project = _pool(tmp_path, ["kelt_mNeonGreen.txt", "Green_1.txt", "Green_2.txt"])
    _census(project)
    assert _pieces_bound(project, "kelt_mNeonGreen.txt") == [
        ("Green", CASE_WHOLE_CONSTRAINT, "fluorescent_protein"),
        ("kelt", None, None),
        ("mNeon", CASE_WHOLE_CONSTRAINT, "fluorescent_protein"),
    ]


def test_a_persons_answer_reaches_the_next_census(tmp_path: Path) -> None:
    names = ["brisaVorn_1.txt"] + [f"brisa_{index}.txt" for index in range(3)]
    names += [f"Vorn_{index}.txt" for index in range(3)]
    project = _pool(tmp_path, names)
    _census(project, 1)
    assert {c for _, c, _ in _pieces_bound(project, "brisaVorn_1.txt")} == {None}
    append_event(
        project.path,
        target=case_target("brisaVorn"),
        kind=RecordEventKind.ASSIGN,
        payload={"reading": "whole"},
        actor="kelt",
        occurred_at="2026-01-01T00:00:02Z",
        against_run_id="run_20260101T000001Z_111111111111",
    )
    _census(project, 3)
    assert {c for _, c, _ in _pieces_bound(project, "brisaVorn_1.txt")} == {CASE_WHOLE_CONSTRAINT}


def test_the_journal_says_how_each_tier_read(tmp_path: Path) -> None:
    names = [f"zibKelt_{index}.txt" for index in range(4)] + ["zib_9.txt"]
    project = _pool(tmp_path, names)
    _census(project)
    with open_catalog(project.path) as catalog:
        (fields,) = catalog.query(
            "SELECT detail FROM journal_events WHERE event = 'case.readings'"
        ).fetchone()
    summary = json.loads(fields)
    assert summary["by_tier"] == {"unit:whole": 1}
    assert summary["asked"] == 0


def test_reading_whole_moves_no_component(tmp_path: Path) -> None:
    """Only a binding moves: the pieces are the components the tokeniser cut, either way."""
    project = _pool(tmp_path, [f"zibKelt_{index}.txt" for index in range(3)])
    _census(project)
    with open_catalog(project.path) as catalog:
        raw = sorted(
            row[0]
            for row in catalog.query(
                "SELECT raw_value FROM components WHERE component_type = 'word' "
                "AND raw_value <> 'pool'"
            ).fetchall()
        )
    assert raw == ["Kelt", "Kelt", "Kelt", "zib", "zib", "zib"]


def test_a_note_after_an_answer_leaves_the_answer_standing() -> None:
    events = [
        _event(case_target("zibKelt"), RecordEventKind.ASSIGN, 1, json.dumps({"reading": "whole"})),
        _event(case_target("zibKelt"), RecordEventKind.NOTE, 2, json.dumps({"text": "checked"})),
    ]
    assert answered_cases(events) == {"zibkelt": Reading.WHOLE}


def test_a_target_written_with_capitals_is_read_casefolded() -> None:
    events = [_event("split:zibKelt", RecordEventKind.ASSIGN, 1, json.dumps({"reading": "whole"}))]
    assert answered_cases(events) == {"zibkelt": Reading.WHOLE}


def test_answers_are_replayed_in_the_records_order() -> None:
    events = [
        _event(
            case_target("zibKelt"), RecordEventKind.ASSIGN, 2, json.dumps({"reading": "pieces"})
        ),
        _event(case_target("zibKelt"), RecordEventKind.ASSIGN, 1, json.dumps({"reading": "whole"})),
    ]
    assert answered_cases(events) == {"zibkelt": Reading.PIECES}


def _piece_binding(project: Project, name: str, raw: str) -> tuple[str, str | None, list[str]]:
    with open_catalog(project.path) as catalog:
        status, constraint, alternatives = catalog.query(
            "SELECT b.resolution_status, b.constraint, b.alternatives_json "
            "FROM dictionary_bindings b JOIN components c ON c.component_id = b.component_id "
            "JOIN entries e ON e.entry_id = b.entry_id WHERE e.name = ? AND c.raw_value = ?",
            [name, raw],
        ).fetchone()
    return status, constraint, [item["term_id"] for item in json.loads(alternatives or "[]")]


def test_a_piece_read_into_an_unknown_whole_keeps_its_own_sense_as_an_alternative(
    tmp_path: Path,
) -> None:
    """Read whole, a spelling the Dictionary does not know binds nothing.

    The sense a piece has alone is kept beside it, shadowed, so more evidence never silently
    erases a reading. The setting ``unknown_whole`` is ``whole`` here (the default is
    ``pieces``)."""
    project = _pool(tmp_path, [f"zibGFP_{index}.txt" for index in range(3)])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {})["case_reading"] = {"unknown_whole": "whole"}
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    _census(project)
    status, constraint, alternatives = _piece_binding(project, "zibGFP_0.txt", "GFP")
    assert (status, constraint) == ("examined_no_evidence", CASE_WHOLE_CONSTRAINT)
    assert len(alternatives) == 1


def test_an_unknown_whole_can_be_read_as_its_pieces_by_a_setting(tmp_path: Path) -> None:
    project = _pool(tmp_path, [f"zibGFP_{index}.txt" for index in range(3)])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {})["case_reading"] = {"unknown_whole": "pieces"}
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    _census(project)
    status, constraint, _ = _piece_binding(project, "zibGFP_0.txt", "GFP")
    assert (status, constraint) == ("resolved", None)


# -- the ratio's default, and a partial rebind across a change -----------------------------------


def test_the_default_ratio_is_one_half() -> None:
    """The default ratio is a half.

    A spelling glued in twenty names whose rarest piece stands alone in thirty is one word at a
    half, and two words at one."""
    from ingence.core.settings import Settings

    assert Settings().dictionary.case_reading.whole_over_alone == 0.5
    assert decide_case(_evidence(20, (30, 900)), whole_known=False).reading is Reading.WHOLE
    assert (
        decide_case(_evidence(20, (30, 900)), whole_known=False, whole_over_alone=1.0).reading
        is Reading.PIECES
    )


def _pool_and_census(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    for name in ("zibKelt_1.txt", "zib_2.txt", "kelt_3.txt"):
        (pool / name).write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260929T000001Z_111111111111", started_at="2026-09-29T00:00:01Z"
    ).published
    return project


def test_a_census_states_the_case_reading_it_read_names_with(tmp_path: Path) -> None:
    project = _pool_and_census(tmp_path)
    with open_catalog(project.path) as catalog:
        (stated,) = catalog.query("SELECT case_reading_json FROM run").fetchone()
    assert json.loads(stated) | {"schema_version": None} == {
        "enabled": True,
        "whole_over_alone": 0.5,
        "unknown_whole": "pieces",
        "schema_version": None,
    }


def test_a_partial_rebind_is_refused_where_the_case_reading_changed(tmp_path: Path) -> None:
    """A partial rebind carries the names it does not read again with their parent's bindings; read
    with another ratio, one divided spelling would be read two ways in one run. The job worker falls
    back to a whole rebind on the words "re-bind them all"."""
    project = _pool_and_census(tmp_path)
    settings = yaml.safe_load((project.path / "settings.yaml").read_text()) or {}
    settings.setdefault("dictionary", {}).setdefault("case_reading", {})["whole_over_alone"] = 2.0
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    with pytest.raises(ValueError, match=r"case reading.*re-bind them all"):
        run_census(
            Project.open(project.path),
            run_id="run_20260929T000002Z_222222222222",
            started_at="2026-09-29T00:00:02Z",
            rebind=True,
            spellings=("zib",),
        )
    whole = run_census(
        Project.open(project.path),
        run_id="run_20260929T000003Z_333333333333",
        started_at="2026-09-29T00:00:03Z",
        rebind=True,
    )
    assert whole.published
    again = run_census(
        Project.open(project.path),
        run_id="run_20260929T000004Z_444444444444",
        started_at="2026-09-29T00:00:04Z",
        rebind=True,
        spellings=("zib",),
    )
    assert again.published
