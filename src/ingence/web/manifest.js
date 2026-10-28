/* What changed: each run's manifest, in sentences, on History.
 *
 * The runs are the ones History already reads (``/api/runs``), newest first, the current one marked
 * and open. Opening a run reads its manifest (``GET /api/v1/manifest?run=<id>``) once. The page
 * shows what changed from the run it was built from, as the engine's own sentences, then the
 * decisions that took effect in it (who, when, where, the answer), the goings back to an earlier
 * run, and what the themes said. A manifest that cannot be computed shows the route's refusal in
 * its own words with a way to try again.
 *
 * Loaded after app.js and review.js (el, api, fill, states, turns). The pure functions are on
 * ``standingManifest`` for the tests. */

(function whatChanged(global) {
  /* How many of a run's sentences are shown before "show the other n" (a guess, flagged). */
  const FIRST_SENTENCES = 12;

  /* The runs newest first: by their number where they carry one, else by when they began. */
  function newestFirst(runs) {
    return [...(runs || [])].sort((a, b) => {
      if (a.number != null && b.number != null) return b.number - a.number;
      return String(b.began || '').localeCompare(String(a.began || ''));
    });
  }

  /* A moment as a person reads it, "2026-10-09 04:48 UTC"; anything else as it came. */
  function when(stamp) {
    const found = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(String(stamp || ''));
    return found ? `${found[1]} ${found[2]} UTC` : String(stamp || '');
  }

  /* Where a decision was about, in words: the engine's ``place`` ("the analysis of <folder>",
   * "every conclusion read as ... by one rule", a folder's path, "the word ..."). */
  function plainPlace(decision) {
    return String(decision.place || decision.target || '');
  }

  /* An answer's label in American spelling (any "analys-" word becomes "analyz-"). */
  function american(text) {
    return String(text || '').replace(/nalys(e|ed|es|ing)\b/g, 'nalyz$1');
  }

  /* One decision that took effect, in a line: who, when, where, the answer. */
  function decisionLine(decision) {
    return `${decision.actor}, ${when(decision.occurred_at)}: ${plainPlace(decision)} — `
      + `${american(decision.answer)}.`;
  }

  /* What a decision changed, where the engine traced it; said apart from the line. */
  function tracedWords(decision) {
    if (decision.files_changed == null) return 'Ingence did not trace what it changed.';
    const beneath = decision.files_beneath == null ? ''
      : ` of ${Number(decision.files_beneath).toLocaleString('en-US')}`;
    return `${Number(decision.files_changed).toLocaleString('en-US')}${beneath} files below it have `
      + 'new results.' + (decision.itself_changed ? ' Its own results changed too.' : '');
  }

  /* The run's own sentences without the ones said again below them: the decisions', the goings
   * back's and the themes' (the engine's ``said`` holds every sentence in reading order). */
  function changeSentences(manifest) {
    const below = new Set([
      ...(manifest.decisions || []).map((decision) => decision.said),
      ...(manifest.withdrawals || []).map((went) => went.said),
      ...((manifest.themes && manifest.themes.said) || []),
    ]);
    return (manifest.said || []).filter((sentence) => !below.has(sentence));
  }

  /* What the themes said, each sentence once. */
  function themeSentences(manifest) {
    return [...new Set((manifest.themes && manifest.themes.said) || [])];
  }

  /* ------------------------------------------------------------ quality figures */

  /* A figure's value as words, a count with what it is out of ("480 of 508 files"), or a
   * duration in seconds ("2 min 5 s"). ``now`` reads the row's own, anything else its compared
   * run's. A figure the run does not state is said as that, never as zero. */
  function seconds(total) {
    const whole = Math.round(total);
    if (whole < 60) return `${whole.toLocaleString('en-US')} s`;
    const minutes = Math.floor(whole / 60);
    const rest = whole % 60;
    if (minutes < 60) return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const left = minutes % 60;
    return left ? `${hours} h ${left} min` : `${hours} h`;
  }

  const MEGABYTE = 1024 * 1024;

  function qualityValue(row, which = 'now') {
    const was = which === 'was';
    const state = was ? row.was_state : row.state;
    const count = was ? row.was_count : row.count;
    const total = was ? row.was_total : row.total;
    const time = was ? row.was_seconds : row.seconds;
    if (state == null) return 'no earlier value';
    if (state === 'not_run') return 'not measured in this scan';
    if (state === 'not_recorded') return 'not recorded';
    if (time != null) return seconds(time);
    if (count == null) return 'no earlier value';
    if (row.figure === 'cost.peak_memory') return `${Math.round(count / MEGABYTE).toLocaleString('en-US')} MiB`;
    const counted = Number(count).toLocaleString('en-US');
    if (total == null) return `${counted} ${row.unit}`;
    return `${counted} of ${Number(total).toLocaleString('en-US')} ${row.unit}`;
  }

  /* The change column: the count's change with its sign. When only what it is out of moved
   * (5 of 5 read, then 5 of 6) the count did not change, so the column says the whole did. A
   * figure the run no longer states says that. Never a signed zero. */
  function qualityChange(row) {
    if (!row.changed) return '';
    if (row.state !== 'counted') return 'no longer stated';
    if (row.change == null) return '';
    if (row.change === 0) {
      return row.total !== row.was_total
        ? `whole ${Number(row.was_total).toLocaleString('en-US')} to ${Number(row.total).toLocaleString('en-US')}`
        : '';
    }
    const size = Math.abs(row.change);
    const shown = row.seconds != null ? seconds(size)
      : row.figure === 'cost.peak_memory' ? `${Math.round(size / MEGABYTE).toLocaleString('en-US')} MiB`
        : Math.round(size).toLocaleString('en-US');
    return `${row.change > 0 ? '+' : '-'}${shown}`;
  }

  /* Why a figure is not stated, in the engine's own words: its pass does not reach it, or the
   * reason it recorded. */
  function qualityReason(row) {
    if (row.state === 'not_run') return 'not measured in this scan';
    return row.note ? `not recorded (${row.note})` : 'not recorded';
  }

  /* The rows a run states, grouped by heading in the order the engine sends them; the rows it
   * does not state (not run, not recorded) apart, each once with its reason. */
  function qualityGroups(quality) {
    const stated = [];
    const unstated = new Map();
    for (const row of (quality && quality.rows) || []) {
      if (row.state === 'counted') stated.push(row);
      else if (!unstated.has(row.figure)) unstated.set(row.figure, row);
    }
    const sections = [];
    for (const row of stated) {
      let group = sections.find((one) => one.section === row.section);
      if (!group) {
        group = {section: row.section, rows: []};
        sections.push(group);
      }
      group.rows.push(row);
    }
    return {sections, unstated: [...unstated.values()]};
  }

  const SECTION_WORDS = {found: 'Found', read: 'Read', names: 'Names', conclusions: 'Results',
    cost: 'Cost', columns: 'Same columns', design: 'Design grid', themes: 'Themes'};

  /* A share is drawn as a bar of its count in its whole, never as a bare percentage. */
  function qualityBar(row) {
    if (row.count == null || !row.total) return null;
    const share = Math.max(0, Math.min(1, row.count / row.total));
    return el('span', {class: 'ui-bar', 'aria-hidden': 'true'}, [
      el('span', {class: 'ui-bar-fill', style: `width:${(100 * share).toFixed(1)}%`})]);
  }

  /* Each figure beside the previous run's: the figure, this run's value and the previous run's, the
   * runs named by number and date. The change is said in words only where the engine states it
   * (``+12``), never in a color: a count that went up is not better or worse. The first run has no
   * previous run, and says so where that value would be. */
  function qualityTable(group, names) {
    const body = group.rows.map((row) => {
      const change = qualityChange(row);
      return el('tr', {title: row.method}, [
        el('th', {scope: 'row', text: row.label}),
        el('td', {class: 'ui-quality-now'}, [el('span', {text: qualityValue(row)}),
          change ? el('span', {class: 'ui-change', title: 'since the previous scan', text: ` ${change}`}) : null,
          qualityBar(row)]),
        el('td', {class: 'ui-then ui-previous', text: names.was ? qualityValue(row, 'was') : 'no previous scan'})]);
    });
    return el('table', {class: 'ui-quality ui-quality-runs'}, [
      el('caption', {text: SECTION_WORDS[group.section] || group.section}),
      el('colgroup', {}, [el('col', {class: 'ui-col-figure'}), el('col'), el('col')]),
      el('thead', {}, [el('tr', {}, [el('th', {text: 'Figure'}),
        el('th', {}, ['This scan', names.now ? el('span', {class: 'ui-runname', text: names.now}) : null]),
        el('th', {}, ['Previous scan', el('span', {class: 'ui-runname', text: names.was || 'none'})])])]),
      el('tbody', {}, body)]);
  }

  /* The run's quality figures: the sentences for those that moved, then every figure beside the
   * compared run's, each with its method on hover, and the figures this pass does not state. */
  function drawQuality(quality, names = {}) {
    if (!quality) return null;
    if (!quality.available) {
      return [el('h4', {text: 'Quality figures'}), el('p', {class: 'faint', text: quality.why_not || ''})];
    }
    const groups = qualityGroups(quality);
    const moved = quality.said || [];
    return [el('h4', {text: 'Quality figures'}),
      quality.why_not ? el('p', {class: 'faint', text: quality.why_not}) : null,
      quality.versions_said ? el('p', {class: 'faint ui-versions', text: quality.versions_said}) : null,
      moved.length ? sentences(moved, 'ui-quality-said') : null,
      el('details', {class: 'ui-why ui-quality-all', 'data-help': 'manifest.quality'}, [
        el('summary', {text: `All figures of this scan (${groups.sections.reduce((n, g) => n + g.rows.length, 0).toLocaleString('en-US')})`}),
        ...groups.sections.map((group) => qualityTable(group, names)),
        groups.unstated.length ? el('div', {class: 'ui-small'}, [
          el('p', {text: 'Not stated by this scan:'}),
          el('ul', {}, groups.unstated.map((row) => el('li', {text: `${row.label}: ${qualityReason(row)}.`})))]) : null])];
  }

  /* ------------------------------------------------------------------ the page */

  function sentences(list, className) {
    const items = list.map((sentence) => el('li', {text: sentence}));
    const shown = el('ul', {class: className}, items.slice(0, FIRST_SENTENCES));
    const rest = items.slice(FIRST_SENTENCES);
    if (!rest.length) return [shown];
    return [shown, el('button', {type: 'button', class: 'link ui-more', 'data-help': 'manifest.more',
      text: `show the other ${rest.length.toLocaleString('en-US')}`,
      onclick: (event) => {
        shown.append(...rest);
        event.currentTarget.remove();
      }})];
  }

  function drawManifest(body, manifest) {
    const decisions = manifest.decisions || [];
    const withdrawals = manifest.withdrawals || [];
    const themes = themeSentences(manifest);
    const said = changeSentences(manifest);
    const nothing = !said.length && !decisions.length && !withdrawals.length && !themes.length;
    fill(body,
      manifest.compared_label ? el('p', {class: 'faint', text: `Compared with ${manifest.compared_label}.`}) : null,
      said.length ? sentences(said, 'ui-said-list') : null,
      nothing ? el('p', {class: 'nothing', text: 'Nothing changed in this scan.'}) : null,
      decisions.length ? [el('h4', {text: `Answers that took effect (${decisions.length.toLocaleString('en-US')})`}),
        el('ul', {class: 'ui-decisions'}, decisions.map((decision) => el('li', {class: 'ui-human-line'}, [
          el('span', {text: decisionLine(decision)}),
          el('span', {class: 'faint', text: ` ${tracedWords(decision)}`}),
          el('details', {class: 'ui-why', 'data-help': 'manifest.why'}, [el('summary', {text: 'Why'}),
            el('p', {class: 'ui-small'}, [decision.said, ' ', el('code', {text: decision.target})])])])))]
        : null,
      withdrawals.length ? [el('h4', {text: `Going back (${withdrawals.length.toLocaleString('en-US')})`}),
        el('ul', {class: 'ui-withdrawals'}, withdrawals.map((went) => el('li', {class: 'ui-human-line',
          text: went.said})))] : null,
      themes.length ? [el('h4', {text: 'Themes'}), el('ul', {class: 'ui-themes'},
        themes.map((sentence) => el('li', {text: sentence})))] : null,
      drawQuality(manifest.quality, {now: manifest.run_label, was: manifest.compared_label}));
  }

  async function readManifest(body, run) {
    const turn = turns.claim(body);
    states.loading(body, `Loading what changed in ${run.label || run.run_id}...`);
    try {
      const manifest = await api(`/api/v1/manifest?${new URLSearchParams({run: run.run_id})}`);
      if (turns.owns(body, turn)) drawManifest(body, manifest);
    } catch (error) {
      if (turns.owns(body, turn)) states.failed(body, error, () => readManifest(body, run));
    }
  }

  function runBlock(run) {
    const body = el('div', {class: 'ui-manifest-body'});
    const block = el('details', {class: run.current ? 'ui-run current' : 'ui-run',
      'data-run': run.run_id, 'data-help': 'manifest.run'}, [
      el('summary', {}, [el('span', {class: 'run-name', title: run.run_id, text: run.label || run.run_id}),
        run.current ? el('span', {class: 'tag now', text: 'current'}) : null,
        run.described ? el('span', {class: 'faint', text: ` · ${run.described}`}) : null]),
      body]);
    let read = false;
    block.addEventListener('toggle', () => {
      if (!block.open || read) return;
      read = true;
      readManifest(body, run);
    });
    if (run.current) block.open = true;
    return block;
  }

  async function showWhatChanged() {
    const turn = turns.claim(listPane);
    states.loading(listPane, 'Loading the scans...');
    const listed = await api('/api/runs');
    if (!turns.owns(listPane, turn) || state.view !== 'manifest') return;
    const runs = newestFirst(listed.runs || []);
    if (!runs.length) {
      states.empty(listPane, 'No scans yet. Start a scan, and each scan shows here what changed.');
      return;
    }
    fill(listPane,
      el('h2', {text: 'What changed', 'data-help': 'section.manifest'}),
      el('p', {class: 'ui-lead', text: `${runs.length.toLocaleString('en-US')} ${runs.length === 1 ? 'scan' : 'scans'}, `
        + 'newest first. Open one to read what changed since the scan before it, and the '
        + 'answers that took effect.'}),
      ...runs.map(runBlock));
  }

  global.standingManifest = {FIRST_SENTENCES, newestFirst, when, plainPlace, american, decisionLine,
    tracedWords, changeSentences, themeSentences, qualityValue, qualityChange, qualityReason, qualityGroups, seconds};
  if (typeof VIEWS !== 'undefined') VIEWS.manifest = showWhatChanged;
})(typeof window !== 'undefined' ? window : globalThis);
