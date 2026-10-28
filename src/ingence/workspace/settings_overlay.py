"""The Dashboard's own settings file, laid over the one a person edits by hand.

A commit never rewrites ``settings.yaml``. Whatever a person typed there, comments included, stays
byte for byte as typed. The Dashboard writes ``settings.dashboard.yaml`` beside it, and
``Project.open`` reads the hand-edited file first and lays the Dashboard's file over it. So a value
set on the Parameters page wins, and deleting the Dashboard's file gives every hand-edited value
back.

A header says what the Dashboard file is. Every value carries one comment line above it that says
who set it, when, and the commit's message. A person reading the file needs that provenance, and
this is the one file nobody else edits. PyYAML writes no comments, so this module writes the file
one value at a time, in a form that PyYAML reads back.

Precedence is a shipped default, then ``settings.yaml``, then ``settings.dashboard.yaml``. Both
files together are validated as one ``Settings`` before the Dashboard's file is replaced, so the
pair can never hold a combination that the engine would refuse at the next open.

The file is read back before it replaces the old one. Without these checks, a control character
typed into a text setting would be written as it was, the YAML reader would refuse the file, and
every command would stop. So the writer escapes every character past plain ASCII, a comment line
holds only printable characters, and the text is read back and checked to give the same settings
before anything is replaced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from ingence.core.destinations import write_atomically
from ingence.core.settings import DASHBOARD_SETTINGS_FILE, SETTINGS_FILE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "DASHBOARD_SETTINGS_FILE",
    "Layers",
    "flatten",
    "merged",
    "read_layers",
    "source_of",
    "unflatten",
    "write_dashboard_settings",
]

#: One level of nesting in the file the Dashboard writes.
_INDENT = "  "

_HEADER = """\
# Ingence settings written by the Dashboard.
#
# Values set on the Engine page's Settings and committed, laid over settings.yaml, which the
# Dashboard never edits, so its comments stay yours. A value here wins over the same value in
# settings.yaml; delete this file and settings.yaml's values apply again. Each value has one
# comment line above it: who set it, when, and the commit's message.
"""


def flatten(values: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """``{"scan": {"workers": 8}}`` as ``{"scan.workers": 8}``."""
    flat: dict[str, Any] = {}
    for key, value in values.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(flatten(value, name))
        else:
            flat[name] = value
    return flat


def unflatten(flat: Mapping[str, Any]) -> dict[str, Any]:
    """The inverse of :func:`flatten`."""
    nested: dict[str, Any] = {}
    for dotted, value in flat.items():
        node = nested
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return nested


def merged(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """``over`` laid on ``base``, mapping by mapping; neither is changed."""
    result = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    from ingence.core.project import read_settings_file

    return read_settings_file(path)


@dataclass(frozen=True, slots=True)
class Layers:
    """The two files, and what they amount to."""

    hand: dict[str, Any] = field(default_factory=dict)
    dashboard: dict[str, Any] = field(default_factory=dict)

    @property
    def effective(self) -> dict[str, Any]:
        return merged(self.hand, self.dashboard)


def read_layers(project: Path) -> Layers:
    return Layers(
        hand=_load(project / SETTINGS_FILE),
        dashboard=_load(project / DASHBOARD_SETTINGS_FILE),
    )


def source_of(layers: Layers, dotted: str) -> str:
    """Where a value in force came from: ``dashboard``, ``settings.yaml`` or ``default``."""
    if dotted in flatten(layers.dashboard):
        return "dashboard"
    if dotted in flatten(layers.hand):
        return "settings.yaml"
    return "default"


def _one_line(text: str) -> str:
    """A comment's text on one line of printable characters: YAML refuses a file holding a
    control character anywhere, comments included, and reads some separators as line ends."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())


#: A character past the first 65,536, as JSON escapes it: two escaped halves. Matched only where
#: the backslash starts an escape (it follows an even number of backslashes).
_ESCAPED_HALVES = re.compile(r"(?<!\\)((?:\\\\)*)\\u(d[89ab][0-9a-f]{2})\\u(d[c-f][0-9a-f]{2})")


def _as_one_escape(match: re.Match[str]) -> str:
    high, low = int(match.group(2), 16), int(match.group(3), 16)
    return f"{match.group(1)}\\U{0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00):08x}"


def _flow(value: Any) -> str:
    """A value in YAML's flow style, which reads back as the value itself.

    JSON is YAML's flow style for every value a setting holds, and with ``ensure_ascii`` it
    escapes every character that is not plain printable ASCII, which a YAML double-quoted string
    reads the same way. The one exception is a character past the first 65,536, which JSON writes
    as two escaped halves and YAML would read as two characters, so it is written as YAML's one
    eight-digit escape.
    """
    return _ESCAPED_HALVES.sub(_as_one_escape, json.dumps(value, ensure_ascii=True))


