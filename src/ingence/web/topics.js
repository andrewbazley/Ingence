// @ts-check
/* Experiments > Collections (topics): a person's topics and what each comes to in the run.
 *
 * A topic is a person's name for a set of the engine's groupings: experiments, groups of shared
 * words, lines of work, and the files and folders a person's "group" always held. It is the
 * person's statement in the Record and a projection on the views. It is never a claim and never
 * read by inference. This page reads ``GET /api/v1/topics`` and draws what that route says. Every
 * count on the page is the route's, and none is computed here.
 *
 * One list sits beside one card, as on the Questions pages. The topics are at the left. The chosen
 * topic is at the right, with
 *
 * - its members, each with what it comes to in the run. A member that comes to nothing (the run
 *   holds no experiment it names) or could not be read is shown as exactly that, with the reason;
 * - what drifted since the run before: the experiments that joined or left, with the member that
 *   explains each;
 * - the topics it overlaps, with the experiments they share;
 * - "Rename" and "Withdraw", each one statement more in the Record (a withdrawal keeps the history,
 *   and nothing on disk changes), and "Export files (CSV)": the topic's files with their stages and
 *   experiments, made by ``GET /api/v1/topics/export`` and saved by the browser as a download, so
 *   nothing is written inside the data or the project.
 *
 * The engine's broad lines of work are offered as places to start. A topic is shown as recorded at
 * once. Nothing in a run reads it, so no Re-run waits on it.
 *
 * Loaded after topic_kit.js, years.js and app.js (el, fill, api, write, states, navigate, VIEWS,
 * dialogs, askActor, me). Its button, Collections, stands in index.html's Experiments section, and
 * app.js lists it in PAGES.experiments and OWN_DETAIL, as it does for the other sections. */

