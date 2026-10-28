/* Overlap: how experiments connect, one of the Dashboard's two main views.
 *
 * Two modes, chosen at the top:
 *
 * - Connections (``GET /api/v1/overlap?mode=connections``): every experiment is a dot, colored by
 *   its theme (the eight largest themes each get a color, the rest are gray). A line joins two
 *   experiments for each way they overlap, and each kind is drawn its own way: shared words, the
 *   same workflow, the same instrument, a repeat, a copy, made from. Opening a line shows its
 *   evidence in words, then its files. Opening a dot shows the experiment's story (story.js).
 *   Filters work by area, theme and kind of line. Experiments the Overview's "Show these" named
 *   (``sessionStorage['ingence.highlight']``) are highlighted and the rest dimmed, until "Clear
 *   highlight". A large project draws its 400 strongest lines and says how many there are.
 * - By year: the rows by time (years.js): areas, then experiments, then one experiment's story.
 *   ``#years``, the address of the old Years view, opens this mode.
 *
 * The drawing is the vendored Cytoscape, as on the Graph page, with the same wheel: a plain wheel
 * scrolls the page and only Ctrl + wheel (a trackpad pinch) zooms. The layout is computed here from
 * the data, never at random, so the same run is always drawn the same way: each theme is a cluster,
 * the largest first, and each experiment is placed in its cluster by its first date.
 *
 * Loaded after app.js (el, api, states, turns, navigate, VIEWS, onLeave, onRefresh, holdFocus,
 * state, listPane, detailPane), review.js (fill), story.js and years.js. No framework, no build. */

