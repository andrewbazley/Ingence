"""A settled word corrected from the Dashboard.

A person's own meaning for a word the Dictionary already settles, for the whole project or below one
folder, is written to the Record through the one answer route as a meaning on the word's target with
its scope. The Dictionary reads it back as a person's sense that outranks the shipped one, and the
next rebind binds it. The shipped vocabulary is unchanged: ``wd`` stays "working distance" in the
Dictionary as shipped, and the correction is the person's, in their Record.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ingence.api.server import WEB_ROOT, DashboardServer
from ingence.api.v1.words import WordMeaning
from ingence.core.project import Project
from ingence.dictionary.store import DictionaryStore
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("word_correction")
    pool = base / "pool"
    (pool / "kelt" / "trell").mkdir(parents=True)
    (pool / "vorn").mkdir(parents=True)
    (pool / "kelt" / "trell" / "zib_wd_01.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "trell" / "zib_wd_02.csv").write_text("a,b\n3,4\n")
    (pool / "vorn" / "brisa_wd_01.csv").write_text("a,b\n5,6\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(
        project, run_id="run_20200101T000001Z_111111111111", started_at="2020-01-01T00:00:01Z"
    )
    run_inference(
        Project.open(project.path),
        run_id="run_20200101T000002Z_222222222222",
        started_at="2020-01-01T00:00:02Z",
    )
    return project.path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def _send(
    server: DashboardServer, body: dict[str, Any], *, guarded: bool = True
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if guarded:
        headers["X-Ingence-Write"] = "1"
    request = urllib.request.Request(
        server.url.rstrip("/") + "/api/review/answer",
        method="POST",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        error.close()
        return error.code, payload


def _read(server: DashboardServer, route: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        error.close()
        return error.code, payload


def _correct(**extra: Any) -> dict[str, Any]:
    return {
        "actor": "kelt",
        "target": "term:wd",
        "answer": "trell disc",
        "theme_kind": "condition",
        "correcting": True,
        **extra,
    }


def test_a_word_s_page_says_what_it_means_and_where_it_stands(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, shown = _read(server, "/api/v1/word/meaning?word=WD")
    assert status == 200
    WordMeaning.model_validate(shown)
    assert shown["standing"] == "settled"
    assert shown["in_force"]["term"] == "working_distance"
    assert shown["in_force"]["origin"] == "builtin"
    assert shown["corrections"] == []
    assert shown["files"] == 3
    assert {folder["path"] for folder in shown["folders"]} == {"kelt/trell", "vorn"}
    assert "condition" in shown["theme_kinds"]


def test_a_word_no_name_carries_is_not_found_in_the_api_s_one_error_shape(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, refused = _read(server, "/api/v1/word/meaning?word=nothingcarriesthis")
    assert status == 404
    assert refused["error"]["code"] == "not_found"


def test_a_settled_word_is_corrected_for_the_whole_project(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        # Without saying it corrects, the refusal stands: a settled word is not defined.
        status, refused = _send(server, {**_correct(), "correcting": False})
        assert status == 400
        assert "already places" in refused["error"]
        status, written = _send(server, _correct(note="our own shorthand"))
        assert status == 201, written
        assert (written["kind"], written["target"]) == ("define", "term:wd")
        _, shown = _read(server, "/api/v1/word/meaning?word=wd")
    assert shown["in_force"]["term"] == "trell disc"
    assert shown["in_force"]["origin"] == "user"
    assert "working_distance" in [sense["term"] for sense in shown["outranked"]]
    (correction,) = shown["corrections"]
    assert correction["below"] is None
    assert correction["corrected"]["term"] == "working_distance"
    assert correction["corrected"]["scope"] == "project"
    event = read_events(project)[-1]
    payload = json.loads(event.payload)
    assert event.scope.token == "project"
    assert payload["corrects"]["origin"] == "builtin"
    assert payload["note"] == "our own shorthand"
    # The shipped vocabulary says what it said: the correction is the person's, in their Record.
    shipped = DictionaryStore.builtin().look_up("wd")
    assert shipped is not None
    assert shipped.sense.term == "working_distance"


def test_a_correction_kept_below_a_folder_leaves_the_rest_of_the_project_alone(
    project: Path,
) -> None:
    with DashboardServer(project, port=0) as server:
        status, written = _send(server, _correct(below="kelt"))
        assert status == 201, written
        _, shown = _read(server, "/api/v1/word/meaning?word=wd")
    assert [item["below"] for item in shown["corrections"]] == ["kelt"]
    assert read_events(project)[-1].scope.token == "folder:kelt"
    store = DictionaryStore.for_project(project)
    from ingence.core.ids import Scope, ScopeKind

    inside = store.look_up("wd", scope=Scope(ScopeKind.FOLDER, "kelt/trell"))
    outside = store.look_up("wd", scope=Scope(ScopeKind.FOLDER, "vorn"))
    assert inside is not None
    assert outside is not None
    assert (inside.sense.term, outside.sense.term) == ("trell disc", "working_distance")


def test_a_correction_the_run_cannot_hold_is_refused_in_plain_words(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        assert _send(server, _correct(below="nowhere/at/all"))[0] == 400
        status, refused = _send(server, _correct(answer="working_distance", theme_kind="technique"))
        assert status == 400
        assert "nothing to correct" in refused["error"]
        assert _send(server, _correct(theme_kind=""))[0] == 400
        assert _send(server, {**_correct(), "actor": ""})[0] == 400
        assert _send(server, _correct(), guarded=False)[0] == 403
    assert not [event for event in read_events(project) if event.target == "term:wd"]


def test_the_next_rebind_binds_the_correction(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        assert _send(server, _correct())[0] == 201
    opened = Project.open(project)
    run_census(
        opened,
        run_id="run_20200101T000003Z_333333333333",
        started_at="2020-01-01T00:00:03Z",
        rebind=True,
        spellings=("wd",),
    )
    with open_catalog(project) as catalog:
        terms = {
            str(term)
            for (term,) in catalog.query(
                """
                SELECT b.term FROM dictionary_bindings b
                JOIN components c ON c.component_id = b.component_id
                WHERE lower(c.normalized_value) = 'wd'
                """
            ).fetchall()
        }
    assert terms == {"trell disc"}


def test_the_correction_is_offered_from_the_word_s_page_the_inspector_and_search() -> None:
    inspector = (WEB_ROOT / "inspector.js").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    assert "correcting: true" in inspector
    assert "/api/v1/word/meaning" in inspector
    # The file inspector lists each word of the name with a way to its page, and a word named in the
    # address opens its page. (Ask lists the words it read as plain text, so no link is left there
    # to hold.)
    assert "showWord(word, undefined, backTo())" in inspector
    assert "window.inspector.showWord(focusHeld.id" in app


def test_a_correction_below_a_folder_keeps_a_project_wide_one(project: Path) -> None:
    """A correction below a folder does not wipe the person's project-wide meaning everywhere else.

    Counting only the latest event on the word's target, whatever its scope, would do that.
    """
    from ingence.core.ids import Scope, ScopeKind

    with DashboardServer(project, port=0) as server:
        assert _send(server, _correct())[0] == 201
        status, written = _send(server, _correct(answer="brisa ring", below="kelt"))
        assert status == 201, written
        _, shown = _read(server, "/api/v1/word/meaning?word=wd")
    assert sorted((item["term"], item["below"] or "") for item in shown["corrections"]) == [
        ("brisa ring", "kelt"),
        ("trell disc", ""),
    ]
    store = DictionaryStore.for_project(project)
    outside = store.look_up("wd", scope=Scope(ScopeKind.FOLDER, "vorn"))
    inside = store.look_up("wd", scope=Scope(ScopeKind.FOLDER, "kelt/trell"))
    assert outside is not None
    assert inside is not None
    assert (outside.sense.term, inside.sense.term) == ("trell disc", "brisa ring")
    # A second correction at the same scope replaces the first there, and nowhere else.
    with DashboardServer(project, port=0) as server:
        assert _send(server, _correct(answer="zib band", below="kelt"))[0] == 201
    store = DictionaryStore.for_project(project)
    inside = store.look_up("wd", scope=Scope(ScopeKind.FOLDER, "kelt/trell"))
    outside = store.look_up("wd", scope=Scope(ScopeKind.FOLDER, "vorn"))
    assert inside is not None
    assert outside is not None
    assert (outside.sense.term, inside.sense.term) == ("trell disc", "zib band")


def test_a_correction_after_a_question_s_answer_replaces_that_answer(project: Path) -> None:
    """A person's answer to a question and their correction are not two senses of one precedence at
    one scope.

    That would make the word contested, and neither would bind.
    """
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    append_event(
        project,
        kind=RecordEventKind.DEFINE,
        actor="kelt",
        occurred_at="2020-01-02T00:00:00Z",
        target="question:vocab.zib:wd",
        against_run_id="run_20200101T000002Z_222222222222",
        payload={
            "raised": "wd",
            "answer": "vorn gap",
            "theme_kind": "condition",
            "rule_id": "vocab.zib",
        },
    )
    before = DictionaryStore.for_project(project).look_up("wd")
    assert before is not None
    assert before.sense.term == "vorn gap"
    with DashboardServer(project, port=0) as server:
        # The word's page says the meaning in force is an answer to that question.
        _, shown = _read(server, "/api/v1/word/meaning?word=wd")
        assert (shown["in_force"]["origin"], shown["in_force"]["raised_by"]) == (
            "user",
            "vocab.zib",
        )
        status, written = _send(server, _correct())
    assert status == 201, written
    assert written["target"] == "question:vocab.zib:wd"
    after = DictionaryStore.for_project(project).look_up("wd")
    assert after is not None
    assert not after.ambiguous
    assert after.sense.term == "trell disc"


def test_a_word_written_in_capitals_is_bound_by_its_correction(tmp_path: Path) -> None:
    """The person's sense carries more than the lower-case spelling.

    The shipped surface written in capitals matches exactly first, so a lower-case-only sense would
    leave the rebind keeping the shipped meaning.
    """
    pool = tmp_path / "pool" / "kelt"
    pool.mkdir(parents=True)
    (pool / "zib_RPM_01.csv").write_text("a\n1\n")
    (pool / "zib_RPM_02.csv").write_text("a\n2\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[tmp_path / "pool"])
    run_census(made, run_id="run_20200101T000001Z_111111111111", started_at="2020-01-01T00:00:01Z")
    run_inference(
        Project.open(made.path),
        run_id="run_20200101T000002Z_222222222222",
        started_at="2020-01-01T00:00:02Z",
    )
    with DashboardServer(made.path, port=0) as server:
        status, written = _send(
            server, {**_correct(), "target": "term:rpm", "answer": "trell spin"}
        )
    assert status == 201, written
    hit = DictionaryStore.for_project(made.path).look_up("RPM")
    assert hit is not None
    assert hit.sense.term == "trell spin"
    run_census(
        Project.open(made.path),
        run_id="run_20200101T000003Z_333333333333",
        started_at="2020-01-01T00:00:03Z",
        rebind=True,
        spellings=("rpm",),
    )
    with open_catalog(made.path) as catalog:
        terms = {
            str(term)
            for (term,) in catalog.query(
                """
                SELECT b.term FROM dictionary_bindings b
                JOIN components c ON c.component_id = b.component_id
                WHERE lower(c.normalized_value) = 'rpm'
                """
            ).fetchall()
        }
    assert terms == {"trell spin"}


def test_a_correction_below_a_folder_is_bound_there_and_nowhere_else(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        assert _send(server, _correct(below="kelt/trell"))[0] == 201
    run_census(
        Project.open(project),
        run_id="run_20200101T000003Z_333333333333",
        started_at="2020-01-01T00:00:03Z",
        rebind=True,
        spellings=("wd",),
    )
    with open_catalog(project) as catalog:
        found = dict(
            catalog.query(
                """
                SELECT e.relative_path, b.term FROM dictionary_bindings b
                JOIN components c ON c.component_id = b.component_id
                JOIN entries e ON e.entry_id = c.entry_id
                WHERE lower(c.normalized_value) = 'wd'
                """
            ).fetchall()
        )
    assert found["kelt/trell/zib_wd_01.csv"] == "trell disc"
    assert found["vorn/brisa_wd_01.csv"] == "working_distance"


def test_a_folder_named_with_a_like_pattern_character_is_matched_as_written(
    tmp_path: Path,
) -> None:
    """The folder is matched as written, by its path and not by a ``LIKE`` pattern.

    So an underscore in its name matches only an underscore, and a neighbour's names are not
    counted.
    """
    pool = tmp_path / "pool"
    (pool / "kelt_a").mkdir(parents=True)
    (pool / "keltxa").mkdir(parents=True)
    (pool / "kelt_a" / "zib_01.csv").write_text("a\n1\n")
    (pool / "keltxa" / "zib_wd_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id="run_20200101T000001Z_111111111111", started_at="2020-01-01T00:00:01Z")
    run_inference(
        Project.open(made.path),
        run_id="run_20200101T000002Z_222222222222",
        started_at="2020-01-01T00:00:02Z",
    )
    with DashboardServer(made.path, port=0) as server:
        status, refused = _send(server, _correct(below="kelt_a"))
    assert status == 400
    assert "no name in the current run carries" in refused["error"]
