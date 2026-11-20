import type {
  DagEvalResponse,
  DeterministicLossRow,
  DeterministicRunResponse,
  PortfolioRollupResponse,
  ProgrammeDeal,
  RunManifest,
  UpliftRule,
  ViewOfRisk,
} from '../types/contracts';

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listProgrammes: () => fetchJson<ProgrammeDeal[]>('/api/programmes'),
  evaluateProgramme: (id: string) =>
    fetchJson<DagEvalResponse>(`/api/programmes/${id}/evaluate`, { method: 'POST' }),
  updateUplifts: (id: string, rules: UpliftRule[]) =>
    fetchJson<ProgrammeDeal>(`/api/programmes/${id}/uplifts`, {
      method: 'PUT',
      body: JSON.stringify(rules),
    }),
  getPortfolioRollup: (view: ViewOfRisk, totalCapital: number) =>
    fetchJson<PortfolioRollupResponse>(
      `/api/portfolio/rollup?view=${view}&totalCapital=${totalCapital}`,
    ),
  runDeterministic: (payload: {
    programmeId: string;
    view: ViewOfRisk;
    hoursClause: number;
    losses: DeterministicLossRow[];
  }) =>
    fetchJson<DeterministicRunResponse>('/api/deterministic/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listManifests: () => fetchJson<RunManifest[]>('/api/lineage/manifests'),
  getBenchmarks: () => fetchJson<Record<string, unknown>>('/api/benchmarks'),
};
