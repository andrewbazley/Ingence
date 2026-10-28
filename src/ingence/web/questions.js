/* The Questions page.
 *
 * The page opens on what the engine concluded, not on a table. First comes the sentence "Run n
 * stands as read. k questions remain; j of them lead." Then come the Questions that matter: the
 * route's ``leads``, a table of checkbox, question with its reason under it, what answering
 * settles, rule and version, and the answers its kind takes. Then comes the rest, one click away: a
 * chip for each kind of question, each opening the same table fifty rows a page with filters for
 * the top-level folder and the rule, and a last chip naming what sits in the other tabs. Nothing is
 * hidden, and the counts add up. The Overview and Overlap show the lines of work, the words two
 * lines share, the groupings and the pairs.
 *
 * Words lead. Above the questions that matter, a block lists the words the engine is unsure of, the
 * ones in the most files first, with a link into the Words section, so the first line of questions
 * is the words. It reads ``/api/v1/words/queue`` and says nothing where that route has nothing or
 * does not answer.
 *
 * Every table has the header box and, once a row is chosen, the bar that answers the chosen rows
 * together (``review.js``, ``chooser``). A row opens one card in the right pane, whatever its kind:
 * the question, what answering settles, three example files relative to the root, the answers, one
 * optional note, and why it is asked. An answered question stays with its answer until Re-run reads
 * it (the row, in the person's color, and the card, in a box), so the page does not change under
 * the person answering it. Nothing runs by itself.
 *
 * Data: ``/api/v1/questions/ranked`` (every question, ranked; ``leads``, ``rest``, ``title``,
 * ``top`` and ``rule`` narrow it) and ``/api/project`` for the counts above the sentence. Every
 * answer is written through ``POST /api/review/answer``: one row by its ``target``, several by
 * ``targets`` in one request. Loaded by review.js the first time Questions opens. It uses el(),
 * api(), write(), fill(), states, turns, me, askActor(), navigate() and the chooser of review.js.
 * The pure functions are on ``questionsPage`` for the tests (``tests/test_standing_views.py``). */

