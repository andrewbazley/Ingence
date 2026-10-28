/* The Dashboard's pages, beside review.js's.
 *
 * This file holds the helpers every page shares (el, api, write, table, bars) and the pages that
 * read without asking anything: the Inventory and the Coverage view. The Rules are rules.js's. The
 * review pages -- questions, groupings, claim samples, proposed pairs, extensions, programs and the
 * changes between two runs -- are review.js's, loaded before this file.
 *
 * No framework and no build step. Everything ships beside the engine. */

/* ``hashQuery``: what the address says after the view's name (``#ask?q=GFP`` is ``q=GFP``). */
const state = { view: 'inventory', project: null, hashQuery: '' };

const listPane = document.getElementById('list');
const detailPane = document.getElementById('detail');

/* The server's own words for a refusal: the old routes say ``{"error": "..."}`` and /api/v1 says
 * ``{"error": {"message": "..."}}``; either way the page shows what the server said. */
function refusalWords(body, fallback) {
  if (!body || body.error == null) return fallback;
  return typeof body.error === 'string' ? body.error : (body.error.message || fallback);
}

/* A failed request names itself. The error carries the method, the route, the status (0 where the
 * engine did not answer at all) and the time. So the error state can say "GET /api/v1/themes/map
 * returned 500 at 9:14 AM", and "Report a problem" can state the same. The route's query is left
 * out, because a query can hold what a person typed. */
function requestFailed(words, method, route, status, name) {
  const error = new Error(words);
  if (name) error.name = name;
  Object.assign(error, {method, route: String(route).split('?')[0], status, at: new Date()});
  return error;
}

async function api(path) {
  let response;
  try {
    response = await fetch(path);
  } catch (error) {
    if (error && error.name === 'AbortError') throw error;
    throw requestFailed('Ingence did not answer.', 'GET', path, 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw requestFailed(refusalWords(body, `${response.status} ${response.statusText}`), 'GET', path,
      response.status);
  }
  return response.json();
}

/* A write carries a header a cross-site form cannot set, which is what keeps a page on
 * another site out of somebody's Record. Not a credential: there are no accounts on a
 * single-user machine, and the server says so itself. */
async function write(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
      body: JSON.stringify(body),
    });
  } catch (error) {
    throw requestFailed('Ingence did not answer.', 'POST', path, 0);
  }
  const parsed = await response.json().catch(() => ({error: response.statusText}));
  if (!response.ok) {
    throw requestFailed(refusalWords(parsed, response.statusText), 'POST', path, response.status);
  }
  if (parsed.applying) watchApplying(parsed.applying);
  /* An answer or a pack installed can wait for Re-run: the header asks again after such writes, on
   * every page, rather than at the next reload. It asks once for a burst of them, because "take
   * all" writes up to two hundred answers one by one, and each ask starts a count on the server. */
  if (WAITS_FOR_RERUN.has(path)) refreshAfterWrites();
  return parsed;
}

/* A rejected assumption and an area's kind that a person stated wait for Re-run, as an answer
 * does. */
const WAITS_FOR_RERUN = new Set(['/api/review/answer', '/api/dictionary/pack', '/api/assumptions/reject',
  '/api/areas/kind']);

/* Asks for what waits once, a moment after the last of a burst of writes. */
const AFTER_WRITES_MS = 300;
let afterWritesTimer = null;
function refreshAfterWrites() {
  if (afterWritesTimer) clearTimeout(afterWritesTimer);
  afterWritesTimer = setTimeout(() => {
    afterWritesTimer = null;
    refreshWaiting();
  }, AFTER_WRITES_MS);
}

/* A job applying what waits (only Re-run starts one, and a definition waits for it): the names read
 * again, then the rules applied in the background. The line says the phase, the step, the counts
 * and the time left (scan.js reads /api/applying's phase_label, step, steps, done, total and
 * seconds_left), never an internal table name and never a time predicted from an earlier job. */
let applyingTimer = null;

/* Whether a job is waiting or running, by its state and, for an older engine, its phase. */
function applyingBusy(state) {
  const scan = window.ingenceScan;
  if (scan) return scan.busy(state) || (state.queued || []).length > 0;
  // From 0.4.0 the engine sends the contract's phase words (names, rules) in phase, and the
  // ledger's in phase_legacy. An older engine sends the ledger's words (binding, inferring) in
  // phase.
  return ['queued', 'binding', 'inferring', 'names', 'rules', 'views'].includes(state.phase)
    || (state.queued || []).length > 0;
}

function applyingText(state) {
  /* Read from the job ledger: a job is queued, running, done, stopped or did not finish, and one
   * whose worker died is said to have been interrupted and is running again. */
  const scan = window.ingenceScan;
  const running = state.state === 'running'
    || Boolean(scan && scan.PHASE_LABELS[state.phase] && state.state !== 'queued');
  const retried = (state.retries && running
    ? ` Attempt ${state.attempts}: the last was interrupted (${state.interrupted || 'its worker stopped'}).`
    : '')
    /* The server says for how long, and what will happen and when. */
    + (state.silent_seconds && running
      ? ` ${state.silent_said || `Its worker has not reported for ${state.silent_seconds} s.`}` : '');
  const words = (state.spellings || state.queued || []).map((word) => `"${word}"`).join(', ');
  const asked = (state.rerun || []).length ? 'Changes' : words || 'An answer';
  if (state.phase === 'queued') {
    const again = state.interrupted ? ` Its last attempt was interrupted: ${state.interrupted}.` : '';
    /* Another process publishing in this project holds the job back (one publish lock). */
    if (state.held_said) return `${asked}: waiting to start. ${state.held_said}${again}`;
    return `${asked}: waiting to start.${again}`;
  }
  if (state.phase === 'cancelled' || state.state === 'cancelled') {
    /* Said only where changes still wait (workspace.js counts them for the Re-run badge). */
    const waits = typeof changesWaiting === 'function' && changesWaiting();
    return `${asked}: stopped.${waits ? ' Your changes still wait for Re-run.' : ''}`;
  }
  /* A re-run of committed changes has no spellings; workspace.js words it. */
  if ((state.rerun || []).length && typeof rerunText === 'function') {
    const said = rerunText(state);
    if (said) return said + retried;
  }
  const queued = (state.queued || []).length && running
    ? ` Next: ${state.queued.map((word) => `"${word}"`).join(', ')}.` : '';
  if (running) {
    const progress = scan ? scan.text(state) : '';
    return `${asked}: ${progress || 'running'}.${queued}${retried}`;
  }
  if (state.phase === 'done') {
    // The header shows no counts, so the line points to none.
    return `${asked}: done. Every page opened from here reads the new scan.`;
  }
  if (state.phase === 'failed') {
    /* What happened and what it means on the page; the commands are "by hand". */
    return `${asked} did not finish: ${state.error}. Your answer is kept in the answer log `
      + 'and takes effect at the next Re-run.';
  }
  return '';
}

/* The commands behind a job that did not finish, for a person who wants them. */
function byHand() {
  return el('details', {class: 'by-hand', 'data-help': 'history.by_hand'}, [
    el('summary', {text: 'by hand'}),
    el('code', {text: 'ingence index --rebind'}), ', then ', el('code', {text: 'ingence infer'}),
  ]);
}

/* What else follows the job: the Overview's job line is told each time the job's state is read, and
 * once more when it ends. (Re-run reads Stop while a job runs: workspace.js.) */
const applyingHooks = [];

function onApplying(hook) {
  applyingHooks.push(hook);
}

/* Whether a job is under way, for the Overview's job line: the header's own reading (applyingBusy,
 * which asks scan.js where it is loaded). */
function jobRunning(status) {
  return Boolean(status) && applyingBusy(status);
}

function watchApplying(state) {
  for (const hook of applyingHooks) {
    try {
      hook(state);
    } catch (error) {
      /* What follows the job is a convenience: the banner is still said. */
    }
  }
  const banner = document.getElementById('applying');
  /* The header's Re-run reads Stop while the job can be stopped (workspace.js). */
  if (typeof showJobControls === 'function') showJobControls(state);
  if (!banner) return;
  const text = applyingText(state);
  banner.hidden = !text;
  banner.textContent = text;
  banner.className = `applying ${state.phase || ''}`;
  const running = applyingBusy(state);
  /* The bar, only where the engine knows the total (scan.js). */
  const bar = running && window.ingenceScan ? window.ingenceScan.bar(state) : null;
  if (text && bar) banner.append(bar);
  // Every job the ledger holds, in its state, is listed on History.
  if (text) {
    banner.append(' ', el('button', {class: 'link', type: 'button', 'data-help': 'history.jobs',
      text: 'every job', onclick: () => navigate('changes')}));
  }
  if (text && state.phase === 'failed') banner.append(' ', byHand());
  if (applyingTimer) clearTimeout(applyingTimer);
  if (state.phase === 'done' && !running) {
    refreshWaiting();
  }
  if (running) {
    applyingTimer = setTimeout(async () => {
      try {
        const response = await fetch('/api/applying');
        watchApplying(await response.json());
      } catch (error) {
        banner.textContent = `Ingence could not read the progress of the job: ${error}`;
      }
    }, 1500);
  }
}

/* On opening, the ledger says whether a job waits, runs or failed: a Dashboard started after a
 * crash shows the job it found and follows it, as one started by a write does. */
async function followApplying() {
  const state = await api('/api/applying');
  if (applyingBusy(state) || state.phase === 'failed') watchApplying(state);
  else if (typeof showJobControls === 'function') showJobControls(state);
}

/* Who is writing: the name set on this machine, asked once where there is none, and recorded
 * with every event. The name is review.js's ``me``, which every page shares. */
async function whoWrites() {
  return (await askActor()) ? me.actor : '';
}

/* A folder row's annotation: a note about the folder, saved in the answer log under the writer's
 * name. Any folder row offers it. */
function annotateControl(folder, path) {
  return el('span', {class: 'answers'}, [el('button', {
    text: 'annotate',
    'data-help': 'folder.annotate',
    onclick: async (event) => {
      event.stopPropagation();
      const button = event.currentTarget;
      const who = await whoWrites();
      if (!who) return;
      const note = await dialogs.ask({title: 'What does this folder hold?',
        text: `${path}. What you write is saved in the answer log as a note about this folder, `
          + 'under your name; it decides nothing.', label: 'What it holds, in your words',
        submit: 'Add the note', multiline: true,
        validate: (typed) => (typed.length > 4000 ? 'A note is at most 4,000 characters.' : '')});
      if (!note) return;
      try {
        await write('/api/review/answer', {actor: who, folder, note});
        button.textContent = 'annotated';
      } catch (error) {
        await dialogs.failed(error);
      }
    },
  })]);
}

