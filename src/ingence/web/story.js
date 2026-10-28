/* The experiment's story and the workflow card.
 *
 * The story (``window.ingenceStory.render(container, experimentId, options)``) shows one experiment
 * day by day, from ``GET /api/v1/experiments/story?id=``. Each day says its stage, how many files
 * were written and their types ("2024-03-15 Raw: 12 ND2 files"). A strip of small boxes shows the
 * same days at once. The workflow the experiment follows says how far its files got, in words and
 * as steps. Its repeats ("Repeated 3 times") and what it was made from are links. Experiments (a
 * row opened), Overlap's Connections (a node opened) and Overlap's By year (an experiment opened)
 * call the same component, each with its own way to open another experiment.
 *
 * The workflow card (``window.ingenceWorkflows.card(container, options)``) shows each workflow from
 * ``GET /api/v1/workflows`` in one line ("Suggested workflow: ND2, then TIFF, then CSV, in 21
 * experiments"), with "Change" and "Turn off". Both first show what would change: they post with
 * ``dry_run: true`` and show the engine's summary, and only the confirm button posts without it.
 * Nothing runs by itself. A change waits for Re-run, as every answer does.
 *
 * Loaded after app.js (el, api, write, states, navigate) and review.js (fill, me, askActor). No
 * framework, no build step. Every number shown is one the engine served. */

