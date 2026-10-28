"""The eight relation primitives, their formal properties, and the specializations.

Everything the engine asserts composes from eight primitives. The set is closed: adding a ninth
requires a formal-property argument, not a use case. The properties declared here are the
metadata that the publication invariants read instead of restating.

A primitive earns its place by having a formal signature that no other primitive has. Its answer
differs on at least one of directionality, symmetry, transitivity, acyclicity, observational
completeness or substitutivity. No two rows of :data:`PRIMITIVE_PROPERTIES` are identical, and
that is the whole justification for there being eight and not fewer.

Everything that differs only in meaning is a specialization: a named relation with a declared
parent and fixed parameters. It inherits every formal property of its parent and may not
contradict them. It costs one registry entry, no schema change, and no new invariant.

There is deliberately no generic ``related_to``. A relation with no properties is not a relation.
Where the engine cannot say how two things relate, it abstains.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

__all__ = [
    "PRIMITIVE_PROPERTIES",
    "SPECIALIZATION_CATALOG",
    "AllenRelation",
    "ExpansionState",
    "OrderShape",
    "Primitive",
    "PrimitiveProperties",
    "QuotientOrigin",
    "ReferenceResolution",
    "Specialization",
    "SpecializationOrigin",
    "SpecializationRegistry",
    "signature_of",
]


class Primitive(StrEnum):
    """The eight relation primitives. The set is closed."""

    #: Filesystem containment. The only relation the engine can assert
    #: exhaustively, and therefore the only basis for a closed-world statement.
    CONTAINS = "contains"
    #: Structural containment inside a container file. Identical to
    #: ``contains`` in every respect except observational completeness.
    EMBEDS = "embeds"
    #: Produced from. A direct claim. The transitive closure is the provenance
    #: chain and is a different object from the edge.
    DERIVES = "derives"
    #: Proven byte equality. The only substitutive relation in the set.
    IDENTICAL = "identical"
    #: Same with respect to a stated dimension. Defines every grouping.
    EQUIVALENT = "equivalent"
    #: Order within a series. Total within its series, and not derivation.
    SUCCEEDS = "succeeds"
    #: Mentions or points at. The only relation permitting cycles.
    REFERENCES = "references"
    #: About the same subject. Symmetric but never transitive.
    CONCERNS = "concerns"


class OrderShape(StrEnum):
    """The shape of the order a directed, acyclic primitive imposes.

    This is the seventh distinguishing property. On the six coarse properties alone, ``embeds``
    and ``succeeds`` have identical signatures: both are directed, antisymmetric, transitive,
    acyclic, not observationally complete and not substitutive. They differ in the shape of their
    order. ``embeds`` orders its nodes as a forest, and ``succeeds`` orders them totally within
    each series.

    Declaring the shape makes that distinction mechanically checkable. A declared property that
    cannot be tested is a comment, not a specification. The shape does not add a primitive to the
    set of eight.
    """

    #: Every node has at most one parent. The relation forms a forest.
    FOREST = "forest"
    #: Every pair within one series is comparable. The relation forms a line.
    TOTAL_WITHIN_SERIES = "total_within_series"
    #: A partial order that branches freely.
    PARTIAL_ORDER = "partial_order"
    #: No order is imposed. Every undirected primitive, and ``references``,
    #: which is directed but permits cycles.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class PrimitiveProperties:
    """The declared formal signature of one primitive.

    The first six fields are the properties the primitive set was derived from.
    The rest are consequences that the invariants and the schema need to read
    mechanically. Every one of them is checkable against an emitted graph. A
    declared property that cannot be tested is a comment, not a specification.
    """

    primitive: Primitive

    # The six distinguishing properties.
    #: Does reversing the relation change its meaning?
    directed: bool
    #: Does A to B imply B to A?
    symmetric: bool
    #: Does A to B and B to C imply A to C, *as asserted*?
    transitive: bool
    #: Is a cycle a contradiction? ``None`` where acyclicity does not apply,
    #: which is every undirected relation.
    acyclic: bool | None
    #: The shape of the order imposed. Separates ``embeds`` from ``succeeds``,
    #: which agree on every other distinguishing property.
    order_shape: OrderShape
    #: After the engine finishes its normal work, can it know it found them all?
    observationally_complete: bool
    #: Does the relation license transferring a fact from one endpoint to the
    #: other? True for exactly one primitive, and that is why it is separate.
    substitutive: bool

    # Consequences the rest of the engine reads.
    #: Every node has at most one parent and there are no cycles.
    forms_forest: bool
    #: The relation is not transitive as asserted, but its closure is a
    #: meaningful object the engine maintains separately.
    closure_meaningful: bool
    #: Transitivity holds only within a fixed respect.
    transitivity_scoped_to_respect: bool
    #: A respect must be named on every edge of this primitive.
    requires_respect: bool
    #: An expansion state must be recorded on every edge of this primitive.
    requires_expansion_state: bool
    #: Every pair within one series must be comparable.
    total_within_series: bool
    #: Cycles are permitted and are not a defect.
    permits_cycles: bool
    #: Edges carry a weight that must be earned against the null model.
    weighted: bool

    definition: str
    identity_condition: str
    #: Opaque identifiers in published vocabularies. Exact CURIEs are pinned
    #: against current ontology releases rather than transcribed, since they
    #: drift and a wrong identifier is worse than none.
    maps_to: tuple[str, ...]


def _props(
    primitive: Primitive,
    *,
    directed: bool,
    symmetric: bool,
    transitive: bool,
    acyclic: bool | None,
    order_shape: OrderShape,
    observationally_complete: bool,
    substitutive: bool,
    definition: str,
    identity_condition: str,
    maps_to: tuple[str, ...],
    forms_forest: bool = False,
    closure_meaningful: bool = False,
    transitivity_scoped_to_respect: bool = False,
    requires_respect: bool = False,
    requires_expansion_state: bool = False,
    total_within_series: bool = False,
    permits_cycles: bool = False,
    weighted: bool = False,
) -> PrimitiveProperties:
    """Construct one row of the signature table."""
    return PrimitiveProperties(
        primitive=primitive,
        directed=directed,
        symmetric=symmetric,
        transitive=transitive,
        acyclic=acyclic,
        order_shape=order_shape,
        observationally_complete=observationally_complete,
        substitutive=substitutive,
        forms_forest=forms_forest,
        closure_meaningful=closure_meaningful,
        transitivity_scoped_to_respect=transitivity_scoped_to_respect,
        requires_respect=requires_respect,
        requires_expansion_state=requires_expansion_state,
        total_within_series=total_within_series,
        permits_cycles=permits_cycles,
        weighted=weighted,
        definition=definition,
        identity_condition=identity_condition,
        maps_to=maps_to,
    )


PRIMITIVE_PROPERTIES: dict[Primitive, PrimitiveProperties] = {
    row.primitive: row
    for row in (
        _props(
            Primitive.CONTAINS,
            directed=True,
            symmetric=False,
            transitive=True,
            acyclic=True,
            order_shape=OrderShape.FOREST,
            observationally_complete=True,
            substitutive=False,
            forms_forest=True,
            definition=(
                "The subject is a location in the filesystem namespace and the "
                "object is directly inside it."
            ),
            identity_condition=(
                "Two containment assertions are the same instance when they name "
                "the same parent entry and the same child entry."
            ),
            maps_to=("prov:hadMember", "dcterms:hasPart", "bfo:has-part", "rcc8:proper-part"),
        ),
        _props(
            Primitive.EMBEDS,
            directed=True,
            symmetric=False,
            transitive=True,
            acyclic=True,
            order_shape=OrderShape.FOREST,
            # The single property that separates embeds from contains. Absence of
            # an embeds edge means either the container holds nothing else or the
            # engine did not look far enough, and those are different facts.
            observationally_complete=False,
            substitutive=False,
            forms_forest=True,
            requires_expansion_state=True,
            definition=(
                "The subject is a container file and the object is a stream, "
                "member, or region recoverable from inside it by opening it."
            ),
            identity_condition=(
                "Two embedding assertions are the same instance when they name the "
                "same container entry and the same member locator."
            ),
            maps_to=("prov:hadMember", "dcterms:hasPart", "bfo:has-part"),
        ),
        _props(
            Primitive.DERIVES,
            directed=True,
            symmetric=False,
            # An asserted derivation is a direct claim: one step. It does not
            # follow that A derives from B's own source.
            transitive=False,
            acyclic=True,
            order_shape=OrderShape.PARTIAL_ORDER,
            observationally_complete=False,
            substitutive=False,
            closure_meaningful=True,
            definition=(
                "The object was produced from the subject by some process, such "
                "that the subject's content materially determined the object's."
            ),
            identity_condition=(
                "Two derivation assertions are the same instance when they name the "
                "same producer, the same product, and the same activity where one "
                "is reified."
            ),
            maps_to=(
                "prov:wasDerivedFrom",
                "prov:used",
                "prov:wasGeneratedBy",
                "dcterms:source",
                "pav:derivedFrom",
                "ro:derives-from",
            ),
        ),
        _props(
            Primitive.IDENTICAL,
            directed=False,
            symmetric=True,
            transitive=True,
            acyclic=None,
            order_shape=OrderShape.NONE,
            observationally_complete=False,
            # The one substitutive relation. Every fact derived from one file's
            # content is automatically true of the other, so content work is done
            # once per distinct content rather than once per path.
            substitutive=True,
            definition=(
                "The two entities have the same content, established by "
                "cryptographic comparison of the whole content."
            ),
            identity_condition=(
                "Two identity assertions are the same instance when they name the "
                "same unordered pair of entries under the same hash algorithm."
            ),
            maps_to=("prov:alternateOf", "skos:exactMatch"),
        ),
        _props(
            Primitive.EQUIVALENT,
            directed=False,
            symmetric=True,
            transitive=True,
            acyclic=None,
            order_shape=OrderShape.NONE,
            observationally_complete=False,
            substitutive=False,
            transitivity_scoped_to_respect=True,
            requires_respect=True,
            definition=(
                "The two entities are the same with respect to a stated dimension "
                "R, and differ or are unknown in every other respect."
            ),
            identity_condition=(
                "Two equivalence assertions are the same instance when they name "
                "the same unordered pair of entries under the same respect."
            ),
            maps_to=("prov:specializationOf", "skos:closeMatch"),
        ),
        _props(
            Primitive.SUCCEEDS,
            directed=True,
            symmetric=False,
            transitive=True,
            acyclic=True,
            order_shape=OrderShape.TOTAL_WITHIN_SERIES,
            observationally_complete=False,
            substitutive=False,
            total_within_series=True,
            definition=(
                "The subject comes after the object in an ordered series to which "
                "both belong. Order only; it claims no material dependence."
            ),
            identity_condition=(
                "Two succession assertions are the same instance when they name the "
                "same ordered pair within the same series."
            ),
            maps_to=(
                "prov:wasRevisionOf",
                "dcterms:replaces",
                "pav:previousVersion",
                "ro:preceded-by",
            ),
        ),
        _props(
            Primitive.REFERENCES,
            directed=True,
            symmetric=False,
            transitive=False,
            # The only primitive where a cycle is ordinary rather than wrong.
            # Two scripts genuinely can reference each other.
            acyclic=False,
            order_shape=OrderShape.NONE,
            observationally_complete=False,
            substitutive=False,
            permits_cycles=True,
            definition=(
                "The subject mentions, names, or points at the object, without "
                "necessarily depending on it."
            ),
            identity_condition=(
                "Two reference assertions are the same instance when they name the "
                "same source entry, the same reference string, and the same "
                "resolution outcome."
            ),
            maps_to=("dcterms:references", "prov:wasInformedBy", "skos:related"),
        ),
        _props(
            Primitive.CONCERNS,
            directed=False,
            symmetric=True,
            # Aboutness chains do not compose. Treating topical association as
            # transitive connects everything to everything after three or four
            # hops, which is the classic failure of naive similarity graphs.
            transitive=False,
            acyclic=None,
            order_shape=OrderShape.NONE,
            observationally_complete=False,
            substitutive=False,
            weighted=True,
            definition="The two entities are about the same subject matter.",
            identity_condition=(
                "Two aboutness assertions are the same instance when they name the "
                "same unordered pair under the same theme kind."
            ),
            maps_to=("skos:related", "dcterms:subject", "foaf:topic"),
        ),
    )
}


def signature_of(
    primitive: Primitive,
) -> tuple[bool, bool, bool, bool | None, OrderShape, bool, bool]:
    """Return the distinguishing signature of a primitive as a comparable tuple.

    Two primitives with the same signature are one primitive with a parameter, so all eight
    signatures must differ. A test asserts this instead of trusting it, because it is the entire
    justification for the size of the set.
    """
    props = PRIMITIVE_PROPERTIES[primitive]
    return (
        props.directed,
        props.symmetric,
        props.transitive,
        props.acyclic,
        props.order_shape,
        props.observationally_complete,
        props.substitutive,
    )


# ---------------------------------------------------------------------------
# Parameters carried on particular primitives.
# ---------------------------------------------------------------------------


class ExpansionState(StrEnum):
    """Why the engine stopped expanding a container. Required on every ``embeds``.

    This field is what makes the absence of an embedding edge readable. Without
    it, "the archive holds nothing else" and "the engine did not look far enough"
    are the same silence, and a closed-world statement could rest on the second.
    """

    #: The container was fully expanded. A closed-world statement is permitted.
    COMPLETE = "complete"
    #: Expansion stopped at a budget, a recursion limit, or a member count.
    BOUNDED = "bounded"
    #: The container declined to open: encrypted, password protected, or a
    #: format the configuration forbids opening.
    REFUSED = "refused"
    #: Expansion was attempted and errored.
    FAILED = "failed"


class ReferenceResolution(StrEnum):
    """How well a reference string resolved. Recorded on every ``references``.

    These are not interchangeable, and keeping them apart is what lets the engine
    say "this script claims to write that file, and that file does not exist",
    which is a finding rather than an error.
    """

    #: The string named an existing entry unambiguously and completely.
    EXACT = "exact"
    #: The string matched the tail of exactly one entry's path.
    UNIQUE_SUFFIX = "unique_suffix"
    #: The string matched exactly one entry's basename.
    UNIQUE_BASENAME = "unique_basename"
    #: The string matched more than one entry and no rule chose between them.
    AMBIGUOUS = "ambiguous"
    #: The string matched nothing. The reference stands, and the target does not exist.
    UNRESOLVED = "unresolved"


class AllenRelation(StrEnum):
    """Allen's thirteen interval relations, recorded as a parameter on ``succeeds``.

    Files and sessions have extents, not only instants, and "overlaps" is
    different evidence from "before". These are parameters on an existing
    primitive rather than primitives of their own: they qualify an ordering, they
    do not constitute a new kind of relationship.

    ``DURING`` and ``CONTAINS_INTERVAL`` are Allen's *during* and *contains*. The
    second is spelled out to keep it clearly distinct from the ``contains``
    primitive, which is filesystem containment and unrelated.
    """

    BEFORE = "before"
    AFTER = "after"
    MEETS = "meets"
    MET_BY = "met_by"
    OVERLAPS = "overlaps"
    OVERLAPPED_BY = "overlapped_by"
    STARTS = "starts"
    STARTED_BY = "started_by"
    DURING = "during"
    CONTAINS_INTERVAL = "contains_interval"
    FINISHES = "finishes"
    FINISHED_BY = "finished_by"
    EQUALS = "equals"


class QuotientOrigin(StrEnum):
    """Whether an edge at a coarse level was lifted from below or asserted there.

    Some relationships exist only at a coarse level and have no fine-grained
    counterpart: two sets of experiments relate within a month while their movie
    files relate within a day. A model that only lifts cannot express that, so
    rules may attach to a level and assert natively at it.
    """

    #: The edge exists because edges exist between members of the two classes.
    LIFTED = "lifted"
    #: The edge was asserted directly at this level by a rule that runs there.
    NATIVE = "native"


# ---------------------------------------------------------------------------
# Specializations.
# ---------------------------------------------------------------------------


class SpecializationOrigin(StrEnum):
    """Where a specialization came from."""

    #: Ships with the engine. Drawn from published vocabularies so that names
    #: are recognizable across projects rather than invented per laboratory.
    BUILTIN = "builtin"
    #: Supplied by an installable domain pack.
    PACK = "pack"
    #: Defined by the user in this project.
    USER = "user"


@dataclass(frozen=True, slots=True)
class Specialization:
    """A named relation with a declared parent primitive and fixed parameters.

    A specialization inherits every formal property of its parent and may not
    contradict them. It costs one registry entry, no schema change and no new
    invariant, which is what makes an expressive vocabulary affordable without
    fragmenting the primitive set.
    """

    name: str
    parent: Primitive
    #: The respect fixed by this specialization, where its parent takes one.
    respect: str | None
    #: Further relations this specialization implies, written as
    #: ``"primitive(respect)"``. The field is declarative. The engine reads it, and no rule
    #: hard-codes it.
    additional: tuple[str, ...]
    definition: str
    origin: SpecializationOrigin
    #: A disabled specialization stays in the table marked inactive, so a graph
    #: built under a different configuration is still interpretable.
    enabled: bool = True


def _spec(
    name: str,
    parent: Primitive,
    definition: str,
    *,
    respect: str | None = None,
    additional: tuple[str, ...] = (),
) -> Specialization:
    """Construct one builtin catalog entry."""
    return Specialization(
        name=name,
        parent=parent,
        respect=respect,
        additional=additional,
        definition=definition,
        origin=SpecializationOrigin.BUILTIN,
        enabled=True,
    )


#: The catalog that ships with the engine. Users may add to it and should rarely
#: need to: ad-hoc vocabulary is the fragmentation failure the primitive set
#: exists to prevent, and two true statements about one pair of files recorded
#: under different names fail to connect.
SPECIALIZATION_CATALOG: tuple[Specialization, ...] = (
    # Under derives.
    _spec("generated_by", Primitive.DERIVES, "The object was produced by the named activity."),
    _spec("used_as_input", Primitive.DERIVES, "The subject was consumed by the named activity."),
    _spec("processed_from", Primitive.DERIVES, "Produced by a processing step over the subject."),
    _spec("rendered_from", Primitive.DERIVES, "A visual rendering of the subject."),
    _spec("summarized_from", Primitive.DERIVES, "A reduction or aggregate of the subject."),
    _spec("converted_from", Primitive.DERIVES, "The same content in a different format."),
    _spec("corrected_from", Primitive.DERIVES, "The subject with a correction applied."),
    # Under equivalent.
    _spec(
        "replicate_of",
        Primitive.EQUIVALENT,
        "Same experimental design, performed on a separate occasion.",
        respect="experimental_condition",
        additional=("succeeds(session_order)",),
    ),
    _spec(
        "variant_of",
        Primitive.EQUIVALENT,
        "Same design with one dimension deliberately changed.",
        respect="design",
    ),
    _spec(
        "same_schema_as",
        Primitive.EQUIVALENT,
        "Same normalized column names and types.",
        respect="schema",
    ),
    _spec(
        "same_shape_as",
        Primitive.EQUIVALENT,
        "Same array shape and dtype signature.",
        respect="shape",
    ),
    _spec(
        "same_condition_as",
        Primitive.EQUIVALENT,
        "Same experimental condition, without implying a separate occasion.",
        respect="experimental_condition",
    ),
    _spec(
        "same_acquisition_as",
        Primitive.EQUIVALENT,
        "Produced by one acquisition event.",
        respect="acquisition_event",
    ),
    _spec(
        "reanalysis_of",
        Primitive.EQUIVALENT,
        "The same input analyzed again, by the same or a different method.",
        respect="analysis_input",
    ),
    # Under succeeds.
    _spec("version_of", Primitive.SUCCEEDS, "A later version in a version series."),
    _spec("revision_of", Primitive.SUCCEEDS, "A revision of the subject."),
    _spec("rerun_of", Primitive.SUCCEEDS, "The same procedure run again."),
    _spec("session_after", Primitive.SUCCEEDS, "A later session in a session series."),
    # Under references.
    _spec("documents", Primitive.REFERENCES, "The subject describes the object."),
    _spec("cites", Primitive.REFERENCES, "The subject cites the object."),
    _spec("configures", Primitive.REFERENCES, "The subject supplies configuration to the object."),
    _spec(
        "declares_output",
        Primitive.REFERENCES,
        "The subject states that it writes the object. The object may not exist.",
    ),
    _spec(
        "declares_input",
        Primitive.REFERENCES,
        "The subject states that it reads the object. The object may not exist.",
    ),
    # Under concerns.
    _spec("about_technique", Primitive.CONCERNS, "Shares a technique.", respect="technique"),
    _spec("about_reagent", Primitive.CONCERNS, "Shares a reagent.", respect="reagent"),
    _spec("about_instrument", Primitive.CONCERNS, "Shares an instrument.", respect="instrument"),
    _spec("about_subject", Primitive.CONCERNS, "Shares a subject of study.", respect="subject"),
    _spec("about_project", Primitive.CONCERNS, "Belongs to the same project.", respect="project"),
    # Under contains and embeds.
    _spec("directory_of", Primitive.CONTAINS, "A directory holding the object."),
    _spec("archive_member", Primitive.EMBEDS, "A member of an archive."),
    _spec("sheet_of", Primitive.EMBEDS, "A sheet within a workbook."),
    _spec("attachment_of", Primitive.EMBEDS, "An attachment within a document."),
    _spec("stream_of", Primitive.EMBEDS, "A stream within a container file."),
)


class SpecializationRegistry:
    """The live specialization table: the shipped catalog plus local additions.

    Nothing in the engine names a specialization directly. Rules and extractors
    resolve one through this registry, which is what makes disabling a
    specialization a configuration change rather than a code change, and what
    lets a specialization be deleted by removing a single catalog entry.
    """

    def __init__(self, catalog: tuple[Specialization, ...] = SPECIALIZATION_CATALOG) -> None:
        self._entries: dict[str, Specialization] = {}
        for entry in catalog:
            self.register(entry)

    def register(self, specialization: Specialization) -> None:
        """Add a specialization, refusing a name that is already taken.

        Two specializations under one name is the fragmentation failure in its
        purest form, so it is an error rather than a last-write-wins.
        """
        if specialization.name in self._entries:
            message = f"specialization {specialization.name!r} is already registered"
            raise ValueError(message)
        parent = PRIMITIVE_PROPERTIES[specialization.parent]
        if parent.requires_respect and specialization.respect is None:
            message = (
                f"specialization {specialization.name!r} has parent "
                f"{specialization.parent.value!r}, which requires a respect"
            )
            raise ValueError(message)
        self._entries[specialization.name] = specialization

    def set_enabled(self, name: str, *, enabled: bool) -> Specialization:
        """Enable or disable a specialization without removing it.

        Disabling leaves the entry in place marked inactive. A graph built while
        it was enabled stays interpretable, and no rule silently reroutes around
        the block: a rule that needed the specialization simply does not fire.
        """
        entry = self._entries[name]
        updated = replace(entry, enabled=enabled)
        self._entries[name] = updated
        return updated

    def get(self, name: str) -> Specialization:
        """Return one specialization whether or not it is enabled."""
        return self._entries[name]

    def resolve(self, name: str) -> Primitive:
        """Return the parent primitive of an enabled specialization.

        Raises when the specialization is disabled, because an assertion may not
        rest on a specialization the configuration has switched off. The INV-9 check in
        ``core/invariants.py`` (every asserted edge names a rule and a rule version, and every
        specialization resolves) tests the same condition over an emitted graph.
        """
        entry = self._entries[name]
        if not entry.enabled:
            message = f"specialization {name!r} is disabled and cannot be resolved"
            raise LookupError(message)
        return entry.parent

    def names(self, *, enabled_only: bool = False) -> tuple[str, ...]:
        """Return registered specialization names in canonical order."""
        return tuple(
            sorted(
                name for name, entry in self._entries.items() if entry.enabled or not enabled_only
            )
        )

    def under(self, primitive: Primitive) -> tuple[Specialization, ...]:
        """Return every specialization declaring the given parent."""
        return tuple(
            sorted(
                (e for e in self._entries.values() if e.parent is primitive),
                key=lambda e: e.name,
            )
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return name in self._entries
