// @ts-check
/* Keyboard paths that no one page owns.
 *
 * Every page is reached with Tab, in the order of the page, with a visible ring on the control that
 * has focus, and Escape closes what a key opened. Most of that is the pages' own work and is
 * checked by the keyboard tests. What belongs to no single page is here:
 *
 * 1. The skip link is the page's first focus stop. It moves focus to the main region (so the long
 *    tables of Engine can be skipped) without changing the address, which names the view.
 * 2. The keys of every page: ``/`` focuses the search box, ``1`` to ``6`` open the six pages, ``R``
 *    focuses the Re-run bar (it never runs anything), ``?`` opens a sheet listing every shortcut of
 *    every view, and ``Esc`` closes a sheet, a drawer or a menu. None of them fires while a text
 *    field has focus, with a modifier held (Ctrl-R reloads), while a sheet is open, or while focus
 *    is inside the Graph workspace, which has keys of its own. The digits also stand aside inside
 *    anything marked ``data-own-digits`` (the Words card, where ``1`` to ``9`` choose a meaning).
 * 3. The header is sticky. A control the browser scrolls to when Tab or Shift+Tab moves focus (into
 *    something above the window's top, for example) could land under the header, where its ring is
 *    hidden. While Tab is down, the page's scroll padding is as tall as the header, so focus is
 *    scrolled to just below it. The padding is held only for that key press: set on the Tab key
 *    going down, released when any key comes up or the pointer is pressed. A page-wide padding
 *    would also move where the browser scrolls a control a mouse is about to click.
 * 4. Escape closes the Graph page's "Choose another folder or file" box, from inside it, and gives
 *    focus back to the line that opened it.
 *
 * A page that opens a drawer or a menu of its own marks it ``data-esc-close`` and gives it a
 * descendant ``data-close`` (its Close control). Escape, pressed outside a text field, clicks that
 * control on the last such element that is showing, and nothing else.
 *
 * This file stands alone. It reads nothing from the other scripts but ``window.dialogs`` (at the
 * moment the sheet opens) and ``window.ingenceHelp`` (the help's point mode, offered on the
 * sheet). */

