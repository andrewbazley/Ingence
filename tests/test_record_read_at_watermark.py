"""Every answer a run reads, it reads at the run's Record watermark.

A run is reproducible from Index, Rulebook, Dictionary and Record, and its run record states the
watermark it read the Record at. So a decision written after the watermark -- while the run is
under way, by the Dashboard or another command -- changes nothing that run publishes, and the
next run reads it. One bound does it (``record.log.read_at``): every read of the Record inside it
replays the Record as it stood at the watermark, so every reader, through ``read_events``, reads
the same Record.

The class is held two ways. Each reader returns, inside the bound, what it returned before the
late answer and, outside it, what the answer says. A run whose answers arrive between its
watermark's capture and its readers (another process, simulated by appending inside the capture)
reads no event above its watermark, publishes what the same run publishes with no late answer, and
the next run reads them. Every name is invented. Every answer is an invented person's.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.one_folder_pairs import allow_pairs_in_one_folder
from ingence.api.routes import decide as decide_routes
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, RecordEventKind
from ingence.infer.run import run_inference
from ingence.infer.subjects import EntryRecords, FolderContents
from ingence.jobs import provenance
from ingence.record import log as record_log
from ingence.record.log import append_event, read_at, read_events
from ingence.record.targets import (
    candidate_target,
    class_target,
    extension_target,
    group_target,
    miscellaneous_target,
    pack_target,
    program_target,
    sense_target,
    split_target,
)
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"
LATE = "run_20260101T000003Z_333333333333"
NEXT = "run_20260101T000005Z_555555555555"
MOMENT = "2026-09-30T23:30:00Z"
ACTOR = "wren"
#: 2020-09-13, one instant shared by the copies, so their metadata agrees too.
SHARED_INSTANT = 1_600_000_000


def _pool(base: Path) -> Path:
    """Three copies of one table in three experiments, a raw and processed pair, a note naming
    another machine's path, and three days of one experiment, one adding a word."""
    pool = base / "pool"
    for day, names in (
        ("20240301 kelt vorn", ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2")),
        ("20240302 kelt vorn", ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2")),
        ("20240303 kelt vorn", ("kelt_vorn_01.nd2", "kelt_vorn_trell_02.nd2")),
    ):
        for name in names:
            (pool / day).mkdir(parents=True, exist_ok=True)
            (pool / day / name).write_text("x")
    for folder in ("2024-03-05_kelt", "2024-03-06_vorn", "2024-03-07_zeph"):
        copy = pool / "runs" / folder / "brisa_19_quant.csv"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text("a,b\n1,2\n3,4\n" * 40)
        os.utime(copy, (SHARED_INSTANT, SHARED_INSTANT))
    plates = pool / "plates"
    plates.mkdir(parents=True)
    (plates / "raw_plate_alpha.csv").write_text("a,b\n1,2\n")
    (plates / "processed_plate_alpha.csv").write_text("a,b\n1,2\n5,6\n")
    # The raw table an hour before the processed one: a pair's direction is its time.
    for turn, name in enumerate(("raw_plate_alpha.csv", "processed_plate_alpha.csv"), start=1):
        at = SHARED_INSTANT + turn * 3600
        os.utime(plates / name, (at, at))
    (plates / "QuoltTable notes.txt").write_text("read C:/elsewhere/brisa/raw_plate_alpha.csv\n")
    return pool


@pytest.fixture
def project(tmp_path: Path) -> Project:
    made = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    # Its raw and processed pair lies in one folder, which a name identity leaves as siblings.
    # These tests are about answers read at a watermark, so the pair is let be ordered there.
    allow_pairs_in_one_folder(made.path)
    run_census(made, run_id=CENSUS, started_at="t")
    run_inference(Project.open(made.path), run_id=INFERENCE, started_at="t")
    return Project.open(made.path)


def _entries(project: Project) -> dict[str, Entry]:
    (root,) = project.identity.roots
    with open_catalog(project.path) as catalog:
        found = catalog.live_entries(root.root_id)
    return {entry.relative_path: entry for entry in found.values()}


def _folder(project: Project, relative: str) -> str:
    return _entries(project)[relative].entry_id


def _candidate(project: Project) -> tuple[str, str, str]:
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT candidate_id, source_id, target_id FROM candidates ORDER BY candidate_id"
        ).fetchone()
    assert row is not None, "the fixture proposes the plate pair"
    return str(row[0]), str(row[1]), str(row[2])


def _copies_class(project: Project) -> str:
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT rule_id, object_reference FROM claims "
            "WHERE rule_id LIKE '%metadata%' AND object_reference IS NOT NULL "
            "ORDER BY rule_id, object_reference"
        ).fetchone()
    assert row is not None, "the fixture's copies form a class"
    return class_target(str(row[0]), str(row[1]))


