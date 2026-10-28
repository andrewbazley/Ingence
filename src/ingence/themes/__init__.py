"""Themes: what a run's experiments are about, drawn from their words.

Themes are a projection and never a claim. They are recomputed from a published run, the
Dictionary and the settings, and never become a Record or Index row, and no question is ever
raised about one. Themes are an optional component: the engine reaches them only through
:data:`THEME_ENGINES`. The dependencies (scikit-learn and scipy) are base dependencies, so a
normal install can run the component. Where a damaged environment or a disabled component cannot,
:func:`unavailable_in_words` says why in plain words. To remove the component, delete this
package, the line for its routes module in ``api/v1/__init__.py``, and its export kind.

The functions that another part of the engine calls are :func:`theme_assignments` and
:func:`experiment_themes`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ingence.core.registry import Registry
from ingence.theme_words import unavailable_in_words

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.core.settings import ThemeSettings
    from ingence.themes.core import ThemeResult

__all__ = [
    "THEME_ENGINES",
    "ThemeEngine",
    "experiment_places_or_reason",
    "experiment_themes",
    "experiment_themes_or_reason",
    "theme_assignments",
    "themes_engine",
    "unavailable_in_words",
]

#: The engine registered by default.
DEFAULT_ENGINE = "lsa_louvain"


class ThemeEngine(Protocol):
    """Draws themes from documents: the pure core, and the run's projection built on it."""

    version: str

    def compute(
        self,
        documents: Mapping[str, Sequence[str] | Mapping[str, float]],
        *,
        collections: Mapping[str, str] | None = None,
        senses: Mapping[str, str] | None = None,
        phrases: Mapping[tuple[str, ...], str] | None = None,
        settings: ThemeSettings | None = None,
    ) -> ThemeResult:
        """The pure core (:func:`ingence.themes.core.compute_themes`)."""
        ...

    def projection(
        self, project: Path, run_id: str, settings: ThemeSettings | None = None
    ) -> dict[str, Any]:
        """The run's themes projection, built once per run and settings (cached)."""
        ...

    def map_projection(
        self, project: Path, run_id: str, settings: ThemeSettings | None = None
    ) -> dict[str, Any]:
        """Where each of the run's experiments lies in the plane (cached beside the projection)."""
        ...

    def held_map_projection(
        self, project: Path, run_id: str, settings: ThemeSettings | None = None
    ) -> dict[str, Any] | None:
        """The run's map where it is already held (memory or its cached file); ``None`` where the
        next :meth:`map_projection` would draw it. Never draws it."""
        ...

    def write_tables(self, built: dict[str, Any], directory: Path) -> tuple[Path, Path]:
        """Write a projection's experiment and theme tables as CSV into ``directory``."""
        ...

    def check_destination(
        self, directory: Path, *, project: Path, roots: Sequence[Path], overwrite: bool = False
    ) -> None:
        """Refuse a destination an export must never write, or one whose files are already there
        and ``overwrite`` was not given (raises ``ValueError``)."""
        ...


#: Every theme engine, reachable only through here.
THEME_ENGINES: Registry[ThemeEngine] = Registry("theme_engine")


class _LsaLouvain:
    """TF-IDF, LSA, a union nearest-neighbour graph and seeded Louvain communities."""

    version = "1"

    def compute(
        self,
        documents: Mapping[str, Sequence[str] | Mapping[str, float]],
        *,
        collections: Mapping[str, str] | None = None,
        senses: Mapping[str, str] | None = None,
        phrases: Mapping[tuple[str, ...], str] | None = None,
        settings: ThemeSettings | None = None,
    ) -> ThemeResult:
        from ingence.themes.core import compute_themes

        return compute_themes(
            documents, collections=collections, senses=senses, phrases=phrases, settings=settings
        )

    def projection(
        self, project: Path, run_id: str, settings: ThemeSettings | None = None
    ) -> dict[str, Any]:
        from ingence.themes.projection import themes_projection

        return themes_projection(project, run_id, settings=settings)

    def map_projection(
        self, project: Path, run_id: str, settings: ThemeSettings | None = None
    ) -> dict[str, Any]:
        from ingence.themes.projection import themes_map

        return themes_map(project, run_id, settings=settings)

    def held_map_projection(
        self, project: Path, run_id: str, settings: ThemeSettings | None = None
    ) -> dict[str, Any] | None:
        from ingence.themes.projection import held_themes_map

        return held_themes_map(project, run_id, settings=settings)

    def write_tables(self, built: dict[str, Any], directory: Path) -> tuple[Path, Path]:
        from ingence.themes.export import write_tables

        return write_tables(built, directory)

    def check_destination(
        self, directory: Path, *, project: Path, roots: Sequence[Path], overwrite: bool = False
    ) -> None:
        from ingence.themes.export import check_destination

        check_destination(directory, project=project, roots=roots, overwrite=overwrite)


