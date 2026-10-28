/* Engine > Rules.
 *
 * The page is a table collapsible by group, with a card for the chosen rule. Each group (what a
 * rule does: what the walk records, what files are, which files go together, ...) is a header row
 * that opens and closes, with its number of rules and what those rules concluded in this run.
 * Under it is one row a rule: the rule in plain words, this run (the count in mono, or
 * "nothing in this run" / "switched off"), its name and version in mono, and whether it is in
 * force (off in magenta where a person switched it off). Choosing a row opens the rule's card in
 * the right pane: the sentence, the name and version, the full statement, what it decides and
 * reads, what stood against it, what this run rests on it, its settings (a change is a draft and
 * waits for Re-run), and the chips "Copy as a revision", "Switch off..." and "Its conclusions".
 *
 * The page already holds every rule, so the search is made here: each word typed must appear in the
 * rule's name, its sentence, its statement or its group's heading. Groups with no match are left
 * out, and groups with a match open. The count reads "k of n". The counts of a group are sums of
 * the rows' served counts, never a number worked out here.
 *
 * "Copy" puts on the clipboard what the website's reference writes (``copy_text.py``), read from
 * ``/api/v1/rules/copy``. There is one source, and a test proves it. "Switch off..." says how the
 * change is made, because a rule is switched off by a change in the project's own rules. The
 * Dashboard does not write that file, and a hand-written file is read at the next run.
 *
 * The view registers itself in VIEWS, as settings.js and dictionary.js do. Loaded after app.js and
 * review.js (el, api, write, fill, states, turns, label, dialogs, navigate) and workspace.js
 * (canonicalValue, draftValue, plainRefusal). */

/* The words on this page. */
const RULES_WORDS = {
  searchPlaceholder: 'Search rules: a word, a name, what it decides',
  searchHelp: 'Each typed word must appear in the rule\'s name, its sentence, its statement or its group.',
  nothing: 'nothing in this scan',
  switchedOff: 'switched off',
  copyRevision: 'Copy as a revision',
  copied: 'Copied',
  offFile: 'To switch off a rule, save a file in your project’s rulebook folder. The next scan reads it.',
  replaced: 'A newer rule replaced this one. Ingence accepts no change to a replaced rule, so there is nothing to copy.',
};

/* How long a Copy button says "Copied" before it says what it did before. */
const COPIED_MS = 2000;

/* What this page remembers while it is drawn again: the typed search, which groups are open
 * (null until the first draw chooses), the rule whose card is open, and a rule another page asked
 * to be shown. */
const rulesView = {query: '', open: null, chosen: '', reveal: '', model: null};

/* The Rules section of Advanced (Engine) is a list beside its card, as the Years view is (app.js
 * lays the page out by these sets); the card is this page's own. */
SINGLE_VIEWS.delete('rules');
YEARS_VIEWS.add('rules');
OWN_DETAIL.add('rules');

/* ------------------------------------------------------------------ plain words */

function rulesCount(n) {
  return (Number(n) || 0).toLocaleString('en-US');
}