function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === 'class') node.className = value;
    else if (name === 'text') node.textContent = value;
    else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

/* A time as a person says it ("9:14 AM"), or nothing for a value that is not a time. */
function clockWords(moment) {
  const when = moment instanceof Date ? moment : new Date(moment);
  if (Number.isNaN(when.getTime())) return '';
  return when.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
}

/* A date as a person says it ("October 13"), or nothing. */
function dayWords(moment) {
  const when = new Date(moment);
  if (Number.isNaN(when.getTime())) return '';
  return when.toLocaleDateString('en-US', {month: 'long', day: 'numeric'});
}

/* The view being looked at, by the name on its tab ("Years", "Map of groups"): the eyebrow of every
 * drawn state. A count on a tab ("Questions (5)") is left off. */
function viewWords() {
  if (state.view === 'ask') return 'Ask';
  const section = document.querySelector('nav.sections:not([hidden]) button.active');
  const page = document.querySelector('#pages button.active');
  const found = section || page;
  if (!found) return 'This view';
  const text = [...found.childNodes].filter((part) => part.nodeType === 3)
    .map((part) => part.textContent).join('').trim();
  return text || 'This view';
}

/* ------------------------------------------------------------------ turns */

/* The last thing asked for is what a pane shows. A pane's contents are read over the network, and
 * two reads can cross: a comparison asked for on opening History can finish after the person asked
 * for a run's restore preview, and must not replace it. Each read claims the pane before it starts
 * and draws only if nothing claimed it since. */
const turns = {
  held: new WeakMap(),
  claim(pane) {
    const turn = (this.held.get(pane) || 0) + 1;
    this.held.set(pane, turn);
    return turn;
  },
  owns(pane, turn) {
    return this.held.get(pane) === turn;
  },
};

/* ------------------------------------------------------------------ states */

/* One way every page says it is reading, that something failed, or that there is nothing. Loading
 * says what is being read. A failure says it could not be read with the server's own words and a
 * way to try again. Empty says so in a sentence. A pane is never left blank. */
const states = {
  loading(pane, words) {
    fill(pane, el('p', {class: 'empty state-loading', role: 'status', 'aria-live': 'polite',
      text: words || 'Reading...'}));
  },
  /* The error state: for a failed request it names the method, the route, the status and the time,
   * says the run is unchanged, and offers Try again and a report prefilled with the route and time
   * (never a name). Any other failure says what could not be read, in the words it gave. The card
   * is returned for a caller that places it itself (`into`). */
  failed(pane, error, retry, {into = false} = {}) {
    const words = String((error && error.message) || error || '').trim()
      || 'The server gave no reason.';
    const asked = error && error.route ? error : null;
    const failure = asked ? {method: asked.method, route: asked.route, status: asked.status, at: asked.at} : null;
    const when = clockWords(asked ? asked.at : new Date());
    const requestLine = asked
      ? `${asked.method} ${asked.route} ${asked.status === 0 ? 'got no answer' : `returned ${asked.status}`}`
        + `${when ? ` at ${when}` : ''}. The scan is unchanged${asked.method === 'GET' ? ', and nothing was saved' : ''}.`
      : null;
    const engineSaid = asked && asked.status !== 0 && !/^\d{3}\b/.test(words)
      ? `Ingence said: ${words}` : null;
    const card = states.say(null, {
      eyebrow: `${viewWords()} \u00b7 error`, dot: 'problem', role: 'alert', into: true,
      sentence: asked ? 'Ingence could not answer.' : 'Ingence could not read this.',
      body: asked ? [requestLine, engineSaid] : [words],
      actions: [
        ...(retry ? [{label: 'Try again', onclick: () => retry(), help: 'state.retry'}] : []),
        {label: 'Report a problem', help: 'footer.report', onclick: () => {
          if (window.ingenceReport) window.ingenceReport.open({failure}).catch(() => {});
        }},
      ],
    });
    card.classList.add('state-failed');
    if (into || !pane) return card;
    fill(pane, card);
    return card;
  },
  empty(pane, words) {
    fill(pane, el('p', {class: 'nothing', text: words}));
  },
  /* One drawn state per view. It has an eyebrow naming the view and the state, and a dot (`empty`
   * grey, `census` the later-stage color, `loading` the accent, `problem` a red border). It has one
   * bold sentence in the engine's words, a line or two, and one or two actions ({label, onclick} or
   * {label, href}). It is never a spinner alone and never a percentage. With `into`, the card is
   * returned for the caller to place rather than filling the pane. */
  say(pane, {eyebrow = '', dot = 'empty', sentence, body = [], actions = [], role = 'status', into = false}) {
    const card = el('div', {class: 'state-card', role, 'data-state': dot}, [
      eyebrow ? el('p', {class: 'eyebrow', text: eyebrow}) : null,
      el('div', {class: 'state-head'}, [
        el('span', {class: `state-dot ${dot}`, 'aria-hidden': 'true'}),
        el('p', {class: 'state-sentence', text: sentence}),
      ]),
      ...[].concat(body).filter(Boolean).map((line) => el('p', {class: 'state-body', text: line})),
      actions.length ? el('div', {class: 'state-actions'}, actions.map((action) => (action.href
        ? el('a', {class: 'answer quiet', href: action.href, text: action.label})
        : el('button', {type: 'button', class: 'answer quiet', onclick: action.onclick,
          ...(action.help ? {'data-help': action.help} : {})},
        [el('strong', {text: action.label})])))) : null,
    ]);
    if (into || !pane) return card;
    fill(pane, card);
    return card;
  },
};

