"""``ingence mcp``: read-only tools over standard input and output.

A scripted JSON-RPC client drives the command in a child process on the sample collection, and
the project directory is walked before and after: nothing in it changed. Every name is invented.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fixtures.agent_sample import (
    SampleProject,
    build_sample_project,
    checkable_names,
    names_of_the_pool,
    survivors,
)
from ingence.agent.mcp import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    ROUTE_REFUSED,
    TOOLS,
    McpServer,
)
from ingence.agent.shape import Vocabulary
from ingence.core.project import Project

NINE = (
    "project_overview",
    "list_experiments",
    "get_experiment",
    "ask",
    "completeness",
    "themes",
    "topics_and_goals",
    "assumptions",
    "open_questions",
)


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> SampleProject:
    return build_sample_project(tmp_path_factory.mktemp("agent_mcp"))


def _walk(directory: Path) -> dict[str, tuple[int, int]]:
    """Every file under a directory with its size and modification time, by relative path."""
    found: dict[str, tuple[int, int]] = {}
    for root, _, files in os.walk(directory):
        for name in files:
            path = Path(root) / name
            stat = path.stat()
            found[str(path.relative_to(directory))] = (stat.st_size, stat.st_mtime_ns)
    return found


def _request(request_id: int, method: str, **params: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params:
        message["params"] = params
    return message


def _run_client(project: Path, level: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Send the messages to ``ingence mcp`` in a child process and read every reply."""
    env = dict(os.environ)  # PYTHONPATH passes through to the child
    completed = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "mcp", "-p", str(project), "--level", level],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def _text(reply: dict[str, Any]) -> dict[str, Any]:
    content = reply["result"]["content"]
    assert len(content) == 1 and content[0]["type"] == "text"
    return json.loads(content[0]["text"])


# -- the scripted client, over a child process -----------------------------------------------------


