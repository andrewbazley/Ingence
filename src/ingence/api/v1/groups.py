"""``GET /api/v1/groups/resolutions``: group resolutions.

Approving a core set of terms resolves every occurrence that shares it. Identical sets become a
group per experiment (each day its own) and one group across the experiments. For supersets and
subsets, the shared core is concluded as a group common to them all, and each deviation (a word
added, a word missing) is left as its own group for confirmation. This read shows exactly that,
per approved core:

- the core: the person's confirm on ``group:<terms>`` in the Record, who and when;
- the groups it resolved in each experiment and across them (the run's claims by the two
  resolving rules);
- the deviations waiting, each a question on the Questions page.

The resolving is the engine's (the rules ``group_resolved_within_experiment``,
``group_resolved_across_experiments`` and ``group_resolution_deviation``). A run whose Rulebook does
not hold those rules, or a run made before the core was approved, resolves nothing. The response
says so (``resolving_rules_in_run``) rather than showing a group the engine did not make. Nothing
here writes.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one, pinned_catalog
from ingence.dictionary.written import written_forms, written_terms, written_word
from ingence.grouping.asked_again import AskedAgain
from ingence.grouping.lattice_words import SPELLINGS, WordMap, words_said_by_run
from ingence.grouping.resolutions import DEFAULT_MINIMUM_EXPERIMENTS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.storage.catalog import Catalog

__all__ = ["GroupCompare", "GroupLattice", "GroupResolutions", "compare", "lattice", "resolutions"]

WITHIN_RULE = "group_resolved_within_experiment"
ACROSS_RULE = "group_resolved_across_experiments"
DEVIATION_RULE = "group_resolution_deviation"
_DEVIATION = re.compile(r"^(?P<word>.+), (?P<way>added to|missing from) the group (?P<core>.+)$")


class ResolvedGroup(ApiModel):
    """One group an approved core resolved, with how many entries it holds."""

    key: str
    #: The experiment's folder, for a group inside one experiment; None for the group across.
    experiment: str | None
    members: Count


class Deviation(ApiModel):
    """A word an experiment adds to, or misses from, an approved core: a question of its own."""

    word: str
    way: Literal["added", "missing"]
    experiment: str
    #: The question's target on the Questions page, where a person answers it.
    question: str
    answered: bool


class CoreResolution(ApiModel):
    """One approved core set of terms and what it resolved in the run."""

    core: list[str]
    target: str
    approved_by: str
    approved_at: str
    sequence: Count
    within: list[ResolvedGroup]
    across: list[ResolvedGroup]
    deviations: list[Deviation]


@register("group_resolutions")
class GroupResolutions(ApiModel):
    """Every core a person approved, the groups each resolved, and the deviations waiting."""

    schema_version: str
    run_id: Identifier
    #: Whether the run holds any conclusion of the resolving rules at all.
    resolving_rules_in_run: bool
    cores: list[CoreResolution]


def _approved(context: V1Context) -> list[tuple[tuple[str, ...], Any]]:
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.targets import group_terms_named_by

    found = []
    for target, event in sorted(latest_by_target(read_events(context.project)).items()):
        # Read as inference reads it: a target naming no group of words is none.
        terms = group_terms_named_by(target)
        if terms and event.kind is RecordEventKind.CONFIRM:
            found.append((tuple(target.partition(":")[2].split()), event))
    return found


def _groups(catalog: Catalog, rule_id: str) -> list[tuple[str, int, str | None]]:
    """Each group one resolving rule concluded: its key, its members, one member's folder path."""
    try:
        rows = catalog.query(
            """
            SELECT c.object_reference AS key, count(DISTINCT c.subject) AS members,
                   min(e.relative_path) AS path
            FROM claims c LEFT JOIN entries e ON e.entry_id = c.subject
            WHERE c.rule_id = ?
            GROUP BY key ORDER BY key
            """,
            [rule_id],
        ).fetchall()
    except Exception:  # a run without the table's columns: nothing resolved
        return []
    return [(str(json.loads(key) if _is_json(key) else key), int(n), path) for key, n, path in rows]


def _is_json(value: Any) -> bool:
    try:
        json.loads(value)
    except (TypeError, ValueError):
        return False
    return isinstance(json.loads(value), str)


def _deviations(catalog: Catalog) -> list[tuple[str, str]]:
    """Each deviation question the run raised: its value and the folder it was raised on."""
    try:
        # Unnested before the join: a correlated unnest under a left join is refused by DuckDB, and
        # the refusal, swallowed below, hid every deviation.
        rows = catalog.query(
            """
            SELECT DISTINCT r.value AS raised, e.relative_path AS path
            FROM (
                SELECT unnest(f.raised) AS value,
                       regexp_replace(f.subject, '^folder:', '') AS folder
                FROM findings f WHERE f.rule_id = ?
            ) r
            LEFT JOIN entries e ON e.entry_id = r.folder
            ORDER BY raised, path
            """,
            [DEVIATION_RULE],
        ).fetchall()
    except Exception:  # a run without the rule's findings
        return []
    return [(str(raised), str(path or "")) for raised, path in rows]


def _core_of(terms: frozenset[str], cores: Sequence[tuple[str, ...]]) -> tuple[str, ...] | None:
    """The approved core a resolved group's terms belong to: the one holding them that shares
    the most, then the first in canonical order (as the engine chooses between two cores)."""
    holding = [core for core in cores if terms <= frozenset(core)]
    if not holding:
        return None
    return sorted(holding, key=lambda core: (len(frozenset(core) - terms), core))[0]


