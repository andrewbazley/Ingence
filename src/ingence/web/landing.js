// @ts-check
/* The landing page, shown before a project exists or before its first run.
 *
 * It holds:
 *
 * - what Ingence is, in one paragraph;
 * - the projects opened on this computer, a card each: its name, the folder it reads and where it
 *   is written, its runs, Open and Rename;
 * - one card, "Scan a data folder": the folder (typed or pasted, and checked), the project’s name,
 *   the years that count as dates, where the project is saved, the reading depth (Quick, Standard
 *   or Deep), the questions box, and "Start scan". Making the project and starting its census is
 *   one act: one call to the landing’s make route carries all of it (``POST /api/landing/create``
 *   with ``read``). The page does not ask who is deciding. The Dashboard asks for that name at the
 *   first answer;
 * - before a project’s first run (one made by ``ingence init``, or a reading that did not finish),
 *   the same fields for that project;
 * - while it scans: the phase, the step, the counts and the time left, a bar where the total is
 *   known (scan.js), and Stop; the four counts the engine has made so far; the questions box
 *   again, each question’s matches filling in as the scan publishes; the question about what the
 *   top folders are, once the walk ends; and three panels saying what to look at first. When the
 *   scan ends the Dashboard opens by itself.
 *
 * Nothing starts without a click. The only thing that reads a folder is "Start scan".
 *
 * It stands alone: app.js starts the Dashboard as it loads, so this page keeps its own small
 * helpers and shares dialogs.js, the one place a box asks something. No framework, no build step,
 * nothing loaded from anywhere else. */

