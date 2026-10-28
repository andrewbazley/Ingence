"""The scan layer's own rules: declared, provisional, and cited as the Rulebook binds them.

Every run cites four rules, and revision 3 is the first revision that declares them. Held here: each
is a rule a person can open, no project revision can switch one off while it is provisional, and the
census and the cascade cite the version the Rulebook they are handed states rather than a constant
of their own.
"""

from __future__ import annotations

import os
import re
import stat
import zipfile
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.core.relations import ExpansionState
from ingence.core.schema import RuleEffect, RuleStatus
from ingence.extractors.base import DerivedStream
from ingence.infer.run import run_inference
from ingence.infer.subjects import DirectoryCounts, Listing
from ingence.rulebook import shipped_rulebook
from ingence.rulebook.load import (
    BUILTIN_DIRECTORY,
    RuleDeclarationError,
    load_project_revisions,
    load_revision,
    load_revisions,
)
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES, DirectoryTotals, Embedding, Reads, reads_of
from ingence.rulebook.render import render_rulebook
from ingence.rulebook.resolve import effective_rulebook, rulebook_digest
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.scan.rules import CASCADE_MEMBERSHIP, CENSUS_RULE_IDS, ScanRule, scan_rules
from ingence.storage.catalog import open_catalog
from ingence.storage.commit import staging_path

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.rulebook.resolve import EffectiveRulebook

load_builtins()

_SCAN_RULES = (*CENSUS_RULE_IDS, CASCADE_MEMBERSHIP)
_CENSUS_RUN = "run_20260101T000001Z_111111111111"
_SECOND_RUN = "run_20260101T000002Z_222222222222"


def _bound(rule_id: str) -> ScanRule:
    return scan_rules((rule_id,))[rule_id]


def _project_revision(directory: Path, number: int, **document: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    body = {"project_revision": number, "summary": "A change.", **document}
    (directory / f"{number:02d}-change.yaml").write_text(
        yaml.safe_dump(body, sort_keys=False), encoding="utf-8"
    )


def _at_version_two(tmp_path: Path) -> EffectiveRulebook:
    """The shipped Rulebook with the scan layer's rules declared again, at version 2."""
    declared = [
        rule
        for path in sorted(BUILTIN_DIRECTORY.glob("*.yaml"))
        for rule in yaml.safe_load(path.read_text(encoding="utf-8")).get("rules") or ()
        if rule["id"] in _SCAN_RULES
    ]
    path = tmp_path / "04-again.yaml"
    document = {
        "revision": 4,
        "summary": "The scan layer's rules again, at version 2.",
        "rules": [{**rule, "version": "2"} for rule in declared],
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return effective_rulebook((*load_revisions(), load_revision(path)))


# ---------------------------------------------------------------------------
# What ships.
# ---------------------------------------------------------------------------


def test_the_scan_layer_s_rules_ship_provisional_under_the_names_every_run_cites() -> None:
    book = shipped_rulebook()
    for rule_id in _SCAN_RULES:
        rule = book.by_id(rule_id)
        assert rule is not None, rule_id
        assert (rule.version, rule.effect, rule.status, rule.added_revision) == (
            "1",
            RuleEffect.ENUMERATES,
            RuleStatus.PROVISIONAL,
            3,
        )
        assert reads_of(PREDICATES.declaration(rule.predicate)) is Reads.ENUMERATION
    enumerating = {rule.rule_id for rule in book if rule.effect is RuleEffect.ENUMERATES}
    provisional = {rule.rule_id for rule in book if rule.status is RuleStatus.PROVISIONAL}
    assert enumerating == provisional == set(_SCAN_RULES)


def test_the_status_stays_out_of_the_digest() -> None:
    """Confirming a rule changes nothing it concludes, so two runs must not look different."""
    rules = shipped_rulebook().rules
    confirmed = [rule.model_copy(update={"status": RuleStatus.ACTIVE}) for rule in rules]
    assert rulebook_digest(confirmed) == rulebook_digest(rules)


def test_rule_show_says_provisional_where_it_holds_and_nowhere_else() -> None:
    marks: dict[str, str] = {}
    for line in render_rulebook(shipped_rulebook()).splitlines():
        matched = re.match(r"^  (\S+) \(v[^,]+, ([^)]*)\) ", line)
        if matched:
            marks[matched.group(1)] = matched.group(2)
    assert marks["census.filesystem_containment"] == "entailed, provisional"
    assert marks["format_from_extension_unless_contradicted"] == "defeasible"


# ---------------------------------------------------------------------------
# Composing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"disable": "census.filesystem_containment"},
        {"supersede": "cascade.container_member"},
        {"override": "census.object_kind", "parameters": {"kind": "fifo"}},
    ],
)
def test_no_project_revision_switches_off_supersedes_or_tunes_a_provisional_rule(
    tmp_path, change
) -> None:
    """The containment forest and the embedding edges are what the invariants check."""
    directory = tmp_path / "rulebook"
    _project_revision(directory, 1, changes=[change])
    with pytest.raises(RuleDeclarationError, match="provisional"):
        effective_rulebook(load_revisions(), project=load_project_revisions(directory))