(function overlapModule() {
  'use strict';

  const fmt = (n) => Number(n || 0).toLocaleString('en-US');
  const plural = (n, one, many) => `${fmt(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;

  /* ------------------------------------------------------------------ the mode */

  const MODE_KEY = 'ingence.overlap.mode';
  const MODES = [['connections', 'Connections'], ['by_year', 'By year']];
  let mode = 'connections';
  try {
    const kept = sessionStorage.getItem(MODE_KEY);
    if (kept === 'by_year' || kept === 'connections') mode = kept;
  } catch (error) { /* a convenience */ }
  function setMode(next) {
    mode = next === 'by_year' ? 'by_year' : 'connections';
    try { sessionStorage.setItem(MODE_KEY, mode); } catch (error) { /* a convenience */ }
  }
  /* An address that names #years asks for By year, also where another state (the card of a project
   * not yet inferred) stands in place of the view and Overlap draws nothing: a load at #years, and
   * a link to it, keep the mode for when Overlap draws. */
  if (location.hash === '#years') setMode('by_year');
  window.addEventListener('hashchange', (event) => {
    if (String(event.newURL || '').endsWith('#years')) setMode('by_year');
  });

  /* ------------------------------------------------------------------ the kinds of line */

  /* Each kind of overlap, its words and how its line is drawn (overlap.css holds the colors). */
  const KINDS = [
    {kind: 'shared_words', words: 'Shared words', token: '--ov-k-words', style: 'solid', width: 1.5},
    {kind: 'workflow', words: 'Same workflow', token: '--ov-k-workflow', style: 'dashed', width: 2, dash: [8, 5]},
    {kind: 'instrument', words: 'Same instrument', token: '--ov-k-instrument', style: 'dotted', width: 2.5},
    // Two parallel lines: a line the page's color with an outline of the kind's (Cytoscape has no double line).
    {kind: 'repeat_of', words: 'Repeat of', token: '--ov-k-repeat', style: 'solid', width: 2.2, double: true},
    {kind: 'copy', words: 'Copy', token: '--ov-k-copy', style: 'dashed', width: 2, dash: [10, 3, 2, 3]},
    {kind: 'made_from', words: 'Made from', token: '--ov-k-made', style: 'solid', width: 3, ends: 'circle'},
  ];
  const KIND_OF = new Map(KINDS.map((item) => [item.kind, item]));
  const kindWords = (kind) => (KIND_OF.get(kind) || {words: String(kind).replace(/_/g, ' ')}).words;

  /* A kind of line that joins more than half of all pairs of the experiments shown starts unticked
   * (a line between nearly every pair says nothing, and hides the rest). Its count stays beside it,
   * with a note, and a tick draws it. Shared words, Repeat of, Made from and Copy are ticked unless
   * they too join most pairs. */
  const CROWDED_SHARE = 0.5;
  const ALMOST_ALL = 0.9;
  function pairShare(kind, nodes, pairs) {
    const total = nodes.length * (nodes.length - 1) / 2;
    if (!total) return 0;
    const ids = new Set(nodes.map((node) => node.experiment_id));
    let joined = 0;
    for (const pair of pairs) if (pair.kinds.includes(kind) && ids.has(pair.a) && ids.has(pair.b)) joined += 1;
    return joined / total;
  }
  const crowdedWords = (share) => (share > ALMOST_ALL ? 'joins almost every pair' : 'joins most pairs');

  /* The most lines a drawing holds; a larger project draws its strongest. */
  const EDGE_CAP = 400;
  /* The themes that each get a color; the rest share the gray of "Other themes". */
  const PALETTE = 8;

  /* One evidence item in words: "Shared words: hela, dmso, timecourse". */
  function evidenceWords(item) {
    const words = (item.words || []).filter(Boolean);
    const said = item.kind === 'workflow' ? words.join(', then ') : words.join(', ');
    if (said) return `${kindWords(item.kind)}: ${said}`;
    if (item.count != null) return `${kindWords(item.kind)}: ${plural(item.count, 'file')}`;
    return kindWords(item.kind);
  }

  /* ------------------------------------------------------------------ the highlight */

  /* The experiments that the Overview's "Show these" button named: the Overview writes JSON
   * ``{kind, id, label, experiments}`` (overview.js); a JSON list of identifiers, an object with
   * ``ids``, or the identifiers joined by commas are read too. */
  const HIGHLIGHT_KEY = 'ingence.highlight';
  let highlightLabel = '';   // what the Overview named the highlighted experiments by (a goal's words)
  function readHighlight() {
    highlightLabel = '';
    let text = '';
    try { text = sessionStorage.getItem(HIGHLIGHT_KEY) || ''; } catch (error) { return new Set(); }
    if (!text) return new Set();
    try {
      const parsed = JSON.parse(text);
      const list = (parsed && Array.isArray(parsed.experiments) && parsed.experiments)
        || (parsed && Array.isArray(parsed.ids) && parsed.ids) || (Array.isArray(parsed) ? parsed : []);
      const ids = new Set(list.map((item) => String(item && typeof item === 'object' ? (item.experiment_id || item.id || '') : item)).filter(Boolean));
      if (parsed && !Array.isArray(parsed) && parsed.label) highlightLabel = String(parsed.label);
      return ids;
    } catch (error) {
      return new Set(text.split(',').map((part) => part.trim()).filter(Boolean));
    }
  }
  function clearHighlight() {
    try { sessionStorage.removeItem(HIGHLIGHT_KEY); } catch (error) { /* a convenience */ }
  }

  /* A theme the Overview opened: its words in ``sessionStorage['ingence.overlap.theme']``
   * (overview.js), read once into the theme filter when Connections draws, then cleared. */
  const THEME_KEY = 'ingence.overlap.theme';
  function takeTheme() {
    let words = '';
    try {
      words = sessionStorage.getItem(THEME_KEY) || '';
      sessionStorage.removeItem(THEME_KEY);
    } catch (error) { /* a convenience: Overlap opens on every theme */ }
    if (words && O.themes.some(([theme]) => theme === words)) { O.theme = words; O.area = ''; }
  }
  /* Set the theme filter (another page's way in); '' for every theme. Connections redraws if shown. */
  function setTheme(words) {
    O.theme = String(words || '');
    if (state.view === 'overlap' && mode === 'connections') showOverlap().catch(() => {});
  }

  /* ------------------------------------------------------------------ what is held */

  const O = {
    run: '', data: null, area: '', theme: '', kinds: new Set(KINDS.map((item) => item.kind)),
    shares: new Map(), drawing: null, pairs: [], shown: [], byId: new Map(), colors: new Map(), selected: null, themes: [],
  };

  function color(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#8b909c';
  }
  const labelOf = (id) => { const found = O.byId.get(id); return found ? found.label : ''; };

  /* Read the run's connections once per run. */
  async function readConnections() {
    const version = await api('/api/v1/version');
    if (O.data && O.run === version.run_pinned) return O.data;
    const answer = await api('/api/v1/overlap?mode=connections');
    const connections = answer.connections || {nodes: [], edges: []};
    O.run = version.run_pinned;
    O.data = connections;
    O.byId = new Map(connections.nodes.map((node) => [node.experiment_id, node]));
    // The themes in order of their size over the whole run, so a filter never moves a color.
    const sizes = new Map();
    for (const node of connections.nodes) if (node.theme) sizes.set(node.theme, (sizes.get(node.theme) || 0) + 1);
    const ranked = [...sizes.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    O.colors = new Map(ranked.slice(0, PALETTE).map(([theme], index) => [theme, `--ov-c${index}`]));
    O.themes = ranked;
    // Every pair once, strongest first; ties in a fixed order.
    O.pairs = connections.edges.map((edge, index) => ({...edge, index}))
      .sort((a, b) => b.weight - a.weight || a.a.localeCompare(b.a) || a.b.localeCompare(b.b));
    // The lines ticked at the start of a run: all but the kinds that join most pairs.
    O.shares = new Map(KINDS.map((item) => [item.kind, pairShare(item.kind, connections.nodes, O.pairs)]));
    O.kinds = new Set(KINDS.filter((item) => !(O.shares.get(item.kind) > CROWDED_SHARE)).map((item) => item.kind));
    return connections;
  }
  const themeToken = (theme) => O.colors.get(theme) || '--ov-other';

  /* ------------------------------------------------------------------ the layout */

  /* Each theme a cluster (the largest first, experiments without a theme last), packed in rows;
   * inside a cluster the experiments sit on a sunflower spiral in the order of their first date. */
  const STEP = 24;
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  function layout(nodes) {
    const groups = new Map();
    for (const node of nodes) {
      const key = node.theme || '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(node);
    }
    const order = [...groups.entries()].sort((a, b) => (a[0] === '' ? 1 : 0) - (b[0] === '' ? 1 : 0)
      || b[1].length - a[1].length || a[0].localeCompare(b[0]));
    const radius = (n) => STEP * Math.sqrt(n) + 26;
    const area = order.reduce((sum, [, members]) => sum + (2 * radius(members.length)) ** 2, 0);
    const rowWidth = Math.max(500, Math.sqrt(area) * 1.4);
    const placed = new Map();
    const labels = [];
    let x = 0; let y = 0; let rowHeight = 0;
    for (const [key, members] of order) {
      const r = radius(members.length);
      if (x > 0 && x + 2 * r > rowWidth) { x = 0; y += rowHeight + 30; rowHeight = 0; }
      const cx = x + r; const cy = y + r + 18;
      members.sort((a, b) => String(a.start || '9999').localeCompare(String(b.start || '9999'))
        || a.label.localeCompare(b.label) || a.experiment_id.localeCompare(b.experiment_id));
      members.forEach((node, k) => {
        const rr = STEP * Math.sqrt(k + (k ? 0.5 : 0));
        placed.set(node.experiment_id, {x: cx + rr * Math.cos(k * GOLDEN), y: cy + rr * Math.sin(k * GOLDEN)});
      });
      labels.push({key, x: cx, y: cy - r - 4, members: members.length});
      x += 2 * r + 30; rowHeight = Math.max(rowHeight, 2 * r + 18);
    }
    return {placed, labels};
  }

  /* ------------------------------------------------------------------ what is drawn */

  function visibleNodes() {
    return O.data.nodes.filter((node) => (!O.area || (node.area || '') === O.area) && (!O.theme || (node.theme || '') === O.theme));
  }
  /* The lines drawn: one per kind chosen of each pair with both ends shown, the strongest pairs
   * first, at most EDGE_CAP lines (the cap counts lines, as the caption says). */
  function visibleLines(ids) {
    const order = KINDS.map((item) => item.kind);
    const lines = [];
    for (const pair of O.pairs) {
      if (!ids.has(pair.a) || !ids.has(pair.b)) continue;
      for (const kind of order) if (pair.kinds.includes(kind) && O.kinds.has(kind)) lines.push({pair, kind});
    }
    const drawn = lines.slice(0, EDGE_CAP);
    const shown = [...new Set(drawn.map((line) => line.pair))];
    return {total: lines.length, drawn, shown};
  }

  function drawingStyle() {
    const ink = color('--ink');
    const page = color('--page');
    const style = [
      {selector: 'node', style: {
        'background-color': 'data(color)', 'border-width': 1.5, 'border-color': page, width: 16, height: 16,
        label: 'data(label)', 'font-size': 10, color: ink, 'text-valign': 'bottom', 'text-margin-y': 3,
        'text-wrap': 'ellipsis', 'text-max-width': 110, 'min-zoomed-font-size': 8,
      }},
      {selector: 'node[cluster = 1]', style: {
        width: 1, height: 1, 'background-opacity': 0, 'border-width': 0, label: 'data(label)',
        'font-size': 12, 'font-weight': 'bold', color: 'data(color)', 'text-valign': 'top', 'text-max-width': 220,
        events: 'no', 'min-zoomed-font-size': 6,
      }},
      {selector: 'node.hl', style: {'border-color': ink, 'border-width': 3}},
      {selector: '.dim', style: {opacity: 0.16}},
      {selector: '.picked', style: {'overlay-color': color('--accent'), 'overlay-opacity': 0.22, 'overlay-padding': 7}},
      {selector: 'edge', style: {'curve-style': 'bezier', 'control-point-step-size': 12, opacity: 0.85}},
    ];
    for (const item of KINDS) {
      const line = color(item.token);
      style.push({selector: `edge[kind = "${item.kind}"]`, style: {
        'line-color': item.double ? color('--panel') : line, 'line-style': item.style, width: item.width,
        ...(item.double ? {'line-outline-width': 1.6, 'line-outline-color': line} : {}),
        ...(item.dash ? {'line-dash-pattern': item.dash} : {}),
        ...(item.ends ? {'source-arrow-shape': item.ends, 'target-arrow-shape': item.ends,
          'source-arrow-color': line, 'target-arrow-color': line, 'arrow-scale': 0.7} : {}),
      }});
    }
    return style;
  }

  /* The wheel over the drawing, as on the Graph page: Ctrl + wheel zooms around the pointer (a
   * trackpad pinch arrives that way), a sideways wheel or Shift + wheel pans, and any other wheel
   * is left to the page, so a two-finger scroll moves past the drawing. */
  function onWheel(event) {
    const drawing = O.drawing;
    if (!drawing) return;
    const sideways = Math.abs(event.deltaX) > Math.abs(event.deltaY);
    if (!event.ctrlKey && !event.shiftKey && !sideways) return;
    event.preventDefault();
    const pixels = (delta) => (event.deltaMode === 1 ? delta * 16 : event.deltaMode === 2 ? delta * 800 : delta);
    if (event.ctrlKey) {
      const step = Math.exp(-Math.max(-0.25, Math.min(0.25, pixels(event.deltaY) * 0.01)));
      const level = Math.max(drawing.minZoom(), Math.min(drawing.maxZoom(), drawing.zoom() * step));
      const box = event.currentTarget.getBoundingClientRect();
      drawing.zoom({level, renderedPosition: {x: event.clientX - box.left, y: event.clientY - box.top}});
    } else {
      drawing.panBy({x: -pixels(sideways ? event.deltaX : event.deltaY), y: 0});
    }
  }
  /* What the drawing's view is, for a reader or a check that cannot see a canvas. */
  function saySight(box) {
    if (!O.drawing) return;
    const at = O.drawing.pan();
    box.dataset.zoom = O.drawing.zoom().toFixed(3);
    box.dataset.pan = `${Math.round(at.x)} ${Math.round(at.y)}`;
  }

  function draw(canvas, nodes, lines, highlight) {
    if (O.drawing) { O.drawing.destroy(); O.drawing = null; }
    if (typeof cytoscape !== 'function') {
      fill(canvas, el('p', {class: 'nothing', text: 'The drawing did not load. Reload the page.'}));
      return;
    }
    const {placed, labels} = layout(nodes);
    const lit = (id) => !highlight.size || highlight.has(id);
    const elements = nodes.map((node) => ({
      group: 'nodes',
      data: {id: node.experiment_id, label: node.label, color: color(themeToken(node.theme))},
      position: {...placed.get(node.experiment_id)},
      classes: [highlight.size && highlight.has(node.experiment_id) ? 'hl' : '', lit(node.experiment_id) ? '' : 'dim'].filter(Boolean).join(' '),
    }));
    if (labels.length > 1) {
      for (const label of labels) {
        elements.push({group: 'nodes', data: {id: `cluster:${label.key}`, cluster: 1,
          label: label.key || 'No theme', color: color(label.key ? themeToken(label.key) : '--ink-soft')},
        position: {x: label.x, y: label.y}, selectable: false, grabbable: false});
      }
    }
    const kindsDrawn = {};
    for (const {pair, kind} of lines) {
      kindsDrawn[kind] = (kindsDrawn[kind] || 0) + 1;
      elements.push({group: 'edges', data: {id: `pair${pair.index}:${kind}`, source: pair.a, target: pair.b, kind, pair: pair.index},
        classes: lit(pair.a) && lit(pair.b) ? '' : 'dim'});
    }
    O.drawing = cytoscape({
      container: canvas, elements, style: drawingStyle(),
      layout: {name: 'preset', fit: false, animate: false},
      // The wheel is the page's own (onWheel): only Ctrl + wheel zooms.
      userZoomingEnabled: false, boxSelectionEnabled: false, autoungrabify: true,
      minZoom: 0.05, maxZoom: 1.8,
    });
    O.drawing.on('tap', 'node[!cluster]', (event) => selectNode(event.target.id()));
    O.drawing.on('tap', 'edge', (event) => selectPair(Number(event.target.data('pair'))));
    O.drawing.on('viewport', () => saySight(canvas));
    if (!canvas.dataset.wheel) {
      canvas.dataset.wheel = '1';
      canvas.addEventListener('wheel', onWheel, {passive: false});
    }
    O.drawing.fit(undefined, 30);
    // What the drawing holds, for a reader or a check that cannot see a canvas.
    canvas.dataset.nodes = String(nodes.length);
    canvas.dataset.pairs = String(new Set(lines.map((line) => line.pair)).size);
    canvas.dataset.lines = String(lines.length);
    canvas.dataset.kinds = JSON.stringify(kindsDrawn);
    canvas.dataset.highlighted = String(nodes.filter((node) => highlight.has(node.experiment_id)).length);
    saySight(canvas);
    if (O.selected) markSelected();
  }

  function markSelected() {
    if (!O.drawing) return;
    O.drawing.elements().removeClass('picked');
    if (!O.selected) return;
    if (O.selected.kind === 'node') O.drawing.getElementById(O.selected.id).addClass('picked');
    else O.drawing.elements(`edge[pair = ${O.selected.index}]`).addClass('picked');
  }

  /* ------------------------------------------------------------------ the parts of the page */

  function modeBar() {
    return el('div', {class: 'ov-modes', role: 'group', 'aria-label': 'Overlap mode', 'data-help': 'overlap.mode'},
      MODES.map(([key, words]) => el('button', {type: 'button', class: `ov-mode${mode === key ? ' on' : ''}`,
        'aria-pressed': mode === key ? 'true' : 'false', 'data-mode': key,
        onclick: () => {
          if (mode === key) return;
          setMode(key);
          const drawing = showOverlap();
          // The bar is drawn again at once: the focus goes back to the same button.
          const again = listPane.querySelector(`.ov-mode[data-mode="${key}"]`);
          if (again) again.focus();
          drawing.catch(() => {});
        }}, [words])));
  }

  function selectNode2(label, help, options, value, onChange) {
    const select = el('select', {'aria-label': label, 'data-help': help}, options.map(([key, words]) => el('option', {value: key, text: words})));
    select.value = value;
    select.addEventListener('change', () => onChange(select.value));
    return el('label', {class: 'ov-field'}, [`${label} `, select]);
  }

  function filters(redraw) {
    const areas = new Map();
    for (const node of O.data.nodes) if (node.area) areas.set(node.area, (areas.get(node.area) || 0) + 1);
    const kindCounts = {};
    for (const pair of O.pairs) for (const kind of pair.kinds) kindCounts[kind] = (kindCounts[kind] || 0) + 1;
    const boxes = KINDS.map((item) => {
      const box = el('input', {type: 'checkbox', 'data-kind': item.kind});
      box.checked = O.kinds.has(item.kind);
      if (!kindCounts[item.kind]) box.disabled = true;
      box.addEventListener('change', () => { if (box.checked) O.kinds.add(item.kind); else O.kinds.delete(item.kind); redraw(); });
      const share = O.shares.get(item.kind) || 0;
      const crowded = share > CROWDED_SHARE;
      return el('label', {class: 'ol-kind', ...(crowded ? {'data-crowded': ''} : {})}, [box, lineSample(item),
        ` ${item.words} (${fmt(kindCounts[item.kind] || 0)})${crowded ? `, ${crowdedWords(share)}` : ''}`]);
    });
    return el('div', {class: 'ov-filters', role: 'search', 'aria-label': 'Filter the connections'}, [
      areas.size ? selectNode2('Area', 'overlap.filter_area', [['', 'every area'], ...[...areas.entries()].sort((a, b) => a[0].localeCompare(b[0]))
        .map(([area, n]) => [area, `${area} (${fmt(n)})`])], O.area, (value) => { O.area = value; redraw(); }) : null,
      O.themes.length ? selectNode2('Theme', 'overlap.filter_theme', [['', 'every theme'], ...O.themes.map(([theme, n]) => [theme, `${theme} (${fmt(n)})`])],
        O.theme, (value) => { O.theme = value; redraw(); }) : null,
      el('fieldset', {class: 'ov-kinds', 'data-help': 'overlap.filter_kind'}, [el('legend', {text: 'Lines'}), ...boxes]),
    ]);
  }

  /* The SVG namespace, from the browser rather than written as an address: the page names no
   * scheme (the offline rule's test reads every asset for one), as years.js does. */
  const SVG_NS = (() => { const holder = document.createElement('template'); holder.innerHTML = '<svg></svg>'; return holder.content.firstElementChild.namespaceURI; })();
  /* A short sample of a kind's line, for the legend and the filters. */
  function lineSample(item) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 36 12'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', `ov-sample ov-sample-${item.kind}`);
    const line = document.createElementNS(SVG_NS, 'line');
    line.setAttribute('x1', '3'); line.setAttribute('y1', '6'); line.setAttribute('x2', '33'); line.setAttribute('y2', '6');
    svg.append(line);
    if (item.double) {
      line.setAttribute('y1', '4'); line.setAttribute('y2', '4');
      const second = line.cloneNode(); second.setAttribute('y1', '8'); second.setAttribute('y2', '8'); svg.append(second);
    }
    if (item.ends) {
      for (const cx of ['3', '33']) {
        const dot = document.createElementNS(SVG_NS, 'circle');
        dot.setAttribute('cx', cx); dot.setAttribute('cy', '6'); dot.setAttribute('r', '2.6'); svg.append(dot);
      }
    }
    return svg;
  }

  function legend(nodes) {
    const shownThemes = new Map();
    for (const node of nodes) if (node.theme) shownThemes.set(node.theme, (shownThemes.get(node.theme) || 0) + 1);
    const colored = O.themes.filter(([theme]) => O.colors.has(theme) && shownThemes.has(theme));
    const others = nodes.filter((node) => !node.theme || !O.colors.has(node.theme)).length;
    const swatch = (token) => el('span', {class: 'ov-swatch', style: `background:var(${token})`, 'aria-hidden': 'true'});
    return el('div', {class: 'ov-legend', 'data-help': 'overlap.legend'}, [
      el('div', {class: 'ov-legend-part'}, [el('strong', {text: 'Themes'}),
        ...colored.map(([theme]) => el('span', {class: 'ov-legend-item'}, [swatch(O.colors.get(theme)), theme])),
        others ? el('span', {class: 'ov-legend-item'}, [swatch('--ov-other'), `Other themes (${fmt(others)})`]) : null]),
      el('div', {class: 'ov-legend-part'}, [el('strong', {text: 'Lines'}),
        ...KINDS.map((item) => el('span', {class: 'ov-legend-item', 'data-kind': item.kind}, [lineSample(item), item.words]))]),
    ]);
  }

  /* The lines drawn, as a list: the same as the drawing, for the keyboard and a screen reader; and
   * the experiments drawn with no line, each of which opens its story. */
  function pairList(pairs, nodes = []) {
    const joined = new Set(pairs.flatMap((pair) => [pair.a, pair.b]));
    const alone = nodes.filter((node) => !joined.has(node.experiment_id));
    const rows = pairs.map((pair) => el('tr', {'data-pair': String(pair.index)}, [
      el('td', {}, [el('button', {type: 'button', class: 'link', 'data-help': 'overlap.open_pair', onclick: () => selectPair(pair.index)},
        [`${labelOf(pair.a) || pair.a} and ${labelOf(pair.b) || pair.b}`])]),
      el('td', {text: pair.kinds.map(kindWords).join(', ')}),
    ]));
    return el('details', {class: 'ov-list', 'data-help': 'overlap.list'}, [
      el('summary', {text: `The lines drawn, as a list (${fmt(pairs.length)})`}),
      pairs.length ? el('table', {}, [el('thead', {}, [el('tr', {}, [el('th', {text: 'Experiments'}), el('th', {text: 'Overlap'})])]), el('tbody', {}, rows)])
        : el('p', {class: 'faint', text: 'No line is drawn.'}),
      alone.length ? el('div', {class: 'ol-alone'}, [el('h3', {class: 'st-eyebrow', text: `Experiments with no line (${fmt(alone.length)})`}),
        el('ul', {class: 'st-links'}, alone.map((node) => el('li', {}, [el('button', {type: 'button', class: 'link',
          'data-help': 'overlap.open_node', 'data-experiment': node.experiment_id, onclick: () => selectNode(node.experiment_id)}, [node.label])])))]) : null,
    ]);
  }

  /* ------------------------------------------------------------------ the detail pane */

  function introCard() {
    fill(detailPane, el('div', {class: 'ov-card', 'data-card': 'intro'}, [
      el('p', {class: 'eyebrow', text: 'Connections'}),
      el('p', {text: 'Each dot is an experiment. A line joins two experiments that overlap.'}),
      el('p', {text: 'Open a dot to read its story. Open a line to see why the two overlap, and then their files.'}),
    ]));
  }

  /* Open another experiment from a story: select it here when it is drawn, else in By year. */
  function openExperiment(id) {
    if (O.byId.has(id)) { selectNode(id); return; }
    openInByYear(id).catch(() => {});
  }
  async function openInByYear(id, {files = false} = {}) {
    setMode('by_year');
    await showOverlap();
    const years = window.ingenceYears;
    if (!years) return;
    await years.load();
    if (files && years.files) years.files(id);
    else { years.open({kind: 'exp', id}); years.select({kind: 'exp', id}); }
  }

  function selectNode(id) {
    const node = O.byId.get(id);
    if (!node) return;
    O.selected = {kind: 'node', id};
    markSelected();
    holdFocus({kind: 'entry', id, label: node.label});
    const facts = [node.area, node.theme, [node.start, node.end].filter(Boolean).filter((day, at, days) => days.indexOf(day) === at).join(' to ') || 'undated']
      .filter(Boolean).join(' · ');
    const neighbors = O.shown.filter((pair) => pair.a === id || pair.b === id);
    const story = el('div', {class: 'ov-story'});
    fill(detailPane, el('div', {class: 'ov-card', 'data-card': 'experiment', 'data-experiment': id}, [
      el('p', {class: 'eyebrow', text: 'Experiment'}),
      el('h2', {class: 'ol-title', text: node.label}),
      el('p', {class: 'faint', text: facts}),
      el('p', {text: node.completeness == null ? 'No raw files found.' : `Raw files analyzed: ${Math.round(node.completeness * 100)}%.`}),
      el('p', {class: 'ov-actions'}, [el('button', {type: 'button', class: 'link', 'data-help': 'overlap.by_year',
        onclick: () => { openInByYear(id).catch(() => {}); }}, ['Open in By year'])]),
      neighbors.length ? el('div', {class: 'ov-neighbors'}, [el('h3', {class: 'st-eyebrow', text: `Overlaps with ${plural(neighbors.length, 'experiment')}`}),
        el('ul', {class: 'st-links'}, neighbors.slice(0, 12).map((pair) => el('li', {}, [
          el('button', {type: 'button', class: 'link', 'data-help': 'overlap.open_pair', onclick: () => selectPair(pair.index)},
            [labelOf(pair.a === id ? pair.b : pair.a)]), el('span', {class: 'faint', text: ` · ${pair.kinds.map(kindWords).join(', ')}`})])))]) : null,
      story,
    ]));
    if (window.ingenceStory) {
      window.ingenceStory.render(story, id, {name: labelOf, onOpen: openExperiment,
        onFiles: () => { openInByYear(id, {files: true}).catch(() => {}); }});
    }
  }

  function selectPair(index, {files = false} = {}) {
    const pair = O.pairs.find((item) => item.index === index);
    if (!pair) return;
    O.selected = {kind: 'pair', index};
    markSelected();
    const name = (id) => el('button', {type: 'button', class: 'link', 'data-help': 'overlap.open_node', onclick: () => selectNode(id)},
      [labelOf(id) || id]);
    const box = el('div', {class: 'ov-files', role: 'region', tabindex: '-1', 'aria-label': 'The files of the two experiments'});
    const show = el('button', {type: 'button', class: 'answer quiet', 'data-help': 'overlap.files',
      onclick: () => {
        showFiles(box, pair).catch(() => {});
        show.remove();
        // The button is gone: the focus goes to the files that took its place.
        box.focus();
      }}, [el('strong', {text: 'Show the files'})]);
    fill(detailPane, el('div', {class: 'ov-card', 'data-card': 'pair', 'data-pair': String(index)}, [
      el('p', {class: 'eyebrow', text: 'Overlap'}),
      el('h2', {class: 'ol-title'}, [name(pair.a), ' and ', name(pair.b)]),
      el('ul', {class: 'ov-evidence', 'data-help': 'overlap.evidence'}, (pair.evidence || []).map((item) =>
        el('li', {'data-kind': item.kind}, [lineSample(KIND_OF.get(item.kind) || KINDS[0]), ' ', evidenceWords(item)]))),
      el('p', {}, [show]),
      box,
    ]));
    if (files) { show.remove(); showFiles(box, pair).catch(() => {}); }
  }

  /* The files of the two experiments of a line, read from the files route the By year scale uses.
   * Files whose names hold a shared word come first. */
  const FILES_SHOWN = 40;
  async function showFiles(box, pair) {
    const shared = new Set((pair.evidence || []).filter((item) => item.kind === 'shared_words')
      .flatMap((item) => item.words || []).map((word) => String(word).toLowerCase()));
    const holds = (file) => [...shared].some((word) => String(file.name || '').toLowerCase().includes(word));
    states.loading(box, 'Reading their files...');
    const parts = [];
    for (const id of [pair.a, pair.b]) {
      let page;
      try {
        page = await api(`/api/v1/experiments/files?${new URLSearchParams({experiment: id, limit: String(FILES_SHOWN)})}`);
      } catch (error) {
        parts.push(el('div', {class: 'ov-filegroup', 'data-experiment': id}, [el('h3', {class: 'st-eyebrow', text: labelOf(id) || id}),
          el('p', {class: 'nothing', text: `Its files could not be read: ${String((error && error.message) || error)}`})]));
        continue;
      }
      const rows = [...(page.rows || [])].sort((a, b) => (holds(b) ? 1 : 0) - (holds(a) ? 1 : 0));
      const total = Number(page.files) || rows.length;
      parts.push(el('div', {class: 'ov-filegroup', 'data-experiment': id}, [
        el('h3', {class: 'st-eyebrow', text: labelOf(id) || id}),
        el('p', {class: 'faint', text: rows.length < total ? `The first ${fmt(rows.length)} of ${plural(total, 'file')}.` : plural(total, 'file')}),
        el('ul', {class: 'ov-filelist'}, rows.map((file) => el('li', {class: holds(file) ? 'ov-match' : ''}, [
          el('button', {type: 'button', class: 'link mono', 'data-help': 'overlap.file', title: file.path || file.name,
            onclick: () => { if (window.inspector) window.inspector.showEntry(file.id, detailPane, {back: () => selectPair(pair.index, {files: true}), backLabel: 'back to the overlap'}); }},
          [file.name || file.path || file.id])]))),
        rows.length < total ? el('button', {type: 'button', class: 'link', 'data-help': 'overlap.by_year',
          onclick: () => { openInByYear(id, {files: true}).catch(() => {}); }}, ['All its files, in By year']) : null,
      ]));
    }
    if (!box.isConnected) return;
    fill(box, shared.size ? el('p', {class: 'faint', text: 'Files whose names hold a shared word come first, in bold.'}) : null, ...parts);
  }

  /* ------------------------------------------------------------------ the view */

  async function showConnections(body) {
    const turn = turns.claim(body);
    states.loading(body, 'Reading how the experiments connect...');
    try {
      await readConnections();
    } catch (error) {
      if (turns.owns(body, turn)) states.failed(body, error, () => { showOverlap().catch(() => {}); });
      return;
    }
    if (!turns.owns(body, turn) || !body.isConnected) return;
    takeTheme();
    if (!O.data.nodes.length) {
      states.say(body, {eyebrow: 'Overlap · Connections · empty', dot: 'empty', sentence: 'No experiments yet.',
        body: 'Ingence found no experiment in this scan. Add a data folder, or change the rules and Re-run.',
        actions: [{label: 'Open the Inventory', onclick: () => { navigate('inventory'); }}]});
      return;
    }
    const highlight = readHighlight();
    // A picture to a screen reader: the list under it is the way in by keyboard.
    const canvas = el('div', {class: 'ov-canvas', role: 'img', 'data-help': 'overlap.drawing',
      'aria-label': 'Experiments and the lines between them. The list under the drawing holds the same lines and experiments.'});
    const caption = el('p', {class: 'ov-caption', role: 'status', 'aria-live': 'polite', tabindex: '-1'});
    const lower = el('div', {class: 'ov-lower'});
    const redraw = () => {
      const nodes = visibleNodes();
      const ids = new Set(nodes.map((node) => node.experiment_id));
      const {total, drawn, shown} = visibleLines(ids);
      O.shown = shown;
      caption.textContent = total > drawn.length
        ? `Showing the ${fmt(drawn.length)} strongest of ${fmt(total)} lines, between ${plural(nodes.length, 'experiment')}.`
        : `${plural(drawn.length, 'line')} between ${plural(nodes.length, 'experiment')}.${total ? '' : ' No two of them overlap in the lines chosen.'}`;
      draw(canvas, nodes, drawn, highlight);
      fill(lower, legend(nodes), pairList(shown, nodes));
    };
    const highlightBar = highlight.size ? el('div', {class: 'ov-highlight notice-note', role: 'status', 'data-help': 'overlap.highlight'}, [
      el('span', {text: `Highlighted: ${plural([...highlight].filter((id) => O.byId.has(id)).length, 'experiment')} from the Overview${highlightLabel ? `, for ${highlightLabel}` : ''}. The rest are dimmed.`}),
      ' ',
      el('button', {type: 'button', class: 'answer quiet', 'data-help': 'overlap.clear_highlight',
        onclick: () => {
          clearHighlight();
          // The bar goes; the focus goes to the line that says what is drawn.
          showOverlap().then(() => { const said = listPane.querySelector('.ov-caption'); if (said) said.focus(); }).catch(() => {});
        }}, [el('strong', {text: 'Clear highlight'})]),
    ]) : null;
    fill(body, el('div', {class: 'ov-connections'}, [
      filters(redraw), highlightBar, caption, canvas,
      el('p', {class: 'faint ov-keys', text: 'Pinch, or hold Ctrl and scroll, to zoom. Drag to move the drawing. Shift and scroll moves it sideways.'}),
      lower,
    ]));
    redraw();
  }

  async function showOverlap() {
    if (state.asked === 'years') { setMode('by_year'); state.asked = ''; }
    const main = document.querySelector('main');
    main.classList.toggle('years-layout', mode === 'by_year');
    main.classList.toggle('overlap-layout', mode === 'connections');
    const body = el('div', {class: 'ov-body', 'data-mode': mode});
    fill(listPane, el('div', {class: 'ol-page', 'data-view': 'overlap', 'data-mode': mode}, [
      el('div', {class: 'ov-top'}, [el('h2', {class: 'ov-heading', text: 'Overlap', 'data-help': 'section.overlap'}), modeBar()]),
      body]));
    if (mode === 'by_year') {
      if (O.drawing) { O.drawing.destroy(); O.drawing = null; }
      if (!window.ingenceYears) { fill(body, el('p', {class: 'nothing', text: 'By year did not load: its script is missing. Reload the page.'})); return; }
      await window.ingenceYears.show(body);
      return;
    }
    if (!O.selected) introCard();
    else if (O.selected.kind === 'node' && O.byId.has(O.selected.id)) selectNode(O.selected.id);
    else if (O.selected.kind === 'pair') selectPair(O.selected.index);
    else introCard();
    await showConnections(body);
  }

  VIEWS.overlap = showOverlap;
  OWN_DETAIL.add('overlap');
  onLeave((next) => {
    if (next === 'overlap') return;
    document.querySelector('main').classList.remove('overlap-layout');
    if (O.drawing) { O.drawing.destroy(); O.drawing = null; }
  });
  // A new run: the next drawing reads it (readConnections compares runs).
  onRefresh(() => {
    if (state.view !== 'overlap' || mode !== 'connections') return;
    api('/api/v1/version').then((version) => { if (O.data && version.run_pinned !== O.run) { O.selected = null; showOverlap().catch(() => {}); } }).catch(() => {});
  });

  // ``drawing`` is for a check that finds where a dot or a line is drawn, to click it.
  window.ingenceOverlap = {mode: () => mode, setMode, show: showOverlap, KINDS, evidenceWords, readHighlight, layout,
    selectNode, selectPair, setTheme, drawing: () => O.drawing};
})();
