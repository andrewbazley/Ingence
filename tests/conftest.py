"""Shared test configuration.

Puts the tests directory on the path so ``fixtures.graphs`` imports as a plain
module rather than needing the package to be installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

#: The website is built from a separate source. A test that reads a page, a style guide or another
#: file of that source carries ``@pytest.mark.needs_website`` (or sets it as ``pytestmark``).
#: Collection skips it where the website's source folder is absent.
WEBSITE_SOURCE = Path(__file__).resolve().parent.parent / "site" / "conf.py"


def pytest_configure(config: pytest.Config) -> None:
    """Declare the ``needs_website`` marker."""
    config.addinivalue_line(
        "markers", "needs_website: reads the website's source, which only some checkouts hold"
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip every ``needs_website`` test where the website's source is absent."""
    if WEBSITE_SOURCE.is_file():
        return
    skip = pytest.mark.skip(reason="the website's source is not in this repository")
    for item in items:
        if "needs_website" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _configuration_kept_apart(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the person's configuration directory at a temporary one, for every test.

    Who decides on a machine is kept in the person's own configuration directory
    (``api/decider.py``). No test may read or write the real one: a test that
    set a name there would attribute the next real decision on this machine to it.
    """
    from ingence.api.decider import CONFIG_HOME_VARIABLE

    directory = tmp_path_factory.mktemp("configuration")
    monkeypatch.setenv(CONFIG_HOME_VARIABLE, str(directory))
    return directory


def pytest_sessionfinish(session: object, exitstatus: object) -> None:
    """Name any thread still running when the suite finishes.

    A thread that outlives its test can hang the whole run. A supervised extraction runs on a
    background thread in one test. On Windows a kill may not reach its child, so the thread
    never returns from ``recv``. As a non-daemon thread it then holds ``threading._shutdown``
    forever. Every test would already have reported, some of them failing, but none of it is
    readable because a job's log is not served until the job ends.

    This turns such a thread into a line of output. It cannot prevent the hang, because a
    non-daemon thread still holds shutdown, but it names the thread first.
    """
    import threading

    survivors = [
        thread
        for thread in threading.enumerate()
        if thread is not threading.main_thread() and thread.is_alive()
    ]
    if not survivors:
        return
    print("\nthreads still running at session finish:")
    for thread in survivors:
        kind = "daemon" if thread.daemon else "NON-DAEMON, will hold shutdown"
        print(f"  {thread.name}: {kind}")


@pytest.fixture(scope="session")
def explorer_project(tmp_path_factory: pytest.TempPathFactory) -> object:
    """The Graph workspace's invented fixture, built once for the whole test session.

    Building it runs a census, a dive and an inference; each module that reads it takes it from
    here, and a test that writes works on a copy.
    """
    from fixtures.explorer_project import build_explorer_project

    return build_explorer_project(tmp_path_factory.mktemp("explorer"))