def test_enabling_a_provisional_rule_is_permitted_and_changes_nothing(tmp_path) -> None:
    directory = tmp_path / "rulebook"
    _project_revision(directory, 1, changes=[{"enable": "census.object_kind"}])
    book = effective_rulebook(load_revisions(), project=load_project_revisions(directory))
    assert book.digest == shipped_rulebook().digest


def test_a_project_s_own_rule_neither_enumerates_nor_declares_itself_provisional(tmp_path) -> None:
    kind = shipped_rulebook().by_id("census.object_kind")
    assert kind is not None
    enumerating = {
        "id": "object_kind_here",
        "predicate": kind.predicate,
        "effect": "enumerates",
        "tier": "entailed",
        "statement": kind.statement,
        "rationale": kind.rationale,
    }
    _project_revision(tmp_path / "enumerating", 1, rules=[enumerating])
    with pytest.raises(RuleDeclarationError, match="enumerates"):
        load_project_revisions(tmp_path / "enumerating")

    provisional = {
        "id": "format_here",
        "predicate": "format_from_extension",
        "effect": "classifies",
        "status": "provisional",
        "statement": "A file is of kind {theme_kind}.",
        "rationale": "Because the extension says so.",
        "parameters": [{"name": "theme_kind", "type": "string", "default": "format"}],
    }
    _project_revision(tmp_path / "provisional", 1, rules=[provisional])
    with pytest.raises(RuleDeclarationError, match="provisional"):
        load_project_revisions(tmp_path / "provisional")


# ---------------------------------------------------------------------------
# What each rule concludes.
# ---------------------------------------------------------------------------


def test_a_directory_contains_its_listing_and_never_itself() -> None:
    rule = _bound("census.filesystem_containment")
    assert rule.evaluate(Listing(directory="d", entries=("b", "a", "d", "a"))) == ("a", "b")
    assert rule.evaluate(Listing(directory="d")) == ()
    with pytest.raises(TypeError, match="listing"):
        rule.evaluate(("d", "a"))


def test_a_directory_s_totals_add_its_subdirectories_to_its_own_counts() -> None:
    rule = _bound("census.directory_aggregate")
    leaf = rule.evaluate(DirectoryCounts(files=2, directories=0, size_bytes=30))
    assert leaf == DirectoryTotals(2, 0, 2, 0, 30)
    assert isinstance(leaf, DirectoryTotals)
    parent = rule.evaluate(
        DirectoryCounts(files=1, directories=2, size_bytes=5, children=(leaf, leaf))
    )
    assert parent == DirectoryTotals(
        direct_file_count=1,
        direct_directory_count=2,
        recursive_file_count=5,
        recursive_directory_count=2,
        recursive_size_bytes=65,
    )


def test_an_object_s_kind_is_read_off_its_mode_only_where_it_is_not_a_file() -> None:
    rule = _bound("census.object_kind")
    assert rule.evaluate(stat.S_IFIFO) == "fifo"
    assert rule.evaluate(stat.S_IFSOCK) == "socket"
    assert rule.evaluate(stat.S_IFCHR) == "character_device"
    assert rule.evaluate(stat.S_IFBLK) == "block_device"
    assert rule.evaluate(0) == "unknown"
    for mode in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
        assert rule.evaluate(mode | 0o644) is None
    with pytest.raises(TypeError, match="mode"):
        rule.evaluate(True)


