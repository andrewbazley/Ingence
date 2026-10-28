// @ts-check
/* Overlap as a ring with chords.
 *
 * At many lines of work a matrix of every line against every other is mostly empty cells: 39 lines
 * make over a thousand cells, most of them empty. The ring draws one arc per line. The arc's length
 * is the experiments in the line, and its color comes from a palette of six categorical colors. A
 * line whose boundary is uncertain (stability under the threshold) is drawn dotted: white inside,
 * with its color dotted at the edge. The ring draws one chord for each pair of lines that share a
 * word. The chord's width is the words shared over the words of the smaller line. The table under
 * the ring repeats every pair, so no arc, chord or label is ever only a picture or a tooltip.
 *
 * The geometry is a pure function of the lines and pairs the engine served (``/api/v1/themes``,
 * ``/api/v1/themes/overlap``, ``/api/v1/themes/stability``). The page counts nothing here but what
 * it draws. This file stands alone: it reads only ``el`` (app.js). */

(() => {
  /**
   * @typedef {{id: number | string, label: string, experiments: number, stability: number | null}} RingLine
   * @typedef {{a: number | string, b: number | string, words: string[], shared: number, of_smaller: number}} RingPair
   * @typedef {{index: number, id: number | string, start: number, end: number, mid: number, path: string, dotted: boolean}} RingArc
   * @typedef {{a: number, b: number, d: string, width: number, pair: RingPair}} RingChord
   */

  /** Stability under which a line's boundary is called uncertain (the same 0.80 the axis marks). */
  const THRESHOLD = 0.8;
  /** Lines from which the ring is the default; at fewer the matrix is. */
  const RING_FROM = 13;

  /**
   * The arcs and chords of the ring. Arcs follow the lines' order, clockwise from the top; a chord
   * leaves the middle of each of its arcs and bends through the center.
   * @param {RingLine[]} lines
   * @param {RingPair[]} pairs
   * @param {{radius?: number, width?: number, gap?: number}} [options]
   * @returns {{arcs: RingArc[], chords: RingChord[], radius: number, inner: number}}
   */
  function ringGeometry(lines, pairs, options = {}) {
    const radius = options.radius ?? 190;
    const band = options.width ?? 22;
    const gap = options.gap ?? 0.012;
    const total = lines.reduce((n, line) => n + Math.max(0, line.experiments), 0) || 1;
    const inner = radius - band;
    const point = (/** @type {number} */ angle, /** @type {number} */ r) => [Math.cos(angle) * r, Math.sin(angle) * r];
    const fixed = (/** @type {number} */ x) => x.toFixed(2);
    let angle = -Math.PI / 2;
    /** @type {RingArc[]} */
    const arcs = lines.map((line, index) => {
      const sweep = (Math.max(0, line.experiments) / total) * Math.PI * 2;
      const start = angle;
      const end = Math.max(start + 0.002, angle + sweep - gap);
      angle += sweep;
      const [x0, y0] = point(start, radius);
      const [x1, y1] = point(end, radius);
      const [x2, y2] = point(end, inner);
      const [x3, y3] = point(start, inner);
      const large = end - start > Math.PI ? 1 : 0;
      const path = `M ${fixed(x0)} ${fixed(y0)} A ${radius} ${radius} 0 ${large} 1 ${fixed(x1)} ${fixed(y1)} `
        + `L ${fixed(x2)} ${fixed(y2)} A ${inner} ${inner} 0 ${large} 0 ${fixed(x3)} ${fixed(y3)} Z`;
      const stability = line.stability;
      return {index, id: line.id, start, end, mid: (start + end) / 2, path,
        dotted: stability != null && stability < THRESHOLD};
    });
    const slot = new Map(lines.map((line, index) => [String(line.id), index]));
    /** @type {RingChord[]} */
    const chords = [];
    for (const pair of pairs) {
      const a = slot.get(String(pair.a));
      const b = slot.get(String(pair.b));
      if (a === undefined || b === undefined || a === b) continue;
      const [x0, y0] = point(arcs[a].mid, inner - 2);
      const [x1, y1] = point(arcs[b].mid, inner - 2);
      const share = pair.of_smaller > 0 ? Math.min(1, pair.shared / pair.of_smaller) : 0;
      chords.push({a, b, d: `M ${fixed(x0)} ${fixed(y0)} Q 0 0 ${fixed(x1)} ${fixed(y1)}`,
        width: Math.round((1 + 5 * share) * 10) / 10, pair});
    }
    return {arcs, chords, radius, inner};
  }

  const capital = (/** @type {string} */ text) => text.charAt(0).toUpperCase() + text.slice(1);

  /** The SVG namespace, taken from the browser rather than written as an address (the page names no scheme). */
  function svgNamespace() {
    const holder = document.createElement('template');
    holder.innerHTML = '<svg></svg>';
    const first = holder.content.firstElementChild;
    return first ? first.namespaceURI : null;
  }

  /**
   * The ring and the table of its pairs.
   * @param {RingLine[]} lines
   * @param {RingPair[]} pairs
   * @param {{lineWord: string, linesWord: string, experimentWord: string, experimentsWord: string,
   *   onSelect?: (id: number | string) => void, threshold?: number}} words
   * @returns {HTMLElement}
   */
  function ringDrawing(lines, pairs, words) {
    const ns = svgNamespace();
    const geometry = ringGeometry(lines, pairs);
    const half = geometry.radius + 10;
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `${-half} ${-half} ${2 * half} ${2 * half}`);
    svg.setAttribute('class', 'yring-svg');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `${lines.length} ${words.linesWord}, one arc each, and ${geometry.chords.length} `
      + `${geometry.chords.length === 1 ? 'chord' : 'chords'} between pairs that share a word`);
    const chordNodes = geometry.chords.map((chord) => {
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', chord.d);
      path.setAttribute('class', 'chord');
      path.setAttribute('stroke-width', String(chord.width));
      path.setAttribute('data-a', String(lines[chord.a].id));
      path.setAttribute('data-b', String(lines[chord.b].id));
      const title = document.createElementNS(ns, 'title');
      title.textContent = `${lines[chord.a].label} and ${lines[chord.b].label}: ${chord.pair.shared} of ${chord.pair.of_smaller} words in common`;
      path.append(title);
      svg.append(path);
      return path;
    });
    const arcNodes = geometry.arcs.map((arc, index) => {
      const line = lines[index];
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', arc.path);
      path.setAttribute('class', `arc c${index % 6}${arc.dotted ? ' dotted' : ''}`);
      path.setAttribute('data-line', String(line.id));
      path.setAttribute('data-stability', line.stability == null ? '' : String(line.stability));
      const title = document.createElementNS(ns, 'title');
      title.textContent = `${line.label} · ${line.experiments} ${line.experiments === 1 ? words.experimentWord : words.experimentsWord}`
        + (arc.dotted ? ' · boundary uncertain' : '');
      path.append(title);
      path.addEventListener('mouseenter', () => hot(arc.index, true));
      path.addEventListener('mouseleave', () => hot(arc.index, false));
      path.addEventListener('click', () => { if (words.onSelect) words.onSelect(line.id); });
      svg.append(path);
      return path;
    });
    /** Draw an arc's chords darker while it is pointed at. */
    function hot(/** @type {number} */ index, /** @type {boolean} */ on) {
      const id = String(lines[index].id);
      for (const chord of chordNodes) {
        if (chord.getAttribute('data-a') === id || chord.getAttribute('data-b') === id) chord.classList.toggle('hot', on);
      }
      arcNodes[index].classList.toggle('hot', on);
    }
    const centerLines = [
      [`${lines.length} ${lines.length === 1 ? words.lineWord : words.linesWord}`, 'main'],
      [`${geometry.chords.length} ${geometry.chords.length === 1 ? 'pair shares' : 'pairs share'} a word`, 'sub'],
    ];
    centerLines.forEach(([text, kind], row) => {
      const label = document.createElementNS(ns, 'text');
      label.setAttribute('x', '0');
      label.setAttribute('y', String(row * 16 - 4));
      label.setAttribute('class', `center ${kind}`);
      label.textContent = text;
      svg.append(label);
    });

    const nameOf = (/** @type {number | string} */ id) => lines.find((line) => String(line.id) === String(id));
    const button = (/** @type {number | string} */ id) => {
      const line = nameOf(id);
      return el('button', {type: 'button', class: 'link', title: line ? line.label : '',
        onclick: () => { if (words.onSelect) words.onSelect(id); }}, [line ? line.label : String(id)]);
    };
    const rows = geometry.chords.map((chord) => el('tr', {'data-pair': `${lines[chord.a].id}|${lines[chord.b].id}`}, [
      el('td', {}, [button(lines[chord.a].id)]),
      el('td', {}, [button(lines[chord.b].id)]),
      el('td', {class: 'words', text: chord.pair.words.join(', ')}),
      el('td', {class: 'n', text: `${chord.pair.shared} of ${chord.pair.of_smaller}`}),
    ]));
    const table = el('table', {class: 'yring-pairs'}, [
      el('thead', {}, [el('tr', {}, [capital(words.lineWord), capital(words.lineWord), 'Words they share', 'Shared of the smaller']
        .map((head) => el('th', {scope: 'col', text: head})))]),
      el('tbody', {}, rows),
    ]);
    return el('div', {class: 'yring', 'data-lines': String(lines.length), 'data-chords': String(geometry.chords.length)}, [
      el('div', {class: 'yring-box'}, [svg]),
      el('p', {class: 'ynote', text: `Arc length: the ${words.experimentsWord} in the ${words.lineWord}. Chord width: the words two ${words.linesWord} share, over the words of the smaller one. A dotted arc: its boundary is uncertain (stability under ${(words.threshold ?? THRESHOLD).toFixed(2)}). Point at an arc to see its name; the table says the same, and so does the list of ${words.linesWord}.`}),
      el('h3', {text: `Every pair that shares a word · ${geometry.chords.length}`}),
      geometry.chords.length ? table : el('p', {class: 'nothing', text: `No two ${words.linesWord} share a word.`}),
    ]);
  }

  /** @type {any} */ (window).ingenceRing = {ringGeometry, ringDrawing, RING_FROM, THRESHOLD};
})();
