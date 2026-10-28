// The Wheel's layout: pure arithmetic over what the server sent, no drawing. Angles are turns
// (0 at the top, clockwise, one turn a whole circle); radii are CSS pixels.
//
// A sector's angle is its share of the center's files (or bytes). Its children take their shares
// of its span in the server's order (largest first, the gathered "more" last), and what is left
// at the end of the span is its own files: the perimeter draws those there. A bin's width is its
// share of its sector's perimeter span.

import type {WheelResponse} from '../../api';

export interface Ring {
  inner: number;
  outer: number;
}

export interface SectorShape {
  index: number;
  a0: number;
  a1: number;
  r0: number;
  r1: number;
  /** Where the perimeter counts this sector's own files, when it counts any. */
  leaf0: number;
  leaf1: number;
}

export interface BinShape {
  index: number;
  a0: number;
  a1: number;
}

export interface WheelLayout {
  cx: number;
  cy: number;
  radius: number;
  centre: number;
  rings: Ring[];
  kindBand: Ring;
  skyline: Ring;
  sectors: SectorShape[];
  bins: BinShape[];
  /** Sector indices per ring, sorted by start angle, for hit testing. */
  byRing: number[][];
  /** Bin indices sorted by start angle. */
  binOrder: number[];
  maxBinBytes: number;
}

export function layoutWheel(data: WheelResponse, width: number, height: number, sizeBy: 'files' | 'bytes'): WheelLayout {
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.max(60, Math.min(width, height) / 2 - 14);
  const centre = Math.max(40, radius * 0.2);
  const ringCount = Math.max(1, ...data.sectors.map((sector) => sector.ring));
  const sectorOuter = radius * 0.8;
  const thickness = (sectorOuter - centre) / ringCount;
  const rings: Ring[] = [{inner: 0, outer: centre}];
  for (let ring = 1; ring <= ringCount; ring += 1) {
    rings.push({inner: centre + (ring - 1) * thickness, outer: centre + ring * thickness});
  }
  const kindBand = {inner: radius * 0.815, outer: radius * 0.855};
  const skyline = {inner: radius * 0.865, outer: radius};

  const weight = (index: number) => {
    const sector = data.sectors[index];
    return sizeBy === 'files' ? sector.files : sector.bytes;
  };
  // A folder with no folders drawn beyond it reaches out to the edge of the sectors, so its files'
  // measure fills the space and its perimeter run sits right against it; a center with none drawn
  // is a whole ring.
  const reaches = (sector: WheelResponse['sectors'][number], index: number) =>
    index > 0 && (sector.kind === 'more' || sector.leaf === 'all');
  const alone = data.sectors.length === 1;
  const shapes: SectorShape[] = data.sectors.map((sector, index) => ({
    index,
    a0: 0,
    a1: index === 0 ? 1 : 0,
    r0: index === 0 && alone ? centre : (rings[sector.ring]?.inner ?? 0),
    r1: reaches(sector, index) || (index === 0 && alone) ? sectorOuter : (rings[sector.ring]?.outer ?? 0),
    leaf0: 0,
    leaf1: 0,
  }));
  const children = new Map<string, number[]>();
  data.sectors.forEach((sector, index) => {
    if (sector.parent === null || index === 0) return;
    const key = sector.parent;
    const held = children.get(key) ?? [];
    held.push(index);
    children.set(key, held);
  });
  // Breadth first, which is the server's order: a parent is placed before its children.
  data.sectors.forEach((sector, index) => {
    const shape = shapes[index];
    const span = shape.a1 - shape.a0;
    const whole = weight(index);
    let cursor = shape.a0;
    const kids = sector.kind !== 'more' ? (children.get(sector.id) ?? []).filter((kid) => data.sectors[kid].ring === sector.ring + 1) : [];
    for (const kid of kids) {
      const share = whole > 0 ? (span * weight(kid)) / whole : 0;
      shapes[kid].a0 = cursor;
      shapes[kid].a1 = cursor + share;
      cursor += share;
    }
    if (sector.leaf === 'all') {
      shape.leaf0 = shape.a0;
      shape.leaf1 = shape.a1;
    } else if (sector.leaf === 'direct') {
      shape.leaf0 = cursor;
      shape.leaf1 = shape.a1;
    } else {
      shape.leaf0 = shape.leaf1 = shape.a1;
    }
  });

  // Bins, sector by sector: each takes its share of its sector's perimeter span.
  const bins: BinShape[] = [];
  const {sector: binSector, n, bytes: binBytes} = data.bins;
  let start = 0;
  let maxBinBytes = 0;
  while (start < binSector.length) {
    let end = start;
    let files = 0;
    let size = 0;
    while (end < binSector.length && binSector[end] === binSector[start]) {
      files += n[end];
      size += binBytes[end];
      end += 1;
    }
    const shape = shapes[binSector[start]];
    const span = shape.leaf1 - shape.leaf0;
    let cursor = shape.leaf0;
    for (let index = start; index < end; index += 1) {
      const share = sizeBy === 'bytes' && size > 0 ? binBytes[index] / size : n[index] / Math.max(1, files);
      bins.push({index, a0: cursor, a1: cursor + span * share});
      cursor += span * share;
      maxBinBytes = Math.max(maxBinBytes, binBytes[index]);
    }
    start = end;
  }

  const byRing: number[][] = rings.map(() => []);
  shapes.forEach((shape, index) => {
    const ring = data.sectors[index].ring;
    if (shape.a1 > shape.a0) byRing[ring]?.push(index);
  });
  for (const list of byRing) list.sort((a, b) => shapes[a].a0 - shapes[b].a0);
  const binOrder = bins.map((_, index) => index).filter((index) => bins[index].a1 > bins[index].a0)
    .sort((a, b) => bins[a].a0 - bins[b].a0);
  return {cx, cy, radius, centre, rings, kindBand, skyline, sectors: shapes, bins, byRing, binOrder, maxBinBytes};
}

