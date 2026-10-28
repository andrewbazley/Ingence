// Lineage: how files led to an output, drawn as layers from raw material to what came of it.
//
// The visual grammar, the same in every mode:
//   published derivation  solid blue line, filled arrow           (the run asserted "made from")
//   published mention     gray dash-dot line, open circle at the file mentioned  (never "made from")
//   proposed              violet dashed line, hollow arrow, only when Proposals is on
//   folder                a quiet box around its files (indexed containment), unit bar if a rule
//                         reads it as an experiment's folder
//   group                 a pill in the lane at the side, dotted lines to its members (a group
//                         never says one file was made from another)
//   open end              a short dashed stub ending in "?": the line names something that is not
//                         an entry of this run
//   a person's words      magenta italics (labels), never mistaken for a file's name

import {useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactElement} from 'react';

import type {LineageNode, LineageResponse} from '../../api';
import {clip, compressSiblings, count, extensionLabel, plural} from '../../format';
import {KIND_VAR} from '../../icons';
import {RELATION_WORDS, type ShownGroup, type ShownRelation} from '../../human';
import {panPixels, useWheel, zoomStep} from '../../wheel';
import {layoutInWorker, type LayoutResult} from './layout';

export type Orientation = 'TB' | 'BT' | 'LR' | 'RL';

interface LineageViewProps {
  data: LineageResponse;
  orientation: Orientation;
  selected: string;
  collapsed: string[];
  labels: Record<string, string>;
  onSelect(id: string): void;
  onRefocus(id: string): void;
  onExpand(id: string): void;
  onOpen(id: string): void;
  onToggleCollapse(id: string): void;
  onLayoutMeasured?(milliseconds: number): void;
  /** Staged moves in the plan: a file drawn in the folder it would go to. */
  moves?: Map<string, {to_id: string; to_name: string; from_name: string}>;
  onStageMove?(id: string, folder: string): void;
  /** A person's layer: relations proposed, entries set aside, groups made (magenta, never the
   * engine's colors: a statement, not provenance). */
  layer?: {
    relations: ShownRelation[];
    excluded: Map<string, {reason: string; pending: boolean}>;
    groups: ShownGroup[];
  };
}

const NODE_W = 196;
const NODE_H = 42;
const AGG_H = 50;
const LANE_GAP = 44;
const PILL_W = 200;
const PILL_H = 26;

const EDGE_STYLE: Record<string, {stroke: string; width: number; dash?: string; start?: string; end?: string}> = {
  published_derivation: {stroke: 'var(--ix-yes)', width: 2, end: 'url(#ix-arrow)'},
  published_mention: {stroke: 'var(--ix-ink-2)', width: 1.4, dash: '7 3 1.5 3', start: 'url(#ix-ring)'},
  proposed_derivation: {stroke: 'var(--ix-proposal)', width: 1.6, dash: '5 4', end: 'url(#ix-arrow-hollow)'},
  proposed_mention: {stroke: 'var(--ix-proposal)', width: 1.2, dash: '2 4', start: 'url(#ix-ring-proposal)'},
  person_relation: {stroke: 'var(--ix-human)', width: 1.6, dash: '7 4', end: 'url(#ix-arrow-human)'},
};

export const EDGE_WORDS: Record<string, string> = {
  published_derivation: 'produced from (published)',
  published_mention: 'mentions (published; not made from)',
  proposed_derivation: 'produced from (proposed)',
  proposed_mention: 'mentions (proposed)',
  group_member: 'member of a group (not provenance)',
  person_relation: 'said by a person (a statement in the Record, not provenance)',
};

/** What an open end's other end is, in words: why nothing is drawn there. */
export function openEndWords(reason: string): string {
  switch (reason) {
    case 'entry_deleted':
      return 'a file deleted before this run';
    case 'folder':
      return 'a folder of this run, not a file (a lineage draws files, and a folder as the cluster around them)';
    case 'inside_a_container':
      return 'a member inside a container file, not a file of the folder hierarchy';
    default:
      return 'not an item of this run';
  }
}

/** Nodes still drawn once some branches are collapsed: reachable from the focus without passing
 * outward through a collapsed node. A file reached another way stays, so a join is never lost. */
