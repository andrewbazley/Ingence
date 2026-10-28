// Arranging the Wheel: which question each ring answers, chosen from what the run can say beneath
// the center. Every axis is offered with how many of the files it would place and how many values
// it has, so a person sees before choosing that "technique" is named in a third of the files and
// "reagent" in almost none. A change applies at once and is one step in the history.

import {useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactElement} from 'react';

import {api, type AxesResponse, type AxisSummary} from './api';
import {PRESETS, arrangementWords} from './arrangement';
import {count, percent} from './format';

const FAMILY_WORDS: Record<string, string> = {
  structure: 'Where files sit',
  units: 'Experiment units',
  names: 'What names say (the Dictionary’s senses)',
  spelling: 'How names spell it (as written)',
  readings: 'What the run read',
  time: 'When',
};
const FAMILY_ORDER = ['names', 'spelling', 'units', 'readings', 'time', 'structure'];

/** Theme kinds that say where or how a file is kept rather than what the work was. */
const BOOKKEEPING = new Set(['format', 'date', 'version', 'identifier', 'unit', 'count', 'read_structure', 'position', 'connective', 'file_role']);

export function useAxes(focus: string, run: string, open: boolean): AxesResponse | null {
  const [axes, setAxes] = useState<AxesResponse | null>(null);
  useEffect(() => {
    if (!open || !focus) return undefined;
    if (axes && axes.focus.id === focus) return undefined;
    const controller = new AbortController();
    api.axes(focus, run || undefined, controller.signal).then(setAxes).catch(() => {});
    return () => controller.abort();
  }, [focus, run, open, axes]);
  return axes;
}

export function axisLabels(axes: AxesResponse | null): Map<string, string> {
  const found = new Map<string, string>();
  for (const item of axes?.axes ?? []) found.set(item.axis.key, item.axis.label);
  return found;
}

function Coverage({item, files}: {item: AxisSummary; files: number}): ReactElement {
  const share = files ? item.files_with_value / files : 0;
  return (
    <span className="ix-coverage" title={`${count(item.files_with_value)} of ${count(files)} files have a value; ${count(item.values)} values`}>
      <span className="ix-coverage-bar" aria-hidden="true"><span style={{width: `${Math.max(2, share * 100)}%`}} /></span>
      <span className="ix-num">{percent(item.files_with_value, files)} · {count(item.values)}</span>
    </span>
  );
}

