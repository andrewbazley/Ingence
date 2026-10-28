"""What a question asks, in words: its heading, its lead, and one invented example.

Each question kind's heading and lead sentence come from this one table. The table is keyed by what
the raising rule tests, which is its predicate, as the words that a row is offered are
(``record/verdicts.py``). It is never keyed by the rule's name. The page says a raised value in
words ("A link with no target") and does not show it as its code. The rule's name and version stay
on the page as the handle that opens the rule. The rule's plain words and one invented example sit
one click away ("what is this?").

A raised value is not a question. So the heading says what was raised in words, such as "A link to a
missing file". For a file whose content cannot be read, the first sentence says that the file is
empty or could not be read, and that its results come only from its name and folder.

A question that this table does not know is headed by its raised value with the underscores read as
spaces. The rule's plain sentence leads it (``api/plain_words.yaml``). A rule's name is never a
sentence's subject. Every example here is made up (``hela_19_quant.csv``, a ``dapi`` stain,
``dmso``, and so on), and none is a laboratory's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ingence.api.plain_words import rule_sentence

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ALIKE_BY",
    "COPIES_READ",
    "EXAMPLES",
    "RAISED_HEADINGS",
    "TIER_WORDS",
    "CopiesReason",
    "copies_reason",
    "counted_as",
    "question_words_for",
]

#: What a raised value is, in words, by the predicate raising it.
RAISED_HEADINGS: dict[str, dict[str, str]] = {
    "content_unreadable": {
        "empty": "An empty file",
        "empty_output": "An empty output file",
        "empty_and_old": "An empty file, not changed for a long time",
        "dehydrated_or_archived": "An empty file, not changed for a long time",
        "archive_link": "A link to a file archived on another server",
        "link_without_a_target": "A link to a missing file",
        "link_outside_the_root": "A link to a place outside your data folders",
        "not_a_regular_file": "Not an ordinary file",
    },
}

#: One invented example per kind of question, shown under "what is this?".
EXAMPLES: dict[str, str] = {
    "content_unreadable": (
        "dapi_03_tracks.csv is empty, but dapi_01_tracks.csv and dapi_02_tracks.csv are not. "
        "Most likely, that run found nothing."
    ),
    "extension_contradicts_content": "dmso_scan.tif begins the way a ZIP archive does.",
    "authored_name_doubt": "A folder named 2024-03-05_hoechst_failed_redo.",
    "vocabulary_unread_in_scope": "A folder whose names are mostly words like qzx, trell and borp.",
    "stated_count_may_mark_replicates": (
        "hoechst_N=3_quant.csv: 3 samples in one table, or the third file of several."
    ),
    "group_resolution_deviation": (
        "2024-03-05_dapi_dmso_assay and 2024-03-06_dapi_dmso_assay both hold dapi and dmso. "
        "They form one group, and each day stays its own experiment."
    ),
    "experiments_may_belong_together": (
        "2024-03-05_dapi_assay and 2024-04-11_dapi_assay share dapi and assay."
    ),
    "same_observed_metadata": (
        "hela_19_quant.csv in 3 experiments, each with the same size and the same last change time."
    ),
    "same_content_digest": "hela_19_quant.csv and hela_19_quant copy.csv hold the same bytes.",
    "same_component_values": "dmso_sd_raw_1.csv, dmso_sd_raw_2.csv and dmso_sd_raw_3.csv.",
}

#: What a rule compared to put a class's files together, by its predicate, said after "12 files".
#: The question leads with what was compared and not with the name pattern.
ALIKE_BY: dict[str, str] = {
    "same_observed_metadata": "with the same name, size and times",
    "same_content_digest": "with the same contents",
    "same_component_values": "whose names share their words",
}

#: Why a set of copies is asked, by what its digest read. The question states the reason plainly,
#: because a person may not realize that the files are byte identical. The table is keyed by the
#: scope that a digest's class key names (``rulebook/predicates/group.py``). The whole file proves
#: the bytes identical. A sample says only that the parts read agree.
COPIES_READ: dict[str, str] = {
    "whole": "identical bytes",
    "sampled": "the same bytes in the parts Ingence read",
    "header": "the same first bytes",
}

#: Each filesystem fact that a metadata agreement compares, in words. The text does not say the
#: kind of entry, because every member of a set of copies is a file.
_FACT_WORDS: dict[str, str] = {
    "name": "name",
    "extension": "extension",
    "size_bytes": "size",
    "created_ns": "creation time",
    "modified_ns": "last change",
    "changed_ns": "last change of file details",
}

#: The predicates that form a set of copies, as ``record/verdicts.py`` offers them as "Copies".
_DIGEST = "same_content_digest"
_METADATA = "same_observed_metadata"


@dataclass(frozen=True, slots=True)
class CopiesReason:
    """What put a set of copies together, in words, and how strongly it says they are copies.

    ``rank`` orders the reasons, strongest first. The order is identical bytes, then sampled bytes,
    then opening bytes, then a digest that reads no scope, then agreeing metadata. Where several
    rules formed one set, the page gives the strongest.
    """

    #: What the files share, after "with" or "hold": "identical bytes".
    shared: str
    #: The verb the sentence takes: "hold" for contents, "have" for metadata.
    verb: str
    rank: int

    def sentence(self, members: int) -> str:
        """Return the question's first line: "These 12 files hold identical bytes"."""
        files = "This file" if members == 1 else f"These {members} files"
        verb = {"hold": "holds", "have": "has"}[self.verb] if members == 1 else self.verb
        return f"{files} {verb} {self.shared}"


