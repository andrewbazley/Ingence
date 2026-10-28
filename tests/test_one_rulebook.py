"""One Rulebook: the rules that projects once kept in their own rulebook folder ship in the engine.

Those rules are composed after the book's first four revisions. The shipped loader reads a
revision's changes and its deferral as a project's loader does. A rule's versions compare as
numbers. Every name is invented.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from typer.testing import CliRunner

from fixtures.revision_4 import (
    FILE_NAME,
    REVISION,
    retired_form,
    stored_version,
    write_retired,
)
from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.record.targets import group_target
from ingence.rulebook import project_rulebook, shipped_rulebook
from ingence.rulebook.load import (
    BUILTIN_DIRECTORY,
    Revision,
    RuleDeclarationError,
    load_project_revision,
    load_revision,
    load_revisions,
)
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.resolve import effective_rulebook, version_key
from ingence.rulebook.retired import read_retired, sentence, set_aside
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import RULEBOOK_DIR

if TYPE_CHECKING:
    from collections.abc import Callable

load_builtins()


# ---------------------------------------------------------------------------
# Versions compare as numbers.
# ---------------------------------------------------------------------------


def test_a_rules_versions_compare_as_numbers_with_a_mark_after_its_number() -> None:
    assert version_key("10") > version_key("9")
    assert version_key("2") > version_key("1+p9")
    assert version_key("3+d1") > version_key("3")
    assert version_key("3+p2") > version_key("3+p1")
    assert version_key("3+p10") > version_key("3+p9")
    ordered = ["1", "1+p1", "2", "2+r5", "9", "10"]
    assert sorted(["10", "2+r5", "9", "1+p1", "2", "1"], key=version_key) == ordered


def test_a_rule_declared_again_at_version_10_displaces_its_version_9(tmp_path: Path) -> None:
    """Two declarations added at one revision: the tie-break reads the version as a number."""
    declaration = {
        "id": "copies_of_zib",
        "predicate": "same_content_digest",
        "effect": "groups",
        "primitive": "identical",
        "tier": "entailed",
        "level": "file",
        "added_revision": 0,
        "statement": "Two entries hold the same bytes when their {digest_algorithm} digests agree.",
        "rationale": "Agreement on a whole digest is a fact.",
        "parameters": [{"name": "digest_algorithm", "type": "string", "default": "blake3_whole"}],
    }
    rules = []
    for number, version in ((0, "9"), (1, "10")):
        path = tmp_path / f"0{number}.yaml"
        body = {"revision": number, "summary": "s", "rules": [{**declaration, "version": version}]}
        path.write_text(yaml.safe_dump(body), encoding="utf-8")
        rules.extend(load_revision(path).rules)
    book = effective_rulebook([Revision(number=0, summary="s", rules=tuple(rules))])
    held = book.by_id("copies_of_zib")
    assert held is not None
    assert held.version == "10"


# ---------------------------------------------------------------------------
# The shipped loader reads a revision's changes and its deferral.
# ---------------------------------------------------------------------------


def _book_with(tmp_path: Path, **document: object) -> Path:
    """The shipped YAML revisions, copied, and one more revision holding ``document``."""
    directory = tmp_path / "book"
    directory.mkdir()
    for path in sorted(BUILTIN_DIRECTORY.glob("*.yaml")):
        shutil.copyfile(path, directory / path.name)
    body = {"revision": 9, "summary": "A later revision.", **document}
    (directory / "09-later.yaml").write_text(
        yaml.safe_dump(body, sort_keys=False), encoding="utf-8"
    )
    return directory


def test_a_shipped_revision_keeps_its_override_its_supersession_and_its_deferral(
    tmp_path: Path,
) -> None:
    directory = _book_with(
        tmp_path,
        changes=[
            {"override": "same_composition", "parameters": {"minimum_children": 8}},
            {"supersede": "authored_name_doubt"},
        ],
        defer_beneath=[
            {
                "scope": "vocabulary_unread_in_folder",
                "except": ["extension_contradicts_content"],
                "rationale": "Nothing beneath a folder of unread words is read for an experiment.",
            }
        ],
    )
    revisions = load_revisions(directory)
    later = revisions[-1]
    assert [change.rule_id for change in later.changes] == [
        "same_composition",
        "authored_name_doubt",
    ]
    assert [deferral.scope for deferral in later.deferrals] == ["vocabulary_unread_in_folder"]

    book = effective_rulebook(revisions)
    tuned = book.by_id("same_composition")
    assert tuned is not None
    assert tuned.version == "1+r9"
    assert {spec.name: spec.default for spec in tuned.parameters}["minimum_children"] == "8"
    assert book.by_id("authored_name_doubt") is None
    assert dict(book.inactive)["authored_name_doubt"] == "superseded at revision 9"
    formats = book.by_id("format_from_extension")
    assert formats is not None
    assert "vocabulary_unread_in_folder" in formats.defeated_by
    assert formats.version == "2+d1"  # the book's first deferral
    assert ("format_from_extension", "vocabulary_unread_in_folder", 9) in book.shipped_deferred
    assert book.by_id("extension_contradicts_content").version == "1"  # type: ignore[union-attr]
    # At an earlier revision none of it holds: a past run replays.
    earlier = effective_rulebook(revisions, at=3)
    assert earlier.by_id("same_composition").version == "1"  # type: ignore[union-attr]
    assert earlier.by_id("authored_name_doubt") is not None
    assert not earlier.shipped_deferred


def test_a_shipped_change_naming_a_rule_not_in_force_is_refused(tmp_path: Path) -> None:
    directory = _book_with(tmp_path, changes=[{"supersede": "zib_from_nothing"}])
    with pytest.raises(RuleDeclarationError, match="zib_from_nothing"):
        effective_rulebook(load_revisions(directory))


# ---------------------------------------------------------------------------
# Revision 4 is shipped.
# ---------------------------------------------------------------------------


def test_the_shipped_book_composes_revision_4_after_revisions_0_to_3() -> None:
    book = shipped_rulebook()
    # The shipped book is at revision 5, which ships from 0.4.0.
    assert book.revision == 5
    declared = {
        rule["id"]
        for rule in REVISION["rules"]  # type: ignore[attr-defined]
        if rule.get("enabled", True)
    }
    superseded = {
        change["supersede"]
        for change in REVISION["changes"]  # type: ignore[attr-defined]
        if "supersede" in change
    }
    from ingence.rulebook.revision_5 import REVISION as FIFTH

    superseded_at_5 = {
        str(change["supersede"])
        for change in FIFTH["changes"]  # type: ignore[union-attr]
        if isinstance(change, dict) and "supersede" in change
    }
    declared_at_5 = {
        str(rule["id"])
        for rule in FIFTH["rules"]  # type: ignore[union-attr]
        if isinstance(rule, dict)
    }
    assert declared - superseded_at_5 <= {rule.rule_id for rule in book.rules}
    # A rule revision 4 ships switched off is inactive too, and says so, not "superseded".
    assert dict(book.inactive).keys() == (
        superseded | superseded_at_5 | {"succeeds_by_series_index"}
    )
    assert {reason for rule, reason in dict(book.inactive).items() if rule in superseded} == {
        "superseded at revision 4"
    }
    assert dict(book.inactive)["succeeds_by_series_index"] == "disabled"
    # Every rule keeps its history. The book at revision 3 is the one before, and the book at 4 is
    # it, less what revision 4 supersedes, with what revision 4 declares. The test states a
    # relation, never a count, so a revision that adds a rule changes nothing here.
    before = effective_rulebook(load_revisions(), at=3)
    held_before = {rule.rule_id for rule in before.rules}
    assert superseded <= held_before
    shipped_off = {"succeeds_by_series_index"}  # its v1 stood in the book before, and v2 ships off
    assert {rule.rule_id for rule in book.rules} == (
        (held_before - superseded - shipped_off) | declared
    ) - superseded_at_5 | declared_at_5
    # The deferral is the book's: every rule but a physical fact defers beneath a program.
    deferred = {rule_id for rule_id, scope, number in book.shipped_deferred}
    assert {scope for _, scope, _ in book.shipped_deferred} == {"installed_software_in_folder"}
    assert {"name_family_without_stages", "same_name_identity"} <= deferred
    assert "identical_by_content_digest" not in deferred
    for rule_id in deferred:
        rule = book.by_id(rule_id)
        assert rule is not None
        assert "installed_software_in_folder" in rule.defeated_by
        # The mark each carried as a project's revision 1: identifiers, and decisions, hold.
        assert rule.version.endswith("+d1"), (rule_id, rule.version)


def test_the_copy_rule_names_the_digest_a_dive_computes_in_the_shipped_book() -> None:
    from ingence.identity.bytes import WholeDigest

    rule = shipped_rulebook().by_id("identical_by_content_digest")
    assert rule is not None
    assert rule.version == "2"
    assert json.loads(rule.parameters[0].default) == WholeDigest.name == "blake3_whole"
    first = effective_rulebook(load_revisions(), at=3).by_id("identical_by_content_digest")
    assert first is not None
    assert (first.version, json.loads(first.parameters[0].default)) == ("1", "blake2b")


def _said(output: str) -> str:
    return " ".join(output.split())


def test_a_newcomers_project_finds_three_byte_identical_files_as_copies(tmp_path: Path) -> None:
    """A bare ``init``, a census and a dive at the default depth: the copy rule groups the three."""
    pool = tmp_path / "pool"
    same = b"zib kelt frame\n" * 256
    for relative in ("brisa/vorn_01.tif", "kelt/vorn_01 copy.tif", "zeph/old/vorn_01.tif"):
        (pool / relative).parent.mkdir(parents=True, exist_ok=True)
        (pool / relative).write_bytes(same)
    other = pool / "brisa" / "vorn_02.tif"
    other.write_bytes(b"zib kelt fraME\n" * 256)  # the same size, other bytes
    project = tmp_path / "project"
    runner = CliRunner()
    for command in (
        ["init", str(project), "--root", str(pool)],
        ["index", "-p", str(project), "--quiet"],
        ["dive", "-p", str(project), "--budget", "60"],
        ["infer", "-p", str(project)],
    ):
        done = runner.invoke(app, command)
        assert done.exit_code == 0, (command, done.output)
    assert not list((project / RULEBOOK_DIR).glob("*.yaml"))  # no rules file: nothing to write
    with open_catalog(project) as catalog:
        classes = catalog.query(
            "SELECT list_sort(list(e.relative_path)) FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'identical_by_content_digest' AND c.resolution_status = 'resolved' "
            "GROUP BY c.object_reference HAVING count(*) > 1"
        ).fetchall()
    assert classes == [(["brisa/vorn_01.tif", "kelt/vorn_01 copy.tif", "zeph/old/vorn_01.tif"],)]


def test_init_writes_no_rules_file_and_its_retired_option_chooses_nothing(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt.txt").write_text("x")
    runner = CliRunner()
    bare = runner.invoke(app, ["init", str(tmp_path / "bare"), "--root", str(pool)])
    assert bare.exit_code == 0, bare.output
    assert "revision" not in bare.output.casefold()
    assert "every shipped domain loads" in _said(bare.output)  # one dictionary (0.4.0)
    for value in ("shipped", "revision-4"):
        made = runner.invoke(
            app, ["init", str(tmp_path / value), "--root", str(pool), "--rules", value]
        )
        assert made.exit_code == 0, made.output
        assert "--rules is retired: every project reads the same rules" in _said(made.output)
    refused = runner.invoke(
        app, ["init", str(tmp_path / "zib"), "--root", str(pool), "--rules", "zib"]
    )
    assert refused.exit_code != 0
    assert not (tmp_path / "zib").exists()
    for made in ("bare", "shipped", "revision-4"):
        assert not list((tmp_path / made / RULEBOOK_DIR).glob("*.yaml"))
        assert project_rulebook(tmp_path / made).digest == shipped_rulebook().digest
    help_text = runner.invoke(app, ["init", "--help"]).output
    assert "--rules" not in help_text


# ---------------------------------------------------------------------------
# Every existing project keeps loading.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(lambda: stored_version("2026-09-30"), id="as-init-wrote-it-on-30-september"),
        pytest.param(lambda: stored_version("2026-09-28"), id="as-on-28-september"),
        pytest.param(lambda: stored_version("2026-09-15"), id="as-on-15-september"),
    ],
)
def test_a_project_holding_the_retired_file_loads_the_engines_rules(
    tmp_path: Path, document: Callable[[], object]
) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    path = write_retired(project.path, document())
    found = read_retired(path.parent)
    assert found is not None
    assert (found.kept, found.not_kept) == ({}, ())  # the rules' own history, nobody's choice
    book = project_rulebook(project.path)
    assert book.digest == shipped_rulebook().digest
    assert book.rules == shipped_rulebook().rules
    assert book.project_revision == 0  # nothing of the project's own


def test_a_file_of_that_name_holding_a_persons_own_rule_is_their_revision(tmp_path: Path) -> None:
    own = retired_form()
    own["rules"] = [
        {
            **next(rule for rule in own["rules"] if rule["id"] == "same_name_identity"),
            "id": "same_name_identity_of_ines",
        }
    ]
    own["changes"] = []
    own.pop("defer_beneath")
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    path = write_retired(project.path, own)
    assert read_retired(path.parent) is None
    book = project_rulebook(project.path)
    assert book.by_id("same_name_identity_of_ines") is not None
    assert book.project_revision == 1


def _five_experiments(base: Path) -> Path:
    pool = base / "pool"
    for day in ("20240301 kelt vorn", "20240302 kelt vorn"):
        for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2"):
            (pool / day).mkdir(parents=True, exist_ok=True)
            (pool / day / name).write_text("x")
    (pool / "20240303 kelt vorn").mkdir(parents=True)
    (pool / "20240303 kelt vorn" / "kelt_vorn_01.nd2").write_text("x")
    (pool / "20240303 kelt vorn" / "kelt_vorn_trell_02.nd2").write_text("x")
    (pool / "20240304 brisa quolt").mkdir(parents=True)
    (pool / "20240304 brisa quolt" / "brisa_quolt_01.nd2").write_text("x")
    return pool


def _deviations_asked(tmp_path: Path, rules_file: object | None) -> set[str]:
    """Approve ``kelt vorn`` over three days and return the deviations the next run asks."""
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[_five_experiments(tmp_path)]
    )
    if rules_file is not None:
        write_retired(project.path, rules_file)
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = "run_20260101T000002Z_000000000002"
    run_inference(project, run_id=first, started_at="t")
    append_event(
        project.path,
        kind=RecordEventKind.CONFIRM,
        actor="ines",
        occurred_at="2026-01-01T00:00:30Z",
        target=group_target(["kelt", "vorn"]),
        against_run_id=first,
        payload={"terms": ["kelt", "vorn"]},
    )
    run_inference(project, run_id="run_20260101T000003Z_000000000003", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT raised FROM findings WHERE rule_id = 'group_resolution_deviation'"
        ).fetchall()
    return {value for (raised,) in rows for value in raised}


def _with_each_deviation(document: dict[str, Any]) -> dict[str, Any]:
    """The old rules file with each ``derivations`` parameter set to ``each_deviation``."""
    for rule in document["rules"]:
        for parameter in rule.get("parameters", ()):
            if parameter["name"] == "derivations":
                parameter["default"] = "each_deviation"
    return document


def test_a_project_made_with_each_deviation_still_asks_each_deviation(tmp_path: Path) -> None:
    chosen = _with_each_deviation(retired_form())
    project = Project.create(tmp_path / "made" / "project", created_at="t", roots=[])
    path = write_retired(project.path, chosen)
    found = read_retired(path.parent)
    assert found is not None
    derivations = {rule_id: values["derivations"] for rule_id, values in found.kept.items()}
    assert set(derivations.values()) == {"each_deviation"}
    assert "group_resolved_across_experiments" in derivations
    book = project_rulebook(project.path)
    for rule_id in derivations:
        rule = book.by_id(rule_id)
        assert rule is not None
        spec = next(spec for spec in rule.parameters if spec.name == "derivations")
        assert json.loads(spec.default) == "each_deviation"
        assert "+p1" in rule.version
    asked = _deviations_asked(tmp_path / "each", chosen)
    assert asked == {"trell, added to the group kelt vorn"}
    assert _deviations_asked(tmp_path / "lattice", None) != asked  # the default asks otherwise


def test_a_rule_change_committed_after_the_retired_file_keeps_applying(tmp_path: Path) -> None:
    from ingence.workspace.revisions import write_revision as write_rule_change

    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    write_retired(project.path, _with_each_deviation(retired_form()))
    number = write_rule_change(
        project.path,
        {"group_resolved_across_experiments": {"maximum_concepts": 400}},
        summary="ines: fewer sets at once",
    )
    assert number == 2  # the retired file keeps its number
    across = project_rulebook(project.path).by_id("group_resolved_across_experiments")
    assert across is not None
    values = {spec.name: json.loads(spec.default) for spec in across.parameters}
    assert (values["derivations"], values["maximum_concepts"]) == ("each_deviation", 400)
    assert across.version.startswith("3+p2")  # both changes, the later one's number


def test_infer_says_once_that_the_retired_file_is_no_longer_read(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt_vorn_01.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_retired(project.path, _with_each_deviation(retired_form()))
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    done = CliRunner().invoke(app, ["infer", "-p", str(project.path)])
    assert done.exit_code == 0, done.output
    said = _said(done.output)
    assert said.count(f"{RULEBOOK_DIR}/{FILE_NAME}") == 1
    assert "no longer read" in said
    assert "Your choices in it still apply" in said
    assert "each deviation" in said
    assert "fix --set-aside-old-rules" in said
    assert "revision 4" not in said


def test_fix_offers_to_set_the_retired_file_aside_and_never_deletes_it(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    path = write_retired(project.path, _with_each_deviation(retired_form()))
    held = path.read_bytes()
    before = project_rulebook(project.path)
    runner = CliRunner()
    offered = runner.invoke(app, ["fix", "-p", str(project.path)])
    assert offered.exit_code == 0, offered.output
    assert "--set-aside-old-rules" in _said(offered.output)
    assert path.is_file()  # offered, not done
    moved = runner.invoke(app, ["fix", "-p", str(project.path), "--set-aside-old-rules"])
    assert moved.exit_code == 0, moved.output
    aside = project.path / RULEBOOK_DIR / "retired" / FILE_NAME
    assert not path.exists()
    assert aside.read_bytes() == held  # moved whole, never rewritten
    assert (project.path / RULEBOOK_DIR / "project-0001.yaml").is_file()
    after = project_rulebook(project.path)
    assert after.digest == before.digest  # the same rules, the same choices
    assert "project-0001.yaml" in _said(moved.output)
    again = runner.invoke(app, ["fix", "-p", str(project.path)])
    assert "--set-aside-old-rules" not in _said(again.output)  # nothing left to offer


def test_setting_aside_a_file_with_no_choices_writes_no_rule_change(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    write_retired(project.path)
    moved = set_aside(project.path)
    assert moved is not None
    assert moved[1] is None
    assert [path.name for path in (project.path / RULEBOOK_DIR).glob("*.yaml")] == []
    assert project_rulebook(project.path).digest == shipped_rulebook().digest


def test_every_rule_keeps_the_version_it_had_so_every_decision_still_applies(
    tmp_path: Path,
) -> None:
    """A claim's, a candidate's and a finding's identifier hold its rule's version (``core/ids``):
    before and after the fold every rule reads at one version but the copy rule, fixed."""
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    path = write_retired(project.path)
    before = effective_rulebook(
        [revision for revision in load_revisions() if revision.number <= 3],
        project=(load_project_revision(path),),
    )
    after = project_rulebook(project.path)
    versions_before = {rule.rule_id: rule.version for rule in before.rules}
    versions_after = {rule.rule_id: rule.version for rule in after.rules}
    assert versions_before.pop("identical_by_content_digest") == "1+p1"
    assert versions_after.pop("identical_by_content_digest") == "2"
    # Raised after the fold: each changed its meaning, so each is a new version, and a decision on
    # the old one belongs to the old one. Every other rule keeps the version the fold froze.
    raised_after = {
        "derivation_within_name_identity": ("4", "7"),
        "derivation_named_for_an_acquisition": ("1", "6"),
        # A file whose whole name another's holds keys a group of its own.
        "named_for_an_acquisition": ("1", "2"),
        "derivation_endpoints_hold_roles_or_a_scripts_calls": ("1", "2"),
        "derivation_named_by_a_describing_file": ("1", "2"),
        "stage_disagrees_with_a_derivation": (None, "2"),
        "related_within_name_identity": (None, "3"),
        # A run's per-item folders holding the same files are its parts.
        "experiment_folder_from_contents": ("2", "3"),
        "experiment_folder_from_dated_name": ("1", "2"),
        # A template's creation stamp is not when a document first existed.
        "earliest_credible_time": ("2", "3"),
        # The rule that orders replicates draws within a condition's class.
        "succeeds_by_series_index": ("1", "2"),
        # The digest holds column names, order and delimiter, not types. The array digest holds
        # the shape, element type, order and dimensions. Both name defeaters.
        "same_column_schema": ("1", "2"),
        "same_array_shape": ("1", "2"),
        # A bound word is its Dictionary term in the lattice (``groups.words_as``), and a stored
        # approval is read in the words the lattice holds.
        "group_resolved_within_experiment": ("2", "3"),
        "group_resolved_across_experiments": ("2", "3"),
        "group_resolution_deviation": ("2", "3"),
        # 0.4.0: a read file is recorded data. A stage word is read only where it stands alone in
        # a name. One file at two steps is not a replicate of itself.
        "acquisition_stage_from_format": ("3", "4"),
        "pipeline_stage_from_name": ("2", "3"),
        "replicates_of_one_condition": ("1", "2"),
        "experiments_may_belong_together": ("3", "4"),
    }
    for rule_id, (old, new) in raised_after.items():
        assert versions_before.pop(rule_id, None) in (old, f"{old}+d1"), rule_id
        if rule_id == "succeeds_by_series_index":
            continue  # shipped off: not among the rules in force (checked in test_series_order)
        assert versions_after.pop(rule_id) in (new, f"{new}+d1"), rule_id
    # The pack rule's version 2 counts an instrument's files and asks a project on every pack
    # about the idle ones. Version 3 takes its share of the entries rather than of the spellings.
    # Pack answers sit on pack:<domain>, so every decision still applies. Version 4 asks its
    # questions first, before the group questions. Revision 5 (0.4.0) supersedes the rule: every
    # domain loads, so nothing is proposed.
    assert versions_before.pop("domain_pack_would_read_this_pool") == "1"
    assert "domain_pack_would_read_this_pool" not in versions_after
    versions_after.pop("folder_domain_profile", None)
    # Revision 5 supersedes the conflict rule too. It is declared again under a name of its own,
    # on the predicate's version 3, which keeps a tie between domains.
    assert versions_before.pop("binding_is_ambiguous_outside_programs") == "1"
    assert "binding_is_ambiguous_outside_programs" not in versions_after
    versions_after.pop("binding_is_ambiguous_outside_programs_and_domains", None)
    versions_after.pop("spelling_read_as", None)
    # A group keyed by an index alone is retired, and a version family is keyed by every part of
    # a name but its version: a supersession and an override, each said by its mark.
    assert versions_before.pop("same_acquisition_event") == "1"
    assert "same_acquisition_event" not in versions_after
    assert versions_before.pop("same_version_family") == "1"
    assert versions_after.pop("same_version_family") == "1+r4"
    # A rule declared after the file was written is new, not changed
    # (``copies_a_person_answered``): every rule the file knew keeps its version.
    assert {rule_id: versions_after.get(rule_id) for rule_id in versions_before} == versions_before


# ---------------------------------------------------------------------------
# A run made before the fold restores.
# ---------------------------------------------------------------------------

BOUNDED = "candidate_generation_is_bounded"


def _rule_change(project: Path, number: int, maximum: int) -> None:
    (project / RULEBOOK_DIR / f"project-{number:04d}.yaml").write_text(
        yaml.safe_dump(
            {
                "project_revision": number,
                "summary": "tomas: fewer candidates",
                "changes": [{"override": BOUNDED, "parameters": {"maximum_candidates": maximum}}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_a_run_made_before_the_fold_restores_its_rule_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its Rulebook (``3+d1``, ``1+p1`` and the project's own changes) is rebuilt the way it was
    composed then, so restoring it returns the parameters it used."""
    from ingence.rulebook import load, retired
    from ingence.workspace.restore import restore_plan

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_retired(made.path)
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z")
    _rule_change(made.path, 2, 6)
    with monkeypatch.context() as before_the_fold:
        before_the_fold.setattr(load, "_declared_in_python", lambda *, scope: [])
        before_the_fold.setattr(retired, "read_retired", lambda directory: None)
        old = project_rulebook(made.path)
        assert old.by_id("identical_by_content_digest").version == "1+p1"  # type: ignore[union-attr]
        run_inference(
            made, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
        )
    _rule_change(made.path, 3, 9)
    run_inference(
        made, run_id="run_20260101T000004Z_444444444444", started_at="2026-01-01T00:00:04Z"
    )
    plan = restore_plan(made.path, "run_20260101T000002Z_222222222222")
    assert plan.rules_reverted == {BOUNDED: {"maximum_candidates": [9, 6]}}
    assert plan.rules_kept == ()
    # And the same once the file is set aside.
    set_aside(made.path)
    plan = restore_plan(made.path, "run_20260101T000002Z_222222222222")
    assert plan.rules_reverted == {BOUNDED: {"maximum_candidates": [9, 6]}}


