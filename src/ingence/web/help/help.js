/* Help: hover a control to learn what it does; press "?" to have every control pointed out.
 *
 * Its words are in help/catalog.json beside it (local, shipped with the engine, never fetched).
 *
 * There are three modes:
 *
 * 1. Hover (or keyboard focus): any element carrying data-help="<key>" shows a small tooltip after
 *    a short pause -- a title and one or two plain sentences from the catalog -- and the element is
 *    described to a screen reader (aria-describedby). Nothing appears on a mere pass of the mouse;
 *    a pause of 450 ms is the usual threshold.
 * 2. Point (the button on the "?" sheet of shortcuts): every help-bearing control on the page gets
 *    a small numbered marker, and a side list explains each number. Choosing one draws a callout
 *    with an arrow pointing at the control. A page may also declare a tour (catalog "tours"): the
 *    same callouts one after another, Next and Back, for a first visit.
 * 3. The first-visit tour: four steps over the Overview (what was found, completeness, Ask,
 *    assumptions and checks), with Skip, Back, Next and Done. It starts by itself once
 *    (localStorage "ingence.tour.v1"), when the Overview's first step is on the screen and nothing
 *    else is open: no dialog, and no quick checks (a container marked data-quick-checks; the page
 *    that shows them dispatches "ingence:checks-done" when they close). "Show the tour" on the "?"
 *    sheet starts it again.
 *
 * Words come only from the catalog, written in plain words, and every Ingence term a tooltip uses
 * is one the glossary defines. A key the catalog lacks shows nothing and logs the key once, so a
 * missing explanation is found in review, not by a user.
 *
 * No framework, no build step; respects prefers-reduced-motion. */

