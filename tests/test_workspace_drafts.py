"""Drafts: saved at once, whole or not at all, validated beside the typing, never refusing it."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.rulebook import project_rulebook
from ingence.workspace.drafts import read_draft, save_draft, uncommitted
from ingence.workspace.revisions import check_overrides, write_revision

#: Two of the shipped Rulebook's own parameters, one whole number each.
BOUNDED = "candidate_generation_is_bounded"
#: A doubt rule in force (revision 4 superseded ``authored_name_doubt``).
DOUBT = "doubt_in_a_folder_name"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return Project.create(tmp_path / "project", created_at="t").path


def test_a_draft_is_on_disk_within_a_second_and_a_new_reader_reads_it(project: Path) -> None:
    started = time.monotonic()
    save_draft(project, "settings", {"scan": {"workers": 5}})
    assert time.monotonic() - started < 1.0
    assert read_draft(project, "settings").values == {"scan": {"workers": 5}}
    assert not list((project / "workspace" / "drafts").glob("*.incoming"))


def test_a_save_adds_to_the_draft_and_a_crash_mid_save_keeps_the_old_one(
    project: Path,
) -> None:
    save_draft(project, "settings", {"scan": {"workers": 5}})
    # A save that died after writing its sibling and before the replace leaves the old draft whole.
    (project / "workspace" / "drafts" / "settings.json.incoming").write_text("{ half a fil")
    assert read_draft(project, "settings").values == {"scan": {"workers": 5}}
    save_draft(project, "settings", {"depth": "deep"})
    assert read_draft(project, "settings").values == {"scan": {"workers": 5}, "depth": "deep"}


def test_a_save_that_sends_nothing_never_empties_the_draft(project: Path) -> None:
    """A page that left Settings before its timer fired sent ``{}``. The draft of every setting
    must not be replaced by nothing."""
    save_draft(project, "settings", {"scan": {"workers": 5}, "depth": "deep"})
    path = project / "workspace" / "drafts" / "settings.json"
    kept = path.read_bytes()
    save_draft(project, "settings", {})
    assert path.read_bytes() == kept
    assert read_draft(project, "settings").values == {"scan": {"workers": 5}, "depth": "deep"}


def test_a_value_leaves_the_draft_only_where_a_save_names_it(project: Path) -> None:
    save_draft(project, "settings", {"scan": {"workers": 5, "max_depth": 2}, "depth": "deep"})
    # One save both adds and takes out; the value it does not name stays.
    save_draft(project, "settings", {"scan": {"workers": 6}}, removed=["scan.max_depth", ["depth"]])
    assert read_draft(project, "settings").values == {"scan": {"workers": 6}}
    # Taking out the last value of a group takes out the group; naming what is not there is
    # nothing.
    save_draft(project, "settings", {}, removed=["scan.workers", "hash.mode"])
    assert read_draft(project, "settings").values == {}
    assert uncommitted(project)["settings"] == 0


def test_the_errors_saved_are_those_of_the_whole_draft(project: Path) -> None:
    save_draft(project, "settings", {"scan": {"workers": -3}})
    saved = save_draft(project, "settings", {"depth": "deep"})
    assert "scan.workers" in saved.errors
    taken_out = save_draft(project, "settings", {}, removed=["scan.workers"])
    assert "scan.workers" not in taken_out.errors


def test_an_invalid_value_is_kept_with_its_error_and_a_save_never_refuses_it(
    project: Path,
) -> None:
    saved = save_draft(project, "settings", {"scan": {"workers": -3}})
    assert "scan.workers" in saved.errors
    kept = read_draft(project, "settings")
    assert kept.values == {"scan": {"workers": -3}}
    assert "scan.workers" in kept.errors


def test_a_rule_parameter_the_rulebook_would_refuse_is_kept_with_its_error(project: Path) -> None:
    saved = save_draft(
        project,
        "rule_parameters",
        {BOUNDED: {"maximum_candidates": "many"}, DOUBT: {"minimum_terms": 2}},
    )
    assert set(saved.errors) == {BOUNDED}
    assert read_draft(project, "rule_parameters").values[BOUNDED] == {"maximum_candidates": "many"}


def test_the_button_counts_leaves_of_the_two_committed_kinds_and_not_views(project: Path) -> None:
    save_draft(project, "settings", {"scan": {"workers": 5, "max_depth": 2}, "depth": "deep"})
    save_draft(project, "rule_parameters", {BOUNDED: {"maximum_candidates": 30}})
    save_draft(project, "views", {"graph": {"depth": 2}})
    assert uncommitted(project) == {"settings": 3, "rule_parameters": 1}


def test_an_unknown_draft_kind_is_refused(project: Path) -> None:
    with pytest.raises(ValueError, match="no draft kind"):
        save_draft(project, "moods", {})


def test_a_revision_is_appended_read_by_the_rulebook_and_never_edits_an_earlier_one(
    project: Path,
) -> None:
    first = write_revision(project, {BOUNDED: {"maximum_candidates": 30}}, summary="one")
    before = (project / "rulebook" / "project-0001.yaml").read_bytes()
    second = write_revision(project, {DOUBT: {"minimum_terms": 2}}, summary="two")
    assert (first, second) == (1, 2)
    assert (project / "rulebook" / "project-0001.yaml").read_bytes() == before
    assert project_rulebook(project).project_revision == 2
    rules = {rule.rule_id: rule for rule in project_rulebook(project).rules}
    assert json.loads(rules[BOUNDED].parameters[0].default) == 30


def test_a_refused_override_writes_no_revision_that_would_stop_the_project_opening(
    project: Path,
) -> None:
    assert check_overrides(project, {"no_such_rule": {"x": 1}})
    with pytest.raises(ValueError, match="no_such_rule"):
        write_revision(project, {"no_such_rule": {"x": 1}}, summary="bad")
    assert not (project / "rulebook").exists() or not list((project / "rulebook").glob("*.yaml"))
    assert project_rulebook(project).project_revision == 0


@pytest.mark.parametrize("value", [["xy"], 5, "zib"])
def test_rule_parameters_that_are_not_names_with_values_are_refused_and_never_written(
    project: Path, value: object
) -> None:
    """A value that is not a mapping is refused. Otherwise the writer would write it anyway
    (``["xy"]`` becoming a parameter ``x``) and the Rulebook would not load."""
    from ingence.workspace.checkpoints import commit

    saved = save_draft(project, "rule_parameters", {BOUNDED: value})
    assert BOUNDED in saved.errors
    assert uncommitted(project)["rule_parameters"] == 1
    with pytest.raises(ValueError, match=BOUNDED):
        commit(project, actor="Ana", message="m")
    with pytest.raises(ValueError, match=BOUNDED):
        write_revision(project, {BOUNDED: value}, summary="m")
    assert not (project / "rulebook").exists() or not list((project / "rulebook").glob("*.yaml"))
    assert project_rulebook(project).project_revision == 0
