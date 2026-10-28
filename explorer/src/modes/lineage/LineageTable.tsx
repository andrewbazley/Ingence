// Lineage as rows: every file drawn, by how far it lies from the focus, and every line with its
// kind and its rule. The same content as the drawing, for a keyboard or a screen reader.

import type {ReactElement} from 'react';

import type {LineageResponse} from '../../api';
import {count} from '../../format';
import {EDGE_WORDS, openEndWords} from './LineageView';

export function LineageTable({data, selected, labels, onSelect, onRefocus}: {
  data: LineageResponse;
  selected: string;
  labels: Record<string, string>;
  onSelect(id: string): void;
  onRefocus(id: string): void;
}): ReactElement {
  const names = new Map(data.nodes.map((node) => [node.id, labels[node.id] ?? node.name]));
  const groups = new Map(data.groups.map((group) => [group.id, group.label]));
  const nodes = [...data.nodes].sort((a, b) => a.hop - b.hop || a.name.localeCompare(b.name));
  return (
    <div className="ix-table-wrap">
      <table className="ix-table">
        <caption className="ix-sr">Files in the lineage, by distance from the focus: negative is upstream, positive downstream.</caption>
        <thead>
          <tr>
            <th scope="col" className="ix-num">Hops</th>
            <th scope="col">File</th>
            <th scope="col">Folder</th>
            <th scope="col" className="ix-num">Not drawn up</th>
            <th scope="col" className="ix-num">Not drawn down</th>
            <th scope="col"><span className="ix-sr">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((node) => (
            <tr key={node.id} aria-selected={selected === node.id}>
              <td className="ix-num">{node.hop}</td>
              <th scope="row">
                <button type="button" className="ix-link" style={{padding: 0}} onClick={() => onSelect(node.id)}>
                  <span className="ix-grow" style={labels[node.id] ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>
                    {names.get(node.id)}{node.is_focus ? ' (focus)' : ''}
                  </span>
                </button>
                <div className="ix-path">{node.kind === 'aggregate' ? `${count(node.members)} files gathered` : node.path}</div>
              </th>
              <td>{node.folder_name ?? ''}</td>
              <td className="ix-num">{count(node.hidden_up)}</td>
              <td className="ix-num">{count(node.hidden_down)}</td>
              <td>{!node.is_focus && node.kind !== 'aggregate' ? <button type="button" className="ix-btn ix-quiet" onClick={() => onRefocus(node.id)}>Focus here</button> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="ix-table" style={{marginTop: 18}}>
        <caption className="ix-sr">Lines in the lineage.</caption>
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">Line</th>
            <th scope="col">To</th>
            <th scope="col">Rule</th>
          </tr>
        </thead>
        <tbody>
          {data.edges.map((edge) => (
            <tr key={edge.id} aria-selected={selected === `edge:${edge.id}`}>
              <td>{names.get(edge.source) ?? edge.source}</td>
              <td>
                <button type="button" className="ix-link" style={{padding: 0}} onClick={() => onSelect(edge.kind === 'group_member' ? edge.target : edge.kind === 'person_relation' ? edge.id : `edge:${edge.id}`)}>
                  {EDGE_WORDS[edge.kind] ?? edge.kind}{edge.lines > 1 ? ` (${count(edge.lines)} lines)` : ''}
                </button>
              </td>
              <td>{names.get(edge.target) ?? groups.get(edge.target) ?? edge.target}</td>
              <td className="ix-mono">{edge.rule_id ? `${edge.rule_id} v${edge.rule_version}` : ''}</td>
            </tr>
          ))}
          {data.open_ends.map((end) => (
            <tr key={end.edge_id}>
              <td>{names.get(end.at) ?? end.at}</td>
              <td>{EDGE_WORDS[end.kind] ?? end.kind}</td>
              <td>names <span className="ix-mono">{end.named}</span>, {openEndWords(end.reason)}</td>
              <td />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
