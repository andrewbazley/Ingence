"""A person's answer reaching the Dictionary, and the loop from a question back to a claim.

An answer becomes a Record event, and the Record is one of the four inputs from which every
conclusion can be made again (the Index, the Rulebook, the Dictionary and the Record).
These tests walk the whole way round. A rule raises a question, a person answers it, the next
index binds what they said, and a rule concludes from the binding. A test that stops at one end
cannot see a gap between the two.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.routes.decide import AnswerRefusedError, answer_question
from ingence.core.ids import PROJECT_SCOPE, Scope, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import DictionaryOrigin, RecordEventKind
from ingence.dictionary.answers import answered_sections, section_from_answers
from ingence.dictionary.store import DictionaryStore
from ingence.infer.run import run_inference
from ingence.record.questions import QuestionAnswer, answers_in_force, orphaned_answers
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

LABEL_RULE = "indexed_label_may_mark_replicates"
REPLICATE_RULE = "replicate_from_indexed_label"

#: ``qq`` binds nothing whole and nothing as a label in the shipped Dictionary, which the
#: first test asserts rather than assuming, so the question is real and invents no vocabulary.
UNPLACED = "qq"


def _run(step: int) -> str:
    return f"run_2026010{step}T000001Z_{str(step) * 12}"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """Two folders, each holding one file per index of a label the Dictionary places nowhere.

    Each name carries two words beside the index, because ``same_name_identity`` asks for two
    components and leaving the index out takes one away: a name of one word and an index has
    no identity once the index is ignored, which is the rule doing what it says.
    """
    pool = tmp_path / "pool"
    for folder in ("first", "second"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"{folder}_plate_{UNPLACED}{index}.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id=_run(1), started_at="t")
    run_inference(project, run_id=_run(2), started_at="t")
    return project


def _answer(project: Project, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "actor": "robin",
        "occurred_at": "2026-09-18T15:00:00Z",
        "rule_id": LABEL_RULE,
        "raised": UNPLACED,
        "kind": RecordEventKind.DEFINE,
        "answer": "biological_replicate",
        "theme_kind": "replicate",
    }
    arguments.update(overrides)
    with open_catalog(project.path) as catalog:
        return answer_question(catalog, project.path, **arguments)


def _claims(project: Project, rule_id: str) -> list[tuple[object, ...]]:
    with open_catalog(project.path) as catalog:
        return catalog.query(
            "SELECT e.name, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject WHERE c.rule_id = ? ORDER BY e.name",
            [rule_id],
        ).fetchall()


# ---------------------------------------------------------------------------
# The loop.
# ---------------------------------------------------------------------------


def test_a_question_answered_is_a_binding_on_the_next_index_and_a_claim_after_it(
    project: Project,
) -> None:
    """The whole way round, which is the thing neither end could show on its own."""
    # Nothing concludes a replicate before the answer, because nothing places the label.
    assert _claims(project, REPLICATE_RULE) == []
    assert DictionaryStore.builtin().look_up(UNPLACED, scope=PROJECT_SCOPE) is None

    _answer(project)

    # The Dictionary a run consults now holds what the person said, at USER origin.
    store = DictionaryStore.for_project(project.path)
    hit = store.look_up(UNPLACED, scope=PROJECT_SCOPE)
    assert hit is not None
    assert (hit.sense.term, hit.sense.theme_kind) == ("biological_replicate", "replicate")
    assert hit.sense.origin is DictionaryOrigin.USER

    run_census(project, run_id=_run(3), started_at="t")
    run_inference(project, run_id=_run(4), started_at="t")

    concluded = dict(_claims(project, REPLICATE_RULE))
    assert concluded == {
        "first_plate_qq1.csv": '{"index":1,"sense":"biological_replicate"}',
        "first_plate_qq2.csv": '{"index":2,"sense":"biological_replicate"}',
        "second_plate_qq1.csv": '{"index":1,"sense":"biological_replicate"}',
        "second_plate_qq2.csv": '{"index":2,"sense":"biological_replicate"}',
    }


def test_answering_makes_two_replicates_one_experiment(project: Project) -> None:
    """The identity leaves out an index whose label binds a replicate, so the two merge."""
    _answer(project)
    run_census(project, run_id=_run(3), started_at="t")
    run_inference(project, run_id=_run(4), started_at="t")
    with open_catalog(project.path) as catalog:
        classes = catalog.query(
            "SELECT count(DISTINCT object_reference) FROM claims "
            "WHERE rule_id = 'same_name_identity' AND resolution_status = 'resolved'",
            [],
        ).fetchone()[0]
        members = catalog.query(
            "SELECT count(*) FROM claims WHERE rule_id = 'same_name_identity' "
            "AND resolution_status = 'resolved'",
            [],
        ).fetchone()[0]
    # One class per folder, holding that folder's two replicates.
    assert (classes, members) == (2, 4)


def test_the_question_stops_being_raised_once_it_is_answered(project: Project) -> None:
    """A label the Dictionary now places is a settled question and is not asked again."""
    _answer(project)
    run_census(project, run_id=_run(3), started_at="t")
    run_inference(project, run_id=_run(4), started_at="t")
    with open_catalog(project.path) as catalog:
        raised = catalog.query(
            "SELECT count(*) FROM findings WHERE rule_id = ?", [LABEL_RULE]
        ).fetchone()[0]
    assert raised == 0


# ---------------------------------------------------------------------------
# What the Record must carry for any of that to be readable.
# ---------------------------------------------------------------------------


def test_a_definition_states_the_axis_its_sense_sits_on(project: Project) -> None:
    """A sense on no axis is a sense no rule reads, so the answer would change nothing."""
    with pytest.raises(AnswerRefusedError, match="names its kind"):
        _answer(project, theme_kind=None)


def test_an_assignment_takes_the_axis_the_engine_already_holds(tmp_path: Path) -> None:
    """Naming a different one is defining another sense under the same name.

    ``corr`` is corrected (a stage) and a correlation (a statistic), and both senses are the
    core's.
    """
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    for name in ("vorn_corr_raw.csv", "vorn_corr_processed.csv"):
        (pool / "kelt" / name).write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=_run(1), started_at="t")
    run_inference(project, run_id=_run(2), started_at="t")
    with open_catalog(project.path) as catalog:
        with pytest.raises(AnswerRefusedError, match="axis"):
            answer_question(
                catalog,
                project.path,
                actor="robin",
                occurred_at="2026-09-18T15:00:00Z",
                rule_id="binding_is_ambiguous_outside_programs_and_domains",
                raised="corr",
                kind=RecordEventKind.ASSIGN,
                answer="correlation",
                theme_kind="reagent",
            )
        # The axis the question offers is recorded without being asked for.
        event = answer_question(
            catalog,
            project.path,
            actor="robin",
            occurred_at="2026-09-18T15:00:00Z",
            rule_id="binding_is_ambiguous_outside_programs_and_domains",
            raised="corr",
            kind=RecordEventKind.ASSIGN,
            answer="correlation",
        )
    assert '"theme_kind"' in event.payload
    answers = answers_in_force(project.path)
    assert [(item.term, item.theme_kind, item.defined) for item in answers] == [
        ("correlation", "statistic", False)
    ]


def test_a_later_answer_wins_and_the_earlier_one_stays_readable(project: Project) -> None:
    _answer(project)
    _answer(project, answer="technical_replicate", occurred_at="2026-09-18T16:00:00Z")
    answers = answers_in_force(project.path)
    assert [item.term for item in answers] == ["technical_replicate"]
    with open_catalog(project.path) as catalog:
        assert catalog.counts()["record_events"] == 2


def test_an_answer_carries_the_scope_the_person_chose() -> None:
    """A person deciding for one folder decided for one folder, and the sense says so."""
    folder = Scope(ScopeKind.FOLDER, "first")
    section = section_from_answers(
        (
            QuestionAnswer(LABEL_RULE, UNPLACED, "biological_replicate", "replicate", folder, True),
            QuestionAnswer(LABEL_RULE, UNPLACED, "technical_replicate", "replicate"),
        )
    )
    assert section is not None
    assert [sense.scope for sense in section.senses] == [folder, PROJECT_SCOPE]
    assert all(sense.origin is DictionaryOrigin.USER for sense in section.senses)


def test_a_project_whose_record_holds_no_answer_gets_exactly_what_ships(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[tmp_path / "pool"])
    (tmp_path / "pool").mkdir()
    assert answered_sections(project.path) == ()
    assert (
        DictionaryStore.for_project(project.path).fingerprint()
        == DictionaryStore.builtin().fingerprint()
    )


# ---------------------------------------------------------------------------
# A decision whose basis has gone.
# ---------------------------------------------------------------------------


def test_an_answer_is_orphaned_when_its_rule_leaves_the_rulebook(project: Project) -> None:
    """Reported and still honoured: a person's word does not stop being true."""
    _answer(project)
    assert orphaned_answers(project.path, in_force={LABEL_RULE}) == ()

    orphans = orphaned_answers(project.path, in_force={"some_other_rule"})
    assert [item.missing_rule for item in orphans] == [LABEL_RULE]
    assert orphans[0].event.kind is RecordEventKind.DEFINE

    # Still honoured: the sense the person stated is still in the Dictionary.
    store = DictionaryStore.for_project(project.path)
    assert store.look_up(UNPLACED, scope=PROJECT_SCOPE) is not None


