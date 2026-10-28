"""A shipped domain pack the pool would use, proposed with its counts.

A project loads only the packs it names. A control proposes packs, for example "this folder holds
ND2 files and buffer names: load microscopy and biochemistry". So after a census, a root is raised
with one question for each domain pack that it does not load and that would read enough of the
unread spellings in the root. Words and extensions both count as spellings, each once. A pack
would read enough when it reads at least ``minimum_spellings`` of the spellings that nothing reads
in the root, carried by at least ``minimum_share`` of those spellings or of the entries that carry
any unread spelling. Either share is enough since version 3. A folder that writes many words of
its own can drown a pack that reads most of its files by spellings, and a folder of many files of
one word can drown a pack by files. ``share_of`` reads one share alone, and a rule that declares
none reads the spellings, as version 2 did.

The counts are the reason. They say how many spellings the pack would read, of how many, the files
that carry them, and the first of them. A person accepts a proposal with a confirm on
``pack:<domain>`` (:mod:`ingence.dictionary.packs.proposals`), which a later run reads to load the
pack. A refusal stops the question. The rule makes a proposal and never a decision. Nothing is
loaded until someone says yes.

An instrument's format is evidence enough on its own: a folder of ND2 files alone proposes
microscopy. A spelling that a pack declares as written by an instrument as it acquires
(``origins``) proposes the pack where at least ``minimum_files`` files of a root carry it unread,
whatever the spelling count and share. One such file among many others, under the floor, does not.
A rule that declares no ``minimum_files`` (version 1) proposes no pack from an instrument's
format.

A project that loads every pack is asked about the packs that read nothing in it. Each loaded
pack that nobody answered, and that no binding in the pool names, is raised as "keep the
<domain> pack". The answer uses the proposal's own words on ``pack:<domain>``. Accept keeps the
pack, and refuse stops loading it from the next run. The rule reads the whole pool, so every root
that raises the question says the same.

The pass hands the packs' spellings, their instruments' formats and, under every pack, the loaded
packs' spellings (``VocabularyRead.UNLOADED_PACKS``), so this never reaches the Dictionary.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from ingence.core.schema import DictionaryBinding, EntryType, RuleEffect
from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.registry import Abstention, Raised, Reads, VocabularyRead, declare

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.rulebook.registry import Predicate

__all__ = [
    "DomainPackWouldRead",
    "idle_clause",
    "keep_value",
    "pack_across",
    "pack_clause",
    "pack_value",
]

#: The kinds of name component a pack could read: a word, and an extension.
_READABLE = frozenset({"word", "extension"})

#: The separators a name may write a term's parts with, taken out before a part is compared.
_SEPARATORS = re.compile(r"[\s_\-.]+")

#: Where one pack's clause begins in a reason naming several.
_CLAUSE_START = re.compile(r"; (?=the [\w-]+ pack would read )")
#: One pack's clause, as :meth:`DomainPackWouldRead.evaluate` writes it: the share in brackets is
#: of the files since version 3, of the spellings before.
_CLAUSE = re.compile(
    r"the (?P<domain>[\w-]+) pack would read (?P<would_read>\d+) of the (?P<unread>\d+) "
    r"spellings nothing reads here \(\d+%[^)]*\), in (?P<where>[^:]*): (?P<spellings>.*)"
)
#: How many files, and how many folders, a clause says. An older run said files only.
_COUNTED = re.compile(r"(?P<number>\d+) (?P<noun>file|folder)s?")


def _where(files: int, folders: int) -> str:
    """How many files and folders carry what a pack would read, each named only where some do."""
    said = [
        f"{number} {noun}{'' if number == 1 else 's'}"
        for number, noun in ((files, "file"), (folders, "folder"))
        if number
    ]
    return " and ".join(said)


def pack_value(domain: str) -> str:
    """The question proposing one domain pack, the same in every run."""
    return f"load the {domain} pack"


def keep_value(domain: str) -> str:
    """The question asking about one loaded pack that reads nothing here, the same in every run."""
    return f"keep the {domain} pack"


def pack_clause(reason: str, domain: str) -> dict[str, object] | None:
    """One pack's own part of a finding's reason, and its counts; None where it names no such pack.

    One finding raises every pack that a root would use, with one reason that names them all,
    because a finding has one reason. So each pack's question reads its own clause: the sentence,
    how many unread spellings the pack would read, of how many, in how many files, and which.
    """
    for clause in _CLAUSE_START.split(reason):
        found = _CLAUSE.fullmatch(clause.strip())
        if found is not None and found.group("domain") == domain:
            listed = found.group("spellings").removesuffix(", ...")
            counted = {"file": 0, "folder": 0}
            for number, noun in _COUNTED.findall(found.group("where")):
                counted[noun] = int(number)
            return {
                "reason": clause.strip(),
                "would_read": int(found.group("would_read")),
                "unread": int(found.group("unread")),
                "files": counted["file"],
                "folders": counted["folder"],
                "spellings": [spelled for spelled in listed.split(", ") if spelled],
            }
    return None


#: One loaded pack's clause where it reads nothing, as
#: :meth:`DomainPackWouldRead._idle` writes it.
_IDLE = re.compile(r"the (?P<domain>[\w-]+) pack reads nothing here: .*")


def idle_clause(reason: str, domain: str) -> str | None:
    """One loaded pack's own sentence in a finding's reason saying it reads nothing; else None."""
    for clause in reason.split("; "):
        found = _IDLE.fullmatch(clause.strip())
        if found is not None and found.group("domain") == domain:
            return clause.strip()
    return None


def pack_across(reasons: Sequence[str], domain: str) -> dict[str, object] | None:
    """One pack's counts summed over every root that raised it; None where none names it.

    Each root raises its own finding, because the rule reads a root at a time. So a pack that a
    person weighs over several roots is counted in all of them. The files and folders are summed,
    and the largest root's clause is kept for its spellings.
    """
    clauses = [found for found in (pack_clause(reason, domain) for reason in reasons) if found]
    if not clauses:
        return None
    largest = max(clauses, key=lambda found: (_entries(found), str(found["reason"])))
    if len(clauses) == 1:
        return {**largest, "roots": 1}
    files = sum(_number(found["files"]) for found in clauses)
    folders = sum(_number(found["folders"]) for found in clauses)
    return {
        **largest,
        "reason": _across_said(domain, largest, _where(files, folders), len(clauses)),
        "files": files,
        "folders": folders,
        "roots": len(clauses),
    }


def _across_said(domain: str, largest: dict[str, object], where: str, roots: int) -> str:
    """The sentence for a pack over several roots, the pack named once."""
    read, unread = _number(largest["would_read"]), _number(largest["unread"])
    spellings = [str(spelled) for spelled in largest["spellings"]]  # type: ignore[attr-defined]
    listed = ", ".join(spellings) + (", ..." if read > len(spellings) else "")
    share = f"{100 * read / unread:.0f}%" if unread else "0%"
    return (
        f"the {domain} pack would read what nothing reads in {where} across {roots} roots; in "
        f"the largest, {read} of its {unread} unread spellings ({share}): {listed}"
    )


def _number(value: object) -> int:
    return value if isinstance(value, int) else 0


def _entries(found: dict[str, object]) -> int:
    return _number(found["files"]) + _number(found["folders"])


class DomainPackWouldRead:
    """Each domain pack not loaded that would read enough of a root's unread spellings."""

    name = "domain_pack_would_read"
    version = "4"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise one proposal per pack that would read enough here, with the counts as reason."""
        packs: Mapping[str, frozenset[str]] = getattr(subject, "unloaded_packs", {}) or {}
        instruments: Mapping[str, frozenset[str]] = (
            getattr(subject, "unloaded_instruments", {}) or {}
        )
        entries = getattr(subject, "entries", ())
        idle: Mapping[str, Any] = getattr(subject, "idle_packs", {}) or {}
        # Only under version 2, whose statement says so: a rule that declares no
        # ``minimum_files`` was written before the question existed.
        if not packs and idle and entries and parameters.get("minimum_files") is not None:
            return self._idle(subject, idle)
        if not packs or not entries:
            return None
        carriers: dict[str, set[str]] = defaultdict(set)
        # The files carrying each unread extension, which an instrument's format is counted by.
        extensions: dict[str, set[str]] = defaultdict(set)
        folders: set[str] = set()
        for held in entries:
            resolved = {
                binding.component_id
                for binding in held.bindings
                if isinstance(binding, DictionaryBinding)
                and binding.resolution_status is ResolutionStatus.RESOLVED
            }
            if held.entry.entry_type is EntryType.DIRECTORY:
                folders.add(held.entry.entry_id)
            for component in held.components:
                if component.component_type in _READABLE and component.component_id not in resolved:
                    spelled = str(component.normalized_value).casefold()
                    carriers[spelled].add(held.entry.entry_id)
                    if component.component_type == "extension":
                        extensions[spelled].add(held.entry.entry_id)
        if not carriers:
            return None
        minimum = int(str(parameters.get("minimum_spellings", 3)))
        share = float(str(parameters.get("minimum_share", 0.02)))
        # Absent from a rule written before it (version 1), which then proposes nothing from an
        # instrument's format.
        floor = parameters.get("minimum_files")
        values: list[str] = []
        reasons: list[str] = []
        cited: set[str] = set()
        # The share is of the entries and not of the spellings (version 3). A folder that writes
        # many words of its own drowned a pack's share of distinct spellings while the pack read
        # most of its files. The entries that carry any unread spelling are the whole.
        unread_entries = set().union(*carriers.values())
        # A rule that declares no ``share_of`` (versions 1 and 2) reads the share of spellings.
        # ``either``, the default, proposes where either share reaches the least.
        share_of = str(parameters.get("share_of") or "spellings")
        for domain in sorted(packs):
            read = sorted(set(carriers) & packs[domain])
            instrument_files = set().union(
                set(), *(extensions[spelling] for spelling in instruments.get(domain, ()))
            )
            by_instrument = floor is not None and len(instrument_files) >= int(str(floor))
            files = set().union(set(), *(carriers[spelling] for spelling in read))
            of_entries = len(files) / len(unread_entries)
            of_spellings = len(read) / len(carriers)
            part = {
                "entries": of_entries,
                "spellings": of_spellings,
                "either": max(of_entries, of_spellings),
            }.get(share_of, of_spellings)
            if not by_instrument and (len(read) < minimum or part < share):
                continue
            values.append(pack_value(domain))
            said_share = (
                f"{100 * of_spellings:.0f}%"
                if share_of == "spellings" or (share_of == "either" and of_spellings >= share)
                else f"{100 * of_entries:.0f}% of the entries carrying one"
            )
            reasons.append(
                f"the {domain} pack would read {len(read)} of the {len(carriers)} spellings "
                f"nothing reads here ({said_share}), in "
                f"{_where(len(files - folders), len(files & folders))}: "
                f"{', '.join(read[:8])}{', ...' if len(read) > 8 else ''}"
            )
            cited.update(sorted(files)[:3])
        if not values:
            return None
        return Raised(values=tuple(values), inputs=tuple(sorted(cited)), reason="; ".join(reasons))

    @staticmethod
    def _idle(subject: object, idle: Mapping[str, Any]) -> object:
        """Raise each loaded pack no binding in the pool names, one question each.

        The run's bindings decide. A pack read a name where a binding, or one of its alternatives
        (tied or shadowed), names one of the pack's senses, or where a name part is a spelling that
        the pack adds to a core type. The pack's spellings as written do not decide, because a
        compound name part (``z-stack``, read as ``z stack``) never matches them.
        """
        held = getattr(subject, "pool", ()) or getattr(subject, "entries", ())
        bound: set[str] = set()
        parts: set[str] = set()
        for records in held:
            for binding in records.bindings:
                if isinstance(binding, DictionaryBinding):
                    if binding.term_id:
                        bound.add(binding.term_id)
                    bound.update(alternative.term_id for alternative in binding.alternatives)
            for component in records.components:
                if component.component_type in _READABLE:
                    parts.add(_SEPARATORS.sub("", str(component.normalized_value).casefold()))
        unread = [
            domain
            for domain in sorted(idle)
            if not idle[domain].terms & bound and not idle[domain].extended & parts
        ]
        if not unread:
            return None
        return Raised(
            values=tuple(keep_value(domain) for domain in unread),
            inputs=(),
            reason="; ".join(
                f"the {domain} pack reads nothing here: no name binds one of its "
                f"{len(idle[domain].terms)} meanings"
                for domain in unread
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that every domain is loaded, or no pack would read enough here."""
        if not getattr(subject, "unloaded_packs", None):
            if getattr(subject, "idle_packs", None):
                return Abstention(reason="every loaded pack reads a word of some name here")
            return Abstention(reason="the project loads every domain pack")
        return Abstention(reason="no domain pack not loaded would read enough of what is unread")


@declare(
    "domain_pack_would_read",
    version="4",
    summary="Raise each domain pack not loaded that would read enough of a root, with its counts",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=("asked_first", "minimum_files", "minimum_share", "minimum_spellings", "share_of"),
    support_families=("vocabulary_overlap",),
    vocabulary=(VocabularyRead.UNLOADED_PACKS,),
)
def _build_domain_pack_would_read() -> Predicate:
    return DomainPackWouldRead()
