"""What a Record event can be about, each spelled once.

A decision can be about a question, which is what a flagging rule raised wherever it raised it, or
about any of the other targets below. A proposing rule's whole output is candidates, so a candidate
target lets a person decide about that output.

| Target | Spelled | A decision about |
|---|---|---|
| a question | ``question:<rule_id>:<raised>`` | what a flagging rule raised, wherever |
| a candidate | ``candidate:<candidate_id>`` | one proposed pair, in the direction proposed |
| a claim | ``claim:<claim_id>`` | one published conclusion about one subject |
| a class | ``class:<rule_id>:<key digest>`` | one class a grouping rule formed |
| an extension | ``extension:<extension>`` | every file carrying one extension, wherever |
| a folder | ``folder:<entry_id>`` | one folder, the region a folder rule raised |
| a divided spelling | ``split:<spelling>`` | whether a spelling a capital divides is one word |
| a group's core | ``group:<terms>`` | every experiment sharing one set of terms |
| miscellaneous | ``miscellaneous:<entry_id>`` | one folder a person keeps as miscellaneous files |
| a domain pack | ``pack:<domain>`` | loading one shipped domain pack the engine proposed |
| a sense | ``sense:<term>`` | whether one sense is in force, whatever its origin |
| a spelling | ``surface:<term>:<spelling>`` | whether one spelling of one sense binds |
| two spellings | ``distinct:<a>+<b>`` | that two spellings are unrelated words |
| an analysis | ``analysed_elsewhere:<entry_id>`` | that an experiment is analysed elsewhere |
| an area | ``area:<entry_id>`` | what one area's folder stands for |

An extension and a folder are what a teaching sheet is about. Whether an extension is worth reading
is a decision about every file that carries it, in whichever run. The extension is spelled as the
census stores it: lower-cased, without its dot, and ``(none)`` for the files that have none. A
folder is spelled as the flag phase already spells the region it raised (``folder:<entry_id>``). So
a description of a folder and a finding about it name one thing.

Every identifier is already the engine's, so a target survives a re-run of unchanged input. A
candidate's, a claim's and a finding's identifier are digests of what they say under which rule at
which version, and never of the run (``core/ids.py``). The same conclusion reached by the next
inference is the same target, and a decision about it still reads as one.

A class is named by its rule and its key and not by its members. A class is the key that a grouping
rule computed, and its members are whoever shares the key in this run. A verdict that a class is one
experiment is about the key. The key is digested so that a target stays one short line however long
a key's components run.

Each spelling lives here, as in ``questions.py``. A target assembled at two call sites is two
spellings of one subject, and the Record would hold one person's two decisions as decisions about
two things.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.ids import digest_text
from ingence.record.questions import TARGET_PREFIX, TERM_PREFIX, question_target

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "ANALYSED_ELSEWHERE_PREFIX",
    "AREA_PREFIX",
    "CANDIDATE_PREFIX",
    "CLAIM_PREFIX",
    "CLASS_PREFIX",
    "DISTINCT_PREFIX",
    "EXTENSION_PREFIX",
    "FOLDER_PREFIX",
    "GROUP_PREFIX",
    "MISCELLANEOUS_PREFIX",
    "NO_EXTENSION",
    "PACK_PREFIX",
    "PROGRAM_PREFIX",
    "QUESTION_PREFIX",
    "RESTORE_PREFIX",
    "SENSE_PREFIX",
    "SPLIT_PREFIX",
    "SURFACE_PREFIX",
    "analysed_elsewhere_target",
    "area_named_by",
    "area_target",
    "candidate_target",
    "claim_target",
    "class_target",
    "distinct_named_by",
    "distinct_target",
    "experiment_analysed_elsewhere",
    "extension_named_by",
    "extension_target",
    "folder_target",
    "group_target",
    "group_terms_named_by",
    "miscellaneous_target",
    "pack_target",
    "program_target",
    "question_target",
    "restore_target",
    "rule_named_by",
    "sense_target",
    "split_target",
    "surface_named_by",
    "surface_target",
    "target_kind",
]

QUESTION_PREFIX = TARGET_PREFIX
CANDIDATE_PREFIX = "candidate"
CLAIM_PREFIX = "claim"
CLASS_PREFIX = "class"
EXTENSION_PREFIX = "extension"
FOLDER_PREFIX = "folder"
#: A person's answer on whether one folder is an installed program or a package.
PROGRAM_PREFIX = "program"
#: A person going back to an earlier run (schema 1.22): the withdrawal a restore writes is about
#: the run it restored (``workspace/restore.py``).
RESTORE_PREFIX = "restore"
#: A person's reading of a spelling that a capital divides: one word or separate words. The
#: payload's ``reading`` is ``whole`` or ``pieces``. It is not a meaning, so it is never a
#: ``define`` on a ``term:`` target. The binder reads it, and it wins over the counts.
SPLIT_PREFIX = "split"
#: A person's approval of a group's core set of terms.
GROUP_PREFIX = "group"
#: A person's answer on whether one folder is their miscellaneous files.
MISCELLANEOUS_PREFIX = "miscellaneous"
#: A person's answer on loading a shipped domain pack that the engine proposed.
PACK_PREFIX = "pack"
#: A person inactivating a sense, or one spelling of one sense. A ``reject`` inactivates and a later
#: ``confirm`` brings it back, so nothing is deleted and the Record says when and by whom. The
#: Dictionary reads these answers (``dictionary/inactive.py``). The shipped files are never edited.
SENSE_PREFIX = "sense"
SURFACE_PREFIX = "surface"
#: A person's answer that two spellings are unrelated. The Words page offers a spelling as possibly
#: the same word as another. A person who says they are unrelated words is not offered the pair
#: again. The target is ``distinct:<a>+<b>`` with the two spellings casefolded and sorted, so a pair
#: has one target whichever spelling the page started from. A ``reject`` says "not these together"
#: and keeps the pair apart. A later ``confirm`` takes that back. A run reads nothing from it,
#: because a pair offered is a view and never a binding, so it waits for no Re-run. It is a Record
#: event like every answer, and a carried answer (``record/imported``) too.
DISTINCT_PREFIX = "distinct"
#: What stands between the two spellings of a ``distinct:`` target.
_PAIR_JOIN = "+"

#: A person's answer that one experiment's analysis is elsewhere. The target is
#: ``analysed_elsewhere:<entry_id>``, the experiment's folder. A ``confirm`` says the analysis is
#: elsewhere, and the next run leaves the experiment off "no analysis found". A later ``reject``
#: takes that back. The answer has its own target, as the program answer does, because a note or a
#: description of the folder must not displace it.
ANALYSED_ELSEWHERE_PREFIX = "analysed_elsewhere"

#: A person's answer on what an area's folder stands for. The target is ``area:<entry_id>``, the
#: folder at the area level, with the kind in the payload (``{"kind": "instrument"}``). The answer
#: has its own target, as the program answer does, because a note on the folder must not displace
#: it.
AREA_PREFIX = "area"

#: How an extension target spells the files that have none. The census stores no extension as
#: an empty string, and a target ending in a bare colon would read as a target naming nothing.
NO_EXTENSION = "(none)"


def candidate_target(candidate_id: str) -> str:
    """Return the canonical target of a decision about one proposed pair."""
    return f"{CANDIDATE_PREFIX}:{candidate_id}"


def claim_target(claim_id: str) -> str:
    """Return the canonical target of a decision about one published claim."""
    return f"{CLAIM_PREFIX}:{claim_id}"


def class_target(rule_id: str, key: str) -> str:
    """Return the canonical target of a decision about one class, from its rule and its key."""
    return f"{CLASS_PREFIX}:{rule_id}:{digest_text(key)}"


def extension_target(extension: str) -> str:
    """Return the canonical target of a decision about every file carrying one extension.

    Spelled as the census stores an extension, lower-cased and without its dot, so a sheet
    writing ``.TIF`` and one writing ``tif`` decide about one thing.
    """
    from ingence.core.extensions import canonical_extension

    spelled = canonical_extension(extension)
    return f"{EXTENSION_PREFIX}:{spelled or NO_EXTENSION}"


def extension_named_by(target: str) -> str | None:
    """Return the extension an extension target names, as the census stores it, or None."""
    prefix, _, rest = target.partition(":")
    if prefix != EXTENSION_PREFIX or not rest:
        return None
    return "" if rest == NO_EXTENSION else rest


def folder_target(entry_id: str) -> str:
    """Return the canonical target of a decision about one folder, from its entry identifier."""
    return f"{FOLDER_PREFIX}:{entry_id}"


def program_target(entry_id: str) -> str:
    """Return the canonical target of a person's answer on whether one folder is a program.

    The answer has its own target and not the folder's. A note or a description of a folder is a
    decision about the folder, and the latest decision on a target is the one in force. An answer
    that shared the folder's target would be displaced by the next thing said about the folder.
    """
    return f"{PROGRAM_PREFIX}:{entry_id}"


def restore_target(run_id: str) -> str:
    """Return the target of the withdrawal a restore to ``run_id`` writes."""
    return f"{RESTORE_PREFIX}:{run_id}"


def split_target(spelling: str) -> str:
    """Return the target of a decision about how one divided spelling reads, casefolded."""
    return f"{SPLIT_PREFIX}:{spelling.strip().casefold()}"


def pack_target(domain: str) -> str:
    """Return the canonical target of an answer on loading one shipped domain pack.

    A confirm loads the pack in every later run. A reject stops the pack being proposed.
    """
    return f"{PACK_PREFIX}:{domain.strip().casefold()}"


def sense_target(term: str) -> str:
    """Return the target of a decision about whether one sense is in force, by its term.

    The term as the Dictionary spells it (terms hold no colon or space), and never casefolded:
    a term is an identifier, and two terms differing in case are two senses.
    """
    return f"{SENSE_PREFIX}:{term.strip()}"


def surface_target(term: str, spelling: str) -> str:
    """Return the target of a decision about whether one spelling of one sense binds.

    The target is one spelling of one sense, and not the spelling everywhere. ``ms`` is a
    millisecond and, in a pack, mass spectrometry, and a person who stops one reading keeps the
    other. The spelling keeps its case, because ``nM`` and ``nm`` are two units.
    """
    return f"{SURFACE_PREFIX}:{term.strip()}:{spelling.strip()}"


def surface_named_by(target: str) -> tuple[str, str] | None:
    """Return the term and spelling a spelling's target names, or None for any other target."""
    prefix, _, rest = target.partition(":")
    term, _, spelling = rest.partition(":")
    if prefix != SURFACE_PREFIX or not term or not spelling:
        return None
    return term, spelling


