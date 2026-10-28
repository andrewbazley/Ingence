/* The Settings section of Advanced (Engine).
 *
 * Loaded after workspace.js, whose drafts, yearsControl and canonicalValue it uses, and app.js,
 * whose el(), api(), fill(), dialogs and ruleHandle it uses. It joins Advanced (Engine) as a
 * section without editing the page's own tables.
 *
 * - Each row shows the setting's name and its purpose in plain words, from the catalog beside the
 *   settings (``api/settings_catalog.yaml``), grouped by section under a plain heading.
 * - The value is an editable field pre-filled with the value in force, of the kind the value takes:
 *   a switch, a choice, a number (left empty where the setting takes none), words, or a comma list.
 *   Editing makes a draft as every field does (workspace.js). Nothing is written to
 *   ``settings.yaml``.
 * - A value that differs from its default is marked changed, says the default on hover, and offers
 *   to go back to it. When the value is put back, the commit takes it out of the Dashboard's file.
 * - "Comes from" names the file and opens its text (``/api/workspace/settings-file/<name>``). A
 *   default comes from nowhere and says so.
 * - A setting that a rule reads links the rule: its plain words on hover, and a click that opens
 *   Engine > Rules at it. */

/* A value as a person reads it. */
function settingWords(value) {
  if (value === null || value === undefined) return 'none';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'nothing';
  return String(value);
}

function settingInput(row) {
  const shown = row.drafted ? row.draft : row.value;
  const attributes = {'data-draft': `settings:${row.name}`, 'data-original': canonicalValue(row.value),
    'aria-label': row.name, 'data-help': 'settings.value'};
  let input;
  if (row.kind === 'boolean') {
    input = el('input', {...attributes, type: 'checkbox', role: 'switch'});
    input.checked = Boolean(shown);
  } else if (row.kind === 'choice') {
    /* Each choice in words saying what it does, the stored value on hover. */
    const words = row.choice_words || {};
    input = el('select', {...attributes, 'data-draft-type': 'json'}, row.choices.map((choice) =>
      el('option', {value: JSON.stringify(choice), text: words[JSON.stringify(choice)] || settingWords(choice),
        title: `written as ${choice === null ? 'nothing' : String(choice)}`})));
    input.value = JSON.stringify(shown === undefined ? null : shown);
  } else if (row.kind === 'number') {
    input = el('input', {...attributes, type: 'number', step: 'any',
      ...(row.nullable ? {placeholder: 'none'} : {required: ''})});
    input.value = shown == null ? '' : String(shown);
  } else if (row.kind === 'list') {
    input = el('input', {...attributes, type: 'text', class: 'comma-list',
      'data-draft-type': 'comma', 'data-list-of': row.list_of || 'mixed',
      placeholder: 'nothing; separate with commas'});
    input.value = (shown || []).join(', ');
  } else {
    input = el('input', {...attributes, type: 'text'});
    input.value = shown == null ? '' : String(shown);
  }
  if (row.drafted) input.dataset.changed = '1';
  return input;
}

/* Put a field back to its default: the field changes as if typed, so the draft follows. */
function backToDefault(input, row) {
  if (row.kind === 'boolean') input.checked = Boolean(row.default);
  else if (row.kind === 'choice') input.value = JSON.stringify(row.default);
  else if (row.kind === 'list') input.value = (row.default || []).join(', ');
  else input.value = row.default == null ? '' : String(row.default);
  input.dispatchEvent(new Event('input', {bubbles: true}));
}

/* A settings file's text, in a dialog: what "comes from" names. */
async function showSettingsFile(name) {
  const shown = await api(`/api/workspace/settings-file/${encodeURIComponent(name)}`);
  await dialogs.open({title: name, className: 'settings-file',
    text: shown.text == null ? 'There is no such file in this project yet.' : shown.path,
    build: (finish) => [
      shown.text == null ? null : el('pre', {class: 'file-text', tabindex: '0', text: shown.text}),
      el('p', {class: 'actions'}, [el('button', {type: 'button', class: 'primary',
        'data-autofocus': '', text: 'Close', onclick: () => finish(null)})]),
    ]});
}

function comesFrom(row) {
  if (!row.source_file) return el('span', {class: 'faint', text: 'default'});
  return el('button', {class: 'link', type: 'button', 'data-help': 'settings.source',
    text: row.source_file, title: `Show ${row.source_file}`,
    onclick: () => showSettingsFile(row.source_file).catch((error) => dialogs.failed(error))});
}

function settingRow(row) {
  const field = row.editable ? settingInput(row) : el('code', {text: settingWords(row.value)});
  const defaultWords = `The default is ${settingWords(row.default)}`;
  const changed = row.changed ? el('span', {class: 'changed-mark'}, [
    el('span', {class: 'tag changed', title: defaultWords, text: 'changed'}), ' ',
    row.editable ? el('button', {class: 'link', type: 'button', 'data-help': 'settings.default',
      title: defaultWords, text: 'use the default',
      onclick: () => backToDefault(field, row)}) : null,
  ]) : null;
  const rules = (row.rules || []).length ? el('div', {class: 'faint read-by'}, ['Read by ',
    ...row.rules.flatMap((rule, index) => [index ? ', ' : '',
      ruleHandle(rule.rule_id, {version: rule.version, plain: rule.plain})])]) : null;
  return el('tr', {class: row.changed ? 'changed' : '', 'data-setting': row.name}, [
    el('td', {}, [el('code', {text: row.name}),
      row.purpose ? el('div', {class: 'purpose', text: row.purpose}) : null, rules]),
    el('td', {}, [field, changed,
      row.drafted ? el('span', {class: `draft-note${row.error ? ' draft-error' : ''}`,
        text: row.error ? `not valid: ${plainRefusal(row.error)}` : 'draft, saved'}) : null]),
    el('td', {}, [comesFrom(row)]),
  ]);
}

async function showSettings() {
  const {settings} = await api('/api/workspace/settings');
  const years = settings.find((row) => row.name === 'dictionary.years') || null;
  const sections = [];
  // The years have one editor, the block above: their row is not repeated.
  for (const row of settings.filter((each) => !(years && each.name === 'dictionary.years'))) {
    const last = sections[sections.length - 1];
    if (last && last.section === row.section) last.rows.push(row);
    else sections.push({section: row.section, heading: row.section_heading, rows: [row]});
  }
  fill(listPane,
    el('h2', {text: 'Settings'}),
    el('p', {class: 'faint', text: 'Changes are drafts until you commit them.'}),
    years ? yearsControl(years, 'settings') : null,
    ...sections.flatMap((section) => [
      el('h3', {text: section.heading}),
      el('table', {class: 'settings', 'data-section': section.section}, [
        el('thead', {}, [el('tr', {}, ['Setting', 'Value', 'Comes from']
          .map((heading) => el('th', {text: heading})))]),
        el('tbody', {}, section.rows.map(settingRow)),
      ]),
    ]),
  );
}

/* Join Advanced (Engine) as a section, without editing the page's own tables. */
VIEWS.settings = showSettings;
PAGES.engine.push('settings');
PAGE_OF.settings = 'engine';
SINGLE_VIEWS.add('settings');
