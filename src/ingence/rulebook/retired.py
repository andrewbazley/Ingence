"""The rules file that a project held before those rules became the engine's own.

Earlier versions kept the rules developed after the book's first four revisions in a project's own
``rulebook`` folder, as ``01-revision-4.yaml``. The file was written by hand, by the tests, and
later by ``init``. The rules are the engine's own now (revision 4 of the book, declared in
:mod:`ingence.rulebook.revision_4`), so a project that still holds the file would declare them
twice, which composition refuses. The change that made the rules the engine's own is called
the fold in this module.

The loader recognizes the file and skips it whole, by its name, its number (project revision 1) and
what it declares. The declared rules are mostly rules that some version of the file declared. The
loader reads them from the history frozen at the fold and never from today's book, which later
revisions change. A file of that name that holds mostly a person's own rules is their revision and
loads as one.

What a person chose in the file is kept, as the project's own rules. This covers a value that no
version of the file gave a parameter, a change that no version made (a rule switched off, say), a
rule switched off by ``enabled: false``, and a rule of their own. Every other difference between an
old file and today's rules is the rules' own development, which the fold brings to every project.
What a person edited that a project's rules cannot hold (a rule's declaration, the deferral's
exceptions) is said and not applied. ``retired_history.yaml`` holds what every version held. It is
read from every version of the declaration that git holds. The choices kept compose as project
revision 1, the file's own number, which no later rule change takes (:func:`taken`). So a later
change still applies after them.

Nothing is deleted or rewritten. ``ingence fix --set-aside-old-rules`` moves the file into
``rulebook/retired/`` and writes the choices kept as ``project-0001.yaml``. Nothing else writes.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ingence.core.ids import PROJECT_SCOPE, canonical_json, digest_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from ingence.core.ids import Scope
    from ingence.core.schema import Rule
    from ingence.rulebook.load import ProjectRevision

__all__ = [
    "FILE_NAME",
    "HISTORY_FILE",
    "SET_ASIDE_DIRECTORY",
    "SHIPPED_BEFORE",
    "History",
    "RetiredRules",
    "history",
    "kept_revision",
    "read_retired",
    "recognised",
    "sentence",
    "set_aside",
    "shape_of",
    "taken",
]

#: The file's name in a project's ``rulebook`` folder.
FILE_NAME = "01-revision-4.yaml"

#: Where ``ingence fix --set-aside-old-rules`` moves it, inside the ``rulebook`` folder: a folder
#: the Rulebook never reads, since it reads only the ``rulebook`` folder's own files.
SET_ASIDE_DIRECTORY = "retired"

#: The number the file gave itself, and the one the choices kept from it compose at.
_NUMBER = 1

#: The last shipped revision before the fold: the file composed after it, and a run made before
#: the fold is rebuilt on it (``workspace/restore.py``).
SHIPPED_BEFORE = 3

#: What every version of the file held, frozen at the fold (``retired_history.yaml``): the
#: rules it declared, the changes it made, the exceptions of its deferral, each rule's shape (its
#: declaration less its words and its values, as a digest), and every value it gave a parameter,
#: as canonical JSON. Recognition reads this and never today's rules, so a later revision that
#: renames or removes a rule leaves every old file recognized; and a value or a shape found here is
#: the rules' own history, never a person's choice. Complete by construction: no version of the
#: file is written after the fold.
HISTORY_FILE = Path(__file__).parent / "retired_history.yaml"

#: A declaration's fields that are neither words nor values: what a rule is, as a shape.
_SHAPE_FIELDS = (
    "version",
    "predicate",
    "effect",
    "tier",
    "level",
    "defeated_by",
    "primitive",
    "respect",
    "specialization",
    "candidates_within",
    "composition",
    "status",
)
_PARAMETER_SHAPE_FIELDS = ("name", "type", "choices", "minimum", "maximum")
#: Hexadecimal digits written as letters, so a stored digest holds no run of digits.
_LETTERS = str.maketrans("0123456789", "ghijklmnop")


def shape_of(declaration: dict[str, Any]) -> str:
    """A rule declaration's shape, as a short digest written in letters."""
    shape = {field: declaration[field] for field in _SHAPE_FIELDS if field in declaration}
    shape["parameters"] = [
        {field: parameter[field] for field in _PARAMETER_SHAPE_FIELDS if field in parameter}
        for parameter in declaration.get("parameters") or ()
        if isinstance(parameter, dict)
    ]
    return digest_text(canonical_json(shape))[:16].translate(_LETTERS)


