// The Wheel: a folder at the center, its folders in rings outward, every file beneath counted at
// the perimeter. Canvas for the thousands of marks; SVG over it for what must stay crisp and
// legible (labels, hover, the selection, the keyboard's place). Arrow keys walk the sectors,
// Enter centers on one, and the table beside the canvas says the same in rows.

import {useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactElement} from 'react';

import type {WheelResponse, WheelSector} from '../../api';
import {headline, shortHeadline} from '../../encoding';
import {bytes, clip, compressSiblings, count, fitAmong, percent, plural} from '../../format';
import type {MeasureKey} from '../../url';
import {hitTest, layoutWheel, polar, wedgePath, type Hit, type WheelLayout} from './geometry';
import {makePalette, paintWheel} from './paint';

export type WheelPick = {type: 'sector'; index: number} | {type: 'bin'; index: number};

interface WheelViewProps {
  data: WheelResponse;
  measure: MeasureKey;
  size: 'files' | 'bytes';
  selectedSector: number | null;
  selectedBin: number | null;
  labels: Record<string, string>;
  onPick(pick: WheelPick): void;
  onRecenter(id: string): void;
  /** Into a sector of an arranged Wheel: its values become filters, its folder the center. */
  onDrill(index: number): void;
  /** Entries a person set aside: their sectors are struck through in magenta. */
  excluded?: Map<string, {reason: string; pending: boolean}>;
  /** Staged moves in the plan: a folder dragged onto another folder is added to it. */
  moves?: Map<string, {to_id: string; to_name: string; from_name: string}>;
  onStageMove?(entry: string, folder: string): void;
  onUp(): void;
  onClear(): void;
}

/** Whose label a sector shows: a folder's own, or the folder an arranged sector names. */
export function labelKey(sector: WheelSector): string {
  return sector.entry_id ?? sector.id;
}

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

function useSize(element: HTMLElement | null): {width: number; height: number} {
  const [size, setSize] = useState({width: 0, height: 0});
  useEffect(() => {
    if (!element) return undefined;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) setSize({width: Math.round(box.width), height: Math.round(box.height)});
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  return size;
}

function useThemeTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!query) return undefined;
    const changed = () => setTick((value) => value + 1);
    query.addEventListener('change', changed);
    return () => query.removeEventListener('change', changed);
  }, []);
  return tick;
}

interface Label {
  index: number;
  x: number;
  y: number;
  rotate: number;
  text: string;
  human: boolean;
  /** A sector of files no name states a value for: drawn quieter. */
  none: boolean;
}

function sectorLabels(data: WheelResponse, layout: WheelLayout, labels: Record<string, string>): Label[] {
  const byParent = new Map<string, number[]>();
  data.sectors.forEach((sector, index) => {
    if (index === 0 || sector.parent === null) return;
    const held = byParent.get(sector.parent) ?? [];
    held.push(index);
    byParent.set(sector.parent, held);
  });
  const shown = new Map<number, string>();
  const family = new Map<number, string[]>();
  for (const siblings of byParent.values()) {
    const plain = siblings.filter((index) => data.sectors[index].kind !== 'more' && !labels[labelKey(data.sectors[index])]);
    const compressed = compressSiblings(plain.map((index) => data.sectors[index].name));
    plain.forEach((index, place) => {
      shown.set(index, compressed[place]);
      family.set(index, plain.map((other) => data.sectors[other].name));
    });
  }
  const found: Label[] = [];
  data.sectors.forEach((sector, index) => {
    if (index === 0 || sector.ring > 3) return;
    const shape = layout.sectors[index];
    const middle = (shape.r0 + shape.r1) / 2;
    const arc = (shape.a1 - shape.a0) * 2 * Math.PI * middle;
    if (arc < 13) return;
    const human = Boolean(labels[labelKey(sector)]);
    // A value no name states is said as such, never shortened to its first word.
    const none = sector.kind === 'value' && sector.value === '';
    const text = human ? labels[labelKey(sector)] : none ? `(${sector.name})` : (shown.get(index) ?? sector.name);
    const room = Math.floor((shape.r1 - shape.r0 - 10) / 6.2);
    if (room < 3) return;
    const turn = (shape.a0 + shape.a1) / 2;
    const degrees = turn * 360 - 90;
    const leftHalf = turn > 0.5;
    const [x, y] = polar(layout, leftHalf ? shape.r1 - 5 : shape.r0 + 5, turn);
    // The whole name where it fits; else the word that tells it from its siblings; else the
    // name with its siblings' shared start hidden, clipped.
    let fitted = clip(text, room);
    // A folder's name is told from its siblings' by its distinguishing word; a value's words are
    // a sense or a spelling ("light sheet"), and a single word of it would name something else.
    const named = sector.kind === 'folder' || sector.axis === 'folder' || sector.axis === 'unit';
    if (!human && !none && named) {
      const among = fitAmong(sector.name, family.get(index) ?? [], room);
      fitted = among === sector.name || !among.endsWith('\u2026') ? among : clip(text, room);
    }
    found.push({index, x, y, rotate: leftHalf ? degrees + 180 : degrees, text: fitted, human, none});
  });
  return found;
}

