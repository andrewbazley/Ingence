"""The Settings page's routes.

- A catalogue beside the settings: every setting the model declares has one line of plain words, and
  no line names a setting that does not exist.
- Every row is an editable field pre-filled with the value in force, of the kind its value takes (a
  switch, a choice, a number, words, a comma list), grouped by section under a plain heading, with
  where it comes from and, where rules read it, which.
- The "Comes from" column links the file: a route answers the file's text, and only for the two
  files.
- Export and import a portable settings file: only what somebody chose, and an import is a preview
  that writes nothing, refusing in words what is unknown or invalid.
- A value put back to its default is never written as if chosen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ingence.api.routes import workspace as workspace_routes
from ingence.core.project import Project
from ingence.core.settings import DASHBOARD_SETTINGS_FILE, SETTINGS_FILE, Settings
from ingence.workspace.checkpoints import commit
from ingence.workspace.drafts import read_draft, save_draft
from ingence.workspace.settings_overlay import flatten

CATALOGUE = Path(workspace_routes.__file__).parents[1] / "settings_catalog.yaml"


def _declared() -> set[str]:
    return set(flatten(Settings().model_dump(mode="json"))) - {"schema_version"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    return Project.create(tmp_path / "project", created_at="t", roots=[pool]).path


def test_the_catalogue_names_every_setting_and_nothing_else() -> None:
    catalogue = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
    named = set(catalogue["settings"])
    assert named == _declared(), (sorted(_declared() - named), sorted(named - _declared()))
    for name, purpose in catalogue["settings"].items():
        assert isinstance(purpose, str) and purpose.strip(), name
        assert not purpose.rstrip().endswith("."), name
    sections = {name.split(".")[0] for name in named}
    assert sections <= set(catalogue["sections"])


def test_every_row_is_pre_filled_with_its_purpose_and_section(project: Path) -> None:
    rows = workspace_routes.read(project, ["settings"])["settings"]
    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == _declared()
    for row in rows:
        assert row["purpose"], row["name"]
        assert row["section_heading"], row["name"]
        assert row["kind"] in {"boolean", "choice", "number", "text", "list"}, row
    # The kinds their values take, and the choices a choice offers.
    assert by_name["scan.include_hidden"]["kind"] == "boolean"
    assert by_name["scan.workers"]["kind"] == "number"
    assert by_name["hash.mode"]["kind"] == "choice"
    assert "need" in by_name["hash.mode"]["choices"]
    # ``true`` is read as no choice and is not offered.
    assert by_name["hash.skip_remote_filesystems"]["choices"] == [None, False, "always"]
    assert by_name["scan.exclude_globs"]["kind"] == "list"
    assert by_name["dictionary.years"]["kind"] == "list"
    assert by_name["scan.max_depth"]["kind"] == "number"
    assert by_name["scan.max_depth"]["nullable"] is True
    assert by_name["listing.user_agent"]["kind"] == "text"
    # A list and a value with no default are editable, and a fixed one is not.
    assert by_name["scan.exclude_globs"]["editable"] and by_name["scan.max_depth"]["editable"]
    assert not by_name["scan.follow_symlinks"]["editable"]
    # Grouped: every row of a section is together, in the catalogue's order of sections.
    order = [row["section"] for row in rows]
    assert order == sorted(order, key=order.index)


def test_where_a_value_comes_from_is_named_by_its_file(project: Path) -> None:
    (project / DASHBOARD_SETTINGS_FILE).write_text("scan:\n  workers: 3\n", encoding="utf-8")
    rows = {row["name"]: row for row in workspace_routes.read(project, ["settings"])["settings"]}
    assert rows["scan.workers"]["source"] == "dashboard"
    assert rows["scan.workers"]["source_file"] == DASHBOARD_SETTINGS_FILE
    assert rows["scan.workers"]["changed"] is True
    assert rows["hash.mode"]["source_file"] is None
    assert rows["hash.mode"]["changed"] is False


def test_a_settings_file_s_text_is_answered_for_the_two_files_only(project: Path) -> None:
    shown = workspace_routes.read(project, ["settings-file", SETTINGS_FILE])
    assert shown["name"] == SETTINGS_FILE
    assert shown["text"] == (project / SETTINGS_FILE).read_text(encoding="utf-8")
    missing = workspace_routes.read(project, ["settings-file", DASHBOARD_SETTINGS_FILE])
    assert missing["text"] is None
    with pytest.raises(LookupError):
        workspace_routes.read(project, ["settings-file", "project.json"])
    with pytest.raises(LookupError):
        workspace_routes.read(project, ["settings-file", "..", "settings.yaml"])


def test_a_setting_read_by_a_rule_names_the_rule(project: Path) -> None:
    """A setting read by a rule names the rule.

    The catalogue derives this from a declaration. A rule whose predicate reads where each part sat
    in a name, because it compares parts in their places, reads ``dictionary.record_name_shapes``.
    The name identity does.
    """
    rows = {row["name"]: row for row in workspace_routes.read(project, ["settings"])["settings"]}
    named = {rule["rule_id"]: rule for rule in rows["dictionary.record_name_shapes"]["rules"]}
    assert "same_name_identity" in named
    assert named["same_name_identity"]["plain"]
    assert rows["scan.workers"]["rules"] == []


def test_an_export_holds_only_what_somebody_chose(project: Path) -> None:
    (project / SETTINGS_FILE).write_text(
        "scan:\n  workers: 8\n  include_hidden: true\n", encoding="utf-8"
    )
    (project / DASHBOARD_SETTINGS_FILE).write_text("hash:\n  mode: none\n", encoding="utf-8")
    exported = workspace_routes.read(project, ["settings-export"])
    held = yaml.safe_load(exported["text"])
    # scan.workers is written at its default in settings.yaml: never exported as chosen.
    assert flatten(held) == {"scan.include_hidden": True, "hash.mode": "none"}
    assert exported["file_name"].endswith(".yaml")


def test_an_import_is_a_preview_that_writes_nothing(project: Path) -> None:
    before = sorted(path.name for path in project.iterdir())
    text = (
        "scan:\n  workers: 4\n  include_hidden: false\n  no_such_thing: 1\n"
        "hash:\n  mode: sideways\n"
    )
    preview = workspace_routes.write(project, "/api/workspace/settings-import", {"document": text})
    changes = {row["name"]: row for row in preview["changes"]}
    assert changes["scan.workers"]["now"] == 8 and changes["scan.workers"]["imported"] == 4
    # A value the same as in force is listed as the same, not as a change.
    assert changes["scan.include_hidden"]["same"] is True
    refused = {row["name"]: row["why"] for row in preview["refused"]}
    assert "no setting" in refused["scan.no_such_thing"]
    assert refused.get("hash.mode")
    assert sorted(path.name for path in project.iterdir()) == before
    assert read_draft(project, "settings").empty


@pytest.mark.parametrize("document", ["", "- a\n- b\n", "scan: [\n"])
def test_an_import_of_something_that_is_no_settings_file_is_refused_in_words(
    project: Path, document: str
) -> None:
    with pytest.raises(ValueError, match="settings"):
        workspace_routes.write(project, "/api/workspace/settings-import", {"document": document})


def test_a_value_put_back_to_its_default_is_never_written_as_chosen(project: Path) -> None:
    save_draft(project, "settings", {"scan": {"workers": 4}})
    commit(project, actor="brisa", message="fewer")
    assert "workers" in (project / DASHBOARD_SETTINGS_FILE).read_text(encoding="utf-8")
    save_draft(project, "settings", {"scan": {"workers": 8}})
    commit(project, actor="brisa", message="back")
    written = yaml.safe_load((project / DASHBOARD_SETTINGS_FILE).read_text(encoding="utf-8"))
    assert "scan.workers" not in flatten(written or {})
    assert Project.open(project).settings.scan.workers == 8


def test_a_default_that_overrides_settings_yaml_is_kept_as_chosen(project: Path) -> None:
    (project / SETTINGS_FILE).write_text("scan:\n  workers: 2\n", encoding="utf-8")
    save_draft(project, "settings", {"scan": {"workers": 8}})
    commit(project, actor="brisa", message="back to eight")
    written = yaml.safe_load((project / DASHBOARD_SETTINGS_FILE).read_text(encoding="utf-8"))
    assert flatten(written)["scan.workers"] == 8
    assert Project.open(project).settings.scan.workers == 8


# -- Import and Export by page: what Groupings and History hold ----------------------------


@pytest.fixture(scope="module")
def answered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project with a run and two decisions: one about a class, one about a question."""
    from ingence.core.schema import RecordEventKind
    from ingence.infer.run import run_inference
    from ingence.record.log import append_event
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("exports")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn_raw_1.csv").write_text("a,b\n1,2\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    for target, word in (("group:kelt vorn", "accept"), ("question:some_rule:zeph", "no")):
        append_event(
            project.path,
            kind=RecordEventKind.CONFIRM,
            actor="brisa",
            occurred_at="2026-01-02T00:00:00Z",
            target=target,
            against_run_id="run_20260101T000002Z_222222222222",
            payload={"word": word, "note": "kelt and vorn go together"},
        )
    return project.path


