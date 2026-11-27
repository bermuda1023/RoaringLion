// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PortfolioTimelineCompareView } from '../components/PortfolioTimelineCompare';
import type {
  PortfolioCompareOptionsResponse,
  PortfolioCompareResponse,
  PortfolioEpPoint,
  PortfolioEpSlice,
  PortfolioHedgeComparisonResponse,
  PortfolioRollupResponse,
} from '../types/contracts';
import { PortfolioReturnsView } from './PortfolioReturns';

vi.mock('../components/RocCdfChart', () => ({
  RocCdfChart: ({ primaryLabel, comparisonLabel }: { primaryLabel?: string; comparisonLabel?: string }) => (
    <div data-testid="mock-roc-chart">
      {primaryLabel} {comparisonLabel ?? ''}
    </div>
  ),
}));

vi.mock('../components/PortfolioEpChart', () => ({
  PortfolioEpChart: ({
    dimension,
    metric,
    selectedSliceKey,
  }: {
    dimension: string;
    metric: string;
    selectedSliceKey?: string | null;
  }) => (
    <div data-testid="mock-ep-chart">
      {dimension}:{metric}:{selectedSliceKey ?? 'all'}
    </div>
  ),
  PortfolioCompareEpChart: ({ labelA, labelB, curveMode }: { labelA: string; labelB: string; curveMode?: string }) => (
    <div data-testid="mock-compare-ep-chart">
      {labelA} vs {labelB} ({curveMode})
    </div>
  ),
}));

afterEach(() => {
  cleanup();
});

function makeEpPoints(scale = 1): PortfolioEpPoint[] {
  return [
    { returnPeriod: 10, probability: 0.1, oep: 12 * scale, oepTvar: 28 * scale, aep: 14 * scale, aepTvar: 32 * scale },
    { returnPeriod: 25, probability: 0.04, oep: 30 * scale, oepTvar: 44 * scale, aep: 35 * scale, aepTvar: 50 * scale },
    { returnPeriod: 50, probability: 0.02, oep: 45 * scale, oepTvar: 58 * scale, aep: 50 * scale, aepTvar: 64 * scale },
    { returnPeriod: 100, probability: 0.01, oep: 58 * scale, oepTvar: 68 * scale, aep: 62 * scale, aepTvar: 72 * scale },
    { returnPeriod: 250, probability: 0.004, oep: 63 * scale, oepTvar: 71 * scale, aep: 66.2 * scale, aepTvar: 75.71 * scale },
    { returnPeriod: 500, probability: 0.002, oep: 72 * scale, oepTvar: 81 * scale, aep: 78 * scale, aepTvar: 88 * scale },
    { returnPeriod: 1000, probability: 0.001, oep: 82 * scale, oepTvar: 91 * scale, aep: 90 * scale, aepTvar: 99 * scale },
  ];
}

function makeSlice(key: string, label: string, dimension: string, aal: number, scale: number): PortfolioEpSlice {
  return {
    key,
    label,
    dimension,
    aal,
    sd: aal * 3,
    maxOcc: 80 * scale,
    maxYear: 95 * scale,
    epPoints: makeEpPoints(scale),
  };
}

