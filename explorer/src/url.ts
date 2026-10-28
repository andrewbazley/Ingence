// What the address holds, so a reload, a bookmark, back and forward all return to one view:
// the mode, the folder at the center, the selection, the measure, the run and the filters.
// A change of mode or center is a step in the history; a change of selection or measure is not.

export type Mode = 'wheel' | 'lineage' | 'timeline';
/** How Lineage is drawn: name families collapsed, every file, or a family's files as rows. */
export type Draw = 'families' | 'files' | 'grid';
/** How many families are drawn at first; "Draw 2n" doubles it. */
export const FAMILIES_DRAWN = 8;
/** The most the engine draws in one request, and the files a page of a family holds. */
export const FAMILIES_MOST = 1000;
export const GRID_PAGE = 100;
export type MeasureKey = 'exam' | 'stage' | 'prov' | 'kind';

export interface ExplorerState {
  mode: Mode;
  focus: string;
  selected: string;
  measure: MeasureKey;
  size: 'files' | 'bytes';
  rings: number;
  run: string;
  table: boolean;
  /** Lineage: which way, how far, proposals and groups drawn, raw material where. */
  direction: 'up' | 'down' | 'both';
  depth: number;
  proposals: boolean;
  groups: boolean;
  orientation: 'TB' | 'BT' | 'LR' | 'RL';
  /** Lineage: how it is drawn, where a person chose (none: families above 40 files, else files);
   * how many families are drawn; the family opened as a Grid and where its page starts. */
  draw: Draw | '';
  drawn: number;
  family: string;
  page: number;
  /** Lineage: branches expanded one at a time, aggregates opened, branches collapsed. */
  expand: string[];
  open: string[];
  collapsed: string[];
  /** Timeline: which reading, placed by which source, rows by folder, unit or any axis, a window. */
  reading: 'thread' | 'activity';
  source: string;
  by: string;
  from: string;
  to: string;
  /** Wheel: the axes its rings answer, center outward (none: the folder tree). */
  arrange: string[];
  /** Wheel and Timeline: only the files with these values, each `axis=value`. */
  where: string[];
}

export const DEFAULTS: ExplorerState = {
  mode: 'wheel',
  focus: '',
  selected: '',
  measure: 'exam',
  size: 'files',
  rings: 4,
  run: '',
  table: false,
  direction: 'both',
  depth: 3,
  proposals: false,
  groups: false,
  orientation: 'TB',
  draw: '',
  drawn: FAMILIES_DRAWN,
  family: '',
  page: 0,
  expand: [],
  open: [],
  collapsed: [],
  reading: 'activity',
  source: 'best',
  by: 'folders',
  from: '',
  to: '',
  arrange: [],
  where: [],
};

/** An axis key as the engine spells it: a fixed axis, or t. / s. and a theme kind. */
const AXIS = /^(folder|unit|kind|stage|exam|prov|year|month|[ts]\.[a-z][a-z0-9_]{0,40})$/;

const MODES: Mode[] = ['wheel', 'lineage', 'timeline'];
const MEASURES: MeasureKey[] = ['exam', 'stage', 'prov', 'kind'];

