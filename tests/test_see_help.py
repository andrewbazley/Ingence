"""``ingence see --help`` says, in plain words, what the command opens, where, how to stop it, and
what its two options do.

The help is read from the command line's own command tree, so a rewording that drops one of these
facts fails here. The words are checked for the facts a person needs, not for their exact wording.
"""

from __future__ import annotations

import re
from typing import Any

import typer

from ingence.cli.main import app


def _see() -> Any:
    root = typer.main.get_command(app)
    return root.commands["see"]  # type: ignore[attr-defined]


def _prose() -> str:
    """The command's help as one line of words."""
    return " ".join(str(_see().help).split())


def _option_help(name: str) -> str:
    (found,) = [param for param in _see().params if name in getattr(param, "opts", [])]
    return " ".join(str(found.help).split())


def test_the_help_says_what_it_opens_and_where() -> None:
    said = _prose()
    assert said.startswith("Open the Dashboard in your web browser.")
    assert "http://127.0.0.1:" in said, "the address a person will see is not shown"
    assert "Only this computer can open it" in said
    assert "no internet" in said


def test_the_help_says_which_project_it_opens() -> None:
    said = _prose()
    assert "--project" in said
    assert "this folder" in said and "nearest folder above it" in said
    assert "make a new one" in said


def test_the_help_says_how_to_stop_it() -> None:
    said = _prose()
    assert "Ctrl-C" in said
    assert "Closing the browser tab does not stop it" in said


def test_the_help_says_what_it_writes_and_that_it_works_unmounted() -> None:
    said = _prose()
    assert "Ingence never changes your files" in said
    assert "answer log in the project folder" in said
    assert "list of recent projects and your name" in said and "settings folder" in said
    assert "Re-run" in said
    assert "works when your data folders are not connected" in said


def test_each_option_says_what_it_does() -> None:
    assert "this folder" in _option_help("--project")
    assert "free port" in _option_help("--port") and "0" in _option_help("--port")
    assert "only print its address" in _option_help("--open")


def test_the_help_has_no_decision_numbers_or_session_numbers() -> None:
    whole = _prose() + " " + _option_help("--project") + _option_help("--port")
    assert not re.search(r"\bD\d{2,4}\b|\bsessions?\s+\d+", whole, flags=re.IGNORECASE)