(() => {
  /** @typedef {any} Json A payload of the landing routes: plain dictionaries, read as they come. */
  /** @typedef {Node | string | null | undefined | false} Part */

  const POLL_MS = 1000;
  /* Where the Dashboard opens when a reading ends: the Overview, which asks a new project’s quick
   * checks first. The path is /index.html and not /, because / is this page’s own address while it
   * follows a reading, and an address that differs only in its fragment is not loaded again: the
   * page would stay where it is. */
  const DASHBOARD_AT_OVERVIEW = '/index.html#overview';
  const YEAR_FLOOR = 1900;
  const YEAR_CEILING = 2200;

  /* The words the page says, other than the opening paragraph. */
  const WORDS = {
    projectsHead: 'Projects on this computer',
    namedByFolder: 'named after its folder',
    newEyebrow: 'New project',
    newHead: 'Scan a data folder',
    folderLabel: 'Data folder',
    folderHelp: 'Type or paste the full path. The browser cannot open a folder chooser here.',
    unknownCount: 'Ingence counts the files during the scan.',
    nameLabel: 'Project name',
    yearsLabel: 'Experiment date range',
    yearsHelp: 'Ingence reads a number in a name as a year only inside this range.',
    whereLabel: 'Save project in',
    writtenHelp: 'Ingence saves the project in this folder. Ingence never changes your files.',
    depthLabel: 'Reading depth',
    depths: [
      ['quick', 'Quick', 'Reads names, sizes and dates.'],
      ['standard', 'Standard', 'Also reads a sample of each file type’s contents.'],
      ['deep', 'Deep', 'Reads every file whole, in the background, with a time limit you set.'],
    ],
    namesOnly: 'This project reads names only, so Quick is the only choice.',
    goalsLabel: 'What do you want to find out from this data?',
    goalsHelp: 'Write one question per line. You can leave this empty.',
    goalsLookFor: 'Ingence will look for:',
    areasHead: 'Top folders',
    firstHead: 'What to look at first',
    stopHelp: 'Closing this page does not stop the scan.',
    carryLabel: 'Words from other projects',
    carryHelp: 'Ingence copies your answers about word meanings from the projects you choose. '
      + 'They wait for Re-run, like any answer. An answer for one folder only is not copied.',
    carryNone: 'Choose at least 1 project, or turn this off.',
    carryWho: 'Ingence saves the copied words under your name. Give your name to copy them.',
    carryNoDecider: 'Ingence did not copy your words, because no name is set on this computer. '
      + 'They are saved in this project as carried-words.json. Set your name, then use Import.',
    readNow: 'Start scan',
    opensItself: 'The Dashboard opens when the scan ends.',
    progress: ['Folders found', 'Files found', 'Total size', 'Rules applied'],
  };

  /** What each kind of top folder is called: one, many, and the choice in its list.
   * @type {{[kind: string]: [string, string]}} */
  const AREA_KINDS = {
    person: ['person', 'people'],
    instrument: ['instrument', 'instruments'],
    date: ['date', 'dates'],
    project: ['project', 'projects'],
    folder: ['plain folder', 'plain folders'],
  };

  /** The state the page keeps between draws: what the server said last, what a person has typed
   * (so a redraw, after a rename or a poll, never empties a field), and whether this window
   * started or followed a first reading. ``goals`` is the questions box: its text, kept before the
   * project exists, and each line saved as a goal with the identifier the engine gave it.
   * @type {{state: Json, decider: Json, timer: number, followed: boolean, draft: Json,
   *   setup: Json, polls: number, goals: {text: string, saved: Map<string, string>, said: string,
   *   syncing: Promise<void> | null, again: boolean, answered: Json},
   *   areas: Json, catalog: Json}} */
  const page = {
    state: null,
    decider: null,
    timer: 0,
    followed: false,
    setup: null,
    polls: 0,
    goals: {text: '', saved: new Map(), said: '', syncing: null, again: false, answered: null},
    areas: null,
    catalog: null,
    draft: {
      root: '', name: '', where: '', whereByHand: false, first: '', last: '', lastByHand: false,
      depth: 'standard', checked: null, wherePlace: null,
      carry: {on: false, chosen: /** @type {string[]} */ ([])},
    },
  };

  /* ------------------------------------------------------------------ helpers */

  /**
   * Make an element: attributes (``class``, ``text``, ``on<event>``, any other attribute) and
   * children.
   * @param {string} tag
   * @param {{[name: string]: any}} [attributes]
   * @param {Part | Part[]} [children]
   * @returns {HTMLElement}
   */
  function el(tag, attributes = {}, children = []) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (value == null || value === false) continue;
      if (name === 'class') node.className = value;
      else if (name === 'text') node.textContent = value;
      else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
      else node.setAttribute(name, value === true ? '' : value);
    }
    for (const child of /** @type {Part[]} */ ([]).concat(children)) {
      if (child == null || child === '' || child === false) continue;
      node.append(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  /**
   * @param {{[name: string]: any}} attributes
   * @returns {HTMLInputElement}
   */
  function field(attributes) {
    return /** @type {HTMLInputElement} */ (el('input', attributes));
  }

  /**
   * @param {{[name: string]: any}} attributes
   * @param {Part | Part[]} text
   * @returns {HTMLButtonElement}
   */
  function button(attributes, text) {
    return /** @type {HTMLButtonElement} */ (el('button', {type: 'button', ...attributes}, text));
  }

  /** @param {Json} body @param {string} fallback @returns {string} */
  function refusalWords(body, fallback) {
    if (!body || body.error == null) return fallback;
    return typeof body.error === 'string' ? body.error : (body.error.message || fallback);
  }

  /** @param {string} path @returns {Promise<Json>} */
  async function api(path) {
    const response = await fetch(path, {cache: 'no-store'});
    const parsed = await response.json().catch(() => null);
    if (!response.ok) throw new Error(refusalWords(parsed, `${response.status} ${response.statusText}`));
    return parsed;
  }

  /** A write carries the header a page on another site cannot set (the server says why).
   * @param {string} path @param {Json} body @returns {Promise<Json>} */
  async function write(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
      body: JSON.stringify(body),
    });
    const parsed = await response.json().catch(() => ({error: response.statusText}));
    if (!response.ok) throw new Error(refusalWords(parsed, response.statusText));
    return parsed;
  }

  /** @param {unknown} error @returns {string} */
  function said(error) {
    return String((error instanceof Error ? error.message : error) || '').trim();
  }

  /** @param {unknown} value @returns {string} */
  const number = (value) => Number(value || 0).toLocaleString('en-US');

  /** @param {unknown} value @param {string} one @param {string} many @returns {string} */
  function counted(value, one, many) {
    return `${number(value)} ${Number(value) === 1 ? one : many}`;
  }

  /** @param {unknown} seconds @returns {string} */
  function duration(seconds) {
    const whole = Math.max(0, Math.round(Number(seconds) || 0));
    if (whole < 60) return `${whole} s`;
    const minutes = Math.floor(whole / 60);
    if (minutes < 60) return `${minutes} min${whole % 60 ? ` ${whole % 60} s` : ''}`;
    return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  }

  /** @param {unknown} bytes @returns {string} */
  function sizeWords(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1e3) return counted(value, 'byte', 'bytes');
    if (value < 1e6) return `${(value / 1e3).toFixed(1)} kB`;
    if (value < 1e9) return `${(value / 1e6).toFixed(1)} MB`;
    return `${(value / 1e9).toFixed(1)} GB`;
  }

  /** "March 5 at 2:30 PM", with the year where it is not this one.
   * @param {unknown} stamp @returns {string} */
  function momentWords(stamp) {
    if (!stamp) return '';
    const when = new Date(String(stamp));
    if (Number.isNaN(when.getTime())) return String(stamp);
    const day = when.toLocaleDateString('en-US', {
      month: 'long', day: 'numeric',
      ...(when.getFullYear() === new Date().getFullYear() ? {} : {year: 'numeric'}),
    });
    const time = when.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
    return `${day} at ${time}`;
  }

  /** "October 13", for a project opened today or before.
   * @param {unknown} stamp @returns {string} */
  function dayWords(stamp) {
    return momentWords(stamp).replace(/ at .*$/, '');
  }

  /** @param {unknown} stamp @returns {string} */
  function clockWords(stamp) {
    const when = new Date(String(stamp || ''));
    return Number.isNaN(when.getTime()) ? ''
      : when.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
  }

  /** @param {unknown} stamp @returns {number | null} */
  function secondsSince(stamp) {
    const when = new Date(String(stamp || ''));
    return Number.isNaN(when.getTime()) ? null : Math.max(0, (Date.now() - when.getTime()) / 1000);
  }

  /** A path as the home folder’s own name: ``~/Ingence Projects/pool``. Display only: the full
   * path is kept in the element’s title.
   * @param {string} path @returns {string} */
  function homely(path) {
    const parent = String((page.state && page.state.projects_parent) || '');
    const home = parent.includes('/') ? parent.slice(0, parent.lastIndexOf('/')) : '';
    return home && (path === home || path.startsWith(`${home}/`)) ? `~${path.slice(home.length)}` : path;
  }

  /** A path in a command a person copies: quoted where it holds a space.
   * @param {string} path @returns {string} */
  function quoted(path) {
    return /[\s\x27"$`\\]/.test(path) ? `"${path.replace(/(["$`\\])/g, '\\$1')}"` : path;
  }

  /** The last name in a path, for "pool, unless you give one".
   * @param {string} path @returns {string} */
  function folderName(path) {
    return path.split(/[\\/]/).filter(Boolean).pop() || '';
  }

  /** The engine’s own name for a thing, small, behind the plain words.
   * @param {string} summary @param {Part[]} parts @returns {HTMLElement} */
  function why(summary, ...parts) {
    return el('details', {class: 'ui-why'}, [el('summary', {text: summary}), el('p', {}, parts)]);
  }

  /** A sentence that something is wrong: a red border and a dot beside the words, never red words.
   * @param {string} words @param {Part[]} more @returns {HTMLElement} */
  function problem(words, ...more) {
    return el('div', {class: 'ui-problem', role: 'alert'}, [
      el('p', {}, [el('span', {class: 'ui-dot', 'aria-hidden': 'true'}), words]), ...more]);
  }

  /** @returns {HTMLElement} */
  function landingPane() {
    return /** @type {HTMLElement} */ (document.getElementById('landing'));
  }

  /* ------------------------------------------------------------------ who decides */

  async function loadDecider() {
    try {
      page.decider = await api('/api/decider');
    } catch (error) {
      page.decider = {decider: null, error: said(error)};
    }
  }

  /** The Dashboard’s own question (review.js askActor), asked from here, for "Say who now". */
  async function askDecider() {
    const known = (page.decider && page.decider.decider) || '';
    const who = await dialogs.ask({
      title: known ? 'Change your name' : 'What is your name?',
      text: 'Ingence saves your name on this computer, outside every project. It saves each answer '
        + 'under this name. Earlier answers keep the name they had.',
      label: 'Your name', value: known, submit: 'Use this name',
      validate: (typed) => (typed.length > 80 ? 'Use 80 characters or fewer.'
        : /[\u0000-\u001f]/.test(typed) ? 'Remove the control characters from the name.' : ''),
    });
    if (!who || who === known) return;
    try {
      page.decider = await write('/api/decider', {decider: who});
    } catch (error) {
      await dialogs.failed(error, 'Ingence could not save the name');
      return;
    }
  }

  /* ------------------------------------------------------------------ the page */

  /** The opening paragraph: never changed here. The release manifest test reads this file’s
   * single-quoted strings to find it, so a straight apostrophe in a comment or a pattern above it
   * would pair the quotes wrongly: use \x27 in a pattern and ’ in a comment. */
  function intro() {
    return el('p', {class: 'ui-lead', text: 'Ingence is a read-only indexing and inference engine for '
      + 'scientific filesystems, with a local Dashboard for exploring them. It reads files’ '
      + 'names, metadata and contents where they already sit, and interprets them through two '
      + 'editable, versioned sources: a Dictionary, which defines what the words in names and '
      + 'files mean, and a Rulebook, the engine’s interpretive logic, which turns that '
      + 'evidence into proposals about how files relate. The Dashboard lets you search and browse '
      + 'what is there, what could not be read, and what the engine concluded and why; where the '
      + 'rules cannot decide, it asks you.'});
  }

  async function load() {
    clearTimeout(page.timer);
    try {
      const [state] = await Promise.all([api('/api/landing/state'), loadDecider()]);
      page.state = state;
    } catch (error) {
      failed(error, load);
      return;
    }
    render();
  }

  /** The reading is over: the Dashboard opens by itself, at the Overview. */
  function openDashboard() {
    window.location.assign(DASHBOARD_AT_OVERVIEW);
  }

  /** @param {unknown} error @param {() => unknown} retry */
  function failed(error, retry) {
    const words = said(error) || 'The server gave no reason.';
    landingPane().replaceChildren(el('div', {class: 'ui-columns'}, [
      el('div', {class: 'ui-left'}, [intro(), problem(`Ingence could not load this page: ${words}`,
        button({class: 'ui-button', onclick: () => retry()}, 'Try again'))])]));
  }

  /** @param {Json} job @returns {Json | null} */
  function firstRun(job) {
    return job && job.kind === 'first_run' ? job : null;
  }

  function render() {
    const state = page.state;
    const project = state.project;
    const job = firstRun(state.job);
    const busy = Boolean(job && ['queued', 'running'].includes(job.state));
    const back = document.getElementById('back-to-dashboard');
    if (back) back.hidden = !(project && project.exists && project.has_run && !busy);
    const chip = document.getElementById('ui-version');
    if (chip && state.version) {
      chip.textContent = state.version;
      chip.title = `Ingence ${state.version}`;
      chip.hidden = false;
    }
    if (!page.draft.first) {
      page.draft.first = String(state.default_years[0]);
      page.draft.last = String(state.this_year);
    }
    if (page.followed === false && location.hash.startsWith('#reading')) page.followed = true;

    /** @type {Part[]} */
    const right = [];
    /** The counters, under both columns, while a reading runs, and the three panels under them.
     * @type {HTMLElement | null} */
    let row = null;
    /** @type {HTMLElement | null} */
    let panels = null;
    if (project && project.exists) {
      if (busy) {
        page.followed = true;
        right.push(el('section', {id: 'ui-progress', class: 'card ui-form ui-progress'},
          progressParts(project, job)));
        if (showsCounters(job)) {
          right.push(goalsSection(), el('section', {id: 'ui-areas', class: 'card ui-form ui-areas',
            'aria-labelledby': 'ui-areas-head', hidden: !page.areas}, page.areas ? areaParts(page.areas) : []));
          row = el('div', {id: 'ui-counters', class: 'ui-counters', role: 'group',
            'aria-label': 'Found so far'}, counterParts(job));
          panels = firstPanels();
        }
      } else if (job && job.state === 'cancelled' && page.followed) {
        right.push(el('section', {id: 'ui-progress', class: 'card ui-form'}, stoppedParts(project, job)));
        if (!project.has_run) right.push(setupCard(project));
      } else if (job && job.state === 'failed' && page.followed) {
        right.push(el('section', {id: 'ui-progress', class: 'card ui-form'}, failedParts(project, job)));
        if (!project.has_run) right.push(setupCard(project));
      } else if (job && job.state === 'done' && page.followed) {
        // The reading is over and the Dashboard opens by itself, at the Overview; a question typed
        // before the project existed is saved first, however short the scan was.
        if (unsavedGoals()) syncGoals().then(openDashboard, openDashboard);
        else openDashboard();
        return;
      } else if (!project.has_run) {
        if (job && job.state === 'failed') {
          right.push(el('section', {class: 'card ui-form'}, failedParts(project, job)));
        }
        right.push(setupCard(project));
      }
    } else if (project && !project.exists) {
      right.push(problem(`The project for this window is missing: ${project.why}`));
    }
    const unread = Boolean(project && project.exists && !busy && !project.has_run);
    if (!busy) {
      right.push(unread
        ? el('details', {class: 'ui-elsewhere'}, [el('summary', {text: 'Scan a different data folder'}),
          newProjectCard()])
        : newProjectCard());
    }

    /** @type {Part[]} */
    const left = [intro()];
    const recent = recentSection(state, project);
    if (recent) left.push(recent);
    landingPane().replaceChildren(el('div', {class: 'ui-columns'}, [
      el('div', {class: 'ui-left'}, left),
      el('div', {class: 'ui-right'}, right),
      row, panels]));
    if (busy) {
      // The project exists: the questions typed before it did are saved as its goals now.
      if (goalLines(page.goals.text).length) syncGoals();
      if (showsCounters(job)) scanExtras(job);
      follow();
    }
  }

  /* ------------------------------------------------------------------ the projects */

  /** @param {Json} state @param {Json} open @returns {HTMLElement | null} */
  function recentSection(state, open) {
    const listed = state.recent || [];
    // A machine with no project shows the paragraph and the card, and no section at all.
    if (!listed.length) return null;
    const reading = Boolean(open && state.job && state.job.kind === 'first_run'
      && ['queued', 'running'].includes(state.job.state));
    const rows = listed.map((/** @type {Json} */ item) => projectCard(item, open, reading));
    return el('section', {class: 'ui-recent', 'aria-labelledby': 'ui-projects-head'}, [
      el('div', {class: 'ui-section-head'}, [
        el('h2', {id: 'ui-projects-head', text: WORDS.projectsHead}),
        el('span', {class: 'ui-count', text: `${listed.length} of ${listed.length}`})]),
      el('ul', {class: 'ui-projects'}, rows)]);
  }

  /** @param {Json} item @param {Json} open @param {boolean} reading @returns {HTMLElement} */
  function projectCard(item, open, reading) {
    const here = Boolean(open && open.path === item.path);
    if (!item.exists) {
      return el('li', {class: 'card ui-project ui-gone', 'data-project': item.path}, [
        el('h3', {text: item.name}),
        el('p', {class: 'ui-reads', title: item.path, text: homely(item.path)}),
        problem(`Missing. ${item.why || ''}`)]);
    }
    const status = el('p', {class: 'ui-status', role: 'status'});
    const opener = button({class: 'ui-button ui-open', 'data-project': item.path,
      'aria-label': `Open ${item.name}`, onclick: () => openProject(item, status)}, 'Open');
    // Rename is offered where the project has no name of its own: its folder’s stands in.
    const [rename, form] = item.named_by_folder ? renameControl(item, status) : [null, null];
    const reads = (item.roots || []).map((/** @type {Json} */ root) => homely(root.path)).join(', ');
    return el('li', {class: 'card ui-project', 'data-project': item.path}, [
      opener,
      el('h3', {}, [item.name,
        rename ? el('span', {class: 'ui-aside'}, [` · ${WORDS.namedByFolder} · `, rename]) : null,
        here ? el('span', {class: 'ui-aside', text: ' · open in this window'}) : null]),
      el('p', {class: 'ui-reads', title: item.path}, [
        reads ? `scans ${reads}` : 'no data folder set', ` · saved in ${homely(item.path)}`]),
      el('p', {class: 'ui-runs', text: here && reading ? 'Scanning now' : runWords(item)}),
      here && item.has_run && !reading ? wordsLine() : null,
      form,
      status]);
  }

  /** Once, quietly, after a first run: how many words cover how many of the word parts the engine
   * could not read, and the way to Words. Nothing where the queue says nothing.
   * @returns {HTMLElement} */
  function wordsLine() {
    const line = el('p', {class: 'ui-words', hidden: ''});
    api('/api/v1/words/queue?limit=20').then((queue) => {
      const rows = queue.rows || [];
      const covered = rows.length ? Number(rows[rows.length - 1].covered) || 0 : 0;
      if (!covered || !Number(queue.unknown_parts)) return;
      line.replaceChildren(`${counted(rows.length, 'word', 'words')} ${rows.length === 1 ? 'covers' : 'cover'} `
        + `${number(covered)} of the ${number(queue.unknown_parts)} word parts Ingence does not know. `
        + 'Add their meanings. ', el('a', {href: '/index.html#words', text: 'Open Words'}));
      line.hidden = false;
    }, () => {});
    return line;
  }

  /** @param {Json} item @returns {string} */
  function runWords(item) {
    const opened = item.opened_at ? ` · opened ${dayWords(item.opened_at)}` : '';
    if (!item.has_run) return `Not scanned yet${opened}`;
    const runs = item.runs == null ? 'Ingence could not count the scans' : counted(item.runs, 'scan', 'scans');
    return `${runs} · the last on ${momentWords(item.last_run_at)}${opened}`;
  }

  /** A project’s name, changed in place: the name is written to the project’s own file, so any
   * computer that opens the project shows it. Returns the link that opens the field and the field
   * (hidden until then).
   * @param {Json} item @param {HTMLElement} status @returns {[HTMLElement, HTMLElement]} */
  function renameControl(item, status) {
    const input = field({type: 'text', value: item.name, maxlength: '200',
      'aria-label': `New name for ${item.name}`, class: 'ui-rename-name'});
    const save = button({class: 'ui-button ui-rename-save'}, 'Save name');
    const form = el('div', {class: 'ui-rename', hidden: ''}, [input, save]);
    const open = button({class: 'link ui-rename-open', 'data-rename': item.path,
      'aria-label': `Rename ${item.name}`, onclick: () => {
        form.hidden = !form.hidden;
        if (!form.hidden) input.select();
      }}, 'Rename');
    save.addEventListener('click', async () => {
      save.disabled = true;
      try {
        await write('/api/landing/rename', {project: item.path, name: input.value.trim()});
        await load();
      } catch (error) {
        save.disabled = false;
        status.replaceChildren(problem(`Ingence could not rename it: ${said(error)}`));
      }
    });
    return [open, form];
  }

  /** @param {Json} item @param {HTMLElement} status */
  async function openProject(item, status) {
    status.textContent = 'Opening...';
    try {
      goTo(await write('/api/landing/open', {project: item.path}), false);
    } catch (error) {
      status.replaceChildren(problem(`Ingence could not open it: ${said(error)}`));
    }
  }

  /** Where to go once a project is open here, or in a window of its own beside this one; and,
   * where a reading was started, to its page.
   * @param {Json} opened @param {boolean} reading */
  function goTo(opened, reading) {
    if (opened.url && opened.url !== '/') {
      // Another server reads the new project, at another address: the questions typed go with the
      // page in the address’s fragment (a browser never sends it to a server), and that page
      // saves them as goals (``takeGoalsFromAddress``).
      const lines = goalLines(page.goals.text).filter((line) => !page.goals.saved.has(line));
      const carried = lines.length ? `&goals=${encodeURIComponent(JSON.stringify(lines))}` : '';
      window.location.assign(reading ? `${opened.url}#reading${carried}` : opened.url);
    } else if (!reading && opened.project && opened.project.has_run) {
      window.location.assign('/');
    } else {
      load();
    }
  }

  /* ------------------------------------------------------------------ the fields a reading needs */

  /** What a person has typed into the fields of a project made and not yet read, kept while the
   * page redraws; begun again from the project’s own settings when another project is shown.
   * @param {Json} project @returns {Json} */
  function setupDraft(project) {
    if (page.setup && page.setup.path === project.path) return page.setup;
    const stated = project.years;
    const present = !stated || stated[1] === 'present';
    const job = firstRun(page.state.job);
    page.setup = {
      path: project.path, first: String(stated ? stated[0] : page.state.default_years[0]),
      last: String(present ? page.state.this_year : stated[1]), lastByHand: !present,
      // Nothing about the depth is kept in the project. A scan that did not finish says what it
      // asked (its job is in the ledger); a project never asked starts at Standard.
      depth: job && !(job.operations || []).includes('dive') ? 'quick' : 'standard',
    };
    return page.setup;
  }

  /** The years and the reading depth: the things a scan asks that a folder cannot say, built once
   * for the new-project card and for a project made and not yet read. ``project`` is that project,
   * where there is one; ``prefix`` keeps the two cards’ identifiers apart.
   * @param {Json | null} project @param {string} prefix
   * @returns {{years: HTMLElement, settings: HTMLElement, read: () => Json | null,
   *   problem: HTMLElement}} */
  function readingFields(project, prefix) {
    const draft = project ? setupDraft(project) : page.draft;
    let present = !draft.lastByHand;
    const first = field({id: `${prefix}-first-year`, type: 'number', min: String(YEAR_FLOOR),
      max: String(YEAR_CEILING), value: draft.first, class: 'ui-year', 'aria-label': 'First year'});
    const last = field({id: `${prefix}-last-year`, type: 'number', min: String(YEAR_FLOOR),
      max: String(YEAR_CEILING), value: draft.last, class: 'ui-year', 'aria-label': 'Last year'});
    first.addEventListener('input', () => { draft.first = first.value; });
    last.addEventListener('input', () => {
      draft.last = last.value;
      draft.lastByHand = true;
      present = false;
    });
    const years = el('div', {class: 'ui-field', role: 'group',
      'aria-labelledby': `${prefix}-years-label`}, [
      el('span', {class: 'ui-label', id: `${prefix}-years-label`, text: WORDS.yearsLabel}),
      el('div', {class: 'ui-years'}, [first, '–', last]),
      el('p', {class: 'ui-help', text: WORDS.yearsHelp})]);

    // Reading depth: Quick, Standard (the default) or Deep, one line each. The project’s depth may
    // open no file at all (ingence init --depth metadata): Quick is then the only choice, and the
    // page says so rather than promising what will not happen.
    const never = Boolean(project && project.dive_opens_files === false);
    if (never) draft.depth = 'quick';
    const choices = WORDS.depths.map(([value, name, line]) => {
      const radio = field({type: 'radio', name: `${prefix}-depth`, id: `${prefix}-depth-${value}`,
        value, checked: draft.depth === value, disabled: never && value !== 'quick',
        'aria-describedby': `${prefix}-depth-${value}-help`});
      radio.addEventListener('change', () => { if (radio.checked) draft.depth = value; });
      return el('div', {class: 'ui-depth-choice'}, [radio,
        el('label', {for: `${prefix}-depth-${value}`}, [
          el('span', {class: 'ui-depth-name', text: name}),
          el('span', {class: 'ui-help', id: `${prefix}-depth-${value}-help`, text: line})])]);
    });
    const trouble = el('div', {class: 'ui-status', role: 'status'});
    const settings = el('fieldset', {class: 'ui-settings ui-depth', id: `${prefix}-depth`}, [
      el('legend', {class: 'ui-setting-name', text: WORDS.depthLabel}),
      ...choices,
      never ? el('p', {class: 'ui-help', text: WORDS.namesOnly}) : null]);

    /** What the person gave, or null with the reason said where the page says what is wrong.
     * @returns {Json | null} */
    function readValues() {
      const begin = Number(first.value);
      const lastNumber = Number(last.value);
      const toNow = present && !draft.lastByHand;
      trouble.replaceChildren();
      if (!first.value.trim() || !Number.isInteger(begin) || (!toNow && !Number.isInteger(lastNumber))) {
        trouble.replaceChildren(problem('Type whole years, such as 2013.'));
        return null;
      }
      if (!toNow && begin > lastNumber) {
        trouble.replaceChildren(problem(`The first year (${begin}) is after the last (${lastNumber}).`));
        return null;
      }
      for (const year of toNow ? [begin] : [begin, lastNumber]) {
        if (year < YEAR_FLOOR || year > YEAR_CEILING) {
          trouble.replaceChildren(problem(`Use a year from ${YEAR_FLOOR} to ${YEAR_CEILING}.`));
          return null;
        }
      }
      const depth = never ? 'quick' : String(draft.depth || 'standard');
      // The route reads ``dive`` to decide whether the first run opens files: Quick opens none,
      // Standard and Deep do. It saves ``depth`` to the project’s settings.
      return {years: [begin, toNow ? 'present' : lastNumber], depth, dive: depth !== 'quick'};
    }
    return {years, settings, read: readValues, problem: trouble};
  }

  /* ------------------------------------------------------------------ a new project */

  /** The card "Scan a data folder": making the project and starting the census is one act.
   * @returns {HTMLElement} */
  function newProjectCard() {
    const draft = page.draft;
    const rootInput = field({id: 'ui-root', type: 'text', autocomplete: 'off', spellcheck: 'false',
      class: 'ui-path', value: draft.root, 'aria-describedby': 'ui-root-hint'});
    const checkButton = button({id: 'ui-check-button', class: 'ui-button'}, 'Check');
    const checked = el('div', {id: 'ui-check', class: 'ui-check', 'aria-live': 'polite'});
    const nameInput = field({id: 'ui-name', type: 'text', autocomplete: 'off', value: draft.name});
    const whereInput = field({id: 'ui-where', type: 'text', autocomplete: 'off', spellcheck: 'false',
      class: 'ui-path', value: draft.where || `${homely(page.state.projects_parent)}/`,
      'aria-describedby': 'ui-where-help ui-where-said'});
    const whereSaid = el('div', {id: 'ui-where-said', class: 'ui-where-said', 'aria-live': 'polite'});
    const status = el('div', {class: 'ui-status', role: 'status'});
    const readButton = button({id: 'ui-read', class: 'ui-button ui-primary'}, WORDS.readNow);
    const fields = readingFields(null, 'ui');
    const carry = carryField();
    /** The sentence the engine said of where the project would be written, when it is a refusal. */
    let whereRefusal = '';

    /** The folder the name placeholder follows: the one checked, else what is typed. */
    function placeholder() {
      const named = (draft.checked && draft.checked.path ? folderName(draft.checked.path)
        : folderName(rootInput.value.trim())) || 'the folder’s name';
      nameInput.placeholder = `${named}, if you leave this empty`;
    }
    placeholder();
    if (draft.checked) checked.replaceChildren(...checkParts(draft.checked));

    function updateRead() {
      const unreadable = Boolean(draft.checked && draft.checked.refusal);
      readButton.disabled = !rootInput.value.trim() || unreadable || Boolean(whereRefusal);
    }

    /** Check the folder: its own listing only, never what lies beneath it.
     * @returns {Promise<Json | null>} */
    async function check() {
      const typed = rootInput.value.trim();
      checked.replaceChildren(el('p', {class: 'ui-quiet', text: 'Checking...'}));
      try {
        draft.checked = await api(`/api/landing/check?path=${encodeURIComponent(typed)}`);
      } catch (error) {
        draft.checked = {typed, refusal: `Not readable: ${said(error)}.`};
      }
      checked.replaceChildren(...checkParts(draft.checked));
      placeholder();
      if (!draft.checked.refusal) await propose();
      updateRead();
      return draft.checked;
    }

    /** The newest question about where the project goes. A reply to an older one is dropped, so
     * a slow answer to what was typed a moment ago never overwrites the answer to what is typed
     * now. */
    let asked = 0;

    /** Ask where the project would be written, and say why not where it cannot be. */
    async function propose() {
      const mine = ++asked;
      const folder = draft.checked && draft.checked.path ? folderName(draft.checked.path) : '';
      const name = nameInput.value.trim() || folder;
      const typed = draft.whereByHand ? whereInput.value.trim() : '';
      const root = draft.checked && draft.checked.path ? draft.checked.path : '';
      try {
        const place = await api(`/api/landing/project-folder?name=${encodeURIComponent(name)}`
          + (typed ? `&path=${encodeURIComponent(typed)}` : '')
          + (root ? `&root=${encodeURIComponent(root)}` : ''));
        if (mine !== asked) return;
        if (!draft.whereByHand) {
          whereInput.value = homely(place.proposed);
          draft.where = whereInput.value;
        }
        whereRefusal = place.refusal || '';
        whereSaid.replaceChildren(...whereParts(place));
      } catch (error) {
        if (mine !== asked) return;
        whereRefusal = '';
        whereSaid.replaceChildren(problem(`Ingence could not check this place: ${said(error)}`));
      }
      updateRead();
    }

    /** The sentence about where the project goes: a refusal; for a project already written there
     * that reads this folder, the way to open it instead; for one that reads another folder, a
     * place that is free.
     * @param {Json} place @returns {HTMLElement[]} */
    function whereParts(place) {
      if (!place.refusal) return [];
      const trouble = (/** @type {Part[]} */ ...parts) => el('div', {class: 'ui-problem', role: 'alert'},
        [el('p', {}, [el('span', {class: 'ui-dot', 'aria-hidden': 'true'}), ...parts])]);
      if (place.existing && place.existing_reads_root) {
        const status = el('span', {role: 'status'});
        return [trouble(`${place.refusal.replace(/\.$/, '')} · `,
          button({class: 'link ui-open-existing', onclick: () => openProject(
            {path: place.existing, name: 'this project'}, status)}, 'Open it instead'), ' ', status)];
      }
      if (place.alternative) {
        const elsewhere = button({class: 'link ui-use-another', onclick: () => {
          whereInput.value = homely(place.alternative);
          draft.whereByHand = true;
          draft.where = whereInput.value;
          propose();
        }}, `Save it in ${homely(place.alternative)}`);
        return [trouble(`${place.refusal} `, elsewhere)];
      }
      return [problem(place.refusal)];
    }

    /** "Start scan": check the folder if it has not been, then make the project and start the
     * census in one call. Nothing is written if any part is refused. */
    async function readNow() {
      if (readButton.disabled) return;
      status.replaceChildren();
      const values = fields.read();
      if (!values) return;
      if (draft.carry.on && !draft.carry.chosen.length) {
        status.replaceChildren(problem(WORDS.carryNone));
        return;
      }
      if (draft.carry.on && !(page.decider && page.decider.decider)) {
        // The copied words are saved under a name: asked now, the one place this page asks it.
        await askDecider();
        if (!(page.decider && page.decider.decider)) {
          status.replaceChildren(problem(WORDS.carryWho));
          return;
        }
      }
      readButton.disabled = true;
      try {
        const typed = rootInput.value.trim();
        if (!draft.checked || draft.checked.typed !== typed) await check();
        if (!draft.checked || draft.checked.refusal) return;
        if (whereRefusal) return;
        status.replaceChildren(el('p', {class: 'ui-quiet', text: 'Starting...'}));
        const lines = goalLines(page.goals.text);
        const made = await write('/api/landing/create', {
          root: draft.checked.path, name: nameInput.value.trim(), project: whereInput.value.trim(),
          years: values.years, dive: {enabled: values.dive}, depth: values.depth, read: true,
          ...(lines.length ? {goals: lines} : {}),
          ...(draft.carry.on ? {carry: draft.carry.chosen} : {})});
        page.followed = true;
        // An engine that saves the questions with the project says so; the page saves the rest.
        if (Array.isArray(made.goals)) {
          for (const goal of made.goals) {
            if (goal && goal.text) page.goals.saved.set(String(goal.text), String(goal.id || ''));
          }
        }
        draft.root = ''; draft.name = ''; draft.where = ''; draft.whereByHand = false;
        draft.checked = null; draft.carry = {on: false, chosen: []};
        goTo(made, true);
      } catch (error) {
        status.replaceChildren(problem(said(error) || 'Ingence could not make the project.'));
      } finally {
        updateRead();
      }
    }

    rootInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); check(); }
    });
    rootInput.addEventListener('input', () => {
      draft.root = rootInput.value;
      draft.checked = null;
      asked += 1;
      checked.replaceChildren();
      whereRefusal = '';
      whereSaid.replaceChildren();
      placeholder();
      updateRead();
    });
    checkButton.addEventListener('click', () => { check(); });
    nameInput.addEventListener('input', () => { draft.name = nameInput.value; propose(); });
    whereInput.addEventListener('input', () => {
      draft.whereByHand = true;
      draft.where = whereInput.value;
    });
    whereInput.addEventListener('change', () => propose());
    readButton.addEventListener('click', () => { readNow(); });
    updateRead();
    if (draft.checked && !draft.checked.refusal) propose();

    return el('section', {class: 'card ui-form', 'aria-labelledby': 'ui-new-head'}, [
      el('div', {}, [el('p', {class: 'eyebrow', text: WORDS.newEyebrow}),
        el('h2', {id: 'ui-new-head', text: WORDS.newHead})]),
      el('div', {class: 'ui-field'}, [
        el('label', {for: 'ui-root', text: WORDS.folderLabel}),
        el('div', {class: 'ui-row'}, [rootInput, checkButton]),
        el('p', {class: 'ui-help', id: 'ui-root-hint', text: WORDS.folderHelp})]),
      checked,
      el('div', {class: 'ui-two'}, [
        el('div', {class: 'ui-field'}, [el('label', {for: 'ui-name', text: WORDS.nameLabel}),
          nameInput]),
        fields.years]),
      el('div', {class: 'ui-field'}, [
        el('label', {for: 'ui-where', text: WORDS.whereLabel}),
        whereInput,
        el('p', {class: 'ui-help', id: 'ui-where-help', text: WORDS.writtenHelp}),
        whereSaid]),
      fields.settings,
      carry,
      goalsField('ui-goals'),
      fields.problem,
      el('div', {class: 'ui-go'}, [readButton,
        el('span', {class: 'ui-help', text: WORDS.opensItself})]),
      status]);
  }

  /** "Bring the words you taught in": off by default, and offered only where an earlier project
   * holds answers about words that can travel. Turned on, it lists those projects, each with how
   * many answers it holds and how many stay behind; the ones chosen go with the new project.
   * @returns {HTMLElement | null} */
  function carryField() {
    const choices = /** @type {Json[]} */ (page.state.carry || []);
    if (!choices.length) return null;
    const draft = page.draft.carry;
    const on = field({id: 'ui-carry', type: 'checkbox', role: 'switch', class: 'ui-switch',
      'aria-describedby': 'ui-carry-help', checked: draft.on});
    const list = el('ul', {class: 'ui-carry-list', id: 'ui-carry-list', 'aria-label': 'Projects to copy words from'},
      choices.map((item, at) => {
        const box = field({id: `ui-carry-${at}`, type: 'checkbox', value: item.path,
          checked: draft.chosen.includes(item.path)});
        box.addEventListener('change', () => {
          draft.chosen = box.checked
            ? [...draft.chosen, item.path] : draft.chosen.filter((/** @type {string} */ path) => path !== item.path);
        });
        const behind = item.left_out
          ? `; Ingence leaves out ${counted(item.left_out, 'answer', 'answers')} made for one folder only`
          : '';
        return el('li', {}, [box, ' ',
          el('label', {for: `ui-carry-${at}`, text: `${item.name}: ${counted(item.words, 'answer', 'answers')}${behind}`})]);
      }));
    list.hidden = !draft.on;
    on.addEventListener('change', () => { draft.on = on.checked; list.hidden = !on.checked; });
    return el('div', {class: 'ui-settings ui-carry'}, [
      el('div', {class: 'ui-setting'}, [
        el('div', {class: 'ui-setting-text'}, [
          el('label', {class: 'ui-setting-name', for: 'ui-carry', text: WORDS.carryLabel}),
          el('p', {class: 'ui-help', id: 'ui-carry-help', text: WORDS.carryHelp})]),
        on]),
      list]);
  }

  /** The words a project was asked to bring in are written once its first reading has ended: an
   * answer is made against a run, and the project had none. Said if it fails, so nobody believes
   * their words arrived when they did not. With nobody set as deciding it says so, since the
   * words are written under a name and the page asks for one before it starts.
   * @returns {Promise<string | null>} the sentence of a failure, else null */
  async function bringWordsIn() {
    const asked = page.state && page.state.carried;
    if (!asked || !asked.staged || asked.applied != null) return null;
    const actor = (page.decider && page.decider.decider) || '';
    if (!actor) return WORDS.carryNoDecider;
    try {
      await write('/api/landing/carry', {actor});
      return null;
    } catch (error) {
      return `Ingence could not copy your words: ${said(error)}`;
    }
  }

  /** The Check result as a note: green dot and "Readable." with what the folder holds at its top,
   * or the red dot and the engine’s own sentence. A file count is not given: it is known only when
   * the reading runs.
   * @param {Json} result @returns {HTMLElement[]} */
  function checkParts(result) {
    if (result.refusal) return [problem(result.refusal)];
    const folders = counted(result.folders_count, 'folder', 'folders');
    return [el('div', {class: 'ui-note'}, [
      el('p', {}, [el('span', {class: 'ui-dot ui-ok', 'aria-hidden': 'true'}),
        el('strong', {text: 'Readable.'}),
        ` ${folders} at the top. Ingence reads each one as an area.`]),
      el('p', {class: 'ui-quiet', text: WORDS.unknownCount})])];
  }

  /* ------------------------------------------------------------------ before the first run */

  /** A project made and not yet read (one ``ingence init`` made, or a reading that did not
   * finish): the same fields, for that project. "Start scan" starts its census.
   * @param {Json} project @returns {HTMLElement} */
  function setupCard(project) {
    const fields = readingFields(project, 'ui-setup');
    const status = el('div', {class: 'ui-status', role: 'status'});
    const readButton = button({id: 'ui-setup-read', class: 'ui-button ui-primary'}, WORDS.readNow);
    const roots = (project.roots || []).map((/** @type {Json} */ root) => root.path);

    async function readNow() {
      status.replaceChildren();
      const values = fields.read();
      if (!values) return;
      readButton.disabled = true;
      try {
        await write('/api/landing/first-run', {years: values.years, dive: values.dive,
          depth: values.depth});
        page.followed = true;
        // The project exists: the questions typed are saved as its goals now.
        syncGoals();
        await load();
      } catch (error) {
        readButton.disabled = false;
        status.replaceChildren(problem(said(error) || 'The scan did not start.'));
      }
    }
    readButton.addEventListener('click', () => { readNow(); });
    return el('section', {id: 'ui-setup', class: 'card ui-form', 'aria-labelledby': 'ui-setup-head'}, [
      el('div', {}, [el('p', {class: 'eyebrow', text: 'Not scanned yet'}),
        el('h2', {id: 'ui-setup-head', text: project.name})]),
      el('p', {class: 'ui-reads-line', title: roots.join(', ')}, [
        roots.length ? `scans ${roots.map(homely).join(', ')}` : 'No data folder is set. '
          + 'Add one with ingence init --root.', ` · saved in ${homely(project.path)}`]),
      el('p', {class: 'ui-help', text: WORDS.writtenHelp}),
      fields.years,
      fields.settings,
      goalsField('ui-setup-goals'),
      fields.problem,
      el('div', {class: 'ui-go'}, [readButton,
        el('span', {class: 'ui-help', text: WORDS.opensItself})]),
      status]);
  }

  /* ------------------------------------------------------------------ while it reads */

  /** One figure of the four: the engine’s own count, or a dash where it is not yet known, never 0.
   * @param {string} name @param {string} label @param {Part[]} figure @param {string} [after]
   * @returns {HTMLElement} */
  function counter(name, label, figure, after) {
    return el('div', {class: 'card ui-counter', 'data-counter': name}, [
      el('p', {class: 'eyebrow', text: label}),
      el('p', {class: 'ui-figure'}, figure),
      after ? el('p', {class: 'ui-after', text: after}) : null]);
  }

  /** The four counters, one row across the page: the engine’s own counts, a dash where one is not
   * yet known. Never a percentage.
   * @param {Json} job @returns {HTMLElement[]} */
  function counterParts(job) {
    const counters = job.counters || {};
    const known = (/** @type {unknown} */ value) => value != null;
    const rulesKnown = known(counters.rules_run) && known(counters.rules_total);
    return [
      counter('folders_found', WORDS.progress[0],
        [known(counters.folders_found) ? number(counters.folders_found) : '–']),
      counter('files_found', WORDS.progress[1],
        [known(counters.files_found) ? number(counters.files_found) : '–']),
      counter('bytes_seen', WORDS.progress[2],
        [known(counters.bytes_seen) ? sizeWords(counters.bytes_seen) : '–']),
      counter('rules_run', WORDS.progress[3],
        rulesKnown ? [number(counters.rules_run), ' ',
          el('span', {class: 'ui-of', text: `of ${number(counters.rules_total)}`})] : ['–'],
        rulesKnown || jobPhase(job) === 'rules' ? undefined : 'after the file list')];
  }

  /** Whether the four counters are shown for this job: not for one a close stopped, which waits
   * for a click.
   * @param {Json} job @returns {boolean} */
  function showsCounters(job) {
    return !(job.state === 'queued' && job.stopped);
  }

  /** Where a job is, as the phase names: listing, reading, names, rules, views; queued while it
   * waits. The phase words that engines before 0.4.0 send name the same steps.
   * @param {Json} job @returns {string} */
  function jobPhase(job) {
    const phase = String((job && job.phase) || '');
    const older = /** @type {{[name: string]: string}} */ ({census: 'listing', dive: 'reading',
      binding: 'names', inferring: 'rules'});
    if (job && job.state === 'queued') return 'queued';
    return older[phase] || phase;
  }

  /** The card at the right while it scans: what it scans and since when, then the phase, the step,
   * the counts and the time left (scan.js), redrawn each second, then Stop, which is not.
   * @param {Json} project @param {Json} job @returns {Part[]} */
  function progressParts(project, job) {
    /** @type {Part[]} */
    const parts = [el('div', {}, [el('p', {class: 'eyebrow', text: 'Scanning'}),
      el('h2', {text: project.name})])];
    if (job.state === 'queued' && job.stopped) {
      parts.push(el('p', {text: 'The scan stopped when Ingence closed. Start scan continues from '
        + 'where it stopped.'}));
      const again = button({class: 'ui-button ui-primary'}, WORDS.readNow);
      const trouble = el('div', {class: 'ui-status', role: 'status'});
      again.addEventListener('click', async () => {
        again.disabled = true;
        try {
          await write('/api/landing/first-run', {dive: (job.operations || []).includes('dive')});
          await load();
        } catch (error) {
          again.disabled = false;
          trouble.replaceChildren(problem(`The scan did not start: ${said(error)}`));
        }
      });
      parts.push(again, trouble);
      return parts;
    }
    parts.push(el('div', {id: 'ui-progress-said', class: 'ui-progress-said'}, progressSaid(project, job)));
    parts.push(stopRow(job));
    return parts;
  }

  /** The lines redrawn each second: what it scans and since when; the phase and its counts, with a
   * bar where the total is known; what could not be read; an attempt started again; a silence.
   * @param {Json} project @param {Json} job @returns {Part[]} */
  function progressSaid(project, job) {
    const roots = (project.roots || []).map((/** @type {Json} */ root) => root.path).join(', ');
    const since = clockWords(job.started_at);
    const phase = jobPhase(job);
    const scan = /** @type {any} */ (window).ingenceScan;
    const totalKnown = typeof job.total === 'number';
    /** @type {Part[]} */
    const parts = [];
    parts.push(el('p', {class: 'ui-reading-line'}, [el('span', {}, 'Scanning '),
      el('code', {title: roots, text: homely(roots)}),
      since ? ` since ${since}` : '',
      (!phase || phase === 'listing' || phase === 'queued') && !totalKnown
        ? ' · Ingence knows the total at the end' : '']));
    if (job.state === 'queued') {
      parts.push(el('p', {class: 'ui-reading-line', text: job.held_said || 'Waiting to start.'}));
    } else if (scan) {
      // A count the engine has not yet sent as done is the files opened so far, where it said them.
      const seen = job.seen && typeof job.seen.entries_examined === 'number' ? job.seen.entries_examined : null;
      const state = job.done == null && phase === 'reading' && seen != null ? {...job, done: seen} : job;
      if (scan.text(state)) {
        parts.push(el('p', {class: 'ui-reading-line ui-scan-line'}, [scan.element(state)]));
      }
    }
    if (job.census_errors) {
      parts.push(el('p', {class: 'ui-reading-line',
        text: `Ingence could not read ${counted(job.census_errors, 'name', 'names')}.`}));
    }
    if (job.retries && job.interrupted) {
      parts.push(el('p', {class: 'ui-reading-line',
        text: `Attempt ${job.attempts}. The attempt before it stopped (${job.interrupted}).`}));
    }
    const heard = secondsSince(job.heartbeat_at);
    if (job.state === 'running' && heard != null && heard > 10) {
      parts.push(el('p', {class: 'ui-reading-line',
        text: `The scan last reported ${duration(heard)} ago.`}));
    }
    if (job.silent_said) parts.push(problem(job.silent_said));
    return parts;
  }

  /** "Closing this page does not stop the scan.", and the Stop button while the engine says the
   * job can be stopped. Drawn once and kept, so focus on Stop survives each second’s redraw.
   * @param {Json} job @returns {HTMLElement} */
  function stopRow(job) {
    const stop = button({id: 'ui-stop', class: 'ui-button', hidden: !job.can_stop,
      onclick: () => { stopScan(); }}, 'Stop');
    return el('div', {id: 'ui-stop-row', class: 'ui-stop-row'}, [
      el('p', {class: 'ui-help', text: WORDS.stopHelp}), stop]);
  }

  /** Stop the scan, asked first under the progress line (scan.js): "What was read so far is kept."
   * only where the engine says so. Stop posts the stop route with keep: true. */
  async function stopScan() {
    /** @type {Json} */
    let job = (page.state && page.state.job) || {};
    try {
      // What the engine says now: whether what was read so far is kept.
      job = await api('/api/landing/progress');
    } catch (error) {
      /* The last state read stands in. */
    }
    const scan = /** @type {any} */ (window).ingenceScan;
    if (!scan || !job.job_id) return;
    const answer = await scan.askToStop({
      after: document.getElementById('ui-stop-row'),
      stopKeeps: Boolean(job.stop_keeps),
      changesWait: false,
      post: () => write('/api/workspace/stop', {job_id: job.job_id, keep: true}),
      primaryClass: 'ui-button ui-primary',
      buttonClass: 'ui-button',
    });
    if (answer && answer.requested !== false) follow(0);
  }

  /** A scan a person stopped: said, with the way on. "What was read so far is kept." only where
   * the engine says so (stop_keeps).
   * @param {Json} project @param {Json} job @returns {Part[]} */
  function stoppedParts(project, job) {
    const said = ['You stopped the scan.'];
    if (job && job.stop_keeps) said.push('What was read so far is kept.');
    if (!project.has_run) said.push('Start scan starts it again.');
    return [
      el('div', {}, [el('p', {class: 'eyebrow', text: 'Scan'}), el('h2', {text: 'Stopped'})]),
      el('p', {class: 'ui-stopped-said', text: said.join(' ')}),
      project.has_run ? el('a', {class: 'ui-button', href: DASHBOARD_AT_OVERVIEW}, 'Open the Dashboard') : null,
    ];
  }

  /* ------------------------------------------------------------------ the questions box */

  /** The questions, one per line: trimmed, without empty or repeated lines, each at most 500
   * characters (what the goal route takes).
   * @param {string} text @returns {string[]} */
  function goalLines(text) {
    const lines = String(text || '').split('\n').map((line) => line.trim().slice(0, 500))
      .filter(Boolean);
    return [...new Set(lines)];
  }

  /** Whether a question typed is not yet saved as a goal.
   * @returns {boolean} */
  function unsavedGoals() {
    return goalLines(page.goals.text).some((line) => !page.goals.saved.has(line));
  }

  /** The questions box: "What do you want to find out from this data?", one question per line,
   * optional. Before the project exists the lines are kept on the page; once it exists each line is
   * saved as a goal, and a line taken out is removed. ``live`` saves as the person types.
   * @param {string} id @param {boolean} [live] @returns {HTMLElement} */
  function goalsField(id, live = false) {
    const box = /** @type {HTMLTextAreaElement} */ (el('textarea', {id, class: 'ui-goals', rows: '3',
      spellcheck: 'true', 'aria-describedby': `${id}-help`}));
    box.value = page.goals.text;
    /** @type {number} */
    let timer = 0;
    box.addEventListener('input', () => {
      page.goals.text = box.value;
      if (!live) return;
      clearTimeout(timer);
      timer = window.setTimeout(() => { syncGoals(); }, 1200);
    });
    if (live) box.addEventListener('change', () => { clearTimeout(timer); syncGoals(); });
    return el('div', {class: 'ui-field ui-goals-field'}, [
      el('label', {for: id, text: WORDS.goalsLabel}),
      box,
      el('p', {class: 'ui-help', id: `${id}-help`, text: WORDS.goalsHelp}),
      live ? el('p', {class: 'ui-status ui-goals-said', id: 'ui-goals-said', role: 'status',
        text: page.goals.said}) : null]);
  }

  /** The questions box again while the project scans, each question’s matches under it.
   * @returns {HTMLElement} */
  function goalsSection() {
    return el('section', {id: 'ui-goals-card', class: 'card ui-form ui-goals-card',
      'aria-labelledby': 'ui-goals-card-head'}, [
      el('h2', {id: 'ui-goals-card-head', text: 'Your questions'}),
      goalsField('ui-goals-live', true),
      el('div', {id: 'ui-goal-results', class: 'ui-goal-results'},
        page.goals.answered ? goalResults(page.goals.answered, page.goals.answered.shownPhase) : [])]);
  }

  /** Save each line as a goal and remove each line taken out, one request at a time; a change
   * made meanwhile is saved after. Said under the box where the engine refuses or keeps nothing.
   * @returns {Promise<void>} */
  function syncGoals() {
    const goals = page.goals;
    if (goals.syncing) {
      goals.again = true;
      return goals.syncing;
    }
    goals.syncing = (async () => {
      try {
        do {
          goals.again = false;
          const lines = goalLines(goals.text);
          for (const line of lines) {
            if (goals.saved.has(line)) continue;
            const answer = await write('/api/goals', {text: line});
            goals.saved.set(line, String((answer && answer.goal && answer.goal.id) || ''));
            goals.said = answer && answer.recorded === false ? String(answer.said || '') : '';
          }
          for (const [line, id] of [...goals.saved]) {
            if (lines.includes(line)) continue;
            if (id) {
              const answer = await write('/api/goals/remove', {id});
              goals.said = answer && answer.recorded === false ? String(answer.said || '') : '';
            }
            goals.saved.delete(line);
          }
        } while (goals.again);
      } catch (error) {
        goals.said = `Ingence could not save the questions: ${said(error)}`;
      } finally {
        goals.syncing = null;
        const line = document.getElementById('ui-goals-said');
        if (line) line.textContent = goals.said;
      }
    })();
    return goals.syncing;
  }

  /** The words a goal is looked for by: its own words, then what each was widened to.
   * @param {Json} goal @returns {string[]} */
  function lookFor(goal) {
    const words = [...((goal.read && goal.read.kept) || [])];
    for (const widened of goal.expanded || []) words.push(...(widened.to || []));
    /** @type {Map<string, string>} */
    const unique = new Map();
    for (const word of words) if (word && !unique.has(String(word).toLowerCase())) unique.set(String(word).toLowerCase(), String(word));
    return [...unique.values()];
  }

  const TIER_WORDS = /** @type {{[tier: string]: string}} */ ({named: 'by name',
    associated: 'by related words', same_theme: 'same theme'});

  /** What each goal says so far, by the phase: the words looked for during the walk, then the
   * matches by name, then the full card.
   * @param {Json} answer @param {string} phase @returns {HTMLElement[]} */
  function goalResults(answer, phase) {
    return (answer.goals || []).map((/** @type {Json} */ goal) => {
      /** @type {Part[]} */
      const body = [];
      if (['queued', 'listing', 'reading', ''].includes(phase)) {
        const words = lookFor(goal);
        const shown = words.slice(0, 8);
        if (shown.length) {
          body.push(el('p', {class: 'ui-goal-said',
            text: `${WORDS.goalsLookFor} ${shown.join(', ')}${words.length > shown.length ? ', …' : ''}`}));
        }
      } else if (phase === 'names' || phase === 'rules') {
        const named = (goal.matches || []).filter((/** @type {Json} */ match) => match.tier === 'named');
        body.push(el('p', {class: 'ui-goal-said', text: named.length
          ? `Matches by name: ${named.slice(0, 5).map((/** @type {Json} */ match) => match.label).join(', ')}`
            + `${named.length > 5 ? ', …' : ''}`
          : 'No matches by name yet.'}));
      } else {
        body.push(...goalCard(goal));
      }
      return el('div', {class: 'ui-goal', 'data-goal': goal.id}, [
        el('p', {class: 'ui-goal-text', text: goal.text}), ...body]);
    });
  }

  /** The full card of a goal: its matches in counts, their dates, how many were analyzed, how many
   * were repeated, and the best matches.
   * @param {Json} goal @returns {HTMLElement[]} */
  function goalCard(goal) {
    const sum = goal.summary || {};
    /** @type {string[]} */
    const lines = [`${counted(sum.experiments, 'experiment matches', 'experiments match')}: `
      + `${number(sum.named)} by name, ${number(sum.associated)} by related words, `
      + `${number(sum.same_theme)} in the same theme.`];
    if (sum.first && sum.last) lines.push(`From ${sum.first} to ${sum.last}.`);
    if (sum.of) lines.push(`Raw files analyzed: ${number(sum.raw_with_analysis)} of ${counted(sum.of, 'experiment', 'experiments')}.`);
    lines.push(`Repeated: ${counted(sum.repeated_groups, 'group', 'groups')}. Seen once: ${number(sum.seen_once)}.`);
    const matches = (goal.matches || []).slice(0, 5);
    return [
      el('div', {class: 'ui-goal-card'}, lines.map((line) => el('p', {text: line}))),
      matches.length ? el('ul', {class: 'ui-goal-matches'}, matches.map((/** @type {Json} */ match) =>
        el('li', {}, [match.label, el('span', {class: 'ui-aside', text: ` · ${TIER_WORDS[match.tier] || match.tier}`})]))) : null,
    ].filter((part) => part !== null).map((part) => /** @type {HTMLElement} */ (part));
  }

  /* ------------------------------------------------------------------ the top folders */

  /** "Your top folders look like instruments (Scope_1, Scope_2, Reader_A). Change if wrong.", from
   * the most common kind the engine guessed and up to three of its folders; then a list per folder,
   * the guess chosen. A change is saved at once (POST /api/areas/kind).
   * @param {Json} answer @returns {Part[]} */
  function areaParts(answer) {
    const areas = /** @type {Json[]} */ (answer.areas || []);
    if (!areas.length) return [];
    /** @type {Map<string, number>} */
    const counts = new Map();
    for (const area of areas) counts.set(area.kind, (counts.get(area.kind) || 0) + 1);
    let top = String(areas[0].kind);
    for (const [kind, count] of counts) if (count > (counts.get(top) || 0)) top = kind;
    const names = areas.filter((area) => area.kind === top).slice(0, 3).map((area) => area.name);
    const plural = (AREA_KINDS[top] || [top, top])[1];
    const rows = areas.map((area, index) => {
      const select = /** @type {HTMLSelectElement} */ (el('select', {id: `ui-area-${index}`,
        'data-area': area.id},
        Object.entries(AREA_KINDS).map(([kind, [one]]) =>
          el('option', {value: kind, text: one.charAt(0).toUpperCase() + one.slice(1)}))));
      select.value = area.kind;
      const status = el('span', {class: 'ui-area-said', role: 'status'});
      select.addEventListener('change', async () => {
        status.textContent = 'Saving...';
        try {
          const saved = await write('/api/areas/kind', {area_id: area.id, kind: select.value});
          status.textContent = saved && saved.recorded === false ? String(saved.said || '') : 'Saved.';
        } catch (error) {
          status.textContent = `Ingence could not save it: ${said(error)}`;
        }
      });
      return el('div', {class: 'ui-area', 'data-area-row': area.id}, [
        el('label', {for: `ui-area-${index}`, text: area.name}), select, status]);
    });
    const shown = rows.slice(0, 8);
    const more = rows.slice(8);
    return [
      el('h2', {id: 'ui-areas-head', text: WORDS.areasHead}),
      el('p', {class: 'ui-areas-guess', text: `Your top folders look like ${plural} `
        + `(${names.join(', ')}). Change if wrong.`}),
      el('div', {class: 'ui-area-list'}, shown),
      more.length ? el('details', {class: 'ui-area-more'}, [
        el('summary', {text: `${counted(more.length, 'more folder', 'more folders')}`}),
        el('div', {class: 'ui-area-list'}, more)]) : null,
    ];
  }

  /** The goals and the top folders, asked as the scan goes: the goals each time, the folders once
   * the walk has ended, and only until they answer.
   * @param {Json} job */
  async function scanExtras(job) {
    const phase = jobPhase(job);
    const walked = !['queued', 'listing', ''].includes(phase);
    if (walked && !page.areas) {
      try {
        const answer = await api('/api/v1/areas');
        if ((answer.areas || []).length) {
          page.areas = answer;
          const section = document.getElementById('ui-areas');
          if (section) {
            section.replaceChildren(...areaParts(answer).filter(Boolean).map((part) => /** @type {Node} */ (part)));
            section.hidden = false;
          }
        }
      } catch (error) {
        /* No run to read yet: asked again at the next turn. */
      }
    }
    if (!page.goals.saved.size && !goalLines(page.goals.text).length) return;
    try {
      const answer = await api('/api/v1/goals');
      const shownPhase = answer.phase || phase;
      page.goals.answered = {...answer, shownPhase};
      const holder = document.getElementById('ui-goal-results');
      if (holder) holder.replaceChildren(...goalResults(answer, shownPhase));
    } catch (error) {
      /* No run to read yet: asked again at the next turn. */
    }
  }

  /* ------------------------------------------------------------------ what to look at first */

  const FIRST = ['landing.first.overview', 'landing.first.overlap', 'landing.first.experiments'];

  /** Three panels while it scans, saying what to look at first when it ends: the Overview, Overlap
   * and Experiments. Their words are the help catalog’s, read once.
   * @returns {HTMLElement} */
  function firstPanels() {
    const cards = FIRST.map((key) => el('div', {class: 'card ui-first-card', 'data-help': key}));
    const fill = () => {
      const entries = (page.catalog && page.catalog.entries) || {};
      cards.forEach((card, at) => {
        const entry = entries[FIRST[at]];
        card.replaceChildren(...(entry ? [el('h3', {text: entry.title}), el('p', {text: entry.text})] : []));
      });
    };
    if (page.catalog) fill();
    else {
      api('/help/catalog.json').then((catalog) => { page.catalog = catalog; fill(); }, () => {});
    }
    return el('section', {id: 'ui-first', class: 'ui-first', 'aria-labelledby': 'ui-first-head'}, [
      el('h2', {id: 'ui-first-head', text: WORDS.firstHead}),
      el('div', {class: 'ui-first-row'}, cards)]);
  }

  /** @param {Json} project @param {Json} job @returns {Part[]} */
  function failedParts(project, job) {
    const path = quoted(project.path);
    /** @type {string[]} */
    const commands = [];
    if (!project.has_run) commands.push(`ingence index -p ${path}`);
    if ((job.operations || []).includes('dive')) commands.push(`ingence dive -p ${path}`);
    commands.push(`ingence infer -p ${path}`, `ingence see -p ${path}`);
    return [
      el('div', {}, [el('p', {class: 'eyebrow', text: 'Scan'}),
        el('h2', {text: `The scan of ${project.name} did not finish`})]),
      problem(`What happened: ${job.error_words || job.error
        || 'the scan stopped and gave no reason.'}`),
      job.error_kind ? why('Why', 'Ingence reported ', el('code', {text: job.error_kind}),
        '. The full report is in the job log, in the project’s jobs folder.') : null,
      el('p', {text: project.has_run
        ? 'Ingence kept what it read before it stopped, and the Dashboard can show it. To finish the '
          + 'scan, run these commands in a terminal:'
        : 'Ingence kept nothing. Check that the data folder exists and that you can open it. '
          + 'Then click Start scan below, or run these commands in a terminal:'}),
      el('pre', {class: 'ui-commands'}, commands.join('\n')),
      project.has_run ? el('a', {class: 'ui-button', href: DASHBOARD_AT_OVERVIEW}, 'Open the Dashboard') : null,
    ];
  }

  /** Asks how the scan goes once a second while it runs, and redraws only what changes: the
   * progress lines, Stop shown or not, the counters; the goals and the top folders every third
   * second. The questions box and the folders’ lists are never redrawn, so typing is never lost.
   * @param {number} [delay] */
  function follow(delay = POLL_MS) {
    clearTimeout(page.timer);
    page.timer = window.setTimeout(async () => {
      /** @type {Json} */
      let job;
      try {
        job = await api('/api/landing/progress');
      } catch (error) {
        const lines = document.getElementById('ui-progress-said');
        if (lines) {
          lines.replaceChildren(problem(`Ingence could not read the progress of the scan: ${said(error)}. `
            + 'Trying again.'));
        }
        follow();
        return;
      }
      const before = page.state.job || {};
      page.state.job = job;
      if (['queued', 'running'].includes(job.state)) {
        const lines = document.getElementById('ui-progress-said');
        const stop = document.getElementById('ui-stop');
        if (lines && Boolean(before.stopped) === Boolean(job.stopped)) {
          lines.replaceChildren(...progressSaid(page.state.project, job).filter(Boolean)
            .map((part) => /** @type {Node | string} */ (part)));
          if (stop) stop.hidden = !job.can_stop;
        } else {
          // A job a close stopped has started again, or the other way: the whole page is drawn.
          render();
          return;
        }
        const row = document.getElementById('ui-counters');
        if (row && showsCounters(job)) row.replaceChildren(...counterParts(job));
        page.polls += 1;
        if (page.polls % 3 === 0) scanExtras(job);
        follow();
        return;
      }
      if (job.state === 'done') {
        // The reading is over: any words asked for are brought in, then the Dashboard opens by
        // itself, at the Overview. A failure is said first, and the Dashboard waits for a click.
        if (unsavedGoals()) await syncGoals();
        const trouble = await bringWordsIn();
        const panel = document.getElementById('ui-progress');
        if (trouble && panel) {
          panel.replaceChildren(problem(trouble),
            button({class: 'ui-button ui-primary', onclick: () => openDashboard()}, 'Open the Dashboard'));
          return;
        }
        openDashboard();
        return;
      }
      await load();
    }, delay);
  }

  /** The questions a page started elsewhere carried in its address (``goTo``): kept, and the
   * address put back to ``#reading``. */
  function takeGoalsFromAddress() {
    const found = /^#reading&goals=(.*)$/.exec(location.hash);
    if (!found) return;
    try {
      const lines = JSON.parse(decodeURIComponent(found[1]));
      if (Array.isArray(lines)) page.goals.text = lines.map(String).join('\n');
    } catch (error) {
      /* An address typed by hand: nothing to keep. */
    }
    history.replaceState(null, '', `${location.pathname}${location.search}#reading`);
  }

  /* ------------------------------------------------------------------ start */

  takeGoalsFromAddress();
  load();
})();
