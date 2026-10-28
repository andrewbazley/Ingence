// The workspace's symbols, drawn here as SVG: nothing is fetched, and each reads at 14 px in
// either theme. A file is a sheet with its kind's color and its short extension; a folder is a
// tabbed folder; a group is a stack of sheets; an archive link is a file of its real kind with a
// link mark; an unknown kind stays visibly unknown (a dashed sheet and a question mark).

import type {ReactElement} from 'react';

export const KIND_VAR: Record<string, string> = {
  image: 'var(--ix-kind-image)',
  table: 'var(--ix-kind-table)',
  text: 'var(--ix-kind-text)',
  archive: 'var(--ix-kind-archive)',
  array: 'var(--ix-kind-array)',
  other: 'var(--ix-kind-other)',
  conflicting: 'var(--ix-problem)',
  unknown: 'var(--ix-track-strong)',
};

interface FileGlyphProps {
  kind: string;
  extension?: string;
  link?: boolean;
  size?: number;
  title?: string;
}

export function FileGlyph({kind, extension = '', link = false, size = 16, title}: FileGlyphProps): ReactElement {
  const unknown = kind === 'unknown';
  const label = extension ? extension.toUpperCase().slice(0, 4) : '?';
  return (
    <svg width={size * 1.6} height={size} viewBox="0 0 26 16" role={title ? 'img' : undefined}
      aria-label={title} aria-hidden={title ? undefined : true} style={{flex: 'none'}}>
      <path d="M1.5 1.5h7.5l3 3v10h-10.5z" fill="var(--ix-raised)" stroke="var(--ix-ink-3)"
        strokeWidth="1" strokeDasharray={unknown ? '2 1.5' : undefined} />
      <path d="M9 1.5v3h3" fill="none" stroke="var(--ix-ink-3)" strokeWidth="1" />
      <rect x="1.5" y="9.5" width="10.5" height="5" fill={KIND_VAR[kind] ?? KIND_VAR.other} />
      <text x="14" y="12" fontSize="7.5" fontWeight="600" fill="var(--ix-ink-2)"
        fontFamily="var(--ix-sans)">{label}</text>
      {link ? (
        <g stroke="var(--ix-ink)" strokeWidth="1.1" fill="none">
          <path d="M3.5 6.2a1.6 1.6 0 0 1 1.6-1.6h1.2" />
          <path d="M9.5 6.2a1.6 1.6 0 0 1-1.6 1.6H6.7" />
        </g>
      ) : null}
    </svg>
  );
}

export function FolderGlyph({size = 16, unit = false}: {size?: number; unit?: boolean}): ReactElement {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" style={{flex: 'none'}}>
      <path d="M1.5 4h4.5l1.5 1.5h7v8h-13z" fill="var(--ix-track)" stroke="var(--ix-ink-2)" strokeWidth="1" />
      {unit ? <path d="M8 7.5l2 2-2 2-2-2z" fill="var(--ix-ink)" /> : null}
    </svg>
  );
}

/** A value of an arrangement's axis: a tag, what a name says rather than where a file is. */
export function TagGlyph({size = 16}: {size?: number}): ReactElement {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" style={{flex: 'none'}}>
      <path d="M1.5 2.5v5l7 7 6-6-7-7h-5z" fill="var(--ix-track)" stroke="var(--ix-ink-2)" strokeWidth="1" strokeLinejoin="round" />
      <circle cx="4.6" cy="5.6" r="1.2" fill="var(--ix-ink-2)" />
    </svg>
  );
}

export function StackGlyph({size = 16}: {size?: number}): ReactElement {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" style={{flex: 'none'}}>
      <rect x="4.5" y="1.5" width="9" height="10" rx="1" fill="var(--ix-raised)" stroke="var(--ix-ink-3)" />
      <rect x="3" y="3" width="9" height="10" rx="1" fill="var(--ix-raised)" stroke="var(--ix-ink-3)" />
      <rect x="1.5" y="4.5" width="9" height="10" rx="1" fill="var(--ix-raised)" stroke="var(--ix-ink-2)" />
    </svg>
  );
}

export function ModeGlyph({mode}: {mode: 'wheel' | 'lineage' | 'timeline'}): ReactElement {
  if (mode === 'wheel') {
    return (
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <circle cx="8" cy="8" r="2.2" fill="currentColor" />
        <path d="M8 1.5v4M14.5 8h-4M3.4 12.6l2.9-2.9" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    );
  }
  if (mode === 'lineage') {
    return (
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="3" cy="3.5" r="1.8" fill="currentColor" />
        <circle cx="3" cy="12.5" r="1.8" fill="currentColor" />
        <circle cx="13" cy="8" r="1.8" fill="currentColor" />
        <path d="M4.6 4.3l6.8 3M4.6 11.7l6.8-3" stroke="currentColor" strokeWidth="1.3" fill="none" />
      </svg>
    );
  }
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M1 13.5h14" stroke="currentColor" strokeWidth="1.3" />
      <path d="M3 10.5h4M6 7.5h6M9 4.5h5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}
