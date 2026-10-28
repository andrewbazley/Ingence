/* The workspace's controls: autosaved drafts, the Commit button, the Re-run button and the badge
 * counting what waits for it. The Settings section of the Engine page belongs to settings.js.
 *
 * Loaded after app.js, whose api(), write(), el() and whoWrites() it uses.
 *
 * - Drafts. Any input carrying data-draft="<kind>:<dotted.path>" is saved 600 ms after the
 *   last keystroke, always inside the second a person expects (DRAFT_DELAY_MS). A draft changes
 *   nothing the engine reads: the field says "draft, saved" and the Commit button counts it. A
 *   value the engine would refuse comes back with its error, shown under the field. The typing is
 *   kept, and only a commit refuses it. Each edit is held in `unsavedEdits` the moment it is
 *   typed, apart from the page. A save sends what is held and never what the page happens to
 *   show, so a timer that fires after the Settings table has gone still sends the edit, and a
 *   save with nothing to send is never made. Every held edit is saved at once when the view
 *   changes and when the page closes. A value put back as it was is sent as `removed`, the only
 *   way anything leaves a draft. The server adds every other save to the draft.
 * - Commit opens a small dialog: a message (today's date by default), what the commit will need
 *   run, and Commit. The server lays the values into settings.dashboard.yaml and appends a
 *   checkpoint, and settings.yaml is never written. Every held edit is saved before the dialog
 *   opens, and its Commit button is disabled while the commit runs.
 * - Re-run is always in the header. It is disabled only when nothing waits, and its hover text
 *   says what it will run. Nothing runs by itself, and nothing interrupts. Every change since
 *   the last run (answers, words defined or turned off, packs, committed settings) is one list.
 *   The badge on Re-run counts it and opens a summary, a line per kind, each line opening its
 *   rows. A session that opens where changes wait says so once, with Re-run now and Later. Later
 *   holds in this browser until something else waits.
 * - Closing. A browser tab cannot show its own dialog when it closes. The beforeunload event
 *   offers only the browser's generic question, which is asked whenever something is uncommitted
 *   and cannot be turned off. The real question is asked every time and remembered never. It
 *   lives where the program closes: `ingence see` stopped with Ctrl-C.
 *
 * Writes go through write() with the X-Ingence-Write header, like every write on this page. */

const DRAFT_DELAY_MS = 600;

const pendingDrafts = new Map();
/* Per kind: each edited value not yet saved, by dotted path, and each path put back as it was. */
const unsavedEdits = new Map();
const putBackEdits = new Map();
let uncommittedTotal = 0;
let workspaceApplying = null;

function heldFor(store, kind, make) {
  if (!store.has(kind)) store.set(kind, make());
  return store.get(kind);
}

/* A value's place in its draft, as keys. A setting's dotted name is its keys (scan.workers);
 * a rule parameter's is the rule and the parameter, split at the last dot only, because a rule's
 * identifier may itself hold a dot (census.directory_aggregate) and a parameter's never does. */
function draftKeys(kind, path) {
  if (kind === 'rule_parameters') {
    const at = path.lastIndexOf('.');
    return at > 0 ? [path.slice(0, at), path.slice(at + 1)] : [path];
  }
  return path.split('.');
}

function setPath(target, keys, value) {
  let node = target;
  for (const part of keys.slice(0, -1)) node = node[part] = node[part] || {};
  node[keys[keys.length - 1]] = value;
}

/* What an input holds, as the draft stores it. A list is one item per line; a mapping is one
 * ``key = value`` per line (the Rules page's parameters). A setting's list is a comma list, a
 * whole number in it read as a number where the setting's list holds numbers, and a choice's
 * option is its value written as JSON (a yes, a no, "none" and words are all choices of one
 * setting). */
