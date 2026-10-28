// The one inspector beside every mode: what is selected, in full words. A folder's exact path and
// counts, every measure with its denominator, whether a rule reads it as an experiment unit and
// who decided; a file's categories and every time the run holds for it, each with its source;
// a bin's or a gathered group's members, a page at a time.

import {useEffect, useState, type ReactElement} from 'react';

import {api, ApiFailure, type ChildrenResponse, type ClaimCard, type EdgeDetail, type EntryCard, type FilesResponse, type Filters, type LineageFamiliesResponse, type LineageResponse, type MeasureDefinition, type TimelineResponse, type SectorExperiment, type ValueCard, type WheelResponse, type WheelSector} from './api';
import {filterText, sectorFilters, sectorPath} from './arrangement';
import {categoryPaints, swatchStyle} from './encoding';
import {bytes, count, percent, plural, standing, when} from './format';
import {FileGlyph, FolderGlyph, StackGlyph, TagGlyph} from './icons';
import type {MeasureKey, Mode} from './url';
import {EDGE_WORDS, openEndWords} from './modes/lineage/LineageView';
import {KIND_WORDS as FAMILY_LINE_WORDS} from './modes/lineage/FamiliesView';
import {AXIS_ZONE, longTime, parseTime, periodLabel} from './modes/timeline/time';
import {LabelEditor, LayerEditor, StateBadge} from './HumanUi';
import {RELATION_WORDS, type Relation} from './human';
import type {HumanLayer} from './human';

/** The format a file's card states: every format read, and a conflict said, never settled. */
function formatWords(card: EntryCard): ReactElement | string {
  const read = card.formats.map((value) => value.replace(/_/g, ' '));
  if (card.format_conflicting) {
    return (
      <span style={{color: 'var(--ix-problem)'}} title="Its format conclusion was published as conflicting (a rule that withdraws it fired), or two format families were read: the run does not settle its kind.">
        readings conflict: {read.join('; ') || 'no format settled'}
      </span>
    );
  }
  if (read.length > 1) return read.join('; ');
  return card.format ? card.format.replace(/_/g, ' ') : 'not concluded';
}

/** A root's own path is recorded as '.' or empty: said in words. */
export function rootless(path: string): string {
  return path === '.' ? '' : path;
}

export type Selection =
  | {type: 'none'}
  | {type: 'sector'; id: string}
  | {type: 'entry'; id: string}
  | {type: 'bin'; sectorId: string; start: number; n: number}
  | {type: 'edge'; id: string}
  | {type: 'group'; id: string}
  | {type: 'aggregate'; id: string}
  | {type: 'tbin'; track: number; bin: number}
  | {type: 'overlay'; id: string}
  | {type: 'family'; id: string}
  | {type: 'quotient'; id: string};

export function selectionToParam(selection: Selection): string {
  switch (selection.type) {
    case 'sector':
    case 'entry':
    case 'group':
    case 'aggregate':
    case 'overlay':
    case 'family':
    case 'quotient':
      return selection.id;
    case 'edge':
      return `edge:${selection.id}`;
    case 'tbin':
      return `tbin:${selection.track}:${selection.bin}`;
    case 'bin':
      return `bin:${selection.sectorId}:${selection.start}:${selection.n}`;
    default:
      return '';
  }
}

export function selectionFromParam(text: string, data: WheelResponse | null): Selection {
  if (!text) return {type: 'none'};
  if (text.startsWith('bin:')) {
    const parts = text.split(':');
    const n = Number(parts.pop());
    const start = Number(parts.pop());
    const sectorId = parts.slice(1).join(':');
    if (Number.isFinite(n) && Number.isFinite(start)) return {type: 'bin', sectorId, start, n};
    return {type: 'none'};
  }
  if (text.startsWith('edge:')) return {type: 'edge', id: text.slice(5)};
  if (text.startsWith('group:')) return {type: 'group', id: text};
  if (text.startsWith('agg:')) return {type: 'aggregate', id: text};
  if (text.startsWith('overlay:')) return {type: 'overlay', id: text};
  if (text.startsWith('family:')) return {type: 'family', id: text};
  if (text.startsWith('quotient:')) return {type: 'quotient', id: text};
  const bin = /^tbin:(\d+):(\d+)$/.exec(text);
  if (bin) return {type: 'tbin', track: Number(bin[1]), bin: Number(bin[2])};
  // A value or a gathered group is a sector of the Wheel's own, never an entry to look up.
  if (/^(v|more):/.test(text) || data?.sectors.some((sector) => sector.id === text)) return {type: 'sector', id: text};
  return {type: 'entry', id: text};
}

interface InspectorProps {
  human: HumanLayer;
  /** Counts up each time a person asks to edit the selection's label (the L key). */
  labelStart: number;
  mode: Mode;
  data: WheelResponse | null;
  lineage: LineageResponse | null;
  /** The name families Lineage draws, where it is drawn as families or a grid. */
  families?: LineageFamiliesResponse | null;
  /** Open one family as a Grid. */
  onOpenFamily?(id: string): void;
  timeline?: TimelineResponse | null;
  measures: MeasureDefinition[];
  selection: Selection;
  measure: MeasureKey;
  run: string;
  labels: Record<string, string>;
  onSelect(selection: Selection): void;
  onRecenter(id: string): void;
  onRefocus(id: string): void;
  onOpen(id: string): void;
  /** The page's filters (an arranged Wheel's drill-downs), each `axis=value`. */
  where?: string[];
  onDrill?(index: number): void;
  /** The selection seen over time (the Timeline, with its files). */
  onOverTime?(): void;
  /** Start proposing a relation from an entry: the next file or folder chosen is its other end. */
  onRelate?(source: {id: string; name: string}, relation: Relation): void;
}

export function Inspector(props: InspectorProps): ReactElement {
  const {data, lineage, selection, mode} = props;
  let body: ReactElement;
  if (selection.type === 'sector' && data) {
    const index = data.sectors.findIndex((item) => item.id === selection.id);
    const sector = index >= 0 ? data.sectors[index] : undefined;
    body = !sector ? <p className="ix-note">Not in this drawing.</p>
      : data.arrangement && index > 0 ? <ValueSectorCard {...props} data={data} sector={sector} index={index} />
      : <SectorCard {...props} data={data} sector={sector} />;
  } else if (selection.type === 'bin' && data) {
    body = <BinCard {...props} data={data} selection={selection} />;
  } else if (selection.type === 'sector' && /^(v|more):/.test(selection.id)) {
    body = <p className="ix-note">Reading…</p>;
  } else if (selection.type === 'entry' || selection.type === 'sector') {
    body = <EntryPanel {...props} id={selection.id} />;
  } else if (selection.type === 'edge') {
    body = <EdgePanel {...props} id={selection.id} />;
  } else if (selection.type === 'group') {
    body = <GroupPanel {...props} id={selection.id} />;
  } else if (selection.type === 'overlay') {
    body = <StatementPanel {...props} id={selection.id} />;
  } else if (selection.type === 'aggregate') {
    body = <AggregatePanel {...props} id={selection.id} />;
  } else if (selection.type === 'family') {
    body = <FamilyPanel {...props} id={selection.id} />;
  } else if (selection.type === 'quotient') {
    body = <QuotientPanel {...props} id={selection.id} />;
  } else if (selection.type === 'tbin' && props.timeline) {
    body = <TimelineBinPanel {...props} timeline={props.timeline} track={selection.track} bin={selection.bin} />;
  } else if (mode === 'timeline' && props.timeline) {
    body = <TimelineOverview timeline={props.timeline} />;
  } else if (mode === 'lineage' && props.families) {
    body = <FamiliesOverview families={props.families} />;
  } else if (mode === 'lineage' && lineage) {
    body = <LineageOverview {...props} lineage={lineage} />;
  } else if (data) {
    body = <Overview {...props} data={data} />;
  } else {
    body = <p className="ix-note">Reading…</p>;
  }
  return (
    <aside className="ix-inspector" aria-label="What is selected" aria-live="polite">
      {body}
    </aside>
  );
}

