"""``/api/v1/manifest``: a run's manifest, typed.

Built from ``api/routes/manifest.py``'s ``manifest`` and shaped field by field: what changed from
the run it was built from, as sentences with their counts, and each sentence's parts.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from ingence import SCHEMA_VERSION
from ingence.api.routes import manifest as manifest_routes
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one, run_named

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["ManifestResponse"]


class ManifestDecision(ApiModel):
    """An answer that took effect in the run: after the parent's watermark, up to its own."""

    sequence: Count
    actor: str
    occurred_at: str
    kind: str
    target: str
    #: Where it was about, as a person reads it: a folder's path, "the word ...", or the target.
    place: str
    #: What it said, as a page shows it ("not a program").
    answer: str
    #: Whether the folder or file it was about reads differently now; None where not traced.
    itself_changed: bool | None
    #: Files beneath it whose readings or flags moved, and how many files are beneath it; None
    #: where the answer names no place the run holds.
    files_changed: Count | None
    files_beneath: Count | None
    said: str


class ManifestWithdrawal(ApiModel):
    """A going back to an earlier run between the two watermarks: what it withdrew."""

    sequence: Count
    actor: str
    occurred_at: str
    answers: Count
    notes: Count
    #: The run gone back to, by its name ("Run 2"), never its identifier.
    went_back_to: str
    said: str


class ManifestRule(ApiModel):
    """A rule new, gone, switched, at another version, or with other parameters."""

    rule_id: Identifier
    rule: str
    version: str
    was_version: str | None
    #: ``new``, ``gone``, ``switched``, ``version`` or ``parameters``.
    change: str
    said: str


class ManifestReading(ApiModel):
    """Readings (or flags) of one rule that moved one way, counted out of the run's entries."""

    rule_id: Identifier
    rule: str
    version: str
    #: ``gained``, ``lost``, ``changed``, ``standing`` (only in doubt or no longer). For flags,
    #: ``raised``, ``cleared`` or ``changed``.
    way: str
    #: The reading before and after, where a sentence names them; None otherwise.
    was: str | None
    now: str | None
    count: Count
    out_of: Count
    said: str


class ManifestPairs(ApiModel):
    """Pairs one rule newly proposes and no longer proposes."""

    rule_id: Identifier
    rule: str
    gained: Count
    lost: Count
    #: The rule's pairs in this run.
    out_of: Count
    said: str


class ThemeJoined(ApiModel):
    theme: str
    experiments: Count
    out_of: Count


class ManifestThemes(ApiModel):
    """Experiments that joined a theme; ``computed`` is false where no theme reader is available."""

    computed: bool
    #: Why themes were not computed, where they were not.
    why_not: str | None
    joined: list[ThemeJoined]
    said: list[str]


class ManifestTopicChange(ApiModel):
    experiment: Identifier
    #: The experiment's folder below its root.
    folder: str
    #: Why it joined or left, in words (the member that explains it).
    reasons: list[str]


class ManifestTopic(ApiModel):
    """One topic of a person's, and what joined or left it since the compared run."""

    topic_id: Identifier
    name: str
    #: Experiments the topic holds in this run; None where no member was read (the run applied no
    #: rules): unknown, never 0.
    experiments: Count | None
    #: False where a member could not be read: the count is then the readable members' at least.
    complete: bool
    #: Whether there was an earlier run to compare with.
    compared: bool
    joined: list[ManifestTopicChange]
    left: list[ManifestTopicChange]
    #: Members that could not be read in one of the two runs, by key.
    unread: list[str]
    not_computed: list[str]
    said: list[str]


class ManifestTopics(ApiModel):
    """A person's topics against the compared run; ``computed`` is false where the reader is not
    available. Where a person made no topic, nothing is said."""

    computed: bool
    why_not: str | None
    topics: list[ManifestTopic]
    said: list[str]


