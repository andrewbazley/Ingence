/* The Dictionary: the engine's vocabulary, where each word came from, and a person's own.
 *
 * Every word is listed with its spellings, its kind, where it came from (a colored tag), and how
 * many names in this run it binds. A search answers as you type. A spelling is added, and a word or
 * a spelling inactivated and brought back, through the Record's one way in (the answer route). A
 * pack from a laboratory is installed from a file or removed through the named pack write. Inactive
 * says more than absent, so an inactive word stays listed.
 *
 * The view is registered into the sections of Advanced (Engine) as workspace.js registers Settings,
 * so app.js is not edited. The header's Export and Import buttons (transfer.js's dictionary entry)
 * call window.ingenceDictionaryExport() and installPack(file), as the Packs section's own do. */

const dictState = {q: '', origin: '', kind: '', order: '', offset: 0, term: '', flash: ''};

const ORIGIN_WORDS = {core: 'Core', domain: 'Domain', laboratory: 'Laboratory',
  yours: 'Yours', inactive: 'Inactive'};

function kindWords(kind) {
  return String(kind || '').replace(/_/g, ' ');
}

function dayOf(moment) {
  const when = new Date(moment);
  return Number.isNaN(when.getTime()) ? String(moment)
    : when.toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'});
}

function originTag(origin) {
  const shown = origin.source && origin.origin !== 'core' && origin.origin !== 'yours'
    ? `${ORIGIN_WORDS[origin.origin]}: ${origin.source}` : ORIGIN_WORDS[origin.origin];
  return el('span', {class: `tag origin-${origin.origin}`, text: shown});
}

function inactiveLine(state) {
  return `inactive since ${dayOf(state.since)}, by ${state.by}`;
}

/* ------------------------------------------------------------------ the list */

let dictTimer = null;
let dictKinds = [];

async function showDictionary(options) {
  /* A search redraws the list only: the word open beside it is left as it is, with whatever is
   * being typed there (a late redraw would empty the spelling being added). */
  const keepDetail = Boolean(options && options.keepDetail === true);
  const turn = turns.claim(listPane);
  const query = new URLSearchParams({offset: String(dictState.offset)});
  if (dictState.q) query.set('q', dictState.q);
  if (dictState.origin) query.set('origin', dictState.origin);
  if (dictState.kind) query.set('kind', dictState.kind);
  if (dictState.order) query.set('order', dictState.order);
  const [data, held] = await Promise.all([api(`/api/dictionary?${query}`),
    api('/api/dictionary/packs')]);
  if (!turns.owns(listPane, turn)) return;
  if (!dictState.q && !dictState.origin) dictKinds = data.kinds.map(([kind]) => kind);
  const box = el('input', {type: 'search', class: 'dict-search', autocomplete: 'off',
    'data-help': 'dictionary.search', 'aria-label': 'Find a word or a spelling',
    placeholder: 'Find a word or a spelling', value: dictState.q});
  box.addEventListener('input', () => {
    if (dictTimer) clearTimeout(dictTimer);
    dictTimer = setTimeout(() => {
      dictState.q = box.value.trim();
      dictState.offset = 0;
      showDictionary({keepDetail: true}).then(() => {
        const again = listPane.querySelector('.dict-search');
        if (again) {
          again.focus();
          again.setSelectionRange(again.value.length, again.value.length);
        }
      }).catch(reportError);
    }, 160);
  });
  fill(listPane,
    el('h2', {text: 'Dictionary'}),
    el('p', {class: 'dict-stats'}, [
      el('strong', {text: data.terms.toLocaleString()}), ' words · ',
      el('strong', {text: data.spellings.toLocaleString()}), ' spellings · ',
      el('button', {class: 'link', type: 'button', 'data-help': 'dictionary.yours',
        text: `${data.yours.toLocaleString()} yours`, onclick: () => chooseOrigin('yours')}),
      data.counted ? null : el('span', {class: 'faint', text: ' · names not counted'}),
    ]),
    box,
    originChips(data),
    el('p', {class: 'dict-controls'}, [kindChooser(data), orderChooser()]),
    dictState.flash ? el('p', {class: 'note kept', role: 'status', text: dictState.flash}) : null,
    data.rows.length ? dictTable(data) : el('p', {class: 'nothing', text: dictState.q
      ? `No word is spelled "${dictState.q}".` : 'No word matches.'}),
    dictPager(data),
    addWordForm(),
    packsPart(held),
  );
  dictState.flash = '';
  const first = listPane.querySelector('table.dictionary tbody tr.row');
  /* A split view is never left empty: with no word chosen the first opens, as every other split
   * view opens its first row. A click or a search moves it. */
  if (!dictState.term && first) first.classList.add('selected');
  const open = dictState.term || (first && first.dataset.term);
  if (keepDetail && open && detailPane.dataset.term === open) return;
  if (open) showTerm(open).catch((error) => states.failed(detailPane, error));
  else fill(detailPane, el('p', {class: 'empty', text: 'Choose a word.'}));
}