def test_groupings_exports_its_answers_and_their_notes(answered: Path) -> None:
    import csv
    import io

    exported = workspace_routes.read(answered, ["export", "groupings"])
    rows = list(csv.DictReader(io.StringIO(exported["text"])))
    assert [row["about"] for row in rows] == ["group:kelt vorn"]
    assert rows[0]["note"] == "kelt and vorn go together" and rows[0]["who"] == "brisa"
    assert exported["file_name"].endswith(".csv")


def test_history_exports_every_run_and_every_record_event(answered: Path) -> None:
    import json

    exported = workspace_routes.read(answered, ["export", "history"])
    held = json.loads(exported["text"])
    assert [event["target"] for event in held["record"]] == [
        "group:kelt vorn",
        "question:some_rule:zeph",
    ]
    assert len(held["runs"]) == 2


# -- more checks of the page ---------------------------------------------------------------


def test_every_choice_of_every_enumerated_setting_is_said_in_words(project: Path) -> None:
    """A choice is offered in words saying what it does, with its stored value on hover.

    A value read as another (``true`` for shares, read as no choice) is not offered.
    """
    rows = {row["name"]: row for row in workspace_routes.read(project, ["settings"])["settings"]}
    choices = [row for row in rows.values() if row["kind"] == "choice"]
    assert len(choices) >= 12
    for row in choices:
        for choice in row["choices"]:
            said = row["choice_words"].get(json.dumps(choice))
            assert said and said.strip(), (row["name"], choice)
    shares = rows["hash.skip_remote_filesystems"]
    assert shares["choices"] == [None, False, "always"]
    assert shares["choice_words"]['"always"'] == "never hash on a network drive"
    assert shares["choice_words"]["null"] == "left to the depth"
    assert shares["choice_words"]["false"] == "hash network drives too"


