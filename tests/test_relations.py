"""The primitive set is what the specification says it is."""

from __future__ import annotations

import pytest

from ingence.core.relations import (
    PRIMITIVE_PROPERTIES,
    SPECIALIZATION_CATALOG,
    Primitive,
    Specialization,
    SpecializationOrigin,
    SpecializationRegistry,
    signature_of,
)


def test_there_are_exactly_eight_primitives():
    assert len(Primitive) == 8
    assert len(PRIMITIVE_PROPERTIES) == 8


def test_every_signature_is_distinct():
    """The entire justification for the size of the set.

    Two primitives with one signature are one primitive with a parameter. If this
    ever fails, either a primitive was added without a formal argument or one of
    the declared properties is wrong.
    """
    signatures = {signature_of(primitive) for primitive in Primitive}
    assert len(signatures) == 8


def test_contains_is_the_only_observationally_complete_primitive():
    """The property the closed-world statement rests on, and it rests on one."""
    complete = [
        primitive
        for primitive, props in PRIMITIVE_PROPERTIES.items()
        if props.observationally_complete
    ]
    assert complete == [Primitive.CONTAINS]


def test_identical_is_the_only_substitutive_primitive():
    substitutive = [
        primitive for primitive, props in PRIMITIVE_PROPERTIES.items() if props.substitutive
    ]
    assert substitutive == [Primitive.IDENTICAL]


def test_references_is_the_only_primitive_permitting_cycles():
    cyclic = [
        primitive for primitive, props in PRIMITIVE_PROPERTIES.items() if props.permits_cycles
    ]
    assert cyclic == [Primitive.REFERENCES]


def test_concerns_is_not_transitive():
    """Aboutness chains do not compose, and the declaration must say so."""
    assert PRIMITIVE_PROPERTIES[Primitive.CONCERNS].transitive is False


def test_embeds_requires_an_expansion_state_and_contains_does_not():
    assert PRIMITIVE_PROPERTIES[Primitive.EMBEDS].requires_expansion_state
    assert not PRIMITIVE_PROPERTIES[Primitive.CONTAINS].requires_expansion_state


def test_catalog_registers_and_every_entry_resolves():
    registry = SpecializationRegistry()
    assert len(registry) == len(SPECIALIZATION_CATALOG)
    for entry in SPECIALIZATION_CATALOG:
        assert registry.resolve(entry.name) is entry.parent


def test_a_specialization_under_equivalent_must_name_a_respect():
    registry = SpecializationRegistry(catalog=())
    with pytest.raises(ValueError, match="requires a respect"):
        registry.register(
            Specialization(
                name="respectless",
                parent=Primitive.EQUIVALENT,
                respect=None,
                additional=(),
                definition="",
                origin=SpecializationOrigin.USER,
            )
        )


def test_disabling_keeps_the_entry_but_refuses_resolution():
    """A disabled specialization stays interpretable and stops being usable."""
    registry = SpecializationRegistry()
    registry.set_enabled("replicate_of", enabled=False)
    assert "replicate_of" in registry
    assert registry.get("replicate_of").enabled is False
    with pytest.raises(LookupError, match="disabled"):
        registry.resolve("replicate_of")


def test_a_duplicate_name_is_refused():
    """Two relations under one name is the fragmentation failure itself."""
    registry = SpecializationRegistry()
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            Specialization(
                name="cites",
                parent=Primitive.REFERENCES,
                respect=None,
                additional=(),
                definition="a second, conflicting sense",
                origin=SpecializationOrigin.USER,
            )
        )
