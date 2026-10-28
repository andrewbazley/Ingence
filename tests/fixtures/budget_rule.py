"""The rule that turns measurements into time budgets (``test_browser_budgets.py``).

The rule: a tier is measured five times in a row, at one load. Each figure's budget is the slowest
of its five, doubled, rounded up to the next half second. This module writes the rule down once, so
that a person who measures again gets the same answer from the same lines. The rule refuses a
figure taken when the one-minute load average was above ``MAX_LOAD``, because other work on the
laptop moves a page's time by more than the margin.

Read the lines the budgets test appends with ``INGENCE_BUDGET_RECORD``::

    PYTHONPATH=tests python -m fixtures.budget_rule record.jsonl

On a machine that cannot be made quiet, ``--max-load N`` takes figures up to load ``N``; write the
load and what held the machine beside the budgets.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

#: The rule's numbers: ``RUNS`` measurements per tier, a margin of ``MARGIN``, rounded up to
#: ``STEP`` seconds, taken at a one-minute load of at most ``MAX_LOAD``.
RUNS = 5
MARGIN = 2.0
STEP = 0.5
MAX_LOAD = 4.0


def budget_from(slowest: float) -> float:
    """The slowest of the five measurements, doubled, rounded up to the next half second."""
    return math.ceil(round(slowest * MARGIN / STEP, 9)) * STEP


def derive(lines: list[dict[str, Any]], *, max_load: float = MAX_LOAD) -> dict[str, dict[str, Any]]:
    """Budgets by tier from the recorded lines.

    Each tier needs ``RUNS`` lines. With more, the last ``RUNS`` are used, because a new
    measurement is appended after an earlier try. The function returns, per tier, ``budgets`` (a
    figure's name to seconds), the ``slowest`` of the five that each came from, the ``loads``
    (lowest, highest) and the ``dates`` (first, last). Raises ``ValueError`` where a tier has
    too few lines or a figure was taken above ``max_load``.
    """
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        by_tier.setdefault(str(line["tier"]), []).append(line)
    derived: dict[str, dict[str, Any]] = {}
    for tier, held in sorted(by_tier.items()):
        if len(held) < RUNS:
            message = f"tier {tier}: {len(held)} measurements, the rule needs {RUNS}"
            raise ValueError(message)
        five = held[-RUNS:]
        loads = [figure["load"] for line in five for figure in line["figures"].values()]
        if max(loads) > max_load:
            message = (
                f"tier {tier}: a figure was taken at load {max(loads):.1f}, above {max_load:g}; "
                "measure again when the machine is quiet"
            )
            raise ValueError(message)
        names = sorted(five[0]["figures"])
        slowest = {name: max(line["figures"][name]["seconds"] for line in five) for name in names}
        derived[tier] = {
            "budgets": {name: budget_from(slowest[name]) for name in names},
            "slowest": slowest,
            "loads": (min(loads), max(loads)),
            "dates": (five[0]["at"], five[-1]["at"]),
        }
    return derived


def main(argv: list[str]) -> int:
    """Print the budgets derived from a record file.

    The arguments are ``RECORD.jsonl [--max-load N]``.

    ``--max-load`` raises the limit for a machine that cannot be made quiet, as when another job
    holds some of its cores. Say so beside the budgets that result.
    """
    arguments = argv[1:]
    max_load = MAX_LOAD
    if "--max-load" in arguments:
        at = arguments.index("--max-load")
        try:
            max_load = float(arguments[at + 1])
        except (IndexError, ValueError):
            print("usage: python -m fixtures.budget_rule RECORD.jsonl [--max-load N]")
            return 2
        del arguments[at : at + 2]
    if len(arguments) != 1:
        print("usage: python -m fixtures.budget_rule RECORD.jsonl [--max-load N]")
        return 2
    lines = [
        json.loads(each)
        for each in Path(arguments[0]).read_text(encoding="utf-8").splitlines()
        if each.strip()
    ]
    try:
        derived = derive(lines, max_load=max_load)
    except ValueError as refused:
        print(f"refused: {refused}")
        return 1
    for tier, held in derived.items():
        low, high = held["loads"]
        first, last = held["dates"]
        print(f"tier {tier}: load {low:.1f} to {high:.1f}, {first} to {last}")
        for name, budget in held["budgets"].items():
            print(f"  {name}: slowest {held['slowest'][name]:.3f} s -> budget {budget:g} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
