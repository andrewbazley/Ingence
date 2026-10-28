"""The generated laboratory pool keeps its promises, before anything is measured on it.

What is tested here is the pool, never the engine's verdicts on it. Invented stems that bind
to nothing, no word from outside the shipped Dictionary, the same bytes on every write, nothing
written over, and a census that reads every member. Whether the rules do what the expectations
say is measured on the pool, and a disagreement there is a finding, never a reason to change the
pool.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

import pytest

from fixtures.laboratory_pool import (
    CASES,
    CONDITIONS,
    STEMS,
    Groups,
    Proposes,
    Retracts,
    SharesTemplate,
    expectations_over,
    members,
    named_members,
    write_pool,
)
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import read_name
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.scan.census import compound_extension, run_census
from ingence.storage.catalog import open_catalog

STORE = DictionaryStore.builtin()
NORMALISER = Normaliser(STORE)


def _reading(name: str):
    """Read one name as the census reads it."""
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


def _contents(root: Path) -> list[tuple[str, bytes | str]]:
    """Every file under a folder with its bytes, and every link with its target."""
    found: list[tuple[str, bytes | str]] = []
    for folder, _, names in os.walk(root):
        for name in names:
            path = Path(folder, name)
            body = os.readlink(path) if path.is_symlink() else path.read_bytes()
            found.append((path.relative_to(root).as_posix(), body))
    return sorted(found)


@pytest.mark.parametrize("stem", STEMS)
def test_an_invented_stem_binds_to_nothing_in_the_shipped_dictionary(stem: str) -> None:
    reading = _reading(stem)

    assert [component.component_type for component in reading.components] == ["word"]
    assert [binding.resolution_status for binding in reading.bindings] == [
        ResolutionStatus.EXAMINED_NO_EVIDENCE
    ]


def test_every_other_word_in_the_pool_resolves_to_a_shipped_sense() -> None:
    for member in members():
        path = PurePosixPath(member.path)
        assert all(set(folder.split("-")) <= set(STEMS) for folder in path.parts[:-1])
        reading = _reading(path.name)
        binding_of = {binding.component_id: binding for binding in reading.bindings}
        for component in reading.components:
            if component.component_type != "word" or component.normalized_value in STEMS:
                continue
            status = binding_of[component.component_id].resolution_status
            assert status is ResolutionStatus.RESOLVED, (member.path, component.normalized_value)


def test_every_expectation_names_members_of_its_own_case() -> None:
    paths = [member.path for member in members()]
    assert len(paths) == len(set(paths))

    for case in CASES:
        own = {member.path for member in case.members}
        for expectation in case.expected:
            assert named_members(expectation) <= own, (case.name, expectation)
            if isinstance(expectation, Proposes):
                assert expectation.within in {"same_name_family", "same_name_identity", "parent"}


def test_every_condition_stated_is_one_an_expectation_names() -> None:
    named = {
        expectation.condition
        for case in CASES
        for expectation in case.expected
        if isinstance(expectation, Retracts)
    }

    assert named == set(CONDITIONS)


def test_the_pool_is_written_the_same_way_every_time(tmp_path: Path) -> None:
    first = write_pool(tmp_path / "first")
    second = write_pool(tmp_path / "second")

    assert first.written == second.written
    assert first.written == tuple(
        member.path for member in members() if member.path not in first.refused_links
    )
    assert _contents(first.root) == _contents(second.root)


def test_the_pool_is_never_written_over_anything(tmp_path: Path) -> None:
    root = tmp_path / "pool"
    root.mkdir()
    (root / "kept.txt").write_text("kept")

    with pytest.raises(FileExistsError):
        write_pool(root)

    assert [path.name for path in root.iterdir()] == ["kept.txt"]


def test_a_link_left_unwritten_takes_with_it_only_what_it_alone_could_show() -> None:
    every = [member.path for member in members()]
    link = next(member.path for member in members() if member.link_to is not None)

    assert expectations_over(every) == tuple(
        expectation for case in CASES for expectation in case.expected
    )
    without = expectations_over([path for path in every if path != link])
    assert len(without) < len(expectations_over(every))
    for expectation in without:
        assert link not in named_members(expectation)
        if isinstance(expectation, (Groups, SharesTemplate)):
            assert len(expectation.members) >= 2


def test_the_census_reads_every_member_written_and_nothing_else(tmp_path: Path) -> None:
    pool = write_pool(tmp_path / "pool")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool.root])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")

    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT relative_path, entry_type FROM entries WHERE entry_type <> 'directory'"
        ).fetchall()

    read = {str(path).replace("\\", "/"): str(kind) for path, kind in rows}
    link_of = {member.path: member.link_to for member in members()}
    assert read == {path: "file" if link_of[path] is None else "symlink" for path in pool.written}
