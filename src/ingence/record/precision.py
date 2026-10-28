"""A rule's precision from what people decided about its output, with the method beside it.

``ingence rule show -p <project>`` prints, for each rule, the decisions in force against what the
rule produced. These are the questions its findings raised, its candidates, its claims and its
classes. They are counted as confirmed, rejected and annotated, with a Wilson score interval on the
share confirmed among the decided. The output makes the audit repeatable.

The output gives counts and an interval, never a point estimate alone. No scoring number may look
like a probability (tiers decide, and scores break ties). A bare ``0.83`` beside a rule looks like a
probability, and it hides whether it rests on six decisions or six hundred. An interval with its
counts cannot hide that. The method is stated with every figure.

The method, in full:

- Only decisions in force count. The latest decision about each target (``latest_by_target``)
  counts. A person who changes their mind replaces their earlier word in the figure, and the earlier
  word stays readable in the log.
- The rule is the one that a target names in its spelling (a question, a class), or the one that the
  event's payload records (a candidate, a claim).
- Only confirm, reject and annotate count. An assignment or a definition says what a term means. It
  does not say whether the rule that raised the term was right.
- A figure covers one version. A decision recorded against another version of the rule, or recording
  none, is counted apart and kept out of the interval. A figure is about one rule at one version.
- Annotations are counted and left out of the share. ``cannot_say`` is an outcome of the audit,
  and is neither a confirmation nor a rejection.
- The interval is Wilson at 95 per cent: ``z = 1.959964``. The normal interval collapses to zero
  width at nought or all confirmed, which is where a small audit of a good rule lands.
- Every rejection's word is counted apart. On a pair, ``reversed`` says that a relation exists and
  runs the other way. ``related`` says that the two belong together with neither made from the
  other. ``unrelated`` says that nothing joins them. On a class of copies, ``related`` says that the
  files belong together and are not copies of one file. ``not_copies`` says that they are distinct
  files that happen to agree. All of these are rejections of what the rule said, so the interval
  counts them alike. The sentence says how many of each, so a direction error or a near miss is
  never read as a false pair.
- A figure from a reviewed file says so. A decision that a person reviewed in a filled-in file
  (:func:`reviewed_in_file`) is counted as usual and counted again apart. The sentence says how many
  of the counted decisions were reviewed this way. A person who approves somebody else's reading is
  anchored to it, which is a different class of evidence from a verdict reached blind.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.schema import RecordEventKind
from ingence.record.log import latest_by_target
from ingence.record.targets import rule_named_by

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.core.schema import RecordEvent

__all__ = ["Z_95", "RulePrecision", "precision_by_rule", "wilson_interval"]

#: The standard normal quantile for a two-sided 95 per cent interval.
Z_95 = 1.959964

_COUNTED = {
    RecordEventKind.CONFIRM: "confirmed",
    RecordEventKind.REJECT: "rejected",
    RecordEventKind.ANNOTATE: "annotated",
}


@dataclass(frozen=True, slots=True)
class RulePrecision:
    """What people decided about one rule's output, at the version in force."""

    rule_id: str
    rule_version: str
    confirmed: int = 0
    rejected: int = 0
    annotated: int = 0
    #: Decisions about the rule's output recorded against another version, or none.
    other_versions: int = 0
    #: Of the counted, how many a person reviewed in a filled-in file rather than answered
    #: themselves: an agent's file they confirmed, or a stored event saying it was pre-filled.
    reviewed_in_file: int = 0
    #: The rejections by the word that made each, most first: ``(("related", 2), ...)``.
    rejected_as: tuple[tuple[str, int], ...] = ()

    @property
    def decided(self) -> int:
        return self.confirmed + self.rejected

    @property
    def interval(self) -> tuple[float, float] | None:
        """The Wilson interval on the confirmed share of the decided, or None with none."""
        return wilson_interval(self.confirmed, self.decided)

    def sentence(self) -> str:
        """The figure as a person reads it: counts first, the interval after, the method named."""
        counts = f"{self.confirmed} confirmed, {self.rejected} rejected, {self.annotated} annotated"
        bounds = self.interval
        if bounds is None:
            figure = "no interval: nothing is confirmed or rejected yet"
        else:
            low, high = bounds
            figure = (
                f"Wilson 95% interval on the confirmed share of the {self.decided} decided: "
                f"{100 * low:.1f}% to {100 * high:.1f}%"
            )
        apart = (
            f"; {self.other_versions} about another version, not counted"
            if self.other_versions
            else ""
        )
        counted = self.confirmed + self.rejected + self.annotated
        words = (
            "; rejected as " + ", ".join(f"{word} {count}" for word, count in self.rejected_as)
            if self.rejected_as
            else ""
        )
        anchored = (
            f"; {self.reviewed_in_file} of the {counted} counted were reviewed in a filled-in "
            "file: anchored to it"
            if self.reviewed_in_file
            else ""
        )
        return f"{counts}{words}; {figure}{apart}{anchored}"


