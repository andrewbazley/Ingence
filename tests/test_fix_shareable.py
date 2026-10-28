"""``ingence fix --shareable``: the same report with the names of the data written as placeholders.

The fixture (``fixtures/fix_project.py``) is a project with two roots, an exclusion pattern a person
wrote and three runs. Every name in it is invented, and the fixture lists them, so a test can say
that none of them reaches the shareable report. Nothing here uses the real denylist. The scan is an
invented one (``fixtures/invented_scan.py``) that matches as the exporter's scan does, against a
denylist of the fixture's names.

Two projects are made once for the module (a census costs seconds), and no test changes them
except to put things back.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from fixtures import fix_project as fixture
from fixtures.invented_scan import SCAN_SCRIPT
from fixtures.revision_4 import retired_form, write_retired
from ingence.cli.main import app
from ingence.cli.shareable import Placeholders
from ingence.core.project import Project
from ingence.core.settings import ScanSettings, Settings

if TYPE_CHECKING:
    from typer.testing import Result

#: What ``fix`` printed before the option existed, on the fixture, with the paths and the counts a
#: build of the engine changes masked. Written by running ``fix`` from the engine as it was before
#: the option; a change to a line of the report made on purpose is made in these files as well.
BASE = Path(__file__).resolve().parent / "fixtures" / "fix_report_base"


@pytest.fixture(scope="module")
def reachable(tmp_path_factory: pytest.TempPathFactory) -> fixture.FixProject:
    """Both roots are there, and the last census saw a file the one before did not."""
    return fixture.project("reachable", tmp_path_factory.mktemp("reachable"))


@pytest.fixture(scope="module")
def away(tmp_path_factory: pytest.TempPathFactory) -> fixture.FixProject:
    """Both roots are out of reach, and an interrupted run's folder is in staging."""
    return fixture.project("away", tmp_path_factory.mktemp("away"))


