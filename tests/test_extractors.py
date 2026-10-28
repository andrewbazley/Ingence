"""The extractor fleet: the byte-level baseline, text, tables and containers."""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile

import pytest

from ingence.core.relations import ExpansionState
from ingence.core.schema import ScanOutcome
from ingence.core.tiers import ExtractionTier
from ingence.extractors import load_builtins
from ingence.extractors.archive import ArchiveExtractor, materialise, sniff_container
from ingence.extractors.base import EXTRACTORS, ExtractionContext
from ingence.extractors.generic import (
    GenericExtractor,
    encode_term,
    harvest_strings,
    scan_for_terms,
)
from ingence.extractors.limits import Limits
from ingence.extractors.tabular import TabularExtractor, guess_column_type
from ingence.extractors.text import TextExtractor, decide_encoding

load_builtins()


def context_for(path, *, limits: Limits | None = None, **kwargs) -> ExtractionContext:
    from pathlib import PurePosixPath

    suffixes = PurePosixPath(path.name).suffixes
    extensions = {suffix.casefold() for suffix in suffixes}
    if len(suffixes) > 1:
        extensions.add("".join(suffixes[-2:]).casefold())
    return ExtractionContext(
        entry_id="entry_x",
        path=path,
        relative_path=path.name,
        extensions=frozenset(extensions),
        size_bytes=path.stat().st_size,
        limits=limits or Limits(),
        **kwargs,
    )


def readings_of(result) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}


# ---------------------------------------------------------------------------
# The registry.
# ---------------------------------------------------------------------------


def test_the_built_in_extractors_are_registered():
    assert {"generic", "text", "tabular", "archive"} <= set(EXTRACTORS.names())


def test_the_generic_extractor_claims_no_extension():
    """A universal fallback is in scope for every file, which is the point.

    Its version therefore enters every file's processing fingerprint, and that is
    correct: a change to how the baseline reads bytes genuinely does invalidate
    every record.
    """
    assert EXTRACTORS.declaration("generic").attributes.get("extensions", "") == ""


# ---------------------------------------------------------------------------
# Multi-encoding term scanning.
# ---------------------------------------------------------------------------


def test_encodings_producing_the_same_bytes_are_deduplicated():
    """Without this, an ordinary term is counted once per agreeing encoding."""
    patterns = encode_term("potato")
    assert len(patterns) == 5  # ascii, utf-8 and cp1252 collapse into one
    assert b"potato" in patterns
    assert patterns[b"potato"] == "ascii"


def test_a_term_is_found_in_several_encodings_and_counted_per_encoding():
    payload = b"potato in ascii " + "potato in wide".encode("utf-16-le") + b" and another potato"
    counts, _ = scan_for_terms(io.BytesIO(payload), ["potato"], limit=1 << 20)
    assert counts["potato"]["ascii"] == 2
    assert counts["potato"]["utf-16-le"] == 1


def test_a_term_spanning_a_chunk_boundary_is_still_found():
    """Chunks overlap by the longest pattern; without that this is missed."""
    filler = b"x" * 4096
    payload = filler + b"potato" + filler
    counts, _ = scan_for_terms(io.BytesIO(payload), ["potato"], limit=1 << 20, chunk_size=4098)
    assert counts["potato"]["ascii"] == 1


def test_the_fallback_search_agrees_with_the_automaton(monkeypatch):
    """The slow path exists so a base install works, not to behave differently."""
    from ingence.extractors import generic

    payload = b"potato " * 5 + "potato".encode("utf-16-be")
    fast, _ = scan_for_terms(io.BytesIO(payload), ["potato"], limit=1 << 20)
    monkeypatch.setattr(generic, "_build_automaton", lambda patterns: None)
    slow, _ = scan_for_terms(io.BytesIO(payload), ["potato"], limit=1 << 20)
    assert fast == slow


def test_no_terms_means_no_scan():
    """The scan is gated on the Dictionary, not on a language."""
    counts, read = scan_for_terms(io.BytesIO(b"anything"), [], limit=1 << 20)
    assert counts == {}
    assert read == 0


