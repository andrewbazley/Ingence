/* The review pages: every review sheet's kind as a page that writes.
 *
 * Each kind of row a review workbook carried is a page here (questions, groupings, claim samples,
 * proposed pairs, extensions), and every row writes the same Record event the workbook's import
 * writes, through the one route that writes. Every row takes a note. A selected group takes one
 * note, and one word where they share their words, written to each row.
 *
 * An answer imported from a file says so where it is shown: "imported from" the file.
 *
 * Loaded before app.js, which holds the helpers (el, api, write, table) and the wiring. Nothing
 * here runs until a view is opened. */

/* Who is deciding on this machine. The engine asks once and keeps the name in the person's own
 * configuration file, outside every project (``/api/decider``), so every browser and every port on
 * this machine sends the same name. The name is never guessed: until a person gives a name there is
 * none, and the first decision asks. Every write still carries it as its actor. A name an earlier
 * page kept in this browser is offered, never taken unasked. */
const me = {actor: '', storedIn: '', remembered: (() => {
  try { return localStorage.getItem('ingence.actor') || ''; } catch (error) { return ''; }
})()};

async function loadDecider() {
  const known = await api('/api/decider');
  me.actor = known.decider || '';
  me.storedIn = known.stored_in || '';
  showDecider();
  return me.actor;
}

async function setDecider(name) {
  const kept = await write('/api/decider', {decider: name});
  me.actor = kept.decider || '';
  me.storedIn = kept.stored_in || me.storedIn;
  showDecider();
  return me.actor;
}

/* A name is a person's words: something printable, not the empty string, and not so long it is
 * a sentence. The server refuses the rest in its own words; this says it before anything is sent. */
function nameProblem(typed) {
  if (typed.length > 80) return 'Use 80 characters or fewer.';
  if (/[\u0000-\u001f]/.test(typed)) return 'Remove the control characters from the name.';
  return '';
}

async function askActor() {
  if (me.actor.trim()) return true;
  const who = await dialogs.ask({title: 'What is your name?',
    text: 'Ingence saves your name on this computer, outside every project. It saves each answer '
      + 'under this name. You can change it at the top of any page.',
    label: 'Your name', value: me.remembered || '', submit: 'Use this name',
    validate: nameProblem});
  if (!who) return false;
  try {
    await setDecider(who);
  } catch (error) {
    await dialogs.failed(error, 'Ingence could not save the name');
    return false;
  }
  return true;
}

async function changeDecider() {
  if (!me.actor) {
    await askActor();
    return;
  }
  const who = await dialogs.ask({title: 'Change your name',
    text: 'Earlier answers keep the name they had.',
    label: 'Your name', value: me.actor, submit: 'Use this name', validate: nameProblem});
  if (!who || who === me.actor) return;
  try {
    await setDecider(who);
  } catch (error) {
    await dialogs.failed(error, 'Ingence could not change the name');
  }
}

/* The name, at the top of every page, with the way to change it. */
function showDecider() {
  const place = document.getElementById('decider');
  if (!place) return;
  const change = (text) => el('button', {class: 'link', text, onclick: () => changeDecider()});
  if (me.actor) {
    place.replaceChildren('Your name: ', el('strong', {text: me.actor}), ' ',
      el('span', {class: 'faint', title: me.storedIn, text: '(saved on this computer, outside '
        + 'the project) '}), change('change'));
  } else if (me.remembered) {
    place.replaceChildren(`No name is set on this computer. This browser remembers `
      + `"${me.remembered}" from an earlier page: `,
    el('button', {class: 'link', text: `use "${me.remembered}"`,
      onclick: () => setDecider(me.remembered).catch((error) =>
        dialogs.failed(error, 'Ingence could not save the name'))}),
    ' or ', change('give another name'), '.');
  } else {
    place.replaceChildren('No name is set on this computer yet. Ingence asks for it at your '
      + 'first answer. ', change('set your name'));
  }
}

/* Replace a pane's contents, leaving out the parts a row does not have. ``replaceChildren``
 * would print a missing part as the word "null". */
function fill(pane, ...parts) {
  pane.replaceChildren(...parts.flat().filter((part) => part != null && part !== ''));
}

/* ------------------------------------------------------------------ auto-advance */

/* The next thing is always in the pane. It is built once, and every split view uses it (Questions,
 * Groupings, Claim samples, Proposed pairs, Extensions, Programs, Coverage's words). Opening a
 * section selects its first waiting row. After a choice the next waiting row fills the detail pane
 * at once (the answered row has left the list). At the end the pane says so in one line. A person
 * can still click any row. */
const advance = {
  /* The waiting row after ``answered`` in the order the list was last shown, or the first
   * waiting row where there is none after it, or where nothing was answered. */
  next(rows, answered, shownBefore) {
    const waiting = rows.filter((row) => !row.in_force);
    if (!waiting.length) return null;
    const was = (shownBefore || []).indexOf(answered);
    if (was >= 0) {
      const after = new Set(shownBefore.slice(was + 1));
      const found = waiting.find((row) => after.has(row.target));
      if (found) return found;
    }
    return waiting[0];
  },
  /* The keyboard stays on the list: where the button pressed has gone with its row, the row open
   * takes the focus, so the arrows and Enter carry on from it. */
  focus(pane) {
    const active = document.activeElement;
    if (active && active !== document.body && document.contains(active)) return;
    const row = pane.querySelector('.row.selected');
    if (row) row.focus({preventScroll: false});
  },
  /* The one line a pane says when nothing is left, under what was just recorded. */
  done(pane, said, flash) {
    fill(pane, flash ? el('p', {class: 'note kept', text: flash}) : null,
      el('p', {class: 'nothing', 'data-advance': 'done', text: said}));
  },
};

/* A row the keyboard reaches: Enter or Space opens it (``open``, or a click on the row), and the
 * arrows move to the row beside. */
function keyedRow(node, open) {
  node.setAttribute('tabindex', '0');
  node.addEventListener('keydown', (event) => {
    if (event.target !== node) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (open) open();
      else node.click();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const beside = event.key === 'ArrowDown' ? node.nextElementSibling : node.previousElementSibling;
      if (beside && beside.classList.contains('row')) {
        event.preventDefault();
        beside.focus();
      }
    }
  });
  return node;
}

/* ------------------------------------------------------------------ the note box */

/* The note box has no label: it says "notes/context" in the entry field, on every page with a
 * note. */
const NOTE_PLACEHOLDER = 'notes/context';

function noteInput() {
  return el('input', {type: 'text', class: 'note-input', placeholder: NOTE_PLACEHOLDER,
    'aria-label': NOTE_PLACEHOLDER});
}

/* What the engine recognizes in a note, as it is typed. The note is read by the engine's own name
 * reader and the Dictionary (``/api/note-terms``: the census's tokenizer and the binder's lookup,
 * no model). Each term it binds is a chip beside the box ("mouse: an organism"), and each word
 * nothing binds is a quieter chip. Nothing is written until a chip is clicked. A term is then kept
 * as an observation about what is asked here, through the one answer route. A word nothing binds
 * opens the same "what it means" row a word question offers, and its answer is that question's
 * write. */
const CHIP_NOUNS = {questions: 'question', classes: 'group', claims: 'file', candidates: 'pair',
  extensions: 'extension'};

function noteField(input, target, noun, keptBefore = []) {
  const chips = el('div', {class: 'note-chips', 'aria-live': 'polite'});
  const asking = el('div', {class: 'note-asking'});
  if (target) readNoteAsTyped(input, chips, asking, target, noun || 'row', keptBefore);
  return el('div', {class: 'field note-field'}, [input, chips, asking]);
}

function readNoteAsTyped(input, chips, asking, target, noun, keptBefore) {
  let timer = null;
  let asked = '';
  const kept = new Set(keptBefore || []);
  input.addEventListener('input', () => {
    // The box's help, shown on focus, would sit over the chips once typing starts.
    const tip = document.getElementById('help-tip');
    if (tip) tip.hidden = true;
    window.clearTimeout(timer);
    timer = window.setTimeout(async () => {
      const text = input.value.trim();
      if (text === asked) return;
      asked = text;
      if (!text) {
        chips.replaceChildren();
        return;
      }
      try {
        const read = await api(`/api/note-terms?${new URLSearchParams({text})}`);
        if (input.value.trim() !== text) return;
        fill(chips, ...(read.terms || []).map((term) => termChip(term, target, noun, kept)),
          ...(read.unbound || []).map((word) => wordChip(word, asking, read.theme_kinds || [])));
      } catch (error) {
        chips.replaceChildren();
      }
    }, 300);
  });
}

function termChip(term, target, noun, kept) {
  const done = kept.has(term.term);
  const chip = el('button', {type: 'button', class: done ? 'note-chip kept' : 'note-chip bound',
    'data-term': term.term, text: done ? `${term.said} · kept` : term.said,
    title: `Keep "${term.text}" with this ${noun}. Ingence does not use it yet.`,
    ...(done ? {disabled: ''} : {}),
    onclick: async () => {
      if (!(await askActor())) return;
      try {
        await write('/api/review/answer', {actor: me.actor, target,
          observe: {text: term.text, term: term.term, theme_kind: term.theme_kind}});
        kept.add(term.term);
        chip.className = 'note-chip kept';
        chip.disabled = true;
        chip.textContent = `${term.said} · kept`;
        chip.title = `Kept with this ${noun}. Ingence does not use it yet.`;
      } catch (error) {
        chip.classList.add('refused');
        chip.title = String(error.message || error);
      }
    }});
  return chip;
}

function wordChip(word, asking, themeKinds) {
  return el('button', {type: 'button', class: 'note-chip unbound', 'data-word': word.word,
    text: word.word, title: `Ingence does not know "${word.word}" yet. Say what it means.`,
    onclick: () => {
      const status = el('p', {class: 'note', role: 'status'});
      fill(asking, el('p', {class: 'note'}, ['What does ', el('mark', {text: word.word}),
        ' mean here?']),
      defineRow(themeKinds, async (meaning, axis) => {
        if (!meaning.trim() || !axis) {
          status.className = 'note refused';
          status.textContent = 'Write what it means and choose what kind of thing it is.';
          return;
        }
        if (!(await askActor())) return;
        try {
          const written = await write('/api/review/answer', {actor: me.actor,
            target: `term:${word.word.toLowerCase()}`, answer: meaning, theme_kind: axis});
          fill(asking, el('p', {class: 'note kept', text: `Saved as answer ${written.sequence}: `
            + `"${word.word}" means ${meaning}.`}));
        } catch (error) {
          status.className = 'note refused';
          status.textContent = String(error.message || error);
        }
      }), status);
    }});
}

/* ------------------------------------------------------------------ a rule, one click away */

/* A rule's name and version as the handle that opens it on the Rules page: never a sentence's
 * subject. */
function ruleHandle(ruleId, version) {
  if (!ruleId) return null;
  return el('button', {class: 'link rule-handle', type: 'button', title: 'Open this rule',
    text: `${ruleId}${version ? ` v${version}` : ''}`,
    onclick: () => {
      navigate('rules');
      window.setTimeout(() => {
        const found = document.getElementById(`rule-${ruleId}`);
        if (found) found.scrollIntoView({block: 'center'});
      }, 400);
    }});
}

/* "What is this?": the rule's plain words and one invented example, one click away. */
function whatIsThis(row) {
  if (!row.rule_plain && !row.example) return null;
  return el('details', {class: 'what-is-this'}, [el('summary', {text: 'about this rule'}),
    row.rule_plain ? el('p', {text: row.rule_plain}) : null,
    row.example ? el('p', {class: 'faint'}, [el('strong', {text: 'For example: '}), row.example])
      : null,
    el('p', {class: 'faint'}, ['The rule: ', ruleHandle(row.rule_id, row.rule_version)])]);
}

/* The five sheet kinds, and how each page names and shows its rows: one or two sentences on the
 * page, the rest behind "What is this?", and one plain sentence where nothing is waiting, so that
 * the Dashboard makes clear when there is nothing to review. */