@THEME_ENGINES.register(
    DEFAULT_ENGINE,
    version="1",
    summary="Themes by TF-IDF, LSA, a nearest-neighbour graph and seeded Louvain communities",
    requires=("sklearn", "scipy"),
)
def _build_lsa_louvain() -> ThemeEngine:
    return _LsaLouvain()


def themes_engine(name: str = DEFAULT_ENGINE) -> ThemeEngine:
    """The theme engine of that name; raises ``ComponentNotAvailableError`` saying why not."""
    return THEME_ENGINES.get(name)


def theme_assignments(
    project: Path, run_id: str, settings: ThemeSettings | None = None
) -> dict[str, dict[str, Any]]:
    """Each experiment's theme in a published run: ``{experiment_id: assignment}``.

    ``experiment_id`` is the experiment folder's entry identifier. Each assignment is a mapping
    with ``theme_id`` (int, or None where the experiment is not placed), ``broad_id`` (int or
    None), ``label`` (the theme's label words joined by `` · ``, or None), ``broad_label``,
    ``folder`` (its path below its root) and ``unplaced_reason`` (words, or None). Built from the
    run's cached themes projection; ``settings`` defaults to the project's own.
    """
    built = themes_engine().projection(project, run_id, settings)
    return {
        row["id"]: {
            "theme_id": row["theme"],
            "broad_id": row["broad_theme"],
            "label": row["theme_label"],
            "broad_label": row["broad_label"],
            "folder": row["folder"],
            "unplaced_reason": row["unplaced_reason"],
        }
        for row in built["experiments"]
    }


def experiment_themes(
    project: Path, run_id: str, settings: ThemeSettings | None = None
) -> dict[str, str | None]:
    """Each experiment's theme label in a published run: ``{experiment_id: label}``, the
    label being the theme's words joined by `` · ``, or None where the experiment is in no theme
    (what a run's manifest names beside each experiment).

    Raises ``ComponentNotAvailableError`` where the themes component cannot run (a package it
    needs is missing or it is disabled), ``FileNotFoundError`` where the run is not a published
    run of the project, and ``NotAProjectError`` where the folder is no project; see
    :func:`experiment_themes_or_reason` for a caller that must not raise."""
    return {
        unit: row["label"] for unit, row in theme_assignments(project, run_id, settings).items()
    }


def experiment_themes_or_reason(
    project: Path, run_id: str, settings: ThemeSettings | None = None
) -> tuple[dict[str, str | None] | None, str | None]:
    """:func:`experiment_themes`, or why they were not computed, never raising for what a run's
    manifest meets: ``(labels, None)``, or ``(None, reason)`` in words where a package themes need
    is missing or the component disabled, the run is not published, the folder is no project, or
    anything else failed (its type and message: a damaged table, say). Themes that are not
    computed are said to be, and are never left absent."""
    from ingence.core.project import NotAProjectError
    from ingence.core.registry import ComponentNotAvailableError

    try:
        return experiment_themes(project, run_id, settings), None
    except ComponentNotAvailableError as error:
        return None, unavailable_in_words(error)
    except FileNotFoundError as error:
        return None, f"run {run_id} is not a published run of this project: {error}"
    except NotAProjectError as error:
        return None, f"not a project: {error}"
    except Exception as error:  # an optional projection: a manifest never fails on it
        return None, f"themes could not be computed: {type(error).__name__}: {error}"


def experiment_places_or_reason(
    project: Path, run_id: str, settings: ThemeSettings | None = None
) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    """Where each experiment stands on the Years ladder, or why that was not computed.

    The result is ``({experiment_id: place}, None)``. Each place holds ``theme_id`` (an int, or
    None where the experiment is in no theme), ``label`` (the theme's words joined by `` · ``, or
    None), ``person`` (the person the first folder below its root names, or None where the person
    level is not set) and ``unplaced_reason`` (words, or None where it is placed). Where the
    component cannot run, the result is ``(None, reason)`` with the reason in words, as
    :func:`experiment_themes_or_reason` says it, and the function never raises. Themes that are
    not computed are said to be, and are never left absent.
    """
    from ingence.core.project import NotAProjectError
    from ingence.core.registry import ComponentNotAvailableError

    try:
        built = themes_engine().projection(project, run_id, settings)
    except ComponentNotAvailableError as error:
        return None, unavailable_in_words(error)
    except FileNotFoundError as error:
        return None, f"run {run_id} is not a published run of this project: {error}"
    except NotAProjectError as error:
        return None, f"not a project: {error}"
    except Exception as error:  # an optional projection: the Graph never fails on it
        return None, f"themes could not be computed: {type(error).__name__}: {error}"
    return {
        str(row["id"]): {
            "theme_id": row["theme"],
            "label": row["theme_label"],
            "person": row.get("person"),
            "unplaced_reason": row["unplaced_reason"],
        }
        for row in built["experiments"]
    }, None
