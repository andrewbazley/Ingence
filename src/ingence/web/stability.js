// @ts-check
/* How well a grouping holds, on its 0-1 axis. One drawing for every place the Dashboard shows a
 * stability: the project card's "seeds agree", a line of work's card, the map's card and each
 * Overlap row. A 6 px track runs from chance (0) to identical (1). A tick marks the threshold below
 * which a boundary is uncertain. A dot marks the value: filled at or above the threshold, white
 * with a dotted border below it. The number is printed beside the picture, so the picture is never
 * the only place the value appears. A value the engine did not serve draws no dot and says so. */

/**
 * @param {number | null | undefined} value the stability, 0 to 1, as the engine served it
 * @param {{label?: string, threshold?: number, ends?: boolean, title?: string}} [options]
 * @returns {HTMLElement}
 */
function stabilityAxis(value, options = {}) {
  const threshold = options.threshold ?? 0.8;
  const known = typeof value === 'number' && Number.isFinite(value);
  const clamped = known ? Math.min(1, Math.max(0, /** @type {number} */ (value))) : 0;
  const below = known && clamped < threshold;
  const track = el('span', {class: 'stability-track', role: 'img',
    'aria-label': known
      ? `${options.label || 'Stability'} ${clamped.toFixed(2)} on a scale from 0 (chance) to 1 (identical); `
        + `below ${threshold.toFixed(2)} the boundary is uncertain`
      : `${options.label || 'Stability'}: not served`}, [
    el('span', {class: 'stability-tick', style: `left:${(threshold * 100).toFixed(1)}%`,
      title: `${threshold.toFixed(2)}: boundary uncertain below`}),
    known ? el('span', {class: below ? 'stability-dot below' : 'stability-dot',
      style: `left:${(clamped * 100).toFixed(1)}%`}) : null,
  ]);
  const parts = [
    el('span', {class: 'stability-label', text: options.label || 'Stability'}),
    track,
    el('span', {class: 'stability-value', text: known ? clamped.toFixed(2) : 'not served'}),
  ];
  if (options.ends !== false) {
    parts.push(el('span', {class: 'stability-ends'}, [
      el('span', {text: '0 · chance'}),
      el('span', {text: `${threshold.toFixed(2)} · boundary uncertain below`}),
      el('span', {text: '1 · identical'}),
    ]));
  }
  const node = el('div', {class: below ? 'stability below-threshold' : 'stability'}, parts);
  if (options.title) node.title = options.title;
  return node;
}
