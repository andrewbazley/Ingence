// Lineage as name families: the sentence that says what was drawn and what was left out, then the
// drawing, or the families as rows, or one family opened as a Grid of its files.

import type {ReactElement} from 'react';

import type {FamilyNode, LineageFamiliesResponse} from '../../api';
import {count, plural} from '../../format';
import {StateCard} from '../../StateCard';
import {FAMILIES_MOST} from '../../url';
import {FamilyGrid, FamiliesList} from './FamiliesGrid';
import {FamiliesView} from './FamiliesView';
import type {Orientation} from './LineageView';

/**
 * What was drawn, and what the limits left out, in the engine's counts. A name family and a file
 * that has a line but belongs to no family are counted apart: a file is never called a family.
 */
export function truncationWords(data: LineageFamiliesResponse): string {
  const cut = data.truncation;
  const drawnFamilies = data.nodes.filter((node) => node.kind === 'family').length;
  const drawnFiles = data.nodes.length - drawnFamilies;
  const files = cut.files_total;
  const parts = [
    cut.families_total > 0 || files === 0 ? `${count(drawnFamilies)} of ${count(cut.families_total)} ${cut.families_total === 1 ? 'family' : 'families'}` : '',
    files > 0 ? `${count(drawnFiles)} of ${plural(files, 'file')} in no family` : '',
  ].filter(Boolean).join(' and ');
  if (cut.nodes_left_out > 0) {
    const noun = files > 0 ? ' families and files' : '';
    return `${parts} drawn; ${count(cut.nodes_left_out)} more${noun} hold ${plural(cut.lines_to_nodes_not_drawn, 'line')} to these and ${count(cut.lines_among_nodes_not_drawn)} among themselves.`;
  }
  return `${parts} drawn. ${cut.message}`;
}

export function FamiliesPane({data, draw, table, orientation, selected, drawn, onSelect, onOpenNode, onOpenFile, onDraw, onTable, onBack, onPage, onLayoutMeasured}: {
  data: LineageFamiliesResponse;
  draw: 'families' | 'grid';
  table: boolean;
  orientation: Orientation;
  selected: string;
  drawn: number;
  onSelect(id: string): void;
  onOpenNode(node: FamilyNode): void;
  onOpenFile(id: string): void;
  onDraw(limit: number): void;
  onTable(): void;
  onBack(): void;
  onPage(offset: number): void;
  onLayoutMeasured?(milliseconds: number): void;
}): ReactElement {
  if (draw === 'grid' && data.open) {
    return <FamilyGrid data={data} selected={selected} onSelect={onSelect} onBack={onBack} onPage={onPage} onFile={onOpenFile} />;
  }
  if (draw === 'grid' || table) {
    return <FamiliesList data={data} selected={selected} onSelect={onSelect} onOpen={onOpenNode} />;
  }
  if (!data.nodes.length) {
    return (
      <div className="ix-lone" data-lone="families-empty">
        <StateCard eyebrow="Lineage · name families" sentence={data.provenance.message} body={[data.families.message]} />
      </div>
    );
  }
  const more = data.truncation.nodes_left_out > 0 && drawn < FAMILIES_MOST;
  return (
    <div className="ix-families-pane" data-draw="families">
      <p className="ix-families-note" data-families="truncation">
        <span data-families="sentence">{truncationWords(data)}</span>
        {more ? (
          <>
            {' '}
            <button type="button" className="ix-link ix-inline" data-families="more" onClick={() => onDraw(drawn * 2)}>
              Draw {Math.min(FAMILIES_MOST, drawn * 2)}
            </button>
          </>
        ) : null}
        {' · '}
        <button type="button" className="ix-link ix-inline" data-families="table" onClick={onTable}>Table</button>
      </p>
      <div className="ix-families-plot">
        <FamiliesView data={data} orientation={orientation} selected={selected} onSelect={onSelect} onOpen={onOpenNode} onLayoutMeasured={onLayoutMeasured} />
      </div>
    </div>
  );
}
