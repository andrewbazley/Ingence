"""A command's summary says what was read, what was concluded, what waits, and where to look.

Each summary is a few plain lines that end with where to look. A summary does not print tables
of truncated rule names and columns, a paragraph per rule that "publishes no conclusion" or is
"not evaluable", "N proposing rules name no index", "reuse: no_previous_record N", a dive's digest
mode, why each file was chosen, or publish timings. The developer's detail is printed with
``--verbose``, never lost. The tests pin what each summary says, not every word. Every name is
invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from ingence.cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

_TREE = (
    "2026-01-12 brisa confocal/brisa_gfp_zstack_01.nd2",
    "2026-01-12 brisa confocal/brisa_gfp_zstack_02.nd2",
    "2026-01-12 brisa confocal/brisa_dapi_zstack_03.nd2",
    "2026-02-03 kelt widefield/kelt_mcherry_timelapse_01.nd2",
    "2026-02-03 kelt widefield/kelt_mcherry_timelapse_02.nd2",
    "2026-02-03 kelt widefield/kelt_notes.txt",
)

#: What only a developer reads: per-rule tables, rule-by-rule notes, reuse reasons, timings.
_DEVELOPER_WORDS = (
    "┃",
    "starved",
    "publishes no conclusion",
    "not evaluable",
    "name no index",
    "held the whole pool",
    "no_previous_record",
    "reuse:",
    "chosen because",
    "digest:",
    "publish:",
)


def _said(output: str) -> str:
    return " ".join(output.split())


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("summaries")
    pool = base / "pool"
    for relative in _TREE:
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 16)
    created = CliRunner().invoke(app, ["init", str(base / "project"), "--root", str(pool)])
    assert created.exit_code == 0, created.output
    # The ND2 default is off: several ND2 files accept the microscopy pack. So the summary is shown
    # proposing a pack to a person.
    settings = base / "project" / "settings.yaml"
    held = yaml.safe_load(settings.read_text())
    held.setdefault("dictionary", {})["files_to_accept_a_pack"] = 0
    settings.write_text(yaml.safe_dump(held))
    return base / "project"


def _run(*arguments: str) -> str:
    done = CliRunner().invoke(app, list(arguments))
    assert done.exit_code == 0, done.output
    return done.output


def test_init_ends_with_the_next_command(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    said = _said(_run("init", str(tmp_path / "project"), "--root", str(pool)))
    assert f"Next: ingence index -p {tmp_path / 'project'}" in said
    alone = _said(_run("init", str(tmp_path / "bare")))
    assert "Next:" not in alone  # no root, nothing to index yet


def test_index_says_what_it_read_and_what_comes_next(project: Path) -> None:
    said = _said(_run("index", "-p", str(project), "--quiet"))
    assert "Listed 9 items" in said
    assert "6 files, 3 folders" in said
    # The step the README and the getting-started guide name after the census.
    assert f"Next: ingence dive -p {project}" in said
    assert "ingence infer" not in said
    for word in _DEVELOPER_WORDS:
        assert word not in said, word
    detail = _said(_run("index", "-p", str(project), "--quiet", "--verbose"))
    assert "reuse: reuse 6" in detail  # the reuse reasons, behind --verbose


def test_a_rebind_ends_with_infer_as_the_readme_says(project: Path) -> None:
    """Re-reading the names offline opens no file, so what follows it is ``infer`` (README)."""
    _run("index", "-p", str(project), "--quiet")
    said = _said(_run("index", "--rebind", "-p", str(project), "--quiet"))
    assert f"Next: ingence infer -p {project}" in said
    assert "ingence dive" not in said


def test_infer_says_what_it_concluded_what_waits_and_where_to_look(project: Path) -> None:
    _run("index", "-p", str(project), "--quiet")
    output = _run("infer", "-p", str(project))
    said = _said(output)
    assert "results" in said
    # No pack is proposed: every domain's words load.
    assert "Packs proposed" not in said
    assert f"ingence see -p {project}" in said  # where to look
    # Counted, never listed, and never said to be the data's fault.
    assert "rules are not available in this version" in said
    assert "could not run on this data" not in said
    for word in _DEVELOPER_WORDS:
        assert word not in said, word
    assert len(output.strip().splitlines()) <= 8  # a few lines, even wrapped at 80 columns
    detail = _said(_run("infer", "-p", str(project), "--verbose"))
    assert "starved" in detail  # each rule's counts
    assert "publishes no conclusion" in detail  # and why a rule published nothing
    assert "held the whole pool at once" in detail


def test_dive_says_what_it_read_and_keeps_its_timings_behind_verbose(project: Path) -> None:
    _run("index", "-p", str(project), "--quiet")  # its own census, whatever ran before it
    said = _said(_run("dive", "-p", str(project), "--budget", "30"))
    assert "Read" in said and "files" in said
    assert f"ingence infer -p {project}" in said
    for word in _DEVELOPER_WORDS:
        assert word not in said, word
    detail = _said(_run("dive", "-p", str(project), "--budget", "30", "--verbose"))
    assert "publish:" in detail
    assert "digest:" in detail
    # A dive over a pool already read says so, not only "Read 0 files".
    again = _said(_run("dive", "-p", str(project), "--budget", "30"))
    assert "Nothing left to read" in again


def test_a_next_command_is_quoted_so_a_path_with_spaces_or_brackets_pastes_whole(
    tmp_path: Path,
) -> None:
    """A "Next:" line is for copying; rich markup in a path is printed as written."""
    import shlex

    pool = tmp_path / "pool"
    pool.mkdir()
    project = tmp_path / "Lab Data [b] brisa" / "project"
    project.parent.mkdir()
    said = _said(_run("init", str(project), "--root", str(pool)))
    assert f"Next: ingence index -p {shlex.quote(str(project))}" in said
    assert "[b]" in said


def _names_only_project(base: Path) -> Path:
    """A project made with ``--depth metadata`` over an invented pool, indexed."""
    pool = base / "pool"
    for relative in _TREE:
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 16)
    created = CliRunner().invoke(
        app, ["init", str(base / "project"), "--root", str(pool), "--depth", "metadata"]
    )
    assert created.exit_code == 0, created.output
    return base / "project"


def test_a_names_only_project_is_not_sent_to_a_dive_that_would_open_nothing(
    tmp_path: Path,
) -> None:
    """``index`` ended "Next: ingence dive" on a project made with ``--depth metadata``, and the
    dive then refused, naming the depth "census" where the person had typed "metadata". The next
    step is the one that works, and both messages say the word the person used."""
    project = _names_only_project(tmp_path)
    said = _said(_run("index", "-p", str(project), "--quiet"))
    assert f"Next: ingence infer -p {project}" in said
    assert "Next: ingence dive" not in said
    assert (
        "reads names, sizes and dates only (depth metadata), so it reads no file contents" in said
    )
    assert "census" not in said.replace("a census", "")

    refused = CliRunner().invoke(app, ["dive", "-p", str(project)])
    assert refused.exit_code == 1, refused.output
    words = _said(refused.output)
    assert "depth metadata" in words
    assert "census" not in words
    # The fact, neutrally: no nudge to open the files, and who decides a deeper dive.
    assert "so `ingence dive` reads no contents" in words
    assert "if the folder's owner agrees" in words
    assert "--depth standard or --depth deep" in words
    assert "Use ingence" not in words
    for push in ("you should", "we recommend", "try "):
        assert push not in words


def test_a_depth_typed_to_a_dive_is_named_as_typed(tmp_path: Path) -> None:
    project = _names_only_project(tmp_path)
    for typed in ("metadata", "census"):
        refused = CliRunner().invoke(app, ["dive", "-p", str(project), "--depth", typed])
        assert refused.exit_code == 1, refused.output
        words = _said(refused.output)
        assert f"Depth {typed} reads names, sizes and dates only" in words
        assert "if the folder's owner agrees" in words


def test_the_depth_a_person_says_is_the_word_a_message_uses() -> None:
    from ingence.scan.profiles import ProfileName, depth_named, depth_word

    assert depth_word(ProfileName.CENSUS) == "metadata"
    assert depth_word(ProfileName.STANDARD) == "standard"
    assert depth_word(ProfileName.DEEP) == "deep"
    for said in ("metadata", "standard", "deep"):
        assert depth_word(depth_named(said)) == said


def test_infer_says_where_a_question_is_asked_when_it_is_not_the_questions_page(
    tmp_path: Path,
) -> None:
    """``infer`` says where a question is asked, by the rule the Dashboard uses to decide the page.

    On a small pool, ``infer`` says "1 question raised" while the Questions page shows none. The
    one question is whether two dated experiment folders belong together, which the Dashboard asks
    on the Groupings page and never on the Questions page. The count is the same, and the summary
    says where."""
    from ingence.api.routes import pages as page_routes
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    for folder in ("kelt_2024-03-04", "kelt_2024-05-06"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "vorn_readings.csv").write_text("sample,value\nkelt_a,1\nkelt_b,2\n")
        (pool / folder / "vorn_summary.csv").write_text("sample,value\nkelt_a,3\n")
        (pool / folder / "notes.txt").write_text("notes on the run\n")
        (pool / folder / "fit.py").write_text('print("vorn")\n')
    project = tmp_path / "project"
    _run("init", str(project), "--root", str(pool))
    _run("index", "-p", str(project), "--quiet")
    _run("dive", "-p", str(project))
    said = _said(_run("infer", "-p", str(project)))
    assert "1 check raised, on the Groupings page" in said

    # The Dashboard agrees: the Questions page lists none and counts that one beside them.
    with open_catalog(project) as catalog:
        shown = page_routes.page(catalog, project, "questions", limit=50)
    assert shown["rows"] == []
    assert shown["asked_on_groupings"] == 1
    assert shown["programs_named"] == 0


def test_the_questions_raised_are_split_by_the_page_that_asks_them() -> None:
    from ingence.cli.main import _questions_raised

    assert _questions_raised(3, {}) == "3 checks raised"
    assert _questions_raised(1, {"Questions": 1}) == "1 check raised"
    assert _questions_raised(2, {"Questions": 2}) == "2 checks raised"
    assert _questions_raised(1, {"Groupings": 1}) == "1 check raised, on the Groupings page"
    assert _questions_raised(2, {"Programs": 2}) == "2 checks raised, on the Programs page"
    assert _questions_raised(3, {"Questions": 1, "Groupings": 2}) == (
        "3 checks raised: 1 on the Questions page, 2 on the Groupings page"
    )
    assert _questions_raised(5, {"Questions": 1, "Groupings": 2, "Programs": 2}) == (
        "5 checks raised: 1 on the Questions page, 2 on the Groupings page, 2 on the Programs page"
    )


def test_the_pages_say_which_page_asks_a_question_in_one_place() -> None:
    from ingence.api.routes.pages import page_asking

    assert page_asking("experiments_may_belong_together", "experiments_may_belong_together") == (
        "Groupings"
    )
    assert page_asking("installed_software_in_scope", "installed_software_in_scope") == "Programs"
    assert page_asking("a_rule_naming_installed_software_somehow", None) == "Programs"
    assert page_asking("domain_pack_would_read", "domain_pack_would_read") == "Questions"
    assert page_asking("anything_else", None) == "Questions"


def test_infer_counts_what_is_set_aside_inside_an_installed_program_under_programs(
    tmp_path: Path,
) -> None:
    """Questions raised only inside an installed program's folder are set aside and asked on the
    Programs page; the first split counted them under Questions, so the line said 3 where the
    Questions page listed 1. It is counted from the queue the page is built from."""
    from ingence.api.routes import pages as page_routes
    from ingence.storage.catalog import open_catalog

    def write(path: Path, text: str = "x") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    pool = tmp_path / "pool"
    program = pool / "Zeph" / "zeph-linux64" / "Zeph.app"
    write(program / "ImageJ-linux64", "launcher")
    write(program / "README.md", "# Zeph")
    for number in range(12):
        write(program / "jars" / f"vorn-library-{number}.jar")
    write(program / "jars" / "n5-kelt-3.1.jar")
    write(program / "jars" / "ij-1.54f.jar")
    for number in range(6):
        write(program / "plugins" / f"brisa_plugin_{number}.jar")
    write(program / "luts" / "fire.lut")
    runtime = program / "java" / "linux64" / "jdk1.8.0_172" / "jre"
    write(runtime / "release", "JAVA_VERSION")
    write(runtime / "bin" / "java", "binary")
    for folder in ("kelt_2024-03-04", "kelt_2024-05-06"):
        write(pool / folder / "vorn_readings.csv", "sample,value\nkelt_a,1\n")
        write(pool / folder / "vorn_summary.csv", "sample,value\nkelt_a,3\n")
        write(pool / folder / "notes.txt", "notes on the run\n")
        write(pool / folder / "fit.py", 'print("vorn")\n')
    project = tmp_path / "project"
    _run("init", str(project), "--root", str(pool))
    _run("index", "-p", str(project), "--quiet")
    _run("dive", "-p", str(project))
    said = _said(_run("infer", "-p", str(project)))

    with open_catalog(project) as catalog:
        shown = page_routes.page(catalog, project, "questions", limit=500)
    listed, aside = len(shown["rows"]), shown["set_aside"]
    groupings, programs = shown["asked_on_groupings"], shown["programs_named"]
    assert aside > 0 and programs > 0 and groupings > 0, shown  # the case this pins
    total = listed + aside + groupings + programs
    assert (
        f"{total} checks raised: {listed} on the Questions page, {groupings} on the Groupings "
        f"page, {aside + programs} on the Programs page"
    ) in said