(function questionsPageScript(global) {
  const FIFTY = 50;
  const LEADS_LIMIT = 1000;
  const THREE = ['none', 'one', 'two', 'three'];
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /* ------------------------------------------------------------------ pure words */

  function fmt(n) {
    return Number(n || 0).toLocaleString('en-US');
  }

  function plural(n, one, many) {
    return `${fmt(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;
  }

  /* The sentence at the top of the page: the run, the questions that remain, and how many of them
   * lead. */
  function standsWords(run, remain, lead) {
    return `${run} stands as read. ${remain === 1 ? '1 question remains' : `${fmt(remain)} questions remain`}; `
      + `${lead === 1 ? '1 of them leads' : `${fmt(lead)} of them lead`}.`;
  }

  function leadHeadWords(lead) {
    return `${fmt(lead)} lead: a rule asks them first, or a grouping waits on them`;
  }

  /* How a word's state is said on its chip in the block of words (the Words section says the same). */
  const WORD_STATE = {unknown: 'unknown', contested: 'tied', glued: 'glued', slip: 'a typo of',
    variant: 'a variant of', understood: 'understood'};
  /* How many of the first words the block lists. */
  const WORDS_SHOWN = 5;

  /* The block's heading: how many words, and what the first ones cover, all the queue's counts. */
  function wordsHeadWords(queue, shown) {
    const total = Number(queue.total) || 0;
    const covered = shown.length ? Number(shown[shown.length - 1].covered) || 0 : 0;
    const first = shown.length === 1 ? 'The first word' : `The first ${plural(shown.length, 'word')}`;
    return `${plural(total, 'word')} the engine is unsure of. ${first} `
      + `${shown.length === 1 ? 'covers' : 'cover'} ${fmt(covered)} of ${fmt(queue.unknown_parts)} `
      + 'word parts it could not read.';
  }

  /* What answering settles, in the unit the route counts, and what waits on it. */
  function settlesCell(question) {
    const unit = question.settles_unit || 'files';
    const said = plural(question.settles_n, String(unit).replace(/s$/, ''), unit);
    const waits = Number(question.classes_touched) || 0;
    return waits ? `${said} · ${plural(waits, 'grouping')}` : said;
  }

  /* The one sentence a card says about what answering settles, by kind, and where it applies. */
  function settlesSentence(question) {
    const n = Number(question.settles_n) || 0;
    const waits = Number(question.classes_touched) || 0;
    const waiting = waits ? `; ${plural(waits, 'grouping')} ${waits === 1 ? 'waits' : 'wait'} on it` : '';
    if (question.kind === 'pack') {
      // A pack that reads nothing here is asked about in the rule's own words, never as a count of
      // files it would read.
      if (question.asked_because && !(question.examples || []).length) {
        const said = String(question.asked_because);
        return {lead: '', strong: '', tail: `${said.charAt(0).toUpperCase()}${said.slice(1)}.`};
      }
      const folders = (question.tops || []).length;
      return {lead: 'It would read ', strong: plural(n, 'file'),
        tail: `${folders > 1 ? ` in ${plural(folders, 'folder')}` : ''}.`
          + `${(question.examples || []).length ? ' Three it would read, as examples:' : ''}`};
    }
    if (question.kind === 'word') {
      return {lead: 'Answering settles ', strong: plural(n, 'file'),
        tail: ` whose names hold the word${waiting}. The answer applies to this root.`};
    }
    if (question.kind === 'derivation pattern' || question.kind === 'atypical pair') {
      return {lead: 'Answering settles ', strong: plural(n, 'pair'),
        tail: '. These pairs are answered one by one.'};
    }
    const unit = String(question.settles_unit || 'files').replace(/s$/, '');
    return {lead: 'Answering settles ', strong: plural(n, unit),
      tail: `${waiting}. The answer applies to this root.`};
  }

  /* "three of 38, relative to the root": the examples shown, of how many files one answer settles. */
  function examplesCaption(question) {
    const shown = (question.examples || []).length;
    return `${THREE[Math.min(shown, 3)]} of ${fmt(question.examples_of)}, relative to the data folder`;
  }

  /* A moment as the card says it: "13 Oct 9:12 AM", in the person's own time. */
  function whenWords(stamp) {
    const moment = new Date(stamp);
    if (!stamp || Number.isNaN(moment.getTime())) return '';
    const hours = moment.getHours();
    const minutes = String(moment.getMinutes()).padStart(2, '0');
    return `${moment.getDate()} ${MONTHS[moment.getMonth()]} ${((hours + 11) % 12) + 1}:${minutes} `
      + `${hours < 12 ? 'AM' : 'PM'}`;
  }

  /* The answer as the card's box says it: the button's label, and a meaning's kind beside it. */
  function answerText(question, answer) {
    const sense = (question.senses || []).find((one) => one.term === answer.word);
    const kind = sense && sense.theme_kind ? ` · ${String(sense.theme_kind).replace(/_/g, ' ')}` : '';
    return `${answer.label}${kind}`;
  }

  /* The title of the card: a tied word names its meanings, from the route's ``senses`` and never by
   * splitting the sentence. */
  function cardTitle(question) {
    const senses = (question.senses || []).map((one) => String(one.term).replace(/_/g, ' '));
    if (question.kind === 'word' && senses.length > 1) {
      const stem = String(question.question).replace(/\?\s*$/, '');
      return `${stem}: ${senses.slice(0, -1).join(', ')} or ${senses[senses.length - 1]}?`;
    }
    return String(question.question);
  }

  /* A path is shown as the route sent it, relative to the root. A path that begins like a machine's
   * own is never shown whole: the route does not send one, and this keeps a stray from reaching
   * the page. */
  function relativePath(path) {
    const text = String(path || '');
    return /^(\/|~|[A-Za-z]:[\\/])/.test(text) ? text.replace(/^.*[\\/]/, '') : text;
  }

  /* The rest of the chips: each title's questions that do not lead, and no chip for none. */
  function restChips(titles) {
    return (titles || []).map((one) => ({title: one.title,
      count: Number(one.questions) - Number(one.leading)}))
      .filter((chip) => chip.count > 0);
  }

  /* ------------------------------------------------------------------ the page's memory */

  const Q = {
    ranked: null,        // the leads response: titles, groups, summary, filters
    leads: [],
    byKey: new Map(),    // every question this page has read, by key, for its card
    fresh: new Map(),    // key -> whether it leads, for questions answered on this page since reading
    open: new Map(),     // title -> {offset, top, rule}: the chips open
    at: '',              // the key of the question whose card is open
    changing: new Set(), // keys whose card is open for a new answer
    leadOffset: 0,
    redraw: new Map(),   // key -> a function that draws the row's answer cell again
    project: null,
    words: null,         // the first words of the Words section's queue
    heading: null,
    said: new Map(),     // key -> what the card says about the last note kept
  };

  const ranked = (query) => api(`/api/v1/questions/ranked?${new URLSearchParams(query)}`);

  /* How many questions remain and how many of them lead: what the route counted when the page
   * read it, less each question answered on this page since (``fresh``: whether each one led). */
  function remainingOf(summary, fresh) {
    const leaders = fresh.filter(Boolean).length;
    return {
      remain: Math.max(0, Number(summary.questions) - Number(summary.answered) - fresh.length),
      lead: Math.max(0, Number(summary.leading) - Number(summary.leading_answered) - leaders),
    };
  }

  function remaining() {
    return remainingOf(Q.ranked.summary, [...Q.fresh.values()]);
  }

  function runName() {
    const run = Q.project && Q.project.run;
    return run && run.number != null ? `Scan ${run.number}` : 'This scan';
  }

  function sayHeading() {
    if (!Q.heading) return;
    const {remain, lead} = remaining();
    Q.heading.textContent = standsWords(runName(), remain, lead);
    const leadHead = document.getElementById('q-lead-head');
    if (leadHead) leadHead.textContent = leadHeadWords(lead);
  }

  function linkTo(address, text, attributes = {}) {
    return el('a', {href: address, class: 'q-link', ...attributes, text});
  }

  /* ------------------------------------------------------------------ the words */

  /* The words block: the first words the engine is unsure of, each with its state and its files,
   * and the way into the Words section. Nothing where the queue holds no word. */
  function wordsBlock() {
    const rows = Q.words && Q.words.rows ? Q.words.rows : [];
    if (!rows.length) return null;
    const shown = rows.slice(0, WORDS_SHOWN);
    return el('section', {class: 'q-section q-words', 'data-section': 'words'}, [
      el('div', {class: 'q-section-head'}, [
        el('div', {}, [el('p', {class: 'eyebrow', text: 'Words'}),
          el('h3', {class: 'q-lead-head', text: wordsHeadWords(Q.words, shown)})]),
        el('p', {class: 'faint q-ranked-by'}, [
          linkTo('#words', 'Teach Ingence your words \u203a', {'data-help': 'questions.words_link'})]),
      ]),
      el('ul', {class: 'words-q-list', 'data-help': 'questions.words'}, shown.map((row) => el('li', {
        'data-word': row.word}, [
        el('code', {text: row.word}), ' ',
        el('span', {class: 'tag plain', text: row.of ? `${WORD_STATE[row.state] || row.state} ${row.of}`
          : (WORD_STATE[row.state] || row.state)}), ' ',
        el('span', {class: 'faint', text: `${plural(row.files, 'file')} \u00b7 ${plural(row.occurrences, 'time')} written`}),
      ]))),
    ]);
  }

  /* ------------------------------------------------------------------ the answers */

  /* The items a table gives the chooser, and how a row says the answer it holds. */
  function itemOf(question) {
    return {key: question.key, name: question.question, question,
      targets: question.target && question.answers.length ? [question.target] : [],
      words: question.answers.map((answer) => ({word: answer.word, label: answer.label,
        title: answer.title || ''}))};
  }

  /* Say an answer in the model: the question is answered, or, for a "Can't say" kept as a note, noted. */
  function applyAnswer(question, choice, written) {
    if (choice.word === null) {
      question.noted = true;
      Q.said.set(question.key, `Kept as a note by ${me.actor}; it decides nothing, so the question stays.`);
    } else {
      question.noted = false;
      if (!question.answered && !Q.fresh.has(question.key)) Q.fresh.set(question.key, Boolean(question.leads));
      question.answered = true;
      question.answer = {word: choice.word, label: choice.label, actor: written.actor || me.actor,
        occurred_at: written.occurred_at || new Date().toISOString(), note: written.note || null,
        sequence: written.sequence == null ? null : written.sequence, waits_for_rerun: true};
    }
    Q.changing.delete(question.key);
    sayHeading();
    const redraw = Q.redraw.get(question.key);
    if (redraw) redraw();
    if (Q.at === question.key) drawCard(question);
    showNothingLeft();
  }

  /* One question answered by one press (a row's button, or the card's): one request. */
  async function answerOne(question, choice, typedNote) {
    if (!(await askActor())) {
      return {refused: 'Ingence needs your name to save an answer. Set your name first.'};
    }
    const typed = String(typedNote || '').trim();
    const body = {actor: me.actor, target: question.target};
    if (choice.word) {
      body.word = choice.word;
      if (typed) body.note = typed;
    } else {
      // "Can't say" on a kind that has no such verdict: the person's words, as a note, deciding nothing.
      body.note = typed ? `Can't say. ${typed}` : "Can't say";
    }
    const written = await write('/api/review/answer', body);
    applyAnswer(question, choice, {...written, note: typed});
    return {written};
  }

  /* The buttons a question takes, each one press. */
  function answerButtons(question, {onPress, quiet = false, kinds = false} = {}) {
    return el('div', {class: `answers q-answers${question.answers.length > 3 ? ' q-many' : ''}`, role: 'group', 'aria-label': 'Answers'},
      question.answers.map((choice) => el('button', {type: 'button',
        class: quiet ? 'answer quiet' : 'answer', 'data-word': choice.word === null ? 'cannot_say' : choice.word,
        'data-help': 'questions.answer', title: choice.title || '',
        onclick: (event) => onPress(choice, event)},
      [el('strong', {text: choice.label}),
        kinds && choice.theme_kind ? el('span', {class: 'kind', text: String(choice.theme_kind).replace(/_/g, ' ')}) : null])));
  }

  /* ------------------------------------------------------------------ a table of questions */

  function ruleCell(question) {
    if (!question.rule_id) return el('td', {class: 'q-rule'}, [el('span', {class: 'mono', text: question.kind})]);
    return el('td', {class: 'q-rule'}, [
      el('span', {class: 'mono', text: question.rule_id}), ' ',
      el('span', {class: 'mono faint', text: question.rule_version ? `· v${question.rule_version}` : ''})]);
  }

  /* The cell that holds a question's answer: the answer in the person's color once given, with
   * "Change"; else the buttons its kind takes. */
  function answerCell(question) {
    const cell = el('td', {class: 'q-answer'});
    const draw = () => {
      if (question.answer && !Q.changing.has(question.key)) {
        cell.replaceChildren(el('span', {class: 'q-said', 'data-said': question.answer.word === null ? '' : question.answer.word,
          title: `${question.answer.actor}${question.answer.occurred_at ? `, ${whenWords(question.answer.occurred_at)}` : ''}`,
          text: answerText(question, question.answer)}), ' ',
        el('button', {type: 'button', class: 'link q-change', 'data-help': 'questions.change',
          text: 'Change', onclick: (event) => {
            event.stopPropagation();
            Q.changing.add(question.key);
            draw();
            const first = cell.querySelector('button.answer');
            if (first) first.focus();
          }}));
        return;
      }
      if (!question.answers.length) {
        cell.replaceChildren(el('span', {class: 'faint'}, ['Answered pair by pair: ',
          el('a', {href: question.address, class: 'q-link', text: 'open the suggested pairs'})]));
        return;
      }
      const buttons = answerButtons(question, {onPress: async (choice, event) => {
        event.stopPropagation();
        for (const button of buttons.querySelectorAll('button')) button.disabled = true;
        try {
          const done = await answerOne(question, choice, '');
          if (done.refused) cell.append(el('p', {class: 'note refused', text: done.refused}));
        } catch (error) {
          cell.append(el('p', {class: 'note refused', text: String((error && error.message) || error)}));
        } finally {
          for (const button of buttons.querySelectorAll('button')) button.disabled = false;
        }
      }});
      fill(cell, buttons, question.noted
        ? el('span', {class: 'faint q-noted', text: "Can't say (noted)"}) : null);
    };
    draw();
    Q.redraw.set(question.key, draw);
    return cell;
  }

  function questionCell(question) {
    return el('td', {class: 'q-question'}, [
      el('a', {href: question.address, class: 'q-text', 'data-address': question.address,
        text: question.question, onclick: (event) => {
          event.preventDefault();
          event.stopPropagation();
          openQuestion(question);
        }}),
      el('span', {class: 'q-reason', text: question.reason}),
    ]);
  }

  /* One table of questions with its header box, its bar and its line saying what was recorded. */
  function questionTable(questions, {noun = 'question', listed = null} = {}) {
    for (const question of questions) Q.byKey.set(question.key, question);
    const said = el('p', {class: 'bulk-said', role: 'status', 'aria-live': 'polite'});
    const items = questions.map(itemOf);
    const made = chooser({items, noun, said, listed, onAnswered: (item, choice, written) => {
      applyAnswer(item.question, choice, written);
    }});
    const body = items.map((item) => {
      const question = item.question;
      const open = () => openQuestion(question);
      const tr = chosenRow(el('tr', {class: question.key === Q.at ? 'row q-row selected' : 'row q-row',
        'data-key': question.key, 'data-target': question.key, onclick: open}, [
        el('td', {class: 'pick'}, [made.boxFor(item)]),
        questionCell(question),
        el('td', {class: 'q-settles', text: settlesCell(question)}),
        ruleCell(question),
        answerCell(question),
      ]), made, question.key, open);
      return tr;
    });
    const head = el('tr', {}, [
      el('th', {class: 'pick'}, [made.header]),
      el('th', {text: 'Question'}), el('th', {text: 'Settles'}),
      el('th', {text: 'Rule · version'}), el('th', {text: 'Answer'}),
    ]);
    const node = el('div', {class: 'q-table'}, [
      made.bar, said,
      el('table', {class: 'q-questions'}, [el('thead', {}, [head]), el('tbody', {}, body)]),
    ]);
    return {node, made};
  }

  /* ------------------------------------------------------------------ the card */

  /* The rule's name and version as the handle that opens it on the Rules page. */
  function ruleLink(question) {
    return el('button', {type: 'button', class: 'link rule-handle', title: 'Open this rule',
      text: `${question.rule_id}${question.rule_version ? ` v${question.rule_version}` : ''}`,
      onclick: async () => {
        await navigate('rules');
        const found = document.getElementById(`rule-${question.rule_id}`);
        if (found) found.scrollIntoView({block: 'center'});
      }});
  }

  function ruleBlock(question) {
    const group = (Q.ranked.groups || []).find((held) => held.key === question.group);
    const meaning = group && group.meaning ? group.meaning : '';
    return [
      meaning ? el('p', {text: meaning}) : null,
      question.asked_because ? el('p', {text: `The rule's own words: ${question.asked_because}.`}) : null,
      question.reason ? el('p', {class: 'faint', text: `It ranks where it does because it ${question.reason}.`}) : null,
      question.rule_id ? el('p', {class: 'q-small'}, ['Asked by ', ruleLink(question)]) : null,
    ];
  }

  async function ownWords(question) {
    const pane = detailPane;
    states.loading(pane, 'Reading the question...');
    const page = await api('/api/pages/questions?limit=1000&answered=waiting');
    const row = (page.rows || []).find((held) => held.target === question.target
      || held.answers_at === question.target);
    if (!row) {
      states.empty(pane, 'The page that says what a word means has no such question in this scan.');
      return;
    }
    sheetState.kind = 'questions';
    sheetState.data = page;
    sheetState.at = row.target;
    await showRow(row, async () => { await refresh(); });
  }

  /* The card, drawn into the right pane. */
  function drawCard(question) {
    const answering = !question.answer || Q.changing.has(question.key);
    const note = el('input', {type: 'text', class: 'note-input q-note', maxlength: '4000',
      placeholder: 'a note saved with the answer (optional)', 'aria-label': 'A note saved with the answer',
      'data-help': 'questions.note'});
    const status = el('p', {class: 'note q-status', role: 'status', 'aria-live': 'polite',
      text: Q.said.get(question.key) || ''});
    const sentence = settlesSentence(question);
    const examples = (question.examples || []).map(relativePath);
    const parts = [
      el('div', {class: 'q-card-head'}, [
        el('h3', {class: 'q-card-title', text: cardTitle(question)}),
        el('span', {class: 'q-kind mono', 'data-help': 'questions.kind',
          text: `${question.kind} · ${question.rule_version ? `v${question.rule_version}` : 'no rule'}`}),
      ]),
      el('p', {class: 'q-card-sentence'}, [sentence.lead, sentence.strong ? el('strong', {text: sentence.strong}) : null, sentence.tail]),
      examples.length ? el('ul', {class: 'q-examples', 'aria-label': 'Examples'},
        examples.map((example) => el('li', {}, [el('code', {text: example})]))) : null,
      examples.length ? el('p', {class: 'q-small'}, [examplesCaption(question),
        question.kind === 'word' && question.value ? ' · ' : null,
        question.kind === 'word' && question.value ? el('button', {type: 'button', class: 'link',
          'data-help': 'questions.stands', text: `where “${question.value}” stands`,
          onclick: () => { askFor(question.value); }}) : null]) : null,
    ];
    if (question.answer) {
      parts.push(el('div', {class: 'q-answered', 'data-answered': question.answer.word || ''}, [
        el('p', {}, [el('strong', {text: 'Answered: '}), answerText(question, question.answer),
          ` · ${question.answer.actor === me.actor ? 'you' : question.answer.actor}`
          + (question.answer.occurred_at ? `, ${whenWords(question.answer.occurred_at)}` : ''), ' · ',
          el('button', {type: 'button', class: 'link', 'data-help': 'questions.change', text: 'Change',
            onclick: () => { Q.changing.add(question.key); drawCard(question); const first = detailPane.querySelector('button.answer'); if (first) first.focus(); }})]),
        question.answer.note ? el('p', {class: 'q-small', text: `Note: ${question.answer.note}`}) : null,
        question.answer.waits_for_rerun
          ? el('p', {class: 'q-small', text: 'Waits for Re-run. The row stays here with its answer until the next scan reads it.'}) : null,
        nextWaiting(question) ? el('p', {class: 'q-small'}, [el('button', {type: 'button', class: 'link',
          'data-help': 'questions.next', text: 'Next question \u203a',
          onclick: () => openQuestion(nextWaiting(question))})]) : null,
      ]));
    }
    if (answering) {
      if (question.answers.length) {
        parts.push(answerButtons(question, {kinds: true, onPress: async (choice, event) => {
          const buttons = event.currentTarget.parentElement.querySelectorAll('button');
          for (const button of buttons) button.disabled = true;
          try {
            const done = await answerOne(question, choice, note.value);
            if (done.refused) {
              status.className = 'note q-status refused';
              status.textContent = done.refused;
            }
          } catch (error) {
            status.className = 'note q-status refused';
            status.textContent = String((error && error.message) || error);
          } finally {
            for (const button of buttons) button.disabled = false;
          }
        }}));
        parts.push(noteField(note, question.target, 'question'));
        // "Can't say" is a note on a kind with no such verdict, and the card says so under the buttons.
        parts.push(el('p', {class: 'q-small', text: "Can't say is saved as a note and decides nothing."}));
        if (question.kind === 'pack') {
          parts.push(el('p', {class: 'q-small', text: 'A word list answered Yes is added at the next scan.'}));
        }
        if (question.own_words) {
          parts.push(el('p', {class: 'q-small'}, [el('button', {type: 'button', class: 'link',
            'data-help': 'questions.own_words', text: 'Say what it means in your own words',
            onclick: () => ownWords(question).catch((error) => states.failed(detailPane, error))})]));
        }
      } else {
        parts.push(el('p', {class: 'q-small'}, ['These pairs are answered one by one: ',
          el('a', {href: question.address, class: 'q-link', text: 'open the suggested pairs'}), '.']));
      }
    }
    parts.push(status);
    parts.push(el('details', {class: 'q-why', 'data-help': 'questions.why'}, [
      el('summary', {text: 'Why this is asked'}), ...ruleBlock(question)]));
    fill(detailPane, el('article', {class: question.answer ? 'card q-card q-card-answered' : 'card q-card',
      'data-key': question.key}, parts));
  }

  /* Mark a question's row and draw its card, without naming it in the address. */
  function selectQuestion(question) {
    Q.at = question.key;
    for (const row of listPane.querySelectorAll('tr.q-row')) {
      row.classList.toggle('selected', row.dataset.key === question.key);
    }
    drawCard(question);
  }

  /* The question waiting after this one in the order the tables show them, wrapping round. */
  function nextWaiting(question) {
    const keys = [...listPane.querySelectorAll('tr.q-row')].map((row) => row.dataset.key);
    const at = keys.indexOf(question.key);
    for (let step = 1; step < keys.length; step += 1) {
      const held = Q.byKey.get(keys[(at + step) % keys.length]);
      if (held && !held.answer) return held;
    }
    return null;
  }

  function openQuestion(question) {
    if (Q.at !== question.key) Q.said.delete(Q.at);
    Q.at = question.key;
    for (const row of listPane.querySelectorAll('tr.q-row')) {
      row.classList.toggle('selected', row.dataset.key === question.key);
    }
    setFocus({kind: 'question', id: question.key, label: String(question.question).slice(0, 80)});
    drawCard(question);
  }

  /* ------------------------------------------------------------------ the leads */

  function leadSection() {
    const lead = Q.leads;
    const section = el('section', {class: 'q-section', 'data-section': 'leads'});
    const {lead: stillLeading} = remaining();
    const ranking = el('div', {class: 'q-ranking', hidden: ''}, [
      el('ol', {}, (Q.ranked.summary.ranked_by || []).map((line) => el('li', {text: line}))),
      el('p', {class: 'q-small', text: 'There is no score: each of these is compared in turn.'}),
    ]);
    const how = el('button', {type: 'button', class: 'link', 'aria-expanded': 'false',
      'data-help': 'questions.ranking', text: 'how the ranking works', onclick: (event) => {
        ranking.hidden = !ranking.hidden;
        event.currentTarget.setAttribute('aria-expanded', String(!ranking.hidden));
      }});
    const parts = [
      el('div', {class: 'q-section-head'}, [
        el('div', {}, [el('p', {class: 'eyebrow', text: 'Questions that matter'}),
          el('h3', {id: 'q-lead-head', class: 'q-lead-head', text: leadHeadWords(stillLeading)})]),
        el('p', {class: 'faint q-ranked-by'}, ["ranked by what settles most; the rule's own order first · ", how]),
      ]),
      ranking,
    ];
    if (!lead.length) {
      parts.push(el('p', {class: 'nothing', text: 'No question comes first in this scan.'}));
    } else {
      const shown = lead.slice(Q.leadOffset, Q.leadOffset + FIFTY);
      parts.push(questionTable(shown, {noun: 'question', listed: lead.length}).node);
      if (lead.length > FIFTY) {
        parts.push(pager({offset: Q.leadOffset, listed: lead.length, onMove: (to) => {
          Q.leadOffset = to;
          draw();
        }}));
      }
    }
    fill(section, ...parts);
    return section;
  }

  /* ------------------------------------------------------------------ the rest */

  function pager({offset, listed, onMove}) {
    if (listed <= FIFTY && !offset) return null;
    const first = listed ? offset + 1 : 0;
    const last = Math.min(offset + FIFTY, listed);
    return el('p', {class: 'pager', 'data-help': 'review.pager'}, [
      el('button', {class: 'link', type: 'button', text: '← previous 50', ...(offset ? {} : {disabled: ''}),
        onclick: () => onMove(Math.max(0, offset - FIFTY))}),
      ` rows ${fmt(first)} to ${fmt(last)} of ${fmt(listed)} `,
      el('button', {class: 'link', type: 'button', text: 'next 50 →', ...(last < listed ? {} : {disabled: ''}),
        onclick: () => onMove(offset + FIFTY)}),
    ]);
  }

  /* One chip's questions, as the same table: fifty a page, filters for the top-level folder and the rule. */
  async function drawGroup(title, holder) {
    const state = Q.open.get(title);
    if (!state) return;
    states.loading(holder, `Reading ${title.toLowerCase()}...`);
    const query = {rest: 'true', answered: 'waiting', limit: String(FIFTY), offset: String(state.offset), title};
    if (state.top) query.top = state.top;
    if (state.rule) query.rule = state.rule;
    const found = await ranked(query);
    if (!Q.open.has(title) || !document.contains(holder)) return;
    const reload = () => drawGroup(title, holder).catch((error) => states.failed(holder, error));
    const choose = (name, label, options) => {
      const select = el('select', {'aria-label': label, 'data-help': 'review.filter',
        onchange: (event) => { state[name] = event.target.value; state.offset = 0; reload(); }},
      [el('option', {value: '', text: name === 'top' ? 'every top-level folder' : 'every rule'}),
        ...options.map((item) => el('option', {value: item.value, text: `${item.value} (${fmt(item.n)})`}))]);
      select.value = state[name];
      return select;
    };
    const filters = found.filters.tops.length > 1 || found.filters.rules.length > 1 || state.top || state.rule
      ? el('div', {class: 'filters q-filters'}, [
        el('label', {text: 'Top-level folder '}), choose('top', 'Top-level folder', found.filters.tops),
        el('label', {text: ' Rule '}), choose('rule', 'Rule', found.filters.rules)]) : null;
    const table = found.questions.length
      ? questionTable(found.questions, {noun: 'question', listed: found.total}).node
      : el('p', {class: 'nothing', text: 'No question here matches the filters.'});
    fill(holder, filters, table, pager({offset: state.offset, listed: found.total, onMove: (to) => {
      state.offset = to;
      reload();
    }}));
  }

  function chipRow(chips, elsewhere) {
    const row = el('div', {class: 'q-chips', role: 'group', 'aria-label': 'The rest of the questions'});
    const sections = el('div', {class: 'q-groups'});
    const holders = new Map();
    for (const chip of chips) {
      const holder = el('div', {class: 'q-group card', 'data-title': chip.title, hidden: ''});
      holders.set(chip.title, holder);
      const button = el('button', {type: 'button', class: 'q-chip', 'data-title': chip.title,
        'aria-expanded': 'false', 'data-help': 'questions.chip'}, [
        el('span', {class: 'q-caret', 'aria-hidden': 'true', text: '▶'}), ` ${chip.title} · ${fmt(chip.count)}`]);
      const toggle = (open) => {
        const opening = open === undefined ? !Q.open.has(chip.title) : open;
        if (opening) {
          if (!Q.open.has(chip.title)) Q.open.set(chip.title, {offset: 0, top: '', rule: ''});
          holder.hidden = false;
          drawGroup(chip.title, holder).catch((error) => states.failed(holder, error));
        } else {
          Q.open.delete(chip.title);
          holder.hidden = true;
        }
        button.setAttribute('aria-expanded', String(opening));
        button.querySelector('.q-caret').textContent = opening ? '▼' : '▶';
      };
      button.addEventListener('click', () => toggle());
      button.toggleChip = toggle;
      row.append(button);
      sections.append(holder);
      if (Q.open.has(chip.title)) toggle(true);
    }
    if (elsewhere.length) {
      row.append(el('span', {class: 'q-chip q-elsewhere', 'data-help': 'questions.elsewhere'}, [
        'Elsewhere: ', ...elsewhere.flatMap((page, index) => [index ? ' · ' : '',
          el('a', {href: `#${page.view}`, class: 'q-link', text: page.label}), ` ${fmt(page.waiting)}`])]));
    }
    return {row, sections};
  }

  function restSection() {
    const chips = restChips(Q.ranked.titles);
    const elsewhere = Q.ranked.summary.elsewhere || [];
    if (!chips.length && !elsewhere.length) return null;
    const made = chipRow(chips, elsewhere);
    return el('section', {class: 'q-section', 'data-section': 'rest'}, [
      el('p', {class: 'eyebrow', text: 'The rest of the questions'}),
      made.row, made.sections,
      el('p', {class: 'q-small', text: 'Each group opens as a table like the one above, 50 rows a page, with the same '
        + 'header checkbox. Filters: area, rule. Nothing is hidden, and nothing starts by itself.'}),
    ]);
  }

  /* ------------------------------------------------------------------ nothing left */

  function rerunCount() {
    const badge = document.getElementById('rerun-badge');
    return badge && !badge.hidden ? badge.textContent.trim() : '';
  }

  /* "Every question is answered or waits for Re-run." under the cards, with the badge's count. */
  function nothingLeft() {
    const count = rerunCount();
    return states.say(null, {eyebrow: 'Questions · nothing left', dot: 'empty', into: true,
      sentence: 'Every question is answered or waits for Re-run.',
      body: count ? `${count} ${count === '1' ? 'change waits' : 'changes wait'} for Re-run.` : ''});
  }

  function showNothingLeft() {
    const holder = document.getElementById('q-nothing');
    if (!holder || !Q.ranked) return;
    const {remain} = remaining();
    if (remain > 0) {
      holder.replaceChildren();
      return;
    }
    holder.replaceChildren(nothingLeft());
  }

  /* ------------------------------------------------------------------ the page */

  function draw() {
    Q.redraw.clear();
    const counts = Q.project ? Q.project.counts : {};
    const said = [
      counts.claims ? plural(counts.claims, 'conclusion') : null,
      counts.candidates ? `${plural(counts.candidates, 'pair')} proposed` : null,
      counts.rule_failures ? plural(counts.rule_failures, 'rule') + ' could not decide' : null,
    ].filter(Boolean);
    const {remain, lead} = remaining();
    Q.heading = el('h2', {class: 'q-stands', id: 'q-stands', text: standsWords(runName(), remain, lead)});
    const rest = restSection();
    fill(listPane, el('div', {class: 'q-page'}, [
      el('div', {class: 'q-head'}, [
        el('p', {class: 'eyebrow', text: 'What Ingence found'}),
        said.length ? el('p', {class: 'faint q-counts', text: said.join(' · ')}) : null,
      ]),
      Q.heading,
      el('div', {id: 'q-nothing', 'aria-live': 'polite'}),
      wordsBlock(),
      leadSection(),
      rest,
    ]));
    showNothingLeft();
  }

  async function read() {
    const soft = (path) => api(path).catch(() => null);
    const [found, project, words] = await Promise.all([
      ranked({leads: 'true', answered: 'waiting', limit: String(LEADS_LIMIT)}),
      soft('/api/project'),
      soft(`/api/v1/words/queue?limit=${WORDS_SHOWN}`),
    ]);
    return {found, project, words};
  }

  async function show() {
    document.querySelector('main').classList.add('q-wide');
    const turn = turns.claim(listPane);
    states.loading(listPane, 'Reading the questions...');
    const got = await read();
    if (!turns.owns(listPane, turn) || state.view !== 'review') return;
    Q.ranked = got.found;
    Q.leads = got.found.questions;
    Q.project = got.project;
    Q.words = got.words;
    Q.fresh.clear();
    Q.changing.clear();
    Q.byKey.clear();
    for (const question of Q.leads) Q.byKey.set(question.key, question);
    draw();
    await openFromAddress();
  }

  /* A question named in the address (``?question=<key>#review``) opens its card, and the chip that
   * holds it where it is not among the leads. */
  async function openFromAddress() {
    const wanted = focusHeld && focusHeld.kind === 'question' ? focusHeld.id : '';
    if (!wanted) {
      // A file, a word or a run being looked at is already open beside the page: leave it.
      if (focusHeld && focusHeld.id && focusHeld.kind !== 'question') return;
      if (!Q.at || !Q.byKey.has(Q.at)) {
        // Something is always in the pane: the first question waiting opens, and answering it
        // leaves it there with its answer and a way to the next.
        const first = Q.leads.find((question) => !question.answer);
        if (first) selectQuestion(first);
        else fill(detailPane, el('p', {class: 'empty', text: 'Choose a question to see what answering it settles.'}));
      } else {
        selectQuestion(Q.byKey.get(Q.at));
      }
      return;
    }
    let question = Q.byKey.get(wanted);
    if (!question) {
      const found = await ranked({key: wanted, answered: 'waiting', limit: '1'});
      question = found.questions[0];
      if (!question) {
        // What the other tabs of Questions are looking at is not this page's to open.
        fill(detailPane, el('p', {class: 'empty', text: 'Choose a question to see what answering it settles.'}));
        return;
      }
      Q.byKey.set(question.key, question);
      const chip = listPane.querySelector(`.q-chip[data-title="${CSS.escape(question.title)}"]`);
      if (chip && chip.toggleChip && !question.leads) chip.toggleChip(true);
    }
    selectQuestion(question);
  }

  /* Read everything again and draw the page where the person was (after an answer written elsewhere). */
  async function refresh() {
    if (state.view !== 'review') return;
    const flash = sheetState.flash;
    sheetState.flash = '';
    await show();
    if (flash) detailPane.prepend(el('p', {class: 'note kept', 'data-advance': 'recorded', text: flash}));
  }

  // The nothing-left state quotes the Re-run badge, which is read beside the page: follow it.
  const watched = typeof document !== 'undefined' ? document.getElementById('rerun-badge') : null;
  if (watched && typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => { if (state.view === 'review') showNothingLeft(); })
      .observe(watched, {attributes: true, childList: true, characterData: true, subtree: true});
  }

  global.questionsPage = {show, refresh, standsWords, leadHeadWords, settlesCell, settlesSentence,
    examplesCaption, whenWords, answerText, cardTitle, relativePath, restChips,
    wordsHeadWords, remaining: remainingOf};
  if (typeof VIEWS !== 'undefined') VIEWS.review = show;
  // Leaving the page gives the list its ordinary width back.
  if (typeof onLeave === 'function') {
    onLeave((next) => {
      if (next !== 'review') document.querySelector('main').classList.remove('q-wide');
    });
  }
})(typeof window !== 'undefined' ? window : globalThis);
