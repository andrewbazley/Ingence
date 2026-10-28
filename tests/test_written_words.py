"""A word a page shows is the sense's own written name, never the parts a name was cut into.

A spelling that the Dictionary holds in parts (``u 2 os``, ``hek 293t``, ``cos 7``) binds the
hyphen, underscore, space and flush forms of a name alike. So the name reader's word for ``U2OS``
is the parts, and the groups made of words would show them, as "dapi u 2 os". The rule is that
every text a page shows of a group reads a multi-part word as its files wrote it (``U2OS``,
``HEK293T``, ``COS-7``, ``widefield``). It never reads the word as the Dictionary's name for the
sense, which is a synonym the files do not hold (``widefield`` is not ``epifluorescence``,
``HEK293T`` is not ``HEK293``). Nothing that decides anything moves. A group's key and the words a
file is found by stay the parts, so no file binds differently and no group forms or is re-keyed.

Every name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes import pages
from ingence.core.project import Project
from ingence.core.settings import GroupSettings, Settings
from ingence.dictionary.written import written_forms, written_text, written_word
from ingence.infer.run import run_inference
from ingence.record.targets import question_target
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

#: A dated folder per cell line and its repeat, and the words each holds in its files' names.
EXPERIMENTS = (
    ("2023-03-14", "U2OS DAPI"),
    ("2023-03-21", "U2OS DAPI repeat"),
    ("2023-04-02", "HEK293T GFP"),
    ("2023-04-09", "HEK293T GFP repeat"),
    ("2023-05-20", "COS-7 actin"),
    ("2023-05-27", "COS-7 actin repeat"),
)
PARTS = ("u 2 os", "hek 293t", "cos 7")
WRITTEN = ("U2OS", "HEK293T", "COS-7")


def _built(base: Path, settings: Settings | None = None) -> Project:
    pool = base / "pool"
    for number, (day, title) in enumerate(EXPERIMENTS):
        folder = pool / "Dana_Whitfield" / f"{day} {title}"
        folder.mkdir(parents=True)
        stem, label = title.split()[:2]
        for replicate in (1, 2, 3):
            (folder / f"{stem}_{label}_{replicate:02d}.tif").write_bytes(
                bytes([number * 5 + replicate]) * 64
            )
        (folder / f"{stem}_summary.csv").write_text("a,b\n1,2\n")
    created = (
        Project.create(base / "project", created_at="t", roots=[pool])
        if settings is None
        else Project.create(base / "project", created_at="t", roots=[pool], settings=settings)
    )
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return created


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return _built(tmp_path_factory.mktemp("written_words"))


@pytest.fixture(scope="module")
def spelled_project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """The same names, the lattice reading the spellings the names wrote (``groups.words_as``)."""
    return _built(
        tmp_path_factory.mktemp("written_spellings"),
        Settings(groups=GroupSettings(words_as="spellings")),
    )


def _page(project: Project) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return pages.page(catalog, project.path, "classes", limit=10_000)


def _shown(row: dict[str, Any]) -> str:
    """Every text a page row shows of its group."""
    return " | ".join([str(row.get("spelled", "")), *map(str, row.get("shared") or [])])


# ---------------------------------------------------------------------------
# 1. The pages show the sense's written name.
# ---------------------------------------------------------------------------


def test_the_run_reads_these_names_in_parts(project: Project) -> None:
    """The premise: the words the name reader cut are the parts, bound to the sense."""
    with open_catalog(project.path) as catalog:
        words = {
            row[0]
            for row in catalog.query(
                "SELECT DISTINCT lower(normalized_value) FROM components "
                "WHERE component_type = 'word'"
            ).fetchall()
        }
        forms = written_forms(catalog)
    assert set(PARTS) <= words
    assert {forms[parts] for parts in PARTS} == set(WRITTEN)


def test_a_proposed_group_of_experiments_is_shown_with_the_sense_written_not_its_parts(
    project: Project,
) -> None:
    experiments = [row for row in _page(project)["rows"] if row.get("level") == "experiments"]
    assert len(experiments) >= 3
    said = " ".join(_shown(row) for row in experiments)
    for parts in PARTS:
        assert parts not in said.casefold(), said
    for written in WRITTEN:
        assert written in said, said


def test_a_group_of_files_is_shown_with_the_sense_written_not_its_parts(project: Project) -> None:
    """The groups listed under an experiment: each class its files formed, spelled by its words."""
    groups = [group for held in _page(project)["within_experiments"] for group in held["groups"]]
    assert groups
    said = " ".join(str(group["spelled"]) for group in groups)
    for written in WRITTEN:
        assert f"word:{written}" in said, said
    for parts in PARTS:
        assert parts not in said.casefold(), said
    # The key a class is answered on is still the parts.
    keys = " ".join(str(group["key"]) for group in groups)
    assert all(parts in keys for parts in PARTS)


# ---------------------------------------------------------------------------
# 2. Nothing that decides anything moves.
# ---------------------------------------------------------------------------


def test_a_groups_key_and_target_stay_the_parts_so_nothing_is_re_keyed(
    spelled_project: Project,
) -> None:
    """Reading spellings (``groups.words_as: spellings``), a group's key is the parts."""
    keyed = [row for row in _page(spelled_project)["rows"] if row.get("level") == "experiments"]
    assert keyed
    assert any(any(parts in str(row["raised"]) for parts in PARTS) for row in keyed)
    for row in keyed:
        assert row["target"] == question_target(row["rule_id"], row["raised"])


