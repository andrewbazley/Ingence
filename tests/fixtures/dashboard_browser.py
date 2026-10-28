"""What the release browser tests share: a Dashboard started on an invented project, a browser, and
a reader for the pixels of a screenshot.

The release tests (``test_browser_release_*.py``) read what a person sees. They all need these
three things, so they share them here. Every name in the project is invented
(``fixtures.theme_pool``). Nothing here touches the person's own settings. The folder that holds
who decides is a folder the caller names.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

REPOSITORY = Path(__file__).resolve().parents[2]
#: Set in CI's browser job, as the repository's other browser tests read it. In that job a missing
#: extra (the browser, or the themes the invented project needs), a browser that will not start, or
#: a missing axe-core is a failure, so the job cannot pass having tested nothing. Elsewhere each of
#: these is a skip.
REQUIRED = os.environ.get("INGENCE_BROWSER_REQUIRED") == "1"


def need(module: str, reason: str) -> Any:
    """Import ``module``, or skip the test where it is missing.

    Where ``REQUIRED`` is set, a missing module fails the test instead. The function is safe to
    call at the top of a test file and inside a fixture.
    """
    try:
        return importlib.import_module(module)
    except ImportError:
        if REQUIRED:
            pytest.fail(f"{reason} (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
        pytest.skip(reason, allow_module_level=True)


#: The three engines Playwright drives. ``INGENCE_BROWSER`` names the one the browser tests use.
BROWSERS = ("chromium", "firefox", "webkit")


def browser_name() -> str:
    """The browser the tests drive: ``INGENCE_BROWSER`` (``chromium``, ``firefox`` or ``webkit``).

    The default is ``chromium`` when the variable is not set. A name that is none of the three
    fails the test that asked, so a misspelling cannot pass by testing the wrong browser.
    """
    named = (os.environ.get("INGENCE_BROWSER") or "chromium").strip().lower()
    if named not in BROWSERS:
        pytest.fail(
            f"INGENCE_BROWSER is {named!r}: it must be one of {', '.join(BROWSERS)}", pytrace=False
        )
    return named


def _patient(browser: Any) -> Any:
    """The browser, with every page and context it makes waiting ``HANG_GUARD_MS`` by default,
    carrying the probe ``settled`` reads, and closing without a route handler's late error.

    Playwright's own default is thirty seconds a wait, which a page on a busy machine can exceed
    while nothing is wrong: Dashboard reads of 5 to 14 s were seen on a quiet machine. Every wait
    still returns the moment its state is reached. The longer default only moves the point at which
    a page that never gets there is called hung. A wait given a timeout of its own keeps it.
    """
    if not (callable(getattr(browser, "new_page", None)) and callable(browser.new_context)):
        return browser
    new_page, new_context = browser.new_page, browser.new_context

    def tidy(made: Any) -> Any:
        """Set the default wait, and let a route handler end quietly when a page or context closes.

        A route handler may still be working at that moment. If it is left alone, its "request
        context disposed" error surfaces in the next test's first call to Playwright and fails that
        test. This was seen in a survey at a high load.
        """
        made.set_default_timeout(HANG_GUARD_MS)
        made.add_init_script(_QUIET_PROBE)
        close = made.close

        def close_quietly(*args: Any, **options: Any) -> Any:
            with contextlib.suppress(Exception):
                made.unroute_all(behavior="ignoreErrors")
            return close(*args, **options)

        made.close = close_quietly
        return made

    def page(*args: Any, **options: Any) -> Any:
        return tidy(new_page(*args, **options))

    def context(*args: Any, **options: Any) -> Any:
        return tidy(new_context(*args, **options))

    browser.new_page = page
    browser.new_context = context
    return browser


def launch_browser(playwright: Any) -> Any:
    """The browser ``INGENCE_BROWSER`` names, as Playwright installs it.

    For Chromium, if that fails, the machine's Chrome is used. The test is skipped where no browser
    starts, and fails where ``REQUIRED`` is set. Every browser test starts its browser here, and
    none names an engine itself. To add Firefox and WebKit, run
    ``python -m playwright install firefox webkit`` and set ``INGENCE_BROWSER=firefox`` (or
    ``webkit``).
    """
    name = browser_name()
    engine = getattr(playwright, name)
    for channel in (None, "chrome") if name == "chromium" else (None,):
        try:
            return _patient(engine.launch(channel=channel) if channel else engine.launch())
        except Exception:  # no browser of that kind here
            continue
    said = f"Playwright could start no {name}: run `python -m playwright install {name}`"
    if REQUIRED:
        pytest.fail(said, pytrace=False)
    pytest.skip(said)


#: How long a wait for a state gives up. This is a guard against a hang. It is not the time the
#: page is expected to take, because every wait returns the moment its state is reached.
HANG_GUARD_MS = 120_000

#: Two frames painted: what the page drew is on the screen.
_PAINTED = "new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"

#: Added to every page the tests open. It counts the requests the page has in flight and when it
#: last made or finished one, which ``settled`` reads. A request is finished once its body has been
#: read (a copy of each response is read to its end), and not when its headers arrive. Playwright's
#: own "network idle" does not help after a page has loaded. It is a lifecycle event that fires
#: once per document, so a wait for it returns at once however much the page asks for since.
#: The probe does not see requests that a Web Worker makes (the Explorer lays out its Wheel in
#: one), nor timers and other work that has not yet asked for anything. ``settled`` covers a timer
#: only by waiting out a quiet window after it is called.
_QUIET_PROBE = """
(() => {
  if (window.__ingenceQuiet) return;
  const quiet = {inflight: 0, last: performance.now()};
  window.__ingenceQuiet = quiet;
  const seen = (done) => { quiet.inflight += done ? -1 : 1; quiet.last = performance.now(); };
  const real = window.fetch;
  window.fetch = function (...args) {
    seen(false);
    return real.apply(this, args).then(
      (answer) => {
        let copy = null;
        try { copy = answer.clone(); } catch (error) { copy = null; }
        if (copy === null || copy.body === null) seen(true);
        else copy.arrayBuffer().then(() => seen(true), () => seen(true));
        return answer;
      },
      (error) => { seen(true); throw error; },
    );
  };
})();
"""


def settled(
    page: Any, quiet_ms: float = 600, timeout: float = HANG_GUARD_MS, *, from_now: bool = True
) -> None:
    """Wait until the page asks for nothing more and has painted what it was given.

    The page has settled when no request it made is still waiting (its body read to the end), none
    was made or finished for ``quiet_ms``, and two frames have been painted since. This state
    replaces a fixed pause. With ``from_now`` (the default) the quiet window starts when this is
    called, so the function still catches something that a timer sends a moment later. A check that
    nothing is sent needs that. ``from_now=False`` counts the window from the page's last request,
    which returns sooner on a page that has long been quiet. The function returns as soon as the
    page has settled. ``timeout`` only bounds a page that never settles. Work in a Web Worker is not
    seen (see ``_QUIET_PROBE``). A page opened without the probe falls back to Playwright's
    "network idle". A page that the test did not get from ``launch_browser`` has no probe.
    """
    if from_now:
        page.evaluate("if (window.__ingenceQuiet) window.__ingenceQuiet.last = performance.now()")
    page.wait_for_function(
        "(quiet) => { const seen = window.__ingenceQuiet; return !seen"
        " || (seen.inflight === 0 && performance.now() - seen.last >= quiet); }",
        arg=quiet_ms,
        timeout=timeout,
    )
    if not page.evaluate("Boolean(window.__ingenceQuiet)"):
        page.wait_for_load_state("networkidle", timeout=timeout)
    page.evaluate(_PAINTED)


def free_port() -> int:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        return int(held.getsockname()[1])


def wait_for(url: str, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as answer:
                if answer.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    message = f"{url} did not answer in {seconds} s"
    raise AssertionError(message)


def start_dashboard(project: Path | None, config_home: Path) -> tuple[subprocess.Popen[bytes], str]:
    """Run ``ingence see`` on ``project``, or, for ``None``, on the landing page with no project.

    The command runs from this checkout's code, with who decides kept in ``config_home``. The
    function returns the process and the page's address. The caller stops the process.
    """
    port = free_port()
    environment = {
        **os.environ,
        "INGENCE_CONFIG_HOME": str(config_home),
        "PYTHONPATH": os.pathsep.join(
            [str(REPOSITORY / "src"), str(REPOSITORY / "tests"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    if project is None:
        code = (
            "import sys, time\n"
            "from ingence.api.server import DashboardServer\n"
            "server = DashboardServer(None, port=int(sys.argv[1]))\n"
            "server.start()\n"
            "print('serving', flush=True)\n"
            "while True:\n"
            "    time.sleep(60)\n"
        )
        command = [sys.executable, "-c", code, str(port)]
    else:
        command = [
            sys.executable,
            "-m",
            "ingence.cli.main",
            "see",
            "-p",
            str(project),
            "--port",
            str(port),
            "--no-open",
        ]
    process = subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}/"
    try:
        wait_for(f"{base}landing" if project is None else f"{base}api/v1/version")
    except AssertionError:
        stop(process)
        raise
    return process, base


def serve_theme_project(
    tmp_path_factory: pytest.TempPathFactory, name: str, *, waiting: bool = False
) -> Iterator[str]:
    """A Dashboard on a copy of the invented theme project, with nobody yet set as deciding.

    The project is ``fixtures.theme_pool``: three invented people, six planted lines of work and
    dated experiment folders. Who decides is kept in a folder of the test's own. The function
    yields the page's address and stops the server after. A module's fixture is
    ``yield from serve_theme_project(tmp_path_factory, "name")``.

    With ``waiting``, one answer that the next run reads is written first, so a change waits for
    Re-run and the badge shows. Without it, nothing waits unless a person answered something,
    because every domain loads by default and no pack waits to be accepted.
    """
    from fixtures.theme_pool import build_theme_project

    base = tmp_path_factory.mktemp(name)
    project, _ = build_theme_project(base / "built")
    copy = base / "project"
    shutil.copytree(project, copy)
    if waiting:
        from ingence.core.schema import RecordEventKind
        from ingence.record.log import append_event, now
        from ingence.storage.commit import read_pointer

        pointer = read_pointer(copy)
        assert pointer is not None
        append_event(
            copy,
            kind=RecordEventKind.CONFIRM,
            actor="kipper",
            occurred_at=now(),
            target="group:keltan vornish",
            against_run_id=pointer.run_id,
        )
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        yield address
    finally:
        stop(process)


def stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()


def png_rows(data: bytes) -> tuple[int, int, int, list[bytes]]:
    """The pixels of a PNG as rows of bytes: ``(width, height, bytes per pixel, rows)``.

    Only what a browser's screenshot is: eight bits a channel, red-green-blue with or without
    alpha, not interlaced. Written here because a screenshot is the one thing a person sees and
    the standard library decodes nothing else.
    """
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    position = 8
    header: tuple[int, ...] = ()
    chunks: list[bytes] = []
    while position < len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        kind = data[position + 4 : position + 8]
        body = data[position + 8 : position + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            chunks.append(body)
        position += 12 + length
    width, height, depth, color, _compression, _filter, interlace = header
    assert depth == 8 and color in (2, 6) and interlace == 0, "an unexpected PNG layout"
    step = 3 if color == 2 else 4
    raw = zlib.decompress(b"".join(chunks))
    stride = width * step
    rows: list[bytes] = []
    previous = bytearray(stride)
    for index in range(height):
        start = index * (stride + 1)
        mode = raw[start]
        line = bytearray(raw[start + 1 : start + 1 + stride])
        if mode == 1:
            for at in range(step, stride):
                line[at] = (line[at] + line[at - step]) & 255
        elif mode == 2:
            for at in range(stride):
                line[at] = (line[at] + previous[at]) & 255
        elif mode == 3:
            for at in range(stride):
                left = line[at - step] if at >= step else 0
                line[at] = (line[at] + ((left + previous[at]) >> 1)) & 255
        elif mode == 4:
            for at in range(stride):
                left = line[at - step] if at >= step else 0
                up = previous[at]
                corner = previous[at - step] if at >= step else 0
                estimate = left + up - corner
                near_left, near_up, near_corner = (
                    abs(estimate - left),
                    abs(estimate - up),
                    abs(estimate - corner),
                )
                if near_left <= near_up and near_left <= near_corner:
                    chosen = left
                elif near_up <= near_corner:
                    chosen = up
                else:
                    chosen = corner
                line[at] = (line[at] + chosen) & 255
        previous = line
        rows.append(bytes(line))
    return width, height, step, rows


def coloured_share(png: bytes, spread: int = 40) -> float:
    """The share of a screenshot's pixels that carry a color.

    A pixel carries a color when its red, green and blue values differ by more than ``spread``.
    Grey text and a white page do not count. A drawn arc in the Dashboard's blues does.
    """
    width, height, step, rows = png_rows(png)
    coloured = 0
    for row in rows:
        for at in range(0, width * step, step):
            channels = row[at : at + 3]
            if max(channels) - min(channels) > spread:
                coloured += 1
    return coloured / (width * height)
