"""Copies by content, containers, content out of reach and no hard file skips, each on a pool.

Copies rest on content, and agreement by metadata is a separate class. A container is its own
class. Content out of reach is raised and skipped. No file is skipped for what it says about
itself. Every name here is invented. The signatures are the formats' own, from their published
specifications, and the shapes come from the grammar of a copy, a container, an empty file and a
note.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.core.schema import Entry
from ingence.core.settings import ExtractionSettings, Settings
from ingence.infer.run import run_inference
from ingence.record.verdicts import spelled_key
from ingence.scan.census import run_census
from ingence.scan.deepen import deepen_entry, run_deepen
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"
INFERENCE = "run_20260101T000003Z_333333333333"

#: The signatures the detection table holds, written as their specifications write them.
HDF5 = b"\x89HDF\r\n\x1a\n"
ZIP = b"PK\x03\x04"
JPEG = b"\xff\xd8\xff"

#: 1 January 2025, before the date that revision 4's unreadable-content rule names.
LONG_AGO = 1_735_689_600


def _project(tmp_path: Path, *, dive: bool = False, profile: str = "standard") -> Project:
    pool = tmp_path / "pool"

    # A copy: one file placed in two folders, byte for byte, with different birth instants.
    for folder in ("kelt", "vorn"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "brisa_trace.csv").write_text("a,b\n1,2\n")
    # The shape of a class that is not a copies class: one name, one size, one
    # modification instant, different bytes, as a tool writes per-parameter files of one
    # architecture.
    for folder in ("tamen-alpha", "tamen-beta"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "quolt_weights.dat").write_bytes(b"1234")
    (pool / "tamen-beta" / "quolt_weights.dat").write_bytes(b"4321")
    for folder in ("kelt", "vorn", "tamen-alpha", "tamen-beta"):
        for name in ("brisa_trace.csv", "quolt_weights.dat"):
            target = pool / folder / name
            if target.exists():
                os.utime(target, (LONG_AGO, LONG_AGO))

    # A container a format is written on, a true contradiction, and a container whose use a
    # laboratory must say.
    (pool / "signatures").mkdir()
    (pool / "signatures" / "pellan_cell.mpr").write_bytes(HDF5 + b"\x00" * 64)
    (pool / "signatures" / "pellan_field.tif").write_bytes(JPEG + b"\x00" * 64)
    (pool / "signatures" / "pellan_regions.zorb").write_bytes(ZIP + b"\x00" * 64)
    # A ZIP under an extension the Dictionary names as a program's own format.
    (pool / "signatures" / "pellan_talk.key").write_bytes(ZIP + b"\x00" * 64)

    # Content out of reach: an empty file, and one empty since before the rule's date.
    (pool / "unreadable").mkdir()
    (pool / "unreadable" / "quolt_placeholder.dat").write_bytes(b"")
    old = pool / "unreadable" / "quolt_archived.dat"
    old.write_bytes(b"")
    os.utime(old, (LONG_AGO, LONG_AGO))

    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    if dive:
        settings = project.settings
        project = project.save_settings(
            settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
        )
    run_census(project, run_id=CENSUS, started_at="t")
    if dive:
        # The profile decides which digest a dive computes. The standard profile samples the
        # head, the tail and the size. The deep profile reads the whole file below its threshold.
        # A dive never computes both, which is why the two copy rules are read on two pools.
        run_deepen(project, run_id=DIVE, started_at="t", profile=profile)
    run_inference(project, run_id=INFERENCE, started_at="t")
    return project


@pytest.fixture
def dived(tmp_path: Path) -> Project:
    """A dive under the deep profile: the whole file below its threshold, which proves identity."""
    return _project(tmp_path / "whole", dive=True, profile="deep")


@pytest.fixture
def sampled(tmp_path: Path) -> Project:
    """A dive under the standard profile's reading: the head, the tail and the size.

    A run never computes both digests: ``quick`` samples, ``full`` reads whole, and one
    definition decides which a file gets. So the sampled copy rule and the whole-file
    identity are read on two pools, which is what "a copy claim needs blake3_quick agreement
    from a dive, whole under the deep profile" means in practice.
    """
    return _project(tmp_path / "sample", dive=True, profile="standard")


def _classes(project: Project, rule: str) -> dict[str, set[str]]:
    """Each class the rule claimed, as its key spelled for a person, with its members' names."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.object_reference, e.name, e.relative_path FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule],
        ).fetchall()
    found: dict[str, set[str]] = {}
    for key, name, path in rows:
        found.setdefault(spelled_key(str(key)), set()).add(f"{path}")
        del name
    return found


