"""A path is text inside SQL, never SQL: every path the engine names to DuckDB is one literal.

A project or a pool can sit under any folder a person names, and a folder's name may hold an
apostrophe (``a person's runs``). Pasted between quotes as it stands, such a name ended the
string: every search broke, and a name built for it ran a second statement, which DuckDB runs
even where the last statement has bound parameters, and which can write files (``COPY ... TO``).
Held here, over invented names: the literal itself, a census and a search under such a folder,
a name built to run a second statement, each view that reads a run's files by path, and a
tripwire over the package's source for the next hand-made literal.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

import ingence
from fixtures.revision_4 import write_revision
from ingence.api.routes import search as routes
from ingence.api.routes.changes import changes
from ingence.api.routes.manifest import manifest
from ingence.api.routes.reorganisation import census_pair, reorganisation
from ingence.compare.carry import moved_file_decisions
from ingence.compare.match import Side
from ingence.compare.report import measure
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog, sql_path, sql_path_list
from ingence.storage.commit import run_path

FIRST_RUN = "run_20260101T000001Z_111111111111"
SECOND_RUN = "run_20260101T000002Z_222222222222"
THIRD_RUN = "run_20260101T000003Z_333333333333"

#: The folder every project below sits under.
APOSTROPHE = "a person's runs"

#: A folder's name built to end a string and run a statement of its own.
CRAFTED = "p'; COPY (SELECT 1) TO 'marker.csv' (FORMAT CSV); --"


def _pool(base: Path) -> Path:
    pool = base / "pool"
    session = pool / "20200105 alpha session"
    session.mkdir(parents=True)
    (pool / "alpha.txt").write_text("alpha words for a search\n", encoding="utf-8")
    (pool / "beta.txt").write_text("beta words for a search\n", encoding="utf-8")
    for number in range(3):
        (session / f"alpha_{number}.txt").write_text(f"alpha {number}\n", encoding="utf-8")
    return pool


def _snapshot(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _project(home: Path, pool: Path) -> Project:
    created = Project.create(home, created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id=FIRST_RUN, started_at="t")
    return created


def _search_and_suggest(project: Project, word: str) -> tuple[dict[str, object], dict[str, object]]:
    with open_catalog(project.path) as catalog:
        routes.build(catalog, project.path)
    with open_catalog(project.path) as catalog:
        found = routes.search(catalog, project.path, text=word)
    with open_catalog(project.path) as catalog:
        suggested = routes.suggest(catalog, project.path, prefix=word[:2])
    return found, suggested


def _paths(found: dict[str, object]) -> list[str]:
    results = found["results"]
    assert isinstance(results, list)
    return [str(row["path"]) for row in results]


# -- the literal -------------------------------------------------------------------------------


def test_a_path_with_an_apostrophe_is_one_literal_with_the_quote_doubled() -> None:
    literal = sql_path(Path("/data/a person's runs/terms.parquet"))
    assert literal == "'/data/a person''s runs/terms.parquet'"
    connection = duckdb.connect()
    assert connection.execute(f"SELECT {literal}").fetchone() == (
        "/data/a person's runs/terms.parquet",
    )


def test_a_list_of_paths_is_one_list_of_literals() -> None:
    listed = sql_path_list([Path("/a/b's"), Path("/c/d")])
    assert listed == "['/a/b''s', '/c/d']"
    connection = duckdb.connect()
    assert connection.execute(f"SELECT {listed}").fetchone() == (["/a/b's", "/c/d"],)


# -- a census and a search under such a folder -------------------------------------------------


def test_a_project_under_a_folder_with_an_apostrophe_is_searched(tmp_path: Path) -> None:
    home = tmp_path / APOSTROPHE
    project = _project(home / "project", _pool(home))
    found, suggested = _search_and_suggest(project, "alpha")
    assert any("alpha" in path for path in _paths(found))
    words = suggested["words"]
    assert isinstance(words, list)
    assert any(word["token"] == "alpha" for word in words)


def test_a_name_built_to_run_a_second_statement_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name is read as a name: nothing is written beside it, and the pool is as it was.

    Pasted into SQL, such a name would make the census and the search write ``marker.csv`` into the
    working folder and a Parquet file named ``p`` beside the project.
    """
    monkeypatch.chdir(tmp_path)
    pool = _pool(tmp_path)
    before = _snapshot(pool)
    refused: Exception | None = None
    try:
        project = _project(tmp_path / CRAFTED, pool)
        found, _ = _search_and_suggest(project, "alpha")
    except Exception as error:  # judged below, once what was written is read
        refused = error
    assert not any(path.name == "marker.csv" for path in tmp_path.rglob("*"))
    assert not (tmp_path / "p").exists()
    assert _snapshot(pool) == before
    if refused is None:
        assert any("alpha" in path for path in _paths(found))
    else:
        # A refusal in words is an answer; a database's error is not one.
        assert not isinstance(refused, duckdb.Error), refused
        assert isinstance(refused, ValueError | OSError) and str(refused)


# -- each view that reads a run's files by path --------------------------------------------------


