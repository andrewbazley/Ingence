"""The census: what it records, what it refuses to do, and its two guarantees.

Two clean scans of an unchanged tree produce the same output, and an interruption at any point
leaves a valid project. Both are tested here.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from fixtures.laboratory_pool import write_pool
from ingence.core.ids import Scope, ScopeKind, entry_id
from ingence.core.invariants import CLOSED_WORLD_PREDICATE_PREFIX
from ingence.core.project import Project
from ingence.core.relations import Primitive
from ingence.core.schema import EntryType, RecognisedAs, ReuseDecision, SymlinkState
from ingence.core.settings import DictionarySettings, ScanSettings, Settings
from ingence.core.tiers import InferenceTier, ResolutionStatus
from ingence.dictionary.store import DictionaryStore
from ingence.scan.cancel import CancellationToken
from ingence.scan.census import (
    compound_extension,
    extension_of,
    is_extension,
    run_census,
    walk_root,
)
from ingence.scan.recognise import installation_directories
from ingence.storage.catalog import compare_runs, describe_differences, open_catalog
from ingence.storage.commit import read_pointer, resolve_current_run, staging_path


def build_pool(base):
    """A small tree with the shapes that matter: nesting, sizes, an odd name."""
    pool = base / "pool"
    (pool / "exp1" / "raw").mkdir(parents=True)
    (pool / "exp1" / "analysis").mkdir(parents=True)
    (pool / "exp2").mkdir(parents=True)
    (pool / "exp1" / "raw" / "m1.tif").write_bytes(b"a" * 100)
    (pool / "exp1" / "raw" / "m2.tif").write_bytes(b"b" * 200)
    (pool / "exp1" / "analysis" / "result.csv").write_bytes(b"c" * 50)
    (pool / "exp2" / "notes.txt").write_bytes(b"d" * 10)
    (pool / "archive.tar.gz").write_bytes(b"e" * 5)
    return pool


def make_project(base, **settings_kwargs):
    pool = build_pool(base)
    settings = Settings(**settings_kwargs) if settings_kwargs else Settings()
    return Project.create(
        base / "project", created_at="2026-01-01T00:00:00Z", roots=[pool], settings=settings
    ), pool


def records_of(project):
    entries, assertions, claims, errors, observations = [], [], [], [], []
    for chunk in walk_root(project.identity.roots[0], settings=project.settings, run_id="run_test"):
        entries += chunk.entries
        assertions += chunk.assertions
        claims += chunk.claims
        errors += chunk.errors
        observations += chunk.observations
    return entries, assertions, claims, errors, observations


# ---------------------------------------------------------------------------
# What it records.
# ---------------------------------------------------------------------------


def test_every_filesystem_object_becomes_an_entry(tmp_path):
    project, _ = make_project(tmp_path)
    entries, _, _, _, _ = records_of(project)
    kinds = {entry.entry_type for entry in entries}
    assert kinds == {EntryType.DIRECTORY, EntryType.FILE}
    assert len(entries) == 10  # five directories, five files


def test_the_root_itself_is_an_entry_with_no_parent(tmp_path):
    project, _ = make_project(tmp_path)
    entries, _, _, _, _ = records_of(project)
    root = next(entry for entry in entries if entry.relative_path == ".")
    assert root.parent_entry_id is None
    assert root.depth == 0


def test_containment_edges_form_a_forest_over_every_entry_but_the_root(tmp_path):
    project, _ = make_project(tmp_path)
    entries, assertions, _, _, _ = records_of(project)
    assert len(assertions) == len(entries) - 1
    assert {assertion.primitive for assertion in assertions} == {Primitive.CONTAINS}
    targets = [assertion.target_id for assertion in assertions]
    assert len(targets) == len(set(targets)), "a child with two parents is not a forest"


def test_containment_is_entailed_and_names_its_rule(tmp_path):
    """Every containment edge is entailed and names its rule, from the very first edge drawn."""
    project, _ = make_project(tmp_path)
    _, assertions, _, _, _ = records_of(project)
    for assertion in assertions:
        assert assertion.inference_tier is InferenceTier.ENTAILED
        assert assertion.rule_id == "census.filesystem_containment"
        assert assertion.rule_version
        assert assertion.specialization == "directory_of"


def test_a_census_marks_content_questions_unexamined_not_null(tmp_path):
    """Three absence states, and a census has only reached the first."""
    project, _ = make_project(tmp_path)
    entries, _, _, _, _ = records_of(project)
    assert {entry.resolution_status for entry in entries} == {ResolutionStatus.UNEXAMINED}


def test_creation_time_records_where_it_came_from(tmp_path):
    """A time whose provenance is unrecorded cannot be reasoned about."""
    project, _ = make_project(tmp_path)
    entries, _, _, _, _ = records_of(project)
    assert all(entry.created_time_source is not None for entry in entries)


def test_a_compound_extension_is_kept_alongside_the_final_one(tmp_path):
    project, _ = make_project(tmp_path)
    entries, _, _, _, _ = records_of(project)
    archive = next(entry for entry in entries if entry.name == "archive.tar.gz")
    assert archive.extension == "gz"
    assert archive.compound_extension == "tar.gz"


def test_compound_extension_is_bounded():
    """Unbounded, a name of many dots produces a long meaningless suffix."""
    assert compound_extension("a.b.c.d.e") == ".d.e"
    assert compound_extension("plain.txt") == ""
    assert compound_extension("noextension") == ""


def test_a_version_or_a_parameter_is_not_an_extension(tmp_path):
    """A version or a parameter after a dot is not a file extension.

    A tool at ``…2.0`` has no extension ``0``, an interpreter at ``…3.7`` has no ``7``, a
    compiler named ``…13.4.0-ar`` has no ``0-ar``, and a chromatogram written ``…framegap=20``
    has no ``framegap=20``. Read as extensions, each would be looked up as a format and put to a
    person as a question.

    An extension is letters and digits and never digits alone. Written over that grammar, with
    invented names."""
    assert is_extension("tif") and is_extension("7z") and is_extension("mp4")
    for suffix in ("0", "20", "001", "0-ar", "framegap=20", "1_N_True", " 3", "docx~", ""):
        assert not is_extension(suffix), suffix

    for name, expected in (
        ("vorntool2.0", ("", "")),
        ("kelt3.7", ("", "")),
        ("x86_64-apple-vorn13.4.0-ar", ("", "")),
        ("trace.framegap=20", ("", "")),
        ("Chrom.1_N_True", ("", "")),
        ("plate.tif", ("tif", "")),
        ("archive.tar.gz", ("gz", "tar.gz")),
        ("zorbex.001", ("", "")),
    ):
        assert extension_of(name) == expected, name

    # And the census stores what the rule decides.
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "vorntool2.0").write_text("x")
    (pool / "plate.tif").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    entries, _, _, _, _ = records_of(project)
    stored = {entry.name: entry.extension for entry in entries if entry.entry_type.value == "file"}
    assert stored == {"vorntool2.0": "", "plate.tif": "tif"}


def test_a_value_set_after_the_first_check_is_checked_as_it_is_stored(tmp_path, monkeypatch):
    """Reading a name dates an entry after its stat facts were checked.

    A time the name gave that the Index cannot store is one entry's recorded error. The entry is
    indexed without it, and the census still publishes.
    """
    from ingence.scan import census as census_module

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate_7.tif").write_text("x")
    (pool / "notes.txt").write_text("x")
    real = census_module._Walker.read_name_of

    def dated_too_late(self, entry, records):
        named = real(self, entry, records)
        if named.name == "plate_7.tif":
            return named.model_copy(update={"event_time_ns": 1 << 64})
        return named

    monkeypatch.setattr(census_module._Walker, "read_name_of", dated_too_late)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    entries, _, _, _, _ = records_of(project)
    stored = {
        entry.name: entry.event_time_ns for entry in entries if entry.entry_type.value == "file"
    }
    assert stored == {"plate_7.tif": None, "notes.txt": None}
    with open_catalog(project.path) as catalog:
        errors = catalog.query(
            "SELECT operation, error_type FROM scan_errors WHERE relative_path = 'plate_7.tif'"
        ).fetchall()
    assert errors == [("stat.event_time_ns", "OutOfRange")]


def test_the_root_is_checked_as_it_is_stored_like_every_other_entry(tmp_path, monkeypatch):
    """The root's own entry is range-checked too.

    Without the check that every other entry has, a value that only the root held would stop the
    census."""
    from ingence.scan import census as census_module

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate_7.tif").write_text("x")
    real = census_module._Walker.read_name_of

    def root_dated_too_late(self, entry, records):
        named = real(self, entry, records)
        if named.relative_path == ".":
            return named.model_copy(update={"event_time_ns": 1 << 64})
        return named

    monkeypatch.setattr(census_module._Walker, "read_name_of", root_dated_too_late)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    with open_catalog(project.path) as catalog:
        errors = catalog.query(
            "SELECT relative_path, operation, error_type FROM scan_errors"
        ).fetchall()
    assert errors == [(".", "stat.event_time_ns", "OutOfRange")]


def test_a_device_number_above_the_signed_range_is_stored_as_its_signed_bits(tmp_path, monkeypatch):
    """Some Windows volumes give a serial number at or above 2**63.

    The census stores it as the signed reading of the same bits, with no error, and a rebind of
    it lands to the digit."""
    from ingence.scan import census as census_module

    serial = (1 << 63) + 7
    assert census_module.device_as_stored(serial) == serial - (1 << 64)
    assert census_module.device_as_stored(905) == 905
    assert census_module.device_as_stored(-3) == -3

    class Renumbered:
        def __init__(self, result: object) -> None:
            self._result = result

        def __getattr__(self, name: str) -> object:
            return serial if name == "st_dev" else getattr(self._result, name)

    real = census_module._Walker.build_entry

    def build_on_a_high_serial(self, *, result, **kwargs):
        return real(self, result=Renumbered(result), **kwargs)

    pool = tmp_path / "pool"
    (pool / "sub").mkdir(parents=True)
    (pool / "sub" / "plate_7.tif").write_text("x")
    monkeypatch.setattr(census_module._Walker, "build_entry", build_on_a_high_serial)
    monkeypatch.setattr(census_module._Walker, "crosses_filesystem", lambda *a, **k: False)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    entries, _, _, _, _ = records_of(project)
    assert {entry.device for entry in entries} == {serial - (1 << 64)}
    with open_catalog(project.path) as catalog:
        assert catalog.query("SELECT count(*) FROM scan_errors").fetchone()[0] == 0


def test_a_compound_the_dictionary_declares_is_admitted_whatever_its_left_half(tmp_path):
    """A format spelled in two parts compounds because the vocabulary says so.

    The both-halves rule still decides everything nobody declared, so a left half that
    is punctuation stays refused until a pack names the whole spelling.
    """
    assert extension_of("trace.q_r.tif") == ("tif", "")
    assert extension_of("trace.q_r.tif", {"q_r.tif"}) == ("tif", "q_r.tif")
    # The last part must still be an extension: a declaration only adds to the last dot.
    assert extension_of("trace.q_r.2", {"q_r.2"}) == ("", "")
    assert extension_of("plate.ome.tif", {"ome.tif"}) == ("tif", "ome.tif")


def test_an_ome_tiff_is_stored_as_its_compound_and_is_still_a_tiff(tmp_path):
    """``ome.tif`` is a compound extension meaning a tif file.

    The TIFF claim stays. The extension a file stores is still ``tif``, and the format read from
    the extension slot is the one a plain ``.tif`` gets, because a profile of TIFF that
    contradicted its own base format would lose information.
    """
    from ingence.dictionary.file_types import declared_compounds
    from ingence.infer.run import run_inference

    assert {"ome.tif", "ome.tiff"} <= declared_compounds(DictionaryStore.builtin())
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "zorn_plane.ome.tif").write_bytes(b"x")
    (pool / "zorn_plate.tif").write_bytes(b"x")
    (pool / "zorn_stack.OME.TIFF").write_bytes(b"x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        stored = {
            str(name): (str(extension), str(compound))
            for name, extension, compound in catalog.query(
                "SELECT name, extension, compound_extension FROM entries WHERE entry_type = 'file'"
            ).fetchall()
        }
        claimed = dict(
            catalog.query(
                "SELECT e.name, c.object_reference FROM claims c JOIN entries e "
                "ON e.entry_id = c.subject WHERE c.rule_id LIKE 'format_from_extension%' "
                "AND c.resolution_status = 'resolved'"
            ).fetchall()
        )
    assert stored == {
        "zorn_plane.ome.tif": ("tif", "ome.tif"),
        "zorn_plate.tif": ("tif", ""),
        "zorn_stack.OME.TIFF": ("tiff", "ome.tiff"),
    }
    assert claimed["zorn_plane.ome.tif"] == claimed["zorn_plate.tif"]
    assert claimed["zorn_stack.OME.TIFF"] == claimed["zorn_plate.tif"]


# ---------------------------------------------------------------------------
# Directory aggregates.
# ---------------------------------------------------------------------------


def test_directory_aggregates_roll_up_correctly(tmp_path):
    project, _ = make_project(tmp_path)
    entries, _, claims, _, _ = records_of(project)
    by_identifier = {entry.entry_id: entry.relative_path for entry in entries}
    values = {
        (by_identifier[claim.subject], claim.predicate.split(".", 1)[1]): claim.object_reference
        for claim in claims
    }
    assert values[(".", "recursive_file_count")] == "5"
    assert values[(".", "direct_file_count")] == "1"
    assert values[(".", "direct_directory_count")] == "2"
    assert values[(".", "recursive_directory_count")] == "4"
    assert values[(".", "recursive_size_bytes")] == "365"
    assert values[("exp1", "recursive_size_bytes")] == "350"
    assert values[("exp1/raw", "recursive_file_count")] == "2"


def test_aggregates_are_closed_world_statements_and_are_marked_as_such(tmp_path):
    """Legitimate only because containment is observationally complete."""
    project, _ = make_project(tmp_path)
    _, _, claims, _, _ = records_of(project)
    assert claims
    for claim in claims:
        assert claim.predicate.startswith(CLOSED_WORLD_PREDICATE_PREFIX)
        assert claim.inference_tier is InferenceTier.ENTAILED


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission bits do not deny the owner here",
)
def test_a_folder_the_census_cannot_enter_is_given_no_closed_world_count(tmp_path):
    """Unexamined is not zero: an unfinished listing licenses no count, here or above it.

    Without this rule, a folder that the account could not enter would be stated, entailed, to
    hold nothing, and every total above it would state the part that was seen as the whole.
    """
    project, pool = make_project(tmp_path)
    locked = pool / "exp1" / "locked"
    locked.mkdir()
    (locked / "unseen.csv").write_text("x")
    locked.chmod(0o000)
    try:
        entries, _, claims, errors, _ = records_of(project)
    finally:
        locked.chmod(0o755)
    assert [error.relative_path for error in errors if error.operation == "scandir"] == [
        "exp1/locked"
    ]
    by_identifier = {entry.entry_id: entry.relative_path for entry in entries}
    stated: dict[str, set[str]] = {}
    for claim in claims:
        folder = by_identifier[claim.subject]
        stated.setdefault(folder, set()).add(claim.predicate.split(".", 1)[1])
    direct = {"direct_file_count", "direct_directory_count"}
    beneath = {"recursive_file_count", "recursive_directory_count", "recursive_size_bytes"}
    assert "exp1/locked" not in stated, "a folder never listed holds nothing anybody saw"
    assert stated["exp1"] == direct, "its own listing finished; what is beneath it did not"
    assert stated["."] == direct
    assert stated["exp2"] == direct | beneath, "a finished branch keeps every count"
    assert stated["exp1/raw"] == direct | beneath


# ---------------------------------------------------------------------------
# What it refuses to do.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlink_is_recorded_and_never_followed(tmp_path):
    """Following turns a tree into a graph and can turn a walk into a hang."""
    project, pool = make_project(tmp_path)
    (pool / "loop").symlink_to(pool, target_is_directory=True)
    entries, _, _, _, _ = records_of(project)
    link = next(entry for entry in entries if entry.name == "loop")
    assert link.entry_type is EntryType.SYMLINK
    assert link.symlink_target is not None
    assert not any(entry.relative_path.startswith("loop/") for entry in entries)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlink_says_whether_its_target_is_there_without_going_to_look(tmp_path):
    """A link is resolved, never traversed.

    The census resolves symlinks and anticipates failure rather than raising an error. The answer
    to keep is whether the thing the link names is there, and that costs one ``stat``. Walking
    into the link would break the containment forest, so no entry under a link appears, however
    the link resolves.
    """
    project, pool = make_project(tmp_path)
    (pool / "real.csv").write_text("a,b\n")
    (pool / "resolving").symlink_to(pool / "real.csv")
    (pool / "dangling").symlink_to(pool / "never_existed.csv")
    (pool / "self_referential").symlink_to(pool / "self_referential")
    (pool / "into_a_folder").symlink_to(pool, target_is_directory=True)

    entries, _, _, errors, _ = records_of(project)
    state = {entry.name: entry.symlink_state for entry in entries}
    assert state["resolving"] is SymlinkState.RESOLVES
    assert state["dangling"] is SymlinkState.BROKEN
    assert state["self_referential"] is SymlinkState.UNREADABLE
    assert state["real.csv"] is SymlinkState.NOT_A_LINK

    # Resolving is not following: nothing under the link is indexed.
    assert not any(entry.relative_path.startswith("into_a_folder/") for entry in entries)
    # And none of it is an error. A link whose target was archived is the pool being itself.
    assert [error for error in errors if "dangling" in error.relative_path] == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_broken_link_and_an_unanswerable_one_are_not_the_same_state(tmp_path):
    """For a link, absent and unexamined are different states.

    Saying ``broken`` where the question was never answered asserts an absence nobody observed,
    and a replicate standing behind an unreadable link would be silently written off.
    """
    project, pool = make_project(tmp_path, scan=ScanSettings(resolve_symlinks=False))
    (pool / "real.csv").write_text("a,b\n")
    (pool / "dangling").symlink_to(pool / "never_existed.csv")

    entries, _, _, _, _ = records_of(project)
    state = {entry.name: entry.symlink_state for entry in entries}
    # With resolution off, the link is not asserted broken -- it is unexamined.
    assert state["dangling"] is SymlinkState.UNREADABLE
    assert state["real.csv"] is SymlinkState.NOT_A_LINK


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_link_whose_target_lies_outside_the_root_is_never_stat_ed(tmp_path, monkeypatch):
    """A census reads nothing outside the roots it was given.

    On a shared server an archived file can be a link to another server's
    path written in Windows' object namespace (smbfs shows ``\\??\\UNC\\...`` as
    ``/??/UNC/...``). Resolving such a link would ``stat`` a path outside the named folder, and
    calling it broken would assert an absence nobody observed. Its place is read from its text.
    """
    project, pool = make_project(tmp_path)
    elsewhere = tmp_path / "elsewhere.csv"
    elsewhere.write_text("a,b\n")
    (pool / "inside").symlink_to(pool / "exp2" / "notes.txt")
    (pool / "escaping").symlink_to("../elsewhere.csv")
    (pool / "absolute").symlink_to(elsewhere)
    (pool / "archived.nd2").symlink_to("/??/UNC/archive/share/archived.nd2")
    (pool / "drive.nd2").symlink_to("C:\\data\\drive.nd2")

    asked: list[str] = []
    real_stat = os.stat

    def recording(path, *args, **kwargs):
        if kwargs.get("follow_symlinks", True):
            asked.append(os.fspath(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr("ingence.scan.census.os.stat", recording)
    entries, _, _, errors, _ = records_of(project)
    state = {entry.name: entry.symlink_state for entry in entries}
    assert state["inside"] is SymlinkState.RESOLVES
    for name in ("escaping", "absolute", "archived.nd2", "drive.nd2"):
        assert state[name] is SymlinkState.OUTSIDE_ROOT, name
    # No link is stat-ed through: a link inside the root is resolved a hop at a time with lstat
    # (tests/test_link_chains.py), and none leaving the root is looked at.
    followed = {Path(path).name for path in asked if Path(path).parent == pool}
    assert followed.isdisjoint({"inside", "escaping", "absolute", "archived.nd2", "drive.nd2"})
    assert errors == []


@pytest.mark.parametrize(
    ("windows", "root", "link", "target", "outside"),
    [
        # POSIX: another machine's spellings are outside, a path under the root is not.
        (False, "/share/root", "/share/root/a/x.nd2", "/??/UNC/archive/x.nd2", True),
        (False, "/share/root", "/share/root/a/x.nd2", "C:\\data\\x.nd2", True),
        (False, "/share/root", "/share/root/a/x.nd2", "../b/y.nd2", False),
        (False, "/share/root", "/share/root/a/x.nd2", "../../other/y.nd2", True),
        (False, "/share/root", "/share/root/a/x.nd2", "/share/root/c/z.nd2", False),
        # Windows: a drive path is native, the prefixes come off, another server stays outside.
        (True, "C:\\pool", "C:\\pool\\a\\x.nd2", "C:\\pool\\b\\y.nd2", False),
        (True, "C:\\pool", "C:\\pool\\a\\x.nd2", "\\\\?\\C:\\pool\\b\\y.nd2", False),
        (True, "C:\\pool", "C:\\pool\\a\\x.nd2", "\\??\\C:\\pool\\b\\y.nd2", False),
        (True, "C:\\pool", "C:\\pool\\a\\x.nd2", "c:\\POOL\\b\\y.nd2", False),
        (True, "C:\\pool", "C:\\pool\\a\\x.nd2", "..\\b\\y.nd2", False),
        (True, "C:\\pool", "C:\\pool\\a\\x.nd2", "D:\\elsewhere\\y.nd2", True),
        (True, "C:\\pool", "C:\\pool\\a\\x.nd2", "\\??\\UNC\\archive\\x.nd2", True),
        (True, "C:\\pool", "C:\\pool\\a\\x.nd2", "..\\..\\y.nd2", True),
    ],
)
def test_a_link_target_is_placed_by_the_path_rules_of_the_platform_reading_it(
    windows, root, link, target, outside
):
    """On Windows, a native drive path is not read as another machine's."""
    from ingence.scan.census import outside_root

    assert outside_root(link, target, root, case_insensitive=True, windows=windows) is outside