def _said_or_refuse(catalog: Catalog, holds_groups: bool) -> str:
    """The words the pinned run made its groups of, or a refusal in words where it cannot be told.

    The function never guesses. A run that does not say which words it made its groups of is
    refused where it holds groups, because its groups could be read in words other than its own. A
    run that holds no groups has nothing to misread.
    """
    said = words_said_by_run(catalog)
    if said is not None:
        return said
    if holds_groups:
        message = (
            "This run does not say which words it made its groups of (the Dictionary's terms or "
            "the spellings its names write), so its groups are not shown rather than shown in "
            "the wrong ones. Read the project again to make a run that says."
        )
        raise ApiError(message, code="words_not_said", status=HTTPStatus.CONFLICT)
    return SPELLINGS


@route("groups/resolutions", model=GroupResolutions)
def resolutions(context: V1Context, query: Mapping[str, Sequence[str]]) -> GroupResolutions:
    """The approved cores and what each resolved in the pinned run; nothing is written."""
    del query
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.questions import question_target

    approved = _approved(context)
    answered = dict.fromkeys(latest_by_target(read_events(context.project)), True)
    with pinned_catalog(context) as (catalog, run_id):
        within = _groups(catalog, WITHIN_RULE)
        across = _groups(catalog, ACROSS_RULE)
        deviations = _deviations(catalog)
        # A stored core is read in the words the run made its groups of: the run's own
        # ``groups.words_as``, never the file as it stands now, so a change waits for Re-run. The
        # groups it resolved are keyed by those words, so a core approved on spellings is looked
        # for as the terms they are (and the reverse). Each reading belongs to the core as it was
        # stored, which is what the page shows and answers.
        words_as = _said_or_refuse(catalog, bool(within or across or deviations))
        reading = _word_map(catalog, context.project, run_id, words_as)
    # A question answered before the words changed is still answered: it is found by what it
    # asks, in the words the run holds.
    answered_again = AskedAgain(lambda: reading, answered)
    read_as: dict[tuple[str, ...], tuple[str, ...]] = {}
    for terms, _ in approved:
        for words in reading.answer(terms):
            read_as.setdefault(words, terms)
    cores = list(read_as)
    held: dict[tuple[str, ...], dict[str, list[Any]]] = {
        terms: {"within": [], "across": [], "deviations": []} for terms, _ in approved
    }
    for key, members, _ in within:
        spelled, _, experiment = key.partition(" in ")
        owner = _core_of(frozenset(spelled.split()), cores)
        if owner is not None:
            held[read_as[owner]]["within"].append(
                ResolvedGroup(key=key, experiment=experiment or None, members=members)
            )
    for key, members, _ in across:
        owner = _core_of(frozenset(key.split()), cores)
        if owner is not None:
            held[read_as[owner]]["across"].append(
                ResolvedGroup(key=key, experiment=None, members=members)
            )
    for raised, path in deviations:
        found = _DEVIATION.match(raised)
        if found is None:
            continue
        stored = read_as.get(tuple(found.group("core").split()))
        if stored is None:
            continue
        core = stored
        target = question_target(DEVIATION_RULE, raised)
        held[core]["deviations"].append(
            Deviation(
                word=found.group("word"),
                way="added" if found.group("way") == "added to" else "missing",
                experiment=path,
                question=target,
                answered=answered_again.find(target) is not None,
            )
        )
    return GroupResolutions(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        resolving_rules_in_run=bool(within or across or deviations),
        cores=[
            CoreResolution(
                core=list(terms),
                target=f"group:{' '.join(terms)}",
                approved_by=event.actor,
                approved_at=event.occurred_at,
                sequence=int(event.sequence),
                within=held[terms]["within"],
                across=held[terms]["across"],
                deviations=held[terms]["deviations"],
            )
            for terms, event in approved
        ],
    )


# ---------------------------------------------------------------------------
# ``GET /api/v1/groups/lattice``: approvals as a lattice of additive derivations.
# ---------------------------------------------------------------------------

LatticeState = Literal["approved", "declined", "asked", "waiting", "declined_above", "not_asked"]


class LatticeSet(ApiModel):
    """One shared set of words: the experiments holding it, what it adds to, and its state."""

    terms: list[str]
    #: The same words as the run's files wrote them, in the same order: a bound word is its
    #: Dictionary term in the lattice (``groups.words_as``), shown in the spelling most of the files
    #: hold. The key and the target stay ``terms``.
    terms_shown: list[str] = Field(default_factory=list)
    #: What its parents share, which it adds to; empty for a most general set.
    base: list[str]
    #: What it adds to ``base``: every term of a most general set.
    added: list[str]
    #: The sets immediately more general, each its terms.
    parents: list[list[str]]
    #: The experiments holding it, by folder path.
    experiments: list[str]
    state: LatticeState
    #: The Record target its answer is written on, ``group:<terms>``.
    target: str
    #: The question the pinned run raised about it, answerable on the Questions page; None where
    #: the run did not (decided, waiting, asked only since a later answer, or asked under
    #: ``each_deviation``): such a set is answered on ``target``.
    question: str | None
    #: Who decided it and when, where a person approved or declined it (or a set standing for it).
    decided_by: str | None
    decided_at: str | None
    #: Where the answer named words no longer terms (a month's, a replicate's), the set as it was
    #: answered, and the words it is now read without; None and empty otherwise.
    answered_as: str | None = None
    read_without: list[str] = Field(default_factory=list)
    #: The experiments that answer reaches: those whose names still hold the words it is read
    #: without. The set is asked of the others holding it.
    read_for: list[str] = Field(default_factory=list)
    #: Who gave that earlier answer, and whether it was a yes: its own line, beside who decided the
    #: set as it stands.
    answered_by: str | None = None
    answered_yes: bool | None = None