(function storyModule() {
  'use strict';

  const fmt = (n) => Number(n || 0).toLocaleString('en-US');
  const plural = (n, one, many) => `${fmt(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;

  /* A stage as a person reads it. The engine's stage names are words of its own (``acquisition``);
   * a stage this table lacks is shown with its underscores as spaces, its first letter capital. */
  const STAGE_WORDS = {
    acquisition: 'Raw', raw: 'Raw', processed: 'Processed', processing: 'Processed',
    analysis: 'Analysis', reanalysis: 'Re-analysis', re_analysis: 'Re-analysis',
    figure: 'Figures', figures: 'Figures', summary: 'Summary', plate: 'Plate', unstated: 'Stage not stated',
  };
  /* The stage's color class on the day strip: raw, made from raw, analysis, figures. */
  const STAGE_CLASS = {
    acquisition: 'raw', raw: 'raw', plate: 'raw', processed: 'made', processing: 'made',
    analysis: 'analysis', reanalysis: 'analysis', re_analysis: 'analysis', summary: 'analysis',
    figure: 'figures', figures: 'figures',
  };
  function stageWord(stage) {
    const said = STAGE_WORDS[stage];
    if (said) return said;
    const text = String(stage || '').replace(/_/g, ' ').trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : 'Stage not stated';
  }
  /* A file type as a scientist names it: the short name in capitals (ND2, TIFF, CSV). */
  function typeWord(type) {
    return String(type || '').replace(/^\./, '').toUpperCase();
  }
  /* "ND2", "CSV and PY", "CSV, XLSX and PY". */
  function listWords(items, joiner = 'and') {
    const shown = items.filter(Boolean);
    if (shown.length <= 1) return shown.join('');
    return `${shown.slice(0, -1).join(', ')} ${joiner} ${shown[shown.length - 1]}`;
  }
  /* One day in words: "Raw: 48 ND2 files". */
  function dayWords(day) {
    const types = listWords((day.types || []).map(typeWord));
    return `${stageWord(day.stage)}: ${fmt(day.files)} ${types ? `${types} ` : ''}${Number(day.files) === 1 ? 'file' : 'files'}`;
  }
  /* How many times an experiment was done: itself and each repeat. */
  function repeatWords(repeats) {
    const times = (repeats || []).length + 1;
    return times === 1 ? 'Seen once' : `Repeated ${fmt(times)} times`;
  }
  /* The workflow's progress in words, from its stages and the last one reached. */
  function progressWords(workflow) {
    const expected = (workflow && workflow.expected) || [];
    if (!workflow || !workflow.id || !expected.length) return 'Ingence knows no workflow for this experiment.';
    const at = workflow.reached ? expected.indexOf(workflow.reached) : -1;
    if (at < 0) return `Its files reached no stage of this workflow yet: ${listWords(expected.map(stageWord), 'then')}.`;
    return `Its files reached ${stageWord(workflow.reached)}, stage ${at + 1} of ${expected.length}.`;
  }

  /* ------------------------------------------------------------------ the names of experiments */

  /* An experiment's name, for a link: the caller's (it has its rows), else the entry's own name,
   * read once. Until it is read, the link shows the identifier. */
  const namesRead = new Map();
  function nameInto(button, id, given) {
    const known = given ? given(id) : '';
    if (known) { button.textContent = known; return; }
    if (namesRead.has(id)) { button.textContent = namesRead.get(id); return; }
    api(`/api/v1/entry?${new URLSearchParams({id})}`).then((found) => {
      const name = (found && found.entry && (found.entry.name || found.entry.path)) || '';
      if (name) { namesRead.set(id, name); button.textContent = name; }
    }).catch(() => {});
  }
  function experimentLink(id, options, help) {
    const button = el('button', {type: 'button', class: 'link st-link', 'data-experiment': id, 'data-help': help,
      onclick: () => { if (options.onOpen) options.onOpen(id); }}, [id]);
    nameInto(button, id, options.name);
    return button;
  }

  /* ------------------------------------------------------------------ the story */

  /* The story of each experiment, read once per run: a page redraws often (a row chosen, a filter
   * changed) and the story must not flash back to "Reading..." each time. */
  const stories = new Map();
  function readStory(id) {
    if (!stories.has(id)) {
      const asked = api(`/api/v1/experiments/story?${new URLSearchParams({id})}`);
      stories.set(id, asked);
      asked.catch(() => stories.delete(id));
    }
    return stories.get(id);
  }

  /* The strip: one small box per day, colored by its stage, in date order. */
  function dayStrip(days) {
    return el('div', {class: 'st-strip', role: 'img', 'data-help': 'story.days',
      'aria-label': `${plural(days.length, 'day')}: ${days.map((day) => `${day.date} ${stageWord(day.stage)}`).join(', ')}`},
    days.map((day) => el('span', {class: `st-day st-${STAGE_CLASS[day.stage] || 'other'}`,
      title: `${day.date} ${dayWords(day)}`})));
  }

  /* The days as a list: the first 12, and the rest behind one disclosure. */
  const DAYS_SHOWN = 12;
  function dayList(days) {
    const item = (day) => el('li', {class: 'st-dayline'}, [
      el('time', {class: 'mono', datetime: day.date, text: day.date}), ' ',
      el('span', {class: `st-swatch st-${STAGE_CLASS[day.stage] || 'other'}`, 'aria-hidden': 'true'}),
      el('span', {text: dayWords(day)})]);
    const list = el('ol', {class: 'st-days'}, days.slice(0, DAYS_SHOWN).map(item));
    if (days.length <= DAYS_SHOWN) return list;
    return el('div', {}, [list, el('details', {class: 'st-more'}, [
      el('summary', {text: `${plural(days.length - DAYS_SHOWN, 'more day')}`}),
      el('ol', {class: 'st-days', start: String(DAYS_SHOWN + 1)}, days.slice(DAYS_SHOWN).map(item))])]);
  }

  /* The workflow's stages as steps, those reached filled. */
  function workflowSteps(workflow) {
    const expected = workflow.expected || [];
    const at = workflow.reached ? expected.indexOf(workflow.reached) : -1;
    return el('ol', {class: 'st-steps', 'aria-label': 'Workflow stages'}, expected.map((stage, index) =>
      el('li', {class: index <= at ? 'st-step reached' : 'st-step', text: stageWord(stage)})));
  }

  /**
   * Draw one experiment's story into ``container``.
   * Options: ``name(id)`` an experiment's name where the caller knows it; ``onOpen(id)`` opens
   * another experiment (a repeat, what it was made from); ``onFiles()`` lists this one's files;
   * ``heading`` false leaves out the "Story" heading.
   */
  async function render(container, experimentId, options = {}) {
    if (!container) return;
    const turn = turns.claim(container);
    if (!stories.has(experimentId)) states.loading(container, 'Reading the story of this experiment...');
    let story;
    try {
      story = await readStory(experimentId);
    } catch (error) {
      if (turns.owns(container, turn)) states.failed(container, error, () => { render(container, experimentId, options); });
      return;
    }
    if (!turns.owns(container, turn) || !container.isConnected) return;
    const days = story.days || [];
    const parts = [];
    if (options.heading !== false) parts.push(el('h3', {class: 'st-head', text: 'Story'}));
    if (days.length) {
      parts.push(dayStrip(days), dayList(days));
    } else {
      parts.push(el('p', {class: 'nothing', text: 'Ingence found no dated files in this experiment.'}));
    }
    // The workflow: its progress in words and as steps, then its card with Change and Turn off.
    const workflow = story.workflow || {id: null, reached: null, expected: []};
    const flow = el('div', {class: 'st-block st-workflow', 'data-help': 'story.workflow'}, [
      el('h4', {class: 'st-eyebrow', text: 'Workflow'}),
      el('p', {class: 'st-progress', text: progressWords(workflow)}),
      workflow.id && (workflow.expected || []).length ? workflowSteps(workflow) : null,
    ]);
    if (workflow.id && window.ingenceWorkflows) {
      const cardBox = el('div', {class: 'st-workflow-card'});
      flow.append(cardBox);
      window.ingenceWorkflows.card(cardBox, {only: workflow.id}).catch(() => {});
    }
    parts.push(flow);
    // Repeats, each a link; and what it was made from. A repeat is {experiment_id, label}, as a
    // source is: the link opens the experiment and says its label.
    const repeats = (story.repeats || []).filter((item) => item && item.experiment_id);
    const labelled = {...options, name: (id) => {
      const repeat = repeats.find((item) => item.experiment_id === id);
      return (repeat && repeat.label) || (options.name ? options.name(id) : '');
    }};
    parts.push(el('div', {class: 'st-block st-repeats', 'data-help': 'story.repeats'}, [
      el('h4', {class: 'st-eyebrow', text: 'Repeats'}),
      el('p', {class: 'st-repeat-count', text: repeatWords(repeats)}),
      repeats.length ? el('ul', {class: 'st-links'}, repeats.map((item) => el('li', {}, [experimentLink(item.experiment_id, labelled, 'story.repeat')]))) : null,
    ]));
    const madeFrom = (story.made_from || []).map((item) => item.experiment_id).filter(Boolean);
    if (madeFrom.length) {
      parts.push(el('div', {class: 'st-block st-made-from', 'data-help': 'story.made_from'}, [
        el('h4', {class: 'st-eyebrow', text: 'Made from'}),
        el('ul', {class: 'st-links'}, madeFrom.map((id) => el('li', {}, [experimentLink(id, options, 'story.made_from')]))),
      ]));
    }
    if (options.onFiles) {
      parts.push(el('p', {class: 'st-files'}, [el('button', {type: 'button', class: 'answer quiet', 'data-help': 'story.files',
        onclick: () => options.onFiles()}, [el('strong', {text: 'Show the files'})])]));
    }
    fill(container, el('div', {class: 'st-story', 'data-story': experimentId}, parts));
  }

  /* ------------------------------------------------------------------ the workflow card */

  let workflowsHeld = null;   // GET /api/v1/workflows, read once per run
  let typesHeld = null;       // the project's file types, for Change
  function readWorkflows({force = false} = {}) {
    if (force || !workflowsHeld) {
      workflowsHeld = api('/api/v1/workflows');
      workflowsHeld.catch(() => { workflowsHeld = null; });
    }
    return workflowsHeld;
  }
  function readTypes() {
    if (!typesHeld) {
      typesHeld = api('/api/v1/overview').then((overview) => (overview.file_types || []).map((type) => String(type.type_id)))
        .catch(() => []);
    }
    return typesHeld;
  }

  /* A stage's types in words: "ND2 or TIFF". */
  const stageTypes = (stage) => listWords((stage.types || []).map(typeWord), 'or') || stageWord(stage.stage);
  /* One workflow in one sentence: "Suggested workflow: ND2, then TIFF, then CSV, in 21 experiments." */
  function workflowSentence(workflow) {
    const kind = workflow.origin === 'declared' ? 'Your workflow' : 'Suggested workflow';
    const steps = (workflow.stages || []).map(stageTypes).join(', then ');
    return `${kind}: ${steps}, in ${plural(workflow.evidence ? workflow.evidence.experiments : 0, 'experiment')}.`;
  }
  /* What a dry run says would change. */
  function changeWords(answer) {
    const would = (answer && answer.what_would_change) || {};
    const counts = `${plural(would.stages_changed, 'stage')}, ${plural(would.files, 'file')} and ${plural(would.experiments, 'experiment')} would change.`;
    return [counts, would.said].filter(Boolean);
  }
  /* After a confirmed change: what the engine said, and, where it recorded the change, that it
   * waits for Re-run. */
  function doneWords(answer) {
    return [answer && answer.said, answer && answer.recorded ? 'This change waits for Re-run.' : null].filter(Boolean);
  }
  /* A confirmed change is a decision, so it carries the person's name, asked once where there is
   * none (review.js askActor). The dry run before it records nothing and needs no name. */
  async function signed(path, body) {
    if (!(await askActor())) throw new Error('Ingence needs your name to save this. Set your name first.');
    return write(path, {...body, actor: me.actor});
  }
  /* The header asks again what waits for Re-run (app.js), so its badge counts this change. */
  function countWaiting() {
    if (typeof window.refreshWaiting === 'function') window.refreshWaiting();
  }

  /* The box under a card where a dry run's summary and the confirm button go. */
  function preview(box, lines, confirmLabel, save, help) {
    const status = el('p', {class: 'wf-status faint', role: 'status', 'aria-live': 'polite'});
    const yes = el('button', {type: 'button', class: 'answer', 'data-help': help, onclick: async () => {
      yes.disabled = true;
      status.textContent = 'Saving...';
      try {
        const answer = await save();
        const done = el('div', {class: 'wf-done', role: 'status', tabindex: '-1'}, doneWords(answer).map((line) => el('p', {text: line})));
        fill(box, done);
        // The button is gone with the summary: the focus goes to what replaced it.
        done.focus();
        countWaiting();
      } catch (error) {
        yes.disabled = false;
        status.textContent = `It was not saved: ${String((error && error.message) || error)}`;
      }
    }}, [el('strong', {text: confirmLabel})]);
    const no = el('button', {type: 'button', class: 'answer quiet', 'data-help': 'workflow.cancel', onclick: () => fill(box)}, [el('strong', {text: 'Cancel'})]);
    fill(box, el('div', {class: 'wf-preview notice-note'}, [
      el('p', {class: 'wf-would-head', text: 'What would change'}),
      ...lines.map((line) => el('p', {class: 'wf-would', text: line})),
      el('div', {class: 'wf-actions'}, [yes, no]), status]));
    yes.focus();
  }

  /* The workflow a person changes: the stages in order, each with its types. */
  function changedWorkflow(workflow, stages) {
    return {name: workflow.name, scope: workflow.scope, exempt: workflow.exempt || [],
      stages: stages.map((stage) => ({stage: stage.stage, types: [...stage.types], folders: [...(stage.folders || [])], names: [...(stage.names || [])]}))};
  }

  /* "Change": the stages' types in a small form. Move a stage up or down, remove a stage or a type,
   * add a type from the project's own file types. Then the dry run, then the confirm. */
  async function changeForm(box, workflow) {
    const stages = (workflow.stages || []).map((stage) => ({...stage, types: [...(stage.types || [])]}));
    const types = await readTypes();
    const form = el('form', {class: 'wf-form', 'aria-label': `Change ${workflow.name}`});
    const result = el('div', {class: 'wf-result'});
    /* After a redraw the focus goes back to the control that was used, or the nearest one left.
     * Each control is named by ``data-focus``, and its stage by ``data-stage``. */
    const refocus = (keys, stageName) => {
      for (const key of keys) {
        const found = form.querySelector(`[data-focus="${key}"]:not([disabled])`);
        if (found) { found.focus(); return; }
      }
      const row = stageName ? form.querySelector(`.wf-stage[data-stage="${stageName}"]`) : null;
      const near = (row && row.querySelector('button:not([disabled]), select')) || form.querySelector('button[type="submit"]');
      if (near) near.focus();
    };
    const draw = (keys = [], stageName = '') => {
      const rows = stages.map((stage, index) => {
        const chips = stage.types.map((type, at) => el('span', {class: 'wf-type'}, [typeWord(type),
          el('button', {type: 'button', class: 'wf-x', 'aria-label': `Remove ${typeWord(type)} from ${stageWord(stage.stage)}`,
            'data-help': 'workflow.remove_type', 'data-focus': `x:${stage.stage}:${type}`,
            onclick: () => {
              stage.types.splice(at, 1);
              const next = stage.types[at] || stage.types[at - 1];
              draw([...(next ? [`x:${stage.stage}:${next}`] : []), `add:${stage.stage}`], stage.stage);
            }}, ['×'])]));
        const offered = types.filter((type) => !stage.types.includes(type));
        const pick = el('select', {'aria-label': `A type to add to ${stageWord(stage.stage)}`, 'data-help': 'workflow.add_type',
          'data-focus': `add:${stage.stage}`},
          [el('option', {value: '', text: 'Add a type'}), ...offered.map((type) => el('option', {value: type, text: typeWord(type)}))]);
        pick.addEventListener('change', () => { if (pick.value) { stage.types.push(pick.value); draw([`add:${stage.stage}`], stage.stage); } });
        return el('li', {class: 'wf-stage', 'data-stage': stage.stage}, [
          el('span', {class: 'wf-stage-name', text: `${index + 1}. ${stageWord(stage.stage)}`}),
          el('span', {class: 'wf-types'}, chips.length ? chips : [el('span', {class: 'faint', text: 'no type'})]),
          offered.length ? pick : null,
          el('span', {class: 'wf-move'}, [
            el('button', {type: 'button', class: 'link', 'data-help': 'workflow.move', 'aria-label': `Move ${stageWord(stage.stage)} up`,
              'data-focus': `up:${stage.stage}`, ...(index === 0 ? {disabled: 'disabled'} : {}),
              onclick: () => { stages.splice(index - 1, 0, stages.splice(index, 1)[0]); draw([`up:${stage.stage}`, `down:${stage.stage}`], stage.stage); }}, ['Up']),
            el('button', {type: 'button', class: 'link', 'data-help': 'workflow.move', 'aria-label': `Move ${stageWord(stage.stage)} down`,
              'data-focus': `down:${stage.stage}`, ...(index === stages.length - 1 ? {disabled: 'disabled'} : {}),
              onclick: () => { stages.splice(index + 1, 0, stages.splice(index, 1)[0]); draw([`down:${stage.stage}`, `up:${stage.stage}`], stage.stage); }}, ['Down']),
            el('button', {type: 'button', class: 'link', 'data-help': 'workflow.remove_stage', 'aria-label': `Remove the stage ${stageWord(stage.stage)}`,
              'data-focus': `remove:${stage.stage}`, ...(stages.length === 1 ? {disabled: 'disabled'} : {}),
              onclick: () => {
                stages.splice(index, 1);
                const next = stages[index] || stages[index - 1];
                draw(next ? [`remove:${next.stage}`] : [], next ? next.stage : '');
              }}, ['Remove'])]),
        ]);
      });
      const status = el('p', {class: 'wf-status faint', role: 'status', 'aria-live': 'polite'});
      const show = el('button', {type: 'submit', class: 'answer', 'data-help': 'workflow.show_change'}, [el('strong', {text: 'Show what changes'})]);
      const cancel = el('button', {type: 'button', class: 'answer quiet', 'data-help': 'workflow.cancel', onclick: () => fill(box)}, [el('strong', {text: 'Cancel'})]);
      fill(form, el('p', {class: 'wf-form-head', text: `Change ${workflow.name}: the stages in order, and the file types at each.`}),
        el('ol', {class: 'wf-stages'}, rows), el('div', {class: 'wf-actions'}, [show, cancel]), status, result);
      if (keys.length || stageName) refocus(keys, stageName);
      form.onsubmit = async (event) => {
        event.preventDefault();
        show.disabled = true;
        status.textContent = 'Reading what would change...';
        const changed = changedWorkflow(workflow, stages);
        try {
          const answer = await write('/api/workflows', {workflow: changed, dry_run: true});
          status.textContent = '';
          preview(result, changeWords(answer), 'Change', () => signed('/api/workflows', {workflow: changed}), 'workflow.change_confirm');
        } catch (error) {
          status.textContent = `It could not be read: ${String((error && error.message) || error)}`;
        } finally {
          show.disabled = false;
        }
      };
    };
    draw();
    fill(box, form);
    const first = form.querySelector('button, select');
    if (first) first.focus();
  }

  /* "Turn off": the dry run's summary first, then the confirm. */
  async function turnOff(box, workflow, button) {
    button.setAttribute('aria-busy', 'true');
    try {
      const answer = await write('/api/workflows/withdraw', {id: workflow.id, dry_run: true});
      preview(box, changeWords(answer), 'Turn off', () => signed('/api/workflows/withdraw', {id: workflow.id}), 'workflow.turn_off_confirm');
    } catch (error) {
      fill(box, el('p', {class: 'wf-status', role: 'status', text: `It could not be read: ${String((error && error.message) || error)}`}));
    } finally {
      button.removeAttribute('aria-busy');
    }
  }

  function cardNode(workflow) {
    const box = el('div', {class: 'wf-box'});
    const off = el('button', {type: 'button', class: 'answer quiet', 'data-help': 'workflow.turn_off',
      onclick: () => { turnOff(box, workflow, off).catch(() => {}); }}, [el('strong', {text: 'Turn off'})]);
    const change = el('button', {type: 'button', class: 'answer quiet', 'data-help': 'workflow.change',
      onclick: () => { changeForm(box, workflow).catch(() => {}); }}, [el('strong', {text: 'Change'})]);
    return el('div', {class: `wf-card${workflow.in_force ? '' : ' wf-off'}`, 'data-workflow': workflow.id, 'data-origin': workflow.origin,
      'data-help': 'workflow.card'}, [
      el('p', {class: 'wf-line'}, [el('span', {class: 'wf-sentence', text: workflowSentence(workflow)}),
        workflow.scope ? el('span', {class: 'faint', text: ` Only in ${workflow.scope}.`}) : null,
        workflow.in_force ? null : el('span', {class: 'faint', text: ' Turned off.'})]),
      el('div', {class: 'wf-actions'}, [change, off]),
      box,
    ]);
  }

  /**
   * Draw the workflow card into ``container``: every workflow, or with ``only`` the one named.
   * Draws nothing where there is no workflow (the story says so in its own words).
   */
  async function card(container, options = {}) {
    if (!container) return;
    let answer;
    try {
      answer = await readWorkflows();
    } catch (error) {
      if (container.isConnected) fill(container, states.failed(null, error, () => { card(container, options); }, {into: true}));
      return;
    }
    if (!container.isConnected) return;
    const workflows = (answer.workflows || []).filter((workflow) => !options.only || workflow.id === options.only);
    if (!workflows.length) { fill(container); return; }
    fill(container, el('div', {class: 'wf-cards', 'data-help': 'workflow.card'}, [
      options.only ? null : el('h3', {class: 'wf-head', text: workflows.length === 1 ? 'Workflow' : 'Workflows'}),
      ...workflows.map(cardNode)]));
  }

  /* A new run: the stories and workflows are read again when next shown. */
  if (typeof onRefresh === 'function') onRefresh(() => { stories.clear(); workflowsHeld = null; });

  window.ingenceStory = {render, stageWord, typeWord, dayWords, repeatWords, progressWords};
  window.ingenceWorkflows = {card, workflowSentence, changeWords};
})();
