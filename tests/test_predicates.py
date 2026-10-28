"""The rule predicates that can run today, against records constructed by hand.

Some of the Rulebook's predicates are registered but cannot be evaluated: each needs something the
inference layer does not provide, and its message names it. The tests below check that each says
so distinctly. The census produces everything about one entry, so every classifying predicate that
reads one entry's own records is evaluable. Those are tested here properly rather than declared.

The records are built in the test rather than produced by a run. A predicate
whose test needs a scan is a predicate nobody can reason about in isolation, and
the narrow subject each one takes is what makes building them by hand possible.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ingence.core.ids import (
    PROJECT_SCOPE,
    CaseSensitivity,
    Scope,
    ScopeKind,
    dictionary_term_id,
)
from ingence.core.schema import (
    BindingAlternative,
    Claim,
    Component,
    Defeater,
    DictionaryBinding,
    DictionaryOrigin,
    Entry,
    FailureClass,
    MatchQuality,
    Root,
    ScanError,
)
from ingence.core.tiers import ExtractionTier, ResolutionStatus
from ingence.dictionary.discover import COUNT_LABEL_CONSTRAINT, INDEX_LABEL_CONSTRAINT
from ingence.infer.evaluate import evaluate_on_entry, raise_on_entry
from ingence.infer.subjects import (
    CandidateSet,
    EntryInRoot,
    EntryRecords,
    NameSpellings,
    PairRecords,
    ScopeRecords,
)
from ingence.record.workflow import Workflow
from ingence.rulebook.load import load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.predicates.classify import _SLOTS, _slot_of
from ingence.rulebook.registry import (
    PREDICATES,
    Concluded,
    PredicateNotEvaluableError,
    Raised,
    Reads,
    Refusal,
)
from ingence.rulebook.resolve import EffectiveRulebook, effective_rulebook

load_builtins()


def _at_revision_3() -> EffectiveRulebook:
    """The book at revision 3, which holds the rules these tests read: revision 4 supersedes
    them, and each stays readable at the revision that held it."""
    return effective_rulebook(load_revisions(), at=3)


_EXTENSION_CONSTRAINT = "extension:format"


def _binding(
    term: str | None,
    theme_kind: str | None,
    *,
    constraint: str | None = None,
    quality: MatchQuality = MatchQuality.EXACT,
    status: ResolutionStatus = ResolutionStatus.RESOLVED,
) -> DictionaryBinding:
    """One binding, with only the fields a predicate reads made meaningful."""
    resolved = status is ResolutionStatus.RESOLVED
    return DictionaryBinding(
        binding_id=f"b:{term}:{constraint}",
        component_id=f"c:{term}",
        entry_id="e",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        constraint=constraint,
        extraction_tier=ExtractionTier.SEMANTIC,
        resolution_status=status,
        term_id=(
            dictionary_term_id(
                term=str(term), scope=PROJECT_SCOPE, origin=DictionaryOrigin.BUILTIN.value
            )
            if resolved
            else None
        ),
        term=term if resolved else None,
        quality=quality if resolved else None,
        theme_kind=theme_kind,
        alternatives=(),
    )


def _component(kind: str, value: str) -> Component:
    return Component(
        component_id=f"c:{kind}:{value}",
        entry_id="e",
        run_id="r",
        component_type=kind,
        normalized_value=value,
        normalizer="test",
        normalizer_version="1",
    )


def _entry(**changes: object) -> Entry:
    fields: dict[str, object] = {
        "entry_id": "e",
        "root_id": "root",
        "relative_path": "a/b.txt",
        "name": "b.txt",
        "depth": 1,
        "entry_type": "file",
        "extension": ".txt",
        "compound_extension": ".txt",
        "first_seen_run": "r",
        "last_seen_run": "r",
    }
    fields.update(changes)
    return Entry(**fields)


def _predicate(name: str) -> object:
    return PREDICATES.get(name)


# ---------------------------------------------------------------------------
# Reading a sense off a name.
# ---------------------------------------------------------------------------


def test_the_extension_rule_reads_only_its_own_axis() -> None:
    """A constraint on what may bind, not a guess at what probably means what."""
    predicate = _predicate("format_from_extension")
    subject = [
        _binding("molecular_dynamics", "technique"),
        _binding("markdown", "format", constraint=_EXTENSION_CONSTRAINT),
    ]
    assert predicate.evaluate(subject, {"theme_kind": "format"}) is not None
    assert predicate.evaluate([subject[0]], {"theme_kind": "format"}) is None


def test_the_extension_rule_reads_only_the_extension_slot() -> None:
    """A format word in a name is not that file's extension.

    The rule's sentence is "a file is of the format its extension names" and its
    rationale rests on an extension being what a filesystem carries. Reading a word
    anywhere in the name made both false: a TIFF exported under the name of the ND2 it
    came from was published as an ND2, on the strength of the word alone.
    """
    predicate = _predicate("format_from_extension")
    word = _binding("vendor_image_format", "format")
    extension = _binding("image_format", "format", constraint=_EXTENSION_CONSTRAINT)
    parameters = {"theme_kind": "format"}
    assert predicate.evaluate([word], parameters) is None
    assert predicate.evaluate([word, extension], parameters) == "image_format"
    assert predicate.evaluate([extension, word], parameters) == "image_format"


def test_the_extension_rules_abstention_never_blames_a_word_in_the_name() -> None:
    """A branch reading a slot the verdict never reads names the wrong token."""
    predicate = _predicate("format_from_extension")
    parameters = {"theme_kind": "format"}
    in_the_name = _conflict(
        "b:word",
        _alternative("markdown", "format"),
        _alternative("another_format", "format"),
        theme_kind="format",
    )
    explained = predicate.explain_abstention([in_the_name], parameters)
    assert explained.inputs == ()
    assert explained.failure_class is None


def test_a_conflicting_binding_is_abstained_on_rather_than_resolved() -> None:
    """``md`` really is both markdown and molecular dynamics.

    Picking one would invent a fact, and the conflict stays in the table for
    anything that goes looking, which is what the state exists for. Note that the
    axis is settled here even though the value is not, so a predicate reading only
    the theme kind would happily return an answer the Dictionary refused to give.
    """
    predicate = _predicate("format_from_extension")
    conflicting = DictionaryBinding(
        binding_id="b:conflict",
        component_id="c:md",
        entry_id="e",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        constraint=_EXTENSION_CONSTRAINT,
        resolution_status=ResolutionStatus.EXAMINED_CONFLICTING,
        theme_kind="format",
        alternative_count=2,
        alternatives=tuple(
            BindingAlternative(
                term_id=term_id,
                theme_kind="format",
                origin=DictionaryOrigin.BUILTIN,
                origin_rank=0,
                scope=PROJECT_SCOPE,
            )
            for term_id in ("one", "other")
        ),
    )
    assert conflicting.theme_kind == "format"
    assert predicate.evaluate([conflicting], {"theme_kind": "format"}) is None


def test_a_name_token_rule_excludes_the_entry_s_own_extension() -> None:
    """The extension exclusion holds where a rule reads, not only where the census writes.

    Counting a file's own extension as a name token counts one fact twice, on the
    most common token in the pool. The binding records which lookups ran under a
    constraint, so the exclusion reads that rather than guessing from a spelling.
    """
    predicate = _predicate("sense_from_name_tokens")
    subject = [
        _binding("notebook_format", "format", constraint=_EXTENSION_CONSTRAINT),
        _binding("protocol", "file_role"),
    ]
    assert predicate.evaluate(subject, {"theme_kind": "file_role"}) == ("protocol",)
    # The extension bound to a format and this axis is format, and it is still
    # excluded: the constraint is what disqualifies it, not the axis.
    assert predicate.evaluate(subject, {"theme_kind": "format"}) == ()


def test_two_spellings_of_one_sense_count_once() -> None:
    """Hits compound across distinct senses and never across spellings of one."""
    predicate = _predicate("sense_from_name_tokens")
    subject = [_binding("protocol", "file_role"), _binding("protocol", "file_role")]
    assert predicate.evaluate(subject, {"theme_kind": "file_role", "minimum_senses": 2}) == ()


def test_a_floor_of_two_senses_holds_and_is_reached_by_distinct_ones() -> None:
    predicate = _predicate("sense_from_name_tokens")
    subject = [_binding("protocol", "file_role"), _binding("figure", "file_role")]
    assert predicate.evaluate(subject, {"theme_kind": "file_role", "minimum_senses": 2}) == (
        "figure",
        "protocol",
    )


#: The stage rule reading pipeline stages alone.
_PIPELINE_STAGES = {"theme_kind": "stage", "minimum_senses": 1, "senses": ["processed", "analysis"]}
_EVERY_STAGE = {"theme_kind": "stage", "minimum_senses": 1}


def test_a_rule_naming_its_senses_reads_only_those_and_only_on_its_axis() -> None:
    """A word for a folder of miscellany sits on the stage axis and places a file in no pipeline."""
    predicate = _predicate("sense_from_name_tokens")
    subject = [
        _binding("unaffiliated", "stage"),
        _binding("processed", "stage"),
        _binding("analysis", "technique"),
    ]
    assert predicate.evaluate(subject, _EVERY_STAGE) == ("processed", "unaffiliated")
    assert predicate.evaluate(subject, _PIPELINE_STAGES) == ("processed",)
    unnamed = [subject[0], subject[2]]
    assert predicate.evaluate(unnamed, _PIPELINE_STAGES) == ()
    assert predicate.explain_abstention(unnamed, _PIPELINE_STAGES).reason == (
        "no name token bound to one of the named senses of kind stage"
    )


def test_a_floor_counts_only_the_senses_a_rule_names() -> None:
    predicate = _predicate("sense_from_name_tokens")
    subject = [_binding("unaffiliated", "stage"), _binding("processed", "stage")]
    parameters = {**_PIPELINE_STAGES, "minimum_senses": 2}
    assert predicate.evaluate(subject, parameters) == ()
    assert predicate.explain_abstention(subject, parameters).reason == (
        "1 distinct senses of kind stage, 2 asked for"
    )


def test_a_tie_fails_a_rule_naming_senses_only_where_a_named_sense_is_in_it() -> None:
    """Whichever of two unnamed senses a token meant, the rule would conclude nothing."""
    predicate = _predicate("sense_from_name_tokens")
    named = _conflict(
        "b:named", _alternative("processed", "stage"), _alternative("crude", "quality")
    )
    unnamed = _conflict(
        "b:unnamed", _alternative("raw", "stage"), _alternative("unaffiliated", "stage")
    )
    failed = predicate.explain_abstention([named], _PIPELINE_STAGES)
    assert (failed.status, failed.inputs) == (ResolutionStatus.EXAMINED_CONFLICTING, ("b:named",))
    passed_over = predicate.explain_abstention([unnamed], _PIPELINE_STAGES)
    assert passed_over.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    every = predicate.explain_abstention([unnamed], _EVERY_STAGE)
    assert every.status is ResolutionStatus.EXAMINED_CONFLICTING


def test_an_empty_list_of_senses_reads_none_rather_than_every_one() -> None:
    """Left out, every sense on the axis counts. Written as an empty list, none does."""
    predicate = _predicate("sense_from_name_tokens")
    subject = [_binding("processed", "stage")]
    assert predicate.evaluate(subject, {**_EVERY_STAGE, "senses": []}) == ()
    assert predicate.evaluate(subject, _EVERY_STAGE) == ("processed",)


def test_a_rule_names_a_sense_and_matches_it_by_name() -> None:
    """The reason the binding carries the term beside its identifier.

    A rule parameter holding a hash fails the product claim outright: a user is
    supposed to open the rule that licensed a conclusion and read it.
    """
    predicate = _predicate("class_from_bound_sense")
    subject = [_binding("vendor_image_format", "format", constraint=_EXTENSION_CONSTRAINT)]
    parameters = {"senses": ["vendor_image_format", "ephys_format"]}
    assert predicate.evaluate(subject, parameters) == "vendor_image_format"
    assert predicate.evaluate(subject, {"senses": ["ms_format"]}) is None


def test_a_single_sense_name_is_read_as_one_name_and_not_as_letters() -> None:
    """A hand-edited file spells a one-item list as a bare string sooner or later."""
    predicate = _predicate("class_from_bound_sense")
    subject = [_binding("macos_metadata", "quality")]
    assert predicate.evaluate(subject, {"senses": "macos_metadata"}) == "macos_metadata"


def test_a_surface_whose_specificity_is_punctuation_is_not_matched_loosely() -> None:
    """The defect this floor exists for, as the minimal case of its class.

    A surface owing its meaning to punctuation, matched after punctuation was
    stripped from both sides, is a match with no evidence left in it. The
    Dictionary grades it correctly, and the floor makes the rules read the grade, so the
    engine does not classify ordinary files as tool artefacts on the strength of a guess it has
    already labelled as one.

    The test is written over the grammar, not over one name: any surface that is punctuation
    plus a short stem collapses onto a common word, and the class is what the test pins.
    """
    predicate = _predicate("class_from_bound_sense")
    guessed = [_binding("archive_helper", "quality", quality=MatchQuality.LOOSE)]
    parameters = {"senses": ["archive_helper"]}
    assert predicate.evaluate(guessed, parameters) == "archive_helper"
    assert predicate.evaluate(guessed, {**parameters, "minimum_match_quality": "exact"}) is None
    # Folding case is not guessing, and a floor of exact refuses it anyway,
    # which is why the floor is three-valued rather than a boolean.
    folded = [_binding("archive_helper", "quality", quality=MatchQuality.CASE_FOLDED)]
    assert predicate.evaluate(folded, {**parameters, "minimum_match_quality": "case_folded"})
    assert predicate.evaluate(folded, {**parameters, "minimum_match_quality": "exact"}) is None


def test_an_unreadable_quality_floor_admits_everything_rather_than_nothing() -> None:
    """A misspelled floor must not silently switch a rule off.

    Failing open is the right direction here and only here: the floor narrows
    what a classifying rule accepts, so an unreadable one that failed closed
    would make a rule stop concluding with no error anywhere, which is the
    silent-drop failure the loader refuses for rules.
    """
    predicate = _predicate("class_from_bound_sense")
    subject = [_binding("archive_helper", "quality", quality=MatchQuality.LOOSE)]
    parameters = {"senses": ["archive_helper"], "minimum_match_quality": "very_exact"}
    assert predicate.evaluate(subject, parameters) == "archive_helper"


def test_a_rule_naming_no_sense_matches_nothing() -> None:
    """An empty list is an empty list, not a wildcard.

    The dangerous reading of an unset parameter is "everything", and a
    classifying rule that matched everything would label the whole pool.
    """
    predicate = _predicate("class_from_bound_sense")
    assert predicate.evaluate([_binding("macos_metadata", "quality")], {}) is None


#: The acquisition stage, concluded from an instrument's own format.
_ACQUISITION = {"senses": ["vendor_image_format", "ephys_format"], "concludes": "acquisition"}


def test_a_rule_naming_its_conclusion_concludes_it_and_cites_every_binding_that_matched() -> None:
    """An acquisition format means the stage acquisition, to which no binding resolved."""
    predicate = _predicate("class_from_bound_sense")
    extension = _binding("vendor_image_format", "format", constraint=_EXTENSION_CONSTRAINT)
    subject = [extension, _binding("raw", "stage")]
    assert predicate.evaluate(subject, _ACQUISITION) == Concluded(
        values=("acquisition",), inputs=(extension.binding_id,)
    )
    assert predicate.evaluate(subject, {"senses": _ACQUISITION["senses"]}) == "vendor_image_format"
    assert predicate.evaluate([subject[1]], _ACQUISITION) is None


def test_a_rule_naming_a_blank_conclusion_is_not_evaluable() -> None:
    predicate = _predicate("class_from_bound_sense")
    subject = [_binding("vendor_image_format", "format", constraint=_EXTENSION_CONSTRAINT)]
    with pytest.raises(PredicateNotEvaluableError, match="which is not a class"):
        predicate.evaluate(subject, {**_ACQUISITION, "concludes": " "})


def test_a_conclusion_resting_on_no_record_is_refused() -> None:
    with pytest.raises(ValueError, match="rests on"):
        Concluded(values=("acquisition",), inputs=())


def test_an_outcome_rests_on_the_records_its_conclusion_names_and_never_another_entry_s() -> None:
    """Carrying a fact across entries is substitution, which only identical licenses."""

    class ConcludesFrom:
        name = "concludes_from"
        version = "1"

        def __init__(self, *inputs: str) -> None:
            self.inputs = inputs

        def evaluate(self, subject: object, parameters: object) -> object:
            return Concluded(values=("acquisition",), inputs=self.inputs)

    rule = _at_revision_3().by_id("raw_data_from_acquisition_format")
    assert rule is not None
    own = _binding("vendor_image_format", "format", constraint=_EXTENSION_CONSTRAINT)
    records = EntryRecords(entry=_entry(), bindings=(own,))
    outcome = evaluate_on_entry(rule, ConcludesFrom(own.binding_id), records, reads=Reads.BINDINGS)
    assert (outcome.values, outcome.inputs) == (("acquisition",), (own.binding_id,))
    with pytest.raises(ValueError, match="not its own"):
        evaluate_on_entry(rule, ConcludesFrom("b:another_entry"), records, reads=Reads.BINDINGS)


def test_a_rule_reading_the_extension_passes_over_a_format_word_in_the_name() -> None:
    """A TIFF exported under the name of the ND2 it came from is not an acquisition."""
    predicate = _predicate("class_from_bound_sense")
    word = _binding("vendor_image_format", "format")
    extension = _binding("image_format", "format", constraint=_EXTENSION_CONSTRAINT)
    from_the_word = Concluded(values=("acquisition",), inputs=(word.binding_id,))
    assert predicate.evaluate([word, extension], {**_ACQUISITION, "slot": "extension"}) is None
    assert predicate.evaluate([word, extension], _ACQUISITION) == from_the_word
    assert predicate.evaluate([word, extension], {**_ACQUISITION, "slot": "name"}) == from_the_word
    with pytest.raises(PredicateNotEvaluableError, match="'folder'"):
        predicate.evaluate([word], {**_ACQUISITION, "slot": "folder"})


def test_a_predicate_states_one_version_and_the_registry_agrees_with_it() -> None:
    """A version written twice is a version that can disagree.

    ``Rule.predicate_version`` comes off the registration and enters the digest,
    while the class carries a version of its own for a reader. Bumping only the class
    leaves the digest unmoved, so a rule whose verdict has changed still hashes as the
    rule that ran before it -- which is the hash failing at the one job it has.
    """
    for name in sorted(PREDICATES.names()):
        declared = str(PREDICATES.declaration(name).version)
        stated = getattr(PREDICATES.get(name), "version", None)
        assert stated is not None, f"{name} states no version"
        assert str(stated) == declared, (name, stated, declared)


# ---------------------------------------------------------------------------
# An empty file nobody has written to since a stated date.
# ---------------------------------------------------------------------------

_LAST_YEAR = "2025-09-16T00:00:00Z"
#: 1 January 2025 and 1 January 2026, in nanoseconds, either side of that date.
_BEFORE_NS = 1_735_689_600_000_000_000
_AFTER_NS = 1_767_225_600_000_000_000


def test_content_unreadable_says_which_way_the_content_is_out_of_reach() -> None:
    """``content_unreadable`` replaces ``empty_and_untouched``.

    The old rule paired emptiness with age and read the pair as a stub standing for something
    held elsewhere. On real laboratory folders, no file it raised stood for anything held elsewhere.
    The pair is kept as a plain fact: an empty file that nothing has written to since the rule's
    date is ``empty_and_old``, with no claim about an archive. A newer empty file is raised as
    ``empty``, which is the fact and no more.
    """
    predicate = _predicate("content_unreadable")
    parameters = {"written_before": _LAST_YEAR}

    old = predicate.evaluate(_entry(size_bytes=0, modified_ns=_BEFORE_NS), parameters)
    # An empty file older than the date is `empty_and_old`: what it is, and no claim about an
    # archive.
    assert old is not None and old.values == ("empty_and_old",)
    fresh = predicate.evaluate(_entry(size_bytes=0, modified_ns=_AFTER_NS), parameters)
    assert fresh is not None and fresh.values == ("empty",)
    assert predicate.evaluate(_entry(size_bytes=12, modified_ns=_BEFORE_NS), parameters) is None
    assert (
        predicate.evaluate(
            _entry(size_bytes=0, modified_ns=_BEFORE_NS, entry_type="directory"), parameters
        )
        is None
    )
    gone = predicate.evaluate(
        _entry(size_bytes=40, modified_ns=_AFTER_NS, entry_type="symlink", symlink_state="broken"),
        parameters,
    )
    assert gone is not None and gone.values == ("link_without_a_target",)
    # A link to a path outside the root is held elsewhere, one way.
    elsewhere = predicate.evaluate(
        _entry(
            size_bytes=40,
            modified_ns=_BEFORE_NS,
            entry_type="symlink",
            symlink_state="outside_root",
        ),
        parameters,
    )
    assert elsewhere is not None and elsewhere.values == ("link_outside_the_root",)
    # A link in a state nobody asked about is not raised.
    unasked = _entry(size_bytes=40, entry_type="symlink", symlink_state="unreadable")
    assert predicate.evaluate(unasked, parameters) is None
    other = predicate.evaluate(
        _entry(size_bytes=0, modified_ns=_AFTER_NS, entry_type="other"), parameters
    )
    assert other is not None and set(other.values) == {"not_a_regular_file"}


def test_content_unreadable_reads_a_date_and_never_the_clock() -> None:
    """A rule reading the wall clock would answer differently tomorrow.

    Two identical runs on identical input must produce identical output, so the date a person
    settled on is written into the rule rather than counted back from today on every run. A rule
    naming no date raises an empty file as empty, which is the half that needs no date.
    """
    predicate = _predicate("content_unreadable")
    entry = _entry(size_bytes=0, modified_ns=_BEFORE_NS)
    assert predicate.evaluate(entry, {"written_before": _LAST_YEAR}).values == ("empty_and_old",)
    for unreadable in ("", "   ", "last Tuesday", None, 1757980800000000000):
        assert predicate.evaluate(entry, {"written_before": unreadable}).values == ("empty",), (
            unreadable
        )
    # A date with no zone is read as UTC: a boundary that moved with the reader would not be one.
    assert predicate.evaluate(entry, {"written_before": "2025-09-16"}).values == ("empty_and_old",)


def test_content_unreadable_abstains_where_the_content_is_there_to_read() -> None:
    """And says which of the three situations it is: a folder, a file with bytes, or no size."""
    predicate = _predicate("content_unreadable")
    parameters = {"written_before": _LAST_YEAR}
    holds_bytes = predicate.explain_abstention(
        _entry(size_bytes=12, modified_ns=_BEFORE_NS), parameters
    )
    assert "holds bytes" in holds_bytes.reason
    folder = predicate.explain_abstention(
        _entry(size_bytes=0, modified_ns=_AFTER_NS, entry_type="directory"), parameters
    )
    assert "no content of its own" in folder.reason


def test_a_filesystem_that_reports_no_size_leaves_it_unexamined() -> None:
    """Treating silence as "its content is readable" would assert something nothing established.

    Unexamined and carrying no failure class: a rule never fails to reach a file, because access
    is the scan layer's failure to report and not a rule's. The registry refuses an
    abstention that claims otherwise.
    """
    predicate = _predicate("content_unreadable")
    explained = predicate.explain_abstention(
        _entry(size_bytes=None, modified_ns=_BEFORE_NS), {"written_before": _LAST_YEAR}
    )
    assert explained.status is ResolutionStatus.UNEXAMINED
    assert explained.failure_class is None


def test_every_marker_the_binder_records_names_a_slot_a_rule_can_read() -> None:
    """The tripwire between the two halves of one convention.

    The binder writes ``<slot>:<restriction>`` and a rule reads the slot back off it.
    Nothing but this test connects them, and while the extension was the only restricted
    slot, reading presence instead of the prefix gave the same answer -- so a second
    marker was the first thing that could have made them disagree silently.

    Imported here rather than at the top, because these tests otherwise reach a
    predicate without the Dictionary package loaded, and that independence is worth
    keeping.
    """
    from ingence.dictionary import discover

    markers = {
        value
        for name, value in vars(discover).items()
        if name.endswith("_CONSTRAINT") and isinstance(value, str)
    }
    # Discovered rather than listed. A list would hold two markers while a third was added and
    # escaped it, which is the silent disagreement the docstring is about.
    assert len(markers) >= 3, "every marker the binder can record belongs in this sweep"
    assert discover.EXTENSION_CONSTRAINT.split(":", 1)[0] == "extension"
    assert discover.NUMBER_CONSTRAINT.split(":", 1)[0] == "name"
    assert discover.INDEX_LABEL_CONSTRAINT.split(":", 1)[0] == "name"
    for marker in markers:
        assert _slot_of(_binding("t", "format", constraint=marker)) in _SLOTS, marker
    assert _slot_of(_binding("t", "format")) == "name"


def test_a_number_in_a_name_is_the_names_binding_and_never_the_extensions() -> None:
    """A digit binds in the name slot, however its lookup was restricted."""
    predicate = _predicate("class_from_bound_sense")
    from ingence.dictionary.discover import NUMBER_CONSTRAINT

    number = _binding("vendor_image_format", "format", constraint=NUMBER_CONSTRAINT)
    concluded = Concluded(values=("acquisition",), inputs=(number.binding_id,))
    assert predicate.evaluate([number], {**_ACQUISITION, "slot": "extension"}) is None
    assert predicate.evaluate([number], {**_ACQUISITION, "slot": "name"}) == concluded


# ---------------------------------------------------------------------------
# What a whole name, or the start or end of one, spells.
# ---------------------------------------------------------------------------

_NOISE_SPELLINGS = {
    "macos_metadata": (".DS_Store", "._"),
    "editor_backup": ("~$", ".~lock"),
    "archive_helper": (".part",),
}
_NOISE = {
    "senses": ["macos_metadata", "editor_backup", "archive_helper"],
    "positions": ["whole", "prefix", "suffix"],
    "excluded_surfaces": ["~$"],
}


def _spelled(name: str) -> NameSpellings:
    return NameSpellings(
        entry=_entry(name=name, relative_path=f"a/{name}"), spellings=_NOISE_SPELLINGS
    )


def test_a_name_spelling_a_named_sense_whole_or_at_either_end_concludes_it() -> None:
    predicate = _predicate("sense_from_name_spelling")
    assert predicate.evaluate(_spelled(".DS_Store"), _NOISE) == ("macos_metadata",)
    assert predicate.evaluate(_spelled("._plate.tif"), _NOISE) == ("macos_metadata",)
    assert predicate.evaluate(_spelled(".~lock.table.ods#"), _NOISE) == ("editor_backup",)
    assert predicate.evaluate(_spelled("table.csv.part"), _NOISE) == ("archive_helper",)
    assert predicate.evaluate(_spelled("plate.tif"), _NOISE) == ()


def test_an_affix_leaves_a_name_beside_it_and_a_whole_name_is_only_read_whole() -> None:
    predicate = _predicate("sense_from_name_spelling")
    prefix_only = {**_NOISE, "positions": ["prefix"]}
    assert predicate.evaluate(_spelled("._"), prefix_only) == ()
    assert predicate.evaluate(_spelled("._"), _NOISE) == ("macos_metadata",)
    assert predicate.evaluate(_spelled("kept.DS_Store.notes"), _NOISE) == ()
    assert predicate.evaluate(_spelled("._plate.tif"), {**_NOISE, "positions": ["whole"]}) == ()


def test_a_spelling_the_rule_never_reads_concludes_nothing_and_says_so() -> None:
    """An office suite's lock file beside a table is somebody at work."""
    predicate = _predicate("sense_from_name_spelling")
    lock = _spelled("~$table_processed.csv")
    assert predicate.evaluate(lock, _NOISE) == ()
    explained = predicate.explain_abstention(lock, _NOISE)
    assert (explained.inputs, explained.reason) == (
        ("e",),
        "the name is spelled with ~$, which the rule never reads",
    )
    assert predicate.evaluate(lock, {**_NOISE, "excluded_surfaces": []}) == ("editor_backup",)


