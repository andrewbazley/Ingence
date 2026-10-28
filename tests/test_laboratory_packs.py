"""A pack a laboratory declares: installed into a project, loaded by name, recorded by a run.

Every name here is invented.
"""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.schema import DictionaryOrigin
from ingence.dictionary.laboratory_packs import (
    PackError,
    install_pack,
    installed_packs,
    remove_pack,
)
from ingence.dictionary.store import DictionaryStore
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog


def _pack(base, *, name="zorn-lab", version="1", surfaces=("zorbexin",), term="zorbexin_assay"):
    directory = base / name
    directory.mkdir(parents=True)
    (directory / "pack.yaml").write_text(
        yaml.safe_dump({"name": name, "version": version, "summary": "An invented laboratory."})
    )
    (directory / "assays.yaml").write_text(
        yaml.safe_dump(
            {
                "section": "zorn_assays",
                "summary": "Assays the invented laboratory runs.",
                "theme_kind": "technique",
                "entries": [{"term": term, "surfaces": list(surfaces)}],
            }
        )
    )
    return directory


def _project(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate_zorbexin_01.csv").write_text("a,b\n1,2\n")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool])


def test_an_installed_pack_is_loaded_by_name_at_the_pack_origin_and_a_run_names_it(tmp_path):
    project = _project(tmp_path)
    source = _pack(tmp_path / "packs")
    before = sorted(path.name for path in source.iterdir())
    pack = install_pack(project.path, source)
    assert Project.open(project.path).settings.dictionary.packs == ("zorn-lab",)
    assert sorted(path.name for path in source.iterdir()) == before

    store = DictionaryStore.for_project(project.path)
    sense = store.sense_for_term("zorbexin_assay")
    assert sense is not None and sense.origin is DictionaryOrigin.PACK
    assert [installed.name for installed in store.packs] == ["zorn-lab"]

    run_census(
        Project.open(project.path), run_id="run_20260101T000001Z_111111111111", started_at="t"
    )
    with open_catalog(project.path) as catalog:
        (packs,) = catalog.query("SELECT packs_json FROM run").fetchone()
        bound = catalog.query(
            "SELECT count(*) FROM dictionary_bindings WHERE term = 'zorbexin_assay'"
        ).fetchone()
    assert '"zorn-lab"' in packs and pack.digest in packs
    assert bound == (1,)


def test_a_spelling_a_pack_and_the_core_both_claim_ties_and_is_reported(tmp_path):
    """Level with the core: never a silent win for either."""
    project = _project(tmp_path)
    install_pack(project.path, _pack(tmp_path / "packs", surfaces=("tif",), term="zorn_tif"))
    reported = [
        item
        for item in DictionaryStore.for_project(project.path).collisions()
        if any(sense.term == "zorn_tif" for sense in (item.winner, *item.tied, *item.shadowed))
    ]
    assert reported
    assert all(item.reported for item in reported)


def test_a_pack_is_reinstalled_as_nothing_and_a_changed_one_is_refused_until_removed(tmp_path):
    project = _project(tmp_path)
    first = _pack(tmp_path / "one")
    install_pack(project.path, first)
    install_pack(project.path, first)
    assert len(installed_packs(project.path)) == 1
    changed = _pack(tmp_path / "two", version="2", surfaces=("zorbexin", "zorbex"))
    with pytest.raises(PackError, match="already installed"):
        install_pack(project.path, changed)
    remove_pack(project.path, "zorn-lab")
    assert installed_packs(project.path) == ()
    assert Project.open(project.path).settings.dictionary.packs == ()
    assert (first / "pack.yaml").is_file()
    assert install_pack(project.path, changed).version == "2"


def test_a_pack_the_settings_name_and_the_project_lacks_is_refused_by_name(tmp_path):
    """A run reading less vocabulary than its settings promise would answer differently."""
    project = _project(tmp_path)
    settings = project.settings
    project.save_settings(
        settings.model_copy(
            update={"dictionary": settings.dictionary.model_copy(update={"packs": ("absent",)})}
        )
    )
    with pytest.raises(PackError, match="absent"):
        DictionaryStore.for_project(project.path)


def test_a_directory_without_a_manifest_or_a_section_is_not_a_pack(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PackError, match="not a pack"):
        install_pack(_project(tmp_path).path, empty)
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "pack.yaml").write_text(yaml.safe_dump({"name": "bare", "version": "1"}))
    with pytest.raises(PackError, match="no section file"):
        install_pack(Project.open(tmp_path / "project").path, bare)


def test_the_teach_pack_commands_install_list_and_remove(tmp_path):
    project = _project(tmp_path)
    source = _pack(tmp_path / "packs")
    runner = CliRunner()
    at = ["-p", str(project.path)]
    installed = runner.invoke(app, ["teach", "pack", "install", str(source), *at])
    assert installed.exit_code == 0, installed.output
    listed = runner.invoke(app, ["teach", "pack", "list", *at])
    assert "zorn-lab" in listed.output and "loaded" in listed.output
    removed = runner.invoke(app, ["teach", "pack", "remove", "zorn-lab", *at])
    assert removed.exit_code == 0
    assert "No pack" in runner.invoke(app, ["teach", "pack", "list", *at]).output


