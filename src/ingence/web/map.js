/* The Map of groups, a section of Advanced (Views).
 *
 * Each experiment is one dot, placed in the plane by the words its themes read
 * (``GET /api/v1/themes/map``: a seeded t-SNE, or the principal components above 6,000 experiments;
 * built once after each run and kept), so experiments that read alike lie close. The dot's color is
 * one measure at a time: its line of work, how far its raw data was followed by an analysis (the
 * Years view's ramp), or its year. Each theme's name stands above a dotted ring at the middle of
 * its experiments. A card at the right says what is known of the experiment clicked.
 *
 * Nothing here moves, merges or splits a point (those edits are not served), and nothing is drawn
 * at a place the engine did not give: an experiment with no place (``x`` and ``y`` null) is counted
 * under the plot and listed on asking, never drawn at 0,0.
 *
 * While the first read builds the map (about a minute on a large project), the plot's box says so,
 * in the engine's words, with three pulsing dots (never a percentage). It names the themes the
 * answer carries first (``?themes_only=true``, answered at once without drawing the layout). The
 * themes' centers come from that layout, so they are drawn only where the layout is already held.
 * Otherwise the names stand in a list until the whole answer arrives. A second visit is immediate.
 *
 * Outlines: ``window.ingenceMap.outline(ids, color, options)`` draws the outline of a set of
 * experiments, under the dots, and the legend toggles them. The compare overlay, "Show on the map"
 * and a person's topics all draw through it (see the section below).
 *
 * Drawn with SVG in plain JavaScript, no library. Dot sizes and the text stay the same size at any
 * zoom: a zoom moves the dots, it does not scale them. The plot is one tab stop: the arrow keys
 * move to the nearest dot in that direction, Enter opens its card, + and - zoom, 0 resets, L
 * toggles the names. The view registers itself as the ``map`` view and answers ``#words`` until
 * words.js takes that address. app.js's helpers (``el``, ``api``, ``fill``, ``states``, ``turns``,
 * ``navigate``, ``holdFocus``) and stability.js's ``stabilityAxis`` are used as they are. */
'use strict';