def test_an_unreadable_directory_is_recorded_and_the_scan_continues(tmp_path):
    """One unreadable directory must never fail a scan."""
    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("permission bits do not deny the owner here")
    project, pool = make_project(tmp_path)
    locked = pool / "locked"
    locked.mkdir()
    (locked / "hidden.txt").write_text("x")
    locked.chmod(0o000)
    try:
        entries, _, _, errors, _ = records_of(project)
    finally:
        locked.chmod(0o755)
    assert any(error.operation == "scandir" for error in errors)
    assert len(entries) >= 9, "the rest of the tree was still walked"


def test_hidden_entries_are_excluded_by_default_and_included_on_request(tmp_path):
    project, pool = make_project(tmp_path)
    (pool / ".DS_Store").write_text("junk")
    entries, _, _, _, _ = records_of(project)
    assert not any(entry.name == ".DS_Store" for entry in entries)

    loud = Project.create(
        tmp_path / "loud",
        created_at="t",
        roots=[pool],
        settings=Settings(scan=ScanSettings(include_hidden=True)),
    )
    entries, _, _, _, _ = records_of(loud)
    assert any(entry.name == ".DS_Store" for entry in entries)


def test_exclusion_excludes_a_name_at_any_depth(tmp_path):
    """A bare name must exclude that name wherever it appears, top level included.

    The default patterns once used shell-style ``**`` recursion, which fnmatch
    does not provide, so a top-level excluded directory was silently walked.
    """
    pool = build_pool(tmp_path)
    for location in (pool / "node_modules", pool / "exp1" / "node_modules"):
        (location / "junk").mkdir(parents=True)
        (location / "junk" / "a.js").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    entries, _, _, _, _ = records_of(project)
    assert not any("node_modules" in entry.relative_path for entry in entries)


