import type { FrontierExplorerPoint, PortfolioMetricsSnapshot } from '../components/OptimizerCharts';
import { fmtMoneyM, fmtNum, fmtPct } from './format';

export type XAxisKey =
  | 'tvar250M'
  | 'tvar100M'
  | 'tvar500M'
  | 'tvar1000M'
  | 'stdDevM'
  | 'deployedCollateralM';

export type YAxisKey =
  | 'returnToTvar250'
  | 'returnToTvar100'
  | 'returnToTvar1000'
  | 'returnToStdDev'
  | 'expectedProfitM'
  | 'meanRocPct';

export type RatioMetricKey =
  | 'returnToTvar100'
  | 'returnToTvar250'
  | 'returnToTvar500'
  | 'returnToTvar1000'
  | 'returnToStdDev';

export type FrontierPresetId = 'ratio250' | 'returnVsTvar' | 'rocVsRisk' | 'ratioStdDev';

export interface FrontierPreset {
  id: FrontierPresetId;
  label: string;
  xKey: XAxisKey;
  yKey: YAxisKey;
  defaultTangent: boolean;
}

export const FRONTIER_PRESETS: readonly FrontierPreset[] = [
  { id: 'ratio250', label: 'Ret / TVaR', xKey: 'tvar250M', yKey: 'returnToTvar250', defaultTangent: false },
  { id: 'returnVsTvar', label: 'Return vs TVaR', xKey: 'tvar250M', yKey: 'expectedProfitM', defaultTangent: true },
  { id: 'rocVsRisk', label: 'ROC vs Risk', xKey: 'tvar250M', yKey: 'meanRocPct', defaultTangent: false },
  { id: 'ratioStdDev', label: 'Ret / Std Dev', xKey: 'stdDevM', yKey: 'returnToStdDev', defaultTangent: false },
] as const;

export const X_AXIS_OPTIONS: readonly { key: XAxisKey; label: string; shortLabel: string }[] = [
  { key: 'tvar250M', label: '1-in-250 TVaR ($M)', shortLabel: 'TVaR 1-in-250' },
  { key: 'tvar100M', label: '1-in-100 TVaR ($M)', shortLabel: 'TVaR 1-in-100' },
  { key: 'tvar500M', label: '1-in-500 TVaR ($M)', shortLabel: 'TVaR 1-in-500' },
  { key: 'tvar1000M', label: '1-in-1000 TVaR ($M)', shortLabel: 'TVaR 1-in-1000' },
  { key: 'stdDevM', label: 'Loss Std Dev ($M)', shortLabel: 'Loss Std Dev' },
  { key: 'deployedCollateralM', label: 'Deployed Capital ($M)', shortLabel: 'Deployed Cap' },
] as const;

export const Y_AXIS_OPTIONS: readonly { key: YAxisKey; label: string; shortLabel: string }[] = [
  { key: 'returnToTvar250', label: 'Return / TVaR 1-in-250 (%)', shortLabel: 'Ret / TVaR 250' },
  { key: 'returnToTvar100', label: 'Return / TVaR 1-in-100 (%)', shortLabel: 'Ret / TVaR 100' },
  { key: 'returnToTvar1000', label: 'Return / TVaR 1-in-1000 (%)', shortLabel: 'Ret / TVaR 1000' },
  { key: 'returnToStdDev', label: 'Return / Std Dev (×)', shortLabel: 'Ret / Std Dev' },
  { key: 'expectedProfitM', label: 'Expected Return ($M)', shortLabel: 'Expected Return ($M)' },
  { key: 'meanRocPct', label: 'Mean ROC (%)', shortLabel: 'Mean ROC (%)' },
] as const;

export const AXIS_LABELS: Record<XAxisKey | YAxisKey, string> = Object.fromEntries([
  ...X_AXIS_OPTIONS.map((o) => [o.key, o.label]),
  ...Y_AXIS_OPTIONS.map((o) => [o.key, o.label]),
]) as Record<XAxisKey | YAxisKey, string>;

export interface ActiveRiskHorizon {
  ratioKey: RatioMetricKey;
  riskKey: Exclude<XAxisKey, 'deployedCollateralM'>;
  shortLabel: string;
  label: string;
  isMultiple: boolean;
}

