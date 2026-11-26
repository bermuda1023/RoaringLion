import { PROGRAMME_STATUSES, type OptimizerMode, type ProgrammeStatus, type ViewOfRisk } from '../types/contracts';

const CURRENT_MODE_STATUSES: readonly ProgrammeStatus[] = ['QUOTED', 'FIRM_ORDER', 'BOUND'];
const OFF_RISK_STATUSES: readonly ProgrammeStatus[] = ['DECLINED', 'NTU', 'EXPIRED'];

/** Mirrors the server's per-mode defaults (docs/CONTRACTS.md §5); the server's answer wins once loaded. */
export function defaultStatusesForMode(mode: OptimizerMode): ProgrammeStatus[] {
  return PROGRAMME_STATUSES.filter((s) =>
    mode === 'CURRENT' ? CURRENT_MODE_STATUSES.includes(s) : !OFF_RISK_STATUSES.includes(s),
  );
}

/** Declined, NTU and expired deals are not on risk, so the UI calls them out wherever they appear. */
export function isOffRiskStatus(status: string): boolean {
  return (OFF_RISK_STATUSES as readonly string[]).includes(status);
}

export function sameStatuses(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((s) => b.includes(s));
}

/**
 * Toggle one status. `selected` of null means "the mode's defaults". The result is null again whenever
 * it equals the defaults, and the last remaining status cannot be removed.
 */
export function toggleStatus(
  selected: readonly ProgrammeStatus[] | null,
  defaults: readonly ProgrammeStatus[],
  status: ProgrammeStatus,
): ProgrammeStatus[] | null {
  const current = selected ?? defaults;
  const next = PROGRAMME_STATUSES.filter((s) => (s === status ? !current.includes(s) : current.includes(s)));
  if (next.length === 0) return selected === null ? null : [...selected];
  return sameStatuses(next, defaults) ? null : next;
}

export interface CandidatesQuery {
  view: ViewOfRisk;
  mode: OptimizerMode;
  includedStatuses: readonly ProgrammeStatus[] | null;
  search: string;
  limit: number;
  offset: number;
}

export function candidatesUrl(q: CandidatesQuery): string {
  const qs = new URLSearchParams({ view: q.view, mode: q.mode });
  for (const s of q.includedStatuses ?? []) qs.append('includedStatuses', s);
  const term = q.search.trim();
  if (term) qs.set('search', term);
  qs.set('limit', String(q.limit));
  qs.set('offset', String(q.offset));
  return `/api/optimizer/candidates?${qs.toString()}`;
}

/** Programmes per status among assumed deals, in status order. The retro hedge candidate is not a deal. */
export function countProgrammesByStatus(
  rows: readonly { programmeId: string; status: string; direction: string }[],
): { status: string; programmes: number }[] {
  const byStatus = new Map<string, Set<string>>();
  for (const r of rows) {
    if (r.direction !== 'ASSUMED') continue;
    if (!byStatus.has(r.status)) byStatus.set(r.status, new Set());
    byStatus.get(r.status)!.add(r.programmeId);
  }
  const order = (s: string) => {
    const i = (PROGRAMME_STATUSES as readonly string[]).indexOf(s);
    return i === -1 ? PROGRAMME_STATUSES.length : i;
  };
  return [...byStatus.entries()]
    .sort((a, b) => order(a[0]) - order(b[0]))
    .map(([status, ids]) => ({ status, programmes: ids.size }));
}