/** One of a person's statements, chosen in a drawing: what it says, its state, its members. */
function StatementPanel({id, human, onSelect, data, lineage}: InspectorProps & {id: string}): ReactElement {
  const statement = human.layer?.statements.find((item) => item.target === id);
  const relation = human.relations.find((item) => item.key === id);
  if (!statement && !relation) return <p className="ix-note">This statement is not in the Record, nor waiting to be saved.</p>;
  const kind = statement?.kind ?? 'relate';
  return (
    <>
      <p className="ix-label">{kind === 'relate' ? 'A relation you proposed' : kind === 'group' ? 'Your topic' : 'Set aside by you'}</p>
      <h2 className="ix-human-text">
        {kind === 'relate' && relation ? `${relation.source_name} ${RELATION_WORDS[relation.relation]} ${relation.target_name}` : statement?.name ?? statement?.reason}
      </h2>
      <StateBadge pending={relation?.pending} statement={statement ?? null} />
      <p className="ix-note">A person's statement, never the engine's. {statement?.agreement ? `The run read: ${statement.agreement.words}.` : ''}</p>
      {statement ? <p className="ix-note">{statement.actor}, {when(statement.at)}{statement.note ? ` · ${statement.note}` : ''}</p> : null}
      <ul className="ix-list">
        {(statement?.members ?? []).map((member) => (
          <li key={member.id}>
            <button type="button" className="ix-link" onClick={() => onSelect({type: 'entry', id: member.id})} disabled={member.kind === 'gone'}>
              {member.kind === 'folder' ? <FolderGlyph /> : <FileGlyph kind="unknown" />}
              <span className="ix-grow">{member.name ?? `${member.name_then ?? member.id} (not in this run)`}</span>
            </button>
          </li>
        ))}
      </ul>
      {relation ? <div className="ix-actions"><button type="button" className="ix-btn ix-quiet" onClick={() => human.unrelate(relation.key)}>Withdraw it</button></div> : null}
      {statement?.kind === 'group' ? (() => {
        // The folders a drawing holds now: where a person's group could be moved in the plan.
        const folders = new Map<string, string>();
        for (const sector of data?.sectors ?? []) if (sector.kind === 'folder') folders.set(sector.id, sector.name);
        for (const folder of lineage?.folders ?? []) folders.set(folder.id, folder.name);
        const members = statement.members.filter((member) => member.kind !== 'gone');
        return folders.size && members.length ? (
          <label className="ix-select">
            <span className="ix-sr">Move the topic's members in the plan to</span>
            <select value="" onChange={(event) => {
              const to = event.target.value;
              if (!to) return;
              for (const member of members) {
                if (member.id === to) continue;
                const parent = (member.path ?? '').split('/').slice(-2, -1)[0] ?? '';
                human.stageMove(member.id, member.name ?? member.id, {id: '', name: parent || 'where it is'}, {id: to, name: folders.get(to) ?? to});
              }
            }}>
              <option value="">Move its {members.length} member{members.length === 1 ? '' : 's'} in the plan to…</option>
              {[...folders.entries()].map(([folderId, name]) => <option key={folderId} value={folderId}>{name}</option>)}
            </select>
          </label>
        ) : null;
      })() : null}
    </>
  );
}

function LineageOverview({lineage}: InspectorProps & {lineage: LineageResponse}): ReactElement {
  const focus = lineage.nodes.find((node) => node.is_focus);
  const drawn = lineage.provenance.drawn;
  return (
    <>
      <p className="ix-label">Lineage of</p>
      <h2>{focus?.name ?? lineage.focus}</h2>
      <div className="ix-path">{focus?.path ?? ''}</div>
      <div className="ix-banner">{lineage.provenance.message}</div>
      <dl className="ix-kv">
        <dt>Files drawn</dt><dd>{count(lineage.nodes.filter((node) => node.kind === 'file').length)}</dd>
        <dt>Published derivations</dt><dd>{count(drawn.published_derivation ?? 0)}</dd>
        <dt>Published mentions</dt><dd>{count(drawn.published_mention ?? 0)}</dd>
        <dt>Proposed</dt><dd>{lineage.include_proposals ? count((drawn.proposed_derivation ?? 0) + (drawn.proposed_mention ?? 0)) : 'not shown'}</dd>
        <dt>Groups</dt><dd>{lineage.include_groups ? count(lineage.groups.length) : 'not shown'}</dd>
        <dt>Open ends</dt><dd>{count(lineage.open_ends.length)}</dd>
      </dl>
      <p className="ix-caps">{lineage.truncation.message}</p>
      <h3>Reading the lineage</h3>
      <p className="ix-note">
        Raw material at the top, what came of it below; the legend names each line. “+3” expands a branch, the
        round button collapses it, and a double-click makes a file the focus.
      </p>
    </>
  );
}

