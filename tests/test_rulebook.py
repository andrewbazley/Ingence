"""The Rulebook: rules declared, refused, composed, hashed and rendered.

The code is the interpreter and the rules are the product, so the properties tested here are
properties of the declaration layer rather than of any inference. Nothing here evaluates a rule.
What is tested is whether a broken rule can reach a run, and the answer must be no.

Two constraints on rules are tested here. A proposing rule never scores on temporal overlap, which
is refused at load. And rule form stays human-authored, which a structural test holds: only the
loader constructs a ``Rule``.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from ingence.core.ids import digest_text
from ingence.core.relations import SpecializationRegistry
from ingence.core.schema import ParameterType, RuleEffect
from ingence.rulebook.load import (
    RuleDeclarationError,
    load_revision,
    load_revisions,
)
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import (
    PARENT_INDEX,
    PREDICATES,
    TEMPORAL_FAMILIES,
    UndeclaredParameterError,
    declare,
    effect_of,
    families_of,
    parameters_of,
)
from ingence.rulebook.render import render_rule, render_rulebook, value_in_force
from ingence.rulebook.resolve import EffectiveRulebook, effective_rulebook

load_builtins()

SOURCE = Path(__file__).resolve().parents[1] / "src" / "ingence"


@pytest.fixture
def base() -> tuple[object, ...]:
    return load_revisions()


def _write(path: Path, document: object) -> Path:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _deregister(name: str) -> None:
    """Drop a predicate a test registered.

    Reaches into the registry's own map on purpose. The development build removes
    a component by deleting its module and its registration line, so a runtime
    ``remove`` would be public API that only tests use, and unused public API is a
    thing somebody later has to decide whether to keep.
    """
    PREDICATES._registrations.pop(name, None)
    PREDICATES._instances.pop(name, None)


def _minimal(**changes: object) -> dict[str, object]:
    """One valid rule declaration, for a test to break in exactly one way."""
    rule: dict[str, object] = {
        "id": "example",
        "version": "1",
        "predicate": "format_from_extension",
        "effect": "classifies",
        "statement": "A file is of kind {theme_kind}.",
        "rationale": "Because the extension says so.",
        "parameters": [{"name": "theme_kind", "type": "string", "default": "format"}],
    }
    rule.update(changes)
    return {"revision": 0, "summary": "synthetic", "rules": [rule]}


# ---------------------------------------------------------------------------
# What ships.
# ---------------------------------------------------------------------------


def test_the_shipped_rulebook_loads_and_covers_every_effect_class(base) -> None:
    """A worked example per phase, so nothing is transcribed against a blank.

    Effect decides which phase a rule runs in, so a Rulebook missing an effect
    class has a phase nobody has exercised.
    """
    book = effective_rulebook(base)
    assert {rule.effect for rule in book.rules} == set(RuleEffect)


def test_every_shipped_rule_carries_a_rationale(base) -> None:
    """A rule that cannot be justified in one sentence is a heuristic in disguise."""
    for rule in effective_rulebook(base):
        assert rule.rationale.strip()


def test_the_digest_is_stable_across_loads(base) -> None:
    """Two loads of one file produce one digest, or the run record means nothing.

    ``RunRecord.rulebook_hash`` exists to say that two runs used the same rules. A digest that
    varied with dictionary or directory order would make every pair of runs look different. Then no
    run record could say which Rulebook to make its conclusions again from.
    """
    first = effective_rulebook(load_revisions()).digest
    second = effective_rulebook(load_revisions()).digest
    assert first == second
    assert first == effective_rulebook(base).digest


def test_the_digest_ignores_prose_and_notices_a_parameter(tmp_path: Path) -> None:
    """Editing an explanation must not invalidate every run that used the rule.

    Changing what the rule *does* must. The two halves are the whole argument for
    hashing a chosen subset rather than the file.
    """
    original = effective_rulebook((load_revision(_write(tmp_path / "a.yaml", _minimal())),))

    reworded = _minimal(rationale="Because a filesystem carries an extension.")
    assert (
        effective_rulebook((load_revision(_write(tmp_path / "b.yaml", reworded)),)).digest
        == original.digest
    )

    retuned = _minimal(
        parameters=[{"name": "theme_kind", "type": "string", "default": "technique"}]
    )
    assert (
        effective_rulebook((load_revision(_write(tmp_path / "c.yaml", retuned)),)).digest
        != original.digest
    )


# ---------------------------------------------------------------------------
# Two constraints on rules.
# ---------------------------------------------------------------------------


def test_a_proposing_rule_may_not_score_on_a_window_or_a_burst(tmp_path: Path) -> None:
    """A proposing rule that scores on a window or a burst is refused at load.

    Two unrelated projects running in parallel overlap perfectly, and every laboratory has months
    where three unconnected efforts were active. Temporal overlap is not evidence of relatedness. A
    timeline makes co-occurrence look persuasive in exactly the way that invites this mistake. That
    is why the refusal happens at load and is not a convention.
    """

    from ingence.rulebook.registry import Reads

    @declare(
        "scores_on_a_burst",
        version="1",
        summary="A predicate that should not be allowed to propose",
        effect=RuleEffect.PROPOSES,
        # A pair, which is what a proposing rule is handed, so that the refusal
        # this test is about is the one that fires.
        reads=Reads.PAIR,
        support_families=("name_family", "burst"),
        parameters=("minimum_support",),
    )
    def _build() -> object:
        message = "never constructed"
        raise AssertionError(message)

    try:
        declaration = _minimal(
            predicate="scores_on_a_burst",
            effect="proposes",
            statement="Propose an edge at support {minimum_support}.",
            parameters=[{"name": "minimum_support", "type": "integer", "default": 2}],
        )
        with pytest.raises(RuleDeclarationError, match="temporal overlap is not evidence"):
            load_revision(_write(tmp_path / "temporal.yaml", declaration))
    finally:
        _deregister("scores_on_a_burst")


def test_a_rule_that_does_not_propose_may_still_read_a_burst(tmp_path: Path) -> None:
    """The prohibition is on positive support, not on the evidence.

    A burst may bound candidate generation, which is a constraint and narrows what
    is considered, and it may be displayed. Forbidding the evidence outright would
    retire a real signal to prevent one misuse of it.
    """
    from ingence.rulebook.registry import Reads

    @declare(
        "flags_a_burst",
        version="1",
        summary="Raise a folder whose activity arrived in one burst",
        effect=RuleEffect.FLAGS,
        reads=Reads.SCOPE,
        support_families=("burst",),
        parameters=("minimum_files",),
    )
    def _build() -> object:
        message = "never constructed"
        raise AssertionError(message)

    try:
        declaration = _minimal(
            predicate="flags_a_burst",
            effect="flags",
            statement="Raise a folder whose burst holds at least {minimum_files} files.",
            parameters=[{"name": "minimum_files", "type": "integer", "default": 5}],
        )
        revision = load_revision(_write(tmp_path / "flagging.yaml", declaration))
        assert revision.rules[0].effect is RuleEffect.FLAGS
    finally:
        _deregister("flags_a_burst")


def test_only_the_loader_constructs_a_rule() -> None:
    """Only the loader constructs a ``Rule``, so rule form stays human-authored.

    The proposal machinery may emit parameter values and Dictionary entries. It must have no code
    path that writes a ``predicate`` field. Exactly one path constructs a ``Rule``, it reads a file
    a person wrote, and this test keeps it that way. A conclusion traceable to a rule nobody wrote
    is not attributable in any sense a reader would accept.
    """
    constructing: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Rule"
            ):
                # ``as_posix`` rather than ``str``: a path separator is a fact about
                # the platform and this assertion is about which module constructs a
                # Rule. Comparing the native spelling made the check fail on Windows
                # for a reason that has nothing to do with what it tests.
                constructing.append(path.relative_to(SOURCE).as_posix())
    assert sorted(set(constructing)) == ["rulebook/load.py"]


def test_the_temporal_families_are_declared_rather_than_spelled_out() -> None:
    """The prohibition lives at the family level, not at the component name.

    ``SupportComponent.family`` already groups components that are views of one
    fact. Forbidding one component name would leave the next spelling of the same
    fact permitted, which is the mistake the family field exists to prevent.
    """
    assert "burst" in TEMPORAL_FAMILIES
    assert "window" in TEMPORAL_FAMILIES
    families = families_of(PREDICATES.declaration("derives_from_name_family"))
    assert not set(families) & TEMPORAL_FAMILIES


# ---------------------------------------------------------------------------
# Refusing what is not a rule.
# ---------------------------------------------------------------------------


def test_a_rule_naming_a_predicate_nobody_registered_is_refused(tmp_path: Path) -> None:
    """Loudly, rather than by disabling the rule.

    A Rulebook that silently drops a rule produces different answers than the file
    says it will, and the person reading the file has no way to tell.
    """
    document = _minimal(predicate="nothing_registered_this")
    with pytest.raises(RuleDeclarationError, match="is not registered"):
        load_revision(_write(tmp_path / "missing.yaml", document))


def test_a_rule_whose_effect_its_predicate_does_not_serve_is_refused(tmp_path: Path) -> None:
    """Effect decides which phase a rule runs in, so a mismatch runs it in the wrong one.

    The symptom would be an absent conclusion rather than an error, which is the
    hardest kind of defect to find in an engine whose valid outputs include
    abstention.
    """
    document = _minimal(effect="proposes")
    with pytest.raises(RuleDeclarationError, match="effect decides which phase"):
        load_revision(_write(tmp_path / "mismatch.yaml", document))


def test_a_statement_and_its_parameters_must_describe_each_other(tmp_path: Path) -> None:
    """Both directions, and each catches a different lie.

    A slot with no parameter renders a literal brace and the sentence stops being
    English. A parameter with no slot is a knob whose setting the sentence never
    mentions, so a person reading the rendered Rulebook cannot see what the engine
    is doing, which is the attributability claim failing quietly.
    """
    unfilled = _minimal(statement="A file is of kind {theme_kind} at depth {depth}.")
    with pytest.raises(RuleDeclarationError, match="which no parameter declares"):
        load_revision(_write(tmp_path / "unfilled.yaml", unfilled))

    hidden = _minimal(
        statement="A file is of kind {theme_kind}.",
        parameters=[
            {"name": "theme_kind", "type": "string", "default": "format"},
            {"name": "depth", "type": "integer", "default": 2},
        ],
    )
    with pytest.raises(RuleDeclarationError, match="the statement never mentions"):
        load_revision(_write(tmp_path / "hidden.yaml", hidden))


def test_a_rule_with_no_rationale_is_refused_in_words(tmp_path: Path) -> None:
    """A person writing a rule by hand reads a sentence, not a validation traceback."""
    document = _minimal()
    del document["rules"][0]["rationale"]
    with pytest.raises(RuleDeclarationError, match="a rule must carry a 'rationale'"):
        load_revision(_write(tmp_path / "no_reason.yaml", document))


def test_a_tier_nobody_defined_is_refused_in_words(tmp_path: Path) -> None:
    """A misspelt tier names the tiers there are, instead of ending in a traceback."""
    document = _minimal(tier="defeasable")
    with pytest.raises(RuleDeclarationError, match="tier 'defeasable' is not one of"):
        load_revision(_write(tmp_path / "tier.yaml", document))


def test_whatever_the_rule_model_refuses_is_said_in_words_at_the_rule(tmp_path: Path) -> None:
    """An equivalence with no respect is the model's refusal, and reaches a person as a sentence."""
    document = _minimal(primitive="equivalent")
    with pytest.raises(
        RuleDeclarationError, match=r"rule 'example': the rule is not valid .*respect"
    ):
        load_revision(_write(tmp_path / "respect.yaml", document))


