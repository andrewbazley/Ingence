"""Programs, bundles and scripts.

An installed Fiji is a copy of a program, and its files must not be read as data. If a program's
folders were found only by their share of program files, its licences, lookup tables and launcher
would be read as data. Seven of revision 4's rules name no installed-program defeater of their
own. The classes built here:

- a program is recognised from its top folder: by a name packaging gives it (``*.app``), or by
  the parts a known program always carries side by side (Fiji's ``jars``, ``plugins`` and ``luts``);
- every rule defers beneath it, flags included, through one declaration in the revision rather
  than an edit per rule, except the rules that state a physical fact;
- a script or macro is a program to the rules that ask, because the Dictionary says which formats
  are programs and the rule reads every one of them.

Every name here is invented. The layout names are the packaging conventions themselves.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import REVISION, write_revision
from ingence.api.routes.review import review
from ingence.core.project import Project
from ingence.dictionary.store import DictionaryStore
from ingence.infer.run import run_inference
from ingence.rulebook.load import RuleDeclarationError, load_project_revisions, load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.resolve import effective_rulebook
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import RULEBOOK_DIR

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"
SCOPE_RULE = "installed_software_in_folder"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pool(base: Path) -> Path:
    """An installed Fiji beside a laboratory's own experiment folders."""
    pool = base / "pool"
    app = pool / "Fiji" / "fiji-linux64" / "Fiji.app"
    _write(app / "ImageJ-linux64", "launcher")
    _write(app / "README.md", "# Fiji")
    for number in range(12):
        _write(app / "jars" / f"vorn-library-{number}.jar")
    _write(app / "jars" / "n5-kelt-3.1.jar")
    _write(app / "jars" / "ij-1.54f.jar")
    for number in range(6):
        _write(app / "plugins" / f"brisa_plugin_{number}.jar")
    _write(app / "luts" / "fire.lut")
    _write(app / "luts" / "ice.lut")
    _write(app / "licenses" / "kelt-annotations.txt", "Apache License")
    _write(app / "licenses" / "vorn-annotations.txt", "Apache License")
    _write(app / "scripts" / "Examples" / "demo_movie.bsh")
    jre = app / "java" / "linux64" / "jdk1.8.0_172" / "jre"
    _write(jre / "release", "JAVA_VERSION")
    _write(jre / "bin" / "java", "binary")
    _write(jre / "lib" / "rt.jar")
    # A laboratory's own experiment, a macro it wrote, and a folder of its scripts and macros.
    data = pool / "kelt_experiments" / "20250611 vorn tamen quolt"
    _write(data / "vorn tamen 01.tif")
    _write(data / "vorn quolt 01.tif")
    _write(data / "split_twin_movies.ijm", "run('Duplicate...');")
    tools = pool / "kelt_experiments" / "analysis tools"
    _write(tools / "scripts" / "fit_vorn.py", "print(1)")
    _write(tools / "macros" / "measure_vorn.ijm", "run('Measure');")
    # A folder whose own name marks an application bundle, with no known program's parts.
    _write(pool / "Brisa Viewer.app" / "Contents" / "Info.plist", "<plist/>")
    return pool


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("programs")
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    write_revision(created.path)
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFERENCE, started_at="t")
    return created


def _rows(project: Project, sql: str, parameters: list[object] | None = None) -> list[tuple]:
    with open_catalog(project.path) as catalog:
        return list(catalog.query(sql, parameters or []).fetchall())


def _path_of(project: Project, subject: str) -> str:
    entry = subject.split(":", 1)[-1]
    (row,) = _rows(project, "SELECT relative_path FROM entries WHERE entry_id = ?", [entry])
    return str(row[0])


def _raised_folders(project: Project) -> dict[str, list[str]]:
    return {
        _path_of(project, subject): list(raised)
        for subject, raised in _rows(
            project, "SELECT subject, raised FROM findings WHERE rule_id = ?", [SCOPE_RULE]
        )
    }


def test_a_program_is_recognised_from_its_top_folder(project: Project) -> None:
    """``Fiji.app`` itself is raised, as Fiji, and not only the parts a share of files gives."""
    raised = _raised_folders(project)
    assert raised["Fiji/fiji-linux64/Fiji.app"] == ["Fiji"]
    # The bundled runtime is a program of its own, recognised by its parts and nested inside.
    assert raised["Fiji/fiji-linux64/Fiji.app/java/linux64/jdk1.8.0_172/jre"] == ["Java runtime"]
    # A folder named as an application bundle, with no known program's parts.
    assert raised["Brisa Viewer.app"] == ["application bundle"]


def test_a_laboratory_s_scripts_and_macros_folders_are_not_a_program(project: Project) -> None:
    """Code a person wrote is not an installed program: no signature, no layout, no share."""
    raised = _raised_folders(project)
    assert not [path for path in raised if path.startswith("kelt_experiments")]


def _claims(project: Project, name: str, rule_id: str) -> list[tuple[str, str]]:
    return [
        (str(value), str(status))
        for value, status in _rows(
            project,
            "SELECT c.object_reference, c.resolution_status FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject WHERE e.name = ? AND c.rule_id = ?",
            [name, rule_id],
        )
    ]


def test_everything_beneath_the_program_is_the_program_s_licences_included(
    project: Project,
) -> None:
    """A licence file's role, a lookup table's and the launcher's stand deferred, not resolved."""
    for name in ("kelt-annotations.txt", "fire.lut", "ImageJ-linux64", "demo_movie.bsh"):
        for _, status in _claims(project, name, "file_role_unless_program"):
            assert status == "examined_conflicting", name
    # A physical fact is exempt: the jar is still Java bytecode, resolved.
    assert _claims(project, "n5-kelt-3.1.jar", "format_from_extension_unless_contradicted") == [
        ("java_bytecode", "resolved")
    ]


