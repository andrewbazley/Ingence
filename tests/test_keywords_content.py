"""The keyword extractor and search's content channel.

A dive records a bounded file's most frequent words with their counts (never its text), and a
script's output names read from its save calls; the search projection weighs the words across
files and a search then finds a file by what it says and a script by what it writes. Every name
and word is invented.
"""

from __future__ import annotations

import json

import pytest

from ingence.api.routes import search as routes
from ingence.core.project import Project
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog
from ingence.textword.prose import count_terms, prose_tokens

PROSE = (
    "The trellium assay ran twice. Trellium binding rose after 10 mM vorn and 2.5h at 37C; "
    "the ER signal stayed flat. Er, the second trellium plate was cleaner. "
)
SCRIPT = (
    "import matplotlib.pyplot as plt\n"
    "stem = 'zib'\n"
    "plt.savefig(stem + '_brisafit.png')\n"
    "frame.to_csv(stem + '_kelttracks.csv')\n"
)


def test_prose_keeps_words_and_drops_values_and_units() -> None:
    terms = dict(count_terms(prose_tokens(PROSE)))
    assert terms["trellium"] == 3
    assert "mm" not in terms and "2.5h" not in terms and "37c" not in terms
    # A short capitalised word keeps its case apart from the ordinary word it folds to.
    assert terms["ER"] == 1
    assert "the" not in terms


@pytest.fixture(scope="module")
def dived(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("keywords")
    pool = base / "pool"
    (pool / "kelt_2024").mkdir(parents=True)
    (pool / "kelt_2024" / "notes_one.txt").write_text(PROSE * 5)
    (pool / "kelt_2024" / "notes_two.txt").write_text(
        "An ordinary vorn note about zib plates.\n" * 5
    )
    (pool / "kelt_2024" / "analyse_zib.py").write_text(SCRIPT)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="2026-01-01T00:00:02Z",
        budget_seconds=60,
    )
    return project


def _reading(project: Project, name: str, key: str) -> object:
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT o.value_structure FROM observations o JOIN entries e USING (entry_id) "
            "WHERE e.name = ? AND o.key = ?",
            [name, key],
        ).fetchone()
    return None if row is None else json.loads(row[0])


def test_a_dive_records_counts_never_text(dived: Project) -> None:
    counted = _reading(dived, "notes_one.txt", "text.term_counts")
    assert isinstance(counted, dict)
    assert counted["counts"][counted["terms"].index("trellium")] == 15
    assert PROSE[:40] not in json.dumps(counted)


def test_a_scripts_output_names_are_read_from_its_save_calls(dived: Project) -> None:
    # The stem is followed to its assignment, so the names are whole.
    assert _reading(dived, "analyse_zib.py", "script.output_names") == [
        "zib_brisafit.png",
        "zib_kelttracks.csv",
    ]


def test_search_finds_a_file_by_what_it_says_and_a_script_by_what_it_writes(
    dived: Project,
) -> None:
    import duckdb

    from ingence.search.projection import read_projection

    with open_catalog(dived.path) as catalog:
        routes.build(catalog, dived.path)
        assert catalog.run_directory is not None
        built = read_projection(dived.path, catalog.run_directory.name)
        found = routes.search(catalog, dived.path, text="trellium")
        written = routes.search(catalog, dived.path, text="brisafit")
        nothing = routes.search(catalog, dived.path, text="glorbex")
    assert built is not None
    rows = duckdb.sql(
        f"SELECT source, token FROM '{built.path / 'terms.parquet'}' "
        "WHERE source IN ('content', 'script_output') ORDER BY source, token"
    ).fetchall()
    assert ("content", "trellium") in rows
    assert ("script_output", "brisafit") in rows
    # The word is in no name, so only the content channel can find it.
    assert found["results"] and written["results"]
    assert not nothing["results"]