def test_a_position_no_name_has_or_no_position_at_all_is_not_evaluable() -> None:
    predicate = _predicate("sense_from_name_spelling")
    with pytest.raises(PredicateNotEvaluableError, match="asks for within"):
        predicate.evaluate(_spelled(".DS_Store"), {**_NOISE, "positions": ["within"]})
    with pytest.raises(PredicateNotEvaluableError, match="names no position"):
        predicate.evaluate(_spelled(".DS_Store"), {**_NOISE, "positions": []})


# ---------------------------------------------------------------------------
# Reading structure off a name, before anything is looked up.
# ---------------------------------------------------------------------------


def test_structural_kinds_are_read_without_the_dictionary() -> None:
    """``rep3`` is an indexed label in any field, which is a firmer footing than a sense."""
    predicate = _predicate("structure_from_component_kind")
    subject = [
        _component("indexed", "rep:3"),
        _component("word", "plate"),
        _component("quantity", "molar:5e-07"),
    ]
    assert predicate.evaluate(subject, {"component_kinds": ["indexed"]}) == ("rep:3",)
    assert predicate.evaluate(subject, {"component_kinds": ["quantity"]}) == ("molar:5e-07",)
    assert predicate.evaluate(subject, {"component_kinds": ["date"]}) == ()


def test_two_spellings_of_one_normalised_quantity_are_one_value() -> None:
    """Forty minutes and two thousand four hundred seconds are one condition.

    The normaliser already reduced both, and comparing spellings would find two
    conditions where there is one.
    """
    predicate = _predicate("structure_from_component_kind")
    subject = [
        _component("quantity", "time_seconds:2400"),
        _component("quantity", "time_seconds:2400"),
    ]
    assert predicate.evaluate(subject, {"component_kinds": ["quantity"]}) == ("time_seconds:2400",)


