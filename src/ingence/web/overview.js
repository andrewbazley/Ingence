/* The Overview and Checks.
 *
 * The Overview opens first, because a person gives the Dashboard little time, often about 30
 * minutes, and too many options lose them. It says what the scan found on one page, top to bottom:
 * the Ask box; one answer card per goal; one sentence of counts; the areas and what each folder
 * stands for; how complete the experiments are; what was seen once and what was repeated; the
 * leading themes; what Ingence assumed, each with "This is wrong"; the few checks that matter; and
 * the job, with "Stop", while one runs. Every number is a route's: nothing is counted on the page.
 *
 * The quick checks come before it, once per project. After a project's first scan, up to 5 checks
 * are asked one at a time, as the Overview's first state (never a pop-up): "Keep" (the guess
 * stands), "Change" (answered through the answer route the Questions page uses; it waits for
 * Re-run), "Skip" (every guess stands; the Overview shows). Shown once per project (localStorage,
 * ``ingence.quickchecks.v1.<project>``); where the browser keeps nothing, once per page load.
 *
 * Checks asks one question at a time, ranked as the engine ranks them, each with the guess already
 * taken and "Change"; beneath it the words that matter (assumed spellings and domains) and every
 * assumption, each with "This is wrong". "All questions" opens Advanced > Questions. The full list
 * is grouped by kind under plain headings (File types, Writers, Word meanings, Spellings, Area
 * kinds, Workflows, Stages, Repeats), and an assumption's two examples each open the file in the
 * one inspector, in a box under the line. "This is wrong" never opens a dialog: the line says
 * "Waits for Re-run" and the header's Re-run badge counts it.
 *
 * Data: ``/api/v1/overview``, ``/api/v1/assumptions``, ``/api/v1/checks``, ``/api/v1/goals`` and
 * ``/api/applying``; writes through app.js's ``write`` (``/api/assumptions/reject``,
 * ``/api/areas/kind``, ``/api/review/answer``), which asks the header to read what waits for
 * Re-run. "Show these" keeps a goal's experiments in ``sessionStorage['ingence.highlight']`` (JSON:
 * ``{kind, id, label, experiments}``) for the Overlap view to light, and opens
 * ``#experiments?goal=<id>``.
 *
 * Loaded after app.js (el, api, write, states, turns, navigate, VIEWS, SINGLE_VIEWS, viewQuery,
 * readShell, onApplying, jobRunning, askFor) and review.js (fill, me, askActor). The pure functions
 * are on ``overviewPage`` for the tests. */

