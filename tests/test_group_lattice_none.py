"""The lattice names no mode where no rule in the run resolves groups.

On the shipped Rulebook alone (``init --rules shipped``, or a project an older ``init`` made) no
rule proposes or resolves a group of experiments. ``/api/v1/groups/lattice`` says ``none``, not
``each_deviation``, which is the mode of a rule that is not there. The Groupings page says in
words that the run proposes no groups. Every name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for relative in (
        "2026-01-12 brisa confocal/brisa_gfp_zstack_01.nd2",
        "2026-01-13 brisa confocal/brisa_gfp_zstack_02.nd2",
        "2026-02-03 kelt widefield/kelt_mcherry_01.nd2",
    ):
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 16)
    return pool


def test_with_no_group_rule_in_the_run_the_lattice_names_no_mode(tmp_path: Path) -> None:
    """On a Rulebook with no group rule nothing asks about groups, and the lattice says ``none``.

    The book at revision 3 is one. Every project's own Rulebook holds the group rules.
    """
    from ingence.api.v1 import V1Context
    from ingence.api.v1 import groups as group_routes
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook

    load_builtins()
    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    run = "run_20260101T000002Z_000000000002"
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(
        project, run_id=run, started_at="t", rulebook=effective_rulebook(load_revisions(), at=3)
    )
    context = V1Context(project=project.path, run_current=run, run_pinned=run, job_running=False)
    shown = group_routes.lattice(context, {})
    assert shown.resolving_rules_in_run is False
    assert shown.mode == "none"
    assert shown.sets == []


def test_the_groupings_page_says_in_words_when_the_run_proposes_no_group() -> None:
    """The section says so rather than showing nothing."""
    from ingence.api.server import WEB_ROOT

    script = (WEB_ROOT / "groups.js").read_text(encoding="utf-8")
    assert "found.mode === 'none'" in script
    assert "This scan suggests no groups of experiments." in script
