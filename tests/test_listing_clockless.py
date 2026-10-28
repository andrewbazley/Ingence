"""A listing states no clock, and nothing may invent one.

An archive's list of its files carries a path and a size and, often, nothing the filesystem would
have said: no modification or change time, no mode, no inode, no device, no link count. The replay
seam that the census uses for a rebind must not turn any of those absences into a zero. A zero would
date a file to the epoch, 1 January 1970, with a mode of 0 that reads as neither file nor folder,
and every temporal rule would read it as a clock. This test checks that no zero appears. The test
also checks that every temporal rule abstains on a clockless listing with a stated reason. The same
tree on disk is the control that shows each of those rules concludes where a clock exists.

Every name here is invented.
"""

from __future__ import annotations

from ingence.core.ids import CaseSensitivity, entry_id, root_id
from ingence.core.project import RootDeclaration
from ingence.core.schema import Entry, EntryType
from ingence.core.settings import Settings
from ingence.scan.census import replay_root

_URI = "listing:kelt:K-0001"
_ROOT = root_id(_URI)


def _declaration() -> RootDeclaration:
    return RootDeclaration(
        root_id=_ROOT,
        root_uri=_URI,
        mount_path="/nonexistent/kelt-listing",
        case_sensitivity=CaseSensitivity.SENSITIVE,
    )


def _listed(relative: str, parent: str | None, kind: EntryType, size: int | None) -> Entry:
    """An entry as a listing states it: a path, a kind, perhaps a size, and nothing else."""
    name = "kelt-listing" if relative == "." else relative.rsplit("/", 1)[-1]
    return Entry(
        entry_id=entry_id(_ROOT, relative, (), case_sensitivity=CaseSensitivity.SENSITIVE),
        root_id=_ROOT,
        relative_path=relative,
        name=name,
        parent_entry_id=parent,
        depth=0 if relative == "." else relative.count("/") + 1,
        entry_type=kind,
        size_bytes=size,
        first_seen_run="run_a",
        last_seen_run="run_a",
    )


def _clockless_listing() -> list[Entry]:
    root = _listed(".", None, EntryType.DIRECTORY, None)
    folder = _listed("vorn_plate", root.entry_id, EntryType.DIRECTORY, None)
    return [
        root,
        folder,
        _listed("vorn_plate/zib_01.tif", folder.entry_id, EntryType.FILE, 4096),
        _listed("vorn_plate/zib_02.tif", folder.entry_id, EntryType.FILE, 4096),
        _listed("readme.txt", root.entry_id, EntryType.FILE, None),
    ]


def test_the_replay_seam_keeps_every_fact_a_listing_does_not_state_absent() -> None:
    """No clock, mode, inode, device or link count is invented, and the kind is the stated one."""
    replayed = [
        entry
        for records in replay_root(
            _declaration(), _clockless_listing(), settings=Settings(), run_id="run_b"
        )
        for entry in records.entries
    ]
    by_path = {entry.relative_path: entry for entry in replayed}
    assert set(by_path) == {
        ".",
        "vorn_plate",
        "vorn_plate/zib_01.tif",
        "vorn_plate/zib_02.tif",
        "readme.txt",
    }
    for entry in replayed:
        assert entry.modified_ns is None, entry.relative_path
        assert entry.changed_ns is None, entry.relative_path
        assert entry.created_ns is None, entry.relative_path
        assert entry.mode is None, entry.relative_path
        assert entry.inode is None, entry.relative_path
        assert entry.device is None, entry.relative_path
        assert entry.nlink is None, entry.relative_path
        # Nothing to fingerprint: a stat fingerprint over absent facts would say "unchanged"
        # about files nobody has stat-ed.
        assert entry.stat_fingerprint is None, entry.relative_path
    assert by_path["vorn_plate"].entry_type is EntryType.DIRECTORY
    assert by_path["vorn_plate/zib_01.tif"].entry_type is EntryType.FILE
    assert by_path["vorn_plate/zib_01.tif"].size_bytes == 4096
    # A size the listing did not state is absent too, not an empty file.
    assert by_path["readme.txt"].size_bytes is None


# -- every temporal rule, end to end ------------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402

