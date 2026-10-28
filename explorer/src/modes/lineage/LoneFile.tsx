// A file no line touches: the lineage is its node alone, and the page says so, and that a line not
// drawn is not evidence of absence. The one way to look further is the proposed pairs.

import {useEffect, useState, type ReactElement} from 'react';

import {runNumber} from '../../api';
import {StateCard} from '../../StateCard';

export function LoneFile({run, proposals, onProposals}: {
  run: string;
  proposals: boolean;
  onProposals(): void;
}): ReactElement {
  const [number, setNumber] = useState<string | null>(null);
  useEffect(() => {
    let stale = false;
    setNumber(null);
    runNumber(run).then((found) => {
      if (!stale) setNumber(found);
    });
    return () => {
      stale = true;
    };
  }, [run]);
  // Said once the run is known, so the sentence is never first drawn with another word in it.
  if (number === null) return <div className="ix-lone" data-lone="reading" />;
  return (
    <div className="ix-lone" data-lone="yes">
      <StateCard
        eyebrow="Lineage · no lines"
        sentence={`No line touches this file in ${number ? `Scan ${number}` : 'this scan'}. A line not drawn is not evidence of absence.`}
        actions={proposals ? [] : [{label: 'Show proposed pairs', onClick: onProposals}]}
      />
    </div>
  );
}
