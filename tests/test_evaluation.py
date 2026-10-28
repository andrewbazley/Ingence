"""The evaluation's public half: a generated pool, its answer key, and the scorer.

These tests hold the generator's promises and the scorer's arithmetic, and one run of the
engine over the smallest pool whose figures are pinned as a tripwire. The pinned figures are what
the engine does today, never what it should do: a change in any of them fails with the figures
printed, and a person decides whether the change is the one they meant (a corpus is a tripwire,
not a target). A rule is never tuned until the figures look right.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pytest

from ingence.core.schema import Entry, EntryType
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import read_name
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.evaluation.generate import STEMS, generate, invented_stems, stems_needed
from ingence.evaluation.key import FAMILIES, AnswerKey, PairFamily, ValueFamily, load_key
from ingence.evaluation.score import (
    CONCLUDED,
    CONFLICTING,
    REJECTED,
    Reading,
    RunReadings,
    evaluate,
    pinned_figures,
    score_key,
    score_pairs,
    score_project,
    score_values,
    summary,
)
from ingence.scan.census import compound_extension

if TYPE_CHECKING:
    from ingence.evaluation.key import PairFamily

STORE = DictionaryStore.builtin()
NORMALISER = Normaliser(STORE)


def _reading(name: str):
    pure = PurePosixPath(name)
    entry = Entry(
        entry_id=f"entry_{name}",
        root_id="root_test",
        relative_path=name,
        name=name,
        entry_type=EntryType.FILE,
        extension=pure.suffix.casefold().removeprefix("."),
        compound_extension=compound_extension(name).removeprefix("."),
        first_seen_run="run_test",
        last_seen_run="run_test",
    )
    return read_name(entry, run_id="run_test", normaliser=NORMALISER, store=STORE)


def _contents(root: Path) -> list[tuple[str, bytes]]:
    found: list[tuple[str, bytes]] = []
    for folder, _, names in os.walk(root):
        for name in names:
            path = Path(folder, name)
            found.append((path.relative_to(root).as_posix(), path.read_bytes()))
    return sorted(found)


@pytest.mark.parametrize("stem", STEMS)
def test_an_invented_stem_binds_to_nothing_in_the_shipped_dictionary(stem: str) -> None:
    reading = _reading(stem)

    assert [component.component_type for component in reading.components] == ["word"]
    assert [binding.resolution_status for binding in reading.bindings] == [
        ResolutionStatus.EXAMINED_NO_EVIDENCE
    ]


def test_the_stems_made_beyond_the_list_bind_to_nothing_either() -> None:
    """A pool of size eight takes 208 stems: the listed ones and those made from syllables.

    Every stem the largest size measured takes is held, and a margin beyond it."""
    assert stems_needed(8) == 208
    made = invented_stems(stems_needed(16))[len(STEMS) :]

    assert len(set(made)) == len(made)
    for stem in made:
        reading = _reading(stem)
        assert [component.component_type for component in reading.components] == ["word"], stem
        assert [binding.resolution_status for binding in reading.bindings] == [
            ResolutionStatus.EXAMINED_NO_EVIDENCE
        ], stem


def test_one_seed_and_size_write_one_pool_byte_for_byte(tmp_path: Path) -> None:
    first = generate(tmp_path / "first", seed=3, size=2)
    second = generate(tmp_path / "second", seed=3, size=2)

    assert _contents(first.pool) == _contents(second.pool)
    assert first.key_path.read_bytes() == second.key_path.read_bytes()


def test_another_seed_writes_another_pool(tmp_path: Path) -> None:
    first = generate(tmp_path / "first", seed=3, size=1)
    other = generate(tmp_path / "other", seed=4, size=1)

    assert _contents(first.pool) != _contents(other.pool)


def test_the_key_is_written_beside_the_pool_and_never_inside_it(tmp_path: Path) -> None:
    written = generate(tmp_path / "evaluation", seed=0, size=1)

    assert written.key_path.parent == written.pool.parent
    assert not any(path.suffix == ".json" for path in written.pool.rglob("*"))
    assert load_key(written.key_path) == written.key


def test_the_key_is_written_byte_for_byte_whatever_the_platform_s_line_ending(
    tmp_path: Path,
) -> None:
    written = generate(tmp_path / "evaluation", seed=0, size=1)

    assert written.key_path.read_bytes() == written.key.dumps().encode("utf-8")
    assert b"\r\n" not in written.key_path.read_bytes()


def test_the_pool_is_never_written_over_anything(tmp_path: Path) -> None:
    (tmp_path / "evaluation" / "pool").mkdir(parents=True)
    (tmp_path / "evaluation" / "pool" / "kept.txt").write_text("kept")

    with pytest.raises(FileExistsError):
        generate(tmp_path / "evaluation", seed=0, size=1)

    assert [path.name for path in (tmp_path / "evaluation" / "pool").iterdir()] == ["kept.txt"]


def test_every_path_the_key_names_is_in_the_pool(tmp_path: Path) -> None:
    written = generate(tmp_path / "evaluation", seed=1, size=2)
    present = {path.relative_to(written.pool).as_posix() for path in written.pool.rglob("*")}

    for path in written.key.named_paths():
        assert path in present, path


def test_the_key_answers_every_family_and_marks_some_pair_undecidable(tmp_path: Path) -> None:
    key = generate(tmp_path / "evaluation", seed=0, size=1).key

    # The generated pool's key answers every family but the two that the sample collection's key
    # adds (workflow, repeats), which this generator does not lay out.
    assert set(key.families) <= set(FAMILIES)
    for name in key.families:
        assert key.families[name].expected_count() > 0, name
    undecidable = sum(len(family.undecidable) for family in key.pair_families())
    assert undecidable > 0


def test_every_file_holds_bytes_of_its_own_unless_the_key_calls_it_a_copy(tmp_path: Path) -> None:
    """Two files alike by accident would be copies the key never named."""
    written = generate(tmp_path / "evaluation", seed=2, size=2)
    by_bytes: dict[bytes, set[str]] = {}
    for path, body in _contents(written.pool):
        if body:
            by_bytes.setdefault(body, set()).add(path)
    copies: PairFamily = written.key.families["copy"]  # type: ignore[assignment]
    named = {frozenset(pair) for pair in copies.expected}
    for paths in by_bytes.values():
        for first in paths:
            for second in paths:
                if first < second:
                    assert frozenset((first, second)) in named, (first, second)


def test_the_key_reads_back_from_its_own_json(tmp_path: Path) -> None:
    key = generate(tmp_path / "evaluation", seed=5, size=1).key

    assert AnswerKey.from_json(json.loads(json.dumps(key.to_json()))) == key


# ---------------------------------------------------------------------------
# The scorer's arithmetic, on readings written by hand.
# ---------------------------------------------------------------------------


def test_a_value_family_counts_right_wrong_and_forced_apart() -> None:
    family = ValueFamily(expected={"a": "raw", "b": "analysis", "c": None}, undecidable=("d",))
    readings = {
        "a": Reading("raw", CONCLUDED, "defeasible"),
        "b": Reading("raw", CONCLUDED, "defeasible"),
        "c": Reading("processed", CONCLUDED, "defeasible"),
        "d": Reading("raw", CONCLUDED, "defeasible"),
        "e": Reading("raw", CONCLUDED, "defeasible"),
    }

    figures = score_values(family, readings)

    assert figures["correct"] == 1
    assert figures["wrong_value"] == 1
    assert figures["wrong_where_none"] == 1
    assert figures["undecidable_decided"] == 1
    assert figures["outside"] == 1
    assert figures["precision"]["successes"] == 1
    assert figures["precision"]["trials"] == 3
    assert figures["recall"]["trials"] == 2
    assert figures["by_tier"] == {"defeasible": {"correct": 1, "wrong": 2}}


def test_no_claim_and_conflicting_are_not_errors_and_not_one_another() -> None:
    family = ValueFamily(expected={"a": "raw", "b": "raw", "c": "raw"})
    readings = {"a": Reading("raw", CONFLICTING, "defeasible")}

    figures = score_values(family, readings)

    assert figures["conflicting"] == 1
    assert figures["no_claim"] == 2
    assert figures["wrong_value"] == figures["wrong_where_none"] == 0
    assert figures["precision"] == {"successes": 0, "trials": 0, "low": None, "high": None}
    assert figures["coverage"]["successes"] == 0


def test_a_reading_that_contradicts_the_key_s_value_is_an_error_and_a_right_no_is_not() -> None:
    """The engine saying a folder is acquisitions where the key says experiment is a wrong answer,
    in precision's denominator. Saying there is none where the key says none stays right and
    outside precision."""
    family = ValueFamily(expected={"a": "experiment", "b": None})
    readings = {"a": Reading(None, CONCLUDED, "defeasible"), "b": Reading(None, CONCLUDED, "x")}

    figures = score_values(family, readings)

    assert figures["none_where_value"] == 1
    assert figures["precision"] == {"successes": 0, "trials": 1, "low": 0.0, "high": 0.7935}
    assert figures["coverage"]["successes"] == 1
    assert figures["by_tier"] == {"defeasible": {"correct": 0, "wrong": 1}}


def test_conflicting_on_an_undecidable_path_is_counted_apart_from_silence() -> None:
    family = ValueFamily(expected={}, undecidable=("a", "b", "c"))
    readings = {"a": Reading("x", CONCLUDED, "t"), "b": Reading(None, CONFLICTING, "t")}

    figures = score_values(family, readings)

    assert figures["undecidable_decided"] == 1
    assert figures["undecidable_conflicting"] == 1
    assert figures["undecidable_no_claim"] == 1


def test_a_pair_family_counts_undecidable_pairs_apart_from_wrong_ones() -> None:
    family = PairFamily(
        directed=False, expected=(("a", "b"), ("b", "c")), undecidable=(("a", "c"),)
    )
    found = {("b", "a"): "circumstantial", ("a", "c"): "circumstantial", ("c", "d"): "supported"}

    figures = score_pairs(family, found)

    assert figures["correct"] == 1
    assert figures["wrong"] == 1
    assert figures["missed"] == 1
    assert figures["undecidable_decided"] == 1
    assert figures["undecidable_no_claim"] == 0
    assert figures["by_tier"] == {
        "circumstantial": {"correct": 1, "wrong": 0},
        "supported": {"correct": 0, "wrong": 1},
    }


def test_a_directed_pair_read_backwards_is_wrong() -> None:
    family = PairFamily(directed=True, expected=(("a", "b"),))

    figures = score_pairs(family, {("b", "a"): "circumstantial"})

    assert (figures["correct"], figures["wrong"], figures["missed"]) == (0, 1, 1)


def test_a_partial_family_scores_a_pair_only_where_it_names_both_paths() -> None:
    """A person's key never makes a wrong of a file it did not look at."""
    family = PairFamily(
        directed=False, expected=(("a", "b"),), complete=False, scope=("a", "b", "c")
    )

    figures = score_pairs(
        family, {("a", "b"): "x", ("a", "c"): "x", ("a", "z"): "x", ("y", "z"): "x"}
    )

    assert (figures["correct"], figures["wrong"], figures["outside"]) == (1, 1, 2)


