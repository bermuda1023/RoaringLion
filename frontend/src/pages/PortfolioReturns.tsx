import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, apiUrl } from '../api/client';
import { BreadcrumbBar } from '../components/BreadcrumbBar';
import { RocCdfChart } from '../components/RocCdfChart';
import type { PortfolioHedgeComparisonResponse, PortfolioRollupResponse, ViewOfRisk } from '../types/contracts';
import { fmtCents, fmtMoneyM, fmtPct, fmtPts } from '../utils/format';

export type HedgeDisplayMode = 'with' | 'without' | 'compare';

export function PortfolioReturnsView({
  data,
  portfolioView,
  onChangeView,
  totalCapital,
  onChangeCapital,
}: {
  data: PortfolioHedgeComparisonResponse;
  portfolioView: ViewOfRisk;
  onChangeView: (v: ViewOfRisk) => void;
  totalCapital: number;
  onChangeCapital: (cap: number) => void;
}) {
  const [mode, setMode] = useState<HedgeDisplayMode>('compare');
  const { withHedges, withoutHedges, accretion } = data;
  const active: PortfolioRollupResponse = mode === 'without' ? withoutHedges : withHedges;
  const includeHedgesForExport = mode !== 'without';
  const deployedCapitalM = active.deployedCollateral;
  const capitalUtilPct =
    active.capitalUtilizationPct ?? (totalCapital > 0 ? (deployedCapitalM / totalCapital) * 100 : 0);
  const capitalHeadroomM = active.capitalHeadroomM ?? totalCapital - deployedCapitalM;
  const capitalExceeded = active.capitalExceeded ?? deployedCapitalM > totalCapital + 1e-6;

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

  return (
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
          <div className="kpi" data-testid="deployed-capital-kpi" title="Auto-calculated: sum of max(0, (Limit − Deposit Premium) × Signed Line) across assumed layers">
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

        <div className="grid-2">
          <div>
            <RocCdfChart
              data={active}
              comparisonData={mode === 'without' || !accretion.hasHedges ? null : withoutHedges}
              primaryLabel={mode === 'without' ? `Long Only (${active.view})` : `With Hedges (${active.view})`}
              comparisonLabel={`Without Hedges (${withoutHedges.view})`}
            />
          </div>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>Risk Appetite Guidelines</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Percentile</th>
                    <th className="num">Limit</th>
                    {mode === 'compare' ? (
                      <>
                        <th className="num">Without Hedges</th>
                        <th className="num">With Hedges</th>
                      </>
                    ) : (
                      <th className="num">Actual</th>
                    )}
                    <th className="num">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {active.guidelines.map((g, idx) => {
                    const grossG = withoutHedges.guidelines[idx];
                    return (
                      <tr key={g.label}>
                        <td>{g.label}</td>
                        <td className="num">{fmtPct(g.thresholdRocPct, 1)}</td>
                        {mode === 'compare' ? (
                          <>
                            <td className="num">{fmtPct(grossG?.actualRocPct, 2)}</td>
                            <td className="num" style={{ fontWeight: 600 }}>
                              {fmtPct(g.actualRocPct, 2)}
                            </td>
                          </>
                        ) : (
                          <td className="num">{fmtPct(g.actualRocPct, 2)}</td>
                        )}
                        <td className={`num ${g.passed ? 'pass' : 'fail'}`}>
                          {g.passed ? 'Pass' : 'Breach'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          <BreadcrumbBar manifest={active.manifest} />
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
    />
  );
}
