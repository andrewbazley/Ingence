/* The Years view: the Dashboard's leading surface, in the engine's own shell.
 *
 * Rows by time at every scale: the project, a person, a line of work, an experiment, its files, one
 * file. A laboratory and an archive are two configurations of one ladder, so one component draws
 * every scale. Each row is one thing at the current scale, with its marks across the time axis and,
 * where it applies, how far its raw data reached an analysis. The inspector beside it shows the
 * selected thing with its evidence, the rule and version behind every conclusion, and the actions
 * allowed on it. The Wheel (W) and Overlap (O) are views behind keys.
 *
 * Everything drawn is the engine's own output: themes (a projection, never a claim), completeness
 * ("no analysis found", never "never analyzed"), one experiment's files, and the entry record.
 * Names, the busiest days of an experiment's files and the words two lines share are the engine's
 * too: the page draws them and computes none. Where a measure needs data the engine does not
 * produce (a file's stage shares per experiment, soft membership by word resampling), the view
 * says so in words or leaves the measure out.
 *
 * Loaded after app.js (el, fill, api, write, states, navigate, VIEWS, PAGES, holdFocus, turns),
 * review.js (askActor, me, dialogs) and inspector.js (window.inspector). No framework, no build
 * step, nothing fetched from anywhere but this server. */

