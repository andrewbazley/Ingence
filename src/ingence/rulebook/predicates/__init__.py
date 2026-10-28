"""Rule predicates, one module per effect class.

A predicate is bound to a rule declaration through the registry. Nothing calls a
predicate by name.

Every predicate declares what it reads. The rule's effect decides what a predicate is handed,
and the registration narrows it (see :mod:`ingence.infer.subjects`). A classifying or grouping
rule gets one entry's records or part of them. A proposing or constraining rule gets a pair
drawn from one class of an index. A flag gets an entry, a group or a scope.

Some predicates are declared and not yet evaluable. A predicate whose records or whose phase do
not exist yet is registered and unavailable, with the reason recorded, in the same way as an
extractor whose optional dependency is missing. So the Rulebook can be written, validated,
hashed and rendered before the phases that run it exist, and no predicate pretends to run.
"""

from __future__ import annotations

__all__ = ["load_builtins"]


def load_builtins() -> None:
    """Import the modules that register the built-in predicates.

    Explicit rather than implicit, for the reason
    :func:`ingence.extractors.load_builtins` is: importing them at this module's
    top level would reintroduce the coupling the registry exists to prevent.

    Removing a predicate is deleting its module and its line here, and every rule
    that named it then fails to load loudly rather than silently abstaining.
    """
    from ingence.rulebook.predicates import answered as _answered  # noqa: F401
    from ingence.rulebook.predicates import areas as _areas  # noqa: F401
    from ingence.rulebook.predicates import classify as _classify  # noqa: F401
    from ingence.rulebook.predicates import clocks as _clocks  # noqa: F401
    from ingence.rulebook.predicates import constrain as _constrain  # noqa: F401
    from ingence.rulebook.predicates import copies as _copies  # noqa: F401
    from ingence.rulebook.predicates import data_points as _data_points  # noqa: F401
    from ingence.rulebook.predicates import declared as _declared  # noqa: F401
    from ingence.rulebook.predicates import domains as _domains  # noqa: F401
    from ingence.rulebook.predicates import enumeration as _enumeration  # noqa: F401
    from ingence.rulebook.predicates import experiments as _experiments  # noqa: F401
    from ingence.rulebook.predicates import file_types as _file_types  # noqa: F401
    from ingence.rulebook.predicates import flag as _flag  # noqa: F401
    from ingence.rulebook.predicates import group as _group  # noqa: F401
    from ingence.rulebook.predicates import notes as _notes  # noqa: F401
    from ingence.rulebook.predicates import packs as _packs  # noqa: F401
    from ingence.rulebook.predicates import programs as _programs  # noqa: F401
    from ingence.rulebook.predicates import propose as _propose  # noqa: F401
    from ingence.rulebook.predicates import spellings as _spellings  # noqa: F401
    from ingence.rulebook.predicates import substitution as _substitution  # noqa: F401
    from ingence.rulebook.predicates import workflows as _workflows  # noqa: F401
