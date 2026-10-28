"""Identity is deterministic, canonical, and carries no evidence."""

from __future__ import annotations

import unicodedata

import pytest

from ingence.core.ids import (
    PROJECT_SCOPE,
    CaseSensitivity,
    LocatorStep,
    Scope,
    ScopeKind,
    canonical_json,
    canonical_relative_path,
    canonical_sorted,
    edge_id,
    entry_id,
    root_id,
)
from ingence.core.relations import Primitive

ROOT = root_id("file:///data")


def test_identity_is_stable_across_repeated_calls():
    first = entry_id(ROOT, "a/b/c.tif")
    second = entry_id(ROOT, "a/b/c.tif")
    assert first == second


def test_decomposed_and_precomposed_names_agree():
    """macOS stores names decomposed; Linux stores the bytes it was given.

    Without normalisation one file scanned from two machines gets two identities,
    and no amount of later reconciliation recovers the link.
    """
    precomposed = unicodedata.normalize("NFC", "café.txt")
    decomposed = unicodedata.normalize("NFD", "café.txt")
    assert precomposed != decomposed
    assert entry_id(ROOT, precomposed) == entry_id(ROOT, decomposed)


def test_windows_separators_agree_with_posix():
    assert entry_id(ROOT, r"a\b\c.tif") == entry_id(ROOT, "a/b/c.tif")


def test_leading_and_trailing_separators_are_ignored():
    assert entry_id(ROOT, "./a/b/") == entry_id(ROOT, "a/b")


def test_case_is_folded_only_where_the_filesystem_is_insensitive():
    sensitive = CaseSensitivity.SENSITIVE
    insensitive = CaseSensitivity.INSENSITIVE
    assert entry_id(ROOT, "Foo.txt", case_sensitivity=sensitive) != entry_id(
        ROOT, "foo.txt", case_sensitivity=sensitive
    )
    assert entry_id(ROOT, "Foo.txt", case_sensitivity=insensitive) == entry_id(
        ROOT, "foo.txt", case_sensitivity=insensitive
    )


def test_unknown_case_sensitivity_keeps_files_apart():
    """Keeping two files apart that might be one is recoverable. Merging is not."""
    assert entry_id(ROOT, "Foo.txt", case_sensitivity=CaseSensitivity.UNKNOWN) == entry_id(
        ROOT, "Foo.txt", case_sensitivity=CaseSensitivity.SENSITIVE
    )


def test_an_empty_locator_chain_is_the_ordinary_case():
    assert entry_id(ROOT, "a/b.zip") == entry_id(ROOT, "a/b.zip", ())


def test_a_locator_chain_produces_a_different_entry():
    inside = (LocatorStep(scheme="zip", target="sheet.csv"),)
    assert entry_id(ROOT, "a/b.zip") != entry_id(ROOT, "a/b.zip", inside)


def test_an_ordinal_separates_two_members_with_one_target():
    first = (LocatorStep(scheme="stream", target="image", ordinal=0),)
    second = (LocatorStep(scheme="stream", target="image", ordinal=1),)
    assert entry_id(ROOT, "a.tif", first) != entry_id(ROOT, "a.tif", second)


def test_a_relocated_root_keeps_its_entry_identities():
    """Identity is the logical root, never the mount path."""
    assert entry_id(root_id("file:///data"), "x") == entry_id(root_id("file:///data"), "x")
    assert entry_id(root_id("file:///data"), "x") != entry_id(root_id("file:///other"), "x")


def test_a_symmetric_edge_is_one_edge_whichever_way_it_is_written():
    forward = edge_id(
        source="A",
        target="B",
        primitive=Primitive.IDENTICAL,
        rule_id="r",
        rule_version="1",
        scope=PROJECT_SCOPE,
    )
    backward = edge_id(
        source="B",
        target="A",
        primitive=Primitive.IDENTICAL,
        rule_id="r",
        rule_version="1",
        scope=PROJECT_SCOPE,
    )
    assert forward == backward


def test_a_directed_edge_is_two_different_edges():
    forward = edge_id(
        source="A",
        target="B",
        primitive=Primitive.DERIVES,
        rule_id="r",
        rule_version="1",
        scope=PROJECT_SCOPE,
    )
    backward = edge_id(
        source="B",
        target="A",
        primitive=Primitive.DERIVES,
        rule_id="r",
        rule_version="1",
        scope=PROJECT_SCOPE,
    )
    assert forward != backward


def test_equivalence_without_a_respect_is_refused():
    with pytest.raises(ValueError, match="requires a respect"):
        edge_id(
            source="A",
            target="B",
            primitive=Primitive.EQUIVALENT,
            rule_id="r",
            rule_version="1",
            scope=PROJECT_SCOPE,
        )


def test_respect_rule_version_and_scope_all_change_edge_identity():
    base = {
        "source": "A",
        "target": "B",
        "primitive": Primitive.EQUIVALENT,
        "rule_id": "r",
        "rule_version": "1",
        "scope": PROJECT_SCOPE,
        "respect": "schema",
    }
    reference = edge_id(**base)
    assert reference != edge_id(**{**base, "respect": "shape"})
    assert reference != edge_id(**{**base, "rule_version": "2"})
    assert reference != edge_id(**{**base, "rule_id": "other"})
    assert reference != edge_id(**{**base, "scope": Scope(ScopeKind.FOLDER, "sub")})


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_sort_is_by_code_point_not_locale():
    assert canonical_sorted(["b", "A", "a"]) == ("A", "a", "b")


def test_canonical_path_normalisation_is_idempotent():
    once = canonical_relative_path("./A/B/")
    assert canonical_relative_path(once) == once