const KINDS = {
  questions: {
    title: 'Questions Ingence could not answer',
    lead: 'Each row is one thing a rule could not decide. Ingence asks it once, for all its files.',
    more: 'Your answer to a row applies to every file that raised it. The most useful questions '
      + 'come first; the list ends with how they are ranked. Questions from inside an installed '
      + 'program are in the Programs section of Advanced, one row per program.',
    empty: 'Nothing to review. No rule raised a question in this scan.',
    done: 'Nothing left to review. You answered every question in this scan.',
    /* Each row says what it asks in words, and what its count counts. */
    head: ['question', 'about', 'rule'],
    cells: (row) => [
      el('td', {}, row.heading ? [el('span', {text: row.heading})] : [el('code', {text: row.raised})]),
      el('td', {class: 'n', text: `${Number(row.entries_affected).toLocaleString()} `
        + `${row.counted ? (Number(row.entries_affected) === 1 ? row.counted.replace(/s$/, '') : row.counted) : ''}`}),
      el('td', {}, [el('span', {class: 'tag plain', text: row.rule_id})]),
    ],
  },
  classes: {
    title: 'Word groups: experiments first, then files outside experiments',
    lead: 'First, whether whole experiments belong together. Then the groups of files that no '
      + 'one experiment holds.',
    more: 'Ingence asks about experiments first: folders a rule found by their contents or by a '
      + 'dated name. A group inside one experiment is listed under it, below, and Ingence does not '
      + 'ask about it. Open it to answer anyway. Ingence leaves out a group of one file, and a '
      + 'group inside an installed program.',
    empty: 'Nothing to review. No rule put two or more files or experiments together in this scan.',
    done: 'Nothing left to review. You answered every group in this scan.',
    head: ['group', '#members', 'rule'],
    cells: (row) => [
      el('td', {}, row.level === 'experiments'
        ? [el('span', {class: 'tag now', text: 'experiments'}), ' ', el('code', {text: row.spelled})]
        : [el('code', {text: row.pattern || row.spelled}),
          row.alike_by || row.varies ? el('div', {class: 'faint', text: (row.alike_by || row.varies)
            + (row.experiments_spanned > 1 ? `; in ${row.experiments_spanned} experiments` : '')})
            : null]),
      el('td', {class: 'n', text: Number(row.members).toLocaleString()}),
      el('td', {}, [el('span', {class: 'tag plain', text: row.rule_id}),
        (row.also || []).length ? el('span', {class: 'faint', text: ` and ${row.also.length} more`,
          title: row.also.map((other) => other.rule_id).join(', ')}) : null]),
    ],
  },
  claims: {
    title: 'Conclusion samples: a few files of each kind',
    lead: 'A few files for each result a rule found. Check that each one is right.',
    more: 'For each value a rule found, Ingence shows enough files to stand for all of them. It '
      + 'takes one file from each folder or file type that holds a tenth of them: at least 3, at '
      + 'most 10. One answer can cover every file like it. Values that differ for each file, '
      + 'such as a time, are grouped under the list for information.',
    empty: 'Nothing to review. No rule found what a file is in this scan.',
    done: 'Nothing left to review. You answered every sampled result in this scan.',
    head: ['file', 'result', 'rule'],
    cells: (row) => [
      el('td', {}, [el('code', {text: row.name})]),
      el('td', {text: valueSaid(row.value).slice(0, 80)}),
      el('td', {}, [el('span', {class: 'tag plain', text: row.rule_id})]),
    ],
  },
  candidates: {
    title: 'Suggested pairs: files that may be made from other files',
    lead: 'Pairs of files a rule found worth checking. Nothing changes until you answer.',
    more: 'A suggested pair is only a suggestion. Ingence treats one file as made from the other only '
      + 'after you answer here.',
    empty: 'Nothing to review. No rule suggested a pair of files in this scan.',
    done: 'Nothing left to review. You answered every suggested pair in this scan.',
    head: ['earlier', '', 'later', 'folder'],
    cells: (row) => [
      el('td', {}, [el('code', {text: row.source_name})]),
      el('td', {text: '→'}),
      el('td', {}, [el('code', {text: row.target_name})]),
      el('td', {class: 'note-cell', text: row.folder_path || '.'}),
    ],
  },
  extensions: {
    title: 'Extensions without a name',
    lead: 'Say what files with each extension are, and whether Ingence should read their contents.',
    more: 'Ingence counts here every file whose extension no file type names. Your answer changes '
      + 'the order in which Ingence reads contents. It never stops Ingence from reading a file.',
    empty: 'Nothing to review. Every extension in this scan has a name.',
    done: 'Nothing left to review. You answered every unnamed extension in this scan.',
    head: ['extension', '#files', '#opened'],
    cells: (row) => [
      el('td', {}, [el('code', {text: row.extension ? `.${row.extension}` : '(no extension)'})]),
      el('td', {class: 'n', text: Number(row.files).toLocaleString()}),
      el('td', {class: 'n', text: Number(row.opened).toLocaleString()}),
    ],
  },
};

/* The page's own few words, and the rest behind a disclosure. */
function explained(lead, more) {
  return [
    el('p', {class: 'lead', text: lead}),
    more ? el('details', {class: 'what'}, [el('summary', {text: 'About this page'}),
      el('p', {text: more})]) : null,
  ];
}

/* An answered row keeps its place with its answer until the next run reads it, on every page of
 * questions. The pages ask for ``answered=waiting``, the answers the run has not read, and the row
 * shows its answer in the person's color. Once Re-run has read the answer, the row is gone. */
const sheetState = {kind: null, data: null, at: null, top: '', rule: '', chosen: new Set(),
  flash: '', showAnswered: true, offset: 0, shownOrder: []};

/* Fifty rows to a page, every row counted. The server counts every row the filters keep
 * (``listed``) and sends one page of them. The pager says which page it is and moves through them.
 * The engine puts no cap on what it proposes. */
const PAGE_SIZE = 50;

function pager(data, reload) {
  const listed = Number(data.listed ?? data.rows.length);
  if (listed <= PAGE_SIZE && !sheetState.offset) return null;
  const first = listed ? sheetState.offset + 1 : 0;
  const last = Math.min(sheetState.offset + PAGE_SIZE, listed);
  const move = (to) => { sheetState.offset = to; sheetState.at = null; reload(); };
  return el('p', {class: 'pager', 'data-help': 'review.pager'}, [
    el('button', {class: 'link', type: 'button', text: '\u2190 previous 50',
      ...(sheetState.offset ? {} : {disabled: ''}),
      onclick: () => move(Math.max(0, sheetState.offset - PAGE_SIZE))}),
    ` rows ${first.toLocaleString()} to ${last.toLocaleString()} of ${listed.toLocaleString()} `,
    el('button', {class: 'link', type: 'button', text: 'next 50 \u2192',
      ...(last < listed ? {} : {disabled: ''}),
      onclick: () => move(sheetState.offset + PAGE_SIZE)}),
  ]);
}

/* The line saying how many answered rows are hidden, and the control bringing them back: the
 * Coverage view's (app.js). The pages of questions keep an answer only while it waits for Re-run. */
function answeredToggle(hidden, shown, onToggle) {
  if (!hidden && !shown) return null;
  return el('span', {class: 'answered-toggle'}, [
    shown ? 'answered rows shown ' : `${hidden.toLocaleString()} answered, hidden `,
    el('button', {class: 'link', onclick: onToggle,
      text: shown ? 'hide answered' : 'show answered'}),
  ]);
}

function label(word) {
  return String(word).replace(/_/g, ' ');
}

/* A stored word as a page shows it: the label the route serves beside it
 * (``record/verdicts.LABELS``, "Can't say" for ``cannot_say``); the stored word is what is sent. */
function shownWord(word) {
  if (word && typeof word === 'object') {
    return word.word === 'cannot_say' ? "Can't say" : (word.label || label(word.word));
  }
  return word === 'cannot_say' ? "Can't say" : label(word);
}

/* The decision in force on a row, in words, and the file it was imported from, if any. */
/* Where an imported answer came from, and who imported it where that is not its author. */
function importedWords(said) {
  return `imported from ${said.imported_from}`
    + (said.imported_by && said.imported_by !== said.actor ? ` by ${said.imported_by}` : '');
}

function decidedTag(row) {
  const now = row.in_force;
  // A note that decides nothing ("Can't say" on a kind that has no such verdict) is shown as one.
  if (!now) {
    return row.noted ? el('span', {class: 'tag plain', text: `${row.noted} (noted)`})
      : el('span', {class: 'unknown', text: ''});
  }
  // The person's color: magenta only where a person said so.
  return el('span', {}, [
    el('span', {class: 'tag now person', text: shownAnswerLabel(now) + (now.reading
      ? `: ${label(now.reading)}` : '')}),
    now.imported_from ? el('span', {class: 'tag imported', text: importedWords(now)}) : null,
  ]);
}

/* What an answer in force is called on a button: its label, and "Can't say" for ``cannot_say``
 * even where an older route served another. */
function shownAnswerLabel(now) {
  return now.word === 'cannot_say' ? "Can't say" : (now.label || label(now.word));
}

/* A row answered on this page, drawn at once from what was just written: its answer in force, or,
 * for "Can't say" written as a note, the note (which decides nothing, so nothing is in force). */
function shownAnswer(row, choice, written) {
  if (choice.word === null) {
    row.noted = CANNOT_SAY;
    return;
  }
  row.noted = null;
  row.in_force = {kind: 'answer', word: choice.word, label: choice.label,
    actor: written.actor || me.actor, occurred_at: written.occurred_at || new Date().toISOString(),
    sequence: written.sequence == null ? null : written.sequence, note: written.note || null,
    correct_value: null, reading: null, imported_from: null, imported_by: null};
}

/* How far a page has got, in one line: what is waiting, what was answered and left the list. */
function progressLine(progress, shown, toggle) {
  const waiting = `${progress.remaining.toLocaleString()} waiting`;
  const narrowed = shown && shown.rows !== progress.rows
    ? ` · in this view, ${shown.remaining.toLocaleString()} waiting` : '';
  return el('p', {class: 'progress'}, [waiting + narrowed,
    toggle ? ' · ' : '', toggle]);
}

function filterBar(data, reload) {
  const tops = el('select', {'aria-label': 'Top-level folder', onchange: (event) => { sheetState.top = event.target.value; sheetState.offset = 0; reload(); }},
    [el('option', {value: '', text: 'every top-level folder'}),
      ...(data.filters.tops || []).map((item) => el('option', {value: item.value,
        text: `${item.value} (${item.n})`}))]);
  tops.value = sheetState.top;
  const rules = el('select', {'aria-label': 'Rule', onchange: (event) => { sheetState.rule = event.target.value; sheetState.offset = 0; reload(); }},
    [el('option', {value: '', text: 'every rule'}),
      ...(data.filters.rules || []).map((item) => el('option', {value: item.value,
        text: `${item.value} (${item.n})`}))]);
  rules.value = sheetState.rule;
  const ruled = (data.filters.rules || []).length > 0;
  return el('div', {class: 'filters'}, [
    el('label', {text: 'Top-level folder '}), tops,
    ruled ? el('label', {text: ' Rule '}) : null, ruled ? rules : null,
  ]);
}

/* Every target one row answers: its own, and, for a set several rules formed, each other rule's
 * class, as a single answer from the pane writes them. */
function targetsOf(row) {
  return [row.target, ...(row.also || []).map((other) => other.target)];
}

/* ------------------------------------------------------------------ bulk answering */

/* One answer for the rows a person chose: everywhere a table of questions appears (the leads, each
 * group, Groupings, Conclusion samples, Proposed pairs, Programs), the header box chooses the
 * page's rows (mixed when some are chosen). Once one row is chosen, a bar above the table says
 * `3 of 5 chosen · answer them together:`, offers the answers the chosen rows have in common (only
 * "Can't say" where their kinds differ), takes one note for all, and says it is recorded under the
 * person's name and waits for Re-run. Answering writes one Record event for each row, in one
 * request, with the same note. The rows show their answer in the person's color and the bar closes.
 * Escape clears the choice.
 *
 * A table gives the chooser its items: ``{key, name, targets, words, set}``, where ``words`` are
 * the buttons its row takes (``{word, label, title}``; a ``word`` of ``null`` is "Can't say"
 * written as a note, which decides nothing) and ``targets`` every Record target one answer on the
 * row writes. The chooser knows nothing else about a row. ``onAnswered(item, choice, written)`` is
 * the table's own way to show the answer. */
const CANNOT_SAY = "Can't say";
const choosers = new Set();

/* A server word as a button says it: the Record's word, and "Can't say" for ``cannot_say``. */
function buttonWord(word) {
  const stored = word.word;
  return {word: stored, label: stored === 'cannot_say' ? CANNOT_SAY : (word.label || label(stored)),
    title: word.explained || word.title || ''};
}

/* The answers every item shares, in the first item's order; "Can't say" is always last. */
function commonWords(items) {
  if (!items.length) return [];
  const first = items[0].words.filter((word) => word.word !== null && word.word !== 'cannot_say');
  const shared = first.filter((word) => items.every((item) =>
    item.words.some((other) => other.word === word.word)));
  return [...shared, {word: null, label: CANNOT_SAY,
    title: 'Saved as a note only. It answers nothing.'}];
}

