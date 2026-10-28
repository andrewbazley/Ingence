"""A summary of how far a run's themes can be trusted, from its projection.

The function that another part of the engine calls is :func:`theme_quality`. It reads a themes
projection (:func:`ingence.themes.projection.themes_projection`) and returns plain numbers, and
never words about a person's data. The numbers say how alike the seeds' final placements are
(every pair, as a distribution), which seed is shown and which is the medoid, whether every theme
is connected in its graph, how many experiments are placed against not placed and why, how steady
the labels are, and the cutoffs that chance gave. Counts come with their denominators. A figure
that was not measured is ``None``, and never 0.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ingence.core.schema import QualityFigure
    from ingence.quality.context import QualityContext

__all__ = ["POINTS", "REASON_CODES", "figures_of_quality", "summarise", "theme_quality"]

PACKAGE = "themes"

#: The whole a similarity or an agreement is stated out of: points, thousandths of 1.
POINTS = 1000


def _spread(values: list[float]) -> dict[str, Any]:
    """Count, minimum, quartiles, maximum and mean of ``values``; all None where there are none."""
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "lower_quartile": None,
            "median": None,
            "upper_quartile": None,
            "maximum": None,
            "mean": None,
        }
    low, middle, high = (float(x) for x in np.quantile(values, [0.25, 0.5, 0.75]))
    return {
        "count": len(values),
        "minimum": round(min(values), 6),
        "lower_quartile": round(low, 6),
        "median": round(middle, 6),
        "upper_quartile": round(high, 6),
        "maximum": round(max(values), 6),
        "mean": round(float(np.mean(values)), 6),
    }


def theme_quality(built: dict[str, Any]) -> dict[str, Any]:
    """The quality figures of one themes projection (a mapping as the projection builds it)."""
    summary = built["summary"]
    experiments = built["experiments"]
    reasons = Counter(row["unplaced_reason"] for row in experiments if row["theme"] is None)
    total = len(experiments)
    placed = sum(1 for row in experiments if row["theme"] is not None)
    chance = summary.get("chance")
    pairs = summary.get("seed_pairs", [])
    support = [
        t["label_stability"] for t in built["themes"] if t.get("label_stability") is not None
    ]
    return {
        "projection_version": built.get("projection_version"),
        "seed_agreement": {
            "measure": "adjusted Rand index of the final placements of every pair of seeds",
            "pairs": _spread([float(p["ari"]) for p in pairs]),
            # Pairs whose final placements agree completely (an adjusted Rand index of 1).
            "identical_pairs": sum(1 for p in pairs if round(float(p["ari"]), 6) == 1.0),
            "mean_per_seed": {str(s["seed"]): s["mean_ari"] for s in summary.get("seeds", [])},
            "sweep_ami": summary.get("seed_ami"),
        },
        "seeds": len(summary.get("seeds", [])),
        "medoid_seed": summary.get("medoid_seed"),
        "shown_seed": summary.get("placement_seed"),
        "final_placement": summary.get("final_placement"),
        "connectivity": {
            "themes": summary.get("themes"),
            "disconnected_themes": summary.get("disconnected_themes"),
            "broad_themes": summary.get("broad_themes"),
            "disconnected_broad_themes": summary.get("disconnected_broad_themes"),
        },
        "placement": {
            "experiments": total,
            "placed": placed,
            "unplaced": total - placed,
            "placed_share": round(placed / total, 6) if total else None,
            "unplaced_reasons": dict(sorted((str(k), v) for k, v in reasons.items())),
        },
        "theme_stability": _spread([float(t["stability"]) for t in built["themes"]]),
        "label_stability": _spread([float(x) for x in support]),
        "chance": chance,
        "chance_null": summary.get("chance_null"),
    }


# ---------------------------------------------------------------------------
# The figures for the quality report.
# ---------------------------------------------------------------------------


#: The breakdown value of each reason the themes give for an experiment placed in none, in the
#: catalogue's words. A reason not named here is stated as the words themselves, never dropped.
#: A test holds this equal to the reasons the core defines.
REASON_CODES: dict[str, str] = {
    "no theme words": "no_words",
    "its group is smaller than a theme": "too_small",
    "its words are no closer to any other experiment's than chance": "not_close",
    "its words are no closer to the rest of its group than chance": "not_close_to_group",
    "its experiments read the same words, which is no theme on its own": "repeated_text",
}


def _points(value: float) -> int | None:
    """A number from 0 to 1 as points of 1,000, or ``None`` where it is below 0."""
    return None if value < 0 else round(value * POINTS)


def figures_of_quality(context: QualityContext, quality: dict[str, Any]) -> Iterator[QualityFigure]:
    """The figures a themes quality summary (:func:`theme_quality`) gives, each with its whole."""
    placement = quality["placement"]
    total = placement["experiments"]
    yield context.counted("themes.placed", count=placement["placed"], total=total)
    reasons: dict[str, int] = placement["unplaced_reasons"]
    from ingence.quality.catalogue import catalogue

    coded: dict[str, int] = {}
    for reason, number in reasons.items():
        key = REASON_CODES.get(reason, reason)
        coded[key] = coded.get(key, 0) + number
    listed = list(catalogue().figures["themes.unplaced"].facets)
    for facet in [*listed, *sorted(set(coded) - set(listed))]:
        yield context.counted(
            "themes.unplaced", facet=facet, count=coded.get(facet, 0), total=total
        )

    seeds = quality["seeds"]
    pairs = quality["seed_agreement"]["pairs"]
    if pairs["count"] == 0:
        for figure in (
            "themes.seed_pairs_identical",
            "themes.seed_agreement_least",
            "themes.seed_agreement_median",
        ):
            yield context.not_recorded(figure, "fewer than two seeds ran, so no pair was compared")
    else:
        yield context.counted(
            "themes.seed_pairs_identical",
            count=quality["seed_agreement"]["identical_pairs"],
            total=pairs["count"],
        )
        for figure, value in (
            ("themes.seed_agreement_least", pairs["minimum"]),
            ("themes.seed_agreement_median", pairs["median"]),
        ):
            scaled = _points(float(value))
            if scaled is None:
                yield context.not_recorded(
                    figure, "the agreement is below 0, which this figure cannot hold"
                )
            else:
                yield context.counted(figure, count=scaled, total=POINTS)
    if seeds:
        # A seed's number is a name, not a count: stored as the value with no whole, so a page
        # draws no bar for it.
        yield context.counted("themes.shown_seed", count=int(quality["shown_seed"]))
    else:
        yield context.not_recorded("themes.shown_seed", "no seed ran")
    if quality["medoid_seed"] is None:
        yield context.not_recorded(
            "themes.medoid_seed", "fewer than two seeds ran, so none agrees most with the others"
        )
    else:
        yield context.counted("themes.medoid_seed", count=int(quality["medoid_seed"]))
    connected = quality["connectivity"]
    yield context.counted(
        "themes.disconnected",
        count=connected["disconnected_themes"],
        total=connected["themes"],
    )
    yield context.counted(
        "themes.broad_disconnected",
        count=connected["disconnected_broad_themes"],
        total=connected["broad_themes"],
    )
    chance = quality["chance"]
    for facet in ("between", "within", "placement"):
        yield _cutoff(context, chance, facet)


def _cutoff(context: QualityContext, chance: dict[str, Any] | None, facet: str) -> QualityFigure:
    """One cutoff chance gave, in points, or not recorded with the one reason it is missing."""
    if chance is None or not chance.get("measured"):
        return context.not_recorded(
            "themes.cutoffs", "no shuffled documents were measured", facet=facet
        )
    if chance.get(f"quantile_{facet}") is None:
        return context.not_recorded(
            "themes.cutoffs", "the settings set no cutoff of this kind", facet=facet
        )
    cut = chance.get(f"cutoff_{facet}")
    if cut is None:
        return context.not_recorded(
            "themes.cutoffs", "no shuffled documents were measured", facet=facet
        )
    scaled = _points(float(cut))
    if scaled is None:
        return context.not_recorded(
            "themes.cutoffs", "the cutoff is below 0, which this figure cannot hold", facet=facet
        )
    return context.counted("themes.cutoffs", facet=facet, count=scaled, total=POINTS)


def summarise(context: QualityContext) -> Iterator[QualityFigure]:
    """The themes figures of a run an inference has written; none for a pass that has not.

    The function builds them when the run is written, from the run's own tables. A missing extra
    (the themes need scikit-learn and scipy) or a switched-off component gives every figure as
    not recorded with its own reason, and the run publishes.
    """
    from ingence.themes import DEFAULT_ENGINE, THEME_ENGINES

    if not context.reaches("themes.placed"):
        return
    # The summary is registered whenever the package is there, so that a base install without the
    # themes extra says why the figures are missing, instead of dropping them without a word.
    if not THEME_ENGINES.declaration(DEFAULT_ENGINE).enabled:
        yield from context.withheld(PACKAGE, "the themes component is switched off")
        return
    if not THEME_ENGINES.available(DEFAULT_ENGINE):
        yield from context.withheld(
            PACKAGE, "the themes extra is not installed, or the themes component could not be built"
        )
        return
    from ingence.themes.projection import projection_of_catalog

    # Kept for the readouts step (0.4.0): the themes are built once, not twice.
    built = projection_of_catalog(context.catalog, context.run_id, keep=True)
    yield from figures_of_quality(context, theme_quality(built))
