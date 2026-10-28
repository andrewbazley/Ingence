// @ts-check
/* The Dashboard's dialogs: the one place the page asks a person something in a box.
 *
 * The page does not use the browser's own boxes (a prompt for a name, an alert for an error). Those
 * cannot be labeled, cannot say what is wrong with an answer before it is sent, and take focus
 * somewhere a keyboard cannot follow. Every dialog here:
 *
 * - is a <dialog> opened modally, labeled by its heading and described by its sentence;
 * - keeps focus inside itself while open (Tab and Shift+Tab go round its own controls), and gives
 *   it back to whatever had it when it closes;
 * - closes with Escape, which is the same as Cancel;
 * - checks an answer before it is sent (``validate`` returns the words saying what is wrong, or
 *   nothing), says so beside the field, and keeps the typing.
 *
 * Loaded first, before review.js and app.js, which call it. No framework and no build step. */

/**
 * @typedef {Node | string | null | undefined} Part
 * @typedef {{[name: string]: string | ((event: Event) => unknown)}} Attributes
 * @typedef {(value: unknown) => void} Finish
 */

window.dialogs = (() => {
  let opened = 0;

  /**
   * Make an element (the page's el(), kept here so this file stands on its own).
   * @param {string} tag
   * @param {Attributes} [attributes]
   * @param {Part | Part[]} [children]
   * @returns {HTMLElement}
   */
  function node(tag, attributes = {}, children = []) {
    const made = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (typeof value === 'function') made.addEventListener(name.slice(2), value);
      else if (name === 'class') made.className = value;
      else if (name === 'text') made.textContent = value;
      else made.setAttribute(name, value);
    }
    /** @type {Part[]} */
    const parts = Array.isArray(children) ? children : [children];
    for (const child of parts) {
      if (child == null) continue;
      made.append(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return made;
  }

  /** @param {HTMLElement} dialog */
  function focusable(dialog) {
    return /** @type {HTMLElement[]} */ ([...dialog.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')])
      .filter((item) => !(/** @type {HTMLButtonElement} */ (item)).disabled && !item.hidden
        && item.offsetParent !== null);
  }

  /**
   * Open one dialog and resolve with what closed it. ``build`` makes the body from the dialog's
   * own ``finish(value)``; Escape and Cancel finish with ``cancelled``.
   * @param {{title: string, text?: string, eyebrow?: string, build: (finish: Finish) => Part | Part[],
   *   cancelled?: unknown, className?: string}} options
   * @returns {Promise<any>}
   */
  function open({title, text, eyebrow = '', build, cancelled = null, className = ''}) {
    return new Promise((resolve) => {
      opened += 1;
      const id = `dialog-${opened}`;
      const before = /** @type {HTMLElement | null} */ (document.activeElement);
      const dialog = /** @type {HTMLDialogElement} */ (node('dialog', {
        class: `ingence-dialog ${className}`.trim(),
        'aria-labelledby': `${id}-title`, 'aria-describedby': `${id}-text`}));
      let done = false;
      /** @type {Finish} */
      const finish = (value) => {
        if (done) return;
        done = true;
        dialog.close();
        dialog.remove();
        if (before && typeof before.focus === 'function' && document.contains(before)) before.focus();
        resolve(value);
      };
      const built = build(finish);
      dialog.append(
        // A sheet may name what it is above its title (the eyebrow).
        ...(eyebrow ? [node('p', {class: 'eyebrow', text: eyebrow})] : []),
        node('h2', {id: `${id}-title`, text: title}),
        node('p', {id: `${id}-text`, class: 'dialog-text', text: text || ''}),
        ...(Array.isArray(built) ? built : [built]).filter((part) => part != null),
      );
      // Escape is Cancel: the browser's own cancel event, turned into the same answer.
      dialog.addEventListener('cancel', (event) => { event.preventDefault(); finish(cancelled); });
      // Focus stays inside while the dialog is open.
      dialog.addEventListener('keydown', (event) => {
        if (event.key !== 'Tab') return;
        const items = focusable(dialog);
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      });
      document.body.append(dialog);
      dialog.showModal();
      const start = /** @type {HTMLElement | null} */ (dialog.querySelector('[data-autofocus]'))
        || focusable(dialog)[0];
      if (start) start.focus();
    });
  }

  /**
   * Ask for a line of text. Resolves with the text as typed (trimmed), or null for Cancel. The
   * answer is checked by ``validate`` before it is accepted; an empty answer is refused unless
   * ``allowEmpty``.
   * @param {{title: string, text?: string, label?: string, value?: string, placeholder?: string,
   *   submit?: string, validate?: ((typed: string) => string) | null, allowEmpty?: boolean,
   *   multiline?: boolean}} options
   * @returns {Promise<string | null>}
   */
  function ask({title, text, label, value = '', placeholder = '', submit = 'Save',
    validate = null, allowEmpty = false, multiline = false}) {
    return open({title, text, build: (finish) => {
      const fieldId = `ask-${opened}`;
      const field = /** @type {HTMLInputElement | HTMLTextAreaElement} */ (node(
        multiline ? 'textarea' : 'input', {id: fieldId, 'data-autofocus': '',
          ...(multiline ? {rows: '4'} : {type: 'text'}), placeholder}));
      field.value = value;
      const problem = node('p', {class: 'dialog-problem', role: 'alert', id: `${fieldId}-problem`});
      const check = () => {
        const typed = field.value.trim();
        const said = !typed && !allowEmpty ? 'Write something first, or press Cancel.'
          : (validate ? validate(typed) : '') || '';
        problem.textContent = said;
        field.setAttribute('aria-invalid', said ? 'true' : 'false');
        if (said) field.setAttribute('aria-describedby', `${fieldId}-problem`);
        return said ? null : typed;
      };
      const form = node('form', {method: 'dialog', onsubmit: (event) => {
        event.preventDefault();
        const accepted = check();
        if (accepted === null) { field.focus(); return; }
        finish(accepted);
      }}, [
        node('label', {for: fieldId, text: label || title}),
        field,
        problem,
        node('p', {class: 'actions'}, [
          node('button', {type: 'button', text: 'Cancel', onclick: () => finish(null)}),
          node('button', {type: 'submit', class: 'primary', text: submit}),
        ]),
      ]);
      field.addEventListener('input', () => { if (problem.textContent) check(); });
      return form;
    }});
  }

  /**
   * Say something that went wrong, in the server's own words, with one button to close it.
   * @param {{title?: string, text: string}} options
   * @returns {Promise<void>}
   */
  function tell({title = 'That did not work', text}) {
    return open({title, text, cancelled: undefined, className: 'dialog-tell', build: (finish) =>
      node('p', {class: 'actions'}, [node('button', {type: 'button', class: 'primary',
        'data-autofocus': '', text: 'Close', onclick: () => finish(undefined)})])});
  }

  /**
   * Offer a choice of two, the second the one that acts. Resolves true only for that one; Escape
   * and the first resolve false. ``details`` are shown between the sentence and the buttons.
   * @param {{title: string, text: string, details?: Node[], yes?: string, no?: string}} options
   * @returns {Promise<boolean>}
   */
  function choose({title, text, details = [], yes = 'Continue', no = 'Cancel'}) {
    return open({title, text, cancelled: false, build: (finish) => [
      ...details,
      node('p', {class: 'actions'}, [
        node('button', {type: 'button', 'data-autofocus': '', text: no, onclick: () => finish(false)}),
        node('button', {type: 'button', class: 'primary', text: yes, onclick: () => finish(true)}),
      ]),
    ]});
  }

  /**
   * Say an error from any write in one box: the words the server gave, never a blank.
   * @param {unknown} error
   * @param {string} [title]
   */
  function failed(error, title) {
    const words = String((error instanceof Error ? error.message : error) || 'Something went '
      + 'wrong. The server gave no reason.');
    return tell({title: title || 'Ingence could not save this', text: words});
  }

  return {open, ask, tell, choose, failed};
})();