def test_the_compare_carry_reads_an_earlier_project_under_a_folder_with_an_apostrophe(
    tmp_path: Path,
) -> None:
    """A conclusion about a file of the earlier run is read from that run, wherever it sits."""
    home = tmp_path / APOSTROPHE
    pool = _pool(home)
    project = _project(home / "project", pool)
    run_inference(project, run_id=SECOND_RUN, started_at="t")
    (pool / "20200105 alpha session").rename(pool / "20200106 alpha session")
    run_census(project, run_id=THIRD_RUN, started_at="t")
    measured = measure(Side(project.path, SECOND_RUN), Side(project.path, THIRD_RUN))
    assert measured.basis.earlier_run == run_path(project.path, SECOND_RUN)
    claims = sorted((run_path(project.path, SECOND_RUN) / "claims").glob("*.parquet"))
    connection = duckdb.connect()
    claim = connection.execute(
        f"SELECT claim_id FROM read_parquet({sql_path_list(claims)}) "
        "WHERE subject IN (SELECT unnest(?::VARCHAR[])) ORDER BY claim_id LIMIT 1",
        [sorted(measured.basis.moved)],
    ).fetchone()
    assert claim is not None
    decided = SimpleNamespace(target=f"claim:{claim[0]}")
    assert moved_file_decisions(measured.basis, [decided]) == 1  # type: ignore[list-item]


def test_the_changes_view_reads_runs_under_a_folder_with_an_apostrophe(tmp_path: Path) -> None:
    home = tmp_path / APOSTROPHE
    project = _project(home / "project", _pool(home))
    run_inference(project, run_id=SECOND_RUN, started_at="t")
    found = changes(project.path, before=FIRST_RUN, after=SECOND_RUN)
    assert found["before"]["run_id"] == FIRST_RUN
    assert found["after"]["run_id"] == SECOND_RUN


def test_the_earlier_rules_are_read_under_a_folder_with_an_apostrophe(tmp_path: Path) -> None:
    """``before_rules``: the third run's manifest reads the rules of the second, which has some."""
    home = tmp_path / APOSTROPHE
    project = _project(home / "project", _pool(home))
    run_inference(project, run_id=SECOND_RUN, started_at="t")
    run_inference(project, run_id=THIRD_RUN, started_at="t")
    assert sorted((run_path(project.path, SECOND_RUN) / "rules").glob("*.parquet"))
    found = manifest(project.path, THIRD_RUN)
    assert found["compared_run_id"] == SECOND_RUN


def test_the_reorganisation_reads_runs_under_a_folder_with_an_apostrophe(tmp_path: Path) -> None:
    home = tmp_path / APOSTROPHE
    pool = _pool(home)
    project = _project(home / "project", pool)
    (pool / "gamma.txt").write_text("gamma\n", encoding="utf-8")
    run_census(project, run_id=SECOND_RUN, started_at="t")
    assert census_pair(project.path) == (FIRST_RUN, SECOND_RUN)
    assert reorganisation(project.path)["available"] is True


# -- the tripwire ---------------------------------------------------------------------------------

