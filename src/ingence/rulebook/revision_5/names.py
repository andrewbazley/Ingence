"""Revision 5, names and spellings: how a name's words are read when two domains spell one word
differently, and how a spelling near a Dictionary spelling is bound and marked ``corrected``.

Every shipped domain section loads in every project, as one dictionary. So the rule that proposed
a pack from a root's unread spellings is superseded by a finding that asks nothing. That finding
is the domain profile that the file list read for each folder. The profile decides a word that
two domains share (``dictionary.domain_skew``).
"""

from __future__ import annotations

__all__ = ["ASKS_NOTHING", "CHANGES", "RULES"]

#: The rules whose findings ask nothing. A folder's domain profile and a spelling read as the
#: Dictionary spelling it is a slip of are stated assumptions. The assumptions route lists them,
#: and the Questions page leaves them out (:func:`ingence.api.routes.review._questions`). A finding
#: names what it raised (the schema holds that), so the mark is the rule's identifier.
ASKS_NOTHING: frozenset[str] = frozenset({"folder_domain_profile", "spelling_read_as"})

#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [
    {
        "id": "folder_domain_profile",
        "version": "1",
        "predicate": "folder_domain_profile",
        "effect": "flags",
        "tier": "abductive",
        "level": "folder",
        "statement": (
            "A folder is raised with the share of its files each domain accounts for, as the "
            "file list read them from the files' types, the words only one domain spells and the "
            "name templates they share; where one domain leads by enough, a word two domains spell "
            "differently is read as that domain's there, and otherwise as the core's meaning, "
            "marked assumed. Nothing is asked."
        ),
        "rationale": (
            "Every domain's words load, so a word shared between domains is decided by the "
            "folder's own files, not by which pack a project named. Recording the profile as a "
            "finding shows the assumption once, in plain words, where a person can see what it "
            "rested on and reverse it with one answer on the word."
        ),
        "parameters": [],
    },
    {
        "id": "spelling_read_as",
        "version": "1",
        "predicate": "spelling_read_as",
        "effect": "flags",
        "tier": "abductive",
        "level": "root",
        "statement": (
            "A root is raised with each spelling the file list read as the one Dictionary "
            "spelling it is a slip from (a word of letters only, five letters and one edit away, "
            "or seven letters and two edits with the same letters or the same first two and last "
            "letters), written in {minimum_files} or more files: the finding says which "
            "spelling is read as which word, and how many files write it. Nothing is asked."
        ),
        "rationale": (
            "A person types a letter twice, drops one or swaps two, and the word is then one "
            "nobody taught the engine. Reading it as the spelling it is a slip of is an "
            "assumption, so it is shown once, in plain words, with its count; one answer on the "
            "word (a meaning, or that the two are unrelated) reverses it at the next reading."
        ),
        "parameters": [
            {
                "name": "minimum_files",
                "type": "integer",
                "default": 1,
                "minimum": 1,
                "maximum": 100000,
                "description": "The fewest files writing a spelling for it to be raised.",
            },
        ],
    },
]
#: What this module changes in rules an earlier revision declared (supersede, override, enable).
CHANGES: list[dict[str, object]] = [
    # Every domain section loads, so nothing is proposed: the profile finding takes its place.
    {"supersede": "domain_pack_would_read_this_pool"},
    # The conflict predicate's version 3 leaves a tie between domains kept in too few files
    # (``name:domain_tie``), so the rule is superseded and declared again below under a name of
    # its own, saying so. One revision says one thing about a rule.
    {"supersede": "binding_is_ambiguous_outside_programs"},
]

RULES.append(
    {
        "id": "binding_is_ambiguous_outside_programs_and_domains",
        "version": "1",
        "predicate": "readings_conflict",
        "effect": "flags",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "An item is raised when a reading of {reading} found {minimum_alternatives} or more "
            "senses standing equal with nothing to separate them, and marked where it lies in a "
            "folder laid out as an installed program, whose names are code; a tie between two "
            "domains' senses that the folder's files could not decide, in fewer files than the "
            "question floor, is kept as a tie and not raised."
        ),
        "rationale": (
            "Every domain's words load, so a word two domains spell differently is a tie unless "
            "the folder's own files decide it. Asking about such a word in a dozen files is not "
            "worth a person's minute; the binding stays conflicting, which is honest, and a word "
            "in more files than the floor is raised."
        ),
        "parameters": [
            {
                "name": "reading",
                "type": "string",
                "default": "dictionary_binding",
                "description": "Which reading is compared.",
            },
            {
                "name": "minimum_alternatives",
                "type": "integer",
                "default": 2,
                "minimum": 2,
                "maximum": 100,
                "description": (
                    "How many senses must stand equal. Two is the minimum a conflict can have, "
                    "and raising it hides exactly the cases that are easiest to fix."
                ),
            },
        ],
    }
)
