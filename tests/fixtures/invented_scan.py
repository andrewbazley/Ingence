"""An invented content scan, as text a test writes to a file and runs.

The script matches as the exporter's scan does: a substring match ignoring case, the denylist line
number only in its report, exit code 1 on a match. It reads ``denylist.txt`` beside itself.
"""

from __future__ import annotations

SCAN_SCRIPT = """\
import sys
from pathlib import Path

entries = []
lines = Path(__file__).with_name("denylist.txt").read_text(encoding="utf-8").splitlines()
for number, line in enumerate(lines, start=1):
    if line.strip() and not line.strip().startswith("#"):
        entries.append((number, line.strip().casefold()))
matches = 0
for source in sys.argv[1:]:
    folded = Path(source).read_text(encoding="utf-8", errors="replace").casefold()
    for number, entry in entries:
        if entry in folded:
            print(f"{source}: denylist line {number} matched")
            matches += 1
print(f"scanned {len(sys.argv) - 1} input(s) against {len(entries)} entries: {matches} match(es)")
raise SystemExit(1 if matches else 0)
"""