function makeRollup(overrides: Partial<PortfolioRollupResponse>): PortfolioRollupResponse {
  return {
    portfolioId: 'fund-main-j27',
    portfolioName: 'RoaringLion Cat Fund — J27',
    view: 'ADJUSTED',
    currency: 'USD',
    numTrials: 100000,
    computeMs: 12.4,
    includeHedges: true,
    hedgeCount: 1,
    totalCapital: 160,
    deployedCollateral: 160,
    grossPremium: 31.4,
    hedgePremium: 3.5,
    netPremium: 27.9,
    expectedRip: 0.4,
    expectedLoss: 2.7,
    expectedHedgeRecovery: 0.67,
    expectedProfit: 30.61,
    meanRocPct: 19.13,
    noEventRocPct: 20.44,
    breakEvenProbPct: 93.4,
    returnOnTvarPct: 40.43,
    portfolioVar250: 66.2,
    portfolioTvar250: 75.71,
    cotvarSumCheckDiff: 1.4e-14,
    cdfCurve: [{ cumulativeProb: 0, rocPct: 20.4 }],
    quantiles: {
      mean: 19.13,
      noEvents: 20.44,
      '50.0th': 20.44,
      '90.0th': 11.2,
      '95.0th': -5.0,
      '98.0th': -19.5,
      '99.0th': -29.0,
      '99.6th': -42.0,
    },
    guidelines: [
      { percentile: 95, label: '95.0th% (1-in-20)', thresholdRocPct: -15, actualRocPct: -5, passed: true },
      { percentile: 99.6, label: '99.6th% (1-in-250)', thresholdRocPct: -65, actualRocPct: -42, passed: true },
    ],
    epCurve: makeEpPoints(1),
    epByRegion: [
      makeSlice('US', 'US · United States', 'REGION', 1.9, 0.85),
      makeSlice('EU', 'EU · Europe', 'REGION', 0.8, 0.4),
    ],
    epByPeril: [
      makeSlice('WS', 'WS · Windstorm', 'PERIL', 1.7, 0.8),
      makeSlice('EQ', 'EQ · Earthquake', 'PERIL', 1.0, 0.5),
    ],
    epByRegionPeril: [
      makeSlice('US-FL-WS', 'US-FL-WS', 'REGION_PERIL', 1.2, 0.65),
      makeSlice('EU-DE-WS', 'EU-DE-WS', 'REGION_PERIL', 0.8, 0.4),
    ],
    layerAllocations: [
      {
        programmeId: 'prog-farmers-us-27',
        layerId: 'lyr-occ-1',
        layerName: '1st Cat XL',
        cedentName: 'Farmers Group',
        direction: 'ASSUMED',
        isHedge: false,
        limit: 50,
        premium: 11.5,
        signedLine: 1,
        el: 1.6,
        expectedRip: 0.2,
        standaloneTvar250: 50,
        eulerCotvar250: 48.2,
        diversificationCreditPct: 3.6,
        marginalRocPct: 21.0,
      },
      {
        programmeId: 'prog-ilw-fl-wind-27',
        layerId: 'lyr-ilw-1',
        layerName: 'US FL Wind $40B Binary ILW',
        cedentName: 'ILW Market Protection',
        direction: 'EXTERNAL_RETRO',
        isHedge: true,
        limit: 25,
        premium: -3.5,
        signedLine: 1,
        el: -0.67,
        expectedRip: 0,
        standaloneTvar250: -25,
        eulerCotvar250: -15.66,
        diversificationCreditPct: 37.4,
        marginalRocPct: 18.1,
      },
    ],
    manifest: {
      manifestId: 'man-port-1',
      runType: 'PORTFOLIO_ROLLUP',
      createdAt: '2026-11-27T08:00:00Z',
      actor: 'system',
      engineVersion: '0.1.0-rust',
      inputHashes: {},
      parameters: {},
      outputHash: 'abc123',
      breadcrumb: [],
    },
    ...overrides,
  };
}

function makeComparison(accretive = true): PortfolioHedgeComparisonResponse {
  const withoutHedges = makeRollup({
    portfolioId: 'fund-main-j27-gross',
    portfolioName: 'RoaringLion Cat Fund — J27 (Without Hedges)',
    includeHedges: false,
    hedgeCount: 0,
    hedgePremium: 0,
    netPremium: 31.4,
    expectedLoss: 3.37,
    expectedHedgeRecovery: 0,
    expectedProfit: 33.44,
    meanRocPct: 20.9,
    noEventRocPct: 22.62,
    breakEvenProbPct: 92.2,
    returnOnTvarPct: 36.6,
    portfolioVar250: 80.8,
    portfolioTvar250: 91.37,
    epCurve: makeEpPoints(1.2),
    layerAllocations: [makeRollup({}).layerAllocations[0]],
  });
  const withHedges = makeRollup({});
  return {
    withHedges,
    withoutHedges,
    accretion: {
      hasHedges: true,
      hedgeLayerCount: 1,
      hedgePremiumM: 3.5,
      expectedHedgeRecoveryM: 0.67,
      netHedgeCostM: 2.83,
      deltaExpectedProfitM: -2.83,
      deltaMeanRocPct: -1.77,
      deltaNoEventRocPct: -2.18,
      deltaBreakEvenProbPct: 1.2,
      deltaVar250M: -14.6,
      deltaTvar250M: -15.66,
      tvar250ReductionM: 15.66,
      grossReturnOnTvarPct: 36.6,
      netReturnOnTvarPct: accretive ? 40.43 : 32.1,
      deltaReturnOnTvarPct: accretive ? 3.83 : -4.5,
      costPerDollarTvarSaved: 0.1807,
      guidelinesBreachedWithout: 0,
      guidelinesBreachedWith: 0,
      isAccretive: accretive,
      accretionVerdict: accretive ? 'Accretive' : 'Dilutive',
      accretionReason: accretive
        ? 'Hedges lift Return on 1-in-250 TVaR by +3.83 pts and save $15.66m of tail TVaR.'
        : 'Hedges reduce Return on 1-in-250 TVaR by -4.50 pts.',
    },
    manifest: withHedges.manifest,
  };
}