def test_reading_terms_a_group_s_key_is_the_term_and_is_still_shown_as_written(
    project: Project,
) -> None:
    """The default reads a bound word as its Dictionary term.

    The key and target hold the term. The page shows it as the files wrote it, never as the
    sense's name.
    """
    keyed = [row for row in _page(project)["rows"] if row.get("level") == "experiments"]
    assert keyed
    said = " ".join(str(row["raised"]) for row in keyed)
    assert not any(parts in said for parts in PARTS)
    assert {"u2os", "hek293", "cos7"} <= set(said.split())
    shown = " ".join(_shown(row) for row in keyed)
    for written in WRITTEN:
        assert written in shown, shown
    for row in keyed:
        assert row["target"] == question_target(row["rule_id"], row["raised"])


def test_the_forms_are_keyed_by_the_parts_the_components_hold(project: Project) -> None:
    """What a file is found by is the word as the name reader cut it; only the text moves."""
    with open_catalog(project.path) as catalog:
        forms = written_forms(catalog)
        held = {
            row[0]
            for row in catalog.query(
                "SELECT DISTINCT lower(normalized_value) FROM components "
                "WHERE component_type = 'word'"
            ).fetchall()
        }
    assert set(forms) <= held
    assert set(PARTS) <= set(forms)


def test_no_file_binds_differently(project: Project) -> None:
    """The words in the components and their bindings are the run's, read and not changed."""
    with open_catalog(project.path) as catalog:
        bound = dict(
            catalog.query(
                "SELECT lower(c.normalized_value), b.term FROM components c "
                "JOIN dictionary_bindings b ON b.component_id = c.component_id "
                "WHERE c.component_type = 'word' AND b.resolution_status = 'resolved' "
                "AND lower(c.normalized_value) IN ('u 2 os', 'hek 293t', 'cos 7')"
            ).fetchall()
        )
    assert bound == {"u 2 os": "u2os", "hek 293t": "hek293", "cos 7": "cos7"}


def test_a_class_is_found_by_the_spelling_a_page_shows_and_by_the_one_it_always_had(
    project: Project,
) -> None:
    from ingence.record.verdicts import class_index

    with open_catalog(project.path) as catalog:
        index = class_index(catalog)
    shown, parts = "word:dapi word:U2OS", "word:dapi word:u 2 os"
    assert shown in index.by_spelling
    assert parts in index.by_spelling
    assert index.by_spelling[shown] == index.by_spelling[parts]
    assert index.find(shown, rule_id=None, folder=None) == index.find(
        parts, rule_id=None, folder=None
    )


