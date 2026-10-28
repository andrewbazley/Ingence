"""The workspace routes: drafts, commit and re-run, and the state the header shows.

Reads (``GET /api/workspace/<what>``):

- ``state``: how many edits are uncommitted, what waits for a re-run (``workspace/rerun``: the
  count that the badge on Re-run shows, and a summary by kind), and what the applier is doing.
  The state holds no preferences: nothing about closing is remembered.
- ``settings``: every setting, with the value in force, where it came from (the shipped default,
  ``settings.yaml`` or the Dashboard's own file), its default, and any draft of it with its
  error. Each row also carries its purpose in plain words and its section's heading, from the
  catalog beside the settings (``api/settings_catalog.yaml``). It carries the kind of field its
  value takes (a switch, a choice with its choices, a number, words, or a comma list), whether it
  differs from its default, the file it comes from, and the rules in force that read it (from
  the catalog's ``read_by``).
- ``settings-file/<name>``: the text of ``settings.yaml`` or ``settings.dashboard.yaml``, and of
  no other file, for the page's "comes from" link.
- ``settings-export``: a portable settings file that holds only what somebody chose: each value
  that either file sets and that differs from its default.
- ``export/groupings`` and ``export/history`` (Import and Export by page): what Groupings holds
  (the group answers and their notes, as a table that a spreadsheet opens), and what History
  holds (every run and every Record event, withdrawn ones too, as the Record keeps them, in one
  file). Both read and write nothing.
- ``commit-preview``: what committing the drafts now would need re-run.

Writes (``POST``) each sit behind the server's write guard. None of them is a decision about the
data, which is the narrower rule that ``server.WRITE_ROUTES`` states:

- ``draft``: add edits to one kind of draft, and take out what was put back as it was
  (``removed``). A save never replaces the draft, so an empty one changes nothing.
- ``sheet-preview``: a review sheet read as ``ingence record import --check`` reads it, writing
  nothing. The preview shows each answer it would write, one per target, and what it refuses and
  why. Import writes those answers through the answer route (``api/routes/imports.py``).
  Groupings reads a groupings sheet (a ``class key`` column), and the route refuses any other
  kind.
- ``settings-import``: a preview that writes nothing. It shows each value a settings file sets,
  beside the value in force, and each refused in words (a name no setting has, a value the
  setting cannot take). The page takes the changes as drafts through ``draft``.
- ``commit``: apply the drafts and record a checkpoint (``settings.yaml`` is never written).
- ``restore``: go back to an earlier run (``workspace/restore.py``). It moves the run pointer,
  restores the Dashboard's settings file, and appends one withdrawal to the Record through the
  Record's own writer. It runs under the project's publish lock. Restore is the one write here
  that touches the Record.
- ``infer``: run the rules on a project that was read and never inferred. Only a person's click
  asks for it, and it reads the project alone.
- ``rerun``: apply everything that waits and reads only the project (the one list in
  ``workspace/state.py``) in one job on the Definition applier's queue. The job reads the names
  again (only those holding the changed words, where only words changed), then the rules. What
  needs a walk or a dive is said with the commands to run, because both read the share and a
  person starts them. It holds nothing else back. Only a person starts it: nothing re-runs when
  the Dashboard opens, and nothing resumes a re-run that a close stopped.
"""

from __future__ import annotations

import functools
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from ingence.workspace.rerun import Step

__all__ = ["CATALOGUE", "read", "write"]

#: What each setting is for, in plain words, with the page's section headings.
CATALOGUE = Path(__file__).parents[1] / "settings_catalog.yaml"

#: The files a "comes from" link may show, by the name the page gives.
_SETTINGS_FILES = ("settings.yaml", "settings.dashboard.yaml")

#: The page's word for a value's source, by the file it names.
_SOURCE_FILE = {"settings.yaml": "settings.yaml", "dashboard": "settings.dashboard.yaml"}


