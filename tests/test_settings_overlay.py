"""The Dashboard's settings file: laid over the hand-edited one, never rewriting it."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ingence.core.project import Project
from ingence.workspace.settings_overlay import (
    DASHBOARD_SETTINGS_FILE,
    read_layers,
    source_of,
    write_dashboard_settings,
)

HAND = "# kept by hand, comment and all\nscan:\n  workers: 3  # three is plenty here\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    made = Project.create(tmp_path / "project", created_at="t")
    (made.path / "settings.yaml").write_text(HAND, "utf-8")
    return made.path


def test_a_write_leaves_the_hand_file_byte_identical_and_open_reads_the_pair(project: Path) -> None:
    before = (project / "settings.yaml").read_bytes()
    write_dashboard_settings(
        project,
        {"scan": {"workers": 12}, "hash": {"chunk_bytes": 8192}},
        provenance={
            "scan.workers": "set by Ana on 2031-01-02: more threads",
            "hash.chunk_bytes": "set by Ana on 2031-01-02: more threads",
        },
    )
    write_dashboard_settings(
        project, {"depth": "deep"}, provenance={"depth": "set by Bo on 2031-02-03: go deeper"}
    )
    assert (project / "settings.yaml").read_bytes() == before
    opened = Project.open(project).settings
    assert opened.scan.workers == 12
    assert opened.hash.chunk_bytes == 8192
    assert opened.depth == "deep"
    layers = read_layers(project)
    assert source_of(layers, "scan.workers") == "dashboard"
    assert source_of(layers, "hash.mode") == "default"


def test_each_value_carries_one_comment_line_and_a_later_write_keeps_the_earlier_ones(
    project: Path,
) -> None:
    write_dashboard_settings(
        project,
        {"scan": {"workers": 12}},
        provenance={"scan.workers": "set by Ana on 2031-01-02: more\nthreads"},
    )
    write_dashboard_settings(
        project, {"depth": "deep"}, provenance={"depth": "set by Bo on 2031-02-03: deeper"}
    )
    lines = (project / DASHBOARD_SETTINGS_FILE).read_text("utf-8").splitlines()
    above = {
        line.split(":", 1)[0].strip(): lines[index - 1].strip()
        for index, line in enumerate(lines)
        if line.strip().startswith(("workers:", "depth:"))
    }
    assert above["workers"] == "# set by Ana on 2031-01-02: more threads"
    assert above["depth"] == "# set by Bo on 2031-02-03: deeper"


def test_a_pair_the_engine_would_refuse_is_refused_before_anything_is_written(
    project: Path,
) -> None:
    with pytest.raises(ValidationError):
        write_dashboard_settings(project, {"scan": {"workers": -4}}, provenance={})
    assert not (project / DASHBOARD_SETTINGS_FILE).exists()


def test_a_project_without_the_file_opens_exactly_as_before(project: Path) -> None:
    assert Project.open(project).settings.scan.workers == 3
    (project / "settings.yaml").unlink()
    assert Project.open(project).settings.scan.workers == 8


def test_a_list_and_a_quoted_string_survive_the_file(project: Path) -> None:
    write_dashboard_settings(
        project,
        {"scan": {"exclude_globs": ["kelt: vorn", "zib"]}},
        provenance={"scan.exclude_globs": "set by Ana on 2031-01-02: skip two"},
    )
    assert Project.open(project).settings.scan.exclude_globs == ("kelt: vorn", "zib")


#: Characters YAML refuses in a file, reads as a line end, or reads as two characters when JSON
#: writes them as it does: a delete, a C1 control, a line separator, a nul, and one past U+FFFF.
ODD = "deep\x7f\x85\u2028\x00\U0001d400"


def test_a_text_setting_holding_control_characters_reads_back_as_typed(project: Path) -> None:
    """A control character is read back as typed.

    A control character written raw makes the YAML reader refuse the file, and then every command
    stops.
    """
    write_dashboard_settings(
        project,
        {"depth": ODD},
        provenance={"depth": "set by Ana\x7f on 2031-01-02: odd\u2028zib: 1\x85still the note"},
    )
    assert Project.open(project).settings.depth == ODD
    # The comment stays one line of printable characters, and adds no setting.
    assert read_layers(project).dashboard == {"depth": ODD}
    text = (project / DASHBOARD_SETTINGS_FILE).read_text("utf-8")
    assert text.isascii()
    assert "# set by Ana on 2031-01-02: odd zib: 1 still the note" in text


def test_a_file_that_would_not_read_back_is_never_written(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_dashboard_settings(project, {"depth": "deep"}, provenance={})
    kept = (project / DASHBOARD_SETTINGS_FILE).read_bytes()
    # A writer that sets down a value as it was typed.
    monkeypatch.setattr("ingence.workspace.settings_overlay._flow", lambda value: f'"{value}"')
    with pytest.raises(ValueError, match="reads back"):
        write_dashboard_settings(project, {"depth": "deep\x7f"}, provenance={})
    assert (project / DASHBOARD_SETTINGS_FILE).read_bytes() == kept
    assert Project.open(project).settings.depth == "deep"


def test_an_unreadable_dashboard_file_is_named_with_what_deleting_it_does(project: Path) -> None:
    from typer.testing import CliRunner

    from ingence.cli.main import app
    from ingence.core.project import SettingsFileError

    (project / DASHBOARD_SETTINGS_FILE).write_text('depth: "deep\x7f"\n', "utf-8")
    with pytest.raises(SettingsFileError) as refused:
        Project.open(project)
    said = str(refused.value)
    assert DASHBOARD_SETTINGS_FILE in said
    assert "delete it and the values in settings.yaml apply again" in said
    result = CliRunner().invoke(app, ["see", "-p", str(project), "--no-open"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    # The name ends a path longer than the terminal is wide, which is folded where the width
    # falls, inside the name as often as not: read with every break taken out.
    assert DASHBOARD_SETTINGS_FILE in "".join(result.output.split())
    # As it says: deleting it gives settings.yaml's values back.
    (project / DASHBOARD_SETTINGS_FILE).unlink()
    assert Project.open(project).settings.scan.workers == 3


def test_an_unreadable_settings_yaml_is_named_too(project: Path) -> None:
    from ingence.core.project import SettingsFileError

    (project / "settings.yaml").write_text("scan: [workers\n", "utf-8")
    with pytest.raises(SettingsFileError, match=r"settings\.yaml cannot be read"):
        Project.open(project)