describe('PortfolioReturnsView live rollup EPs, splits, hedge comparison, and Lineage/Guidelines removal', () => {
  it('shows accretion banner, side-by-side table, live EP numbers, and removes Risk Appetite Guidelines & Lineage', () => {
    render(
      <PortfolioReturnsView
        data={makeComparison(true)}
        portfolioView="ADJUSTED"
        onChangeView={() => {}}
        totalCapital={160}
        onChangeCapital={() => {}}
      />,
    );

    const banner = screen.getByTestId('hedge-accretion-banner');
    expect(banner.textContent).toContain('Accretive');
    expect(banner.textContent).toContain('+3.83 pts');
    expect(banner.textContent).toContain('$15.66m');
    expect(banner.textContent).toContain('18.1¢');

    // Risk Appetite Guidelines and Lineage breadcrumb bar must not render
    expect(screen.queryByText(/Risk Appetite Guidelines/i)).toBeNull();
    expect(screen.queryByText(/^Lineage$/i)).toBeNull();

    // Live EP Return Period Numbers table renders 1-in-10 through 1-in-1000
    const epTable = screen.getByTestId('return-period-numbers-table');
    expect(epTable.textContent).toContain('1-in-10');
    expect(epTable.textContent).toContain('1-in-25');
    expect(epTable.textContent).toContain('1-in-100');
    expect(epTable.textContent).toContain('1-in-250');
    expect(epTable.textContent).toContain('1-in-1000');
    expect(epTable.textContent).toContain('$66.20m');
    expect(epTable.textContent).toContain('$75.71m');

    // Side-by-side comparison table visible by default
    const cmpTable = screen.getByTestId('hedge-comparison-table');
    expect(cmpTable.textContent).toContain('Without Hedges (Long Only)');
    expect(cmpTable.textContent).toContain('With Hedges (Net)');
    expect(cmpTable.textContent).toContain('-$15.66m');

    // Euler table shows both Long and Hedge rows in Net/Compare mode
    expect(screen.getByText('ILW Market Protection')).toBeTruthy();
    expect(screen.getByText('Hedge')).toBeTruthy();
    expect(screen.getByText('Long')).toBeTruthy();

    // Switch to Without Hedges -> hides comparison table and hedge layer row, shows Gross TVaR ($91.37m)
    fireEvent.click(screen.getByRole('button', { name: 'Without Hedges' }));
    expect(screen.queryByTestId('hedge-comparison-table')).toBeNull();
    expect(screen.queryByText('ILW Market Protection')).toBeNull();
    expect(screen.getByText('$91.37m')).toBeTruthy();

    // Export CSV link tracks includeHedges=false
    const csvLink = screen.getByRole('link', { name: 'Export CSV' });
    expect(csvLink.getAttribute('href')).toContain('includeHedges=false');

    // Switch to With Hedges -> brings back Hedge layer row and Net TVaR ($75.71m)
    fireEvent.click(screen.getByRole('button', { name: /With Hedges/ }));
    expect(screen.getByText('ILW Market Protection')).toBeTruthy();
    expect(screen.getAllByText('$75.71m').length).toBeGreaterThan(0);
    expect(csvLink.getAttribute('href')).toContain('includeHedges=true');
  });

  it('switches EP splits across Region, Peril, and Region-Peril and supports row drill-down', () => {
    render(
      <PortfolioReturnsView
        data={makeComparison(true)}
        portfolioView="ADJUSTED"
        onChangeView={() => {}}
        totalCapital={160}
        onChangeCapital={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /By Region \(2\)/ }));
    const regionTable = screen.getByTestId('ep-splits-table');
    expect(regionTable.textContent).toContain('US · United States');
    expect(regionTable.textContent).toContain('EU · Europe');
    expect(screen.getByTestId('mock-ep-chart').textContent).toContain('REGION:aep:all');

    // Click OEP metric toggle
    fireEvent.click(screen.getByRole('button', { name: 'OEP' }));
    expect(screen.getByTestId('mock-ep-chart').textContent).toContain('REGION:oep:all');

    // Click US row to drill down its full return-period table
    fireEvent.click(screen.getByText('US · United States'));
    expect(screen.getByTestId('mock-ep-chart').textContent).toContain('REGION:oep:US');
    expect(screen.getByTestId('return-period-numbers-table').textContent).toContain('US · United States');

    // Switch to By Peril and By Region-Peril
    fireEvent.click(screen.getByRole('button', { name: /By Peril \(2\)/ }));
    expect(screen.getByTestId('ep-splits-table').textContent).toContain('WS · Windstorm');
    expect(screen.getByTestId('ep-splits-table').textContent).toContain('EQ · Earthquake');

    fireEvent.click(screen.getByRole('button', { name: /By Region-Peril \(2\)/ }));
    expect(screen.getByTestId('ep-splits-table').textContent).toContain('US-FL-WS');
    expect(screen.getByTestId('ep-splits-table').textContent).toContain('EU-DE-WS');
  });

  it('displays Dilutive verdict when hedges fail to improve return on TVaR', () => {
    render(
      <PortfolioReturnsView
        data={makeComparison(false)}
        portfolioView="ADJUSTED"
        onChangeView={() => {}}
        totalCapital={160}
        onChangeCapital={() => {}}
      />,
    );

    const banner = screen.getByTestId('hedge-accretion-banner');
    expect(banner.textContent).toContain('Dilutive');
    expect(banner.textContent).toContain('-4.50 pts');
  });

  it('displays auto-calculated Deployed Capital KPI and flags violation when Deployed Capital > Fund Capital', () => {
    const cmp = makeComparison(true);
    cmp.withHedges.deployedCollateral = 195.5;
    cmp.withHedges.capitalUtilizationPct = 122.19;
    cmp.withHedges.capitalHeadroomM = -35.5;
    cmp.withHedges.capitalExceeded = true;

    render(
      <PortfolioReturnsView
        data={cmp}
        portfolioView="ADJUSTED"
        onChangeView={() => {}}
        totalCapital={160}
        onChangeCapital={() => {}}
      />,
    );

    const kpi = screen.getByTestId('deployed-capital-kpi');
    expect(kpi.textContent).toContain('$195.50m');
    expect(kpi.textContent).toContain('122.2%');
    expect(kpi.textContent).toContain('Over by $35.5m');

    const alert = screen.getByTestId('capital-exceeded-banner');
    expect(alert.textContent).toContain('Capital Constraint Violation');
    expect(alert.textContent).toContain('$195.50m');
    expect(alert.textContent).toContain('$160.00m');
  });
});

