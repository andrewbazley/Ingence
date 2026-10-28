// The human layer: a person's own words over the drawing, kept apart from the engine's.
//
// Two kinds of edit, both drafts until a person says otherwise, both undoable one step at a time:
//   - a display label for a file or folder: shown beside its name in every mode (magenta,
//     italic), never replacing it; saved as a batch, after a preview, as annotations in the
//     Record through its one guarded way in, with who and when;
//   - a staged move in a plan: where a person would put a file, drawn in Lineage in its new
//     folder with a dashed magenta edge. A plan only: nothing on disk moves, no evidence changes,
//     and it is not saved to the Record.
//
// A person's layer also holds groups of files and folders under a name, exclusions with a reason,
// and proposed relationships between two entries. Each is a draft until saved with the labels.
// A saved one is an attributed annotation in the Record, read back with one of four states:
// recorded, projected (drawn in this run), confirmed by a newer run, or stale. An annotation is
// never a claim or an assertion of the engine's.
//
// Drafts survive a reload of this tab (sessionStorage); saved labels are read from the Record.

import {useCallback, useEffect, useMemo, useState} from 'react';

import type {HumanStatement, OverlayResponse, PlanCheck} from './api';

export interface SavedLabel {
  entry_id: string;
  label: string;
  actor: string;
  at: string;
  event_id: string;
  earlier: number;
  name_then: string | null;
}

export type Relation = 'derives' | 'references' | 'copy_of' | 'same_experiment' | 'not_related';

export const RELATION_WORDS: Record<Relation, string> = {
  derives: 'was used to make',
  references: 'names or mentions',
  copy_of: 'is a copy of',
  same_experiment: 'is the same experiment as',
  not_related: 'is not related to',
};

export type Edit =
  | {kind: 'label'; entry_id: string; name: string; label: string | null; before: string | null}
  | {kind: 'move'; entry_id: string; name: string; from_id: string; from_name: string; to_id: string; to_name: string}
  /** A group as it should stand: its id where it is saved, its members, or removed. */
  | {kind: 'group'; key: string; id: string | null; name: string; members: string[]; names: Record<string, string>; removed: boolean}
  | {kind: 'exclude'; entry_id: string; name: string; reason: string | null}
  | {kind: 'relate'; source: string; target: string; relation: Relation; source_name: string; target_name: string; removed: boolean};

/** A group as the page shows it: saved, overlaid by the draft. */
export interface ShownGroup {
  key: string;
  id: string | null;
  name: string;
  members: string[];
  names: Record<string, string>;
  pending: boolean;
  statement: HumanStatement | null;
}

export interface ShownRelation {
  key: string;
  source: string;
  target: string;
  relation: Relation;
  source_name: string;
  target_name: string;
  pending: boolean;
  statement: HumanStatement | null;
}

/** A topic's members as an earlier statement said them, in the words the overlay reads: the
 * experiments among its entries marked as experiments, its groups of words and its lines of work
 * with the experiments they were added by. Sent whole by Revert, which says the earlier list again. */
export function membersSaid(said: Record<string, unknown>): unknown[] {
  const experiments = new Set((said.experiments as string[] | undefined) ?? []);
  const members: unknown[] = ((said.members as string[] | undefined) ?? []).map((id) => (experiments.has(id) ? {kind: 'experiment', id} : id));
  for (const terms of (said.cores as string[][] | undefined) ?? []) members.push({kind: 'core', terms});
  for (const line of (said.lines as Array<{experiments: string[]; words?: string[]; label?: string | null}> | undefined) ?? []) {
    members.push({kind: 'line', experiments: line.experiments, words: line.words ?? [], ...(line.label ? {label: line.label} : {})});
  }
  return members;
}

export function relationKey(source: string, target: string, relation: string): string {
  return `overlay:relate:${source}:${target}:${relation}`;
}

interface Stored {
  draft: Edit[];
  undone: Edit[];
  /** The plan the staged moves were last saved as, so saving again makes its next version. */
  plan?: {id: string | null; name: string};
}

