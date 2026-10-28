// @ts-check
/* Topics on the views: the pieces every view shares.
 *
 * A topic is a person's name for a set of the engine's groupings: experiments, groups of shared
 * words (the stacks of Groupings), lines of work, and the files and folders a person's "group"
 * always held. It is a person's statement in the Record (``overlay:group:<id>``) and a projection
 * on the views. It is never a claim and never read by inference: it changes what a view draws,
 * never what the engine concludes.
 *
 * What is here, so that no view makes its own:
 *
 * - ``read()``: every topic and the experiments it holds in the run read (``GET /api/v1/topics``,
 *   which resolves the members; this page adds nothing to those counts), kept until a topic
 *   changes. The Years lens, the "also in" chips, the Wheel's band, the Map's outlines and the
 *   neighborhood's band all read it.
 * - ``addTo()``: "Add to topic...", one chooser for every view (a new topic by name, or one that
 *   exists), written through the overlay route's batch (``POST /api/review/answer``). A topic is
 *   shown as recorded at once, because nothing in a run reads it, so no Re-run waits on it.
 * - ``colorOf()``: a topic's color is a person's, from the magenta family (``--topic-1`` to
 *   ``--topic-6`` in topics.css, one per topic in the order they were made). The engine's own
 *   colors are never a topic's.
 * - ``pickBar()`` and ``chipsFor()``: the bar a view shows while things are picked, and the "also
 *   in" chips.
 *
 * Loaded after app.js, review.js and dialogs.js (el, fill, api, write, askActor, me, dialogs). No
 * framework, no build step, nothing fetched from anywhere but this server. */

