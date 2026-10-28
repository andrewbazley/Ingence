"""An answer to one of the engine's questions reaches the target the engine reads.

A verdict written to ``question:<rule>:<value>`` is read by nothing. So the Dashboard's one write
route for a question, ``decide_target``, writes it to the target the engine reads: a proposed
group is answered on ``group:<its core>``, a deviation on ``group:<core with or without the word>``,
a folder of miscellaneous files on ``miscellaneous:<entry id>``, a folder asked whether it is a
program on ``program:<entry id>``, and a pack on ``pack:<domain>``. Each answer then does what it
says. Every name is invented. The person answering is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.revision_4 import each_deviation, write_revision
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.api.routes.review import review
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

PERSON = "trell (an invented person)"
MOMENT = "2026-01-01T00:00:30Z"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _questions(project: Project, rule_id: str) -> list[str]:
    with open_catalog(project.path) as catalog:
        return sorted(
            str(question["raised"])
            for question in review(catalog, limit=10000)["questions"]
            if question["rule_id"] == rule_id
        )


def _answer(project: Project, rule_id: str, raised: str, word: str) -> object:
    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog, project.path)
        return decide_target(
            context,
            Decision(target=f"question:{rule_id}:{raised}", word=word),
            actor=PERSON,
            occurred_at=MOMENT,
        )


def _claims(project: Project, rule_id: str) -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule_id],
        ).fetchall()
    return dict(rows)


def _groups_pool(base: Path) -> Project:
    pool = base / "pool"
    pairs = [
        ("20240301", "kelt vorn brisa"),
        ("20240302", "kelt vorn quolt"),
        *[
            (f"202404{number:02d}", words)
            for number, words in enumerate(
                ("alpa bret", "cind dorn", "elk fenn", "gorb hask", "ilm jorv", "kasp lunt"),
                start=1,
            )
        ],
    ]
    for day, words in pairs:
        for number in (1, 2):
            _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    each_deviation(write_revision(created.path))  # every deviation asked, pinned
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_000000000002", started_at="t")
    return created


def test_a_proposed_group_and_a_deviation_are_answered_on_the_groups_core(tmp_path: Path) -> None:
    created = _groups_pool(tmp_path)
    (proposal,) = [
        raised
        for raised in _questions(created, "experiments_may_belong_together")
        if raised.startswith("kelt vorn")
    ]
    event = _answer(created, "experiments_may_belong_together", proposal, "one_thing")
    assert event.target == "group:kelt vorn"  # type: ignore[attr-defined]
    assert event.kind is RecordEventKind.CONFIRM  # type: ignore[attr-defined]
    run_inference(created, run_id="run_20260101T000003Z_000000000003", started_at="t")
    assert set(_claims(created, "group_resolved_across_experiments").values()) == {"kelt vorn"}
    assert not [
        raised
        for raised in _questions(created, "experiments_may_belong_together")
        if raised.startswith("kelt vorn")
    ]  # answered: its members are resolved, and it is not proposed again
    deviations = _questions(created, "group_resolution_deviation")
    assert deviations == [
        "brisa, added to the group kelt vorn",
        "quolt, added to the group kelt vorn",
    ]
    event = _answer(created, "group_resolution_deviation", deviations[0], "correct")
    assert event.target == "group:brisa kelt vorn"  # type: ignore[attr-defined]


def test_a_proposed_group_answered_on_the_page_groups_every_file_of_its_members(
    tmp_path: Path,
) -> None:
    """The page writes a proposal's answer to ``group:<core>``, so the rule reading approved
    proposals must look there and not only at ``question:<rule>:<value>``."""
    created = _groups_pool(tmp_path)
    (proposal,) = [
        raised
        for raised in _questions(created, "experiments_may_belong_together")
        if raised.startswith("kelt vorn")
    ]
    event = _answer(created, "experiments_may_belong_together", proposal, "one_thing")
    assert event.target == "group:kelt vorn"  # type: ignore[attr-defined]
    run_inference(created, run_id="run_20260101T000003Z_000000000003", started_at="t")
    together = _claims(created, "experiments_approved_together")
    assert {path.split("/")[0] for path in together} == {
        "20240301 kelt vorn brisa",
        "20240302 kelt vorn quolt",
    }
    assert set(together.values()) == {proposal}


def test_a_declined_group_is_not_proposed_again(tmp_path: Path) -> None:
    created = _groups_pool(tmp_path)
    (proposal,) = [
        raised
        for raised in _questions(created, "experiments_may_belong_together")
        if raised.startswith("kelt vorn")
    ]
    event = _answer(created, "experiments_may_belong_together", proposal, "mixed")
    assert event.target == "group:kelt vorn"  # type: ignore[attr-defined]
    assert event.kind is RecordEventKind.REJECT  # type: ignore[attr-defined]
    run_inference(created, run_id="run_20260101T000003Z_000000000003", started_at="t")
    assert not [
        raised
        for raised in _questions(created, "experiments_may_belong_together")
        if raised.startswith("kelt vorn")
    ]
    assert _claims(created, "group_resolved_across_experiments") == {}


def test_a_folder_question_is_answered_on_its_folders_own_target(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2"):
        _write(pool / "20240301 kelt vorn" / name)
    _write(pool / "brisa assay" / "plate layout.xlsx")
    _write(pool / "brisa assay" / "brisa scores.csv")
    _write(pool / "zibtool" / "bin" / "zibtool.exe")
    _write(pool / "zibtool" / "LICENSE.txt")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_000000000002", started_at="t")
    with open_catalog(created.path) as catalog:
        ids = dict(
            catalog.query(
                "SELECT relative_path, entry_id FROM entries WHERE relative_path IN "
                "('brisa assay', 'zibtool')"
            ).fetchall()
        )
    misc = "brisa assay: miscellaneous files"
    assert misc in _questions(created, "miscellaneous_files_to_approve")
    event = _answer(created, "miscellaneous_files_to_approve", misc, "correct")
    assert event.target == f"miscellaneous:{ids['brisa assay']}"  # type: ignore[attr-defined]
    (program,) = _questions(created, "software_outside_the_catalogue")
    event = _answer(created, "software_outside_the_catalogue", program, "wrong")
    assert event.target == f"program:{ids['zibtool']}"  # type: ignore[attr-defined]
    assert event.kind is RecordEventKind.REJECT  # type: ignore[attr-defined]
    run_inference(created, run_id="run_20260101T000003Z_000000000003", started_at="t")
    assert _claims(created, "miscellaneous_files_approved") == {"brisa assay": "miscellaneous"}
    assert misc not in _questions(created, "miscellaneous_files_to_approve")
    assert _questions(created, "software_outside_the_catalogue") == []