const KEY = 'ingence.explorer.human';

function load(): Stored {
  try {
    const held = JSON.parse(sessionStorage.getItem(KEY) ?? 'null') as Stored | null;
    if (held && Array.isArray(held.draft) && Array.isArray(held.undone)) return held;
  } catch {
    // A tab with no storage keeps its drafts in memory only.
  }
  return {draft: [], undone: []};
}

function store(value: Stored): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(value));
  } catch {
    // Kept in memory.
  }
}

export interface HumanLayer {
  saved: Map<string, SavedLabel>;
  /** The person's layer as the Record holds it, each statement with its state. */
  layer: OverlayResponse | null;
  groups: ShownGroup[];
  /** Groups by member: every group an entry is directly in. */
  groupsOf: Map<string, ShownGroup[]>;
  excluded: Map<string, {reason: string; pending: boolean; statement: HumanStatement | null}>;
  relations: ShownRelation[];
  /** Layer edits not saved yet (groups, exclusions, relations), counted by what they change. */
  layerPending: number;
  addToGroup(group: {key: string | null; name: string}, entryId: string, name: string): void;
  removeFromGroup(key: string, entryId: string): void;
  renameGroup(key: string, name: string): void;
  removeGroup(key: string): void;
  setExcluded(entryId: string, name: string, reason: string | null): void;
  relate(source: {id: string; name: string}, target: {id: string; name: string}, relation: Relation): void;
  unrelate(key: string): void;
  /** Say again exactly what a statement said before (its history's payload): one more event. */
  revert(target: string, payload: string, actor: string): Promise<void>;
  draft: Edit[];
  /** Every label as shown: saved, overlaid by the draft. */
  labels: Record<string, string>;
  /** Entries whose shown label is not saved yet. */
  pending: Set<string>;
  /** Staged moves in the plan, by entry: the folder it would go to. */
  moves: Map<string, {to_id: string; to_name: string; from_name: string}>;
  plan: {id: string | null; name: string};
  renamePlan(name: string): void;
  /** Keep the staged moves as a plan in the workspace (a new version where it was kept before),
   * then read what it would do. Nothing on disk moves. */
  checkPlan(actor: string, runId: string): Promise<PlanCheck>;
  canUndo: boolean;
  canRedo: boolean;
  setLabel(entryId: string, name: string, label: string | null): void;
  stageMove(entryId: string, name: string, from: {id: string; name: string}, to: {id: string; name: string}): void;
  undo(): void;
  redo(): void;
  discard(kind?: 'label' | 'move' | 'layer'): void;
  refresh(): Promise<void>;
  save(actor: string): Promise<{written: number}>;
  error: string;
}