@dataclass(frozen=True, slots=True)
class History:
    """What every version of the retired file held (``retired_history.yaml``)."""

    rules: frozenset[str]
    changes: frozenset[str]
    exceptions: frozenset[str]
    shapes: dict[str, frozenset[str]]
    values: dict[tuple[str, str], frozenset[str]]


@functools.cache
def history() -> History:
    """What every version of the retired file held, read once."""
    document = yaml.safe_load(HISTORY_FILE.read_text(encoding="utf-8")) or {}
    return History(
        rules=frozenset(str(rule_id) for rule_id in document.get("rules") or ()),
        changes=frozenset(str(change) for change in document.get("changes") or ()),
        exceptions=frozenset(str(listed) for listed in document.get("exceptions") or ()),
        shapes={
            str(rule_id): frozenset(str(shape) for shape in shapes)
            for rule_id, shapes in (document.get("shapes") or {}).items()
        },
        values={
            (str(rule_id), str(name)): frozenset(str(value) for value in values)
            for rule_id, parameters in (document.get("values") or {}).items()
            for name, values in parameters.items()
        },
    )


@dataclass(frozen=True, slots=True)
class RetiredRules:
    """The retired file a project holds, and what of it is the project's own."""

    path: Path
    #: Values a person chose: rule -> parameter -> value.
    kept: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Changes a person made that no version of the file held: ``{"disable": rule}`` and the like.
    changes: tuple[dict[str, str], ...] = ()
    #: Rules a person declared that no version of the file did, as they declared them.
    rules: tuple[dict[str, Any], ...] = ()
    #: What the file holds of a person's that today's rules cannot take (a parameter gone, a value
    #: refused, a rule's declaration or the deferral's exceptions edited): said, never applied.
    not_kept: tuple[str, ...] = ()

    @property
    def holds_choices(self) -> bool:
        """Whether anything in the file is a person's that still applies."""
        return bool(self.kept or self.changes or self.rules)

    def document(self) -> dict[str, Any]:
        """The project revision the choices compose as: the file's own number, 1."""
        return {
            PROJECT_REVISION_KEY: _NUMBER,
            "summary": f"Your project's own choices, kept from {FILE_NAME}",
            "changes": [
                *(
                    {"override": rule_id, "parameters": parameters}
                    for rule_id, parameters in self.kept.items()
                ),
                *self.changes,
            ],
            "rules": list(self.rules),
        }


#: Mirrors ``ingence.rulebook.load.PROJECT_REVISION_KEY`` without importing the loader here.
PROJECT_REVISION_KEY = "project_revision"
_VERBS = ("supersede", "disable", "enable", "override")


@functools.cache
def _book() -> dict[str, Rule]:
    """The rules the shipped book has in force."""
    from ingence.rulebook import shipped_rulebook

    return {rule.rule_id: rule for rule in shipped_rulebook().rules}


def recognised(name: str, document: object) -> bool:
    """Whether a rulebook file is the retired one: its name, its number, half its rules or more.

    Read against what any version of the file declared (:func:`history`), never against today's
    book, so a rule a later revision renames or removes leaves the file recognized. A file of that
    name whose rules are mostly a person's own is their revision, and loads as one; a tie is the old
    file, so a person's one rule beside one former rule is kept as theirs, never refused.
    """
    if name != FILE_NAME or not isinstance(document, dict):
        return False
    if "revision" in document or document.get(PROJECT_REVISION_KEY) != _NUMBER:
        return False
    rules = document.get("rules")
    changes = document.get("changes") or []
    if not isinstance(rules, list) or not rules or not isinstance(changes, list):
        return False
    if not all(isinstance(rule, dict) for rule in rules):
        return False
    if not all(isinstance(change, dict) for change in changes):
        return False
    known = history().rules
    former = sum(1 for rule in rules if str(rule.get("id", "")) in known)
    return former >= len(rules) - former


def read_retired(directory: Path) -> RetiredRules | None:
    """The retired file in a ``rulebook`` folder, with the choices kept from it; None where none."""
    path = directory / FILE_NAME
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not recognised(path.name, document):
        return None
    return _choices(path, document)