@functools.cache
def _catalogue() -> dict[str, Any]:
    loaded = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _shape(name: str) -> dict[str, Any]:
    """The kind of field one setting's value takes, read from its declaration in the model.

    A yes-or-no is a switch; a closed set (an enumeration, a literal, or a union of them with a
    yes-or-no and "none") a choice with its choices; a number a number, which may be left empty
    where the setting takes none; a tuple a comma list; anything else words. A frozen field is
    shown and never edited.
    """
    import enum
    import types
    import typing

    from ingence.core.settings import Settings

    closed_by_reader = _closed_by_reader().get(name)
    if closed_by_reader is not None:
        return {"nullable": False, "editable": True, "kind": "choice", "choices": closed_by_reader}
    model: Any = Settings
    field_info = None
    for part in name.split("."):
        field_info = model.model_fields[part]
        model = field_info.annotation
    assert field_info is not None
    annotation: Any = field_info.annotation

    def unwrapped(kind: Any) -> Any:
        while typing.get_origin(kind) is typing.Annotated:
            kind = typing.get_args(kind)[0]
        return kind

    annotation = unwrapped(annotation)
    members = (
        [unwrapped(member) for member in typing.get_args(annotation)]
        if typing.get_origin(annotation) in (typing.Union, types.UnionType)
        else [annotation]
    )
    nullable = type(None) in members
    members = [member for member in members if member is not type(None)]
    editable = not bool(getattr(field_info, "frozen", False))
    choices: list[Any] = []
    closed = True
    for member in members:
        if member is bool:
            choices.extend([True, False])
        elif typing.get_origin(member) is typing.Literal:
            choices.extend(typing.get_args(member))
        elif isinstance(member, type) and issubclass(member, enum.Enum):
            choices.extend(item.value for item in member)
        else:
            closed = False
    shape: dict[str, Any] = {"nullable": nullable, "editable": editable, "choices": []}
    if closed and members == [bool] and not nullable:
        return {**shape, "kind": "boolean"}
    if closed and choices:
        return {**shape, "kind": "choice", "choices": ([None] if nullable else []) + choices}
    single = members[0] if len(members) == 1 else None
    if typing.get_origin(single) is tuple:
        # What a list holds is read from the model, never from what is typed. Words stay words
        # ("001", "2019"), numbers stay numbers, and a mixed list (a year or "present") takes a
        # whole number as a number.
        items = [unwrapped(item) for item in typing.get_args(single) if item is not Ellipsis]
        if all(item is str for item in items):
            return {**shape, "kind": "list", "list_of": "text"}
        numbers = all(item in (int, float) for item in items)
        return {**shape, "kind": "list", "list_of": "number" if numbers else "mixed"}
    if single in (int, float):
        return {**shape, "kind": "number"}
    return {**shape, "kind": "text"}


def _closed_by_reader() -> dict[str, list[Any]]:
    """Settings declared as words whose reader takes only a fixed set: offered as a choice.

    ``depth`` is words in the model, validated where it is read (``scan.profiles.depth_named``),
    so a wrong value is answered in a sentence. A dive reads only its three depths, each by the
    word a person uses (``metadata`` for the census profile). So the Settings page offers
    ``depth`` as a choice and not as a free text box.
    """
    from ingence.scan.profiles import DEPTH_ALIASES, DEPTHS

    spelled = {profile: alias for alias, profile in DEPTH_ALIASES.items()}
    return {"depth": [spelled.get(depth, depth.value) for depth in DEPTHS]}


def _catalogued_choice(spelled: object) -> object:
    """A choice as the catalog keys it (``null``, ``true``, ``false`` or a word), as a value."""
    return {"null": None, "true": True, "false": False}.get(str(spelled), str(spelled))


def _offered(name: str, choices: list[Any], in_force: object) -> tuple[list[Any], dict[str, str]]:
    """The choices a setting offers, in the catalog's order, and each one's words by its JSON.

    A choice the catalog gives no words (``~``) is read as another and is not offered, unless
    a file already holds it, when it is shown with the words of what it is read as.
    """
    import json

    said = (_catalogue().get("choices") or {}).get(name) or {}
    words: dict[str, str] = {}
    hidden: list[Any] = []
    offered: list[Any] = []
    for spelled, text in said.items():
        value = _catalogued_choice(spelled)
        if value not in choices:
            continue
        if text:
            offered.append(value)
            words[json.dumps(value)] = str(text)
        else:
            hidden.append(value)
    offered.extend(choice for choice in choices if choice not in offered and choice not in hidden)
    if in_force in hidden:
        offered.append(in_force)
        read_as = words.get("null", "")
        words[json.dumps(in_force)] = f"{read_as} (as written)" if read_as else json.dumps(in_force)
    elif in_force is not None and in_force not in offered:
        # A word the reader takes but the page does not offer (``depth: census``), as written.
        offered.append(in_force)
        words[json.dumps(in_force)] = f"{in_force} (as written)"
    return offered, words


