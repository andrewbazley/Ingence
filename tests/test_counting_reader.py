"""Exact reads at chosen offsets, each counted against the byte ceiling.

The reader instrument formats need, because their structure is not at the front of
the file. A corrupt file and a large one are different findings, and the reader
keeps them apart rather than handing a parser a short read.
"""

from __future__ import annotations

import io

import pytest

from ingence.extractors.limits import CountingReader, LimitExceededError, StructureBoundsError


def _reader(data: bytes, limit: int = 1 << 20) -> CountingReader:
    return CountingReader(io.BytesIO(data), limit=limit)


def test_a_read_returns_exactly_the_bytes_asked_for_and_counts_them() -> None:
    reader = _reader(b"0123456789")
    assert reader.size == 10
    assert reader.read_at(6, 3) == b"678"
    assert reader.read_at(0, 2) == b"01"
    assert reader.read_at(10, 0) == b""
    assert reader.bytes_read == 5


def test_a_structure_pointing_outside_the_file_is_refused_rather_than_read_short() -> None:
    """A parser handed three bytes where it asked for eight would take them for data."""
    reader = _reader(b"0123456789")
    with pytest.raises(StructureBoundsError):
        reader.read_at(8, 8)
    with pytest.raises(StructureBoundsError):
        reader.read_at(-1, 2)
    assert reader.bytes_read == 0


def test_a_read_past_the_ceiling_is_refused_before_a_byte_is_read() -> None:
    """A check after the fact has already paid the cost it exists to avoid."""
    reader = _reader(bytes(100), limit=10)
    reader.read_at(0, 8)
    with pytest.raises(LimitExceededError) as raised:
        reader.read_at(8, 8)
    assert raised.value.limit == "bytes_read"
    assert reader.bytes_read == 8


def test_a_claim_larger_than_the_file_is_a_corrupt_file_whatever_the_ceiling() -> None:
    """Reported as corruption first, because that is the finding however small the ceiling."""
    reader = _reader(bytes(10), limit=4)
    with pytest.raises(StructureBoundsError):
        reader.read_at(0, 1 << 40)


def test_the_size_is_measured_from_the_open_file(tmp_path) -> None:
    """Not the size the census recorded, because a file can change before it is read."""
    path = tmp_path / "sample.bin"
    path.write_bytes(bytes(10))
    with path.open("rb") as handle:
        reader = CountingReader(handle, limit=100)
        assert reader.size == 10
        assert reader.read_at(9, 1) == b"\x00"
