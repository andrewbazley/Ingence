"""A claim's class: every claim one rule made with one value, and three of its files.

"This is wrong" about one file is nearly always about a class. A rule that read one file's stage
from a word read every file holding the word the same way, so a person who corrects one file is
judging the rule's reading of all of them. Every classifying claim therefore carries a class
handle. The handle names the class by its rule and its value, and it is the Record's class target
(``record/targets.class_target``). The class's summary is shown before anyone commits. It gives the
rule, the value, how many claims the class holds and three examples. An answer on the handle is one
Record event on the class, never one per file, and inference reads it for every member
(``record/answers.py``, ``infer/answered.py``).

The three examples are drawn reproducibly from the class's own key. Each member is ranked by the
digest of the class key beside its path, and the three lowest are shown, in path order. The same
class in the same run shows the same three, whatever order its members are read in. Two classes do
not share one choice of files.

Nothing here writes. The module reads one run's claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.ids import digest_text
from ingence.record.targets import class_target, rule_named_by

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from ingence.storage.catalog import Catalog

__all__ = [
    "ANSWER_PREDICATE",
    "CLASS_STATUSES",
    "EXAMPLES",
    "PROGRAM_PREDICATE",
    "RETRACTABLE_TIERS",
    "ClaimClass",
    "Member",
    "belongs",
    "claim_class",
    "claim_class_target",
    "class_members",
    "class_named_by",
    "class_of",
    "drawn_examples",
    "program_regions",
    "summarised",
]

#: How many examples a class is shown by. Three are enough to see a pattern and few enough to read
#: before answering.
EXAMPLES = 3

#: The statuses a member's claim is published at: resolved, or conflicting where the only
#: defeater that fired is a person's answer on the class (:func:`belongs`).
CLASS_STATUSES: tuple[str, ...] = ("resolved", "examined_conflicting")
#: The tiers a person's answer may retract (``Rule`` allows defeaters only on them).
RETRACTABLE_TIERS = frozenset({"defeasible", "abductive"})


def claim_class_target(rule_id: str, value: str) -> str:
    """The Record target of a decision about every claim one rule made with one value."""
    return class_target(rule_id, value)


def drawn_examples(key: str, members: Iterable[str], count: int = EXAMPLES) -> list[str]:
    """``count`` of ``members`` drawn by the digest of ``key`` beside each, shown in their order.

    Deterministic, independent of the order the members come in, and different for another key.
    """
    ranked = sorted(set(members), key=lambda member: (digest_text(f"{key}\n{member}"), member))
    return sorted(ranked[:count])


@dataclass(frozen=True, slots=True)
class ClaimClass:
    """One class of claims: its rule, its value, its size, its target and three of its files."""

    rule_id: str
    rule_version: str
    predicate: str
    value: str
    #: The class handle: the Record target an answer on the class is written on.
    target: str
    size: int
    #: The members' paths, three of them, drawn from the target.
    examples: tuple[str, ...]
    #: Every member, as (claim, subject), in subject order.
    members: tuple[tuple[str, str], ...]
    #: Whether the rule's tier lets a person's answer retract its claims (defeasible or
    #: abductive). The answer on a proven reading's class is recorded and changes nothing.
    retractable: bool = True


#: The predicate of the installed-program rule whose folders a class of claims leaves out. The
#: code reads the rule by predicate, so a project's own rule on it is read the same way.
PROGRAM_PREDICATE = "installed_software_in_scope"
#: The predicate of the rule a person's answer on a class is recorded as
#: (``rulebook/predicates/ answered.py``). A claim that only this rule defeated is still a member of
#: its class.
ANSWER_PREDICATE = "answered_claim_class"


def belongs(fired_by: Iterable[str], readers: Collection[str], *, beneath_a_program: bool) -> bool:
    """Whether a claim is a member of its class: every claim one rule made with one value.

    A claim is a member when it lies outside every folder raised as an installed program, and no
    defeater fired on it except a person's answer on the class (``readers`` are the rules that
    answer is recorded as). So the members are the resolved claims outside installed programs, as
    they stood before the answer. The sheet, the handle and the answer read the run's claims
    through this function (:func:`class_members`). Inference asks it of each claim as the claim is
    made (``infer/run.py``). So the sheet, the handle, the answer and inference all count the same
    set.
    """
    return not beneath_a_program and all(rule_id in readers for rule_id in fired_by)


@dataclass(frozen=True, slots=True)
class Member:
    claim_id: str
    subject: str
    path: str
    is_folder: bool


def _answer_readers(catalog: Catalog) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in catalog.query(
            "SELECT rule_id FROM rules WHERE predicate = ?", [ANSWER_PREDICATE]
        ).fetchall()
    )


def program_regions(catalog: Catalog) -> list[str]:
    """The outermost folders the run raised as an installed program's (the review pages' own)."""
    from ingence.api.routes import pages

    return pages._regions(catalog)


def _fired(defeaters_json: object) -> list[str]:
    import json

    try:
        held = json.loads(str(defeaters_json or "[]"))
    except ValueError:
        return []
    return [
        str(item.get("rule_id") or "")
        for item in held
        if isinstance(item, dict) and item.get("fired")
    ]


def class_members(
    catalog: Catalog, *, rule_id: str | None = None, value: str | None = None
) -> dict[tuple[str, str], list[Member]]:
    """Every class of claims in the run with its members, by (rule, value), by :func:`belongs`.

    ``rule_id`` and ``value`` narrow it to one rule, or to one class. Members in path order.
    """
    from ingence.api.routes.pages import _BENEATH_SQL

    readers = _answer_readers(catalog)
    narrowed = ""
    parameters: list[object] = [program_regions(catalog)]
    if rule_id is not None:
        narrowed += " AND c.rule_id = ?"
        parameters.append(rule_id)
    if value is not None:
        narrowed += " AND c.object_reference = ?"
        parameters.append(value)
    rows = catalog.query(
        f"""
        SELECT c.claim_id, c.rule_id, c.object_reference, c.subject,
               coalesce(e.relative_path, c.subject) AS path,
               coalesce(e.entry_type = 'directory', FALSE) AS is_folder,
               c.resolution_status, c.defeaters_json, {_BENEATH_SQL} AS beneath
        FROM claims c LEFT JOIN entries e ON e.entry_id = c.subject
        WHERE c.predicate LIKE 'classifies.%' AND c.object_reference IS NOT NULL
          AND c.resolution_status IN (SELECT unnest(?::VARCHAR[])){narrowed}
        ORDER BY c.rule_id, c.object_reference, path, c.claim_id
        """,  # noqa: S608 -- constant SQL; every value is a parameter
        [parameters[0], list(CLASS_STATUSES), *parameters[1:]],
    ).fetchall()
    classes: dict[tuple[str, str], list[Member]] = {}
    for claim_id, rule, held, subject, path, is_folder, status, defeaters, beneath in rows:
        fired = _fired(defeaters) if status != "resolved" else []
        if not belongs(fired, readers, beneath_a_program=bool(beneath)):
            continue
        classes.setdefault((str(rule), str(held)), []).append(
            Member(str(claim_id), str(subject), str(path), bool(is_folder))
        )
    return classes


def claim_class(catalog: Catalog, claim_id: str) -> ClaimClass | None:
    """The class that a classifying claim's rule and value name in this run, or None.

    None means that no such claim exists. The claim itself may lie outside the class
    (:func:`outside_why`). A class with no member is still named, with a size of zero.
    """
    found = catalog.query(
        "SELECT rule_id, rule_version, predicate, object_reference FROM claims WHERE claim_id = ?",
        [claim_id],
    ).fetchone()
    if found is None or found[3] is None or not str(found[2]).startswith("classifies."):
        return None
    rule_id, rule_version, predicate, value = (str(item) for item in found)
    held = class_of(catalog, rule_id, value, rule_version=rule_version, predicate=predicate)
    if held is not None:
        return held
    return summarised(rule_id, value, [], rule_version=rule_version, predicate=predicate)


#: Why a claim lies outside the class that its rule and value name, in plain words. There is one
#: text for each of the two conditions in :func:`belongs`.
OUTSIDE_A_PROGRAM = (
    "This file lies inside an installed program, so it is not in the class: an answer here is "
    "about the files outside programs and leaves this one as it is."
)
OUTSIDE_A_DEFEATER = (
    "Another reason already marks this file's reading as in doubt, so it is not in the class: an "
    "answer here is about the other files and leaves this one as it is."
)


def outside_why(catalog: Catalog, claim_id: str) -> str | None:
    """Why a classifying claim is not a member of its class, or None where it is a member.

    Membership is decided by :func:`belongs`.
    """
    from ingence.api.routes.pages import _BENEATH_SQL

    found = catalog.query(
        f"""
        SELECT c.resolution_status, c.defeaters_json, {_BENEATH_SQL} AS beneath
        FROM claims c LEFT JOIN entries e ON e.entry_id = c.subject
        WHERE c.claim_id = ?
        """,  # noqa: S608 -- constant SQL; every value is a parameter
        [program_regions(catalog), claim_id],
    ).fetchone()
    if found is None:
        return None
    status, defeaters, beneath = found
    if beneath:
        return OUTSIDE_A_PROGRAM
    fired = _fired(defeaters) if status != "resolved" else []
    if not belongs(fired, _answer_readers(catalog), beneath_a_program=False):
        return OUTSIDE_A_DEFEATER
    return None


def class_of(
    catalog: Catalog,
    rule_id: str,
    value: str,
    *,
    rule_version: str = "",
    predicate: str = "",
) -> ClaimClass | None:
    """One class of claims in this run, by :func:`class_members`; None where it has no member."""
    members = class_members(catalog, rule_id=rule_id, value=value).get((rule_id, value))
    if not members:
        return None
    if not rule_version or not predicate:
        found = catalog.query(
            "SELECT rule_version, predicate FROM claims WHERE claim_id = ?", [members[0].claim_id]
        ).fetchone()
        rule_version = rule_version or str(found[0] if found else "")
        predicate = predicate or str(found[1] if found else "")
    retractable = catalog.query(
        "SELECT tier FROM rules WHERE rule_id = ? ORDER BY version DESC LIMIT 1", [rule_id]
    ).fetchone()
    return summarised(
        rule_id,
        value,
        [(member.claim_id, member.subject, member.path) for member in members],
        rule_version=rule_version,
        predicate=predicate,
        retractable=retractable is None or str(retractable[0]) in RETRACTABLE_TIERS,
    )


def summarised(
    rule_id: str,
    value: str,
    members: Sequence[tuple[str, str, str]],
    *,
    rule_version: str,
    predicate: str,
    retractable: bool = True,
) -> ClaimClass:
    """A class from its members, each (claim, subject, path): its size and three examples."""
    target = claim_class_target(rule_id, value)
    return ClaimClass(
        retractable=retractable,
        rule_id=rule_id,
        rule_version=rule_version,
        predicate=predicate,
        value=value,
        target=target,
        size=len({subject for _, subject, _ in members}),
        examples=tuple(drawn_examples(target, (path for _, _, path in members))),
        members=tuple((claim, subject) for claim, subject, _ in members),
    )


def class_named_by(catalog: Catalog, target: str) -> ClaimClass | None:
    """The class of claims a class target names in this run, or None where it names none.

    The target carries the rule and a digest of the value, so the rule's classes are read
    (:func:`class_members`) and the one whose target it is found: a grouping rule's class is never
    a class of claims.
    """
    rule_id = rule_named_by(target)
    if rule_id is None:
        return None
    for rule, value in class_members(catalog, rule_id=rule_id):
        if claim_class_target(rule, value) == target:
            return class_of(catalog, rule, value)
    return None