def _read_by(project: Path) -> dict[str, list[dict[str, Any]]]:
    """The rules in force now that read each setting, by the catalog's ``read_by``."""
    declared = _catalogue().get("read_by") or {}
    if not declared:
        return {}
    from ingence.api.plain_words import rule_sentence
    from ingence.rulebook.load import (
        RuleDeclarationError,
        load_project_revisions,
        load_revisions,
    )
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.render import value_in_force
    from ingence.rulebook.resolve import effective_rulebook
    from ingence.storage.layout import RULEBOOK_DIR

    try:
        load_builtins()
        book = effective_rulebook(
            load_revisions(), project=load_project_revisions(project / RULEBOOK_DIR)
        )
    except (RuleDeclarationError, OSError, ValueError):
        return {}
    found: dict[str, list[dict[str, Any]]] = {}
    for setting, readers in declared.items():
        for rule in book.rules:
            for reader in readers or []:
                if rule.predicate != reader.get("predicate"):
                    continue
                needed = reader.get("when_parameter")
                if needed:
                    held = next((spec for spec in rule.parameters if spec.name == needed), None)
                    if held is None or not value_in_force(held):
                        continue
                found.setdefault(str(setting), []).append(
                    {
                        "rule_id": rule.rule_id,
                        "version": rule.version,
                        "plain": rule_sentence(rule.rule_id) or "",
                    }
                )
    return found


def _settings_rows(project: Path) -> list[dict[str, Any]]:
    from ingence.core.settings import Settings
    from ingence.workspace.drafts import read_draft
    from ingence.workspace.settings_overlay import flatten, read_layers, source_of

    layers = read_layers(project)
    defaults = flatten(Settings().model_dump(mode="json"))
    in_force = flatten(Settings.model_validate(layers.effective).model_dump(mode="json"))
    draft = read_draft(project, "settings")
    drafted = flatten(draft.values)
    settings_catalog = _catalogue()
    purposes = settings_catalog.get("settings") or {}
    headings = settings_catalog.get("sections") or {}
    section_order = list(headings)
    read_by = _read_by(project)
    rows = []
    for name in sorted(defaults):
        if name == "schema_version":
            continue
        default = defaults[name]
        source = source_of(layers, name)
        section = name.split(".")[0]
        shape = _shape(name)
        if shape["kind"] == "choice":
            shape["choices"], shape["choice_words"] = _offered(
                name, shape["choices"], in_force[name]
            )
        rows.append(
            {
                "name": name,
                "value": in_force[name],
                "default": default,
                "changed": in_force[name] != default,
                "source": source,
                "source_file": _SOURCE_FILE.get(source),
                "purpose": str(purposes.get(name) or ""),
                "section": section,
                "section_heading": str(headings.get(section) or section),
                **shape,
                "rules": read_by.get(name, []),
                "draft": drafted.get(name),
                "drafted": name in drafted,
                "error": draft.errors.get(name),
            }
        )
    order = {section: index for index, section in enumerate(section_order)}
    rows.sort(key=lambda row: (order.get(row["section"], len(order)), row["name"]))
    return rows


def _settings_file(project: Path, parts: list[str]) -> dict[str, Any]:
    """The text of one of the two settings files, for the page's "comes from" link."""
    if len(parts) != 1 or parts[0] not in _SETTINGS_FILES:
        message = "only settings.yaml and settings.dashboard.yaml are shown"
        raise LookupError(message)
    path = project / parts[0]
    return {
        "name": parts[0],
        "path": str(path),
        "text": path.read_text(encoding="utf-8") if path.is_file() else None,
    }


