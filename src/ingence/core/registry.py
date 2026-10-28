"""The registry that makes an optional component removable in one line.

The development build contains every adapter, every optional path and every backup approach, so
that each can be measured and then removed. That only works if removal is cheap, and removal is
cheap only if the code was designed for it. This module is that design.

The rule it enforces is simple and absolute:

    Nothing calls an optional component by name.

An extractor, an identity method, a decoder, a type-detection adapter, a graph backend, a
scheduler adapter: every one of them declares itself into a registry and is reached through it.
Deleting one then means deleting one module and one registration, and the test suite says
immediately whether anything depended on it. The alternative, direct imports scattered across the
engine, turns every removal into surgery and is why "we will slim it down later" usually does not
happen.

Two further properties follow, and both are needed elsewhere.

Feature flags, not code branches. Configuration enables or disables a component. A comparison run
is a configuration change instead of a checkout, which makes an experiment register's results
attributable to a specific configuration.

Every run states what was active. :meth:`Registry.snapshot` produces the record that goes into
the run record. Without it, a result measured under one configuration cannot be told apart from a
result measured under another, and the whole measure-then-subtract strategy loses its evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

__all__ = ["ComponentNotAvailableError", "Registration", "Registry"]


class ComponentNotAvailableError(LookupError):
    """A component was requested that is absent, disabled, or missing a dependency.

    Distinct from a plain ``KeyError`` because the three causes need different
    responses from a caller: absent means it was removed, disabled means the
    configuration switched it off, and unavailable means an optional dependency
    is not installed. The message says which.
    """


@dataclass(frozen=True, slots=True)
class Registration[T]:
    """One component's declaration.

    The factory is not called at registration time. An optional component whose
    dependency is missing must be registerable and merely unavailable, so that
    the run record can state honestly that it was configured and could not run,
    rather than the whole import failing.
    """

    name: str
    #: What this registry holds: ``extractor``, ``identity_method``, ``decoder``.
    #: Carried on the registration so a snapshot is readable on its own.
    kind: str
    factory: Callable[[], T]
    version: str
    #: One sentence. A component nobody can describe is a component nobody can
    #: decide to keep.
    summary: str
    #: True when the component may run. A disabled component stays registered
    #: and appears in the snapshot marked inactive, so a result produced while it
    #: was enabled is still interpretable.
    enabled: bool = True
    #: Import names this component needs. Checked lazily, at construction.
    requires: tuple[str, ...] = ()
    #: Free-form declared metadata, such as the formats an extractor claims.
    #: Read by the engine, never interpreted by this module.
    attributes: dict[str, str] = field(default_factory=dict)


class Registry[T]:
    """A named collection of interchangeable components of one kind.

    Construct one per kind at module scope and let components register into it.
    The engine asks the registry for what it needs and never imports a component
    module directly.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._registrations: dict[str, Registration[T]] = {}
        # Constructed components are cached. A learned detector loads a model on
        # construction, and building one per file would cost more than every
        # other part of a scan put together. Caching also makes availability
        # honest: a component is only known to work once it has been built.
        self._instances: dict[str, T] = {}
        # Why a component could not be built, recorded the first time it fails.
        # A dependency that imports but cannot initialise is the common case and
        # the one a bare import check misses.
        self._failures: dict[str, str] = {}

    # -- registration -----------------------------------------------------

    def register(
        self,
        name: str,
        *,
        version: str,
        summary: str,
        requires: tuple[str, ...] = (),
        attributes: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> Callable[[Callable[[], T]], Callable[[], T]]:
        """Return a decorator registering a factory under a name.

        Used as::

            @extractors.register("tabular", version="1", summary="CSV and TSV")
            def _build() -> Extractor:
                from ingence.extractors.tabular import TabularExtractor

                return TabularExtractor()

        The import sits inside the factory on purpose. A registry whose module
        imports every component at module scope has reintroduced the coupling it
        exists to prevent, and one missing optional dependency then breaks the
        whole engine rather than one component.
        """

        def decorator(factory: Callable[[], T]) -> Callable[[], T]:
            if name in self._registrations:
                message = f"{self.kind} {name!r} is already registered"
                raise ValueError(message)
            self._registrations[name] = Registration(
                name=name,
                kind=self.kind,
                factory=factory,
                version=version,
                summary=summary,
                enabled=enabled,
                requires=requires,
                attributes=dict(attributes or {}),
            )
            return factory

        return decorator

    def probe(self, name: str) -> str | None:
        """Try to build a component, returning why it could not be built.

        Returns ``None`` when it built successfully. Called before a run so the
        run record states what could actually run rather than what was merely
        installed, which are different facts and only one of them is useful when
        comparing two runs.
        """
        try:
            self.get(name)
        except ComponentNotAvailableError as exc:
            return str(exc)
        return None

    def unregister(self, name: str) -> None:
        """Remove a component entirely.

        Present so that a test can prove removal is clean. In the engine itself,
        removing a component means deleting its module and its registration, not
        calling this.
        """
        del self._registrations[name]
        self._instances.pop(name, None)
        self._failures.pop(name, None)

    def release(self, name: str) -> None:
        """Drop one built instance, so that what it kept from one pass is freed.

        After a component is built once, :meth:`get` hands out the same instance on every later
        call. A predicate that keeps a pass's pool to answer its next subject quickly keeps the
        pool after the pass as well: an inference's whole pool stays alive through compaction and
        until the next pass. After this call, the next :meth:`get` builds the component again. A
        recorded failure stays recorded.
        """
        self._instances.pop(name, None)

    def set_enabled(self, name: str, *, enabled: bool) -> Registration[T]:
        """Switch a component on or off without removing it."""
        current = self._registrations[name]
        updated = replace(current, enabled=enabled)
        self._registrations[name] = updated
        return updated

    # -- lookup -----------------------------------------------------------

    def available(self, name: str) -> bool:
        """Return True when the component is registered, enabled and usable.

        Usable, not merely importable. A component that has already failed to
        build is unavailable however present its dependency is: the common
        failure is a Python wrapper that imports cleanly around a system library
        that is not installed, and reporting that as available makes a run record
        claim something ran when it could not.
        """
        registration = self._registrations.get(name)
        if registration is None or not registration.enabled:
            return False
        if name in self._failures:
            return False
        if name in self._instances:
            return True
        return not self._missing_imports(registration)

    def get(self, name: str) -> T:
        """Construct and return one component.

        Raises :class:`ComponentNotAvailableError` with a message naming the
        cause, so a run record can record why a configured component did not run
        instead of recording only that it did not.
        """
        registration = self._registrations.get(name)
        if registration is None:
            message = f"no {self.kind} named {name!r} is registered"
            raise ComponentNotAvailableError(message)
        if not registration.enabled:
            message = f"{self.kind} {name!r} is registered but disabled"
            raise ComponentNotAvailableError(message)

        cached = self._instances.get(name)
        if cached is not None:
            return cached
        recorded = self._failures.get(name)
        if recorded is not None:
            raise ComponentNotAvailableError(recorded)

        missing = self._missing_imports(registration)
        if missing:
            message = f"{self.kind} {name!r} needs {', '.join(missing)}, which is not installed"
            self._failures[name] = message
            raise ComponentNotAvailableError(message)
        try:
            instance = registration.factory()
        except Exception as exc:
            # The common case is a Python wrapper that imports cleanly around a
            # system library that is not installed. An import check alone calls
            # that available, and a run record then claims something ran when it
            # could not. Recorded once and remembered, so a scan does not pay the
            # failure again for every file.
            message = f"{self.kind} {name!r} could not be built: {type(exc).__name__}: {exc}"
            self._failures[name] = message
            raise ComponentNotAvailableError(message) from exc
        self._instances[name] = instance
        return instance

    def declaration(self, name: str) -> Registration[T]:
        """Return a component's declaration without constructing it."""
        return self._registrations[name]

    def names(self, *, enabled_only: bool = False, available_only: bool = False) -> tuple[str, ...]:
        """Return component names in canonical order.

        Canonical order rather than registration order, because registration
        order depends on import order and anything derived from it would vary
        between runs.
        """
        names = sorted(self._registrations)
        if enabled_only:
            names = [n for n in names if self._registrations[n].enabled]
        if available_only:
            names = [n for n in names if self.available(n)]
        return tuple(names)

    # -- reporting --------------------------------------------------------

    def snapshot(self) -> tuple[dict[str, str | bool], ...]:
        """Return what goes into the run record, in canonical order.

        One entry per registered component, whether or not it ran. A run that
        records only what was active cannot be compared with a run that had a
        different set configured, and comparing runs is the entire point of the
        experiment register.
        """
        return tuple(
            {
                "kind": registration.kind,
                "name": registration.name,
                "version": registration.version,
                "enabled": registration.enabled,
                "available": self.available(registration.name),
            }
            for registration in (self._registrations[name] for name in sorted(self._registrations))
        )

    # -- dunder -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._registrations)

    def __contains__(self, name: object) -> bool:
        return name in self._registrations

    def __iter__(self) -> Iterator[Registration[T]]:
        for name in sorted(self._registrations):
            yield self._registrations[name]

    # -- internals --------------------------------------------------------

    @staticmethod
    def _missing_imports(registration: Registration[T]) -> tuple[str, ...]:
        """Return the declared requirements that cannot be imported."""
        import importlib.util

        return tuple(
            module for module in registration.requires if importlib.util.find_spec(module) is None
        )
