"""The five identities: what each proves, and what none of them may claim."""

from __future__ import annotations

import numpy as np
import pytest

from ingence.core.schema import IdentityScope
from ingence.extractors.base import ExtractionContext
from ingence.identity import IDENTITY_METHODS, load_builtins
from ingence.identity.perceptual import SKETCH_PERMUTATIONS, shingles
from ingence.identity.semantic import signature_of

# The sketch methods live behind an optional extra. The rest of the identity
# suite must still run on a base install, so this skips rather than failing
# collection.
MinHash = pytest.importorskip("datasketch").MinHash

load_builtins()


def context_for(path, media_type: str | None = None) -> ExtractionContext:
    from pathlib import PurePosixPath

    return ExtractionContext(
        entry_id="entry_x",
        path=path,
        relative_path=path.name,
        extensions=frozenset({PurePosixPath(path.name).suffix}),
        size_bytes=path.stat().st_size,
        media_type=media_type,
    )


def write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The rule the whole set rests on.
# ---------------------------------------------------------------------------


def test_exactly_one_identity_is_substitutive():
    """Only byte identity licenses transferring a fact between two entries.

    Two tables with one schema are not interchangeable and two experiments under
    one condition certainly are not. If this ever finds a second substitutive
    method, something has claimed a power it does not have.
    """
    substitutive = [
        name
        for name in IDENTITY_METHODS.names(available_only=True)
        if IDENTITY_METHODS.get(name).substitutive
    ]
    assert substitutive == ["blake3_whole"]


def test_a_sampled_digest_says_it_is_sampled(tmp_path):
    """A partial hash never asserts identity. This is a hard rule."""
    path = write(tmp_path, "a.txt", "x" * 500_000)
    quick = IDENTITY_METHODS.get("blake3_quick").compute(context_for(path))
    assert quick is not None
    assert quick.scope is IdentityScope.SAMPLED
    assert quick.substitutive is False


def test_a_whole_digest_says_it_is_whole(tmp_path):
    path = write(tmp_path, "a.txt", "content")
    whole = IDENTITY_METHODS.get("blake3_whole").compute(context_for(path))
    assert whole is not None
    assert whole.scope is IdentityScope.WHOLE
    assert whole.substitutive is True
    assert whole.bytes_read == path.stat().st_size


# ---------------------------------------------------------------------------
# Byte identity.
# ---------------------------------------------------------------------------


def test_identical_bytes_give_identical_digests(tmp_path):
    one = write(tmp_path, "one.txt", "the same content")
    two = write(tmp_path, "two.txt", "the same content")
    method = IDENTITY_METHODS.get("blake3_whole")
    assert method.compute(context_for(one)).value == method.compute(context_for(two)).value


def test_a_difference_anywhere_changes_the_digest(tmp_path):
    """The negative is the valuable half: it prunes candidates permanently."""
    method = IDENTITY_METHODS.get("blake3_whole")
    one = write(tmp_path, "one.txt", "a" * 1000 + "X" + "a" * 1000)
    two = write(tmp_path, "two.txt", "a" * 1000 + "Y" + "a" * 1000)
    assert method.compute(context_for(one)).value != method.compute(context_for(two)).value


def test_a_quick_digest_can_miss_a_middle_difference(tmp_path):
    """Why it may never prove identity, demonstrated rather than asserted."""
    quick = IDENTITY_METHODS.get("blake3_quick")
    whole = IDENTITY_METHODS.get("blake3_whole")
    filler = "a" * 200_000
    one = write(tmp_path, "one.txt", filler + "X" + filler)
    two = write(tmp_path, "two.txt", filler + "Y" + filler)
    assert quick.compute(context_for(one)).value == quick.compute(context_for(two)).value
    assert whole.compute(context_for(one)).value != whole.compute(context_for(two)).value