function chooser({items, noun, onAnswered, said, listed = null, held = null}) {
  /* ``held`` is a choice kept from the last drawing of the same table (a filter, a page): a row no
   * longer shown is dropped from it, and the others stay chosen. */
  const chosen = held || new Set();
  const boxes = new Map();
  const byKey = new Map(items.map((item) => [item.key, item]));
  const selectable = items.filter((item) => item.targets.length);
  const header = el('input', {type: 'checkbox', class: 'select-all', 'data-help': 'questions.select',
    'aria-label': `Select all ${noun}s shown`});
  if (!selectable.length) header.disabled = true;
  /* A table that pages chooses the rows on its page, and says that the others are left alone. */
  if (listed != null && listed > items.length) {
    header.title = `Selects the ${items.length.toLocaleString()} ${items.length === 1 ? 'row' : 'rows'} `
      + `on this page. ${(listed - items.length).toLocaleString()} more are on other pages.`;
  }
  const count = el('strong', {class: 'bulk-count'});
  const buttons = el('span', {class: 'bulk-buttons', role: 'group', 'aria-label': 'Answers'});
  const noteBox = el('input', {type: 'text', class: 'note-input bulk-note',
    'aria-label': 'One note for every chosen row', maxlength: '4000'});
  const status = el('span', {class: 'bulk-status', role: 'status', 'aria-live': 'polite'});
  const bar = el('div', {class: 'bulk-bar', role: 'region', 'aria-label': 'Answer the chosen rows together',
    hidden: ''}, [count, ' ', el('span', {class: 'bulk-lead', text: 'answer them together:'}), ' ',
    buttons, noteBox, el('span', {class: 'bulk-recorded',
      text: 'recorded under your name · waits for Re-run'}), status]);

  const refresh = () => {
    const keys = [...chosen].filter((key) => byKey.has(key));
    header.checked = keys.length > 0 && keys.length === selectable.length;
    header.indeterminate = keys.length > 0 && keys.length < selectable.length;
    bar.hidden = !keys.length;
    if (!keys.length) return;
    const rows = keys.map((key) => byKey.get(key));
    count.textContent = `${keys.length.toLocaleString()} of ${selectable.length.toLocaleString()} chosen`;
    noteBox.placeholder = `one note for all ${keys.length.toLocaleString()} (optional)`;
    const shared = commonWords(rows);
    fill(buttons, ...shared.map((word) => el('button', {type: 'button', class: 'answer',
      'data-help': 'questions.bulk', 'data-word': word.word === null ? 'cannot_say' : word.word,
      title: word.title || '',
      onclick: () => answerChosen(word)}, [el('strong', {text: word.label})])));
  };

  const toggle = (key, on) => {
    if (on) chosen.add(key);
    else chosen.delete(key);
    const box = boxes.get(key);
    if (box) box.checked = on;
    refresh();
  };
  const clear = () => {
    chosen.clear();
    for (const box of boxes.values()) box.checked = false;
    refresh();
  };
  header.addEventListener('click', (event) => {
    event.stopPropagation();
    // What the box says is read once: choosing the first row redraws it as mixed.
    const on = header.checked;
    for (const item of selectable) {
      if (on) chosen.add(item.key);
      else chosen.delete(item.key);
      const box = boxes.get(item.key);
      if (box) box.checked = on;
    }
    refresh();
  });

  /* The box of one row; the row's own click never reaches it. */
  const boxFor = (item) => {
    const box = el('input', {type: 'checkbox', 'aria-label': `Select: ${item.name}`,
      onclick: (event) => { event.stopPropagation(); toggle(item.key, event.target.checked); }});
    if (!item.targets.length) {
      box.disabled = true;
      return box;
    }
    box.checked = chosen.has(item.key);
    boxes.set(item.key, box);
    return box;
  };

  async function answerChosen(choice) {
    const rows = [...chosen].filter((key) => byKey.has(key)).map((key) => byKey.get(key));
    if (!rows.length) return;
    if (!(await askActor())) {
      status.className = 'bulk-status refused';
      status.textContent = 'Ingence needs your name to save an answer. Set your name first.';
      return;
    }
    const typed = noteBox.value.trim();
    status.className = 'bulk-status';
    status.textContent = 'Saving...';
    for (const button of buttons.querySelectorAll('button')) button.disabled = true;
    try {
      let result;
      if (choice.word === null) {
        /* "Can't say": the verdict where the row has one, a note where it has none; either decides
         * nothing. One request, each row as it takes it. */
        const decisions = rows.flatMap((item) => item.targets.map((target) => (
          item.words.some((word) => word.word === 'cannot_say')
            ? {target, word: 'cannot_say', note: typed || undefined}
            : {target, note: typed ? `${CANNOT_SAY}. ${typed}` : CANNOT_SAY})));
        result = await write('/api/review/answer', {actor: me.actor, decisions});
      } else {
        result = await write('/api/review/answer', {actor: me.actor,
          targets: rows.flatMap((item) => item.targets), word: choice.word, note: typed || undefined});
      }
      const refused = result.refused || [];
      const writtenOf = new Map(result.written.map((entry) => [entry.target, entry]));
      const turnedAway = new Set(refused.map((item) => item.target));
      for (const item of rows) {
        // A row is answered unless every target it names was refused.
        if (item.targets.every((target) => turnedAway.has(target))) continue;
        const hit = item.targets.map((target) => writtenOf.get(target)).find(Boolean) || {};
        /* Each row is handed the choice it was sent: "Can't say" is the verdict for a row that has
         * one and a note for a row that has none, and the page shows what a reload shows. */
        const sent = choice.word !== null ? choice
          : (item.words.some((word) => word.word === 'cannot_say')
            ? {word: 'cannot_say', label: CANNOT_SAY, title: choice.title} : choice);
        onAnswered(item, sent, {...hit, note: typed, actor: me.actor});
      }
      const told = `Saved ${plural(result.written.length, 'answer', 'answers')} by ${me.actor}`
        + (refused.length ? `; ${refused.length.toLocaleString()} refused: `
          + [...new Set(refused.map((item) => item.error))].slice(0, 3).join(' / ') : '') + '.';
      noteBox.value = '';
      chosen.clear();
      for (const box of boxes.values()) box.checked = false;
      refresh();
      if (said) said.textContent = told;
    } catch (error) {
      status.className = 'bulk-status refused';
      status.textContent = String((error && error.message) || error);
      for (const button of buttons.querySelectorAll('button')) button.disabled = false;
    }
  }

  for (const key of [...chosen]) if (!byKey.has(key)) chosen.delete(key);
  const made = {header, bar, boxFor, toggle, clear, chosen, items, byKey, selectable, refresh};
  refresh();
  choosers.add(made);
  return made;
}

/* Escape clears every choice on the page (the keyboard table, "Every page"). A dialog or a menu
 * that is open takes the key first. */
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || event.defaultPrevented || document.querySelector('dialog[open]')) return;
  for (const made of [...choosers]) {
    if (!document.contains(made.bar)) {
      choosers.delete(made);
      continue;
    }
    if (made.chosen.size) {
      made.clear();
      event.preventDefault();
    }
  }
});

/* The keys of a row that carries a box (the keyboard table, "Questions"): ``Space`` toggles its box,
 * ``Shift`` with the arrows extends the choice to the row beside, the arrows alone move, ``A``
 * puts the focus on the row's first answer button and ``Enter`` opens its card. A key typed in a
 * field or on a button inside the row is the field's or the button's own. */
