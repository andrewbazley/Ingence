"""The Dashboard's first slice: what it serves, and what it refuses to.

The tests that matter here are not that a route returns 200 but that the two things the
Dashboard promises are structurally true: it holds no truth, and it never leaves the machine.
Both are cheap to assert and expensive to notice the loss of.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from ingence.api.routes.review import DEFAULT_ORDER
from ingence.api.server import (
    ANSWER_ROUTE,
    DECIDER_ROUTE,
    HOST,
    LANDING_ROUTES,
    WEB_ROOT,
    WORKSPACE_ROUTES,
    WRITE_ROUTES,
    DashboardServer,
)
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    """A tiny indexed project, inferred, behind a running server."""
    base = tmp_path_factory.mktemp("dashboard")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "kelt_vorn_processed.csv").write_text("a,b\n3,4\n")
    (pool / "kelt" / "brisa_notes.txt").write_text("hello")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    server = DashboardServer(project.path, port=0)
    server.start()
    yield server, project
    server.stop()


def fetch(server: DashboardServer, path: str):
    with urllib.request.urlopen(server.url.rstrip("/") + path) as response:
        return json.loads(response.read())


def test_it_binds_the_loopback_interface_and_nothing_else(served) -> None:
    """A read-only window onto somebody's filesystem has no business on a public interface.

    ``HOST`` is a constant rather than a default a caller can override, and this asserts that
    what the server actually bound is that constant.
    """
    server, _ = served
    assert server.url.startswith(f"http://{HOST}:")


def test_the_page_and_its_assets_fetch_nothing_from_anywhere(served) -> None:
    """The Dashboard runs only on the user's machine, and this test checks it, not trusts it.

    The Dashboard must render on a machine that has never had a network. A CDN link, a web
    font or an absolute URL in any asset would break that silently -- the page would simply
    look wrong offline -- so no asset may name a scheme at all. The two addresses a person opens by
    clicking (the footer's documentation link and the reporting guide in "Report a problem") are
    navigation, let through only where ``test_web_vendor`` allows them.
    """
    from test_web_vendor import without_the_links_a_person_opens

    for asset in sorted(WEB_ROOT.glob("*")):
        if asset.suffix not in {".html", ".css", ".js"}:
            continue
        text = without_the_links_a_person_opens(asset.read_text(encoding="utf-8"))
        for scheme in ("http://", "https://", "//cdn", "@import url("):
            assert scheme not in text, f"{asset.name} reaches out with {scheme!r}"


def test_it_serves_what_the_engine_already_concluded_and_computes_nothing(served) -> None:
    """The Lens holds no truth: every number the page shows is already in the store."""
    server, project = served
    payload = fetch(server, "/api/project")
    with open_catalog(project.path) as catalog:
        entries = catalog.query("SELECT count(*) FROM entries", []).fetchone()[0]
        claims = catalog.query("SELECT count(*) FROM claims", []).fetchone()[0]
    assert payload["counts"]["entries"] == entries
    assert payload["counts"]["claims"] == claims
    # The run being read, named for a person, its identifier kept beside it.
    assert payload["run"]["run_id"] == "run_20260101T000002Z_222222222222"
    assert payload["run"]["number"] == 2
    assert payload["run"]["label"].startswith("Scan 2, ")


def test_a_folder_view_carries_the_class_key_each_grouping_rule_computed(served) -> None:
    """The key is what shows why two files in one folder are not in one class."""
    server, project = served
    with open_catalog(project.path) as catalog:
        folder_id = catalog.query(
            "SELECT entry_id FROM entries WHERE entry_type = 'directory' "
            "AND parent_entry_id IS NOT NULL LIMIT 1",
            [],
        ).fetchone()[0]
    payload = fetch(server, f"/api/folder/{folder_id}")
    assert payload["folder"]["entry_id"] == folder_id
    assert payload["entries"], "the folder has children and the view returned none"
    for entry in payload["entries"]:
        assert set(entry) >= {"name", "claims", "classes", "components", "bindings"}
        for record in entry["classes"].values():
            assert "key" in record


def test_an_entry_view_shows_what_no_rule_could_decide_beside_what_they_did(served) -> None:
    """A tool that displays only what it knows feels authoritative and is untrustworthy."""
    server, project = served
    with open_catalog(project.path) as catalog:
        entry_id = catalog.query(
            "SELECT entry_id FROM entries WHERE entry_type = 'file' LIMIT 1", []
        ).fetchone()[0]
    payload = fetch(server, f"/api/entry/{entry_id}")
    assert payload["entry"]["entry_id"] == entry_id
    for key in ("claims", "failures", "findings", "components", "bindings"):
        assert key in payload, f"{key} is part of the shape of what is known"


def test_a_view_that_does_not_exist_says_so_rather_than_failing_oddly(served) -> None:
    server, _ = served
    with pytest.raises(urllib.error.HTTPError) as raised:
        fetch(server, "/api/nothing-here")
    raised.value.close()
    assert raised.value.code == 404


def test_the_folders_with_acquisitions_list_is_gone_and_one_folder_stays(served) -> None:
    """The list of folders holding an acquisition has no page and no route.

    The list was specific to microscopy. One folder's view stays, for the graph of what files held
    a thing that replaces it."""
    server, _ = served
    with pytest.raises(urllib.error.HTTPError) as raised:
        fetch(server, "/api/folders")
    raised.value.close()
    assert raised.value.code == 404
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    assert 'data-view="folders"' not in page


def test_no_asset_escapes_the_web_root(served) -> None:
    """A path that climbs out of the asset directory is refused, not served."""
    server, _ = served
    for path in ("/../../pyproject.toml", "/..%2f..%2fpyproject.toml"):
        with (
            pytest.raises(urllib.error.HTTPError) as raised,
            urllib.request.urlopen(server.url.rstrip("/") + path) as response,
        ):
            response.read()
        raised.value.close()
        assert raised.value.code == 404


def _post(server: DashboardServer, route: str, headers: dict[str, str], body: bytes = b"{}") -> int:
    request = urllib.request.Request(
        server.url.rstrip("/") + route, method="POST", data=body, headers=headers
    )
    try:
        with urllib.request.urlopen(request) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        error.close()
        return error.code


def test_the_record_has_one_way_in_and_every_write_route_is_named_and_guarded(served) -> None:
    """The Record has exactly one way in, and every route that writes anything is named.

    Every such route is behind the same guard. The Record is one attributable, append-only path
    for decisions. The workspace's writes are not decisions. A draft is a person's unsent edit, a
    commit writes files a person could write with an editor plus a checkpoint, and a re-run
    starts a job the applier already runs. So the claim has three parts. The Record's way in is
    one route. Every route that accepts a POST is named in ``WRITE_ROUTES`` and refuses one
    without the ``X-Ingence-Write`` header, from a foreign ``Origin``, or form-encoded. Every
    other route refuses the verb. The failure to prevent is a server that grows a write nobody
    named.

    What the answer route refuses, and the guard's own reasoning, is ``tests/test_decide.py``'s.
    """
    server, _ = served
    assert ANSWER_ROUTE == "/api/review/answer"
    assert (ANSWER_ROUTE, DECIDER_ROUTE, *WORKSPACE_ROUTES, *LANDING_ROUTES) == WRITE_ROUTES
    json_header = {"Content-Type": "application/json"}
    for route in WRITE_ROUTES:
        # No X-Ingence-Write header, which a cross-site form cannot set.
        assert _post(server, route, json_header) == 403, route
        # A foreign page's origin, everything else right.
        foreign = {**json_header, "X-Ingence-Write": "1", "Origin": "http://elsewhere.invalid"}
        assert _post(server, route, foreign) == 403, route
        # A form-encoded body, which is what a cross-site post looks like.
        form = {"Content-Type": "application/x-www-form-urlencoded", "X-Ingence-Write": "1"}
        assert _post(server, route, form) == 403, route
    # The workspace writes never reach the Record: the Record's way in is record.log.append_event,
    # and none of the workspace's code names it. Two exceptions go through that same writer: the
    # one withdrawal of a restore, only from workspace/restore.py, and the withdrawal of one
    # answer about a word, from workspace/withdraw.py.
    root = Path(WEB_ROOT).parent
    for path in (*(root / "workspace").glob("*.py"), root / "api" / "routes" / "workspace.py"):
        if path.name in ("restore.py", "withdraw.py") and path.parent.name == "workspace":
            continue
        assert "append_event" not in path.read_text("utf-8"), path.name
    assert "/api/workspace/restore" in WRITE_ROUTES
    # The withdrawal path is as narrow as the restore's: it writes a withdrawal and nothing else,
    # only the answer route calls it, and only for a person the request names.
    withdrawing = (root / "workspace" / "withdraw.py").read_text("utf-8")
    assert set(re.findall(r"RecordEventKind\.(\w+)", withdrawing)) == {"WITHDRAW"}
    callers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path.name != "withdraw.py" and "withdraw_answer(" in path.read_text("utf-8")
    )
    assert callers == ["api/server.py"]
    server_text = (root / "api" / "server.py").read_text("utf-8")
    method = server_text[server_text.index("def _withdraw(") :]
    assert "actor = self._actor(body)" in method.split("def _decide_one(")[0]
    # Through the guarded answer route, a withdrawal that names nobody is refused, and one that
    # names an answer the Record does not hold writes nothing.
    json_write = {"Content-Type": "application/json", "X-Ingence-Write": "1"}
    anonymous = json.dumps({"withdraw": {"sequence": 1, "digest": "x"}}).encode()
    assert _post(server, ANSWER_ROUTE, json_write, anonymous) == 400
    unknown = json.dumps(
        {"actor": "Sam Okafor", "withdraw": {"sequence": 999999, "digest": "x"}}
    ).encode()
    assert _post(server, ANSWER_ROUTE, json_write, unknown) in (400, 409)


def _request_naming(
    server: DashboardServer, host: str | None, method: str = "GET", route: str = "/api/project"
) -> int:
    """Send one request whose ``Host`` header says ``host`` (or none at all), return its status."""
    import http.client

    parsed = urllib.parse.urlparse(server.url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.putrequest(method, route, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            connection.putheader("Host", host)
        if method == "POST":
            for name, value in (
                ("Content-Type", "application/json"),
                ("X-Ingence-Write", "1"),
                ("Content-Length", "2"),
            ):
                connection.putheader(name, value)
        connection.endheaders(b"{}" if method == "POST" else None)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def test_a_request_made_to_another_name_is_refused_before_it_reads(served) -> None:
    """A page on another site can make its own name resolve to this machine after it has loaded
    (DNS rebinding); the browser then treats this server's answers as that page's, and the
    origin check on writes does not stop a read. Its ``Host`` header still carries the other
    name, so a request naming anything but this server is refused, reads and writes alike, and
    one naming this server (or none, as an HTTP/1.0 client) is served."""
    server, _ = served
    port = urllib.parse.urlparse(server.url).port
    for own in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"LOCALHOST:{port}"):
        assert _request_naming(server, own) == 200, own
    assert _request_naming(server, None) == 200
    for foreign in (
        "elsewhere.invalid",
        f"elsewhere.invalid:{port}",
        f"127.0.0.1:{port + 1}",
        "127.0.0.1",
        f"localhost.elsewhere.invalid:{port}",
    ):
        assert _request_naming(server, foreign) == 403, foreign
        assert _request_naming(server, foreign, route="/") == 403, foreign
        assert _request_naming(server, foreign, method="POST", route=ANSWER_ROUTE) == 403, foreign


def test_every_other_route_refuses_a_post(served) -> None:
    """The third part of the rule: what is not named in ``WRITE_ROUTES`` refuses the verb."""
    server, _ = served
    # The Questions pages read through their own routes and write through the one.
    for route in (
        "/api/project",
        "/api/inventory",
        "/api/review",
        "/api/record",
        "/api/rules",
        "/api/waiting",
        "/api/pages/questions",
        "/api/pages/classes",
        "/api/pages/claims",
        "/api/pages/candidates",
        "/api/pages/extensions",
        "/api/word",
        "/api/programs",
        "/api/runs",
        "/api/timeline",
        "/api/changes",
        "/api/workspace/state",
        "/api/workspace/settings",
        "/api/workspace/commit-preview",
    ):
        request = urllib.request.Request(server.url.rstrip("/") + route, method="POST", data=b"{}")
        request.add_header("Content-Type", "application/json")
        with (
            pytest.raises(urllib.error.HTTPError) as raised,
            urllib.request.urlopen(request) as response,
        ):
            response.read()
        raised.value.close()
        assert raised.value.code in (404, 405, 501), route


def test_the_review_pages_read_on_a_project_with_no_findings(served) -> None:
    """Every Questions page answers on a project with no findings."""
    server, _ = served
    for kind in ("questions", "classes", "claims", "candidates", "extensions"):
        page = fetch(server, f"/api/pages/{kind}")
        assert page["kind"] == kind
        assert set(page["progress"]) == {"rows", "answered", "remaining"}
    assert fetch(server, "/api/programs")["programs"] == []
    assert [run["inference"] for run in fetch(server, "/api/runs")["runs"]] == [False, True]


def test_the_assets_travel_with_the_installed_engine() -> None:
    """``WEB_ROOT`` resolves the same from a checkout and from site-packages."""
    assert WEB_ROOT.is_dir()
    assert (WEB_ROOT / "__init__.py").is_file(), "a package, so a wheel carries it"
    for name in ("index.html", "app.css", "app.js"):
        assert (WEB_ROOT / name).is_file(), name


def test_the_asset_directory_is_declared_as_package_data() -> None:
    """A wheel that drops the assets serves a blank page, and the failure is silent."""
    manifest = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    for asset in sorted(WEB_ROOT.glob("*")):
        if asset.suffix in {".html", ".css", ".js"}:
            declared = f"src/ingence/web/*{asset.suffix}"
            assert declared in manifest, f"the wheel would drop {asset.name}; declare {declared}"


def test_the_review_route_serves_the_ranked_queue(served) -> None:
    """``/api/review`` serves the ranked queue.

    The fixture's pool raises nothing, so this holds the shape rather than the content: the
    declared dimensions travel with the page, so a reader can see what ranked it.
    """
    server, _ = served
    page = fetch(server, "/api/review")
    assert [entry["name"] for entry in page["dimensions"]] == list(DEFAULT_ORDER)
    assert page["order"] == list(DEFAULT_ORDER)
    # The totals count the questions set aside beside the ones asked.
    assert set(page["totals"]) == {"questions", "findings", "set_aside"}


def test_the_review_route_takes_the_order_from_the_request(served) -> None:
    """Tuning the ranking is a request, not a re-run and not a release."""
    server, _ = served
    page = fetch(server, "/api/review?order=senses_offered,entries_affected")
    assert page["order"] == ["senses_offered", "entries_affected"]


def test_the_review_route_reports_a_dimension_it_cannot_compute(served) -> None:
    """A refusal reaches the page as a refusal, rather than an order silently ignored."""
    server, _ = served
    with (
        pytest.raises(urllib.error.HTTPError) as raised,
        urllib.request.urlopen(server.url.rstrip("/") + "/api/review?order=urgency") as response,
    ):
        response.read()
    body = json.loads(raised.value.read())
    raised.value.close()
    assert raised.value.code == 500
    assert "UnknownDimensionError" in body["error"]


def test_the_coverage_view_counts_what_is_unread_and_why(served) -> None:
    """The coverage view as a page: every figure is a count over the run's own tables."""
    server, _ = served
    data = fetch(server, "/api/coverage")
    reached = data["reached"]
    assert reached["files"] == 3 and reached["opened"] == 0
    assert sum(row["files"] for row in reached["file_types"]) == 3
    # No dive has run, so every file is unopened and none has a clock from its content.
    assert data["clocks"] == {"no clock, and no dive has opened it": 3}
    assert {"left_out", "folders"} <= set(data["set_aside"])


def test_an_extension_s_worth_reading_answer_is_written_through_the_one_route(
    tmp_path: Path,
) -> None:
    """The coverage view's one control writes the same event as an imported answer about an
    extension."""
    from ingence.record.verdicts import worth_reading

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt_trace.zorbex").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    server = DashboardServer(project.path, port=0)
    server.start()
    try:

        def post(body: dict[str, object]) -> int:
            request = urllib.request.Request(
                server.url.rstrip("/") + ANSWER_ROUTE,
                method="POST",
                data=json.dumps(body).encode(),
            )
            request.add_header("Content-Type", "application/json")
            request.add_header("X-Ingence-Write", "1")
            try:
                with urllib.request.urlopen(request) as response:
                    return int(response.status)
            except urllib.error.HTTPError as error:
                error.close()
                return int(error.code)

        assert post({"actor": "kelt", "extension": ".ZORBEX", "worth_reading": "never"}) == 201
        assert worth_reading(project.path) == {"zorbex": "never"}
        # Answered, it leaves the list, and comes back when asked for.
        waiting = fetch(server, "/api/coverage")
        assert waiting["unbound_extensions"] == []
        assert waiting["unbound_extensions_answered"] == 1
        rows = fetch(server, "/api/coverage?answered=show")["unbound_extensions"]
        assert rows == [{"extension": "zorbex", "files": 1, "opened": 0, "worth_reading": "never"}]
        # A word the table does not hold, and an extension no file carries, are refused.
        assert post({"actor": "kelt", "extension": "zorbex", "worth_reading": "maybe"}) == 400
        assert post({"actor": "kelt", "extension": "absent", "worth_reading": "yes"}) == 400
    finally:
        server.stop()