def test_size_is_part_of_the_quick_digest(tmp_path):
    quick = IDENTITY_METHODS.get("blake3_quick")
    short = write(tmp_path, "short.txt", "abc")
    longer = write(tmp_path, "longer.txt", "abcabc")
    assert quick.compute(context_for(short)).value != quick.compute(context_for(longer)).value


def test_an_unreadable_file_yields_no_identity_rather_than_a_wrong_one(tmp_path):
    method = IDENTITY_METHODS.get("blake3_whole")
    missing = tmp_path / "absent.txt"
    context = ExtractionContext(
        entry_id="e",
        path=missing,
        relative_path="absent.txt",
        extensions=frozenset({".txt"}),
        size_bytes=0,
    )
    assert method.compute(context) is None


# ---------------------------------------------------------------------------
# Structural identity.
# ---------------------------------------------------------------------------


def test_two_exports_of_one_schema_agree(tmp_path):
    method = IDENTITY_METHODS.get("tabular_schema")
    one = write(tmp_path, "one.csv", "Sample ID, Treatment ,DOSE\n1,a,5\n")
    two = write(tmp_path, "two.csv", "sample id,treatment,dose\n99,z,1\n")
    assert method.compute(context_for(one)).value == method.compute(context_for(two)).value


def test_a_reordered_table_is_a_different_schema(tmp_path):
    """A reordered table is a different table to anybody joining it."""
    method = IDENTITY_METHODS.get("tabular_schema")
    one = write(tmp_path, "one.csv", "a,b,c\n1,2,3\n")
    two = write(tmp_path, "two.csv", "c,b,a\n3,2,1\n")
    assert method.compute(context_for(one)).value != method.compute(context_for(two)).value


def test_a_renamed_column_is_a_different_schema(tmp_path):
    method = IDENTITY_METHODS.get("tabular_schema")
    one = write(tmp_path, "one.csv", "a,b\n1,2\n")
    two = write(tmp_path, "two.csv", "a,B_RENAMED\n1,2\n")
    assert method.compute(context_for(one)).value != method.compute(context_for(two)).value


def test_a_single_column_is_not_a_schema(tmp_path):
    """Every line of every text file would otherwise produce a signature."""
    method = IDENTITY_METHODS.get("tabular_schema")
    path = write(tmp_path, "one.csv", "just_one_column\nvalue\n")
    assert method.compute(context_for(path)) is None


def test_structural_identity_reads_only_a_header(tmp_path):
    method = IDENTITY_METHODS.get("tabular_schema")
    path = write(tmp_path, "big.csv", "a,b,c\n" + "1,2,3\n" * 200_000)
    result = method.compute(context_for(path))
    assert result is not None
    assert result.scope is IdentityScope.HEADER
    assert result.bytes_read < path.stat().st_size


# ---------------------------------------------------------------------------
# Perceptual identity.
# ---------------------------------------------------------------------------


def sketch(method, path) -> MinHash:
    result = method.compute(context_for(path))
    assert result is not None
    value = MinHash(num_perm=SKETCH_PERMUTATIONS)
    value.hashvalues = np.frombuffer(bytes.fromhex(result.value), dtype=np.uint64).copy()
    return value


@pytest.mark.skipif(
    not IDENTITY_METHODS.available("text_minhash"), reason="datasketch not installed"
)
def test_a_small_edit_leaves_text_recognisably_similar(tmp_path):
    method = IDENTITY_METHODS.get("text_minhash")
    prose = (
        "Phosphate quantification was performed on lysates from three replicates. "
        "Absorbance was measured at 620 nanometres and normalised to the control. "
    ) * 8
    original = write(tmp_path, "one.txt", prose)
    edited = write(tmp_path, "two.txt", prose.replace("620", "595"))
    unrelated = write(tmp_path, "three.txt", "Chromatin libraries were sequenced. " * 30)

    assert sketch(method, original).jaccard(sketch(method, edited)) > 0.6
    assert sketch(method, original).jaccard(sketch(method, unrelated)) < 0.1


