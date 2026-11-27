/**
 * Shared institutional number, currency, and percentage formatters used across RoaringLion.
 * Guarantees thousands separators, proper negative sign placement (-$12.50m, never $-12.50m),
 * and consistent precision everywhere.
 */

export function fmtNum(value: number | null | undefined, decimals = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtInt(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return Math.round(value).toLocaleString('en-US');
}

export function fmtMoneyM(
  value: number | null | undefined,
  decimals = 2,
  opts?: { signed?: boolean },
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const neg = value < 0;
  const abs = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const sign = neg ? '-' : opts?.signed && value > 0 ? '+' : '';
  return `${sign}$${abs}m`;
}

export function fmtMoneyB(value: number | null | undefined, decimals = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const neg = value < 0;
  const abs = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return `${neg ? '-' : ''}$${abs}B`;
}

/** Format a value that is already in percentage points (e.g. 20.9 -> "20.90%"). */
export function fmtPct(
  value: number | null | undefined,
  decimals = 2,
  opts?: { signed?: boolean },
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const sign = opts?.signed && value > 0 ? '+' : '';
  return `${sign}${value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}%`;
}

/** Format a 0..1 ratio as a percentage (e.g. 0.2271 -> "22.71%"). */
export function fmtRatioPct(
  ratio: number | null | undefined,
  decimals = 1,
  opts?: { signed?: boolean },
): string {
  if (ratio == null || !Number.isFinite(ratio)) return '—';
  return fmtPct(ratio * 100, decimals, opts);
}

/** Format percentage-point delta (e.g. +1.25 -> "+1.25 pts"). */
export function fmtPts(value: number | null | undefined, decimals = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })} pts`;
}

/** Format ratio as cents per dollar (e.g. 0.367 -> "36.7¢"). */
export function fmtCents(ratio: number | null | undefined, decimals = 1): string {
  if (ratio == null || !Number.isFinite(ratio)) return '—';
  return `${(ratio * 100).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}¢`;
}

/** Metadata for DAG node parameters so the inspector displays human labels, units, and % conversion. */
export interface ParamFieldMeta {
  label: string;
  unit?: '$M' | '$B' | '%' | '×' | 'count' | 'day';
  isRatioPct?: boolean; // stored as 0..1 on backend, edited as 0..100% in UI
  placeholder?: string;
}

export const NODE_PARAM_META: Record<string, ParamFieldMeta> = {
  occLimit: { label: 'Occurrence Limit', unit: '$M', placeholder: 'e.g. 50' },
  occAttachment: { label: 'Occurrence Attachment (Xs)', unit: '$M', placeholder: 'e.g. 80' },
  occFranchise: { label: 'Occurrence Franchise', unit: '$M', placeholder: '0' },
  reinstatements: { label: 'Reinstatements', unit: 'count', placeholder: 'e.g. 1' },
  ripRate: { label: 'Reinstatement Premium (RIP) Rate', unit: '%', isRatioPct: true, placeholder: 'e.g. 100' },
  depositPremium: { label: 'Deposit Premium', unit: '$M', placeholder: 'e.g. 11.5' },
  placedShare: { label: 'Placed / Signed Share', unit: '%', isRatioPct: true, placeholder: '100' },
  writtenLine: { label: 'Written Line', unit: '%', isRatioPct: true, placeholder: '100' },
  brokeragePct: { label: 'Brokerage', unit: '%', isRatioPct: true, placeholder: 'e.g. 10' },
  aggLimit: { label: 'Aggregate Limit', unit: '$M', placeholder: 'e.g. 35' },
  aggAttachment: { label: 'Aggregate Attachment (Deductible)', unit: '$M', placeholder: 'e.g. 95' },
  perEventFranchise: { label: 'Per-Event Franchise', unit: '$M', placeholder: '0' },
  perEventCap: { label: 'Per-Event Cap (Optional)', unit: '$M', placeholder: 'No cap' },
  cessionShare: { label: 'Cession Share', unit: '%', isRatioPct: true, placeholder: 'e.g. 25' },
  eventCap: { label: 'Per-Event Cap (Optional)', unit: '$M', placeholder: 'No cap' },
  aggCap: { label: 'Aggregate Cap (Optional)', unit: '$M', placeholder: 'No cap' },
  retentionLine: { label: 'Retention Line', unit: '$M', placeholder: 'e.g. 25' },
  numLines: { label: 'Number of Lines', unit: 'count', placeholder: 'e.g. 4' },
  trigger: { label: 'Industry Loss Trigger', unit: '$B', placeholder: 'e.g. 40' },
  limit: { label: 'Limit', unit: '$M', placeholder: 'e.g. 25' },
  minOrder: { label: 'Min Event Order', unit: 'count', placeholder: '2' },
  maxOrder: { label: 'Max Event Order (Optional)', unit: 'count', placeholder: 'No max' },
  minLossThreshold: { label: 'Min Loss Threshold', unit: '$M', placeholder: '0' },
  startDay: { label: 'Start Day of Year', unit: 'day', placeholder: '1' },
  endDay: { label: 'End Day of Year', unit: 'day', placeholder: '365' },
  minLoss: { label: 'Min Loss', unit: '$M', placeholder: '0' },
  maxLoss: { label: 'Max Loss (Optional)', unit: '$M', placeholder: 'No max' },
  factor: { label: 'Uplift Factor', unit: '×', placeholder: '1.10' },
  additiveLoad: { label: 'Additive Load', unit: '$M', placeholder: '0' },
};
