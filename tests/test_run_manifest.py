"""A run's manifest: what changed from its parent, in plain sentences with their counts.

Per run, the sentences are computed from the difference of claims, pairs and themes between the run
and the run it was built from. They also report the decisions that took effect in the run: the
Record events after the parent's watermark and up to the run's own. Every sentence is made from
counts and a template, never by a model, and carries its number and what it is out of.

Two synthetic inferences differ by one answer ("not a program" on a folder) and one rule change (a
stage word taken out of a rule's senses). Every name is invented: ``Vorn.app``, ``kelt``, ``brisa``.
The program's layout is a packaging convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import tune
from ingence.api.routes import decide as decide_routes
from ingence.api.routes.manifest import THEME_READERS, manifest
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.log import now
from ingence.rulebook import project_rulebook
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
BEFORE = "run_20260101T000002Z_222222222222"
AFTER = "run_20260101T000003Z_333333333333"

#: The files named for the raw stage, whose stage the rule change takes away.
RAW_FILES = 3
#: The rule changed, and the stage word taken out of its senses.
STAGE_RULE = "pipeline_stage_from_name"
#: The Dashboard shows that rule by its plain words first, quoted. A manifest line names a rule by
#: its first sentence only (``_cited``).
STAGE_PLAIN = (
    "\u201cIngence places a file at the step of the work its name states, such as raw, processed "
    "or analysis\u201d"
)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pool(base: Path) -> Path:
    pool = base / "pool"
    app = pool / "tools" / "Vorn.app"
    _write(app / "Vorn-launcher", "launcher")
    for number in range(4):
        _write(app / "jars" / f"vorn-library-{number}.jar")
    for number in range(3):
        _write(app / "plugins" / f"brisa_plugin_{number}.jar")
    for number in range(1, RAW_FILES + 1):
        _write(pool / "data" / f"kelt_raw_{number}.csv", "a,b\n1,2\n")
    _write(pool / "data" / "kelt_summary.csv", "a,b\n1,2\n")
    return pool


def _program_folder(project: Project) -> str:
    with open_catalog(project.path) as catalog:
        found = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'tools/Vorn.app'"
        ).fetchone()
    assert found is not None
    return str(found[0])


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("manifest")
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=BEFORE, started_at="t")
    # One answer: the folder is not a program.
    with open_catalog(created.path) as catalog:
        decide_routes.say_folder_is_program(
            catalog,
            created.path,
            actor="a person",
            occurred_at=now(),
            entry_id=_program_folder(created),
            is_program=False,
        )
    # One rule change: the raw stage is no longer a word the stage rule reads.
    senses = next(
        rule for rule in project_rulebook(created.path).rules if rule.rule_id == STAGE_RULE
    ).parameters
    held = next(spec for spec in senses if spec.name == "senses")
    kept = [sense for sense in _values(held) if sense != "raw"]
    tune(created.path, "senses", kept, rules=(STAGE_RULE,))
    run_inference(created, run_id=AFTER, started_at="t")
    return created


def _values(spec: Any) -> list[str]:
    import json

    value = spec.default
    return list(json.loads(value) if isinstance(value, str) else value)


def _program_files(project: Project) -> int:
    with open_catalog(project.path) as catalog:
        found = catalog.query(
            "SELECT count(*) FROM entries WHERE entry_type = 'file' "
            "AND relative_path LIKE 'tools/Vorn.app/%'"
        ).fetchone()
    assert found is not None
    return int(found[0])


def test_the_manifest_names_the_answer_that_took_effect_with_its_count(project: Project) -> None:
    found = manifest(project.path, AFTER, viewer="a person")
    assert found["parent_run_id"] == BEFORE
    [decision] = found["decisions"]
    assert decision["place"] == "tools/Vorn.app"
    assert decision["answer"] == "not a program"
    assert decision["files_beneath"] == _program_files(project) == 8
    # The folder's own flag is cleared, and the seven jar files' name families and replicates,
    # in doubt while their folder was a program, are settled; the launcher's reading is unchanged.
    assert decision["itself_changed"] is True
    assert decision["files_changed"] == 7
    assert decision["said"] == (
        "Your answer 'not a program' on tools/Vorn.app took effect: the folder itself and "
        "7 of 8 files beneath it have new results now."
    )
    # No rule's identifier and no engine version mark reaches a sentence.
    import re

    text = " ".join(found["said"])
    assert not re.search(r"[a-z]+_[a-z]+", text), "a rule's identifier reached a sentence"
    assert "+p" not in text and "+d" not in text
    assert decision["said"] in found["said"]
    # Someone else reading it is told whose answer it was.
    other = manifest(project.path, AFTER, viewer="someone else")
    assert other["decisions"][0]["said"].startswith("a person's answer 'not a program'")


def test_the_manifest_names_the_rule_change_and_what_it_moved_with_exact_counts(
    project: Project,
) -> None:
    found = manifest(project.path, AFTER)
    [changed] = [rule for rule in found["rules"] if rule["rule_id"] == STAGE_RULE]
    assert changed["change"] == "parameters"
    # Revision 5 declares the stage rule at version 3 (it was 2), so the change reads 3.
    assert changed["said"] == (
        f"The rule {STAGE_PLAIN} (pipeline stage from name) changed by a person: senses: raw "
        "taken out; it is now version 3 with a person's change."
    )
    lost = [
        group
        for group in found["claims"]
        if group["rule_id"] == STAGE_RULE and group["was"] == "raw" and group["now"] is None
    ]
    assert [group["count"] for group in lost] == [RAW_FILES]
    with open_catalog(project.path, run_id=AFTER) as catalog:
        files = catalog.query("SELECT count(*) FROM entries WHERE entry_type = 'file'").fetchone()
    assert files is not None
    assert lost[0]["said"] == (
        f"{RAW_FILES} of {files[0]} files no longer read as raw, by the rule {STAGE_PLAIN} "
        "(pipeline stage from name, version 3 with a person's change)."
    )
    # The rule change moved nothing beneath the answered folder: no stage reading there moved.
    assert not [
        group
        for group in found["claims"]
        if group["rule_id"] == STAGE_RULE and group is not lost[0]
    ]


def test_a_manifest_without_themes_says_so(project: Project) -> None:
    found = manifest(project.path, AFTER, themes=None)
    assert found["themes"]["computed"] is False
    assert "themes not computed" in found["themes"]["said"][0]


def test_themes_read_through_the_registry_say_who_joined(project: Project) -> None:
    def reader(project_path: Path, run_id: str) -> dict[str, str]:
        del project_path
        if run_id == BEFORE:
            return {"exp_a": "zeph · kelt", "exp_b": "brisa", "exp_c": "brisa"}
        return {"exp_a": "zeph · kelt", "exp_b": "zeph · kelt", "exp_c": "zeph · kelt"}

    found = manifest(project.path, AFTER, themes=reader)
    assert found["themes"]["computed"] is True
    assert "2 of 3 experiments joined theme zeph · kelt." in found["themes"]["said"]


def test_the_default_theme_reader_is_reached_only_through_the_registry() -> None:
    assert "themes" in THEME_READERS.names()


def test_the_first_run_has_no_parent_and_says_so(project: Project) -> None:
    found = manifest(project.path, CENSUS)
    assert found["parent_run_id"] is None
    assert found["said"][0] == "This is the first run: there is nothing before it to compare."


def test_the_typed_route_sends_the_manifest(project: Project) -> None:
    from ingence.api import v1

    context = v1.context_for(project.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "manifest", {"run": [AFTER]})
    assert status == 200, payload
    assert payload["run_id"] == AFTER
    assert payload["decisions"][0]["answer"] == "not a program"
    assert any("no longer read as raw" in sentence for sentence in payload["said"])


# -- what took effect is what the run read ----------------------------------------------------


@pytest.fixture(scope="module")
def withdrawn(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """A note, and an answer withdrawn by going back to the first inference before the next."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event, read_events
    from ingence.record.targets import folder_target, restore_target

    base = tmp_path_factory.mktemp("withdrawn")
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=BEFORE, started_at="t")
    folder = _program_folder(created)
    append_event(
        created.path,
        kind=RecordEventKind.NOTE,
        actor="a person",
        occurred_at=now(),
        target=folder_target(folder),
        against_run_id=BEFORE,
        payload={"note": "kelt words here"},
    )
    with open_catalog(created.path) as catalog:
        decide_routes.say_folder_is_program(
            catalog,
            created.path,
            actor="a person",
            occurred_at=now(),
            entry_id=folder,
            is_program=False,
        )
    answer = [event for event in read_events(created.path) if event.kind.value == "reject"][-1]
    append_event(
        created.path,
        kind=RecordEventKind.WITHDRAW,
        actor="a person",
        occurred_at=now(),
        target=restore_target(BEFORE),
        against_run_id=BEFORE,
        payload={"withdrawn": [answer.event_id], "restored_to": BEFORE, "previewed": "digest0"},
    )
    run_inference(created, run_id=AFTER, started_at="t")
    return created