def _chosen(project: Path) -> dict[str, Any]:
    """What somebody chose: each value that either file sets and that differs from its default."""
    from ingence.core.settings import Settings
    from ingence.workspace.settings_overlay import flatten, read_layers

    layers = read_layers(project)
    defaults = flatten(Settings().model_dump(mode="json"))
    in_force = flatten(Settings.model_validate(layers.effective).model_dump(mode="json"))
    set_by_a_file = set(flatten(layers.hand)) | set(flatten(layers.dashboard))
    return {
        name: in_force[name]
        for name in sorted(set_by_a_file & set(defaults))
        if name != "schema_version" and in_force[name] != defaults[name]
    }


def _settings_export(project: Path) -> dict[str, Any]:
    """A portable settings file: only what somebody chose, as ``settings.yaml`` reads it."""
    from ingence.workspace.settings_overlay import unflatten

    chosen = _chosen(project)
    header = (
        "# Ingence settings exported from the Dashboard: only what somebody chose in this "
        "project.\n# A value left at its default is not here. Import it on another project's "
        "Settings page.\n"
    )
    body = yaml.safe_dump(unflatten(chosen), sort_keys=True) if chosen else "{}\n"
    return {"file_name": "ingence-settings.yaml", "text": header + body, "count": len(chosen)}


#: The targets Groupings answers: a class a grouping rule found, and a group of words approved.
_GROUPING_PREFIXES = ("class:", "group:")


def _payload(event: Any) -> dict[str, Any]:
    import json

    try:
        loaded = json.loads(event.payload or "{}")
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _groupings_export(project: Path) -> dict[str, Any]:
    """Every group answer and note not withdrawn, in the order made, as CSV a spreadsheet opens.

    A later answer to the same group follows an earlier one, which stays listed (the Record
    keeps both). Not the sheet Groupings' Import reads: that is a review sheet, by class key.
    """
    import csv
    import io

    from ingence.record.log import read_events

    rows = []
    for event in read_events(project):
        if not event.target.startswith(_GROUPING_PREFIXES):
            continue
        said = _payload(event)
        if said.get("of") == "claims":
            # A class of claims is answered on the Questions page, not Groupings.
            continue
        rows.append(
            {
                "decision": event.sequence,
                "when": event.occurred_at,
                "who": event.actor,
                "about": event.target,
                "kind": event.kind.value,
                "answer": str(said.get("word") or said.get("verdict") or ""),
                "note": str(said.get("note") or ""),
            }
        )
    out = io.StringIO()
    writer = csv.DictWriter(
        out, fieldnames=["decision", "when", "who", "about", "kind", "answer", "note"]
    )
    writer.writeheader()
    writer.writerows(rows)
    return {"file_name": "ingence-groupings.csv", "text": out.getvalue(), "count": len(rows)}


def _history_export(project: Path) -> dict[str, Any]:
    """Every run and every Record event, withdrawn ones too, as one JSON file."""
    import json

    from ingence.api.routes import changes
    from ingence.record.log import read_events

    events = [
        event.model_dump(mode="json") for event in read_events(project, withdrawn=True, moved=False)
    ]
    held = {"runs": changes.runs(project).get("runs", []), "record": events}
    text = json.dumps(held, indent=1, sort_keys=True, default=str) + "\n"
    return {"file_name": "ingence-history.json", "text": text, "count": len(events)}