def test_a_default_its_own_bounds_reject_is_refused(tmp_path: Path) -> None:
    """A rule cannot start outside its own axis.

    The failure is silent otherwise: the rule produces a conclusion at a setting
    the interface will not let anybody reproduce, and a sweep would start from a
    point off its own curve.
    """
    document = _minimal(
        statement="A file is of kind {theme_kind} at depth {depth}.",
        parameters=[
            {"name": "theme_kind", "type": "string", "default": "format"},
            {"name": "depth", "type": "integer", "default": 9, "minimum": 1, "maximum": 5},
        ],
    )
    with pytest.raises(RuleDeclarationError, match="above its maximum"):
        load_revision(_write(tmp_path / "bounds.yaml", document))


def test_two_revisions_sharing_a_number_are_refused(tmp_path: Path) -> None:
    """Composition order would otherwise depend on the order the filesystem returned names.

    The Rulebook is one of the four inputs an interpretation must be reproducible from. A Rulebook
    that composes differently depending on the order of a directory listing is not an input anybody
    can reproduce from.
    """
    _write(tmp_path / "00-a.yaml", _minimal())
    _write(tmp_path / "00-b.yaml", _minimal(id="other"))
    with pytest.raises(RuleDeclarationError, match="declared twice"):
        load_revisions(tmp_path)