function chosenRow(node, made, key, open) {
  node.setAttribute('tabindex', '0');
  node.addEventListener('keydown', (event) => {
    if (event.target !== node) return;
    const beside = (step) => {
      let next = step > 0 ? node.nextElementSibling : node.previousElementSibling;
      while (next && !next.classList.contains('row')) {
        next = step > 0 ? next.nextElementSibling : next.previousElementSibling;
      }
      return next;
    };
    if (event.key === ' ') {
      event.preventDefault();
      if (made.byKey.get(key) && made.byKey.get(key).targets.length) {
        made.toggle(key, !made.chosen.has(key));
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      open();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const next = beside(event.key === 'ArrowDown' ? 1 : -1);
      if (!next) return;
      event.preventDefault();
      if (event.shiftKey) {
        // Extending: the row left and the row arrived at are both chosen.
        if (made.byKey.get(key) && made.byKey.get(key).targets.length) made.toggle(key, true);
        const other = next.dataset.key;
        if (other && made.byKey.get(other) && made.byKey.get(other).targets.length) made.toggle(other, true);
      }
      next.focus();
    } else if (event.key === 'a' || event.key === 'A') {
      const first = node.querySelector('button.answer');
      if (first) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  return node;
}

/* Whether the view that asked is still the one shown: rows read for a page a person left while they
 * loaded are dropped, never drawn over the page that replaced it. */
function stillShowing(view) {
  return state.view === view;
}

async function showSheet(kind) {
  if (sheetState.kind !== kind) {
    Object.assign(sheetState, {kind, at: null, top: '', rule: '', showAnswered: true, offset: 0,
      shownOrder: []});
    sheetState.chosen.clear();
  }
  const reload = () => showSheet(kind).catch(reportError);
  const query = new URLSearchParams({limit: String(PAGE_SIZE), offset: String(sheetState.offset)});
  if (sheetState.top) query.set('top', sheetState.top);
  if (sheetState.rule) query.set('rule', sheetState.rule);
  if (sheetState.showAnswered) query.set('answered', 'waiting');
  states.loading(listPane, 'Loading the rows...');
  const data = await api(`/api/pages/${kind}?${query}`);
  if (!stillShowing(VIEW_OF[kind] || kind)) return;
  sheetState.data = data;
  // A page past the end (rows answered since) goes back to the last page that has rows.
  if (!data.rows.length && sheetState.offset && Number(data.listed || 0) > 0) {
    sheetState.offset = Math.max(0, Math.floor((Number(data.listed) - 1) / PAGE_SIZE) * PAGE_SIZE);
    return showSheet(kind);
  }
  const config = KINDS[kind];
  const rows = data.rows;
  /* One box in the header, one bar above the table: the chooser (above) holds the choice. A row
   * hidden by a filter or on another page is never chosen, because only the rows drawn here are its
   * items. A proposal (answered on its own row) has no box. */
  const said = el('p', {class: 'bulk-said', role: 'status', 'aria-live': 'polite'});
  const trs = new Map();
  const items = rows.map((row) => ({key: row.target, name: questionLabel(row), row,
    targets: row.proposal ? [] : targetsOf(row), words: (row.words || []).map(buttonWord)}));
  const made = chooser({items, noun: CHIP_NOUNS[kind] || 'row', said,
    listed: Number(data.listed ?? rows.length), held: sheetState.chosen,
    onAnswered: (item, choice, written) => {
      shownAnswer(item.row, choice, written);
      const tr = trs.get(item.key);
      if (tr) tr.lastElementChild.replaceChildren(decidedTag(item.row));
    }});
  const body = items.map((item) => {
    const row = item.row;
    const box = row.proposal ? el('span', {class: 'faint', title: 'answered on its own row', text: ''})
      : made.boxFor(item);
    const open = () => {
      sheetState.at = row.target;
      markSelected();
      setFocus({kind: 'question', id: row.target, label: questionLabel(row)});
      showRow(row, reload).catch((error) => states.failed(detailPane, error));
    };
    const tr = chosenRow(el('tr', {class: row.target === sheetState.at ? 'row selected' : 'row',
      'data-target': row.target, 'data-key': row.target, onclick: open}, [
      el('td', {}, [box]),
      ...config.cells(row),
      el('td', {}, [decidedTag(row)]),
    ]), made, row.target, open);
    trs.set(row.target, tr);
    return tr;
  });
  setWaiting(kind, data.progress.remaining);
  const told = data.informational || [];
  if (!data.progress.rows && !told.length && !(data.within_experiments || []).length) {
    // Nothing to review: one plain sentence, and what the page is for behind a disclosure.
    const wordsWaiting = kind === 'questions' ? (data.word_questions || {}).waiting : null;
    fill(listPane, el('h2', {text: config.title}),
      el('p', {class: 'nothing', text: config.empty}),
      wordsWaiting ? el('p', {class: 'faint', text: wordsWaiting}) : null,
      explained('', config.more).slice(1));
    if (!openFocus(detailPane)) states.empty(detailPane, 'Nothing to review here.');
    return;
  }
  const extra = [];
  if (kind === 'questions') {
    extra.push(dimensionsNote(data));
    if (data.set_aside) {
      extra.push(el('p', {class: 'faint', text: `${data.set_aside} more question`
        + `${data.set_aside === 1 ? ' comes' : 's come'} only from inside installed programs. `
        + 'See the Programs section of Advanced.'}));
    }
    if (data.asked_on_groupings) {
      extra.push(el('p', {class: 'faint', text: `${data.asked_on_groupings} question`
        + `${data.asked_on_groupings === 1 ? '' : 's'} about experiments that may belong `
        + 'together, asked first in the Word groups section of Advanced.'}));
    }
    /* Words nothing binds: why none is asked yet, or what is not asked and why. */
    const words = data.word_questions || {};
    if (words.waiting) extra.push(el('p', {class: 'faint', text: words.waiting}));
    if ((words.not_asked || []).length) {
      extra.push(el('p', {class: 'faint', text: `Words Ingence does not ask about: ${words.not_asked.join('; ')}.`}));
    }
  }
  if (kind === 'classes' && data.inside_programs) {
    extra.push(el('p', {class: 'faint', text: `Ingence leaves out ${data.inside_programs} groups `
      + 'that lie inside installed programs.'}));
  }
  const allDone = !data.progress.remaining;
  const filtered = Boolean(sheetState.top || sheetState.rule);
  const nothing = !data.progress.rows
    ? 'Nothing waiting. The results below are for information only.'
    : allDone ? config.done : 'Nothing waiting in this view.';
  fill(listPane,
    el('h2', {text: config.title}),
    ...explained(config.lead, config.more),
    progressLine(data.progress, data.shown, null),
    allDone && !filtered && !rows.length ? null : filterBar(data, reload),
    kind === 'classes' ? resolutionsSection() : null,
    ...(rows.length ? extra : []),
    pager(data, reload),
    rows.length ? made.bar : null,
    rows.length ? said : null,
    rows.length
      ? table([made.header, ...config.head, 'answer'], body)
      : el('p', {class: 'nothing', text: nothing}),
    rows.length > 10 ? pager(data, reload) : null,
    toldGroups(told, reload),
    heldByExperiments(data, reload),
  );
  const examples = [...(data.informational || []).flatMap((group) => group.examples),
    ...(data.within_experiments || []).flatMap((held) => held.groups)];
  // The question under investigation, where it is on this page, is the one opened.
  if (!sheetState.at && focusHeld.kind === 'question') sheetState.at = focusHeld.id;
  const current = rows.find((row) => row.target === sheetState.at)
    || examples.find((row) => row.target === sheetState.at);
  const shownBefore = sheetState.shownOrder;
  sheetState.shownOrder = rows.map((row) => row.target);
  // Just answered: the row has left (or, with answered rows shown, is answered now).
  const answeredNow = Boolean(sheetState.flash) && (!current || Boolean(current.in_force));
  if (current && !answeredNow) {
    await showRow(current, reload);
    return;
  }
  /* The next waiting row fills the pane (auto-advance), unless, on opening, something else is being
   * looked at (a file, a word, a run): that stays open, named under the header. */
  const lookingElsewhere = !answeredNow && focusHeld.id && focusHeld.kind !== 'question';
  const next = lookingElsewhere ? null : advance.next(rows, sheetState.at, shownBefore);
  if (next) {
    sheetState.at = next.target;
    markSelected();
    if (focusHeld.kind === 'question') {
      setFocus({kind: 'question', id: next.target, label: questionLabel(next)});
    }
    await showRow(next, reload);
    advance.focus(listPane);
  } else if (answeredNow) {
    advance.done(detailPane, allDone ? config.done : 'Nothing waiting in this view.',
      sheetState.flash);
    sheetState.flash = '';
  } else if (!openFocus(detailPane)) states.empty(detailPane, 'Nothing waiting here.');
}

/* The groups each experiment holds, for information: not asked, because the experiment above them
 * is the level a person answers at; each written as its name pattern with the parts that vary
 * named, and each still opens to be answered. */
function heldByExperiments(data, reload) {
  const held = data.within_experiments || [];
  if (!held.length) return null;
  return el('div', {class: 'told'}, [
    el('h3', {text: `Inside each experiment, for information: ${Number(data.held_by_experiments)
      .toLocaleString()} groups in ${held.length.toLocaleString()} experiments`}),
    el('p', {class: 'faint', text: 'Ingence does not ask about these: each lies inside an '
      + 'experiment, and you answer for the experiment. Open one to answer it anyway.'}),
    ...held.map((experiment) => el('details', {class: 'told-group'}, [
      el('summary', {}, [el('strong', {text: experiment.name}), ' ',
        el('span', {class: 'faint', text: `${experiment.kind === 'named' ? 'an experiment by its dated name'
          : `${label(experiment.kind)}, by what it holds`} · ${experiment.groups.length} `
          + `group${experiment.groups.length === 1 ? '' : 's'}`})]),
      el('ul', {class: 'told-examples'}, experiment.groups.map((group) => el('li', {
        class: group.target === sheetState.at ? 'row selected' : 'row',
        'data-target': group.target,
        onclick: () => {
          sheetState.at = group.target;
          markSelected();
          showRow(group, reload).catch(reportError);
        }}, [
        el('code', {text: group.pattern || group.spelled}), ' ',
        el('span', {class: 'faint', text: `${group.members} files, ${group.varies}`
          /* A series counted in several experiments is listed under each. */
          + (group.series ? `, in ${group.experiments_spanned} experiments` : '')}), ' ',
        el('span', {class: 'tag plain', text: group.rule_id}),
        group.in_force ? ' ' : null, group.in_force ? decidedTag(group) : null,
      ]))),
    ])),
  ]);
}

/* Conclusions whose value each file has its own of (a time, say), grouped by what the files share,
 * a few examples each, and shown as information. Nothing here waits for an answer or counts among
 * the remaining, and an example opens to be answered if wanted. */
function toldGroups(groups, reload) {
  if (!groups || !groups.length) return null;
  const parts = (held) => Object.entries(held || {});
  return el('div', {class: 'told'}, [
    el('h3', {text: 'For information: values that differ for each file'}),
    el('p', {class: 'faint', text: 'Grouped by what the files share, with a few examples from '
      + 'different folders. Nothing here waits for an answer. Open an example to answer it.'}),
    ...groups.map((group) => el('div', {class: 'told-group'}, [
      el('p', {class: 'told-head'}, [
        el('strong', {text: `${Number(group.files).toLocaleString()} `
          + `file${group.files === 1 ? '' : 's'}`}),
        ' ', ...parts(group.shared).map(([part, value]) => el('code', {text: `${label(part)}: ${value}`})),
        ' ', el('span', {class: 'tag plain', text: group.rule_id}),
      ]),
      el('p', {class: 'told-why', text: group.explained}),
      el('ul', {class: 'told-examples'}, group.examples.map((example) => el('li', {
        class: example.target === sheetState.at ? 'row selected' : 'row',
        'data-target': example.target,
        onclick: () => {
          sheetState.at = example.target;
          markSelected();
          showRow(example, reload).catch(reportError);
        }}, [
        el('code', {text: example.name}), ' ',
        el('span', {class: 'faint', text: parts(example.differing)
          .map(([part, value]) => `${label(part)} ${value}`).join(', ')}),
        example.in_force ? ' ' : null, example.in_force ? decidedTag(example) : null,
      ]))),
    ])),
  ]);
}

function markSelected() {
  const at = state.view === 'programs' ? programState.at : sheetState.at;
  for (const row of listPane.querySelectorAll('tr.row, li.row, div.row')) {
    row.classList.toggle('selected', row.dataset.target === at);
  }
}

/* The ranking's facts in words (``entries_affected -> conclusions_blocked -> ...`` is not a line
 * for a page). Each fact's longer meaning is its hover. */
const DIMENSION_WORDS = {
  entries_affected: 'files asking it',
  conclusions_blocked: 'results waiting on it',
  classes_touched: 'groups its files are in',
  senses_offered: 'meanings already known',
  default_standing: 'how sure the asking rule is',
};

function dimensionsNote(page) {
  if (!page.dimensions) return null;
  const said = (name) => DIMENSION_WORDS[name] || label(name);
  return el('details', {class: 'dims'}, [
    el('summary', {text: `ranked by ${page.order.map(said).join(', then ')}`}),
    el('p', {class: 'note', text: 'Ingence compares one fact at a time, in this order. It does not '
      + 'weight or add them.'}),
    el('ul', {}, page.dimensions.map((d) => el('li', {title: d.meaning},
      [`${said(d.name)}: ${d.direction === 'more' ? 'more first' : 'fewer first'}`]))),
  ]);
}

/* A row as the line under the header names it. */
function questionLabel(row) {
  // In words first: a class's key is a digest, and a raised value a code.
  return String(row.heading || row.pattern || row.raised || row.spelled || row.name
    || row.extension || row.target_name || row.target).slice(0, 80);
}

/* ------------------------------------------------------------- group resolutions */

/* Group resolutions: approving a core set of words resolves every experiment sharing it. Identical
 * sets are a group per experiment (each day its own) and one group across them. For supersets and
 * subsets the shared core is the group common to them all, and each deviation (a word added, a word
 * missing) is its own question. Per approved core, this says who approved it and when, the groups
 * it resolved in each experiment and across, and each deviation waiting, with a link to its
 * question (``/api/v1/groups/resolutions``). The engine does the resolving. A run made before the
 * engine resolved groups resolves nothing, and says so. */
function resolutionsSection() {
  const holder = el('div', {class: 'resolutions', 'data-help': 'groupings.resolutions'});
  const section = el('div', {class: 'group-sections'}, [latticeHook(), holder]);
  api('/api/v1/groups/resolutions').then((found) => {
    const cores = found.cores || [];
    if (!cores.length) return;
    const when = (stamp) => {
      const moment = new Date(stamp);
      return Number.isNaN(moment.getTime()) ? stamp
        : moment.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
    };
    fill(holder,
      el('h3', {text: `Groups you approved, and what each one grouped (${cores.length})`}),
      el('p', {class: 'faint', text: 'Each set of words groups every experiment that holds them. '
        + 'Each day is its own group, and one group joins them all.'}),
      found.resolving_rules_in_run ? null : el('p', {class: 'note', text: 'The current scan has '
        + 'grouped nothing yet. The next scan will, once it has the rules for approved words.'}),
      ...cores.map((core) => el('details', {class: 'told-group', open: ''}, [
        el('summary', {}, [el('strong', {text: core.core.join(' ')}),
          el('span', {class: 'faint', text: ` · approved by ${core.approved_by}, ${when(core.approved_at)}`})]),
        core.within.length ? el('p', {}, ['In each experiment: ',
          ...core.within.flatMap((group, index) => [index ? '; ' : '',
            `${group.experiment || group.key} (${group.members} files)`])]) : null,
        core.across.length ? el('p', {}, ['Across the experiments: ',
          ...core.across.flatMap((group, index) => [index ? '; ' : '',
            `${group.key} (${group.members} files)`])]) : null,
        core.deviations.length ? el('ul', {class: 'ids'}, core.deviations.map((deviation) => el('li', {}, [
          el('span', {class: 'tag plain', text: deviation.way === 'added' ? `+ ${deviation.word}` : `− ${deviation.word}`}),
          ` ${deviation.way === 'added' ? 'added' : 'missing'} in ${deviation.experiment || 'an experiment'}: `,
          deviation.answered ? el('span', {class: 'tag now', text: 'answered'})
            : el('button', {class: 'link', type: 'button', text: 'waiting for your answer',
              onclick: () => navigate('review', {focus: {kind: 'question', id: deviation.question,
                label: `${deviation.word}, ${deviation.way}`}})}),
        ]))) : null,
        !core.within.length && !core.across.length && !core.deviations.length
          ? el('p', {class: 'faint', text: 'It grouped nothing in this scan.'}) : null,
      ])));
  }).catch(() => null);
  return section;
}

/* The lattice of group approvals lives in groups.js, loaded once on first use and drawn into this
 * holder. This hook is all of it that sits in this file. */
let latticeScript = null;
function latticeHook() {
  const holder = el('div', {class: 'group-lattice', 'data-help': 'groupings.lattice'});
  latticeScript = latticeScript || new Promise((resolve, reject) => {
    const script = el('script', {src: '/groups.js'});
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  });
  latticeScript.then(() => window.groupLattice(holder)).catch(() => null);
  return holder;
}

/* Approve the words a proposed group of experiments shares as a group: each word a box, all ticked,
 * so a person keeps the core they mean; the answer is a confirm on group:<words>, written through
 * the one answer route, and the next run resolves every experiment sharing it. */
function approveCore(row, status, reload) {
  const words = (row.shared || []).map((word) => String(word).trim().toLowerCase()).filter(Boolean);
  if (!words.length) return null;
  const boxes = words.map((word) => {
    const box = el('input', {type: 'checkbox', value: word, id: `core-${word}`});
    box.checked = true;
    return el('label', {for: `core-${word}`, class: 'core-word'}, [box, ` ${word} `]);
  });
  return el('div', {class: 'approve-core', 'data-help': 'groupings.approve'}, [
    el('h3', {text: 'Or approve the words they share as a group'}),
    el('p', {class: 'faint', text: 'This groups every experiment that holds these words. Each day '
      + 'is its own group, and one group joins them all.'}),
    el('p', {}, boxes),
    el('div', {class: 'answers'}, [el('button', {class: 'answer', type: 'button', onclick: () => {
      const kept = [...new Set(boxes.map((item) => item.querySelector('input'))
        .filter((box) => box.checked).map((box) => box.value))].sort();
      if (kept.length < 2) {
        status.className = 'note refused';
        status.textContent = 'Keep at least 2 words. One word is not a group.';
        return;
      }
      (async () => {
        if (!(await askActor())) return;
        try {
          const written = await write('/api/review/answer', {actor: me.actor,
            target: `group:${kept.join(' ')}`, word: 'accept'});
          // Said on the row, which stays: its own question (one thing or not) is still open.
          sheetState.flash = `Saved as answer ${written.sequence} by ${written.actor}: `
            + `"${kept.join(' ')}" is one group. The question about these experiments is still open.`;
          await reload();
        } catch (error) {
          status.className = 'note refused';
          status.textContent = String(error.message || error);
        }
      })();
    }}, [el('strong', {text: 'approve these words as a group'})])]),
  ]);
}

/* A Questions section imports and exports its question file through the header's Import and Export
 * (transfer.js). */

/* ------------------------------------------------------------- one row's page */

/* The pane's answers held while a write is under way: a second click never lands on the same row
 * twice, nor on the next row before it is read. */
function holdAnswers(held) {
  for (const button of detailPane.querySelectorAll('button.answer')) button.disabled = held;
}

async function send(row, said, status, reload) {
  if (!(await askActor())) {
    status.className = 'note refused';
    status.textContent = 'Ingence needs your name to save an answer. Set your name first.';
    return;
  }
  status.className = 'note';
  status.textContent = 'Saving...';
  holdAnswers(true);
  try {
    const written = await write('/api/review/answer', {actor: me.actor, target: row.target, ...said});
    sheetState.flash = `Saved as answer ${written.sequence} by ${written.actor}.`;
    await reload();
  } catch (error) {
    holdAnswers(false);
    status.className = 'note refused';
    status.textContent = String(error.message || error);
  }
}

/* One answer written to several targets, one event each (a set two rules formed). */
async function sendToEach(targets, said, status, reload) {
  if (!(await askActor())) return;
  status.className = 'note';
  status.textContent = 'Saving...';
  holdAnswers(true);
  try {
    const result = await write('/api/review/answer', {actor: me.actor,
      decisions: targets.map((target) => ({target, ...said}))});
    sheetState.flash = `Saved ${plural(result.written.length, 'answer', 'answers')} by `
      + `${me.actor}, one for each rule.`
      + (result.refused.length ? ` ${result.refused.length} refused: `
        + result.refused.map((item) => item.error).join(' / ') : '');
    await reload();
  } catch (error) {
    holdAnswers(false);
    status.className = 'note refused';
    status.textContent = String(error.message || error);
  }
}

/* Who the decision will be recorded under: the name set on this machine, never typed per row. */
function actorField() {
  return el('p', {class: 'faint decided-by', text: me.actor
    ? `Saved under your name, ${me.actor}. You can change it at the top of the page.`
    : 'Ingence asks for your name at your first answer.'});
}

/* One button per word a row offers: its label, and beneath it a phrase only where the label alone
 * would mislead. The longer meaning is the hover. */
function wordButtons(words, onWord) {
  return el('div', {class: 'answers'}, words.map((word) => el('button', {
    class: 'answer', 'data-word': word.word, title: word.explained || '',
    onclick: () => onWord(word.word)},
  [el('strong', {text: shownWord(word)}),
    word.meaning ? el('span', {class: 'kind', text: word.meaning}) : null])));
}

function recordOf(row) {
  const items = [];
  if (row.in_force) {
    const now = row.in_force;
    items.push(el('li', {class: 'in-force'}, [
      el('span', {class: 'tag now person', text: shownAnswerLabel(now)}),
      ` by ${now.actor}, ${now.occurred_at}`,
      now.correct_value ? `; should be ${now.correct_value}` : '',
      now.imported_from ? `; ${importedWords(now)}` : '',
      now.note ? el('div', {class: 'payload', text: `note: ${now.note}`}) : null,
    ]));
  }
  for (const note of row.notes || []) {
    items.push(el('li', {}, [el('span', {class: 'tag plain', text: 'note'}),
      ` ${note.actor}, ${note.occurred_at}: ${note.note}`]));
  }
  return items.length
    ? el('div', {}, [el('h3', {text: 'In the answer log'}), el('ul', {class: 'record'}, items)])
    : el('p', {class: 'note', text: 'The answer log has nothing about this row yet.'});
}

/* A concluded value in words: an object's parts as "from 2024-03-05, source name", never its JSON;
 * anything else with its underscores read as spaces. */
function valueSaid(value) {
  const spelled = String(value ?? '');
  if (spelled.startsWith('{')) {
    try {
      const parts = JSON.parse(spelled);
      return Object.entries(parts).map(([part, held]) => `${label(part)} ${label(held)}`).join(', ');
    } catch (error) {
      /* Not an object after all: said as written. */
    }
  }
  return label(spelled);
}

/* A value as a heading shows it: its first letter a capital, a long value cut. */
function valueWords(value) {
  const said = valueSaid(value).slice(0, 80);
  return said.charAt(0).toUpperCase() + said.slice(1);
}

function plural(count, one, many) {
  return `${Number(count).toLocaleString()} ${count === 1 ? one : many}`;
}

/* One answer for every file a value was concluded about. The list is every file, by place
 * (``/api/claim-value``). The answer writes one ordinary claim event per file still waiting, each
 * saying it was one answer over the listed set (``answered_together``), so every reader of claim
 * verdicts reads it as it reads one. The answer is sent in parts, a write each. */
const FOR_ALL_PART = 200;

function forAllClaims(row, correction, status, reload) {
  const count = Number(row.of_value || 0);
  if (count < 2 || row.grouped_by) return null;
  /* Only the list the person saw is written: the first press opens the list, and the answer is
   * offered once it is on the page. What is written is that list's waiting files, held from when it
   * was shown, never read again at the press. A list cut short (more files than it names) is not
   * answered for all. */
  const list = el('div', {class: 'for-all-list'});
  const buttons = el('div', {class: 'answers'});
  let shown = null;
  const listed = el('details', {class: 'for-all-files'}, [
    el('summary', {text: `See all ${count.toLocaleString()}`}), list]);
  const sayForAll = async (word, targets) => {
    if (!(await askActor())) return;
    let written = 0;
    const refused = [];
    holdAnswers(true);
    try {
      for (let at = 0; at < targets.length; at += FOR_ALL_PART) {
        const result = await write('/api/review/answer', {actor: me.actor,
          decisions: targets.slice(at, at + FOR_ALL_PART).map((target) => ({target, word,
            together: targets.length, correct_value: word === 'wrong'
              ? (correction.value || undefined) : undefined}))});
        written += result.written.length;
        refused.push(...result.refused);
      }
    } catch (error) {
      // A part written before the failure stays written: say how much, and what stopped it.
      sheetState.flash = `Saved ${plural(written, 'answer', 'answers')} of `
        + `${targets.length.toLocaleString()}, then stopped: ${String(error.message || error)}`;
      await reload();
      return;
    }
    // A file answered since the list was shown keeps its answer, and is counted.
    const already = refused.filter((item) => item.already).length;
    const other = refused.filter((item) => !item.already);
    sheetState.flash = `Saved ${plural(written, 'answer', 'answers')} by ${me.actor}: `
      + `${word} for all ${plural(targets.length, 'file', 'files')} listed`
      + (already ? `; ${already.toLocaleString()} already answered, kept as before` : '')
      + (other.length ? `; ${other.length.toLocaleString()} refused: `
        + [...new Set(other.map((item) => item.error))].join(' / ') : '') + '.';
    await reload();
  };
  const offer = () => {
    const targets = shown.places.flatMap((place) => place.files)
      .filter((file) => !file.in_force).map((file) => file.target);
    if (shown.files > shown.listed) {
      fill(buttons, el('p', {class: 'note', 'data-for-all': 'cut', text: `${Number(shown.listed)
        .toLocaleString()} of ${Number(shown.files).toLocaleString()} listed. One answer covers `
        + 'only a whole list, so answer these files on their own rows.'}));
      return;
    }
    if (!targets.length) {
      fill(buttons, el('p', {class: 'note', text: 'Every file listed is answered.'}));
      return;
    }
    fill(buttons,
      el('button', {class: 'answer', type: 'button', 'data-word': 'correct-for-all',
        onclick: () => sayForAll('correct', targets)},
      [el('strong', {text: `Correct for all ${targets.length.toLocaleString()} listed`})]),
      el('button', {class: 'answer', type: 'button', 'data-word': 'wrong-for-all',
        onclick: () => sayForAll('wrong', targets)},
      [el('strong', {text: `Wrong for all ${targets.length.toLocaleString()} listed`})]));
  };
  const open = async () => {
    listed.open = true;
    if (shown) return;
    try {
      shown = await api(`/api/claim-value?${new URLSearchParams({rule: row.rule_id,
        value: String(row.value)})}`);
    } catch (error) {
      fill(list, el('p', {class: 'note refused', text: String(error.message || error)}));
      return;
    }
    fill(list, ...shown.places.map((place) => el('details', {class: 'for-all-place', open: ''}, [
      el('summary', {text: `${place.folder} (${place.files.length})`}),
      el('ul', {class: 'names'}, place.files.map((file) => el('li', {}, [el('code', {text: file.name}),
        file.in_force ? el('span', {class: 'tag now', text: file.in_force.label || file.in_force.word})
          : null])))])),
    shown.files > shown.listed ? el('p', {class: 'faint', text: `and ${(shown.files
      - shown.listed).toLocaleString()} more, not listed`}) : null);
    offer();
  };
  listed.addEventListener('toggle', () => { if (listed.open) open(); });
  fill(buttons, el('button', {class: 'answer', type: 'button', 'data-word': 'list-for-all',
    onclick: () => open()}, [el('strong', {text: `Answer all ${count.toLocaleString()} at once`})]));
  return el('div', {class: 'for-all', 'data-help': 'claims.for_all'}, [buttons, listed]);
}

async function showRow(row, reload) {
  // Every writer on a proposal's row writes on the proposal's own target (a pack, a person's
  // miscellaneous files), where the engine reads it; the question's target is never written.
  const writesTo = row.proposal ? {...row, target: row.proposal.target} : row;
  // What was just recorded is said at the top of the next row's pane (auto-advance).
  const said = sheetState.flash;
  sheetState.flash = '';
  const status = el('p', {class: 'note', role: 'status'});
  const note = noteInput();
  const correction = el('input', {type: 'text', placeholder: 'if wrong, what it should be',
    'aria-label': 'if wrong, what it should be'});
  const described = el('input', {type: 'text', placeholder: 'what it is, in your words'});
  const belongs = el('input', {type: 'text', placeholder: 'the format, program or instrument'});
  const noteOnly = el('button', {class: 'answer quiet', onclick: () => {
    if (!note.value.trim()) {
      status.className = 'note refused';
      status.textContent = 'Write the note first.';
      return;
    }
    send(writesTo, {note: note.value}, status, reload);
  }}, [el('strong', {text: 'save the note only'})]);
  const sayWord = (word) => send(row, {word, note: note.value || undefined,
    correct_value: correction.value || undefined, described: described.value || undefined,
    belongs_to: belongs.value || undefined}, status, reload);

  const head = [];
  let controls = [];
  if (sheetState.kind === 'questions' && row.proposal) {
    /* A proposal to accept or refuse: a vocabulary pack to load, or a folder of a person's
     * miscellaneous files. The answer is written on the proposal's own target. */
    const proposal = row.proposal;
    const answered = {...row, target: proposal.target};
    head.push(el('h2', {class: 'question', text: proposal.question || (proposal.kind === 'pack'
      ? `Add the ${label(proposal.names)} words to the Dictionary?`
      : 'Are these your miscellaneous files?')}),
    el('p', {class: 'why', text: row.reason || ''}),
    el('p', {class: 'note', text: proposal.note || (proposal.kind === 'pack'
      ? 'These words and file types are new to this project. Once added, Ingence reads them in '
        + 'every later scan.'
      : `${proposal.names}: files outside every experiment. If you keep them as your miscellaneous `
        + 'files, Ingence never puts them into an experiment.')}),
    proposal.kind === 'pack' ? null : window.inspector.filesOf(row.subject_files || [],
      () => { showRow(row, reload); }));
    controls = [wordButtons(proposal.words, (word) => send(answered, {word,
      note: note.value || undefined}, status, reload))];
  } else if (sheetState.kind === 'questions' && row.word_question) {
    await placedWordQuestion(row, note, status, reload, head, (built) => { controls = built; });
  } else if (sheetState.kind === 'questions' && row.about_a_word) {
    await wordQuestion(row, note, status, reload, head, (built) => { controls = built; });
  } else if (sheetState.kind === 'questions') {
    /* What it asks, in words: a heading and a lead from the route's one table, the rule as a
     * handle, its plain words and an example one click away. */
    const counted = row.counted || 'files';
    head.push(el('h2', {class: 'question', text: row.heading || valueWords(row.raised)}),
      row.lead ? el('p', {class: 'asks', text: row.lead}) : null,
      el('p', {class: 'faint'}, [`${plural(Number(row.entries_affected), counted.replace(/s$/, ''),
        counted)} · `, ruleHandle(row.rule_id, row.rule_version)]),
      whatIsThis(row),
      window.inspector.filesOf(row.subject_files || [], () => { showRow(row, reload); }));
    controls = [wordButtons(row.words, sayWord)];
  } else if (sheetState.kind === 'classes' && row.level === 'experiments') {
    // A proposed group of experiments: the highest level, asked first.
    head.push(el('h2', {class: 'question', text: 'Do these experiments belong together?'}),
      el('p', {class: 'asks', text: `${plural(row.members, 'experiment', 'experiments')}`
        + `${(row.shared || []).length ? `, sharing ${row.shared.join(', ')}` : ''}.`}),
      el('p', {class: 'faint'}, [ruleHandle(row.rule_id, row.rule_version)]),
      el('ul', {class: 'ids'}, (row.experiments || []).map((member) => el('li', {}, [
        el('strong', {text: member.name}), ` (${member.kind === 'named' ? 'by its dated name'
          : `${label(member.kind)}, by what it holds`})`,
        member.path !== member.name ? el('span', {class: 'faint', text: ` ${member.path}`}) : null,
      ]))));
    controls = [wordButtons(row.words, sayWord), approveCore(row, status, reload)];
  } else if (sheetState.kind === 'classes') {
    /* A group or not: the answer sounds like group/no group, because the question is whether these
     * files are a group. */
    const copies = (row.words || []).some((word) => word.word === 'copies');
    const where = row.experiment ? `, all in ${row.experiment}`
      : row.experiments_spanned > 1 ? `, in ${row.experiments_spanned} experiments`
        : row.folder ? `, in ${row.folder}` : '';
    /* Led by why it asks: "These 12 files hold identical bytes, in 12 experiments", the name
     * pattern after it as the list's summary. A set two rules formed is asked once, by the
     * strongest reason, with both rules as its handles, and the answer is written to each rule's
     * own class. A set a person refused is doubted by their answer, which the pane shows, not by a
     * rule. */
    const rules = [row, ...(row.also || [])];
    const refused = (row.in_force && row.in_force.kind === 'reject') || row.answered_before;
    const before = row.answered_before;
    head.push(el('h2', {class: 'question', text: copies ? 'Copies of one file?' : 'One group?'}),
      el('p', {class: 'asks', text: (row.why || `${plural(row.members, 'file', 'files')}`
        + `${row.alike_by ? ` ${row.alike_by}` : ''}`) + `${where}.`
        + (row.conflicting && !refused ? ' A rule doubts this group.' : '')}),
      // Asked again because a file joined the set since it was answered.
      before ? el('p', {class: 'note', text: `Answered “${before.label}” when it held `
        + `${plural(before.files, 'file', 'files')}.`}) : null,
      // The first line says what the files share; the pattern's own phrase would say it again.
      row.pattern ? el('p', {}, [el('code', {text: row.pattern}),
        row.varies && !row.why ? el('span', {class: 'faint', text: ` ${row.varies}`}) : null]) : null,
      el('p', {class: 'faint'}, rules.flatMap((rule, index) => [index ? ' · ' : '',
        ruleHandle(rule.rule_id, rule.rule_version)])),
      whatIsThis(row),
      window.inspector.filesOf(row.listed || [], () => { showRow(row, reload); }),
      row.members > (row.listed || []).length
        ? el('p', {class: 'note', text: `and ${row.members - row.listed.length} more.`}) : null);
    const sayForEachRule = (word) => sendToEach(rules.map((rule) => rule.target),
      {word, note: note.value || undefined}, status, reload);
    controls = [wordButtons(row.words, rules.length > 1 ? sayForEachRule : sayWord)];
  } else if (sheetState.kind === 'claims' && row.grouped_by) {
    // An example of a per-file group: information, answerable only if the person wants to.
    const shared = Object.entries(row.grouped_by).map(([part, value]) => `${label(part)} ${value}`);
    head.push(el('h2', {class: 'question'}, [`${valueWords(row.value)}: `, el('code', {text: row.name})]),
      el('p', {class: 'asks', text: `One of ${plural(Number(row.of_value), 'file', 'files')} sharing `
        + `${shared.join(', ') || 'a value of its own'}. Nothing waits for an answer here.`}),
      el('p', {class: 'faint'}, [row.path, ' · ', ruleHandle(row.rule_id, row.rule_version)]));
    controls = [wordButtons(row.words, sayWord),
      el('div', {class: 'field'}, [correction])];
  } else if (sheetState.kind === 'claims') {
    /* The row in few words: "Acquisition: kelt_01.nd2", and
     * "and 239 more like it, in 12 places". */
    const more = Number(row.of_value) - 1;
    const places = Number(row.places_of_value || 0);
    head.push(el('h2', {class: 'question'}, [`${valueWords(row.value)}: `, el('code', {text: row.name})]),
      more > 0 ? el('p', {class: 'asks', text: `and ${more.toLocaleString()} more like it`
        + (places > 1 ? `, in ${places.toLocaleString()} places.` : ', in one place.')}) : null,
      el('p', {class: 'faint'}, [row.path, ' · ', ruleHandle(row.rule_id, row.rule_version)]));
    controls = [wordButtons(row.words, sayWord),
      el('div', {class: 'field'}, [correction]),
      forAllClaims(row, correction, status, reload)];
  } else if (sheetState.kind === 'candidates') {
    /* In plain words: the question, where, the stages, and the rule as a handle, its tier in
     * words. */
    /* A pair proposed as related has no direction: it is asked whether the two belong together, and
     * no earlier or later is said of it. */
    const related = row.primitive === 'concerns';
    /* Two files of one name in sibling folders are told apart by their paths. */
    const same = row.source_name === row.target_name;
    const sourceSaid = same ? (row.source_path || row.source_name) : row.source_name;
    const targetSaid = same ? (row.target_path || row.target_name) : row.target_name;
    head.push(el('h2', {class: 'question'}, related
      ? ['Do ', el('code', {text: sourceSaid}), ' and ', el('code', {text: targetSaid}),
        ' belong together?']
      : ['Was ', el('code', {text: targetSaid}), ' made from ',
        el('code', {text: sourceSaid}), '?']),
    el('p', {class: 'asks', text: related ? `In ${row.folder_path || '.'}.`
      : `In ${row.folder_path || '.'}. Earlier: `
      + `${row.source_stage || 'unknown'}; later: ${row.target_stage || 'unknown'}.`}),
    el('p', {class: 'faint'}, [row.tier_said ? `${row.tier_said} · ` : '',
      ruleHandle(row.rule_id, row.rule_version)]));
    controls = [wordButtons(row.words, sayWord)];
  } else if (sheetState.kind === 'extensions') {
    head.push(el('h2', {class: 'question'}, ['What are ', el('code',
      {text: row.extension ? `.${row.extension}` : 'files with no extension'}), ' files?']),
    el('p', {class: 'asks', text: `${plural(Number(row.files), 'file', 'files')}, `
      + `contents read for ${Number(row.opened).toLocaleString()}, in ${row.folders.join(', ')}.`}));
    controls = [
      el('div', {class: 'field'}, [el('label', {text: 'what it is'}), described]),
      el('div', {class: 'field'}, [el('label', {text: 'belongs to'}), belongs]),
      el('p', {class: 'note', text: 'Should Ingence read the contents of such files?'}),
      wordButtons(row.words, sayWord),
      el('div', {class: 'answers secondary'}, [el('button', {class: 'answer quiet',
        onclick: () => send(row, {described: described.value || undefined,
          belongs_to: belongs.value || undefined, note: note.value || undefined}, status, reload)},
      [el('strong', {text: 'save what it is only'})])]),
    ];
  }
  fill(detailPane,
    said ? el('p', {class: 'note kept', 'data-advance': 'recorded', text: said}) : null,
    ...head,
    el('h3', {text: 'Your answer'}),
    ...controls,
    // A note's terms are kept about the question itself, also on a proposal's row; a term kept
    // before shows as kept.
    noteField(note, row.target, CHIP_NOUNS[sheetState.kind],
      (row.notes || []).flatMap((said) => said.observed || []).map((term) => term.term)),
    el('div', {class: 'answers secondary'}, [noteOnly]),
    actorField(),
    status,
    recordOf(row),
  );
}

/* ------------------------------------------------------ a word, made plain */

/* A name with the word marked and everything else quiet. */
function markedName(name) {
  const parts = [];
  let at = 0;
  for (const [start, end] of name.spans) {
    if (start > at) parts.push(el('span', {class: 'quiet', text: name.name.slice(at, start)}));
    parts.push(el('mark', {text: name.name.slice(start, end)}));
    at = end;
  }
  if (at < name.name.length) parts.push(el('span', {class: 'quiet', text: name.name.slice(at)}));
  return el('li', {class: 'marked'}, [el('span', {class: 'name'}, parts),
    el('span', {class: 'where', text: ` in ${name.folder}`})]);
}

function wordEvidence(shown) {
  return [
    el('p', {class: 'summary-strong', text: `It is in ${shown.files.toLocaleString()} `
      + `${shown.are} across ${shown.folder_count.toLocaleString()} `
      + `folder${shown.folder_count === 1 ? '' : 's'}.`}),
    el('h3', {text: `Where it is used (${shown.names.length} of ${shown.files}, `
      + 'spread over its folders)'}),
    el('ul', {class: 'names'}, shown.names.map(markedName)),
    el('details', {}, [el('summary', {text: 'which folders, and how many in each'}),
      el('ul', {class: 'ids'}, [...shown.folders.map((item) => el('li', {},
        [`${item.folder}: ${item.files}`])),
      shown.folders_not_shown ? el('li', {text: `and ${shown.folders_not_shown} more folders`})
        : null])]),
  ];
}

/* What the engine itself would read the word as, and why, in plain words. It is the engine's
 * reading, never an answer: nothing is stored until a person answers. */
function engineReading(row) {
  const recommended = row.recommended;
  if (!recommended) return null;
  const known = (row.senses || []).length;
  const text = recommended.value
    ? `Ingence’s guess: ${label(recommended.value)}. Of the meanings it knows, no other ranks above `
      + 'this one.'
    : known
      ? `Ingence has no guess. The ${known} meanings it knows rank equal here.`
      : 'Ingence has no guess for this word.';
  return el('div', {class: 'engine-reading'}, [
    el('p', {class: 'guess-by', text: 'Ingence’s guess (not an answer)'}),
    el('p', {text}),
  ]);
}

/* The define row: a meaning, its kind, and, where the row offers one, where the meaning applies. A
 * meaning applies everywhere, since a word's meaning is usually the laboratory's, or only in the
 * folder holding every place the word stands. The server says whether that folder exists (the row's
 * ``choices``, computed by the function the answer's route reads), so the choice is offered only
 * where it can be written, and named by its folder: "only in plates/day1 and below". */
function defineRow(themeKinds, onDefine, choices) {
  const meaning = el('input', {class: 'define-term', type: 'text',
    placeholder: 'what it means here, in your words'});
  const axis = el('select', {class: 'define-kind', 'aria-label': 'What kind of thing it is'}, [
    el('option', {value: '', text: 'what kind of thing it is...'}),
    ...(themeKinds || []).map((kind) => el('option', {value: kind, text: label(kind)}))]);
  const offered = (choices || []).filter((choice) => choice.value && choice.label);
  const scoped = offered.some((choice) => choice.value === 'where_it_stands');
  const where = scoped ? el('select', {class: 'define-where', 'data-help': 'review.where', 'aria-label': 'Where the meaning applies'},
    offered.map((choice) => el('option', {value: choice.value, text: choice.value === 'everywhere'
      ? 'applies everywhere' : `applies ${choice.label}`}))) : null;
  return el('div', {class: 'define'}, [
    el('div', {class: 'define-row'}, [meaning, axis, where, el('button', {class: 'answer',
      onclick: () => onDefine(meaning.value, axis.value, where ? where.value : 'everywhere')},
    [el('strong', {text: 'say what it means'})])]),
    el('p', {class: 'faint', text: 'Saved exactly as you write it. It takes effect at the next '
      + 'Re-run.'}),
  ]);
}

/* Every place a word nothing binds stands, as the word-question page lists them: the folder, the
 * part of its names (shown by an example name), the known words beside it, and how many names. */
function wordPlaces(row) {
  const places = row.appearances || [];
  const folders = row.where_folders || [];
  return [
    el('h3', {text: `Where it appears: ${row.places} place${row.places === 1 ? '' : 's'} in `
      + `${folders.length} folder${folders.length === 1 ? '' : 's'}`}),
    el('ul', {class: 'ids'}, [
      ...places.map((place) => el('li', {}, [
        el('strong', {text: place.folder}), `: ${place.where}. Next to `
          + `${place.alongside.join(', ')}${place.alongside_not_shown
            ? ` and ${place.alongside_not_shown} more` : ''}; `
          + `${place.files} ${place.files === 1 ? 'name contains' : 'names contain'} it.`])),
      row.places_not_shown ? el('li', {class: 'faint', text: `and ${row.places_not_shown} more `
        + `place${row.places_not_shown === 1 ? '' : 's'}, in the folders below`}) : null,
    ]),
    row.places_not_shown ? el('details', {}, [el('summary', {text: 'every folder it appears in'}),
      el('ul', {class: 'ids'}, folders.map((folder) => el('li', {text: folder})))]) : null,
  ];
}

/* "Not a word" asks only whether it is a person's word. There are many "word" shaped things, like
 * extensions, signifiers, or even just letter combinations, that mean something to a computer but
 * not necessarily to a human. Where it is not a person's word, the person says what it is to a
 * computer. A reading the string's shape fits comes first, with the engine's reason beside it:
 * evidence, never an answer, and nothing is stored until a person chooses. */
function computerChoices(shown, onReading) {
  const readings = shown.computer || [];
  return [
    el('p', {class: 'note', text: 'Or is it something only a computer or a program reads? '
      + 'Say which:'}),
    el('div', {class: 'answers'}, readings.map((reading) => el('button', {
      class: reading.shape ? 'answer' : 'answer quiet',
      onclick: () => onReading(reading.word)}, [
      el('strong', {text: label(reading.word)}),
      el('span', {class: 'kind', text: reading.meaning}),
      reading.shape ? el('span', {class: 'kind engine-hint', text: `Ingence: its shape fits. `
        + `${reading.shape.charAt(0).toUpperCase()}${reading.shape.slice(1)}.`}) : null,
    ]))),
  ];
}

async function wordQuestion(row, note, status, reload, head, setControls) {
  const shown = await api(`/api/word?rule=${encodeURIComponent(row.rule_id)}`
    + `&raised=${encodeURIComponent(row.raised)}`);
  head.push(el('h2', {class: 'question'}, ['What does ', el('mark', {text: row.raised}), ' mean here?']),
    ...wordEvidence(shown), engineReading(row));
  const data = sheetState.data || {};
  const senses = (row.senses || []).map((sense) => ({word: sense.term,
    meaning: `already known: a ${label(sense.theme_kind || 'sense')}`}));
  setControls([
    el('p', {class: 'note', text: 'Is it a word a person would use here? If so, choose one of its '
      + 'known meanings:'}),
    wordButtons(senses, (word) => send(row, {word, note: note.value || undefined}, status, reload)),
    el('p', {class: 'note', text: 'Or say what it means yourself:'}),
    defineRow(data.theme_kinds, (meaning, axis, where) => {
      if (!meaning.trim() || !axis) {
        status.className = 'note refused';
        status.textContent = 'Write what it means and choose what kind of thing it is.';
        return;
      }
      send(row, {answer: meaning, theme_kind: axis, where, note: note.value || undefined}, status, reload);
    }, row.choices),
    ...computerChoices(shown, (reading) => send(row, {word: 'not_a_word', reading,
      note: note.value || undefined}, status, reload)),
    wordButtons(shown.cannot_say || [], (word) => send(row, {word, note: note.value || undefined},
      status, reload)),
  ]);
}

/* A word nothing binds, asked once for every place it stands: its sentence, the engine's reading
 * shown as the engine's, every place, then the answers: a meaning with its kind and where it
 * applies, not a word (and what it is to a computer), or cannot say. The row comes from the
 * word-question page, which the server keeps by run; what the string's shape says it may be to a
 * computer comes from the names carrying it. */
async function placedWordQuestion(row, note, status, reload, head, setControls) {
  const shown = await api(`/api/word?word=${encodeURIComponent(row.raised)}`);
  const reading = row.reading || {};
  head.push(el('h2', {class: 'question'}, ['What is ', el('mark', {text: row.raised}), '?']),
    el('p', {class: 'why', text: row.evidence || row.sentence || ''}),
    el('div', {class: 'engine-reading'}, [
      el('p', {class: 'guess-by', text: 'Ingence’s guess (not an answer)'}),
      el('p', {text: reading.reading || ''})]),
    /* The known words it may be a slip of: offered, never bound. */
    ...(row.did_you_mean ? [el('p', {class: 'note did-you-mean', text: row.did_you_mean})] : []),
    ...wordPlaces(row));
  const data = sheetState.data || {};
  const cannot = (row.words || []).filter((word) => word.word === 'cannot_say');
  setControls([
    el('p', {class: 'note', text: 'Is it a word a person would use here? If so, say what it '
      + 'means, what kind of thing it is, and where it applies:'}),
    defineRow(data.theme_kinds, (meaning, axis, where) => {
      if (!meaning.trim() || !axis) {
        status.className = 'note refused';
        status.textContent = 'Write what it means and choose what kind of thing it is.';
        return;
      }
      send(row, {answer: meaning, theme_kind: axis, where, note: note.value || undefined}, status, reload);
    }, row.choices),
    row.where && !row.where.folder ? el('p', {class: 'faint', text: row.where.because}) : null,
    ...computerChoices(shown, (said) => send(row, {word: 'not_a_word', reading: said,
      note: note.value || undefined}, status, reload)),
    wordButtons(cannot, (word) => send(row, {word, note: note.value || undefined}, status, reload)),
  ]);
}

/* ---------------------------------------------------------------- programs */

const programState = {showAnswered: true, at: null, shownOrder: []};

async function showPrograms() {
  const reload = () => showPrograms().catch(reportError);
  states.loading(listPane, 'Loading the programs...');
  const data = await api(`/api/programs${programState.showAnswered ? '?answered=waiting' : ''}`);
  if (!stillShowing('programs')) return;
  const openProgram = (program) => {
    programState.at = program.target || program.path;
    markSelected();
    showProgramRow(program, reload);
  };
  /* Bulk answering: a box on each program with a question, one in the header above the list, and
   * the bar when any is chosen. */
  const said = el('p', {class: 'bulk-said', role: 'status', 'aria-live': 'polite'});
  const tags = new Map();
  const items = data.programs.map((program) => ({key: program.target || program.path,
    name: program.title, row: program, targets: program.target ? [program.target] : [],
    words: (program.words || []).map(buttonWord)}));
  const made = chooser({items, noun: 'program', said, onAnswered: (item, choice, written) => {
    shownAnswer(item.row, choice, written);
    const tag = tags.get(item.key);
    if (tag) tag.replaceChildren(decidedTag(item.row));
  }});
  const rows = items.map((item) => {
    const program = item.row;
    const tag = el('span', {class: 'program-answer'}, [program.not_a_program
      ? el('span', {class: 'tag now person', text: 'said: not a program'})
      : program.target ? decidedTag(program) : null]);
    tags.set(item.key, tag);
    return chosenRow(el('div', {class: program.target === programState.at
      ? 'program row selected' : 'program row', 'data-target': program.target || program.path,
    'data-key': item.key}, [
      made.boxFor(item),
      el('h3', {text: program.title}),
      el('p', {class: 'faint', text: `${program.path} — ${program.reason || ''}`}),
      tag,
      el('details', {}, [
        el('summary', {text: `its parts: ${program.parts.length} `
          + `folder${program.parts.length === 1 ? '' : 's'}`
          + (program.programs_inside.length ? `, ${program.programs_inside.length} `
            + `program${program.programs_inside.length === 1 ? '' : 's'} inside it` : '')}),
        el('ul', {class: 'ids'}, [
          ...program.programs_inside.map((inside) => el('li', {},
            [`${inside.path}: ${inside.title}`])),
          ...program.parts.map((part) => el('li', {}, [`${part.name}/: ${part.files} files`])),
        ]),
      ]),
      program.set_aside.length ? el('details', {}, [
        el('summary', {text: `${program.set_aside.length} question`
          + `${program.set_aside.length === 1 ? '' : 's'} from inside it, which Ingence does not ask`}),
        el('ul', {class: 'ids'}, program.set_aside.map((item) => el('li', {}, [
          el('code', {text: item.raised}), ` by ${item.rule_id}, ${item.entries_affected} `
          + `file${item.entries_affected === 1 ? '' : 's'}`]))),
      ]) : null,
      el('button', {class: 'answer quiet', onclick: () => openProgram(program)},
        [el('strong', {text: program.target ? 'answer' : 'not a program?'})]),
    ]), made, item.key, () => openProgram(program));
  });
  setWaiting('programs', data.progress ? data.progress.remaining : 0);
  if (!data.programs.length && !data.hidden_answered) {
    fill(listPane, el('h2', {text: 'Programs found in the data'}),
      el('p', {class: 'nothing', text: 'Nothing to review. Ingence found no installed program in '
        + 'this scan.'}));
    fill(detailPane, el('p', {class: 'empty', text: 'Nothing to review here.'}));
    return;
  }
  fill(listPane,
    el('h2', {text: 'Programs found in the data'}),
    ...explained('Each row is one installed program found in your data. Check that each one is '
      + 'what it says.',
      'A row is a program’s folder and everything in it. Ingence still lists and reads its files. '
      + 'Questions from inside a program are kept apart from the questions about your data.'),
    data.progress && data.progress.rows ? progressLine(data.progress, null, null) : null,
    ...(rows.length ? [
      el('label', {class: 'bulk-all'}, [made.header, ' Select all programs shown']),
      made.bar, said, ...rows,
    ] : [el('p', {class: 'nothing', text: 'Nothing left to review. You answered '
      + 'every program found.'})]),
  );
  /* Auto-advance: the next program waiting for an answer fills the pane. */
  const flash = sheetState.flash;
  const shownBefore = programState.shownOrder;
  programState.shownOrder = data.programs.map((program) => program.target || program.path);
  const waiting = data.programs.filter((program) => program.target);
  const next = advance.next(waiting, programState.at, shownBefore);
  if (next) {
    programState.at = next.target;
    markSelected();
    showProgramRow(next, reload);
    advance.focus(listPane);
  } else {
    advance.done(detailPane, 'Nothing left to review. You answered every program found.', flash);
    sheetState.flash = '';
  }
}

function showProgramRow(program, reload) {
  const said = sheetState.flash;
  sheetState.flash = '';
  const status = el('p', {class: 'note', text: ''});
  const note = noteInput();
  const row = {target: program.target, in_force: program.in_force, notes: program.notes};
  /* A row whose layout names no single program asks no question of its own; a person can still say
   * its folder is not a program. */
  const asks = Boolean(program.target);
  fill(detailPane,
    said ? el('p', {class: 'note kept', 'data-advance': 'recorded', text: said}) : null,
    el('h2', {class: 'question', text: `Is this ${program.title.split(',')[0]}?`}),
    el('p', {class: 'note', text: `${program.path}. ${program.reason || ''}` + (asks
      ? ' An answer covers every copy of this program in this scan.' : '')}),
    asks ? wordButtons(program.words, (word) => send(row, {word, note: note.value || undefined},
      status, reload)) : null,
    noteField(note, null),
    asks ? el('div', {class: 'answers secondary'}, [el('button', {class: 'answer quiet',
      onclick: () => send(row, {note: note.value}, status, reload)},
    [el('strong', {text: 'save the note only'})])]) : null,
    el('h3', {text: program.not_a_program ? 'You said: not a program' : 'Not a program'}),
    el('p', {class: 'note', text: program.not_a_program
      ? 'From the next scan, Ingence reads it as data and no longer reads its contents last.'
      : 'If it is not a program or a package, the next scan reads it as data. Ingence then no '
        + 'longer reads its contents last.'}),
    el('div', {class: 'answers secondary'}, [el('button', {class: 'answer quiet',
      'data-help': 'programs.not_a_program',
      onclick: async () => {
        const who = await whoWrites();
        if (!who || !program.folder) return;
        const isProgram = Boolean(program.not_a_program);
        try {
          await write('/api/review/answer', {actor: who, folder: program.folder,
            is_program: isProgram, note: note.value || undefined});
          sheetState.flash = isProgram
            ? `Saved: ${program.path} is a program after all. It takes effect in the next scan.`
            : `Saved: ${program.path} is not a program. It takes effect in the next scan.`;
          await reload();
        } catch (error) {
          status.className = 'note refused';
          status.textContent = String(error.message || error);
        }
      }}, [el('strong', {text: program.not_a_program ? 'it is a program after all'
      : 'this folder is not a program'})])]),
    actorField(),
    status,
    recordOf(row),
  );
}

/* ----------------------------------------------------------------- changes */

/* The Changes page as one timeline of runs. Every run is shown by its name, oldest first, and under
 * each run are the answers that first took effect in it: written after the run before it began, and
 * no later than it began. A run opens what changed from the latest earlier run of its own kind, and
 * any two runs can still be compared below. This is a function of its own, so another page can hold
 * it as a section. Nothing here writes. */
function countedBy(held) {
  return Object.entries(held || {}).sort((a, b) => b[1] - a[1])
    .map(([who, n]) => `${who} (${Number(n).toLocaleString()})`).join(', ');
}

function answersLanded(landed, heading) {
  const total = (landed.decided || 0) + (landed.noted || 0);
  if (!total) return null;
  const parts = [];
  if (landed.decided) parts.push(`${landed.decided.toLocaleString()} decision${landed.decided === 1 ? '' : 's'}`);
  if (landed.noted) parts.push(`${landed.noted.toLocaleString()} note${landed.noted === 1 ? '' : 's'}`);
  const listed = landed.answers || [];
  return el('details', {class: 'landed'}, [
    el('summary', {}, [el('strong', {text: `${total.toLocaleString()} answer${total === 1 ? '' : 's'} `
      + heading}), `: ${parts.join(' and ')}, by ${countedBy(landed.by_actor)}`]),
    el('p', {class: 'faint', text: `On the pages: ${countedBy(landed.by_page)}.`}),
    el('ul', {class: 'ids'}, listed.map((said) => el('li', {}, [
      el('span', {class: 'tag plain', text: said.kind === 'note' ? 'note'
        : (said.label || label(said.word)) + (said.reading ? `: ${label(said.reading)}` : '')}),
      ` ${said.actor}, ${said.occurred_at}: `, el('code', {text: said.target}),
      said.imported_from ? el('span', {class: 'faint', text: ` (${importedWords(said)})`}) : null,
      said.note ? el('span', {class: 'faint', text: ` "${said.note}"`}) : null,
    ]))),
    total > listed.length ? el('p', {class: 'faint', text: `and ${(total - listed.length)
      .toLocaleString()} more. The answer log keeps every one.`}) : null,
  ]);
}

async function compareRuns(before, after) {
  const turn = turns.claim(detailPane);
  states.loading(detailPane, 'Comparing the two scans...');
  try {
    const found = await api(`/api/changes?before=${encodeURIComponent(before)}`
      + `&after=${encodeURIComponent(after)}`);
    // Something asked for since (a run's restore preview) keeps the pane.
    if (turns.owns(detailPane, turn)) renderChanges(found);
  } catch (error) {
    if (turns.owns(detailPane, turn)) states.failed(detailPane, error, () => compareRuns(before, after));
  }
}

/* A run by its name alone, its identifier in the tooltip: no identifier is shown where a name says
 * it. The Record and the logs still use the identifier, which a hover gives. */
function namedRun(run) {
  return el('span', {class: 'run-name', title: run.run_id, text: run.label || run.run_id});
}

async function showChanges() {
  states.loading(listPane, 'Loading the scans...');
  const listed = await api('/api/timeline');
  if (!stillShowing('changes')) return;
  const named = Object.fromEntries(listed.runs.map((run) => [run.run_id, run]));
  const short = (run) => String(run.label || run.run_id).split(',')[0];
  const choose = (value, name) => {
    const select = el('select', {'aria-label': name}, listed.runs.map((run) => el('option', {value: run.run_id,
      title: run.run_id,
      text: `${run.label}${run.current ? ' (current)' : ''}: ${run.described}`})));
    if (value) select.value = value;
    return select;
  };
  const before = choose(listed.pair.before, 'The earlier scan');
  const after = choose(listed.pair.after, 'The later scan');
  const nodes = listed.runs.map((run) => {
    const earlier = run.compare_with ? named[run.compare_with] : null;
    return el('li', {class: run.current ? 'run current' : 'run'}, [
      el('p', {class: 'run-head'}, [namedRun(run),
        run.current ? el('span', {class: 'tag now', text: 'current'}) : null]),
      el('p', {class: 'faint', text: `${run.described} · `
        + `${Number(run.counts.claims).toLocaleString()} results, `
        + `${Number(run.counts.entries).toLocaleString()} items`}),
      // A run no answer took effect in says nothing about it: the page is quiet where nothing is.
      answersLanded(run.took_effect, 'took effect here'),
      run.made_here ? el('p', {class: 'faint', text: `${run.made_here.toLocaleString()} `
        + `answer${run.made_here === 1 ? '' : 's'} given while it was current.`}) : null,
      el('p', {class: 'run-actions'}, [
        earlier ? el('button', {class: 'link', text: `what changed from ${short(earlier)}`,
          onclick: () => compareRuns(earlier.run_id, run.run_id)}) : null,
        earlier && !run.current ? ' \u00b7 ' : null,
        // Going back to a run: its preview first, and only that preview is written.
        run.current ? null : el('button', {class: 'link', type: 'button', 'data-help': 'history.restore',
          text: 'restore this scan...',
          onclick: () => showRunInspector(run.run_id, detailPane, short(run))
            .catch((error) => states.failed(detailPane, error))}),
      ]),
    ]);
  });
  // Every answer made since, notes too; what Re-run applies is the badge's.
  const waiting = answersLanded(listed.waiting, 'given since the last scan');
  /* The reorganization notice: shown while the folders are laid out differently from the census
   * before, never a queue. The first comparison of a large census takes seconds, so the runs are
   * shown first and the notice is added to its place when it arrives. */
  const notice = el('div', {});
  api('/api/reorganisation').then((moved) => {
    if (moved && moved.noticed) {
      /* The files a dive found were not the files a walk took them for (the move rule), each with
       * the path it was taken for, under the sentence that counts them. */
      const undone = moved.moves_undone || [];
      fill(notice, el('p', {class: 'waiting', 'data-help': 'reorganised.notice', text: moved.notice}),
        undone.length ? el('details', {class: 'moves-undone'}, [
          el('summary', {text: `${undone.length.toLocaleString()} move${undone.length === 1 ? '' : 's'} undone by reading contents`}),
          el('ul', {class: 'ids'}, undone.slice(0, 50).map((row) => el('li', {
            text: `${row.path}: not ${row.taken_for}, whose bytes differ`}))),
          undone.length > 50 ? el('p', {class: 'faint', text: `${undone.length - 50} more not listed.`}) : null,
        ]) : null);
    }
  }).catch(() => null);
  fill(listPane,
    el('h2', {text: 'Scans and their answers'}),
    notice,
    ...explained('Oldest first.',
    'An answer takes effect in the first scan that starts after it. Listing the files uses the '
      + 'meanings you gave. Reading contents follows what you marked. Applying the rules uses every '
      + 'answer.'),
    el('ol', {class: 'timeline'}, nodes),
    waiting ? el('div', {class: 'waiting'}, [waiting]) : null,
    listed.unplaced ? el('p', {class: 'faint', text: `${listed.unplaced} answer`
      + `${listed.unplaced === 1 ? ' has a time' : 's have times'} that cannot be read, `
      + 'so no scan counts them.'}) : null,
    el('details', {class: 'what'}, [el('summary', {text: 'compare any two scans'}),
      el('div', {class: 'field'}, [el('label', {text: 'before'}), before]),
      el('div', {class: 'field'}, [el('label', {text: 'after'}), after]),
      el('div', {class: 'answers'}, [el('button', {class: 'answer',
        onclick: () => compareRuns(before.value, after.value)}, [el('strong', {text: 'compare'})])]),
    ]),
    jobsSection(),
  );
  // The run under investigation opens its restore preview; otherwise the latest comparison.
  if (focusHeld.kind === 'run' && named[focusHeld.id] && !named[focusHeld.id].current) {
    /* Opening on a run named in the address has asked for its preview already (navigate): asking
     * again would replace a preview drawn in the meantime with the line saying it is being read. */
    if (!inspecting(detailPane, focusHeld.id)) await showRunInspector(focusHeld.id, detailPane);
  } else if (listed.pair.before && listed.pair.after) {
    await compareRuns(listed.pair.before, listed.pair.after);
  }
}

function byRule(section, gained, lost) {
  if (!section.by_rule.length) return el('p', {class: 'empty', text: 'no change'});
  return el('div', {}, section.by_rule.map((held) => el('details', {}, [
    el('summary', {}, [el('code', {text: held.rule_id}),
      ` ${held[gained]} ${label(gained)}, ${held[lost]} ${label(lost)}`]),
    el('ul', {class: 'ids'}, held.examples.map((item) => el('li', {}, [
      el('span', {class: 'tag plain', text: item.way}), ` ${item.name}: `,
      el('code', {text: String(item.said).slice(0, 90)})]))),
  ])));
}

/* The counts of each kind of record in the two runs, a pair of bars each. */
function beforeAfter(found) {
  const kinds = [['entries', 'items in the file list'], ['claims', 'results'],
    ['findings', 'flags'], ['candidates', 'suggested pairs']];
  const short = (run) => String(run.label || run.run_id).split(',')[0];
  const was = found.before.counts;
  const now = found.after.counts;
  return el('div', {class: 'before-after'}, kinds.map(([key, noun]) => {
    const earlier = Number(was[key]) || 0;
    const later = Number(now[key]) || 0;
    const top = Math.max(earlier, later) || 1;
    const bar = (run, n, accent) => el('div', {class: 'bar'}, [
      el('span', {class: 'bar-label', title: run.run_id, text: short(run)}),
      el('span', {class: 'bar-track'}, [el('span', {class: 'bar-fill',
        style: `width:${Math.max(1, (100 * n) / top)}%;background:${accent}`})]),
      el('span', {class: 'bar-n', text: n.toLocaleString()}),
    ]);
    const change = later - earlier;
    return el('div', {class: 'ba'}, [
      el('p', {class: 'ba-head'}, [el('strong', {text: noun}), ' ',
        el('span', {class: 'faint', text: change ? `${change > 0 ? '+' : ''}${change.toLocaleString()}`
          : 'no change'})]),
      bar(found.before, earlier, 'var(--line)'),
      bar(found.after, later, 'var(--accent)'),
    ]);
  }));
}

function renderChanges(found) {
  const bindings = found.bindings;
  fill(detailPane,
    el('h2', {text: `From ${found.before.label} to ${found.after.label}`,
      title: `${found.before.run_id} to ${found.after.run_id}`}),
    el('ul', {class: 'said'}, (found.said || []).map((sentence) => el('li', {text: sentence}))),
    beforeAfter(found),
    el('details', {class: 'changed'}, [
      el('summary', {text: `Which words: ${bindings.moved.toLocaleString()} read differently, `
        + `${bindings.new.toLocaleString()} new, ${bindings.gone.toLocaleString()} gone`}),
      bars(bindings.by_group.map((item) => ({value: item.group, n: item.n})), {accent: 'var(--doubt)'}),
      el('ul', {class: 'ids'}, bindings.examples.map((item) => el('li', {}, [
        el('code', {text: item.token}), ` in ${item.name || '?'}: ${item.was} → ${item.now}`]))),
    ]),
    el('details', {class: 'changed'}, [
      el('summary', {text: `Which rules’ results: ${found.claims.gained.toLocaleString()} `
        + `gained, ${found.claims.lost.toLocaleString()} lost`}),
      byRule(found.claims, 'gained', 'lost')]),
    el('details', {class: 'changed'}, [
      el('summary', {text: `Which rules' flags: ${found.findings.raised.toLocaleString()} raised, `
        + `${found.findings.cleared.toLocaleString()} cleared`}),
      byRule(found.findings, 'raised', 'cleared')]),
    el('details', {class: 'changed'}, [
      el('summary', {text: `Which rules' suggested pairs: ${found.candidates.gained.toLocaleString()} `
        + `gained, ${found.candidates.lost.toLocaleString()} lost`}),
      byRule(found.candidates, 'gained', 'lost')]),
  );
}

/* ------------------------------------------------------------ the Questions page */

/* Where the Questions page is opened. The page is ``questions.js``, loaded the first time Questions
 * opens, as ``groups.js`` is loaded for the lattice. It is registered when the page's scripts have
 * all run (app.js, loaded after this file, holds ``VIEWS``), and before app.js opens the first
 * view. An address a person kept (``#sheet``, ``#questions``) opens Questions. */
let questionsScript = null;

function loadQuestionsPage() {
  if (window.questionsPage) return Promise.resolve();
  questionsScript = questionsScript || new Promise((resolve, reject) => {
    const script = el('script', {src: '/questions.js'});
    script.onload = () => resolve();
    script.onerror = () => {
      questionsScript = null;
      reject(new Error('Questions did not load: its script is missing from this '
        + 'installation. Reload the page.'));
    };
    document.head.append(script);
  });
  return questionsScript;
}

async function showQuestionsPage() {
  await loadQuestionsPage();
  return window.questionsPage.show();
}

document.addEventListener('DOMContentLoaded', () => {
  if (typeof VIEWS !== 'undefined') VIEWS.review = showQuestionsPage;
  if (typeof VIEW_ALIASES !== 'undefined') {
    Object.assign(VIEW_ALIASES, {sheet: 'review', questions: 'review'});
  }
});