(function topicKitModule() {
  /** @typedef {import('./types/api').TopicsResponse} TopicsResponse */

  /** How many colors the magenta family holds before they repeat (topics.css). */
  const COLORS = 6;
  /** A topic's name, in characters (the overlay route's own limit). */
  const NAME_LIMIT = 120;
  /** Seconds the quiet line at the foot of the page stays. */
  const SAY_SECONDS = 9;
  const PATH = '/api/v1/topics?limit=0&suggestions=0&compare=none';

  /** @type {TopicKitReading | null} */
  let held = null;
  /** @type {Promise<TopicKitReading> | null} */
  let loading = null;
  /** @type {Set<() => void>} */
  const listeners = new Set();

  /** @param {{index: number}} topic */
  function colorOf(topic) {
    return `var(--topic-${(topic.index % COLORS) + 1})`;
  }

  /** @param {TopicsResponse} body @returns {TopicKitReading} */
  function readingOf(body) {
    /** @type {TopicKitTopic[]} */
    const topics = body.topics.map((item, index) => ({
      id: item.id,
      target: item.target,
      name: item.name,
      index,
      color: colorOf({index}),
      experiments: new Set(item.experiment_ids),
      count: item.experiments_count,
      complete: item.complete,
    }));
    /** @type {Map<string, TopicKitTopic[]>} */
    const byExperiment = new Map();
    for (const topic of topics) {
      for (const experiment of topic.experiments) {
        const list = byExperiment.get(experiment);
        if (list) list.push(topic);
        else byExperiment.set(experiment, [topic]);
      }
    }
    return {run: body.run_id, topics, byExperiment, runExperiments: body.run_experiments};
  }

  /** @param {{force?: boolean}} [options] @returns {Promise<TopicKitReading>} */
  function read({force = false} = {}) {
    if (!force && held) return Promise.resolve(held);
    if (loading && !force) return loading;
    const asked = (async () => {
      const body = /** @type {TopicsResponse} */ (await api(PATH));
      const reading = readingOf(body);
      held = reading;
      return reading;
    })();
    loading = asked;
    const clear = () => {
      if (loading === asked) loading = null;
    };
    asked.then(clear, clear);
    return asked;
  }

  function notify() {
    for (const listener of [...listeners]) {
      try {
        listener();
      } catch (error) {
        /* A view that fails to redraw never stops the others. */
      }
    }
  }

  function changed() {
    held = null;
    loading = null;
    notify();
  }

  /* After a Re-run the topics are read again. The reading is kept until a topic changes, and a
   * re-run publishes a new run, so the lens, the chips, the Wheel's band, the Map's outlines and
   * the neighborhood's halos would show the run before. The page's refresh hook (app.js, called
   * after a job's end and after every write) compares the run held with the run read now, and only
   * a different run clears the reading. */
  if (typeof onRefresh === 'function') {
    onRefresh(async () => {
      if (!held) return;
      try {
        const version = await api('/api/v1/version');
        if (held && version.run_pinned && held.run !== version.run_pinned) changed();
      } catch (error) {
        /* A page that cannot ask keeps what it holds. */
      }
    });
  }

  /** @param {() => void} listener */
  function subscribe(listener) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }

  /* ---------------------------------------------------------------- the quiet line */

  /** @type {number} */
  let sayTimer = 0;

  /** @param {string} text @param {{topicId?: string}} [options] */
  function say(text, {topicId} = {}) {
    let line = document.getElementById('topic-say');
    if (!line) {
      line = el('div', {id: 'topic-say', class: 'topic-say', role: 'status', 'aria-live': 'polite'});
      document.body.append(line);
    }
    const open = el('button', {type: 'button', class: 'link', 'data-help': 'topics.open',
      onclick: () => openTopic(topicId || '')}, ['open the collection']);
    fill(line, text, ' ', open);
    line.hidden = false;
    window.clearTimeout(sayTimer);
    sayTimer = window.setTimeout(() => {
      const there = document.getElementById('topic-say');
      if (there) there.hidden = true;
    }, SAY_SECONDS * 1000);
  }

  /** @param {string} topicId */
  function openTopic(topicId) {
    if (window.ingenceTopics) window.ingenceTopics.show(topicId || undefined).catch(() => {});
    else navigate('topics').catch(() => {});
  }

  /* ---------------------------------------------------------------- the members */

  /** The key a member is told apart by within a topic.
   * @param {TopicMemberSpec} member */
  function keyOf(member) {
    if (member.kind === 'core') return `core:${[...member.terms].map((t) => t.toLowerCase()).sort().join(' ')}`;
    if (member.kind === 'line') return `line:${[...member.experiments].sort().join(' ')}`;
    return `${member.kind}:${member.id}`;
  }

  /** @param {TopicMemberSpec[]} members @returns {TopicMemberSpec[]} */
  function distinct(members) {
    /** @type {Map<string, TopicMemberSpec>} */
    const seen = new Map();
    for (const member of members) if (!seen.has(keyOf(member))) seen.set(keyOf(member), member);
    return [...seen.values()];
  }

  /** A line of work's key, as the server makes it (``api/routes/overlay.line_key``): ``line:`` and the
   * first twelve hex digits of the SHA-256 of the experiments it was added by, each once, sorted,
   * joined by the unit separator. Null where the browser offers no digest (a page not served from
   * this computer), when the line is simply counted as new: the server leaves a line it already holds
   * as it was either way.
   * @param {string[]} experiments @returns {Promise<string | null>} */
  async function lineKey(experiments) {
    if (!(globalThis.crypto && globalThis.crypto.subtle)) return null;
    const anchors = [...new Set(experiments.map(String))].sort();
    const bytes = new TextEncoder().encode(anchors.join('\u001f'));
    const digest = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', bytes));
    return `line:${[...digest].map((byte) => byte.toString(16).padStart(2, '0')).join('').slice(0, 12)}`;
  }

  /** The key the overlay serves a member under within its topic: an entry's or an experiment's
   * identifier, ``group:<its words, casefolded, sorted, joined by a space>`` for a core, and a
   * line's digest (``lineKey``).
   * @param {TopicMemberSpec} member @returns {Promise<string | null>} */
  async function servedKey(member) {
    if (member.kind === 'core') {
      return `group:${[...new Set(member.terms.map((t) => t.trim().toLowerCase()).filter(Boolean))].sort().join(' ')}`;
    }
    if (member.kind === 'line') return lineKey(member.experiments);
    return member.id;
  }

  /** The keys of what a topic holds now, as the topics route serves its members. Only to say that
   * what was chosen is already in the topic: the edit that adds it names only what is added, and
   * the server carries every member already held (a file since gone never blocks an add).
   * @param {TopicKitTopic} topic @returns {Promise<Set<string>>} */
  async function heldKeys(topic) {
    const body = /** @type {TopicsResponse} */ (await api(
      `/api/v1/topics?topic=${encodeURIComponent(topic.id)}&limit=0&suggestions=0&compare=none`));
    const found = body.topics.find((item) => item.id === topic.id);
    if (!found) throw new Error(`The collection ${topic.name} no longer exists.`);
    return new Set(found.members.map((member) => member.key));
  }

  /* ---------------------------------------------------------------- the chooser */

  /** @param {string} typed */
  function nameProblem(typed) {
    if (typed.length > NAME_LIMIT) return `Use ${NAME_LIMIT} characters or fewer.`;
    if (/[\u0000-\u001f]/.test(typed)) return 'Remove the control characters from the name.';
    return '';
  }

  /** @param {number} n @param {string} one */
  const counted = (n, one) => `${n.toLocaleString('en-US')} ${n === 1 ? one : `${one}s`}`;

  /**
   * Ask which topic: a new one by name, or one that exists.
   * @param {TopicKitTopic[]} topics @param {string} what
   * @returns {Promise<{id: string, name: string} | {name: string} | null>}
   */
  function choose(topics, what) {
    return dialogs.open({
      title: 'Add to a collection',
      eyebrow: 'Collection',
      text: `${what}. A collection is your own set of experiments, word groups or themes, with a `
        + 'name you choose. It changes what the views show. Ingence uses none of it for results.',
      className: 'topic-chooser',
      build: (finish) => {
        const select = /** @type {HTMLSelectElement} */ (el('select', {id: 'topic-choice',
          'data-autofocus': '', 'data-help': 'topics.choose'}, [
          el('option', {value: ''}, ['A new collection']),
          ...topics.map((topic) => el('option', {value: topic.id},
            [`${topic.name} (${topic.count == null ? 'experiments not counted' : counted(topic.count, 'experiment')})`])),
        ]));
        const field = /** @type {HTMLInputElement} */ (el('input', {id: 'topic-new-name', type: 'text',
          placeholder: 'a name for it', maxlength: String(NAME_LIMIT), 'data-help': 'topics.name'}));
        const naming = el('div', {class: 'topic-naming'}, [
          el('label', {for: 'topic-new-name'}, ['Its name']), field]);
        const problem = el('p', {class: 'dialog-problem', role: 'alert'});
        select.addEventListener('change', () => {
          naming.hidden = select.value !== '';
          problem.textContent = '';
        });
        if (!topics.length) {
          select.disabled = true;
          select.title = 'You have no collection yet';
        }
        const submit = () => {
          if (select.value) {
            const picked = topics.find((topic) => topic.id === select.value);
            if (picked) finish({id: picked.id, name: picked.name});
            return;
          }
          const typed = field.value.trim();
          const said = !typed ? 'Type a name for the collection, or press Cancel.' : nameProblem(typed);
          problem.textContent = said;
          if (said) {
            field.setAttribute('aria-invalid', 'true');
            field.focus();
            return;
          }
          finish({name: typed});
        };
        return el('form', {method: 'dialog', onsubmit: (event) => {
          event.preventDefault();
          submit();
        }}, [
          el('label', {for: 'topic-choice'}, ['Add to']), select, naming, problem,
          el('p', {class: 'actions'}, [
            el('button', {type: 'button', onclick: () => finish(null)}, ['Cancel']),
            el('button', {type: 'submit', class: 'primary'}, ['Add to collection']),
          ]),
        ]);
      },
    });
  }

  /**
   * Add things to a topic: choose it, then write one batch through the overlay route.
   * @param {{members: TopicMemberSpec[], what: string}} options
   * @returns {Promise<TopicAdded | null>}
   */
  async function addTo({members, what}) {
    const wanted = distinct(members);
    if (!wanted.length) return null;
    if (!(await askActor())) return null;
    /** @type {TopicKitTopic[]} */
    let topics = [];
    try {
      topics = (await read({force: true})).topics;
    } catch (error) {
      topics = [];
    }
    const chosen = await choose(topics, what);
    if (!chosen) return null;
    try {
      /** @type {Record<string, unknown>} */
      let edit;
      let added = wanted.length;
      let name = chosen.name;
      if ('id' in chosen) {
        const topic = /** @type {TopicKitTopic} */ (topics.find((item) => item.id === chosen.id));
        const held = await heldKeys(topic);
        const keys = await Promise.all(wanted.map(servedKey));
        const fresh = wanted.filter((_member, at) => keys[at] === null || !held.has(/** @type {string} */ (keys[at])));
        added = fresh.length;
        if (!added) {
          say(`${what}: already in the collection ${name}. Nothing changed.`, {topicId: chosen.id});
          return {id: chosen.id, name, added: 0};
        }
        // Only what is added is named: the topic's other members are never sent back.
        edit = {kind: 'topic', id: chosen.id, add: fresh};
      } else {
        edit = {kind: 'topic', name, members: wanted};
      }
      const written = await write('/api/review/answer', {actor: me.actor, overlay: [edit]});
      const target = String((written.events && written.events[0] && written.events[0].target) || '');
      const id = 'id' in chosen ? chosen.id : target.split(':')[2] || '';
      changed();
      say(`Added ${counted(added, 'item')} to the collection ${name}, saved under ${me.actor}.`,
        {topicId: id});
      return {id, name, added};
    } catch (error) {
      await dialogs.failed(error, 'Ingence could not change the collection');
      return null;
    }
  }

  /* ---------------------------------------------------------------- the pieces views draw */

  /** @param {{noun: string, count: number, onAdd: () => void, onClear: () => void}} options */
  function pickBar({noun, count, onAdd, onClear}) {
    return el('div', {class: 'topic-pickbar', role: 'status', 'data-help': 'topics.pickbar'}, [
      el('strong', {}, [`${counted(count, noun)} picked`]), ' ',
      el('button', {type: 'button', class: 'answer', 'data-help': 'topics.add', onclick: () => onAdd()},
        [el('strong', {}, ['Add to collection...'])]), ' ',
      el('button', {type: 'button', class: 'link', 'data-help': 'topics.clear_picks', onclick: () => onClear()}, ['Clear']),
    ]);
  }

  /** @param {string[]} experiments @param {{limit?: number, label?: string}} [options] */
  function chipsFor(experiments, {limit = Infinity, label = 'also in '} = {}) {
    if (!held || !held.topics.length || !experiments.length) return null;
    /** @type {Map<string, number>} */
    const within = new Map();
    for (const experiment of experiments) {
      for (const topic of held.byExperiment.get(experiment) || []) {
        within.set(topic.id, (within.get(topic.id) || 0) + 1);
      }
    }
    const reading = held;
    const mine = reading.topics.filter((topic) => within.has(topic.id));
    if (!mine.length) return null;
    const shown = mine.slice(0, limit);
    const chips = shown.map((topic) => {
      const n = within.get(topic.id) || 0;
      const part = experiments.length > 1 ? ` ${n}/${experiments.length}` : '';
      return el('button', {type: 'button', class: 'topic-chip', 'data-topic': topic.id,
        'data-help': 'topics.chip', title: `In the collection ${topic.name}${experiments.length > 1 ? `: ${counted(n, 'experiment')} of ${experiments.length}` : ''}`,
        style: `--t: ${topic.color}`,
        onclick: (event) => {
          event.stopPropagation();
          openTopic(topic.id);
        }}, [el('span', {class: 'topic-dot', 'aria-hidden': 'true'}), `${topic.name}${part}`]);
    });
    const more = mine.length - shown.length;
    return el('span', {class: 'topic-chips', 'data-help': 'topics.chips'},
      [label, ...chips, more > 0 ? el('span', {class: 'faint'}, [` and ${more} more`]) : null]);
  }

  window.topicKit = {
    read, current: () => held, subscribe, changed, colorOf, addTo, pickBar, chipsFor, lineKey,
    open: openTopic, say,
  };
})();
