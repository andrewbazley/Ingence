"""The local HTTP surface, and the static assets it serves beside it.

The server listens on loopback only, and it uses no internet at any point. The engine owns disk
access and the interface asks the engine. The interface never opens a file itself. That is a
correctness boundary before it is a security one, and it is what lets a browser front a tool that
reads arbitrary local paths at all.

The server uses the standard library and nothing else. One reader on one machine makes read-only
queries, with no concurrency to speak of, so the server needs no web framework. Adding a dependency
to the base install is a decision, and it is worth taking when the API earns it, for example for
streaming progress or a write path. ``ThreadingHTTPServer`` keeps a slow query from blocking the
page.

The server works with the source data unmounted, because everything it serves comes from the
project store. Nothing here opens an indexed file.

One route writes, and it is guarded as the first write on a loopback port must be.
``POST /api/review/answer`` appends to the Record. It takes an answer to a question, or a verdict on
a proposed pair from the Candidates view, and each one is one event. A server on ``127.0.0.1`` with
no accounts is reachable by any page in the person's browser, so the route protects a write the way
a local write has to be protected. The ``Origin`` header, where the browser sends one, must be this
server's own. The request must also carry ``X-Ingence-Write``, which a cross-site form cannot set
and a same-origin script can. Neither is authentication, because there are no accounts to
authenticate against. Both stop a page on another site from making a decision in somebody's Record
without them knowing. Every route outside ``WRITE_ROUTES`` reads. The workspace and landing writes
are described beside ``WORKSPACE_ROUTES`` and ``LANDING_ROUTES``.

A second route writes who is deciding on this machine. ``POST /api/decider`` keeps a name in the
person's own configuration file (``api/decider.py``), outside every project and every root, behind
the same two checks. It writes nothing to a project and decides nothing. A page sends the name as
the actor of every decision, so a person types their name once per machine and not once per
browser. Every decision still states its actor, and the answer route still refuses one that does
not.

The one write route takes every review sheet's kind: a question, a class, a claim, a pair, an
extension, a word, a folder. It names the kind by its target, as one row or a selected group
(``decide.decide_target``), and writes the event that the sheet import writes for that kind. Import
writes a file's answers through the same route, each as the author that the person names
(``api/routes/imports.py``).

A batch is one request through the same way in: ``{"actor", "targets": [...], "word", "note"}``. It
answers every target with the one word and the one note, each as its own Record event and each
refused on its own terms, as a list of ``decisions`` is. It uses the same code path
(``decide.decide_many``), with the shared fields written once. The request also carries
``correcting`` and ``below``, so that a word that the Dictionary places or contests can be answered
in a batch. A list of ``decisions`` answers different things in one request, such as a word's
meaning, then each spelling that is or is not the same word. The Words section accepts a pack with
``{"target": "pack:<name>", "word": "accept", "accept_pack": true}``, which writes the answer to
"load the X pack" on every project.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import signal
import sys
import threading
import time
import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, unquote, urlparse

from ingence.api import decider as decider_file
from ingence.api import v1 as api_v1
from ingence.api.apply import DefinitionApplier, progress_keys
from ingence.api.routes import changes as change_routes
from ingence.api.routes import classes as class_routes
from ingence.api.routes import controls as controls_routes
from ingence.api.routes import coverage as coverage_routes
from ingence.api.routes import decide as decide_routes
from ingence.api.routes import dictionary as dictionary_routes
from ingence.api.routes import notes as note_routes
from ingence.api.routes import pages as page_routes
from ingence.api.routes import reorganisation as reorganisation_routes
from ingence.api.routes import review as review_routes
from ingence.api.routes import rules as rule_routes
from ingence.api.routes import search as search_routes
from ingence.api.routes import views
from ingence.api.routes import workspace as workspace_routes
from ingence.core.ids import PROJECT_SCOPE, Scope, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.record import log as record_log
from ingence.scan.cancel import CancellationToken
from ingence.storage.catalog import open_catalog
from ingence.workspace import state as workspace_state
from ingence.workspace.close_prompt import ask_before_closing

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["LANDING_ROUTES", "WEB_ROOT", "DashboardServer", "say_what_waits", "serve"]

#: The static assets, beside the engine and not downloaded. The server fetches nothing at run
#: time, so the page renders on a machine that has never had a network.
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

#: The only interface that the server will bind. A default that can be overridden is not enough: a
#: server that holds a read-only window onto somebody's filesystem has no business on a public
#: one.
HOST = "127.0.0.1"

#: The route that writes to the Record. It is named here so that a test can assert that every
#: other route refuses a write, and does not trust that none of them happens to implement one.
ANSWER_ROUTE = "/api/review/answer"

#: The most targets that one batch answer names. This keeps a request to a page's worth of rows,
#: so that a runaway page cannot hold the Record's one writer for long. A page lists fifty rows.
BATCH_LIMIT = 2_000
#: What a batch says once for every target: the answer's own words, and never who or which row.
BATCH_SHARED = (
    "word",
    "note",
    "answer",
    "theme_kind",
    "correct_value",
    "reading",
    "where",
    "described",
    "belongs_to",
    "together",
    # A word that the Dictionary already places, or that two senses claim, is answered in a batch
    # by saying it is a correction, for the whole project or below one folder.
    "correcting",
    "below",
)

#: The route that keeps who is deciding on this machine. It is a file of the person's own and
#: never the project's. A page reads it with ``GET`` and writes it with ``POST`` behind the same
#: guard.
DECIDER_ROUTE = "/api/decider"

#: The workspace's writes: save a draft, commit, start a re-run, and the search's hide, bring-back
#: and build. None is a decision about the data. That is why the rule they sit beside is narrower
#: than "one route writes", and no weaker for it. The Record has exactly one way in,
#: ``ANSWER_ROUTE``. These routes write a person's unsent edit, files that a person could write
#: with an editor (plus a checkpoint), a job that the applier already runs, a preference about what
#: a search shows, and a derived projection. Each takes the same guard as the answer route.
WORKSPACE_ROUTES = (
    "/api/workspace/draft",
    "/api/workspace/commit",
    "/api/workspace/rerun",
    # "Run the rules now" on a project that was read and never inferred. It queues the inference
    # only, and only where no rule has run in the current run.
    "/api/workspace/infer",
    # Going back to an earlier run (schema 1.22): moves the pointer, restores the Dashboard's
    # settings file, and appends one withdrawal through the Record's own writer (workspace/restore).
    "/api/workspace/restore",
    # A person's reorganization plan, kept in the workspace as a file and never applied.
    # /api/v1/explorer/plan reads what it would do.
    "/api/workspace/plan",
    # A settings file to import, previewed and never written. Each value appears beside the value
    # in force, and is refused in words where it is not a setting's. The page takes changes as
    # drafts.
    "/api/workspace/settings-import",
    # A review sheet checked by the sheet reader and never written. Groupings' Import lists each
    # answer, and a person takes each through the answer route.
    "/api/workspace/sheet-preview",
    # A preference about what to see, kept in the workspace and never in the Record: hide a
    # search result from similar searches, and bring it back. Also building the search's derived
    # projection ahead of time.
    "/api/search/hide",
    "/api/search/unhide",
    "/api/search/build",
    # A laboratory's pack installed from a file, or removed (the Dictionary page). The write is
    # a pack directory and the settings naming it, which ``teach pack install`` writes. It is
    # never a decision in the Record.
    dictionary_routes.PACK_ROUTE,
    # The controls for a job and for the contract's workflow and goals: stop a job, reject an
    # assumption, declare or withdraw a workflow, say what an area is, add or remove a goal. Each
    # is a workspace write that never pins to a run and answers while a job runs
    # (``api/routes/controls.py``).
    *controls_routes.ROUTES,
)

#: The landing page's writes: make a project, open one, and start a project's first run. None is a
#: decision about the data, and none reaches the Record. Making a project writes what
#: ``ingence init`` writes. Opening one binds this server to it, or starts a server of its own
#: beside this one. A first run is the job that a person's click on "Read it now" asks for. Each
#: takes the same guard as every other write.
LANDING_ROUTES = (
    "/api/landing/create",
    "/api/landing/open",
    "/api/landing/first-run",
    "/api/landing/rename",
    # The words that a new project was asked to bring in, written once it has a run.
    "/api/landing/carry",
)

#: Every route that accepts a write. Any other route refuses one. A test holds both halves.
WRITE_ROUTES = (ANSWER_ROUTE, DECIDER_ROUTE, *WORKSPACE_ROUTES, *LANDING_ROUTES)

#: The landing page. ``/`` serves it where no project is open or the project has no run yet, and
#: ``/landing`` serves it always ("Open another project").
LANDING_PAGE = "landing.html"
LANDING_ADDRESSES = frozenset({"/landing", "/landing/", "/landing.html"})
#: The prefix where a page asks the landing page's questions. The server reads it like any ``/api``
#: route and never pins it.
LANDING_PREFIX = "/api/landing/"

#: Whether a job is queued or running, on every ``/api/...`` response. The server reads it from
#: the project's job ledger: ``true`` or ``false``.
JOB_RUNNING_HEADER = "X-Ingence-Job-Running"
#: The run that a pinned response served. The header is present while a job is queued or running,
#: on every route that reads one run. It names the run settled before the job, which is the last
#: published run that a page can read whole. ``/api/v1`` says the same in its bodies
#: (``run_pinned``).
RUN_PINNED_HEADER = "X-Ingence-Run-Pinned"
#: The ``/api/...`` routes that are never pinned: the applier's own state, who decides, the
#: workspace, the lists and comparisons of runs (which read every run by name), and the pages about
#: words, which read the new bindings at once.
UNPINNED_ROUTES = frozenset(
    {
        "applying",
        "decider",
        "workspace",
        "runs",
        "timeline",
        "reorganisation",
        "folder-line",
        "changes",
        "word",
        "coverage",
        "dictionary",
        # A note's words, read with the Dictionary that the Record composes now.
        "note-terms",
    }
)

#: A header that a cross-site form cannot set. Its presence is not a credential, because there are
#: no accounts on a single-user machine. Requiring it means that a page on another site cannot reach
#: this route without the browser asking permission first, which is never granted.
WRITE_HEADER = "X-Ingence-Write"

#: A decision is a few hundred bytes. A ceiling means that the server refuses a malformed or
#: hostile body before anything reads it, and not after.
MAX_BODY_BYTES = 64 * 1024


class DashboardHandler(BaseHTTPRequestHandler):
    """One request. ``/api/...`` is data, and everything else is a file under :data:`WEB_ROOT`."""

    server_version = "Ingence"
    sys_version = ""

    def __init__(
        self,
        *args: Any,
        project: Path | None = None,
        applier: DefinitionApplier | None = None,
        config_home: Path | None = None,
        binding: DashboardServer | None = None,
        **kwargs: Any,
    ) -> None:
        # The server supplies the project for each request. A server that the landing page started
        # with no project is bound to one, once, when a person opens or makes it. Every request
        # after that reads that project. The server makes a handler per request.
        self._binding = binding
        if binding is not None:
            project, applier, config_home = binding.project, binding.applier, binding.config_home
        #: The project this request reads, or None where the landing page has opened none yet.
        self._bound: Path | None = project
        #: Typed as a project for every route past the landing page's guards, which are
        #: reached only where one is bound (``do_GET``, ``do_POST``).
        self._project: Path = cast("Path", project)
        self._applier = applier
        self._config_home = config_home
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Stay silent by default. A request log that names paths records somebody's data."""

    def do_GET(self) -> None:
        if self._refused_for_its_host():
            return
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        if route in LANDING_ADDRESSES or (route in ("", "/", "/index.html") and self._lands()):
            # The landing page: always at /landing, and at / until there is a run.
            self._serve_asset("/" + LANDING_PAGE)
        elif route.startswith(LANDING_PREFIX):
            self._serve_landing(route[len(LANDING_PREFIX) :], parse_qs(parsed.query))
        elif self._bound is None and route.startswith("/api/") and route != DECIDER_ROUTE:
            self._no_project_yet(route)
        elif route.startswith(api_v1.PREFIX):
            self._serve_v1(route[len(api_v1.PREFIX) :], parse_qs(parsed.query))
        elif route.startswith("/api/"):
            self._serve_api(route, parse_qs(parsed.query))
        else:
            self._serve_asset(route)

    def do_POST(self) -> None:
        """Handle the one verb that writes, and only at the routes that write."""
        if self._refused_for_its_host():
            return
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        if route not in WRITE_ROUTES:
            self._send_json(
                {"error": f"nothing at {route} accepts a write"}, status=HTTPStatus.NOT_FOUND
            )
            return
        refusal = self._why_the_write_is_refused()
        if refusal is not None:
            self._send_json({"error": refusal}, status=HTTPStatus.FORBIDDEN)
            return
        if route == DECIDER_ROUTE:
            self._set_decider()
            return
        if route in LANDING_ROUTES:
            self._landing_write(route)
            return
        if self._bound is None:
            self._send_json(
                {"error": "no project is open here yet: open or make one on the landing page"},
                status=HTTPStatus.CONFLICT,
            )
            return
        if route in WORKSPACE_ROUTES:
            self._workspace_write(route)
            return
        # Every answer waits for Re-run. A word that is defined or turned off is not applied at
        # once. It goes on the one list of what waits (``workspace/state.py``), and a definition
        # takes effect after the re-run.
        try:
            body = self._read_json_body()
            if "targets" in body and "decisions" not in body:
                body = {**body, "decisions": self._batch_decisions(body)}
            if "decisions" in body:
                status, payload = self._decide_group(body)
                self._send_json(payload, status=status)
                return
            payload = self._answer(body)
        except decide_routes.QuestionMovedError as error:
            # This is a conflict and not a bad request. The answer was fine and the run moved, so
            # the page's move is to reload and not to change what it sent.
            self._send_json({"error": str(error)}, status=HTTPStatus.CONFLICT)
        except (ValueError, KeyError, decide_routes.AnswerRefusedError) as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json(
                {"error": f"{type(error).__name__}: {error}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        else:
            self._send_json(payload, status=HTTPStatus.CREATED)

    def _workspace_write(self, route: str) -> None:
        """Handle a workspace write: a draft, a commit, a re-run, or a search's hide or build.

        The route refuses in plain words and never with a server error.
        """
        if route == "/api/workspace/plan":
            from ingence.api.routes.plans import PlanRefusedError, save_plan

            try:
                payload = save_plan(
                    self._project, self._read_json_body(), saved_at=record_log.now()
                )
            except (PlanRefusedError, ValueError) as error:
                self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(payload, status=HTTPStatus.CREATED)
            return
        if route == dictionary_routes.PACK_ROUTE:
            try:
                # A pack file travels in the body, so it has a ceiling of its own.
                body = self._read_json_body(
                    limit=dictionary_routes.PACK_BODY_BYTES,
                    too_large=dictionary_routes.PACK_TOO_LARGE,
                )
                payload = dictionary_routes.pack_write(self._project, body)
            except (ValueError, OSError, dictionary_routes.DictionaryRefusedError) as error:
                self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(payload, status=HTTPStatus.CREATED)
            return
        if route in controls_routes.ROUTES:
            try:
                decider = decider_file.read_decider(self._config_home)
                payload = controls_routes.write(
                    route,
                    self._read_json_body(),
                    context=controls_routes.Context(
                        project=self._project,
                        applier=self._applier,
                        decider=decider.name if decider is not None else None,
                    ),
                )
            except (ValueError, LookupError) as error:
                self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(payload, status=HTTPStatus.CREATED)
            return
        if route.startswith("/api/search/"):
            try:
                body = self._read_json_body()
                if route.endswith("/build"):
                    with open_catalog(self._project) as catalog:
                        payload = search_routes.build(catalog, self._project)
                else:
                    handler = (
                        search_routes.hide if route.endswith("/hide") else search_routes.unhide
                    )
                    payload = handler(self._project, body)
                self._send_json(payload, status=HTTPStatus.CREATED)
            except (ValueError, LookupError) as error:
                self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = workspace_routes.write(
                self._project, route, self._read_json_body(), applier=self._applier
            )
        except (ValueError, LookupError) as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as error:  # the route says it to the page and does not drop the connection
            self._send_json(
                {"error": f"{type(error).__name__}: {error}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self._send_json(payload, status=HTTPStatus.CREATED)

    def _decider(self) -> dict[str, Any]:
        """Return who decides on this machine, as the header of every page shows it.

        ``decider`` is None where nobody has said, and the page then asks once. The payload also
        says where the file is kept, so that a person can see that it is theirs and not the
        project's.
        """
        found = decider_file.read_decider(self._config_home)
        if found is not None:
            return found.as_payload()
        return {
            "decider": None,
            "set_at": None,
            "stored_in": str(decider_file.decider_path(self._config_home)),
        }

    def _set_decider(self) -> None:
        """Keep the name a person gave as who decides on this machine, and refuse what it cannot.

        The route refuses an empty or unprintable name. It refuses a file that would land inside a
        root that this project indexes. It refuses a name that ends ``_agent``, which marks the
        answers of an imported file as an agent's (``record/imported.py``), so a person's own never
        read as one.
        """
        try:
            body = self._read_json_body()
            name = str(body.get("decider") or "")
            roots = (
                [root.mount_path for root in Project.open(self._project).identity.roots]
                if self._bound is not None
                else []
            )
            refusal = decider_file.refusal_for(name, roots, config_home=self._config_home)
            if refusal is not None:
                raise decider_file.DeciderRefusedError(refusal)
            found = decider_file.write_decider(
                name, set_at=record_log.now(), config_home=self._config_home
            )
        except (ValueError, OSError) as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json(found.as_payload(), status=HTTPStatus.CREATED)

    def _why_the_write_is_refused(self) -> str | None:
        """Return why a write is not accepted, or None where it is.

        There are two checks, and each stops a different thing. A custom header cannot be set by an
        HTML form or an image. So a page on another site cannot make the request at all without the
        browser first asking this server for permission, which the server never gives. The origin,
        where a browser sends one, must be this server's own. A request from another page carries
        that page's origin and is refused by name. A request with no origin at all is a
        command-line client, which is a person on this machine already.
        """
        if self.headers.get(WRITE_HEADER) is None:
            return (
                f"a write must carry the {WRITE_HEADER} header. A cross-site form cannot set "
                "it, which is what keeps another page out of this Record."
            )
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            return "a write is JSON; a form-encoded body is what a cross-site post looks like"
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") not in self._own_origins():
            return f"{origin} is not this server, so a decision from it is not written"
        return None

    def _refused_for_its_host(self) -> bool:
        """Refuse a request that names another host, and say so. Return True where it was refused.

        A page on another site can reach this server through its own name. A name that the page's
        owner controls can be made to resolve to ``127.0.0.1`` after the page has loaded (DNS
        rebinding). The browser then treats this server's answers as that page's own, and the origin
        check on writes does not stop a read. The one thing that such a request cannot hide is the
        name it was made to: its ``Host`` header carries the other site's name. So every request
        names this server, as a browser on this machine always does, or names none (a command-line
        client speaking HTTP/1.0). The server refuses anything else before it reads.
        """
        host = self.headers.get("Host")
        if host is None or host.strip().lower() in self._own_hosts():
            return False
        self._send_json(
            {
                "error": (
                    f"this Dashboard answers only at its own address, not at {host}: a request "
                    "made to another name is refused, which keeps other web pages out of this "
                    "project"
                )
            },
            status=HTTPStatus.FORBIDDEN,
        )
        return True

    def _own_hosts(self) -> set[str]:
        """Return the ``Host`` values that name this server: its loopback names, with its port."""
        address = self.server.server_address
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if port is None:
            return set()
        names = (HOST, "localhost", "127.0.0.1", "[::1]")
        hosts = {f"{name}:{port}" for name in names}
        if port == 80:  # the one port a browser leaves out of the header
            hosts.update(names)
        return hosts

    def _own_origins(self) -> set[str]:
        """Return the origins that are this server.

        The function reads ``server_address`` defensively and does not unpack it, because it is
        typed loosely. On an address family that this code never binds, it is not a host and port
        pair at all, and an exception here would refuse every write.
        """
        address = self.server.server_address
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if port is None:
            return set()
        return {
            f"http://{HOST}:{port}",
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
        }

    def _read_json_body(
        self, *, limit: int = MAX_BODY_BYTES, too_large: str | None = None
    ) -> dict[str, Any]:
        declared = self.headers.get("Content-Length")
        length = int(declared) if declared and declared.isdigit() else 0
        if length <= 0 or length > limit:
            message = (
                too_large
                if too_large and length > limit
                else f"a write states a body of 1 to {limit} bytes"
            )
            raise ValueError(message)
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            message = f"the body is not JSON: {error}"
            raise ValueError(message) from error
        if not isinstance(parsed, dict):
            message = "the body is a JSON object naming the question and the answer"
            raise ValueError(message)
        return parsed

    def _worth_reading(self, body: dict[str, Any]) -> dict[str, Any]:
        """Write what a person said about reading one extension's files."""
        missing = [name for name in ("actor", "worth_reading") if not body.get(name)]
        if missing:
            message = f"a write states {', '.join(missing)}"
            raise ValueError(message)
        with open_catalog(self._project) as catalog:
            event = decide_routes.answer_extension(
                catalog,
                self._project,
                actor=str(body["actor"]),
                occurred_at=str(body.get("occurred_at") or record_log.now()),
                extension=str(body["extension"]),
                worth_reading=str(body["worth_reading"]),
                note=body.get("note"),
            )
        return {
            "event_id": event.event_id,
            "sequence": event.sequence,
            "kind": event.kind.value,
            "actor": event.actor,
            "occurred_at": event.occurred_at,
            "target": event.target,
        }

    @staticmethod
    def _event_summary(event: Any) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "sequence": event.sequence,
            "kind": event.kind.value,
            "actor": event.actor,
            "occurred_at": event.occurred_at,
            "target": event.target,
        }

    def _define_word(self, body: dict[str, Any]) -> dict[str, Any]:
        """Write what a person said that an unbound word means (the coverage view)."""
        missing = [name for name in ("actor", "answer", "theme_kind") if not body.get(name)]
        if missing:
            message = f"a write states {', '.join(missing)}"
            raise ValueError(message)
        with open_catalog(self._project) as catalog:
            event = decide_routes.define_word(
                catalog,
                self._project,
                actor=str(body["actor"]),
                occurred_at=str(body.get("occurred_at") or record_log.now()),
                word=str(body["word"]),
                answer=str(body["answer"]),
                theme_kind=str(body["theme_kind"]),
                note=body.get("note"),
            )
        return self._event_summary(event)

    def _say_folder_is_program(self, body: dict[str, Any]) -> dict[str, Any]:
        """Write whether a person says that a folder is an installed program (the Programs page)."""
        if not body.get("actor"):
            message = "a write states actor"
            raise ValueError(message)
        with open_catalog(self._project) as catalog:
            event = decide_routes.say_folder_is_program(
                catalog,
                self._project,
                actor=str(body["actor"]),
                occurred_at=str(body.get("occurred_at") or record_log.now()),
                entry_id=str(body["folder"]),
                is_program=bool(body["is_program"]),
                note=body.get("note"),
            )
        return self._event_summary(event)

    def _annotate_folder(self, body: dict[str, Any]) -> dict[str, Any]:
        """Write what a person says a folder holds (the coverage view)."""
        if not body.get("actor"):
            message = "a write states actor"
            raise ValueError(message)
        with open_catalog(self._project) as catalog:
            event = decide_routes.annotate_folder(
                catalog,
                self._project,
                actor=str(body["actor"]),
                occurred_at=str(body.get("occurred_at") or record_log.now()),
                entry_id=str(body["folder"]),
                described=body.get("described"),
                note=body.get("note"),
            )
        return self._event_summary(event)

    def _answer(self, body: dict[str, Any]) -> dict[str, Any]:
        """Turn one request into one Record event.

        The function reads every field explicitly and defaults nothing on the person's behalf. An
        actor that the server invented would be the engine attributing a decision to somebody,
        which is what the Record's own refusals exist to prevent. A body that names a
        ``candidate_id`` is a verdict on a proposed pair. Any other body is an answer to a
        question.
        """
        if "labels" in body or "overlay" in body:
            return self._write_labels(body)
        if "dictionary" in body:
            # The Dictionary page: a spelling added, a sense or spelling inactivated or brought
            # back, each as the event that the engine already reads.
            with open_catalog(self._project) as catalog:
                event = dictionary_routes.write(
                    catalog, self._project, body, occurred_at=record_log.now()
                )
            return {**self._event_summary(event), "payload": event.payload}
        if "import" in body:
            return self._import(body)
        if "withdraw" in body:
            return self._withdraw(body)
        if "target" in body:
            return self._decide_one(body)
        if "candidate_id" in body:
            return self._verdict(body)
        if "extension" in body:
            return self._worth_reading(body)
        if "word" in body:
            return self._define_word(body)
        if "folder" in body and "is_program" in body:
            return self._say_folder_is_program(body)
        if "folder" in body:
            return self._annotate_folder(body)
        missing = [name for name in ("actor", "rule_id", "raised", "kind") if not body.get(name)]
        if missing:
            message = f"a write states {', '.join(missing)}"
            raise ValueError(message)
        try:
            kind = RecordEventKind(str(body["kind"]))
        except ValueError as error:
            allowed = ", ".join(sorted(item.value for item in decide_routes.ANSWER_KINDS))
            message = f"{body['kind']!r} is not a decision a question takes: {allowed}"
            raise ValueError(message) from error
        with open_catalog(self._project) as catalog:
            event = decide_routes.answer_question(
                catalog,
                self._project,
                actor=str(body["actor"]),
                occurred_at=str(body.get("occurred_at") or record_log.now()),
                rule_id=str(body["rule_id"]),
                raised=str(body["raised"]),
                kind=kind,
                answer=body.get("answer"),
                theme_kind=body.get("theme_kind"),
                note=body.get("note"),
                scope=_scope(body.get("scope")),
            )
        return {
            "event_id": event.event_id,
            "sequence": event.sequence,
            "kind": event.kind.value,
            "actor": event.actor,
            "occurred_at": event.occurred_at,
            "target": event.target,
            "payload": event.payload,
        }

    def _write_labels(self, body: dict[str, Any]) -> dict[str, Any]:
        """Write a person's layer from the Graph workspace, in one batch.

        The layer holds display labels, and groups, exclusions and proposed relationships
        (``overlay``). Each is an annotation and is never the entry's name or a decision. The
        route checks the layer's edits whole first (``api/routes/overlay.py``), then checks and
        writes the labels (``api/routes/labels.py``), then writes the layer, so a refused batch
        writes nothing.
        """
        from ingence.api.routes.labels import LabelRefusedError, write_labels
        from ingence.api.routes.overlay import OverlayRefusedError, prepare_overlay, write_prepared

        labels = body.get("labels", [])
        overlay = body.get("overlay", [])
        if not isinstance(labels, list) or not isinstance(overlay, list):
            message = "labels is a list of {entry_id, label}; overlay a list of edits"
            raise ValueError(message)
        actor = self._actor(body)
        # The server's moment is used, and never the body's. A statement's standing is read
        # against runs, and a moment that a client chose would say nothing about what the person
        # saw.
        occurred_at = record_log.now()
        written = []
        try:
            prepared = (
                prepare_overlay(self._project, actor=actor, occurred_at=occurred_at, edits=overlay)
                if overlay
                else None
            )
            if labels:
                written += write_labels(
                    self._project, actor=actor, occurred_at=occurred_at, labels=labels
                )
            if prepared is not None:
                written += write_prepared(
                    self._project, prepared, actor=actor, occurred_at=occurred_at
                )
        except (LabelRefusedError, OverlayRefusedError) as error:
            raise decide_routes.AnswerRefusedError(str(error)) from error
        return {"written": len(written), "events": [self._event_summary(e) for e in written]}

    def _actor(self, body: dict[str, Any]) -> str:
        actor = str(body.get("actor") or "").strip()
        if not actor:
            message = "a write states actor: a decision nobody made is not attributable"
            raise ValueError(message)
        return actor

    def _import(self, body: dict[str, Any]) -> dict[str, Any]:
        """Import a file of answers under the author that the person named."""
        from ingence.api.routes.imports import import_file

        said = body.get("import")
        if not isinstance(said, dict):
            message = "an import states its kind, its file and who wrote it"
            raise ValueError(message)
        with open_catalog(self._project) as catalog:
            return import_file(
                catalog, self._project, body.get("actor"), said, occurred_at=record_log.now()
            )

    def _withdraw(self, body: dict[str, Any]) -> dict[str, Any]:
        """Withdraw one answer about a word, at the request of the person who gave it.

        The body is ``{"actor", "withdraw": {"sequence", "digest"}}``, with the answer named as the
        page read it. A digest that the Record no longer holds is a conflict, and the page reloads.
        Any other refusal is a bad request, in words.
        """
        from ingence.record.log import WithdrawRefusedError
        from ingence.workspace.withdraw import withdraw_answer

        actor = self._actor(body)
        said = body.get("withdraw")
        sequence = said.get("sequence") if isinstance(said, dict) else None
        if (
            not isinstance(said, dict)
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
        ):
            message = "a withdrawal names the answer by its sequence and its digest"
            raise ValueError(message)
        try:
            event = withdraw_answer(
                self._project,
                actor=actor,
                sequence=sequence,
                digest=str(said.get("digest") or ""),
                occurred_at=record_log.now(),
            )
        except WithdrawRefusedError as error:
            if error.stale:
                raise decide_routes.QuestionMovedError(str(error)) from error
            raise decide_routes.AnswerRefusedError(str(error)) from error
        return {**self._event_summary(event), "payload": event.payload}

    def _decide_one(self, body: dict[str, Any]) -> dict[str, Any]:
        """Write one row of a review page, named by its target, as its sheet would write it."""
        actor = self._actor(body)
        decision = decide_routes.Decision.from_body(body)
        with open_catalog(self._project) as catalog:
            context = decide_routes.DecisionContext(catalog, self._project)
            event = decide_routes.decide_target(
                context,
                decision,
                actor=actor,
                occurred_at=str(body.get("occurred_at") or record_log.now()),
            )
        return {**self._event_summary(event), "payload": event.payload}

    @staticmethod
    def _batch_decisions(body: dict[str, Any]) -> list[dict[str, Any]]:
        """Write a batch's one answer and one note out as a decision for each of its targets.

        Each target appears once, in the order given.
        """
        listed = body.get("targets")
        if (
            not isinstance(listed, list)
            or not listed
            or not all(isinstance(item, str) and item.strip() for item in listed)
        ):
            message = "a batch answer lists the targets it answers, each by its target"
            raise ValueError(message)
        targets = list(dict.fromkeys(item.strip() for item in listed))
        if len(targets) > BATCH_LIMIT:
            message = (
                f"a batch answers at most {BATCH_LIMIT:,} targets; this one names {len(targets):,}"
            )
            raise ValueError(message)
        said = {name: body[name] for name in BATCH_SHARED if body.get(name) is not None}
        return [{**said, "target": target} for target in targets]

    def _decide_group(self, body: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
        """Write a selected group: one event per row, each refused on its own terms."""
        actor = self._actor(body)
        listed = body.get("decisions")
        if not isinstance(listed, list) or not listed:
            message = "a group write lists its rows' decisions"
            raise ValueError(message)
        decisions = [
            decide_routes.Decision.from_body(item if isinstance(item, dict) else {})
            for item in listed
        ]
        with open_catalog(self._project) as catalog:
            context = decide_routes.DecisionContext(catalog, self._project)
            written, refused = decide_routes.decide_many(
                context,
                decisions,
                actor=actor,
                occurred_at=str(body.get("occurred_at") or record_log.now()),
            )
        payload: dict[str, Any] = {
            "written": [self._event_summary(event) for event in written],
            "refused": refused,
        }
        if written:
            return HTTPStatus.CREATED, payload
        if refused and all(item.get("already") for item in refused):
            # Every row was answered since its list was shown. There is nothing to write and nothing
            # wrong.
            return HTTPStatus.OK, payload
        moved = refused and all(item.get("moved") for item in refused)
        # A batch that is refused whole says why in a sentence. The page shows ``error`` and reads
        # nothing of the list. The sentence gives the one reason, or the first of several.
        if refused:
            first = str(refused[0].get("error") or "the answer was refused")
            many = f"None of the {len(refused):,} rows was answered. The first was refused: {first}"
            payload["error"] = first if len(refused) == 1 else many
        return (HTTPStatus.CONFLICT if moved else HTTPStatus.BAD_REQUEST), payload

    def _verdict(self, body: dict[str, Any]) -> dict[str, Any]:
        """Turn a verdict on a proposed pair into one Record event, and refuse what is missing."""
        missing = [name for name in ("actor", "candidate_id", "verdict") if not body.get(name)]
        if missing:
            message = f"a verdict states {', '.join(missing)}"
            raise ValueError(message)
        with open_catalog(self._project) as catalog:
            event = decide_routes.answer_candidate(
                catalog,
                self._project,
                actor=str(body["actor"]),
                occurred_at=str(body.get("occurred_at") or record_log.now()),
                candidate_id=str(body["candidate_id"]),
                verdict=str(body["verdict"]),
                note=body.get("note"),
            )
        return {
            "event_id": event.event_id,
            "sequence": event.sequence,
            "kind": event.kind.value,
            "actor": event.actor,
            "occurred_at": event.occurred_at,
            "target": event.target,
            "payload": event.payload,
        }

    def _serve_v1(self, path: str, query: dict[str, list[str]]) -> None:
        """Handle an ``/api/v1`` read: typed, versioned, and pinned to one run.

        While a job that will publish a newer run is running, every ``/api/v1`` read serves the
        run that was settled before the job began, and says so in the response.
        """
        # The pin and whether a job is busy, read from the job ledger in one transaction.
        settled, running = self._applier.reading() if self._applier is not None else (None, False)
        try:
            context = api_v1.context_for(self._project, settled=settled, job_running=running)
        except Exception as error:
            self._send_json(
                {
                    "api_version": "1.0",
                    "error": {
                        "code": "server_error",
                        "message": f"{type(error).__name__}: {error}",
                    },
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        status, payload = api_v1.dispatch(context, path, query)
        self._send_json(payload, status=status)

    def _serve_api(self, route: str, query: dict[str, list[str]]) -> None:
        # Pin the settled run for reading. While a job is queued or running, every route that reads
        # one run reads the run settled before the job. Every response says so in two headers:
        # whether a job is busy, and the run that a pinned route served.
        settled, running = self._applier.reading() if self._applier is not None else (None, False)
        parts = [part for part in route.split("/") if part][1:]
        pinned = None if parts[:1] and parts[0] in UNPINNED_ROUTES else settled
        headers = {JOB_RUNNING_HEADER: "true" if running else "false"}
        if pinned is not None:
            headers[RUN_PINNED_HEADER] = pinned
        if parts == ["search", "stream"]:
            self._stream_search(query, settled=pinned, headers=headers)
            return
        try:
            payload = self._dispatch(route, query, settled=pinned)
        except LookupError as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.NOT_FOUND, headers=headers)
        except Exception as error:  # the page shows a broken query
            self._send_json(
                {"error": f"{type(error).__name__}: {error}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                headers=headers,
            )
        else:
            self._send_json(payload, headers=headers)

    def _stream_search(
        self, query: dict[str, list[str]], *, settled: str | None, headers: dict[str, str]
    ) -> None:
        """Handle ``GET /api/search/stream``: a search's parts as they are ready, one JSON per line.

        The ranked cards come first and their chips and reasons after. The page shows the results
        that a person chooses among while the details, which cost most of the time, are read, as a
        search engine sends its first results before their snippets. The same query answers as
        ``/api/search`` does, merged. A search asked again comes whole, at once.
        """
        started = False
        try:
            with open_catalog(self._project, run_id=settled) as catalog:
                parts = search_routes.search_stream(
                    catalog,
                    self._project,
                    text=_first(query, "q"),
                    order=_order(query),
                    limit=_limit(query, default=search_routes.LIMIT),
                    offset=_offset(query),
                    show_hidden=_first(query, "hidden") == "show",
                )
                for part in parts:
                    line = json.dumps(part, default=str).encode("utf-8") + b"\n"
                    if not started:
                        self.send_response(HTTPStatus.OK)
                        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        for name, value in headers.items():
                            self.send_header(name, value)
                        # There is no length: the server writes the parts as they come, and the
                        # connection's close ends them (HTTP/1.0, as every response here).
                        self.end_headers()
                        started = True
                    self.wfile.write(line)
                    self.wfile.flush()
        except ConnectionError:
            # The page asked for another search before this one finished. That is not an error.
            # The clause covers every way a client can go: a broken pipe, a reset, and Windows'
            # abort (WinError 10053).
            return
        except Exception as error:  # a broken query is the page's to show, as /api/search's is
            said = {"phase": "error", "error": f"{type(error).__name__}: {error}"}
            if started:
                with contextlib.suppress(ConnectionError):
                    self.wfile.write(json.dumps(said).encode("utf-8") + b"\n")
                return
            status = (
                HTTPStatus.NOT_FOUND
                if isinstance(error, LookupError)
                else HTTPStatus.INTERNAL_SERVER_ERROR
            )
            self._send_json({"error": said["error"]}, status=status, headers=headers)

    def _dispatch(
        self, route: str, query: dict[str, list[str]], *, settled: str | None = None
    ) -> Any:
        parts = [part for part in route.split("/") if part][1:]
        if parts == ["applying"]:
            # Which phase runs and how far it is, and whether the server is warming a run's
            # readouts.
            from ingence.readouts import warming_payload

            if self._applier is None:
                return {"phase": "off", **progress_keys("off")}
            return {**self._applier.status(), "readouts": warming_payload(self._project)}
        if parts == ["decider"]:
            # Who decides on this machine: the name that every decision is sent with.
            return self._decider()
        if parts[:1] == ["workspace"]:
            return workspace_routes.read(self._project, parts[1:], applier=self._applier)
        if parts == ["runs"]:
            return change_routes.runs(self._project)
        if parts == ["timeline"]:
            # Every run, oldest first, each answer marked where it took effect.
            return change_routes.timeline(self._project)
        if parts == ["reorganisation"]:
            # Whether the folders were reorganised since the census before, said once.
            return reorganisation_routes.reorganisation(
                self._project, before=_first(query, "before"), after=_first(query, "after")
            )
        if parts == ["folder-line"]:
            # The one quiet line that a folder's own page shows where its decisions followed its
            # files.
            return {
                "line": reorganisation_routes.folder_line(
                    self._project, _first(query, "path") or ""
                )
            }
        if parts == ["changes"]:
            return change_routes.changes(
                self._project,
                before=_first(query, "before"),
                after=_first(query, "after"),
                examples=_limit(query, default=change_routes.EXAMPLES),
            )
        # While a definition's job is queued or runs, every page built on inference reads the run
        # settled before it. The job ledger supplies ``settled``. The pages about words read the
        # new bindings at once (``UNPINNED_ROUTES``).
        with open_catalog(self._project, run_id=settled) as catalog:
            if len(parts) == 2 and parts[0] == "pages" and parts[1] in page_routes.KINDS:
                return page_routes.page(
                    catalog,
                    self._project,
                    parts[1],
                    top=_first(query, "top"),
                    rule=_first(query, "rule"),
                    order=_order(query),
                    limit=_limit(query, default=page_routes.LIMIT),
                    offset=_offset(query),
                    answered=_answered(query) or _answered_waiting(query),
                    waiting_only=_answered_waiting(query),
                )
            if parts == ["note-terms"]:
                # A note's words read as a name is read, as it is typed: the terms that the
                # Dictionary binds and the words that nothing binds. Nothing is written.
                return note_routes.read_note(catalog, self._project, _first(query, "text"))
            if parts == ["claim-value"]:
                # Every file that one rule concluded one value about, by place. A person answers
                # "correct for all" over this list.
                return page_routes.claim_value(
                    catalog,
                    self._project,
                    rule_id=_first(query, "rule"),
                    value=_first(query, "value"),
                )
            if parts == ["word"]:
                return page_routes.word_page(
                    catalog,
                    word=_first(query, "word"),
                    rule_id=_first(query, "rule"),
                    raised=_first(query, "raised"),
                    project=self._project,
                )
            if parts == ["search"]:
                # Units, not files, ranked by a tuple. Nothing is written.
                return search_routes.search(
                    catalog,
                    self._project,
                    text=_first(query, "q"),
                    order=_order(query),
                    limit=_limit(query, default=search_routes.LIMIT),
                    offset=_offset(query),
                    show_hidden=_first(query, "hidden") == "show",
                )
            if parts[:1] == ["dictionary"]:
                # The vocabulary, where each word came from, and a person's own.
                return dictionary_routes.read(catalog, self._project, parts[1:], query)
            if parts == ["search", "suggest"]:
                return search_routes.suggest(catalog, self._project, prefix=_first(query, "prefix"))
            if parts == ["waiting"]:
                # How many rows wait on each review page, for the tabs. The server keeps the
                # count per run and Record state, and every page asks on every load. The count runs
                # on a thread of its own, and a page is told that the rows are being counted.
                return _waiting_held(catalog, self._project, patience=WAITING_PATIENCE_SECONDS)
            if parts == ["programs"]:
                return page_routes.programs(
                    catalog,
                    self._project,
                    answered=_answered(query) or _answered_waiting(query),
                    waiting_only=_answered_waiting(query),
                )
            if parts == ["project"]:
                return views.project(catalog)
            if parts == ["rules"]:
                # The rules in force, each in plain words.
                return rule_routes.rules(catalog, self._project)
            if parts == ["inventory"]:
                return views.inventory(catalog, limit=_limit(query))
            if parts == ["candidates"]:
                return views.candidates(catalog, limit=_limit(query))
            if len(parts) == 2 and parts[0] == "folder":
                return views.folder(catalog, parts[1], limit=_limit(query))
            if len(parts) == 2 and parts[0] == "entry":
                return views.entry(catalog, parts[1])
            if parts == ["logical-context"]:
                return class_routes.logical_context(
                    catalog,
                    self._project,
                    limit=_limit(query, default=class_routes.LIMIT),
                    offset=_offset(query),
                )
            if parts == ["coverage"]:
                return coverage_routes.coverage(
                    catalog,
                    self._project,
                    limit=_limit(query, default=coverage_routes.LIMIT),
                    answered=_answered(query),
                )
            if parts == ["record"]:
                return decide_routes.recorded_answers(
                    self._project, limit=_limit(query, default=200)
                )
            if parts == ["review"]:
                # This is the one route whose ranking a request may change, because reordering a
                # queue is a rendering and changes no claim.
                return review_routes.review(
                    catalog,
                    order=_order(query),
                    limit=_limit(query, default=review_routes.LIMIT),
                    offset=_offset(query),
                )
        message = f"no view at {route}"
        raise LookupError(message)

    # -- the landing page ----------------------------------------------------------------------

    def _lands(self) -> bool:
        """Whether ``/`` is the landing page: no project is open, or it has no run yet."""
        if self._bound is None:
            return True
        if self._applier is not None and self._applier.first_run_busy():
            return True
        from ingence.storage.commit import read_pointer

        try:
            return read_pointer(self._project) is None
        except Exception:  # the Dashboard says so when a pointer cannot be read
            return False

    def _no_project_yet(self, route: str) -> None:
        """Answer a read that needs a project, on a server with none open yet, without a 500."""
        words = "no project is open here yet: open or make one on the landing page, at /"
        if route.startswith(api_v1.PREFIX):
            payload: dict[str, Any] = {
                "api_version": "1.0",
                "error": {"code": "no_project", "message": words},
            }
        elif route == "/api/applying":
            self._send_json({"phase": "off", **progress_keys("off")})
            return
        else:
            payload = {"error": words}
        self._send_json(payload, status=HTTPStatus.NOT_FOUND)

    def _serve_landing(self, name: str, query: dict[str, list[str]]) -> None:
        """Handle the landing page's reads: its state, a folder checked, a project folder proposed,
        and a first run's progress.

        Each read uses small files and listings. None touches anything beneath a folder's own
        listing.
        """
        from ingence.api.routes import landing

        try:
            if name == "state":
                payload = landing.landing_state(
                    self._bound, config_home=self._config_home, applier=self._applier
                )
            elif name == "check":
                payload = landing.check_folder(_first(query, "path"))
            elif name == "project-folder":
                typed = _first(query, "path")
                root = _first(query, "root") or None
                proposed = landing.proposed_project_folder(_first(query, "name"))
                if not typed:
                    # Two folders of one name read by two projects: the second is proposed a
                    # place of its own and not the first's.
                    proposed = landing.free_project_folder(proposed, root)
                where = typed or proposed
                refusal = landing.check_project_folder(where, root)
                existing = landing.existing_project(where)
                payload = {
                    "proposed": proposed,
                    "path": where,
                    "refusal": refusal,
                    # A project already written there. The page offers to open it instead, but
                    # only where it reads the folder being asked about.
                    "existing": existing,
                    "existing_reads_root": (
                        landing.project_reads_root(existing, root) if existing and root else None
                    ),
                    # Where else it could go, where the place holds a project of another folder.
                    "alternative": (
                        landing.free_project_folder(where, root)
                        if refusal == landing.ANOTHER_HERE
                        else None
                    ),
                }
            elif name == "progress":
                payload = (
                    landing.first_run_progress(self._project, self._applier)
                    if self._bound is not None and self._applier is not None
                    else {"phase": "off"}
                )
            else:
                self._send_json(
                    {"error": f"no view at {LANDING_PREFIX}{name}"}, status=HTTPStatus.NOT_FOUND
                )
                return
        except Exception as error:  # the page says so, with a way to try again
            self._send_json(
                {"error": f"{type(error).__name__}: {error}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self._send_json(payload)

    def _landing_write(self, route: str) -> None:
        """Make a project, open one, rename one, or start a project's first run.

        The route refuses in words and never with a server error. Nothing here starts a job except
        ``first-run``.
        """
        from ingence.api.recent import RecentNotKeptError, describe_project, remember_project
        from ingence.api.routes import landing
        from ingence.core.project import NotAProjectError, SettingsFileError

        goals_written: list[dict[str, str]] | None = None
        try:
            body = self._read_json_body()
            if route.endswith("/carry"):
                if self._bound is None:
                    message = "no project is open here yet: open or make one first"
                    raise landing.LandingRefusedError(message)
                carried = landing.carry_words(self._project, body, occurred_at=record_log.now())
                self._send_json(carried, status=HTTPStatus.CREATED)
                return
            if route.endswith("/first-run"):
                if self._bound is None or self._applier is None:
                    message = "no project is open here yet: open or make one first"
                    raise landing.LandingRefusedError(message)
                landing.save_first_run_settings(self._project, body)
                landing.save_depth(self._project, body)
                self._applier.submit_first_run(dive=landing.dive_asked(body))
                payload = landing.first_run_progress(self._project, self._applier)
                self._send_json(payload, status=HTTPStatus.CREATED)
                return
            if route.endswith("/rename"):
                where = str(body.get("project") or "").strip()
                if not where:
                    message = "Say which project to rename."
                    raise landing.LandingRefusedError(message)
                renamed = landing.rename_project(Path(where), str(body.get("name") or ""))
                self._send_json({"project": describe_project(renamed)}, status=HTTPStatus.OK)
                return
            if self._binding is None:
                message = "this server cannot open another project"
                raise landing.LandingRefusedError(message)
            if route.endswith("/create"):
                project = self._make_project(body)
                # Goals typed before the project exists are written into the new project's
                # workspace, and answered as {id, text} so the page skips its own POST /api/goals.
                goals_written = landing.write_goals(project, body.get("goals"))
            else:
                typed = str(body.get("project") or "").strip()
                if not typed:
                    message = "Say which project to open."
                    raise landing.LandingRefusedError(message)
                project = Project.open(Path(typed)).path
            address = self._binding.bind(project)
            if route.endswith("/create") and body.get("read") is True:
                # Making the project and reading it is one act ("Read it now"). The census is
                # queued on the server that now reads the project, which is this one or the one
                # started beside it. Nothing walks a root unless the page said ``read``.
                reading = self._binding.server_for(project)
                if reading is None or reading.applier is None:
                    message = "the project was made, but no server reads it to start the reading"
                    raise landing.LandingRefusedError(message)
                reading.applier.submit_first_run(dive=landing.dive_asked(body))
            note = None
            try:
                remember_project(
                    project,
                    opened_at=record_log.now(),
                    name=str(body.get("name") or "").strip() or None,
                    config_home=self._config_home,
                )
            except RecentNotKeptError as not_kept:
                # The project opens and is only not listed. The reason is said once here and to
                # the page.
                note = str(not_kept)
                _say_once(note)
        except (
            landing.LandingRefusedError,
            NotAProjectError,
            SettingsFileError,
            ValueError,
        ) as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as error:  # the page says so and does not lose the connection
            self._send_json(
                {"error": f"{type(error).__name__}: {error}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self._send_json(
            {
                "url": address,
                "project": describe_project(project),
                **({"note": note} if note else {}),
                **({"goals": goals_written} if goals_written is not None else {}),
            },
            status=HTTPStatus.CREATED,
        )

    def _make_project(self, body: dict[str, Any]) -> Path:
        """Make the project that a landing page's form describes, and keep who decides if it said.

        The function refuses everything that can be refused before it writes anything: the folder
        to read, the name, where it goes, the years and the name for who decides. Then it writes the
        project, and then the name. The name is the machine's and lies outside every project, as
        the decider route writes it. ``who`` left blank, or a name already kept, changes nothing.
        """
        from ingence.api.routes import landing

        who = " ".join(str(body.get("who") or "").split())
        kept = decider_file.read_decider(self._config_home)
        writes_who = bool(who) and (kept is None or kept.name != who)
        if writes_who:
            root = landing.check_folder(str(body.get("root") or "")).get("path")
            refusal = decider_file.refusal_for(
                who, [str(root)] if root else [], config_home=self._config_home
            )
            if refusal is not None:
                raise landing.LandingRefusedError(refusal)
        project = landing.create_project(
            body, created_at=record_log.now(), config_home=self._config_home
        )
        if writes_who:
            decider_file.write_decider(who, set_at=record_log.now(), config_home=self._config_home)
        return project

    def _serve_asset(self, route: str) -> None:
        relative = "index.html" if route in ("", "/") else route.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if not target.is_file() or WEB_ROOT.resolve() not in target.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        payload: Any,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        # A page that moved on before the answer came has closed the connection. The Graph
        # workspace, for example, aborts a request that its next one supersedes. That is not an
        # error to report.
        with contextlib.suppress(ConnectionError):
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)


#: The tabs' counts, kept per run and Record state. Every page asks for them on every load, and
#: they are every review page built whole to count its rows. On a large census that takes tens of
#: seconds and gigabytes of memory, and it contends with the page that the person opened. They
#: change only when the run read or the Record does. So the server holds them under (project, run,
#: the Record's last sequence and size), a few at a time, and two requests for one key compute it
#: once.
#:
#: The server counts on a thread of its own, from the server's start. On a large project, the first
#: page's own reads answered in under a second while the first count took well over a minute. A
#: request waits for a count being made at most ``WAITING_PATIENCE_SECONDS``. It then answers that
#: the counts are being counted, and the page says so and asks again. So a small project's counts
#: come in the first answer, and a page never waits in silence for a large one's.
_WAITING: dict[tuple[str, str, int, int], dict[str, int]] = {}
_WAITING_COUNTING: dict[tuple[str, str, int, int], tuple[threading.Event, float]] = {}
_WAITING_FAILED: dict[tuple[str, str, int, int], BaseException] = {}
#: The count that a server begins as it starts, by project, until it is made. A request that finds
#: it answers "counting" before reading anything, because the Record's first read is the start's
#: too.
_WAITING_STARTING: dict[str, tuple[threading.Event, float]] = {}
#: Each count's thread and, while it reads, its catalog. Also the counts that a stopping server
#: ended, which are not failures to report (see :func:`stop_counting`).
_WAITING_THREADS: dict[tuple[str, str, int, int], threading.Thread] = {}
_WAITING_CATALOGS: dict[tuple[str, str, int, int], Any] = {}
_WAITING_STOPPED: set[tuple[str, str, int, int]] = set()
#: How long a stopping server waits for its own background work to end. The server interrupts a
#: count or a search warm first, so this limit covers only the Python between two of its queries.
BACKGROUND_STOP_SECONDS = 10.0
#: How long a stopping server waits for a count to end before it interrupts it again.
_STOP_AGAIN_SECONDS = 0.05
_WAITING_GUARD = threading.Lock()
_WAITING_HELD = 8
#: How long a request for the tabs' counts waits for a count being made before it answers that they
#: are being counted. The wait is long enough for a small project's counts to come in the same
#: answer, and short enough that a page is never left waiting in silence.
WAITING_PATIENCE_SECONDS = 2.0


def _waiting_key(project: Path, run_id: str) -> tuple[str, str, int, int]:
    events = record_log.read_events(project)
    return (
        str(project.resolve()),
        run_id,
        max((event.sequence for event in events), default=0),
        len(events),
    )


def _waiting_held(catalog: Any, project: Path, *, patience: float | None = None) -> dict[str, Any]:
    """Return the tabs' counts for the catalog's run: held, or counted now on a thread of their own.

    ``patience`` is how long to wait for a count being made (``None``: until it is made). Where the
    count is not made in time, the answer is ``{"counting": True, "since_seconds": ...}``. A count
    that failed raises its error to the request that finds it, and the next request counts again.
    """
    with _WAITING_GUARD:
        starting = _WAITING_STARTING.get(str(project.resolve()))
    if starting is not None and patience is not None:
        begun, when = starting
        if not begun.wait(patience):
            return {"counting": True, "since_seconds": round(time.monotonic() - when, 1)}
    run_id = catalog.run_directory.name if catalog.run_directory is not None else ""
    key = _waiting_key(project, run_id)
    held, event, since = _counting(project, run_id, key)
    if held is not None:
        return held
    if event.wait(patience):
        with _WAITING_GUARD:
            held = _WAITING.get(key)
            failed = _WAITING_FAILED.pop(key, None)
        if held is not None:
            return dict(held)
        if failed is not None:
            raise failed
    return {"counting": True, "since_seconds": round(time.monotonic() - since, 1)}


def _counting(
    project: Path, run_id: str, key: tuple[str, str, int, int]
) -> tuple[dict[str, int] | None, threading.Event, float]:
    """Return the held counts, or the count being made for ``key`` and when it started.

    The function starts the count now if none is being made.
    """
    with _WAITING_GUARD:
        held = _WAITING.get(key)
        if held is not None:
            return dict(held), threading.Event(), 0.0
        found = _WAITING_COUNTING.get(key)
        if found is not None:
            return None, *found
        event = threading.Event()
        started = time.monotonic()
        _WAITING_COUNTING[key] = (event, started)
    thread = threading.Thread(
        target=_count, args=(project, run_id, key, event), daemon=True, name="tab-counts"
    )
    with _WAITING_GUARD:
        _WAITING_THREADS[key] = thread
    thread.start()
    return None, event, started


def _count(
    project: Path, run_id: str, key: tuple[str, str, int, int], event: threading.Event
) -> None:
    try:
        with open_catalog(project, run_id=run_id or None) as catalog:
            with _WAITING_GUARD:
                _WAITING_CATALOGS[key] = catalog
                stopped = key in _WAITING_STOPPED
            if stopped:
                # The server was stopped while this catalog was opening, before there was a query
                # to interrupt. Begin no query, because a stopped count is not a failure.
                message = "the server stopped before the count began"
                raise InterruptedError(message)
            counted = page_routes.waiting(catalog, project)
    except BaseException as error:  # the request that finds it hears it, then counts again
        with _WAITING_GUARD:
            if key not in _WAITING_STOPPED:
                _WAITING_FAILED[key] = error
            _WAITING_STOPPED.discard(key)
            _WAITING_COUNTING.pop(key, None)
            _WAITING_CATALOGS.pop(key, None)
            _WAITING_THREADS.pop(key, None)
        event.set()
        return
    with _WAITING_GUARD:
        _WAITING[key] = dict(counted)
        _WAITING_STOPPED.discard(key)
        _WAITING_COUNTING.pop(key, None)
        _WAITING_CATALOGS.pop(key, None)
        _WAITING_THREADS.pop(key, None)
        while len(_WAITING) > _WAITING_HELD:
            _WAITING.pop(next(iter(_WAITING)))
    event.set()


def stop_counting(project: Path, *, timeout: float = BACKGROUND_STOP_SECONDS) -> None:
    """End the counts being made for the project: interrupt each query and wait for its thread.

    A stopped server leaves no thread of its own inside DuckDB. If a count is still reading when the
    process exits, the process ends with an abort ("terminate called without an active exception").
    A count ended here is not a failure, and the next request counts again.
    """
    prefix = str(project.resolve())
    deadline = time.monotonic() + timeout
    while True:
        with _WAITING_GUARD:
            running = [
                (_WAITING_THREADS.get(key), _WAITING_CATALOGS.get(key))
                for key in _WAITING_COUNTING
                if key[0] == prefix
            ]
            _WAITING_STOPPED.update(key for key in _WAITING_COUNTING if key[0] == prefix)
        # Interrupt again until the count ends. DuckDB forgets an interrupt that arrives before a
        # query begins, and a count that is still opening its catalog has none to interrupt. So one
        # interrupt could end nothing and leave the count running past the stop. A count that finds
        # itself stopped begins no query (see :func:`_count`).
        for _, catalog in running:
            if catalog is not None:
                with contextlib.suppress(Exception):
                    catalog.connection.interrupt()
        remaining = deadline - time.monotonic()
        joinable = [
            thread
            for thread, _ in running
            if thread is not None and thread is not threading.current_thread()
        ]
        if not running or remaining <= 0:
            return
        if not joinable:
            time.sleep(min(_STOP_AGAIN_SECONDS, remaining))
            continue
        for thread in joinable:
            thread.join(min(_STOP_AGAIN_SECONDS, max(0.0, deadline - time.monotonic())))


def _starting(project: Path) -> threading.Event:
    """Say that a server starting on ``project`` is counting its tabs.

    The event is set once the count is done.
    """
    event = threading.Event()
    with _WAITING_GUARD:
        _WAITING_STARTING[str(project.resolve())] = (event, time.monotonic())
    return event


def _started(project: Path, event: threading.Event) -> None:
    with _WAITING_GUARD:
        found = _WAITING_STARTING.get(str(project.resolve()))
        if found is not None and found[0] is event:
            _WAITING_STARTING.pop(str(project.resolve()))
    event.set()


def waiting_settled(timeout: float = 60.0) -> bool:
    """Wait until no tab count is being made (for tests), and return whether none is."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _WAITING_GUARD:
            pending = [event for event, _ in _WAITING_COUNTING.values()]
            pending += [event for event, _ in _WAITING_STARTING.values()]
        if not pending:
            return True
        pending[0].wait(max(0.0, deadline - time.monotonic()))
    return False


def _scope(value: Any) -> Scope:
    """Read the scope that a decision applies to, defaulting to the whole project.

    Scope is first-class, because the most common real correction is not "this is wrong" but "this
    is wrong here". An example is "in this folder ``ms`` means mass spectrometry", which is a
    folder-scoped decision. The function refuses an unknown kind and does not widen it to the
    project, because silently deciding something everywhere when a person meant one folder is the
    worst available failure.
    """
    if not value:
        return PROJECT_SCOPE
    if not isinstance(value, dict):
        message = "a scope is an object naming its kind and, where it has one, its value"
        raise ValueError(message)
    try:
        kind = ScopeKind(str(value.get("kind")))
    except ValueError as error:
        allowed = ", ".join(sorted(item.value for item in ScopeKind))
        message = f"{value.get('kind')!r} is not a scope kind: {allowed}"
        raise ValueError(message) from error
    named = value.get("value")
    if kind is not ScopeKind.PROJECT and not named:
        message = f"a {kind.value} scope names the {kind.value} it applies to"
        raise ValueError(message)
    return Scope(kind, None if named is None else str(named))


def _limit(query: dict[str, list[str]], *, default: int = views.LIMIT) -> int:
    values = query.get("limit")
    if not values:
        return default
    try:
        return max(1, min(int(values[0]), 5000))
    except ValueError:
        return default


def _first(query: dict[str, list[str]], name: str) -> str | None:
    """Return one named value of a request's query, or None where it gives none."""
    values = query.get(name)
    if not values or not values[0].strip():
        return None
    return values[0].strip()


def _answered(query: dict[str, list[str]]) -> bool:
    """Return whether a review page is asked for its answered rows too (``answered=show``).

    Absent, a page leaves them out. An answered row has left the list, and a person asks to see it
    again to revise it.
    """
    return _first(query, "answered") == "show"


def _answered_waiting(query: dict[str, list[str]]) -> bool:
    """Return whether a review page asks only for the answered rows that still wait for Re-run.

    The query is ``answered=waiting``. The rows that the run has read leave the list.
    """
    return _first(query, "answered") == "waiting"


def _offset(query: dict[str, list[str]]) -> int:
    values = query.get("offset")
    if not values:
        return 0
    try:
        return max(0, int(values[0]))
    except ValueError:
        return 0


def _order(query: dict[str, list[str]]) -> list[str] | None:
    """Read the ranking a request asked for, as a comma-separated list of dimensions.

    Absent means the review queue's own order. The function refuses a name that the queue does not
    compute, and does not drop it. A queue ordered differently from the way that the page says it
    is ordered gives a reader no way to tell.
    """
    values = query.get("order")
    if not values:
        return None
    return [name.strip() for name in values[0].split(",") if name.strip()]


class _DashboardHTTPServer(ThreadingHTTPServer):
    """The loopback server with room for a page's opening burst of requests.

    ``socketserver`` listens with a queue of 5 by default. When a page opens and asks for a dozen
    routes at once, the operating system resets the connections past the fifth, and the page says
    that the engine did not answer. A queue of 128 holds the burst, and nothing else changes.
    """

    request_queue_size = 128


class DashboardServer:
    """A running server, and the address it actually bound.

    Port 0 means the operating system picks, which is what a tool launched from a terminal
    should do: two projects open at once must not fight over a number.
    """

    def __init__(
        self,
        project: Path | None,
        *,
        port: int = 0,
        config_home: Path | None = None,
        warm_search: bool = True,
    ) -> None:
        # A server with no project is still a server. ``ingence see`` with none found serves the
        # landing page, and the server is bound to a project, once, when a person opens or makes
        # one there (:meth:`bind`). A server never changes the project it reads. Another project
        # opened from its landing page gets a server of its own, beside it.
        self._project = project
        self._warm_search = warm_search
        self.config_home = config_home
        self.applier: DefinitionApplier | None = (
            DefinitionApplier(project) if project is not None else None
        )
        self._binding_guard = threading.Lock()
        #: The servers this one started for other projects opened from its landing page.
        self._beside: dict[str, DashboardServer] = {}
        self._started = False
        handler: Callable[..., BaseHTTPRequestHandler] = partial(DashboardHandler, binding=self)
        self._http = _DashboardHTTPServer((HOST, port), handler)
        self._thread: threading.Thread | None = None
        self._stopped = False
        #: The thread that reads the Record and warms the pages as the server starts, and the
        #: catalog it is reading while it does. :meth:`stop` ends both.
        self._background: threading.Thread | None = None
        self._warming: Any = None
        #: The thread that warms a run's readouts as the server starts. It is kept apart from
        #: ``_warming``, which holds the search warm's catalog for :meth:`stop`.
        self._readouts: threading.Thread | None = None
        #: What stops the readouts warm between two views when the server stops.
        self._readouts_token = CancellationToken()

    @property
    def url(self) -> str:
        """The address actually bound, which is the only one worth printing.

        ``server_address`` is typed for every address family the base class supports,
        including a Unix socket path, so the host is narrowed rather than formatted blind: a
        bytes host would render as ``b'127.0.0.1'`` and the printed link would not work.
        """
        address = self._http.server_address
        host, port = address[0], address[1]
        spelled = host.decode("ascii") if isinstance(host, bytes) else str(host)
        return f"http://{spelled}:{port}/"

    @property
    def project(self) -> Path | None:
        """The project this server reads, or None where the landing page has opened none yet."""
        return self._project

    def bind(self, project: Path) -> str:
        """Read ``project`` from now on, where this server reads none yet, and return where to go.

        One server reads one project. A server with no project is bound to the first one a person
        opens or makes on its landing page, and its address then serves that project (``/``). A
        server already reading a project never changes it: another project gets a server of its
        own, started beside this one on a port the system picks, and its address is returned for
        the page to go to. Opening the project already read returns this server's own address.
        """
        resolved = project.expanduser().resolve()
        with self._binding_guard:
            if self._project is None:
                self._project = resolved
                self.applier = DefinitionApplier(resolved)
                if self._started:
                    self._begin_project()
                return "/"
            if self._project.resolve() == resolved:
                return "/"
            beside = self._beside.get(str(resolved))
            if beside is None:
                beside = DashboardServer(
                    resolved, config_home=self.config_home, warm_search=self._warm_search
                )
                beside.start()
                self._beside[str(resolved)] = beside
            return beside.url

    def server_for(self, project: Path) -> DashboardServer | None:
        """Return the server that reads ``project``.

        That is this server, or the one started beside it for the project.
        """
        resolved = project.expanduser().resolve()
        with self._binding_guard:
            if self._project is not None and self._project.resolve() == resolved:
                return self
            return self._beside.get(str(resolved))

    @property
    def beside(self) -> list[DashboardServer]:
        """Return the servers started beside this one for other projects."""
        return list(self._beside.values())

    def start(self) -> None:
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()
        self._started = True
        if self._project is not None:
            self._begin_project()
            # A run with no readouts is warmed on a thread of its own. A run made by the command
            # line, or by an earlier build, has its views built while /api/applying says that the
            # new scan is not ready yet.
            self._readouts = threading.Thread(
                target=self._warm_readouts, daemon=True, name="readouts-warm"
            )
            self._readouts.start()

    def _warm_readouts(self) -> None:
        """Warm the pinned run's readouts where it has none.

        The method never warms while a job is queued or running, because the job's own views phase
        builds them and the pin may move. It never warms a run that is not the one that reads are
        pinned to.
        """
        project = self._project
        if project is None or self.applier is None or self._stopped:
            return
        from ingence.readouts import warm

        with contextlib.suppress(Exception):
            pinned, busy = self.applier.reading()
            if busy:
                return
            from ingence.storage.commit import read_pointer

            pointer = read_pointer(project)
            run_id = pinned or (pointer.run_id if pointer is not None else None)
            if run_id is not None and not self._stopped:
                warm(project, run_id, token=self._readouts_token)

    def _begin_project(self) -> None:
        """Supervise the project's jobs, and read its Record while the first page loads."""
        assert self._project is not None
        assert self.applier is not None
        # The server supervises jobs from the start. It finds what a server that died left
        # running, says so (``applier.reconciled``), and queues it again.
        self.applier.start()
        # The Record is read once while the first page loads. Every page asks what is decided,
        # and a process holds the Record after its first read (``record/log.read_events``), so
        # reading it now spares the first page that wait. The tabs' counts begin as the Record
        # is read, and the page is told so.
        starting = _starting(self._project)
        self._background = threading.Thread(
            target=self._read_the_record, args=(starting,), daemon=True, name="record-read"
        )
        self._background.start()

    def _read_the_record(self, starting: threading.Event) -> None:
        project = self._project
        assert project is not None  # the server starts only once a project is bound
        try:
            # A Record that cannot be read now is reported by the page that reads it next.
            with contextlib.suppress(Exception):
                record_log.read_events(project)
            # And begin the tabs' counts, on a thread of their own. Every page asks for them
            # first, and on a large census they take the longest. The first page is told that
            # they are being counted and is not left waiting for them in silence. Once begun, a
            # request finds the count by the Record it reads, which is held now.
            if not self._stopped:
                with contextlib.suppress(Exception), open_catalog(project) as catalog:
                    # Stopped while the catalog was opening: begin no count.
                    if not self._stopped:
                        run_id = catalog.run_directory.name if catalog.run_directory else ""
                        _counting(project, run_id, _waiting_key(project, run_id))
        finally:
            _started(project, starting)
        # And the search is made ready. The Dictionary and the tokenizer are read into memory.
        # In a project where a search index is kept, the run's index is brought up to date, so
        # that the first search does not wait (seconds on a large census). Opening the Dashboard
        # does not write to a project that was never searched.
        if self._warm_search and not self._stopped:
            with contextlib.suppress(Exception), open_catalog(project) as catalog:
                self._warming = catalog
                try:
                    search_routes.warm(catalog, project)
                finally:
                    self._warming = None

    def stop(self) -> None:
        """Stop serving and close the port. Stopping a stopped server does nothing."""
        if self._stopped:
            return
        self._stopped = True
        self._http.shutdown()
        self._http.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        # The servers started beside this one for other projects stop with it.
        for beside in self.beside:
            beside.stop()
        if self._project is None or self.applier is None:
            return
        # Nothing of its own is left working. The tabs' count and the search's warm are
        # interrupted and waited for, so no thread of this server is inside DuckDB when the
        # process exits. Otherwise the process ends with an abort.
        warming = self._warming
        if warming is not None:
            with contextlib.suppress(Exception):
                warming.connection.interrupt()
        stop_counting(self._project)
        if self._background is not None:
            self._background.join(BACKGROUND_STOP_SECONDS)
            # The starting read may have begun a count after the stop above looked. Once it has
            # ended, no more can begin, so a second stop ends whatever it began.
            stop_counting(self._project)
        # The readouts' warm is stopped between two views and waited for, within the same bound.
        # A server stopped while it writes would leave it writing, and a process that ends while
        # it is inside DuckDB aborts.
        self._readouts_token.cancel("the server stopped")
        if self._readouts is not None:
            self._readouts.join(BACKGROUND_STOP_SECONDS)
        # The Groupings page's rows held for it let go.
        page_routes.forget_held(self._project)
        # A job still running is stopped and queued for the next start, and never lost.
        self.applier.stop()

    def __enter__(self) -> DashboardServer:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def _ctrl_c_in_a_query(error: BaseException) -> bool:
    """Return whether an error is DuckDB's report of a Ctrl-C that arrived during a query.

    DuckDB 1.5 reports it as ``RuntimeError('Query interrupted')``. A DuckDB build that defines
    ``InterruptException`` raises that instead.
    """
    import duckdb

    interrupt = getattr(duckdb, "InterruptException", None)
    if interrupt is not None and isinstance(error, interrupt):
        return True
    return isinstance(error, RuntimeError) and str(error).strip() == "Query interrupted"


def say_what_waits(project: Path, *, say: Callable[[str], None] = print) -> str | None:
    """Say in one line what waits for Re-run, and start nothing.

    Nothing runs by itself when the Dashboard opens, because a long start without alerting the user
    is not acceptable. The page shows the same line once, with Re-run now and Later. Nothing found
    here stops the Dashboard opening. A checkpoint that cannot be read is named in one line and
    left out, and anything else that goes wrong is said in one line.
    """
    try:
        unreadable = workspace_state.unreadable_line(project)
        if unreadable is not None:
            say(unreadable)
        line = workspace_state.waiting_line(project)
    except Exception as error:  # the Dashboard opens whatever this finds
        if _ctrl_c_in_a_query(error):
            # A Ctrl-C pressed while the check reads the catalog reaches here as DuckDB's own
            # error and not as KeyboardInterrupt. So the code raises it as the close that it is. If
            # the error were said as a line and dropped, the server would keep serving and the close
            # would wait forever.
            raise KeyboardInterrupt from error
        say(
            "Could not check what waits for Re-run "
            f"({type(error).__name__}: {error}); the Dashboard opens anyway."
        )
        return None
    if line is not None:
        say(line)
    return line


#: What this process has said already, so that a reason is told once and not at every opening.
_SAID: set[str] = set()


def _say_once(message: str) -> None:
    """Print ``message`` to the person running the server, the first time only."""
    if message not in _SAID:
        _SAID.add(message)
        print(message)


def serve(
    project: Path | None,
    *,
    port: int = 0,
    open_browser: bool = True,
) -> None:
    """Serve one project until interrupted, or serve the landing page where ``project`` is None.

    The server always has somewhere to land. With no project, the page opens one or makes one.
    With a project that has not been read yet, the page asks what it needs and reads the project on
    a person's click. A project opened here is put first on this machine's list of projects
    (``api/recent.py``).
    """
    from ingence.api.recent import RecentNotKeptError, remember_project
    from ingence.storage.commit import read_pointer

    server = DashboardServer(project, port=port)
    if project is not None:
        try:
            remember_project(project, opened_at=record_log.now())
        except RecentNotKeptError as not_kept:
            _say_once(str(not_kept))
        except OSError:
            pass
    server.start()
    # Ctrl-C stops the server, however it was started. A process started in the background of a
    # script inherits SIGINT ignored, and Python then installs no handler. The interrupt that is
    # the one way to stop the server would do nothing, and the server would serve on forever. The
    # default handler is put back for the session, and what was there before is put back after it.
    before = _ctrl_c_raises()
    # The address is flushed. When output is piped or redirected, it waits in a buffer, and
    # nothing would appear while the server runs, which is when a person needs the address
    # (``ingence see``). Everything after the server starts is inside the one ``try``. Ctrl-C
    # pressed while the session opens is a close like any other, and never a traceback past the
    # question.
    try:
        if project is None:
            print(f"Ingence at {server.url}", flush=True)
            print(
                "No project is open: the page opens one, or makes one. Ctrl-C to stop.", flush=True
            )
        elif read_pointer(project) is None:
            print(f"Ingence at {server.url}", flush=True)
            print(
                "This project has not been read yet: the page says what it will read, and reads "
                "it when you say. Ctrl-C to stop.",
                flush=True,
            )
        else:
            print(f"Dashboard at {server.url}", flush=True)
            print("Ingence never changes your files. Press Ctrl-C to stop.", flush=True)
        if server.applier is not None:
            # What a Dashboard that stopped left unfinished, found when this one started.
            for line in server.applier.reconciled:
                print(line)
        if project is not None:
            say_what_waits(project)
        if open_browser:
            webbrowser.open(server.url)
        _until_ctrl_c()
    except KeyboardInterrupt:
        # Serving stops before the question. Nothing is written from the page while it is asked,
        # and a question that nobody answers cannot keep the port open.
        server.stop()
        # Every close asks. There is no remembered answer and no "always". Where there is no
        # terminal to answer in, nothing is asked, the drafts stay, and one line says so. The
        # project that the landing page opened is asked about too. A project opened beside it
        # keeps its drafts for its next opening.
        read = server.project
        if read is not None:
            found = decider_file.read_decider()
            ask_before_closing(
                read,
                actor=found.name if found is not None else "",
                interactive=_answers_can_be_typed(),
            )
    finally:
        server.stop()
        if before is not None:
            signal.signal(signal.SIGINT, before)


def _ctrl_c_raises() -> Any:
    """Make SIGINT raise KeyboardInterrupt in this thread, and return the handler it had.

    The result is None where nothing was changed. A thread other than the main one cannot set a
    handler.
    """
    if threading.current_thread() is not threading.main_thread():
        return None
    before = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    return before


def _until_ctrl_c() -> None:
    """Wait for Ctrl-C, the one way to stop ``ingence see``. It arrives as KeyboardInterrupt."""
    while True:
        threading.Event().wait(3600)


def _answers_can_be_typed() -> bool:
    """Return whether a person can answer a question here: the input is a terminal.

    The input is not a pipe, and it is not missing. Without this check, a server that another
    program started, with a pipe that stays open and silent, would wait forever for an answer that
    nobody can give, and keep serving.
    """
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        return False