function chooseOrigin(origin) {
  dictState.origin = dictState.origin === origin ? '' : origin;
  dictState.offset = 0;
  showDictionary().catch(reportError);
}

function originChips(data) {
  const chip = (origin, count) => el('button', {type: 'button',
    class: `chip origin-${origin || 'all'}${dictState.origin === origin ? ' active' : ''}`,
    'data-help': 'dictionary.origin', 'aria-pressed': dictState.origin === origin ? 'true' : 'false',
    onclick: () => chooseOrigin(origin)},
  [origin ? ORIGIN_WORDS[origin] : 'All', ' ', el('span', {class: 'chip-n', text: count.toLocaleString()})]);
  return el('p', {class: 'chips'}, [
    chip('', data.searched),
    ...['core', 'domain', 'laboratory', 'yours', 'inactive']
      .filter((origin) => data.origins[origin] || dictState.origin === origin)
      .map((origin) => chip(origin, data.origins[origin] || 0)),
  ]);
}

function kindChooser(data) {
  const counts = Object.fromEntries(data.kinds);
  const select = el('select', {'data-help': 'dictionary.kind', 'aria-label': 'Kind'}, [
    el('option', {value: '', text: 'every kind'}),
    ...(dictKinds.length ? dictKinds : data.kinds.map(([kind]) => kind)).map((kind) =>
      el('option', {value: kind, text: `${kindWords(kind)} (${(counts[kind] || 0).toLocaleString()})`})),
  ]);
  select.value = dictState.kind;
  select.addEventListener('change', () => {
    dictState.kind = select.value;
    dictState.offset = 0;
    showDictionary().catch(reportError);
  });
  return select;
}

function orderChooser() {
  const select = el('select', {'data-help': 'dictionary.order', 'aria-label': 'Order'}, [
    el('option', {value: '', text: 'most used first'}),
    el('option', {value: 'term', text: 'A to Z'}),
  ]);
  select.value = dictState.order;
  select.addEventListener('change', () => {
    dictState.order = select.value;
    dictState.offset = 0;
    showDictionary().catch(reportError);
  });
  return select;
}

function spellingChips(row, limit = 5) {
  const shown = row.surfaces.slice(0, limit).map((item) => el('code', {
    class: `spelling${item.inactive ? ' inactive' : ''}${item.origins.includes('yours') ? ' yours' : ''}`,
    title: item.inactive ? inactiveLine(item.inactive) : '', text: item.spelling}));
  const more = row.surfaces.length - limit;
  return [...shown, more > 0 ? el('span', {class: 'faint', text: ` +${more}`}) : null];
}

/* Who taught it: a person's own answer says who, with a magenta dot, and when; a word that ships
 * with Ingence says where it comes from instead. */
function taughtCell(row) {
  const others = row.origins.filter((origin) => origin.origin !== 'yours').map(originTag);
  if (!row.yours) return el('td', {class: 'taught'}, others);
  return el('td', {class: 'taught'}, [
    el('span', {class: 'person-dot'}),
    el('span', {class: 'person-name', text: row.taught ? row.taught.by : 'a person'}),
    row.taught ? el('span', {class: 'faint', text: dayOf(row.taught.at)}) : null,
    ...others,
  ]);
}

/* The rows are laid out as the Rules' are: the word as it is written (its spellings), the sense it
 * has, the kind of meaning, who taught it and where it stands (how many names in this run it
 * binds). Choosing a row opens the word's card beside the table. */
