"""The questions as one file: how to answer, the questions and their evidence, the answers.

The Questions page exports its questions as one self-describing file that anyone can fill in, and
Import brings it back. The first field of the file says in plain words how to answer. The
questions follow, each with the evidence the page shows a person: sample names, where they sit,
and the engine's own reading. The last field holds the answers, already keyed by every question's
target. Whoever fills in the file fills blanks and does not invent a format.

Import records each answer as its author's once the person says who wrote the file
(``record/imported.py``, ``api/routes/imports.py``). The file itself names nobody.

The file holds file and folder names from the project and nothing else: no file contents and no
paths above the project's roots. Nothing in a run reads the file, and the engine sends it
nowhere. The person carries it both ways.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["INSTRUCTIONS", "PACK_FORMAT", "question_pack"]

#: Named in the file so a later Dashboard knows what it is reading.
PACK_FORMAT = "ingence.questions/1"

#: How to answer, first in the file, for whoever fills it in.
INSTRUCTIONS = """\
Questions Ingence could not answer from this project's file and folder names, each with its
evidence: example names, their folders, and what Ingence reads there now.

To answer one, fill its entry under "answers": "word", one of the question's "words" or
"cannot_say", and "basis", what in the evidence led you there. Leave an entry blank to skip it.
Importing the file records each answer as its author's.
"""


def question_pack(
    questions: Sequence[Mapping[str, Any]],
    *,
    project_name: str,
    run_label: str,
    run_id: str,
    engine_readings: Mapping[str, Mapping[str, Any]] | None = None,
    limit_names: int = 15,
) -> dict[str, Any]:
    """The export: instructions, questions with their evidence, and the answer shape to fill.

    ``questions`` are the Questions page's rows as its route returns them (each with ``target``,
    ``question``, ``words`` offered, and its sample ``names``); ``engine_readings`` adds the
    engine's own reading per target where one exists.
    """
    readings = engine_readings or {}
    rows: list[dict[str, Any]] = []
    for question in questions:
        target = str(question["target"])
        rows.append(
            {
                "target": target,
                "question": (
                    question.get("question")
                    or question.get("heading")
                    or question.get("sentence")
                    or target
                ),
                "words": list(question.get("words") or []),
                "evidence": {
                    "names": list(question.get("names") or [])[:limit_names],
                    "folders": list(question.get("folders") or [])[:limit_names],
                    "engine_reading": dict(readings.get(target) or {}),
                },
            }
        )
    rows.sort(key=lambda row: row["target"])
    return {
        "format": PACK_FORMAT,
        "instructions": INSTRUCTIONS,
        "project": project_name,
        "run": {"label": run_label, "run_id": run_id},
        "questions": rows,
        "answers": {row["target"]: {"word": "", "basis": ""} for row in rows},
    }