# ---------------------------------------------------------------------------
# Reading a placeholder off the filesystem.
# ---------------------------------------------------------------------------


def test_a_symbolic_link_is_a_placeholder_and_needs_no_threshold() -> None:
    """The link half is what the filesystem says. Only the stub half is a heuristic."""
    predicate = _predicate("placeholder_from_entry_kind")
    assert predicate.evaluate(_entry(entry_type="symlink"), {}) == "symbolic_link"


def test_a_file_occupying_nothing_while_claiming_a_size_reads_as_a_stub() -> None:
    predicate = _predicate("placeholder_from_entry_kind")
    stub = _entry(size_bytes=4_000_000, allocated_bytes=0)
    assert predicate.evaluate(stub, {}) == "offline_stub"
    assert predicate.evaluate(stub, {"detect_stubs": False}) is None


def test_an_ordinary_empty_file_is_not_a_stub() -> None:
    """Claiming nothing and occupying nothing is an empty file everywhere."""
    predicate = _predicate("placeholder_from_entry_kind")
    assert predicate.evaluate(_entry(size_bytes=0, allocated_bytes=0), {}) is None


def test_an_entry_with_no_allocation_figure_abstains() -> None:
    """Not every filesystem reports one, and a schema that requires it lies somewhere."""
    predicate = _predicate("placeholder_from_entry_kind")
    assert predicate.evaluate(_entry(size_bytes=4_000_000), {}) is None


# ---------------------------------------------------------------------------
# The ones that cannot run, and say so distinctly.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "class_from_enclosing_folder",
        "concerns_by_found_sense",
        "derives_from_aggregation",
        "equivalent_by_multi_signal_design",
        "failure_concentrated_in_scope",
        "group_below_expected_size",
        "group_incomplete_against_workflow",
        "naming_is_authored_or_emitted",
        "readings_conflict",
        "relation_follows_the_clock",
        "same_content_digest",
        "substitution_rests_on_content",
        "succession_stays_within_one_series",
        "unverified_substitution_candidate",
        "vocabulary_unread_in_scope",
    ],
)
def test_a_predicate_ahead_of_the_inference_layer_says_so_distinctly(name: str) -> None:
    """Not evaluable is a third state, distinct from holding and from not holding.

    A rule that cannot be evaluated has produced no conclusion, which differs from
    a rule that produced none. Raising a distinct type is what keeps the Rulebook
    being ahead of the inference layer from looking like a rule failing, and the
    message names what is missing so that nobody has to guess which half.
    """
    with pytest.raises(PredicateNotEvaluableError) as raised:
        PREDICATES.get(name).evaluate(object(), {})
    message = str(raised.value)
    # Names itself, so a reader of a log knows which rule stopped, and then says
    # what is missing rather than only that something is.
    assert message.startswith(name)
    assert len(message) > len(name) + 40


#: What the inference layer has built, each with the words a message would use to name it. A
#: message naming one beside a denial says a built part is missing, which is how two predicates
#: went on saying candidate generation was not built after it was built. A phase that lands
#: adds its row, and every message still denying it fails in the same commit.
BUILT_PARTS: dict[str, tuple[str, ...]] = {
    "candidate generation": (
        "candidate generation",
        "bounded pairing",
        "candidate pair is generated",
        "draws a pair",
        "proposes edges",
    ),
    "grouping": ("grouping phase", "grouping/"),
    "the inference pass": ("inference pass", "infer/"),
    "the flag phase": ("flag phase", "publishes a raised finding"),
}

#: The words a message uses to say that something does not exist.
DENIALS = ("not built", "is not generated", "does not exist", "is empty", " yet")


def test_no_reason_for_not_evaluating_says_a_built_part_is_missing() -> None:
    """A predicate that cannot evaluate names what is missing, and never something that exists.

    Every registered predicate is asked, rather than a list of those known to raise, so one that
    starts raising is read too. A message that denies a part goes stale when that part is built,
    so every predicate is asked.
    """
    messages: dict[str, str] = {}
    for name in PREDICATES.names():
        try:
            PREDICATES.get(name).evaluate(object(), {})
        except PredicateNotEvaluableError as raised:
            messages[name] = str(raised)
        except Exception:
            # An evaluable predicate refusing a subject that is not one.
            continue
    assert "derives_from_name_family" in messages
    for name, message in sorted(messages.items()):
        lowered = message.casefold()
        if not any(denial in lowered for denial in DENIALS):
            continue
        for part, words in BUILT_PARTS.items():
            said = [word for word in words if word in lowered]
            assert not said, f"{name} says {part} is missing: {message!r}"


# ---------------------------------------------------------------------------
# Flags: what drew attention, and the records that did.
# ---------------------------------------------------------------------------

_DICTIONARY_READING = {"reading": "dictionary_binding", "minimum_alternatives": 2}


def _token(binding_id: str, raw: str) -> Component:
    """The name token a hand-built conflicting binding stands on."""
    return Component(
        component_id=f"c:{binding_id}",
        entry_id="e",
        run_id="r",
        component_type="word",
        normalized_value=raw.casefold(),
        raw_value=raw,
        normalizer="test",
        normalizer_version="1",
    )


def _tie() -> DictionaryBinding:
    return _conflict(
        "b:tie", _alternative("first_sense", "technique"), _alternative("second_sense", "unit")
    )


def test_a_tie_is_raised_with_the_binding_that_could_not_choose() -> None:
    """The token is what a person reads, and the binding is what the finding walks back to."""
    predicate = _predicate("readings_conflict")
    records = EntryRecords(entry=_entry(), components=(_token("b:tie", "Tie"),), bindings=(_tie(),))
    raised = predicate.evaluate(records, _DICTIONARY_READING)
    assert isinstance(raised, Raised)
    assert (raised.values, raised.inputs) == (("Tie",), ("b:tie",))
    assert raised.reason == "Tie stands between 2 senses of kind technique, unit"


def test_a_reason_reads_the_same_whatever_the_identifiers_its_bindings_carry() -> None:
    """Identifiers derive from the root, so one name under two roots read two orders."""
    predicate = _predicate("readings_conflict")

    def records(first_id: str, second_id: str) -> EntryRecords:
        first = _conflict(first_id, _alternative("a_sense", "technique"), _alternative("b", "unit"))
        second = _conflict(second_id, _alternative("c_sense", "stage"), _alternative("d", "unit"))
        return EntryRecords(
            entry=_entry(),
            components=(_token(first_id, "Zeta"), _token(second_id, "Alpha")),
            bindings=tuple(sorted((first, second), key=lambda binding: binding.binding_id)),
        )

    one = predicate.evaluate(records("b:1", "b:2"), _DICTIONARY_READING)
    other = predicate.evaluate(records("b:2", "b:1"), _DICTIONARY_READING)
    assert isinstance(one, Raised) and isinstance(other, Raised)
    assert one.reason == other.reason
    assert one.reason.startswith("Alpha stands between")


def test_a_tie_between_fewer_senses_than_the_rule_asks_for_is_not_raised() -> None:
    predicate = _predicate("readings_conflict")
    records = EntryRecords(entry=_entry(), components=(_token("b:tie", "Tie"),), bindings=(_tie(),))
    parameters = {**_DICTIONARY_READING, "minimum_alternatives": 3}
    assert predicate.evaluate(records, parameters) is None
    abstention = predicate.explain_abstention(records, parameters)
    assert abstention.inputs == ("b:tie",)
    assert abstention.reason == "a binding stood between fewer than 3 senses"


def test_two_readings_of_a_clock_are_not_compared_and_the_rule_says_so() -> None:
    """The clock rule is kept apart from the Dictionary rule, so neither fires on the other."""
    predicate = _predicate("readings_conflict")
    records = EntryRecords(entry=_entry(), bindings=(_tie(),))
    with pytest.raises(PredicateNotEvaluableError, match="event_time"):
        predicate.evaluate(records, {"reading": "event_time", "tolerance_seconds": 86400})


def test_a_named_doubt_term_in_a_name_is_raised_once_however_often_it_matched() -> None:
    predicate = _predicate("authored_name_doubt")
    parameters = {"senses": ["broken", "deprecated"], "minimum_terms": 1}
    bindings = [
        _binding("broken", "quality"),
        _binding("broken", "quality", quality=MatchQuality.CASE_FOLDED),
        _binding("export", "stage"),
    ]
    assert predicate.evaluate(bindings, parameters) == ("broken",)


def test_a_doubt_rule_asks_for_its_number_of_distinct_terms() -> None:
    predicate = _predicate("authored_name_doubt")
    parameters = {"senses": ["broken", "deprecated"], "minimum_terms": 2}
    once = [_binding("broken", "quality")]
    assert predicate.evaluate(once, parameters) is None
    assert predicate.explain_abstention(once, parameters).reason == (
        "1 of the named terms, 2 asked for"
    )
    both = [*once, _binding("deprecated", "quality")]
    assert predicate.evaluate(both, parameters) == ("broken", "deprecated")


def test_a_doubt_rule_naming_no_terms_is_not_evaluable() -> None:
    """No kind says which bindings are doubt: the lexicon's kind also holds filesystem noise."""
    predicate = _predicate("authored_name_doubt")
    with pytest.raises(PredicateNotEvaluableError, match="names none"):
        predicate.evaluate([_binding("broken", "quality")], {"minimum_terms": 1})


def test_what_the_extension_slot_bound_is_no_doubt_in_the_name() -> None:
    predicate = _predicate("authored_name_doubt")
    parameters = {"senses": ["broken"], "minimum_terms": 1}
    extension = _binding("broken", "quality", constraint=_EXTENSION_CONSTRAINT)
    assert predicate.evaluate([extension], parameters) is None


#: Three terms, two of them graded low (questionable and quarantine).
_GRADED = {
    "senses": ["broken", "questionable", "quarantine"],
    "low_strength_senses": ["questionable", "quarantine"],
    "minimum_terms": 1,
}


def test_a_low_strength_doubt_term_counts_only_beside_another() -> None:
    """A word such as weird is ordinary in names, and raises nothing alone."""
    predicate = _predicate("authored_name_doubt")
    alone = [_binding("questionable", "quality")]
    assert predicate.evaluate(alone, _GRADED) is None
    assert predicate.explain_abstention(alone, _GRADED).reason == (
        "the name carries only low-strength terms, which count beside another"
    )
    beside = [*alone, _binding("broken", "quality")]
    assert predicate.evaluate(beside, _GRADED) == ("broken", "questionable")


def test_a_confirmation_rule_raises_exactly_the_names_only_low_strength_terms_carry() -> None:
    predicate = _predicate("authored_name_doubt")
    confirming = {**_GRADED, "raises": "confirmation"}
    alone = [_binding("questionable", "quality"), _binding("quarantine", "quality")]
    assert predicate.evaluate(alone, confirming) == ("quarantine", "questionable")
    beside = [*alone, _binding("broken", "quality")]
    assert predicate.evaluate(beside, confirming) is None
    assert "stands alone" in predicate.explain_abstention(beside, confirming).reason


def test_a_doubt_rule_raising_anything_but_doubt_or_confirmation_is_not_evaluable() -> None:
    predicate = _predicate("authored_name_doubt")
    with pytest.raises(PredicateNotEvaluableError, match="'certainty'"):
        predicate.evaluate([_binding("broken", "quality")], {**_GRADED, "raises": "certainty"})


def test_a_finding_citing_another_entrys_records_is_refused() -> None:
    """A finding carrying a fact across entries is substitution, which only identical licenses."""

    class CitesElsewhere:
        name = "cites_elsewhere"
        version = "1"

        def evaluate(self, subject: object, parameters: object) -> object:
            return Raised(values=("elsewhere",), inputs=("b:another_entry",))

    rule = _at_revision_3().by_id("binding_is_ambiguous")
    assert rule is not None
    records = EntryRecords(entry=_entry(), bindings=(_binding("broken", "quality"),))
    with pytest.raises(ValueError, match="not its own"):
        raise_on_entry(rule, CitesElsewhere(), records, reads=Reads.RECORDS)


# ---------------------------------------------------------------------------
# Why a predicate abstained: what separates a starved rule from one that cannot decide.
# ---------------------------------------------------------------------------


def _alternative(term: str, theme_kind: str) -> BindingAlternative:
    return BindingAlternative(
        term_id=dictionary_term_id(
            term=term, scope=PROJECT_SCOPE, origin=DictionaryOrigin.BUILTIN.value
        ),
        theme_kind=theme_kind,
        origin=DictionaryOrigin.BUILTIN,
        origin_rank=0,
        scope=PROJECT_SCOPE,
    )


def _conflict(
    binding_id: str,
    *alternatives: BindingAlternative,
    theme_kind: str | None = None,
    constraint: str | None = None,
) -> DictionaryBinding:
    return DictionaryBinding(
        binding_id=binding_id,
        component_id=f"c:{binding_id}",
        entry_id="e",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        constraint=constraint,
        resolution_status=ResolutionStatus.EXAMINED_CONFLICTING,
        theme_kind=theme_kind,
        alternative_count=len(alternatives),
        alternatives=alternatives,
    )


def test_a_conflicting_extension_is_the_rule_unable_to_decide() -> None:
    """``md`` in a name is markdown or molecular dynamics, and nothing says which."""
    from ingence.core.schema import FailureClass

    predicate = _predicate("format_from_extension")
    conflict = _conflict(
        "b:md",
        _alternative("markdown", "format"),
        _alternative("another_format", "format"),
        theme_kind="format",
        constraint=_EXTENSION_CONSTRAINT,
    )
    parameters = {"theme_kind": "format"}
    assert predicate.evaluate([conflict], parameters) is None
    explained = predicate.explain_abstention([conflict], parameters)
    assert explained.status is ResolutionStatus.EXAMINED_CONFLICTING
    assert explained.failure_class is FailureClass.INTERPRETATION
    assert explained.inputs == ("b:md",)