(function helpSystem() {
  const DELAY_MS = 450;
  let catalog = {entries: {}, tours: {}};
  let tip = null;
  let timer = null;
  const missing = new Set();

  function words(key) {
    const entry = catalog.entries[key];
    if (!entry && !missing.has(key)) {
      missing.add(key);
      console.warn(`help: no catalog entry for "${key}"`);
    }
    return entry || null;
  }

  function ensureTip() {
    if (tip) return tip;
    tip = document.createElement('div');
    tip.id = 'help-tip';
    tip.setAttribute('role', 'tooltip');
    tip.className = 'help-tip';
    tip.hidden = true;
    document.body.append(tip);
    return tip;
  }

  function place(box, target) {
    const rect = target.getBoundingClientRect();
    const below = rect.bottom + 8 + box.offsetHeight < window.innerHeight;
    box.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - box.offsetWidth - 8))}px`;
    box.style.top = `${below ? rect.bottom + 8 : rect.top - box.offsetHeight - 8}px`;
    box.dataset.side = below ? 'below' : 'above';
  }

  function show(target) {
    const entry = words(target.dataset.help);
    if (!entry) return;
    const box = ensureTip();
    box.replaceChildren();
    const title = document.createElement('strong');
    title.textContent = entry.title;
    const body = document.createElement('p');
    body.textContent = entry.text;
    box.append(title, body);
    if (entry.shortcut) {
      const key = document.createElement('kbd');
      key.textContent = entry.shortcut;
      box.append(key);
    }
    box.hidden = false;
    place(box, target);
    target.setAttribute('aria-describedby', 'help-tip');
  }

  function hide(target) {
    clearTimeout(timer);
    if (tip) tip.hidden = true;
    if (target) target.removeAttribute('aria-describedby');
  }

  function helpTarget(node) {
    return node && node.closest ? node.closest('[data-help]') : null;
  }

  document.addEventListener('pointerover', (event) => {
    const target = helpTarget(event.target);
    if (!target) return;
    clearTimeout(timer);
    timer = setTimeout(() => show(target), DELAY_MS);
  });
  document.addEventListener('pointerout', (event) => hide(helpTarget(event.target)));
  document.addEventListener('focusin', (event) => {
    const target = helpTarget(event.target);
    if (target) show(target);
  });
  document.addEventListener('focusout', (event) => hide(helpTarget(event.target)));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { hide(null); stopPointing(); }
    // "?" opens the sheet of shortcuts (keys.js), which offers point mode.
  });

  /* ---- point mode: numbered markers and a list explaining each ---------------------------- */

  let pointing = null;

  function stopPointing() {
    if (!pointing) return;
    pointing.forEach((node) => node.remove());
    pointing = null;
    document.body.classList.remove('help-pointing');
  }

  function callout(target, entry, number) {
    const box = document.createElement('div');
    box.className = 'help-callout';
    const heading = document.createElement('strong');
    heading.textContent = `${number}. ${entry.title}`;
    const text = document.createElement('p');
    text.textContent = entry.text;
    box.append(heading, text);
    const arrow = document.createElement('div');
    arrow.className = 'help-arrow';
    placeCallout(box, arrow, target);
    return [box, arrow];
  }

  /* A callout beside its control, and its arrow between them; placed again when the page scrolls
   * under a tour. A control too near the right edge has its callout below it. */
  function placeCallout(box, arrow, target) {
    const rect = target.getBoundingClientRect();
    const width = Math.min(300, window.innerWidth - 16);
    const left = Math.min(rect.right + 16, window.innerWidth - width);
    if (left > rect.right) {
      box.style.left = `${left}px`;
      box.style.top = `${Math.max(8, rect.top - 4)}px`;
      arrow.hidden = false;
      arrow.style.left = `${rect.right + 2}px`;
      arrow.style.top = `${rect.top + rect.height / 2 - 1}px`;
      arrow.style.width = `${Math.max(8, left - rect.right - 2)}px`;
      return;
    }
    box.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
    box.style.top = `${Math.max(8, Math.min(window.innerHeight - 160, rect.bottom + 12))}px`;
    arrow.hidden = true;
  }

  /* Whether a person can see and press a control right now: inside the window and not covered (a
   * control scrolled up under the sticky header is not). A marker for one that cannot be seen
   * would sit over the header, on top of another control's marker. */
  function inView(node) {
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0
      || rect.top >= window.innerHeight || rect.left >= window.innerWidth) return false;
    const x = Math.min(window.innerWidth - 1, Math.max(0, rect.left + rect.width / 2));
    const y = Math.min(window.innerHeight - 1, Math.max(0, rect.top + rect.height / 2));
    const top = document.elementFromPoint(x, y);
    return top !== null && (node === top || node.contains(top) || top.contains(node));
  }

  function togglePointing() {
    if (pointing) { stopPointing(); return; }
    pointing = [];
    document.body.classList.add('help-pointing');
    const targets = [...document.querySelectorAll('[data-help]')]
      .filter((node) => node.offsetParent !== null && words(node.dataset.help) && inView(node));
    targets.forEach((target, index) => {
      const rect = target.getBoundingClientRect();
      const marker = document.createElement('button');
      marker.type = 'button';
      marker.className = 'help-marker';
      marker.textContent = String(index + 1);
      marker.style.left = `${rect.left - 10}px`;
      marker.style.top = `${rect.top - 10}px`;
      marker.setAttribute('aria-label', `Explain: ${words(target.dataset.help).title}`);
      let open = [];
      marker.addEventListener('click', () => {
        open.forEach((node) => node.remove());
        if (open.length) { open = []; return; }
        open = callout(target, words(target.dataset.help), index + 1);
        open.forEach((node) => document.body.append(node));
        pointing.push(...open);
      });
      document.body.append(marker);
      pointing.push(marker);
    });
  }

  /* ---- tours ------------------------------------------------------------------------------- */

  const TOUR_SEEN = 'ingence.tour.v1';
  /* Started on this page already (where storage is refused, it starts once a page). */
  let tourStarted = false;
  let touring = null;

  function reducedMotion() {
    return Boolean(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function tourSeen() {
    try {
      return window.localStorage.getItem(TOUR_SEEN) !== null;
    } catch (error) {
      return tourStarted;
    }
  }

  function markTourSeen() {
    tourStarted = true;
    try {
      window.localStorage.setItem(TOUR_SEEN, 'seen');
    } catch (error) {
      /* Storage refused (a private window): it starts once on this page. */
    }
  }

  /* The control a tour step points at, only while it is on the page shown: connected and drawn
   * (a hidden section or another page's control has no boxes). */
  function anchorOf(key) {
    const node = document.querySelector(`[data-help="${key}"]`);
    return node && node.isConnected && node.getClientRects().length > 0 ? node : null;
  }

  /* The steps whose control is on the page and has words, in the catalog's order: [key, entry]. */
  function tourSteps(name) {
    return (catalog.tours[name] || [])
      .map((key) => [key, words(key)])
      .filter(([key, entry]) => entry && anchorOf(key));
  }

  /* A tour: one callout a step, with Skip, Back, and Next (Done on the last). Escape is Skip.
   * With reduced motion the page jumps to each step, and nothing moves. Returns whether it started
   * (false where none of its controls is on the page). */
  window.ingenceTour = function tour(name = 'first_visit') {
    if (touring) touring.close();
    const steps = tourSteps(name);
    if (!steps.length) return false;
    const returnTo = document.activeElement;
    let at = 0;
    let shown = [];
    /* A step shows only while its anchor is on the page shown, and never floats over another
     * section: when the anchor leaves (the page changed, the section is hidden), the step goes to
     * the next one that is on the page, or the tour ends. A redraw that brings the anchor back
     * under the same key keeps the step and puts the callout beside the new control. */
    let anchor = null;
    let watching = 0;
    const place = () => {
      if (!shown.length) return;
      const node = anchorOf(steps[at][0]);
      if (!node) { check(); return; }
      anchor = node;
      placeCallout(shown[0], shown[1], node);
    };
    function check() {
      watching = 0;
      if (!shown.length) return;
      const here = anchorOf(steps[at][0]);
      if (here === anchor) return;
      if (here) { place(); return; }
      const rest = steps.findIndex(([key], index) => index > at && anchorOf(key));
      if (rest >= 0) { at = rest; step(); return; }
      close();
    }
    const watch = new MutationObserver(() => {
      if (!watching) watching = requestAnimationFrame(check);
    });
    window.addEventListener('hashchange', check);
    function onKey(event) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      close();
    }
    function close() {
      shown.forEach((node) => node.remove());
      shown = [];
      touring = null;
      watch.disconnect();
      window.removeEventListener('hashchange', check);
      if (watching) cancelAnimationFrame(watching);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('scroll', place);
      window.removeEventListener('resize', place);
      if (returnTo && returnTo.isConnected && typeof returnTo.focus === 'function') returnTo.focus();
    }
    function step() {
      shown.forEach((node) => node.remove());
      const [key, entry] = steps[at];
      const node = anchorOf(key);
      if (!node) { close(); return; }
      anchor = node;
      node.scrollIntoView({block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth'});
      shown = callout(node, entry, at + 1);
      const box = shown[0];
      box.classList.add('help-tour');
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-label', `Tour, step ${at + 1} of ${steps.length}: ${entry.title}`);
      const count = document.createElement('p');
      count.className = 'help-tour-count';
      count.textContent = `Step ${at + 1} of ${steps.length}`;
      box.prepend(count);
      const controls = document.createElement('p');
      controls.className = 'help-tour-controls';
      const make = (label, action, extra) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.dataset.tour = label.toLowerCase();
        if (extra) button.className = extra;
        button.addEventListener('click', action);
        controls.append(button);
        return button;
      };
      make('Skip', close);
      const back = make('Back', () => { at -= 1; step(); });
      back.disabled = at === 0;
      const last = at === steps.length - 1;
      const next = make(last ? 'Done' : 'Next', () => {
        if (last) close();
        else { at += 1; step(); }
      }, 'primary');
      box.append(controls);
      shown.forEach((element) => document.body.append(element));
      next.focus({preventScroll: true});
    }
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', place, {passive: true});
    window.addEventListener('resize', place);
    touring = {close};
    watch.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'class', 'style']});
    step();
    return true;
  };

  /* Whether something a person must deal with first is open: a dialog, or the quick checks. */
  function somethingOpen() {
    if (document.querySelector('dialog[open], [aria-modal="true"]')) return true;
    return [...document.querySelectorAll('[data-quick-checks]')]
      .some((node) => !node.hidden && node.getClientRects().length > 0);
  }

  /* The first visit's tour starts by itself once: when its first step is on the page, after the
   * quick checks, with nothing else open. */
  function maybeStartTour() {
    if (touring || tourStarted || tourSeen()) return;
    const first = (catalog.tours.first_visit || [])[0];
    const node = first ? document.querySelector(`[data-help="${first}"]`) : null;
    if (!node || node.getClientRects().length === 0 || somethingOpen()) return;
    if (window.ingenceTour('first_visit')) markTourSeen();
  }
  document.addEventListener('ingence:checks-done', () => setTimeout(maybeStartTour, 0));

  /* "Show the tour" on the "?" sheet: the tour again, on the Overview, where its steps are. */
  function showTour() {
    markTourSeen();
    if (window.ingenceTour('first_visit')) return;
    try {
      // Only where the Dashboard has an Overview: a page without one is left as it is.
      const named = typeof window.viewNamed === 'function' ? window.viewNamed('overview') : '';
      if (!named) return;
      if (typeof window.navigate === 'function') window.navigate(named);
    } catch (error) {
      return;
    }
    let tries = 0;
    const again = () => {
      tries += 1;
      if (window.ingenceTour('first_visit') || tries > 20) return;
      setTimeout(again, 150);
    };
    setTimeout(again, 150);
  }

  /* Controls the pages build themselves get their key from the catalog's `auto` list, by
   * selector, so a page never has to carry one and a new control is explained by one line. */
  function applyAuto() {
    for (const rule of catalog.auto || []) {
      let nodes = [];
      try { nodes = document.querySelectorAll(rule.selector); } catch (error) { continue; }
      for (const node of nodes) if (!node.dataset.help) node.dataset.help = rule.key;
    }
  }

  /* The "?" key and the header's "?" open the sheet of every shortcut (keys.js); that sheet offers
   * "Point out every control on this page", which calls this. */
  window.ingenceHelp = {point: togglePointing, stop: stopPointing, tour: showTour};
  /* The markers are placed where each control is on the screen, so a scroll would leave them
   * pointing at the wrong thing: point mode closes, and "?" opens it again where the page is. */
  window.addEventListener('scroll', () => { hide(null); stopPointing(); }, {passive: true});
  let pending = null;
  new MutationObserver(() => {
    if (pending) return;
    pending = setTimeout(() => { pending = null; applyAuto(); maybeStartTour(); }, 100);
  }).observe(document.body, {childList: true, subtree: true});

  fetch('/help/catalog.json')
    .then((response) => (response.ok ? response.json() : catalog))
    .then((loaded) => { catalog = loaded; applyAuto(); maybeStartTour(); })
    .catch(() => {});
})();
