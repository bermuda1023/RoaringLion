import type {
  DagEvalResponse,
  DeterministicLossRow,
  DeterministicRunResponse,
  GraphTemplate,
  PortfolioRollupResponse,
  ProgrammeDeal,
  RunManifest,
  UpliftRule,
  ViewOfRisk,
} from '../types/contracts';

export interface ProgrammeProfile {
  uniqueCode: string;
  programCode: string;
  legalEntity: string;
  tradingUnit: string;
  planningClass: string;
  newOrRenewal: string;
  isMultiYear: boolean;
  writtenDate?: string | null;
  externalFilePath?: string | null;
  cedentGroup: string;
  cedentCountry: string;
  cedentCreditRating: string;
  brokerGroup: string;
  closingBroker?: string | null;
  brokerContact?: string | null;
  mgaCode?: string | null;
  underwriterAssistant?: string | null;
  pricingAnalyst?: string | null;
  claimHandler?: string | null;
  offeredLine?: number | null;
  estimatedSignedPct: number;
  limitBasis: string;
  claimTrigger: string;
  lossEventType: string;
  reinstatementBasis: string;
  inuringNote?: string | null;
  economicInflationPct: number;
  socialInflationPct: number;
  gearingEffectPct: number;
  rarcExposurePct: number;
  rarcLayerPct: number;
  rarcPricePct: number;
  rarcTotalPct: number;
  priorYearLimitM?: number | null;
  priorYearPremiumM?: number | null;
  priorYearRolPct?: number | null;
  priorYearSignedLine?: number | null;
}

export interface ContractFull extends ProgrammeDeal {
  chainId: string;
  direction: string;
  structure: string;
  instrument: string;
  placement: string;
  inceptionDate?: string | null;
  expiryDate?: string | null;
  hoursClause: number;
  authorizedLine: number;
  attachment: number;
  technicalRol?: number | null;
  brokeragePct: number;
  frontingFeePct: number;
  customTags: string[];
  exposureEclipseProgrammeId?: string | null;
  profile: ProgrammeProfile;
  version: number;
  contentHash: string;
}

export interface SimSetDetail {
  simSetId: string;
  name: string;
  sourceFormat: string;
  eventCatalogId: string;
  numTrials: number;
  totalEvents: number;
  currency: string;
  regionPerils: string[];
  contentHash: string;
  parquetPath: string;
  isIndustryCatalog: boolean;
  expectedAnnualLoss?: number | null;
  manifest?: RunManifest | null;
}

export interface IlwPriceRequest {
  name: string;
  industrySimSetId: string;
  regionPerils: string[];
  triggerType: 'BINARY' | 'PRO_RATA' | 'AGGREGATE' | 'SECOND_EVENT';
  triggerBn: number;
  exhaustionBn?: number | null;
  limitM: number;
  rol: number;
  direction: 'ASSUMED' | 'EXTERNAL_RETRO' | 'INTERNAL_CEDED';
  currency: string;
  portfolioView: ViewOfRisk;
}

export interface IlwPriceResponse {
  name: string;
  industrySimSetId: string;
  eventCatalogId: string;
  regionPerils: string[];
  triggerType: string;
  triggerBn: number;
  exhaustionBn?: number | null;
  limitM: number;
  premiumM: number;
  rol: number;
  direction: string;
  currency: string;
  computeMs: number;
  attachProbPct: number;
  exhaustProbPct: number;
  expectedLossM: number;
  sdM: number;
  elrPct: number;
  indemnityIndexCorrelation: number;
  grossPortfolioTvar250M: number;
  netPortfolioTvar250M: number;
  tvar250ReductionM: number;
  hedgeEffectivenessPct: number;
  basisRiskResidualM: number;
  costPerDollarTvarSaved?: number | null;
  manifest: RunManifest;
}

export interface StaleManifestInfo {
  manifestId: string;
  runType: string;
  isStale: boolean;
  staleReasons: string[];
}

export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export function readableError(status: number, text: string): string {
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) {
      const msgs = body.detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : ''))
        .filter(Boolean);
      if (msgs.length) return msgs.join('; ');
    }
  } catch {
    // fall through to raw text
  }
  return `HTTP ${status}: ${text.slice(0, 300)}`;
}

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(url), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    throw new Error(readableError(res.status, await res.text()));
  }
  return res.json() as Promise<T>;
}

export function postJson<T>(url: string, body?: unknown): Promise<T> {
  return fetchJson<T>(url, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

export const api = {
  listProgrammes: () => fetchJson<ProgrammeDeal[]>('/api/programmes'),
  listContracts: (params?: { status?: string; search?: string }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    if (params?.search) qs.set('search', params.search);
    const q = qs.toString();
    return fetchJson<ContractFull[]>(`/api/contracts${q ? `?${q}` : ''}`);
  },
  createContract: (payload: Record<string, unknown>) =>
    fetchJson<ContractFull>('/api/contracts', { method: 'POST', body: JSON.stringify(payload) }),
  updateContract: (id: string, patch: Record<string, unknown>) =>
    fetchJson<ContractFull>(`/api/contracts/${id}`, { method: 'PUT', body: JSON.stringify(patch) }),
  listSimSets: () => fetchJson<SimSetDetail[]>('/api/sim-sets'),
  ingestYeltFile: async (formData: FormData): Promise<SimSetDetail> => {
    const res = await fetch(apiUrl('/api/sim-sets/ingest/yelt'), { method: 'POST', body: formData });
    if (!res.ok) throw new Error(readableError(res.status, await res.text()));
    return res.json() as Promise<SimSetDetail>;
  },
  ingestElt: (payload: Record<string, unknown>) =>
    fetchJson<SimSetDetail>('/api/sim-sets/ingest/elt', { method: 'POST', body: JSON.stringify(payload) }),
  priceIlw: (payload: IlwPriceRequest) =>
    fetchJson<IlwPriceResponse>('/api/ilw/price', { method: 'POST', body: JSON.stringify(payload) }),
  promoteIlw: (payload: IlwPriceRequest) =>
    fetchJson<ContractFull>('/api/ilw/promote', { method: 'POST', body: JSON.stringify(payload) }),
  evaluateProgramme: (id: string) =>
    fetchJson<DagEvalResponse>(`/api/programmes/${id}/evaluate`, { method: 'POST' }),
  updateGraph: (id: string, graph: unknown) =>
    fetchJson<ProgrammeDeal>(`/api/programmes/${id}/graph`, {
      method: 'PUT',
      body: JSON.stringify(graph),
    }),
  bindProgramme: (id: string) =>
    fetchJson<{ contract: ContractFull; bindManifestId: string }>(`/api/programmes/${id}/bind`, {
      method: 'POST',
    }),
  listGraphTemplates: (programmeId?: string) =>
    fetchJson<GraphTemplate[]>(`/api/graph-templates${programmeId ? `?programmeId=${encodeURIComponent(programmeId)}` : ''}`),
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
  checkStaleness: () => fetchJson<StaleManifestInfo[]>('/api/lineage/staleness'),
  getBenchmarks: () => fetchJson<Record<string, unknown>>('/api/benchmarks'),
};