export function visibleNodes(data: LineageResponse, collapsed: Set<string>): Set<string> {
  const neighbours = new Map<string, string[]>();
  for (const edge of data.edges) {
    if (edge.kind === 'group_member') continue;
    neighbours.set(edge.source, [...(neighbours.get(edge.source) ?? []), edge.target]);
    neighbours.set(edge.target, [...(neighbours.get(edge.target) ?? []), edge.source]);
  }
  const hop = new Map(data.nodes.map((node) => [node.id, node.hop]));
  const seen = new Set<string>();
  const queue = data.nodes.filter((node) => node.is_focus || data.seeds.includes(node.id)).map((node) => node.id);
  for (const id of queue) seen.add(id);
  while (queue.length) {
    const here = queue.shift() as string;
    if (collapsed.has(here) && !data.seeds.includes(here)) continue;
    for (const next of neighbours.get(here) ?? []) {
      if (seen.has(next)) continue;
      // Past a collapsed node only toward the focus.
      if (collapsed.has(here) && Math.abs(hop.get(next) ?? 0) > Math.abs(hop.get(here) ?? 0)) continue;
      seen.add(next);
      queue.push(next);
    }
  }
  if (!seen.size) data.nodes.forEach((node) => seen.add(node.id));
  return seen;
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
    const c1 = {x: p1.x + (p2.x - p0.x) / 6, y: p1.y + (p2.y - p0.y) / 6};
    const c2 = {x: p2.x - (p3.x - p1.x) / 6, y: p2.y - (p3.y - p1.y) / 6};
    path += ` C ${c1.x} ${c1.y} ${c2.x} ${c2.y} ${p2.x} ${p2.y}`;
  }
  return path;
}

interface View {
  k: number;
  x: number;
  y: number;
}