def test_a_pair_touching_an_ignored_path_is_not_scored() -> None:
    family = PairFamily(directed=False, expected=(("a", "b"),), ignored=("c",))

    figures = score_pairs(family, {("a", "b"): "x", ("a", "c"): "x"})

    assert (figures["correct"], figures["wrong"], figures["ignored"]) == (1, 0, 1)


def test_a_pair_the_engine_blocked_is_its_own_no_and_never_a_conclusion() -> None:
    """A blocked candidate is written as rejected, its conflict recorded."""
    family = PairFamily(directed=True, expected=(("a", "b"),))

    figures = score_pairs(family, {("a", "b"): "blocked"})

    assert figures["correct"] == 0
    assert figures["concluded"] == 0
    assert figures["rejected_where_pair"] == 1
    assert figures["missed"] == 1
    assert figures["precision"]["trials"] == 1  # a "no" the key contradicts is an error


def test_a_pair_the_engine_blocked_where_the_key_has_none_is_right() -> None:
    family = PairFamily(directed=True, expected=(("a", "b"),), undecidable=(("a", "d"),))

    figures = score_pairs(
        family, {("a", "c"): "blocked", ("b", "c"): "set_aside", ("a", "d"): "blocked"}
    )

    assert (figures["wrong"], figures["rejected_right"]) == (0, 2)
    assert figures["undecidable_rejected"] == 1
    assert figures["undecidable_decided"] == 0
    assert figures["precision"]["trials"] == 0