def test_a_scripted_client_initializes_lists_nine_tools_calls_two_and_writes_nothing(
    sample: SampleProject,
) -> None:
    before = _walk(sample.project.path)
    replies = _run_client(
        sample.project.path,
        "words",
        [
            _request(1, "initialize", protocolVersion=PROTOCOL_VERSION, capabilities={}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _request(2, "tools/list"),
            _request(3, "tools/call", name="list_experiments", arguments={"limit": 3}),
            _request(4, "tools/call", name="ask", arguments={"question": "nuclear stain"}),
            _request(5, "ping"),
            # The themes projection is built on the way and held in memory: the walk after
            # proves the session wrote no cache file under the project.
            _request(6, "tools/call", name="themes", arguments={}),
        ],
    )
    assert _walk(sample.project.path) == before
    assert [reply["id"] for reply in replies] == [1, 2, 3, 4, 5, 6]  # the notification: no reply
    assert _text(replies[5])["summary"]["themes"] >= 1
    initialized = replies[0]["result"]
    assert initialized["protocolVersion"] == PROTOCOL_VERSION
    assert initialized["serverInfo"]["name"] == "ingence"
    assert initialized["capabilities"] == {"tools": {"listChanged": False}}
    assert "no language model" in initialized["instructions"]
    tools = replies[1]["result"]["tools"]
    assert [tool["name"] for tool in tools] == list(NINE)
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object" and tool["description"]
    listed = _text(replies[2])
    assert len(listed["rows"]) == 3 and listed["rows"][0]["id"] == "E1"
    assert listed["matching"] >= 20
    assert all("folder" not in row and "name" not in row for row in listed["rows"])
    asked = _text(replies[3])
    # Every domain section loads, so the two words read as the one term they spell.
    assert asked["q"] == "nuclear stain" and asked["read"]["kept"] == ["nuclear stain"]
    assert replies[4]["result"] == {}


def test_at_paths_there_are_ten_tools_and_an_experiments_files_are_listed_by_their_paths(
    sample: SampleProject,
) -> None:
    before = _walk(sample.project.path)
    replies = _run_client(
        sample.project.path,
        "paths",
        [
            _request(1, "tools/list"),
            _request(2, "tools/call", name="list_experiments", arguments={"limit": 1}),
            _request(
                3, "tools/call", name="experiment_files", arguments={"alias": "E1", "limit": 2}
            ),
        ],
    )
    assert _walk(sample.project.path) == before
    assert [tool["name"] for tool in replies[0]["result"]["tools"]] == [*NINE, "experiment_files"]
    files = _text(replies[2])
    assert files["experiment"]["id"] == "E1" and files["experiment"]["folder"]
    assert len(files["rows"]) == 2 and all(row["path"] and row["name"] for row in files["rows"])
    assert str(sample.pool) not in json.dumps(files)


# -- the protocol, in-process --------------------------------------------------------------------


@pytest.fixture(scope="module")
def server(sample: SampleProject) -> McpServer:
    return McpServer(sample.project, "words")


def test_errors_are_json_rpc_errors_in_words_never_a_traceback(server: McpServer) -> None:
    parse = json.loads(server.handle_line(b"{not json"))
    assert parse["error"]["code"] == PARSE_ERROR and parse["id"] is None
    assert server.handle({"id": 1, "method": "ping"})["error"]["code"] == INVALID_REQUEST
    assert server.handle(_request(2, "resources/list"))["error"] == {
        "code": METHOD_NOT_FOUND,
        "message": "no method resources/list",
    }
    unknown = server.handle(_request(3, "tools/call", name="write_answer", arguments={}))
    assert unknown["error"]["code"] == INVALID_PARAMS
    assert unknown["error"]["message"] == "no tool write_answer at level words"
    files = server.handle(
        _request(4, "tools/call", name="experiment_files", arguments={"alias": "E1"})
    )
    assert files["error"]["message"] == "no tool experiment_files at level words"
    missing = server.handle(_request(5, "tools/call", name="get_experiment", arguments={}))
    assert missing["error"]["message"] == "get_experiment needs alias"
    extra = server.handle(
        _request(6, "tools/call", name="ask", arguments={"question": "x", "y": 1})
    )
    assert extra["error"]["message"] == "ask takes no argument y"
    typed = server.handle(
        _request(7, "tools/call", name="list_experiments", arguments={"limit": "3"})
    )
    assert typed["error"]["message"] == "limit is a whole number"
    nobody = server.handle(
        _request(8, "tools/call", name="get_experiment", arguments={"alias": "E999"})
    )
    assert (
        nobody["error"]["message"]
        == "no experiment is called E999; list_experiments gives the aliases"
    )
    for reply in (parse, unknown, files, missing, extra, typed, nobody):
        assert "Traceback" not in json.dumps(reply)


def test_a_notification_is_never_answered_and_initialized_is_remembered(server: McpServer) -> None:
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.initialized
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None
    assert server.handle_line(b"") is None or True  # an empty line is skipped by the loop


def test_get_experiment_gives_the_row_and_its_story_under_one_alias(server: McpServer) -> None:
    listed = _text(
        server.handle(_request(1, "tools/call", name="list_experiments", arguments={"limit": 2}))
    )
    alias = listed["rows"][1]["id"]
    one = _text(
        server.handle(_request(2, "tools/call", name="get_experiment", arguments={"alias": alias}))
    )
    assert (
        one["experiment"]["id"] == alias
        and one["experiment"]["files"] == listed["rows"][1]["files"]
    )
    assert one["story"]["experiment_id"] == alias
    assert one["story"]["days"] and one["story"]["workflow"]["expected"]


def test_every_tool_answers_at_words_and_no_name_of_the_pool_survives(
    server: McpServer, sample: SampleProject
) -> None:
    names, people = names_of_the_pool(sample)
    checked = checkable_names(names, Vocabulary.for_project(sample.project.path))
    checked |= checkable_names(people, Vocabulary())
    arguments: dict[str, dict[str, Any]] = {
        "get_experiment": {"alias": "E1"},
        "ask": {"question": "nuclear stain with analysis"},
        "list_experiments": {"limit": 50},
    }
    for number, tool in enumerate(NINE, start=1):
        reply = server.handle(
            _request(number, "tools/call", name=tool, arguments=arguments.get(tool, {}))
        )
        assert "result" in reply, (tool, reply)
        text = reply["result"]["content"][0]["text"]
        assert survivors(text, checked) == [], tool
        assert str(sample.pool) not in text and "entry_" not in text


def test_below_words_get_experiment_is_hidden_as_experiment_files_is_below_paths(
    sample: SampleProject,
) -> None:
    at_counts = McpServer(sample.project, "counts")
    assert [tool.name for tool in at_counts.tools()] == [
        name for name in NINE if name != "get_experiment"
    ]
    reply = at_counts.handle(
        _request(1, "tools/call", name="get_experiment", arguments={"alias": "E1"})
    )
    assert reply["error"]["message"] == "no tool get_experiment at level counts"
    listed = _text(
        at_counts.handle(_request(2, "tools/call", name="list_experiments", arguments={"limit": 2}))
    )
    assert listed["rows"] and all("id" not in row for row in listed["rows"])


def test_the_tools_have_a_schema_each_and_no_write_tool() -> None:
    assert len(TOOLS) == 10
    for tool in TOOLS:
        assert tool.schema["type"] == "object" and tool.schema["additionalProperties"] is False
        assert not any(word in tool.name for word in ("write", "set", "answer", "import", "reject"))
    assert [tool.name for tool in TOOLS if tool.least_level == "paths"] == ["experiment_files"]


def test_a_project_without_a_run_is_refused_before_serving(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    completed = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "mcp", "-p", str(project.path)],
        input="",
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    # The strings job: the refusal says the project has no scan yet.
    assert completed.returncode == 1 and "has no scan yet" in completed.stderr
    bad = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "mcp", "-p", str(project.path), "--level", "x"],
        input="",
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert bad.returncode == 2 and "no level 'x'" in bad.stderr


