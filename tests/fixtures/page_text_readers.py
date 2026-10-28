"""Readers the page-text tests share: the page scripts, strings in JavaScript, and a JSON walk."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.api.server import WEB_ROOT

if TYPE_CHECKING:
    from collections.abc import Iterator

#: What is the page's own code, as opposed to a library it carries or a build it was made by.
FOREIGN = {"vendor", "explorer", "types"}


def _own_scripts() -> list[Path]:
    return sorted(
        path
        for path in WEB_ROOT.rglob("*.js")
        if not FOREIGN & set(path.relative_to(WEB_ROOT).parts)
    )


# ---------------------------------------------------------------------- reading JavaScript

#: After one of these a ``/`` begins a regular expression rather than dividing.
_BEFORE_REGEX = frozenset("(,=:[!&|?{};+-*%<>~^")
_WORDS_BEFORE_REGEX = frozenset(
    {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do"}
)


def string_literals(source: str) -> list[tuple[int, str]]:
    """Every string in a JavaScript source as ``(line, text)``.

    That covers quoted strings, the text parts of template literals, and the strings inside their
    ``${...}``. Comments and regular expressions are skipped, because only a string can reach the
    page. Escapes are left as written. This is not a full parser. It only has to know whether a
    quote begins a string or sits inside a comment or a regular expression.
    """
    found: list[tuple[int, str]] = []
    length = len(source)
    state = {"last": ""}  # the last significant word or character, to tell "/" apart

    def regex_may_start() -> bool:
        last = state["last"]
        return not last or last in _BEFORE_REGEX or last in _WORDS_BEFORE_REGEX

    def template(at: int, line: int) -> tuple[int, int]:
        begun = line
        buffer: list[str] = []
        at += 1
        while at < length:
            char = source[at]
            if char == "\\":
                buffer.append(source[at : at + 2])
                at += 2
                continue
            if char == "\n":
                line += 1
            if char == "`":
                found.append((begun, "".join(buffer)))
                return at + 1, line
            if char == "$" and source[at + 1 : at + 2] == "{":
                found.append((begun, "".join(buffer)))
                buffer = []
                at, line = code(at + 2, line, nested=True)
                continue
            buffer.append(char)
            at += 1
        return at, line

    def code(at: int, line: int, nested: bool = False) -> tuple[int, int]:
        depth = 0
        while at < length:
            char = source[at]
            if char == "\n":
                line += 1
                at += 1
            elif char in " \t\r":
                at += 1
            elif source.startswith("//", at):
                while at < length and source[at] != "\n":
                    at += 1
            elif source.startswith("/*", at):
                end = source.find("*/", at + 2)
                end = length if end < 0 else end + 2
                line += source.count("\n", at, end)
                at = end
            elif char in "'\"":
                end = at + 1
                buffer: list[str] = []
                while end < length and source[end] != char:
                    if source[end] == "\\":
                        buffer.append(source[end : end + 2])
                        end += 2
                        continue
                    buffer.append(source[end])
                    end += 1
                found.append((line, "".join(buffer)))
                at = end + 1
                state["last"] = "string"
            elif char == "`":
                at, line = template(at, line)
                state["last"] = "string"
            elif char == "/" and regex_may_start():
                end = at + 1
                inside_class = False
                while end < length:
                    here = source[end]
                    if here == "\\":
                        end += 2
                        continue
                    if here == "[":
                        inside_class = True
                    elif here == "]":
                        inside_class = False
                    elif (here == "/" and not inside_class) or here == "\n":
                        break
                    end += 1
                at = end + 1
                while at < length and source[at].isalpha():
                    at += 1
                state["last"] = "regex"
            elif char.isalnum() or char in "_$":
                end = at
                while end < length and (source[end].isalnum() or source[end] in "_$"):
                    end += 1
                state["last"] = source[at:end]
                at = end
            else:
                if char == "{":
                    depth += 1
                elif char == "}":
                    if nested and depth == 0:
                        return at + 1, line
                    depth -= 1
                state["last"] = char
                at += 1
        return at, line

    code(0, 1)
    return found


def _walk(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")
