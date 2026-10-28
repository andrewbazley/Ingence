/* The lattice of group approvals, on the Groupings page, drawn as a cascade.
 *
 * A group is a set of files with common themes, and approving one confirms that words the engine
 * saw together are really linked. Approval flows only to what holds every word of it. The set most
 * experiments share is asked first, then each addition to it, never a question about a missing
 * word. A confirmed addition flags its own additions, and every experiment stays its own unit. A
 * run asks either through this lattice or word by word, so the section says which way the run
 * asked.
 *
 * Each most general set is a stack: the core at its base, as wide as the experiments holding it,
 * and each set adding to it a block stacked above, as wide as its share of the core's experiments,
 * its added words strong and the core's faint. A person confirms the base and sees at once what
 * each block adds. The stack's meter counts what is settled. Sets held by fewer experiments than
 * the run asks from are listed apart, not asked, with the number said.
 *
 * What it shows (``/api/v1/groups/lattice``): the run's shared sets of words, each with the
 * experiments holding it and its state. Approve and decline sit on the sets asked now, written
 * through the one answer route (the Record has one way in): on the run's own question where the run
 * raised it, so the Questions page marks it answered, and otherwise on the set's group target.
 *
 * A "How to read this" panel has a picture for the stacks, and the same for the Map and for the
 * compare panel (``window.ingenceGroups.howTo``). A tick on each set compares two to four of them,
 * with lines of work, in the panel ``GET /api/v1/groups/compare`` serves. "Show on the map" is
 * offered from each stack, and the way back from the Map opens a stack found and lit
 * (``window.ingenceGroups.pendingStack``). Nothing here is answered or written except through the
 * one answer route, and nothing opens by itself.
 *
 * Loaded by the Groupings page's hook in review.js, which calls ``window.groupLattice(holder)``
 * (the Map loads it too, for its panels). It reads the page's own helpers (``api``, ``write``,
 * ``el``, ``fill``, ``askActor``, ``me``), and brings its own stylesheet, ``groups.css``. */
'use strict';

