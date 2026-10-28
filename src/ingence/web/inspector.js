// @ts-check
/* The one evidence inspector: Experiments, Connections, Questions and History call it.
 *
 * It shows one thing at a time with its evidence, the rule and version behind every conclusion, and
 * the actions allowed on it:
 *
 * - an entry (a file or a folder): what the census recorded, the words of its name and what each
 *   reads as, what every rule concluded (rule, version, tier, standing), where a rule could not
 *   decide, and what was raised about it. Each word opens its page;
 * - a word: what the Dictionary settles it as now and where that came from (the shipped vocabulary,
 *   a pack, a person), what it ties with or outranked, where it stands in the data, and a
 *   correction: a person's own meaning, for the whole project or below one folder, written to the
 *   Record through the one answer route;
 * - a line of the graph (an edge): the relation, its direction, the run, the rule and version, the
 *   tier, its support, what would retract it, the records it rests on, and the way to decide a
 *   proposed pair on the Suggested pairs section.
 *
 * The Graph page keeps its own panel for a node and a line (graph.js), and this module renders the
 * same entry and line for the other pages. A question's evidence (its files) opens entries here
 * too, with a way back to the question.
 *
 * Loaded after app.js (whose el, fill, write, states and focus it uses) and before graph.js. */

(function inspectorModule() {
  /** @typedef {import('./types/api').EntryResponse} EntryResponse */
  /** @typedef {import('./types/api').ColumnsResponse} ColumnsResponse */
  /** @typedef {import('./types/api').RelatedColumnFile} RelatedColumnFile */
  /** @typedef {import('./types/api').EdgeDetail} EdgeDetail */
  /** @typedef {import('./types/api').WordMeaning} WordMeaning */
  /** @typedef {import('./types/api').SenseItem} SenseItem */
  /** @typedef {import('./types/api').ErrorResponse} ErrorResponse */

  /**
   * What a caller adds to an entry's page: its own facts and actions, and the way back.
   * @typedef {{facts?: Array<[string, string]>, actions?: HTMLElement[], back?: (() => void) | null,
   *   backLabel?: string, keepFocus?: boolean}} EntryOptions
   */

  /** The kinds of line, in words, as the graph's legend says them. */
  const KIND_WORDS = /** @type {{[kind: string]: string}} */ ({
    indexed_containment: 'indexed directory',
    published_assertion: 'published',
    proposed_candidate: 'proposed',
  });

  /** Where a meaning comes from, in words. */
  const ORIGIN_WORDS = /** @type {{[origin: string]: string}} */ ({
    builtin: 'the Dictionary Ingence ships with',
    pack: 'a word list in the Dictionary',
    discovered: 'a suggestion Ingence found in the names',
    user: 'a person, in this project’s answer log',
  });

  /** @param {unknown} text */
  const words = (text) => String(text ?? '').replace(/_/g, ' ');

  /** @param {unknown} error */
  const said = (error) => String(error instanceof Error ? error.message : error);

  /** A moment as a person reads it, in this machine's own time, its zone said as a run's name and
   * the Graph workspace's card say it. @param {string} stamp */
  const when = (stamp) => {
    const moment = new Date(stamp);
    return Number.isNaN(moment.getTime()) ? stamp
      : moment.toLocaleString(undefined, {year: 'numeric', month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit', timeZoneName: 'short'});
  };

  /** Where the inspector shows: the Graph page's own panel while it is shown, else the detail pane. */
  function pane() {
    const graphPanel = /** @type {HTMLElement | null} */ (document.querySelector('[data-graph="inspector"]'));
    return graphPanel || /** @type {HTMLElement} */ (document.getElementById('detail'));
  }

  /**
   * One /api/v1 read, refusing in the API's one error shape.
   * @param {string} path
   * @returns {Promise<any>}
   */
  async function v1(path) {
    const response = await fetch(path);
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      /** @type {ErrorResponse | null} */
      const refused = body;
      throw new Error(refused && refused.error ? refused.error.message : response.statusText);
    }
    return body;
  }

  /** @param {Array<[string, string | HTMLElement]>} rows */
  function facts(rows) {
    return el('dl', {class: 'inspector-facts'}, rows.flatMap(([term, value]) => [
      el('dt', {text: term}), typeof value === 'string' ? el('dd', {text: value}) : el('dd', {}, [value]),
    ]));
  }

  /**
   * A way back to what opened this, where something did.
   * @param {(() => void) | null | undefined} back
   * @param {string} [label]
   */
  function backLink(back, label) {
    if (!back) return null;
    return el('button', {class: 'link inspector-back', type: 'button', onclick: () => back()},
      [`← ${label || 'back'}`]);
  }

  // ------------------------------------------------------------------ an entry

  /**
   * Show one entry, reading it from the run every page reads.
   * @param {string} id
   * @param {HTMLElement} [where]
   * @param {EntryOptions} [options]
   */
  async function showEntry(id, where, options = {}) {
    const target = where || pane();
    const turn = turns.claim(target);
    states.loading(target, 'Loading what Ingence saved about it...');
    /** @type {EntryResponse} */
    let found;
    try {
      found = await v1(`/api/v1/entry?${new URLSearchParams({id})}`);
    } catch (error) {
      if (turns.owns(target, turn)) states.failed(target, error, () => { showEntry(id, target, options); });
      return;
    }
    if (!turns.owns(target, turn)) return;
    if (!options.keepFocus) setFocus({kind: 'entry', id, label: found.entry.name || found.entry.path});
    const columns = columnsSlot(found);
    fill(target, ...entryBlocks(found, target, options), columns);
    if (columns) loadColumns(found, columns, target, turn);
  }

  // ------------------------------------------------------------------ a table's columns

  /** The extensions of the tables whose columns the engine reads. */
  const TABLE_EXTENSIONS = new Set(['csv', 'tsv', 'tab']);

  /**
   * The place a table's columns will be drawn, or null for a file that is no delimited table.
   * @param {EntryResponse} found
   */
  function columnsSlot(found) {
    if (found.entry.entry_type !== 'file') return null;
    if (!TABLE_EXTENSIONS.has(String(found.entry.extension).toLowerCase())) return null;
    return el('div', {class: 'inspector-columns', 'data-columns': found.entry.id});
  }

  /**
   * Read a table's columns and draw them into their place; the entry's page stands without them.
   * @param {EntryResponse} found
   * @param {HTMLElement} slot
   * @param {HTMLElement} target
   * @param {number} turn
   */
  async function loadColumns(found, slot, target, turn) {
    /** @type {ColumnsResponse} */
    let columns;
    try {
      columns = await v1(`/api/v1/columns?${new URLSearchParams({id: found.entry.id, run: found.run_id})}`);
    } catch (error) {
      if (turns.owns(target, turn)) fill(slot, el('p', {class: 'faint', text: `Ingence could not read its columns: ${said(error)}`}));
      return;
    }
    if (!turns.owns(target, turn)) return;
    fill(slot, ...columnBlocks(columns, target));
  }

  /**
   * How many files, said once: "1 file", "3 files".
   * @param {number} count
   */
  const fileCount = (count) => `${count.toLocaleString()} file${count === 1 ? '' : 's'}`;

  /**
   * A list of tables, each a button to its own page, with the columns it adds or lacks.
   * @param {RelatedColumnFile[]} files
   * @param {number} total
   * @param {HTMLElement} target
   * @param {'added' | 'missing'} which
   */
  function relatedList(files, total, target, which) {
    return el('ul', {class: 'ids inspector-related'}, [
      ...files.map((file) => {
        const named = which === 'added' ? file.added : file.missing;
        const said = file.same_names ? 'the same names in another order or delimiter'
          : named.length ? `${which === 'added' ? 'adds' : 'lacks'} ${named.join(', ')}` : '';
        return el('li', {}, [
          el('button', {class: 'link', type: 'button',
            onclick: () => { showEntry(file.id, target); }}, [file.path]),
          said ? el('span', {class: 'faint', text: ` ${said}`}) : null,
        ]);
      }),
      total > files.length ? el('li', {class: 'faint', text: `and ${(total - files.length).toLocaleString()} more`}) : null,
    ]);
  }

  /**
   * The lines about a table's columns: the tables with the same columns, and the tables whose
   * columns contain its own. The group is "same columns" and says nothing about an experiment.
   * @param {ColumnsResponse} columns
   * @param {HTMLElement} target
   */
  function columnBlocks(columns, target) {
    if (!columns.read) {
      return [el('h4', {text: 'Its columns'}),
        el('p', {class: 'faint', text: `Not compared with other tables: ${columns.why_not || 'its columns were not read'}.`})];
    }
    const blocks = [
      el('h4', {text: 'Its columns'}),
      el('p', {class: 'faint', text: `${columns.column_count} column${columns.column_count === 1 ? '' : 's'}: ${columns.column_names.join(', ')}.`}),
      el('p', {class: 'columns-same', 'data-columns-line': 'same',
        text: `Same columns as ${fileCount(columns.same_columns_total)}`}),
    ];
    if (columns.same_columns.length) {
      blocks.push(el('ul', {class: 'ids inspector-related'}, [
        ...columns.same_columns.map((file) => el('li', {}, [
          el('button', {class: 'link', type: 'button', onclick: () => { showEntry(file.id, target); }}, [file.path])])),
        columns.same_columns_total > columns.same_columns.length
          ? el('li', {class: 'faint', text: `and ${(columns.same_columns_total - columns.same_columns.length).toLocaleString()} more`}) : null,
      ]));
    }
    if (columns.inclusion_on) {
      blocks.push(el('p', {class: 'columns-contained', 'data-columns-line': 'contained',
        text: `Its columns are contained in ${fileCount(columns.contained_in_total)}`}));
      if (columns.contained_in.length) blocks.push(relatedList(columns.contained_in, columns.contained_in_total, target, 'added'));
      if (columns.contains_total) {
        blocks.push(el('p', {class: 'columns-contains', 'data-columns-line': 'contains',
          text: `${fileCount(columns.contains_total)} ${columns.contains_total === 1 ? 'has' : 'have'} columns contained in its own`}));
        blocks.push(relatedList(columns.contains, columns.contains_total, target, 'missing'));
      }
    }
    blocks.push(el('p', {class: 'faint', text: columns.notes[0]}));
    return blocks;
  }

  /**
   * @param {EntryResponse} found
   * @param {HTMLElement} target
   * @param {EntryOptions} options
   */
  function entryBlocks(found, target, options) {
    const entry = found.entry;
    const again = () => { showEntry(entry.id, target, {...options, keepFocus: true}); };
    /** @type {Array<[string, string | HTMLElement]>} */
    const rows = [
      ['Kind', `${entry.entry_type}${entry.entry_kind === 'derived' ? ', from inside a file' : ''}`],
      ...(options.facts || []),
      ['Last scan', words(entry.scan_outcome)],
      ['Its questions', words(entry.resolution_status)],
      ['Size', entry.size_bytes == null ? 'not recorded' : `${entry.size_bytes.toLocaleString()} bytes`],
      ['Modified', entry.modified_at ? when(entry.modified_at) : 'not recorded'],
      // Where it came from, a copy said as one: the server's words.
      ...(found.moved ? /** @type {Array<[string, string]>} */ ([['Moved here', found.moved.said]]) : []),
      ['In the scan', found.run_id],
      ['Identifier', el('code', {text: entry.id})],
    ];
    const onGraph = Boolean(document.querySelector('[data-graph="inspector"]'));
    const actions = [
      ...(options.actions || []),
      onGraph || !window.graphButton ? null : window.graphButton(entry.id),
    ].filter((item) => item != null);
    return [
      backLink(options.back, options.backLabel),
      el('h3', {text: entry.name || entry.path || entry.id}),
      el('p', {class: 'faint', text: entry.path}),
      facts(rows),
      actions.length ? el('p', {class: 'inspector-actions'}, actions) : null,
      ...nameWords(found, () => ({back: again, backLabel: `back to ${entry.name}`})),
      ...claimBlocks(found),
    ];
  }

  /**
   * The words of an entry's name, each with what it reads as and a way to its page.
   * @param {EntryResponse} found
   * @param {() => {back: () => void, backLabel: string}} backTo
   */
  function nameWords(found, backTo) {
    const bound = new Map(found.bindings.map((binding) => [binding.component_id, binding]));
    const shown = found.components.filter((part) => part.normalized_value
      && /[a-z]/i.test(part.normalized_value));
    if (!shown.length) return [];
    return [
      el('h4', {text: 'The words of its name'}),
      el('p', {class: 'faint', text: 'What each word means here. Open a word to see where its '
        + 'meaning comes from, or to give your own.'}),
      el('ul', {class: 'ids inspector-words'}, shown.map((part) => {
        const binding = bound.get(part.component_id);
        const reads = binding && binding.term
          ? `means ${words(binding.term)}${binding.theme_kind ? ` (${words(binding.theme_kind)})` : ''}`
          : binding && binding.resolution_status
            ? words(binding.resolution_status) : 'no meaning yet';
        const word = String(part.normalized_value);
        return el('li', {}, [
          el('button', {class: 'link word-link', type: 'button', 'data-help': 'inspector.word',
            onclick: () => { showWord(word, undefined, backTo()); }}, [part.raw_value || word]),
          ` ${reads}`,
        ]);
      })),
    ];
  }

  /**
   * A conclusion's value as a person reads it: a word as written, a group's key as the words it
   * names, anything else as short text. The value as stored is kept in the title for a reader
   * who needs it.
   * @param {unknown} value
   * @returns {string}
   */
  function readable(value) {
    if (value == null) return 'nothing';
    if (typeof value === 'string') {
      // A group's key is stored as JSON text; read it as what it names.
      if (/^[[{]/.test(value)) {
        try {
          return readable(JSON.parse(value));
        } catch (error) {
          return value;
        }
      }
      return value;
    }
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) {
      const pairs = value.every((item) => Array.isArray(item) && item.length === 2);
      return pairs ? value.map((item) => String(item[1])).join(' ')
        : value.map((item) => readable(item)).join(', ');
    }
    if (typeof value === 'object') {
      const held = /** @type {{[key: string]: unknown}} */ (value);
      if (Array.isArray(held.components)) return readable(held.components);
      return Object.entries(held).filter(([, item]) => item != null && item !== '')
        .map(([key, item]) => `${words(key)} ${readable(item)}`).join('; ');
    }
    return String(value);
  }

  /**
   * What the rules concluded about an entry, where they could not decide, and what was raised.
   * @param {EntryResponse} found
   */
  function claimBlocks(found) {
    const blocks = [el('h4', {text: 'What the rules said about it'})];
    if (!found.claims.length) blocks.push(el('p', {class: 'empty', text: 'No rule found anything about it.'}));
    else {
      // What was concluded first, in words (the predicate says what the value is: a count of
      // files beneath, a stage), then the rule and its version, the tier and the standing.
      blocks.push(el('ul', {class: 'ids inspector-claims'}, found.claims.map((claim) => el('li', {}, [
        `${words(String(claim.predicate).split('.').pop())}: `,
        el('strong', {title: typeof claim.object_reference === 'string' ? claim.object_reference
          : JSON.stringify(claim.object_reference), text: readable(claim.object_reference)}),
        el('span', {class: 'faint'}, [' by ', el('code', {text: claim.rule_id}),
          ` version ${claim.rule_version} (${words(claim.inference_tier)}, ${words(claim.resolution_status)})`]),
      ]))));
    }
    if (found.failures.length) {
      blocks.push(el('h4', {text: 'Where a rule could not decide'}),
        el('ul', {class: 'ids'}, found.failures.map((failure) => el('li', {}, [
          el('code', {text: failure.rule_id}), ` version ${failure.rule_version}: ${failure.reason}`]))));
    }
    if (found.findings.length) {
      blocks.push(el('h4', {text: 'What was raised about it'}),
        el('ul', {class: 'ids'}, found.findings.map((finding) => el('li', {}, [
          el('code', {text: finding.rule_id}), ` version ${finding.rule_version}: ${readable(finding.raised)}`,
          el('span', {class: 'faint', text: finding.reason ? `, because ${finding.reason}` : ''})]))));
    }
    return blocks;
  }

  // ------------------------------------------------------------------ a word

  /**
   * A button that opens a word's page in the inspector.
   * @param {string} word
   * @param {string} [label]
   */
  function wordLink(word, label) {
    return el('button', {class: 'link word-link', type: 'button', 'data-help': 'inspector.word',
      onclick: (event) => { event.stopPropagation(); showWord(word); }}, [label || word]);
  }

  /** @param {SenseItem} sense */
  function senseWords(sense) {
    const scope = sense.scope === 'project' ? '' : `, ${sense.scope.replace(/^folder:/, 'below ')}`;
    return `${words(sense.term)} (${words(sense.theme_kind)}), from ${ORIGIN_WORDS[sense.origin] || sense.origin}`
      + `${sense.section ? `, its "${sense.section}" section` : ''}${scope}`;
  }

  /**
   * Show what a word means here, and offer a correction.
   * @param {string} word
   * @param {HTMLElement} [where]
   * @param {{back?: (() => void) | null, backLabel?: string}} [options]
   */
  async function showWord(word, where, options = {}) {
    const target = where || pane();
    const turn = turns.claim(target);
    states.loading(target, `Loading what "${word}" means here...`);
    /** @type {WordMeaning} */
    let shown;
    try {
      shown = await v1(`/api/v1/word/meaning?${new URLSearchParams({word})}`);
    } catch (error) {
      if (turns.owns(target, turn)) states.failed(target, error, () => { showWord(word, target, options); });
      return;
    }
    if (!turns.owns(target, turn)) return;
    setFocus({kind: 'word', id: shown.word, label: `the word "${shown.word}"`});
    const standing = shown.standing === 'settled' && shown.in_force
      ? `It means ${senseWords(shown.in_force)}.`
      : shown.standing === 'contested'
        ? 'Two or more meanings tie, so it has no meaning until you choose one.'
        : 'The Dictionary has no meaning for it yet.';
    fill(target,
      backLink(options.back, options.backLabel),
      el('h3', {}, ['The word ', el('mark', {text: shown.word})]),
      el('p', {class: 'summary-strong', text: standing}),
      shown.tied.length ? el('p', {class: 'faint', text: `Tied: ${shown.tied.map(senseWords).join('; ')}.`}) : null,
      shown.outranked.length ? el('p', {class: 'faint', text: `Ranked lower: ${shown.outranked.map(senseWords).join('; ')}.`}) : null,
      // A person's meanings, one per scope: the whole project, and below each folder given.
      ...shown.corrections.map((correction) => el('p', {class: 'note kept', text: `${correction.actor} gave it `
        + `their own meaning on ${when(correction.occurred_at)}: ${words(correction.term)} (${words(correction.theme_kind)})`
        + `${correction.below ? `, below ${correction.below}` : ', for the whole project'}`
        + `${correction.corrected ? `, in place of ${words(correction.corrected.term)}` : ''}.`})),
      el('p', {class: 'faint', text: `${shown.files.toLocaleString()} name${shown.files === 1 ? '' : 's'} in `
        + `${shown.folders.length + shown.folders_not_shown} folder${shown.folders.length + shown.folders_not_shown === 1 ? '' : 's'} contain it `
        + `(scan ${shown.run_id}).`}),
      el('details', {}, [el('summary', {text: 'which folders'}),
        el('ul', {class: 'ids'}, [
          ...shown.folders.map((folder) => el('li', {text: `${folder.path || '(the top)'}: ${folder.files}`})),
          shown.folders_not_shown ? el('li', {text: `and ${shown.folders_not_shown} more`}) : null,
        ])]),
      correctionForm(shown, target, options),
    );
  }

  /**
   * The correction: a person's own meaning, its kind, and where it applies.
   * @param {WordMeaning} shown
   * @param {HTMLElement} target
   * @param {{back?: (() => void) | null, backLabel?: string}} options
   */
  function correctionForm(shown, target, options) {
    const id = `correct-${shown.word.replace(/[^a-z0-9]/gi, '_')}`;
    const meaning = el('input', {id: `${id}-meaning`, type: 'text', 'aria-describedby': `${id}-problem`,
      placeholder: 'what it means in your data, in your words'});
    const kind = el('select', {id: `${id}-kind`}, [el('option', {value: '', text: 'choose...'}),
      ...shown.theme_kinds.map((name) => el('option', {value: name, text: words(name)}))]);
    const below = el('select', {id: `${id}-where`}, [
      el('option', {value: '', text: 'the whole project'}),
      ...shown.folders.filter((folder) => folder.path)
        .map((folder) => el('option', {value: folder.path, text: `only below ${folder.path}`})),
    ]);
    const note = el('input', {id: `${id}-note`, type: 'text', placeholder: 'why, in your words (optional)'});
    const problem = el('p', {class: 'dialog-problem', id: `${id}-problem`, role: 'alert'});
    const status = el('p', {class: 'note', role: 'status'});
    const check = () => {
      const typed = /** @type {HTMLInputElement} */ (meaning).value.trim();
      const axis = /** @type {HTMLSelectElement} */ (kind).value;
      let wrong = '';
      if (!typed) wrong = 'Write what the word means first.';
      else if (typed.length > 200) wrong = 'Use 200 characters or fewer.';
      else if (!axis) wrong = 'Choose what kind of thing it is.';
      else if (shown.in_force && !/** @type {HTMLSelectElement} */ (below).value
        && shown.in_force.term === typed && shown.in_force.theme_kind === axis) {
        wrong = 'That is what it already means here.';
      }
      problem.textContent = wrong;
      meaning.setAttribute('aria-invalid', wrong ? 'true' : 'false');
      return !wrong;
    };
    const send = async () => {
      if (!check()) return;
      const typed = /** @type {HTMLInputElement} */ (meaning).value.trim();
      const axis = /** @type {HTMLSelectElement} */ (kind).value;
      const folder = /** @type {HTMLSelectElement} */ (below).value;
      // Say what it replaces, if anything: a person's meaning at this same place (the whole
      // project, or this folder), or a person's answer to a question at this place. A meaning at
      // another place is kept.
      const scopeToken = folder ? `folder:${folder}` : 'project';
      const same = shown.corrections.find((correction) => (correction.below || '') === folder);
      const answer = shown.in_force && shown.in_force.origin === 'user' && shown.in_force.raised_by
        && shown.in_force.scope === scopeToken ? shown.in_force : null;
      const replaces = same
        ? ` It replaces the meaning ${same.actor} gave it ${folder ? `below ${folder}` : 'for the whole project'}, `
          + `${words(same.term)}.`
        : answer ? ` It replaces the answer given to the question ${answer.raised_by} asked, ${words(answer.term)}.`
          : '';
      const kept = shown.corrections.filter((correction) => correction !== same);
      const sure = await dialogs.choose({
        title: `Give "${shown.word}" your own meaning?`,
        text: `${shown.word} will read as ${typed} (${words(axis)}) ${folder ? `below ${folder}` : 'across this project'}`
          + `${shown.in_force && !same && !answer ? `, in place of ${words(shown.in_force.term)}` : ''}.${replaces}`
          + `${kept.length ? ` Your meaning${kept.length === 1 ? '' : 's'} elsewhere (${kept.map((correction) =>
            correction.below ? `below ${correction.below}` : 'the whole project').join(', ')}) stay${kept.length === 1 ? 's' : ''}.` : ''}`
          + ' Ingence saves it under your name and keeps every earlier answer in the answer log. It '
          + 'waits for Re-run, which reads again the names that contain the word.',
        yes: 'Record it',
      });
      if (!sure) return;
      if (!(await askActor())) return;
      status.textContent = 'Saving...';
      try {
        const written = await write('/api/review/answer', {actor: me.actor, target: `term:${shown.word}`,
          answer: typed, theme_kind: axis, correcting: true, below: folder || undefined,
          note: /** @type {HTMLInputElement} */ (note).value.trim() || undefined});
        status.className = 'note kept';
        status.textContent = `Saved as answer ${written.sequence}. It waits for Re-run, which reads `
          + 'again the names that contain the word. The badge on Re-run counts it.';
        window.setTimeout(() => { showWord(shown.word, target, options); }, 1200);
      } catch (error) {
        status.className = 'note refused';
        status.textContent = said(error);
      }
    };
    meaning.addEventListener('input', () => { if (problem.textContent) check(); });
    return el('div', {class: 'correction', 'data-help': 'inspector.correct'}, [
      el('h4', {text: shown.corrections.length ? 'Change your meaning, or give one for another place'
        : 'Give it your own meaning'}),
      el('p', {class: 'faint', text: 'Use this when the word means something else in your data. '
        + 'Ingence saves your answer in this project’s answer log. The Dictionary it ships with stays '
        + 'as it is.'}),
      el('div', {class: 'field'}, [el('label', {for: `${id}-meaning`, text: 'means'}), meaning]),
      el('div', {class: 'field'}, [el('label', {for: `${id}-kind`, text: 'a kind of'}), kind]),
      el('div', {class: 'field'}, [el('label', {for: `${id}-where`, text: 'where'}), below]),
      el('div', {class: 'field'}, [el('label', {for: `${id}-note`, text: 'note'}), note]),
      problem,
      el('div', {class: 'answers'}, [el('button', {class: 'answer', type: 'button', onclick: () => { send(); }},
        [el('strong', {text: 'record this meaning'})])]),
      status,
    ]);
  }

  // ------------------------------------------------------------------ a line of the graph

  /**
   * Show one line's evidence, read from the run the drawing came from.
   * @param {string} id
   * @param {string} run
   * @param {HTMLElement} [where]
   */
  async function showEdge(id, run, where) {
    const target = where || pane();
    const turn = turns.claim(target);
    states.loading(target, 'Loading its evidence...');
    try {
      /** @type {EdgeDetail} */
      const detail = await v1(`/api/v1/graph/edge?${new URLSearchParams({id, run})}`);
      if (turns.owns(target, turn)) fill(target, ...edgeBlocks(detail));
    } catch (error) {
      if (turns.owns(target, turn)) states.failed(target, error, () => { showEdge(id, run, target); });
    }
  }

  /** @param {EdgeDetail} detail */
  function edgeBlocks(detail) {
    const direction = detail.symmetric ? 'The relation goes both ways.'
      : detail.directed ? `From ${detail.source.name || detail.source.id} to `
        + `${detail.target ? detail.target.name || detail.target.id : 'nothing recorded'}.`
        : '';
    /** @type {Array<[string, string | HTMLElement]>} */
    const rows = [
      ['Scan', el('code', {text: detail.run_id})],
      ['Rule', detail.rule_id ? el('span', {}, [el('code', {text: detail.rule_id}), ` version ${detail.rule_version}`])
        : 'none recorded'],
      ['Level', detail.tier ? `${detail.tier}${detail.tier_definition ? `: ${detail.tier_definition}` : ''}` : 'none'],
      ['Standing', detail.status],
    ];
    if (detail.level) rows.push(['Level', `${detail.level}${detail.quotient_origin ? `, ${detail.quotient_origin}` : ''}`]);
    if (detail.expansion_state) rows.push(['Expansion', detail.expansion_state]);
    if (detail.reference_resolution) rows.push(['Reference', words(detail.reference_resolution)]);
    if (detail.candidate_id && detail.kind === 'published_assertion') {
      rows.push(['Promoted from', el('code', {text: detail.candidate_id})]);
    }
    const blocks = [
      el('h3', {text: `${detail.primitive}${detail.specialization ? ` (${words(detail.specialization)})` : ''}`}),
      el('p', {class: `graph-kind ${detail.kind}`, text: KIND_WORDS[detail.kind] || detail.kind}),
      el('p', {text: detail.reading}),
      el('p', {class: 'faint', text: direction}),
      facts(rows),
      detail.rule_statement ? el('details', {}, [el('summary', {text: 'the rule, as the scan recorded it'}),
        el('p', {class: 'faint', text: detail.rule_statement})]) : null,
      el('p', {class: 'faint', text: detail.definition}),
    ];
    const ends = [detail.source, detail.target].filter((end) => end && end.is_entry);
    if (ends.length) {
      blocks.push(el('p', {class: 'inspector-actions'}, ends.map((end) => el('button', {
        class: 'link', type: 'button', onclick: () => {
          if (end) showEntry(end.id, undefined, {back: () => { showEdge(detail.id, detail.run_id); }, backLabel: 'back to the line'});
        }}, [`open ${end ? end.name || end.id : ''}`]))));
    }
    if (detail.support.length) {
      blocks.push(el('h4', {text: 'Support'}), el('ul', {class: 'ids'}, detail.support.map((item) =>
        el('li', {}, [`${words(item.name)} (${words(item.family)})`,
          item.observations.length ? el('span', {class: 'faint', text: ` from ${item.observations.length} record${item.observations.length === 1 ? '' : 's'}`}) : null]))));
    }
    if (detail.defeaters.length) {
      blocks.push(el('h4', {text: 'What would remove it'}), el('ul', {class: 'ids'}, detail.defeaters.map((item) =>
        el('li', {}, [`${item.reason}${item.fired ? ' (present)' : ' (not present)'} `, el('code', {text: item.rule_id})]))));
    }
    if (detail.records.length) {
      blocks.push(el('details', {'data-help': 'graph.records'}, [
        el('summary', {text: `the ${detail.records.length} record${detail.records.length === 1 ? '' : 's'} it rests on`}),
        el('ul', {class: 'ids'}, detail.records.map((record) => el('li', {}, [el('code', {text: record})]))),
      ]));
    }
    if (detail.review) {
      const review = detail.review;
      blocks.push(el('p', {class: 'faint', text: review.message}), el('button', {
        class: 'answer', type: 'button', 'data-help': 'graph.review',
        onclick: () => {
          // The existing Suggested pairs section, filtered to this pair's rule and folder.
          sheetState.rule = review.rule;
          sheetState.top = review.top;
          showView(review.view);
        }}, [el('strong', {text: 'Answer it on Suggested pairs'})]));
    }
    return blocks;
  }

  // ------------------------------------------------------------------ a question's files

  /**
   * A question's files, each opening its entry here with a way back to the question.
   * @param {Array<{entry_id?: string, id?: string, path?: string, name?: string}>} files
   * @param {() => void} back
   */
  function filesOf(files, back) {
    return el('ul', {class: 'ids'}, files.map((file) => {
      // A folder's finding names its subject as the folder's target (``folder:<entry_id>``, found
      // on a real census): the entry is the part after the prefix.
      const id = String(file.entry_id || file.id || '').replace(/^folder:/, '');
      const label = String(file.path || file.name || id);
      return el('li', {}, [id
        ? el('button', {class: 'link', type: 'button', 'data-help': 'inspector.file',
          onclick: () => { showEntry(id, undefined, {back, backLabel: 'back to the question'}); }}, [label])
        : label]);
    }));
  }

  window.inspector = {pane, showEntry, showWord, showEdge, wordLink, filesOf, entryBlocks, edgeBlocks};
})();