# ---------------------------------------------------------------------------
# String harvesting.
# ---------------------------------------------------------------------------


def test_printable_runs_are_harvested_with_their_offsets():
    """A run without a location is a claim nobody can check."""
    payload = b"\x00\x01readable text here\x00\xff"
    runs = harvest_strings(payload)
    assert runs[0].text == "readable text here"
    assert runs[0].offset == 2


def test_wide_text_is_harvested_and_reported_once():
    """A wide run matches both byte orders; counting both doubles every string."""
    payload = "wide text content".encode("utf-16-le")
    runs = harvest_strings(payload)
    assert len(runs) == 1
    assert runs[0].encoding == "utf-16-le"
    assert runs[0].text == "wide text content"


def test_short_runs_are_ignored():
    """Below the threshold, ordinary binary produces runs that mean nothing."""
    assert harvest_strings(b"\x00ab\x00cd\x00") == []


def test_the_generic_extractor_records_a_negative_when_there_is_no_text(tmp_path):
    """ "No readable text" narrows what a file can be. It is stored, not dropped."""
    path = tmp_path / "opaque.bin"
    path.write_bytes(bytes(range(8)) * 100)
    result = GenericExtractor().extract(context_for(path))
    assert "strings.none_found" in readings_of(result)


def test_a_match_in_the_original_file_is_literal(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("the potato is here")
    result = GenericExtractor().extract(context_for(path, terms=("potato",)))
    tiers = {reading.key: reading.extraction_tier for reading in result.readings}
    assert tiers["scan.term.potato.ascii"] is ExtractionTier.LITERAL


def test_a_match_inside_a_container_is_mechanically_exposed(tmp_path):
    """Bytes the engine produced by unpacking are one step from the original."""
    path = tmp_path / "notes.txt"
    path.write_text("x")
    context = context_for(path, terms=("potato",), depth=1, payload=b"the potato is here")
    result = GenericExtractor().extract(context)
    tiers = {reading.key: reading.extraction_tier for reading in result.readings}
    assert tiers["scan.term.potato.ascii"] is ExtractionTier.MECHANICALLY_EXPOSED


# ---------------------------------------------------------------------------
# Text.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "encoding", "evidence"),
    [
        (b"\xef\xbb\xbfhello", "utf-8-sig", "byte_order_mark"),
        ("hello there".encode("utf-16-le"), "utf-16-le", "null_interleaving"),
        ("héllo wörld".encode(), "utf-8", "valid_utf8"),
        (b"\x93smart quotes\x94 in windows text", "cp1252", "fallback"),
    ],
)
def test_encoding_is_decided_on_evidence_and_the_evidence_is_recorded(payload, encoding, evidence):
    """A decoding backed by a byte-order mark differs from one backed by a guess."""
    assert decide_encoding(payload) == (encoding, evidence)


def test_decoded_readings_are_not_literal(tmp_path):
    """The bytes were literal; the characters are a decoding that could be wrong."""
    path = tmp_path / "notes.txt"
    path.write_text("some prose here\nand a second line\n")
    result = TextExtractor().extract(context_for(path))
    tiers = {reading.key: reading.extraction_tier for reading in result.readings}
    assert tiers["text.encoding"] is ExtractionTier.LITERAL
    assert tiers["text.character_count"] is ExtractionTier.FORMALLY_EXTRACTED


def test_references_are_recorded_as_strings_not_resolved(tmp_path):
    """Whether a path resolves is an inference over the whole Index."""
    path = tmp_path / "script.py"
    path.write_text('open("data/raw/acquisition.csv")\nsave("results/out.csv")\n')
    result = TextExtractor().extract(context_for(path))
    assert "data/raw/acquisition.csv" in result.references
    assert "results/out.csv" in result.references