def _sheet_preview(project: Path, body: dict[str, Any]) -> dict[str, Any]:
    """A groupings sheet checked by the review sheet reader (``record/sheets.py``), never written.

    The sheet is held in a temporary folder of its own for the reader, which takes a file, and
    is gone when the check returns; nothing reaches the project or the Record.
    """
    import tempfile
    from datetime import UTC, datetime
    from pathlib import PurePosixPath, PureWindowsPath

    from ingence.record.sheets import SheetRefusedError, import_sheet
    from ingence.storage.catalog import open_catalog

    document = body.get("document")
    if not isinstance(document, str) or not document.strip():
        message = "the file is empty: a groupings sheet has a class key column and a verdict column"
        raise ValueError(message)
    named = str(body.get("name") or "sheet.csv")
    name = PureWindowsPath(PurePosixPath(named).name).name or "sheet.csv"
    actor = str(body.get("actor") or "").strip()
    if not actor:
        message = "say who is deciding first: whether an answer is already yours depends on it"
        raise ValueError(message)
    now = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(prefix="ingence-sheet-") as held:
        sheet = Path(held) / (name if name.lower().endswith(".csv") else f"{name}.csv")
        sheet.write_text(document, encoding="utf-8")
        try:
            with open_catalog(project) as catalog:
                report = import_sheet(
                    catalog, project, sheet, actor=actor, occurred_at=now, check=True
                )
        except SheetRefusedError as error:
            message = f"the sheet cannot be read: {error}"
            raise ValueError(message) from error
    if report.kind != "class":
        message = (
            f"this is a sheet of {report.kind}s; Groupings reads a groupings sheet, one with a "
            "class key column"
        )
        raise ValueError(message)
    # A word that the group's rule does not take is refused here, as the answer route would
    # refuse it on taking. The sheet's header may offer more than a group takes. A word that was
    # offered before and is still taken (``related`` on a set of copies) is planned.
    from ingence.api.routes.decide import DecisionContext
    from ingence.record.verdicts import class_takes

    refused = dict(report.refused)
    kept: list[dict[str, Any]] = []
    with open_catalog(project) as catalog:
        context = DecisionContext(catalog, project)
        for planned in report.planned:
            word = planned.get("word")
            rule = str(planned.get("target", "")).split(":")[1:2]
            offered = class_takes(context.predicate(rule[0]) if rule else None)
            if word and word not in offered:
                why = f"the word is not one this group's rule offers ({', '.join(offered)})"
                refused[why] = refused.get(why, 0) + len(planned.get("rows") or [1])
                continue
            kept.append(planned)
    return {
        "sheet": name,
        "kind": report.kind,
        "rows": report.rows,
        "blank": report.blank,
        "already": report.already,
        "refused": refused,
        "examples": dict(report.examples),
        "planned": kept,
    }


