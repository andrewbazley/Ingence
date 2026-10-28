"""Two proposals that a person accepts or refuses on the Questions page.

The engine proposes two things and a person decides. Each is raised as a question and answered on
a Record target of its own:

- A vocabulary pack that the project does not load, which would read what nothing reads here. The
  question is raised as ``load the <domain> pack`` and answered on ``pack:<domain>``. A confirm
  loads the pack in every later run, and a reject stops it being proposed.
- A folder of a person's miscellaneous files: the outermost folder that no experiment is, holds
  or lies in. The question is raised as ``<path>: miscellaneous files`` and answered on
  ``miscellaneous:<entry_id>``. A confirm keeps the folder so (nothing in it is moved or
  regrouped), and any answer stops the question.

This module says which question is which and which target its answer goes to. It reads this from
the run and never from the page. A question row carries its proposal (``proposal``: the kind, the
target, the words offered). The decision in force on a question is the one on that target. The
one answer route (``decide.py``) writes an accept or a refusal there. Nothing here writes.

The predicate names and the two raised spellings (``domain_pack_would_read``,
``miscellaneous_files_to_approve``) come from the rulebook. Where the engine that serves the page
raises neither, no row carries a proposal and nothing changes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ingence.dictionary import packs as packs_package
from ingence.record.targets import group_terms_named_by

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = [
    "GROUP_PREFIX",
    "MISCELLANEOUS_PREFIX",
    "PACK_PREFIX",
    "PROPOSAL_WORDS",
    "folders_asked_about",
    "proposal_of",
    "shipped_domains",
]

#: The Record targets the answers are written on (``record/targets.py`` spells them).
PACK_PREFIX = "pack"
MISCELLANEOUS_PREFIX = "miscellaneous"
#: A person's approval of a group's core set of terms: ``group:<terms>``, the terms casefolded,
#: each once, sorted and joined by a space, so one set is one target.
GROUP_PREFIX = "group"

#: The predicates raising the two proposals, and how each spells what it raised.
PACK_PREDICATE = "domain_pack_would_read"
MISCELLANEOUS_PREDICATE = "miscellaneous_files_to_approve"
_PACK_RAISED = re.compile(r"^(?P<act>load|keep) the (?P<domain>[\w-]+) pack$")
_MISCELLANEOUS_SUFFIX = ": miscellaneous files"

#: The words each proposal offers, and the Record event each becomes.
PROPOSAL_WORDS: dict[str, list[dict[str, str]]] = {
    PACK_PREFIX: [
        {"word": "accept", "meaning": "load this pack in every later run", "kind": "confirm"},
        {"word": "refuse", "meaning": "do not load it, and stop proposing it", "kind": "reject"},
    ],
    GROUP_PREFIX: [
        {
            "word": "accept",
            "meaning": "approve these words as a group: every experiment holding them is grouped",
            "kind": "confirm",
        },
        {"word": "refuse", "meaning": "these words are not a group", "kind": "reject"},
    ],
    MISCELLANEOUS_PREFIX: [
        {
            "word": "accept",
            "meaning": "these are my miscellaneous files: keep them as they are",
            "kind": "confirm",
        },
        {
            "word": "refuse",
            "meaning": "not miscellaneous files: leave the folder to the other rules",
            "kind": "reject",
        },
    ],
}


def group_spelled(terms: list[str] | tuple[str, ...]) -> str:
    """A set of terms as its target spells it: casefolded, each once, sorted, space-joined."""
    return " ".join(sorted({term.strip().casefold() for term in terms if term.strip()}))


def shipped_domains() -> frozenset[str]:
    """The domain packs that ship with the engine, by their folder's name."""
    root = packs_package.__file__
    from pathlib import Path

    return frozenset(
        path.name
        for path in Path(root).parent.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )


def _folder_at(catalog: Catalog, path: str) -> str | None:
    found = catalog.query(
        "SELECT entry_id FROM entries WHERE relative_path = ? AND entry_type = 'directory'",
        [path],
    ).fetchone()
    return str(found[0]) if found else None