def test_a_choice_in_force_that_is_not_offered_is_still_shown(project: Path) -> None:
    (project / SETTINGS_FILE).write_text("hash:\n  skip_remote_filesystems: true\n", "utf-8")
    rows = {row["name"]: row for row in workspace_routes.read(project, ["settings"])["settings"]}
    shares = rows["hash.skip_remote_filesystems"]
    assert True in shares["choices"]
    assert "left to the depth" in shares["choice_words"]["true"]


def test_a_list_of_words_stays_words_and_a_list_of_numbers_numbers(project: Path) -> None:
    """The kind of a list's items is read from the settings model, never from what is typed."""
    rows = {row["name"]: row for row in workspace_routes.read(project, ["settings"])["settings"]}
    assert rows["hash.include_extensions"]["list_of"] == "text"
    assert rows["scan.exclude_globs"]["list_of"] == "text"
    assert rows["dictionary.plausible_years"]["list_of"] == "number"
    assert rows["dictionary.years"]["list_of"] == "mixed"


def test_a_settings_file_repeating_itself_through_references_is_refused(project: Path) -> None:
    """YAML references (anchors and aliases) are refused before anything is expanded."""
    levels = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for level in range(1, 6):
        refs = ", ".join([f"*a{level - 1}"] * 10)
        levels.append(f"a{level}: &a{level} [{refs}]")
    text = "\n".join(levels) + "\nscan: {exclude_globs: *a5}\n"
    with pytest.raises(ValueError, match="names each setting once"):
        workspace_routes.write(project, "/api/workspace/settings-import", {"document": text})


def test_an_import_refuses_at_most_a_bounded_list_and_says_how_many_more(project: Path) -> None:
    text = "scan:\n" + "".join(f"  unknown_{index}: 1\n" for index in range(400))
    shown = workspace_routes.write(project, "/api/workspace/settings-import", {"document": text})
    assert len(shown["refused"]) <= 100
    assert shown["refused_more"] == 400 - len(shown["refused"])


def test_a_refusal_is_said_in_plain_words(project: Path) -> None:
    """A refusal is said in plain words: a section named as if it were a setting, and a value of the
    wrong kind.
    """
    text = "scan: 5\nhash:\n  exclude_extensions: tif\n"
    shown = workspace_routes.write(project, "/api/workspace/settings-import", {"document": text})
    refused = {row["name"]: row["why"] for row in shown["refused"]}
    assert "is a section" in refused["scan"]
    assert "a list" in refused["hash.exclude_extensions"]
    assert "tuple" not in refused["hash.exclude_extensions"]


