"""A glued spelling whose one half is a known word keeps that word.

A glued run whose one half is a known word keeps that word. A spelling that a capital divides,
that the counts read as one word (it is never written apart), and that the Dictionary does not
know as a whole, reads its known half with that half's own sense. Its unknown half is a piece of
the unknown whole. A spelling with no known half stays one word. The setting
``dictionary.case_reading.unknown_whole`` says which reading applies (``pieces`` by default).
The value ``whole`` keeps the one-word reading. A settings file that an older ``init`` wrote
holds ``whole``, and ``ingence fix`` names it. Every name here is invented, and every spelling is
generated from its grammar.
"""

from __future__ import annotations

import json
from itertools import product
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.core.settings import Settings
from ingence.dictionary.discover import CASE_WHOLE_CONSTRAINT, UNIT_KINDS
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

STORE = DictionaryStore.builtin()
READER = Normaliser(STORE, years=(2013, 2026))

#: Halves the shipped Dictionary knows, written before a capital: in lower case, and in capitals
#: (an acronym before a capitalised word or a lower-case one), and after one.
KNOWN_BEFORE = ("raw", "mask", "crop", "stack", "GFP", "DAPI")
KNOWN_AFTER = ("GFP", "DAPI", "Mask", "Raw")
#: Invented halves the Dictionary does not know, in the same two positions; a lower-case one
#: follows only a half in capitals, since nothing else marks where it begins.
UNKNOWN_BEFORE = ("zib", "vorn", "brisa")
UNKNOWN_AFTER = ("Kelt", "Zeph", "QRV", "zorl", "quab")


def _spellings() -> list[tuple[str, str, str]]:
    """Every glued spelling of two different halves a case boundary divides, with its halves.

    Left out: two halves in capitals (``GFPQRV`` has no boundary to read) and a lower-case half
    after a lower-case one (``rawzeph`` neither).
    """
    return [
        (before + after, before, after)
        for before, after in product(
            (*KNOWN_BEFORE, *UNKNOWN_BEFORE), (*KNOWN_AFTER, *UNKNOWN_AFTER)
        )
        if before.casefold() != after.casefold()
        and not (before.isupper() and after.isupper())
        and not (after.islower() and not before.isupper())
    ]


SPELLINGS = _spellings()


def _known(word: str) -> bool:
    return STORE.look_up(word, excluded_kinds=UNIT_KINDS) is not None


def test_the_grammar_holds_its_own_premises() -> None:
    """Each spelling is cut at its capital into its two halves; no whole is a word the Dictionary
    knows; each half is known exactly where the grammar says so."""
    for whole, before, after in SPELLINGS:
        atoms = READER.atoms(f"{whole}_1")
        assert [(atom.text, atom.case_whole) for atom in atoms[:2]] == [
            (before, whole),
            (after, whole),
        ], whole
        assert not _known(whole), whole
    assert all(_known(word) for word in (*KNOWN_BEFORE, *KNOWN_AFTER))
    assert not any(_known(word) for word in (*UNKNOWN_BEFORE, *UNKNOWN_AFTER))


def test_the_default_reads_an_unknown_whole_in_pieces() -> None:
    assert Settings().dictionary.case_reading.unknown_whole == "pieces"


def _pool(tmp_path: Path, names: list[str], *, unknown_whole: str | None = None) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    for name in names:
        (pool / name).write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    if unknown_whole is not None:
        path = project.path / "settings.yaml"
        settings = yaml.safe_load(path.read_text()) or {}
        settings.setdefault("dictionary", {})["case_reading"] = {"unknown_whole": unknown_whole}
        path.write_text(yaml.safe_dump(settings))
    assert run_census(
        Project.open(project.path),
        run_id="run_20260101T000001Z_111111111111",
        started_at="2026-01-01T00:00:01Z",
    ).published
    return project


def _halves_bound(project: Project) -> dict[str, dict[str, tuple[str, str | None, str | None]]]:
    """By name, each half's status, constraint and sense."""
    found: dict[str, dict[str, tuple[str, str | None, str | None]]] = {}
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.raw_value, b.resolution_status, b.constraint, b.term "
            "FROM dictionary_bindings b JOIN components c ON c.component_id = b.component_id "
            "JOIN entries e ON e.entry_id = b.entry_id WHERE c.component_type = 'word'"
        ).fetchall()
    for name, raw, status, constraint, term in rows:
        found.setdefault(name, {})[raw] = (status, constraint, term)
    return found