from fixtures.revision_4 import write_revision  # noqa: E402
from ingence.core.project import Project  # noqa: E402
from ingence.infer.run import run_inference  # noqa: E402
from ingence.listing.format import (  # noqa: E402
    ClockMeaning,
    DeclaredClock,
    DeclaredDigest,
    Listing,
    ListingHeader,
    ListingRow,
    parse_instant,
    write_listing,
)
from ingence.scan.census import run_census  # noqa: E402
from ingence.storage.catalog import open_catalog  # noqa: E402

#: The rules that read a clock, in the shipped Rulebook and revision 4, and what each abstains to.
#: ``clocks_disagree`` and ``relation_follows_the_clock`` are not evaluable anywhere and are
#: checked apart. A rule added later that reads a clock belongs here.
TEMPORAL_RULES = (
    "earliest_credible_time",
    "folder_copied_in_bulk",
    "copied_in_bulk",
    "same_observed_metadata_outside_programs",
)
_FILES = [f"vorn_plate/zib_{number:02d}.tif" for number in range(6)]
_HEADER = ListingHeader(
    archive="kelt",
    identifier="K-0001",
    fetched_at="2026-01-02T03:04:05Z",
    converter="test",
    converter_version="1",
)


def _listing_project(tmp_path: Path, rows: list[ListingRow]) -> Project:
    project = Project.create(tmp_path / "project", created_at="t", listings=[_URI])
    write_listing(Listing(_HEADER, rows), Path(project.identity.roots[0].mount_path))
    write_revision(project.path)
    return project


def _index_and_infer(project: Project) -> dict[str, object]:
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    report = run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    return {tally.rule_id: tally for tally in (*report.tallies, *report.groupings, *report.flags)}


def _published(project: Project) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    with open_catalog(project.path) as catalog:
        claims = catalog.query(
            "SELECT rule_id, coalesce(object_reference, '') FROM claims ORDER BY claim_id"
        ).fetchall()
        findings = catalog.query(
            "SELECT rule_id, coalesce(reason, '') FROM findings ORDER BY finding_id"
        ).fetchall()
    return claims, findings


def test_every_temporal_rule_abstains_on_a_clockless_listing(tmp_path: Path) -> None:
    """No clock, no conclusion about time, and each abstention says why."""
    project = _listing_project(
        tmp_path, [ListingRow(path=path, size_bytes=4096) for path in _FILES]
    )
    tallies = _index_and_infer(project)
    for rule in TEMPORAL_RULES:
        tally = tallies[rule]
        assert tally.evaluated > 0, rule  # type: ignore[attr-defined]
        assert tally.concluded == 0, rule  # type: ignore[attr-defined]
        assert tally.claims == 0 and tally.findings == 0 and tally.classes == 0, rule  # type: ignore[attr-defined]
    assert tallies["clocks_disagree"].not_evaluable  # type: ignore[attr-defined]
    claims, findings = _published(project)
    assert not [claim for claim in claims if "1970" in claim[1]]
    assert not [finding for finding in findings if "1970" in finding[1]]
    # And the abstention says why, in the rule's own words.
    from ingence.core.schema import Entry
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.registry import PREDICATES

    load_builtins()
    with open_catalog(project.path) as catalog:
        row = (
            catalog.query("SELECT * FROM entries WHERE relative_path = ?", [_FILES[0]])
            .to_arrow_table()
            .to_pylist()[0]
        )
    entry = Entry.model_validate(
        {key: value for key, value in row.items() if key in Entry.model_fields}
    )
    explained = PREDICATES.get("earliest_credible_time").explain_abstention(entry, {})
    assert "recorded a time" in explained.reason


