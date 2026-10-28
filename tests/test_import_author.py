"""Import says who wrote the file: a person who has answered here, or an agent.

Importing asks what the file is and who wrote it. An agent's file is imported only once the person
confirms they reviewed it, and each answer is then written as ``<their name>_agent``; any other
choice writes the named person as the author, and where that is not the person importing, the event
says who imported it. A refused confirmation writes nothing. Every name here is invented, and the
rules are the shipped ones.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.server import WRITE_HEADER, DashboardServer
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.imported import (
    ImportRefusedError,
    agent_author,
    record_authors,
    stated,
)
from ingence.record.log import append_event, read_events
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"


def _pool_project(base: Path) -> Project:
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_kelt"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"zib_glorbex_{index:02d}.csv").write_text(f"a\n{index}\n")
        (pool / folder / "trace.qzx").write_text("x")
        # Two shipped packs mean ``sim`` differently (a simulation, structured illumination), so
        # its question offers no word of its own: the core wins only against a pack.
        (pool / folder / "sim.qzx").write_text("x")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    # A tie between two domains in fewer files than the floor is not asked; the floor is
    # lowered so ``sim`` is.
    dictionary = project.settings.dictionary
    project = project.save_settings(
        project.settings.model_copy(
            update={
                "dictionary": dictionary.model_copy(
                    update={
                        "domain_skew": dictionary.domain_skew.model_copy(
                            update={"question_above_files": 0}
                        )
                    }
                )
            }
        )
    )
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFERENCE, started_at="t")
    return project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return _pool_project(tmp_path)


@pytest.fixture
def server(project: Project):
    served = DashboardServer(project.path, port=0, warm_search=False)
    served.start()
    yield served
    served.stop()


def _get(server: DashboardServer, path: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + path) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _post(server: DashboardServer, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        server.url.rstrip("/") + "/api/review/answer",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", WRITE_HEADER: "1"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


#: A filled answers file: two extensions the invented pool holds, one question it raises.
ANSWERS = {
    "extension:qzx": {"word": "never", "described": "a tracker's trace", "basis": "one per day"},
    "extension:csv": {"word": "yes", "basis": "tables of values"},
    "question:binding_is_ambiguous:trace": {"word": "", "basis": ""},
}


def _import(server: DashboardServer, **statement: Any) -> tuple[int, dict[str, Any]]:
    body = {
        "actor": statement.pop("actor", "ines"),
        "import": {
            "kind": "answers",
            "name": "answers-from-kelt.json",
            "document": json.dumps({"answers": ANSWERS}),
            **statement,
        },
    }
    return _post(server, body)


def _source(event: Any) -> dict[str, Any]:
    return json.loads(event.payload)["source"]


# ---------------------------------------------------------------------------
# The statement.
# ---------------------------------------------------------------------------


def test_the_person_importing_is_the_author_unless_they_name_another() -> None:
    mine = stated("ines")
    assert (mine.author, mine.source()) == ("ines", {})
    theirs = stated("ines", written_by="  wren ")
    assert (theirs.author, theirs.source()) == ("wren", {"imported_by": "ines"})


def test_an_agent_s_file_is_written_as_the_person_s_agent_once_they_confirm_the_review() -> None:
    agent = stated("ines", agent_generated=True, reviewed=True)
    assert agent.author == agent_author("ines") == "ines_agent"
    assert agent.source() == {"imported_by": "ines", "agent_generated": True, "reviewed_by": "ines"}


@pytest.mark.parametrize(
    ("importer", "arguments", "words"),
    [
        ("ines", {"agent_generated": True}, "confirm you reviewed"),
        ("ines", {"agent_generated": True, "reviewed": True, "written_by": "wren"}, "not both"),
        ("ines", {"written_by": "tomas_agent"}, "import the file as agent-generated"),
        ("  ", {}, "say who is importing"),
        ("ines", {"written_by": "w\u0007ren"}, "does not print"),
    ],
)
def test_a_statement_that_cannot_be_recorded_is_refused(
    importer: str, arguments: dict[str, Any], words: str
) -> None:
    with pytest.raises(ImportRefusedError, match=words):
        stated(importer, **arguments)


def test_the_authors_offered_are_the_record_s_people_the_decider_first(project: Project) -> None:
    for actor in ("wren", "ines", "Wren", "tomas_agent", "anouk"):
        append_event(
            project.path,
            kind=RecordEventKind.NOTE,
            actor=actor,
            occurred_at="2026-09-30T20:00:00Z",
            target="extension:csv",
            against_run_id=INFERENCE,
            payload={"note": "seen"},
            scope=PROJECT_SCOPE,
        )
    assert record_authors(project.path, decider="ines") == ["ines", "anouk", "wren"]
    assert record_authors(project.path, decider="tomas") == ["tomas", "anouk", "ines", "wren"]
    assert record_authors(project.path) == ["anouk", "ines", "wren"]


# ---------------------------------------------------------------------------
# The Dashboard's import, through the Record's one way in.
# ---------------------------------------------------------------------------


def test_an_import_as_your_own_writes_each_answer_as_yours(server, project: Project) -> None:
    status, said = _import(server)
    assert status == 201, said
    events = read_events(project.path)
    assert [event.actor for event in events] == ["ines", "ines"]
    assert {event.target for event in events} == {"extension:qzx", "extension:csv"}
    for event in events:
        source = _source(event)
        assert source["imported_from"] == "answers-from-kelt.json"
        assert "imported_by" not in source
        assert "agent_generated" not in source
    assert said["author"] == "ines"
    assert (len(said["written"]), said["blank"]) == (2, 1)
    # The note is the author's basis, written beside the answer.
    qzx = next(event for event in events if event.target == "extension:qzx")
    assert json.loads(qzx.payload)["note"] == "one per day"


def test_an_import_written_by_another_person_names_who_imported_it(
    server, project: Project
) -> None:
    status, said = _import(server, written_by="wren")
    assert status == 201, said
    events = read_events(project.path)
    assert [event.actor for event in events] == ["wren", "wren"]
    assert all(_source(event)["imported_by"] == "ines" for event in events)


def test_an_agent_generated_import_writes_every_answer_as_the_person_s_agent(
    server, project: Project
) -> None:
    status, said = _import(server, agent_generated=True, reviewed=True)
    assert status == 201, said
    events = read_events(project.path)
    assert [event.actor for event in events] == ["ines_agent", "ines_agent"]
    for event in events:
        source = _source(event)
        assert (source["agent_generated"], source["reviewed_by"], source["imported_by"]) == (
            True,
            "ines",
            "ines",
        )
    assert said["author"] == "ines_agent"


def test_a_refused_confirmation_leaves_the_record_and_the_project_unchanged(
    server, project: Project
) -> None:
    before = sorted(path.relative_to(project.path) for path in project.path.rglob("*"))
    status, said = _import(server, agent_generated=True, reviewed=False)
    assert status == 400, said
    assert "reviewed" in said["error"]
    assert read_events(project.path, withdrawn=True) == ()
    after = sorted(path.relative_to(project.path) for path in project.path.rglob("*"))
    assert after == before


def test_the_imported_file_is_kept_as_it_came(server, project: Project) -> None:
    status, said = _import(server, written_by="wren")
    assert status == 201, said
    kept = project.path / "imports" / said["kept_as"]
    assert json.loads(kept.read_text("utf-8"))["document"] == {"answers": ANSWERS}
    assert all(_source(event)["kept_as"] == said["kept_as"] for event in read_events(project.path))


def test_an_answer_the_engine_cannot_place_is_refused_in_words_and_the_rest_written(
    server, project: Project
) -> None:
    document = {"extension:csv": {"word": "yes"}, "extension:nope": {"word": "yes"}, "x": "y"}
    status, said = _post(
        server,
        {
            "actor": "ines",
            "import": {"kind": "answers", "name": "a.json", "document": json.dumps(document)},
        },
    )
    assert status == 201, said
    assert [event.target for event in read_events(project.path)] == ["extension:csv"]
    assert {row["target"] for row in said["refused"]} == {"extension:nope", "x"}


def test_the_import_dialog_offers_the_record_s_authors(server, project: Project) -> None:
    _import(server, written_by="wren")
    status, said = _get(server, "/api/v1/authors")
    assert status == 200, said
    assert said["authors"] == ["wren"]


# ---------------------------------------------------------------------------
# The same statement on the command line.
# ---------------------------------------------------------------------------

EXTENSION_SHEET = (
    "extension,files,what it is (your words),format / program / instrument it belongs to,"
    "worth reading? (yes | no | never)\n"
    "qzx,2,a tracker's trace,,never\n"
    "csv,4,,,yes\n"
)


def _record_import(project: Project, sheet: Path, *args: str, stdin: str = "") -> Any:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ingence.cli.main",
            "record",
            "import",
            str(sheet),
            "-p",
            str(project.path),
            "--actor",
            "ines",
            *args,
        ],
        capture_output=True,
        text=True,
        input=stdin,
        check=False,
    )


def test_the_record_verb_writes_the_named_author_and_who_imported_it(
    project: Project, tmp_path: Path
) -> None:
    sheet = tmp_path / "extensions.csv"
    sheet.write_text(EXTENSION_SHEET)
    done = _record_import(project, sheet, "--written-by", "wren")
    assert done.returncode == 0, done.stdout + done.stderr
    events = read_events(project.path)
    assert [event.actor for event in events] == ["wren", "wren"]
    assert all(_source(event)["imported_by"] == "ines" for event in events)
    assert "written by wren" in done.stdout


def test_the_record_verb_writes_an_agent_s_sheet_once_the_review_is_confirmed(
    project: Project, tmp_path: Path
) -> None:
    sheet = tmp_path / "extensions.csv"
    sheet.write_text(EXTENSION_SHEET)
    refused = _record_import(project, sheet, "--agent-generated", stdin="n\n")
    assert refused.returncode != 0
    assert read_events(project.path, withdrawn=True) == ()

    confirmed = _record_import(project, sheet, "--agent-generated", stdin="y\n")
    assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
    events = read_events(project.path)
    assert [event.actor for event in events] == ["ines_agent", "ines_agent"]
    assert all(_source(event)["reviewed_by"] == "ines" for event in events)


def test_the_record_verb_takes_the_confirmation_as_a_flag(project: Project, tmp_path: Path) -> None:
    sheet = tmp_path / "extensions.csv"
    sheet.write_text(EXTENSION_SHEET)
    done = _record_import(project, sheet, "--agent-generated", "--reviewed")
    assert done.returncode == 0, done.stdout + done.stderr
    assert {event.actor for event in read_events(project.path)} == {"ines_agent"}


def test_only_what_a_row_takes_is_read_from_an_answer(server, project: Project) -> None:
    """A file says what it likes; only an answer's words reach the Record (no page control)."""
    document = {
        "extension:csv": {"word": "yes", "together": 9, "sheet": "elsewhere.csv", "rank": 1}
    }
    status, said = _post(
        server,
        {
            "actor": "ines",
            "import": {"kind": "answers", "name": "a.json", "document": json.dumps(document)},
        },
    )
    assert status == 201, said
    (event,) = read_events(project.path)
    source = _source(event)
    assert source["imported_from"] == "a.json"
    assert "answered_together" not in source