function dictTable(data) {
  const top = Math.max(1, ...data.rows.map((row) => Number(row.names) || 0));
  const rows = data.rows.map((row) => {
    const tr = el('tr', {class: `row${row.inactive ? ' inactive' : ''}${row.term === dictState.term ? ' selected' : ''}`,
      'data-term': row.term, tabindex: '0'}, [
      el('td', {class: 'spellings'}, spellingChips(row)),
      el('td', {class: 'sense'}, [el('strong', {text: row.term}),
        row.inactive ? el('div', {class: 'faint', text: inactiveLine(row.inactive)}) : null]),
      el('td', {class: 'kind'}, row.kinds.map((kind) => el('span', {class: 'tag plain', text: kindWords(kind)}))),
      taughtCell(row),
      el('td', {class: 'n names'}, row.names == null ? [el('span', {class: 'faint', text: 'not counted'})] : [
        el('span', {class: 'names-bar', style: `width:${Math.round((48 * row.names) / top)}px`}),
        ` ${Number(row.names).toLocaleString()} ${row.names === 1 ? 'name' : 'names'}`]),
    ]);
    const open = () => {
      dictState.term = row.term;
      for (const other of listPane.querySelectorAll('tr.row.selected')) other.classList.remove('selected');
      tr.classList.add('selected');
      showTerm(row.term).catch((error) => states.failed(detailPane, error));
    };
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', (event) => { if (event.key === 'Enter') open(); });
    return tr;
  });
  return el('table', {class: 'dictionary', 'data-help': 'dictionary.table'}, [
    el('thead', {}, [el('tr', {}, ['Word', 'Meaning', 'Kind of meaning', 'Who taught it', '#Names matched'].map((h) =>
      el('th', {class: h.startsWith('#') ? 'n' : '', text: h.replace(/^#/, '')})))]),
    el('tbody', {}, rows)]);
}

function dictPager(data) {
  if (data.listed <= data.limit && !dictState.offset) return null;
  const last = Math.min(dictState.offset + data.limit, data.listed);
  const move = (to) => { dictState.offset = to; showDictionary().catch(reportError); };
  return el('p', {class: 'pager', 'data-help': 'review.pager'}, [
    el('button', {class: 'link', type: 'button', text: '← previous 50',
      ...(dictState.offset ? {} : {disabled: ''}), onclick: () => move(Math.max(0, dictState.offset - 50))}),
    ` ${(dictState.offset + 1).toLocaleString()} to ${last.toLocaleString()} of ${data.listed.toLocaleString()} `,
    el('button', {class: 'link', type: 'button', text: 'next 50 →',
      ...(last < data.listed ? {} : {disabled: ''}), onclick: () => move(dictState.offset + 50)}),
  ]);
}

/* ------------------------------------------------------------------ writing */

async function dictWrite(body) {
  const who = await whoWrites();
  if (!who) return null;
  try {
    const written = await write('/api/review/answer', {actor: who, ...body});
    dictState.flash = written.applying ? '' : 'Recorded. Takes effect at the next Re-run.';
    return written;
  } catch (error) {
    await dialogs.failed(error);
    return null;
  }
}

/* One answer per spelling (the Record's): where a person's answer already gives the spelling a
 * meaning, or writes it in another case, the page says so before anything is written. */
async function dictAdd(body) {
  let held = null;
  try {
    held = (await api(`/api/dictionary/answer?spelling=${encodeURIComponent(body.spelling)}`)).answer;
  } catch (error) {
    held = null;
  }
  if (held && (held.means !== body.term || held.spelling !== body.spelling)) {
    const sure = await dialogs.choose({title: 'Replace an answer?',
      text: `${held.spelling} already means ${held.means} (${held.by}'s answer of ${dayOf(held.at)}). `
        + 'This answer replaces it.', yes: 'Replace'});
    if (!sure) return null;
    return dictWrite({...body, replace: true});
  }
  return dictWrite(body);
}

function addWordForm() {
  const spelling = el('input', {type: 'text', name: 'spelling', autocomplete: 'off',
    'aria-label': 'Spelling', placeholder: 'spelling', 'data-help': 'dictionary.add'});
  const term = el('input', {type: 'text', name: 'term', autocomplete: 'off',
    'aria-label': 'Meaning', placeholder: 'meaning: a word listed, or a new one'});
  const kind = el('select', {name: 'kind', 'aria-label': 'Kind'}, [
    el('option', {value: '', text: 'kind (for a new meaning)'}),
    ...dictKinds.map((each) => el('option', {value: each, text: kindWords(each)}))]);
  const form = el('form', {class: 'dict-add'}, [
    spelling, ' means ', term, ' ', kind, ' ',
    el('button', {type: 'submit', class: 'primary', text: 'Add'}),
  ]);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const written = await dictAdd({dictionary: 'add', spelling: spelling.value.trim(),
      term: term.value.trim(), theme_kind: kind.value || undefined});
    if (!written) return;
    dictState.term = term.value.trim();
    await showDictionary();
  });
  return el('details', {class: 'dict-add-part', 'data-help': 'dictionary.add'}, [
    el('summary', {text: 'Add a word'}), form]);
}

/* ------------------------------------------------------------------ one word */

/* Copy as YAML: the entry a pack file accepts, read from the engine (the same text the website's
 * reference writes). What a person inactivated is left out, so an inactive word has nothing to
 * copy. */
function copyYamlChip(term, inactive) {
  const chip = copyChip({text: 'Copy as YAML', help: 'dictionary.copy', kind: 'yaml',
    getText: async () => (await api(`/api/v1/dictionary/copy?term=${encodeURIComponent(term)}`)).text || null});
  if (inactive) {
    chip.disabled = true;
    chip.title = 'This word is inactive, so there is nothing to copy. Bring it back to copy it.';
  }
  return chip;
}

async function showTerm(term) {
  const turn = turns.claim(detailPane);
  const data = await api(`/api/dictionary/term?term=${encodeURIComponent(term)}`);
  if (!turns.owns(detailPane, turn)) return;
  const refresh = async () => { await showDictionary(); };
  const senseButton = data.inactive
    ? el('button', {type: 'button', class: 'answer', 'data-help': 'dictionary.reactivate',
      onclick: async () => { if (await dictWrite({dictionary: 'reactivate', term})) await refresh(); }},
    [el('strong', {text: `Bring back ${term}`})])
    : el('button', {type: 'button', class: 'answer quiet', 'data-help': 'dictionary.inactivate',
      onclick: async () => { if (await dictWrite({dictionary: 'inactivate', term})) await refresh(); }},
    [el('strong', {text: `Inactivate ${term}`})]);
  const addBox = el('input', {type: 'text', autocomplete: 'off', 'aria-label': 'Another spelling',
    placeholder: 'another spelling'});
  const addForm = el('form', {class: 'dict-add-spelling', 'data-help': 'dictionary.add'}, [addBox, ' ',
    el('button', {type: 'submit', text: 'Add'})]);
  addForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (await dictAdd({dictionary: 'add', term, spelling: addBox.value.trim()})) await refresh();
  });
  detailPane.dataset.term = term;
  /* As the website's word card: what the word is, where it comes from,
   * the kind of meaning, whether it is ambiguous, who taught it, its meanings and where it stands. */
  const spelled = data.surfaces.slice(0, 6).map((item) => item.spelling);
  const idLine = [...data.origins.map((origin) => (origin.source && origin.origin !== 'core' && origin.origin !== 'yours'
    ? origin.source : ORIGIN_WORDS[origin.origin].toLowerCase())),
  spelled.length ? `spelled ${spelled.join(', ')}${data.surfaces.length > spelled.length
    ? ` and ${data.surfaces.length - spelled.length} more` : ''}` : ''].filter(Boolean).join(' \u00b7 ');
  const alsoRead = [...new Set(data.senses.flatMap((sense) => sense.ambiguous_with || []))];
  const facts = el('dl', {class: 'word-facts'}, [
    el('dt', {text: 'Kind of meaning'}),
    el('dd', {text: data.kinds.map(kindWords).join(', ') || 'none stated'}),
    alsoRead.length ? el('dt', {text: 'Ambiguous'}) : null,
    alsoRead.length ? el('dd', {}, [el('span', {class: 'ambiguous', text: 'Yes.'}),
      ` Also read as ${alsoRead.map(kindWords).join(', ')}.`]) : null,
    el('dt', {text: 'Who taught it'}),
    el('dd', {}, data.yours
      ? [el('span', {class: 'person-dot'}), `${data.taught ? data.taught.by : 'A person'}`,
        data.taught ? `, ${dayOf(data.taught.at)}` : '']
      : ['No one: it comes with Ingence.']),
  ]);
  fill(detailPane, el('div', {class: 'card word-card', 'data-term': term}, [
    el('p', {class: 'eyebrow', text: 'Dictionary word'}),
    el('h2', {class: 'word-card-sentence', text: term}),
    el('p', {class: 'card-id', text: idLine}),
    el('p', {}, [...data.kinds.map((kind) => el('span', {class: 'tag plain', text: kindWords(kind)})),
      ...data.origins.map(originTag)]),
    data.inactive ? el('p', {class: 'note refused', text: `Inactive since ${dayOf(data.inactive.since)}, by ${data.inactive.by}.`}) : null,
    facts,
    el('div', {class: 'rules-chips'}, [
      copyYamlChip(term, Boolean(data.inactive)),
    ]),
    el('p', {class: 'faint rules-waits', text: 'The entry as a dictionary file holds it, under entries:.'}),
    el('h3', {text: 'Meanings'}),
    el('ol', {class: 'word-meanings dict-senses'}, data.senses.map((sense) => el('li', {}, [
      originTag(sense), ` reads it as ${kindWords(sense.theme_kind)}`,
      sense.scope !== 'project' ? el('span', {class: 'faint', text: ` \u00b7 ${sense.scope}`}) : null,
      sense.origin === 'yours' && data.taught ? el('span', {class: 'faint', text: ` \u00b7 taught by ${data.taught.by}`}) : null,
    ]))),
    el('h3', {text: 'Names matched'}),
    el('p', {class: 'dict-names'}, data.names == null ? 'Names it matches: not counted.' : [
      el('strong', {text: Number(data.names).toLocaleString()}),
      data.names === 1 ? ' name in this scan.' : ' names in this scan.']),
    data.examples.length ? el('p', {class: 'faint'}, data.examples.flatMap((name, index) =>
      [index ? ' \u00b7 ' : '', el('code', {text: name})])) : null,
    el('h3', {text: 'Spellings'}),
    el('ul', {class: 'dict-spellings'}, data.surfaces.map((item) => el('li', {class: item.inactive ? 'inactive' : ''}, [
      el('code', {class: 'spelling', text: item.spelling}), ' ',
      ...item.origins.map((origin) => el('span', {class: `tag origin-${origin}`, text: ORIGIN_WORDS[origin]})),
      item.inactive ? el('span', {class: 'faint', text: ` ${inactiveLine(item.inactive)} `}) : ' ',
      data.inactive ? null : el('button', {type: 'button', class: 'link',
        'data-help': item.inactive ? 'dictionary.reactivate' : 'dictionary.inactivate',
        text: item.inactive ? 'bring back' : 'inactivate',
        onclick: async () => {
          const act = item.inactive ? 'reactivate' : 'inactivate';
          if (await dictWrite({dictionary: act, term, spelling: item.spelling})) await refresh();
        }}),
    ]))),
    data.inactive ? null : addForm,
    el('p', {class: 'answers'}, [senseButton]),
    el('h3', {text: 'In the answer log'}),
    data.history.length ? el('ul', {class: 'record'}, data.history.map((item) => el('li', {class: item.in_force ? 'in-force' : ''}, [
      `${item.what}, by ${item.actor}, ${dayOf(item.occurred_at)}`,
      item.note ? el('div', {class: 'payload', text: `note: ${item.note}`}) : null,
    ]))) : el('p', {class: 'faint', text: 'Nothing yet.'}),
  ]));
}

/* ------------------------------------------------------------------ packs */

const PACK_ANSWERED = {accept: 'accepted', refuse: 'refused', not_over_the_core: 'used after the core'};

function packsPart(held) {
  /* In every mode, a pack that reads nothing here can be answered, and may be refused. */
  const answerable = (pack) => held.mode === 'named' || pack.idle || Boolean(pack.answer);
  const domainRows = held.domain.map((pack) => el('tr', {}, [
    el('td', {}, [el('strong', {text: pack.domain}), pack.proposed
      ? el('span', {class: 'tag now', text: pack.idle ? 'reads nothing here' : 'suggested'}) : null]),
    el('td', {class: 'n', text: Number(pack.senses).toLocaleString()}),
    el('td', {}, [el('span', {class: `tag ${pack.loaded ? 'now' : 'plain'}`, text: pack.loaded ? 'loaded' : 'not loaded'}),
      pack.answer ? el('span', {class: 'faint', text: ` ${PACK_ANSWERED[pack.answer.word] || pack.answer.word} by ${pack.answer.by}, ${dayOf(pack.answer.at)}`})
        : ((held.mode === 'named' && pack.loaded) || String(pack.why || '').startsWith('accepted by default')
          ? el('span', {class: 'faint', text: ` ${pack.why}`}) : null),
      /* Over the core, or kept loaded and not over it: answerable in every mode. */
      pack.accepted ? el('span', {class: 'tag now', text: 'used before the core'}) : null,
      pack.not_over_the_core ? el('span', {class: 'faint', text: ' loaded, used after the core'}) : null]),
    el('td', {}, answerable(pack) || pack.accepted ? [el('span', {class: 'answers'}, [
      ...(answerable(pack) ? ['accept', 'refuse'] : []),
      ...(pack.accepted ? ['not_over_the_core'] : [])].map((word) =>
      el('button', {type: 'button', 'data-help': 'dictionary.pack_answer',
        /* A loaded pack's words say what each does to it. */
        text: word === 'not_over_the_core' ? 'use after the core'
          : held.mode === 'named' ? word : {accept: 'keep', refuse: 'stop loading it'}[word],
        class: pack.answer && pack.answer.word === word ? 'active' : '',
        onclick: async () => {
          if (await dictWrite({target: pack.target, word})) {
            dictState.flash = 'Recorded. Takes effect at the next Re-run.';
            await showDictionary();
          }
        }})))] : []),
  ]));
  const labRows = held.laboratory.map((pack) => el('tr', {}, [
    el('td', {}, [el('strong', {text: pack.name}), el('span', {class: 'faint', text: ` version ${pack.version}`})]),
    el('td', {class: 'n', text: Number(pack.senses).toLocaleString()}),
    el('td', {}, [el('span', {class: `tag ${pack.loaded ? 'now' : 'plain'}`, text: pack.loaded ? 'loaded' : 'not loaded'}),
      pack.summary ? el('span', {class: 'faint', text: ` ${pack.summary}`}) : null]),
    el('td', {}, [el('button', {type: 'button', class: 'link', 'data-help': 'dictionary.pack_remove',
      text: 'remove', onclick: async () => {
        const sure = await dialogs.choose({title: `Remove ${pack.name}?`,
          text: 'Ingence stops loading its words at the next Re-run. Its files stay in the project.', yes: 'Remove'});
        if (!sure) return;
        const who = await whoWrites();
        if (!who) return;
        try {
          await write('/api/dictionary/pack', {remove: pack.name, actor: who});
          dictState.flash = `${pack.name} removed. Takes effect at the next Re-run.`;
          await showDictionary();
        } catch (error) {
          await dialogs.failed(error);
        }
      }})]),
  ]));
  const picker = el('input', {type: 'file', accept: '.yaml,.yml', 'aria-label': 'A word list file',
    class: 'dict-file', 'data-help': 'dictionary.import'});
  picker.addEventListener('change', async () => {
    const file = picker.files && picker.files[0];
    if (!file) return;
    try {
      await installPack(file);
    } catch (error) {
      await dialogs.failed(error, 'Ingence could not install the word list');
    }
  });
  return el('section', {class: 'dict-packs', 'data-help': 'dictionary.packs'}, [
    el('h3', {text: 'Word lists'}),
    el('p', {class: 'faint', text: 'A word list only adds words and their meanings.'
      + (held.mode === 'named' ? '' : held.domain.every((pack) => pack.loaded)
        ? ' Ingence loads every domain word list.' : ' Ingence loads every domain word list except those refused.')}),
    el('table', {}, [
      el('thead', {}, [el('tr', {}, ['word list', '#words', '', ''].map((h) =>
        el('th', {class: h.startsWith('#') ? 'n' : '', text: h.replace(/^#/, '')})))]),
      el('tbody', {}, [...domainRows, ...labRows])]),
    collisionsPart(held.collisions || [], held.standing_rule),
    el('p', {class: 'answers'}, [
      el('label', {class: 'answer quiet'}, [el('strong', {text: 'Install from a file'}), picker]),
      el('button', {type: 'button', class: 'answer quiet', 'data-help': 'dictionary.export',
        onclick: () => window.ingenceDictionaryExport().catch((error) => dialogs.failed(error, 'Ingence could not export your words'))},
      [el('strong', {text: 'Export yours'})]),
    ]),
  ]);
}

/* Spellings the core and a loaded pack share: the core's meaning is used over a shipped pack's. A
 * pack from a laboratory ties with the core until a person chooses, and its meaning is used over a
 * shipped pack's. Where the project accepted a pack, its meaning is used over the core's, and
 * another shipped pack's ties with the core. One row per spelling. Folded away, each a search. */
function collisionsPart(rows, rule) {
  if (!rows.length) return null;
  const meaning = (tag, origin, term, used) => [el('span', {class: `tag origin-${origin}`, text: tag}), ' ',
    used ? el('strong', {text: kindWords(term)}) : kindWords(term)];
  const ties = rows.filter((row) => row.used === 'neither').length;
  return el('details', {class: 'dict-collisions', 'data-help': 'dictionary.collisions'}, [
    el('summary', {text: `${rows.length.toLocaleString()} spellings a word list shares with the core or your list`
      + (ties ? ` (${ties.toLocaleString()} tied)` : '')}),
    /* The one rule of standing, in the engine's words. */
    rule ? el('p', {class: 'faint', text: rule}) : null,
    el('ul', {class: 'dict-senses'}, rows.map((row) => el('li', {}, [
      el('button', {type: 'button', class: 'link', text: row.spelling, onclick: () => {
        dictState.q = row.spelling;
        dictState.offset = 0;
        showDictionary().catch(reportError);
      }}), ' ',
      ...row.core.flatMap((sense) => [...meaning('Core', 'core', sense.term, row.used === 'core'), ' ']),
      ...row.packs.flatMap((sense) => [...meaning(sense.pack, sense.laboratory ? 'laboratory' : 'domain',
        sense.term, (row.used === 'laboratory' && sense.laboratory)
          || (row.used === 'accepted pack' && !sense.laboratory && sense.pack === row.accepted)), ' ']),
      row.used === 'neither' ? el('span', {class: 'tag now', text: 'tie'}) : null,
    ]))),
  ]);
}

/* ------------------------------------------------------------------ export and import */

/* Export the person's own dictionary -- what they added and answered, and what they inactivated
 * -- as a pack section file, saved by the browser. Resolves to what the server said. */
window.ingenceDictionaryExport = async function ingenceDictionaryExport() {
  const found = await api('/api/dictionary/export');
  const link = el('a', {href: URL.createObjectURL(new Blob([found.text], {type: 'text/yaml'})),
    download: found.filename});
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  return found;
};

/* Install a pack file, say so, and show the page again: the Packs section's picker and the header's
 * Import do the same. */
async function installPack(file) {
  const done = await window.ingenceDictionaryImport(file);
  dictState.flash = `${done.installed} installed: ${done.senses} words. Takes effect at the next Re-run.`;
  await showDictionary();
  return done;
}

/* Install one pack section file (a File) as a laboratory pack of this project. */
window.ingenceDictionaryImport = async function ingenceDictionaryImport(file) {
  if (file.size > 4 * 1024 * 1024 - 4096) {
    throw new Error('A file up to 4 MB installs here. Install a larger one with '
      + '`ingence teach pack install`.');
  }
  const who = await whoWrites();
  if (!who) throw new Error('No name is set on this computer, so Ingence installed nothing.');
  const text = await file.text();
  return write('/api/dictionary/pack', {install: {filename: file.name, text}, actor: who});
};

/* Joined to whichever page's sections hold its button, once, whether or not another script named
 * the section first. */
VIEWS.dictionary = showDictionary;
(() => {
  const nav = document.querySelector('nav.sections button[data-view="dictionary"]');
  const page = (nav && nav.closest('nav.sections').dataset.sectionsOf) || 'parameters';
  PAGES[page] = PAGES[page] || [];
  if (!PAGES[page].includes('dictionary')) PAGES[page].push('dictionary');
  PAGE_OF.dictionary = page;
})();
/* A row carries its spellings, so the list takes the wider half, as the review pages' do. */
REVIEW_VIEWS.add('dictionary');
