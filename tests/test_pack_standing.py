"""Every shipped section stands level.

A spelling that the core and a shipped domain section both hold is a reported tie in the
vocabulary. A run reads it from the folder's files or, failing that, as the core's meaning marked
assumed (``name:universal_sense``), with the domain's sense kept as the shadowed alternative. A
laboratory's own pack stands level too, and a person's answer still beats everything. Every name
here is invented.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from ingence.core.project import Project
from ingence.core.schema import DictionaryOrigin
from ingence.dictionary.store import ORIGIN_PRECEDENCE, DictionaryStore, Section, Sense, origin_rank
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


def _one(
    name: str,
    *,
    term: str,
    surface: str,
    kind: str,
    origin: DictionaryOrigin = DictionaryOrigin.BUILTIN,
    domain: str | None = None,
) -> Section:
    """A section of one sense: the core's where no domain is given, a pack's where one is."""
    return Section(
        name=name,
        summary="",
        origin=origin,
        domain=domain,
        senses=(
            Sense(
                term=term,
                canonical=term,
                theme_kind=kind,
                section=name,
                surfaces=(surface,),
                origin=origin,
                domain=domain,
            ),
        ),
    )


def test_a_spelling_the_core_and_a_shipped_pack_define_differently_ties() -> None:
    store = DictionaryStore(
        (
            _one(
                "kelt_pack", term="zorbit_scope", surface="zorbit", kind="instrument", domain="kelt"
            ),
            _one("positions", term="zorbit_place", surface="zorbit", kind="position"),
        )
    )
    hit = store.look_up("zorbit")
    assert hit is not None
    assert hit.ambiguous
    assert {hit.sense.term, *(sense.term for sense in hit.alternatives)} == {
        "zorbit_place",
        "zorbit_scope",
    }
    assert hit.shadowed == ()
    (contested,) = store.collisions()
    assert contested.reported


def test_a_laboratory_pack_colliding_with_the_core_still_ties() -> None:
    store = DictionaryStore(
        (
            _one(
                "vorn_lab",
                term="zorbit_scope",
                surface="zorbit",
                kind="instrument",
                origin=DictionaryOrigin.PACK,
                domain="kelt",
            ),
            _one("positions", term="zorbit_place", surface="zorbit", kind="position"),
        )
    )
    hit = store.look_up("zorbit")
    assert hit is not None
    assert hit.ambiguous
    assert {hit.sense.term, *(sense.term for sense in hit.alternatives)} == {
        "zorbit_scope",
        "zorbit_place",
    }


def test_two_shipped_packs_claiming_one_spelling_still_tie() -> None:
    store = DictionaryStore(
        (
            _one(
                "kelt_pack", term="zorbit_scope", surface="zorbit", kind="instrument", domain="kelt"
            ),
            _one(
                "zeph_pack", term="zorbit_assay", surface="zorbit", kind="technique", domain="zeph"
            ),
        )
    )
    hit = store.look_up("zorbit")
    assert hit is not None and hit.ambiguous


def test_a_person_s_answer_still_beats_the_core_and_the_pack() -> None:
    store = DictionaryStore(
        (
            _one(
                "kelt_pack", term="zorbit_scope", surface="zorbit", kind="instrument", domain="kelt"
            ),
            _one("positions", term="zorbit_place", surface="zorbit", kind="position"),
            _one(
                "answers",
                term="zorbit_mine",
                surface="zorbit",
                kind="technique",
                origin=DictionaryOrigin.USER,
            ),
        )
    )
    hit = store.look_up("zorbit")
    assert hit is not None and hit.sense.term == "zorbit_mine"
    assert {sense.term for sense in hit.shadowed} == {"zorbit_scope", "zorbit_place"}


def test_every_shipped_sense_stands_level() -> None:
    core = _one("positions", term="a", surface="a", kind="position").senses[0]
    shipped = _one("kelt_pack", term="b", surface="b", kind="position", domain="kelt").senses[0]
    lab = _one(
        "vorn", term="c", surface="c", kind="position", origin=DictionaryOrigin.PACK, domain="kelt"
    ).senses[0]
    assert origin_rank(core) == ORIGIN_PRECEDENCE[DictionaryOrigin.BUILTIN]
    assert origin_rank(lab) == ORIGIN_PRECEDENCE[DictionaryOrigin.PACK] == origin_rank(core)
    assert origin_rank(shipped) == origin_rank(core)


def test_every_collision_between_the_core_and_a_shipped_pack_is_reported() -> None:
    """Over what ships: a core sense and a domain's sense of one spelling tie, nothing shadowed."""
    found = 0
    for contested in DictionaryStore.builtin().collisions():
        core = [sense for sense in contested.senses if sense.domain is None]
        packs = [sense for sense in contested.senses if sense.domain is not None]
        if not core or not packs:
            continue
        found += 1
        assert contested.reported, contested.surface
        assert contested.shadowed == (), contested.surface
    assert found, "the shipped vocabulary holds spellings the core and a pack share"