def test_removing_a_pack_that_was_never_installed_says_so_and_changes_nothing(tmp_path):
    """Removing a name the project does not hold never prints "Removed ..." or exits 0. The command
    says the name is not installed, exits nonzero, and leaves the settings and the packs as they
    were; a name spelled as a path into a folder outside the project deletes nothing there."""
    import os

    from ingence.dictionary.laboratory_packs import packs_directory

    project = _project(tmp_path)
    source = _pack(tmp_path / "packs")
    runner = CliRunner()
    at = ["-p", str(project.path)]
    assert runner.invoke(app, ["teach", "pack", "install", str(source), *at]).exit_code == 0
    settings_before = (project.path / "settings.yaml").read_bytes()
    kept = sorted(path.name for path in packs_directory(project.path).rglob("*"))

    towards_the_source = os.path.relpath(source, packs_directory(project.path))
    for name in ("vorn-lab", "zorn", "ZORN-LAB", "", ".", towards_the_source):
        done = runner.invoke(app, ["teach", "pack", "remove", name, *at])
        assert done.exit_code != 0, (name, done.output)
        assert "Removed" not in done.output, name
        if name:
            assert "nothing was changed" in done.output
        if name in ("vorn-lab", "zorn"):  # a name an installed pack could have, and none is
            assert f"no pack named {name!r} is installed in this project" in done.output
        elif name:  # a name that breaks the install rule
            assert f"{name!r} is not a pack's name" in done.output
    assert (project.path / "settings.yaml").read_bytes() == settings_before
    assert sorted(path.name for path in packs_directory(project.path).rglob("*")) == kept
    assert [pack.name for pack in installed_packs(project.path)] == ["zorn-lab"]
    assert (source / "pack.yaml").is_file(), "a pack's source is never touched"


def test_a_pack_the_settings_name_with_no_copy_installed_can_still_be_removed(tmp_path):
    """The repair for a project whose settings name a pack it lacks (refused by name at a run)."""
    project = _project(tmp_path)
    settings = project.settings
    project.save_settings(
        settings.model_copy(
            update={"dictionary": settings.dictionary.model_copy(update={"packs": ("absent",)})}
        )
    )
    done = CliRunner().invoke(app, ["teach", "pack", "remove", "absent", "-p", str(project.path)])
    assert done.exit_code == 0, done.output
    assert "Removed absent" in done.output
    assert Project.open(project.path).settings.dictionary.packs == ()


def _pack_shaped_folder(base):
    folder = base / "vorn-pack"
    folder.mkdir(parents=True)
    (folder / "pack.yaml").write_text(
        yaml.safe_dump({"name": "vorn-pack", "version": "1", "summary": "An invented pack."})
    )
    (folder / "words.yaml").write_text(yaml.safe_dump({"section": "vorn_words", "entries": []}))
    return folder


def test_a_settings_entry_that_is_a_path_names_no_folder_to_delete(tmp_path):
    """With ``dictionary.packs`` edited by hand to a path, ``teach pack remove`` followed it with
    the packs folder present and deleted a pack-shaped folder inside the data. A name that breaks
    the install rule is refused, in the command and in ``remove_pack``, and nothing is touched."""
    import os

    from ingence.dictionary.laboratory_packs import packs_directory

    project = _project(tmp_path)
    install_pack(project.path, _pack(tmp_path / "packs"))  # the packs folder exists
    pool = tmp_path / "pool"
    victim = _pack_shaped_folder(pool)
    name = os.path.relpath(victim, packs_directory(project.path))
    assert name.startswith("..")
    settings = project.settings
    project.save_settings(
        settings.model_copy(
            update={
                "dictionary": settings.dictionary.model_copy(
                    update={"packs": (*settings.dictionary.packs, name)}
                )
            }
        )
    )
    settings_before = (project.path / "settings.yaml").read_bytes()
    done = CliRunner().invoke(app, ["teach", "pack", "remove", name, "-p", str(project.path)])
    assert done.exit_code != 0, done.output
    assert "Removed" not in done.output and "is not a pack's name" in done.output
    with pytest.raises(PackError, match="is not a pack's name"):
        remove_pack(project.path, name)
    assert (victim / "pack.yaml").is_file() and (victim / "words.yaml").is_file()
    assert (project.path / "settings.yaml").read_bytes() == settings_before


