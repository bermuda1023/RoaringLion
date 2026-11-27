/**
 * Layer-to-programme rollup calculations shared by CMS UI components.
 * Mirrors backend `compute_weighted_rarc` and `compute_programme_loss_ratio` in `backend/app/services/contracts.py`.
 */

export interface LayerRarcWeightInput {
  limit: number | null;
  signedLine: number | null;
  writtenLine: number | null;
  rarcPct: number | null;
}

/**
 * Line-size-weighted average RARC (in percentage points) across layers with RARC set.
 * Weighting hierarchy:
 * 1. Signed line size (`limit * signedLine`, i.e. our placed limit in $M).
 * 2. Written line size (`limit * writtenLine`) if total signed line size is 0.
 * 3. 100% layer limit (`limit`) if both signed and written line sizes are 0.
 */
export function computeWeightedRarc(layers: LayerRarcWeightInput[]): number | null {
  const withRarc = layers.filter((l) => l.rarcPct != null && l.limit != null && l.limit > 0);
  if (withRarc.length === 0) return null;

  const weightFns: Array<(l: LayerRarcWeightInput) => number> = [
    (l) => (l.limit ?? 0) * Math.max(l.signedLine ?? 0, 0),
    (l) => (l.limit ?? 0) * Math.max(l.writtenLine ?? 0, 0),
    (l) => l.limit ?? 0,
  ];

  for (const weightOf of weightFns) {
    let totalW = 0;
    let weightedSum = 0;
    for (const l of withRarc) {
      const w = weightOf(l);
      totalW += w;
      weightedSum += w * (l.rarcPct ?? 0);
    }
    if (totalW > 0) {
      return Number((weightedSum / totalW).toFixed(4));
    }
  }
  return null;
}

export interface LayerLossRatioInput {
  savedExpectedLoss?: number | null;
  savedExpectedRip?: number | null;
  savedLossRatio?: number | null;
  savedDepositPremium: number;
  savedSignedLine: number;
  liveDepositPremium: number;
  liveSignedLine: number | null;
}

/**
 * Read-only layer Loss Ratio = Adjusted Expected Loss / (Deposit Premium + Expected RIP).
 * Re-scales smoothly when deposit premium / ROL is edited in draft before saving.
 */
export function computeLayerLossRatio(input: LayerLossRatioInput): number | null {
  const {
    savedExpectedLoss,
    savedExpectedRip,
    savedLossRatio,
    savedDepositPremium,
    savedSignedLine,
    liveDepositPremium,
  } = input;
  if (liveDepositPremium <= 0) return null;
  if (savedExpectedLoss != null && savedSignedLine > 0) {
    const el100 = savedExpectedLoss / savedSignedLine;
    const rip100 = (savedExpectedRip ?? 0) / savedSignedLine;
    const liveRip100 = savedDepositPremium > 0 ? rip100 * (liveDepositPremium / savedDepositPremium) : rip100;
    const denom = liveDepositPremium + liveRip100;
    return denom > 0 ? el100 / denom : null;
  }
  if (savedLossRatio != null && Math.abs(liveDepositPremium - savedDepositPremium) < 1e-9) {
    return savedLossRatio;
  }
  return null;
}

/**
 * Programme-level Loss Ratio = sum(Our Adjusted EL) / sum(Our Deposit Premium + Our Expected RIP),
 * falling back to 100%-basis when total signed premium is 0.
 */
export function computeTotalLossRatio(layers: LayerLossRatioInput[]): number | null {
  let ourEl = 0;
  let ourIncome = 0;
  let el100Sum = 0;
  let income100Sum = 0;

  for (const l of layers) {
    if (l.savedExpectedLoss == null || l.savedSignedLine <= 0 || l.liveDepositPremium <= 0) continue;
    const el100 = l.savedExpectedLoss / l.savedSignedLine;
    const rip100 = (l.savedExpectedRip ?? 0) / l.savedSignedLine;
    const liveRip100 = l.savedDepositPremium > 0 ? rip100 * (l.liveDepositPremium / l.savedDepositPremium) : rip100;
    const income100 = l.liveDepositPremium + liveRip100;
    el100Sum += el100;
    income100Sum += income100;

    const signed = Math.max(l.liveSignedLine ?? 0, 0);
    if (signed > 0) {
      ourEl += el100 * signed;
      ourIncome += income100 * signed;
    }
  }

  if (ourIncome > 0) return ourEl / ourIncome;
  if (income100Sum > 0) return el100Sum / income100Sum;
  return null;
}
