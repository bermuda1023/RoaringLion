import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchJson, postJson } from '../api/client';
import { DealStatusBadge, LayerStatusBadge } from '../components/DealStatusBadge';
import {
  OptimizerCandidatesTable,
  listedLayers,
  type LayerBoundState,
} from '../components/OptimizerCandidatesTable';
import { OptimizerStatusFilter } from '../components/OptimizerStatusFilter';
import {
  PROGRAMME_STATUSES,
  type ConstraintConflict,
  type OptimizerCandidatesPage,
  type OptimizerLayerCatalogItem,
  type OptimizerMode,
  type OptimizerObjective,
  type ProgrammeStatus,
} from '../types/contracts';
import { fmtInt, fmtMoneyM, fmtNum, fmtPct, fmtRatioPct } from '../utils/format';
import {
  clampSharePct,
  computeAutoBandBound,
  computeLayerDeployedCapitalM,
  detectOptimizerConflicts,
  isHedgeDirection,
  normalizeLayerBound,
} from '../utils/optimizerConstraints';
import {
  candidatesUrl,
  countProgrammesByStatus,
  defaultStatusesForMode,
  isOffRiskStatus,
  sameStatuses,
  toggleStatus,
} from '../utils/optimizerStatuses';
import {
  LayerContributionBarChart,
  ParetoFrontierChart,
  PortfolioRadarChart,
  ReturnPeriodStackChart,
  type FrontierExplorerPoint,
  type OptimizedLayerRow,
  type PortfolioMetricsSnapshot,
  type XAxisKey,
  type YAxisKey,
} from '../components/OptimizerCharts';
import {
  FRONTIER_PRESETS,
  X_AXIS_OPTIONS,
  Y_AXIS_OPTIONS,
  activeRiskHorizon,
  canShowTangent,
  detectActivePreset,
  findPeakReturnRiskPoint,
  formatRatioDual,
  formatRatioHeadline,
  getRatioValue,
  type FrontierPreset,
} from '../utils/frontierMetrics';

interface FrontierExplorerResponse {
  mode: string;
  granularity: string;
  view: string;
  /** Deal statuses the portfolio was built from. */
  includedStatuses?: ProgrammeStatus[];
  fundCapitalM?: number;
  computeMs: number;
  initialPortfolio: PortfolioMetricsSnapshot;
  initialLayers: OptimizedLayerRow[];
  recommendedIndex: number;
  peakReturnToTvar100Index?: number;
  peakReturnToTvar250Index?: number;
  peakReturnToTvar1000Index?: number;
  peakReturnToStdDevIndex?: number;
  frontier: FrontierExplorerPoint[];
  conflicts?: ConstraintConflict[];
}

type DynamicConstraint =
  | { id: string; kind: 'TVAR'; returnPeriod: number; maxTvarM: number; regionPerilKey: string }
  | { id: string; kind: 'RP_CAP'; regionPerilKey: string; maxLimitM: number }
  | { id: string; kind: 'EL_CAP'; maxRatio: number }
  | { id: string; kind: 'CEDENT_CAP'; maxLimitM: number };

type SubTab = 'results' | 'config' | 'portfolio';

const REGION_PERILS = ['US-FL-WS', 'US-TX-WS', 'US-SE-WS', 'US-CA-EQ', 'JP-KANTO-EQ', 'EU-DE-WS'];

const MODE_LABELS: Record<OptimizerMode, string> = {
  FORWARD_PLANNING: 'Forward-Looking Planning',
  CURRENT: 'Current Portfolio Rebalance',
};

/** Programmes listed per page of the deal table. */
const CANDIDATE_PAGE_SIZE = 25;

/** How many deals of each status a frontier point holds, so declined deals are visible at a glance. */
function PortfolioStatusSummary({ layers, stale }: { layers: OptimizedLayerRow[]; stale: boolean }) {
  const counts = countProgrammesByStatus(layers.filter((l) => l.status));
  const offRisk = counts.filter((c) => isOffRiskStatus(c.status));
  if (counts.length === 0 && !stale) return null;
  return (
    <div className="status-summary">
      {counts.length > 0 && <span>Deals in this portfolio by status:</span>}
      {counts.map((c) => (
        <span key={c.status} className="deal-name">
          <DealStatusBadge status={c.status} /> {fmtInt(c.programmes)}
        </span>
      ))}
      {offRisk.length > 0 && (
        <div className="warning-banner" role="note">
          This portfolio includes deals that are not on risk ({offRisk.map((c) => `${fmtInt(c.programmes)} ${c.status}`).join(', ')}).
          Their rows are marked below.
        </div>
      )}
      {stale && (
        <div className="warning-banner" role="note">
          The status selection changed after this run. Run Frontier Explorer to apply it.
        </div>
      )}
    </div>
  );
}

function DeltaBar({ deltaShare, maxAbsDelta }: { deltaShare: number; maxAbsDelta: number }) {
  const pct = deltaShare * 100;
  const absPct = Math.abs(pct);
  const scale = Math.max(25, maxAbsDelta * 100);
  const widthPct = Math.min(100, (absPct / scale) * 100);

  if (absPct < 0.05) {
    return <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>No Change</span>;
  }

  const isPos = pct > 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 104 }}>
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', height: 14 }}>
        <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', paddingRight: 2 }}>
          {!isPos && (
            <div
              style={{
                width: `${widthPct}%`,
                height: 11,
                borderRadius: '3px 0 0 3px',
                background: 'linear-gradient(90deg, var(--negative), var(--negative))',
              }}
            />
          )}
        </div>
        <div style={{ width: 1, height: 16, background: 'var(--text-faint)' }} />
        <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-start', paddingLeft: 2 }}>
          {isPos && (
            <div
              style={{
                width: `${widthPct}%`,
                height: 11,
                borderRadius: '0 3px 3px 0',
                background: 'linear-gradient(90deg, var(--positive), var(--positive))',
              }}
            />
          )}
        </div>
      </div>
      <span
        style={{
          width: 46,
          textAlign: 'right',
          fontSize: 11,
          fontWeight: 600,
          color: isPos ? 'var(--positive)' : 'var(--negative)',
        }}
      >
        {isPos ? '+' : ''}
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}

