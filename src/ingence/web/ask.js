/* Ask: the experiments that relate to a research question.
 *
 * Ingence shows the evidence for a question, never an answer to it. People arrive with research
 * questions. Ask shows the experiments that relate to a question, each with the reason, in the
 * engine's own words and classes (no language model): ``GET /api/v1/ask?q=``.
 *
 * The page, top to bottom, shows: the question; "Save as goal"; how the question was read (the
 * words kept, the words left out, the words nothing knows), the words each was widened to, and the
 * filters read from it; the experiments, by tier (Named, then Associated), each with the words it
 * matched and how, its dates and its raw files analyzed; and apart, under its own heading, the
 * experiments in the same theme as a match, each labelled as a suggestion.
 *
 * "Save as goal" posts ``POST /api/goals {text}``. The Overview then shows the goal's answer card.
 * A build that records nothing says so in the engine's words.
 *
 * Opening an experiment opens its story beside the list, with
 * ``window.ingenceStory.render(container, experimentId)`` where story.js is loaded. Otherwise the
 * experiment opens as its row in Experiments does.
 *
 * The header's box and the Overview's box open this view (``#ask?q=``, app.js's ``askFor``). An old
 * ``#search`` address opens it too (app.js's ``VIEW_ALIASES``).
 *
 * Loaded after app.js (el, api, write, states, turns, navigate, VIEWS, listPane, detailPane,
 * viewQuery, askState) and review.js (fill). The pure functions are on ``askPage`` for the tests. */