def _names() -> list[str]:
    """Each spelling glued in two names and never written apart, so the counts read it whole."""
    return [f"{whole}_{index}.txt" for whole, _, _ in SPELLINGS for index in (1, 2)]


def test_a_known_half_keeps_its_sense_and_an_unknown_half_stays_a_piece_of_the_whole(
    tmp_path: Path,
) -> None:
    bound = _halves_bound(_pool(tmp_path, _names()))
    wrong = []
    for whole, before, after in SPELLINGS:
        halves = bound[f"{whole}_1.txt"]
        if not (_known(before) or _known(after)):
            continue
        for half in (before, after):
            status, constraint, term = halves[half]
            expected_term = STORE.look_up(half, excluded_kinds=UNIT_KINDS)
            if expected_term is not None:
                if (status, constraint, term) != ("resolved", None, expected_term.sense.term):
                    wrong.append((whole, half, status, constraint, term))
            elif (status, constraint) != ("examined_no_evidence", CASE_WHOLE_CONSTRAINT):
                wrong.append((whole, half, status, constraint, term))
    assert wrong == []


def test_a_spelling_with_no_known_half_stays_one_word(tmp_path: Path) -> None:
    bound = _halves_bound(_pool(tmp_path, _names()))
    wrong = []
    for whole, before, after in SPELLINGS:
        if _known(before) or _known(after):
            continue
        for half in (before, after):
            status, constraint, _ = bound[f"{whole}_1.txt"][half]
            if (status, constraint) != ("examined_no_evidence", CASE_WHOLE_CONSTRAINT):
                wrong.append((whole, half, status, constraint))
    assert wrong == []
    assert any(not (_known(b) or _known(a)) for _, b, a in SPELLINGS)


def test_whole_is_kept_as_a_setting_and_reads_every_half_into_the_unknown_whole(
    tmp_path: Path,
) -> None:
    bound = _halves_bound(_pool(tmp_path, _names(), unknown_whole="whole"))
    readings = {
        (status, constraint)
        for whole, before, after in SPELLINGS
        for half in (before, after)
        for status, constraint, _ in (bound[f"{whole}_1.txt"][half],)
    }
    assert readings == {("examined_no_evidence", CASE_WHOLE_CONSTRAINT)}


def test_a_whole_the_dictionary_knows_still_binds_its_sense_on_every_piece(
    tmp_path: Path,
) -> None:
    """Only an unknown whole reads in pieces: ``mNeonGreen`` is one fluorescent protein."""
    bound = _halves_bound(_pool(tmp_path, ["kelt_mNeonGreen.txt", "zib_mNeonGreen.txt"]))
    assert bound["kelt_mNeonGreen.txt"]["mNeon"] == (
        "resolved",
        CASE_WHOLE_CONSTRAINT,
        "fluorescent_protein",
    )
    assert bound["kelt_mNeonGreen.txt"]["Green"][2] == "fluorescent_protein"


def test_a_census_states_pieces_as_its_case_reading(tmp_path: Path) -> None:
    project = _pool(tmp_path, ["zibGFP_1.txt"])
    with open_catalog(project.path) as catalog:
        (stated,) = catalog.query("SELECT case_reading_json FROM run").fetchone()
    assert json.loads(stated)["unknown_whole"] == "pieces"


def test_fix_names_unknown_whole_an_older_init_wrote_and_changes_nothing(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ingence import SCHEMA_VERSION
    from ingence.cli.main import app

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    (project.path / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "dictionary": {"case_reading": {"unknown_whole": "whole"}},
                "schema_version": SCHEMA_VERSION,
            }
        )
    )
    before = (project.path / "settings.yaml").read_bytes()
    result = CliRunner().invoke(app, ["fix", "-p", str(project.path)])
    assert result.exit_code == 0, result.output
    assert "dictionary.case_reading.unknown_whole: whole" in result.output
    said = " ".join(result.output.split())
    # The default's own words already say "now", so the message must not say it twice.
    assert "the default is pieces now: a glued word keeps its known half." in said
    assert " now pieces now" not in said
    assert (project.path / "settings.yaml").read_bytes() == before
    assert Project.open(project.path).settings.dictionary.case_reading.unknown_whole == "whole"


