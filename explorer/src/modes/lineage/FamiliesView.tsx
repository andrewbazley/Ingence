// Lineage at scale: name families collapsed into one node each, and the lines between them.
//
// A family is a set of two or more files whose names agree on every word once stage words are left
// out (the engine's own grouping rule forms them; this page forms none). The drawing is the
// quotient graph: one node per family, one edge per kind of line between two families, its width
// the number of lines it stands for. Nothing is invented to make a picture: a node says how many
// files it holds and how many lines touch them; a family some of whose files are in conflict says
// so, with a dashed red stroke and the count in words.
//
//   box stroke weight   files with a line over files (a heavier box: most of its files are joined)
//   dashed red stroke   some of its files are in conflict with the family: "n in conflict"
//   edge width          lines, 1 to 6 px
//   edge dashed         proposed: a rule proposed it and nobody asserted it (the later-stage color)
//
// Clicking a family opens its files as a Grid; a file in no family is chosen by a click and
// centered (as Files) by a double click. The keyboard: the plot is one stop; Tab and Shift Tab walk
// the nodes, the arrows follow an edge, Enter opens a family as a Grid (a lone file is centered as
// Files), F fits the drawing.

import {useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactElement} from 'react';

import type {FamilyEdge, FamilyNode, LineageFamiliesResponse} from '../../api';
import {clip, count, plural} from '../../format';
import {panPixels, useWheel, zoomStep} from '../../wheel';
import {layoutInWorker, type LayoutResult} from './layout';
import type {Orientation} from './LineageView';

const NODE_W = 176;
const NODE_H = 68;
/** A file in no family is drawn as a file: a smaller box, a thin stroke, a page's corner. */
const FILE_W = 156;
const FILE_H = 46;

/** An edge's width in pixels: one line is 1, each doubling adds one, six at most. */
export function edgeWidth(lines: number): number {
  return Math.min(6, Math.max(1, 1 + Math.log2(Math.max(1, lines))));
}

/** A box's stroke weight: heavier where more of its files have a line. 1.2 to 2.8 px. */
export function boxWeight(node: Pick<FamilyNode, 'members' | 'members_with_lines'>): number {
  if (node.members <= 0) return 1.2;
  return 1.2 + 1.6 * Math.min(1, node.members_with_lines / node.members);
}

export const KIND_WORDS: Record<string, string> = {
  published_derivation: 'produced from (published)',
  published_mention: 'mentions (published; not made from)',
  proposed_derivation: 'produced from (proposed)',
  proposed_mention: 'mentions (proposed)',
};

const EDGE_STYLE: Record<string, {stroke: string; dash?: string; start?: string; end?: string}> = {
  published_derivation: {stroke: 'var(--ix-yes)', end: 'url(#ixf-arrow)'},
  published_mention: {stroke: 'var(--ix-ink-2)', dash: '7 3 1.5 3', start: 'url(#ixf-ring)'},
  proposed_derivation: {stroke: 'var(--later, var(--ix-proposal))', dash: '6 4', end: 'url(#ixf-arrow-later)'},
  proposed_mention: {stroke: 'var(--later, var(--ix-proposal))', dash: '2 4', start: 'url(#ixf-ring-later)'},
};

/** What a node says under its name, in words with their denominators. */
export function nodeLines(node: FamilyNode): {counts: string; small: string; conflict: string} {
  if (node.kind === 'file') return {counts: `in no family \u00b7 ${plural(node.lines, 'line')}`, small: '', conflict: ''};
  const small = [
    node.lines_inside > 0 ? `${count(node.lines_inside)} ${node.lines_inside === 1 ? 'line' : 'lines'} inside` : '',
    node.kind === 'family' && node.members_with_lines < node.members ? `${count(node.members_with_lines)} with lines` : '',
    node.kind === 'family' && node.folders > 1 ? plural(node.folders, 'folder') : '',
  ].filter(Boolean).join(' · ');
  return {
    counts: `${plural(node.members, 'file')} · ${plural(node.lines, 'line')}`,
    small,
    conflict: node.members_in_conflict > 0 ? `${count(node.members_in_conflict)} in conflict` : '',
  };
}

function smooth(points: Array<{x: number; y: number}>): string {
  if (!points.length) return '';
  if (points.length < 3) return `M ${points[0].x} ${points[0].y} ${points.slice(1).map((p) => `L ${p.x} ${p.y}`).join(' ')}`;
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const p0 = points[Math.max(0, index - 1)];
    const p1 = points[index];
    const p2 = points[index + 1];
    const p3 = points[Math.min(points.length - 1, index + 2)];
    path += ` C ${p1.x + (p2.x - p0.x) / 6} ${p1.y + (p2.y - p0.y) / 6} ${p2.x - (p3.x - p1.x) / 6} ${p2.y - (p3.y - p1.y) / 6} ${p2.x} ${p2.y}`;
  }
  return path;
}