def test_an_excluded_directory_is_not_descended_into(tmp_path):
    """Excluding a name must cost nothing for everything beneath it."""
    pool = build_pool(tmp_path)
    deep = pool / "__pycache__" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "buried.pyc").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    entries, _, _, _, _ = records_of(project)
    assert not any("__pycache__" in entry.relative_path for entry in entries)


def test_what_the_census_leaves_out_is_counted_where_it_was_left_out(tmp_path):
    """The census says which part of a drive it never saw.

    Counted per reason, pattern and type, at the point of exclusion and never beneath
    it: an excluded folder is one name, and nothing inside it is indexed or counted.
    """
    from ingence.core.schema import ExclusionReason

    pool = build_pool(tmp_path)
    (pool / ".DS_Store").write_text("junk")
    (pool / "exp1" / ".hidden_notes.txt").write_text("x")
    (pool / ".cache" / "deeper").mkdir(parents=True)
    (pool / ".cache" / "deeper" / "a.bin").write_text("x")
    (pool / "node_modules" / "pkg").mkdir(parents=True)
    (pool / "node_modules" / "pkg" / "index.js").write_text("x")
    (pool / "exp2" / "__pycache__").mkdir()
    (pool / "exp2" / "__pycache__" / "m.pyc").write_text("x")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(scan=ScanSettings(max_depth=2)),
    )
    report = run_census(project, run_id=FIRST_RUN, started_at="t")

    left_out = {
        (item.reason, item.pattern, item.entry_type): item.count
        for item in report_exclusions(project)
    }
    assert left_out == {
        (ExclusionReason.HIDDEN, None, EntryType.FILE): 2,
        (ExclusionReason.HIDDEN, None, EntryType.DIRECTORY): 1,
        (ExclusionReason.EXCLUDE_GLOB, "__pycache__", EntryType.DIRECTORY): 1,
        (ExclusionReason.EXCLUDE_GLOB, "node_modules", EntryType.DIRECTORY): 1,
        # exp1/raw/m1.tif, exp1/raw/m2.tif and exp1/analysis/result.csv sit at depth three.
        (ExclusionReason.MAX_DEPTH, None, EntryType.FILE): 3,
    }
    # Nothing that was left out is in the Index, and nothing beneath a folder was walked.
    with open_catalog(project.path) as catalog:
        names = {row[0] for row in catalog.query("SELECT name FROM entries").fetchall()}
    assert not names & {".DS_Store", ".hidden_notes.txt", ".cache", "a.bin", "index.js", "m.pyc"}
    assert report.published


