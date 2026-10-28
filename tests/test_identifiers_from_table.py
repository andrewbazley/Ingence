"""Public archives' accessions are identifiers.

The file-type table lists the accession patterns; the tokeniser keeps each whole, as one
identifier, and the binder records that it declined rather than looking it up, so none reaches
the Words queue. Every name here is invented; the accession shapes are the archives' own.
"""

from __future__ import annotations

import pytest

from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import IDENTIFIER_CONSTRAINT, read_name
from ingence.dictionary.file_types import identifiers
from ingence.dictionary.normalize import AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore
from test_discover import entry_named

STORE = DictionaryStore.builtin()
NORMALISER = Normaliser(STORE)

ACCESSIONS = [
    ("ENCFF001ABC_signal.bigwig", "ENCFF001ABC"),
    ("GSE12345_counts.csv", "GSE12345"),
    ("GSM987_raw.txt", "GSM987"),
    ("GPL570_probes.tsv", "GPL570"),
    ("SRR123456_1.fastq", "SRR123456"),
    ("SRR12345678_2.fastq", "SRR12345678"),
    ("ERX1_reads.fq", "ERX1"),
    ("DRP2.txt", "DRP2"),
    ("PRJNA12345_metadata.csv", "PRJNA12345"),
    ("PRJEB7_samples.tsv", "PRJEB7"),
    ("EMPIAR-10001_tomo.mrc", "EMPIAR-10001"),
    ("S-BIAD123_images.zip", "S-BIAD123"),
    ("zorbexin GSE12345 day3.csv", "GSE12345"),
]


def test_the_table_lists_the_seven_archives() -> None:
    assert len(identifiers()) == 7
    assert all(identifier.name and identifier.pattern for identifier in identifiers())


@pytest.mark.parametrize(("name", "accession"), ACCESSIONS)
def test_an_accession_is_one_identifier_atom_kept_whole(name, accession) -> None:
    stem = name.rsplit(".", 1)[0]
    found = [atom for atom in NORMALISER.atoms(stem) if atom.kind is AtomKind.IDENTIFIER]
    assert [atom.text for atom in found] == [accession]
    assert found[0].normalised == accession.casefold()
    assert stem[found[0].start : found[0].start + len(accession)] == accession


@pytest.mark.parametrize(("name", "accession"), ACCESSIONS)
def test_an_accession_is_never_looked_up_and_never_a_word(name, accession) -> None:
    reading = read_name(entry_named(name), run_id="run_a", normaliser=NORMALISER, store=STORE)
    (component,) = [c for c in reading.components if c.component_type == "identifier"]
    assert component.normalized_value == accession.casefold()
    (binding,) = [b for b in reading.bindings if b.component_id == component.component_id]
    assert binding.constraint == IDENTIFIER_CONSTRAINT
    assert binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert binding.term_id is None
    # No piece of the accession was read as a word a person could be asked about.
    words = {c.normalized_value for c in reading.components if c.component_type == "word"}
    assert not any(piece in words for piece in accession.casefold().split("-"))


@pytest.mark.parametrize(
    "stem",
    ["gse12345_counts", "XSRR123456_reads", "SRR_reads", "empiar_10001", "sbiad123"],
)
def test_what_is_not_written_as_an_accession_is_not_one(stem) -> None:
    assert not [atom for atom in NORMALISER.atoms(stem) if atom.kind is AtomKind.IDENTIFIER]


def test_a_hyphen_inside_an_accession_divides_nothing() -> None:
    atoms = NORMALISER.atoms("EMPIAR-10001-S-BIAD123")
    assert [(atom.kind, atom.text) for atom in atoms] == [
        (AtomKind.IDENTIFIER, "EMPIAR-10001"),
        (AtomKind.IDENTIFIER, "S-BIAD123"),
    ]
