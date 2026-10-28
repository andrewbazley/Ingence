// Lineage's compact legend: the same grammar the Wheel's inspector and the Timeline use.

import type {ReactElement} from 'react';

export function LineageLegend(): ReactElement {
  return (
    <div className="ix-legend" aria-label="Legend">
      <span className="ix-key" title="The run published that the lower file was produced from the upper one.">
        <svg width="30" height="10" aria-hidden="true"><path d="M1 5h24" stroke="var(--ix-yes)" strokeWidth="2" /><path d="M23 1l6 4-6 4z" fill="var(--ix-yes)" /></svg>
        produced from (published)
      </span>
      <span className="ix-key" title="The run published that one file mentions or points at another. Never 'made from'.">
        <svg width="30" height="10" aria-hidden="true"><path d="M6 5h23" stroke="var(--ix-ink-2)" strokeWidth="1.4" strokeDasharray="7 3 1.5 3" /><circle cx="4.5" cy="5" r="3" fill="var(--ix-panel)" stroke="var(--ix-ink-2)" strokeWidth="1.4" /></svg>
        mentions
      </span>
      <span className="ix-key" title="A rule proposed it and nobody asserted it. Shown only with Proposals on.">
        <svg width="30" height="10" aria-hidden="true"><path d="M1 5h22" stroke="var(--ix-proposal)" strokeWidth="1.6" strokeDasharray="5 4" /><path d="M23 1.5l5.5 3.5-5.5 3.5z" fill="var(--ix-panel)" stroke="var(--ix-proposal)" strokeWidth="1.2" /></svg>
        proposed
      </span>
      <span className="ix-key" title="A folder: its files, as the census found them.">
        <svg width="20" height="12" aria-hidden="true"><rect x="1" y="1" width="18" height="10" rx="3" fill="var(--ix-track)" stroke="var(--ix-rule-strong)" /></svg>
        folder
      </span>
      <span className="ix-key" title="A group the files belong to: belonging, not provenance.">
        <svg width="30" height="10" aria-hidden="true"><path d="M1 5h14" stroke="var(--ix-ink-3)" strokeDasharray="1.5 3" /><rect x="15" y="0.5" width="14" height="9" rx="4.5" fill="var(--ix-raised)" stroke="var(--ix-rule-strong)" /></svg>
        group
      </span>
      <span className="ix-key" title="The line names something that is not an item of this run: nothing is drawn for it.">
        <svg width="24" height="12" aria-hidden="true"><path d="M1 6h12" stroke="var(--ix-ink-3)" strokeDasharray="3 3" /><circle cx="18" cy="6" r="5" fill="var(--ix-panel)" stroke="var(--ix-ink-3)" strokeDasharray="2 2" /></svg>
        open end
      </span>
      <span className="ix-key" title="A relation, group or exclusion a person stated: magenta, never the engine's colors, never provenance the run asserts.">
        <svg width="24" height="12" aria-hidden="true"><path d="M1 6h22" stroke="var(--ix-human)" strokeWidth="1.6" strokeDasharray="7 4" /></svg>
        said by you
      </span>
      <span className="ix-key" title="Lines one way not drawn: click to expand that branch.">
        <svg width="24" height="12" aria-hidden="true"><rect x="1" y="1" width="22" height="10" rx="5" fill="var(--ix-raised)" stroke="var(--ix-ink-3)" strokeDasharray="2 2" /></svg>
        more
      </span>
    </div>
  );
}

/** The legend of the name families' drawing: each mark said in words beside it. */
export function FamiliesLegend(): ReactElement {
  return (
    <div className="ix-legend" aria-label="Legend">
      <span className="ix-key" title="A family: files whose names agree on every word once the stage words are left out. Its label is those words; under it, its files and the lines that touch them.">
        <svg width="26" height="14" aria-hidden="true"><rect x="1" y="1" width="24" height="12" rx="3" fill="var(--ix-raised)" stroke="var(--ix-ink)" strokeWidth="2" /></svg>
        family (a heavier box: more of its files have a line)
      </span>
      <span className="ix-key" title="A file that has a line and belongs to no family stands as itself. A file a person answered Wrong about, in its family, appears this way.">
        <svg width="26" height="14" aria-hidden="true"><rect x="1" y="1" width="24" height="12" rx="3" fill="var(--ix-panel)" stroke="var(--ix-rule-strong)" strokeWidth="1.2" /></svg>
        file in no family
      </span>
      <span className="ix-key" title="Some of the family's files are in conflict with it: a rule defeated their place in it, or a person answered Wrong. They are counted beside it and never collapsed with it.">
        <svg width="26" height="14" aria-hidden="true"><rect x="1" y="1" width="24" height="12" rx="3" fill="var(--ix-raised)" stroke="var(--ix-problem)" strokeWidth="1.4" strokeDasharray="3 2" /></svg>
        dashed red: files in conflict
      </span>
      <span className="ix-key" title="One edge for each kind of line between two families; its width, from 1 to 6 px, is how many file-to-file lines it stands for.">
        <svg width="30" height="10" aria-hidden="true"><path d="M1 5h22" stroke="var(--ix-yes)" strokeWidth="4" /><path d="M21 1l7 4-7 4z" fill="var(--ix-yes)" /></svg>
        produced from (published); wider: more lines
      </span>
      <span className="ix-key" title="The run published that one file mentions or points at another. Never 'made from'.">
        <svg width="30" height="10" aria-hidden="true"><path d="M6 5h23" stroke="var(--ix-ink-2)" strokeWidth="1.4" strokeDasharray="7 3 1.5 3" /><circle cx="4.5" cy="5" r="3" fill="var(--ix-panel)" stroke="var(--ix-ink-2)" strokeWidth="1.4" /></svg>
        mentions
      </span>
      <span className="ix-key" title="A rule proposed it and nobody asserted it. Shown only with Proposals on.">
        <svg width="30" height="10" aria-hidden="true"><path d="M1 5h22" stroke="var(--later, var(--ix-proposal))" strokeWidth="1.6" strokeDasharray="6 4" /><path d="M23 1.5l5.5 3.5-5.5 3.5z" fill="var(--ix-panel)" stroke="var(--later, var(--ix-proposal))" strokeWidth="1.2" /></svg>
        proposed
      </span>
      <span className="ix-key" data-families="wrong">A family answered &quot;Wrong&quot; un-collapses into its files.</span>
    </div>
  );
}