def _say(project: Project, kind: RecordEventKind, target: str, **payload: Any) -> None:
    append_event(
        project.path,
        kind=kind,
        actor=ACTOR,
        occurred_at=MOMENT,
        target=target,
        against_run_id=INFERENCE,
        payload=payload or None,
    )


# ---------------------------------------------------------------------------
# Each answer a run reads, and the reader that reads it.
# ---------------------------------------------------------------------------


def _late_copies(project: Project) -> None:
    _say(project, RecordEventKind.REJECT, _copies_class(project), verdict="not_copies")


def _late_pair(project: Project) -> None:
    candidate, source, target = _candidate(project)
    _say(
        project,
        RecordEventKind.REJECT,
        candidate_target(candidate),
        verdict="unrelated",
        source_id=source,
        target_id=target,
    )


def _late_workflow(project: Project) -> None:
    _say(
        project,
        RecordEventKind.DEFINE,
        "workflow:brisa",
        name="brisa",
        rungs=["raw", "processed"],
        observation=0,
    )


def _late_prefix(project: Project) -> None:
    (root,) = project.identity.roots
    _say(project, RecordEventKind.DEFINE, "prefix:C:/elsewhere", root_id=root.root_id)


def _late_not_a_program(project: Project) -> None:
    _say(project, RecordEventKind.REJECT, program_target(_folder(project, "plates")))


def _late_a_program(project: Project) -> None:
    _say(project, RecordEventKind.CONFIRM, program_target(_folder(project, "runs")))


def _late_miscellaneous(project: Project) -> None:
    _say(project, RecordEventKind.CONFIRM, miscellaneous_target(_folder(project, "plates")))


def _late_group_declined(project: Project) -> None:
    _say(project, RecordEventKind.REJECT, group_target(["brisa", "quant"]))


def _late_group_approved(project: Project) -> None:
    _say(project, RecordEventKind.CONFIRM, group_target(["kelt", "vorn"]), terms=["kelt", "vorn"])


def _late_split(project: Project) -> None:
    _say(project, RecordEventKind.SPLIT, split_target("QuoltTable"), reading="pieces")


def _late_inactivation(project: Project) -> None:
    _say(project, RecordEventKind.REJECT, sense_target("raw"))