def report_exclusions(project):
    from ingence.core.schema import ExcludedNames

    with open_catalog(project.path) as catalog:
        (value,) = catalog.query("SELECT excluded_json FROM run").fetchone()
    return [ExcludedNames.model_validate(item) for item in json.loads(value)]


def test_a_census_that_leaves_nothing_out_says_so_with_an_empty_record(tmp_path):
    project, _ = make_project(tmp_path)
    run_census(project, run_id=FIRST_RUN, started_at="t")
    assert report_exclusions(project) == []


def test_every_integer_is_checked_against_its_column_before_a_flush():
    """Every integer is checked against its column's range before a flush.

    An inode is unsigned, so a real inode up to ``2**64 - 1`` fits. A value outside its column's
    range is reported with the column's name."""
    from ingence.core.schema import Entry
    from ingence.storage.layout import integer_range_violations, table_for

    spec = table_for(Entry)

    def entry(**fields):
        return Entry(
            entry_id="entry_x",
            root_id="root_x",
            relative_path="a",
            name="a",
            first_seen_run="r",
            last_seen_run="r",
            **fields,
        )

    assert integer_range_violations(entry(size_bytes=2**63 - 1, inode=2**64 - 1), spec) == []
    assert integer_range_violations(entry(size_bytes=2**63), spec) == [("size_bytes", 2**63)]
    assert integer_range_violations(entry(inode=2**64), spec) == [("inode", 2**64)]
    assert integer_range_violations(entry(inode=-1), spec) == [("inode", -1)]


def test_a_value_the_index_cannot_store_is_one_entry_s_fact_and_the_census_publishes(
    tmp_path, monkeypatch
):
    """One strange file must not make a census that publishes nothing."""
    from ingence.scan import census as census_module

    project, _ = make_project(tmp_path)
    original = census_module._Walker.build_entry

    def impossible_size(self, **kwargs):
        built = original(self, **kwargs)
        if built.name == "notes.txt":
            return built.model_copy(update={"size_bytes": 2**64})
        return built

    monkeypatch.setattr(census_module._Walker, "build_entry", impossible_size)
    report = run_census(project, run_id=FIRST_RUN, started_at="t")

    assert report.published
    with open_catalog(project.path) as catalog:
        (size,) = catalog.query(
            "SELECT size_bytes FROM entries WHERE name = 'notes.txt'"
        ).fetchone()
        errors = catalog.query(
            "SELECT operation, error_type, failure_class FROM scan_errors"
        ).fetchall()
    assert size is None
    assert errors == [("stat.size_bytes", "OutOfRange", "comprehension")]


# ---------------------------------------------------------------------------
# The engine's own files, recognised by their layout and left out.
# ---------------------------------------------------------------------------

FIRST_RUN = "run_20260101T000001Z_111111111111"
SECOND_RUN = "run_20260101T000002Z_222222222222"


def published_project(location, pool_it_indexes):
    """A project that has published a run, wherever the test wants it kept."""
    pool_it_indexes.mkdir(parents=True, exist_ok=True)
    (pool_it_indexes / "a.txt").write_text("a")
    kept = Project.create(location, created_at="t", roots=[pool_it_indexes])
    run_census(kept, run_id=FIRST_RUN, started_at="t")
    return kept


def indexed_paths(project):
    with open_catalog(project.path) as catalog:
        rows = catalog.query("SELECT relative_path FROM entries").fetchall()
    return {str(row[0]) for row in rows}


def listed(report):
    return [(path.relative_path, path.recognised_as, path.indexed) for path in report.recognised]


def test_a_project_and_a_run_inside_a_root_are_recognised_and_left_out(tmp_path):
    pool = build_pool(tmp_path)
    kept = published_project(pool / "kept_project", tmp_path / "elsewhere")
    shutil.copytree(kept.path / "runs" / FIRST_RUN, pool / "exp2" / "kept_run")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])

    report = run_census(project, run_id=SECOND_RUN, started_at="t")

    paths = indexed_paths(project)
    assert "exp2/notes.txt" in paths
    assert not [path for path in paths if path.startswith(("kept_project", "exp2/kept_run"))]
    assert listed(report) == [
        ("exp2/kept_run", RecognisedAs.RUN, False),
        ("kept_project", RecognisedAs.PROJECT, False),
    ]
    with open_catalog(project.path) as catalog:
        (stored,) = catalog.query('SELECT recognised_json FROM "run"').fetchone()
    assert [item["relative_path"] for item in json.loads(str(stored))] == [
        "exp2/kept_run",
        "kept_project",
    ]