# ---------------------------------------------------------------------------
# What relation a rule asserts, and the five ways of failing to say it.
# ---------------------------------------------------------------------------


def _proposing(**changes: object) -> dict[str, object]:
    """One valid proposing declaration naming an index, as revision 99, to change one way."""
    declaration: dict[str, object] = {
        "id": "example_proposal",
        "predicate": "derives_from_stage_transition",
        "effect": "proposes",
        "primitive": "derives",
        "specialization": "processed_from",
        "statement": "A file is processed from another where {stage_transitions} names the step.",
        "parameters": [
            {"name": "stage_transitions", "type": "mapping", "default": {"raw": "processed"}}
        ],
        "candidates_within": "same_name_family",
    }
    declaration.update(changes)
    document = _minimal(**declaration)
    document["revision"] = 99
    return document


def test_an_index_on_a_rule_that_does_not_propose_is_refused(tmp_path: Path) -> None:
    """Only a proposing rule draws pairs, so an index anywhere else could never be read."""
    declaration = _minimal(candidates_within="same_name_family")
    with pytest.raises(RuleDeclarationError, match="only a proposing rule draws"):
        load_revision(_write(tmp_path / "classifying.yaml", declaration))


@pytest.mark.parametrize(
    ("index", "refusal"),
    [
        ("no_such_rule", "which no revision declares"),
        ("stage_from_name", "whose effect is classifies"),
    ],
)
def test_an_index_naming_no_grouping_rule_stops_the_rulebook_composing(
    tmp_path: Path, base, index: str, refusal: str
) -> None:
    revision = load_revision(
        _write(tmp_path / "proposing.yaml", _proposing(candidates_within=index))
    )
    with pytest.raises(RuleDeclarationError, match=refusal):
        effective_rulebook([*base, revision])


def test_an_index_of_one_folder_or_of_a_grouping_composes_and_is_part_of_the_digest(
    tmp_path: Path, base
) -> None:
    digests = set()
    for index in (PARENT_INDEX, "same_name_family"):
        document = _proposing(candidates_within=index)
        book = effective_rulebook(
            [*base, load_revision(_write(tmp_path / f"{index}.yaml", document))]
        )
        rule = book.by_id("example_proposal")
        assert rule is not None
        assert rule.candidates_within == index
        digests.add(book.digest)
    assert len(digests) == 2