def _held(document: dict[str, Any], known: frozenset[str]) -> list[tuple[str, str, object]]:
    """Each value the file gives a parameter of a rule a version declared, and each override's."""
    held: list[tuple[str, str, object]] = []
    for rule in document.get("rules") or ():
        if str(rule.get("id")) not in known:
            continue
        for parameter in rule.get("parameters") or ():
            if isinstance(parameter, dict) and "name" in parameter:
                held.append((str(rule["id"]), str(parameter["name"]), parameter.get("default")))
    for change in document.get("changes") or ():
        values = change.get("parameters")
        if "override" in change and isinstance(values, dict):
            rule_id = str(change["override"])
            held.extend((rule_id, str(name), value) for name, value in values.items())
    return held


def _choices(path: Path, document: dict[str, Any]) -> RetiredRules:
    """What in the file is a person's: what still applies, and what is said and not applied."""
    from ingence.rulebook.load import RuleDeclarationError, refuse_a_value_its_parameter_refuses

    in_force = _book()
    past = history()
    kept: dict[str, dict[str, Any]] = {}
    not_kept: list[str] = []
    for rule_id, name, value in _held(document, past.rules):
        rule = in_force.get(rule_id)
        written = canonical_json(value)
        if rule is None or written in past.values.get((rule_id, name), frozenset()):
            continue  # the rules' own history, or a rule nothing of a person's rests on now
        specification = next((spec for spec in rule.parameters if spec.name == name), None)
        if specification is None:
            not_kept.append(f"{rule_id}: {name}")
            continue
        try:
            refuse_a_value_its_parameter_refuses(specification, value, where=rule_id)
        except RuleDeclarationError:
            not_kept.append(f"{rule_id}: {name}")
            continue
        kept.setdefault(rule_id, {})[name] = value
    changes: list[dict[str, str]] = []
    for change in document.get("changes") or ():
        if canonical_json(change) in past.changes:
            continue
        verbs = [verb for verb in _VERBS if verb in change]
        if len(verbs) != 1 or verbs[0] == "override":
            continue  # an override's values are read above
        target = str(change[verbs[0]])
        if target in in_force:
            changes.append({verbs[0]: target})
        else:
            not_kept.append(f"{target}: {verbs[0]}")
    rules: list[dict[str, Any]] = []
    for rule in document.get("rules") or ():
        rule_id = str(rule.get("id", ""))
        if rule_id not in past.rules:
            rules.append(rule)  # a rule of the person's own
            continue
        if rule.get("enabled") is False:
            if rule_id in in_force:
                changes.append({"disable": rule_id})
            else:
                not_kept.append(f"{rule_id}: switched off")
        if shape_of(rule) not in past.shapes.get(rule_id, frozenset()):
            not_kept.append(f"{rule_id}: how it is declared")
    for deferral in document.get("defer_beneath") or ():
        if not isinstance(deferral, dict):
            continue
        listed = {"scope": deferral.get("scope"), "except": sorted(deferral.get("except") or [])}
        if canonical_json(listed) not in past.exceptions:
            not_kept.append("the rules a program's folder does not defer")
    disabled = {change.get("disable") for change in changes}
    for rule_id in sorted(set(kept) & disabled):
        # One change to a rule in one revision: switched off, its values wait.
        del kept[rule_id]
        not_kept.append(f"{rule_id}: its settings, while it is switched off")
    return RetiredRules(
        path=path,
        kept=dict(sorted(kept.items())),
        changes=tuple(changes),
        rules=tuple(rules),
        not_kept=tuple(sorted(set(not_kept))),
    )


def kept_revision(retired: RetiredRules, *, scope: Scope = PROJECT_SCOPE) -> ProjectRevision:
    """The choices kept, as the project revision the file's own number names."""
    from ingence.rulebook.load import project_revision_from

    return project_revision_from(retired.document(), name=retired.path.name, scope=scope)


def _value_words(value: object, typed: Callable[[str], str] | None = None) -> str:
    if typed is not None and value is not None and not isinstance(value, bool | int | float):
        return typed(value if isinstance(value, str) else canonical_json(value))
    if isinstance(value, str):
        return value.replace("_", " ")
    return canonical_json(value)


