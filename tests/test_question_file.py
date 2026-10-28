"""The question file, and bringing it back filled.

The Questions page exports one self-describing file anyone can fill in; Import writes each of its
answers as the author the person names, through the answer route. Every name is invented.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.server import WRITE_HEADER, DashboardServer
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.review.question_pack import INSTRUCTIONS
from ingence.scan.census import run_census


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("question_file")
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_kelt", "2024-03-07_kelt"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2, 3):
            (pool / folder / f"zib_glorbex_{index:02d}.csv").write_text(f"a\n{index}\n")
        (pool / folder / "empty.txt").write_text("")
        (pool / folder / "brisa_trellium_notes.txt").write_text("x")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    server = DashboardServer(project.path, port=0)
    server.start()
    yield server, project
    server.stop()


def _get(server: DashboardServer, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + path) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _post(server: DashboardServer, body: dict) -> tuple[int, dict]:
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


def _import(server: DashboardServer, name: str, document: object) -> tuple[int, dict]:
    text = document if isinstance(document, str) else json.dumps(document)
    return _post(
        server, {"actor": "vorn", "import": {"kind": "answers", "name": name, "document": text}}
    )


def test_the_exported_file_says_how_to_answer_in_plain_words_and_names_nobody(served) -> None:
    server, _ = served
    status, pack = _get(server, "/api/v1/questions/pack")
    assert status == 200
    assert pack["format"] == "ingence.questions/1"
    # The words pinned whole: plain, for whoever fills the file in, offering only a question's
    # own words or cannot_say, and asking nobody to sign it.
    assert pack["instructions"] == INSTRUCTIONS
    assert INSTRUCTIONS.startswith("Questions Ingence could not answer")
    assert "your own" not in INSTRUCTIONS
    assert "suggested_by" not in INSTRUCTIONS
    assert "file and folder names" in pack["carries"]
    targets = [row["target"] for row in pack["questions"]]
    assert targets, "the invented pool raises questions under revision 4"
    assert set(pack["answers"]) == set(targets)


def test_the_file_returned_filled_writes_each_answer_and_skips_the_blank(served) -> None:
    server, project = served
    _, pack = _get(server, "/api/v1/questions/pack")
    targets = [row["target"] for row in pack["questions"]]
    # The run's own question, not a pack's keep question (which takes keep or stop).
    first = next(target for target in targets if not target.startswith("pack:"))
    returned = dict(pack["answers"])
    returned[first] = {"word": "cannot_say", "basis": "the names do not say"}
    before = len(read_events(project.path, withdrawn=True))
    status, said = _import(server, "filled.json", {**pack, "answers": returned})
    assert status == 201, said
    assert [row["target"] for row in said["written"]] == [first]
    assert said["blank"] == len(targets) - 1
    events = read_events(project.path, withdrawn=True)
    assert len(events) == before + 1
    source = json.loads(events[-1].payload)["source"]
    assert (events[-1].actor, source["imported_from"]) == ("vorn", "filled.json")


def test_a_file_that_is_not_json_is_refused_and_nothing_is_kept(served) -> None:
    server, project = served
    kept = project.path / "imports"
    before = sorted(kept.iterdir()) if kept.is_dir() else []
    status, said = _import(server, "broken.json", "not { json")
    assert status == 400
    assert "not JSON" in said["error"]
    assert (sorted(kept.iterdir()) if kept.is_dir() else []) == before


def test_the_pack_holds_names_and_nothing_above_the_roots(served, tmp_path: Path) -> None:
    server, project = served
    _, pack = _get(server, "/api/v1/questions/pack")
    text = json.dumps(pack)
    assert str(project.path) not in text


@pytest.mark.parametrize(
    "name", ["..\\\\project.json", "C:project.json", "imports\\\\..\\\\x.json", "a/b.json"]
)
def test_a_file_s_name_is_recorded_plainly_on_every_platform(served, name) -> None:
    server, project = served
    status, said = _import(server, name, {"extension:csv": {"word": "yes"}})
    assert status == 201, said
    assert "/" not in said["file"] and "\\" not in said["file"]
    assert (project.path / "imports" / said["kept_as"]).is_file()