# ---------------------------------------------------------------------------
# Words.
# ---------------------------------------------------------------------------


def test_the_rules_page_and_rule_show_say_it_in_words_and_never_revision_4(tmp_path: Path) -> None:
    from ingence.api.routes import rules as rule_routes

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt_vorn_01.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_retired(project.path, _with_each_deviation(retired_form()))
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_000000000002", started_at="t")
    with open_catalog(project.path) as catalog:
        shown = rule_routes.rules(catalog, project.path)
    assert "no longer read" in shown["older_rules_file"]
    reasons = {rule["reason"] for rule in shown["inactive"]}
    assert reasons == {"replaced by a newer rule", "switched off"}
    assert "revision 4" not in json.dumps(shown["inactive"]) + shown["older_rules_file"].replace(
        FILE_NAME, ""
    )
    printed = CliRunner().invoke(app, ["rule", "show", "-p", str(project.path)])
    assert printed.exit_code == 0, printed.output
    said = _said(printed.output)
    assert said.startswith("Rules in force, with your project's rules")
    assert "replaced by a newer rule" in said
    assert "revision 4" not in said


def test_a_default_changed_after_the_fold_is_never_taken_for_a_persons_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later revision changing a default (``minimum_experiments`` 2 to 5, say) leaves an old
    project's file holding 2, which every version of the file gave it: history, not a choice."""
    from ingence.rulebook import retired

    in_force = retired._book()
    across = in_force["group_resolved_across_experiments"]
    changed = across.model_copy(
        update={
            "parameters": tuple(
                spec.model_copy(update={"default": "5"})
                if spec.name == "minimum_experiments"
                else spec
                for spec in across.parameters
            )
        }
    )
    monkeypatch.setattr(retired, "_book", lambda: {**in_force, changed.rule_id: changed})
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    path = write_retired(project.path)
    found = read_retired(path.parent)
    assert found is not None
    assert found.kept == {}
    chosen = retired_form()
    for rule in chosen["rules"]:
        for parameter in rule.get("parameters", ()):
            if parameter["name"] == "maximum_concepts":
                parameter["default"] = 400  # a value no version gave: somebody's
    write_retired(project.path, chosen)
    found = read_retired(path.parent)
    assert found is not None
    assert found.kept == {"group_resolved_across_experiments": {"maximum_concepts": 400}}