(function overviewScript(global) {
  'use strict';

  const QUICK_KEY = 'ingence.quickchecks.v1.';
  const HIGHLIGHT_KEY = 'ingence.highlight';
  /* How many checks the quick checks ask at most, and how many the Overview lists. */
  const QUICK_LIMIT = 5;
  /* How many checks are read to find the first QUICK_LIMIT that have a guess (the route's most). */
  const QUICK_SEARCH = 50;
  /* How many assumptions the Overview lists; Checks lists them all. */
  const ASSUMPTIONS_SHOWN = 8;
  /* How many checks Checks reads: the most the route gives. */
  const CHECKS_ALL = 50;
  /* How many file types the Overview's sentence names. */
  const TYPES_NAMED = 3;
  const AREA_KINDS = ['person', 'instrument', 'project', 'date', 'folder'];
  /* What a write says when the person gives no name: every answer is saved under one. */
  const NAME_FIRST = 'Ingence needs your name to save an answer. Set your name first.';
  const TIER_ORDER = ['named', 'associated', 'same_theme'];
  const TIER_WORDS = {named: 'Named', associated: 'Associated', same_theme: 'Same theme'};
  /* The kinds of assumption Checks lists as the words that matter. */
  const WORD_KINDS = new Set(['spelling', 'domain']);
  /* The kinds of assumption, in the order Checks' full list groups them, with a plain heading each.
   * A kind the engine adds later is listed last, under "Other". */
  const KIND_ORDER = ['file_type', 'writer', 'domain', 'spelling', 'area_kind', 'workflow', 'stage', 'repeat'];
  const KIND_WORDS = {file_type: 'File types', writer: 'Writers', domain: 'Word meanings', spelling: 'Spellings',
    area_kind: 'Area kinds', workflow: 'Workflows', stage: 'Stages', repeat: 'Repeats'};

  /* ------------------------------------------------------------------ pure words */

  function fmt(n) {
    return Number(n || 0).toLocaleString('en-US');
  }

  function plural(n, one, many) {
    return `${fmt(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;
  }

  /* A date as the Overview says it: the year and month ("2024-03"). */
  function month(date) {
    return date ? String(date).slice(0, 7) : '';
  }

  /* A span of dates: "2023-01 to 2024-09", one month alone, or nothing where none was read. */
  function spanWords(first, last) {
    const from = month(first);
    const to = month(last);
    if (!from && !to) return '';
    if (!from || !to || from === to) return from || to;
    return `${from} to ${to}`;
  }

  /* A file type's label for many files: "TIFF image" is "TIFF images". */
  function typePlural(label) {
    const words = String(label || '').trim();
    return /s$/i.test(words) ? words : `${words}s`;
  }

  /* A share as a person reads it: "47%", or "less than 1%". */
  function shareWords(share) {
    const percent = Math.round(Number(share || 0) * 100);
    return percent < 1 ? 'less than 1%' : `${percent}%`;
  }

  /* The Overview's one sentence: "32 experiments in 4 areas, 2023-01 to 2024-09, 2,511 files (ND2
   * images 47%, TIFF images 24%, CSV tables 16%)." From the route's counts, dates and file types. */
  function overviewSentence(found) {
    const counts = found.counts || {};
    const span = spanWords(found.date_range && found.date_range.first, found.date_range && found.date_range.last);
    const types = (found.file_types || []).slice(0, TYPES_NAMED)
      .map((type) => `${typePlural(type.label)} ${shareWords(type.share)}`);
    return `${plural(counts.experiments, 'experiment')} in ${plural(counts.areas, 'area')}`
      + `${span ? `, ${span}` : ''}, ${plural(counts.files, 'file')}`
      + `${types.length ? ` (${types.join(', ')})` : ''}.`;
  }

  /* A goal's answer in one line: "12 experiments relate (8 named, 3 associated, 1 by theme).
   * 2023-03 to 2025-01. Raw to analysis: 7 of 12. Repeated: 3 groups; seen once: 4." */
  function goalLine(summary) {
    const s = summary || {};
    const span = spanWords(s.first, s.last);
    return [
      `${plural(s.experiments, 'experiment')} ${Number(s.experiments) === 1 ? 'relates' : 'relate'} `
        + `(${fmt(s.named)} named, ${fmt(s.associated)} associated, ${fmt(s.same_theme)} same theme).`,
      span ? `${span}.` : null,
      `Raw to analysis: ${fmt(s.raw_with_analysis)} of ${fmt(s.of)}.`,
      `Repeated: ${plural(s.repeated_groups, 'group')}; seen once: ${fmt(s.seen_once)}.`,
    ].filter(Boolean).join(' ');
  }

  /* A goal's matches by tier, each tier apart, in the order named, associated, by theme. */
  function tiersOf(goal) {
    return TIER_ORDER.map((tier) => ({tier, words: TIER_WORDS[tier],
      matches: (goal.matches || []).filter((match) => match.tier === tier)}))
      .filter((group) => group.matches.length);
  }

  /* "Raw files analyzed: 1,344 of 1,792". */
  function analyzedWords(completeness) {
    const c = completeness || {};
    return `Raw files analyzed: ${fmt(c.raw_with_analysis)} of ${fmt(c.raw_files)}`;
  }

  /* "Experiments: 18 complete, 7 partial, 5 raw only, 2 analysis only." */
  function experimentsWords(completeness) {
    const c = completeness || {};
    return `Experiments: ${fmt(c.experiments_complete)} complete, ${fmt(c.experiments_partial)} partial, `
      + `${fmt(c.experiments_raw_only)} raw only, ${fmt(c.experiments_analysis_only)} analysis only.`;
  }

  /* "Seen once: 20 experiments. Repeated: 12 experiments in 5 groups." */
  function repeatsWords(repeats) {
    const r = repeats || {};
    return `Seen once: ${plural(r.seen_once, 'experiment')}. `
      + `Repeated: ${plural(r.repeated, 'experiment')} in ${plural(r.groups, 'group')}.`;
  }

  /* What a job's line says: "Reading the names: step 2 of 3, 120 of 400 files." Measured progress
   * only; never a time left. */
  function jobWords(status) {
    const s = status || {};
    const label = s.phase_label || 'A scan is running';
    const step = s.steps ? `step ${fmt(s.step)} of ${fmt(s.steps)}` : '';
    const done = s.total ? `${fmt(s.done)} of ${fmt(s.total)}` : '';
    const parts = [step, done].filter(Boolean).join(', ');
    return `${label}${parts ? `: ${parts}` : ''}.`;
  }

  /* What a write that waits for Re-run says on its line. A build that records nothing says so in the
   * engine's words. */
  function waitsWords(answer) {
    if (answer && answer.recorded === false && answer.said) return String(answer.said);
    return 'Waits for Re-run';
  }

  /* The body an answer to a check posts: the route's own body, the question as the answer route's
   * target, the option's value as the word, and who answers. */
  function answerBody(check, option, actor) {
    const given = (check.answer && check.answer.body) || {};
    return {...given, actor, target: given.target || given.question_id || check.question_id, word: option.value};
  }

  /* The assumptions by kind, in KIND_ORDER, each kind's most files first; a kind with none is left
   * out, and a kind not in KIND_ORDER is listed last under "Other". */
  function kindsOf(items) {
    const groups = new Map();
    for (const item of items || []) {
      const kind = KIND_WORDS[item.kind] ? item.kind : 'other';
      if (!groups.has(kind)) groups.set(kind, []);
      groups.get(kind).push(item);
    }
    return [...KIND_ORDER, 'other'].filter((kind) => groups.has(kind)).map((kind) => ({kind,
      words: KIND_WORDS[kind] || 'Other',
      items: groups.get(kind).sort((a, b) => Number(b.count || 0) - Number(a.count || 0))}));
  }

  /* A short name for a project, for its storage key: the project's path, folded. */
  function projectKey(path) {
    let hash = 0x811c9dc5;
    for (const character of String(path || '')) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash.toString(16).padStart(8, '0');
  }

  /* ------------------------------------------------------------------ storage */

  const quick = {
    /* 'unknown' until the first Overview asks; 'active' while the checks are asked; 'done' after. */
    phase: 'unknown',
    checks: [],
    at: 0,
    said: '',
    /* Where the browser keeps nothing: the checks show once per page load. */
    shownThisLoad: false,
  };

  function storage() {
    try {
      return global.localStorage || null;
    } catch (error) {
      return null;
    }
  }

  async function quickKey() {
    const shell = await readShell().catch(() => null);
    return `${QUICK_KEY}${projectKey(shell && shell.project_path)}`;
  }

  async function quickSeen() {
    if (quick.shownThisLoad) return true;
    const key = await quickKey();
    try {
      const kept = storage();
      return Boolean(kept && kept.getItem(key));
    } catch (error) {
      return false;
    }
  }

  async function markQuickSeen() {
    quick.shownThisLoad = true;
    const key = await quickKey();
    try {
      const kept = storage();
      if (kept) kept.setItem(key, new Date().toISOString());
    } catch (error) {
      /* Storage refused (a private window): shownThisLoad holds it for this page load. */
    }
  }

  /* The experiments Overlap lights, kept for the tab's session (overlap.js reads it). */
  function keepHighlight(kind, id, label, experiments) {
    try {
      global.sessionStorage.setItem(HIGHLIGHT_KEY, JSON.stringify({kind, id, label, experiments}));
    } catch (error) {
      /* Nothing is lit where the browser keeps nothing; the page still opens. */
    }
  }

  /* A theme opens Overlap's Connections with its filter set to the theme. The theme's words go in
   * ``sessionStorage['ingence.overlap.theme']`` (overlap.js reads it). A goal's highlight is left
   * as it is. */
  const OVERLAP_THEME_KEY = 'ingence.overlap.theme';

  function openThemeInOverlap(label) {
    try {
      global.sessionStorage.setItem(OVERLAP_THEME_KEY, String(label || ''));
    } catch (error) {
      /* Overlap opens on every theme where the browser keeps nothing. */
    }
    const overlap = global.ingenceOverlap;
    if (overlap && typeof overlap.setMode === 'function') overlap.setMode('connections');
    else {
      try { global.sessionStorage.setItem('ingence.overlap.mode', 'connections'); } catch (error) { /* a convenience */ }
    }
    return navigate('overlap');
  }

  /* ------------------------------------------------------------------ shared parts */

  function heading(level, text, attributes = {}) {
    return el(`h${level}`, {text, ...attributes});
  }

  /* A pressed button keeps the focus: while its write runs, and after it, a button is held with
   * aria-disabled and its clicks are ignored; ``disabled`` would drop the focus to the page. */
  function hold(button, on) {
    if (on) button.setAttribute('aria-disabled', 'true');
    else button.removeAttribute('aria-disabled');
  }

  function held(button) {
    return button.getAttribute('aria-disabled') === 'true';
  }

  /* A button in the Dashboard's answer style, its label in bold. */
  function action(label, help, onclick, extra = {}) {
    return el('button', {type: 'button', class: extra.quiet === false ? 'answer' : 'answer quiet',
      'data-help': help, onclick, ...(extra.attributes || {})}, [el('strong', {text: label})]);
  }

  /* A part of a page that could not be read says so in a line, and the rest of the page stays. */
  function unread(what, error) {
    return el('p', {class: 'note refused', text: `${what} could not be read: ${(error && error.message) || error}`});
  }

  /* An example of an assumption opens in the one inspector, in a box under its line (the Overview and
   * Checks have no pane beside them); the same example again closes it. */
  function exampleButton(example, box) {
    const button = el('button', {type: 'button', class: 'link ov-example', 'data-help': 'assumptions.example',
      'data-entry': example.entry_id, 'aria-expanded': 'false'}, [el('code', {text: example.label})]);
    button.addEventListener('click', () => {
      const open = box.getAttribute('data-entry') === example.entry_id && !box.hidden;
      for (const other of box.parentNode.querySelectorAll('.ov-example')) other.setAttribute('aria-expanded', 'false');
      if (open || !global.inspector) {
        box.hidden = true;
        box.removeAttribute('data-entry');
        box.replaceChildren();
        return;
      }
      box.hidden = false;
      box.setAttribute('data-entry', example.entry_id);
      button.setAttribute('aria-expanded', 'true');
      global.inspector.showEntry(example.entry_id, box, {keepFocus: true, backLabel: 'close', back: () => {
        box.hidden = true;
        box.removeAttribute('data-entry');
        box.replaceChildren();
        button.setAttribute('aria-expanded', 'false');
        button.focus();
      }});
    });
    return button;
  }

  /* An assumption's ``text`` is one plain sentence; the rule's full statement may follow after a
   * blank line and "Why: ", which the page keeps behind a Why disclosure. */
  function assumptionWords(text) {
    const [sentence, ...rest] = String(text || '').split('\n\nWhy: ');
    return {sentence, why: rest.join('\n\nWhy: ').trim()};
  }

  /* One assumption: its sentence, how many files rest on it, two of them, and "This is wrong". */
  function assumptionLine(item) {
    const said = el('span', {class: 'ov-said', role: 'status'});
    const line = el('li', {class: 'ov-assumption', 'data-assumption': item.id});
    const wrong = action('This is wrong', 'assumptions.wrong', async () => {
      if (held(wrong)) return;
      hold(wrong, true);
      try {
        // The reject route records an answer, so it names who gives it (review.js askActor).
        if (!(await askActor())) {
          hold(wrong, false);
          said.textContent = NAME_FIRST;
          return;
        }
        const reject = item.reject || {};
        const answer = await write(reject.route || '/api/assumptions/reject',
          {...(reject.body || {id: item.id}), actor: me.actor});
        // The same assumption listed twice (Checks' words and its full list) says it on both lines.
        for (const same of document.querySelectorAll('.ov-assumption')) {
          if (same.getAttribute('data-assumption') !== item.id) continue;
          same.classList.add('ov-rejected');
          const words = same.querySelector('.ov-said');
          if (words) words.textContent = waitsWords(answer);
          const button = same.querySelector('button[data-wrong]');
          if (button) hold(button, true);
        }
        said.textContent = waitsWords(answer);
        line.classList.add('ov-rejected');
      } catch (error) {
        hold(wrong, false);
        said.textContent = `Not saved: ${(error && error.message) || error}`;
      }
    }, {attributes: {'data-wrong': ''}});
    const assumed = assumptionWords(item.text);
    const examples = (item.examples || []).slice(0, 2);
    const box = el('div', {class: 'ov-inspect'});
    box.hidden = true;
    line.append(
      el('p', {class: 'ov-assumption-text'}, [assumed.sentence]),
      assumed.why ? el('details', {class: 'ov-why', 'data-why': ''}, [
        el('summary', {text: 'Why'}), el('p', {class: 'faint', text: assumed.why})]) : null,
      examples.length ? el('ul', {class: 'ov-examples', 'aria-label': 'Examples'},
        examples.map((example) => el('li', {}, [exampleButton(example, box)]))) : null,
      box,
      el('p', {class: 'ov-line-actions'}, [wrong, ' ', said]),
    );
    return line;
  }

  function assumptionList(items, {limit} = {}) {
    const shown = [...items].sort((a, b) => Number(b.count || 0) - Number(a.count || 0));
    return el('ul', {class: 'ov-assumptions'}, (limit ? shown.slice(0, limit) : shown).map(assumptionLine));
  }

  /* Answer a check with one of its options, through the answer route. */
  async function answerCheck(check, option) {
    if (!(await askActor())) return {refused: NAME_FIRST};
    const route = (check.answer && check.answer.route) || '/api/review/answer';
    const written = await write(route, answerBody(check, option, me.actor));
    return {written};
  }

  /* What a check says where the engine abstains: ``default`` null says it, and no field besides. */
  const NO_GUESS = 'Ingence has no guess here';

  /* The guess a check already took, or that there is none until it is answered. */
  function guessNode(check) {
    if (!check.default) {
      return el('p', {class: 'ck-guess ck-none', 'data-guess': ''}, [el('strong', {text: NO_GUESS})]);
    }
    return el('p', {class: 'ck-guess', 'data-guess': check.default.value}, [
      'Ingence’s guess: ', el('strong', {text: check.default.text})]);
  }

  /* The options of a check, shown by "Change": one button each, and "Cancel". */
  function optionsNode(check, {onAnswered, onCancel, said}) {
    const holder = el('div', {class: 'ck-options', role: 'group', 'aria-label': 'Answers'});
    const buttons = (check.options || []).map((option) => {
      const button = action(option.text, 'checks.answer', async () => {
        if (held(button)) return;
        for (const other of holder.querySelectorAll('button')) hold(other, true);
        try {
          const done = await answerCheck(check, option);
          if (done.refused) {
            said.textContent = done.refused;
            for (const other of holder.querySelectorAll('button')) hold(other, false);
            return;
          }
          onAnswered(option, done.written);
        } catch (error) {
          said.textContent = `Not saved: ${(error && error.message) || error}`;
          for (const other of holder.querySelectorAll('button')) hold(other, false);
        }
      }, {attributes: {'data-option': option.value}});
      if (check.default && check.default.value === option.value) {
        button.classList.add('ck-current');
        button.title = 'Ingence\u2019s guess now';
      }
      return button;
    });
    holder.append(...buttons, action('Cancel', 'checks.cancel', onCancel));
    return holder;
  }

  /* The question's own words: the title, and its sentence (sent whether or not there is a guess). */
  function questionNodes(check, headingId) {
    const text = String(check.text || '').trim();
    return [
      el('h2', {class: 'ck-title', id: headingId, tabindex: '-1', text: check.title}),
      text && text !== check.title ? el('p', {class: 'ck-text', text}) : null,
      guessNode(check),
    ];
  }

  function focusHeading(id) {
    const node = document.getElementById(id);
    if (node) node.focus({preventScroll: false});
  }

  /* ------------------------------------------------------------------ the quick checks */

  /* The quick checks close (Skip, or the last check): the first-visit tour waits for this event. */
  function finishQuick() {
    quick.phase = 'done';
    quick.said = '';
    document.dispatchEvent(new Event('ingence:checks-done'));
    drawOverview({focusTop: true}).catch(reportError);
  }

  function drawQuick() {
    const check = quick.checks[quick.at];
    if (!check) {
      finishQuick();
      return;
    }
    const last = quick.at === quick.checks.length - 1;
    const said = el('p', {class: 'ck-said', role: 'status', 'aria-live': 'polite', text: quick.said || ''});
    quick.said = '';
    const next = () => {
      quick.at += 1;
      if (quick.at >= quick.checks.length) finishQuick();
      else drawQuick();
    };
    const keep = action(check.default ? 'Keep' : 'Next', check.default ? 'checks.keep' : 'checks.next', next,
      {quiet: false, attributes: {'data-quick': 'keep'}});
    const buttons = el('div', {class: 'ck-actions'}, [
      keep,
      action('Change', 'checks.change', () => {
        buttons.hidden = true;
        options.hidden = false;
        const first = options.querySelector('button');
        if (first) first.focus();
      }, {attributes: {'data-quick': 'change'}}),
      action('Skip', 'checks.skip', finishQuick, {attributes: {'data-quick': 'skip'}}),
    ]);
    const options = optionsNode(check, {
      said,
      onAnswered: () => {
        quick.said = 'Ingence saved your answer. It waits for Re-run.';
        next();
      },
      onCancel: () => {
        options.hidden = true;
        buttons.hidden = false;
        keep.focus();
      },
    });
    options.hidden = true;
    fill(listPane, el('section', {class: 'ck-card ck-quick', 'data-view': 'quick-checks', 'data-quick-checks': '',
      'data-check': check.id,
      'aria-labelledby': 'quick-title'}, [
      el('p', {class: 'eyebrow ck-count', 'data-help': 'checks.quick', text: `Check ${quick.at + 1} of ${quick.checks.length}`}),
      ...questionNodes(check, 'quick-title'),
      buttons, options, said,
      el('p', {class: 'faint ck-note', text: last
        ? 'This is the last check. The Overview comes next.'
        : 'Each check already has a guess, so you can skip it. Checks lists every check later.'}),
    ]));
    focusHeading('quick-title');
  }

  /* ------------------------------------------------------------------ the Overview */

  /* The Overview's line that follows the job: built once with the page, its words and "Stop"
   * brought up to date each time the job is read. "Stop" opens the dialog
   * ``window.ingenceStop(jobId)``, which asks "Stop the scan?" itself. Where that script is not
   * loaded, "Stop" is off. */
  const jobHolder = {line: null, words: null, stop: null, status: null};

  function jobLine() {
    const line = el('div', {class: 'ov-job', 'data-part': 'job', 'data-help': 'overview.job', role: 'status'});
    line.hidden = true;
    const words = el('span');
    const stop = action('Stop', 'overview.job', () => {
      const status = jobHolder.status;
      if (status && typeof global.ingenceStop === 'function') {
        Promise.resolve(global.ingenceStop(status.job_id)).catch(reportError);
      }
    }, {quiet: false});
    line.append(el('p', {class: 'ov-job-line'}, [words, ' ', stop]));
    Object.assign(jobHolder, {line, words, stop, status: null});
    return line;
  }

  function drawJob(status) {
    const {line, words, stop} = jobHolder;
    if (!line || !line.isConnected) return;
    if (!jobRunning(status)) {
      jobHolder.status = null;
      line.hidden = true;
      return;
    }
    jobHolder.status = status;
    words.textContent = jobWords(status);
    const off = status.can_stop === false || typeof global.ingenceStop !== 'function';
    stop.disabled = off;
    stop.title = off ? 'Ingence cannot stop this job now.' : '';
    line.hidden = false;
  }

  function goalCard(goal) {
    const show = action('Show these', 'overview.show_these', () => {
      const ids = (goal.matches || []).map((match) => match.experiment_id);
      keepHighlight('goal', goal.id, goal.text, ids);
      navigate('experiments', {query: new URLSearchParams({goal: goal.id}).toString()});
    }, {quiet: false});
    return el('article', {class: 'ov-goal', 'data-goal': goal.id, 'data-help': 'overview.goal'}, [
      heading(3, goal.text),
      el('p', {class: 'ov-goal-line', text: goalLine(goal.summary)}),
      el('p', {class: 'faint', text: 'Seen once means one experiment supports it: observational.'}),
      ...tiersOf(goal).map((group) => el('div', {class: 'ov-tier', 'data-tier': group.tier}, [
        heading(4, group.words),
        el('ul', {}, group.matches.map((match) => el('li', {}, [
          el('strong', {text: match.label}), el('span', {class: 'faint', text: `: ${match.why}`})]))),
      ])),
      el('p', {}, [show]),
    ]);
  }

  function areaRow(area) {
    const said = el('span', {class: 'ov-said', role: 'status'});
    const parts = [
      el('strong', {class: 'ov-area-name', text: area.name}), ' ',
      el('span', {class: 'ov-kind', 'data-kind': area.kind, text: area.kind}),
      el('span', {class: 'faint', text: ` · ${plural(area.experiments, 'experiment')}, ${plural(area.files, 'file')}`}),
    ];
    const row = el('li', {class: 'ov-area', 'data-area': area.id}, parts);
    if (area.kind_assumed) {
      const select = el('select', {'aria-label': `What ${area.name} is`, 'data-help': 'overview.area_kind'},
        AREA_KINDS.map((kind) => el('option', {value: kind, text: kind})));
      select.value = area.kind;
      select.hidden = true;
      const change = el('button', {type: 'button', class: 'link', 'data-help': 'overview.area_kind', text: 'Change'});
      /* Posted on Save, not on the select's change, which the arrow keys fire on some systems. */
      const save = action('Save', 'overview.area_kind', async () => {
        if (held(save)) return;
        hold(save, true);
        try {
          if (!(await askActor())) {
            hold(save, false);
            said.textContent = NAME_FIRST;
            return;
          }
          const answer = await write('/api/areas/kind', {area_id: area.id, kind: select.value, actor: me.actor});
          said.textContent = waitsWords(answer);
        } catch (error) {
          hold(save, false);
          said.textContent = `Not saved: ${(error && error.message) || error}`;
        }
      }, {attributes: {'data-area-save': ''}});
      save.hidden = true;
      change.addEventListener('click', () => {
        change.hidden = true;
        select.hidden = false;
        save.hidden = false;
        select.focus();
      });
      row.append(' ', el('span', {class: 'ov-assumed', text: 'assumed'}), ' ', change, select, ' ', save, ' ', said);
    }
    return row;
  }

  function meter(part, whole) {
    const share = whole > 0 ? Math.max(0, Math.min(1, part / whole)) : 0;
    return el('div', {class: 'ov-meter', 'aria-hidden': 'true'}, [
      el('span', {class: 'ov-meter-fill', style: `width:${(share * 100).toFixed(1)}%`})]);
  }

  function section(name, help, title, children) {
    return el('section', {class: `ov-part ov-part-${name}`, 'data-part': name, 'data-help': help,
      'aria-label': title}, [heading(3, title), ...children]);
  }

  function askBox() {
    const input = el('input', {type: 'search', class: 'ov-ask-input', autocomplete: 'off', 'aria-label': 'Ask',
      'data-help': 'overview.ask', placeholder: 'Ask: for example, which experiments used GFP'});
    const form = el('form', {class: 'ov-ask', role: 'search', 'aria-label': 'Ask'}, [
      input, el('button', {type: 'submit', class: 'answer', 'data-help': 'overview.ask'}, [el('strong', {text: 'Ask'})])]);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (input.value.trim()) askFor(input.value);
    });
    return form;
  }

  function checksList(checks) {
    if (!checks.length) return [el('p', {class: 'faint', text: 'No checks wait. Ingence asks a check when one answer settles many files.'})];
    return [el('ol', {class: 'ov-check-list'}, checks.map(checkListItem))];
  }

  /* One check in the Overview's list: its title and guess; or, where Ingence has no guess, its
   * question, its options inline and the words that there is none. */
  function checkListItem(check) {
    const open = () => navigate('checks', {query: new URLSearchParams({check: check.id}).toString()});
    const title = el('button', {type: 'button', class: 'link', 'data-help': 'overview.checks', text: check.title,
      onclick: open});
    if (check.default) {
      return el('li', {'data-check': check.id}, [title,
        el('span', {class: 'faint', text: ` · guess: ${check.default.text}`})]);
    }
    const said = el('p', {class: 'ck-said', role: 'status', 'aria-live': 'polite'});
    const options = optionsNode(check, {
      said,
      onAnswered: () => {
        said.textContent = 'Ingence saved your answer. It waits for Re-run.';
        options.hidden = true;
      },
      onCancel: () => { options.hidden = true; },
    });
    for (const cancel of options.querySelectorAll('button[data-help="checks.cancel"]')) cancel.remove();
    const text = String(check.text || '').trim();
    return el('li', {'data-check': check.id, 'data-no-guess': ''}, [
      title,
      text && text !== check.title ? el('p', {class: 'ck-text', text}) : null,
      el('p', {class: 'ck-guess ck-none', 'data-guess': ''}, [el('strong', {text: NO_GUESS})]),
      options, said,
    ]);
  }

  async function drawOverview({focusTop = false} = {}) {
    const turn = turns.claim(listPane);
    if (!listPane.querySelector('.ov-page')) states.loading(listPane, 'Reading the overview...');
    const settled = (promise) => promise.then((value) => ({value}), (error) => ({error}));
    const [overview, assumptions, checks, goals, applying] = await Promise.all([
      settled(api('/api/v1/overview')),
      settled(api('/api/v1/assumptions')),
      settled(api(`/api/v1/checks?limit=${QUICK_LIMIT}`)),
      settled(api('/api/v1/goals')),
      settled(api('/api/applying')),
    ]);
    if (!turns.owns(listPane, turn) || state.view !== 'overview') return;
    if (overview.error) {
      states.failed(listPane, overview.error, () => navigate('overview', {push: false}));
      return;
    }
    const found = overview.value;
    const counts = found.counts || {};
    const parts = [heading(2, 'Overview', {class: 'ov-title', id: 'overview-title', tabindex: '-1'}), askBox()];

    // 2. One answer card per goal leads.
    if (goals.error) parts.push(unread('The goals', goals.error));
    else if ((goals.value.goals || []).length) {
      parts.push(el('div', {class: 'ov-goals', 'data-part': 'goals'}, goals.value.goals.map(goalCard)));
    }

    // 3. One sentence; or, where the scan found no experiment, what to do next.
    if (!Number(counts.experiments)) {
      parts.push(states.say(null, {eyebrow: 'Overview · empty', dot: 'empty', into: true,
        sentence: 'No experiments yet.',
        body: [`Ingence listed ${plural(counts.files, 'file')} and found no experiments in them. `
          + 'The Inventory lists every file.'],
        actions: [{label: 'Open the Inventory', onclick: () => navigate('inventory')}]}));
    } else {
      parts.push(el('p', {class: 'ov-sentence', 'data-part': 'sentence', 'data-help': 'overview.found',
        title: (found.date_range && found.date_range.basis) || '', text: overviewSentence(found)}));
    }

    // 4. The areas, each with what its folder stands for.
    if ((found.areas || []).length) {
      parts.push(section('areas', 'overview.areas', 'Areas',
        [el('ul', {class: 'ov-areas'}, found.areas.map(areaRow))]));
    }

    // 5. How complete the experiments are.
    const completeness = found.completeness || {};
    if (Number(counts.experiments) || Number(completeness.raw_files)) {
      parts.push(section('completeness', 'overview.completeness', 'Completeness', [
        el('p', {class: 'ov-analyzed', text: analyzedWords(completeness)}),
        meter(Number(completeness.raw_with_analysis || 0), Number(completeness.raw_files || 0)),
        el('p', {text: experimentsWords(completeness)}),
        Number(completeness.not_read) ? el('p', {class: 'faint', text: `${plural(completeness.not_read, 'file')} not read yet.`}) : null,
      ]));
    }

    // 6. Seen once and repeated.
    if (Number(counts.experiments)) {
      parts.push(section('repeats', 'overview.repeats', 'Repeats', [el('p', {text: repeatsWords(found.repeats)})]));
    }

    // 7. The leading themes, each opening Overlap.
    if ((found.themes_top || []).length) {
      parts.push(section('themes', 'overview.themes', 'Themes', [el('p', {class: 'ov-themes'},
        found.themes_top.map((theme) => el('button', {type: 'button', class: 'chip ov-theme', 'data-theme': theme.id,
          'data-help': 'overview.themes', onclick: () => {
            openThemeInOverlap(theme.label);
          }}, [theme.label, el('span', {class: 'faint', text: ` ${fmt(theme.experiments)}`})])))]));
    }

    // 8. What Ingence assumed.
    const assumedParts = [];
    if (assumptions.error) assumedParts.push(unread('The assumptions', assumptions.error));
    else if (!(assumptions.value.assumptions || []).length) {
      assumedParts.push(el('p', {class: 'faint', text: 'Ingence assumed nothing in this scan.'}));
    } else {
      assumedParts.push(assumptionList(assumptions.value.assumptions, {limit: ASSUMPTIONS_SHOWN}),
        el('p', {}, [el('button', {type: 'button', class: 'link', 'data-help': 'assumptions.all',
          text: `All assumptions (${fmt(assumptions.value.total)})`,
          onclick: () => navigate('checks', {query: 'show=assumptions'})})]));
    }
    parts.push(section('assumptions', 'overview.assumptions', 'What Ingence assumed', assumedParts));

    // 9. The checks that matter.
    parts.push(section('checks', 'overview.checks', 'Checks',
      checks.error ? [unread('The checks', checks.error)] : checksList(checks.value.checks || [])));

    // 10. The job, while one runs.
    parts.push(jobLine());

    fill(listPane, el('div', {class: 'ov-page', 'data-view': 'overview'}, parts));
    if (!applying.error) drawJob(applying.value);
    if (focusTop) focusHeading('overview-title');
  }

  async function showOverview() {
    const turn = turns.claim(listPane);
    if (quick.phase === 'active') {
      drawQuick();
      return;
    }
    if (quick.phase === 'unknown') {
      states.loading(listPane, 'Reading the checks...');
      quick.phase = 'done';
      if (!(await quickSeen())) {
        let found = null;
        try {
          found = await api(`/api/v1/checks?limit=${QUICK_SEARCH}`);
        } catch (error) {
          found = null;
        }
        /* Only a check with a guess is a quick check: one with none is answered, not kept. */
        const guessed = found ? (found.checks || []).filter((check) => check.default) : [];
        if (!turns.owns(listPane, turn) || state.view !== 'overview') {
          if (guessed.length) quick.phase = 'unknown';
          return;
        }
        if (guessed.length) {
          quick.checks = guessed.slice(0, QUICK_LIMIT);
          quick.at = 0;
          quick.phase = 'active';
          await markQuickSeen();
          if (!turns.owns(listPane, turn) || state.view !== 'overview') return;
          drawQuick();
          return;
        }
      }
    }
    await drawOverview();
  }

  /* ------------------------------------------------------------------ Checks */

  const checksHeld = {at: 0, checks: [], answered: new Map()};

  function checkCard(holder) {
    const checks = checksHeld.checks;
    const check = checks[checksHeld.at];
    const said = el('p', {class: 'ck-said', role: 'status', 'aria-live': 'polite',
      text: checksHeld.answered.get(check.id) || ''});
    const move = (by) => {
      checksHeld.at = Math.max(0, Math.min(checks.length - 1, checksHeld.at + by));
      state.hashQuery = new URLSearchParams({check: checks[checksHeld.at].id}).toString();
      writeAddress('checks', {push: false});
      checkCard(holder);
      focusHeading('check-title');
    };
    const change = action('Change', 'checks.change', () => {
      change.hidden = true;
      options.hidden = false;
      const first = options.querySelector('button');
      if (first) first.focus();
    }, {quiet: false});
    const options = optionsNode(check, {
      said,
      onAnswered: (option) => {
        const words = `Answered: ${option.text}. It waits for Re-run.`;
        checksHeld.answered.set(check.id, words);
        said.textContent = words;
        options.hidden = true;
        change.hidden = false;
        // The pressed answer is gone with its row: the focus goes to Change, beside what it says.
        change.focus();
      },
      onCancel: () => {
        options.hidden = true;
        change.hidden = false;
        change.focus();
      },
    });
    /* Where Ingence has no guess the answers stand open: there is nothing to keep. */
    options.hidden = Boolean(check.default);
    change.hidden = !check.default;
    const previous = action('Previous', 'checks.move', () => move(-1));
    const next = action('Next', 'checks.move', () => move(1));
    previous.disabled = checksHeld.at === 0;
    next.disabled = checksHeld.at >= checks.length - 1;
    fill(holder, el('section', {class: 'ck-card', 'data-check': check.id, 'aria-labelledby': 'check-title'}, [
      el('p', {class: 'eyebrow ck-count', text: `Check ${checksHeld.at + 1} of ${checks.length}`}),
      ...questionNodes(check, 'check-title'),
      el('p', {class: 'faint ck-settles', text: `One answer settles ${fmt(check.settles_n)} ${check.settles_unit}.`}),
      el('div', {class: 'ck-actions'}, [change, previous, next]),
      options, said,
    ]));
  }

  async function showChecks() {
    const turn = turns.claim(listPane);
    states.loading(listPane, 'Reading the checks...');
    const settled = (promise) => promise.then((value) => ({value}), (error) => ({error}));
    const [checks, assumptions] = await Promise.all([
      settled(api(`/api/v1/checks?limit=${CHECKS_ALL}`)),
      settled(api('/api/v1/assumptions')),
    ]);
    if (!turns.owns(listPane, turn) || state.view !== 'checks') return;
    if (checks.error && assumptions.error) {
      states.failed(listPane, checks.error, () => navigate('checks', {push: false}));
      return;
    }
    const parts = [heading(2, 'Checks', {class: 'ck-page-title'})];
    const asked = viewQuery();
    if (checks.error) parts.push(unread('The checks', checks.error));
    else {
      checksHeld.checks = checks.value.checks || [];
      const named = checksHeld.checks.findIndex((check) => check.id === asked.get('check'));
      checksHeld.at = named >= 0 ? named : Math.min(checksHeld.at, Math.max(0, checksHeld.checks.length - 1));
      if (!checksHeld.checks.length) {
        parts.push(states.say(null, {eyebrow: 'Checks · empty', dot: 'empty', into: true,
          sentence: 'No checks wait.',
          body: ['Ingence asks a check when one answer settles many files. Questions lists every question.']}));
      } else {
        const guessed = checksHeld.checks.filter((check) => check.default).length;
        const rest = checksHeld.checks.length - guessed;
        const guesses = !rest ? 'Each already has a guess. Change a guess where it is wrong.'
          : !guessed ? 'Ingence has no guess for these. Each waits for your answer.'
            : `${plural(guessed, 'check')} already ${guessed === 1 ? 'has' : 'have'} a guess. `
              + 'Ingence has no guess for the others, which wait for your answer.';
        parts.push(el('p', {class: 'lead', text: `${plural(checks.value.total, 'check')}, the most files first. ${guesses}`}));
        const holder = el('div', {class: 'ck-holder', 'data-part': 'check'});
        parts.push(holder);
        checkCard(holder);
      }
    }
    parts.push(el('p', {}, [el('button', {type: 'button', class: 'link', 'data-help': 'checks.all_questions',
      text: 'All questions', onclick: () => navigate('review')})]));
    if (assumptions.error) parts.push(unread('The assumptions', assumptions.error));
    else {
      const items = assumptions.value.assumptions || [];
      const words = items.filter((item) => WORD_KINDS.has(item.kind));
      parts.push(section('words', 'checks.words', 'Words that matter', words.length
        ? [assumptionList(words)]
        : [el('p', {class: 'faint', text: 'Ingence assumed no spelling and no meaning in this scan.'})]));
      parts.push(el('section', {class: 'ov-part ov-all-assumptions', 'data-part': 'all-assumptions',
        'data-help': 'assumptions.all', 'aria-label': 'All assumptions'}, [
        heading(3, 'All assumptions', {id: 'all-assumptions', tabindex: '-1'}),
        ...(items.length ? kindsOf(items).map((group) => el('div', {class: 'ov-kind-group', 'data-kind': group.kind}, [
          heading(4, group.words, {class: 'ov-kind-heading'}),
          assumptionList(group.items),
        ])) : [el('p', {class: 'faint', text: 'Ingence assumed nothing in this scan.'})]),
      ]));
    }
    fill(listPane, el('div', {class: 'ck-page', 'data-view': 'checks'}, parts));
    if (asked.get('show') === 'assumptions') {
      const target = document.getElementById('all-assumptions');
      if (target) {
        const still = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
        target.scrollIntoView({block: 'start', behavior: still ? 'auto' : 'smooth'});
        target.focus({preventScroll: true});
      }
    }
  }

  /* ------------------------------------------------------------------ the goal on Experiments */

  /* "Show these" opens Experiments at ``#experiments?goal=<id>``: the goal's experiments by tier
   * above the table, each opening its story under it, and the table filtered to them on the page
   * (experiments.js filters the table on the page). One reading of the goals serves both, for a few
   * seconds. */
  const goalsRead = {at: null, when: 0};
  const GOALS_KEPT_MS = 10000;

  function readGoal(id) {
    if (!goalsRead.at || Date.now() - goalsRead.when > GOALS_KEPT_MS) {
      goalsRead.when = Date.now();
      goalsRead.at = api('/api/v1/goals').catch((error) => {
        goalsRead.at = null;
        throw error;
      });
    }
    return goalsRead.at.then((found) => (found.goals || []).find((item) => item.id === id) || null);
  }

  /* A goal's experiment, a button that opens its story in a box under it; again, it closes. */
  function goalMatchLine(match, labelOf) {
    const box = el('div', {class: 'ov-goal-story', id: `goal-story-${match.experiment_id}`});
    box.hidden = true;
    const open = el('button', {type: 'button', class: 'link ov-goal-open', 'data-help': 'overview.goal_story',
      'aria-expanded': 'false', 'aria-controls': box.id}, [el('strong', {text: match.label})]);
    const show = (experimentId) => {
      const story = global.ingenceStory;
      box.hidden = false;
      open.setAttribute('aria-expanded', 'true');
      if (!story || typeof story.render !== 'function') {
        fill(box, el('p', {class: 'note', text: 'The story did not load. Reload the page.'}));
        return;
      }
      story.render(box, experimentId, {name: labelOf, onOpen: (other) => show(other)});
    };
    open.addEventListener('click', () => {
      if (!box.hidden) {
        box.hidden = true;
        box.replaceChildren();
        open.setAttribute('aria-expanded', 'false');
        return;
      }
      show(match.experiment_id);
    });
    return el('li', {'data-experiment': match.experiment_id}, [
      open, el('span', {class: 'faint', text: `: ${match.why}`}), box]);
  }

  function goalPanel() {
    const id = viewQuery().get('goal');
    if (!id) return null;
    const panel = el('section', {class: 'ov-goal-panel', 'data-goal': id, 'aria-label': 'The goal\u2019s experiments'},
      [el('p', {class: 'faint', role: 'status', text: 'Reading the goal...'})]);
    readGoal(id).then((goal) => {
      if (!goal) {
        fill(panel, el('p', {class: 'note', text: 'Ingence no longer has this goal.'}),
          el('button', {type: 'button', class: 'link', text: 'Show all', onclick: () => navigate('experiments')}));
        return;
      }
      const labels = new Map((goal.matches || []).map((match) => [match.experiment_id, match.label]));
      const labelOf = (experimentId) => labels.get(experimentId) || '';
      fill(panel,
        heading(3, `Goal: ${goal.text}`),
        el('p', {class: 'ov-goal-line', text: goalLine(goal.summary)}),
        ...tiersOf(goal).map((group) => el('div', {class: 'ov-tier', 'data-tier': group.tier}, [
          heading(4, group.words),
          el('ul', {}, group.matches.map((match) => goalMatchLine(match, labelOf))),
        ])));
    }).catch((error) => fill(panel, unread('The goal', error)));
    return panel;
  }

  global.overviewPage = {
    fmt, plural, month, spanWords, typePlural, shareWords, overviewSentence, goalLine, tiersOf, analyzedWords,
    experimentsWords, repeatsWords, jobWords, waitsWords, answerBody, projectKey, kindsOf, QUICK_KEY, HIGHLIGHT_KEY,
  };
  global.ingenceGoalPanel = goalPanel;
  global.ingenceGoals = {read: readGoal};
  if (typeof VIEWS !== 'undefined') {
    VIEWS.overview = showOverview;
    VIEWS.checks = showChecks;
    SINGLE_VIEWS.add('overview');
    SINGLE_VIEWS.add('checks');
    onApplying((status) => {
      if (state.view === 'overview') drawJob(status);
    });
  }
})(typeof window !== 'undefined' ? window : globalThis);
