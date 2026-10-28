// @ts-check
/* The Words section of Questions: where a person teaches the engine their own words.
 *
 * A person clicks a word in a list ordered by how many files carry it, says what it means by
 * choosing, says whether the similar spellings beside it are the same thing, and the next word
 * opens. Nothing applies until Re-run: every answer is a decision in the Record under the
 * person's name, and the Re-run badge counts it.
 *
 * - The list (``GET /api/v1/words/queue``): by default the words the engine is unsure of, most
 *   files first, occurrences beside them, a bar for the files against the first row's and a chip
 *   for the state (unknown, contested, a slip of, a variant of, glued, understood). Above it is
 *   the reading meter: how many word parts are understood now, and how many would be if every
 *   word down to the open one were answered. Both are the engine's counts (the header's and each
 *   row's ``covered``), with the number they are counted of, and never a share alone.
 * - The card has two halves. "What it means" shows the meanings the Dictionary holds for this
 *   spelling and its near spellings, each with its kind and origin, then "something else" (the
 *   define row: a meaning, its kind and where it applies), "not a person's word", "can't say" and
 *   "skip". "The same thing?" shows each spelling that may be this word again, with its reason in
 *   words and its files, answered "the same thing", "different things of one kind" or "unrelated"
 *   (unanswered is allowed: nothing is written for it).
 * - Recording writes one batch to ``POST /api/review/answer`` (a list of ``decisions``): the
 *   word's own answer and one for each spelling answered. The word and every spelling answered
 *   with it leave the list, and the next word opens (the auto-advance of the other pages). Skip
 *   writes nothing and moves the word to the end of the list.
 * - Keys: ``1`` to ``9`` choose a meaning, ``S`` / ``K`` / ``U`` answer the focused spelling (the
 *   same thing, of one kind, unrelated), ``Enter`` records, ``N`` skips, ``?`` lists them
 *   (keys.js). While focus is inside the card the digits are the card's: the page keys ``1`` to
 *   ``5`` stand aside there, as they do inside the Graph workspace.
 * - Show answered lists answered words again with the decision, who made it and when, and whether
 *   it waits for Re-run. Choosing again replaces the answer (the Record keeps both). Show words
 *   it understood adds the words the Dictionary already reads, to confirm or correct.
 *
 * Standing on app.js's helpers (el, api, write, fill, states, turns, table) and review.js's (the
 * define row, the word evidence, the computer readings, advance, keyedRow, me, askActor). The pure
 * functions are on ``wordsPage`` for the tests. */