def _grouping(**changes: object) -> dict[str, object]:
    """One valid grouping declaration, for a test to break in exactly one way."""
    declaration: dict[str, object] = {
        "predicate": "same_composition",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "composition",
        "statement": "Two folders agree when each holds {minimum_children} children.",
        "parameters": [{"name": "minimum_children", "type": "integer", "default": 3}],
    }
    declaration.update(changes)
    return _minimal(**declaration)


def test_a_rule_that_asserts_a_relationship_must_say_which_one(tmp_path: Path) -> None:
    """There is no generic related_to, and a rule is the wrong place to abstain.

    Where the engine cannot say how two things relate it abstains. A proposing
    rule is not in that position: the person who wrote it knew what it proposes,
    and leaving it unsaid produces a candidate the decoder cannot type.
    """
    declaration = _minimal(
        predicate="derives_from_name_family",
        effect="proposes",
        statement="Propose an edge at support {minimum_support}.",
        parameters=[{"name": "minimum_support", "type": "integer", "default": 2}],
    )
    with pytest.raises(RuleDeclarationError, match="names no primitive"):
        load_revision(_write(tmp_path / "untyped.yaml", declaration))


def test_a_primitive_outside_the_eight_is_refused(tmp_path: Path) -> None:
    """The set is closed, and a ninth is a formal-property argument rather than a typo."""
    with pytest.raises(RuleDeclarationError, match="not one of the eight"):
        load_revision(_write(tmp_path / "ninth.yaml", _grouping(primitive="related_to")))


def test_a_specialization_the_catalog_does_not_hold_is_refused(tmp_path: Path) -> None:
    """An ad-hoc relation name is the fragmentation the closed set exists to prevent.

    Two true statements about one pair of files, recorded under two invented
    names, fail to connect. A rule file is exactly where such a name would be
    introduced, so it is refused here rather than registered on the way past.
    """
    declaration = _grouping(specialization="basically_the_same_as")
    with pytest.raises(RuleDeclarationError, match="not in the catalog"):
        load_revision(_write(tmp_path / "adhoc.yaml", declaration))


def test_a_specialization_may_not_contradict_its_parent(tmp_path: Path) -> None:
    """A specialization inherits every formal property of its parent.

    ``replicate_of`` sits under ``equivalent``. A rule declaring it under ``succeeds`` would inherit
    a total order inside a series from a relation that has none, and nothing downstream could tell
    which of the two was meant.
    """
    declaration = _minimal(
        predicate="derives_from_name_family",
        effect="proposes",
        primitive="succeeds",
        specialization="replicate_of",
        statement="Propose an edge at support {minimum_support}.",
        parameters=[{"name": "minimum_support", "type": "integer", "default": 2}],
    )
    with pytest.raises(RuleDeclarationError, match="may not contradict"):
        load_revision(_write(tmp_path / "contradiction.yaml", declaration))


def test_a_respect_may_not_disagree_with_the_specialization_that_fixes_it(
    tmp_path: Path,
) -> None:
    """The respect comes from the catalog, so there is no second copy to drift.

    Refused rather than silently preferred either way. Preferring the catalog
    would leave a rule file saying something no run honours, and preferring the
    file would let one rule mean something the specialization does not.
    """
    declaration = _grouping(specialization="same_shape_as", respect="composition")
    with pytest.raises(RuleDeclarationError, match="fixes"):
        load_revision(_write(tmp_path / "disagreement.yaml", declaration))


def test_a_specialization_supplies_the_respect_it_fixes(tmp_path: Path) -> None:
    """The other side of the same rule: naming one is enough, and it is derived."""
    declaration = _grouping(specialization="same_shape_as", respect=None)
    rule = load_revision(_write(tmp_path / "derived.yaml", declaration)).rules[0]
    assert rule.respect == "shape"


def test_a_grouping_on_a_relation_that_is_not_an_equivalence_is_refused(
    tmp_path: Path,
) -> None:
    """A grouping is a quotient and a quotient needs an equivalence relation.

    ``concerns`` is the case that matters and the reason this reads the declared
    signature rather than naming the two primitives that qualify. It is symmetric
    and never transitive, so grouping by it would put A and C in one group
    because each shares a subject with B, with nothing anywhere asserting that A
    and C are related. Wrong in a way no output could reveal.
    """
    declaration = _grouping(primitive="concerns", respect="technique")
    with pytest.raises(RuleDeclarationError, match="not transitive"):
        load_revision(_write(tmp_path / "quotient.yaml", declaration))


def test_an_equivalence_without_a_stated_respect_is_refused(tmp_path: Path) -> None:
    """Same as what? The question the only respect-bearing primitive exists to force."""
    with pytest.raises(ValueError, match="requires a respect"):
        load_revision(_write(tmp_path / "bare.yaml", _grouping(respect=None)))


def test_a_respect_on_a_relation_that_takes_none_is_refused(tmp_path: Path) -> None:
    """Not harmless decoration: it tells a reader something the engine never uses.

    ``derives`` has no dimension to state. A respect written there would render
    into the sentence a person approves and correspond to nothing that runs.
    """
    declaration = _minimal(
        predicate="derives_from_name_family",
        effect="proposes",
        primitive="derives",
        respect="composition",
        statement="Propose an edge at support {minimum_support}.",
        parameters=[{"name": "minimum_support", "type": "integer", "default": 2}],
    )
    with pytest.raises(ValueError, match="takes no respect"):
        load_revision(_write(tmp_path / "spurious.yaml", declaration))