export function WheelView(props: WheelViewProps): ReactElement {
  const {data, measure, size, selectedSector, selectedBin, labels, onPick, onRecenter, onDrill, onUp, onClear, excluded, moves, onStageMove} = props;
  // A folder dragged onto another folder of the plain Wheel: a move in the plan, never on disk.
  const [dragging, setDragging] = useState<{index: number; x0: number; y0: number; x: number; y: number; moved: boolean} | null>(null);
  const dragged = useRef(false);
  const [wrap, setWrap] = useState<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const {width, height} = useSize(wrap);
  const theme = useThemeTick();
  const [hover, setHover] = useState<{hit: Hit; x: number; y: number} | null>(null);
  const [cursor, setCursor] = useState<number>(0);
  const [progress, setProgress] = useState(1);
  const [announce, setAnnounce] = useState('');
  // The keyboard's place is drawn only once the keyboard is in use, never for a pointer.
  const [keyboard, setKeyboard] = useState(false);

  const layout = useMemo(() => (width && height ? layoutWheel(data, width, height, size) : null), [data, width, height, size]);
  const definition = data.measures.find((item) => item.key === measure) ?? data.measures[0];

  // The entrance: a short sweep when the center changes, none when motion is reduced.
  useEffect(() => {
    if (reducedMotion()) {
      setProgress(1);
      return undefined;
    }
    let frame = 0;
    const began = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - began) / 320);
      setProgress(1 - (1 - t) ** 3);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    setProgress(0.001);
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [data.focus.id, data.run_id, arrangedKey(data)]);

  useEffect(() => {
    setCursor(selectedSector ?? 0);
  }, [data, selectedSector]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !layout || !wrap) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const context = canvas.getContext('2d');
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const root = wrap.closest('.ix-root') as HTMLElement | null;
    const palette = makePalette(root ?? wrap, context, ratio);
    paintWheel(context, layout, data, palette, {measure, progress});
  }, [layout, data, measure, progress, theme, width, height, wrap]);

  const labelList = useMemo(() => (layout ? sectorLabels(data, layout, labels) : []), [data, layout, labels]);

  const pointAt = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (!layout || !wrap) return null;
    const box = wrap.getBoundingClientRect();
    const x = event.clientX - box.left;
    const y = event.clientY - box.top;
    return {x, y, hit: hitTest(layout, x, y)};
  }, [layout, wrap]);

  const movable = (index: number) => index > 0 && Boolean(onStageMove) && data.sectors[index]?.kind === 'folder';
  const onMove = (event: PointerEvent<HTMLDivElement>) => {
    const found = pointAt(event);
    setHover(found && found.hit.kind !== 'none' ? found : null);
    if (dragging && found) {
      const moved = dragging.moved || Math.hypot(found.x - dragging.x0, found.y - dragging.y0) > 8;
      setDragging({...dragging, x: found.x, y: found.y, moved});
    }
  };
  const onDown = (event: PointerEvent<HTMLDivElement>) => {
    setKeyboard(false);
    dragged.current = false;
    const found = pointAt(event);
    if (found && found.hit.kind === 'sector' && movable(found.hit.index) && event.button === 0) {
      setDragging({index: found.hit.index, x0: found.x, y0: found.y, x: found.x, y: found.y, moved: false});
    }
  };
  const onUpPointer = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const found = pointAt(event);
    if (dragging.moved) {
      dragged.current = true;
      const target = found && found.hit.kind === 'sector' ? found.hit.index : found && found.hit.kind === 'centre' ? 0 : -1;
      const source = data.sectors[dragging.index];
      const into = target >= 0 ? data.sectors[target] : undefined;
      if (into && into.kind === 'folder' && into.id !== source.id && into.id !== source.parent) onStageMove?.(source.id, into.id);
    }
    setDragging(null);
  };
  const onClick = (event: PointerEvent<HTMLDivElement>) => {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    const found = pointAt(event);
    if (!found) return;
    if (found.hit.kind === 'sector') {
      setCursor(found.hit.index);
      onPick({type: 'sector', index: found.hit.index});
    } else if (found.hit.kind === 'bin') {
      onPick({type: 'bin', index: found.hit.index});
    } else if (found.hit.kind === 'centre') {
      setCursor(0);
      onPick({type: 'sector', index: 0});
    } else {
      onClear();
    }
  };
  const onDoubleClick = (event: PointerEvent<HTMLDivElement>) => {
    const found = pointAt(event);
    if (!found) return;
    if (found.hit.kind === 'sector' && data.sectors[found.hit.index].kind === 'folder') {
      onRecenter(data.sectors[found.hit.index].id);
    } else if (found.hit.kind === 'sector' && data.sectors[found.hit.index].kind === 'value') {
      onDrill(found.hit.index);
    } else if (found.hit.kind === 'centre') {
      onUp();
    }
  };

  const describe = useCallback((index: number) => {
    const sector = data.sectors[index];
    const counts = sector.counts[definition.key] ?? [];
    const unit = sector.experiment && sector.experiment.status !== 'none' ? `, experiment unit (${sector.experiment.status})` : '';
    const axis = sector.axis ? data.arrangement?.axes.find((item) => item.key === sector.axis)?.label : undefined;
    const said = labels[labelKey(sector)] ? `${labels[labelKey(sector)]} (labeled; named ${sector.name})` : sector.name;
    return `${axis ? `${axis}: ` : ''}${said}: ${plural(sector.files, 'file')}, ${headline(definition, counts, sector.files)}${unit}`;
  }, [data, definition, labels]);

  const move = (next: number) => {
    setCursor(next);
    setAnnounce(describe(next));
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!layout) return;
    setKeyboard(true);
    const sector = data.sectors[cursor];
    const ring = sector?.ring ?? 0;
    const list = layout.byRing[ring] ?? [];
    const place = list.indexOf(cursor);
    let handled = true;
    switch (event.key) {
      case 'ArrowRight':
        if (list.length) move(list[(place + 1) % list.length]);
        break;
      case 'ArrowLeft':
        if (list.length) move(list[(place - 1 + list.length) % list.length]);
        break;
      case 'ArrowDown': {
        const kids = (layout.byRing[ring + 1] ?? []).filter((index) => data.sectors[index].parent === sector.id);
        if (kids.length) move(kids[0]);
        break;
      }
      case 'ArrowUp': {
        const parent = data.sectors.findIndex((item) => item.id === sector.parent);
        if (parent >= 0) move(parent);
        break;
      }
      case 'Home':
        move(0);
        break;
      case 'Enter':
        if (sector.kind === 'folder' && cursor !== 0) onRecenter(sector.id);
        else if (sector.kind === 'value') onDrill(cursor);
        else onPick({type: 'sector', index: cursor});
        break;
      case ' ':
        onPick({type: 'sector', index: cursor});
        break;
      case 'Backspace':
        onUp();
        break;
      case 'Escape':
        onClear();
        break;
      default:
        handled = false;
    }
    if (handled) event.preventDefault();
  };

  const outline = (index: number | null) => {
    if (index === null || !layout) return null;
    const shape = layout.sectors[index];
    if (!shape) return null;
    if (index === 0) return wedgePath(layout, 0, Math.max(layout.centre - 1, shape.r1 - 0.5), 0, 1);
    return wedgePath(layout, shape.r0 + 0.5, shape.r1 - 0.5, shape.a0, shape.a1);
  };
  const binOutline = (index: number | null) => {
    if (index === null || !layout) return null;
    const shape = layout.bins.find((item) => item.index === index);
    if (!shape) return null;
    return wedgePath(layout, layout.kindBand.inner - 1, layout.skyline.outer + 1, shape.a0, Math.max(shape.a1, shape.a0 + 0.0015));
  };

  const focus = data.sectors[0];
  const focusCounts = focus?.counts[definition.key] ?? [];
  const focusName = labels[data.focus.id] ?? data.focus.name;
  const hovered = hover?.hit;

  return (
    <div
      ref={setWrap}
      className="ix-canvas-wrap ix-fade"
      key={`${data.focus.id}:${data.run_id}:${arrangedKey(data)}`}
      tabIndex={0}
      role="application"
      aria-roledescription="wheel"
      aria-label={data.arrangement
        ? `Wheel of ${focusName} arranged by ${data.arrangement.axes.map((axis) => axis.label).join(', then ')}: ${plural(data.totals.files, 'file')}. Arrow keys move between sectors, Enter looks inside one, Backspace goes up, A arranges, T shows the table.`
        : `Wheel of ${focusName}: ${plural(data.totals.files, 'file')} in ${plural(data.totals.folders, 'folder')}. Arrow keys move between folders, Enter centers on one, Backspace goes up, A arranges, T shows the table.`}
      onPointerMove={onMove}
      onPointerLeave={() => setHover(null)}
      onPointerDown={onDown}
      onPointerUp={onUpPointer}
      onBlur={() => setKeyboard(false)}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onKeyDown={onKeyDown}
      onFocus={() => setAnnounce(describe(cursor))}
    >
      <canvas ref={canvasRef} aria-hidden="true" />
      {layout ? (
        <svg className="ix-overlay" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
          {labelList.map((label) => (
            <text
              key={label.index}
              x={label.x}
              y={label.y}
              transform={`rotate(${label.rotate} ${label.x} ${label.y})`}
              dominantBaseline="central"
              fontSize="11"
              className="ix-halo"
              fontStyle={label.human || label.none ? 'italic' : undefined}
              fill={label.human ? 'var(--ix-human)' : label.none ? 'var(--ix-ink-3)' : undefined}
            >
              {label.text}
            </text>
          ))}
          <defs>
            <pattern id="ix-set-aside" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="7" stroke="var(--ix-human)" strokeWidth="1.4" />
            </pattern>
          </defs>
          {excluded && excluded.size ? data.sectors.map((sector, index) => (
            index > 0 && excluded.has(labelKey(sector)) ? (
              <path key={`aside-${sector.id}`} d={outline(index) ?? ''} fill="url(#ix-set-aside)" stroke="var(--ix-human)" strokeWidth={1} opacity={0.8} />
            ) : null
          )) : null}
          {moves && moves.size ? data.sectors.map((sector, index) => (
            index > 0 && moves.has(sector.id) ? (
              <path key={`planned-${sector.id}`} d={outline(index) ?? ''} fill="none" stroke="var(--ix-human)" strokeWidth={2} strokeDasharray="5 3" />
            ) : null
          )) : null}
          {hovered?.kind === 'sector' ? (
            <path d={outline(hovered.index) ?? ''} fill="none" stroke={dragging?.moved ? 'var(--ix-human)' : 'var(--ix-ink)'} strokeWidth={dragging?.moved ? 2.2 : 1.25} />
          ) : null}
          {hovered?.kind === 'bin' ? (
            <path d={binOutline(hovered.index) ?? ''} fill="none" stroke="var(--ix-ink)" strokeWidth={1} />
          ) : null}
          {selectedSector !== null ? (
            <path d={outline(selectedSector) ?? ''} fill="none" stroke="var(--ix-focus)" strokeWidth={2.5} />
          ) : null}
          {selectedBin !== null ? (
            <path d={binOutline(selectedBin) ?? ''} fill="none" stroke="var(--ix-focus)" strokeWidth={2} />
          ) : null}
          {keyboard && cursor !== selectedSector ? (
            <path d={outline(cursor) ?? ''} fill="none" stroke="var(--ix-focus)" strokeWidth={1.5} strokeDasharray="4 3" className="ix-cursor" />
          ) : null}
          <g textAnchor="middle">
            <text x={layout.cx} y={layout.cy - 13} fontSize="12.5" fontWeight="600"
              fontStyle={labels[data.focus.id] ? 'italic' : undefined}
              fill={labels[data.focus.id] ? 'var(--ix-human)' : undefined}>
              {clip(focusName, Math.max(8, Math.floor((layout.centre * 2 - 16) / 7)))}
            </text>
            <text x={layout.cx} y={layout.cy + 3} fontSize="11" fill="var(--ix-ink-2)">
              {plural(data.totals.files, 'file')}
            </text>
            <text x={layout.cx} y={layout.cy + 18} fontSize="10.5" fill="var(--ix-ink-3)">
              {shortHeadline(definition, focusCounts, data.totals.files)}
            </text>
            {data.arrangement ? (
              <text x={layout.cx} y={layout.cy + 32} fontSize="10" fill="var(--ix-ink-3)" fontStyle="italic">
                {clip(`by ${data.arrangement.axes.map((axis) => axis.label.toLowerCase()).filter((word, place, all) => all.indexOf(word) === place).join(', ')}`, Math.max(10, Math.floor((layout.centre * 2 - 16) / 6)))}
              </text>
            ) : null}
          </g>
        </svg>
      ) : null}
      {hover && hover.hit.kind !== 'none' ? (
        <Tip data={data} hit={hover.hit} x={hover.x} y={hover.y} width={width} height={height} measure={measure} labels={labels} excluded={excluded} />
      ) : null}
      {dragging?.moved ? (
        <div className="ix-drag-ghost" style={{left: dragging.x + 12, top: dragging.y + 12}}>
          move {data.sectors[dragging.index]?.name} in the plan
        </div>
      ) : null}
      <div className="ix-sr" aria-live="polite">{announce}</div>
    </div>
  );
}