export function useHumanLayer(): HumanLayer {
  const [saved, setSaved] = useState<Map<string, SavedLabel>>(new Map());
  const [layer, setLayer] = useState<OverlayResponse | null>(null);
  const [stored, setStored] = useState<Stored>(load);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const [labelsResponse, layerResponse] = await Promise.all([
        fetch('/api/v1/explorer/labels'),
        fetch('/api/v1/explorer/overlay'),
      ]);
      if (!labelsResponse.ok) throw new Error(`the labels could not be read (${labelsResponse.status})`);
      const body = (await labelsResponse.json()) as {labels: SavedLabel[]};
      setSaved(new Map(body.labels.map((label) => [label.entry_id, label])));
      // A project with no run has no layer to show; the labels still read.
      if (layerResponse.ok) setLayer((await layerResponse.json()) as OverlayResponse);
      setError('');
    } catch (failure) {
      setError((failure as Error).message);
    }
  }, []);
  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  // Every change keeps what it does not name (the plan's name and identity among them).
  const commit = useCallback((next: Partial<Stored>) => {
    setStored((current) => {
      const merged = {...current, ...next};
      store(merged);
      return merged;
    });
  }, []);

  const labels = useMemo(() => {
    const shown: Record<string, string> = {};
    for (const [id, label] of saved) shown[id] = label.label;
    for (const edit of stored.draft) {
      if (edit.kind !== 'label') continue;
      if (edit.label === null) delete shown[edit.entry_id];
      else shown[edit.entry_id] = edit.label;
    }
    return shown;
  }, [saved, stored.draft]);

  const pending = useMemo(() => {
    const ids = new Set<string>();
    for (const edit of stored.draft) if (edit.kind === 'label') ids.add(edit.entry_id);
    // An edit that ends where the Record already is, is nothing to save.
    for (const id of [...ids]) {
      const last = [...stored.draft].reverse().find((edit) => edit.kind === 'label' && edit.entry_id === id);
      if (last && last.kind === 'label' && (saved.get(id)?.label ?? null) === last.label) ids.delete(id);
    }
    return ids;
  }, [stored.draft, saved]);

  const moves = useMemo(() => {
    const planned = new Map<string, {to_id: string; to_name: string; from_name: string}>();
    for (const edit of stored.draft) {
      if (edit.kind !== 'move') continue;
      if (edit.to_id === edit.from_id) planned.delete(edit.entry_id);
      else planned.set(edit.entry_id, {to_id: edit.to_id, to_name: edit.to_name, from_name: edit.from_name});
    }
    return planned;
  }, [stored.draft]);

  // The layer as shown: the Record's statements, then the draft's edits in order.
  const groups = useMemo(() => {
    const byKey = new Map<string, ShownGroup>();
    for (const statement of layer?.statements ?? []) {
      if (statement.kind !== 'group') continue;
      const names: Record<string, string> = {};
      for (const member of statement.members) names[member.id] = member.name ?? member.name_then ?? member.id;
      byKey.set(statement.target, {
        key: statement.target,
        id: statement.target.split(':')[2],
        name: statement.name ?? '',
        members: statement.members.map((member) => member.id),
        names,
        pending: false,
        statement,
      });
    }
    for (const edit of stored.draft) {
      if (edit.kind !== 'group') continue;
      if (edit.removed) byKey.delete(edit.key);
      else byKey.set(edit.key, {key: edit.key, id: edit.id, name: edit.name, members: edit.members, names: edit.names, pending: true, statement: byKey.get(edit.key)?.statement ?? null});
    }
    return [...byKey.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [layer, stored.draft]);

  const groupsOf = useMemo(() => {
    const found = new Map<string, ShownGroup[]>();
    for (const group of groups) for (const member of group.members) found.set(member, [...(found.get(member) ?? []), group]);
    return found;
  }, [groups]);

  const excluded = useMemo(() => {
    const found = new Map<string, {reason: string; pending: boolean; statement: HumanStatement | null}>();
    for (const statement of layer?.statements ?? []) {
      if (statement.kind === 'exclude') found.set(statement.members[0]?.id ?? '', {reason: statement.reason ?? '', pending: false, statement});
    }
    for (const edit of stored.draft) {
      if (edit.kind !== 'exclude') continue;
      if (edit.reason === null) found.delete(edit.entry_id);
      else found.set(edit.entry_id, {reason: edit.reason, pending: true, statement: found.get(edit.entry_id)?.statement ?? null});
    }
    return found;
  }, [layer, stored.draft]);

  const relations = useMemo(() => {
    const byKey = new Map<string, ShownRelation>();
    for (const statement of layer?.statements ?? []) {
      if (statement.kind !== 'relate' || statement.members.length < 2) continue;
      const [source, target] = statement.members;
      byKey.set(statement.target, {
        key: statement.target, source: source.id, target: target.id, relation: (statement.relation ?? 'derives') as Relation,
        source_name: source.name ?? source.name_then ?? source.id, target_name: target.name ?? target.name_then ?? target.id,
        pending: false, statement,
      });
    }
    for (const edit of stored.draft) {
      if (edit.kind !== 'relate') continue;
      const key = relationKey(edit.source, edit.target, edit.relation);
      if (edit.removed) byKey.delete(key);
      else byKey.set(key, {key, source: edit.source, target: edit.target, relation: edit.relation, source_name: edit.source_name, target_name: edit.target_name, pending: true, statement: byKey.get(key)?.statement ?? null});
    }
    return [...byKey.values()];
  }, [layer, stored.draft]);

  /** The layer's edits to send: each group, exclusion and relation whose final draft differs from the Record. */
  const layerEdits = useMemo(() => {
    const edits: Array<Record<string, unknown>> = [];
    const savedGroups = new Map((layer?.statements ?? []).filter((item) => item.kind === 'group').map((item) => [item.target, item]));
    const lastGroup = new Map<string, Extract<Edit, {kind: 'group'}>>();
    const lastExclude = new Map<string, Extract<Edit, {kind: 'exclude'}>>();
    const lastRelate = new Map<string, Extract<Edit, {kind: 'relate'}>>();
    for (const edit of stored.draft) {
      if (edit.kind === 'group') lastGroup.set(edit.key, edit);
      if (edit.kind === 'exclude') lastExclude.set(edit.entry_id, edit);
      if (edit.kind === 'relate') lastRelate.set(relationKey(edit.source, edit.target, edit.relation), edit);
    }
    for (const [key, edit] of lastGroup) {
      const was = savedGroups.get(key);
      if (edit.removed) {
        if (edit.id) edits.push({kind: 'group', id: edit.id, remove: true});
        continue;
      }
      if (!edit.members.length) continue;
      if (!edit.id) {
        // A topic made here: its name and its members, which are all files and folders.
        edits.push({kind: 'group', name: edit.name, members: edit.members});
        continue;
      }
      // A topic that stands is edited by what changed. A rename names no member, so the groups of
      // words, the lines of work and the marks of experiments it holds are never sent back as plain
      // identifiers. Members added are ``add`` and members taken out are ``remove_members``, as the
      // overlay served them (an entry's identifier, ``group:<words>``, ``line:<digest>``). The
      // server carries over every member already held.
      const held = new Set(was ? was.members.map((m) => m.id) : []);
      const now = new Set(edit.members);
      const added = edit.members.filter((id) => !held.has(id));
      const taken = [...held].filter((id) => !now.has(id));
      const renamed = !was || was.name !== edit.name;
      if (!renamed && !added.length && !taken.length) continue;
      edits.push({
        kind: 'group',
        id: edit.id,
        ...(renamed ? {name: edit.name} : {}),
        ...(added.length ? {add: added.map((id) => ({kind: 'entry', id}))} : {}),
        ...(taken.length ? {remove_members: taken} : {}),
      });
    }
    const savedExcluded = new Map((layer?.statements ?? []).filter((item) => item.kind === 'exclude').map((item) => [item.members[0]?.id, item.reason]));
    for (const [entry, edit] of lastExclude) {
      const was = savedExcluded.get(entry);
      if (edit.reason === null) { if (was !== undefined) edits.push({kind: 'exclude', entry, remove: true}); continue; }
      if (was !== edit.reason) edits.push({kind: 'exclude', entry, reason: edit.reason});
    }
    const savedRelations = new Set((layer?.statements ?? []).filter((item) => item.kind === 'relate').map((item) => item.target));
    for (const [key, edit] of lastRelate) {
      if (edit.removed) { if (savedRelations.has(key)) edits.push({kind: 'relate', source: edit.source, target: edit.target, relation: edit.relation, remove: true}); continue; }
      if (!savedRelations.has(key)) edits.push({kind: 'relate', source: edit.source, target: edit.target, relation: edit.relation});
    }
    return edits;
  }, [layer, stored.draft]);

  const push = useCallback((edit: Edit) => commit({draft: [...stored.draft, edit], undone: []}), [stored.draft, commit]);

  const addToGroup = useCallback((group: {key: string | null; name: string}, entryId: string, name: string) => {
    const current = group.key ? groups.find((item) => item.key === group.key) : undefined;
    const key = current?.key ?? `draft:${Date.now().toString(36)}:${group.name}`;
    const members = [...new Set([...(current?.members ?? []), entryId])];
    push({kind: 'group', key, id: current?.id ?? null, name: current?.name ?? group.name, members, names: {...(current?.names ?? {}), [entryId]: name}, removed: false});
  }, [groups, push]);

  const removeFromGroup = useCallback((key: string, entryId: string) => {
    const current = groups.find((item) => item.key === key);
    if (!current) return;
    const members = current.members.filter((member) => member !== entryId);
    push({kind: 'group', key, id: current.id, name: current.name, members, names: current.names, removed: members.length === 0});
  }, [groups, push]);

  const renameGroup = useCallback((key: string, name: string) => {
    const current = groups.find((item) => item.key === key);
    if (!current || !name.trim() || name.trim() === current.name) return;
    push({kind: 'group', key, id: current.id, name: name.trim(), members: current.members, names: current.names, removed: false});
  }, [groups, push]);

  const removeGroup = useCallback((key: string) => {
    const current = groups.find((item) => item.key === key);
    if (!current) return;
    push({kind: 'group', key, id: current.id, name: current.name, members: current.members, names: current.names, removed: true});
  }, [groups, push]);

  const setExcluded = useCallback((entryId: string, name: string, reason: string | null) => {
    const text = reason === null ? null : reason.normalize('NFC').trim() || null;
    if ((excluded.get(entryId)?.reason ?? null) === text) return;
    push({kind: 'exclude', entry_id: entryId, name, reason: text});
  }, [excluded, push]);

  const relate = useCallback((source: {id: string; name: string}, target: {id: string; name: string}, relation: Relation) => {
    if (source.id === target.id) return;
    push({kind: 'relate', source: source.id, target: target.id, relation, source_name: source.name, target_name: target.name, removed: false});
  }, [push]);

  const unrelate = useCallback((key: string) => {
    const current = relations.find((item) => item.key === key);
    if (!current) return;
    push({kind: 'relate', source: current.source, target: current.target, relation: current.relation, source_name: current.source_name, target_name: current.target_name, removed: true});
  }, [relations, push]);

  const setLabel = useCallback((entryId: string, name: string, label: string | null) => {
    const text = label === null ? null : label.normalize('NFC').trim() || null;
    const before = labels[entryId] ?? null;
    if (before === text) return;
    commit({draft: [...stored.draft, {kind: 'label', entry_id: entryId, name, label: text, before}], undone: []});
  }, [labels, stored.draft, commit]);

  const stageMove = useCallback((entryId: string, name: string, from: {id: string; name: string}, to: {id: string; name: string}) => {
    commit({
      draft: [...stored.draft, {kind: 'move', entry_id: entryId, name, from_id: from.id, from_name: from.name, to_id: to.id, to_name: to.name}],
      undone: [],
    });
  }, [stored.draft, commit]);

  const plan = stored.plan ?? {id: null, name: `Plan of ${new Date().toLocaleDateString()}`};
  const renamePlan = useCallback((name: string) => commit({...stored, plan: {id: plan.id, name}}), [stored, plan.id, commit]);
  const checkPlan = useCallback(async (actor: string, runId: string) => {
    const moveList = [...moves.entries()].map(([entry, move]) => ({entry_id: entry, to_folder_id: move.to_id}));
    const response = await fetch('/api/workspace/plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
      body: JSON.stringify({plan_id: plan.id ?? undefined, name: plan.name, actor, run_id: runId, moves: moveList}),
    });
    const saved = (await response.json().catch(() => ({}))) as {plan_id?: string; error?: string};
    if (!response.ok || !saved.plan_id) throw new Error(saved.error ?? `the plan was not kept (${response.status})`);
    commit({...stored, plan: {id: saved.plan_id, name: plan.name}});
    const checked = await fetch(`/api/v1/explorer/plan?id=${encodeURIComponent(saved.plan_id)}`);
    const body = (await checked.json().catch(() => ({}))) as PlanCheck & {error?: {message?: string}};
    if (!checked.ok) throw new Error(body.error?.message ?? `the plan could not be checked (${checked.status})`);
    return body;
  }, [moves, plan, stored, commit]);

  const undo = useCallback(() => {
    const last = stored.draft[stored.draft.length - 1];
    if (!last) return;
    commit({...stored, draft: stored.draft.slice(0, -1), undone: [...stored.undone, last]});
  }, [stored, commit]);

  const redo = useCallback(() => {
    const last = stored.undone[stored.undone.length - 1];
    if (!last) return;
    commit({...stored, draft: [...stored.draft, last], undone: stored.undone.slice(0, -1)});
  }, [stored, commit]);

  const discard = useCallback((kind?: 'label' | 'move' | 'layer') => {
    const layered = (edit: Edit) => edit.kind === 'group' || edit.kind === 'exclude' || edit.kind === 'relate';
    commit({draft: kind ? stored.draft.filter((edit) => (kind === 'layer' ? !layered(edit) : edit.kind !== kind)) : [], undone: []});
  }, [stored.draft, commit]);

  const save = useCallback(async (actor: string) => {
    const batch = [...pending].map((id) => ({entry_id: id, label: labels[id] ?? null}));
    if (!batch.length && !layerEdits.length) return {written: 0};
    const response = await fetch('/api/review/answer', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
      body: JSON.stringify({labels: batch, overlay: layerEdits, actor}),
    });
    const body = (await response.json().catch(() => ({}))) as {written?: number; error?: string};
    if (!response.ok) throw new Error(body.error ?? `nothing was written (${response.status})`);
    await refresh();
    // Saved: the labels and the layer leave the draft; a staged move stays, being a plan.
    commit({draft: stored.draft.filter((edit) => edit.kind === 'move'), undone: []});
    return {written: body.written ?? batch.length + layerEdits.length};
  }, [pending, labels, layerEdits, refresh, stored.draft, commit]);

  const revert = useCallback(async (target: string, payload: string, actor: string) => {
    const said = JSON.parse(payload) as Record<string, unknown>;
    const [, kind, ...parts] = target.split(':');
    const edit: Record<string, unknown> = kind === 'group'
      ? {kind, id: parts[0], name: said.name, members: membersSaid(said)}
      : kind === 'exclude'
        ? {kind, entry: parts[0], reason: said.reason}
        : {kind, source: parts[0], target: parts[1], relation: parts[2]};
    if (said.note) edit.note = said.note;
    const response = await fetch('/api/review/answer', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
      body: JSON.stringify({overlay: [edit], actor}),
    });
    const body = (await response.json().catch(() => ({}))) as {error?: string};
    if (!response.ok) throw new Error(body.error ?? `the revert was not written (${response.status})`);
    await refresh();
  }, [refresh]);

  return {
    saved,
    layer,
    groups,
    groupsOf,
    excluded,
    relations,
    layerPending: layerEdits.length,
    addToGroup,
    removeFromGroup,
    renameGroup,
    removeGroup,
    setExcluded,
    relate,
    unrelate,
    revert,
    draft: stored.draft,
    labels,
    pending,
    moves,
    plan,
    renamePlan,
    checkPlan,
    canUndo: stored.draft.length > 0,
    canRedo: stored.undone.length > 0,
    setLabel,
    stageMove,
    undo,
    redo,
    discard,
    refresh,
    save,
    error,
  };
}

export async function readDecider(): Promise<string | null> {
  try {
    const response = await fetch('/api/decider');
    const body = (await response.json()) as {decider?: string | null};
    return body.decider ?? null;
  } catch {
    return null;
  }
}

export async function setDecider(name: string): Promise<void> {
  const response = await fetch('/api/decider', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
    body: JSON.stringify({decider: name}),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {error?: string};
    throw new Error(body.error ?? `the name was not kept (${response.status})`);
  }
}
