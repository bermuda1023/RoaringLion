// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchJson, postJson } from '../api/client';
import {
  PROGRAMME_STATUSES,
  type OptimizerCandidatesPage,
  type OptimizerLayerCatalogItem,
  type OptimizerMode,
  type ProgrammeStatus,
} from '../types/contracts';
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

const HEDGE: OptimizerLayerCatalogItem = {
  layerKey: 'cand-retro-ilw-fl',
  programmeId: 'cand-retro-ilw-fl',
  layerId: 'cand-retro-ilw-fl',
  cedentName: 'ILW Market Hedge',
  programmeName: 'Retro FL ILW',
  layerName: 'Retro FL ILW',
  direction: 'EXTERNAL_RETRO',
  status: 'PLANNED',
  regionPerils: ['US-FL-WS'],
  currentShare: 0,
  limit1xM: 0,
  premium1xM: 5,
  el1xM: 0.6,
  expectedProfit1xM: -4.4,
  rolPct: 20,
  defaultMinShare: 0,
  defaultMaxShare: 2,
};

function candidatesPage(url: string): OptimizerCandidatesPage {
  const qs = new URL(url, 'http://x').searchParams;
  const picked = qs.getAll('includedStatuses') as ProgrammeStatus[];
  const mode = qs.get('mode') as OptimizerMode;
  const defaults = defaultStatusesForMode(mode);
  const statuses = picked.length ? picked : defaults;
  const deals = dealsIn(statuses);
  return {
    mode,
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
    retroCandidate: HEDGE,
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

interface ExplorePayload {
  includedStatuses: ProgrammeStatus[] | null;
  mode: string;
  objective?: string;
  layerBounds: { layerKey: string; minShare: number; maxShare: number; locked: boolean }[];
}

function lastExplorePayload() {
  const calls = postMock.mock.calls.filter(([url]) => url.startsWith('/api/optimizer/explore'));
  return calls[calls.length - 1][1] as ExplorePayload;
}

/** Run the explorer from the config tab and return the request it sent. */
async function runAndGetPayload(): Promise<ExplorePayload> {
  const before = postMock.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: 'Run Frontier Explorer' }));
  await waitFor(() => expect(postMock.mock.calls.length).toBe(before + 1));
  const payload = lastExplorePayload();
  fireEvent.click(screen.getByRole('button', { name: '1. Optimization Config' }));
  return payload;
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

  it('selects every status with one click', async () => {
    await openConfig();
    fireEvent.click(screen.getByRole('button', { name: 'All statuses' }));
    await waitFor(() => expect(candidateUrls().at(-1)?.getAll('includedStatuses')).toEqual([...PROGRAMME_STATUSES]));
    expect((await runAndGetPayload()).includedStatuses).toEqual([...PROGRAMME_STATUSES]);
  });

  it('treats a selection equal to the new mode\'s defaults as the defaults after a mode switch', async () => {
    await openConfig();
    for (const status of ['SUBMISSION', 'INDICATIVE', 'AUTHORIZED', 'BOUND_FOR_OPS', 'PLANNED']) {
      fireEvent.click(screen.getByRole('checkbox', { name: new RegExp(`^${status}\\b`) }));
    }
    await waitFor(() =>
      expect(candidateUrls().at(-1)?.getAll('includedStatuses')).toEqual(['QUOTED', 'FIRM_ORDER', 'BOUND']),
    );
    expect(screen.getByText(/Custom selection/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/Horizon Mode/), { target: { value: 'CURRENT' } });
    await waitFor(() => expect(candidateUrls().at(-1)?.get('mode')).toBe('CURRENT'));
    expect(candidateUrls().at(-1)?.getAll('includedStatuses')).toEqual([]);
    expect(await screen.findByText(/Default for Current Portfolio Rebalance/)).toBeTruthy();
    const payload = await runAndGetPayload();
    expect(payload.mode).toBe('CURRENT');
    expect(payload.includedStatuses).toBeNull();
  });

  it('keeps a custom selection that is not the new mode\'s defaults when the mode changes', async () => {
    fireEvent.click(await openConfig());
    fireEvent.change(screen.getByLabelText(/Horizon Mode/), { target: { value: 'CURRENT' } });
    await waitFor(() => expect(candidateUrls().at(-1)?.get('mode')).toBe('CURRENT'));
    expect(candidateUrls().at(-1)?.getAll('includedStatuses')).toContain('DECLINED');
    expect((await runAndGetPayload()).includedStatuses).toContain('DECLINED');
  });

  it('sends only the bounds the user set and keeps them across pages', async () => {
    await openConfig();
    expect((await runAndGetPayload()).layerBounds).toEqual([]);

    fireEvent.change(await screen.findByRole('spinbutton', { name: /Minimum share % for Farmers Group/ }), {
      target: { value: '10' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(candidateUrls().at(-1)?.get('offset')).toBe('25'));
    expect((await runAndGetPayload()).layerBounds).toEqual([
      { layerKey: 'prog-farmers::lyr', minShare: 0.1, maxShare: 1.5, locked: false },
    ]);
  });

  it('applies Lock All to the listed layers only, leaving a hedge hidden by a search alone', async () => {
    await openConfig();
    await screen.findByText('ILW Market Hedge');
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search deals' }), { target: { value: 'farm' } });
    await waitFor(() => expect(screen.queryByText('ILW Market Hedge')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Lock All' }));
    const whileSearching = await runAndGetPayload();
    expect(whileSearching.layerBounds.map((b) => b.layerKey).sort()).toEqual(['prog-farmers::lyr', 'prog-pacific::lyr']);
    expect(whileSearching.layerBounds.every((b) => b.locked && b.minShare === 0.5 && b.maxShare === 0.5)).toBe(true);

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search deals' }), { target: { value: '' } });
    await screen.findByText('ILW Market Hedge');
    fireEvent.click(screen.getByRole('button', { name: 'Lock All' }));
    const hedge = (await runAndGetPayload()).layerBounds.find((b) => b.layerKey === 'cand-retro-ilw-fl');
    expect(hedge).toEqual({ layerKey: 'cand-retro-ilw-fl', minShare: 0, maxShare: 0, locked: true });

    fireEvent.click(screen.getByRole('button', { name: '0–150% Open' }));
    expect((await runAndGetPayload()).layerBounds).toEqual([]);
  });

  it('shows the server\'s reason when a run is rejected', async () => {
    await openConfig();
    postMock.mockRejectedValueOnce(new Error('No programmes match the selected deal statuses, so there is no portfolio to optimize'));
    fireEvent.click(screen.getByRole('button', { name: 'Run Frontier Explorer' }));
    expect(await screen.findByText(/No programmes match the selected deal statuses/)).toBeTruthy();
  });

  it('offers a retry when the deal list cannot be loaded', async () => {
    const declined = await openConfig();
    fetchMock.mockRejectedValue(new Error('Backend unreachable'));
    fireEvent.click(declined);
    expect(await screen.findByText('Backend unreachable')).toBeTruthy();
    expect(screen.getByText('Deals could not be loaded')).toBeTruthy();

    fetchMock.mockImplementation(async (url: string) => candidatesPage(url) as never);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    const row = await waitFor(() => rowFor('Zed Declined Re'));
    expect(within(row).getByText('DECLINED')).toBeTruthy();
    expect(screen.queryByText('Backend unreachable')).toBeNull();
  });

  it('offers a retry when a frontier solve fails and succeeds on retry', async () => {
    await screen.findAllByText('Farmers Group');
    postMock.mockRejectedValueOnce(new Error('Could not reach backend API'));
    fireEvent.click(screen.getByRole('button', { name: 'Run Frontier Explorer' }));
    expect(await screen.findByText('Could not reach backend API')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText('Could not reach backend API')).toBeNull());
    expect(await screen.findByText(/Point #1/)).toBeTruthy();
  });
});

describe('PortfolioOptimizer Return / Risk views and peak jump', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderWithMultiPointFrontier() {
    const layers = dealsIn(['BOUND', 'QUOTED']).map((d) => ({
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

    const multiResp = {
      mode: 'FORWARD_PLANNING',
      granularity: 'LAYER',
      view: 'ADJUSTED',
      includedStatuses: defaultStatusesForMode('FORWARD_PLANNING'),
      computeMs: 18,
      initialPortfolio: {
        ...METRICS,
        expectedProfitM: 25.0,
        tvar100M: 60.0,
        tvar250M: 80.0,
        tvar1000M: 95.0,
        stdDevM: 10.0,
        riskRewardRatio: 0.3125,
        returnToTvar100: 0.4167,
        returnToTvar250: 0.3125,
        returnToTvar500: 0.28,
        returnToTvar1000: 0.2632,
        returnToStdDev: 2.5,
      },
      initialLayers: layers,
      recommendedIndex: 2,
      peakReturnToTvar100Index: 1,
      peakReturnToTvar250Index: 1,
      peakReturnToTvar1000Index: 2,
      peakReturnToStdDevIndex: 2,
      frontier: [
        {
          pointIndex: 0,
          targetTvar250M: 35,
          status: 'Optimal',
          solver: 'HiGHS LP',
          metrics: {
            ...METRICS,
            expectedProfitM: 15.0,
            meanRocPct: 12.38,
            tvar100M: 32.0,
            tvar250M: 35.0,
            tvar1000M: 50.0,
            stdDevM: 5.5,
            riskRewardRatio: 0.4286,
            returnToTvar100: 0.4688,
            returnToTvar250: 0.4286,
            returnToTvar500: 0.35,
            returnToTvar1000: 0.3,
            returnToStdDev: 2.73,
          },
          layers,
          constraints: [],
          returnPeriodContributions: [],
        },
        {
          pointIndex: 1,
          targetTvar250M: 52,
          status: 'Optimal',
          solver: 'HiGHS LP',
          metrics: {
            ...METRICS,
            expectedProfitM: 23.0,
            meanRocPct: 17.38,
            tvar100M: 48.0,
            tvar250M: 52.0,
            tvar1000M: 74.0,
            stdDevM: 8.1,
            riskRewardRatio: 0.4423,
            returnToTvar100: 0.4792,
            returnToTvar250: 0.4423,
            returnToTvar500: 0.36,
            returnToTvar1000: 0.3108,
            returnToStdDev: 2.84,
          },
          layers,
          constraints: [],
          returnPeriodContributions: [],
        },
        {
          pointIndex: 2,
          targetTvar250M: 75,
          status: 'Optimal',
          solver: 'HiGHS LP',
          metrics: {
            ...METRICS,
            expectedProfitM: 28.5,
            meanRocPct: 20.81,
            tvar100M: 60.5,
            tvar250M: 74.5,
            tvar1000M: 89.0,
            stdDevM: 9.4,
            riskRewardRatio: 0.3826,
            returnToTvar100: 0.4711,
            returnToTvar250: 0.3826,
            returnToTvar500: 0.34,
            returnToTvar1000: 0.3202,
            returnToStdDev: 3.03,
          },
          layers,
          constraints: [],
          returnPeriodContributions: [],
        },
      ],
    };

    fetchMock.mockImplementation(async (url: string) => candidatesPage(url) as never);
    postMock.mockImplementation(async () => multiResp as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PortfolioOptimizer />
      </QueryClientProvider>,
    );
  }

  it('displays Return / TVaR ratios on KPI strip and jumps directly to the peak point', async () => {
    renderWithMultiPointFrontier();
    await screen.findByText(/Point #3 \/ 3/);

    // KPI strip displays Return / TVaR 100, 250, 1000 and Ret / Std Dev
    const kpiStrip = document.querySelector('.grid-kpi') as HTMLElement;
    expect(within(kpiStrip).getByText('Ret / TVaR 100')).toBeTruthy();
    expect(within(kpiStrip).getByText('Ret / TVaR 250')).toBeTruthy();
    expect(within(kpiStrip).getByText('Ret / TVaR 1000')).toBeTruthy();
    expect(within(kpiStrip).getByText('Ret / Std Dev')).toBeTruthy();
    expect(within(kpiStrip).getByText('38.3%')).toBeTruthy();

    // Jump to Peak button moves from Point #3 (38.3%) to Point #2 (44.2%)
    const jumpBtn = screen.getByRole('button', { name: /Jump to Peak Ret\/TVaR 250 \(#2 · 44\.2%\)/ });
    fireEvent.click(jumpBtn);

    expect(await screen.findByText(/Point #2 \/ 3/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /At Peak Ret\/TVaR 250 \(#2 · 44\.2%\)/ })).toBeTruthy();
    expect(screen.getByText('44.2%')).toBeTruthy();
  });

  it('switches chart presets, toggles the tangent line on Return vs TVaR, and supports Max Return/TVaR objective', async () => {
    renderWithMultiPointFrontier();
    await screen.findByText(/Point #3 \/ 3/);

    const ySelect = screen.getByLabelText('Y-axis metric') as HTMLSelectElement;
    const xSelect = screen.getByLabelText('X-axis metric') as HTMLSelectElement;
    expect(ySelect.value).toBe('returnToTvar250');
    expect(xSelect.value).toBe('tvar250M');
    expect(screen.queryByLabelText(/Tangent/)).toBeNull();

    // Switch to Return vs TVaR preset -> enables Tangent toggle
    fireEvent.click(screen.getByRole('button', { name: 'Return vs TVaR' }));
    expect(ySelect.value).toBe('expectedProfitM');
    expect(xSelect.value).toBe('tvar250M');
    const tangentBox = screen.getByLabelText(/Tangent/) as HTMLInputElement;
    expect(tangentBox.checked).toBe(true);

    // Switch to Ret / Std Dev preset -> active peak updates to Ret/Std Dev (#3 · 3.03×)
    fireEvent.click(screen.getByRole('button', { name: 'Ret / Std Dev' }));
    expect(ySelect.value).toBe('returnToStdDev');
    expect(xSelect.value).toBe('stdDevM');
    expect(screen.getByRole('button', { name: /At Peak Ret\/Std Dev \(#3 · 3\.03×\)/ })).toBeTruthy();

    // Choose Max Return / TVaR 1-in-250 objective in Config and run
    fireEvent.click(screen.getByRole('button', { name: '1. Optimization Config' }));
    fireEvent.change(screen.getByLabelText(/Primary Objective/), {
      target: { value: 'MAX_RETURN_ON_TVAR_250' },
    });
    expect(screen.getByText(/fractional LP\/MILP/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Run Frontier Explorer' }));
    await waitFor(() => expect(lastExplorePayload().objective).toBe('MAX_RETURN_ON_TVAR_250'));
  });
});
