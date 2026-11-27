import type {
  ConflictLayerDetail,
  ConstraintConflict,
  OptimizerLayerCatalogItem,
} from '../types/contracts';
import type { LayerBoundState } from '../components/OptimizerCandidatesTable';

const EPS = 1e-6;

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

export function isHedgeDirection(direction: string | undefined): boolean {
  return direction === 'EXTERNAL_RETRO' || direction === 'INTERNAL_CEDED';
}

/** Clamp a participation percentage strictly into [0, 100]. */
export function clampSharePct(pct: number): number {
  if (!Number.isFinite(pct)) return 0;
  return round2(Math.min(100, Math.max(0, pct)));
}

/**
 * Normalize a layer's Min/Max participation bounds so 0 <= minPct <= maxPct <= 100 always holds.
 * When `changedField` is 'min' and minPct rises above maxPct, maxPct rises with it (up to 100).
 * When `changedField` is 'max' and maxPct drops below minPct, minPct drops with it (down to 0).
 */
export function normalizeLayerBound(
  bound: LayerBoundState,
  changedField?: 'min' | 'max',
): LayerBoundState {
  let minPct = clampSharePct(bound.minPct);
  let maxPct = clampSharePct(bound.maxPct);
  if (changedField === 'min' && minPct > maxPct) {
    maxPct = minPct;
  } else if (changedField === 'max' && maxPct < minPct) {
    minPct = maxPct;
  } else if (minPct > maxPct) {
    maxPct = minPct;
  }
  return { minPct, maxPct, locked: bound.locked };
}

/**
 * Compute auto-banded Min/Max participation bounds for a layer with ±`bandPct`% relative banding.
 * - Held / bound layers (`currentShare > 0`): relative band `cur * (1 - band)` to `min(100%, cur * (1 + band))`.
 *   Example: 10% current line with ±25% banding -> 7.5% to 12.5%; 90% ±25% -> 67.5% to 100%.
 * - Unplaced / 0%-line deals (`currentShare <= 0`): open 0% to 100% (never locked to 0%–0%, never > 100%).
 */
export function computeAutoBandBound(
  layer: Pick<OptimizerLayerCatalogItem, 'currentShare' | 'defaultMaxShare'>,
  bandPct = 25,
): LayerBoundState {
  const curPct = clampSharePct(layer.currentShare * 100);
  const ratio = Math.min(100, Math.max(0, Number.isFinite(bandPct) ? bandPct : 25)) / 100;
  if (curPct > EPS) {
    return {
      minPct: round2(Math.max(0, curPct * (1 - ratio))),
      maxPct: round2(Math.min(100, curPct * (1 + ratio))),
      locked: false,
    };
  }
  const openMax = clampSharePct(Math.min(1, layer.defaultMaxShare > 0 ? layer.defaultMaxShare : 1) * 100);
  return {
    minPct: 0,
    maxPct: openMax > 0 ? openMax : 100,
    locked: false,
  };
}

/** 100%-share collateralized capital needed for one layer: max(0, limit - deposit_premium) for assumed, 0 for hedges. */
export function computeLayerCapital1xM(
  layer: Pick<OptimizerLayerCatalogItem, 'limit1xM' | 'premium1xM' | 'capital1xM' | 'direction'>,
): number {
  if (isHedgeDirection(layer.direction)) return 0;
  if (typeof layer.capital1xM === 'number' && Number.isFinite(layer.capital1xM)) {
    return Math.max(0, layer.capital1xM);
  }
  return Math.max(0, layer.limit1xM - layer.premium1xM);
}

/** Deployed capital needed for a layer at a given line share (0..1): max(0, (limit - premium) * share). */
export function computeLayerDeployedCapitalM(
  layer: Pick<OptimizerLayerCatalogItem, 'limit1xM' | 'premium1xM' | 'capital1xM' | 'direction'>,
  share: number,
): number {
  if (isHedgeDirection(layer.direction)) return 0;
  const s = Math.max(0, Number.isFinite(share) ? share : 0);
  return computeLayerCapital1xM(layer) * s;
}

