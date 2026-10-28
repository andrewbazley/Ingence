"""Revision 5, areas: what an area's folder stands for (a person, an instrument, a date, a
project, a folder). The repeats of experiments are not declared here. ``workflows.py`` declares
``replicates_of_one_condition``."""

from __future__ import annotations

__all__ = ["CHANGES", "RULES"]

#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [
    {
        "id": "area_kind_from_folder_name",
        "version": "1",
        "predicate": "area_kind_from_folder_name",
        "effect": "classifies",
        "tier": "abductive",
        "level": "folder",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "A folder {area_level} below a root is assumed to stand for a date where its name "
            "holds a year or a date; for an instrument where a whole word of its name is an "
            "instrument's spelling in the file-type table (a spelling that is also an ordinary "
            "word only beside other words); and for a plain folder otherwise. "
            "A person's answer on the folder replaces the assumption, and a folder read as an "
            "installed program stands for nothing."
        ),
        "rationale": (
            'A page says "by person", "by instrument" or "by year" rather than "by folder" '
            "where the folders at the area level are named for one of those, and the name is "
            "the only evidence the file list holds before anything is read. A name is weak "
            "evidence, so the reading is an assumption at the abductive tier, shown once on the "
            "assumptions page with the answer that reverses it; a person is never guessed from "
            "a name alone (the answer log's actors are read by the pages, not here), and a "
            "project is only ever said by a person. The rule reads the name alone: a folder "
            "mostly holding one instrument's own file type is read as that instrument's by the "
            "pages until the rules have run, because the rule is handed the folder and not its "
            "files. The level is the themes' area level, bound at inference, so one setting "
            "governs both."
        ),
        "parameters": [
            {
                "name": "area_level",
                "type": "integer",
                "default": 1,
                "minimum": 1,
                "maximum": 8,
                "description": (
                    "How many folders below a root the areas lie: bound from themes.area_level "
                    "at inference (one by default, the first folder below each root), so the "
                    "setting and the rule never disagree."
                ),
            },
        ],
    },
]
#: What this module changes in rules an earlier revision declared (supersede, override, enable).
CHANGES: list[dict[str, object]] = []