def copies_reason(
    predicate: str | None, key: str, compared_facts: Sequence[str] = ()
) -> CopiesReason | None:
    """Return why a class of copies was formed, in words, or None where its rule forms no copies.

    A digest's reason comes from the scope that its key names. A metadata agreement's reason comes
    from the facts that its rule compares (``compared_facts``, the parameter in force), in the order
    given.
    """
    if predicate == _DIGEST:
        try:
            parsed = json.loads(key)
        except (TypeError, ValueError):
            parsed = {}
        read = str(parsed.get("read") or "") if isinstance(parsed, dict) else ""
        order = tuple(COPIES_READ)
        if read in COPIES_READ:
            return CopiesReason(COPIES_READ[read], "hold", order.index(read))
        return CopiesReason("the same contents", "hold", len(order))
    if predicate == _METADATA:
        facts = [_FACT_WORDS[fact] for fact in compared_facts if fact in _FACT_WORDS]
        listed = (
            ", ".join(facts[:-1]) + " and " + facts[-1] if len(facts) > 1 else "".join(facts)
        ) or "metadata"
        return CopiesReason(f"the same {listed}", "have", len(COPIES_READ) + 1)
    return None


#: A proposed pair's decision tier, in words. "Tier circumstantial" is not a phrase that a person
#: reads.
TIER_WORDS: dict[str, str] = {
    "direct": "named in a file",
    "anchored": "a close match, supported by other evidence",
    "circumstantial": "matched by place, name and time only",
    "blocked": "contradicted by other evidence",
}

#: The predicates whose questions are about experiments, counted as experiments.
_EXPERIMENT_PREDICATES = frozenset(
    {"group_resolution_deviation", "experiments_may_belong_together"}
)

_LATTICE = "a group: "
_ADDS = " + "
#: A deviation, as ``grouping/resolutions.deviation_value`` spells it.
_DEVIATION = re.compile(r"^(?P<word>\S+), (?P<way>added to|missing from) the group (?P<core>.+)$")


def _spoken(value: str) -> str:
    return value.replace("_", " ").strip()


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def counted_as(predicate: str | None, *, folders: bool) -> str:
    """Return what a question's count counts: experiments, folders or files."""
    if (predicate or "") in _EXPERIMENT_PREDICATES:
        return "experiments"
    return "folders" if folders else "files"