const RISK_HORIZONS: Record<RatioMetricKey, ActiveRiskHorizon> = {
  returnToTvar100: {
    ratioKey: 'returnToTvar100',
    riskKey: 'tvar100M',
    shortLabel: 'Ret/TVaR 100',
    label: 'Return / TVaR 1-in-100',
    isMultiple: false,
  },
  returnToTvar250: {
    ratioKey: 'returnToTvar250',
    riskKey: 'tvar250M',
    shortLabel: 'Ret/TVaR 250',
    label: 'Return / TVaR 1-in-250',
    isMultiple: false,
  },
  returnToTvar500: {
    ratioKey: 'returnToTvar500',
    riskKey: 'tvar500M',
    shortLabel: 'Ret/TVaR 500',
    label: 'Return / TVaR 1-in-500',
    isMultiple: false,
  },
  returnToTvar1000: {
    ratioKey: 'returnToTvar1000',
    riskKey: 'tvar1000M',
    shortLabel: 'Ret/TVaR 1000',
    label: 'Return / TVaR 1-in-1000',
    isMultiple: false,
  },
  returnToStdDev: {
    ratioKey: 'returnToStdDev',
    riskKey: 'stdDevM',
    shortLabel: 'Ret/Std Dev',
    label: 'Return / Std Dev',
    isMultiple: true,
  },
};

export function safeRatio(numerator: number, denominator: number): number {
  return denominator > 1e-6 && Number.isFinite(numerator) && Number.isFinite(denominator)
    ? numerator / denominator
    : 0;
}

/** Read a Return/Risk ratio from a snapshot, falling back cleanly if a response omits newer fields. */
export function getRatioValue(m: PortfolioMetricsSnapshot, key: RatioMetricKey): number {
  const direct = m[key];
  if (typeof direct === 'number' && Number.isFinite(direct)) return direct;
  switch (key) {
    case 'returnToTvar100':
      return safeRatio(m.expectedProfitM, m.tvar100M);
    case 'returnToTvar250':
      return typeof m.riskRewardRatio === 'number' && Number.isFinite(m.riskRewardRatio)
        ? m.riskRewardRatio
        : safeRatio(m.expectedProfitM, m.tvar250M);
    case 'returnToTvar500':
      return safeRatio(m.expectedProfitM, m.tvar500M);
    case 'returnToTvar1000':
      return safeRatio(m.expectedProfitM, m.tvar1000M);
    case 'returnToStdDev':
      return safeRatio(m.expectedProfitM, m.stdDevM ?? 0);
  }
}

/** Read any chart X/Y value in plot units (TVaR ratios plotted in percentage points so axes read 44.0%). */
export function getAxisPlotValue(m: PortfolioMetricsSnapshot, key: XAxisKey | YAxisKey): number {
  switch (key) {
    case 'returnToTvar100':
      return getRatioValue(m, 'returnToTvar100') * 100;
    case 'returnToTvar250':
      return getRatioValue(m, 'returnToTvar250') * 100;
    case 'returnToTvar1000':
      return getRatioValue(m, 'returnToTvar1000') * 100;
    case 'returnToStdDev':
      return getRatioValue(m, 'returnToStdDev');
    case 'stdDevM':
      return typeof m.stdDevM === 'number' && Number.isFinite(m.stdDevM) ? m.stdDevM : 0;
    default: {
      const v = m[key];
      return typeof v === 'number' && Number.isFinite(v) ? v : 0;
    }
  }
}

/** Resolve which Return/Risk ratio horizon is active for peak highlighting and tangent rays. */
export function activeRiskHorizon(xKey: XAxisKey, yKey: YAxisKey): ActiveRiskHorizon {
  if (yKey === 'returnToTvar100') return RISK_HORIZONS.returnToTvar100;
  if (yKey === 'returnToTvar250') return RISK_HORIZONS.returnToTvar250;
  if (yKey === 'returnToTvar1000') return RISK_HORIZONS.returnToTvar1000;
  if (yKey === 'returnToStdDev') return RISK_HORIZONS.returnToStdDev;
  if (xKey === 'tvar100M') return RISK_HORIZONS.returnToTvar100;
  if (xKey === 'tvar500M') return RISK_HORIZONS.returnToTvar500;
  if (xKey === 'tvar1000M') return RISK_HORIZONS.returnToTvar1000;
  if (xKey === 'stdDevM') return RISK_HORIZONS.returnToStdDev;
  return RISK_HORIZONS.returnToTvar250;
}

export function isRiskAxis(xKey: XAxisKey): xKey is Exclude<XAxisKey, 'deployedCollateralM'> {
  return xKey !== 'deployedCollateralM';
}

export function canShowTangent(xKey: XAxisKey, yKey: YAxisKey): boolean {
  return yKey === 'expectedProfitM' && isRiskAxis(xKey);
}

/**
 * Find the feasible frontier point maximizing Return/Risk for `ratioKey`.
 * Breaks near-ties (within `tieTol`, e.g. points on an initial linear ray) toward higher expected return.
 */
