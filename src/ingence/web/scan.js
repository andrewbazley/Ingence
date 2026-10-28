// @ts-check
/* A scan's progress in words, its bar, and the small panel that asks before a scan stops. The
 * landing page (landing.js) and the Dashboard (app.js's applying line, workspace.js's header and
 * Stop) load this file before their own scripts and share it.
 *
 * Progress comes from /api/applying. The landing page reads /api/landing/progress, which carries
 * the same keys. The words name the phase in the page's own word for each phase it knows
 * ("Reading contents"), and use phase_label only for a phase it does not know. They also name the
 * step ("Step 2 of 5"), the counts ("2,400 of 3,100") and, only where the engine gives
 * seconds_left, the time left ("About 4 minutes left"). A bar is drawn only where the total is
 * known. No internal table name is ever shown: waiting_for and expected_seconds are not read here.
 *
 * Stop asks first, in a small panel under the progress line and never in a pop-up: "Stop the scan?"
 * Then, each only when it is true, it says "What was read so far is kept." and
 * "Your changes still wait for Re-run.", then shows "Stop" and "Cancel". Escape is "Cancel". The
 * page that asks says how to post the stop. This file only draws the question and says the
 * answer. */

(() => {
  /** @typedef {any} Json A payload of /api/applying or /api/workspace/stop, read as it comes. */

  /** The phase in words, where the engine sends no phase_label: the five phases, and the phase
   * words that engines before 0.4.0 send, which name the same steps.
   * @type {{[phase: string]: string}} */
  const PHASE_LABELS = {
    listing: 'Listing files',
    reading: 'Reading contents',
    names: 'Reading names',
    rules: 'Applying rules',
    views: 'Preparing views',
    census: 'Listing files',
    dive: 'Reading contents',
    binding: 'Reading names',
    inferring: 'Applying rules',
  };

  /** A job's state in words, where its phase word is one of the states it ends or waits in.
   * @type {{[phase: string]: string}} */
  const STATE_LABELS = {
    queued: 'Waiting to start',
    done: 'Done',
    failed: 'Did not finish',
    cancelled: 'Stopped',
    stopped: 'Stopped',
  };

  /** @param {unknown} value @returns {value is number} */
  const known = (value) => typeof value === 'number' && Number.isFinite(value);

  /** @param {unknown} value @returns {string} */
  const number = (value) => Number(value || 0).toLocaleString('en-US');

  /** Whether a job is waiting or running: the state where the engine says it, else the phase.
   * @param {Json} state @returns {boolean} */
  function busy(state) {
    if (!state) return false;
    if (state.state === 'running') return true;
    if (state.state === 'queued') return !state.stopped;
    return Object.prototype.hasOwnProperty.call(PHASE_LABELS, String(state.phase || ''))
      || state.phase === 'queued';
  }

  /** The phase in words: the page's own word for a phase it knows (the style guide's scan phases
   * and job states), else the engine's phase_label for a phase the page does not know.
   * @param {Json} state @returns {string} */
  function phaseLabel(state) {
    const phase = String((state && state.phase) || '');
    const own = PHASE_LABELS[phase] || STATE_LABELS[phase];
    if (own) return own;
    return String((state && state.phase_label) || '').trim();
  }

  /** "Step 2 of 5", where a job has more than one step and says which it is in.
   * @param {Json} state @returns {string} */
  function stepWords(state) {
    if (!state || !known(state.step) || !known(state.steps)) return '';
    if (state.step < 1 || state.steps < 2) return '';
    return `Step ${number(Math.min(state.step, state.steps))} of ${number(state.steps)}`;
  }

  /** "2,400 of 3,100" where the total is known; "2,400 so far" where only the count is.
   * @param {Json} state @returns {string} */
  function countWords(state) {
    if (!state || !known(state.done)) return '';
    if (known(state.total)) return `${number(state.done)} of ${number(state.total)}`;
    return `${number(state.done)} so far`;
  }

  /** "About 4 minutes left", only where the engine gives the seconds left.
   * @param {unknown} seconds @returns {string} */
  function timeLeftWords(seconds) {
    if (!known(seconds) || seconds < 0) return '';
    if (seconds < 60) return 'Less than 1 minute left';
    if (seconds < 90 * 60) {
      const minutes = Math.max(1, Math.round(seconds / 60));
      return `About ${minutes} ${minutes === 1 ? 'minute' : 'minutes'} left`;
    }
    const hours = Math.round(seconds / 3600);
    return `About ${hours} ${hours === 1 ? 'hour' : 'hours'} left`;
  }

  /** The parts of the progress line, in order, each with its name: phase, step, count, left.
   * @param {Json} state @returns {[string, string][]} */
  function parts(state) {
    return /** @type {[string, string][]} */ ([
      ['phase', phaseLabel(state)],
      ['step', stepWords(state)],
      ['count', countWords(state)],
      ['left', timeLeftWords(state && state.seconds_left)],
    ]).filter(([, words]) => words);
  }

  /** The progress line as one sentence-like string: "Reading contents · Step 2 of 5 · 2,400 of
   * 3,100 · About 4 minutes left".
   * @param {Json} state @returns {string} */
  function text(state) {
    return parts(state).map(([, words]) => words).join(' · ');
  }

  /** The bar, only where the total is known: done of total, said to a screen reader in the same
   * words as the line, never as a percentage.
   * @param {Json} state @returns {HTMLElement | null} */
  function bar(state) {
    if (!state || !known(state.done) || !known(state.total) || state.total <= 0) return null;
    const done = Math.max(0, Math.min(state.done, state.total));
    const holder = document.createElement('span');
    holder.className = 'scan-bar';
    holder.setAttribute('role', 'progressbar');
    holder.setAttribute('aria-label', phaseLabel(state) || 'Scan');
    holder.setAttribute('aria-valuemin', '0');
    holder.setAttribute('aria-valuemax', String(state.total));
    holder.setAttribute('aria-valuenow', String(done));
    holder.setAttribute('aria-valuetext', countWords(state));
    const fill = document.createElement('span');
    fill.className = 'scan-bar-fill';
    fill.style.width = `${(done / state.total) * 100}%`;
    holder.append(fill);
    return holder;
  }

  /** The line and the bar, as spans (so either fits inside a paragraph).
   * @param {Json} state @returns {HTMLElement} */
  function element(state) {
    const holder = document.createElement('span');
    holder.className = 'scan-progress';
    holder.dataset.phase = String((state && state.phase) || '');
    const line = document.createElement('span');
    line.className = 'scan-progress-line';
    parts(state).forEach(([name, words], at) => {
      if (at) line.append(document.createTextNode(' · '));
      const part = document.createElement('span');
      part.className = `scan-${name}`;
      part.textContent = words;
      line.append(part);
    });
    holder.append(line);
    const drawn = bar(state);
    if (drawn) holder.append(drawn);
    return holder;
  }

  /* ---------------------------------------------------------------- the question before Stop */

  /** The panel open now, and what it resolves.
   * @type {{panel: HTMLElement, done: Promise<Json | null>} | null} */
  let asking = null;

  /** Ask before a scan stops: a small panel placed after ``after`` (or at the top of ``inside``).
   * Resolves with the stop route's answer, or null when the person chose Cancel.
   * @param {{after?: Element | null, inside?: Element | null, stopKeeps?: boolean,
   *   changesWait?: boolean, post: () => Promise<Json>, primaryClass?: string,
   *   buttonClass?: string}} options
   * @returns {Promise<Json | null>} */
  function askToStop(options) {
    if (asking && asking.panel.isConnected) {
      const first = asking.panel.querySelector('button');
      if (first instanceof HTMLElement) first.focus();
      return asking.done;
    }
    const returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const panel = document.createElement('div');
    panel.id = 'stop-confirm';
    panel.className = 'stop-confirm';
    panel.setAttribute('role', 'group');
    panel.setAttribute('aria-labelledby', 'stop-confirm-head');
    const head = document.createElement('p');
    head.id = 'stop-confirm-head';
    head.className = 'stop-confirm-head';
    head.textContent = 'Stop the scan?';
    panel.append(head);
    /** @param {string} name @param {string} words */
    const line = (name, words) => {
      const said = document.createElement('p');
      said.className = 'stop-confirm-line';
      said.dataset.line = name;
      said.textContent = words;
      panel.append(said);
    };
    if (options.stopKeeps) line('keeps', 'What was read so far is kept.');
    if (options.changesWait) line('waits', 'Your changes still wait for Re-run.');
    const actions = document.createElement('p');
    actions.className = 'stop-confirm-actions';
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.className = options.primaryClass || 'primary';
    stop.dataset.stop = '';
    stop.textContent = 'Stop';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = options.buttonClass || '';
    cancel.dataset.cancel = '';
    cancel.textContent = 'Cancel';
    actions.append(stop, document.createTextNode(' '), cancel);
    const status = document.createElement('p');
    status.className = 'stop-confirm-said';
    status.setAttribute('role', 'status');
    panel.append(actions, status);

    /** @type {(value: Json | null) => void} */
    let resolve = () => {};
    const done = /** @type {Promise<Json | null>} */ (new Promise((settle) => { resolve = settle; }));
    /** @param {Json | null} value */
    const close = (value) => {
      panel.remove();
      if (asking && asking.panel === panel) asking = null;
      if (returnTo && returnTo.isConnected) returnTo.focus();
      resolve(value);
    };
    /** What Cancel (or Close, once the engine has answered) resolves with.
     * @type {Json | null} */
    let result = null;
    cancel.addEventListener('click', () => close(result));
    panel.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      close(result);
    });
    stop.addEventListener('click', async () => {
      stop.disabled = true;
      cancel.disabled = true;
      status.textContent = 'Stopping...';
      /** @type {Json} */
      let answer;
      try {
        answer = await options.post();
      } catch (error) {
        const words = String((error instanceof Error ? error.message : error) || '').trim()
          .replace(/[.\s]+$/, '');
        status.textContent = `Ingence could not stop the scan: ${words || 'Ingence gave no reason'}.`;
        actions.replaceChildren(cancel);
        cancel.disabled = false;
        cancel.textContent = 'Close';
        cancel.focus();
        return;
      }
      if (answer && answer.requested === false) {
        // The engine did not take the stop: what it said stays, until the person closes it.
        status.textContent = String(answer.said || 'Ingence did not stop the scan.');
        result = answer;
        actions.replaceChildren(cancel);
        cancel.disabled = false;
        cancel.textContent = 'Close';
        cancel.focus();
        return;
      }
      close(answer || {});
    });

    if (options.after && options.after.parentNode) options.after.after(panel);
    else if (options.inside) options.inside.prepend(panel);
    else document.body.prepend(panel);
    asking = {panel, done};
    cancel.focus();
    return done;
  }

  /** @type {any} */ (window).ingenceScan = {
    busy, phaseLabel, stepWords, countWords, timeLeftWords, text, bar, element, askToStop,
    PHASE_LABELS, STATE_LABELS,
  };
})();