def test_a_specialization_may_fix_a_respect_its_parent_does_not_require(
    tmp_path: Path,
) -> None:
    """A specialization may fix a respect that its parent does not require.

    ``concerns`` requires no respect and ``about_technique`` fixes ``technique`` under it, which is
    the catalog saying what that narrower relation is about. Refusing every respect on a primitive
    that does not demand one would make five shipped specializations undeclarable.
    """
    declaration = _minimal(
        predicate="derives_from_name_family",
        effect="proposes",
        primitive="concerns",
        specialization="about_technique",
        statement="Propose an edge at support {minimum_support}.",
        parameters=[{"name": "minimum_support", "type": "integer", "default": 2}],
    )
    rule = load_revision(_write(tmp_path / "about.yaml", declaration)).rules[0]
    assert rule.respect == "technique"


def test_the_digest_notices_a_rule_retargeted_to_another_specialization(
    tmp_path: Path,
) -> None:
    """The digest changes when a rule keeps its predicate, parameters and sentence but its claim
    differs.

    ``variant_of`` says a design was deliberately changed and ``replicate_of`` says the same design
    was performed again. Two runs whose digests agreed while one of them meant that would defeat the
    hash at its one job.
    """

    def _proposal(specialization: str) -> dict[str, object]:
        return _minimal(
            predicate="derives_from_name_family",
            effect="proposes",
            primitive="equivalent",
            specialization=specialization,
            statement="Propose an edge at support {minimum_support}.",
            parameters=[{"name": "minimum_support", "type": "integer", "default": 2}],
        )

    variant = load_revision(_write(tmp_path / "variant.yaml", _proposal("variant_of")))
    replicate = load_revision(_write(tmp_path / "replicate.yaml", _proposal("replicate_of")))
    assert effective_rulebook((variant,)).digest != effective_rulebook((replicate,)).digest


# ---------------------------------------------------------------------------
# Composition.
# ---------------------------------------------------------------------------


def test_a_later_revision_supersedes_without_deleting(tmp_path: Path) -> None:
    """Superseding marks a rule inactive from a revision forward, and keeps it.

    A past run must replay exactly, so asking for the earlier revision must still return the rule
    that ran. And "superseded at revision 1" and "no such rule" are answers to different questions,
    which is why the inactive set carries its reason rather than the rule simply vanishing.
    """
    _write(tmp_path / "00-base.yaml", _minimal())
    _write(
        tmp_path / "01-retire.yaml",
        {"revision": 1, "summary": "retire it", "rules": [], "supersedes": ["example"]},
    )
    revisions = load_revisions(tmp_path)

    current = effective_rulebook(revisions)
    assert current.by_id("example") is None
    assert ("example", "superseded at revision 1") in current.inactive

    earlier = effective_rulebook(revisions, at=0)
    assert earlier.by_id("example") is not None


def test_a_disabled_rule_is_in_the_rulebook_and_out_of_force(tmp_path: Path) -> None:
    """Switched off and never existed are answers to different questions.

    A user who switched a rule off is exactly the person who will ask why it did
    not fire, and a resolver returning only the active set can give only the
    second answer.
    """
    _write(tmp_path / "00-base.yaml", _minimal(enabled=False))
    book = effective_rulebook(load_revisions(tmp_path))
    assert book.by_id("example") is None
    assert ("example", "disabled") in book.inactive


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def test_a_rule_renders_at_the_value_in_force_not_at_its_default(base) -> None:
    """The point of rendering is to say what the engine is doing.

    A rule whose threshold a user raised must read at the raised value, or the
    sentence a person approved describes something else.
    """
    rule = effective_rulebook(base).by_id("same_composition")
    assert rule is not None
    assert "at least 3 children" in render_rule(rule)
    assert "at least 8 children" in render_rule(rule, overrides={"minimum_children": 8})


def test_a_list_parameter_renders_as_english(base) -> None:
    """A rule sentence is for a person, and a bracketed array is the implementation.

    Joined with "or" rather than with commas alone, because "a file whose
    extension is .tif, .tiff" reads as a conjunction and means a disjunction.
    """
    rule = effective_rulebook(base).by_id("derives_from_name_family")
    assert rule is not None
    assert "raw_acquisition, derived_image or tabular_data" in render_rule(rule)


def test_a_list_after_all_of_joins_with_and(base) -> None:
    """A list after "all of" joins with "and", because "or" would state the opposite.

    The sentence is what a person approves, so a wrong conjunction is a wrong sentence. Every rule a
    shipped revision declares is rendered, superseded ones included, because a past run cites them.
    """
    or_after_all_of = re.compile(r"\ball of (?:[\w.-]+, )+[\w.-]+ or ")
    for revision in base:
        for rule in revision.rules:
            assert not or_after_all_of.search(render_rule(rule)), rule.rule_id
    # The rule is superseded at revision 4 and readable at revision 3, which held it.
    rule = effective_rulebook(base, at=3).by_id("same_observed_metadata")
    assert rule is not None
    assert "all of name, size_bytes, created_ns, modified_ns and entry_type" in render_rule(rule)


