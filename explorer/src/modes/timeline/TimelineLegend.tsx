// Timeline's compact legend: how a mark says its source and how precisely it is known.

import type {ReactElement} from 'react';

export function TimelineLegend({reading}: {reading: string}): ReactElement {
  return (
    <div className="ix-legend" aria-label="Legend">
      {reading === 'thread' ? (
        <>
          <span className="ix-key" title="An instant from a rule's earliest credible time or a clock read from the content"><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="var(--ix-kind-image)" stroke="var(--ix-ink-2)" /></svg>earliest or content clock</span>
          <span className="ix-key" title="An instant from the filesystem: modification or birth"><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="var(--ix-panel)" stroke="var(--ix-kind-image)" strokeWidth="2.2" /></svg>filesystem time</span>
          <span className="ix-key" title="A day or a span read from a name: context, never a creation time"><svg width="24" height="12" aria-hidden="true"><rect x="1" y="1" width="22" height="10" rx="5" fill="var(--ix-panel)" stroke="var(--ix-ink-2)" strokeDasharray="3 2" /></svg>named date</span>
          <span className="ix-key" title="A time whose conclusion was published as conflicting (a rule that withdraws it fired): placed, and never passed as settled"><svg width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="5" fill="var(--ix-kind-image)" stroke="var(--ix-ink-2)" /><circle cx="9" cy="9" r="7.5" fill="none" stroke="var(--ix-problem)" strokeWidth="1.6" strokeDasharray="2 1.5" /></svg>time in conflict</span>
          <span className="ix-key" title="A derivation drawn backwards in time under this source: one of the two times is not the file's making"><svg width="28" height="10" aria-hidden="true"><path d="M1 5h20" stroke="var(--ix-problem)" strokeWidth="1.8" /><path d="M20 1l6 4-6 4z" fill="var(--ix-problem)" /></svg>runs backwards</span>
        </>
      ) : (
        <>
          <span className="ix-key" title="Files whose time falls in the period, one shared scale across rows"><svg width="14" height="12" aria-hidden="true"><rect x="2" y="3" width="10" height="9" fill="var(--ix-yes)" /></svg>files in a period</span>
          <span className="ix-key" title="Counted by a date read from names: context, never creation"><svg width="14" height="12" aria-hidden="true"><rect x="2" y="3" width="10" height="9" fill="var(--ix-track)" stroke="var(--ix-ink-3)" strokeDasharray="2 1.5" /></svg>named date</span>
          <span className="ix-key" title="The span a rule read from the folder's own name"><svg width="24" height="8" aria-hidden="true"><rect x="1" y="2" width="22" height="4" fill="none" stroke="var(--ix-ink-3)" strokeDasharray="2 2" /></svg>folder's named date</span>
          <span className="ix-key" title="A day a rule read the folder as copied in bulk: its files' times there are the copy's"><svg width="12" height="10" aria-hidden="true"><path d="M2 2 L10 2 L6 8 z" fill="var(--ix-proposal)" /></svg>copied in bulk</span>
          <span className="ix-key" title="A rule reads the row's folder as an experiment's"><svg width="6" height="12" aria-hidden="true"><rect x="1" y="1" width="3" height="10" fill="var(--ix-ink-2)" /></svg>experiment unit</span>
        </>
      )}
      <span className="ix-key" title="No time from the chosen source: counted, never given one"><svg width="12" height="12" aria-hidden="true"><rect x="1" y="1" width="10" height="10" fill="var(--ix-track)" stroke="var(--ix-ink-3)" /><path d="M1 11L11 1" stroke="var(--ix-ink-3)" /></svg>undated</span>
      <span className="ix-key" title="The project's runs, by when each began; the one shown is filled"><svg width="12" height="12" aria-hidden="true"><path d="M6 1 L11 6 L6 11 L1 6 z" fill="var(--ix-ink)" /></svg>run shown</span>
    </div>
  );
}