interface View {
  k: number;
  x: number;
  y: number;
}

const ARROWS: Record<string, [number, number]> = {ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1]};

export function FamiliesView({data, orientation, selected, onSelect, onOpen, onLayoutMeasured}: {
  data: LineageFamiliesResponse;
  orientation: Orientation;
  /** A node's identifier, or `quotient:<digest>` for an edge. */
  selected: string;
  onSelect(id: string): void;
  /** Enter, or a double click: a family opens as a Grid; a file is centered as Files. */
  onOpen(node: FamilyNode): void;
  onLayoutMeasured?(milliseconds: number): void;
}): ReactElement {
  const [layout, setLayout] = useState<LayoutResult | null>(null);
  const [layoutFailed, setLayoutFailed] = useState('');
  const [view, setView] = useState<View>({k: 1, x: 0, y: 0});
  const [box, setBox] = useState({width: 800, height: 520});
  const [hover, setHover] = useState('');
  const [announce, setAnnounce] = useState('');
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{x: number; y: number; view: View} | null>(null);

  const byId = useMemo(() => new Map(data.nodes.map((node) => [node.id, node])), [data]);

  useEffect(() => {
    const element = wrapRef.current;
    if (!element) return undefined;
    const observer = new ResizeObserver((entries) => {
      const found = entries[0]?.contentRect;
      if (found) setBox({width: Math.max(200, found.width), height: Math.max(200, found.height)});
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let stale = false;
    setLayoutFailed('');
    layoutInWorker({
      direction: orientation,
      clusters: [],
      nodes: data.nodes.map((node) => ({id: node.id, width: node.kind === 'file' ? FILE_W : NODE_W, height: node.kind === 'file' ? FILE_H : NODE_H, cluster: null})),
      edges: data.edges.map((edge) => ({id: edge.id, from: edge.flow_from, to: edge.flow_to, weight: Math.max(1, edge.lines)})),
    }).then((result) => {
      if (stale) return;
      setLayout(result);
      onLayoutMeasured?.(result.milliseconds);
    }).catch((error: unknown) => {
      if (!stale) setLayoutFailed(error instanceof Error ? error.message : String(error));
    });
    return () => {
      stale = true;
    };
  }, [data, orientation, onLayoutMeasured]);

  const fit = useCallback(() => {
    if (!layout) return;
    const placed = Object.values(layout.nodes);
    if (!placed.length) return;
    const left = Math.min(...placed.map((p) => p.x - p.width / 2)) - 30;
    const right = Math.max(...placed.map((p) => p.x + p.width / 2)) + 30;
    const top = Math.min(...placed.map((p) => p.y - p.height / 2)) - 30;
    const bottom = Math.max(...placed.map((p) => p.y + p.height / 2)) + 30;
    const k = Math.min(1.4, Math.max(0.2, Math.min(box.width / (right - left), box.height / (bottom - top))));
    setView({k, x: box.width / 2 - (k * (left + right)) / 2, y: box.height / 2 - (k * (top + bottom)) / 2});
  }, [layout, box]);

  useEffect(() => {
    fit();
  }, [fit]);

  // Ctrl + wheel (a trackpad pinch) zooms around the pointer, a sideways wheel pans, and any other
  // wheel scrolls the page (../../wheel).
  useWheel(wrapRef, (event, intent) => {
    const bounds = wrapRef.current?.getBoundingClientRect();
    if (!bounds) return false;
    if (intent === 'pan') {
      const moved = panPixels(event);
      setView((current) => ({...current, x: current.x - moved}));
      return true;
    }
    const px = event.clientX - bounds.left;
    const py = event.clientY - bounds.top;
    const step = zoomStep(event);
    setView((current) => {
      const k = Math.min(3, Math.max(0.15, current.k * step));
      return {k, x: px - ((px - current.x) * k) / current.k, y: py - ((py - current.y) * k) / current.k};
    });
    return true;
  });
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if ((event.target as Element).closest('[data-families-hit]')) return;
    drag.current = {x: event.clientX, y: event.clientY, view};
    (event.currentTarget as HTMLDivElement).setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const held = drag.current;
    if (!held) return;
    setView({...held.view, x: held.view.x + event.clientX - held.x, y: held.view.y + event.clientY - held.y});
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  /** The nodes' neighbors along an edge, in the layout's direction or against it. */
  const neighbors = (id: string): string[] => data.edges.flatMap((edge: FamilyEdge) => (edge.flow_from === id ? [edge.flow_to] : edge.flow_to === id ? [edge.flow_from] : []));

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey || !data.nodes.length) return;
    const current = byId.get(selected) ?? data.nodes[0];
    const say = (node: FamilyNode) => setAnnounce(`${node.label}, ${node.kind === 'family' ? `a family of ${plural(node.members, 'file')}` : 'a file in no family'}, ${plural(node.lines, 'line')}`);
    if (event.key === 'Tab') {
      // With nothing chosen the first Tab chooses the first node, the one with most lines.
      const here = byId.has(selected) ? data.nodes.indexOf(current) : -1;
      const next = here + (event.shiftKey ? -1 : 1);
      // Inside the plot Tab walks the nodes; past the last, or before the first, it leaves the plot.
      if (next < 0 || next >= data.nodes.length) return;
      event.preventDefault();
      onSelect(data.nodes[next].id);
      say(data.nodes[next]);
    } else if (event.key in ARROWS) {
      event.preventDefault();
      if (!layout) return;
      const [dx, dy] = ARROWS[event.key];
      const here = layout.nodes[current.id];
      const ahead = neighbors(current.id)
        .map((id) => ({id, at: layout.nodes[id]}))
        .filter((other) => other.at && here && (other.at.x - here.x) * dx + (other.at.y - here.y) * dy > 0)
        .sort((a, b) => Math.hypot(a.at.x - here.x, a.at.y - here.y) - Math.hypot(b.at.x - here.x, b.at.y - here.y));
      const found = ahead.length ? byId.get(ahead[0].id) : undefined;
      if (found) {
        onSelect(found.id);
        say(found);
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      onOpen(current);
    } else if (event.key === 'f' || event.key === 'F') {
      event.preventDefault();
      fit();
    }
  };

  const chosen = selected;

  return (
    <div
      ref={wrapRef}
      className="ix-canvas-wrap ix-families"
      tabIndex={0}
      role="application"
      aria-roledescription="lineage of name families"
      aria-label={`Lineage of name families: ${plural(data.nodes.length, 'node')}, ${plural(data.edges.length, 'edge')}. Tab and Shift Tab move between nodes, the arrow keys follow an edge, Enter opens a family as a grid, F fits the drawing.`}
      data-draw="families"
      data-nodes={data.nodes.length}
      data-edges={data.edges.length}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onKeyDown={onKeyDown}
      style={{cursor: drag.current ? 'grabbing' : 'grab'}}
    >
      <svg width={box.width} height={box.height} aria-hidden="true" style={{position: 'absolute', inset: 0}}>
        <defs>
          <marker id="ixf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="var(--ix-yes)" />
          </marker>
          <marker id="ixf-arrow-later" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M1 1 L9 5 L1 9 z" fill="var(--ix-panel)" stroke="var(--later, var(--ix-proposal))" strokeWidth="1.4" />
          </marker>
          <marker id="ixf-ring" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse">
            <circle cx="5" cy="5" r="3.5" fill="var(--ix-panel)" stroke="var(--ix-ink-2)" strokeWidth="1.5" />
          </marker>
          <marker id="ixf-ring-later" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse">
            <circle cx="5" cy="5" r="3.5" fill="var(--ix-panel)" stroke="var(--later, var(--ix-proposal))" strokeWidth="1.5" />
          </marker>
        </defs>
        {layout ? (
          <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
            {data.edges.map((edge) => {
              const points = layout.edges[edge.id];
              if (!points) return null;
              const style = EDGE_STYLE[edge.kind] ?? EDGE_STYLE.published_derivation;
              const emphasised = chosen === edge.id || hover === edge.id || chosen === edge.flow_from || chosen === edge.flow_to;
              const width = edgeWidth(edge.lines);
              const path = smooth(points);
              return (
                <g key={edge.id} data-edge={edge.id} data-lines={edge.lines} data-kind={edge.kind}>
                  <path d={path} fill="none" stroke={style.stroke} strokeWidth={chosen === edge.id ? width + 1.5 : width}
                    strokeDasharray={style.dash} markerEnd={style.end} markerStart={style.start}
                    opacity={chosen && !emphasised ? 0.4 : 1} />
                  <path d={path} fill="none" stroke="transparent" strokeWidth={Math.max(12, width + 8)} data-families-hit
                    style={{pointerEvents: 'stroke', cursor: 'pointer'}}
                    onClick={(event) => { event.stopPropagation(); onSelect(edge.id); }}
                    onPointerEnter={() => setHover(edge.id)} onPointerLeave={() => setHover('')}>
                    <title>{`${KIND_WORDS[edge.kind] ?? edge.kind}: ${plural(edge.lines, 'line')}, between ${byId.get(edge.flow_from)?.label ?? 'a node'} and ${byId.get(edge.flow_to)?.label ?? 'a node'}`}</title>
                  </path>
                </g>
              );
            })}
            {data.nodes.map((node) => {
              const placed = layout.nodes[node.id];
              if (!placed) return null;
              const left = placed.x - placed.width / 2;
              const top = placed.y - placed.height / 2;
              const said = nodeLines(node);
              const conflicted = node.members_in_conflict > 0;
              const picked = chosen === node.id;
              const isFile = node.kind === 'file';
              return (
                <g key={node.id} data-families-hit data-node={node.id} data-kind={node.kind} data-members={node.members}
                  data-conflict={node.members_in_conflict} style={{cursor: 'pointer'}}
                  onClick={(event) => { event.stopPropagation(); if (node.kind === 'family') onOpen(node); else onSelect(node.id); }}
                  onDoubleClick={(event) => { event.stopPropagation(); if (node.kind === 'file') onOpen(node); }}
                  onPointerEnter={() => setHover(node.id)} onPointerLeave={() => setHover('')}>
                  <rect x={left} y={top} width={placed.width} height={placed.height} rx={isFile ? 6 : 8}
                    fill={picked ? 'var(--accent-soft, var(--ix-track))' : isFile ? 'var(--ix-panel)' : 'var(--ix-raised)'}
                    stroke={conflicted ? 'var(--ix-problem)' : picked ? 'var(--ix-focus)' : hover === node.id ? 'var(--ix-ink-2)' : isFile ? 'var(--ix-rule-strong)' : 'var(--ix-ink)'}
                    strokeWidth={picked ? Math.max(2, boxWeight(node)) : isFile ? 1.2 : boxWeight(node)}
                    strokeDasharray={conflicted ? '4 3' : undefined} />
                  {isFile ? (
                    <g transform={`translate(${left + 9} ${top + 10})`} aria-hidden="true">
                      <path d="M0.5 0.5h9l3.5 3.5v17h-12.5z" fill="var(--ix-raised)" stroke="var(--ix-ink-3)" />
                    </g>
                  ) : null}
                  <text x={isFile ? placed.x + 10 : placed.x} y={top + (isFile ? 19 : 20)} fontSize={isFile ? 12 : 12.5} fontWeight={isFile ? 500 : 700} textAnchor="middle" fill="var(--ix-ink)">{clip(node.label, isFile ? 17 : 24)}</text>
                  <text x={isFile ? placed.x + 10 : placed.x} y={top + (isFile ? 35 : 37)} fontSize={isFile ? 10.5 : 11.5} textAnchor="middle" fill="var(--ix-ink-2)">{said.counts}</text>
                  {said.conflict ? (
                    <>
                      <circle cx={placed.x - said.conflict.length * 3 - 6} cy={top + 48} r={3.5} fill="var(--ix-problem)" />
                      <text x={placed.x + 2} y={top + 51} fontSize={11} textAnchor="middle" fill="var(--ix-ink)">{said.conflict}</text>
                    </>
                  ) : null}
                  {said.small ? (
                    <text x={placed.x} y={top + (said.conflict ? 63 : 53)} fontSize={10} textAnchor="middle" fill="var(--ix-ink-2)">{clip(said.small, 34)}</text>
                  ) : null}
                  <title>{isFile
                    ? `${node.label}: a file with a line that belongs to no family; ${plural(node.lines, 'line')}.`
                    : `${node.label}: a family of ${plural(node.members, 'file')}; ${plural(node.members_with_lines, 'file')} with a line; ${plural(node.lines, 'line')}${node.lines_inside ? `, ${node.lines_inside} inside it` : ''}${node.members_in_conflict ? `; ${node.members_in_conflict} in conflict with the family` : ''}.`}</title>
                </g>
              );
            })}
          </g>
        ) : null}
      </svg>
      {layoutFailed ? (
        <div className="ix-state" role="alert"><div><h2>These families could not be laid out</h2><p>{layoutFailed}. The table lists every family and line.</p></div></div>
      ) : !layout ? (
        <div className="ix-state" role="status"><div><div className="ix-spin" aria-hidden="true" /><h2>Laying out the families</h2></div></div>
      ) : null}
      <div className="ix-zoom">
        <button type="button" className="ix-btn ix-quiet" onClick={() => fit()} title="Fit every family drawn (F)">Fit</button>
      </div>
      <div className="ix-sr" aria-live="polite">{announce}</div>
    </div>
  );
}