@pytest.fixture(autouse=True)
def _a_console_wide_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give ``fix`` a console of its own, with a width set, so no path in the plain report wraps.

    Rich reads ``COLUMNS`` and ``LINES`` when a console is made, and fixes its width for good if
    both are in the environment. The command's console is made when the command is first
    imported, so in a test worker it has whatever width the first test left in the environment,
    and a long temporary path then wraps. An explicit width does not depend on the environment.
    """
    monkeypatch.setattr("ingence.cli.main.console", Console(width=fixture.WIDTH))


def _fix(made: fixture.FixProject, *options: str) -> Result:
    return CliRunner().invoke(app, ["fix", "-p", str(made.path), *options])


def _private(made: fixture.FixProject) -> list[str]:
    """Everything the shareable report must not hold: paths, the pattern, run ids, every name."""
    return [
        str(made.path),
        str(made.roots[0]),
        str(made.roots[1]),
        fixture.PATTERN,
        *made.runs,
        fixture.ABANDONED,
        *fixture.NAMES,
    ]


# -- 1. none of the names, and the placeholders that stand for them -------------------------------


def test_the_shareable_report_holds_none_of_the_names_and_has_the_placeholders(
    away: fixture.FixProject,
) -> None:
    result = _fix(away, "--shareable", "--verify")
    said = result.output
    assert result.exit_code == 0, said
    for placeholder in ("<project>", "<root 1>", "<root 2>", "<pattern 1>", "<run 1>"):
        assert placeholder in said, said
    for private in _private(away):
        assert private not in said, (private, said)
    # The first pass caught everything: the backstop found nothing to replace.
    assert "were replaced" not in said


def test_the_report_is_the_same_one_with_the_placeholders_in_the_places_of_the_names(
    away: fixture.FixProject,
) -> None:
    shared = _fix(away, "--shareable", "--verify").output
    plain = _fix(away, "--verify").output
    expected = plain
    for private, placeholder in (
        (str(away.path), "<project>"),
        (str(away.roots[0]), "<root 1>"),
        (str(away.roots[1]), "<root 2>"),
        (fixture.PATTERN, "<pattern 1>"),
        (fixture.ABANDONED, "<run 1>"),
        (away.runs[0], "<run 2>"),
        (away.runs[1], "<run 3>"),
        (away.runs[2], "<run 4>"),
    ):
        expected = expected.replace(private, placeholder)
    # The same lines. (A wide terminal splits nothing; the plain report sorts the pattern lines
    # by the pattern's text, and the shareable one by its placeholder.)
    assert sorted(shared.splitlines()) == sorted(expected.splitlines())


def test_the_shareable_lines_say_which_root_run_and_pattern_is_which(
    away: fixture.FixProject,
) -> None:
    lines = _fix(away, "--shareable", "--verify").output.splitlines()
    assert lines[0] == "Project <project>"
    for number in (1, 2):
        assert (
            f"  data folder <root {number}>: cannot be reached now; not checked "
            "(it does not exist: not mounted, moved or removed)"
        ) in lines
    # Runs are numbered oldest first, the interrupted one among them.
    assert any(line.startswith("  current run <run 4>, updated ") for line in lines)
    assert "  file list <run 4> left out, on purpose:" in lines
    assert ("  staging folder left by a stopped scan: <run 1>.partial (safe to delete)") in lines
    assert "  runs <run 3> and <run 4> give the same results" in lines
    # The patterns every project starts with are the engine's, so they stay; the person's does not.
    assert "    left out, $RECYCLE.BIN: 1 folder (folders not opened)" in lines
    assert "    left out, <pattern 1>: 1 folder (folders not opened)" in lines
    assert "    left out, hidden names: 1 file" in lines
    # Counts that describe the engine stay.
    assert "  2 data folders" in lines


def test_a_root_that_is_a_file_is_a_problem_and_numbered_not_named(
    away: fixture.FixProject,
) -> None:
    away.roots[0].write_text("not a folder")
    try:
        result = _fix(away, "--shareable")
    finally:
        away.roots[0].unlink()
    assert result.exit_code == 1, result.output
    assert "  data folder <root 1>: not a folder" in result.output.splitlines()
    for private in _private(away):
        assert private not in result.output


def test_a_project_that_is_not_there_is_written_as_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "Okafor_not_a_project"
    empty.mkdir()
    named = CliRunner().invoke(app, ["fix", "--shareable", "-p", str(empty)])
    assert named.exit_code == 1
    assert named.output.strip() == "<project> is not an Ingence project; no project.json found"
    monkeypatch.chdir(empty)
    searched = CliRunner().invoke(app, ["fix", "--shareable"])
    assert searched.exit_code == 1
    assert (
        searched.output.strip()
        == "no Ingence project found at <project> or in any parent directory"
    )


def test_an_error_in_the_middle_leaves_the_report_so_far_and_names_only_its_class(
    reachable: fixture.FixProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fails(project: Path) -> None:
        raise RuntimeError(f"cannot read {project}/CURRENT in {fixture.FIRST_ROOT}")

    monkeypatch.setattr("ingence.cli.main.read_pointer", fails)
    result = _fix(reachable, "--shareable")
    assert result.exit_code == 1
    lines = result.output.splitlines()
    assert lines[0] == "Project <project>"
    assert lines[-1] == "  ingence fix stopped before it finished: RuntimeError"
    for private in _private(reachable):
        assert private not in result.output


# -- 2. the private scan ---------------------------------------------------------------------------


def _scan(directory: Path, text: str) -> subprocess.CompletedProcess[str]:
    """Run the scan's logic over a text, against a denylist of the fixture's invented names."""
    directory.mkdir()
    (directory / "denylist.txt").write_text(
        "\n".join(
            [
                "# invented names of the fixture, never real ones",
                *fixture.NAMES,
                "Priya",
                "run_20310101T",
                "_Jane_",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script = directory / "invented_scan.py"
    script.write_text(SCAN_SCRIPT, encoding="utf-8")
    report = directory / "report.txt"
    report.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script), str(report)], capture_output=True, text=True, check=False
    )


def test_the_shareable_output_passes_the_private_scan_and_the_plain_output_does_not(
    away: fixture.FixProject, tmp_path: Path
) -> None:
    shared = _scan(tmp_path / "scan_shared", _fix(away, "--shareable", "--verify").output)
    assert shared.returncode == 0, shared.stdout
    assert "0 match(es)" in shared.stdout
    # The scan can see these names: the plain report trips it, on a denylist line each time.
    plain = _scan(tmp_path / "scan_plain", _fix(away, "--verify").output)
    assert plain.returncode == 1
    assert "denylist line" in plain.stdout


# -- 3. the --verify rows --------------------------------------------------------------------------

_COUNT = re.compile(r"^    (\w+): (\d+) differing rows$", re.MULTILINE)


def test_the_verify_rows_are_left_out_and_their_counts_kept(
    reachable: fixture.FixProject,
) -> None:
    plain = _fix(reachable, "--verify")
    shared = _fix(reachable, "--shareable", "--verify")
    assert plain.exit_code == shared.exit_code == 1
    # Normally the rows are there.
    assert "      only in the later run: " in plain.output
    assert " has '" in plain.output
    # In the shareable report no row is: nothing is indented under a table's count except the
    # line that says how many were left out.
    assert "only in the" not in shared.output
    assert " has '" not in shared.output
    beneath = [line for line in shared.output.splitlines() if line.startswith("      ")]
    assert beneath
    left_out = [
        re.fullmatch(r"      (\d+) sample rows? left out of a shareable report", x) for x in beneath
    ]
    assert all(left_out), beneath
    # The counts are the plain report's, table by table, and so is the number of rows left out.
    assert _COUNT.findall(shared.output) == _COUNT.findall(plain.output)
    assert _COUNT.findall(shared.output)
    rows_shown = sum(1 for line in plain.output.splitlines() if line.startswith("      "))
    assert sum(int(match.group(1)) for match in left_out if match) == rows_shown
    assert "  runs <run 2> and <run 3> differ" in shared.output.splitlines()


# -- 4. without the option the report is the base engine's, to the byte ----------------------------


@pytest.mark.parametrize("name", ["reachable", "away"])
def test_without_the_option_the_report_is_the_one_the_engine_printed_before(
    name: str, reachable: fixture.FixProject, away: fixture.FixProject
) -> None:
    made = {"reachable": reachable, "away": away}[fixture.ASKED[name][0]]
    result = _fix(made, *fixture.ASKED[name][1])
    assert result.exit_code == int((BASE / f"{name}.exit").read_text())
    assert fixture.masked(result.output, made) == (BASE / f"{name}.txt").read_text()


def test_the_option_is_in_the_help_in_plain_words() -> None:
    result = CliRunner().invoke(app, ["fix", "--help"], env={"COLUMNS": "1000"})
    said = " ".join(result.output.split())
    assert "--shareable" in said
    assert "paste it into an email" in said
    assert "Values you typed in an older project's rules file" in said
    assert "<project>" in said


# -- the numbers, and the backstop -----------------------------------------------------------------


def _opened(made: fixture.FixProject) -> Placeholders:
    names = Placeholders(made.path)
    names.opened(Project.open(made.path))
    return names


def test_roots_are_numbered_as_the_project_lists_them_and_runs_oldest_first(
    reachable: fixture.FixProject,
) -> None:
    names = _opened(reachable)
    assert [names.root(str(root)) for root in reachable.roots] == ["<root 1>", "<root 2>"]
    assert [names.run(run) for run in reachable.runs] == ["<run 1>", "<run 2>", "<run 3>"]
    assert names.run(reachable.runs[2]) == "<run 3>"  # the same run is the same number everywhere
    assert names.run(fixture.ABANDONED) == "<run 4>"  # met later, numbered after
    assert names.project(reachable.path) == "<project>"


def test_two_runs_are_run_1_and_run_2(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "p", created_at="t", roots=[])
    for run in reversed(fixture.RUNS[:2]):
        (project.path / "runs" / run).mkdir()
    names = Placeholders(project.path)
    names.opened(project)
    assert [names.run(run) for run in fixture.RUNS[:2]] == ["<run 1>", "<run 2>"]


def test_patterns_are_numbered_in_the_settings_order_and_the_engines_own_stay(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    settings = Settings(
        scan=ScanSettings(exclude_globs=(*ScanSettings().exclude_globs, "backup_2031*", "*_Jane_*"))
    )
    project = Project.create(tmp_path / "p", created_at="t", roots=[pool], settings=settings)
    names = Placeholders(project.path)
    names.opened(project)
    assert names.pattern("*_Jane_*") == "<pattern 2>"
    assert names.pattern("backup_2031*") == "<pattern 1>"
    assert names.pattern("$RECYCLE.BIN") == "$RECYCLE.BIN"
    assert names.pattern(".git") == ".git"
    assert names.pattern("met_later") == "<pattern 3>"


def test_a_message_has_the_project_and_the_roots_replaced_and_what_lies_beneath_a_root(
    reachable: fixture.FixProject,
) -> None:
    names = _opened(reachable)
    first = str(reachable.roots[0])
    assert names.message(f"cannot read {first}") == "cannot read <root 1>"
    assert (
        names.message(f"[Errno 13] Permission denied: '{first}/Jane_Doe/plate.tif'")
        == "[Errno 13] Permission denied: '<root 1>/...'"
    )
    assert names.message(f"{reachable.path}/runs/x") == "<project>/..."
    # A longer name that only begins like a root is not that root.
    assert names.message(first + "2/x") == first + "2/x"
    # The other Unicode form of a name is the same name.
    assert names.message(unicodedata.normalize("NFD", first)) == "<root 1>"
    assert names.message("nothing of the data here") == "nothing of the data here"


def test_the_backstop_replaces_what_the_first_pass_did_not_see_and_counts_it(
    reachable: fixture.FixProject,
) -> None:
    names = _opened(reachable)
    leaked = (
        f"stray {reachable.roots[0]}/Jane_Doe/plate.tif and {reachable.path} and "
        f"{fixture.PATTERN} and {reachable.runs[0]}"
    )
    assert names.backstop(leaked) == "stray <root 1>/... and <project> and <pattern 1> and <run 1>"
    assert names.replaced == 4
    # A report with nothing left in it is returned as it is, and counts nothing more.
    assert names.backstop("a report of counts: 12 items") == "a report of counts: 12 items"
    assert names.replaced == 4


def test_the_backstop_leaves_a_placeholder_and_the_reports_own_words_alone(
    reachable: fixture.FixProject,
) -> None:
    names = _opened(reachable)
    names.pattern("root")
    names.pattern("a")
    names.pattern("old")
    text = "<root 1> and <pattern 2> are not a root of older runs, a person, or an old one"
    # "root" and "old" are patterns a person wrote: each is replaced as a whole word, but not
    # inside a placeholder or a longer word, and a pattern of one letter is not searched for.
    assert names.backstop(text) == (
        "<root 1> and <pattern 2> are not a <pattern 2> of older runs, a person, "
        "or an <pattern 4> one"
    )


def test_a_root_too_short_to_search_for_is_not_searched_for(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "p", created_at="t", roots=[pool])
    names = Placeholders(project.path)
    names.opened(project)
    names.root("/")
    assert names.backstop("a/b/c") == "a/b/c"


# -- the rules file an older project holds ---------------------------------------------------------


def _retired_project(tmp_path: Path) -> Project:
    """A project holding the retired rules file, with a word a person typed into one rule."""
    project = Project.create(tmp_path / fixture.PROJECT, created_at="t", roots=[])
    document: dict[str, Any] = retired_form()
    for rule in document["rules"]:
        for parameter in rule.get("parameters", ()):
            if parameter["name"] == "derivations":
                parameter["default"] = "each_deviation"
        if rule["id"] == "content_says_do_not_analyse":
            for parameter in rule["parameters"]:
                if parameter["name"] == "phrases":
                    parameter["default"] = ["donotanalyse", "Okafor_secret"]
    write_retired(project.path, document)
    return project


def test_a_rules_file_says_what_a_person_typed_as_a_pattern_and_keeps_the_engines_words(
    tmp_path: Path,
) -> None:
    project = _retired_project(tmp_path)
    plain = CliRunner().invoke(app, ["fix", "-p", str(project.path)])
    assert "Okafor_secret" in plain.output
    shared = CliRunner().invoke(app, ["fix", "--shareable", "-p", str(project.path)])
    assert shared.exit_code == 0, shared.output
    said = " ".join(shared.output.split())
    assert "Okafor_secret" not in said
    assert "content_says_do_not_analyse phrases: <pattern 1>" in said
    # A choice among a rule's own words, a number or a switch is the engine's, whoever chose it.
    assert "derivations: each deviation" in said
    assert "no longer read" in said
    assert "ingence fix --set-aside-old-rules -p '<project>'" in said
    for private in (str(project.path), fixture.PROJECT):
        assert private not in said


def test_setting_the_rules_file_aside_with_the_option_writes_no_name(tmp_path: Path) -> None:
    project = _retired_project(tmp_path)
    moved = CliRunner().invoke(
        app, ["fix", "--shareable", "--set-aside-old-rules", "-p", str(project.path)]
    )
    assert moved.exit_code == 0, moved.output
    assert (project.path / "rulebook" / "retired").is_dir()  # the move was made
    for private in (str(project.path), fixture.PROJECT, "Okafor_secret"):
        assert private not in moved.output


def test_a_quoted_path_beneath_a_root_is_replaced_to_its_closing_quote_spaces_included(
    reachable: fixture.FixProject,
) -> None:
    names = _opened(reachable)
    first = str(reachable.roots[0])
    quoted = f"[Errno 13] Permission denied: '{first}/Jane Doe/plate 03.tif' (read)"
    assert names.message(quoted) == "[Errno 13] Permission denied: '<root 1>/...' (read)"
    # Unquoted, a path goes on to the next space.
    assert names.message(f"cannot read {first}/Jane_Doe/x.tif now") == (
        "cannot read <root 1>/... now"
    )
    assert names.message(f"cannot read {first}/ now") == "cannot read <root 1>/... now"


def test_the_backstop_never_reads_inside_a_placeholder_it_wrote_itself(
    reachable: fixture.FixProject,
) -> None:
    names = _opened(reachable)
    names.pattern("root")
    names.pattern("run")
    leaked = f"{reachable.roots[0]}/x then {reachable.runs[0]} in a run"
    assert names.backstop(leaked) == "<root 1>/... then <run 1> in a <pattern 3>"


def test_a_name_the_first_pass_misses_is_replaced_by_the_backstop_and_the_report_says_so(
    reachable: fixture.FixProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A first pass that forgets to hide the project's path: the finished text still has none.
    monkeypatch.setattr(Placeholders, "project", lambda self, path: str(path))
    result = _fix(reachable, "--shareable")
    lines = result.output.splitlines()
    assert lines[0] == "Project <project>"
    assert str(reachable.path) not in result.output
    assert lines[-1] == (
        "  note: 1 name(s) of the data were still in this report after it was written "
        "and were replaced; please tell the developer"
    )


# -- a project given in a form that cannot be resolved, or in a relative form -----------------

#: The engine's source, put first on the path of a child process so it runs this checkout.
SOURCE = Path(__file__).resolve().parents[1] / "src"
STOPPED = "  ingence fix stopped before it finished: RuntimeError"


def _loop(tmp_path: Path) -> Path:
    """Two links that point at each other, with invented names."""
    first, second = tmp_path / "Loop_Okafor_a", tmp_path / "Loop_Okafor_b"
    try:
        first.symlink_to(second)
        second.symlink_to(first)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not make symbolic links")
    return first


def test_a_symbolic_link_loop_given_as_the_project_prints_no_traceback_and_no_name(
    tmp_path: Path,
) -> None:
    loop = _loop(tmp_path)
    done = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "fix", "--shareable", "-p", str(loop)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(SOURCE)},
    )
    assert done.returncode == 1
    assert done.stdout.splitlines() == [STOPPED]
    assert "Traceback" not in done.stderr
    for name in ("Loop_Okafor", str(tmp_path)):
        assert name not in done.stdout + done.stderr


def test_a_loop_or_an_unknown_home_folder_given_as_the_project_is_a_line_not_an_error(
    tmp_path: Path,
) -> None:
    for given in (str(_loop(tmp_path)), "~no_such_user_okafor/project"):
        result = CliRunner().invoke(app, ["fix", "--shareable", "-p", given])
        assert isinstance(result.exception, SystemExit), (given, result.exception)
        assert result.exit_code == 1
        assert result.output.splitlines() == [STOPPED]


def test_a_relative_project_path_does_not_damage_the_report_or_set_off_the_backstop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A project in a folder named like a word the report uses.
    Project.create(tmp_path / "data", created_at="t", roots=[])
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["fix", "--shareable", "-p", "data"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Project <project>"
    assert lines[1].startswith("  data format ")
    assert "were replaced" not in result.output
    # The absolute path is still hidden, wherever it is printed.
    assert str(tmp_path) not in result.output