export function LineageView(props: LineageViewProps): ReactElement {
  const {data, orientation, selected, collapsed, labels, onSelect, onRefocus, onExpand, onOpen, onToggleCollapse, onLayoutMeasured, moves, onStageMove, layer} = props;
  const [dragging, setDragging] = useState<{id: string; x: number; y: number; moved: boolean} | null>(null);
  const [layout, setLayout] = useState<LayoutResult | null>(null);
  const [layoutFailed, setLayoutFailed] = useState('');
  const [view, setView] = useState<View>({k: 1, x: 0, y: 0});
  const [box, setBox] = useState({width: 800, height: 600});
  const [hover, setHover] = useState('');
  const [announce, setAnnounce] = useState('');
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{x: number; y: number; view: View} | null>(null);

  const hidden = useMemo(() => new Set(collapsed), [collapsed]);
  const visible = useMemo(() => visibleNodes(data, hidden), [data, hidden]);
  const nodes = useMemo(() => data.nodes.filter((node) => visible.has(node.id)), [data, visible]);
  const byId = useMemo(() => new Map(data.nodes.map((node) => [node.id, node])), [data]);
  const lineEdges = useMemo(
    () => data.edges.filter((edge) => edge.kind !== 'group_member' && visible.has(edge.source) && visible.has(edge.target)),
    [data, visible],
  );
  const memberEdges = useMemo(
    () => data.edges.filter((edge) => edge.kind === 'group_member' && visible.has(edge.source)),
    [data, visible],
  );
  // A person's groups with a member drawn here: the file itself, or the folder it is in.
  const humanMembers = useMemo(() => {
    const found: Array<{group: ShownGroup; node: string}> = [];
    for (const group of layer?.groups ?? []) {
      const members = new Set(group.members);
      for (const node of data.nodes) {
        if (!visible.has(node.id)) continue;
        if (members.has(node.id) || (node.folder_id && members.has(node.folder_id))) found.push({group, node: node.id});
      }
    }
    return found;
  }, [layer, data.nodes, visible]);
  const humanGroups = useMemo(() => [...new Map(humanMembers.map((item) => [item.group.key, item.group])).values()], [humanMembers]);
  const names = useMemo(() => {
    // Siblings in one folder show the part of their names that tells them apart.
    const byFolder = new Map<string, LineageNode[]>();
    for (const node of nodes) {
      if (node.kind !== 'file') continue;
      const key = node.folder_id ?? '';
      byFolder.set(key, [...(byFolder.get(key) ?? []), node]);
    }
    const shown = new Map<string, string>();
    for (const members of byFolder.values()) {
      const plain = members.filter((node) => !labels[node.id]);
      const compressed = compressSiblings(plain.map((node) => node.name));
      plain.forEach((node, index) => shown.set(node.id, compressed[index]));
    }
    return shown;
  }, [nodes, labels]);

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
    const folders = new Map(data.folders.map((folder) => [folder.id, folder]));
    const request = {
      direction: orientation,
      clusters: [...new Set(nodes.map((node) => node.folder_id).filter((id): id is string => Boolean(id)))]
        .filter((id) => folders.has(id))
        .map((id) => ({id: `cluster:${id}`, label: folders.get(id)?.name ?? ''})),
      nodes: nodes.map((node) => {
        const folder = moves?.get(node.id)?.to_id ?? node.folder_id;
        return {
          id: node.id,
          width: NODE_W,
          height: node.kind === 'aggregate' ? AGG_H : NODE_H,
          cluster: folder && folders.has(folder) ? `cluster:${folder}` : null,
        };
      }),
      edges: lineEdges.map((edge) => ({
        id: edge.id,
        from: edge.flow_from,
        to: edge.flow_to,
        weight: edge.kind === 'published_derivation' ? 4 : 1,
      })),
    };
    setLayoutFailed('');
    layoutInWorker(request).then((result) => {
      if (stale) return;
      setLayout(result);
      onLayoutMeasured?.(result.milliseconds);
    }).catch((error: unknown) => {
      if (!stale) setLayoutFailed(error instanceof Error ? error.message : String(error));
    });
    return () => {
      stale = true;
    };
  }, [data, nodes, lineEdges, orientation, onLayoutMeasured, moves]);

  const fit = useCallback((only?: string[]) => {
    if (!layout) return;
    const placed = (only && only.length ? only : Object.keys(layout.nodes)).map((id) => layout.nodes[id]).filter(Boolean);
    if (!placed.length) return;
    const lane = (data.groups.length || humanGroups.length) && !only ? PILL_W + LANE_GAP : 0;
    const left = Math.min(...placed.map((p) => p.x - p.width / 2)) - 30;
    const right = Math.max(...placed.map((p) => p.x + p.width / 2)) + 30 + lane;
    const top = Math.min(...placed.map((p) => p.y - p.height / 2)) - 40;
    const bottom = Math.max(...placed.map((p) => p.y + p.height / 2)) + 40;
    const k = Math.min(1.6, Math.max(0.2, Math.min(box.width / (right - left), box.height / (bottom - top))));
    setView({k, x: box.width / 2 - k * (left + right) / 2, y: box.height / 2 - k * (top + bottom) / 2});
  }, [layout, box, data.groups.length, humanGroups.length]);

  // A new layout is fitted to the page; a new selection is kept in sight.
  useEffect(() => {
    fit();
  }, [layout, fit]);

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
    if ((event.target as Element).closest('[data-lineage-hit]')) return;
    drag.current = {x: event.clientX, y: event.clientY, view};
    (event.currentTarget as HTMLDivElement).setPointerCapture(event.pointerId);
  };
  const toLayout = (clientX: number, clientY: number) => {
    const bounds = wrapRef.current?.getBoundingClientRect();
    if (!bounds) return {x: 0, y: 0};
    return {x: (clientX - bounds.left - view.x) / view.k, y: (clientY - bounds.top - view.y) / view.k};
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragging) {
      const at = toLayout(event.clientX, event.clientY);
      const moved = dragging.moved || Math.hypot(at.x - dragging.x, at.y - dragging.y) > 6 / view.k;
      setDragging({...dragging, x: at.x, y: at.y, moved});
      return;
    }
    const held = drag.current;
    if (!held) return;
    setView({...held.view, x: held.view.x + event.clientX - held.x, y: held.view.y + event.clientY - held.y});
  };
  const onPointerUp = () => {
    drag.current = null;
    if (dragging && dragging.moved && layout && onStageMove) {
      // Dropped on a folder other than its own: a move staged in the plan, nothing more.
      const over = Object.entries(layout.clusters).find(([, placed]) =>
        Math.abs(dragging.x - placed.x) <= placed.width / 2 && Math.abs(dragging.y - placed.y) <= placed.height / 2);
      const node = byId.get(dragging.id);
      const folder = over ? over[0].slice('cluster:'.length) : '';
      const current = moves?.get(dragging.id)?.to_id ?? node?.folder_id;
      if (folder && node && folder !== current) onStageMove(dragging.id, folder);
    }
    setDragging(null);
  };

  // Keyboard: up and down along lines, left and right within a layer, Enter to refocus.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!layout) return;
    const current = selected && layout.nodes[selected] ? selected : (nodes.find((node) => node.is_focus)?.id ?? nodes[0]?.id);
    if (!current) return;
    const here = layout.nodes[current];
    const vertical = orientation === 'TB' || orientation === 'BT';
    const choose = (candidates: string[]) => {
      if (!candidates.length) return;
      const nearest = candidates.sort((a, b) => {
        const pa = layout.nodes[a];
        const pb = layout.nodes[b];
        return Math.hypot(pa.x - here.x, pa.y - here.y) - Math.hypot(pb.x - here.x, pb.y - here.y);
      })[0];
      onSelect(nearest);
      const node = byId.get(nearest);
      if (node) setAnnounce(`${node.name}, ${node.kind === 'aggregate' ? `${node.members} files` : node.file_kind}`);
    };
    const placedIds = Object.keys(layout.nodes);
    const sameLayer = placedIds.filter((id) => id !== current && (vertical ? Math.abs(layout.nodes[id].y - here.y) < 4 : Math.abs(layout.nodes[id].x - here.x) < 4));
    const before = (id: string) => (vertical ? layout.nodes[id].x < here.x : layout.nodes[id].y < here.y);
    const upstream = lineEdges.filter((edge) => edge.flow_to === current).map((edge) => edge.flow_from);
    const downstream = lineEdges.filter((edge) => edge.flow_from === current).map((edge) => edge.flow_to);
    const towardTop = orientation === 'BT' || orientation === 'RL' ? downstream : upstream;
    const towardBottom = orientation === 'BT' || orientation === 'RL' ? upstream : downstream;
    let handled = true;
    switch (event.key) {
      case 'ArrowUp':
        choose(vertical ? towardTop : sameLayer.filter(before));
        break;
      case 'ArrowDown':
        choose(vertical ? towardBottom : sameLayer.filter((id) => !before(id)));
        break;
      case 'ArrowLeft':
        choose(vertical ? sameLayer.filter(before) : towardTop);
        break;
      case 'ArrowRight':
        choose(vertical ? sameLayer.filter((id) => !before(id)) : towardBottom);
        break;
      case 'Enter':
        if (byId.get(current)?.kind === 'aggregate') onOpen(current);
        else onRefocus(current);
        break;
      case '+':
      case '=':
        onExpand(current);
        break;
      case '-':
        onToggleCollapse(current);
        break;
      case 'f':
        fit();
        break;
      default:
        handled = false;
    }
    if (handled) event.preventDefault();
  };

  const vertical = orientation === 'TB' || orientation === 'BT';
  const upSide = orientation === 'TB' ? 'top' : orientation === 'BT' ? 'bottom' : orientation === 'LR' ? 'left' : 'right';
  const downSide = {top: 'bottom', bottom: 'top', left: 'right', right: 'left'}[upSide] as string;
  const sidePoint = (placed: {x: number; y: number; width: number; height: number}, side: string, offset = 0) => {
    switch (side) {
      case 'top':
        return {x: placed.x, y: placed.y - placed.height / 2 - offset};
      case 'bottom':
        return {x: placed.x, y: placed.y + placed.height / 2 + offset};
      case 'left':
        return {x: placed.x - placed.width / 2 - offset, y: placed.y};
      default:
        return {x: placed.x + placed.width / 2 + offset, y: placed.y};
    }
  };
  const selectedEdges = new Set(lineEdges.filter((edge) => edge.source === selected || edge.target === selected).map((edge) => edge.id));
  const laneX = layout ? layout.width + LANE_GAP : 0;
  const pills = data.groups.map((group, index) => ({group, x: laneX, y: 24 + index * (PILL_H + 10)}));
  const pillById = new Map(pills.map((pill) => [pill.group.id, pill]));
  const humanPills = humanGroups.map((group, index) => ({group, x: laneX, y: 24 + (pills.length + index) * (PILL_H + 10) + (pills.length ? 14 : 0)}));
  const humanPillByKey = new Map(humanPills.map((pill) => [pill.group.key, pill]));
  const unitOf = new Map(data.folders.map((folder) => [folder.id, folder]));

  return (
    <div
      ref={wrapRef}
      className="ix-canvas-wrap ix-lineage"
      tabIndex={0}
      role="application"
      aria-roledescription="lineage"
      aria-label={`Lineage around ${byId.get(data.focus)?.name ?? 'the focus'}: ${plural(nodes.length, 'item')}, ${plural(lineEdges.length, 'line')}. Arrow keys move along lines, Enter refocuses, plus expands a branch, minus collapses it, F fits.`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onKeyDown={onKeyDown}
      style={{cursor: drag.current ? 'grabbing' : 'grab'}}
    >
      <svg width={box.width} height={box.height} aria-hidden="true" style={{position: 'absolute', inset: 0}}>
        <defs>
          <marker id="ix-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="var(--ix-yes)" />
          </marker>
          <marker id="ix-arrow-hollow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M1 1 L9 5 L1 9 z" fill="var(--ix-panel)" stroke="var(--ix-proposal)" strokeWidth="1.4" />
          </marker>
          <marker id="ix-arrow-human" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M1 1 L9 5 L1 9" fill="none" stroke="var(--ix-human)" strokeWidth="1.6" />
          </marker>
          <marker id="ix-ring" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7">
            <circle cx="5" cy="5" r="3.5" fill="var(--ix-panel)" stroke="var(--ix-ink-2)" strokeWidth="1.5" />
          </marker>
          <marker id="ix-ring-proposal" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7">
            <circle cx="5" cy="5" r="3.5" fill="var(--ix-panel)" stroke="var(--ix-proposal)" strokeWidth="1.5" />
          </marker>
        </defs>
        {layout ? (
          <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
            {Object.entries(layout.clusters).map(([id, placed]) => {
              const folder = unitOf.get(id.slice('cluster:'.length));
              const unit = folder && folder.unit_status !== 'none';
              return (
                <g key={id}>
                  <rect x={placed.x - placed.width / 2} y={placed.y - placed.height / 2} width={placed.width} height={placed.height}
                    rx={10} fill="var(--ix-track)" fillOpacity={0.55} stroke="var(--ix-rule-strong)" strokeWidth={1} />
                  {unit ? (
                    <path d={`M ${placed.x - placed.width / 2 + 10} ${placed.y - placed.height / 2 + 1.5} h ${placed.width - 20}`}
                      stroke={folder?.unit_status === 'conflicting' ? 'var(--ix-problem)' : 'var(--ix-ink-2)'}
                      strokeWidth={folder?.unit_status === 'confirmed' ? 4 : 3} />
                  ) : null}
                  <text x={placed.x - placed.width / 2 + 10} y={placed.y - placed.height / 2 + 16} fontSize={11} fill="var(--ix-ink-2)">
                    {labels[folder?.id ?? ''] ?? clip(folder?.name ?? '', Math.floor(placed.width / 6.5))}
                    {unit ? ` · experiment unit (${folder?.unit_status})` : ''}
                  </text>
                </g>
              );
            })}
            {lineEdges.map((edge) => {
              const points = layout.edges[edge.id];
              if (!points) return null;
              const style = EDGE_STYLE[edge.kind] ?? EDGE_STYLE.published_derivation;
              const emphasised = selectedEdges.has(edge.id) || selected === edge.id || hover === edge.id;
              const width = edge.lines > 1 ? Math.min(6, style.width + Math.log2(edge.lines)) : style.width;
              return (
                <g key={edge.id}>
                  <path d={smooth(points)} fill="none" stroke={style.stroke} strokeWidth={emphasised ? width + 1.2 : width}
                    strokeDasharray={style.dash} markerEnd={style.end} markerStart={style.start}
                    opacity={selected && !emphasised && selected !== '' && byId.has(selected) ? 0.45 : 1} />
                  <path d={smooth(points)} fill="none" stroke="transparent" strokeWidth={12} data-lineage-hit
                    style={{pointerEvents: 'stroke', cursor: 'pointer'}}
                    onClick={(event) => { event.stopPropagation(); onSelect(edge.kind === 'person_relation' ? edge.id : `edge:${edge.id}`); }}
                    onPointerEnter={() => setHover(edge.id)} onPointerLeave={() => setHover('')}>
                    <title>{`${EDGE_WORDS[edge.kind]}${edge.lines > 1 ? `: ${edge.lines} lines` : ''}${edge.rule_id ? `, by ${edge.rule_id} v${edge.rule_version}` : ''}`}</title>
                  </path>
                </g>
              );
            })}
            {memberEdges.map((edge) => {
              const from = layout.nodes[edge.source];
              const pill = pillById.get(edge.target);
              if (!from || !pill) return null;
              const start = {x: from.x + from.width / 2, y: from.y};
              const end = {x: pill.x, y: pill.y + PILL_H / 2};
              const mid = (start.x + end.x) / 2;
              return (
                <path key={edge.id} d={`M ${start.x} ${start.y} C ${mid} ${start.y} ${mid} ${end.y} ${end.x} ${end.y}`}
                  fill="none" stroke="var(--ix-ink-3)" strokeWidth={selected === edge.target || hover === edge.target ? 1.6 : 1}
                  strokeDasharray="1.5 3" opacity={selected === edge.target || hover === edge.target ? 1 : 0.6} />
              );
            })}
            {pills.map(({group, x, y}) => (
              <g key={group.id} transform={`translate(${x} ${y})`} data-lineage-hit style={{cursor: 'pointer'}}
                onClick={(event) => { event.stopPropagation(); onSelect(group.id); }}
                onPointerEnter={() => setHover(group.id)} onPointerLeave={() => setHover('')}>
                <rect width={PILL_W} height={PILL_H} rx={PILL_H / 2} fill="var(--ix-raised)"
                  stroke={selected === group.id ? 'var(--ix-focus)' : 'var(--ix-rule-strong)'} strokeWidth={selected === group.id ? 2 : 1} />
                <g transform="translate(8 5)">
                  <rect x="4" y="0" width="9" height="11" rx="1" fill="var(--ix-raised)" stroke="var(--ix-ink-3)" />
                  <rect x="2" y="2" width="9" height="11" rx="1" fill="var(--ix-raised)" stroke="var(--ix-ink-3)" />
                  <rect x="0" y="4" width="9" height="11" rx="1" fill="var(--ix-raised)" stroke="var(--ix-ink-2)" />
                </g>
                <text x={28} y={17} fontSize={11} fill="var(--ix-ink)">{clip(group.label, 22)}</text>
                <text x={PILL_W - 8} y={17} fontSize={10.5} fill="var(--ix-ink-3)" textAnchor="end">{count(group.members_total)}</text>
                <title>{`${group.label}: ${plural(group.members_total, 'member')} (${group.rule_id}). A group, not provenance.`}</title>
              </g>
            ))}
            {data.open_ends.map((end, index) => {
              const at = layout.nodes[end.at];
              if (!at) return null;
              const side = end.kind.endsWith('mention') ? downSide : upSide;
              const start = sidePoint(at, side);
              const stub = sidePoint(at, side, 34);
              const shift = (index % 3) * 14 - 14;
              const tip = vertical ? {x: stub.x + shift, y: stub.y} : {x: stub.x, y: stub.y + shift};
              return (
                <g key={end.edge_id} data-lineage-hit style={{cursor: 'pointer'}} onClick={(event) => { event.stopPropagation(); onSelect(`edge:${end.edge_id}`); }}>
                  <path d={`M ${start.x} ${start.y} L ${tip.x} ${tip.y}`} stroke="var(--ix-ink-3)" strokeWidth={1.3} strokeDasharray="3 3" />
                  <circle cx={tip.x} cy={tip.y} r={7} fill="var(--ix-panel)" stroke="var(--ix-ink-3)" strokeDasharray="2 2" />
                  <text x={tip.x} y={tip.y + 3.5} fontSize={10} textAnchor="middle" fill="var(--ix-ink-2)">?</text>
                  <title>{`Names ${end.named}, which is ${openEndWords(end.reason)}: the line is recorded, its other end is not drawn.`}</title>
                </g>
              );
            })}
            {(layer?.relations ?? []).filter((relation) => relation.pending || !lineEdges.some((edge) => edge.id === relation.key)).map((relation) => {
              const from = layout.nodes[relation.source];
              const to = layout.nodes[relation.target];
              if (!from || !to) return null;
              const start = {x: from.x, y: from.y};
              const end = {x: to.x, y: to.y};
              const bend = vertical ? {x: Math.max(start.x, end.x) + 70, y: (start.y + end.y) / 2} : {x: (start.x + end.x) / 2, y: Math.max(start.y, end.y) + 60};
              const path = `M ${start.x} ${start.y} Q ${bend.x} ${bend.y} ${end.x} ${end.y}`;
              const middle = {x: 0.25 * start.x + 0.5 * bend.x + 0.25 * end.x, y: 0.25 * start.y + 0.5 * bend.y + 0.25 * end.y};
              const chosen = selected === relation.key;
              return (
                <g key={relation.key} data-lineage-hit style={{cursor: 'pointer'}} onClick={(event) => { event.stopPropagation(); onSelect(relation.key); }}>
                  <path d={path} fill="none" stroke="var(--ix-human)" strokeWidth={chosen ? 2.6 : 1.6} strokeDasharray={relation.pending ? '2 4' : '7 4'}
                    markerEnd={relation.relation === 'derives' ? 'url(#ix-arrow-human)' : undefined} opacity={0.9} />
                  <path d={path} fill="none" stroke="transparent" strokeWidth={12} style={{pointerEvents: 'stroke'}} />
                  <g transform={`translate(${middle.x} ${middle.y})`}>
                    <circle r={8} fill="var(--ix-human-soft)" stroke="var(--ix-human)" />
                    <circle cy={-2.5} r={2.2} fill="var(--ix-human)" />
                    <path d="M -4 4.5 a 4 3.4 0 0 1 8 0" fill="var(--ix-human)" />
                  </g>
                  <title>{`Proposed by a person${relation.pending ? ' (a draft, not saved)' : ''}: ${relation.source_name} ${RELATION_WORDS[relation.relation]} ${relation.target_name}. A statement, never provenance the run asserts.`}</title>
                </g>
              );
            })}
            {humanMembers.map(({group, node}) => {
              const from = layout.nodes[node];
              const pill = humanPillByKey.get(group.key);
              if (!from || !pill) return null;
              const start = {x: from.x + from.width / 2, y: from.y};
              const end = {x: pill.x, y: pill.y + PILL_H / 2};
              const mid = (start.x + end.x) / 2;
              return (
                <path key={`${group.key}:${node}`} d={`M ${start.x} ${start.y} C ${mid} ${start.y} ${mid} ${end.y} ${end.x} ${end.y}`}
                  fill="none" stroke="var(--ix-human)" strokeWidth={selected === group.key ? 1.6 : 1} strokeDasharray="1.5 3"
                  opacity={selected === group.key || hover === group.key ? 1 : 0.55} />
              );
            })}
            {humanPills.map(({group, x, y}) => (
              <g key={group.key} transform={`translate(${x} ${y})`} data-lineage-hit style={{cursor: 'pointer'}}
                onClick={(event) => { event.stopPropagation(); if (group.statement) onSelect(group.key); }}
                onPointerEnter={() => setHover(group.key)} onPointerLeave={() => setHover('')}>
                <rect width={PILL_W} height={PILL_H} rx={PILL_H / 2} fill="var(--ix-human-soft)"
                  stroke="var(--ix-human)" strokeWidth={selected === group.key ? 2 : 1} strokeDasharray={group.pending ? '4 3' : undefined} />
                <text x={12} y={17} fontSize={11} fill="var(--ix-human)" fontStyle="italic">{clip(group.name, 24)}</text>
                <text x={PILL_W - 8} y={17} fontSize={10.5} fill="var(--ix-human)" textAnchor="end">{count(group.members.length)}</text>
                <title>{`Your topic ${group.name}: ${plural(group.members.length, 'member')}${group.pending ? ', not saved' : ''}. A statement of yours, not a grouping the run read.`}</title>
              </g>
            ))}
            {nodes.map((node) => {
              const placed = layout.nodes[node.id];
              if (!placed) return null;
              return (
                <NodeMark key={node.id} node={node} placed={placed} name={labels[node.id] ?? names.get(node.id) ?? node.name}
                  human={Boolean(labels[node.id])} selected={selected === node.id} hovered={hover === node.id}
                  collapsed={hidden.has(node.id)} upSide={upSide} downSide={downSide} sidePoint={sidePoint}
                  onSelect={() => onSelect(node.id)} onRefocus={() => (node.kind === 'aggregate' ? onOpen(node.id) : onRefocus(node.id))}
                  onExpand={() => onExpand(node.id)} onToggleCollapse={() => onToggleCollapse(node.id)}
                  onHover={(on) => setHover(on ? node.id : '')}
                  planned={moves?.get(node.id) ?? null}
                  setAside={layer?.excluded.get(node.id)?.reason ?? (node.folder_id ? layer?.excluded.get(node.folder_id)?.reason : undefined) ?? null}
                  onDragStart={node.kind === 'file' && onStageMove ? (clientX, clientY) => {
                    const at = toLayout(clientX, clientY);
                    setDragging({id: node.id, x: at.x, y: at.y, moved: false});
                  } : undefined} />
              );
            })}
            {dragging && dragging.moved ? (
              <g pointerEvents="none">
                <rect x={dragging.x - NODE_W / 2} y={dragging.y - NODE_H / 2} width={NODE_W} height={NODE_H} rx={8}
                  fill="var(--ix-human-soft)" stroke="var(--ix-human)" strokeDasharray="5 3" opacity={0.85} />
                <text x={dragging.x - NODE_W / 2 + 10} y={dragging.y + 4} fontSize={11.5} fill="var(--ix-human)">
                  {clip(`move ${byId.get(dragging.id)?.name ?? ''} in the plan`, 30)}
                </text>
              </g>
            ) : null}
          </g>
        ) : null}
      </svg>
      {layoutFailed ? (
        <div className="ix-state" role="alert"><div><h2>This lineage could not be laid out</h2><p>{layoutFailed}. {layout ? 'The drawing is the one before; ' : ''}the table lists every file and line.</p></div></div>
      ) : !layout ? (
        <div className="ix-state" role="status"><div><div className="ix-spin" aria-hidden="true" /><h2>Laying out the lineage</h2></div></div>
      ) : null}
      <div className="ix-zoom">
        <button type="button" className="ix-btn ix-quiet" onClick={() => fit()} title="Fit the whole lineage (F)">Fit</button>
        <button type="button" className="ix-btn ix-quiet" disabled={!selected || !layout?.nodes[selected]}
          onClick={() => fit([selected, ...lineEdges.filter((edge) => edge.source === selected || edge.target === selected).flatMap((edge) => [edge.source, edge.target])])}
          title="Fit the selection and its neighbors">Fit selection</button>
      </div>
      <div className="ix-sr" aria-live="polite">{announce}</div>
    </div>
  );
}

