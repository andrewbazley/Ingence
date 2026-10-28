// Timeline: relationships on a true time axis. Two readings, one axis:
//
//   thread    a file's lineage, each file at its time under the chosen source, lines between them
//             across time; a line that runs backwards in time is marked, never hidden
//   activity  a folder's files counted over time, one row per child folder or experiment unit,
//             bars on one shared scale, a folder's own named dates and bulk copies as context
//
// Every mark says its source and how precisely it is known: a dot is an instant (filled for a
// rule's earliest credible time or a content clock, hollow for a filesystem time), a bar is a day
// or a span, dashed where the date was read from a name (context, never a creation time). What
// has no time from the chosen source is in the undated lane; the project's runs have their own
// lane, the one read filled.

import {useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactElement} from 'react';

import type {TimelineResponse} from '../../api';
import {clip, count, plural} from '../../format';
import {KIND_VAR} from '../../icons';
import {layoutInWorker} from '../lineage/layout';
import {EDGE_WORDS} from '../lineage/LineageView';
import {AXIS_ZONE, longTime, parseTime, periodLabel, ticks, timeScale, type TimeScale} from './time';
import {panPixels, useWheel, zoomStep} from '../../wheel';

export interface TimeWindow {
  start: number;
  end: number;
}

interface TimelineViewProps {
  data: TimelineResponse;
  selected: string;
  labels: Record<string, string>;
  onSelect(id: string): void;
  onRefocus(id: string): void;
  onWindow(window: TimeWindow | null): void;
  onPickBin(track: number, bin: number): void;
  selectedBin: {track: number; bin: number} | null;
}

const AXIS_H = 34;
const RUNS_H = 34;
const UNDATED_H = 40;
const ROW_H = 34;
const SOLID_SOURCES = new Set(['earliest', 'event']);

const EDGE_STYLE: Record<string, {stroke: string; width: number; dash?: string}> = {
  published_derivation: {stroke: 'var(--ix-yes)', width: 1.8},
  published_mention: {stroke: 'var(--ix-ink-2)', width: 1.3, dash: '7 3 1.5 3'},
  proposed_derivation: {stroke: 'var(--ix-proposal)', width: 1.4, dash: '5 4'},
  proposed_mention: {stroke: 'var(--ix-proposal)', width: 1.1, dash: '2 4'},
};