(function wordsPageScript(/** @type {any} */ global) {
  'use strict';

  /** @typedef {import('./types/api').QueueWords} QueueWords */
  /** @typedef {import('./types/api').QueueRow} QueueRow */
  /** @typedef {import('./types/api').QueueVariant} QueueVariant */
  /** @typedef {import('./types/api').QueueSense} QueueSense */
  /** @typedef {import('./types/api').SenseItem} SenseItem */
  /** @typedef {{term: string, theme_kind: string, origin: string, accepted: boolean, role: string, from: string | null}} Option */
  /** @typedef {{type: 'sense', term: string, theme_kind: string, inForce: boolean}
   *   | {type: 'own'} | {type: 'not_a_word', reading: string} | {type: 'cannot_say'}} Choice */
  /** @typedef {{answer: 'same' | 'kind' | 'unrelated', meaning: string}} VariantAnswer */
  /** @typedef {{[name: string]: unknown}} Decision */

  /** Rows to a page. */
  const PAGE_SIZE = 50;
  /** How many of the first words the one quiet line on other pages counts. */
  const QUIET_WORDS = 20;
  /** The keys that choose a meaning. */
  const MOST_NUMBERED = 9;

  /* ------------------------------------------------------------------ pure words */

  /** @param {unknown} n */
  const fmt = (n) => Number(n || 0).toLocaleString('en-US');

  /** @param {unknown} n @param {string} one @param {string} [many] */
  const count = (n, one, many) => `${fmt(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;

  /** @param {unknown} text */
  const spaced = (text) => String(text ?? '').replace(/_/g, ' ');

  /** What a row's state is called on its chip, and what it says in a sentence.
   * @type {{[state: string]: {chip: string, says: string}}} */
  const STATE_WORDS = {
    unknown: {chip: 'unknown', says: 'The Dictionary has no meaning for this word.'},
    contested: {chip: 'tied',
      says: 'Two or more meanings tie, so Ingence uses none of them.'},
    glued: {chip: 'glued',
      says: 'A capital letter divides this spelling. Ingence read it as one word or as separate words, or asks you which.'},
    slip: {chip: 'a typo of', says: 'The Dictionary has no meaning for it. It is one or two letters away from a known word.'},
    variant: {chip: 'a variant of', says: 'The Dictionary has no meaning for it. It looks like another unknown word, written differently.'},
    understood: {chip: 'understood', says: 'The Dictionary already reads this word.'},
  };

  /** What the filter chips above the list call a state. @type {{[state: string]: string}} */
  const FILTER_WORDS = {unknown: 'unknown', contested: 'tied', glued: 'glued', slip: 'typos',
    variant: 'variants', understood: 'understood'};

  /** The chip a row wears.
   * @param {QueueRow} row @returns {{text: string, title: string}} */
  function stateChip(row) {
    const held = STATE_WORDS[row.state] || {chip: spaced(row.state), says: ''};
    const text = (row.state === 'slip' || row.state === 'variant') && row.of
      ? `${held.chip} ${row.of}` : held.chip;
    return {text, title: held.says};
  }

  /** The bar of a row's files against the first row's, as a width in percent (a drawing, not a
   * number anyone reads). @param {number} files @param {number} first */
  function barWidth(files, first) {
    if (!(first > 0)) return 0;
    return Math.max(1, Math.round((100 * files) / first));
  }

  /** The rows with the words a person skipped moved to the end, in the order they were skipped.
   * @param {QueueRow[]} rows @param {string[]} skipped @returns {QueueRow[]} */
  function orderRows(rows, skipped) {
    const held = new Set(skipped);
    const moved = skipped.map((word) => rows.find((row) => row.word === word))
      .filter((row) => row !== undefined);
    return [...rows.filter((row) => !held.has(row.word)), ...(/** @type {QueueRow[]} */ (moved))];
  }

  /** The unknown word parts covered by every row down to each row, in the order shown: the served
   * per-row counts added up, from ``base`` (what the rows before the page covered).
   * @param {QueueRow[]} rows @param {number} base @returns {number[]} */
  function coveredAlong(rows, base) {
    let sum = base;
    return rows.map((row) => {
      sum += Number(row.unknown_parts || 0);
      return sum;
    });
  }

  /** What the rows before a page covered: the first row's cumulative count less its own.
   * @param {QueueWords} queue @returns {number} */
  function coveredBefore(queue) {
    const first = (queue.rows || [])[0];
    return first ? Number(first.covered || 0) - Number(first.unknown_parts || 0) : 0;
  }

  /** The meter's numbers: all from the queue's header and rows.
   * @param {QueueWords} queue @param {number} covered
   * @returns {{read: number, now: number, would: number, covered: number, unknown: number}} */
  function meterOf(queue, covered) {
    const words = (queue.reading && queue.reading.words) || {};
    const now = Number(words.understood || 0);
    return {read: Number(words.read || 0), now, would: now + Number(covered || 0),
      covered: Number(covered || 0), unknown: Number(queue.unknown_parts || 0)};
  }

  /** The meter in words: counts, each with what it is a count of.
   * @param {{read: number, now: number, would: number}} meter */
  function meterWords(meter) {
    return `${fmt(meter.now)} of ${fmt(meter.read)} word parts understood now. `
      + `If every word down to this one were answered: ${fmt(meter.would)} of ${fmt(meter.read)}.`;
  }

  /** The one quiet line the Years view and the landing page say after a first run.
   * @param {QueueWords | null | undefined} queue @param {number} [most] @returns {string} */
  function quietLine(queue, most = QUIET_WORDS) {
    const rows = (queue && queue.rows) || [];
    const total = Number(queue && queue.unknown_parts) || 0;
    if (!rows.length || !total) return '';
    const top = rows.slice(0, most);
    const covered = Number(top[top.length - 1].covered) || 0;
    if (!covered) return '';
    return `${count(top.length, 'word')} ${top.length === 1 ? 'covers' : 'cover'} ${fmt(covered)} of `
      + `the ${fmt(total)} word parts Ingence does not know. Add their meanings.`;
  }

  /** Where a meaning comes from, in words. @type {{[origin: string]: string}} */
  const ORIGIN_WORDS = {
    builtin: 'the built-in words',
    core: 'the built-in words',
    domain: 'a domain word list',
    pack: 'a domain word list',
    laboratory: 'your laboratory’s word list',
    discovered: 'found in the names',
    user: 'your own answer',
    yours: 'your own answer',
  };

  /** The meanings to offer for a word: its own (the one in force, those tied, those outranked),
   * then those of its near spellings, each once.
   * @param {QueueRow} row @param {Map<string, Array<SenseItem | QueueSense>>} near
   * @returns {Option[]} */
  function meaningOptions(row, near) {
    /** @type {Option[]} */
    const found = [];
    const seen = new Set();
    /** @param {any} sense @param {string | null} from */
    const add = (sense, from) => {
      const key = `${sense.term}|${sense.theme_kind}`;
      if (seen.has(key)) return;
      seen.add(key);
      found.push({term: sense.term, theme_kind: sense.theme_kind, origin: sense.origin,
        accepted: sense.accepted !== false, role: sense.role || (from ? 'near' : 'in_force'), from});
    };
    const order = ['in_force', 'tied', 'outranked'];
    [...row.senses].sort((a, b) => order.indexOf(a.role) - order.indexOf(b.role))
      .forEach((sense) => add(sense, null));
    for (const [spelling, senses] of near) senses.forEach((sense) => add(sense, spelling));
    return found;
  }

  /** The line under a meaning: where it comes from, and the word it was found beside.
   * @param {Option} option @param {string} example */
  function optionLine(option, example) {
    const parts = [ORIGIN_WORDS[option.origin] || spaced(option.origin)];
    if (!option.accepted) parts.push('a word list this project does not use yet');
    if (option.role === 'tied') parts.push('tied with another meaning');
    if (option.from) parts.push(`the meaning of "${option.from}"`);
    if (example) parts.push(`for example ${example}`);
    return parts.join(' · ');
  }

  /** A word's target in the Record, the way the engine spells one.
   * @param {string} spelling */
  const termTarget = (spelling) => `term:${spelling.toLowerCase()}`;

  /** The spellings a card asks about. A glued spelling and the pieces it is made of are answered by
   * the one reading ("one word" or "separate words"), so they are not asked again as spellings.
   * @param {QueueRow} row @returns {QueueVariant[]} */
  function askedVariants(row) {
    return row.variants.filter((variant) => variant.reason !== 'glued' && variant.reason !== 'glued_in');
  }

  /** The folders a meaning can be kept below: the data's top level is not one (below it is everywhere).
   * @param {QueueRow} row */
  function foldersOf(row) {
    return (row.where || []).filter((folder) => folder.path);
  }

  /** A spelling answered with the word carries the word's own place: where the word's meaning is kept
   * below a folder, the spelling's is kept below it too (a correction there), so the two apply in one place.
   * @param {{below?: string | null}} head */
  function scopeOf(head) {
    return head.below ? {correcting: true, below: head.below} : {};
  }

  /** What the card holds, as the batch needs it.
   * @typedef {{row: QueueRow, choice: Choice | null,
   *   own?: {meaning: string, kind: string, below: string | null},
   *   variants: Map<string, VariantAnswer>, split?: 'whole' | 'pieces' | null, note?: string,
   *   near?: Map<string, Array<SenseItem | QueueSense>>}} BatchSpec */

  /** One word's answers as one batch (a list of decisions for ``POST /api/review/answer``): the
   * word's own, then one for each spelling answered, then the reading of a glued spelling. What
   * each writes: a meaning held -> an assignment on ``term:<spelling>``; a meaning of one's own
   * -> a definition (kind, and the folder it is kept below); "the same thing" -> the variant takes
   * the head's meaning; "of one kind" -> the variant gets a meaning of its own in the head's kind;
   * "unrelated" -> ``distinct:<a>+<b>``; a glued spelling one word or two -> ``split:<spelling>``;
   * "not a person's word" -> the not-a-word answer with its computer reading; "can't say" -> a
   * note, which decides nothing. A problem is said in words and nothing is sent.
   * @param {BatchSpec} spec
   * @returns {{decisions: Decision[], problem: string, confirmed: boolean}} */
  function batchFor(spec) {
    const {row, choice, own, variants} = spec;
    /** @type {Map<string, Array<SenseItem | QueueSense>>} */
    const near = spec.near || new Map();
    const typed = String(spec.note || '').trim();
    /** @type {Decision[]} */
    const decisions = [];
    const refuse = (/** @type {string} */ problem) => ({decisions: [], problem, confirmed: false});
    /** The head's meaning and kind, where it has one, and the folder it is kept below if it is.
     * @type {{term: string, kind: string, below?: string | null} | null} */
    let head = null;
    /** @type {Decision | null} */
    let headDecision = null;
    let confirmed = false;
    const placed = row.senses.length > 0;
    // The word's own target: a glued spelling's row is named by its reading (``split:...``), and what
    // the word means is still said on the word.
    const headTarget = row.target.startsWith('term:') ? row.target : termTarget(row.word);

    if (choice && choice.type === 'sense') {
      head = {term: choice.term, kind: choice.theme_kind};
      // The meaning the Dictionary already reads is confirmed, which writes nothing.
      if (choice.inForce && row.state === 'understood') confirmed = true;
      else {
        headDecision = {target: headTarget, answer: choice.term, theme_kind: choice.theme_kind,
          ...(placed ? {correcting: true} : {})};
      }
    } else if (choice && choice.type === 'own') {
      const meaning = String((own && own.meaning) || '').trim();
      const kind = String((own && own.kind) || '').trim();
      if (!meaning || !kind) return refuse('Write what it means and choose what kind of thing it is.');
      const below = own && own.below ? own.below : null;
      head = {term: meaning, kind, below};
      headDecision = {target: headTarget, answer: meaning, theme_kind: kind,
        ...(placed || below ? {correcting: true} : {}), ...(below ? {below} : {})};
    } else if (choice && choice.type === 'not_a_word') {
      if (!choice.reading) return refuse('Say what it is to a computer: choose one of the options.');
      headDecision = {target: headTarget, word: 'not_a_word', reading: choice.reading};
    } else if (choice && choice.type === 'cannot_say') {
      headDecision = {target: headTarget, note: typed ? `Can't say. ${typed}` : "Can't say"};
    }

    /** @type {Decision[]} */
    const afterwards = [];
    for (const variant of askedVariants(row)) {
      const given = variants.get(variant.spelling);
      if (!given) continue;
      if (given.answer === 'unrelated') {
        afterwards.push({target: variant.distinct_target, word: 'unrelated'});
        continue;
      }
      const target = termTarget(variant.spelling);
      // A spelling the engine already folds into this word has nothing to be told.
      if (target === headTarget) continue;
      if (variant.known_as) {
        // A word the Dictionary knows is not written to. "The same thing" gives this word its
        // meaning; where this word has none chosen yet, that is the answer.
        if (given.answer === 'same') {
          if (!head) {
            const offered = (near.get(variant.spelling) || [])
              .find((sense) => sense.term === variant.known_as) || (near.get(variant.spelling) || [])[0];
            if (!offered) {
              return refuse(`Choose what "${row.word}" means: "${variant.spelling}" is already known as `
                + `${spaced(variant.known_as)}.`);
            }
            head = {term: offered.term, kind: offered.theme_kind};
            headDecision = {target: headTarget, answer: offered.term, theme_kind: offered.theme_kind,
              ...(placed ? {correcting: true} : {})};
          } else if (head.term !== variant.known_as) {
            return refuse(`"${variant.spelling}" is already known as ${spaced(variant.known_as)}. `
              + `Choose that meaning for "${row.word}", or answer it another way.`);
          }
        } else if (!head) {
          return refuse(`Say what "${row.word}" means: it is a different thing of the same kind as `
            + `"${variant.spelling}".`);
        }
        continue;
      }
      if (given.answer === 'same') {
        if (!head) {
          return refuse(`Say what "${row.word}" means first, so "the same thing" has a meaning to name.`);
        }
        afterwards.push({target, answer: head.term, theme_kind: head.kind, ...scopeOf(head)});
      } else {
        if (!head || !head.kind) {
          return refuse(`Say what "${row.word}" means first: "of one kind" takes its kind.`);
        }
        const meaning = String(given.meaning || '').trim() || variant.spelling;
        afterwards.push({target, answer: meaning, theme_kind: head.kind, ...scopeOf(head)});
      }
    }
    if (spec.split && row.glued) {
      afterwards.push({target: row.glued.target, word: spec.split === 'whole' ? 'whole' : 'pieces'});
    }
    if (headDecision) decisions.push(headDecision);
    decisions.push(...afterwards);
    if (typed && decisions.length && !(choice && choice.type === 'cannot_say')) {
      decisions[0] = {...decisions[0], note: typed};
    }
    if (!decisions.length && !confirmed) {
      return refuse('Nothing to record yet: choose what the word means, answer a spelling, or skip it.');
    }
    return {decisions, problem: '', confirmed};
  }

  /** Who gave the answer in force on a row, and when: "you, 14 Oct 3:10 PM".
   * @param {QueueRow} row @param {string} [you] */
  function answeredBy(row, you = '') {
    const answer = row.answered;
    if (!answer) return '';
    return `${you && answer.actor === you ? 'you' : answer.actor}, ${whenWords(answer.occurred_at)}`;
  }

  /** What was recorded, in a sentence: how many decisions, and which of them wait for Re-run. A pair
   * called unrelated is kept in the Record, and no run reads it, so it does not wait.
   * @param {string} word @param {string} actor @param {string[]} targets the targets written */
  function recordedWords(word, actor, targets) {
    const apart = targets.filter((target) => target.startsWith('distinct:')).length;
    const waiting = targets.length - apart;
    const parts = [`Recorded ${count(targets.length, 'answer')} by ${actor} for "${word}".`];
    if (waiting) parts.push(`${waiting === 1 ? 'It waits' : `${fmt(waiting)} wait`} for Re-run.`);
    if (apart) {
      parts.push(`${count(apart, 'pair')} called unrelated ${apart === 1 ? 'is' : 'are'} kept in the answer log. `
        + `No scan reads ${apart === 1 ? 'it' : 'them'}.`);
    }
    return parts.join(' ');
  }

  /** The request that withdraws the answer in force on a row: the answer named by its sequence and
   * its digest as this page read them, so a page the Record has moved past cannot withdraw another
   * answer. Nothing where the answer cannot be withdrawn on its own (the queue says which).
   * @param {QueueRow} row @param {string} actor
   * @returns {{actor: string, withdraw: {sequence: number, digest: string}} | null} */
  function withdrawBody(row, actor) {
    const answer = row.answered;
    if (!answer || !answer.withdrawable) return null;
    return {actor, withdraw: {sequence: answer.sequence, digest: answer.digest}};
  }

  /** What the confirmation says before an answer is withdrawn, in words.
   * @param {QueueRow} row @param {string} [you] */
  function withdrawWords(row, you = '') {
    const answer = row.answered;
    if (!answer) return '';
    return `The answer about "${row.word}" says: ${answer.said}. It was given by ${answeredBy(row, you)}. `
      + 'The answer log keeps it and marks it withdrawn. Any answer before it is in force again. '
      + `${row.waiting ? NO_RUN_READ : 'Withdrawing waits for Re-run, like every answer.'}`;
  }

  /** What is said of a withdrawal when no run has read the answer: it is cancelled, and nothing waits. */
  const NO_RUN_READ = 'No scan has read this answer, so withdrawing cancels it. Nothing waits for Re-run.';

  /** The note after an answer is withdrawn, by whether a run had read it.
   * @param {QueueRow} row @param {string} actor */
  function withdrawnWords(row, actor) {
    return `Withdrawn by ${actor}: the answer about "${row.word}". The answer log keeps it. `
      + (row.waiting ? NO_RUN_READ : 'The withdrawal waits for Re-run.');
  }

  /** The sentence beside the Withdraw button in the Answered box. Where a run has read the answer,
   * the earlier answer is back in force only after Re-run.
   * @param {QueueRow} row */
  function withdrawBeside(row) {
    return 'Takes this answer back. Any answer before it is in force again'
      + `${row.waiting ? '.' : ' after Re-run.'}`;
  }

  /** What a refused withdrawal says: the engine's own words, and for a page that was out of date
   * (409) that the list is read again.
   * @param {number} status @param {string} message */
  function withdrawRefusal(status, message) {
    const said = String(message || '').trim() || 'Ingence gave no reason.';
    return status === 409 ? `${said} The page was out of date, so Ingence loads the list again.` : said;
  }

  /** The decision in force on an answered row, in a sentence.
   * @param {QueueRow} row @param {string} [you] */
  function answeredWords(row, you = '') {
    return row.answered ? `${row.answered.said} \u00b7 ${answeredBy(row, you)}` : '';
  }

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /** A moment as the card says it: "14 Oct 3:10 PM".
   * @param {unknown} stamp */
  function whenWords(stamp) {
    const moment = new Date(String(stamp));
    if (!stamp || Number.isNaN(moment.getTime())) return '';
    const hours = moment.getHours();
    return `${moment.getDate()} ${MONTHS[moment.getMonth()]} ${((hours + 11) % 12) + 1}:`
      + `${String(moment.getMinutes()).padStart(2, '0')} ${hours < 12 ? 'AM' : 'PM'}`;
  }

  global.wordsPage = {fmt, count, stateChip, barWidth, orderRows, coveredAlong, coveredBefore,
    meterOf, meterWords, quietLine, meaningOptions, optionLine, askedVariants, foldersOf, batchFor, recordedWords,
    withdrawBody, withdrawWords, withdrawnWords, withdrawBeside,
    withdrawRefusal, answeredWords, answeredBy, whenWords,
    STATE_WORDS, FILTER_WORDS, termTarget};

  // Under Node (the tests) there is no page: the pure functions above are all there is.
  if (typeof document === 'undefined' || typeof VIEWS === 'undefined') return;

  /* ------------------------------------------------------------------ the page's memory */

  const W = {
    /** @type {QueueWords | null} */ data: null,
    /** The rows in the order shown. @type {QueueRow[]} */ rows: [],
    /** The unknown parts covered down to each row, in that order. @type {number[]} */ covered: [],
    at: '',
    /** The words skipped, in the order they were skipped. @type {string[]} */ skipped: [],
    /** The targets in the order the list last showed them. @type {string[]} */ shownOrder: [],
    offset: 0,
    answered: 'hide',
    understood: 'hide',
    stateFilter: '',
    flash: '',
    /** @type {HTMLElement | null} */ meter: null,
  };

  /** @typedef {{row: QueueRow, choice: Choice | null, variants: Map<string, VariantAnswer>,
   *   split: 'whole' | 'pieces' | null, near: Map<string, Array<SenseItem | QueueSense>>,
   *   shown: any, options: Option[], busy: boolean, card: HTMLElement, status: HTMLElement,
   *   note: HTMLInputElement, defineBox: HTMLElement | null, computerBox: HTMLElement | null,
   *   refresh: () => void, redrawVariants: () => void}} Card */

  /** The card that is open. @type {Card | null} */
  let C = null;

  /** The queue, one page. @returns {Promise<QueueWords>} */
  function read() {
    const query = new URLSearchParams({limit: String(PAGE_SIZE), offset: String(W.offset),
      answered: W.answered, understood: W.understood});
    if (W.stateFilter) query.set('state', W.stateFilter);
    return api(`/api/v1/words/queue?${query}`);
  }

  /** Put the rows in the order shown, with the skipped words last. */
  function arrange() {
    if (!W.data) return;
    W.rows = orderRows(W.data.rows || [], W.skipped);
    W.covered = coveredAlong(W.rows, coveredBefore(W.data));
  }

  /** The rows a person can still answer, shaped for review.js's ``advance.next``.
   * @returns {Array<{target: string, in_force: boolean}>} */
  function waitingProxies() {
    return W.rows.map((row) => ({target: row.target, in_force: Boolean(row.answered)}));
  }

  /** The row to open after ``from``, in the order last shown.
   * @param {string} from @param {string[]} before @returns {QueueRow | null} */
  function rowAfter(from, before) {
    const found = advance.next(waitingProxies(), from, before);
    return found ? (W.rows.find((row) => row.target === found.target) || null) : null;
  }

  /* ------------------------------------------------------------------ the list */

  /** The reading meter above the list, for the row open (or the first). */
  function drawMeter() {
    if (!W.data || !W.meter) return;
    const at = Math.max(0, W.rows.findIndex((row) => row.target === W.at));
    const covered = W.rows.length ? W.covered[at] : 0;
    const meter = meterOf(W.data, covered);
    // A run that read no word part has no figure to draw: nothing is said, never "0 of 0".
    W.meter.hidden = !(meter.read > 0);
    if (W.meter.hidden) {
      W.meter.replaceChildren();
      return;
    }
    const bar = el('div', {class: 'words-meter-bar', role: 'img',
      'aria-label': meterWords(meter)}, [
      el('span', {class: 'words-meter-now', style: `flex-grow:${meter.now}`}),
      el('span', {class: 'words-meter-gain', style: `flex-grow:${meter.covered}`}),
      el('span', {class: 'words-meter-rest',
        style: `flex-grow:${Math.max(0, meter.read - meter.would)}`}),
    ]);
    fill(W.meter,
      el('p', {class: 'eyebrow', text: 'How much of the names Ingence understands'}),
      el('p', {class: 'words-meter-line'}, [
        el('strong', {text: fmt(meter.now)}), ` of ${fmt(meter.read)} word parts understood now. `,
        'If every word down to this one were answered: ', el('strong', {text: fmt(meter.would)}),
        ` of ${fmt(meter.read)}.`]),
      bar,
      el('p', {class: 'faint words-meter-note',
        text: 'Ingence counts word parts in names. A word written twice in a name counts twice. '
          + `${fmt(meter.unknown)} word parts are unknown. Solid is understood now, light is what these answers would add, hatched is the rest.`}),
    );
  }

  /** @param {HTMLElement} node @param {string} help */
  function helped(node, help) {
    node.setAttribute('data-help', help);
    return node;
  }

  /** A toggle in the list's header: a labelled box.
   * @param {string} text @param {boolean} on @param {(on: boolean) => void} onChange @param {string} help */
  function toggle(text, on, onChange, help) {
    const box = el('input', {type: 'checkbox', ...(on ? {checked: ''} : {})});
    box.addEventListener('change', () => onChange(/** @type {HTMLInputElement} */ (box).checked));
    return helped(el('label', {class: 'words-toggle'}, [box, ` ${text}`]), help);
  }

  /** The state chips that narrow the list, each with the engine's count.
   * @param {QueueWords} data */
  function chips(data) {
    const order = ['unknown', 'contested', 'glued', 'slip', 'variant', 'understood'];
    const held = data.by_state || {};
    const total = Object.values(held).reduce((sum, n) => sum + Number(n), 0);
    /** @param {string} value @param {string} text @param {number} n */
    const chip = (value, text, n) => {
      const on = W.stateFilter === value;
      return el('button', {type: 'button', class: on ? 'words-chip on' : 'words-chip',
        'aria-pressed': String(on), 'data-state': value, 'data-help': 'words.state',
        onclick: () => { W.stateFilter = on ? '' : value; W.offset = 0; show().catch(reportError); }},
      [`${text} · ${fmt(n)}`]);
    };
    return el('div', {class: 'words-chips', role: 'group', 'aria-label': 'Narrow the list by state'}, [
      chip('', 'all', total),
      ...order.filter((one) => Number(held[one]) > 0 || W.stateFilter === one)
        .map((one) => chip(one, FILTER_WORDS[one] || one, Number(held[one] || 0))),
    ]);
  }

  /** @param {QueueRow} row @param {number} first */
  function rowNode(row, first) {
    const chip = stateChip(row);
    const answered = row.answered;
    const spellings = row.examples.filter((one) => one.toLowerCase() !== row.word.toLowerCase());
    const tr = el('tr', {class: row.target === W.at ? 'row words-row selected' : 'row words-row',
      'data-target': row.target, 'data-key': row.target, 'data-word': row.word,
      'data-state': row.state, onclick: () => openWord(row, {focus: true})}, [
      el('td', {class: 'words-word'}, [
        el('code', {text: row.word}),
        spellings.length ? el('span', {class: 'faint words-written', text: ` written ${spellings.join(', ')}`}) : null,
        answered ? el('span', {class: 'words-answer', 'data-said': answered.kind}, [
          el('span', {class: 'tag now person', title: answeredWords(row, me.actor), text: answered.said}),
          row.waiting ? el('span', {class: 'faint', text: ' waits for Re-run'}) : null,
          answered.withdrawable ? ' ' : null,
          answered.withdrawable ? withdrawButton(row) : null]) : null,
      ]),
      el('td', {}, [el('span', {class: 'tag plain words-state', title: chip.title, text: chip.text})]),
      el('td', {class: 'n words-files'}, [
        el('span', {class: 'words-count', text: fmt(row.files)}),
        el('span', {class: 'words-bar', 'aria-hidden': 'true'}, [
          el('span', {class: 'words-bar-fill', style: `width:${barWidth(row.files, first)}%`})]),
      ]),
      el('td', {class: 'n', text: fmt(row.occurrences)}),
    ]);
    return keyedRow(tr, () => openWord(row, {focus: true}));
  }

  /** @param {QueueWords} data */
  function pagerNode(data) {
    const listed = Number(data.total);
    if (listed <= PAGE_SIZE && !W.offset) return null;
    const first = listed ? W.offset + 1 : 0;
    const last = Math.min(W.offset + PAGE_SIZE, listed);
    /** @param {number} to */
    const move = (to) => { W.offset = to; show().catch(reportError); };
    return el('p', {class: 'pager words-pager', 'data-help': 'review.pager'}, [
      el('button', {class: 'link', type: 'button', text: '← previous 50',
        ...(W.offset ? {} : {disabled: ''}), onclick: () => move(Math.max(0, W.offset - PAGE_SIZE))}),
      ` words ${fmt(first)} to ${fmt(last)} of ${fmt(listed)} `,
      el('button', {class: 'link', type: 'button', text: 'next 50 →',
        ...(last < listed ? {} : {disabled: ''}), onclick: () => move(W.offset + PAGE_SIZE)}),
    ]);
  }

  /** The whole list pane: heading, the way the list is ordered, the meter, the toggles, the chips
   * and the rows. */
  function drawList() {
    const data = W.data;
    if (!data) return;
    W.meter = helped(el('section', {class: 'card words-meter', 'aria-label': 'The word meter'}), 'words.meter');
    const first = W.rows.length ? Math.max(...W.rows.map((row) => Number(row.files))) : 0;
    const head = [
      el('h2', {text: 'Words'}),
      ...explained('The words in your folder names that Ingence is unsure of, the ones in the most '
        + 'files first. Say what a word means, and which spellings are the same thing.',
      'Each row is a word that Ingence read in file and folder names. Files is how many names have it. '
      + 'Times written is how often they write it. The word in the most names comes first. '
      + 'Ingence saves your answers under your name, and they wait for Re-run. Nothing changes until '
      + 'you click Re-run. Skip saves nothing and moves the word to the end of this list.'),
      W.meter,
      el('div', {class: 'words-toggles'}, [
        toggle('Show answered', W.answered === 'show', (on) => {
          W.answered = on ? 'show' : 'hide'; W.offset = 0; show().catch(reportError);
        }, 'words.show_answered'),
        toggle('Show words it understood', W.understood === 'show', (on) => {
          W.understood = on ? 'show' : 'hide'; W.offset = 0; show().catch(reportError);
        }, 'words.show_understood'),
        Number(data.waiting) ? el('span', {class: 'faint words-waiting',
          text: `${count(data.waiting, 'answer')} here ${Number(data.waiting) === 1 ? 'waits' : 'wait'} for Re-run`}) : null,
      ]),
      chips(data),
    ];
    if (!W.rows.length) {
      fill(listPane, ...head, nothingHere(data));
      drawMeter();
      return;
    }
    const body = W.rows.map((row) => rowNode(row, first));
    fill(listPane, ...head,
      helped(table(['Word', 'State', '#Files', '#Times written'], body), 'words.list'),
      pagerNode(data));
    drawMeter();
  }

  /** What the list says when it has no row: engine's words, one drawn state.
   * @param {QueueWords} data */
  function nothingHere(data) {
    const narrowed = Boolean(W.stateFilter) || W.answered !== 'hide';
    return states.say(null, {eyebrow: 'Words · nothing to answer', dot: 'empty', into: true,
      sentence: narrowed ? 'No word matches this view.' : 'Ingence is sure of every word.',
      body: [data.reading && data.reading.said ? data.reading.said : '',
        W.understood === 'hide' ? 'To confirm or correct the words it understood, turn on Show words it understood above.' : '']
        .filter(Boolean),
      actions: narrowed ? [{label: 'Show every state', onclick: () => {
        W.stateFilter = ''; W.answered = 'hide'; show().catch(reportError);
      }}] : []});
  }

  /* ------------------------------------------------------------------ the card */

  /** Mark the open row in the list. */
  function markSelected() {
    for (const row of listPane.querySelectorAll('tr.words-row')) {
      row.classList.toggle('selected', /** @type {HTMLElement} */ (row).dataset.target === W.at);
    }
  }

  /** What the card says about the row's state, as one or two sentences.
   * @param {QueueRow} row @returns {string} */
  function stateSentence(row) {
    const held = STATE_WORDS[row.state];
    if (row.state === 'slip' && row.of) return `${held.says} It may be a typo of "${row.of}".`;
    if (row.state === 'variant' && row.of) return `${held.says} It may be another spelling of "${row.of}".`;
    if (row.state === 'understood' && row.senses.length) {
      return `The Dictionary reads it as ${spaced(row.senses[0].term)} (${spaced(row.senses[0].theme_kind)}).`;
    }
    return held ? held.says : '';
  }

  /** Open a word: the card in the right pane, the row marked, the meter following.
   * @param {QueueRow} row @param {{focus?: boolean, flash?: string}} [options] */
  async function openWord(row, options = {}) {
    const turn = turns.claim(detailPane);
    W.at = row.target;
    markSelected();
    drawMeter();
    states.loading(detailPane, `Loading "${row.word}"...`);
    /** @type {any} */
    let shown = null;
    /** @type {Map<string, Array<SenseItem | QueueSense>>} */
    const near = new Map();
    await Promise.all([
      api(`/api/word?word=${encodeURIComponent(row.word)}`).then((found) => { shown = found; }, () => null),
      // The meaning of a spelling the Dictionary knows is read by its term, so it is found even where
      // no name in this run writes the spelling (the word's own page says there is no such name).
      ...askedVariants(row).filter((variant) => variant.known_as).map((variant) =>
        api(`/api/dictionary/term?term=${encodeURIComponent(String(variant.known_as))}`).then((found) => {
          near.set(variant.spelling, (found.senses || []).map((/** @type {any} */ sense) => ({
            term: String(variant.known_as), theme_kind: sense.theme_kind, origin: sense.origin})));
        }, () => null)),
    ]);
    if (!turns.owns(detailPane, turn) || W.at !== row.target) return;
    drawCard(row, shown, near, options);
  }

  /** @param {QueueRow} row @param {any} shown
   * @param {Map<string, Array<SenseItem | QueueSense>>} near
   * @param {{focus?: boolean, flash?: string}} options */
  function drawCard(row, shown, near, options) {
    const kinds = (W.data && W.data.theme_kinds) || [];
    const example = shown && shown.names && shown.names[0] ? String(shown.names[0].name) : '';
    const meanings = meaningOptions(row, near);
    const asked = askedVariants(row);
    const status = el('p', {class: 'note words-status', role: 'status', 'aria-live': 'polite'});
    const note = noteInput();
    note.setAttribute('data-help', 'words.note');
    const card = el('article', {class: 'card words-card', tabindex: '-1', 'data-own-digits': '',
      'data-word': row.word, 'data-help': 'words.card',
      'aria-label': `What does ${row.word} mean here?`});
    /** @type {Card} */
    const made = {row, choice: null, variants: new Map(), split: null, near, shown, options: meanings,
      busy: false, card, status, note, defineBox: null, computerBox: null,
      refresh: () => {}, redrawVariants: () => {}};
    C = made;

    // ---- What it means
    const numbered = [];
    const optionNodes = new Map();
    /** @param {Choice} choice */
    const choose = (choice) => {
      made.choice = choice;
      made.status.textContent = '';
      made.status.className = 'note words-status';
      made.refresh();
    };
    /** @param {number | null} number @param {string} title @param {string} meta @param {Choice} choice
     * @param {string} matches @param {string} help */
    const optionButton = (number, title, meta, choice, matches, help) => {
      const button = el('button', {type: 'button', role: 'radio', 'aria-checked': 'false',
        class: 'words-option', 'data-help': help, 'data-choice': choice.type,
        ...(number ? {'data-number': String(number)} : {}),
        onclick: () => choose(choice)}, [
        number ? el('kbd', {class: 'words-number', text: String(number)}) : el('span', {class: 'words-number'}),
        el('span', {class: 'words-option-text'}, [el('strong', {text: title}),
          meta ? el('span', {class: 'words-meta', text: meta}) : null])]);
      optionNodes.set(`${choice.type}|${matches}`, {button, choice, matches});
      return button;
    };
    let number = 0;
    for (const option of meanings.slice(0, MOST_NUMBERED)) {
      number += 1;
      numbered.push(optionButton(number, spaced(option.term),
        `${spaced(option.theme_kind)} · ${optionLine(option, example)}`,
        {type: 'sense', term: option.term, theme_kind: option.theme_kind,
          inForce: option.role === 'in_force' && option.accepted},
        `${option.term}|${option.theme_kind}`, 'words.sense'));
    }
    for (const option of meanings.slice(MOST_NUMBERED)) {
      numbered.push(optionButton(null, spaced(option.term),
        `${spaced(option.theme_kind)} · ${optionLine(option, example)}`,
        {type: 'sense', term: option.term, theme_kind: option.theme_kind,
          inForce: option.role === 'in_force' && option.accepted},
        `${option.term}|${option.theme_kind}`, 'words.sense'));
    }
    const fixed = (/** @type {string} */ title, /** @type {string} */ meta, /** @type {Choice} */ choice,
      /** @type {string} */ help) => {
      number += 1;
      return optionButton(number <= MOST_NUMBERED ? number : null, title, meta, choice, '', help);
    };
    const own = fixed('Something else', 'Say what it means in your words, and what kind of thing it is.',
      {type: 'own'}, 'words.own');
    const notAWord = fixed('Not a person\'s word',
      'A string a computer or a program reads, such as an extension or a code.',
      {type: 'not_a_word', reading: ''}, 'words.not_a_word');
    const cannot = fixed("Can't say", 'Ingence saves it as a note beside the word. The word stays as it is.',
      {type: 'cannot_say'}, 'words.cannot_say');

    const chosenFolders = foldersOf(row);
    /** @type {Array<{value: string, label: string}>} */
    const where = chosenFolders.length ? [{value: 'everywhere', label: 'everywhere'},
      ...chosenFolders.map((folder, at) => ({value: at === 0 ? 'where_it_stands' : `below:${folder.path}`,
        label: `only below ${folder.path} (${count(folder.files, 'file')})`}))] : [];
    // The define row reused from the question pages: its button says the meaning and records at once.
    const define = defineRow(kinds, () => {
      made.choice = {type: 'own'};
      made.refresh();
      submit().catch(reportError);
    }, where);
    define.setAttribute('data-help', 'words.own_row');
    const defineBox = el('div', {class: 'words-own', hidden: ''}, [define]);
    made.defineBox = defineBox;
    for (const input of define.querySelectorAll('.define-term, .define-kind, .define-where')) {
      input.addEventListener('input', () => { if (!made.choice || made.choice.type !== 'own') choose({type: 'own'}); });
      input.addEventListener('change', () => { if (!made.choice || made.choice.type !== 'own') choose({type: 'own'}); });
    }
    const computerBox = el('div', {class: 'words-computer', hidden: ''});
    made.computerBox = computerBox;
    if (shown) {
      computerBox.append(...computerChoices(shown, (reading) => {
        made.choice = {type: 'not_a_word', reading};
        made.refresh();
        submit().catch(reportError);
      }).filter(Boolean).map((part) => /** @type {Node} */ (part)));
    }

    const skipButton = helped(el('button', {type: 'button', class: 'answer quiet words-skip',
      onclick: () => skip(row)}, [el('strong', {text: 'Skip'}), ' ', el('kbd', {text: 'N'})]), 'words.skip');
    const recordButton = helped(el('button', {type: 'button', class: 'answer words-record',
      onclick: () => { submit().catch(reportError); }}, [el('strong', {text: 'Save'}), ' ',
      el('kbd', {text: 'Enter'})]), 'words.record');

    /* A spelling a capital divides has no word of its own in the names (the census holds its pieces),
     * so nothing can be said of what it means: what is asked is how it reads, and the pieces are words
     * of their own in this list. */
    const unread = row.glued != null;
    const group = unread
      ? el('p', {class: 'faint words-glued-note', text: `A capital letter divides "${row.word}", so the names hold its `
        + `pieces, ${row.glued ? row.glued.pieces.join(' and ') : ''}, as separate words. Each piece has its own `
        + 'row.'})
      : el('div', {class: 'words-options', role: 'radiogroup', 'aria-label': 'What it means'},
        [...numbered, own, defineBox, notAWord, computerBox, cannot]);

    // ---- The same thing?
    const variantsBox = el('div', {class: 'words-variants'});
    made.redrawVariants = () => redrawVariants(made, variantsBox);
    redrawVariants(made, variantsBox);

    // ---- Glued
    const glued = row.glued ? gluedBlock(made, row.glued) : null;

    const answered = row.answered ? el('div', {class: 'words-answered person', 'data-answered': row.answered.kind}, [
      el('p', {}, [el('strong', {text: 'Answered: '}), row.answered.said, ' · ', answeredBy(row, me.actor)]),
      row.answered.below ? el('p', {text: `Kept below ${row.answered.below}. The meaning elsewhere stays as it is.`}) : null,
      el('p', {class: 'faint', text: `${row.waiting ? 'Waits for Re-run.' : 'The last scan read it.'} `
        + (row.answered.below
          ? 'Answering again for the whole project leaves this folder\'s meaning in force below it. '
          : 'Answering again here replaces this answer for the same place. ')
        + 'The answer log keeps every earlier answer.'}),
      row.answered.withdrawable ? el('p', {}, [withdrawButton(row), ` ${withdrawBeside(row)}`]) : null]) : null;

    made.refresh = () => {
      const now = made.choice;
      for (const {button, choice, matches} of optionNodes.values()) {
        let on = false;
        if (now && now.type === choice.type) {
          on = now.type === 'sense' && choice.type === 'sense'
            ? `${now.term}|${now.theme_kind}` === matches : true;
        }
        button.setAttribute('aria-checked', String(on));
        button.classList.toggle('on', on);
      }
      defineBox.hidden = !(now && now.type === 'own');
      computerBox.hidden = !(now && now.type === 'not_a_word');
    };

    fill(card,
      options.flash ? el('p', {class: 'note kept', 'data-advance': 'recorded', text: options.flash}) : null,
      el('p', {class: 'eyebrow', text: `Word ${fmt(Math.max(0, W.rows.findIndex((one) => one.target === row.target)) + 1 + W.offset)} of ${fmt(W.data ? W.data.total : 0)}`}),
      row.glued
        ? el('h2', {class: 'question'}, ['Is ', el('mark', {text: row.word}), ' one word?'])
        : el('h2', {class: 'question'}, ['What does ', el('mark', {text: row.word}), ' mean here?']),
      el('p', {class: 'words-facts'}, [
        el('strong', {text: count(row.files, 'file')}), ` carry it, ${count(row.occurrences, 'time')} written`,
        row.experiments != null ? ` · ${count(row.experiments, 'experiment')}` : '', ' · ',
        el('span', {class: 'tag plain', title: stateChip(row).title, text: stateChip(row).text})]),
      el('p', {class: 'words-state-says', text: stateSentence(row)}),
      row.examples.length > 1 ? el('p', {class: 'faint', text: `Written as: ${row.examples.join(', ')}`}) : null,
      answered,
      glued,
      row.glued ? null : el('h3', {text: 'What it means'}),
      group,
      asked.length ? el('h3', {class: 'words-same-head', text: 'Other spellings'}) : null,
      asked.length ? el('p', {class: 'faint', text: 'These spellings may be the same word. '
        + 'Answer the ones you can. The others stay as they are.'}) : null,
      variantsBox,
      noteInputRow(note),
      el('div', {class: 'words-actions'}, [recordButton, skipButton]),
      el('p', {class: 'faint words-keys',
        text: '1 to 9 choose a meaning · S, K, U answer a spelling (same, one kind, unrelated) · Enter saves (on a meaning not chosen yet, it chooses it first) · N skips · ? lists every key'}),
      status,
      el('details', {class: 'words-evidence', ...(asked.length ? {} : {open: ''})}, [
        el('summary', {text: 'Where it is used'}),
        ...(shown ? wordEvidence(shown).filter(Boolean).map((part) => /** @type {Node} */ (part))
          : [el('p', {class: 'faint', text: 'Ingence could not load where it is used.'})])]),
    );
    made.refresh();
    fill(detailPane, card);
    if (options.focus) card.focus({preventScroll: true});
  }

  /** @param {HTMLInputElement} note */
  function noteInputRow(note) {
    return el('div', {class: 'field words-note'}, [note]);
  }

  /** The glued spelling's reading: one word or separate words, with the counts that decided.
   * @param {Card} made @param {NonNullable<QueueRow['glued']>} glued */
  function gluedBlock(made, glued) {
    /** @type {HTMLElement[]} */
    const buttons = [];
    /** @param {'whole' | 'pieces'} value @param {string} text */
    const button = (value, text) => {
      const node = el('button', {type: 'button', role: 'radio', 'aria-checked': 'false',
        class: 'words-option words-split', 'data-split': value, 'data-help': 'words.glued',
        onclick: () => {
          made.split = made.split === value ? null : value;
          for (const each of buttons) {
            const on = made.split === each.getAttribute('data-split');
            each.setAttribute('aria-checked', String(on));
            each.classList.toggle('on', on);
          }
        }}, [el('span', {class: 'words-number'}),
        el('span', {class: 'words-option-text'}, [el('strong', {text})])]);
      buttons.push(node);
      return node;
    };
    return el('div', {class: 'words-glued'}, [
      el('h3', {text: 'One word or separate words'}),
      el('p', {text: glued.because}),
      el('p', {class: 'faint', text: `Ingence reads it as ${glued.reading === 'whole' ? 'one word' : 'separate words'}`
        + `${glued.asked ? ' and asks you' : ''}.`}),
      el('div', {class: 'words-options', role: 'radiogroup', 'aria-label': 'One word or separate words'}, [
        button('whole', `One word: ${glued.written}`),
        button('pieces', `Separate words: ${glued.pieces.join(', ')}`)]),
    ]);
  }

  /** The kind buttons of one spelling, in the order the engine offers them.
   * @param {QueueVariant} variant */
  function variantChoices(variant) {
    /** @type {Array<{answer: 'same' | 'kind' | 'unrelated', text: string, key: string}>} */
    const all = [
      {answer: 'same', text: 'The same thing', key: 'S'},
      {answer: 'kind', text: 'Different things of one kind', key: 'K'},
      {answer: 'unrelated', text: 'Unrelated', key: 'U'},
    ];
    return variant.offer === 'kind' ? [all[1], all[0], all[2]] : all;
  }

  /** Draw the spellings that may be this word again, each with its three answers.
   * @param {Card} made @param {HTMLElement} box */
  function redrawVariants(made, box) {
    const row = made.row;
    fill(box, ...askedVariants(row).map((variant) => {
      const given = made.variants.get(variant.spelling);
      const reason = [variant.reason_said ? variant.reason_said.charAt(0).toUpperCase()
        + variant.reason_said.slice(1) : '',
      variant.files > 0 ? `in ${count(variant.files, 'file')}` : 'no name here writes it',
      variant.known_as ? `the Dictionary knows it as ${spaced(variant.known_as)}` : ''].filter(Boolean).join(' · ');
      const node = el('div', {class: 'words-variant', tabindex: '0', 'data-spelling': variant.spelling,
        'data-offer': variant.offer, role: 'group', 'aria-label': `${variant.spelling}: ${reason}`}, [
        el('p', {class: 'words-variant-head'}, [el('code', {text: variant.spelling}), ' ',
          el('span', {class: 'faint', text: reason})]),
        el('div', {class: 'words-variant-answers', role: 'radiogroup', 'aria-label': `Is ${variant.spelling} the same word?`},
          variantChoices(variant).map((choice) => el('button', {type: 'button', role: 'radio',
            'aria-checked': String(Boolean(given && given.answer === choice.answer)),
            class: given && given.answer === choice.answer ? 'words-variant-answer on' : 'words-variant-answer',
            'data-answer': choice.answer, 'data-help': `words.${choice.answer}`,
            onclick: () => answerVariant(made, variant, choice.answer, false)},
          [choice.text, ' ', el('kbd', {text: choice.key})]))),
        given && given.answer === 'kind' && variant.known_as
          ? el('p', {class: 'faint words-variant-hint', text: `Say above what "${row.word}" means: a different `
            + `thing of the same kind as "${variant.spelling}". Ingence saves nothing for "${variant.spelling}", which the Dictionary knows.`})
          : null,
        given && given.answer === 'kind' && !variant.known_as ? el('div', {class: 'field words-variant-field'}, [
          el('label', {text: 'its meaning'}),
          el('input', {type: 'text', class: 'words-variant-meaning', value: given.meaning,
            'aria-label': `What ${variant.spelling} means, of the same kind`, 'data-help': 'words.kind_meaning',
            oninput: (event) => {
              const held = made.variants.get(variant.spelling);
              if (held) held.meaning = /** @type {HTMLInputElement} */ (event.target).value;
            }})]) : null,
      ]);
      node.addEventListener('keydown', (event) => {
        if (event.target !== node) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          const beside = event.key === 'ArrowDown' ? node.nextElementSibling : node.previousElementSibling;
          if (beside instanceof HTMLElement && beside.classList.contains('words-variant')) {
            event.preventDefault();
            beside.focus();
          }
        }
      });
      return node;
    }));
  }

  /** Give a spelling an answer (or take it back by giving the same one again).
   * @param {Card} made @param {QueueVariant} variant
   * @param {'same' | 'kind' | 'unrelated'} answer @param {boolean} moveOn */
  function answerVariant(made, variant, answer, moveOn) {
    const held = made.variants.get(variant.spelling);
    if (held && held.answer === answer) made.variants.delete(variant.spelling);
    else made.variants.set(variant.spelling, {answer, meaning: held && held.meaning ? held.meaning : variant.spelling});
    made.redrawVariants();
    const spelling = variant.spelling;
    const again = /** @type {HTMLElement | null} */ (made.card.querySelector(`.words-variant[data-spelling="${CSS.escape(spelling)}"]`));
    const next = moveOn && again ? again.nextElementSibling : null;
    const target = next instanceof HTMLElement && next.classList.contains('words-variant') ? next : again;
    if (target) target.focus({preventScroll: true});
    if (answer === 'kind' && made.variants.has(spelling) && !moveOn) {
      const input = made.card.querySelector(`.words-variant[data-spelling="${CSS.escape(spelling)}"] .words-variant-meaning`);
      if (input instanceof HTMLInputElement) { input.focus(); input.select(); }
    }
  }

  /* ------------------------------------------------------------------ recording */

  /** What the define row holds now.
   * @param {Card} made @returns {{meaning: string, kind: string, below: string | null}} */
  function readOwn(made) {
    const meaning = made.card.querySelector('.define-term');
    const kind = made.card.querySelector('.define-kind');
    const where = made.card.querySelector('.define-where');
    const place = where instanceof HTMLSelectElement ? where.value : 'everywhere';
    const folders = foldersOf(made.row);
    const below = place === 'where_it_stands' ? (folders[0] ? folders[0].path : null)
      : place.startsWith('below:') ? place.slice('below:'.length) : null;
    return {meaning: meaning instanceof HTMLInputElement ? meaning.value : '',
      kind: kind instanceof HTMLSelectElement ? kind.value : '', below};
  }

  /** @param {string} words @param {'refused' | 'kept' | ''} tone */
  function say(words, tone) {
    if (!C) return;
    C.status.textContent = words;
    C.status.className = `note words-status${tone ? ` ${tone}` : ''}`;
  }

  /** Whether the person is working in the card by keyboard: focus is inside it, or nowhere in
   * particular (a click on a button leaves it on the page).
   * @param {Card} made */
  function cardHasFocus(made) {
    const active = document.activeElement;
    return !active || active === document.body || made.card.contains(active);
  }

  /** Write the card's answers as one batch, then open the next word. */
  async function submit() {
    const made = C;
    if (!made || made.busy) return;
    const built = batchFor({row: made.row, choice: made.choice, own: readOwn(made),
      variants: made.variants, split: made.split, note: made.note.value, near: made.near});
    if (built.problem) {
      say(built.problem, 'refused');
      return;
    }
    const row = made.row;
    const keyboard = cardHasFocus(made);
    if (built.confirmed && !built.decisions.length) {
      W.skipped = W.skipped.includes(row.word) ? W.skipped : [...W.skipped, row.word];
      await moveOn(row, `"${row.word}" already means ${spaced(row.senses[0] ? row.senses[0].term : '')}, `
        + 'so Ingence saved nothing.', false, keyboard);
      return;
    }
    if (!(await askActor())) {
      say('Ingence saves each answer under a name. Set your name first.', 'refused');
      return;
    }
    made.busy = true;
    for (const button of made.card.querySelectorAll('button')) button.disabled = true;
    say('Saving to the answer log...', '');
    try {
      const result = await write('/api/review/answer', {actor: me.actor, decisions: built.decisions});
      const written = /** @type {Array<{target: string}>} */ (Array.isArray(result.written) ? result.written : []);
      const refused = /** @type {Array<{target: string, error: string}>} */ (result.refused || []);
      const asked = made.choice && made.choice.type === 'cannot_say';
      let flash = asked
        ? `Noted by ${me.actor}: you can’t say what "${row.word}" means. The word stays as it is.`
        : recordedWords(row.word, me.actor, written.map((one) => String(one.target)));
      if (refused.length) {
        flash += ` ${count(refused.length, 'answer')} refused: `
          + [...new Set(refused.map((one) => one.error))].slice(0, 3).join(' / ');
      }
      if (asked) W.skipped = W.skipped.includes(row.word) ? W.skipped : [...W.skipped, row.word];
      // The word's own answer was refused: it stays open (a glued word is named by its reading, and
      // what it means is still said on the word).
      const own = new Set([row.target, termTarget(row.word)]);
      const headRefused = refused.some((one) => own.has(one.target));
      await moveOn(row, flash, headRefused, keyboard);
    } catch (error) {
      made.busy = false;
      for (const button of made.card.querySelectorAll('button')) button.disabled = false;
      say(String((error && /** @type {any} */ (error).message) || error), 'refused');
    }
  }

  /** Read the queue again and open what follows the word: the next waiting word in the order
   * shown, or the same word again where its own answer was refused.
   * @param {QueueRow} row @param {string} flash @param {boolean} [stay] @param {boolean} [hadFocus] */
  async function moveOn(row, flash, stay = false, hadFocus = false) {
    const before = W.rows.map((one) => one.target);
    const turn = turns.claim(listPane);
    const data = await read();
    if (!turns.owns(listPane, turn) || state.view !== 'words') return;
    W.data = data;
    arrange();
    W.shownOrder = W.rows.map((one) => one.target);
    drawList();
    const same = stay ? W.rows.find((one) => one.target === row.target) : undefined;
    const next = same || rowAfter(row.target, before);
    if (next) {
      await openWord(next, {focus: hadFocus, flash});
      advance.focus(listPane);
    } else {
      advance.done(detailPane, 'You have seen every word in this list.', flash);
      if (W.skipped.length) {
        detailPane.append(el('button', {class: 'link', type: 'button', text: 'the skipped words again',
          onclick: () => { W.skipped = []; show().catch(reportError); }}));
      }
    }
  }

  /** The "Withdraw" control of an answered word. A click in a row is the button's own, never the row's.
   * @param {QueueRow} row */
  function withdrawButton(row) {
    return el('button', {type: 'button', class: 'link words-withdraw', 'data-help': 'words.withdraw',
      'aria-label': `Withdraw the answer about ${row.word}`, text: 'Withdraw',
      onclick: (event) => { event.stopPropagation(); withdraw(row).catch(reportError); }});
  }

  /** Withdraw the answer in force on a word, after asking in words. Nothing is deleted: the Record
   * keeps the answer and marks it withdrawn, and the withdrawal waits for Re-run like every answer.
   * @param {QueueRow} row */
  async function withdraw(row) {
    const asked = withdrawBody(row, me.actor);
    if (!asked) return;
    const sure = await dialogs.choose({title: `Withdraw the answer about "${row.word}"?`,
      text: withdrawWords(row, me.actor), yes: 'Withdraw', no: 'Keep it'});
    if (!sure) return;
    if (!(await askActor())) return;
    try {
      await write('/api/review/answer', {...asked, actor: me.actor});
    } catch (error) {
      const status = Number(error && /** @type {any} */ (error).status) || 0;
      await dialogs.tell({title: 'Ingence could not withdraw the answer',
        text: withdrawRefusal(status, String((error && /** @type {any} */ (error).message) || error))});
      // The engine answered, so the page it read is not the Record's now: read the queue again.
      if (status !== 0) show().catch(reportError);
      return;
    }
    await afterWithdraw(row);
  }

  /** Read the queue again after a withdrawal, and open the word: it is back in the list, its earlier
   * answer (if it had one) in force again.
   * @param {QueueRow} row */
  async function afterWithdraw(row) {
    const turn = turns.claim(listPane);
    const data = await read();
    if (!turns.owns(listPane, turn) || state.view !== 'words') return;
    W.data = data;
    arrange();
    W.shownOrder = W.rows.map((one) => one.target);
    drawList();
    const back = W.rows.find((one) => one.word === row.word);
    const flash = withdrawnWords(row, me.actor);
    if (back) await openWord(back, {flash});
    else {
      // A run that already read the answer reads the word as understood until Re-run reads it again
      // without it, so the word is not among the words the engine is unsure of yet.
      advance.done(detailPane, `The answer about "${row.word}" is withdrawn. The last scan still reads `
        + 'the word as understood. It comes back to this list after Re-run. Until then, '
        + '"Show words it understood" lists it.', flash);
    }
  }

  /** Skip the word: nothing is written, and it moves to the end of the list.
   * @param {QueueRow} row */
  function skip(row) {
    const before = W.rows.map((one) => one.target);
    if (!W.skipped.includes(row.word)) W.skipped = [...W.skipped, row.word];
    const hadFocus = C === null || cardHasFocus(C);
    const next = (() => {
      const after = new Set(before.slice(before.indexOf(row.target) + 1));
      arrange();
      return W.rows.find((one) => after.has(one.target) && !one.answered)
        || W.rows.find((one) => one.target !== row.target && !one.answered) || null;
    })();
    W.shownOrder = W.rows.map((one) => one.target);
    drawList();
    if (next) {
      openWord(next, {focus: hadFocus, flash: `Skipped "${row.word}". Ingence saved nothing and moved it to the end.`})
        .catch(reportError);
    } else {
      advance.done(detailPane, 'You have seen every word in this list.', `Skipped "${row.word}".`);
    }
  }

  /* ------------------------------------------------------------------ the keys */

  /** @param {Element} target */
  function typingInto(target) {
    return (target instanceof HTMLElement && target.isContentEditable)
      || target.matches('input:not([type="checkbox"]):not([type="radio"]):not([type="button"]), textarea, select');
  }

  /** Choose the meaning with this number (1 to 9).
   * @param {number} at */
  function pickNumber(at) {
    if (!C) return;
    const button = C.card.querySelector(`.words-option[data-number="${at}"]`);
    if (button instanceof HTMLElement) button.click();
    if (button instanceof HTMLElement && button.getAttribute('data-choice') === 'own') {
      const input = C.card.querySelector('.define-term');
      if (input instanceof HTMLInputElement) input.focus();
    }
  }

  /** The spelling the next S, K or U answers: the one focused, else the last one answered, else the first.
   * @param {Card} made @param {Element} target @returns {QueueVariant | null} */
  function focusedVariant(made, target) {
    const node = target.closest('.words-variant');
    const named = node instanceof HTMLElement ? node.dataset.spelling : '';
    const asked = askedVariants(made.row);
    const found = asked.find((one) => one.spelling === named);
    return found || asked.find((one) => !made.variants.has(one.spelling)) || asked[0] || null;
  }

  /** @param {KeyboardEvent} event */
  function onKey(event) {
    if (state.view !== 'words' || !C || event.defaultPrevented) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (!(target instanceof Element) || !target.closest('.words-card')) return;
    if (document.querySelector('dialog[open]')) return;
    if (typingInto(target)) {
      // Enter in the words a person types records; every other key is the field's own.
      if (event.key === 'Enter' && target.matches('.define-term, .words-variant-meaning, .note-input')) {
        event.preventDefault();
        submit().catch(reportError);
      }
      return;
    }
    const key = event.key;
    if (/^[1-9]$/.test(key) && !event.shiftKey) {
      event.preventDefault();
      pickNumber(Number(key));
    } else if (key === 's' || key === 'S' || key === 'k' || key === 'K' || key === 'u' || key === 'U') {
      const variant = focusedVariant(C, target);
      if (!variant) return;
      event.preventDefault();
      const answer = key.toLowerCase() === 's' ? 'same' : key.toLowerCase() === 'k' ? 'kind' : 'unrelated';
      answerVariant(C, variant, answer, true);
    } else if (key === 'n' || key === 'N') {
      event.preventDefault();
      skip(C.row);
    } else if (key === 'Enter') {
      // On a control the key presses (a link, a summary, a plain button) it is the control's own.
      if (target.matches('.words-option') && target.getAttribute('aria-checked') !== 'true') {
        // On a meaning not chosen yet, Enter chooses it; a second Enter records.
        event.preventDefault();
        /** @type {HTMLElement} */ (target).click();
      } else if (target === C.card || target.matches('.words-option, .words-variant')) {
        event.preventDefault();
        submit().catch(reportError);
      }
    }
  }
  document.addEventListener('keydown', onKey);

  /* ------------------------------------------------------------------ the view */

  async function show() {
    const turn = turns.claim(listPane);
    states.loading(listPane, 'Loading the words...');
    /** @type {QueueWords} */
    let data;
    try {
      data = await read();
    } catch (error) {
      if (turns.owns(listPane, turn)) states.failed(listPane, error, () => show().catch(reportError));
      return;
    }
    if (!turns.owns(listPane, turn) || state.view !== 'words') return;
    // A page past the end (rows answered since) goes back to the last page that has rows.
    if (!(data.rows || []).length && W.offset && Number(data.total) > 0) {
      W.offset = Math.max(0, Math.floor((Number(data.total) - 1) / PAGE_SIZE) * PAGE_SIZE);
      await show();
      return;
    }
    W.data = data;
    arrange();
    const before = W.shownOrder;
    W.shownOrder = W.rows.map((one) => one.target);
    drawList();
    const kept = W.rows.find((one) => one.target === W.at);
    const next = kept || rowAfter(W.at, before) || null;
    if (next) await openWord(next, {});
    else states.empty(detailPane, 'No word to answer here.');
  }

  global.wordsPage.show = show;
  VIEWS.words = show;
  REVIEW_VIEWS.add('words');
  OWN_DETAIL.add('words');
  // Teaching words does not wait for a rule to have run: the names are read already.
  if (typeof STAYS_WITH_CENSUS !== 'undefined') STAYS_WITH_CENSUS.add('words');
  // map.js sends the address #words to the Map. Deleting that alias makes #words open this page.
  if (typeof VIEW_ALIASES !== 'undefined' && VIEW_ALIASES.words) delete VIEW_ALIASES.words;
  // A Re-run that ends has read the names again: the meter and the list say the new run's counts.
  // The header's banner says "done" once the job is over; the run the queue names says whether it
  // is a new one, so a queue read for that is made only then, and only once at a time.
  let rereading = false;
  /** The words of the "done" banner already looked at: the banner stands until the next job, and a
   * run is looked for once.
   * @type {string} */
  let checkedBanner = '';
  onRefresh(() => {
    const banner = document.getElementById('applying');
    if (state.view !== 'words' || !W.data || rereading || !banner || banner.hidden
      || !banner.classList.contains('done')) return;
    const said = banner.textContent || '';
    if (said === checkedBanner) return;
    checkedBanner = said;
    rereading = true;
    read().then((data) => {
      if (state.view === 'words' && W.data && data.run_id !== W.data.run_id) show().catch(reportError);
    }, () => null).finally(() => { rereading = false; });
  });
  // Leaving the page forgets nothing a person chose in the list: the toggles and the skipped
  // words stay for the session, the card starts afresh.
  onLeave((next) => { if (next !== 'words') C = null; });
})(typeof window !== 'undefined' ? window : globalThis);