def test_every_rule_defers_beneath_a_program_flags_included(project: Project) -> None:
    """The seven rules that named no program defeater carry it, by one declaration."""
    load_builtins()
    book = effective_rulebook(
        load_revisions(), project=load_project_revisions(project.path / RULEBOOK_DIR)
    )
    # The seventh, filesystem_noise_from_spelling, is exempt: a tool's artefact is one anywhere.
    seven = (
        "stated_count_may_mark_replicates",
        "name_family_without_stages",
        "same_name_identity",
        "doubt_in_a_folder_name",
        "doubt_to_confirm_in_a_folder_name",
        "indexed_label_may_mark_replicates",
    )
    for rule_id in seven:
        rule = book.by_id(rule_id)
        assert rule is not None
        assert SCOPE_RULE in rule.defeated_by, rule_id
        assert rule.version.endswith("+d1"), rule.version
    for rule_id in ("filesystem_noise_from_spelling", "program_from_extension", SCOPE_RULE):
        rule = book.by_id(rule_id)
        assert rule is not None
        assert SCOPE_RULE not in rule.defeated_by, rule_id


def test_a_deferral_never_makes_a_rule_publish_what_it_did_not(project: Project) -> None:
    """A rule naming no defeater publishes nothing, and the deferral leaves it so.

    ``later_stage_from_name`` serves only as another rule's defeater. Given the scope as its first
    defeater it would publish stages, and the proposing rule would draw pairs between scripts.
    """
    load_builtins()
    book = effective_rulebook(
        load_revisions(), project=load_project_revisions(project.path / RULEBOOK_DIR)
    )
    rule = book.by_id("later_stage_from_name")
    assert rule is not None
    assert rule.defeated_by == ()
    assert not _rows(project, "SELECT 1 FROM claims WHERE rule_id = 'later_stage_from_name'")


def test_a_count_raised_inside_a_program_is_marked_and_set_aside(project: Project) -> None:
    """``n5`` in a library's name is no replicate count: raised, marked, and not asked about."""
    found = _rows(
        project,
        "SELECT f.raised, f.defeaters_json FROM findings f JOIN entries e "
        "ON e.entry_id = f.subject WHERE f.rule_id = 'stated_count_may_mark_replicates' "
        "AND e.name = 'n5-kelt-3.1.jar'",
    )
    for _, defeaters in found:
        assert any(
            item["rule_id"] == SCOPE_RULE and item["fired"] for item in json.loads(defeaters)
        )
    with open_catalog(project.path) as catalog:
        page = review(catalog, limit=1000, sample=0)
    asked = {(item["rule_id"], item["raised"]) for item in page["questions"]}
    assert not any(rule == "stated_count_may_mark_replicates" for rule, _ in asked)


def test_a_macro_is_a_program_and_never_the_movie_its_name_mentions(project: Project) -> None:
    """``split_twin_movies.ijm`` is a macro, so its name's ``movies`` is no role of its own."""
    assert _claims(project, "split_twin_movies.ijm", "program_from_extension") == [
        ("imagej_macro", "resolved")
    ]
    for value, status in _claims(project, "split_twin_movies.ijm", "file_role_unless_program"):
        assert (value, status) == ("movie", "examined_conflicting")


def test_every_format_the_dictionary_calls_a_program_is_one_the_program_rule_reads() -> None:
    """The class, held by construction: a script format added later is read without an edit."""
    store = DictionaryStore.builtin()
    declared = {sense.term for sense in store.senses() if sense.properties.get("program") is True}
    assert {"imagej_macro", "beanshell_script", "clojure_source", "ruby_source"} <= declared
    (rule,) = [item for item in REVISION["rules"] if item["id"] == "program_from_extension"]
    (senses,) = [item for item in rule["parameters"] if item["name"] == "senses"]
    assert declared <= set(senses["default"])


def test_a_macro_s_spelling_is_the_core_s_and_not_a_pack_s() -> None:
    """``.ijm`` is a known, frozen format: it binds with no domain pack loaded."""
    store = DictionaryStore.builtin()
    sense = store.sense_for_term("imagej_macro")
    assert sense is not None
    assert sense.section == "formats"


@pytest.mark.parametrize(
    ("deferral", "refused_for"),
    [
        ({"scope": SCOPE_RULE, "except": ["no_such_rule"], "rationale": "why"}, "no_such_rule"),
        ({"scope": SCOPE_RULE, "except": []}, "says why"),
        ({"scope": "program_from_extension", "rationale": "why"}, "raises a folder"),
    ],
)
def test_a_deferral_that_cannot_mean_what_it_says_is_refused(
    tmp_path: Path, deferral: dict[str, object], refused_for: str
) -> None:
    """A typo would exempt nothing or everything. A reach over every rule must say why."""
    import yaml

    directory = tmp_path / RULEBOOK_DIR
    directory.mkdir()
    # A project's own deferral (the shipped book carries its own).
    document = {"project_revision": 1, "summary": "ines: a deferral", "defer_beneath": [deferral]}
    (directory / "01.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    load_builtins()
    with pytest.raises(RuleDeclarationError, match=refused_for):
        effective_rulebook(load_revisions(), project=load_project_revisions(directory))