class ManifestTopicEdit(ApiModel):
    """A person's edit of a topic that took effect in the run: an edit, never an answer."""

    sequence: Count
    actor: str
    occurred_at: str
    #: The Record target the topic is written on, and its identifier.
    target: str
    topic_id: Identifier
    name: str
    #: ``made``, ``brought back``, ``changed`` or ``withdrew``.
    what: str
    #: Its members after the edit.
    members: Count
    said: str


class ManifestQualityRow(ApiModel):
    """One quality figure of the run, beside the compared run's value of it."""

    #: ``found``, ``read``, ``names``, ``conclusions`` or ``cost``: the heading it sits under.
    section: str
    figure: str
    #: The breakdown value, or the empty string where the figure has none.
    facet: str
    #: The figure in words, with its breakdown value ("Files not read (the budget ran out)").
    label: str
    #: What is counted, in the plural ("files").
    unit: str
    #: How it is counted, in one line, from the catalog.
    method: str
    #: ``counted``, ``not_run`` (this run's pass does not reach it) or ``not_recorded``.
    state: str
    count: Count | None
    #: What the count is out of, where it is out of something: a page draws the pair as a bar.
    total: Count | None
    seconds: float | None
    was_state: str | None
    was_count: Count | None
    was_total: Count | None
    was_seconds: float | None
    #: Now minus then, where both are counted; ``None`` otherwise.
    change: float | None
    changed: bool
    #: A figure of the machine and the moment (a duration, a peak memory): in the table, and
    #: not in the sentences about what moved.
    volatile: bool
    #: The figure as a sentence with its counts, for a counted figure; empty otherwise.
    said: str
    #: Why a figure is not recorded, in the pass's own words; empty otherwise.
    note: str
    #: The compared run's note on the same figure; empty where it has none.
    was_note: str


class ManifestQualityVersion(ApiModel):
    """One version that wrote the run, beside the compared run's (read from the run records)."""

    name: str
    label: str
    now: str
    was: str


class ManifestQuality(ApiModel):
    """A run's quality figures beside the compared run's."""

    available: bool
    #: Why there are no figures, or none to set beside them, where that is so.
    why_not: str | None
    #: The pass that wrote the run, where the run stored figures.
    phase: str | None
    rows: list[ManifestQualityRow]
    #: One sentence for each figure that moved from the compared run's.
    said: list[str]
    #: The versions that wrote the run and the compared run, and one sentence on whether they
    #: differ: the same versions leave the pool as the reason a figure moved. Empty without a
    #: compared run.
    versions: list[ManifestQualityVersion]
    versions_said: str | None


@register("manifest")
class ManifestResponse(ApiModel):
    """``GET /api/v1/manifest?run=``: what changed in a run from its parent, in sentences."""

    schema_version: str
    run_id: Identifier
    run_label: str | None
    parent_run_id: Identifier | None
    parent_label: str | None
    #: The run its readings are compared with: the run before it of its own kind (an inference
    #: with the inference before it), else its parent.
    compared_run_id: Identifier | None
    compared_label: str | None
    #: The parent's Record watermark and the run's: the decisions after the first and up to the
    #: second took effect in this run.
    watermark_from: Count | None
    watermark_to: Count | None
    #: Every sentence, in the order a person reads them.
    said: list[str]
    decisions: list[ManifestDecision]
    withdrawals: list[ManifestWithdrawal]
    rules: list[ManifestRule]
    claims: list[ManifestReading]
    #: Groups of readings moved past the sentences said, counted in one closing sentence.
    claims_not_said: Count
    flags: list[ManifestReading]
    pairs: list[ManifestPairs]
    themes: ManifestThemes
    #: A person's topics: which experiments joined or left each.
    topics: ManifestTopics
    #: A person's edits of their topics that took effect in this run, said as edits.
    topic_edits: list[ManifestTopicEdit]
    quality: ManifestQuality


