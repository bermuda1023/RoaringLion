import { describe, expect, it } from 'vitest';
import { computeLayerLossRatio, computeTotalLossRatio, computeWeightedRarc } from './layerRollup';

describe('computeWeightedRarc', () => {
  it('weights layer RARC by signed line size (limit * signedLine)', () => {
    const result = computeWeightedRarc([
      { limit: 20, signedLine: 0.3, writtenLine: 0.4, rarcPct: 10 },
      { limit: 40, signedLine: 0.1, writtenLine: 0.2, rarcPct: 4 },
    ]);
    // (6 * 10 + 4 * 4) / (6 + 4) = 7.6
    expect(result).toBe(7.6);
  });

  it('falls back to written line size when total signed line size is zero', () => {
    const result = computeWeightedRarc([
      { limit: 20, signedLine: 0, writtenLine: 0.5, rarcPct: 12 },
      { limit: 60, signedLine: 0, writtenLine: 0.5, rarcPct: 4 },
    ]);
    // (10 * 12 + 30 * 4) / 40 = 6.0
    expect(result).toBe(6);
  });

  it('falls back to 100% limit when both signed and written line sizes are zero', () => {
    const result = computeWeightedRarc([
      { limit: 20, signedLine: 0, writtenLine: 0, rarcPct: 12 },
      { limit: 60, signedLine: 0, writtenLine: 0, rarcPct: 0 },
    ]);
    expect(result).toBe(3);
  });

  it('returns null when no layers have RARC set and supports negative values', () => {
    expect(computeWeightedRarc([{ limit: 50, signedLine: 1, writtenLine: 1, rarcPct: null }])).toBeNull();
    expect(
      computeWeightedRarc([
        { limit: 50, signedLine: 0.5, writtenLine: 0.5, rarcPct: -6 },
        { limit: 50, signedLine: 0.5, writtenLine: 0.5, rarcPct: 2 },
      ]),
    ).toBe(-2);
  });
});

describe('computeLayerLossRatio and computeTotalLossRatio', () => {
  it('computes read-only loss ratio and rescales when deposit premium changes', () => {
    const saved = {
      savedExpectedLoss: 2.0,
      savedExpectedRip: 0.0,
      savedLossRatio: 0.2,
      savedDepositPremium: 10.0,
      savedSignedLine: 1.0,
      liveDepositPremium: 10.0,
      liveSignedLine: 1.0,
    };
    expect(computeLayerLossRatio(saved)).toBeCloseTo(0.2, 6);
    expect(computeLayerLossRatio({ ...saved, liveDepositPremium: 20.0 })).toBeCloseTo(0.1, 6);
    expect(computeLayerLossRatio({ ...saved, liveDepositPremium: 0 })).toBeNull();
  });

  it('computes programme total loss ratio across layers', () => {
    const total = computeTotalLossRatio([
      {
        savedExpectedLoss: 1.0,
        savedExpectedRip: 0.25,
        savedLossRatio: 1.0 / 5.25,
        savedDepositPremium: 10.0,
        savedSignedLine: 0.5,
        liveDepositPremium: 10.0,
        liveSignedLine: 0.5,
      },
      {
        savedExpectedLoss: 0.25,
        savedExpectedRip: 0.0,
        savedLossRatio: 0.1,
        savedDepositPremium: 5.0,
        savedSignedLine: 0.5,
        liveDepositPremium: 5.0,
        liveSignedLine: 0.5,
      },
    ]);
    expect(total).toBeCloseTo(1.25 / 7.75, 6);
  });
});