(function topicsModule() {
  /** @typedef {import('./types/api').TopicsResponse} TopicsResponse */
  /** @typedef {import('./types/api').TopicItem} TopicItem */
  /** @typedef {import('./types/api').TopicMemberItem} TopicMemberItem */
  /** @typedef {import('./types/api').TopicExportResponse} TopicExportResponse */

  const KEPT = 'ingence.topics.selected';
  /** Experiments a card lists before "Show more". */
  const FIRST = 20;
  const PAGE = 100;

  const count = (/** @type {number} */ n) => n.toLocaleString('en-US');
  const plural = (/** @type {number} */ n, /** @type {string} */ one, many = `${one}s`) =>
    `${count(n)} ${n === 1 ? one : many}`;

  /** @type {{body: TopicsResponse | null, selected: string}} */
  const held = {body: null, selected: ''};
  try {
    held.selected = sessionStorage.getItem(KEPT) || '';
  } catch (error) {
    held.selected = '';
  }

  const KIND_WORDS = {
    entry: 'file or folder', experiment: 'experiment', core: 'word group', line: 'theme',
    goal: 'goal',
  };

  /** @param {string} stamp */
  function when(stamp) {
    const moment = new Date(stamp);
    if (Number.isNaN(moment.getTime())) return stamp;
    return moment.toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'});
  }

  /** Save text as a file on this machine, through the browser's own download.
   * @param {string} name @param {string} text @param {string} type */
  function saveAs(name, text, type) {
    const link = /** @type {HTMLAnchorElement} */ (el('a', {
      href: URL.createObjectURL(new Blob([text], {type})), download: name}));
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  /** @param {string} name */
  function dated(name) {
    const dot = name.lastIndexOf('.');
    const today = new Date().toISOString().slice(0, 10);
    return dot < 0 ? `${name}-${today}` : `${name.slice(0, dot)}-${today}${name.slice(dot)}`;
  }

  /** What a member comes to, in a cell: its experiments, or exactly why it comes to none.
   * @param {TopicMemberItem} member */
  function comesTo(member) {
    if (member.state === 'resolved') {
      return el('span', {}, [plural(member.experiments || 0, 'experiment')]);
    }
    const label = member.state === 'nothing' ? 'matches no experiment' : 'not readable';
    return el('span', {}, [el('span', {class: 'topic-nothing', 'data-state': member.state}, [label]),
      member.why ? el('div', {class: 'faint'}, [member.why]) : null]);
  }

  /** @param {TopicMemberItem} member */
  function memberName(member) {
    const named = el('div', {}, [member.name || '(no name)']);
    const parts = [named];
    if (member.path) parts.push(el('div', {class: 'faint mono'}, [member.path]));
    if (member.kind === 'entry' && !member.in_run) {
      parts.push(el('div', {class: 'faint'}, ['not in this scan: moved or deleted']));
    }
    const line = member.line;
    if (line) {
      const said = line.drifted
        ? `now called ${line.label}. Its words changed after you added it`
        : `still called ${line.label}`;
      parts.push(el('div', {class: 'faint'}, [`${said}. It holds ${count(line.kept)} of the `
        + `${count(line.added_by)} experiments you added it with.`]));
    }
    return el('div', {}, parts);
  }

  /** @param {TopicItem} topic */
  function membersTable(topic) {
    if (!topic.members.length) return el('p', {class: 'faint'}, ['This collection is empty.']);
    const rows = topic.members.map((member) => el('tr', {
      class: member.state === 'resolved' ? '' : 'is-nothing', 'data-member': member.key,
      'data-state': member.state}, [
      el('td', {}, [KIND_WORDS[member.kind]]),
      el('td', {}, [memberName(member)]),
      el('td', {class: 'topic-comes'}, [comesTo(member)]),
      el('td', {}, [takeOutButton(topic, member)]),
    ]));
    return el('div', {class: 'topic-members'}, [table(['Kind', 'Member', 'Experiments', ''], rows)]);
  }

  /** One member taken out of the topic, as the overlay's ``remove_members`` edit says it: the member
   * by the key the route served (an identifier, ``group:<words>`` or ``line:<digest>``), and every
   * other member carried over as it was. The last member cannot be taken out: withdraw the topic.
   * @param {TopicItem} topic @param {TopicMemberItem} member */
  function takeOutButton(topic, member) {
    const button = /** @type {HTMLButtonElement} */ (el('button', {type: 'button', class: 'link',
      'data-help': 'topics.remove_member', 'aria-label': `Remove ${member.name || 'this member'} from the collection`,
      onclick: () => takeOut(topic, member)}, ['Remove']));
    if (topic.members.length < 2) {
      button.disabled = true;
      button.title = 'You cannot remove the last member. Withdraw the collection instead.';
    }
    return button;
  }

  /** @param {TopicItem} topic @param {TopicMemberItem} member */
  async function takeOut(topic, member) {
    if (!(await askActor())) return;
    const yes = await dialogs.choose({title: `Remove ${member.name || 'this member'} from ${topic.name}?`,
      text: 'Only this member leaves the collection. The answer log keeps what you said and when. '
        + 'Ingence never changes your files.', yes: 'Remove it', no: 'Keep it'});
    if (!yes) return;
    try {
      await write('/api/review/answer', {actor: me.actor,
        overlay: [{kind: 'topic', id: topic.id, remove_members: [member.key]}]});
      topicKit.changed();
    } catch (error) {
      await dialogs.failed(error, 'Ingence could not remove the member');
    }
  }

  /** @param {TopicItem} topic @param {TopicsResponse} body */
  function driftBlock(topic, body) {
    const drift = topic.drift;
    if (!drift) {
      return el('p', {class: 'faint', 'data-drift': 'none'},
        [body.drift_why || 'There is no earlier scan to compare with.']);
    }
    /** @param {Array<import('./types/api').TopicChangeItem>} list @param {string} sign */
    const changes = (list, sign) => list.map((change) => el('li', {}, [
      `${sign} ${change.folder || change.experiment}`,
      change.reasons.length ? el('span', {class: 'faint'}, [`: ${change.reasons.join('; ')}`]) : null]));
    const same = !drift.joined.length && !drift.left.length;
    return el('div', {'data-drift': same ? 'same' : 'changed'}, [
      ...drift.said.map((line) => el('p', {class: 'topic-said'}, [line])),
      same ? null : el('ul', {}, [...changes(drift.joined, 'Joined:'), ...changes(drift.left, 'Left:')]),
      drift.unread.length ? el('p', {class: 'faint'}, [`Ingence could not read ${plural(drift.unread.length, 'member')} `
        + 'in one of the two scans, so their changes are not shown.']) : null,
    ]);
  }

  /** @param {TopicItem} topic */
  function overlapBlock(topic) {
    if (!topic.overlaps.length) {
      return el('p', {class: 'faint', 'data-overlap': 'none'}, ['No other collection shares an experiment with this one.']);
    }
    return el('ul', {'data-overlap': 'some'}, topic.overlaps.map((overlap) => el('li', {}, [
      el('button', {type: 'button', class: 'link', onclick: () => select(overlap.topic)}, [overlap.name]),
      ` shares ${plural(overlap.experiments, 'experiment')}`])));
  }

  /** The experiments listed so far for one topic, and how many are beyond them.
   * @param {TopicItem} topic @param {HTMLElement} into */
  function experimentsBlock(topic, into) {
    const shown = [...topic.experiments];
    const draw = (/** @type {number} */ beyond) => {
      fill(into,
        shown.length ? table(['Experiment', '#Files'], shown.map((experiment) => el('tr', {
          'data-experiment': experiment.id}, [
          el('td', {}, [experiment.folder, experiment.in_run ? null
            : el('span', {class: 'faint'}, [' (not in this scan)'])]),
          el('td', {class: 'n'}, [experiment.files == null ? 'not counted' : count(experiment.files)]),
        ]))) : el('p', {class: 'faint'}, ['This collection has no experiment in this scan.']),
        beyond > 0 ? el('p', {class: 'topic-more'}, [el('button', {type: 'button', class: 'link',
          'data-help': 'topics.more', onclick: async () => {
            try {
              const next = /** @type {TopicsResponse} */ (await api(`/api/v1/topics?topic=${
                encodeURIComponent(topic.id)}&suggestions=0&compare=none&limit=${PAGE}&offset=${shown.length}`));
              const page = next.topics[0];
              shown.push(...page.experiments);
              draw(page.experiments_not_shown);
            } catch (error) {
              await dialogs.failed(error, 'Ingence could not read the experiments');
            }
          }}, [`Show ${count(Math.min(beyond, PAGE))} more of ${count(beyond)} not shown`])]) : null);
    };
    draw(topic.experiments_not_shown);
  }

  /** @param {TopicItem} topic */
  async function rename(topic) {
    if (!(await askActor())) return;
    const named = await dialogs.ask({title: 'Rename the collection',
      text: 'Only the name changes. The collection keeps its members, and the answer log keeps the old name.',
      label: 'Name', value: topic.name, submit: 'Rename',
      validate: (typed) => (typed.length > 120 ? 'Use 120 characters or fewer.' : '')});
    if (!named || named === topic.name) return;
    try {
      await write('/api/review/answer', {actor: me.actor, overlay: [{kind: 'topic', id: topic.id, name: named}]});
      topicKit.changed();
    } catch (error) {
      await dialogs.failed(error, 'Ingence could not rename the collection');
    }
  }

  /** @param {TopicItem} topic */
  async function withdraw(topic) {
    if (!(await askActor())) return;
    const yes = await dialogs.choose({title: `Withdraw the collection ${topic.name}?`,
      text: 'The views stop showing the collection. The answer log keeps what you said and when. '
        + 'Ingence never changes your files.', yes: 'Withdraw it', no: 'Keep it'});
    if (!yes) return;
    try {
      await write('/api/review/answer', {actor: me.actor, overlay: [{kind: 'topic', id: topic.id, remove: true}]});
      held.selected = '';
      topicKit.changed();
    } catch (error) {
      await dialogs.failed(error, 'Ingence could not withdraw the collection');
    }
  }

  /** @param {TopicItem} topic @param {HTMLElement} button */
  async function exportCsv(topic, button) {
    button.setAttribute('disabled', 'disabled');
    try {
      const made = /** @type {TopicExportResponse} */ (await api(
        `/api/v1/topics/export?topic=${encodeURIComponent(topic.id)}`));
      saveAs(dated(made.file_name), made.text, 'text/csv');
      /* What a person needs to read the file is shown beside the button and stays there: why a cell
       * may begin with an apostrophe, what a blank stage means, which root a path is below, and,
       * where the topic holds more files than one file lists, how many are not listed. */
      const notes = /** @type {HTMLElement | null} */ (document.querySelector(`[data-export-notes="${CSS.escape(topic.id)}"]`));
      if (notes) {
        notes.hidden = false;
        fill(notes, el('p', {class: 'eyebrow'}, ['About the file you saved']),
          el('ul', {class: 'topic-note-list'}, made.notes.map((line) => el('li', {}, [line]))));
      }
      topicKit.say(made.truncated
        ? `${made.notes[0]}`
        : `Saved ${plural(made.files, 'file')} of ${plural(made.experiments, 'experiment')} as a `
          + `spreadsheet file${made.complete ? '' : ' (at least: Ingence could not read one member)'}.`,
      {topicId: topic.id});
    } catch (error) {
      await dialogs.failed(error, 'Ingence could not export the files');
    } finally {
      button.removeAttribute('disabled');
    }
  }

  /** The Years lens and the Map's outline for one topic, opened from its card.
   * @param {TopicItem} topic */
  function viewsOf(topic) {
    /** @type {Array<{label: string, help: string, go: () => unknown}>} */
    const links = [
      {label: 'Show on Overlap', help: 'topics.show_years', go: () => {
        const years = /** @type {any} */ (window).ingenceYears;
        if (years && years.lens) years.lens(topic.id);
        return navigate('years');
      }},
      {label: 'Show on the Map', help: 'topics.show_map', go: () => {
        const map = /** @type {any} */ (window).ingenceMapTopics;
        if (map && map.show) return map.show(topic.id);
        return navigate('map');
      }},
    ];
    return links.map((link) => el('button', {type: 'button', class: 'answer quiet', 'data-help': link.help,
      onclick: () => {
        Promise.resolve(link.go()).catch((error) => dialogs.failed(error));
      }}, [el('strong', {}, [link.label])]));
  }

  /** @param {TopicItem} topic @param {TopicsResponse} body @param {number} index */
  function card(topic, body, index) {
    const exportButton = /** @type {HTMLElement} */ (el('button', {type: 'button', class: 'answer',
      'data-help': 'topics.export', onclick: () => exportCsv(topic, exportButton)},
    [el('strong', {}, ['Export files (CSV)'])]));
    const experiments = el('div', {'data-experiments': topic.id});
    experimentsBlock(topic, experiments);
    return el('section', {class: 'topic-card', 'data-topic': topic.id, style: `--t: ${topicKit.colorOf({index})}`,
      'aria-label': `The collection ${topic.name}`}, [
      el('p', {class: 'eyebrow'}, ['Collection']),
      el('h2', {}, [el('span', {class: 'topic-dot', 'aria-hidden': 'true'}), topic.name]),
      el('p', {class: 'topic-said'}, [topic.said]),
      el('p', {class: 'faint'}, [`Made by ${topic.made_by} on ${when(topic.made_at)}`
        + (topic.edited_at !== topic.made_at || topic.edited_by !== topic.made_by
          ? `; last changed by ${topic.edited_by} on ${when(topic.edited_at)}` : '')
        + (topic.earlier ? `, after ${plural(topic.earlier, 'earlier change')}` : '')
        + '. You made this collection, and it is in your answer log.']),
      el('div', {class: 'topic-actions'}, [
        exportButton, ...viewsOf(topic),
        el('button', {type: 'button', class: 'answer quiet', 'data-help': 'topics.rename',
          onclick: () => rename(topic)}, [el('strong', {}, ['Rename'])]),
        el('button', {type: 'button', class: 'answer quiet', 'data-help': 'topics.withdraw',
          onclick: () => withdraw(topic)}, [el('strong', {}, ['Withdraw'])]),
      ]),
      el('div', {class: 'topic-export-notes', 'data-export-notes': topic.id, hidden: ''}),
      el('h3', {}, [`Members (${count(topic.members.length)})`]),
      membersTable(topic),
      el('h3', {}, ['Changes since the last scan']),
      driftBlock(topic, body),
      el('h3', {}, ['Experiments shared with your other collections']),
      overlapBlock(topic),
      el('h3', {}, [topic.experiments_count == null || topic.experiments_out_of == null
        ? 'Experiments (not counted: this scan applied no rules yet)'
        : `Experiments (${count(topic.experiments_count)} of ${count(topic.experiments_out_of)} in the scan)`]),
      experiments,
    ]);
  }

  /** One row of the list.
   * @param {TopicItem} topic @param {number} index */
  function row(topic, index) {
    const flags = [];
    if (topic.nothing.length) {
      flags.push(el('span', {class: 'tag later', 'data-help': 'topics.flag_nothing'},
        [`${plural(topic.nothing.length, 'member')} match${topic.nothing.length === 1 ? 'es' : ''} no experiment`]));
    }
    if (topic.not_computed.length) {
      flags.push(el('span', {class: 'tag plain', 'data-help': 'topics.flag_unread'},
        [`${plural(topic.not_computed.length, 'member')} not readable`]));
    }
    if (topic.drift && (topic.drift.joined.length || topic.drift.left.length)) {
      flags.push(el('span', {class: 'tag plain', 'data-help': 'topics.flag_drift'},
        [`${topic.drift.joined.length ? `+${count(topic.drift.joined.length)}` : ''}${topic.drift.joined.length && topic.drift.left.length ? ' ' : ''}${topic.drift.left.length ? `-${count(topic.drift.left.length)}` : ''} since the last scan`]));
    }
    return el('li', {}, [el('button', {type: 'button', class: 'topic-row', 'data-topic': topic.id,
      'aria-current': topic.id === held.selected ? 'true' : 'false', style: `--t: ${topicKit.colorOf({index})}`,
      onclick: () => select(topic.id),
      onkeydown: (event) => walk(/** @type {KeyboardEvent} */ (event))}, [
      el('div', {class: 'topic-name'}, [el('span', {class: 'topic-dot', 'aria-hidden': 'true'}), topic.name]),
      el('p', {class: 'topic-sub'}, [(topic.experiments_count == null || topic.experiments_out_of == null || topic.files_count == null
        ? 'experiments and files not counted: this scan applied no rules'
        : `${count(topic.experiments_count)} of ${plural(topic.experiments_out_of, 'experiment')}`
          + ` · ${plural(topic.files_count, 'file')}${topic.complete ? '' : ' (at least)'}`)
        + ` · ${plural(topic.members.length, 'member')}`]),
      flags.length ? el('div', {class: 'topic-flags'}, flags) : null,
    ])]);
  }

  /** Up and Down move through the list, as in the Questions pages.
   * @param {KeyboardEvent} event */
  function walk(event) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const rows = /** @type {HTMLElement[]} */ ([...listPane.querySelectorAll('.topic-row')]);
    const here = rows.indexOf(/** @type {HTMLElement} */ (event.currentTarget));
    const next = rows[here + (event.key === 'ArrowDown' ? 1 : -1)];
    if (!next) return;
    event.preventDefault();
    next.focus();
    select(next.dataset.topic || '', {keepFocus: true});
  }

  /** A broad line of work offered as a place to start; never made by itself.
   * @param {import('./types/api').SuggestionItem} suggestion */
  function suggestion(suggestion) {
    return el('div', {class: 'topic-suggest', 'data-broad': String(suggestion.broad)}, [
      el('span', {}, [el('strong', {}, [suggestion.label]), el('span', {class: 'faint'}, [` · ${suggestion.said}`])]),
      el('button', {type: 'button', class: 'answer quiet', 'data-help': 'topics.suggestion', onclick: async () => {
        const topic = await topicKit.addTo({what: `The ${plural(suggestion.experiments, 'experiment')} of the theme `
          + `${suggestion.label}`,
        members: suggestion.experiment_ids.map((id) => ({kind: 'experiment', id}))});
        if (topic) held.selected = topic.id;
      }}, [el('strong', {}, ['Start a collection from it'])]),
    ]);
  }

  /** The list pane's content, and the card in the detail pane.
   * @param {TopicsResponse} body */
  function paint(body) {
    const topics = body.topics;
    if (!topics.some((topic) => topic.id === held.selected)) held.selected = topics.length ? topics[0].id : '';
    const list = el('ul', {class: 'topic-list', 'aria-label': 'Your collections'}, topics.map(row));
    const intro = el('p', {class: 'topics-intro'}, [
      'A collection is your own set of experiments, word groups or themes, with a name you choose. '
      + 'Make one from By year, the Map, Word groups or Ask, with Add to collection. '
      + 'It changes what the views show. Ingence uses none of it for results.']);
    const starts = body.suggestions.slice(0, 6);
    const startBlock = el('div', {class: 'topic-starts', 'data-help': 'topics.starts'}, [
      el('h3', {class: 'eyebrow'}, ['Places to start']),
      starts.length ? el('p', {class: 'faint'}, [
        'Ingence’s broadest themes. A collection started from one holds each of its experiments.'])
        : el('p', {class: 'faint'}, [body.suggestions_why || 'Ingence found no broad theme to offer.']),
      ...starts.map(suggestion),
    ]);
    if (topics.length) {
      fill(listPane, el('div', {class: 'topics-page'}, [
        el('p', {class: 'eyebrow'}, ['Explore']), el('h2', {}, [`Collections (${count(topics.length)})`]), intro, list, startBlock]));
    } else {
      fill(listPane, el('div', {class: 'topics-page'}, [
        el('p', {class: 'eyebrow'}, ['Explore']), el('h2', {}, ['Collections']),
        states.say(null, {eyebrow: 'Collections', dot: 'empty', into: true,
          sentence: 'No collections yet.',
          body: ['Pick experiments in By year, dots on the Map, word groups in Word groups, or Ask results. '
            + 'Then choose Add to collection, or start from a theme below.']}),
        intro, startBlock]));
    }
    paintCard(body);
  }

  /** @param {TopicsResponse} body */
  function paintCard(body) {
    const index = body.topics.findIndex((topic) => topic.id === held.selected);
    if (index < 0) {
      fill(detailPane, el('p', {class: 'empty'}, ['Choose a collection to see what it holds.']));
      return;
    }
    fill(detailPane, card(body.topics[index], body, index));
  }

  /** @param {string} topicId @param {{keepFocus?: boolean}} [options] */
  function select(topicId, {keepFocus = false} = {}) {
    held.selected = topicId;
    try {
      sessionStorage.setItem(KEPT, topicId);
    } catch (error) {
      /* A convenience. */
    }
    if (!held.body) return;
    for (const rowNode of listPane.querySelectorAll('.topic-row')) {
      rowNode.setAttribute('aria-current', /** @type {HTMLElement} */ (rowNode).dataset.topic === topicId ? 'true' : 'false');
    }
    paintCard(held.body);
    if (!keepFocus) detailPane.scrollTop = 0;
  }

  async function draw() {
    const asked = turns.claim(listPane);
    states.loading(listPane, 'Loading your collections...');
    try {
      const body = /** @type {TopicsResponse} */ (await api(`/api/v1/topics?limit=${FIRST}`));
      if (!turns.owns(listPane, asked)) return;
      held.body = body;
      paint(body);
      await topicKit.read({force: true}).catch(() => null);
    } catch (error) {
      states.failed(listPane, error, () => navigate('topics', {push: false}));
      fill(detailPane, el('p', {class: 'empty'}, ['Nothing selected.']));
    }
  }

  /** The Topics section: open it, on one topic where one is named.
   * @param {string} [topicId] */
  async function show(topicId) {
    if (topicId) {
      held.selected = topicId;
      try {
        sessionStorage.setItem(KEPT, topicId);
      } catch (error) {
        /* A convenience. */
      }
    }
    if (state.view === 'topics') return draw();
    return navigate('topics');
  }

  /* ----------------------------------------------------------- wiring into the Experiments page */

  VIEWS.topics = draw;
  topicKit.subscribe(() => {
    if (state.view === 'topics') draw().catch(() => {});
  });
  window.ingenceTopics = {show};
})();