def _group_heading(raised: str) -> str | None:
    """A lattice or deviation question about a group of words, as a question in words."""
    if raised.startswith(_LATTICE):
        base, _, added = raised[len(_LATTICE) :].partition(_ADDS)
        if added:
            return f'Possible addition to the group "{base}": "{added}"'
        return f'Possible group: "{base}"'
    found = _DEVIATION.match(raised)
    if found:
        word, core = found.group("word"), found.group("core")
        if found.group("way") == "added to":
            return f'Possible addition to the group "{core}": "{word}"'
        return f'Possible group without "{word}": "{core}"'
    return None


def _count_heading(raised: str) -> str | None:
    try:
        stated = json.loads(raised)
    except ValueError:
        return None
    if isinstance(stated, dict) and "count" in stated:
        return f"A count in the names: {stated['count']}"
    return None


def question_words_for(
    predicate: str | None, rule_id: str, raised: str, affected: int, *, folders: bool = False
) -> dict[str, Any]:
    """Return a question's heading, lead, example and rule sentence, in words.

    ``affected`` is the number of files (or folders, or experiments) that raised the question. It
    chooses between "This file" and "These 3 files".
    """
    kind = predicate or ""
    counted = counted_as(kind, folders=folders)
    plain = rule_sentence(rule_id) or ""
    # The rule's sentence ends with its identifier in parentheses. The page shows that as a handle.
    plain = re.sub(r"\s*\([a-z0-9_]+\)\.?$", ".", plain).strip() if plain else ""
    heading = RAISED_HEADINGS.get(kind, {}).get(raised)
    lead: str | None = None
    if kind == "content_unreadable":
        heading = heading or "A file that cannot be read"
        lead = (
            "This file is empty, or Ingence could not read it. Its results come only from its name "
            "and folder."
            if affected == 1
            else f"These {affected} files are empty, or Ingence could not read them. "
            "Their results come only from their names and folders."
        )
    elif kind == "extension_contradicts_content":
        heading = heading or "Contents that do not match the extension"
        lead = (
            "Its contents start like a different file type."
            if affected == 1
            else f"The contents of these {affected} files start like a different file type."
        )
    elif kind == "authored_name_doubt":
        heading = f'A folder marked "{_spoken(raised)}"'
        lead = (
            f'The word "{_spoken(raised)}" in its name often marks failed work, a test or old work.'
        )
    elif kind == "vocabulary_unread_in_scope":
        heading = "Names Ingence cannot read"
        lead = (
            "Ingence does not know most words in this folder's names."
            if affected == 1
            else f"Ingence does not know most words in the names of these {affected} folders."
        )
    elif kind == "stated_count_may_mark_replicates":
        heading = _count_heading(raised) or f"A count in the names: {_spoken(raised)}"
        lead = (
            "The number may say how many samples one file holds, or which file of a series it is."
        )
    elif kind == "miscellaneous_files_to_approve" and raised.endswith(": miscellaneous files"):
        heading = f'Possible miscellaneous files: "{raised.rsplit(": ", 1)[0]}"'
    elif raised.startswith("load the ") and raised.endswith(" pack"):
        heading = f"Words for {_spoken(raised[len('load the ') : -len(' pack')])}"
    elif raised.startswith("keep the ") and raised.endswith(" pack"):
        # A loaded pack that reads nothing here, under every pack. It has its own lead, because the
        # proposing rule's sentence says "a pack you have not loaded", which is not what this
        # question asks about a pack that is loaded.
        domain = _spoken(raised[len("keep the ") : -len(" pack")])
        heading = f"Words for {domain}: not used here"
        lead = (
            f"Ingence reads {domain} words, but no name here uses them. "
            "Turning them off takes effect at the next Re-run."
        )
    elif kind in _EXPERIMENT_PREDICATES:
        heading = _group_heading(raised) or f'Possible group: "{_spoken(raised)}"'
        lead = (
            f"{affected} {_plural(affected, 'experiment holds', 'experiments hold')} these words."
        )
    if heading is None:
        heading = _spoken(raised)[:1].upper() + _spoken(raised)[1:]
    return {
        "heading": heading,
        "lead": lead if lead is not None else plain,
        "rule_plain": plain,
        "example": EXAMPLES.get(kind),
        "counted": counted,
    }