def test_an_embedding_names_the_container_s_kind_and_why_expansion_stopped() -> None:
    rule = _bound("cascade.container_member")
    zipped = DerivedStream(locator=(), transformation="zip", transformation_version="1")
    assert rule.evaluate(zipped) == Embedding("archive_member", ExpansionState.COMPLETE)
    bounded = DerivedStream(
        locator=(),
        transformation="gzip",
        transformation_version="1",
        expansion_state=ExpansionState.BOUNDED,
    )
    assert rule.evaluate(bounded) == Embedding("stream_of", ExpansionState.BOUNDED)
    unnamed = DerivedStream(locator=(), transformation="ole", transformation_version="1")
    assert rule.evaluate(unnamed) == Embedding(None, ExpansionState.COMPLETE)
    with pytest.raises(TypeError, match="member"):
        rule.evaluate("zip")


# ---------------------------------------------------------------------------
# Cited as the Rulebook binds them.
# ---------------------------------------------------------------------------


def test_the_census_cites_the_versions_of_the_rulebook_it_is_handed(tmp_path) -> None:
    pool = tmp_path / "pool"
    (pool / "folder").mkdir(parents=True)
    (pool / "folder" / "notes.txt").write_text("x", encoding="utf-8")
    fifo = hasattr(os, "mkfifo")
    if fifo:
        os.mkfifo(pool / "folder" / "pipe")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    book = _at_version_two(tmp_path)

    run_census(project, run_id=_CENSUS_RUN, started_at="t", rulebook=book)

    with open_catalog(project.path) as catalog:
        edges = catalog.query("SELECT DISTINCT rule_id, rule_version FROM assertions").fetchall()
        claims = catalog.query("SELECT DISTINCT rule_id, rule_version FROM claims").fetchall()
        kinds = catalog.query(
            "SELECT DISTINCT observer_version, value_text FROM observations "
            "WHERE key = 'filesystem.object_kind'"
        ).fetchall()
        recorded = catalog.query("SELECT rulebook_hash FROM run").fetchall()
    assert edges == [("census.filesystem_containment", "2")]
    assert claims == [("census.directory_aggregate", "2")]
    assert kinds == ([("2", "fifo")] if fifo else [])
    assert recorded == [(book.digest,)]


def test_a_census_handed_a_rulebook_without_its_rules_stops_before_it_writes(tmp_path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    before_revision_3 = effective_rulebook(load_revisions(), at=2)
    with pytest.raises(RuleDeclarationError, match=re.escape("census.filesystem_containment")):
        run_census(project, run_id=_CENSUS_RUN, started_at="t", rulebook=before_revision_3)
    assert not staging_path(project.path, _CENSUS_RUN).exists()


def test_the_cascade_cites_the_version_of_the_rulebook_it_is_handed(tmp_path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    with zipfile.ZipFile(pool / "bundle.zip", "w") as bundle:
        bundle.writestr("inner/one.txt", "first")
        bundle.writestr("inner/two.txt", "second")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=_CENSUS_RUN, started_at="t")

    run_deepen(
        project,
        run_id=_SECOND_RUN,
        started_at="t",
        budget_seconds=60.0,
        rulebook=_at_version_two(tmp_path),
    )

    with open_catalog(project.path) as catalog:
        edges = catalog.query(
            "SELECT DISTINCT rule_id, rule_version, specialization FROM assertions "
            "WHERE primitive = 'embeds'"
        ).fetchall()
        observed = catalog.query(
            "SELECT DISTINCT observer_version FROM observations WHERE observer = 'cascade'"
        ).fetchall()
    assert edges == [("cascade.container_member", "2", "archive_member")]
    assert observed == [("2",)]


def test_inference_stores_each_rule_s_status_and_never_evaluates_an_enumerating_rule(
    tmp_path,
) -> None:
    pool = tmp_path / "pool"
    (pool / "folder").mkdir(parents=True)
    (pool / "folder" / "notes.txt").write_text("x", encoding="utf-8")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=_CENSUS_RUN, started_at="t")

    run_inference(project, run_id=_SECOND_RUN, started_at="t")

    with open_catalog(project.path) as catalog:
        statuses = dict(catalog.query("SELECT rule_id, status FROM rules").fetchall())
        failed = {
            row[0] for row in catalog.query("SELECT DISTINCT rule_id FROM rule_failures").fetchall()
        }
    assert {rule_id for rule_id, status in statuses.items() if status == "provisional"} == set(
        _SCAN_RULES
    )
    assert statuses["format_from_extension_unless_contradicted"] == "active"
    assert not failed & set(_SCAN_RULES)