export interface RowConflictHighlight {
  severity: 'ERROR' | 'WARNING';
  badge: string;
  reasons: string[];
}

export interface ConflictDetectionResult {
  conflicts: ConstraintConflict[];
  rowConflictsByLayerKey: Record<string, RowConflictHighlight>;
  hasBlockingError: boolean;
  minRequiredCapitalM: number;
}

export interface DetectConflictsInput {
  layers: OptimizerLayerCatalogItem[];
  bounds: Record<string, LayerBoundState>;
  maxSingleCedentLimitM: number | null;
  fundCapitalM: number;
  minLineSizeShare?: number;
  serverConflicts?: ConstraintConflict[];
  universeMaxLayerLimit1xM?: number;
  universeMaxLayerLabel?: string;
  universeMaxCedentLimit1xM?: number;
  universeMaxCedentName?: string;
  hasAllUniverseLayers?: boolean;
}

function effectiveBoundsForLayer(
  layer: OptimizerLayerCatalogItem,
  bounds: Record<string, LayerBoundState>,
): { minShare: number; maxShare: number; locked: boolean } {
  const b = bounds[layer.layerKey];
  if (b) {
    if (b.locked) {
      const s = clampSharePct(layer.currentShare * 100) / 100;
      return { minShare: s, maxShare: s, locked: true };
    }
    const norm = normalizeLayerBound(b);
    return {
      minShare: norm.minPct / 100,
      maxShare: norm.maxPct / 100,
      locked: Math.abs(norm.maxPct - norm.minPct) < 1e-6,
    };
  }
  const minShare = clampSharePct(layer.defaultMinShare * 100) / 100;
  const maxShare = Math.max(minShare, clampSharePct(Math.min(1, layer.defaultMaxShare) * 100) / 100);
  return { minShare, maxShare, locked: false };
}

function addRowHighlight(
  map: Record<string, RowConflictHighlight>,
  layerKey: string,
  severity: 'ERROR' | 'WARNING',
  badge: string,
  reason: string,
) {
  const existing = map[layerKey];
  if (!existing) {
    map[layerKey] = { severity, badge, reasons: [reason] };
    return;
  }
  if (!existing.reasons.includes(reason)) {
    existing.reasons.push(reason);
  }
  if (severity === 'ERROR' && existing.severity !== 'ERROR') {
    existing.severity = 'ERROR';
    existing.badge = badge;
  }
}

/**
 * Real-time pre-solve conflict detector for Single Cedent Max and Fund Capital constraints.
 * Mirrors backend `detect_constraint_conflicts` and highlights exact offending rows in the layer list.
 */
