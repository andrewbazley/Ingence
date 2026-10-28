// The human layer's controls: the unsaved count with undo and redo in the toolbar, the review of
// the edits made in this tab before they are saved, and the label editor in the inspector. No
// dialog asks for confirmation of an edit. The review before saving is the one place a person
// looks back.

import {useEffect, useRef, useState, type ReactElement} from 'react';

import type {HumanStatement, PlanCheck} from './api';
import {when} from './format';
import {RELATION_WORDS, readDecider, setDecider, type HumanLayer, type Relation, type SavedLabel} from './human';

export function HumanBar({human, onReview}: {human: HumanLayer; onReview(): void}): ReactElement {
  const unsaved = human.pending.size + human.layerPending;
  const planned = human.moves.size;
  return (
    <div className="ix-human" role="group" aria-label="Your edits">
      <button type="button" className="ix-btn ix-quiet" onClick={human.undo} disabled={!human.canUndo} title="Undo the last edit (Cmd or Ctrl Z)" aria-label="Undo">↶</button>
      <button type="button" className="ix-btn ix-quiet" onClick={human.redo} disabled={!human.canRedo} title="Redo (Shift Cmd or Ctrl Z)" aria-label="Redo">↷</button>
      {!unsaved && !planned && (human.layer?.statements.length ?? 0) > 0 ? (
        <button type="button" className="ix-btn ix-quiet" onClick={onReview} title="What you said in the Record: each topic, exclusion and relation, with its state, its history and a way to say it again">
          Your layer · {human.layer?.statements.length}
        </button>
      ) : null}
      {unsaved || planned ? (
        <button type="button" className="ix-btn ix-human-pill" onClick={onReview} title="Look over your edits, and save the labels to the Record">
          <span aria-hidden="true">✎</span>
          {unsaved ? ` ${unsaved} unsaved edit${unsaved === 1 ? '' : 's'}` : ''}
          {unsaved && planned ? ' ·' : ''}
          {planned ? ` ${planned} in the plan` : ''}
        </button>
      ) : null}
    </div>
  );
}