def test_no_core_section_declares_a_domain() -> None:
    """A shipped sense's standing is read from its domain, so the core must declare none."""
    from ingence.dictionary.store import BUILTIN_DIRECTORY, load_sections

    assert [section.name for section in load_sections(BUILTIN_DIRECTORY) if section.domain] == []


def test_a_name_binds_the_core_by_default_and_keeps_the_pack_s_sense_as_its_shadowed_alternative(
    tmp_path: Path,
) -> None:
    """End to end: ``fov`` is a position in the core and a field of view in the microscopy pack;
    one file in no domain's folder reads the core's, marked assumed (``name:universal_sense``)."""
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "brisa_fov_kelt.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {}).update(domain_packs="all")
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    project = Project.open(project.path)
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT b.resolution_status, b.term, b.sense_origin_rank, b.alternatives_json, "
            'b."constraint" '
            "FROM dictionary_bindings b JOIN components c ON c.component_id = b.component_id "
            "WHERE lower(c.normalized_value) = 'fov' AND b.source_scope = 'name'"
        ).fetchall()
    ((status, term, rank, alternatives, constraint),) = rows
    assert (status, term, rank, constraint) == (
        "resolved",
        "position",
        ORIGIN_PRECEDENCE[DictionaryOrigin.BUILTIN],
        "name:universal_sense",
    )
    listed = json.loads(alternatives) if isinstance(alternatives, str) else alternatives
    assert [(item["shadowed"], item["origin_rank"]) for item in listed] == [
        (True, ORIGIN_PRECEDENCE[DictionaryOrigin.BUILTIN])
    ]


def _all_packs_project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "brisa_fov_kelt.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {}).update(domain_packs="all")
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    project = Project.open(project.path)
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    return project


def test_the_dictionary_page_lists_where_the_core_and_a_pack_disagree(tmp_path: Path) -> None:
    from ingence.api.routes.dictionary import packs

    project = _all_packs_project(tmp_path)
    with open_catalog(project.path) as catalog:
        found = packs(catalog, project.path)
    rows = {row["spelling"]: row for row in found["collisions"]}
    assert rows["fov"]["used"] == "neither"
    assert rows["fov"]["core"] == [{"term": "position", "kind": "position"}]
    assert rows["fov"]["packs"] == [
        {"pack": "microscopy", "term": "field_of_view", "kind": "acquisition", "laboratory": False}
    ]
    # Only where the core and a pack both hold a spelling: two packs alone are not listed here.
    assert all(row["core"] and row["packs"] for row in rows.values())