function draftValue(input) {
  if (input.type === 'checkbox') return input.checked;
  if (input.type === 'number') return input.value === '' ? null : Number(input.value);
  if (input.dataset.draftType === 'json') return JSON.parse(input.value);
  if (input.dataset.draftType === 'comma') {
    const numbers = input.dataset.listOf !== 'text';
    return input.value.split(',').map((item) => item.trim()).filter(Boolean)
      .map((item) => (numbers && /^-?\d+$/.test(item) ? Number(item) : item));
  }
  if (input.dataset.draftType === 'list') {
    return input.value.split('\n').map((line) => line.trim()).filter(Boolean);
  }
  if (input.dataset.draftType === 'mapping') {
    const mapping = {};
    for (const line of input.value.split('\n').map((item) => item.trim()).filter(Boolean)) {
      const at = line.indexOf('=');
      if (at > 0) mapping[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
    return mapping;
  }
  return input.value;
}

/* One spelling of a value for comparing it with what is in force: a list or a mapping as JSON
 * with its keys in order, anything else as its text. */
function canonicalValue(value) {
  if (value !== null && typeof value === 'object') {
    if (Array.isArray(value)) return JSON.stringify(value);
    return JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : 1))));
  }
  return String(value);
}

/* The Rulebook's refusal without the part that names its own bookkeeping ("project revision 2:
 * override of 'a_rule':"), which the field it stands under already says. */
function plainRefusal(text) {
  return String(text || '').replace(/project revision \d+: override of '[^']*': /g, '');
}

/* Whether an input's value is one it can hold: the browser's own checks (a number's bounds and
 * step, a required choice) and a mapping's lines. Said under the field at once; the draft is
 * still saved, with the typing, and only a commit refuses it. */
function inputProblem(input) {
  if (input.dataset.draftType === 'mapping') {
    const bad = input.value.split('\n').map((line) => line.trim())
      .filter((line) => line && line.indexOf('=') <= 0);
    if (bad.length) return `Write each line as a key = a value ("${bad[0]}" is not).`;
  }
  const validity = input.validity;
  if (validity && !validity.valid) {
    if (validity.valueMissing) return 'Give a value.';
    if (validity.badInput) return 'This is a number.';
    if (validity.rangeUnderflow) return `At least ${input.min}.`;
    if (validity.rangeOverflow) return `At most ${input.max}.`;
    if (validity.stepMismatch) return 'A whole number.';
    return 'This is not a value the setting takes.';
  }
  return '';
}

function heldEdits(kind) {
  return {edits: heldFor(unsavedEdits, kind, () => new Map()),
    putBack: heldFor(putBackEdits, kind, () => new Set())};
}

function draftBody(kind) {
  const {edits, putBack} = heldEdits(kind);
  const values = {};
  for (const [path, value] of edits) setPath(values, draftKeys(kind, path), value);
  // Sent as keys, never as a dotted name the server would split at every dot.
  return {kind, values, removed: [...putBack].map((path) => draftKeys(kind, path))};
}

/* Send what is held for one kind. Nothing held, nothing sent: a save never empties a draft. */
async function saveDrafts(kind) {
  clearTimeout(pendingDrafts.get(kind));
  pendingDrafts.delete(kind);
  const {edits, putBack} = heldEdits(kind);
  if (!edits.size && !putBack.size) return;
  const sent = new Map(edits);
  const sentBack = new Set(putBack);
  const saved = await write('/api/workspace/draft', draftBody(kind));
  // What was sent is saved; an edit typed while it was on its way stays held for the next save.
  for (const [path, value] of sent) if (Object.is(edits.get(path), value)) edits.delete(path);
  for (const path of sentBack) if (!edits.has(path)) putBack.delete(path);
  for (const input of document.querySelectorAll(`[data-draft^="${kind}:"]`)) {
    const path = input.dataset.draft.slice(kind.length + 1);
    // A rule's parameters are refused by rule (the Rulebook says which parameter, in words).
    const errors = saved.errors || {};
    // The field's own plain words first; the engine's words where only it can tell.
    const error = inputProblem(input) || errors[path]
      || (input.dataset.changed ? errors[draftKeys(kind, path)[0]] : '');
    const holder = input.closest('td') || input.parentElement;
    const note = holder.querySelector('.draft-note')
      || holder.appendChild(el('span', {class: 'draft-note'}));
    note.textContent = error ? `not valid: ${plainRefusal(error)}` : (input.dataset.changed ? 'draft, saved' : '');
    note.classList.toggle('draft-error', Boolean(error));
  }
  await refreshUncommitted();
}

/* Save every held edit now, without waiting for its timer. */
function saveEveryDraftNow() {
  const kinds = new Set([...unsavedEdits.keys(), ...putBackEdits.keys()]);
  return Promise.all([...kinds].map((kind) => saveDrafts(kind).catch(reportError)));
}

