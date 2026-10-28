"""An invented pool of experiments, one design in each.

Every name is invented and a date stands in every one of them. Used by ``test_design.py`` and the
browser test of the design grid.

- ``20261012_HeLa_drug_time``: drug by time, taxol at 2 hours not found;
- ``20261013_HeLa_nested``: each dose lies under one drug;
- ``20261014_HeLa_dose``: a dose ladder, 1, 10 and 100 uM;
- ``20261015_HeLa_channels``: GFP always with 488 and mCherry with 561, one file off the pairing;
- ``20261016_HeLa_series``: five counted movies of one condition (a series);
- ``20261017_HeLa_tie``: ``1`` and ``001``, a tie;
- ``20261018_HeLa_live``: ``t01`` to ``t05``, each the only file of its condition;
- ``20261019_HeLa_empty``: one condition's table is an empty output, beside one that holds bytes
  in the folder next to it;
- ``20261020_HeLa_clock``: a clock of the day in every name, which varies and is no condition;
- ``20261021_HeLa_wavelengths``, ``20261022_HeLa_unitless``: numbers that are conditions (488, 561,
  640; 1, 10, 100; 0, 30, 60), which the series rule never puts in order;
- ``20261023_HeLa_single``: drug by time where one condition has a single file and no class;
- ``20261027_HeLa_weekday``, ``20261028_HeLa_monthword``: a weekday or a month word, then a
  counter after an underscore, which is the replicate axis and no part of a date;
- ``20261029_HeLa_timepoints``: ``tp1`` to ``tp3``, a time point, which is a condition;
- ``20261024_HeLa_months``, ``20261025_HeLa_labels``, ``20261026_HeLa_samples``: a day beside a
  month (``12Oct``, ``Nov12``) and labels that count repeats (``exp1``, ``s2``), which are no
  conditions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.project import Project

__all__ = ["project_with_the_grid_on", "write_pool"]


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_pool(root: Path) -> Path:
    # A crossed design, drug by time, taxol at 2 hours not found.
    crossed = root / "20261012_HeLa_drug_time"
    for drug, hours in (("DMSO", 0), ("DMSO", 2), ("taxol", 0)):
        for number in (1, 2, 3):
            _write(crossed / f"20261012_HeLa_{drug}_{hours}h_{number:03d}.tif")
    # A nested design: each dose lies under one drug.
    nested = root / "20261013_HeLa_nested"
    for drug, doses in (("taxol", (1, 10)), ("nocodazole", (5, 50))):
        for dose in doses:
            for number in (1, 2):
                _write(nested / f"20261013_HeLa_{drug}_{dose}uM_{number:03d}.tif")
    # A dose ladder.
    dosed = root / "20261014_HeLa_dose"
    for dose in (1, 10, 100):
        for number in (1, 2):
            _write(dosed / f"20261014_HeLa_nocodazole-{dose}uM_{number:03d}.tif")
    # Two parts that always go together (GFP with 488, mCherry with 561), one file off the pairing.
    channels = root / "20261015_HeLa_channels"
    for number in range(1, 8):
        _write(channels / f"20261015_HeLa_DMSO_GFP_488_{number:03d}.tif")
    for number in range(1, 7):
        _write(channels / f"20261015_HeLa_DMSO_mCherry_561_{number:03d}.tif")
    _write(channels / "20261015_HeLa_DMSO_GFP_561_008.tif")
    # A series of replicates: five counted movies of one condition.
    _write_series(root / "20261016_HeLa_series", "20261016_HeLa_DMSO_{:03d}.tif", range(1, 6))
    # A tie: 1 and 001 are the same number.
    tie = root / "20261017_HeLa_tie"
    for name in (
        "20261017_HeLa_DMSO_1.tif",
        "20261017_HeLa_DMSO_001.tif",
        "20261017_HeLa_DMSO_002.tif",
    ):
        _write(tie / name)
    # Files the replicate grouping gives no class: an indexed label of no replicate word.
    live = root / "20261018_HeLa_live"
    for number in range(1, 6):
        _write(live / f"20261018_HeLa_live_t{number:02d}.tif")
    # An empty output: one condition's table is empty, beside a table of its name that holds bytes.
    empty = root / "20261019_HeLa_empty"
    for drug, hours in (("DMSO", 0), ("DMSO", 2), ("taxol", 0), ("taxol", 2)):
        for number in (1, 2):
            full = (drug, hours) != ("taxol", 0)
            _write(
                empty / f"20261019_HeLa_{drug}_{hours}h_{number:03d}.csv",
                "a,b\n1,2\n" if full else "",
            )
    _write(root / "20261019_HeLa_empty_beside" / "20261019_HeLa_taxol_0h_003.csv", "a,b\n1,2\n")
    # A clock in every name (tokenised as three numbers) is no condition either.
    clocked = root / "20261020_HeLa_clock"
    for drug in ("DMSO", "taxol"):
        for number, (hour, minute) in enumerate(((14, 32), (15, 41), (16, 47)), start=1):
            _write(clocked / f"20261020_HeLa_{drug}_{hour}-{minute:02d}-59_{number:03d}.tif")
    # Numbers that are conditions, written without a unit: wavelengths, a dose, minutes.
    wavelengths = root / "20261021_HeLa_wavelengths"
    for wavelength in (488, 561, 640):
        _write(wavelengths / f"20261021_HeLa_GFP_{wavelength}.tif")
    unitless = root / "20261022_HeLa_unitless"
    for drug, values in (("nocodazole", (1, 10, 100)), ("DMSO", (0, 30, 60))):
        for value in values:
            _write(unitless / f"20261022_HeLa_{drug}_{value}.tif")
    # DMSO and taxol at 0 and 2 hours; taxol at 2 hours has a single file, so no condition class.
    single = root / "20261023_HeLa_single"
    for drug, hours, count in (
        ("DMSO", 0, 2),
        ("DMSO", 2, 2),
        ("taxol", 0, 2),
        ("taxol", 2, 1),
    ):
        for number in range(1, count + 1):
            _write(single / f"20261023_HeLa_{drug}_{hours}h_{number:03d}.tif")
    # A day written beside a month, and labels counting repeats, are no conditions.
    months = root / "20261024_HeLa_months"
    for drug in ("DMSO", "taxol"):
        for day in (12, 13):
            for number in (1, 2):
                _write(months / f"20261024_HeLa_{drug}_{day}Oct_{number:03d}.tif")
                _write(months / f"20261024_HeLa_{drug}_Nov{day}_{number:03d}.tif")
    for folder, label in (("20261025_HeLa_labels", "exp"), ("20261026_HeLa_samples", "s")):
        for drug in ("DMSO", "taxol"):
            for repeat in (1, 2, 3):
                for number in (1, 2):
                    name = f"{folder[:8]}_HeLa_{drug}_{label}{repeat}_{number:03d}.tif"
                    _write(root / folder / name)
    # A date word beside a counter across an underscore is two things; a time point is a condition.
    for folder, word in (("20261027_HeLa_weekday", "Monday"), ("20261028_HeLa_monthword", "Oct")):
        for drug in ("DMSO", "taxol"):
            for number in (1, 2):
                _write(root / folder / f"{folder[:8]}_HeLa_{drug}_{word}_{number:03d}.tif")
    for drug in ("DMSO", "taxol"):
        for point in (1, 2, 3):
            for number in (1, 2):
                name = f"20261029_HeLa_{drug}_tp{point}_{number:03d}.tif"
                _write(root / "20261029_HeLa_timepoints" / name)
    return root


def _write_series(folder: Path, pattern: str, numbers: range) -> None:
    for number in numbers:
        _write(folder / pattern.format(number))


def project_with_the_grid_on(path: Path, roots: list[Path]) -> Project:
    """A project whose own settings turn the design grid on (it is off by default)."""
    from ingence.core.project import Project

    made = Project.create(path, created_at="t", roots=roots)
    held = made.settings
    made.save_settings(
        held.model_copy(update={"design": held.design.model_copy(update={"enabled": True})})
    )
    return made
