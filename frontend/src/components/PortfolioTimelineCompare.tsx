import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type {
  EpSplitDeltaRow,
  PortfolioCompareContractMeta,
  PortfolioCompareOptionsResponse,
  PortfolioComparePreset,
  PortfolioCompareResponse,
  PortfolioSliceSpec,
  ViewOfRisk,
} from '../types/contracts';
import { fmtMoneyM, fmtPct, fmtPts } from '../utils/format';
import { type CompareCurveMode, PortfolioCompareEpChart } from './PortfolioEpChart';

type SplitDeltaDim = 'REGION' | 'PERIL' | 'REGION_PERIL';

interface EditableSlice {
  label: string;
  view: ViewOfRisk;
  totalCapital: number;
  includeHedges: boolean;
  treatyYears: number[];
  statuses: string[];
  inceptionFrom: string;
  inceptionTo: string;
  programmeIds: string[] | null;
}

function fromPresetSlice(spec: PortfolioSliceSpec, fallbackCapital: number, fallbackView: ViewOfRisk): EditableSlice {
  return {
    label: spec.label,
    view: spec.view ?? fallbackView,
    totalCapital: spec.totalCapital ?? fallbackCapital,
    includeHedges: spec.includeHedges ?? true,
    treatyYears: spec.treatyYears ? [...spec.treatyYears] : [],
    statuses: spec.statuses ? [...spec.statuses] : [],
    inceptionFrom: spec.inceptionFrom ?? '',
    inceptionTo: spec.inceptionTo ?? '',
    programmeIds: spec.programmeIds ? [...spec.programmeIds] : null,
  };
}

function toApiSpec(s: EditableSlice): PortfolioSliceSpec {
  return {
    label: s.label.trim() || 'Portfolio',
    view: s.view,
    totalCapital: s.totalCapital,
    includeHedges: s.includeHedges,
    treatyYears: s.treatyYears.length > 0 ? s.treatyYears : null,
    statuses: s.statuses.length > 0 ? s.statuses : null,
    inceptionFrom: s.inceptionFrom.trim() || null,
    inceptionTo: s.inceptionTo.trim() || null,
    programmeIds: s.programmeIds && s.programmeIds.length > 0 ? s.programmeIds : null,
  };
}

function countMatchingContracts(slice: EditableSlice, contracts: PortfolioCompareContractMeta[]): number {
  const from = slice.inceptionFrom.trim();
  const to = slice.inceptionTo.trim();
  return contracts.filter((c) => {
    if (!slice.includeHedges && c.isHedge) return false;
    if (slice.programmeIds && slice.programmeIds.length > 0 && !slice.programmeIds.includes(c.programmeId)) {
      return false;
    }
    if (slice.treatyYears.length > 0 && !slice.treatyYears.includes(c.treatyYear)) return false;
    if (slice.statuses.length > 0 && !slice.statuses.includes(c.status)) return false;
    if (from && c.inceptionDate < from) return false;
    if (to && c.inceptionDate > to) return false;
    return true;
  }).length;
}

