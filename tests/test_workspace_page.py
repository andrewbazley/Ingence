"""The header's workspace controls, read from the page's own files (the browser is not needed).

What is checked: the Re-run button is always in the header and never hidden, what waits is a
count on it and never a dialog, a draft is saved inside a second, and nothing about closing is
remembered.
"""

from __future__ import annotations

import json
import re

from ingence.api.server import WEB_ROOT

PAGE = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
SCRIPT = (WEB_ROOT / "workspace.js").read_text(encoding="utf-8")
CATALOG = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))


def test_the_rerun_button_is_always_in_the_header_and_never_hidden_by_the_page() -> None:
    slot = re.search(r'<span id="workspace-slot">(.*?)</span>', PAGE, re.DOTALL)
    assert slot is not None
    assert 'id="commit-button"' in slot.group(1)
    button = re.search(r'<button id="rerun-button"[^>]*>', slot.group(1))
    assert button is not None
    assert "hidden" not in button.group(0)
    # Disabled where nothing waits, with hover text either way, and nothing that hides it.
    # Enabled too where a re-run a close stopped waits for a person, and its hover then says what
    # it starts.
    assert "button.disabled = (!waiting && !rerunStopped) || lastRunning" in SCRIPT
    assert "Starts the Re-run that stopped when the Dashboard last closed." in SCRIPT
    assert "button.hidden" not in SCRIPT
    assert "Nothing is waiting for a Re-run" in SCRIPT


def test_rerun_s_words_fit_answers_as_well_as_commits() -> None:
    """A person's answers wait for Re-run too, so its words never say that only committed changes
    were applied, or that only commits wait."""
    from ingence.api.server import WEB_ROOT as ROOT

    app = (ROOT / "app.js").read_text(encoding="utf-8")
    assert "Committed changes" not in SCRIPT and "Committed changes" not in app
    assert "Nothing committed" not in SCRIPT


def test_what_waits_is_a_badge_and_a_notice_and_never_a_dialog() -> None:
    """A count on Re-run opens a summary by kind, and one notice appears when a session opens.
    There is no alert line."""
    slot = re.search(r'<span id="workspace-slot">(.*?)</span>', PAGE, re.DOTALL)
    assert slot is not None
    order = slot.group(1)
    assert order.index('id="rerun-button"') < order.index('id="rerun-badge"')
    assert re.search(r'<div id="rerun-notice"[^>]*hidden', PAGE)
    assert re.search(r'<div id="rerun-summary"[^>]*hidden', PAGE)
    assert 'id="rerun-alert"' not in PAGE and "waiting.alert" not in SCRIPT
    assert "window.alert" not in SCRIPT
    # The commit dialog is the one dialog here, opened by a click on Commit and nowhere else
    # (one of dialogs.js's, labelled, focus kept inside, Escape cancels).
    assert "showModal" not in SCRIPT
    assert SCRIPT.count("dialogs.open(") == 1
    assert "button.addEventListener('click', () => openCommitDialog()" in SCRIPT
    # Later is kept in the browser until something else waits, and the notice is said once per
    # page.
    assert "window.localStorage" in SCRIPT and "waitingMark(" in SCRIPT
    assert "noticeAsked" in SCRIPT
    for key in ("workspace.waiting", "workspace.notice"):
        assert key in CATALOG["entries"], key


def test_a_draft_is_saved_inside_a_second() -> None:
    delay = re.search(r"const DRAFT_DELAY_MS = (\d+);", SCRIPT)
    assert delay is not None
    assert 0 < int(delay.group(1)) < 1000


def test_a_save_sends_the_held_edits_and_never_an_empty_draft() -> None:
    """A save does not read the Settings table, which may be gone, and send ``{}``.

    A save sends what the input handler held, is skipped where nothing is held, and every held
    edit is saved when the view changes and when the page closes."""
    save = SCRIPT[SCRIPT.index("async function saveDrafts") :]
    save = save[: save.index("\n}\n")]
    assert "querySelectorAll" not in save.split("await write(")[0]
    assert "if (!edits.size && !putBack.size) return;" in save
    # app.js's one navigation function runs the workspace's hook on leaving a page, so this file
    # does not wrap showView.
    assert "showView =" not in SCRIPT
    assert "onLeave(() => saveEveryDraftNow());" in SCRIPT
    assert "addEventListener('pagehide'" in SCRIPT
    assert "keepalive: true" in SCRIPT


def test_nothing_about_closing_is_remembered_in_the_page() -> None:
    assert "window.sessionStorage" not in SCRIPT
    # The one thing the page keeps in the browser is the opening notice's Later (in localStorage).
    uses = [line for line in SCRIPT.splitlines() if "window.localStorage" in line]
    assert uses
    assert all("LATER_KEY" in line for line in uses)


def test_the_settings_section_joins_the_engine_page_and_the_script_loads_after_app() -> None:
    assert 'data-view="settings"' in PAGE
    assert PAGE.index('src="/app.js"') < PAGE.index('src="/workspace.js"')
    # The Settings section is in settings.js, which adds it to the Engine page and loads after
    # workspace.js, whose drafts it uses.
    settings = (WEB_ROOT / "settings.js").read_text(encoding="utf-8")
    assert "PAGES.engine.push('settings')" in settings
    assert PAGE.index('src="/workspace.js"') < PAGE.index('src="/settings.js"')


def test_the_new_controls_have_words_in_the_help_catalog() -> None:
    for key in ("workspace.commit", "workspace.rerun", "section.settings"):
        assert CATALOG["entries"][key]["text"].strip()


def test_the_commit_dialog_saves_what_is_held_first_and_commits_once_per_click() -> None:
    """An edit not yet saved is included in the commit, and a second click while the first commit
    runs cannot start another."""
    dialog = SCRIPT[SCRIPT.index("async function openCommitDialog") :]
    dialog = dialog[: dialog.index("\n}\n")]
    assert dialog.index("await saveEveryDraftNow();") < dialog.index("commit-preview")
    assert "commitButton.disabled = true;" in dialog
    assert dialog.index("commitButton.disabled = true;") < dialog.index("/api/workspace/commit'")
