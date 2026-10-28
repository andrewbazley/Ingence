"""The catalogue of quality figures: what each is, in which pass it first exists, and its method.

A figure's label, its unit, what it is out of and the one line saying how it is counted live in a
YAML file beside the module that computes the figure (``catalogue.yaml`` for the core's). They
never live in the code that counts it, and are never repeated by hand in a page, a sentence or an
export. Every summary registered with :data:`SUMMARIES` names its catalogue.
:func:`catalogue` merges them in the registry's canonical order.

A figure the code emits that no catalogue names is refused when it is built, so a figure cannot
exist without its method.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "CORE_CATALOGUE",
    "PHASES",
    "Catalogue",
    "FigureDefinition",
    "catalogue",
    "load_catalogue",
    "phase_rank",
    "reached",
    "unreadable_catalogues",
]

#: The passes in the order they know things, and the rank of each. A rebind is a census that
#: reads names again, and a listing's remote readings add to what a dive holds.
PHASES: Mapping[str, int] = {"census": 0, "rebind": 0, "dive": 1, "listing": 1, "infer": 2}

#: The core's own catalogue, beside this module and declared in the wheel's artifacts.
CORE_CATALOGUE = Path(__file__).with_name("catalogue.yaml")


def phase_rank(phase: str) -> int:
    """How much a pass knows: 0 census, 1 dive, 2 inference."""
    return PHASES[phase]


@dataclass(frozen=True, slots=True)
class FigureDefinition:
    """One figure as its catalogue states it."""

    figure: str
    section: str
    label: str
    unit: str
    known_from: str
    method: str
    #: What the count is out of, in words. It is ``None`` for a figure with no whole.
    total: str | None = None
    #: The breakdown's identifiers and their words, for a closed breakdown.
    facets: Mapping[str, str] = field(default_factory=dict)
    #: How an open breakdown's identifier is written, for a figure whose facets are not listed.
    facet_form: str | None = None


@dataclass(frozen=True, slots=True)
class Catalogue:
    """Every figure of every registered summary, with the sections' words and the versions."""

    sections: Mapping[str, str]
    figures: Mapping[str, FigureDefinition]
    versions: Mapping[str, str]
    #: Why each enabled summary's catalogue was left out, by the summary's name: it could not be
    #: read, or it defines a figure an earlier catalogue defines. Empty when all are in.
    problems: Mapping[str, str] = field(default_factory=dict)

    def __contains__(self, figure: object) -> bool:
        return figure in self.figures

    def facet_words(self, figure: str, facet: str) -> str:
        """A breakdown value as the catalogue words it, or as written for an open one."""
        return self.figures[figure].facets.get(facet, facet)


def reached(known_from: str, phase: str) -> bool:
    """Whether a pass of this phase knows what a figure first known from ``known_from`` counts."""
    return phase_rank(phase) >= phase_rank(known_from)


def _text(value: Any) -> str:
    return " ".join(str(value).split())


@functools.cache
def load_catalogue(path: Path) -> Catalogue:
    """Read one catalogue file, refusing a figure that lacks a part of its definition."""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        message = f"{path.name}: a catalogue is a mapping"
        raise TypeError(message)
    figures: dict[str, FigureDefinition] = {}
    for name, held in (document.get("figures") or {}).items():
        missing = [
            part
            for part in ("section", "label", "unit", "known_from", "method")
            if part not in held
        ]
        if missing:
            message = f"{path.name}: figure {name} lacks {', '.join(missing)}"
            raise ValueError(message)
        if held["known_from"] not in PHASES:
            message = f"{path.name}: figure {name} is known from {held['known_from']!r}, no pass"
            raise ValueError(message)
        figures[str(name)] = FigureDefinition(
            figure=str(name),
            section=str(held["section"]),
            label=_text(held["label"]),
            unit=_text(held["unit"]),
            known_from=str(held["known_from"]),
            method=_text(held["method"]),
            total=_text(held["total"]) if held.get("total") else None,
            facets={str(k): _text(v) for k, v in (held.get("facets") or {}).items()},
            facet_form=_text(held["facet_form"]) if held.get("facet_form") else None,
        )
    return Catalogue(
        sections={str(k): _text(v) for k, v in (document.get("sections") or {}).items()},
        figures=figures,
        versions={str(k): _text(v) for k, v in (document.get("versions") or {}).items()},
    )


#: What a catalogue file that cannot be read raises: a missing file, bad YAML, a figure lacking
#: part of its definition.
UNREADABLE = (OSError, ValueError, TypeError, yaml.YAMLError)


def unreadable_catalogues() -> dict[str, str]:
    """Why each enabled summary's catalogue was left out, by name, or an empty mapping.

    A summary without a catalogue in has no figures that can be stated, and is not recorded:
    it never stops a run being published.
    """
    return dict(catalogue().problems)


@functools.cache
def catalogue() -> Catalogue:
    """The merged catalogue of the enabled summaries whose catalogues can be read, in order.

    A catalogue is refused whole when it cannot be read, and when it defines a figure an
    earlier catalogue already defines: two summaries would otherwise count under one method. The
    refusal is in :attr:`Catalogue.problems`, and the figures of the other catalogues stand.
    """
    from ingence.quality.registry import catalogue_paths

    sections: dict[str, str] = {}
    figures: dict[str, FigureDefinition] = {}
    versions: dict[str, str] = {}
    problems: dict[str, str] = {}
    # The core's sections come first, then each other summary's in the registry's order.
    for name, path in sorted(catalogue_paths().items(), key=lambda item: item[0] != "core"):
        try:
            loaded = load_catalogue(path)
        except UNREADABLE as error:
            problems[name] = f"its catalogue could not be read: {type(error).__name__}"
            continue
        twice = sorted(set(loaded.figures) & set(figures))
        if twice:
            problems[name] = f"its catalogue defines {', '.join(twice)}, which another defines"
            continue
        sections.update(loaded.sections)
        versions.update(loaded.versions)
        figures.update(loaded.figures)
    return Catalogue(sections=sections, figures=figures, versions=versions, problems=problems)
