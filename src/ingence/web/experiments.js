// @ts-check
/* Experiments > Experiments: the experiments the engine found, how complete each is, and how its
 * files were tied to it.
 *
 * One table (``GET /api/v1/experiments``), filtered, sorted and paged by the engine, so this page
 * counts nothing: every number on it is a count the engine served, with its denominator beside it.
 * Each row says how the folder was found (what it holds, or its dated name), its dates, its files
 * (by stage and by kind, behind a disclosure), its raw files, analysis files and results tables,
 * how many raw files are followed by an analysis file, and how well its files were characterized (a
 * stage read and a role read, each of the experiment's files).
 *
 * The bar comes first. The second column is the raw files followed, shown to a person as "Raw files
 * analyzed", because the word "followed" is not used in what a person reads. The code keeps the
 * engine's field names. The column is a bar the full width of its cell, with the counts in bold
 * beside it ("12 / 40") and the engine's sentence behind "Why", closed. One legend above the table
 * names the bar's two shades.
 *
 * The page shows its pairing assumption. A raw file counts as followed when a pair links it to an
 * analysis file or a results table, whether a person confirmed the pair or the engine only proposed
 * it. Each row splits the count into two parts. One part is followed through pairs a person
 * confirmed (the person's color, the only place this page uses it). The other is followed through
 * the engine's own pairs and links (pairs it proposed, derivations it published, files named for a
 * raw file), assumed right.
 *
 * Advanced > Suggested pairs lists every proposed pair. It cannot be narrowed to one experiment,
 * and the page says so beside the link.
 *
 * A row opens the experiment's story under it (story.js), with the way to Overlap's By year mode
 * and to its files. The workflows stand above the list, each with "Change" and "Turn off". The
 * unknown stays unknown: no raw file found is "nothing to follow", never 0%, and an undated
 * experiment says so.
 *
 * Topics (a person's own named sets of groupings) are a column of chips in the person's color and a
 * filter. The engine reads both from the Record when asked (``topics`` on each row, ``topic=`` on
 * the request), and a chip filters to its topic. Topics that could not be read, or that a run with
 * no rules could not count, are said in words, never left out.
 *
 * Loaded after app.js (el, api, states, turns, navigate, VIEWS, PAGES, SINGLE_VIEWS), review.js
 * (fill) and years.js (window.ingenceYears). The pure functions are on ``experimentsView`` for the
 * tests. The view is registered only where ``VIEWS`` exists. */

