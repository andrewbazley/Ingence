// Numbers, sizes and names as the workspace shows them. A name is only ever shortened on screen:
// the stored name is never changed, and the full name is always one focus or selection away.

const numberFormat = new Intl.NumberFormat();

export function count(value: number): string {
  return numberFormat.format(value);
}

/**
 * A claim's standing in plain words, the same words the Dashboard's other pages use
 * (app.js's PLAIN_VALUES). Not examined, no evidence found and conflicting evidence stay three
 * different states. A value the table lacks shows with its underscores as spaces.
 */
const STANDING: Record<string, string> = {
  unexamined: 'not examined',
  examined_no_evidence: 'no evidence found',
  examined_conflicting: 'conflicting evidence',
  resolved: 'settled',
};

export function standing(value: string): string {
  return STANDING[value] ?? value.replace(/_/g, ' ');
}

export function plural(value: number, one: string, many?: string): string {
  return `${count(value)} ${value === 1 ? one : (many ?? `${one}s`)}`;
}

export function percent(part: number, whole: number): string {
  if (!whole) return '0%';
  const share = (100 * part) / whole;
  if (share > 0 && share < 1) return '<1%';
  if (share < 100 && share > 99) return '>99%';
  return `${Math.round(share)}%`;
}

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];

export function bytes(value: number): string {
  let size = value;
  let unit = 0;
  while (size >= 1000 && unit < UNITS.length - 1) {
    size /= 1000;
    unit += 1;
  }
  return `${size >= 100 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${UNITS[unit]}`;
}

/**
 * Siblings' names with the words they all begin with shown once, as an ellipsis.
 *
 * Folders of one experiment often share a long first part ("2021 confocal run ..."); the Wheel
 * shows the part that tells them apart. Only whole words are hidden, only where at least two
 * siblings share them and at least six characters would be saved; the full name is never lost.
 */
export function compressSiblings(names: string[]): string[] {
  if (names.length < 2) return names;
  const split = names.map((name) => name.split(/(?<=[\s_\-.])/));
  let shared = 0;
  const shortest = Math.min(...split.map((parts) => parts.length));
  while (shared < shortest - 1 && split.every((parts) => parts[shared] === split[0][shared])) {
    shared += 1;
  }
  const saved = split[0].slice(0, shared).join('').length;
  if (shared === 0 || saved < 6) return names;
  return split.map((parts) => `…${parts.slice(shared).join('')}`);
}

/** Text cut to a width in characters, with an ellipsis, never mid-surrogate. */
export function clip(text: string, characters: number): string {
  const chars = Array.from(text);
  if (chars.length <= characters) return text;
  return `${chars.slice(0, Math.max(1, characters - 1)).join('')}…`;
}

/** A short extension label for a file-kind symbol: ND2, TXT; empty stays explicitly unknown. */
export function extensionLabel(extension: string): string {
  return extension ? clip(extension.toUpperCase(), 5) : '?';
}

/** When a person did something (a Record event), in the reader's own zone, named: a file's
 * times are shown in UTC instead (``longTime``), as the Timeline draws them. */
export function when(iso: string | null | undefined): string {
  if (!iso) return 'unknown';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  });
}

/**
 * A name to fit ``room`` characters among its siblings: the whole name where it fits, else the
 * first word no sibling shares ("kelt" among dated experiment folders), else the name clipped.
 * Only what is drawn is shortened; the full name is one hover, focus or selection away.
 */
export function fitAmong(name: string, siblings: string[], room: number): string {
  if (Array.from(name).length <= room) return name;
  const words = (text: string) => text.split(/[\s_\-.]+/).filter(Boolean);
  const others = siblings.filter((sibling) => sibling !== name).map((sibling) => new Set(words(sibling)));
  const telling = words(name).find((word) => /[A-Za-z]/.test(word) && word.length >= 2 && others.every((set) => !set.has(word)));
  if (telling && Array.from(telling).length <= room) return telling;
  return clip(name, room);
}