document.addEventListener('input', (event) => {
  const input = event.target.closest && event.target.closest('[data-draft]');
  if (!input) return;
  const kind = input.dataset.draft.split(':')[0];
  const path = input.dataset.draft.slice(kind.length + 1);
  const value = draftValue(input);
  const {edits, putBack} = heldEdits(kind);
  const problem = inputProblem(input);
  const holder = input.closest('td') || input.parentElement;
  const note = holder.querySelector('.draft-note') || holder.appendChild(el('span', {class: 'draft-note'}));
  note.textContent = problem ? `not valid: ${problem}` : note.textContent;
  note.classList.toggle('draft-error', Boolean(problem));
  input.setAttribute('aria-invalid', problem ? 'true' : 'false');
  // Put back to what is in force, the edit leaves the draft; anything else is held and saved.
  if (canonicalValue(value) === input.dataset.original) {
    delete input.dataset.changed;
    edits.delete(path);
    putBack.add(path);
  } else {
    input.dataset.changed = '1';
    edits.set(path, value);
    putBack.delete(path);
  }
  clearTimeout(pendingDrafts.get(kind));
  pendingDrafts.set(kind, setTimeout(() => saveDrafts(kind).catch(reportError), DRAFT_DELAY_MS));
});

/* A view that changes saves what is held first: app.js's one navigation function runs this on
 * leaving every page. */
onLeave(() => saveEveryDraftNow());

/* A page that closes sends what is held on a request the browser finishes after the page is gone. */
window.addEventListener('pagehide', () => {
  for (const kind of new Set([...unsavedEdits.keys(), ...putBackEdits.keys()])) {
    const {edits, putBack} = heldEdits(kind);
    if (!edits.size && !putBack.size) continue;
    fetch('/api/workspace/draft', {
      method: 'POST',
      keepalive: true,
      headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
      body: JSON.stringify(draftBody(kind)),
    }).catch(() => {});
  }
});

/* ----------------------------------------------------------- the header's buttons */

function applyingRerun(status) {
  const scan = window.ingenceScan;
  const running = scan ? scan.busy(status) && status.phase !== 'queued'
    : Boolean(status) && ['binding', 'inferring', 'names', 'rules', 'views'].includes(status.phase);
  return Boolean(status && (status.rerun || []).length && running);
}

async function refreshUncommitted() {
  const held = await api('/api/workspace/state');
  uncommittedTotal = Object.values(held.uncommitted || {}).reduce((a, b) => a + b, 0);
  const button = document.getElementById('commit-button');
  if (button) {
    button.textContent = uncommittedTotal ? `Commit (${uncommittedTotal})` : 'Committed';
    button.disabled = !uncommittedTotal;
    button.title = uncommittedTotal
      ? 'Save your draft changes to the project, with a note of what and why'
      : 'Nothing is waiting to be committed';
  }
  const running = applyingRerun(held.applying);
  // A session that opened while a re-run was under way shows its banner and follows it.
  if (running && !workspaceApplying) watchApplying(held.applying);
  workspaceApplying = running ? held.applying : null;
  // A re-run that a close stopped waits for a person: the notice says so.
  rerunStopped = Boolean(held.applying && held.applying.stopped);
  if (held.applying) lastApplying = held.applying;
  showRerun(held.pending_rerun || null, running);
  showNotReady();
}

/* ------------------------------------------------ what waits for Re-run */

/* Later is kept in this browser's localStorage, so that a new tab does not ask again. It holds a
 * mark of what waited when Later was chosen: a reload, a new tab and a new window keep the notice
 * closed, and it asks again once something else waits. Where storage is refused (some private
 * windows), Later closes the notice for this page only. */
const LATER_KEY = 'ingence.rerun.later';
/* The rows a summary line opens to, before "and N more". */
const ROWS_SHOWN = 50;
/* The notice is said once, from the first reading of what waits after the page opens. */
let noticeAsked = false;
let lastWaiting = null;
/* Whether a re-run that a close stopped waits for a person. */
let rerunStopped = false;
/* Whether a re-run is under way, and the job state the page read last (the header's button reads
 * Stop while that job can be stopped). */
let lastRunning = false;
let lastApplying = null;
let stopMode = false;
/* The line a Re-run said about what needs the share, kept while it is true. */
let shareLine = '';
/* The summary's kinds a person opened, kept across the header's readings. */
const openKinds = new Set();

/* A short mark of what waits (and whether a re-run stopped): the same changes give the same mark,
 * and any change added, removed or committed gives another. */
