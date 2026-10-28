// The Graph workspace: one selection, one run, one inspector, and the mode that answers the
// question being asked. The address holds all of it, so a reload or back and forward return here.
//
//   Wheel    what is in this folder, how many experiment units, what has been examined
//   Lineage  how did these files lead to this output, where do lineages fork and join
//   Timeline what is the dated provenance of this file, how did a folder's work change over time
//
// The selection survives a change of mode: a file chosen in the Wheel is the focus of its
// lineage; a file in focus in Lineage is shown in its folder's Wheel, still selected.

import {useCallback, useEffect, useMemo, useRef, useState, type ReactElement} from 'react';

import {api, ApiFailure, type FamilyNode, type LineageFamiliesResponse, type LineageResponse, type MeasureDefinition, type TimelineResponse, type VersionResponse, type WheelResponse} from './api';
import {Inspector, selectionFromParam, selectionToParam, type Selection} from './Inspector';
import {WheelLegend} from './Legend';
import {count, plural} from './format';
import {ModeGlyph} from './icons';
import {FamiliesLegend, LineageLegend} from './modes/lineage/LineageLegend';
import {FamiliesPane} from './modes/lineage/FamiliesPane';
import {LoneFile} from './modes/lineage/LoneFile';
import {LineageTable} from './modes/lineage/LineageTable';
import {LineageView, type Orientation} from './modes/lineage/LineageView';
import {TimelineLegend} from './modes/timeline/TimelineLegend';
import {TimelineTable} from './modes/timeline/TimelineTable';
import {TimelineView} from './modes/timeline/TimelineView';
import {longTime} from './modes/timeline/time';
import {WheelTable} from './modes/wheel/WheelTable';
import {WheelView, type WheelPick} from './modes/wheel/WheelView';
import {HumanBar, ReviewPanel} from './HumanUi';
import {ArrangeButton, ArrangePanel, useAxes} from './ArrangeUi';
import {drillTarget} from './arrangement';
import {RELATION_WORDS, useHumanLayer, type Relation} from './human';
import {FAMILIES_MOST, GRID_PAGE, readState, writeState, type Draw, type ExplorerState, type MeasureKey, type Mode} from './url';

const MEASURE_WORDS: Record<MeasureKey, string> = {
  exam: 'Examined',
  stage: 'Stage reading',
  prov: 'Provenance',
  kind: 'Kind',
};

/** Above this many files a lineage is drawn as name families unless a person chose how. */
const FAMILIES_ABOVE = 40;

const DRAW_WORDS: Record<Draw, {label: string; title: string}> = {
  families: {label: 'Families', title: 'Files whose names agree collapsed into one node each, and the lines between the nodes (key D cycles)'},
  files: {label: 'Files', title: 'Every file as a node of its own, around the one in focus (key D cycles)'},
  grid: {label: 'Grid', title: 'A family\'s files as rows: name, folder, lines in and out (key D cycles)'},
};
const NEXT_DRAW: Record<Draw, Draw> = {families: 'files', files: 'grid', grid: 'families'};

const ORIENTATION_WORDS: Record<Orientation, string> = {
  TB: 'Raw at top',
  BT: 'Raw at bottom',
  LR: 'Raw at left',
  RL: 'Raw at right',
};

type Load<T> =
  | {state: 'idle'}
  | {state: 'loading'; slow: boolean}
  | {state: 'ready'; data: T}
  | {state: 'failed'; failure: ApiFailure};

