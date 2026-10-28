"""Programs and packages by their structure.

A laboratory's own package may have a name that the shipped dictionary does not hold, such as a
folder of Python scripts beside a tar.gz archive of the same package. That is not a missing
signature. The engine recognises the package by its file structure and file types. The classes
built here:

- the layouts are vocabulary: the core's packaging conventions (a Python, R, MATLAB or Java
  package, an environment) and a pack of named scientific suites a person may switch off, each a
  structure and never a bare name;
- a laboratory's own package is recognised without its name being known, by its packaging, or by
  the archive of its own name it was shared as where the folder is mostly its code;
- a folder of scripts with no packaging is not a program, however many scripts it holds, and an
  experiment zipped beside itself is not a package for the one script among its images.

Every name here is invented. The layout names are the packaging conventions themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import REVISION, write_revision
from ingence.core.ids import Scope, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType
from ingence.dictionary.programs import (
    FolderView,
    ProgramLayout,
    layouts_from,
    program_layouts,
)
from ingence.dictionary.store import DictionaryStore, Section
from ingence.infer.run import run_inference
from ingence.infer.subjects import EntryRecords, ScopeRecords
from ingence.rulebook.predicates.flag import InstalledSoftwareInScope
from ingence.rulebook.registry import Raised
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

RULE = next(rule for rule in REVISION["rules"] if rule["id"] == "installed_software_in_folder")  # type: ignore[index]
DEFAULTS = {parameter["name"]: parameter["default"] for parameter in RULE["parameters"]}
LAYOUTS = program_layouts(DictionaryStore.builtin().sections)


def _entry(name: str, kind: EntryType, index: int, parent: str) -> Entry:
    return Entry(
        entry_id=f"entry_{parent}_{index}",
        root_id="root_x",
        relative_path=f"{parent}/{name}",
        name=name,
        entry_type=kind,
        extension=name.rsplit(".", 1)[-1] if "." in name and kind is EntryType.FILE else "",
        parent_entry_id=f"entry_{parent}",
        first_seen_run="r",
        last_seen_run="r",
    )


def _children(spelled: list[str], parent: str = "tool") -> tuple[EntryRecords, ...]:
    """Children from their names: a trailing ``/`` is a folder."""
    return tuple(
        EntryRecords(
            entry=_entry(
                name.rstrip("/"),
                EntryType.DIRECTORY if name.endswith("/") else EntryType.FILE,
                index,
                parent,
            )
        )
        for index, name in enumerate(spelled)
    )


def _scope(
    own_name: str,
    spelled: list[str],
    *,
    beside: list[str] = (),  # type: ignore[assignment]
    layouts: tuple[ProgramLayout, ...] = LAYOUTS,
) -> ScopeRecords:
    holder = EntryRecords(
        entry=Entry(
            entry_id="entry_tool",
            root_id="root_x",
            relative_path=own_name,
            name=own_name,
            entry_type=EntryType.DIRECTORY,
            parent_entry_id="entry_up",
            first_seen_run="r",
            last_seen_run="r",
        )
    )
    return ScopeRecords(
        scope=Scope(ScopeKind.FOLDER, "entry_tool"),
        entries=_children(spelled),
        holder=holder,
        siblings=(holder, *_children(list(beside), parent="up")),
        layouts=layouts,
    )


def _raised(scope: ScopeRecords) -> tuple[str, ...] | None:
    verdict = InstalledSoftwareInScope().evaluate(scope, DEFAULTS)
    return verdict.values if isinstance(verdict, Raised) else None


# ---------------------------------------------------------------------------
# The layouts are vocabulary, and structural.
# ---------------------------------------------------------------------------


def test_every_shipped_program_is_a_structure_of_at_least_two_parts_and_never_a_name() -> None:
    programs = [layout for layout in LAYOUTS if layout.names_a_program]
    assert {"Fiji", "ImageJ", "MATLAB", "R", "RStudio", "Python"} <= {
        layout.name for layout in programs
    }
    assert all(len(layout.parts) >= 2 for layout in programs)
    assert all(layout.section == "programs" for layout in programs)
    packages = {layout.name for layout in LAYOUTS if not layout.names_a_program}
    assert {"Python package", "R package", "MATLAB toolbox", "MATLAB class"} <= packages


def test_a_program_declared_by_one_name_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 2 parts"):
        layouts_from([{"program": "Kelt", "holds": ["kelt.exe"]}], section="kelt")
    with pytest.raises(ValueError, match="one program or one package"):
        layouts_from([{"holds": ["a/", "b/"]}], section="kelt")
    # A package's own packaging file may stand alone: nothing but a virtual environment writes it.
    (single,) = layouts_from([{"package": "Kelt env", "holds": ["kelt.cfg"]}], section="kelt")
    assert single.parts


def test_the_programs_pack_switches_off_and_the_packaging_conventions_stay(tmp_path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.record.targets import pack_target

    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    # A veto on the programs domain: its words and its named programs go.
    append_event(
        project.path,
        kind=RecordEventKind.REJECT,
        actor="kelt",
        occurred_at="2026-01-01T00:00:00Z",
        target=pack_target("programs"),
        against_run_id="",
        payload={"verdict": "refuse"},
    )
    loaded = program_layouts(DictionaryStore.for_project(project.path).sections)
    assert not [layout for layout in loaded if layout.names_a_program]
    assert {layout.name for layout in loaded} >= {"Python package", "R package", "Java runtime"}
    # Fiji's own folder, with the pack off: the names go and the structure still raises it.
    fiji = ["jars/", "plugins/", "luts/", "ImageJ-linux64"]
    assert _raised(_scope("Fiji.app", fiji)) == ("Fiji",)
    assert _raised(_scope("Fiji.app", fiji, layouts=loaded)) == ("application bundle",)


def test_a_layout_is_part_of_the_vocabulary_s_fingerprint() -> None:
    store = DictionaryStore.builtin()
    without = DictionaryStore(
        Section(
            name=section.name,
            summary=section.summary,
            senses=section.senses,
            domain=section.domain,
            origin=section.origin,
        )
        for section in store.sections
    )
    assert store.fingerprint() != without.fingerprint()
    assert program_layouts(without.sections) == ()


# ---------------------------------------------------------------------------
# Matching: each part a different child, a folder a folder, the own name where it lives.
# ---------------------------------------------------------------------------


def test_each_part_is_met_by_a_different_child_and_a_folder_part_only_by_a_folder() -> None:
    (package,) = layouts_from(
        [{"package": "Kelt", "holds": [["setup.py", "pyproject.toml"], ["*.py", "src/"]]}],
        section="kelt",
    )
    alone = FolderView.of("kelt", [child.entry for child in _children(["setup.py", "data/"])])
    assert package.met_in(alone) is None, "setup.py is not also the module beside itself"
    met = package.met_in(
        FolderView.of("kelt", [child.entry for child in _children(["setup.py", "fit_vorn.py"])])
    )
    assert met is not None
    assert sorted(entry.name for entry in met) == ["fit_vorn.py", "setup.py"]
    (fiji,) = [layout for layout in LAYOUTS if layout.name == "Fiji"]
    as_files = FolderView.of("x", [child.entry for child in _children(["jars", "plugins", "luts"])])
    assert fiji.met_in(as_files) is None, "a file called jars is not Fiji's folder of jars"


def test_a_matlab_class_is_read_from_the_folder_s_own_name() -> None:
    assert _raised(_scope("@Kelt", ["Kelt.m", "fitVorn.m"])) == ("MATLAB class",)
    assert _raised(_scope("Kelt", ["Kelt.m", "fitVorn.m"])) is None
    assert _raised(_scope("vorn tools", ["@Kelt/", "plotVorn.m", "README.md"])) == (
        "MATLAB toolbox",
    )


# ---------------------------------------------------------------------------
# A laboratory's own package, by its packaging.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("children", "kind"),
    [
        (["setup.py", "analyse_vorn.py", "data/"], "Python package"),
        (["pyproject.toml", "src/", "tests/", "README.md"], "Python package"),
        (["requirements.txt", "predict_kelt.py", "README"], "Python package"),
        (["__init__.py", "zib.py"], "Python package"),
        (["DESCRIPTION", "NAMESPACE", "R/", "man/"], "R package"),
        (["Contents.m", "quolt.m"], "MATLAB toolbox"),
        (["pom.xml", "src/", "LICENCE.txt"], "Java package"),
        (["conda-meta/", "bin/", "lib/"], "conda environment"),
        (["pyvenv.cfg", "bin/", "lib/"], "Python environment"),
        (["LICENSE", "README.md", "track_zib.py", "notebooks/"], "published code"),
    ],
)
def test_a_package_is_recognised_by_its_packaging_whatever_it_is_called(
    children: list[str], kind: str
) -> None:
    assert _raised(_scope("kelt_analysis", children)) == (kind,)


def test_a_packaging_file_with_no_code_beside_it_raises_nothing() -> None:
    assert _raised(_scope("plates", ["setup.py", "plate_01.csv", "plate_02.csv"])) is None
    assert _raised(_scope("plates", ["requirements.txt", "plate_01.csv"])) is None
    # A dataset or a design published under a licence holds no code at its top.
    published = ["LICENSE", "README.txt", "images/", "kelt_plate.stl"]
    assert _raised(_scope("kelt plate design", published)) is None


def test_a_folder_of_scripts_with_no_packaging_is_not_a_program() -> None:
    """Its folders can hold a laboratory's experiments, and all beneath would be deferred."""
    scripts = ["fit_kelt.r", "plot_kelt.r", "norm_kelt.r", "Experiments/", "Scripts/"]
    assert _raised(_scope("R", scripts)) is None
    assert _raised(_scope("vorn scripts", [f"step_{n}.m" for n in range(20)])) is None


