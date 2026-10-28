"""Groupings' Import reuses the review sheet reader, and writes only what a person takes.

The engine has a reader for a groupings sheet, ``ingence record import`` (``record/sheets.py``). The
page reuses it in its check mode, which lists each answer it would write, one per target, and writes
nothing. A person takes each answer through the answer route, the Record's one way in, and the event
says which sheet it came from.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from ingence.api.routes import workspace as workspace_routes
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.core.project import Project
from ingence.record.log import read_events
from ingence.record.verdicts import class_index, spelled_key
from ingence.storage.catalog import open_catalog

HEADERS = ["class key", "members", "folder(s)", "verdict (one_thing | mixed)", "note"]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from fixtures.revision_4 import write_revision
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("groupings_import")
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_kelt"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2, 3):
            (pool / folder / f"zib_glorbex_wd_{index:02d}.csv").write_text(f"a\n{index}\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project.path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    import shutil

    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def _one_class(project: Path) -> tuple[str, str]:
    """A class the run holds under exactly one rule, as a sheet spells it, and its target."""
    with open_catalog(project) as catalog:
        index = class_index(catalog)
    for key, rules in sorted(index.by_key.items()):
        spelled = spelled_key(key)
        if len(rules) == 1 and len(index.by_spelling.get(spelled, ())) == 1:
            found = index.find(spelled, rule_id=None, folder=None)
            assert not isinstance(found, str)
            return spelled, found.target
    pytest.fail("the invented run holds no class under one rule")


def _sheet(rows: list[list[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADERS)
    writer.writerows(rows)
    return out.getvalue()


def _preview(project: Path, text: str, name: str = "groups.csv") -> dict[str, object]:
    return workspace_routes.write(
        project,
        "/api/workspace/sheet-preview",
        {"document": text, "name": name, "actor": "brisa"},
    )


def test_a_groupings_sheet_is_listed_answer_by_answer_and_nothing_is_written(
    project: Path,
) -> None:
    spelled, target = _one_class(project)
    before = sorted(path.name for path in project.iterdir())
    text = _sheet(
        [[spelled, "3", "x", "mixed", "two batches"], ["no such key", "1", "x", "mixed", ""]]
    )
    shown = _preview(project, text)
    assert shown["kind"] == "class" and shown["rows"] == 2
    assert shown["planned"] == [
        {"target": target, "word": "mixed", "note": "two batches", "correction": None, "rows": [1]}
    ]
    assert sum(shown["refused"].values()) == 1  # type: ignore[union-attr]
    assert read_events(project) == ()
    assert sorted(path.name for path in project.iterdir()) == before


def test_a_sheet_of_another_kind_or_none_is_refused_in_words(project: Path) -> None:
    with pytest.raises(ValueError, match="Groupings reads a groupings sheet"):
        _preview(project, "claim_id,verdict (correct | wrong)\nabc,correct\n")
    with pytest.raises(ValueError, match="cannot be read"):
        _preview(project, "nothing,here\n1,2\n")
    with pytest.raises(ValueError, match="who is deciding"):
        workspace_routes.write(
            project, "/api/workspace/sheet-preview", {"document": _sheet([]), "actor": ""}
        )


def test_an_answer_taken_from_a_sheet_says_which_sheet(project: Path) -> None:
    spelled, target = _one_class(project)
    shown = _preview(project, _sheet([[spelled, "3", "x", "mixed", "two batches"]]))
    planned = shown["planned"][0]  # type: ignore[index]
    body = {
        "target": planned["target"],
        "word": planned["word"],
        "note": planned["note"],
        "sheet": "groups.csv",
    }
    with open_catalog(project) as catalog:
        event = decide_target(
            DecisionContext(catalog, project),
            Decision.from_body(body),
            actor="brisa",
            occurred_at="2026-01-02T00:00:00Z",
        )
    assert (event.actor, event.target) == ("brisa", target)
    payload = json.loads(event.payload)
    assert payload["source"]["imported_from"] == "groups.csv"
    assert payload["note"] == "two batches"
    # Checked again, the answer is already the person's: nothing more would be written.
    again = _preview(project, _sheet([[spelled, "3", "x", "mixed", "two batches"]]))
    assert again["planned"] == [] and again["already"] == 1


def test_a_row_the_sheet_reader_would_refuse_is_refused_in_the_preview_too(project: Path) -> None:
    """A check plans exactly what an import writes. A verdict carrying a correction it cannot carry
    is refused, as the import refuses it, and its note is kept."""
    spelled, target = _one_class(project)
    text = (
        "class key,members,folder(s),verdict (one_thing | mixed),if wrong: the stage,note\n"
        f'"{spelled}",3,x,mixed,two runs,n1\n'
    )
    shown = _preview(project, text)
    assert shown["refused"] == {"the verdict does not stand as written": 1}
    assert shown["planned"] == [
        {"target": target, "word": None, "note": "n1", "correction": None, "rows": [1]}
    ]
    assert read_events(project) == ()


def test_a_word_the_group_is_not_offered_is_refused_in_the_preview(project: Path) -> None:
    spelled, _ = _one_class(project)
    text = f'class key,members,folder(s),verdict (correct | mixed),note\n"{spelled}",3,x,correct,\n'
    shown = _preview(project, text)
    assert shown["planned"] == []
    assert sum(shown["refused"].values()) == 1  # type: ignore[union-attr]
    assert any("not one this group" in why for why in shown["refused"])  # type: ignore[union-attr]
