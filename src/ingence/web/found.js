/* No analysis found.
 *
 * The page shows the engine's own list (``GET /api/v1/completeness/no_analysis_found``): the
 * experiments with raw data where no analysis was found in what was read. What the engine did not
 * find may exist elsewhere, so the page never says "never analyzed". It offers a person's answer,
 * "Analyzed elsewhere", which is written to the experiment's own target through the one answer
 * route. The answer waits for Re-run, and the next run leaves the experiment off the list.
 *
 * The page groups the list by the engine's reason (forgotten, a line that stopped, none given)
 * where themes are computed, and does not group it where they are not. A row opens its experiment
 * folder in the inspector. The thresholds are said in one quiet line, each with its setting's name.
 *
 * Loaded after app.js and review.js (el, api, write, fill, states, turns, me, askActor). The pure
 * functions are on ``standingFound`` for the tests. */

(function noAnalysisFound(global) {
  /* The reasons in the order the page shows them, each with its heading. */
  const REASONS = [
    {reason: 'forgotten', heading: 'Forgotten'},
    {reason: 'stopped', heading: 'A theme that stopped'},
    {reason: null, heading: 'No reason given'},
  ];

  function count(n) {
    return (Number(n) || 0).toLocaleString('en-US');
  }

  function plural(n, one, many) {
    return `${count(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;
  }

  /* The engine's own label in American spelling (any "analys-" word a label still carries
   * becomes "analyz-"); the stored word is unchanged. */
  function american(text) {
    return String(text || '').replace(/nalys(e|ed|es|ing)\b/g, 'nalyz$1');
  }

  /* The header, with the engine's words: listed of judged, then the two reasons where themes are
   * computed, or why no reason is given where they are not. */
  function headerWords(found) {
    const listed = (found.listed || []).length;
    const judged = Number(found.judged) || 0;
    if (!judged) {
      return ['No experiment has enough raw data to judge. An experiment needs '
        + `${plural(found.settings ? found.settings.judged_from_acquisitions : 0, 'raw acquisition')} `
        + 'before Ingence lists it.'];
    }
    const said = [`${count(listed)} of ${plural(judged, 'experiment')} with raw data: no analysis `
      + 'found in the files Ingence read.'];
    if (!listed) return said;
    if (!found.themes_computed) {
      said.push(found.themes_said || 'Ingence did not compute themes, so no reason is given.');
      return said;
    }
    const forgotten = (found.listed || []).filter((row) => row.reason === 'forgotten').length;
    const stopped = (found.listed || []).filter((row) => row.reason === 'stopped').length;
    const neither = listed - forgotten - stopped;
    said.push(`${count(forgotten)} ${forgotten === 1 ? 'looks' : 'look'} forgotten: the rest of `
      + `${forgotten === 1 ? 'its' : 'their'} theme is mostly analyzed. ${count(stopped)} `
      + `${stopped === 1 ? 'belongs' : 'belong'} to a theme that stopped.`
      + (neither ? ` ${count(neither)} ${neither === 1 ? 'has' : 'have'} no reason given.` : ''));
    return said;
  }

  /* The rows grouped by reason, in the same order. They are not grouped where themes are not
   * computed (one group, no heading). */
  function groupedRows(found) {
    const rows = found.listed || [];
    if (!found.themes_computed) return [{reason: null, heading: '', rows}];
    return REASONS.map((group) => ({...group, rows: rows.filter((row) => (row.reason || null) === group.reason)}))
      .filter((group) => group.rows.length);
  }

  /* The thresholds, each with its setting's name, in one quiet line. */
  function thresholdParts(settings) {
    if (!settings) return [];
    const share = (value) => `${Math.round(100 * (Number(value) || 0))}%`;
    return [
      [`judged from ${plural(settings.judged_from_acquisitions, 'raw acquisition')} found`, 'completeness.judged_from_acquisitions'],
      [`listed below ${share(settings.listed_below_share)} of its raw data analyzed`, 'completeness.listed_below_share'],
      [`an analysis of its own is ${plural(settings.own_analysis_files, 'analysis file')}`, 'completeness.own_analysis_files'],
      [`or ${plural(settings.own_result_tables, 'results table')}`, 'completeness.own_result_tables'],
      [`forgotten when the rest of its theme is at least ${share(settings.forgotten_at_least)} analyzed`, 'completeness.forgotten_at_least'],
      [`stopped when under ${share(settings.stopped_below)}`, 'completeness.stopped_below'],
    ];
  }

  /* The last part of an experiment's folder, its name; the whole folder is its title. */
  function folderName(folder) {
    const parts = String(folder || '').split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : String(folder || '');
  }

  /* What a row says of a person's answer, and whether it wears the person's color. */
  function answeredWords(row, words) {
    const said = row.analysed_elsewhere;
    if (!said) return null;
    const label = american((words.find((word) => word.word === 'elsewhere') || {}).label || 'Analyzed elsewhere');
    if (said.said_now && !said.read_by_run) return {waiting: true, text: `${label} · waits for Re-run · ${said.actor}`};
    return {waiting: false, text: american(said.said)};
  }

  /* ------------------------------------------------------------------ the page */

  function bar(followed, raw) {
    const share = raw ? Math.max(0, Math.min(1, followed / raw)) : 0;
    return el('span', {class: 'ui-bar', 'aria-hidden': 'true'}, [
      el('span', {class: 'ui-bar-fill', style: `width:${(100 * share).toFixed(1)}%`})]);
  }

  async function answerRow(node, row, word, found, onAnswered) {
    const status = node.querySelector('.ui-status');
    const noteField = node.querySelector('input.ui-note');
    if (!(await askActor())) {
      status.className = 'ui-status note refused';
      status.textContent = 'Set your name first. Ingence saves each answer under it.';
      return;
    }
    const typed = noteField ? noteField.value.trim() : '';
    status.className = 'ui-status note';
    status.textContent = 'Saving to the answer log...';
    try {
      const written = await write('/api/review/answer', {actor: me.actor, target: row.answer_target,
        word: word.word, ...(typed ? {note: typed} : {})});
      const actor = written.actor || me.actor;
      row.analysed_elsewhere = word.word === 'elsewhere'
        ? {actor, occurred_at: written.occurred_at || '', event_id: written.event_id || '',
          read_by_run: false, said_now: true, said: ''}
        : {actor, occurred_at: written.occurred_at || '', event_id: written.event_id || '',
          read_by_run: false, said_now: false,
          said: `${actor} took back the answer that its analysis is elsewhere. It stays on the list.`};
      onAnswered(node, row);
      const after = node.querySelector('.ui-status');
      after.className = 'ui-status note';
      after.textContent = `Saved as answer ${written.sequence}.`;
    } catch (error) {
      status.className = 'ui-status note refused';
      status.textContent = String((error && error.message) || error);
    }
  }

  function rowNode(row, found) {
    const node = el('article', {class: 'ui-found-row', tabindex: '0', 'data-id': row.id,
      'data-help': 'found.row', 'aria-label': `${folderName(row.folder)}: open it in the inspector`});
    const open = () => {
      if (window.inspector) window.inspector.showEntry(row.id, detailPane);
    };
    node.addEventListener('click', (event) => {
      if (event.target.closest('button, input, label, details')) return;
      open();
    });
    node.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && event.target === node) open();
    });
    draw(node, row, found);
    return node;
  }

  function draw(node, row, found) {
    const words = found.answer_words || [];
    const answered = answeredWords(row, words);
    node.classList.toggle('ui-answered', Boolean(answered && answered.waiting));
    const elsewhere = row.analysed_elsewhere && row.analysed_elsewhere.said_now;
    const offered = words.filter((word) => (elsewhere ? word.word === 'not_elsewhere' : word.word === 'elsewhere'));
    const who = [row.person, row.theme ? `theme ${row.theme_label || row.theme}` : null].filter(Boolean).join(' · ');
    fill(node,
      el('p', {class: 'ui-found-head'}, [el('strong', {class: 'ui-folder', title: row.folder, text: folderName(row.folder)}),
        who ? el('span', {class: 'faint', text: ` · ${who}`}) : null]),
      el('p', {class: 'ui-found-counts'}, [
        `${count(row.raw_followed)} of ${count(row.raw_acquisitions)} raw files analyzed `, bar(row.raw_followed, row.raw_acquisitions),
        ` · ${plural(row.analysis_files, 'analysis file')}, ${plural(row.result_tables, 'results table')}`]),
      el('p', {class: 'ui-reason', text: row.reason_said || ''}),
      answered ? el('p', {class: answered.waiting ? 'ui-said' : 'ui-said-plain', text: answered.text}) : null,
      el('div', {class: 'ui-found-actions'}, [
        ...offered.map((word) => el('button', {type: 'button', class: 'answer ui-word',
          'data-word': word.word, 'data-help': `found.${word.word}`, title: word.meaning,
          onclick: () => answerRow(node, row, word, found, (again) => draw(again, row, found))},
        [el('strong', {text: american(word.label)})])),
        el('label', {class: 'ui-note-label'}, ['Note (optional) ', el('input', {type: 'text',
          class: 'ui-note', maxlength: '4000', 'data-help': 'found.note',
          placeholder: 'on another drive, in another folder'})]),
      ]),
      el('p', {class: 'ui-status note', role: 'status', 'aria-live': 'polite'}));
  }

  async function showNoAnalysisFound() {
    const turn = turns.claim(listPane);
    states.loading(listPane, 'Finding experiments with no analysis...');
    const found = await api('/api/v1/completeness/no_analysis_found');
    if (!turns.owns(listPane, turn) || state.view !== 'found') return;
    const sections = groupedRows(found).map((group) => el('section', {class: 'ui-group',
      'data-reason': group.reason || 'none'}, [
      group.heading ? el('h3', {text: `${group.heading} (${count(group.rows.length)})`}) : null,
      ...group.rows.map((row) => rowNode(row, found)),
    ]));
    const answered = found.answered || [];
    const thresholds = thresholdParts(found.settings);
    fill(listPane,
      el('h2', {text: 'No analysis found', 'data-help': 'section.found'}),
      ...headerWords(found).map((sentence, index) => el('p', {class: index ? '' : 'ui-lead', text: sentence})),
      el('p', {class: 'faint', text: 'An analysis may exist in a place Ingence did not read, such as '
        + 'another drive or folder. Say so on a row. After Re-run, the row leaves the list.'}),
      ...sections,
      answered.length ? el('section', {class: 'ui-group', 'data-reason': 'answered'}, [
        el('h3', {text: `Answered, applied in this scan (${count(answered.length)})`}),
        ...answered.map((row) => rowNode(row, found))]) : null,
      thresholds.length ? el('p', {class: 'ui-thresholds', 'data-help': 'found.thresholds'},
        ['Thresholds (Advanced \u203a Settings): ', ...thresholds.flatMap(([words, name], index) => [
          index ? ' · ' : '', words, ' ', el('code', {class: 'ui-small', text: name})]), '.']) : null);
  }

  global.standingFound = {REASONS, american, headerWords, groupedRows, thresholdParts, folderName, answeredWords};
  if (typeof VIEWS !== 'undefined') VIEWS.found = showNoAnalysisFound;
})(typeof window !== 'undefined' ? window : globalThis);