def test_an_extension_nobody_taught_the_dictionary_starves_the_rule() -> None:
    """The remedy is vocabulary, never a looser rule, and the class is what says so."""
    from ingence.core.schema import FailureClass

    predicate = _predicate("format_from_extension")
    unknown = DictionaryBinding(
        binding_id="b:xyz",
        component_id="c:xyz",
        entry_id="e",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        constraint=_EXTENSION_CONSTRAINT,
        resolution_status=ResolutionStatus.EXAMINED_NO_EVIDENCE,
    )
    explained = predicate.explain_abstention([unknown], {"theme_kind": "format"})
    assert explained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert explained.failure_class is FailureClass.COMPREHENSION
    assert explained.inputs == ("b:xyz",)


def test_a_name_token_conflict_counts_only_against_the_axis_it_is_on() -> None:
    from ingence.core.schema import FailureClass

    predicate = _predicate("sense_from_name_tokens")
    conflict = _conflict(
        "b:ms", _alternative("mass_spectrometry", "technique"), _alternative("millisecond", "unit")
    )
    on_axis = predicate.explain_abstention([conflict], {"theme_kind": "technique"})
    off_axis = predicate.explain_abstention([conflict], {"theme_kind": "stage"})
    assert on_axis.failure_class is FailureClass.INTERPRETATION
    assert off_axis.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert off_axis.failure_class is None


def test_too_few_senses_is_the_rule_s_own_floor_and_not_a_failure() -> None:
    predicate = _predicate("sense_from_name_tokens")
    parameters = {"theme_kind": "file_role", "minimum_senses": 2}
    explained = predicate.explain_abstention([_binding("protocol", "file_role")], parameters)
    assert explained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert explained.failure_class is None
    assert explained.inputs == ("b:protocol:None",)


def test_a_named_sense_standing_equal_with_another_is_the_rule_unable_to_decide() -> None:
    """Matched by recomputing each wanted sense's identifier under the alternative's own scope."""
    from ingence.core.schema import FailureClass

    predicate = _predicate("class_from_bound_sense")
    parameters = {"senses": ["vendor_image_format"]}
    wanted = _conflict(
        "b:tok", _alternative("vendor_image_format", "format"), _alternative("a_word", "technique")
    )
    unrelated = _conflict(
        "b:ms", _alternative("mass_spectrometry", "technique"), _alternative("millisecond", "unit")
    )
    assert predicate.explain_abstention([wanted], parameters).failure_class is (
        FailureClass.INTERPRETATION
    )
    assert predicate.explain_abstention([unrelated], parameters).failure_class is None


def test_a_stub_cannot_be_told_from_a_file_where_no_allocation_is_reported() -> None:
    predicate = _predicate("placeholder_from_entry_kind")
    unreported = predicate.explain_abstention(_entry(size_bytes=4_000_000), {})
    reported = predicate.explain_abstention(_entry(size_bytes=10, allocated_bytes=16), {})
    assert unreported.status is ResolutionStatus.UNEXAMINED
    assert reported.status is ResolutionStatus.EXAMINED_NO_EVIDENCE


def test_an_abstention_can_be_neither_a_verdict_nor_a_failure_to_reach_the_file() -> None:
    from ingence.core.schema import FailureClass
    from ingence.rulebook.registry import Abstention

    with pytest.raises(ValueError, match="verdict"):
        Abstention(status=ResolutionStatus.RESOLVED)
    with pytest.raises(ValueError, match="access"):
        Abstention(failure_class=FailureClass.ACCESS)
    with pytest.raises(ValueError, match="interpretation"):
        Abstention(status=ResolutionStatus.EXAMINED_CONFLICTING)
    with pytest.raises(ValueError, match="interpretation"):
        Abstention(failure_class=FailureClass.INTERPRETATION)
    with pytest.raises(ValueError, match="comprehension"):
        Abstention(status=ResolutionStatus.UNEXAMINED, failure_class=FailureClass.COMPREHENSION)


# ---------------------------------------------------------------------------
# The first defeat condition.
# ---------------------------------------------------------------------------


def _observation(
    key: str, *, text: str | None = None, boolean: bool | None = None, integer: int | None = None
) -> object:
    from ingence.core.schema import Observation, ValueKind

    kind = ValueKind.TEXT
    if boolean is not None:
        kind = ValueKind.BOOLEAN
    elif integer is not None:
        kind = ValueKind.INTEGER
    return Observation(
        observation_id=f"o:{key}",
        entry_id="e",
        run_id="r",
        observer="deepen",
        observer_version="1",
        observed_at="t",
        key=key,
        value_kind=kind,
        value_text=text,
        value_integer=integer,
        value_boolean=boolean,
    )


_SIGNATURE = {"detector": "signature"}


def test_content_contradicting_its_extension_is_raised_with_what_the_content_is() -> None:
    predicate = _predicate("extension_contradicts_content")
    contradicted = [
        _observation("type.extension_agrees", boolean=False),
        _observation("type.opinion.signature", text="image/jpeg"),
    ]
    agreeing = [
        _observation("type.extension_agrees", boolean=True),
        _observation("type.opinion.signature", text="image/png"),
    ]
    assert predicate.evaluate(contradicted, _SIGNATURE) == "image/jpeg"
    assert predicate.evaluate(agreeing, _SIGNATURE) is None
    explained = predicate.explain_abstention(agreeing, _SIGNATURE)
    assert explained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert explained.inputs == ("o:type.extension_agrees",)


def test_a_disagreement_no_signature_stands_behind_is_not_a_contradiction() -> None:
    """Two vocabularies spelling one format differently is not a misnamed file.

    Where no signature matched, detection compares one detector's media type with a
    hint read from the extension, as strings, and the comparison disagrees whenever
    the two spell a format differently. It cannot retract a claim on its own.
    """
    predicate = _predicate("extension_contradicts_content")
    spelled_differently = [
        _observation("type.extension_agrees", boolean=False),
        _observation("type.opinion.database", text="text/x-one-spelling"),
        _observation("type.opinion.extension", text="text/x-another-spelling"),
    ]
    assert predicate.evaluate(spelled_differently, _SIGNATURE) is None
    explained = predicate.explain_abstention(spelled_differently, _SIGNATURE)
    assert explained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert explained.failure_class is None
    assert explained.inputs == ("o:type.extension_agrees",)


def test_every_signature_declares_the_extensions_a_contradiction_is_read_against() -> None:
    """A signature declaring none is compared as a string, which the defeat condition refuses."""
    from ingence.extractors.detect import SIGNATURES

    assert all(signature.extensions for signature in SIGNATURES)


def test_content_nobody_has_read_leaves_the_defeat_condition_unexamined() -> None:
    """Detection never runs in a census, and that is not the extension agreeing."""
    predicate = _predicate("extension_contradicts_content")
    assert predicate.evaluate([], _SIGNATURE) is None
    assert predicate.explain_abstention([], _SIGNATURE).status is ResolutionStatus.UNEXAMINED
    nothing_to_compare = [_observation("type.media_type", text="text/plain")]
    assert predicate.explain_abstention(nothing_to_compare, _SIGNATURE).status is (
        ResolutionStatus.EXAMINED_NO_EVIDENCE
    )


# ---------------------------------------------------------------------------
# A grouping predicate returns the class its subject belongs to.
# ---------------------------------------------------------------------------

_FAMILY = {"component_kinds": ["word"], "minimum_components": 2}


def test_names_agreeing_on_every_stated_component_share_a_key_whatever_their_order() -> None:
    predicate = _predicate("same_component_values")
    first = predicate.evaluate(
        [_component("word", "beta"), _component("word", "alpha"), _component("number", "3")],
        _FAMILY,
    )
    second = predicate.evaluate(
        [_component("number", "7"), _component("word", "alpha"), _component("word", "beta")],
        _FAMILY,
    )
    assert first.key == second.key
    assert first.inputs == ("c:word:alpha", "c:word:beta")


def test_a_name_differing_in_one_stated_component_is_in_another_class() -> None:
    predicate = _predicate("same_component_values")
    one = predicate.evaluate([_component("word", "alpha"), _component("word", "beta")], _FAMILY)
    other = predicate.evaluate([_component("word", "alpha"), _component("word", "gamma")], _FAMILY)
    assert one.key != other.key


def test_absence_on_both_sides_is_not_agreement() -> None:
    """Below the floor a name has no key, so two names saying nothing share no class."""
    predicate = _predicate("same_component_values")
    sparse = [_component("word", "alpha"), _component("number", "3")]
    assert predicate.evaluate(sparse, _FAMILY) is None
    assert predicate.evaluate([], _FAMILY) is None
    explained = predicate.explain_abstention(sparse, _FAMILY)
    assert explained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert explained.inputs == ("c:word:alpha",)


def test_the_kind_of_a_component_is_part_of_its_key() -> None:
    """A word and a number spelled alike are different components, and in different classes."""
    predicate = _predicate("same_component_values")
    kinds = {"component_kinds": ["word", "number"], "minimum_components": 1}
    word = predicate.evaluate([_component("word", "12")], kinds)
    number = predicate.evaluate([_component("number", "12")], kinds)
    assert word.key != number.key


def test_only_components_read_off_a_name_are_keyed() -> None:
    from ingence.core.schema import SourceScope

    predicate = _predicate("same_component_values")
    from_content = _component("word", "alpha").model_copy(
        update={"source_scope": SourceScope.CONTENT}
    )
    assert predicate.evaluate([from_content, _component("word", "beta")], _FAMILY) is None


_WITHOUT_STAGE_WORDS = {**_FAMILY, "ignored_theme_kinds": ["stage"]}
_CONDITION_WORDS = {
    "component_kinds": ["quantity", "word"],
    "minimum_components": 1,
    "counted_theme_kinds": ["condition", "reagent"],
}
_UNKNOWN = _binding(None, None, status=ResolutionStatus.EXAMINED_NO_EVIDENCE)


def _bound(component: Component, binding: DictionaryBinding) -> DictionaryBinding:
    """A binding made about one particular component."""
    return binding.model_copy(
        update={"component_id": component.component_id, "binding_id": f"b:{component.component_id}"}
    )


def _named(*words: tuple[str, DictionaryBinding]) -> list[Component | DictionaryBinding]:
    """Word components, each beside the binding the Dictionary made of it."""
    records: list[Component | DictionaryBinding] = []
    for value, binding in words:
        component = _component("word", value)
        records.extend((component, _bound(component, binding)))
    return records


def test_a_word_bound_to_an_ignored_kind_is_left_out_of_the_key() -> None:
    """A raw file and the file processed from it share a family once stage words are left out."""
    predicate = _predicate("same_component_values")
    raw = _named(("alpha", _UNKNOWN), ("beta", _UNKNOWN), ("raw", _binding("raw", "stage")))
    processed = _named(
        ("alpha", _UNKNOWN), ("beta", _UNKNOWN), ("processed", _binding("processed", "stage"))
    )
    assert predicate.evaluate(raw, _FAMILY).key != predicate.evaluate(processed, _FAMILY).key
    keyed = predicate.evaluate(raw, _WITHOUT_STAGE_WORDS)
    assert keyed.key == predicate.evaluate(processed, _WITHOUT_STAGE_WORDS).key
    assert keyed.inputs == ("c:word:alpha", "c:word:beta")


def test_a_word_standing_between_senses_stays_even_where_one_of_them_is_ignored() -> None:
    """Leaving the word out would decide a conflict the Dictionary could not."""
    predicate = _predicate("same_component_values")
    undecided = _conflict("x", _alternative("raw", "stage"), _alternative("crude", "quality"))
    keyed = predicate.evaluate(
        _named(("alpha", _UNKNOWN), ("beta", _UNKNOWN), ("raw", undecided)), _WITHOUT_STAGE_WORDS
    )
    assert keyed.inputs == ("c:word:alpha", "c:word:beta", "c:word:raw")


def test_only_words_bound_to_a_counted_kind_count_and_quantities_count_as_before() -> None:
    """A shared condition is its condition and reagent words and its quantities."""
    predicate = _predicate("same_component_values")
    dose = _component("quantity", "1e-08 molar")
    control = ("control", _binding("control", "condition"))
    keyed = predicate.evaluate([dose, *_named(("alpha", _UNKNOWN), control)], _CONDITION_WORDS)
    assert keyed.key == predicate.evaluate([dose, *_named(control)], _CONDITION_WORDS).key
    assert keyed.inputs == ("c:quantity:1e-08 molar", "c:word:control", "b:c:word:control")

    generic = _named(("alpha", _UNKNOWN), ("data", _binding("dataset", "dataset")))
    assert predicate.evaluate(generic, _CONDITION_WORDS) is None
    explained = predicate.explain_abstention(generic, _CONDITION_WORDS)
    assert explained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert "filters count" in explained.reason


def test_the_key_compares_words_so_two_words_for_one_sense_stay_apart() -> None:
    predicate = _predicate("same_component_values")
    mock = predicate.evaluate(_named(("mock", _binding("control", "condition"))), _CONDITION_WORDS)
    untreated = predicate.evaluate(
        _named(("untreated", _binding("control", "condition"))), _CONDITION_WORDS
    )
    assert mock.key != untreated.key


def test_a_word_the_binder_never_examined_leaves_a_filtered_name_unexamined() -> None:
    """Unfiltered, a name needs no binding. Filtered, a word nobody read cannot be counted."""
    predicate = _predicate("same_component_values")
    unread = [_component("word", "alpha"), _component("word", "beta")]
    assert predicate.evaluate(unread, _FAMILY) is not None
    assert predicate.evaluate(unread, _WITHOUT_STAGE_WORDS) is None
    explained = predicate.explain_abstention(unread, _WITHOUT_STAGE_WORDS)
    assert explained.status is ResolutionStatus.UNEXAMINED
    assert explained.inputs == ("c:word:alpha", "c:word:beta")


def test_one_entry_s_records_key_exactly_as_the_same_records_listed() -> None:
    from ingence.infer.subjects import EntryRecords

    predicate = _predicate("same_component_values")
    listed = _named(("alpha", _UNKNOWN), ("beta", _UNKNOWN), ("raw", _binding("raw", "stage")))
    records = EntryRecords(
        entry=_entry(),
        components=tuple(item for item in listed if isinstance(item, Component)),
        bindings=tuple(item for item in listed if isinstance(item, DictionaryBinding)),
    )
    for parameters in (_FAMILY, _WITHOUT_STAGE_WORDS):
        assert predicate.evaluate(records, parameters) == predicate.evaluate(listed, parameters)


#: A name identity: every word and every number, at least two of them.
_NAME_IDENTITY = {"component_kinds": ["word", "number"], "minimum_components": 2}
_WITHIN_A_FOLDER = {**_NAME_IDENTITY, "compared_within": "folder"}