def test_a_rule_that_still_runs_and_no_longer_asks_is_not_an_orphan(project: Project) -> None:
    """A term the Dictionary now places is a term nothing asks about: the answer working."""
    _answer(project)
    run_census(project, run_id=_run(3), started_at="t")
    run_inference(project, run_id=_run(4), started_at="t")
    with open_catalog(project.path) as catalog:
        assert (
            catalog.query(
                "SELECT count(*) FROM findings WHERE rule_id = ?", [LABEL_RULE]
            ).fetchone()[0]
            == 0
        )
    assert orphaned_answers(project.path, in_force={LABEL_RULE}) == ()


# ---------------------------------------------------------------------------
# A collision between what shipped and what a pack adds, settled by a person.
# ---------------------------------------------------------------------------


def _shipped_and_packed() -> DictionaryStore:
    """A store where the core and a pack claim one spelling, which is the whole of the ms case."""
    from ingence.dictionary.store import Section, Sense

    store = DictionaryStore()
    for name, origin, term, kind in (
        ("units", DictionaryOrigin.BUILTIN, "millisecond", "unit"),
        ("spectrometry", DictionaryOrigin.PACK, "mass_spectrometry", "technique"),
    ):
        store.add(
            Section(
                name=name,
                summary="",
                origin=origin,
                senses=(
                    Sense(
                        term=term,
                        canonical=term,
                        theme_kind=kind,
                        section=name,
                        surfaces=("ms",),
                        origin=origin,
                    ),
                ),
            )
        )
    return store


