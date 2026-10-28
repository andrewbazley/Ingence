"""The Dashboard consolidated, read from the page's own files.

One accessible dialog module replaces the browser's own prompt, alert and confirm boxes. One
evidence inspector serves the pages. One helper handles loading, failure and emptiness. The
object under investigation is kept in the address and the session through one navigation
function, and workspace.js does not wrap showView. What a browser would show is driven in
tests/test_browser.py. This file reads the files the server serves.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ingence.api.server import WEB_ROOT

REPOSITORY = Path(__file__).resolve().parents[1]
PAGE = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
APP = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
REVIEW = (WEB_ROOT / "review.js").read_text(encoding="utf-8")
WORKSPACE = (WEB_ROOT / "workspace.js").read_text(encoding="utf-8")
DIALOGS = (WEB_ROOT / "dialogs.js").read_text(encoding="utf-8")
INSPECTOR = (WEB_ROOT / "inspector.js").read_text(encoding="utf-8")
GRAPH = (WEB_ROOT / "graph.js").read_text(encoding="utf-8")
CATALOG = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))


def _ours() -> list[Path]:
    """Every script and page the Dashboard ships that is its own (the vendored library is not)."""
    return sorted(
        path
        for path in WEB_ROOT.rglob("*")
        if path.suffix in {".js", ".html"} and "vendor" not in path.relative_to(WEB_ROOT).parts
    )


_BROWSER_BOXES = re.compile(r"window\.prompt\(|window\.alert\(|\bconfirm\(")


def test_no_browser_prompt_alert_or_confirm_remains() -> None:
    found = {
        str(path.relative_to(WEB_ROOT)): hits
        for path in _ours()
        if (hits := _BROWSER_BOXES.findall(path.read_text("utf-8")))
    }
    assert found == {}


def test_every_dialog_is_labelled_keeps_focus_closes_on_escape_and_checks_first() -> None:
    assert "showModal()" in DIALOGS
    assert "'aria-labelledby'" in DIALOGS
    assert "'aria-describedby'" in DIALOGS
    # Escape is the dialog's cancel event, answered as Cancel.
    assert "addEventListener('cancel'" in DIALOGS
    # Tab and Shift+Tab go round the dialog's own controls; focus goes back where it was.
    assert "event.key !== 'Tab'" in DIALOGS
    assert "before.focus()" in DIALOGS
    # An answer is checked before it is accepted, and the problem said beside the field.
    ask = DIALOGS[DIALOGS.index("function ask(") :]
    ask = ask[: ask.index("\n  }\n")]
    assert "validate" in ask
    assert "role: 'alert'" in ask
    assert "'aria-invalid'" in ask


def test_the_scripts_load_in_the_order_each_needs() -> None:
    order = re.findall(r'<script src="/([\w/.]+)"></script>', PAGE)
    assert order.index("dialogs.js") < order.index("review.js") < order.index("app.js")
    assert order.index("app.js") < order.index("inspector.js") < order.index("graph.js")
    assert order.index("app.js") < order.index("workspace.js")


def test_every_page_s_regions_are_present() -> None:
    for region in (
        '<nav id="pages"',
        # The header has no count line (id="summary"). The Overview says the counts.
        'id="decider"',
        'id="focus"',
        'id="applying"',
        # The badge, its summary and the opening notice replace an alert line.
        'id="rerun-badge"',
        'id="rerun-summary"',
        'id="rerun-notice"',
        '<section id="list"',
        '<section id="detail"',
        'id="commit-button"',
        'id="rerun-button"',
        # Import and Export sit beside them, and their meaning is the page's.
        'id="import-button"',
        'id="export-button"',
    ):
        assert region in PAGE, region
    pages = re.findall(r'<button data-page="(\w+)"', PAGE)
    # Six sections. Advanced, last, holds the Engine group.
    assert pages == ["overview", "overlap", "experiments", "checks", "history", "advanced"]
    for part in ("tools", "canvas", "list", "inspector"):
        assert f'data-graph="{part}"' in PAGE, part


def test_one_navigation_function_and_nothing_patched() -> None:
    assert "function navigate(" in APP
    # showView remains as a name for the scripts that call it, and does nothing but navigate.
    assert re.search(r"function showView\(view\) \{\s*navigate\(view\);\s*\}", APP)
    for script in (WORKSPACE, REVIEW, INSPECTOR, GRAPH):
        assert not re.search(r"^\s*(showView|refreshWaiting|navigate)\s*=", script, re.MULTILINE)
    assert "onLeave(() => saveEveryDraftNow());" in WORKSPACE
    assert "onRefresh(" in WORKSPACE


def test_the_object_under_investigation_is_kept_in_the_address_and_the_session() -> None:
    assert "const FOCUS_KINDS = ['entry', 'question', 'run', 'word'];" in APP
    assert "sessionStorage.setItem(FOCUS_KEY" in APP
    # Each page keeps its own, the address naming what the page opened is looking at.
    assert "focusByPage = focusFromSession(" in APP
    assert "const fromAddress = focusFromAddress();" in APP
    # The Graph page's own centre names an entry, both ways.
    assert "query.get('center')" in APP
    assert "query.set('center', focusHeld.id)" in APP
    # Selecting on each page sets it: the inspector (every file or folder opened, on every page),
    # Questions, History, a word.
    assert "setFocus({kind: 'entry'" in INSPECTOR
    assert "setFocus({kind: 'question'" in REVIEW
    assert "setFocus({kind: 'run'" in APP
    assert "setFocus({kind: 'word'" in INSPECTOR


def test_the_shell_s_own_requests_never_hold_the_page_back() -> None:
    start = APP[APP.index("document.addEventListener('DOMContentLoaded', function start()") :]
    # The page is drawn before the tabs' waiting counts are read. The header has no count line, so
    # nothing else is read for the header.
    assert start.index("navigate(") < start.index("refreshWaiting()")
    assert "await refreshWaiting()" not in start
    assert "showCounts" not in APP and 'id="summary"' not in PAGE


def test_one_helper_says_loading_failure_and_emptiness_everywhere() -> None:
    assert "const states = {" in APP
    assert "states.failed(listPane, error" in APP  # reportError, with a way to try again
    for script in (APP, REVIEW):
        # The old one-off loading lines are gone.
        assert not re.search(r"text: '(reading|searching|comparing)[^']*\.\.\.'", script)
    for view in ("showInventory", "showCoverage"):
        body = APP[APP.index(f"async function {view}(") :]
        assert "states.loading(listPane" in body[: body.index("\n}\n")], view
    # The Rules section is in rules.js, not app.js.
    rules = (WEB_ROOT / "rules.js").read_text(encoding="utf-8")
    body = rules[rules.index("async function showRules(") :]
    assert "states.loading(listPane" in body[: body.index("\n}\n")]
    assert "showRules" not in APP
    for view in ("showSheet", "showPrograms", "showChanges"):
        body = REVIEW[REVIEW.index(f"async function {view}(") :]
        assert "states.loading(listPane" in body[: body.index("\n}\n")], view


def test_one_inspector_serves_explore_graph_questions_and_history() -> None:
    assert INSPECTOR.startswith("// @ts-check")
    tsconfig = json.loads((REPOSITORY / "tsconfig.json").read_text(encoding="utf-8"))
    assert "src/ingence/web/inspector.js" in tsconfig["files"]
    # The Graph page keeps its own panel, and graph.js does not call the inspector. The inspector
    # renders the same entry and line for the other pages.
    assert "async function showEdge(" in INSPECTOR
    assert "function edgeBlocks(" in INSPECTOR
    assert "window.inspector" not in GRAPH
    # What a page looks at opens in it. An assumption's examples open in it (the Overview and
    # Checks). Questions opens a question's files in it. History shows a run's restore preview
    # there.
    assert "window.inspector.showEntry(focusHeld.id, pane" in APP
    overview = (WEB_ROOT / "overview.js").read_text(encoding="utf-8")
    assert "global.inspector.showEntry(example.entry_id" in overview
    assert "window.inspector.filesOf(row.subject_files" in REVIEW
    assert "showRunInspector(run.run_id, detailPane, short(run))" in REVIEW
    # Every entry shows each rule's version beside what it concluded.
    assert "` version ${claim.rule_version} (" in INSPECTOR


def test_every_new_control_has_words_in_the_help_catalog() -> None:
    keys: set[str] = set()
    for script in (APP, REVIEW, WORKSPACE, INSPECTOR, DIALOGS):
        keys |= set(re.findall(r"'data-help': '([\w.]+)'", script))
    assert keys <= set(CATALOG["entries"]), keys - set(CATALOG["entries"])


def test_proposed_groups_are_paged_fifty_at_a_time_and_every_row_counted() -> None:
    assert "const PAGE_SIZE = 50;" in REVIEW
    assert "new URLSearchParams({limit: String(PAGE_SIZE), offset: String(sheetState.offset)})" in (
        REVIEW
    )
    assert "data.listed" in REVIEW


def test_restore_writes_only_the_preview_the_person_saw() -> None:
    body = APP[APP.index("async function showRunInspector(") :]
    body = body[: body.index("\n}\n")]
    assert "/api/v1/restore/preview?run=" in body
    assert "previewed: preview.digest" in body
    assert body.index("dialogs.choose(") < body.index("'/api/workspace/restore'")


def test_the_years_before_a_run_are_written_as_a_settings_draft() -> None:
    control = WORKSPACE[WORKSPACE.index("function yearsControl(") :]
    control = control[: control.index("\n}\n")]
    assert "'/api/workspace/draft', {kind: 'settings'" in control
    assert "values: {dictionary: {years: [start, end]}}" in control
    assert "'present'" in control
    # Shown only where the engine serving the page has the setting.
    assert "row.name === 'dictionary.years'" in WORKSPACE


def test_the_question_file_is_exported_and_imported_under_the_author_a_person_names() -> None:
    # The header's Import and Export serve every Questions section (transfer.js), in place of the
    # Questions page's own disclosure. Import asks who wrote the file.
    transfer = (WEB_ROOT / "transfer.js").read_text(encoding="utf-8")
    assert "/api/v1/questions/pack" in transfer
    assert "api('/api/v1/authors')" in transfer
    # Written through the one answer route, stating who wrote it.
    body = transfer[transfer.index("async function importAnswers(") :]
    assert "write('/api/review/answer', {actor: me.actor, import: {kind," in body
    assert "agent_generated: agent, reviewed: agent" in body
    # An agent's file is written only after the person confirms that they reviewed every answer.
    assert body.index("dialogs.choose(") < body.index("write('/api/review/answer'")


def test_the_banner_shows_the_ledger_s_states_and_history_lists_the_jobs() -> None:
    assert "api('/api/v1/jobs?limit=20')" in APP
    for state in ("queued", "running", "done", "failed", "interrupted"):
        assert f"{state}:" in APP[APP.index("const JOB_STATE_WORDS") :][:300], state
    assert "jobsSection()" in REVIEW


def test_a_proposal_is_accepted_or_refused_on_its_own_target() -> None:
    row = REVIEW[REVIEW.index("if (sheetState.kind === 'questions' && row.proposal) {") :]
    row = row[: row.index("} else if")]
    assert "const answered = {...row, target: proposal.target};" in row
    assert "wordButtons(proposal.words" in row
    assert "send(answered," in row


def test_group_resolutions_are_shown_as_defined_and_a_core_is_approved() -> None:
    section = REVIEW[REVIEW.index("function resolutionsSection()") :]
    section = section[: section.index("\n}\n")]
    assert "api('/api/v1/groups/resolutions')" in section
    for part in ("core.within", "core.across", "core.deviations", "approved_by"):
        assert part in section, part
    # A deviation waiting opens its own question.
    assert "navigate('review', {focus: {kind: 'question', id: deviation.question" in section
    approve = REVIEW[REVIEW.index("function approveCore(") :]
    approve = approve[: approve.index("\n}\n")]
    assert "target: `group:${kept.join(' ')}`, word: 'accept'" in approve
    assert "write('/api/review/answer'" in approve
    assert "kept.length < 2" in approve


def test_a_folder_finding_s_subject_opens_as_its_entry() -> None:
    """A folder's finding names its subject ``folder:<entry_id>``.

    The inspector must not ask the run for an entry of that name."""
    files = INSPECTOR[INSPECTOR.index("function filesOf(") :]
    assert ".replace(/^folder:/, '')" in files[: files.index("\n  }\n")]


def test_the_correction_dialog_names_what_it_replaces_and_what_it_keeps() -> None:
    """The dialog does not say "nothing is overwritten" whatever it replaced."""
    form = INSPECTOR[INSPECTOR.index("function correctionForm(") :]
    form = form[: form.index("\n  }\n")]
    assert "nothing is overwritten" not in form
    assert "It replaces the meaning" in form
    assert "It replaces the answer given to the question" in form
    assert "shown.corrections.find(" in form


def test_every_writer_on_a_proposal_s_row_writes_on_the_proposal_s_target() -> None:
    """The bulk bar and the note write on a proposal's own target, not the question's.

    Writing on the question's target would leave the proposal's row waiting."""
    # The bar's items are the rows' targets. A proposal is answered on its own row and names
    # none, so the bar never chooses it.
    assert "targets: row.proposal ? [] : targetsOf(row)" in REVIEW
    row = REVIEW[REVIEW.index("async function showRow(") :]
    row = row[: row.index("\n}\n")]
    assert "const writesTo = row.proposal ? {...row, target: row.proposal.target} : row;" in row
    assert "send(writesTo, {note: note.value}" in row


def test_a_rule_parameter_s_draft_path_splits_at_the_last_dot() -> None:
    """A rule identifier may hold a dot (census.directory_aggregate).

    The draft's path must not be split at every dot, which would nest the rule's name."""
    keys = WORKSPACE[WORKSPACE.index("function draftKeys(") :]
    keys = keys[: keys.index("\n}\n")]
    assert "kind === 'rule_parameters'" in keys
    assert "lastIndexOf('.')" in keys
    body = WORKSPACE[WORKSPACE.index("function draftBody(") :]
    body = body[: body.index("\n}\n")]
    assert "setPath(values, draftKeys(kind, path), value)" in body
    assert "removed: [...putBack].map((path) => draftKeys(kind, path))" in body


def test_the_years_control_has_its_own_ids_in_each_place_and_a_commit_waits_for_its_save() -> None:
    """The control on Settings and the control in the Commit dialog have different ids.

    With the same ids, a change inside the dialog could race the commit."""
    control = WORKSPACE[WORKSPACE.index("function yearsControl(") :]
    control = control[: control.index("\n}\n")]
    assert "function yearsControl(row, place)" in control
    assert "`${place}-years-from`" in control
    assert "yearsSaving = " in control
    # The Settings section is settings.js's.
    assert "yearsControl(years, 'settings')" in (WEB_ROOT / "settings.js").read_text("utf-8")
    assert "yearsControl(years, 'commit')" in WORKSPACE
    dialog = WORKSPACE[WORKSPACE.index("async function openCommitDialog()") :]
    dialog = dialog[: dialog.index("\n}\n")]
    assert dialog.index("await yearsSaving;") < dialog.index("'/api/workspace/commit'")