(function experimentsModule(/** @type {any} */ global) {
  'use strict';

  /** @typedef {import('./types/api').ExperimentsResponse} ExperimentsResponse */
  /** @typedef {import('./types/api').ExperimentRow} ExperimentRow */
  /** @typedef {import('./types/api').ExperimentClassCount} ClassCount */
  /**
   * What the page asks the engine for, and keeps while a person looks at an experiment and returns.
   * @typedef {{sort: string, order: string, person: string, line: string, topic: string,
   *   state: string, q: string, offset: number, limit: number}} Query
   */

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  /** How many rows a page lists. */
  const PAGE_SIZE = 50;
  /** The sorts the engine offers, with the words for each. */
  const SORTS = [['name', 'Name'], ['date', 'Date'], ['files', 'Files'], ['completeness', 'Completeness']];
  /** The order each sort starts in: the biggest experiments first, the rest in their natural order. */
  const FIRST_ORDER = {name: 'asc', date: 'asc', files: 'desc', completeness: 'asc'};

  /** @returns {Query} */
  function defaults() {
    return {sort: 'name', order: 'asc', person: '', line: '', topic: '', state: '', q: '', offset: 0,
      limit: PAGE_SIZE};
  }

  /** @param {unknown} n */
  function count(n) {
    return (Number(n) || 0).toLocaleString('en-US');
  }

  /**
   * @param {unknown} n
   * @param {string} one
   * @param {string} [many]
   */
  function plural(n, one, many) {
    return `${count(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;
  }

  /**
   * A day as a person says it ("5 Feb 2024"), from an ISO day or instant, in UTC.
   * @param {string | null | undefined} at
   */
  function dayWords(at) {
    const text = String(at || '');
    const parsed = /^\d{4}-\d{2}-\d{2}/.test(text) ? Date.parse(`${text.slice(0, 10)}T00:00:00Z`) : Date.parse(text);
    if (Number.isNaN(parsed)) return text;
    const moment = new Date(parsed);
    return `${moment.getUTCDate()} ${MON[moment.getUTCMonth()]} ${moment.getUTCFullYear()}`;
  }

  /**
   * An experiment's first and last dates. An unknown date is said so, never guessed.
   * @param {ExperimentRow} row
   */
  function datesWords(row) {
    const first = row.date ? dayWords(row.date) : '';
    const last = row.last_file_time ? dayWords(row.last_file_time) : '';
    if (first && last && first !== last) return `${first} to ${last}`;
    if (first) return first;
    return last ? `undated; the last file was written ${last}` : 'undated';
  }

  /**
   * What a row says of the raw files followed: the engine's own sentence, the counts, and how wide
   * each part of the bar is (a drawing only; the numbers are the engine's).
   * @param {ExperimentRow} row
   */
  function followedView(row) {
    const held = row.followed;
    const raw = held.raw_found;
    return {
      said: held.said,
      known: raw > 0,
      raw,
      followed: held.followed,
      confirmed: held.by_confirmed,
      assumed: held.assumed,
      confirmedPercent: raw ? (100 * held.by_confirmed) / raw : 0,
      assumedPercent: raw ? (100 * held.assumed) / raw : 0,
    };
  }

  /**
   * How well an experiment's files were characterized: a stage read and a role read, each over
   * the experiment's files, with the readings in conflict said apart.
   * @param {ExperimentRow} row
   * @returns {string[]}
   */
  function characterizedLines(row) {
    const held = row.characterized;
    /** @param {string} what @param {number} read @param {number} conflicting */
    const line = (what, read, conflicting) => `${what} read in ${count(read)} of ${plural(held.files, 'file')}`
      + (conflicting ? `; results in conflict for ${count(conflicting)}` : '');
    return [line('Stage', held.stage_read, held.stage_conflicting), line('Role', held.role_read, held.role_conflicting)];
  }

  /**
   * The classes of a breakdown that hold files, each as "label: n", in the engine's order.
   * @param {ClassCount[]} items
   * @returns {Array<[string, string]>}
   */
  function classLines(items) {
    return items.filter((item) => item.files > 0).map((item) => [item.label, count(item.files)]);
  }

  /**
   * An experiment's raw files, analysis files and results tables, in one line.
   * @param {ExperimentRow} row
   */
  function countsWords(row) {
    return `${count(row.raw_acquisitions)} raw · ${count(row.analysis_files)} analysis · `
      + `${plural(row.result_tables, 'results table')}`;
  }

  /**
   * The flags a row carries about the No analysis found list. A person's word is the person's color.
   * @param {ExperimentRow} row
   * @returns {Array<{key: string, text: string, person: boolean}>}
   */
  function flagsOf(row) {
    if (row.no_analysis_found === 'listed') {
      return [{key: 'listed', text: 'On the No analysis found list', person: false}];
    }
    if (row.no_analysis_found === 'answered') {
      return [{key: 'answered', text: 'Analysis said to be elsewhere', person: true}];
    }
    return [];
  }

  /**
   * The request's query string: only what differs from the page's starting point.
   * @param {Query} query
   */
  function queryString(query) {
    /** @type {Array<[string, string]>} */
    const asked = [['sort', query.sort], ['order', query.order]];
    for (const name of /** @type {const} */ (['person', 'line', 'topic', 'state', 'q'])) {
      if (query[name]) asked.push([name, query[name]]);
    }
    asked.push(['offset', String(query.offset)], ['limit', String(query.limit)]);
    return asked.map(([name, value]) => `${name}=${encodeURIComponent(value)}`).join('&');
  }

  /**
   * Choosing a sort again turns it round; choosing another starts it in its first order.
   * @param {Query} query
   * @param {string} sort
   */
  function nextSort(query, sort) {
    if (query.sort === sort) return {sort, order: query.order === 'asc' ? 'desc' : 'asc'};
    return {sort, order: /** @type {Record<string, string>} */ (FIRST_ORDER)[sort] || 'asc'};
  }

  /**
   * Which rows are listed, of how many match, of how many experiments there are in all.
   * @param {{offset: number, shown: number, matching: number, total: number}} page
   */
  function rangeWords(page) {
    if (!page.matching) return `No experiment matches, of ${plural(page.total, 'experiment')} in all.`;
    const first = page.offset + 1;
    const last = page.offset + page.shown;
    const span = first === last ? `${count(first)}` : `${count(first)} to ${count(last)}`;
    return page.matching === page.total
      ? `Showing ${span} of ${plural(page.total, 'experiment')}.`
      : `Showing ${span} of ${count(page.matching)} matching, of ${plural(page.total, 'experiment')} in all.`;
  }

  /**
   * The line under the title: how many experiments, how many files, how many files outside them.
   * @param {ExperimentsResponse} found
   */
  function headline(found) {
    const held = found.summary;
    return `${plural(held.experiments, 'experiment')} found, holding ${plural(held.files, 'file')}. `
      + `${plural(held.files_outside, 'file')} found ${held.files_outside === 1 ? 'lies' : 'lie'} in no experiment.`;
  }

  /**
   * How many of the experiments judged show no analysis found, with the denominator.
   * @param {ExperimentsResponse} found
   */
  function standingWords(found) {
    const held = found.summary;
    if (!held.judged) return '';
    return `${count(held.no_analysis_found)} of ${plural(held.judged, 'experiment')} with enough raw files `
      + 'show no analysis found.';
  }

  /**
   * A topic in the Topic filter's options: its count, "at least" where a member could not be read
   * (it may hold more), and "not read" where none was (unknown, never 0).
   * @param {import('./types/api').ExperimentsTopic} topic
   */
  function topicOptionLabel(topic) {
    if (topic.experiments === null) return `${topic.name} (not read)`;
    return `${topic.name} (${topic.complete ? '' : 'at least '}${count(topic.experiments)})`;
  }

  /**
   * What an experiment with no topic says in its Topics cell: "none" only where every topic was
   * read; where one could not be, it may hold the experiment, so the cell says so.
   * @param {boolean} topicsRead whether the topics could be read at all
   * @param {boolean} anyUnread whether some topic was not read
   */
  function noTopicWords(topicsRead, anyUnread) {
    if (!topicsRead) return 'not read';
    return anyUnread ? 'none of the collections read' : 'none';
  }

  /**
   * One line for each topic that was not read, with the sentence the engine served for it.
   * @param {ExperimentsResponse} found
   * @returns {string[]}
   */
  function unreadTopicLines(found) {
    return found.topics.filter((topic) => !topic.read).map((topic) => `\u201c${topic.name}\u201d: ${topic.said}`);
  }

  /**
   * The card's lead sentence where nothing is listed. Where the topic asked for cannot be read, or
   * no longer stands (a person deleted or merged it while the filter was kept), it says that
   * instead of "no match": the topic holds no experiment here only because it was not read.
   * @param {ExperimentsResponse} found
   * @param {Query} query
   */
  function noMatchSentence(found, query) {
    if (query.topic) {
      const topic = found.topics.find((item) => item.id === query.topic);
      if (!found.topics_read || (topic && !topic.read)) return 'Ingence could not read this collection.';
      if (!topic) return 'This collection no longer exists.';
    }
    return 'No experiment matches these filters.';
  }

  /** @param {Query} query */
  function isFiltered(query) {
    return Boolean(query.person || query.line || query.topic || query.state || query.q);
  }

  /* ------------------------------------------------------------------ the page */

  const kept = {query: defaults()};
  /** What the results region redraws with, kept so a re-query keeps the same actions. */
  const holder = {actions: /** @type {any} */ (null)};

  /**
   * @param {Query} query
   * @returns {Promise<ExperimentsResponse>}
   */
  function read(query) {
    if (goal.at) return readForGoal(query, goal.at);
    return api(`/api/v1/experiments?${queryString(query)}`);
  }

  /* A goal's experiments: the Overview's "Show these" opens ``#experiments?goal=<id>``, and the
   * table lists the goal's experiments only, with a line that says so and "Show all". The page does
   * not send the route's ``goal=`` filter. It reads every row the other filters match (500 a
   * request, 20 requests at most) and keeps the goal's, by the identifiers the goal's matches name.
   * Every count shown is still one the engine served. */
  /** @type {{at: {id: string, text: string, ids: Set<string>} | null}} */
  const goal = {at: null};
  /** The most rows read for a goal's filter: 20 requests of 500. */
  const GOAL_PAGE = 500;
  const GOAL_PAGES = 20;

  /** The goal the address names, read through the Overview's script. */
  async function goalAsked() {
    const asked = typeof global.viewQuery === 'function' ? global.viewQuery().get('goal') : null;
    if (!asked || !global.ingenceGoals) return null;
    const found = await global.ingenceGoals.read(asked).catch(() => null);
    if (!found) return null;
    return {id: String(found.id), text: String(found.text || ''),
      ids: new Set((found.matches || []).map((/** @type {any} */ match) => String(match.experiment_id)))};
  }

  /**
   * The rows of the goal's experiments, paged on the page.
   * @param {Query} query
   * @param {{ids: Set<string>}} wanted
   * @returns {Promise<ExperimentsResponse>}
   */
  async function readForGoal(query, wanted) {
    const all = {...query, offset: 0, limit: GOAL_PAGE};
    const first = await api(`/api/v1/experiments?${queryString(all)}`);
    const rows = [...first.rows];
    for (let page = 1; page < GOAL_PAGES && page * GOAL_PAGE < first.matching; page += 1) {
      const more = await api(`/api/v1/experiments?${queryString({...all, offset: page * GOAL_PAGE})}`);
      rows.push(...more.rows);
    }
    const mine = rows.filter((row) => wanted.ids.has(row.id));
    return {...first, rows: mine.slice(query.offset, query.offset + query.limit), offset: query.offset,
      matching: mine.length};
  }

  /**
   * "Showing the 7 experiments of this goal." and Show all, above the table.
   * @param {number} matching
   */
  function goalLineNode(matching) {
    const wanted = goal.at;
    if (!wanted) return null;
    const words = matching === 1 ? 'Showing the 1 experiment of this goal.'
      : `Showing the ${count(matching)} experiments of this goal.`;
    return el('p', {class: 'exp-goal-line', role: 'status', 'data-goal': wanted.id}, [words, ' ',
      el('button', {type: 'button', class: 'link', 'data-help': 'experiments.goal_all',
        onclick: () => { navigate('experiments'); }}, ['Show all'])]);
  }

  /** The names of the rows drawn, for the story's links to other experiments. */
  const rowNames = new Map();

  /**
   * Open Overlap's By year mode at an experiment: its story there, or with ``files`` its files.
   * @param {string} id
   * @param {HTMLElement} status
   * @param {{files?: boolean}} [options]
   */
  async function openInByYear(id, status, options = {}) {
    const years = global.ingenceYears;
    if (!years) {
      status.textContent = 'By year did not load, so this experiment cannot open there. Reload the page.';
      return;
    }
    status.textContent = 'Opening...';
    try {
      const model = await years.load();
      if (!model.byId.has(id)) {
        status.textContent = 'By year does not list this experiment.';
        return;
      }
      await navigate('years');
      if (options.files && years.files) years.files(id);
      else {
        years.open({kind: 'exp', id});
        years.select({kind: 'exp', id});
      }
    } catch (error) {
      status.textContent = `Ingence could not open it: ${String((error && /** @type {Error} */ (error).message) || error)}`;
    }
  }

  /**
   * Open a row: the experiment's story in a row of its own under it (story.js), with the way to the
   * same experiment in Overlap's By year mode and to its files. A second click on the name closes
   * it.
   * @param {ExperimentRow} row
   * @param {HTMLElement} status
   * @param {HTMLElement} button
   */
  async function openExperiment(row, status, button) {
    const line = button.closest('tr');
    if (!line) return;
    const next = line.nextElementSibling;
    if (next && next.classList.contains('exp-story-row')) {
      next.remove();
      button.setAttribute('aria-expanded', 'false');
      return;
    }
    const story = global.ingenceStory;
    if (!story) {
      status.textContent = 'The story did not load. Reload the page.';
      return;
    }
    const box = el('div', {class: 'exp-story'});
    line.after(el('tr', {class: 'exp-story-row', 'data-story-of': row.id}, [el('td', {colspan: '8'}, [
      el('p', {class: 'exp-story-head'}, [el('button', {type: 'button', class: 'link', 'data-help': 'experiments.by_year',
        onclick: () => { openInByYear(row.id, status).catch(() => {}); }}, ['Open in By year'])]),
      box])]));
    button.setAttribute('aria-expanded', 'true');
    await story.render(box, row.id, {
      name: (/** @type {string} */ id) => rowNames.get(id) || '',
      onOpen: (/** @type {string} */ id) => { openInByYear(id, status).catch(() => {}); },
      onFiles: () => { openInByYear(row.id, status, {files: true}).catch(() => {}); },
    });
  }

  /**
   * @param {ClassCount[]} items
   * @param {string} heading
   * @param {number} files
   */
  function breakdown(items, heading, files) {
    const lines = classLines(items);
    return el('div', {class: 'exp-breakdown'}, [
      el('p', {class: 'exp-breakdown-head'}, [heading, el('span', {class: 'faint', text: ` of ${plural(files, 'file')}`})]),
      el('ul', {}, lines.map(([label, shown]) => el('li', {}, [
        el('span', {text: label}), el('span', {class: 'exp-n', text: shown})]))),
    ]);
  }

  /**
   * The topics column: a chip for each topic that holds the experiment, in the person's color
   * (a topic is a person's own name), and where the topics could not be read, that, never "none".
   * @param {ExperimentRow} row
   * @param {{read: boolean, unread: boolean}} topics whether they were read at all, and whether
   *   some topic was not
   * @param {(topic: string) => void} onTopic
   */
  function topicsCell(row, topics, onTopic) {
    const cell = el('td', {class: 'exp-topics', 'data-help': 'experiments.topics'});
    if (!row.topics.length) {
      const said = noTopicWords(topics.read, topics.unread);
      cell.append(el('span', {class: said === 'none' ? 'faint' : 'faint exp-unknown', text: said}));
    }
    for (const topic of row.topics) {
      cell.append(el('button', {type: 'button', class: 'exp-chip', 'data-topic': topic.id,
        'data-help': 'experiments.topic_chip', title: `Show only the experiments in ${topic.name}`,
        onclick: () => onTopic(topic.id), text: topic.name}));
    }
    return cell;
  }

  /**
   * The raw files analyzed: a bar the full width of the cell with the counts in bold beside it,
   * and the engine's sentence behind Why, closed. No raw file found is unknown, never 0: no bar,
   * the hatched swatch and the words.
   * @param {ExperimentRow} row
   */
  function followedCell(row) {
    const view = followedView(row);
    const cell = el('td', {class: 'exp-followed', 'data-help': 'experiments.followed'});
    const why = el('details', {class: 'exp-why', 'data-help': 'experiments.followed_why'}, [
      el('summary', {text: 'Why'}),
      el('p', {class: 'exp-said', text: view.said}),
      view.assumed > 0 ? el('p', {class: 'exp-pairs'}, [
        el('button', {type: 'button', class: 'link', 'data-help': 'experiments.pairs',
          onclick: () => navigate('candidates'), text: 'See the suggested pairs'}),
        el('span', {class: 'faint', text: ' (every pair in the scan, for all experiments)'})]) : null,
    ]);
    if (!view.known) {
      cell.append(el('p', {class: 'exp-unknown exp-none', text: 'No raw files found'}), why);
      return cell;
    }
    cell.append(
      el('div', {class: 'exp-meter'}, [
        el('span', {class: 'exp-bar', role: 'img',
          'aria-label': `${count(view.followed)} of ${plural(view.raw, 'raw file')} analyzed: `
            + `${count(view.confirmed)} confirmed by you, ${count(view.assumed)} assumed`}, [
          el('span', {class: 'exp-bar-confirmed', style: `width:${view.confirmedPercent.toFixed(1)}%`}),
          el('span', {class: 'exp-bar-assumed', style: `width:${view.assumedPercent.toFixed(1)}%`}),
        ]),
        // The bar's label says the same counts in words, so a screen reader hears them once.
        el('strong', {class: 'exp-ratio', 'aria-hidden': 'true', text: `${count(view.followed)} / ${count(view.raw)}`}),
      ]),
      why);
    return cell;
  }

  /** The one key to the bars' two shades, above the table. */
  function legendNode() {
    /** @param {string} shade @param {string} words */
    const key = (shade, words) => el('span', {class: 'exp-key'}, [
      el('span', {class: `exp-swatch ${shade}`, 'aria-hidden': 'true'}), words]);
    return el('p', {class: 'exp-legend', 'data-help': 'experiments.legend'}, [
      el('strong', {text: 'Raw files analyzed'}),
      key('confirmed', 'Confirmed: a pair you confirmed'),
      key('assumed', 'Assumed: a pair or link Ingence found'),
    ]);
  }

  /**
   * @param {ExperimentRow} row
   * @param {{read: boolean, unread: boolean}} topics
   * @param {(topic: string) => void} onTopic
   */
  function rowNode(row, topics, onTopic) {
    const status = el('span', {class: 'exp-status faint', role: 'status', 'aria-live': 'polite'});
    rowNames.set(row.id, row.shown_name || row.name);
    const open = el('button', {type: 'button', class: 'link exp-open', 'data-help': 'experiments.story',
      title: `${row.name}: open its story`, 'aria-expanded': 'false',
      onclick: () => { openExperiment(row, status, open).catch(() => {}); }}, [row.shown_name || row.name]);
    const who = [row.person, row.theme_label ? `theme ${row.theme_label}` : null].filter(Boolean).join(' · ');
    const flags = flagsOf(row);
    return el('tr', {class: 'exp-row', 'data-id': row.id, 'data-state': row.state, 'data-help': 'experiments.row'}, [
      el('td', {class: 'exp-name'}, [
        open,
        el('p', {class: 'exp-folder', title: row.folder, text: row.folder}),
        who ? el('p', {class: 'faint', text: who}) : null,
        status,
      ]),
      followedCell(row),
      topicsCell(row, topics, onTopic),
      el('td', {class: 'exp-found', 'data-help': 'experiments.found_by', text: row.found_by_said}),
      el('td', {class: row.date ? 'exp-dates' : 'exp-dates exp-unknown', text: datesWords(row)}),
      el('td', {class: 'exp-files'}, [
        el('details', {'data-help': 'experiments.files'}, [
          el('summary', {text: plural(row.files, 'file')}),
          breakdown(row.by_stage, 'By stage', row.files),
          breakdown(row.by_kind, 'By kind', row.files),
        ]),
        el('p', {class: 'exp-counts', text: countsWords(row)}),
      ]),
      el('td', {class: 'exp-char', 'data-help': 'experiments.characterized'},
        characterizedLines(row).map((line) => el('p', {text: line}))),
      el('td', {class: 'exp-state'}, [
        el('span', {class: 'tag plain', 'data-help': 'experiments.state', text: row.state_said}),
        // How the state was judged, behind a small disclosure so the column stays narrow.
        el('details', {class: 'exp-why', 'data-help': 'experiments.why'}, [
          el('summary', {text: 'Why'}), el('p', {class: 'faint', text: row.state_why})]),
        ...flags.map((flag) => el('p', {class: flag.person ? 'exp-flag exp-person' : 'exp-flag', 'data-flag': flag.key}, [
          el('button', {type: 'button', class: 'link', onclick: () => navigate('found'), text: flag.text})])),
      ]),
    ]);
  }

  /**
   * @param {Query} query
   * @param {(sort: string) => void} onSort
   */
  function headRow(query, onSort) {
    /** @param {string} label @param {string | null} sort @param {string} [cls] */
    const head = (label, sort, cls) => {
      const sorted = sort !== null && query.sort === sort;
      return el('th', {scope: 'col', class: cls || '',
        'aria-sort': sorted ? (query.order === 'asc' ? 'ascending' : 'descending') : 'none'},
      sort === null ? [label] : [el('button', {type: 'button', class: 'link exp-sort', 'data-help': 'experiments.sort_head',
        onclick: () => onSort(sort)}, [label, sorted ? (query.order === 'asc' ? ' ↑' : ' ↓') : ''])]);
    };
    return el('tr', {}, [
      head('Experiment', 'name'), head('Raw files analyzed', 'completeness'), head('Collections', null),
      head('How it was found', null), head('Dates', 'date'), head('Files', 'files'), head('Characterized', null), head('Found', null),
    ]);
  }

  /* The pager: Previous and Next, and the range in words. */
  /**
   * @param {ExperimentsResponse} found
   * @param {Query} query
   * @param {(offset: number) => void} go
   */
  function pagerNode(found, query, go) {
    const last = found.offset + found.rows.length;
    return el('div', {class: 'pager exp-pager', 'data-help': 'experiments.pager'}, [
      el('button', {type: 'button', class: 'answer quiet', ...(found.offset <= 0 ? {disabled: 'disabled'} : {}),
        onclick: () => go(Math.max(0, found.offset - query.limit))}, [el('strong', {text: 'Previous'})]),
      el('span', {text: rangeWords({offset: found.offset, shown: found.rows.length, matching: found.matching,
        total: found.summary.experiments})}),
      el('button', {type: 'button', class: 'answer quiet', ...(last >= found.matching ? {disabled: 'disabled'} : {}),
        onclick: () => go(last)}, [el('strong', {text: 'Next'})]),
    ]);
  }

  /**
   * The results region: a table, or the drawn state when no experiment matches.
   * @param {HTMLElement} results
   * @param {ExperimentsResponse} found
   * @param {{onSort: (sort: string) => void, onPage: (offset: number) => void, onClear: () => void,
   *   onTopic: (topic: string) => void}} actions
   */
  function drawResults(results, found, actions) {
    if (!found.rows.length && goal.at) {
      fill(results, states.say(null, {eyebrow: 'Experiments · goal', dot: 'empty', into: true,
        sentence: 'None of this goal\u2019s experiments is in this list.',
        body: [`${plural(found.summary.experiments, 'experiment')} were found in all.`],
        actions: [{label: 'Show all', onclick: () => { navigate('experiments'); }}]}));
      return;
    }
    if (!found.rows.length) {
      fill(results, states.say(null, {eyebrow: 'Experiments · no match', dot: 'empty', into: true,
        sentence: noMatchSentence(found, kept.query),
        body: [`${plural(found.summary.experiments, 'experiment')} were found in all.`,
          ...(found.topic_filter_said ? [found.topic_filter_said] : [])],
        actions: [{label: 'Clear the filters', onclick: actions.onClear}]}));
      return;
    }
    const topicsState = {read: found.topics_read, unread: found.topics.some((topic) => !topic.read)};
    fill(results,
      goalLineNode(found.matching),
      legendNode(),
      el('div', {class: 'exp-tablewrap', role: 'region', tabindex: '0', 'aria-label': 'Experiments, scrolls sideways when narrow'}, [
        el('table', {class: 'exp-table', 'data-help': 'experiments.table'}, [
          el('thead', {}, [headRow(kept.query, actions.onSort)]),
          el('tbody', {}, found.rows.map((row) => rowNode(row, topicsState, actions.onTopic))),
        ])]),
      pagerNode(found, kept.query, actions.onPage));
  }

  /**
   * @param {string} name
   * @param {string} label
   * @param {string} help
   * @param {Array<[string, string]>} options
   * @param {string} value
   * @param {(value: string, select: HTMLSelectElement) => void} onChange
   */
  function selectNode(name, label, help, options, value, onChange) {
    const select = /** @type {HTMLSelectElement} */ (el('select', {'aria-label': label, 'data-help': help, 'data-filter': name},
      options.map(([key, words]) => el('option', {value: key, text: words}))));
    select.value = value;
    select.addEventListener('change', () => onChange(select.value, select));
    return el('label', {class: 'exp-field'}, [`${label} `, select]);
  }

  /**
   * The filters, built once from the first answer's facets (which count the whole run).
   * @param {ExperimentsResponse} found
   * @param {() => void} requery
   * @param {() => void} clear
   */
  function filterBar(found, requery, clear) {
    const query = kept.query;
    /** @type {number | undefined} */
    let typing;
    const text = /** @type {HTMLInputElement} */ (el('input', {type: 'search', autocomplete: 'off', class: 'exp-search',
      'aria-label': 'Filter by name', placeholder: 'a word in the name or the folder', 'data-help': 'experiments.filter_name'}));
    text.value = query.q;
    text.addEventListener('input', () => {
      window.clearTimeout(typing);
      typing = window.setTimeout(() => { query.q = text.value.trim(); query.offset = 0; requery(); }, 250);
    });
    const people = found.people.length > 1 || (found.people.length === 1 && Boolean(query.person));
    /** @type {Array<HTMLElement | null>} */
    const fields = [
      el('label', {class: 'exp-field'}, ['Name ', text]),
      people ? selectNode('person', 'Area', 'experiments.filter_person',
        [['', 'every area'], ...found.people.map((item) => /** @type {[string, string]} */ ([item.value, `${item.label} (${item.experiments})`]))],
        query.person, (value) => { query.person = value; query.offset = 0; requery(); }) : null,
      found.themes_computed && found.lines.length ? selectNode('line', 'Theme', 'experiments.filter_line',
        [['', 'every theme'], ...found.lines.map((item) => /** @type {[string, string]} */ ([item.value, `${item.label} (${item.experiments})`]))],
        query.line, (value) => { query.line = value; query.offset = 0; requery(); }) : null,
      found.topics.length || query.topic ? selectNode('topic', 'Collection', 'experiments.filter_topic',
        [['', 'every collection'], ...found.topics.map((item) => /** @type {[string, string]} */ ([item.id,
          topicOptionLabel(item)])),
        // A topic kept in the filter that no longer stands is shown, so the filter can be cleared.
        ...(query.topic && !found.topics.some((item) => item.id === query.topic)
          ? [/** @type {[string, string]} */ ([query.topic, 'a collection that no longer exists'])] : [])],
        query.topic, (value, select) => {
          query.topic = value;
          query.offset = 0;
          // The placeholder for a topic that no longer stands goes once the filter moves off it.
          for (const option of [...select.options]) {
            const stands = found.topics.some((item) => item.id === option.value);
            if (option.value && option.value !== value && !stands) option.remove();
          }
          requery();
        }) : null,
      selectNode('state', 'Completeness', 'experiments.filter_state',
        [['', 'every experiment'], ...found.states.map((item) => /** @type {[string, string]} */ ([item.value, `${item.label} (${item.experiments})`]))],
        query.state, (value) => { query.state = value; query.offset = 0; requery(); }),
      selectNode('sort', 'Sort by', 'experiments.sort', SORTS.map(([key, words]) => /** @type {[string, string]} */ ([key, words])),
        query.sort, (value, select) => {
          Object.assign(query, nextSort({...query, sort: ''}, value), {offset: 0});
          const bar = select.closest('.exp-filters');
          if (bar instanceof HTMLElement) syncSort(bar);
          requery();
        }),
      selectNode('order', 'Order', 'experiments.sort', [['asc', 'ascending'], ['desc', 'descending']],
        query.order, (value) => { query.order = value; query.offset = 0; requery(); }),
      el('button', {type: 'button', class: 'link exp-clear', 'data-help': 'experiments.clear', onclick: clear,
        text: 'Clear the filters'}),
    ];
    return el('div', {class: 'filters exp-filters', role: 'search', 'aria-label': 'Filter and sort the experiments'}, fields);
  }

  /** The words above the table: what an experiment is, what the engine assumes about pairs. */
  /** @param {ExperimentsResponse} found */
  function headNodes(found) {
    const standing = standingWords(found);
    return [
      el('p', {class: 'ui-lead', text: headline(found)}),
      standing ? el('p', {}, [standing, ' ',
        el('button', {type: 'button', class: 'link', onclick: () => navigate('found'), text: 'See that list'})]) : null,
      el('details', {class: 'what'}, [el('summary', {text: 'What counts as an experiment'}),
        el('p', {text: found.counted_as})]),
      el('div', {class: 'notice-note exp-assumption', 'data-help': 'experiments.assumption'}, [
        el('p', {text: found.pairing_assumption}),
        el('p', {}, [el('button', {type: 'button', class: 'link', onclick: () => navigate('candidates'),
          text: 'Open Suggested pairs'}),
        el('span', {class: 'faint', text: ' to confirm or refuse a pair. The list holds every pair in the scan.'})]),
      ]),
      found.themes_computed ? null : el('p', {class: 'faint', text: found.themes_said}),
      found.topics_said ? el('p', {class: 'faint', 'data-topics': 'said', text: found.topics_said}) : null,
      ...unreadTopicLines(found).map((line) => el('p', {class: 'faint', 'data-topics': 'unread', text: line})),
    ];
  }

  async function showExperiments() {
    const turn = turns.claim(listPane);
    states.loading(listPane, 'Reading the experiments...');
    /** @type {ExperimentsResponse} */
    let first;
    try {
      goal.at = await goalAsked();
      first = await read(kept.query);
    } catch (error) {
      if (turns.owns(listPane, turn) && state.view === 'experiments') {
        states.failed(listPane, error, () => { navigate('experiments', {push: false}); });
      }
      return;
    }
    if (!turns.owns(listPane, turn) || state.view !== 'experiments') return;
    if (!first.summary.experiments) {
      states.say(listPane, {eyebrow: 'Experiments · empty', dot: 'empty',
        sentence: 'No experiments were found in what was read.',
        body: [first.counted_as, 'The rules that read a folder as an experiment found none. The Inventory lists what is here.'],
        actions: [{label: 'Open the Inventory', onclick: () => { navigate('inventory'); }}]});
      return;
    }
    const results = el('div', {class: 'exp-results', 'aria-live': 'polite'});
    const bar = filterBar(first, () => { requery(results).catch(() => {}); }, () => {
      kept.query = defaults();
      showExperiments().catch(() => {});
    });
    const actions = {
      onSort: (/** @type {string} */ sort) => {
        Object.assign(kept.query, nextSort(kept.query, sort), {offset: 0});
        syncSort(bar);
        requery(results, {keepFocus: true}).catch(() => {});
      },
      onPage: (/** @type {number} */ offset) => {
        kept.query.offset = offset;
        requery(results, {keepFocus: true}).catch(() => {});
      },
      onClear: () => { kept.query = defaults(); showExperiments().catch(() => {}); },
      onTopic: (/** @type {string} */ topic) => {
        kept.query.topic = topic;
        kept.query.offset = 0;
        const select = /** @type {HTMLSelectElement | null} */ (bar.querySelector('select[data-filter="topic"]'));
        if (select) select.value = topic;
        requery(results, {keepFocus: true}).catch(() => {});
      },
    };
    holder.actions = actions;
    // The workflows above the list, each with Change and Turn off (story.js).
    const workflows = el('div', {class: 'exp-workflows'});
    fill(listPane, el('div', {class: 'exp-page', 'data-view': 'experiments'}, [
      el('h2', {text: 'Experiments', 'data-help': 'section.experiments'}),
      // A goal's experiments (#experiments?goal=<id>), from the Overview's Show these.
      global.ingenceGoalPanel ? global.ingenceGoalPanel() : null,
      ...headNodes(first), workflows, bar, results]));
    if (global.ingenceWorkflows) global.ingenceWorkflows.card(workflows).catch(() => {});
    drawResults(results, first, actions);
  }

  /** Show the sort a header click chose in the two selects. @param {HTMLElement} bar */
  function syncSort(bar) {
    for (const name of ['sort', 'order']) {
      const select = /** @type {HTMLSelectElement | null} */ (bar.querySelector(`select[data-filter="${name}"]`));
      if (select) select.value = name === 'sort' ? kept.query.sort : kept.query.order;
    }
  }

  /**
   * Ask again with the query as it stands and redraw the results. A page past the end (rows
   * changed since) goes back to the last page that has rows. With ``keepFocus`` the focus moves to
   * the results, since the button that was pressed is gone with the old rows.
   * @param {HTMLElement} results
   * @param {{keepFocus?: boolean}} [options]
   */
  async function requery(results, options = {}) {
    const turn = turns.claim(results);
    results.setAttribute('aria-busy', 'true');
    try {
      let found = await read(kept.query);
      if (!found.rows.length && found.matching > 0 && kept.query.offset > 0) {
        kept.query.offset = Math.floor((found.matching - 1) / kept.query.limit) * kept.query.limit;
        found = await read(kept.query);
      }
      if (!turns.owns(results, turn) || !results.isConnected) return;
      drawResults(results, found, holder.actions);
      if (options.keepFocus) {
        results.tabIndex = -1;
        results.focus({preventScroll: true});
      }
    } catch (error) {
      if (!turns.owns(results, turn) || !results.isConnected) return;
      fill(results, states.failed(null, error, () => { requery(results).catch(() => {}); }, {into: true}));
    } finally {
      results.removeAttribute('aria-busy');
    }
  }

  global.experimentsView = {
    PAGE_SIZE, SORTS, defaults, count, plural, dayWords, datesWords, followedView, characterizedLines,
    classLines, countsWords, flagsOf, queryString, nextSort, rangeWords, headline, standingWords, isFiltered,
    topicOptionLabel, noTopicWords, unreadTopicLines, noMatchSentence,
  };
  if (typeof VIEWS !== 'undefined') {
    VIEWS.experiments = showExperiments;
    SINGLE_VIEWS.add('experiments');
  }
})(typeof window !== 'undefined' ? window : globalThis);
