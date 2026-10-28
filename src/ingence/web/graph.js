// @ts-check
/* The Graph page: one folder or file a person chooses, and what directly surrounds it.
 *
 * Reads GET /api/v1/graph/neighborhood and GET /api/v1/graph/edge, whose shapes are the generated
 * declarations in types/api.d.ts, and draws with the vendored Cytoscape.js. What it promises:
 *
 * - Three kinds of line that never blur, each told apart by words and line style as well as color:
 *   indexed containment (thin, solid), a published assertion (thick, solid, arrowed) and a proposed
 *   pair (dashed, labeled "proposed"), shown only when Proposals is on.
 * - The same content in a list beside the drawing, reachable by keyboard and screen reader.
 * - Every line opens its evidence in the inspector. A proposed line is decided on the existing
 *   Suggested pairs section, never here: the graph writes nothing.
 * - The address holds the center, the filters and the selection, so a reload or back and forward
 *   return to the same view.
 * - A deterministic layout: the center's folder above it and everything else in rows below, one
 *   block per ladder (the center's first, the others side by side), and a node keeps its place when
 *   the filters change. The layout is for finding your way. Distance on the page is not evidence of
 *   anything.
 * - Many ladders: a ladder is the chain of levels the Years view climbs. Every ladder the
 *   neighborhood touches has a translucent band behind its nodes, in one categorical palette (the
 *   center's ladder first), and a card in the Ladders list at the right. Color by ladder turns the
 *   bands off, and Lines of work on the rungs asks the engine to read the themes once. The Items
 *   table names each item's ladder. A file no line touches says so in a sentence.
 * - One stop for the keyboard: the plot. Inside it Tab walks the items, an arrow follows a line,
 *   Enter centers the graph on the item, F fits the drawing.
 *
 * Loaded after app.js and review.js, whose el, fill, listPane and showView it calls. Everything it
 * defines stays inside this function except the three entry points on window. */

