"""The mention pool carries only the columns resolution reads.

The graph's re-resolution selects only the columns it needs. The resolver reads eight fields, an
entry record requires six and a container's member two more. ``POOL_COLUMNS`` names them, the route
selects exactly those, and a pool built from entries that hold nothing else resolves every mention
as a pool built from whole entries does. Every name is invented.
"""

from __future__ import annotations

from typing import Any

import pytest

from ingence.core.ids import CaseSensitivity, LocatorStep
from ingence.core.schema import Entry, EntryKind, EntryType, Root
from ingence.infer.references import POOL_COLUMNS, Mention, PoolPaths, resolve_all


class _Only:
    """An entry exposing only the pool's columns: reading any other attribute fails."""

    def __init__(self, entry: Entry) -> None:
        self._held = {column: getattr(entry, column) for column in POOL_COLUMNS}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name == "_held":
            raise AttributeError(name)
        if name not in self._held:
            message = f"resolution read {name!r}, which the mention pool does not select"
            raise AssertionError(message)
        return self._held[name]


def _entry(entry_id: str, path: str, **extra: object) -> Entry:
    name = path.rsplit("/", 1)[-1]
    return Entry(
        entry_id=entry_id,
        root_id="root_a",
        relative_path=path,
        name=name,
        entry_type=EntryType.FILE,
        extension=name.rsplit(".", 1)[-1] if "." in name else "",
        size_bytes=40,
        modified_ns=1,
        first_seen_run="r",
        last_seen_run="r",
        **extra,
    )


def _entries() -> list[Entry]:
    member = _entry(
        "member",
        "kelt/bundle.zip",
        entry_kind=EntryKind.DERIVED,
        parent_entry_id="zip",
        locator_chain=(LocatorStep(scheme="zip", target="code/run.py"),),
        transformation="zip",
        ordinal=0,
    ).model_copy(update={"name": "code/run.py"})
    return [
        _entry("script", "kelt/analysis/run.py"),
        _entry("table", "kelt/analysis/vorn.csv"),
        _entry("far", "zib/trell.csv"),
        _entry("twin_a", "brisa/one/plate.csv"),
        _entry("twin_b", "brisa/two/plate.csv"),
        _entry("gone", "kelt/old.csv", is_deleted=True),
        Entry(
            entry_id="folder",
            root_id="root_a",
            relative_path="kelt/analysis",
            name="analysis",
            entry_type=EntryType.DIRECTORY,
            first_seen_run="r",
            last_seen_run="r",
        ),
        _entry("zip", "kelt/bundle.zip"),
        member,
    ]


MENTIONS = [
    Mention(entry_id="script", observer="text", path=path, shape=shape)
    for path, shape in (
        ("vorn.csv", "bare"),
        ("../../zib/trell.csv", "relative"),
        ("plate.csv", "bare"),
        ("/mnt/vorn/zib/trell.csv", "absolute_local"),
        ("C:\\elsewhere\\trell.csv", "absolute_foreign"),
        ("kelt/old.csv", "relative"),
        ("analysis", "bare"),
    )
] + [Mention(entry_id="member", observer="text", path="../analysis/vorn.csv", shape="relative")]


@pytest.mark.parametrize("sensitivity", [CaseSensitivity.SENSITIVE, CaseSensitivity.INSENSITIVE])
def test_a_pool_of_the_selected_columns_resolves_as_a_pool_of_whole_entries(sensitivity) -> None:
    roots = {
        "root_a": Root(
            root_id="root_a",
            root_uri="file:///mnt/vorn",
            mount_path="/mnt/vorn",
            case_sensitivity=sensitivity,
            first_seen_run="r",
            last_seen_run="r",
        )
    }
    whole = resolve_all(MENTIONS, PoolPaths.of(_entries(), roots))
    selected = resolve_all(
        MENTIONS,
        PoolPaths.of([_Only(entry) for entry in _entries()], roots),  # type: ignore[misc]
    )
    assert selected == whole
    assert {resolution.outcome for resolution in whole} >= {"in_folder", "rooted", "outside_pool"}


def test_the_pool_columns_rebuild_an_entry_record() -> None:
    required = {name for name, field in Entry.model_fields.items() if field.is_required()}
    assert required <= set(POOL_COLUMNS)
    assert set(POOL_COLUMNS) <= set(Entry.model_fields)


def test_the_graph_selects_the_pool_columns_and_no_others() -> None:
    from ingence.api.v1 import graph

    selected = [column.strip() for column in graph.MENTION_POOL_SELECT.split(",")]
    assert sorted(column.removesuffix("_json") for column in selected) == sorted(POOL_COLUMNS)


def test_a_container_member_rebuilds_from_the_selected_columns() -> None:
    """A derived entry must say where it sits and what made it, so those columns are selected too.
    Without them a container's member fails to rebuild."""
    from ingence.api.v1 import graph
    from ingence.storage.index_store import record_from_row, row_for
    from ingence.storage.layout import table_for

    selected = [column.strip() for column in graph.MENTION_POOL_SELECT.split(",")]
    for entry in _entries():
        row = row_for(entry, table_for(Entry))
        rebuilt = record_from_row({column: row[column] for column in selected}, Entry)
        assert all(getattr(rebuilt, column) == getattr(entry, column) for column in POOL_COLUMNS)
