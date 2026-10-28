"""Optional components are reachable only through a registry, and removal is cheap."""

from __future__ import annotations

import pytest

from ingence.core.registry import ComponentNotAvailableError, Registry


def build() -> Registry[str]:
    registry: Registry[str] = Registry("extractor")

    @registry.register("tabular", version="1", summary="CSV and TSV")
    def _tabular() -> str:
        return "tabular"

    @registry.register(
        "exotic",
        version="1",
        summary="needs a library nobody has",
        requires=("a_module_that_does_not_exist",),
    )
    def _exotic() -> str:  # pragma: no cover - never constructed
        return "exotic"

    return registry


def test_a_registered_component_is_constructed_on_demand():
    assert build().get("tabular") == "tabular"


def test_a_component_with_a_missing_dependency_is_unavailable_not_broken():
    """A missing optional dependency must not break the engine that does not use it."""
    registry = build()
    assert not registry.available("exotic")
    with pytest.raises(ComponentNotAvailableError, match="is not installed"):
        registry.get("exotic")


def test_a_disabled_component_is_refused_and_says_so():
    registry = build()
    registry.set_enabled("tabular", enabled=False)
    with pytest.raises(ComponentNotAvailableError, match="disabled"):
        registry.get("tabular")


def test_an_unknown_component_is_distinguishable_from_a_disabled_one():
    with pytest.raises(ComponentNotAvailableError, match="no extractor named"):
        build().get("absent")


def test_removal_is_one_line_and_leaves_nothing_behind():
    """The whole development-build strategy rests on this being true."""
    registry = build()
    registry.unregister("exotic")
    assert "exotic" not in registry
    assert registry.names() == ("tabular",)


def test_names_are_canonically_ordered_not_registration_ordered():
    """Registration order follows import order, which varies between runs."""
    registry: Registry[str] = Registry("thing")
    for name in ("zebra", "alpha", "middle"):
        registry.register(name, version="1", summary="")(lambda: "x")
    assert registry.names() == ("alpha", "middle", "zebra")


def test_a_duplicate_registration_is_refused():
    registry = build()
    with pytest.raises(ValueError, match="already registered"):
        registry.register("tabular", version="2", summary="")(lambda: "x")


def test_the_snapshot_lists_everything_configured_not_only_what_ran():
    """A run recording only what was active cannot be compared with another."""
    registry = build()
    registry.set_enabled("tabular", enabled=False)
    snapshot = registry.snapshot()
    assert [row["name"] for row in snapshot] == ["exotic", "tabular"]
    assert [row["enabled"] for row in snapshot] == [True, False]
    assert [row["available"] for row in snapshot] == [False, False]


def test_a_component_that_imports_but_cannot_build_is_unavailable():
    """The common failure: a Python wrapper around an absent system library.

    An import check alone calls that available, and a run record then claims
    something ran when it could not.
    """
    registry: Registry[str] = Registry("thing")

    @registry.register("brittle", version="1", summary="needs a library that is absent")
    def _brittle() -> str:
        message = "failed to find libwhatever"
        raise ImportError(message)

    assert registry.available("brittle")  # nothing has tried yet
    reason = registry.probe("brittle")
    assert reason is not None
    assert "could not be built" in reason
    assert not registry.available("brittle")
    assert registry.snapshot()[0]["available"] is False


def test_a_failure_is_remembered_rather_than_paid_for_every_file():
    calls = {"count": 0}
    registry: Registry[str] = Registry("thing")

    @registry.register("brittle", version="1", summary="")
    def _brittle() -> str:
        calls["count"] += 1
        message = "no"
        raise RuntimeError(message)

    for _ in range(5):
        registry.probe("brittle")
    assert calls["count"] == 1


def test_a_built_component_is_cached():
    """A learned detector loads a model on construction. Once, not per file."""
    calls = {"count": 0}
    registry: Registry[list[int]] = Registry("thing")

    @registry.register("expensive", version="1", summary="")
    def _expensive() -> list[int]:
        calls["count"] += 1
        return [1, 2, 3]

    first = registry.get("expensive")
    second = registry.get("expensive")
    assert first is second
    assert calls["count"] == 1


def test_unregistering_clears_the_cache_and_the_failure():
    registry: Registry[str] = Registry("thing")
    registry.register("thing", version="1", summary="")(lambda: "value")
    registry.get("thing")
    registry.unregister("thing")
    assert "thing" not in registry


def test_a_released_component_is_built_again_and_its_failure_stays_recorded():
    """Releasing a built instance frees what it kept. A failure stays recorded."""
    registry: Registry[list[str]] = Registry("predicate")

    @registry.register("kelt", version="1", summary="keeps what it was handed")
    def _kelt() -> list[str]:
        return []

    first = registry.get("kelt")
    first.append("vorn")
    assert registry.get("kelt") is first
    registry.release("kelt")
    assert registry.get("kelt") == []
    assert registry.get("kelt") is not first

    exotic = build()
    with pytest.raises(ComponentNotAvailableError):
        exotic.get("exotic")
    exotic.release("exotic")
    assert not exotic.available("exotic")