def test_fix_says_nothing_of_pieces_written_on_purpose(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ingence import SCHEMA_VERSION
    from ingence.cli.main import app

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    (project.path / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "dictionary": {"case_reading": {"unknown_whole": "pieces"}},
                "schema_version": SCHEMA_VERSION,
            }
        )
    )
    result = CliRunner().invoke(app, ["fix", "-p", str(project.path)])
    assert result.exit_code == 0, result.output
    assert "unknown_whole" not in result.output


def test_a_project_read_with_whole_takes_one_whole_rebind_to_keep_the_known_half(
    tmp_path: Path,
) -> None:
    """An older project read with ``whole`` takes one whole rebind to keep the known half.

    Its settings then leave the default in force. A partial rebind is refused, in the words the
    job worker falls back on, and a whole rebind reads the known half with its sense."""
    project = _pool(tmp_path, ["zibGFP_1.txt", "zibGFP_2.txt"], unknown_whole="whole")
    assert _halves_bound(project)["zibGFP_1.txt"]["GFP"][:2] == (
        "examined_no_evidence",
        CASE_WHOLE_CONSTRAINT,
    )
    (project.path / "settings.yaml").write_text(
        yaml.safe_dump({"schema_version": Settings().schema_version})
    )
    with pytest.raises(ValueError, match="re-bind them all"):
        run_census(
            Project.open(project.path),
            run_id="run_20260101T000002Z_222222222222",
            started_at="2026-01-01T00:00:02Z",
            rebind=True,
            spellings=("zib",),
        )
    assert run_census(
        Project.open(project.path),
        run_id="run_20260101T000003Z_333333333333",
        started_at="2026-01-01T00:00:03Z",
        rebind=True,
    ).published
    assert _halves_bound(project)["zibGFP_1.txt"]["GFP"] == (
        "resolved",
        None,
        "fluorescent_protein",
    )


def test_a_known_half_in_capitals_before_another_word_is_divided() -> None:
    """An acronym the Dictionary knows divides from the word after it, capitalised or in lower case.

    Examples are ``GFP`` and ``Kelt``, ``DAPI`` and ``zorl``. A known word after capitals divides
    the same way. A tail shorter than a word stays on the acronym (``GFPs``, ``DAPIx``)."""
    for whole, before, after in SPELLINGS:
        if not before.isupper():
            continue
        assert [atom.text for atom in READER.atoms(whole)] == [before, after], whole
    assert [atom.text for atom in READER.atoms("GFPs")] == ["GFPs"]
    assert READER.atoms("DAPIx_1")[0].text == "DAPIx"
    assert [atom.text for atom in READER.atoms("ZIBylate")] == ["ZIBylate"]


def test_three_glued_words_with_a_known_acronym_between_read_its_sense(tmp_path: Path) -> None:
    project = _pool(tmp_path, ["zibGFPKelt_1.txt", "zibGFPKelt_2.txt"])
    atoms = READER.atoms("zibGFPKelt_1")
    assert [(atom.text, atom.case_whole) for atom in atoms[:3]] == [
        ("zib", "zibGFPKelt"),
        ("GFP", "zibGFPKelt"),
        ("Kelt", "zibGFPKelt"),
    ]
    halves = _halves_bound(project)["zibGFPKelt_1.txt"]
    assert halves["GFP"] == ("resolved", None, "fluorescent_protein")
    assert (
        halves["zib"][:2] == halves["Kelt"][:2] == ("examined_no_evidence", CASE_WHOLE_CONSTRAINT)
    )