def _settings_import(project: Path, document: object) -> dict[str, Any]:
    """Each value a settings file sets, beside the value in force; refused in words where not.

    Writes nothing: the page takes the changes as drafts through the draft route, and a person
    commits them as any other.
    """
    from pydantic import ValidationError

    from ingence.core.settings import Settings
    from ingence.workspace.settings_overlay import flatten, merged, read_layers, unflatten

    if not isinstance(document, str) or not document.strip():
        message = "the file is empty: a settings file names settings and their values"
        raise ValueError(message)
    try:
        # References are refused before anything is built: a file of 600 bytes that repeats
        # itself through anchors and aliases can expand to a million rows.
        for event in yaml.parse(document, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                message = (
                    "a settings file names each setting once; this one repeats itself through "
                    "references (& and *)"
                )
                raise ValueError(message)
        loaded = yaml.safe_load(document)
    except yaml.YAMLError as error:
        why = str(error).splitlines()[0] if str(error) else type(error).__name__
        message = f"the file cannot be read as a settings file ({why})"
        raise ValueError(message) from error
    if not isinstance(loaded, dict) or not loaded:
        message = "the file holds no settings: a settings file names settings and their values"
        raise ValueError(message)
    layers = read_layers(project)
    defaults = flatten(Settings().model_dump(mode="json"))
    in_force = flatten(Settings.model_validate(layers.effective).model_dump(mode="json"))
    changes: list[dict[str, Any]] = []
    refused: list[dict[str, str]] = []
    for name, value in sorted(flatten(loaded).items()):
        if name == "schema_version":
            continue
        if name not in defaults:
            section = any(known.startswith(f"{name}.") for known in defaults)
            refused.append(
                {
                    "name": name,
                    "why": f"{name} is a section: name its settings, like "
                    f"{next(known for known in sorted(defaults) if known.startswith(name + '.'))}"
                    if section
                    else "there is no setting of this name",
                }
            )
            continue
        if not _shape(name)["editable"]:
            refused.append({"name": name, "why": "this setting cannot be changed"})
            continue
        try:
            checked = flatten(
                Settings.model_validate(
                    merged(layers.effective, unflatten({name: value}))
                ).model_dump(mode="json")
            )[name]
        except ValidationError as error:
            said = "; ".join(_plain_problem(problem) for problem in error.errors())
            refused.append({"name": name, "why": f"not a value it takes: {said}"})
            continue
        changes.append(
            {
                "name": name,
                "now": in_force[name],
                "imported": checked,
                "same": checked == in_force[name],
                "default": defaults[name],
            }
        )
    return {
        "changes": changes,
        "refused": refused[:_REFUSALS_LISTED],
        "refused_more": max(0, len(refused) - _REFUSALS_LISTED),
    }


#: The most refusals an import lists. The rest are counted.
_REFUSALS_LISTED = 100


def _plain_problem(problem: Any) -> str:
    """The settings model's refusal in a person's words."""
    kind = str(problem.get("type", ""))
    said = str(problem.get("msg", ""))
    plain = {
        "tuple_type": "it should be a list, like [a, b]",
        "list_type": "it should be a list, like [a, b]",
        "int_type": "it should be a whole number",
        "int_parsing": "it should be a whole number",
        "int_from_float": "it should be a whole number",
        "float_type": "it should be a number",
        "float_parsing": "it should be a number",
        "bool_type": "it should be yes or no (true or false)",
        "bool_parsing": "it should be yes or no (true or false)",
        "string_type": "it should be words",
    }
    if kind in plain:
        return plain[kind]
    return said[0].lower() + said[1:] if said else kind


def read(project: Path, parts: list[str], *, applier: Any = None) -> dict[str, Any]:
    """``GET /api/workspace/<parts>``."""
    from ingence.workspace.drafts import read_draft
    from ingence.workspace.rerun import needs_of_settings
    from ingence.workspace.settings_overlay import flatten
    from ingence.workspace.state import state

    if parts == ["state"]:
        return {**state(project), "applying": applier.status() if applier is not None else None}
    if parts == ["settings"]:
        return {"settings": _settings_rows(project)}
    if parts[:1] == ["settings-file"]:
        return _settings_file(project, parts[1:])
    if parts == ["settings-export"]:
        return _settings_export(project)
    if parts == ["export", "groupings"]:
        return _groupings_export(project)
    if parts == ["export", "history"]:
        return _history_export(project)
    if parts == ["commit-preview"]:
        settings = flatten(read_draft(project, "settings").values)
        steps = list(needs_of_settings(list(settings)))
        if read_draft(project, "rule_parameters").values and Step.INFERENCE not in steps:
            steps.append(Step.INFERENCE)
        return {
            "needs": [step.words for step in steps],
            "steps": [step.value for step in steps],
        }
    message = f"nothing at /api/workspace/{'/'.join(parts)}"
    raise LookupError(message)


def census_only(project: Path) -> bool:
    """Whether the project's current run was read and never inferred: it holds entries and no
    rule has been evaluated in it (the engine's own test, ``textword/questions._not_yet_asked``)."""
    from ingence.storage.catalog import open_catalog
    from ingence.storage.commit import read_pointer

    pointer = read_pointer(project)
    if pointer is None:
        return False
    with open_catalog(project, run_id=pointer.run_id) as catalog:
        ruled = catalog.query("SELECT count(*) FROM rules").fetchone()
        held = catalog.query("SELECT count(*) FROM entries").fetchone()
    return bool(held and held[0]) and not (ruled and ruled[0])


def write(
    project: Path, route: str, body: dict[str, Any], *, applier: Any = None
) -> dict[str, Any]:
    """``POST`` to one of the workspace routes; refusals raise ``ValueError`` or ``LookupError``."""
    from ingence.workspace.checkpoints import commit
    from ingence.workspace.drafts import save_draft
    from ingence.workspace.state import pending_rerun

    if route.endswith("/draft"):
        values = body.get("values", {})
        if not isinstance(values, dict):
            message = "a draft states its values as an object"
            raise ValueError(message)
        # What a person put back as it was leaves the draft, and nothing else does (a save adds).
        removed = body.get("removed", [])
        if not isinstance(removed, list) or not all(
            isinstance(place, str)
            or (isinstance(place, list) and all(isinstance(key, str) for key in place))
            for place in removed
        ):
            message = "a draft names what it takes out as a list of setting names"
            raise ValueError(message)
        saved = save_draft(project, str(body.get("kind") or ""), values, removed=removed)
        return {"kind": saved.kind, "errors": saved.errors}
    if route.endswith("/settings-import"):
        return _settings_import(project, body.get("document"))
    if route.endswith("/sheet-preview"):
        return _sheet_preview(project, body)
    if route.endswith("/restore"):
        # Going back to an earlier run: the exact preview the person confirmed, by its digest,
        # written by a person and never by the engine (workspace/restore.py). It runs under the
        # project's publish lock. A restore moves the pointer, and a job or a hand-run command
        # that published meanwhile would publish over it.
        from ingence.jobs.locks import ProjectBusyError, publishing
        from ingence.workspace.restore import RestoreRefusedError, restore

        try:
            with publishing(project, command="a restore from the Dashboard", wait=False):
                return restore(
                    project,
                    run_id=str(body.get("run") or ""),
                    actor=str(body.get("actor") or ""),
                    previewed=str(body.get("previewed") or ""),
                )
        except ProjectBusyError as error:
            message = (
                f"{error.said} A restore now would be published over by it: restore once it has "
                "finished."
            )
            raise RestoreRefusedError(message) from error
    if route.endswith("/commit"):
        checkpoint = commit(
            project,
            actor=str(body.get("actor") or ""),
            message=str(body.get("message") or "Changes"),
        )
        return {"checkpoint": asdict(checkpoint)}
    if route.endswith("/infer"):
        # "Run the rules now": a project that was read (``ingence index``) and never inferred
        # shows "Read, not yet inferred" with this as its action. Only a person's click asks for
        # it, and it reads the project alone, never a root.
        # - Only while no rule has run in the current run: after that, what changed is Re-run's.
        # - Never a second job: while one is queued or running (a double click, a reload) the
        #   answer is the applier's status and nothing is queued, because the ledger joins a job
        #   only while it is still queued.
        # - A job that a close left stopped is started by this ask, as Re-run starts it (only a
        #   person's ask may). Otherwise it would count as busy and nothing would ever start.
        # - Where changes already wait for Re-run and Re-run would start something, this does
        #   Re-run's work, in one job. Where the only thing waiting needs the folders read again,
        #   Re-run would start nothing: the rules are queued over the census the project already
        #   has, and the commands for the rest are still passed on.
        if applier is None:
            message = "no job can be started here"
            raise ValueError(message)
        if not census_only(project):
            message = (
                "the rules have run in this project already; Re-run applies what changed since"
            )
            raise ValueError(message)
        if applier.stopped():
            return {"started": [], "applying": applier.resume()}
        if applier.job_busy():
            return {
                "started": [],
                "applying": applier.status(),
                "note": "the rules are being applied already",
            }
        waiting = pending_rerun(project)
        if waiting is None:
            return {"started": ["inference"], "applying": applier.submit_rerun(["inference"])}
        asked, _words = waiting.request()
        if asked:
            return write(project, "/api/workspace/rerun", {}, applier=applier)
        over_the_census: dict[str, Any] = {
            "started": ["inference"],
            "applying": applier.submit_rerun(["inference"]),
        }
        share = waiting.needing_the_share()
        if share:
            over_the_census["needs_the_share"] = [
                step.value for step in share if step.reads_the_share
            ]
            over_the_census["commands"] = [step.command for step in share]
        return over_the_census
    if route.endswith("/rerun"):
        waiting = pending_rerun(project)
        if waiting is None:
            # A re-run that a close stopped, whose changes another run has since applied, is
            # started as asked, since only a person's Re-run starts it.
            if applier is not None and applier.stopped():
                return {"started": [], "applying": applier.resume()}
            return {"started": [], "note": "nothing is waiting for a re-run"}
        share = waiting.needing_the_share()
        said: dict[str, Any] = {"started": []}
        if share:
            # A walk or a dive reads the share: a person starts it, and is told first.
            said["needs_the_share"] = [step.value for step in share if step.reads_the_share]
            said["commands"] = [step.command for step in share]
        asked, words = waiting.request()
        if not asked:
            # A re-run that a close stopped starts whatever else needs the share. Only a
            # person's Re-run starts it, and this is one.
            if applier is not None and applier.stopped():
                said["applying"] = applier.resume()
            return said
        if applier is None:
            return {**said, "commands": [step.command for step in waiting.steps]}
        # What reads only the project runs in one job, whatever else waits: the names that hold
        # the changed words, or every name where anything else changed, then the rules.
        said["started"] = [step.value for step in waiting.applies()]
        said["applying"] = applier.submit_rerun(asked, spellings=words)
        return said
    message = f"nothing at {route} accepts a write"
    raise LookupError(message)
