// A time axis: a linear scale from instants to pixels, and calendar ticks that suit the span shown.
// Times are held as milliseconds since the epoch and shown in UTC, the zone the server bins in.
// A period is then the same period for every reader, and the axis says which zone it is in. Shown
// in the reader's own zone, every UTC month would read a day early west of Greenwich.
// Nothing here reads the reader's zone: only ``getUTC*`` and ``Date.UTC``.

/** The zone every time on the Timeline is drawn and named in. */
export const AXIS_ZONE = 'UTC';

export interface TimeScale {
  start: number;
  end: number;
  left: number;
  right: number;
  x(time: number): number;
  time(x: number): number;
}

export function timeScale(start: number, end: number, left: number, right: number): TimeScale {
  const span = Math.max(1, end - start);
  const width = Math.max(1, right - left);
  return {
    start,
    end,
    left,
    right,
    x: (time: number) => left + ((time - start) / span) * width,
    time: (x: number) => start + ((x - left) / width) * span,
  };
}

export function parseTime(text: string | null | undefined): number | null {
  if (!text) return null;
  const iso = text.includes('T') ? text : `${text}T00:00:00Z`;
  const value = Date.parse(iso);
  return Number.isNaN(value) ? null : value;
}

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

interface TickUnit {
  step: number;
  kind: 'hour' | 'day' | 'month' | 'year';
  count: number;
}

const UNITS: TickUnit[] = [
  {kind: 'hour', count: 1, step: HOUR},
  {kind: 'hour', count: 3, step: 3 * HOUR},
  {kind: 'hour', count: 6, step: 6 * HOUR},
  {kind: 'hour', count: 12, step: 12 * HOUR},
  {kind: 'day', count: 1, step: DAY},
  {kind: 'day', count: 2, step: 2 * DAY},
  {kind: 'day', count: 7, step: 7 * DAY},
  {kind: 'month', count: 1, step: 30.4 * DAY},
  {kind: 'month', count: 3, step: 91.3 * DAY},
  {kind: 'month', count: 6, step: 182.6 * DAY},
  {kind: 'year', count: 1, step: 365.25 * DAY},
  {kind: 'year', count: 2, step: 730.5 * DAY},
  {kind: 'year', count: 5, step: 1826 * DAY},
  {kind: 'year', count: 10, step: 3652 * DAY},
];

export interface Tick {
  time: number;
  label: string;
  major: boolean;
}

/** Calendar ticks in UTC, about one per ``spacing`` pixels. */
export function ticks(scale: TimeScale, spacing = 90): Tick[] {
  const wanted = Math.max(2, Math.floor((scale.right - scale.left) / spacing));
  const span = scale.end - scale.start;
  const unit = UNITS.find((item) => span / item.step <= wanted) ?? UNITS[UNITS.length - 1];
  const found: Tick[] = [];
  const first = new Date(scale.start);
  const year = first.getUTCFullYear();
  const month = first.getUTCMonth();
  const day = first.getUTCDate();
  let cursor: Date;
  if (unit.kind === 'hour') {
    cursor = new Date(Date.UTC(year, month, day, first.getUTCHours() - (first.getUTCHours() % unit.count)));
  } else if (unit.kind === 'day') {
    cursor = new Date(Date.UTC(year, month, day));
    if (unit.count === 7) cursor.setUTCDate(cursor.getUTCDate() - ((cursor.getUTCDay() + 6) % 7));
  } else if (unit.kind === 'month') {
    cursor = new Date(Date.UTC(year, month - (month % unit.count), 1));
  } else {
    cursor = new Date(Date.UTC(year - (year % unit.count), 0, 1));
  }
  let guard = 0;
  while (cursor.getTime() <= scale.end && guard < 400) {
    guard += 1;
    if (cursor.getTime() >= scale.start) {
      found.push({time: cursor.getTime(), label: tickLabel(cursor, unit), major: isMajor(cursor, unit)});
    }
    const next = new Date(cursor);
    if (unit.kind === 'hour') next.setUTCHours(next.getUTCHours() + unit.count);
    else if (unit.kind === 'day') next.setUTCDate(next.getUTCDate() + unit.count);
    else if (unit.kind === 'month') next.setUTCMonth(next.getUTCMonth() + unit.count);
    else next.setUTCFullYear(next.getUTCFullYear() + unit.count);
    cursor = next;
  }
  return found;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function tickLabel(moment: Date, unit: TickUnit): string {
  if (unit.kind === 'year') return String(moment.getUTCFullYear());
  if (unit.kind === 'month') return moment.getUTCMonth() === 0 ? String(moment.getUTCFullYear()) : MONTHS[moment.getUTCMonth()];
  if (unit.kind === 'day') return moment.getUTCDate() === 1 ? `${MONTHS[moment.getUTCMonth()]} ${moment.getUTCFullYear()}` : `${moment.getUTCDate()} ${MONTHS[moment.getUTCMonth()]}`;
  return `${String(moment.getUTCHours()).padStart(2, '0')}:00`;
}

function isMajor(moment: Date, unit: TickUnit): boolean {
  if (unit.kind === 'year') return true;
  if (unit.kind === 'month') return moment.getUTCMonth() === 0;
  if (unit.kind === 'day') return moment.getUTCDate() === 1;
  return moment.getUTCHours() === 0;
}

/** A day in UTC: ``5 Mar 2024``. */
export function shortDate(time: number): string {
  const moment = new Date(time);
  return `${moment.getUTCDate()} ${MONTHS[moment.getUTCMonth()]} ${moment.getUTCFullYear()}`;
}

/** An instant to the minute in UTC, saying so: ``5 Mar 2024 14:03 UTC``. */
export function longTime(time: number): string {
  const moment = new Date(time);
  const clock = `${String(moment.getUTCHours()).padStart(2, '0')}:${String(moment.getUTCMinutes()).padStart(2, '0')}`;
  return `${shortDate(time)} ${clock} ${AXIS_ZONE}`;
}

/**
 * The period a bin starting at ``time`` covers, named in UTC by its unit (the server's bins fall on
 * UTC calendar boundaries): ``5 Mar 2024 14:00``, ``5 Mar 2024``, ``week of 4 Mar 2024``,
 * ``Mar 2024``, ``Q1 2024``, ``2024``.
 */
export function periodLabel(time: number, unit: string): string {
  const moment = new Date(time);
  const year = moment.getUTCFullYear();
  if (unit === 'hour') return `${shortDate(time)} ${String(moment.getUTCHours()).padStart(2, '0')}:00`;
  if (unit === 'week') return `week of ${shortDate(time)}`;
  if (unit === 'month') return `${MONTHS[moment.getUTCMonth()]} ${year}`;
  if (unit === 'quarter') return `Q${Math.floor(moment.getUTCMonth() / 3) + 1} ${year}`;
  if (unit === 'year') return String(year);
  return shortDate(time);
}
