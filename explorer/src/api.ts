// The workspace's one way to the engine: typed reads of /api/v1, whose shapes are the generated
// declarations. The page holds what it is sent and nothing more; the scientific store stays in
// the engine.

import type {
  AxesResponse,
  ChildrenResponse,
  ClaimCard,
  EdgeDetail,
  EntryCard,
  ErrorResponse,
  FilesResponse,
  LineageFamiliesResponse,
  LineageResponse,
  MeasuresResponse,
  TimelineResponse,
  ValueCard,
  VersionResponse,
  WheelResponse,
} from '../../src/ingence/web/types/api';

export type {
  AxesResponse,
  AxisInfo,
  HumanStatement,
  OverlayResponse,
  PlanCheck,
  PlanMoveCheck,
  PlansResponse,
  AxisSummary,
  ValueCard,
  WhereInfo,
  ChildrenResponse,
  ClaimCard,
  EdgeDetail,
  FamilyEdge,
  FamilyMember,
  FamilyNode,
  FamiliesTruncation,
  LineageEdge,
  LineageFamiliesResponse,
  LineageGroup,
  LineageNode,
  LineageResponse,
  OpenFamily,
  TimelineResponse,
  ThreadNode,
  ActivityTrack,
  Crumb,
  EntryCard,
  EntryTime,
  FileRow,
  FilesResponse,
  FolderRow,
  MeasureCategory,
  MeasureDefinition,
  PinnedRun,
  SectorExperiment,
  VersionResponse,
  WheelResponse,
  WheelSector,
} from '../../src/ingence/web/types/api';

export class ApiFailure extends Error {
  readonly code: string;
  readonly status: number;
  readonly parameter: string | null;

  constructor(status: number, code: string, message: string, parameter: string | null = null) {
    super(message);
    this.code = code;
    this.status = status;
    this.parameter = parameter;
  }
}

type Query = Record<string, string | number | boolean | string[] | null | undefined>;

async function read<T>(path: string, query: Query = {}, signal?: AbortSignal): Promise<T> {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) {
    if (Array.isArray(value)) for (const item of value) params.append(name, item);
    else if (value !== undefined && value !== null && value !== '') params.set(name, String(value));
  }
  const text = params.toString();
  let response: Response;
  try {
    response = await fetch(`/api/v1/${path}${text ? `?${text}` : ''}`, {signal});
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error;
    throw new ApiFailure(0, 'unreachable', 'The engine did not answer: is `ingence see` still running?');
  }
  const body = (await response.json().catch(() => null)) as T | ErrorResponse | null;
  if (!response.ok) {
    const failure = body as ErrorResponse | null;
    throw new ApiFailure(
      response.status,
      failure?.error?.code ?? 'server_error',
      failure?.error?.message ?? `The engine answered ${response.status}.`,
      failure?.error?.parameter ?? null,
    );
  }
  return body as T;
}

export interface WheelQuery {
  focus?: string;
  run?: string;
  rings?: number;
  size?: 'files' | 'bytes';
  max_bins?: number;
  max_sectors?: number;
  /** Comma-separated axis keys, center outward. */
  arrange?: string;
  where?: string[];
}

/** Only the files with these values (``where``), or all but those (``not``). */
export interface Filters {
  where?: string[];
  not?: string[];
}

export const api = {
  version: (signal?: AbortSignal) => read<VersionResponse>('version', {}, signal),
  wheel: (query: WheelQuery, signal?: AbortSignal) =>
    read<WheelResponse>('explorer/wheel', {...query}, signal),
  children: (folder: string, offset: number, run?: string, signal?: AbortSignal) =>
    read<ChildrenResponse>('explorer/children', {folder, offset, limit: 50, run}, signal),
  files: (folder: string, scope: 'direct' | 'beneath', offset: number, limit: number,
    run?: string, signal?: AbortSignal, window?: {source: string; from: string; to: string}, filters?: Filters) =>
    read<FilesResponse>('explorer/files', {folder, scope, offset, limit, run, ...(window ?? {}), where: filters?.where, not: filters?.not}, signal),
  axes: (focus: string, run?: string, signal?: AbortSignal) =>
    read<AxesResponse>('explorer/axes', {focus, run}, signal),
  value: (query: {focus: string; axis: string; value: string; where?: string[]; run?: string}, signal?: AbortSignal) =>
    read<ValueCard>('explorer/value', {...query}, signal),
  entry: (id: string, run?: string, signal?: AbortSignal) =>
    read<EntryCard>('explorer/entry', {id, run}, signal),
  lineage: (query: LineageQuery, signal?: AbortSignal) =>
    read<LineageResponse>('explorer/lineage', {...query}, signal),
  families: (query: FamiliesQuery, signal?: AbortSignal) =>
    read<LineageFamiliesResponse>('explorer/lineage/families', {...query}, signal),
  edge: (id: string, run?: string, signal?: AbortSignal) =>
    read<EdgeDetail>('graph/edge', {id, run}, signal),
  claim: (id: string, run?: string, signal?: AbortSignal) =>
    read<ClaimCard>('explorer/claim', {id, run}, signal),
  measures: (signal?: AbortSignal) => read<MeasuresResponse>('explorer/measures', {}, signal),
  timeline: (query: TimelineQuery, signal?: AbortSignal) =>
    read<TimelineResponse>('explorer/timeline', {...query}, signal),
};

export interface TimelineQuery {
  focus?: string;
  run?: string;
  reading: 'thread' | 'activity';
  source?: string;
  by?: string;
  where?: string[];
  from?: string;
  to?: string;
  depth?: number;
  proposals?: boolean;
}

export interface LineageQuery {
  focus: string;
  run?: string;
  direction?: 'up' | 'down' | 'both';
  depth?: number;
  proposals?: boolean;
  groups?: boolean;
  expand?: string;
  open?: string;
}

/** Lineage at scale: name families collapsed (``GET /api/v1/explorer/lineage/families``). */
export interface FamiliesQuery {
  /** The folder the families are limited to; none is the whole run. */
  folder?: string;
  run?: string;
  proposals?: boolean;
  /** How many nodes are drawn, the ones with most lines first. */
  limit?: number;
  /** One family to open: its files, `members_limit` at a time from `members_offset`. */
  family?: string;
  members_limit?: number;
  members_offset?: number;
}

const runNumbers = new Map<string, string>();

/**
 * "3" for the run History calls Run 3; empty where the run is not found. Read from the runs'
 * names as History lists them, once for each run.
 */
export async function runNumber(runId: string): Promise<string> {
  if (!runId) return '';
  if (!runNumbers.has(runId)) {
    try {
      const response = await fetch('/api/timeline');
      const body = (await response.json()) as {runs?: Array<{run_id: string; label?: string}>};
      for (const run of body.runs ?? []) {
        // A run's label reads "Scan 5", or "Run 5" in older labels.
        const said = /^(?:Scan|Run)\s+(\d+)/.exec(String(run.label ?? ''));
        if (said) runNumbers.set(run.run_id, said[1]);
      }
    } catch {
      // The sentence says "this scan" instead.
    }
  }
  return runNumbers.get(runId) ?? '';
}