def _optional_count(value: Any) -> int | None:
    return None if value is None else int(value)


def _reading(group: Mapping[str, Any]) -> ManifestReading:
    return ManifestReading(
        rule_id=str(group["rule_id"]),
        rule=str(group["rule"]),
        version=str(group.get("version") or ""),
        way=str(group["way"]),
        was=None if group.get("was") is None else str(group["was"]),
        now=None if group.get("now") is None else str(group["now"]),
        count=int(group["count"]),
        out_of=int(group["out_of"]),
        said=str(group["said"]),
    )


@route("manifest", model=ManifestResponse)
def manifest_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> ManifestResponse:
    run_id, _ = run_named(context, query)
    if run_id is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    found = manifest_routes.manifest(context.project, run_id, viewer=one(query, "viewer"))
    watermark = found.get("watermark") or {}
    themes = found.get("themes") or {}
    return ManifestResponse(
        schema_version=SCHEMA_VERSION,
        run_id=str(found["run_id"]),
        run_label=found.get("run_label"),
        parent_run_id=found.get("parent_run_id"),
        parent_label=found.get("parent_label"),
        compared_run_id=found.get("compared_run_id"),
        compared_label=found.get("compared_label"),
        watermark_from=_optional_count(watermark.get("from")),
        watermark_to=_optional_count(watermark.get("to")),
        said=[str(sentence) for sentence in found.get("said") or ()],
        decisions=[
            ManifestDecision(
                sequence=int(decision["sequence"]),
                actor=str(decision["actor"]),
                occurred_at=str(decision["occurred_at"]),
                kind=str(decision["kind"]),
                target=str(decision["target"]),
                place=str(decision["place"]),
                answer=str(decision["answer"]),
                itself_changed=decision.get("itself_changed"),
                files_changed=_optional_count(decision.get("files_changed")),
                files_beneath=_optional_count(decision.get("files_beneath")),
                said=str(decision["said"]),
            )
            for decision in found.get("decisions") or ()
        ],
        withdrawals=[
            ManifestWithdrawal(
                sequence=int(went["sequence"]),
                actor=str(went["actor"]),
                occurred_at=str(went["occurred_at"]),
                answers=int(went["answers"]),
                notes=int(went["notes"]),
                went_back_to=str(went["went_back_to"]),
                said=str(went["said"]),
            )
            for went in found.get("withdrawals") or ()
        ],
        rules=[
            ManifestRule(
                rule_id=str(rule["rule_id"]),
                rule=str(rule["rule"]),
                version=str(rule["version"]),
                was_version=rule.get("was_version"),
                change=str(rule["change"]),
                said=str(rule["said"]),
            )
            for rule in found.get("rules") or ()
        ],
        claims=[_reading(group) for group in found.get("claims") or ()],
        claims_not_said=int(found.get("claims_not_said") or 0),
        flags=[_reading(group) for group in found.get("flags") or ()],
        pairs=[
            ManifestPairs(
                rule_id=str(pair["rule_id"]),
                rule=str(pair["rule"]),
                gained=int(pair["gained"]),
                lost=int(pair["lost"]),
                out_of=int(pair["out_of"]),
                said=str(pair["said"]),
            )
            for pair in found.get("pairs") or ()
        ],
        themes=ManifestThemes(
            computed=bool(themes.get("computed")),
            why_not=themes.get("why_not"),
            joined=[
                ThemeJoined(
                    theme=str(joined["theme"]),
                    experiments=int(joined["experiments"]),
                    out_of=int(joined["out_of"]),
                )
                for joined in themes.get("joined") or ()
            ],
            said=[str(sentence) for sentence in themes.get("said") or ()],
        ),
        topics=ManifestTopics.model_validate(found["topics"]),
        topic_edits=[ManifestTopicEdit.model_validate(edit) for edit in found["topic_edits"]],
        quality=ManifestQuality.model_validate(found["quality"]),
    )
