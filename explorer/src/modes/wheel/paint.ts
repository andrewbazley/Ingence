// The Wheel's Canvas painter: thousands of marks in one pass, no DOM per mark. Crisp labels,
// hover, selection and the keyboard's focus are drawn over it in SVG by the view.
//
// Four layers, inside out:
//   1. sectors: a neutral track, and the chosen measure's categories stacked outward from the
//      inner edge in proportion to their counts, so a wedge reads as a gauge of what the run holds;
//   2. units: a folder a rule reads as an experiment's folder is outlined (solid when supported,
//      doubled when a person confirmed it, red and dashed when conflicting, gray and dashed when
//      rejected); a sector holding units beneath carries a rail on its inner edge, dotted where
//      only proposals lie beneath;
//   3. the kind band: every perimeter bin in its files' main format family;
//   4. the skyline: every bin a tick whose length is its size in bytes (log scale) and whose
//      color is the chosen measure's main category, fainter where the bin is mixed.

import type {WheelResponse} from '../../api';
import {paintFor, type Paint} from '../../encoding';
import type {WheelLayout} from './geometry';

export interface Palette {
  read(token: string): string;
  pattern(texture: 'hatch' | 'dots', token: string): CanvasPattern | string;
}

export function makePalette(root: HTMLElement, context: CanvasRenderingContext2D, ratio: number): Palette {
  const style = getComputedStyle(root);
  const colours = new Map<string, string>();
  const patterns = new Map<string, CanvasPattern | string>();
  const read = (token: string) => {
    let value = colours.get(token);
    if (value === undefined) {
      value = style.getPropertyValue(token).trim() || '#888888';
      colours.set(token, value);
    }
    return value;
  };
  const pattern = (texture: 'hatch' | 'dots', token: string) => {
    const key = `${texture}:${token}`;
    const held = patterns.get(key);
    if (held !== undefined) return held;
    const size = Math.round(6 * ratio);
    const tile = document.createElement('canvas');
    tile.width = size;
    tile.height = size;
    const ink = tile.getContext('2d');
    let made: CanvasPattern | string = read(token);
    if (ink) {
      ink.fillStyle = read('--ix-track');
      ink.fillRect(0, 0, size, size);
      ink.fillStyle = read(token);
      ink.strokeStyle = read(token);
      if (texture === 'hatch') {
        ink.lineWidth = 1.4 * ratio;
        ink.beginPath();
        ink.moveTo(-1, size + 1);
        ink.lineTo(size + 1, -1);
        ink.stroke();
        ink.beginPath();
        ink.moveTo(-1, 1);
        ink.lineTo(1, -1);
        ink.moveTo(size - 1, size + 1);
        ink.lineTo(size + 1, size - 1);
        ink.stroke();
      } else {
        ink.beginPath();
        ink.arc(size / 2, size / 2, 1.1 * ratio, 0, Math.PI * 2);
        ink.fill();
      }
      made = context.createPattern(tile, 'repeat') ?? made;
    }
    patterns.set(key, made);
    return made;
  };
  return {read, pattern};
}

function fillOf(palette: Palette, paint: Paint): CanvasPattern | string {
  if (paint.texture === 'hatch' || paint.texture === 'dots') return palette.pattern(paint.texture, paint.token);
  return palette.read(paint.token);
}

function wedge(context: CanvasRenderingContext2D, layout: WheelLayout, r0: number, r1: number, a0: number, a1: number): void {
  const start = a0 * 2 * Math.PI - Math.PI / 2;
  const end = a1 * 2 * Math.PI - Math.PI / 2;
  context.beginPath();
  context.arc(layout.cx, layout.cy, r1, start, end, false);
  if (r0 > 0) context.arc(layout.cx, layout.cy, r0, end, start, true);
  else context.lineTo(layout.cx, layout.cy);
  context.closePath();
}

export interface PaintOptions {
  measure: string;
  /** 0 to 1: how far the entrance has run; 1 when motion is reduced. */
  progress: number;
}