def test_a_conflicting_pair_is_counted_apart_from_right_wrong_and_no_claim() -> None:
    family = PairFamily(directed=False, expected=(("a", "b"),), undecidable=(("c", "d"),))

    figures = score_pairs(family, {("a", "b"): "conflicting", ("c", "d"): "conflicting"})

    assert (figures["correct"], figures["wrong"], figures["conflicting"]) == (0, 0, 1)
    assert figures["missed"] == 1
    assert figures["undecidable_conflicting"] == 1
    assert figures["undecidable_no_claim"] == 0


def test_a_finding_a_fired_defeater_set_aside_is_read_as_the_engine_s_no() -> None:
    from ingence.evaluation.score import set_aside

    fired = '[{"fired": true, "rule_id": "installed_software_in_folder"}]'
    assert set_aside(fired) is True
    assert set_aside('[{"fired": false, "rule_id": "installed_software_in_folder"}]') is False
    assert set_aside("[]") is False
    assert set_aside(None) is False


def test_a_class_member_whose_claim_conflicts_makes_its_pairs_conflicting() -> None:
    from ingence.evaluation.score import pairs_of

    found = pairs_of({"kelt": ["a", "b", "c"]}, "class", conflicting={"c"})

    assert found == {("a", "b"): "class", ("a", "c"): "conflicting", ("b", "c"): "conflicting"}