def test_a_withdrawn_answer_and_a_note_never_took_effect(withdrawn: Project) -> None:
    found = manifest(withdrawn.path, AFTER, viewer="a person")
    assert found["decisions"] == []
    assert not any("took effect" in sentence for sentence in found["said"])
    assert found["said"][0] == (
        "Nothing changed from the run before: no answer, rule, reading or pair."
    )


def test_a_withdrawal_is_said_plainly(withdrawn: Project) -> None:
    found = manifest(withdrawn.path, AFTER, viewer="a person")
    [went_back] = found["withdrawals"]
    assert went_back["said"] == "1 answer withdrawn when you went back to Scan 2."
    assert went_back["said"] in found["said"]
    text = " ".join(found["said"])
    assert "run_2026" not in text and "restore:" not in text


@pytest.fixture(scope="module")
def worded(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """A word given a meaning, and a label on one file, before the second inference."""
    base = tmp_path_factory.mktemp("worded")
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=BEFORE, started_at="t")
    with open_catalog(created.path) as catalog:
        decide_routes.define_word(
            catalog,
            created.path,
            actor="a person",
            occurred_at=now(),
            word="kelt",
            answer="kelt_way",
            theme_kind="technique",
        )
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.record.targets import label_target

    with open_catalog(created.path) as catalog:
        summary = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'data/kelt_summary.csv'"
        ).fetchone()
    assert summary is not None
    append_event(
        created.path,
        kind=RecordEventKind.ANNOTATE,
        actor="a person",
        occurred_at=now(),
        target=label_target(str(summary[0])),
        against_run_id=BEFORE,
        payload={"label": "brisa totals"},
    )
    run_census(created, run_id="run_20260101T000004Z_444444444444", started_at="t", rebind=True)
    run_inference(created, run_id="run_20260101T000005Z_555555555555", started_at="t")
    return created