def test_a_folder_holding_only_some_of_the_names_is_an_ordinary_folder(tmp_path):
    """A project.json beside a settings.yaml is common outside this engine."""
    pool = build_pool(tmp_path)
    lookalike = pool / "lookalike"
    (lookalike / "runs").mkdir(parents=True)
    (lookalike / "project.json").write_text("{}")
    (lookalike / "settings.yaml").write_text("a: 1\n")
    (pool / "exp1" / "manifest.json").write_text("{}")
    (pool / "exp1" / "run").mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])

    report = run_census(project, run_id=SECOND_RUN, started_at="t")

    assert report.recognised == ()
    assert {
        "lookalike/project.json",
        "lookalike/runs",
        "exp1/manifest.json",
        "exp1/run",
    } <= indexed_paths(project)


def test_with_the_setting_on_projects_and_runs_are_indexed_and_still_listed(tmp_path):
    pool = build_pool(tmp_path)
    kept = published_project(pool / "kept_project", tmp_path / "elsewhere")
    shutil.copytree(kept.path / "runs" / FIRST_RUN, pool / "exp2" / "kept_run")
    settings = Settings(scan=ScanSettings(index_recognised_outputs=True))
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)

    report = run_census(project, run_id=SECOND_RUN, started_at="t")

    assert {"kept_project/project.json", "exp2/kept_run/manifest.json"} <= indexed_paths(project)
    assert listed(report) == [
        ("exp2/kept_run", RecognisedAs.RUN, True),
        ("kept_project", RecognisedAs.PROJECT, True),
        (f"kept_project/runs/{FIRST_RUN}", RecognisedAs.RUN, True),
    ]


def test_the_installation_is_never_indexed_even_with_the_setting_on(tmp_path):
    pool = build_pool(tmp_path)
    engine = pool / "exp1" / "engine"
    (engine / "scan").mkdir(parents=True)
    (engine / "scan" / "census.py").write_text("x")
    identity = os.stat(engine)
    settings = Settings(scan=ScanSettings(index_recognised_outputs=True))
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)

    report = run_census(
        project,
        run_id=SECOND_RUN,
        started_at="t",
        installation=frozenset({(identity.st_dev, identity.st_ino)}),
    )

    assert not [path for path in indexed_paths(project) if path.startswith("exp1/engine")]
    assert listed(report) == [("exp1/engine", RecognisedAs.INSTALLATION, False)]


def test_the_running_engine_is_recognised_by_the_directory_it_is_imported_from():
    import ingence

    package = os.stat(Path(ingence.__file__).resolve().parent)
    assert (package.st_dev, package.st_ino) in installation_directories()


def test_the_census_imports_no_network_client() -> None:
    """Recognising an editable install reads one file, and needs no network machinery loaded.

    Measured in a fresh interpreter, because this process has imported everything. Recognition
    reads a file URL with ``urllib.request``, which brings ``http.client`` and ``ssl`` with it,
    so a regression there loads all three, and the first two are asserted.

    **``ssl`` is loaded by pyarrow, and is reported rather than asserted.** On Linux, importing
    the census imports ``pyarrow.parquet``, whose ``pyarrow.fs`` imports the cloud filesystem
    bindings, and those load ``ssl``; ``import pyarrow`` alone does not. CI named that chain on
    CPython 3.12.14, and macOS and Windows load none of the three. A module loaded is not a
    connection made, so ``ssl`` loaded by pyarrow is an expected failure, and loaded by anything
    else it fails the test and names the import that did.
    """
    import subprocess

    clients = ("http.client", "ssl", "urllib.request")
    probe = (
        "import json, sys\n"
        f"clients = {clients!r}\n"
        "loaders = {name: 'the interpreter, before the census' for name in clients"
        " if name in sys.modules}\n"
        "class Watch:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name in clients and name not in loaders:\n"
        "            frame, chain = sys._getframe(1), []\n"
        "            while frame is not None:\n"
        "                module = frame.f_globals.get('__name__', '?')\n"
        "                if not module.startswith(('importlib._bootstrap', '_frozen_importlib'))"
        " and module not in chain:\n"
        "                    chain.append(module)\n"
        "                frame = frame.f_back\n"
        "            loaders[name] = ' <- '.join(chain)\n"
        "sys.meta_path.insert(0, Watch())\n"
        "import ingence.scan.census\n"
        "print(json.dumps({name: loaders.get(name, '?') for name in clients"
        " if name in sys.modules}))\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=120
    )
    loaded = json.loads(finished.stdout)
    ssl_loader = loaded.pop("ssl", None)
    assert loaded == {}, f"importing the census loads {loaded}"
    if ssl_loader is not None:
        assert ssl_loader.startswith("pyarrow."), f"ssl is loaded by {ssl_loader}"
        pytest.xfail(f"ssl is loaded by {ssl_loader}: pyarrow's filesystem bindings")


def test_a_census_records_the_filesystem_facts_its_platform_gives(tmp_path):
    """Which facts an entry carries is a property of the platform, measured on a real walk.

    Every test that builds an ``Entry`` by hand carries the facts its author imagined, which is
    how ``same_observed_metadata`` keyed nothing on Linux while every test of it passed: Linux
    gives no creation time. So a census walks the generated pool, and each platform's answer is
    stated rather than skipped.

    - **macOS.** A creation time from the birth time, and an inode, a device and a mode on every
      entry.
    - **Linux.** No creation time, recorded as unavailable, and the other three on every entry.
    - **Windows.** A creation time from the birth time CPython 3.12 reads there, and a mode on
      every entry. An inode and a device only where the census takes a real stat, the root and
      each directory, because ``DirEntry.stat`` carries neither for a file or a link.
    """
    pool = write_pool(tmp_path / "pool")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool.root])
    run_census(project, run_id=FIRST_RUN, started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT entry_type, depth, created_ns, created_time_source, inode, device, mode "
            "FROM entries WHERE NOT is_deleted"
        ).fetchall()
    assert len(rows) > len(pool.written)

    for entry_type, depth, created_ns, created_source, inode, device, mode in rows:
        where = f"a {entry_type} at depth {depth}"
        assert mode is not None, where
        if sys.platform == "darwin":
            assert (created_source, created_ns is None) == ("birthtime", False), where
            assert (inode is None, device is None) == (False, False), where
        elif sys.platform == "win32":
            assert (created_source, created_ns is None) == ("birthtime", False), where
            statted = depth == 0 or entry_type == EntryType.DIRECTORY.value
            assert (inode is not None, device is not None) == (statted, statted), where
        elif sys.platform.startswith("linux"):
            assert (created_source, created_ns) == ("unavailable", None), where
            assert (inode is None, device is None) == (False, False), where
        else:
            pytest.fail(f"no expectation is stated for {sys.platform}")


def test_a_link_to_a_project_is_recorded_as_a_link_and_never_recognised(tmp_path):
    """A link is never followed, so nothing behind one is looked at to recognise it."""
    pool = build_pool(tmp_path)
    kept = published_project(tmp_path / "kept_project", tmp_path / "elsewhere")
    try:
        os.symlink(kept.path, pool / "linked_project", target_is_directory=True)
    except OSError:
        pytest.skip("this platform refuses to create a symbolic link here")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])

    report = run_census(project, run_id=SECOND_RUN, started_at="t")

    assert report.recognised == ()
    entries, _, _, _, _ = records_of(project)
    linked = [entry.entry_type for entry in entries if entry.name == "linked_project"]
    assert linked == [EntryType.SYMLINK]


def test_max_depth_stops_the_walk(tmp_path):
    pool = build_pool(tmp_path)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(scan=ScanSettings(max_depth=1)),
    )
    entries, _, _, _, _ = records_of(project)
    assert max(entry.depth for entry in entries) == 1


