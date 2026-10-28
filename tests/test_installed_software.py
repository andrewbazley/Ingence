"""An installed program is a scope, not an experiment.

The layout rule on hand-built folders, and then on a pool where a program's layout sits beside a
laboratory's folder: what the rule raises, and what the rules naming it as a defeater publish
beneath the folder it raised, as conflicting, blocked or marked, and never hidden.

Every name here is invented; the layout conventions are packaging's, not anybody's laboratory's.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from fixtures.one_folder_pairs import allow_pairs_in_one_folder
from fixtures.revision_4 import REVISION, write_revision
from ingence.core.ids import Scope, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType
from ingence.infer.run import run_inference
from ingence.infer.subjects import EntryRecords, ScopeRecords
from ingence.rulebook.predicates.flag import InstalledSoftwareInScope
from ingence.rulebook.registry import Abstention, Raised
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

RULE = next(rule for rule in REVISION["rules"] if rule["id"] == "installed_software_in_folder")  # type: ignore[index]
DEFAULTS = {parameter["name"]: parameter["default"] for parameter in RULE["parameters"]}


def _scope(children: list[tuple[str, EntryType]]) -> ScopeRecords:
    entries = tuple(
        EntryRecords(
            entry=Entry(
                entry_id=f"entry_{index}",
                root_id="root_x",
                relative_path=f"tool/{name}",
                name=name,
                entry_type=kind,
                extension=name.rsplit(".", 1)[-1] if "." in name and kind is EntryType.FILE else "",
                parent_entry_id="entry_tool",
                first_seen_run="r",
                last_seen_run="r",
            )
        )
        for index, (name, kind) in enumerate(children)
    )
    return ScopeRecords(scope=Scope(ScopeKind.FOLDER, "entry_tool"), entries=entries)


def test_folders_named_for_a_program_s_parts_side_by_side_are_its_layout():
    scope = _scope(
        [("bin", EntryType.DIRECTORY), ("lib", EntryType.DIRECTORY), ("README", EntryType.FILE)]
    )
    raised = InstalledSoftwareInScope().evaluate(scope, DEFAULTS)
    assert isinstance(raised, Raised)
    assert raised.values == ("layout folders",)
    assert set(raised.inputs) == {"entry_0", "entry_1"}


def test_a_folder_mostly_of_headers_and_libraries_is_a_program_s():
    children = [(f"part{index}.h", EntryType.FILE) for index in range(25)]
    children += [(f"notes{index}.txt", EntryType.FILE) for index in range(5)]
    raised = InstalledSoftwareInScope().evaluate(_scope(children), DEFAULTS)
    assert isinstance(raised, Raised)
    assert raised.values == ("program files",)
    assert "25 of its 30 files" in raised.reason


def test_a_packaging_file_alone_raises_nothing_and_says_so():
    """A packaging file with no code beside it is not a package, and data beside it is data.

    A ``setup.py`` beside a module is a Python package by its packaging, which
    ``test_program_layouts.py`` holds. A laboratory's own package is found by file structure
    and type.
    """
    scope = _scope(
        [
            ("setup.py", EntryType.FILE),
            ("plate_alpha.csv", EntryType.FILE),
            ("data", EntryType.DIRECTORY),
        ]
    )
    predicate = InstalledSoftwareInScope()
    assert predicate.evaluate(scope, DEFAULTS) is None
    abstention = predicate.explain_abstention(scope, DEFAULTS)
    assert isinstance(abstention, Abstention)
    assert "raises nothing alone" in abstention.reason


def test_a_stray_header_among_data_is_not_a_program():
    children = [("config.h", EntryType.FILE)] + [
        (f"plate{index}.tif", EntryType.FILE) for index in range(40)
    ]
    assert InstalledSoftwareInScope().evaluate(_scope(children), DEFAULTS) is None


# ---------------------------------------------------------------------------
# On a pool: what the defeaters publish beneath the folder the rule raised.
# ---------------------------------------------------------------------------

LONG_AGO = 1_577_836_800  # 2020-01-01, before the stub rule's date


def _in_turn(first, second) -> None:  # type: ignore[no-untyped-def]
    """The first file written an hour before the second."""
    os.utime(first, (LONG_AGO + 3600, LONG_AGO + 3600))
    os.utime(second, (LONG_AGO + 7200, LONG_AGO + 7200))


@pytest.fixture
def inferred(tmp_path):
    pool = tmp_path / "pool"
    tool = pool / "vendortool"
    for part in ("bin", "lib", "include"):
        (tool / part).mkdir(parents=True)
    (tool / "bin" / "vendortool").write_bytes(b"\x7fELF")
    (tool / "lib" / "libvendor.so").write_bytes(b"\x7fELF")
    (tool / "include" / "vendor.h").write_text("int f(void);\n")
    share = tool / "share" / "examples"
    share.mkdir(parents=True)
    (share / "raw_table_sample.csv").write_text("a,b\n1,2\n")
    (share / "processed_table_sample.csv").write_text("a,b\n1,2\n")
    (share / "placeholder.dat").write_bytes(b"")
    lab = pool / "plates"
    lab.mkdir()
    (lab / "raw_plate_alpha.csv").write_text("a,b\n1,2\n")
    (lab / "processed_plate_alpha.csv").write_text("a,b\n1,2\n")
    # Each raw file an hour before its processed one: a pair's direction is the earlier file's, by
    # its earliest credible time, and never its stage words.
    _in_turn(share / "raw_table_sample.csv", share / "processed_table_sample.csv")
    _in_turn(lab / "raw_plate_alpha.csv", lab / "processed_plate_alpha.csv")
    (lab / "stub.dat").write_bytes(b"")
    for stub in (share / "placeholder.dat", lab / "stub.dat"):
        os.utime(stub, (LONG_AGO, LONG_AGO))

    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    allow_pairs_in_one_folder(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project


def _defeat(defeaters_json: str) -> dict[str, bool]:
    return {item["rule_id"]: item["fired"] for item in json.loads(defeaters_json)}


def test_the_program_s_folder_is_raised_and_the_laboratory_s_is_not(inferred):
    with open_catalog(inferred.path) as catalog:
        raised = catalog.query(
            "SELECT e.relative_path, f.raised FROM findings f JOIN entries e ON e.entry_id = "
            "regexp_extract(f.subject, 'folder:(.*)', 1) "
            "WHERE f.rule_id = 'installed_software_in_folder'"
        ).fetchall()
    assert [(path, list(values)) for path, values in raised] == [("vendortool", ["layout folders"])]


def test_a_stage_claim_beneath_a_program_is_published_conflicting_and_one_outside_it_stands(
    inferred,
):
    with open_catalog(inferred.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.resolution_status, c.defeaters_json FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = 'pipeline_stage_from_name'"
        ).fetchall()
    by_name = {name: (status, _defeat(defeaters)) for name, status, defeaters in rows}
    assert by_name["raw_table_sample.csv"][0] == "examined_conflicting"
    assert by_name["raw_table_sample.csv"][1]["installed_software_in_folder"] is True
    assert by_name["raw_plate_alpha.csv"][0] == "resolved"
    assert by_name["raw_plate_alpha.csv"][1]["installed_software_in_folder"] is False


def test_a_derivation_beneath_a_program_loses_its_stages_and_one_outside_it_is_proposed(inferred):
    """The name identity a derivation is drawn within defers beneath the program, so no pair
    forms there (the pair's direction is its time, and no stage is read)."""
    with open_catalog(inferred.path) as catalog:
        rows = catalog.query(
            "SELECT s.name, c.decision_tier, c.defeaters_json FROM candidates c JOIN entries s "
            "ON s.entry_id = c.source_id WHERE c.rule_id = 'derivation_within_name_identity'"
        ).fetchall()
    by_source = {name: (tier, _defeat(defeaters)) for name, tier, defeaters in rows}
    assert "raw_table_sample.csv" not in by_source
    tier, defeat = by_source["raw_plate_alpha.csv"]
    assert tier == "circumstantial"
    assert defeat["installed_software_in_folder"] is False


def test_a_proposing_rule_s_own_defeater_blocks_a_pair_whose_stages_stand(tmp_path):
    """Read on both ends of the pair, and published blocked with the defeater, never dropped."""
    import copy

    from ingence.rulebook.load import load_revisions, revision_from
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook

    load_builtins()
    revision = copy.deepcopy(REVISION)
    for rule in revision["rules"]:  # type: ignore[attr-defined]
        if rule["id"] == "pipeline_stage_from_name":
            rule["defeated_by"] = ["program_from_extension"]
            rule["statement"] = rule["statement"].replace(
                " or lies in a folder laid out as an installed program", ""
            )
        # The pair lies in one folder, which the identity's derivation leaves as siblings.
        # This test is about the defeater, so it lets one folder pair.
        if rule["id"] == "derivation_within_name_identity":
            for spec in rule["parameters"]:
                if spec["name"] == "across_folders_only":
                    spec["default"] = False
    # The stage rule's program defeater is taken off so its stages stand beneath the program and
    # only the proposing rule's own defeater is left to block the pair. The revision's deferral
    # would put it back, and would defer the identity the pair is drawn within, so the copy
    # leaves both out of it. A test book: the book's revision 4 so changed, because it is
    # shipped and a project cannot change a shipped deferral.
    (deferral,) = revision["defer_beneath"]  # type: ignore[misc]
    drawn_within = next(
        rule["candidates_within"]
        for rule in revision["rules"]  # type: ignore[attr-defined]
        if rule["id"] == "derivation_within_name_identity"
    )
    deferral["except"] = [*deferral["except"], "pipeline_stage_from_name", drawn_within]
    book = effective_rulebook(
        [
            *(shipped for shipped in load_revisions() if shipped.number < 4),
            revision_from(revision, name="changed-revision-4"),
        ]
    )
    pool = tmp_path / "pool"
    tool = pool / "vendortool"
    for part in ("bin", "lib"):
        (tool / part).mkdir(parents=True)
    (tool / "raw_table_sample.csv").write_text("a,b\n1,2\n")
    (tool / "processed_table_sample.csv").write_text("a,b\n1,2\n")
    # Written in turn, so time orders the pair and the derivation rule, not the related one,
    # proposes it.
    _in_turn(tool / "raw_table_sample.csv", tool / "processed_table_sample.csv")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", rulebook=book
    )

    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.decision_tier, c.defeaters_json FROM candidates c "
            "WHERE c.rule_id = 'derivation_within_name_identity'"
        ).fetchall()
    ((tier, defeaters),) = rows
    defeat = _defeat(defeaters)
    assert tier == "blocked"
    assert defeat.pop("installed_software_in_folder") is True
    # The pair constraints stay recorded beside it, and none of them refused the pair.
    assert defeat and not any(defeat.values())