def test_an_empty_file_is_recorded_as_empty_rather_than_failing(tmp_path):
    path = tmp_path / "nothing.txt"
    path.write_text("")
    result = TextExtractor().extract(context_for(path))
    assert result.outcome is ScanOutcome.EXTRACTED
    assert "text.empty" in readings_of(result)


# ---------------------------------------------------------------------------
# Tables.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1", "2", "30"], "integer"),
        (["1.5", "2e3", "-0.1"], "real"),
        (["2026-01-04", "2026-02-01"], "date"),
        (["true", "no", "1"], "boolean"),
        (["a1", "b2"], "text"),
        (["", "  ", ""], "empty"),
        (["1", "", "2"], "integer"),
    ],
)
def test_column_types_are_guessed_from_a_sample(values, expected):
    """Blanks are gaps in a column, not a type of their own."""
    assert guess_column_type(values) == expected


def test_a_table_reports_its_columns_and_shape(tmp_path):
    path = tmp_path / "results.csv"
    path.write_text("sample,dose,when\nA1,800,2026-01-04\nA2,0,2026-01-05\n")
    result = TabularExtractor().extract(context_for(path))
    values = readings_of(result)
    assert values["table.column_count"] == 3
    assert '"sample"' in str(values["table.column_names"])
    assert '"dose":"integer"' in str(values["table.column_types"])
    assert values["table.row_count"] == 2


def test_column_types_are_recorded_as_guesses(tmp_path):
    """A column of digits might be a measurement, or a barcode that overflows."""
    path = tmp_path / "results.csv"
    path.write_text("a,b\n1,2\n")
    result = TabularExtractor().extract(context_for(path))
    tiers = {reading.key: reading.extraction_tier for reading in result.readings}
    assert tiers["table.column_types"] is ExtractionTier.HEURISTIC
    assert tiers["table.column_names"] is ExtractionTier.FORMALLY_EXTRACTED


def test_a_single_column_is_declined_rather_than_called_a_table(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text("value\n1\n2\n")
    result = TabularExtractor().extract(context_for(path))
    assert result.outcome is ScanOutcome.SKIPPED_BY_POLICY


def test_ragged_rows_are_surfaced(tmp_path):
    """Usually a delimiter inside an unquoted field, which corrupts the next reader."""
    path = tmp_path / "broken.csv"
    path.write_text("a,b,c\n1,2,3\n4,5\n6,7,8,9\n")
    result = TabularExtractor().extract(context_for(path))
    assert readings_of(result)["table.ragged_rows"] == 2


def test_a_row_count_over_a_truncated_read_is_marked_inexact(tmp_path):
    path = tmp_path / "big.csv"
    path.write_text("a,b\n" + "1,2\n" * 5000)
    result = TabularExtractor().extract(context_for(path, limits=Limits(max_bytes_read=200)))
    values = readings_of(result)
    assert values["table.row_count_is_exact"] is False
    assert "table.row_count" not in values


# ---------------------------------------------------------------------------
# Containers.
# ---------------------------------------------------------------------------


def make_zip(path, members: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


def test_a_zip_yields_its_members_with_content_hashes(tmp_path):
    path = make_zip(
        tmp_path / "data.zip",
        {"readme.txt": b"hello", "nested/results.csv": b"a,b\n1,2\n"},
    )
    result = ArchiveExtractor().extract(context_for(path))
    assert len(result.derived) == 2
    assert all(stream.content_hash for stream in result.derived)
    assert readings_of(result)["archive.expansion_state"] == "complete"


def test_a_tar_and_a_single_stream_both_expand(tmp_path):
    tar_path = tmp_path / "bundle.tar"
    with tarfile.open(tar_path, "w") as archive:
        info = tarfile.TarInfo("one.txt")
        info.size = 5
        archive.addfile(info, io.BytesIO(b"first"))
    assert len(ArchiveExtractor().extract(context_for(tar_path)).derived) == 1

    gzip_path = tmp_path / "notes.txt.gz"
    gzip_path.write_bytes(gzip.compress(b"decompressed"))
    streams = ArchiveExtractor().extract(context_for(gzip_path)).derived
    assert len(streams) == 1
    assert streams[0].locator[-1].target == "notes.txt"


def test_container_kind_comes_from_content_not_the_name(tmp_path):
    """A stated constraint: handle unknown and misleading extensions."""
    path = make_zip(tmp_path / "actually.zip.txt", {"inside.csv": b"a,b\n"})
    result = ArchiveExtractor().extract(context_for(path))
    assert [stream.locator[-1].target for stream in result.derived] == ["inside.csv"]


def test_a_compressed_tar_expands_in_two_steps_whatever_it_is_called(tmp_path):
    """Provenance that changes shape with a filename is provenance nobody can rely on."""
    path = tmp_path / "bundle.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("one.txt")
        info.size = 5
        archive.addfile(info, io.BytesIO(b"first"))
    streams = ArchiveExtractor().extract(context_for(path)).derived
    assert [stream.locator[-1].target for stream in streams] == ["bundle.tar"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"PK\x03\x04rest", "zip"),
        (b"\x1f\x8brest", "gzip"),
        (b"BZhrest", "bzip2"),
        (b"\x00" * 257 + b"ustar\x00", "tar"),
        (b"not a container at all", None),
    ],
)
def test_containers_are_sniffed_from_their_bytes(payload, expected):
    assert sniff_container(payload) == expected


