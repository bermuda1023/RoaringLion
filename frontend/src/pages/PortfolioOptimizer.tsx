import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { fetchJson, postJson } from '../api/client';
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

interface OptimizerLayerCatalogItem {
  layerKey: string;
  programmeId: string;
  layerId: string;
  cedentName: string;
  programmeName: string;
  layerName: string;
  direction: string;
  status: string;
  regionPerils: string[];
  currentShare: number;
  limit1xM: number;
  premium1xM: number;
  el1xM: number;
  expectedProfit1xM: number;
  rolPct: number;
  defaultMinShare: number;
  defaultMaxShare: number;
}

interface FrontierExplorerResponse {
  mode: string;
  granularity: string;
  view: string;
  computeMs: number;
  initialPortfolio: PortfolioMetricsSnapshot;
  initialLayers: OptimizedLayerRow[];
  recommendedIndex: number;
  frontier: FrontierExplorerPoint[];
}

interface LayerBoundState {
  minPct: number;
  maxPct: number;
  locked: boolean;
}

type DynamicConstraint =
  | { id: string; kind: 'TVAR'; returnPeriod: number; maxTvarM: number; regionPerilKey: string }
  | { id: string; kind: 'RP_CAP'; regionPerilKey: string; maxLimitM: number }
  | { id: string; kind: 'EL_CAP'; maxRatio: number }
  | { id: string; kind: 'CEDENT_CAP'; maxLimitM: number };

type SubTab = 'results' | 'config' | 'portfolio';

const REGION_PERILS = ['US-FL-WS', 'US-TX-WS', 'US-SE-WS', 'US-CA-EQ', 'JP-KANTO-EQ', 'EU-DE-WS'];

function DeltaBar({ deltaShare, maxAbsDelta }: { deltaShare: number; maxAbsDelta: number }) {
  const pct = deltaShare * 100;
  const absPct = Math.abs(pct);
  const scale = Math.max(25, maxAbsDelta * 100);
  const widthPct = Math.min(100, (absPct / scale) * 100);

  if (absPct < 0.05) {
    return <span style={{ color: '#64748b', fontSize: 11 }}>No Change</span>;
  }

  const isPos = pct > 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 145 }}>
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', height: 14 }}>
        <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', paddingRight: 2 }}>
          {!isPos && (
            <div
              style={{
                width: `${widthPct}%`,
                height: 11,
                borderRadius: '3px 0 0 3px',
                background: 'linear-gradient(90deg, #ef4444, #dc2626)',
              }}
            />
          )}
        </div>
        <div style={{ width: 1, height: 16, background: '#475569' }} />
        <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-start', paddingLeft: 2 }}>
          {isPos && (
            <div
              style={{
                width: `${widthPct}%`,
                height: 11,
                borderRadius: '0 3px 3px 0',
                background: 'linear-gradient(90deg, #16a34a, #22c55e)',
              }}
            />
          )}
        </div>
      </div>
      <span
        style={{
          width: 54,
          textAlign: 'right',
          fontSize: 11,
          fontWeight: 600,
          color: isPos ? '#4ade80' : '#f87171',
        }}
      >
        {isPos ? '+' : ''}
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}