export function readState(): ExplorerState {
  const query = new URLSearchParams(location.search);
  const mode = query.get('mode') as Mode | null;
  const measure = query.get('m') as MeasureKey | null;
  const rings = Number(query.get('rings'));
  return {
    mode: mode && MODES.includes(mode) ? mode : DEFAULTS.mode,
    focus: query.get('focus') ?? '',
    selected: query.get('sel') ?? '',
    measure: measure && MEASURES.includes(measure) ? measure : DEFAULTS.measure,
    size: query.get('size') === 'bytes' ? 'bytes' : 'files',
    rings: Number.isInteger(rings) && rings >= 1 && rings <= 8 ? rings : DEFAULTS.rings,
    run: query.get('run') ?? '',
    table: query.get('table') === '1',
    direction: (['up', 'down', 'both'] as const).find((way) => way === query.get('dir')) ?? DEFAULTS.direction,
    depth: Number.isInteger(Number(query.get('depth'))) && Number(query.get('depth')) >= 1 && Number(query.get('depth')) <= 12
      ? Number(query.get('depth')) : DEFAULTS.depth,
    proposals: query.get('prop') === '1',
    groups: query.get('groups') === '1',
    orientation: (['TB', 'BT', 'LR', 'RL'] as const).find((way) => way === query.get('orient')) ?? DEFAULTS.orientation,
    draw: (['families', 'files', 'grid'] as const).find((way) => way === query.get('draw')) ?? DEFAULTS.draw,
    drawn: Number.isInteger(Number(query.get('drawn'))) && Number(query.get('drawn')) >= 1 && Number(query.get('drawn')) <= FAMILIES_MOST
      ? Number(query.get('drawn')) : DEFAULTS.drawn,
    family: query.get('family') ?? '',
    page: Number.isInteger(Number(query.get('page'))) && Number(query.get('page')) >= 0 ? Number(query.get('page')) : 0,
    expand: list(query.get('expand')),
    open: list(query.get('open')),
    collapsed: list(query.get('collapsed')),
    reading: query.get('reading') === 'thread' ? 'thread' : 'activity',
    source: SOURCES.includes(query.get('src') ?? '') ? String(query.get('src')) : DEFAULTS.source,
    by: readBy(query.get('by')),
    from: query.get('from') ?? '',
    to: query.get('to') ?? '',
    arrange: list(query.get('arr')).filter((key) => AXIS.test(key)).slice(0, 4),
    where: query.getAll('w').filter((item) => AXIS.test(item.split('=')[0].split('@')[0]) && item.includes('=')).slice(0, 12),
  };
}

function readBy(text: string | null): string {
  if (!text || text === 'folders') return 'folders';
  if (text === 'units') return 'units';
  return AXIS.test(text) && text !== 'folder' && text !== 'unit' ? text : 'folders';
}

const SOURCES = ['best', 'earliest', 'event', 'modified', 'created', 'name'];

function list(text: string | null): string[] {
  return (text ?? '').split(',').map((part) => part.trim()).filter(Boolean);
}

export function writeState(state: ExplorerState, {push}: {push: boolean}): void {
  const query = new URLSearchParams();
  if (state.mode !== DEFAULTS.mode) query.set('mode', state.mode);
  if (state.focus) query.set('focus', state.focus);
  if (state.selected) query.set('sel', state.selected);
  if (state.measure !== DEFAULTS.measure) query.set('m', state.measure);
  if (state.size !== DEFAULTS.size) query.set('size', state.size);
  if (state.rings !== DEFAULTS.rings) query.set('rings', String(state.rings));
  if (state.run) query.set('run', state.run);
  if (state.table) query.set('table', '1');
  if (state.direction !== DEFAULTS.direction) query.set('dir', state.direction);
  if (state.depth !== DEFAULTS.depth) query.set('depth', String(state.depth));
  if (state.proposals) query.set('prop', '1');
  if (state.groups) query.set('groups', '1');
  if (state.orientation !== DEFAULTS.orientation) query.set('orient', state.orientation);
  if (state.draw) query.set('draw', state.draw);
  if (state.drawn !== DEFAULTS.drawn) query.set('drawn', String(state.drawn));
  if (state.family) query.set('family', state.family);
  if (state.page) query.set('page', String(state.page));
  if (state.expand.length) query.set('expand', state.expand.join(','));
  if (state.open.length) query.set('open', state.open.join(','));
  if (state.collapsed.length) query.set('collapsed', state.collapsed.join(','));
  if (state.mode === 'timeline' && state.reading !== DEFAULTS.reading) query.set('reading', state.reading);
  if (state.source !== DEFAULTS.source) query.set('src', state.source);
  if (state.by !== DEFAULTS.by) query.set('by', state.by);
  if (state.from && state.to) {
    query.set('from', state.from);
    query.set('to', state.to);
  }
  if (state.arrange.length) query.set('arr', state.arrange.join(','));
  for (const item of state.where) query.append('w', item);
  const text = query.toString();
  const target = `${location.pathname}${text ? `?${text}` : ''}#explorer`;
  if (target === `${location.pathname}${location.search}${location.hash}`) return;
  if (push) history.pushState({view: 'explorer'}, '', target);
  else history.replaceState({view: 'explorer'}, '', target);
}