(() => {
  /** The keyboard table, in full: what each view's keys do. The per-view keys are built by the
   * views; this is the one list a person can read. ``keys`` are the keys as a person presses them;
   * each is a separate key unless it says "then".
   * @type {{view: string, rows: {keys: string[], what: string}[]}[]} */
  const SHORTCUTS = [
    {view: 'Every page', rows: [
      {keys: ['/'], what: 'Focus the Ask box'},
      {keys: ['1', '2', '3', '4', '5', '6'], what: 'Open Overview, Overlap, Experiments, Checks, History or Advanced'},
      {keys: ['R'], what: 'Move focus to Re-run. It never starts by itself'},
      {keys: ['?'], what: 'Show this list'},
      {keys: ['Esc'], what: 'Close a panel, a drawer or a menu, or clear the chosen rows'},
    ]},
    {view: 'Overlap', rows: [
      {keys: ['↑', '↓'], what: 'Walk the rows'},
      {keys: ['→'], what: 'Open a row, one scale down'},
      {keys: ['←', 'Backspace'], what: 'Up one scale'},
      {keys: ['Enter'], what: 'Show the inspector for the row'},
      {keys: ['W'], what: 'The Wheel'},
      {keys: ['O'], what: 'Stability'},
      {keys: ['N'], what: 'The relations of the row'},
      {keys: ['Home', 'End'], what: 'The first or the last row'},
      {keys: ['+', '-'], what: 'Zoom the axis'},
      {keys: ['Ctrl', 'scroll'], what: 'Zoom the axis (or pinch on a trackpad)'},
      {keys: ['0'], what: 'Reset the zoom'},
      {keys: ['Space'], what: 'Pick the row (a theme or an experiment) for a collection. Shift-click does the same'},
    ]},
    {view: 'Overlap › files one by one', rows: [
      {keys: ['↑', '↓'], what: 'Walk the rows'},
      {keys: ['Enter'], what: 'Open the file’s record'},
      {keys: ['T'], what: 'Teach a word from the record’s parts (opens the parts as a list)'},
    ]},
    {view: 'Advanced › Words', rows: [
      {keys: ['1', '2', '3', '4', '5', '6', '7', '8', '9'], what: 'In the word’s card, choose that meaning. There, keys 1 to 6 do not open pages'},
      {keys: ['S'], what: 'Answer the focused spelling: the same thing'},
      {keys: ['K'], what: 'Answer the focused spelling: different things of one kind'},
      {keys: ['U'], what: 'Answer the focused spelling: unrelated'},
      {keys: ['↑', '↓'], what: 'On a spelling, move to the one beside it'},
      {keys: ['Enter'], what: 'Save the word’s answers and open the next word. On a meaning not chosen yet, choose it first'},
      {keys: ['N'], what: 'Skip the word. Nothing is saved, and the word moves to the end'},
    ]},
    {view: 'Advanced › Questions', rows: [
      {keys: ['Space'], what: 'Choose the row, or clear it'},
      {keys: ['Shift', '↑ ↓'], what: 'Choose more rows'},
      {keys: ['A'], what: 'Focus the first answer button of the row'},
      {keys: ['Enter'], what: 'Open the row’s card'},
      {keys: ['Esc'], what: 'Clear the choice'},
    ]},
    {view: 'Advanced › Map of groups', rows: [
      {keys: ['←', '→', '↑', '↓'], what: 'The nearest point in that direction'},
      {keys: ['Enter'], what: 'Open the point’s card'},
      {keys: ['+', '-'], what: 'Zoom'},
      {keys: ['0'], what: 'Reset the zoom'},
      {keys: ['L'], what: 'Show or hide the labels'},
      {keys: ['Shift', 'click'], what: 'Pick the point for a collection. Shift and drag a box to pick every point in it'},
    ]},
    {view: 'Advanced › Connections and Lineage', rows: [
      {keys: ['Tab'], what: 'Inside the plot: the next item'},
      {keys: ['Enter'], what: 'Center on the item'},
      {keys: ['F'], what: 'Fit the plot to the window'},
      {keys: ['Ctrl', 'scroll'], what: 'Zoom the plot (or pinch on a trackpad)'},
      {keys: ['D'], what: 'Change the drawing: Families, Files, Grid'},
    ]},
    {view: 'Advanced › Rules', rows: [
      {keys: ['Enter'], what: 'On a group’s header, fold or unfold it. On a rule, open its card'},
      {keys: ['C'], what: 'Copy the card’s revision text'},
    ]},
    {view: 'Landing page', rows: [
      {keys: ['Enter'], what: 'In Data folder or on Check, check the folder. On Start scan, start the scan'},
    ]},
  ];

  /** Whether a key press is meant for a text field (or anything a person types into).
   * @param {EventTarget | null} target */
  function typingInto(target) {
    if (!(target instanceof HTMLElement)) return false;
    return target.isContentEditable
      || target.matches('input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]), textarea, select');
  }

  /** Whether a sheet (a modal dialog) is open: it owns the keys while it is.
   * @returns {boolean} */
  function sheetOpen() {
    return document.querySelector('dialog[open]') !== null;
  }

  /** A line said to a screen reader and shown nowhere (the one live region of these keys).
   * @param {string} words */
  function say(words) {
    let region = document.getElementById('keys-status');
    if (!region) {
      region = document.createElement('p');
      region.id = 'keys-status';
      region.setAttribute('role', 'status');
      region.setAttribute('aria-live', 'polite');
      region.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;';
      document.body.append(region);
    }
    region.textContent = '';
    window.setTimeout(() => { if (region) region.textContent = words; }, 30);
  }

  /** ``/``: the Ask box. */
  function focusSearch() {
    const box = document.getElementById('search-box');
    if (box instanceof HTMLElement) box.focus();
  }

  /** ``1`` to ``6``: the page, as its tab would open it.
   * @param {number} number */
  function openPage(number) {
    const tabs = [...document.querySelectorAll('#pages button')];
    const tab = tabs[number - 1];
    if (tab instanceof HTMLElement) tab.click();
  }

  /** ``R``: focus on Re-run (or on the count beside it), never a press. Where nothing waits,
   * Re-run is off and cannot take focus, so the key says so.
   * @returns {void} */
  function focusRerun() {
    const button = document.getElementById('rerun-button');
    if (button instanceof HTMLButtonElement && !button.disabled) {
      button.focus();
      return;
    }
    const badge = document.getElementById('rerun-badge');
    if (badge instanceof HTMLElement && !badge.hidden) {
      badge.focus();
      return;
    }
    say('Nothing waits for Re-run, so Re-run is off.');
  }

  /** The key's own table, one per view, as the sheet shows it.
   * @param {{view: string, rows: {keys: string[], what: string}[]}} group
   * @returns {HTMLElement} */
  function tableOf(group) {
    const table = document.createElement('table');
    table.className = 'shortcut-table';
    const caption = document.createElement('caption');
    caption.textContent = group.view;
    table.append(caption);
    for (const row of group.rows) {
      const line = document.createElement('tr');
      const keys = document.createElement('th');
      keys.scope = 'row';
      row.keys.forEach((key, at) => {
        if (at) keys.append(document.createTextNode(' '));
        const cap = document.createElement('kbd');
        cap.textContent = key;
        keys.append(cap);
      });
      const what = document.createElement('td');
      what.textContent = row.what;
      line.append(keys, what);
      table.append(line);
    }
    return table;
  }

  /** ``?``: the sheet listing every shortcut, of every view.
   * @returns {Promise<void>} */
  async function openShortcuts() {
    const dialogs = /** @type {any} */ (window).dialogs;
    if (!dialogs || sheetOpen()) return;
    const choice = await dialogs.open({
      title: 'Keyboard shortcuts', eyebrow: 'Help', className: 'sheet shortcuts-sheet',
      text: 'A shortcut does nothing while you are typing in a field.',
      build: (/** @type {(value: unknown) => void} */ finish) => {
        const scroll = document.createElement('div');
        scroll.className = 'sheet-scroll';
        scroll.tabIndex = 0;
        scroll.setAttribute('role', 'region');
        scroll.setAttribute('aria-label', 'Every shortcut');
        scroll.append(...SHORTCUTS.map(tableOf));
        const actions = document.createElement('p');
        actions.className = 'sheet-actions';
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'sheet-button primary';
        close.textContent = 'Close';
        close.setAttribute('data-close', '');
        close.setAttribute('data-autofocus', '');
        close.addEventListener('click', () => finish(null));
        const point = document.createElement('button');
        point.type = 'button';
        point.className = 'sheet-button';
        point.id = 'shortcuts-point';
        point.textContent = 'Point out every control on this page';
        point.addEventListener('click', () => finish('point'));
        // The first visit's tour, again.
        const tour = document.createElement('button');
        tour.type = 'button';
        tour.className = 'sheet-button';
        tour.id = 'shortcuts-tour';
        tour.textContent = 'Show the tour';
        tour.addEventListener('click', () => finish('tour'));
        actions.append(close, point, tour);
        return [scroll, actions];
      },
    });
    const help = /** @type {any} */ (window).ingenceHelp;
    if (choice === 'point' && help && typeof help.point === 'function') help.point();
    if (choice === 'tour' && help && typeof help.tour === 'function') help.tour();
  }

  /** Escape on what a page opened without a dialog: anything a page marks ``data-esc-close`` (the
   * Re-run summary closes itself, in workspace.js, and a dialog closes with the browser's own
   * Escape). Returns whether it closed something.
   * @returns {boolean} */
  function closeWhatIsOpen() {
    const marked = [...document.querySelectorAll('[data-esc-close]')]
      .filter((each) => each instanceof HTMLElement && each.offsetParent !== null);
    const last = marked[marked.length - 1];
    const close = last ? last.querySelector('[data-close]') : null;
    if (close instanceof HTMLElement) {
      close.click();
      return true;
    }
    return false;
  }

  /** The keys of every page, on the document. */
  function onKey(/** @type {KeyboardEvent} */ event) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    if (typingInto(event.target)) return;
    // The Graph workspace (the React Explorer, ``.ix-host`` in the list pane) has keys of its own,
    // among them the digits: while focus is inside it, these stand aside.
    if (event.target instanceof Element && event.target.closest('.ix-host')) return;
    if (sheetOpen()) return;
    if (event.key === 'Escape') {
      if (closeWhatIsOpen()) event.preventDefault();
      return;
    }
    if (event.key === '/') {
      event.preventDefault();
      focusSearch();
    } else if (/^[1-6]$/.test(event.key) && !event.shiftKey) {
      // The Words card takes the digits for itself (words.js): they choose a meaning there.
      if (event.target instanceof Element && event.target.closest('[data-own-digits]')) return;
      event.preventDefault();
      openPage(Number(event.key));
    } else if (event.key === 'r' || event.key === 'R') {
      event.preventDefault();
      focusRerun();
    } else if (event.key === '?') {
      event.preventDefault();
      openShortcuts();
    }
  }

  /** The skip link: focus goes to the main region and the address stays as it is. */
  function skipLink() {
    const link = document.getElementById('skip-link');
    const main = document.getElementById('main');
    if (!(link instanceof HTMLElement) || !(main instanceof HTMLElement)) return;
    link.addEventListener('click', (event) => {
      event.preventDefault();
      main.focus();
      main.scrollIntoView({block: 'start'});
    });
  }

  /** Hold the page's scroll padding as tall as the sticky header while Tab moves focus. */
  function keepFocusClearOfTheHeader() {
    const header = document.querySelector('body > header');
    if (!(header instanceof HTMLElement)) return;
    const root = document.documentElement;
    const hold = () => {
      root.style.scrollPaddingTop = `${Math.ceil(header.getBoundingClientRect().height) + 8}px`;
    };
    const release = () => { root.style.scrollPaddingTop = ''; };
    // Capturing, so the padding is there before the browser moves focus (the key's own action).
    document.addEventListener('keydown', (event) => { if (event.key === 'Tab') hold(); }, true);
    document.addEventListener('keyup', release, true);
    document.addEventListener('pointerdown', release, true);
    window.addEventListener('blur', release);
  }

  /** Escape inside the Graph page's "Choose another folder or file" box closes it. */
  function closeTheChooser(/** @type {KeyboardEvent} */ event) {
    const target = event.target;
    if (event.key !== 'Escape' || !(target instanceof Element)) return;
    const chooser = target.closest('details[data-graph="choose"][open]');
    if (!(chooser instanceof HTMLDetailsElement)) return;
    chooser.open = false;
    const summary = chooser.querySelector('summary');
    if (summary instanceof HTMLElement) summary.focus();
  }

  document.addEventListener('keydown', closeTheChooser);
  document.addEventListener('keydown', onKey);
  document.addEventListener('DOMContentLoaded', () => {
    keepFocusClearOfTheHeader();
    skipLink();
    const help = document.getElementById('help-button');
    if (help) help.addEventListener('click', () => { openShortcuts(); });
  });

  /** For a test and for the views' own keys: the table, and the sheet's opener.
   * @type {any} */
  (window).ingenceKeys = {shortcuts: SHORTCUTS, open: openShortcuts};
})();