def _in_folder(parent: str | None) -> EntryRecords:
    """One name, ``alpha`` and ``3``, on an entry sitting directly in ``parent``."""
    return EntryRecords(
        entry=_entry(parent_entry_id=parent),
        components=(_component("word", "alpha"), _component("number", "3")),
    )


def test_names_compared_within_a_folder_agree_only_beside_each_other() -> None:
    """One name in two output folders is two classes, so the class drawn from is the class cut."""
    predicate = _predicate("same_component_values")
    here, beside, elsewhere = _in_folder("f:one"), _in_folder("f:one"), _in_folder("f:two")
    across = predicate.evaluate(here, _NAME_IDENTITY)
    assert across.key == predicate.evaluate(elsewhere, _NAME_IDENTITY).key
    keyed = predicate.evaluate(here, _WITHIN_A_FOLDER)
    assert keyed.key == predicate.evaluate(beside, _WITHIN_A_FOLDER).key
    assert keyed.key != predicate.evaluate(elsewhere, _WITHIN_A_FOLDER).key
    assert keyed.inputs == ("c:number:3", "c:word:alpha", "e")


def test_a_name_compared_across_the_pool_keys_as_version_two_did() -> None:
    predicate = _predicate("same_component_values")
    across = {**_NAME_IDENTITY, "compared_within": "pool"}
    assert predicate.evaluate(_in_folder("f:one"), across) == predicate.evaluate(
        _in_folder("f:one"), _NAME_IDENTITY
    )


def test_a_name_no_folder_holds_has_no_key_within_a_folder() -> None:
    predicate = _predicate("same_component_values")
    root = _in_folder(None)
    assert predicate.evaluate(root, _NAME_IDENTITY) is not None
    assert predicate.evaluate(root, _WITHIN_A_FOLDER) is None
    explained = predicate.explain_abstention(root, _WITHIN_A_FOLDER)
    assert explained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert explained.reason == (
        "the rule compares names within one folder, and no folder holds this entry"
    )


#: An identity that keeps each number in the place the name wrote it.
_WITH_POSITIONS = {**_NAME_IDENTITY, "ordered_component_kinds": ["number"]}


def _tile(*numbers: str) -> EntryRecords:
    """One name: the word ``alpha`` and these numbers, in this order, each its own slot."""
    components = (
        _component("word", "alpha"),
        *(_component("number", value) for value in dict.fromkeys(numbers)),
    )
    by_value = {component.normalized_value: component.component_id for component in components}
    return EntryRecords(
        entry=_entry(),
        components=components,
        slots=("c:word:alpha", *(by_value[value] for value in numbers)),
    )


def test_an_identity_that_keeps_positions_tells_the_same_numbers_in_two_places_apart() -> None:
    """A plate reader writes a well, a field and a frame as numbers in fixed positions. A key that
    compared them as a set would make ``…_1_2_…_001`` and ``…_1_3_…_002`` one identity and mix
    classes."""
    predicate = _predicate("same_component_values")
    one_two = predicate.evaluate(_tile("1", "2"), _WITH_POSITIONS)
    two_one = predicate.evaluate(_tile("2", "1"), _WITH_POSITIONS)
    assert one_two.key != two_one.key
    # The same numbers in the same places are still one class, and repeats count.
    assert one_two.key == predicate.evaluate(_tile("1", "2"), _WITH_POSITIONS).key
    assert one_two.key != predicate.evaluate(_tile("1", "2", "1"), _WITH_POSITIONS).key
    # Without the parameter the key is exactly version 4's, which compares them as a set.
    assert (
        predicate.evaluate(_tile("1", "2"), _NAME_IDENTITY).key
        == predicate.evaluate(_tile("2", "1"), _NAME_IDENTITY).key
    )


def _conditions(*written: tuple[str, str]) -> EntryRecords:
    """One name giving each quantity to the word after it: ``10mM-kelt_0mM-vorn``."""
    components = tuple(
        dict.fromkeys(
            component
            for quantity, word in written
            for component in (_component("quantity", quantity), _component("word", word))
        )
    )
    return EntryRecords(
        entry=_entry(),
        components=components,
        slots=tuple(
            f"c:{kind}:{value}"
            for quantity, word in written
            for kind, value in (("quantity", quantity), ("word", word))
        ),
    )


def test_quantities_given_to_different_words_are_two_conditions_once_their_places_count() -> None:
    """``10mM-kelt_0mM-vorn`` and ``0mM-kelt_10mM-vorn`` name two conditions, and a key comparing
    quantities as a set would make them one identity. Keeping a quantity's place, as a number's,
    is a parameter."""
    predicate = _predicate("same_component_values")
    one = _conditions(("0.01 molar", "kelt"), ("0 molar", "vorn"))
    other = _conditions(("0 molar", "kelt"), ("0.01 molar", "vorn"))
    today = {**_WITH_POSITIONS, "component_kinds": ["word", "number", "quantity"]}
    assert predicate.evaluate(one, today).key == predicate.evaluate(other, today).key
    placed = {**today, "ordered_component_kinds": ["number", "quantity"]}
    assert predicate.evaluate(one, placed).key != predicate.evaluate(other, placed).key
    assert (
        predicate.evaluate(one, placed).key
        == predicate.evaluate(_conditions(("0.01 molar", "kelt"), ("0 molar", "vorn")), placed).key
    )


def test_a_name_whose_layout_this_run_did_not_record_cannot_be_keyed_on_positions() -> None:
    """A fact about a setting rather than about the name, so it is unexamined and says so."""
    predicate = _predicate("same_component_values")
    without = EntryRecords(
        entry=_entry(),
        components=(_component("word", "alpha"), _component("number", "3")),
    )
    assert predicate.evaluate(without, _NAME_IDENTITY) is not None
    assert predicate.evaluate(without, _WITH_POSITIONS) is None
    explained = predicate.explain_abstention(without, _WITH_POSITIONS)
    assert explained.status is ResolutionStatus.UNEXAMINED
    assert "recorded no layout" in explained.reason


def test_a_comparison_within_anything_but_the_pool_or_a_folder_is_not_evaluable() -> None:
    predicate = _predicate("same_component_values")
    with pytest.raises(PredicateNotEvaluableError, match="asks for 'root'"):
        predicate.evaluate(_in_folder("f:one"), {**_NAME_IDENTITY, "compared_within": "root"})


# ---------------------------------------------------------------------------
# A proposing or constraining predicate is handed a pair.
# ---------------------------------------------------------------------------

_TRANSITIONS = {"stage_transitions": {"raw": "processed", "processed": "analysis"}}
_IDENTITY = {"identity_bearing_kinds": ["subject", "condition"]}
_ROLES = {"source_roles": ["raw", "processed"], "target_roles": ["processed", "analysis"]}


def _stage_claim(
    subject: str, value: str, *, status: ResolutionStatus = ResolutionStatus.RESOLVED
) -> Claim:
    """A classification published about one end, standing or retracted."""
    return Claim(
        claim_id=f"claim:{subject}:{value}",
        run_id="r",
        subject=subject,
        predicate="classifies.stage_from_name",
        object_reference=value,
        rule_id="stage_from_name",
        rule_version="1",
        scope=PROJECT_SCOPE,
        defeaters=(Defeater(reason="a test defeater", rule_id="defeater", rule_version="1"),),
        resolution_status=status,
    )


def _end(
    entry_id: str, *claims: Claim, bindings: tuple[DictionaryBinding, ...] = ()
) -> EntryRecords:
    """One end of a pair: an entry, what its name bound to, and what was concluded about it."""
    return EntryRecords(entry=_entry(entry_id=entry_id), bindings=bindings, claims=claims)


def _pair(
    source: EntryRecords, target: EntryRecords, *, within: str = "same_name_family"
) -> PairRecords:
    return PairRecords(
        source=source,
        target=target,
        within=within,
        source_membership=f"member:{source.entry.entry_id}",
        target_membership=f"member:{target.entry.entry_id}",
    )


def _named_as(entry_id: str, term: str, theme_kind: str) -> DictionaryBinding:
    """A binding one end's name resolved to a sense of one kind."""
    return _binding(term, theme_kind).model_copy(
        update={"binding_id": f"b:{entry_id}:{term}", "entry_id": entry_id}
    )


def _timed(end: EntryRecords, hour: int) -> EntryRecords:
    """One end, last modified at the given hour of 1 March 2020 (UTC)."""
    at = (1_583_020_800 + hour * 3600) * 1_000_000_000
    return EntryRecords(
        entry=end.entry.model_copy(update={"modified_ns": at, "last_seen_run": "r"}),
        bindings=end.bindings,
        claims=end.claims,
    )


def test_a_declared_step_corroborates_a_derivation_and_on_its_own_proposes_nothing() -> None:
    """On its own, the stage step from raw to processed proposes nothing. It is a component of a
    derivation that the earlier file's time gives, where the step agrees."""
    stages = _predicate("derives_from_stage_transition")
    raw = _end("raw", _stage_claim("raw", "raw"))
    processed = _end("processed", _stage_claim("processed", "processed"))
    assert stages.evaluate(_pair(raw, processed), _TRANSITIONS) is None

    identity = _predicate("derives_within_a_name_identity")
    support = identity.evaluate(_pair(_timed(raw, 1), _timed(processed, 2)), _TRANSITIONS)
    assert [(component.name, component.observations) for component in support][-1] == (
        "stage_step",
        ("claim:processed:processed", "claim:raw:raw"),
    )
    # The later file is never the source, whatever its stage words say.
    assert identity.evaluate(_pair(_timed(processed, 1), _timed(raw, 2)), _TRANSITIONS) is not None
    assert identity.evaluate(_pair(_timed(raw, 2), _timed(processed, 1)), _TRANSITIONS) is None


def test_two_ends_on_one_rung_or_without_a_standing_stage_corroborate_nothing() -> None:
    """Siblings on one rung are not a step, and a retracted stage is not a stage. The derivation
    that the time gives stands without the component."""
    identity = _predicate("derives_within_a_name_identity")
    first = _timed(_end("first", _stage_claim("first", "processed")), 1)
    second = _timed(_end("second", _stage_claim("second", "processed")), 2)
    support = identity.evaluate(_pair(first, second), _TRANSITIONS)
    assert "stage_step" not in {component.name for component in support}

    retracted = _timed(
        _end("raw", _stage_claim("raw", "raw", status=ResolutionStatus.EXAMINED_CONFLICTING)), 0
    )
    support = identity.evaluate(_pair(retracted, second), _TRANSITIONS)
    assert "stage_step" not in {component.name for component in support}


def test_ends_naming_different_terms_on_an_identity_bearing_axis_are_refused() -> None:
    predicate = _predicate("components_do_not_conflict")
    control = _end("a", bindings=(_named_as("a", "control", "condition"),))
    treated = _end("b", bindings=(_named_as("b", "treatment", "condition"),))

    refusal = predicate.evaluate(_pair(control, treated), _IDENTITY)
    assert isinstance(refusal, Refusal)
    assert refusal.records == ("b:a:control", "b:b:treatment")
    assert "condition control against treatment" in refusal.reason


def test_silence_agreement_and_an_unsettled_word_are_never_a_conflict() -> None:
    predicate = _predicate("components_do_not_conflict")
    control = _end("a", bindings=(_named_as("a", "control", "condition"),))
    silent = _end("b")
    also_control = _end("c", bindings=(_named_as("c", "control", "condition"),))
    unsettled = _end("d", bindings=(_conflict("x", _alternative("treatment", "condition")),))

    assert predicate.evaluate(_pair(control, silent), _IDENTITY) is None
    assert predicate.evaluate(_pair(control, also_control), _IDENTITY) is None
    assert predicate.evaluate(_pair(control, unsettled), _IDENTITY) is None


def test_an_end_holding_no_eligible_role_is_refused_and_the_refusal_says_which() -> None:
    predicate = _predicate("endpoint_holds_an_eligible_role")
    raw = _end("raw", _stage_claim("raw", "raw"))
    processed = _end("processed", _stage_claim("processed", "processed"))
    summary = _end("summary", _stage_claim("summary", "summary"))
    unclassified = _end("unclassified")

    assert predicate.evaluate(_pair(raw, processed), _ROLES) is None
    both = predicate.evaluate(_pair(summary, unclassified), _ROLES)
    assert both.reason == (
        "the source holds none of the source roles; the target holds none of the target roles"
    )
    assert both.records == ("claim:summary:summary",)
    target = predicate.evaluate(_pair(raw, summary), _ROLES)
    assert target.reason == "the target holds none of the target roles"


def test_a_bound_says_how_many_counterparts_to_cut_and_never_which() -> None:
    predicate = _predicate("candidates_are_bounded_per_subject")
    offered = CandidateSet(
        subject=_end("subject"),
        counterparts=tuple(_end(f"counterpart_{number}") for number in range(5)),
        within="same_name_family",
    )
    assert predicate.evaluate(offered, {"maximum_candidates": 3}) == 2
    assert predicate.evaluate(offered, {"maximum_candidates": 25}) == 0
    with pytest.raises(TypeError, match="positive integer"):
        predicate.evaluate(offered, {"maximum_candidates": True})


_OBSERVED = {"compared_facts": ["name", "size_bytes", "modified_ns"]}


def _in_root(entry: Entry, sensitivity: CaseSensitivity = CaseSensitivity.UNKNOWN) -> EntryInRoot:
    """An entry beside the record of its root, whose filesystem ignores case or does not."""
    root = Root(
        root_id="root",
        root_uri="file:///pool",
        mount_path="/pool",
        case_sensitivity=sensitivity,
        first_seen_run="r",
        last_seen_run="r",
    )
    return EntryInRoot(entry=entry, root=root)


def test_entries_agreeing_on_every_compared_fact_share_a_class_and_one_fact_splits_it() -> None:
    predicate = _predicate("same_observed_metadata")
    first = predicate.evaluate(
        _in_root(_entry(entry_id="a", size_bytes=10, modified_ns=5)), _OBSERVED
    )
    copy = predicate.evaluate(
        _in_root(_entry(entry_id="b", size_bytes=10, modified_ns=5)), _OBSERVED
    )
    larger = predicate.evaluate(
        _in_root(_entry(entry_id="c", size_bytes=11, modified_ns=5)), _OBSERVED
    )
    assert first.key == copy.key
    assert first.key != larger.key
    assert first.inputs == ("a",)