def test_a_core_to_pack_tie_is_settled_by_an_answer_and_only_where_it_was_given() -> None:
    """A person's answer settles a tie between the core and a pack, in the scope it was given.

    Dropping ``PACK`` to the core's precedence leaves a tie, and the engine cannot say which sense
    applies until somebody answers. A ``USER`` sense at 40 carrying the scope the person chose
    answers it. ``section_from_answers`` already builds that sense out of the Record. "In this
    folder ``ms`` means mass spectrometry, and everywhere else it means milliseconds" is one
    such answer.
    """
    store = _shipped_and_packed()
    everywhere = store.look_up("ms")
    assert everywhere is not None and everywhere.ambiguous

    inside = Scope(ScopeKind.FOLDER, "spectra")
    section = section_from_answers(
        (QuestionAnswer("binding_is_ambiguous", "ms", "mass_spectrometry", "technique", inside),)
    )
    assert section is not None
    store.add(section)

    settled = store.look_up("ms", scope=inside)
    assert settled is not None
    assert settled.sense.term == "mass_spectrometry"
    assert not settled.ambiguous
    assert {sense.term for sense in settled.shadowed} == {"millisecond", "mass_spectrometry"}

    # And nowhere else. A person deciding for one folder decided for one folder.
    elsewhere = store.look_up("ms", scope=Scope(ScopeKind.FOLDER, "kinetics"))
    assert elsewhere is not None and elsewhere.ambiguous


def test_an_answer_at_the_project_scope_settles_the_tie_everywhere() -> None:
    """The other thing a person may mean, and the same machinery says it."""
    store = _shipped_and_packed()
    section = section_from_answers(
        (QuestionAnswer("binding_is_ambiguous", "ms", "millisecond", "unit"),)
    )
    assert section is not None
    store.add(section)
    settled = store.look_up("ms", scope=Scope(ScopeKind.FOLDER, "anywhere"))
    assert settled is not None
    assert (settled.sense.term, settled.sense.origin) == ("millisecond", DictionaryOrigin.USER)
    assert not settled.ambiguous


# ---------------------------------------------------------------------------
# What a person taught travels.
# ---------------------------------------------------------------------------


def test_the_answers_in_force_export_as_a_section_that_loads_as_a_pack(
    project: Project, tmp_path: Path
) -> None:
    """A definition exports with who gave it, and the file loads through the section loader."""
    from ingence.dictionary.answers import pack_section_from_answers
    from ingence.dictionary.store import load_sections

    event = _answer(project)
    exported = pack_section_from_answers(project.path, section="taught")
    assert exported.exported == 1
    assert not exported.left_out
    assert event.event_id in exported.text
    assert "decides nothing" in exported.text

    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "taught.yaml").write_text(exported.text, encoding="utf-8")
    (section,) = load_sections(packs, origin=DictionaryOrigin.PACK)
    (sense,) = section.senses
    assert (sense.term, sense.theme_kind, sense.surfaces) == (
        "biological_replicate",
        "replicate",
        (UNPLACED,),
    )
    assert sense.origin is DictionaryOrigin.PACK
    (given,) = sense.properties["answers"]
    assert (given["answered"], given["actor"], given["raised_by"]) == (
        "define",
        "robin",
        LABEL_RULE,
    )

    assert pack_section_from_answers(project.path, section="taught").text == exported.text


def test_an_answer_scoped_to_one_folder_is_counted_and_left_out(project: Project) -> None:
    """A folder scope names this project's folders, and a pack has none."""
    from ingence.dictionary.answers import pack_section_from_answers

    folder = Scope(ScopeKind.FOLDER, "first")
    _answer(project, scope=folder)
    exported = pack_section_from_answers(project.path, section="taught")
    assert exported.exported == 0
    assert exported.left_out == {"scoped to one folder": 1}