class SetChangedByAnswers(ApiModel):
    """A set whose state differs between the lattice with the person's answers and without."""

    terms: list[str]
    state_without_answers: LatticeState
    state_with_answers: LatticeState


@register("group_lattice")
class GroupLattice(ApiModel):
    """The run's experiments' shared sets of words, ordered by inclusion, each with its state."""

    schema_version: str
    run_id: Identifier
    #: The mode the run's rule declares: ``lattice`` or ``each_deviation``. Under the second the
    #: sets are what the lattice would ask, shown to compare. ``none`` where no rule in the run
    #: resolves groups, as in a run of the shipped Rulebook alone, so nothing is asked.
    mode: Literal["lattice", "each_deviation", "none"]
    #: Whether the run holds the rules that resolve approved groups at all.
    resolving_rules_in_run: bool
    minimum_core_terms: Count
    #: The fewest experiments a most general set is held by to be asked (five by default). A page
    #: says a set held by fewer is not asked, and why.
    minimum_experiments: Count = DEFAULT_MINIMUM_EXPERIMENTS
    maximum_concepts: Count
    #: True where the lattice stopped at ``maximum_concepts``: the sets shown are then those of the
    #: experiments read first, in canonical order.
    bound_reached: bool
    #: How many experiments the sets are read over.
    experiments: Count
    sets: list[LatticeSet]
    #: True where the request passed ``untaught=1``: no person's answer was given to the lattice,
    #: every state is as the run's words alone make it.
    untaught: bool = False
    #: Under ``untaught``, the sets whose state is not what it would be with the person's answers
    #: given (asked only because a set it adds to was approved, approved, declined, declined
    #: above): the set's terms and both states. The sets themselves are the same either way, as
    #: they come from the experiments' words alone; empty otherwise, and where no answer was given.
    sets_changed_by_answers: list[SetChangedByAnswers] = Field(default_factory=list)


def _rules_in_run(catalog: Catalog) -> dict[str, tuple[str, dict[str, object]]]:
    """Every rule of the run: its predicate and its parameters' values, decoded as declared."""
    try:
        rows = catalog.query(
            "SELECT rule_id, predicate, parameters_json FROM rules ORDER BY rule_id"
        ).fetchall()
    except Exception:  # a run without a rules table resolves nothing
        return {}
    found: dict[str, tuple[str, dict[str, object]]] = {}
    for rule_id, predicate, encoded in rows:
        values: dict[str, object] = {}
        for declared in json.loads(encoded or "[]"):
            try:
                values[str(declared["name"])] = json.loads(declared.get("default"))
            except (TypeError, ValueError):
                values[str(declared["name"])] = declared.get("default")
        found[str(rule_id)] = (str(predicate), values)
    return found


