"""Type detection: several opinions, all kept, and their disagreement recorded."""

from __future__ import annotations

import os

import pytest

from ingence.extractors.detect import (
    DETECTORS,
    Agreement,
    ByteCharacter,
    detect,
    profile_bytes,
    sample_head_and_tail,
)


def write(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def extensions_of(name: str) -> frozenset[str]:
    from pathlib import PurePosixPath

    return frozenset({PurePosixPath(name).suffix})


def test_the_built_in_detectors_need_no_dependency():
    """A tool whose type detection needs a system library is a tool that fails."""
    assert {"signature", "extension"} <= set(DETECTORS.names(available_only=True))


def test_magic_bytes_beat_the_extension(tmp_path):
    """The finding this module exists for: a file whose name contradicts it."""
    path = write(tmp_path, "photo.txt", b"\xff\xd8\xff\xe0" + b"\x00" * 500)
    verdict = detect(path, extensions_of("photo.txt"), enabled=("signature", "extension"))
    assert verdict.media_type == "image/jpeg"
    assert verdict.chosen_by == "signature"
    assert verdict.extension_agrees is False
    assert verdict.contested


def test_every_opinion_is_kept_not_only_the_winner(tmp_path):
    """A file three detectors agreed on differs from one where two split."""
    path = write(tmp_path, "photo.txt", b"\xff\xd8\xff\xe0" + b"\x00" * 500)
    verdict = detect(path, extensions_of("photo.txt"), enabled=("signature", "extension"))
    assert {opinion.detector for opinion in verdict.opinions} == {"signature", "extension"}


def test_a_correctly_named_file_agrees(tmp_path):
    path = write(tmp_path, "real.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    verdict = detect(path, extensions_of("real.png"), enabled=("signature", "extension"))
    assert verdict.extension_agrees is True
    assert not verdict.contested


def test_a_more_specific_type_does_not_contradict_a_generic_one(tmp_path):
    """Markdown is plain text. Reporting that as conflict would flood a user.

    A finding that fires on every text file is a finding nobody reads, which
    would destroy the value of the one case that matters.
    """
    path = write(tmp_path, "notes.md", b"# Heading\n\nSome prose.\n" * 40)
    verdict = detect(path, extensions_of("notes.md"), enabled=("extension",))
    assert verdict.extension_agrees is not False


def test_nothing_is_invented_for_unrecognised_bytes(tmp_path):
    """Preferring unknown over unsupported certainty, checked."""
    path = write(tmp_path, "secret.bin", os.urandom(4096))
    verdict = detect(path, extensions_of("secret.bin"), enabled=("signature", "extension"))
    assert verdict.media_type is None
    assert verdict.agreement is Agreement.NONE


def test_high_entropy_is_not_reported_as_compressed_or_encrypted(tmp_path):
    """The three are indistinguishable from bytes alone, so none is claimed."""
    path = write(tmp_path, "x.bin", os.urandom(8192))
    verdict = detect(path, extensions_of("x.bin"), enabled=("signature",))
    assert verdict.profile is not None
    assert verdict.profile.character is ByteCharacter.HIGH_ENTROPY


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", ByteCharacter.EMPTY),
        (b"plain readable prose, mostly letters and spaces. " * 40, ByteCharacter.TEXT),
        ("wide text content here".encode("utf-16-le"), ByteCharacter.WIDE_TEXT),
        # Low entropy, unprintable, no null interleaving: structured binary.
        # Note that every byte value appearing equally often is *maximum*
        # entropy, so a full byte range is high entropy rather than binary.
        (b"\x80\x81\x82\x83" * 256, ByteCharacter.BINARY),
        (bytes(range(256)) * 4, ByteCharacter.HIGH_ENTROPY),
    ],
)
def test_byte_character_without_recognising_a_format(data, expected):
    assert profile_bytes(data).character is expected


def test_detection_reads_both_ends_of_a_file(tmp_path):
    """Several formats put their structure at the end; reading only the head misses it."""
    path = write(tmp_path, "big.bin", b"HEAD" + b"\x00" * 100_000 + b"TAIL")
    sample = sample_head_and_tail(path, size=64)
    assert sample.startswith(b"HEAD")
    assert sample.endswith(b"TAIL")


def test_detection_cost_does_not_scale_with_file_size(tmp_path):
    path = write(tmp_path, "huge.bin", b"\x00" * 2_000_000)
    assert len(sample_head_and_tail(path)) <= 2 * 8192


def test_restricting_detectors_is_how_routing_is_compared(tmp_path):
    """The experiment register compares one corpus under several routings."""
    path = write(tmp_path, "photo.txt", b"\xff\xd8\xff\xe0" + b"\x00" * 500)
    only_extension = detect(path, extensions_of("photo.txt"), enabled=("extension",))
    assert only_extension.media_type == "text/plain"
    assert only_extension.chosen_by == "extension"


def test_an_unreadable_file_is_an_error_the_caller_records_not_an_empty_opinion(tmp_path):
    """A file that cannot be opened is an error, not an empty file detected as nothing.

    A root that has stopped answering must not look like a pool of empty files. The content pass
    records the error against the file and counts it."""
    import pytest

    with pytest.raises(OSError):
        detect(tmp_path / "absent.txt", frozenset({".txt"}), enabled=("signature",))


def test_an_nd2_is_recognised_by_its_chunk_magic(tmp_path):
    """The magic was checked against a real file's first bytes, and it fits the name."""
    path = write(tmp_path, "stack.nd2", b"\xda\xce\xbe\x0a" + b"\x00" * 200)
    verdict = detect(path, extensions_of("stack.nd2"), enabled=("signature", "extension"))
    assert verdict.media_type == "application/x-nikon-nd2"
    assert verdict.chosen_by == "signature"
    assert verdict.extension_agrees is True


def test_an_li_flim_header_is_recognised_where_an_animation_named_fli_is_not(tmp_path):
    """Autodesk FLIC shares the extension, so only the header's bytes may say FLIM.

    Detection routes the animation by its name, and the FLIM extractor then declines it
    by content, which is the division of labour this module exists for.
    """
    flim = write(tmp_path, "lifetime.fli", b"{FLIMIMAGE}\n[INFO]\nversion = 1.0\n")
    verdict = detect(flim, extensions_of("lifetime.fli"), enabled=("signature", "extension"))
    assert verdict.media_type == "application/x-lambert-flim"
    assert verdict.chosen_by == "signature"
    assert verdict.extension_agrees is True

    animation = write(tmp_path, "cartoon.fli", b"\x00\x04\x00\x00\x12\xaf" + b"\x00" * 200)
    verdict = detect(animation, extensions_of("cartoon.fli"), enabled=("signature", "extension"))
    assert verdict.chosen_by == "extension"


@pytest.mark.parametrize("name", ["plate.oib", "slide.zvi"])
def test_ole_based_instrument_files_agree_with_their_container(tmp_path, name):
    """Olympus and Zeiss built these on compound storage, so that signature fits the name."""
    path = write(tmp_path, name, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 500)
    verdict = detect(path, extensions_of(name), enabled=("signature", "extension"))
    assert verdict.media_type == "application/x-ole-storage"
    assert verdict.extension_agrees is True