export function PortfolioOptimizer({
  totalCapital: controlledCapital,
  onChangeCapital,
}: {
  totalCapital?: number;
  onChangeCapital?: (cap: number) => void;
} = {}) {
  const qc = useQueryClient();
  const [subTab, setSubTab] = useState<SubTab>('results');

  // Core Optimization Config state
  const [mode, setMode] = useState<OptimizerMode>('FORWARD_PLANNING');
  const [view, setView] = useState<'ADJUSTED' | 'UNADJUSTED'>('ADJUSTED');
  const [objective, setObjective] = useState<OptimizerObjective>('MAX_EXPECTED_PROFIT');
  const [riskLambda, setRiskLambda] = useState(0.15);
  const [localCapital, setLocalCapital] = useState(450);
  const totalCapital = controlledCapital ?? localCapital;
  const setTotalCapital = (next: number) => {
    const clean = Number.isFinite(next) && next > 0 ? next : 1;
    if (onChangeCapital) onChangeCapital(clean);
    else setLocalCapital(clean);
  };
  const [maxTvar100, setMaxTvar100] = useState(95);
  const [maxTvar250, setMaxTvar250] = useState(115);
  const [minLine, setMinLine] = useState(0.0);
  const [bandPct, setBandPct] = useState(25);
  const [includeRetro, setIncludeRetro] = useState(true);
  const [retroLimit, setRetroLimit] = useState(25);
  const [retroTrigger, setRetroTrigger] = useState(40);
  const [numPoints, setNumPoints] = useState(12);

  // Dynamic constraints builder (Analyze Re-style Add/Remove Constraints)
  const [constraints, setConstraints] = useState<DynamicConstraint[]>([
    { id: 'c-fl', kind: 'RP_CAP', regionPerilKey: 'US-FL-WS', maxLimitM: 75 },
    { id: 'c-el', kind: 'EL_CAP', maxRatio: 0.35 },
  ]);

  // Per-layer participation bounds the user has set; any other layer keeps the solver's defaults
  const [layerBounds, setLayerBounds] = useState<Record<string, LayerBoundState>>({});

  // Deal statuses in the portfolio. null follows the horizon mode's defaults.
  const [customStatuses, setCustomStatuses] = useState<ProgrammeStatus[] | null>(null);
  const [search, setSearch] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [offset, setOffset] = useState(0);

  // Frontier Explorer interactive selection & axis selectors
  const [selectedIdx, setSelectedIdx] = useState<number>(0);
  const [xKey, setXKey] = useState<XAxisKey>('tvar250M');
  const [yKey, setYKey] = useState<YAxisKey>('returnToTvar250');
  const [showTangent, setShowTangent] = useState<boolean>(true);
  const [forkedBranchMsg, setForkedBranchMsg] = useState<string | null>(null);
  const [lastSolvedConstraintSig, setLastSolvedConstraintSig] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearchTerm(search.trim());
      setOffset(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);

  // One page of the deals in the selected statuses; the server filters, searches and pages them.
  const candidatesQ = useQuery({
    queryKey: ['optimizerCandidates', view, mode, customStatuses, searchTerm, offset],
    queryFn: () =>
      fetchJson<OptimizerCandidatesPage>(
        candidatesUrl({
          view,
          mode,
          includedStatuses: customStatuses,
          search: searchTerm,
          limit: CANDIDATE_PAGE_SIZE,
          offset,
        }),
      ),
    placeholderData: keepPreviousData,
  });
  const candidates = candidatesQ.data;

  const defaultStatuses =
    candidates && candidates.mode === mode ? candidates.defaultStatuses : defaultStatusesForMode(mode);
  const selectedStatuses = customStatuses ?? defaultStatuses;
  const statusCounts = useMemo(
    () =>
      candidates
        ? (Object.fromEntries(candidates.statusCounts.map((c) => [c.status, c.programmes])) as Partial<
            Record<ProgrammeStatus, number>
          >)
        : undefined,
    [candidates],
  );
  const universeProgrammes = statusCounts
    ? selectedStatuses.reduce((n, s) => n + (statusCounts[s] ?? 0), 0)
    : undefined;

  const changeStatuses = (next: ProgrammeStatus[] | null) => {
    setCustomStatuses(next);
    setOffset(0);
  };

  const buildPayload = () => {
    const regionPerilCaps = constraints
      .filter((c): c is Extract<DynamicConstraint, { kind: 'RP_CAP' }> => c.kind === 'RP_CAP')
      .map((c) => ({ regionPerilKey: c.regionPerilKey, maxLimitM: c.maxLimitM }));

    const tvarCaps = constraints
      .filter((c): c is Extract<DynamicConstraint, { kind: 'TVAR' }> => c.kind === 'TVAR')
      .map((c) => ({
        returnPeriod: c.returnPeriod,
        maxTvarM: c.maxTvarM,
        regionPerilKey: c.regionPerilKey || null,
      }));

    const elCap = constraints.find((c): c is Extract<DynamicConstraint, { kind: 'EL_CAP' }> => c.kind === 'EL_CAP');
    const cedCaps = constraints.filter(
      (c): c is Extract<DynamicConstraint, { kind: 'CEDENT_CAP' }> => c.kind === 'CEDENT_CAP',
    );
    const activeCedCapM =
      cedCaps.length > 0 ? Math.min(...cedCaps.map((c) => c.maxLimitM).filter((v) => Number.isFinite(v) && v > 0)) : null;

    const boundsList = Object.entries(layerBounds).map(([layerKey, raw]) => {
      const b = raw.locked ? raw : normalizeLayerBound(raw);
      return {
        layerKey,
        minShare: Number((clampSharePct(b.minPct) / 100).toFixed(4)),
        maxShare: Number((clampSharePct(b.maxPct) / 100).toFixed(4)),
        locked: b.locked,
      };
    });

    return {
      mode,
      includedStatuses: customStatuses,
      granularity: 'LAYER',
      objective,
      riskAversionLambda: riskLambda,
      view,
      totalCapital,
      maxDeployedCollateral: totalCapital,
      maxTvar100M: maxTvar100,
      maxTvar250M: maxTvar250,
      maxElCapitalRatio: elCap ? elCap.maxRatio : null,
      maxSingleCedentLimitM: activeCedCapM && Number.isFinite(activeCedCapM) ? activeCedCapM : null,
      regionPerilCaps,
      tvarCaps,
      layerBounds: boundsList,
      minLineSizeShare: minLine,
      includeRetroIlwCandidate: includeRetro,
      retroIlwLimitM: retroLimit,
      retroIlwTriggerBn: retroTrigger,
    };
  };

  const activeSingleCedentCapM = useMemo(() => {
    const vals = constraints
      .filter((c): c is Extract<DynamicConstraint, { kind: 'CEDENT_CAP' }> => c.kind === 'CEDENT_CAP')
      .map((c) => c.maxLimitM)
      .filter((v) => Number.isFinite(v) && v > 0);
    return vals.length > 0 ? Math.min(...vals) : null;
  }, [constraints]);

  const currentConstraintSig = useMemo(
    () =>
      JSON.stringify({
        mode,
        statuses: selectedStatuses,
        totalCapital,
        activeSingleCedentCapM,
        minLine,
        layerBounds,
      }),
    [mode, selectedStatuses, totalCapital, activeSingleCedentCapM, minLine, layerBounds],
  );

  const exploreMut = useMutation({
    mutationFn: () => {
      const sig = currentConstraintSig;
      return postJson<FrontierExplorerResponse>(`/api/optimizer/explore?num_points=${numPoints}`, buildPayload()).then(
        (res) => ({ res, sig }),
      );
    },
    onSuccess: ({ res, sig }) => {
      setLastSolvedConstraintSig(sig);
      setSelectedIdx(res.recommendedIndex);
      setForkedBranchMsg(null);
      if (objective === 'MAX_RETURN_ON_TVAR_250') {
        setYKey('returnToTvar250');
        setXKey('tvar250M');
      } else if (objective === 'MAX_RETURN_ON_TVAR_100') {
        setYKey('returnToTvar100');
        setXKey('tvar100M');
      } else if (objective === 'MAX_RETURN_ON_TVAR_1000') {
        setYKey('returnToTvar1000');
        setXKey('tvar1000M');
      }
    },
  });

  const didAutoRunRef = useRef(false);
  useEffect(() => {
    if (didAutoRunRef.current) return;
    didAutoRunRef.current = true;
    exploreMut.mutate();
  }, []);

  const data = exploreMut.data?.res;
  const activePoint: FrontierExplorerPoint | null =
    data && data.frontier.length ? data.frontier[Math.min(selectedIdx, data.frontier.length - 1)] : null;

  const horizon = useMemo(() => activeRiskHorizon(xKey, yKey), [xKey, yKey]);
  const activePreset = useMemo(() => detectActivePreset(xKey, yKey), [xKey, yKey]);
  const peakPoint = useMemo(
    () => (data ? findPeakReturnRiskPoint(data.frontier, horizon.ratioKey) : null),
    [data, horizon.ratioKey],
  );
  const peakByRatio = useMemo(() => {
    if (!data) return null;
    return {
      returnToTvar100: findPeakReturnRiskPoint(data.frontier, 'returnToTvar100')?.pointIndex ?? -1,
      returnToTvar250: findPeakReturnRiskPoint(data.frontier, 'returnToTvar250')?.pointIndex ?? -1,
      returnToTvar1000: findPeakReturnRiskPoint(data.frontier, 'returnToTvar1000')?.pointIndex ?? -1,
      returnToStdDev: findPeakReturnRiskPoint(data.frontier, 'returnToStdDev')?.pointIndex ?? -1,
    };
  }, [data]);
  const isAtPeak = !!activePoint && !!peakPoint && activePoint.pointIndex === peakPoint.pointIndex;

  const applyPreset = (preset: FrontierPreset) => {
    setXKey(preset.xKey);
    setYKey(preset.yKey);
    setShowTangent(preset.defaultTangent);
  };

  // One-click promote selected frontier point into a versioned Portfolio Branch for historical backtesting!
  const forkBranchMut = useMutation({
    mutationFn: async () => {
      if (!activePoint) return null;
      // Aggregate layer shares back to programme signedLine overrides (weighted average by limit)
      const byProg: Record<string, { wLim: number; lim: number; shares: number[] }> = {};
      for (const l of activePoint.layers) {
        if (l.direction !== 'ASSUMED') continue;
        if (!byProg[l.programmeId]) byProg[l.programmeId] = { wLim: 0, lim: 0, shares: [] };
        const baseLim = l.optimalShare > 1e-6 ? l.optimalLimitM / l.optimalShare : 1.0;
        byProg[l.programmeId].wLim += l.optimalShare * baseLim;
        byProg[l.programmeId].lim += baseLim;
        byProg[l.programmeId].shares.push(l.optimalShare);
      }
      const overrides: Record<string, number> = {};
      for (const [pid, acc] of Object.entries(byProg)) {
        const avg = acc.lim > 0 ? acc.wLim / acc.lim : acc.shares[0] ?? 1.0;
        overrides[pid] = Math.min(1.0, Math.max(0.0, Number(avg.toFixed(4))));
      }
      return postJson<{ branchId: string; name: string }>('/api/branches', {
        name: `Frontier #${activePoint.pointIndex + 1} (TVaR $${activePoint.metrics.tvar250M.toFixed(0)}m / ROC ${activePoint.metrics.meanRocPct}%)`,
        description: `Auto-forked from HiGHS Frontier Explorer (${activePoint.solver})`,
        signedLineOverrides: overrides,
      });
    },
    onSuccess: (br) => {
      if (br) {
        qc.invalidateQueries({ queryKey: ['branches'] });
        setForkedBranchMsg(`Created branch "${br.name}" (${br.branchId}) — open Retro & Forge to backtest across 1990–2025.`);
      }
    },
  });

  // Sort layers by deltaShare ascending (biggest cuts on top, biggest upsizes at bottom — matching Analyze Re!)
  const sortedShareRows = useMemo(() => {
    if (!activePoint) return [];
    return [...activePoint.layers].sort((a, b) => a.deltaShare - b.deltaShare);
  }, [activePoint]);

  const maxAbsDelta = useMemo(() => {
    return sortedShareRows.reduce((m, r) => Math.max(m, Math.abs(r.deltaShare)), 0.1);
  }, [sortedShareRows]);

  const addConstraint = (kind: DynamicConstraint['kind']) => {
    const id = `c-${Date.now().toString(36)}`;
    if (kind === 'TVAR') {
      setConstraints((cs) => [...cs, { id, kind: 'TVAR', returnPeriod: 500, maxTvarM: 130, regionPerilKey: '' }]);
    } else if (kind === 'RP_CAP') {
      setConstraints((cs) => [...cs, { id, kind: 'RP_CAP', regionPerilKey: 'US-CA-EQ', maxLimitM: 60 }]);
    } else if (kind === 'EL_CAP') {
      setConstraints((cs) => [...cs, { id, kind: 'EL_CAP', maxRatio: 0.35 }]);
    } else {
      setConstraints((cs) => [...cs, { id, kind: 'CEDENT_CAP', maxLimitM: 85 }]);
    }
  };

  const applyLayerPreset = (preset: 'open' | 'band' | 'lockAll') => {
    if (preset === 'open') {
      // Dropping every override returns all layers, on every page, to the solver's open 0–100% defaults.
      setLayerBounds({});
      return;
    }
    const listed = listedLayers(candidates);
    setLayerBounds((prev) => {
      const next = { ...prev };
      for (const l of listed) {
        const cur = clampSharePct(l.currentShare * 100);
        next[l.layerKey] =
          preset === 'band'
            ? computeAutoBandBound(l, bandPct)
            : { minPct: cur, maxPct: cur, locked: true };
      }
      return next;
    });
  };

  const morePagesThanListed = !!candidates && candidates.matchedProgrammes > candidates.programmeIds.length;
  const resultIsStale =
    !!data?.includedStatuses && !exploreMut.isPending && !sameStatuses(data.includedStatuses, selectedStatuses);

  // Auto-calculated Deployed Capital (read-only): sum of max(0, (limit - premium) * lineShare) for assumed layers
  const currentDeployedCapitalM = useMemo(() => {
    if (data && !resultIsStale) {
      return data.initialPortfolio.deployedCollateralM;
    }
    if (typeof candidates?.universeDeployedCapitalM === 'number') {
      return candidates.universeDeployedCapitalM;
    }
    return Number(
      (candidates?.layers ?? [])
        .reduce((sum, l) => sum + computeLayerDeployedCapitalM(l, l.currentShare), 0)
        .toFixed(2),
    );
  }, [data, resultIsStale, candidates]);

  const capitalUtilPct = totalCapital > 0 ? (currentDeployedCapitalM / totalCapital) * 100 : 0;
  const capitalHeadroomM = totalCapital - currentDeployedCapitalM;
  const currentCapitalExceeded = currentDeployedCapitalM > totalCapital + 1e-6;

  // Merge listed page layers with any full-portfolio initialLayers (same statuses), preserving 0%-line deals via limit1xM
  const conflictUniverseLayers = useMemo<OptimizerLayerCatalogItem[]>(() => {
    const byKey = new Map<string, OptimizerLayerCatalogItem>();
    if (data && !resultIsStale) {
      for (const l of data.initialLayers) {
        if (isHedgeDirection(l.direction)) continue;
        const share = l.currentShare > 1e-6 ? l.currentShare : 0;
        const lim1x =
          typeof l.limit1xM === 'number' && l.limit1xM > 0
            ? l.limit1xM
            : share > 1e-6
              ? l.optimalLimitM / share
              : 0;
        const prem1x =
          typeof l.premium1xM === 'number' && l.premium1xM >= 0
            ? l.premium1xM
            : share > 1e-6
              ? l.optimalPremiumM / share
              : 0;
        const cap1x =
          share > 1e-6 && typeof l.optimalCapitalM === 'number'
            ? l.optimalCapitalM / share
            : Math.max(0, lim1x - prem1x);
        if (lim1x <= 0) continue;
        byKey.set(l.layerKey, {
          layerKey: l.layerKey,
          programmeId: l.programmeId,
          layerId: l.layerId,
          cedentName: l.cedentName,
          programmeName: l.programmeName,
          layerName: l.layerName,
          direction: l.direction,
          status: l.status,
          regionPerils: l.regionPerils,
          currentShare: l.currentShare,
          limit1xM: lim1x,
          premium1xM: prem1x,
          capital1xM: cap1x,
          el1xM: share > 1e-6 ? l.optimalElM / share : 0,
          expectedProfit1xM: share > 1e-6 ? l.optimalExpectedProfitM / share : 0,
          rolPct: lim1x > 0 ? (prem1x / lim1x) * 100 : 0,
          defaultMinShare: 0,
          defaultMaxShare: 1.0,
        });
      }
    }
    for (const l of candidates?.layers ?? []) {
      byKey.set(l.layerKey, l);
    }
    return [...byKey.values()];
  }, [candidates?.layers, data, resultIsStale]);

  const serverConflictsMatchCurrent =
    !resultIsStale && lastSolvedConstraintSig !== null && lastSolvedConstraintSig === currentConstraintSig;

  const conflictAnalysis = useMemo(
    () =>
      detectOptimizerConflicts({
        layers: conflictUniverseLayers,
        bounds: layerBounds,
        maxSingleCedentLimitM: activeSingleCedentCapM,
        fundCapitalM: totalCapital,
        minLineSizeShare: minLine,
        serverConflicts: serverConflictsMatchCurrent ? (data?.conflicts ?? []) : [],
        universeMaxLayerLimit1xM: candidates?.universeMaxLayerLimit1xM,
        universeMaxLayerLabel: candidates?.universeMaxLayerLabel,
        universeMaxCedentLimit1xM: candidates?.universeMaxCedentLimit1xM,
        universeMaxCedentName: candidates?.universeMaxCedentName,
        hasAllUniverseLayers: Boolean(
          (candidates && !morePagesThanListed && !candidates.search) || (data && !resultIsStale),
        ),
      }),
    [
      conflictUniverseLayers,
      layerBounds,
      activeSingleCedentCapM,
      totalCapital,
      minLine,
      serverConflictsMatchCurrent,
      data?.conflicts,
      resultIsStale,
      candidates,
      morePagesThanListed,
    ],
  );

  return (
    <>
      <div className="card">
        <div className="card-title">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span>Frontier Explorer</span>
            <span className="badge">HiGHS LP / MILP</span>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <button
              className={`tab ${subTab === 'config' ? 'active' : ''}`}
              onClick={() => setSubTab('config')}
            >
              1. Optimization Config
            </button>
            <button
              className={`tab ${subTab === 'results' ? 'active' : ''}`}
              onClick={() => setSubTab('results')}
            >
              2. Results Dashboard
            </button>
            <button
              className={`tab ${subTab === 'portfolio' ? 'active' : ''}`}
              onClick={() => setSubTab('portfolio')}
            >
              3. Portfolio Dashboard
            </button>
            <button
              className="btn"
              onClick={() => {
                exploreMut.mutate();
                if (subTab === 'config') setSubTab('results');
              }}
              disabled={exploreMut.isPending}
            >
              {exploreMut.isPending ? 'Solving Frontier…' : 'Run Frontier Explorer'}
            </button>
          </div>
        </div>

        {(exploreMut.error || forkBranchMut.error) && (
          <div className="error-banner" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
            <span>{String(((exploreMut.error ?? forkBranchMut.error) as Error).message)}</span>
            {exploreMut.error && (
              <button type="button" className="btn btn-ghost" onClick={() => exploreMut.mutate()}>
                Retry
              </button>
            )}
          </div>
        )}

        {forkedBranchMsg && (
          <div
            style={{
              background: 'rgba(34, 197, 94, 0.12)',
              border: '1px solid rgba(34, 197, 94, 0.4)',
              color: '#7fe0a9',
              borderRadius: 6,
              padding: '8px 10px',
              fontSize: 12,
              marginBottom: 12,
            }}
          >
            {forkedBranchMsg}
          </div>
        )}

        {/* ══════════════════ TAB 1: OPTIMIZATION CONFIG ══════════════════ */}
        {subTab === 'config' && (
          <div className="grid-2">
            {/* Left: Objectives & Dynamic Constraints */}
            <div>
              <div
                className="panel" style={{ marginBottom: 12 }}
              >
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 10 }}>
                  Objectives & Solver Settings
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Primary Objective
                    <select
                      value={objective}
                      onChange={(e) => setObjective(e.target.value as OptimizerObjective)}
                      style={{ width: '100%', marginTop: 4 }}
                    >
                      <option value="MAX_EXPECTED_PROFIT">Max Expected Return</option>
                      <option value="MAX_RETURN_ON_TVAR_250">Peak Ret / TVaR 250</option>
                      <option value="MAX_RETURN_ON_TVAR_100">Peak Ret / TVaR 100</option>
                      <option value="MAX_RETURN_ON_TVAR_1000">Peak Ret / TVaR 1000</option>
                      <option value="MAX_RISK_ADJUSTED_RETURN">Risk-Adjusted (E[R] − λ·TVaR)</option>
                      <option value="MIN_TVAR_250">Min 1-in-250 TVaR</option>
                    </select>
                  </label>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Horizon Mode
                    <select
                      value={mode}
                      onChange={(e) => {
                        const next = e.target.value as OptimizerMode;
                        setMode(next);
                        // A selection that is exactly the new mode's defaults means "defaults" again.
                        if (customStatuses && sameStatuses(customStatuses, defaultStatusesForMode(next))) {
                          setCustomStatuses(null);
                        }
                        setOffset(0);
                      }}
                      style={{ width: '100%', marginTop: 4 }}
                    >
                      <option value="FORWARD_PLANNING">{MODE_LABELS.FORWARD_PLANNING}</option>
                      <option value="CURRENT">{MODE_LABELS.CURRENT}</option>
                    </select>
                  </label>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    View of Risk
                    <select
                      value={view}
                      onChange={(e) => {
                        setView(e.target.value as any);
                        setOffset(0);
                      }}
                      style={{ width: '100%', marginTop: 4 }}
                    >
                      <option value="ADJUSTED">ADJUSTED (House Uplifts)</option>
                      <option value="UNADJUSTED">UNADJUSTED (Raw Model)</option>
                    </select>
                  </label>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Fund Capital ($M)
                    <input
                      type="number"
                      min={1}
                      value={totalCapital}
                      onChange={(e) => setTotalCapital(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <div
                    className={`capital-readonly-box${currentCapitalExceeded ? ' exceeded' : ''}`}
                    data-testid="optimizer-deployed-capital-readonly"
                    title="Auto-calculated read-only: Σ max(0, (Limit − Deposit Premium) × Line Share) across assumed layers. Solver enforces Deployed Capital ≤ Fund Capital."
                  >
                    <div className="capital-readonly-label">
                      Deployed Capital (Auto)
                      <span className={`capital-util-pill${currentCapitalExceeded ? ' exceeded' : ''}`}>
                        {fmtPct(capitalUtilPct, 1)}
                      </span>
                    </div>
                    <div className="capital-readonly-val">
                      {fmtMoneyM(currentDeployedCapitalM, 2)}
                      <span className="capital-readonly-sub">
                        {currentCapitalExceeded
                          ? `Over by ${fmtMoneyM(Math.abs(capitalHeadroomM), 1)}`
                          : `${fmtMoneyM(capitalHeadroomM, 1)} headroom`}
                      </span>
                    </div>
                    <div className="capital-readonly-formula">Σ (Limit − Prem) × Line · Cap ≤ Fund Capital</div>
                  </div>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Target 1-in-250 TVaR ($M)
                    <input
                      type="number"
                      value={maxTvar250}
                      onChange={(e) => setMaxTvar250(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Max 1-in-100 TVaR ($M)
                    <input
                      type="number"
                      value={maxTvar100}
                      onChange={(e) => setMaxTvar100(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Min Line Share % (MILP &gt; 0)
                    <input
                      type="number"
                      step="5"
                      min="0"
                      max="100"
                      value={Number((minLine * 100).toFixed(1))}
                      onChange={(e) => setMinLine(clampSharePct(Number(e.target.value)) / 100)}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Frontier Points ({numPoints})
                    <input
                      type="range"
                      min={6}
                      max={18}
                      value={numPoints}
                      onChange={(e) => setNumPoints(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 6 }}
                    />
                  </label>
                  {objective === 'MAX_RISK_ADJUSTED_RETURN' && (
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Risk Aversion λ
                      <input
                        type="number"
                        step="0.05"
                        value={riskLambda}
                        onChange={(e) => setRiskLambda(Number(e.target.value))}
                        style={{ width: '100%', marginTop: 4 }}
                      />
                    </label>
                  )}
                </div>

                {currentCapitalExceeded && (
                  <div className="warning-banner" role="alert" style={{ marginTop: 10, marginBottom: 0 }} data-testid="optimizer-capital-warning">
                    <strong>Current Deployed Capital exceeds Fund Capital:</strong> Initial book deploys{' '}
                    <b>{fmtMoneyM(currentDeployedCapitalM, 2)}</b> against <b>{fmtMoneyM(totalCapital, 2)}</b> Fund
                    Capital ({fmtPct(capitalUtilPct, 1)} utilization; over by{' '}
                    <b>{fmtMoneyM(Math.abs(capitalHeadroomM), 2)}</b>). The optimizer will scale down lines to stay
                    within Fund Capital unless layer min/locked bounds prevent it.
                  </div>
                )}

                {objective.startsWith('MAX_RETURN_ON_TVAR_') && (
                  <div className="deal-sub" style={{ marginTop: 8 }}>
                    Targets the peak <b>Expected Return / TVaR</b> portfolio under all active constraints via fractional LP/MILP and selects it automatically on the frontier.
                  </div>
                )}

                <div
                  style={{
                    display: 'flex',
                    gap: 12,
                    flexWrap: 'wrap',
                    alignItems: 'center',
                    marginTop: 12,
                    paddingTop: 10,
                    borderTop: '1px solid var(--surface-3)',
                  }}
                >
                  <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <input
                      type="checkbox"
                      checked={includeRetro}
                      onChange={(e) => setIncludeRetro(e.target.checked)}
                    />
                    Include Candidate Retro FL ILW Hedge
                  </label>
                  {includeRetro && (
                    <>
                      <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        Limit ($M)
                        <input
                          type="number"
                          value={retroLimit}
                          onChange={(e) => setRetroLimit(Number(e.target.value))}
                          style={{ width: 65, marginLeft: 4 }}
                        />
                      </label>
                      <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        Trigger ($B)
                        <input
                          type="number"
                          value={retroTrigger}
                          onChange={(e) => setRetroTrigger(Number(e.target.value))}
                          style={{ width: 65, marginLeft: 4 }}
                        />
                      </label>
                    </>
                  )}
                </div>
              </div>

              {/* Dynamic Constraints Builder */}
              <div className="panel">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    gap: 6,
                    marginBottom: 10,
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
                    Dynamic Portfolio Constraints ({constraints.length})
                  </span>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button className="btn btn-ghost" onClick={() => addConstraint('TVAR')}>
                      + TVaR Cap
                    </button>
                    <button className="btn btn-ghost" onClick={() => addConstraint('RP_CAP')}>
                      + Region-Peril Cap
                    </button>
                    <button className="btn btn-ghost" onClick={() => addConstraint('EL_CAP')}>
                      + EL / Capital
                    </button>
                    <button className="btn btn-ghost" onClick={() => addConstraint('CEDENT_CAP')}>
                      + Single Cedent
                    </button>
                  </div>
                </div>

                {conflictAnalysis.conflicts.length > 0 && (
                  <div
                    className={`conflict-banner ${conflictAnalysis.hasBlockingError ? 'error' : 'warning'}`}
                    role="alert"
                    data-testid="optimizer-conflict-banner"
                  >
                    <div className="conflict-banner-title">
                      {conflictAnalysis.hasBlockingError
                        ? `Constraint Conflict Detected (${conflictAnalysis.conflicts.filter((c) => c.severity === 'ERROR').length} infeasible)`
                        : `Constraint Warning (${conflictAnalysis.conflicts.length})`}
                    </div>
                    <ul className="conflict-list">
                      {conflictAnalysis.conflicts.map((c, i) => (
                        <li key={`${c.kind}-${c.cedentName ?? 'all'}-${i}`}>
                          <span className={`conflict-tag ${c.severity === 'ERROR' ? 'error' : 'warning'}`}>
                            {c.severity === 'ERROR' ? 'CONFLICT' : 'WARNING'}
                          </span>{' '}
                          {c.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {constraints.length === 0 ? (
                  <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>
                    No extra constraints added (base collateral &amp; 1-in-100 / 1-in-250 TVaR still apply).
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {constraints.map((c, idx) => (
                      <div
                        key={c.id}
                        style={{
                          display: 'flex',
                          gap: 8,
                          flexWrap: 'wrap',
                          alignItems: 'center',
                          background: 'var(--surface)',
                          border: '1px solid var(--border-strong)',
                          borderRadius: 6,
                          padding: '7px 10px',
                        }}
                      >
                        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent)', minWidth: 96 }}>
                          {c.kind === 'TVAR'
                            ? 'Tail VaR'
                            : c.kind === 'RP_CAP'
                              ? 'Region-Peril'
                              : c.kind === 'EL_CAP'
                                ? 'EL / Capital'
                                : 'Single Cedent'}
                        </span>

                        {c.kind === 'TVAR' && (
                          <>
                            <select
                              value={c.returnPeriod}
                              onChange={(e) =>
                                setConstraints((cs) =>
                                  cs.map((x, i) => (i === idx && x.kind === 'TVAR' ? { ...x, returnPeriod: Number(e.target.value) } : x)),
                                )
                              }
                            >
                              {[50, 100, 200, 250, 500, 1000].map((rp) => (
                                <option key={rp} value={rp}>
                                  1-in-{rp}
                                </option>
                              ))}
                            </select>
                            <select
                              value={c.regionPerilKey}
                              onChange={(e) =>
                                setConstraints((cs) =>
                                  cs.map((x, i) => (i === idx && x.kind === 'TVAR' ? { ...x, regionPerilKey: e.target.value } : x)),
                                )
                              }
                            >
                              <option value="">All Perils</option>
                              {REGION_PERILS.map((rp) => (
                                <option key={rp} value={rp}>
                                  {rp}
                                </option>
                              ))}
                            </select>
                            <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                              Max $M
                              <input
                                type="number"
                                value={c.maxTvarM}
                                onChange={(e) =>
                                  setConstraints((cs) =>
                                    cs.map((x, i) => (i === idx && x.kind === 'TVAR' ? { ...x, maxTvarM: Number(e.target.value) } : x)),
                                  )
                                }
                                style={{ width: 70, marginLeft: 4 }}
                              />
                            </label>
                          </>
                        )}

                        {c.kind === 'RP_CAP' && (
                          <>
                            <select
                              value={c.regionPerilKey}
                              onChange={(e) =>
                                setConstraints((cs) =>
                                  cs.map((x, i) => (i === idx && x.kind === 'RP_CAP' ? { ...x, regionPerilKey: e.target.value } : x)),
                                )
                              }
                            >
                              {REGION_PERILS.map((rp) => (
                                <option key={rp} value={rp}>
                                  {rp}
                                </option>
                              ))}
                            </select>
                            <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                              Max Limit $M
                              <input
                                type="number"
                                value={c.maxLimitM}
                                onChange={(e) =>
                                  setConstraints((cs) =>
                                    cs.map((x, i) => (i === idx && x.kind === 'RP_CAP' ? { ...x, maxLimitM: Number(e.target.value) } : x)),
                                  )
                                }
                                style={{ width: 70, marginLeft: 4 }}
                              />
                            </label>
                          </>
                        )}

                        {c.kind === 'EL_CAP' && (
                          <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                            Max Ratio
                            <input
                              type="number"
                              step="0.05"
                              value={c.maxRatio}
                              onChange={(e) =>
                                setConstraints((cs) =>
                                  cs.map((x, i) => (i === idx && x.kind === 'EL_CAP' ? { ...x, maxRatio: Number(e.target.value) } : x)),
                                )
                              }
                              style={{ width: 70, marginLeft: 4 }}
                            />
                          </label>
                        )}

                        {c.kind === 'CEDENT_CAP' && (
                          <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                            Max Limit $M
                            <input
                              type="number"
                              value={c.maxLimitM}
                              onChange={(e) =>
                                setConstraints((cs) =>
                                  cs.map((x, i) => (i === idx && x.kind === 'CEDENT_CAP' ? { ...x, maxLimitM: Number(e.target.value) } : x)),
                                )
                              }
                              style={{ width: 70, marginLeft: 4 }}
                            />
                          </label>
                        )}

                        <button
                          className="btn btn-ghost"
                          style={{ marginLeft: 'auto', color: '#f7a3a1', padding: '3px 8px' }}
                          onClick={() => setConstraints((cs) => cs.filter((x) => x.id !== c.id))}
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Right: which deals are in the portfolio, and each layer's participation bounds */}
            <div className="panel">
              <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 10 }}>
                Deals in Portfolio
              </div>
              <OptimizerStatusFilter
                modeLabel={MODE_LABELS[mode]}
                selected={selectedStatuses}
                isCustom={customStatuses !== null}
                counts={statusCounts}
                universeProgrammes={universeProgrammes}
                onToggle={(status) => changeStatuses(toggleStatus(customStatuses, defaultStatuses, status))}
                onSelectAll={() =>
                  changeStatuses(sameStatuses(PROGRAMME_STATUSES, defaultStatuses) ? null : [...PROGRAMME_STATUSES])
                }
                onReset={() => changeStatuses(null)}
              />
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: 6,
                  marginBottom: 10,
                }}
              >
                <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
                  Layer Participation Constraints (Min % / Max % ≤ 100% / Lock)
                </span>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                  <button className="btn btn-ghost" onClick={() => applyLayerPreset('open')}>
                    0–100% Open
                  </button>
                  <span className="band-control-group">
                    <input
                      type="number"
                      aria-label="Auto-band percentage"
                      min={1}
                      max={100}
                      step={5}
                      value={bandPct}
                      onChange={(e) => {
                        const n = Number(e.target.value);
                        if (Number.isFinite(n)) setBandPct(Math.min(100, Math.max(1, n)));
                      }}
                      className="bound-input"
                      style={{ width: 52, height: 28 }}
                      title="Relative ±% band applied to current held lines (capped at 100%)"
                    />
                    <button
                      className="btn btn-ghost"
                      onClick={() => applyLayerPreset('band')}
                      title="Relative ±% around current line for held layers (e.g. 10% ± 25% → 7.5%–12.5%); 0–100% for 0%-line layers; max capped at 100%"
                    >
                      ±{bandPct}% Band
                    </button>
                  </span>
                  <button className="btn btn-ghost" onClick={() => applyLayerPreset('lockAll')}>
                    Lock All
                  </button>
                </div>
              </div>
              {morePagesThanListed && (
                <div className="deal-sub" style={{ marginBottom: 8 }}>
                  ±{bandPct}% Band (relative to current line, max 100%) and Lock All apply to the layers listed on this
                  page. 0–100% Open clears every bound. Layers you have not changed stay open up to 100%.
                </div>
              )}
              <OptimizerCandidatesTable
                page={candidates}
                isPreviousPage={candidatesQ.isPlaceholderData}
                offset={offset}
                pageSize={CANDIDATE_PAGE_SIZE}
                isFetching={candidatesQ.isFetching}
                error={candidatesQ.error}
                onRetry={() => void candidatesQ.refetch()}
                search={search}
                onSearch={setSearch}
                onPage={setOffset}
                bounds={layerBounds}
                onBoundChange={(layerKey, next) => setLayerBounds((prev) => ({ ...prev, [layerKey]: next }))}
                rowConflicts={conflictAnalysis.rowConflictsByLayerKey}
              />
            </div>
          </div>
        )}

        {/* ══════════════════ TAB 2: RESULTS DASHBOARD (FRONTIER EXPLORER) ══════════════════ */}
        {subTab === 'results' && (
          <>
            {exploreMut.isPending && !data && (
              <div style={{ color: 'var(--text-muted)', fontSize: 12, padding: 12 }}>
                Sweeping layer-level Pareto Frontier via HiGHS…
              </div>
            )}

            {data && activePoint && (
              <>
                {/* Interactive Frontier Scrubber + Quick Actions */}
                <div
                  style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 10,
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    background: 'var(--surface-sunken)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 6,
                    padding: '8px 12px',
                    marginBottom: 12,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--warning)' }}>
                      Point #{activePoint.pointIndex + 1} / {data.frontier.length}
                    </span>
                    <input
                      type="range"
                      aria-label="Frontier point scrubber"
                      min={0}
                      max={data.frontier.length - 1}
                      value={selectedIdx}
                      onChange={(e) => setSelectedIdx(Number(e.target.value))}
                      style={{ width: 160 }}
                    />
                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      {activePoint.solver} · {activePoint.status} · {data.computeMs} ms ({data.frontier.length} solves)
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                    {peakPoint && (
                      <button
                        type="button"
                        className={`btn btn-peak ${isAtPeak ? 'at-peak' : ''}`}
                        onClick={() => setSelectedIdx(peakPoint.pointIndex)}
                        title={`Jump to the constrained frontier point maximizing ${horizon.label}`}
                      >
                        {isAtPeak ? '★ At Peak' : '★ Jump to Peak'} {horizon.shortLabel} (#{peakPoint.pointIndex + 1} ·{' '}
                        {formatRatioHeadline(getRatioValue(peakPoint.metrics, horizon.ratioKey), horizon.isMultiple)})
                      </button>
                    )}
                    <button
                      className="btn"
                      style={{ background: 'var(--teal-strong)', height: 30, fontSize: 12 }}
                      onClick={() => forkBranchMut.mutate()}
                      disabled={forkBranchMut.isPending}
                    >
                      {forkBranchMut.isPending ? 'Forking…' : 'Fork Point → Portfolio Branch'}
                    </button>
                  </div>
                </div>

                <PortfolioStatusSummary layers={activePoint.layers} stale={resultIsStale} />

                {(activePoint.status === 'Infeasible' || (data.conflicts && data.conflicts.length > 0)) && (
                  <div
                    className={`conflict-banner ${activePoint.status === 'Infeasible' || data.conflicts?.some((c) => c.severity === 'ERROR') ? 'error' : 'warning'}`}
                    role="alert"
                    data-testid="results-conflict-banner"
                  >
                    <div className="conflict-banner-title">
                      {activePoint.status === 'Infeasible'
                        ? 'Infeasible Portfolio Constraints'
                        : 'Active Constraint Notices'}
                    </div>
                    {data.conflicts && data.conflicts.length > 0 ? (
                      <ul className="conflict-list">
                        {data.conflicts.map((c, i) => (
                          <li key={`res-${c.kind}-${c.cedentName ?? 'all'}-${i}`}>
                            <span className={`conflict-tag ${c.severity === 'ERROR' ? 'error' : 'warning'}`}>
                              {c.severity === 'ERROR' ? 'CONFLICT' : 'WARNING'}
                            </span>{' '}
                            {c.message}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div>
                        Solver status is <b>Infeasible</b>. Open <b>1. Optimization Config</b> to inspect constraints and
                        highlighted conflicting layers.
                      </div>
                    )}
                  </div>
                )}

                {/* Top KPI Strip (compact single row on desktop) */}
                <div className="grid-kpi">
                  <div className="kpi">
                    <div className="kpi-label">Expected Return</div>
                    <div className="kpi-val" style={{ color: 'var(--positive)' }}>
                      {fmtMoneyM(activePoint.metrics.expectedProfitM, 2)}
                    </div>
                    <div className="kpi-sub">Mean ROC {fmtPct(activePoint.metrics.meanRocPct, 2)}</div>
                  </div>
                  <div className="kpi">
                    <div className="kpi-label">1-in-250 TVaR</div>
                    <div className="kpi-val" style={{ color: 'var(--accent)' }}>
                      {fmtMoneyM(activePoint.metrics.tvar250M, 1)}
                    </div>
                    <div className="kpi-sub">
                      100yr {fmtMoneyM(activePoint.metrics.tvar100M, 1)} · 1000yr {fmtMoneyM(activePoint.metrics.tvar1000M, 1)}
                    </div>
                  </div>
                  <div
                    className={`kpi ${horizon.ratioKey === 'returnToTvar100' ? 'kpi-highlight' : ''}`}
                    title={`Return / TVaR 1-in-100: ${formatRatioDual(getRatioValue(activePoint.metrics, 'returnToTvar100'))}`}
                  >
                    <div className="kpi-label-row">
                      <span className="kpi-label">Ret / TVaR 100</span>
                      {peakByRatio?.returnToTvar100 === activePoint.pointIndex && <span className="kpi-peak-tag">Peak</span>}
                    </div>
                    <div className="kpi-val">
                      {formatRatioHeadline(getRatioValue(activePoint.metrics, 'returnToTvar100'))}
                    </div>
                    <div className="kpi-sub">{fmtNum(getRatioValue(activePoint.metrics, 'returnToTvar100'), 3)}×</div>
                  </div>
                  <div
                    className={`kpi ${horizon.ratioKey === 'returnToTvar250' ? 'kpi-highlight' : ''}`}
                    title={`Return / TVaR 1-in-250: ${formatRatioDual(getRatioValue(activePoint.metrics, 'returnToTvar250'))}`}
                  >
                    <div className="kpi-label-row">
                      <span className="kpi-label">Ret / TVaR 250</span>
                      {peakByRatio?.returnToTvar250 === activePoint.pointIndex && <span className="kpi-peak-tag">Peak</span>}
                    </div>
                    <div className="kpi-val" style={{ color: 'var(--positive)' }}>
                      {formatRatioHeadline(getRatioValue(activePoint.metrics, 'returnToTvar250'))}
                    </div>
                    <div className="kpi-sub">{fmtNum(getRatioValue(activePoint.metrics, 'returnToTvar250'), 3)}×</div>
                  </div>
                  <div
                    className={`kpi ${horizon.ratioKey === 'returnToTvar1000' ? 'kpi-highlight' : ''}`}
                    title={`Return / TVaR 1-in-1000: ${formatRatioDual(getRatioValue(activePoint.metrics, 'returnToTvar1000'))}`}
                  >
                    <div className="kpi-label-row">
                      <span className="kpi-label">Ret / TVaR 1000</span>
                      {peakByRatio?.returnToTvar1000 === activePoint.pointIndex && <span className="kpi-peak-tag">Peak</span>}
                    </div>
                    <div className="kpi-val">
                      {formatRatioHeadline(getRatioValue(activePoint.metrics, 'returnToTvar1000'))}
                    </div>
                    <div className="kpi-sub">{fmtNum(getRatioValue(activePoint.metrics, 'returnToTvar1000'), 3)}×</div>
                  </div>
                  <div
                    className={`kpi ${horizon.ratioKey === 'returnToStdDev' ? 'kpi-highlight' : ''}`}
                    title={`Return / Loss Std Dev · EL / Capital ${fmtRatioPct(activePoint.metrics.elCapitalRatio, 1)}`}
                  >
                    <div className="kpi-label-row">
                      <span className="kpi-label">Ret / Std Dev</span>
                      {peakByRatio?.returnToStdDev === activePoint.pointIndex && <span className="kpi-peak-tag">Peak</span>}
                    </div>
                    <div className="kpi-val">
                      {formatRatioHeadline(getRatioValue(activePoint.metrics, 'returnToStdDev'), true)}
                    </div>
                    <div className="kpi-sub">EL / Cap {fmtRatioPct(activePoint.metrics.elCapitalRatio, 1)}</div>
                  </div>
                </div>

                {/* Row 1: Pareto Frontier + Diverging Share Summary */}
                <div className="grid-2" style={{ marginBottom: 14 }}>
                  <div className="panel">
                    <div className="frontier-toolbar">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
                          Pareto Frontier
                        </span>
                        <div className="seg" role="group" aria-label="Pareto chart view presets">
                          {FRONTIER_PRESETS.map((preset) => (
                            <button
                              key={preset.id}
                              type="button"
                              className={activePreset === preset.id ? 'active' : ''}
                              onClick={() => applyPreset(preset)}
                            >
                              {preset.label}
                            </button>
                          ))}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                        <select
                          aria-label="Y-axis metric"
                          className="frontier-select"
                          value={yKey}
                          onChange={(e) => setYKey(e.target.value as YAxisKey)}
                        >
                          {Y_AXIS_OPTIONS.map((opt) => (
                            <option key={opt.key} value={opt.key}>
                              Y: {opt.shortLabel}
                            </option>
                          ))}
                        </select>
                        <select
                          aria-label="X-axis metric"
                          className="frontier-select"
                          value={xKey}
                          onChange={(e) => setXKey(e.target.value as XAxisKey)}
                        >
                          {X_AXIS_OPTIONS.map((opt) => (
                            <option key={opt.key} value={opt.key}>
                              X: {opt.shortLabel}
                            </option>
                          ))}
                        </select>
                        {canShowTangent(xKey, yKey) && (
                          <label className="frontier-tangent-toggle" title="Show origin iso-ratio tangent line through the peak Return/Risk point">
                            <input
                              type="checkbox"
                              checked={showTangent}
                              onChange={(e) => setShowTangent(e.target.checked)}
                            />
                            Tangent
                          </label>
                        )}
                      </div>
                    </div>
                    <ParetoFrontierChart
                      initial={data.initialPortfolio}
                      frontier={data.frontier}
                      selectedIndex={activePoint.pointIndex}
                      xKey={xKey}
                      yKey={yKey}
                      showTangent={showTangent}
                      onSelectPoint={setSelectedIdx}
                    />
                  </div>

                  {/* Diverging Share Summary */}
                  <div className="panel">
                    <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 8 }}>
                      Share Summary — Proposed vs Initial (Δ)
                    </div>
                    <div className="table-wrap"><table className="compact-table">
                      <thead>
                        <tr>
                          <th>Layer</th>
                          <th className="num">Share</th>
                          <th>Δ Share</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedShareRows.map((r) => (
                          <tr key={r.layerKey} className={isOffRiskStatus(r.status) ? 'row-off-risk' : undefined}>
                            <td style={{ whiteSpace: 'normal', minWidth: 125 }}>
                              <div className="deal-name">
                                {r.cedentName}
                                <LayerStatusBadge status={r.status} direction={r.direction} />
                              </div>
                              <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{r.layerName}</div>
                            </td>
                            <td className="num" style={{ fontWeight: 700, color: 'var(--accent)' }}>
                              {(r.optimalShare * 100).toFixed(1)}%
                            </td>
                            <td>
                              <DeltaBar deltaShare={r.deltaShare} maxAbsDelta={maxAbsDelta} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table></div>
                  </div>
                </div>

                {/* Row 2: Portfolio Summary Comparison + 6-Axis Radar */}
                <div className="grid-2" style={{ marginBottom: 14 }}>
                  <div className="panel">
                    <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 8 }}>
                      Portfolio Summary — Initial vs Proposed
                    </div>
                    <div className="table-wrap"><table className="compact-table">
                      <thead>
                        <tr>
                          <th>Metric</th>
                          <th className="num">Initial</th>
                          <th className="num">Proposed</th>
                          <th className="num">Change (Δ)</th>
                          <th className="num">% Δ</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[
                          { label: 'Expected Return ($M)', a: data.initialPortfolio.expectedProfitM, b: activePoint.metrics.expectedProfitM, goodUp: true, fmt: 'money' as const },
                          { label: 'Mean ROC (%)', a: data.initialPortfolio.meanRocPct, b: activePoint.metrics.meanRocPct, goodUp: true, fmt: 'pct' as const },
                          {
                            label: 'Return / TVaR 1-in-100',
                            a: getRatioValue(data.initialPortfolio, 'returnToTvar100'),
                            b: getRatioValue(activePoint.metrics, 'returnToTvar100'),
                            goodUp: true,
                            fmt: 'ratio' as const,
                          },
                          {
                            label: 'Return / TVaR 1-in-250',
                            a: getRatioValue(data.initialPortfolio, 'returnToTvar250'),
                            b: getRatioValue(activePoint.metrics, 'returnToTvar250'),
                            goodUp: true,
                            fmt: 'ratio' as const,
                          },
                          {
                            label: 'Return / TVaR 1-in-1000',
                            a: getRatioValue(data.initialPortfolio, 'returnToTvar1000'),
                            b: getRatioValue(activePoint.metrics, 'returnToTvar1000'),
                            goodUp: true,
                            fmt: 'ratio' as const,
                          },
                          {
                            label: 'Return / Std Dev',
                            a: getRatioValue(data.initialPortfolio, 'returnToStdDev'),
                            b: getRatioValue(activePoint.metrics, 'returnToStdDev'),
                            goodUp: true,
                            fmt: 'mult' as const,
                          },
                          { label: 'TVaR 1-in-100 ($M)', a: data.initialPortfolio.tvar100M, b: activePoint.metrics.tvar100M, goodUp: false, fmt: 'money' as const },
                          { label: 'TVaR 1-in-250 ($M)', a: data.initialPortfolio.tvar250M, b: activePoint.metrics.tvar250M, goodUp: false, fmt: 'money' as const },
                          { label: 'TVaR 1-in-500 ($M)', a: data.initialPortfolio.tvar500M, b: activePoint.metrics.tvar500M, goodUp: false, fmt: 'money' as const },
                          { label: 'TVaR 1-in-1000 ($M)', a: data.initialPortfolio.tvar1000M, b: activePoint.metrics.tvar1000M, goodUp: false, fmt: 'money' as const },
                          { label: 'Loss Std Dev ($M)', a: data.initialPortfolio.stdDevM ?? 0, b: activePoint.metrics.stdDevM ?? 0, goodUp: false, fmt: 'money' as const },
                          { label: 'Deployed Capital ($M)', a: data.initialPortfolio.deployedCollateralM, b: activePoint.metrics.deployedCollateralM, goodUp: false, fmt: 'money' as const },
                          {
                            label: `Capital Utilization (% of ${fmtMoneyM(data.fundCapitalM ?? totalCapital, 0)})`,
                            a:
                              data.initialPortfolio.capitalUtilizationPct ??
                              ((data.initialPortfolio.deployedCollateralM / Math.max(1, data.fundCapitalM ?? totalCapital)) * 100),
                            b:
                              activePoint.metrics.capitalUtilizationPct ??
                              ((activePoint.metrics.deployedCollateralM / Math.max(1, data.fundCapitalM ?? totalCapital)) * 100),
                            goodUp: false,
                            fmt: 'pct' as const,
                          },
                        ].map((row) => {
                          const diff = row.b - row.a;
                          const pct = Math.abs(row.a) > 1e-6 ? (diff / Math.abs(row.a)) * 100 : 0;
                          const improved = row.goodUp ? diff >= -1e-4 : diff <= 1e-4;
                          const fmtVal = (v: number) => {
                            if (row.fmt === 'ratio') return formatRatioDual(v);
                            if (row.fmt === 'mult') return `${fmtNum(v, 2)}×`;
                            if (row.fmt === 'pct') return fmtPct(v, 2);
                            return fmtMoneyM(v, 2);
                          };
                          const fmtDiff = (d: number) => {
                            const sign = d >= 0 ? '+' : '';
                            if (row.fmt === 'ratio') return `${sign}${(d * 100).toFixed(1)} pts`;
                            if (row.fmt === 'mult') return `${sign}${d.toFixed(2)}×`;
                            if (row.fmt === 'pct') return `${sign}${d.toFixed(2)} pts`;
                            return `${sign}${d.toFixed(2)}`;
                          };
                          return (
                            <tr key={row.label}>
                              <td>{row.label}</td>
                              <td className="num">{fmtVal(row.a)}</td>
                              <td className="num" style={{ fontWeight: 700 }}>
                                {fmtVal(row.b)}
                              </td>
                              <td className="num" style={{ color: improved ? 'var(--positive)' : 'var(--warning)' }}>
                                {fmtDiff(diff)}
                              </td>
                              <td className="num" style={{ color: improved ? 'var(--positive)' : 'var(--warning)' }}>
                                {pct >= 0 ? '+' : ''}
                                {pct.toFixed(1)}%
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table></div>
                  </div>

                  <div className="panel">
                    <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 4 }}>
                      Multi-Metric Risk/Return Radar (% Δ vs Initial)
                    </div>
                    <PortfolioRadarChart initial={data.initialPortfolio} proposed={activePoint.metrics} />
                  </div>
                </div>

                {/* Row 3: Constraint Diagnostics & LP Shadow Prices */}
                <div className="panel">
                  <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 8 }}>
                    Constraint Diagnostics &amp; Shadow Prices (Duals)
                  </div>
                  <div className="table-wrap"><table>
                    <thead>
                      <tr>
                        <th>Constraint</th>
                        <th className="num">Limit</th>
                        <th className="num">Actual</th>
                        <th className="num">Slack</th>
                        <th className="num">Shadow Price</th>
                        <th className="num">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activePoint.constraints.map((c) => (
                        <tr key={c.name}>
                          <td>{c.name}</td>
                          <td className="num">{c.limitValue.toFixed(2)}</td>
                          <td className="num">{c.actualValue.toFixed(2)}</td>
                          <td className="num">{c.slack.toFixed(2)}</td>
                          <td className="num" style={{ color: 'var(--warning)', fontWeight: 600 }}>
                            {c.shadowPrice != null ? `$${c.shadowPrice.toFixed(4)}m` : '—'}
                          </td>
                          <td className={`num ${c.binding ? 'fail' : 'pass'}`}>{c.binding ? 'BINDING' : 'SLACK'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table></div>
                </div>
              </>
            )}
          </>
        )}

        {/* ══════════════════ TAB 3: PORTFOLIO DASHBOARD (LAYER CONTRIBUTION DEEP-DIVE) ══════════════════ */}
        {subTab === 'portfolio' && data && activePoint && (
          <>
            <PortfolioStatusSummary layers={activePoint.layers} stale={resultIsStale} />
            <div className="panel" style={{ marginBottom: 14 }}>
              <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 8 }}>
                Layer Summary — Frontier Point #{activePoint.pointIndex + 1} (TVaR 250: $
                {activePoint.metrics.tvar250M.toFixed(1)}m · Exp Return: ${activePoint.metrics.expectedProfitM.toFixed(2)}m · Ret/TVaR 250:{' '}
                {formatRatioDual(getRatioValue(activePoint.metrics, 'returnToTvar250'))})
              </div>
              <div className="table-wrap"><table className="dense-table">
                <thead>
                  <tr>
                    <th>Layer</th>
                    <th title="Deal status">Status</th>
                    <th className="num" title="Share">Share</th>
                    <th title="Share change versus the initial portfolio">Δ</th>
                    <th className="num" title="Limit, $M">Lim</th>
                    <th className="num" title="Premium, $M">Prem</th>
                    <th className="num" title="Expected loss, $M">EL</th>
                    <th className="num" title="Co-TVaR at 1-in-250">CoTVaR</th>
                    <th className="num" title="Share of TVaR">TVaR %</th>
                    <th className="num" title="Share of VaR">VaR %</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedShareRows.map((r) => (
                    <tr key={r.layerKey} className={isOffRiskStatus(r.status) ? 'row-off-risk' : undefined}>
                      <td>
                        <div style={{ fontWeight: 600, fontSize: 12 }}>{r.cedentName}</div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{r.layerName}</div>
                      </td>
                      <td>
                        <LayerStatusBadge status={r.status} direction={r.direction} />
                      </td>
                      <td className="num" style={{ fontWeight: 700, color: 'var(--accent)' }}>
                        {fmtRatioPct(r.optimalShare, 1)}
                      </td>
                      <td>
                        <DeltaBar deltaShare={r.deltaShare} maxAbsDelta={maxAbsDelta} />
                      </td>
                      <td className="num">{fmtMoneyM(r.optimalLimitM, 1)}</td>
                      <td className="num">{fmtMoneyM(r.optimalPremiumM, 2)}</td>
                      <td className="num">{fmtMoneyM(r.optimalElM, 2)}</td>
                      <td className="num">{fmtMoneyM(r.coTvar250M, 2)}</td>
                      <td className="num">{fmtPct(r.coTvar250Pct, 1)}</td>
                      <td className="num">{fmtPct(r.coVar250Pct, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </div>

            {/* 2x2 ECharts Contribution Analytics (Analyze Re Portfolio Dashboard) */}
            <div className="grid-2" style={{ marginBottom: 14 }}>
              <div className="panel">
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 6 }}>
                  Top Layers: Euler Contribution to Return Period (1-in-2 → 1-in-1000)
                </div>
                <ReturnPeriodStackChart rows={activePoint.returnPeriodContributions} />
              </div>

              <div className="panel">
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 6 }}>
                  Top Layers by Expected Loss ($M)
                </div>
                <LayerContributionBarChart
                  labels={[...activePoint.layers]
                    .sort((a, b) => b.optimalElM - a.optimalElM)
                    .map((l) => l.layerName)}
                  values={[...activePoint.layers]
                    .sort((a, b) => b.optimalElM - a.optimalElM)
                    .map((l) => Number(l.optimalElM.toFixed(3)))}
                  unit="$M"
                  color="var(--accent)"
                />
              </div>
            </div>

            <div className="grid-2">
              <div className="panel">
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 6 }}>
                  Top Contributing Layers: 1-in-250 TVaR (%)
                </div>
                <LayerContributionBarChart
                  labels={[...activePoint.layers]
                    .sort((a, b) => b.coTvar250Pct - a.coTvar250Pct)
                    .map((l) => l.layerName)}
                  values={[...activePoint.layers]
                    .sort((a, b) => b.coTvar250Pct - a.coTvar250Pct)
                    .map((l) => l.coTvar250Pct)}
                  unit="%"
                  color="#f43f5e"
                />
              </div>

              <div className="panel">
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 6 }}>
                  Top Contributing Layers: 1-in-250 VaR (%)
                </div>
                <LayerContributionBarChart
                  labels={[...activePoint.layers]
                    .sort((a, b) => b.coVar250Pct - a.coVar250Pct)
                    .map((l) => l.layerName)}
                  values={[...activePoint.layers]
                    .sort((a, b) => b.coVar250Pct - a.coVar250Pct)
                    .map((l) => l.coVar250Pct)}
                  unit="%"
                  color="var(--positive)"
                />
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