# ---------------------------------------------------------------------------
# The tripwire: the smallest pool through the engine, its figures pinned.
# ---------------------------------------------------------------------------

PINNED = Path(__file__).parent / "fixtures" / "evaluation_pinned.json"


def _differences(pinned: object, now: object, where: str = "") -> list[str]:
    """Every place the figures differ, as ``where: pinned -> now``."""
    if isinstance(pinned, dict) and isinstance(now, dict):
        found: list[str] = []
        for name in sorted(set(pinned) | set(now)):
            found.extend(
                _differences(pinned.get(name), now.get(name), f"{where}/{name}" if where else name)
            )
        return found
    return [] if pinned == now else [f"{where}: {pinned!r} -> {now!r}"]


@pytest.fixture(scope="module")
def smallest_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, Path, AnswerKey]:
    """The smallest pool through the engine once: the report, the folder it ran in, the key."""
    base = tmp_path_factory.mktemp("evaluation")
    written = generate(base / "evaluation", seed=0, size=1)
    # One column: the shipped rules. The pinned figures are the ones they gave as a project
    # revision.
    report = evaluate(written.key, written.pool, base / "work", {"shipped": ()})
    return report, base, written.key


@pytest.fixture(scope="module")
def smallest_report(smallest_run: tuple[dict, Path, AnswerKey]) -> dict:
    return smallest_run[0]


def test_the_smallest_pool_s_figures_are_the_ones_pinned(smallest_report: dict) -> None:
    """A tripwire, never a target: a figure that moved is for a person to read and decide.

    If the change is the one meant, re-pin with ``python tools/evaluate.py --write-pinned``.
    Never change a rule, the generator or the key to bring a figure back.
    """
    now = json.loads(json.dumps(pinned_figures(smallest_report), sort_keys=True))
    pinned = json.loads(PINNED.read_text(encoding="utf-8"))

    moved = _differences(pinned, now)
    assert not moved, (
        "The evaluation's figures moved. What moved, pinned -> now:\n  "
        + "\n  ".join(moved)
        + "\n\nThe figures now:\n"
        + summary(smallest_report)
    )


def test_the_report_states_its_method_and_counts_no_single_score(smallest_report: dict) -> None:
    method = smallest_report["method"]

    assert method["engine"]["schema"]
    assert method["engine"]["tokeniser"]
    assert "not_measured" in method
    assert set(smallest_report["columns"]) == {"shipped"}
    for body in smallest_report["columns"].values():
        assert set(body["families"]) <= set(FAMILIES)
        assert "score" not in body
        for scored in body["families"].values():
            for row in scored["rules"].values():
                for name in ("precision", "recall"):
                    assert set(row[name]) == {"successes", "trials", "low", "high"}


def test_the_smallest_pool_stays_a_suite_sized_run(
    smallest_run: tuple[dict, Path, AnswerKey],
) -> None:
    """About 15 s on the development laptop with files read in-process. The bound sits under the
    suite's 180 s hang ceiling, so it can fail before the process is killed, and is generous so a
    slow runner does not; the engine's own settings say the files were read in-process."""
    from ingence.core.project import Project

    report, base, _ = smallest_run

    assert report["seconds"] < 120
    for column in ("shipped",):
        settings = Project.open(base / "work" / f"column-{column}").settings
        assert settings.extraction.use_subprocess_isolation is False


def test_a_project_already_run_scores_as_the_run_that_made_it(
    smallest_run: tuple[dict, Path, AnswerKey],
) -> None:
    """The private half: the runner's own project, scored after the fact, runs nothing."""
    report, base, _ = smallest_run

    again = score_project(base / "evaluation" / "answer_key.json", base / "work" / "column-shipped")

    (body,) = again["columns"].values()
    assert body["families"] == report["columns"]["shipped"]["families"]
    assert body["questions"] == report["columns"]["shipped"]["questions"]
    assert body["run"]


