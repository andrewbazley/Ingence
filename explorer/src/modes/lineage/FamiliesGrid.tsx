// Lineage's families as rows: the families themselves, and one family opened to its files.
//
// The Grid is what a family holds: each file's name, its folder, the lines that lead to it and the
// lines that lead from it, a hundred at a time with the rest counted. Nothing here is a drawing:
// it is the same content a keyboard or a screen reader reaches, and the way to a single file.

import type {ReactElement} from 'react';

import type {FamilyNode, LineageFamiliesResponse} from '../../api';
import {count, plural} from '../../format';
import {GRID_PAGE} from '../../url';
import {KIND_WORDS} from './FamiliesView';

/** The families (and the lone files) as rows; a row opens the family as a Grid. */
export function FamiliesList({data, selected, onSelect, onOpen}: {
  data: LineageFamiliesResponse;
  selected: string;
  onSelect(id: string): void;
  onOpen(node: FamilyNode): void;
}): ReactElement {
  const names = new Map(data.nodes.map((node) => [node.id, node.label]));
  return (
    <div className="ix-table-wrap" data-view="families-list" tabIndex={0} role="region" aria-label="Name families and the lines between them">
      <table className="ix-table" data-table="families">
        <caption className="ix-sr">Name families, the ones with most lines first. Choose one to read its files as a grid.</caption>
        <thead>
          <tr>
            <th scope="col">Family or file</th>
            <th scope="col" className="ix-num">Files</th>
            <th scope="col" className="ix-num">With a line</th>
            <th scope="col" className="ix-num">Lines</th>
            <th scope="col" className="ix-num">Lines inside</th>
            <th scope="col" className="ix-num">In conflict</th>
            <th scope="col" className="ix-num">Folders</th>
          </tr>
        </thead>
        <tbody>
          {data.nodes.map((node) => (
            <tr key={node.id} aria-selected={selected === node.id} data-node={node.id}>
              <th scope="row">
                <button type="button" className="ix-link" style={{padding: 0}} onClick={() => (onSelect(node.id), onOpen(node))}
                  title={node.kind === 'family' ? 'Read its files as a grid' : 'Center the lineage on this file'}>
                  {node.label}
                </button>
                <div className="ix-path">{node.kind === 'family' ? 'a family of files whose names share these words' : `a file in no family \u00b7 ${node.path ?? ''}`}</div>
              </th>
              <td className="ix-num">{count(node.members)}</td>
              <td className="ix-num">{count(node.members_with_lines)}</td>
              <td className="ix-num">{count(node.lines)}</td>
              <td className="ix-num">{count(node.lines_inside)}</td>
              <td className="ix-num">{node.members_in_conflict ? <span className="ix-dot-problem" aria-hidden="true" /> : null}{count(node.members_in_conflict)}</td>
              <td className="ix-num">{count(node.folders)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="ix-table" style={{marginTop: 18}} data-table="family-lines">
        <caption className="ix-sr">Lines between the families drawn, one row for each kind of line.</caption>
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">Line</th>
            <th scope="col">To</th>
            <th scope="col" className="ix-num">Lines</th>
          </tr>
        </thead>
        <tbody>
          {data.edges.map((edge) => (
            <tr key={edge.id} aria-selected={selected === edge.id}>
              <td>{names.get(edge.flow_from) ?? edge.flow_from}</td>
              <td>
                <button type="button" className="ix-link" style={{padding: 0}} onClick={() => onSelect(edge.id)}>
                  {KIND_WORDS[edge.kind] ?? edge.kind}
                </button>
              </td>
              <td>{names.get(edge.flow_to) ?? edge.flow_to}</td>
              <td className="ix-num">{count(edge.lines)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One family opened: its files, a page at a time, the rest counted. */
export function FamilyGrid({data, selected, onSelect, onBack, onPage, onFile}: {
  data: LineageFamiliesResponse;
  selected: string;
  onSelect(id: string): void;
  onBack(): void;
  onPage(offset: number): void;
  /** Center the lineage on one file (as Files). */
  onFile(id: string): void;
}): ReactElement | null {
  const open = data.open;
  if (!open) return null;
  const first = open.members_offset + 1;
  const last = open.members_offset + open.members.length;
  return (
    <div className="ix-table-wrap" data-draw="grid" data-family={open.id} tabIndex={0} role="region" aria-label={`The files of the family ${open.label}`}>
      <div className="ix-grid-head">
        <nav aria-label="Where this grid is" className="ix-grid-crumbs">
          <button type="button" className="ix-btn ix-quiet" onClick={onBack}>All families</button>
          <span className="ix-crumb-sep" aria-hidden="true">{'›'}</span>
          <strong>{open.label}</strong>
        </nav>
        <p className="ix-note" data-grid="counts">
          {open.members.length ? `Files ${count(first)} to ${count(last)} of ${count(open.members_total)}` : `${plural(open.members_total, 'file')}`}
          {open.members_left_out ? `; ${count(open.members_left_out)} more not listed` : ''}
          {open.members_in_conflict ? ` · ${count(open.members_in_conflict)} more in conflict with the family, not listed here` : ''}
          {` · ${plural(open.lines_total, 'line')} touch them`}
        </p>
      </div>
      <table className="ix-table" data-table="family-members">
        <caption className="ix-sr">The files of the family {open.label}: lines in lead to the file, lines out lead from it.</caption>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Folder</th>
            <th scope="col" className="ix-num">Lines in</th>
            <th scope="col" className="ix-num">Lines out</th>
          </tr>
        </thead>
        <tbody>
          {open.members.map((member) => (
            <tr key={member.id} aria-selected={selected === member.id} data-member={member.id}>
              <th scope="row">
                <button type="button" className="ix-link" style={{padding: 0}} onClick={() => onSelect(member.id)} onDoubleClick={() => onFile(member.id)}
                  title="Read this file's record">
                  {member.name}
                </button>
              </th>
              <td className="ix-mono ix-path">{member.folder_path ?? ''}</td>
              <td className="ix-num">{count(member.lines_in)}</td>
              <td className="ix-num">{count(member.lines_out)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="ix-grid-pages">
        <button type="button" className="ix-btn" disabled={open.members_offset <= 0}
          onClick={() => onPage(Math.max(0, open.members_offset - GRID_PAGE))}>Earlier {GRID_PAGE}</button>
        <button type="button" className="ix-btn" disabled={open.members_left_out <= 0}
          onClick={() => onPage(open.members_offset + GRID_PAGE)}>Next {Math.min(GRID_PAGE, open.members_left_out) || GRID_PAGE}</button>
      </div>
    </div>
  );
}
