/* Import and Export in the header, whose meaning is the page's.
 *
 * On the settings page, Export means export a portable settings file. On Questions, Export lets
 * you export a document. Import works the same way: import settings, import groupings, import
 * histories. There is one pair of buttons beside Commit and Re-run. Each page says what they do
 * there in their tooltips, and where a page has nothing to import or export the button is
 * disabled with its reason.
 *
 * Import asks what the file is and who wrote it. A box shows the file, a list of the kinds it
 * reads (the page's own chosen), and, for a file of answers, a list of who wrote it: the people
 * who have answered in this project and "Agent-generated". An agent's file asks the person to
 * confirm they reviewed it, and each answer is recorded as <their name>_agent. Any other choice
 * records that person, and the event names who imported it where that is not them. Canceling the
 * confirmation writes nothing.
 *
 * - Settings: export a portable settings file (only what somebody chose). Import one as a
 *   preview of each change, taken as drafts the person commits as any other and never written
 *   into settings.yaml. Each unknown name or invalid value is refused in words.
 * - Questions (every section but Groupings): export the questions as one file. Import a filled
 *   answers file, each answer written through the answer route, the Record's one way in.
 * - Groupings: export the group answers and their notes. Import a groupings sheet, read by the
 *   review sheet reader ``ingence record import`` uses, each answer written the same way.
 * - History: export every run and the Record's events. It imports nothing.
 * - Dictionary: export yours. Install a pack from a file.
 *
 * Loaded after app.js, review.js and workspace.js, whose el(), api(), write(), dialogs and
 * askActor it uses. */

