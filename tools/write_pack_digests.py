#!/usr/bin/env python3
"""Write each shipped pack's manifest digest again, after its words changed.

A shipped pack's ``pack:`` block carries a digest over its words, and a test fails when the words
change and the digest does not. A change to a pack's words is meant to come with a new version too,
which is a person's decision, so this writes the digest only and says which packs changed. Give each
a new version by hand.

Run with ``--check`` to list the packs whose digest is stale without writing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The checkout this script lives in is the one it reads (as ``export_schemas.py`` does).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from ingence.dictionary.pack_manifest import MANIFEST_KEY, words_digest
from ingence.dictionary.store import PACKS_DIRECTORY

_DIGEST_LINE = re.compile(r"^(  digest: )([0-9a-f]+)$", re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="List stale digests; write nothing.")
    arguments = parser.parse_args()
    stale: list[str] = []
    for path in sorted(PACKS_DIRECTORY.glob("*/*.yaml")):
        text = path.read_text(encoding="utf-8")
        document = yaml.safe_load(text) or {}
        block = document.get(MANIFEST_KEY)
        if not isinstance(block, dict):
            print(f"{path.name}: no manifest", file=sys.stderr)
            stale.append(path.name)
            continue
        held = words_digest([document])
        if block.get("digest") == held:
            continue
        stale.append(path.name)
        if not arguments.check:
            path.write_text(_DIGEST_LINE.sub(rf"\g<1>{held}", text, count=1), encoding="utf-8")
        print(
            f"{path.name}: digest {'stale' if arguments.check else 'written'}; version stays "
            f"{block.get('version')!r} until you change it"
        )
    return 1 if arguments.check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