def miscellaneous_target(entry_id: str) -> str:
    """Return the canonical target of an answer on whether one folder is miscellaneous files.

    The answer has its own target, as the program answer does, because a note about the folder must
    not displace it. A confirm keeps the folder as miscellaneous files, claimed so. A reject says it
    is not. Either way the question is answered and not asked again.
    """
    return f"{MISCELLANEOUS_PREFIX}:{entry_id}"


def group_target(terms: Iterable[str]) -> str:
    """Return the canonical target of a decision about a group's core set of terms.

    The target is the set, and not a question or a list of folders. Approving a core set of terms
    resolves every experiment that shares it, wherever it is and in whichever run, so the decision
    is about the terms. The target is spelled casefolded, with each term once, sorted and joined by
    a space, so two pages that offer one set in two orders write one target.
    """
    return f"{GROUP_PREFIX}:{' '.join(sorted({t.strip().casefold() for t in terms if t.strip()}))}"


def group_terms_named_by(target: str) -> tuple[str, ...] | None:
    """Return the core set of terms a group target names, in canonical order, or None."""
    prefix, _, rest = target.partition(":")
    # A core holds no colon. A target that does (``group:<rule>:<json>``, the old key of the claim
    # samples, now ``value-group:``) names no group.
    if prefix != GROUP_PREFIX or not rest.strip() or ":" in rest:
        return None
    return tuple(sorted(set(rest.split())))