# ---------------------------------------------------------------------------
# Edge cases of the old file: recognition frozen at the fold, number 1 never reused, a person's
# other edits kept or said.
# ---------------------------------------------------------------------------


def test_a_rule_a_later_revision_renames_leaves_every_old_file_recognised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recognition reads what the file ever held, never today's book."""
    import copy

    from ingence.rulebook import load, retired, revision_4

    renamed = copy.deepcopy(REVISION)
    for rule in renamed["rules"]:  # type: ignore[attr-defined]
        if rule["id"] == "derivation_endpoints_hold_roles_or_a_scripts_calls":
            rule["id"] = "derivation_endpoints_are_not_programs_of_zib"
    monkeypatch.setattr(revision_4, "REVISION", renamed)
    monkeypatch.setattr(retired, "_book", lambda: {})  # nothing of a person's to keep here
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    path = write_retired(project.path)
    assert read_retired(path.parent) is not None
    book = effective_rulebook(load_revisions(), project=load.load_project_revisions(path.parent))
    assert book.by_id("derivation_endpoints_are_not_programs_of_zib") is not None


def test_a_rule_change_after_the_fold_never_takes_the_old_files_number(tmp_path: Path) -> None:
    """Number 1 stays the old file's, standing or set aside."""
    from ingence.workspace.revisions import write_revision as write_rule_change

    for set_aside_first in (False, True):
        project = Project.create(tmp_path / f"p{set_aside_first}", created_at="t", roots=[])
        write_retired(project.path)
        if set_aside_first:
            set_aside(project.path)
        number = write_rule_change(
            project.path,
            {"candidate_generation_is_bounded": {"maximum_candidates": 9}},
            summary="x",
        )
        assert number == 2, set_aside_first
    fresh = Project.create(tmp_path / "fresh", created_at="t", roots=[])
    assert (
        write_rule_change(
            fresh.path, {"candidate_generation_is_bounded": {"maximum_candidates": 9}}, summary="x"
        )
        == 1
    )