export type Hit =
  | {kind: 'centre'}
  | {kind: 'sector'; index: number}
  | {kind: 'bin'; index: number}
  | {kind: 'none'};

function turnOf(layout: WheelLayout, x: number, y: number): number {
  const angle = Math.atan2(x - layout.cx, -(y - layout.cy));
  return (angle / (2 * Math.PI) + 1) % 1;
}

function search<T extends {a0: number; a1: number}>(order: number[], shapes: T[], turn: number): number {
  let low = 0;
  let high = order.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const shape = shapes[order[middle]];
    if (turn < shape.a0) high = middle - 1;
    else if (turn >= shape.a1) low = middle + 1;
    else return order[middle];
  }
  return -1;
}

export function hitTest(layout: WheelLayout, x: number, y: number): Hit {
  const distance = Math.hypot(x - layout.cx, y - layout.cy);
  if (distance <= layout.centre) return {kind: 'centre'};
  const turn = turnOf(layout, x, y);
  if (distance >= layout.kindBand.inner - 2 && distance <= layout.skyline.outer + 4) {
    const found = search(layout.binOrder, layout.bins, turn);
    return found >= 0 ? {kind: 'bin', index: found} : {kind: 'none'};
  }
  if (layout.sectors.length === 1 && distance < layout.sectors[0].r1) return {kind: 'centre'};
  for (let ring = 1; ring < layout.rings.length; ring += 1) {
    const band = layout.rings[ring];
    if (distance >= band.inner && distance < band.outer) {
      // A wedge reaching out from an inner ring is found in its own ring.
      for (let inner = ring; inner >= 1; inner -= 1) {
        const found = search(layout.byRing[inner], layout.sectors, turn);
        if (found >= 0 && layout.sectors[found].r1 >= distance) return {kind: 'sector', index: found};
      }
      return {kind: 'none'};
    }
  }
  return {kind: 'none'};
}

/** An annular wedge as an SVG path, for crisp outlines over the canvas. */
export function wedgePath(layout: WheelLayout, r0: number, r1: number, a0: number, a1: number): string {
  const {cx, cy} = layout;
  const full = a1 - a0 >= 0.9999;
  const point = (r: number, turn: number) => {
    const angle = turn * 2 * Math.PI;
    return [cx + r * Math.sin(angle), cy - r * Math.cos(angle)];
  };
  if (full) {
    const outer = `M ${cx} ${cy - r1} A ${r1} ${r1} 0 1 1 ${cx} ${cy + r1} A ${r1} ${r1} 0 1 1 ${cx} ${cy - r1} Z`;
    if (r0 <= 0) return outer;
    return `${outer} M ${cx} ${cy - r0} A ${r0} ${r0} 0 1 0 ${cx} ${cy + r0} A ${r0} ${r0} 0 1 0 ${cx} ${cy - r0} Z`;
  }
  const large = a1 - a0 > 0.5 ? 1 : 0;
  const [x0, y0] = point(r1, a0);
  const [x1, y1] = point(r1, a1);
  const [x2, y2] = point(r0, a1);
  const [x3, y3] = point(r0, a0);
  return `M ${x0} ${y0} A ${r1} ${r1} 0 ${large} 1 ${x1} ${y1} L ${x2} ${y2} A ${r0} ${r0} 0 ${large} 0 ${x3} ${y3} Z`;
}

export function polar(layout: WheelLayout, r: number, turn: number): [number, number] {
  const angle = turn * 2 * Math.PI;
  return [layout.cx + r * Math.sin(angle), layout.cy - r * Math.cos(angle)];
}