def test_the_rendered_rulebook_carries_its_digest_and_the_revision_asked_for(base) -> None:
    """A hash says two runs agree and a number says which Rulebook to open.

    The number is said where a person asked for one (``rule show --revision``). Otherwise the latest
    is called "the rules in force", in words.
    """
    book = effective_rulebook(base)
    rendered = render_rulebook(book)
    assert rendered.startswith("Rules in force\n")
    assert book.digest in rendered
    earlier = render_rulebook(effective_rulebook(base, at=2), revision_asked=True)
    assert earlier.startswith("Rules in force at revision 2\n")
    for rule in book.rules:
        assert rule.rule_id in rendered


def test_every_registered_predicate_declares_an_effect() -> None:
    """An effect cannot be defaulted: it decides which phase a rule runs in."""
    for name in PREDICATES.names():
        assert isinstance(effect_of(PREDICATES.declaration(name)), RuleEffect)


def test_every_registered_predicate_declares_what_it_reads_and_its_effect_can_hand_it() -> None:
    """What a predicate is handed cannot be defaulted either.

    The loader refuses the mismatch rule by rule. This test asserts the same over every
    registration, including the predicates nothing can evaluate yet, whose declarations decide what
    they will be handed.
    """
    from ingence.rulebook.registry import READS_BY_EFFECT, reads_of

    for name in PREDICATES.names():
        declaration = PREDICATES.declaration(name)
        assert reads_of(declaration) in READS_BY_EFFECT[effect_of(declaration)], name


def test_a_rule_whose_effect_cannot_hand_its_predicate_what_it_reads_is_refused(
    tmp_path: Path,
) -> None:
    """A classifying rule over a predicate that reads a pair could only abstain.

    That abstention would look exactly like a predicate that found nothing. So the rule is refused
    where the person who can fix it is reading the file.
    """
    from ingence.rulebook.registry import Reads

    @declare(
        "reads_a_pair",
        version="1",
        summary="A predicate reading a pair, declared to serve a classifying rule",
        effect=RuleEffect.CLASSIFIES,
        reads=Reads.PAIR,
        parameters=("theme_kind",),
    )
    def _build() -> object:
        message = "never constructed"
        raise AssertionError(message)

    try:
        declaration = _minimal(predicate="reads_a_pair")
        with pytest.raises(RuleDeclarationError, match="could only ever abstain"):
            load_revision(_write(tmp_path / "pair.yaml", declaration))
    finally:
        _deregister("reads_a_pair")


def test_a_predicate_declaring_nothing_it_reads_is_refused_rather_than_defaulted(
    tmp_path: Path,
) -> None:
    """Registered around ``declare``, which is the only way to leave the declaration out."""

    @PREDICATES.register(
        "reads_nothing_declared",
        version="1",
        summary="A predicate registered without saying what it reads",
        attributes={"effect": RuleEffect.CLASSIFIES.value},
    )
    def _build() -> object:
        message = "never constructed"
        raise AssertionError(message)

    try:
        declaration = _minimal(predicate="reads_nothing_declared")
        with pytest.raises(RuleDeclarationError, match="declares nothing it reads"):
            load_revision(_write(tmp_path / "undeclared.yaml", declaration))
    finally:
        _deregister("reads_nothing_declared")


def test_a_rule_setting_a_parameter_its_predicate_does_not_read_is_refused(tmp_path: Path) -> None:
    """A setting nothing reads changes nothing while the sentence says it is in force.

    Example: a rule that names what it concludes would load against a predicate that concludes the
    sense it matched regardless. The rule would then render as though the setting took effect.
    """
    ignored = _minimal(
        statement="A file is of kind {theme_kind}, shown in {colour}.",
        parameters=[
            {"name": "theme_kind", "type": "string", "default": "format"},
            {"name": "colour", "type": "string", "default": "blue"},
        ],
    )
    refusal = "declares colour, which predicate 'format_from_extension' does not read"
    with pytest.raises(RuleDeclarationError, match=refusal):
        load_revision(_write(tmp_path / "ignored.yaml", ignored))


def test_every_rule_any_revision_ships_sets_only_what_its_predicate_reads(base) -> None:
    for revision in base:
        for rule in revision.rules:
            declared = {specification.name for specification in rule.parameters}
            assert declared <= parameters_of(PREDICATES.declaration(rule.predicate)), rule.rule_id


def test_a_predicate_reading_a_parameter_its_registration_does_not_declare_is_refused() -> None:
    """The loader holds a rule to the declaration, and the read holds the predicate to it.

    A predicate that reached for a parameter through ``get`` would otherwise take a default that no
    registration and no rule declared, and the registration would stop saying what it reads.
    """
    from ingence.infer.evaluate import parameters_in_force

    # The rule is superseded at revision 4 and readable at revision 3, which held it.
    rule = effective_rulebook(load_revisions(), at=3).by_id("format_from_extension")
    assert rule is not None
    parameters = parameters_in_force(rule)
    assert parameters["theme_kind"] == "format"
    assert parameters.get("minimum_match_quality") is None
    with pytest.raises(UndeclaredParameterError, match="'senses', which its registration"):
        parameters.get("senses")
    with pytest.raises(UndeclaredParameterError):
        assert "senses" not in parameters