function useBox(element: HTMLElement | null): {width: number; height: number} {
  const [box, setBox] = useState({width: 900, height: 560});
  useEffect(() => {
    if (!element) return undefined;
    const observer = new ResizeObserver((entries) => {
      const found = entries[0]?.contentRect;
      if (found) setBox({width: Math.max(280, found.width), height: Math.max(240, found.height)});
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  return box;
}

function domainOf(data: TimelineResponse): TimeWindow | null {
  const range = data.window ?? data.extent;
  const start = parseTime(range?.start);
  const end = parseTime(range?.end);
  if (start === null || end === null) return null;
  const pad = Math.max(3_600_000, (end - start) * 0.03);
  // More room after the last mark, where its label is written.
  return {start: start - pad, end: end + pad * 4};
}

export function TimelineView(props: TimelineViewProps): ReactElement {
  const {data, onWindow} = props;
  const [wrap, setWrap] = useState<HTMLDivElement | null>(null);
  const box = useBox(wrap);
  const narrow = box.width < 640;
  const gutter = narrow ? 104 : 196;
  const base = useMemo(() => domainOf(data), [data]);
  const [domain, setDomain] = useState<TimeWindow | null>(base);
  const [brush, setBrush] = useState<{from: number; to: number} | null>(null);
  const drag = useRef<{x: number; domain: TimeWindow; brushing: boolean} | null>(null);
  useEffect(() => setDomain(base), [base]);
  const scale = useMemo(
    () => (domain ? timeScale(domain.start, domain.end, gutter, box.width - 16) : null),
    [domain, gutter, box.width],
  );

  // Ctrl + wheel (a trackpad pinch) zooms around the pointer, a sideways wheel pans, and any other
  // wheel scrolls the page (../../wheel). Each step reads the span as it is by then, so the many
  // small steps of a pinch add up.
  useWheel(wrap, (event, intent) => {
    if (!scale || !domain || !wrap) return false;
    const x = event.clientX - wrap.getBoundingClientRect().left;
    const width = Math.max(1, scale.right - scale.left);
    if (intent === 'pan') {
      const moved = panPixels(event) / width;
      setDomain((current) => {
        if (!current) return current;
        const shift = moved * (current.end - current.start);
        return {start: current.start + shift, end: current.end + shift};
      });
      return true;
    }
    const factor = 1 / zoomStep(event);
    setDomain((current) => {
      if (!current) return current;
      const at = current.start + ((x - scale.left) / width) * (current.end - current.start);
      const start = at - (at - current.start) * factor;
      const end = at + (current.end - at) * factor;
      return end - start < 60_000 ? current : {start, end};
    });
    return true;
  });
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!domain || (event.target as Element).closest('[data-timeline-hit]')) return;
    const bounds = wrap?.getBoundingClientRect();
    if (!bounds) return;
    drag.current = {x: event.clientX, domain, brushing: event.shiftKey};
    if (event.shiftKey && scale) {
      const at = scale.time(event.clientX - bounds.left);
      setBrush({from: at, to: at});
    }
    (event.currentTarget as HTMLDivElement).setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const held = drag.current;
    if (!held || !scale) return;
    const bounds = wrap?.getBoundingClientRect();
    if (!bounds) return;
    if (held.brushing) {
      setBrush((current) => (current ? {...current, to: scale.time(event.clientX - bounds.left)} : current));
      return;
    }
    const span = held.domain.end - held.domain.start;
    const shift = ((event.clientX - held.x) / (scale.right - scale.left)) * span;
    setDomain({start: held.domain.start - shift, end: held.domain.end - shift});
  };
  const onPointerUp = () => {
    const held = drag.current;
    drag.current = null;
    if (held?.brushing && brush) {
      const from = Math.min(brush.from, brush.to);
      const to = Math.max(brush.from, brush.to);
      setBrush(null);
      if (to - from > 60_000) onWindow({start: from, end: to});
    }
  };

  // Keyboard: in a thread, left and right through the files by time, up and down across lanes,
  // Enter to refocus; in activity, up and down through the rows, left and right through a row's
  // bins. F fits the whole span; Escape clears a window.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    let handled = true;
    if (event.key === 'f') setDomain(base);
    else if (event.key === 'Escape') onWindow(null);
    else if (data.reading === 'thread' && data.thread) {
      const placed = data.thread.nodes
        .filter((node) => node.placed)
        .map((node) => ({id: node.id, at: parseTime(node.placed?.at) ?? 0}))
        .sort((a, b) => a.at - b.at);
      const here = placed.findIndex((item) => item.id === props.selected);
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') props.onSelect(placed[Math.min(placed.length - 1, here + 1)]?.id ?? '');
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') props.onSelect(placed[Math.max(0, here - 1)]?.id ?? '');
      else if (event.key === 'Enter' && props.selected) props.onRefocus(props.selected);
      else handled = false;
    } else if (data.reading === 'activity' && data.activity) {
      const rows = data.activity.tracks.map((track, index) => ({track, index})).filter(({track}) => track.files > 0);
      const current = props.selectedBin ?? {track: rows[0]?.index ?? 0, bin: -1};
      const grid = data.activity.counts[data.source] ?? [];
      const filled = (track: number) => (grid[track] ?? []).map((value, bin) => (value ? bin : -1)).filter((bin) => bin >= 0);
      const at = rows.findIndex(({index}) => index === current.track);
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        const next = rows[Math.max(0, Math.min(rows.length - 1, at + (event.key === 'ArrowDown' ? 1 : -1)))];
        const bins = next ? filled(next.index) : [];
        if (next && bins.length) props.onPickBin(next.index, bins.reduce((best, bin) => (Math.abs(bin - current.bin) < Math.abs(best - current.bin) ? bin : best), bins[0]));
      } else if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        const bins = filled(current.track);
        const next = event.key === 'ArrowRight' ? bins.find((bin) => bin > current.bin) : [...bins].reverse().find((bin) => bin < current.bin);
        if (next !== undefined) props.onPickBin(current.track, next);
      } else handled = false;
    } else handled = false;
    if (handled) event.preventDefault();
  };

  const axis = scale ? ticks(scale, narrow ? 70 : 96) : [];
  const runs = data.runs.map((run) => ({...run, at: parseTime(run.began)}));

  return (
    <div
      ref={setWrap}
      className="ix-canvas-wrap ix-timeline"
      tabIndex={0}
      role="application"
      aria-roledescription="timeline"
      aria-label={`Timeline, ${data.reading === 'thread' ? 'the dated thread' : 'activity over time'} of ${data.focus.name}, placed by ${data.sources.find((item) => item.key === data.source)?.label ?? data.source}, times in ${AXIS_ZONE}. Arrow keys move between ${data.reading === 'thread' ? 'files in time order, Enter refocuses' : 'rows and their periods'}; F fits; shift-drag chooses a window.`}
      onKeyDown={onKeyDown}
      data-start={domain ? Math.round(domain.start) : undefined}
      data-end={domain ? Math.round(domain.end) : undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    >
      {scale ? (
        <svg width={box.width} height={box.height} style={{position: 'absolute', inset: 0}} aria-hidden="true">
          <defs>
            <pattern id="ix-t-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="5" height="5" fill="var(--ix-track)" />
              <line x1="0" y1="0" x2="0" y2="5" stroke="var(--ix-ink-3)" strokeWidth="1.2" />
            </pattern>
            <marker id="ix-t-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0 0 L10 5 L0 10 z" fill="var(--ix-yes)" />
            </marker>
            <marker id="ix-t-arrow-late" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0 0 L10 5 L0 10 z" fill="var(--ix-problem)" />
            </marker>
          </defs>
          {axis.map((tick) => (
            <g key={tick.time}>
              <line x1={scale.x(tick.time)} x2={scale.x(tick.time)} y1={AXIS_H - 6} y2={box.height - RUNS_H}
                stroke="var(--ix-rule)" strokeWidth={tick.major ? 1 : 0.6} />
              <text x={scale.x(tick.time) + 3} y={AXIS_H - 12} fontSize={10.5} fill={tick.major ? 'var(--ix-ink-2)' : 'var(--ix-ink-3)'}>
                {tick.label}
              </text>
            </g>
          ))}
          <line x1={gutter} x2={box.width - 16} y1={AXIS_H - 6} y2={AXIS_H - 6} stroke="var(--ix-rule-strong)" />
          <text x={10} y={AXIS_H - 12} fontSize={10.5} fill="var(--ix-ink-3)">Times in {AXIS_ZONE}</text>
          {data.reading === 'activity' && data.activity ? (
            <ActivityRows {...props} scale={scale} top={AXIS_H} width={box.width} height={box.height - AXIS_H - RUNS_H} gutter={gutter} />
          ) : null}
          {data.reading === 'thread' && data.thread ? (
            <ThreadLanes {...props} scale={scale} top={AXIS_H} width={box.width} height={box.height - AXIS_H - RUNS_H} gutter={gutter} />
          ) : null}
          <RunsLane runs={runs} scale={scale} top={box.height - RUNS_H} gutter={gutter} width={box.width} />
          {brush ? (
            <rect x={scale.x(Math.min(brush.from, brush.to))} y={AXIS_H - 6} width={Math.abs(scale.x(brush.to) - scale.x(brush.from))}
              height={box.height - AXIS_H - RUNS_H} fill="var(--ix-focus)" fillOpacity={0.12} stroke="var(--ix-focus)" strokeDasharray="4 3" />
          ) : null}
        </svg>
      ) : (
        <div className="ix-state"><div><h2>No time to place</h2><p>Nothing here holds a time from any source in this run.</p></div></div>
      )}
      <div className="ix-zoom ix-zoom-top">
        <button type="button" className="ix-btn ix-quiet" onClick={() => setDomain(base)} title="Show the whole span again">Fit</button>
        {data.window ? (
          <button type="button" className="ix-btn ix-quiet" onClick={() => onWindow(null)} title="Clear the window chosen">Clear window</button>
        ) : (
          <span className="ix-note ix-zoom-hint" style={{alignSelf: 'center', padding: '0 6px'}}>Shift-drag to choose a window</span>
        )}
      </div>
    </div>
  );
}