def test_a_name_folds_case_only_where_its_root_is_known_to_ignore_case() -> None:
    """A root of unknown sensitivity compares names as stored, which never merges two files."""
    predicate = _predicate("same_observed_metadata")

    def key(name: str, sensitivity: CaseSensitivity) -> str:
        entry = _entry(name=name, size_bytes=10, modified_ns=5)
        return predicate.evaluate(_in_root(entry, sensitivity), _OBSERVED).key

    insensitive, sensitive = CaseSensitivity.INSENSITIVE, CaseSensitivity.SENSITIVE
    assert key("Plate.TIF", insensitive) == key("plate.tif", insensitive)
    assert key("Plate.TIF", sensitive) != key("plate.tif", sensitive)
    assert key("Plate.TIF", CaseSensitivity.UNKNOWN) != key("plate.tif", CaseSensitivity.UNKNOWN)
    assert key("café.tif", sensitive) == key("café.tif", sensitive)


def test_an_absent_fact_is_abstention_and_a_fact_it_cannot_read_is_not_evaluable() -> None:
    predicate = _predicate("same_observed_metadata")
    undated = _in_root(_entry(size_bytes=10))

    assert predicate.evaluate(undated, _OBSERVED) is None
    explained = predicate.explain_abstention(undated, _OBSERVED)
    assert explained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert "modified_ns" in explained.reason
    with pytest.raises(PredicateNotEvaluableError, match="inode"):
        predicate.evaluate(undated, {"compared_facts": ["name", "inode"]})


def test_folders_composed_alike_share_a_class_once_they_hold_enough_children() -> None:
    predicate = _predicate("same_composition")
    parameters = {"minimum_children": 3}
    held = [
        _observation("folder.composition_digest", text="composition_one"),
        _observation("folder.child_count", integer=4),
    ]
    sparse = [
        _observation("folder.composition_digest", text="composition_one"),
        _observation("folder.child_count", integer=2),
    ]
    key = predicate.evaluate(held, parameters)
    assert key.key == "composition_one"
    assert key.inputs == ("o:folder.child_count", "o:folder.composition_digest")
    assert predicate.evaluate(sparse, parameters) is None
    assert predicate.explain_abstention(sparse, parameters).status is (
        ResolutionStatus.EXAMINED_NO_EVIDENCE
    )


def test_a_folder_nobody_characterised_is_unexamined_rather_than_unlike_the_rest() -> None:
    predicate = _predicate("same_composition")
    explained = predicate.explain_abstention([], {"minimum_children": 3})
    assert explained.status is ResolutionStatus.UNEXAMINED


# ---------------------------------------------------------------------------
# An indexed component's label, read by the ignoring filter.
# ---------------------------------------------------------------------------


def _indexed(value: str, binding: DictionaryBinding) -> list[Component | DictionaryBinding]:
    """One indexed component beside what its label bound to."""
    component = _component("indexed", value)
    return [component, _bound(component, binding)]


_WITHOUT_REPLICATES = {
    "component_kinds": ["word", "indexed"],
    "minimum_components": 1,
    "ignored_theme_kinds": ["stage", "replicate"],
}


def test_two_replicates_of_one_experiment_share_an_identity() -> None:
    """An experiment is the class sharing a name identity once stage words and replicate
    components are left out. Because the label binds, ``rep1`` and ``rep2`` can be left out, and
    two files of one experiment stay in one class. Without the binding there would be nothing to
    leave out by.
    """
    predicate = _predicate("same_component_values")
    first = [
        *_named(("alpha", _UNKNOWN), ("beta", _UNKNOWN)),
        *_indexed("rep1", _binding("replicate", "replicate")),
    ]
    second = [
        *_named(("alpha", _UNKNOWN), ("beta", _UNKNOWN)),
        *_indexed("rep2", _binding("replicate", "replicate")),
    ]
    one = predicate.evaluate(first, _WITHOUT_REPLICATES)
    other = predicate.evaluate(second, _WITHOUT_REPLICATES)
    assert one is not None
    assert one.key == other.key


def test_an_index_whose_label_binds_nothing_still_separates() -> None:
    """The filter leaves out what it can read, and an unbound index is a distinction it cannot.

    On real folders many indexed labels bind nothing at all, so this is the ordinary case
    rather than the exception. Quietly dropping them would merge files that nothing says are
    related.
    """
    predicate = _predicate("same_component_values")
    first = [*_named(("alpha", _UNKNOWN)), *_indexed("q1", _UNKNOWN)]
    second = [*_named(("alpha", _UNKNOWN)), *_indexed("q2", _UNKNOWN)]
    one = predicate.evaluate(first, _WITHOUT_REPLICATES)
    other = predicate.evaluate(second, _WITHOUT_REPLICATES)
    assert one is not None and other is not None
    assert one.key != other.key


def test_a_counting_filter_keeps_every_index_it_is_given() -> None:
    """The asymmetry, asserted: counting names the whole basis of a key and an index carries a
    position rather than a meaning, so two doses written ``d1`` and ``d2`` stay two conditions.
    Dropping every index from a counted key would merge things that differ, and it would change
    the shipped condition rule.
    """
    predicate = _predicate("same_component_values")
    first = [
        *_named(("dmso", _binding("vehicle", "condition"))),
        *_indexed("d1", _UNKNOWN),
    ]
    second = [
        *_named(("dmso", _binding("vehicle", "condition"))),
        *_indexed("d2", _UNKNOWN),
    ]
    counted = {**_CONDITION_WORDS, "component_kinds": ["quantity", "word", "indexed"]}
    one = predicate.evaluate(first, counted)
    other = predicate.evaluate(second, counted)
    assert one is not None and other is not None
    assert one.key != other.key


def test_with_no_ignoring_filter_an_index_is_never_read() -> None:
    """A rule that ignores nothing keys exactly as version 3 did, so no shipped key moves."""
    predicate = _predicate("same_component_values")
    records = [*_named(("alpha", _UNKNOWN)), *_indexed("rep1", _binding("replicate", "replicate"))]
    plain = {"component_kinds": ["word", "indexed"], "minimum_components": 1}
    key = predicate.evaluate(records, plain)
    assert key is not None
    assert "rep1" in key.key


# ---------------------------------------------------------------------------
# A label the Dictionary misses, numbered like a replicate set.
# ---------------------------------------------------------------------------


def _labelled(entry_id: str, value: str, *, status: ResolutionStatus | None) -> EntryRecords:
    """One entry carrying one indexed component, bound as the caller says or not at all.

    ``status`` of None is a component the binder never looked at, which is a different state
    from one it looked at and found nothing for.
    """
    component = Component(
        component_id=f"c:{entry_id}:{value}",
        entry_id=entry_id,
        run_id="r",
        component_type="indexed",
        normalized_value=value,
        raw_value=value,
        normalizer="test",
        normalizer_version="1",
    )
    bindings: tuple[DictionaryBinding, ...] = ()
    if status is not None:
        bindings = (
            DictionaryBinding(
                binding_id=f"b:{entry_id}:{value}",
                component_id=component.component_id,
                entry_id=entry_id,
                run_id="r",
                resolver="test",
                resolver_version="1",
                lookup_scope=PROJECT_SCOPE,
                extraction_tier=ExtractionTier.SEMANTIC,
                resolution_status=status,
                term_id=(
                    dictionary_term_id(
                        term="placed", scope=PROJECT_SCOPE, origin=DictionaryOrigin.BUILTIN.value
                    )
                    if status is ResolutionStatus.RESOLVED
                    else None
                ),
                term="placed" if status is ResolutionStatus.RESOLVED else None,
                quality=(MatchQuality.EXACT if status is ResolutionStatus.RESOLVED else None),
                theme_kind="format" if status is ResolutionStatus.RESOLVED else None,
            ),
        )
    return EntryRecords(
        entry=_entry(entry_id=entry_id, parent_entry_id=f"folder:{entry_id[0]}"),
        components=(component,),
        bindings=bindings,
    )


def _scope(*records: EntryRecords) -> ScopeRecords:
    return ScopeRecords(scope=Scope(ScopeKind.ROOT, "root_x"), entries=records)


#: A run from 1 to 2, in two folders, of a label nothing placed.
_MARKER_SHAPE = {"maximum_index": 9, "minimum_folders": 2}

_UNPLACED = ResolutionStatus.EXAMINED_NO_EVIDENCE


def _run_from_one() -> ScopeRecords:
    return _scope(
        _labelled("a1", "qq1", status=_UNPLACED),
        _labelled("a2", "qq2", status=_UNPLACED),
        _labelled("b1", "qq1", status=_UNPLACED),
        _labelled("b2", "qq2", status=_UNPLACED),
    )


def test_a_label_numbered_from_one_across_two_folders_is_raised_with_what_carries_it() -> None:
    predicate = _predicate("indexed_label_may_mark_replicates")
    raised = predicate.evaluate(_run_from_one(), _MARKER_SHAPE)
    assert isinstance(raised, Raised)
    assert raised.values == ("qq",)
    assert raised.reason == "qq runs 1 to 2 across 2 folders and 4 files"
    assert len(raised.inputs) == 4


def test_a_label_numbered_with_a_gap_is_not_a_run_from_one() -> None:
    """A set of replicates is numbered from its first without a hole in the middle."""
    predicate = _predicate("indexed_label_may_mark_replicates")
    scope = _scope(
        _labelled("a1", "qq1", status=_UNPLACED),
        _labelled("b3", "qq3", status=_UNPLACED),
    )
    assert predicate.evaluate(scope, _MARKER_SHAPE) is None
    assert "without a gap" in predicate.explain_abstention(scope, _MARKER_SHAPE).reason


def test_a_run_reaching_past_the_maximum_is_a_version_and_not_a_set() -> None:
    predicate = _predicate("indexed_label_may_mark_replicates")
    scope = _scope(
        *(_labelled(f"a{index}", f"qq{index}", status=_UNPLACED) for index in range(1, 5)),
        *(_labelled(f"b{index}", f"qq{index}", status=_UNPLACED) for index in range(1, 5)),
    )
    assert predicate.evaluate(scope, {"maximum_index": 3, "minimum_folders": 2}) is None
    reason = predicate.explain_abstention(scope, {"maximum_index": 3, "minimum_folders": 2}).reason
    assert "past 3" in reason


def test_a_label_in_one_folder_alone_is_not_how_a_laboratory_marks_replicates() -> None:
    predicate = _predicate("indexed_label_may_mark_replicates")
    scope = _scope(
        _labelled("a1", "qq1", status=_UNPLACED),
        _labelled("a2", "qq2", status=_UNPLACED),
    )
    assert predicate.evaluate(scope, _MARKER_SHAPE) is None
    assert "2 or more folders" in predicate.explain_abstention(scope, _MARKER_SHAPE).reason


def test_a_label_the_dictionary_placed_is_a_settled_question_and_is_not_raised() -> None:
    """``nd2`` binds a vendor format whole, so asking what ``nd`` means asks a settled thing."""
    predicate = _predicate("indexed_label_may_mark_replicates")
    scope = _scope(
        _labelled("a1", "qq1", status=ResolutionStatus.RESOLVED),
        _labelled("a2", "qq2", status=ResolutionStatus.RESOLVED),
        _labelled("b1", "qq1", status=ResolutionStatus.RESOLVED),
        _labelled("b2", "qq2", status=ResolutionStatus.RESOLVED),
    )
    assert predicate.evaluate(scope, _MARKER_SHAPE) is None
    assert "the Dictionary misses" in predicate.explain_abstention(scope, _MARKER_SHAPE).reason


def test_a_component_the_binder_never_looked_at_is_not_a_label_the_dictionary_misses() -> None:
    """Never asked and asked-and-found-nothing are different states."""
    predicate = _predicate("indexed_label_may_mark_replicates")
    scope = _scope(
        _labelled("a1", "qq1", status=None),
        _labelled("a2", "qq2", status=None),
        _labelled("b1", "qq1", status=None),
        _labelled("b2", "qq2", status=None),
    )
    assert predicate.evaluate(scope, _MARKER_SHAPE) is None


def test_a_run_of_one_is_not_a_run() -> None:
    """A lone ``V1`` says nothing about replicates, however many folders hold one."""
    predicate = _predicate("indexed_label_may_mark_replicates")
    scope = _scope(
        _labelled("a1", "qq1", status=_UNPLACED),
        _labelled("b1", "qq1", status=_UNPLACED),
    )
    assert predicate.evaluate(scope, _MARKER_SHAPE) is None


# ---------------------------------------------------------------------------
# Which repetition a name says a file is.
# ---------------------------------------------------------------------------

#: The marker a lookup records when an indexed atom's *label* is what bound. A whole
#: text that bound carries no marker, and reading the slot rather than the marker would file
#: every digit in a name as a repetition.
_LABEL_MARKER = "name:replicate"


def _numbered(*labelled: tuple[str, str, str | None]) -> EntryRecords:
    """One entry whose name carries indexed components, each bound as the caller says.

    Each triple is the component's normalised value, the sense its label reached, and the
    marker the lookup recorded -- None for a binding the whole text made.
    """
    components = tuple(
        Component(
            component_id=f"c:{value}",
            entry_id="e",
            run_id="r",
            component_type="indexed",
            normalized_value=value,
            raw_value=value,
            normalizer="test",
            normalizer_version="1",
        )
        for value, _, _ in labelled
    )
    bindings = tuple(
        DictionaryBinding(
            binding_id=f"b:{value}",
            component_id=f"c:{value}",
            entry_id="e",
            run_id="r",
            resolver="test",
            resolver_version="1",
            lookup_scope=PROJECT_SCOPE,
            constraint=marker,
            extraction_tier=ExtractionTier.SEMANTIC,
            resolution_status=ResolutionStatus.RESOLVED,
            term_id=dictionary_term_id(
                term=term, scope=PROJECT_SCOPE, origin=DictionaryOrigin.BUILTIN.value
            ),
            term=term,
            quality=MatchQuality.EXACT,
            theme_kind="replicate" if marker == _LABEL_MARKER else "format",
        )
        for value, term, marker in labelled
    )
    return EntryRecords(entry=_entry(), components=components, bindings=bindings)


_REPLICATE_AXIS = {"theme_kind": "replicate", "minimum_match_quality": "exact"}


def test_a_bound_label_concludes_the_sense_and_the_index_together() -> None:
    """Neither alone is the fact: a sense without a number, or a number without a sense."""
    predicate = _predicate("index_of_bound_label")
    records = _numbered(("br2", "biological_replicate", _LABEL_MARKER))
    concluded = predicate.evaluate(records, _REPLICATE_AXIS)
    assert isinstance(concluded, Concluded)
    assert concluded.values == ('{"index":2,"sense":"biological_replicate"}',)
    assert concluded.inputs == ("b:br2",)