export function ReviewPanel({human, onClose, runId}: {human: HumanLayer; onClose(): void; runId?: string}): ReactElement {
  const [decider, setDeciderName] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState('');
  const heading = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => {
    readDecider().then(setDeciderName).catch(() => {});
    heading.current?.focus();
  }, []);
  const labelEdits = [...human.pending].map((id) => {
    const last = [...human.draft].reverse().find((edit) => edit.kind === 'label' && edit.entry_id === id);
    return last && last.kind === 'label' ? last : null;
  }).filter((edit): edit is Extract<typeof human.draft[number], {kind: 'label'}> => edit !== null);
  const moves = [...human.moves.entries()].map(([id, move]) => ({id, ...move, name: human.draft.reduce<string>((found, edit) => (edit.kind === 'move' && edit.entry_id === id ? edit.name : found), id)}));

  const save = async () => {
    setBusy(true);
    setSaid('');
    try {
      let actor = decider;
      if (!actor) {
        if (!name.trim()) {
          setSaid('Say who is deciding on this machine first: every label is written under a name.');
          return;
        }
        await setDecider(name.trim());
        actor = name.trim();
        setDeciderName(actor);
      }
      const result = await human.save(actor);
      setSaid(`${result.written} statement${result.written === 1 ? '' : 's'} written to the Record under ${actor}: labels and your layer, each an annotation, none a conclusion of the engine's.`);
    } catch (failure) {
      setSaid((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const exportPlan = () => {
    const plan = {
      kind: 'ingence graph workspace plan',
      note: 'A plan only: nothing on disk was moved and no evidence changed.',
      moves: moves.map((move) => ({entry_id: move.id, name: move.name, from: move.from_name, to: move.to_name, to_folder: move.to_id})),
    };
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([JSON.stringify(plan, null, 2)], {type: 'application/json'}));
    link.download = 'graph-workspace-plan.json';
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return (
    <aside className="ix-review" aria-label="Your edits, before saving" onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}>
      <div className="ix-review-head">
        <h2 ref={heading} tabIndex={-1}>Your edits</h2>
        <button type="button" className="ix-btn ix-quiet" onClick={onClose} aria-label="Close">Close</button>
      </div>
      {!decider && (labelEdits.length || human.layerPending || moves.length) ? (
        <label className="ix-field">
          <span>Who is deciding on this machine</span>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="your name, as the Record should show it" />
        </label>
      ) : decider ? <p className="ix-note">Written under <strong>{decider}</strong>.</p> : null}
      <h3>Labels ({labelEdits.length})</h3>
      {labelEdits.length ? (
        <>
          <p className="ix-note">Saved to the Record as your annotation; the file's name does not change, and nothing earlier is lost.</p>
          <ul className="ix-list">
            {labelEdits.map((edit) => (
              <li key={edit.entry_id} className="ix-diff">
                <span className="ix-mono">{edit.name}</span>
                {edit.label === null ? (
                  <span><del>{human.saved.get(edit.entry_id)?.label ?? edit.before}</del> <em>(label removed)</em></span>
                ) : (
                  <span>
                    {human.saved.get(edit.entry_id) ? <del>{human.saved.get(edit.entry_id)?.label}</del> : null}{' '}
                    <ins>{edit.label}</ins>
                  </span>
                )}
              </li>
            ))}
          </ul>
          <div className="ix-actions">
            <button type="button" className="ix-btn" onClick={save} disabled={busy}>{busy ? 'Writing…' : `Save ${labelEdits.length} label${labelEdits.length === 1 ? '' : 's'}${human.layerPending ? ` and ${human.layerPending} layer edit${human.layerPending === 1 ? '' : 's'}` : ''} to the Record`}</button>
            <button type="button" className="ix-btn ix-quiet" onClick={() => human.discard('label')} disabled={busy}>Discard label edits</button>
          </div>
        </>
      ) : <p className="ix-note">No label waits to be saved.</p>}
      <LayerReview human={human} decider={decider} busy={busy} onSave={save} />
      {said ? <p className="ix-banner" role="status">{said}</p> : null}
      <h3>Plan ({moves.length})</h3>
      {moves.length ? (
        <>
          <p className="ix-note"><strong>A plan only</strong>: nothing on disk moves, and nothing is written to the Record. Drag a file onto a folder in Lineage, or a folder onto another in the Wheel, to add to it.</p>
          <label className="ix-field">
            <span>The plan's name</span>
            <input value={human.plan.name} maxLength={120} onChange={(event) => human.renamePlan(event.target.value)} onKeyDown={(event) => event.stopPropagation()} />
          </label>
          <ul className="ix-list">
            {moves.map((move) => (
              <li key={move.id} className="ix-diff">
                <span className="ix-mono">{move.name}</span>
                <span><del>{move.from_name}</del> → <ins>{move.to_name}</ins></span>
              </li>
            ))}
          </ul>
          <PlanChecker human={human} decider={decider} name={name} runId={runId} onDecider={setDeciderName} />
          <div className="ix-actions">
            <button type="button" className="ix-btn ix-quiet" onClick={exportPlan}>Export the plan</button>
            <button type="button" className="ix-btn ix-quiet" onClick={() => human.discard('move')}>Discard the plan</button>
          </div>
        </>
      ) : <p className="ix-note">No file is staged to move.</p>}
    </aside>
  );
}

export function LabelEditor({entryId, name, human, start}: {entryId: string; name: string; human: HumanLayer; start?: number}): ReactElement {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const [history, setHistory] = useState<Array<{event_id: string; label: string | null; actor: string; at: string}> | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  const shown = human.labels[entryId];
  const saved: SavedLabel | undefined = human.saved.get(entryId);
  const unsaved = human.pending.has(entryId);
  useEffect(() => {
    if (start) {
      setText(human.labels[entryId] ?? '');
      setEditing(true);
    }
  }, [start, entryId, human.labels]);
  useEffect(() => {
    if (editing) input.current?.focus();
  }, [editing]);
  useEffect(() => setHistory(null), [entryId]);
  const done = () => {
    human.setLabel(entryId, name, text.trim() ? text : null);
    setEditing(false);
  };
  return (
    <div className="ix-label-editor">
      <h3>Your label</h3>
      {editing ? (
        <div className="ix-actions" style={{marginTop: 0}}>
          <input ref={input} className="ix-input" value={text} maxLength={200} aria-label={`Label for ${name}`}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') done();
              if (event.key === 'Escape') setEditing(false);
              event.stopPropagation();
            }} />
          <button type="button" className="ix-btn" onClick={done}>Done</button>
        </div>
      ) : (
        <>
          <p style={{margin: '2px 0'}}>
            {shown ? <span className="ix-human-text">{shown}</span> : <span className="ix-note">none; the file is shown by its name</span>}
            {unsaved ? <span className="ix-unsaved" title="Not saved to the Record yet"> ✎ unsaved</span> : null}
          </p>
          <div className="ix-actions" style={{marginTop: 4}}>
            <button type="button" className="ix-btn ix-quiet" onClick={() => { setText(shown ?? ''); setEditing(true); }} title="Give it a label of your own (L)">{shown ? 'Change label' : 'Add a label'}</button>
            {shown ? <button type="button" className="ix-btn ix-quiet" onClick={() => human.setLabel(entryId, name, null)}>Remove label</button> : null}
          </div>
        </>
      )}
      {saved && !unsaved ? (
        <p className="ix-note">
          Written by <strong>{saved.actor}</strong>, {when(saved.at)}, as an annotation in the Record{saved.earlier ? `, after ${saved.earlier} earlier` : ''}.{' '}
          <button type="button" className="ix-link" style={{display: 'inline', padding: 0, width: 'auto'}} onClick={() => {
            fetch(`/api/v1/explorer/labels?id=${encodeURIComponent(entryId)}`).then((response) => response.json())
              .then((body: {history: Array<{event_id: string; label: string | null; actor: string; at: string}>}) => setHistory(body.history)).catch(() => {});
          }}>History</button>
        </p>
      ) : null}
      {history ? (
        <ul className="ix-list">
          {history.map((item) => (
            <li key={item.event_id} style={{padding: '3px 0'}}>
              {item.label === null ? <em>removed</em> : <span className="ix-human-text">{item.label}</span>} · {item.actor}, {when(item.at)} <span className="ix-mono ix-note">{item.event_id}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}


const STATE_WORDS: Record<string, {text: string; title: string}> = {
  draft: {text: '✎ draft', title: 'Not saved: only this tab holds it. Undo removes it; saving writes it to the Record.'},
  recorded: {text: 'recorded', title: 'In the Record, under the name of who said it.'},
  projected: {text: 'drawn', title: 'In the Record, and every item it names is in the run read, so every mode draws it. A statement of yours, not a conclusion of the engine.'},
  confirmed: {text: '✓ a newer run agrees', title: 'A later run, after the files were observed again, agrees by its own evidence where the run you said it against did not.'},
  stale: {text: 'names what this run lacks', title: 'An item it names is not in the run read: moved and not followed, or gone. It stays in the Record.'},
};

/** A statement's state in words: draft, recorded, drawn in this run, confirmed by a newer run, or stale. */
export function StateBadge({state, pending, statement}: {state?: string; pending?: boolean; statement?: HumanStatement | null}): ReactElement {
  const key = pending ? 'draft' : state ?? statement?.state ?? 'recorded';
  const words = STATE_WORDS[key] ?? STATE_WORDS.recorded;
  const agreed = statement?.agreement;
  const standing = agreed?.agrees && agreed.standing ? ` (${agreed.standing}: agreement, not confirmation)` : '';
  const agreement = !pending && agreed ? ` The run read: ${agreed.words}${standing}.` : '';
  return <span className={`ix-state-badge ix-state-${key}`} title={`${words.title}${agreement}`}>{words.text}</span>;
}

const REASONS = ['scratch, not data', 'a duplicate', 'belongs elsewhere', 'a failed attempt'];

/** Your layer for one entry: its groups, whether you set it aside, and relations you proposed. */
export function LayerEditor({entryId, name, human, onRelate}: {
  entryId: string; name: string; human: HumanLayer; onRelate?(relation: Relation): void;
}): ReactElement {
  const [newGroup, setNewGroup] = useState('');
  const [adding, setAdding] = useState(false);
  const [reason, setReason] = useState('');
  const [asking, setAsking] = useState(false);
  const mine = human.groupsOf.get(entryId) ?? [];
  const others = human.groups.filter((group) => !group.members.includes(entryId));
  const excluded = human.excluded.get(entryId);
  const touching = human.relations.filter((item) => item.source === entryId || item.target === entryId);
  return (
    <div className="ix-label-editor ix-layer">
      <h3>Your layer</h3>
      <p className="ix-note" style={{marginTop: 0}}>Saved to the Record under your name, apart from what the run read.</p>
      <div className="ix-layer-row">
        <span className="ix-label">Topics</span>
        {mine.length ? mine.map((group) => (
          <span key={group.key} className="ix-chip ix-human-chip">
            {group.name} <StateBadge pending={group.pending} statement={group.statement} />
            <button type="button" className="ix-chip-btn" onClick={() => human.removeFromGroup(group.key, entryId)} aria-label={`Take ${name} out of the topic ${group.name}`}>×</button>
          </span>
        )) : <span className="ix-note" style={{margin: 0}}>in none of yours</span>}
      </div>
      {adding ? (
        <div className="ix-actions" style={{marginTop: 4}}>
          <input className="ix-input" value={newGroup} placeholder="a new topic's name" aria-label="A new topic's name" maxLength={120} autoFocus
            onChange={(event) => setNewGroup(event.target.value)}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (event.key === 'Enter' && newGroup.trim()) { human.addToGroup({key: null, name: newGroup.trim()}, entryId, name); setNewGroup(''); setAdding(false); }
              if (event.key === 'Escape') setAdding(false);
            }} />
          <button type="button" className="ix-btn" disabled={!newGroup.trim()} onClick={() => { human.addToGroup({key: null, name: newGroup.trim()}, entryId, name); setNewGroup(''); setAdding(false); }}>Make</button>
        </div>
      ) : (
        <div className="ix-actions" style={{marginTop: 4}}>
          {others.length ? (
            <label className="ix-select">
              <span className="ix-sr">Add to one of your topics</span>
              <select value="" onChange={(event) => { const group = others.find((item) => item.key === event.target.value); if (group) human.addToGroup({key: group.key, name: group.name}, entryId, name); }}>
                <option value="">Add to a topic…</option>
                {others.map((group) => <option key={group.key} value={group.key}>{group.name} ({group.members.length})</option>)}
              </select>
            </label>
          ) : null}
          <button type="button" className="ix-btn ix-quiet" onClick={() => setAdding(true)}>New topic…</button>
        </div>
      )}
      <div className="ix-layer-row">
        <span className="ix-label">Set aside</span>
        {excluded ? (
          <>
            <span className="ix-human-text">{excluded.reason}</span> <StateBadge pending={excluded.pending} statement={excluded.statement} />
            <button type="button" className="ix-btn ix-quiet" onClick={() => human.setExcluded(entryId, name, null)}>Set back in</button>
          </>
        ) : asking ? (
          <span className="ix-actions" style={{margin: 0}}>
            {REASONS.map((words) => <button key={words} type="button" className="ix-btn ix-quiet" onClick={() => { human.setExcluded(entryId, name, words); setAsking(false); }}>{words}</button>)}
            <input className="ix-input" value={reason} placeholder="or say why" aria-label="Why it is set aside" maxLength={400} autoFocus
              onChange={(event) => setReason(event.target.value)}
              onKeyDown={(event) => { event.stopPropagation(); if (event.key === 'Enter' && reason.trim()) { human.setExcluded(entryId, name, reason); setReason(''); setAsking(false); } if (event.key === 'Escape') setAsking(false); }} />
          </span>
        ) : (
          <button type="button" className="ix-btn ix-quiet" onClick={() => setAsking(true)} title="Say it is not part of the work, with the reason in words; nothing on disk changes">Set aside…</button>
        )}
      </div>
      <div className="ix-layer-row">
        <span className="ix-label">Relations</span>
        {touching.length ? (
          <ul className="ix-list" style={{width: '100%'}}>
            {touching.map((item) => (
              <li key={item.key} style={{padding: '3px 0', display: 'flex', gap: 6, alignItems: 'baseline', flexWrap: 'wrap'}}>
                <span className="ix-human-text">
                  {item.source === entryId ? `${RELATION_WORDS[item.relation]} ${item.target_name}` : `${item.source_name} ${RELATION_WORDS[item.relation]} this`}
                </span>
                <StateBadge pending={item.pending} statement={item.statement} />
                <button type="button" className="ix-chip-btn" onClick={() => human.unrelate(item.key)} aria-label="Withdraw this relation">×</button>
              </li>
            ))}
          </ul>
        ) : <span className="ix-note" style={{margin: 0}}>none proposed</span>}
      </div>
      {onRelate ? (
        <label className="ix-select">
          <span className="ix-sr">Propose a relation from this to another file or folder</span>
          <select value="" onChange={(event) => { if (event.target.value) onRelate(event.target.value as Relation); }}>
            <option value="">Propose a relation…</option>
            {(Object.keys(RELATION_WORDS) as Relation[]).map((relation) => <option key={relation} value={relation}>this {RELATION_WORDS[relation]}…</option>)}
          </select>
        </label>
      ) : null}
    </div>
  );
}

/** The review's part for your layer: what waits to be saved, and what the Record holds, with its
 * state, its history and a way to say again what was said before (a revert is one more event). */
function LayerReview({human, decider, busy, onSave}: {human: HumanLayer; decider: string | null; busy: boolean; onSave(): void}): ReactElement {
  const [history, setHistory] = useState<{target: string; items: Array<{event_id: string; actor: string; at: string; removed: boolean; said: string; payload: string}>} | null>(null);
  const [said, setSaid] = useState('');
  const pendingGroups = human.groups.filter((group) => group.pending);
  const pendingExcluded = [...human.excluded.entries()].filter(([, item]) => item.pending);
  const pendingRelations = human.relations.filter((item) => item.pending);
  const statements = human.layer?.statements ?? [];
  const showHistory = (target: string) => {
    fetch(`/api/v1/explorer/overlay?target=${encodeURIComponent(target)}`).then((response) => response.json())
      .then((body: {history: Array<{event_id: string; actor: string; at: string; removed: boolean; said: string; payload: string}>}) => setHistory({target, items: body.history}))
      .catch(() => {});
  };
  const describe = (statement: HumanStatement) => statement.kind === 'group'
    ? `topic ${statement.name} (${statement.members.length})`
    : statement.kind === 'exclude'
      ? `${statement.members[0]?.name ?? statement.members[0]?.name_then ?? ''} set aside: ${statement.reason}`
      : `${statement.members[0]?.name ?? statement.members[0]?.name_then} ${RELATION_WORDS[(statement.relation ?? 'derives') as Relation]} ${statement.members[1]?.name ?? statement.members[1]?.name_then}`;
  return (
    <>
      <h3>Your layer ({human.layerPending} to save)</h3>
      {human.layerPending ? (
        <>
          <ul className="ix-list">
            {pendingGroups.map((group) => <li key={group.key} className="ix-diff"><span>topic <ins>{group.name}</ins></span><span className="ix-note">{group.members.length} member{group.members.length === 1 ? '' : 's'}: {group.members.map((id) => group.names[id] ?? id).slice(0, 4).join(', ')}{group.members.length > 4 ? '…' : ''}</span></li>)}
            {pendingExcluded.map(([id, item]) => <li key={id} className="ix-diff"><span className="ix-mono">{id}</span><span>set aside: <ins>{item.reason}</ins></span></li>)}
            {pendingRelations.map((item) => <li key={item.key} className="ix-diff"><span>{item.source_name} <ins>{RELATION_WORDS[item.relation]}</ins> {item.target_name}</span></li>)}
          </ul>
          <p className="ix-note">Saved to the Record as your annotation{decider ? ` under ${decider}` : ''}; none changes a file, a name or a conclusion, and nothing said is lost.</p>
          <div className="ix-actions">
            <button type="button" className="ix-btn" onClick={onSave} disabled={busy}>{busy ? 'Writing…' : 'Save to the Record'}</button>
            <button type="button" className="ix-btn ix-quiet" onClick={() => human.discard('layer')} disabled={busy}>Discard layer edits</button>
          </div>
        </>
      ) : <p className="ix-note">Nothing of your layer waits to be saved.</p>}
      <h3>In the Record ({statements.length})</h3>
      {statements.length ? (
        <>
          <p className="ix-note">{human.layer?.counts.projected ?? 0} drawn in this run · {human.layer?.counts.confirmed ?? 0} confirmed by a newer run · {human.layer?.counts.stale ?? 0} naming what this run lacks</p>
          <ul className="ix-list">
            {statements.map((statement) => (
              <li key={statement.target} style={{padding: '4px 0'}}>
                <div style={{display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'baseline'}}>
                  <span className="ix-human-text">{describe(statement)}</span>
                  <StateBadge statement={statement} />
                </div>
                <div className="ix-note" style={{margin: 0}}>{statement.actor}, {when(statement.at)}{statement.earlier ? ` · ${statement.earlier} earlier` : ''}{statement.agreement ? ` · the run read: ${statement.agreement.words}` : ''}</div>
                <button type="button" className="ix-link" style={{display: 'inline', width: 'auto', padding: 0}} onClick={() => showHistory(statement.target)}>History</button>
                {history && history.target === statement.target ? (
                  <ul className="ix-list">
                    {history.items.map((item, index) => (
                      <li key={item.event_id} style={{padding: '2px 0'}}>
                        {item.removed ? <em>{item.said}</em> : <span className="ix-human-text">{item.said}</span>} · {item.actor}, {when(item.at)}
                        {!item.removed && index < history.items.length - 1 && decider ? (
                          <button type="button" className="ix-btn ix-quiet" onClick={() => {
                            human.revert(statement.target, item.payload, decider).then(() => { setSaid('Said again, as one more event.'); showHistory(statement.target); }).catch((failure: Error) => setSaid(failure.message));
                          }}>Say this again</button>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
          {said ? <p className="ix-note" role="status">{said}</p> : null}
        </>
      ) : <p className="ix-note">No topic, exclusion or relation of yours is in the Record yet.</p>}
    </>
  );
}


const OUTCOME_WORDS: Record<string, {glyph: string; words: string}> = {
  ok: {glyph: '✓', words: 'fine'},
  warn: {glyph: '!', words: 'look at it'},
  refuse: {glyph: '✕', words: 'would be refused'},
  unknown: {glyph: '?', words: 'not known here'},
};

/** Keep the staged moves as a plan in the workspace and read what it would do: each move's
 * checks, the folders it touches before and after, and why applying is not available. */
function PlanChecker({human, decider, name, runId, onDecider}: {
  human: HumanLayer; decider: string | null; name: string; runId?: string; onDecider(name: string): void;
}): ReactElement {
  const [check, setCheck] = useState<PlanCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async () => {
    setBusy(true);
    setError('');
    try {
      let actor = decider;
      if (!actor) {
        if (!name.trim()) {
          setError('Say who is deciding on this machine first: a plan is kept under a name.');
          return;
        }
        await setDecider(name.trim());
        actor = name.trim();
        onDecider(actor);
      }
      if (!runId) throw new Error('the run is not known yet');
      setCheck(await human.checkPlan(actor, runId));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="ix-plan-check">
      <div className="ix-actions">
        <button type="button" className="ix-btn" onClick={run} disabled={busy}>{busy ? 'Checking…' : human.plan.id ? 'Keep and check again' : 'Keep and check the plan'}</button>
        <button type="button" className="ix-btn" disabled title={check?.apply.why ?? 'Applying a plan is not available in this build.'}>Apply (not available)</button>
      </div>
      {error ? <p className="ix-warn">{error}</p> : null}
      {check ? (
        <>
          <p className="ix-banner" role="status">
            Kept as <strong>{check.name}</strong>, version {check.version}. {check.message}
          </p>
          <ul className="ix-list">
            {check.moves.map((move) => (
              <li key={move.entry_id} className={`ix-plan-move ix-plan-${move.outcome}`}>
                <div><span className={`ix-plan-glyph ix-glyph-${move.outcome}`} aria-hidden="true">{OUTCOME_WORDS[move.outcome]?.glyph}</span> <span className="ix-mono">{move.name}</span> <span className="ix-note">({OUTCOME_WORDS[move.outcome]?.words})</span></div>
                <div className="ix-note" style={{margin: 0}}><del>{move.from_path}</del> → <ins>{move.to_path || move.to_folder_path}</ins>{move.kind === 'folder' ? ` · with ${move.files} file${move.files === 1 ? '' : 's'} and ${move.folders - 1} folder${move.folders - 1 === 1 ? '' : 's'} beneath` : ''}</div>
                <details>
                  <summary>Checks</summary>
                  <ul className="ix-list">
                    {move.checks.map((item) => (
                      <li key={item.check} style={{padding: '2px 0'}}>
                        <span className={`ix-plan-glyph ix-glyph-${item.outcome}`} aria-hidden="true">{OUTCOME_WORDS[item.outcome]?.glyph}</span> {item.words}
                      </li>
                    ))}
                  </ul>
                  {move.links.length ? (
                    <>
                      <p className="ix-note">May need editing:</p>
                      <ul className="ix-list">
                        {move.links.map((link) => <li key={`${link.entry_id}-${link.why}`}><span className="ix-mono">{link.path}</span> <span className="ix-note">{link.why}</span></li>)}
                      </ul>
                    </>
                  ) : null}
                </details>
              </li>
            ))}
          </ul>
          {check.diff.length ? (
            <>
              <h3>Folders before and after</h3>
              <ul className="ix-list">
                {check.diff.map((folder) => (
                  <li key={folder.folder_id} className="ix-diff">
                    <span className="ix-mono">{folder.path || 'the root'}</span>
                    <span className="ix-note">{folder.before} → {folder.after} items</span>
                    {folder.removed.length ? <span><del>{folder.removed.join(', ')}</del></span> : null}
                    {folder.added.length ? <span><ins>{folder.added.join(', ')}</ins></span> : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <p className="ix-note">{check.apply.why}</p>
        </>
      ) : null}
    </div>
  );
}
