"""The rule that sets a time budget from five measurements (``fixtures/budget_rule.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fixtures.budget_rule import MAX_LOAD, RUNS, budget_from, derive, main


def _line(tier: str, at: int, seconds: float, load: float = 1.5) -> dict[str, Any]:
    return {
        "tier": tier,
        "at": f"2026-10-14T01:0{at}:00+00:00",
        "figures": {
            "first_paint": {"seconds": seconds, "load": load},
            "view_switch": {"seconds": seconds / 10, "load": load},
        },
    }


@pytest.mark.parametrize(
    ("slowest", "budget"),
    [
        (26.7, 53.5),  # a slow cold first paint: doubled, to the next half second
        (1.64, 3.5),
        (0.35, 1.0),
        (0.057, 0.5),
        (0.25, 0.5),  # doubled is exactly a half second: not pushed to the next
        (1.0, 2.0),
    ],
)
def test_a_budget_is_the_slowest_doubled_and_rounded_up_to_the_half_second(
    slowest: float, budget: float
) -> None:
    assert budget_from(slowest) == budget


def test_the_slowest_of_five_sets_the_budget_and_the_loads_and_dates_are_kept() -> None:
    lines = [_line("M", index, seconds) for index, seconds in enumerate([2.0, 3.1, 2.4, 2.2, 2.9])]
    derived = derive(lines)
    assert derived["M"]["budgets"] == {"first_paint": 6.5, "view_switch": 1.0}
    assert derived["M"]["slowest"]["first_paint"] == 3.1
    assert derived["M"]["loads"] == (1.5, 1.5)
    assert derived["M"]["dates"] == (lines[0]["at"], lines[-1]["at"])


def test_a_tier_with_fewer_than_five_measurements_is_refused() -> None:
    with pytest.raises(ValueError, match=f"needs {RUNS}"):
        derive([_line("L", index, 5.0) for index in range(RUNS - 1)])


def test_a_figure_taken_at_a_busy_load_is_refused() -> None:
    lines = [_line("M", index, 2.0) for index in range(RUNS)]
    lines[2] = _line("M", 2, 2.0, load=MAX_LOAD + 0.1)
    with pytest.raises(ValueError, match="quiet"):
        derive(lines)


def test_an_earlier_try_before_the_five_is_left_out() -> None:
    slow_try = _line("M", 0, 90.0, load=1.0)
    five = [_line("M", index, 2.0) for index in range(1, RUNS + 1)]
    assert derive([slow_try, *five])["M"]["slowest"]["first_paint"] == 2.0


def test_the_command_refuses_a_busy_load_unless_the_limit_is_raised(tmp_path: Path) -> None:
    import json

    lines = [_line("M", index, 2.0, load=5.2) for index in range(RUNS)]
    record = tmp_path / "record.jsonl"
    record.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    assert main(["budget_rule", str(record)]) == 1
    assert main(["budget_rule", str(record), "--max-load", "6"]) == 0
    assert main(["budget_rule", str(record), "--max-load"]) == 2