def test_a_shown_spelling_never_makes_a_sheet_match_two_classes(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another class already has this spelling: the shown one is not taken from it."""
    import ingence.dictionary.written as written_module
    from ingence.record.verdicts import class_index

    # Show the U2OS class's words as the HEK293T class's own spelling.
    monkeypatch.setattr(
        written_module,
        "written_forms",
        lambda catalog: {"u 2 os": "hek 293t", "dapi": "gfp"},
    )
    with open_catalog(project.path) as catalog:
        index = class_index(catalog)
    own = "word:gfp word:hek 293t"
    classes = index.by_spelling[own]
    assert classes
    assert all("hek 293t" in key and "u 2 os" not in key for _, key in classes)
    # The U2OS class is still found by its parts spelling.
    assert index.by_spelling["word:dapi word:u 2 os"]


@pytest.fixture(scope="module")
def twin_project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """Two dated folders holding the same file names, so each holds a class of one spelling."""
    base = tmp_path_factory.mktemp("written_twins")
    pool = base / "pool"
    for day in ("2023-03-14", "2023-03-21"):
        folder = pool / "Sam_Okafor" / f"{day} U2OS DAPI"
        folder.mkdir(parents=True)
        for replicate in (1, 2, 3):
            (folder / f"U2OS_DAPI_{replicate:02d}.tif").write_bytes(bytes([replicate]) * 64)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return created


def test_a_sheet_naming_the_shown_spelling_finds_the_class_in_the_folder_it_names(
    twin_project: Project,
) -> None:
    """Two folders each hold a class of one spelling. The folder a sheet gives tells them apart,
    by the spelling the page shows and by the key's parts spelling."""
    from ingence.record.verdicts import SEVERAL_CLASSES, Subject, class_index

    with open_catalog(twin_project.path) as catalog:
        index = class_index(catalog)
    parts = "extension:tif word:dapi word:u 2 os"
    shown = "extension:tif word:dapi word:U2OS"
    # The premise: two classes share the spelling, one in each folder.
    assert len(index.by_spelling[parts]) == 2
    folders = sorted(index.folders[key] for _, key in index.by_spelling[parts])
    assert folders == [
        "Sam_Okafor/2023-03-14 U2OS DAPI",
        "Sam_Okafor/2023-03-21 U2OS DAPI",
    ]
    # The shown spelling belongs to both, so the folder filter still works.
    assert index.by_spelling[shown] == index.by_spelling[parts]
    for folder in folders:
        by_parts = index.find(parts, rule_id=None, folder=folder)
        by_shown = index.find(shown, rule_id=None, folder=folder)
        assert isinstance(by_shown, Subject), by_shown
        assert by_shown == by_parts
        assert index.folders[by_shown.about["key"]] == folder
    # And with no folder to say which, it is refused as several, by either spelling.
    assert index.find(parts, rule_id=None, folder=None) == SEVERAL_CLASSES
    assert index.find(shown, rule_id=None, folder=None) == SEVERAL_CLASSES


# ---------------------------------------------------------------------------
# 3. The rule for writing a word again.
# ---------------------------------------------------------------------------

FORMS = {"u 2 os": "U2OS", "hek 293t": "HEK293T", "cos 7": "COS-7", "u 2 osmo": "Osmo"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("dapi u 2 os", "dapi U2OS"),
        ("word:dapi word:u 2 os", "word:dapi word:U2OS"),
        ("gfp, hek 293t, cos 7", "gfp, HEK293T, COS-7"),
        # A word is found whole, never inside another, and the longest first.
        ("u 2 osmo and u 2 os", "Osmo and U2OS"),
        ("hu 2 os", "hu 2 os"),
        ("u 2 oss", "u 2 oss"),
        # Whatever case the text writes it in.
        ("U 2 OS", "U2OS"),
        # A text with no multi-part word is returned as it is.
        ("dapi gfp actin", "dapi gfp actin"),
        ("", ""),
    ],
)
def test_a_text_is_written_word_by_word(text: str, expected: str) -> None:
    assert written_text(text, FORMS) == expected


def test_one_word_is_written_or_left() -> None:
    assert written_word("hek 293t", FORMS) == "HEK293T"
    assert written_word("dapi", FORMS) == "dapi"


def test_without_forms_a_text_is_unchanged() -> None:
    assert written_text("dapi u 2 os", {}) == "dapi u 2 os"


# ---------------------------------------------------------------------------
# 4. A word the Dictionary has a synonym for is shown as the files wrote it.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def widefield_project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """Four files say widefield (three flush, one with an underscore), and one says wide field."""
    base = tmp_path_factory.mktemp("written_widefield")
    pool = base / "pool"
    pool.mkdir()
    for name in (
        "HeLa_widefield_01.tif",
        "HeLa_widefield_02.tif",
        "HEK293_widefield_01.tif",
        "HEK293_wide_field_01.tif",
        "COS7_wide field_01.tif",
    ):
        (pool / name).write_bytes(b"x" * 16)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return created


def test_a_word_bound_to_a_synonym_is_shown_as_the_files_wrote_it(
    widefield_project: Project,
) -> None:
    with open_catalog(widefield_project.path) as catalog:
        bound = {
            row[0]
            for row in catalog.query(
                "SELECT b.term FROM components c JOIN dictionary_bindings b "
                "ON b.component_id = c.component_id WHERE lower(c.normalized_value) = 'wide field'"
            ).fetchall()
        }
        forms = written_forms(catalog)
    # The Dictionary reads all five as one sense, whose own name is not what any file says.
    assert bound == {"epifluorescence"}
    # And the page shows the way the most files wrote it.
    assert forms["wide field"] == "widefield"
    shown = written_text("word:hela word:wide field", forms)
    assert shown == "word:hela word:widefield"
    assert "epifluorescence" not in " ".join(forms.values())


def test_the_spelling_most_files_hold_is_the_one_shown(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    for number, name in enumerate(
        ("a_Wide-Field_1.tif", "b_Wide-Field_1.tif", "c_Wide-Field_1.tif", "d_widefield_1.tif")
    ):
        (pool / name).write_bytes(bytes([number]) * 16)
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    with open_catalog(created.path) as catalog:
        assert written_forms(catalog)["wide field"] == "Wide-Field"
