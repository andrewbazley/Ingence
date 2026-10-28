"""A project a newer build wrote is refused in words, never with a traceback.

An engine meeting a ``project.json`` it cannot read says which build wrote it and to upgrade,
whatever made it unreadable: a newer major schema, a field that a newer minor version added, a
wrong type, and a file that is not JSON. The identity model forbids fields it does not declare, so
the engine reads the version that explains the problem before it checks the fields. A newer minor
schema with no field this engine lacks still opens.

Every name is invented. The field is called ``a_later_field``, so the test says the same of any
field a later build adds, ``name`` included.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ingence import SCHEMA_VERSION, __version__
from ingence.cli.main import app
from ingence.core.project import PROJECT_FILE, NotAProjectError, Project

REPOSITORY = Path(__file__).resolve().parents[1]
MAJOR, MINOR = (int(part) for part in SCHEMA_VERSION.split("."))
#: Versions after this build's, derived from it so the test never goes stale when it moves.
NEWER_MINOR = f"{MAJOR}.{MINOR + 7}"
NEWER_MAJOR = f"{MAJOR + 1}.0"


def _project(base: Path, **changes: Any) -> Path:
    """A project whose ``project.json`` holds what this build wrote, with ``changes`` laid on."""
    pool = base / "pool"
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "kelt_01.csv").write_text("a,b\n1,2\n")
    made = Project.create(base / "project", created_at="t", roots=[pool]).path
    held = json.loads((made / PROJECT_FILE).read_text(encoding="utf-8"))
    held.update(changes)
    (made / PROJECT_FILE).write_text(json.dumps(held), encoding="utf-8")
    return made


def test_an_unknown_field_is_refused_in_words_naming_the_build_that_wrote_it(
    tmp_path: Path,
) -> None:
    path = _project(tmp_path, a_later_field="x", created_by="9.8.7")
    with pytest.raises(NotAProjectError) as refused:
        Project.open(path)
    said = str(refused.value)
    assert "made by Ingence 9.8.7" in said
    assert "the field a_later_field is not one it knows" in said
    assert f"Ingence {__version__}" in said and "Upgrade Ingence to open it" in said
    assert "Traceback" not in said and "ValidationError" not in said


def test_a_newer_minor_schema_with_an_unknown_field_is_refused_in_words(tmp_path: Path) -> None:
    path = _project(tmp_path, a_later_field="x", schema_version=NEWER_MINOR, created_by="9.8.7")
    with pytest.raises(NotAProjectError) as refused:
        Project.open(path)
    said = str(refused.value)
    assert "made by Ingence 9.8.7 at schema version " + NEWER_MINOR in said
    assert "Upgrade Ingence to open it" in said


def test_a_newer_major_schema_is_refused_in_words_naming_the_build(tmp_path: Path) -> None:
    path = _project(tmp_path, schema_version=NEWER_MAJOR, created_by="9.8.7")
    with pytest.raises(NotAProjectError) as refused:
        Project.open(path)
    said = str(refused.value)
    assert f"made by Ingence 9.8.7 at schema version {NEWER_MAJOR}" in said
    assert f"this engine understands {SCHEMA_VERSION}" in said
    assert "Upgrade Ingence to open it" in said


def test_a_newer_major_schema_is_refused_before_its_unknown_fields_are_checked(
    tmp_path: Path,
) -> None:
    path = _project(tmp_path, a_later_field="x", schema_version=NEWER_MAJOR, created_by="9.8.7")
    with pytest.raises(NotAProjectError, match="this engine understands"):
        Project.open(path)


def test_a_newer_minor_schema_with_no_field_this_engine_lacks_still_opens(tmp_path: Path) -> None:
    path = _project(tmp_path, schema_version=NEWER_MINOR, created_by="9.8.7")
    assert Project.open(path).identity.schema_version == NEWER_MINOR


def test_a_wrong_type_is_refused_in_words_and_a_missing_builder_is_said_so(
    tmp_path: Path,
) -> None:
    path = _project(tmp_path, roots="none")
    held = json.loads((path / PROJECT_FILE).read_text(encoding="utf-8"))
    del held["created_by"]
    (path / PROJECT_FILE).write_text(json.dumps(held), encoding="utf-8")
    with pytest.raises(NotAProjectError) as refused:
        Project.open(path)
    said = str(refused.value)
    assert "made by another build of Ingence" in said
    assert "created_by is missing" in said and "roots is not a value it can read" in said
    assert "Upgrade Ingence to open it" in said


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"a string"', ""])
def test_a_project_json_that_is_no_record_is_refused_in_words(tmp_path: Path, text: str) -> None:
    path = _project(tmp_path)
    (path / PROJECT_FILE).write_text(text, encoding="utf-8")
    with pytest.raises(NotAProjectError, match=r"project\.json"):
        Project.open(path)


def test_a_version_that_is_no_version_does_not_hide_the_fields_check(tmp_path: Path) -> None:
    path = _project(tmp_path, schema_version="soon")
    # Not a number to compare, so the file's own fields are checked: they hold, and it opens.
    assert Project.open(path).identity.schema_version == "soon"


#: Each verb that opens a project, with where it takes the project's name.
VERBS = (
    (["index"], "-p"),
    (["dive"], "-p"),
    (["infer"], "-p"),
    (["fix"], "-p"),
    (["see", "--no-open"], "-p"),
    (["rule", "show"], "-p"),
    (["teach", "vocabulary"], "-p"),
    (["teach", "unknown"], "-p"),
    (["teach", "collisions"], "-p"),
    (["teach", "shapes"], "-p"),
    (["teach", "pack", "list"], "-p"),
    (["export", "themes"], "--project"),
)


@pytest.mark.parametrize(("verb", "flag"), VERBS, ids=[" ".join(verb) for verb, _ in VERBS])
def test_every_verb_refuses_a_newer_builds_project_in_words(
    tmp_path: Path, verb: list[str], flag: str
) -> None:
    path = _project(tmp_path, a_later_field="x", schema_version=NEWER_MINOR, created_by="9.8.7")
    ran = CliRunner().invoke(app, [*verb, flag, str(path)])
    assert ran.exit_code != 0, ran.output
    # A refusal is an exit, never an exception the runner had to catch.
    assert isinstance(ran.exception, SystemExit), repr(ran.exception)
    assert "Traceback" not in ran.output
    said = " ".join(ran.output.split())
    assert "made by Ingence 9.8.7" in said and "Upgrade Ingence to open it" in said


def test_the_command_a_person_types_prints_no_traceback(tmp_path: Path) -> None:
    path = _project(tmp_path, a_later_field="x", created_by="9.8.7")
    environment = {**os.environ, "PYTHONPATH": str(REPOSITORY / "src")}
    ran = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "index", "-p", str(path)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    both = ran.stdout + ran.stderr
    assert ran.returncode != 0
    assert "Traceback" not in both and "ValidationError" not in both
    assert "Upgrade Ingence to open it" in " ".join(both.split())
