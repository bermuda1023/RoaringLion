// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ContractManager } from './ContractManager';

const contractFixture = {
  programmeId: 'prog-farmers-us-27',
  chainId: 'chain-prog-farmers-us',
  priorContractId: null,
  cedentName: 'Farmers Group',
  programmeName: 'US Wind Occ + Agg Tower',
  treatyYear: 2027,
  renewalPeriod: 'J27',
  office: 'BDA',
  underwriter: 'Lead Underwriter',
  broker: 'Broker A',
  status: 'BOUND',
  direction: 'ASSUMED',
  isHedge: false,
  structure: 'OCC_XL',
  instrument: 'UNL',
  placement: 'OPEN_MARKET',
  hoursClause: 72,
  writtenLine: 1.0,
  signedLine: 1.0,
  limit: 85.0,
  attachment: 80.0,
  depositPremium: 19.3,
  rol: 0.2271,
  brokeragePct: 0.1,
  frontingFeePct: 0.02,
  collateralRequired: 85.0,
  currency: 'USD',
  simSetId: 'sim-1',
  regionPerils: ['US-FL-WS'],
  perils: ['WS'],
  customTags: [],
  upliftSet: { upliftSetId: 'u1', name: 'Uplift', version: 1, rules: [], contentHash: '' },
  graph: {
    graphId: 'g1',
    name: 'Graph',
    version: 1,
    currency: 'USD',
    nodes: [
      { nodeId: 'lyr-1', nodeType: 'OCC_XL', label: 'L1', params: { occLimit: 50, depositPremium: 11.5, placedShare: 1.0, rarcPct: 6.8 }, position: { x: 0, y: 0 } },
      { nodeId: 'lyr-2', nodeType: 'AGG_XL', label: 'L2', params: { aggLimit: 35, depositPremium: 7.8, placedShare: 1.0, rarcPct: 5.1 }, position: { x: 0, y: 120 } },
    ],
    edges: [],
    contentHash: '',
  },
  profile: {
    uniqueCode: 'RL-2027-001',
    programCode: 'PGM-001',
    legalEntity: 'RoaringLion Re Transformer',
    tradingUnit: 'N.A. Property',
    planningClass: 'Property CAT',
    newOrRenewal: 'RENEWAL',
    isMultiYear: false,
    cedentGroup: 'Farmers Group',
    cedentCountry: 'USA',
    cedentCreditRating: 'AM Best A',
    brokerGroup: 'Broker A',
    estimatedSignedPct: 1.0,
    limitBasis: 'UNL',
    claimTrigger: 'LOSSES_OCCURRING',
    lossEventType: '1ST_LOSS',
    reinstatementBasis: 'PRO_RATA_AMOUNT',
    economicInflationPct: 3.5,
    socialInflationPct: 1.2,
    gearingEffectPct: 14.5,
    rarcExposurePct: 1.8,
    rarcLayerPct: -2.1,
    rarcPricePct: 6.4,
    rarcTotalPct: 6.1,
  },
  version: 1,
  contentHash: 'abc',
};

const dossierFixture = {
  contract: contractFixture,
  relatedProgrammes: [],
  renewalChain: [],
  notes: [],
  pricingLink: {
    graphId: 'g1',
    graphVersion: 1,
    computeMs: 1.2,
    programmeLossRatio: 0.1095,
    unadjustedNodes: [],
    adjustedNodes: [
      { nodeId: 'lyr-1', nodeLabel: 'L1', nodeType: 'OCC_XL', el: 1.835, expectedRip: 0.414, elr: 0.154, epPoints: [] },
      { nodeId: 'lyr-2', nodeLabel: 'L2', nodeType: 'AGG_XL', el: 0.324, expectedRip: 0.0, elr: 0.0415, epPoints: [] },
    ],
  },
  exposureLink: null,
  modelingLink: { simSet: null, upliftSet: contractFixture.upliftSet, modelRuns: [] },
  claimsCount: 0,
  recentManifests: [],
};