(function graphPage() {
  /** @typedef {import('./types/api').NeighborhoodResponse} NeighborhoodResponse */
  /** @typedef {import('./types/api').GraphNode} GraphNode */
  /** @typedef {import('./types/api').Ladder} Ladder */
  /** @typedef {import('./types/api').EdgeDetail} EdgeDetail */
  /** @typedef {import('./types/api').ErrorResponse} ErrorResponse */
  /** @typedef {import('./types/api').SearchResponse} SearchResponse */
  /** @typedef {import('./types/api').EntryResponse} EntryResponse */
  /** @typedef {import('./types/api').FindResponse} FindResponse */

  /**
   * What the address holds. ``lines`` asks for the lines of work on the ladders' rungs (builds the
   * themes once); ``color`` is false where Color by ladder is turned off.
   * @typedef {{center: string, relations: string[], proposals: boolean, selected: string,
   *   maxNodes: number, maxEdges: number, maxLadders: number, lines: boolean,
   *   color: boolean}} GraphAddress
   */

  const RELATIONS = [
    {name: 'contains', label: 'Contains (folders)'},
    {name: 'derives', label: 'Produced from'},
    {name: 'references', label: 'Mentions'},
  ];
  const DEFAULT_RELATIONS = ['contains', 'derives', 'references'];
  const DEFAULT_MAX_NODES = 120;
  const DEFAULT_MAX_EDGES = 240;
  /** How many ladders the engine lists unless asked for fewer (its own default). */
  const DEFAULT_MAX_LADDERS = 24;
  /** The categorical palette the ladders are colored from, in order (the center's ladder first);
   * past its length the colors repeat, and the ladder's name beside every color says which. */
  const LADDER_COLORS = 6;
  /** The kinds of line, in words, as the legend and the list say them. */
  const KIND_WORDS = {
    indexed_containment: 'indexed directory',
    published_assertion: 'published',
    proposed_candidate: 'proposed',
  };

  /** @type {CytoscapeCore | null} */
  let drawing = null;
  /** @type {NeighborhoodResponse | null} */
  let shown = null;
  /** The latest page draw and the latest inspector choice, so a stale answer is dropped. */
  let drawingRequest = 0;
  let inspecting = 0;
  /** Positions kept per center, so a node stays where it was when the filters change. */
  /** @type {Map<string, Map<string, {x: number, y: number}>>} */
  const kept = new Map();

  /** The drawing boxes that already listen to the wheel: a box is drawn into again and again. */
  /** @type {WeakSet<HTMLElement>} */
  const wheeled = new WeakSet();
  /**
   * The parts of Cytoscape's view the wheel uses, beyond those globals.d.ts declares.
   * @typedef {{pan(): {x: number, y: number}, panBy(by: {x: number, y: number}): void,
   *   zoom(options: {level: number, renderedPosition: {x: number, y: number}}): void,
   *   minZoom(): number, maxZoom(): number}} CytoscapeView
   */

  const reducedMotion = () => window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ------------------------------------------------------------------ the address

  /** @returns {GraphAddress} */
  function readAddress() {
    const query = new URLSearchParams(location.search);
    const relations = (query.get('relations') || DEFAULT_RELATIONS.join(','))
      .split(',').map((part) => part.trim()).filter(Boolean);
    return {
      center: query.get('center') || '',
      relations,
      proposals: query.get('proposals') === '1',
      selected: query.get('selected') || '',
      maxNodes: Number(query.get('max_nodes')) || DEFAULT_MAX_NODES,
      maxEdges: Number(query.get('max_edges')) || DEFAULT_MAX_EDGES,
      maxLadders: Number(query.get('max_ladders')) || DEFAULT_MAX_LADDERS,
      lines: query.get('lines') === '1',
      color: query.get('color') !== '0',
    };
  }

  /**
   * Write the view into the address: a new history entry for a new center or new filters, the
   * same entry for a selection.
   * @param {GraphAddress} address
   * @param {{push: boolean}} how
   */
  function writeAddress(address, {push}) {
    const query = new URLSearchParams();
    if (address.center) query.set('center', address.center);
    if (address.relations.join(',') !== DEFAULT_RELATIONS.join(',')) {
      query.set('relations', address.relations.join(','));
    }
    if (address.proposals) query.set('proposals', '1');
    if (address.selected) query.set('selected', address.selected);
    if (address.maxNodes !== DEFAULT_MAX_NODES) query.set('max_nodes', String(address.maxNodes));
    if (address.maxEdges !== DEFAULT_MAX_EDGES) query.set('max_edges', String(address.maxEdges));
    if (address.maxLadders !== DEFAULT_MAX_LADDERS) query.set('max_ladders', String(address.maxLadders));
    if (address.lines) query.set('lines', '1');
    if (!address.color) query.set('color', '0');
    const text = query.toString();
    const target = `${location.pathname}${text ? `?${text}` : ''}#graph`;
    if (target === `${location.pathname}${location.search}${location.hash}`) return;
    if (push) history.pushState({graph: true}, '', target);
    else history.replaceState({graph: true}, '', target);
  }

  // ------------------------------------------------------------------ reading

  /**
   * One /api/v1 read, refusing in the API's one error shape.
   * @param {string} path
   * @returns {Promise<any>}
   */
  async function v1(path) {
    const response = await fetch(path);
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      /** @type {ErrorResponse | null} */
      const refused = body;
      throw new Error(refused && refused.error ? refused.error.message : response.statusText);
    }
    return body;
  }

  /**
   * @param {GraphAddress} address
   * @returns {Promise<NeighborhoodResponse>}
   */
  function readNeighborhood(address) {
    const query = new URLSearchParams({
      center: address.center,
      relations: address.relations.join(',') || 'contains',
      include_candidates: address.proposals ? 'true' : 'false',
      max_nodes: String(address.maxNodes),
      max_edges: String(address.maxEdges),
      max_ladders: String(address.maxLadders),
      lines_of_work: address.lines ? 'true' : 'false',
    });
    return v1(`/api/v1/graph/neighborhood?${query}`);
  }

  // ------------------------------------------------------------------ layout

  /** The width of a column of nodes, the height of a row, and the legible size a drawing keeps. */
  const COL_W = 130;
  const ROW_H = 86;
  const BAND_MIN = 240;
  const GAP = 30;
  /** How far a band reaches past the centers of its outer nodes: half a label, and its padding. */
  const BAND_EDGE = 84;
  /** The widest a row of bands may be (in drawing units) before the next band starts a new row. */
  const SHELF_WIDTH = 1040;
  /** The lowest zoom a drawing is fitted at: labels are 13 px, so they stay about 11 px. */
  const FIT_FLOOR = 0.85;

  /** @param {number} count how many nodes stand on the ladder */
  const colsFor = (count) => Math.min(8, Math.max(2, Math.ceil(Math.sqrt(count * 1.5))));

  /** @typedef {{x: number, y: number, cols: number, rows: number, shelf: number, right: number, home: boolean}} Block */
  /** Where each ladder's block starts, kept per center so a block keeps its place.
   * @type {Map<string, Map<string, Block>>} */
  const keptBlocks = new Map();

  /**
   * The next block's place: beside the last on its row, or on a new row when the row is full. The
   * band behind a block reaches past its outer nodes (their labels and its padding), and is never
   * narrower than BAND_MIN, so the next block starts a gap beyond the band's edge.
   * @param {Map<string, Block>} blocks
   * @param {number} count
   * @param {boolean} home
   * @returns {Block}
   */
  function allocate(blocks, count, home) {
    const cols = colsFor(count);
    const span = (Math.min(cols, count) - 1) * COL_W;
    const bandWidth = Math.max(span + BAND_EDGE * 2, BAND_MIN);
    const rows = Math.ceil(count / cols) + (home ? 2 : 0);
    const held = [...blocks.values()];
    let shelf = held.length ? Math.max(...held.map((block) => block.shelf)) : 0;
    const row = held.filter((block) => block.shelf === shelf);
    let left = row.length ? Math.max(...row.map((block) => block.right)) + GAP : 0;
    if (row.length && left + bandWidth > SHELF_WIDTH) {
      shelf += 1;
      left = 0;
    }
    const above = held.filter((block) => block.shelf === shelf - 1);
    const y = shelf === 0 ? 0 : Math.max(...above.map((block) => block.y + block.rows * ROW_H)) + 90;
    return {x: left + (bandWidth - span) / 2, y, cols, rows, shelf, right: left + bandWidth, home};
  }

  /**
   * Positions for every node, kept ones reused. Every ladder is one compact block of columns, so
   * its band lies behind its nodes and never over another's, and the blocks stand side by side as
   * far as the page is wide, the center's ladder first. In the center's block its folder is above
   * it and what it holds below; in any other the folders come first, then the files. A block and a
   * node keep their place once given, so the filters never move what a person has found. The layout
   * is for finding your way; distance on the page is not evidence of anything.
   * @param {NeighborhoodResponse} data
   */
  function positions(data) {
    const memory = kept.get(data.center_id) || new Map();
    kept.set(data.center_id, memory);
    const blocks = keptBlocks.get(data.center_id) || new Map();
    keptBlocks.set(data.center_id, blocks);
    const ladders = laddersOf(data);
    const centerLadder = (ladders.find((ladder) => ladder.is_center_ladder) || {}).id || '';
    /** @param {GraphNode} node */
    const keyOf = (node) => (node.is_center ? centerLadder : node.ladder || '');
    for (const key of new Set([centerLadder, ...ladders.map((ladder) => ladder.id), ''])) {
      const home = key === centerLadder;
      const members = data.nodes.filter((node) => keyOf(node) === key);
      if (!members.length) continue;
      let block = blocks.get(key);
      if (!block) {
        block = allocate(blocks, members.length, home);
        blocks.set(key, block);
      }
      const held = block;
      const middle = Math.floor(held.cols / 2);
      /** @param {number} cell */
      const at = (cell) => ({x: held.x + (cell % held.cols) * COL_W, y: held.y + Math.floor(cell / held.cols) * ROW_H});
      const taken = new Set(members.filter((node) => memory.has(node.id))
        .map((node) => `${(memory.get(node.id) || {}).x},${(memory.get(node.id) || {}).y}`));
      // The center's block holds its folder in the top row and the center under it; the rest
      // follows. Another block lists its folders first.
      const ordered = home ? members
        : [...members].sort((a, b) => Number(b.entry_type === 'directory') - Number(a.entry_type === 'directory'));
      let next = home ? held.cols * 2 : 0;
      for (const node of ordered) {
        if (memory.has(node.id)) continue;
        let cell = home && node.is_center ? held.cols + middle : home && node.placement === 'parent' ? middle : -1;
        if (cell < 0 || taken.has(`${at(cell).x},${at(cell).y}`)) {
          while (taken.has(`${at(next).x},${at(next).y}`)) next += 1;
          cell = next;
          next += 1;
        }
        const place = at(cell);
        taken.add(`${place.x},${place.y}`);
        memory.set(node.id, place);
        held.rows = Math.max(held.rows, Math.floor(cell / held.cols) + 1);
      }
    }
    return memory;
  }

  // ------------------------------------------------------------------ drawing

  /** @param {string} name */
  function color(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#555';
  }

  /**
   * The color of the ladder at this place in the response's list (the center's first).
   * @param {number} index
   */
  function ladderColor(index) {
    return color(`--ladder-${(Math.max(0, index) % LADDER_COLORS) + 1}`);
  }

  /**
   * The ladders a response lists (every response does; the declarations call the field optional).
   * @param {NeighborhoodResponse} data
   */
  function laddersOf(data) {
    return data.ladders || [];
  }

  /** @param {string} ladderId */
  const bandId = (ladderId) => `band:${ladderId}`;

  /**
   * Topics, a second band beside the ladders'. A topic is the person's name for a set of
   * experiments, so a ladder whose experiment rung is in a topic stands in it: its nodes are drawn
   * with a halo in the topic's color (a person's, from the magenta family) and its card lists the
   * topic. A node can stand in one ladder band only, and Cytoscape cannot nest a node in two bands,
   * so a topic is a halo and not a second rectangle. The first topic made colors the halo; the
   * band's title and the card name every topic the ladder is in. A topic is never read as evidence:
   * it changes what is drawn, nothing a rule concluded.
   * @param {Ladder} ladder
   * @returns {TopicKitTopic[]}
   */
  function topicsOfLadder(ladder) {
    const reading = window.topicKit ? topicKit.current() : null;
    const experiment = ladder.rungs.find((rung) => rung.level === 'experiment');
    if (!reading || !experiment || !experiment.id) return [];
    return reading.byExperiment.get(experiment.id) || [];
  }

  /**
   * Each node's topics, by the ladder it stands on.
   * @param {NeighborhoodResponse} data
   * @returns {Map<string, TopicKitTopic[]>}
   */
  function topicsOfNodes(data) {
    /** @type {Map<string, TopicKitTopic[]>} */
    const found = new Map();
    for (const ladder of laddersOf(data)) {
      const topics = topicsOfLadder(ladder);
      if (!topics.length) continue;
      for (const id of ladder.members) found.set(id, topics);
    }
    return found;
  }

  /** @param {TopicKitTopic[]} topics */
  const topicWords = (topics) => (topics.length
    ? ` \u00b7 YOUR COLLECTION${topics.length === 1 ? '' : 'S'}: ${topics.map((topic) => topic.name).join(', ')}` : '');

  /**
   * What a ladder is called: its root, then its experiment (or the folder it stands on).
   * @param {Ladder} ladder
   */
  function ladderName(ladder) {
    const first = ladder.rungs[0];
    const last = ladder.rungs[ladder.rungs.length - 1];
    if (!first) return 'a chain of levels';
    return last && last !== first ? `${first.name} \u203a ${last.name}` : first.name;
  }

  /**
   * The words along a band's top edge.
   * @param {Ladder} ladder
   */
  function bandWords(ladder) {
    return `FOLDER LEVELS \u00b7 ${ladderName(ladder)}${ladder.is_center_ladder ? " (the center's)" : ''}`;
  }

  /** @param {NeighborhoodResponse} data @param {HTMLElement} container @param {string} selected */
  function draw(data, container, selected) {
    if (drawing) {
      drawing.destroy();
      drawing = null;
    }
    if (typeof cytoscape !== 'function') {
      container.replaceChildren(el('p', {class: 'note', text: 'The drawing did not load. The list '
        + 'below holds all of it.'}));
      return;
    }
    // Timed, so a product check can read how long placing and drawing took (performance
    // entries "ingence-graph-layout" and "ingence-graph-draw"; nothing is sent anywhere).
    const began = performance.now();
    const placed = positions(data);
    const laidOut = performance.now();
    performance.measure('ingence-graph-layout', {start: began, end: laidOut});
    // One translucent band per ladder, behind its nodes: a compound parent the nodes stand in.
    // Turning Color by ladder off draws none and leaves every node its own color.
    const shownLadders = readAddress().color ? laddersOf(data).filter((ladder) => ladder.members.length) : [];
    const tints = new Map(shownLadders.map((ladder) => [ladder.id, ladderColor(laddersOf(data).indexOf(ladder))]));
    const inTopics = topicsOfNodes(data);
    const elements = [
      ...shownLadders.map((ladder) => ({
        group: 'nodes',
        data: {id: bandId(ladder.id), band: 1, label: `${bandWords(ladder)}${topicWords(topicsOfLadder(ladder))}`,
          tint: tints.get(ladder.id)},
      })),
      ...data.nodes.map((node) => ({
        group: 'nodes',
        data: {
          id: node.id, label: node.name, type: node.entry_type, center: node.is_center ? 1 : 0,
          ...(node.ladder && tints.has(node.ladder)
            ? {parent: bandId(node.ladder), tint: tints.get(node.ladder)} : {}),
          ...(inTopics.has(node.id) ? {halo: color(`--topic-${((inTopics.get(node.id) || [{index: 0}])[0].index % 6) + 1}`)} : {}),
        },
        position: {...(placed.get(node.id) || {x: 0, y: 0})},
      })),
      ...data.edges.map((edge) => ({
        group: 'edges',
        data: {
          id: edge.id, source: edge.source, target: edge.target, kind: edge.kind,
          label: edge.kind === 'indexed_containment' ? '' : edge.label,
          directed: edge.directed ? 1 : 0,
        },
      })),
    ];
    const ink = color('--ink');
    const soft = color('--ink-soft');
    drawing = cytoscape({
      container,
      elements,
      layout: {name: 'preset', fit: false, animate: false},
      // The wheel is the page's own (onDrawingWheel): a plain scroll moves the page past the
      // drawing, and only Ctrl + wheel (a trackpad pinch) zooms it.
      userZoomingEnabled: false,
      // A neighborhood of one or two items is not blown up to fill the page.
      maxZoom: 1.5,
      boxSelectionEnabled: false,
      autoungrabify: false,
      style: [
        {selector: 'node', style: {
          label: 'data(label)', 'font-size': 13, color: ink, 'text-valign': 'bottom',
          'text-margin-y': 4, 'text-wrap': 'ellipsis', 'text-max-width': 120,
          'background-color': color('--panel'), 'border-width': 1.5, 'border-color': soft,
          shape: 'ellipse', width: 26, height: 26,
        }},
        {selector: 'node[type = "directory"]', style: {shape: 'round-rectangle', width: 34, height: 24}},
        {selector: 'node[center = 1]', style: {
          'border-width': 4, 'border-color': color('--accent'), 'font-weight': 'bold',
        }},
        {selector: 'node[tint][!band]', style: {'border-color': 'data(tint)'}},
        {selector: 'node[halo][!band]', style: {
          'underlay-color': 'data(halo)', 'underlay-opacity': 0.34, 'underlay-padding': 9, 'underlay-shape': 'ellipse',
        }},
        {selector: 'node[band = 1]', style: {
          shape: 'round-rectangle', 'background-color': 'data(tint)', 'background-opacity': 0.13,
          'border-width': 1, 'border-color': 'data(tint)', 'border-opacity': 0.5,
          label: 'data(label)', color: 'data(tint)', 'font-size': 13, 'font-weight': 'bold',
          'text-valign': 'top', 'text-halign': 'center', 'text-margin-y': -4,
          // The whole name, wrapped where it is long: a band's title is never cut off.
          'text-wrap': 'wrap', 'text-overflow-wrap': 'anywhere', 'text-max-width': BAND_MIN - 50,
          padding: 24, 'min-width': BAND_MIN - 48, 'min-height': 90,
          'compound-sizing-wrt-labels': 'include', events: 'no', 'z-compound-depth': 'bottom',
        }},
        {selector: 'edge', style: {
          'curve-style': 'bezier', width: 1.5, 'line-color': soft, 'target-arrow-color': soft,
          label: 'data(label)', 'font-size': 11, color: soft, 'text-rotation': 'autorotate',
          'text-background-color': color('--page'), 'text-background-opacity': 1,
          'text-background-padding': 2,
        }},
        {selector: 'edge[directed = 1]', style: {'target-arrow-shape': 'triangle'}},
        {selector: 'edge[kind = "published_assertion"]', style: {
          width: 3.5, 'line-color': color('--acquisition'), 'target-arrow-color': color('--acquisition'),
          color: color('--acquisition'),
        }},
        {selector: 'edge[kind = "proposed_candidate"]', style: {
          width: 2, 'line-style': 'dashed', 'line-dash-pattern': [7, 5], 'line-color': color('--later'),
          'target-arrow-color': color('--later'), 'target-arrow-fill': 'hollow', color: color('--later'),
        }},
        {selector: '.picked', style: {
          'overlay-color': color('--accent'), 'overlay-opacity': 0.18, 'overlay-padding': 6,
        }},
      ],
    });
    performance.measure('ingence-graph-draw', {start: laidOut, end: performance.now()});
    // What the drawing holds, for a reader or a check that cannot see a canvas: how many bands
    // are behind the nodes, and the ladder each node stands in (none where Color by ladder is off).
    container.dataset.bands = String(shownLadders.length);
    container.dataset.topicNodes = JSON.stringify(Object.fromEntries([...inTopics].map(([id, topics]) => [id, topics.map((topic) => topic.id)])));
    container.dataset.bandNodes = JSON.stringify(Object.fromEntries(data.nodes
      .filter((node) => node.ladder && tints.has(node.ladder)).map((node) => [node.id, node.ladder])));
    drawing.on('tap', 'node[!band]', (event) => select(event.target.id()));
    drawing.on('tap', 'edge', (event) => select(event.target.id()));
    // Positions a person drags are kept for this center, until the page is reloaded.
    drawing.on('dragfree', 'node[!band]', (event) => {
      const memory = kept.get(data.center_id);
      if (memory) memory.set(event.target.id(), {...event.target.position()});
    });
    drawing.on('viewport', () => saySight(container));
    if (!wheeled.has(container)) {
      wheeled.add(container);
      container.addEventListener('wheel', onDrawingWheel, {passive: false});
    }
    fitLegibly();
    if (selected) mark(selected);
  }

  /**
   * What the drawing's view is now, for a reader or a check that cannot see a canvas: the zoom,
   * and how far the drawing is moved, in pixels.
   * @param {HTMLElement} box
   */
  function saySight(box) {
    if (!drawing) return;
    const view = /** @type {CytoscapeView} */ (/** @type {unknown} */ (drawing));
    const at = view.pan();
    box.dataset.zoom = drawing.zoom().toFixed(3);
    box.dataset.pan = `${Math.round(at.x)} ${Math.round(at.y)}`;
  }

  /**
   * The wheel over the drawing. Ctrl + wheel zooms around the pointer: a trackpad pinch arrives
   * that way, and a mouse user holds Ctrl. A sideways wheel, or Shift + wheel, pans. Any other
   * wheel is left to the page, so a two-finger scroll moves past the drawing.
   * @param {WheelEvent} event
   */
  function onDrawingWheel(event) {
    if (!drawing) return;
    const sideways = Math.abs(event.deltaX) > Math.abs(event.deltaY);
    if (!event.ctrlKey && !event.shiftKey && !sideways) return;
    event.preventDefault();
    const view = /** @type {CytoscapeView} */ (/** @type {unknown} */ (drawing));
    /** A delta in pixels, whatever unit the browser gave it in. @param {number} delta */
    const pixels = (delta) => (event.deltaMode === 1 ? delta * 16 : event.deltaMode === 2 ? delta * 800 : delta);
    if (event.ctrlKey) {
      // A notch of a mouse wheel zooms by about a quarter; a pinch comes in many small steps.
      const step = Math.exp(-Math.max(-0.25, Math.min(0.25, pixels(event.deltaY) * 0.01)));
      const level = Math.max(view.minZoom(), Math.min(view.maxZoom(), drawing.zoom() * step));
      const box = /** @type {HTMLElement} */ (event.currentTarget).getBoundingClientRect();
      view.zoom({level, renderedPosition: {x: event.clientX - box.left, y: event.clientY - box.top}});
    } else {
      // Shift + wheel: some browsers turn the upright delta sideways, others leave it upright.
      view.panBy({x: -pixels(sideways ? event.deltaX : event.deltaY), y: 0});
    }
  }

  /**
   * Fit the drawing to the page, but never so small that a label is hard to read: where the whole
   * is wider than the page allows at FIT_FLOOR, the drawing stays at that zoom with the center's
   * ladder in view, and the person pans to the rest (F fits again).
   */
  function fitLegibly() {
    if (!drawing) return;
    drawing.fit(undefined, 24);
    if (drawing.zoom() < FIT_FLOOR) {
      drawing.zoom(FIT_FLOOR);
      const center = drawing.nodes('[center = 1]');
      const anchor = center.parent().length ? center.parent() : center;
      const box = anchor.boundingBox({});
      drawing.pan({x: 24 - box.x1 * FIT_FLOOR, y: 24 - box.y1 * FIT_FLOOR});
    }
    // What the drawing shows, for a reader or a check that cannot see a canvas: the zoom, and the
    // box each band fills on the page, in pixels from the drawing's top left.
    const box = drawing.container ? drawing.container() : null;
    if (box) {
      box.dataset.zoom = drawing.zoom().toFixed(3);
      box.dataset.bandBoxes = JSON.stringify(drawing.nodes('[band = 1]').map((band) => {
        const at = band.renderedBoundingBox({});
        return {id: band.id(), x1: Math.round(at.x1), y1: Math.round(at.y1), x2: Math.round(at.x2), y2: Math.round(at.y2)};
      }));
    }
  }

  /**
   * Mark the chosen item on the drawing, and bring it into view: moved there smoothly, or at
   * once where the person's system asks for reduced motion.
   * @param {string} id
   * @param {{bring?: boolean}} [how]
   */
  function mark(id, {bring = false} = {}) {
    if (!drawing) return;
    drawing.elements().removeClass('picked');
    const found = drawing.getElementById(id);
    if (!found || !found.length) return;
    found.addClass('picked');
    if (!bring) return;
    if (reducedMotion()) drawing.center(found);
    else drawing.animate({center: {eles: found}}, {duration: 250});
  }

  // ------------------------------------------------------------------ the page

  /** The regions, cloned from index.html's template. */
  function regions() {
    const template = /** @type {HTMLTemplateElement | null} */ (document.getElementById('graph-template'));
    if (!template) throw new Error('index.html holds no graph-template');
    const page = /** @type {HTMLElement} */ (template.content.firstElementChild?.cloneNode(true));
    /** @param {string} name */
    const part = (name) => /** @type {HTMLElement} */ (page.querySelector(`[data-graph="${name}"]`));
    return {page, part};
  }

  /** The page with nothing chosen: pick a folder or file to start from. */
  function chooseState() {
    const {page, part} = regions();
    page.classList.add('choosing');
    part('title').textContent = 'Choose a folder or file';
    fill(part('status'), el('p', {class: 'lead', text: 'Search below, or use Show connections on '
      + 'a file or folder.'}));
    /** @type {HTMLDetailsElement} */ (part('choose')).open = true;
    part('canvas-wrap').hidden = true;
    part('list').hidden = true;
    fill(part('inspector'), el('p', {class: 'empty', text: 'Nothing selected.'}));
    wireChooser(part('choose'));
    listPane.replaceChildren(page);
  }

  /** @param {HTMLElement} chooser */
  function wireChooser(chooser) {
    const input = /** @type {HTMLInputElement} */ (chooser.querySelector('input'));
    const results = /** @type {HTMLElement} */ (chooser.querySelector('[data-graph="results"]'));
    const run = async () => {
      const typed = input.value.trim();
      if (!typed) return;
      fill(results, el('p', {class: 'empty', text: `searching for "${typed}"...`}));
      try {
        // Two answers side by side: the experiments and folders a search reads the words as,
        // and every live folder or file whose path holds them, so any single file can be a center.
        const [data, found] = await Promise.all([
          /** @type {Promise<SearchResponse>} */ (v1(`/api/v1/search?${new URLSearchParams({q: typed})}`)),
          /** @type {Promise<FindResponse>} */ (v1(`/api/v1/graph/find?${new URLSearchParams({q: typed})}`)),
        ]);
        if (!data.results.length && !found.matches.length) {
          fill(results, el('p', {class: 'nothing', text: 'Nothing matched. Try one word from a name.'}));
          return;
        }
        fill(results,
          data.results.length ? el('h4', {text: 'Experiments and folders'}) : null,
          data.results.length ? el('ul', {class: 'graph-results'}, data.results.map(
            (card) => el('li', {}, [graphButton(card.folder_id, card.path || card.folder_id),
              el('span', {class: 'faint', text: ` ${card.unit_kind}`})]))) : null,
          found.matches.length ? el('h4', {text: 'Paths holding it'}) : null,
          found.matches.length ? el('ul', {class: 'graph-results'}, found.matches.map(
            (match) => el('li', {}, [graphButton(match.id, match.path || match.name),
              el('span', {class: 'faint', text: ` ${plainValue(match.entry_type)}`})]))) : null,
          found.more ? el('p', {class: 'faint', text: 'More paths hold it than are listed.'}) : null,
        );
      } catch (error) {
        fill(results, el('p', {class: 'note', text: String(error instanceof Error ? error.message : error)}));
      }
    };
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') run(); });
    const button = chooser.querySelector('button');
    if (button) button.addEventListener('click', run);
  }

  /** The Graph page, as the address says. Called by app.js's showGraph. */
  async function showGraphPage() {
    const address = readAddress();
    if (!address.center) {
      chooseState();
      return;
    }
    const {page, part} = regions();
    part('title').textContent = 'Connections';
    fill(part('status'), el('p', {class: 'empty', text: address.lines
      ? 'Loading the connections and the themes. The first time, Ingence builds the themes...'
      : 'reading the neighborhood...'}));
    listPane.replaceChildren(page);
    wireChooser(part('choose'));
    wireControls(part, address);
    const mine = ++drawingRequest;
    let data;
    try {
      data = await readNeighborhood(address);
      // A newer address was drawn meanwhile (a click, back or forward): this answer is stale.
      if (mine !== drawingRequest) return;
    } catch (error) {
      if (mine !== drawingRequest) return;
      fill(part('status'), el('p', {class: 'note', text: String(error instanceof Error ? error.message : error)}),
        el('button', {class: 'link', text: 'choose another folder or file',
          onclick: () => { writeAddress({...address, center: '', selected: ''}, {push: true}); showGraphPage(); }}));
      part('canvas-wrap').hidden = true;
      part('list').hidden = true;
      return;
    }
    // A file no line touches is drawn alone, whatever relations are on: the sentence is about
    // lines, and nothing is drawn beside the file to look like one.
    const lone = isLone(data);
    const view = lone ? centerAlone(data) : data;
    shown = view;
    const center = view.nodes.find((node) => node.is_center);
    part('title').textContent = center ? `Connections: ${center.name}` : 'Connections';
    renderStatus(part('status'), view, lone);
    renderCaps(part('caps'), view, address);
    const canvas = part('canvas');
    canvas.setAttribute('aria-label', `A drawing of ${view.nodes.length} items and `
      + `${view.edges.length} relationships around ${center ? center.name : 'the center'}, on `
      + `${laddersOf(view).length} ladder${laddersOf(view).length === 1 ? '' : 's'}. `
      + 'The list below holds the same content.');
    wirePlot(canvas);
    // Topics are the person's and optional: read (once, kept) before the first draw so the halos are
    // there at once; a reading that fails draws the neighborhood without them.
    if (window.topicKit) await topicKit.read().catch(() => null);
    if (mine !== drawingRequest) return;
    renderLadders(part('ladders'), view, address);
    renderList(part('list'), view);
    draw(view, canvas, address.selected);
    part('band-legend').hidden = !address.color || !laddersOf(view).length;
    part('topic-legend').hidden = !topicsOfNodes(view).size;
    renderLone(part('lone'), view, address, mine, lone).catch(reportError);
    if (plotHadFocus) canvas.focus({preventScroll: true});
    plotHadFocus = false;
    const known = address.selected
      && (view.nodes.some((node) => node.id === address.selected)
        || view.edges.some((edge) => edge.id === address.selected));
    await inspect(known ? address.selected : view.center_id);
  }

  /**
   * The toolbar: relation toggles, Proposals, caps.
   * @param {(name: string) => HTMLElement} part
   * @param {GraphAddress} address
   */
  function wireControls(part, address) {
    const toggles = part('relations');
    toggles.replaceChildren(...RELATIONS.map((relation) => {
      const box = /** @type {HTMLInputElement} */ (el('input', {type: 'checkbox', value: relation.name}));
      box.checked = address.relations.includes(relation.name);
      box.addEventListener('change', () => {
        // Relations the toggles do not show (embeds, say, added from the status line) are kept.
        const current = readAddress().relations;
        const chosen = box.checked ? [...current.filter((name) => name !== relation.name), relation.name]
          : current.filter((name) => name !== relation.name);
        if (!chosen.length) { box.checked = true; return; }
        writeAddress({...readAddress(), relations: chosen}, {push: true});
        showGraphPage().catch(reportError);
      });
      return el('label', {}, [box, ` ${relation.label}`]);
    }));
    const proposals = /** @type {HTMLInputElement} */ (part('proposals').querySelector('input'));
    proposals.checked = address.proposals;
    proposals.addEventListener('change', () => {
      writeAddress({...readAddress(), proposals: proposals.checked}, {push: true});
      showGraphPage().catch(reportError);
    });
    // Ladders: Color by ladder redraws what is held; Lines of work on the rungs asks the engine
    // again (it builds the themes once, the first time).
    const colored = /** @type {HTMLInputElement} */ (part('color').querySelector('input'));
    colored.checked = address.color;
    colored.addEventListener('change', () => {
      writeAddress({...readAddress(), color: colored.checked}, {push: false});
      if (!shown) return;
      const now = readAddress();
      renderLadders(part('ladders'), shown, now);
      renderList(part('list'), shown);
      draw(shown, part('canvas'), now.selected);
      part('band-legend').hidden = !now.color || !laddersOf(shown).length;
      part('topic-legend').hidden = !topicsOfNodes(shown).size;
    });
    const lines = /** @type {HTMLInputElement} */ (part('lines').querySelector('input'));
    lines.checked = address.lines;
    lines.addEventListener('change', () => {
      writeAddress({...readAddress(), lines: lines.checked}, {push: true});
      showGraphPage().catch(reportError);
    });
    fill(part('caps'), `Up to ${address.maxNodes} items and ${address.maxEdges} lines, one step out.`);
  }

  /**
   * The caps, and what the ladders were held to.
   * @param {HTMLElement} caps
   * @param {NeighborhoodResponse} data
   * @param {GraphAddress} address
   */
  function renderCaps(caps, data, address) {
    const ladders = `${data.ladders_total} ladder${data.ladders_total === 1 ? '' : 's'} here; `
      + `up to ${address.maxLadders} are drawn, the rest counted.`;
    fill(caps, `Up to ${address.maxNodes} items and ${address.maxEdges} lines, one step out. ${ladders}`);
  }

  /**
   * The run, what was left out, and what could not be connected.
   * @param {HTMLElement} status
   * @param {NeighborhoodResponse} data
   * @param {boolean} [lone] the file is drawn alone
   */
  function renderStatus(status, data, lone = false) {
    const address = readAddress();
    const parts = [];
    parts.push(el('p', {class: data.pinned.job_running ? 'note pinned' : 'faint', 'data-help': 'graph.run'},
      [data.pinned.message]));
    // Where the file is drawn alone, what the caps left out is not said: nothing else is drawn.
    if (!lone) {
      parts.push(el('p', {class: data.truncation.capped ? 'note' : 'faint', 'data-help': 'graph.caps'},
        [data.truncation.message, ' ',
          data.truncation.capped && address.maxNodes < 1000 ? el('button', {class: 'link', text: 'raise the caps',
            onclick: () => {
              writeAddress({...address, maxNodes: Math.min(1000, address.maxNodes * 2),
                maxEdges: Math.min(2000, address.maxEdges * 2)}, {push: true});
              showGraphPage().catch(reportError);
            }}) : null]));
    }
    parts.push(el('p', {class: 'faint', text: data.availability.message}));
    const unresolved = data.availability.unresolved;
    if (unresolved.count) {
      parts.push(el('details', {class: 'graph-unresolved', 'data-help': 'graph.unresolved'}, [
        el('summary', {text: `${unresolved.count} relation${unresolved.count === 1 ? '' : 's'} `
          + 'name nothing in this scan, so nothing is drawn for them'}),
        el('ul', {class: 'ids'}, unresolved.items.map((item) => el('li', {}, [
          `${item.primitive}: `, el('code', {text: item.named}),
          item.reference_resolution ? ` (${item.reference_resolution.replace(/_/g, ' ')})` : '',
          item.reason === 'entry_deleted' ? ' (deleted before this scan)' : ' (not in this scan)',
        ]))),
      ]));
    }
    const notDrawn = data.availability.not_drawn;
    const hiddenKinds = Object.entries(notDrawn.published);
    if (hiddenKinds.length || notDrawn.proposed) {
      parts.push(el('p', {class: 'note', 'data-help': 'graph.not_drawn'}, [
        ...hiddenKinds.flatMap(([primitive, count]) => [
          `${count} published "${primitive}" line${count === 1 ? '' : 's'} not drawn: `,
          el('button', {class: 'link', type: 'button', text: `draw ${primitive}`, onclick: () => {
            writeAddress({...address, relations: [...address.relations, primitive]}, {push: true});
            showGraphPage().catch(reportError);
          }}), '. ']),
        notDrawn.proposed ? `${notDrawn.proposed} suggested pair${notDrawn.proposed === 1 ? '' : 's'} not drawn. ` : null,
        notDrawn.proposed && !address.proposals ? el('button', {class: 'link', type: 'button', text: 'show suggestions',
          onclick: () => {
            writeAddress({...address, proposals: true}, {push: true});
            showGraphPage().catch(reportError);
          }}) : null,
      ]));
    }
    const mentions = data.availability.mentions;
    if (mentions) parts.push(el('p', {class: 'faint', 'data-help': 'graph.mentions'}, [mentionWords(mentions)]));
    fill(status, ...parts);
  }

  /** @param {import('./types/api').MentionSummary} mentions */
  function mentionWords(mentions) {
    // Unexamined is not "mentions nothing": only a reader that looks for paths finding none says
    // so.
    if (mentions.state === 'not_read' || mentions.state === 'reader_does_not_look') {
      return `What it mentions is not read yet: ${mentions.not_resolved_because || 'no mention recorded'}.`;
    }
    if (mentions.state === 'none_found') {
      return 'Read for paths; none found.';
    }
    if (!mentions.examined) {
      return `It mentions ${mentions.total} path${mentions.total === 1 ? '' : 's'}; `
        + `${mentions.not_resolved_because || 'they were not resolved again here'}.`;
    }
    const by = mentions.by_outcome;
    const count = (/** @type {string[]} */ names) => names.reduce((sum, name) => sum + (by[name] || 0), 0);
    const resolved = count(['in_folder', 'rooted', 'from_root', 'by_prefix', 'by_name']);
    const said = [`It mentions ${mentions.total} path${mentions.total === 1 ? '' : 's'}: `
      + `${resolved} name one item`];
    if (by.several_matches) said.push(`${by.several_matches} are ambiguous (several match)`);
    if (by.no_match) said.push(`${by.no_match} name nothing in this project`);
    if (by.outside_pool) said.push(`${by.outside_pool} point outside the indexed folders`);
    const other = count(['code_include_not_resolved', 'not_a_path']);
    if (other) said.push(`${other} are code includes or not paths`);
    let text = `${said.join(', ')}.`;
    if (mentions.not_resolved_because) text += ` Note: ${mentions.not_resolved_because}.`;
    return text;
  }

  // ------------------------------------------------------------------ the list

  /**
   * The same content as the drawing, as tables a keyboard and a screen reader reach.
   * @param {HTMLElement} list
   * @param {NeighborhoodResponse} data
   */
  function renderList(list, data) {
    const address = readAddress();
    const names = new Map(data.nodes.map((node) => [node.id, node.name]));
    const lines = data.edges.map((edge) => el('tr', {'data-id': edge.id}, [
      el('td', {text: names.get(edge.source) || edge.source}),
      el('td', {}, [el('button', {class: 'graph-pick', type: 'button', 'data-id': edge.id,
        'aria-label': `${edge.summary} Show its evidence.`,
        onclick: () => select(edge.id, {bring: true})}, [edge.label])]),
      el('td', {text: names.get(edge.target) || edge.target}),
      el('td', {}, [el('span', {class: `graph-kind ${edge.kind}`, text: KIND_WORDS[edge.kind]})]),
    ]));
    const items = data.nodes.map((node) => el('tr', {'data-id': node.id}, [
      el('td', {}, [el('button', {class: 'graph-pick', type: 'button', 'data-id': node.id,
        'aria-label': `${node.name}, ${node.entry_type}${node.is_center ? ', the center' : ''}. Show it.`,
        onclick: () => select(node.id, {bring: true})}, [node.name])]),
      el('td', {text: node.is_center ? 'the center' : node.placement}),
      el('td', {text: plainValue(node.entry_type)}),
      el('td', {class: 'graph-ladder-cell', 'data-ladder': node.ladder || ''}, ladderCell(node, data, address)),
      el('td', {class: 'faint', text: node.path}),
    ]));
    fill(list,
      el('h3', {text: 'Relationships'}),
      lines.length ? el('table', {class: 'graph-table'}, [
        el('caption', {text: `${data.edges.length} line${data.edges.length === 1 ? '' : 's'}, `
          + 'as drawn. Choose one to see its evidence.'}),
        el('thead', {}, [el('tr', {}, ['From', 'Relation', 'To', 'Kind'].map((h) => el('th', {scope: 'col', text: h})))]),
        el('tbody', {}, lines),
      ]) : el('p', {class: 'empty', text: 'No line to list.'}),
      el('h3', {text: 'Items'}),
      el('table', {class: 'graph-table'}, [
        el('caption', {text: `${data.nodes.length} item${data.nodes.length === 1 ? '' : 's'}, as drawn.`}),
        el('thead', {}, [el('tr', {}, ['Name', 'Placed', 'Kind', 'Levels', 'Path'].map((h) => el('th', {scope: 'col', text: h})))]),
        el('tbody', {}, items),
      ]),
    );
    // Up and down move between the list's buttons, as in any list.
    list.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const buttons = Array.from(list.querySelectorAll('button.graph-pick'));
      const here = buttons.indexOf(/** @type {Element} */ (document.activeElement));
      if (here < 0) return;
      event.preventDefault();
      const next = buttons[Math.max(0, Math.min(buttons.length - 1, here + (event.key === 'ArrowDown' ? 1 : -1)))];
      if (next instanceof HTMLElement) next.focus();
    });
  }

  // ------------------------------------------------------------------ the ladders

  const LEVEL_WORDS = {
    root: 'data folder', person: 'area', line_of_work: 'theme', experiment: 'experiment', folder: 'folder',
  };

  /**
   * The ladder's color for a dot or an edge on a card, as the stylesheet names it (unset where
   * Color by ladder is off, so the card keeps a neutral edge).
   * @param {NeighborhoodResponse} data
   * @param {Ladder} ladder
   * @param {GraphAddress} address
   */
  function ladderStyle(data, ladder, address) {
    if (!address.color) return '';
    return `--ladder: var(--ladder-${(Math.max(0, laddersOf(data).indexOf(ladder)) % LADDER_COLORS) + 1})`;
  }

  /**
   * Where a ladder's line of work stands, in words: asked for or not, found or not.
   * @param {Ladder} ladder
   * @param {GraphAddress} address
   */
  function lineWords(ladder, address) {
    if (ladder.line_of_work === 'placed') {
      const rung = ladder.rungs.find((held) => held.level === 'line_of_work');
      return `theme: ${rung ? rung.name : 'placed'}`;
    }
    if (ladder.line_of_work === 'not_placed') return 'theme: none';
    if (ladder.line_of_work === 'not_computed') {
      return address.lines ? 'theme: not computed' : 'theme: not asked for';
    }
    return ladder.line_of_work_reason || 'no folder above it is read as an experiment';
  }

  /**
   * Who stands on a ladder, in words: how many, and the first few by name.
   * @param {Ladder} ladder
   * @param {NeighborhoodResponse} data
   */
  function memberWords(ladder, data) {
    const known = new Map(data.nodes.map((node) => [node.id, node]));
    const members = ladder.members.map((id) => known.get(id)).filter((node) => node);
    const named = members.slice(0, 3).map((node) => (node && node.is_center ? `${node.name} (the center)` : node && node.name));
    const rest = members.length - named.length;
    return `${members.length} member${members.length === 1 ? '' : 's'}: ${named.join(', ')}`
      + `${rest > 0 ? ` and ${rest} more` : ''}`;
  }

  /**
   * The Ladders list at the right: one card per ladder with its color along the left edge.
   * @param {HTMLElement} section
   * @param {NeighborhoodResponse} data
   * @param {GraphAddress} address
   */
  function renderLadders(section, data, address) {
    if (!laddersOf(data).length) {
      fill(section);
      return;
    }
    const cards = laddersOf(data).map((ladder) => {
      const experiment = ladder.rungs.find((rung) => rung.level === 'experiment');
      const experimentId = experiment ? experiment.id : null;
      const between = ladder.rungs.filter((rung) => rung.level === 'person');
      return el('article', {class: 'card graph-ladder', 'data-ladder': ladder.id,
        style: ladderStyle(data, ladder, address)}, [
        el('h4', {}, [ladderName(ladder),
          ladder.is_center_ladder ? el('span', {class: 'faint', text: ' \u00b7 the center\u2019s'}) : null]),
        el('p', {class: 'faint graph-rungs', title: ladder.line_of_work_reason || ''}, [
          `${ladder.rungs.map((rung) => LEVEL_WORDS[rung.level]).join(' \u203a ')} \u00b7 ${lineWords(ladder, address)}`,
          between.length ? ` \u00b7 area: ${between.map((rung) => rung.name).join(', ')}` : '']),
        experiment && experiment.path ? el('p', {class: 'mono graph-ladder-path', text: experiment.path}) : null,
        el('p', {class: 'graph-members', text: memberWords(ladder, data)}),
        experimentId && window.topicKit ? topicKit.chipsFor([experimentId], {label: 'in your collections '}) : null,
        experimentId ? el('button', {class: 'link', type: 'button', 'data-graph-years': experimentId,
          onclick: () => openInYears(experimentId)}, ['Open in By year'])
          : el('p', {class: 'faint', text: 'No experiment to open: no folder above it is read as one.'}),
      ]);
    });
    fill(section,
      el('p', {class: 'eyebrow', text: `Folder levels in these connections \u00b7 ${laddersOf(data).length} of ${data.ladders_total}`}),
      ...cards,
      data.ladders_left_out ? el('p', {class: 'faint graph-ladders-more',
        text: `and ${data.ladders_left_out} more chain${data.ladders_left_out === 1 ? '' : 's'} of levels, not drawn`}) : null);
  }

  /**
   * A ladder in the Items table: its color as a dot, its name beside it.
   * @param {GraphNode} node
   * @param {NeighborhoodResponse} data
   * @param {GraphAddress} address
   */
  function ladderCell(node, data, address) {
    const ladder = node.ladder ? laddersOf(data).find((held) => held.id === node.ladder) : null;
    if (!ladder) {
      return [el('span', {class: 'faint',
        text: data.ladders_left_out ? 'on levels not drawn' : 'none'})];
    }
    return [el('span', {class: 'ladder-dot', 'aria-hidden': 'true', style: ladderStyle(data, ladder, address)}),
      ladderName(ladder)];
  }

  /**
   * Show a ladder's experiment in the Years view, where Years can open it.
   * @param {string} experimentId
   */
  function openInYears(experimentId) {
    showView('years');
    const years = /** @type {any} */ (window).ingenceYears;
    if (!years || typeof years.load !== 'function' || typeof years.open !== 'function') return;
    Promise.resolve(years.load()).then(() => window.setTimeout(() => {
      try {
        years.open({kind: 'exp', id: experimentId});
      } catch (error) {
        /* Years keeps its own place: nothing more is promised than the page opening. */
      }
    }, 0)).catch(() => {});
  }

  // ------------------------------------------------------------------ a file no line touches

  /** @type {Map<string, string>} */
  const runNumbers = new Map();

  /**
   * "Run 3" for a run's identifier, as History numbers the runs; empty where it is not found.
   * @param {string | null} runId
   */
  async function runNumber(runId) {
    if (!runId) return '';
    if (!runNumbers.has(runId)) {
      try {
        const found = await v1('/api/timeline');
        for (const run of found.runs || []) {
          // A run is labelled "Scan 5" ("Run 5" from engines before 0.4.0).
          const said = /^(?:Scan|Run)\s+(\d+)/.exec(String(run.label || ''));
          if (said) runNumbers.set(String(run.run_id), said[1]);
        }
      } catch (error) {
        /* The sentence says "this scan" instead. */
      }
    }
    return runNumbers.get(runId) || '';
  }

  /**
   * A file nothing joins: the center is a file, no line of the kinds that join files is drawn
   * (a folder's containing it is not one), none is hidden by a filter, and no mention of it names
   * nothing.
   * @param {NeighborhoodResponse} data
   */
  function isLone(data) {
    const center = data.nodes.find((node) => node.is_center);
    if (!center || center.entry_type === 'directory') return false;
    const drawnLine = data.edges.some((edge) => edge.kind !== 'indexed_containment');
    const hidden = Object.values(data.availability.not_drawn.published).reduce((sum, count) => sum + count, 0);
    return !drawnLine && !hidden && !data.availability.unresolved.count;
  }

  /**
   * The same answer with the center alone: its folder and the line that contains it are left out
   * of what is drawn and listed, and a ladder holding nothing else is not listed.
   * @param {NeighborhoodResponse} data
   * @returns {NeighborhoodResponse}
   */
  function centerAlone(data) {
    const center = data.nodes.find((node) => node.is_center);
    if (!center) return data;
    const ladders = laddersOf(data)
      .map((ladder) => ({...ladder, members: ladder.members.filter((id) => id === center.id)}))
      .filter((ladder) => ladder.members.length);
    const left = laddersOf(data).length - ladders.length;
    return {...data, nodes: [center], edges: [], ladders,
      ladders_total: Math.max(0, (data.ladders_total || 0) - left)};
  }

  /**
   * Say so where the file is alone: the sentence, that a line not drawn is not evidence of
   * absence, and the way to look further (the proposed pairs).
   * @param {HTMLElement} pane
   * @param {NeighborhoodResponse} data
   * @param {GraphAddress} address
   * @param {number} turn the draw this answer belongs to
   * @param {boolean} lone
   */
  async function renderLone(pane, data, address, turn, lone) {
    fill(pane);
    // The pane says when it has decided (data-lone), so a reader that waits on the page's state
    // knows a missing sentence is a decision and not a read still under way.
    if (!lone) {
      pane.dataset.lone = 'no';
      return;
    }
    const number = await runNumber(data.run_id);
    // A newer draw was made meanwhile: this answer is stale.
    if (turn !== drawingRequest) return;
    const where = number ? `Scan ${number}` : 'this scan';
    fill(pane, states.say(null, {
      eyebrow: 'Connections \u00b7 no lines', dot: 'empty', into: true,
      sentence: `No line touches this file in ${where}. A line that is not drawn does not prove there is none.`,
      actions: address.proposals ? [] : [{label: 'Show suggested pairs', onclick: () => {
        writeAddress({...readAddress(), proposals: true}, {push: true});
        showGraphPage().catch(reportError);
      }}],
    }));
    pane.dataset.lone = 'yes';
  }

  // ------------------------------------------------------------------ the keyboard

  /** The directions the arrow keys follow a line in. */
  const ARROWS = {ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1]};
  /** Whether the plot had the keyboard when a redraw began, so it has it again after. */
  let plotHadFocus = false;

  /**
   * The plot is one stop. Inside it: Tab and Shift Tab walk the items (the center first, then as
   * listed) and leave past the last or before the first; the arrow keys follow a line from the
   * item chosen; Enter centers the graph on it; F fits the drawing.
   * @param {HTMLElement} plot
   */
  function wirePlot(plot) {
    plot.addEventListener('keydown', (event) => {
      if (!shown || event.altKey || event.ctrlKey || event.metaKey) return;
      const order = [...shown.nodes].sort((a, b) => Number(b.is_center) - Number(a.is_center));
      const current = order.find((node) => node.id === readAddress().selected) || order[0];
      if (!current) return;
      if (event.key === 'Tab') {
        const next = order.indexOf(current) + (event.shiftKey ? -1 : 1);
        if (next < 0 || next >= order.length) return;
        event.preventDefault();
        select(order[next].id, {bring: true});
      } else if (event.key in ARROWS) {
        event.preventDefault();
        const [dx, dy] = ARROWS[/** @type {keyof typeof ARROWS} */ (event.key)];
        const at = drawing ? drawing.getElementById(current.id).position() : {x: 0, y: 0};
        const joined = shown.edges.flatMap((edge) => (edge.source === current.id ? [edge.target]
          : edge.target === current.id ? [edge.source] : []));
        const ahead = joined.map((id) => ({id, at: drawing ? drawing.getElementById(id).position() : {x: 0, y: 0}}))
          .filter((other) => (other.at.x - at.x) * dx + (other.at.y - at.y) * dy > 0)
          .sort((a, b) => Math.hypot(a.at.x - at.x, a.at.y - at.y) - Math.hypot(b.at.x - at.x, b.at.y - at.y));
        if (ahead.length) select(ahead[0].id, {bring: true});
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (!current.is_center) {
          plotHadFocus = true;
          centerOn(current.id);
        }
      } else if (event.key === 'f' || event.key === 'F') {
        event.preventDefault();
        fitLegibly();
      }
    });
  }

  // ------------------------------------------------------------------ the inspector

  /**
   * @param {string} id
   * @param {{bring?: boolean}} [how] bring it into view on the drawing (a choice from the list)
   */
  function select(id, how = {}) {
    writeAddress({...readAddress(), selected: id}, {push: false});
    mark(id, how);
    inspect(id).catch(reportError);
  }

  /** @param {string} id */
  async function inspect(id) {
    // Only the latest choice may fill the inspector: an answer that arrives after a newer
    // choice is dropped, so a slow read never shows beside another item.
    const mine = ++inspecting;
    const inspector = /** @type {HTMLElement | null} */ (document.querySelector('[data-graph="inspector"]'));
    if (!inspector || !shown) return;
    mark(id);
    for (const row of document.querySelectorAll('.graph-table tr[data-id]')) {
      row.classList.toggle('picked', row.getAttribute('data-id') === id);
    }
    const node = shown.nodes.find((item) => item.id === id);
    if (node) {
      const facts = nodeDetail(node);
      fill(inspector, ...facts, el('p', {class: 'empty', text: 'reading what the rules said about it...'}));
      try {
        /** @type {EntryResponse} */
        const said = await v1(`/api/v1/entry?${new URLSearchParams({id: node.id, run: shown.run_id || ''})}`);
        if (mine !== inspecting) return;
        // Where it came from, in the inspector's words.
        const moved = said.moved
          ? el('dl', {class: 'graph-facts'}, [el('dt', {text: 'Moved here'}), el('dd', {text: said.moved.said})])
          : null;
        fill(inspector, ...facts, moved, ...claimsOf(said));
      } catch (error) {
        if (mine !== inspecting) return;
        fill(inspector, ...facts, el('p', {class: 'note', text: String(error instanceof Error ? error.message : error)}));
      }
      return;
    }
    const edge = shown.edges.find((item) => item.id === id);
    if (!edge) return;
    fill(inspector, el('p', {class: 'empty', text: 'reading its evidence...'}));
    try {
      /** @type {EdgeDetail} */
      const detail = await v1(`/api/v1/graph/edge?${new URLSearchParams({id, run: shown.run_id || ''})}`);
      if (mine !== inspecting) return;
      fill(inspector, ...edgeDetail(detail));
    } catch (error) {
      if (mine !== inspecting) return;
      fill(inspector, el('p', {class: 'note', text: String(error instanceof Error ? error.message : error)}));
    }
  }

  /** @param {GraphNode} node */
  function nodeDetail(node) {
    const ladder = shown && node.ladder ? laddersOf(shown).find((held) => held.id === node.ladder) : null;
    return [
      el('h3', {text: node.name}),
      el('p', {class: 'faint', text: node.path}),
      el('dl', {class: 'graph-facts'}, [
        el('dt', {text: 'Kind'}), el('dd', {text: `${plainValue(node.entry_type)}${node.entry_kind === 'derived' ? ', from inside a file' : ''}`}),
        el('dt', {text: 'Placed'}), el('dd', {text: node.is_center ? 'the center' : node.placement}),
        el('dt', {text: 'Levels'}), el('dd', {text: ladder ? ladderName(ladder) : 'not drawn in any levels'}),
        el('dt', {text: 'Last scan'}), el('dd', {title: node.scan_outcome, text: plainValue(node.scan_outcome)}),
        el('dt', {text: 'Its questions'}), el('dd', {title: node.resolution_status, text: plainValue(node.resolution_status)}),
        el('dt', {text: 'Identifier'}), el('dd', {}, [el('code', {text: node.id})]),
      ]),
      node.is_center ? el('p', {class: 'faint', text: 'This is the center.'})
        : el('button', {class: 'answer quiet', type: 'button', 'data-help': 'graph.recentre',
          onclick: () => centerOn(node.id)}, [el('strong', {text: 'Center the graph here'})]),
    ];
  }

  /**
   * Redraw the graph around another item (a step in the history).
   * @param {string} id
   */
  function centerOn(id) {
    writeAddress({...readAddress(), center: id, selected: ''}, {push: true});
    showGraphPage().catch(reportError);
  }

  /**
   * What the rules concluded about an item, and where they could not decide.
   * @param {EntryResponse} said
   */
  function claimsOf(said) {
    const blocks = [el('h4', {text: 'What the rules said about it'})];
    if (!said.claims.length) blocks.push(el('p', {class: 'empty', text: 'No rule found anything about it.'}));
    else {
      // A rule is shown as the evidence inspector shows it: what was concluded first, then the rule
      // and its version, the tier and the standing.
      blocks.push(el('ul', {class: 'ids'}, said.claims.map((claim) => el('li', {}, [
        `${plainValue(String(claim.predicate).split('.').pop())}: `,
        el('strong', {text: typeof claim.object_reference === 'string' ? claim.object_reference
          : JSON.stringify(claim.object_reference)}),
        el('span', {class: 'faint'}, [' by ', el('code', {text: claim.rule_id}),
          ` version ${claim.rule_version} (${claim.inference_tier}, ${plainValue(claim.resolution_status)})`]),
      ]))));
    }
    if (said.failures.length) {
      blocks.push(el('p', {class: 'faint', text: `${said.failures.length} rule${said.failures.length === 1 ? '' : 's'} `
        + 'could not decide about it: ' + said.failures.map((failure) => failure.rule_id).join(', ') + '.'}));
    }
    return blocks;
  }

  /** @param {EdgeDetail} detail */
  function edgeDetail(detail) {
    const direction = detail.symmetric ? 'Either way round: the relation is symmetric.'
      : detail.directed ? `From ${detail.source.name || detail.source.id} to `
        + `${detail.target ? detail.target.name || detail.target.id : 'nothing recorded'}.`
        : '';
    const blocks = [
      el('h3', {text: `${detail.primitive}${detail.specialization ? ` (${detail.specialization.replace(/_/g, ' ')})` : ''}`}),
      el('p', {class: `graph-kind ${detail.kind}`, text: KIND_WORDS[detail.kind]}),
      el('p', {text: detail.reading}),
      el('p', {class: 'faint', text: direction}),
      el('dl', {class: 'graph-facts'}, [
        el('dt', {text: 'Scan'}), el('dd', {}, [el('code', {text: detail.run_id})]),
        el('dt', {text: 'Rule'}), el('dd', {}, detail.rule_id
          ? [el('code', {text: detail.rule_id}), ` version ${detail.rule_version}`] : ['none recorded']),
        el('dt', {text: 'Level'}), el('dd', {text: detail.tier
          ? `${detail.tier}${detail.tier_definition ? `: ${detail.tier_definition}` : ''}` : 'none'}),
        el('dt', {text: 'Standing'}), el('dd', {text: detail.status}),
        detail.level ? el('dt', {text: 'Level'}) : null,
        detail.level ? el('dd', {text: `${detail.level}${detail.quotient_origin ? `, ${detail.quotient_origin}` : ''}`}) : null,
        detail.expansion_state ? el('dt', {text: 'Expansion'}) : null,
        detail.expansion_state ? el('dd', {text: detail.expansion_state}) : null,
        detail.reference_resolution ? el('dt', {text: 'Reference'}) : null,
        detail.reference_resolution ? el('dd', {text: detail.reference_resolution.replace(/_/g, ' ')}) : null,
        detail.candidate_id && detail.kind === 'published_assertion' ? el('dt', {text: 'Promoted from'}) : null,
        detail.candidate_id && detail.kind === 'published_assertion' ? el('dd', {}, [el('code', {text: detail.candidate_id})]) : null,
      ]),
      detail.rule_statement ? el('details', {}, [el('summary', {text: 'the rule, as the scan saved it'}),
        el('p', {class: 'faint', text: detail.rule_statement})]) : null,
      el('p', {class: 'faint', text: detail.definition}),
    ];
    if (detail.support.length) {
      blocks.push(el('h4', {text: 'Support'}), el('ul', {class: 'ids'}, detail.support.map((item) =>
        el('li', {}, [`${item.name.replace(/_/g, ' ')} (${item.family.replace(/_/g, ' ')})`,
          item.observations.length ? el('span', {class: 'faint', text: ` from ${item.observations.length} record${item.observations.length === 1 ? '' : 's'}`}) : null]))));
    }
    if (detail.defeaters.length) {
      blocks.push(el('h4', {text: 'What would remove it'}), el('ul', {class: 'ids'}, detail.defeaters.map((item) =>
        el('li', {}, [`${item.reason}${item.fired ? ' (present)' : ' (not present)'} `, el('code', {text: item.rule_id})]))));
    }
    if (detail.records.length) {
      blocks.push(el('details', {'data-help': 'graph.records'}, [
        el('summary', {text: `the ${detail.records.length} record${detail.records.length === 1 ? '' : 's'} it rests on`}),
        el('ul', {class: 'ids'}, detail.records.map((record) => el('li', {}, [el('code', {text: record})]))),
      ]));
    }
    if (detail.review) {
      const review = detail.review;
      blocks.push(el('p', {class: 'faint', text: review.message}), el('button', {
        class: 'answer', type: 'button', 'data-help': 'graph.review',
        onclick: () => {
          // The existing Suggested pairs section, filtered to this pair's rule and folder.
          if (typeof sheetState === 'object' && sheetState) {
            sheetState.rule = review.rule;
            sheetState.top = review.top;
          }
          showView(review.view);
        }}, [el('strong', {text: 'Answer it on Suggested pairs'})]));
    }
    return blocks;
  }

  // ------------------------------------------------------------------ entry points

  /**
   * Open the Graph page centered on one entry (View in Graph).
   * @param {string} entryId
   */
  function openInGraph(entryId) {
    const address = readAddress();
    writeAddress({...address, center: entryId, selected: ''}, {push: true});
    if (location.hash === '#graph' && document.querySelector('[data-graph="canvas"]')) {
      showGraphPage().catch(reportError);
    } else {
      showView('graph');
    }
  }

  /**
   * A "View in Graph" button for Experiments, Ask results and the file inspector.
   * @param {string} entryId
   * @param {string} [label]
   */
  function graphButton(entryId, label) {
    return el('button', {class: 'link graph-open', type: 'button', 'data-help': 'graph.view_in_graph',
      'data-graph-center': entryId, onclick: (event) => { event.stopPropagation(); openInGraph(entryId); }},
    [label || 'Show connections']);
  }

  // Back and forward move between centers and filters; the hash change is app.js's.
  window.addEventListener('popstate', () => {
    if (location.hash === '#graph' && document.querySelector('[data-graph]')) {
      showGraphPage().catch(reportError);
    }
  });
  // Any element carrying data-graph-center opens the graph there: the smallest hook another
  // page's markup needs.
  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target.closest('[data-graph-center]') : null;
    if (!target || target.classList.contains('graph-open')) return;
    event.preventDefault();
    openInGraph(String(target.getAttribute('data-graph-center')));
  });
  // The drawing follows the page's size, and is fitted to it again.
  /** @type {number | undefined} */
  let resizing;
  window.addEventListener('resize', () => {
    window.clearTimeout(resizing);
    resizing = window.setTimeout(() => {
      if (!drawing) return;
      drawing.resize();
      fitLegibly();
    }, 150);
  });
  // A change of theme redraws with the new colors.
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (shown && location.hash === '#graph') showGraphPage().catch(reportError);
    });
  }

  window.showGraphPage = showGraphPage;
  window.openInGraph = openInGraph;
  window.graphButton = graphButton;
})();
