"""``ingence mcp``: the project served to a program over standard input and output.

MCP over stdio is newline-delimited JSON-RPC 2.0: one request a line in, one answer a line out.
The server answers ``initialize``, ``ping``, ``tools/list`` and ``tools/call``, accepts the
``notifications/initialized`` notification (and any other notification, with no reply), and
refuses anything else in the JSON-RPC error shape, with words and never a traceback.

Every tool is read-only, and there is no write tool. Each reads one or two ``/api/v1``
routes in-process (``ingence.api.v1.dispatch``), shapes the answer to the level the command
was started at (:mod:`ingence.agent.shape`), and sends it as one text block of compact JSON.
``experiment_files`` exists only at the level ``paths``, where file names and paths leave the
project at all. Experiments are named by local aliases (``E1``, ``E2``...), given in date order
on first need and kept for the server's life. An identifier never leaves.

Standard library only: no MCP package, as the Dashboard's own HTTP server is standard library.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from http import HTTPStatus
from typing import IO, TYPE_CHECKING, Any

from ingence import __version__
from ingence.agent.brief import Fetch, in_process_fetch
from ingence.agent.shape import LEVELS, Aliases, Level, Shaper, compact_json, level_named

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.core.project import Project

__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "TOOLS",
    "McpServer",
    "Tool",
    "serve",
]

#: The protocol revision the server speaks. An older one a client names is answered as it asked.
PROTOCOL_VERSION = "2025-06-18"
_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "ingence"

#: JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
#: A refusal a route stated (its status and code are in the error's data).
ROUTE_REFUSED = -32000

#: The most rows one page of experiments or files gives, and the server's default.
_PAGE_MOST = 500
_PAGE_DEFAULT = 50
#: The most pages read to number the experiments on first need.
_NUMBERING_PAGES = 40
_WORD = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*")


@dataclass(frozen=True, slots=True)
class Tool:
    """One tool: its name, what it gives, its input schema, and the least level it exists at."""

    name: str
    description: str
    schema: dict[str, Any]
    least_level: str = "counts"

    def listed(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}


def _schema(
    properties: dict[str, Any] | None = None, required: Sequence[str] = ()
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_PAGING = {
    "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "Rows to skip."},
    "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": _PAGE_MOST,
        "default": _PAGE_DEFAULT,
        "description": "The most rows to give.",
    },
}

#: The tools, in the order ``tools/list`` gives them.
TOOLS: tuple[Tool, ...] = (
    Tool(
        "project_overview",
        "The project in one screen: counts of experiments, files and areas, the span of dates, "
        "file types, completeness, repeats, the leading themes, goals, and how far the reading "
        "reached.",
        _schema(),
    ),
    Tool(
        "list_experiments",
        "The experiments the engine found, a page at a time, in date order: each with its alias "
        "(E1, E2...), dates, theme, files by stage and kind, completeness and repeats. Filters: "
        "q (words), state (both, raw_only, analysis_only, neither).",
        _schema(
            {
                "q": {"type": "string", "description": "Words an experiment must match."},
                "state": {
                    "type": "string",
                    "enum": ["both", "raw_only", "analysis_only", "neither"],
                    "description": "Keep experiments in one completeness state.",
                },
                **_PAGING,
            }
        ),
    ),
    Tool(
        "get_experiment",
        "One experiment by its alias (E1, E2...): its row, and its story day by day with its "
        "workflow, repeats and what it was made from. Only at level words and above, where "
        "aliases exist.",
        _schema(
            {"alias": {"type": "string", "description": "The experiment's alias, such as E3."}},
            ["alias"],
        ),
        least_level="words",
    ),
    Tool(
        "ask",
        "A question in words, answered from the run: how the words were read and widened, the "
        "filters found in them, the experiments matching (best first) and those in the same "
        "theme as a match. An empty experiments list means nothing matched; the count of "
        "matches says whether anything did.",
        _schema(
            {"question": {"type": "string", "description": "The question, in words."}},
            ["question"],
        ),
    ),
    Tool(
        "completeness",
        "Every experiment's completeness (raw files found, how many were followed by an "
        "analysis, result tables), with the sums per person and per theme and the thresholds.",
        _schema(),
    ),
    Tool(
        "themes",
        "The lines of work (themes) the run found: each with its words, how many experiments it "
        "holds, its years, its direction and its stability.",
        _schema(),
    ),
    Tool(
        "topics_and_goals",
        "The person's own topics and goals, and what each comes to in the run: members, "
        "experiments, matches by tier, and drift since the compared run.",
        _schema(),
    ),
    Tool(
        "assumptions",
        "The assumptions the run rests on: each a claim the engine made from the evidence it "
        "had, with its kind, tier and how many files rest on it. Nothing here rejects one.",
        _schema(),
    ),
    Tool(
        "open_questions",
        "The checks waiting for a person's answer, what one answer settles most first, with the "
        "answers each takes. Nothing here answers one.",
        _schema(),
    ),
    Tool(
        "experiment_files",
        "One experiment's files by its alias, a page at a time: each file's name, path relative "
        "to its data folder, size, type, and what the engine read it as. Only at level paths.",
        _schema(
            {
                "alias": {"type": "string", "description": "The experiment's alias, such as E3."},
                **_PAGING,
            },
            ["alias"],
        ),
        least_level="paths",
    ),
)


class ToolError(Exception):
    """A tool's refusal in words, with its JSON-RPC code."""

    def __init__(self, message: str, *, code: int = INVALID_PARAMS, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class McpServer:
    """One project served at one level: the tools, and the JSON-RPC loop."""

    def __init__(
        self,
        project: Project,
        level: Level = "words",
        *,
        fetch: Fetch | None = None,
        shaper: Shaper | None = None,
    ) -> None:
        self.project = project
        self.level: Level = level_named(level)
        self._fetch = fetch if fetch is not None else in_process_fetch(project.path)
        self._shaper = shaper if shaper is not None else Shaper.for_project(project, self.level)
        self._aliases: Aliases = self._shaper.aliases
        #: The shaped experiment rows by identifier, filled when the experiments are numbered.
        self._rows: dict[str, dict[str, Any]] | None = None
        self.initialized = False

    # -- what the server offers ------------------------------------------------------------------

    def tools(self) -> list[Tool]:
        """The tools at this level: all but ``experiment_files`` below ``paths``."""
        rank = LEVELS.index(self.level)
        return [tool for tool in TOOLS if LEVELS.index(tool.least_level) <= rank]

    def instructions(self) -> str:
        return (
            f"A read-only view of one Ingence project at level {self.level} "
            f"({', '.join(LEVELS)} from least to most). Experiments are named by local aliases "
            "(E1, E2...) given by list_experiments; areas, themes, goals, topics, assumptions "
            "and questions by A, T, G, K, S and Q aliases. No tool changes the project, and no "
            "file's contents are sent. Ingence uses no language model."
        )

    # -- the JSON-RPC loop -----------------------------------------------------------------------

    def serve(self, stdin: IO[bytes], stdout: IO[bytes]) -> int:
        """Answer requests line by line until the input ends. Returns an exit status."""
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            reply = self.handle_line(line)
            if reply is not None:
                stdout.write(reply.encode("utf-8") + b"\n")
                stdout.flush()
        return 0

    def handle_line(self, line: bytes | str) -> str | None:
        """One line in, one line out (or none, for a notification)."""
        try:
            message = json.loads(line)
        except ValueError:
            return compact_json(_error(None, PARSE_ERROR, "the line is not JSON"))
        reply = self.handle(message)
        return None if reply is None else compact_json(reply)

    def handle(self, message: Any) -> dict[str, Any] | None:
        """One JSON-RPC message, answered. A notification is answered with None."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, 'a request is an object with "jsonrpc": "2.0"')
        method = message.get("method")
        has_id = "id" in message
        request_id = message.get("id")
        if not isinstance(method, str):
            return (
                _error(request_id, INVALID_REQUEST, "a request names its method")
                if has_id
                else None
            )
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params is an object") if has_id else None
        if not has_id:
            if method == "notifications/initialized":
                self.initialized = True
            return None  # a notification is never answered
        try:
            result = self._dispatch(method, params)
        except ToolError as refused:
            words = self._shaper.error_message("tool_error", str(refused))
            return _error(request_id, refused.code, words, refused.data)
        except Exception as error:  # said in words, never a traceback; below names, its type
            words = self._shaper.error_message("internal_error", f"{type(error).__name__}: {error}")
            return _error(request_id, INTERNAL_ERROR, words)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            asked = str(params.get("protocolVersion") or PROTOCOL_VERSION)
            return {
                "protocolVersion": asked if asked in _PROTOCOL_VERSIONS else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": self.instructions(),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [tool.listed() for tool in self.tools()]}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                raise ToolError("tools/call names the tool")
            if not isinstance(arguments, dict):
                raise ToolError("arguments is an object")
            return self.call(name, arguments)
        raise ToolError(f"no method {method}", code=METHOD_NOT_FOUND)

    # -- the tools -------------------------------------------------------------------------------

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """One tool's result: one text block of compact JSON, shaped and backstopped."""
        tool = next((tool for tool in self.tools() if tool.name == name), None)
        if tool is None:
            raise ToolError(f"no tool {name} at level {self.level}")
        _check_arguments(tool, arguments)
        handlers = {
            "project_overview": self._project_overview,
            "list_experiments": self._list_experiments,
            "get_experiment": self._get_experiment,
            "ask": self._ask,
            "completeness": self._completeness,
            "themes": self._themes,
            "topics_and_goals": self._topics_and_goals,
            "assumptions": self._assumptions,
            "open_questions": self._open_questions,
            "experiment_files": self._experiment_files,
        }
        answered = handlers[name](arguments)
        text = self._shaper.finish(compact_json(answered))
        return {"content": [{"type": "text", "text": text}]}

    def _read(
        self,
        route: str,
        query: Mapping[str, Sequence[str]] | None = None,
        *,
        own: Sequence[str] = (),
    ) -> dict[str, Any]:
        status, payload = self._fetch(route, query or {})
        shaped = self._shaper.shape(route, payload, own_words=own)
        if "error" in shaped:
            error = shaped["error"]
            raise ToolError(
                str(error.get("message") or error.get("code")),
                code=ROUTE_REFUSED,
                data={"status": int(status), "code": error.get("code")},
            )
        if status != HTTPStatus.OK:
            raise ToolError(f"the route {route} answered status {int(status)}", code=ROUTE_REFUSED)
        return shaped

    def _project_overview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._read("overview")

    def _list_experiments(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._number_experiments()
        query: dict[str, list[str]] = {
            "sort": ["date"],
            "order": ["asc"],
            "offset": [str(arguments.get("offset", 0))],
            "limit": [str(arguments.get("limit", _PAGE_DEFAULT))],
        }
        words = str(arguments.get("q") or "").strip()
        if words:
            query["q"] = [words]
        if arguments.get("state"):
            query["state"] = [str(arguments["state"])]
        return self._read("experiments", query, own=_WORD.findall(words))

    def _get_experiment(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        identifier, row = self._experiment_named(arguments)
        story = self._read("experiments/story", {"id": [identifier]})
        return {"experiment": row, "story": story}

    def _ask(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        question = str(arguments.get("question") or "").strip()
        if not question:
            raise ToolError("question is the question, in words; none was given")
        self._number_experiments()
        return self._read("ask", {"q": [question]}, own=_WORD.findall(question))

    def _completeness(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._number_experiments()
        return self._read("completeness")

    def _themes(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._read("themes", {"limit": ["0"]})

    def _topics_and_goals(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._number_experiments()
        answered: dict[str, Any] = {}
        for key, route, query in (
            ("topics", "topics", {"suggestions": ["0"]}),
            ("goals", "goals", {}),
        ):
            try:
                answered[key] = self._read(route, query)
            except ToolError as refused:
                answered[key] = {"error": str(refused)}
        return answered

    def _assumptions(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._read("assumptions")

    def _open_questions(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._read("checks")

    def _experiment_files(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        identifier, _ = self._experiment_named(arguments)
        return self._read(
            "experiments/files",
            {
                "experiment": [identifier],
                "offset": [str(arguments.get("offset", 0))],
                "limit": [str(arguments.get("limit", _PAGE_DEFAULT))],
            },
        )

    # -- the aliases -----------------------------------------------------------------------------

    def _number_experiments(self) -> dict[str, dict[str, Any]]:
        """Give every experiment its alias, in date order, once; return the shaped rows by id."""
        if self._rows is not None:
            return self._rows
        rows: dict[str, dict[str, Any]] = {}
        query: dict[str, list[str]] = {
            "sort": ["date"],
            "order": ["asc"],
            "limit": [str(_PAGE_MOST)],
        }
        for page in range(_NUMBERING_PAGES):
            status, payload = self._fetch(
                "experiments", {**query, "offset": [str(page * _PAGE_MOST)]}
            )
            if status != HTTPStatus.OK or "error" in payload:
                break
            listed = payload.get("rows") or []
            shaped = self._shaper.shape("experiments", payload)
            # A raw row is paired with its shaped row by the alias its identifier was given,
            # never by position (a shaped row nothing of which is kept is left out).
            by_alias = {row["id"]: row for row in shaped.get("rows") or [] if row.get("id")}
            for raw in listed:
                identifier = str(raw.get("id") or "")
                alias = self._aliases.alias("E", identifier) if identifier else None
                if alias is not None and alias in by_alias:
                    rows[identifier] = by_alias[alias]
            if len(listed) < _PAGE_MOST or len(rows) >= int(payload.get("matching") or 0):
                break
        self._rows = rows
        return rows

    def _experiment_named(self, arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        alias = str(arguments.get("alias") or "").strip()
        rows = self._number_experiments()
        identifier = self._aliases.identifier(alias) if alias else None
        if not alias or identifier is None or identifier not in rows:
            named = alias or "(nothing)"
            raise ToolError(f"no experiment is called {named}; list_experiments gives the aliases")
        return identifier, rows[identifier]


def _check_arguments(tool: Tool, arguments: Mapping[str, Any]) -> None:
    """The arguments against the tool's schema: the required ones there, the types right."""
    properties = tool.schema["properties"]
    for name in tool.schema.get("required", ()):
        if name not in arguments:
            raise ToolError(f"{tool.name} needs {name}")
    for name, value in arguments.items():
        if name not in properties:
            raise ToolError(f"{tool.name} takes no argument {name}")
        wanted = properties[name].get("type")
        if wanted == "string" and not isinstance(value, str):
            raise ToolError(f"{name} is a string")
        if wanted == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ToolError(f"{name} is a whole number")
        if wanted == "integer":
            low, high = properties[name].get("minimum"), properties[name].get("maximum")
            if (low is not None and value < low) or (high is not None and value > high):
                raise ToolError(f"{name} is from {low} to {high}")
        choices = properties[name].get("enum")
        if choices and value not in choices:
            raise ToolError(f"{name} is one of {', '.join(choices)}")


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def serve(project: Project, level: Level = "words") -> int:
    """Serve a project on this process's standard input and output until the input ends."""
    server = McpServer(project, level)
    return server.serve(sys.stdin.buffer, sys.stdout.buffer)
