// @ts-check
/* "Report a problem".
 *
 * The footer's link, and the error state's button, open a sheet. It shows the title "An email to
 * hello@ingence.org, with nothing from your data", the prefilled text in a mono box so a person
 * reads it before it leaves, "Open the email" (a link to ``mailto:`` carrying that same text),
 * "Copy the text instead", and the link to the site's guide. Where the repository is public (the
 * engine says so, ``/api/v1/reporting``) the primary button opens its new-issue page with the same
 * text, and the sheet says so in one line. The Dashboard sends nothing. The text leaves only when a
 * person clicks the link or pastes what they copied.
 *
 * The text holds no names. It states only versions, the computer, counts, fixed view names and
 * switch states. A setting is stated only if its value is a switch, a choice among words the
 * Dashboard itself offers, or a number. A setting that holds a list or text (patterns, folder
 * names, a user-agent string) is counted and left out. No path, root, pattern, file name, run
 * identifier, project name or word the person taught is read into it at all: the facts are picked
 * field by field from the engine's answers, never copied whole. An error is stated as its method,
 * its route with every segment that is not a plain route word replaced, its status and its time.
 *
 * It reads nothing when the page opens. The version comes from the one read footer.js makes for the
 * footer (``window.ingenceVersion``). The counts, the settings and the computer are read when the
 * sheet opens (and again at most every half minute).
 *
 * The functions are exposed as ``window.ingenceReport`` so that a test can give them a hostile
 * input. The sheet is opened through ``window.dialogs`` (dialogs.js, loaded later in the page and
 * used at click time). Where it is missing, the plain email link in the footer still works. */

/**
 * @typedef {{name: string, kind: string, value: unknown, changed?: boolean, choices?: unknown[],
 *   choice_words?: {[json: string]: string}}} SettingRow
 * @typedef {{engine_version?: string, engine_commit?: string | null, schema_version?: string}} VersionFacts
 * @typedef {{entries?: number, claims?: number, findings?: number, candidates?: number,
 *   rule_failures?: number}} CountFacts
 * @typedef {{method?: string, route?: string, status?: number, at?: Date | string | number}} Failure
 * @typedef {{computer?: string, python?: string, new_issue?: string | null}} ReportingFacts
 * @typedef {{version?: VersionFacts | null, counts?: CountFacts | null, roots?: number | null,
 *   settings?: SettingRow[] | null, agent?: string, window?: [number, number] | null,
 *   page?: string | null, reporting?: ReportingFacts | null, failure?: Failure | null}} Facts
 */