def test_a_key_written_for_one_folder_deep_in_a_root_reads_its_paths_from_there(
    smallest_run: tuple[dict, Path, AnswerKey], tmp_path: Path
) -> None:
    _, base, key = smallest_run
    experiments = key.families["experiment_folder"]
    assert isinstance(experiments, ValueFamily)
    (dated,) = [path for path, value in experiments.expected.items() if value and "/" in path]
    year, folder = dated.split("/")
    partial = AnswerKey(
        families={
            "experiment_folder": ValueFamily(expected={folder: "experiment"}, complete=False)
        },
        describes={"written_by": "a test"},
    )

    key_path = tmp_path / "partial_key.json"
    key_path.write_bytes(partial.dumps().encode("utf-8"))

    scored = score_project(key_path, base / "work" / "column-shipped", within=year)

    (body,) = scored["columns"].values()
    rows = body["families"]["experiment_folder"]["rules"]
    assert rows["experiment_folder_from_contents"]["correct"] == 1
    assert rows["experiment_folder_from_contents"]["outside"] == 0


def test_the_module_command_writes_a_pool_and_scores_a_project(
    tmp_path: Path, smallest_run: tuple[dict, Path, AnswerKey]
) -> None:
    from ingence.evaluation.__main__ import main

    _, base, _ = smallest_run
    assert main(["generate", str(tmp_path / "made"), "--seed", "2", "--size", "1"]) == 0
    assert (tmp_path / "made" / "answer_key.json").is_file()

    assert (
        main(
            [
                "project",
                "--key",
                str(base / "evaluation" / "answer_key.json"),
                "--project",
                str(base / "work" / "column-shipped"),
                "--out",
                str(tmp_path / "scored"),
            ]
        )
        == 0
    )
    assert json.loads((tmp_path / "scored" / "report.json").read_text())["columns"]
    assert "questions for a person" in (tmp_path / "scored" / "summary.txt").read_text()


def test_the_report_carries_the_engine_s_own_account_of_what_it_left_undecided(
    smallest_report: dict,
) -> None:
    """Why a family is empty, in the engine's words: no evidence, starved, unexamined, conflicting,
    and for a proposing rule how many pairs it drew and how many it proposed."""
    column = smallest_report["columns"]["shipped"]

    stage = column["engine_tallies"]["pipeline_stage_from_name"]
    assert set(stage) == {
        "evaluated", "concluded", "no_evidence", "starved", "unexamined", "conflicting"
    }  # fmt: skip
    assert stage["evaluated"] >= stage["concluded"] > 0
    drawn = column["proposals"]["derivation_within_name_identity"]
    # Derivations are drawn within the identity compared across an experiment.
    assert drawn["within"] == "same_name_identity_within_experiment"
    assert drawn["pairs"] > 0
    assert set(drawn) >= {"pairs", "supported", "circumstantial", "blocked", "blocked_by"}


def test_a_readme_naming_a_moved_folder_s_files_is_a_reference_the_files_cannot_settle(
    tmp_path: Path,
) -> None:
    """A subtree moved away from where a readme says it is: the same names stand elsewhere, and
    whether they are the same files is not in the files, so the right output is abstention."""
    written = generate(tmp_path / "evaluation", seed=0, size=1)
    references = written.key.families["reference"]
    assert isinstance(references, PairFamily)

    moved = [pair for pair in references.undecidable if " archive/" in pair[1]]
    assert len(moved) == 2
    for readme, target in moved:
        assert readme.endswith("README.txt")
        assert (written.pool / target).is_file()
        mentioned = (written.pool / readme).read_text()
        assert target.split(" archive/", 1)[1] in mentioned
        assert not (written.pool / readme).parent.joinpath(target.split(" archive/", 1)[1]).exists()


def test_the_group_row_says_how_many_of_the_key_s_named_groups_the_engine_proposed_whole(
    smallest_report: dict,
) -> None:
    """Pairs say how much of each group was found; a named group matched says it was found whole."""
    row = smallest_report["columns"]["shipped"]["families"]["group"]["rules"][
        "experiments_may_belong_together"
    ]

    assert row["named_groups"] == 3  # replicate days, the lattice's core, and its sub-group
    assert 0 <= row["named_groups_matched"] <= row["named_groups"]


def test_every_rule_s_pairs_read_together_keep_a_shared_no_and_conflict_where_rules_differ() -> (
    None
):
    from ingence.evaluation.score import together_pairs

    merged = together_pairs(
        [
            {("a", "b"): "blocked", ("a", "c"): "circumstantial", ("a", "d"): "class"},
            {("b", "a"): "set_aside", ("a", "c"): "blocked", ("a", "d"): "class"},
        ],
        directed=False,
    )

    assert merged[("a", "b")] in REJECTED
    assert merged[("a", "c")] == CONFLICTING
    assert merged[("a", "d")] == "class"