def test_an_unreadable_file_beneath_a_program_is_still_raised_and_marked(inferred):
    """Nothing is hidden: the finding stands, with the reason to look twice recorded.

    The rule is ``content_unreadable``, and the defeat is the same.
    """
    with open_catalog(inferred.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, f.defeaters_json FROM findings f "
            "JOIN entries e ON e.entry_id = f.subject WHERE f.rule_id = 'content_unreadable'"
        ).fetchall()
    assert {name: _defeat(defeaters) for name, defeaters in rows} == {
        "placeholder.dat": {"installed_software_in_folder": True},
        "stub.dat": {"installed_software_in_folder": False},
    }


def test_the_inventory_and_the_folder_view_say_what_was_set_aside_and_why(inferred):
    """Installed software is a scope, never an exclusion, and the person is shown why a region
    was set aside -- in the Inventory and in the folder they are looking at."""
    from ingence.api.routes import views

    with open_catalog(inferred.path) as catalog:
        aside = views.set_aside(catalog)
        folders = {
            str(row["relative_path"]): str(row["entry_id"])
            for row in views._rows(
                catalog, "SELECT entry_id, relative_path FROM entries WHERE entry_type='directory'"
            )
        }
        program = views.folder(catalog, folders["vendortool"])
        beneath = views.folder(catalog, folders["vendortool/share/examples"])
        laboratory = views.folder(catalog, folders["plates"])
        inventory = views.inventory(catalog)

    assert [item["path"] for item in aside["folders"]] == ["vendortool"]
    assert aside["outermost"] == ["vendortool"]
    assert aside["entries_beneath"] >= 6, "the program's own tree, counted once"
    assert inventory["set_aside"]["outermost"] == ["vendortool"]

    ((itself,),) = [(said,) for said in program["set_aside_by"]]
    assert itself["itself"] is True
    assert "installed_software_in_folder" in itself["rule_id"]
    ((above,),) = [(said,) for said in beneath["set_aside_by"]]
    assert (above["itself"], above["path"]) == (False, "vendortool")
    assert laboratory["set_aside_by"] == [], "a laboratory's folder is not set aside"


