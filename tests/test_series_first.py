"""Series first, without inheritance.

The file list records which files are alike, and a content pass reads one of every series before any
series gets a second. A series is the files of one folder sharing a name shape, an extension and a
size class. Its key is a fact the census records about each file, never a claim. Within a series the
first and the last by identifier are read, and any file whose size stands out. Where those disagree
on what the file is, twice as many are read, until they agree or the series is read. The rest stay
unread, each described by a read file of its series. Every name is invented.
"""

from __future__ import annotations

import struct
from pathlib import Path

from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, ScanOutcome
from ingence.core.settings import ExtractionSettings, Settings, SpendOrder
from ingence.scan.census import SERIES_KEY, SERIES_SIZE_CLASS_KEY, run_census, series_key
from ingence.scan.deepen import run_deepen, series_picks, series_signature, spend_order
from ingence.storage.catalog import open_catalog

# -- the key --------------------------------------------------------------------------------------


def test_the_key_is_the_folder_the_shape_the_extension_and_the_size_class() -> None:
    same = series_key("root_a", "plates/day1", "shape_x", "tif", 5_000)
    assert same == series_key("root_a", "plates/day1", "shape_x", "tif", 7_000), "one size class"
    assert same == series_key("root_a", "plates/day1", "shape_x", "TIF", 5_000), "case-folded"
    assert same != series_key("root_a", "plates/day2", "shape_x", "tif", 5_000), "the folder"
    assert same != series_key("root_a", "plates/day1", "shape_y", "tif", 5_000), "the shape"
    assert same != series_key("root_a", "plates/day1", "shape_x", "png", 5_000), "the extension"
    assert same != series_key("root_a", "plates/day1", "shape_x", "tif", 50_000), "the size class"
    assert same != series_key("root_b", "plates/day1", "shape_x", "tif", 5_000), "the root"
    assert len(same) == 32


def _pool(base: Path) -> Path:
    pool = base / "pool"
    (pool / "day1").mkdir(parents=True)
    (pool / "day2").mkdir(parents=True)
    for number in range(1, 5):
        (pool / "day1" / f"well_{number:02d}.csv").write_text(f"well,value\nA{number},{number}\n")
    (pool / "day1" / "notes.txt").write_text("a note about the plate\n")
    (pool / "day2" / "well_01.csv").write_text("well,value\nB1,1\n")
    (pool / "day2" / "well_02.csv").write_text("well,value\nB2,2\n")
    return pool