def _two_root_project(base: Path) -> Path:
    """Two roots, each with one note at the same relative folder: a census, nothing else."""
    from ingence.core.ids import run_id
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    first = base / "kelt"
    second = base / "vorn"
    (first / "notes").mkdir(parents=True)
    (second / "notes").mkdir(parents=True)
    (first / "notes" / "2021-03-04_zib.txt").write_text("zib one\n")
    (second / "notes" / "2022-05-06_zib.txt").write_text("zib two\n")
    project = Project.create(
        base / "project", created_at="t", roots=[first, second], labels=["kelt", "vorn"]
    )
    census = run_id(started_at="2026-01-01T00:00:01Z", nonce="two roots")
    run_census(project, run_id=census, started_at="t")
    return project.path


def _date_key(base: Path) -> Path:
    key = AnswerKey(
        families={
            "name_date": ValueFamily(
                expected={"notes/2021-03-04_zib.txt": "2021-03-04"}, complete=False
            )
        },
        describes={"written_by": "a test"},
    )
    path = base / "key.json"
    path.write_bytes(key.dumps().encode("utf-8"))
    return path


def test_a_key_saved_inside_one_of_the_project_s_roots_is_refused_in_words(tmp_path: Path) -> None:
    """The key is what a person knows and is never part of what the engine observes."""
    from ingence.evaluation.score import KeyPlacementError

    project = _two_root_project(tmp_path)
    inside = tmp_path / "vorn" / "notes" / "key.json"
    inside.write_bytes(_date_key(tmp_path).read_bytes())

    with pytest.raises(KeyPlacementError, match="inside the root"):
        score_project(inside, project, root="kelt")


def test_the_module_command_refuses_a_key_inside_a_root_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ingence.evaluation.__main__ import main

    project = _two_root_project(tmp_path)
    inside = tmp_path / "kelt" / "key.json"
    inside.write_bytes(_date_key(tmp_path).read_bytes())

    arguments = ["project", "--key", str(inside), "--project", str(project), "--root", "kelt"]
    code = main([*arguments, "--out", str(tmp_path / "out")])

    assert code == 2
    assert "inside the root" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_the_module_command_refuses_a_report_folder_inside_a_root_or_the_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Source data is read-only: the report folder is judged as every other file the engine writes
    on request is (``core/destinations.py``). Refused inside a root or inside the project, apart
    from its exports folder; a folder elsewhere is the person's own."""
    from ingence.evaluation.__main__ import main

    project = _two_root_project(tmp_path)
    key = _date_key(tmp_path)
    arguments = ["project", "--key", str(key), "--project", str(project), "--root", "kelt"]
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    for refused in (
        tmp_path / "kelt" / "report",  # inside a root
        tmp_path / "vorn" / "notes" / "report",  # inside the other root
        project / "report",  # inside the project, outside its exports
        project / "runs",
    ):
        assert main([*arguments, "--out", str(refused)]) == 2, refused
        said = " ".join(capsys.readouterr().err.split())
        assert "refused" in said
        assert "never writes into the folders it reads" in said or "keeps for itself" in said
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before

    assert main([*arguments, "--out", str(tmp_path / "scored")]) == 0
    assert (tmp_path / "scored" / "report.json").is_file()
    assert main([*arguments, "--out", str(project / "projections" / "exports" / "scored")]) == 0
    assert (project / "projections" / "exports" / "scored" / "summary.txt").is_file()


def test_the_module_command_does_not_write_through_a_second_name_of_an_indexed_file(
    tmp_path: Path,
) -> None:
    """A report file that is a hard link to an indexed file is replaced, not written through."""
    import os

    from ingence.evaluation.__main__ import main

    project = _two_root_project(tmp_path)
    key = _date_key(tmp_path)
    indexed = next(path for path in sorted((tmp_path / "kelt").rglob("*")) if path.is_file())
    kept = indexed.read_bytes()
    out = tmp_path / "scored"
    out.mkdir()
    try:
        os.link(indexed, out / "report.json")
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a hard link")
    arguments = ["project", "--key", str(key), "--project", str(project), "--root", "kelt"]
    assert main([*arguments, "--out", str(out)]) == 0
    assert indexed.read_bytes() == kept
    assert json.loads((out / "report.json").read_text())["columns"]