# ---------------------------------------------------------------------------
# A term tied only beneath a program is set aside, not asked.
# ---------------------------------------------------------------------------


@pytest.fixture
def tied(tmp_path):
    """``cluster`` stands between two senses, tied only beneath the program; ``n`` stands between
    two, tied beneath the program and in the laboratory's folder."""
    pool = tmp_path / "pool"
    tool = pool / "vendortool"
    for part in ("bin", "lib", "include"):
        (tool / part).mkdir(parents=True)
    (tool / "bin" / "vendortool").write_bytes(b"\x7fELF")
    examples = tool / "share" / "examples"
    examples.mkdir(parents=True)
    (examples / "vorn_cluster_zorbex.csv").write_text("a,b\n1,2\n")
    (examples / "vorn_n_zorbex.csv").write_text("a,b\n1,2\n")
    lab = pool / "plates"
    lab.mkdir()
    (lab / "kelt_n_plate.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    allow_pairs_in_one_folder(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project


def test_a_term_tied_only_beneath_a_program_is_set_aside_and_one_tied_outside_it_is_asked(tied):
    from ingence.api.routes.decide import current_questions
    from ingence.api.routes.review import review

    with open_catalog(tied.path) as catalog:
        page = review(catalog)
        answerable = {question["raised"] for question in current_questions(catalog)}
    asked = {question["raised"]: question for question in page["questions"]}
    aside = {question["raised"]: question for question in page["set_aside"]}

    assert set(aside) == {"cluster"}
    assert aside["cluster"]["rule_id"] == "binding_is_ambiguous_outside_programs_and_domains"
    assert aside["cluster"]["set_aside_by"] == ["installed_software_in_folder"]
    assert (aside["cluster"]["findings"], aside["cluster"]["findings_set_aside"]) == (1, 1)

    assert "n" in asked
    assert (asked["n"]["findings"], asked["n"]["findings_set_aside"]) == (2, 1)
    assert page["totals"]["set_aside"] == 1
    # Set aside is not hidden: the run still raises it, so it may still be answered.
    assert {"cluster", "n"} <= answerable


def test_a_finding_beneath_a_program_records_the_program_as_fired(tied):
    with open_catalog(tied.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, f.defeaters_json FROM findings f JOIN entries e "
            "ON e.entry_id = f.subject "
            "WHERE f.rule_id = 'binding_is_ambiguous_outside_programs_and_domains'"
        ).fetchall()
    assert {name: _defeat(defeaters) for name, defeaters in rows} == {
        "vorn_cluster_zorbex.csv": {"installed_software_in_folder": True},
        "vorn_n_zorbex.csv": {"installed_software_in_folder": True},
        "kelt_n_plate.csv": {"installed_software_in_folder": False},
    }


def test_a_file_s_earliest_credible_time_stands_beneath_a_program_as_outside_it(tmp_path):
    """A time is a physical fact, like a format: the program's scope retracts no time."""
    pool = tmp_path / "pool"
    tool = pool / "vendortool"
    for part in ("bin", "lib", "include"):
        (tool / part).mkdir(parents=True)
    (tool / "bin" / "vendortool").write_bytes(b"\x7fELF")
    (tool / "lib" / "libvendor.so").write_bytes(b"\x7fELF")
    (tool / "include" / "vendor.h").write_text("int f(void);\n")
    (pool / "plates").mkdir()
    (pool / "plates" / "raw_plate_alpha.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    allow_pairs_in_one_folder(project.path)
    # Stamped after the files were written, as a real run is (a later stamp is no older time).
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_census(project, run_id=f"run_{stamp}_111111111111", started_at="t")
    run_inference(project, run_id=f"run_{stamp}_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.resolution_status FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = 'earliest_credible_time'"
        ).fetchall()
    status = dict(rows)
    assert status["vendor.h"] == "resolved"
    assert status["raw_plate_alpha.csv"] == "resolved"
