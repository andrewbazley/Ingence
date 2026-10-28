"""The text a reader copies from the reference pages and the Dashboard into their own files.

Each function writes text that the engine itself reads, in the form a person's own file takes:

- a Dictionary word is an entry of a pack file (``entries:``), as ``ingence teach pack install``
  reads it;
- a rule change is an item of ``changes:`` in a project's own revision file, as
  :func:`ingence.rulebook.load.project_revision_from` reads it;
- a setting is a key of the project's ``settings.yaml``, nested as the file nests it.

Every piece of text starts with two spaces and a dash where it is an item of a list, as the
shipped packs write theirs, so it can be pasted under the key that names the list. The website's
reference pages and the Dashboard's Copy buttons (``/api/v1/rules/copy`` and
``/api/v1/dictionary/copy``, ``api/v1/copy_text.py``) call the functions below. So the text a
reader copies from either place is the same text.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.core.schema import Rule
    from ingence.dictionary.store import Sense

__all__ = [
    "WIDTH",
    "joined",
    "revision_file_name",
    "revision_file_text",
    "rule_override_text",
    "rule_switch_off_text",
    "setting_text",
    "word_entry_text",
]

#: How long a line of a copied entry may grow before it continues on the next.
WIDTH = 96

#: A spelling written without quotes in the shipped packs.
_BARE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: Words YAML reads as something other than text.
_NOT_TEXT = frozenset({"y", "n", "yes", "no", "on", "off", "true", "false", "null", "~"})

#: The key a folded extension leaves on a sense. It has done its work by the time a pack is read.
_EXTENDS = "extends"
#: What the engine keeps on a sense a person's answer made, for its own bookkeeping (which rule
#: raised the word, whether the person named a new meaning): not vocabulary, so never copied.
_BOOKKEEPING = frozenset({_EXTENDS, "raised_by", "defined"})
#: The key an entry names the one type of file it is a reading in under.
_FILE_TYPE = "file_type"


def _text(value: str) -> str:
    """A string as YAML text: bare where the shipped packs write it bare, else in quotes."""
    if _BARE.match(value) and value.casefold() not in _NOT_TEXT:
        return value
    return json.dumps(value, ensure_ascii=False)


def _float(value: float) -> str:
    """A number as YAML reads a number: ``1e-06`` is text to YAML, and ``1.0e-06`` is a number."""
    text = repr(value)
    if "e" in text and "." not in text:
        text = text.replace("e", ".0e")
    return text


def _flow(value: Any) -> str:
    """A value as a YAML flow node (a list in brackets, a map in braces, a scalar)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, float):
        return _float(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{_text(str(key))}: {_flow(item)}" for key, item in value.items())
            + "}"
        )
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_flow(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _wrapped(line: str, *, indent: str) -> str:
    """Break a flow line after a comma outside quotes, so no line is longer than :data:`WIDTH`."""
    breaks: list[int] = []
    quoted = escaped = False
    for place, char in enumerate(line):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "," and line[place + 1 : place + 2] == " ":
            breaks.append(place)
    pieces: list[str] = []
    start = 0
    for place, after in zip(breaks, [*breaks[1:], len(line)], strict=True):
        if after - start > WIDTH and place > start:
            pieces.append(line[start : place + 1])
            start = place + 2
    pieces.append(line[start:])
    return ("\n" + indent).join(pieces)


def word_entry_text(sense: Sense) -> str:
    """One Dictionary sense as an entry of a pack file, ready to paste under ``entries:``.

    The entry names the sense's term, its canonical name where that differs, its kind, its
    spellings, and everything else the sense carries (a unit's dimension, a declared ambiguity,
    a spelling that counts only as a file ending), so the pack reads the sense as it ships.
    """
    fields: list[tuple[str, Any]] = [("term", sense.term)]
    if sense.canonical != sense.term:
        fields.append(("canonical", sense.canonical))
    fields.append(("theme_kind", sense.theme_kind))
    fields.append(("surfaces", list(sense.surfaces)))
    if sense.ontology_ids:
        fields.append(("ontology_ids", list(sense.ontology_ids)))
    if sense.scope.kind.value == "file_type":
        fields.append((_FILE_TYPE, str(sense.scope.value)))
    fields.extend(
        (key, value) for key, value in sense.properties.items() if key not in _BOOKKEEPING
    )
    body = ", ".join(f"{key}: {_flow(value)}" for key, value in fields)
    return _wrapped("  - {" + body + "}", indent="     ")


def rule_switch_off_text(rule: Rule) -> str | None:
    """A change that switches a rule off, to paste under ``changes:``, or None where it cannot.

    A provisional rule stays in force until a person confirms it, and the Rulebook refuses a
    change that switches it off.
    """
    if rule.status.value == "provisional":
        return None
    return f"  - disable: {_text(rule.rule_id)}"


def rule_override_text(rule: Rule, values: Mapping[str, Any]) -> str | None:
    """A change that sets a rule's parameters, at their shipped values, to paste under ``changes:``.

    ``values`` are the parameters' values in force by name. None where the rule has no parameter
    to set, or is provisional (the Rulebook refuses a change to it).
    """
    if not values or rule.status.value == "provisional":
        return None
    lines = [f"  - override: {_text(rule.rule_id)}", "    parameters:"]
    lines.extend(f"      {_text(name)}: {_flow(value)}" for name, value in values.items())
    return "\n".join(lines)


def revision_file_name(number: int) -> str:
    """The name a project's own revision file takes, as the Dashboard writes it."""
    return f"project-{number:04d}.yaml"


def revision_file_text(number: int, changes: Sequence[str], *, summary: str) -> str:
    """A whole project revision file, to save in the project's ``rulebook`` folder.

    ``changes`` are the items :func:`rule_switch_off_text` and :func:`rule_override_text` write,
    each already indented to sit under ``changes:``. The number is the file's own, one past every
    number taken (``workspace/revisions.next_number``), because the Rulebook refuses two files
    that declare the same one.
    """
    return "\n".join(
        [f"project_revision: {number}", f"summary: {_text(summary)}", "changes:", *changes, ""]
    )


def setting_text(name: str, value: Any) -> str:
    """A setting at a value as the lines of ``settings.yaml`` that hold it.

    A dotted name is a nested key (``hash.mode`` is ``mode`` inside ``hash``), so it is written
    nested, one key a line.
    """
    parts = name.split(".")
    lines = [f"{'  ' * place}{part}:" for place, part in enumerate(parts[:-1])]
    lines.append(f"{'  ' * (len(parts) - 1)}{parts[-1]}: {_flow(value)}")
    return "\n".join(lines)


def joined(texts: Sequence[str]) -> str:
    """Several copied pieces as one block, a piece to a line or more, in the order given."""
    return "\n".join(texts)