(function yearsModule() {
  'use strict';

  /* ------------------------------------------------------------------ words and numbers */

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const fmt = (n) => Number(n || 0).toLocaleString('en-US');
  const plural = (n, one, many) => `${fmt(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;
  const DAY = 864e5;
  /* The room on each side of the large Wheel for the labels of arcs near its left and right edges,
   * which read outward from the rim: the drawing is that much wider than the circle. */
  const LABEL_ROOM = 72;

  /* ------------------------------------------------------------------ the ladder's words */

  /* Every sentence that names a level reads its word from the ladder. A laboratory's rows say
   * "line of work" and "experiment", and an archive's say "theme" and "study", from one component.
   * The engine publishes the words with the project (``/api/v1/ladder``). The table here is only
   * the fallback for a project whose engine serves none, and it holds the same two kinds of root.
   * A level is named by its id: root, person, theme, experiment, file. */
  const FALLBACK_WORDS = {
    lab: {root: ['project', 'projects'], person: ['area', 'areas'], theme: ['theme', 'themes'],
      experiment: ['experiment', 'experiments'], file: ['file', 'files']},
    archive: {root: ['archive', 'archives'], theme: ['theme', 'themes'], experiment: ['study', 'studies'], file: ['file', 'files']},
  };
  /* The scales' own kinds (project, exp) and the ladder's level ids. */
  const LEVEL_OF = {project: 'root', person: 'person', theme: 'theme', exp: 'experiment', file: 'file'};
  function ladderOf(served, listing) {
    const kind = served ? served.kind : (listing ? 'archive' : 'lab');
    const words = {};
    for (const [id, pair] of Object.entries(FALLBACK_WORDS[kind])) words[id] = pair;
    if (served) for (const l of served.levels) words[l.kind] = [l.name, l.plural];
    // The first folder below a data folder is an area.
    if (words.person) words.person = ['area', 'areas'];
    return {kind, words, levels: served ? served.levels : null};
  }
  let ladderHeld = ladderOf(null, false);   // the words in force: the model's, once one is read
  /* The word for a level: ``word('theme')`` is "line of work", ``word('theme', true)`` "lines of work". */
  function word(level, many = false) {
    const id = LEVEL_OF[level] || level;
    const pair = ladderHeld.words[id] || FALLBACK_WORDS.lab[id] || [id, id];
    return many ? pair[1] : pair[0];
  }
  const cap = (text) => text.charAt(0).toUpperCase() + text.slice(1);
  /* The word for the coarser groups of the lines (``broad lines``, ``broad themes``), from the
   * ladder. */
  function broadWord() {
    const level = ladderHeld.levels ? ladderHeld.levels.find((l) => l.kind === 'theme') : null;
    return (level && level.broad) || `broad ${word('theme', true)}`;
  }
  /* A count with the level's word: "12 studies", "1 line of work". */
  const countOf = (n, level) => `${fmt(n)} ${Number(n) === 1 ? word(level) : word(level, true)}`;
  const article = (text) => (/^[aeiou]/i.test(text) ? 'an' : 'a');
  /* The sentence under a scale's title: what a row is, what the bar's height is, and how
   * many of what there are in how many. */
  function rowSentence(level, rows, n, parents) {
    return `A row per ${word(level)}; bar height is ${word(rows, true)} that year. ${countOf(n, rows)} in ${countOf(parents, level)}.`;
  }
  /* The keys of the Years rows, in one line under the legend. */
  const KEYS_ROWS = '↑ ↓ walk rows · → open · ← or Backspace up · Enter the inspector · W Wheel · O Stability · N relations · Home End first or last · + − zoom · 0 reset';
  const KEYS_STORY = '← or Backspace up · Enter the inspector';
  const KEYS_FILES = '↑ ↓ walk rows · → or Enter opens a file\'s record · ← or Backspace up · + − zoom · 0 reset';

  /* A moment from what the API sends: an ISO instant with its zone, or a day. */
  function ms(value) {
    if (value == null || value === '') return NaN;
    if (typeof value === 'number') return value;
    const text = String(value);
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return Date.parse(`${text}T00:00:00Z`);
    if (/^\d{4}$/.test(text)) return Date.UTC(Number(text), 0, 1);
    const parsed = Date.parse(text);
    return Number.isNaN(parsed) ? Date.parse(text.replace(' ', 'T')) : parsed;
  }
  function dayWords(at) {
    const t = ms(at);
    if (Number.isNaN(t)) return String(at || '');
    const d = new Date(t);
    return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
  function momentWords(at) {
    const t = ms(at);
    if (Number.isNaN(t)) return String(at || '');
    const d = new Date(t);
    const pad = (n) => String(n).padStart(2, '0');
    return `${dayWords(at)} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
  }

  /* A name is the engine's. The census read every folder's name into parts with the tokenizer, and
   * the themes and completeness routes send ``name`` (as written), ``shown_name`` (without the dates
   * it read) and ``name_parts``. The page shows ``shown_name`` and reads no name itself. The
   * inspector shows the full name. */

  /* The completeness ramp: how far raw data reached an analysis, in five words (never a number
   * in the main reading). The counts are beside it. */
  function level(e) {
    if (!e || !e.raw_acquisitions || e.share_of_raw_followed_by_analysis == null) return 'na';
    const s = e.share_of_raw_followed_by_analysis;
    if (s >= 0.95) return 'all';
    if (s >= 0.5) return 'most';
    if (s > 0.02) return 'some';
    return 'none';
  }
  const LEVEL_WORDS = {all: 'all raw data analyzed', most: 'most raw data analyzed', some: 'some raw data analyzed',
    none: 'no raw data analyzed', na: 'no raw data found'};
  /* has / gap / none. Analysis without its raw data is a gap, never "nothing to analyze". */
  function rawState(e) {
    if (!e) return 'na';
    if (e.raw_acquisitions) return 'has';
    return (e.analysis_files || e.derived_files) ? 'gap' : 'none';
  }
  function agg(exps) {
    let raw = 0; let followed = 0;
    for (const e of exps) { raw += e.raw_acquisitions || 0; followed += e.raw_followed || 0; }
    return {raw, followed, share: raw ? followed / raw : null};
  }
  /* A group's sums as the engine served them, in the shape ``agg`` gives. */
  function sumShape(row) {
    return {raw: row.raw_acquisitions, followed: row.raw_followed, share: row.share_of_raw_followed_by_analysis};
  }
  function aggLevel(a) {
    if (!a.raw) return 'na';
    const s = a.share;
    return s >= 0.95 ? 'all' : s >= 0.5 ? 'most' : s > 0.02 ? 'some' : 'none';
  }

  /* ------------------------------------------------------------------ the state */

  const Y = {
    path: [{kind: 'project'}], sel: null, view: 'rows', measure: 'analyzed', zoom: null,
    groupBy: 'story', filter: '', wholeSpan: {}, focusRow: -1, listScroll: 0,
  };
  const PATH_KEY = 'ingence.years.path';
  let M = null;           // the model of the pinned run
  let loadingModel = null;
  let lastRows = [];      // the rows last drawn, for the keyboard
  let lastAxis = null;
  const filesCache = new Map();
  const designCache = new Map();  // the design grid of an experiment, read when its Design chip is chosen
  const entryCache = new Map();
  let yearsStory = null;  // /api/v1/themes/years, read once per run
  let stabilityHeld = null;
  let overlapHeld = null;  // /api/v1/themes/overlap, read once per run
  let noAnalysisHeld = null;

  /* By year is a mode of Overlap. The rows draw only while the Overlap view shows its By year mode,
   * into the box overlap.js gives them under its mode bar. */
  let hostEl = null;
  const host = () => (hostEl && hostEl.isConnected ? hostEl : listPane);
  function shown() {
    if (state.view !== 'overlap') return false;
    return !window.ingenceOverlap || window.ingenceOverlap.mode() === 'by_year';
  }

  /* ------------------------------------------------------------------ topics */

  /* A topic is a lens and a mark on the rows, never a rung. When a person chooses a topic, the rows
   * show only its experiments, at every level (the people, the lines of work, the experiments,
   * their files), and the ladder itself stays the same. One click clears the topic. A row names
   * the other topics its experiments are in ("also in" chips). A topic is the person's. Its marks are magenta,
   * and nothing here reads it as evidence. Rows can be picked (a line of work, an experiment) and
   * made a topic with "Add to topic...". topic_kit.js holds the chooser, the colors and the
   * chips. */
  const LENS_KEY = 'ingence.years.topic';
  let lens = '';   // the topic whose experiments the rows show; '' for all of them
  try { lens = sessionStorage.getItem(LENS_KEY) || ''; } catch (error) { lens = ''; }
  const picked = new Map();   // the rows picked for a topic: key -> {key, label, member}
  function lensTopic() {
    const reading = window.topicKit ? topicKit.current() : null;
    return lens && reading ? reading.topics.find((t) => t.id === lens) || null : null;
  }
  const lensed = () => !!lensTopic();
  /* An experiment in the lens: all of them where there is none. */
  function inLens(e) { const t = lensTopic(); return !t || t.experiments.has(e.id); }
  function setLens(topicId) {
    lens = topicId || '';
    try { if (lens) sessionStorage.setItem(LENS_KEY, lens); else sessionStorage.removeItem(LENS_KEY); } catch (error) { /* a convenience */ }
    Y.sel = null; Y.focusRow = -1; Y.listScroll = 0;
    if (shown() && M) render();
  }
  /* The years the experiments in view fall in, for a title. */
  const yearsOf = (exps) => [...new Set(exps.map((e) => e.year).filter((y) => y != null))].sort((a, b) => a - b);

  function v1(path) { return api(path); }

  /* A reason ends with a full stop, except where it ends with a command to copy: a stop after
   * `pip install scikit-learn scipy` would be copied with it. */
  function endSentence(text) {
    return /\.$/.test(text) || /pip install [^.]*$/.test(text) ? text : `${text}.`;
  }

  /* ------------------------------------------------------------------ the model */

  /* Everything the scales draw, from the engine's own routes, joined by the experiment folder's
   * identifier; cached while the pinned run is the same. */
  async function load({force = false} = {}) {
    const version = await v1('/api/v1/version');
    if (!force && M && M.run === version.run_pinned) return M;
    if (loadingModel && !force) return loadingModel;
    loadingModel = (async () => {
      let themes = null; let themesSaid = '';
      try {
        themes = await v1('/api/v1/themes');
      } catch (error) {
        themesSaid = String(error.message || error);
      }
      let completeness = null; let completenessSaid = '';
      try {
        completeness = await v1('/api/v1/completeness');
      } catch (error) {
        completenessSaid = String(error.message || error);
      }
      // The project's roots: an archive read from its listings has one root per listing, and the
      // first folder below such a root is not a person's, so the archive's ladder has no person
      // level. The root is the archive, and each experiment is a study.
      let roots = [];
      try { roots = (await api('/api/project')).roots || []; } catch (error) { roots = []; }
      // The project's ladder, with the word for each level. The words are the engine's. A
      // project whose engine serves none keeps the page's fallback words.
      let ladder = null;
      try { ladder = await v1('/api/v1/ladder'); } catch (error) { ladder = null; }
      const built = buildModel(themes, completeness, version, roots, ladder);
      built.themesSaid = themesSaid; built.completenessSaid = completenessSaid;
      M = built; ladderHeld = built.ladder; yearsStory = null; stabilityHeld = null; overlapHeld = null; noAnalysisHeld = null;
      filesCache.clear(); entryCache.clear(); designCache.clear();
      loadingModel = null;
      return built;
    })();
    return loadingModel;
  }

  function buildModel(themes, completeness, version, roots, ladder) {
    const personSums = new Map((completeness ? completeness.people : []).map((r) => [r.key, r]));
    const themeSums = new Map((completeness ? completeness.themes : []).map((r) => [Number(r.key), r]));
    const rootById = new Map(roots.map((r) => [r.root_id, r]));
    // An archive read from its listings: the engine's ladder says so (``kind``). An engine that
    // serves none leaves the page to read it from the roots' addresses.
    const fromRoots = roots.length > 0 && roots.every((r) => String(r.root_uri || '').startsWith('listing:'));
    const listing = ladder ? ladder.kind === 'archive' : fromRoots;
    const rootName = (id) => {
      const r = rootById.get(id); if (!r) return '';
      const uri = String(r.root_uri || '');
      if (uri.startsWith('listing:')) return uri.split(':').pop();
      try { return decodeURIComponent(uri.replace(/\/+$/, '').split('/').pop() || uri); } catch (error) { return uri; }
    };
    const byId = new Map();
    const exps = [];
    const add = (row) => { let e = byId.get(row.id); if (!e) { e = {id: row.id}; byId.set(row.id, e); exps.push(e); } return e; };
    if (themes) {
      for (const row of themes.experiments) {
        const e = add(row);
        Object.assign(e, {folder: row.folder, name: row.name, shown: row.shown_name, name_parts: row.name_parts,
          root: row.root, date: row.date, last_file_time: row.last_file_time,
          year: row.year, files: row.files, bytes: row.bytes, theme: row.theme, theme_label: row.theme_label,
          broad_theme: row.broad_theme, broad_label: row.broad_label, unplaced_reason: row.unplaced_reason,
          person: row.person, collection: row.collection, words: row.words || []});
      }
    }
    if (completeness) {
      for (const row of completeness.experiments) {
        const e = add(row);
        if (!e.folder) e.folder = row.folder;
        if (e.name == null) { e.name = row.name; e.shown = row.shown_name; e.name_parts = row.name_parts; }
        if (e.person == null) e.person = row.person;
        // Dated from the names even where themes cannot be drawn.
        if (e.date == null && row.date != null) e.date = row.date;
        if (e.theme == null && row.theme != null) e.theme = Number(row.theme);
        Object.assign(e, {files: e.files ?? row.files, raw_acquisitions: row.raw_acquisitions, derived_files: row.derived_files,
          raw_followed: row.raw_followed, share_of_raw_followed_by_analysis: row.share_of_raw_followed_by_analysis,
          analysis_files: row.analysis_files, result_tables: row.result_tables, has_analysis: row.has_analysis,
          said: row.said, analysed_elsewhere: row.analysed_elsewhere, answer_target: row.answer_target, root_id: row.root_id});
      }
    }
    for (const e of exps) {
      e.rootName = rootName(e.root || e.root_id);
      e.at = e.date ? ms(e.date) : NaN;
      e.until = e.last_file_time ? ms(e.last_file_time) : NaN;
      if (e.year == null && e.date) e.year = Number(String(e.date).slice(0, 4));
      e.words = e.words || [];
    }
    exps.sort((a, b) => (Number.isNaN(a.at) ? 1 : 0) - (Number.isNaN(b.at) ? 1 : 0) || a.at - b.at || a.folder.localeCompare(b.folder));
    // Lines of work: the themes with their experiments.
    const themeList = []; const themeById = new Map();
    if (themes) {
      for (const t of themes.themes) {
        const item = {...t, exps: [], key: t.target, shown: t.person_label || t.label};
        themeList.push(item); themeById.set(t.theme, item);
      }
      for (const e of exps) if (e.theme != null && themeById.has(e.theme)) themeById.get(e.theme).exps.push(e);
      for (const t of themeList) {
        const served = themeSums.get(t.theme);
        t.a = served ? sumShape(served) : agg(t.exps);
        t.n = served ? served.experiments : t.exps.length;
      }
      themeList.sort((a, b) => b.exps.length - a.exps.length || a.label.localeCompare(b.label));
    }
    const broadList = []; const broadById = new Map();
    if (themes) {
      for (const b of themes.broad) {
        const item = {...b, themes: [], exps: []};
        broadList.push(item); broadById.set(b.broad_theme, item);
      }
      for (const t of themeList) { const b = broadById.get(t.broad_theme); if (b) { b.themes.push(t); b.exps.push(...t.exps); } }
      for (const b of broadList) b.a = agg(b.exps);
      broadList.sort((a, b) => b.exps.length - a.exps.length);
    }
    // People: the first folder level below a root, as completeness and themes read them.
    const peopleById = new Map();
    for (const e of exps) {
      const name = listing ? '' : (e.person || '');
      if (!name) continue;
      let p = peopleById.get(name);
      if (!p) { p = {name, exps: [], byYear: {}, themes: new Set()}; peopleById.set(name, p); }
      p.exps.push(e);
      if (e.year != null) p.byYear[e.year] = (p.byYear[e.year] || 0) + 1;
      if (e.theme != null) p.themes.add(e.theme);
    }
    const people = [...peopleById.values()];
    for (const p of people) {
      const years = Object.keys(p.byYear).map(Number);
      p.first = years.length ? Math.min(...years) : null; p.last = years.length ? Math.max(...years) : null;
      p.a = agg(p.exps);
    }
    people.sort((a, b) => (a.first ?? 9999) - (b.first ?? 9999) || b.exps.length - a.exps.length);
    // The sums are the engine's (``/api/v1/completeness``: ``people`` and ``themes``, summed over
    // the experiments it found): a person's and a line's count of experiments and its raw data
    // followed by an analysis are read from there. Where a count crosses two groups (a line within
    // one person) no sum is served, and the page adds up the served experiments' own counts.
    for (const p of people) {
      const served = personSums.get(p.name);
      if (served) { p.a = sumShape(served); p.n = served.experiments; } else p.n = p.exps.length;
    }
    const years = [...new Set(exps.map((e) => e.year).filter((y) => y != null))].sort((a, b) => a - b);
    const unplaced = exps.filter((e) => e.theme == null);
    return {
      ladder: ladderOf(ladder, listing), personSums, themeSums,
      run: version.run_pinned, exps, byId, themes: themeList, themeById, broads: broadList, broadById,
      people, peopleById, years, unplaced, themesComputed: !!themes, summary: themes ? themes.summary : null,
      unplacedNote: themes ? themes.unplaced_note : '', notShown: themes ? themes.experiments_not_shown : 0,
      completeness, answerWords: completeness ? completeness.answer_words : [], groups: completeness ? completeness.themes : [],
      peopleRows: completeness ? completeness.people : [], outside: completeness ? completeness.outside : null,
      thresholds: completeness ? completeness.settings : null, files: exps.reduce((n, e) => n + (e.files || 0), 0),
      multiPerson: people.length > 1 && !listing, roots, listing,
    };
  }

  /* The ladder: what a row is at each level, where it comes from, which axis it draws. A root's
   * levels are data, so a third kind of root is a third configuration. */
  const LADDER = [
    {kind: 'project', name: 'Project', rows: 'people, where each top folder is a person\'s; otherwise themes', source: '/api/v1/themes (experiments and their person), /api/v1/completeness (people)', axis: 'the year of each experiment', measures: ['count', 'raw data analyzed']},
    {kind: 'person', name: 'Area', rows: 'themes, under their broad themes', source: '/api/v1/themes (recomputed when read; never a result)', axis: 'the date of each experiment', measures: ['raw data analyzed', 'a person said so']},
    {kind: 'theme', name: 'Theme', rows: 'experiments', source: '/api/v1/themes, /api/v1/completeness', axis: 'the experiment\'s date, a line to the last file written', measures: ['raw data analyzed', 'a person said so']},
    {kind: 'exp', name: 'Experiment', rows: 'files, grouped by folder, stage or kind, or one by one', source: '/api/v1/experiments/files (each file with what Ingence found about it)', axis: 'the believed time of each file (the earliest credible time rule), else its modification', measures: ['stage', 'kind']},
    {kind: 'file', name: 'File', rows: 'its details: the parts of its name, its clocks, every result with its rule, what it was made from, its copies', source: '/api/v1/entry, /api/v1/experiments/files', axis: 'its own clocks, sources never merged', measures: []},
  ];
  const LADDER_NOTE = 'An archive read from its listing climbs archive, theme, study, file, with no person level. Its studies are the experiments Ingence found, and its release years are the dates. A third kind of data folder is one more row of this table.';

  /* ------------------------------------------------------------------ navigation */

  const scale = () => Y.path[Y.path.length - 1];
  function keep() { try { sessionStorage.setItem(PATH_KEY, JSON.stringify(Y.path)); } catch (error) { /* a convenience */ } }
  function restorePath() {
    try {
      const held = JSON.parse(sessionStorage.getItem(PATH_KEY) || 'null');
      if (Array.isArray(held) && held.length && held[0].kind === 'project') Y.path = held;
    } catch (error) { /* a convenience */ }
  }
  function go(path, extra = {}) {
    Y.path = path; Y.sel = null; Y.view = 'rows'; Y.zoom = null; Y.focusRow = -1; Y.filter = ''; Y.listScroll = 0;
    Object.assign(Y, extra);
    keep();
    render();
  }
  /* One scale up. From the keyboard (``focus``) the row just left is the one to take the focus, so
   * a walk down and back up ends where it began. */
  function up({focus = false} = {}) {
    if (Y.view !== 'rows') { Y.view = 'rows'; render(); return; }
    if (Y.path.length > 1) {
      const from = scale();
      go(Y.path.slice(0, -1));
      if (focus) {
        const index = lastRows.findIndex((r) => r.kind === from.kind && r.id === from.id);
        if (index >= 0) Y.focusRow = index;
        Y.keyFocus = true;
        keepKeyFocus();
      }
    }
  }
  function select(sel) {
    Y.sel = sel;
    if (sel && (sel.kind === 'exp' || sel.kind === 'file') && sel.id) holdFocus({kind: 'entry', id: sel.id, label: nodeName(sel)});
    render();
  }
  // An experiment opens on its story. Its files are one click down.
  function open(node) { go(pathTo(node), node.kind === 'exp' ? {groupBy: 'story'} : {}); }

  function pathTo(node) {
    const project = [{kind: 'project'}];
    const personOf = (e) => (M.multiPerson && e && e.person ? [{kind: 'person', id: e.person}] : []);
    if (node.kind === 'project') return project;
    if (node.kind === 'person') return [...project, {kind: 'person', id: node.id}];
    if (node.kind === 'unplaced') return [...project, ...(node.person ? [{kind: 'person', id: node.person}] : []), {kind: 'unplaced', person: node.person || null}];
    if (node.kind === 'theme') {
      const person = node.person ? [{kind: 'person', id: node.person}] : [];
      return [...project, ...person, {kind: 'theme', id: node.id, person: node.person || null}];
    }
    if (node.kind === 'exp') {
      const e = M.byId.get(node.id);
      const theme = e && e.theme != null && M.themeById.has(e.theme) ? [{kind: 'theme', id: e.theme, person: M.multiPerson && e.person ? e.person : null}] : [];
      return [...project, ...personOf(e), ...theme, {kind: 'exp', id: node.id}];
    }
    if (node.kind === 'file') {
      const e = M.byId.get(node.exp);
      return [...pathTo({kind: 'exp', id: node.exp}), {kind: 'file', id: node.id, exp: node.exp, name: node.name || ''}];
    }
    return project;
  }

  function themeShown(t) { return t.person_label || t.label; }
  function nodeName(n) {
    if (!M) return '';
    switch (n.kind) {
      case 'project': return cap(word('project'));
      case 'person': return n.id;
      case 'theme': { const t = M.themeById.get(n.id); return t ? themeShown(t) : `${article(word('theme'))} ${word('theme')}`; }
      case 'exp': { const e = M.byId.get(n.id); return e ? e.shown : `${article(word('exp'))} ${word('exp')}`; }
      case 'file': return n.name || `${article(word('file'))} ${word('file')}`;
      case 'unplaced': return `In no ${word('theme')}`;
      case 'year': return `What was I working on in ${n.id}`;
      default: return '';
    }
  }

  /* ------------------------------------------------------------------ the time axis */

  function zoomKey() { return Y.path.map((n) => `${n.kind}:${n.id || ''}`).join('/'); }
  /* The axis for a span, zoomed where the person zoomed, or opened on a default window until the
   * whole span is asked for. */
  function axisFor(t0, t1, def) {
    if (!(t1 > t0)) t1 = t0 + DAY * 30;
    const full = {t0, t1};
    let a0 = t0; let a1 = t1; let defaulted = false;
    if (Y.zoom && Y.zoom.key === zoomKey()) { a0 = Y.zoom.t0; a1 = Y.zoom.t1; }
    else if (def && !Y.wholeSpan[zoomKey()]) { a0 = def.t0; a1 = def.t1; defaulted = true; }
    const span = Math.max(1, a1 - a0);
    const A = {
      t0: a0, t1: a1, full, defaulted, span,
      x: (t) => `${(((ms(t) - a0) / span) * 100).toFixed(3)}%`,
      w: (ta, tb) => `${Math.max(0.3, ((ms(tb) - ms(ta)) / span) * 100).toFixed(3)}%`,
      zoomed: a0 !== t0 || a1 !== t1,
    };
    lastAxis = A;
    return A;
  }
  function showWholeSpan() { Y.zoom = null; Y.wholeSpan[zoomKey()] = true; render(); }

  /* Ticks step from five-year to year, quarter, month, week, day, hour and minute as the span
   * narrows. Labels thin out so none collide. */
  function timeTicks(A, onYear) {
    const span = A.t1 - A.t0; const days = span / DAY; const out = [];
    const pad = (n) => String(n).padStart(2, '0');
    const push = (t, tn, label, year) => {
      const l = ((t - A.t0) / span) * 100; const r = ((tn - A.t0) / span) * 100;
      if (r <= 0 || l >= 100) return;
      out.push({t, tn, label, left: Math.max(0, l), width: Math.min(100, r) - Math.max(0, l), year});
    };
    const d0 = new Date(A.t0);
    if (days > 3 * 365) {
      const every = days > 12 * 365 ? 5 : days > 7 * 365 ? 2 : 1;
      for (let y = d0.getUTCFullYear(); Date.UTC(y, 0, 1) < A.t1; y += 1) push(Date.UTC(y, 0, 1), Date.UTC(y + 1, 0, 1), (every === 1 || y % every === 0) ? String(y) : '', y);
    } else if (days > 120) {
      const step = days > 500 ? 3 : 1; let y = d0.getUTCFullYear(); let m = Math.floor(d0.getUTCMonth() / step) * step;
      while (Date.UTC(y, m, 1) < A.t1) { push(Date.UTC(y, m, 1), Date.UTC(y, m + step, 1), m === 0 || (step === 1 && m % 6 === 0) ? `${MON[m]} ${y}` : MON[m], y); m += step; if (m >= 12) { m -= 12; y += 1; } }
    } else if (days > 4) {
      const step = days > 60 ? 7 : 1; let t = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth(), d0.getUTCDate());
      while (t < A.t1) { const d = new Date(t); push(t, t + step * DAY, `${d.getUTCDate()} ${MON[d.getUTCMonth()]}`, null); t += step * DAY; }
    } else if (days > 0.2) {
      const step = days > 2 ? 6 : days > 0.6 ? 2 : 1; let t = Math.floor(A.t0 / 36e5) * 36e5;
      while (t < A.t1) { const d = new Date(t); push(t, t + step * 36e5, d.getUTCHours() === 0 ? `${d.getUTCDate()} ${MON[d.getUTCMonth()]}` : `${pad(d.getUTCHours())}:00`, null); t += step * 36e5; }
    } else {
      const step = span > 36e5 ? 6e5 : span > 6e5 ? 6e4 : span > 1.2e5 ? 1e4 : 5e3; let t = Math.floor(A.t0 / step) * step;
      while (t < A.t1) { const d = new Date(t); push(t, t + step, `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${step < 6e4 ? `:${pad(d.getUTCSeconds())}` : ''}`, null); t += step; }
    }
    // Thin the labels so none collide: keep every k-th labeled tick, the one with a year first.
    const axisPx = Math.max(200, (listPane.clientWidth || 900) - 520);
    const labelled = out.filter((t) => t.label);
    if (labelled.length) {
      const perLabelPx = axisPx / labelled.length;
      const need = Math.max(...labelled.map((t) => t.label.length), 1) * 7 + 10;
      const k = Math.max(1, Math.ceil(need / Math.max(1, perLabelPx)));
      if (k > 1) {
        const yi = labelled.findIndex((t) => /\d{4}/.test(t.label)); const off = yi >= 0 ? yi % k : 0;
        labelled.forEach((t, i) => { if ((i - off + k) % k !== 0) t.label = ''; });
      }
    }
    for (const t of out) t.onYear = onYear && t.year != null ? () => onYear(t.year) : null;
    return out;
  }

  /* Bins for a dense row: at most ``nb`` across the axis, each with its count. */
  function bins(times, A, nb) {
    const w = A.span / nb; const out = new Map();
    for (const t of times) { if (Number.isNaN(t) || t < A.t0 || t > A.t1) continue; const k = Math.min(nb - 1, Math.floor((t - A.t0) / w)); out.set(k, (out.get(k) || 0) + 1); }
    return [...out.entries()].sort((a, b) => a[0] - b[0]).map(([k, n]) => ({t: A.t0 + k * w, tn: A.t0 + (k + 1) * w, n}));
  }

  /* ------------------------------------------------------------------ rows at each scale */

  const LEGEND_C = [['all', 'all raw data analyzed'], ['most', 'most'], ['some', 'some'], ['none', 'none'],
    ['na', 'no raw data found'], ['gap', 'analysis here, raw data not found'], ['human', 'a person said so']];

  function expMark(A, e, extra = {}) {
    const l = level(e); const gap = rawState(e) === 'gap';
    const human = Y.measure === 'provenance' ? (e.analysed_elsewhere ? 'human' : 'plain') : null;
    const classes = ['ymark', human || (gap ? 'na' : l)];
    if (gap && Y.measure !== 'provenance') classes.push('gap');
    if (e.analysed_elsewhere && e.analysed_elsewhere.said_now && !e.analysed_elsewhere.read_by_run) classes.push('pending');
    if (Y.sel && Y.sel.kind === 'exp' && Y.sel.id === e.id) classes.push('selected');
    const title = `${e.shown} · ${e.date ? dayWords(e.date) : 'undated'} · ${gap ? 'analysis here, raw data not found' : LEVEL_WORDS[l]}`
      + (e.analysed_elsewhere && e.analysed_elsewhere.said_now ? ` · ${e.analysed_elsewhere.said}` : '');
    return {left: A.x(e.at), top: extra.top || '10px', classes, title, op: extra.op ?? 1,
      onClick: () => select({kind: 'exp', id: e.id})};
  }
  /* One mark per experiment at its date, stacked within a month so none hides another. */
  function stack(exps, A, op) {
    const seen = {}; const out = [];
    for (const e of exps) {
      if (Number.isNaN(e.at)) continue;
      const k = new Date(e.at).toISOString().slice(0, 7); const n = (seen[k] = (seen[k] || 0) + 1);
      out.push(expMark(A, e, {top: `${8 + (n - 1) * 7}px`, op}));
    }
    return out;
  }
  function yearBars(byYear, A, max, make) {
    return Object.entries(byYear).map(([y, n]) => {
      const h = Math.max(4, Math.round(30 * n / Math.max(1, max)));
      return {bin: true, left: A.x(`${y}-01-15`), width: `calc(${A.w(`${y}-01-01`, `${y}-12-31`)} - 3px)`, height: `${h}px`, classes: ['ybin'], title: make(y, n)};
    });
  }
  function barOf(a, right, rightClass) {
    if (!a.raw) return {na: true, text: 'no raw data found', right: right || '', rightClass};
    return {pct: Math.round((a.share || 0) * 100), text: `${fmt(a.followed)} of ${fmt(a.raw)} raw files analyzed`, right: right || '', rightClass};
  }
  function directionWords(t) {
    if (t.direction === 'stopped') return `stopped ${t.last}`;
    if (t.direction === 'new') return 'new';
    if (t.direction === 'rising') return 'rising';
    if (t.direction === 'falling') return 'falling';
    if (t.direction === 'undated') return 'undated';
    return t.going ? 'still going' : `last ${t.last}`;
  }
  const isSel = (kind, id) => !!(Y.sel && Y.sel.kind === kind && Y.sel.id === id);
  const softTheme = (t) => t.stability != null && t.stability < 0.8;
  /* A value on a 0 to 1 axis, drawn as a track. The chance band is hatched where one is given
     (adjusted indices are about 0 for placements made at random). A tick marks a line worth
     reading against (the 0.80 under which a boundary is called uncertain), and a mark shows the
     value. The number sits beside it, and the whole is described for a screen reader. */
  const axisPct = (x) => `${Math.round(Math.max(0, Math.min(1, x)) * 1000) / 10}%`;
  function axisValue(value, o) {
    const opt = o || {};
    const parts = [];
    if (opt.band) parts.push(node('span', {class: 'yaxband', style: `left:${axisPct(opt.band[0])};width:${axisPct(opt.band[1] - opt.band[0])}`}));
    if (opt.tick != null) parts.push(node('span', {class: 'yaxtick', style: `left:${axisPct(opt.tick)}`}));
    for (const x of (opt.dots || [])) parts.push(node('span', {class: 'yaxdot', style: `left:${axisPct(x)}`}));
    if (value != null) parts.push(node('span', {class: `yaxmark${opt.soft ? ' soft' : ''}`, style: `left:${axisPct(value)}`}));
    const said = opt.said || (value == null ? 'not measured' : `${value.toFixed(2)} on a scale from 0 to 1`);
    return node('span', {class: 'yaxbox'}, [
      node('span', {class: 'yaxis01', role: 'img', 'aria-label': said, title: opt.title || said}, parts),
      node('span', {class: 'ybartext', text: opt.text != null ? opt.text : (value == null ? 'not measured' : value.toFixed(2))}),
    ]);
  }
  const CHANCE_BAND = [0, 0.05];

  function spanOf(exps) {
    const ats = exps.map((e) => e.at).filter((t) => !Number.isNaN(t));
    const untils = exps.map((e) => e.until).filter((t) => !Number.isNaN(t));
    if (!ats.length) return null;
    const t0 = Math.min(...ats); const t1 = Math.max(...ats, ...untils);
    return {t0: Date.UTC(new Date(t0).getUTCFullYear(), 0, 1), t1: Date.UTC(new Date(t1).getUTCFullYear() + 1, 0, 1)};
  }

  function themeRow(t, A, person) {
    const sel = isSel('theme', t.theme);
    const dimmed = Y.sel && Y.sel.kind === 'theme' && !sel;
    const exps = (person ? t.exps.filter((e) => e.person === person) : t.exps).filter(inLens);
    // The engine's sums for a line; within one person's (or a topic's) no sum is served, so the page
    // adds up the served experiments' own counts.
    const summed = person || lensed();
    const a = summed ? agg(exps) : t.a;
    const n = summed ? exps.length : t.n;
    const renamed = !!t.person_label;
    const dated = exps.filter((e) => !Number.isNaN(e.at));
    const spans = dated.length > 1 ? [{left: A.x(dated[0].at), width: A.w(dated[0].at, dated[dated.length - 1].at), top: '48px'}] : [];
    return {
      isRow: true, kind: 'theme', id: t.theme, label: themeShown(t), labelHuman: renamed,
      sub: `${renamed ? `Ingence: ${t.label} · ` : ''}${countOf(n, 'exp')}${t.peak ? ` · peak ${t.peak}` : ''}${t.undated ? ` · ${t.undated} undated` : ''}`,
      height: 58, selected: sel, dim: dimmed, soft: softTheme(t), human: renamed,
      marks: stack(exps, A, dimmed ? 0.3 : 1), spans,
      bar: barOf(a, directionWords(t), t.going ? 'going' : ''),
      canOpen: true, onClick: () => select({kind: 'theme', id: t.theme, person}), onOpen: () => open({kind: 'theme', id: t.theme, person}),
      topicIds: exps.map((e) => e.id),
      pick: {key: `line:${t.theme}`, label: themeShown(t), member: {kind: 'line', experiments: t.exps.map((e) => e.id).sort(), words: String(t.label || '').split(' \u00b7 ').filter(Boolean), label: t.label}},
    };
  }
  function expRow(e, A) {
    const sel = isSel('exp', e.id); const rs = rawState(e); const l = level(e);
    const elsewhere = e.analysed_elsewhere && e.analysed_elsewhere.said_now;
    const spans = !Number.isNaN(e.until) && e.until > e.at ? [{left: A.x(e.at), width: A.w(e.at, e.until), top: '20px', height: '3px'}] : [];
    let bar;
    if (rs === 'has') bar = {pct: Math.round((e.share_of_raw_followed_by_analysis || 0) * 100), text: `${fmt(e.raw_followed)} of ${fmt(e.raw_acquisitions)} raw files analyzed`, right: elsewhere ? 'analyzed elsewhere' : LEVEL_WORDS[l], rightClass: elsewhere ? 'human' : ''};
    else if (rs === 'gap') bar = {na: true, text: elsewhere ? 'raw data elsewhere, a person said' : 'analysis here, raw data not found', right: `${plural(e.analysis_files, 'analysis file')}`, rightClass: elsewhere ? 'human' : 'problem'};
    else bar = {na: true, text: 'no raw data found', right: plural(e.files, 'file'), rightClass: ''};
    return {
      isRow: true, kind: 'exp', id: e.id, label: e.shown,
      sub: `${e.date ? dayWords(e.date) : 'undated'} · ${plural(e.files, 'file')}${M.listing && e.rootName ? ` · ${e.rootName}` : (scale().kind === 'person' || (e.person && !M.multiPerson) ? '' : (e.person ? ` · ${e.person}` : ''))}${elsewhere && !e.analysed_elsewhere.read_by_run ? ' · waits for Re-run' : ''}`,
      height: 48, selected: sel, human: !!elsewhere, problem: rs === 'gap' && !elsewhere, subHuman: !!elsewhere,
      marks: Number.isNaN(e.at) ? [] : [expMark(A, e)], spans, bar,
      canOpen: true, onClick: () => select({kind: 'exp', id: e.id}), onOpen: () => open({kind: 'exp', id: e.id}),
      topicIds: [e.id], pick: {key: `exp:${e.id}`, label: e.shown, member: {kind: 'experiment', id: e.id}},
    };
  }

  function rowsFor() {
    const node = scale();
    const none = {title: '', hint: '', ticks: [], rows: [], rightHead: '', note: '', legend: [], keys: ''};
    if (!M.exps.length) {
      return {...none, title: `No ${word('exp', true)} found`, hint: M.completenessSaid || M.themesSaid || `This scan found no folder it reads as ${article(word('exp'))} ${word('exp')}. The Inventory shows what is here, and Checks shows what Ingence could not decide.`};
    }
    if (lensed() && !M.exps.some(inLens)) {
      return {...none, title: `The collection ${lensTopic().name} holds no ${word('exp')} in this scan`, hint: `Nothing is drawn, because the collection's members match no ${word('exp')} here. Its card in Collections says why. Clear the collection to see every ${word('exp')}.`};
    }
    if (node.kind === 'project') {
      if (M.multiPerson && (lensed() ? yearsOf(M.exps.filter(inLens)) : M.years).length) {
        const mine = lensed() ? M.people.map((p) => {
          // Within a topic no sum is served, so the page adds up the served experiments' own counts.
          const exps = p.exps.filter(inLens); const byYear = {};
          for (const e of exps) if (e.year != null) byYear[e.year] = (byYear[e.year] || 0) + 1;
          const ys = Object.keys(byYear).map(Number);
          return {...p, exps, byYear, n: exps.length, a: agg(exps), first: ys.length ? Math.min(...ys) : null, last: ys.length ? Math.max(...ys) : null,
            themes: new Set(exps.map((e) => e.theme).filter((th) => th != null))};
        }).filter((p) => p.exps.length) : M.people;
        const years = lensed() ? yearsOf(M.exps.filter(inLens)) : M.years;
        const A = axisFor(Date.UTC(years[0], 0, 1), Date.UTC(years[years.length - 1] + 1, 0, 1));
        const max = Math.max(1, ...mine.flatMap((p) => Object.values(p.byYear)));
        const rows = mine.map((p) => ({
          isRow: true, kind: 'person', id: p.name, label: p.name,
          sub: `${countOf(p.n, 'exp')} · ${p.themes.size ? countOf(p.themes.size, 'theme') : `no ${word('theme')}`}`,
          height: 44, selected: isSel('person', p.name),
          marks: yearBars(p.byYear, A, max, (y, n) => `${p.name} · ${y} · ${countOf(n, 'exp')}`),
          bar: barOf(p.a, p.first != null ? `${p.first}–${p.last}` : 'undated'),
          canOpen: true, onClick: () => select({kind: 'person', id: p.name}), onOpen: () => open({kind: 'person', id: p.name}),
          topicIds: p.exps.map((e) => e.id),
        }));
        return {...none, title: `The ${word('project')}, ${years[0]}–${years[years.length - 1]}`, hint: rowSentence('person', 'exp', lensed() ? M.exps.filter(inLens).length : M.exps.length, mine.length),
          ticks: timeTicks(A, (y) => select({kind: 'year', id: y})), rows, rightHead: 'Raw files analyzed', legend: [['plain', `${word('exp', true)} per year`]], keys: KEYS_ROWS, A};
      }
      return themeRows(null);
    }
    if (node.kind === 'person') return areaRows(node.id);
    if (node.kind === 'theme') {
      const t = M.themeById.get(node.id);
      if (!t) return {...none, title: `This ${word('theme')} is not in this scan`, hint: `The scan shown now has no ${word('theme')} with this identifier. Go up a level.`};
      const exps = (node.person ? t.exps.filter((e) => e.person === node.person) : t.exps).filter(inLens);
      const sp = spanOf(exps) || {t0: Date.UTC(2020, 0, 1), t1: Date.UTC(2021, 0, 1)};
      const A = axisFor(sp.t0, sp.t1);
      const rows = exps.map((e) => expRow(e, A));
      const undated = exps.filter((e) => Number.isNaN(e.at)).length;
      return {...none, title: `${themeShown(t)}, ${t.first ?? '?'}–${t.last ?? '?'}`,
        hint: `${countOf(exps.length, 'exp')}. The mark is the ${word('exp')}'s date. The line goes to the last time a file in it was written.${softTheme(t) ? ` Boundary uncertain: this ${word('theme')} is not stable.` : ''}${undated ? ` ${countOf(undated, 'exp')} ${undated === 1 ? 'is' : 'are'} undated and drawn with no mark.` : ''}`,
        ticks: timeTicks(A, (y) => select({kind: 'year', id: y})), rows, rightHead: 'Raw files analyzed', legend: LEGEND_C, keys: KEYS_ROWS, A};
    }
    if (node.kind === 'unplaced') {
      const exps = M.exps.filter((e) => e.theme == null && (!node.person || e.person === node.person) && inLens(e));
      const sp = spanOf(exps) || {t0: Date.UTC(2020, 0, 1), t1: Date.UTC(2021, 0, 1)};
      const A = axisFor(sp.t0, sp.t1);
      const reasons = {}; for (const e of exps) { const r = e.unplaced_reason || M.unplacedNote || 'no reason given'; reasons[r] = (reasons[r] || 0) + 1; }
      return {...none, title: `In no ${word('theme')}, ${countOf(exps.length, 'exp')}`,
        hint: `${Object.entries(reasons).sort((a, b) => b[1] - a[1]).map(([r, n]) => `${fmt(n)}: ${r}`).join('. ')}. ${cap(article(word('theme')))} ${word('theme')} is made of words; ${article(word('exp'))} ${word('exp')} whose words no other shares, or that reads the same words as another, is left out rather than forced in.`,
        ticks: timeTicks(A, (y) => select({kind: 'year', id: y})), rows: exps.map((e) => expRow(e, A)), rightHead: 'Raw files analyzed', legend: LEGEND_C, keys: KEYS_ROWS, A};
    }
    if (node.kind === 'exp') return filesRows(node);
    if (node.kind === 'file') return {...none, title: node.name || cap(`${article(word('file'))} ${word('file')}`), hint: `Its details are beside this. The ${word('file', true)} around it are one level up.`, rows: []};
    return none;
  }

  /* Lines of work (and their broad lines as header rows where the broad level says something),
   * for the project or one person. */
  function themeRows(person) {
    const none = {title: '', hint: '', ticks: [], rows: [], rightHead: '', note: '', legend: [], keys: ''};
    const exps = (person ? M.exps.filter((e) => e.person === person) : M.exps).filter(inLens);
    if (!M.themesComputed) {
      const sp = spanOf(exps) || {t0: Date.UTC(2020, 0, 1), t1: Date.UTC(2021, 0, 1)};
      const A = axisFor(sp.t0, sp.t1);
      return {...none, title: person ? `${person}` : `The ${word('project')}`, hint: `Rows are the ${word('exp', true)} found, dated from their names where a name gives a date. ${cap(word('theme', true))} are not drawn: ${endSentence(M.themesSaid || 'themes were not computed')}`,
        ticks: timeTicks(A, (y) => select({kind: 'year', id: y})), rows: exps.map((e) => expRow(e, A)), rightHead: 'Raw files analyzed', legend: LEGEND_C, keys: KEYS_ROWS, A};
    }
    const themes = M.themes.filter((t) => t.exps.some((e) => (!person || e.person === person) && inLens(e)));
    const sp = spanOf(exps) || {t0: Date.UTC(2020, 0, 1), t1: Date.UTC(2021, 0, 1)};
    const A = axisFor(sp.t0, sp.t1);
    const rows = [];
    // Broad lines head the rows only where they gather: a broad line per theme says nothing.
    const headed = M.broads.length && M.broads.length <= Math.ceil(themes.length * 0.6);
    if (headed) {
      for (const b of M.broads) {
        const here = b.themes.filter((t) => themes.includes(t));
        if (!here.length) continue;
        const bexps = (person ? b.exps.filter((e) => e.person === person) : b.exps).filter(inLens);
        const ba = agg(bexps);
        rows.push({isHead: true, label: b.label, sub: `${countOf(person || lensed() ? bexps.length : b.experiments, 'exp')} · ${countOf(here.length, 'theme')}${ba.raw ? ` · ${fmt(ba.followed)} of ${fmt(ba.raw)} raw files analyzed` : ''}`});
        for (const t of here) rows.push(themeRow(t, A, person));
      }
    } else for (const t of themes) rows.push(themeRow(t, A, person));
    const unplacedHere = exps.filter((e) => e.theme == null);
    if (unplacedHere.length) {
      // The experiments in no line: a few here, the whole list one scale down.
      rows.push({isHead: true, label: `In no ${word('theme')}`, sub: `${countOf(unplacedHere.length, 'exp')}: ${unplacedHere[0].unplaced_reason || M.unplacedNote}`});
      for (const e of unplacedHere.slice(0, 5)) rows.push(expRow(e, A));
      if (unplacedHere.length > 5) rows.push({isRow: true, kind: 'unplaced', id: 'all', label: `All ${fmt(unplacedHere.length)} in no ${word('theme')}`, sub: `one scale down: every ${word('exp')} no ${word('theme')} holds`, height: 44, marks: [], bar: null, right: '›', canOpen: true,
        onClick: () => open({kind: 'unplaced', person}), onOpen: () => open({kind: 'unplaced', person})});
    }
    const years = lensed() ? yearsOf(exps) : M.years;
    const span = years.length ? `, ${years[0]}–${years[years.length - 1]}` : '';
    const title = `${person ? person : `The ${word('project')}`}${span}`;
    const soft = themes.filter(softTheme).length;
    return {...none, title,
      hint: `Each mark is ${article(word('exp'))} ${word('exp')} at its date, one ${word('exp')} high. Its color shows how much of its raw data was analyzed. ${countOf(themes.length, 'theme')} over ${countOf(exps.length, 'exp')}.${soft ? ` ${countOf(soft, 'theme')} ${soft === 1 ? 'has' : 'have'} an uncertain boundary (dotted separator).` : ''}`,
      ticks: timeTicks(A, (y) => select({kind: 'year', id: y})), rows, rightHead: 'Raw files analyzed', legend: LEGEND_C,
      keys: KEYS_ROWS, A};
  }

  /* An area's rows are its experiments. The scales run from areas to experiments to one
   * experiment's story. Each theme heads its experiments, and a heading opens the theme's own
   * scale. */
  function areaRows(area) {
    const none = {title: '', hint: '', ticks: [], rows: [], rightHead: '', note: '', legend: [], keys: ''};
    const exps = M.exps.filter((e) => e.person === area).filter(inLens);
    const sp = spanOf(exps) || {t0: Date.UTC(2020, 0, 1), t1: Date.UTC(2021, 0, 1)};
    const A = axisFor(sp.t0, sp.t1);
    const rows = [];
    if (M.themesComputed) {
      for (const t of M.themes) {
        const here = exps.filter((e) => e.theme === t.theme);
        if (!here.length) continue;
        rows.push({isHead: true, label: themeShown(t), sub: countOf(here.length, 'exp'), onClick: () => open({kind: 'theme', id: t.theme, person: area})});
        for (const e of here) rows.push(expRow(e, A));
      }
      const unplacedHere = exps.filter((e) => e.theme == null || !M.themeById.has(e.theme));
      if (unplacedHere.length) {
        rows.push({isHead: true, label: `In no ${word('theme')}`, sub: countOf(unplacedHere.length, 'exp')});
        for (const e of unplacedHere) rows.push(expRow(e, A));
      }
    } else for (const e of exps) rows.push(expRow(e, A));
    const years = yearsOf(exps);
    const span = years.length ? `, ${years[0]}–${years[years.length - 1]}` : '';
    const undated = exps.filter((e) => Number.isNaN(e.at)).length;
    // Where themes cannot be drawn, the rows say why, in the words the project's rows use.
    const hint = M.themesComputed
      ? `A row per ${word('exp')}, under its ${word('theme')}. The mark is the ${word('exp')}'s date. The line goes to the last time a file in it was written. ${countOf(exps.length, 'exp')}.${undated ? ` ${countOf(undated, 'exp')} ${undated === 1 ? 'is' : 'are'} undated and drawn with no mark.` : ''}`
      : `Rows are the ${word('exp', true)} found, dated from their names where a name gives a date. ${cap(word('theme', true))} are not drawn: ${endSentence(M.themesSaid || 'themes were not computed')}`;
    return {...none, title: `${area}${span}`, hint,
      ticks: timeTicks(A, (y) => select({kind: 'year', id: y})), rows, rightHead: 'Raw files analyzed', legend: LEGEND_C, keys: KEYS_ROWS, A};
  }

  /* ------------------------------------------------------------------ the design grid */

  /* One experiment's design (GET /api/v1/experiments/design): the parts of its file
   * names that are conditions as factors, each combination of levels as a cell. Shading is the
   * replicate count, hatching is a combination not found, an outline is an empty output. */
  async function loadDesign(expId) {
    if (designCache.has(expId)) return designCache.get(expId);
    const answer = await v1(`/api/v1/experiments/design?${new URLSearchParams({experiment: expId})}`);
    designCache.set(expId, answer);
    return answer;
  }

  /* A factor's name, with its place in the file names where two factors would read alike. */
  function factorLabels(factors) {
    const same = new Map();
    for (const f of factors) same.set(f.name, (same.get(f.name) || 0) + 1);
    return new Map(factors.map((f) => [f.id, same.get(f.name) > 1 ? `${f.name} (part ${f.positions[0]})` : f.name]));
  }

  function designRows(d) {
    // Rows are every combination of the factors but the last; columns are the last factor's levels.
    const last = d.factors.length - 1;
    const keys = new Map();
    for (const c of d.cells) {
      const rowKey = c.levels.slice(0, last).join(',');
      if (!keys.has(rowKey)) keys.set(rowKey, c.levels.slice(0, last));
    }
    return [...keys.entries()].sort((a, b) => {
      for (let i = 0; i < a[1].length; i += 1) if (a[1][i] !== b[1][i]) return a[1][i] - b[1][i];
      return 0;
    }).map(([key, levels]) => ({key, levels}));
  }

  function designGrid(d) {
    const last = d.factors.length - 1;
    const columns = d.factors[last].levels;
    const rows = last === 0 ? [{key: '', levels: []}] : designRows(d);
    const byCell = new Map(d.cells.map((c) => [c.levels.join(','), c]));
    const most = Math.max(1, ...d.cells.map((c) => c.replicates));
    const rowHead = (r) => r.levels.map((level, i) => d.factors[i].levels[level].shown).join(' · ');
    const body = rows.map((r) => node('tr', {}, [
      last === 0 ? '' : node('th', {scope: 'row', class: 'dhead', text: rowHead(r)}),
      ...columns.map((_, column) => {
        const cell = byCell.get([...r.levels, column].join(','));
        if (!cell) return node('td', {class: 'dcell dnone', 'aria-label': 'not a combination of this design'}, ['']);
        const label = [last === 0 ? '' : rowHead(r), columns[column].shown].filter(Boolean).join(' · ');
        const stages = cell.stages.map((s) => `${plural(s.files, 'file')} ${s.stage === 'unstated' ? 'at no stage stated' : 'at ' + stageWord(s.stage)}`).join(', ');
        const said = cell.state === 'not found'
          ? `${label}: not found in the file names read`
          : `${label}: ${plural(cell.replicates, 'replicate')}, ${plural(cell.files, 'file')}${stages ? ` (${stages})` : ''}${cell.state === 'empty output' ? '; the output found is empty' : ''}`;
        const state = cell.state === 'not found' ? 'dnotfound' : cell.state === 'empty output' ? 'dempty' : 'dfull';
        return node('td', {class: `dcell ${state}`, 'data-state': cell.state, 'data-replicates': String(cell.replicates), title: said, 'aria-label': said,
          style: cell.state === 'not found' ? '' : `--d-share:${(0.18 + 0.82 * cell.replicates / most).toFixed(3)}`}, [cell.state === 'not found' ? '' : node('span', {class: 'dnum', text: String(cell.replicates)})]);
      }),
    ]));
    const labels = factorLabels(d.factors);
    const corner = d.factors.slice(0, last).map((f) => labels.get(f.id)).join(' · ');
    return node('table', {class: 'dgrid', 'data-help': 'years.design_grid'}, [
      node('thead', {}, [node('tr', {}, [last === 0 ? '' : node('th', {class: 'dhead dcorner', scope: 'col', text: corner}), ...columns.map((l) => node('th', {scope: 'col', class: 'dhead', text: l.shown}))])]),
      node('tbody', {}, body),
    ]);
  }

  function designPanel(d) {
    if (d.error) return node('p', {class: 'ynote', text: `Its design could not be read: ${d.error}`});
    if (!d.enabled) return node('p', {class: 'ynote', text: 'The design grid is off by default while it is measured. Turn it on in the settings (design.enabled) to see a grid of the conditions an experiment\'s names vary.'});
    const x = d.experiment;
    const parts = [];
    const leftOut = x.coverage.left_out.length ? ` Left out: ${x.coverage.left_out.map((l) => `${fmt(l.files)}, ${l.said}`).join('; ')}.` : '';
    if (!x.has_grid) {
      parts.push(node('p', {class: 'ynote', text: `No grid is drawn: ${x.reason}. ${plural(x.coverage.files, 'file')} found in it.${leftOut}`}));
      return node('div', {class: 'ydesign', 'data-grid': 'none'}, parts);
    }
    parts.push(node('p', {class: 'yhint', text: `${x.said} The names read as ${x.pattern || 'no single pattern'}.${leftOut}`}));
    parts.push(designGrid(x));
    parts.push(node('div', {class: 'ylegend dlegend'}, [
      node('span', {}, [node('span', {class: 'dsw dfull'}), 'replicates found (darker is more)']),
      node('span', {}, [node('span', {class: 'dsw dnotfound'}), 'combination not found']),
      node('span', {}, [node('span', {class: 'dsw dempty'}), 'the output found is empty']),
    ]));
    const facts = [];
    const labels = factorLabels(x.factors);
    for (const f of x.factors) {
      const levels = f.levels.map((l) => `${l.shown} (${l.files})`).join(', ');
      const where = f.positions.length > 1 ? `parts ${f.positions.join(' and ')} of the name` : `part ${f.positions[0]} of the name`;
      facts.push(node('li', {}, [node('strong', {text: labels.get(f.id)}), ` (${where}, ${f.order === 'numeric' ? 'in numeric order' : 'in alphabetical order'}): ${levels}.`,
        f.ladder ? ` ${f.ladder.said}.` : '',
        ...f.merged.map((m) => ` Part ${m.position} (${m.name}) goes with it: ${m.said}.`)]));
    }
    parts.push(node('h3', {text: 'Factors: what the file names vary'}));
    parts.push(node('ul', {class: 'dfacts'}, facts));
    if (x.pairs.length) {
      const names = labels;
      parts.push(node('ul', {class: 'dfacts'}, x.pairs.map((p) => node('li', {}, [`${names.get(p.first)} and ${names.get(p.second)}: `, node('strong', {text: p.relation}), `, ${p.said}.`]))));
    }
    parts.push(node('p', {class: 'ynote', text: 'A factor is a part of the file names that was varied between files, such as a drug or a time. Crossed: every combination of two factors was found. Nested: each level of one lies under one level of the other, as doses belong to one drug. Partly crossed: some combinations of the two were not found.'}));
    if (x.replicate_positions.length) parts.push(node('p', {class: 'ynote', text: `Repeats are counted by part ${x.replicate_positions.join(' and ')} of the name.`}));
    if (x.other_layouts.length) parts.push(node('p', {class: 'ynote', text: `Left out, laid out unlike the grid: ${x.other_layouts.map((l) => `${plural(l.files, 'file')} written ${l.pattern}`).join('; ')}.`}));
    if (x.not_found_said) parts.push(node('p', {class: 'ynote', text: `Combinations not listed: ${x.not_found_said}.`}));
    for (const finding of x.findings) parts.push(node('p', {class: 'dfinding', role: 'note', text: finding.question}));
    return node('div', {class: 'ydesign', 'data-grid': 'drawn'}, parts);
  }

  /* ------------------------------------------------------------------ the file scale */

  async function loadFiles(expId) {
    if (filesCache.has(expId)) return filesCache.get(expId);
    const limit = 500; let offset = 0; let header = null; let rows = []; let acquisitions = []; let notes = []; let total = 0; let busy = null;
    for (;;) {
      const page = await v1(`/api/v1/experiments/files?${new URLSearchParams({experiment: expId, offset: String(offset), limit: String(limit)})}`);
      header = page.experiment; total = page.files; notes = page.notes; busy = page.busy_days; acquisitions = acquisitions.concat(page.acquisitions || []);
      rows = rows.concat(page.rows);
      offset += page.rows.length;
      if (!page.rows.length || offset >= total || offset >= 20000) break;
    }
    const F = rows.map((f, i) => {
      const modified = f.clocks.find((c) => c.source === 'modified');
      const at = f.believed ? ms(f.believed.at) : (modified ? ms(modified.at) : NaN);
      const parent = f.path.slice(0, Math.max(0, f.path.lastIndexOf('/')));
      const stage = f.stage.length ? f.stage[0].value : 'unstated';
      const format = f.format.length ? f.format[0].value : '';
      return {...f, i, at, parent, stage, format, kind: kindWord(format), conflicting: [...f.format, ...f.stage, ...f.role].some((v) => v.conflicting)};
    });
    const held = {experiment: header, total, F, acquisitions, notes, busy, shownAll: offset >= total};
    filesCache.set(expId, held);
    return held;
  }
  const KIND_WORDS = {image_format: 'image', tabular_format: 'table', plain_text: 'text', portable_document: 'PDF', imagej_macro: 'ImageJ macro',
    embedded_database: 'database', structured_format: 'structured data', xml_document: 'XML', log_file: 'log', document: 'document', code: 'code'};
  function kindWord(format) { if (!format) return 'other'; return KIND_WORDS[format] || String(format).replace(/_format$/, '').replace(/_/g, ' '); }
  const STAGE_WORDS = {acquisition: 'raw acquisition', raw: 'raw', analysis: 'analysis', summary: 'summary', pooled: 'pooled', template: 'template', unstated: 'stage not stated'};
  const stageWord = (s) => STAGE_WORDS[s] || String(s).replace(/_/g, ' ');

  /* The busiest days of an experiment's files are the engine's (``busy_days`` on the files route,
   * the same on every page): the window the file scale opens on, drawn from its first and last
   * day. The page works out only where on its axis those days stand. */
  function busyWindow(busy) {
    if (!busy) return null;
    return {t0: ms(busy.first_day) - DAY / 2, t1: ms(busy.last_day) + DAY + DAY / 2, share: busy.percent, text: busy.said, n: busy.files};
  }

  function filesRows(node) {
    const none = {title: '', hint: '', ticks: [], rows: [], rightHead: '', note: '', legend: [], keys: ''};
    const e = M.byId.get(node.id);
    if (Y.groupBy === 'story') {
      return {...none, title: e ? e.shown : cap(`${article(word('exp'))} ${word('exp')}`),
        hint: `What happened in this ${word('exp')}, day by day. To list its files, choose folders, stages, kinds or files one by one.`,
        story: true, fileBar: {count: '', busy: null}, keys: KEYS_STORY};
    }
    if (Y.groupBy === 'design') {
      const shown = designCache.get(node.id);
      const title = e ? e.shown : cap(`${article(word('exp'))} ${word('exp')}`);
      if (!shown) {
        loadDesign(node.id).then(() => { if (scale().kind === 'exp' && scale().id === node.id) render(); })
          .catch((error) => { designCache.set(node.id, {error: String(error.message || error)}); render(); });
        return {...none, title, hint: 'Reading its design...', design: {loading: true}, fileBar: {count: '', busy: null}};
      }
      return {...none, title, hint: 'The design grid reads the parts of its file names that are conditions; it is drawn from the names alone.',
        design: shown, fileBar: {count: '', busy: null}, keys: 'Backspace closes'};
    }
    const held = filesCache.get(node.id);
    if (!held) {
      loadFiles(node.id).then(() => { if (scale().kind === 'exp' && scale().id === node.id) render(); }).catch((error) => { filesCache.set(node.id, {error: String(error.message || error), F: [], total: 0, acquisitions: [], notes: []}); render(); });
      return {...none, title: e ? e.shown : cap(`${article(word('exp'))} ${word('exp')}`), hint: 'Reading its files...', rows: [], loading: true};
    }
    if (held.error) return {...none, title: e ? e.shown : cap(`${article(word('exp'))} ${word('exp')}`), hint: `Its files could not be read: ${held.error}`, rows: []};
    const F = held.F;
    const q = Y.filter.trim().toLowerCase();
    const matched = q ? F.filter((f) => { const twin = twinMember(q, f); return twin !== null ? twin : (f.path.toLowerCase().includes(q) || f.kind.includes(q) || stageWord(f.stage).includes(q) || (f.role || []).some((r) => String(r.value).toLowerCase().includes(q))); }) : F;
    const dated = F.filter((f) => !Number.isNaN(f.at));
    const ats = dated.map((f) => f.at);
    const t0 = ats.length ? Math.min(...ats) : (e && !Number.isNaN(e.at) ? e.at : Date.now() - 30 * DAY);
    const t1 = ats.length ? Math.max(...ats) : t0 + DAY;
    const pad = Math.max(DAY / 2, (t1 - t0) * 0.03);
    const busy = busyWindow(held.busy);
    const A = axisFor(t0 - pad, t1 + pad, busy ? {t0: busy.t0, t1: busy.t1} : null);
    const groupBy = Y.groupBy;
    let rows;
    const binsFor = (fs, cls) => {
      const times = fs.map((f) => f.at); const max = Math.max(1, ...bins(times, A, 60).map((b) => b.n));
      return bins(times, A, 60).map((b) => ({bin: true, left: A.x(b.t), width: A.w(b.t, b.tn), height: `${Math.max(3, Math.round(30 * b.n / max))}px`, classes: ['ybin', cls || ''], title: `${plural(b.n, 'file')} · ${dayWords(b.t)}`}));
    };
    if (groupBy === 'list') {
      rows = matched.map((f) => fileRow(f, A, node.id));
    } else {
      const keyOf = groupBy === 'stage' ? (f) => stageWord(f.stage) : groupBy === 'kind' ? (f) => f.kind : (f) => (f.parent === (e ? e.folder : '') ? `(the ${word('exp')} folder itself)` : f.parent.replace(`${e ? e.folder : ''}/`, ''));
      const groups = new Map();
      for (const f of matched) { const k = keyOf(f); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(f); }
      const order = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
      rows = order.map(([k, fs]) => {
        const sel = isSel('fgroup', k);
        const raw = fs.filter((f) => f.stage === 'acquisition' || f.stage === 'raw').length;
        const made = fs.filter((f) => f.derivations.some((d) => d.direction === 'made_from')).length;
        const kinds = {}; for (const f of fs) kinds[f.kind] = (kinds[f.kind] || 0) + 1;
        const kindText = Object.entries(kinds).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([kk, n]) => `${n} ${kk}`).join(', ');
        return {isRow: true, kind: 'fgroup', id: k, label: k, mono: groupBy === 'folder', sub: `${plural(fs.length, 'file')} · ${kindText}${raw ? ` · ${raw} raw` : ''}${made ? ` · ${made} made from another file` : ''}`,
          height: 48, selected: sel, marks: binsFor(fs), bar: null, right: `${plural(fs.length, 'file')}`, canOpen: false,
          onClick: () => select({kind: 'fgroup', id: k, files: fs.map((f) => f.i), exp: node.id})};
      });
    }
    const span = ats.length ? `${dayWords(t0)} to ${dayWords(t1)}` : 'no file is dated';
    const defaulted = A.defaulted;
    return {...none, title: `${e ? e.shown : held.experiment.name}, ${e && e.date ? dayWords(e.date) : ''}`,
      hint: `${plural(held.total, 'file')}${held.shownAll ? '' : ` (the first ${fmt(F.length)} read)`}; files written ${span}. Rows are ${groupBy === 'list' ? 'files, one by one' : groupBy === 'folder' ? 'folders' : groupBy === 'stage' ? 'stages' : 'kinds of file'}; bar height is files per time bin at their believed time.${busy && defaulted ? ` Opened on ${busy.text}, which holds ${busy.share}% of the files. You can choose the whole span.` : ''}`,
      ticks: timeTicks(A, null), rows, rightHead: 'Files', note: held.notes && held.notes.length ? held.notes.join(' ') : '',
      legend: [['plain', 'files per time bin'], ['human', 'a person said so']], keys: Y.groupBy === 'list' ? KEYS_FILES : KEYS_ROWS, A,
      fileBar: {count: q ? `${fmt(matched.length)} of ${fmt(F.length)} files` : `${fmt(F.length)} files`, busy, defaulted}};
  }
  function fileRow(f, A, expId) {
    const sel = isSel('file', f.id);
    const made = f.derivations.filter((d) => d.direction === 'made_from');
    const into = f.derivations.filter((d) => d.direction === 'made_into');
    const cls = f.stage === 'acquisition' || f.stage === 'raw' ? 'ink' : 'plain';
    const classes = ['ymark', cls]; if (f.conflicting) classes.push('gap'); if (sel) classes.push('selected');
    return {isRow: true, kind: 'file', id: f.id, label: f.name, mono: true,
      sub: `${f.believed ? dayWords(f.believed.at) : (Number.isNaN(f.at) ? 'undated' : dayWords(f.at))} · ${stageWord(f.stage)} · ${f.kind}${made.length ? ` · made from ${made.length === 1 ? (made[0].other_name || 'a file') : plural(made.length, 'file')}` : ''}${into.length ? ` · ${plural(into.length, 'file')} made from it` : ''}${f.copies.length ? ` · ${f.copies[0].kind}` : ''}`,
      height: 44, selected: sel, problem: f.conflicting,
      marks: Number.isNaN(f.at) ? [] : [{left: A.x(f.at), top: '11px', classes, title: `${f.name} · ${f.believed_said}`, op: 1, onClick: () => select({kind: 'file', id: f.id, exp: expId, name: f.name})}],
      bar: null, right: f.size_bytes == null ? '' : bytesWords(f.size_bytes), canOpen: true,
      onClick: () => select({kind: 'file', id: f.id, exp: expId, name: f.name}), onOpen: () => open({kind: 'file', id: f.id, exp: expId, name: f.name})};
  }
  const bytesWords = (b) => b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${(b / 1e3).toFixed(0)} KB` : `${b} B`;

  /* ------------------------------------------------------------------ drawing the page */

  const node = (tag, attributes, children) => el(tag, attributes, children);

  function crumbs() {
    const parts = [];
    Y.path.forEach((n, i) => {
      const last = i === Y.path.length - 1 && Y.view === 'rows';
      parts.push(node('button', {type: 'button', 'aria-current': last ? 'location' : 'false', title: nodeName(n),
        class: n.kind === 'file' ? 'mono' : '', onclick: () => go(Y.path.slice(0, i + 1))}, [nodeName(n)]));
      if (!last) parts.push(node('span', {class: 'ysep', 'aria-hidden': 'true'}, ['›']));
    });
    if (Y.view !== 'rows') {
      const names = {wheel: 'The Wheel', overlap: 'Shared words', year: nodeName({kind: 'year', id: Y.sel && Y.sel.kind === 'year' ? Y.sel.id : ''})};
      parts.push(node('button', {type: 'button', 'aria-current': 'location', onclick: () => {}}, [names[Y.view] || Y.view]));
    }
    return node('nav', {class: 'ycrumbs', 'aria-label': 'Where you are'}, parts);
  }

  function chip(text, on, onClick, extra = {}) {
    return node('button', {type: 'button', class: `ychip${on ? ' on' : ''}${extra.human ? ' human' : ''}`, 'data-help': extra.help || 'years.chip', onclick: onClick, title: extra.title || ''}, [text]);
  }

  /* The topic lens (the select and the clear chip) and the pick chip, for the scales whose rows are
   * people, lines of work or experiments. Nothing is drawn until the person has made a topic. */
  function topicControls(R) {
    if (!window.topicKit) return [];
    const reading = topicKit.current();
    const parts = [];
    if (reading && reading.topics.length) {
      const select = node('select', {class: 'ylens', 'aria-label': 'Show only the experiments of a collection', 'data-help': 'years.lens'}, [
        node('option', {value: ''}, ['every experiment']),
        ...reading.topics.map((t) => node('option', {value: t.id}, [`${t.name} (${t.count == null ? 'not counted' : fmt(t.count)})`])),
      ]);
      select.value = lensed() ? lens : '';
      select.addEventListener('change', () => setLens(select.value));
      parts.push(node('span', {text: 'Collection'}), select);
      if (lensed()) parts.push(chip('Clear the collection', true, () => setLens(''), {human: true, help: 'years.lens_clear', title: 'Show every experiment again'}));
    }
    if (R && R.rows && R.rows.some((r) => r.pick)) {
      parts.push(chip('Pick for a collection', !!Y.picking, () => { Y.picking = !Y.picking; render(); }, {help: 'years.pick', title: 'Tick themes and experiments, then add them to a collection. Shift-click picks a row at any time.'}));
    }
    return parts;
  }

  function toolbar(R) {
    const kind = scale().kind;
    const parts = [];
    if (['project', 'person', 'theme'].includes(kind) && M.themesComputed) {
      parts.push(node('span', {text: 'Color by'}));
      parts.push(chip('raw data analyzed', Y.measure === 'analyzed', () => { Y.measure = 'analyzed'; render(); }, {help: 'years.measure'}));
      parts.push(chip('a person said so', Y.measure === 'provenance', () => { Y.measure = 'provenance'; render(); }, {help: 'years.measure'}));
      parts.push(node('span', {class: 'ygap'}));
      if (kind !== 'theme') parts.push(chip('Wheel (W)', Y.view === 'wheel', () => toggleView('wheel'), {help: 'years.wheel'}));
    }
    if (['project', 'person', 'theme', 'unplaced'].includes(kind) && Y.view === 'rows') {
      const mine = topicControls(R);
      if (mine.length) {
        if (parts.length) parts.push(node('span', {class: 'ygap'}));
        parts.push(...mine);
      }
    }
    if (kind === 'exp' && R.fileBar) {
      // An experiment opens on its story. These chips list its files.
      parts.push(chip('Story', Y.groupBy === 'story', () => { Y.groupBy = 'story'; Y.sel = null; Y.focusRow = -1; render(); }, {help: 'years.story'}));
      parts.push(node('span', {class: 'ygap'}));
      parts.push(node('span', {text: 'Rows are'}));
      for (const [k, t] of [['folder', 'folders'], ['stage', 'stages'], ['kind', 'kinds'], ['list', 'files, one by one']]) {
        parts.push(chip(t, Y.groupBy === k, () => { Y.groupBy = k; Y.sel = null; Y.focusRow = -1; render(); }, {help: 'years.rows_are'}));
      }
      parts.push(node('span', {class: 'ygap'}));
      parts.push(node('span', {text: 'Or draw its'}));
      parts.push(chip('design grid', Y.groupBy === 'design', () => { Y.groupBy = 'design'; Y.sel = null; Y.focusRow = -1; render(); }, {help: 'years.design'}));
      if (Y.groupBy !== 'design' && Y.groupBy !== 'story') {
        const input = node('input', {type: 'search', value: Y.filter, placeholder: 'filter by name, folder, stage or kind', 'aria-label': 'Filter the files', 'data-help': 'years.filter'});
        input.addEventListener('input', () => { Y.filter = input.value; renderList(); });
        parts.push(input);
      }
      parts.push(node('span', {class: 'ycount', text: R.fileBar.count}));
      if (R.fileBar.busy) {
        const b = R.fileBar.busy; const on = R.fileBar.defaulted || (Y.zoom && Y.zoom.key === zoomKey() && Y.zoom.t0 === b.t0);
        parts.push(chip(on ? `Show the whole span · ${b.text} holds ${b.share}% of files` : `Zoom to ${b.text} · ${b.share}% of files`, !!on, () => {
          if (on) showWholeSpan(); else { Y.zoom = {key: zoomKey(), t0: b.t0, t1: b.t1}; delete Y.wholeSpan[zoomKey()]; render(); }
        }, {help: 'years.busy'}));
      }
    }
    // Below 1024 pixels the inspector is a drawer: a chip at the bar's right opens it (as Enter on a
    // row does), and the Wheel and Overlap chips above open sheets over the rows.
    if (NARROW.matches && !PHONE.matches) {
      if (!parts.some((part) => part.classList && part.classList.contains('ygap'))) parts.push(node('span', {class: 'ygap'}));
      parts.push(node('button', {type: 'button', class: `ychip details${drawerIsOpen() ? ' on' : ''}`, 'data-help': 'years.details', 'aria-expanded': drawerIsOpen() ? 'true' : 'false', 'aria-controls': 'detail',
        onclick: () => openDrawer()}, ['Details ›']));
    }
    return parts.length ? node('div', {class: 'ybar'}, parts) : null;
  }

  function axisHead(R) {
    const A = R.A;
    // The span the axis shows, in milliseconds, for a reader or a check that cannot see the ticks.
    const shown = A ? {'data-start': String(Math.round(A.t0)), 'data-end': String(Math.round(A.t1))} : {};
    const ticks = node('div', {class: 'yticks', 'data-zoom': 'time', 'data-help': 'years.axis', ...shown}, (R.ticks || []).map((t) => {
      const chosen = Y.sel && Y.sel.kind === 'year' && Y.sel.id === t.year;
      return node(t.onYear ? 'button' : 'span', {...(t.onYear ? {type: 'button', onclick: t.onYear} : {}), class: `ytick${t.onYear ? ' clickable' : ''}${chosen ? ' chosen' : ''}`,
        style: `left:${t.left.toFixed(3)}%;width:${t.width.toFixed(3)}%`, title: t.year != null ? `What was I working on in ${t.year}?` : ''}, [t.label]);
    }));
    ticks.addEventListener('wheel', onWheel, {passive: false});
    let drag = null;
    ticks.addEventListener('mousedown', (ev) => { if (ev.button !== 0 || !A) return; drag = {x: ev.clientX, A, width: ticks.getBoundingClientRect().width}; });
    window.addEventListener('mousemove', (ev) => {
      if (!drag) return;
      const dx = -(ev.clientX - drag.x) / Math.max(1, drag.width) * drag.A.span;
      Y.zoom = {key: zoomKey(), t0: drag.A.t0 + dx, t1: drag.A.t1 + dx}; drag.x = ev.clientX; drag.A = {...drag.A, t0: Y.zoom.t0, t1: Y.zoom.t1};
      renderList();
    });
    window.addEventListener('mouseup', () => { drag = null; });
    const left = node('div', {class: 'yaxisname'}, [A && A.zoomed ? node('button', {type: 'button', class: 'yreset', 'data-help': 'years.reset', onclick: () => { Y.zoom = null; delete Y.wholeSpan[zoomKey()]; render(); }}, ['Reset zoom']) : '']);
    return node('div', {class: 'yaxis'}, [left, ticks, node('div', {class: 'yrighthead', text: R.rightHead || ''})]);
  }

  /* The wheel on the axis or a row's time cell. Ctrl + wheel zooms around the pointer:
   * a trackpad pinch arrives that way, and a mouse user holds Ctrl. A sideways wheel, or Shift +
   * wheel, pans. Any other wheel is left to the page, so a two-finger scroll moves past the rows
   * (the listeners are not passive, so the page is held still only when the axis moves). */
  function onWheel(ev) {
    const A = lastAxis; if (!A || Y.view !== 'rows') return;
    const sideways = Math.abs(ev.deltaX) > Math.abs(ev.deltaY);
    if (!ev.ctrlKey && !ev.shiftKey && !sideways) return;
    const cell = ev.currentTarget; const rect = cell.getBoundingClientRect();
    ev.preventDefault();
    const f = Math.max(0, Math.min(1, (ev.clientX - rect.left) / Math.max(1, rect.width)));
    const fullSpan = A.full.t1 - A.full.t0; let t0 = A.t0; let t1 = A.t1;
    if (!ev.ctrlKey) {
      // Shift + wheel: some browsers turn the upright delta sideways, others leave it upright.
      const dx = (wheelPixels(sideways ? ev.deltaX : ev.deltaY, ev) / Math.max(1, rect.width)) * A.span; t0 += dx; t1 += dx;
    } else {
      // A notch of a mouse wheel zooms by about a quarter; a pinch comes in many small steps.
      const k = Math.exp(Math.max(-0.25, Math.min(0.25, wheelPixels(ev.deltaY, ev) * 0.01)));
      const ns = Math.max(4000, Math.min(A.span * k, fullSpan));
      if (ns >= fullSpan) { showWholeSpan(); return; }
      const at = A.t0 + f * A.span; t0 = at - f * ns; t1 = at + (1 - f) * ns;
    }
    Y.zoom = {key: zoomKey(), t0, t1};
    renderList();
  }
  /* A wheel delta in pixels, whatever unit the browser gave it in (lines or pages). */
  function wheelPixels(delta, ev) { return ev.deltaMode === 1 ? delta * 16 : ev.deltaMode === 2 ? delta * 800 : delta; }

  function rowNode(r, index) {
    if (r.isRow && r.k != null) index = r.k;
    if (r.isHead) {
      return node('div', {class: 'yhead-row', ...(r.onClick ? {onclick: r.onClick} : {})}, [node('strong', {text: r.label}), node('span', {text: r.sub || ''})]);
    }
    if (r.spacer) return node('div', {class: 'yspacer', style: `height:${r.height}px`});
    const classes = ['yrow']; if (r.selected) classes.push('selected'); if (r.dim) classes.push('dim'); if (r.soft) classes.push('soft'); if (index === Y.focusRow) classes.push('focused');
    const chips = r.topicIds && window.topicKit ? topicKit.chipsFor(r.topicIds) : null;
    const box = r.pick && Y.picking && window.topicKit
      ? node('input', {type: 'checkbox', class: 'ypickbox', 'aria-label': `Pick ${r.label} for a collection`, 'data-help': 'years.pick_row', onclick: (ev) => ev.stopPropagation(), onchange: () => togglePick(r.pick)})
      : null;
    if (box) box.checked = picked.has(r.pick.key);
    const name = node('div', {class: 'yname'}, [
      node('div', {class: `ylabel${r.labelHuman ? ' human' : ''}${r.mono ? ' mono' : ''}`, title: r.label}, [box, r.human ? node('span', {class: 'ydot human'}) : (r.problem ? node('span', {class: 'ydot problem'}) : ''), r.label]),
      r.sub ? node('div', {class: `ysub${r.subHuman ? ' human' : ''}`, title: r.sub, text: r.sub}) : '',
      chips ? node('div', {class: 'ychips'}, [chips]) : '',
    ]);
    if (r.pick && picked.has(r.pick.key)) classes.push('topic-picked');
    const marks = (r.marks || []).map((m) => {
      if (m.bin) return node('span', {class: m.classes.join(' '), style: `left:${m.left};width:${m.width};height:${m.height}`, title: m.title});
      const mark = node('span', {class: m.classes.join(' '), style: `left:${m.left};top:${m.top};opacity:${m.op ?? 1}`, title: m.title});
      if (m.onClick) mark.addEventListener('click', (ev) => { ev.stopPropagation(); m.onClick(); });
      return mark;
    });
    const spans = (r.spans || []).map((s) => node('span', {class: 'yspan', style: `left:${s.left};width:${s.width};top:${s.top || '48px'};height:${s.height || '2px'}`}));
    const time = node('div', {class: 'ytime', 'data-zoom': 'time'}, [...spans, ...marks]);
    time.addEventListener('wheel', onWheel, {passive: false});
    let right;
    if (r.bar) {
      const b = r.bar;
      right = node('div', {class: 'ybarbox'}, [
        node('span', {class: `ybartrack${b.na ? ' na' : ''}`}, [b.na ? '' : node('span', {class: 'ybarfill', style: `width:${b.pct}%`})]),
        node('span', {class: `ybartext ${b.rightClass || ''}`, text: b.text}),
      ]);
      right = node('div', {class: 'yright'}, [right, b.right ? node('span', {class: `ybartext ${b.rightClass || ''}`, text: b.right}) : '']);
    } else right = node('div', {class: 'yright'}, [node('span', {text: r.right || ''})]);
    // The rows are one tab stop (the one with the ring, or the first); the arrow keys move inside.
    const stop = index === (Y.focusRow >= 0 ? Y.focusRow : 0);
    const row = node('div', {class: classes.join(' '), role: 'listitem', tabindex: stop ? '0' : '-1', style: `min-height:${r.height || 44}px`, 'data-index': String(index), ...(r.pick ? {'data-pick': r.pick.key} : {})}, [name, time, right, r.canOpen ? node('span', {class: 'yopen', 'aria-hidden': 'true', text: '›'}) : '']);
    row.addEventListener('click', (ev) => {
      Y.focusRow = index; Y.keyFocus = false;
      // Picking rows for a topic: a click while picking, or a shift-click at any time, picks the row.
      if (r.pick && window.topicKit && (Y.picking || ev.shiftKey)) { togglePick(r.pick); return; }
      r.onClick && r.onClick();
    });
    row.addEventListener('dblclick', () => { r.onOpen && r.onOpen(); });
    return row;
  }

  /* Long lists are windowed: only the rows around the viewport exist. Two spacers hold the rest of
   * the height. The pitch is measured from a drawn row. */
  let rowPitch = 48;
  function listNodes(rows) {
    // A row's place among the rows (a heading is no row), which the keys and the ring read.
    let k = 0; for (const r of rows) if (r.isRow) r.k = k++;
    const N = rows.length;
    if (N <= 150 || rows.some((r) => r.isHead)) return rows.map((r, i) => rowNode(r, i));
    const px = rowPitch; const over = 10; const top = Y.listScroll || 0; const vh = 700;
    const start = Math.max(0, Math.floor(top / px) - over); const end = Math.min(N, Math.ceil((top + vh) / px) + over);
    const out = rows.slice(start, end).map((r, j) => rowNode(r, start + j));
    if (start > 0) out.unshift(rowNode({spacer: true, height: start * px}, -1));
    if (end < N) out.push(rowNode({spacer: true, height: (N - end) * px}, -1));
    return out;
  }

  function gridLines(R) {
    return (R.ticks || []).filter((t) => t.label).slice(1).map((t) => node('span', {class: 'ygridline', style: `left:calc(var(--y-name) + ${t.left.toFixed(3)}% * (1 - (var(--y-name) + var(--y-right)) / 100%) )`}));
  }

  let listEl = null;
  function renderList() {
    const R = rowsFor(); lastRows = R.rows.filter((r) => r.isRow);
    const holder = listPane.querySelector('.ylistbox');
    if (!holder) { render(); return; }
    const scrolled = listEl ? listEl.scrollTop : 0;
    const axis = axisHead(R);
    const list = node('div', {class: 'ylist', role: 'list', 'aria-label': R.title}, listNodes(R.rows));
    list.addEventListener('scroll', () => { Y.listScroll = list.scrollTop; if (lastRows.length > 150) { if (!list._raf) list._raf = requestAnimationFrame(() => { list._raf = null; renderList(); }); } });
    holder.replaceChildren(axis, list);
    listEl = list; list.scrollTop = scrolled;
    const first = list.querySelector('.yrow'); if (first) { const h = Math.round(first.getBoundingClientRect().height); if (h >= 24) rowPitch = h; }
    const bar = listPane.querySelector('.ybar');
    if (bar) {
      const fresh = toolbar(R);
      const field = bar.querySelector('input');
      if (fresh && field && document.activeElement === field) {
        // A person is typing in the filter: the bar stays (replacing it would take the focus), and
        // only the count beside it is brought up to date.
        const count = bar.querySelector('.ycount'); const next = fresh.querySelector('.ycount');
        if (count && next) count.textContent = next.textContent;
      } else if (fresh) bar.replaceWith(fresh);
    }
    keepKeyFocus();
  }
  /* A key moved to this row: after a redraw the row's new node takes the focus, so a person who is
   * walking the rows with the arrow keys never loses their place. */
  function keepKeyFocus() {
    if (!Y.keyFocus) return;
    // Only where a redraw took the focus (it is on the page itself) or it is on a row already.
    const held = document.activeElement;
    if (held && held !== document.body && held.closest && !held.closest('.ylist')) return;
    const row = listPane.querySelector(`.yrow[data-index="${Y.focusRow >= 0 ? Y.focusRow : 0}"]`);
    if (!row) return;
    row.focus({preventScroll: true});
    row.scrollIntoView({block: 'nearest'});
  }

  function legendNode(R) {
    const sw = (k) => node('span', {class: `ysw ymark ${k}`, style: 'position:static;width:11px;height:11px;top:auto'});
    return node('div', {class: 'ylegend'}, [...(R.legend || []).map(([k, text]) => node('span', {}, [sw(k), text])), R.keys ? node('span', {class: 'ykeys', text: R.keys}) : '']);
  }

  /* ------------------------------------------------------------------ narrow windows */

  /* Below 1024 pixels nothing is removed. Each part that does not fit opens from a chip.
   * From 600 to 1023 pixels, the inspector is a drawer. A "Details ›" chip at the bar's right opens
   * it, and so does Enter on a row. The Wheel (W) and Overlap (O) stay as chips that open
   * full-width sheets over the rows. Escape or Close shuts each one. Below 600 pixels, the page
   * shows the title, one sentence and a box that says what still works and links to it. */
  const NARROW = window.matchMedia('(max-width: 1023px)');
  const PHONE = window.matchMedia('(max-width: 599px)');
  const mainEl = () => document.querySelector('main');
  const headerBottom = () => { const header = document.querySelector('body > header'); return header ? Math.max(0, Math.round(header.getBoundingClientRect().bottom)) : 0; };
  function drawerIsOpen() { const main = mainEl(); return !!main && main.classList.contains('drawer-open'); }
  function openDrawer() {
    if (!NARROW.matches || PHONE.matches) return;
    const main = mainEl();
    main.classList.add('drawer-open');
    main.style.setProperty('--ydrawer-top', `${headerBottom()}px`);
    let close = main.querySelector('.ydrawer-close');
    if (!close) { close = node('button', {type: 'button', class: 'ydrawer-close', 'data-help': 'years.drawer_close', onclick: () => closeDrawer()}, ['Close']); main.append(close); }
    close.hidden = false;
    for (const chipNode of listPane.querySelectorAll('.ychip.details')) { chipNode.classList.add('on'); chipNode.setAttribute('aria-expanded', 'true'); }
    close.focus();
  }
  function closeDrawer({restore = true} = {}) {
    const main = mainEl();
    if (!drawerIsOpen()) return;
    main.classList.remove('drawer-open');
    const close = main.querySelector('.ydrawer-close'); if (close) close.hidden = true;
    for (const chipNode of listPane.querySelectorAll('.ychip.details')) { chipNode.classList.remove('on'); chipNode.setAttribute('aria-expanded', 'false'); }
    if (restore) { const back = listPane.querySelector('.ychip.details') || listPane.querySelector(`.yrow[data-index="${Y.focusRow}"]`); if (back) back.focus(); }
  }
  onLeave(() => closeDrawer({restore: false}));
  const onWidth = () => { if (!NARROW.matches) closeDrawer({restore: false}); if (shown() && M) render(); };
  NARROW.addEventListener('change', onWidth);
  PHONE.addEventListener('change', onWidth);

  /* The sheet a view opens over the rows in a narrow window: its own title, a Close button. */
  function sheet(kicker, title, content) {
    return node('div', {class: 'ysheet', role: 'dialog', 'aria-label': title, style: `top:${headerBottom()}px`}, [
      node('div', {class: 'ysheet-head'}, [node('div', {}, [node('p', {class: 'ykicker', text: kicker}), node('h2', {text: title})]),
        node('button', {type: 'button', class: 'ysheet-close', 'data-help': 'years.sheet_close', onclick: () => closeSheet()}, ['Close'])]),
      ...content]);
  }
  function closeSheet() {
    const was = Y.view;
    Y.view = 'rows'; render();
    const back = listPane.querySelector(`.ychip[data-help="years.${was}"]`); if (back) back.focus();
  }

  /* Below 600 pixels: what the project is in one sentence, and the two things that still work. */
  const NARROW_600 = 'The rows need a window at least 1024 px wide. On this screen you can search, read any card and answer questions.';
  function renderPhone() {
    lastRows = [];
    const years = M.years;
    const span = years.length ? `, ${years[0]} to ${years[years.length - 1]}` : '';
    const who = M.multiPerson ? ` in ${countOf(M.people.length, 'person')}` : '';
    const a = agg(M.exps);
    const said = `${countOf(M.exps.length, 'exp')}${who}${span}. ${a.raw ? `${fmt(a.followed)} of ${fmt(a.raw)} raw acquisitions found are followed by an analysis file found.` : 'No raw files were found.'}`;
    const lead = node('button', {type: 'button', class: 'link', 'data-help': 'years.narrow_questions', onclick: () => navigate('sheet')}, ['Questions']);
    v1('/api/v1/questions/ranked?limit=1').then((ranked) => {
      const n = ranked && ranked.summary ? ranked.summary.leading : 0;
      if (n) lead.textContent = `Questions · ${fmt(n)} lead`;
    }).catch(() => {});
    const search = node('button', {type: 'button', class: 'link', 'data-help': 'years.narrow_search', onclick: () => { const box = document.getElementById('search-box'); if (box) box.focus(); }}, [`Search ${word('exp', true)}`]);
    fill(host(), node('div', {class: 'years phone', 'data-years': 'phone'}, [
      node('div', {class: 'yhead'}, [node('h2', {text: years.length ? `The ${word('project')}, ${years[0]}–${years[years.length - 1]}` : `The ${word('project')}`})]),
      node('p', {class: 'yhint ycomplete', text: said}),
      node('div', {class: 'ynarrow notice-note', role: 'note'}, [node('p', {text: NARROW_600}), node('p', {class: 'ylinks'}, [search, ' · ', lead])]),
    ]));
  }

  /* ------------------------------------------------------------------ drawing the page, continued */

  function rowsParts(R) {
    const parts = [
      node('div', {class: 'yhead'}, [node('h2', {text: R.title})]),
      node('p', {class: 'yhint', text: R.hint}),
      toolbar(R) || '',
      node('div', {class: 'ypickholder'}),
    ];
    if (scale().kind === 'file') {
      parts.push(node('div', {class: 'yfile'}));
    } else if (R.story) {
      parts.push(node('div', {class: 'ystory', 'data-help': 'years.story'}));
    } else if (R.design) {
      parts.push(R.design.loading ? node('p', {class: 'empty state-loading', text: 'Reading its design...'}) : designPanel(R.design));
    } else {
      parts.push(node('div', {class: 'ylistbox'}));
    }
    if (!R.design && !R.story) parts.push(legendNode(R));
    if (R.note) parts.push(node('p', {class: 'ynote', text: R.note}));
    return parts;
  }
  function wheelParts(R, {inSheet = false} = {}) {
    const size = Math.min(720, Math.max(300, listPane.clientWidth - (inSheet ? 40 : 360) - 2 * LABEL_ROOM));
    return [
      inSheet ? '' : node('div', {class: 'yhead'}, [node('h2', {text: `The Wheel: ${nodeName(scale())}`})]),
      inSheet ? '' : (toolbar(R) || ''),
      lensed() ? node('p', {class: 'ynote', 'data-lens-note': 'wheel', text: `The Wheel draws every ${word('exp')}. The collection ${lensTopic().name} narrows only the rows. The band outside the Wheel shows where your collections lie.`}) : '',
      node('p', {class: 'yhint', text: `The current scale as rings: ${broadWord()}, ${word('theme', true)}, ${word('exp', true)}. Angle is how many ${word('exp', true)}, and color shows how much raw data was analyzed. Click an arc to select it, and double-click to open it. Press W or Escape to return to the rows.`}),
      node('div', {class: 'ybig'}, [node('div', {class: 'ywheel'}, [wheelSvg(size)]), node('div', {}, [wheelKey(), wheelLegend()])]),
    ];
  }
  /* The first draw keeps an entry opened before it landed. A person (or a link) can open one in
   * the first fraction of a second, while the rows are still being read, and the draw must not clear
   * it. ``navigate`` (app.js) leaves ``state.detailTurn``, the detail pane's turn once the page began,
   * and ``state.detailEarly``, whether something claimed the pane before the page began at all. A
   * later turn is someone's, and the first draw (``first``) leaves the pane alone. */
  function keepsDetail(first) {
    if (!first) return false;
    const early = !!state.detailEarly; state.detailEarly = false;
    return early || (turns.held.get(detailPane) || 0) !== state.detailTurn;
  }
  function render({first = false} = {}) {
    if (!shown() || !M) return;
    if (PHONE.matches) { renderPhone(); if (!keepsDetail(first)) renderInspector(); return; }
    const sheeted = NARROW.matches && Y.view !== 'rows';
    const R = Y.view === 'rows' || sheeted ? rowsFor() : {rows: [], ticks: [], title: '', hint: '', legend: []};
    lastRows = R.rows.filter((r) => r.isRow);
    const parts = [crumbs()];
    if (Y.view === 'rows' || sheeted) parts.push(...rowsParts(R));
    else if (Y.view === 'wheel') parts.push(...wheelParts(R));
    if (sheeted) parts.push(sheet(`The Wheel · ${word('project')}`, `The Wheel: ${nodeName(scale())}`, wheelParts(R, {inSheet: true})));
    fill(host(), node('div', {class: 'years', 'data-years': scale().kind, 'data-view': Y.view}, parts));
    if (Y.view === 'rows' || sheeted) {
      if (scale().kind === 'file') {
        const box = listPane.querySelector('.yfile');
        if (window.inspector) window.inspector.showEntry(scale().id, box, {back: () => up(), backLabel: 'back to its experiment', keepFocus: true});
      } else if (R.story) drawStory();
      else if (!R.design) renderList();
      drawPickBar();
    }
    if (sheeted) { const close = listPane.querySelector('.ysheet-close'); if (close) close.focus(); }
    if (!keepsDetail(first)) renderInspector();
  }

  /* The experiment's story (story.js). */
  function drawStory() {
    const box = listPane.querySelector('.ystory');
    const id = scale().id;
    if (!box) return;
    if (!window.ingenceStory) { box.append(node('p', {class: 'nothing', text: 'The story did not load. Reload the page.'})); return; }
    window.ingenceStory.render(box, id, {
      name: (other) => { const e = M.byId.get(other); return e ? e.shown : ''; },
      onOpen: (other) => { if (M.byId.has(other)) open({kind: 'exp', id: other}); },
      onFiles: () => { Y.groupBy = 'folder'; Y.sel = null; Y.focusRow = -1; render(); },
    });
  }

  /* ---- picking rows for a topic */
  function togglePick(spec) {
    if (picked.has(spec.key)) picked.delete(spec.key); else picked.set(spec.key, spec);
    for (const rowEl of listPane.querySelectorAll('.yrow[data-pick]')) {
      const on = picked.has(rowEl.dataset.pick);
      rowEl.classList.toggle('topic-picked', on);
      const tick = rowEl.querySelector('.ypickbox');
      if (tick) tick.checked = on;
    }
    drawPickBar();
  }
  function drawPickBar() {
    const holder = listPane.querySelector('.ypickholder');
    if (!holder || !window.topicKit) return;
    if (!picked.size) { holder.replaceChildren(); return; }
    holder.replaceChildren(topicKit.pickBar({noun: 'row', count: picked.size,
      onClear: () => { picked.clear(); togglePickRefresh(); },
      onAdd: async () => {
        const chosen = [...picked.values()];
        const lines = chosen.filter((c) => c.member.kind === 'line').length;
        const experiments = chosen.length - lines;
        const parts = [lines ? countOf(lines, 'theme') : '', experiments ? countOf(experiments, 'exp') : ''].filter(Boolean);
        const made = await topicKit.addTo({what: `${parts.join(' and ')} picked on Overlap`, members: chosen.map((c) => c.member)});
        if (made) { picked.clear(); togglePickRefresh(); }
      }}));
  }
  function togglePickRefresh() {
    for (const rowEl of listPane.querySelectorAll('.yrow[data-pick]')) {
      rowEl.classList.remove('topic-picked');
      const tick = rowEl.querySelector('.ypickbox');
      if (tick) tick.checked = false;
    }
    drawPickBar();
  }

  function toggleView(view) { Y.view = Y.view === view ? 'rows' : view; render(); }

  /* ------------------------------------------------------------------ the inspector */

  /* The level words of the cards, in one place. A card names the level it is about (a line of work,
   * an experiment, a person) only through ``levelWord(level, many)`` and ``levelShort(level)``, which read
   * the ladder's words (``word()`` above): an archive's cards say "theme" and "study". The cards' own level
   * ``line`` is the ladder's ``theme``. ``levelShort`` is the first word of a name ("line" of "line of
   * work"), where a sentence says "this line". */
  const CARD_KEY = {line: 'theme', experiment: 'experiment', person: 'person'};
  const levelWord = (level, many = false) => word(CARD_KEY[level], many);
  const levelShort = (level) => word(CARD_KEY[level]).split(' ')[0];
  const capFirst = cap;
  const withArticle = (text) => `${article(text)} ${text}`;
  /* How many of a level, with its word. */
  const countOfLevel = (n, level) => countOf(n, CARD_KEY[level]);

  /* The cards' own words, in one place, so a sentence is said the same way wherever a card says
   * it. Every number in them is the engine's, or a sum of the engine's counts. */
  const CARD = {
    completeness: (followed, raw) => `${fmt(followed)} of ${fmt(raw)} raw acquisitions found are followed by an analysis file found.`,
    noRaw: 'Ingence found no raw files in the files it read.',
    shares: (word, other, k, n) => `Shares ${word} with ${other} (${fmt(k)} of ${fmt(n)} of its words).`,
    yourName: 'Your name for it',
    noName: 'None. A name you give is shown in magenta with Ingence\'s words underneath.',
    openGrid: (more) => (more > 0 ? `Open the grid (${fmt(more)} more column${more === 1 ? '' : 's'})` : 'Open the grid'),
    believed: (at, clock) => `${at}, from its ${clock} clock. A date in a name is only context; it does not say when the file was made.`,
    clocks: 'Its clocks, never merged',
    clockNotRead: 'not read: no file was opened',
    sameColumnsHow: 'Read from the first line of each table that was opened.',
    noTwin: 'No other table read has these columns.',
    rules: 'What the rules found',
    rulesNote: 'Each row opens its rule, version and evidence. A rule\'s name is never cut short: it wraps.',
  };
  const act = (text, onClick, kind, help) => node('button', {type: 'button', class: `yact${kind === 'human' ? ' human' : ''}`, 'data-help': help || 'years.action', onclick: onClick, ...(kind === 'off' ? {disabled: 'disabled'} : {})}, [text]);
  /* A chip that goes to a page by its address. */
  const addressChip = (text, address, help) => node('a', {class: 'yact', href: address, 'data-help': help || 'years.action'}, [text]);
  const wordChips = (words, senses) => (words || []).slice(0, 12).map((w) => ({word: w, sense: (senses || []).includes(w)}));

  /* The pieces a card is made of. */
  const lead = (text) => {
    const found = /^([\d,]+ of [\d,]+)( .*)$/.exec(text);
    return node('p', {class: 'yc-lead'}, found ? [node('strong', {text: found[1]}), found[2]] : [text]);
  };
  const eyebrowNode = (text) => node('h3', {class: 'yc-eyebrow', text});
  const linkButton = (text, onClick, extra = {}) => node('button', {type: 'button', class: 'link', onclick: onClick, ...extra}, [text]);
  /* A key and its value, two columns; a value is words or a node. */
  const keyValues = (rows) => node('dl', {class: 'yc-kv'}, rows.flatMap(([key, value, mono]) => [node('dt', {text: key}), node('dd', {class: mono ? 'mono' : ''}, [value])]));
  /* The note under a card: what the thing is, never a conclusion, and the rule or route behind it. */
  const noteBox = (text, rule) => node('div', {class: 'notice-note yc-note'}, [text, rule ? node('div', {class: 'card-id', text: rule}) : '']);
  const chipRow = (chips) => node('div', {class: 'yactions'}, chips);
  const wordsBlock = (words, head) => (words.length ? node('div', {class: 'yc-block'}, [eyebrowNode(head),
    node('div', {class: 'ywords'}, words.map((w) => node('button', {type: 'button', class: `yword${w.sense ? ' sense' : ''}`, title: w.sense ? `${w.word}: a meaning the Dictionary knows; opens its page` : `${w.word}: opens its page`, 'data-help': 'years.word', onclick: () => { if (window.inspector) window.inspector.showWord(w.word); }}, [w.word])))]) : null);
  const hatchedValue = (text) => node('span', {class: 'yc-hatched'}, [node('span', {class: 'yc-hatch', 'aria-hidden': 'true'}), text]);

  /* The card: either the fields kicker, title, engine, sub, wheel, bar, facts, blocks, words, why,
   * actions and extra, in that order, or `parts`, a list of nodes in the order given. */
  function card(spec) {
    const parts = [];
    const head = [];
    if (spec.kicker) head.push(node('p', {class: 'ykicker', text: spec.kicker}));
    head.push(node('h2', {class: `ytitle${spec.titleHuman ? ' human' : ''}${spec.titleMono ? ' mono' : ''}`, text: spec.title}));
    if (spec.mono) head.push(node('p', {class: 'yc-monoline', text: spec.mono}));
    if (spec.path) head.push(spec.path);
    if (spec.engine) head.push(node('p', {class: 'yengine', text: spec.engine}));
    if (spec.sub) head.push(node('p', {class: 'ysubline', text: spec.sub}));
    if (spec.parts) parts.push(node('div', {class: 'yc-head'}, head)); else parts.push(...head);
    for (const part of spec.parts || []) if (part) parts.push(part);
    if (spec.wheel) parts.push(node('div', {class: 'ywheel'}, [spec.wheel]));
    if (spec.bar) {
      const b = spec.bar;
      parts.push(node('div', {class: 'ybarbox'}, [node('span', {class: `ybartrack${b.na ? ' na' : ''}`}, [b.na ? '' : node('span', {class: 'ybarfill', style: `width:${b.pct}%`})]), node('span', {class: `ybartext ${b.rightClass || ''}`, text: b.text})]));
    }
    if (spec.said) parts.push(node('p', {class: 'ysaid', text: spec.said}));
    if (spec.status) parts.push(node('p', {class: `ystatus ${spec.statusClass || ''}`, text: spec.status}));
    if (spec.facts && spec.facts.length) parts.push(node('dl', {class: 'yfacts'}, spec.facts.flatMap(([k, v]) => [node('dt', {text: k}), node('dd', {}, [v])])));
    for (const block of spec.blocks || []) {
      if (!block.rows.length && !block.empty) continue;
      parts.push(node('h3', {text: block.head}));
      if (!block.rows.length) { parts.push(node('p', {class: 'faint', text: block.empty})); continue; }
      parts.push(node('div', {class: 'yrows'}, block.rows.map((r) => node('button', {type: 'button', class: 'yitem', ...(r.onClick ? {onclick: r.onClick} : {}), title: r.title || r.text}, [
        r.swatch ? node('span', {class: `ysw ymark ${r.swatch}`, style: 'position:static;top:auto'}) : '', node('span', {class: `ytext${r.mono ? ' mono' : ''}`, text: r.text}), r.num ? node('span', {class: 'ynum', text: r.num}) : '']))));
      if (block.more) parts.push(node('p', {class: 'faint', text: block.more}));
    }
    if (spec.words && spec.words.length) {
      parts.push(node('h3', {text: spec.wordsHead || 'Its words'}));
      parts.push(node('div', {class: 'ywords'}, spec.words.map((w) => node('button', {type: 'button', class: `yword${w.sense ? ' sense' : ''}`, title: w.sense ? `${w.word}: a meaning the Dictionary knows; opens its page` : `${w.word}: opens its page`, 'data-help': 'years.word', onclick: () => { if (window.inspector) window.inspector.showWord(w.word); }}, [w.word]))));
    }
    if (spec.why) parts.push(node('div', {class: `ywhy ${spec.whyClass || ''}`}, [spec.why, spec.rule ? node('span', {class: 'yrule', text: spec.rule}) : '']));
    if (spec.actions && spec.actions.length) parts.push(node('div', {class: 'yactions'}, spec.actions));
    for (const extra of spec.extra || []) parts.push(extra);
    return node('div', {class: `ycard${spec.parts ? ' yc' : ''}`, ...(spec.cardKind ? {'data-card': spec.cardKind} : {})}, parts);
  }

  function renderInspector() {
    const s = Y.sel || scale();
    const turn = turns.claim(detailPane);
    const draw = (spec) => { if (turns.owns(detailPane, turn)) fill(detailPane, card(spec)); };
    switch (s.kind) {
      case 'project': draw(projectCard()); break;
      case 'person': draw(personCard(s.id)); break;
      case 'theme': draw(themeCard(s.id)); break;
      case 'exp': draw(expCard(s.id)); break;
      case 'fgroup': draw(groupCard(s)); break;
      case 'file': fileCard(s, turn); break;
      case 'year': yearCard(s.id, turn); break;
      case 'unplaced': draw({kicker: `Not in any ${levelShort('line')}`, title: `In no ${levelWord('line')}`, sub: M.unplacedNote, why: `${capFirst(levelWord('line', true))} are made of words. ${capFirst(withArticle(levelWord('experiment')))} whose words no other ${levelWord('experiment')} shares has no close neighbor. ${capFirst(levelWord('experiment', true))} that share words only with one another do not make a ${levelShort('line')} on their own.`, whyClass: 'projection', rule: 'themes · min_theme_documents'}); break;
      default: draw({title: nodeName(s)});
    }
  }

  /* ---------- reads the cards share, each once per run (the model clears them when a run changes) */

  /* Run ``read`` once a card has stood a moment: a person walking down the rows with the arrow keys
   * passes experiments by, and each need not cost the server a read of its files. */
  function whenStill(shown, read) {
    setTimeout(() => { if (shown.isConnected) read(); }, 300);
  }

  let projectHeld = null;        // the project's name and folder, from the landing's state
  async function loadProjectName() {
    // Read again after a few seconds: a person may have renamed the project since.
    if (projectHeld && Date.now() - projectHeld.at < 5000) return projectHeld;
    try {
      const landing = await api('/api/landing/state');
      const folder = String((landing.project && landing.project.path) || '').replace(/\/+$/, '').split('/').pop() || '';
      projectHeld = {name: (landing.project && landing.project.name) || folder, folder, at: Date.now()};
    } catch (error) {
      projectHeld = {name: '', folder: '', at: Date.now()};
    }
    return projectHeld;
  }
  /* The overlap under which a boundary is called uncertain, as the engine serves it (0.80 until the route is read). */
  const boundary = () => (stabilityHeld && stabilityHeld.uncertain_below != null ? stabilityHeld.uncertain_below : undefined);
  /* The folder the project reads, as the line under its name says it (a listing's own id for an archive; how
   * many where there are several). */
  function readsWhat() {
    const names = [...new Set((M.roots || []).map((r) => {
      const uri = String(r.root_uri || '');
      if (uri.startsWith('listing:')) return uri.split(':').pop();
      try { return decodeURIComponent(uri.replace(/\/+$/, '').split('/').pop() || uri); } catch (error) { return uri; }
    }).filter(Boolean))];
    if (names.length === 1) return names[0];
    return names.length ? `${fmt(names.length)} ${M.listing ? word('experiment', true) : 'folders'}` : '';
  }
  async function loadOverlap() {
    if (!overlapHeld) overlapHeld = await v1('/api/v1/themes/overlap');
    return overlapHeld;
  }
  /* One experiment's whole count of files and of those a dive opened: the first file of its list
   * carries both (the route counts every file whatever page it lists). */
  const openedHeld = new Map();
  async function loadOpened(expId) {
    const key = `${M.run}:${expId}`;
    if (openedHeld.has(key)) return openedHeld.get(key);
    const page = await v1(`/api/v1/experiments/files?${new URLSearchParams({experiment: expId, limit: '1'})}`);
    const held = {files: page.files, opened: page.opened_files};
    openedHeld.set(key, held);
    return held;
  }
  async function loadEntry(id) {
    if (entryCache.has(id)) return entryCache.get(id);
    const found = await v1(`/api/v1/entry?${new URLSearchParams({id})}`);
    entryCache.set(id, found);
    return found;
  }

  /* ---------- the project */

  function projectCard() {
    const years = M.years;
    const a = agg(M.exps);
    const span = years.length ? (years[0] === years[years.length - 1] ? String(years[0]) : `${years[0]}–${years[years.length - 1]}`) : '';
    const hasLines = M.themesComputed && M.themes.length > 0;
    const monoOf = () => [readsWhat(), M.multiPerson ? countOfLevel(M.people.length, 'person') : '', span].filter(Boolean).join(' · ');
    const facts = [[capFirst(levelWord('experiment', true)), `${fmt(M.exps.length)}${M.multiPerson ? ` in ${countOfLevel(M.people.length, 'person')}` : ''}${M.notShown ? ` (${fmt(M.notShown)} more not listed)` : ''}`]];
    const outside = M.completeness && M.outside ? M.outside.files : null;
    facts.push(['Files in them', outside == null ? fmt(M.files) : `${fmt(M.files)} of ${fmt(M.files + outside)} read`]);
    if (M.themesComputed) {
      facts.push([capFirst(levelWord('line', true)), `${M.themes.length ? fmt(M.themes.length) : 'none'}${M.broads.length && M.broads.length < M.themes.length ? ` in ${plural(M.broads.length, `broad ${levelShort('line')}`)}` : ''}`]);
      facts.push([`In no ${levelShort('line')}`, M.unplaced.length
        ? node('span', {}, [`${countOfLevel(M.unplaced.length, 'experiment')} · `, linkButton('see them', () => open({kind: 'unplaced', person: null}), {'data-help': 'years.unplaced'})])
        : 'none']);
    } else facts.push([capFirst(levelWord('line', true)), `not computed: ${M.themesSaid || 'no themes'}`]);
    const completeness = M.completeness ? lead(a.raw ? CARD.completeness(a.followed, a.raw) : CARD.noRaw) : null;
    // No Wheel, no Wheel sentence and no heading over an empty list where the engine drew no line.
    const wheelRow = hasLines ? node('div', {class: 'yc-wheelrow'}, [
      node('div', {class: 'ywheel yc-small'}, [wheelSvg(120, {small: true})]),
      node('div', {class: 'yc-wheeltext'}, [completeness, node('p', {class: 'yc-soft'}, [`The Wheel: ${levelWord('line', true)} inside, one arc per ${levelWord('experiment')} at the rim; blue where raw data is analyzed. `,
        linkButton('Open it (W)', () => { if (Y.view !== 'wheel') toggleView('wheel'); }, {class: 'link yc-wheel-link', 'data-help': 'years.wheel'})])])]) : completeness;
    // The name last read is drawn at once, and read again where it may have changed (never a flash of
    // "The project" over a name already known).
    const held = projectHeld;
    if (!held || Date.now() - held.at >= 5000) {
      loadProjectName().then((now) => {
        const shown = detailPane.querySelector('.ycard[data-card="project"]'); if (!shown) return;
        const name = shown.querySelector('.ytitle'); if (name && now.name) name.textContent = now.name;
      });
    }
    return {cardKind: 'project', kicker: 'The project', title: (held && held.name) || 'The project', mono: monoOf(),
      // How far the seeds agree is in Advanced (the Stability section), not on the main view.
      parts: [wheelRow, keyValues(facts), hasLines ? linesList() : null,
        noteBox(M.themesComputed ? `${capFirst(levelWord('line', true))} come from the words in the names of this scan's ${levelWord('experiment', true)}. They are a view, never a result. Completeness counts what Ingence found in the files it read.` : 'Completeness counts what Ingence found in the files it read. Other files may exist.', M.themesComputed ? 'themes · completeness' : 'completeness'),
        chipRow([addressChip('Inventory', '#inventory', 'years.inventory'), addressChip('Questions', '#review', 'years.questions'), addressChip('Map of groups', '#map', 'years.map')])]};
  }

  /* Lines of work as a two-column list (words, then how many and where it is going): four rows,
   * then "and n more", which opens the rest in a list that scrolls. */
  function linesList() {
    const rows = M.themes.map((t) => node('button', {type: 'button', class: 'yc-line', title: themeShown(t), onclick: () => select({kind: 'theme', id: t.theme})}, [
      node('span', {class: `yc-linename${t.person_label ? ' human' : ''}`, text: themeShown(t)}), node('span', {class: 'yc-linenum', text: `${fmt(t.exps.length)} · ${directionWords(t)}`})]));
    const shown = 4;
    const box = node('div', {class: 'yc-lines'}, rows.slice(0, shown));
    const parts = [eyebrowNode(capFirst(levelWord('line', true))), box];
    if (rows.length > shown) {
      const more = linkButton(`and ${fmt(rows.length - shown)} more`, () => {
        const opened = box.classList.toggle('open');
        box.replaceChildren(...(opened ? rows : rows.slice(0, shown)));
        more.textContent = opened ? 'show fewer' : `and ${fmt(rows.length - shown)} more`;
        more.setAttribute('aria-expanded', opened ? 'true' : 'false');
      }, {class: 'link yc-more', 'aria-expanded': 'false', 'data-help': 'years.more_lines'});
      parts.push(more);
    }
    return node('div', {class: 'yc-block'}, parts);
  }

  async function loadNoAnalysis() {
    if (noAnalysisHeld) return noAnalysisHeld;
    noAnalysisHeld = await v1('/api/v1/completeness/no_analysis_found');
    return noAnalysisHeld;
  }

  function personCard(name) {
    const p = M.peopleById.get(name);
    if (!p) return {kicker: capFirst(withArticle(levelWord('person'))), title: name, sub: 'Not in this scan.'};
    const themes = M.themes.filter((t) => p.themes.has(t.theme));
    const row = (M.peopleRows || []).find((r) => r.key === name);
    return {kicker: `${capFirst(withArticle(levelWord('person')))} (a top-level folder in a data folder)`, title: name, sub: `${countOfLevel(p.exps.length, 'experiment')}, ${p.first ?? '?'}–${p.last ?? '?'}.`,
      bar: barOf(p.a), said: row ? `${fmt(row.no_analysis_found)} of ${fmt(row.experiments_with_raw)} ${levelWord('experiment', true)} with raw data found have no analysis found.` : '',
      blocks: [{head: capFirst(levelWord('line', true)), rows: themes.map((t) => ({text: themeShown(t), num: `${t.exps.filter((e) => e.person === name).length} of ${t.exps.length}`, swatch: aggLevel(agg(t.exps.filter((e) => e.person === name))), onClick: () => select({kind: 'theme', id: t.theme, person: name})}))}],
      why: `${capFirst(withArticle(levelWord('person')))} is a top-level folder inside a data folder. It can be a person, an instrument, a project or a date.`, rule: 'completeness.person_level',
      actions: [act('Open', () => open({kind: 'person', id: name}))]};
  }

  /* ---------- a line of work */

  /* A line of work on the Map of groups: its experiments outlined, the map fitted to them. */
  function showLineOnMap(t) {
    if (window.ingenceMap) window.ingenceMap.show({fit: true, outlines: [{key: `line:${t.theme}`, label: themeShown(t), tag: '', ids: t.exps.map((e) => e.id)}]});
  }

  function themeCard(id) {
    const t = M.themeById.get(id); if (!t) return {title: capFirst(withArticle(levelWord('line'))), sub: 'Not in this scan.'};
    const renamed = !!t.person_label;
    const exps = t.exps;
    const a = t.a;    // the sums the completeness route serves for this line
    const people = [...new Set(exps.map((e) => e.person).filter(Boolean))];
    const whose = M.listing ? '' : (people.length === 1 ? people[0] : (people.length > 1 ? countOfLevel(people.length, 'person') : ''));
    const span = t.first != null ? (t.last != null && t.last !== t.first ? `${t.first}–${t.last}` : String(t.first)) : '';
    const sub = [whose, countOfLevel(exps.length, 'experiment'), span, directionWords(t)].filter(Boolean).join(' · ');
    // The words, and the words it shares with other lines (the engine's pairs, never worked out here).
    const shared = node('div', {class: 'yc-shares'});
    loadOverlap().then((O) => {
      const mine = O.pairs.filter((p) => p.theme_a === t.theme || p.theme_b === t.theme)
        .sort((x, y) => y.shared - x.shared || x.theme_a - y.theme_a || x.theme_b - y.theme_b).slice(0, 3);
      for (const p of mine) {
        const other = M.themeById.get(p.theme_a === t.theme ? p.theme_b : p.theme_a);
        if (other) shared.append(node('p', {class: 'yc-soft', text: CARD.shares(p.words.join(', '), themeShown(other), p.shared, t.words.length)}));
      }
    }).catch(() => {});
    const words = wordChips(t.words, t.senses);
    const wordsPart = node('div', {class: 'yc-block'}, [eyebrowNode('Its words'),
      node('div', {class: 'ywords'}, words.map((w) => node('button', {type: 'button', class: `yword${w.sense ? ' sense' : ''}`, title: w.sense ? `${w.word}: a meaning the Dictionary knows; opens its page` : `${w.word}: opens its page`, 'data-help': 'years.word', onclick: () => { if (window.inspector) window.inspector.showWord(w.word); }}, [w.word]))),
      shared,
      M.themesComputed ? node('p', {class: 'yc-soft'}, [linkButton('Stability (O)', () => navigate('stability'), {class: 'link yc-overlap-link', 'data-help': 'years.stability'})]) : null]);
    const years = [t.first != null ? `${t.first} first` : '', t.peak != null ? `${t.peak} peak` : '', t.last != null ? `${t.last} last` : ''].filter(Boolean).join(' · ');
    const group = M.themeSums ? M.themeSums.get(t.theme) : null;
    const facts = [['Dates', years || 'undated'], ['Files', fmt(exps.reduce((n, e) => n + (e.files || 0), 0))]];
    if (group) facts.push(['No analysis found', node('span', {}, [`${fmt(group.no_analysis_found)} of ${countOfLevel(group.experiments_with_raw, 'experiment')} with raw data`, group.no_analysis_found ? ' · ' : '', group.no_analysis_found ? linkButton('see them', () => navigate('found'), {'data-help': 'years.no_analysis'}) : ''])]);
    const nameBox = node('div', {class: 'yc-yourname'}, [
      node('div', {class: 'yc-namehead'}, [eyebrowNode(CARD.yourName), linkButton('Rename…', () => rename(t), {class: 'link yc-rename', 'data-help': 'years.rename'})]),
      renamed ? node('p', {class: 'yc-given'}, [node('strong', {class: 'human', text: t.person_label}), node('span', {class: 'yc-soft', text: ` · ${t.labelled_by ? `${t.labelled_by}, ${dayWords(t.labelled_at)}` : 'a person said so'}${t.person_note ? ` · ${t.person_note}` : ''}`}), ' ', linkButton('Use Ingence\'s words again', () => unname(t), {'data-help': 'years.rename'})])
        : node('p', {class: 'yc-soft', text: CARD.noName})]);
    // More about the line: its experiments and its words over time, behind one disclosure.
    const more = node('details', {class: 'yc-more-about'}, [node('summary', {text: `More about this ${levelShort('line')}`}),
      node('div', {class: 'yrows'}, exps.slice(0, 10).map((e) => node('button', {type: 'button', class: 'yitem', onclick: () => select({kind: 'exp', id: e.id}), title: e.shown}, [node('span', {class: `ysw ymark ${level(e)}`, style: 'position:static;top:auto'}), node('span', {class: 'ytext', text: e.shown}), node('span', {class: 'ynum', text: e.date ? dayWords(e.date).slice(-8) : 'undated'})]))),
      exps.length > 10 ? node('p', {class: 'faint', text: `and ${fmt(exps.length - 10)} more: open its ${levelWord('experiment', true)} below`}) : null,
      node('div', {class: 'yc-story'})]);
    more.addEventListener('toggle', () => { if (more.open) themeStory(id, more.querySelector('.yc-story')); });
    return {cardKind: 'theme', kicker: capFirst(withArticle(levelWord('line'))), title: themeShown(t), titleHuman: renamed,
      engine: renamed ? `Ingence: ${t.label}` : '', sub,
      // Its stability across seeds is in Advanced (the Stability section). The "Its words" block
      // holds the link.
      parts: [a.raw ? lead(CARD.completeness(a.followed, a.raw)) : (M.completeness ? lead(CARD.noRaw) : null), wordsPart, keyValues(facts), nameBox, more,
        chipRow([act(`Open its ${levelWord('experiment', true)}`, () => open({kind: 'theme', id: t.theme, person: Y.sel && Y.sel.person}), undefined, 'years.open_theme'),
          act('Show on the map', () => showLineOnMap(t), undefined, 'years.show_on_map')])]};
  }

  /* The theme card's second half, read when "More about this line" opens: the words handed on and the
   * words specific to it, from the years route. */
  async function themeStory(id, into) {
    if (!into || into.dataset.read) return;
    into.dataset.read = '1';
    try {
      if (!yearsStory) yearsStory = await v1('/api/v1/themes/years');
    } catch (error) { return; }
    const over = yearsStory.themes.find((t) => t.theme === id); if (!over) return;
    const spans = over.specific_words.slice(0, 8).map((w) => node('li', {}, [node('strong', {text: w.word}), ` in ${countOfLevel(w.experiments, 'experiment')}, ${w.first === w.last ? w.first : `${w.first}–${w.last}`}`]));
    const succession = over.succession.slice(0, 6).map((w) => node('li', {}, [node('strong', {text: w.word}), ` ${w.first === w.last ? w.first : `${w.first}–${w.last}`}`]));
    into.append(spans.length ? node('h3', {class: 'yc-eyebrow', text: 'Its own words, over time'}) : '', spans.length ? node('ul', {class: 'ids'}, spans) : '',
      succession.length ? node('h3', {class: 'yc-eyebrow', text: 'Words that came one after another'}) : '', succession.length ? node('ul', {class: 'ids'}, succession) : '');
  }

  /* ---------- an experiment */

  /* A folder path that may wrap at its slashes. */
  const pathNode = (path) => node('p', {class: 'yc-monoline yc-path'}, path.split('/').flatMap((part, i, all) => (i < all.length - 1 ? [part, '/', node('wbr')] : [part])));

  function expCard(id) {
    const e = M.byId.get(id); if (!e) return {title: capFirst(withArticle(levelWord('experiment'))), sub: 'Not in this scan.'};
    const t = e.theme != null ? M.themeById.get(e.theme) : null;
    const rs = rawState(e); const l = level(e);
    const datedByName = (e.name_parts || []).some((p) => p.kind === 'date');
    const date = e.date ? `${dayWords(e.date)} · ${datedByName ? 'from the folder\'s name' : 'from a name or a time inside it'}` : 'undated';
    const filesFound = node('span', {text: fmt(e.files)});
    const facts = [['Date', date], ['Files found', filesFound]];
    if (M.listing && e.rootName) facts.push([capFirst(word('root')), e.rootName]);
    facts.push([capFirst(levelWord('line')), t ? linkButton(themeShown(t), () => select({kind: 'theme', id: t.theme})) : (e.unplaced_reason ? `none: ${e.unplaced_reason}` : 'none')]);
    if (M.completeness) facts.push(['Raw · analysis · results', `${fmt(e.raw_acquisitions)} · ${fmt(e.analysis_files)} · ${fmt(e.result_tables)}`, true]);
    whenStill(filesFound, () => loadOpened(id).then((held) => { filesFound.textContent = `${fmt(held.files)} · ${fmt(held.opened)} opened`; }).catch(() => {}));
    const ae = e.analysed_elsewhere;
    let status = null;
    if (ae && ae.said_now) status = node('p', {class: 'ystatus yhuman', text: `${ae.said} (${ae.actor}, ${dayWords(ae.occurred_at)})${ae.read_by_run ? '' : ' · waits for Re-run'}`});
    else if (rs === 'gap') status = node('p', {class: 'ystatus yproblem'}, [node('span', {class: 'yc-problem-dot', 'aria-hidden': 'true'}), 'Analysis here, raw data not found: its raw data may be on another drive, in another folder, or deleted.']);
    const chips = [act('Open its files', () => open({kind: 'exp', id: e.id}), undefined, 'years.open_files')];
    if (M.completeness && e.answer_target) {
      const words = M.answerWords || [];
      const elsewhere = words.find((w) => w.word === 'elsewhere'); const back = words.find((w) => w.word === 'not_elsewhere');
      if (ae && ae.said_now) { if (back) chips.push(act(back.label, () => sayElsewhere(e, back), 'human', 'years.elsewhere')); }
      else if (elsewhere && (rs !== 'has' || l !== 'all')) chips.push(act(`${elsewhere.label}…`, () => sayElsewhere(e, elsewhere), 'human', 'years.elsewhere'));
    }
    chips.push(act('Relations (N)', () => { holdFocus({kind: 'entry', id: e.id, label: e.shown}); navigate('graph', {focus: {kind: 'entry', id: e.id, label: e.shown}}); }, undefined, 'years.graph'));
    const grid = node('div', {class: 'yc-grid-slot'});
    whenStill(grid, () => designSection(e, grid));
    const rule = node('div', {class: 'card-id', text: 'experiment_folder_from_contents · experiment_folder_from_dated_name · completeness'});
    whenStill(rule, () => loadEntry(id).then((found) => {
      const named = found.claims.find((c) => /^experiment_folder_/.test(c.rule_id));
      if (named) rule.textContent = `${named.rule_id} v${named.rule_version} · completeness`;
    }).catch(() => {}));
    const record = node('details', {class: 'yrecord'}, [node('summary', {text: 'Everything Ingence saved about this folder'})]);
    record.addEventListener('toggle', () => { if (record.open && !record.dataset.loaded) { record.dataset.loaded = '1'; const box = node('div', {}); record.append(box); if (window.inspector) window.inspector.showEntry(e.id, box, {keepFocus: true}); } });
    return {cardKind: 'exp', kicker: capFirst(withArticle(levelWord('experiment'))), title: e.shown,
      path: pathNode(e.folder),
      parts: [M.completeness && e.said ? lead(e.said) : null, status, keyValues(facts), grid, wordsBlock(wordChips(e.words), 'Its own words'),
        node('div', {class: 'notice-note yc-note'}, [`A folder the experiment rules read as one ${levelWord('experiment')}, from what it holds or its dated name. Ingence counts what it found in it; other files may exist.`, rule]),
        chipRow(chips), record]};
  }

  /* The design grid in an experiment's card: shown only where the grid is switched on and
   * the experiment has one. An experiment with no design has no section at all. */
  const GRID_COLUMNS = 9;
  function levelsSaid(factor) {
    const levels = factor.levels;
    if (levels.length <= 3) return levels.map((x) => x.shown).join(', ');
    return `${levels[0].shown}–${levels[levels.length - 1].shown}`;
  }
  function designSection(e, slot) {
    const draw = (d) => {
      if (!d || d.error || !d.enabled || !d.experiment || !d.experiment.has_grid) return;
      slot.replaceChildren(designCompact(e, d.experiment));
    };
    if (designCache.has(e.id)) { draw(designCache.get(e.id)); return; }
    loadDesign(e.id).then((d) => { if (slot.isConnected) draw(d); }).catch(() => {});
  }
  function designCompact(e, x) {
    const last = x.factors.length - 1;
    const columns = x.factors[last].levels;
    const shownColumns = Math.min(columns.length, GRID_COLUMNS);
    const more = columns.length - shownColumns;
    const rows = last === 0 ? [{key: '', levels: []}] : designRows(x);
    const byCell = new Map(x.cells.map((c) => [c.levels.join(','), c]));
    const found = x.cells.filter((c) => c.state !== 'not found').length;
    const rowHead = (r) => r.levels.map((level, i) => x.factors[i].levels[level].shown).join(' · ');
    const cells = [node('span', {})];
    for (let c = 0; c < shownColumns; c += 1) cells.push(node('span', {class: 'yc-gcol', title: columns[c].shown, text: columns[c].shown}));
    for (const r of rows) {
      cells.push(node('span', {class: 'yc-grow', text: last === 0 ? '' : rowHead(r)}));
      for (let c = 0; c < shownColumns; c += 1) {
        const cell = byCell.get([...r.levels, c].join(','));
        const label = [last === 0 ? '' : rowHead(r), columns[c].shown].filter(Boolean).join(' · ');
        if (!cell) { cells.push(node('span', {class: 'yc-cell none', role: 'img', 'aria-label': `${label}: not a combination of this design`})); continue; }
        if (cell.state === 'not found') { cells.push(node('span', {class: 'yc-cell notfound', role: 'img', 'data-state': 'not found', title: `${label}: not found in the file names read`, 'aria-label': `${label}: not found in the file names read`})); continue; }
        const said = `${label}: ${plural(cell.replicates, 'replicate')}, ${plural(cell.files, 'file')}${cell.state === 'empty output' ? '; the output found is empty' : ''}`;
        cells.push(node('span', {class: `yc-cell ${cell.replicates > 1 ? 'many' : 'one'}${cell.state === 'empty output' ? ' empty' : ''}`, role: 'img', 'data-state': cell.state, 'data-replicates': String(cell.replicates), title: said, 'aria-label': said}, [String(cell.replicates)]));
      }
    }
    const relations = [...new Set((x.pairs || []).map((p) => p.relation))].join(', ');
    const names = x.factors.map((f) => `${f.name} (${levelsSaid(f)})`);
    const keyText = `${names.join(' × ')}${relations ? `, ${relations}` : ''} · darker is more replicates · hatched: combination not found`;
    return node('div', {class: 'yc-gridblock', 'data-grid': 'card'}, [
      node('div', {class: 'yc-namehead'}, [eyebrowNode('Design grid'), node('span', {class: 'yc-soft', text: `${plural(x.factors.length, 'factor')} · ${plural(found, 'combination')} found · ${fmt(x.coverage.in_grid)} of ${fmt(x.coverage.files)} files`})]),
      node('div', {class: 'yc-gridwrap'}, [node('div', {class: 'yc-minigrid', style: `--cols:${shownColumns}`, role: 'group', 'aria-label': 'The design grid, one cell per combination'}, cells)]),
      node('p', {class: 'yc-soft yc-gridkey'}, [`${keyText} · `, linkButton(CARD.openGrid(more), () => go(pathTo({kind: 'exp', id: e.id}), {groupBy: 'design'}), {'data-help': 'years.design'})]),
    ]);
  }

  /* ---------- a group of files */

  function groupCard(s) {
    const held = filesCache.get(s.exp); if (!held) return {title: s.id};
    const fs = s.files.map((i) => held.F[i]);
    const byStage = {}; const byKind = {}; for (const f of fs) { byStage[stageWord(f.stage)] = (byStage[stageWord(f.stage)] || 0) + 1; byKind[f.kind] = (byKind[f.kind] || 0) + 1; }
    const rows = (counts) => Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => ({text: k, num: fmt(n)}));
    return {kicker: Y.groupBy === 'folder' ? 'A folder' : Y.groupBy === 'stage' ? 'A stage' : 'A kind of file', title: s.id, sub: `${plural(fs.length, 'file')}.`,
      blocks: [{head: 'By stage', rows: rows(byStage)}, {head: 'By kind', rows: rows(byKind)}, {head: 'Files', rows: fs.slice(0, 15).map((f) => ({text: f.name, mono: true, num: f.believed ? dayWords(f.believed.at) : '', onClick: () => select({kind: 'file', id: f.id, exp: s.exp, name: f.name})})), more: fs.length > 15 ? `and ${fs.length - 15} more: choose "files, one by one"` : ''}],
      why: 'A stage is what a rule read from the file\'s format or a word in its name; a kind is its format from its extension unless its content contradicts it.', rule: 'acquisition_stage_from_format · pipeline_stage_from_name · format_from_extension_unless_contradicted'};
  }

  /* ---------- a file */

  /* The engine's own words for the clock a believed time came from, as a person says them. */
  /* The rule's own words for the clock it chose (``earliest_credible_time``'s source), each as the noun in
   * "from its {clock} clock", and whether the clock is one read inside the file (said "instrument clock"). */
  const CLOCK_WORDS = {modification: 'modified', modified_before_born: 'modified', birth: 'birth',
    instrument_clock: 'instrument', document_clock: 'document', document_last_saved: 'last-save'};
  const INSIDE_CLOCKS = ['instrument_clock', 'document_clock', 'document_last_saved'];
  const clockWord = (source) => CLOCK_WORDS[source] || String(source || '').replace(/_clock$/, '').replace(/_/g, ' ');
  /* A clock as the card prints it: the day and the minute, in UTC. */
  const clockMinute = (c) => (c.precision === 'day' ? dayWords(c.at) + (c.until ? ` to ${dayWords(c.until)}` : '') : momentWords(c.at).replace(/ UTC$/, ''));
  const folderTail = (parent) => {
    const parts = String(parent || '').split('/').filter(Boolean);
    return parts.length ? `${parts.length > 2 ? '…/' : ''}${parts.slice(-2).join('/')}/` : `the ${levelWord('experiment')}'s folder`;
  };
  /* Whether a dive read the file's content: the engine's word for what the last run did with it. */
  const READ_WORDS = ['extracted', 'extracted_with_fallback'];

  /* A rule's name is never cut short. It wraps. A row in "What the rules concluded": the
   * conclusion in words at the left, the rule and its version in mono at the right. A click opens the
   * rule, version and evidence beneath it. */
  function ruleRow(text, rule, version, detail) {
    const body = node('div', {class: 'yc-ruledetail', hidden: 'hidden'}, detail);
    const row = node('button', {type: 'button', class: 'yc-rulerow', 'aria-expanded': 'false', 'data-help': 'years.rule_row', title: `${rule} v${version}`,
      onclick: () => { const open = body.hidden; body.hidden = !open; row.setAttribute('aria-expanded', open ? 'true' : 'false'); }}, [
      node('span', {class: 'yc-ruletext', text}), node('span', {class: 'yc-rulename', text: `${rule} v${version}`})]);
    return node('div', {class: 'yc-rule'}, [row, body]);
  }
  const SINGULAR = [[/\btheir\b/g, 'its'], [/\bthey were\b/g, 'it was']];
  const REASONS = {
    format_from_extension_unless_contradicted: 'from their extension', format_from_extension: 'from their extension',
    acquisition_stage_from_format: 'because their format is an instrument\'s', pipeline_stage_from_name: 'from a word in their name',
    stage_from_name: 'from a word in their name', analysis_stage_from_derived_sense: 'because they were made from other files',
    folder_copied_in_bulk: 'because the folder\'s files share one copy time', file_role_unless_program: 'from a word in their name, unless it is a program\'s',
    file_role_from_name: 'from a word in their name', program_from_extension: 'from their extension'};
  const reasonOf = (rule) => (REASONS[rule] ? SINGULAR.reduce((text, [pattern, word]) => text.replace(pattern, word), REASONS[rule]) : '');
  const sayValue = (value) => String(value == null ? '' : value).replace(/_format$/, '').replace(/_/g, ' ');
  /* A structured value (a group's key, a date's span) as a few plain words. */
  function readableValue(value) {
    let held = value;
    if (typeof held === 'string' && /^[[{]/.test(held)) { try { held = JSON.parse(held); } catch (error) { return sayValue(held); } }
    if (Array.isArray(held)) return held.map(readableValue).join(', ');
    if (held && typeof held === 'object') return Object.entries(held).filter(([, v]) => v != null && v !== '').map(([k, v]) => `${sayValue(k)} ${readableValue(v)}`).join('; ');
    return sayValue(held);
  }
  /* What a classifying claim is about, in a word: the stage, the format, the role, a time. */
  function kindOf(predicate) {
    const name = predicate.replace(/^classifies\./, '');
    for (const word of ['stage', 'format', 'role']) if (name.includes(word)) return word;
    return name.replace(/neighbourhood/g, 'neighborhood').replace(/_/g, ' ');
  }

  function conclusionRows(f, held, expId) {
    const rows = [];
    for (const c of f.claims) {
      if (!c.predicate.startsWith('classifies.') || c.value == null) continue;
      const what = kindOf(c.predicate);
      let text;
      if (c.predicate === 'classifies.earliest_credible_time') {
        let source = '';
        try { source = JSON.parse(c.value).source || ''; } catch (error) { source = f.believed ? f.believed.source : ''; }
        text = `earliest credible time: its ${clockWord(source)}${INSIDE_CLOCKS.includes(source) ? ' clock' : ''}`;
      } else if (c.predicate === 'classifies.date_neighbourhood_of_a_file') {
        let span = {};
        try { span = JSON.parse(c.value); } catch (error) { span = {}; }
        text = span.from ? `the date its name or its folder's gives: ${dayWords(span.from)}${span.to && span.to !== span.from ? ` to ${dayWords(span.to)}` : ''}` : 'a date in its name or its folder\'s';
      } else {
        const why = reasonOf(c.rule_id);
        const said = what === 'format' && kindWord(c.value) !== 'other' ? kindWord(c.value) : readableValue(c.value);
        text = `${what}: ${said}${why ? `, ${why}` : ''}`;
      }
      const fired = c.defeaters.filter((d) => d.fired);
      const detail = [node('p', {class: 'yc-soft', text: `${c.rule_id} version ${c.rule_version} · ${c.tier.replace(/_/g, ' ')} · ${c.status.replace(/_/g, ' ')} · read ${plural(c.evidence.length, 'record')}`}),
        ...fired.map((d) => node('p', {class: 'yc-soft yc-conflict', text: `Conflicting: ${d.reason}`})),
        ...c.support.slice(0, 4).map((x) => node('p', {class: 'yc-soft', text: `${x.name}${x.family ? ` (${x.family})` : ''}: ${typeof x.value === 'object' ? JSON.stringify(x.value) : x.value}`}))];
      rows.push(ruleRow(text, c.rule_id, c.rule_version, detail));
    }
    const grouped = new Map();
    for (const d of f.derivations) {
      const key = `${d.direction}|${d.rule_id}|${d.rule_version}|${d.published ? 'published' : 'proposed'}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(d);
    }
    for (const list of grouped.values()) {
      const first = list[0];
      const state = first.published ? 'published' : 'proposed';
      const first_name = first.other_name || 'a file';
      const text = first.direction === 'made_from'
        ? `made from ${first_name}${list.length > 1 ? ` and ${fmt(list.length - 1)} more` : ''}, ${state}`
        : `${plural(list.length, 'file')} made from it, ${state}`;
      const detail = list.slice(0, 12).map((d) => node('button', {type: 'button', class: 'link yc-file', onclick: () => select({kind: 'file', id: d.other_id, exp: expId, name: d.other_name || ''})}, [d.other_name || d.other_id]));
      rows.push(ruleRow(text, first.rule_id, first.rule_version, [node('p', {class: 'yc-soft', text: `${first.rule_id} version ${first.rule_version} · ${first.tier.replace(/_/g, ' ')}`}), ...detail]));
    }
    for (const c of f.copies) {
      rows.push(ruleRow(`${c.kind}: ${plural(c.members, 'file')} by ${c.evidence}`, c.rule_id, c.rule_version, [node('p', {class: 'yc-soft', text: `${c.rule_id} version ${c.rule_version} · ${c.status.replace(/_/g, ' ')}`}), ...c.others.map((o) => node('p', {class: 'mono', text: o.path || o.name || o.id}))]));
    }
    return rows;
  }

  /* A table's twin files: read from the columns route once the card is drawn. */
  const twinSets = new Map();   // the filter text a "See the n" sets, to the ids it keeps
  function twinMember(q, f) {
    const held = twinSets.get(q);
    return held ? held.has(f.id) : null;
  }
  /* A table's columns, read once per run: the card is drawn again whenever the page is. */
  const columnsHeld = new Map();
  function loadColumns(id) {
    const key = `${M.run}:${id}`;
    if (!columnsHeld.has(key)) {
      // The most files the route will name, so "See the n" can open every one that lies in this experiment.
      columnsHeld.set(key, v1(`/api/v1/columns?${new URLSearchParams({id, limit: '500'})}`).catch((error) => { columnsHeld.delete(key); throw error; }));
    }
    return columnsHeld.get(key);
  }
  async function fillSameColumns(slot, f, expId) {
    if (!/^(csv|tsv|tab)$/.test(f.extension || '')) return;
    let c;
    try { c = await loadColumns(f.id); } catch (error) { return; }
    if (!c.read || !slot.isConnected) return;
    const names = c.column_names.length > 24 ? `${c.column_names.slice(0, 24).join(' · ')} · and ${fmt(c.column_names.length - 24)} more` : c.column_names.join(' · ');
    if (!c.same_columns_total) {
      slot.replaceChildren(node('div', {class: 'yc-columns', 'data-columns': 'none'}, [node('div', {text: CARD.noTwin}), node('div', {class: 'card-id', text: names})]));
      return;
    }
    // "Same columns as {n} files in this folder and {k} others": the engine's whole counts, the file's own
    // folder first. "See the {n}" opens the files the route names in that folder, so the two are one answer.
    const here = c.same_columns_in_folder;
    const elsewhere = c.same_columns_elsewhere;
    const twinIds = new Set((c.same_columns_here || []).map((x) => x.id)); twinIds.add(f.id);
    const how = node('div', {class: 'yc-soft'}, [CARD.sameColumnsHow, ' ',
      here ? linkButton(`See the ${fmt(here)}`, () => {
        const q = `same columns as ${f.name}`.toLowerCase();
        twinSets.set(q, twinIds);
        go(pathTo({kind: 'exp', id: expId}), {groupBy: 'list', filter: `same columns as ${f.name}`});
      }, {'data-help': 'years.same_columns'}) : null]);
    const said = here ? [node('strong', {text: `Same columns as ${fmt(here)} file${here === 1 ? '' : 's'}`}), ` in this folder${elsewhere ? ` and ${fmt(elsewhere)} other${elsewhere === 1 ? '' : 's'}` : ''}.`]
      : [node('strong', {text: `Same columns as ${fmt(elsewhere)} file${elsewhere === 1 ? '' : 's'}`}), ' in other folders.'];
    slot.replaceChildren(node('div', {class: 'yc-columns', 'data-columns': 'twins'}, [node('div', {}, said), node('div', {class: 'card-id', text: names}), how]));
  }

  function fileCard(s, turn) {
    const held = filesCache.get(s.exp); const f = held ? held.F.find((x) => x.id === s.id) : null;
    // An experiment opens on its story, so its files may not be read yet: read them,
    // and draw the file's card once they are, if it is still the one chosen.
    if (!held && s.exp) loadFiles(s.exp).then(() => { if (Y.sel && Y.sel.kind === 'file' && Y.sel.id === s.id && shown()) renderInspector(); }).catch(() => {});
    if (!f) { if (window.inspector) window.inspector.showEntry(s.id, detailPane, {keepFocus: true}); return; }
    const e = M.byId.get(s.exp);
    const clock = (source) => f.clocks.find((c) => c.source === source);
    const modified = clock('modified'); const born = clock('created'); const inside = clock('event'); const named = clock('name');
    const opened = READ_WORDS.includes(f.scan_outcome);
    const failed = f.scan_outcome === 'content_error' || f.scan_outcome === 'changed_during_read';
    const clockRows = [
      ['modified', modified ? clockMinute(modified) : hatchedValue('not recorded'), !!modified],
      ['birth', born ? `${clockMinute(born)}${born.detail && born.detail !== 'birthtime' ? ` · ${born.detail === 'ctime' ? 'change time, standing in' : born.detail.replace(/_/g, ' ')}` : ''}` : hatchedValue('not recorded'), !!born],
      ['inside the file', inside ? `${clockMinute(inside)} · ${inside.detail}` : hatchedValue(opened ? 'examined: no clock found inside' : (failed ? 'not read: reading it failed' : CARD.clockNotRead)), !!inside],
      ['name (folder)', named ? clockMinute(named) : hatchedValue('no date in its name'), !!named],
    ];
    const believedText = f.believed
      ? node('p', {class: 'yc-lead'}, [node('strong', {text: 'Believed time'}), ` ${CARD.believed(momentWords(f.believed.at), clockWord(f.believed.source))}`, f.believed.conflicting ? node('span', {class: 'yproblem'}, [' ', node('span', {class: 'yc-problem-dot', 'aria-hidden': 'true'}), 'Marked as conflicting, because a rule that withdraws it applied.']) : ''])
      : node('p', {class: 'yc-lead'}, [node('strong', {text: 'Believed time:'}), ` ${f.believed_said}. A date read from a name is context, never when the file was made.`]);
    const slot = node('div', {class: 'yc-columns-slot'});
    fillSameColumns(slot, f, s.exp);
    const rows = conclusionRows(f, held, s.exp);
    const teachBox = node('div', {class: 'yc-teach', hidden: 'hidden'});
    // ``data-teach`` marks the chip for the T key (Years, files one by one), which clicks it.
    const teachChip = act('Teach a word…', () => { teachBox.hidden = !teachBox.hidden; if (!teachBox.hidden) fillTeach(teachBox, f, s, turn); }, undefined, 'years.teach');
    teachChip.setAttribute('data-teach', '1');
    const spec = {cardKind: 'file', kicker: 'A file', title: f.name, titleMono: true,
      sub: `${f.kind} · ${stageWord(f.stage)}${f.role.length ? ` · ${f.role.map((r) => r.value).join(', ')}` : ''}${f.size_bytes == null ? '' : ` · ${bytesWords(f.size_bytes)}`} · in ${folderTail(f.parent)}`,
      parts: [believedText,
        node('div', {class: 'yc-block'}, [eyebrowNode(CARD.clocks), node('dl', {class: 'yc-kv yc-clocks'}, clockRows.flatMap(([k, v, mono]) => [node('dt', {text: k}), node('dd', {class: mono ? 'mono' : ''}, [v])]))]),
        slot,
        node('div', {class: 'yc-block'}, [eyebrowNode(CARD.rules), rows.length ? node('div', {class: 'yrules yc-rules'}, rows) : node('p', {class: 'yc-soft', text: 'No rule found anything about it in the files Ingence read.'}), node('p', {class: 'yc-soft', text: CARD.rulesNote})]),
        chipRow([act('Its record', () => open({kind: 'file', id: f.id, exp: s.exp, name: f.name}), undefined, 'years.record'),
          act('Relations (N)', () => { holdFocus({kind: 'entry', id: f.id, label: f.name}); navigate('graph', {focus: {kind: 'entry', id: f.id, label: f.name}}); }, undefined, 'years.graph'),
          teachChip]),
        teachBox]};
    void e;
    if (turns.owns(detailPane, turn)) fill(detailPane, card(spec));
  }

  /* The words in a file's name, each a way to its meaning here (and to give your own). */
  async function fillTeach(box, f, s, turn) {
    box.replaceChildren(node('p', {class: 'yc-soft', text: 'Reading the words of its name...'}));
    let found;
    try { found = await loadEntry(f.id); } catch (error) { box.replaceChildren(node('p', {class: 'yc-soft', text: `Its words could not be read: ${String(error.message || error)}`})); return; }
    const parts = found.components.filter((p) => p.normalized_value && /[a-z]/i.test(p.normalized_value));
    if (!parts.length) { box.replaceChildren(node('p', {class: 'yc-soft', text: 'Its name holds no word.'})); return; }
    box.replaceChildren(node('p', {class: 'yc-soft', text: 'The words of its name. Open one to see what it means here, and to give your own.'}),
      node('div', {class: 'ywords'}, parts.map((p) => node('button', {type: 'button', class: 'yword', 'data-help': 'years.word', onclick: () => { if (window.inspector) window.inspector.showWord(String(p.normalized_value), undefined, {back: () => select({kind: 'file', id: f.id, exp: s.exp, name: f.name}), backLabel: `back to ${f.name}`}); }}, [p.raw_value || p.normalized_value]))));
  }

  /* Nothing the experiment rules read is an experiment. This is the one drawn empty state, in the
   * engine's words, naming the two rules that read experiments and the way to the Inventory. */
  function yearsEmpty() {
    const emptyCard = states.say(host(), {eyebrow: 'Overlap · By year · empty', dot: 'empty', sentence: `No ${levelWord('experiment', true)} were found in what was read.`,
      body: [`The rules that read a folder as ${withArticle(levelWord('experiment'))} found none: no dated names and no ${levelWord('experiment')} files. The Inventory lists what is here.`,
        'One rule reads a folder from what it holds (experiment_folder_from_contents). The other reads its dated name (experiment_folder_from_dated_name).'],
      actions: [{label: 'Open the Inventory', href: '#inventory'}, {label: `Which rules read ${levelWord('experiment', true)}`, href: '#rules'}]});
    // Once, quietly, show the words that cover the most unread word parts (never a dialog or a badge).
    v1('/api/v1/words/queue?limit=20').then((queue) => {
      const said = window.wordsPage ? window.wordsPage.quietLine(queue) : '';
      if (said && emptyCard.isConnected) emptyCard.append(node('p', {class: 'state-body'}, [`${said} `, node('a', {href: '#words', 'data-help': 'questions.words_link', text: 'Open Words'})]));
    }, () => {});
    // The pane beside it is never left blank: the project's own card, with its way to the Inventory.
    try { fill(detailPane, card(projectCard())); } catch (error) { fill(detailPane); }
  }

  /* "What was I working on in <year>": the year's story from the themes route. */
  async function yearCard(year, turn) {
    if (!turns.owns(detailPane, turn)) return;
    states.loading(detailPane, `Reading ${year}...`);
    try { if (!yearsStory) yearsStory = await v1('/api/v1/themes/years'); } catch (error) { if (turns.owns(detailPane, turn)) states.failed(detailPane, error, () => renderInspector()); return; }
    if (!turns.owns(detailPane, turn)) return;
    const story = yearsStory.story[String(year)];
    const exps = M.exps.filter((e) => e.year === year);
    if (!story && !exps.length) { fill(detailPane, card({kicker: 'A year', title: String(year), sub: `No experiment in this project is dated ${year}.`})); return; }
    const blocks = [];
    if (story) {
      blocks.push({head: 'Themes, compared with other years', rows: story.most.map((m) => ({text: m.label, num: `${plural(m.experiments, 'experiment')} · ${m.lift.toFixed(1)}×`, onClick: () => { const t = M.themes.find((x) => x.label === m.label); if (t) select({kind: 'theme', id: t.theme}); }}))});
      if (story.began.length) blocks.push({head: 'Began this year', rows: story.began.map((l) => ({text: l}))});
      if (story.last_seen_year_before.length) blocks.push({head: 'Last seen the year before', rows: story.last_seen_year_before.map((l) => ({text: l}))});
    }
    blocks.push({head: 'Experiments', rows: exps.slice(0, 20).map((e) => ({text: e.shown, num: e.date ? dayWords(e.date).slice(0, 6) : '', swatch: level(e), onClick: () => select({kind: 'exp', id: e.id})})), more: exps.length > 20 ? `and ${exps.length - 20} more` : ''});
    fill(detailPane, card({kicker: 'A year', title: `What was I working on in ${year}`, sub: `${plural(exps.length, 'experiment')} dated ${year}.`, blocks,
      words: story ? story.new_words.slice(0, 12).map((w) => ({word: w.word})) : [], wordsHead: 'Words first written this year',
      why: 'A theme\'s share this year, compared with its share of all years. Words first written this year are words no earlier experiment used.', rule: 'themes/years', whyClass: 'projection'}));
  }

  /* ------------------------------------------------------------------ actions that write */

  async function rename(t) {
    const who = await askActor(); if (!who) return;
    const name = await dialogs.ask({title: 'Naming a theme', text: `Ingence's words: ${t.label}. Your name is shown wherever this line appears; Ingence's words stay underneath. It applies to this line in this project: ${plural(t.exps.length, 'experiment')}. Recorded under your name; it decides nothing.`,
      label: 'Your name for it', submit: 'Record it', validate: (typed) => (typed.length > 200 ? 'A name is at most 200 characters.' : '')});
    if (!name) return;
    try {
      await write('/api/review/answer', {actor: me.actor, target: t.target, described: name, reading: t.label});
      await load({force: true}); render();
    } catch (error) { await dialogs.failed(error); }
  }
  async function unname(t) {
    const who = await askActor(); if (!who) return;
    try {
      await write('/api/review/answer', {actor: me.actor, target: t.target, note: `the name "${t.person_label}" was removed; Ingence's words again`, reading: t.label});
      await load({force: true}); render();
    } catch (error) { await dialogs.failed(error); }
  }
  async function sayElsewhere(e, word) {
    const who = await askActor(); if (!who) return;
    const note = await dialogs.ask({title: word.label, text: `${word.meaning} Saved under your name for ${e.shown}. The next scan reads it. Add a note to say where, if you know.`, label: 'Where (optional)', submit: 'Save', allowEmpty: true});
    if (note === null || note === undefined) return;
    try {
      await write('/api/review/answer', {actor: me.actor, target: e.answer_target, word: word.word, ...(note ? {note} : {})});
      await load({force: true}); render();
    } catch (error) { await dialogs.failed(error); }
  }

  /* ------------------------------------------------------------------ the Wheel */

  function arcPath(cx, cy, r0, r1, a0, a1) {
    const big = a1 - a0 > Math.PI ? 1 : 0;
    const p = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    const [x0, y0] = p(r1, a0); const [x1, y1] = p(r1, a1); const [x2, y2] = p(r0, a1); const [x3, y3] = p(r0, a0);
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r1} ${r1} 0 ${big} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} L ${x2.toFixed(2)} ${y2.toFixed(2)} A ${r0} ${r0} 0 ${big} 0 ${x3.toFixed(2)} ${y3.toFixed(2)} Z`;
  }
  /* The words that fit in `room` pixels at the Wheel's label size: the whole text, or its start
   * and an ellipsis, or nothing where fewer than four letters would show. */
  let ruler = null;
  function fitted(text, room) {
    if (!ruler) ruler = document.createElement('canvas').getContext('2d');
    if (!ruler) return text;
    ruler.font = `11px ${getComputedStyle(document.body).fontFamily}`;
    if (ruler.measureText(text).width <= room) return text;
    for (let n = text.length - 1; n >= 4; n -= 1) {
      const cut = `${text.slice(0, n).trimEnd()}\u2026`;
      if (ruler.measureText(cut).width <= room) return cut;
    }
    return '';
  }
  /* The Wheel's lines, in the order they stand around it: what the arcs, their labels and the key
   * beside the Wheel all read, so a number means the same arc in each. */
  function wheelLines() {
    const person = scale().kind === 'person' ? scale().id : null;
    const themes = M.themes.filter((t) => (person ? t.exps.some((e) => e.person === person) : t.exps.length));
    const expsOf = (t) => (person ? t.exps.filter((e) => e.person === person) : t.exps);
    const total = themes.reduce((n, t) => n + expsOf(t).length, 0) || 1;
    // Broad lines head the rows only where they gather: a broad line per theme says nothing.
    const headed = M.broads.length && M.broads.length <= Math.ceil(themes.length * 0.6);
    const groups = headed ? M.broads.map((b) => ({b, ts: b.themes.filter((t) => themes.includes(t))})).filter((g) => g.ts.length) : [{b: null, ts: themes}];
    const order = groups.flatMap((g) => g.ts.map((t) => ({t, es: expsOf(t), n: person ? expsOf(t).length : t.n})));
    order.forEach((line, i) => { line.number = i + 1; });
    return {person, themes, expsOf, total, headed, groups, order};
  }

  /* An arc narrower than this many pixels (measured along the middle of its ring) is no place for a
   * label: it is numbered, and the key beside the Wheel names it. */
  const LABEL_MIN_ARC = 14;
  const WHEEL_VROOM = 22;
  /* The Wheel's outer band of topics: one thin ring per topic, outside the rim, in the
   * topic's own color (a person's). The rings inside are drawn a little smaller to make room. At most
   * this many topics are drawn as rings. The key says how many more there are. */
  const WHEEL_TRACKS = 8;
  const TRACK_PX = 6;
  const TRACK_GAP = 3;
  /* The topics with an experiment among those the Wheel draws (the topics of the reading in hand). */
  function wheelTracks(order) {
    const reading = window.topicKit ? topicKit.current() : null;
    if (!reading || !reading.topics.length) return {tracks: [], more: 0, inView: new Set()};
    const inView = new Set(order.flatMap((line) => line.es.map((e) => e.id)));
    const holding = reading.topics.filter((t) => [...t.experiments].some((id) => inView.has(id)));
    return {tracks: holding.slice(0, WHEEL_TRACKS), more: Math.max(0, holding.length - WHEEL_TRACKS), inView};
  }

  function wheelSvg(size, {small = false} = {}) {
    // The SVG namespace, taken from the browser rather than written as an address: the page names no
    // scheme (the offline rule's test reads every asset for one). The HTML parser puts an <svg> in
    // the SVG namespace by itself; the XML parser does not (an XML <svg/> with no xmlns attribute has
    // no namespace at all, so every arc became an unknown element and its <title> printed as text).
    // A test reads each drawn element's namespace.
    const holder = document.createElement('template'); holder.innerHTML = '<svg></svg>';
    const svgNS = holder.content.firstElementChild.namespaceURI;
    const svg = document.createElementNS(svgNS, 'svg');
    const side = small ? 0 : LABEL_ROOM; const above = small ? 0 : WHEEL_VROOM;
    svg.setAttribute('viewBox', `${-side} ${-above} ${size + 2 * side} ${size + 2 * above}`); svg.setAttribute('width', String(size + 2 * side)); svg.setAttribute('height', String(size + 2 * above));
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `The Wheel: ${word('theme', true)} as rings`);
    const {person, expsOf, total, headed, groups, order} = wheelLines();
    const {tracks, inView} = small ? {tracks: [], inView: new Set()} : wheelTracks(order);
    const bandPx = tracks.length ? TRACK_GAP + tracks.length * (TRACK_PX + 1) : 0;
    const cx = size / 2; const cy = size / 2; const R = size / 2 - 4 - bandPx;
    const slots = new Map();   // each experiment's angles, for the topics' band
    const rings = headed ? [[0.26, 0.42], [0.44, 0.68], [0.70, 1]] : [null, [0.3, 0.62], [0.64, 1]];
    const path = (r0, r1, a0, a1, cls, title, onClick, onOpen, selected, dim) => {
      const p = document.createElementNS(svgNS, 'path');
      p.setAttribute('d', arcPath(cx, cy, r0, r1, a0, a1));
      p.setAttribute('class', `${cls}${selected ? ' selected' : ''}${dim ? ' dim' : ''}`);
      const t = document.createElementNS(svgNS, 'title'); t.textContent = title; p.append(t);
      p.addEventListener('click', (ev) => { ev.stopPropagation(); onClick(); });
      if (onOpen) p.addEventListener('dblclick', (ev) => { ev.stopPropagation(); onOpen(); });
      svg.append(p);
      return p;
    };
    // Labels never overlap. A label stands at the rim and reads outward, so one near the left or
    // right edge would run out of the picture, so it is shortened to the room it has, or numbered.
    // The boxes of what is placed are kept, and a label or number that would touch one is not
    // drawn. The key beside the Wheel names every line, so nothing is ever only on the picture or on hover.
    const boxes = [];
    const clash = (b) => boxes.some((o) => b.x0 < o.x1 + 2 && b.x1 + 2 > o.x0 && b.y0 < o.y1 + 1 && b.y1 + 1 > o.y0);
    const textWidth = (text) => { if (!ruler) ruler = document.createElement('canvas').getContext('2d'); if (!ruler) return text.length * 6.5; ruler.font = `11px ${getComputedStyle(document.body).fontFamily}`; return ruler.measureText(text).width; };
    const place = (r, a, text, cls, line) => {
      const x = cx + r * Math.cos(a); const y = Math.min(size + above - 7, Math.max(7 - above, cy + r * Math.sin(a)));
      const toLeft = Math.cos(a) < 0;
      const said = cls === 'wlabel' ? fitted(text, (toLeft ? x + side : size + side - x) - 2) : text;
      if (!said) return false;
      const w = cls === 'wlabel' ? textWidth(said) : textWidth(said) + 2;
      const box = {x0: toLeft ? x - w : x, x1: toLeft ? x : x + w, y0: y - 6.5, y1: y + 6.5};
      if (box.x0 < -side || box.x1 > size + side || clash(box)) return false;
      boxes.push(box);
      const t = document.createElementNS(svgNS, 'text');
      t.setAttribute('x', x.toFixed(1)); t.setAttribute('y', y.toFixed(1)); t.setAttribute('dominant-baseline', 'middle');
      t.setAttribute('text-anchor', toLeft ? 'end' : 'start'); t.setAttribute('class', cls); t.setAttribute('data-line', String(line.t.theme)); t.setAttribute('data-n', String(line.number));
      t.textContent = said; svg.append(t);
      return true;
    };
    let a = -Math.PI / 2;
    const selTheme = Y.sel && Y.sel.kind === 'theme' ? Y.sel.id : null; const selExp = Y.sel && Y.sel.kind === 'exp' ? Y.sel.id : null;
    const drawn = [];
    for (const g of groups) {
      const n = g.ts.reduce((k, t) => k + expsOf(t).length, 0); const a0 = a; const a1 = a + (2 * Math.PI * n) / total;
      if (g.b && rings[0]) path(R * rings[0][0], R * rings[0][1], a0, a1, 'fill-broad', `${g.b.label} · ${countOf(n, 'exp')}`, () => {}, null, false, false);
      let ta = a0;
      for (const t of g.ts) {
        const es = expsOf(t); const t0 = ta; const t1 = ta + (2 * Math.PI * es.length) / total;
        const dim = (selTheme != null && selTheme !== t.theme) || (selExp != null && !es.some((e) => e.id === selExp));
        const arcPx = (t1 - t0) * R * ((rings[1][0] + rings[1][1]) / 2);
        const arc = path(R * rings[1][0], R * rings[1][1], t0, t1, `fill-${aggLevel(t.a)}`, `${themeShown(t)} · ${countOf(es.length, 'exp')} · ${LEVEL_WORDS[aggLevel(t.a)]}`, () => select({kind: 'theme', id: t.theme, person}), () => open({kind: 'theme', id: t.theme, person}), selTheme === t.theme, dim);
        arc.setAttribute('data-line', String(t.theme)); arc.setAttribute('data-arc-px', arcPx.toFixed(1));
        let ea = t0;
        for (const e of es) {
          const e1 = ea + (2 * Math.PI) / total;
          const cls = Y.measure === 'provenance' ? (e.analysed_elsewhere ? 'fill-human' : 'fill-plain') : `fill-${level(e)}`;
          path(R * rings[2][0], R * (selExp === e.id ? 1 : rings[2][1] - 0.03), ea, e1, cls, `${e.shown} · ${e.date ? dayWords(e.date) : 'undated'} · ${LEVEL_WORDS[level(e)]}`, () => select({kind: 'exp', id: e.id}), () => open({kind: 'exp', id: e.id}), selExp === e.id, dim);
          slots.set(e.id, [ea, e1]);
          ea = e1;
        }
        drawn.push({t, mid: (t0 + t1) / 2, arcPx, label: themeShown(t).length > 28 ? `${themeShown(t).slice(0, 27)}…` : themeShown(t)});
        ta = t1;
      }
      a = a1;
    }
    // The band: for each topic, its experiments' slots joined where they touch, as arcs of its ring.
    tracks.forEach((topic, k) => {
      const mine = [...topic.experiments].filter((id) => slots.has(id)).map((id) => slots.get(id)).sort((x, y) => x[0] - y[0]);
      const runs = [];
      for (const [a0, a1] of mine) {
        const last = runs[runs.length - 1];
        if (last && a0 - last[1] < 1e-6) last[1] = a1; else runs.push([a0, a1]);
      }
      const r0 = R + TRACK_GAP + k * (TRACK_PX + 1);
      const held = [...topic.experiments].filter((id) => inView.has(id)).length;
      for (const [a0, a1] of runs) {
        const arc = document.createElementNS(svgNS, 'path');
        arc.setAttribute('d', arcPath(cx, cy, r0, r0 + TRACK_PX, a0, Math.min(a1, a0 + 2 * Math.PI - 1e-4)));
        arc.setAttribute('class', 'wtopic'); arc.setAttribute('data-topic', topic.id);
        arc.setAttribute('style', `fill: ${topic.color}`);
        const title = document.createElementNS(svgNS, 'title'); title.textContent = `Your collection ${topic.name} · ${countOf(held, 'exp')} here`;
        arc.append(title);
        arc.addEventListener('click', (ev) => { ev.stopPropagation(); topicKit.open(topic.id); });
        svg.append(arc);
      }
    });
    if (!small) {
      const rim = R * (rings[2][1] + 0.02) - 2 + bandPx;
      const lineOf = (d) => order.find((l) => l.t === d.t);
      // The widest arcs claim their labels first; an arc that could not take one is numbered.
      const wide = drawn.filter((d) => d.arcPx > LABEL_MIN_ARC).sort((x, y) => y.arcPx - x.arcPx);
      const labelled = new Set();
      for (const d of wide) if (place(rim, d.mid, d.label, 'wlabel', lineOf(d))) labelled.add(d);
      for (const d of drawn) {
        if (labelled.has(d)) continue;
        // A number tries the rim, then a little further out (the picture has room above and below).
        for (const step of [0, 13, 26]) if (place(rim + step, d.mid, String(lineOf(d).number), 'wnum', lineOf(d))) break;
      }
    }
    const center = document.createElementNS(svgNS, 'text'); center.setAttribute('x', String(cx)); center.setAttribute('y', String(cy)); center.setAttribute('class', 'center'); center.setAttribute('dominant-baseline', 'middle');
    center.textContent = small ? `${fmt(total)}` : countOf(total, 'exp'); svg.append(center);
    // The word under the count only where the hole has room for it (the card's 120 px Wheel names its count alone).
    if (small && size >= 160) { const sub = document.createElementNS(svgNS, 'text'); sub.setAttribute('x', String(cx)); sub.setAttribute('y', String(cy + 14)); sub.setAttribute('class', 'center sub'); sub.textContent = word('exp', true); svg.append(sub); }
    svg.addEventListener('click', () => { if (Y.sel) select(null); });
    return svg;
  }
  /* The key beside the Wheel: a row for every line, its number, its label in full and its count. It is
   * where a line's name is read when its arc is too narrow to carry it, and never only on hover. */
  function wheelKey() {
    const {person, order} = wheelLines();
    const hot = (id, on) => { for (const nodeEl of listPane.querySelectorAll(`.ywheel [data-line="${id}"]`)) nodeEl.classList.toggle('hot', on); };
    const items = order.map((line) => node('li', {}, [node('button', {type: 'button', class: 'ykeyrow', 'data-line': String(line.t.theme), 'data-n': String(line.number), 'data-help': 'years.wheel_key', title: themeShown(line.t),
      onclick: () => select({kind: 'theme', id: line.t.theme, person}), onmouseenter: () => hot(line.t.theme, true), onmouseleave: () => hot(line.t.theme, false),
      onfocus: () => hot(line.t.theme, true), onblur: () => hot(line.t.theme, false)}, [
      node('span', {class: 'ynumber', text: String(line.number)}), node('span', {class: 'ylinelabel', text: themeShown(line.t)}), node('span', {class: 'ylinecount', text: countOf(line.n, 'exp')})]) ]));
    return node('div', {class: 'ywheelkey'}, [node('h3', {text: `${cap(word('theme', true))} · ${fmt(order.length)}`}), node('ol', {class: 'ykeylist', 'aria-label': `Every ${word('theme')} around the Wheel`}, items)]);
  }
  /* The key of the topics' band: a ring per topic, outermost last, with its color and how many of the
   * Wheel's experiments it holds. */
  function wheelTopicKey() {
    const {tracks, more, inView} = wheelTracks(wheelLines().order);
    if (!tracks.length) return '';
    return node('div', {class: 'ywheeltopics', 'data-help': 'years.wheel_topics'}, [
      node('h3', {text: 'Your collections'}),
      node('p', {class: 'ynote', text: 'The thin rings outside the Wheel are your collections, the first one made innermost. A ring is drawn where the collection holds an experiment of the Wheel.'}),
      node('ul', {class: 'ykeylist'}, tracks.map((t) => node('li', {class: 'ytopickey'}, [
        node('button', {type: 'button', class: 'topic-chip', style: `--t: ${t.color}`, onclick: () => topicKit.open(t.id)}, [node('span', {class: 'topic-dot', 'aria-hidden': 'true'}), t.name]),
        node('span', {class: 'ylinecount', text: countOf([...t.experiments].filter((id) => inView.has(id)).length, 'exp')}),
      ]))),
      more ? node('p', {class: 'ynote', text: `${fmt(more)} more collection${more === 1 ? '' : 's'} not drawn as rings. Collections lists every one.`}) : '',
    ]);
  }
  function wheelLegend() {
    return node('div', {}, [wheelTopicKey(), node('h3', {text: 'How to read it'}), node('p', {class: 'ynote', text: `Inner ring: ${broadWord()} (where Ingence found them); middle: ${word('theme', true)}; rim: one arc per ${word('exp')}. Angle is how many ${word('exp', true)}. Color shows how much raw data was analyzed, and gray means no raw data was found. An arc too narrow for its name carries its number; the list above names every one.`}),
      legendNode({legend: LEGEND_C, keys: ''}), node('p', {class: 'ynote', text: `The Graph page's Explorer draws the same folders by their contents and bytes; this Wheel draws the ${word('theme', true)} Ingence read from their names.`})]);
  }

  /* ------------------------------------------------------------------ Overlap */

  /* How alike the seeds are, and the resolutions tried: every pair of seeds'
     final placements as dots on the 0 to 1 axis with the chance band, the seed shown, and a small
     strip with one column per resolution (its height the seeds' agreement there, empty where the
     seeds were not compared, the resolution kept marked). */
  function seedPictures(S) {
    const pairs = S.seed_pairs || [];
    const out = [];
    if (pairs.length) {
      const mean = S.seed_ari;
      const same = S.placement_seed === S.medoid_seed;
      out.push(node('div', {class: 'yseedpic'}, [
        node('strong', {text: 'Stability'}),
        axisValue(mean, {band: CHANCE_BAND, dots: pairs.map((p) => p.ari), title: `Each dot compares 2 tries (${pairs.length} pairs): how alike their final placements are (adjusted Rand index). The hatched band near 0 is what random placements give.`, text: `${pairs.length} pairs, mean ${mean == null ? '?' : mean.toFixed(2)}, lowest ${Math.min(...pairs.map((p) => p.ari)).toFixed(2)}; near 0 is chance`}),
        node('span', {class: 'ynote', text: S.medoid_seed == null ? '' : `Shown: seed ${S.placement_seed}${same ? ', the one most like the others' : `; the one most like the others is seed ${S.medoid_seed}`}.`}),
      ]));
    }
    const sweep = (S.sweep || []);
    if (sweep.length) {
      const columns = sweep.map((row) => {
        const kept = S.resolution != null && Math.abs(row.resolution - S.resolution) < 1e-9;
        const height = row.seed_ami == null ? 0 : Math.max(2, Math.round(Math.max(0, row.seed_ami) * 100));
        return node('span', {class: `ysweepcol${kept ? ' kept' : ''}`, title: `Resolution ${row.resolution}: ${row.themes} themes; ${row.seed_ami == null ? 'stability not measured here' : `stability ${row.seed_ami.toFixed(2)}`}${kept ? '; kept' : ''}`}, [
          node('span', {class: `ysweepbar${row.seed_ami == null ? ' na' : ''}`, style: `height:${height}%`}),
          node('span', {class: 'ysweeplab', text: String(row.themes)}),
        ]);
      });
      out.push(node('div', {class: 'yseedpic'}, [
        node('strong', {text: 'Resolutions tried'}),
        node('span', {class: 'ysweep', role: 'img', 'aria-label': `${sweep.length} resolutions tried; the number under each column is its number of themes`}, columns),
        node('span', {class: 'ynote', text: 'Column height: how stable the themes are at that resolution. A dashed line only: not measured there. Outlined: the one kept. The number under a column is its number of themes.'}),
      ]));
    }
    return node('div', {class: 'yseedpics'}, out);
  }

  /* Overlap: how steady each line is from seed to seed, and the words two lines share. At 12 lines
   * or fewer, a matrix of every line against every other is readable and is the default. From 13
   * lines up, the matrix is mostly empty cells. Then the ring with chords (ring.js) draws the pairs
   * that exist, with a table under it that repeats every pair. The one component that every view
   * uses (stability.js) draws each line's stability. */
  async function overlapView({onTheme, redraw}) {
    if (!M.themesComputed) return node('p', {class: 'nothing', text: `${cap(word('theme', true))} are not drawn: ${endSentence(M.themesSaid || 'themes were not computed')}`});
    if (!stabilityHeld) stabilityHeld = await v1('/api/v1/themes/stability');
    // Shared words between lines: the words two lines are made of in common, as the engine finds
    // them (the route lists only the pairs that share a word; a pair not listed shares none).
    if (!overlapHeld) overlapHeld = await v1('/api/v1/themes/overlap');
    const S = stabilityHeld;
    const themes = M.themes;
    const ringApi = window.ingenceRing;
    const manyLines = themes.length >= ringApi.RING_FROM;
    const drawing = manyLines || Y.overlapDraw === 'ring' ? 'ring' : 'matrix';
    const stabilityOf = new Map(S.themes.map((x) => [x.theme, x]));
    const back = (id) => onTheme(id);
    const rows = [node('div', {class: 'ystab head'}, [node('strong', {text: cap(word('theme'))}), node('strong', {text: 'Kept together each time'}), node('strong', {text: 'Core'})])];
    for (const t of themes) {
      const st = stabilityOf.get(t.theme) || {stability: t.stability, core_experiments: t.core_experiments};
      rows.push(node('div', {class: 'ystab'}, [
        node('button', {type: 'button', class: 'link', onclick: () => back(t.theme)}, [themeShown(t)]),
        stabilityAxis(st.stability, {label: themeShown(t), ends: false}),
        node('span', {text: `${fmt(st.core_experiments)} of ${fmt(t.n)}`}),
      ]));
    }
    const sharedBy = new Map(overlapHeld.pairs.map((p) => [`${p.theme_a}|${p.theme_b}`, p]));
    const sharedOf = (a, b) => sharedBy.get(a.theme < b.theme ? `${a.theme}|${b.theme}` : `${b.theme}|${a.theme}`);
    let shared;
    if (drawing === 'ring') {
      const lines = themes.map((t) => ({id: t.theme, label: themeShown(t), experiments: t.n, stability: (stabilityOf.get(t.theme) || t).stability ?? null}));
      const pairs = overlapHeld.pairs.map((p) => ({a: p.theme_a, b: p.theme_b, words: p.words, shared: p.shared, of_smaller: p.of_smaller}));
      shared = node('div', {class: 'yovl'}, [
        node('p', {class: 'eyebrow', text: `Shared words · ${M.ladder.kind === 'archive' ? 'the archive' : `the ${word('project')}`} · ${countOf(themes.length, 'theme')}`}),
        node('h3', {class: 'yringhead', text: `The ring: ${word('theme', true)} by size, a chord where two share a word`}),
        ringApi.ringDrawing(lines, pairs, {lineWord: word('theme'), linesWord: word('theme', true), experimentWord: word('exp'), experimentsWord: word('exp', true), onSelect: back}),
        node('p', {class: 'ynote', text: 'At this count a matrix is mostly empty cells; the ring shows the pairs that exist.'}),
      ]);
    } else {
      const head = node('tr', {}, [node('th', {}), ...themes.map((t) => node('th', {class: 'rot', title: themeShown(t), text: themeShown(t).slice(0, 16)}))]);
      const body = themes.map((a) => node('tr', {}, [node('th', {class: 'rot', title: themeShown(a), text: themeShown(a).slice(0, 22)}), ...themes.map((b) => {
        if (a === b) return node('td', {class: 'cell', style: 'background: var(--y-track)'});
        const pair = sharedOf(a, b);
        const share = Math.min(1, (pair ? pair.shared : 0) / 3);
        return node('td', {class: 'cell', title: pair ? `${themeShown(a)} and ${themeShown(b)} share: ${pair.words.join(', ')}` : 'no word in common', style: `background: color-mix(in srgb, var(--y-yes) ${Math.round(share * 80)}%, var(--y-panel))`});
      })]));
      shared = node('div', {class: 'yovl'}, [node('h3', {text: `Words two ${word('theme', true)} share`}), node('table', {class: 'ymatrix'}, [node('thead', {}, [head]), node('tbody', {}, body)]), node('p', {class: 'ynote', text: `Darker: more of their words in common (hover for the words). How often ${article(word('exp'))} ${word('exp')} would land in another ${word('theme')} when its words are resampled is not computed yet. This table shows the words only.`})]);
    }
    const drawChoice = manyLines ? '' : node('div', {class: 'ybar'}, [node('span', {text: 'Draw'}),
      chip('Matrix', drawing === 'matrix', () => { Y.overlapDraw = 'matrix'; redraw(); }, {help: 'years.overlap_draw'}),
      chip('Ring', drawing === 'ring', () => { Y.overlapDraw = 'ring'; redraw(); }, {help: 'years.overlap_draw'})]);
    return node('div', {class: 'yoverlap', 'data-drawing': drawing}, [
      seedPictures(S),
      node('p', {class: 'yhint', text: `${S.measure} Seeds: ${S.seeds}. ${cap(article(word('theme')))} ${word('theme')} below 0.80 has an uncertain boundary and a dotted separator in the rows.`}),
      drawChoice,
      node('div', {class: 'ybig'}, [node('div', {class: 'ystabs'}, [...rows, node('p', {class: 'ynote', text: '0 is chance and 1 is identical; a tick marks 0.80, under which a boundary is uncertain.'})]), shared]),
    ]);
  }

  /* ------------------------------------------------------------------ keyboard */

  /* The Years rows' keys (nothing here fires while a field has focus):
   * ↑ ↓ walk the rows, → opens a row (one scale down), ← or Backspace goes up, Enter is the inspector
   * for the row (a drawer in a narrow window, or a file's record on the files one by one), W and O the
   * Wheel and Overlap, N the graph of the row's relations, Home and End the first and last row, + and
   * − zoom the axis and 0 resets it. The rows are one tab stop. The arrows move inside. */
  function zoomBy(factor) {
    const A = lastAxis; if (!A || Y.view !== 'rows') return;
    const fullSpan = A.full.t1 - A.full.t0; const next = Math.max(4000, A.span * factor);
    if (next >= fullSpan) { showWholeSpan(); return; }
    const mid = (A.t0 + A.t1) / 2;
    Y.zoom = {key: zoomKey(), t0: mid - next / 2, t1: mid + next / 2};
    renderList();
  }
  function resetZoom() { Y.zoom = null; delete Y.wholeSpan[zoomKey()]; render(); }
  function walkTo(index) {
    if (!lastRows.length) return;
    Y.focusRow = Math.max(0, Math.min(lastRows.length - 1, index)); Y.keyFocus = true;
    const r = lastRows[Y.focusRow]; if (r && r.onClick) r.onClick(); else render();
  }
  /* Enter: the inspector for the row. A file in the one-by-one list opens its record; elsewhere the
   * row is chosen, and the inspector takes the focus (in a narrow window it opens as a drawer). */
  function inspectRow() {
    const r = lastRows[Y.focusRow];
    if (r && r.kind === 'file' && r.onOpen) { r.onOpen(); return; }
    if (r && r.onClick && !r.selected) r.onClick();
    if (NARROW.matches) { openDrawer(); return; }
    detailPane.tabIndex = -1; detailPane.focus();
  }
  function onKey(ev) {
    if (!shown() || !M) return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const t = ev.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    if (typing) return;
    if (document.querySelector('dialog[open]')) return;
    const key = ev.key;
    if (key === 'Escape') {
      if (drawerIsOpen()) { closeDrawer(); return; }
      if (Y.view !== 'rows') { if (NARROW.matches) closeSheet(); else { Y.view = 'rows'; render(); } return; }
      if (Y.sel) select(null);
      return;
    }
    if (PHONE.matches) return;
    if (key === 'Backspace') { ev.preventDefault(); up({focus: true}); return; }
    if (key === 'w' || key === 'W') { if (['project', 'person'].includes(scale().kind) && M.themesComputed) toggleView('wheel'); return; }
    // The stability pictures are in Advanced. O opens them there.
    if (key === 'o' || key === 'O') { if (M.themesComputed && scale().kind !== 'file') navigate('stability'); return; }
    if (key === 'n' || key === 'N') { const s = Y.sel; if (s && (s.kind === 'exp' || s.kind === 'file')) { holdFocus({kind: 'entry', id: s.id, label: nodeName(s)}); navigate('graph', {focus: {kind: 'entry', id: s.id, label: nodeName(s)}}); } return; }
    // T, from a file's record: the record's "Teach a word" control (marked ``data-teach``) opens the
    // parts of its name as a list to teach a word from. Where the record offers none, T does nothing.
    if ((key === 't' || key === 'T') && Y.view === 'rows' && (scale().kind === 'file' || (Y.sel && Y.sel.kind === 'file'))) {
      const control = document.querySelector('#detail [data-teach], #list [data-teach]');
      if (control) { ev.preventDefault(); control.click(); }
      return;
    }
    if (Y.view !== 'rows') return;
    if (key === '+' || key === '=') { ev.preventDefault(); zoomBy(1 / 1.5); return; }
    if (key === '-' || key === '_') { ev.preventDefault(); zoomBy(1.5); return; }
    if (key === '0') { ev.preventDefault(); resetZoom(); return; }
    // The arrows and Enter belong to the rows only while focus is on them or on the page itself, so a
    // button or a link elsewhere keeps its own use of them.
    const onRows = !t || t === document.body || t === document.documentElement || (t.closest && t.closest('.ylist'));
    const rowEl = t && t.closest ? t.closest('.yrow') : null;
    if (rowEl) Y.focusRow = Number(rowEl.dataset.index);
    if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Home' || key === 'End') {
      if (!onRows) return;
      ev.preventDefault();
      const last = lastRows.length - 1;
      const index = key === 'Home' ? 0 : key === 'End' ? last : (Y.focusRow < 0 ? (key === 'ArrowDown' ? 0 : last) : Y.focusRow + (key === 'ArrowDown' ? 1 : -1));
      walkTo(index);
      return;
    }
    // Space picks the row in focus for a topic (the rows that can be picked are lines and experiments).
    if (key === ' ' && rowEl && onRows) { const r = lastRows[Y.focusRow]; if (r && r.pick && window.topicKit) { ev.preventDefault(); togglePick(r.pick); } return; }
    if (key === 'ArrowRight') { if (!onRows) return; const r = lastRows[Y.focusRow]; if (r && r.onOpen) { ev.preventDefault(); Y.keyFocus = true; r.onOpen(); } return; }
    if (key === 'ArrowLeft') { if (!onRows) return; ev.preventDefault(); up({focus: true}); return; }
    // Enter on a button, link or disclosure is that control's own: it must not also open the inspector.
    if (key === 'Enter') { if (t && t.closest && t.closest('button, a, summary, select')) return; if (!onRows || Y.focusRow < 0) return; ev.preventDefault(); inspectRow(); }
  }
  window.addEventListener('keydown', onKey);
  // A pointer takes the focus from the keys: a redraw never moves it to a row then.
  window.addEventListener('pointerdown', () => { Y.keyFocus = false; }, true);
  // Focus that moves to anything but a row (the filter, a chip, the inspector) is no longer the keys'.
  window.addEventListener('focusin', (ev) => { if (!(ev.target.closest && ev.target.closest('.ylist'))) Y.keyFocus = false; });

  /* The search box: a year typed alone asks "what was I working on then" here. */
  const searchBoxNode = document.getElementById('search-box');
  if (searchBoxNode) {
    searchBoxNode.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter') return;
      const typed = searchBoxNode.value.trim();
      if (!/^(19|20)\d{2}$/.test(typed)) return;
      ev.preventDefault(); ev.stopImmediatePropagation();
      const year = Number(typed);
      const show = () => { Y.view = 'rows'; select({kind: 'year', id: year}); };
      if (shown() && M) show(); else navigate('years').then(() => load().then(show));
    }, true);
  }

  /* ------------------------------------------------------------------ the sections */

  /* By year, drawn into ``into``: the box under Overlap's mode bar (overlap.js). */
  async function showYears(into) {
    hostEl = into || null;
    states.loading(host(), `Loading the ${word('project')}'s ${word('theme', true)}...`);
    try {
      await load();
    } catch (error) {
      if (shown()) states.failed(host(), error, () => navigate('years', {push: false}));
      return;
    }
    if (!shown()) return;
    if (!M.exps.length && !M.themesSaid && !M.completenessSaid) { yearsEmpty(); return; }
    // Topics are the person's and optional: read before the first draw only where a lens is set (so the
    // rows are never drawn whole and then narrowed), otherwise after it.
    if (window.topicKit) {
      const reading = topicKit.read().then(() => {
        if (lens && !lensTopic()) setLens('');
        else if (shown() && M && (topicKit.current().topics.length || picked.size)) render();
      }, () => null);
      if (lens) await reading;
    }
    if (!Y.restored) { restorePath(); Y.restored = true; }
    // A path that names something this run no longer holds falls back to the project.
    const ok = Y.path.every((n) => n.kind === 'project' || n.kind === 'unplaced' || (n.kind === 'person' && M.peopleById.has(n.id)) || (n.kind === 'theme' && M.themeById.has(n.id)) || (n.kind === 'exp' && M.byId.has(n.id)) || n.kind === 'file');
    if (!ok) Y.path = [{kind: 'project'}];
    render({first: true});
  }

  /* Engine › Ladders: the project's levels as the engine publishes them (``/api/v1/ladder``), each
   * with the word for it in this root's kind; the page's own table only where the engine serves none. */
  async function showLadders() {
    states.loading(listPane, 'Loading the folder levels...');
    let served = null;
    try { served = await v1('/api/v1/ladder'); } catch (error) { served = null; }
    const levels = served ? served.levels.map((l) => ({name: cap(l.name), rows: l.rows, source: l.source, axis: l.axis, measures: l.measures}))
      : LADDER.map((l) => ({name: l.name, rows: l.rows, source: l.source, axis: l.axis, measures: l.measures}));
    const kinds = served ? [...new Set(served.roots.map((r) => r.kind))] : [];
    const table = el('table', {}, [
      el('thead', {}, [el('tr', {}, ['Level', 'A row is', 'Where it comes from', 'The time axis', 'Measures'].map((h) => el('th', {text: h})))]),
      el('tbody', {}, levels.map((l) => el('tr', {}, [el('td', {text: l.name}), el('td', {text: l.rows}), el('td', {}, [el('code', {text: l.source})]), el('td', {text: l.axis}), el('td', {text: l.measures.join(', ') || '—'})]))),
    ]);
    fill(listPane, el('div', {class: 'yladders', 'data-kind': served ? served.kind : ''}, [el('h2', {text: 'Folder levels for each kind of data folder'}),
      el('p', {class: 'note', text: 'By year climbs the folder levels: what a row is at each level, where its data comes from, its time axis and its colors. The same view draws a laboratory\'s folders and an archive\'s listing.'}),
      served ? el('p', {class: 'note', text: !kinds.length ? 'This project has no data folder yet, so it uses the laboratory\'s levels.' : `This project scans ${kinds.length === 1 ? (kinds[0] === 'archive' ? 'an archive' : 'a laboratory\'s folders') : 'a laboratory\'s folders and an archive\'s listing'}, so it uses ${served.kind === 'archive' ? 'the archive\'s' : 'the laboratory\'s'} levels.`}) : null,
      table, el('p', {class: 'faint', text: LADDER_NOTE})]));
  }

  /* Advanced › Stability: how steady each theme is from seed to seed, the seeds' agreement and
   * the resolutions tried. ``overlapView`` draws these pictures. A theme opens in Overlap's By year
   * mode. */
  async function showStability() {
    const turn = turns.claim(listPane);
    states.loading(listPane, `Loading how stable the ${word('theme', true)} are...`);
    const failed = (error) => {
      if (turns.owns(listPane, turn) && state.view === 'stability') states.failed(listPane, error, () => navigate('stability', {push: false}));
    };
    let drawn;
    try {
      await load();
      drawn = await overlapView({
        onTheme: (id) => navigate('years').then(() => { Y.view = 'rows'; go([{kind: 'project'}]); select({kind: 'theme', id}); }),
        redraw: () => { showStability().catch(() => {}); },
      });
    } catch (error) { failed(error); return; }
    if (!turns.owns(listPane, turn) || state.view !== 'stability') return;
    fill(listPane, node('div', {class: 'years ystability', 'data-view': 'stability'}, [
      node('div', {class: 'yhead'}, [node('h2', {text: 'Stability'})]),
      node('p', {class: 'yhint', text: `How stable each ${word('theme')} is when Ingence groups the ${word('exp', true)} again from other starting points.`}),
      // The ``yovlbox`` class lays the pictures out in one column (years.css).
      node('div', {class: 'yovlbox'}, [drawn])]));
  }

  VIEWS.ladders = showLadders;
  VIEWS.stability = showStability;
  // Advanced holds the Stability view, under Views. app.js places each view on its page once loaded.
  if (PAGES.advanced && !PAGES.advanced.includes('stability')) PAGES.advanced.push('stability');
  SINGLE_VIEWS.add('stability');
  // A topic made, added to, renamed or withdrawn: the lens, the chips and the Wheel's band are read again.
  if (window.topicKit) topicKit.subscribe(() => {
    topicKit.read().then(() => {
      if (lens && !lensTopic()) setLens('');
      else if (shown() && M) render();
    }, () => null);
  });
  if (!PAGES.engine.includes('ladders')) PAGES.engine.push('ladders');
  SINGLE_VIEWS.add('ladders');
  // A re-run that published a new run: the next opening of the view reads it (load compares runs).
  onRefresh(() => { if (shown() && M) load().then((model) => { if (model !== M) render(); }).catch(() => {}); });
  window.ingenceYears = {open, select, go, load, LADDER, lens: setLens, show: showYears,
    // An experiment's files, listed by folder (Overlap's "Show the files").
    files: (id) => go(pathTo({kind: 'exp', id}), {groupBy: 'folder'})};
})();
