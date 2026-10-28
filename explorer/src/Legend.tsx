// The compact legend beneath the drawing: the chosen measure's categories, the kinds on the band,
// and the unit marks. The drawing is meant to read without it; it is here as the backup, and
// every category's meaning is one hover or focus away in its title.

import type {ReactElement} from 'react';

import type {MeasureDefinition} from './api';
import {categoryPaints, swatchStyle} from './encoding';

export function WheelLegend({measure, kinds}: {measure: MeasureDefinition; kinds: MeasureDefinition | undefined}): ReactElement {
  return (
    <div className="ix-legend" aria-label="Legend">
      <span className="ix-label" title={`${measure.question} Out of: ${measure.denominator}.`}>{measure.label}</span>
      {categoryPaints(measure).map((category) => (
        <span className="ix-key" key={category.key} title={category.meaning}>
          <span className="ix-swatch" style={swatchStyle(category.paint)} aria-hidden="true" />
          {category.label}
        </span>
      ))}
      {kinds && measure.key !== 'kind' ? (
        <>
          <span className="ix-label" title="The thin band inside the perimeter: each run of files in its main format family.">Band</span>
          {categoryPaints(kinds).map((category) => (
            <span className="ix-key" key={category.key} title={category.meaning}>
              <span className="ix-swatch" style={{...swatchStyle(category.paint), width: 8, height: 12, borderRadius: 2}} aria-hidden="true" />
              {category.label}
            </span>
          ))}
        </>
      ) : null}
      <span className="ix-label">Units</span>
      <span className="ix-key" title="A rule reads this folder as one experiment's folder; nobody has confirmed it. Drawn as a bar along the wedge's outer edge.">
        <svg width="18" height="12" aria-hidden="true"><rect x="1" y="1" width="16" height="10" rx="1.5" fill="var(--ix-track)" /><path d="M1 2.5h16" stroke="var(--ix-ink-2)" strokeWidth="3" /></svg>
        supported
      </span>
      <span className="ix-key" title="A person confirmed the rule's reading in the Record.">
        <svg width="18" height="12" aria-hidden="true"><rect x="1" y="1" width="16" height="10" rx="1.5" fill="var(--ix-track)" /><path d="M1 3h16" stroke="var(--ix-ink)" strokeWidth="4.5" /></svg>
        confirmed
      </span>
      <span className="ix-key" title="The rule's reading was published as conflicting.">
        <svg width="18" height="12" aria-hidden="true"><rect x="1" y="1" width="16" height="10" rx="1.5" fill="var(--ix-track)" /><path d="M1 2.5h16" stroke="var(--ix-problem)" strokeWidth="3.5" strokeDasharray="4 2.5" /></svg>
        conflicting
      </span>
      <span className="ix-key" title="Units lie beneath this folder: a bar along the wedge's inner edge.">
        <svg width="18" height="12" aria-hidden="true"><rect x="1" y="1" width="16" height="10" rx="1.5" fill="var(--ix-track)" /><path d="M1 9.5h16" stroke="var(--ix-ink-3)" strokeWidth="2.5" /></svg>
        units beneath
      </span>
      <span className="ix-key" title="Only groupings the engine proposed lie beneath: proposals, never units.">
        <svg width="18" height="12" aria-hidden="true"><rect x="1" y="1" width="16" height="10" rx="1.5" fill="var(--ix-track)" /><path d="M1 9.5h16" stroke="var(--ix-proposal)" strokeWidth="2.5" strokeDasharray="1.5 2.5" /></svg>
        proposed groupings
      </span>
    </div>
  );
}
