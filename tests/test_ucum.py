"""Every UCUM code the Dictionary names is a code UCUM defines.

The Dictionary holds a reference to UCUM, never a merge, and the licence is the reason. UCUM permits
reproducing and distributing the Work complete and unaltered with its notices, and forbids adding,
deleting or modifying its field names, contents, descriptions or comments, or using it to create a
derivative work. Copying UCUM codes into ``units.yaml`` as that file's own data would be exactly
such a derivative. So the table sits whole under ``vendor/ucum/``, ``units.yaml`` stays the authored
file, and a unit may *point at* a code.

A pointer nobody checks is a pointer that rots, which is what this file is for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ingence.dictionary.ucum import load_ucum, vendored_path

UNITS = Path(__file__).resolve().parents[1] / "src/ingence/dictionary/builtin/units.yaml"

#: Terms UCUM does not define, checked one at a time rather than counted.
#: **None of these is a gap.** UCUM identifies units of measure; a fold change,
#: a numerical aperture, a magnification and a frame rate are ratios, optical
#: properties and instrument settings, and a pixel is not a length. Listing them
#: means adding a twelfth is a decision somebody takes rather than a number that
#: quietly moves.
UNGROUNDED = frozenset(
    {
        "percent_weight_volume",
        "percent_volume_volume",
        "fold",
        "revolutions_per_minute",
        "relative_centrifugal_force",
        "numerical_aperture",
        "magnification",
        "pixel",
        "voxel",
        "dots_per_inch",
        "frames_per_second",
    }
)


@pytest.fixture(scope="module")
def entries() -> list[dict]:
    return yaml.safe_load(UNITS.read_text())["entries"]


def test_the_vendored_table_is_present_and_readable():
    """Absent in an installed package, and required in this repository."""
    assert vendored_path().exists(), "vendor/ucum/ucum-essence.xml is missing"
    table = load_ucum()
    assert table.version == "2.2"
    assert table.revision_date == "2024-06-17"
    assert len(table.units) > 300


def test_every_referenced_code_is_one_ucum_defines(entries: list[dict]):
    """The whole point of the reference: it resolves, or it is not a reference."""
    table = load_ucum()
    wrong = [
        (entry["term"], entry["ucum"])
        for entry in entries
        if "ucum" in entry and not table.defines_reference(entry["ucum"])
    ]
    assert not wrong, f"references UCUM does not define: {wrong}"


def test_exactly_the_units_that_are_not_measurements_lack_a_code(entries: list[dict]):
    without = {entry["term"] for entry in entries if "ucum" not in entry}
    assert without == UNGROUNDED


def test_ucum_grounds_the_case_distinction_the_dictionary_makes(entries: list[dict]):
    """``nM`` against ``nm`` stops being a judgement call.

    The Dictionary does not fold case, a choice first made by argument: a folded index reported a
    length where a concentration was written, wrong by nine orders of magnitude. UCUM reaches the
    same answer from outside, by keeping case-sensitive and case-insensitive codes in separate
    columns, which is what makes the next case checkable rather than arguable.
    """
    table = load_ucum()
    nanomolar = next(e for e in entries if e["term"] == "nanomolar")["ucum"]
    nanometre = next(e for e in entries if e["term"] == "nanometre")["ucum"]
    assert nanomolar == "nmol/L"
    assert nanometre == "nm"
    assert nanomolar != nanometre

    # And the case-insensitive column shows why folding cannot work: the two
    # collapse to the same thing the moment case is thrown away.
    assert table.units["m"].code_insensitive == "M"


def test_the_case_sensitive_column_is_ascii(entries: list[dict]):
    """Which makes a micro-sign surface an alias, not a code.

    UCUM writes microgram ``ug`` and reserves ``μ`` for the print symbol. That
    is the right shape for the Dictionary too: ``µg`` and ``μg`` are two
    spellings pointing at one code rather than two codes.
    """
    for entry in entries:
        code = entry.get("ucum")
        if code is None:
            continue
        assert code.isascii(), f"{entry['term']} references a non-ASCII code {code!r}"


def test_a_compound_reference_uses_the_solidus_ucum_defines(entries: list[dict]):
    """The separator question, answered from outside rather than by taste.

    UCUM's solidus is a formal "per" operator, so ``ug/mL`` is a canonical compound unit. A slash
    cannot appear in a POSIX basename, so ``ug_mL`` is a spelling somebody invented to reach it.
    Grounding the first is what tells a reader that the second is a reachability workaround rather
    than a term.
    """
    table = load_ucum()
    compounds = [e for e in entries if "/" in str(e.get("ucum", ""))]
    assert compounds, "the Dictionary has compound units and they should be grounded"
    for entry in compounds:
        assert table.defines_reference(entry["ucum"])
        assert entry["ucum"].count("/") == 1


def test_nothing_writes_to_the_vendored_table():
    """The Work is unmodified, and stays so. Checked by digest, not by care."""
    import hashlib

    digest = hashlib.sha256(vendored_path().read_bytes()).hexdigest()
    assert digest == "dfccea1b5dc284245ebae97edd1dc03c45864da4e87df55bc9851797b4fd0b61"