def test_an_import_keeps_every_answer_already_in_force(server, project: Project) -> None:
    """An import is a bulk answer, and like an answer over a list it keeps what was answered
    already, saying so, rather than writing over it or writing it again."""
    from ingence.record.log import latest_by_target

    status, said = _post(server, {"actor": "ines", "target": "extension:qzx", "word": "no"})
    assert status == 201, said
    status, said = _import(server, written_by="wren")
    assert status == 201, said
    assert [row["target"] for row in said["written"]] == ["extension:csv"]
    assert said["kept"] == ["extension:qzx"]
    status, said = _import(server, agent_generated=True, reviewed=True)
    assert status == 201, said
    assert (said["written"], said["kept"]) == ([], ["extension:csv", "extension:qzx"])
    in_force = latest_by_target(read_events(project.path))
    assert (in_force["extension:qzx"].actor, in_force["extension:csv"].actor) == ("ines", "wren")
    assert len(read_events(project.path)) == 2


def test_who_imported_an_answer_is_shown_beside_it(server, project: Project) -> None:
    """An answer shown under another person's name says who imported it."""
    from ingence.api.routes import pages
    from ingence.storage.catalog import open_catalog

    _import(server, written_by="wren")
    with open_catalog(project.path) as catalog:
        shown = pages.page(catalog, project.path, "extensions", answered=True)
    (row,) = [row for row in shown["rows"] if row["target"] == "extension:qzx"]
    assert (row["in_force"]["actor"], row["in_force"]["imported_by"]) == ("wren", "ines")
    assert row["in_force"]["imported_from"] == "answers-from-kelt.json"