(() => {
  /* Loaded by the Groupings page and by the Map (which wants its panels): once is enough, so the
   * choice a person ticked to compare is not lost to a second load. */
  if (window.groupLattice) return;
  /* Each state in plain words, and the tag style it is shown in. */
  const STATES = {
    approved: ['one group', 'tag now'],
    declined: ['not one group', 'tag plain'],
    asked: ['asked now', 'tag doubt'],
    waiting: ['waits for the group below', 'tag later'],
    declined_above: ['not asked: the group below was declined', 'tag plain'],
    not_asked: ['not asked: one experiment alone, or fewer experiments than the scan asks about',
      'tag plain'],
  };
  /* A set nothing can be answered on yet: it waits, or what it adds to was declined. */
  const QUIET = new Set(['waiting', 'declined_above']);
  /* What a person has settled: approved or declined, by them or through a set below. */
  const SETTLED = new Set(['approved', 'declined', 'declined_above']);
  /* How many stacks, and how many blocks in one stack, are shown before a "show more" (a real
   * folder can share many sets; each is still reachable). */
  const FIRST_SHOWN = 30;
  /* How far a stack is built: deeper additions are reached from the Questions page. */
  const DEEPEST = 12;
  const key = (terms) => terms.join(' ');
  /* The set answered last, so the redrawn stack shows it settling in place. */
  let justAnswered = null;
  /* What a person ticked to compare, by group (``words:<terms>`` or ``line:<n>``), in the order
   * ticked, which is the order of the marks A to D. Two to four are compared. */
  const chosen = new Map();
  const LEAST = 2;
  const MOST = 4;
  const BAR = '|';
  const MARKS = 'ABCD';
  /* The lines of work a person may add to the choice, read once per drawing of the page. */
  let lineChoices = null;
  /* What the tray of the page drawn now redraws, so a tick anywhere shows in it. */
  const trays = new Set();
  const specOf = (terms) => `words:${terms.join(BAR)}`;
  /* Words as the files wrote them: the lattice's words are a bound word's Dictionary term, and a
   * page shows each in the spelling most files hold. Every key, target and request stays the
   * lattice's word; only what is drawn is read through ``shown``. */
  const written = new Map();
  const shown = (word) => written.get(word) || word;
  const said = (terms) => terms.map(shown).join(', ');
  const shared = window.ingenceGroups = window.ingenceGroups || {};

  /* Topics: a stack can be picked, and the stacks picked made a topic with "Add to topic...". A
   * stack's core is a topic's member of the kind "group of words": the topic follows the words, so
   * an experiment that comes to carry them joins it. The pick is kept by the core's words across
   * the page's redraws (an answer redraws every stack). */
  const picked = new Map();
  let pickHolder = null;

  function drawPickBar() {
    if (!pickHolder || !window.topicKit) return;
    if (!picked.size) {
      pickHolder.replaceChildren();
      return;
    }
    pickHolder.replaceChildren(topicKit.pickBar({noun: 'word group', count: picked.size,
      onClear: () => {
        picked.clear();
        for (const box of document.querySelectorAll('.cascade .topic-pick input')) box.checked = false;
        for (const node of document.querySelectorAll('.cascade.topic-picked')) node.classList.remove('topic-picked');
        drawPickBar();
      },
      onAdd: async () => {
        const cores = [...picked.values()];
        const added = await topicKit.addTo({
          what: `${cores.length} word group${cores.length === 1 ? '' : 's'} (${cores.map((terms) => terms.join(' ')).join('; ')})`,
          members: cores.map((terms) => ({kind: 'core', terms}))});
        if (added) {
          picked.clear();
          if (pickHolder.parentElement) render(pickHolder.parentElement);
        }
      }}));
  }

  function pickBox(core) {
    const name = key(core.terms);
    const box = el('input', {type: 'checkbox', 'aria-label': `Pick the word group ${name} for a collection`});
    box.checked = picked.has(name);
    box.addEventListener('change', () => {
      if (box.checked) picked.set(name, [...core.terms]);
      else picked.delete(name);
      const stackNode = box.closest('.cascade');
      if (stackNode) stackNode.classList.toggle('topic-picked', box.checked);
      drawPickBar();
    });
    return el('label', {class: 'topic-pick', 'data-help': 'groups.pick'}, [box, 'Pick for a collection']);
  }

  (function stylesheet() {
    if (document.querySelector('link[href="/groups.css"]')) return;
    document.head.append(el('link', {rel: 'stylesheet', href: '/groups.css'}));
  })();

  function modeLine(found) {
    const lattice = found.mode === 'lattice';
    return el('p', {class: 'lattice-mode', 'data-help': 'groupings.lattice_mode'}, [
      'This scan asks ',
      el('strong', {text: lattice ? 'by word group' : 'word by word'}),
      lattice
        ? ': first the word group most experiments share, then each larger group once the group below is approved. '
        : ': each word that an experiment adds to or lacks from an approved group is its own question. '
          + 'Below is what Ingence would ask by word group. ',
      el('button', {class: 'link', type: 'button',
        text: `switch to ${lattice ? 'word by word' : 'by word group'} in the Rules section of Engine`,
        title: 'The setting is "derivations" on the rule group_resolved_across_experiments. The next '
          + 'scan asks the other way.',
        onclick: () => navigate('rules')}),
    ]);
  }

  async function answer(set, yes, status, holder) {
    if (!(await askActor())) return;
    const body = set.question
      ? {actor: me.actor, target: set.question, word: yes ? 'correct' : 'wrong'}
      : {actor: me.actor, target: set.target, word: yes ? 'accept' : 'refuse'};
    try {
      const written = await write('/api/review/answer', body);
      status.className = 'note';
      status.textContent = `Saved as answer ${written.sequence} by ${written.actor}: `
        + `"${set.terms.map(shown).join(' ')}" ${yes ? 'is' : 'is not'} one group.`;
      justAnswered = key(set.terms);
      render(holder);
    } catch (error) {
      status.className = 'note refused';
      status.textContent = String(error.message || error);
    }
  }

  /* A set's words: what it adds to, faint, then what it adds, strong, so each block says what it
   * adds; a core's words are all its own. */
  function words(set) {
    const added = set.base.length ? set.added : set.terms;
    return el('span', {class: 'cascade-words'}, [
      ...set.base.map((term) => el('span', {class: 'cascade-word', text: shown(term)})),
      ...added.map((term) => el('span', {class: 'cascade-word added',
        text: set.base.length ? `+ ${shown(term)}` : shown(term)})),
    ]);
  }

  function experimentsOf(set, core) {
    const count = set.experiments.length;
    const said = core && set !== core ? `${count} of ${core.experiments.length} experiments`
      : `${count} experiment${count === 1 ? '' : 's'}`;
    return el('details', {class: 'lattice-experiments'}, [
      el('summary', {text: said}),
      el('ul', {class: 'ids'}, set.experiments.map((path) => el('li', {}, [el('code', {text: path})]))),
    ]);
  }

  /* One block of a stack: its words, how many experiments hold it, its state, and the two answers
   * while it is asked. As wide as its share of the core's experiments. */
  function block(set, core, holder, answerable) {
    const [said, style] = STATES[set.state] || [set.state, 'tag plain'];
    const status = el('span', {class: 'note', role: 'status'});
    const share = core ? Math.max(set.experiments.length / core.experiments.length, 0) : 1;
    const decided = set.decided_by && set.decided_by !== set.answered_by
      ? el('span', {class: 'faint', text: ` by ${set.decided_by}`}) : null;
    /* An earlier answer is its own line: who gave it, as what words, read without a date's or a
     * repeat's word, and whom it reaches; the set is asked of the rest. */
    const partly = set.read_for.length < set.experiments.length;
    const earlier = set.answered_by ? el('div', {class: 'cascade-line faint cascade-earlier',
      text: `${set.answered_by} answered "${set.answered_as}" `
        + `${set.answered_yes ? 'one group' : 'not one group'}, ignoring `
        + `${set.read_without.join(' ')}`
        + (partly ? `, for ${set.read_for.length} of ${set.experiments.length}`
          + (set.state === 'asked' ? '; asked of the rest' : '') : '')}) : null;
    const buttons = set.state === 'asked' && answerable ? el('span', {class: 'answers'}, [
      /* The same words as every group question: one group, or not. */
      el('button', {class: 'answer', type: 'button', text: 'One group',
        onclick: () => answer(set, true, status, holder)}),
      el('button', {class: 'answer quiet', type: 'button', text: 'Not one group',
        onclick: () => answer(set, false, status, holder)}),
    ]) : null;
    /* One adding to several sets says what it adds to, all of it. */
    const several = set.parents.length > 1 ? el('span', {class: 'faint cascade-also',
      text: `adds to each of: ${set.parents.map((parent) => parent.map(shown).join(' ')).join('; ')}`}) : null;
    const settling = justAnswered === key(set.terms) ? ' settling' : '';
    /* Pointing at a block lights what it adds to, so a person sees which set it stands on. */
    const light = (on) => (event) => {
      const stack = event.currentTarget.closest('.cascade');
      for (const parent of set.parents) {
        const below = stack && stack.querySelector(
          `.cascade-block[data-terms="${CSS.escape(key(parent))}"]`);
        if (below) below.classList.toggle('lit', on);
      }
    };
    return el('li', {class: `lattice-set cascade-block state-${set.state}${settling}`,
      'data-terms': key(set.terms), style: `--f: ${Math.min(share, 1).toFixed(3)}`,
      onmouseenter: light(true), onmouseleave: light(false),
      onfocusin: light(true), onfocusout: light(false)}, [
      el('div', {class: 'cascade-line'}, [words(set), ' ',
        el('span', {class: style, text: said}), decided]),
      earlier,
      el('div', {class: 'cascade-line faint'}, [experimentsOf(set, core), several, compareBox(set.terms)]),
      buttons, status,
    ]);
  }

  /* A core and everything adding to it, row by row: each set one row above the first set it adds
   * to, so the core stands at the base and each addition stands on what it adds to. */
  function rows(core, children) {
    const found = [];
    const seen = new Set([key(core.terms)]);
    let level = [core];
    while (level.length && found.length <= DEEPEST) {
      found.push(level);
      const next = [];
      for (const set of level) {
        for (const child of children.get(key(set.terms)) || []) {
          if (seen.has(key(child.terms))) continue;
          seen.add(key(child.terms));
          next.push(child);
        }
      }
      level = next;
    }
    return found.reverse();
  }

  /* One row of a stack, its first blocks shown and the rest behind one button. */
  function row(sets, core, holder, answerable) {
    const list = el('ol', {class: 'cascade-row lattice-branch'},
      sets.slice(0, FIRST_SHOWN).map((set) => block(set, core, holder, answerable)));
    if (sets.length > FIRST_SHOWN) {
      const more = el('li', {class: 'cascade-more'}, [el('button', {class: 'link', type: 'button',
        text: `show ${sets.length - FIRST_SHOWN} more`, onclick: () => {
          more.replaceWith(...sets.slice(FIRST_SHOWN)
            .map((set) => block(set, core, holder, answerable)));
        }})]);
      list.append(more);
    }
    return list;
  }

  /* One stack: its rows above the base, and a meter of what is settled. */
  function stack(core, children, holder, answerable) {
    const built = rows(core, children);
    const every = built.flat();
    const settled = every.filter((set) => SETTLED.has(set.state)).length;
    const asked = every.filter((set) => set.state === 'asked').length;
    const meter = el('div', {class: 'cascade-meter', role: 'meter', 'aria-valuemin': '0',
      'aria-valuemax': String(every.length), 'aria-valuenow': String(settled),
      'aria-label': `${settled} of ${every.length} settled`}, [
      el('span', {class: 'cascade-fill',
        style: `width: ${(100 * settled / every.length).toFixed(1)}%`}),
    ]);
    /* What waits folds into one line: the rows on top whose every set waits for what it adds to are
     * one "N more wait for this one" above the first row with a question, opened on a tap, so a
     * stack of twenty additions stays as short as its questions. */
    let top = 0;
    while (top < built.length - 1 && built[top].every((set) => QUIET.has(set.state))) top += 1;
    const waiting = built.slice(0, top).flat().length;
    const fold = waiting ? el('details', {class: 'cascade-fold'}, [
      el('summary', {text: `${waiting} more wait for this one`}),
      ...built.slice(0, top).map((sets) => row(sets, core, holder, answerable)),
    ]) : null;
    return el('li', {class: `cascade${asked ? ' has-asked' : ''}${picked.has(key(core.terms)) ? ' topic-picked' : ''}`,
      'data-terms': key(core.terms)}, [
      el('div', {class: 'cascade-tools'}, [
        el('span', {class: 'faint', text: `${plural(core.experiments.length, 'experiment')} share ${said(core.terms)}`}),
        el('button', {type: 'button', class: 'link', 'data-help': 'groupings.show_on_map', text: 'Show on the map',
          onclick: () => (window.ingenceMap ? window.ingenceMap.show({fit: true, outlines: [{
            key: specOf(core.terms), label: said(core.terms), tag: '', folders: core.experiments,
            stack: core.terms}]}) : null)}),
        window.topicKit ? pickBox(core) : null,
      ]),
      el('div', {class: 'cascade-blocks'},
        [fold, ...built.slice(top).map((sets) => row(sets, core, holder, answerable))]),
      el('p', {class: 'cascade-score'}, [meter, el('span', {class: 'faint',
        text: `${settled} of ${every.length} settled${asked ? `, ${asked} asked now` : ''}`})]),
    ]);
  }

  /* The sets the run does not ask, each with how many experiments hold it. */
  function notAsked(sets, found) {
    if (!sets.length) return null;
    const fewest = found.minimum_experiments;
    return el('details', {class: 'cascade-unasked'}, [
      el('summary', {text: `Not asked: ${sets.length} word group${sets.length === 1 ? '' : 's'} `
        + `shared by fewer than ${fewest} experiments`}),
      el('ul', {class: 'ids'}, sets.slice(0, FIRST_SHOWN * 4).map((set) => el('li', {'data-terms': key(set.terms)}, [
        el('span', {text: set.terms.map(shown).join(' ')}),
        el('span', {class: 'faint', text: ` · ${set.experiments.length} experiments `}),
        compareBox(set.terms),
      ]))),
      sets.length > FIRST_SHOWN * 4
        ? el('p', {class: 'faint', text: `and ${sets.length - FIRST_SHOWN * 4} more`}) : null,
    ]);
  }


  /* ---------------------------------------------------------------- how to read this */

  /* A panel that explains a view with a picture of it. The picture is the view as the demo draws
   * it, taken by ``tools/docs/render_howto_pictures.py``. This panel draws the numbered marks over
   * the picture, at the places the tool measured (``help/pictures/positions.json``, as fractions of
   * the picture), so the words can be changed without taking the picture again. What each mark says
   * is in the help catalog (``pictures``). The panel is closed until a person opens it, and is
   * never a dialog: it opens in place. */
  let pictureWords = null;
  function pictures() {
    pictureWords = pictureWords || Promise.all([
      api('/help/catalog.json'),
      api('/help/pictures/positions.json').catch(() => ({})),
    ]).then(([catalog, positions]) => ({words: catalog.pictures || {}, positions}));
    return pictureWords;
  }

  async function fillHowTo(panel, name) {
    const {words, positions} = await pictures().catch(() => ({words: {}, positions: {}}));
    const entry = words[name];
    if (!entry) {
      fill(panel, el('p', {class: 'faint', text: 'The explanation of this view is missing from this installation.'}));
      return;
    }
    const where = (positions[name] || {}).marks || {};
    const marks = entry.marks.map((mark, index) => ({...mark, number: index + 1, at: where[mark.id] || null}));
    const picture = el('figure', {class: 'howto-figure'}, [
      el('a', {href: `/help/pictures/${entry.file}`, target: '_blank', rel: 'noopener',
        title: 'Open the picture at full size'}, [
        el('img', {src: `/help/pictures/${entry.file}`, alt: entry.alt, loading: 'lazy'})]),
      ...marks.filter((mark) => mark.at).map((mark) => el('span', {class: 'howto-mark', 'aria-hidden': 'true',
        style: `left: ${(100 * mark.at[0]).toFixed(2)}%; top: ${(100 * mark.at[1]).toFixed(2)}%`,
        text: String(mark.number)})),
    ]);
    fill(panel,
      el('p', {class: 'eyebrow', text: 'How to read this'}),
      el('h4', {class: 'howto-title', text: entry.title}),
      el('p', {text: entry.lead}),
      el('div', {class: 'howto-body'}, [
        picture,
        el('ol', {class: 'howto-list'}, marks.map((mark) => el('li', {value: String(mark.number)}, [
          el('strong', {text: `${mark.title}. `}), mark.text]))),
      ]),
      entry.note ? el('p', {class: 'faint howto-note', text: entry.note}) : null);
  }

  /* A link that opens the panel in place, and the panel itself (closed): the caller puts the pair
   * beside a heading. The words are read when it is first opened. */
  let howToCount = 0;
  function howTo(name, help) {
    howToCount += 1;
    const id = `howto-${name}-${howToCount}`;
    const panel = el('section', {class: 'howto', id, hidden: '', 'aria-label': 'How to read this'});
    let loaded = false;
    const toggle = el('button', {type: 'button', class: 'link howto-toggle', 'aria-expanded': 'false',
      'aria-controls': id, 'data-help': help, text: 'How to read this',
      onclick: () => {
        const open = toggle.getAttribute('aria-expanded') !== 'true';
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        panel.hidden = !open;
        if (open && !loaded) {
          loaded = true;
          fillHowTo(panel, name).catch(() => null);
        }
      }});
    return {toggle, panel};
  }

  /* ---------------------------------------------------------------- compare */

  /* A tick on a set, a line or a chip: the group is in the choice or not. */
  function pick(spec, label, on, said) {
    if (on && !chosen.has(spec) && chosen.size >= MOST) {
      said.textContent = `Compare ${MOST} groups at most. Remove one first.`;
      return false;
    }
    if (on) chosen.set(spec, label);
    else chosen.delete(spec);
    for (const box of document.querySelectorAll('.compare-box input')) {
      box.checked = chosen.has(box.dataset.spec);
    }
    for (const draw of [...trays]) draw();
    return true;
  }

  function compareBox(terms) {
    const spec = specOf(terms);
    const input = el('input', {type: 'checkbox', 'data-spec': spec, 'aria-label': `Compare ${said(terms)}`});
    input.checked = chosen.has(spec);
    input.addEventListener('change', () => {
      const note = document.querySelector('.compare-tray .compare-said') || {};
      if (!pick(spec, said(terms), input.checked, note)) input.checked = false;
    });
    return el('label', {class: 'compare-box faint', 'data-help': 'groupings.compare_box'}, [input, ' Compare']);
  }

  /* What is ticked, as chips with their marks, a way to add a line of work, and the button. */
  function compareTray(slot) {
    const bar = el('div', {class: 'compare-tray', role: 'region', 'aria-label': 'Groups to compare',
      'data-help': 'groupings.compare_tray', hidden: ''});
    const note = el('span', {class: 'compare-said note', role: 'status'});
    /* A tray left behind by an earlier drawing of the page forgets itself; one not yet placed does not. */
    let placed = false;
    let asked = false;
    const draw = () => {
      if (bar.isConnected) placed = true;
      else if (placed) {
        trays.delete(draw);
        return;
      }
      bar.hidden = chosen.size === 0;
      if (!chosen.size) return;
      /* The lines of work are read when the tray first appears, not with the page: they come from
       * the themes, which the Groupings page does not otherwise need. */
      if (!asked) {
        asked = true;
        api('/api/v1/themes/map?themes_only=true').then((found) => {
          lineChoices = found.themes.map((theme) => ({spec: `line:${theme.theme}`,
            label: theme.person_label || theme.label, experiments: theme.experiments}));
          draw();
        }).catch(() => null);
      }
      const picked = [...chosen];
      const adder = lineChoices && lineChoices.length ? el('select', {class: 'compare-add',
        'aria-label': 'Add a theme to compare', onchange: (event) => {
          const choice = lineChoices.find((line) => line.spec === event.target.value);
          if (choice) pick(choice.spec, choice.label, true, note);
        }}, [el('option', {value: '', text: 'Add a theme...'}),
        ...lineChoices.filter((line) => !chosen.has(line.spec)).map((line) => el('option',
          {value: line.spec, text: `${line.label} (${plural(line.experiments, 'experiment')})`}))]) : null;
      const compareButton = el('button', {type: 'button', class: 'answer',
        text: picked.length < LEAST ? `Tick ${LEAST - picked.length} more to compare` : `Compare ${picked.length} groups`,
        onclick: () => openCompare(slot, picked.map(([spec]) => spec), {host: 'groupings'})});
      compareButton.disabled = picked.length < LEAST;
      fill(bar,
        el('span', {class: 'faint', text: 'Compare:'}),
        ...picked.map(([spec, label], index) => el('span', {class: `compare-chip o${index}`}, [
          el('strong', {class: 'compare-letter', text: MARKS[index]}), ` ${label} `,
          el('button', {type: 'button', class: 'link', 'aria-label': `Remove ${label}`, text: 'remove',
            onclick: () => pick(spec, label, false, note)})])),
        adder,
        compareButton,
        el('button', {type: 'button', class: 'link', text: 'Clear', onclick: () => {
          for (const [spec] of picked) chosen.delete(spec);
          pick('', '', false, note);
          slot.hidden = true;
          slot.replaceChildren();
        }}),
        note);
    };
    trays.add(draw);
    lineChoices = null;
    draw();
    return bar;
  }

  const plural = (n, one, many) => `${Number(n).toLocaleString('en-US')} ${n === 1 ? one : (many || `${one}s`)}`;
  const outOf = (n, of) => `${Number(n).toLocaleString('en-US')} of ${Number(of).toLocaleString('en-US')}`;

  const wordList = (words, empty) => (words.length
    ? el('span', {class: 'compare-words'}, words.map((word) => el('span', {class: 'compare-word', text: word})))
    : el('span', {class: 'faint', text: empty}));

  /* The experiments of a group as one bar: those in every compared group, those in some, those only
   * this group has, each a share of the group's own experiments (a picture of the overlap). */
  function overlapBar(group, data) {
    const own = group.experiments.length;
    const only = group.experiments_only.length;
    const every = data.experiments_in_all.length;
    const some = Math.max(own - only - every, 0);
    const share = (n) => (own ? (100 * n / own).toFixed(1) : '0');
    return el('div', {class: 'compare-bar', role: 'img', 'aria-label': `${group.tag}: ${outOf(every, own)} experiments `
      + `are in every group, ${outOf(only, own)} only here`}, [
      el('span', {class: 'compare-seg all', style: `width: ${share(every)}%`}),
      el('span', {class: 'compare-seg some', style: `width: ${share(some)}%`}),
      el('span', {class: 'compare-seg only', style: `width: ${share(only)}%`}),
    ]);
  }

  const experimentItems = (list) => list.map((experiment) => el('li', {}, [el('code', {text: experiment.label}),
    el('span', {class: 'faint', text: ` · ${plural(experiment.files, 'file')}`})]));

  function listIn(title, list) {
    return el('details', {class: 'compare-list'}, [el('summary', {text: `${title} (${list.length})`}),
      list.length ? el('ul', {class: 'ids'}, experimentItems(list)) : el('p', {class: 'faint', text: 'None.'})]);
  }

  /* The comparison, drawn into ``holder``: the groups with their marks, then words, experiments and
   * files, each count with what it is out of. ``host`` is where it is shown: the Groupings page can
   * show it on the map; the Map is already there. */
  function drawCompare(holder, data, {host = 'groupings', onClose = null} = {}) {
    const groups = data.groups;
    /* The engine says how each word was written (``shown_as``); the stacks' own list fills the rest. */
    const show = (word) => (data.shown_as || {})[word] || shown(word);
    const many = groups.length > 2;
    const keys = groups.map((group, index) => el('div', {class: `compare-key o${index}`}, [
      el('strong', {class: 'compare-letter', text: group.tag}),
      el('span', {class: 'compare-name'}, [group.label,
        group.state ? el('span', {class: 'faint', text: ` · ${(STATES[group.state] || [group.state])[0]}`}) : null]),
      el('span', {class: 'faint', text: `${plural(group.experiments.length, 'experiment')}, ${plural(group.files, 'file')}`}),
    ]));
    const howto = howTo('compare', 'compare.howto');
    const body = el('div', {class: 'compare-body'}, [
      el('div', {class: 'groups-head'}, [el('h3', {text: `Comparing ${groups.length} groups`}), howto.toggle]),
      howto.panel,
      el('div', {class: 'compare-keys'}, keys),
      el('section', {class: 'compare-section', 'data-help': 'compare.words'}, [
        el('h4', {text: 'Words'}),
        el('p', {}, [`In all ${groups.length}: ${outOf(data.words_in_all.length, data.words_in_either)} words in any of them. `,
          wordList(data.words_in_all.map(show), 'none')]),
        ...groups.map((group) => el('p', {}, [`Only in ${group.tag}: `, wordList(group.words_only.map(show), 'none')])),
      ]),
      el('section', {class: 'compare-section', 'data-help': 'compare.experiments'}, [
        el('h4', {text: 'Experiments'}),
        el('p', {text: `In all ${groups.length}: ${outOf(data.experiments_in_all.length, data.experiments_in_either)} `
          + 'experiments in any of them.'}),
        ...groups.map((group) => el('div', {class: 'compare-row'}, [
          el('span', {class: 'compare-letter', text: group.tag}), overlapBar(group, data),
          el('span', {class: 'faint', text: `${outOf(group.experiments_only.length, group.experiments.length)} only here`})])),
        el('p', {class: 'faint compare-legend'}, [el('span', {class: 'compare-swatch all'}), ' in every group ',
          el('span', {class: 'compare-swatch some'}), ' in some ', el('span', {class: 'compare-swatch only'}), ' only here']),
        listIn(`In all ${groups.length}`, data.experiments_in_all),
        ...groups.map((group) => listIn(`Only in ${group.tag}`, group.experiments_only)),
      ]),
      el('section', {class: 'compare-section', 'data-help': 'compare.files'}, [
        el('h4', {text: 'Files'}),
        el('p', {text: `In the experiments in all ${groups.length}: ${outOf(data.files_in_all, data.files_in_either)} `
          + 'files in the experiments of any of them.'}),
        ...groups.map((group) => el('p', {text: `${group.tag}: ${plural(group.files, 'file')} in `
          + `${plural(group.experiments.length, 'experiment')}; ${outOf(group.files_only, group.files)} `
          + 'are in experiments only it holds.'})),
      ]),
      many ? el('section', {class: 'compare-section'}, [el('h4', {text: 'Each two'}),
        el('ul', {class: 'ids'}, data.pairs.map((pair) => {
          const tag = (key) => groups.find((group) => group.key === key).tag;
          return el('li', {text: `${tag(pair.first)} and ${tag(pair.second)}: `
            + `${outOf(pair.experiments_shared, pair.experiments_in_either)} experiments, `
            + `${outOf(pair.words_shared, pair.words_in_either)} words`});
        }))]) : null,
      el('div', {class: 'compare-actions'}, [
        host === 'groupings' ? el('button', {type: 'button', class: 'answer', 'data-help': 'compare.show_on_map',
          text: 'Show on the map', onclick: () => showOnMap(groups, data)}) : null,
        onClose ? el('button', {type: 'button', class: 'answer quiet', text: 'Close the comparison', onclick: onClose}) : null,
      ])]);
    fill(holder, body);
  }

  /* Ask for the comparison and draw it; ``onData`` hears the answer once (the Map outlines from it). */
  async function openCompare(holder, specs, options = {}) {
    holder.hidden = false;
    fill(holder, el('p', {class: 'faint', role: 'status', text: 'Comparing...'}));
    let data;
    try {
      data = await api(`/api/v1/groups/compare?${specs.map((spec) => `group=${encodeURIComponent(spec)}`).join('&')}`);
    } catch (error) {
      fill(holder, el('p', {class: 'note refused', role: 'alert', text: String(error.message || error)}));
      return null;
    }
    drawCompare(holder, data, options);
    if (options.onData) options.onData(data);
    return data;
  }

  /* The map, opened with the compared groups outlined (a mark and a dash of its own each) and fitted
   * to them; the comparison is carried over so the map shows it beside the outlines. */
  function showOnMap(groups, data) {
    if (!window.ingenceMap) return null;
    return window.ingenceMap.show({
      outlines: groups.map((group, index) => ({key: group.key, label: group.label, tag: group.tag,
        ids: group.experiments.map((experiment) => experiment.id),
        stack: group.kind === 'words' ? group.words : null, color: window.ingenceMap.outlineColor(index)})),
      fit: true,
      compare: data,
    });
  }

  /* ---------------------------------------------------------------- the way between stacks and the map */

  /* Open the stack (or the set listed apart) of these terms, found and lit, where the Map's "Show the
   * stack" sent a person: a stack past the first thirty, a block in a folded row and a set listed
   * apart are all reached. */
  function reveal(holder, terms) {
    const find = () => [...holder.querySelectorAll('[data-terms]')].find((node) => node.dataset.terms === terms);
    let found = find();
    for (let more = holder.querySelector('.cascades > li > button.link'); !found && more;
      more = holder.querySelector('.cascades > li > button.link')) {
      more.click();
      found = find();
    }
    if (!found) return false;
    for (let up = found.parentElement; up && up !== holder; up = up.parentElement) {
      if (up.tagName === 'DETAILS') up.open = true;
    }
    found.classList.add('revealed');
    found.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    return true;
  }

  Object.assign(shared, {howTo, fillHowTo, openCompare, drawCompare, revealStack: reveal});

  function render(holder) {
    api('/api/v1/groups/lattice').then((found) => {
      if (found.mode === 'none' || !found.resolving_rules_in_run) {
        /* No rule in the run resolves groups (the shipped Rulebook alone), so nothing is asked. */
        fill(holder, el('p', {class: 'faint', text: 'This scan suggests no groups of experiments.'}));
        return;
      }
      /* Each set sits under its first parent; a set adding to two is said so there. */
      const children = new Map();
      for (const set of found.sets) {
        if (!set.parents.length) continue;
        const under = key(set.parents[0]);
        if (!children.has(under)) children.set(under, []);
        children.get(under).push(set);
      }
      const roots = found.sets.filter((set) => !set.parents.length
        && (set.experiments.length > 1 || set.state !== 'not_asked'));
      /* A set held by too few experiments to ask is listed apart, with its number. */
      const stacked = roots.filter((set) => set.state !== 'not_asked' || found.bound_reached);
      const apart = roots.filter((set) => set.state === 'not_asked' && !found.bound_reached);
      /* Under the word-by-word way, the lattice is shown to compare, and nothing here is answered:
       * that run asks each word an experiment adds to or lacks from an approved set. */
      const answerable = found.mode === 'lattice';
      const asked = found.sets.filter((set) => set.state === 'asked').length;
      /* Each word as its files wrote it, before anything is drawn: the engine says it beside the
       * words (``terms_shown``, in order), and the stacks below are built through ``shown``. */
      written.clear();
      for (const set of found.sets) {
        set.terms.forEach((term, at) => {
          const shownAs = (set.terms_shown || [])[at];
          if (shownAs && shownAs !== term) written.set(term, shownAs);
        });
      }
      const stacks = el('ul', {class: 'cascades'},
        stacked.slice(0, FIRST_SHOWN).map((core) => stack(core, children, holder, answerable)));
      if (stacked.length > FIRST_SHOWN) {
        const more = el('li', {}, [el('button', {class: 'link', type: 'button',
          text: `show ${stacked.length - FIRST_SHOWN} more`, onclick: () => {
            more.replaceWith(...stacked.slice(FIRST_SHOWN)
              .map((core) => stack(core, children, holder, answerable)));
          }})]);
        stacks.append(more);
      }
      justAnswered = null;
      pickHolder = el('div', {class: 'topic-pickholder'});
      const howto = howTo('groupings_stacks', 'groupings.howto');
      const slot = el('div', {class: 'compare compare-slot', hidden: '', 'aria-live': 'polite'});
      fill(holder,
        el('div', {class: 'groups-head'}, [
          el('h3', {text: `Word groups that experiments share (${roots.length}), `
            + (answerable ? `${asked} asked now` : 'shown to compare')}),
          howto.toggle]),
        howto.panel,
        modeLine(found),
        pickHolder,
        el('p', {class: 'faint', text: 'Tick Compare on 2 to 4 word groups to see their words, experiments and files '
          + 'side by side. You can also add a theme.'}),
        compareTray(slot),
        slot,
        answerable ? null : el('p', {class: 'faint', text: 'Each word that an experiment adds to or '
          + 'lacks from an approved group is its own question in Questions, in Advanced. Nothing '
          + 'here is answered while the scan asks word by word.'}),
        found.bound_reached ? el('p', {class: 'note refused', text: `The scan stopped at `
          + `${found.maximum_concepts} word groups (the rule’s maximum_concepts), so nothing here is asked. `
          + 'These are the word groups of the experiments read first. Ingence still asks about its '
          + 'suggested groups of experiments. Raise the limit in the Rules section of Engine to see the rest.'}) : null,
        stacked.length ? stacks : el('p', {class: 'faint', text: roots.length
          ? 'No word group is shared by enough experiments to ask.'
          : 'No word group is shared by 2 experiments in this scan.'}),
        notAsked(apart, found),
      );
      drawPickBar();
      /* Sent here by the Map's "Show the stack": the stack opens found and lit. */
      if (shared.pendingStack) {
        const wanted = shared.pendingStack;
        shared.pendingStack = null;
        reveal(holder, wanted);
      }
    }).catch(() => null);
  }

  window.groupLattice = render;
})();