_CHANGE_WORDS = {"disable": "switched off", "enable": "switched on", "supersede": "replaced"}


def sentence(retired: RetiredRules, *, typed: Callable[[str], str] | None = None) -> str:
    """What a person reads where the Rulebook loads: once, in plain words.

    With ``typed`` (a shareable report, ``ingence fix --shareable``) whatever a person typed into
    the file is written by it: a text value, a list, the name of a rule of their own. The rules'
    names, the names of their parameters and the numbers and switches chosen stay, and what could
    not be applied is only counted.
    """
    from ingence.storage.layout import RULEBOOK_DIR

    said = (
        f"{RULEBOOK_DIR}/{retired.path.name} holds rules that are now part of Ingence, "
        "so it is no longer read"
    )
    choices = [
        f"{rule_id} {name}: {_value_words(value, _typed_for(typed, rule_id, name))}"
        for rule_id, parameters in retired.kept.items()
        for name, value in parameters.items()
    ]
    choices += [
        f"{target} {_CHANGE_WORDS[verb]}"
        for change in retired.changes
        for verb, target in change.items()
    ]
    choices += [
        f"your own rule {rule.get('id') if typed is None else typed(str(rule.get('id')))}"
        for rule in retired.rules
    ]
    if choices:
        said += f". Your choices in it still apply ({'; '.join(choices)})"
    if retired.not_kept:
        not_applied = (
            ", ".join(retired.not_kept) if typed is None else _plural_items(len(retired.not_kept))
        )
        said += f". Not applied, because the engine's rules cannot take them: {not_applied}"
    return said


def _typed_for(
    typed: Callable[[str], str] | None, rule_id: str, name: str
) -> Callable[[str], str] | None:
    """``typed`` for a parameter that takes text a person writes, none for one that does not."""
    return None if typed is None or _from_a_list(rule_id, name) else typed


def _from_a_list(rule_id: str, name: str) -> bool:
    """Whether a rule's parameter takes a number, a switch or one of the choices it declares.

    Its value is then the engine's own word, whoever chose it.
    """
    from ingence.core.schema import ParameterType

    rule = _book().get(rule_id)
    specification = (
        next((spec for spec in rule.parameters if spec.name == name), None) if rule else None
    )
    return specification is not None and specification.type not in {
        ParameterType.STRING,
        ParameterType.STRING_LIST,
        ParameterType.MAPPING,
    }


def _plural_items(number: int) -> str:
    return f"{number} item{'' if number == 1 else 's'}"


def set_aside(project: Path) -> tuple[Path, int | None] | None:
    """Move the retired file into ``rulebook/retired/``; write the choices kept, if any.

    Returns where the file went and the number of the project revision written (None where
    nothing was kept), or None where the project holds no retired file. Never deletes: a file
    already set aside is never overwritten, and the move is refused instead.
    """
    from ingence.storage.layout import RULEBOOK_DIR, owned_path

    directory = project / RULEBOOK_DIR
    retired = read_retired(directory)
    if retired is None:
        return None
    # Both the project's own, nothing on the way to either a link, before the file is moved.
    destination = owned_path(project, RULEBOOK_DIR, SET_ASIDE_DIRECTORY, FILE_NAME)
    if destination.exists():
        message = f"{destination} already exists; nothing was moved"
        raise FileExistsError(message)
    destination.parent.mkdir(parents=True, exist_ok=True)
    retired.path.rename(owned_path(project, destination))
    if not retired.holds_choices:
        return destination, None
    target = directory / f"project-{_NUMBER:04d}.yaml"
    try:
        # Created exclusively, as a commit's revision is: never over a file of the same name.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(retired.document(), handle, sort_keys=False)
    except OSError:
        # The choices could not be written: the file goes back, and its choices keep applying.
        destination.rename(owned_path(project, retired.path))
        raise
    return destination, _NUMBER


def taken(directory: Path) -> bool:
    """Whether the retired file's number is taken in a ``rulebook`` folder.

    It is taken while the file stands there or has been set aside. A run made before the fold read
    the file as project revision 1, so a rule change committed after the fold never takes that
    number, and a restore across the fold reads "revision 1 or below" as the file alone.
    """
    return (directory / FILE_NAME).is_file() or (
        directory / SET_ASIDE_DIRECTORY / FILE_NAME
    ).is_file()
