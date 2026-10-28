// An arranged Wheel's sectors as filters: which files a sector holds, where a drill-down goes,
// and the presets a person starts from. The engine names each value; the page only walks the
// sectors' parents to say which values a sector stands for (see the engine's explorer/arrange).

import type {Filters, WheelResponse} from './api';

export interface Step {
  axis: string;
  /** For a folder axis, which folder level beneath the center (1 for the first), else 0. */
  level: number;
  value: string;
}

/** The values a sector stands for, center outward. Empty for the center and a plain Wheel. */
export function sectorPath(data: WheelResponse, index: number): Step[] {
  const byId = new Map(data.sectors.map((sector, place) => [sector.id, place]));
  const chain: number[] = [];
  let at: number | undefined = index;
  while (at !== undefined && at > 0) {
    chain.unshift(at);
    const parent: string | null = data.sectors[at].parent;
    at = parent === null ? undefined : byId.get(parent);
  }
  const steps: Step[] = [];
  let folders = 0;
  for (const place of chain) {
    const sector = data.sectors[place];
    if (sector.kind !== 'value' || !sector.axis) continue;
    if (sector.axis === 'folder') folders += 1;
    steps.push({axis: sector.axis, level: sector.axis === 'folder' ? folders : 0, value: sector.value ?? ''});
  }
  return steps;
}

export function filterText(step: Step): string {
  return `${step.axis}${step.axis === 'folder' ? `@${step.level}` : ''}=${step.value}`;
}

/**
 * Exactly the files a sector's perimeter holds: its values, and for a ``more`` sector every
 * value but its drawn siblings'; the page's own filters come first.
 */
export function sectorFilters(data: WheelResponse, index: number, where: string[]): Filters {
  const sector = data.sectors[index];
  if (sector.kind === 'more') {
    const parent = data.sectors.findIndex((item) => item.id === sector.parent);
    const base = parent > 0 ? sectorPath(data, parent).map(filterText) : [];
    const siblings = data.sectors.filter((item) => item.parent === sector.parent && item.kind === 'value');
    const levels = parent > 0 ? sectorPath(data, parent).filter((step) => step.axis === 'folder').length : 0;
    const not = siblings.map((item) => filterText({axis: item.axis ?? '', level: item.axis === 'folder' ? levels + 1 : 0, value: item.value ?? ''}));
    return {where: [...where, ...base], not};
  }
  const steps = sectorPath(data, index);
  const filters: Filters = {where: [...where, ...steps.map(filterText)]};
  // A sector whose own files are only some of its files ("direct"): those lying above the next
  // folder level, which the engine spells as an empty folder value.
  if (sector.leaf === 'direct') {
    const axes = data.arrangement?.axes ?? [];
    const next = axes[steps.length];
    if (next?.key === 'folder') {
      const level = steps.filter((step) => step.axis === 'folder').length + 1;
      filters.where = [...(filters.where ?? []), `folder@${level}=`];
    }
  }
  return filters;
}

/** Where a drill-down into a value sector goes: a folder becomes the center, a value a filter. */
export function drillTarget(data: WheelResponse, index: number, where: string[]): {focus: string; where: string[]; arrange: string[]} {
  const steps = sectorPath(data, index);
  const axes = (data.arrangement?.axes ?? []).map((axis) => axis.key);
  let focus = data.focus.id;
  const kept = [...where];
  for (const step of steps) {
    if (step.axis === 'folder' && step.value) {
      focus = step.value;
    } else if (step.axis === 'unit' && step.value) {
      focus = step.value;
      kept.push(filterText(step));
    } else {
      kept.push(filterText(step));
    }
  }
  return {focus, where: Array.from(new Set(kept)), arrange: axes.slice(steps.length)};
}

export interface Preset {
  name: string;
  axes: string[];
  /** What a person would use it for, in a line. */
  why: string;
}

export const PRESETS: Preset[] = [
  {name: 'Folders', axes: [], why: 'The tree as it is stored'},
  {name: 'Technique', axes: ['t.technique', 'folder'], why: 'Which techniques, and where their files are'},
  {name: 'Technique, then condition', axes: ['t.technique', 't.condition'], why: 'What was compared under each technique'},
  {name: 'Spelling of techniques', axes: ['s.technique', 'folder'], why: 'One technique written several ways: where to tidy names'},
  {name: 'Year, then technique', axes: ['year', 't.technique'], why: 'How the work changed over time'},
  {name: 'Experiment units', axes: ['unit', 'stage'], why: 'Each experiment, and how far its files got'},
  {name: 'Stage, then kind', axes: ['stage', 'kind'], why: 'Raw to figures, by format'},
];

export function arrangementWords(labels: Map<string, string>, axes: string[]): string {
  if (!axes.length) return 'Folders';
  const words: string[] = [];
  let run = 0;
  axes.forEach((key, place) => {
    if (key === 'folder') {
      run += 1;
      if (axes[place + 1] === 'folder') return;
      words.push(run === 1 ? 'Folders' : `Folders (${run})`);
      run = 0;
      return;
    }
    words.push(labels.get(key) ?? key);
  });
  return words.join(' › ');
}
