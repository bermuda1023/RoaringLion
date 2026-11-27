// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { OptimizerStabilityPanel } from './OptimizerStabilityPanel';
import type { DealStabilitySummary } from '../types/contracts';

afterEach(cleanup);

const SAMPLE_STABILITY: DealStabilitySummary = {
  feasiblePointCount: 6,
  totalPointCount: 6,
  consistentGrowCount: 1,
  consistentShrinkCount: 1,
  unstableCount: 1,
  thresholds: {
    boundTolerance: 0.02,
    deltaTolerance: 0.02,
    consensusFraction: 0.7,
    dispersionIqrCutoff: 0.25,
    dispersionStdCutoff: 0.3,
  },
  growThemes: {
    dealCount: 1,
    deployedCapitalM: 20.0,
    shareOfDeployedCapitalPct: 57.1,
    expectedReturnM: 4.0,
    shareOfExpectedReturnPct: 66.7,
    topCedents: [
      {
        dimension: 'CEDENT',
        key: 'Alpha Re',
        dealCount: 1,
        deployedCapitalM: 20.0,
        shareOfDeployedCapitalPct: 57.1,
        expectedReturnM: 4.0,
        shareOfExpectedReturnPct: 66.7,
      },
    ],
    topRegions: [
      {
        dimension: 'REGION',
        key: 'US-MW',
        dealCount: 1,
        deployedCapitalM: 20.0,
        shareOfDeployedCapitalPct: 57.1,
        expectedReturnM: 4.0,
        shareOfExpectedReturnPct: 66.7,
      },
    ],
    topPerils: [
      {
        dimension: 'PERIL',
        key: 'CS',
        dealCount: 1,
        deployedCapitalM: 20.0,
        shareOfDeployedCapitalPct: 57.1,
        expectedReturnM: 4.0,
        shareOfExpectedReturnPct: 66.7,
      },
    ],
  },
  shrinkThemes: {
    dealCount: 1,
    deployedCapitalM: 10.0,
    shareOfDeployedCapitalPct: 41.7,
    expectedReturnM: 0.5,
    shareOfExpectedReturnPct: 12.5,
    topCedents: [
      {
        dimension: 'CEDENT',
        key: 'Beta Ins',
        dealCount: 1,
        deployedCapitalM: 10.0,
        shareOfDeployedCapitalPct: 41.7,
        expectedReturnM: 0.5,
        shareOfExpectedReturnPct: 12.5,
      },
    ],
    topRegions: [
      {
        dimension: 'REGION',
        key: 'US-FL',
        dealCount: 1,
        deployedCapitalM: 10.0,
        shareOfDeployedCapitalPct: 41.7,
        expectedReturnM: 0.5,
        shareOfExpectedReturnPct: 12.5,
      },
    ],
    topPerils: [
      {
        dimension: 'PERIL',
        key: 'WS',
        dealCount: 1,
        deployedCapitalM: 10.0,
        shareOfDeployedCapitalPct: 41.7,
        expectedReturnM: 0.5,
        shareOfExpectedReturnPct: 12.5,
      },
    ],
  },
  deals: [
    {
      layerKey: 'A-max',
      programmeId: 'p1',
      layerId: 'l1',
      cedentName: 'Alpha Re',
      programmeName: 'Alpha Cat XL',
      layerName: 'SCS Agg XL',
      direction: 'ASSUMED',
      status: 'BOUND',
      regionPerils: ['US-MW-CS'],
      classification: 'CONSISTENT_GROW',
      locked: false,
      stabilityScore: 1.0,
      currentShare: 0.2,
      recommendedShare: 1.0,
      minShare: 1.0,
      medianShare: 1.0,
      maxShare: 1.0,
      meanShare: 1.0,
      stdShare: 0.0,
      iqrShare: 0.0,
      boundMinShare: 0.0,
      boundMaxShare: 1.0,
      medianDeltaShare: 0.8,
      pctAtMax: 1.0,
      pctAtMin: 0.0,
      pctGrown: 1.0,
      pctShrunk: 0.0,
      growSupport: 1.0,
      shrinkSupport: 0.0,
      currentCapitalM: 4.0,
      meanOptimalCapitalM: 20.0,
      currentExpectedProfitM: 0.8,
      meanOptimalExpectedProfitM: 4.0,
    },
    {
      layerKey: 'B-min',
      programmeId: 'p2',
      layerId: 'l2',
      cedentName: 'Beta Ins',
      programmeName: 'Beta FL XL',
      layerName: 'FL 2nd XL',
      direction: 'ASSUMED',
      status: 'BOUND',
      regionPerils: ['US-FL-WS'],
      classification: 'CONSISTENT_SHRINK',
      locked: false,
      stabilityScore: 1.0,
      currentShare: 0.25,
      recommendedShare: 0.0,
      minShare: 0.0,
      medianShare: 0.0,
      maxShare: 0.0,
      meanShare: 0.0,
      stdShare: 0.0,
      iqrShare: 0.0,
      boundMinShare: 0.0,
      boundMaxShare: 1.0,
      medianDeltaShare: -0.25,
      pctAtMax: 0.0,
      pctAtMin: 1.0,
      pctGrown: 0.0,
      pctShrunk: 1.0,
      growSupport: 0.0,
      shrinkSupport: 1.0,
      currentCapitalM: 10.0,
      meanOptimalCapitalM: 0.0,
      currentExpectedProfitM: 0.5,
      meanOptimalExpectedProfitM: 0.0,
    },
    {
      layerKey: 'C-swing',
      programmeId: 'p3',
      layerId: 'l3',
      cedentName: 'Gamma Mut',
      programmeName: 'Gamma EQ',
      layerName: 'CA EQ XL',
      direction: 'ASSUMED',
      status: 'QUOTED',
      regionPerils: ['US-CA-EQ'],
      classification: 'UNSTABLE',
      locked: false,
      stabilityScore: 0.25,
      currentShare: 0.2,
      recommendedShare: 1.0,
      minShare: 0.0,
      medianShare: 0.5,
      maxShare: 1.0,
      meanShare: 0.5,
      stdShare: 0.5,
      iqrShare: 1.0,
      boundMinShare: 0.0,
      boundMaxShare: 1.0,
      medianDeltaShare: 0.3,
      pctAtMax: 0.5,
      pctAtMin: 0.5,
      pctGrown: 0.5,
      pctShrunk: 0.5,
      growSupport: 0.5,
      shrinkSupport: 0.5,
      currentCapitalM: 6.0,
      meanOptimalCapitalM: 15.0,
      currentExpectedProfitM: 0.6,
      meanOptimalExpectedProfitM: 1.5,
    },
  ],
};