(() => {
  /* ---------------------------------------------------------------- words */

  /* The heading, the loading state, the unplaced line, and the two sentences that say what the map
   * cannot do. */
  const SAY = {
    head: (placed, n) => `${count(placed)} of ${count(n)} experiments placed by the words their themes read`,
    loading: (n) => `Placing ${count(n)} experiments…`,
    /* The words for this view's loading card. They do not say that the themes are placed first,
     * because no theme is placed until the layout is. */
    loadingBody: 'Ingence builds the map once after each scan and keeps it. '
      + 'The rows and the Wheel are ready meanwhile.',
    unplaced: (n) => `${count(n)} not placed: none of their words are among the words the themes read`,
    noMove: 'Nothing here can move, merge or split a point.',
    perExperiment: "Per-experiment stability is not served; the theme's is shown.",
    noExperiments: 'No experiments were found in what was read.',
  };

  /* The other words of this view. */
  const BOARD = {
    eyebrow: 'Map of groups',
    colorBy: 'Color by',
    measures: {line: 'theme', raw: 'raw data analyzed', year: 'year'},
    experiment: 'An experiment',
    listThem: 'list them',
    legendCenter: "a theme's center",
    openYears: 'Open in By year',
    relations: 'Relations (N)',
    meanwhile: 'Go to By year meanwhile',
    help: "Point at a dot for its name and theme, and click it for its card. Click a theme's name to keep its "
      + 'experiments and fade the rest. Drag to zoom, and double-click to reset.',
    nothingChosen: 'Click a point to read its card.',
    stability: "Its theme's stability",
    noTheme: 'in no theme',
    undated: 'undated',
  };

  const LEVEL_WORDS = {all: 'all raw data analyzed', most: 'most raw data analyzed',
    some: 'some raw data analyzed', none: 'no raw data analyzed', na: 'no raw data found'};
  const LEVELS = ['all', 'most', 'some', 'none', 'na'];

  const count = (n) => Number(n).toLocaleString('en-US');
  const plural = (n, one, many) => `${count(n)} ${n === 1 ? one : (many || `${one}s`)}`;

  /* ---------------------------------------------------------------- the model */

  /* How many lines have a hue of their own (map.css); the others share one neutral. */
  const HUES = 8;
  const DOT_MIN = 3.5;
  const DOT_MAX = 5;
  const PAD = 28;
  /* The dotted ring at a theme's center, in pixels. */
  const RING = 7;
  const MOST_ZOOM = 400;
  const STEP = 1.5;
  /* The most unplaced experiments listed; the rest are counted. */
  const LISTED = 200;
  /* A pointer that moves farther than this many pixels between press and release is a drag. */
  const DRAG = 5;
  /* The drawing's namespace, as the page's own parser gives it (no address is written here). */
  const SVG = (() => {
    const holder = document.createElement('template');
    holder.innerHTML = '<svg></svg>';
    return holder.content.firstElementChild.namespaceURI;
  })();

  const view = {
    measure: 'line', labels: true, kept: null, selected: null, cursor: null,
    data: null, points: [], themes: [], byId: new Map(), themeById: new Map(), hue: new Map(),
    z: {cx: 0.5, cy: 0.5, k: 1}, carded: null, w: 0, h: 0, observer: null, parts: null, years: null, dragged: false,
    /* The outlines asked for (by key), whether they are shown, a request waiting for the next
     * drawing (``show``), and who wants to hear when the map is drawn. */
    outlines: new Map(), outlinesShown: true, request: null, fitIds: null, compared: null, comparing: false, drawn: false, listeners: [], folderIndex: null,
  };

  function svgEl(tag, attributes = {}) {
    const node = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
    return node;
  }

  const themeName = (theme) => (theme ? (theme.person_label || theme.label) : '');

  /* The Years view's five words, from the same cutoffs: never a number in the main reading. */
  function levelOf(standing) {
    if (!standing || !standing.raw_acquisitions || standing.share_of_raw_followed_by_analysis == null) return 'na';
    const share = standing.share_of_raw_followed_by_analysis;
    if (share >= 0.95) return 'all';
    if (share >= 0.5) return 'most';
    if (share > 0.02) return 'some';
    return 'none';
  }

  function prepare(data) {
    view.data = data;
    view.themes = data.themes;
    view.themeById = new Map(data.themes.map((theme) => [theme.theme, theme]));
    /* A hue by rank: the line with the most experiments first, so a run's colors do not shift when a
     * small line is added. */
    const ranked = [...data.themes].sort((a, b) => b.experiments - a.experiments || a.theme - b.theme);
    view.hue = new Map(ranked.map((theme, index) => [theme.theme, index < HUES ? index : -1]));
    view.points = data.experiments.map((point) => ({...point, px: 0, py: 0, node: null}));
    view.byId = new Map(view.points.map((point) => [point.id, point]));
    /* Loops, not Math.min(...list): a spread of a very large list overflows the call stack. */
    let yearLow = Infinity;
    let yearHigh = -Infinity;
    let fewest = Infinity;
    let most = 0;
    for (const point of view.points) {
      if (point.year != null) {
        yearLow = Math.min(yearLow, point.year);
        yearHigh = Math.max(yearHigh, point.year);
      }
      fewest = Math.min(fewest, point.files);
      most = Math.max(most, point.files);
    }
    view.years = yearLow <= yearHigh ? {low: yearLow, high: yearHigh} : null;
    /* A dot is 7 to 10 pixels across: the fewest files the smallest, the most the largest, between
     * them on a log scale. */
    const low = Math.log1p(Number.isFinite(fewest) ? fewest : 0);
    const span = Math.log1p(most) - low;
    for (const point of view.points) {
      point.r = DOT_MIN + (DOT_MAX - DOT_MIN) * (span > 0 ? (Math.log1p(point.files) - low) / span : 0);
    }
  }

  const placedPoints = () => view.points.filter((point) => point.x != null && point.y != null);

  /* A color as CSS, or null where the value is unknown (drawn hatched, never as a color). */
  function hueVar(theme) {
    const hue = view.hue.get(theme);
    return hue == null ? null : (hue < 0 ? 'var(--map-other)' : `var(--map-c${hue})`);
  }

  function colorOf(point) {
    if (view.measure === 'line') return point.theme == null ? null : hueVar(point.theme);
    if (view.measure === 'raw') {
      const level = levelOf(point.standing);
      return level === 'na' ? null : `var(--map-${level})`;
    }
    if (point.year == null || !view.years) return null;
    const span = view.years.high - view.years.low;
    const share = span ? Math.round((100 * (point.year - view.years.low)) / span) : 100;
    return `color-mix(in srgb, var(--map-year-late) ${share}%, var(--map-year-early))`;
  }

  /* ---------------------------------------------------------------- the plot's geometry */

  const baseScale = () => Math.max(60, Math.min(view.w, view.h) - 2 * PAD);
  const scale = () => baseScale() * view.z.k;
  const screenX = (x) => view.w / 2 + (x - view.z.cx) * scale();
  const screenY = (y) => view.h / 2 + (y - view.z.cy) * scale();

  function reset() {
    view.z = {cx: 0.5, cy: 0.5, k: 1};
    layout();
  }

  /* Zoom to a rectangle given in pixels of the plot. */
  function zoomTo(left, top, right, bottom) {
    const s = scale();
    const x0 = view.z.cx + (left - view.w / 2) / s;
    const x1 = view.z.cx + (right - view.w / 2) / s;
    const y0 = view.z.cy + (top - view.h / 2) / s;
    const y1 = view.z.cy + (bottom - view.h / 2) / s;
    const next = 0.92 * Math.min(view.w / Math.max(x1 - x0, 1e-6), view.h / Math.max(y1 - y0, 1e-6)) / baseScale();
    view.z = {cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, k: Math.min(MOST_ZOOM, Math.max(1, next))};
    layout();
  }

  function zoomBy(factor) {
    const point = view.cursor ? view.byId.get(view.cursor) : null;
    const next = Math.min(MOST_ZOOM, Math.max(1, view.z.k * factor));
    if (next === 1) {
      reset();
      return;
    }
    if (point && point.x != null && factor > 1) {
      view.z.cx = point.x;
      view.z.cy = point.y;
    }
    view.z.k = next;
    layout();
  }

  /* ---------------------------------------------------------------- drawing the dots and names */

  function layout() {
    const parts = view.parts;
    if (!parts) return;
    for (const point of view.points) {
      if (point.node == null) continue;
      point.px = screenX(point.x);
      point.py = screenY(point.y);
      point.node.setAttribute('cx', point.px.toFixed(1));
      point.node.setAttribute('cy', point.py.toFixed(1));
    }
    placeNames();
    placeOutlines();
  }

  function paint() {
    for (const point of view.points) {
      if (point.node == null) continue;
      const color = colorOf(point);
      point.node.style.fill = color || 'url(#map-hatch)';
      point.node.classList.toggle('unknown', color == null);
      point.node.classList.toggle('faded', view.kept != null && point.theme !== view.kept);
    }
    for (const group of view.parts.names.querySelectorAll('.map-theme')) {
      const theme = view.themeById.get(Number(group.dataset.theme));
      const text = group.querySelector('text');
      text.style.fill = view.measure === 'line' && !theme.person_label ? hueVar(theme.theme) : '';
      group.classList.toggle('kept', view.kept === theme.theme);
      group.setAttribute('aria-pressed', view.kept === theme.theme ? 'true' : 'false');
    }
    view.parts.outlines.classList.toggle('hide-groups', !view.outlinesShown);
    view.parts.names.classList.toggle('hidden', !view.labels);
    view.parts.labelsButton.setAttribute('aria-pressed', view.labels ? 'true' : 'false');
  }

  function drawDots() {
    const dots = view.parts.dots;
    const fragment = document.createDocumentFragment();
    for (const point of placedPoints()) {
      const node = svgEl('circle', {r: point.r.toFixed(2), class: 'map-dot', 'data-id': point.id});
      point.node = node;
      fragment.append(node);
    }
    dots.replaceChildren(fragment);
  }

  /* One group per theme that has a center: a dotted ring at the center and the name on a
   * translucent ground. A name that would sit on another is moved down or up until it does not; the
   * ring stays at the center. */
  function drawNames() {
    const names = view.parts.names;
    names.replaceChildren();
    const ranked = [...view.themes].sort((a, b) => b.experiments - a.experiments || a.theme - b.theme);
    for (const theme of ranked) {
      if (theme.x == null || theme.y == null) continue;
      const group = svgEl('g', {class: 'map-theme', 'data-theme': theme.theme, role: 'button', tabindex: '-1',
        'aria-pressed': 'false', 'aria-label': `${themeName(theme)}: ${plural(theme.experiments, 'experiment')}`});
      const ring = svgEl('circle', {class: 'map-center', r: RING});
      const ground = svgEl('rect', {class: 'map-ground', rx: 4, height: 18});
      const text = svgEl('text', {class: theme.person_label ? 'map-text person' : 'map-text',
        'text-anchor': 'middle', 'dominant-baseline': 'central'});
      text.textContent = themeName(theme);
      group.append(ring, ground, text);
      names.append(group);
    }
  }

  function placeNames() {
    const names = view.parts.names;
    const placed = [];
    for (const group of names.querySelectorAll('.map-theme')) {
      const theme = view.themeById.get(Number(group.dataset.theme));
      const cx = screenX(theme.x);
      const cy = screenY(theme.y);
      const text = group.querySelector('text');
      const ground = group.querySelector('rect');
      const ring = group.querySelector('circle');
      let width = 0;
      try {
        width = text.getComputedTextLength();
      } catch (error) {
        width = 0;
      }
      if (!width) width = 7 * text.textContent.length;
      const box = {w: width + 12, h: 18};
      /* Above the center (9% of the plot's height), so the ring stays visible and the name covers
       * few of its own dots. */
      const base = cy - Math.max(RING + 4 + box.h, 0.09 * view.h) - box.h / 2;
      let top = base;
      for (let attempt = 0; attempt < 8; attempt += 1) {
        const clash = placed.some((other) => Math.abs(other.x - cx) < (other.w + box.w) / 2
          && Math.abs(other.y - (top + 9)) < box.h);
        if (!clash) break;
        const step = Math.ceil((attempt + 1) / 2) * 18;
        top = base + (attempt % 2 === 0 ? -step : step);
      }
      placed.push({x: cx, y: top + 9, w: box.w});
      ring.setAttribute('cx', cx.toFixed(1));
      ring.setAttribute('cy', cy.toFixed(1));
      ground.setAttribute('x', (cx - box.w / 2).toFixed(1));
      ground.setAttribute('y', top.toFixed(1));
      ground.setAttribute('width', box.w.toFixed(1));
      text.setAttribute('x', cx.toFixed(1));
      text.setAttribute('y', (top + 9).toFixed(1));
    }
    placeMarks();
  }

  /* The rings for the selected dot and the keyboard's. */
  function placeMarks() {
    const {chosen, cursor} = view.parts;
    for (const [mark, id] of [[chosen, view.selected], [cursor, view.cursor]]) {
      const point = id ? view.byId.get(id) : null;
      if (!point || point.node == null) {
        mark.setAttribute('visibility', 'hidden');
        continue;
      }
      mark.setAttribute('visibility', 'visible');
      mark.setAttribute('cx', point.px.toFixed(1));
      mark.setAttribute('cy', point.py.toFixed(1));
      mark.setAttribute('r', (point.r + (mark === chosen ? 4 : 6)).toFixed(1));
    }
  }


  /* ---------------------------------------------------------------- outlines */

  /* Outlines. One function draws the outline of a set of experiments over the map, under the dots:
   *
   *     window.ingenceMap.outline(ids, color, {key, label, tag, owner})
   *
   * ``ids`` are experiment ids (a map point's ``id``). ``color`` is any CSS color (the engine's own
   * are ``window.ingenceMap.outlineColor(n)``, never the magenta that marks a person's words).
   * ``key`` names the outline, so a second call with it replaces the first (without one, a key no
   * other outline has is made). ``label`` is what the legend says it is, and ``tag`` the short mark
   * (a letter) drawn at its edge. The function returns the key.
   *
   * ``owner`` is optional and says who an outline belongs to. The groups' own views (the compare
   * overlay, "Show on the map", a line's card) draw as owner ``groups``, the default. Everything
   * the Map's groups code does to outlines (replace, close, "Clear outlines", the card, the legend
   * line) acts only on outlines whose owner is ``groups``. Another owner (topics pass ``owner:
   * 'topics'``) has its outlines drawn here with the same shape, tag and overlap (overlaps are
   * shaded between outlines of one owner). That code never clears, lists or relabels them. Their
   * owner lists and removes them: ``clear(key)`` for one or ``clear(undefined, {owner})`` for all
   * of one owner (``clear()`` alone clears the owner ``groups``), and ``keys(owner)`` lists the
   * keys held.
   *
   * The outline is the smallest rounded shape that holds the dots of the experiments that have a
   * place on the map: the convex hull of their places, padded by OUTLINE_PAD pixels. It is the
   * shape of the places, not a boundary of the group, so another group's dot can lie inside it. An
   * id that is not a dot on this map, or whose experiment has no place, is not drawn and is counted
   * in the legend ("5 of 6 placed"). The function may be called before the map is drawn: it is held
   * and drawn with the map. ``window.ingenceMap.onDraw(f)`` calls ``f`` each time the map is drawn,
   * and at once if it is drawn now. It returns a function that stops the calls. */
  const OUTLINE_PAD = 13;
  /* The segments of a rounded corner. */
  const OUTLINE_ARC = 16;
  /* How many outline colors the engine has before they repeat (map.css: --map-o0 to --map-o3). */
  const OUTLINE_COLORS = 4;
  /* The owner of the groups' own outlines, and the count that makes keys no two outlines share. */
  const GROUPS = 'groups';
  let outlineSerial = 0;

  const crossOf = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

  /* The convex hull of [x, y] pairs (monotone chain), counter-clockwise on the page's numbers: a
   * repeated point counts once, one point is its own hull, and points on a line are the line's two
   * ends. */
  function convexHull(points) {
    const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const unique = sorted.filter((point, index) => index === 0
      || point[0] !== sorted[index - 1][0] || point[1] !== sorted[index - 1][1]);
    if (unique.length < 3) return unique;
    const lower = [];
    for (const point of unique) {
      while (lower.length >= 2 && crossOf(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop();
      lower.push(point);
    }
    const upper = [];
    for (let index = unique.length - 1; index >= 0; index -= 1) {
      const point = unique[index];
      while (upper.length >= 2 && crossOf(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop();
      upper.push(point);
    }
    lower.pop();
    upper.pop();
    return lower.concat(upper);
  }

  /* The hull of these points grown by ``pad`` all round: the hull of a small circle at each corner of
   * the hull, so one point is a circle and two are a capsule. Empty for no points. */
  function paddedOutline(points, pad = OUTLINE_PAD, arc = OUTLINE_ARC) {
    const around = [];
    for (const [x, y] of convexHull(points)) {
      for (let step = 0; step < arc; step += 1) {
        const angle = (2 * Math.PI * step) / arc;
        around.push([x + pad * Math.cos(angle), y + pad * Math.sin(angle)]);
      }
    }
    return convexHull(around);
  }

  /* The part of one convex polygon (as ``convexHull`` gives them) inside another: Sutherland-Hodgman,
   * empty where they do not meet. */
  function overlapOf(subject, clip) {
    let result = subject;
    for (let index = 0; index < clip.length && result.length; index += 1) {
      const a = clip[index];
      const b = clip[(index + 1) % clip.length];
      const input = result;
      result = [];
      for (let at = 0; at < input.length; at += 1) {
        const p = input[at];
        const q = input[(at + 1) % input.length];
        const pSide = crossOf(a, b, p);
        const qSide = crossOf(a, b, q);
        if (pSide >= 0) result.push(p);
        if ((pSide >= 0) !== (qSide >= 0)) {
          const t = pSide / (pSide - qSide);
          result.push([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]);
        }
      }
    }
    return result;
  }

  function areaOf(polygon) {
    let twice = 0;
    for (let index = 0; index < polygon.length; index += 1) {
      const a = polygon[index];
      const b = polygon[(index + 1) % polygon.length];
      twice += a[0] * b[1] - b[0] * a[1];
    }
    return Math.abs(twice) / 2;
  }

  const pathOf = (polygon) => (polygon.length
    ? `M${polygon.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join('L')}Z` : '');

  /* The engine's outline colors, in turn; a person's magenta is never one of them. */
  const outlineColor = (index) => `var(--map-o${((index % OUTLINE_COLORS) + OUTLINE_COLORS) % OUTLINE_COLORS})`;
  const outlineTag = (index) => String.fromCharCode(65 + (index % 26));

  /* The map's experiments by the folder they stand for, for a caller that names a group's
   * experiments by folder (the Groupings page does). A name that ends in " [id]" carries the id the
   * engine uses to tell two folders of one path apart. A name that is not a dot on this map (an
   * experiment the map does not draw) is not lost: it is counted, so a group keeps its size. */
  function idsOfFolders(folders) {
    if (!view.folderIndex) {
      view.folderIndex = new Map();
      for (const point of view.points) {
        if (!view.folderIndex.has(point.folder)) view.folderIndex.set(point.folder, []);
        view.folderIndex.get(point.folder).push(point.id);
      }
    }
    const ids = [];
    let missing = 0;
    for (const name of folders) {
      const tagged = /^(.*) \[([^\]]+)\]$/.exec(name);
      const found = tagged && view.byId.has(tagged[2])
        ? [tagged[2]] : (view.folderIndex.get(tagged ? tagged[1] : name) || []);
      if (found.length) ids.push(...found);
      else missing += 1;
    }
    return {ids, missing};
  }

  /* Which of an outline's experiments are dots with a place, and the hull of their places. */
  function resolveOutline(held) {
    const dots = held.ids.map((id) => view.byId.get(id)).filter((point) => point && point.node != null);
    held.placed = dots.length;
    held.hull = convexHull(dots.map((point) => [point.x, point.y]));
    held.dotIds = dots.map((point) => point.id);
  }

  const ownedBy = (owner) => [...view.outlines.values()].filter((held) => held.owner === owner);
  /* How many experiments an outline stands for: the ones it names, and those that are no dot here. */
  const totalOf = (held) => held.ids.length + held.missing;

  /* Take away every outline of one owner, and no other's. */
  function dropOutlines(owner) {
    for (const [key, held] of [...view.outlines]) if (held.owner === owner) view.outlines.delete(key);
  }

  function outline(ids, color, options = {}) {
    const owner = String(options.owner || GROUPS);
    const index = ownedBy(owner).length;
    outlineSerial += 1;
    let key = String(options.key || `${owner}-outline-${outlineSerial}`);
    /* A key another owner holds is never taken over: this owner's outline takes its name. */
    if (view.outlines.has(key) && view.outlines.get(key).owner !== owner) key = `${owner}:${key}`;
    const held = {
      key, owner, ids: [...new Set([...ids].map(String))], missing: Number(options.missing) || 0,
      color: color || outlineColor(index), label: options.label || key,
      tag: options.tag == null ? outlineTag(index) : String(options.tag),
      stack: options.stack || null, placed: 0, hull: [], dotIds: [],
    };
    /* A second call with a key replaces the first, keeping its place in the order. */
    view.outlines.set(key, held);
    if (view.drawn) {
      resolveOutline(held);
      if (!options.quiet) refreshOutlines();
    }
    return key;
  }

  /* One outline by its key, or every outline of an owner (the groups' own when none is named). */
  function clearOutline(key, {owner = GROUPS} = {}) {
    if (key == null) dropOutlines(owner);
    else view.outlines.delete(String(key));
    if (view.drawn) refreshOutlines();
  }

  const outlineKeys = (owner) => [...view.outlines.values()]
    .filter((held) => owner == null || held.owner === owner).map((held) => held.key);

  function refreshOutlines() {
    placeOutlines();
    legend();
    if (!view.carded && !view.comparing) showCard(null);
  }

  /* Rectangles in the plot that an outline's tag keeps clear of: the themes' names, where shown. */
  function nameBoxes() {
    if (!view.labels || !view.parts) return [];
    return [...view.parts.names.querySelectorAll('.map-ground')].map((ground) => ({
      x: Number(ground.getAttribute('x')), y: Number(ground.getAttribute('y')),
      w: Number(ground.getAttribute('width')), h: Number(ground.getAttribute('height'))}));
  }

  const meets = (a, b) => a.x < b.x + b.w + 2 && b.x < a.x + a.w + 2 && a.y < b.y + b.h + 2 && b.y < a.y + a.h + 2;

  /* Where an outline's tag goes: above its highest corner, else below its lowest, else beside its
   * ends, the first place that is inside the plot and sits on no name and no other tag. Where there
   * is none it stays above, moved aside from the tags already there. */
  function tagPlace(polygon, avoid) {
    const at = (pick) => polygon.reduce((best, point) => (pick(point, best) ? point : best), polygon[0]);
    const high = at((point, best) => point[1] < best[1]);
    const low = at((point, best) => point[1] > best[1]);
    const left = at((point, best) => point[0] < best[0]);
    const right = at((point, best) => point[0] > best[0]);
    const box = (x, y) => ({x, y, w: 22, h: 18});
    const tries = [box(high[0] - 11, high[1] - 22), box(low[0] - 11, low[1] + 4),
      box(left[0] - 26, left[1] - 9), box(right[0] + 4, right[1] - 9),
      box(high[0] + 15, high[1] - 22), box(high[0] - 37, high[1] - 22),
      box(low[0] + 15, low[1] + 4), box(low[0] - 37, low[1] + 4)];
    const inside = (b) => b.x >= 0 && b.y >= 0 && b.x + b.w <= view.w && b.y + b.h <= view.h;
    const found = tries.find((b) => inside(b) && !avoid.some((other) => meets(b, other)));
    if (found) return found;
    const first = tries[0];
    while (avoid.some((other) => other.tag && meets(first, other))) first.x += 26;
    return first;
  }

  /* Draw every outline at the dots' places now, and the overlaps of each two of one owner. Called
   * with each layout, so the shapes follow a zoom: the padding stays so many pixels, as the dots do. */
  function placeOutlines() {
    const layer = view.parts && view.parts.outlines;
    if (!layer) return;
    /* Each outline's dash (map.css: o0 to o3) follows its place in its owner's order, as in the
     * legend and the card, whether or not it has a shape to draw. */
    const drawn = [];
    const seen = new Map();
    for (const held of view.outlines.values()) {
      const order = seen.get(held.owner) || 0;
      seen.set(held.owner, order + 1);
      const polygon = paddedOutline(held.hull.map(([x, y]) => [screenX(x), screenY(y)]));
      if (polygon.length) drawn.push({held, polygon, order});
    }
    const nodes = [];
    drawn.forEach(({held, polygon, order}) => {
      const path = svgEl('path', {class: `map-outline o${order % OUTLINE_COLORS}`, d: pathOf(polygon),
        'data-key': held.key, 'data-owner': held.owner});
      path.style.setProperty('--o', held.color);
      const title = svgEl('title');
      title.textContent = `${held.tag ? `${held.tag}: ` : ''}${held.label}, ${held.placed} of ${totalOf(held)} placed`;
      path.append(title);
      nodes.push(path);
    });
    /* Where two outlines of one owner meet, the shared part is shaded. */
    for (let first = 0; first < drawn.length; first += 1) {
      for (let second = first + 1; second < drawn.length; second += 1) {
        if (drawn[first].held.owner !== drawn[second].held.owner) continue;
        const shared = overlapOf(drawn[first].polygon, drawn[second].polygon);
        if (areaOf(shared) < 1) continue;
        nodes.push(svgEl('path', {class: 'map-overlap', d: pathOf(shared), 'data-owner': drawn[first].held.owner,
          'data-keys': `${drawn[first].held.key} ${drawn[second].held.key}`}));
      }
    }
    /* Each outline's tag clear of the names and of the other tags. */
    const avoid = nameBoxes();
    drawn.forEach(({held, polygon}) => {
      if (!held.tag) return;
      const place = tagPlace(polygon, avoid);
      avoid.push({...place, tag: true});
      const group = svgEl('g', {class: 'map-outline-tag', 'data-owner': held.owner});
      const ground = svgEl('rect', {x: place.x.toFixed(1), y: place.y.toFixed(1), width: 22, height: 18, rx: 4});
      ground.style.setProperty('--o', held.color);
      const text = svgEl('text', {x: (place.x + 11).toFixed(1), y: (place.y + 9).toFixed(1), 'text-anchor': 'middle',
        'dominant-baseline': 'central'});
      text.textContent = held.tag;
      group.append(ground, text);
      nodes.push(group);
    });
    layer.replaceChildren(...nodes);
  }

  /* The legend's line for the groups' outlines: a button that shows or hides them, then each with
   * its mark, its words and how many of its experiments have a place; one key for the overlap.
   * Another owner's outlines are not listed here: their owner says what they are. */
  function outlineLegend() {
    const own = ownedBy(GROUPS);
    if (!own.length) return null;
    const entries = own.map((held, order) => {
      const mark = el('span', {class: `map-outline-key o${order % OUTLINE_COLORS}`, 'aria-hidden': 'true'});
      mark.style.setProperty('--o', held.color);
      return el('span', {class: 'map-key plain', 'data-outline': held.key}, [mark,
        held.tag ? el('strong', {class: 'map-outline-letter', text: held.tag}) : null,
        held.label,
        el('span', {class: 'faint', text: ` · ${count(held.placed)} of ${count(totalOf(held))} placed`}),
        held.placed === 0 ? el('span', {class: 'faint', text: ' (none has a place, so none is drawn)'}) : null]);
    });
    return el('p', {class: 'map-legend-line map-outline-legend', 'data-help': 'map.outlines'}, [
      el('button', {type: 'button', class: 'map-tool', 'aria-pressed': view.outlinesShown ? 'true' : 'false',
        text: 'Outlines', onclick: () => {
          view.outlinesShown = !view.outlinesShown;
          paint();
          legend();
        }}),
      ...entries,
      own.length > 1 ? el('span', {class: 'map-key plain'}, [
        el('span', {class: 'map-overlap-key', 'aria-hidden': 'true'}), 'where outlines overlap']) : null,
      el('button', {type: 'button', class: 'link', text: 'Clear outlines', onclick: () => clearOutline()}),
    ]);
  }

  /* Zoom the map to the dots of these experiments, with room round them. */
  function fitTo(ids) {
    const dots = ids.map((id) => view.byId.get(id)).filter((point) => point && point.node != null);
    if (!dots.length || !view.parts) return;
    let left = Infinity;
    let right = -Infinity;
    let top = Infinity;
    let bottom = -Infinity;
    for (const point of dots) {
      left = Math.min(left, point.x);
      right = Math.max(right, point.x);
      top = Math.min(top, point.y);
      bottom = Math.max(bottom, point.y);
    }
    /* A group of one place, or of places very near, is not zoomed to a point. */
    const spanX = Math.max(right - left, 0.06);
    const spanY = Math.max(bottom - top, 0.06);
    const k = (0.82 * Math.min(view.w / spanX, view.h / spanY)) / baseScale();
    view.z = {cx: (left + right) / 2, cy: (top + bottom) / 2, k: Math.min(MOST_ZOOM, Math.max(1, k))};
    layout();
  }

  /* ---------------------------------------------------------------- the card */

  function swatch(color) {
    const node = el('span', {class: color ? 'map-swatch' : 'map-swatch unknown', 'aria-hidden': 'true'});
    if (color) node.style.background = color;
    return node;
  }

  function lineOf(point) {
    const theme = point.theme == null ? null : view.themeById.get(point.theme);
    if (!theme) return el('span', {text: `${BOARD.noTheme}${point.no_theme_reason ? `: ${point.no_theme_reason}` : ''}`});
    /* The line's swatch only while the dots are colored by line: one measure at a time. */
    return el('span', {}, [view.measure === 'line' ? swatch(hueVar(theme.theme)) : null,
      el('span', {class: theme.person_label ? 'person' : '', text: themeName(theme)})]);
  }

  function standingOf(point) {
    if (point.standing) return point.standing.said;
    return view.data.standing_said || 'not read';
  }

  function whereOf(point) {
    if (point.x == null) return `not placed: ${point.no_place_reason || 'no place'}`;
    return plural(point.at_this_place, 'experiment');
  }

  function openInYears(point) {
    const years = window.ingenceYears;
    navigate('years').then(() => (years ? years.load().then(() => {
      years.open({kind: 'exp', id: point.id});
      years.select({kind: 'exp', id: point.id});
    }) : null)).catch(() => {});
  }

  function openRelations(point) {
    const focus = {kind: 'entry', id: point.id, label: point.label};
    holdFocus(focus, 'explore');
    navigate('graph', {focus});
  }

  function showCard(point) {
    /* The point the card is about, kept so a change of measure can draw the card again (a card
     * opened from the unplaced list belongs to no dot, so view.selected cannot stand for it). */
    view.carded = point || null;
    view.comparing = false;
    const card = view.parts.card;
    const hint = el('p', {class: 'map-hint faint', text: `${BOARD.help} ${SAY.noMove}`});
    if (!point) {
      fill(card, el('p', {class: 'eyebrow', 'data-help': 'map.card', text: BOARD.experiment}),
        el('p', {class: 'map-nothing', text: BOARD.nothingChosen}), topicPickBlock(), groupsBlock(), hint);
      return;
    }
    const theme = point.theme == null ? null : view.themeById.get(point.theme);
    const rows = [
      ['Line', lineOf(point)],
      ['Year', point.year == null ? BOARD.undated : String(point.year)],
      ['Files', count(point.files)],
      ['Standing', standingOf(point)],
      ['At this place', whereOf(point)],
    ];
    fill(card,
      topicPickBlock(),
      el('p', {class: 'eyebrow', 'data-help': 'map.card', text: BOARD.experiment}),
      el('h3', {class: 'map-name', text: point.label}),
      el('p', {class: 'mono map-folder', text: point.folder}),
      el('dl', {class: 'map-facts'}, rows.flatMap(([name, value]) => [el('dt', {text: name}), el('dd', {}, [value])])),
      theme ? el('div', {class: 'map-stability', 'data-help': 'map.stability'}, [
        stabilityAxis(theme.stability, {label: BOARD.stability}),
        el('p', {class: 'faint', text: SAY.perExperiment}),
      ]) : null,
      el('p', {class: 'mono map-source faint', text: `themes/map · ${view.data.method || 'no layout'} · seed ${view.data.seed}`}),
      el('div', {class: 'map-actions'}, [
        el('button', {type: 'button', class: 'answer quiet', 'data-help': 'map.open_years',
          onclick: () => openInYears(point)}, [el('strong', {text: BOARD.openYears})]),
        el('button', {type: 'button', class: 'answer quiet', 'data-help': 'map.relations',
          onclick: () => openRelations(point)}, [el('strong', {text: BOARD.relations})]),
      ]),
      hint);
  }

  function select(point, {focusPlot = false} = {}) {
    view.selected = point && point.node ? point.id : null;
    view.cursor = point && point.node ? point.id : view.cursor;
    showCard(point);
    placeMarks();
    if (focusPlot) view.parts.svg.focus();
  }

  /* ---------------------------------------------------------------- the pointer */

  function tip(point, event) {
    const {tooltip, canvas} = view.parts;
    if (!point) {
      tooltip.hidden = true;
      return;
    }
    const theme = point.theme == null ? null : view.themeById.get(point.theme);
    tooltip.replaceChildren(el('strong', {text: point.label}), el('br'),
      view.measure === 'line' ? swatch(colorOf(point)) : null, theme ? themeName(theme) : BOARD.noTheme);
    tooltip.hidden = false;
    const box = canvas.getBoundingClientRect();
    const x = event ? event.clientX - box.left : point.px;
    const y = event ? event.clientY - box.top : point.py;
    tooltip.style.left = `${Math.min(Math.max(x + 14, 4), Math.max(4, box.width - tooltip.offsetWidth - 4))}px`;
    tooltip.style.top = `${Math.min(Math.max(y + 14, 4), Math.max(4, box.height - tooltip.offsetHeight - 4))}px`;
  }

  function keep(theme) {
    view.kept = view.kept === theme ? null : theme;
    paint();
    legend();
    keptLine();
  }

  function keptLine() {
    const holder = view.parts.kept;
    const theme = view.kept == null ? null : view.themeById.get(view.kept);
    holder.hidden = !theme;
    if (!theme) {
      holder.replaceChildren();
      return;
    }
    holder.replaceChildren(
      `Showing only ${themeName(theme)}: ${plural(theme.experiments, 'experiment')} of ${count(view.data.count)}. `,
      el('button', {type: 'button', class: 'link', text: 'Show all', onclick: () => keep(theme.theme)}));
  }

  function wirePlot() {
    const {svg, canvas, rubber} = view.parts;
    let down = null;
    svg.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      const box = canvas.getBoundingClientRect();
      down = {x: event.clientX - box.left, y: event.clientY - box.top, moved: false, shift: event.shiftKey};
      view.dragged = false;
    });
    svg.addEventListener('pointermove', (event) => {
      const box = canvas.getBoundingClientRect();
      const x = event.clientX - box.left;
      const y = event.clientY - box.top;
      if (down) {
        if (!down.moved && Math.hypot(x - down.x, y - down.y) > DRAG) {
          down.moved = true;
          svg.setPointerCapture(event.pointerId);
          view.parts.tooltip.hidden = true;
        }
        if (down.moved) {
          rubber.hidden = false;
          rubber.style.left = `${Math.min(x, down.x)}px`;
          rubber.style.top = `${Math.min(y, down.y)}px`;
          rubber.style.width = `${Math.abs(x - down.x)}px`;
          rubber.style.height = `${Math.abs(y - down.y)}px`;
        }
        return;
      }
      const target = event.target instanceof Element ? event.target.closest('.map-dot') : null;
      tip(target ? view.byId.get(target.dataset.id) : null, event);
    });
    const end = (event) => {
      if (!down) return;
      const box = canvas.getBoundingClientRect();
      const x = event.clientX - box.left;
      const y = event.clientY - box.top;
      const was = down;
      down = null;
      rubber.hidden = true;
      if (!was.moved) return;
      view.dragged = true;
      // Shift held: the box picks the dots inside it for a topic; otherwise it zooms to the box.
      if (was.shift && window.topicKit) {
        pickBox(Math.min(x, was.x), Math.min(y, was.y), Math.max(x, was.x), Math.max(y, was.y));
        return;
      }
      if (Math.abs(x - was.x) > 8 && Math.abs(y - was.y) > 8) {
        zoomTo(Math.min(x, was.x), Math.min(y, was.y), Math.max(x, was.x), Math.max(y, was.y));
      }
    };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('pointerleave', () => { view.parts.tooltip.hidden = true; });
    svg.addEventListener('click', (event) => {
      if (view.dragged) {
        view.dragged = false;
        return;
      }
      if (!(event.target instanceof Element)) return;
      const dot = event.target.closest('.map-dot');
      if (dot && event.shiftKey && window.topicKit) {
        togglePicked(view.byId.get(dot.dataset.id));
        return;
      }
      if (dot) {
        select(view.byId.get(dot.dataset.id));
        return;
      }
      const name = event.target.closest('.map-theme');
      if (name) keep(Number(name.dataset.theme));
    });
    svg.addEventListener('dblclick', () => reset());
    svg.addEventListener('keydown', onKey);
    svg.addEventListener('blur', () => { view.parts.tooltip.hidden = true; });
  }

  /* ---------------------------------------------------------------- the keyboard */

  /* The nearest dot in a direction: those strictly beyond the cursor along that direction, the
   * least of the distance along it and twice the distance off it. */
  function nearest(from, dx, dy) {
    let best = null;
    let bestCost = Infinity;
    for (const point of placedPoints()) {
      if (point === from || (view.kept != null && point.theme !== view.kept)) continue;
      if (point.px < 0 || point.px > view.w || point.py < 0 || point.py > view.h) continue;
      const along = (point.px - from.px) * dx + (point.py - from.py) * dy;
      if (along <= 0) continue;
      const off = Math.abs((point.px - from.px) * dy - (point.py - from.py) * dx);
      const cost = Math.hypot(along, 2 * off);
      if (cost < bestCost) {
        best = point;
        bestCost = cost;
      }
    }
    return best;
  }

  function startingPoint() {
    let best = null;
    let bestDistance = Infinity;
    for (const point of placedPoints()) {
      if (view.kept != null && point.theme !== view.kept) continue;
      const distance = Math.hypot(point.px - view.w / 2, point.py - view.h / 2);
      if (distance < bestDistance) {
        best = point;
        bestDistance = distance;
      }
    }
    return best;
  }

  function say(point) {
    const theme = point.theme == null ? null : view.themeById.get(point.theme);
    view.parts.live.textContent = `${point.label}, ${theme ? themeName(theme) : BOARD.noTheme}`;
  }

  function moveCursor(point) {
    if (!point) return;
    view.cursor = point.id;
    if ((point.px < 24 || point.px > view.w - 24 || point.py < 24 || point.py > view.h - 24) && view.z.k > 1) {
      view.z.cx = point.x;
      view.z.cy = point.y;
      layout();
    }
    placeMarks();
    tip(point, null);
    say(point);
  }

  function onKey(event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const directions = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
    const key = event.key;
    const current = view.cursor ? view.byId.get(view.cursor) : null;
    let handled = true;
    if (directions[key]) {
      const [dx, dy] = directions[key];
      moveCursor(current && current.node ? nearest(current, dx, dy) || current : startingPoint());
    } else if (key === 'Enter') {
      const point = current && current.node ? current : startingPoint();
      if (point) {
        moveCursor(point);
        select(point);
      }
    } else if (key === '+' || key === '=') zoomBy(STEP);
    else if (key === '-' || key === '_') zoomBy(1 / STEP);
    else if (key === '0') reset();
    else if (key === 'l' || key === 'L') {
      view.labels = !view.labels;
      paint();
    } else if ((key === 'n' || key === 'N') && view.selected) openRelations(view.byId.get(view.selected));
    else if (key === 'Escape') {
      if (view.kept != null) keep(view.kept);
      else if (view.selected) select(null);
      else handled = false;
    } else handled = false;
    if (handled) {
      event.preventDefault();
      event.stopPropagation();
    }
  }

  /* ---------------------------------------------------------------- the page */

  function chips() {
    const row = el('div', {class: 'map-chips', role: 'group', 'aria-label': BOARD.colorBy, 'data-help': 'map.color_by'},
      [el('span', {class: 'map-chips-label', text: BOARD.colorBy})]);
    for (const [name, words] of Object.entries(BOARD.measures)) {
      row.append(el('button', {type: 'button', class: 'map-chip', 'data-measure': name,
        'aria-pressed': view.measure === name ? 'true' : 'false', text: words,
        onclick: () => {
          view.measure = name;
          for (const chip of row.querySelectorAll('.map-chip')) {
            chip.setAttribute('aria-pressed', chip.dataset.measure === name ? 'true' : 'false');
          }
          paint();
          legend();
          /* The open card follows the measure: the line's swatch is drawn only for the line. */
          showCard(view.carded);
        }}));
    }
    return row;
  }

  /* The legend binds the colors of the measure shown, and only that one: the ramp of how far raw
   * data was followed is never beside the palette of lines. */
  function legend() {
    const holder = view.parts.legend;
    /* Drawn again after a choice: the key that had the keyboard keeps it, and the list of the
     * smaller lines stays open if it was. */
    const focused = document.activeElement instanceof HTMLElement && holder.contains(document.activeElement)
      ? document.activeElement.dataset.theme : null;
    const more = holder.querySelector('details.map-more');
    const wasOpen = !!(more && more.open);
    const items = [];
    if (view.measure === 'line') {
      const ranked = [...view.themes].sort((a, b) => b.experiments - a.experiments || a.theme - b.theme);
      const entries = ranked.map((theme) => el('button', {type: 'button', class: 'map-key', 'data-theme': theme.theme,
        'aria-pressed': view.kept === theme.theme ? 'true' : 'false',
        title: `${themeName(theme)}: ${plural(theme.experiments, 'experiment')}`}, [
        swatch(hueVar(theme.theme)),
        el('span', {class: theme.person_label ? 'person' : '', text: themeName(theme)}),
        el('span', {class: 'map-key-count faint', text: count(theme.experiments)}),
      ]));
      for (const entry of entries) entry.addEventListener('click', () => keep(Number(entry.dataset.theme)));
      const shown = 10;
      items.push(el('div', {class: 'map-keys', 'data-help': 'map.legend'}, [
        ...entries.slice(0, shown),
        entries.length > shown ? el('details', {class: 'map-more'}, [
          el('summary', {text: `${count(entries.length - shown)} more`}), el('div', {class: 'map-keys'}, entries.slice(shown))]) : null,
      ]));
      if (view.points.some((point) => point.theme == null)) {
        items.push(el('span', {class: 'map-key plain'}, [swatch(null), BOARD.noTheme]));
      }
      if (ranked.length > HUES) {
        items.push(el('span', {class: 'map-key plain'}, [swatch('var(--map-other)'),
          `${plural(ranked.length - HUES, 'smaller line')} share this gray; read the name at its center`]));
      }
    } else if (view.measure === 'raw' && !view.data.standing_computed) {
      /* Unknown, not "none": what was found could not be counted, so no dot has a level. */
      items.push(el('div', {class: 'map-keys', 'data-help': 'map.legend'}, [
        el('span', {class: 'map-key plain'}, [swatch(null), view.data.standing_said || 'not read'])]));
    } else if (view.measure === 'raw') {
      items.push(el('div', {class: 'map-keys', 'data-help': 'map.legend'}, LEVELS.map((level) => el('span',
        {class: 'map-key plain', 'data-level': level}, [swatch(level === 'na' ? null : `var(--map-${level})`),
          LEVEL_WORDS[level]]))));
    } else {
      const span = view.years;
      items.push(el('div', {class: 'map-keys', 'data-help': 'map.legend'}, [
        span ? el('span', {class: 'map-key plain'}, [String(span.low), el('span', {class: 'map-ramp', 'aria-hidden': 'true'}),
          String(span.high)]) : el('span', {class: 'map-key plain', text: 'no year was read'}),
        view.points.some((point) => point.year == null) ? el('span', {class: 'map-key plain'}, [swatch(null), BOARD.undated]) : null,
      ]));
    }
    holder.replaceChildren(
      el('p', {class: 'map-legend-line'}, [
        /* Hatching means unknown, so a known value is never keyed by it: a hollow dot stands for an
         * experiment where the colors are a ramp. */
        el('span', {class: 'map-key plain'}, [swatch(view.measure === 'line' ? 'var(--map-c0)' : 'var(--panel)'),
          `an experiment, colored by its ${BOARD.measures[view.measure]}`]),
        el('span', {class: 'map-key plain'}, [el('span', {class: 'map-center-key', 'aria-hidden': 'true'}), BOARD.legendCenter]),
      ]),
      ...items);
    /* The outlines' line only where a group is outlined: replaceChildren would print a missing part as the word "null". */
    const outlines = outlineLegend();
    if (outlines) holder.append(outlines);
    const topics = topicLegend();
    if (topics) holder.append(topics);
    const again = holder.querySelector('details.map-more');
    if (again && wasOpen) again.open = true;
    if (focused != null) {
      const key = holder.querySelector(`.map-key[data-theme="${focused}"]`);
      if (key instanceof HTMLElement) key.focus();
    }
  }

  function unplacedLine() {
    const holder = view.parts.unplaced;
    const none = view.points.filter((point) => point.x == null || point.y == null);
    holder.hidden = !none.length;
    if (!none.length) return;
    const words = none.every((point) => (point.no_place_reason || '').startsWith('none of its words'))
      ? SAY.unplaced(none.length)
      : `${count(none.length)} not placed: ${[...new Set(none.map((point) => point.no_place_reason))].filter(Boolean).join('; ')}`;
    const list = el('ul', {class: 'map-unplaced-list', hidden: ''});
    for (const point of none.slice(0, LISTED)) {
      list.append(el('li', {}, [el('button', {type: 'button', class: 'link', text: point.label,
        onclick: () => select(point)}), ' ', el('span', {class: 'mono', text: point.folder})]));
    }
    if (none.length > LISTED) list.append(el('li', {class: 'faint', text: `and ${count(none.length - LISTED)} more`}));
    const toggle = el('button', {type: 'button', class: 'link', 'aria-expanded': 'false', 'data-help': 'map.unplaced',
      text: BOARD.listThem, onclick: () => {
        const open = toggle.getAttribute('aria-expanded') !== 'true';
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        list.hidden = !open;
      }});
    holder.replaceChildren(`${words} · `, toggle, list);
  }

  function toolbar() {
    const labelsButton = el('button', {type: 'button', class: 'map-tool', 'aria-pressed': 'true', text: 'Labels',
      onclick: () => { view.labels = !view.labels; paint(); }});
    const bar = el('div', {class: 'map-tools', role: 'group', 'aria-label': 'Zoom and labels', 'data-help': 'map.tools'}, [
      el('button', {type: 'button', class: 'map-tool', 'aria-label': 'Zoom out', text: '−', onclick: () => zoomBy(1 / STEP)}),
      el('button', {type: 'button', class: 'map-tool', 'aria-label': 'Zoom in', text: '+', onclick: () => zoomBy(STEP)}),
      el('button', {type: 'button', class: 'map-tool', text: 'Reset', onclick: () => reset()}),
      labelsButton,
    ]);
    return {bar, labelsButton};
  }

  function pattern() {
    const defs = svgEl('defs');
    const hatch = svgEl('pattern', {id: 'map-hatch', width: 4, height: 4, patternUnits: 'userSpaceOnUse',
      patternTransform: 'rotate(45)'});
    hatch.append(svgEl('rect', {width: 4, height: 4, class: 'map-hatch-ground'}),
      svgEl('rect', {width: 1.4, height: 4, class: 'map-hatch-line'}));
    defs.append(hatch);
    return defs;
  }

  function build(data) {
    const {bar, labelsButton} = toolbar();
    const svg = svgEl('svg', {class: 'map-svg', tabindex: '0', role: 'application', 'aria-roledescription': 'map of groups',
      'aria-label': `${BOARD.eyebrow}: ${SAY.head(data.placed, data.count)}. Arrow keys move between points, `
        + 'Enter opens the card, plus and minus zoom, 0 resets, L toggles the names.'});
    const outlines = svgEl('g', {class: 'map-outlines', 'aria-hidden': 'true'});
    const dots = svgEl('g', {class: 'map-dots'});
    const names = svgEl('g', {class: 'map-names'});
    const chosen = svgEl('circle', {class: 'map-chosen', visibility: 'hidden'});
    const cursor = svgEl('circle', {class: 'map-cursor', visibility: 'hidden'});
    svg.append(pattern(), outlines, dots, names, chosen, cursor);
    const tooltip = el('div', {class: 'map-tip', role: 'tooltip', hidden: ''});
    const rubber = el('div', {class: 'map-rubber', hidden: ''});
    const live = el('div', {class: 'map-live', 'aria-live': 'polite', role: 'status'});
    const canvas = el('div', {class: 'map-canvas'}, [svg, tooltip, rubber, bar, live]);
    const method = el('p', {class: 'map-method', text: data.method_said});
    const card = el('aside', {class: 'map-card', 'aria-label': 'What is selected', 'aria-live': 'polite'});
    const legendHolder = el('div', {class: 'map-legend'});
    const unplaced = el('p', {class: 'map-unplaced'});
    const kept = el('p', {class: 'map-kept', role: 'status', hidden: ''});
    const howto = howToRead();
    const page = el('div', {class: 'map-page'}, [
      el('div', {class: 'map-main'}, [
        el('div', {class: 'map-head'}, [
          /* The help for the plot is on its heading: a tip on the plot itself would open over the
           * dots every time the pointer rests on one. */
          el('div', {}, [el('div', {'data-help': 'map.plot'}, [el('p', {class: 'eyebrow', text: BOARD.eyebrow}),
            el('h2', {class: 'map-title', text: SAY.head(data.placed, data.count)})]), howto.toggle]),
          chips(),
        ]),
        howto.panel,
        el('div', {class: 'map-plot'}, [canvas, method]),
        kept, legendHolder, compareControl(), unplaced,
      ]),
      card,
    ]);
    view.parts = {page, svg, outlines, dots, names, chosen, cursor, tooltip, rubber, live, canvas, card, legend: legendHolder,
      unplaced, kept, labelsButton};
    return page;
  }

  function measure() {
    const {canvas} = view.parts;
    const box = canvas.getBoundingClientRect();
    view.w = Math.max(200, Math.round(box.width));
    view.h = Math.max(200, Math.round(box.height));
    view.parts.svg.setAttribute('viewBox', `0 0 ${view.w} ${view.h}`);
    view.parts.svg.setAttribute('width', String(view.w));
    view.parts.svg.setAttribute('height', String(view.h));
  }

  function draw(pane, data) {
    prepare(data);
    view.kept = null;
    view.selected = null;
    view.cursor = null;
    view.z = {cx: 0.5, cy: 0.5, k: 1};
    view.folderIndex = null;
    const page = build(data);
    fill(pane, page);
    measure();
    drawDots();
    drawNames();
    /* Outlines asked for before the dots existed (``show``, a caller of ``outline``) are drawn now. */
    view.drawn = true;
    applyRequest();
    for (const held of view.outlines.values()) resolveOutline(held);
    wirePlot();
    paint();
    layout();
    if (view.fitIds) {
      fitTo(view.fitIds);
      view.fitIds = null;
    }
    legend();
    unplacedLine();
    showCard(null);
    for (const listener of view.listeners) {
      try {
        listener();
      } catch (error) {
        /* A listener that fails never stops the map. */
      }
    }
    if (view.compared) {
      const carried = view.compared;
      view.compared = null;
      whenGroups().then((groups) => {
        if (!view.parts || !view.parts.card.isConnected) return;
        groups.drawCompare(view.parts.card, carried, {host: 'map', onClose: closeCompare});
        view.comparing = true;
      }).catch((error) => {
        if (view.parts && view.parts.card.isConnected) {
          fill(view.parts.card, el('p', {class: 'note refused', role: 'alert', text: String(error.message || error)}));
        }
      });
    }
    if (view.observer) view.observer.disconnect();
    if (window.ResizeObserver) {
      let frame = 0;
      view.observer = new ResizeObserver(() => {
        if (frame) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
          frame = 0;
          if (!view.parts || !view.parts.canvas.isConnected) return;
          measure();
          layout();
        });
      });
      view.observer.observe(view.parts.canvas);
    }
  }

  /* ---------------------------------------------------------------- the loading state */

  function pulsingDots() {
    return el('span', {class: 'map-dots-loading', 'aria-hidden': 'true'}, [el('span'), el('span'), el('span')]);
  }

  function showLoading(pane, first) {
    const card = states.say(null, {
      eyebrow: `${BOARD.eyebrow} · loading`, dot: 'loading', sentence: SAY.loading(first.count),
      body: [SAY.loadingBody], into: true,
      actions: [{label: BOARD.meanwhile, onclick: () => navigate('years')}],
    });
    card.classList.add('map-loading-card');
    const names = el('ul', {class: 'map-themes-first', 'aria-label': 'The themes'},
      first.themes.map((theme) => el('li', {'data-theme': theme.theme}, [
        el('span', {class: theme.person_label ? 'person' : '', text: themeName(theme)}),
        el('span', {class: 'faint', text: ` ${plural(theme.experiments, 'experiment')}`}),
      ])));
    /* The sentence and its body, then the dots and the themes' names, then the way out. */
    const way = card.querySelector('.state-actions');
    if (way) way.before(pulsingDots(), names);
    else card.append(pulsingDots(), names);
    fill(pane, el('div', {class: 'map-page loading'}, [el('div', {class: 'map-main'}, [
      el('div', {class: 'map-head'}, [el('h2', {class: 'map-title', text: BOARD.eyebrow})]),
      el('div', {class: 'map-plot loading'}, [el('div', {class: 'map-canvas map-loading'}, [card])]),
    ])]));
  }

  /* A project with no themes says so, in the engine's words. */
  async function showNoThemes(pane, first, turn) {
    let reasons = {};
    if (first.count) {
      try {
        reasons = (await api('/api/v1/themes/unplaced?limit=0')).reasons || {};
      } catch (error) {
        reasons = {};
      }
    }
    if (!turns.owns(pane, turn)) return;
    states.say(pane, {
      eyebrow: `${BOARD.eyebrow} · empty`, dot: 'empty',
      sentence: first.count ? `No themes were found among ${plural(first.count, 'experiment')}.` : SAY.noExperiments,
      body: Object.entries(reasons).map(([reason, n]) => `${count(n)} of ${count(first.count)}: ${reason}`),
      actions: [{label: 'Open By year', onclick: () => navigate('years')}],
    });
  }

  async function showMap() {
    const pane = listPane;
    const turn = turns.claim(pane);
    if (view.observer) view.observer.disconnect();
    view.parts = null;
    view.drawn = false;
    states.loading(pane, 'Reading the themes...');
    let first;
    try {
      first = await api('/api/v1/themes/map?themes_only=true');
    } catch (error) {
      if (turns.owns(pane, turn)) states.failed(pane, error, () => showMap());
      return;
    }
    if (!turns.owns(pane, turn)) return;
    if (!first.themes.length) {
      /* A request to show outlines (or a comparison) waits for a drawn map: there is none to draw. */
      view.request = null;
      view.compared = null;
      await showNoThemes(pane, first, turn);
      return;
    }
    if (!first.layout_ready) showLoading(pane, first);
    let whole;
    try {
      whole = await api('/api/v1/themes/map');
    } catch (error) {
      if (turns.owns(pane, turn)) states.failed(pane, error, () => showMap());
      return;
    }
    if (!turns.owns(pane, turn)) return;
    draw(pane, whole);
  }


  /* ---------------------------------------------------------------- topics on the map */

  /* Topics on the map. A topic is the person's: its outline is drawn in the person's color (the
   * magenta family) through the one outline function above, under the dots, and the legend shows or
   * hides each. An outline is the shape of the places of the topic's experiments, never a boundary:
   * another experiment's dot can lie inside it. A dot picked with shift-click, or every dot in a
   * box drawn with shift held, is picked for a topic ("Add to topic...", in the card at the right).
   * A plain box still zooms to what it draws. */
  const pickedDots = new Set();

  function markPicked() {
    for (const point of view.points) {
      if (point.node != null) point.node.classList.toggle('topic-picked', pickedDots.has(point.id));
    }
  }

  function refreshPick() {
    markPicked();
    if (view.parts) showCard(view.carded);
  }

  function togglePicked(point) {
    if (!point || point.node == null) return;
    if (pickedDots.has(point.id)) pickedDots.delete(point.id);
    else pickedDots.add(point.id);
    refreshPick();
  }

  /* Every dot whose center lies inside the box (in the plot's own pixels) is picked. */
  function pickBox(left, top, right, bottom) {
    for (const point of view.points) {
      if (point.node == null) continue;
      if (point.px >= left && point.px <= right && point.py >= top && point.py <= bottom) pickedDots.add(point.id);
    }
    refreshPick();
  }

  function topicPickBlock() {
    if (!window.topicKit || !pickedDots.size) return null;
    return topicKit.pickBar({noun: 'dot', count: pickedDots.size,
      onClear: () => {
        pickedDots.clear();
        refreshPick();
      },
      onAdd: async () => {
        const ids = [...pickedDots].sort();
        const made = await topicKit.addTo({
          what: `${plural(ids.length, 'experiment')} picked on the map`,
          members: ids.map((id) => ({kind: 'experiment', id}))});
        if (made) {
          pickedDots.clear();
          refreshPick();
        }
      }});
  }

  /* A topic's outline is its own owner's (``topics``): the groups' legend line and card never list or
   * relabel it, "Outlines" and "Clear outlines" and a comparison never touch it, and it is found by its
   * owner and not by the spelling of its key. */
  const TOPICS = 'topics';
  const topicKeyOf = (topic) => `topic:${topic.id}`;

  /* Draw, or take away, one topic's outline. */
  function setTopicOutline(topic, on) {
    if (!window.ingenceMap) return;
    if (on) {
      window.ingenceMap.outline([...topic.experiments], topic.color,
        {key: topicKeyOf(topic), label: topic.name, tag: '', owner: TOPICS});
    } else window.ingenceMap.clear(topicKeyOf(topic), {owner: TOPICS});
  }

  /* After a topic changed: its outline is drawn again from what it holds now, or dropped where the
   * topic is withdrawn. */
  function syncTopicOutlines() {
    if (!view.parts || !window.topicKit) return;
    const reading = topicKit.current();
    const standing = new Map((reading ? reading.topics : []).map((topic) => [topicKeyOf(topic), topic]));
    for (const key of window.ingenceMap.keys(TOPICS)) {
      const topic = standing.get(key);
      if (topic) setTopicOutline(topic, true);
      else window.ingenceMap.clear(key, {owner: TOPICS});
    }
    legend();
  }

  /* The legend's line for the topics: one switch for each, with its color and how many of its
   * experiments have a place on the map. */
  function topicLegend() {
    const reading = window.topicKit ? topicKit.current() : null;
    if (!reading || !reading.topics.length) return null;
    const keys = reading.topics.map((topic) => {
      const held = window.ingenceMap && window.ingenceMap.keys(TOPICS).includes(topicKeyOf(topic));
      const placed = [...topic.experiments].filter((id) => {
        const point = view.byId.get(id);
        return point && point.node != null;
      }).length;
      const box = el('input', {type: 'checkbox', 'aria-label': `Outline the collection ${topic.name}`});
      box.checked = !!held;
      box.addEventListener('change', () => {
        setTopicOutline(topic, box.checked);
        legend();
      });
      const mark = el('span', {class: 'map-outline-key', 'aria-hidden': 'true', style: `--o: ${topic.color}`});
      return el('label', {class: 'map-key plain map-topic-key', 'data-topic': topic.id,
        title: `The shape of the places of ${topic.count == null ? 'the experiments' : plural(topic.count, 'experiment')} of the collection ${topic.name}`}, [
        box, mark, topic.name,
        el('span', {class: 'faint', text: topic.count == null ? ` · ${count(placed)} placed`
          : ` · ${count(placed)} of ${count(topic.count)} placed`})]);
    });
    return el('p', {class: 'map-legend-line map-topic-legend', 'data-help': 'map.topics'}, [
      el('strong', {text: 'Your collections'}), ...keys]);
  }

  /* From the Topics section: open the map with this topic outlined and the map fitted to it. */
  function showTopic(topicId) {
    const reading = window.topicKit ? topicKit.current() : null;
    const topic = reading ? reading.topics.find((item) => item.id === topicId) : null;
    if (!topic || !window.ingenceMap) return navigate('map');
    return window.ingenceMap.show({replace: false, fit: true, outlines: [{key: topicKeyOf(topic), label: topic.name,
      tag: '', ids: [...topic.experiments], color: topic.color, owner: TOPICS}]});
  }

  if (window.topicKit) {
    topicKit.subscribe(() => {
      topicKit.read().then(() => {
        if (state.view === 'map') syncTopicOutlines();
      }, () => null);
    });
  }

  /* ---------------------------------------------------------------- how to read this, and compare */

  /* The Groupings script (groups.js) holds the panels this view shares with the Groupings page: the
   * picture that explains a view, and the comparison of two to four groups. The Map asks for it only
   * when a person opens one of them. */
  let groupsLoading = null;
  /* How long the script may take before the Map says it did not load. */
  const GROUPS_WAIT_MS = 15000;
  function whenGroups() {
    const ready = () => window.ingenceGroups && window.ingenceGroups.openCompare;
    if (ready()) return Promise.resolve(window.ingenceGroups);
    /* A load of its own each time it is not there: a tag already in the page may have failed before,
     * and would never say so again. The script does nothing when it runs a second time. */
    groupsLoading = groupsLoading || new Promise((resolve, reject) => {
      const script = el('script', {src: '/groups.js'});
      let timer = 0;
      const fail = () => {
        clearTimeout(timer);
        reject(new Error('The panel did not load. Reload the page and try again.'));
      };
      timer = setTimeout(fail, GROUPS_WAIT_MS);
      script.addEventListener('load', () => {
        clearTimeout(timer);
        if (ready()) resolve(window.ingenceGroups);
        else fail();
      });
      script.addEventListener('error', fail);
      document.head.append(script);
    }).catch((error) => {
      groupsLoading = null;
      throw error;
    });
    return groupsLoading;
  }

  /* "How to read this" beside the map's heading, and the panel it opens (closed until asked). */
  function howToRead() {
    const panel = el('section', {class: 'howto', id: 'howto-map', hidden: '', 'aria-label': 'How to read the map'});
    let asked = false;
    const toggle = el('button', {type: 'button', class: 'link howto-toggle', 'aria-expanded': 'false',
      'aria-controls': 'howto-map', 'data-help': 'map.howto', text: 'How to read this', onclick: () => {
        const open = toggle.getAttribute('aria-expanded') !== 'true';
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        panel.hidden = !open;
        if (open && !asked) {
          asked = true;
          whenGroups().then((groups) => groups.fillHowTo(panel, 'map')).catch(() => {
            asked = false;
            fill(panel, el('p', {class: 'faint', text: 'The explanation could not be loaded. Close this and open it again.'}));
          });
        }
      }});
    return {toggle, panel};
  }

  /* The lines of work to compare, ticked in a list (two to four), and the button that opens the
   * comparison in the card's place; the map draws each group's outline from the answer. */
  function compareControl() {
    const ticked = new Set();
    const said = el('span', {class: 'note', role: 'status'});
    const go = el('button', {type: 'button', class: 'answer', disabled: '', text: 'Tick 2 to 4 themes', onclick: () => {
      const specs = [...ticked].map((theme) => `line:${theme}`);
      whenGroups().then((groups) => groups.openCompare(view.parts.card, specs, {host: 'map', onData: showCompared,
        onClose: closeCompare})).catch((error) => { said.textContent = String(error.message || error); });
    }});
    const ranked = [...view.themes].sort((a, b) => b.experiments - a.experiments || a.theme - b.theme);
    const boxes = ranked.map((theme) => {
      const box = el('input', {type: 'checkbox', value: String(theme.theme)});
      box.addEventListener('change', () => {
        if (box.checked && ticked.size >= 4) {
          box.checked = false;
          said.textContent = '4 themes at most. Untick one first.';
          return;
        }
        if (box.checked) ticked.add(theme.theme);
        else ticked.delete(theme.theme);
        said.textContent = '';
        go.disabled = ticked.size < 2;
        go.textContent = ticked.size < 2 ? 'Tick 2 to 4 themes' : `Compare ${ticked.size} themes`;
      });
      return el('label', {class: 'map-compare-line'}, [box, ' ', el('span', {class: theme.person_label ? 'person' : '', text: themeName(theme)}),
        el('span', {class: 'faint', text: ` ${count(theme.experiments)}`})]);
    });
    return el('details', {class: 'map-compare', 'data-help': 'map.compare'}, [
      el('summary', {text: 'Compare themes'}),
      el('div', {class: 'map-compare-lines'}, boxes),
      el('div', {class: 'map-compare-go'}, [go, said]),
    ]);
  }

  /* The answer of a comparison: each group's outline, with its mark, and the map fitted to them. */
  function showCompared(data) {
    if (!view.parts) return;
    dropOutlines(GROUPS);
    const every = [];
    data.groups.forEach((group, order) => {
      const ids = group.experiments.map((experiment) => experiment.id);
      every.push(...ids);
      outline(ids, outlineColor(order), {key: group.key, label: group.label, tag: group.tag,
        stack: group.kind === 'words' ? group.words : null, quiet: true});
    });
    placeOutlines();
    legend();
    view.comparing = true;
    fitTo(every);
  }

  /* Close the comparison: its outlines go, and the card shows what it showed before. */
  function closeCompare() {
    dropOutlines(GROUPS);
    placeOutlines();
    legend();
    showCard(view.carded);
  }

  /* ---------------------------------------------------------------- groups on the map */

  /* The card for the groups outlined: what each is, how many of its experiments have a place, the
   * way back to its stack on the Groupings page (a group of words has one), and a way to remove it. */
  function groupsBlock() {
    const own = ownedBy(GROUPS);
    if (!own.length) return null;
    return el('div', {class: 'map-groups', 'data-help': 'map.groups'}, own.map((held, order) => {
      const mark = el('span', {class: `map-outline-key o${order % OUTLINE_COLORS}`, 'aria-hidden': 'true'});
      mark.style.setProperty('--o', held.color);
      return el('div', {class: 'map-group'}, [
        el('p', {class: 'eyebrow', text: held.stack ? 'A group of words' : 'A group'}),
        el('p', {class: 'map-group-name'}, [mark, held.tag ? el('strong', {class: 'map-outline-letter', text: held.tag}) : null, held.label]),
        el('p', {class: 'faint', text: `${count(held.placed)} of ${plural(totalOf(held), 'experiment')} placed on the map.`}),
        el('div', {class: 'map-actions'}, [
          held.stack ? el('button', {type: 'button', class: 'answer quiet', 'data-help': 'map.show_stack',
            onclick: () => showStack(held.stack)}, [el('strong', {text: 'Show the word group'})]) : null,
          el('button', {type: 'button', class: 'answer quiet', onclick: () => clearOutline(held.key)}, ['Remove outline']),
        ]),
      ]);
    }));
  }

  /* Back to the stack of a group of words on the Groupings page: it opens there with that set found,
   * opened and lit (groups.js reads ``ingenceGroups.pendingStack`` once it has drawn). */
  function showStack(terms) {
    window.ingenceGroups = window.ingenceGroups || {};
    window.ingenceGroups.pendingStack = terms.join(' ');
    return navigate('classes');
  }

  /* What ``show`` asked for, applied when the map is next drawn: the outlines named (by experiment
   * id or by folder), and whether to fit the map to them. */
  function applyRequest() {
    const request = view.request;
    if (!request) return;
    view.request = null;
    /* Replacing takes away the groups' own outlines and no other owner's. */
    if (request.replace !== false) dropOutlines(GROUPS);
    const every = [];
    (request.outlines || []).forEach((entry, order) => {
      const named = idsOfFolders(entry.folders || []);
      const ids = [...(entry.ids || []), ...named.ids];
      every.push(...ids);
      outline(ids, entry.color || outlineColor(order), {key: entry.key, label: entry.label, tag: entry.tag,
        owner: entry.owner, stack: entry.stack, missing: named.missing, quiet: true});
    });
    if (request.fit !== false && every.length) view.fitIds = every.map(String);
    view.compared = request.compare || null;
  }

  /* Open the map with these outlines drawn, and the map fitted to them:
   *   window.ingenceMap.show({outlines: [{key, label, tag, ids | folders, color, stack, owner}], fit: true})
   * An entry's ``owner`` is the outline's (default ``groups``), as in ``outline()``: ``replace`` (true
   * unless ``replace: false``) takes away the groups' own outlines only, so an outline opened with
   * ``owner: 'topics'`` stays through it, and through "Clear outlines" and a comparison. */
  function show(request) {
    view.request = request || null;
    return navigate('map');
  }

  /* Hear each drawing of the map (and now, if it is drawn); the function returned stops the hearing. */
  function onDraw(listener) {
    view.listeners.push(listener);
    if (view.drawn) listener();
    return () => {
      const at = view.listeners.indexOf(listener);
      if (at >= 0) view.listeners.splice(at, 1);
    };
  }

  window.ingenceMapTopics = {show: showTopic};
  window.ingenceMap = {
    outline, clear: clearOutline, keys: outlineKeys, outlineColor, fit: fitTo, show, onDraw,
    geometry: {convexHull, paddedOutline, overlapOf, areaOf},
  };
  /* Each time the map is drawn the topics are read (once, kept), and its legend gets their switches. */
  if (window.topicKit) {
    onDraw(() => {
      topicKit.read().then(() => {
        if (view.parts) {
          syncTopicOutlines();
          markPicked();
        }
      }, () => null);
    });
  }

  /* ---------------------------------------------------------------- registration */

  VIEWS.map = showMap;
  SINGLE_VIEWS.add('map');
  /* The address #words opens this view until words.js loads and takes it back. */
  VIEW_ALIASES.words = 'map';
  onLeave((next) => {
    if (next !== 'map' && view.observer) view.observer.disconnect();
  });
})();