def folders_asked_about(catalog: Catalog, rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """The folder entry of every miscellaneous-files question among ``rows``, by its path.

    One query reads them all, and :func:`proposal_of` takes the result as ``folders``. Without it,
    :func:`proposal_of` looks each folder up alone, with one scan of the file list per question.
    That takes minutes on a large run. A path that no folder has is left out.
    """
    paths = sorted(
        {
            raised[: -len(_MISCELLANEOUS_SUFFIX)]
            for row in rows
            if str(row.get("predicate") or "") == MISCELLANEOUS_PREDICATE
            and (raised := str(row.get("raised") or "")).endswith(_MISCELLANEOUS_SUFFIX)
        }
    )
    if not paths:
        return {}
    found: dict[str, str] = {}
    for path, entry_id in catalog.query(
        "SELECT relative_path, entry_id FROM entries WHERE entry_type = 'directory' "
        "AND relative_path IN (SELECT unnest(?::VARCHAR[]))",
        [paths],
    ).fetchall():
        found.setdefault(str(path), str(entry_id))
    return found


def proposal_of(
    catalog: Catalog, row: dict[str, Any], *, folders: Mapping[str, str] | None = None
) -> dict[str, Any] | None:
    """The proposal a question row asks about, with its Record target, or None for any other.

    ``folders`` is :func:`folders_asked_about` of the rows asked about, where a caller asks of
    many rows at once; without it a folder is looked up alone."""
    raised = str(row.get("raised") or "")
    predicate = str(row.get("predicate") or "")
    if predicate == PACK_PREDICATE:
        spelled = _PACK_RAISED.match(raised)
        if spelled is None or spelled.group("domain") not in shipped_domains():
            return None
        domain = spelled.group("domain")
        from ingence.rulebook.predicates.packs import idle_clause, pack_across

        if spelled.group("act") == "keep":
            # A loaded pack that reads nothing here is raised under every pack. Every root gives
            # the same sentence, so the first finding's own clause is the reason.
            said = idle_clause(str(row.get("reason") or ""), domain)
            return {
                "kind": PACK_PREFIX,
                "target": f"{PACK_PREFIX}:{domain}",
                "names": domain,
                "words": _kept_words(),
                "question": f"Keep the {domain} vocabulary pack?",
                "note": "Stopping it takes effect at the next re-run.",
                **({"reason": said} if said else {}),
            }

        # Each pack's question says its own counts. A finding's reason names every pack its root
        # would use, so this row reads only its own pack's clause, from the finding of every root
        # that raised it, summed.
        reasons = [str(row.get("reason") or "")]
        if row.get("rule_id"):
            reasons = [
                str(reason)
                for (reason,) in catalog.query(
                    "SELECT reason FROM findings WHERE rule_id = ? AND list_contains(raised, ?) "
                    "ORDER BY subject",
                    [str(row["rule_id"]), raised],
                ).fetchall()
            ] or reasons
        own = pack_across(reasons, domain) or {}
        return {
            "kind": PACK_PREFIX,
            "target": f"{PACK_PREFIX}:{domain}",
            "names": domain,
            "words": _shown(PROPOSAL_WORDS[PACK_PREFIX]),
            **own,
        }
    if predicate == MISCELLANEOUS_PREDICATE and raised.endswith(_MISCELLANEOUS_SUFFIX):
        path = raised[: -len(_MISCELLANEOUS_SUFFIX)]
        entry_id = folders.get(path) if folders is not None else _folder_at(catalog, path)
        if entry_id is None:
            return None
        return {
            "kind": MISCELLANEOUS_PREFIX,
            "target": f"{MISCELLANEOUS_PREFIX}:{entry_id}",
            "names": path,
            "words": _shown(PROPOSAL_WORDS[MISCELLANEOUS_PREFIX]),
        }
    return None


def _kept_words() -> list[dict[str, str]]:
    """A loaded pack's question's words: the Record's accept and refuse, shown as what each does
    to a pack that is already loaded."""
    labels = {
        "accept": ("keep", "keep it loaded"),
        "refuse": ("stop loading it", "from the next re-run"),
    }
    return [
        {
            **item,
            "label": labels[item["word"]][0],
            "meaning": "",
            "explained": labels[item["word"]][1],
        }
        for item in PROPOSAL_WORDS[PACK_PREFIX]
    ]


def _shown(words: list[dict[str, str]]) -> list[dict[str, str]]:
    """A proposal's words as a page shows them: each word's label from the one table, with no
    phrase under it. The phrase is kept as the hover."""
    from ingence.record.verdicts import shown_word

    return [
        {**item, "label": shown_word(item["word"]), "meaning": "", "explained": item["meaning"]}
        for item in words
    ]


def target_is_proposed(catalog: Catalog, project: Path, target: str) -> str | None:
    """Why a proposal's target cannot be answered in this run, or None where it can."""
    del project
    prefix, _, rest = target.partition(":")
    if prefix == PACK_PREFIX:
        if rest not in shipped_domains():
            return f"no vocabulary pack called {rest!r} ships with the engine"
        return None
    if prefix == GROUP_PREFIX:
        terms = rest.split()
        # Only what the engine reads as a group's words is approved: a key that holds a colon, or
        # anything else ``group_terms_named_by`` refuses, is not a group.
        if group_terms_named_by(target) is None and terms:
            return f"{target!r} names no group of words: a group's words hold no colon"
        if not terms:
            return "a group is approved by the words its experiments share, and none were given"
        if group_spelled(terms) != rest:
            return (
                f"a group's words are written once each, in lower case and in order: "
                f"group:{group_spelled(terms)}"
            )
        return None
    if prefix == MISCELLANEOUS_PREFIX:
        found = catalog.query(
            "SELECT count(*) FROM entries WHERE entry_id = ? AND entry_type = 'directory'",
            [rest],
        ).fetchone()
        if not found or not int(found[0]):
            return f"the current run holds no folder {rest!r}"
        return None
    return f"{target!r} is not a proposal"
