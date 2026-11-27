import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, apiUrl } from '../api/client';
import { type EpDimension, type EpMetricKey, PortfolioEpChart } from '../components/PortfolioEpChart';
import { PortfolioTimelineCompare } from '../components/PortfolioTimelineCompare';
import { RocCdfChart } from '../components/RocCdfChart';
import type {
  PortfolioEpPoint,
  PortfolioEpSlice,
  PortfolioHedgeComparisonResponse,
  PortfolioRollupResponse,
  ViewOfRisk,
} from '../types/contracts';
import { fmtCents, fmtMoneyM, fmtPct, fmtPts } from '../utils/format';

export type HedgeDisplayMode = 'with' | 'without' | 'compare';
export type PortfolioSubTab = 'live' | 'compare';

const RETURN_PERIOD_ORDER = [10, 25, 50, 100, 250, 500, 1000];

function epPointAt(points: PortfolioEpPoint[], rp: number): PortfolioEpPoint | undefined {
  return points.find((p) => Math.abs(p.returnPeriod - rp) < 1e-6);
}

const EP_METRIC_LABELS: Record<EpMetricKey, string> = {
  aep: 'AEP',
  oep: 'OEP',
  aepTvar: 'AEP TVaR',
  oepTvar: 'OEP TVaR',
};

