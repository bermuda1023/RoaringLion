import { describe, expect, it } from 'vitest';
import type { OptimizerLayerCatalogItem } from '../types/contracts';
import {
  clampSharePct,
  computeAutoBandBound,
  computeLayerDeployedCapitalM,
  detectOptimizerConflicts,
  normalizeLayerBound,
} from './optimizerConstraints';

function makeLayer(over: Partial<OptimizerLayerCatalogItem> = {}): OptimizerLayerCatalogItem {
  return {
    layerKey: 'p1::l1',
    programmeId: 'p1',
    layerId: 'l1',
    cedentName: 'Alpha Re',
    programmeName: 'Alpha Cat XL',
    layerName: '1st Cat XL',
    direction: 'ASSUMED',
    status: 'BOUND',
    regionPerils: ['US-FL-WS'],
    currentShare: 0.1,
    limit1xM: 50,
    premium1xM: 10,
    capital1xM: 40,
    el1xM: 3,
    expectedProfit1xM: 7,
    rolPct: 20,
    defaultMinShare: 0,
    defaultMaxShare: 1.0,
    ...over,
  };
}

describe('optimizerConstraints — relative auto banding & 100% hard cap', () => {
  it('computes relative ±X% bounds for held/bound deals (10% ± 25% -> 7.5% to 12.5%)', () => {
    const l10 = makeLayer({ currentShare: 0.1 });
    expect(computeAutoBandBound(l10, 25)).toEqual({ minPct: 7.5, maxPct: 12.5, locked: false });

    const l50 = makeLayer({ currentShare: 0.5 });
    expect(computeAutoBandBound(l50, 25)).toEqual({ minPct: 37.5, maxPct: 62.5, locked: false });

    const l20 = makeLayer({ currentShare: 0.2 });
    expect(computeAutoBandBound(l20, 10)).toEqual({ minPct: 18, maxPct: 22, locked: false });
  });

  it('hard-caps max line at 100% even when currentShare * (1 + band) exceeds 100%', () => {
    const l90 = makeLayer({ currentShare: 0.9 });
    expect(computeAutoBandBound(l90, 25)).toEqual({ minPct: 67.5, maxPct: 100, locked: false });

    const l100 = makeLayer({ currentShare: 1.0 });
    expect(computeAutoBandBound(l100, 25)).toEqual({ minPct: 75, maxPct: 100, locked: false });
  });

  it('keeps 0%-line / non-bound deals optimizable at 0%–100% rather than locking to 0%–0%', () => {
    const unplaced = makeLayer({ status: 'DECLINED', currentShare: 0, defaultMaxShare: 1.0 });
    expect(computeAutoBandBound(unplaced, 25)).toEqual({ minPct: 0, maxPct: 100, locked: false });

    const hedge = makeLayer({
      direction: 'EXTERNAL_RETRO',
      status: 'PLANNED',
      currentShare: 0,
      defaultMaxShare: 2.0,
    });
    expect(computeAutoBandBound(hedge, 25)).toEqual({ minPct: 0, maxPct: 100, locked: false });
  });

  it('clamps manual bounds into [0, 100] and maintains min <= max', () => {
    expect(clampSharePct(150)).toBe(100);
    expect(clampSharePct(-10)).toBe(0);
    expect(normalizeLayerBound({ minPct: 30, maxPct: 150, locked: false }, 'max')).toEqual({
      minPct: 30,
      maxPct: 100,
      locked: false,
    });
    expect(normalizeLayerBound({ minPct: 70, maxPct: 50, locked: false }, 'min')).toEqual({
      minPct: 70,
      maxPct: 70,
      locked: false,
    });
    expect(normalizeLayerBound({ minPct: 70, maxPct: 50, locked: false }, 'max')).toEqual({
      minPct: 50,
      maxPct: 50,
      locked: false,
    });
  });
});

describe('optimizerConstraints — auto deployed capital formula', () => {
  it('computes max(0, (limit - premium) * share) for assumed layers and max(0, premium * share) for hedges', () => {
    const l = makeLayer({ limit1xM: 50, premium1xM: 11.5, capital1xM: undefined });
    expect(computeLayerDeployedCapitalM(l, 0.5)).toBeCloseTo(19.25, 6);

    const negCap = makeLayer({ limit1xM: 10, premium1xM: 12, capital1xM: undefined });
    expect(computeLayerDeployedCapitalM(negCap, 1.0)).toBe(0);

    // Buying a hedge costs its upfront purchase premium, reducing deployable capital
    const hedge = makeLayer({ direction: 'EXTERNAL_RETRO', limit1xM: 25, premium1xM: 5, capital1xM: undefined });
    expect(computeLayerDeployedCapitalM(hedge, 1.0)).toBeCloseTo(5.0, 6);
    expect(computeLayerDeployedCapitalM(hedge, 0.5)).toBeCloseTo(2.5, 6);
  });
});