function NodeMark({node, placed, name, human, selected, hovered, collapsed, upSide, downSide, sidePoint, onSelect, onRefocus, onExpand, onToggleCollapse, onHover, planned, onDragStart, setAside}: {
  node: LineageNode;
  placed: {x: number; y: number; width: number; height: number};
  name: string;
  human: boolean;
  selected: boolean;
  hovered: boolean;
  collapsed: boolean;
  upSide: string;
  downSide: string;
  sidePoint(placed: {x: number; y: number; width: number; height: number}, side: string, offset?: number): {x: number; y: number};
  onSelect(): void;
  onRefocus(): void;
  onExpand(): void;
  onToggleCollapse(): void;
  onHover(on: boolean): void;
  planned: {to_name: string; from_name: string} | null;
  /** Why a person set it (or its folder) aside, where one did. */
  setAside?: string | null;
  onDragStart?(clientX: number, clientY: number): void;
}): ReactElement {
  const left = placed.x - placed.width / 2;
  const top = placed.y - placed.height / 2;
  const aggregate = node.kind === 'aggregate';
  const folderNode = node.kind === 'folder';
  const colour = KIND_VAR[node.file_kind] ?? KIND_VAR.other;
  const stroke = selected ? 'var(--ix-focus)' : planned ? 'var(--ix-human)' : node.is_focus ? 'var(--ix-ink)' : hovered ? 'var(--ix-ink-3)' : 'var(--ix-rule-strong)';
  const secondary = aggregate
    ? `${Object.entries(node.member_kinds).map(([kind, held]) => `${held} ${kind}`).join(' · ')}`
    : folderNode
      ? `${plural(node.members, 'file')} with provenance`
      : [node.stage && node.stage !== 'none' ? `${node.stage} (reading)` : '', node.exam === 'read' ? 'content read' : node.exam === 'elsewhere' ? 'archive link' : 'name only'].filter(Boolean).join(' · ');
  const expander = (side: string, hiddenCount: number) => {
    if (!hiddenCount) return null;
    const point = sidePoint(placed, side, 11);
    return (
      <g transform={`translate(${point.x} ${point.y})`} data-lineage-hit style={{cursor: 'pointer'}}
        onClick={(event) => { event.stopPropagation(); onExpand(); }}>
        <rect x={-17} y={-9} width={34} height={18} rx={9} fill="var(--ix-raised)" stroke="var(--ix-ink-3)" strokeDasharray="2 2" />
        <text x={0} y={4} fontSize={10.5} textAnchor="middle" fill="var(--ix-ink-2)">+{hiddenCount}</text>
        <title>{`${plural(hiddenCount, 'line')} ${side === upSide ? 'upstream' : 'downstream'} not drawn: expand this branch`}</title>
      </g>
    );
  };
  return (
    <g data-lineage-hit style={{cursor: onDragStart ? 'grab' : 'pointer'}} opacity={setAside ? 0.62 : undefined} onClick={(event) => { event.stopPropagation(); onSelect(); }}
      onPointerDown={(event) => { if (onDragStart && event.button === 0) onDragStart(event.clientX, event.clientY); }}
      onDoubleClick={(event) => { event.stopPropagation(); onRefocus(); }}
      onPointerEnter={() => onHover(true)} onPointerLeave={() => onHover(false)}>
      {aggregate ? (
        <>
          <rect x={left + 6} y={top - 6} width={placed.width} height={placed.height} rx={8} fill="var(--ix-raised)" stroke="var(--ix-rule-strong)" />
          <rect x={left + 3} y={top - 3} width={placed.width} height={placed.height} rx={8} fill="var(--ix-raised)" stroke="var(--ix-rule-strong)" />
        </>
      ) : null}
      <rect x={left} y={top} width={placed.width} height={placed.height} rx={8} fill={node.is_focus ? 'var(--ix-track)' : 'var(--ix-raised)'}
        stroke={stroke} strokeWidth={selected || node.is_focus || planned ? 2 : 1} strokeDasharray={collapsed || planned ? '4 3' : undefined} />
      {folderNode ? (
        <path d={`M ${left + 10} ${top + 12} h 6 l 2 2 h 8 v 12 h -16 z`} fill="var(--ix-track)" stroke="var(--ix-ink-2)" />
      ) : (
        <g transform={`translate(${left + 9} ${top + 9})`}>
          <path d="M0.5 0.5h9l3.5 3.5v19h-12.5z" fill="var(--ix-raised)" stroke="var(--ix-ink-3)" strokeDasharray={node.file_kind === 'unknown' ? '2 1.5' : undefined} />
          <rect x={0.5} y={15} width={12.5} height={8.5} fill={colour} />
          {node.link ? <path d="M2 7a2 2 0 0 1 2-2h2M11 7a2 2 0 0 1-2 2H7" stroke="var(--ix-ink)" fill="none" strokeWidth={1.1} /> : null}
        </g>
      )}
      <text x={left + 30} y={top + 17} fontSize={12} fill={human ? 'var(--ix-human)' : 'var(--ix-ink)'} fontStyle={human ? 'italic' : undefined} fontWeight={node.is_focus ? 700 : 500}>
        {clip(name, 22)}
      </text>
      <text x={left + 30} y={top + 32} fontSize={10.5} fill={planned ? 'var(--ix-human)' : 'var(--ix-ink-3)'}>
        {planned
          ? clip(`in the plan, from ${planned.from_name}`, 28)
          : clip(aggregate ? secondary : `${extensionLabel(node.extension)}${secondary ? ` · ${secondary}` : ''}`, 28)}
      </text>
      {setAside ? (
        <path d={`M ${left + 4} ${top + placed.height - 4} L ${left + placed.width - 4} ${top + 4}`} stroke="var(--ix-human)" strokeWidth={1.4} />
      ) : null}
      <title>{`${node.kind === 'aggregate' ? node.name : node.path}${human ? ` (labeled; named ${node.name})` : ''}${setAside ? `. Set aside by you: ${setAside}` : ''}`}</title>
      {expander(upSide, node.hidden_up)}
      {expander(downSide, node.hidden_down)}
      {!node.is_focus && !aggregate ? (
        <g transform={`translate(${left + placed.width - 12} ${top + 10})`} data-lineage-hit style={{cursor: 'pointer'}}
          onClick={(event) => { event.stopPropagation(); onToggleCollapse(); }}>
          <circle r={7} fill="var(--ix-panel)" stroke="var(--ix-rule-strong)" />
          <text y={3.5} fontSize={11} textAnchor="middle" fill="var(--ix-ink-2)">{collapsed ? '+' : '−'}</text>
          <title>{collapsed ? 'Show the branch beyond this file' : 'Collapse the branch beyond this file'}</title>
        </g>
      ) : null}
    </g>
  );
}