function waitingMark(waiting) {
  const said = JSON.stringify([rerunStopped, waiting ? [waiting.count, waiting.summary,
    waiting.commits, waiting.answers] : null]);
  let hash = 5381;
  for (let index = 0; index < said.length; index += 1) hash = ((hash * 33) ^ said.charCodeAt(index)) >>> 0;
  return `${said.length}.${hash.toString(36)}`;
}

function laterChosen(waiting) {
  try {
    return window.localStorage.getItem(LATER_KEY) === waitingMark(waiting);
  } catch (error) {
    return false;
  }
}

function chooseLater(waiting) {
  try {
    window.localStorage.setItem(LATER_KEY, waitingMark(waiting));
  } catch (error) {
    /* Storage refused (a private window): it closes for this page only. */
  }
}

/* The Re-run button and its badge. Never hidden: disabled when nothing waits. While a job can be
 * stopped (/api/applying's can_stop) the button reads Stop instead (paintStop). */
function showRerun(waiting, running) {
  lastWaiting = waiting;
  lastRunning = running;
  paintRerunButton();
  const badge = document.getElementById('rerun-badge');
  if (badge) {
    const count = waiting && !running ? waiting.count : 0;
    badge.hidden = !count;
    badge.textContent = count > 99 ? '99+' : String(count || '');
    badge.title = count ? waiting.banner : '';
    if (count) badge.setAttribute('aria-label', `${waiting.banner} Show them.`);
    else badge.removeAttribute('aria-label');
  }
  const panel = document.getElementById('rerun-summary');
  if (panel && !panel.hidden) {
    // Kept open while a re-run works, so the share's line stays read.
    if (waiting) fillSummary(panel, waiting);
    else toggleSummary(false);
  }
  if (!noticeAsked) {
    noticeAsked = true;
    showNotice(waiting, running);
  } else {
    keepNoticeTrue(waiting, running);
  }
}

/* The header's one button: Stop while a job can be stopped, else Re-run. */
function paintRerunButton() {
  const button = document.getElementById('rerun-button');
  if (!button) return;
  const scan = window.ingenceScan;
  const stopping = Boolean(lastApplying && lastApplying.can_stop && lastApplying.job_id
    && (!scan || scan.busy(lastApplying)));
  stopMode = stopping;
  button.classList.toggle('stop-mode', stopping);
  if (stopping) {
    button.textContent = 'Stop';
    button.dataset.help = 'workspace.stop';
    button.disabled = false;
    button.title = 'Stop the scan';
    return;
  }
  button.textContent = 'Re-run';
  button.dataset.help = 'workspace.rerun';
  const waiting = lastWaiting;
  button.disabled = (!waiting && !rerunStopped) || lastRunning;
  const steps = waiting ? waiting.steps.map((step) => step.words).join(', then ') : '';
  button.title = lastRunning ? 'A Re-run is running'
    : waiting ? `${steps.charAt(0).toUpperCase()}${steps.slice(1)}.`
      : rerunStopped ? 'Starts the Re-run that stopped when the Dashboard last closed.'
        : 'Nothing is waiting for a Re-run';
}

function noticeWords(waiting) {
  const stopped = rerunStopped ? 'A Re-run stopped when the Dashboard last closed. ' : '';
  return `${stopped}${waiting ? waiting.banner : ''} `;
}

/* A notice still open says what waits now: a change made meanwhile is counted, and it closes once
 * nothing waits or a re-run is under way. */
function keepNoticeTrue(waiting, running) {
  const notice = document.getElementById('rerun-notice');
  if (!notice || notice.hidden) return;
  if ((!waiting && !rerunStopped) || running) {
    notice.hidden = true;
    return;
  }
  const said = notice.querySelector('.rerun-said');
  if (said) said.textContent = noticeWords(waiting);
}

/* Said once when a session opens where changes wait: what waits, Re-run now, Later. */
function showNotice(waiting, running) {
  const notice = document.getElementById('rerun-notice');
  if (!notice) return;
  const show = Boolean((waiting || rerunStopped) && !running && !laterChosen(waiting));
  notice.replaceChildren();
  notice.hidden = !show;
  if (!show) return;
  notice.append(
    el('span', {class: 'rerun-said', text: noticeWords(waiting)}),
    el('button', {type: 'button', class: 'primary', text: 'Re-run now',
      onclick: () => startRerun().catch(reportError)}), ' ',
    el('button', {type: 'button', text: 'Later', onclick: () => {
      // What waits now: the open notice counts a change made meanwhile (keepNoticeTrue).
      chooseLater(lastWaiting);
      notice.hidden = true;
    }}),
  );
}