def test_the_census_records_each_file_s_series_key_and_size_class(tmp_path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    with open_catalog(project.path) as catalog:
        keys = dict(
            catalog.query(
                "SELECT e.relative_path, o.value_text FROM observations o JOIN entries e USING "
                "(entry_id) WHERE o.key = ? AND o.observer = 'census'",
                [SERIES_KEY],
            ).fetchall()
        )
        classes = dict(
            catalog.query(
                "SELECT e.relative_path, o.value_integer FROM observations o JOIN entries e "
                "USING (entry_id) WHERE o.key = ?",
                [SERIES_SIZE_CLASS_KEY],
            ).fetchall()
        )
        tiers = {
            row[0]
            for row in catalog.query(
                "SELECT DISTINCT extraction_tier FROM observations WHERE key = ?", [SERIES_KEY]
            ).fetchall()
        }
    assert set(keys) == {
        "day1/well_01.csv",
        "day1/well_02.csv",
        "day1/well_03.csv",
        "day1/well_04.csv",
        "day1/notes.txt",
        "day2/well_01.csv",
        "day2/well_02.csv",
    }, "every file and no folder"
    wells_day1 = {keys[f"day1/well_{n:02d}.csv"] for n in range(1, 5)}
    assert len(wells_day1) == 1, "one folder, one shape, one extension, one size class"
    assert keys["day1/notes.txt"] not in wells_day1
    assert keys["day2/well_01.csv"] == keys["day2/well_02.csv"]
    assert keys["day2/well_01.csv"] not in wells_day1, "another folder is another series"
    assert (
        classes["day1/well_01.csv"] == (pool / "day1" / "well_01.csv").stat().st_size.bit_length()
    )
    assert tiers == {"heuristic"}


# -- the order ------------------------------------------------------------------------------------


def _sized(name: str, size: int, number: int) -> Entry:
    return Entry(
        entry_id=f"entry_{number:03d}",
        root_id="root_x",
        relative_path=name,
        name=name.rsplit("/", 1)[-1],
        entry_type=EntryType.FILE,
        extension=name.rsplit(".", 1)[-1],
        size_bytes=size,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


def test_one_file_of_every_series_comes_before_any_series_gets_a_second() -> None:
    entries = [
        *(_sized(f"a/plate_{n}.tif", 1000 + n, n) for n in range(5)),
        *(_sized(f"b/scan_{n}.png", 2000 + n, 10 + n) for n in range(3)),
        _sized("c/lone.csv", 50, 20),
    ]
    series = {e.entry_id: e.relative_path.split("/")[0] for e in entries}
    ordered = spend_order(entries, SpendOrder.SERIES_FIRST, series=lambda e: series[e.entry_id])
    keys = [series[e.entry_id] for e in ordered]
    # The first round: one of each series; the second: the last of each series with two or more.
    assert sorted(keys[:3]) == ["a", "b", "c"]
    assert sorted(keys[3:]) == ["a", "b"]
    assert len(ordered) == 5, "the rest of each series is not in the order at all"
    assert ordered[:3][keys[:3].index("a")].entry_id == "entry_000", "the first by identifier"
    assert ordered[3:][keys[3:].index("a")].entry_id == "entry_004", "then the last"


def test_a_file_whose_size_stands_out_is_read_with_the_first_two() -> None:
    entries = [_sized(f"a/plate_{n}.tif", 1000, n) for n in range(6)]
    entries[3] = _sized("a/plate_3.tif", 1900, 3)  # the same size class, half again as large
    ordered = spend_order(entries, SpendOrder.SERIES_FIRST, series=lambda e: "a")
    assert [e.entry_id for e in ordered] == ["entry_000", "entry_005", "entry_003"]


def test_picks_are_evenly_spaced_and_always_include_the_first_and_the_last() -> None:
    entries = [_sized(f"a/plate_{n}.tif", 1000, n) for n in range(9)]
    assert [e.entry_id for e in series_picks(entries, 2)] == ["entry_000", "entry_008"]
    assert [e.entry_id for e in series_picks(entries, 4)] == [
        "entry_000",
        "entry_003",
        "entry_005",
        "entry_008",
    ]
    assert len(series_picks(entries, 16)) == 9, "never more than there are"


def test_the_signature_is_what_two_files_are_compared_on() -> None:
    from ingence.core.schema import Observation, ValueKind

    def seen(key: str, text: str | None = None, integer: int | None = None) -> Observation:
        return Observation(
            observation_id=f"obs_{key}",
            entry_id="e",
            run_id="r",
            observer="o",
            observer_version="1",
            observed_at="t",
            key=key,
            value_kind=ValueKind.TEXT if text is not None else ValueKind.INTEGER,
            value_text=text,
            value_integer=integer,
        )

    signature = series_signature(
        [
            seen("type.media_type", "image/tiff"),
            seen("image.width", integer=512),
            seen("image.page_count", integer=3),
            seen("image.software", "an acquisition program"),
            seen("strings.run_count", integer=40),
            seen("text.character_count", integer=9),
        ]
    )
    assert signature == frozenset(
        {
            ("type.media_type", "image/tiff"),
            ("image.width", "512"),
            ("image.page_count", "3"),
            ("image.software", "an acquisition program"),
        }
    )


# -- the pass -------------------------------------------------------------------------------------


def _png(width: int) -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, 8, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
    return header + chunk + b"\x00" * 200


def _by_identifier(project: Project, relative_paths: list[str]) -> list[str]:
    """The paths in the order of their entry identifiers, as the series picks its first and last."""
    from ingence.core.ids import entry_id

    root = project.identity.roots[0]
    return sorted(
        relative_paths,
        key=lambda relative: entry_id(
            root.root_id, relative, (), case_sensitivity=root.case_sensitivity
        ),
    )


def test_a_series_that_agrees_is_read_twice_and_the_rest_are_left_to_it(tmp_path) -> None:
    pool = tmp_path / "pool"
    (pool / "stacks").mkdir(parents=True)
    frames = [f"stacks/frame_{number:02d}.png" for number in range(1, 7)]
    for relative in frames:
        (pool / relative).write_bytes(_png(64))
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(
            extraction=ExtractionSettings(enabled=True, spend_order=SpendOrder.SERIES_FIRST)
        ),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    report = run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=120.0
    )
    assert report.series_found == 1
    assert report.examined == 2, "the first and the last by identifier"
    assert report.left_to_series == 4
    assert report.series_rounds == 0
    assert report.budget is not None
    assert report.budget.unreached == 0, "left to the series is not unreached"
    with open_catalog(project.path) as catalog:
        outcomes = dict(
            catalog.query(
                "SELECT relative_path, scan_outcome FROM entries WHERE entry_type = 'file'"
            ).fetchall()
        )
        kept = catalog.query(
            "SELECT count(*) FROM observations WHERE key = ?", [SERIES_KEY]
        ).fetchone()[0]
    ordered = _by_identifier(project, frames)
    assert outcomes[ordered[0]] == "extracted"
    assert outcomes[ordered[-1]] == "extracted"
    assert [outcomes[relative] for relative in ordered[1:-1]] == ["metadata_only"] * 4
    assert kept == 6, "the series keys of the read files are carried, not dropped with the rest"


def test_a_series_whose_two_files_disagree_has_twice_as_many_read_until_it_is_read(
    tmp_path,
) -> None:
    pool = tmp_path / "pool"
    (pool / "mixed").mkdir(parents=True)
    items = [f"mixed/item_{number:02d}.dat" for number in range(1, 5)]
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(
            extraction=ExtractionSettings(enabled=True, spend_order=SpendOrder.SERIES_FIRST)
        ),
    )
    # One shape, one extension, one size class, two kinds of content: the first by identifier
    # is an image and the last is text, so the two disagree on what the file is and the series
    # is read on until it is read.
    ordered = _by_identifier(project, items)
    for position, relative in enumerate(ordered):
        image = position < len(ordered) // 2
        (pool / relative).write_bytes(_png(64) if image else b"a line of plain text\n" * 11)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    report = run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=120.0
    )
    assert report.series_found == 1
    assert report.series_rounds == 1
    assert report.examined == 4, "the first and the last disagreed, so all four were read"
    assert report.left_to_series == 0
    with open_catalog(project.path) as catalog:
        outcomes = {
            row[0]
            for row in catalog.query(
                "SELECT scan_outcome FROM entries WHERE entry_type = 'file'"
            ).fetchall()
        }
    assert ScanOutcome.METADATA_ONLY.value not in outcomes