export function PortfolioReturnsView({
  data,
  portfolioView,
  onChangeView,
  totalCapital,
  onChangeCapital,
  initialSubTab = 'live',
  refreshKey,
}: {
  data: PortfolioHedgeComparisonResponse;
  portfolioView: ViewOfRisk;
  onChangeView: (v: ViewOfRisk) => void;
  totalCapital: number;
  onChangeCapital: (cap: number) => void;
  initialSubTab?: PortfolioSubTab;
  refreshKey?: number | string;
}) {
  const [subTab, setSubTab] = useState<PortfolioSubTab>(initialSubTab);
  const [mode, setMode] = useState<HedgeDisplayMode>('compare');
  const [epDim, setEpDim] = useState<EpDimension>('TOTAL');
  const [epMetric, setEpMetric] = useState<EpMetricKey>('aep');
  const [selectedSliceKey, setSelectedSliceKey] = useState<string | null>(null);

  const { withHedges, withoutHedges, accretion } = data;
  const active: PortfolioRollupResponse = mode === 'without' ? withoutHedges : withHedges;
  const includeHedgesForExport = mode !== 'without';
  const deployedCapitalM = active.deployedCollateral;
  const capitalUtilPct =
    active.capitalUtilizationPct ?? (totalCapital > 0 ? (deployedCapitalM / totalCapital) * 100 : 0);
  const capitalHeadroomM = active.capitalHeadroomM ?? totalCapital - deployedCapitalM;
  const capitalExceeded = active.capitalExceeded ?? deployedCapitalM > totalCapital + 1e-6;

  const activeEpCurve: PortfolioEpPoint[] = active.epCurve ?? [];
  const grossEpCurve: PortfolioEpPoint[] = withoutHedges.epCurve ?? [];
  const activeSlices: PortfolioEpSlice[] =
    epDim === 'REGION'
      ? active.epByRegion ?? []
      : epDim === 'PERIL'
        ? active.epByPeril ?? []
        : epDim === 'REGION_PERIL'
          ? active.epByRegionPeril ?? []
          : [];

  const inspectedSlice = selectedSliceKey ? activeSlices.find((s) => s.key === selectedSliceKey) ?? null : null;

  const verdictTone = !accretion.hasHedges
    ? 'neutral'
    : accretion.isAccretive
      ? 'positive'
      : 'negative';

  const comparisonRows: {
    label: string;
    gross: string;
    net: string;
    delta: string;
    good: boolean | null;
  }[] = [
    {
      label: 'Net Premium',
      gross: fmtMoneyM(withoutHedges.grossPremium, 2),
      net: fmtMoneyM(withHedges.netPremium ?? withHedges.grossPremium, 2),
      delta: fmtMoneyM(-accretion.hedgePremiumM, 2, { signed: true }),
      good: null,
    },
    {
      label: 'Expected Loss (Net)',
      gross: fmtMoneyM(withoutHedges.expectedLoss, 2),
      net: fmtMoneyM(withHedges.expectedLoss, 2),
      delta: fmtMoneyM(withHedges.expectedLoss - withoutHedges.expectedLoss, 2, { signed: true }),
      good: withHedges.expectedLoss <= withoutHedges.expectedLoss,
    },
    {
      label: 'Expected Profit',
      gross: fmtMoneyM(withoutHedges.expectedProfit, 2),
      net: fmtMoneyM(withHedges.expectedProfit, 2),
      delta: fmtMoneyM(accretion.deltaExpectedProfitM, 2, { signed: true }),
      good: accretion.deltaExpectedProfitM >= 0,
    },
    {
      label: 'Mean ROC',
      gross: fmtPct(withoutHedges.meanRocPct, 2),
      net: fmtPct(withHedges.meanRocPct, 2),
      delta: fmtPts(accretion.deltaMeanRocPct, 2),
      good: accretion.deltaMeanRocPct >= 0,
    },
    {
      label: 'No-Event ROC',
      gross: fmtPct(withoutHedges.noEventRocPct, 2),
      net: fmtPct(withHedges.noEventRocPct, 2),
      delta: fmtPts(accretion.deltaNoEventRocPct, 2),
      good: accretion.deltaNoEventRocPct >= 0,
    },
    {
      label: 'Break-Even Prob',
      gross: fmtPct(withoutHedges.breakEvenProbPct, 1),
      net: fmtPct(withHedges.breakEvenProbPct, 1),
      delta: fmtPts(accretion.deltaBreakEvenProbPct, 2),
      good: accretion.deltaBreakEvenProbPct >= 0,
    },
    {
      label: '1-in-250 VaR',
      gross: fmtMoneyM(withoutHedges.portfolioVar250, 2),
      net: fmtMoneyM(withHedges.portfolioVar250, 2),
      delta: fmtMoneyM(accretion.deltaVar250M, 2, { signed: true }),
      good: accretion.deltaVar250M <= 0,
    },
    {
      label: '1-in-250 TVaR',
      gross: fmtMoneyM(withoutHedges.portfolioTvar250, 2),
      net: fmtMoneyM(withHedges.portfolioTvar250, 2),
      delta: fmtMoneyM(accretion.deltaTvar250M, 2, { signed: true }),
      good: accretion.deltaTvar250M <= 0,
    },
    {
      label: 'Return on 1-in-250 TVaR',
      gross: fmtPct(accretion.grossReturnOnTvarPct, 2),
      net: fmtPct(accretion.netReturnOnTvarPct, 2),
      delta: fmtPts(accretion.deltaReturnOnTvarPct, 2),
      good: accretion.deltaReturnOnTvarPct >= 0,
    },
  ];

  const rocQuantileRows = [
    { label: 'No-Event ROC', gross: withoutHedges.quantiles?.noEvents ?? withoutHedges.noEventRocPct, net: active.quantiles?.noEvents ?? active.noEventRocPct },
    { label: 'Mean ROC', gross: withoutHedges.quantiles?.mean ?? withoutHedges.meanRocPct, net: active.quantiles?.mean ?? active.meanRocPct },
    { label: '50th% (Median)', gross: withoutHedges.quantiles?.['50.0th'], net: active.quantiles?.['50.0th'] },
    { label: '90th% (1-in-10)', gross: withoutHedges.quantiles?.['90.0th'], net: active.quantiles?.['90.0th'] },
    { label: '95th% (1-in-20)', gross: withoutHedges.quantiles?.['95.0th'], net: active.quantiles?.['95.0th'] },
    { label: '98th% (1-in-50)', gross: withoutHedges.quantiles?.['98.0th'], net: active.quantiles?.['98.0th'] },
    { label: '99th% (1-in-100)', gross: withoutHedges.quantiles?.['99.0th'], net: active.quantiles?.['99.0th'] },
    { label: '99.6th% (1-in-250)', gross: withoutHedges.quantiles?.['99.6th'], net: active.quantiles?.['99.6th'] },
  ];

  return (
    <>
      <div className="tabs" role="tablist" aria-label="Portfolio analysis sections" style={{ marginBottom: 14 }}>
        <button
          type="button"
          role="tab"
          aria-selected={subTab === 'live'}
          className={`tab ${subTab === 'live' ? 'active' : ''}`}
          onClick={() => setSubTab('live')}
        >
          Live Rollup &amp; EP Curves
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={subTab === 'compare'}
          className={`tab ${subTab === 'compare' ? 'active' : ''}`}
          onClick={() => setSubTab('compare')}
        >
          Compare Portfolios (YoY &amp; Plan)
        </button>
      </div>

      {subTab === 'compare' ? (
        <PortfolioTimelineCompare
          defaultCapital={totalCapital}
          defaultView={portfolioView}
          refreshKey={refreshKey}
        />
      ) : (
        <>
          <div className="card">
            <div className="card-title">
              <span>{active.portfolioName}</span>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <div className="seg" role="group" aria-label="Hedge view mode">
                  <button
                    type="button"
                    className={mode === 'compare' ? 'active' : ''}
                    onClick={() => setMode('compare')}
                  >
                    Side-by-Side
                  </button>
                  <button
                    type="button"
                    className={mode === 'with' ? 'active' : ''}
                    onClick={() => setMode('with')}
                  >
                    With Hedges ({withHedges.hedgeCount ?? 0})
                  </button>
                  <button
                    type="button"
                    className={mode === 'without' ? 'active' : ''}
                    onClick={() => setMode('without')}
                  >
                    Without Hedges
                  </button>
                </div>
                <select
                  aria-label="Portfolio view of risk"
                  value={portfolioView}
                  onChange={(e) => onChangeView(e.target.value as ViewOfRisk)}
                >
                  <option value="ADJUSTED">Adjusted view</option>
                  <option value="UNADJUSTED">Unadjusted view</option>
                </select>
                <label style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                  Fund Capital ($M)
                  <input
                    type="number"
                    min={1}
                    value={totalCapital}
                    onChange={(e) => {
                      const val = Number(e.target.value);
                      if (Number.isFinite(val) && val > 0) onChangeCapital(val);
                    }}
                    style={{ width: 84, marginLeft: 6 }}
                  />
                </label>
                <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                  Rollup: {active.computeMs} ms
                </span>
                <a
                  className="btn btn-ghost"
                  style={{ textDecoration: 'none' }}
                  href={apiUrl(
                    `/api/exports/portfolio-csv?view=${portfolioView}&totalCapital=${totalCapital}&includeHedges=${includeHedgesForExport}`,
                  )}
                  download
                >
                  Export CSV
                </a>
                <a
                  className="btn btn-ghost"
                  style={{ textDecoration: 'none' }}
                  href={apiUrl(
                    `/api/exports/portfolio-tearsheet?view=${portfolioView}&totalCapital=${totalCapital}&includeHedges=${includeHedgesForExport}`,
                  )}
                  target="_blank"
                  rel="noreferrer"
                >
                  Tear Sheet
                </a>
              </div>
            </div>
            {/* Hedge Accretion Summary Strip */}
            <div className={`accretion-banner tone-${verdictTone}`} data-testid="hedge-accretion-banner">
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span
                  className={`badge ${
                    !accretion.hasHedges ? '' : accretion.isAccretive ? 'badge-long' : 'badge-hedge-dilutive'
                  }`}
                >
                  {accretion.accretionVerdict}
                </span>
                <span style={{ fontSize: 12.5, color: 'var(--text-strong)', fontWeight: 500 }}>
                  {accretion.accretionReason}
                </span>
              </div>
              {accretion.hasHedges && (
                <div className="accretion-metrics">
                  <div>
                    <span>RoTVaR Δ</span>
                    <b className={accretion.deltaReturnOnTvarPct >= 0 ? 'delta-pos' : 'delta-neg'}>
                      {fmtPts(accretion.deltaReturnOnTvarPct, 2)}
                    </b>
                  </div>
                  <div>
                    <span>TVaR 250 Saved</span>
                    <b className={accretion.tvar250ReductionM >= 0 ? 'delta-pos' : 'delta-neg'}>
                      {fmtMoneyM(accretion.tvar250ReductionM, 2)}
                    </b>
                  </div>
                  <div>
                    <span>Net Hedge Cost</span>
                    <b>{fmtMoneyM(accretion.netHedgeCostM, 2)}</b>
                  </div>
                  <div>
                    <span>Cost / $ TVaR Saved</span>
                    <b>{fmtCents(accretion.costPerDollarTvarSaved, 1)}</b>
                  </div>
                  <div>
                    <span>Mean ROC Δ</span>
                    <b className={accretion.deltaMeanRocPct >= 0 ? 'delta-pos' : 'delta-neg'}>
                      {fmtPts(accretion.deltaMeanRocPct, 2)}
                    </b>
                  </div>
                </div>
              )}
            </div>

            {capitalExceeded && (
              <div className="error-banner" role="alert" data-testid="capital-exceeded-banner">
                <strong>Capital Constraint Violation:</strong> Auto-calculated Deployed Capital (
                <strong>{fmtMoneyM(deployedCapitalM, 2)}</strong>, Σ (Limit − Premium) × Line) exceeds Fund Capital (
                <strong>{fmtMoneyM(totalCapital, 2)}</strong>) by{' '}
                <strong>{fmtMoneyM(Math.abs(capitalHeadroomM), 2)}</strong> ({fmtPct(capitalUtilPct, 1)} utilization).
                Increase Fund Capital or reduce bound lines.
              </div>
            )}

            <div className="grid-kpi">
              <div className="kpi">
                <div className="kpi-label">Mean ROC</div>
                <div className="kpi-val">{fmtPct(active.meanRocPct, 2)}</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Return on 1-in-250 TVaR</div>
                <div className="kpi-val">{fmtPct(active.returnOnTvarPct ?? 0, 2)}</div>
              </div>
              <div
                className="kpi"
                data-testid="deployed-capital-kpi"
                title="Auto-calculated: sum of max(0, (Limit − Deposit Premium) × Signed Line) across assumed layers"
              >
                <div className="kpi-label">Deployed Capital (Auto)</div>
                <div className="kpi-val" style={{ color: capitalExceeded ? 'var(--negative)' : 'var(--text-strong)' }}>
                  {fmtMoneyM(deployedCapitalM, 2)}
                </div>
                <div className="kpi-sub">
                  {fmtPct(capitalUtilPct, 1)} of {fmtMoneyM(totalCapital, 0)} ·{' '}
                  {capitalExceeded
                    ? `Over by ${fmtMoneyM(Math.abs(capitalHeadroomM), 1)}`
                    : `${fmtMoneyM(capitalHeadroomM, 1)} headroom`}
                </div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Break-Even Prob</div>
                <div className="kpi-val">{fmtPct(active.breakEvenProbPct, 1)}</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Expected Profit</div>
                <div className="kpi-val">{fmtMoneyM(active.expectedProfit, 2)}</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">1-in-250 TVaR</div>
                <div className="kpi-val">{fmtMoneyM(active.portfolioTvar250, 2)}</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Co-TVaR Sum Err</div>
                <div className="kpi-val" style={{ color: 'var(--positive)' }}>
                  {active.cotvarSumCheckDiff.toExponential(1)}
                </div>
              </div>
            </div>

            {mode === 'compare' && (
              <div style={{ marginBottom: 14 }} data-testid="hedge-comparison-table">
                <div style={{ fontWeight: 600, marginBottom: 8 }}>
                  With Hedges vs Without Hedges (Long-Only) Comparison
                </div>
                <div className="table-wrap">
                  <table className="dense-table">
                    <thead>
                      <tr>
                        <th>Portfolio Metric</th>
                        <th className="num">Without Hedges (Long Only)</th>
                        <th className="num">With Hedges (Net)</th>
                        <th className="num">Hedge Impact (Δ)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparisonRows.map((r) => (
                        <tr key={r.label}>
                          <td style={{ fontWeight: r.label.includes('TVaR') || r.label === 'Mean ROC' ? 600 : 400 }}>
                            {r.label}
                          </td>
                          <td className="num">{r.gross}</td>
                          <td className="num" style={{ fontWeight: 600 }}>
                            {r.net}
                          </td>
                          <td
                            className={`num ${
                              r.good == null ? '' : r.good ? 'delta-pos' : 'delta-neg'
                            }`}
                            style={{ fontWeight: 600 }}
                          >
                            {r.delta}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          {/* Live Rollup Exceedance Probability (OEP / AEP / TVaR) & Splits */}
          <div className="card" data-testid="live-ep-card">
            <div className="card-title">
              <span>
                Live Rollup EP Curves &amp; Return Period Losses ({active.currency} M)
                <span className="badge" style={{ marginLeft: 8 }}>
                  {mode === 'without' ? 'Long Only' : 'Net of Hedges'}
                </span>
              </span>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <div className="seg" role="group" aria-label="EP split dimension">
                  <button
                    type="button"
                    className={epDim === 'TOTAL' ? 'active' : ''}
                    onClick={() => {
                      setEpDim('TOTAL');
                      setSelectedSliceKey(null);
                    }}
                  >
                    Total Portfolio
                  </button>
                  <button
                    type="button"
                    className={epDim === 'REGION' ? 'active' : ''}
                    onClick={() => {
                      setEpDim('REGION');
                      setSelectedSliceKey(null);
                    }}
                  >
                    By Region ({(active.epByRegion ?? []).length})
                  </button>
                  <button
                    type="button"
                    className={epDim === 'PERIL' ? 'active' : ''}
                    onClick={() => {
                      setEpDim('PERIL');
                      setSelectedSliceKey(null);
                    }}
                  >
                    By Peril ({(active.epByPeril ?? []).length})
                  </button>
                  <button
                    type="button"
                    className={epDim === 'REGION_PERIL' ? 'active' : ''}
                    onClick={() => {
                      setEpDim('REGION_PERIL');
                      setSelectedSliceKey(null);
                    }}
                  >
                    By Region-Peril ({(active.epByRegionPeril ?? []).length})
                  </button>
                </div>

                {epDim !== 'TOTAL' && (
                  <div className="seg" role="group" aria-label="Split EP metric">
                    {(['aep', 'oep', 'aepTvar', 'oepTvar'] as EpMetricKey[]).map((m) => (
                      <button
                        key={m}
                        type="button"
                        className={epMetric === m ? 'active' : ''}
                        onClick={() => setEpMetric(m)}
                      >
                        {EP_METRIC_LABELS[m]}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="grid-2" style={{ alignItems: 'start' }}>
              <div>
                {inspectedSlice && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginBottom: 8,
                      fontSize: 12,
                      color: 'var(--accent-soft)',
                    }}
                  >
                    <span>
                      Inspecting <strong>{inspectedSlice.label}</strong> (OEP · AEP · AEP TVaR)
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost"
                      style={{ height: 24, padding: '0 8px', fontSize: 11 }}
                      onClick={() => setSelectedSliceKey(null)}
                    >
                      Show all splits
                    </button>
                  </div>
                )}
                <PortfolioEpChart
                  epCurve={activeEpCurve}
                  comparisonEpCurve={mode === 'compare' && accretion.hasHedges ? grossEpCurve : null}
                  slices={activeSlices}
                  dimension={epDim}
                  metric={epMetric}
                  selectedSliceKey={selectedSliceKey}
                  primaryLabel={mode === 'without' ? 'Long Only' : 'Net'}
                  comparisonLabel="Without Hedges"
                  currency={active.currency}
                />
              </div>

              <div data-testid="return-period-numbers-table">
                <div style={{ fontWeight: 600, marginBottom: 8 }}>
                  {epDim === 'TOTAL'
                    ? 'Computed Return Period Losses — Total Portfolio'
                    : inspectedSlice
                      ? `Computed Return Period Losses — ${inspectedSlice.label}`
                      : `Return Period ${EP_METRIC_LABELS[epMetric]} by ${
                          epDim === 'REGION' ? 'Region' : epDim === 'PERIL' ? 'Peril' : 'Region-Peril'
                        }`}
                </div>

                {epDim === 'TOTAL' || inspectedSlice ? (
                  <div className="table-wrap">
                    <table className="dense-table">
                      <thead>
                        <tr>
                          <th>Return Period</th>
                          <th className="num">Prob</th>
                          <th className="num">OEP</th>
                          <th className="num">OEP TVaR</th>
                          <th className="num">AEP</th>
                          <th className="num">AEP TVaR</th>
                          {epDim === 'TOTAL' && mode === 'compare' && accretion.hasHedges && (
                            <th className="num">AEP TVaR Δ</th>
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {(inspectedSlice ? inspectedSlice.epPoints : activeEpCurve).map((pt) => {
                          const grossPt = epDim === 'TOTAL' ? epPointAt(grossEpCurve, pt.returnPeriod) : undefined;
                          const deltaTvar = grossPt ? pt.aepTvar - grossPt.aepTvar : 0;
                          return (
                            <tr key={pt.returnPeriod}>
                              <td style={{ fontWeight: pt.returnPeriod === 100 || pt.returnPeriod === 250 ? 600 : 400 }}>
                                1-in-{Math.round(pt.returnPeriod)}
                              </td>
                              <td className="num" style={{ color: 'var(--text-muted)' }}>
                                {(pt.probability * 100).toFixed(pt.returnPeriod >= 1000 ? 1 : pt.returnPeriod >= 250 ? 1 : 0)}%
                              </td>
                              <td className="num">{fmtMoneyM(pt.oep, 2)}</td>
                              <td className="num">{fmtMoneyM(pt.oepTvar, 2)}</td>
                              <td className="num" style={{ fontWeight: 600 }}>
                                {fmtMoneyM(pt.aep, 2)}
                              </td>
                              <td className="num" style={{ fontWeight: 600, color: 'var(--accent-soft)' }}>
                                {fmtMoneyM(pt.aepTvar, 2)}
                              </td>
                              {epDim === 'TOTAL' && mode === 'compare' && accretion.hasHedges && (
                                <td
                                  className={`num ${deltaTvar <= 0 ? 'delta-pos' : 'delta-neg'}`}
                                  style={{ fontWeight: 600 }}
                                >
                                  {fmtMoneyM(deltaTvar, 2, { signed: true })}
                                </td>
                              )}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                    Click any row in the split table below to inspect its full OEP · AEP · TVaR curve.
                  </div>
                )}
              </div>
            </div>

            {epDim !== 'TOTAL' && (
              <div style={{ marginTop: 14 }} data-testid="ep-splits-table">
                <div style={{ fontWeight: 600, marginBottom: 8 }}>
                  {epDim === 'REGION' ? 'Region' : epDim === 'PERIL' ? 'Peril' : 'Region-Peril'} Return Period Table ({EP_METRIC_LABELS[epMetric]}, {active.currency} M)
                </div>
                <div className="table-wrap">
                  <table className="dense-table">
                    <thead>
                      <tr>
                        <th>{epDim === 'REGION' ? 'Region' : epDim === 'PERIL' ? 'Peril' : 'Region-Peril'}</th>
                        <th className="num">AAL</th>
                        <th className="num">SD</th>
                        {RETURN_PERIOD_ORDER.map((rp) => (
                          <th key={rp} className="num">
                            1-in-{rp}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {activeSlices.length === 0 && (
                        <tr>
                          <td colSpan={3 + RETURN_PERIOD_ORDER.length} className="empty-cell">
                            No active losses in this split.
                          </td>
                        </tr>
                      )}
                      {activeSlices.map((slice) => {
                        const isSelected = selectedSliceKey === slice.key;
                        return (
                          <tr
                            key={slice.key}
                            onClick={() => setSelectedSliceKey(isSelected ? null : slice.key)}
                            style={{
                              cursor: 'pointer',
                              background: isSelected ? 'var(--accent-bg)' : undefined,
                            }}
                            title="Click to inspect OEP, AEP, and TVaR for this split"
                          >
                            <td style={{ fontWeight: 600 }}>{slice.label}</td>
                            <td className="num">{fmtMoneyM(slice.aal, 2)}</td>
                            <td className="num" style={{ color: 'var(--text-muted)' }}>
                              {fmtMoneyM(slice.sd, 2)}
                            </td>
                            {RETURN_PERIOD_ORDER.map((rp) => {
                              const pt = epPointAt(slice.epPoints, rp);
                              const val = pt ? pt[epMetric] : 0;
                              return (
                                <td
                                  key={rp}
                                  className="num"
                                  style={{ fontWeight: rp === 100 || rp === 250 ? 600 : 400 }}
                                >
                                  {fmtMoneyM(val, 2)}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
          {/* ROC Distribution & Return Period ROC Quantiles */}
          <div className="card">
            <div className="card-title">
              <span>Fund Return on Capital (ROC) Distribution &amp; Return Period Quantiles</span>
            </div>
            <div className="grid-2">
              <div>
                <RocCdfChart
                  data={active}
                  comparisonData={mode === 'without' || !accretion.hasHedges ? null : withoutHedges}
                  primaryLabel={mode === 'without' ? `Long Only (${active.view})` : `With Hedges (${active.view})`}
                  comparisonLabel={`Without Hedges (${withoutHedges.view})`}
                />
              </div>
              <div data-testid="return-period-roc-table">
                <div style={{ fontWeight: 600, marginBottom: 8 }}>Computed ROC Distribution Quantiles</div>
                <div className="table-wrap">
                  <table className="dense-table">
                    <thead>
                      <tr>
                        <th>Percentile / Return Period</th>
                        {mode === 'compare' ? (
                          <>
                            <th className="num">Without Hedges</th>
                            <th className="num">With Hedges</th>
                            <th className="num">Hedge Δ</th>
                          </>
                        ) : (
                          <th className="num">Computed ROC</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {rocQuantileRows.map((row) => {
                        const delta =
                          row.net != null && row.gross != null ? row.net - row.gross : null;
                        return (
                          <tr key={row.label}>
                            <td style={{ fontWeight: row.label.includes('1-in-100') || row.label.includes('1-in-250') || row.label === 'Mean ROC' ? 600 : 400 }}>
                              {row.label}
                            </td>
                            {mode === 'compare' ? (
                              <>
                                <td className="num">{fmtPct(row.gross, 2)}</td>
                                <td className="num" style={{ fontWeight: 600 }}>
                                  {fmtPct(row.net, 2)}
                                </td>
                                <td
                                  className={`num ${delta == null ? '' : delta >= 0 ? 'delta-pos' : 'delta-neg'}`}
                                  style={{ fontWeight: 600 }}
                                >
                                  {delta != null ? fmtPts(delta, 2) : '—'}
                                </td>
                              </>
                            ) : (
                              <td className="num" style={{ fontWeight: 600 }}>
                                {fmtPct(row.net, 2)}
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-title">
              <span>
                Euler Capital Allocation · Co-TVaR 1-in-250{' '}
                <span className="badge" style={{ marginLeft: 4 }}>
                  {mode === 'without' ? 'Long Only' : 'Net of Hedges'}
                </span>
              </span>
            </div>
            <div className="table-wrap">
              <table className="dense-table">
                <thead>
                  <tr>
                    <th>Cedent</th>
                    <th>Layer</th>
                    <th>Dir</th>
                    <th className="num" title="Limit ($M)">
                      Lim
                    </th>
                    <th className="num" title="Signed Premium (+ received / − hedge cost)">
                      Prem
                    </th>
                    <th className="num" title="Expected loss (+ assumed / − hedge recovery)">
                      EL
                    </th>
                    <th className="num" title="Expected reinstatement premium">
                      RIP
                    </th>
                    <th className="num" title="Standalone 1-in-250 TVaR">
                      TVaR
                    </th>
                    <th className="num" title="Euler co-TVaR at 1-in-250 (negative = hedge tail relief)">
                      Euler
                    </th>
                    <th className="num" title="Diversification credit / residual basis">
                      Div
                    </th>
                    <th className="num" title="Marginal return on capital (or hedge cost % of TVaR saved)">
                      mROC
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {active.layerAllocations.length === 0 && (
                    <tr>
                      <td colSpan={11} className="empty-cell">
                        No bound layers in this view.
                      </td>
                    </tr>
                  )}
                  {active.layerAllocations.map((row) => (
                    <tr key={`${row.programmeId}-${row.layerId}`}>
                      <td>{row.cedentName}</td>
                      <td>{row.layerName}</td>
                      <td>
                        <span className={`badge ${row.isHedge ? 'badge-hedge' : 'badge-long'}`}>
                          {row.isHedge ? 'Hedge' : 'Long'}
                        </span>
                      </td>
                      <td className="num">{fmtMoneyM(row.limit, 1)}</td>
                      <td className="num">{fmtMoneyM(row.premium, 2)}</td>
                      <td className="num">{fmtMoneyM(row.el, 2)}</td>
                      <td className="num">{fmtMoneyM(row.expectedRip, 2)}</td>
                      <td className="num">{fmtMoneyM(row.standaloneTvar250, 2)}</td>
                      <td
                        className={`num ${row.isHedge && row.eulerCotvar250 < 0 ? 'delta-pos' : ''}`}
                        style={{ fontWeight: 600 }}
                      >
                        {fmtMoneyM(row.eulerCotvar250, 2)}
                      </td>
                      <td className="num">{fmtPct(row.diversificationCreditPct, 1)}</td>
                      <td className="num" style={{ color: 'var(--accent)', fontWeight: 600 }}>
                        {fmtPct(row.marginalRocPct, 1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </>
  );
}

export function PortfolioReturns({
  portfolioView,
  onChangeView,
  totalCapital,
  onChangeCapital,
  refreshKey,
}: {
  portfolioView: ViewOfRisk;
  onChangeView: (v: ViewOfRisk) => void;
  totalCapital: number;
  onChangeCapital: (cap: number) => void;
  refreshKey?: number | string;
}) {
  const compQ = useQuery({
    queryKey: ['portfolio', portfolioView, totalCapital, refreshKey],
    queryFn: () => api.getPortfolioComparison(portfolioView, totalCapital),
    placeholderData: keepPreviousData,
  });

  if (compQ.isError) {
    return <div className="error-banner">{String((compQ.error as Error).message)}</div>;
  }
  if (!compQ.data) {
    return (
      <div className="card" style={{ color: 'var(--text-muted)' }}>
        Evaluating portfolio rollup (with &amp; without hedges)…
      </div>
    );
  }
  return (
    <PortfolioReturnsView
      data={compQ.data}
      portfolioView={portfolioView}
      onChangeView={onChangeView}
      totalCapital={totalCapital}
      onChangeCapital={onChangeCapital}
      refreshKey={refreshKey}
    />
  );
}
