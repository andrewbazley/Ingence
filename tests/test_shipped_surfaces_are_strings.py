"""Every spelling a shipped Dictionary file declares loads as a string.

YAML 1.1 reads an unquoted ``on``, ``off``, ``yes``, ``no``, ``true`` or ``false`` as a boolean and
an unquoted number as a number, so a surface written that way is not the spelling its author typed.
For example, overnight's ``on`` would load as ``True``, and a name holding the word "true" would be
read as overnight. A surface that is not a string fails here, in the core and in every shipped pack,
before it can bind anything.

The check covers every list of words, not only the surfaces. The quality marks'
``negated_by: [not, no]`` would load ``no`` as False, so "no good" would never cancel the mark.
Every scalar inside a list a shipped Dictionary file holds is a word a reader matches, and each is
checked here: surfaces, ``negated_by``, and whatever list a later section adds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import ingence.dictionary as dictionary_package

ROOT = Path(dictionary_package.__file__).resolve().parent
SHIPPED = sorted(
    [*ROOT.joinpath("builtin").rglob("*.yaml"), *ROOT.joinpath("packs").rglob("*.yaml")]
)


def _surfaces(node: object, where: str):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "surfaces" and isinstance(value, list):
                for surface in value:
                    yield where, surface
            else:
                yield from _surfaces(value, where)
    elif isinstance(node, list):
        for item in node:
            yield from _surfaces(item, where)


def _listed(node: object, where: str):
    """Every scalar inside any list, anywhere in a document, with the key it is listed under."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, (dict, list)):
                        yield from _listed(item, where)
                    else:
                        yield f"{where}:{key}", item
            else:
                yield from _listed(value, where)
    elif isinstance(node, list):
        for item in node:
            yield from _listed(item, where)


def test_the_shipped_dictionary_files_are_found() -> None:
    assert len(SHIPPED) > 10


@pytest.mark.parametrize("path", SHIPPED, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_every_shipped_surface_is_a_string(path: Path) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    wrong = [
        (where, surface)
        for where, surface in _surfaces(document, path.name)
        if not isinstance(surface, str)
    ]
    assert not wrong, f"surfaces that YAML did not load as the spelling written: {wrong}"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_every_word_a_shipped_list_holds_is_a_string(path: Path) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    wrong = [
        (where, word) for where, word in _listed(document, path.name) if not isinstance(word, str)
    ]
    assert not wrong, f"listed words that YAML did not load as the word written: {wrong}"