def test_the_module_command_does_not_replace_a_report_file_that_is_not_writable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import os

    from ingence.evaluation.__main__ import main

    project = _two_root_project(tmp_path)
    key = _date_key(tmp_path)
    out = tmp_path / "scored"
    out.mkdir()
    (out / "report.json").write_text("theirs\n")
    (out / "report.json").chmod(0o444)
    if os.access(out / "report.json", os.W_OK):
        pytest.skip("this account can write a read-only file (the super-user can)")
    arguments = ["project", "--key", str(key), "--project", str(project), "--root", "kelt"]
    assert main([*arguments, "--out", str(out)]) == 1
    assert f"could not write {out / 'report.json'} (read-only)" in capsys.readouterr().err
    assert (out / "report.json").read_text() == "theirs\n"


def test_a_project_with_two_roots_asks_which_one_the_key_describes(tmp_path: Path) -> None:
    """Two roots can each hold one relative path, so a key names its root, or is refused."""
    from ingence.evaluation.score import KeyPlacementError

    project = _two_root_project(tmp_path)
    key = _date_key(tmp_path)

    with pytest.raises(KeyPlacementError, match="--root"):
        score_project(key, project)

    kelt = score_project(key, project, root="kelt")["columns"]["project"]
    vorn = score_project(key, project, root="vorn")["columns"]["project"]
    assert kelt["root"] == "kelt"
    read = kelt["families"]["name_date"]["rules"]["tokeniser"]
    assert (read["correct"], read["outside"]) == (1, 0)
    other = vorn["families"]["name_date"]["rules"]["tokeniser"]
    assert (other["correct"], other["no_claim"], other["outside"]) == (0, 1, 1)


# ---------------------------------------------------------------------------
# The group lattice's questions, read from findings built by hand.
# ---------------------------------------------------------------------------

_LATTICE_RULE = "group_resolution_deviation"
_CLUSTERING_RULE = "experiments_may_belong_together"


def test_a_lattice_question_names_the_whole_set_it_asks_about() -> None:
    from ingence.evaluation.score import lattice_terms

    assert lattice_terms("a group: kelt vorn") == ("kelt", "vorn")
    assert lattice_terms("a group: kelt vorn + zib") == ("kelt", "vorn", "zib")
    assert lattice_terms("zib + kelt vorn") is None  # a word added to an approved core
    assert lattice_terms("morv [2018 kelt vorn morv and 1 more]") is None


def test_the_lattice_s_questions_are_groups_of_every_experiment_holding_their_terms() -> None:
    """Each set asked is a proposed group: every experiment whose terms contain the set."""
    from ingence.evaluation.score import group_readings

    paths = {"a": "2021 kelt vorn", "b": "2022 kelt vorn zib", "c": "2023 kelt vorn zib"}
    paths |= {"d": "2024 brisa"}
    rows = [
        ("folder:a", _LATTICE_RULE, ["a group: kelt vorn"], False),
        ("folder:b", _LATTICE_RULE, ["a group: kelt vorn", "a group: kelt vorn + zib"], False),
        ("folder:c", _LATTICE_RULE, ["a group: kelt vorn", "a group: kelt vorn + zib"], False),
        ("folder:d", _LATTICE_RULE, ["brisa + kelt vorn"], False),
    ]

    pairs, groups = group_readings(rows, paths)

    assert groups[_LATTICE_RULE] == {
        "a group: kelt vorn": ("2021 kelt vorn", "2022 kelt vorn zib", "2023 kelt vorn zib"),
        "a group: kelt vorn zib": ("2022 kelt vorn zib", "2023 kelt vorn zib"),
    }
    assert set(pairs[_LATTICE_RULE]) == {
        ("2021 kelt vorn", "2022 kelt vorn zib"),
        ("2021 kelt vorn", "2023 kelt vorn zib"),
        ("2022 kelt vorn zib", "2023 kelt vorn zib"),
    }
    assert _CLUSTERING_RULE in pairs  # read beside the lattice, empty here