def test_a_traversing_member_refuses_the_whole_container(tmp_path):
    """An archive containing a traversal is a finding, not a mess to tidy away."""
    path = make_zip(tmp_path / "evil.zip", {"../../etc/passwd": b"root:x:0:0"})
    result = ArchiveExtractor().extract(context_for(path))
    assert readings_of(result)["archive.expansion_state"] == ExpansionState.REFUSED.value
    assert "outside its container" in result.warnings[0]


def test_a_compression_bomb_is_stopped_and_the_state_says_bounded(tmp_path):
    path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeros.bin", b"\x00" * 2_000_000)
    result = ArchiveExtractor().extract(context_for(path, limits=Limits(max_expansion_ratio=5.0)))
    assert readings_of(result)["archive.expansion_state"] == ExpansionState.BOUNDED.value


def test_expansion_stops_at_the_depth_limit(tmp_path):
    path = make_zip(tmp_path / "data.zip", {"a.txt": b"x"})
    result = ArchiveExtractor().extract(context_for(path, limits=Limits(max_expansion_depth=0)))
    assert result.outcome is ScanOutcome.SKIPPED_BY_POLICY
    assert readings_of(result)["archive.expansion_state"] == ExpansionState.BOUNDED.value


def test_nothing_is_written_to_disk_while_expanding(tmp_path):
    """Members are read into memory and discarded. No temporary directory at all."""
    path = make_zip(tmp_path / "data.zip", {"a.txt": b"x", "b/c.txt": b"y"})
    before = sorted(item.name for item in tmp_path.iterdir())
    ArchiveExtractor().extract(context_for(path))
    assert sorted(item.name for item in tmp_path.iterdir()) == before


def test_a_member_can_be_read_again_by_following_its_locator(tmp_path):
    """The other half of the cascade: a discovered member stays examinable."""
    path = make_zip(tmp_path / "data.zip", {"nested/results.csv": b"a,b\n1,2\n"})
    result = ArchiveExtractor().extract(context_for(path))
    stream = result.derived[0]
    assert materialise(path, stream.locator, limit=1 << 20) == b"a,b\n1,2\n"


def test_a_locator_that_no_longer_resolves_returns_nothing(tmp_path):
    """A missing member is a fact about the container, not an error in the reader."""
    from ingence.core.ids import LocatorStep

    path = make_zip(tmp_path / "data.zip", {"present.txt": b"x"})
    chain = (LocatorStep(scheme="zip", target="absent.txt"),)
    assert materialise(path, chain, limit=1 << 20) is None