function useFitHeight(element: HTMLElement | null): void {
  useEffect(() => {
    if (!element) return undefined;
    const fit = () => {
      const top = element.getBoundingClientRect().top;
      const room = Math.max(520, window.innerHeight - Math.max(0, top) - 16);
      element.style.setProperty('--ix-height', `${room}px`);
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [element]);
}

function failureOf(failure: unknown): ApiFailure {
  return failure instanceof ApiFailure ? failure : new ApiFailure(0, 'error', String(failure));
}

export function Workspace(): ReactElement {
  const [state, setState] = useState<ExplorerState>(readState);
  const [wheel, setWheel] = useState<Load<WheelResponse>>({state: 'idle'});
  const [shownWheel, setShownWheel] = useState<WheelResponse | null>(null);
  const [lineage, setLineage] = useState<Load<LineageResponse>>({state: 'idle'});
  const [shownLineage, setShownLineage] = useState<LineageResponse | null>(null);
  const [families, setFamilies] = useState<Load<LineageFamiliesResponse>>({state: 'idle'});
  const [shownFamilies, setShownFamilies] = useState<LineageFamiliesResponse | null>(null);
  const [timeline, setTimeline] = useState<Load<TimelineResponse>>({state: 'idle'});
  const [shownTimeline, setShownTimeline] = useState<TimelineResponse | null>(null);
  const [measures, setMeasures] = useState<MeasureDefinition[]>([]);
  const [version, setVersion] = useState<VersionResponse | null>(null);
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  const [layoutMs, setLayoutMs] = useState<number | null>(null);
  const [trail, setTrail] = useState<Array<{id: string; name: string}>>([]);
  // The human layer: a person's labels (saved, and drafts shown at once) and a staged plan.
  const human = useHumanLayer();
  const labels = useMemo(() => {
    const shown: Record<string, string> = {};
    for (const [id, label] of Object.entries(human.labels)) shown[id] = human.pending.has(id) ? `✎ ${label}` : label;
    return shown;
  }, [human.labels, human.pending]);
  const [reviewing, setReviewing] = useState(false);
  const [arranging, setArranging] = useState(false);
  // Proposing a relation: its first end and kind, waiting for the other end to be chosen.
  const [relating, setRelating] = useState<{source: {id: string; name: string}; relation: Relation} | null>(null);
  const [labelStart, setLabelStart] = useState(0);
  useFitHeight(root);

  const update = useCallback((change: Partial<ExplorerState>, push = false) => {
    setState((current) => {
      const next = {...current, ...change};
      writeState(next, {push});
      return next;
    });
  }, []);

  useEffect(() => {
    const onPop = () => {
      if (location.hash === '#explorer') setState(readState());
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  // Arriving with a selection and no center (from another page's "what is being looked at"): a
  // folder becomes the Wheel's center; a file's folder does, with the file kept selected. Only on
  // arriving: back and forward to such an address mean the root with that selection.
  const [arriving, setArriving] = useState(true);
  const resolving = arriving && state.mode === 'wheel' && !state.focus && Boolean(state.selected)
    && !/^(bin|more|edge|group|agg|tbin|v|overlay|family|quotient):/.test(state.selected);
  useEffect(() => {
    if (!resolving) {
      setArriving(false);
      return undefined;
    }
    const controller = new AbortController();
    api.entry(state.selected, state.run || undefined, controller.signal)
      .then((card) => {
        const folder = card.entry_type === 'folder' ? card.id : card.ancestors[card.ancestors.length - 1]?.id;
        update({focus: folder ?? ''});
        setArriving(false);
      })
      .catch((failure: unknown) => {
        if ((failure as Error).name === 'AbortError') return;
        update({selected: ''});
        setArriving(false);
      });
    return () => controller.abort();
  }, [resolving, state.selected, state.run, update]);

  // The Wheel.
  const arrangeKey = state.arrange.join(',');
  const whereKey = state.where.join('\n');
  useEffect(() => {
    if (state.mode !== 'wheel' || resolving) return undefined;
    const controller = new AbortController();
    setWheel({state: 'loading', slow: false});
    const slow = window.setTimeout(() => setWheel((current) => (current.state === 'loading' ? {state: 'loading', slow: true} : current)), 1200);
    api.wheel({
      focus: state.focus || undefined,
      run: state.run || undefined,
      size: state.size,
      rings: state.rings,
      arrange: state.arrange.join(',') || undefined,
      where: state.where.length ? state.where : undefined,
    }, controller.signal)
      .then((data) => {
        setWheel({state: 'ready', data});
        setShownWheel(data);
        setMeasures(data.measures);
      })
      .catch((failure: unknown) => {
        if ((failure as Error).name === 'AbortError') return;
        if (failure instanceof ApiFailure && failure.code === 'not_a_folder' && state.focus) {
          // A file as the center (from Lineage): its folder's Wheel, the file still selected.
          api.entry(state.focus, state.run || undefined).then((card) => {
            const folder = card.ancestors[card.ancestors.length - 1];
            if (folder) update({focus: folder.id, selected: card.id});
          }).catch(() => setWheel({state: 'failed', failure}));
          return;
        }
        setWheel({state: 'failed', failure: failureOf(failure)});
      })
      .finally(() => window.clearTimeout(slow));
    return () => {
      controller.abort();
      window.clearTimeout(slow);
    };
  }, [state.mode, state.focus, state.run, state.size, state.rings, arrangeKey, whereKey, update, resolving]);

  // The lineage.
  const expandKey = state.expand.join(',');
  const openKey = state.open.join(',');
  useEffect(() => {
    if (state.mode !== 'lineage') return undefined;
    if (!state.focus) {
      setLineage({state: 'idle'});
      return undefined;
    }
    const controller = new AbortController();
    setLineage({state: 'loading', slow: false});
    const slow = window.setTimeout(() => setLineage((current) => (current.state === 'loading' ? {state: 'loading', slow: true} : current)), 1200);
    api.lineage({
      focus: state.focus,
      run: state.run || undefined,
      direction: state.direction,
      depth: state.depth,
      proposals: state.proposals,
      groups: state.groups,
      expand: expandKey || undefined,
      open: openKey || undefined,
    }, controller.signal)
      .then((data) => {
        setLineage({state: 'ready', data});
        setShownLineage(data);
      })
      .catch((failure: unknown) => {
        if ((failure as Error).name === 'AbortError') return;
        setLineage({state: 'failed', failure: failureOf(failure)});
      })
      .finally(() => window.clearTimeout(slow));
    return () => {
      controller.abort();
      window.clearTimeout(slow);
    };
  }, [state.mode, state.focus, state.run, state.direction, state.depth, state.proposals, state.groups, expandKey, openKey]);

  // The timeline.
  useEffect(() => {
    if (state.mode !== 'timeline') return undefined;
    const controller = new AbortController();
    setTimeline({state: 'loading', slow: false});
    const slow = window.setTimeout(() => setTimeline((current) => (current.state === 'loading' ? {state: 'loading', slow: true} : current)), 1200);
    api.timeline({
      focus: state.focus || undefined,
      run: state.run || undefined,
      reading: state.reading,
      source: state.source,
      by: state.by,
      where: state.where.length ? state.where : undefined,
      from: state.from || undefined,
      to: state.to || undefined,
      depth: state.depth,
      proposals: state.proposals,
    }, controller.signal)
      .then((data) => {
        setTimeline({state: 'ready', data});
        setShownTimeline(data);
      })
      .catch((failure: unknown) => {
        if ((failure as Error).name === 'AbortError') return;
        if (failure instanceof ApiFailure && failure.code === 'not_a_folder') {
          // A file's activity is its thread.
          update({reading: 'thread'});
          return;
        }
        setTimeline({state: 'failed', failure: failureOf(failure)});
      })
      .finally(() => window.clearTimeout(slow));
    return () => {
      controller.abort();
      window.clearTimeout(slow);
    };
  }, [state.mode, state.focus, state.run, state.reading, state.source, state.by, whereKey, state.from, state.to, state.depth, state.proposals, update]);

  // The measures' words, for an inspector opened before any Wheel.
  useEffect(() => {
    if (measures.length) return undefined;
    const controller = new AbortController();
    api.measures(controller.signal).then((found) => setMeasures(found.measures)).catch(() => {});
    return () => controller.abort();
  }, [measures.length]);

  useEffect(() => {
    let stopped = false;
    const check = () => api.version().then((found) => { if (!stopped) setVersion(found); }).catch(() => {});
    check();
    const timer = window.setInterval(check, 8000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, []);

  const wheelData = wheel.state === 'ready' ? wheel.data : shownWheel;
  const wheelDataRef = useRef<WheelResponse | null>(null);
  wheelDataRef.current = wheelData;
  const axisWords = useMemo(() => new Map((wheelData?.arrangement?.axes ?? []).map((axis) => [axis.key, axis.label])), [wheelData]);
  const whereLabels = useMemo(() => {
    const found = new Map<string, string>();
    for (const item of wheelData?.arrangement?.where ?? []) {
      found.set(`${item.axis}${item.axis === 'folder' ? `@${item.level}` : ''}=${item.value}`, item.label);
    }
    return found;
  }, [wheelData]);
  const lineageData = lineage.state === 'ready' ? lineage.data : shownLineage;
  // How Lineage is drawn: as a person chose; else, with nothing in focus, name families (the whole
  // run); with a folder in focus holding more than FAMILIES_ABOVE files, name families (that
  // folder); otherwise every file. A file in focus is always drawn as files: its lines leave its
  // folder, and a plot of its folder's families could come up empty. A folder whose families
  // draw nothing falls back to files.
  const lineageHere = lineageData && lineageData.focus === state.focus ? lineageData : null;
  const filesInLineage = lineageHere
    ? lineageHere.nodes.reduce((sum, node) => sum + (node.kind === 'file' ? 1 : node.kind === 'aggregate' ? node.members : 0), 0)
    : 0;
  const familiesEmptyHere = Boolean(state.focus) && shownFamilies !== null && shownFamilies.folder === state.focus && shownFamilies.nodes.length === 0;
  const draw: Draw = state.draw || (!state.focus
    ? 'families'
    : lineageHere?.focus_kind === 'folder' && filesInLineage > FAMILIES_ABOVE && !familiesEmptyHere ? 'families' : 'files');
  // The folder the families are limited to: the focus where it is a folder; none (the whole run)
  // without a focus, or with a file in focus, whose lines leave its folder; unknown (null) until
  // the focus has been read.
  const familyScope: string | null = !state.focus
    ? ''
    : lineageHere
      ? (lineageHere.focus_kind === 'folder' ? state.focus : '')
      : null;
  const familiesWanted = state.mode === 'lineage' && draw !== 'files' && familyScope !== null;
  const openFamily = draw === 'grid' ? state.family : '';
  useEffect(() => {
    if (!familiesWanted || familyScope === null) return undefined;
    const controller = new AbortController();
    setFamilies({state: 'loading', slow: false});
    const slow = window.setTimeout(() => setFamilies((current) => (current.state === 'loading' ? {state: 'loading', slow: true} : current)), 1200);
    api.families({
      folder: familyScope || undefined,
      run: state.run || undefined,
      proposals: state.proposals,
      limit: state.drawn,
      family: openFamily || undefined,
      members_limit: GRID_PAGE,
      members_offset: openFamily ? state.page : undefined,
    }, controller.signal)
      .then((data) => {
        setFamilies({state: 'ready', data});
        setShownFamilies(data);
      })
      .catch((failure: unknown) => {
        if ((failure as Error).name === 'AbortError') return;
        // A family that is no longer one (a person answered Wrong, or the run changed): its page
        // is not a page; the families are.
        if (failure instanceof ApiFailure && failure.parameter === 'family') {
          update({family: '', page: 0, draw: 'families'});
          return;
        }
        setFamilies({state: 'failed', failure: failureOf(failure)});
      })
      .finally(() => window.clearTimeout(slow));
    return () => {
      controller.abort();
      window.clearTimeout(slow);
    };
  }, [familiesWanted, familyScope, state.run, state.proposals, state.drawn, openFamily, state.page, update]);
  const familiesData = families.state === 'ready' ? families.data : shownFamilies;
  const timelineData = timeline.state === 'ready' ? timeline.data : shownTimeline;
  const timelineAxes = useAxes(timelineData?.focus.id ?? state.focus, state.run, state.mode === 'timeline' && state.reading === 'activity');
  const selection = useMemo(() => selectionFromParam(state.selected, wheelData), [state.selected, wheelData]);
  const selectedSector = selection.type === 'sector' && wheelData ? wheelData.sectors.findIndex((item) => item.id === selection.id) : -1;
  const selectedBin = selection.type === 'bin' && wheelData
    ? wheelData.bins.sector.findIndex((sector, index) => wheelData.sectors[sector]?.id === selection.sectorId && wheelData.bins.start[index] === selection.start)
    : -1;

  // The name a drawing gives an entry, for a relation's words.
  const nameOf = useCallback((id: string): string => {
    const node = lineageData?.nodes.find((item) => item.id === id);
    if (node) return node.name;
    const sector = wheelData?.sectors.find((item) => (item.entry_id ?? item.id) === id);
    return sector?.name ?? id;
  }, [lineageData, wheelData]);
  const chooseId = useCallback((id: string) => {
    // In a drawing, a bare identifier is an entry, a line, a group or a statement.
    if (relating && id.startsWith('entry_')) select({type: 'entry', id});
    else update({selected: id});
  // eslint-disable-next-line react-hooks/exhaustive-deps -- select is declared just below
  }, [relating, update]);
  const select = useCallback((next: Selection) => {
    if (relating) {
      const id = next.type === 'entry' ? next.id
        : next.type === 'sector' ? (wheelData?.sectors.find((item) => item.id === next.id)?.entry_id ?? (next.id.startsWith('entry_') ? next.id : ''))
          : '';
      if (id && id !== relating.source.id) {
        human.relate(relating.source, {id, name: nameOf(id)}, relating.relation);
        setRelating(null);
        update({selected: selectionToParam(next)});
        return;
      }
    }
    update({selected: selectionToParam(next)});
  }, [update, relating, human, nameOf, wheelData]);
  const recenter = useCallback((id: string) => update({mode: 'wheel', focus: id, selected: ''}, true), [update]);
  // Into a value sector: a folder it names becomes the center, a value a filter, and the rings
  // left are the axes beyond it (the folder tree where none are).
  const drill = useCallback((index: number) => {
    if (!wheelDataRef.current) return;
    const data = wheelDataRef.current;
    const sector = data.sectors[index];
    if (!sector || index === 0 || sector.kind === 'more') return;
    if (sector.kind === 'folder') {
      update({mode: 'wheel', focus: sector.id, selected: ''}, true);
      return;
    }
    const target = drillTarget(data, index, state.where);
    update({mode: 'wheel', focus: target.focus, where: target.where, arrange: target.arrange, selected: ''}, true);
  }, [update, state.where]);
  const refocus = useCallback((id: string) => {
    const current = lineageData?.nodes.find((node) => node.is_focus);
    if (state.mode === 'lineage' && current && current.id !== id) {
      setTrail((held) => [...held.filter((item) => item.id !== current.id), {id: current.id, name: current.name}].slice(-6));
    }
    update({mode: 'lineage', focus: id, selected: id, expand: [], open: [], collapsed: []}, true);
  }, [update, state.mode, lineageData]);
  const goUp = useCallback(() => {
    if (!wheelData || !wheelData.breadcrumb.length) return;
    const parent = wheelData.breadcrumb[wheelData.breadcrumb.length - 1];
    update({focus: parent.id, selected: wheelData.focus.id}, true);
  }, [wheelData, update]);
  // D replaces the view that had the keyboard. The workspace holds it meanwhile, and the new view
  // takes it as soon as it is drawn (for a moment only), so D goes on working.
  const wantFocus = useRef(false);
  const cycleDraw = useCallback(() => {
    wantFocus.current = true;
    window.setTimeout(() => { wantFocus.current = false; }, 2000);
    root?.focus({preventScroll: true});
    const next = NEXT_DRAW[draw];
    update({draw: next, family: next === 'grid' ? state.family : '', page: 0}, true);
  }, [draw, state.family, update, root]);
  // A family opens as a Grid; a file in no family is centered, as Files.
  const openNode = useCallback((node: FamilyNode) => {
    if (node.kind === 'family') update({draw: 'grid', family: node.id, page: 0, selected: node.id}, true);
    else update({mode: 'lineage', draw: 'files', focus: node.id, selected: node.id, family: '', page: 0, expand: [], open: [], collapsed: []}, true);
  }, [update]);
  const openFile = useCallback((id: string) => {
    update({mode: 'lineage', draw: 'files', focus: id, selected: id, family: '', page: 0, expand: [], open: [], collapsed: []}, true);
  }, [update]);
  const onPick = useCallback((pick: WheelPick) => {
    if (!wheelData) return;
    if (pick.type === 'sector') select({type: 'sector', id: wheelData.sectors[pick.index].id});
    else {
      const sector = wheelData.sectors[wheelData.bins.sector[pick.index]];
      select({type: 'bin', sectorId: sector.id, start: wheelData.bins.start[pick.index], n: wheelData.bins.n[pick.index]});
    }
  }, [wheelData, select]);
  const switchMode = useCallback((mode: Mode) => {
    if (mode === state.mode) return;
    if (mode === 'timeline') {
      // A file's dated thread; a folder's activity over time.
      const lineageFocus = lineageData?.nodes.find((node) => node.is_focus);
      if (state.mode === 'lineage' && lineageFocus) {
        update({mode, focus: lineageFocus.id, reading: lineageFocus.kind === 'folder' ? 'activity' : 'thread', from: '', to: ''}, true);
        return;
      }
      const valueIndex = selection.type === 'sector' && wheelData ? wheelData.sectors.findIndex((sector) => sector.id === selection.id && sector.kind === 'value') : -1;
      if (valueIndex > 0 && wheelData) {
        // A value seen over time: its files, rows by the next ring's axis where it is one.
        const target = drillTarget(wheelData, valueIndex, state.where);
        const next = target.arrange.find((key) => key !== 'folder' && key !== 'unit');
        update({mode, focus: target.focus, where: target.where, reading: 'activity', by: next ?? 'folders', from: '', to: '', selected: ''}, true);
        return;
      }
      const chosen = selection.type === 'entry' || (selection.type === 'sector' && !selection.id.startsWith('more:')) ? selection.id : '';
      const isFolder = chosen ? Boolean(wheelData?.sectors.some((sector) => sector.id === chosen)) : true;
      update({mode, focus: chosen || state.focus || wheelData?.focus.id || '', reading: chosen && !isFolder ? 'thread' : 'activity', from: '', to: ''}, true);
      return;
    }
    if (mode === 'lineage') {
      // The Wheel's selection, where it is a file or a folder, is the lineage's focus; a value of
      // an arranged Wheel is no entry, so the folder it names, else the Wheel's center.
      const sector = selection.type === 'sector' ? wheelData?.sectors.find((item) => item.id === selection.id) : undefined;
      const chosen = selection.type === 'entry'
        ? selection.id
        : sector && sector.kind === 'folder'
          ? sector.id
          : sector?.entry_id ?? (state.focus || wheelData?.focus.id || '');
      update({mode, focus: chosen, selected: chosen, expand: [], open: [], collapsed: []}, true);
    } else {
      // A file in focus in Lineage is shown in its folder's Wheel, still selected.
      const focusNode = lineageData?.nodes.find((node) => node.is_focus);
      const chosen = lineageData?.nodes.find((node) => node.id === state.selected) ?? focusNode;
      if (chosen && chosen.kind === 'file' && chosen.folder_id) {
        update({mode, focus: chosen.folder_id, selected: chosen.id}, true);
      } else {
        update({mode}, true);
      }
    }
  }, [state.mode, state.focus, state.selected, selection, wheelData, lineageData, update]);
  const pickBin = useCallback((track: number, bin: number) => update({selected: `tbin:${track}:${bin}`}), [update]);
  const selectedTimelineBin = (() => {
    const found = /^tbin:(\d+):(\d+)$/.exec(state.selected);
    return found ? {track: Number(found[1]), bin: Number(found[2])} : null;
  })();
  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((item) => item !== id) : [...list, id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!root || !root.contains(document.activeElement)) return;
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes((event.target as HTMLElement).tagName);
      if ((event.metaKey || event.ctrlKey) && (event.key === 'z' || event.key === 'Z') && !typing) {
        if (event.shiftKey) human.redo();
        else human.undo();
        event.preventDefault();
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === 'Escape' && relating) {
        setRelating(null);
        event.preventDefault();
        return;
      }
      const target = event.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable) return;
      if ((event.key === 'z' || event.key === 'Z') && !event.altKey) {
        // Handled below for Cmd or Ctrl Z; a bare Z does nothing.
      }
      if (event.key === 'l' || event.key === 'L') {
        setLabelStart((value) => value + 1);
        event.preventDefault();
      } else if ((event.key === 'a' || event.key === 'A') && state.mode === 'wheel') {
        setArranging((open) => !open);
        event.preventDefault();
      } else if ((event.key === 'd' || event.key === 'D') && state.mode === 'lineage') {
        cycleDraw();
        event.preventDefault();
      } else if (event.key === 't' || event.key === 'T') {
        update({table: !state.table});
        event.preventDefault();
      } else if (state.mode === 'wheel' && ['1', '2', '3', '4'].includes(event.key)) {
        update({measure: (['exam', 'stage', 'prov', 'kind'] as MeasureKey[])[Number(event.key) - 1]});
        event.preventDefault();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [root, state.table, state.mode, update, human, relating, cycleDraw]);

  const lineageSource = state.mode === 'lineage' && draw !== 'files' ? familiesData : lineageData;
  const pinned = state.mode === 'lineage' ? lineageSource?.pinned : state.mode === 'timeline' ? timelineData?.pinned : wheelData?.pinned;
  const runId = state.mode === 'lineage' ? lineageSource?.run_id : state.mode === 'timeline' ? timelineData?.run_id : wheelData?.run_id;
  const runState = (() => {
    if (!pinned || !runId) return {state: 'current', words: 'reading…', title: ''};
    if (!pinned.is_pinned_run) return {state: 'historic', words: 'An earlier run', title: `${runId}: an earlier run, named in the address; not the current one.`};
    if (version?.job_running || pinned.job_running) return {state: 'pending', words: 'Settled run · recomputing', title: `${runId}: a job is running; this is the settled run, kept until the new run is published.`};
    return {state: 'current', words: 'Current run', title: `${runId}: ${pinned.message}`};
  })();
  const newer = runId && !state.run && version && version.run_pinned && version.run_pinned !== runId && !version.job_running;
  const lineageLoad: Load<unknown> = draw === 'files' ? lineage : familyScope === null ? lineage : families;
  const loading = (state.mode === 'wheel' ? wheel.state : state.mode === 'lineage' ? lineageLoad.state : timeline.state) === 'loading';

  useEffect(() => {
    if (!wantFocus.current || loading || !root) return;
    const target = root.querySelector<HTMLElement>('.ix-canvas-wrap, .ix-table-wrap[tabindex], .ix-state[tabindex]');
    if (target && document.activeElement !== target) target.focus({preventScroll: true});
  });

  const measure = wheelData?.measures.find((item) => item.key === state.measure) ?? wheelData?.measures[0];
  const kinds = wheelData?.measures.find((item) => item.key === 'kind');
  const lineageFocus = lineageData?.nodes.find((node) => node.is_focus);
  const lineageSelected = state.selected.startsWith('edge:') ? state.selected.slice(5) : state.selected;
  // A file no line touches: the lineage is its node alone. Said, with the way to look further.
  const lineageLone = state.mode === 'lineage' && draw === 'files' && lineageData !== null && lineageData.focus === state.focus
    && lineageData.focus_kind === 'file' && lineageData.edges.length === 0 && lineageData.open_ends.length === 0
    && lineageData.truncation.lines_not_drawn === 0;

  return (
    <div className="ix-root" ref={setRoot} tabIndex={-1} data-loading={loading ? 'true' : undefined} aria-busy={loading}>
      <div className="ix-toolbar" role="toolbar" aria-label="Graph workspace">
        <div className="ix-seg" role="group" aria-label="Mode">
          <button type="button" aria-pressed={state.mode === 'wheel'} onClick={() => switchMode('wheel')} title="What is in this folder, how many experiment units, what has been examined">
            <ModeGlyph mode="wheel" /> Wheel
          </button>
          <button type="button" aria-pressed={state.mode === 'lineage'} onClick={() => switchMode('lineage')} title="How files led to an output, and where lineages fork and join">
            <ModeGlyph mode="lineage" /> Lineage
          </button>
          <button type="button" aria-pressed={state.mode === 'timeline'} onClick={() => switchMode('timeline')} title="A file's dated provenance, or a folder's work over time">
            <ModeGlyph mode="timeline" /> Timeline
          </button>
        </div>
        {state.mode === 'timeline' ? (
          <nav className="ix-crumbs" aria-label="What the timeline shows">
            {timelineData ? (
              <button type="button" className="ix-crumb" aria-current="location" title={timelineData.focus.path}>
                {labels[timelineData.focus.id] ?? timelineData.focus.name}
              </button>
            ) : null}
            <WhereChips where={state.where} labels={whereLabels} onRemove={(item) => update({where: state.where.filter((kept) => kept !== item)}, true)} />
            {state.from && state.to ? (
              <button type="button" className="ix-btn ix-quiet" onClick={() => update({from: '', to: ''})} title="Clear the window chosen">
                {longTime(Date.parse(state.from))} to {longTime(Date.parse(state.to))} ×
              </button>
            ) : null}
          </nav>
        ) : state.mode === 'wheel' ? (
          <nav className="ix-crumbs" aria-label="Where the Wheel is centered">
            {wheelData ? (
              <>
                {wheelData.breadcrumb.map((crumb) => (
                  <span key={crumb.id} style={{display: 'contents'}}>
                    <button type="button" className="ix-crumb" title={crumb.path || crumb.name} onClick={() => recenter(crumb.id)}>
                      {labels[crumb.id] ?? crumb.name}
                    </button>
                    <span className="ix-crumb-sep" aria-hidden="true">/</span>
                  </span>
                ))}
                <button type="button" className="ix-crumb" aria-current={state.where.length ? undefined : 'location'} title={wheelData.focus.path || wheelData.focus.name}
                  onClick={() => (state.where.length ? update({where: [], selected: ''}, true) : undefined)}>
                  {labels[wheelData.focus.id] ?? wheelData.focus.name}
                </button>
                <WhereChips where={state.where} labels={whereLabels} onRemove={(item) => update({where: state.where.filter((kept) => kept !== item), selected: ''}, true)} />
              </>
            ) : null}
          </nav>
        ) : (
          <nav className="ix-crumbs" aria-label="The lineage's focus, and earlier ones">
            {draw !== 'files' ? (
              <button type="button" className="ix-crumb" aria-current="location"
                title={familiesData?.folder_name ? `Name families in ${familiesData.folder_name}` : 'Name families among every file with a line in this run'}>
                {familiesData?.folder_name ?? 'Every file with a line'}
              </button>
            ) : null}
            {draw === 'files' ? trail.map((crumb) => (
              <span key={crumb.id} style={{display: 'contents'}}>
                <button type="button" className="ix-crumb" onClick={() => refocus(crumb.id)} title="An earlier focus">{labels[crumb.id] ?? crumb.name}</button>
                <span className="ix-crumb-sep" aria-hidden="true">›</span>
              </span>
            )) : null}
            {draw === 'files' && lineageFocus ? (
              <button type="button" className="ix-crumb" aria-current="location" title={lineageFocus.path}
                onClick={() => update({selected: lineageFocus.id})}>
                {labels[lineageFocus.id] ?? lineageFocus.name}
              </button>
            ) : null}
            {draw === 'files' && (state.expand.length || state.open.length || state.collapsed.length) ? (
              <button type="button" className="ix-btn ix-quiet" onClick={() => update({expand: [], open: [], collapsed: []})} title="Put back every branch expanded, opened or collapsed">
                Reset branches ({state.expand.length + state.open.length + state.collapsed.length})
              </button>
            ) : null}
          </nav>
        )}
        {state.mode === 'wheel' ? (
          <>
            <div className="ix-seg" role="group" aria-label="Color by">
              {(['exam', 'stage', 'prov', 'kind'] as MeasureKey[]).map((key, index) => (
                <button key={key} type="button" aria-pressed={state.measure === key} onClick={() => update({measure: key})}
                  title={`${wheelData?.measures.find((item) => item.key === key)?.question ?? ''} (key ${index + 1})`}>
                  {MEASURE_WORDS[key]}
                </button>
              ))}
            </div>
            <div className="ix-seg" role="group" aria-label="Size wedges by">
              <button type="button" aria-pressed={state.size === 'files'} onClick={() => update({size: 'files'})}>Files</button>
              <button type="button" aria-pressed={state.size === 'bytes'} onClick={() => update({size: 'bytes'})}>Bytes</button>
            </div>
            <div className="ix-arrange-anchor">
              <ArrangeButton arrange={state.arrange} labels={axisWords} open={arranging} onToggle={() => setArranging(!arranging)} />
              {arranging && wheelData ? (
                <ArrangePanel focus={wheelData.focus.id} run={state.run} arrange={state.arrange}
                  onChange={(axes) => update({arrange: axes, selected: ''}, true)} onClose={() => setArranging(false)} />
              ) : null}
            </div>
            {state.arrange.length ? null : (
              <div className="ix-seg" role="group" aria-label="Rings">
                <button type="button" onClick={() => update({rings: Math.max(1, state.rings - 1)})} disabled={state.rings <= 1} aria-label="Fewer rings">−</button>
                <span className="ix-seg-value">{state.rings} rings</span>
                <button type="button" onClick={() => update({rings: Math.min(8, state.rings + 1)})} disabled={state.rings >= 8} aria-label="More rings">+</button>
              </div>
            )}
          </>
        ) : state.mode === 'timeline' ? (
          <>
            <div className="ix-seg" role="group" aria-label="Reading">
              <button type="button" aria-pressed={state.reading === 'thread'} onClick={() => update({reading: 'thread', from: '', to: ''})}
                title="One file's lineage, each file at its time">Thread</button>
              <button type="button" aria-pressed={state.reading === 'activity'} onClick={() => update({reading: 'activity', from: '', to: ''})}
                title="A folder's files counted over time">Activity</button>
            </div>
            <label className="ix-select" title={timelineData?.sources.find((item) => item.key === state.source)?.meaning}>
              <span className="ix-label" style={{marginRight: 6}}>Placed by</span>
              <select value={state.source} onChange={(event) => update({source: event.target.value})}>
                {(timelineData?.sources ?? [{key: 'best', label: 'best available', meaning: ''}]).map((item) => (
                  <option key={item.key} value={item.key}>{item.label}{timelineData ? ` (${count(timelineData.dated[item.key] ?? 0)})` : ''}</option>
                ))}
              </select>
            </label>
            {state.reading === 'activity' ? (
              <div className="ix-seg" role="group" aria-label="Rows">
                <button type="button" aria-pressed={state.by === 'folders'} onClick={() => update({by: 'folders'})} title="One row per child folder">Folders</button>
                <button type="button" aria-pressed={state.by === 'units'} onClick={() => update({by: 'units'})} title="One row per experiment unit beneath">Units</button>
                <label className="ix-select" title="One row per value of what names say, or of what the run read">
                  <span className="ix-sr">Rows by another axis</span>
                  <select value={state.by === 'folders' || state.by === 'units' ? '' : state.by} onChange={(event) => update({by: event.target.value || 'folders'})}>
                    <option value="">By…</option>
                    {(timelineAxes?.axes ?? [])
                      .filter((item) => item.axis.key !== 'folder' && item.axis.key !== 'unit' && item.files_with_value > 0)
                      .map((item) => <option key={item.axis.key} value={item.axis.key}>{item.axis.label} ({count(item.values)})</option>)}
                    {state.by !== 'folders' && state.by !== 'units' && !timelineAxes ? <option value={state.by}>{state.by}</option> : null}
                  </select>
                </label>
              </div>
            ) : (
              <button type="button" className="ix-btn" aria-pressed={state.proposals} onClick={() => update({proposals: !state.proposals})}
                title="Draw proposed lines too: violet, dashed, never a fact">Proposals</button>
            )}
          </>
        ) : (
          <>
            <span className="ix-label">Draw</span>
            <div className="ix-seg" role="group" aria-label="Draw">
              {(['families', 'files', 'grid'] as Draw[]).map((way) => (
                <button key={way} type="button" aria-pressed={draw === way} title={DRAW_WORDS[way].title}
                  onClick={() => update({draw: way, family: way === 'grid' ? state.family : '', page: 0}, true)}>
                  {DRAW_WORDS[way].label}
                </button>
              ))}
            </div>
            {draw === 'files' ? (
              <>
                <div className="ix-seg" role="group" aria-label="Which way">
                  {(['up', 'both', 'down'] as const).map((way) => (
                    <button key={way} type="button" aria-pressed={state.direction === way} onClick={() => update({direction: way, expand: [], open: []})}>
                      {way === 'up' ? 'Upstream' : way === 'down' ? 'Downstream' : 'Both'}
                    </button>
                  ))}
                </div>
                <div className="ix-seg" role="group" aria-label="Steps">
                  <button type="button" onClick={() => update({depth: Math.max(1, state.depth - 1)})} disabled={state.depth <= 1} aria-label="Fewer steps">−</button>
                  <span className="ix-seg-value">{state.depth} step{state.depth === 1 ? '' : 's'}</span>
                  <button type="button" onClick={() => update({depth: Math.min(12, state.depth + 1)})} disabled={state.depth >= 12} aria-label="More steps">+</button>
                </div>
              </>
            ) : null}
            <button type="button" className="ix-btn" aria-pressed={state.proposals} onClick={() => update({proposals: !state.proposals})}
              title="Draw the pairs a rule proposed and nobody asserted: dashed, never a fact">Proposals</button>
            {draw === 'files' ? (
              <button type="button" className="ix-btn" aria-pressed={state.groups} onClick={() => update({groups: !state.groups})}
                title="Draw the groups the files belong to: memberships, not provenance">Groups</button>
            ) : null}
            <label className="ix-select">
              <span className="ix-sr">Orientation</span>
              <select value={state.orientation} onChange={(event) => update({orientation: event.target.value as Orientation})}>
                {(Object.keys(ORIENTATION_WORDS) as Orientation[]).map((key) => <option key={key} value={key}>{ORIENTATION_WORDS[key]}</option>)}
              </select>
            </label>
          </>
        )}
        <button type="button" className="ix-btn" aria-pressed={state.table} onClick={() => update({table: !state.table})} title="The same content as rows (key T)">
          Table
        </button>
        <HumanBar human={human} onReview={() => setReviewing(true)} />
        <span className="ix-run" data-state={runState.state} title={runState.title}>
          <span className="ix-dot" aria-hidden="true" />{runState.words}
        </span>
        {newer ? (
          <button type="button" className="ix-btn" onClick={() => window.location.reload()}>A newer run is published: show it</button>
        ) : null}
      </div>
      {relating ? (
        <div className="ix-relating" role="status">
          <span>Choose the file or folder that <strong>{relating.source.name}</strong> {RELATION_WORDS[relating.relation]}, in any mode.</span>
          <button type="button" className="ix-btn ix-quiet" onClick={() => setRelating(null)}>Cancel (Esc)</button>
        </div>
      ) : null}
      <div className="ix-body">
        <div className="ix-stage-area">
          <div style={{position: 'relative', minHeight: 0}}>
            {state.mode === 'wheel' && wheelData && !state.table ? (
              <WheelView
                data={wheelData}
                measure={state.measure}
                size={state.size}
                selectedSector={selectedSector >= 0 ? selectedSector : null}
                selectedBin={selectedBin >= 0 ? selectedBin : null}
                labels={labels}
                onPick={onPick}
                onRecenter={recenter}
                onDrill={drill}
                excluded={human.excluded}
                moves={human.moves}
                onStageMove={(entry, folder) => {
                  const source = wheelData.sectors.find((item) => item.id === entry);
                  const from = wheelData.sectors.find((item) => item.id === source?.parent) ?? (source?.parent === wheelData.focus.id ? wheelData.sectors[0] : undefined);
                  const to = wheelData.sectors.find((item) => item.id === folder);
                  if (source && from && to) human.stageMove(entry, source.name, {id: from.id, name: from.name}, {id: to.id, name: to.name});
                }}
                onUp={goUp}
                onClear={() => select({type: 'none'})}
              />
            ) : null}
            {state.mode === 'wheel' && wheelData && state.table ? (
              <WheelTable data={wheelData} measure={state.measure} selectedSector={selectedSector >= 0 ? selectedSector : null} labels={labels}
                onPick={(index) => select({type: 'sector', id: wheelData.sectors[index].id})} onRecenter={recenter} onDrill={drill} />
            ) : null}
            {state.mode === 'lineage' && draw === 'files' && lineageData && !state.table ? (
              <LineageView
                data={lineageData}
                orientation={state.orientation}
                selected={lineageSelected}
                collapsed={state.collapsed}
                labels={labels}
                onSelect={chooseId}
                onRefocus={refocus}
                onExpand={(id) => update({expand: state.expand.includes(id) ? state.expand : [...state.expand, id]})}
                onOpen={(id) => update({open: state.open.includes(id) ? state.open : [...state.open, id], selected: ''})}
                onToggleCollapse={(id) => update({collapsed: toggle(state.collapsed, id)})}
                onLayoutMeasured={setLayoutMs}
                moves={human.moves}
                layer={{relations: human.relations, excluded: human.excluded, groups: human.groups}}
                onStageMove={(id, folder) => {
                  const node = lineageData.nodes.find((item) => item.id === id);
                  const from = lineageData.folders.find((item) => item.id === node?.folder_id);
                  const to = lineageData.folders.find((item) => item.id === folder);
                  if (node && from && to) human.stageMove(id, node.name, {id: from.id, name: from.name}, {id: to.id, name: to.name});
                }}
              />
            ) : null}
            {state.mode === 'lineage' && draw === 'files' && lineageData && lineageLone ? (
              <LoneFile run={lineageData.run_id} proposals={state.proposals} onProposals={() => update({proposals: true})} />
            ) : null}
            {state.mode === 'lineage' && draw !== 'files' && familiesData ? (
              <FamiliesPane
                data={familiesData}
                draw={draw}
                table={state.table}
                orientation={state.orientation}
                selected={state.selected}
                drawn={state.drawn}
                onSelect={chooseId}
                onOpenNode={openNode}
                onOpenFile={openFile}
                onDraw={(next) => update({drawn: Math.min(FAMILIES_MOST, next)})}
                onTable={() => update({table: true})}
                onBack={() => update({draw: 'families', family: '', page: 0}, true)}
                onPage={(offset) => update({page: offset})}
                onLayoutMeasured={setLayoutMs}
              />
            ) : null}
            {state.mode === 'lineage' && draw === 'files' && lineageData && state.table ? (
              <LineageTable data={lineageData} selected={state.selected} labels={labels} onSelect={chooseId} onRefocus={refocus} />
            ) : null}
            {state.mode === 'timeline' && timelineData && !state.table ? (
              <TimelineView
                data={timelineData}
                selected={state.selected.startsWith('edge:') ? state.selected.slice(5) : state.selected}
                labels={labels}
                onSelect={chooseId}
                onRefocus={(id) => update({focus: id, selected: id, reading: 'thread', from: '', to: ''}, true)}
                onWindow={(chosen) => update(chosen ? {from: new Date(chosen.start).toISOString(), to: new Date(chosen.end).toISOString()} : {from: '', to: ''}, true)}
                onPickBin={pickBin}
                selectedBin={selectedTimelineBin}
              />
            ) : null}
            {state.mode === 'timeline' && timelineData && state.table ? (
              <TimelineTable data={timelineData} selected={state.selected} labels={labels} onSelect={chooseId} />
            ) : null}
            {state.mode === 'timeline' ? (
              <LoadState load={timeline} hasData={Boolean(timelineData)} what="timeline" onRetry={() => update({...state})} onRoot={() => switchMode('wheel')} />
            ) : null}
            {state.mode === 'timeline' ? null : state.mode === 'wheel' ? (
              <LoadState load={wheel} hasData={Boolean(wheelData)} what="Wheel" onRetry={() => update({...state})} onRoot={() => update({focus: '', selected: ''}, true)} />
            ) : (
              <LoadState load={lineageLoad} hasData={draw === 'files' ? Boolean(lineageData) : Boolean(familiesData)} what={draw === 'files' ? 'lineage' : 'name families'} onRetry={() => update({...state})} onRoot={() => switchMode('wheel')} />
            )}
          </div>
          <div className="ix-footer">
            {state.mode === 'wheel' && measure ? <WheelLegend measure={measure} kinds={kinds} /> : null}
            {state.mode === 'lineage' ? (draw === 'files' ? <LineageLegend /> : <FamiliesLegend />) : null}
            {state.mode === 'timeline' ? <TimelineLegend reading={state.reading} /> : null}
            <span className="ix-spacer" />
            {state.mode === 'wheel' && wheelData ? (
              <span className="ix-caps" title={wheelData.caps.message}>
                {count(wheelData.caps.sectors_shown)} wedges · {count(wheelData.totals.files)} files in {count(wheelData.caps.bins_shown)} bins
                {wheelData.projection.built_now ? ` · prepared for this run in ${wheelData.projection.build_seconds.toFixed(1)} s` : ''}
              </span>
            ) : null}
            {state.mode === 'timeline' && timelineData ? (
              <span className="ix-caps" title={timelineData.notes.join(' ')}>
                placed by {timelineData.sources.find((item) => item.key === timelineData.source)?.label} · {count(timelineData.dated[timelineData.source] ?? 0)} dated
                {timelineData.activity ? ` · ${timelineData.activity.bins.unit} bins` : ''}{timelineData.thread ? ` · ${count(timelineData.thread.undated.length)} undated` : ''} · {count(timelineData.runs.length)} runs
              </span>
            ) : null}
            {state.mode === 'lineage' && draw === 'files' && lineageData ? (
              <span className="ix-caps" title={lineageData.truncation.message}>
                {count(lineageData.nodes.length)} items · {count(lineageData.edges.length)} lines{layoutMs !== null ? ` · laid out in ${layoutMs} ms` : ''}
              </span>
            ) : null}
            {state.mode === 'lineage' && draw !== 'files' && familiesData ? (
              <span className="ix-caps" title={familiesData.truncation.message}>
                {count(familiesData.nodes.filter((node) => node.kind === 'family').length)} of {count(familiesData.truncation.families_total)} families · {count(familiesData.nodes.filter((node) => node.kind === 'file').length)} of {plural(familiesData.truncation.files_total, 'file')} in no family · {count(familiesData.edges.length)} edges
              </span>
            ) : null}
          </div>
        </div>
        {reviewing ? <ReviewPanel human={human} onClose={() => setReviewing(false)} runId={runId} /> : null}
        <Inspector human={human} labelStart={labelStart} mode={state.mode} data={wheelData} lineage={lineageData} families={draw !== 'files' ? familiesData : null}
          onOpenFamily={(id) => update({draw: 'grid', family: id, page: 0, selected: id}, true)} timeline={timelineData} measures={measures} selection={selection} measure={state.measure}
          run={state.run} labels={labels} onSelect={select} onRecenter={recenter} onRefocus={refocus}
          where={state.where} onDrill={drill} onOverTime={() => switchMode('timeline')}
          onRelate={(source, relation) => setRelating({source, relation})}
          onOpen={(id) => update({open: state.open.includes(id) ? state.open : [...state.open, id]})} />
      </div>
    </div>
  );
}

function WhereChips({where, labels, onRemove}: {where: string[]; labels: Map<string, string>; onRemove(item: string): void}): ReactElement | null {
  if (!where.length) return null;
  return (
    <>
      {where.map((item) => {
        const [key, ...rest] = item.split('=');
        const value = rest.join('=');
        const words = labels.get(item) ?? (value || 'none');
        return (
          <span key={item} style={{display: 'contents'}}>
            <span className="ix-crumb-sep" aria-hidden="true">›</span>
            <button type="button" className="ix-chip ix-where" onClick={() => onRemove(item)}
              title={`Only files whose ${key.replace(/@\d+$/, '')} is ${words}. Choose to remove this filter.`}>
              {words} <span aria-hidden="true">×</span><span className="ix-sr">remove filter</span>
            </button>
          </span>
        );
      })}
    </>
  );
}

function LoadState<T>({load, hasData, what, onRetry, onRoot}: {load: Load<T>; hasData: boolean; what: string; onRetry(): void; onRoot(): void}): ReactElement | null {
  if (load.state === 'idle' && what === 'lineage') {
    return (
      <div className="ix-state" tabIndex={-1}>
        <div>
          <h2>Choose a file or a folder</h2>
          <p>Select one in the Wheel, then choose Lineage.</p>
          <div className="ix-actions" style={{justifyContent: 'center'}}><button type="button" className="ix-btn" onClick={onRoot}>Go to the Wheel</button></div>
        </div>
      </div>
    );
  }
  if (load.state === 'loading') {
    if (hasData && !load.slow) return null;
    return (
      <div className="ix-state" role="status">
        <div>
          <div className="ix-spin" aria-hidden="true" />
          <h2>{load.slow && what === 'Wheel' ? 'Preparing the Wheel for this run' : `Reading the ${what}`}</h2>
          {load.slow && what === 'Wheel' ? <p>Counted once for each run; after this it opens at once.</p> : null}
        </div>
      </div>
    );
  }
  if (load.state === 'failed') {
    const noRun = load.failure.code === 'no_published_run';
    return (
      <div className="ix-state" role="alert" tabIndex={-1}>
        <div>
          <h2>{noRun ? 'Nothing to draw yet' : `The ${what} could not be drawn`}</h2>
          <p>{load.failure.message}</p>
          {!noRun ? (
            <div className="ix-actions" style={{justifyContent: 'center'}}>
              <button type="button" className="ix-btn" onClick={onRetry}>Try again</button>
              <button type="button" className="ix-btn ix-quiet" onClick={onRoot}>{what === 'Wheel' ? 'Start from the root' : 'Go to the Wheel'}</button>
            </div>
          ) : null}
        </div>
      </div>
    );
  }
  return null;
}