describe('PortfolioTimelineCompareView (YoY, Plan vs Current, flexible A vs B)', () => {
  const mockOptions: PortfolioCompareOptionsResponse = {
    treatyYears: [2027, 2026],
    statuses: [
      { status: 'BOUND', count: 2 },
      { status: 'QUOTED', count: 1 },
      { status: 'EXPIRED', count: 1 },
    ],
    renewalPeriods: ['J27', 'J26'],
    regionPerils: ['US-FL-WS', 'EU-DE-WS'],
    presets: [
      {
        id: 'plan-vs-current',
        name: 'Plan vs Current (2027)',
        description: '2027 Bound + Quoted vs 2027 Bound',
        portfolioA: { label: '2027 Plan', treatyYears: [2027], statuses: ['BOUND', 'QUOTED'], includeHedges: true },
        portfolioB: { label: '2027 Bound', treatyYears: [2027], statuses: ['BOUND'], includeHedges: true },
      },
      {
        id: 'yoy-book',
        name: 'YoY Book (2027 vs 2026)',
        description: '2027 Bound vs 2026 Prior UY',
        portfolioA: { label: '2027 Bound', treatyYears: [2027], statuses: ['BOUND'], includeHedges: true },
        portfolioB: { label: '2026 Prior UY', treatyYears: [2026], statuses: ['EXPIRED'], includeHedges: true },
      },
    ],
    contracts: [
      { programmeId: 'p1', treatyYear: 2027, status: 'BOUND', direction: 'ASSUMED', isHedge: false, inceptionDate: '2027-01-01', renewalPeriod: 'J27' },
      { programmeId: 'p2', treatyYear: 2027, status: 'BOUND', direction: 'EXTERNAL_RETRO', isHedge: true, inceptionDate: '2027-01-01', renewalPeriod: 'J27' },
      { programmeId: 'p3', treatyYear: 2027, status: 'QUOTED', direction: 'ASSUMED', isHedge: false, inceptionDate: '2027-01-01', renewalPeriod: 'J27' },
      { programmeId: 'p4', treatyYear: 2026, status: 'EXPIRED', direction: 'ASSUMED', isHedge: false, inceptionDate: '2026-01-01', renewalPeriod: 'J26' },
    ],
  };

  const mockCompareResult: PortfolioCompareResponse = {
    currency: 'USD',
    portfolioA: makeRollup({ portfolioName: '2027 Plan', netPremium: 34.0, expectedLoss: 3.1, expectedProfit: 35.7, portfolioTvar250: 82.0 }),
    portfolioB: makeRollup({ portfolioName: '2027 Bound', netPremium: 27.9, expectedLoss: 2.7, expectedProfit: 30.61, portfolioTvar250: 75.71 }),
    programmeCountA: 3,
    programmeCountB: 2,
    delta: {
      programmeCountA: 3,
      programmeCountB: 2,
      layerCountA: 4,
      layerCountB: 2,
      deltaNetPremiumM: 6.1,
      deltaGrossPremiumM: 6.1,
      deltaExpectedLossM: 0.4,
      deltaExpectedProfitM: 5.09,
      deltaMeanRocPct: 3.18,
      deltaNoEventRocPct: 3.81,
      deltaBreakEvenProbPct: 0.5,
      deltaReturnOnTvarPct: 3.1,
      deltaVar250M: 5.0,
      deltaTvar250M: 6.29,
      deltaOep100M: 4.0,
      deltaOep250M: 5.5,
      deltaAep100M: 4.2,
      deltaAep250M: 5.0,
    },
    epDeltas: [
      { returnPeriod: 100, probability: 0.01, oepA: 62, oepB: 58, deltaOep: 4, oepTvarA: 72, oepTvarB: 68, deltaOepTvar: 4, aepA: 66.2, aepB: 62, deltaAep: 4.2, aepTvarA: 77, aepTvarB: 72, deltaAepTvar: 5 },
      { returnPeriod: 250, probability: 0.004, oepA: 68.5, oepB: 63, deltaOep: 5.5, oepTvarA: 77, oepTvarB: 71, deltaOepTvar: 6, aepA: 71.2, aepB: 66.2, deltaAep: 5, aepTvarA: 82, aepTvarB: 75.71, deltaAepTvar: 6.29 },
    ],
    regionDeltas: [
      { key: 'US', label: 'US · United States', dimension: 'REGION', aalA: 2.3, aalB: 1.9, deltaAal: 0.4, oep100A: 54, oep100B: 50, deltaOep100: 4, oep250A: 66, oep250B: 60, deltaOep250: 6, aep100A: 58, aep100B: 53, deltaAep100: 5, aep250A: 70, aep250B: 64, deltaAep250: 6, tvar250A: 80, tvar250B: 74, deltaTvar250: 6 },
    ],
    perilDeltas: [
      { key: 'WS', label: 'WS · Windstorm', dimension: 'PERIL', aalA: 2.1, aalB: 1.7, deltaAal: 0.4, oep100A: 50, oep100B: 46, deltaOep100: 4, oep250A: 62, oep250B: 56, deltaOep250: 6, aep100A: 54, aep100B: 50, deltaAep100: 4, aep250A: 65, aep250B: 60, deltaAep250: 5, tvar250A: 75, tvar250B: 69, deltaTvar250: 6 },
    ],
    regionPerilDeltas: [
      { key: 'US-FL-WS', label: 'US-FL-WS', dimension: 'REGION_PERIL', aalA: 1.5, aalB: 1.2, deltaAal: 0.3, oep100A: 42, oep100B: 38, deltaOep100: 4, oep250A: 50, oep250B: 46, deltaOep250: 4, aep100A: 45, aep100B: 40, deltaAep100: 5, aep250A: 52, aep250B: 48, deltaAep250: 4, tvar250A: 60, tvar250B: 55, deltaTvar250: 5 },
    ],
    manifest: makeRollup({}).manifest,
  };

  it('renders presets, live matched counts, side-by-side KPIs, overlaid EPs, and Region/Peril/Region-Peril deltas', () => {
    const onRun = vi.fn();
    render(
      <PortfolioTimelineCompareView
        options={mockOptions}
        result={mockCompareResult}
        isPending={false}
        error={null}
        defaultCapital={160}
        defaultView="ADJUSTED"
        onRunCompare={onRun}
      />,
    );

    // Initial matched counts from Plan vs Current preset (3 vs 2)
    expect(screen.getByTestId('slice-builder-a-count').textContent).toContain('3 programmes');
    expect(screen.getByTestId('slice-builder-b-count').textContent).toContain('2 programmes');

    // KPIs & deltas rendered
    const kpi = screen.getByTestId('compare-kpi-table');
    expect(kpi.textContent).toContain('+$6.10m');
    expect(kpi.textContent).toContain('+$5.09m');
    expect(kpi.textContent).toContain('+$6.29m');

    // Overlaid EP chart & return period table rendered
    expect(screen.getByTestId('mock-compare-ep-chart').textContent).toContain('2027 Plan vs 2027 Bound');
    expect(screen.getByTestId('compare-ep-section').textContent).toContain('1-in-250');

    // Region / Peril / Region-Peril delta tabs work
    expect(screen.getByTestId('compare-splits-table').textContent).toContain('US · United States');
    fireEvent.click(screen.getByRole('button', { name: /By Peril \(1\)/ }));
    expect(screen.getByTestId('compare-splits-table').textContent).toContain('WS · Windstorm');
    fireEvent.click(screen.getByRole('button', { name: /By Region-Peril \(1\)/ }));
    expect(screen.getByTestId('compare-splits-table').textContent).toContain('US-FL-WS');

    // Clicking YoY Book preset triggers onRunCompare with 2027 vs 2026 specs and updates live counts (2 vs 1)
    fireEvent.click(screen.getByRole('button', { name: 'YoY Book (2027 vs 2026)' }));
    expect(onRun).toHaveBeenCalledTimes(1);
    const callArg = onRun.mock.calls[0][0];
    expect(callArg.portfolioA.treatyYears).toEqual([2027]);
    expect(callArg.portfolioB.treatyYears).toEqual([2026]);
    expect(screen.getByTestId('slice-builder-a-count').textContent).toContain('2 programmes');
    expect(screen.getByTestId('slice-builder-b-count').textContent).toContain('1 programme');
  });

  it('respects UNADJUSTED defaultView in both slice View selectors when presets omit view', () => {
    render(
      <PortfolioTimelineCompareView
        options={mockOptions}
        result={null}
        isPending={false}
        error={null}
        defaultCapital={160}
        defaultView="UNADJUSTED"
        onRunCompare={() => {}}
      />,
    );
    const viewA = screen.getByLabelText('Portfolio A (Target / Current) view') as HTMLSelectElement;
    const viewB = screen.getByLabelText('Portfolio B (Baseline / Prior) view') as HTMLSelectElement;
    expect(viewA.value).toBe('UNADJUSTED');
    expect(viewB.value).toBe('UNADJUSTED');
  });
});