function rulesPlural(n, one, many) {
  return `${rulesCount(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;
}

/* What a run rests on a rule, in words. */
function restsOn(rule) {
  const said = [];
  if (rule.claims) said.push(`${rulesCount(rule.claims)} result${rule.claims === 1 ? '' : 's'}`);
  if (rule.findings) said.push(`${rulesCount(rule.findings)} flag${rule.findings === 1 ? '' : 's'}`);
  if (rule.candidates) {
    said.push(`${rulesCount(rule.candidates)} pair${rule.candidates === 1 ? '' : 's'} suggested`);
  }
  return said.length ? said.join(' · ') : RULES_WORDS.nothing;
}

/* The one number a row shows: what the run rests on the rule, the three kinds added (a sum of
 * served counts). The card says each kind in words. */
function ruleRests(rule) {
  return (Number(rule.claims) || 0) + (Number(rule.findings) || 0) + (Number(rule.candidates) || 0);
}

/* A group's counts in words: its conclusions, its flags and the pairs its rules suggested. */
function groupRests(rows) {
  const total = (name) => rows.reduce((sum, row) => sum + (Number(row.rule[name]) || 0), 0);
  const said = [];
  if (total('claims')) said.push(rulesPlural(total('claims'), 'result'));
  if (total('findings')) said.push(rulesPlural(total('findings'), 'flag'));
  if (total('candidates')) said.push(`${rulesCount(total('candidates'))} pairs suggested`);
  return said.length ? said.join(' · ') : RULES_WORDS.nothing;
}

/* A parameter's value as a person reads it: yes or no, a list joined, a mapping as arrows. */
function parameterWords(value) {
  if (value === null || value === undefined) return 'none';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'nothing';
  if (typeof value === 'object') {
    const pairs = Object.entries(value);
    return pairs.length ? pairs.map(([key, item]) => `${key} → ${item}`).join(', ') : 'nothing';
  }
  return String(value);
}

/* ------------------------------------------------------------------ settings */

/* Every rule parameter has an input made for its declared type: a yes-or-no gets a checkbox, a
 * whole number or a number of seconds a whole-number box with its bounds, a real number a number
 * box with its bounds, a choice a list of its choices, a list one item per line, a mapping one
 * "key = value" per line, and a word a text box. The value shown is the draft where there is one,
 * else what the project's revisions give now. Typing saves a draft of kind ``rule_parameters``
 * (workspace.js), which Commit turns into a project Rulebook revision. Neither writes
 * settings.yaml or the Record. */
function parameterInput(ruleId, parameter) {
  const shown = parameter.drafted ? parameter.draft : parameter.committed;
  const attributes = {'data-draft': `rule_parameters:${ruleId}.${parameter.name}`,
    'data-original': canonicalValue(parameter.committed),
    'aria-label': `${parameter.name} of ${ruleId}`, 'data-help': 'rules.parameter'};
  const bounded = (attributesOf) => {
    if (parameter.minimum !== null && parameter.minimum !== undefined) attributesOf.min = String(parameter.minimum);
    if (parameter.maximum !== null && parameter.maximum !== undefined) attributesOf.max = String(parameter.maximum);
    return attributesOf;
  };
  let input;
  switch (parameter.type) {
    case 'boolean':
      input = el('input', {...attributes, type: 'checkbox'});
      input.checked = Boolean(shown);
      break;
    case 'integer':
    case 'duration':
      input = el('input', bounded({...attributes, type: 'number', step: '1', required: '',
        value: shown == null ? '' : String(shown)}));
      break;
    case 'real':
      input = el('input', bounded({...attributes, type: 'number', step: 'any', required: '',
        value: shown == null ? '' : String(shown)}));
      break;
    case 'enum':
      input = el('select', attributes, parameter.choices.map((choice) =>
        el('option', {value: choice, text: label(choice)})));
      input.value = String(shown);
      break;
    case 'string_list':
      input = el('textarea', {...attributes, rows: String(Math.min(8, Math.max(2, (shown || []).length))),
        'data-draft-type': 'list'});
      input.value = (shown || []).join('\n');
      break;
    case 'mapping':
      input = el('textarea', {...attributes, rows: String(Math.min(8, Math.max(2, Object.keys(shown || {}).length))),
        'data-draft-type': 'mapping'});
      input.value = Object.entries(shown || {}).map(([key, item]) => `${key} = ${item}`).join('\n');
      break;
    default:
      input = el('input', {...attributes, type: 'text', value: shown == null ? '' : String(shown)});
  }
  if (parameter.drafted) input.dataset.changed = '1';
  return input;
}

/* One setting on a rule's card: its name and what it is for, its value as a field, a value that
 * differs from the shipped one marked "changed" with the default on hover, and what the run
 * shown used where that is another value. */
function ruleSetting(rule, parameter) {
  const changed = canonicalValue(parameter.committed) !== canonicalValue(parameter.default);
  const input = parameterInput(rule.rule_id, parameter);
  /* What is typed is kept in what this page holds, so choosing another rule and coming back shows
   * it, not the value the page was drawn with. The draft itself is workspace.js's. */
  input.addEventListener('input', () => {
    try {
      const value = draftValue(input);
      parameter.draft = value;
      parameter.drafted = canonicalValue(value) !== canonicalValue(parameter.committed);
    } catch (error) {
      parameter.drafted = false;
    }
  });
  const bounds = [
    parameter.minimum != null ? `at least ${parameter.minimum}` : '',
    parameter.maximum != null ? `at most ${parameter.maximum}` : '',
    parameter.type === 'string_list' ? 'one per line' : '',
    parameter.type === 'mapping' ? 'one "key = value" per line' : '',
  ].filter(Boolean).join(', ');
  return el('div', {class: `rule-setting${changed ? ' changed' : ''}`, 'data-setting': parameter.name}, [
    el('div', {class: 'rule-setting-name'}, [
      el('code', {text: parameter.name}),
      parameter.description ? el('div', {class: 'purpose', text: parameter.description}) : null,
      bounds ? el('div', {class: 'faint', text: bounds}) : null,
    ]),
    el('div', {class: 'rule-setting-value'}, [
      input,
      changed ? el('span', {class: 'changed-mark'}, [el('span', {class: 'tag changed',
        title: `The default is ${parameterWords(parameter.default)}`, text: 'changed'})]) : null,
      parameter.drafted ? el('span', {class: `draft-note${parameter.error ? ' draft-error' : ''}`,
        text: parameter.error ? `not valid: ${plainRefusal(parameter.error)}` : 'draft, saved'}) : null,
      !parameter.drafted && canonicalValue(parameter.committed) !== canonicalValue(parameter.value)
        ? el('span', {class: 'draft-note', text: 'committed; the next re-run uses it'}) : null,
    ]),
    canonicalValue(parameter.value) !== canonicalValue(parameter.committed)
      ? el('div', {class: 'faint rule-setting-used', text: `This scan used ${parameterWords(parameter.value)}`}) : null,
  ]);
}

/* ------------------------------------------------------------------ copy */

/* Put text on the clipboard: the browser's own way where it allows it, else a selected box that
 * is copied and removed. Says in words where neither works. */
async function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (error) {
      /* Not allowed here: try the older way below. */
    }
  }
  const box = el('textarea', {'aria-hidden': 'true', tabindex: '-1', readonly: '',
    style: 'position:fixed;left:-9999px;top:0'});
  box.value = text;
  document.body.append(box);
  box.select();
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } finally {
    box.remove();
  }
  if (!copied) throw new Error('This browser did not let the page copy. Select the text and copy it by hand.');
}

/* A button that copies what ``getText`` returns, says "Copied" for two seconds and then says what
 * it said before. Nothing else moves: the button keeps its width while it says the shorter word. */
function copyChip({text, help, kind, getText, className = 'rules-chip'}) {
  let timer = null;
  const button = el('button', {type: 'button', class: className, 'data-help': help, 'data-copy': kind, text});
  button.addEventListener('click', async () => {
    try {
      const copied = await getText();
      if (copied == null) return;
      await copyToClipboard(copied);
      if (timer) clearTimeout(timer);
      else button.style.minWidth = `${button.offsetWidth}px`;
      button.textContent = RULES_WORDS.copied;
      button.dataset.copied = 'true';
      timer = setTimeout(() => {
        timer = null;
        button.textContent = text;
        button.style.minWidth = '';
        delete button.dataset.copied;
      }, COPIED_MS);
    } catch (error) {
      await dialogs.failed(error, 'Nothing was copied');
    }
  });
  return button;
}

/* What a rule copies, read from the engine every time it is asked for: the number a new revision
 * file takes moves whenever the project takes one (a Commit writes one), so a copy read earlier
 * would number a file with a number already taken, and the Rulebook refuses two files of one
 * number. */
function ruleCopyText(ruleId) {
  return api(`/api/v1/rules/copy?rule=${encodeURIComponent(ruleId)}`);
}

/* How to switch off a rule: a rule is switched off by a file in the project's own rules, which the
 * Dashboard does not write. The route serves the whole file, numbered one past every file the
 * project has (the Rulebook refuses two with one number), and the box shows it and copies exactly
 * what it shows. Nothing here changes the project. */
async function switchOffDialog(rule) {
  const copied = await ruleCopyText(rule.rule_id);
  if (!copied.switch_off) {
    await dialogs.tell({title: `${rule.rule_id} stays on`,
      text: 'This rule is provisional. It stays in force until a person confirms it, and a change cannot switch it off.'});
    return;
  }
  if (!copied.file_text) {
    await dialogs.tell({title: 'The file cannot be numbered',
      text: 'Ingence could not read your project’s own rules, so it does not know the next free number. '
        + 'Fix them, then open Rules in Advanced again.'});
    return;
  }
  await dialogs.open({title: `How to switch off ${rule.rule_id}`,
    text: `Save this as ${copied.file_name} in your project’s rulebook folder. The next scan reads it `
      + 'and switches the rule off. Nothing changes until you save it.',
    className: 'rules-switch-off',
    build: (finish) => [
      el('pre', {class: 'rules-yaml', text: copied.file_text}),
      el('p', {class: 'actions'}, [
        el('button', {type: 'button', text: 'Close', onclick: () => finish(null)}),
        copyChip({text: 'Copy the file', help: 'rules.copy', kind: 'switch_off', className: 'primary',
          getText: async () => copied.file_text}),
      ]),
    ]});
}

/* ------------------------------------------------------------------ the card */

/* A rule named on another page: its name and version, its plain words on hover, and a click
 * that opens it on Engine > Rules. */
function ruleHandle(ruleId, {version = null, plain = ''} = {}) {
  return el('button', {class: 'link rule-link', type: 'button', 'data-help': 'rules.link',
    title: plain || `Open ${ruleId} in Rules`,
    text: version ? `${ruleId}, version ${version}` : ruleId,
    onclick: (event) => { event.stopPropagation(); openRule(ruleId).catch(reportError); }});
}

/* Another page's way to a rule: the page is drawn with no search, the rule's group open and its
 * card chosen, and the row is scrolled into view and marked as arrived at. */
async function openRule(ruleId) {
  rulesView.query = '';
  rulesView.reveal = ruleId;
  await navigate('rules');
}

/* The rule's status, in words: on, or off and who or what switched it off. */
function ruleForce(row) {
  if (!row.off) return 'in force';
  if (row.kind === 'person') return RULES_WORDS.switchedOff;
  if (row.kind === 'replaced') return String(row.rule.reason || 'replaced by a newer rule');
  return RULES_WORDS.switchedOff;
}

function ruleCard(row, model) {
  const rule = row.rule;
  const handle = [rule.rule_id, rule.version ? `version ${rule.version}` : null, ruleForce(row)]
    .filter(Boolean).join(' · ');
  const written = rule.plain_from === 'statement' || !rule.statement ? null : rule.statement;
  const defeaters = (rule.defeated_by || []);
  const parameters = rule.parameters || [];
  const facts = [
    ['Decides', rule.decides],
    ['Reads', rule.reads],
    ['This scan', row.off && !rule.in_this_run && row.kind !== 'replaced' ? RULES_WORDS.switchedOff
      : `${restsOn(rule)}${row.off && rule.in_this_run ? ' (the scan shown still used it)' : ''}`],
  ].filter(([, said]) => said);
  const list = el('dl', {class: 'rule-facts'});
  for (const [name, said] of facts) list.append(el('dt', {text: name}), el('dd', {text: said}));
  if (defeaters.length) {
    list.append(el('dt', {text: 'Exceptions'}), el('dd', {}, defeaters.flatMap((id, place) => [
      place ? ', ' : null,
      ruleHandle(id, {plain: model.byId.get(id) ? model.byId.get(id).plain : ''}),
    ])));
  }
  const alreadyOff = row.off;
  return el('div', {class: 'card rule-card', 'data-rule': rule.rule_id}, [
    el('p', {class: 'eyebrow', text: 'Rule'}),
    el('h3', {class: 'rule-card-sentence', text: rule.plain}),
    el('p', {class: 'card-id', text: handle}),
    written ? el('p', {class: 'rule-statement', text: written}) : null,
    list,
    el('p', {class: 'eyebrow rule-card-settings', text: 'Settings'}),
    parameters.length ? el('div', {class: 'rule-settings'}, parameters.map((each) => ruleSetting(rule, each)))
      : el('p', {class: 'faint', text: 'This rule has no settings.'}),
    el('div', {class: 'rules-chips'}, [
      copyRevisionChip(rule, row.kind === 'replaced'),
      el('button', {type: 'button', class: 'rules-chip', 'data-help': 'rules.switch_off',
        text: 'Switch off…', ...(alreadyOff ? {disabled: '', title: 'This rule is already switched off.'} : {}),
        onclick: () => switchOffDialog(rule).catch(reportError)}),
      el('button', {type: 'button', class: 'rules-chip', 'data-help': 'rules.conclusions',
        text: 'Its results', ...conclusionsOf(rule),
        onclick: () => showConclusionsOf(rule.rule_id)}),
    ]),
    row.kind === 'replaced' ? el('p', {class: 'faint rules-copy-why', text: RULES_WORDS.replaced}) : null,
    el('p', {class: 'faint rules-waits', text: RULES_WORDS.offFile}),
  ]);
}

/* "Copy as a revision". A rule a newer one replaced offers none: the Rulebook refuses any change
 * to a rule that is not in force, and a refused file makes all the project's rules unreadable. The
 * button is there, disabled, with the reason beside it, and C does nothing. */
function copyRevisionChip(rule, replaced) {
  const chip = copyChip({text: RULES_WORDS.copyRevision, help: 'rules.copy', kind: 'revision',
    getText: async () => {
      const found = await ruleCopyText(rule.rule_id);
      if (!found.revision) {
        await dialogs.tell({title: `${rule.rule_id} cannot be changed`,
          text: found.replaced ? RULES_WORDS.replaced
            : 'This rule is provisional. It stays in force as shipped until a person confirms it, '
              + 'and a revision cannot change it.'});
        return null;
      }
      return found.revision;
    }});
  if (replaced) {
    chip.disabled = true;
    chip.title = RULES_WORDS.replaced;
  }
  return chip;
}

/* Whether the Questions page can show a rule's conclusions: it samples what a rule concluded about
 * files; a rule that concluded nothing, and the walk's own facts, have none there. */
function conclusionsOf(rule) {
  if (!rule.claims) return {disabled: '', title: 'This rule found nothing in this scan.'};
  if (rule.effect === 'enumerates') {
    return {disabled: '', title: 'Ingence records these as facts when it lists the files. The Questions page does not sample them.'};
  }
  return {};
}

/* "Its conclusions": the Questions page's Conclusion samples, on this rule alone. */
function showConclusionsOf(ruleId) {
  if (typeof sheetState === 'undefined') return;
  Object.assign(sheetState, {kind: 'claims', at: null, top: '', rule: ruleId, showAnswered: true,
    offset: 0, shownOrder: []});
  sheetState.chosen.clear();
  navigate('claims');
}

/* ------------------------------------------------------------------ the page */

/* The page's own plan: groups in the order the engine applies them, each with its rows; a rule
 * switched off stays in its group; a rule replaced by a newer one is listed apart. */
function rulesModel(data) {
  const groups = [];
  const byKey = new Map();
  const place = (key, heading) => {
    if (!byKey.has(key)) {
      const made = {key, heading, rows: []};
      byKey.set(key, made);
      groups.push(made);
    }
    return byKey.get(key);
  };
  const rows = [];
  for (const section of data.sections || []) {
    const group = place(section.effect, section.heading);
    for (const rule of section.rules) {
      const row = {rule, group, off: false, kind: ''};
      group.rows.push(row);
      rows.push(row);
    }
  }
  const replaced = [];
  for (const rule of data.inactive || []) {
    const kind = rule.off_kind || 'shipped';
    if (kind === 'replaced' || !rule.effect) {
      replaced.push({rule, group: null, off: true, kind: 'replaced'});
      continue;
    }
    const group = place(rule.effect, rule.heading || rule.effect);
    const row = {rule, group, off: true, kind};
    group.rows.push(row);
    rows.push(row);
  }
  const byId = new Map([...rows, ...replaced].map((row) => [row.rule.rule_id, row.rule]));
  return {groups, rows, replaced, byId, ran: rows.filter((row) => ruleRests(row.rule) > 0).length};
}

/* Each typed word must appear in the rule's name, its plain sentence, its statement or its
 * group's heading, in any case. */
function ruleSearchWords(text) {
  return String(text || '').toLocaleLowerCase().split(/\s+/).filter(Boolean);
}

function ruleAnswers(words, rule, heading) {
  if (!words.length) return true;
  const held = [rule.rule_id, rule.plain, rule.statement, heading]
    .filter(Boolean).join('\n').toLocaleLowerCase();
  return words.every((word) => held.includes(word));
}

/* The row of one rule: four cells, reached by Tab, chosen with a click or Enter. */
function ruleRow(row) {
  const rule = row.rule;
  const total = ruleRests(rule);
  const runCell = row.kind === 'replaced' || (row.off && !rule.in_this_run)
    ? el('td', {class: 'rules-run soft', text: row.kind === 'replaced' ? String(rule.reason || 'replaced') : RULES_WORDS.switchedOff})
    : (total ? el('td', {class: 'rules-run mono', title: restsOn(rule), text: rulesCount(total)})
      : el('td', {class: 'rules-run soft', text: RULES_WORDS.nothing}));
  const node = el('tr', {class: `rules-row${row.off ? ' off' : ''}`, id: `rule-${rule.rule_id}`,
    'data-rule': rule.rule_id, tabindex: '0'}, [
    el('td', {class: 'rule-plain', text: rule.plain}),
    runCell,
    el('td', {class: 'rules-name'}, [el('code', {class: 'mono', text: `${rule.rule_id}${rule.version ? ` · v${rule.version}` : ''}`})]),
    el('td', {class: `rules-force${row.off ? (row.kind === 'person' ? ' person' : ' off') : ''}`,
      text: row.off ? 'off' : 'on'}),
  ]);
  row.node = node;
  return node;
}

function rulesTable(rows) {
  return el('table', {class: 'rules-table'}, [
    el('thead', {}, [el('tr', {}, [
      el('th', {class: 'rules-plain-head', text: 'The rule, in plain words'}),
      el('th', {class: 'rules-run-head', text: 'This scan'}),
      el('th', {text: 'Name · version'}),
      el('th', {text: 'In force'}),
    ])]),
    el('tbody', {}, rows.map(ruleRow)),
  ]);
}

async function showRules() {
  states.loading(listPane, 'Loading the rules...');
  const turn = turns.claim(listPane);
  const data = await api('/api/rules');
  if (!turns.owns(listPane, turn) || state.view !== 'rules') return;
  const notes = [
    data.revisions_unreadable ? el('p', {class: 'note refused', text: 'Ingence could not read your '
      + `project’s rules, so it shows only this scan’s values: ${data.revisions_unreadable}`}) : null,
    data.older_rules_file ? el('p', {class: 'note', text: data.older_rules_file}) : null,
  ];
  if (!data.rules && !(data.inactive || []).length) {
    fill(listPane, el('h2', {class: 'eyebrow', text: 'Rules'}), ...notes,
      states.say(null, {dot: 'empty', sentence: 'Ingence has not applied the rules to this project yet.',
        body: 'The rules appear here after the first scan.', into: true}));
    states.empty(detailPane, 'Every rule is listed on the left.');
    return;
  }
  const model = rulesModel(data);
  rulesView.model = model;
  const run = data.run && data.run.number ? `Scan ${data.run.number}` : 'this scan';
  const title = `${rulesPlural(model.rows.length, 'rule')} in ${rulesPlural(model.groups.length, 'group')}`
    + ` · ${rulesCount(model.ran)} applied in ${run}`;

  /* Which groups open: those the person left open; the first time, the group with the most
   * conclusions (the page opens on what it has most to say about). */
  if (!rulesView.open) {
    const sums = model.groups.map((group) => [group, group.rows.reduce((sum, row) => sum + ruleRests(row.rule), 0)]);
    sums.sort((a, b) => b[1] - a[1]);
    rulesView.open = new Set(sums.length ? [sums[0][0].key] : []);
  }

  const finder = el('input', {type: 'search', class: 'rules-search', autocomplete: 'off',
    'data-help': 'rules.search', 'aria-label': 'Find a rule', placeholder: RULES_WORDS.searchPlaceholder,
    value: rulesView.query});
  const counter = el('span', {class: 'rules-count', role: 'status', 'aria-live': 'polite'});
  const none = el('p', {class: 'faint rules-search-note', role: 'status'});

  for (const group of model.groups) {
    const head = el('button', {type: 'button', class: 'rules-group-head', 'data-help': 'rules.group',
      'aria-expanded': 'false', 'aria-controls': `rules-body-${group.key}`}, [
      el('span', {class: 'rules-twist', 'aria-hidden': 'true'}),
      el('span', {class: 'rules-group-name', text: group.heading}),
      el('span', {class: 'rules-group-counts'}),
    ]);
    const body = el('div', {class: 'rules-group-body', id: `rules-body-${group.key}`}, [rulesTable(group.rows)]);
    group.head = head;
    group.body = body;
    group.box = el('section', {class: 'rules-group', 'data-group': group.key}, [
      el('h3', {class: 'rules-group-title'}, [head]), body]);
    head.addEventListener('click', () => {
      if (rulesView.open.has(group.key)) rulesView.open.delete(group.key);
      else rulesView.open.add(group.key);
      refresh();
    });
  }
  const replacedTitle = el('span', {class: 'rules-replaced-title'});
  const replacedBox = model.replaced.length ? el('details', {class: 'rules-inactive', 'data-help': 'rules.inactive'}, [
    el('summary', {}, [replacedTitle]), rulesTable(model.replaced)]) : null;

  /* Every row's visibility, every header's words and the count, from the typed search. */
  function refresh() {
    const words = ruleSearchWords(rulesView.query);
    let kept = 0;
    for (const group of model.groups) {
      let here = 0;
      for (const row of group.rows) {
        const answers = ruleAnswers(words, row.rule, group.heading);
        row.node.hidden = !answers;
        if (answers) here += 1;
      }
      kept += here;
      /* A search opens the groups that answer it, and leaves out the rest. */
      if (words.length && here) rulesView.open.add(group.key);
      group.box.hidden = words.length > 0 && here === 0;
      const open = rulesView.open.has(group.key);
      group.head.setAttribute('aria-expanded', open ? 'true' : 'false');
      group.box.classList.toggle('open', open);
      group.body.hidden = !open;
      group.head.querySelector('.rules-group-counts').textContent = `${words.length
        ? `${rulesCount(here)} of ${rulesPlural(group.rows.length, 'rule')}`
        : rulesPlural(group.rows.length, 'rule')} · ${groupRests(group.rows)}`;
    }
    if (replacedBox) {
      let here = 0;
      for (const row of model.replaced) {
        const answers = ruleAnswers(words, row.rule, 'Replaced');
        row.node.hidden = !answers;
        if (answers) here += 1;
      }
      replacedBox.hidden = words.length > 0 && here === 0;
      if (words.length && here) replacedBox.open = true;
      replacedTitle.textContent = words.length
        ? `Replaced by newer rules (${here} of ${model.replaced.length})`
        : `Replaced by newer rules (${model.replaced.length})`;
    }
    counter.textContent = `${rulesCount(kept)} of ${rulesCount(model.rows.length)}`;
    none.textContent = words.length && !kept ? 'No rule matches your search.' : '';
  }

  finder.addEventListener('input', () => { rulesView.query = finder.value; refresh(); });
  const everyone = (open) => {
    rulesView.open = new Set(open ? model.groups.map((group) => group.key) : []);
    if (replacedBox) replacedBox.open = open;
    refresh();
  };
  const groupsBox = el('div', {class: 'rules-groups'}, model.groups.map((group) => group.box));
  const tableBox = el('div', {class: 'rules-tables'}, [groupsBox, replacedBox]);
  const everyRow = [...model.rows, ...model.replaced];
  const rowOf = (node) => {
    const found = node instanceof Element ? node.closest('tr.rules-row') : null;
    return found ? everyRow.find((each) => each.rule.rule_id === found.dataset.rule) : undefined;
  };

  /* The rows are reached with the arrow keys as well as Tab; Enter opens a row's card. A group's
   * header is a button, so Enter and Space fold it. */
  tableBox.addEventListener('keydown', (event) => {
    const row = rowOf(event.target);
    if (row && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      choose(row, {});
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const stops = [...tableBox.querySelectorAll('button.rules-group-head, tr.rules-row')]
      .filter((node) => !node.closest('[hidden]'));
    const at = stops.indexOf(document.activeElement);
    if (at < 0) return;
    const next = stops[at + (event.key === 'ArrowDown' ? 1 : -1)];
    if (next) {
      event.preventDefault();
      next.focus();
    }
  });
  tableBox.addEventListener('click', (event) => {
    const row = rowOf(event.target);
    if (row) choose(row, {});
  });

  /* Choosing a rule marks its row and draws its card beside the table. */
  function choose(row, {scroll = false} = {}) {
    if (!row) return;
    rulesView.chosen = row.rule.rule_id;
    for (const each of everyRow) {
      if (each.node) {
        const here = each === row;
        each.node.classList.toggle('selected', here);
        if (here) each.node.setAttribute('aria-current', 'true');
        else each.node.removeAttribute('aria-current');
      }
    }
    fill(detailPane, ruleCard(row, model));
    detailPane.dataset.rule = row.rule.rule_id;
    detailPane.classList.add('rules-pane');
    keepCardInView();
    if (scroll) row.node.scrollIntoView({block: 'center'});
  }

  fill(listPane,
    el('h2', {class: 'eyebrow', text: 'Rules'}),
    el('p', {class: 'rules-title', text: title}),
    ...notes,
    el('div', {class: 'rules-find'}, [finder, counter]),
    el('p', {class: 'faint rules-help'}, [
      `${RULES_WORDS.searchHelp} Choose a row to read its card · `,
      el('button', {type: 'button', class: 'link', 'data-help': 'rules.expand', text: 'Expand all',
        onclick: () => everyone(true)}), ' · ',
      el('button', {type: 'button', class: 'link', 'data-help': 'rules.expand', text: 'Collapse all',
        onclick: () => everyone(false)}),
    ]),
    none,
    tableBox,
  );

  /* A rule another page asked for is shown: its group opens, its row is chosen and marked. */
  const wanted = rulesView.reveal;
  rulesView.reveal = '';
  const target = wanted && everyRow.find((each) => each.rule.rule_id === wanted);
  if (target) {
    if (target.group) rulesView.open.add(target.group.key);
    else if (replacedBox) replacedBox.open = true;
  }
  refresh();
  const first = model.rows.find((row) => !row.node.hidden && !row.node.closest('[hidden]'));
  const chosen = target || everyRow.find((each) => each.rule.rule_id === rulesView.chosen) || first;
  if (chosen) {
    choose(chosen, {scroll: Boolean(target)});
    if (target) target.node.classList.add('arrived');
  } else {
    states.say(detailPane, {eyebrow: 'Rule', dot: 'empty', sentence: 'Choose a rule to read its card.'});
  }
}

/* The card stays in view while a long table is read: it sticks just under the header, whose height
 * changes as its notices come and go, so the top is set from the header as it is now. */
function keepCardInView() {
  const header = document.querySelector('body > header');
  const top = header ? Math.ceil(header.getBoundingClientRect().height) : 0;
  detailPane.style.setProperty('--rules-card-top', `${top}px`);
}

window.addEventListener('resize', () => { if (state.view === 'rules') keepCardInView(); });
onLeave((next) => {
  if (next !== 'rules') {
    detailPane.classList.remove('rules-pane');
    delete detailPane.dataset.rule;
  }
});

/* C copies the open card's revision text (the keyboard table, Engine > Rules). Never while a field
 * has focus, and only on this page. */
document.addEventListener('keydown', (event) => {
  if (state.view !== 'rules' || event.defaultPrevented) return;
  if (event.key.toLowerCase() !== 'c' || event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target;
  if (target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"], dialog')) return;
  const button = detailPane.querySelector('.rules-chip[data-copy="revision"]');
  if (button instanceof HTMLButtonElement && !button.disabled) {
    event.preventDefault();
    button.click();
  }
});

VIEWS.rules = showRules;