function RunsLane({runs, scale, top, gutter, width}: {runs: Array<{run_id: string; label: string; at: number | null; is_read: boolean; is_current: boolean}>; scale: TimeScale; top: number; gutter: number; width: number}): ReactElement {
  return (
    <g>
      <line x1={0} x2={width} y1={top + 1} y2={top + 1} stroke="var(--ix-rule)" />
      <text x={10} y={top + 21} fontSize={11} fill="var(--ix-ink-3)">Runs</text>
      {runs.map((run) => {
        if (run.at === null) return null;
        const inside = run.at >= scale.start && run.at <= scale.end;
        const x = inside ? scale.x(run.at) : run.at < scale.start ? gutter + 4 : width - 20;
        return (
          <g key={run.run_id} transform={`translate(${x} ${top + 17})`}>
            {inside ? (
              <path d="M0 -6 L6 0 L0 6 L-6 0 z" fill={run.is_read ? 'var(--ix-ink)' : 'var(--ix-panel)'} stroke="var(--ix-ink-2)" strokeWidth={run.is_current ? 2 : 1} />
            ) : (
              <path d={run.at < scale.start ? 'M6 -5 L0 0 L6 5' : 'M-6 -5 L0 0 L-6 5'} fill="none" stroke="var(--ix-ink-3)" />
            )}
            <title>{`${run.label}${run.is_read ? ' (the run shown)' : ''}${run.is_current ? ' (the current run)' : ''}${inside ? '' : ': outside the span shown'}`}</title>
          </g>
        );
      })}
    </g>
  );
}