def test_a_copy_that_is_a_link_is_not_followed_when_a_pack_is_removed(tmp_path):
    from ingence.dictionary.laboratory_packs import packs_directory

    project = _project(tmp_path)
    victim = _pack_shaped_folder(tmp_path / "pool")
    packs = packs_directory(project.path)
    packs.mkdir(parents=True)
    link = packs / "vorn-pack"
    try:
        link.symlink_to(victim, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")
    settings_before = (project.path / "settings.yaml").read_bytes()
    with pytest.raises(PackError, match="is a link"):
        remove_pack(project.path, "vorn-pack")
    done = CliRunner().invoke(
        app, ["teach", "pack", "remove", "vorn-pack", "-p", str(project.path)]
    )
    assert done.exit_code != 0 and "Removed" not in done.output, done.output
    assert "is a link" in done.output
    assert link.is_symlink()
    assert (victim / "pack.yaml").is_file() and (victim / "words.yaml").is_file()
    assert (project.path / "settings.yaml").read_bytes() == settings_before


def test_a_packs_folder_that_is_a_link_out_of_the_project_is_not_used(tmp_path):
    """With ``dictionary/packs`` itself a link into the data, every pack-shaped folder there looked
    installed, ``teach pack remove`` deleted one, and an install would have copied into the data."""
    from ingence.api.routes import dictionary as dictionary_routes
    from ingence.dictionary.laboratory_packs import packs_directory

    project = _project(tmp_path)
    pool = tmp_path / "pool"
    victim = _pack_shaped_folder(pool)
    source = _pack(tmp_path / "sources")
    packs = packs_directory(project.path)
    packs.parent.mkdir(parents=True, exist_ok=True)
    try:
        packs.symlink_to(pool, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")
    before = sorted(path.relative_to(pool).as_posix() for path in pool.rglob("*"))
    settings_before = (project.path / "settings.yaml").read_bytes()
    at = ["-p", str(project.path)]
    runner = CliRunner()

    removed = runner.invoke(app, ["teach", "pack", "remove", "vorn-pack", *at])
    assert removed.exit_code != 0 and "Removed" not in removed.output, removed.output
    assert "through a link" in removed.output and "nothing was changed" in removed.output
    installed = runner.invoke(app, ["teach", "pack", "install", str(source), *at])
    assert installed.exit_code != 0 and "Installed" not in installed.output, installed.output
    with pytest.raises(PackError, match="through a link"):
        remove_pack(project.path, "vorn-pack")
    with pytest.raises(PackError, match="through a link"):
        install_pack(project.path, source)
    with pytest.raises(dictionary_routes.DictionaryRefusedError, match="through a link"):
        dictionary_routes.remove(project.path, "vorn-pack", actor="a person")

    assert (victim / "pack.yaml").is_file() and (victim / "words.yaml").is_file()
    assert sorted(path.relative_to(pool).as_posix() for path in pool.rglob("*")) == before
    assert (project.path / "settings.yaml").read_bytes() == settings_before
    assert not (project.path / "dictionary" / "removed-packs").exists()


def test_a_pack_name_ends_where_it_ends_a_trailing_newline_is_not_part_of_one(tmp_path):
    """The name rule used a pattern whose ``$`` also matches before a final newline, so
    ``"zorn-lab\\n"`` passed as a name (to a command, or in a manifest)."""
    from ingence.dictionary.laboratory_packs import is_pack_name, read_pack

    assert is_pack_name("zorn-lab") and is_pack_name("a") and is_pack_name("zorn_lab-2")
    for bad in ("zorn-lab\n", "zorn-lab\r\n", "\nzorn-lab", "zorn-lab ", "", "Zorn", "a/b"):
        assert not is_pack_name(bad), repr(bad)
    folder = _pack(tmp_path / "packs", name="zorn-lab")
    (folder / "pack.yaml").write_text(
        yaml.safe_dump({"name": "zorn-lab\n", "version": "1", "summary": "An invented one."})
    )
    with pytest.raises(PackError, match="lower-case letters"):
        read_pack(folder)
    project = _project(tmp_path)
    done = CliRunner().invoke(
        app, ["teach", "pack", "remove", "zorn-lab\n", "-p", str(project.path)]
    )
    assert done.exit_code != 0 and "is not a pack's name" in done.output, done.output


def test_a_bad_name_only_the_settings_hold_is_answered_by_where_to_edit(tmp_path):
    """The command cannot clear a settings entry that is not a name; it says which file to edit."""
    project = _project(tmp_path)
    settings = project.settings
    for name in ("../../elsewhere/vorn-pack", "zorn-lab\n"):
        project.save_settings(
            settings.model_copy(
                update={"dictionary": settings.dictionary.model_copy(update={"packs": (name,)})}
            )
        )
        before = (project.path / "settings.yaml").read_bytes()
        done = CliRunner().invoke(app, ["teach", "pack", "remove", name, "-p", str(project.path)])
        said = " ".join(done.output.split())
        assert done.exit_code != 0, done.output
        assert f"{project.path / 'settings.yaml'} names" in said
        assert "Edit settings.yaml and take it out of the list under dictionary: packs" in said
        assert "nothing was changed" in said
        assert (project.path / "settings.yaml").read_bytes() == before