def test_a_word_answer_says_the_files_whose_names_carry_it(worded: Project) -> None:
    found = manifest(worded.path, "run_20260101T000005Z_555555555555", viewer="a person")
    # The inference after the rebind is compared with the inference before, never the rebind.
    assert found["compared_run_id"] == BEFORE
    assert found["said"][0] == "Compared with Scan 2, the run before it of its own kind."
    [word] = [decision for decision in found["decisions"] if decision["target"] == "term:kelt"]
    # Four files carry kelt in their names; the rebind read each one's word anew.
    assert word["files_beneath"] == 4
    assert "files whose names carry it" in word["said"]
    assert word["said"].startswith("Your answer 'kelt way' on the word kelt took effect")


def test_a_label_on_a_file_is_traced_to_the_file(worded: Project) -> None:
    found = manifest(worded.path, "run_20260101T000005Z_555555555555", viewer="a person")
    [label] = [
        decision for decision in found["decisions"] if decision["target"].startswith("label:")
    ]
    assert label["place"] == "data/kelt_summary.csv"
    assert label["said"] == (
        "Your label 'brisa totals' on data/kelt_summary.csv took effect; the file reads as it did."
    )


def test_versions_are_said_plainly() -> None:
    from ingence.api.routes.manifest import version_said

    assert version_said("2+p1") == "version 2 with a person's change"
    assert version_said("1+d1") == "version 1"
    assert version_said("3") == "version 3"


# -- themes read through the registry, as the themes package gives them -----------------------


@pytest.fixture
def stub_themes() -> Any:
    """Register a stub reader ahead of the themes package's, and remove it after."""
    registered: list[str] = []

    def register(reader: Any) -> None:
        THEME_READERS.register("a_stub", version="1", summary="a stub for a test")(lambda: reader)
        registered.append("a_stub")

    yield register
    for name in registered:
        THEME_READERS.unregister(name)


def test_an_experiment_in_no_theme_is_never_counted_as_a_theme(
    project: Project, stub_themes: Any
) -> None:
    def reader(project_path: Path, run_id: str, settings: Any = None) -> dict[str, str | None]:
        del project_path, settings
        if run_id == BEFORE:
            return {"exp_a": "zeph · kelt", "exp_b": "brisa", "exp_c": "brisa"}
        return {"exp_a": "zeph · kelt", "exp_b": "zeph · kelt", "exp_c": None}

    stub_themes(reader)
    found = manifest(project.path, AFTER)
    said = found["themes"]["said"]
    assert found["themes"]["computed"] is True
    assert not any("None" in sentence for sentence in said)
    # Out of the experiments placed in a theme: two.
    assert "1 of 2 experiments joined theme zeph · kelt." in said
    assert "1 experiment is in no theme now (1 moved out of a theme)." in said
    assert found["themes"]["joined"] == [{"theme": "zeph · kelt", "experiments": 1, "out_of": 2}]


def test_a_theme_reader_that_fails_leaves_themes_not_computed(
    project: Project, stub_themes: Any
) -> None:
    def reader(project_path: Path, run_id: str, settings: Any = None) -> dict[str, str | None]:
        message = "the themes extra is not installed"
        raise ImportError(message)

    stub_themes(reader)
    found = manifest(project.path, AFTER)
    assert found["themes"]["computed"] is False
    assert found["themes"]["said"] == ["Themes: themes not computed."]
    assert "the themes extra is not installed" in found["themes"]["why_not"]