/* The badge's summary: a line per kind, each opening its rows; a line on top where one is said. */
function fillSummary(panel, waiting, said) {
  panel.replaceChildren();
  // The share's line stays while a change still needs the share.
  const sharing = waiting && waiting.steps.some((step) => step.reads_the_share);
  if (!sharing) shareLine = '';
  const line = said || shareLine;
  if (line) panel.append(el('p', {class: 'rerun-share', text: line}));
  for (const line of (waiting && waiting.summary) || []) {
    const more = line.rows.length - ROWS_SHOWN;
    // A kind the person opened stays open when the header reads what waits again.
    const kind = el('details', {class: 'rerun-kind', 'data-kind': line.kind,
      ontoggle: (event) => (event.target.open ? openKinds.add(line.kind) : openKinds.delete(line.kind))});
    kind.open = openKinds.has(line.kind);
    panel.append(kind);
    kind.append(...[
      el('summary', {text: line.words}),
      el('ul', {}, [
        ...line.rows.slice(0, ROWS_SHOWN).map((row) => el('li', {text: row})),
        more > 0 ? el('li', {class: 'faint', text: `and ${more.toLocaleString()} more`}) : null,
      ].filter(Boolean)),
    ]);
  }
}

function toggleSummary(open) {
  const panel = document.getElementById('rerun-summary');
  const badge = document.getElementById('rerun-badge');
  if (!panel) return;
  const opening = open === undefined ? panel.hidden : open;
  panel.hidden = !opening;
  if (badge) badge.setAttribute('aria-expanded', String(opening));
  if (opening) fillSummary(panel, lastWaiting);
  else shareLine = '';
}

/* The banner's words for a re-run of what waits, commits and answers alike (app.js hands these
 * states over). The progress is scan.js's: the phase, the step, the counts and the time left. */
function rerunText(status) {
  const scan = window.ingenceScan;
  if (scan && scan.busy(status) && status.phase !== 'queued') {
    return `Changes: ${scan.text(status) || 'running'}.`;
  }
  if (status.phase === 'done') {
    return 'Changes took effect. The counts above are from the new scan.';
  }
  if (status.phase === 'failed') {
    return `Changes were not applied: ${status.error}. They are kept; Re-run applies `
      + 'them again.';
  }
  return '';
}

async function startRerun() {
  const notice = document.getElementById('rerun-notice');
  if (notice) notice.hidden = true;
  const answer = await write('/api/workspace/rerun', {});
  if (answer.needs_the_share) {
    // A walk or a dive reads the share, so a person starts it: said on top of the summary and
    // never in a dialog. What reads only the project is started meanwhile.
    shareLine = 'Start what needs the data folders yourself, in a terminal: '
      + `${answer.commands.join(', then ')}.`;
    toggleSummary(true);
  } else {
    toggleSummary(false);
  }
  await refreshUncommitted();
  return answer;
}

/* ------------------------------------------------ Stop */

/* What the page read last about the job (app.js's watchApplying hands every state over): the
 * header's button and the line over the readout follow it. */
function showJobControls(state) {
  if (!state) return;
  lastApplying = state;
  paintRerunButton();
  showNotReady();
}

/* Stop a scan, asked first in a small panel under the progress line (scan.js): "Stop the scan?",
 * "What was read so far is kept." only where /api/applying says stop_keeps, and "Your changes
 * still wait for Re-run." only where changes wait. Stop posts /api/workspace/stop with keep: true
 * through write() (its X-Ingence-Write header). It resolves with the route's answer, or null
 * where the person chose Cancel or no job runs. Any page may call it (the header's Stop, and the
 * Overview's). */