function TimelineOverview({timeline}: {timeline: TimelineResponse}): ReactElement {
  const chosen = timeline.sources.find((item) => item.key === timeline.source);
  return (
    <>
      <p className="ix-label">{timeline.reading === 'thread' ? 'The dated thread of' : 'Activity over time in'}</p>
      <h2>{timeline.focus.name}</h2>
      <div className="ix-path">{rootless(timeline.focus.path) || 'the root folder'}</div>
      <div className="ix-banner"><strong>Placed by {chosen?.label}:</strong> {chosen?.meaning}.</div>
      {timeline.notes.map((note) => <p key={note} className="ix-note">{note}</p>)}
      <h3>Files with a time, by source</h3>
      <table className="ix-cats">
        <tbody>
          {timeline.sources.map((source) => (
            <tr key={source.key} title={source.meaning}>
              <td>{source.label}</td>
              <td className="ix-num">{count(timeline.dated[source.key] ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="ix-note">Each source answers “when” on its own, and they can disagree (a copy resets a modification time). None is merged into another.</p>
      <h3>Runs</h3>
      <ul className="ix-list">
        {timeline.runs.map((run) => (
          <li key={run.run_id} style={{padding: '3px 0'}}>{run.label}{run.is_read ? ' · shown here' : ''}{run.is_current ? ' · current' : ''}</li>
        ))}
      </ul>
      <p className="ix-note">Every mark is read from the run shown; runs are never combined.</p>
    </>
  );
}

function TimelineBinPanel({timeline, track, bin, run, labels, onSelect}: InspectorProps & {timeline: TimelineResponse; track: number; bin: number}): ReactElement {
  const activity = timeline.activity;
  const row = activity?.tracks[track];
  const edges = activity?.bins.edges ?? [];
  const from = edges[bin];
  const to = edges[bin + 1];
  if (!activity || !row || !from || !to) return <p className="ix-note">This period is not in the current drawing.</p>;
  const value = activity.counts[timeline.source]?.[track]?.[bin] ?? 0;
  const folder = row.kind === 'folder' ? row.id : row.kind === 'direct' ? timeline.focus.id : '';
  return (
    <>
      <p className="ix-label">A period</p>
      <h2>{plural(value, 'file')} in {labels[row.id] ?? row.name}</h2>
      <p className="ix-note">{periodLabel(parseTime(from) ?? 0, activity.bins.unit)} (a {activity.bins.unit} in {AXIS_ZONE}), placed by {timeline.sources.find((item) => item.key === timeline.source)?.label}.</p>
      {folder ? (
        <FileList folder={folder} scope={row.kind === 'direct' ? 'direct' : 'beneath'} start={0} n={value} run={run} labels={labels} onSelect={onSelect}
          window={{source: timeline.source, from, to}} />
      ) : <p className="ix-note">In folders gathered together: choose a row of its own to list them.</p>}
    </>
  );
}

function EdgePanel({id, run, lineage, onSelect, onOpen}: InspectorProps & {id: string}): ReactElement {
  const line = lineage?.edges.find((edge) => edge.id === id);
  const openEnd = lineage?.open_ends.find((end) => end.edge_id === id);
  if (id.startsWith('member:')) return <ClaimPanel id={id} run={run} onSelect={onSelect} />;
  if (id.startsWith('agg:')) {
    return (
      <>
        <p className="ix-label">Lines gathered together</p>
        <h2>{count(line?.lines ?? 0)} {EDGE_WORDS[line?.kind ?? ''] ?? 'lines'}</h2>
        <p className="ix-note">Too many to draw one by one: gathered by folder and kind of line. Each keeps its own rule and evidence.</p>
        <div className="ix-actions"><button type="button" className="ix-btn" onClick={() => onOpen(id)}>Show each file</button></div>
      </>
    );
  }
  return <EdgeDetailPanel id={id} run={run} line={line} openEnd={openEnd} />;
}

function EdgeDetailPanel({id, run, line, openEnd}: {id: string; run: string; line?: LineageResponse['edges'][number]; openEnd?: LineageResponse['open_ends'][number]}): ReactElement {
  const [detail, setDetail] = useState<EdgeDetail | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setError('');
    api.edge(id, run || undefined, controller.signal).then(setDetail).catch((failure: unknown) => {
      if ((failure as Error).name !== 'AbortError') setError((failure as Error).message);
    });
    return () => controller.abort();
  }, [id, run]);
  if (error) return <p className="ix-warn">{error}</p>;
  if (!detail) return <p className="ix-note">Reading…</p>;
  const proposed = detail.kind === 'proposed_candidate';
  return (
    <>
      <p className="ix-label">{proposed ? 'A proposal' : detail.kind === 'indexed_containment' ? 'Indexed directory' : 'A published line'}</p>
      <h2>{EDGE_WORDS[line?.kind ?? ''] ?? detail.primitive}</h2>
      <p>{detail.reading}</p>
      {openEnd ? <div className="ix-warn">Its other end, <span className="ix-mono">{openEnd.named}</span>, is {openEndWords(openEnd.reason)}: recorded, not drawn.</div> : null}
      <dl className="ix-kv">
        <dt>Relation</dt><dd>{detail.primitive}{detail.specialization ? ` (${detail.specialization})` : ''}</dd>
        <dt>Status</dt><dd>{detail.status}</dd>
        <dt>Rule</dt><dd className="ix-mono">{detail.rule_id ? `${detail.rule_id} v${detail.rule_version}` : 'the census'}</dd>
        <dt>Level</dt><dd title={detail.tier_definition ?? ''}>{detail.tier ?? ''}</dd>
        {detail.reference_resolution ? <><dt>Resolved</dt><dd>{detail.reference_resolution.replace(/_/g, ' ')}</dd></> : null}
        <dt>Run</dt><dd className="ix-mono">{detail.run_id}</dd>
      </dl>
      {line?.decision ? (
        <div className="ix-banner"><strong>{line.decision.actor}</strong> {line.decision.kind === 'confirm' ? 'confirmed' : line.decision.kind === 'reject' ? 'rejected' : line.decision.kind} it, {when(line.decision.at)}.</div>
      ) : null}
      {detail.rule_statement ? <details><summary>The rule, as the run recorded it</summary><p className="ix-note">{detail.rule_statement}</p></details> : null}
      <p className="ix-note">{detail.definition}</p>
      {detail.support.length ? (
        <>
          <h3>Support</h3>
          <ul className="ix-list">{detail.support.map((item, index) => <li key={index} style={{padding: '3px 0'}}>{item.name.replace(/_/g, ' ')} <span className="ix-note">({item.family.replace(/_/g, ' ')}, {plural(item.observations.length, 'record')})</span></li>)}</ul>
        </>
      ) : null}
      {detail.defeaters.length ? (
        <>
          <h3>What would retract it</h3>
          <ul className="ix-list">{detail.defeaters.map((item, index) => <li key={index} style={{padding: '3px 0'}}>{item.reason} <span className="ix-note">{item.fired ? '(present)' : '(not present)'}</span></li>)}</ul>
        </>
      ) : null}
      {detail.review ? (
        <div className="ix-actions"><a className="ix-btn" href="#candidates">Decide it on Proposed pairs</a></div>
      ) : null}
      <details><summary>The {plural(detail.records.length, 'record')} it rests on</summary><ul className="ix-list">{detail.records.map((record) => <li key={record} className="ix-mono">{record}</li>)}</ul></details>
    </>
  );
}

function ClaimPanel({id, run, onSelect}: {id: string; run: string; onSelect(selection: Selection): void}): ReactElement {
  const [claim, setClaim] = useState<ClaimCard | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api.claim(id, run || undefined, controller.signal).then(setClaim).catch((failure: unknown) => {
      if ((failure as Error).name !== 'AbortError') setError((failure as Error).message);
    });
    return () => controller.abort();
  }, [id, run]);
  if (error) return <p className="ix-warn">{error}</p>;
  if (!claim) return <p className="ix-note">Reading…</p>;
  return (
    <>
      <p className="ix-label">Membership of a group</p>
      <h2>{claim.predicate.replace(/^groups\./, '').replace(/_/g, ' ')}</h2>
      <p className="ix-note">A rule put this file in a group: the files belong together, which never says one was made from another.</p>
      <dl className="ix-kv">
        <dt>Rule</dt><dd className="ix-mono">{claim.rule_id} v{claim.rule_version}</dd>
        <dt>Level</dt><dd>{claim.tier}</dd>
        <dt>Standing</dt><dd title={claim.resolution_status}>{standing(claim.resolution_status)}</dd>
      </dl>
      {claim.decision ? <div className="ix-banner"><strong>{claim.decision.actor}</strong> {claim.decision.kind} it, {when(claim.decision.at)}{claim.decision.note ? `: ${claim.decision.note}` : ''}.</div> : null}
      {claim.rule_statement ? <details open><summary>The rule</summary><p className="ix-note">{claim.rule_statement}</p></details> : null}
      <details><summary>The group's key, as the rule wrote it</summary><p className="ix-mono" style={{wordBreak: 'break-all'}}>{claim.object_reference}</p></details>
      <div className="ix-actions"><button type="button" className="ix-btn ix-quiet" onClick={() => onSelect({type: 'entry', id: claim.subject})}>The file</button></div>
    </>
  );
}

function GroupPanel({id, lineage, labels, onSelect}: InspectorProps & {id: string}): ReactElement {
  const group = lineage?.groups.find((item) => item.id === id);
  if (!group || !lineage) return <p className="ix-note">This group is not in the current drawing.</p>;
  const names = new Map(lineage.nodes.map((node) => [node.id, labels[node.id] ?? node.name]));
  const memberships = lineage.edges.filter((edge) => edge.kind === 'group_member' && edge.target === id);
  return (
    <>
      <p className="ix-label">A group</p>
      <h2 style={{display: 'flex', gap: 8, alignItems: 'center'}}><StackGlyph />{group.label}</h2>
      <p className="ix-note">Files a rule reads as belonging together, which never says one was made from another.</p>
      <dl className="ix-kv">
        <dt>Members in this run</dt><dd>{count(group.members_total)}</dd>
        <dt>Drawn here</dt><dd>{count(group.members_shown.length)}</dd>
        <dt>Rule</dt><dd className="ix-mono">{group.rule_id}</dd>
      </dl>
      <h3>Members drawn</h3>
      <ul className="ix-list">
        {memberships.map((edge) => (
          <li key={edge.id}>
            <button type="button" className="ix-link" onClick={() => onSelect({type: 'edge', id: edge.id})}>
              <span className="ix-grow">{names.get(edge.source) ?? edge.source}</span>
              <span className="ix-num">why</span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

/** The name families as a whole, before one is chosen: what formed them and what was left out. */
function FamiliesOverview({families}: {families: LineageFamiliesResponse}): ReactElement {
  return (
    <>
      <p className="ix-label">Name families</p>
      <h2>{families.folder_name ?? 'Every file with a line'}</h2>
      <p className="ix-note">{families.families.message}</p>
      <p className="ix-note">{families.provenance.message}</p>
      <dl className="ix-kv">
        <dt>Families</dt><dd>{count(families.nodes.filter((node) => node.kind === 'family').length)} of {count(families.truncation.families_total)} drawn</dd>
        <dt>Files in no family</dt><dd>{count(families.nodes.filter((node) => node.kind === 'file').length)} of {count(families.truncation.files_total)} drawn</dd>
        <dt>Edges</dt><dd>{count(families.edges.length)} of {count(families.truncation.edges_total)} among them</dd>
        <dt>Lines in all</dt><dd>{count(families.provenance.lines)}</dd>
        <dt>Lines inside a family</dt><dd>{count(families.provenance.lines_inside_families)}</dd>
        {families.provenance.lines_to_open_ends ? <><dt>Not drawn</dt><dd>{count(families.provenance.lines_to_open_ends)} that name no file of this run</dd></> : null}
        {families.families.rule_ids.length ? <><dt>Rule</dt><dd className="ix-mono">{families.families.rule_ids.join(', ')}</dd></> : null}
      </dl>
      <p className="ix-note">Choose a family to read what it holds. Its files open as a grid.</p>
    </>
  );
}

/** A name family, chosen in the drawing: what it holds, in words with their denominators. */
function FamilyPanel({id, families, onOpenFamily}: InspectorProps & {id: string}): ReactElement {
  const node = families?.nodes.find((item) => item.id === id);
  if (!node || !families) return <p className="ix-note">This family is not in the current drawing.</p>;
  return (
    <>
      <p className="ix-label">{node.kind === 'family' ? 'A family of files' : 'A file in no family'}</p>
      <h2 style={{display: 'flex', gap: 8, alignItems: 'center'}}><StackGlyph />{node.label}</h2>
      <p className="ix-note">
        {node.kind === 'family'
          ? 'Files whose names agree on every word once the stage words are left out. A family is a way of reading names; it never says one file was made from another.'
          : 'It has a line and belongs to no family, so it stands as itself.'}
      </p>
      <dl className="ix-kv">
        <dt>Files</dt><dd>{count(node.members)}</dd>
        <dt>With a line</dt><dd>{count(node.members_with_lines)} of {count(node.members)}</dd>
        <dt>Lines touching them</dt><dd>{count(node.lines)}</dd>
        <dt>Lines inside it</dt><dd>{count(node.lines_inside)}</dd>
        {node.members_in_conflict ? <><dt>In conflict</dt><dd><span className="ix-dot-problem" aria-hidden="true" />{count(node.members_in_conflict)} more files, not counted above</dd></> : null}
        <dt>Folders</dt><dd>{count(node.folders)}</dd>
        {node.extensions.length ? <><dt>Extensions</dt><dd>{node.extensions.map((item) => item.toUpperCase()).join(', ')}</dd></> : null}
        {node.rule_id ? <><dt>Rule</dt><dd className="ix-mono">{node.rule_id}</dd></> : null}
      </dl>
      {node.members_in_conflict ? (
        <p className="ix-note">The rule that forms families put these files here, but its claim about them is in conflict: another rule defeated it, or a person answered Wrong. They are never collapsed with the rest.</p>
      ) : null}
      {node.examples.length ? (
        <>
          <h3>Some of its files with a line</h3>
          <ul className="ix-list">{node.examples.map((name) => <li key={name} className="ix-mono">{name}</li>)}</ul>
        </>
      ) : null}
      {node.kind === 'family' && families.open?.id !== node.id ? (
        <div className="ix-actions"><button type="button" className="ix-btn" onClick={() => onOpenFamily?.(node.id)}>Read its files as a grid</button></div>
      ) : null}
    </>
  );
}

/** One edge between two families, chosen in the drawing. */
function QuotientPanel({id, families, onSelect}: InspectorProps & {id: string}): ReactElement {
  const edge = families?.edges.find((item) => item.id === id);
  if (!edge || !families) return <p className="ix-note">This line is not in the current drawing.</p>;
  const label = (nodeId: string) => families.nodes.find((item) => item.id === nodeId)?.label ?? nodeId;
  return (
    <>
      <p className="ix-label">Lines between two families</p>
      <h2>{FAMILY_LINE_WORDS[edge.kind] ?? edge.kind}</h2>
      <p className="ix-note">One edge for each kind of line between two nodes; its width is the number of file-to-file lines it stands for. Open a family to read them one by one.</p>
      <dl className="ix-kv">
        <dt>From</dt><dd><button type="button" className="ix-link" onClick={() => onSelect({type: 'family', id: edge.flow_from})}>{label(edge.flow_from)}</button></dd>
        <dt>To</dt><dd><button type="button" className="ix-link" onClick={() => onSelect({type: 'family', id: edge.flow_to})}>{label(edge.flow_to)}</button></dd>
        <dt>Lines</dt><dd>{count(edge.lines)}</dd>
      </dl>
    </>
  );
}

function AggregatePanel({id, lineage, onOpen}: InspectorProps & {id: string}): ReactElement {
  const node = lineage?.nodes.find((item) => item.id === id);
  if (!node) return <p className="ix-note">Not in the current drawing.</p>;
  return (
    <>
      <p className="ix-label">Files gathered together</p>
      <h2 style={{display: 'flex', gap: 8, alignItems: 'center'}}><StackGlyph />{node.name}</h2>
      <div className="ix-path">{node.folder_path ?? ''}</div>
      <p className="ix-note">Too many to draw one by one, so gathered by folder. Show each file draws up to 200.</p>
      <dl className="ix-kv">
        {Object.entries(node.member_kinds).map(([kind, held]) => <span key={kind} style={{display: 'contents'}}><dt>{kind}</dt><dd>{count(held)}</dd></span>)}
        <dt>Extensions</dt><dd>{node.member_extensions.map((item) => item.toUpperCase()).join(', ') || 'none'}</dd>
      </dl>
      <div className="ix-actions"><button type="button" className="ix-btn" onClick={() => onOpen(id)}>Show each file</button></div>
    </>
  );
}

function Overview({data, measure}: InspectorProps & {data: WheelResponse}): ReactElement {
  const totals = data.totals;
  const experiment = totals.experiment;
  return (
    <>
      <p className="ix-label">Nothing selected</p>
      <h2>{data.focus.name}</h2>
      <div className="ix-path">{rootless(data.focus.path) || 'the root folder'}</div>
      {data.arrangement ? (
        <div className="ix-banner">
          Arranged by {data.arrangement.axes.map((axis) => axis.label.toLowerCase()).join(', then ')}.
          {data.arrangement.where.length ? (
            <> Only files whose {data.arrangement.where.map((item) => `${data.arrangement?.axes.find((axis) => axis.key === item.axis)?.label.toLowerCase() ?? item.axis.replace(/^[ts]\./, '').replace(/_/g, ' ')} ${item.negated ? 'is not' : 'is'} {item.label}`).join(' and ')}: {count(totals.files)} of the {count(data.arrangement.files_beneath)} beneath this folder.</>
          ) : null}
        </div>
      ) : null}
      <dl className="ix-kv">
        <dt>Files</dt><dd>{count(totals.files)}</dd>
        <dt>Folders</dt><dd>{count(totals.folders)}</dd>
        <dt>Size</dt><dd>{bytes(totals.bytes)}</dd>
      </dl>
      <h3>Experiment units</h3>
      <UnitCounts experiment={experiment} />
      <h3>Provenance</h3>
      <div className="ix-banner">{totals.provenance}</div>
      <Measures data={data} counts={totals.counts} files={totals.files} active={measure} />
      <h3>Reading the Wheel</h3>
      <p className="ix-note">
        Each wedge is a folder, sized by the files beneath; the center is the folder chosen. An unfilled wedge is
        one the run holds little about, not an empty folder. Click to select, double-click or Enter to center,
        Backspace to go up. Nearness on the page is not evidence.
      </p>
      <p className="ix-caps">{data.caps.message}</p>
    </>
  );
}

function UnitCounts({experiment}: {experiment: SectorExperiment}): ReactElement {
  if (!experiment.units && !experiment.proposed_groups) {
    return <p className="ix-note">No rule reads any folder here as an experiment's folder, and no grouping is proposed.</p>;
  }
  return (
    <dl className="ix-kv">
      <dt>Units a rule supports</dt><dd>{count(experiment.units - experiment.confirmed - experiment.rejected - experiment.conflicting)}</dd>
      <dt>Confirmed by a person</dt><dd>{count(experiment.confirmed)}</dd>
      <dt>Rejected by a person</dt><dd>{count(experiment.rejected)}</dd>
      <dt>Conflicting</dt><dd>{count(experiment.conflicting)}</dd>
      <dt title="Groupings of experiments the engine proposed and nobody approved: proposals, never units.">Proposed groupings</dt>
      <dd>{count(experiment.proposed_groups)}</dd>
    </dl>
  );
}

function Measures({data, counts, files, active}: {data: WheelResponse; counts: Record<string, number[]>; files: number; active: MeasureKey}): ReactElement {
  const ordered = [...data.measures].sort((a, b) => (a.key === active ? -1 : b.key === active ? 1 : 0));
  return (
    <>
      {ordered.map((measure) => (
        <MeasureBlock key={measure.key} measure={measure} counts={counts[measure.key] ?? []} files={files} open={measure.key === active} />
      ))}
    </>
  );
}

function MeasureBlock({measure, counts, files, open}: {measure: MeasureDefinition; counts: number[]; files: number; open: boolean}): ReactElement {
  const paints = categoryPaints(measure);
  return (
    <details open={open}>
      <summary><h3 style={{display: 'inline'}}>{measure.label}</h3></summary>
      <p className="ix-note">{measure.question} Out of {count(files)} {measure.denominator}.</p>
      <div className="ix-bar" role="img" aria-label={paints.map((item, index) => `${item.label} ${percent(counts[index] ?? 0, files)}`).join(', ')}>
        {paints.map((item, index) => (counts[index] ? (
          <span key={item.key} style={{...swatchStyle(item.paint), width: `${(100 * (counts[index] ?? 0)) / Math.max(1, files)}%`}} />
        ) : null))}
      </div>
      <table className="ix-cats">
        <tbody>
          {paints.map((item, index) => (
            <tr key={item.key} title={item.meaning}>
              <td><span className="ix-swatch" style={{...swatchStyle(item.paint), marginRight: 6, verticalAlign: '-2px'}} aria-hidden="true" />{item.label}</td>
              <td className="ix-num">{count(counts[index] ?? 0)}</td>
              <td className="ix-num">{percent(counts[index] ?? 0, files)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function ExperimentBlock({experiment, isMore}: {experiment: SectorExperiment | null | undefined; isMore: boolean}): ReactElement {
  if (!experiment) {
    return <p className="ix-note">No rule reads {isMore ? 'these folders' : 'this folder'} or anything beneath as an experiment's folder.</p>;
  }
  const own = experiment.status !== 'none';
  const words: Record<string, string> = {
    supported: 'A rule reads this folder as one experiment’s folder. Nobody has confirmed it.',
    confirmed: 'A person confirmed that this folder is one experiment’s folder.',
    rejected: 'A person said this folder is not one experiment’s folder.',
    conflicting: 'The rule’s reading of this folder was published as conflicting.',
  };
  return (
    <>
      {own ? (
        <div className={experiment.status === 'conflicting' ? 'ix-warn' : 'ix-banner'}>
          <strong>{experiment.status === 'none' ? '' : `Experiment unit: ${experiment.status}`}</strong>
          {experiment.unit_kind ? ` · holds ${experiment.unit_kind}` : ''}
          <div>{words[experiment.status]}</div>
          {experiment.decided_by ? <div>{experiment.decided_by}, {when(experiment.decided_at)}</div> : null}
          {experiment.rules.length ? <div className="ix-mono">{experiment.rules.join(', ')}</div> : null}
        </div>
      ) : null}
      <UnitCounts experiment={experiment} />
    </>
  );
}

function SectorCard(props: InspectorProps & {data: WheelResponse; sector: WheelSector}): ReactElement {
  const {data, sector, measure, run, labels, onSelect, onRecenter, human, labelStart} = props;
  const isFocus = sector.id === data.focus.id;
  const isMore = sector.kind === 'more';
  const label = labels[sector.id];
  const parentId = isMore ? sector.parent ?? '' : sector.id;
  return (
    <>
      <p className="ix-label">{isFocus ? 'The center' : isMore ? 'Folders gathered together' : 'Folder'}</p>
      <h2 style={{display: 'flex', gap: 8, alignItems: 'center'}}>
        {isMore ? <StackGlyph /> : <FolderGlyph unit={Boolean(sector.experiment && sector.experiment.status !== 'none')} />}
        <span style={label ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>{label ?? sector.name}</span>
      </h2>
      {label ? <p className="ix-note">Your label. Named <strong>{sector.name}</strong> on disk.</p> : null}
      <div className="ix-path">{isMore ? `beneath ${rootless(sector.path) || 'the root folder'}` : rootless(sector.path) || 'the root folder'}</div>
      {!isMore ? <LabelEditor entryId={sector.id} name={sector.name} human={human} start={labelStart} /> : null}
      {!isMore ? <LayerEditor entryId={sector.id} name={sector.name} human={human} onRelate={props.onRelate ? (relation) => props.onRelate?.({id: sector.id, name: sector.name}, relation) : undefined} /> : null}
      <div className="ix-actions">
        {!isFocus && !isMore ? <button type="button" className="ix-btn" onClick={() => onRecenter(sector.id)}>Center the Wheel here</button> : null}
        {!isMore ? (
          <a className="ix-btn ix-quiet" href={`?center=${encodeURIComponent(sector.id)}#graph`}>Neighborhood</a>
        ) : null}
      </div>
      <dl className="ix-kv">
        <dt>Files beneath</dt><dd>{count(sector.files)}</dd>
        {!isMore ? <><dt>Directly inside</dt><dd>{count(sector.direct_files)}</dd></> : null}
        <dt>Folders beneath</dt><dd>{count(sector.folders)}</dd>
        <dt>Size</dt><dd>{bytes(sector.bytes)}</dd>
        {!isMore && sector.direct_folders > sector.children_shown ? (
          <><dt>Child folders</dt><dd>{count(sector.direct_folders)} ({count(sector.children_shown)} drawn alone)</dd></>
        ) : null}
      </dl>
      <h3>Experiment units</h3>
      <ExperimentBlock experiment={sector.experiment} isMore={isMore} />
      <Measures data={data} counts={sector.counts} files={sector.files} active={measure} />
      {isMore ? (
        <>
          <h3>The {count(sector.gathered)} folders gathered here</h3>
          <p className="ix-note">Too small to draw alone at this scale. Largest first; those drawn are marked.</p>
          <ChildList folder={parentId} run={run} drawn={new Set(data.sectors.filter((item) => item.parent === parentId).map((item) => item.id))} labels={labels} onSelect={onSelect} onRecenter={onRecenter} />
        </>
      ) : sector.direct_files ? (
        <>
          <h3>Files directly inside</h3>
          <FileList folder={sector.id} scope="direct" start={0} n={sector.direct_files} run={run} labels={labels} onSelect={onSelect} />
        </>
      ) : null}
    </>
  );
}

/** The names of a sector and the sectors between it and the center, center outward. */
function chainNames(data: WheelResponse, index: number): string[] {
  const names: string[] = [];
  let at: WheelSector | undefined = data.sectors[index];
  while (at && at.parent !== null) {
    names.unshift(at.name);
    const parent: string = at.parent;
    at = data.sectors.find((item) => item.id === parent);
  }
  return names;
}

/** A sector of an arranged Wheel: a value of one axis (or values gathered), with what it holds. */
function ValueSectorCard(props: InspectorProps & {data: WheelResponse; sector: WheelSector; index: number}): ReactElement {
  const {data, sector, index, measure, run, labels, onSelect, onRecenter, human, labelStart, where, onDrill, onOverTime} = props;
  const axis = data.arrangement?.axes.find((item) => item.key === sector.axis);
  const isMore = sector.kind === 'more';
  const steps = sectorPath(data, index);
  const filters = sectorFilters(data, index, where ?? []);
  const folderValue = sector.entry_id && (sector.axis === 'folder' || sector.axis === 'unit') ? sector.entry_id : null;
  const label = folderValue ? labels[folderValue] : undefined;
  const [card, setCard] = useState<ValueCard | null>(null);
  const [error, setError] = useState('');
  const last = steps[steps.length - 1];
  const before = steps.slice(0, -1).map(filterText);
  const askAxis = last ? `${last.axis}${last.axis === 'folder' ? `@${last.level}` : ''}` : '';
  useEffect(() => {
    if (isMore || !last) return undefined;
    const controller = new AbortController();
    setCard(null);
    setError('');
    api.value({focus: data.focus.id, axis: askAxis, value: last.value, where: [...(where ?? []), ...before], run: run || undefined}, controller.signal)
      .then(setCard)
      .catch((failure: unknown) => {
        if ((failure as Error).name !== 'AbortError') setError((failure as Error).message);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by the sector and filters
  }, [data.focus.id, sector.id, askAxis, (where ?? []).join('\n'), run, isMore]);
  const placedHere = sector.leaf === 'direct' ? sector.direct_files : sector.leaf === 'all' ? sector.files : 0;
  return (
    <>
      <p className="ix-label">{isMore ? `${axis?.label ?? 'Values'}: gathered together` : axis?.label ?? 'Value'}</p>
      <h2 style={{display: 'flex', gap: 8, alignItems: 'center'}}>
        {isMore ? <StackGlyph /> : folderValue ? <FolderGlyph unit={sector.axis === 'unit'} /> : <TagGlyph />}
        <span style={label ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>{label ?? sector.name}</span>
      </h2>
      {label ? <p className="ix-note">Your label. Named <strong>{sector.name}</strong> on disk.</p> : null}
      {axis ? <p className="ix-note">{axis.question}.</p> : null}
      {steps.length > 1 ? (
        <div className="ix-path">{chainNames(data, index).join(' › ')}</div>
      ) : folderValue && sector.path ? <div className="ix-path">{rootless(sector.path)}</div> : null}
      {folderValue ? <LabelEditor entryId={folderValue} name={sector.name} human={human} start={labelStart} /> : null}
      {folderValue ? <LayerEditor entryId={folderValue} name={sector.name} human={human} onRelate={props.onRelate ? (relation) => props.onRelate?.({id: folderValue, name: sector.name}, relation) : undefined} /> : null}
      <div className="ix-actions">
        {!isMore && onDrill ? <button type="button" className="ix-btn" onClick={() => onDrill(index)}>Look inside</button> : null}
        {!isMore && onOverTime ? <button type="button" className="ix-btn ix-quiet" onClick={onOverTime}>Over time</button> : null}
        {folderValue ? <button type="button" className="ix-btn ix-quiet" onClick={() => onRecenter(folderValue)}>Center on this folder</button> : null}
      </div>
      <dl className="ix-kv">
        <dt>Files</dt><dd>{count(sector.files)}</dd>
        <dt>{isMore ? 'Folders holding them' : 'In folders'}</dt><dd>{count(sector.folders)}</dd>
        <dt>Size</dt><dd>{bytes(sector.bytes)}</dd>
        {isMore ? <><dt>Values gathered</dt><dd>{count(sector.gathered)}</dd></> : null}
      </dl>
      {card && card.spellings.length && axis ? (
        <>
          <h3>{axis.family === 'spelling' ? 'Read by the Dictionary as' : 'Written in names as'}</h3>
          <ul className="ix-list">
            {card.spellings.map((item) => (
              <li key={item.text} style={{display: 'flex', gap: 8, padding: '3px 0'}}>
                <span className={axis.family === 'spelling' ? '' : 'ix-mono'} style={{flex: 1}}>{item.text}</span>
                <span className="ix-num">{count(item.files)}</span>
              </li>
            ))}
          </ul>
          {card.spellings.length > 1 && axis.family === 'names' ? <p className="ix-note">One sense written {card.spellings.length} ways: arrange by “as spelled” to see where each is used.</p> : null}
        </>
      ) : null}
      {card && card.own_name !== null && card.inherited !== null ? (
        <p className="ix-note">Said in the files’ own names: {count(card.own_name)}; only in a folder’s name above them: {count(card.inherited)}. Read from names, not content.</p>
      ) : null}
      {error ? <p className="ix-warn">{error}</p> : null}
      <h3>Experiment units among its files</h3>
      {sector.experiment ? <UnitCounts experiment={sector.experiment} /> : <p className="ix-note">None of its files lies in a folder a rule reads as an experiment’s folder.</p>}
      <Measures data={data} counts={sector.counts} files={sector.files} active={measure} />
      {card && card.holders.length ? (
        <>
          <h3>Folders holding it</h3>
          <ul className="ix-list">
            {card.holders.map((holder) => (
              <li key={holder.id}>
                <button type="button" className="ix-link" onClick={() => onSelect({type: 'entry', id: holder.id})} onDoubleClick={() => onRecenter(holder.id)} title={holder.path}>
                  <FolderGlyph />
                  <span className="ix-grow" style={labels[holder.id] ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>{labels[holder.id] ?? holder.name}</span>
                  <span className="ix-num">{count(holder.files)}</span>
                </button>
              </li>
            ))}
          </ul>
          {card.folders > card.holders.length ? <p className="ix-note">{count(card.folders - card.holders.length)} more folders hold fewer of its files.</p> : null}
        </>
      ) : null}
      {placedHere ? (
        <>
          <h3>{isMore ? 'Their files' : sector.leaf === 'direct' ? 'Its files not in a ring beyond' : 'Its files'}</h3>
          <FileList folder={data.focus.id} scope="beneath" start={0} n={placedHere} run={run} labels={labels} onSelect={onSelect} filters={filters} />
        </>
      ) : null}
    </>
  );
}

function BinCard({data, selection, run, labels, onSelect, where}: InspectorProps & {data: WheelResponse; selection: {sectorId: string; start: number; n: number}}): ReactElement {
  const index = data.sectors.findIndex((item) => item.id === selection.sectorId);
  const sector = index >= 0 ? data.sectors[index] : undefined;
  if (!sector) return <p className="ix-note">This run of files is not in the current drawing.</p>;
  const name = labels[sector.entry_id ?? sector.id] ?? sector.name;
  if (data.arrangement) {
    const filters = sectorFilters(data, index, where ?? []);
    return (
      <>
        <p className="ix-label">Files at the edge</p>
        <h2>{plural(selection.n, 'file')} in {name}</h2>
        <p className="ix-note">Files {count(selection.start + 1)} to {count(selection.start + selection.n)}, by kind, then whether read, then name.</p>
        <FileList folder={data.focus.id} scope="beneath" start={selection.start} n={selection.n} run={run} labels={labels} onSelect={onSelect} filters={filters} />
      </>
    );
  }
  if (sector.kind === 'more') {
    return (
      <>
        <p className="ix-label">Files at the edge</p>
        <h2>{plural(selection.n, 'file')} in {name}</h2>
        <p className="ix-note">In folders gathered at this scale: choose one, or center lower down, to list them.</p>
      </>
    );
  }
  const scope = sector.leaf === 'direct' ? 'direct' : 'beneath';
  return (
    <>
      <p className="ix-label">Files at the edge</p>
      <h2>{plural(selection.n, 'file')} {scope === 'direct' ? 'directly inside' : 'beneath'} {name}</h2>
      <p className="ix-note">Files {count(selection.start + 1)} to {count(selection.start + selection.n)}, by kind, then whether read, then name.</p>
      <FileList folder={sector.id} scope={scope} start={selection.start} n={selection.n} run={run} labels={labels} onSelect={onSelect} />
    </>
  );
}

const PAGE = 50;

function FileList({folder, scope, start, n, run, labels, onSelect, window, filters}: {
  folder: string; scope: 'direct' | 'beneath'; start: number; n: number; run: string;
  labels: Record<string, string>; onSelect(selection: Selection): void;
  window?: {source: string; from: string; to: string};
  filters?: Filters;
}): ReactElement {
  const filterKey = `${(filters?.where ?? []).join('\n')}|${(filters?.not ?? []).join('\n')}`;
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<FilesResponse | null>(null);
  const [error, setError] = useState('');
  useEffect(() => setOffset(0), [folder, scope, start, n, filterKey]);
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    api.files(folder, scope, start + offset, Math.min(PAGE, n - offset), run || undefined, controller.signal, window, filters)
      .then(setPage)
      .catch((failure: unknown) => {
        if ((failure as Error).name !== 'AbortError') setError((failure as Error).message);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the filters are keyed by their text
  }, [folder, scope, start, n, offset, run, window?.source, window?.from, window?.to, filterKey]);
  if (error) return <p className="ix-warn">{error}</p>;
  if (!page) return <p className="ix-note">Reading…</p>;
  return (
    <>
      <ul className="ix-list">
        {page.files.map((file) => (
          <li key={file.id}>
            <button type="button" className="ix-link" onClick={() => onSelect({type: 'entry', id: file.id})} title={file.path}>
              <FileGlyph kind={file.kind} extension={file.extension} link={file.is_archive_link} />
              <span className="ix-grow" style={labels[file.id] ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>{labels[file.id] ?? file.name}</span>
              <span className="ix-num">{bytes(file.bytes)}</span>
            </button>
          </li>
        ))}
      </ul>
      {n > PAGE ? (
        <div className="ix-pager">
          <button type="button" className="ix-btn ix-quiet" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span>{count(offset + 1)}–{count(Math.min(n, offset + PAGE))} of {count(n)}</span>
          <button type="button" className="ix-btn ix-quiet" disabled={offset + PAGE >= n} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
      ) : null}
    </>
  );
}

function ChildList({folder, run, drawn, labels, onSelect, onRecenter}: {
  folder: string; run: string; drawn: Set<string>; labels: Record<string, string>;
  onSelect(selection: Selection): void; onRecenter(id: string): void;
}): ReactElement {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ChildrenResponse | null>(null);
  const [error, setError] = useState('');
  useEffect(() => setOffset(0), [folder]);
  useEffect(() => {
    const controller = new AbortController();
    api.children(folder, offset, run || undefined, controller.signal).then(setPage).catch((failure: unknown) => {
      if ((failure as Error).name !== 'AbortError') setError((failure as Error).message);
    });
    return () => controller.abort();
  }, [folder, offset, run]);
  if (error) return <p className="ix-warn">{error}</p>;
  if (!page) return <p className="ix-note">Reading…</p>;
  return (
    <>
      <ul className="ix-list">
        {page.children.map((child) => (
          <li key={child.id}>
            <button type="button" className="ix-link" onClick={() => onSelect({type: 'entry', id: child.id})} onDoubleClick={() => onRecenter(child.id)} title={child.path}>
              <FolderGlyph unit={child.unit_status !== 'none'} />
              <span className="ix-grow" style={labels[child.id] ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>
                {labels[child.id] ?? child.name}{drawn.has(child.id) ? ' (drawn)' : ''}
              </span>
              <span className="ix-num">{count(child.files)}</span>
            </button>
          </li>
        ))}
      </ul>
      <div className="ix-pager">
        <button type="button" className="ix-btn ix-quiet" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button>
        <span>{count(page.total ? offset + 1 : 0)}–{count(Math.min(page.total, offset + 50))} of {count(page.total)}</span>
        <button type="button" className="ix-btn ix-quiet" disabled={offset + 50 >= page.total} onClick={() => setOffset(offset + 50)}>Next</button>
      </div>
    </>
  );
}

function EntryPanel({id, run, measure, labels, onRecenter, onRefocus, data, measures: known, human, labelStart, lineage, mode, onRelate}: InspectorProps & {id: string}): ReactElement {
  const [card, setCard] = useState<EntryCard | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setCard(null);
    setError(null);
    api.entry(id, run || undefined, controller.signal).then(setCard).catch((failure: unknown) => {
      if ((failure as Error).name === 'AbortError') return;
      setError(failure instanceof ApiFailure ? failure : new ApiFailure(0, 'error', String(failure)));
    });
    return () => controller.abort();
  }, [id, run]);
  if (error) return <p className="ix-warn">{error.message}</p>;
  if (!card) return <p className="ix-note">Reading…</p>;
  const label = labels[card.id];
  const measures = data?.measures ?? known;
  if (card.entry_type === 'folder') {
    return (
      <>
        <p className="ix-label">Folder</p>
        <h2 style={{display: 'flex', gap: 8, alignItems: 'center'}}><FolderGlyph unit={Boolean(card.experiment && card.experiment.status !== 'none')} />
          <span style={label ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>{label ?? card.name}</span></h2>
        {label ? <p className="ix-note">Your label. Named <strong>{card.name}</strong> on disk.</p> : null}
        <div className="ix-path">{card.path}</div>
        <LabelEditor entryId={card.id} name={card.name} human={human} start={labelStart} />
        <LayerEditor entryId={card.id} name={card.name} human={human} onRelate={onRelate ? (relation) => onRelate({id: card.id, name: card.name}, relation) : undefined} />
        <div className="ix-actions">
          <button type="button" className="ix-btn" onClick={() => onRecenter(card.id)}>Center the Wheel here</button>
          <button type="button" className="ix-btn ix-quiet" onClick={() => onRefocus(card.id)}>Lineage from here</button>
        </div>
        <dl className="ix-kv">
          <dt>Files beneath</dt><dd>{count(card.files)}</dd>
          <dt>Folders beneath</dt><dd>{count(card.folders)}</dd>
          <dt>Size</dt><dd>{bytes(card.bytes)}</dd>
          {card.moved ? <><dt>Moved here</dt><dd>{card.moved.said}</dd></> : null}
        </dl>
        <h3>Experiment units</h3>
        <ExperimentBlock experiment={card.experiment} isMore={false} />
        {data && card.counts ? <Measures data={data} counts={card.counts} files={card.files} active={measure} /> : null}
      </>
    );
  }
  return (
    <>
      <p className="ix-label">File</p>
      <h2 style={{display: 'flex', gap: 8, alignItems: 'center'}}>
        <FileGlyph kind={card.kind ?? 'unknown'} extension={card.extension} link={card.is_archive_link} title={`${card.kind ?? 'unknown'} file`} />
        <span style={label ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>{label ?? card.name}</span>
      </h2>
      {label ? <p className="ix-note">Your label. Named <strong>{card.name}</strong> on disk.</p> : null}
      <div className="ix-path">{card.path}</div>
      <LabelEditor entryId={card.id} name={card.name} human={human} start={labelStart} />
      <LayerEditor entryId={card.id} name={card.name} human={human} onRelate={onRelate ? (relation) => onRelate({id: card.id, name: card.name}, relation) : undefined} />
      {mode === 'lineage' && lineage && lineage.folders.length > 1 ? <PlanMove card={card} lineage={lineage} human={human} /> : null}
      {card.is_archive_link ? (
        <div className="ix-banner">An archive link: its bytes are outside the root{card.link_target ? <>, at <span className="ix-mono">{card.link_target}</span></> : ''}, recorded as written and not followed.</div>
      ) : null}
      <dl className="ix-kv">
        <dt>Format</dt><dd>{formatWords(card)}</dd>
        <dt>Size</dt><dd>{bytes(card.bytes)}</dd>
        {card.moved ? <><dt>Moved here</dt><dd>{card.moved.said}</dd></> : null}
      </dl>
      <h3>What the run holds</h3>
      <table className="ix-cats">
        <tbody>
          {measures.filter((item) => item.key !== 'kind').map((measure) => {
            const key = card.categories?.[measure.key] ?? '';
            const category = measure.categories.find((item) => item.key === key);
            const paint = categoryPaints(measure).find((item) => item.key === key)?.paint;
            return (
              <tr key={measure.key} title={category?.meaning}>
                <td style={{color: 'var(--ix-ink-3)', paddingRight: 8}}>{measure.label}</td>
                <td>{paint ? <span className="ix-swatch" style={{...swatchStyle(paint), marginRight: 6, verticalAlign: '-2px'}} aria-hidden="true" /> : null}{category?.label ?? key}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h3>Times</h3>
      {card.times.length ? (
        <ul className="ix-list">
          {card.times.map((time) => (
            <li key={time.kind} style={{padding: '4px 0'}} title={time.note}>
              <strong>{time.kind.replace('_', ' ')}</strong>{' '}
              {/* In UTC and saying so, as the Timeline beside it: never the reader's zone unnamed. */}
              {time.precision === 'instant' ? (time.at ? longTime(Date.parse(time.at)) : 'unknown') : time.until ? `${time.at} to ${time.until}` : time.at}
              <div className="ix-note" style={{margin: 0}}>from {time.source ?? 'an unstated source'}: {time.note}</div>
            </li>
          ))}
        </ul>
      ) : <p className="ix-note">The run holds no time for this file.</p>}
      <div className="ix-actions">
        <button type="button" className="ix-btn" onClick={() => onRefocus(card.id)}>Lineage from here</button>
        <a className="ix-btn ix-quiet" href={`?center=${encodeURIComponent(card.id)}#graph`}>Neighborhood</a>
      </div>
    </>
  );
}

function PlanMove({card, lineage, human}: {card: EntryCard; lineage: LineageResponse; human: HumanLayer}): ReactElement {
  const node = lineage.nodes.find((item) => item.id === card.id);
  const from = lineage.folders.find((folder) => folder.id === node?.folder_id);
  const planned = human.moves.get(card.id);
  if (!node || !from) return <></>;
  return (
    <div className="ix-label-editor">
      <h3>In the plan</h3>
      {planned ? <p className="ix-note">Would move from <strong>{planned.from_name}</strong> to <span className="ix-human-text">{planned.to_name}</span>. A plan only: nothing on disk moves.</p> : null}
      <label className="ix-select">
        <span className="ix-sr">Move in the plan to</span>
        <select value={planned?.to_id ?? from.id} onChange={(event) => {
          const to = lineage.folders.find((folder) => folder.id === event.target.value);
          if (to) human.stageMove(card.id, card.name, {id: from.id, name: from.name}, {id: to.id, name: to.name});
        }}>
          {lineage.folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.id === from.id ? `${folder.name} (where it is)` : `move to ${folder.name}`}</option>)}
        </select>
      </label>
    </div>
  );
}
