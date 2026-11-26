// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchJson, postJson } from '../api/client';
import { PROGRAMME_STATUSES, type OptimizerCandidatesPage, type ProgrammeStatus } from '../types/contracts';
import { defaultStatusesForMode } from '../utils/optimizerStatuses';
import { PortfolioOptimizer } from './PortfolioOptimizer';

vi.mock('../api/client', () => ({ fetchJson: vi.fn(), postJson: vi.fn() }));
vi.mock('../components/OptimizerCharts', () => ({
  LayerContributionBarChart: () => null,
  ParetoFrontierChart: () => null,
  PortfolioRadarChart: () => null,
  ReturnPeriodStackChart: () => null,
}));

const DEALS = [
  { programmeId: 'prog-farmers', cedentName: 'Farmers Group', status: 'BOUND' },
  { programmeId: 'prog-pacific', cedentName: 'Pacific Mutual', status: 'QUOTED' },
  { programmeId: 'prog-zed', cedentName: 'Zed Declined Re', status: 'DECLINED' },
] as const;

const METRICS = {
  expectedProfitM: 10,
  meanRocPct: 9,
  deployedCollateralM: 100,
  grossPremiumM: 20,
  expectedLossM: 8,
  elCapitalRatio: 0.05,
  riskRewardRatio: 0.1,
  tvar50M: 40,
  tvar100M: 60,
  tvar250M: 90,
  tvar500M: 110,
  tvar1000M: 120,
  var100M: 50,
  var250M: 80,
};

function dealsIn(statuses: readonly string[]) {
  return DEALS.filter((d) => statuses.includes(d.status));
}

function candidatesPage(url: string): OptimizerCandidatesPage {
  const qs = new URL(url, 'http://x').searchParams;
  const picked = qs.getAll('includedStatuses') as ProgrammeStatus[];
  const defaults = defaultStatusesForMode('FORWARD_PLANNING');
  const statuses = picked.length ? picked : defaults;
  const deals = dealsIn(statuses);
  return {
    mode: 'FORWARD_PLANNING',
    view: 'ADJUSTED',
    includedStatuses: statuses,
    defaultStatuses: defaults,
    usesDefaultStatuses: !picked.length,
    statusCounts: PROGRAMME_STATUSES.map((status) => ({
      status,
      programmes: DEALS.filter((d) => d.status === status).length,
      included: statuses.includes(status),
      defaultIncluded: defaults.includes(status),
    })),
    universeProgrammes: deals.length,
    matchedProgrammes: 60,
    search: qs.get('search') ?? '',
    limit: Number(qs.get('limit')),
    offset: Number(qs.get('offset')),
    programmeIds: deals.map((d) => d.programmeId),
    layers: deals.map((d) => ({
      layerKey: `${d.programmeId}::lyr`,
      programmeId: d.programmeId,
      layerId: 'lyr',
      cedentName: d.cedentName,
      programmeName: 'Cat XL',
      layerName: '1st Cat XL',
      direction: 'ASSUMED',
      status: d.status,
      regionPerils: ['US-FL-WS'],
      currentShare: 0.5,
      limit1xM: 20,
      premium1xM: 3,
      el1xM: 1,
      expectedProfit1xM: 2,
      rolPct: 15,
      defaultMinShare: 0,
      defaultMaxShare: 1.5,
    })),
    retroCandidate: null,
  };
}

function exploreResponse(payload: { includedStatuses: ProgrammeStatus[] | null }) {
  const statuses = payload.includedStatuses ?? defaultStatusesForMode('FORWARD_PLANNING');
  const layers = dealsIn(statuses).map((d) => ({
    layerKey: `${d.programmeId}::lyr`,
    programmeId: d.programmeId,
    layerId: 'lyr',
    cedentName: d.cedentName,
    programmeName: 'Cat XL',
    layerName: '1st Cat XL',
    direction: 'ASSUMED',
    status: d.status,
    regionPerils: ['US-FL-WS'],
    currentShare: 0.5,
    optimalShare: 0.75,
    deltaShare: 0.25,
    optimalLimitM: 15,
    optimalPremiumM: 2.25,
    optimalElM: 0.75,
    optimalExpectedProfitM: 1.5,
    coTvar100M: 5,
    coTvar250M: 8,
    coTvar250Pct: 30,
    coVar250M: 7,
    coVar250Pct: 30,
  }));
  return {
    mode: 'FORWARD_PLANNING',
    granularity: 'LAYER',
    view: 'ADJUSTED',
    includedStatuses: statuses,
    computeMs: 12,
    initialPortfolio: METRICS,
    initialLayers: layers,
    recommendedIndex: 0,
    frontier: [
      {
        pointIndex: 0,
        targetTvar250M: 115,
        status: 'Optimal',
        solver: 'HiGHS LP',
        metrics: METRICS,
        layers,
        constraints: [],
        returnPeriodContributions: [],
      },
    ],
  };
}

const fetchMock = vi.mocked(fetchJson);
const postMock = vi.mocked(postJson);

function candidateUrls(): URLSearchParams[] {
  return fetchMock.mock.calls.map(([url]) => new URL(url, 'http://x').searchParams);
}