export function ArrangePanel({focus, run, arrange, onChange, onClose}: {
  focus: string; run: string; arrange: string[];
  onChange(axes: string[]): void; onClose(): void;
}): ReactElement {
  const axes = useAxes(focus, run, true);
  const panel = useRef<HTMLDivElement | null>(null);
  const [showAll, setShowAll] = useState(false);
  // Opened beside its button, and kept inside the window: aligned to the button's right edge
  // where it would otherwise run past the window's.
  const [side, setSide] = useState<'left' | 'right'>('left');
  useLayoutEffect(() => {
    const anchor = panel.current?.parentElement?.getBoundingClientRect();
    if (anchor && anchor.left + 436 > window.innerWidth) setSide('right');
  }, []);
  useEffect(() => {
    panel.current?.querySelector<HTMLElement>('button, select')?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Back to the button that opened it, so the keyboard stays in the workspace.
      panel.current?.parentElement?.querySelector<HTMLElement>(':scope > button')?.focus();
      onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const labels = axisLabels(axes);
  const byKey = useMemo(() => new Map((axes?.axes ?? []).map((item) => [item.axis.key, item])), [axes]);
  const files = axes?.files ?? 0;
  const families = useMemo(() => {
    const grouped = new Map<string, AxisSummary[]>();
    for (const item of axes?.axes ?? []) {
      const kind = item.axis.key.slice(2);
      const theme = item.axis.family === 'names' || item.axis.family === 'spelling';
      if (theme && item.files_with_value === 0) continue;
      if (theme && !showAll && BOOKKEEPING.has(kind)) continue;
      // A spelling axis says something only where some sense is written more than one way.
      if (item.axis.family === 'spelling' && !showAll && (item.spellings ?? 0) <= (item.senses ?? 0)) continue;
      const held = grouped.get(item.axis.family) ?? [];
      held.push(item);
      grouped.set(item.axis.family, held);
    }
    return FAMILY_ORDER.filter((family) => grouped.has(family)).map((family) => ({family, items: grouped.get(family) ?? []}));
  }, [axes, showAll]);
  const move = (place: number, by: number) => {
    const next = [...arrange];
    const [taken] = next.splice(place, 1);
    next.splice(Math.max(0, Math.min(next.length, place + by)), 0, taken);
    onChange(next);
  };
  const flip = (place: number) => {
    const key = arrange[place];
    const other = key.startsWith('t.') ? `s.${key.slice(2)}` : key.startsWith('s.') ? `t.${key.slice(2)}` : key;
    onChange(arrange.map((item, index) => (index === place ? other : item)));
  };
  const usable = (preset: string[]) => preset.every((key) => key === 'folder' || byKey.has(key) && (byKey.get(key)?.files_with_value ?? 0) > 0);
  return (
    <div className="ix-popover ix-arrange" ref={panel} role="dialog" aria-label="Arrange the Wheel"
      style={side === 'right' ? {left: 'auto', right: 0} : undefined}>
      <div className="ix-popover-head">
        <strong>Arrange the Wheel</strong>
        <button type="button" className="ix-btn ix-quiet" aria-label="Close" onClick={() => {
          panel.current?.parentElement?.querySelector<HTMLElement>(':scope > button')?.focus();
          onClose();
        }}>×</button>
      </div>
      <p className="ix-note">Each ring, center outward, sorts every file beneath <strong>{axes?.focus.name ?? 'the center'}</strong> by one question. Every file is counted once: one whose names say two techniques sits in the pair’s sector.</p>
      <div className="ix-arrange-now" aria-label="The rings, center outward">
        {arrange.length ? arrange.map((key, place) => (
          <span key={`${key}-${place}`} className="ix-chip">
            <span className="ix-chip-ring">{place + 1}</span>
            {labels.get(key) ?? (key === 'folder' ? 'Folders' : key)}
            {key.startsWith('t.') || key.startsWith('s.') ? (
              <button type="button" className="ix-chip-btn" onClick={() => flip(place)} title={key.startsWith('t.') ? 'Group by how it is spelled in names instead' : 'Group by the Dictionary’s sense instead'}>
                {key.startsWith('t.') ? 'sense' : 'spelled'}
              </button>
            ) : null}
            <button type="button" className="ix-chip-btn" disabled={place === 0} onClick={() => move(place, -1)} aria-label={`Move ${labels.get(key) ?? key} inward`}>‹</button>
            <button type="button" className="ix-chip-btn" disabled={place === arrange.length - 1} onClick={() => move(place, 1)} aria-label={`Move ${labels.get(key) ?? key} outward`}>›</button>
            <button type="button" className="ix-chip-btn" onClick={() => onChange(arrange.filter((_, index) => index !== place))} aria-label={`Remove ${labels.get(key) ?? key}`}>×</button>
          </span>
        )) : <span className="ix-note" style={{margin: 0}}>Folders, as stored (the plain Wheel).</span>}
      </div>
      <h3>Start from</h3>
      <div className="ix-presets">
        {PRESETS.map((preset) => {
          const ok = !axes || usable(preset.axes);
          return (
            <button key={preset.name} type="button" className="ix-btn ix-quiet" disabled={!ok}
              aria-pressed={preset.axes.join(',') === arrange.join(',')}
              onClick={() => onChange(preset.axes)} title={ok ? preset.why : `${preset.why}. No name beneath this folder says it.`}>
              {preset.name}
            </button>
          );
        })}
      </div>
      <h3>Add a ring {arrange.length >= 4 ? '(four at most)' : ''}</h3>
      {!axes ? <p className="ix-note">Reading what the names beneath say…</p> : null}
      {families.map(({family, items}) => (
        <div key={family} className="ix-arrange-family">
          <div className="ix-label">{FAMILY_WORDS[family] ?? family}</div>
          <ul className="ix-list">
            {items.map((item) => (
              <li key={item.axis.key}>
                <button type="button" className="ix-link" disabled={arrange.length >= 4 || (arrange.includes(item.axis.key) && item.axis.key !== 'folder')}
                  onClick={() => onChange([...arrange, item.axis.key])} title={item.axis.question}>
                  <span className="ix-grow">
                    {item.axis.label}
                    {item.examples.length ? <span className="ix-note" style={{margin: 0, display: 'block'}}>{item.examples.map((example) => example.label).join(', ')}</span> : null}
                  </span>
                  <Coverage item={item} files={files} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {axes?.unarranged?.length ? (
        <p className="ix-note">Not offered as a ring: {axes.unarranged.map((item) => item.words).join('; ')}.</p>
      ) : null}
      {axes ? (
        <button type="button" className="ix-btn ix-quiet" onClick={() => setShowAll(!showAll)}>
          {showAll ? 'Fewer axes' : 'Every axis, bookkeeping words and single spellings too'}
        </button>
      ) : null}
    </div>
  );
}

export function ArrangeButton({arrange, labels, open, onToggle}: {arrange: string[]; labels: Map<string, string>; open: boolean; onToggle(): void}): ReactElement {
  return (
    <button type="button" className="ix-btn" aria-expanded={open} aria-haspopup="dialog" onClick={onToggle}
      title="Choose what each ring of the Wheel answers: folders, techniques, conditions, spellings, stages, years, units (key A)">
      <span className="ix-label" style={{marginRight: 6}}>Arrange</span>{arrangementWords(labels, arrange)}
    </button>
  );
}