def _names(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _experiment_records(catalog: Catalog, parameters: Mapping[str, object]) -> list[Any]:
    """The records the lattice reads: every folder, and the files whose words are terms.

    The function reads only what the term source reads, because the Groupings page must not
    assemble a whole pool. It reads the folders, with the experiment rules' claims, and, by
    ``term_source``, the files an acquisition rule concluded on, every file, or none. Each comes
    with its name's components and bindings. These are the run's own records, read from its tables
    and handed to the run's own functions.
    """
    from ingence.core.schema import Claim, Component, DictionaryBinding, Entry
    from ingence.infer.subjects import EntryRecords
    from ingence.storage.views import view_from_row

    experiment_rules = _names(parameters.get("experiment_rules"))
    acquiring = _names(parameters.get("acquisition_rules"))
    source = str(parameters.get("term_source", ""))
    if source == "folder_and_files":
        files = "entry_type IN ('file', 'symlink')"
    elif source == "folder_and_acquisitions":
        files = (
            "entry_type IN ('file', 'symlink') AND entry_id IN (SELECT subject FROM claims "
            "WHERE list_contains(?, rule_id) AND resolution_status = 'resolved')"
        )
    else:
        files = "FALSE"
    wanted = f"NOT is_deleted AND (entry_type = 'directory' OR ({files}))"
    arguments: list[object] = [acquiring] if source == "folder_and_acquisitions" else []

    def rows(sql: str, extra: Sequence[object] = ()) -> list[dict[str, Any]]:
        result = catalog.query(sql, [*extra, *arguments])
        names = [column[0] for column in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]

    within = f"(SELECT entry_id FROM entries WHERE {wanted})"  # noqa: S608 (fixed fragments)
    entries = [
        view_from_row(row, Entry)
        for row in rows(f"SELECT * FROM entries WHERE {wanted} ORDER BY entry_id")  # noqa: S608
    ]
    # Only the rows a term is read from: a name's words, and the resolved bindings that leave a
    # word out for its kind. Reading every component and binding of the folders and acquisitions
    # made the page wait seconds.
    components: dict[str, list[Any]] = {}
    for row in rows(
        "SELECT * FROM components WHERE component_type = 'word' "  # noqa: S608
        f"AND entry_id IN {within} ORDER BY entry_id, component_id"
    ):
        components.setdefault(str(row["entry_id"]), []).append(view_from_row(row, Component))
    bindings: dict[str, list[Any]] = {}
    from ingence.grouping.identifiers import NOT_GROUP_WORD_KINDS

    # And a date's or a replicate's word, never a term whatever the rule declares.
    ignored = sorted({*_names(parameters.get("ignored_theme_kinds")), *NOT_GROUP_WORD_KINDS})
    # Under ``terms`` every resolved binding of a name's words is read (``groups.words_as``,
    # handed in the parameters). A bound word is its Dictionary term, so its binding is a term's
    # source.
    words_as = str(parameters.get("words_as", "terms"))
    every = words_as == "terms"
    for row in rows(
        "SELECT * FROM dictionary_bindings WHERE resolution_status = 'resolved' "  # noqa: S608
        f"AND (? OR list_contains(?, theme_kind)) AND entry_id IN {within} "
        "ORDER BY entry_id, binding_id",
        [every, ignored],
    ):
        bindings.setdefault(str(row["entry_id"]), []).append(view_from_row(row, DictionaryBinding))
    claims: dict[str, list[Any]] = {}
    for row in rows(
        "SELECT * FROM claims WHERE list_contains(?, rule_id) "  # noqa: S608
        f"AND subject IN {within} ORDER BY claim_id",
        [experiment_rules + acquiring],
    ):
        claims.setdefault(str(row["subject"]), []).append(view_from_row(row, Claim))
    return [
        EntryRecords(
            entry=entry,
            components=tuple(components.get(entry.entry_id, ())),
            bindings=tuple(bindings.get(entry.entry_id, ())),
            claims=tuple(claims.get(entry.entry_id, ())),
            words_as=words_as,
        )
        for entry in entries
    ]


#: A run's experiments and their terms, kept per run. Reading the folders and acquisitions is most
#: of the route's time and grows with the pool, and a published run's records never change. The
#: states are recomputed from the Record at every read. The few runs a page moves between are kept,
#: the oldest dropped first.
_TERMS_KEPT: dict[
    tuple[str, str, str],
    tuple[dict[str, frozenset[str]], dict[str, str], dict[str, frozenset[str]]],
] = {}
_RUNS_KEPT = 4
#: The cache is guarded because the server answers requests on threads. An entry is computed into
#: a local and only then kept, and keeping and dropping happen under one lock.
_TERMS_LOCK = threading.Lock()


def _term_sets(
    catalog: Catalog,
    project: object,
    run_id: str,
    parameters: Mapping[str, object],
    words_as: str = "terms",
) -> tuple[dict[str, frozenset[str]], dict[str, str], dict[str, frozenset[str]]]:
    """The experiments' terms, their labels, and each one's words no longer terms, kept per
    run (and per reading of a bound word, ``groups.words_as``)."""
    from ingence.rulebook.predicates.experiments import experiment_term_sets_with_never

    # The reading of a bound word is one of the parameters the terms are read under.
    read_under = {**parameters, "words_as": words_as}
    key = (str(project), run_id, json.dumps(read_under, sort_keys=True, default=str))
    with _TERMS_LOCK:
        kept = _TERMS_KEPT.get(key)
    if kept is not None:
        return kept
    computed = experiment_term_sets_with_never(_experiment_records(catalog, read_under), read_under)
    with _TERMS_LOCK:
        _TERMS_KEPT.pop(key, None)
        while len(_TERMS_KEPT) >= _RUNS_KEPT:
            _TERMS_KEPT.pop(next(iter(_TERMS_KEPT)))
        _TERMS_KEPT[key] = computed
    return computed


def _word_map(catalog: Catalog, project: object, run_id: str, words_as: str) -> WordMap:
    """The run's bound words and their terms, kept per run (:meth:`WordMap.of_run`)."""
    del project, run_id  # the map is kept by the run's own directory
    return WordMap.of_run(catalog, words_as=words_as)


def _in_the_lattice_words(
    answers: Mapping[tuple[str, ...], Any], reading: WordMap
) -> dict[tuple[str, ...], Any]:
    """Stored approvals keyed by the words the lattice holds now; the first of two keeping a key."""
    moved: dict[tuple[str, ...], Any] = {}
    for terms, event in answers.items():
        for words in reading.answer(terms):
            moved.setdefault(words, event)
    return moved


@dataclass(frozen=True, slots=True)
class _LatticeRead:
    """What reading the lattice of the pinned run gives: shared by the lattice route and the
    compare route, so the sets one shows are exactly the sets the other compares, in the same
    words (``groups.words_as``) and shown the same way."""

    mode: str
    readers: bool
    asking: str | None
    parameters: Mapping[str, object]
    labels: dict[str, str]
    found: Any
    scoped: list[Any]
    changed: list[SetChangedByAnswers]
    raised: set[str]
    decided: dict[tuple[str, ...], Any]
    #: What the run made its groups of (``groups.words_as``, read through ``_said_or_refuse``:
    #: where the run keeps it is ``grouping/lattice_words``' business, and a run that cannot say
    #: is refused in words where it holds groups), the run's bound words and their terms, and
    #: each word as its files wrote it (only what a page shows moves).
    words_as: str
    reading: WordMap
    shown_as: dict[str, str]


def _read_lattice(
    context: V1Context, catalog: Catalog, run_id: str, *, untaught: bool = False
) -> _LatticeRead:
    """The pinned run's lattice as the Record's answers make it (or, ``untaught``, without them)."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.targets import group_terms_named_by
    from ingence.rulebook.predicates.experiments import (
        RESOLUTION_PREDICATES,
        derivation_mode,
        lattice_of,
        resolution_parameters,
        split_answers,
    )
    from ingence.rulebook.registry import PredicateNotEvaluableError

    taught: dict[tuple[str, ...], Any] = {}
    for target, event in sorted(latest_by_target(read_events(context.project)).items()):
        terms = group_terms_named_by(target)
        if terms is not None and event.kind in (RecordEventKind.CONFIRM, RecordEventKind.REJECT):
            taught[terms] = event
    rules = _rules_in_run(catalog)
    readers = [
        (rule_id, values)
        for rule_id, (predicate, values) in rules.items()
        if predicate in RESOLUTION_PREDICATES
    ]
    declared = next((values for _, values in readers if "term_source" in values), {})
    asking = next((rule_id for rule_id, (p, _) in rules.items() if p == DEVIATION_RULE), None)
    proposal = rules.get(str(declared.get("proposal_rule", "")))
    parameters = resolution_parameters(declared, proposal[1] if proposal else None)
    # Stored approvals are read in the words the lattice holds (``groups.words_as``): one given on
    # spellings finds its core when terms are read, and the reverse. The decisions keep their
    # authors and moments; only the words they name move.
    words_as = _said_or_refuse(catalog, bool(readers))
    reading = _word_map(catalog, context.project, run_id, words_as)
    taught = _in_the_lattice_words(taught, reading)
    # Untaught (``untaught=1``): the lattice as the run's words alone make it, passing no answer, so
    # a set that exists only because a person approved or declined another is absent; the response
    # names those sets.
    decided: dict[tuple[str, ...], Any] = {} if untaught else taught
    try:
        # No rule in the run resolves groups, so none asks either way.
        mode = derivation_mode(parameters) if readers else "none"
        held, labels, never_of = (
            _term_sets(catalog, context.project, run_id, parameters, words_as)
            if readers
            else ({}, {}, {})
        )

        # An earlier answer is read as the terms are now: a set answered holding a month's or a
        # replicate's word stands for the set without it, for the experiments whose names still hold
        # the word, and for no others.
        def lattice_given(answers: Mapping[tuple[str, ...], Any]) -> tuple[Any, list[Any]]:
            confirmed, scoped_yes = split_answers(
                [t for t, e in answers.items() if e.kind is RecordEventKind.CONFIRM],
                True,
                never_of,
            )
            declined, scoped_no = split_answers(
                [t for t, e in answers.items() if e.kind is RecordEventKind.REJECT],
                False,
                never_of,
            )
            both = [*scoped_yes, *scoped_no]
            return (
                lattice_of(held, parameters, approved=confirmed, declined=declined, scoped=both),
                both,
            )

        found, scoped = lattice_given(decided)
        changed: list[SetChangedByAnswers] = []
        if untaught and taught and readers:
            with_answers, _ = lattice_given(taught)
            without = {c.terms: c.state for c in found.concepts}
            changed = [
                SetChangedByAnswers(
                    terms=list(c.terms),
                    state_without_answers=without[c.terms],
                    state_with_answers=c.state,
                )
                for c in with_answers.concepts
                if c.terms in without and without[c.terms] != c.state
            ]
    except PredicateNotEvaluableError as error:
        raise ApiError(str(error), code="lattice_not_readable") from error
    raised = {value for value, _ in _deviations(catalog)}
    # A word shown as its files wrote it: the parts of a multi-part spelling, and, where the run
    # read terms, a term in the spelling most files hold (a spelling is its own text, and a term's
    # written form must not be read as one). Only what a page shows moves.
    shown_as = {**written_forms(catalog), **(written_terms(catalog) if words_as == "terms" else {})}
    return _LatticeRead(
        mode=mode,
        readers=bool(readers),
        asking=asking,
        parameters=parameters,
        labels=labels,
        found=found,
        scoped=scoped,
        changed=changed,
        raised=raised,
        decided=decided,
        words_as=words_as,
        reading=reading,
        shown_as=shown_as,
    )


@route("groups/lattice", model=GroupLattice)
def lattice(context: V1Context, query: Mapping[str, Sequence[str]]) -> GroupLattice:
    """The pinned run's lattice of shared sets, each with its state; nothing is written.

    Read by the run's own definitions (``experiment_term_sets``, ``lattice_of``), from its own
    claims and the Record's decisions, so the page shows exactly what the run asks. Under
    ``each_deviation`` the sets are what the lattice would ask, and none is a question of the run's.
    """
    from ingence.core.schema import RecordEventKind
    from ingence.grouping.resolutions import (
        ASKED,
        DEFAULT_MINIMUM_EXPERIMENTS,
        FEWEST_EXPERIMENTS,
    )
    from ingence.record.questions import question_target
    from ingence.record.targets import group_target

    untaught = (one(query, "untaught") or "").strip().casefold() in ("1", "true", "yes")
    with pinned_catalog(context) as (catalog, run_id):
        got = _read_lattice(context, catalog, run_id, untaught=untaught)
    mode, labels, found, scoped = got.mode, got.labels, got.found, got.scoped
    decided, parameters, asking, raised = got.decided, got.parameters, got.asking, got.raised
    shown_as = got.shown_as
    # A decision stands for the closed set with every experiment holding its words. Who decided a
    # set is the answer that gave it its state: a plain answer on its own words wins over one read
    # without a month's word, which keeps a line of its own (``answered_*``), so a later "no" is
    # never shown under an earlier approver's name.
    by_concept: dict[tuple[str, ...], Any] = {}
    by_earlier: dict[tuple[str, ...], Any] = {}
    answered: dict[tuple[str, ...], tuple[tuple[str, ...], frozenset[str]]] = {}
    read = {answer.answered_as: answer for answer in scoped}
    for terms, event in decided.items():
        answer = read.get(tuple(terms))
        now = answer.terms if answer is not None else tuple(terms)
        holding = [c for c in found.concepts if now and frozenset(now) <= frozenset(c.terms)]
        if not holding:
            continue
        nearest = max(holding, key=lambda c: (len(c.experiments), -len(c.terms)))
        if answer is None:
            by_concept.setdefault(nearest.terms, event)
            continue
        by_earlier.setdefault(nearest.terms, event)
        reached = answer.experiments & frozenset(nearest.experiments)
        answered.setdefault(nearest.terms, (tuple(terms), reached))
    for terms, event in by_earlier.items():
        concept_state = next(c.state for c in found.concepts if c.terms == terms)
        if terms not in by_concept and concept_state in ("approved", "declined"):
            by_concept[terms] = event  # the earlier answer alone gave it its state
    sets = []
    for concept in found.concepts:
        decision = by_concept.get(concept.terms)
        # The run's own question, where it raised one (a decision since may be ahead of the
        # run: a set asked by the Record's latest answers is answered on its group's target).
        asked_now = concept.state == ASKED and concept.value in raised
        sets.append(
            LatticeSet(
                terms=list(concept.terms),
                terms_shown=[written_word(term, shown_as) for term in concept.terms],
                base=list(concept.base),
                added=list(concept.added),
                parents=[list(parent) for parent in concept.parents],
                experiments=sorted(labels.get(e, e) for e in concept.experiments),
                state=concept.state,
                target=group_target(concept.terms),
                question=question_target(asking, concept.value) if asked_now and asking else None,
                decided_by=decision.actor if decision is not None else None,
                decided_at=decision.occurred_at if decision is not None else None,
                answered_as=" ".join(answered[concept.terms][0])
                if concept.terms in answered
                else None,
                read_without=sorted(
                    set(answered[concept.terms][0]) - set(concept.terms)
                    if concept.terms in answered
                    else ()
                ),
                read_for=sorted(labels.get(e, e) for e in answered[concept.terms][1])
                if concept.terms in answered
                else [],
                answered_by=by_earlier[concept.terms].actor
                if concept.terms in by_earlier
                else None,
                answered_yes=by_earlier[concept.terms].kind is RecordEventKind.CONFIRM
                if concept.terms in by_earlier
                else None,
            )
        )
    return GroupLattice(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        mode=mode,
        resolving_rules_in_run=got.readers,
        minimum_core_terms=int(str(parameters.get("minimum_core_terms", 2))),
        minimum_experiments=max(
            FEWEST_EXPERIMENTS,
            int(str(parameters.get("minimum_experiments", DEFAULT_MINIMUM_EXPERIMENTS))),
        ),
        maximum_concepts=found.maximum_concepts,
        bound_reached=found.bound_reached,
        experiments=len(labels),
        sets=sets,
        untaught=untaught,
        sets_changed_by_answers=got.changed,
    )


# ---------------------------------------------------------------------------
# ``GET /api/v1/groups/compare``: two to four groups, side by side.
# ---------------------------------------------------------------------------

#: How many groups one comparison takes: the fewest that can be compared, and the most a page can
#: draw apart by a mark and a dash of its own.
COMPARE_LEAST = 2
COMPARE_MOST = 4
#: The mark each group takes, in the order they were asked for.
COMPARE_TAGS = "ABCD"
#: What ends each word of ``words:<terms>`` in a request: a term may hold a space.
COMPARE_TERM_BAR = "|"

CompareKind = Literal["words", "line"]


class CompareExperiment(ApiModel):
    """One experiment of a compared group: its folder, and how many files it holds."""

    id: Identifier
    #: The experiment's folder path, as the lattice and the Map name it.
    label: str
    #: Live files in the folder and beneath it, none in an installed program's folders: the same
    #: count the Map gives the experiment.
    files: Count


class CompareGroup(ApiModel):
    """One group in the comparison: what it is, its words, its experiments and its files."""

    #: The group as it was asked for: ``words:<terms, bar between them>`` or ``line:<number>``.
    key: str
    kind: CompareKind
    #: The mark it carries on a page and on the Map: ``A`` to ``D``, in the order asked.
    tag: str
    #: The words of a set of words; the name of a line of work (a person's, else the engine's).
    label: str
    #: A set of words' terms; a line of work's distinctive words. Sorted.
    words: list[str]
    #: For a set of words, its state as the Groupings page shows it; null for a line of work.
    state: LatticeState | None
    experiments: list[CompareExperiment]
    #: Files in its experiments (the sum of theirs).
    files: Count
    #: Words of this group that no other compared group has.
    words_only: list[str]
    #: Experiments only this group holds, and the files in them.
    experiments_only: list[CompareExperiment]
    files_only: Count


class ComparePair(ApiModel):
    """What two of the compared groups share, each count beside the count it is out of."""

    first: str
    second: str
    words_shared: Count
    words_in_either: Count
    experiments_shared: Count
    experiments_in_either: Count
    files_shared: Count
    files_in_either: Count


@register("group_compare")
class GroupCompare(ApiModel):
    """``GET /api/v1/groups/compare?group=...``: two to four groups compared; nothing is written.

    A group is ``words:<terms>`` (a set of words as the Groupings page's lattice holds it, a bar
    between the terms since one may hold a space; its experiments are those the lattice gives it)
    or ``line:<number>`` (a line of work of the themes projection). Every count is given with what
    it is out of: the words in all of the groups out of the words in any of them, and the same for
    experiments and for files.
    """

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    groups: list[CompareGroup]
    words_in_all: list[str]
    words_in_either: Count
    experiments_in_all: list[CompareExperiment]
    experiments_in_either: Count
    #: Files in the experiments in all of the groups, out of the files in the experiments of any.
    files_in_all: Count
    files_in_either: Count
    #: Each two groups, in the order asked (three groups have three pairs, four have six).
    pairs: list[ComparePair]
    #: A word of a set of words as the run's files wrote it, where that is not the word itself
    #: (``groups.words_as``: a bound word is its Dictionary term in the lattice, and the label of a
    #: set is shown in the spelling most files hold). Read for every word of the comparison alike.
    shown_as: dict[str, str] = Field(default_factory=dict)


def _parse_group(text: str) -> tuple[CompareKind, str]:
    """A group as asked, by kind and canonical key; refused in words where it is neither kind."""
    kind, _, rest = text.strip().partition(":")
    kind = kind.casefold()
    if kind == "words":
        # A term may hold a space ("wild type"), so the terms are separated by a bar.
        terms = sorted({word.strip().casefold() for word in rest.split(COMPARE_TERM_BAR)} - {""})
        if terms:
            return "words", COMPARE_TERM_BAR.join(terms)
    elif kind == "line":
        try:
            number = int(rest)
        except ValueError:
            number = -1
        if number >= 0:
            return "line", str(number)
    message = (
        f"{text!r} is not a group: write words:<the words, each ended by a bar> or "
        "line:<a line of work's number>"
    )
    raise ApiError(message, parameter="group")


def _file_counts(catalog: Catalog, folders: Sequence[str]) -> dict[str, int]:
    """Live files in each folder and beneath it, none counted from an installed program's folders.

    The experiments' own definition (:func:`ingence.experiment_units.run_units`): so an
    experiment's count here is the count the Map and the Years view give it.
    """
    from ingence.experiment_units import run_units

    held = run_units(catalog)
    direct = catalog.query(
        "SELECT parent_entry_id, count(*) FROM entries "
        "WHERE NOT is_deleted AND entry_type = 'file' AND entry_kind = 'filesystem' "
        "AND parent_entry_id IS NOT NULL GROUP BY 1"
    ).fetchall()
    wanted = set(folders)
    below = dict.fromkeys(folders, 0)
    for parent, number in direct:
        at: str | None = str(parent)
        if at in held.programs:
            continue
        while at is not None:
            if at in wanted:
                below[at] += int(number)
            at = held.parents.get(at)
    return below


class _Found(NamedTuple):
    """A group found: its key (as the engine holds it, never as it was asked), its label, its words,
    its state and its experiments by label."""

    key: str
    label: str
    words: list[str]
    state: str | None
    members: dict[str, str]


def _words_group(spec: str, lat: _LatticeRead) -> _Found:
    """A set of words of the lattice: its key, label, words, state and experiments by label.

    The set is compared in the lattice's words and shown as the files wrote them
    (``groups.words_as``). It is found by the words the run's lattice holds. Where the request
    names them in another reading (a spelling where the run read terms, or the reverse), the run's
    own map of words moves them over, as it does a stored answer. The label is the set as its files
    spelled it. The words stay the lattice's, for the keys and the counts. The key is the found
    set's own terms, so a set asked in either reading has one key.
    """
    named = tuple(sorted(spec.split(COMPARE_TERM_BAR)))
    wanted = [frozenset(named)]
    wanted.extend(frozenset(words) for words in lat.reading.answer(named))
    for terms in wanted:
        for concept in lat.found.concepts:
            if frozenset(word.casefold() for word in concept.terms) == terms:
                shown = [written_word(word, lat.shown_as) for word in concept.terms]
                return _Found(
                    f"words:{COMPARE_TERM_BAR.join(sorted(concept.terms))}",
                    ", ".join(shown),
                    list(concept.terms),
                    str(concept.state),
                    {entry: lat.labels.get(entry, entry) for entry in concept.experiments},
                )
    if lat.mode == "none":
        message = "this run holds no sets of words: no rule in it resolves groups"
    else:
        message = f"no set of words {', '.join(named)!r} is in this run's lattice"
    raise ApiError(message, code="group_not_found", status=HTTPStatus.NOT_FOUND, parameter="group")


def _line_group(
    spec: str, built: dict[str, Any], names: dict[str, dict[str, str | None]]
) -> _Found:
    """A line of work: its key, name, words, no state, its experiments by folder path."""
    number = int(spec)
    for theme in built["themes"]:
        if theme["theme"] == number:
            named = _named_line(theme, names)
            return _Found(
                f"line:{number}",
                named,
                sorted(str(word) for word in theme["words"]),
                None,
                {
                    str(row["id"]): str(row["folder"])
                    for row in built["experiments"]
                    if row["theme"] == number
                },
            )
    message = f"this run has no line of work numbered {number}"
    raise ApiError(message, code="group_not_found", status=HTTPStatus.NOT_FOUND, parameter="group")


def _named_line(theme: dict[str, Any], names: dict[str, dict[str, str | None]]) -> str:
    """A line of work's name: the one a person gave it, else the engine's words for it."""
    from ingence.api.v1.themes import _named

    return str(_named(theme, names).get("person_label") or theme["label"])


@route("groups/compare", model=GroupCompare)
def compare(context: V1Context, query: Mapping[str, Sequence[str]]) -> GroupCompare:
    """Two to four groups compared: words, experiments and files, in common and apart.

    ``group`` is given two to four times, each ``words:<terms>`` (a set of the Groupings page's
    lattice) or ``line:<number>`` (a line of work). Nothing is written, and no model reads
    anything: each count is a set operation over what the lattice and the themes already served.
    """
    asked = [_parse_group(text) for text in query.get("group", [])]
    if len(set(asked)) != len(asked):
        message = "a group is named once; the same group twice compares nothing"
        raise ApiError(message, parameter="group")
    if not COMPARE_LEAST <= len(asked) <= COMPARE_MOST:
        message = (
            f"compare takes {COMPARE_LEAST} to {COMPARE_MOST} groups, each as group=words:<words> "
            f"or group=line:<number>; {len(asked)} were given"
        )
        raise ApiError(message, parameter="group")
    built: dict[str, Any] | None = None
    names: dict[str, dict[str, str | None]] = {}
    if any(kind == "line" for kind, _ in asked):
        from ingence.api.v1.themes import _projection, person_labels

        built = _projection(context, {})
        names = person_labels(context.project)
    with pinned_catalog(context) as (catalog, run_id):
        lat = (
            _read_lattice(context, catalog, run_id)
            if any(kind == "words" for kind, _ in asked)
            else None
        )
        found: list[_Found] = []
        for kind, spec in asked:
            if kind == "words" and lat is not None:
                found.append(_words_group(spec, lat))
            elif built is not None:
                found.append(_line_group(spec, built, names))
        # A group is named once, as the engine holds it: the same set asked once in each reading
        # (spellings and terms) is one set, found only now, and compares nothing with itself.
        keys = [group.key for group in found]
        if len(set(keys)) != len(keys):
            message = "a group is named once; the same group twice compares nothing"
            raise ApiError(message, parameter="group")
        everyone = sorted({entry for group in found for entry in group.members})
        counts = _file_counts(catalog, everyone)
    label_of = {entry: label for group in found for entry, label in group.members.items()}

    def made(entry: str) -> CompareExperiment:
        return CompareExperiment(id=entry, label=label_of[entry], files=counts.get(entry, 0))

    def listed(entries: set[str]) -> list[CompareExperiment]:
        return [made(entry) for entry in sorted(entries, key=lambda e: (label_of[e], e))]

    def files_of(entries: set[str]) -> int:
        return sum(counts.get(entry, 0) for entry in entries)

    words = [set(group.words) for group in found]
    members = [set(group.members) for group in found]
    groups = []
    for index, (one_found, (kind, _)) in enumerate(zip(found, asked, strict=True)):
        others_words = set().union(*(w for i, w in enumerate(words) if i != index))
        others = set().union(*(m for i, m in enumerate(members) if i != index))
        alone = members[index] - others
        groups.append(
            CompareGroup(
                key=one_found.key,
                kind=kind,
                tag=COMPARE_TAGS[index],
                label=one_found.label,
                words=sorted(one_found.words),
                state=one_found.state,
                experiments=listed(members[index]),
                files=files_of(members[index]),
                words_only=sorted(words[index] - others_words),
                experiments_only=listed(alone),
                files_only=files_of(alone),
            )
        )
    pairs = [
        ComparePair(
            first=groups[a].key,
            second=groups[b].key,
            words_shared=len(words[a] & words[b]),
            words_in_either=len(words[a] | words[b]),
            experiments_shared=len(members[a] & members[b]),
            experiments_in_either=len(members[a] | members[b]),
            files_shared=files_of(members[a] & members[b]),
            files_in_either=files_of(members[a] | members[b]),
        )
        for a in range(len(groups))
        for b in range(a + 1, len(groups))
    ]
    in_all = set.intersection(*members)
    in_either = set.union(*members)
    shown: dict[str, str] = {}
    for group in groups:
        for word in group.words if group.kind == "words" and lat is not None else ():
            written = written_word(word, lat.shown_as) if lat is not None else word
            if written != word:
                shown[word] = written
    return GroupCompare(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        groups=groups,
        words_in_all=sorted(set.intersection(*words)),
        words_in_either=len(set.union(*words)),
        experiments_in_all=listed(in_all),
        experiments_in_either=len(in_either),
        files_in_all=files_of(in_all),
        files_in_either=files_of(in_either),
        pairs=pairs,
        shown_as=dict(sorted(shown.items())),
    )
