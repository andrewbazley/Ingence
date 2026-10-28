"""The browser lanes name no engine of their own.

Every browser test starts its browser with ``fixtures.dashboard_browser.launch_browser``, which
reads ``INGENCE_BROWSER`` (``chromium``, ``firefox`` or ``webkit``; Chromium when it is not set).
So adding Firefox and WebKit is one install command and that one variable (``CONTRIBUTING.md``
says so). This file checks the two halves: the launcher asks the engine the variable names, and no
test file launches an engine in a place of its own, which would leave it in Chromium whatever the
variable says.

It needs no browser and no Playwright, so it runs in the ordinary suite.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from fixtures import dashboard_browser
from fixtures.dashboard_browser import BROWSERS, browser_name, launch_browser

TESTS = Path(__file__).resolve().parent

#: A call that starts an engine by its name, in the form Playwright's documentation writes it.
_NAMES_AN_ENGINE = re.compile(r"\.(?:chromium|firefox|webkit)\s*\.\s*launch\w*\s*\(")


class _Engine:
    """Stands in for one of Playwright's browser types and records how it was started."""

    def __init__(self, name: str, fails_for: tuple[str | None, ...] = ()) -> None:
        self.name = name
        self.started: list[str | None] = []
        self._fails_for = fails_for

    def launch(self, channel: str | None = None) -> Any:
        self.started.append(channel)
        if channel in self._fails_for:
            message = f"no {self.name} here"
            raise RuntimeError(message)
        return f"{self.name}:{channel}"


class _Playwright:
    def __init__(self, **engines: _Engine) -> None:
        self.chromium = engines.get("chromium", _Engine("chromium"))
        self.firefox = engines.get("firefox", _Engine("firefox"))
        self.webkit = engines.get("webkit", _Engine("webkit"))


def test_the_default_browser_is_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGENCE_BROWSER", raising=False)
    assert browser_name() == "chromium"
    monkeypatch.setenv("INGENCE_BROWSER", "")
    assert browser_name() == "chromium"


@pytest.mark.parametrize("named", BROWSERS)
def test_the_variable_names_the_browser(named: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGENCE_BROWSER", f" {named.upper()} ")
    assert browser_name() == named


def test_a_word_that_is_no_browser_fails_rather_than_testing_the_wrong_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGENCE_BROWSER", "firefx")
    with pytest.raises(pytest.fail.Exception, match="firefx"):
        browser_name()
    with pytest.raises(pytest.fail.Exception, match="firefx"):
        launch_browser(_Playwright())


@pytest.mark.parametrize("named", BROWSERS)
def test_the_launcher_starts_the_engine_the_variable_names_and_no_other(
    named: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INGENCE_BROWSER", named)
    playwright = _Playwright()
    assert launch_browser(playwright) == f"{named}:None"
    for engine in (playwright.chromium, playwright.firefox, playwright.webkit):
        assert engine.started == ([None] if engine.name == named else [])


def test_chromium_falls_back_to_the_machines_chrome_and_the_others_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGENCE_BROWSER", "chromium")
    chromium = _Engine("chromium", fails_for=(None,))
    assert launch_browser(_Playwright(chromium=chromium)) == "chromium:chrome"
    assert chromium.started == [None, "chrome"]

    monkeypatch.setenv("INGENCE_BROWSER", "firefox")
    firefox = _Engine("firefox", fails_for=(None,))
    monkeypatch.setattr(dashboard_browser, "REQUIRED", False)
    with pytest.raises(pytest.skip.Exception, match="playwright install firefox"):
        launch_browser(_Playwright(firefox=firefox))
    assert firefox.started == [None]


def test_a_browser_that_will_not_start_fails_where_one_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGENCE_BROWSER", "webkit")
    monkeypatch.setattr(dashboard_browser, "REQUIRED", True)
    with pytest.raises(pytest.fail.Exception, match="playwright install webkit"):
        launch_browser(_Playwright(webkit=_Engine("webkit", fails_for=(None,))))


class _Made:
    def __init__(self) -> None:
        self.timeouts: list[float] = []
        self.said: list[str] = []

    def set_default_timeout(self, milliseconds: float) -> None:
        self.timeouts.append(milliseconds)

    def add_init_script(self, script: str) -> None:
        self.said.append("init script")

    def unroute_all(self, behavior: str = "default") -> None:
        self.said.append(f"unroute_all {behavior}")

    def close(self) -> None:
        self.said.append("close")


class _Browser:
    def new_page(self, **options: Any) -> _Made:
        return _Made()

    def new_context(self, **options: Any) -> _Made:
        return _Made()


def test_every_page_and_context_waits_the_hang_guard_and_closes_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGENCE_BROWSER", "chromium")
    browser = _Browser()
    engine = _Engine("chromium")
    engine.launch = lambda channel=None: browser  # type: ignore[method-assign]
    launched = launch_browser(_Playwright(chromium=engine))
    assert launched is browser
    for made in (launched.new_page(viewport={"width": 1}), launched.new_context()):
        assert made.timeouts == [dashboard_browser.HANG_GUARD_MS]
        # A route handler still working at the close ends quietly, and before the close.
        made.close()
        assert made.said == ["init script", "unroute_all ignoreErrors", "close"]


def test_no_browser_test_starts_an_engine_in_a_place_of_its_own() -> None:
    own: list[str] = []
    for path in sorted(TESTS.glob("test_browser*.py")):
        if path == Path(__file__).resolve():
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if _NAMES_AN_ENGINE.search(line):
                own.append(f"{path.name}:{number}")
    assert own == [], (
        "these start an engine by name, so INGENCE_BROWSER cannot change it; "
        "call fixtures.dashboard_browser.launch_browser(playwright) instead: " + ", ".join(own)
    )


def test_every_file_that_drives_a_browser_starts_it_through_the_launcher() -> None:
    elsewhere: list[str] = []
    seen = 0
    for path in sorted(TESTS.glob("test_browser*.py")):
        text = path.read_text()
        if "sync_playwright" not in text:
            continue
        seen += 1
        if "launch_browser(" not in text:
            elsewhere.append(path.name)
    assert seen > 20, "the scan found the browser test files"
    assert elsewhere == [], "these drive a browser without launch_browser: " + ", ".join(elsewhere)


def test_the_guide_names_the_variable_and_the_install_step() -> None:
    guide = (TESTS.parent / "CONTRIBUTING.md").read_text()
    assert "INGENCE_BROWSER=firefox" in guide
    assert "playwright install firefox webkit" in guide