function SliceBuilderCard({
  title,
  accentColor,
  slice,
  onChange,
  options,
  matchedCount,
  testId,
}: {
  title: string;
  accentColor: string;
  slice: EditableSlice;
  onChange: (next: EditableSlice) => void;
  options: PortfolioCompareOptionsResponse;
  matchedCount: number;
  testId: string;
}) {
  const toggleYear = (yr: number) => {
    const exists = slice.treatyYears.includes(yr);
    const nextYears = exists ? slice.treatyYears.filter((y) => y !== yr) : [...slice.treatyYears, yr];
    onChange({ ...slice, treatyYears: nextYears, programmeIds: null });
  };

  const toggleStatus = (st: string) => {
    const exists = slice.statuses.includes(st);
    const nextStatuses = exists ? slice.statuses.filter((x) => x !== st) : [...slice.statuses, st];
    onChange({ ...slice, statuses: nextStatuses, programmeIds: null });
  };

  return (
    <div className="panel" data-testid={testId} style={{ borderTop: `2px solid ${accentColor}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10 }}>
        <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-strong)' }}>{title}</span>
        <span
          className={`badge ${matchedCount === 0 ? 'status-declined' : 'status-bound'}`}
          data-testid={`${testId}-count`}
        >
          {matchedCount} {matchedCount === 1 ? 'programme' : 'programmes'}
        </span>
      </div>

      <div style={{ display: 'grid', gap: 10 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 8, alignItems: 'end' }}>
          <label style={{ fontSize: 11.5, display: 'flex', flexDirection: 'column', gap: 4 }}>
            Portfolio Label
            <input
              type="text"
              aria-label={`${title} label`}
              value={slice.label}
              onChange={(e) => onChange({ ...slice, label: e.target.value })}
              style={{ height: 30, fontSize: 12.5 }}
            />
          </label>
          <label style={{ fontSize: 11.5, display: 'flex', flexDirection: 'column', gap: 4 }}>
            Hedge Basis
            <select
              aria-label={`${title} hedge basis`}
              value={slice.includeHedges ? 'net' : 'gross'}
              onChange={(e) => onChange({ ...slice, includeHedges: e.target.value === 'net' })}
              style={{ height: 30, fontSize: 12 }}
            >
              <option value="net">Net (With Hedges)</option>
              <option value="gross">Gross (Long Only)</option>
            </select>
          </label>
          <label style={{ fontSize: 11.5, display: 'flex', flexDirection: 'column', gap: 4 }}>
            View
            <select
              aria-label={`${title} view`}
              value={slice.view}
              onChange={(e) => onChange({ ...slice, view: e.target.value as ViewOfRisk })}
              style={{ height: 30, fontSize: 12 }}
            >
              <option value="ADJUSTED">Adjusted</option>
              <option value="UNADJUSTED">Unadjusted</option>
            </select>
          </label>
        </div>

        <div>
          <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 5 }}>Underwriting Year</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            <button
              type="button"
              className={`status-option ${slice.treatyYears.length === 0 ? 'on' : ''}`}
              onClick={() => onChange({ ...slice, treatyYears: [], programmeIds: null })}
              style={{ fontSize: 11.5, padding: '3px 9px' }}
            >
              All Years
            </button>
            {options.treatyYears.map((yr) => {
              const active = slice.treatyYears.includes(yr);
              return (
                <button
                  key={yr}
                  type="button"
                  className={`status-option ${active ? 'on' : ''}`}
                  onClick={() => toggleYear(yr)}
                  style={{ fontSize: 11.5, padding: '3px 9px' }}
                >
                  {yr}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 5 }}>Contract Statuses</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            <button
              type="button"
              className={`status-option ${slice.statuses.length === 0 ? 'on' : ''}`}
              onClick={() => onChange({ ...slice, statuses: [], programmeIds: null })}
              style={{ fontSize: 11, padding: '3px 8px' }}
            >
              All
            </button>
            {options.statuses.map(({ status, count }) => {
              const active = slice.statuses.includes(status);
              return (
                <button
                  key={status}
                  type="button"
                  className={`status-option ${active ? 'on' : ''}`}
                  onClick={() => toggleStatus(status)}
                  style={{ fontSize: 11, padding: '3px 8px' }}
                >
                  <span className={`badge status-${status.toLowerCase()}`} style={{ height: 18, fontSize: 10 }}>
                    {status}
                  </span>
                  <span className="status-option-count">{count}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <label style={{ fontSize: 11.5, display: 'flex', flexDirection: 'column', gap: 4 }}>
            Inception From
            <input
              type="date"
              aria-label={`${title} inception from`}
              value={slice.inceptionFrom}
              onChange={(e) => onChange({ ...slice, inceptionFrom: e.target.value, programmeIds: null })}
              style={{ height: 30, fontSize: 12 }}
            />
          </label>
          <label style={{ fontSize: 11.5, display: 'flex', flexDirection: 'column', gap: 4 }}>
            Inception To
            <input
              type="date"
              aria-label={`${title} inception to`}
              value={slice.inceptionTo}
              onChange={(e) => onChange({ ...slice, inceptionTo: e.target.value, programmeIds: null })}
              style={{ height: 30, fontSize: 12 }}
            />
          </label>
        </div>

        {slice.programmeIds && slice.programmeIds.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 11.5, color: 'var(--accent-soft)' }}>
            <span>Scoped to {slice.programmeIds.length} matched renewal programmes</span>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ height: 24, padding: '0 8px', fontSize: 11 }}
              onClick={() => onChange({ ...slice, programmeIds: null })}
            >
              Clear scope
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function PortfolioTimelineCompareView({
  options,
  result,
  isPending,
  error,
  defaultCapital,
  defaultView,
  onRunCompare,
}: {
  options: PortfolioCompareOptionsResponse;
  result: PortfolioCompareResponse | null;
  isPending: boolean;
  error: string | null;
  defaultCapital: number;
  defaultView: ViewOfRisk;
  onRunCompare: (req: { portfolioA: PortfolioSliceSpec; portfolioB: PortfolioSliceSpec }) => void;
}) {
  const firstPreset = options.presets[0];
  const [activePresetId, setActivePresetId] = useState<string>(firstPreset?.id ?? 'custom');
  const [sliceA, setSliceA] = useState<EditableSlice>(() =>
    firstPreset
      ? fromPresetSlice(firstPreset.portfolioA, defaultCapital, defaultView)
      : {
          label: 'Portfolio A',
          view: defaultView,
          totalCapital: defaultCapital,
          includeHedges: true,
          treatyYears: options.treatyYears.slice(0, 1),
          statuses: ['BOUND'],
          inceptionFrom: '',
          inceptionTo: '',
          programmeIds: null,
        },
  );
  const [sliceB, setSliceB] = useState<EditableSlice>(() =>
    firstPreset
      ? fromPresetSlice(firstPreset.portfolioB, defaultCapital, defaultView)
      : {
          label: 'Portfolio B',
          view: defaultView,
          totalCapital: defaultCapital,
          includeHedges: true,
          treatyYears: options.treatyYears.slice(1, 2),
          statuses: [],
          inceptionFrom: '',
          inceptionTo: '',
          programmeIds: null,
        },
  );
  const [curveMode, setCurveMode] = useState<CompareCurveMode>('oep_aep');
  const [splitDim, setSplitDim] = useState<SplitDeltaDim>('REGION');

  const countA = useMemo(() => countMatchingContracts(sliceA, options.contracts), [sliceA, options.contracts]);
  const countB = useMemo(() => countMatchingContracts(sliceB, options.contracts), [sliceB, options.contracts]);

  const applyPreset = (preset: PortfolioComparePreset) => {
    const nextA = fromPresetSlice(preset.portfolioA, defaultCapital, defaultView);
    const nextB = fromPresetSlice(preset.portfolioB, defaultCapital, defaultView);
    setActivePresetId(preset.id);
    setSliceA(nextA);
    setSliceB(nextB);
    onRunCompare({ portfolioA: toApiSpec(nextA), portfolioB: toApiSpec(nextB) });
  };

  const handleSwap = () => {
    setActivePresetId('custom');
    const nextA = sliceB;
    const nextB = sliceA;
    setSliceA(nextA);
    setSliceB(nextB);
    if (result && countB > 0 && countA > 0) {
      onRunCompare({
        portfolioA: toApiSpec({ ...nextA, totalCapital: defaultCapital }),
        portfolioB: toApiSpec({ ...nextB, totalCapital: defaultCapital }),
      });
    }
  };

  const handleRun = () => {
    onRunCompare({
      portfolioA: toApiSpec({ ...sliceA, totalCapital: defaultCapital }),
      portfolioB: toApiSpec({ ...sliceB, totalCapital: defaultCapital }),
    });
  };

  const splitRows: EpSplitDeltaRow[] = useMemo(() => {
    if (!result) return [];
    if (splitDim === 'REGION') return result.regionDeltas;
    if (splitDim === 'PERIL') return result.perilDeltas;
    return result.regionPerilDeltas;
  }, [result, splitDim]);

  const kpiRows = useMemo(() => {
    if (!result) return [];
    const { portfolioA: a, portfolioB: b, delta: d, epDeltas } = result;
    const ep100 = epDeltas.find((p) => Math.abs(p.returnPeriod - 100) < 1e-6);
    const ep250 = epDeltas.find((p) => Math.abs(p.returnPeriod - 250) < 1e-6);
    return [
      {
        label: 'Programmes / Layers',
        a: `${result.programmeCountA} / ${d.layerCountA}`,
        b: `${result.programmeCountB} / ${d.layerCountB}`,
        delta: `${result.programmeCountA - result.programmeCountB >= 0 ? '+' : ''}${result.programmeCountA - result.programmeCountB} progs`,
        good: null as boolean | null,
      },
      {
        label: 'Net Premium',
        a: fmtMoneyM(a.netPremium ?? a.grossPremium, 2),
        b: fmtMoneyM(b.netPremium ?? b.grossPremium, 2),
        delta: fmtMoneyM(d.deltaNetPremiumM, 2, { signed: true }),
        good: d.deltaNetPremiumM >= 0,
      },
      {
        label: 'Expected Loss (AAL)',
        a: fmtMoneyM(a.expectedLoss, 2),
        b: fmtMoneyM(b.expectedLoss, 2),
        delta: fmtMoneyM(d.deltaExpectedLossM, 2, { signed: true }),
        good: d.deltaExpectedLossM <= 0,
      },
      {
        label: 'Expected Net Profit',
        a: fmtMoneyM(a.expectedProfit, 2),
        b: fmtMoneyM(b.expectedProfit, 2),
        delta: fmtMoneyM(d.deltaExpectedProfitM, 2, { signed: true }),
        good: d.deltaExpectedProfitM >= 0,
      },
      {
        label: 'Mean ROC',
        a: fmtPct(a.meanRocPct, 2),
        b: fmtPct(b.meanRocPct, 2),
        delta: fmtPts(d.deltaMeanRocPct, 2),
        good: d.deltaMeanRocPct >= 0,
      },
      {
        label: 'Return on 1-in-250 TVaR',
        a: fmtPct(a.returnOnTvarPct ?? 0, 2),
        b: fmtPct(b.returnOnTvarPct ?? 0, 2),
        delta: fmtPts(d.deltaReturnOnTvarPct, 2),
        good: d.deltaReturnOnTvarPct >= 0,
      },
      {
        label: '1-in-100 OEP',
        a: fmtMoneyM(ep100?.oepA ?? 0, 2),
        b: fmtMoneyM(ep100?.oepB ?? 0, 2),
        delta: fmtMoneyM(d.deltaOep100M, 2, { signed: true }),
        good: d.deltaOep100M <= 0,
      },
      {
        label: '1-in-100 AEP',
        a: fmtMoneyM(ep100?.aepA ?? 0, 2),
        b: fmtMoneyM(ep100?.aepB ?? 0, 2),
        delta: fmtMoneyM(d.deltaAep100M, 2, { signed: true }),
        good: d.deltaAep100M <= 0,
      },
      {
        label: '1-in-250 OEP',
        a: fmtMoneyM(ep250?.oepA ?? 0, 2),
        b: fmtMoneyM(ep250?.oepB ?? 0, 2),
        delta: fmtMoneyM(d.deltaOep250M, 2, { signed: true }),
        good: d.deltaOep250M <= 0,
      },
      {
        label: '1-in-250 AEP (VaR)',
        a: fmtMoneyM(a.portfolioVar250, 2),
        b: fmtMoneyM(b.portfolioVar250, 2),
        delta: fmtMoneyM(d.deltaVar250M, 2, { signed: true }),
        good: d.deltaVar250M <= 0,
      },
      {
        label: '1-in-250 AEP TVaR',
        a: fmtMoneyM(a.portfolioTvar250, 2),
        b: fmtMoneyM(b.portfolioTvar250, 2),
        delta: fmtMoneyM(d.deltaTvar250M, 2, { signed: true }),
        good: d.deltaTvar250M <= 0,
      },
    ];
  }, [result]);

  return (
    <div data-testid="portfolio-timeline-compare">
      <div className="card">
        <div className="card-title">
          <span>Compare Portfolios · Timelines, Plan vs Current &amp; Custom Bases</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-ghost" onClick={handleSwap}>
              Swap A ↔ B
            </button>
            <button
              type="button"
              className="btn"
              onClick={handleRun}
              disabled={isPending || countA === 0 || countB === 0}
            >
              {isPending ? 'Comparing…' : 'Compare A vs B'}
            </button>
          </div>
        </div>

        {/* Presets Bar */}
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 6 }}>Quick Timeline &amp; Basis Presets</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }} data-testid="compare-presets">
            {options.presets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={`tab ${activePresetId === preset.id ? 'active' : ''}`}
                style={{
                  border: '1px solid var(--border-strong)',
                  background: activePresetId === preset.id ? 'var(--accent-bg)' : 'var(--surface-2)',
                }}
                title={preset.description}
                onClick={() => applyPreset(preset)}
              >
                {preset.name}
              </button>
            ))}
          </div>
        </div>

        {/* Flexible A vs B Selectors */}
        <div className="grid-2">
          <SliceBuilderCard
            title="Portfolio A (Target / Current)"
            accentColor="#e8a84a"
            slice={sliceA}
            onChange={(next) => {
              setActivePresetId('custom');
              setSliceA(next);
            }}
            options={options}
            matchedCount={countA}
            testId="slice-builder-a"
          />
          <SliceBuilderCard
            title="Portfolio B (Baseline / Prior)"
            accentColor="#6e9cd4"
            slice={sliceB}
            onChange={(next) => {
              setActivePresetId('custom');
              setSliceB(next);
            }}
            options={options}
            matchedCount={countB}
            testId="slice-builder-b"
          />
        </div>

        {error && (
          <div className="error-banner" style={{ marginTop: 12, marginBottom: 0 }}>
            {error}
          </div>
        )}
      </div>

      {isPending && !result && (
        <div className="card" style={{ color: 'var(--text-muted)' }}>
          Running live portfolio-to-portfolio comparison…
        </div>
      )}

      {result && (
        <>
          {/* KPI Side-by-Side & Deltas */}
          <div className="card" data-testid="compare-kpi-table">
            <div className="card-title">
              <span>
                Side-by-Side KPIs &amp; Deltas · {result.portfolioA.portfolioName} vs {result.portfolioB.portfolioName}
              </span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                Rollup A: {result.portfolioA.computeMs} ms · Rollup B: {result.portfolioB.computeMs} ms
              </span>
            </div>
            <div className="table-wrap">
              <table className="dense-table">
                <thead>
                  <tr>
                    <th>Portfolio Metric</th>
                    <th className="num" style={{ color: '#f0c57a' }}>
                      A · {result.portfolioA.portfolioName}
                    </th>
                    <th className="num" style={{ color: '#9abce6' }}>
                      B · {result.portfolioB.portfolioName}
                    </th>
                    <th className="num">Δ (A − B)</th>
                  </tr>
                </thead>
                <tbody>
                  {kpiRows.map((row) => (
                    <tr key={row.label}>
                      <td style={{ fontWeight: row.label.includes('TVaR') || row.label === 'Expected Net Profit' ? 600 : 400 }}>
                        {row.label}
                      </td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {row.a}
                      </td>
                      <td className="num">{row.b}</td>
                      <td
                        className={`num ${row.good == null ? '' : row.good ? 'delta-pos' : 'delta-neg'}`}
                        style={{ fontWeight: 600 }}
                      >
                        {row.delta}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Overlaid EP Curves + Return Period Table */}
          <div className="card" data-testid="compare-ep-section">
            <div className="card-title">
              <span>Overlaid Exceedance Probability Curves (Portfolio A vs Portfolio B)</span>
              <div className="seg" role="group" aria-label="Comparison EP curve mode">
                <button
                  type="button"
                  className={curveMode === 'oep_aep' ? 'active' : ''}
                  onClick={() => setCurveMode('oep_aep')}
                >
                  OEP &amp; AEP
                </button>
                <button
                  type="button"
                  className={curveMode === 'aep_tvar' ? 'active' : ''}
                  onClick={() => setCurveMode('aep_tvar')}
                >
                  AEP &amp; TVaR
                </button>
                <button
                  type="button"
                  className={curveMode === 'oep_tvar' ? 'active' : ''}
                  onClick={() => setCurveMode('oep_tvar')}
                >
                  OEP &amp; TVaR
                </button>
              </div>
            </div>

            <div style={{ marginBottom: 14 }}>
              <PortfolioCompareEpChart
                epDeltas={result.epDeltas}
                labelA={result.portfolioA.portfolioName}
                labelB={result.portfolioB.portfolioName}
                curveMode={curveMode}
                currency={result.currency ?? result.portfolioA.currency}
              />
            </div>

            <div className="table-wrap">
              <table className="dense-table">
                <thead>
                  <tr>
                    <th>Return Period</th>
                    <th className="num">OEP A</th>
                    <th className="num">OEP B</th>
                    <th className="num">OEP Δ</th>
                    <th className="num">AEP A</th>
                    <th className="num">AEP B</th>
                    <th className="num">AEP Δ</th>
                    <th className="num">TVaR A</th>
                    <th className="num">TVaR B</th>
                    <th className="num">TVaR Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {result.epDeltas.map((pt) => (
                    <tr key={pt.returnPeriod}>
                      <td style={{ fontWeight: pt.returnPeriod === 100 || pt.returnPeriod === 250 ? 600 : 400 }}>
                        1-in-{Math.round(pt.returnPeriod)}
                      </td>
                      <td className="num">{fmtMoneyM(pt.oepA, 2)}</td>
                      <td className="num">{fmtMoneyM(pt.oepB, 2)}</td>
                      <td className={`num ${pt.deltaOep <= 0 ? 'delta-pos' : 'delta-neg'}`}>
                        {fmtMoneyM(pt.deltaOep, 2, { signed: true })}
                      </td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {fmtMoneyM(pt.aepA, 2)}
                      </td>
                      <td className="num">{fmtMoneyM(pt.aepB, 2)}</td>
                      <td className={`num ${pt.deltaAep <= 0 ? 'delta-pos' : 'delta-neg'}`}>
                        {fmtMoneyM(pt.deltaAep, 2, { signed: true })}
                      </td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {fmtMoneyM(pt.aepTvarA, 2)}
                      </td>
                      <td className="num">{fmtMoneyM(pt.aepTvarB, 2)}</td>
                      <td className={`num ${pt.deltaAepTvar <= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontWeight: 600 }}>
                        {fmtMoneyM(pt.deltaAepTvar, 2, { signed: true })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Region / Peril / Region-Peril Delta Splits */}
          <div className="card" data-testid="compare-splits-table">
            <div className="card-title">
              <span>Exposure &amp; Tail Risk Deltas by Split (A − B)</span>
              <div className="seg" role="group" aria-label="Comparison split dimension">
                <button
                  type="button"
                  className={splitDim === 'REGION' ? 'active' : ''}
                  onClick={() => setSplitDim('REGION')}
                >
                  By Region ({result.regionDeltas.length})
                </button>
                <button
                  type="button"
                  className={splitDim === 'PERIL' ? 'active' : ''}
                  onClick={() => setSplitDim('PERIL')}
                >
                  By Peril ({result.perilDeltas.length})
                </button>
                <button
                  type="button"
                  className={splitDim === 'REGION_PERIL' ? 'active' : ''}
                  onClick={() => setSplitDim('REGION_PERIL')}
                >
                  By Region-Peril ({result.regionPerilDeltas.length})
                </button>
              </div>
            </div>
            <div className="table-wrap">
              <table className="dense-table">
                <thead>
                  <tr>
                    <th>{splitDim === 'REGION' ? 'Region' : splitDim === 'PERIL' ? 'Peril' : 'Region-Peril'}</th>
                    <th className="num">AAL A</th>
                    <th className="num">AAL B</th>
                    <th className="num">AAL Δ</th>
                    <th className="num">1-in-100 OEP Δ</th>
                    <th className="num">1-in-100 AEP Δ</th>
                    <th className="num">1-in-250 AEP A</th>
                    <th className="num">1-in-250 AEP B</th>
                    <th className="num">1-in-250 TVaR A</th>
                    <th className="num">1-in-250 TVaR B</th>
                    <th className="num">TVaR 250 Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {splitRows.map((r) => (
                    <tr key={r.key}>
                      <td style={{ fontWeight: 600 }}>{r.label}</td>
                      <td className="num">{fmtMoneyM(r.aalA, 2)}</td>
                      <td className="num">{fmtMoneyM(r.aalB, 2)}</td>
                      <td className={`num ${r.deltaAal <= 0 ? 'delta-pos' : 'delta-neg'}`}>
                        {fmtMoneyM(r.deltaAal, 2, { signed: true })}
                      </td>
                      <td className={`num ${r.deltaOep100 <= 0 ? 'delta-pos' : 'delta-neg'}`}>
                        {fmtMoneyM(r.deltaOep100, 2, { signed: true })}
                      </td>
                      <td className={`num ${r.deltaAep100 <= 0 ? 'delta-pos' : 'delta-neg'}`}>
                        {fmtMoneyM(r.deltaAep100, 2, { signed: true })}
                      </td>
                      <td className="num">{fmtMoneyM(r.aep250A, 2)}</td>
                      <td className="num">{fmtMoneyM(r.aep250B, 2)}</td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {fmtMoneyM(r.tvar250A, 2)}
                      </td>
                      <td className="num">{fmtMoneyM(r.tvar250B, 2)}</td>
                      <td className={`num ${r.deltaTvar250 <= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontWeight: 600 }}>
                        {fmtMoneyM(r.deltaTvar250, 2, { signed: true })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export function PortfolioTimelineCompare({
  defaultCapital,
  defaultView,
  refreshKey,
}: {
  defaultCapital: number;
  defaultView: ViewOfRisk;
  refreshKey?: number | string;
}) {
  const optionsQ = useQuery({
    queryKey: ['portfolio', 'compare-options', refreshKey],
    queryFn: api.getPortfolioCompareOptions,
  });

  const compareMut = useMutation({
    mutationFn: api.comparePortfolios,
  });

  const autoRanRef = useRef(false);
  useEffect(() => {
    if (autoRanRef.current) return;
    const first = optionsQ.data?.presets?.[0];
    if (!first) return;
    autoRanRef.current = true;
    compareMut.mutate({
      portfolioA: { ...first.portfolioA, totalCapital: defaultCapital, view: first.portfolioA.view ?? defaultView },
      portfolioB: { ...first.portfolioB, totalCapital: defaultCapital, view: first.portfolioB.view ?? defaultView },
    });
  }, [optionsQ.data, defaultCapital, defaultView]);

  if (optionsQ.isError) {
    return <div className="error-banner">{String((optionsQ.error as Error).message)}</div>;
  }
  if (!optionsQ.data) {
    return (
      <div className="card" style={{ color: 'var(--text-muted)' }}>
        Loading portfolio timeline options…
      </div>
    );
  }

  return (
    <PortfolioTimelineCompareView
      options={optionsQ.data}
      result={compareMut.data ?? null}
      isPending={compareMut.isPending}
      error={compareMut.isError ? String((compareMut.error as Error).message) : null}
      defaultCapital={defaultCapital}
      defaultView={defaultView}
      onRunCompare={(req) => compareMut.mutate(req)}
    />
  );
}
