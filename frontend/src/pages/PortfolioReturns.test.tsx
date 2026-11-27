// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PortfolioHedgeComparisonResponse, PortfolioRollupResponse } from '../types/contracts';
import { PortfolioReturnsView } from './PortfolioReturns';

vi.mock('../components/RocCdfChart', () => ({
  RocCdfChart: ({ primaryLabel, comparisonLabel }: { primaryLabel?: string; comparisonLabel?: string }) => (
    <div data-testid="mock-roc-chart">
      {primaryLabel} {comparisonLabel ?? ''}
    </div>
  ),
}));

afterEach(() => {
  cleanup();
});

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
    quantiles: { mean: 19.13 },
    guidelines: [
      { percentile: 95, label: '95.0th% (1-in-20)', thresholdRocPct: -15, actualRocPct: -5, passed: true },
      { percentile: 99.6, label: '99.6th% (1-in-250)', thresholdRocPct: -65, actualRocPct: -42, passed: true },
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

describe('PortfolioReturnsView with-hedges vs without-hedges comparison', () => {
  it('shows accretion banner, side-by-side table, and toggles between With Hedges and Without Hedges', () => {
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
    expect(screen.getByText('$75.71m')).toBeTruthy();
    expect(csvLink.getAttribute('href')).toContain('includeHedges=true');
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