function lastExplorePayload() {
  const calls = postMock.mock.calls.filter(([url]) => url.startsWith('/api/optimizer/explore'));
  return calls[calls.length - 1][1] as { includedStatuses: ProgrammeStatus[] | null; mode: string };
}

function rowFor(cedent: string): HTMLElement {
  const row = screen.getAllByText(cedent)[0].closest('tr');
  if (!row) throw new Error(`no table row for ${cedent}`);
  return row;
}

async function openConfig() {
  fireEvent.click(screen.getByRole('button', { name: '1. Optimization Config' }));
  return (await screen.findByRole('checkbox', { name: /^DECLINED\b/ })) as HTMLInputElement;
}

describe('PortfolioOptimizer deal status selection', () => {
  beforeEach(() => {
    fetchMock.mockImplementation(async (url: string) => candidatesPage(url) as never);
    postMock.mockImplementation(async (_url: string, body?: unknown) => exploreResponse(body as never) as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PortfolioOptimizer />
      </QueryClientProvider>,
    );
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('builds the portfolio from the default statuses until the user changes them', async () => {
    await screen.findAllByText('Farmers Group');
    expect(lastExplorePayload().includedStatuses).toBeNull();
    expect(candidateUrls().every((qs) => qs.getAll('includedStatuses').length === 0)).toBe(true);
    expect(within(rowFor('Farmers Group')).getByText('BOUND')).toBeTruthy();
    expect(within(rowFor('Pacific Mutual')).getByText('QUOTED')).toBeTruthy();
    expect(screen.queryByText('Zed Declined Re')).toBeNull();

    const declined = await openConfig();
    expect(declined.checked).toBe(false);
    expect((screen.getByRole('checkbox', { name: /^BOUND\b/ }) as HTMLInputElement).checked).toBe(true);
  });

  it('includes declined deals once Declined is ticked and marks them in every table', async () => {
    fireEvent.click(await openConfig());

    // The deal list is re-queried with the wider status filter and shows the declined deal's status.
    await waitFor(() => expect(candidateUrls().at(-1)?.getAll('includedStatuses')).toContain('DECLINED'));
    const defaults = defaultStatusesForMode('FORWARD_PLANNING');
    expect(candidateUrls().at(-1)?.getAll('includedStatuses')).toEqual(
      PROGRAMME_STATUSES.filter((s) => s === 'DECLINED' || defaults.includes(s)),
    );
    const listed = await waitFor(() => rowFor('Zed Declined Re'));
    expect(within(listed).getByText('DECLINED')).toBeTruthy();
    expect(listed.className).toContain('row-off-risk');
    expect(screen.getByText(/Custom selection/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Run Frontier Explorer' }));
    await waitFor(() => expect(lastExplorePayload().includedStatuses).toContain('DECLINED'));
    expect(lastExplorePayload().includedStatuses).toContain('BOUND');

    // Results dashboard: the declined deal is in the share summary with its status.
    const shareRow = await waitFor(() => rowFor('Zed Declined Re'));
    expect(within(shareRow).getByText('DECLINED')).toBeTruthy();
    expect(shareRow.className).toContain('row-off-risk');
    expect(screen.getByText(/includes deals that are not on risk \(1 DECLINED\)/)).toBeTruthy();

    // Portfolio dashboard: a Status column shows every deal's status.
    fireEvent.click(screen.getByRole('button', { name: '3. Portfolio Dashboard' }));
    expect(screen.getByRole('columnheader', { name: 'Status' })).toBeTruthy();
    expect(within(rowFor('Zed Declined Re')).getByText('DECLINED')).toBeTruthy();
    expect(within(rowFor('Farmers Group')).getByText('BOUND')).toBeTruthy();
  });

  it('goes back to the defaults on reset', async () => {
    fireEvent.click(await openConfig());
    await waitFor(() => expect(candidateUrls().at(-1)?.getAll('includedStatuses')).toContain('DECLINED'));
    fireEvent.click(screen.getByRole('button', { name: 'Reset to default' }));
    await waitFor(() => expect(candidateUrls().at(-1)?.getAll('includedStatuses')).toEqual([]));
    fireEvent.click(screen.getByRole('button', { name: 'Run Frontier Explorer' }));
    await waitFor(() => expect(lastExplorePayload().includedStatuses).toBeNull());
  });

  it('searches and pages the deal list on the server instead of loading every deal', async () => {
    await openConfig();
    const first = candidateUrls().at(-1)!;
    expect(first.get('limit')).toBe('25');
    expect(first.get('offset')).toBe('0');
    expect(await screen.findByText(/Programmes 1–2 of 60 · 2 layers/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(candidateUrls().at(-1)?.get('offset')).toBe('25'));

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search deals' }), { target: { value: 'zed' } });
    await waitFor(() => expect(candidateUrls().at(-1)?.get('search')).toBe('zed'));
    expect(candidateUrls().at(-1)?.get('offset')).toBe('0');
  });

  it('warns that results are out of date after the status selection changes', async () => {
    await screen.findAllByText('Farmers Group');
    expect(screen.queryByText(/status selection changed after this run/)).toBeNull();
    fireEvent.click(await openConfig());
    fireEvent.click(screen.getByRole('button', { name: '2. Results Dashboard' }));
    expect(await screen.findByText(/status selection changed after this run/)).toBeTruthy();
  });
});