describe('ContractManager CMS cleanups', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('displays read-only programme RARC rollup and ROL / Loss Ratio in summary without Tech ROL', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/api/contracts')) return { ok: true, json: async () => [contractFixture] };
        if (url.includes('/dossier')) return { ok: true, json: async () => dossierFixture };
        if (url.includes('/layers')) return { ok: true, json: async () => [] };
        return { ok: true, json: async () => [] };
      }),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContractManager onSelectProgramme={() => undefined} />
      </QueryClientProvider>,
    );

    // KPI ribbon shows ROL / Loss Ratio (22.7% / 11.0%), no Tech ROL
    expect(await screen.findByText('ROL / Loss Ratio')).toBeTruthy();
    expect(screen.getByText('22.7% / 11.0%')).toBeTruthy();
    expect(screen.queryByText(/Tech ROL/i)).toBeNull();

    // Programme Profile displays RARC as read-only rollup (not an input)
    const rarcReadonly = screen.getByTestId('programme-rarc-readonly');
    expect(rarcReadonly.textContent).toBe('+6.10%');
    expect(screen.queryByLabelText(/Risk-Adjusted Rate Change \(RARC\) %/i)).toBeNull();

    // Clicking "Edit per layer" switches to the Layers & Lines tab
    fireEvent.click(screen.getByRole('button', { name: /Edit per layer/i }));
    expect(await screen.findByText(/Layer Terms & Lines/i)).toBeTruthy();
  });
});

const HEDGE_MOCK_CONTRACTS = [
  contractFixture,
  {
    ...contractFixture,
    programmeId: 'prog-ilw-fl-wind-27',
    chainId: 'chain-ilw',
    cedentName: 'ILW Market Protection',
    programmeName: 'US FL Wind $40B Binary ILW',
    direction: 'EXTERNAL_RETRO',
    isHedge: true,
    structure: 'ILW',
    instrument: 'INDUSTRY_INDEX',
    placement: 'PRIVATE',
    simSetId: 'ind-synth-cat-v1-100k',
    graph: {
      graphId: 'g2',
      name: 'g2',
      version: 1,
      currency: 'USD',
      nodes: [{ nodeId: 'ilw1', nodeType: 'ILW_OPTION', label: 'ILW', params: {}, position: { x: 0, y: 0 } }],
      edges: [],
      contentHash: '',
    },
    depositPremium: 3.5,
    limit: 25,
    attachment: 40,
    rol: 0.14,
    brokeragePct: 0,
    collateralRequired: 0,
    profile: { ...contractFixture.profile, uniqueCode: 'RL-2027-004', programCode: 'PGM-004', rarcTotalPct: -2.5 },
  },
  {
    ...contractFixture,
    programmeId: 'prog-retro-unl-27',
    chainId: 'chain-retro',
    cedentName: 'RoaringLion UNL Book',
    programmeName: 'Whole-Account UNL Retro XL',
    status: 'QUOTED',
    direction: 'EXTERNAL_RETRO',
    isHedge: true,
    structure: 'OCC_XL',
    instrument: 'UNL',
    portfolioSnapshotDate: '2027-01-01',
    placement: 'PRIVATE',
    graph: {
      graphId: 'g3',
      name: 'g3',
      version: 1,
      currency: 'USD',
      nodes: [{ nodeId: 'r1', nodeType: 'OCC_XL', label: 'Retro', params: {}, position: { x: 0, y: 0 } }],
      edges: [],
      contentHash: '',
    },
    depositPremium: 3.5,
    limit: 35,
    attachment: 45,
    rol: 0.1,
    brokeragePct: 0,
    collateralRequired: 0,
    regionPerils: [],
    profile: { ...contractFixture.profile, uniqueCode: 'RL-2027-005', programCode: 'PGM-005', rarcTotalPct: -1.0 },
  },
];