def distinct_target(first: str, second: str) -> str:
    """Return the target of a decision that two spellings are unrelated, the pair sorted.

    Each spelling is stripped and casefolded; a spelling holding the join (``+``) or nothing at all
    names no word and is refused.
    """
    pair = sorted({first.strip().casefold(), second.strip().casefold()})
    if len(pair) != 2 or not all(pair) or any(_PAIR_JOIN in spelling for spelling in pair):
        message = "a pair is two different spellings, and neither holds a plus sign"
        raise ValueError(message)
    return f"{DISTINCT_PREFIX}:{pair[0]}{_PAIR_JOIN}{pair[1]}"


def distinct_named_by(target: str) -> tuple[str, str] | None:
    """Return the two spellings a ``distinct:`` target names, or None for any other.

    Only the target as :func:`distinct_target` writes it: casefolded, stripped, and sorted.
    A pair written any other way (``distinct:Brisa+alpha``) is one nothing reads, because the
    Words section looks a pair up by its one canonical target, so it names nothing here either.
    """
    prefix, _, rest = target.partition(":")
    if prefix != DISTINCT_PREFIX or rest.count(_PAIR_JOIN) != 1:
        return None
    first, second = rest.split(_PAIR_JOIN)
    try:
        return (first, second) if distinct_target(first, second) == target else None
    except ValueError:
        return None