(() => {
  const CONTACT = 'hello@ingence.org';
  const GUIDE = 'https://ingence.org/guides/reporting-a-problem.html';
  /** The sheet's title. */
  const TITLE = 'An email to hello@ingence.org, with nothing from your data';
  /** The route segments after which an identifier or a name may stand, all left out of the report. */
  const TAKES_AN_ID = new Set(['folder', 'entry', 'dictionary', 'word', 'search', 'pages']);
  /** Settings stated whether or not they changed: the two that decide how deep a run reads. */
  const ALWAYS_STATED = ['depth', 'hash.mode'];
  /** More changed settings than this are counted, not listed: a mail link has a length limit. */
  const MOST_LISTED = 12;
  /** The longest address, in characters, that every mail program takes (some cut at about 2,000). */
  const MOST_CHARACTERS = 2000;
  /** What a choice is called when it is not one of the words the Settings page offers. */
  const NOT_OFFERED = 'not one of the offered words';
  /** The mark the server puts on a word it does not offer, but found in a file as typed. */
  const AS_WRITTEN = '(as written)';
  /** The version, the counts and the settings are read again at most this often, in ms. */
  const READ_AGAIN_AFTER = 30000;

  /** @type {Facts} */
  const held = {};

  /**
   * A whole number as a person reads it, or nothing for anything else (``null`` is not zero).
   * @param {unknown} value
   * @returns {string | null}
   */
  function whole(value) {
    return typeof value === 'number' && Number.isInteger(value) && value >= 0
      ? value.toLocaleString('en-US') : null;
  }

  /**
   * A setting's value as the draft states it: only a switch, a number or one of the choices the
   * Dashboard offers for it. Anything else (a list, text, a value outside its choices) is null.
   * @param {SettingRow} row
   * @returns {string | null}
   */
  function statable(row) {
    const {kind, value} = row;
    if (kind === 'boolean') return typeof value === 'boolean' ? (value ? 'on' : 'off') : null;
    if (kind === 'number') {
      return typeof value === 'number' && Number.isFinite(value) ? String(value) : null;
    }
    if (kind === 'choice') {
      const offered = Array.isArray(row.choices) ? row.choices : [];
      if (!offered.includes(value)) return null;
      if (value === null) return 'none';
      // The server adds a typed value to the choices (``depth`` is text in the settings) and marks
      // its words "(as written)": it is a person's own word, and may be a name. Never stated.
      const words = row.choice_words ? row.choice_words[JSON.stringify(value)] : undefined;
      if (typeof words === 'string' && words.includes(AS_WRITTEN)) return NOT_OFFERED;
      return typeof value === 'string' && /^[A-Za-z0-9_.-]{1,40}$/.test(value) ? value : null;
    }
    return null;
  }

  /**
   * The settings lines: what reads deep, then what differs from the default.
   * @param {SettingRow[] | null | undefined} settings
   * @param {number} [listAtMost] how many changed settings to name; the rest are counted
   * @returns {string[]}
   */
  function settingLines(settings, listAtMost = MOST_LISTED) {
    if (!Array.isArray(settings)) return ['Settings: could not be read.'];
    const rows = settings.filter((row) => row && typeof row.name === 'string'
      && /^[a-z][a-z0-9_.]*$/.test(row.name));
    /** @type {string[]} */
    const lines = [];
    /** @type {string[]} */
    const reading = [];
    for (const name of ALWAYS_STATED) {
      const row = rows.find((each) => each.name === name);
      const said = row ? statable(row) : null;
      if (row && said !== null) reading.push(`${row.name} = ${said}`);
    }
    if (reading.length) lines.push(`Settings that decide how much is read: ${reading.join('; ')}.`);
    const changed = rows.filter((row) => row.changed === true && !ALWAYS_STATED.includes(row.name));
    /** @type {string[]} */
    const stated = [];
    for (const row of changed) {
      const said = statable(row);
      if (said !== null) stated.push(`${row.name} = ${said}`);
    }
    const leftOut = changed.length - stated.length;
    if (!changed.length) {
      lines.push('Settings changed from their defaults: none.');
    } else {
      const listed = stated.slice(0, listAtMost);
      const more = stated.length - listed.length;
      const named = listed.length
        ? `${listed.join('; ')}${more > 0 ? `; and ${more} more` : ''}`
        : (more > 0 ? `${more} not named here, to keep this short` : 'none stated');
      lines.push(`Settings changed from their defaults (${changed.length.toLocaleString('en-US')}): `
        + `${named}.`);
      if (leftOut > 0) {
        lines.push(leftOut === 1
          ? '1 changed setting holds a list or text and is left out, because that can hold names.'
          : `${leftOut} changed settings hold lists or text and are left out, because those can hold names.`);
      }
    }
    return lines;
  }

  /**
   * A route as the report may state it: a path, never a query, and each segment kept only when it
   * is a plain route word (lowercase letters, underscores, hyphens); every other segment (an
   * identifier, a name) is ``:id``, and nothing after a segment that takes one is kept.
   * @param {unknown} route
   * @returns {string}
   */
  function safeRoute(route) {
    const path = String(route || '').split(/[?#]/)[0];
    const kept = [];
    for (const part of path.split('/').filter((each) => each !== '').slice(0, 8)) {
      if (kept.length && TAKES_AN_ID.has(kept[kept.length - 1])) { kept.push(':id'); break; }
      kept.push(part === 'v1' || /^[a-z][a-z_-]{0,30}$/.test(part) ? part : ':id');
    }
    return kept.length && kept[0] === 'api' ? `/${kept.join('/')}` : '/:id';
  }

  /** A time as a person says it: ``9:14 AM``. Nothing for a value that is not a time.
   * @param {unknown} moment
   * @returns {string | null} */
  function timeSaid(moment) {
    const when = moment instanceof Date ? moment : new Date(/** @type {any} */ (moment));
    if (Number.isNaN(when.getTime())) return null;
    return when.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
  }

  /**
   * The line stating a failed request: its method, its route (every identifier taken out), its
   * status and the time. Nothing of the response is stated.
   * @param {Failure | null | undefined} failure
   * @returns {string | null}
   */
  function failureLine(failure) {
    if (!failure || typeof failure !== 'object') return null;
    const method = failure.method === 'POST' ? 'POST' : 'GET';
    const status = Number.isInteger(failure.status) && /** @type {number} */ (failure.status) >= 0
      && /** @type {number} */ (failure.status) <= 599 ? /** @type {number} */ (failure.status) : null;
    const at = timeSaid(failure.at);
    const result = status === 0 ? 'got no answer' : status === null ? 'failed' : `returned ${status}`;
    return `error: ${method} ${safeRoute(failure.route)} ${result}${at ? ` at ${at}` : ''}`;
  }

  /**
   * The text of the report, below the line a person writes.
   * @param {Facts} facts
   * @param {number} [listAtMost]
   * @returns {string}
   */
  function detailsText(facts, listAtMost = MOST_LISTED) {
    const version = facts.version || {};
    const lines = [];
    const built = typeof version.engine_commit === 'string' && /^[0-9a-f]{7,40}(\+dirty)?$/.test(version.engine_commit)
      ? ` · build ${version.engine_commit}` : '';
    const shown = typeof version.engine_version === 'string' && /^[0-9A-Za-z.+-]{1,40}$/.test(version.engine_version)
      ? version.engine_version : null;
    const schema = typeof version.schema_version === 'string' && /^[0-9.]{1,12}$/.test(version.schema_version)
      ? ` · schema ${version.schema_version}` : '';
    lines.push(shown ? `ingence ${shown}${schema}${built}` : 'ingence: the version could not be read.');
    const reporting = facts.reporting || {};
    const computer = typeof reporting.computer === 'string' && /^[A-Za-z0-9 ._()-]{1,60}$/.test(reporting.computer)
      ? reporting.computer : null;
    const python = typeof reporting.python === 'string' && /^[0-9.]{1,20}$/.test(reporting.python)
      ? reporting.python : null;
    if (computer || python) {
      lines.push(`computer: ${[computer, python ? `Python ${python}` : null].filter(Boolean).join(' · ')}`);
    }
    if (facts.agent) lines.push(`browser: ${facts.agent}`);
    if (facts.window) lines.push(`window: ${facts.window[0]} by ${facts.window[1]} pixels`);
    const counts = facts.counts || null;
    /** @type {[unknown, string, string][]} */
    const kinds = counts ? [
      [counts.entries, 'file or folder', 'files and folders'],
      [counts.claims, 'result', 'results'],
      [counts.findings, 'flag', 'flags'],
      [counts.candidates, 'pair suggested', 'pairs suggested'],
      [counts.rule_failures, 'rule that could not decide', 'rules that could not decide'],
    ] : [];
    const said = kinds.map(([number, one, many]) => {
      const shownNumber = whole(number);
      return shownNumber === null ? null : `${shownNumber} ${shownNumber === '1' ? one : many}`;
    }).filter((part) => part !== null);
    const roots = whole(facts.roots);
    if (roots !== null) said.push(`${roots} ${roots === '1' ? 'data folder' : 'data folders'}`);
    lines.push(said.length ? `project: ${said.join(' · ')}` : 'project: nothing read yet, or not readable.');
    lines.push(...settingLines(facts.settings, listAtMost));
    if (facts.page) lines.push(`view: #${facts.page}`);
    const failed = failureLine(facts.failure);
    if (failed) lines.push(failed);
    return lines.join('\n');
  }

  /**
   * The whole text a person reads in the box, copies and sends: the details, the guide, and the
   * line they write beneath.
   * @param {Facts} facts
   * @param {number} [listAtMost]
   * @returns {string}
   */
  function bodyText(facts, listAtMost = MOST_LISTED) {
    return [
      detailsText(facts, listAtMost),
      `How to report a problem without sharing your data: ${GUIDE}`,
      '— what happened, and what you expected —',
      '',
      '',
    ].join('\n');
  }

  /**
   * The subject line: the product and the version, nothing else.
   * @param {Facts} facts
   * @returns {string}
   */
  function subjectFor(facts) {
    const shown = facts.version && typeof facts.version.engine_version === 'string'
      && /^[0-9A-Za-z.+-]{1,40}$/.test(facts.version.engine_version) ? ` ${facts.version.engine_version}` : '';
    return `Ingence problem report${shown}`;
  }

  /**
   * The ``mailto:`` address with this many changed settings named: lines end as email lines do
   * (CR LF).
   * @param {Facts} facts
   * @param {number} listAtMost
   * @returns {string}
   */
  function addressWith(facts, listAtMost) {
    const body = bodyText(facts, listAtMost).replace(/\n/g, '\r\n');
    return `mailto:${CONTACT}?subject=${encodeURIComponent(subjectFor(facts))}&body=${encodeURIComponent(body)}`;
  }

  /**
   * The repository's new-issue page with the same text, or null where the repository is not
   * public (the engine names no address then).
   * @param {Facts} facts
   * @param {number} listAtMost
   * @returns {string | null}
   */
  function issueAddressWith(facts, listAtMost) {
    const base = facts.reporting && typeof facts.reporting.new_issue === 'string'
      && /^https:\/\/[^\s?#]+$/.test(facts.reporting.new_issue) ? facts.reporting.new_issue : null;
    if (!base) return null;
    return `${base}?title=${encodeURIComponent(subjectFor(facts))}&body=${encodeURIComponent(bodyText(facts, listAtMost))}`;
  }

  /**
   * The report as it will leave: the text, the ``mailto:`` address and the issue address (or null).
   * One text for all three: fewer changed settings are named (the rest are counted, "and N more")
   * until the email address is no longer than ``MOST_CHARACTERS``, and as a last step the browser's
   * line is cut short, so the box, the copy, the email and the issue page hold the same words.
   * @param {Facts} facts
   * @returns {{text: string, subject: string, email: string, issue: string | null}}
   */
  function reportFor(facts) {
    let held = facts;
    let named = 0;
    for (named = MOST_LISTED; named >= 0; named -= 1) {
      if (addressWith(held, named).length <= MOST_CHARACTERS) break;
    }
    if (named < 0) {
      named = 0;
      held = {...facts, agent: String(facts.agent || '').slice(0, 80)};
    }
    return {
      text: bodyText(held, named), subject: subjectFor(held), email: addressWith(held, named),
      issue: issueAddressWith(held, named),
    };
  }

  /**
   * The ``mailto:`` address, no longer than ``MOST_CHARACTERS`` (see :func:`reportFor`).
   * @param {Facts} facts
   * @returns {string}
   */
  function addressFor(facts) {
    return reportFor(facts).email;
  }

  /** A JSON answer from the loopback server, or null: a missing fact is stated as missing.
   * @param {string} path
   * @returns {Promise<any>} */
  async function read(path) {
    try {
      const response = await fetch(path);
      return response.ok ? await response.json() : null;
    } catch (error) {
      return null;
    }
  }

  /** The view the Dashboard is on, from the navigation's fixed names and nothing else: the page,
   * then its section (``explore/years``), as the address names them.
   * @returns {string | null} */
  function pageNow() {
    /** @param {string} selector @param {string} key */
    const named = (selector, key) => {
      const found = document.querySelector(selector);
      const value = found instanceof HTMLElement ? found.dataset[key] : undefined;
      return typeof value === 'string' && /^[a-z][a-z-]{0,30}$/.test(value) ? value : null;
    };
    const view = named('nav.sections:not([hidden]) button.active', 'view');
    const page = named('#pages button.active', 'page');
    return view || page;
  }

  /** What the browser says of itself, now.
   * @returns {Pick<Facts, 'agent' | 'window' | 'page'>} */
  function browserFacts() {
    return {
      agent: String(navigator.userAgent || '').slice(0, 300),
      window: [window.innerWidth, window.innerHeight],
      page: pageNow(),
    };
  }

  /** The version: the one read footer.js made, or this script's own if that script is missing.
   * @returns {Promise<any>} */
  function readVersion() {
    return typeof window.ingenceVersion === 'function' ? window.ingenceVersion() : read('/api/v1/version');
  }

  /** When the answers were last read (ms since the epoch), and the read in flight, if any. */
  let readAt = 0;
  /** @type {Promise<void> | null} */
  let reading = null;

  /** Read the engine's answers, field by field, into ``held``. Never throws. A read already under
   * way is joined, not repeated.
   * @returns {Promise<void>} */
  function refresh() {
    if (reading) return reading;
    reading = (async () => {
      const [version, project, settings, reporting] = await Promise.all([
        readVersion(), read('/api/project'), read('/api/workspace/settings'), read('/api/v1/reporting'),
      ]);
      held.version = version ? {
        engine_version: version.engine_version, engine_commit: version.engine_commit,
        schema_version: version.schema_version,
      } : null;
      held.counts = project && project.counts ? {
        entries: project.counts.entries, claims: project.counts.claims, findings: project.counts.findings,
        candidates: project.counts.candidates, rule_failures: project.counts.rule_failures,
      } : null;
      held.roots = project && Array.isArray(project.roots) ? project.roots.length : null;
      held.settings = settings && Array.isArray(settings.settings) ? settings.settings.map(
        (/** @type {SettingRow} */ row) => ({name: row.name, kind: row.kind, value: row.value,
          changed: row.changed, choices: row.choices, choice_words: row.choice_words})) : null;
      held.reporting = reporting ? {
        computer: reporting.computer, python: reporting.python, new_issue: reporting.new_issue,
      } : null;
      readAt = Date.now();
    })().finally(() => { reading = null; });
    return reading;
  }

  /** The report now: what was last read, with the browser's facts taken at this moment.
   * @param {Failure | null} [failure] */
  function reportNow(failure = null) {
    return reportFor({...held, ...browserFacts(), failure});
  }

  /** The text copied to the clipboard: the clipboard where the browser gives it, else a hidden
   * field selected and copied. Resolves true where it was copied.
   * @param {string} text
   * @returns {Promise<boolean>} */
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      const field = document.createElement('textarea');
      field.value = text;
      field.setAttribute('readonly', '');
      field.style.position = 'fixed';
      field.style.opacity = '0';
      document.body.append(field);
      field.select();
      let done = false;
      try { done = document.execCommand('copy'); } catch (failed) { done = false; }
      field.remove();
      return done;
    }
  }

  /** Make an element. */
  function node(/** @type {string} */ tag, /** @type {{[name: string]: string}} */ attributes = {},
    /** @type {(Node | string)[]} */ children = []) {
    const made = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (name === 'text') made.textContent = value;
      else made.setAttribute(name === 'class' ? 'class' : name, value);
    }
    made.append(...children);
    return made;
  }

  /** The sheet's one waiting for the engine's answers: never longer than this, in ms. */
  const MOST_WAIT = 3000;

  /**
   * Open the sheet: the title, the text, the two ways to send it and the guide.
   * @param {{failure?: Failure | null}} [options]
   * @returns {Promise<void>}
   */
  async function openSheet(options = {}) {
    const failure = options.failure || null;
    if (!readAt || Date.now() - readAt >= READ_AGAIN_AFTER) {
      await Promise.race([refresh(), new Promise((resolve) => { window.setTimeout(resolve, MOST_WAIT); })]);
    }
    const report = reportNow(failure);
    const dialogs = /** @type {any} */ (window).dialogs;
    if (!dialogs || typeof dialogs.open !== 'function') {
      window.location.href = report.email;
      return;
    }
    const repository = report.issue !== null;
    const status = node('p', {class: 'sheet-note', role: 'status', 'aria-live': 'polite'});
    await dialogs.open({
      title: TITLE, eyebrow: 'Report a problem', className: 'sheet report-sheet',
      build: (/** @type {(value: unknown) => void} */ finish) => {
        const text = node('pre', {class: 'report-text', id: 'report-text', tabindex: '0',
          'aria-label': 'The text of the report', text: report.text});
        const primary = node('a', {class: 'sheet-button primary', id: 'report-open',
          href: repository ? /** @type {string} */ (report.issue) : report.email,
          ...(repository ? {target: '_blank', rel: 'noopener noreferrer'} : {}),
          'data-autofocus': '', text: repository ? 'Open the issue page' : 'Open the email'});
        const copy = node('button', {type: 'button', class: 'sheet-button', id: 'report-copy',
          text: 'Copy the text instead'});
        copy.addEventListener('click', async () => {
          const done = await copyText(report.text);
          status.textContent = done ? 'Copied.' : 'Ingence could not copy the text. Select it in the box and copy it.';
          if (done) {
            copy.textContent = 'Copied';
            window.setTimeout(() => { copy.textContent = 'Copy the text instead'; }, 2000);
          }
        });
        const guide = node('a', {class: 'sheet-link', id: 'report-guide', href: GUIDE, target: '_blank',
          rel: 'noopener noreferrer', text: 'How to report without sharing your data'});
        const shapes = node('code', {text: 'ingence teach shapes'});
        const close = node('button', {type: 'button', class: 'sheet-button', 'data-close': '', text: 'Close'});
        close.addEventListener('click', () => finish(null));
        return [
          text,
          node('p', {class: 'sheet-note'}, [
            'The message holds versions, counts and switch states. It never holds a name, a path or a word '
            + 'from your data. If the names matter, ', shapes, ' prints the shapes of the names without '
            + 'the names. Paste that in by hand.']),
          node('p', {class: 'sheet-actions'}, [primary, copy, guide]),
          repository ? node('p', {class: 'sheet-note', id: 'report-repository'}, [
            'The repository is public, so the button opens its new-issue page with the same text. '
            + 'To write to ', CONTACT, ' instead, ',
            node('a', {id: 'report-email', href: report.email, text: 'open the email'}), '.']) : null,
          status,
          node('p', {class: 'sheet-actions'}, [close]),
        ].filter((part) => part !== null);
      },
    });
  }

  /** @param {HTMLAnchorElement} link */
  function keepCurrent(link) {
    // The link is an email address with no page's facts until the sheet reads them; clicking it opens
    // the sheet instead, whose "Open the email" is the address that carries them.
    link.addEventListener('click', (event) => {
      event.preventDefault();
      openSheet().catch(() => { window.location.href = link.href; });
    });
  }

  window.ingenceReport = {addressFor, bodyText, detailsText, settingLines, reportFor, safeRoute,
    failureLine, open: openSheet};

  document.addEventListener('DOMContentLoaded', () => {
    const link = document.getElementById('report-link');
    if (link instanceof HTMLAnchorElement) keepCurrent(link);
  });
})();