/* A header is a string (``#`` first marks a number column) or a node, such as the select-all box. */
function table(headers, rows) {
  const head = el('tr', {}, headers.map((h) => (typeof h === 'string'
    ? el('th', { class: h.startsWith('#') ? 'n' : '', text: h.replace(/^#/, '') })
    : el('th', {class: 'pick'}, [h]))));
  return el('table', {}, [el('thead', {}, [head]), el('tbody', {}, rows)]);
}

/* ------------------------------------------------------------------ inventory */

/* A row of proportional bars. No chart library: this is a count and a width, and the
 * Structure view is where a graph library will earn its place. */
function bars(rows, {accent = 'var(--accent)'} = {}) {
  if (!rows || !rows.length) return el('p', {class: 'empty', text: 'nothing recorded'});
  const top = Math.max(...rows.map((r) => Number(r.n) || 0)) || 1;
  return el('div', {class: 'bars'}, rows.map((row) => el('div', {class: 'bar'}, [
    el('span', {class: 'bar-label', text: String(row.value ?? '(blank)')}),
    el('span', {class: 'bar-track'}, [
      el('span', {
        class: 'bar-fill',
        style: `width:${Math.max(1, (100 * Number(row.n)) / top)}%;background:${accent}`,
      }),
    ]),
    el('span', {class: 'bar-n', text: Number(row.n).toLocaleString()}),
  ])));
}

function sumOf(rows) {
  return (rows || []).reduce((total, row) => total + (Number(row.n) || 0), 0);
}

/* A stored value in plain words, from one table. The engine's words stay in the Index and the
 * Record. A page shows these words and keeps the stored word in a tooltip. Not yet examined,
 * examined with no evidence, and examined with conflicting evidence stay three different words. A
 * word the table lacks is shown with its underscores as spaces. */
const PLAIN_VALUES = {
  unexamined: 'not examined', examined_no_evidence: 'no evidence found',
  examined_conflicting: 'conflicting evidence', resolved: 'settled',
  file: 'file', directory: 'folder', symlink: 'link', other: 'other',
  not_a_link: 'not a link', resolves: 'target found', broken: 'target missing',
  unreadable: 'could not check', outside_root: 'points outside the data folder',
  extracted: 'content read', extracted_with_fallback: 'content read by a fallback reader',
  metadata_only: 'name and metadata only', skipped_by_policy: 'nothing to read',
  skipped_by_settings: 'unread by the settings', deferred: 'deferred',
  content_error: 'could not be read', changed_during_read: 'changed while read',
  access: 'could not open', comprehension: 'could not read', interpretation: 'could not interpret',
  whole_rebind: 'every name read again', partial_rebind: 'names holding it read again',
  census: 'listed', dive: 'contents read',
  inference: 'rules applied', exclude_glob: 'matched an exclusion', max_depth: 'below the depth limit',
};

function plainValue(raw) {
  const word = String(raw ?? '');
  return PLAIN_VALUES[word] ?? word.replace(/_/g, ' ');
}

/* bars(), each label in plain words and its stored value in the label's tooltip. */
function plainBars(rows, options = {}) {
  const drawn = bars(rows, options);
  for (const shown of drawn.querySelectorAll('.bar-label')) {
    const raw = shown.textContent;
    const plain = plainValue(raw);
    if (plain !== raw) {
      shown.textContent = plain;
      shown.title = raw;
    }
  }
  return drawn;
}

async function showInventory() {
  states.loading(listPane, 'Reading the project...');
  const data = await api('/api/inventory?limit=12');
  const here = data.here;
  const unexplained = data.unexplained;

  listPane.replaceChildren(
    el('h2', {text: 'What is here'}),
    el('h3', {text: 'By kind'}), plainBars(here.by_kind),
    el('h3', {text: 'By extension'}), bars(here.by_extension),
    el('h3', {text: 'By depth'}), bars(here.by_depth),
    el('h3', {text: 'Last scan'}), plainBars(here.by_scan_outcome),
  );

  const bindings = unexplained.bindings || [];
  const resolved = bindings.find((r) => r.value === 'resolved');
  const unread = sumOf(bindings) - (resolved ? Number(resolved.n) : 0);

  detailPane.replaceChildren(
    el('h2', {text: 'What is unexplained'}),
    ...explained(`${unread.toLocaleString()} of ${sumOf(bindings).toLocaleString()} words in names `
      + 'have no settled meaning.',
      'Not read yet, no evidence found and conflicting evidence are three different states. '
      + 'Ingence never adds them together.'),
    el('h3', {text: 'Words in names'}),
    plainBars(bindings, {accent: 'var(--doubt)'}),
    el('h3', {text: 'Questions about files and folders'}),
    plainBars(unexplained.entries, {accent: 'var(--doubt)'}),
    el('h3', {text: 'Rules that could not decide'}),
    plainBars(unexplained.rule_failures, {accent: 'var(--doubt)'}),
    el('h3', {text: 'What the scan could not read'}),
    plainBars(unexplained.scan_errors, {accent: 'var(--doubt)'}),
    el('h3', {text: 'Links'}),
    plainBars(unexplained.links, {accent: 'var(--later)'}),
    el('h3', {text: 'Results'}),
    plainBars(data.concluded.claims, {accent: 'var(--acquisition)'}),
    el('h3', {text: 'Flags'}),
    plainBars(data.concluded.findings, {accent: 'var(--later)'}),
    ...setAsideHere(data.set_aside),
  );

  /* Which of three worlds each file is in, composed from its stage claim and the workflow the
   * Record declares. Appended after the rest, so a slow join never delays the landing screen. */
  const context = await api('/api/logical-context?limit=1');
  // Only where the page's own half is still shown (the person may have opened something there).
  if (state.view !== 'inventory' || !detailPane.querySelector('h2')
    || detailPane.querySelector('h2').textContent !== 'What is unexplained') return;
  detailPane.append(
    el('h3', {text: 'Place in the workflow'}),
    el('p', {class: 'note', text: context.declared
      ? `Against ${context.workflows.map((w) => w.name).join(', ')}: `
        + context.workflows.map((w) => w.rungs.join(' → ')).join('; ')
      : 'No workflow declared; the default: '
        + context.workflows.map((w) => w.rungs.join(' → ')).join('; ')}),
    plainBars(context.tally, {accent: 'var(--accent)'}),
  );
}

/* What this run left out and what it set aside, with the reason for each (A1, A2).
 *
 * Two different things and they stay apart: a name the walk never counted is not in the Index
 * at all, and a folder a rule read as a program's is indexed, interpreted, and marked. */
function setAsideHere(aside) {
  if (!aside) return [];
  const left = (aside.left_out || []).filter((row) => Number(row.count || 0) > 0);
  const blocks = [el('h3', {text: 'Left out and set aside'})];
  blocks.push(...explained('Files Ingence left out when it listed your files, and folders it read as installed programs.',
    'A file left out is not listed at all. Ingence lists and reads a program\'s folder, and '
    + 'marks it.'));
  if (left.length) {
    blocks.push(table(['why', 'pattern', 'kind', '#'], left.map((row) => el('tr', {}, [
      el('td', {}, [el('span', {class: 'tag plain', title: String(row.reason || ''),
        text: plainValue(row.reason || '')})]),
      el('td', {}, [el('code', {text: String(row.pattern || '')})]),
      el('td', {text: plainValue(row.entry_type || '')}),
      el('td', {class: 'n', text: Number(row.count || 0).toLocaleString()}),
    ]))));
  } else {
    blocks.push(el('p', {class: 'note', text: 'Nothing left out.'}));
  }
  const folders = aside.folders || [];
  if (folders.length) {
    blocks.push(el('p', {class: 'summary'}, [
      `Set aside: ${(aside.outermost || []).length.toLocaleString()} `
      + `program${(aside.outermost || []).length === 1 ? '' : 's'}, `
      + `${folders.length.toLocaleString()} folder${folders.length === 1 ? '' : 's'}, `
      + `${Number(aside.entries_beneath || 0).toLocaleString()} files and folders beneath.`,
    ]));
    blocks.push(el('details', {}, [
      el('summary', {text: 'the folders a rule read as a program\'s'}),
      el('ul', {class: 'ids'}, folders.map((row) => el('li', {}, [
        `${row.path} `, el('code', {text: String(row.rule_id)}), ' ',
        annotateControl(row.folder, row.path),
      ]))),
    ]));
  }
  return blocks;
}

/* ------------------------------------------------------------------ coverage */

/* What the engine has not read yet, and why: what was set aside, what the budget reached by the
 * file type a dive spends on, the extensions nobody has taught the engine, ranked by files, and why
 * a file has no clock. An extension row carries the worth-reading answer, written through the one
 * route that writes. The words nothing binds are taught in the Words section of Advanced, which
 * Coverage links to. */
const WORTH_READING = ['yes', 'no', 'never'];

function worthControl(row, onAnswered, labels = {}) {
  return el('span', {class: 'answers'}, WORTH_READING.map((word) => el('button', {
    class: row.worth_reading === word ? 'active' : '',
    'data-help': 'coverage.worth_reading',
    // The label the Extensions page shows too; the stored word is what is sent.
    text: labels[word] || word,
    onclick: async () => {
      const who = await whoWrites();
      if (!who) return;
      try {
        await write('/api/review/answer', {actor: who, extension: row.extension, worth_reading: word});
        row.worth_reading = word;
        onAnswered();
      } catch (error) {
        await dialogs.failed(error);
      }
    },
  })));
}

const coverageState = {showAnswered: false};

async function showCoverage() {
  states.loading(listPane, 'Reading what has not been read...');
  const data = await api(`/api/coverage${coverageState.showAnswered ? '?answered=show' : ''}`);
  const reached = data.reached;
  const answeredHere = () => {
    // An extension answered on this page leaves the list at once.
    const rows = data.unbound_extensions;
    return coverageState.showAnswered ? rows : rows.filter((row) => !row.worth_reading);
  };
  const render = () => {
    const extensions = answeredHere();
    const hidden = (data.unbound_extensions_answered || 0)
      + (data.unbound_extensions.length - extensions.length);
    // ``fill`` leaves out a part the page does not have; ``replaceChildren`` printed "null".
    fill(listPane,
      el('h2', {text: 'What the time limit reached'}),
      ...explained(`Ingence has read the contents of ${Number(reached.opened).toLocaleString()} of `
        + `${Number(reached.files).toLocaleString()} files.`,
        'Ingence reads contents by file type in turn, so it reaches every type early. The longer it '
        + 'reads, the deeper it reads each type.'),
      /* What a person's extraction settings leave unread, apart from content that cannot be read,
       * each reason in words with its count. */
      reached.set_aside_by_settings ? el('p', {class: 'note set-aside-by-settings',
        text: `${Number(reached.set_aside_by_settings).toLocaleString()} left unread by this `
          + "project's extraction settings: "
          + Object.entries(reached.set_aside_by_settings_because || {})
            .map(([why, count]) => `${Number(count).toLocaleString()} because ${why}`).join('; ')
          + '. Ingence reads their contents once the settings allow it.'}) : null,
      table(['File type', '#Files', '#Opened'], reached.file_types.map((row) => el('tr', {}, [
        el('td', {text: row.file_type}),
        el('td', {class: 'n', text: Number(row.files).toLocaleString()}),
        el('td', {class: 'n', text: Number(row.opened).toLocaleString()}),
      ]))),
      reached.file_types_not_shown
        ? el('p', {class: 'note', text: `${reached.file_types_not_shown} more types not shown.`})
        : '',
      el('h3', {text: 'Extensions no file type names, and whether to read them first'}),
      el('p', {class: 'faint', text: 'Your answer changes the order in which Ingence reads contents. '
        + 'An answer never stops Ingence from reading a file.'}),
      el('p', {class: 'progress'}, [answeredToggle(hidden, coverageState.showAnswered, () => {
        coverageState.showAnswered = !coverageState.showAnswered;
        showCoverage().catch(reportError);
      })]),
      !extensions.length ? el('p', {class: 'nothing', text: hidden
        ? 'Nothing left to review: every unnamed extension is answered.'
        : 'Nothing to review: every extension in this scan is named.'}) : null,
      !extensions.length ? null : table(['Extension', '#Files', '#Opened', 'Read first'], extensions.map(
        (row) => el('tr', {}, [
          el('td', {text: row.extension ? `.${row.extension}` : '(none)'}),
          el('td', {class: 'n', text: Number(row.files).toLocaleString()}),
          el('td', {class: 'n', text: Number(row.opened).toLocaleString()}),
          el('td', {}, [worthControl(row, render, data.worth_labels)]),
        ]))),
    );
    fill(detailPane,
      el('h2', {text: 'Why a file has no clock'}),
      bars(Object.entries(data.clocks).map(([value, n]) => ({value, n})), {accent: 'var(--later)'}),
      el('h3', {text: 'Words that match nothing'}),
      /* What the run read of the names, each count saying what it counts. */
      data.reading_said ? el('p', {class: 'note', text: data.reading_said}) : null,
      /* The table of these words, with the question about each, is the Words section of Advanced,
       * ordered by the files that carry a word. */
      el('p', {class: 'faint'}, ['Words that mean nothing yet are taught in ',
        el('a', {href: '#words', 'data-help': 'coverage.words', text: 'Words'}),
        ', in Advanced. There you say what a word means, and which spellings are the same thing.']),
      ...setAsideHere(data.set_aside),
    );
  };
  render();
}

/* ---------------------------------------------------------------------- ask */

/* Ask: the box in the header, on every page, and the Overview's box open the Ask view
 * (``#ask?q=``), which is ask.js's (``VIEWS.ask``). An old ``#search`` address opens Ask
 * (``VIEW_ALIASES``). The question last asked is kept, so a page that opens Ask without one
 * (``navigate('ask')``) shows it again. */
const askState = {query: ''};

/* Open the Ask view with a question, from the header's box, the Overview's, or a page's link. */
function askFor(question) {
  const typed = String(question || '').trim();
  askState.query = typed;
  return navigate('ask', {query: typed ? new URLSearchParams({q: typed}).toString() : ''});
}

/* Words as they are typed: the words the pool's names use that begin with the last word typed, most
 * used first, and the Dictionary's senses. They are read from the search index's vocabulary alone
 * so that they answer at the speed of typing. A word chosen completes the last one. A newer
 * keystroke stops an older request. Enter opens Ask. */
const suggestState = {controller: null, timer: 0, words: [], chosen: -1};

/* The box is a combobox (it suggests words as it is typed in), so the list it controls exists
 * from the start, hidden, and `aria-expanded` says whether it is shown: a reader of the page
 * never meets an `aria-controls` that names nothing. */
function suggestList() {
  let list = document.getElementById('search-suggest');
  if (!list) {
    list = el('ul', {id: 'search-suggest', role: 'listbox', 'aria-label': 'Words the names use'});
    list.hidden = true;
    document.getElementById('search-slot').appendChild(list);
  }
  return list;
}

function closeSuggest() {
  const list = document.getElementById('search-suggest');
  if (list) list.hidden = true;
  suggestState.words = [];
  suggestState.chosen = -1;
  if (searchBox) {
    searchBox.removeAttribute('aria-activedescendant');
    searchBox.setAttribute('aria-expanded', 'false');
  }
}

function chooseSuggestion(word) {
  const typed = searchBox.value;
  const cut = typed.search(/\S+$/);
  searchBox.value = `${cut >= 0 ? typed.slice(0, cut) : typed}${word} `;
  closeSuggest();
  searchBox.focus();
}

function showSuggestions(prefix, found) {
  const list = suggestList();
  const words = (found.words || []).slice(0, 8);
  suggestState.words = words.map((word) => word.token);
  suggestState.chosen = -1;
  if (!words.length || searchBox.value.split(/\s+/).pop() !== prefix) {
    closeSuggest();
    return;
  }
  list.replaceChildren(...words.map((word, index) => el('li', {
    id: `search-suggest-${index}`, role: 'option', 'aria-selected': 'false',
    onmousedown: (event) => { event.preventDefault(); chooseSuggestion(word.token); },
  }, [el('span', {text: word.token}), ' ',
    el('span', {class: 'faint', text: `${Number(word.files || 0).toLocaleString()} name${word.files === 1 ? '' : 's'}`
      + (word.term && word.term !== word.token ? ` · ${String(word.term).replace(/_/g, ' ')}` : '')
      + (word.kind ? ` · ${word.kind}` : '')})])));
  list.hidden = false;
  searchBox.setAttribute('aria-expanded', 'true');
}

function askSuggestions() {
  const prefix = (searchBox.value.match(/(\S+)$/) || [])[1] || '';
  if (suggestState.controller) suggestState.controller.abort();
  if (prefix.length < 2) {
    closeSuggest();
    return;
  }
  const controller = new AbortController();
  suggestState.controller = controller;
  fetch(`/api/search/suggest?prefix=${encodeURIComponent(prefix)}`, {signal: controller.signal})
    .then((response) => (response.ok ? response.json() : null))
    .then((found) => { if (found && !controller.signal.aborted) showSuggestions(prefix, found); })
    .catch(() => null);
}

const searchBox = document.getElementById('search-box');
if (searchBox) {
  searchBox.setAttribute('role', 'combobox');
  searchBox.setAttribute('aria-autocomplete', 'list');
  searchBox.setAttribute('aria-controls', 'search-suggest');
  searchBox.setAttribute('aria-expanded', 'false');
  suggestList();
  searchBox.addEventListener('input', () => {
    window.clearTimeout(suggestState.timer);
    suggestState.timer = window.setTimeout(askSuggestions, 90);
  });
  searchBox.addEventListener('blur', () => window.setTimeout(closeSuggest, 120));
  searchBox.addEventListener('keydown', (event) => {
    const open = suggestState.words.length && !suggestList().hidden;
    if (open && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault();
      const count = suggestState.words.length;
      suggestState.chosen = (suggestState.chosen + (event.key === 'ArrowDown' ? 1 : count - 1) + (suggestState.chosen < 0 && event.key === 'ArrowUp' ? 1 : 0)) % count;
      [...suggestList().children].forEach((item, index) => item.setAttribute('aria-selected', String(index === suggestState.chosen)));
      searchBox.setAttribute('aria-activedescendant', `search-suggest-${suggestState.chosen}`);
      return;
    }
    if (open && event.key === 'Tab' && suggestState.chosen >= 0) {
      event.preventDefault();
      chooseSuggestion(suggestState.words[suggestState.chosen]);
      return;
    }
    if (event.key === 'Escape') {
      closeSuggest();
      return;
    }
    if (event.key !== 'Enter') return;
    if (open && suggestState.chosen >= 0) {
      event.preventDefault();
      chooseSuggestion(suggestState.words[suggestState.chosen]);
      return;
    }
    closeSuggest();
    /* The words asked for before Enter are not drawn over the page when they come late. */
    window.clearTimeout(suggestState.timer);
    if (suggestState.controller) suggestState.controller.abort();
    askFor(searchBox.value);
  });
  /* The "/" key that focuses this box is keys.js's, with the other keys of every page. */
}

/* ------------------------------------------------------------------- history */

/* Going back to an earlier run shows its preview first: which decisions made since would be set
 * aside, which brought back, which Dashboard settings change back, which rule settings stay. A
 * restore writes only that exact preview. The page sends the preview's digest, and the server
 * refuses if anything changed since it was shown. Nothing is deleted: the decisions set aside are
 * withdrawn by one event in the Record, which a later restore can bring back. */
/* A decision's kind as a person counts it: one and more. */
const DECISION_WORDS = {confirm: ['confirmation', 'confirmations'],
  reject: ['rejection', 'rejections'], merge: ['merge', 'merges'], split: ['split', 'splits'],
  assign: ['meaning chosen', 'meanings chosen'], define: ['meaning given', 'meanings given'],
  override: ['rule setting', 'rule settings'], annotate: ['note on a folder', 'notes on folders'],
  note: ['note', 'notes'], withdraw: ['going back', 'goings back']};

function countedKinds(held) {
  const parts = Object.entries(held || {}).filter(([, n]) => Number(n) > 0)
    .map(([kind, n]) => {
      const [one, many] = DECISION_WORDS[kind] || [label(kind), label(kind)];
      return `${Number(n).toLocaleString()} ${Number(n) === 1 ? one : many}`;
    });
  return parts.length ? parts.join(', ') : 'none';
}

/* The preview a pane last asked for, so that a page which would ask again for the same run
 * (History opening on a run named in the address) can see it is already there. */
let lastInspection = {pane: null, runId: '', turn: 0};

/* Whether ``pane`` is asking for, or showing, the restore preview of ``runId`` and nothing has
 * claimed the pane since. */
function inspecting(pane, runId) {
  return lastInspection.pane === pane && lastInspection.runId === runId
    && turns.owns(pane, lastInspection.turn);
}

async function showRunInspector(runId, pane, named = '') {
  const turn = turns.claim(pane);
  lastInspection = {pane, runId, turn};
  states.loading(pane, 'Working out what restoring it would change...');
  let preview;
  let listed = [];
  try {
    /* The runs' names as History lists them: a reload or a shared address opens the preview with no
     * name in hand. */
    [preview, listed] = await Promise.all([
      api(`/api/v1/restore/preview?run=${encodeURIComponent(runId)}`),
      api('/api/timeline').then((found) => found.runs || []).catch(() => []),
    ]);
  } catch (error) {
    if (turns.owns(pane, turn)) {
      // A preview that could not be read is not one that is there: the page may ask again.
      lastInspection = {pane: null, runId: '', turn: 0};
      states.failed(pane, error, () => showRunInspector(runId, pane, named));
    }
    return;
  }
  if (!turns.owns(pane, turn)) return;
  const nameOf = (id) => {
    const run = listed.find((each) => each.run_id === id);
    return run ? String(run.label || id).split(',')[0] : '';
  };
  setFocus({kind: 'run', id: runId, label: named || nameOf(runId)});
  const settings = Object.entries(preview.settings || {});
  /* Rule settings a restore returns (an appended revision), as rule, setting, now -> then. */
  const reverted = Object.entries(preview.rules_reverted || {}).flatMap(([rule, parameters]) =>
    Object.entries(parameters).map(([name, [now, then]]) =>
      `${rule}, ${name}: ${JSON.stringify(now)} → ${JSON.stringify(then)}`));
  if (preview.current_run_id === preview.run_id && !preview.withdraw_count && !settings.length
    && !reverted.length) {
    fill(pane, el('h2', {text: 'The current scan', title: preview.run_id}),
      el('p', {class: 'lead', text: 'Nothing to go back to.'}));
    return;
  }
  /* Runs by their names where the page knows them, the identifier in the tooltip. */
  const going = named || nameOf(preview.run_id)
    || (focusHeld.kind === 'run' && focusHeld.id === runId && focusHeld.label) || preview.run_id;
  const status = el('p', {class: 'note', role: 'status'});
  const summaryRows = [
    ['Going back to', going],
    ['Now showing', preview.current_run_id ? nameOf(preview.current_run_id) || preview.current_run_id : 'no scan'],
    /* A position in the Record, never a count: notes and earlier withdrawals are numbered too. */
    ['Answer log read up to', `number ${preview.watermark.toLocaleString()} (known from `
      + `${label(preview.watermark_from)})`],
    ['Set aside', preview.withdraw_count
      ? `${preview.withdraw_count.toLocaleString()} answer${preview.withdraw_count === 1 ? '' : 's'} given since `
        + `(${countedKinds(preview.set_aside)})` : 'nothing'],
    ['Brought back', countedKinds(preview.brought_back)],
    ['Settings changed back', settings.length ? settings.map(([name, [now, then]]) =>
      `${name}: ${JSON.stringify(now)} → ${JSON.stringify(then)}`).join('; ') : 'none'],
    ['Rule settings changed back', reverted.length ? reverted.join('; ') : 'none'],
    ['Rule settings kept', (preview.rules_kept || []).length ? preview.rules_kept.join(', ')
      : 'none changed since'],
  ];
  const restoreButton = el('button', {class: 'answer', type: 'button', 'data-help': 'history.restore',
    onclick: async () => {
      const sure = await dialogs.choose({title: 'Restore exactly this?',
        text: `Every page will read ${going} again. ${preview.withdraw_count
          ? `${preview.withdraw_count} answer${preview.withdraw_count === 1 ? ' is' : 's are'} set aside, `
            + 'under your name. Nothing is deleted: restoring a later scan brings them back.'
          : 'No answer is set aside.'}`,
        yes: 'Restore this scan'});
      if (!sure) return;
      const who = await whoWrites();
      if (!who) return;
      restoreButton.disabled = true;
      try {
        const done = await write('/api/workspace/restore', {run: preview.run_id, actor: who,
          previewed: preview.digest});
        status.className = 'note kept';
        status.textContent = `Restored: every page reads ${going} now`
          + `${done.withdrawn ? `, and ${done.withdrawn} decision${done.withdrawn === 1 ? ' was' : 's were'} `
            + 'set aside' : ''}.`;
        status.title = done.restored_to;
        refreshWaiting();
        // The timeline beside it says which run every page reads now.
        if (state.view === 'changes') {
          showChanges().then(() => fill(pane, el('h2', {text: 'Restored'}), status)).catch(reportError);
        }
      } catch (error) {
        restoreButton.disabled = false;
        status.className = 'note refused';
        status.textContent = String(error.message || error);
        pane.append(el('button', {class: 'link', type: 'button', text: 'look at the preview again',
          onclick: () => showRunInspector(runId, pane, named)}));
      }
    }}, [el('strong', {text: 'restore exactly this'})]);
  fill(pane,
    el('h2', {text: 'Going back to this scan', title: preview.run_id}),
    el('p', {class: 'lead', text: 'Nothing is written until you confirm.'}),
    el('dl', {class: 'inspector-facts'}, summaryRows.flatMap(([term, value]) => [
      el('dt', {text: term}), el('dd', {text: String(value)})])),
    restoreButton,
    status,
  );
}

/* What the job ledger holds: every job that applied an answer or a commit, newest first, in its
 * state (waiting to start, running, done, failed, or interrupted because its worker stopped and it
 * was started again), with what happened to it. */
const JOB_STATE_WORDS = {queued: 'Waiting to start', running: 'Running', done: 'Done',
  failed: 'Did not finish', interrupted: 'Interrupted', cancelled: 'Stopped'};

/* A moment as a person reads it, in this machine's own time; the stamp as written where it is
 * not one. */
function momentWords(stamp) {
  const moment = new Date(stamp);
  return Number.isNaN(moment.getTime()) ? String(stamp || '')
    : moment.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
}

function jobsSection() {
  const holder = el('div', {class: 'jobs', 'data-help': 'history.jobs'});
  api('/api/v1/jobs?limit=20').then((found) => {
    if (!found.jobs.length) return;
    fill(holder,
      el('h3', {text: 'Recent jobs'}),
      el('ol', {class: 'timeline'}, found.jobs.map((job) => el('li', {class: `run job-${job.state}`}, [
        el('p', {class: 'run-head'}, [
          el('span', {class: `tag ${job.state === 'failed' ? 'refused' : job.state === 'done' ? 'now' : 'plain'}`,
            text: JOB_STATE_WORDS[job.state] || job.state}), ' ',
          job.kind === 'rerun' ? 'Re-run' : job.kind === 'first_run' ? 'First scan'
            : `Meaning of ${job.spellings.map((word) => `"${word}"`).join(', ') || 'a word'}`,
          el('span', {class: 'faint', text: ` · asked ${momentWords(job.created_at)}`}),
        ]),
        el('p', {class: 'faint', title: job.produced_run || '',
          text: `${job.operations.map(plainValue).join(', then ')}`
          + `${job.attempts > 1 ? ` · ${job.attempts} attempts` : ''}`
          + `${job.produced_run ? ' · made a new scan' : ''}`}),
        job.error ? el('p', {class: 'note refused', text: job.error}) : null,
        job.events.length ? el('details', {}, [el('summary', {text: 'what happened'}),
          el('ul', {class: 'ids'}, job.events.map((event) => el('li', {},
            [`${momentWords(event.at)}: ${JOB_STATE_WORDS[event.state] || event.state}${event.message ? `, ${event.message}` : ''}`])))])
          : null,
      ]))),
    );
  }).catch(() => null);
  return holder;
}

/* ------------------------------------------------------------------- graph */

/* The Graph page is graph.js's: one folder or file a person chooses, what directly surrounds it,
 * and every line's evidence. This is the one call into it. */
async function showGraph() {
  await window.showGraphPage();
}

/* The Graph workspace: Wheel, Lineage and Timeline, a React page built into web/explorer/, mounted
 * into the list pane and unmounted on leaving it. */
async function showExplorer() {
  if (!window.ingenceExplorer) {
    fill(listPane, el('p', {class: 'empty', text: 'The Explorer did not load: its script is missing from this installation.'}));
    return;
  }
  window.ingenceExplorer.mount(listPane);
}

/* ------------------------------------------------------------------- wiring */

/* The views laid out with a wider list, where a row carries its answer. */
const REVIEW_VIEWS = new Set(['review', 'classes', 'claims', 'candidates', 'extensions']);

/* The views whose right half is part of the page itself: the object under investigation is opened
 * there only on asking ("open", under the header). */
const OWN_DETAIL = new Set(['inventory', 'coverage', 'found', 'sheet', 'manifest', 'topics']);

/* The views laid out as a list beside a card of a fixed width (rules.js adds Rules; Overlap's By
 * year mode sets the layout itself, overlap.js). */
const YEARS_VIEWS = new Set();

/* The views with nothing beside their list, laid out across the whole page. */
const SINGLE_VIEWS = new Set(['rules', 'graph', 'explorer']);

/* The review pages are review.js's, one generic page per sheet kind. */
const VIEWS = {
  // The Overview and Checks are overview.js's, which replaces these as it loads.
  overview: () => notLoaded('The Overview'),
  checks: () => notLoaded('Checks'),
  // Ask is ask.js's, which replaces this as it loads.
  ask: () => notLoaded('Ask'),
  // Overlap is overlap.js's, which replaces this as it loads. #years opens its By year mode.
  overlap: () => notLoaded('Overlap'),
  topics: () => notLoaded('Collections'),
  found: () => notLoaded('No analysis found'),
  sheet: () => notLoaded('The question list'),
  manifest: () => notLoaded('What changed'),
  ladders: () => notLoaded('Folder levels'),
  words: () => notLoaded('Words'),
  inventory: showInventory,
  graph: showGraph,
  explorer: showExplorer,
  review: () => showSheet('questions'),
  classes: () => showSheet('classes'),
  claims: () => showSheet('claims'),
  candidates: () => showSheet('candidates'),
  extensions: () => showSheet('extensions'),
  programs: showPrograms,
  changes: showChanges,
  coverage: showCoverage,
  rules: () => notLoaded('Rules'),
  dictionary: showDictionarySection,
};

/* Addresses from earlier versions of the Dashboard. #parameters, #engine and #advanced open Rules,
 * the first section of Advanced. #search opens Ask, and #collections opens the topics view. A
 * page's name opens its first section. #years opens Overlap, and the name asked for is kept in
 * ``state.asked`` so overlap.js opens it in its By year mode. */
const VIEW_ALIASES = {parameters: 'rules', engine: 'rules', advanced: 'rules', search: 'ask',
  collections: 'topics', years: 'overlap'};

/* A section whose script did not load says so, in one line, rather than failing. */
async function notLoaded(name) {
  fill(listPane, el('h2', {text: name}),
    el('p', {class: 'nothing', text: `${name} did not load: its script is missing from this installation. Reload the page.`}));
}

function viewNamed(named) {
  const view = VIEW_ALIASES[named] || named;
  return VIEWS[view] ? view : '';
}

/* The Dictionary section of Advanced (Engine) is dictionary.js's page, which replaces this in VIEWS
 * as it loads. This one is shown only where that script did not load. */
async function showDictionarySection() {
  fill(listPane, el('h2', {text: 'Dictionary'}),
    el('p', {class: 'nothing', text: 'The Dictionary did not load. Reload the page.'}));
}

/* Six pages. The Dashboard leads with the Overview and the experiments and shows one thing at a
 * time: Overview, Overlap, Experiments, Checks, History and Advanced. Advanced holds every other
 * view. A view's address (`#programs`) opens it, and opens the page that holds it. Overlap holds
 * the By year rows (years.js). Settings (settings.js), Folder levels (years.js) and the Dictionary
 * (dictionary.js) join Advanced as they load. ``ask`` sits on the Overview, which holds the Ask
 * box, with no section button of its own. */
const PAGES = {
  overview: ['overview', 'ask'],
  overlap: ['overlap'],
  experiments: ['experiments', 'topics'],
  checks: ['checks'],
  history: ['changes', 'manifest'],
  advanced: ['rules', 'dictionary', 'settings', 'ladders', 'inventory', 'coverage', 'classes', 'candidates',
    'extensions', 'programs', 'claims', 'review', 'words', 'found', 'explorer', 'graph', 'map'],
};
/* An old page name, as a saved focus or a script that joins a page may carry it
 * (`PAGES.engine.push('settings')` in settings.js, years.js's Folder levels): each is its new page. */
const PAGE_ALIASES = {parameters: 'advanced', engine: 'advanced', explore: 'experiments', graph: 'advanced',
  questions: 'advanced'};
for (const [old, now] of Object.entries(PAGE_ALIASES)) {
  Object.defineProperty(PAGES, old, {get: () => PAGES[now], enumerable: false, configurable: true});
}
const PAGE_OF = {};
/* Which page each view is on, from PAGES: made again once every script has joined its sections,
 * each view once, a page's name of before read as its new page. */
function placeViews() {
  for (const view of Object.keys(PAGE_OF)) delete PAGE_OF[view];
  for (const [page, views] of Object.entries(PAGES)) {
    PAGES[page] = [...new Set(views)];
    for (const view of PAGES[page]) PAGE_OF[view] = page;
  }
}
placeViews();
const lastView = {};

/* ------------------------------------------------------------ the object under investigation */

/* What a person is looking at is kept across pages and reloads: an entry (a file or a folder), a
 * question, a run or a word. It is named in the address (``?entry=<id>``, ``?question=<target>``,
 * ``?run=<id>``, ``?word=<word>``; the Graph page's own ``?center=`` names an entry too) and kept
 * for the browser tab's session, so moving from Experiments to Advanced to History keeps it, and a
 * reload keeps it. A line under the header says what it is, with a way to open it and a way to put
 * it down.
 *
 * What is looked at belongs to the page it was set on. Each page keeps its own: on another page the
 * line is hidden, the panel beside the page does not open it and the address does not name it, and
 * returning to the page brings it back. The Graph page, with nothing of its own, centers on the
 * file or folder the page before it was looking at. */
const FOCUS_KINDS = ['entry', 'question', 'run', 'word'];
const FOCUS_KEY = 'ingence.focus';
const NOTHING_LOOKED_AT = {kind: '', id: '', label: ''};
let focusHeld = {...NOTHING_LOOKED_AT};
/* What each page is looking at, by page; focusHeld is the current page's. A focus saved under an
 * old page name is read through PAGE_ALIASES (above). */
let focusByPage = {};

function focusFromAddress() {
  const query = new URLSearchParams(location.search);
  for (const kind of FOCUS_KINDS) {
    const id = query.get(kind);
    if (id) return {kind, id, label: ''};
  }
  const center = query.get('center');
  if (center) return {kind: 'entry', id: center, label: ''};
  // The Graph workspace names its selection (sel) and its center (focus); a bin, a gathered group,
  // a line, a group, an arranged value or a timeline bin is the workspace's own and not something
  // looked at elsewhere, and then the center is what is being looked at.
  const own = /^(bin|more|edge|group|agg|tbin|v|overlay):/;
  const selected = [query.get('sel'), query.get('focus')].find((id) => id && !own.test(id));
  return selected ? {kind: 'entry', id: selected, label: ''} : null;
}

function lookedAt(kept) {
  return kept && FOCUS_KINDS.includes(kept.kind) && kept.id
    ? {kind: kept.kind, id: String(kept.id), label: String(kept.label || '')} : null;
}

/* What every page was looking at, kept for the tab's session. A focus saved as one for every page
 * is read as the focus of the page that opens first. */
function focusFromSession(openingPage) {
  try {
    const kept = JSON.parse(sessionStorage.getItem(FOCUS_KEY) || 'null');
    if (kept && kept.pages && typeof kept.pages === 'object') {
      const pages = {};
      for (const [page, focus] of Object.entries(kept.pages)) {
        const held = lookedAt(focus);
        if (held) pages[PAGE_ALIASES[page] || page] = held;
      }
      return pages;
    }
    const held = lookedAt(kept);
    return held ? {[openingPage]: held} : {};
  } catch (error) {
    return {};
  }
}

/* Hold what the current page is looking at, and keep it for the tab's session. */
function holdFocus(focus, page = PAGE_OF[state.view]) {
  const next = lookedAt(focus) || {...NOTHING_LOOKED_AT};
  if (next.kind === focusHeld.kind && next.id === focusHeld.id && !next.label) next.label = focusHeld.label;
  focusHeld = next;
  if (next.kind === 'entry' && next.id) lastEntryLooked = {...next};
  if (page) {
    if (next.id) focusByPage[page] = next;
    else delete focusByPage[page];
  }
  try {
    if (Object.keys(focusByPage).length) {
      sessionStorage.setItem(FOCUS_KEY, JSON.stringify({pages: focusByPage}));
    } else sessionStorage.removeItem(FOCUS_KEY);
  } catch (error) {
    /* A browser that keeps nothing still keeps the address. */
  }
}

/* What a page shows as looked at on arriving: its own. On the Graph views (Connections, Explorer,
 * in Advanced) with nothing of its own, it shows the file or folder the page before it was looking
 * at, else the last one looked at on any page (Advanced is often reached through Rules). */
const CENTERED_VIEWS = new Set(['graph', 'explorer']);
let lastEntryLooked = null;

function focusOfPage(page, from, view) {
  const own = focusByPage[page];
  if (own && own.id) return own;
  const before = from ? focusByPage[from] : null;
  const carried = before && before.kind === 'entry' ? before : lastEntryLooked;
  if (CENTERED_VIEWS.has(view) && carried && carried.kind === 'entry') {
    focusByPage[page] = {...carried};
    return focusByPage[page];
  }
  return {...NOTHING_LOOKED_AT};
}

function setFocus(focus) {
  holdFocus(focus);
  writeAddress(state.view, {push: false});
  showFocus();
}

const FOCUS_WORDS = {entry: 'Looking at', question: 'Looking at the question', run: 'Looking at',
  word: 'Looking at'};

function showFocus() {
  const line = document.getElementById('focus');
  if (!line) return;
  if (!focusHeld.id) {
    line.hidden = true;
    line.replaceChildren();
    return;
  }
  const label = focusHeld.label || {word: `the word "${focusHeld.id}"`, run: `run ${focusHeld.id}`}[focusHeld.kind]
    || focusHeld.id;
  // An entry named only by its identifier (a link, a reload) is named by the run once read.
  if (!focusHeld.label && focusHeld.kind === 'entry') {
    const asked = focusHeld.id;
    api(`/api/v1/entry?id=${encodeURIComponent(asked)}`).then((found) => {
      if (focusHeld.id === asked && !focusHeld.label) {
        focusHeld.label = found.entry.name || found.entry.path;
        showFocus();
      }
    }).catch(() => null);
  }
  line.hidden = false;
  fill(line,
    `${FOCUS_WORDS[focusHeld.kind]}: `, el('strong', {text: label}), ' ',
    el('button', {class: 'link', type: 'button', 'data-help': 'focus.open', text: 'open',
      onclick: () => openFocus(detailPane)}),
    focusHeld.kind === 'entry' && window.graphButton && state.view !== 'graph' ? ' · ' : null,
    focusHeld.kind === 'entry' && window.graphButton && state.view !== 'graph'
      ? window.graphButton(focusHeld.id) : null,
    ' · ',
    el('button', {class: 'link', type: 'button', 'data-help': 'focus.clear', text: 'put it down',
      onclick: () => setFocus(null)}),
  );
}

/* Show the object under investigation in a pane, through the one inspector. */
function openFocus(pane) {
  if (!focusHeld.id || !window.inspector) return false;
  if (focusHeld.kind === 'entry') window.inspector.showEntry(focusHeld.id, pane, {keepFocus: true});
  else if (focusHeld.kind === 'word') window.inspector.showWord(focusHeld.id, pane);
  else if (focusHeld.kind === 'run') showRunInspector(focusHeld.id, pane).catch((error) => states.failed(pane, error));
  else if (focusHeld.kind === 'question') {
    if (state.view === 'review') return false;
    fill(pane, el('p', {class: 'note', text: `The question ${focusHeld.label || focusHeld.id} is in Questions, in Advanced.`}),
      el('button', {class: 'answer quiet', type: 'button', onclick: () => navigate('review')},
        [el('strong', {text: 'go to it'})]));
  }
  return true;
}

/* ------------------------------------------------------------------ navigation */

/* One navigation function: every page change goes through navigate(). It runs what must happen on
 * leaving a page (the workspace saves what is typed: onLeave), writes the address, and draws the
 * page. showView is kept as its name for the scripts that call it. */
const leaving = [];

function onLeave(hook) {
  leaving.push(hook);
}

/* The address: ``?<focus>=<id>#<view>``, or the Graph page's own query, which names its center. */
function writeAddress(view, {push = false} = {}) {
  if (!view) return;
  let query = new URLSearchParams();
  if (view === 'graph') {
    query = new URLSearchParams(location.hash === '#graph' ? location.search : '');
    if (!query.get('center') && focusHeld.kind === 'entry') query.set('center', focusHeld.id);
  } else if (view === 'explorer') {
    query = new URLSearchParams(location.hash === '#explorer' ? location.search : '');
    if (!query.get('sel') && !query.get('focus') && focusHeld.kind === 'entry') query.set('sel', focusHeld.id);
  } else if (focusHeld.id) {
    query.set(focusHeld.kind, focusHeld.id);
  }
  const text = query.toString();
  const target = `${location.pathname}${text ? `?${text}` : ''}#${view}${state.hashQuery ? `?${state.hashQuery}` : ''}`;
  if (target === `${location.pathname}${location.search}${location.hash}`) return;
  if (push) history.pushState({view}, '', target);
  else history.replaceState({view}, '', target);
}

/* The view an address names after its ``#``, and what follows the view's name: ``#ask?q=GFP`` is
 * the view ``ask`` and the query ``q=GFP`` (Ask's question, the goal Experiments shows, the check
 * Checks opens at). */
function hashParts(hash = location.hash) {
  const text = String(hash || '').replace(/^#/, '');
  const at = text.indexOf('?');
  return {view: at < 0 ? text : text.slice(0, at), query: at < 0 ? '' : text.slice(at + 1)};
}

/* What the address says after the view's name, as a URLSearchParams. */
function viewQuery() {
  return new URLSearchParams(state.hashQuery || '');
}

function navigate(view, {push = true, focus, query} = {}) {
  // The name asked for, before its alias (#years asks for Overlap's By year mode).
  state.asked = view;
  view = viewNamed(view) || 'inventory';
  // Ask opened without a question shows the question last asked.
  if (view === 'ask' && query == null && askState.query) {
    query = new URLSearchParams({q: askState.query}).toString();
  }
  // Whether something claimed the detail pane before the page began (an entry opened in the first
  // fraction of a second): the Years view's first draw leaves such an entry alone (years.js).
  const detailEarly = !state.view && (turns.held.get(detailPane) || 0) > 0;
  for (const hook of leaving) {
    try {
      hook(view);
    } catch (error) {
      /* A hook that fails never stops the page changing. */
    }
  }
  const leavingPage = state.view ? PAGE_OF[state.view] : '';
  const page = PAGE_OF[view];
  // Leaving the Graph page, its center is what that page is looking at.
  const fromAddress = focusFromAddress();
  if (leavingPage && fromAddress
    && (fromAddress.kind !== focusHeld.kind || fromAddress.id !== focusHeld.id)) {
    holdFocus(fromAddress, leavingPage);
  }
  // Each page its own: the page arrived at shows what it was looking at.
  if (focusHeld.kind === 'entry' && focusHeld.id) lastEntryLooked = {...focusHeld};
  if (page !== leavingPage || (CENTERED_VIEWS.has(view) && !focusHeld.id)) {
    focusHeld = focusOfPage(page, page !== leavingPage ? leavingPage : '', view);
  }
  if (focus !== undefined) holdFocus(focus, page);
  lastView[page] = view;
  for (const other of document.querySelectorAll('#pages button')) {
    other.classList.toggle('active', other.dataset.page === page);
    if (other.dataset.page === page) other.setAttribute('aria-current', 'page');
    else other.removeAttribute('aria-current');
  }
  for (const group of document.querySelectorAll('nav.sections')) {
    group.hidden = group.dataset.sectionsOf !== page;
    // A page with one section needs no bar of its own.
    group.classList.toggle('single', group.querySelectorAll('button[data-view]').length <= 1);
  }
  for (const other of document.querySelectorAll('nav.sections button')) {
    other.classList.toggle('active', other.dataset.view === view);
  }
  state.view = view;
  state.hashQuery = query ? String(query).replace(/^\?/, '') : '';
  showWhatWaits();
  // The view is named in the address, so a link or a bookmark opens it, and the object under
  // investigation beside it.
  writeAddress(view, {push});
  showFocus();
  document.querySelector('main').classList.toggle('review-layout', REVIEW_VIEWS.has(view));
  // A page with nothing to show beside its list takes the whole width (the rules).
  document.querySelector('main').classList.toggle('single-layout', SINGLE_VIEWS.has(view));
  document.querySelector('main').classList.toggle('years-layout', YEARS_VIEWS.has(view));
  // The Inventory and Coverage pages' right half is their own; elsewhere it shows what is being
  // looked at, through the one inspector.
  if (OWN_DETAIL.has(view) || !openFocus(detailPane)) {
    fill(detailPane, el('p', {class: 'empty', text: 'Nothing selected.'}));
  }
  state.detailTurn = turns.held.get(detailPane) || 0;
  state.detailEarly = detailEarly;
  states.loading(listPane);
  return drawView(view);
}

function showView(view) {
  navigate(view);
}

// Leaving the Graph workspace unmounts it, so nothing of it keeps running behind another page.
onLeave((next) => {
  if (next !== 'explorer' && window.ingenceExplorer) window.ingenceExplorer.unmount();
});

for (const button of document.querySelectorAll('nav.sections button')) {
  button.addEventListener('click', () => navigate(button.dataset.view));
}
/* A page's tab opens the section last opened there, where it has a button of its own (Ask, opened
 * from the Overview, has none, so the tab opens the Overview itself), else the page's first. */
for (const button of document.querySelectorAll('#pages button')) {
  button.addEventListener('click', () => {
    const page = button.dataset.page;
    const last = lastView[page];
    const named = last && document.querySelector(`nav.sections button[data-view="${last}"]`);
    navigate(named ? last : PAGES[page][0]);
  });
}

/* How many rows wait on each review section, on its button. A button with nothing waiting is quiet
 * (a lighter gray, a dashed outline and "(0)"), and showWhatWaits hides it unless it is the section
 * open. The section a person is on sets its own count from what it just read. All of them are read
 * again when a definition takes effect. */
const VIEW_OF = {questions: 'review', classes: 'classes', claims: 'claims',
  candidates: 'candidates', extensions: 'extensions', programs: 'programs'};

const waitingCounts = {};

function setWaiting(kind, count) {
  const button = document.querySelector(`nav.sections button[data-waiting="${kind}"]`);
  if (!button || count == null) return;
  waitingCounts[kind] = Number(count);
  let badge = button.querySelector('.count');
  if (!badge) {
    badge = el('span', {class: 'count'});
    button.append(badge);
  }
  badge.textContent = ` (${Number(count).toLocaleString()})`;
  button.classList.toggle('quiet', Number(count) === 0);
  button.title = Number(count) === 0 ? 'Nothing waiting here' : `${count} waiting`;
  showWhatWaits();
  // The page button carries a dot where anything waits in its sections.
  const page = PAGE_OF[VIEW_OF[kind]];
  const total = PAGES[page]
    .map((view) => Object.entries(VIEW_OF).find(([, viewName]) => viewName === view))
    .filter(Boolean)
    .reduce((sum, [name]) => sum + (waitingCounts[name] || 0), 0);
  pageDot(page, total);
}

/* A dot on the page's tab, not a number. The dot is in the problem color while anything waits, and
 * absent when nothing does. The number is the tab's tooltip and part of its accessible name. While
 * the server is still counting, the dot is quiet (gray, hollow) and the name says it is
 * counting. */
function pageDot(page, total, {counting = false} = {}) {
  const pageButton = document.querySelector(`#pages button[data-page="${page}"]`);
  if (!pageButton) return;
  if (!pageButton.dataset.name) pageButton.dataset.name = pageButton.textContent.trim();
  const name = pageButton.dataset.name;
  let dot = pageButton.querySelector('.dot');
  if (!counting && !total) {
    if (dot) dot.remove();
    pageButton.removeAttribute('aria-label');
    pageButton.title = 'Nothing waiting';
    return;
  }
  if (!dot) {
    dot = el('span', {class: 'dot', 'aria-hidden': 'true'});
    pageButton.append(dot);
  }
  dot.classList.toggle('counting', counting);
  const said = counting ? 'counting what waits' : `${Number(total).toLocaleString()} waiting`;
  pageButton.setAttribute('aria-label', `${name}, ${said}`);
  pageButton.title = counting ? 'Counting what waits' : said;
}

/* Empty sections are hidden, not greyed out. A section that counts what waits on it is hidden while
 * nothing does, unless it is the one open (an old address can still open it). A heading in Advanced
 * with no section left is hidden, and so is a page with no section left. While the server counts, a
 * section shows. */
function showWhatWaits() {
  for (const button of document.querySelectorAll('nav.sections button[data-waiting]')) {
    const count = waitingCounts[button.dataset.waiting];
    button.hidden = count === 0 && button.dataset.view !== state.view;
  }
  for (const group of document.querySelectorAll('nav.sections .section-group')) {
    group.hidden = ![...group.querySelectorAll('button[data-view]')].some((button) => !button.hidden);
  }
  for (const tab of document.querySelectorAll('#pages button[data-page]')) {
    const sections = document.querySelector(`nav.sections[data-sections-of="${tab.dataset.page}"]`);
    if (!sections) continue;
    const shown = [...sections.querySelectorAll('button[data-view]')].some((button) => !button.hidden);
    tab.hidden = !shown && PAGE_OF[state.view] !== tab.dataset.page;
  }
}

/* What else is read again when the tabs' counts are (the workspace's header: workspace.js). */
const refreshing = [];

function onRefresh(hook) {
  refreshing.push(hook);
}

/* While the server counts, the tabs say so. On a large project the first count takes a minute or
 * more, so the server answers that it is counting, each tab says "counting…" where its number will
 * be, and the page asks again until the numbers come. */
let waitingTimer = null;

function sayCounting() {
  document.querySelectorAll('nav.sections button[data-waiting]').forEach((button) => {
    if (waitingCounts[button.dataset.waiting] != null) return;
    let badge = button.querySelector('.count');
    if (!badge) {
      badge = el('span', {class: 'count'});
      button.append(badge);
    }
    badge.textContent = ' (counting…)';
    button.title = 'Counting what waits here';
    const page = PAGE_OF[VIEW_OF[button.dataset.waiting]];
    if (page) pageDot(page, 0, {counting: true});
  });
}

async function refreshWaiting() {
  try {
    const counts = await api('/api/waiting');
    if (waitingTimer) clearTimeout(waitingTimer);
    waitingTimer = null;
    if (counts.counting) {
      sayCounting();
      waitingTimer = setTimeout(() => refreshWaiting(), 2000);
    } else {
      for (const [kind, count] of Object.entries(counts)) {
        if (VIEW_OF[kind]) setWaiting(kind, count);
      }
    }
  } catch (error) {
    /* The counts are a convenience; a page still opens without them. */
  }
  for (const hook of refreshing) {
    try {
      hook();
    } catch (error) {
      /* The header's parts are conveniences too. */
    }
  }
}

function reportError(error) {
  states.failed(listPane, error, () => navigate(state.view, {push: false}));
}

/* ------------------------------------------------------------------ the frame */

/* What the header, the footer's states and the first draw of a view ask the engine once
 * (``/api/v1/shell``): the project's name and where it is written, whether any rule has run, and
 * what the census counted. The answer is kept for a few seconds, so a run of page changes asks
 * once. It is asked again, fresh, when a job ends. */
const shellHeld = {read: null, at: 0, value: null};
const SHELL_KEPT_MS = 10000;
const SHELL_WAIT_MS = 20000;

function readShell({fresh = false} = {}) {
  if (!shellHeld.read || fresh || Date.now() - shellHeld.at > SHELL_KEPT_MS) {
    shellHeld.at = Date.now();
    shellHeld.read = api('/api/v1/shell').then((found) => (shellHeld.value = found)).catch((error) => {
      shellHeld.read = null;
      throw error;
    });
  }
  return shellHeld.read;
}

/* The project's name, with Rename: a name a person gave is theirs, in the person's color. The
 * folder's own name, which nobody gave, is quiet. Rename writes the project's own file (the landing
 * page's route), so any computer that opens the project shows the name. */
function showProjectName(shell) {
  const holder = document.getElementById('project-name');
  const text = document.getElementById('project-name-text');
  if (!holder || !text || !shell) return;
  text.textContent = shell.project_name;
  text.classList.toggle('given', shell.name_given);
  text.title = shell.name_given ? shell.project_name
    : `${shell.project_name}: the name of its folder; nobody has named it`;
  holder.hidden = false;
}

function keepProjectName() {
  const rename = document.getElementById('rename-button');
  if (rename) {
    rename.addEventListener('click', async () => {
      try {
        const shell = await readShell();
        const typed = await dialogs.ask({
          title: 'Rename the project',
          text: 'The name is written to the project\u2019s own file, so any computer that opens the '
            + 'project shows it. Nothing it reads is touched.',
          label: 'Name', value: shell.project_name, submit: 'Rename',
          validate: (words) => (words.length > 200 ? 'A name is at most 200 characters.' : ''),
        });
        if (!typed) return;
        await write('/api/landing/rename', {project: shell.project_path, name: typed});
        showProjectName(await readShell({fresh: true}));
      } catch (error) {
        await dialogs.failed(error, 'It was not renamed');
      }
    });
  }
  readShell().then(showProjectName).catch(() => {});
}

/* A folder that cannot be reached: the banner sits above the rows, and the last run still reads
 * beneath it. The red is a border on the dot, never a fill. "Show the last run" folds the banner to
 * a line so the rows have the room. "Check again" asks the engine again (a ``stat`` of each folder,
 * never a read inside it). The page asks when it opens, when the window is looked at again after a
 * while, and when that button is pressed. */
const rootWatch = {strip: false, at: 0};
const ROOT_LOOK_AGAIN_MS = 15000;

function foldersWords(down) {
  return down.map((root) => root.label && root.kind === 'folder' ? `${root.label} (${root.path})` : root.path)
    .join('; ');
}

async function checkRoots({again = false} = {}) {
  const slot = document.getElementById('root-state');
  if (!slot) return;
  rootWatch.at = Date.now();
  let reach;
  try {
    reach = await api('/api/v1/reachability');
  } catch (error) {
    return;  // The engine not answering is said by the page's own error state, not twice.
  }
  const down = reach.roots.filter((root) => !root.reachable);
  if (!down.length) {
    slot.hidden = true;
    slot.replaceChildren();
    rootWatch.strip = false;
    return;
  }
  const one = down.length === 1;
  const where = foldersWords(down);
  const sentence = one ? `The folder it reads is not reachable: ${where}.`
    : `${down.length} of ${reach.roots.length} folders it reads are not reachable.`;
  const published = reach.run_finished_at ? dayWords(reach.run_finished_at) : '';
  const checkAgain = {label: 'Check again', help: 'state.check_again', onclick: () => {
    checkRoots({again: true}).catch(() => {});
  }};
  if (rootWatch.strip) {
    slot.replaceChildren(el('p', {class: 'root-strip', role: 'status'}, [
      el('span', {class: 'state-dot problem', 'aria-hidden': 'true'}),
      el('span', {text: sentence}),
      el('button', {type: 'button', class: 'link', text: 'Details', onclick: () => {
        rootWatch.strip = false;
        checkRoots().catch(() => {});
      }}),
      el('button', {type: 'button', class: 'link', text: 'Check again', onclick: checkAgain.onclick}),
    ]));
  } else {
    slot.replaceChildren(states.say(null, {
      eyebrow: 'Any view \u00b7 unplugged', dot: 'problem', into: true, sentence,
      body: [
        ...(one ? [] : [`Not reachable: ${where}.`]),
        published ? `The last scan is shown as it was published on ${published}.`
          : 'No scan has been published.',
        'Ingence can read nothing again until the drive is back. Reading contents is paused.',
        ...(one && down[0].why ? [`Why: ${down[0].why}.`] : []),
      ],
      actions: [{label: 'Show the last scan', help: 'state.show_last_run', onclick: () => {
        rootWatch.strip = true;
        checkRoots().catch(() => {});
      }}, checkAgain],
    }));
  }
  slot.hidden = false;
  if (again) {
    const focus = slot.querySelector('button');
    if (focus) focus.focus();
  }
}

function keepRootsChecked() {
  const lookAgain = () => {
    if (document.visibilityState === 'hidden' || Date.now() - rootWatch.at < ROOT_LOOK_AGAIN_MS) return;
    checkRoots().catch(() => {});
  };
  window.addEventListener('focus', lookAgain);
  document.addEventListener('visibilitychange', lookAgain);
  checkRoots().catch(() => {});
}

/* Read, not yet inferred: a project that was read (``ingence index``) and has had no rule run over
 * it has no experiments, lines or conclusions to show. Every view that shows results says so in one
 * drawn state, with the census's counts as the engine counted them. The Inventory, which reads the
 * census alone, works as it does. Ask answers from a run's experiments, so it shows the state too.
 * Rules, the Dictionary, Settings and Changes work without a rule having run, so they stay, with
 * the same state above them. */
const CENSUS_WORKS = new Set(['inventory']);
const STAYS_WITH_CENSUS = new Set(['rules', 'dictionary', 'settings', 'changes']);

function censusOnlyOf(shell) {
  return Boolean(shell && shell.census && !shell.inferred
    && (shell.census.files > 0 || shell.census.folders > 0));
}

function counted(number, one, many) {
  return `${Number(number).toLocaleString('en-US')} ${Number(number) === 1 ? one : many}`;
}

/* "Run the rules now" never starts a second job. The page says the rules are running whenever the
 * engine says a job is, so a reload does not offer the button again. The button is off once it is
 * clicked. The engine itself refuses a second job while one is queued or running. A job that fails
 * is said on the card. ``rulesStarted`` is a click in this tab or a job found running.
 * ``followRules`` asks ``/api/applying`` every two seconds until the job is over. */
let rulesStarted = false;
let rulesFailed = '';
let rulesStopped = false;
let rulesFollowing = false;
/* The phases in which rules are being applied: the ledger's words, which engines before 0.4.0 send,
 * and the contract's words from 0.4.0 (names, rules, then views, which ends before the run is
 * published). */
const RULES_BUSY = new Set(['queued', 'binding', 'inferring', 'names', 'rules', 'views']);
const RULES_ASKED_EVERY_MS = 2000;

async function followRules() {
  if (rulesFollowing) return;
  rulesFollowing = true;
  try {
    for (;;) {
      let said = null;
      try {
        said = await api('/api/applying');
      } catch (error) {
        break;
      }
      if (!RULES_BUSY.has(said.phase)) {
        rulesStarted = false;
        rulesFailed = said.phase === 'failed' ? String(said.error || 'it gave no reason') : '';
        break;
      }
      await new Promise((resolve) => { window.setTimeout(resolve, RULES_ASKED_EVERY_MS); });
    }
  } finally {
    rulesFollowing = false;
  }
  await readShell({fresh: true}).catch(() => null);
  drawView(state.view).catch(reportError);
}

async function runTheRulesNow(button) {
  if (button) button.disabled = true;
  rulesFailed = '';
  let answer;
  try {
    answer = await write('/api/workspace/infer', {});
  } catch (error) {
    if (button) button.disabled = false;
    await dialogs.failed(error, 'The rules did not start');
    return;
  }
  const share = answer.needs_the_share
    ? `Start what needs the folders read again yourself, in a terminal: ${answer.commands.join(', then ')}.`
    : '';
  if (answer.applying) {
    rulesStarted = true;
    rulesStopped = false;
    drawView(state.view).catch(reportError);
    followRules().catch(() => {});
  } else if (button) {
    button.disabled = false;
  }
  // What needs the folders is a person's to start: said, in a sheet, whether or not the rules started.
  if (share) await dialogs.tell({title: 'Something waits for the folders', text: share});
}

function censusCard(shell) {
  if (rulesStarted) {
    return states.say(null, {
      eyebrow: `${viewWords()} \u00b7 loading`, dot: 'loading', into: true,
      sentence: 'Applying the rules\u2026',
      body: ['This page shows its view when they finish. You can open the Inventory meanwhile.'],
      actions: [{label: 'Browse the Inventory', onclick: () => navigate('inventory')}],
    });
  }
  return states.say(null, {
    eyebrow: `${viewWords()} \u00b7 files listed only`, dot: 'census', into: true,
    sentence: 'Files listed. The rules have not run yet.',
    body: [
      `Ingence listed ${counted(shell.census.files, 'file', 'files')} in `
        + `${counted(shell.census.folders, 'folder', 'folders')}. No rule has run, so there are no `
        + 'experiments, themes or results yet.',
      ...(rulesFailed ? [`The last attempt did not finish: ${rulesFailed}.`] : []),
      ...(rulesStopped ? ['A scan stopped when the Dashboard last closed. Run the rules now starts it again.'] : []),
    ],
    actions: [
      {label: 'Run the rules now', help: 'state.run_rules',
        onclick: (event) => runTheRulesNow(event.currentTarget)},
      {label: 'Browse the Inventory', onclick: () => navigate('inventory')},
    ],
  });
}

let drawTurn = 0;
let drawnCensusOnly = false;

/* The one place a view is drawn from: asks the shell what the project is, then draws the view, or the
 * census-only state where the view has nothing to show without a rule's conclusions. */
async function drawView(view) {
  const mine = ++drawTurn;
  /* A project too large to count in a while (or a machine too loaded to) draws its view without the
   * state: the state is a convenience, and the page never waits on it for long. The answer, when it
   * comes late and says the project was read and never inferred, puts the state in place of the view. */
  const asked = readShell().catch(() => null);
  const shell = await Promise.race([
    asked,
    new Promise((resolve) => { window.setTimeout(() => resolve(undefined), SHELL_WAIT_MS); }),
  ]);
  if (mine !== drawTurn) return undefined;
  if (shell === undefined) {
    asked.then((late) => {
      if (mine === drawTurn && censusOnlyOf(late) && !drawnCensusOnly) drawView(view).catch(reportError);
    });
  }
  const census = censusOnlyOf(shell);
  drawnCensusOnly = census;
  if (!census) {
    rulesStarted = false;
  } else if (!rulesStarted) {
    // A job found running (a reload, another tab) is said, never offered again; one that failed is said.
    const applying = await api('/api/applying').catch(() => null);
    if (mine !== drawTurn) return undefined;
    if (applying && RULES_BUSY.has(applying.phase)) {
      rulesStarted = true;
      followRules().catch(() => {});
    } else if (applying && applying.phase === 'failed' && !rulesFailed) {
      rulesFailed = String(applying.error || 'it gave no reason');
    }
    // A job a close left stopped is not busy: the button starts it.
    rulesStopped = Boolean(applying && applying.phase === 'stopped');
  }
  const slot = document.getElementById('run-state');
  if (slot) {
    const above = census && STAYS_WITH_CENSUS.has(view);
    slot.hidden = !above;
    slot.replaceChildren(...(above ? [censusCard(shell)] : []));
  }
  if (census && !CENSUS_WORKS.has(view) && !STAYS_WITH_CENSUS.has(view)) {
    fill(listPane, censusCard(shell));
    return undefined;
  }
  return VIEWS[view]().catch(reportError);
}

/* A job that ends changes what the project is: a project that was census-only shows its views now. */
function keepShellCurrent() {
  onRefresh(() => {
    if (!drawnCensusOnly) return;
    readShell({fresh: true}).then((shell) => {
      if (!censusOnlyOf(shell) && drawnCensusOnly) navigate(state.view, {push: false});
    }).catch(() => {});
  });
}

/* A view named in the address opens it, on a load, when the address changes on the page, and
 * on back and forward (the Graph page moves between its own centers itself). */
function followAddress() {
  const parts = hashParts();
  const named = viewNamed(parts.view);
  // An alias (#years) is followed even onto the view it names, which reads what was asked.
  if (named && (named !== state.view || parts.query !== state.hashQuery || parts.view !== named)) {
    navigate(parts.view, {push: false, query: parts.query});
  }
}
window.addEventListener('hashchange', followAddress);
window.addEventListener('popstate', followAddress);

/* The page's own requests go first: what waits on each tab, who decides and what the applier is
 * doing are read beside the page, never before it, so a large project's Graph or Questions page
 * does not wait for the header. */
document.addEventListener('DOMContentLoaded', function start() {
  // After every script has loaded: workspace.js adds the Settings section, inspector.js the
  // inspector and graph.js the Graph page; each view is then placed on its page once.
  placeViews();
  // The Dashboard opens on the Overview (after a new project's quick checks).
  const parts = hashParts();
  const named = viewNamed(parts.view) || 'overview';
  // What each page was looking at; the address names what the page it opens is looking at.
  focusByPage = focusFromSession(PAGE_OF[named]);
  const fromAddress = focusFromAddress();
  if (fromAddress) {
    const kept = focusByPage[PAGE_OF[named]];
    focusByPage[PAGE_OF[named]] = kept && kept.kind === fromAddress.kind && kept.id === fromAddress.id
      ? kept : fromAddress;
  }
  state.view = '';
  // The name the address asked for, so #years reaches Overlap's By year mode.
  const drawn = navigate(viewNamed(parts.view) ? parts.view : named, {push: false, query: parts.query});
  loadDecider().catch(() => showDecider());
  followApplying().catch(() => {});
  // What waits on each tab is read after the page's own first draw.
  Promise.resolve(drawn).finally(() => {
    refreshWaiting();
    // The project's name, whether its folders can be reached, and the page's own state.
    keepProjectName();
    keepRootsChecked();
    keepShellCurrent();
  });
});
