// The Wheel as rows: every sector drawn, in tree order, with its counts, the chosen measure in
// words and numbers, and whether it is an experiment unit. The same content as the drawing, for a
// keyboard, a screen reader, or anybody who would rather read numbers.

import type {ReactElement} from 'react';

import type {WheelResponse} from '../../api';
import {bytes, count, percent} from '../../format';
import type {MeasureKey} from '../../url';

interface WheelTableProps {
  data: WheelResponse;
  measure: MeasureKey;
  selectedSector: number | null;
  labels: Record<string, string>;
  onPick(index: number): void;
  onRecenter(id: string): void;
  onDrill(index: number): void;
}

function treeOrder(data: WheelResponse): number[] {
  const children = new Map<string, number[]>();
  data.sectors.forEach((sector, index) => {
    if (index === 0 || sector.parent === null) return;
    const held = children.get(sector.parent) ?? [];
    held.push(index);
    children.set(sector.parent, held);
  });
  const order: number[] = [];
  const walk = (index: number) => {
    order.push(index);
    const sector = data.sectors[index];
    if (sector.kind === 'more') return;
    for (const kid of children.get(sector.id) ?? []) walk(kid);
  };
  walk(0);
  return order;
}

export function WheelTable({data, measure, selectedSector, labels, onPick, onRecenter, onDrill}: WheelTableProps): ReactElement {
  const definition = data.measures.find((item) => item.key === measure) ?? data.measures[0];
  const order = treeOrder(data);
  const axes = new Map((data.arrangement?.axes ?? []).map((axis) => [axis.key, axis.label]));
  const arranged = data.arrangement ? ` arranged by ${data.arrangement.axes.map((axis) => axis.label).join(', then ')}` : '';
  return (
    <div className="ix-table-wrap">
      <table className="ix-table">
        <caption className="ix-sr">
          {`The Wheel of ${data.focus.name}${arranged} as a table: ${count(order.length)} rows, each a sector drawn or a group gathered together. ${definition.label}: ${definition.question}`}
        </caption>
        <thead>
          <tr>
            <th scope="col">{data.arrangement ? 'Sector' : 'Folder'}</th>
            <th scope="col" className="ix-num">Files</th>
            <th scope="col" className="ix-num">Folders</th>
            <th scope="col" className="ix-num">Size</th>
            <th scope="col">{definition.label}</th>
            <th scope="col">Experiment unit</th>
            <th scope="col"><span className="ix-sr">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {order.map((index) => {
            const sector = data.sectors[index];
            const counts = sector.counts[definition.key] ?? [];
            const parts = definition.categories
              .map((category, place) => ({label: category.label, value: counts[place] ?? 0}))
              .filter((item) => item.value > 0)
              .map((item) => `${item.label} ${count(item.value)} (${percent(item.value, sector.files)})`);
            const experiment = sector.experiment;
            const label = labels[sector.entry_id ?? sector.id];
            const axis = sector.axis ? axes.get(sector.axis) : undefined;
            return (
              <tr key={sector.id} aria-selected={selectedSector === index}>
                <th scope="row" style={{paddingLeft: 8 + 16 * sector.ring, fontWeight: index === 0 ? 700 : 400}}>
                  <button type="button" className="ix-link" onClick={() => onPick(index)} style={{padding: 0}}>
                    <span className="ix-grow" style={label ? {fontStyle: 'italic', color: 'var(--ix-human)'} : undefined}>
                      {label ?? sector.name}
                    </span>
                  </button>
                  {label ? <span className="ix-note"> named {sector.name}</span> : null}
                  <div className="ix-path">
                    {sector.kind === 'more'
                      ? `${count(sector.gathered)} ${sector.axis && sector.axis !== 'folder' ? 'values' : 'folders'} gathered${sector.axis ? '' : `, in ${sector.path || 'the root'}`}`
                      : sector.kind === 'value' ? `${axis ?? sector.axis}${sector.path ? ` · ${sector.path}` : ''}` : sector.path || '(root)'}
                  </div>
                </th>
                <td className="ix-num">{count(sector.files)}</td>
                <td className="ix-num">{count(sector.folders)}</td>
                <td className="ix-num">{bytes(sector.bytes)}</td>
                <td>{parts.join('; ') || 'no files'}</td>
                <td>
                  {experiment && experiment.status !== 'none' ? `${experiment.status}${experiment.unit_kind ? ` (${experiment.unit_kind})` : ''}` : ''}
                  {experiment && experiment.units - (experiment.status !== 'none' ? 1 : 0) > 0 ? ` ${count(experiment.units - (experiment.status !== 'none' ? 1 : 0))} beneath` : ''}
                  {experiment && experiment.proposed_groups ? `; ${count(experiment.proposed_groups)} proposed groupings` : ''}
                </td>
                <td>
                  {sector.kind === 'folder' && index !== 0 ? (
                    <button type="button" className="ix-btn ix-quiet" onClick={() => onRecenter(sector.id)}>Center here</button>
                  ) : null}
                  {sector.kind === 'value' ? (
                    <button type="button" className="ix-btn ix-quiet" onClick={() => onDrill(index)}>Look inside</button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