def wilson_interval(successes: int, trials: int, *, z: float = Z_95) -> tuple[float, float] | None:
    """Return the Wilson score interval for ``successes`` of ``trials``, or None for no trials."""
    if trials <= 0:
        return None
    share = successes / trials
    spread = z * z / trials
    centre = (share + spread / 2) / (1 + spread)
    half = (z / (1 + spread)) * math.sqrt(share * (1 - share) / trials + spread / (4 * trials))
    # At none or all confirmed the bound is exactly 0 or 1, and floating point lands a hair
    # inside it; the exact value is what the formula means.
    low = 0.0 if successes == 0 else max(0.0, centre - half)
    high = 1.0 if successes == trials else min(1.0, centre + half)
    return low, high


def reviewed_in_file(source: object) -> bool:
    """Whether an event's source says a person reviewed its answer in a filled-in file.

    An agent's file imported once the person confirmed the review (``record/imported.py``), or
    an event stored before that import existed whose source names who pre-filled it.
    """
    if not isinstance(source, dict):
        return False
    stored = source.get("prefilled_by")
    return source.get("agent_generated") is True or (isinstance(stored, str) and bool(stored))


def precision_by_rule(
    events: Sequence[RecordEvent], versions: Mapping[str, str]
) -> dict[str, RulePrecision]:
    """Count the decisions in force about each rule's output, against the versions in force.

    ``versions`` maps each rule in force to its version; a decision about a rule not in it is
    left out, because there is no version in force to hold it against.
    """
    counts: dict[str, dict[str, int]] = {}
    rejected_as: dict[str, dict[str, int]] = {}
    for event in latest_by_target(events).values():
        kind = _COUNTED.get(event.kind)
        if kind is None:
            continue
        payload = json.loads(event.payload or "{}")
        rule_id = rule_named_by(event.target) or payload.get("rule_id")
        if not isinstance(rule_id, str) or rule_id not in versions:
            continue
        held = counts.setdefault(rule_id, {"confirmed": 0, "rejected": 0, "annotated": 0})
        if payload.get("rule_version") != versions[rule_id]:
            held["other_versions"] = held.get("other_versions", 0) + 1
            continue
        held[kind] += 1
        if event.kind is RecordEventKind.REJECT:
            word = str(payload.get("verdict") or payload.get("worth_reading") or "reject")
            said = rejected_as.setdefault(rule_id, {})
            said[word] = said.get(word, 0) + 1
        if reviewed_in_file(payload.get("source")):
            held["reviewed_in_file"] = held.get("reviewed_in_file", 0) + 1
    return {
        rule_id: RulePrecision(
            rule_id=rule_id,
            rule_version=versions[rule_id],
            rejected_as=tuple(
                sorted(rejected_as.get(rule_id, {}).items(), key=lambda item: (-item[1], item[0]))
            ),
            **held,
        )
        for rule_id, held in sorted(counts.items())
    }