def test_a_persons_one_word_answer_keeps_the_known_half_out(tmp_path: Path) -> None:
    """A person's answer wins over the default.

    Where somebody said a spelling is one word, every piece binds as that word (unknown until it
    is defined). The known half's own sense is kept beside it, shadowed."""
    from ingence.core.schema import RecordEventKind
    from ingence.dictionary.case_reading import case_target
    from ingence.record.log import append_event

    project = _pool(tmp_path, ["zibGFP_1.txt", "zibGFP_2.txt"])
    assert _halves_bound(project)["zibGFP_1.txt"]["GFP"][0] == "resolved"
    append_event(
        project.path,
        target=case_target("zibGFP"),
        kind=RecordEventKind.ASSIGN,
        payload={"reading": "whole"},
        actor="ines",
        occurred_at="2026-01-01T00:00:02Z",
        against_run_id="run_20260101T000001Z_111111111111",
    )
    assert run_census(
        Project.open(project.path),
        run_id="run_20260101T000003Z_333333333333",
        started_at="2026-01-01T00:00:03Z",
        rebind=True,
    ).published
    with open_catalog(project.path) as catalog:
        status, constraint, alternatives = catalog.query(
            "SELECT b.resolution_status, b.constraint, b.alternatives_json "
            "FROM dictionary_bindings b JOIN components c ON c.component_id = b.component_id "
            "JOIN entries e ON e.entry_id = b.entry_id WHERE e.name = ? AND c.raw_value = ?",
            ["zibGFP_1.txt", "GFP"],
        ).fetchone()
    assert (status, constraint) == ("examined_no_evidence", CASE_WHOLE_CONSTRAINT)
    (kept,) = json.loads(alternatives)
    hit = STORE.look_up("GFP", excluded_kinds=UNIT_KINDS)
    assert hit is not None
    assert (kept["term_id"], kept["shadowed"]) == (STORE.term_id(hit.sense), True)


#: Spellings whose capitals a cut would split in the middle or strip from a word they form,
#: each with the atoms it reads as: an acronym and the letters glued after it.
STAYING_WHOLE = (
    ("GTPase_assay", ["GTPase", "assay"]),
    ("RhoGTPase", ["Rho", "GTPase"]),
    ("HEKcells", ["HEKcells"]),
    ("CHOcells", ["CHOcells"]),
    ("PEGylated", ["PEGylated"]),
    ("ICCstain", ["ICCstain"]),
    ("ATPase", ["ATPase"]),
    ("ATPases", ["ATPases"]),
    ("ADPase", ["ADPase"]),
    ("SUMOylation", ["SUMOylation"]),
)
#: Spellings a known word in capitals still divides from the word glued after it.
STILL_DIVIDING = (
    ("GFPKelt", ["GFP", "Kelt"]),
    ("DAPIzeph", ["DAPI", "zeph"]),
    ("EGFPKelt", ["EGFP", "Kelt"]),
    ("zibGFPKelt", ["zib", "GFP", "Kelt"]),
)


def test_an_acronym_is_never_cut_after_a_two_letter_tag_inside_it() -> None:
    """``GTPase`` is not ``GT`` and ``Pase``, and ``HEKcells`` is not ``HE`` and ``Kcells``.

    A two-letter tag the Dictionary knows divides only where it is the whole run of capitals."""
    for name, atoms in STAYING_WHOLE[:6]:
        assert [atom.text for atom in READER.atoms(name)] == atoms, name


def test_a_word_forming_ending_after_a_known_acronym_keeps_one_word() -> None:
    """``ATPase`` is an enzyme, not ATP and a piece; the endings that form a new word of biochemical
    nomenclature are a Dictionary list, read where capitals meet lower case."""
    for name, atoms in STAYING_WHOLE[6:]:
        assert [atom.text for atom in READER.atoms(name)] == atoms, name
    for name, atoms in STILL_DIVIDING:
        assert [atom.text for atom in READER.atoms(name)] == atoms, name


def test_the_endings_are_the_dictionarys_not_the_codes() -> None:
    from ingence.dictionary.store import load_shipped_sections

    endings = STORE.word_endings()
    assert {"ase", "ases", "ylation", "ylated", "ylate"} <= endings
    without = DictionaryStore(
        section for section in load_shipped_sections() if not section.word_endings
    )
    assert without.word_endings() == frozenset()
    assert [atom.text for atom in Normaliser(without).atoms("ATPase")] == ["ATP", "ase"]
