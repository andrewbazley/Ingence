"""Claim samples whose value is each file's own are grouped, told, and never waiting.

A person facing thousands of questions such as "Earliest credible time?", one per file, cannot
say why one time preceded another, and times are most useful relative to each other. The tests
check these rules:

- a rule whose values are nearly one per file is recognised by the shape of what it concluded,
  not by its name, and is not sampled value by value;
- its conclusions are grouped by the parts of their values the files share (a time's
  ``source``), examples to a group sized as a value's sample is, spread over
  folders, each group said in plain words;
- a group is information: no row of it waits for an answer, it counts toward nothing remaining,
  and an example can still be answered, which leaves the progress where it was;
- a rule with few values is sampled per value.

Every name is invented, and the times are set on the files the test writes.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.plain_words import PLAIN_WORDS, plain_words, value_sentence
from ingence.api.routes import pages
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

RULE = "earliest_credible_time"
FOLDERS = ("kelt", "quolt", "zeph", "brisa")
#: 2021-01-01: every file is dated before the day it is written, an hour apart from the next.
LONG_AGO = 1_609_459_200


@pytest.fixture(scope="module")
def dated(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """Forty files in four folders, each last changed at a time of its own."""
    base = tmp_path_factory.mktemp("claim-groups")
    pool = base / "pool"
    for index in range(40):
        folder = pool / FOLDERS[index % len(FOLDERS)]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"vorn_trace_{index:02d}.csv"
        path.write_text("a,b\n1,2\n")
        moment = LONG_AGO + 3600 * index
        os.utime(path, (moment, moment))
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    # Stamped after the files were written, as a real run is: a filesystem that cannot set a
    # birth back (Windows) records today's, and a run stamped before it would observe a file
    # "born after the run", which the rule rightly sets aside.
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_census(project, run_id=f"run_{stamp}_111111111111", started_at="t")
    run_inference(project, run_id=f"run_{stamp}_222222222222", started_at="t")
    return project


def claims_page(project: Project, **filters: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return pages.page(catalog, project.path, "claims", **filters)


def concluded(project: Project) -> int:
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT count(*) FROM claims WHERE rule_id = ? AND resolution_status = 'resolved'",
            [RULE],
        ).fetchone()
    return int(row[0]) if row else 0


def test_a_rule_whose_values_are_each_file_s_own_is_grouped_and_not_sampled(dated) -> None:
    shown = claims_page(dated)
    assert concluded(dated) == 40
    assert not [row for row in shown["rows"] if row["rule_id"] == RULE]
    groups = [group for group in shown["informational"] if group["rule_id"] == RULE]
    assert groups, "the per-file conclusions are told as groups"
    assert sum(group["files"] for group in groups) == 40
    for group in groups:
        # Grouped by the part the files share, and never by the part each has its own of.
        assert set(group["shared"]) == {"source"}
        assert group["differs"] == ["at"]
        assert 1 <= len(group["examples"]) <= pages.CLAIMS_AT_MOST
        assert group["explained"]
        known = value_sentence(group["shared"])
        if known is not None:
            assert group["explained"] == known
        for example in group["examples"]:
            assert example["target"].startswith("claim:")
            assert set(example["differing"]) == {"at"}
            assert example["grouped_by"] == group["shared"]
            assert example["in_force"] is None
    # One example from each folder before a second from any, as every sample spreads.
    largest = max(groups, key=lambda group: group["files"])
    folders = [example["folder"] for example in largest["examples"]]
    assert len(set(folders)) == min(len(folders), len(FOLDERS))


def test_a_group_waits_for_nothing_and_an_example_can_still_be_answered(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    for index in range(36):
        path = pool / FOLDERS[index % 2] / f"brisa_scan_{index:02d}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a\n")
        os.utime(path, (LONG_AGO + 60 * index, LONG_AGO + 60 * index))
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    before = claims_page(project)
    # Only the rows are waiting; the groups beside them count toward nothing.
    assert before["progress"]["rows"] == len(before["rows"]) + before["hidden_answered"]
    example = before["informational"][0]["examples"][0]
    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog, project.path)
        decide_target(
            context,
            Decision(target=example["target"], word="correct"),
            actor="robin",
            occurred_at="2026-09-24T12:00:00Z",
        )
    after = claims_page(project)
    assert after["progress"] == before["progress"]
    answered = {
        item["target"]: item for group in after["informational"] for item in group["examples"]
    }
    assert answered[example["target"]]["in_force"]["word"] == "correct"
    # The rule filter keeps the groups of that rule and no rows.
    only = claims_page(project, rule=RULE)
    assert only["rows"] == [] and only["informational"]
    assert {"value": RULE, "n": len(only["informational"])} in only["filters"]["rules"]


def test_a_rule_with_few_values_is_still_sampled_value_by_value(dated) -> None:
    shown = claims_page(dated)
    formats = [row for row in shown["rows"] if "format" in row["rule_id"]]
    # One format over forty files, a quarter of them in each of four folders: one file from
    # each folder, since each holds at least a tenth (a fixed three would hide one).
    assert sorted(row["folder"] for row in formats) == sorted(FOLDERS)
    assert {row["shown_of_value"] for row in formats} == {len(FOLDERS)}
    assert not [group for group in shown["informational"] if "format" in group["rule_id"]]


def test_every_source_a_time_names_is_said_in_plain_words() -> None:
    """The five sources ``earliest_credible_time`` names, each explained, and one it does not
    name explained by the page's own sentence rather than by nothing."""
    for source in (
        "instrument_clock",
        "document_clock",
        "birth",
        "modified_before_born",
        "modification",
    ):
        assert value_sentence({"source": source}), source
    assert value_sentence({"source": "modified_before_born"}) == (
        "These files say they were created after their last change. This usually means they were "
        "copied: a copy gets a new creation time and keeps the old change time. So Ingence takes "
        "the change time as the earliest time each file is known to have existed."
    )
    assert value_sentence({"source": "a_source_nobody_named"}) is None
    assert value_sentence({}) is None
    said = pages._unexplained({"source": "a_source_nobody_named"}, ["at"])
    assert said.startswith("These files agree on source a source nobody named. Each has its own at")
    assert "value of its own" in pages._unexplained({}, [])


def test_the_plain_words_travel_with_the_installed_engine() -> None:
    """A wheel that dropped the table would show every rule by its statement and every group by
    the page's own sentence, silently."""
    from pathlib import Path

    assert PLAIN_WORDS.is_file()
    manifest = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert "src/ingence/api/*.yaml" in manifest
    assert plain_words().values["source"]
