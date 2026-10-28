"""What a newcomer's install does, says and reads.

A newcomer's install must propose something. The pack proposal, the group lattice, both derivation
proposals and the word questions are the engine's own, so every project, old or new, reads them and
``init`` writes no rules file.

Every name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from ingence.api.routes import decide as decide_routes
from ingence.api.routes import pages
from ingence.cli.main import app
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.rulebook.retired import FILE_NAME
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import RULEBOOK_DIR

if TYPE_CHECKING:
    from pathlib import Path

#: A microscope's tree as a newcomer's might be: dated folders of sample words, and ND2 files
#: named with the words a microscopy pack knows. Every name is invented.
_ND2_TREE = (
    "2026-01-12 brisa confocal/brisa_gfp_zstack_01.nd2",
    "2026-01-12 brisa confocal/brisa_gfp_zstack_02.nd2",
    "2026-01-12 brisa confocal/brisa_dapi_zstack_03.nd2",
    "2026-02-03 kelt widefield/kelt_mcherry_timelapse_01.nd2",
    "2026-02-03 kelt widefield/kelt_mcherry_timelapse_02.nd2",
    "2026-02-03 kelt widefield/kelt_brightfield_03.nd2",
    "2026-02-03 kelt widefield/notes.txt",
)


def _tree(base: Path) -> Path:
    pool = base / "pool"
    for relative in _ND2_TREE:
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 16)
    return pool


def _without_the_nd2_default(project: Path) -> None:
    """Several ND2 files accept the microscopy pack by default, so nothing proposes it.

    These tests pin the question that proposes a pack, so the default is switched off.
    """
    import yaml

    settings = project / "settings.yaml"
    held = yaml.safe_load(settings.read_text())
    held.setdefault("dictionary", {})["files_to_accept_a_pack"] = 0
    settings.write_text(yaml.safe_dump(held))


def _said(output: str) -> str:
    return " ".join(output.split())


def test_a_bare_init_writes_no_rules_file_and_says_the_engine_proposes(tmp_path: Path) -> None:
    """The rules that propose are the engine's: nothing to write, nothing to leave out
    (``test_one_rulebook.py`` holds ``--rules``, retired).
    """
    pool = _tree(tmp_path)
    created = CliRunner().invoke(app, ["init", str(tmp_path / "project"), "--root", str(pool)])
    assert created.exit_code == 0, created.output
    assert not (tmp_path / "project" / RULEBOOK_DIR / FILE_NAME).exists()
    said = _said(created.output)
    assert "every shipped domain loads" in said
    assert "revision" not in said.casefold()


def test_a_run_never_writes_a_rules_file(tmp_path: Path) -> None:
    """A project an older ``init`` made holds no rules file, and a run never writes one."""
    project = Project.create(tmp_path / "project", created_at="t", roots=[_tree(tmp_path)])
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_000000000002", started_at="t")
    assert not (project.path / RULEBOOK_DIR / FILE_NAME).exists()


def _nd2_read(project: Path) -> int:
    with open_catalog(project) as catalog:
        return int(
            catalog.query(
                "SELECT count(DISTINCT b.entry_id) FROM dictionary_bindings b "
                "JOIN components c USING (component_id) "
                "WHERE c.component_type = 'extension' AND c.normalized_value = 'nd2' "
                "AND b.resolution_status = 'resolved'"
            ).fetchone()[0]
        )


#: A pool two packs would read: a microscope's files and a bench's, each named in its own words.
_TWO_PACK_TREE = (
    *_ND2_TREE,
    "2026-03-09 zeph bench/zeph_elisa_plate_01.csv",
    "2026-03-09 zeph bench/zeph_hplc_trace_02.csv",
    "2026-03-09 zeph bench/zeph_qpcr_run_03.csv",
    "2026-03-09 zeph bench/zeph_sdspage_04.csv",
)


def test_a_newcomers_install_finds_byte_identical_copies_after_a_dive(tmp_path: Path) -> None:
    """The shipped copy rule names the digest that a dive computes, so a newcomer finds copies."""
    pool = tmp_path / "pool"
    for number, relative in enumerate(_ND2_TREE):
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"zib frame {number}\n".encode() * 64)  # every file its own bytes
    copied = pool / "2026-02-03 kelt widefield" / "kept" / "brisa_gfp_zstack_01.nd2"
    copied.parent.mkdir()
    copied.write_bytes((pool / _ND2_TREE[0]).read_bytes())  # one copy, in another folder
    project = tmp_path / "project"
    runner = CliRunner()
    for command in (
        ["init", str(project), "--root", str(pool)],
        ["index", "-p", str(project), "--quiet"],
        ["dive", "-p", str(project), "--budget", "60"],
        ["infer", "-p", str(project)],
    ):
        done = runner.invoke(app, command)
        assert done.exit_code == 0, (command, done.output)
    with open_catalog(project) as catalog:
        classes = catalog.query(
            "SELECT c.rule_id, list_sort(list(e.relative_path)) FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id IN ('copies_by_sampled_digest', 'identical_by_content_digest') "
            "AND c.resolution_status = 'resolved' "
            "GROUP BY c.rule_id, c.object_reference HAVING count(*) > 1"
        ).fetchall()
    pair = sorted([_ND2_TREE[0], "2026-02-03 kelt widefield/kept/brisa_gfp_zstack_01.nd2"])
    assert classes, "no copy class"
    assert all(members == pair for _, members in classes)


def test_a_folder_of_miscellaneous_files_is_offered_accept_and_refuse_like_a_pack(
    tmp_path: Path,
) -> None:
    """The same class as a pack's: a proposal's row offers its proposal's words, and the answer
    route takes them on the question's own target.
    """
    pool = tmp_path / "pool"
    for relative in (
        "20240301 kelt vorn/kelt_vorn_01.nd2",
        "20240301 kelt vorn/kelt_vorn_02.nd2",
        "brisa assay/plate layout.xlsx",
        "brisa assay/brisa scores.csv",
    ):
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_000000000002", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = pages.page(catalog, project.path, "questions", limit=1000)["rows"]
    (row,) = [row for row in rows if (row.get("proposal") or {}).get("kind") == "miscellaneous"]
    assert [word["word"] for word in row["words"]] == ["accept", "refuse"]
    with open_catalog(project.path) as catalog:
        context = decide_routes.DecisionContext(catalog, project.path)
        written = decide_routes.decide_target(
            context,
            decide_routes.Decision.from_body({"target": row["target"], "word": "accept"}),
            actor="vorn (an invented person)",
            occurred_at="2026-09-30T00:00:00Z",
        )
    assert written.target == row["proposal"]["target"]
    assert written.kind.value == "confirm"


def _later_than_the_runs() -> str:
    """Now, a second on, so a run started next begins after it (a run in the same second waits)."""
    import time
    from datetime import UTC, datetime

    time.sleep(1.1)
    stamp = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    time.sleep(1.1)
    return stamp