export function findPeakReturnRiskPoint(
  frontier: FrontierExplorerPoint[],
  ratioKey: RatioMetricKey,
  tieTol = 1e-3,
): FrontierExplorerPoint | null {
  const feasible = frontier.filter((p) => p.status === 'Optimal' || p.status === 'Feasible');
  if (!feasible.length) return null;
  let maxRatio = -Infinity;
  for (const p of feasible) {
    const r = getRatioValue(p.metrics, ratioKey);
    if (r > maxRatio) maxRatio = r;
  }
  const nearPeak = feasible.filter((p) => getRatioValue(p.metrics, ratioKey) >= maxRatio - tieTol);
  nearPeak.sort((a, b) => {
    if (b.metrics.expectedProfitM !== a.metrics.expectedProfitM) {
      return b.metrics.expectedProfitM - a.metrics.expectedProfitM;
    }
    return getRatioValue(b.metrics, ratioKey) - getRatioValue(a.metrics, ratioKey);
  });
  return nearPeak[0] ?? null;
}

/** Format a raw ratio (e.g. 0.4405 -> "44.1%", or multiple 2.85 -> "2.85×"). */
export function formatRatioHeadline(ratio: number, isMultiple = false): string {
  if (!Number.isFinite(ratio)) return '—';
  return isMultiple ? `${fmtNum(ratio, 2)}×` : fmtPct(ratio * 100, 1);
}

/** Format a raw ratio with both percentage and multiple for TVaR ratios (e.g. "44.1% (0.441×)"). */
export function formatRatioDual(ratio: number, isMultiple = false): string {
  if (!Number.isFinite(ratio)) return '—';
  if (isMultiple) return `${fmtNum(ratio, 2)}×`;
  return `${fmtPct(ratio * 100, 1)} (${fmtNum(ratio, 3)}×)`;
}

/** Format a plotted axis value with appropriate units for tooltips. */
export function formatAxisValue(key: XAxisKey | YAxisKey, plotValue: number): string {
  if (!Number.isFinite(plotValue)) return '—';
  if (key === 'returnToTvar100' || key === 'returnToTvar250' || key === 'returnToTvar1000') {
    return `${fmtPct(plotValue, 1)} (${fmtNum(plotValue / 100, 3)}×)`;
  }
  if (key === 'returnToStdDev') return `${fmtNum(plotValue, 2)}×`;
  if (key === 'meanRocPct') return fmtPct(plotValue, 2);
  return fmtMoneyM(plotValue, 2);
}

/** Format compact tick labels on chart axes. */
export function formatAxisTick(key: XAxisKey | YAxisKey, plotValue: number): string {
  if (!Number.isFinite(plotValue)) return '';
  if (key === 'returnToTvar100' || key === 'returnToTvar250' || key === 'returnToTvar1000') {
    return `${plotValue.toFixed(1)}%`;
  }
  if (key === 'returnToStdDev') return `${plotValue.toFixed(2)}×`;
  if (key === 'meanRocPct') return `${plotValue.toFixed(1)}%`;
  return fmtMoneyM(plotValue, 0);
}

export interface TangentRaySpec {
  points: [number, number][];
  slope: number;
  label: string;
}

/** Compute an origin iso-ratio tangent ray through the peak Return/Risk point for Return-vs-Risk charts. */
export function computeTangentRay(
  frontier: FrontierExplorerPoint[],
  initial: PortfolioMetricsSnapshot,
  xKey: XAxisKey,
  yKey: YAxisKey,
  peak: FrontierExplorerPoint | null,
): TangentRaySpec | null {
  if (!canShowTangent(xKey, yKey) || !peak) return null;
  const peakX = getAxisPlotValue(peak.metrics, xKey);
  const peakY = getAxisPlotValue(peak.metrics, yKey);
  if (peakX <= 1e-6 || peakY <= 1e-6) return null;

  const slope = peakY / peakX;
  const horizon = activeRiskHorizon(xKey, yKey);
  const xs = [initial, ...frontier.map((p) => p.metrics)].map((m) => getAxisPlotValue(m, xKey));
  const ys = [initial, ...frontier.map((p) => p.metrics)].map((m) => getAxisPlotValue(m, yKey));
  const maxX = Math.max(peakX, ...xs);
  const maxY = Math.max(peakY, ...ys);
  // Extend past the peak without blowing up the Y scale above the frontier
  const endX = Math.max(peakX, Math.min(maxX * 1.04, (maxY * 1.12) / slope));
  const endY = slope * endX;

  return {
    points: [
      [0, 0],
      [peakX, peakY],
      [endX, endY],
    ],
    slope,
    label: `Peak ${formatRatioHeadline(slope, horizon.isMultiple)}`,
  };
}

export function detectActivePreset(xKey: XAxisKey, yKey: YAxisKey): FrontierPresetId | null {
  const match = FRONTIER_PRESETS.find((p) => p.xKey === xKey && p.yKey === yKey);
  return match ? match.id : null;
}