# ---------------------------------------------------------------------------
# A folder of code shared as an archive of its own name.
# ---------------------------------------------------------------------------


def test_a_folder_of_code_beside_an_archive_of_its_own_name_is_that_kind_of_package() -> None:
    """A versioned source archive beside a folder of Python modules."""
    modules = ["track_zib.py", "fit_zib.py", "plot_zib.py", "notes.txt"]
    assert _raised(_scope("zib-analysis-0.3", modules, beside=["zib-analysis-0.3.tar.gz"])) == (
        "Python package",
    )
    assert _raised(_scope("zib-analysis", modules, beside=["zib-analysis-0.3.tar.gz"])) == (
        "Python package",
    )
    matlab = [f"vorn_{n}.m" for n in range(6)] + ["Example data/"]
    assert _raised(_scope("Vorn_analysis", matlab, beside=["Vorn_analysis.zip"])) == (
        "MATLAB toolbox",
    )
    # No archive of its name beside it: a folder of scripts, as above.
    assert _raised(_scope("zib-analysis", modules, beside=["other.tar.gz"])) is None


def test_an_experiment_zipped_beside_itself_is_not_a_package_for_one_script() -> None:
    images = [f"kelt_{n:03d}.tif" for n in range(40)] + ["measure_kelt.m"]
    assert _raised(_scope("20000102 kelt", images, beside=["20000102 kelt.zip"])) is None
    tables = [f"vorn_{n}.csv" for n in range(12)]
    assert _raised(_scope("vorn results", tables, beside=["vorn results.zip"])) is None