def analysed_elsewhere_target(entry_id: str) -> str:
    """Return the target of a person's answer that one experiment's analysis is elsewhere."""
    return f"{ANALYSED_ELSEWHERE_PREFIX}:{entry_id}"


def experiment_analysed_elsewhere(target: str) -> str | None:
    """Return the experiment folder an analysed-elsewhere target names, or None for any other."""
    prefix, _, rest = target.partition(":")
    return rest if prefix == ANALYSED_ELSEWHERE_PREFIX and rest and ":" not in rest else None


def area_target(entry_id: str) -> str:
    """Return the canonical target of a person's answer on what one area's folder stands for."""
    return f"{AREA_PREFIX}:{entry_id}"


def area_named_by(target: str) -> str | None:
    """Return the area folder's entry an area target names, or None where it names none."""
    prefix, _, rest = target.partition(":")
    return rest if prefix == AREA_PREFIX and rest and ":" not in rest else None


def target_kind(target: str) -> str:
    """Return what a target is about: its prefix, or an empty string for none of these."""
    prefix, _, rest = target.partition(":")
    known = (
        QUESTION_PREFIX,
        CANDIDATE_PREFIX,
        CLAIM_PREFIX,
        CLASS_PREFIX,
        EXTENSION_PREFIX,
        FOLDER_PREFIX,
        PROGRAM_PREFIX,
        RESTORE_PREFIX,
        SPLIT_PREFIX,
        GROUP_PREFIX,
        MISCELLANEOUS_PREFIX,
        PACK_PREFIX,
        TERM_PREFIX,
        SENSE_PREFIX,
        SURFACE_PREFIX,
        DISTINCT_PREFIX,
        ANALYSED_ELSEWHERE_PREFIX,
        AREA_PREFIX,
    )
    return prefix if rest and prefix in known else ""


def rule_named_by(target: str) -> str | None:
    """Return the rule a target names in its own spelling, or None where it names none.

    A question and a class carry their rule in the target; a candidate and a claim carry only
    an identifier, and the rule behind one is in the event's payload.
    """
    prefix, _, rest = target.partition(":")
    if prefix in (QUESTION_PREFIX, CLASS_PREFIX):
        rule_id, _, remainder = rest.partition(":")
        return rule_id if rule_id and remainder else None
    return None


#: A person's display label for one entry (the Graph workspace's human layer). The target is
#: ``label:<entry_id>``. It is an annotation shown beside the entry's own name and never a decision
#: about the entry. Nothing in inference, the review or the sheets reads it, and the entry's name on
#: disk and in the Index is unchanged. A later annotation with no label removes it from view, while
#: the earlier ones stay in the Record. A file that moves keeps its label (``at_new_places`` reads
#: any part of a target that names an entry at the entry's new place).
LABEL_PREFIX = "label"


def label_target(entry_id: str) -> str:
    """The target a person's display label for one entry is written against."""
    return f"{LABEL_PREFIX}:{entry_id}"


def entry_labelled_by(target: str) -> str | None:
    """The entry a label target names, or None for any other target."""
    prefix, _, rest = target.partition(":")
    return rest if prefix == LABEL_PREFIX and rest else None


#: A person's name for a theme (the Dashboard's Rename). The target is ``theme:<key>``, where the
#: key is the theme's own words, sorted and joined with ``+``. The name follows the theme across
#: runs as long as the engine reads the same words for it. It is one ``annotate`` event with the
#: name in its payload beside the engine's label. A later annotation with no name removes it from
#: view, while the earlier ones stay in the Record. Nothing in inference, the review or the sheets
#: reads it, because themes are a projection and never a claim.
THEME_PREFIX = "theme"


def theme_key(words: Iterable[str]) -> str:
    """The key a theme is named by: its words, lower-cased, sorted, joined with ``+``."""
    return "+".join(sorted({str(word).strip().lower() for word in words if str(word).strip()}))


def theme_target(key: str) -> str:
    """The target a person's name for one line of work is written against."""
    return f"{THEME_PREFIX}:{key}"


def theme_named_by(target: str) -> str | None:
    """The theme key a theme target names, or None for any other target."""
    prefix, _, rest = target.partition(":")
    return rest if prefix == THEME_PREFIX and rest else None
