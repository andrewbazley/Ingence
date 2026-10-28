"""A rule described in plain words: what it decides and what it reads.

One source serves two readers. The website's rule pages (where it is "What it
concludes") and the Dashboard's rule card (``api/routes/rules.py``, "Decides") say what
a rule decides with :func:`decides`, and what it reads with :func:`reads`. So the two places cannot
say it differently. The site marks each identifier as code, and the card writes it plain. Nothing
here reads a project or a run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ingence.core.schema import Rule

__all__ = ["EFFECTS", "LEVELS", "TIERS", "decides", "reads"]

#: What a rule of each effect produces, in a phrase, and how the engine reads it. Effects are kept
#: in
#: the order the engine applies them.
EFFECTS: dict[str, tuple[str, str]] = {
    "enumerates": (
        "a recorded fact",
        "Records a fact while the folders are walked or a container is opened. Run by the walk, "
        "not by the reading of the rules.",
    ),
    "classifies": (
        "a class",
        "Says what a file or a folder is, by placing it in a class.",
    ),
    "groups": (
        "a group",
        "Says which things count as the same in some respect, and so forms groups.",
    ),
    "proposes": (
        "a proposed relation",
        "Proposes that two files are related in one of the eight ways, for a person to look at. "
        "A proposal is never a conclusion.",
    ),
    "flags": (
        "a finding",
        "Raises something for a person's attention without concluding anything.",
    ),
    "constraint": (
        "a limit",
        "Limits what any conclusion may say, by naming what cannot be true together.",
    ),
}

#: The four kinds of reasoning, in plain words (the kind is a property of the rule).
TIERS: dict[str, str] = {
    "entailed": "A conclusion cannot be wrong if what it read is right.",
    "defeasible": (
        "A conclusion is usually right, and is retracted when a rule it names concludes against it."
    ),
    "abductive": (
        "A conclusion is the best explanation of what was seen, and another explanation could "
        "replace it."
    ),
    "testified": "A conclusion is stated by a person, and outranks every rule within its scope.",
}

#: What the level a rule reads means, in plain words.
LEVELS: dict[str, str] = {
    "file": "one file at a time",
    "folder": "one folder at a time",
    "group": "a group of files",
    "root": "a whole folder the engine was pointed at",
    "experiment": "one experiment at a time",
}


def reads(rule: Rule) -> str:
    """What a rule is handed, in words; a grouping rule is handed the things it sorts."""
    if rule.effect.value == "groups":
        things = {
            "file": "files",
            "folder": "folders",
            "group": "groups",
            "root": "roots",
            "experiment": "experiments",
        }.get(rule.level, rule.level)
        return f"{things}, which it sorts into groups"
    return LEVELS.get(rule.level, rule.level)


def decides(rule: Rule, *, mark: Callable[[str], str] = str) -> str:
    """What a rule decides, as a phrase: the relation if it names one, else its kind of output.

    ``mark`` is how an identifier in the phrase is shown (the site marks it as code).
    """
    if rule.primitive is not None:
        extra = [
            f"named {mark(rule.specialization)}" if rule.specialization else "",
            f"in respect of {mark(rule.respect)}" if rule.respect else "",
        ]
        return mark(rule.primitive.value) + "".join(f", {part}" for part in extra if part)
    return EFFECTS.get(rule.effect.value, (rule.effect.value, ""))[0]