describe('OptimizerStabilityPanel', () => {
  it('renders bucket counts, theme summaries, and all three deal rows', () => {
    render(<OptimizerStabilityPanel stability={SAMPLE_STABILITY} />);

    expect(screen.getByText(/Deal Stability Across Frontier \(6\/6 feasible points\)/i)).toBeTruthy();
    const growCard = screen.getByTestId('stability-grow-themes');
    expect(within(growCard).getByText(/Always Grow Themes \(1\)/i)).toBeTruthy();
    expect(within(growCard).getByText('CS')).toBeTruthy();
    expect(within(growCard).getByText('US-MW')).toBeTruthy();

    const shrinkCard = screen.getByTestId('stability-shrink-themes');
    expect(within(shrinkCard).getByText(/Always Shrink \/ Drop Themes \(1\)/i)).toBeTruthy();
    expect(within(shrinkCard).getByText('WS')).toBeTruthy();
    expect(within(shrinkCard).getByText('US-FL')).toBeTruthy();

    expect(screen.getByText('SCS Agg XL · US-MW-CS')).toBeTruthy();
    expect(screen.getByText('FL 2nd XL · US-FL-WS')).toBeTruthy();
    expect(screen.getByText('CA EQ XL · US-CA-EQ')).toBeTruthy();
  });

  it('filters table rows by stability bucket', () => {
    render(<OptimizerStabilityPanel stability={SAMPLE_STABILITY} />);

    fireEvent.click(screen.getByRole('button', { name: /Always Grow \(1\)/i }));
    expect(screen.getByText('SCS Agg XL · US-MW-CS')).toBeTruthy();
    expect(screen.queryByText('FL 2nd XL · US-FL-WS')).toBeNull();
    expect(screen.queryByText('CA EQ XL · US-CA-EQ')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Unstable \(1\)/i }));
    expect(screen.getByText('CA EQ XL · US-CA-EQ')).toBeTruthy();
    expect(screen.queryByText('SCS Agg XL · US-MW-CS')).toBeNull();
  });

  it('shows empty notice when no feasible frontier points exist', () => {
    render(
      <OptimizerStabilityPanel
        stability={{
          ...SAMPLE_STABILITY,
          feasiblePointCount: 0,
          consistentGrowCount: 0,
          consistentShrinkCount: 0,
          unstableCount: 0,
          deals: [],
        }}
      />,
    );
    expect(screen.getByText(/No feasible frontier points available to classify deal stability/i)).toBeTruthy();
  });
});