def test_a_value_put_back_to_what_settings_yaml_says_is_not_written_again(project: Path) -> None:
    """A value put back to what ``settings.yaml`` says is not written again.

    ``settings.yaml`` itself sets the default. Set another value on the page and commit, then put it
    back and commit. The Dashboard's line goes, since it chooses nothing.
    """
    (project / SETTINGS_FILE).write_text("scan:\n  workers: 8\n", encoding="utf-8")
    save_draft(project, "settings", {"scan": {"workers": 4}})
    commit(project, actor="brisa", message="fewer")
    save_draft(project, "settings", {"scan": {"workers": 8}})
    commit(project, actor="brisa", message="back")
    written = yaml.safe_load((project / DASHBOARD_SETTINGS_FILE).read_text(encoding="utf-8"))
    assert "scan.workers" not in flatten(written or {})
    assert Project.open(project).settings.scan.workers == 8


def _declared_by_model() -> set[str]:
    """Every setting the model declares, walked field by field.

    A section is a ``Setting`` of its own, and a mapping-valued setting would still be named here.
    """
    from ingence.core.settings import Setting

    found: set[str] = set()

    def walk(model: type[Setting], prefix: str) -> None:
        for name, field in model.model_fields.items():
            kind = field.annotation
            dotted = f"{prefix}{name}"
            if isinstance(kind, type) and issubclass(kind, Setting):
                walk(kind, f"{dotted}.")
            elif dotted != "schema_version":
                found.add(dotted)

    walk(Settings, "")
    return found


def test_the_catalogue_names_what_the_model_declares_and_its_declarations_are_real() -> None:
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.registry import PREDICATES

    catalogue = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
    declared = _declared_by_model()
    assert set(catalogue["settings"]) == declared
    load_builtins()
    for setting, readers in catalogue["read_by"].items():
        assert setting in declared, setting
        for reader in readers:
            assert reader["predicate"] in PREDICATES.names(), reader
    assert set(catalogue["choices"]) <= declared


def test_a_commit_stands_where_the_dashboard_overrules_an_invalid_hand_value(
    project: Path,
) -> None:
    """A commit stands when ``settings.yaml`` holds a value the model refuses and the Dashboard's
    file overrules it.

    The composed settings are valid, so a commit must stand.
    """
    (project / SETTINGS_FILE).write_text("hash:\n  mode: sideways\n", encoding="utf-8")
    (project / DASHBOARD_SETTINGS_FILE).write_text("hash:\n  mode: need\n", encoding="utf-8")
    save_draft(project, "settings", {"scan": {"workers": 4}})
    commit(project, actor="brisa", message="fewer")
    written = flatten(
        yaml.safe_load((project / DASHBOARD_SETTINGS_FILE).read_text(encoding="utf-8"))
    )
    assert written["scan.workers"] == 4 and written["hash.mode"] == "need"
    # Put back to the default, it is taken out; the overruling value stays.
    save_draft(project, "settings", {"scan": {"workers": 8}})
    commit(project, actor="brisa", message="back")
    written = flatten(
        yaml.safe_load((project / DASHBOARD_SETTINGS_FILE).read_text(encoding="utf-8"))
    )
    assert "scan.workers" not in written and written["hash.mode"] == "need"


def test_the_depth_is_offered_as_its_three_choices_in_words(project: Path) -> None:
    """``depth`` is a choice, each in the catalogue's words, and a value a file already holds is
    still shown as written.

    It is not free text because a dive reads only ``metadata``, ``standard`` or ``deep``
    (``scan.profiles.depth_named``).
    """
    rows = {row["name"]: row for row in workspace_routes.read(project, ["settings"])["settings"]}
    depth = rows["depth"]
    assert depth["kind"] == "choice"
    assert depth["choices"] == ["metadata", "standard", "deep"]
    assert all(depth["choice_words"].get(json.dumps(choice)) for choice in depth["choices"])
    (project / SETTINGS_FILE).write_text("depth: census\n", "utf-8")
    rows = {row["name"]: row for row in workspace_routes.read(project, ["settings"])["settings"]}
    assert rows["depth"]["choices"][-1] == "census"
    assert "as written" in rows["depth"]["choice_words"]['"census"']