def test_a_run_made_before_the_fold_restores_after_a_change_committed_since(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old file with no choice in it, a run before the fold, then a person's rule change
    after it. Restoring the run returns the parameter."""
    from ingence.rulebook import load, retired
    from ingence.workspace.restore import restore_plan
    from ingence.workspace.revisions import write_revision as write_rule_change

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_retired(made.path)
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z")
    with monkeypatch.context() as before_the_fold:
        before_the_fold.setattr(load, "_declared_in_python", lambda *, scope: [])
        before_the_fold.setattr(retired, "read_retired", lambda directory: None)
        was = project_rulebook(made.path).by_id(BOUNDED)
        run_inference(
            made, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
        )
    assert was is not None
    default = json.loads(next(s for s in was.parameters if s.name == "maximum_candidates").default)
    write_rule_change(made.path, {BOUNDED: {"maximum_candidates": 9}}, summary="tomas: fewer")
    run_inference(
        made, run_id="run_20260101T000004Z_444444444444", started_at="2026-01-01T00:00:04Z"
    )
    plan = restore_plan(made.path, "run_20260101T000002Z_222222222222")
    assert plan.rules_reverted == {BOUNDED: {"maximum_candidates": [9, default]}}
    assert plan.rules_kept == ()


def test_an_edit_that_is_no_value_is_kept_as_the_projects_rules_or_said(tmp_path: Path) -> None:
    """A rule switched off is kept. A tier or a deferral edited is said."""
    edited = retired_form()
    edited["changes"].append({"disable": "same_composition"})
    for rule in edited["rules"]:
        if rule["id"] == "copied_in_bulk":
            rule["enabled"] = False
        if rule["id"] == "same_name_identity":
            rule["tier"] = "entailed"
    edited["defer_beneath"][0]["except"].append("same_name_identity")
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    path = write_retired(project.path, edited)
    found = read_retired(path.parent)
    assert found is not None
    assert {"disable": "same_composition"} in found.changes
    assert {"disable": "copied_in_bulk"} in found.changes
    assert "same_name_identity: how it is declared" in found.not_kept
    assert "the rules a program's folder does not defer" in found.not_kept
    book = project_rulebook(project.path)
    assert book.by_id("same_composition") is None
    assert book.by_id("copied_in_bulk") is None
    said = sentence(found)
    assert "same_composition switched off" in said
    assert "same_name_identity: how it is declared" in said


def test_the_old_file_with_a_persons_own_rule_added_still_loads(tmp_path: Path) -> None:
    """The whole old file and one rule of a person's own."""
    own = retired_form()
    (identity,) = [rule for rule in own["rules"] if rule["id"] == "same_name_identity"]
    own["rules"].append({**identity, "id": "same_name_identity_of_ines"})
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    path = write_retired(project.path, own)
    found = read_retired(path.parent)
    assert found is not None
    assert [rule["id"] for rule in found.rules] == ["same_name_identity_of_ines"]
    book = project_rulebook(project.path)
    assert book.by_id("same_name_identity_of_ines") is not None
    assert book.project_revision == 1
    assert "your own rule same_name_identity_of_ines" in sentence(found)


def test_a_file_holding_one_former_rule_and_one_of_a_persons_own_is_the_old_file() -> None:
    """A tie is the old file: otherwise neither reading loads the project."""
    from ingence.rulebook.retired import FILE_NAME, history, recognised

    former = sorted(history().rules)[0]
    document = {
        "project_revision": 1,
        "rules": [{"id": former}, {"id": "zeph_rule_of_my_own"}],
        "changes": [],
    }
    assert recognised(FILE_NAME, document)
    document["rules"].append({"id": "kelt_rule_of_my_own"})
    assert not recognised(FILE_NAME, document)