function ActivityRows({data, scale, top, width, height, gutter, selected, labels, onSelect, onPickBin, selectedBin}: TimelineViewProps & {scale: TimeScale; top: number; width: number; height: number; gutter: number}): ReactElement {
  const activity = data.activity!;
  const key = data.source;
  const grid = activity.counts[key] ?? [];
  const edges = activity.bins.edges.map((edge) => parseTime(edge) ?? 0);
  const rows = activity.tracks.map((track, index) => ({track, index})).filter(({track}) => track.files > 0);
  const rowH = Math.max(18, Math.min(ROW_H, (height - UNDATED_H) / Math.max(1, rows.length)));
  const max = Math.max(1, ...rows.flatMap(({index}) => grid[index] ?? []));
  const hatched = key === 'name';
  return (
    <g>
      {rows.map(({track, index}, row) => {
        const y = top + 4 + row * rowH;
        const counts = grid[index] ?? [];
        const undated = activity.undated[key]?.[index] ?? 0;
        const outside = activity.outside[key]?.[index] ?? 0;
        const label = labels[track.id] ?? track.name;
        const nameFrom = parseTime(track.name_from);
        const nameTo = parseTime(track.name_to);
        const isSelected = selected === track.id;
        return (
          <g key={track.id}>
            <rect x={0} y={y} width={width} height={rowH} fill={row % 2 ? 'var(--ix-track)' : 'transparent'} fillOpacity={0.35} />
            <g data-timeline-hit style={{cursor: 'pointer'}} onClick={() => onSelect(track.kind === 'folder' ? track.id : '')}>
              {track.unit_status !== 'none' ? <rect x={4} y={y + 5} width={3} height={rowH - 10} fill={track.unit_status === 'conflicting' ? 'var(--ix-problem)' : 'var(--ix-ink-2)'} /> : null}
              <text x={12} y={y + rowH / 2 + 4} fontSize={11.5} fill={labels[track.id] ? 'var(--ix-human)' : track.kind === 'folder' ? 'var(--ix-ink)' : 'var(--ix-ink-3)'}
                fontStyle={labels[track.id] || track.kind !== 'folder' ? 'italic' : undefined} fontWeight={isSelected ? 700 : 400}>
                {clip(label, Math.floor((gutter - 60) / 6.4))}
              </text>
              <text x={gutter - 8} y={y + rowH / 2 + 4} fontSize={10} textAnchor="end" fill="var(--ix-ink-3)">{count(track.files)}</text>
              <title>{`${track.name}: ${plural(track.files, 'file')}${track.unit_status !== 'none' ? `, experiment unit (${track.unit_status})` : ''}`}</title>
            </g>
            {nameFrom !== null ? (
              <g>
                <rect x={scale.x(nameFrom)} y={y + rowH - 6} width={Math.max(4, scale.x((nameTo ?? nameFrom) + 86_400_000) - scale.x(nameFrom))} height={4}
                  fill="none" stroke="var(--ix-ink-3)" strokeDasharray="2 2" />
                <title>{`Named for ${track.name_from}${track.name_to && track.name_to !== track.name_from ? ` to ${track.name_to}` : ''}: a date read from the folder's name, context only`}</title>
              </g>
            ) : null}
            {track.copied_on.map((day) => {
              const at = parseTime(day);
              if (at === null) return null;
              return (
                <g key={day} transform={`translate(${scale.x(at)} ${y + 3})`}>
                  <path d="M-4 0 L4 0 L0 5 z" fill="var(--ix-proposal)" />
                  <title>{`Copied in bulk on ${day}, as a rule read it: the files' times there are a copy's`}</title>
                </g>
              );
            })}
            {counts.map((value, bin) => {
              if (!value) return null;
              const x0 = scale.x(edges[bin]);
              const x1 = scale.x(edges[bin + 1] ?? edges[bin]);
              if (x1 < gutter || x0 > width - 16) return null;
              const h = Math.max(1.5, ((rowH - 8) * value) / max);
              const chosen = selectedBin && selectedBin.track === index && selectedBin.bin === bin;
              return (
                <rect key={bin} data-timeline-hit x={Math.max(gutter, x0 + 0.5)} y={y + rowH - 4 - h} width={Math.max(1, Math.min(x1, width - 16) - Math.max(gutter, x0) - 1)} height={h}
                  fill={hatched ? 'url(#ix-t-hatch)' : 'var(--ix-yes)'} stroke={chosen ? 'var(--ix-focus)' : hatched ? 'var(--ix-ink-3)' : 'none'}
                  strokeWidth={chosen ? 2 : 0.8} strokeDasharray={hatched && !chosen ? '2 1.5' : undefined}
                  style={{cursor: 'pointer'}}
                  onClick={() => onPickBin(index, bin)}>
                  <title>{`${track.name}: ${plural(value, 'file')}, ${periodLabel(edges[bin], activity.bins.unit)} (a ${activity.bins.unit} in ${AXIS_ZONE})`}</title>
                </rect>
              );
            })}
            {outside ? (
              <text x={width - 20} y={y + 12} fontSize={9.5} textAnchor="end" fill="var(--ix-ink-3)">{count(outside)} outside ›</text>
            ) : null}
            {undated ? (
              <g>
                <rect x={width - 14} y={y + 5} width={10} height={rowH - 10} fill="url(#ix-t-hatch)" stroke="var(--ix-ink-3)" strokeWidth={0.6} />
                <title>{`${plural(undated, 'file')} with no time from this source`}</title>
              </g>
            ) : null}
          </g>
        );
      })}
    </g>
  );
}

