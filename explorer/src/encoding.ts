// How every category is drawn, in one place, so the Wheel, the legend, the inspector and the
// table never disagree. A category is a fill (a theme token) and a texture; "no" is the empty
// track, "unknown" is hatched, "held elsewhere or proposed" is dotted, a problem is the status red.

import type {CSSProperties} from 'react';

import type {MeasureDefinition} from './api';

export type Texture = 'solid' | 'hatch' | 'dots' | 'track';

export interface Paint {
  /** A CSS custom property of .ix-root, read at paint time so each theme gets its own step. */
  token: string;
  texture: Texture;
}

const STAGE_TOKENS: Record<string, string> = {
  acquisition: '--ix-stage-1',
  processing: '--ix-stage-2',
  analysis: '--ix-stage-3',
  summary: '--ix-stage-4',
};

const KIND_TOKENS: Record<string, string> = {
  image: '--ix-kind-image',
  table: '--ix-kind-table',
  text: '--ix-kind-text',
  archive: '--ix-kind-archive',
  array: '--ix-kind-array',
  other: '--ix-kind-other',
};

export function paintFor(measure: string, category: string, level: string): Paint {
  if (measure === 'kind') {
    if (category === 'unknown') return {token: '--ix-ink-3', texture: 'hatch'};
    // A format read in conflict is a problem, never painted as a settled family.
    if (level === 'problem') return {token: '--ix-problem', texture: 'solid'};
    return {token: KIND_TOKENS[category] ?? '--ix-kind-other', texture: 'solid'};
  }
  if (measure === 'stage' && STAGE_TOKENS[category]) {
    return {token: STAGE_TOKENS[category], texture: 'solid'};
  }
  switch (level) {
    case 'yes':
      return {token: '--ix-yes', texture: 'solid'};
    case 'partial':
      return {token: measure === 'stage' ? '--ix-other' : '--ix-partial', texture: 'solid'};
    case 'problem':
      return {token: '--ix-problem', texture: 'solid'};
    case 'elsewhere':
      return {token: measure === 'prov' ? '--ix-proposal' : '--ix-ink-3', texture: 'dots'};
    case 'unknown':
      return {token: '--ix-ink-3', texture: 'hatch'};
    default:
      return {token: '--ix-track', texture: 'track'};
  }
}

/** Each category of a measure, with its paint, in the measure's order. */
export function categoryPaints(measure: MeasureDefinition): Array<{key: string; label: string; paint: Paint; meaning: string; level: string}> {
  return measure.categories.map((category) => ({
    key: category.key,
    label: category.label,
    meaning: category.meaning,
    level: category.level,
    paint: paintFor(measure.key, category.key, category.level),
  }));
}

/** A CSS background for a swatch or a bar segment showing a paint, texture included. */
export function swatchStyle(paint: Paint): CSSProperties {
  const color = `var(${paint.token})`;
  if (paint.texture === 'hatch') {
    return {background: `repeating-linear-gradient(135deg, ${color} 0 2px, var(--ix-panel) 2px 4px)`};
  }
  if (paint.texture === 'dots') {
    return {
      backgroundColor: 'var(--ix-track)',
      backgroundImage: `radial-gradient(${color} 1.2px, transparent 1.4px)`,
      backgroundSize: '4px 4px',
    };
  }
  return {background: color};
}

/** A one-line reading of the headline for a measure over some counts, in plain words. */
export function headline(measure: MeasureDefinition, counts: number[], files: number): string {
  if (!files) return 'no files';
  const by = Object.fromEntries(measure.categories.map((category, index) => [category.key, counts[index] ?? 0]));
  const share = (value: number) => {
    const exact = (100 * value) / files;
    if (value === 0) return '0%';
    if (exact < 1) return '<1%';
    return `${Math.round(exact)}%`;
  };
  switch (measure.key) {
    case 'exam':
      return `${share(by.read ?? 0)} content read${by.elsewhere ? ` · ${share(by.elsewhere)} archive links` : ''}`;
    case 'stage':
      return `${share(files - (by.none ?? 0))} have a stage reading${by.conflicting ? ` · ${share(by.conflicting)} conflict` : ''}`;
    case 'prov': {
      const joined = (by.derivation ?? 0) + (by.reference ?? 0);
      if (!joined && !by.proposed) return 'no provenance recorded';
      return `${share(joined)} joined by published provenance${by.proposed ? ` · ${share(by.proposed)} proposed only` : ''}`;
    }
    default: {
      let best = 0;
      measure.categories.forEach((_, index) => {
        if ((counts[index] ?? 0) > (counts[best] ?? 0)) best = index;
      });
      return `mostly ${measure.categories[best]?.label ?? 'unknown'} (${share(counts[best] ?? 0)})`;
    }
  }
}

/** The center's few words for a measure: short enough for the disc. */
export function shortHeadline(measure: MeasureDefinition, counts: number[], files: number): string {
  if (!files) return 'no files';
  const by = Object.fromEntries(measure.categories.map((category, index) => [category.key, counts[index] ?? 0]));
  const share = (value: number) => (value === 0 ? '0%' : (100 * value) / files < 1 ? '<1%' : `${Math.round((100 * value) / files)}%`);
  switch (measure.key) {
    case 'exam':
      return `${share(by.read ?? 0)} read`;
    case 'stage':
      return `${share(files - (by.none ?? 0))} staged`;
    case 'prov': {
      const joined = (by.derivation ?? 0) + (by.reference ?? 0);
      return joined ? `${share(joined)} joined` : 'none published';
    }
    default: {
      let best = 0;
      measure.categories.forEach((_, index) => {
        if ((counts[index] ?? 0) > (counts[best] ?? 0)) best = index;
      });
      return `${share(counts[best] ?? 0)} ${measure.categories[best]?.key ?? ''}`;
    }
  }
}