def test_a_corrupt_container_fails_that_file_and_nothing_else(tmp_path):
    path = tmp_path / "broken.zip"
    path.write_bytes(b"PK\x03\x04" + b"\xff" * 200)
    result = ArchiveExtractor().extract(context_for(path))
    assert result.outcome is ScanOutcome.CONTENT_ERROR
    assert result.error_type


# ---------------------------------------------------------------------------
# Mentions, found in linear time.
# ---------------------------------------------------------------------------


def _found_by_the_pattern(payload: bytes) -> list[bytes]:
    from ingence.extractors.mentions import _REFERENCE

    return [group for match in _REFERENCE.findall(payload) for group in match if group]


def test_the_linear_harvest_finds_what_the_pattern_finds_over_generated_strings():
    """The pattern is the definition; the harvest must agree with it string for string.

    Generated over the grammar the pattern reads, quotes, separators, dots, a colon and a
    newline among name characters, never harvested from anybody's files. A corpus is a
    tripwire, and this one is the pattern itself.
    """
    import random

    from ingence.extractors.mentions import _mentions

    rng = random.Random(20260921)
    alphabets = (b"aB1._-/\\'\" :\nCx", b"a./\\'\"", b"ab/.c", b"/.a1\"'")
    for trial in range(8000):
        alphabet = alphabets[trial % len(alphabets)]
        payload = bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 70)))
        assert list(_mentions(payload)) == _found_by_the_pattern(payload), payload


@pytest.mark.parametrize(
    "payload",
    [
        b'read "data/raw/plate_01.csv" and write results\\summary.txt',
        b"#include <sys/types.h>\nimport pkg.mod\n",
        b"a//b/c.txt and x/y.abcdefghijkl and ._/.b and a/b.",
        b"'/abs/path/file.tif' 'C:\\Users\\name\\x.nd2' \"unterminated/a.b",
        b"/usr/include/stdio.h e/f.g-h/i.jk",
    ],
)
def test_the_linear_harvest_agrees_with_the_pattern_on_written_cases(payload):
    from ingence.extractors.mentions import _mentions

    assert list(_mentions(payload)) == _found_by_the_pattern(payload)


def test_a_long_run_of_path_characters_is_read_in_linear_time():
    """A file of one long run of path characters, as a bug class.

    The pattern retried the rest of a run from each of its characters, so 8,000 of them
    cost a second and 400,000 would cost hours. The file reads in well under a second.
    """
    import time

    from ingence.extractors.mentions import mentioned_paths

    started = time.monotonic()
    mentioned_paths(b"0123456789abcdef/" * 24_000 + b"a." * 200_000)
    assert time.monotonic() - started < 5.0


# ---------------------------------------------------------------------------
# A mention's shape, read off its own line.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b"#include <sys/types.h>", "code_include"),
        (b'  #import "Widgets/Button.h"', "code_include"),
        (b'import Qt.labs/settings.qml "x"', "code_include"),
        (b"from pkg.mod import thing  # see pkg/mod.py", "code_include"),
        (b'@import url("css/base.css");', "code_include"),
        (b'const x = require("lib/util.js")', "code_include"),
        (b'source("helpers/util.R")', "code_include"),
        (b"addpath('toolbox/io.m')", "code_include"),
        (b"\\input{sections/methods.tex}", "code_include"),
        (b'data = read("/Volumes/Lab/plates/p1.csv")', "absolute"),
        (b'path = "C:\\\\Users\\\\x\\\\p1.nd2"', "absolute"),
        (b"see /usr/share/lab/protocol.txt for the steps", "absolute"),
        (b"results are in out/plots/fig1.png", "relative"),
        (b"we require data/raw/p1.csv first", "relative"),
    ],
)
def test_a_mention_carries_the_shape_its_line_gives_it(line, expected):
    from ingence.extractors.mentions import mentioned_paths_and_shapes

    paths, shapes = mentioned_paths_and_shapes(line + b"\n")
    assert len(paths) == 1, paths
    assert shapes == (expected,)