def test_two_senses_in_one_name_are_two_facts_and_both_are_concluded() -> None:
    """Three technical replicates of one dish is n = 1, so the two are never summed."""
    predicate = _predicate("index_of_bound_label")
    records = _numbered(
        ("br2", "biological_replicate", _LABEL_MARKER),
        ("tr3", "technical_replicate", _LABEL_MARKER),
    )
    concluded = predicate.evaluate(records, _REPLICATE_AXIS)
    assert isinstance(concluded, Concluded)
    assert concluded.values == (
        '{"index":2,"sense":"biological_replicate"}',
        '{"index":3,"sense":"technical_replicate"}',
    )


def test_one_sense_numbered_twice_is_a_conflict_and_never_a_choice() -> None:
    """Picking the first would make the answer depend on the order components were read."""
    predicate = _predicate("index_of_bound_label")
    records = _numbered(
        ("br1", "biological_replicate", _LABEL_MARKER),
        ("br2", "biological_replicate", _LABEL_MARKER),
    )
    assert predicate.evaluate(records, _REPLICATE_AXIS) is None
    abstention = predicate.explain_abstention(records, _REPLICATE_AXIS)
    assert abstention.status is ResolutionStatus.EXAMINED_CONFLICTING
    assert abstention.failure_class is FailureClass.INTERPRETATION
    assert abstention.inputs == ("b:br1", "b:br2")
    assert "nothing says which" in abstention.reason


def test_a_whole_text_that_bound_is_not_a_label_that_bound() -> None:
    """``nd2`` binds a vendor format whole. Its label never reached a sense."""
    predicate = _predicate("index_of_bound_label")
    records = _numbered(("nd2", "vendor_image_format", None))
    assert predicate.evaluate(records, _REPLICATE_AXIS) is None
    assert "no indexed label" in predicate.explain_abstention(records, _REPLICATE_AXIS).reason


def test_a_rule_may_narrow_to_the_senses_it_reads_on_the_axis() -> None:
    predicate = _predicate("index_of_bound_label")
    records = _numbered(
        ("br2", "biological_replicate", _LABEL_MARKER),
        ("tr3", "technical_replicate", _LABEL_MARKER),
    )
    narrowed = {**_REPLICATE_AXIS, "senses": ["biological_replicate"]}
    concluded = predicate.evaluate(records, narrowed)
    assert isinstance(concluded, Concluded)
    assert concluded.values == ('{"index":2,"sense":"biological_replicate"}',)


# ---------------------------------------------------------------------------
# A name that states a count, read two ways.
# ---------------------------------------------------------------------------

#: The default workflow's shape, which places raw at the rung where data enters and every
#: stage after it on a derivation.
_WORKFLOW = Workflow(name="default", rungs=("raw", "processed", "analysis", "pooled"))

#: What revision 4 declares: the bound, the count axis, and the stage rules.
_STATED_COUNT = {
    "maximum_count": 9,
    "theme_kind": "count",
    "stage_rules": ["stage_from_name"],
}


def _counted(
    index: int,
    *claims: Claim,
    workflows: tuple[Workflow, ...] = (_WORKFLOW,),
    kind: str = "count",
    constraint: str | None = COUNT_LABEL_CONSTRAINT,
) -> EntryRecords:
    """One entry whose name states a count, with whatever was claimed about its stage."""
    value = f"n{index}"
    component = Component(
        component_id=f"c:count:{value}",
        entry_id="e",
        run_id="r",
        source_scope="name",
        component_type="indexed",
        normalized_value=value,
        raw_value=f"N={index}",
        normalizer="test",
        normalizer_version="1",
    )
    binding = DictionaryBinding(
        binding_id=f"b:{value}",
        component_id=component.component_id,
        entry_id="e",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        extraction_tier=ExtractionTier.SEMANTIC,
        resolution_status=ResolutionStatus.RESOLVED,
        term_id=dictionary_term_id(
            term="stated_replicate_count",
            scope=PROJECT_SCOPE,
            origin=DictionaryOrigin.BUILTIN.value,
        ),
        term="stated_replicate_count",
        quality=MatchQuality.EXACT,
        theme_kind=kind,
        constraint=constraint,
    )
    return EntryRecords(
        entry=_entry(),
        components=(component,),
        bindings=(binding,),
        claims=claims,
        workflows=workflows,
    )


def test_a_count_on_a_derivation_is_raised_as_a_cardinal() -> None:
    """An analysis saying N=3 is saying what it rests on, which is the most obvious case of a
    cardinal."""
    predicate = _predicate("stated_count_may_mark_replicates")
    raised = predicate.evaluate(_counted(3, _stage_claim("e", "analysis")), _STATED_COUNT)
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":3,"reading":"cardinal"}',)
    assert "rests on 3 replicates" in raised.reason
    assert "past the rung where data enters" in raised.reason


def test_a_count_on_an_observation_is_raised_as_an_ordinal_and_never_claimed() -> None:
    """A person writing N=3 on raw data meaning "three exist" and one meaning "this is the
    third" produce the same string, and nothing in it says which."""
    predicate = _predicate("stated_count_may_mark_replicates")
    raised = predicate.evaluate(_counted(3, _stage_claim("e", "raw")), _STATED_COUNT)
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":3,"reading":"ordinal"}',)
    assert "cannot tell which reading was meant" in raised.reason


def test_a_file_no_rule_placed_is_read_as_an_ordinal_uncertainly() -> None:
    """Unplaced is an absence, and reading a count as a cardinal on one would be concluding
    that a file nothing placed is a derivation."""
    predicate = _predicate("stated_count_may_mark_replicates")
    raised = predicate.evaluate(_counted(2), _STATED_COUNT)
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":2,"reading":"ordinal"}',)
    assert "no rule placed this file at a stage" in raised.reason


def test_a_project_that_declared_no_workflow_gets_the_uncertain_reading() -> None:
    """A project that has declared none has none, and not the default: nothing says where
    data enters, so nothing separates the two readings."""
    predicate = _predicate("stated_count_may_mark_replicates")
    raised = predicate.evaluate(
        _counted(3, _stage_claim("e", "analysis"), workflows=()), _STATED_COUNT
    )
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":3,"reading":"ordinal"}',)
    assert "no workflow is declared" in raised.reason


def test_two_workflows_placing_one_stage_two_ways_are_reported_and_not_resolved() -> None:
    """Reported rather than resolved. The cardinal reading needs the file to be past
    the observation rung, and a stage that is past it in one workflow and at it in another
    is not."""
    predicate = _predicate("stated_count_may_mark_replicates")
    other = Workflow(name="instrument", rungs=("processed", "analysis"))
    records = _counted(3, _stage_claim("e", "processed"), workflows=(_WORKFLOW, other))
    raised = predicate.evaluate(records, _STATED_COUNT)
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":3,"reading":"ordinal"}',)
    assert "placed two ways" in raised.reason


def test_a_retracted_stage_claim_does_not_decide_the_reading() -> None:
    """A claim whose defeater fired is published as conflicting, and a conflicting
    classification is not what a second rule reads as settled."""
    predicate = _predicate("stated_count_may_mark_replicates")
    retracted = _stage_claim("e", "analysis", status=ResolutionStatus.EXAMINED_CONFLICTING)
    raised = predicate.evaluate(_counted(3, retracted), _STATED_COUNT)
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":3,"reading":"ordinal"}',)


_OUTRANKED = {
    **_STATED_COUNT,
    "observation_rules": ["instrument_rule"],
    "derivation_rules": ["program_rule"],
}


def _concluded(rule_id: str, value: str) -> Claim:
    """A claim from a rule outranking any workflow, about the counted entry."""
    return Claim(
        claim_id=f"claim:e:{rule_id}",
        run_id="r",
        subject="e",
        predicate=f"classifies.{rule_id}",
        object_reference=value,
        rule_id=rule_id,
        rule_version="1",
        scope=PROJECT_SCOPE,
        defeaters=(Defeater(reason="a test defeater", rule_id="defeater", rule_version="1"),),
        resolution_status=ResolutionStatus.RESOLVED,
    )


def test_a_program_stating_a_count_is_a_cardinal_whatever_the_workflow_says() -> None:
    """A program is a derivation by construction, so a script's N=3 states what it rests on,
    with no workflow declared and no stage claimed."""
    predicate = _predicate("stated_count_may_mark_replicates")
    records = _counted(3, _concluded("program_rule", "python_source"), workflows=())
    raised = predicate.evaluate(records, _OUTRANKED)
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":3,"reading":"cardinal"}',)
    assert "a program is a derivation" in raised.reason


def test_an_instrument_file_named_as_processed_is_still_an_observation() -> None:
    """The instrument's own extension outranks a stage word in the name and the workflow."""
    predicate = _predicate("stated_count_may_mark_replicates")
    records = _counted(3, _stage_claim("e", "analysis"), _concluded("instrument_rule", "raw"))
    raised = predicate.evaluate(records, _OUTRANKED)
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":3,"reading":"ordinal"}',)
    assert "instrument's own extension is an observation" in raised.reason


def test_two_readings_that_each_outrank_the_workflow_leave_the_uncertain_one() -> None:
    predicate = _predicate("stated_count_may_mark_replicates")
    records = _counted(
        3, _concluded("instrument_rule", "raw"), _concluded("program_rule", "python_source")
    )
    raised = predicate.evaluate(records, _OUTRANKED)
    assert isinstance(raised, Raised)
    assert raised.values == ('{"count":3,"reading":"ordinal"}',)
    assert "each outranks any workflow" in raised.reason


def test_a_count_past_the_bound_is_a_true_count_and_a_false_replicate_design() -> None:
    """A large N is both, and only a reader tells them apart -- which is why the bound is on the
    rule and not on the tokeniser."""
    predicate = _predicate("stated_count_may_mark_replicates")
    records = _counted(847, _stage_claim("e", "analysis"))
    assert predicate.evaluate(records, _STATED_COUNT) is None
    assert "past 9" in predicate.explain_abstention(records, _STATED_COUNT).reason


def test_a_label_that_bound_no_count_is_not_a_stated_count() -> None:
    """A replicate label is an ordinal that another rule concludes, and reading it here
    would read a position as a cardinality."""
    predicate = _predicate("stated_count_may_mark_replicates")
    records = _counted(3, kind="replicate", constraint=INDEX_LABEL_CONSTRAINT)
    assert predicate.evaluate(records, _STATED_COUNT) is None
    assert "kind count" in predicate.explain_abstention(records, _STATED_COUNT).reason


def test_a_rule_naming_an_axis_no_label_binds_is_not_evaluable() -> None:
    """The marker follows the axis rather than being written beside it, so an axis with no
    marker is a rule that could never fire and says so."""
    predicate = _predicate("stated_count_may_mark_replicates")
    with pytest.raises(PredicateNotEvaluableError) as raised:
        predicate.evaluate(_counted(3), {**_STATED_COUNT, "theme_kind": "instrument"})
    assert "no label binds a sense of kind 'instrument'" in str(raised.value)


# ---------------------------------------------------------------------------
# A folder whose vocabulary the Dictionary cannot read.
# ---------------------------------------------------------------------------

#: What the shipped rule declares.
_UNREAD = {
    "denominator": "word_occurrences",
    "maximum_unbound_share": 0.8,
    "minimum_occurrences": 4,
}

_NO_EVIDENCE = ResolutionStatus.EXAMINED_NO_EVIDENCE


def _worded(
    entry_id: str,
    *words: tuple[str, ResolutionStatus | None, int],
    kind: str = "word",
) -> EntryRecords:
    """One entry whose name held these components, each bound, unbound or never looked at."""
    components = []
    bindings = []
    for value, status, occurrences in words:
        component = Component(
            component_id=f"c:{entry_id}:{value}",
            entry_id=entry_id,
            run_id="r",
            source_scope="name",
            component_type=kind,
            normalized_value=value,
            raw_value=value,
            normalizer="test",
            normalizer_version="1",
            occurrences=occurrences,
        )
        components.append(component)
        if status is None:
            continue
        bindings.append(
            DictionaryBinding(
                binding_id=f"b:{entry_id}:{value}",
                component_id=component.component_id,
                entry_id=entry_id,
                run_id="r",
                resolver="test",
                resolver_version="1",
                lookup_scope=PROJECT_SCOPE,
                extraction_tier=ExtractionTier.SEMANTIC,
                resolution_status=status,
                term_id=(
                    dictionary_term_id(
                        term=value, scope=PROJECT_SCOPE, origin=DictionaryOrigin.BUILTIN.value
                    )
                    if status is ResolutionStatus.RESOLVED
                    else None
                ),
                term=value if status is ResolutionStatus.RESOLVED else None,
                quality=MatchQuality.EXACT if status is ResolutionStatus.RESOLVED else None,
                theme_kind="process" if status is ResolutionStatus.RESOLVED else None,
                alternative_count=2 if status is ResolutionStatus.EXAMINED_CONFLICTING else 0,
                alternatives=(
                    tuple(
                        BindingAlternative(
                            term_id=dictionary_term_id(
                                term=f"{value}_{which}",
                                scope=PROJECT_SCOPE,
                                origin=DictionaryOrigin.BUILTIN.value,
                            ),
                            theme_kind="process",
                            origin=DictionaryOrigin.BUILTIN,
                            origin_rank=0,
                            scope=PROJECT_SCOPE,
                        )
                        for which in ("one", "two")
                    )
                    if status is ResolutionStatus.EXAMINED_CONFLICTING
                    else ()
                ),
            )
        )
    return EntryRecords(
        entry=_entry(entry_id=entry_id, parent_entry_id="folder:a"),
        components=tuple(components),
        bindings=tuple(bindings),
    )


def _folder(*records: EntryRecords) -> ScopeRecords:
    return ScopeRecords(scope=Scope(ScopeKind.FOLDER, "folder:a"), entries=records)


def test_a_folder_whose_occurrences_mostly_reached_no_sense_is_raised() -> None:
    predicate = _predicate("vocabulary_unread_in_scope")
    scope = _folder(
        _worded("e1", ("zorbex", _NO_EVIDENCE, 3), ("quillan", _NO_EVIDENCE, 2)),
        _worded("e2", ("vramp", _NO_EVIDENCE, 1), ("wash", ResolutionStatus.RESOLVED, 1)),
    )
    raised = predicate.evaluate(scope, _UNREAD)
    assert isinstance(raised, Raised)
    assert raised.values == ("word_occurrences",)
    assert raised.reason == "6 of 7 occurrences reached no sense, a share of 0.86"
    assert len(raised.inputs) == 3


