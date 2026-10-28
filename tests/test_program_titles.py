"""A Programs row says what a folder is in plain words.

A program is "a copy of" something. A kind of package is not: a folder laid out as a Python
package is "a Python package", not "a copy of Python package". The names are the Dictionary's
layouts', never a list in the page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.api.routes.pages import _package_kinds, _plainly, _with_article
from ingence.core.project import Project

if TYPE_CHECKING:
    from pathlib import Path


def test_a_named_program_is_a_copy() -> None:
    assert _plainly(["Fiji"], 12) == "a copy of Fiji, 12 files"


def test_a_kind_of_package_is_said_as_what_it_is() -> None:
    kinds = {"Python package": "a Python package", "R package": "an R package"}
    assert _plainly(["Python package"], 1, kinds) == "a Python package, 1 file"
    assert _plainly(["R package"], 3, kinds) == "an R package, 3 files"


def test_a_program_and_a_package_in_one_folder_are_both_said() -> None:
    kinds = {"Python package": "a Python package"}
    said = _plainly(["Fiji", "Python package"], 9, kinds)
    assert said == "a copy of Fiji and a Python package, 9 files"


def test_articles_follow_the_sound_of_the_first_word() -> None:
    assert _with_article("MATLAB toolbox") == "a MATLAB toolbox"
    assert _with_article("Java archive") == "a Java archive"
    assert _with_article("R package") == "an R package"
    assert _with_article("Anaconda environment") == "an Anaconda environment"


def test_the_kinds_are_the_layouts_the_dictionary_declares(tmp_path: Path) -> None:
    (tmp_path / "pool").mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[tmp_path / "pool"])
    kinds = _package_kinds(project.path)
    assert kinds["Python package"] == "a Python package"
    assert kinds["published code"] == "published code"
    assert "Fiji" not in kinds


def test_a_layouts_phrase_leaves_the_vocabularys_fingerprint_alone() -> None:
    from dataclasses import replace

    from ingence.dictionary.programs import program_layouts
    from ingence.dictionary.store import DictionaryStore

    (layout,) = [
        found
        for found in program_layouts(DictionaryStore.builtin().sections)
        if found.name == "published code"
    ]
    assert layout.said_as == "published code"
    assert replace(layout, said_as=None).as_payload() == layout.as_payload()