# ---------------------------------------------------------------------------
# Shipped vocabulary, and what must stay out of it.
# ---------------------------------------------------------------------------

#: Digests of vocabulary belonging to the author's own research: a cell line, three reporter
#: constructs, a toxin and its mutants, and a plasmid identifier prefix. Widely used public cell
#: lines ship, so none of them is in the set.
#:
#: The set holds digests rather than the words, because a denylist of confidential terms published
#: in the repository defeats its own purpose. A leak stays detectable and the terms stay unreadable.
#: A failure names the offending token because by then the token is in a file this test has just
#: read.
#:
#: Two neighbouring classes are deliberately excluded. Generic reagents are ordinary vocabulary that
#: any laboratory writes, and several of them ship already. Third-party software names are a case
#: where the engine raises the question instead of deciding, and some of those ship too. Neither is
#: a fact about anybody's research programme.
_PRIVATE_VOCABULARY = frozenset(
    {
        "0250fbfce7437b1c8d5593488934a971",
        "39a696992b66b72d6d8f5590781f0c5a",
        "3de4179cc319bca075407aa0a6a4510f",
        "8355709bda12056202bc25b4f977dacf",
        "af62911f89910975c991352c95d6fc12",
        "bd3a8aeddfeb8bd64eb85fca1228c288",
        "dac8fd24389720190232144f2a82ff0f",
        "f795bdaac8b2fa5bb16e7eb068d92105",
    }
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{1,}")


def _shipped_vocabulary_files() -> list[Path]:
    return sorted(
        [
            *(SOURCE / "rulebook" / "builtin").glob("*.yaml"),
            *(SOURCE / "dictionary" / "builtin").rglob("*.yaml"),
            *(SOURCE / "dictionary" / "packs").rglob("*.yaml"),
        ]
    )


def test_no_shipped_rule_or_sense_carries_the_author_s_own_research_vocabulary() -> None:
    """A rule of the project, made mechanical.

    No organism, gene, protein, cell line or project name from the author's own research may reach a
    shipped dictionary or example. The widely used public names ship and are not on the list. Words
    copied from earlier engines are the most likely place for such a name to slip in, and a shipped
    cell line looks like ordinary domain vocabulary to anybody who does not know whose corpus it
    came from.

    The test is a tripwire rather than a target. It pins the current state so that a later
    transcription cannot quietly widen it, and adding a digest is a decision somebody takes rather
    than something a passing edit does.
    """
    offenders: list[str] = []
    for path in _shipped_vocabulary_files():
        for word in set(_WORD.findall(path.read_text(encoding="utf-8"))):
            if digest_text(word.casefold()) in _PRIVATE_VOCABULARY:
                offenders.append(f"{path.name}: {word}")
    assert not offenders, f"private vocabulary reached shipped files: {offenders}"


def test_every_registered_predicate_is_named_by_a_rule(base, tmp_path: Path) -> None:
    """A predicate nothing names is code with no reader.

    The development build removes a component by deleting its module and its registration line,
    which only works while every registration has a caller. A predicate that no rule declares is a
    mechanism whose behaviour nothing pins, and it is the first thing to rot. A rule that any
    shipped revision declares names its predicate, superseded or in force.
    """
    named = {rule.predicate for revision in base for rule in revision.rules}
    assert set(PREDICATES.names()) == named


def test_every_effect_class_has_more_than_a_worked_example(base) -> None:
    """Revision 0 shipped one rule per phase and revision 1 is the phase itself.

    A phase with a single rule cannot exercise ordering, composition or the interaction between two
    rules that disagree, which is most of what a phase does. The scan layer's own rules are not a
    phase of inference, and are exactly four, so that is what their count is held to.
    """
    counted = Counter(rule.effect for rule in effective_rulebook(base))
    assert set(counted) == set(RuleEffect)
    assert counted.pop(RuleEffect.ENUMERATES) == 4
    assert min(counted.values()) >= 5


def test_revision_3_declares_the_condition_and_the_eligible_roles_again(base) -> None:
    """Revision 3 declares the condition and the eligible roles as versions of their own, not as
    edits to revision 1.

    The condition counts only quantities and words bound to a condition or a reagent. A derivation
    may start from an analysis or a quantification and never from ``tabular``, which no sense names.
    Read at revision 2, both rules are still as they were.
    """
    # Revision 3 declared them. Revision 4 supersedes the eligible roles.
    latest = effective_rulebook(base, at=3)
    earlier = effective_rulebook(base, at=2)

    def in_force(book: EffectiveRulebook, rule_id: str) -> tuple[str, dict[str, object]]:
        rule = book.by_id(rule_id)
        assert rule is not None, rule_id
        return rule.version, {spec.name: value_in_force(spec) for spec in rule.parameters}

    version, condition = in_force(latest, "same_experimental_condition")
    assert version == "2"
    assert condition["counted_theme_kinds"] == ["condition", "reagent"]
    assert condition["component_kinds"] == ["quantity", "word"]
    assert in_force(earlier, "same_experimental_condition")[0] == "1"

    version, roles = in_force(latest, "derivation_endpoints_hold_eligible_roles")
    _, before = in_force(earlier, "derivation_endpoints_hold_eligible_roles")
    assert version == "2"
    assert set(roles["source_roles"]) == (set(before["source_roles"]) - {"tabular"}) | {
        "analysis",
        "quantification",
    }
    assert roles["target_roles"] == before["target_roles"]


def test_every_proposing_and_grouping_rule_says_what_it_asserts(base) -> None:
    """Enforced at load, asserted over what ships, because the two can drift.

    A refusal that nothing shipped exercises passes vacuously.
    """
    for rule in effective_rulebook(base):
        if rule.effect in {RuleEffect.PROPOSES, RuleEffect.GROUPS}:
            assert rule.primitive is not None, rule.rule_id


def test_every_shipped_specialization_is_in_the_catalog(base) -> None:
    """An ad-hoc relation name is the fragmentation the closed primitive set prevents."""
    catalog = SpecializationRegistry()
    for rule in effective_rulebook(base):
        if rule.specialization is not None:
            entry = catalog.get(rule.specialization)
            assert entry.parent is rule.primitive, rule.rule_id
            assert entry.respect == rule.respect, rule.rule_id


def test_every_shipped_rule_renders_without_a_literal_brace(base) -> None:
    """A slot the renderer could not fill stops the sentence being English.

    The loader refuses a statement naming a parameter that does not exist, so
    this is the other side: that what the loader accepted actually renders.
    """
    for rule in effective_rulebook(base):
        rendered = render_rule(rule)
        assert "{" not in rendered and "}" not in rendered, rule.rule_id
        assert rendered.endswith("."), rule.rule_id


def test_a_rule_reads_correctly_at_the_ends_of_its_own_parameter_range(base) -> None:
    """A sentence that reads as English only at its shipped value is half a sentence.

    Rendering is what a person approves, and a parameter is meant to be changed, so "at least 1
    distinct senses" is a defect rather than a nicety. The test checks the declared bounds because
    those are the values an interface's slider will actually produce.
    """
    broken: list[str] = []
    for rule in effective_rulebook(base):
        for specification in rule.parameters:
            for edge in (specification.minimum, specification.maximum):
                if edge is None:
                    continue
                numeric = specification.type in {ParameterType.INTEGER, ParameterType.DURATION}
                value = int(edge) if numeric else edge
                rendered = render_rule(rule, overrides={specification.name: value})
                if re.search(r"\b1 [a-z]+s\b", rendered):
                    broken.append(f"{rule.rule_id}/{specification.name}")
    assert set(broken) <= _READ_WRONG_AT_A_BOUND, f"reads wrong at a bound: {broken}"


#: These sentences read "1 seconds" and "1 words" at their lower bound. Each is its rule's statement
#: to change, in a ``Rule:`` commit of its own (the fold changes no rule). The set is listed so that
#: a new one is still caught.
_READ_WRONG_AT_A_BOUND = frozenset(
    {
        "copied_in_bulk/burst_window_seconds",
        "declared_copy_window_exceeded/burst_window_seconds",
        "folder_copied_by_declared_clocks/burst_window_seconds",
        "folder_copied_in_bulk/burst_window_seconds",
        "unbound_word_in_a_kinds_place/minimum_bound",
    }
)


# ---------------------------------------------------------------------------
# Defeaters.
# ---------------------------------------------------------------------------


def test_a_defeater_no_revision_declares_is_refused_when_the_rulebook_composes(
    tmp_path: Path,
) -> None:
    """A typo in ``defeated_by`` names nothing, and a claim naming it could never be retracted."""
    revision = load_revision(_write(tmp_path / "typo.yaml", _minimal(defeated_by=["no_such_rule"])))
    with pytest.raises(RuleDeclarationError, match="no revision declares"):
        effective_rulebook((revision,))


def test_a_defeater_not_in_force_leaves_the_rulebook_composing(tmp_path: Path) -> None:
    """Switching a defeater off must not stop the Rulebook loading; the claim pass reports it."""
    document = _minimal(defeated_by=["defeating"])
    document["rules"].append(  # type: ignore[attr-defined]
        {
            "id": "defeating",
            "version": "1",
            "predicate": "extension_contradicts_content",
            "effect": "flags",
            "enabled": False,
            "statement": "A file is raised.",
            "rationale": "Because its content says otherwise.",
        }
    )
    book = effective_rulebook((load_revision(_write(tmp_path / "off.yaml", document)),))
    assert book.by_id("example") is not None
    assert ("defeating", "disabled") in book.inactive


def test_the_digest_notices_a_defeater(tmp_path: Path) -> None:
    """A defeater changes what every claim the rule licenses carries, so it is part of the rule."""
    from ingence.rulebook.resolve import rulebook_digest

    defeating = {
        "id": "defeating",
        "version": "1",
        "predicate": "extension_contradicts_content",
        "effect": "flags",
        "statement": "A file is raised.",
        "rationale": "Because its content says otherwise.",
    }
    plain = _minimal()
    named = _minimal(defeated_by=["defeating"])
    for document in (plain, named):
        document["rules"].append(defeating)  # type: ignore[attr-defined]
    before = effective_rulebook((load_revision(_write(tmp_path / "a.yaml", plain)),))
    after = effective_rulebook((load_revision(_write(tmp_path / "b.yaml", named)),))
    assert rulebook_digest(before.rules) != rulebook_digest(after.rules)