interface Lane {
  id: string;
  y: number;
}

function ThreadLanes({data, scale, top, width, height, gutter, selected, labels, onSelect, onRefocus}: TimelineViewProps & {scale: TimeScale; top: number; width: number; height: number; gutter: number}): ReactElement {
  const thread = data.thread!;
  const [lanes, setLanes] = useState<Lane[]>([]);
  // Which lane each file takes: a layered layout's order across, so a fork and a join stay apart.
  useEffect(() => {
    let stale = false;
    layoutInWorker({
      direction: 'LR',
      clusters: [],
      nodes: thread.nodes.map((node) => ({id: node.id, width: 60, height: 24, cluster: null})),
      edges: thread.edges.map((edge) => ({id: edge.id, from: edge.flow_from, to: edge.flow_to, weight: 1})),
    }).then((result) => {
      if (stale) return;
      const order = Object.entries(result.nodes).sort((a, b) => a[1].y - b[1].y || a[1].x - b[1].x).map(([id]) => id);
      const ys = [...new Set(order.map((id) => Math.round(result.nodes[id].y)))].sort((a, b) => a - b);
      setLanes(order.map((id) => ({id, y: ys.indexOf(Math.round(result.nodes[id].y))})));
    }).catch(() => {
      // Lanes only keep a fork's branches apart: where no layout can be made, one lane holds all.
      if (!stale) setLanes(thread.nodes.map((node) => ({id: node.id, y: 0})));
    });
    return () => {
      stale = true;
    };
  }, [thread]);
  const laneCount = Math.max(1, ...lanes.map((lane) => lane.y + 1));
  const usable = height - UNDATED_H - 10;
  const laneH = Math.max(22, Math.min(64, usable / laneCount));
  const undatedIds = new Set(thread.undated);
  const undatedList = thread.nodes.filter((node) => undatedIds.has(node.id));
  const position = new Map<string, {x: number; y: number; w: number}>();
  for (const node of thread.nodes) {
    const lane = lanes.find((item) => item.id === node.id);
    if (!node.placed) {
      const index = undatedList.indexOf(node);
      position.set(node.id, {x: gutter + 20 + index * 150, y: top + usable + 26, w: 0});
      continue;
    }
    const at = parseTime(node.placed.at) ?? 0;
    const until = node.placed.until ? parseTime(node.placed.until) ?? at : node.placed.precision === 'day' ? at + 86_400_000 : at;
    position.set(node.id, {x: scale.x(at), y: top + 18 + (lane?.y ?? 0) * laneH, w: Math.max(0, scale.x(until) - scale.x(at))});
  }
  const chosen = thread.nodes.find((node) => node.id === selected);
  // Labels: to the right of their mark, moved above or below where the next mark on the same
  // lane is too close to leave room, so no two names are written over each other.
  const labelShift = new Map<string, number>();
  const byLane = new Map<number, Array<{id: string; x: number}>>();
  for (const node of thread.nodes) {
    const place = position.get(node.id);
    if (!place) continue;
    const key = Math.round(place.y);
    byLane.set(key, [...(byLane.get(key) ?? []), {id: node.id, x: place.x + place.w}]);
  }
  for (const members of byLane.values()) {
    members.sort((a, b) => a.x - b.x);
    let lastEnd = -Infinity;
    let flip = 0;
    members.forEach((member, index) => {
      const next = members[index + 1];
      const room = next ? next.x - member.x : Infinity;
      if (room < 150 || member.x < lastEnd) {
        flip = flip === -1 ? 1 : -1;
        labelShift.set(member.id, flip * 13);
      } else {
        labelShift.set(member.id, 0);
      }
      lastEnd = member.x + 150;
    });
  }
  return (
    <g>
      <line x1={0} x2={width} y1={top + usable + 6} y2={top + usable + 6} stroke="var(--ix-rule)" />
      <text x={10} y={top + usable + 30} fontSize={11} fill="var(--ix-ink-3)">Undated</text>
      {thread.edges.map((edge) => {
        const from = position.get(edge.flow_from);
        const to = position.get(edge.flow_to);
        if (!from || !to) return null;
        const style = EDGE_STYLE[edge.kind] ?? EDGE_STYLE.published_derivation;
        const backwards = edge.kind.endsWith('derivation') && to.x + to.w < from.x - 1;
        const mid = (from.x + to.x) / 2;
        return (
          <path key={edge.id} data-timeline-hit d={`M ${from.x + from.w} ${from.y} C ${mid} ${from.y} ${mid} ${to.y} ${to.x} ${to.y}`} fill="none"
            stroke={backwards ? 'var(--ix-problem)' : style.stroke} strokeWidth={selected === edge.id ? style.width + 1.2 : style.width} strokeDasharray={style.dash}
            markerEnd={edge.kind.endsWith('derivation') ? (backwards ? 'url(#ix-t-arrow-late)' : 'url(#ix-t-arrow)') : undefined}
            style={{cursor: 'pointer', pointerEvents: 'stroke'}} onClick={() => onSelect(`edge:${edge.id}`)}>
            <title>{`${EDGE_WORDS[edge.kind] ?? edge.kind}${backwards ? `. Under this source the output is dated before what it ${edge.kind.startsWith('proposed') ? 'is proposed to be made from (a proposal)' : 'was made from'}: one of the times is not the file's making` : ''}`}</title>
          </path>
        );
      })}
      {chosen ? (() => {
        // Every time the chosen file has, one tick per distinct moment, its sources named together.
        const place = position.get(chosen.id);
        if (!place) return null;
        const byMoment = new Map<number, string[]>();
        for (const mark of chosen.marks) {
          const at = parseTime(mark.at);
          if (at === null) continue;
          const key = Math.round(scale.x(at));
          byMoment.set(key, [...(byMoment.get(key) ?? []), mark.source === 'created' ? 'born' : mark.source]);
        }
        return [...byMoment.entries()].map(([x, sources], index) => (
          <g key={x}>
            <line x1={place.x} x2={x} y1={place.y} y2={place.y} stroke="var(--ix-focus)" strokeOpacity={0.35} />
            <line x1={x} x2={x} y1={place.y - 9} y2={place.y + 9} stroke="var(--ix-focus)" strokeWidth={1.2} />
            <text x={x} y={place.y - 12 - (index % 2) * 11} fontSize={9.5} textAnchor="middle" fill="var(--ix-focus)" className="ix-halo">{sources.join(' · ')}</text>
          </g>
        ));
      })() : null}
      {thread.nodes.map((node) => {
        const place = position.get(node.id);
        if (!place) return null;
        const mark = node.placed;
        const solid = mark ? SOLID_SOURCES.has(mark.source) : false;
        // A time published as conflicting is placed, and ringed in the problem color.
        const disputed = Boolean(mark?.conflicting);
        const name = labels[node.id] ?? node.name;
        const isSelected = selected === node.id;
        const colour = KIND_VAR[node.file_kind] ?? KIND_VAR.other;
        return (
          <g key={node.id} data-timeline-hit style={{cursor: 'pointer'}} onClick={() => onSelect(node.id)} onDoubleClick={() => onRefocus(node.id)}>
            {mark && place.w > 3 ? (
              <rect x={place.x} y={place.y - 5} width={place.w} height={10} rx={5} fill={mark.source === 'name' ? 'var(--ix-panel)' : colour}
                stroke="var(--ix-ink-2)" strokeDasharray={mark.source === 'name' ? '3 2' : undefined} />
            ) : (
              <circle cx={place.x} cy={place.y} r={node.is_focus ? 7 : 5.5} fill={!mark ? 'url(#ix-t-hatch)' : solid ? colour : 'var(--ix-panel)'}
                stroke={mark && !solid ? colour : 'var(--ix-ink-2)'} strokeWidth={!mark || solid ? 1 : 2.2}
                strokeDasharray={mark?.source === 'name' ? '2 2' : undefined} />
            )}
            {disputed ? <circle cx={place.x + place.w / 2} cy={place.y} r={node.is_focus ? 8.5 : 7} fill="none" stroke="var(--ix-problem)" strokeWidth={1.6} strokeDasharray="2 1.5" /> : null}
            {isSelected || node.is_focus ? <circle cx={place.x + place.w / 2} cy={place.y} r={10} fill="none" stroke={isSelected ? 'var(--ix-focus)' : 'var(--ix-ink)'} strokeWidth={isSelected ? 2 : 1.2} /> : null}
            <text x={place.x + place.w + 9} y={place.y + 4 + (labelShift.get(node.id) ?? 0)} fontSize={11} fill={labels[node.id] ? 'var(--ix-human)' : 'var(--ix-ink)'}
              fontStyle={labels[node.id] ? 'italic' : undefined} fontWeight={node.is_focus ? 700 : 400} className="ix-halo">
              {clip(name, 22)}
            </text>
            <title>{`${node.path}\n${mark ? `${mark.source} (${mark.detail ?? 'no detail'}), ${mark.precision}: ${mark.until ? `${mark.at} to ${mark.until}` : mark.precision === 'instant' ? longTime(parseTime(mark.at) ?? 0) : mark.at}${disputed ? '. Published as conflicting: a rule that withdraws it fired, so this time is not settled' : ''}` : 'no time from this source'}`}</title>
          </g>
        );
      })}
    </g>
  );
}
