// Timeline as rows: in a thread, every file with every time the run holds for it, each with its
// source; in activity, every row with its files dated and undated, its earliest and latest period
// and its busiest. The same content as the drawing, for a keyboard or a screen reader.

import type {ReactElement} from 'react';

import type {TimelineResponse} from '../../api';
import {count} from '../../format';
import {AXIS_ZONE, longTime, parseTime, periodLabel} from './time';

export function TimelineTable({data, selected, labels, onSelect}: {
  data: TimelineResponse;
  selected: string;
  labels: Record<string, string>;
  onSelect(id: string): void;
}): ReactElement {
  if (data.reading === 'thread' && data.thread) {
    return (
      <div className="ix-table-wrap">
        <table className="ix-table">
          <caption className="ix-sr">Every file in the thread and every time the run holds for it.</caption>
          <thead><tr><th scope="col">File</th><th scope="col" className="ix-num">Hops</th><th scope="col">Placed by</th><th scope="col">Every time, by source</th></tr></thead>
          <tbody>
            {data.thread.nodes.map((node) => (
              <tr key={node.id} aria-selected={selected === node.id}>
                <th scope="row">
                  <button type="button" className="ix-link" style={{padding: 0}} onClick={() => onSelect(node.id)}>
                    <span className="ix-grow" style={labels[node.id] ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>{labels[node.id] ?? node.name}</span>
                  </button>
                  <div className="ix-path">{node.path}</div>
                </th>
                <td className="ix-num">{node.hop}</td>
                <td>{node.placed ? `${node.placed.source}: ${node.placed.precision === 'instant' ? longTime(parseTime(node.placed.at) ?? 0) : node.placed.at}${node.placed.conflicting ? ' (published as conflicting)' : ''}` : 'undated under this source'}</td>
                <td>{node.marks.map((mark) => `${mark.source} ${mark.until ? `${mark.at} to ${mark.until}` : mark.precision === 'instant' ? longTime(parseTime(mark.at) ?? 0) : mark.at} (${mark.detail ?? 'no detail'}${mark.conflicting ? ', published as conflicting' : ''})`).join('; ') || 'none'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  const activity = data.activity;
  const edges = activity?.bins.edges.map((edge) => parseTime(edge) ?? 0) ?? [];
  const unit = activity?.bins.unit ?? 'day';
  return (
    <div className="ix-table-wrap">
      <table className="ix-table">
        <caption className="ix-sr">Each row's files over time under the chosen source, in {AXIS_ZONE} periods.</caption>
        <thead><tr><th scope="col">Row</th><th scope="col" className="ix-num">Files</th><th scope="col" className="ix-num">Dated</th><th scope="col" className="ix-num">Undated</th><th scope="col">First period ({AXIS_ZONE})</th><th scope="col">Last period ({AXIS_ZONE})</th><th scope="col">Busiest period ({AXIS_ZONE})</th></tr></thead>
        <tbody>
          {activity?.tracks.map((track, index) => {
            const counts = activity.counts[data.source]?.[index] ?? [];
            const dated = counts.reduce((sum, value) => sum + value, 0);
            const first = counts.findIndex((value) => value > 0);
            const last = counts.length - 1 - [...counts].reverse().findIndex((value) => value > 0);
            const busiest = counts.reduce((best, value, bin) => (value > (counts[best] ?? 0) ? bin : best), 0);
            return (
              <tr key={track.id} aria-selected={selected === track.id}>
                <th scope="row">
                  <button type="button" className="ix-link" style={{padding: 0}} onClick={() => onSelect(track.kind === 'folder' ? track.id : '')}>
                    <span className="ix-grow">{labels[track.id] ?? track.name}</span>
                  </button>
                  {track.name_from ? <div className="ix-note">named for {track.name_from}{track.name_to && track.name_to !== track.name_from ? ` to ${track.name_to}` : ''}</div> : null}
                </th>
                <td className="ix-num">{count(track.files)}</td>
                <td className="ix-num">{count(dated)}</td>
                <td className="ix-num">{count(activity.undated[data.source]?.[index] ?? 0)}</td>
                <td>{first >= 0 ? periodLabel(edges[first], unit) : ''}</td>
                <td>{first >= 0 && last >= 0 ? periodLabel(edges[last], unit) : ''}</td>
                <td>{dated ? `${periodLabel(edges[busiest], unit)} (${count(counts[busiest])})` : ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