# ---------------------------------------------------------------------------
# On a pool: the pass hands the layouts and what lies beside each folder.
# ---------------------------------------------------------------------------


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture(scope="module")
def inferred(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("layouts")
    pool = base / "pool"
    shared = pool / "kelt_lab" / "software"
    _write(shared / "zib-analysis-0.3" / "track_zib.py", "print(1)")
    _write(shared / "zib-analysis-0.3" / "fit_zib.py", "print(2)")
    _write(shared / "zib-analysis-0.3" / "raw_zib_example.csv", "a,b\n1,2\n")
    _write(shared / "zib-analysis-0.3.tar.gz", "archive")
    klass = pool / "kelt_lab" / "matlab" / "@Quolt"
    _write(klass / "Quolt.m", "classdef Quolt")
    _write(klass / "fitQuolt.m", "function fitQuolt")
    experiment = pool / "kelt_lab" / "20000102 vorn"
    _write(experiment / "raw_vorn_01.csv", "a,b\n1,2\n")
    _write(experiment / "processed_vorn_01.csv", "a,b\n1,2\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project


def test_the_pass_hands_the_layouts_and_the_neighbours(inferred: Project) -> None:
    with open_catalog(inferred.path) as catalog:
        raised = catalog.query(
            "SELECT e.relative_path, f.raised FROM findings f JOIN entries e ON e.entry_id = "
            "regexp_extract(f.subject, 'folder:(.*)', 1) "
            "WHERE f.rule_id = 'installed_software_in_folder' ORDER BY 1"
        ).fetchall()
    assert [(path, list(values)) for path, values in raised] == [
        ("kelt_lab/matlab/@Quolt", ["MATLAB class"]),
        ("kelt_lab/software/zib-analysis-0.3", ["Python package"]),
    ]


def test_a_stage_beneath_a_laboratory_s_package_defers_and_one_in_its_experiment_stands(
    inferred: Project,
) -> None:
    with open_catalog(inferred.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.resolution_status FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = 'pipeline_stage_from_name'"
        ).fetchall()
    by_name = dict(rows)
    assert by_name["raw_zib_example.csv"] == "examined_conflicting"
    assert by_name["raw_vorn_01.csv"] == "resolved"


def test_a_program_a_project_declares_in_version_2_s_form_is_still_read() -> None:
    """A revision still declaring ``program_signatures`` loads and names its program."""
    declared = {**DEFAULTS, "program_signatures": {"Kelt": "kelt.jar, plugins"}}
    scope = _scope("kelt", ["kelt.jar", "plugins/", "notes.txt"], layouts=())
    verdict = InstalledSoftwareInScope().evaluate(scope, declared)
    assert isinstance(verdict, Raised)
    assert verdict.values == ("Kelt",)