describe('optimizerConstraints — Single Cedent Max & Fund Capital conflict detection', () => {
  it('flags infeasible single-cedent error and highlights offending multi-layer rows', () => {
    const l1 = makeLayer({ layerKey: 'f::1', cedentName: 'Farmers Group', layerName: 'Occ XL', limit1xM: 50, premium1xM: 10, capital1xM: 40 });
    const l2 = makeLayer({ layerKey: 'f::2', cedentName: 'Farmers Group', layerName: 'Agg XL', limit1xM: 35, premium1xM: 5, capital1xM: 30 });
    const l3 = makeLayer({ layerKey: 'p::1', cedentName: 'Pacific Re', layerName: 'EQ XL', limit1xM: 45, premium1xM: 8, capital1xM: 37 });

    const res = detectOptimizerConflicts({
      layers: [l1, l2, l3],
      bounds: {
        'f::1': { minPct: 60, maxPct: 80, locked: false }, // min $30m
        'f::2': { minPct: 60, maxPct: 80, locked: false }, // min $21m -> sum $51m > $40m cap
      },
      maxSingleCedentLimitM: 40,
      fundCapitalM: 450,
      hasAllUniverseLayers: true,
    });

    expect(res.hasBlockingError).toBe(true);
    const err = res.conflicts.find((c) => c.kind === 'SINGLE_CEDENT_MIN_EXCEEDS_CAP');
    expect(err).toBeTruthy();
    expect(err?.cedentName).toBe('Farmers Group');
    expect(err?.requiredMinLimitM).toBe(51);
    expect(err?.excessM).toBe(11);
    expect(res.rowConflictsByLayerKey['f::1']?.severity).toBe('ERROR');
    expect(res.rowConflictsByLayerKey['f::2']?.severity).toBe('ERROR');
    // Pacific Re is not infeasible, though its default 100% max ($45m) is clipped by $40m cap
    expect(res.rowConflictsByLayerKey['p::1']?.severity).toBe('WARNING');
  });

  it('does not raise a false-positive non-binding warning for a binding multi-layer Single Cedent cap', () => {
    const l1 = makeLayer({ layerKey: 'f::1', cedentName: 'Farmers Group', layerName: 'Occ XL', limit1xM: 50, premium1xM: 10, capital1xM: 40 });
    const l2 = makeLayer({ layerKey: 'f::2', cedentName: 'Farmers Group', layerName: 'Agg XL', limit1xM: 35, premium1xM: 5, capital1xM: 30 });
    const res = detectOptimizerConflicts({
      layers: [l1, l2],
      bounds: {},
      maxSingleCedentLimitM: 60, // > single layer 50m, <= combined cedent max 85m -> valid binding cap
      fundCapitalM: 450,
      hasAllUniverseLayers: true,
    });
    expect(res.conflicts).toEqual([]);
    expect(res.rowConflictsByLayerKey).toEqual({});
  });

  it('flags warning and highlights largest layer when Single Cedent Max exceeds largest cedent/layer max', () => {
    const l1 = makeLayer({ layerKey: 'a::1', cedentName: 'Alpha Re', limit1xM: 50, premium1xM: 10, capital1xM: 40 });
    const l2 = makeLayer({ layerKey: 'b::1', cedentName: 'Beta Re', limit1xM: 30, premium1xM: 6, capital1xM: 24 });

    const res = detectOptimizerConflicts({
      layers: [l1, l2],
      bounds: {},
      maxSingleCedentLimitM: 85,
      fundCapitalM: 450,
      hasAllUniverseLayers: true,
    });

    expect(res.hasBlockingError).toBe(false);
    const warn = res.conflicts.find((c) => c.kind === 'SINGLE_CEDENT_CAP_EXCEEDS_LARGEST_MAX');
    expect(warn).toBeTruthy();
    expect(warn?.message).toContain('Alpha Re');
    expect(warn?.message).toContain('$50.0m');
    expect(res.rowConflictsByLayerKey['a::1']?.severity).toBe('WARNING');
    expect(res.rowConflictsByLayerKey['b::1']).toBeUndefined();
  });

  it('flags Fund Capital conflict when locked/min layer deployed capital (including hedge purchase cost) exceeds Fund Capital', () => {
    const l1 = makeLayer({ layerKey: 'a::1', currentShare: 1.0, limit1xM: 100, premium1xM: 20, capital1xM: 80 });
    const hedge = makeLayer({
      layerKey: 'h::1',
      direction: 'EXTERNAL_RETRO',
      cedentName: 'ILW Market Hedge',
      currentShare: 1.0,
      limit1xM: 25,
      premium1xM: 5,
      capital1xM: 5,
    });
    const res = detectOptimizerConflicts({
      layers: [l1, hedge],
      bounds: {
        'a::1': { minPct: 100, maxPct: 100, locked: true },
        'h::1': { minPct: 100, maxPct: 100, locked: true },
      },
      maxSingleCedentLimitM: null,
      fundCapitalM: 82, // 80m assumed + 5m hedge cost = 85m > 82m!
      hasAllUniverseLayers: true,
    });

    expect(res.hasBlockingError).toBe(true);
    const err = res.conflicts.find((c) => c.kind === 'FUND_CAPITAL_MIN_EXCEEDS_CAP');
    expect(err).toBeTruthy();
    expect(err?.requiredMinLimitM).toBe(85);
    expect(err?.excessM).toBe(3);
    expect(res.rowConflictsByLayerKey['a::1']?.severity).toBe('ERROR');
    expect(res.rowConflictsByLayerKey['h::1']?.severity).toBe('ERROR');
  });
});