def test_a_set_the_clustering_and_the_lattice_both_propose_counts_once() -> None:
    from ingence.evaluation.score import group_readings

    paths = {"a": "2021 kelt vorn", "b": "2022 kelt vorn", "c": "2023 kelt vorn zib"}
    rows = [
        ("folder:a", _CLUSTERING_RULE, ["kelt vorn [2021 kelt vorn and 1 more]"], False),
        ("folder:b", _CLUSTERING_RULE, ["kelt vorn [2021 kelt vorn and 1 more]"], False),
        ("folder:a", _LATTICE_RULE, ["a group: kelt vorn"], False),
        ("folder:b", _LATTICE_RULE, ["a group: kelt vorn"], False),
        ("folder:c", _LATTICE_RULE, ["a group: kelt vorn", "a group: kelt vorn + zib"], False),
    ]
    pairs, groups = group_readings(rows, paths)
    family = PairFamily(
        directed=False,
        expected=(("2021 kelt vorn", "2022 kelt vorn"),),
        named={"kelt vorn": ("2021 kelt vorn", "2022 kelt vorn")},
    )
    key = AnswerKey(families={"group": family}, describes={"written_by": "a test"})
    readings = RunReadings(
        values={},
        pairs={"group": pairs},
        groups={"group": groups},
        questions={},
        beside_questions={},
        outputs={},
    )

    rows_scored = score_key(key, readings)["families"]["group"]["rules"]

    assert rows_scored[_CLUSTERING_RULE]["groups_proposed"] == 1  # {a, b}
    # {a, b, c} holds kelt vorn; kelt vorn zib is held by c alone, which groups nothing.
    assert rows_scored[_LATTICE_RULE]["groups_proposed"] == 1
    every = rows_scored["every rule"]
    assert every["groups_proposed"] == 2  # {a, b} and {a, b, c}, each once
    assert every["named_groups_matched"] == 1
    assert (every["correct"], every["wrong"]) == (1, 2)


def test_one_set_both_propose_is_one_group_and_its_pairs_count_once() -> None:
    from ingence.evaluation.score import group_readings

    paths = {"a": "2021 kelt vorn", "b": "2022 kelt vorn"}
    rows = [
        ("folder:a", _CLUSTERING_RULE, ["kelt vorn [2021 kelt vorn and 1 more]"], False),
        ("folder:b", _CLUSTERING_RULE, ["kelt vorn [2021 kelt vorn and 1 more]"], False),
        ("folder:a", _LATTICE_RULE, ["a group: kelt vorn"], False),
        ("folder:b", _LATTICE_RULE, ["a group: kelt vorn"], False),
    ]
    pairs, groups = group_readings(rows, paths)
    family = PairFamily(directed=False, expected=(("2021 kelt vorn", "2022 kelt vorn"),))
    readings = RunReadings(
        values={},
        pairs={"group": pairs},
        groups={"group": groups},
        questions={},
        beside_questions={},
        outputs={},
    )
    key = AnswerKey(families={"group": family}, describes={"written_by": "a test"})

    every = score_key(key, readings)["families"]["group"]["rules"]["every rule"]

    assert every["groups_proposed"] == 1
    assert (every["correct"], every["wrong"], every["concluded"]) == (1, 0, 1)


# ---------------------------------------------------------------------------
# A column where the key's cores are approved as a person would, so the inner levels show.
# ---------------------------------------------------------------------------


def test_the_key_s_cores_are_its_most_general_named_groups() -> None:
    from ingence.evaluation.score import cores_of

    named = {
        "kelt vorn": ("a", "b", "c", "d"),
        "kelt vorn zib": ("b", "d"),
        "brisa trell": ("e", "f", "g"),
    }

    assert cores_of(named) == (("brisa", "trell"), ("kelt", "vorn"))


def test_a_column_with_the_key_s_cores_approved_writes_only_its_own_record(
    tmp_path: Path,
) -> None:
    """Off by default and never pinned: the scorer approves each core as the key in its own
    scratch project, infers again, and counts the named groups beneath the cores apart."""
    from ingence.core.schema import RecordEventKind
    from ingence.evaluation.score import APPROVED_SUFFIX, APPROVER, cores_of
    from ingence.record.log import read_events

    written = generate(tmp_path / "evaluation", seed=0, size=1)
    before = _contents(written.pool)
    group = written.key.families["group"]
    assert isinstance(group, PairFamily)
    cores = cores_of(group.named)

    report = evaluate(
        written.key,
        written.pool,
        tmp_path / "work",
        {"shipped": ()},
        approve_cores_in=("shipped",),
    )

    assert _contents(written.pool) == before
    approved = report["columns"][f"shipped{APPROVED_SUFFIX}"]
    assert approved["approved"] == [" ".join(core) for core in cores]
    events = read_events(tmp_path / "work" / f"column-shipped{APPROVED_SUFFIX}")
    assert sorted(event.target for event in events) == sorted(
        "group:" + " ".join(core) for core in cores
    )
    assert {(event.kind, event.actor) for event in events} == {(RecordEventKind.CONFIRM, APPROVER)}
    assert read_events(tmp_path / "work" / "column-shipped") == ()
    row = approved["families"]["group"]["rules"]["experiments_may_belong_together"]
    assert row["named_groups_given"] == len(cores)
    assert row["named_groups"] == len(group.named) - len(cores)
    assert "shipped" in report["columns"]  # the column without approvals, beside it