#: Every place in the package where a quote opens straight into a substitution, each with why it is
#: not a path pasted into SQL. A site is found by its file and a piece of its own line, not by its
#: line number, so an edit elsewhere in the file leaves the list as it is. A new site is written
#: with ``sql_path`` or ``sql_path_list`` instead, or reviewed and added here.
REVIEWED: dict[tuple[str, str], str] = {
    # SQL over constants the engine spells itself, never a path or anything a person typed.
    ("api/routes/pages.py", "ELSE '{TOP_LEVEL}' END"): "a constant label",
    ("api/routes/review.py", "f\"'{rule}'\" for rule in"): "constant rule identifiers",
    ("api/v1/explorer/arrange.py", "theme_kind = '{kind}')"): "a theme kind of the arrangement",
    ("api/v1/explorer/arrange.py", "'{PAIR}'"): "a constant separator",
    ("api/v1/explorer/arrange.py", "'{SET_ASIDE}'"): "a constant label",
    ("api/v1/explorer/arranged.py", "= '{key}') AS"): "a measure's category key",
    ("api/v1/explorer/axes.py", "SELECT '{key}' AS axis"): "an axis's own key",
    ("api/v1/explorer/axes.py", "'{PAIR}'"): "a constant separator",
    ("api/v1/explorer/axes.py", "= '{key}')"): "a measure's category key",
    ("api/v1/explorer/projection.py", "'{CONNECTION_ZONE}'"): "a constant zone name",
    ("api/v1/explorer/projection.py", "f\"'{value}'\" for value in values"): "category keys",
    ("api/v1/explorer/projection.py", "THEN '{family}'"): "a category family's key",
    ("api/v1/explorer/projection.py", "theme_kind = '{theme_kind}'"): "a constant theme kind",
    ("api/v1/explorer/projection.py", "'{CLASSIFIES}'"): "a constant predicate pattern",
    ("api/v1/explorer/projection.py", "'{by_name}'"): "a constant predicate pattern",
    ("api/v1/explorer/projection.py", "'{EARLIEST_PREDICATE}'"): "a constant predicate",
    ("api/v1/explorer/projection.py", "'{NAME_DATE_PREDICATE}'"): "a constant predicate",
    ("api/v1/explorer/projection.py", "'{EXPERIMENT_PREDICATES}'"): "a constant pattern",
    ("api/v1/explorer/projection.py", "= '{key}') AS {prefix}_{key}"): "a category key",
    ("api/v1/explorer/projection.py", "f\"'{rule}'\" for rule in"): "constant rule identifiers",
    ("api/v1/explorer/timeline.py", "date_diff('{unit}'"): "a constant unit of time",
    ("api/v1/explorer/wheel.py", "f\"'{value}'\" for value in values"): "category keys",
    ("compare/fingerprints.py", "f\"'{move.value}'\""): "an enumeration's values",
    ("compare/fingerprints.py", "'{DIGEST_KEYS[tier]}'"): "a constant observation key",
    ("compare/match.py", "side = '{side}'"): "one of two constant sides",
    ("search/engine.py", "'{AMBIGUOUS_DATE_JOINER}'"): "a constant joiner",
    ("search/engine.py", "'{edge}')"): "one of two fixed spellings",
    ("storage/catalog.py", "'{pattern}', '{replacement}'"): "a declared column's fixed rewrite",
    ("storage/compact.py", "f\"'{literal}' AS"): "a declared default, canonical JSON",
    ("storage/compact.py", "f\"'{escaped}' AS"): "a declared default, quotes doubled",
    ("storage/own_files.py", "'{EntryKind.FILESYSTEM.value}'"): "an enumeration's value",
    # Not SQL at all.
    ("api/v1/topic_export.py", 'f"\'{value}" if'): "a spreadsheet cell kept from being a formula",
    ("cli/main.py", "f\"{count} '{word}'\""): "a sentence for a person",
    ("compare/carry.py", "'{self.new_place}'"): "a sentence for a person",
    ("compare/carry.py", "'{self.old_place}'"): "a sentence for a person",
    ("compare/carry.py", "written about '{olds[0]}'"): "a sentence for a person",
    ("compare/report.py", "f\"'{left.under}'\""): "a sentence for a person",
    ("compare/report.py", "f\"'{right.under}'\""): "a sentence for a person",
    ("compare/verdict.py", "one rewrite, '{old or"): "a sentence for a person",
    ("compare/verdict.py", "f\"'{folder}'\""): "a sentence for a person",
    ("compare/verdict.py", "sits inside '{new}'"): "a sentence for a person",
    ("compare/verdict.py", "inside '{old}' now sits"): "a sentence for a person",
    ("compare/verdict.py", "everything in '{old}' is now in '{new}'"): "a sentence for a person",
    ("core/settings.py", "schema_version: '{SCHEMA_VERSION}'"): "a line of YAML",
    ("evaluation/sample_lab_shapes.py", "('{name}');"): "a line of an invented script",
    ("evaluation/sample_lab_shapes.py", "(S, '{name}');"): "a line of an invented script",
}

#: A plain string that a later ``format`` fills: a quote opening straight into a placeholder.
_PLACEHOLDER = re.compile(r"'\{[A-Za-z_][A-Za-z_0-9.]*[!:}]")


def _quote_opens_into_substitution(tree: ast.Module) -> list[int]:
    """The lines of every f-string part that ends in a quote just before a substitution, and of
    every plain string holding a quoted placeholder."""
    found: list[int] = []
    parts_of_fstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts_of_fstrings.update(id(part) for part in node.values)
            for before, after in zip(node.values, node.values[1:], strict=False):
                if (
                    isinstance(before, ast.Constant)
                    and isinstance(before.value, str)
                    and before.value.endswith("'")
                    and isinstance(after, ast.FormattedValue)
                ):
                    found.append(after.lineno)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in parts_of_fstrings
            and _PLACEHOLDER.search(node.value)
        ):
            found.append(node.lineno)
    return found


def test_no_path_is_pasted_between_quotes_into_sql() -> None:
    package = Path(ingence.__file__).parent
    unreviewed: list[str] = []
    used: set[tuple[str, str]] = set()
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for number in sorted(set(_quote_opens_into_substitution(ast.parse(source)))):
            line = lines[number - 1]
            matched = [key for key in REVIEWED if key[0] == relative and key[1] in line]
            if matched:
                used.update(matched)
            else:
                unreviewed.append(f"{relative}:{number}: {line.strip()}")
    assert not unreviewed, (
        "a quote opens straight into a substitution; write the path with sql_path or "
        "sql_path_list, or review the site and add it to REVIEWED:\n" + "\n".join(unreviewed)
    )
    assert not set(REVIEWED) - used, f"reviewed sites no longer found: {set(REVIEWED) - used}"


def test_the_tripwire_finds_a_path_pasted_between_quotes() -> None:
    planted = ast.parse(
        "def f(path):\n"
        "    a = f\"SELECT * FROM '{path.as_posix()}'\"\n"
        "    b = \"SELECT * FROM '{path}'\".format(path=path)\n"
        '    return f"COPY (SELECT 1) TO {sql_path(path)}" + f"\'{{\'"\n'
    )
    assert _quote_opens_into_substitution(planted) == [2, 3]