function arrangedKey(data: WheelResponse): string {
  return data.arrangement ? `${data.arrangement.axes.map((axis) => axis.key).join(',')}|${data.arrangement.where.map((item) => `${item.axis}=${item.value}`).join(',')}` : '';
}

function Tip({data, hit, x, y, width, height, measure, labels, excluded}: {
  data: WheelResponse; hit: Hit; x: number; y: number; width: number; height: number; measure: MeasureKey;
  labels: Record<string, string>; excluded?: Map<string, {reason: string; pending: boolean}>;
}): ReactElement | null {
  const definition = data.measures.find((item) => item.key === measure) ?? data.measures[0];
  let body: ReactElement | null = null;
  if (hit.kind === 'sector' || hit.kind === 'centre') {
    const index = hit.kind === 'centre' ? 0 : hit.index;
    const sector: WheelSector = data.sectors[index];
    const counts = sector.counts[definition.key] ?? [];
    const top = definition.categories
      .map((category, place) => ({label: category.label, value: counts[place] ?? 0}))
      .filter((item) => item.value > 0)
      .sort((a, b) => b.value - a.value)
      .slice(0, 3);
    const label = labels[labelKey(sector)];
    const setAsideReason = excluded?.get(labelKey(sector))?.reason;
    const axis = sector.axis ? data.arrangement?.axes.find((item) => item.key === sector.axis) : undefined;
    const noun = sector.kind === 'more' && sector.axis && sector.axis !== 'folder' ? 'value' : 'folder';
    body = (
      <>
        {axis ? <div className="ix-tip-sub">{axis.label}</div> : null}
        <strong style={label ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>{label ?? sector.name}</strong>
        {label ? <div className="ix-tip-sub">named {sector.name}</div> : null}
        {setAsideReason ? <div className="ix-tip-sub" style={{color: 'var(--ix-human)'}}>set aside by you: {setAsideReason}</div> : null}
        <div className="ix-tip-sub">
          {sector.kind === 'more' ? `${plural(sector.gathered, noun)} gathered here · ` : ''}
          {plural(sector.files, 'file')} · {sector.kind === 'value' ? `in ${plural(sector.folders, 'folder')}` : plural(sector.folders, 'folder')} · {bytes(sector.bytes)}
        </div>
        <div>{top.map((item) => `${item.label} ${percent(item.value, sector.files)}`).join(' · ') || 'no files'}</div>
        {sector.experiment && sector.experiment.status !== 'none' ? (
          <div>experiment unit: {sector.experiment.status}{sector.experiment.unit_kind ? ` (${sector.experiment.unit_kind})` : ''}</div>
        ) : null}
        {sector.experiment && sector.experiment.units - (sector.experiment.status !== 'none' ? 1 : 0) > 0 ? (
          <div className="ix-tip-sub">{plural(sector.experiment.units - (sector.experiment.status !== 'none' ? 1 : 0), 'unit')} beneath</div>
        ) : null}
      </>
    );
  } else if (hit.kind === 'bin') {
    const {bins} = data;
    const sector = data.sectors[bins.sector[hit.index]];
    const kinds = data.measures.find((item) => item.key === 'kind');
    const kind = kinds?.categories[bins.dominant.kind?.[hit.index] ?? 0];
    const category = definition.categories[bins.dominant[definition.key]?.[hit.index] ?? 0];
    const n = bins.n[hit.index];
    body = (
      <>
        <strong>{plural(n, 'file')} in {labels[labelKey(sector)] ?? sector.name}</strong>
        <div className="ix-tip-sub">
          {n > 1 ? `files ${count(bins.start[hit.index] + 1)} to ${count(bins.start[hit.index] + n)} of ${count(sector.leaf === 'direct' ? sector.direct_files : sector.files)} here` : `file ${count(bins.start[hit.index] + 1)} here`} · {bytes(bins.bytes[hit.index])}
        </div>
        <div>mostly {kind?.label ?? 'unknown'} ({bins.purity.kind?.[hit.index] ?? 0}%)</div>
        <div>{definition.label}: {category?.label ?? 'unknown'} ({bins.purity[definition.key]?.[hit.index] ?? 0}%)</div>
      </>
    );
  }
  if (!body) return null;
  const left = x + 16 + 300 > width ? Math.max(4, x - 316) : x + 16;
  const top = y + 16 + 110 > height ? Math.max(4, y - 120) : y + 16;
  return <div className="ix-tip" style={{left, top}}>{body}</div>;
}