def test_the_data_pool_is_never_written_to(tmp_path):
    """Ingence never changes, moves or deletes the files it reads."""
    project, pool = make_project(tmp_path)
    before = {
        path.relative_to(pool).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(pool.rglob("*"))
    }
    run_census(project, run_id="run_20260101T000000Z_aaaaaaaaaaaa", started_at="t")
    after = {
        path.relative_to(pool).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(pool.rglob("*"))
    }
    assert before == after


# ---------------------------------------------------------------------------
# Reuse, deltas and tombstones.
# ---------------------------------------------------------------------------


def run(project, suffix: str):
    return run_census(project, run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}", started_at="t")


def test_a_second_scan_of_an_unchanged_tree_reuses_every_file(tmp_path):
    project, _ = make_project(tmp_path)
    first = run(project, "1")
    second = run(project, "2")
    assert first.counts.reuse[ReuseDecision.NO_PREVIOUS_RECORD] == first.counts.files
    assert second.counts.reuse[ReuseDecision.REUSE] == second.counts.files


def test_only_files_are_counted_as_reuse_candidates(tmp_path):
    """A directory reported as a miss makes every run look like it redid work."""
    project, _ = make_project(tmp_path)
    report = run(project, "1")
    assert sum(report.counts.reuse.values()) == report.counts.files


def test_first_seen_is_carried_forward_on_reuse(tmp_path):
    """Overwriting it would make every entry look new on every scan."""
    project, _ = make_project(tmp_path)
    first = run(project, "1")
    run(project, "2")
    with open_catalog(project.path) as catalog:
        seen = catalog.query("SELECT DISTINCT first_seen_run FROM entries").fetchall()
    assert [row[0] for row in seen] == [first.run_id]


def test_a_new_file_is_detected_and_a_deleted_one_is_tombstoned(tmp_path):
    """Deletion is recorded, never applied."""
    project, pool = make_project(tmp_path)
    run(project, "1")
    (pool / "exp2" / "added.txt").write_text("new")
    (pool / "exp1" / "raw" / "m1.tif").unlink()
    report = run(project, "2")

    assert report.counts.tombstones == 1
    with open_catalog(project.path) as catalog:
        rows = catalog.query("SELECT count(*) FROM entries WHERE is_deleted").fetchone()
        assert rows[0] == 1
        assert (
            catalog.query(
                "SELECT count(*) FROM entries WHERE relative_path = 'exp2/added.txt'"
            ).fetchone()[0]
            == 1
        )


def test_a_tombstone_keeps_what_the_entry_it_replaces_was(tmp_path):
    """A blank row says nothing about what has gone."""
    project, pool = make_project(tmp_path)
    run(project, "1")
    (pool / "exp1" / "raw" / "m1.tif").unlink()
    run(project, "2")

    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT entry_kind, entry_type, relative_path, name, depth, first_seen_run "
            "FROM entries WHERE is_deleted"
        ).fetchall()
    assert rows == [
        ("filesystem", "file", "exp1/raw/m1.tif", "m1.tif", 3, "run_20260101T000001Z_111111111111")
    ]


def test_a_changed_file_is_not_reused(tmp_path):
    project, pool = make_project(tmp_path)
    run(project, "1")
    (pool / "exp2" / "notes.txt").write_bytes(b"z" * 999)
    report = run(project, "2")
    assert report.counts.reuse[ReuseDecision.STAT_CHANGED] == 1


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_two_clean_scans_of_an_unchanged_tree_agree_exactly(tmp_path):
    """The reproducibility claim for two clean scans of an unchanged tree.

    Byte-identical files are impossible and were never the claim: the run
    identifier is stamped into every row. The claim is that two runs agree on
    every column that describes the data, and that is what is compared.

    The test makes three runs and compares the last two, because of a platform
    behaviour. On Windows a directory's last-write time is not settled
    immediately after the directory is written: an enumeration taken moments
    later can report a value a couple of milliseconds earlier than the one that
    eventually sticks. A tree still settling is a tree that is changing, and the
    claim concerns an unchanged one. The first run absorbs the settling. Runs two
    and three are the ones that count.

    The engine is unaffected either way. A directory whose recorded time later
    differs simply reports a changed stat fingerprint and is re-examined, which
    is the correct response to a time that changed.
    """
    project, _ = make_project(tmp_path)
    run(project, "1")
    second = run(project, "2")
    third = run(project, "3")
    differences = compare_runs(project.path, second.run_id, third.run_id)
    assert differences == {}, describe_differences(project.path, second.run_id, third.run_id)


def test_the_comparison_notices_a_genuine_difference(tmp_path):
    """A comparison that cannot fail proves nothing about the one that passes."""
    project, pool = make_project(tmp_path)
    first = run(project, "1")
    (pool / "exp2" / "another.txt").write_text("different")
    second = run(project, "2")
    assert compare_runs(project.path, first.run_id, second.run_id) != {}


def test_concurrency_does_not_change_the_result(tmp_path):
    """Records arrive in completion order; nothing downstream may depend on it."""
    pool = build_pool(tmp_path)
    serial = Project.create(
        tmp_path / "serial",
        created_at="t",
        roots=[pool],
        settings=Settings(scan=ScanSettings(workers=1)),
    )
    parallel = Project.create(
        tmp_path / "parallel",
        created_at="t",
        roots=[pool],
        settings=Settings(scan=ScanSettings(workers=16)),
    )
    one, _, _, _, _ = records_of(serial)
    many, _, _, _, _ = records_of(parallel)
    assert sorted(entry.entry_id for entry in one) == sorted(entry.entry_id for entry in many)


# ---------------------------------------------------------------------------
# Interruption.
# ---------------------------------------------------------------------------


def test_a_cancelled_run_publishes_nothing_and_leaves_the_project_valid(tmp_path):
    project, _ = make_project(tmp_path)
    token = CancellationToken()
    token.cancel("interrupted before it began")
    report = run_census(
        project, run_id="run_20260101T000000Z_cccccccccccc", started_at="t", token=token
    )
    assert report.cancelled
    assert not report.published
    assert read_pointer(project.path) is None
    assert resolve_current_run(project.path) is None


def test_cancelling_a_later_run_leaves_the_earlier_one_current(tmp_path):
    project, _ = make_project(tmp_path)
    first = run(project, "1")
    token = CancellationToken()
    token.cancel("stop")
    run_census(project, run_id="run_20260101T000009Z_dddddddddddd", started_at="t", token=token)
    pointer = read_pointer(project.path)
    assert pointer is not None
    assert pointer.run_id == first.run_id


@pytest.mark.parametrize("after_directories", [0, 1, 2, 3, 5])
def test_interruption_at_five_points_always_leaves_a_valid_project(tmp_path, after_directories):
    """An interruption at five distinct points in the walk leaves a valid project."""
    project, _ = make_project(tmp_path)
    baseline = run(project, "1")

    token = CancellationToken()
    seen = 0
    identifier = f"run_20260101T000008Z_{after_directories:012d}"
    original_walk = walk_root

    def cancel_after(*args, **kwargs):
        nonlocal seen
        for chunk in original_walk(*args, **kwargs):
            yield chunk
            seen += 1
            if seen >= after_directories:
                token.cancel(f"after {after_directories} directories")

    import ingence.scan.census as census_module

    census_module.walk_root = cancel_after
    try:
        report = run_census(project, run_id=identifier, started_at="t", token=token)
    finally:
        census_module.walk_root = original_walk

    assert not report.published
    pointer = read_pointer(project.path)
    assert pointer is not None
    assert pointer.run_id == baseline.run_id
    with open_catalog(project.path) as catalog:
        assert catalog.count("entries") == baseline.counts.entries


# ---------------------------------------------------------------------------
# Publication.
# ---------------------------------------------------------------------------


def test_a_published_run_passes_every_invariant(tmp_path):
    project, _ = make_project(tmp_path)
    report = run(project, "1")
    assert report.published
    assert [o.invariant_id for o in report.invariants if not o.passed] == []


