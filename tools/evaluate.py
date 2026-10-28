#!/usr/bin/env python3
"""Score this checkout's engine over a generated pool, with the rules every project reads.

The development build's form of ``python -m ingence.evaluation run``. The rules every project reads
are the shipped ones, so one column holds them all. A larger size than the suite's is a person's
run:

    python tools/evaluate.py ../runs/evaluation-4 --seed 0 --size 4

writes the pool, its key, the projects, ``report.json`` and ``summary.txt`` under the folder, which
belongs outside every git repository like any development run.

``--write-pinned`` re-pins the suite's tripwire (``tests/fixtures/evaluation_pinned.json``) from
the smallest pool, seed 0 and size 1. A person runs it, after reading what moved and deciding that
the change is the one they meant. It is never run to make a failing test pass, because a corpus is
a tripwire and not a target.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# The checkout this script lives in is the one it scores, whichever checkout the environment's
# editable install points at. This matters in a second worktree.
_CHECKOUT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_CHECKOUT / "src"))
sys.path.insert(0, str(_CHECKOUT / "tests"))

from ingence.evaluation.generate import generate  # noqa: E402
from ingence.evaluation.score import dumps, evaluate, pinned_figures, summary  # noqa: E402

PINNED = _CHECKOUT / "tests" / "fixtures" / "evaluation_pinned.json"
#: The pool the suite pins: the smallest, from the first seed.
PINNED_SEED = 0
PINNED_SIZE = 1


def run(
    destination: Path, *, seed: int, size: int, isolate: bool, approve_cores: bool = False
) -> dict[str, object]:
    written = generate(destination, seed=seed, size=size)
    report = evaluate(
        written.key,
        written.pool,
        destination / "work",
        {"shipped": ()},
        isolate=isolate,
        approve_cores_in=("shipped",) if approve_cores else (),
    )
    (destination / "report.json").write_text(dumps(report), encoding="utf-8")
    (destination / "summary.txt").write_text(summary(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--size", type=int, default=1)
    parser.add_argument("--isolate", action="store_true", help="read files in child processes")
    parser.add_argument(
        "--approve-cores",
        action="store_true",
        help="also run the rules with the key's cores approved as answer_key (never when pinning)",
    )
    parser.add_argument("--write-pinned", action="store_true", help="re-pin the suite's figures")
    arguments = parser.parse_args()
    if arguments.write_pinned and arguments.approve_cores:
        parser.error("the pinned figures never approve anything; drop --approve-cores")
    if arguments.write_pinned:
        with tempfile.TemporaryDirectory() as scratch:
            report = run(
                Path(scratch) / "pinned", seed=PINNED_SEED, size=PINNED_SIZE, isolate=False
            )
        PINNED.write_text(
            json.dumps(pinned_figures(report), indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        sys.stdout.write(summary(report))
        sys.stdout.write(f"\npinned {PINNED}\n")
        return 0
    if arguments.destination is None:
        parser.error("name a destination folder, outside every git repository")
    report = run(
        arguments.destination.expanduser(),
        seed=arguments.seed,
        size=arguments.size,
        isolate=arguments.isolate,
        approve_cores=arguments.approve_cores,
    )
    sys.stdout.write(summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