def test_the_page_says_a_laboratory_pack_s_collision_is_a_tie(tmp_path: Path) -> None:
    from ingence.api.routes.dictionary import packs
    from ingence.dictionary.laboratory_packs import install_pack

    project = _all_packs_project(tmp_path)
    source = tmp_path / "vorn-lab"
    source.mkdir()
    (source / "pack.yaml").write_text(yaml.safe_dump({"name": "vorn-lab", "version": "1"}))
    (source / "words.yaml").write_text(
        yaml.safe_dump(
            {
                "section": "vorn_words",
                "theme_kind": "technique",
                "entries": [{"term": "zorbit_assay", "surfaces": ["zorbit", "raw"]}],
            }
        )
    )
    install_pack(project.path, source)
    with open_catalog(project.path) as catalog:
        found = packs(catalog, project.path)
    rows = {row["spelling"]: row for row in found["collisions"]}
    assert rows["raw"]["used"] == "neither"
    assert {
        "pack": "vorn-lab",
        "term": "zorbit_assay",
        "kind": "technique",
        "laboratory": True,
    } in (rows["raw"]["packs"])


def test_the_page_leaves_out_a_format_spelled_only_after_a_dot(tmp_path: Path) -> None:
    """``md`` is markdown after a dot and molecular dynamics in a word: they never meet."""
    from ingence.api.routes.dictionary import packs

    project = _all_packs_project(tmp_path)
    with open_catalog(project.path) as catalog:
        spelled = {row["spelling"] for row in packs(catalog, project.path)["collisions"]}
    assert "fov" in spelled
    assert "md" not in spelled


def _with_laboratory_pack(tmp_path: Path, spellings: list[str]) -> Project:
    from ingence.dictionary.laboratory_packs import install_pack

    project = _all_packs_project(tmp_path)
    source = tmp_path / "vorn-lab"
    source.mkdir()
    (source / "pack.yaml").write_text(yaml.safe_dump({"name": "vorn-lab", "version": "1"}))
    (source / "words.yaml").write_text(
        yaml.safe_dump(
            {
                "section": "vorn_words",
                "theme_kind": "technique",
                "entries": [{"term": "zorbit_assay", "surfaces": spellings}],
            }
        )
    )
    install_pack(project.path, source)
    return project


def test_a_laboratory_pack_and_a_shipped_pack_tie_on_the_page_and_the_command(
    tmp_path: Path,
) -> None:
    """A laboratory's pack and a shipped domain section stand level: where both spell a
    word the page says neither is used and ``teach collisions`` reports the tie, never "decided
    by neither"."""
    from typer.testing import CliRunner

    from ingence.api.routes.dictionary import packs
    from ingence.cli.main import app

    project = _with_laboratory_pack(tmp_path, ["zorbit", "confocal"])
    with open_catalog(project.path) as catalog:
        rows = {row["spelling"]: row for row in packs(catalog, project.path)["collisions"]}
    assert rows["confocal"]["used"] == "neither"
    assert {item["pack"] for item in rows["confocal"]["packs"]} == {"vorn-lab", "microscopy"}
    said = " ".join(
        CliRunner()
        .invoke(app, ["teach", "collisions", "-p", str(project.path), "--decided", "-n", "200"])
        .output.split()
    )
    assert "No collision is decided" in said


def test_installing_a_laboratory_pack_says_the_spellings_it_ties_with(
    tmp_path: Path,
) -> None:
    from typer.testing import CliRunner

    from ingence.cli.main import app

    project = _all_packs_project(tmp_path)
    source = tmp_path / "vorn-lab"
    source.mkdir()
    (source / "pack.yaml").write_text(yaml.safe_dump({"name": "vorn-lab", "version": "1"}))
    (source / "words.yaml").write_text(
        yaml.safe_dump(
            {
                "section": "vorn_words",
                "theme_kind": "technique",
                "entries": [{"term": "zorbit_assay", "surfaces": ["zorbit", "confocal"]}],
            }
        )
    )
    result = CliRunner().invoke(
        app, ["teach", "pack", "install", str(source), "-p", str(project.path)]
    )
    assert result.exit_code == 0, result.output
    said = " ".join(result.output.split())
    assert "The pack and the Dictionary now both define" in said
