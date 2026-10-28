"""``ingence --version`` answers without importing the engine.

A question the package can answer from its own two version strings is answered before the command
line is imported. So the first ``--version`` after an install prints at once instead of waiting
while Python compiles the whole engine to import it.

The test checks what a fresh interpreter has imported once the answer is printed, which does not
depend on how fast the machine is or whether bytecode was already written. It calls the console
script's own entry point, as the installer's launcher calls it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ingence

#: What the full command line imports and a version answer must not: the libraries it is built
#: on and the engine's own modules beyond the package's two version strings.
HEAVY = (
    "typer",
    "click",
    "rich",
    "pydantic",
    "duckdb",
    "pyarrow",
    "ingence.cli.main",
    "ingence.core",
    "ingence.api",
    "ingence.scan",
    "ingence.infer",
)

_PROBE = """
import json, sys
sys.argv = ["ingence", *sys.argv[1:]]
from ingence.cli.launch import main
try:
    main()
except SystemExit as stopped:
    code = stopped.code or 0
else:
    code = 0
heavy = json.loads(sys.stdin.read())
print(json.dumps({
    "code": code,
    "imported": sorted(name for name in sys.modules if name.split(".")[0] in heavy
                       or any(name == h or name.startswith(h + ".") for h in heavy)),
}))
"""


def _launched(*arguments: str) -> tuple[str, dict[str, object]]:
    source = Path(ingence.__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(
        [sys.executable, "-c", _PROBE, *arguments],
        input=json.dumps(list(HEAVY)),
        capture_output=True,
        text=True,
        env=environment,
        check=True,
        timeout=120,
    )
    *printed, last = done.stdout.strip().splitlines()
    return "\n".join(printed), json.loads(last)


def test_version_is_answered_without_importing_the_engine() -> None:
    printed, seen = _launched("--version")
    # A checkout has no build stamp; a built package's line adds ", build <commit>".
    assert printed.startswith(f"ingence {ingence.__version__} (schema {ingence.SCHEMA_VERSION}")
    assert printed.endswith(")")
    assert seen["code"] == 0
    assert seen["imported"] == [], seen["imported"]


def test_any_other_command_still_reaches_the_command_line() -> None:
    """The positive control: the probe sees the engine's imports where they happen."""
    printed, seen = _launched("--help")
    assert "Usage" in printed
    assert "ingence.cli.main" in seen["imported"]


def test_a_built_package_names_its_commit_from_its_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stamp a build writes beside ``__init__`` is what the quick answer names."""
    from ingence.cli import launch

    (tmp_path / "_build_commit.txt").write_text("0123456789abcdef0123\n", encoding="utf-8")
    monkeypatch.setattr(ingence, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(sys, "argv", ["ingence", "--version"])
    launch.main()
    printed = capsys.readouterr().out.strip()
    assert printed == (
        f"ingence {ingence.__version__} (schema {ingence.SCHEMA_VERSION}, build 0123456789ab)"
    )
