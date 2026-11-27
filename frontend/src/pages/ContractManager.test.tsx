// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