def test_the_same_tree_on_disk_is_the_control_where_each_temporal_rule_concludes(
    tmp_path: Path,
) -> None:
    """The control: with clocks, the rules the listing silenced conclude, so silence was absence."""
    pool = tmp_path / "pool"
    for path in _FILES:
        target = pool / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"k" * 4096)
        os.utime(target, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    tallies = _index_and_infer(project)
    assert tallies["earliest_credible_time"].concluded == len(_FILES)  # type: ignore[attr-defined]
    # Six files agreeing on every observed fact but their names are no class of one name each;
    # the rule is evaluated on a clock it can read, which is all the control needs of it.
    assert tallies["same_observed_metadata_outside_programs"].evaluated == len(_FILES)  # type: ignore[attr-defined]


def test_a_listing_with_only_copy_times_is_one_bulk_copy_and_no_order(tmp_path: Path) -> None:
    """An archive's write time is a copy's arrival, never the file's own time."""
    moment = parse_instant("2023-08-11T12:09:30Z")
    assert moment is not None
    rows = [
        ListingRow(
            path=path,
            size_bytes=4096,
            clocks=(
                DeclaredClock(
                    ClockMeaning.ARCHIVE_WRITE, moment + number * 1_000_000_000, "Last-Modified"
                ),
            ),
        )
        for number, path in enumerate(_FILES)
    ]
    project = _listing_project(tmp_path, rows)
    tallies = _index_and_infer(project)
    assert tallies["earliest_credible_time"].concluded == 0  # type: ignore[attr-defined]
    assert tallies["folder_copied_in_bulk"].concluded == 0  # type: ignore[attr-defined]
    assert tallies["copied_in_bulk"].findings == 0  # type: ignore[attr-defined]
    claims, _ = _published(project)
    copied = [value for rule, value in claims if rule == "folder_copied_by_declared_clocks"]
    assert [json.loads(value) for value in copied] == [
        {
            "copied_on": "2023-08-11",
            "files": 6,
            "first": "2023-08-11T12:09:30Z",
            "last": "2023-08-11T12:09:35Z",
        }
    ]
    assert not [value for _, value in claims if "1970" in value]


def test_a_declared_digest_groups_one_tier_below_and_the_engines_reading_retracts_it(
    tmp_path: Path,
) -> None:
    """Identical by a declared digest, one tier below an observed one."""
    same = DeclaredDigest("md5", "c" * 32, "Sum")
    rows = [
        ListingRow(path="vorn/a.tif", size_bytes=5, digests=(same,)),
        ListingRow(path="vorn/b.tif", size_bytes=5, digests=(same,)),
        ListingRow(
            path="vorn/c.tif", size_bytes=5, digests=(DeclaredDigest("md5", "d" * 32, "S"),)
        ),
    ]
    project = _listing_project(tmp_path, rows)
    tallies = _index_and_infer(project)
    assert tallies["identical_by_declared_digest"].classes == 1  # type: ignore[attr-defined]
    assert tallies["identical_by_declared_digest"].grouped == 2  # type: ignore[attr-defined]
    with open_catalog(project.path) as catalog:
        tiers = catalog.query(
            "SELECT DISTINCT inference_tier FROM claims "
            "WHERE rule_id = 'identical_by_declared_digest'"
        ).fetchall()
    assert tiers == [("defeasible",)]


def test_the_engines_own_digest_contradicting_a_declared_one_is_concluded() -> None:
    from ingence.core.schema import Observation
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.registry import PREDICATES

    load_builtins()

    def observation(key: str, value: str, scope: str) -> Observation:
        return Observation(
            observation_id=f"obs_{key}_{value}",
            entry_id="entry_a",
            run_id="r",
            observer="o",
            observer_version="1",
            observed_at="t",
            key=key,
            value_text=value,
            source_scope=scope,
        )

    declared = observation("declared.digest.md5", "c" * 32, "declared")
    agreeing = observation("identity.md5", "c" * 32, "content")
    disagreeing = observation("identity.md5", "e" * 32, "content")
    predicate = PREDICATES.get("declared_digest_contradicted")
    parameters = {"digest_algorithm": "md5"}
    assert predicate.evaluate((declared,), parameters) is None
    assert "not read" in predicate.explain_abstention((declared,), parameters).reason
    assert predicate.evaluate((declared, agreeing), parameters) is None
    concluded = predicate.evaluate((declared, disagreeing), parameters)
    assert concluded is not None
    assert json.loads(concluded.values[0])["read"] == ["e" * 32]
    grouping = PREDICATES.get("same_declared_digest")
    two = observation("declared.digest.md5", "f" * 32, "declared")
    assert grouping.evaluate((declared, two), parameters) is None
    assert "2 different" in grouping.explain_abstention((declared, two), parameters).reason