def test_one_person_spelled_two_ways_is_one_author() -> None:
    """The importer's own name, cased differently, is the importer."""
    mine = stated("ines", written_by="Ines")
    assert (mine.author, mine.source()) == ("ines", {})


def test_an_older_file_s_author_field_is_no_answer() -> None:
    """An older file's author field is not an answer.

    Files exported before Import asked who wrote them carry such a field."""
    from ingence.review.answer_file import read_answers

    read = read_answers({"answers": {"suggested_by": "tomas", "extension:csv": {"word": "yes"}}})
    assert (list(read.answers), read.blank, read.unreadable) == (["extension:csv"], (), ())


def test_a_stored_answer_given_beside_somebody_else_s_guess_is_the_person_s_own() -> None:
    """A stored answer given beside somebody else's guess is the person's own.

    Its record says ``guess_taken: false``."""
    from ingence.record.imported import imported_from

    assert imported_from({"page": "review", "prefilled_by": "wren", "guess_taken": False}) is None
    assert (
        imported_from({"page": "review", "prefilled_by": "wren", "guess_taken": True}) == "a file"
    )
    assert imported_from({"sheet": "a.csv", "prefilled_by": "wren"}) == "a.csv"


def test_a_meaning_with_no_kind_is_refused_in_plain_words(server, project: Project) -> None:
    """The refusal a person reads when a file gives a meaning without its kind."""
    # A question offering no words, as the rules in force spell its target.
    _, pack = _get(server, "/api/v1/questions/pack")
    target = next(row["target"] for row in pack["questions"] if not row["words"])
    document = {target: {"word": "a measured spread"}}
    status, said = _post(
        server,
        {
            "actor": "ines",
            "import": {"kind": "answers", "name": "a.json", "document": json.dumps(document)},
        },
    )
    assert status == 201, said
    (refused,) = said["refused"]
    assert "[kind]" in refused["error"]
    assert "axis" not in refused["error"]