export function PortfolioOptimizer() {
  const qc = useQueryClient();
  const [subTab, setSubTab] = useState<SubTab>('results');

  // Core Optimization Config state
  const [mode, setMode] = useState<'CURRENT' | 'FORWARD_PLANNING'>('FORWARD_PLANNING');
  const [view, setView] = useState<'ADJUSTED' | 'UNADJUSTED'>('ADJUSTED');
  const [objective, setObjective] = useState<'MAX_EXPECTED_PROFIT' | 'MIN_TVAR_250' | 'MAX_RISK_ADJUSTED_RETURN'>(
    'MAX_EXPECTED_PROFIT',
  );
  const [riskLambda, setRiskLambda] = useState(0.15);
  const [totalCapital, setTotalCapital] = useState(160);
  const [maxCollateral, setMaxCollateral] = useState(160);
  const [maxTvar100, setMaxTvar100] = useState(95);
  const [maxTvar250, setMaxTvar250] = useState(115);
  const [minLine, setMinLine] = useState(0.0);
  const [includeRetro, setIncludeRetro] = useState(true);
  const [retroLimit, setRetroLimit] = useState(25);
  const [retroTrigger, setRetroTrigger] = useState(40);
  const [numPoints, setNumPoints] = useState(12);

  // Dynamic constraints builder (Analyze Re-style Add/Remove Constraints)
  const [constraints, setConstraints] = useState<DynamicConstraint[]>([
    { id: 'c-fl', kind: 'RP_CAP', regionPerilKey: 'US-FL-WS', maxLimitM: 75 },
    { id: 'c-el', kind: 'EL_CAP', maxRatio: 0.35 },
  ]);

  // Per-layer participation bounds
  const [layerBounds, setLayerBounds] = useState<Record<string, LayerBoundState>>({});

  // Frontier Explorer interactive selection & axis selectors
  const [selectedIdx, setSelectedIdx] = useState<number>(0);
  const [xKey, setXKey] = useState<XAxisKey>('tvar250M');
  const [yKey, setYKey] = useState<YAxisKey>('expectedProfitM');
  const [forkedBranchMsg, setForkedBranchMsg] = useState<string | null>(null);

  const layersQ = useQuery({
    queryKey: ['optimizerLayers', view, mode],
    queryFn: () => fetchJson<OptimizerLayerCatalogItem[]>(`/api/optimizer/layers?view=${view}&mode=${mode}`),
  });

  // Initialize editable layer bounds whenever the catalog loads
  useEffect(() => {
    if (!layersQ.data) return;
    setLayerBounds((prev) => {
      const next: Record<string, LayerBoundState> = { ...prev };
      for (const l of layersQ.data) {
        if (!next[l.layerKey]) {
          next[l.layerKey] = {
            minPct: Math.round(l.defaultMinShare * 100),
            maxPct: Math.round(l.defaultMaxShare * 100),
            locked: false,
          };
        }
      }
      return next;
    });
  }, [layersQ.data]);

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
    const cedCap = constraints.find(
      (c): c is Extract<DynamicConstraint, { kind: 'CEDENT_CAP' }> => c.kind === 'CEDENT_CAP',
    );

    const boundsList = Object.entries(layerBounds).map(([layerKey, b]) => ({
      layerKey,
      minShare: b.minPct / 100,
      maxShare: b.maxPct / 100,
      locked: b.locked,
    }));

    return {
      mode,
      granularity: 'LAYER',
      objective,
      riskAversionLambda: riskLambda,
      view,
      totalCapital,
      maxDeployedCollateral: maxCollateral,
      maxTvar100M: maxTvar100,
      maxTvar250M: maxTvar250,
      maxElCapitalRatio: elCap ? elCap.maxRatio : null,
      maxSingleCedentLimitM: cedCap ? cedCap.maxLimitM : null,
      regionPerilCaps,
      tvarCaps,
      layerBounds: boundsList,
      minLineSizeShare: minLine,
      includeRetroIlwCandidate: includeRetro,
      retroIlwLimitM: retroLimit,
      retroIlwTriggerBn: retroTrigger,
    };
  };

  const exploreMut = useMutation({
    mutationFn: () => postJson<FrontierExplorerResponse>(`/api/optimizer/explore?num_points=${numPoints}`, buildPayload()),
    onSuccess: (res) => {
      setSelectedIdx(res.recommendedIndex);
      setForkedBranchMsg(null);
    },
  });

  useEffect(() => {
    exploreMut.mutate();
  }, []);

  const data = exploreMut.data;
  const activePoint: FrontierExplorerPoint | null =
    data && data.frontier.length ? data.frontier[Math.min(selectedIdx, data.frontier.length - 1)] : null;

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
        overrides[pid] = Math.min(1.5, Math.max(0.0, Number(avg.toFixed(4))));
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

  const applyLayerPreset = (preset: 'open' | 'band25' | 'lockAll') => {
    if (!layersQ.data) return;
    const next: Record<string, LayerBoundState> = {};
    for (const l of layersQ.data) {
      const cur = Math.round(l.currentShare * 100);
      if (preset === 'open') {
        next[l.layerKey] = { minPct: 0, maxPct: l.direction === 'EXTERNAL_RETRO' ? 200 : 150, locked: false };
      } else if (preset === 'band25') {
        next[l.layerKey] = {
          minPct: Math.max(0, cur - 25),
          maxPct: Math.min(200, cur + 25),
          locked: false,
        };
      } else {
        next[l.layerKey] = { minPct: cur, maxPct: cur, locked: true };
      }
    }
    setLayerBounds(next);
  };

  return (
    <>
      <div className="card">
        <div className="card-title">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span>Optimization Frontier Explorer</span>
            <span className="badge">Layer-Level HiGHS LP/MILP · Interactive Pareto Frontier</span>
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
          <div className="error-banner">
            {String(((exploreMut.error ?? forkBranchMut.error) as Error).message)}
          </div>
        )}

        {forkedBranchMsg && (
          <div
            style={{
              background: 'rgba(34, 197, 94, 0.12)',
              border: '1px solid rgba(34, 197, 94, 0.4)',
              color: '#86efac',
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
                style={{
                  background: '#0d1422',
                  border: '1px solid #26324a',
                  borderRadius: 6,
                  padding: 12,
                  marginBottom: 12,
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 10 }}>
                  Objectives & Solver Settings
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
                    Primary Objective
                    <select
                      value={objective}
                      onChange={(e) => setObjective(e.target.value as any)}
                      style={{ width: '100%', marginTop: 4 }}
                    >
                      <option value="MAX_EXPECTED_PROFIT">Max Expected Return</option>
                      <option value="MAX_RISK_ADJUSTED_RETURN">Max Risk-Adjusted (E[R] − λ·TVaR)</option>
                      <option value="MIN_TVAR_250">Min 1-in-250 TVaR</option>
                    </select>
                  </label>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
                    Horizon Mode
                    <select
                      value={mode}
                      onChange={(e) => setMode(e.target.value as any)}
                      style={{ width: '100%', marginTop: 4 }}
                    >
                      <option value="FORWARD_PLANNING">Forward-Looking Planning</option>
                      <option value="CURRENT">Current Portfolio Rebalance</option>
                    </select>
                  </label>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
                    View of Risk
                    <select
                      value={view}
                      onChange={(e) => setView(e.target.value as any)}
                      style={{ width: '100%', marginTop: 4 }}
                    >
                      <option value="ADJUSTED">ADJUSTED (House Uplifts)</option>
                      <option value="UNADJUSTED">UNADJUSTED (Raw Model)</option>
                    </select>
                  </label>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
                    Fund Capital ($M)
                    <input
                      type="number"
                      value={totalCapital}
                      onChange={(e) => setTotalCapital(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
                    Max Deployed Collateral ($M)
                    <input
                      type="number"
                      value={maxCollateral}
                      onChange={(e) => setMaxCollateral(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
                    Target 1-in-250 TVaR ($M)
                    <input
                      type="number"
                      value={maxTvar250}
                      onChange={(e) => setMaxTvar250(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
                    Max 1-in-100 TVaR ($M)
                    <input
                      type="number"
                      value={maxTvar100}
                      onChange={(e) => setMaxTvar100(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
                    Min Line Share (MILP &gt; 0)
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      max="1"
                      value={minLine}
                      onChange={(e) => setMinLine(Number(e.target.value))}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                  </label>
                  <label style={{ fontSize: 11, color: '#94a3b8' }}>
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
                    <label style={{ fontSize: 11, color: '#94a3b8' }}>
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

                <div
                  style={{
                    display: 'flex',
                    gap: 12,
                    flexWrap: 'wrap',
                    alignItems: 'center',
                    marginTop: 12,
                    paddingTop: 10,
                    borderTop: '1px solid #1e293b',
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
                      <label style={{ fontSize: 11, color: '#94a3b8' }}>
                        Limit ($M)
                        <input
                          type="number"
                          value={retroLimit}
                          onChange={(e) => setRetroLimit(Number(e.target.value))}
                          style={{ width: 65, marginLeft: 4 }}
                        />
                      </label>
                      <label style={{ fontSize: 11, color: '#94a3b8' }}>
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
              <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
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
                  <span style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd' }}>
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

                {constraints.length === 0 ? (
                  <div style={{ color: '#64748b', fontSize: 12 }}>
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
                          background: '#131a29',
                          border: '1px solid #26324a',
                          borderRadius: 6,
                          padding: '7px 10px',
                        }}
                      >
                        <span style={{ fontSize: 11, fontWeight: 600, color: '#38bdf8', minWidth: 96 }}>
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
                            <label style={{ fontSize: 11, color: '#94a3b8' }}>
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
                            <label style={{ fontSize: 11, color: '#94a3b8' }}>
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
                          <label style={{ fontSize: 11, color: '#94a3b8' }}>
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
                          <label style={{ fontSize: 11, color: '#94a3b8' }}>
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
                          style={{ marginLeft: 'auto', color: '#fca5a5', padding: '3px 8px' }}
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

            {/* Right: Layer Participation Constraints Table (Analyze Re-style) */}
            <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
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
                <span style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd' }}>
                  Layer Participation Constraints (Min % / Max % / Lock)
                </span>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <button className="btn btn-ghost" onClick={() => applyLayerPreset('open')}>
                    0–150% Open
                  </button>
                  <button className="btn btn-ghost" onClick={() => applyLayerPreset('band25')}>
                    ±25% Band
                  </button>
                  <button className="btn btn-ghost" onClick={() => applyLayerPreset('lockAll')}>
                    Lock All
                  </button>
                </div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Layer</th>
                    <th className="num">Cur %</th>
                    <th className="num">Min %</th>
                    <th className="num">Max %</th>
                    <th className="num">Lock</th>
                  </tr>
                </thead>
                <tbody>
                  {(layersQ.data ?? []).map((l) => {
                    const b = layerBounds[l.layerKey] ?? {
                      minPct: Math.round(l.defaultMinShare * 100),
                      maxPct: Math.round(l.defaultMaxShare * 100),
                      locked: false,
                    };
                    return (
                      <tr key={l.layerKey}>
                        <td>
                          <div style={{ fontWeight: 600, fontSize: 12 }}>{l.cedentName}</div>
                          <div style={{ fontSize: 11, color: '#94a3b8' }}>
                            {l.layerName} · ${l.limit1xM.toFixed(0)}m lim · EL ${l.el1xM.toFixed(2)}m
                          </div>
                        </td>
                        <td className="num">{(l.currentShare * 100).toFixed(0)}%</td>
                        <td className="num">
                          <input
                            type="number"
                            min={0}
                            max={300}
                            disabled={b.locked}
                            value={b.minPct}
                            onChange={(e) =>
                              setLayerBounds((prev) => ({
                                ...prev,
                                [l.layerKey]: { ...b, minPct: Number(e.target.value) },
                              }))
                            }
                            style={{ width: 58, textAlign: 'right', color: b.minPct > 0 ? '#f87171' : '#e6edf7' }}
                          />
                        </td>
                        <td className="num">
                          <input
                            type="number"
                            min={0}
                            max={300}
                            disabled={b.locked}
                            value={b.maxPct}
                            onChange={(e) =>
                              setLayerBounds((prev) => ({
                                ...prev,
                                [l.layerKey]: { ...b, maxPct: Number(e.target.value) },
                              }))
                            }
                            style={{ width: 58, textAlign: 'right' }}
                          />
                        </td>
                        <td className="num">
                          <input
                            type="checkbox"
                            checked={b.locked}
                            onChange={(e) => {
                              const cur = Math.round(l.currentShare * 100);
                              setLayerBounds((prev) => ({
                                ...prev,
                                [l.layerKey]: {
                                  minPct: e.target.checked ? cur : b.minPct,
                                  maxPct: e.target.checked ? cur : b.maxPct,
                                  locked: e.target.checked,
                                },
                              }));
                            }}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ══════════════════ TAB 2: RESULTS DASHBOARD (FRONTIER EXPLORER) ══════════════════ */}
        {subTab === 'results' && (
          <>
            {exploreMut.isPending && !data && (
              <div style={{ color: '#94a3b8', fontSize: 12, padding: 12 }}>
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
                    gap: 12,
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    background: '#0d1422',
                    border: '1px solid #26324a',
                    borderRadius: 6,
                    padding: '9px 12px',
                    marginBottom: 14,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#f59e0b' }}>
                      Point #{activePoint.pointIndex + 1} / {data.frontier.length}
                    </span>
                    <input
                      type="range"
                      min={0}
                      max={data.frontier.length - 1}
                      value={selectedIdx}
                      onChange={(e) => setSelectedIdx(Number(e.target.value))}
                      style={{ width: 170 }}
                    />
                    <span style={{ fontSize: 11, color: '#94a3b8' }}>
                      {activePoint.solver} · {activePoint.status} · {data.computeMs} ms total ({data.frontier.length}{' '}
                      solves)
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <button
                      className="btn"
                      style={{ background: '#0f766e' }}
                      onClick={() => forkBranchMut.mutate()}
                      disabled={forkBranchMut.isPending}
                    >
                      {forkBranchMut.isPending ? 'Forking…' : 'Fork Point → Portfolio Branch'}
                    </button>
                  </div>
                </div>

                {/* Top KPI Strip */}
                <div className="grid-kpi">
                  <div className="kpi">
                    <div className="kpi-label">Expected Return</div>
                    <div className="kpi-val" style={{ color: '#22c55e' }}>
                      ${activePoint.metrics.expectedProfitM.toFixed(2)}m
                    </div>
                  </div>
                  <div className="kpi">
                    <div className="kpi-label">Mean ROC</div>
                    <div className="kpi-val">{activePoint.metrics.meanRocPct.toFixed(2)}%</div>
                  </div>
                  <div className="kpi">
                    <div className="kpi-label">1-in-100 TVaR</div>
                    <div className="kpi-val">${activePoint.metrics.tvar100M.toFixed(1)}m</div>
                  </div>
                  <div className="kpi">
                    <div className="kpi-label">1-in-250 TVaR</div>
                    <div className="kpi-val" style={{ color: '#38bdf8' }}>
                      ${activePoint.metrics.tvar250M.toFixed(1)}m
                    </div>
                  </div>
                  <div className="kpi">
                    <div className="kpi-label">Risk / Reward</div>
                    <div className="kpi-val">{activePoint.metrics.riskRewardRatio.toFixed(3)}</div>
                  </div>
                  <div className="kpi">
                    <div className="kpi-label">EL / Capital</div>
                    <div className="kpi-val">{(activePoint.metrics.elCapitalRatio * 100).toFixed(1)}%</div>
                  </div>
                </div>

                {/* Row 1: Pareto Frontier + Diverging Share Summary */}
                <div className="grid-2" style={{ marginBottom: 14 }}>
                  <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        flexWrap: 'wrap',
                        gap: 8,
                        marginBottom: 8,
                      }}
                    >
                      <span style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd' }}>
                        Pareto Frontier (Click any dot to inspect)
                      </span>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <select value={yKey} onChange={(e) => setYKey(e.target.value as YAxisKey)}>
                          <option value="expectedProfitM">Y: Expected Return ($M)</option>
                          <option value="meanRocPct">Y: Mean ROC (%)</option>
                          <option value="riskRewardRatio">Y: Risk / Reward</option>
                        </select>
                        <select value={xKey} onChange={(e) => setXKey(e.target.value as XAxisKey)}>
                          <option value="tvar250M">X: TVaR 1-in-250</option>
                          <option value="tvar100M">X: TVaR 1-in-100</option>
                          <option value="tvar500M">X: TVaR 1-in-500</option>
                          <option value="deployedCollateralM">X: Deployed Collateral</option>
                        </select>
                      </div>
                    </div>
                    <ParetoFrontierChart
                      initial={data.initialPortfolio}
                      frontier={data.frontier}
                      selectedIndex={activePoint.pointIndex}
                      xKey={xKey}
                      yKey={yKey}
                      onSelectPoint={setSelectedIdx}
                    />
                  </div>

                  {/* Diverging Share Summary */}
                  <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                    <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 8 }}>
                      Share Summary — Proposed vs Initial (Δ)
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th>Layer</th>
                          <th className="num">Share</th>
                          <th>Share Change (Δ)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedShareRows.map((r) => (
                          <tr key={r.layerKey}>
                            <td>
                              <div style={{ fontWeight: 600, fontSize: 12 }}>{r.cedentName}</div>
                              <div style={{ fontSize: 10, color: '#94a3b8' }}>{r.layerName}</div>
                            </td>
                            <td className="num" style={{ fontWeight: 700, color: '#38bdf8' }}>
                              {(r.optimalShare * 100).toFixed(1)}%
                            </td>
                            <td>
                              <DeltaBar deltaShare={r.deltaShare} maxAbsDelta={maxAbsDelta} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Row 2: Portfolio Summary Comparison + 6-Axis Radar */}
                <div className="grid-2" style={{ marginBottom: 14 }}>
                  <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                    <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 8 }}>
                      Portfolio Summary — Initial vs Proposed
                    </div>
                    <table>
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
                          { label: 'Expected Return ($M)', a: data.initialPortfolio.expectedProfitM, b: activePoint.metrics.expectedProfitM, goodUp: true },
                          { label: 'Mean ROC (%)', a: data.initialPortfolio.meanRocPct, b: activePoint.metrics.meanRocPct, goodUp: true },
                          { label: 'TVaR 1-in-100 ($M)', a: data.initialPortfolio.tvar100M, b: activePoint.metrics.tvar100M, goodUp: false },
                          { label: 'TVaR 1-in-250 ($M)', a: data.initialPortfolio.tvar250M, b: activePoint.metrics.tvar250M, goodUp: false },
                          { label: 'TVaR 1-in-500 ($M)', a: data.initialPortfolio.tvar500M, b: activePoint.metrics.tvar500M, goodUp: false },
                          { label: 'TVaR 1-in-1000 ($M)', a: data.initialPortfolio.tvar1000M, b: activePoint.metrics.tvar1000M, goodUp: false },
                          { label: 'Deployed Collateral ($M)', a: data.initialPortfolio.deployedCollateralM, b: activePoint.metrics.deployedCollateralM, goodUp: false },
                          { label: 'Risk / Reward Ratio', a: data.initialPortfolio.riskRewardRatio, b: activePoint.metrics.riskRewardRatio, goodUp: true },
                        ].map((row) => {
                          const diff = row.b - row.a;
                          const pct = Math.abs(row.a) > 1e-6 ? (diff / Math.abs(row.a)) * 100 : 0;
                          const improved = row.goodUp ? diff >= -1e-4 : diff <= 1e-4;
                          return (
                            <tr key={row.label}>
                              <td>{row.label}</td>
                              <td className="num">{row.a.toFixed(2)}</td>
                              <td className="num" style={{ fontWeight: 700 }}>
                                {row.b.toFixed(2)}
                              </td>
                              <td className="num" style={{ color: improved ? '#4ade80' : '#f59e0b' }}>
                                {diff >= 0 ? '+' : ''}
                                {diff.toFixed(2)}
                              </td>
                              <td className="num" style={{ color: improved ? '#4ade80' : '#f59e0b' }}>
                                {pct >= 0 ? '+' : ''}
                                {pct.toFixed(1)}%
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                    <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 4 }}>
                      Multi-Metric Risk/Return Radar (% Δ vs Initial)
                    </div>
                    <PortfolioRadarChart initial={data.initialPortfolio} proposed={activePoint.metrics} />
                  </div>
                </div>

                {/* Row 3: Constraint Diagnostics & LP Shadow Prices */}
                <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                  <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 8 }}>
                    Constraint Diagnostics &amp; Shadow Prices (Duals)
                  </div>
                  <table>
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
                          <td className="num" style={{ color: '#f59e0b', fontWeight: 600 }}>
                            {c.shadowPrice != null ? `$${c.shadowPrice.toFixed(4)}m` : '—'}
                          </td>
                          <td className={`num ${c.binding ? 'fail' : 'pass'}`}>{c.binding ? 'BINDING' : 'SLACK'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}

        {/* ══════════════════ TAB 3: PORTFOLIO DASHBOARD (LAYER CONTRIBUTION DEEP-DIVE) ══════════════════ */}
        {subTab === 'portfolio' && data && activePoint && (
          <>
            <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12, marginBottom: 14 }}>
              <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 8 }}>
                Layer Summary — Frontier Point #{activePoint.pointIndex + 1} (TVaR 250: $
                {activePoint.metrics.tvar250M.toFixed(1)}m · Exp Return: ${activePoint.metrics.expectedProfitM.toFixed(2)}m)
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Layer</th>
                    <th className="num">Share</th>
                    <th>Share Change (Δ)</th>
                    <th className="num">Limit ($M)</th>
                    <th className="num">Prem ($M)</th>
                    <th className="num">EL ($M)</th>
                    <th className="num">Co-TVaR 250</th>
                    <th className="num">TVaR %</th>
                    <th className="num">VaR %</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedShareRows.map((r) => (
                    <tr key={r.layerKey}>
                      <td>
                        <div style={{ fontWeight: 600, fontSize: 12 }}>{r.cedentName}</div>
                        <div style={{ fontSize: 10, color: '#94a3b8' }}>{r.layerName}</div>
                      </td>
                      <td className="num" style={{ fontWeight: 700, color: '#38bdf8' }}>
                        {(r.optimalShare * 100).toFixed(1)}%
                      </td>
                      <td>
                        <DeltaBar deltaShare={r.deltaShare} maxAbsDelta={maxAbsDelta} />
                      </td>
                      <td className="num">${r.optimalLimitM.toFixed(1)}m</td>
                      <td className="num">${r.optimalPremiumM.toFixed(2)}m</td>
                      <td className="num">${r.optimalElM.toFixed(2)}m</td>
                      <td className="num">${r.coTvar250M.toFixed(2)}m</td>
                      <td className="num">{r.coTvar250Pct.toFixed(1)}%</td>
                      <td className="num">{r.coVar250Pct.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* 2x2 ECharts Contribution Analytics (Analyze Re Portfolio Dashboard) */}
            <div className="grid-2" style={{ marginBottom: 14 }}>
              <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 6 }}>
                  Top Layers: Euler Contribution to Return Period (1-in-2 → 1-in-1000)
                </div>
                <ReturnPeriodStackChart rows={activePoint.returnPeriodContributions} />
              </div>

              <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 6 }}>
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
                  color="#38bdf8"
                />
              </div>
            </div>

            <div className="grid-2">
              <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 6 }}>
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

              <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: '#93c5fd', marginBottom: 6 }}>
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
                  color="#22c55e"
                />
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