@pytest.mark.skipif(
    not IDENTITY_METHODS.available("text_minhash"), reason="datasketch not installed"
)
def test_the_sketch_is_taken_in_batches_and_is_the_sketch_taken_one_shingle_at_a_time(tmp_path):
    """Batching moved a table's sketch from 11 s to 1 s and must move nothing else.

    More shingles than one batch, so at least two batches are taken, and the digest is
    held to the one ``MinHash.update`` gives shingle by shingle, which is what version 1
    stores.
    """
    import random

    from ingence.identity.perceptual import UPDATE_BATCH

    rng = random.Random(7)
    rows = "\n".join(
        f"{index},{rng.uniform(-500, 500):.4f},{rng.uniform(0, 65535):.2f}" for index in range(900)
    )
    path = write(tmp_path, "table.csv", "t,x,intensity\n" + rows)
    windows = sorted(shingles(path.read_text(encoding="utf-8")))
    assert len(windows) > 2 * UPDATE_BATCH

    one_at_a_time = MinHash(num_perm=SKETCH_PERMUTATIONS)
    for window in windows:
        one_at_a_time.update(window)
    result = IDENTITY_METHODS.get("text_minhash").compute(context_for(path))
    assert result is not None
    assert result.value == one_at_a_time.digest().tobytes().hex()


def test_shingles_survive_reflowing_and_recasing():
    """Two exports differing only in whitespace must not look different."""
    assert shingles("The  Quick\nBrown Fox") == shingles("the quick brown fox")


def test_shingles_of_text_shorter_than_a_window_are_empty():
    assert shingles("short") == set()


# ---------------------------------------------------------------------------
# Semantic identity.
# ---------------------------------------------------------------------------


def test_component_order_does_not_change_the_signature():
    """A component set has no order; hashing discovery order would vary per run."""
    first = signature_of([("dose", "800ng/ml"), ("line", "hela"), ("time", "2.5h")])
    second = signature_of([("time", "2.5h"), ("dose", "800ng/ml"), ("line", "hela")])
    assert first == second


def test_a_repeated_component_says_nothing_extra():
    """A verbose name must not become a different experiment from a terse one."""
    once = signature_of([("line", "hela"), ("dose", "5")])
    twice = signature_of([("line", "hela"), ("line", "hela"), ("dose", "5")])
    assert once == twice


def test_semantic_identity_reports_nothing_until_components_exist(tmp_path):
    """Inventing one from raw filename fragments would be a fabricated fact."""
    method = IDENTITY_METHODS.get("semantic_signature")
    path = write(tmp_path, "SubX_800ngmL_2.5h_r1.tif", "x")
    assert method.compute(context_for(path)) is None


@pytest.mark.skipif(
    not IDENTITY_METHODS.available("text_minhash"), reason="datasketch not installed"
)
def test_a_text_sketch_reads_the_sample_its_setting_names_and_its_version_says_so(tmp_path):
    """The sketch's cost grows with the bytes it reads, so the sample is a setting whose default
    was measured on a real dive. A sketch of another sample is another sketch."""
    from dataclasses import replace

    from ingence.core.settings import ExtractionSettings
    from ingence.extractors.limits import Limits

    path = write(tmp_path, "vorn.log", "".join(f"kelt line {n} tamen\n" for n in range(40_000)))
    assert path.stat().st_size > (512 << 10)
    method = IDENTITY_METHODS.get("text_minhash")
    default = Limits.from_settings(ExtractionSettings())
    assert default.text_sketch_bytes == 256 << 10
    bounded = method.compute(replace(context_for(path), limits=default))
    assert bounded.bytes_read == 256 << 10
    assert bounded.method_version == f"{method.version}+{256 << 10}"
    whole = method.compute(
        replace(context_for(path), limits=replace(default, text_sketch_bytes=1 << 20))
    )
    assert whole.bytes_read == path.stat().st_size and whole.method_version == method.version


