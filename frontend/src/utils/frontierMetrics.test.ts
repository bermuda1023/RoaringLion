import { describe, expect, it } from 'vitest';
import type { FrontierExplorerPoint, PortfolioMetricsSnapshot } from '../components/OptimizerCharts';
import {
  activeRiskHorizon,
  canShowTangent,
  computeTangentRay,
  detectActivePreset,
  findPeakReturnRiskPoint,
  formatAxisTick,
  formatAxisValue,
  formatRatioDual,
  formatRatioHeadline,
  getAxisPlotValue,
  getRatioValue,
} from './frontierMetrics';

function makeMetrics(overrides: Partial<PortfolioMetricsSnapshot> = {}): PortfolioMetricsSnapshot {
  return {
    expectedProfitM: 20,
    meanRocPct: 15.5,
    deployedCollateralM: 120,
    grossPremiumM: 26,
    expectedLossM: 6,
    elCapitalRatio: 0.0375,
    riskRewardRatio: 0.4,
    stdDevM: 8,
    returnToTvar100: 0.45,
    returnToTvar250: 0.4,
    returnToTvar500: 0.34,
    returnToTvar1000: 0.3,
    returnToStdDev: 2.5,
    tvar50M: 35,
    tvar100M: 44.44,
    tvar250M: 50,
    tvar500M: 58.82,
    tvar1000M: 66.67,
    var100M: 38,
    var250M: 45,
    ...overrides,
  };
}

function makePoint(pointIndex: number, overrides: Partial<PortfolioMetricsSnapshot>, status = 'Optimal'): FrontierExplorerPoint {
  return {
    pointIndex,
    targetTvar250M: overrides.tvar250M ?? 50,
    status,
    solver: 'HiGHS LP',
    metrics: makeMetrics(overrides),
    layers: [],
    constraints: [],
    returnPeriodContributions: [],
  };
}

describe('frontierMetrics helpers', () => {
  it('computes fallback ratios when newer backend fields are absent', () => {
    const legacy = makeMetrics({
      expectedProfitM: 25,
      tvar100M: 50,
      tvar250M: 62.5,
      tvar1000M: 100,
      stdDevM: 10,
      returnToTvar100: undefined,
      returnToTvar250: undefined,
      returnToTvar1000: undefined,
      returnToStdDev: undefined,
      riskRewardRatio: 0.4,
    });
    expect(getRatioValue(legacy, 'returnToTvar100')).toBeCloseTo(0.5, 4);
    expect(getRatioValue(legacy, 'returnToTvar250')).toBeCloseTo(0.4, 4);
    expect(getRatioValue(legacy, 'returnToTvar1000')).toBeCloseTo(0.25, 4);
    expect(getRatioValue(legacy, 'returnToStdDev')).toBeCloseTo(2.5, 4);
    expect(getAxisPlotValue(legacy, 'returnToTvar250')).toBeCloseTo(40, 4);
    expect(getAxisPlotValue(legacy, 'returnToStdDev')).toBeCloseTo(2.5, 4);
  });

  it('resolves the active risk horizon from Y ratio or X risk axis', () => {
    expect(activeRiskHorizon('tvar250M', 'returnToTvar100').ratioKey).toBe('returnToTvar100');
    expect(activeRiskHorizon('tvar100M', 'expectedProfitM').ratioKey).toBe('returnToTvar100');
    expect(activeRiskHorizon('tvar1000M', 'expectedProfitM').ratioKey).toBe('returnToTvar1000');
    expect(activeRiskHorizon('stdDevM', 'expectedProfitM').ratioKey).toBe('returnToStdDev');
    expect(activeRiskHorizon('deployedCollateralM', 'expectedProfitM').ratioKey).toBe('returnToTvar250');
    expect(canShowTangent('tvar250M', 'expectedProfitM')).toBe(true);
    expect(canShowTangent('deployedCollateralM', 'expectedProfitM')).toBe(false);
    expect(canShowTangent('tvar250M', 'returnToTvar250')).toBe(false);
  });

  it('finds the peak Return/Risk point and breaks near-ties in favor of the higher-return knee', () => {
    const frontier = [
      makePoint(0, { expectedProfitM: 12, tvar250M: 27.25, returnToTvar250: 0.4404 }),
      makePoint(1, { expectedProfitM: 23, tvar250M: 52.3, returnToTvar250: 0.4398 }), // knee within 0.001
      makePoint(2, { expectedProfitM: 28, tvar250M: 74.4, returnToTvar250: 0.3763 }),
      makePoint(3, { expectedProfitM: 40, tvar250M: 50, returnToTvar250: 0.8 }, 'Infeasible'),
    ];
    const peak = findPeakReturnRiskPoint(frontier, 'returnToTvar250');
    expect(peak?.pointIndex).toBe(1);
  });

  it('builds an origin iso-ratio tangent ray through the peak point', () => {
    const p0 = makePoint(0, { expectedProfitM: 15, tvar250M: 35, returnToTvar250: 0.4286 });
    const p1 = makePoint(1, { expectedProfitM: 23, tvar250M: 52, returnToTvar250: 0.4423 });
    const p2 = makePoint(2, { expectedProfitM: 28, tvar250M: 74, returnToTvar250: 0.3784 });
    const init = makeMetrics({ expectedProfitM: 25, tvar250M: 79, returnToTvar250: 0.3165 });
    const ray = computeTangentRay([p0, p1, p2], init, 'tvar250M', 'expectedProfitM', p1);
    expect(ray).not.toBeNull();
    expect(ray!.points[0]).toEqual([0, 0]);
    expect(ray!.points[1]).toEqual([52, 23]);
    expect(ray!.slope).toBeCloseTo(23 / 52, 4);
    expect(ray!.points[2][1]).toBeCloseTo(ray!.slope * ray!.points[2][0], 4);
    expect(ray!.label).toContain('44.2%');
    expect(computeTangentRay([p0, p1, p2], init, 'tvar250M', 'returnToTvar250', p1)).toBeNull();
  });

  it('formats ratios, axis ticks, and detects presets cleanly', () => {
    expect(formatRatioHeadline(0.4405)).toBe('44.1%');
    expect(formatRatioHeadline(2.85, true)).toBe('2.85×');
    expect(formatRatioDual(0.4405)).toBe('44.1% (0.441×)');
    expect(formatAxisTick('returnToTvar250', 44.1)).toBe('44.1%');
    expect(formatAxisTick('tvar250M', 52)).toBe('$52m');
    expect(formatAxisTick('expectedProfitM', -5)).toBe('-$5m');
    expect(formatAxisValue('returnToTvar250', 44.1)).toBe('44.1% (0.441×)');
    expect(detectActivePreset('tvar250M', 'returnToTvar250')).toBe('ratio250');
    expect(detectActivePreset('tvar250M', 'expectedProfitM')).toBe('returnVsTvar');
    expect(detectActivePreset('tvar100M', 'returnToTvar1000')).toBeNull();
  });
});