def test_a_path_named_in_an_include_and_elsewhere_keeps_the_strongest_shape():
    from ingence.extractors.mentions import mentioned_paths_and_shapes

    payload = b'// the header lib/core.h is ours\n#include "lib/core.h"\n'
    assert mentioned_paths_and_shapes(payload) == (("lib/core.h",), ("code_include",))


def test_shapes_leave_the_paths_found_exactly_as_they_were():
    """The shapes are beside the paths; the paths are the pattern's, string for string."""
    import random

    from ingence.extractors.mentions import mentioned_paths, mentioned_paths_and_shapes

    rng = random.Random(3)
    for _ in range(500):
        payload = bytes(rng.choice(b"a/.\\n#'\"imc lude") for _ in range(rng.randint(0, 80)))
        paths, shapes = mentioned_paths_and_shapes(payload)
        assert paths == mentioned_paths(payload)
        assert len(shapes) == len(paths)


def test_a_number_after_a_slash_is_not_a_mention_and_cannot_crowd_one_out():
    """The census's extension rule applies to the harvest.

    A cytometry workspace writes gate and ratio expressions as ``name/1.0000`` by the
    thousand. Read as relative paths they fill a file's bounded list of mentions ahead of its
    real data references and resolve to nothing. Digits alone after the last dot are a
    number's decimals, for the harvest exactly as for the census.
    """
    from ingence.extractors.mentions import MAX_MENTIONS, mentioned_paths

    assert mentioned_paths(b"<Gate ratio='Comp-FL1-A/1.0000'/>\n") == ()
    assert mentioned_paths(b"<Gate x='Kelt/0.5'/>\n<File uri='data/zorn_01.fcs'/>\n") == (
        "data/zorn_01.fcs",
    )
    crowd = b"".join(
        b"<G v='Vorn%d/0.%04d'/>\n" % (index, index) for index in range(3 * MAX_MENTIONS)
    )
    assert mentioned_paths(crowd + b"<File uri='zz/zorn_99.fcs'/>\n") == ("zz/zorn_99.fcs",)
    # A rooted mention is quoted and needs no extension.
    assert mentioned_paths(b'path = "/Volumes/Lab/run/1.0"\n') == ("/Volumes/Lab/run/1.0",)


def test_markup_on_one_line_is_not_a_rooted_path():
    """A quoted string that starts with a slash is markup when it holds ``<`` or ``>``.

    Single-line markup puts one element's closing quote, ``/>`` and the next element's opening
    quote on one line, and the quoted-rooted pattern read the span between as another machine's
    path. On a real drive this was most of what a dive recorded as outside the pool.
    """
    from ingence.extractors.mentions import mentioned_paths

    assert mentioned_paths(b'<a:b c="d"/></a:b><e f="g"/>\n') == ()
    assert mentioned_paths(b'path = "/Volumes/Lab/zorn plate/run 1.csv"\n') == (
        "/Volumes/Lab/zorn plate/run 1.csv",
    )


def test_mentions_on_one_long_line_are_read_in_linear_time():
    """A mention's line is found incrementally, not by searching back to the start of the payload.

    The search back is quadratic on a file whose content is one line -- minified markup, a long
    JSON document -- and on a real drive it was most of the text extractor's time.
    """
    import time

    from ingence.extractors.mentions import mentioned_paths, mentioned_paths_and_shapes

    payload = b" ".join(b"dir%d/file%d.csv" % (index, index) for index in range(60_000))
    started = time.monotonic()
    paths, _ = mentioned_paths_and_shapes(payload)
    assert time.monotonic() - started < 5.0
    assert paths == mentioned_paths(payload)
    # A line bringing in code is still read as one, wherever it falls among others.
    mixed = b'x = "a/b.csv"\n#include "lib/core.h"\ny = "c/d.csv"\n'
    assert mentioned_paths_and_shapes(mixed) == (
        ("a/b.csv", "c/d.csv", "lib/core.h"),
        ("relative", "relative", "code_include"),
    )