@pytest.mark.skipif(
    not IDENTITY_METHODS.available("image_dhash"), reason="imagehash or Pillow not installed"
)
def test_an_image_hash_reads_through_the_one_readable_stream_and_maps_nothing(
    tmp_path, monkeypatch
):
    """Pillow memory-maps an uncompressed image it is given by name (a second open and an
    ``mmap``); on a share mounted over SMB every TIFF a dive hashed that way had its change time
    moved, where no file read as a stream did. The hash reads the stream the extractor opens
    instead: the same pixels, the same value, and nothing mapped."""
    import mmap

    from PIL import Image

    path = tmp_path / "plain.tif"
    pixels = np.arange(64 * 48, dtype=np.uint8).reshape(48, 64)
    Image.fromarray(pixels).save(path, compression=None)
    method = IDENTITY_METHODS.get("image_dhash")
    with Image.open(path) as image:
        expected = str(method._imagehash.dhash(image, hash_size=16))

    def refuse_map(*args, **kwargs):
        raise AssertionError("an image was memory-mapped")

    monkeypatch.setattr(mmap, "mmap", refuse_map)
    result = method.compute(context_for(path))
    assert result is not None
    assert result.value == expected


@pytest.mark.skipif(
    not IDENTITY_METHODS.available("image_dhash"), reason="imagehash or Pillow not installed"
)
@pytest.mark.parametrize("compression", ["tiff_lzw", "tiff_adobe_deflate"])
def test_a_compressed_tiff_is_hashed_through_a_stream_with_no_descriptor(
    tmp_path, monkeypatch, compression
):
    """Libtiff decodes a compressed TIFF, and Pillow hands it the stream's file descriptor, which
    libtiff memory-maps itself, in C, out of reach of Python's ``mmap``. The stream Pillow is given
    has no descriptor, so libtiff is handed the bytes and maps nothing; the value is the one the
    file gives by name."""
    from PIL import Image

    path = tmp_path / f"{compression}.tif"
    pixels = np.arange(64 * 48, dtype=np.uint16).reshape(48, 64) * 17
    Image.fromarray(pixels).save(path, compression=compression)
    method = IDENTITY_METHODS.get("image_dhash")
    with Image.open(path) as image:
        expected = str(method._imagehash.dhash(image, hash_size=16))
    real_open = Image.open
    handed: list[bool] = []

    class _Spy:
        @staticmethod
        def open(stream, *args, **kwargs):
            try:
                stream.fileno()
            except OSError:
                handed.append(False)
            else:
                handed.append(True)
            return real_open(stream, *args, **kwargs)

    monkeypatch.setattr(method, "_image", _Spy)
    result = method.compute(context_for(path))
    assert handed == [False]  # no descriptor for libtiff to map
    assert result is not None
    assert result.value == expected


@pytest.mark.skipif(
    not IDENTITY_METHODS.available("image_dhash"), reason="imagehash or Pillow not installed"
)
def test_an_image_larger_than_the_depth_s_sample_gets_no_picture_fingerprint(tmp_path) -> None:
    """The hash decodes every pixel, so a large stitched image would be read whole over a
    share. Above the depth's sample (``Limits.max_bytes_read``) it is not hashed, and nothing
    is read; at or below it, it is."""
    from dataclasses import replace

    from PIL import Image

    from ingence.extractors.limits import Limits

    path = tmp_path / "plate.tif"
    pixels = np.arange(64 * 48, dtype=np.uint8).reshape(48, 64)
    Image.fromarray(pixels).save(path, compression=None)
    method = IDENTITY_METHODS.get("image_dhash")
    size = path.stat().st_size
    below = replace(context_for(path), limits=Limits(max_bytes_read=size - 1))
    at = replace(context_for(path), limits=Limits(max_bytes_read=size))
    assert method.compute(below) is None
    result = method.compute(at)
    assert result is not None and result.bytes_read == size