describe('ContractManager ILW, Retro Hedge, and Long direction support', () => {
  let createdPayloads: Record<string, unknown>[] = [];

  beforeEach(() => {
    createdPayloads = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/api/contracts') && (!init?.method || init.method === 'GET')) {
          return new Response(JSON.stringify(HEDGE_MOCK_CONTRACTS), { status: 200 });
        }
        if (url.endsWith('/api/contracts') && init?.method === 'POST') {
          const parsed = JSON.parse(String(init.body)) as Record<string, unknown>;
          createdPayloads.push(parsed);
          return new Response(JSON.stringify({ ...HEDGE_MOCK_CONTRACTS[1], programmeId: 'prog-new-ilw' }), { status: 200 });
        }
        if (url.includes('/dossier')) {
          return new Response(JSON.stringify(dossierFixture), { status: 200 });
        }
        return new Response(JSON.stringify([]), { status: 200 });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it('shows ILWs and UNL Retro Hedges in Contracts ledger with direction/type filters and auto-wired New Deal creation', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ContractManager onSelectProgramme={() => {}} />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText('US FL Wind $40B Binary ILW')).toBeTruthy();
      expect(screen.getByText('Whole-Account UNL Retro XL')).toBeTruthy();
    });

    // Filter by Dir = Hedge -> hides Long Farmers programme
    fireEvent.change(screen.getByLabelText('Filter Dir'), { target: { value: 'Hedge' } });
    expect(screen.queryByText('US Wind Occ + Agg Tower')).toBeNull();
    expect(screen.getByText('US FL Wind $40B Binary ILW')).toBeTruthy();
    expect(screen.getByText('Whole-Account UNL Retro XL')).toBeTruthy();

    // Filter by Type = ILW -> shows only ILW
    fireEvent.change(screen.getByLabelText('Filter Type'), { target: { value: 'ILW' } });
    expect(screen.getByText('US FL Wind $40B Binary ILW')).toBeTruthy();
    expect(screen.queryByText('Whole-Account UNL Retro XL')).toBeNull();

    // Open + New Deal and verify UNL Retro Snapshot & ILW Industry auto-wiring controls
    fireEvent.click(screen.getByRole('button', { name: '+ New Deal' }));
    fireEvent.change(screen.getByLabelText('Deal direction'), { target: { value: 'EXTERNAL_RETRO' } });
    expect(screen.getByText(/connects directly to our own UNL Portfolio Snapshot/i)).toBeTruthy();
    expect(screen.getByLabelText(/UNL Snapshot As-Of/i)).toBeTruthy();

    // Switch basis to ILW -> shows Industry Loss Run auto-wiring & Trigger ($B)
    fireEvent.change(screen.getByLabelText('Programme basis'), { target: { value: 'ILW' } });
    expect(screen.getByText(/connects directly to the Industry Loss Run/i)).toBeTruthy();
    expect(screen.getByLabelText('ILW trigger type')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Save Contract' }));
    await waitFor(() => {
      expect(createdPayloads.length).toBe(1);
    });
    expect(createdPayloads[0]).toMatchObject({
      direction: 'EXTERNAL_RETRO',
      isHedge: true,
      structure: 'ILW',
      instrument: 'INDUSTRY_INDEX',
    });
  });

  it('re-fetches contracts when expectedCount disagrees with stale cached rows and surfaces refresh errors', async () => {
    const makeBatch = (n: number) =>
      Array.from({ length: n }, (_, i) => ({
        ...contractFixture,
        programmeId: i === 0 ? 'prog-farmers-us-27' : `prog-syn-${String(i).padStart(3, '0')}`,
        cedentName: `Cedent ${i + 1}`,
        programmeName: `Programme ${i + 1}`,
        profile: { ...contractFixture.profile, uniqueCode: `RL-2027-${String(i + 1).padStart(3, '0')}` },
      }));

    let fetchCount = 0;
    let shouldFail = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('/api/contracts')) {
          fetchCount += 1;
          if (shouldFail) {
            return new Response('Bad Gateway', { status: 502 });
          }
          return new Response(JSON.stringify(fetchCount === 1 ? makeBatch(6) : makeBatch(48)), { status: 200 });
        }
        if (url.includes('/dossier')) {
          return new Response(JSON.stringify(dossierFixture), { status: 200 });
        }
        return new Response(JSON.stringify([]), { status: 200 });
      }),
    );

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <ContractManager onSelectProgramme={() => {}} expectedCount={6} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('6 of 6')).toBeTruthy();

    // Header pill updates to 48 (e.g. after backend redeploy): ledger must re-fetch and match 48 of 48
    rerender(
      <QueryClientProvider client={qc}>
        <ContractManager onSelectProgramme={() => {}} expectedCount={48} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('48 of 48')).toBeTruthy();
    expect(fetchCount).toBe(2);

    // If a subsequent refresh fails with 502, an explicit error banner + Retry button appear
    shouldFail = true;
    rerender(
      <QueryClientProvider client={qc}>
        <ContractManager onSelectProgramme={() => {}} expectedCount={49} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Could not load contracts ledger/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
});
