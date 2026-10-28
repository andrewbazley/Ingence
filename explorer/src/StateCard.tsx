// The Dashboard's one drawn state (empty, census only, loading, problem), as the page's own script
// draws it (`states.say` in app.js), for the places this workspace says one. The stylesheet is the
// page's: the same card, the same dot, the same actions, so a person sees one design.

import type {ReactElement} from 'react';

export interface StateAction {
  label: string;
  onClick(): void;
}

export function StateCard({eyebrow, dot = 'empty', sentence, body = [], actions = []}: {
  eyebrow?: string;
  dot?: 'empty' | 'census' | 'loading' | 'problem';
  sentence: string;
  body?: string[];
  actions?: StateAction[];
}): ReactElement {
  return (
    <div className="state-card" role="status" data-state={dot}>
      {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
      <div className="state-head">
        <span className={`state-dot ${dot}`} aria-hidden="true" />
        <p className="state-sentence">{sentence}</p>
      </div>
      {body.map((line) => <p key={line} className="state-body">{line}</p>)}
      {actions.length ? (
        <div className="state-actions">
          {actions.map((action) => (
            <button key={action.label} type="button" className="answer quiet" onClick={action.onClick}><strong>{action.label}</strong></button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