def _emit(node: Mapping[str, Any], notes: Mapping[str, str], path: str, depth: int) -> list[str]:
    lines: list[str] = []
    pad = _INDENT * depth
    for key in sorted(node):
        value, dotted = node[key], f"{path}.{key}" if path else str(key)
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.extend(_emit(value, notes, dotted, depth + 1))
            continue
        if notes.get(dotted):
            lines.append(f"{pad}# {_one_line(notes[dotted])}")
        lines.append(f"{pad}{key}: {_flow(value)}")
    return lines


def _previous_notes(path: Path) -> dict[str, str]:
    """The comment line above each value in the file as it stands, so a later write keeps them."""
    if not path.is_file():
        return {}
    notes: dict[str, str] = {}
    stack: list[str] = []
    comment = ""
    for line in path.read_text("utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            comment = stripped[1:].strip()
            continue
        depth = (len(line) - len(line.lstrip(" "))) // len(_INDENT)
        key, _, rest = stripped.partition(":")
        del stack[depth:]
        if rest.strip():
            if comment:
                notes[".".join([*stack, key])] = comment
        else:
            stack.append(key)
        comment = ""
    return notes


def _refuse_unless_read_back(text: str, hand: Mapping[str, Any], meant: Any) -> None:
    """Refuse a file text that the reader would refuse, or read as other settings than ``meant``.

    Compared as settings in force rather than as text: the reader may give a number as text
    that the settings then read as the number, which is the same setting.
    """
    from pydantic import ValidationError

    from ingence.core.settings import Settings

    try:
        loaded = yaml.safe_load(text)
        read = Settings.model_validate(merged(hand, loaded if isinstance(loaded, dict) else {}))
    except (yaml.YAMLError, ValidationError) as error:
        why = str(error).splitlines()[0]
        message = f"the settings could not be written in a form that reads back ({why})"
        raise ValueError(message) from error
    if read != meant:
        message = "the settings could not be written in a form that reads back as they were set"
        raise ValueError(message)


def _without_the_dashboard(hand: Mapping[str, Any]) -> dict[str, Any]:
    """What applies with no Dashboard line: ``settings.yaml`` over the defaults, by dotted name.

    Read as the model reads it where ``settings.yaml`` is valid on its own. Where ``settings.yaml``
    holds a value that the model refuses and the Dashboard's file overrules, its values are laid
    over the defaults as written. So the comparison never fails a commit that the composed
    settings allow.
    """
    from pydantic import ValidationError

    from ingence.core.settings import Settings

    try:
        return flatten(Settings.model_validate(hand).model_dump(mode="json"))
    except ValidationError:
        return flatten(merged(Settings().model_dump(mode="json"), hand))


def write_dashboard_settings(
    project: Path,
    values: Mapping[str, Any],
    *,
    provenance: Mapping[str, str],
    replace: bool = False,
) -> Layers:
    """Lay ``values`` into the Dashboard file, keeping what it already holds.

    ``replace`` makes ``values`` the whole of the file instead (a restore returning it to what
    it was at an earlier run, keys added since removed).

    ``provenance`` maps each dotted key written now to the line its comment carries ("set by
    Robin on 2031-03-09: Changes of the week"). A value written earlier keeps its own line. The pair
    of files is validated as one ``Settings`` before the Dashboard file is replaced, atomically, and
    the text is read back first. Where it would not give the same settings, nothing is written
    (``ValueError``). ``settings.yaml`` is never opened for writing.

    A value put back to what applies without it is taken out and never written as chosen. What
    applies without the Dashboard's line is ``settings.yaml``'s value, or the default where it sets
    none. A value equal to it is taken out, and one that overrules ``settings.yaml`` is a choice
    and is kept.
    """
    from ingence.core.settings import Settings

    layers = read_layers(project)
    path = project / DASHBOARD_SETTINGS_FILE
    flat = {} if replace else flatten(layers.dashboard)
    flat.update(flatten(values))
    without = _without_the_dashboard(layers.hand)
    shown = flatten(
        Settings.model_validate(merged(layers.hand, unflatten(flat))).model_dump(mode="json")
    )
    for key in flatten(values):
        if key in without and shown.get(key) == without[key]:
            flat.pop(key, None)
    dashboard = unflatten(flat)
    meant = Settings.model_validate(merged(layers.hand, dashboard))

    notes = _previous_notes(path)
    notes.update({key: _one_line(note) for key, note in provenance.items()})
    text = _HEADER + "\n" + "\n".join(_emit(dashboard, notes, "", 0)) + "\n"
    _refuse_unless_read_back(text, layers.hand, meant)
    write_atomically(path, text)
    return Layers(hand=layers.hand, dashboard=dashboard)