export function detectOptimizerConflicts({
  layers,
  bounds,
  maxSingleCedentLimitM,
  fundCapitalM,
  minLineSizeShare = 0,
  serverConflicts = [],
  universeMaxLayerLimit1xM,
  universeMaxLayerLabel,
  universeMaxCedentLimit1xM,
  universeMaxCedentName,
  hasAllUniverseLayers = false,
}: DetectConflictsInput): ConflictDetectionResult {
  const conflicts: ConstraintConflict[] = [];
  const rowMap: Record<string, RowConflictHighlight> = {};

  const assumed = layers
    .filter((l) => !isHedgeDirection(l.direction))
    .map((layer) => {
      const { minShare, maxShare, locked } = effectiveBoundsForLayer(layer, bounds);
      const cap1x = computeLayerCapital1xM(layer);
      const minLimitM = minShare * layer.limit1xM;
      const maxLimitM = maxShare * layer.limit1xM;
      const minCapitalM = minShare * cap1x;
      const detail: ConflictLayerDetail = {
        layerKey: layer.layerKey,
        programmeId: layer.programmeId,
        layerId: layer.layerId,
        cedentName: layer.cedentName,
        layerName: layer.layerName,
        minShare: Number(minShare.toFixed(4)),
        maxShare: Number(maxShare.toFixed(4)),
        locked,
        limit1xM: layer.limit1xM,
        minLimitM: round2(minLimitM),
        maxLimitM: round2(maxLimitM),
        capital1xM: round2(cap1x),
        minCapitalM: round2(minCapitalM),
      };
      return { layer, minShare, maxShare, locked, cap1x, minLimitM, maxLimitM, minCapitalM, detail };
    });

  const minRequiredCapitalM = round2(assumed.reduce((sum, r) => sum + r.minCapitalM, 0));
  const maxPossibleCapitalM = round2(assumed.reduce((sum, r) => sum + r.maxShare * r.cap1x, 0));

  // 1. Fund Capital vs minimum required Deployed Capital from layer min/locked bounds
  if (Number.isFinite(fundCapitalM) && fundCapitalM > 0 && minRequiredCapitalM > fundCapitalM + EPS) {
    const offending = assumed.filter((r) => r.minCapitalM > EPS);
    const excessM = round2(minRequiredCapitalM - fundCapitalM);
    const message = `Minimum required Deployed Capital from layer min/locked bounds ($${minRequiredCapitalM.toFixed(1)}m) exceeds Fund Capital ($${fundCapitalM.toFixed(1)}m) by $${excessM.toFixed(1)}m. Raise Fund Capital or lower highlighted layer Min % bounds.`;
    conflicts.push({
      kind: 'FUND_CAPITAL_MIN_EXCEEDS_CAP',
      severity: 'ERROR',
      cedentName: null,
      capLimitM: round2(fundCapitalM),
      requiredMinLimitM: minRequiredCapitalM,
      maxPossibleLimitM: maxPossibleCapitalM,
      excessM,
      message,
      offendingLayers: offending.map((r) => r.detail),
    });
    for (const r of offending) {
      addRowHighlight(
        rowMap,
        r.layer.layerKey,
        'ERROR',
        `Min cap $${r.minCapitalM.toFixed(1)}m (book > Fund Cap)`,
        message,
      );
    }
  }

  // 2. Single Cedent Max checks
  if (maxSingleCedentLimitM !== null && Number.isFinite(maxSingleCedentLimitM) && maxSingleCedentLimitM > 0 && assumed.length > 0) {
    const cap = maxSingleCedentLimitM;
    const byCedent = new Map<string, typeof assumed>();
    for (const row of assumed) {
      const list = byCedent.get(row.layer.cedentName) ?? [];
      list.push(row);
      byCedent.set(row.layer.cedentName, list);
    }

    const clipped: typeof assumed = [];
    const milpBlocked: typeof assumed = [];
    let largestLayer = assumed[0];
    let largestCedent: { cedentName: string; maxM: number; rows: typeof assumed } | null = null;

    for (const [cedentName, rows] of byCedent.entries()) {
      const cedMinM = rows.reduce((s, r) => s + r.minLimitM, 0);
      const cedMaxM = rows.reduce((s, r) => s + r.maxLimitM, 0);
      if (!largestCedent || cedMaxM > largestCedent.maxM) {
        largestCedent = { cedentName, maxM: cedMaxM, rows };
      }
      for (const r of rows) {
        if (r.maxLimitM > largestLayer.maxLimitM) {
          largestLayer = r;
        }
      }

      if (cedMinM > cap + EPS) {
        const offending = rows.filter((r) => r.minLimitM > EPS);
        const excessM = round2(cedMinM - cap);
        const message = `Single Cedent Max ($${cap.toFixed(1)}m) conflicts with ${cedentName}: minimum required limit from layer bounds is $${cedMinM.toFixed(1)}m (+$${excessM.toFixed(1)}m over cap). Reduce Min % / unlock the highlighted layer(s) or raise Single Cedent Max to at least $${cedMinM.toFixed(1)}m.`;
        conflicts.push({
          kind: 'SINGLE_CEDENT_MIN_EXCEEDS_CAP',
          severity: 'ERROR',
          cedentName,
          capLimitM: round2(cap),
          requiredMinLimitM: round2(cedMinM),
          maxPossibleLimitM: round2(cedMaxM),
          excessM,
          message,
          offendingLayers: offending.map((r) => r.detail),
        });
        for (const r of offending) {
          const badge =
            offending.length === 1
              ? `Min $${r.minLimitM.toFixed(1)}m > Cedent Max $${cap.toFixed(1)}m`
              : `Cedent min $${cedMinM.toFixed(1)}m > Max $${cap.toFixed(1)}m`;
          addRowHighlight(rowMap, r.layer.layerKey, 'ERROR', badge, message);
        }
      } else {
        for (const r of rows) {
          if (
            minLineSizeShare > EPS &&
            r.minShare <= 1e-9 &&
            r.maxShare >= minLineSizeShare &&
            minLineSizeShare * r.layer.limit1xM > cap + EPS
          ) {
            milpBlocked.push(r);
          }
          if (r.maxLimitM > cap + EPS) {
            clipped.push(r);
          }
        }
      }
    }

    if (milpBlocked.length > 0) {
      const worstMin = Math.max(...milpBlocked.map((r) => minLineSizeShare * r.layer.limit1xM));
      const names = milpBlocked
        .slice(0, 3)
        .map((r) => `${r.layer.cedentName} · ${r.layer.layerName}`)
        .join(', ');
      const extra = milpBlocked.length > 3 ? ` (+${milpBlocked.length - 3} more)` : '';
      const message = `MILP Min Line Share (${(minLineSizeShare * 100).toFixed(1)}%) exceeds Single Cedent Max ($${cap.toFixed(1)}m) on ${milpBlocked.length} layer(s): ${names}${extra}, forcing them to 0%.`;
      conflicts.push({
        kind: 'SINGLE_CEDENT_MILP_BLOCKS_LAYER',
        severity: 'WARNING',
        cedentName: milpBlocked.length === 1 ? milpBlocked[0].layer.cedentName : null,
        capLimitM: round2(cap),
        requiredMinLimitM: round2(worstMin),
        maxPossibleLimitM: round2(Math.max(...milpBlocked.map((r) => r.maxLimitM))),
        excessM: round2(worstMin - cap),
        message,
        offendingLayers: milpBlocked.map((r) => r.detail),
      });
      for (const r of milpBlocked) {
        addRowHighlight(
          rowMap,
          r.layer.layerKey,
          'WARNING',
          `MILP min $${(minLineSizeShare * r.layer.limit1xM).toFixed(1)}m > Cap $${cap.toFixed(1)}m`,
          message,
        );
      }
    }

    if (clipped.length > 0) {
      const sortedClipped = [...clipped].sort((a, b) => b.maxLimitM - a.maxLimitM);
      const worst = sortedClipped[0];
      const names = sortedClipped
        .slice(0, 3)
        .map((r) => `${r.layer.cedentName} · ${r.layer.layerName} (max $${r.maxLimitM.toFixed(1)}m)`)
        .join(', ');
      const extra = sortedClipped.length > 3 ? ` (+${sortedClipped.length - 3} more)` : '';
      const message = `Single Cedent Max ($${cap.toFixed(1)}m) is tighter than configured Max % on ${clipped.length} layer(s): ${names}${extra}. Those layers will be capped below their Max % bound.`;
      conflicts.push({
        kind: 'SINGLE_CEDENT_CLIPS_LAYER_MAX',
        severity: 'WARNING',
        cedentName: clipped.length === 1 ? worst.layer.cedentName : null,
        capLimitM: round2(cap),
        requiredMinLimitM: round2(clipped.reduce((s, r) => s + r.minLimitM, 0)),
        maxPossibleLimitM: round2(worst.maxLimitM),
        excessM: round2(worst.maxLimitM - cap),
        message,
        offendingLayers: sortedClipped.map((r) => r.detail),
      });
      for (const r of sortedClipped) {
        const effMaxPct = r.layer.limit1xM > EPS ? Math.min(100, (cap / r.layer.limit1xM) * 100) : 0;
        addRowHighlight(
          rowMap,
          r.layer.layerKey,
          'WARNING',
          `Max $${r.maxLimitM.toFixed(1)}m capped at ${effMaxPct.toFixed(1)}% ($${cap.toFixed(1)}m)`,
          message,
        );
      }
    }

    if (largestLayer && largestCedent) {
      const cedFromUniverse =
        !hasAllUniverseLayers &&
        typeof universeMaxCedentLimit1xM === 'number' &&
        universeMaxCedentLimit1xM > largestCedent.maxM;
      const layerFromUniverse =
        !hasAllUniverseLayers &&
        typeof universeMaxLayerLimit1xM === 'number' &&
        universeMaxLayerLimit1xM > largestLayer.maxLimitM;

      const effectiveLargestCedentMax = cedFromUniverse ? universeMaxCedentLimit1xM! : largestCedent.maxM;
      const effectiveLargestCedentName =
        cedFromUniverse && universeMaxCedentName ? universeMaxCedentName : largestCedent.cedentName;
      const effectiveLargestLayerMax = layerFromUniverse ? universeMaxLayerLimit1xM! : largestLayer.maxLimitM;
      const effectiveLargestLayerLabel =
        layerFromUniverse && universeMaxLayerLabel
          ? universeMaxLayerLabel
          : `${largestLayer.layer.cedentName} · ${largestLayer.layer.layerName}`;

      if (cap > effectiveLargestCedentMax + EPS) {
        // Only highlight rows on-page that actually belong to the named largest cedent or largest layer
        const offendingRows: typeof assumed = [];
        if (!cedFromUniverse) {
          offendingRows.push(...largestCedent.rows);
        }
        if (
          !layerFromUniverse &&
          !offendingRows.some((r) => r.layer.layerKey === largestLayer.layer.layerKey)
        ) {
          offendingRows.push(largestLayer);
        }
        const message = `Single Cedent Max ($${cap.toFixed(1)}m) is larger than the largest cedent's max exposure (${effectiveLargestCedentName}: $${effectiveLargestCedentMax.toFixed(1)}m) and largest layer's max (${effectiveLargestLayerLabel}: $${effectiveLargestLayerMax.toFixed(1)}m), so this cap will not bind.`;
        conflicts.push({
          kind: 'SINGLE_CEDENT_CAP_EXCEEDS_LARGEST_MAX',
          severity: 'WARNING',
          cedentName: effectiveLargestCedentName,
          capLimitM: round2(cap),
          requiredMinLimitM: 0,
          maxPossibleLimitM: round2(effectiveLargestCedentMax),
          excessM: round2(cap - effectiveLargestCedentMax),
          message,
          offendingLayers: offendingRows.map((r) => r.detail),
        });
        for (const r of offendingRows) {
          addRowHighlight(
            rowMap,
            r.layer.layerKey,
            'WARNING',
            `Max $${r.maxLimitM.toFixed(1)}m < Cap $${cap.toFixed(1)}m`,
            message,
          );
        }
      }
    }
  }

  // Merge any server-reported conflicts not already represented locally (e.g., cedents on another page)
  for (const sc of serverConflicts) {
    const alreadyPresent = conflicts.some(
      (c) => c.kind === sc.kind && (c.cedentName ?? '') === (sc.cedentName ?? ''),
    );
    if (!alreadyPresent) {
      conflicts.push(sc);
      for (const ol of sc.offendingLayers) {
        addRowHighlight(
          rowMap,
          ol.layerKey,
          sc.severity,
          sc.kind === 'SINGLE_CEDENT_MIN_EXCEEDS_CAP'
            ? `Min $${ol.minLimitM.toFixed(1)}m > Cap $${sc.capLimitM.toFixed(1)}m`
            : `Conflict ($${sc.capLimitM.toFixed(1)}m cap)`,
          sc.message,
        );
      }
    }
  }

  return {
    conflicts,
    rowConflictsByLayerKey: rowMap,
    hasBlockingError: conflicts.some((c) => c.severity === 'ERROR'),
    minRequiredCapitalM,
  };
}