(function transfer() {
  const exportButton = document.getElementById('export-button');
  const importButton = document.getElementById('import-button');
  if (!exportButton || !importButton) return;

  /* Save text as a file on this machine, through the browser's own download. */
  function saveAs(name, text, type) {
    const link = el('a', {href: URL.createObjectURL(new Blob([text], {type})), download: name});
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function today() {
    return new Date().toISOString().slice(0, 10);
  }

  function dated(name) {
    const dot = name.lastIndexOf('.');
    return `${name.slice(0, dot)}-${today()}${name.slice(dot)}`;
  }

  /* ------------------------------------------------------------------ settings */

  async function exportSettings() {
    const exported = await api('/api/workspace/settings-export');
    saveAs(dated(exported.file_name), exported.text, 'application/yaml');
  }

  /* A settings file's values beside those in force, in a dialog (the Settings page has no
   * panel beside it); taken as drafts only by the person's click. */
  async function importSettings(text) {
    const preview = await write('/api/workspace/settings-import', {document: text});
    const changes = preview.changes.filter((row) => !row.same);
    const same = preview.changes.length - changes.length;
    const values = {};
    for (const row of changes) {
      let node = values;
      const keys = row.name.split('.');
      for (const key of keys.slice(0, -1)) node = node[key] = node[key] || {};
      node[keys[keys.length - 1]] = row.imported;
    }
    const took = await dialogs.open({title: 'Import settings', className: 'settings-import',
      text: `${changes.length} change${changes.length === 1 ? '' : 's'}`
        + `${same ? `, ${same} the same as now` : ''}`
        + `${preview.refused.length ? `, ${preview.refused.length} refused` : ''}.`,
      cancelled: false,
      build: (finish) => [
        changes.length ? table(['Setting', 'Now', 'Imported'], changes.map((row) => el('tr', {}, [
          el('td', {}, [el('code', {text: row.name})]),
          el('td', {text: settingWords(row.now)}),
          el('td', {}, [el('strong', {text: settingWords(row.imported)})]),
        ]))) : null,
        preview.refused.length ? el('ul', {class: 'ids refused-list'}, preview.refused.map((row) =>
          el('li', {}, [el('code', {text: row.name}), `: ${row.why}`]))) : null,
        el('p', {class: 'actions'}, [
          el('button', {type: 'button', text: changes.length ? 'Cancel' : 'Close',
            'data-autofocus': '', onclick: () => finish(false)}),
          changes.length ? el('button', {type: 'button', class: 'primary', 'data-help': 'transfer.take_settings',
            text: `Take ${changes.length} as drafts`, onclick: () => finish(true)}) : null,
        ]),
      ]});
    if (!took) return;
    const saved = await write('/api/workspace/draft', {kind: 'settings', values});
    await refreshUncommitted();
    if (state.view === 'settings') await showSettings();
    const refused = Object.keys(saved.errors || {}).length;
    if (refused) {
      await dialogs.tell({title: 'Taken as drafts', text: `${refused} of them are not valid. `
        + 'Each one is marked where you edit it.'});
    }
  }

  /* ------------------------------------------------------------------ questions */

  async function exportQuestions() {
    const pack = await api('/api/v1/questions/pack');
    const sure = await dialogs.choose({title: 'Export the questions?',
      text: pack.carries, yes: 'Save the file'});
    if (!sure) return;
    saveAs(`ingence-questions-${today()}.json`, JSON.stringify(pack, null, 2), 'application/json');
  }

  /* ------------------------------------------------------------------ import */

  /* What Import reads. A kind with ``answers`` is written as its author's, so it asks who. */
  const KINDS = {
    answers: {label: 'Answers to the questions', accept: 'application/json,.json', answers: true},
    groupings: {label: 'A table of group answers', accept: '.csv,text/csv', answers: true},
    settings: {label: 'A settings file', accept: '.yaml,.yml,text/yaml,application/yaml'},
    pack: {label: 'A dictionary file', accept: '.yaml,.yml', file: true},
  };
  /* The most the server reads in one write (api/server.py's body limit), said before sending. */
  const LARGEST = 60 * 1024;
  /* The "who wrote it" choice for an agent's file: a value no name can be. */
  const AGENT = ' agent';

  /* The people who have answered here, the person at the page first, then Agent-generated. */
  async function authorOptions() {
    let others = [];
    try {
      others = (await api('/api/v1/authors')).authors || [];
    } catch (error) {
      others = [];
    }
    const mine = me.actor.trim();
    const listed = others.filter((name) => name.toLowerCase() !== mine.toLowerCase());
    return [
      el('option', {value: '', text: mine ? `${mine} (you)` : 'You'}),
      ...listed.map((name) => el('option', {value: name, text: name})),
      el('option', {value: AGENT, text: 'Agent-generated'}),
    ];
  }

  /* The Import box: the file, what it is, and for answers who wrote it. Resolves with the
   * three, or null for Cancel. */
  async function askImport(kind) {
    const options = await authorOptions();
    return dialogs.open({title: 'Import', className: 'import-dialog', build: (finish) => {
      const file = el('input', {type: 'file', id: 'import-file', 'data-autofocus': ''});
      const what = el('select', {id: 'import-kind'}, Object.entries(KINDS).map(([name, held]) =>
        el('option', {value: name, text: held.label})));
      what.value = kind;
      const who = el('select', {id: 'import-author'}, options);
      const whoRow = el('p', {}, [el('label', {for: 'import-author', text: 'Who wrote it'}), who]);
      const problem = el('p', {class: 'dialog-problem', role: 'alert'});
      const fit = () => {
        file.accept = KINDS[what.value].accept;
        whoRow.hidden = !KINDS[what.value].answers;
      };
      what.addEventListener('change', fit);
      fit();
      return [
        el('p', {}, [el('label', {for: 'import-file', text: 'File'}), file]),
        el('p', {}, [el('label', {for: 'import-kind', text: 'What it is'}), what]),
        whoRow,
        problem,
        el('p', {class: 'actions'}, [
          el('button', {type: 'button', text: 'Cancel', onclick: () => finish(null)}),
          el('button', {type: 'button', class: 'primary', text: 'Import', onclick: () => {
            const chosen = file.files && file.files[0];
            const held = KINDS[what.value];
            /* A file of answers is measured by its answers alone, once read (importAnswers). */
            problem.textContent = !chosen ? 'Choose a file first.'
              : !held.file && what.value !== 'answers' && chosen.size > LARGEST
                ? `${chosen.name} is larger than 60 KB, the most `
                + 'the Dashboard reads at once.' + (what.value === 'groupings'
                  ? ' Import a table this size with "ingence record import" in a terminal.' : '')
                : '';
            if (!problem.textContent) finish({file: chosen, kind: what.value, author: who.value});
          }}),
        ]),
      ];
    }});
  }

  /* The answers a returned file holds: the whole export filled in comes back with its questions
   * and evidence too, often more than one write takes, so only its "answers" object is sent. A
   * file that is not JSON is sent as it is, for the server to refuse in words. */
  function answersOf(text) {
    try {
      const read = JSON.parse(text);
      return read && typeof read.answers === 'object' && read.answers ? read.answers : read;
    } catch (error) {
      return text;
    }
  }

  /* A file of answers, written as its author's once the person has said who that is. */
  async function importAnswers(file, kind, author) {
    const document = kind === 'answers' ? answersOf(await file.text()) : await file.text();
    const size = typeof document === 'string' ? document.length : JSON.stringify(document).length;
    if (size > LARGEST) {
      await dialogs.tell({title: 'Too large to import here', text: `The answers in ${file.name} `
        + 'are larger than 60 KB, the most the Dashboard reads at once.' + (kind === 'groupings'
          ? ' Import a table this size with "ingence record import" in a terminal.' : '')});
      return;
    }
    if (!(await askActor())) return;
    const agent = author === AGENT;
    if (agent && !(await dialogs.choose({title: 'Agent-generated answers',
      text: `Confirm that you reviewed every answer. Ingence saves them as ${me.actor}_agent's.`,
      yes: 'I reviewed them'}))) return;
    const imported = await write('/api/review/answer', {actor: me.actor, import: {kind,
      name: file.name, document, written_by: agent ? undefined : author || undefined,
      agent_generated: agent, reviewed: agent}});
    refreshWaiting();
    showImported(imported);
    /* The page drawn again, so what was answered leaves it and the next question opens. */
    navigate(state.view, {push: false});
  }

  /* What an import wrote, and what it refused and why, in a line under the header. The page
   * goes on to its next waiting question as after any answer, and the line stays readable
   * there until hidden or replaced by the next import. */
  const importedLine = document.getElementById('imported');

  function showImported(imported) {
    if (!importedLine) return;
    const written = imported.written.length;
    const refused = imported.refused || [];
    const kept = (imported.kept || []).length;
    fill(importedLine,
      `Imported ${imported.file}: ${written} answer${written === 1 ? '' : 's'} saved as `
        + `${imported.author}'s`
        + `${kept ? `, ${kept} already answered and kept` : ''}`
        + `${imported.blank ? `, ${imported.blank} blank` : ''}`
        + `${refused.length ? `, ${refused.length} not saved` : ''}. `,
      el('button', {class: 'link', type: 'button', text: 'hide', 'data-help': 'transfer.done',
        onclick: () => { importedLine.hidden = true; }}),
      refused.length ? el('details', {}, [el('summary', {text: 'why'}),
        el('ul', {class: 'ids refused-list'}, refused.map((row) => el('li', {}, [
          row.target ? el('code', {text: row.target}) : null,
          row.target ? `: ${row.error}` : row.error])))]) : null,
    );
    importedLine.hidden = false;
  }

  /* ------------------------------------------------------------ groupings, history */

  async function exportGroupings() {
    const exported = await api('/api/workspace/export/groupings');
    saveAs(dated(exported.file_name), exported.text, 'text/csv');
  }

  async function exportHistory() {
    const exported = await api('/api/workspace/export/history');
    saveAs(dated(exported.file_name), exported.text, 'application/json');
  }

  /* ------------------------------------------------------------------ by page */

  const QUESTIONS = {
    export: {title: 'Export the questions as one file', run: exportQuestions},
    import: {title: 'Import a file of answers', kind: 'answers'},
  };
  const NOTHING_HERE = {export: 'Nothing on this page to export', import: 'Nothing on this page to import'};

  /* What the two buttons do on each section; a section not named has nothing to import or export. */
  const TRANSFER = {
    settings: {
      export: {title: 'Export this project’s settings file', run: exportSettings},
      import: {title: 'Import a settings file', kind: 'settings'},
    },
    review: QUESTIONS, claims: QUESTIONS, candidates: QUESTIONS, extensions: QUESTIONS,
    programs: QUESTIONS, coverage: QUESTIONS,
    classes: {
      export: {title: 'Export the group answers and their notes as a table', run: exportGroupings},
      import: {title: 'Import a table of group answers', kind: 'groupings'},
    },
    /* The Dictionary's own Export yours and Install from a file. A pack is handed over as the
     * File itself: the pack route reads up to 4 MB, not the 60 KB below. */
    dictionary: {
      export: {title: 'Export yours: your words as a dictionary file',
        run: () => window.ingenceDictionaryExport()},
      import: {title: 'Install dictionary words from a file', kind: 'pack'},
    },
    changes: {
      export: {title: 'Export every scan and every answer in the answer log as one file', run: exportHistory},
      import: 'The answer log grows only by answers. You cannot import a history',
    },
  };

  function forView(view) {
    const held = TRANSFER[view] || {};
    return {export: held.export || NOTHING_HERE.export, import: held.import || NOTHING_HERE.import};
  }

  function show(view) {
    const held = forView(view);
    for (const [button, what] of [[exportButton, held.export], [importButton, held.import]]) {
      const reason = typeof what === 'string' ? what : '';
      button.disabled = Boolean(reason);
      button.title = reason || what.title;
      button.dataset.view = view;
    }
  }

  async function run(action) {
    try {
      await action();
    } catch (error) {
      await dialogs.failed(error, 'Ingence could not do that');
    }
  }

  exportButton.addEventListener('click', () => {
    const held = forView(state.view).export;
    if (typeof held !== 'string') run(held.run);
  });
  importButton.addEventListener('click', () => {
    const held = forView(state.view).import;
    if (typeof held === 'string') return;
    run(async () => {
      const chosen = await askImport(held.kind);
      if (!chosen) return;
      if (chosen.kind === 'settings') await importSettings(await chosen.file.text());
      else if (chosen.kind === 'pack') await installPack(chosen.file);
      else await importAnswers(chosen.file, chosen.kind, chosen.author);
    });
  });

  onLeave((next) => show(next));
  document.addEventListener('DOMContentLoaded', () => show(state.view));
  show(state.view);
})();