(function askScript(global) {
  'use strict';

  /* The tiers listed as matches, in order, and their headings. */
  const TIER_ORDER = ['named', 'associated'];
  const TIER_WORDS = {named: 'Named', associated: 'Associated'};
  /* The tier that is only a suggestion: listed apart, with the same-theme suggestions. */
  const SUGGESTED = 'same_theme';
  /* How many same-theme suggestions are listed, and how many of those are named from the run, one
   * read each, where the route gives no name. */
  const SUGGESTIONS_SHOWN = 50;
  const NAMES_READ = 12;

  /* ------------------------------------------------------------------ pure words */

  function listWords(words) {
    return (words || []).map(String).join(', ');
  }

  /* "Read: DAPI, fixed. Left out: which, the. Not known: zorbit." Each part only where it has words. */
  function readWords(read) {
    const r = read || {};
    return [
      (r.kept || []).length ? `Read: ${listWords(r.kept)}.` : 'Read: nothing.',
      (r.dropped || []).length ? `Left out: ${listWords(r.dropped)}.` : '',
      (r.unknown || []).length ? `Not known: ${listWords(r.unknown)}.` : '',
    ].filter(Boolean).join(' ');
  }

  /* "Years: 2024. File types: images. Areas: Scope_1." Nothing where the question names no filter. */
  function filterWords(filters) {
    const f = filters || {};
    return [
      (f.years || []).length ? `Years: ${listWords(f.years)}.` : '',
      (f.types || []).length ? `File types: ${listWords(f.types)}.` : '',
      (f.areas || []).length ? `Areas: ${listWords(f.areas)}.` : '',
    ].filter(Boolean).join(' ');
  }

  /* One widened word: "nuclear stain: DAPI, Hoechst", and how, in the engine's words. */
  function expansionWords(expansion) {
    const e = expansion || {};
    return {label: `${e.word}: ${listWords(e.to)}`, how: String(e.how || '')};
  }

  /* The words an experiment matched, one line for each way they matched:
   * "hela, dmso (in the experiment's name)". */
  function matchedLines(matched) {
    const byHow = new Map();
    for (const match of matched || []) {
      const how = String(match.how || '');
      if (!byHow.has(how)) byHow.set(how, []);
      byHow.get(how).push(String(match.word));
    }
    return [...byHow].map(([how, words]) => `${listWords(words)}${how ? `: ${how}` : ''}`);
  }

  /* "2024-03-12 to 2024-03-15", one day alone, or "Undated". */
  function datesWords(dates) {
    const first = dates && dates.first;
    const last = dates && dates.last;
    if (!first && !last) return 'Undated';
    if (!first || !last || first === last) return first || last;
    return `${first} to ${last}`;
  }

  /* The raw files analyzed, as a share the engine counted: "Raw files analyzed: 80%". The unknown
   * stays unknown: an experiment with no raw files says so, never 0%. */
  function completenessWords(completeness) {
    if (completeness == null) return 'No raw files found';
    const percent = Math.round(Math.max(0, Math.min(1, Number(completeness))) * 100);
    return `Raw files analyzed: ${percent}%`;
  }

  /* The matches by tier, each tier apart, in the order named, associated; a tier with none is left
   * out. */
  function tiersOf(experiments) {
    return TIER_ORDER.map((tier) => ({tier, words: TIER_WORDS[tier],
      experiments: (experiments || []).filter((item) => item.score_tier === tier)}))
      .filter((group) => group.experiments.length);
  }

  /* The suggestions, apart from the matches: the experiments the route tiers as same theme, then
   * the route's same-theme list, each experiment once. */
  function suggestionsOf(answer) {
    const a = answer || {};
    const seen = new Set();
    const found = [];
    for (const item of a.experiments || []) {
      if (item.score_tier !== SUGGESTED || seen.has(item.experiment_id)) continue;
      seen.add(item.experiment_id);
      found.push({experiment_id: item.experiment_id, label: item.label, via_theme: '', matched: item.matched || []});
    }
    const matched = new Set((a.experiments || []).map((item) => item.experiment_id));
    for (const item of a.same_theme || []) {
      if (seen.has(item.experiment_id) || matched.has(item.experiment_id)) continue;
      seen.add(item.experiment_id);
      found.push({experiment_id: item.experiment_id, label: item.label || '', via_theme: item.via_theme || '', matched: []});
    }
    return found;
  }

  /* A suggestion's line: "Suggestion: shares the theme zstack imaging." */
  function suggestionWords(item) {
    return item.via_theme ? `Suggestion: shares the theme ${item.via_theme}.` : 'Suggestion: in the same theme as a match.';
  }

  /* What "Save as goal" says once posted. A build that records nothing says so in the engine's
   * words. */
  function goalSaid(answer) {
    if (answer && answer.recorded === false && answer.said) return String(answer.said);
    return 'Saved as a goal. The Overview shows it.';
  }

  /* How many experiments the answer lists as matches: "3 experiments match." */
  function countWords(n) {
    const count = Number(n || 0);
    return `${count.toLocaleString('en-US')} ${count === 1 ? 'experiment matches' : 'experiments match'}.`;
  }

  /* ------------------------------------------------------------------ opening an experiment */

  /* The story beside the list, or, where story.js is not loaded, the experiment as its row in
   * Experiments opens it. */
  async function openExperiment(id, label, status) {
    const story = global.ingenceStory;
    if (story && typeof story.render === 'function') {
      // The pane is this story's now: a read another page began there must not draw over it.
      turns.claim(detailPane);
      const holder = el('div', {class: 'ask-story', 'data-story': id});
      fill(detailPane, el('h2', {class: 'ask-story-title', text: label || 'Experiment'}), holder);
      await story.render(holder, id);
      return;
    }
    const years = global.ingenceYears;
    if (years) {
      if (status) status.textContent = 'Opening...';
      const model = await years.load();
      if (model && model.byId && model.byId.has(id)) {
        if (status) status.textContent = '';
        await navigate('years');
        years.open({kind: 'exp', id});
        years.select({kind: 'exp', id});
        return;
      }
    }
    if (status) status.textContent = '';
    await navigate('experiments');
  }

  function openButton(id, label, status) {
    return el('button', {type: 'button', class: 'link ask-open', 'data-help': 'ask.open', text: label,
      onclick: () => {
        openExperiment(id, label, status).catch((error) => {
          if (status) status.textContent = `Ingence could not open it: ${(error && error.message) || error}`;
        });
      }});
  }

  /* ------------------------------------------------------------------ the page */

  function heading(level, text, attributes = {}) {
    return el(`h${level}`, {text, ...attributes});
  }

  function saveGoal(question) {
    const said = el('span', {class: 'ask-said', role: 'status', 'aria-live': 'polite'});
    const button = el('button', {type: 'button', class: 'answer', 'data-help': 'ask.save_goal',
      'data-ask': 'save-goal'}, [el('strong', {text: 'Save as goal'})]);
    /* The pressed button keeps the focus: it is held with aria-disabled, and its clicks are
     * ignored, while the goal is written and after. */
    button.addEventListener('click', async () => {
      if (button.getAttribute('aria-disabled') === 'true') return;
      button.setAttribute('aria-disabled', 'true');
      try {
        const answer = await write('/api/goals', {text: question});
        said.textContent = goalSaid(answer);
      } catch (error) {
        button.removeAttribute('aria-disabled');
        said.textContent = `Not saved: ${(error && error.message) || error}`;
      }
    });
    return el('p', {class: 'ask-actions'}, [button, ' ', said]);
  }

  function readPart(answer) {
    const filters = filterWords(answer.filters);
    const expanded = (answer.expanded || []).map(expansionWords);
    return el('section', {class: 'ask-part ask-read', 'data-part': 'read', 'data-help': 'ask.read',
      'aria-label': 'Your question'}, [
      heading(3, 'Your question'),
      el('p', {'data-read': '', text: readWords(answer.read)}),
      filters ? el('p', {'data-filters': '', 'data-help': 'ask.filters', text: filters}) : null,
      expanded.length ? el('div', {class: 'ask-included', 'data-help': 'ask.included'}, [
        el('p', {class: 'ask-label', text: 'Words included'}),
        el('ul', {}, expanded.map((item, index) => el('li', {'data-expansion': String(index)}, [
          el('span', {class: 'ask-expansion', text: item.label}),
          item.how ? el('span', {class: 'faint', text: ` (${item.how})`}) : null,
        ]))),
      ]) : null,
    ]);
  }

  function meter(completeness) {
    if (completeness == null) return null;
    const share = Math.max(0, Math.min(1, Number(completeness)));
    return el('span', {class: 'ask-meter', 'aria-hidden': 'true'}, [
      el('span', {class: 'ask-meter-fill', style: `width:${(share * 100).toFixed(1)}%`})]);
  }

  function experimentLine(item) {
    const status = el('span', {class: 'ask-status faint', role: 'status', 'aria-live': 'polite'});
    return el('li', {class: 'ask-experiment', 'data-experiment': item.experiment_id, 'data-tier': item.score_tier}, [
      el('p', {class: 'ask-name'}, [openButton(item.experiment_id, item.label, status), ' ', status]),
      ...matchedLines(item.matched).map((line) => el('p', {class: 'ask-matched', text: `Matched: ${line}`})),
      el('p', {class: 'ask-facts'}, [
        el('span', {class: 'ask-dates', text: datesWords(item.dates)}),
        el('span', {class: 'faint', text: ' · '}),
        meter(item.completeness),
        el('span', {class: item.completeness == null ? 'ask-raw exp-unknown' : 'ask-raw',
          text: completenessWords(item.completeness)}),
      ]),
    ]);
  }

  function matchesPart(answer) {
    const tiers = tiersOf(answer.experiments);
    if (!tiers.length) {
      return states.say(null, {eyebrow: 'Ask · empty', dot: 'empty', into: true,
        sentence: 'No experiment matches these words.',
        body: ['Try a broader word, or check the spelling.']});
    }
    const count = tiers.reduce((sum, group) => sum + group.experiments.length, 0);
    return el('section', {class: 'ask-part ask-matches', 'data-part': 'experiments', 'aria-label': 'Experiments'}, [
      heading(3, 'Experiments'),
      el('p', {class: 'faint', 'data-count': String(count), text: countWords(count)}),
      ...tiers.map((group) => el('div', {class: 'ask-tier', 'data-tier': group.tier, 'data-help': 'ask.tier'}, [
        heading(4, group.words),
        el('ul', {class: 'ask-list'}, group.experiments.map(experimentLine)),
      ])),
    ]);
  }

  /* An experiment the route names only by its identifier is named from the run, where it can be. */
  function nameFromRun(id, button) {
    api(`/api/v1/entry?id=${encodeURIComponent(id)}`).then((found) => {
      const entry = found && found.entry;
      const name = entry && (entry.name || entry.path);
      if (name && button.isConnected) button.textContent = name;
    }).catch(() => null);
  }

  function suggestionsPart(answer) {
    const items = suggestionsOf(answer);
    if (!items.length) return null;
    return el('section', {class: 'ask-part ask-suggestions', 'data-part': 'same-theme', 'data-help': 'ask.same_theme',
      'aria-label': 'Same theme'}, [
      heading(3, 'Same theme'),
      el('p', {class: 'faint', text: 'Suggestions: experiments in a theme with a match. Your words may not be in them.'}),
      el('ul', {class: 'ask-list'}, items.slice(0, SUGGESTIONS_SHOWN).map((item, index) => {
        const status = el('span', {class: 'ask-status faint', role: 'status', 'aria-live': 'polite'});
        const button = openButton(item.experiment_id, item.label || 'An experiment of this theme', status);
        if (!item.label && index < NAMES_READ) nameFromRun(item.experiment_id, button);
        return el('li', {class: 'ask-experiment ask-suggestion', 'data-experiment': item.experiment_id,
          'data-tier': SUGGESTED}, [
          el('p', {class: 'ask-name'}, [button, ' ', status]),
          el('p', {class: 'ask-suggested', text: suggestionWords(item)}),
          ...matchedLines(item.matched).map((line) => el('p', {class: 'ask-matched', text: `Matched: ${line}`})),
        ]);
      })),
    ]);
  }

  function askHeading(question) {
    return heading(2, `Ask: ${question}`, {class: 'ask-question', 'data-ask-view': '', tabindex: '-1'});
  }

  async function showAsk() {
    const question = (viewQuery().get('q') || '').trim();
    askState.query = question;
    const box = document.getElementById('search-box');
    if (box && document.activeElement !== box) box.value = question;
    const turn = turns.claim(listPane);
    if (!question) {
      fill(listPane, el('div', {class: 'ask-page', 'data-view': 'ask'}, [
        heading(2, 'Ask'),
        el('p', {class: 'lead', text: 'Type a question in the Ask box and press Enter.'})]));
      return;
    }
    states.loading(listPane, 'Reading the question...');
    let answer;
    try {
      answer = await api(`/api/v1/ask?${new URLSearchParams({q: question})}`);
    } catch (error) {
      if (turns.owns(listPane, turn) && state.view === 'ask') {
        states.failed(listPane, error, () => navigate('ask', {push: false, query: new URLSearchParams({q: question}).toString()}));
      }
      return;
    }
    if (!turns.owns(listPane, turn) || state.view !== 'ask') return;
    fill(listPane, el('div', {class: 'ask-page', 'data-view': 'ask'}, [
      askHeading(question),
      el('p', {class: 'faint ask-lead', text: 'Ingence lists the experiments that relate to your words, with the reason for each.'}),
      saveGoal(question),
      readPart(answer),
      matchesPart(answer),
      suggestionsPart(answer),
    ]));
  }

  global.askPage = {
    readWords, filterWords, expansionWords, matchedLines, datesWords, completenessWords, tiersOf,
    suggestionsOf, suggestionWords, goalSaid, countWords,
  };
  if (typeof VIEWS !== 'undefined') {
    VIEWS.ask = showAsk;
  }
})(typeof window !== 'undefined' ? window : globalThis);