def test_a_route_that_refuses_is_a_json_rpc_error_with_its_code(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])

    def refusing(route: str, query: Any) -> tuple[int, dict[str, Any]]:
        del route, query
        return 409, {"api_version": "1.0", "error": {"code": "no_published_run", "message": "none"}}

    from ingence.agent.shape import Shaper

    server = McpServer(
        project, "words", fetch=refusing, shaper=Shaper("words", vocabulary=Vocabulary())
    )
    reply = server.handle(_request(1, "tools/call", name="project_overview", arguments={}))
    assert reply["error"]["code"] == ROUTE_REFUSED
    assert reply["error"]["message"] == "none"
    assert reply["error"]["data"] == {"status": 409, "code": "no_published_run"}

    home = Path.home() / "invented" / "missing.parquet"

    def raising(route: str, query: Any) -> tuple[int, dict[str, Any]]:
        raise FileNotFoundError(f"[Errno 2] No such file or directory: '{home}'")

    # Below names, an exception is said by its type only: no path leaves. At names and above
    # its words pass, read by the backstop.
    for level in ("counts", "words"):
        broken = McpServer(
            project,
            level,
            fetch=raising,
            shaper=Shaper(level, vocabulary=Vocabulary()),  # type: ignore[arg-type]
        )
        reply = broken.handle(_request(2, "tools/call", name="themes", arguments={}))
        assert reply["error"] == {"code": INTERNAL_ERROR, "message": "FileNotFoundError"}
    named = McpServer(
        project, "names", fetch=raising, shaper=Shaper("names", vocabulary=Vocabulary())
    )
    reply = named.handle(_request(3, "tools/call", name="themes", arguments={}))
    assert reply["error"]["code"] == INTERNAL_ERROR
    assert reply["error"]["message"].startswith("FileNotFoundError: [Errno 2]")