def test_an_occurrence_is_counted_as_often_as_it_occurred() -> None:
    """A folder whose every name repeats one unknown word is more illegible than one that
    uses it once, and the record keeps the number."""
    predicate = _predicate("vocabulary_unread_in_scope")
    resolved = ResolutionStatus.RESOLVED
    once = _folder(_worded("e1", ("zorbex", _NO_EVIDENCE, 1), ("wash", resolved, 5)))
    often = _folder(_worded("e1", ("zorbex", _NO_EVIDENCE, 5), ("wash", resolved, 1)))
    assert predicate.evaluate(once, _UNREAD) is None
    assert isinstance(predicate.evaluate(often, _UNREAD), Raised)


def test_a_conflicting_binding_is_the_dictionary_having_an_answer_and_not_lacking_one() -> None:
    predicate = _predicate("vocabulary_unread_in_scope")
    scope = _folder(
        _worded(
            "e1",
            ("zorbex", _NO_EVIDENCE, 1),
            ("sd", ResolutionStatus.EXAMINED_CONFLICTING, 3),
        )
    )
    assert predicate.evaluate(scope, _UNREAD) is None
    assert "a share of 0.25" in predicate.explain_abstention(scope, _UNREAD).reason


def test_a_component_the_binder_never_looked_at_is_neither_read_nor_unread() -> None:
    """Never asked and asked-and-found-nothing are different states. Folding them
    together would report a project with binding switched off as entirely illegible."""
    predicate = _predicate("vocabulary_unread_in_scope")
    scope = _folder(_worded("e1", ("zorbex", None, 4), ("quillan", None, 4)))
    assert predicate.evaluate(scope, _UNREAD) is None
    reason = predicate.explain_abstention(scope, _UNREAD).reason
    assert "looked at none of this region's 8 occurrences" in reason


def test_a_folder_below_the_floor_is_not_judged_by_its_share() -> None:
    predicate = _predicate("vocabulary_unread_in_scope")
    scope = _folder(_worded("e1", ("zorbex", _NO_EVIDENCE, 3)))
    assert predicate.evaluate(scope, _UNREAD) is None
    assert "fewer than the 4" in predicate.explain_abstention(scope, _UNREAD).reason


def test_the_wider_denominator_counts_the_kinds_that_intentionally_bind_nothing() -> None:
    """A date is not an unknown word, and a rule using the wrong denominator reports a folder
    of well-dated files as illegible."""
    predicate = _predicate("vocabulary_unread_in_scope")
    dated = _folder(_worded("e1", ("2026-01-04", _NO_EVIDENCE, 4), kind="date"))
    assert predicate.evaluate(dated, _UNREAD) is None
    wider = {**_UNREAD, "denominator": "all_kinds"}
    assert isinstance(predicate.evaluate(dated, wider), Raised)


def test_an_extension_is_counted_by_neither_denominator() -> None:
    """It is its own slot, looked up against formats alone, so a folder whose extensions have
    no format is a fact about detection rather than about anybody's vocabulary."""
    predicate = _predicate("vocabulary_unread_in_scope")
    scope = _folder(_worded("e1", ("zqx", _NO_EVIDENCE, 8), kind="extension"))
    wider = {**_UNREAD, "denominator": "all_kinds"}
    assert predicate.evaluate(scope, wider) is None
    assert "no occurrence of a counted kind" in predicate.explain_abstention(scope, wider).reason


def test_a_rule_declaring_a_denominator_nobody_counts_is_not_evaluable() -> None:
    """A share with no stated denominator is a number that cannot be compared with any other."""
    predicate = _predicate("vocabulary_unread_in_scope")
    scope = _folder(_worded("e1", ("zorbex", _NO_EVIDENCE, 8)))
    with pytest.raises(PredicateNotEvaluableError, match="word_occurrences or all_kinds"):
        predicate.evaluate(scope, {**_UNREAD, "denominator": "every_token"})


# ---------------------------------------------------------------------------
# A region failing far more often than the pool it is part of.
# ---------------------------------------------------------------------------

#: What the shipped comprehension rule declares.
_CONCENTRATED = {
    "failure_class": "comprehension",
    "minimum_failures": 3,
    "concentration_factor": 5.0,
}


def _failed(entry_id: str, failure_class: FailureClass | None) -> EntryRecords:
    """One entry, with or without a failure of one class recorded against it."""
    failures = ()
    if failure_class is not None:
        failures = (
            ScanError(
                entry_id=entry_id,
                relative_path=f"a/{entry_id}",
                operation="extract.test",
                error_type="TestError",
                message="by construction",
                run_id="r",
                failure_class=failure_class,
            ),
        )
    return EntryRecords(
        entry=_entry(entry_id=entry_id, parent_entry_id="folder:a"), failures=failures
    )


def _region(held: Sequence[EntryRecords], pool: Sequence[EntryRecords]) -> ScopeRecords:
    return ScopeRecords(
        scope=Scope(ScopeKind.FOLDER, "folder:a"), entries=tuple(held), pool=tuple(pool)
    )


_COMPREHENSION = FailureClass.COMPREHENSION


def test_a_region_failing_far_more_often_than_the_pool_is_raised_and_names_the_class() -> None:
    held = [_failed(f"h{index}", _COMPREHENSION) for index in range(4)]
    elsewhere = [_failed(f"e{index}", None) for index in range(96)]
    raised = _predicate("failure_concentrated_in_scope").evaluate(
        _region(held, [*held, *elsewhere]), _CONCENTRATED
    )
    assert isinstance(raised, Raised)
    assert raised.values == ("comprehension",)
    assert raised.reason == (
        "4 of 4 entries failed on comprehension, a rate of 1.00 against the pool's 0.04"
    )
    assert len(raised.inputs) == 4


def test_an_entry_failing_three_times_is_one_failing_entry() -> None:
    """The rule's own sentence counts its entries failing, so one pathological file cannot
    outrank a folder nobody can read."""
    predicate = _predicate("failure_concentrated_in_scope")
    thrice = EntryRecords(
        entry=_entry(entry_id="h0", parent_entry_id="folder:a"),
        failures=tuple(
            ScanError(
                entry_id="h0",
                relative_path="a/h0",
                operation=f"extract.{which}",
                error_type="TestError",
                message="by construction",
                run_id="r",
                failure_class=_COMPREHENSION,
            )
            for which in ("one", "two", "three")
        ),
    )
    held = [thrice, _failed("h1", None), _failed("h2", None)]
    scope = _region(held, [*held, *[_failed(f"e{index}", None) for index in range(97)]])
    assert predicate.evaluate(scope, _CONCENTRATED) is None
    assert "1 of 3 entries failed" in predicate.explain_abstention(scope, _CONCENTRATED).reason


def test_a_region_failing_no_more_often_than_the_pool_is_background() -> None:
    """A hundred failures in a pool of a hundred thousand is background, and a hundred in a
    folder of a hundred and ten is a mount that is not mounted."""
    predicate = _predicate("failure_concentrated_in_scope")
    held = [_failed(f"h{index}", _COMPREHENSION) for index in range(4)]
    alike = [_failed(f"e{index}", _COMPREHENSION) for index in range(46)]
    scope = _region(held, [*held, *alike])
    assert predicate.evaluate(scope, _CONCENTRATED) is None
    assert "under 5.0 times the pool's" in predicate.explain_abstention(scope, _CONCENTRATED).reason


def test_a_region_below_the_floor_is_not_judged_by_its_rate() -> None:
    """A floor under the ratio, so that two failures out of three do not outrank two hundred
    out of a thousand."""
    predicate = _predicate("failure_concentrated_in_scope")
    held = [_failed("h0", _COMPREHENSION), _failed("h1", _COMPREHENSION), _failed("h2", None)]
    scope = _region(held, [*held, *[_failed(f"e{index}", None) for index in range(97)]])
    assert predicate.evaluate(scope, _CONCENTRATED) is None
    assert "fewer than the 3" in predicate.explain_abstention(scope, _CONCENTRATED).reason


def test_a_failure_of_another_class_is_another_rule_s() -> None:
    """The class names the remedy, and counting them together would send somebody to the
    permissions when the answer was a missing format."""
    predicate = _predicate("failure_concentrated_in_scope")
    held = [_failed(f"h{index}", FailureClass.ACCESS) for index in range(4)]
    scope = _region(held, [*held, *[_failed(f"e{index}", None) for index in range(96)]])
    assert predicate.evaluate(scope, _CONCENTRATED) is None
    assert isinstance(
        predicate.evaluate(scope, {**_CONCENTRATED, "failure_class": "access"}), Raised
    )


def test_a_rule_counting_interpretation_failures_is_not_evaluable() -> None:
    """An interpretation failure is a rule's own, produced by the pass this flag runs in, so
    a flag reading one would read a record its own phase is still writing."""
    predicate = _predicate("failure_concentrated_in_scope")
    scope = _region([_failed("h0", _COMPREHENSION)], [_failed("h0", _COMPREHENSION)])
    with pytest.raises(PredicateNotEvaluableError, match="its own phase is still writing"):
        predicate.evaluate(scope, {**_CONCENTRATED, "failure_class": "interpretation"})


def test_a_rule_naming_no_failure_class_is_not_evaluable() -> None:
    predicate = _predicate("failure_concentrated_in_scope")
    scope = _region([_failed("h0", _COMPREHENSION)], [_failed("h0", _COMPREHENSION)])
    with pytest.raises(PredicateNotEvaluableError, match="access, comprehension"):
        predicate.evaluate(scope, {**_CONCENTRATED, "failure_class": ""})


def test_a_pool_that_never_failed_offers_no_rate_to_exceed() -> None:
    """Zero is not a rate to be five times above, and dividing by it would raise every region
    holding a single failure."""
    predicate = _predicate("failure_concentrated_in_scope")
    held = [_failed(f"h{index}", _COMPREHENSION) for index in range(4)]
    scope = _region(held, held)
    assert predicate.evaluate(scope, {**_CONCENTRATED, "concentration_factor": 2.0}) is None


# ---------------------------------------------------------------------------
# Who writes a spelling.
# ---------------------------------------------------------------------------


def _extension_spelled(term: str, spelling: str) -> tuple[DictionaryBinding, Component]:
    """An extension binding of ``term`` made from the spelling ``spelling``."""
    component = _component("extension", spelling)
    binding = _binding(term, "format", constraint=_EXTENSION_CONSTRAINT).model_copy(
        update={"component_id": component.component_id, "binding_id": f"b:{spelling}"}
    )
    return binding, component


def test_an_image_spelling_means_acquisition_only_where_an_instrument_writes_it() -> None:
    """One image type holds ND2 and TIFF. Only the instrument's spelling is raw data."""
    from ingence.infer.subjects import FormatOrigins

    predicate = _predicate("class_from_format_origin")
    parameters = {
        "senses": ["image_format", "ephys_format"],
        "written_by": ["instrument"],
        "concludes": "acquisition",
        "slot": "extension",
    }
    origins = {"image_format": {"nd2": "instrument", "ims": "software"}}

    def subject(term: str, spelling: str) -> FormatOrigins:
        binding, component = _extension_spelled(term, spelling)
        return FormatOrigins(
            entry=_entry(), components=(component,), bindings=(binding,), origins=origins
        )

    raw = predicate.evaluate(subject("image_format", "nd2"), parameters)
    assert raw == Concluded(values=("acquisition",), inputs=("b:nd2",))
    # A spelling the sense declares no origin for is anything's. One written by software is
    # a conversion. Neither begins a workflow, and each says why.
    for spelling in ("tif", "ims"):
        assert predicate.evaluate(subject("image_format", spelling), parameters) is None
        why = predicate.explain_abstention(subject("image_format", spelling), parameters)
        assert "not declared as written by instrument" in why.reason
    # A sense declaring no origins is taken whole.
    whole = predicate.evaluate(subject("ephys_format", "abf"), parameters)
    assert whole == Concluded(values=("acquisition",), inputs=("b:abf",))
    with pytest.raises(PredicateNotEvaluableError, match="names no writer"):
        predicate.evaluate(subject("image_format", "nd2"), {**parameters, "written_by": []})


def test_a_sense_declaring_origins_and_naming_no_writer_admits_no_spelling() -> None:
    """The core's image type where no microscopy pack is loaded.

    It declares origins and names none, because anything writes a TIFF or a PNG. Read as a
    sense declaring nothing, every image would be an instrument's.
    """
    from ingence.infer.subjects import FormatOrigins

    predicate = _predicate("class_from_format_origin")
    parameters = {
        "senses": ["image_format", "ephys_format"],
        "written_by": ["instrument"],
        "concludes": "acquisition",
        "slot": "extension",
    }

    def subject(term: str, spelling: str) -> FormatOrigins:
        binding, component = _extension_spelled(term, spelling)
        return FormatOrigins(
            entry=_entry(),
            components=(component,),
            bindings=(binding,),
            origins={"image_format": {}},
        )

    for spelling in ("tif", "png"):
        assert predicate.evaluate(subject("image_format", spelling), parameters) is None
        why = predicate.explain_abstention(subject("image_format", spelling), parameters)
        assert "not declared as written by instrument" in why.reason
    whole = predicate.evaluate(subject("ephys_format", "abf"), parameters)
    assert whole == Concluded(values=("acquisition",), inputs=("b:abf",)), "absent is still whole"


def test_a_folder_nobody_could_enter_is_raised_where_it_is_listed_whatever_the_floor() -> None:
    """A folder nobody could enter is raised where it is listed, whatever the floor.

    A folder the account cannot enter is one failing entry in its parent, and its contents are
    no entries at all, so under a floor of five the flag whose rationale is "a permission nobody
    granted" could never fire. Its failure is total whatever it held.
    """
    locked = EntryRecords(
        entry=_entry(entry_id="locked", parent_entry_id="folder:a", entry_type="directory"),
        failures=(
            ScanError(
                entry_id="locked",
                relative_path="a/locked",
                operation="scandir",
                error_type="PermissionError",
                message="by construction",
                run_id="r",
                failure_class=FailureClass.ACCESS,
            ),
        ),
    )
    readable = [_failed(f"e{index}", None) for index in range(40)]
    access = {"failure_class": "access", "minimum_failures": 5, "concentration_factor": 5.0}
    predicate = _predicate("failure_concentrated_in_scope")
    raised = predicate.evaluate(_region([locked, *readable], [locked, *readable]), access)
    assert isinstance(raised, Raised)
    assert raised.values == ("access",)
    assert raised.inputs == ("locked",)
    assert (
        raised.reason
        == "1 folder here could not be entered (access), so nothing beneath it was seen"
    )
    # A file that failed on access is still counted by rate, under the floor.
    one_file = _failed("f", FailureClass.ACCESS)
    assert predicate.evaluate(_region([one_file, *readable], [one_file, *readable]), access) is None
    # And comprehension is untouched: a listing that failed is access, never comprehension.
    assert (
        predicate.evaluate(_region([locked, *readable], [locked, *readable]), _CONCENTRATED) is None
    )
