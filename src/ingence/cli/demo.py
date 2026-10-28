"""``ingence demo``: make a sample laboratory, read it and open it, in one step.

The sample collection (tier S, seed 1) is written into the folder given, with the project beside
its ``pool/`` folder. The engine then indexes, dives into and infers over the pool, and opens the
Dashboard on the result. The project loads the packs the collection's words need, so its first run
reads them. The command writes only the folder it makes and, when the Dashboard opens, the list of
recent projects in the settings folder. The pool is only read.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

from ingence.evaluation.sample_lab import describe, generate_sample_lab
from ingence.evaluation.sample_lab_words import words

__all__ = ["DEFAULT_FOLDER", "run_demo"]

DEFAULT_FOLDER = Path("ingence-demo")

#: Seconds of content reading to grant the dive. The smallest tier is read in well under this.
DIVE_SECONDS = 600


def _stop_words(seconds: int) -> str:
    """A time limit as a person says it: whole minutes where there are, else seconds."""
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    return f"{seconds} second{'' if seconds == 1 else 's'}"


def run_demo(destination: Path, *, open_browser: bool = True) -> int:
    """Write the sample collection to ``destination``, read it, and open the Dashboard.

    Returns a command's exit status: 0 when the Dashboard closed, 2 when ``destination`` already
    holds something (nothing is written then), or the status of the first step that failed.
    """
    from rich.console import Console

    from ingence.cli.main import app

    console = Console()
    try:
        lab = generate_sample_lab(destination, tier="S", seed=1)
    except FileExistsError as refused:
        console.print(f"[red]{refused}[/red]")
        return 2
    console.print(f"Step 1 of 5: Ingence made the sample collection, {describe(lab.manifest)}.")
    project = destination / "project"
    packs = [name for pack in words().pack_names() for name in ("--domain", pack)]
    steps = (
        (
            "Step 2 of 5: Making the project.",
            ("init", str(project), "--root", str(lab.pool), "--label", "sample", *packs),
        ),
        (
            "Step 3 of 5: Reading names.",
            ("index", "--project", str(project), "--quiet"),
        ),
        (
            f"Step 4 of 5: Reading contents. It stops after {_stop_words(DIVE_SECONDS)}.",
            ("dive", "--project", str(project), "--budget", str(DIVE_SECONDS)),
        ),
        ("Step 5 of 5: Applying rules.", ("infer", "--project", str(project))),
    )
    for sentence, step in steps:
        console.print(sentence)
        # The command's own report (and its hint for what to run next) is for someone running the
        # commands one by one, not for this one.
        report = io.StringIO()
        with contextlib.redirect_stdout(report):
            code = app(args=list(step), standalone_mode=False)
        if code:
            console.print(report.getvalue())
            console.print(f"[red]ingence {step[0]} did not finish (status {code}).[/red]")
            return int(code)
    console.print("Opening the Dashboard. Press Ctrl-C to stop it.")
    return int(
        app(
            args=["see", "--project", str(project), "--open" if open_browser else "--no-open"],
            standalone_mode=False,
        )
        or 0
    )