def _late_word(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        decide_routes.define_word(
            catalog,
            project.path,
            actor=ACTOR,
            occurred_at=MOMENT,
            word="quolt",
            answer="a quolt sample",
            theme_kind="subject",
        )


def _late_pack(project: Project) -> None:
    # A yes is a prior only where it answered "load the X pack" (or came from init).
    _say(
        project,
        RecordEventKind.CONFIRM,
        pack_target("microscopy"),
        question="load the microscopy pack",
    )


def _late_extension(project: Project) -> None:
    _say(project, RecordEventKind.ANNOTATE, extension_target("csv"))


def _held(project: Project) -> tuple[EntryRecords, ...]:
    """Every folder's records as inference holds them, with what its contents are counted as."""
    return tuple(
        EntryRecords(entry=entry, contents=FolderContents(entry=entry))
        for _, entry in sorted(_entries(project).items())
        if entry.entry_type is EntryType.DIRECTORY
    )


def _read_answers(project: Project) -> object:
    from ingence.record.answers import answers_read

    read = answers_read(read_events(project.path))
    return (sorted(read.copies), sorted(sorted(ends) for ends in read.pairs))


def _read_workflows(project: Project) -> object:
    from ingence.record.workflow import workflows_in_force

    return workflows_in_force(project.path)


def _read_prefixes(project: Project) -> object:
    from ingence.infer.references import prefix_mappings_in_force

    return prefix_mappings_in_force(project.path)


def _read_programs(project: Project) -> object:
    from ingence.infer.run import _with_folders_not_programs

    return [
        (held.not_a_program, held.a_program)
        for held in _with_folders_not_programs(_held(project), project.path)
    ]


def _read_miscellaneous(project: Project) -> object:
    from ingence.infer.run import _with_miscellaneous_answers

    return [
        held.contents.miscellaneous_answer if held.contents is not None else None
        for held in _with_miscellaneous_answers(_held(project), project.path)
    ]


def _read_declined(project: Project) -> object:
    from ingence.infer.run import _declined_groups
    from ingence.rulebook import project_rulebook

    in_force = {rule.rule_id: rule for rule in project_rulebook(project.path).rules}
    return _declined_groups(project.path, in_force)


#: Each reading pass a reader runs takes the next run identifier.
_PASSES = iter(range(10, 99))


def _read_resolved_groups(project: Project) -> object:
    """What the engine's own readers of approved groups resolve: a pass, its groups across days.

    A pass inside a bound reads at it: the watermark it captures is the bound's.
    """
    number = next(_PASSES)
    run_id = f"run_20260101T0001{number:02d}Z_{number:012d}"
    run_inference(Project.open(project.path), run_id=run_id, started_at="t")
    with open_catalog(project.path, run_id=run_id) as catalog:
        return sorted(
            catalog.query(
                "SELECT subject, object_reference FROM claims "
                "WHERE rule_id = 'group_resolved_across_experiments' "
                "AND resolution_status = 'resolved'"
            ).fetchall()
        )


def _read_cases(project: Project) -> object:
    from ingence.dictionary.case_reading import project_answers

    return project_answers(project.path)


def _read_inactive(project: Project) -> object:
    from ingence.dictionary.inactive import inactivations_in_force

    return inactivations_in_force(project.path).as_payload()


def _read_vocabulary(project: Project) -> object:
    from ingence.dictionary.store import DictionaryStore

    return DictionaryStore.for_project(project.path).fingerprint()


def _read_packs(project: Project) -> object:
    from ingence.dictionary.packs.proposals import domain_priors

    return domain_priors(project.path)


def _read_extensions(project: Project) -> object:
    from ingence.record.verdicts import worth_reading

    return worth_reading(project.path)


#: Each answer a run reads, the reader that reads it, by what it is about.
READERS: dict[str, tuple[Callable[[Project], None], Callable[[Project], object]]] = {
    "copies": (_late_copies, _read_answers),
    "pair": (_late_pair, _read_answers),
    "workflow": (_late_workflow, _read_workflows),
    "prefix": (_late_prefix, _read_prefixes),
    "not a program": (_late_not_a_program, _read_programs),
    "a program": (_late_a_program, _read_programs),
    "miscellaneous": (_late_miscellaneous, _read_miscellaneous),
    "group declined": (_late_group_declined, _read_declined),
    "group approved": (_late_group_approved, _read_resolved_groups),
    "split": (_late_split, _read_cases),
    "inactivation": (_late_inactivation, _read_inactive),
    "inactivation in the vocabulary": (_late_inactivation, _read_vocabulary),
    "word defined": (_late_word, _read_vocabulary),
    "pack accepted": (_late_pack, _read_packs),
    "extension": (_late_extension, _read_extensions),
}


@pytest.mark.parametrize("about", sorted(READERS))
def test_each_reader_reads_the_record_as_it_stood_at_a_watermark(
    project: Project, about: str
) -> None:
    late, reader = READERS[about]
    watermark = provenance.record_watermark(project.path)
    before = reader(project)
    late(project)
    assert provenance.record_watermark(project.path) > watermark
    with read_at(project.path, watermark):
        assert reader(project) == before, "an answer after the watermark is not read"
    # The next run, at the next watermark, reads it.
    assert reader(project) != before
    with read_at(project.path, provenance.record_watermark(project.path)):
        assert reader(project) != before


def test_a_bound_is_the_project_s_own_and_ends_with_its_scope(
    project: Project, tmp_path: Path
) -> None:
    _late_pack(project)
    watermark = provenance.record_watermark(project.path)
    _late_workflow(project)
    other = tmp_path / "elsewhere"
    with read_at(other, 0):
        assert read_events(project.path)[-1].sequence > watermark, "another project's bound"
    with read_at(project.path, watermark), read_at(project.path, watermark + 5):
        # A bound inside another never reaches past it.
        assert max(event.sequence for event in read_events(project.path)) == watermark
    assert read_events(project.path)[-1].sequence > watermark
    with read_at(project.path, None):
        assert read_events(project.path)[-1].sequence > watermark, "no watermark, no bound"


def test_a_withdrawal_after_the_watermark_does_not_reach_back(project: Project) -> None:
    from ingence.record.answers import events_at

    _late_workflow(project)
    watermark = provenance.record_watermark(project.path)
    (said,) = [event for event in read_events(project.path) if event.target == "workflow:brisa"]
    _say(
        project,
        RecordEventKind.WITHDRAW,
        "restore:" + INFERENCE,
        withdrawn=[said.event_id],
        restored_to=INFERENCE,
        previewed="a preview",
    )
    assert "workflow:brisa" not in {event.target for event in read_events(project.path)}
    with read_at(project.path, watermark):
        assert "workflow:brisa" in {event.target for event in read_events(project.path)}
    # The answers' own reading at a watermark is this one.
    assert events_at(project.path, watermark) == _bounded(project, watermark)


def _bounded(project: Project, watermark: int) -> tuple[Any, ...]:
    with read_at(project.path, watermark):
        return read_events(project.path)


# ---------------------------------------------------------------------------
# A run whose answers arrive while it runs.
# ---------------------------------------------------------------------------

#: Every late answer at once, one of each kind a run reads.
EVERY_LATE = tuple(dict.fromkeys(late for late, _ in READERS.values()))


def _arriving_late(
    monkeypatch: pytest.MonkeyPatch, module: str, project: Project, seen: list[int]
) -> dict[str, int]:
    """Append every late answer between the run's watermark's capture and its first reader.

    What writing them read is not the run's reading, so the run's reads are counted from here.
    """
    captured: dict[str, int] = {}
    real = provenance.record_watermark

    def watermark_then_answers(path: Path) -> int:
        captured["watermark"] = real(path)
        for late in EVERY_LATE:
            late(project)
        seen.clear()
        return captured["watermark"]

    monkeypatch.setattr(f"{module}.record_watermark", watermark_then_answers)
    return captured


def _spied(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The highest sequence each read of the Record returned, read by read: every read, with
    withdrawn events or without, passes through the bound."""
    seen: list[int] = []
    real = record_log._within_bound

    def spy(project: Path, events: Any) -> Any:
        found = real(project, events)
        seen.append(max((event.sequence for event in found), default=0))
        return found

    monkeypatch.setattr(record_log, "_within_bound", spy)
    return seen


def _early_word(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        decide_routes.define_word(
            catalog,
            project.path,
            actor=ACTOR,
            occurred_at=MOMENT,
            word="kelt",
            answer="a kelt sample",
            theme_kind="subject",
        )


def _early_extension(project: Project) -> None:
    _say(project, RecordEventKind.ANNOTATE, extension_target("txt"))


def _stated(project: Project, run_id: str) -> tuple[Any, ...]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        found = catalog.query("SELECT dictionary_hash, record_watermark FROM run").fetchone()
    assert found is not None
    return tuple(found)


def _published(project: Project, run_id: str) -> dict[str, Any]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        claims = catalog.query(
            "SELECT subject, rule_id, object_reference, resolution_status, defeaters_json "
            "FROM claims ORDER BY subject, rule_id, object_reference"
        ).fetchall()
        candidates = catalog.query(
            "SELECT source_id, target_id, rule_id FROM candidates ORDER BY 1, 2, 3"
        ).fetchall()
        findings = catalog.query(
            "SELECT rule_id, subject, raised FROM findings ORDER BY 1, 2"
        ).fetchall()
        run = catalog.query("SELECT dictionary_hash, record_watermark FROM run").fetchone()
    return {
        "claims": [(*row[:4], json.loads(row[4] or "[]")) for row in claims],
        "candidates": candidates,
        "findings": [(row[0], row[1], sorted(row[2] or ())) for row in findings],
        "dictionary_hash": run[0],
        "watermark": run[1],
    }


def test_an_answer_written_while_inference_runs_changes_nothing_it_publishes(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    # The same run with no late answer, on a copy of the project as it stands.
    alone = project.path.parent / "alone"
    shutil.copytree(project.path, alone)
    run_inference(Project.open(alone), run_id=LATE, started_at="t")
    unanswered = _published(Project.open(alone), LATE)

    seen = _spied(monkeypatch)
    captured = _arriving_late(monkeypatch, "ingence.infer.run", project, seen)
    run_inference(Project.open(project.path), run_id=LATE, started_at="t")
    watermark = captured["watermark"]
    assert provenance.record_watermark(project.path) > watermark, "the answers arrived"
    assert seen, "the run read the Record"
    assert max(seen) <= watermark, "no read during the run reached past its watermark"
    published = _published(project, LATE)
    assert published == unanswered

    # The next run reads them: its watermark holds them, and what it publishes changes.
    monkeypatch.undo()
    seen = _spied(monkeypatch)
    run_inference(Project.open(project.path), run_id=NEXT, started_at="t")
    assert max(seen) == provenance.record_watermark(project.path)
    after = _published(project, NEXT)
    assert after["watermark"] == provenance.record_watermark(project.path)
    assert after["claims"] != unanswered["claims"], "the copies answered unrelated now conflict"
    assert after["dictionary_hash"] != unanswered["dictionary_hash"], "the word and the sense"


def test_an_answer_written_while_the_census_reads_names_changes_nothing_it_binds(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An answer before the run: read, since the run's watermark holds it.
    _early_word(project)
    seen = _spied(monkeypatch)
    captured = _arriving_late(monkeypatch, "ingence.scan.census", project, seen)
    run_census(Project.open(project.path), run_id=LATE, started_at="t", rebind=True)
    watermark = captured["watermark"]
    assert watermark > 0
    assert seen, "the census read the Record"
    assert max(seen) == watermark, "read up to its watermark, and nothing past it"
    bound_with, stated = _stated(project, LATE)
    assert stated == watermark
    assert bound_with != _stated(project, CENSUS)[0], "the early word is in its vocabulary"
    # The next run reads the late answers.
    monkeypatch.undo()
    seen = _spied(monkeypatch)
    run_census(Project.open(project.path), run_id=NEXT, started_at="t", rebind=True)
    now = provenance.record_watermark(project.path)
    assert max(seen) == now
    assert _stated(project, NEXT)[1] == now
    assert _stated(project, NEXT)[0] != bound_with, "the late word and the sense"


def test_an_answer_written_while_a_dive_runs_changes_nothing_it_reads(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _early_extension(project)
    seen = _spied(monkeypatch)
    captured = _arriving_late(monkeypatch, "ingence.scan.deepen", project, seen)
    run_deepen(Project.open(project.path), run_id=LATE, started_at="t", budget_seconds=5)
    watermark = captured["watermark"]
    assert watermark > 0
    assert seen, "the dive read the Record"
    assert max(seen) == watermark, "read up to its watermark, and nothing past it"
    assert _stated(project, LATE)[1] == watermark
    monkeypatch.undo()
    seen = _spied(monkeypatch)
    run_deepen(Project.open(project.path), run_id=NEXT, started_at="t", budget_seconds=5)
    assert max(seen) == provenance.record_watermark(project.path)


def test_a_watermark_read_inside_a_bound_is_the_bound(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _late_pack(project)
    outer = provenance.record_watermark(project.path)
    _late_workflow(project)
    with read_at(project.path, outer):
        assert provenance.record_watermark(project.path) == outer
    assert provenance.record_watermark(project.path) > outer
    # A run started inside a bound states the watermark it reads at.
    seen = _spied(monkeypatch)
    with read_at(project.path, outer):
        run_inference(Project.open(project.path), run_id=LATE, started_at="t")
    assert _stated(project, LATE)[1] == outer == max(seen)


def test_the_graph_page_reads_a_run_s_prefixes_as_the_run_read_them(project: Project) -> None:
    from ingence.api.v1 import graph
    from ingence.infer.references import prefix_mappings_in_force

    _late_prefix(project)
    run_inference(Project.open(project.path), run_id=NEXT, started_at="t")
    watermark = _stated(project, NEXT)[1]
    (said,) = [event for event in read_events(project.path) if event.target.startswith("prefix:")]
    # A restore withdraws the mapping after the run: it does not reach back on the run's page.
    _say(
        project,
        RecordEventKind.WITHDRAW,
        "restore:" + INFERENCE,
        withdrawn=[said.event_id],
        restored_to=INFERENCE,
        previewed="a preview",
    )
    with read_at(project.path, watermark):
        run_read = prefix_mappings_in_force(project.path)
    page, words = graph._prefix_mappings(project.path, watermark)
    assert run_read
    assert (page, words) == (run_read, f"1 from the Record up to event {watermark}")