window.ingenceStop = async function ingenceStop(jobId) {
  try {
    // What the engine says now: the job (and whether what it read is kept), and what waits.
    await refreshUncommitted();
  } catch (error) {
    /* The last state read stands in. */
  }
  const state = lastApplying;
  const job = jobId || (state && state.job_id);
  if (!job) return null;
  const scan = window.ingenceScan;
  if (!scan) return null;
  const line = document.getElementById('applying');
  const answer = await scan.askToStop({
    after: line,
    inside: document.getElementById('main'),
    stopKeeps: Boolean(state && state.stop_keeps),
    changesWait: changesWaiting(),
    post: () => write('/api/workspace/stop', {job_id: job, keep: true}),
  });
  if (answer && answer.requested !== false) {
    // The job line follows the job to its end ("stopped"), and the header reads Re-run again.
    try {
      watchApplying(await api('/api/applying'));
    } catch (error) {
      /* The next reading of the header says it. */
    }
    refreshUncommitted().catch(() => {});
  }
  return answer;
};

/* Whether changes wait for Re-run: the count the Re-run badge shows when no job runs. */
function changesWaiting() {
  return Boolean(lastWaiting && lastWaiting.count > 0);
}

/* While a new scan runs over a finished one, the views read the last finished scan: one line over
 * the readout says so, and goes when the new scan is published. */
function showNotReady() {
  const scan = window.ingenceScan;
  const busy = Boolean(lastApplying && scan && scan.busy(lastApplying) && !lastApplying.stopped);
  let line = document.getElementById('scan-not-ready');
  if (!busy) {
    if (line) line.hidden = true;
    return;
  }
  if (!line) {
    const main = document.getElementById('main');
    if (!main || !main.parentNode) return;
    line = el('p', {id: 'scan-not-ready', class: 'scan-not-ready', role: 'status',
      'data-help': 'workspace.not_ready',
      text: 'This is the last finished scan. The new one is not ready yet.'});
    main.parentNode.insertBefore(line, main);
  }
  line.hidden = false;
}

async function openCommitDialog() {
  // What is typed and not yet saved is committed with the rest, and counted in the dialog.
  await saveEveryDraftNow();
  const preview = await api('/api/workspace/commit-preview');
  const who = await whoWrites();
  if (!who) return;
  const years = await yearsRow();
  const today = new Date().toLocaleDateString(undefined, {day: 'numeric', month: 'long', year: 'numeric'});
  // One of the Dashboard's dialogs (dialogs.js): labeled, focus kept inside, Escape cancels.
  await dialogs.open({title: 'Commit changes', className: 'commit-dialog',
    text: `${uncommittedTotal} changed ${uncommittedTotal === 1 ? 'value' : 'values'}. `
      + 'They become part of the project, and Ingence keeps a record of this moment.',
    build: (finish) => {
      const message = el('input', {type: 'text', id: 'commit-note', value: `Changes of ${today}`,
        'data-autofocus': ''});
      const problem = el('p', {class: 'draft-error', id: 'commit-error', role: 'alert'});
      // Disabled while the commit runs, so one click is one commit.
      const commitButton = el('button', {type: 'button', class: 'primary', text: 'Commit', onclick: async () => {
        if (!message.value.trim()) {
          problem.textContent = 'Say in a few words what changed.';
          message.focus();
          return;
        }
        commitButton.disabled = true;
        try {
          // A years change made in this dialog is saved first, so the commit carries it.
          await yearsSaving;
          await write('/api/workspace/commit', {message: message.value, actor: who});
        } catch (error) {
          const said = plainRefusal(error.message || error);
          problem.textContent = said.includes('would not be accepted')
            ? `${said}. Each such value is marked where it is edited: Settings, or a rule's settings `
              + 'on Rules.' : said;
          commitButton.disabled = false;
          return;
        }
        finish(true);
        await refreshUncommitted();
      }});
      return [
        preview.needs && preview.needs.length
          ? el('p', {class: 'needs', text: `After committing, this needs: ${preview.needs.join(', then ')}.`})
          : el('p', {class: 'needs', text: 'Nothing needs a Re-run for these.'}),
        years ? yearsControl(years, 'commit') : null,
        el('label', {for: 'commit-note', text: 'Note '}), message,
        problem,
        el('p', {class: 'actions'}, [
          el('button', {type: 'button', text: 'Cancel', onclick: () => finish(false)}),
          commitButton,
        ]),
      ].filter(Boolean);
    }});
}

/* ------------------------------------------------ the years the data spans, before a run */

/* The years a date in a name may fall in, asked before a run. Whatever the user gives before a
 * run is used, and 2013 to the present when they give nothing. The setting is
 * ``dictionary.years``, a first year and a last (a year, or "present"). Where the engine this
 * page is served by has no such setting, no control is shown. The control is written as a
 * settings draft and committed like any setting, never into settings.yaml. */