def test_the_catalog_reads_what_the_census_wrote(tmp_path):
    project, _ = make_project(tmp_path)
    report = run(project, "1")
    with open_catalog(project.path) as catalog:
        counts = catalog.counts()
    assert counts["entries"] == report.counts.entries
    assert counts["assertions"] == report.counts.entries - 1
    assert counts["roots"] == 1
    assert counts["run"] == 1


def test_entry_identity_is_the_composite_function_with_an_empty_locator(tmp_path):
    project, _ = make_project(tmp_path)
    entries, _, _, _, _ = records_of(project)
    declaration = project.identity.roots[0]
    notes = next(entry for entry in entries if entry.relative_path == "exp2/notes.txt")
    assert notes.entry_id == entry_id(
        declaration.root_id,
        "exp2/notes.txt",
        (),
        case_sensitivity=declaration.case_sensitivity,
    )


def test_the_scope_of_every_census_edge_is_its_root(tmp_path):
    project, _ = make_project(tmp_path)
    _, assertions, _, _, _ = records_of(project)
    expected = Scope(ScopeKind.ROOT, project.identity.roots[0].root_id)
    assert {assertion.scope for assertion in assertions} == {expected}


def test_a_staging_directory_is_removed_once_a_run_publishes(tmp_path):
    project, _ = make_project(tmp_path)
    report = run(project, "1")
    assert not staging_path(project.path, report.run_id).exists()


def test_a_missing_device_identifier_does_not_stop_the_walk(tmp_path):
    """The Windows bug, reproduced on every platform.

    ``DirEntry.stat`` reuses directory-enumeration data, which on Windows does
    not carry the volume serial number, so the device identifier arrives as
    zero. Compared naively against the root's it differs, and the same-filesystem
    guard then rejected every subdirectory: the walk found only the top level.
    """
    project, pool = make_project(tmp_path)
    root = project.identity.roots[0]

    class ZeroDevice:
        """A stat result reporting no device, as Windows enumeration does."""

        def __init__(self, real):
            self._real = real
            self.st_dev = 0

        def __getattr__(self, name):
            return getattr(self._real, name)

    from ingence.scan.census import _Walker

    walker = _Walker(
        root=root,
        mount=pool,
        settings=project.settings,
        run_id="run_test",
        previous={},
        extractor_registry=None,
        token=None,
    )
    walker.root_device = int(pool.stat().st_dev)
    child = pool / "exp1"
    assert not walker.crosses_filesystem(child, ZeroDevice(child.stat()))


def test_cancelling_as_the_last_directory_completes_still_publishes_nothing(tmp_path):
    """Whether a run survives a Ctrl-C must not depend on a race nobody can see."""
    project, _ = make_project(tmp_path)
    baseline = run(project, "1")

    token = CancellationToken()
    import ingence.scan.census as census_module

    original = census_module.walk_root

    def cancel_at_the_very_end(*args, **kwargs):
        chunks = list(original(*args, **kwargs))
        yield from chunks
        token.cancel("pressed at the last moment")

    census_module.walk_root = cancel_at_the_very_end
    try:
        report = run_census(
            project, run_id="run_20260101T000007Z_eeeeeeeeeeee", started_at="t", token=token
        )
    finally:
        census_module.walk_root = original

    assert report.cancelled
    assert not report.published
    pointer = read_pointer(project.path)
    assert pointer is not None
    assert pointer.run_id == baseline.run_id


# ---------------------------------------------------------------------------
# The Dictionary, wired to the walk.
# ---------------------------------------------------------------------------


def test_a_census_records_what_the_dictionary_made_of_every_name(tmp_path):
    """The vocabulary is a by-product of the walk the census already does.

    Before this, the Index stored every name as an opaque string and nothing in
    a run ever looked one up. The point of doing it here rather than in the
    content pass is that it never depends on a deepening budget, which can
    legitimately run out halfway through a pool.
    """
    project, _ = make_project(tmp_path)
    report = run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    assert report.published
    assert report.table_rows["components"] > 0
    assert report.table_rows["name_shapes"] > 0
    # One shape per entry, because every entry has a name.
    assert report.table_rows["name_shapes"] == report.table_rows["entries"]


def test_a_run_writes_down_the_vocabulary_it_used(tmp_path):
    """A run can be made again only from a Dictionary that was written down.

    A run records ``dictionary_hash`` and a snapshot of its vocabulary. Reproducibility from the
    Index, the Rulebook, the Dictionary and the Record needs all four written down.
    """
    project, _ = make_project(tmp_path)
    run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    with open_catalog(project.path) as catalog:
        (digest,) = catalog.query("SELECT dictionary_hash FROM run").fetchone()
        assert digest
        assert catalog.count("dictionary_entries") == len(DictionaryStore.builtin())


def test_tokenising_names_can_be_switched_off(tmp_path):
    """A default that cannot be turned off is not a default, it is a decision."""
    project, _ = make_project(tmp_path, dictionary=DictionarySettings(tokenise_names=False))
    report = run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    assert report.table_rows["components"] == 0
    assert report.table_rows["name_shapes"] == 0
    assert report.table_rows["entries"] > 0
    with open_catalog(project.path) as catalog:
        (digest,) = catalog.query("SELECT dictionary_hash FROM run").fetchone()
        # No Dictionary was consulted, so none is claimed. Absent rather than
        # stamped is the honest reading of a run that did not use one.
        assert digest is None


def test_position_can_be_switched_off_while_content_is_kept(tmp_path):
    """The separation is what makes this an experiment rather than a rewrite."""
    project, _ = make_project(tmp_path, dictionary=DictionarySettings(record_name_shapes=False))
    report = run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    assert report.table_rows["components"] > 0
    assert report.table_rows["name_shapes"] == 0


def test_the_vocabulary_views_answer_from_sql(tmp_path):
    """Both queries run against DuckDB, so they hold past a million entries."""
    project, _ = make_project(tmp_path)
    run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    with open_catalog(project.path) as catalog:
        recognised = catalog.recognised_vocabulary(limit=10)
        unbound = catalog.unbound_tokens(limit=10)
    assert recognised
    assert all(row["entries"] >= 1 for row in recognised)
    assert unbound
    # Ordered by distinct entries, because one token in forty names says more
    # than forty occurrences in one.
    counts = [row["entries"] for row in unbound]
    assert counts == sorted(counts, reverse=True)


def test_tokenising_does_not_disturb_determinism(tmp_path):
    """177,000 component rows arrive in thread completion order and must not show it."""
    project, _ = make_project(tmp_path)
    for run in ("run_a", "run_b"):
        run_census(project, run_id=run, started_at="2026-01-01T00:00:00Z")
    # Says *how* they differ, not merely that they do, which is what the other
    # two determinism assertions in the suite already do and this one did not.
    # It failed on Windows reporting four entries and eight observations and
    # nothing about which column was unstable, which is precisely the position
    # `describe_differences` was written to stop a reader being left in.
    assert compare_runs(project.path, "run_a", "run_b") == {}, describe_differences(
        project.path, "run_a", "run_b"
    )


def test_a_census_records_what_the_dictionary_made_of_every_component(tmp_path):
    """One binding per component, written even when the binder declined.

    Absence of a row is then never a fact about a component: it can only mean the
    binder did not run. That is what keeps "not examined" distinct from "examined
    and found nothing", which a bare null cannot express.
    """
    project, _ = make_project(tmp_path)
    report = run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    assert report.published
    assert report.table_rows["dictionary_bindings"] == report.table_rows["components"]


def test_binding_can_be_switched_off_while_the_tokens_are_kept(tmp_path):
    """The switch behind deferring the Dictionary until something needs it."""
    project, _ = make_project(tmp_path, dictionary=DictionarySettings(bind_terms=False))
    report = run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    assert report.table_rows["components"] > 0
    assert report.table_rows["dictionary_bindings"] == 0