def _findings(project: Project, rule: str) -> dict[str, set[str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, f.raised FROM findings f JOIN entries e ON e.entry_id = f.subject "
            "WHERE f.rule_id = ?",
            [rule],
        ).fetchall()
    return {str(name): set(values) for name, values in rows}


# ---------------------------------------------------------------------------
# Copies by content, agreement by metadata.
# ---------------------------------------------------------------------------


def test_a_copy_is_claimed_from_a_digest_and_files_that_merely_agree_are_not(
    dived: Project, sampled: Project
):
    """Birth time is not in the key, and a copy claim rests on content.

    Files can share one name, one size and both instants and still hold different bytes. A digest
    tells copies from such files. Nothing about the filesystem can.
    """
    copied = {"kelt/brisa_trace.csv", "vorn/brisa_trace.csv"}
    not_copies = {"tamen-alpha/quolt_weights.dat", "tamen-beta/quolt_weights.dat"}
    by_sample = _classes(sampled, "copies_by_sampled_digest")
    whole = _classes(dived, "identical_by_content_digest")
    assert copied in by_sample.values(), "a standard dive's sampled digest claims the copy"
    assert copied in whole.values(), "and the deep profile's whole digest proves it"
    # The two files agreeing on every filesystem fact and differing in content are two classes
    # of one member under both digests, so neither claims them as copies.
    for classes in (by_sample, whole):
        assert not any(members & not_copies and len(members) > 1 for members in classes.values())


def test_the_metadata_signal_drops_birth_time_and_stands_at_the_abductive_tier(dived: Project):
    """What a copy-and-paste looks like from outside, and it is not a copy claim."""
    with open_catalog(dived.path) as catalog:
        rows = catalog.query(
            "SELECT DISTINCT r.parameters_json, r.tier, r.version FROM rules r "
            "WHERE r.rule_id = 'same_observed_metadata_outside_programs'"
        ).fetchall()
    ((parameters, tier, version),) = rows
    compared = next(
        json.loads(item["default"])
        for item in json.loads(parameters)
        if item["name"] == "compared_facts"
    )
    assert "created_ns" not in compared
    assert set(compared) == {"name", "size_bytes", "modified_ns", "entry_type"}
    assert (tier, version) == ("abductive", "2")

    # Both pairs agree on name, size and modification time, so both are raised as a signal --
    # which is the point: it narrows what is worth hashing and claims nothing about bytes.
    classes = _classes(dived, "same_observed_metadata_outside_programs")
    grouped = [members for members in classes.values() if len(members) > 1]
    assert {"kelt/brisa_trace.csv", "vorn/brisa_trace.csv"} in grouped
    assert {"tamen-alpha/quolt_weights.dat", "tamen-beta/quolt_weights.dat"} in grouped


# ---------------------------------------------------------------------------
# A container is its own class, and only a contradiction retracts a format.
# ---------------------------------------------------------------------------


def test_a_container_a_format_is_written_on_is_raised_as_one_and_retracts_nothing(dived: Project):
    """A container that a format is written on is not a contradiction.

    Reading such a container as a contradiction would retract format claims that are correct."""
    containers = _findings(dived, "format_built_on_a_container")
    contradictions = _findings(dived, "content_contradicts_extension")

    assert containers["pellan_cell.mpr"] == {"built_on_container:application/x-hdf5"}
    assert "pellan_cell.mpr" not in contradictions

    # A laboratory's ZIP: a program's own format or an archive, and only a person can say.
    assert containers["pellan_regions.zorb"] == {"container_unclear:application/zip"}
    assert "pellan_regions.zorb" not in contradictions

    # Unless the vocabulary already says whose format it is. Then nobody is asked.
    assert containers["pellan_talk.key"] == {"built_on_container:application/zip"}
    assert "pellan_talk.key" not in contradictions

    # A real contradiction is still one, and is still what retracts a format claim.
    assert contradictions["pellan_field.tif"] == {"image/jpeg"}
    assert "pellan_field.tif" not in containers

    with open_catalog(dived.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.resolution_status FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'format_from_extension_unless_contradicted'"
        ).fetchall()
    status = {str(name): str(state) for name, state in rows}
    assert status["pellan_field.tif"] == "examined_conflicting", "a contradiction retracts it"
    assert status.get("brisa_trace.csv") == "resolved", "a container convention retracts nothing"


# ---------------------------------------------------------------------------
# Content out of reach.
# ---------------------------------------------------------------------------


def test_an_empty_file_is_raised_as_empty_and_the_dive_skips_it_saying_why(dived: Project):
    """Such files cannot be scanned deeply or hashed, and the analysis proceeds.

    Skipping them is also what keeps every empty file in a pool out of one digest class.
    """
    raised = _findings(dived, "content_unreadable")
    assert raised["quolt_placeholder.dat"] == {"empty"}
    assert raised["quolt_archived.dat"] == {"empty_and_old"}

    with open_catalog(dived.path) as catalog:
        digests = catalog.query(
            "SELECT count(*) FROM observations o JOIN entries e ON e.entry_id = o.entry_id "
            "WHERE o.key LIKE 'identity.blake3%' AND e.size_bytes = 0"
        ).fetchone()[0]
        outcomes = dict(
            catalog.query(
                "SELECT name, scan_outcome FROM entries WHERE size_bytes = 0 AND "
                "entry_type = 'file'"
            ).fetchall()
        )
    assert digests == 0, "no digest is computed over no bytes"
    assert set(outcomes.values()) == {"skipped_by_policy"}, "a deliberate omission, not a failure"


# ---------------------------------------------------------------------------
# A note in a file's own text is a finding, and nothing is skipped for it.
# ---------------------------------------------------------------------------


def test_a_do_not_analyse_note_in_a_file_is_raised_and_the_file_is_read_anyway(tmp_path: Path):
    """A note in a file can be raised, and Ingence still reads the file.

    Ingence assumes full read access to every file in the directory the user names."""
    pool = tmp_path / "pool"
    pool.mkdir()
    noted = pool / "quolt_trace.csv"
    noted.write_text("a,b\n1,2\n# do-not-analyse: kept for the record\n")
    (pool / "quolt_other.csv").write_text("a,b\n3,4\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    settings = project.settings
    project = project.save_settings(
        settings.model_copy(
            update={
                "extraction": settings.extraction.model_copy(
                    update={"enabled": True, "byte_scan_sections": ("negative_lexicon",)}
                )
            }
        )
    )
    run_census(project, run_id=CENSUS, started_at="t")
    run_deepen(project, run_id=DIVE, started_at="t")
    run_inference(project, run_id=INFERENCE, started_at="t")

    raised = _findings(project, "content_says_do_not_analyse")
    assert raised == {"quolt_trace.csv": {"do-not-analyse"}}
    with open_catalog(project.path) as catalog:
        outcomes = dict(
            catalog.query(
                "SELECT name, scan_outcome FROM entries WHERE entry_type = 'file'"
            ).fetchall()
        )
        read = catalog.query(
            "SELECT count(*) FROM observations o JOIN entries e ON e.entry_id = o.entry_id "
            "WHERE e.name = 'quolt_trace.csv' AND o.source_scope = 'content'"
        ).fetchone()[0]
    assert outcomes["quolt_trace.csv"] == "extracted", "the note is not obeyed"
    assert read > 0, "and its content was read like any other file's"


def test_a_claim_defeated_by_a_flag_that_raises_records_is_published_with_it_fired(tmp_path: Path):
    """A defeater is evaluated the way its own effect is.

    ``content_unreadable`` is a flag, and it answers with a finding (its records and its reason),
    not with a plain value. Read as a classification, it would fail the inference pass with a
    type error on the first entry where it fires. The minimal case is a claim read off a name,
    defeated by a flag that raises, on a file whose content is out of reach.
    """
    import copy

    import yaml

    from fixtures.revision_4 import REVISION
    from ingence.storage.layout import RULEBOOK_DIR

    # A project's own rule: the format rule superseded and declared again under a name of
    # its own, retracted by content out of reach too.
    (declared,) = [
        copy.deepcopy(rule)
        for rule in REVISION["rules"]  # type: ignore[attr-defined]
        if rule["id"] == "format_from_extension_unless_contradicted"
    ]
    declared["id"] = "format_unless_contradicted_or_unreadable"
    declared["defeated_by"] = ["content_contradicts_extension", "content_unreadable"]
    declared["statement"] += " Content out of reach retracts it too."

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "quolt_placeholder.tif").write_bytes(b"")
    (pool / "quolt_field.tif").write_bytes(b"II*\x00" + b"\x00" * 64)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    directory = project.path / RULEBOOK_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "project-0001.yaml").write_text(
        yaml.safe_dump(
            {
                "project_revision": 1,
                "summary": "ines: content out of reach retracts a format too",
                "changes": [{"supersede": "format_from_extension_unless_contradicted"}],
                "rules": [declared],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFERENCE, started_at="t")

    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.resolution_status, c.defeaters_json FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'format_unless_contradicted_or_unreadable'"
        ).fetchall()
    found = {str(name): (str(status), json.loads(defeaters)) for name, status, defeaters in rows}
    empty = found["quolt_placeholder.tif"]
    assert empty[0] == "examined_conflicting"
    assert {item["rule_id"]: item["fired"] for item in empty[1]}["content_unreadable"] is True
    # And the reason the flag raised travels with the defeat, which is why it raises one.
    ((unreadable,),) = [(item,) for item in empty[1] if item["rule_id"] == "content_unreadable"]
    assert "cannot be read" in unreadable["reason"]
    assert found["quolt_field.tif"][0] == "resolved"


def test_the_dive_says_why_it_skipped_an_unreadable_file_on_the_entry_itself(tmp_path: Path):
    """Per entry, in the dive's own outcome, which is what the journal row carries."""
    pool = tmp_path / "pool"
    pool.mkdir()
    empty = pool / "quolt_placeholder.dat"
    empty.write_bytes(b"")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    with open_catalog(project.path) as catalog:
        cursor = catalog.query("SELECT * FROM entries WHERE name = ?", [empty.name])
        columns = [column[0] for column in cursor.description]
        row = dict(zip(columns, cursor.fetchone(), strict=True))

    entry = record_from_row(row, Entry)
    outcome = deepen_entry(entry, empty, run_id=DIVE, observed_at="t", settings=Settings())
    assert outcome.outcome.value == "skipped_by_policy"
    assert outcome.stages == []
    assert any("holds no bytes" in message for _, message in outcome.warnings)
    assert outcome.observations == []