async function yearsRow() {
  try {
    const {settings} = await api('/api/workspace/settings');
    return settings.find((row) => row.name === 'dictionary.years') || null;
  } catch (error) {
    return null;
  }
}

/* The pending save of the years, which a commit waits for (a change made inside the Commit
 * dialog is saved before the commit reads the drafts). */
let yearsSaving = Promise.resolve();

function yearsControl(row, place) {
  const shown = row.drafted && Array.isArray(row.draft) ? row.draft : row.value;
  const [first, last] = Array.isArray(shown) ? shown : [2013, 'present'];
  const thisYear = new Date().getFullYear();
  const from = el('input', {type: 'number', id: `${place}-years-from`, min: '1900', max: String(thisYear),
    step: '1', value: String(first)});
  const toPresent = el('input', {type: 'checkbox', id: `${place}-years-present`});
  toPresent.checked = last === 'present';
  const to = el('input', {type: 'number', id: `${place}-years-to`, min: '1900', max: String(thisYear + 1),
    step: '1', value: String(last === 'present' ? thisYear : last)});
  to.disabled = toPresent.checked;
  const note = el('span', {class: 'draft-note', role: 'status'});
  const save = async () => {
    const start = Number(from.value);
    const end = toPresent.checked ? 'present' : Number(to.value);
    let problem = '';
    if (!Number.isInteger(start)) problem = 'The first year is a whole year, like 2013.';
    else if (end !== 'present' && !Number.isInteger(end)) problem = 'The last year is a whole year.';
    else if (end !== 'present' && start > end) problem = 'The first year comes before the last.';
    note.classList.toggle('draft-error', Boolean(problem));
    if (problem) { note.textContent = `not valid: ${problem}`; return; }
    const saved = await write('/api/workspace/draft', {kind: 'settings',
      values: {dictionary: {years: [start, end]}}});
    const error = (saved.errors || {})['dictionary.years'] || (saved.errors || {}).dictionary;
    note.classList.toggle('draft-error', Boolean(error));
    note.textContent = error ? `not valid: ${error}` : 'draft, saved';
    await refreshUncommitted();
  };
  const saving = () => { yearsSaving = save().catch(reportError); };
  from.addEventListener('change', saving);
  to.addEventListener('change', saving);
  toPresent.addEventListener('change', () => { to.disabled = toPresent.checked; saving(); });
  return el('fieldset', {class: 'years', 'data-help': 'workspace.years'}, [
    el('legend', {text: 'Experiment date range'}),
    el('p', {class: 'faint', text: 'Ingence reads a number in a name as a year only inside this range.'}),
    el('label', {for: `${place}-years-from`, text: 'from '}), from, ' ',
    el('label', {for: `${place}-years-to`, text: ' to '}), to, ' ',
    el('label', {}, [toPresent, ' the present']), ' ', note,
  ]);
}

/* The browser tab's fallback. The browser shows its own generic words; ours cannot appear. */
window.addEventListener('beforeunload', (event) => {
  if (uncommittedTotal > 0 && !window.ingenceDesktop) {
    event.preventDefault();
    event.returnValue = '';
  }
});

/* The Settings section of the Engine page belongs to settings.js. */

/* A definition's job finishing changes what waits; the header reads it again (app.js's
 * refreshWaiting runs every onRefresh hook after it reads the tabs' counts). */
onRefresh(() => refreshUncommitted().catch(() => {}));

(function wireWorkspace() {
  const button = document.getElementById('commit-button');
  if (button) button.addEventListener('click', () => openCommitDialog().catch(reportError));
  const rerun = document.getElementById('rerun-button');
  if (rerun) {
    rerun.addEventListener('click', () => {
      if (stopMode) window.ingenceStop(lastApplying && lastApplying.job_id).catch(reportError);
      else startRerun().catch(reportError);
    });
  }
  const badge = document.getElementById('rerun-badge');
  if (badge) badge.addEventListener('click', () => toggleSummary());
  // Escape closes the summary, where no dialog is open to take it.
  document.addEventListener('keydown', (event) => {
    const panel = document.getElementById('rerun-summary');
    if (event.key !== 'Escape' || !panel || panel.hidden || document.querySelector('dialog[open]')) return;
    toggleSummary(false);
  });
  refreshUncommitted().catch(() => {});
})();