def test_an_interpretation_never_shares_a_tier_with_an_observation(tmp_path):
    """A token read from a name and a meaning resolved from it never share a tier, over a whole run.

    A component is a token read off the filesystem and sits at the literal rung.
    A binding is a resolution of meaning and sits at the semantic one, which is
    the only place in the engine that rung is assigned.
    """
    project, _ = make_project(tmp_path)
    run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    with open_catalog(project.path) as catalog:
        (literal,) = catalog.query(
            "SELECT count(*) FROM components WHERE extraction_tier <> 'literal'"
        ).fetchone()
        (semantic,) = catalog.query(
            "SELECT count(*) FROM dictionary_bindings WHERE extraction_tier <> 'semantic'"
        ).fetchone()
    assert literal == 0
    assert semantic == 0


def test_an_extension_is_never_read_as_an_ordinary_word(tmp_path):
    """The file's type is a fact, and facts are not guessed at from vocabulary."""
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "ARCHITECTURE.md").write_bytes(b"x")
    project = Project.create(tmp_path / "project", created_at="2026-01-01T00:00:00Z", roots=[pool])
    run_census(project, run_id="run_a", started_at="2026-01-01T00:00:00Z")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            """
            SELECT b.theme_kind, b.constraint
            FROM dictionary_bindings b
            JOIN components c ON c.component_id = b.component_id
            WHERE c.normalized_value = 'md'
            """
        ).fetchall()
    assert rows
    assert all(kind == "format" for kind, _ in rows)
    assert all(constraint == "extension:format" for _, constraint in rows)


# ---------------------------------------------------------------------------
# Failure class, end to end.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod cannot make a directory untraversable on Windows; it toggles a "
    "read-only flag on files and traversal is governed by ACLs, so the fixture "
    "cannot create the condition and the census has nothing to fail on",
)
def test_a_census_failure_is_recorded_as_an_environment_problem(tmp_path):
    """Every failure the census can produce is access-class, and it says so.

    **Skipped on Windows because the fixture cannot build the case, not because
    the engine behaves differently.** The census catches ``OSError`` from
    ``scandir`` identically everywhere; what is unavailable there is a way to
    provoke one with the standard library. Reaching for ACLs would add a
    dependency and a good deal of machinery to test a branch that is already
    platform-independent.

    The point is not the label. A rule failing most of the time whose failures
    are predominantly access or comprehension needs no modification: it is being
    starved of inputs, and loosening it would compensate for a missing mount by
    degrading a rule that was correct. Recording the class is what makes that
    distinction automatic rather than a judgement somebody has to remember.
    """
    project, pool = make_project(tmp_path)
    unreadable = pool / "locked"
    unreadable.mkdir()
    (unreadable / "inside.txt").write_text("x")
    unreadable.chmod(0o000)
    try:
        run(project, "1")
        with open_catalog(project.path) as catalog:
            rows = catalog.query("SELECT operation, failure_class FROM scan_errors").fetchall()
    finally:
        unreadable.chmod(0o755)

    assert rows, "the unreadable directory should have produced an error"
    assert {row[1] for row in rows} == {"access"}
    assert all(row[0] in {"scandir", "stat", "lstat", "readlink"} for row in rows)


def test_a_name_carrying_a_date_gets_an_event_clock_with_its_tier(tmp_path):
    """The clock, its source and its tier travel together onto the row.

    ``created_ns`` has carried a source since the schema froze and the event
    clock carried none, so the engine could store a time it had no way to reason
    about. A date parsed from a filename is tier three: weaker than an
    instrument's and stronger than the filesystem's, because it survives a copy.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "acquisition_2026-01-04_63x.tif").write_text("x")
    (pool / "undated.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run(project, "1")

    with open_catalog(project.path) as catalog:
        rows = {
            name: (source, tier)
            for name, source, tier in catalog.query(
                "SELECT name, event_time_source, event_time_tier FROM entries "
                "WHERE entry_type = 'file'"
            ).fetchall()
        }

    assert rows["acquisition_2026-01-04_63x.tif"] == ("filename", "name")
    assert rows["undated.txt"] == ("unavailable", "unavailable")


# ---------------------------------------------------------------------------
# Folder organisation, characterised rather than assumed.
# ---------------------------------------------------------------------------


def test_the_characteriser_emits_observations_and_nothing_else(tmp_path):
    """**No claims, no edges, no grouping.** Everything here is a recording.

    The boundary is the point. ``contains`` is observationally complete, so
    after enumeration a composition signature is a fact about what was found.
    Deciding whether a recurring composition is a real workflow is a question
    for a person, and emitting a claim would answer it silently.
    """
    pool = tmp_path / "pool"
    for folder in ("blots", "microscopy"):
        (pool / folder).mkdir(parents=True)
        for index in range(4):
            (pool / folder / f"sample_{index}_2026-01-0{index + 1}.tif").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run(project, "1")

    with open_catalog(project.path) as catalog:
        keys = {
            row[0]
            for row in catalog.query(
                "SELECT DISTINCT key FROM observations WHERE observer = 'census.organisation'"
            ).fetchall()
        }
        rules = {row[0] for row in catalog.query("SELECT DISTINCT rule_id FROM claims").fetchall()}

    assert "folder.composition_digest" in keys
    assert "folder.burst_count" in keys
    assert "folder.name_authorship" in keys
    # The census's own directory rollup is the only rule that emits claims. The
    # characteriser adds none.
    assert rules == {"census.directory_aggregate"}


def test_two_folders_holding_the_same_mixture_share_a_digest(tmp_path):
    """Which is what makes recurrence a group-by rather than a comparison of pairs."""
    pool = tmp_path / "pool"
    for folder in ("run_a", "run_b"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "readings_2026-01-01.csv").write_text("x")
        (pool / folder / "image_2026-01-01.tif").write_text("x")
    (pool / "odd").mkdir()
    (pool / "odd" / "notes.md").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run(project, "1")

    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, o.value_text FROM observations o JOIN entries e "
            "ON e.entry_id = o.entry_id WHERE o.key = 'folder.composition_digest'"
        ).fetchall()
    digests = dict(rows)
    assert digests["run_a"] == digests["run_b"]
    assert digests["odd"] != digests["run_a"]


def test_creation_time_survives_on_a_platform_without_the_nanosecond_field(tmp_path):
    """The regression, as the minimal synthetic case of its class.

    ``st_birthtime_ns`` exists on some platforms and not on macOS, where only
    the float ``st_birthtime`` is offered. Reading only the nanosecond field
    recorded a null and ``unavailable`` for every entry in the pool, on the one
    platform whose filesystem genuinely knows. Reproduced here with a stat
    result that carries only the float, so it fails everywhere rather than only
    where the bug bit.
    """
    from ingence.core.schema import TimeSource
    from ingence.scan.census import _creation_time

    class OnlyFloat:
        st_birthtime = 1_767_484_800.5
        st_ctime_ns = 999

    value, source = _creation_time(OnlyFloat())  # type: ignore[arg-type]
    assert source is TimeSource.BIRTHTIME
    assert value == 1_767_484_800_500_000_000

    class NoBirthtime:
        st_ctime_ns = 999

    # **The right answer differs by platform and both readings are correct**,
    # which is why this asserts each rather than the POSIX one everywhere. On
    # Windows ``st_ctime`` *is* the creation time, so falling back to it states a
    # fact; on POSIX it is the inode's metadata-change time, and reporting that
    # as a creation time is exactly the trap the source field exists to close.
    # Asserted unconditionally, this failed on Windows because the engine was
    # right, and it pinned only half of a two-sided behaviour.
    value, source = _creation_time(NoBirthtime())  # type: ignore[arg-type]
    if os.name == "nt":
        assert source is TimeSource.CTIME
        assert value == 999
    else:
        assert source is TimeSource.UNAVAILABLE
        assert value is None
