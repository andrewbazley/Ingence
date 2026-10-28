"""An escaped space is a space: a name written through percent-encoding.

A web address, a download or a program writing an output file after another file's name writes each
space in it as ``%20`` (RFC 3986's percent-encoding). Read as written, ``kelt%2010min`` is one glued
word that binds nothing, where a person reads a word and a duration. The name's bytes stay as they
are; the reading treats the escaped space as a space, and every atom carries the name's own text and
position.

The class, written over the grammar of percent-encoding and never over the names that revealed it:
a name holding an escaped space and no bare percent sign is read with ``%20`` as a space and
``%25`` as a percent sign; no other escape is read, because an encoder writes no other escape of a
character the tokeniser divides at, and every other ``%`` and two hexadecimal digits measured was a
percentage glued to what followed it. Every name here is invented.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from ingence.core.schema import Entry, EntryType, ScanOutcome
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import read_name
from ingence.dictionary.normalize import AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore

STORE = DictionaryStore.builtin()
DEFAULT = Normaliser(STORE)


def _read(name: str) -> list[tuple[str, str]]:
    return [(atom.text, atom.kind.value) for atom in DEFAULT.atoms(name)]


def _entry(name: str) -> Entry:
    pure = PurePosixPath(name)
    return Entry(
        entry_id=f"entry_{name}",
        root_id="root_test",
        relative_path=name,
        name=name,
        entry_type=EntryType.FILE,
        extension=pure.suffix.casefold().removeprefix("."),
        size_bytes=0,
        mode=33188,
        nlink=1,
        modified_ns=0,
        changed_ns=0,
        resolution_status=ResolutionStatus.UNEXAMINED,
        first_seen_run="run_a",
        last_seen_run="run_a",
        scan_outcome=ScanOutcome.METADATA_ONLY,
    )


@pytest.mark.parametrize(
    ("escaped", "spaced"),
    [
        ("kelt%2010min", "kelt 10min"),
        ("Zorn_vorn3%20brisa-4.zib_crop", "Zorn_vorn3 brisa-4.zib_crop"),
        ("kelt%20brisa%20120min", "kelt brisa 120min"),
        ("20030407%20vorn", "20030407 vorn"),
    ],
)
def test_an_escaped_space_reads_as_a_space(escaped: str, spaced: str) -> None:
    """Every atom compares as it would with a space written, from a word to a date."""
    assert DEFAULT.comparable(escaped) == DEFAULT.comparable(spaced)
    assert [atom.kind for atom in DEFAULT.atoms(escaped)] == [
        atom.kind for atom in DEFAULT.atoms(spaced)
    ]


def test_the_escaped_space_divides_a_word_from_a_duration() -> None:
    """``kelt%2010min`` reads as a word and a duration, not as one glued word."""
    assert _read("kelt%2010min") == [("kelt", "word"), ("10min", "quantity")]


@pytest.mark.parametrize(
    "name", ["kelt%2010min", "vorn%205%25%20zib", "Zorn_kelt%20brisa%2030min-2.zib_crop"]
)
def test_every_atom_carries_the_names_own_text_and_position(name: str) -> None:
    """The bytes as read are the bytes kept: ``name[start:end]`` is exactly the atom's text."""
    for atom in DEFAULT.atoms(name):
        assert name[atom.start : atom.start + len(atom.text)] == atom.text


def test_an_escaped_percent_sign_in_an_encoded_name_is_a_percentage() -> None:
    """``%25`` is how an encoder writes a percent sign, so ``5%25`` is five per cent."""
    atoms = DEFAULT.atoms("vorn%205%25%20zib")
    assert [(atom.text, atom.kind) for atom in atoms] == [
        ("vorn", AtomKind.WORD),
        ("5%25", AtomKind.QUANTITY),
        ("zib", AtomKind.WORD),
    ]
    assert atoms[1].normalised == DEFAULT.atoms("5%")[0].normalised


@pytest.mark.parametrize(
    ("name", "misread"),
    [
        # A percentage glued to a word beginning 2D, and never an escaped hyphen: an encoder
        # leaves a hyphen, an underscore and a dot as they are.
        ("kelt%2Dvorn", "kelt-vorn"),
        ("kelt%5Fvorn", "kelt_vorn"),
        # An escaped percent sign followed by 20 holds no escaped space, and is read once.
        ("vorn%2520kelt", "vorn kelt"),
    ],
)
def test_a_name_holding_no_escaped_space_is_not_read_through_its_escapes(
    name: str, misread: str
) -> None:
    assert DEFAULT.comparable(name) != DEFAULT.comparable(misread)
    assert any("%" in atom.text for atom in DEFAULT.atoms(name))


def test_a_name_holding_a_bare_percent_sign_is_not_read_as_encoded() -> None:
    """An encoder writes a literal percent sign as ``%25``; a bare one says a person wrote it."""
    assert _read("5%_kelt%2010min") == [("5%", "quantity"), ("kelt%2010min", "word")]


def test_no_other_escape_is_read_in_an_encoded_name() -> None:
    """Brackets, commas and the rest were never measured escaped, and stay as they are written."""
    assert DEFAULT.comparable("kelt%20%28vorn%29") != DEFAULT.comparable("kelt (vorn)")


def test_the_layout_record_keeps_the_escape_as_its_separator() -> None:
    """The shape of the name points at the name's own characters, escape and all."""
    reading = read_name(_entry("kelt%2010min.zib"), run_id="run_a", normaliser=DEFAULT, store=STORE)
    assert reading.shape is not None
    separators = [slot.separator for slot in reading.shape.slots]
    assert separators[:2] == ["", "%20"]
    raw = {component.raw_value for component in reading.components}
    assert {"kelt", "10min"} <= raw


def test_the_reading_is_the_tokenisers_seventh() -> None:
    """A component records the reading that made it, so a re-read makes new components."""
    assert DEFAULT.version == "14"  # Tokeniser 14 rereads names.