export function paintWheel(
  context: CanvasRenderingContext2D,
  layout: WheelLayout,
  data: WheelResponse,
  palette: Palette,
  options: PaintOptions,
): void {
  const {measure} = options;
  const definition = data.measures.find((item) => item.key === measure) ?? data.measures[0];
  const paints = definition.categories.map((category) => paintFor(definition.key, category.key, category.level));
  const kindDefinition = data.measures.find((item) => item.key === 'kind');
  const kindPaints = kindDefinition ? kindDefinition.categories.map((category) => paintFor('kind', category.key, category.level)) : [];
  const surface = palette.read('--ix-panel');
  const track = palette.read('--ix-track');
  const ink = palette.read('--ix-ink');
  const ink2 = palette.read('--ix-ink-2');
  const ink3 = palette.read('--ix-ink-3');
  const problem = palette.read('--ix-problem');
  const proposal = palette.read('--ix-proposal');
  const grow = options.progress;

  context.save();
  context.clearRect(0, 0, context.canvas.width, context.canvas.height);

  // 1. Sectors, each a gauge of the measure.
  data.sectors.forEach((sector, index) => {
    const shape = layout.sectors[index];
    if (index === 0 && shape.r0 <= 0) return;
    if (shape.a1 - shape.a0 <= 0) return;
    const a0 = shape.a0 * grow;
    const a1 = shape.a1 * grow;
    const r0 = shape.r0 + 0.75;
    const r1 = shape.r1 - 0.75;
    wedge(context, layout, r0, r1, a0, a1);
    context.fillStyle = track;
    context.fill();
    const counts = sector.counts[definition.key] ?? [];
    const total = sector.files;
    if (total > 0) {
      let inner = r0;
      counts.forEach((value, place) => {
        if (!value) return;
        const paint = paints[place];
        const height = ((r1 - r0) * value) / total;
        if (paint.texture !== 'track') {
          wedge(context, layout, inner, inner + height, a0, a1);
          context.fillStyle = fillOf(palette, paint);
          context.fill();
        }
        inner += height;
      });
    }
    // The gap between neighbors, drawn in the surface's own color.
    wedge(context, layout, r0, r1, a0, a1);
    context.lineWidth = 1;
    context.strokeStyle = surface;
    context.stroke();
    if (sector.kind === 'more') {
      context.setLineDash([2, 2]);
      context.strokeStyle = ink3;
      context.stroke();
      context.setLineDash([]);
    }
  });

  // 2. Experiment units: a cap along a unit's outer edge, a rail along the inner edge of a sector
  // with units beneath (dotted where only proposed groupings lie beneath). Outlines are kept for
  // hover and selection, so thousands of units never turn the Wheel into a barcode.
  const arcAt = (radius: number, a0: number, a1: number, width: number, colour: string, dash: number[]) => {
    const start = a0 * 2 * Math.PI - Math.PI / 2 + 0.0025;
    const end = a1 * 2 * Math.PI - Math.PI / 2 - 0.0025;
    if (end <= start) return;
    context.beginPath();
    context.arc(layout.cx, layout.cy, radius, start, end);
    context.lineWidth = width;
    context.strokeStyle = colour;
    context.setLineDash(dash);
    context.stroke();
    context.setLineDash([]);
  };
  data.sectors.forEach((sector, index) => {
    const experiment = sector.experiment;
    if (!experiment || index === 0) return;
    const shape = layout.sectors[index];
    if (shape.a1 - shape.a0 <= 0) return;
    const a0 = shape.a0 * grow;
    const a1 = shape.a1 * grow;
    const beneath = experiment.units - (experiment.status !== 'none' ? 1 : 0);
    if (beneath > 0) arcAt(shape.r0 + 2.25, a0, a1, 2.5, ink3, []);
    else if (experiment.proposed_groups) arcAt(shape.r0 + 2.25, a0, a1, 2.5, proposal, [1.5, 2.5]);
    switch (experiment.status) {
      case 'supported':
        arcAt(shape.r1 - 2.75, a0, a1, 3, ink2, []);
        break;
      case 'confirmed':
        arcAt(shape.r1 - 3.25, a0, a1, 4.5, ink, []);
        break;
      case 'conflicting':
        arcAt(shape.r1 - 2.75, a0, a1, 3.5, problem, [4, 2.5]);
        break;
      case 'rejected':
        arcAt(shape.r1 - 2.75, a0, a1, 2, ink3, [1.5, 2.5]);
        break;
      default:
        break;
    }
  });

  // 3 and 4. The perimeter: the kind band and the skyline of bins.
  const {bins} = data;
  const kindDominant = bins.dominant.kind ?? [];
  const measureDominant = bins.dominant[definition.key] ?? [];
  const measurePurity = bins.purity[definition.key] ?? [];
  const logMax = Math.log1p(Math.max(1, layout.maxBinBytes));
  const skyInner = layout.skyline.inner;
  const skyRange = layout.skyline.outer - layout.skyline.inner;
  for (const shape of layout.bins) {
    const span = shape.a1 - shape.a0;
    if (span <= 0) continue;
    const a0 = shape.a0 * grow;
    const a1 = shape.a1 * grow;
    const kindPaint = kindPaints[kindDominant[shape.index] ?? kindPaints.length - 1];
    wedge(context, layout, layout.kindBand.inner, layout.kindBand.outer, a0, a1);
    context.fillStyle = kindPaint ? fillOf(palette, kindPaint) : track;
    context.fill();
    const size = bins.bytes[shape.index] ?? 0;
    const length = 0.18 + 0.82 * (Math.log1p(size) / logMax);
    const paint = paints[measureDominant[shape.index] ?? 0];
    wedge(context, layout, skyInner, skyInner + skyRange * length * grow, a0, a1);
    if (!paint || paint.texture === 'track') {
      context.fillStyle = palette.read('--ix-track-strong');
      context.globalAlpha = 0.55;
    } else {
      context.fillStyle = fillOf(palette, paint);
      context.globalAlpha = 0.35 + 0.65 * ((measurePurity[shape.index] ?? 100) / 100);
    }
    context.fill();
    context.globalAlpha = 1;
  }
  // A hairline between the band and the sectors, and the band's own edge.
  for (const radius of [layout.kindBand.inner - 0.5, layout.kindBand.outer + 0.5]) {
    context.beginPath();
    context.arc(layout.cx, layout.cy, radius, 0, Math.PI * 2);
    context.lineWidth = 1;
    context.strokeStyle = surface;
    context.stroke();
  }

  // The center disc.
  context.beginPath();
  context.arc(layout.cx, layout.cy, layout.centre - 1, 0, Math.PI * 2);
  context.fillStyle = palette.read('--ix-raised');
  context.fill();
  context.lineWidth = 1;
  context.strokeStyle = palette.read('--ix-rule-strong');
  context.stroke();
  const focusExperiment = data.sectors[0]?.experiment;
  if (focusExperiment && focusExperiment.status !== 'none') {
    context.beginPath();
    context.arc(layout.cx, layout.cy, layout.centre - 3, 0, Math.PI * 2);
    context.lineWidth = focusExperiment.status === 'confirmed' ? 3 : 1.5;
    context.strokeStyle = focusExperiment.status === 'conflicting' ? problem : ink;
    if (focusExperiment.status === 'conflicting' || focusExperiment.status === 'rejected') context.setLineDash([4, 3]);
    context.stroke();
    context.setLineDash([]);
  }
  context.restore();
}
