import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from './api/client';
import { BreadcrumbBar } from './components/BreadcrumbBar';
import { RocCdfChart } from './components/RocCdfChart';
import { ContractManager } from './pages/ContractManager';
import { IlwWorkbench } from './pages/IlwWorkbench';
import { SimSetCatalog } from './pages/SimSetCatalog';
import { StructuringStudio } from './pages/StructuringStudio';
import type { DeterministicLossRow, ViewOfRisk } from './types/contracts';

type TabKey = 'cms' | 'simsets' | 'ilw' | 'dag' | 'portfolio' | 'deterministic' | 'benchmarks';

const DEFAULT_LOSSES: DeterministicLossRow[] = [
  {
    lossId: 'L1',
    eventName: 'Hurricane Alpha (FL Landfall)',
    occurrenceDatetime: '2027-08-22T12:00:00Z',
    regionPerilKey: 'US-FL-WS',
    groundUpLoss: 115,
  },
  {
    lossId: 'L2',
    eventName: 'Hurricane Beta (TX Gulf)',
    occurrenceDatetime: '2027-09-14T18:00:00Z',
    regionPerilKey: 'US-TX-WS',
    groundUpLoss: 140,
  },
  {
    lossId: 'L3',
    eventName: 'Hurricane Gamma (Southeast)',
    occurrenceDatetime: '2027-10-08T09:00:00Z',
    regionPerilKey: 'US-SE-WS',
    groundUpLoss: 95,
  },
];

export function App() {
  const [tab, setTab] = useState<TabKey>('dag');
  const [selectedProgId, setSelectedProgId] = useState<string>('prog-farmers-us-27');
  const [portfolioView, setPortfolioView] = useState<ViewOfRisk>('ADJUSTED');
  const [totalCapital, setTotalCapital] = useState<number>(160);
  const [detView, setDetView] = useState<ViewOfRisk>('UNADJUSTED');
  const [losses, setLosses] = useState<DeterministicLossRow[]>(DEFAULT_LOSSES);

  const programmesQ = useQuery({ queryKey: ['programmes'], queryFn: api.listProgrammes });
  const selectedProg = programmesQ.data?.find((p) => p.programmeId === selectedProgId) ?? programmesQ.data?.[0];

  const portfolioQ = useQuery({
    queryKey: ['portfolio', portfolioView, totalCapital, selectedProg?.upliftSet.version],
    queryFn: () => api.getPortfolioRollup(portfolioView, totalCapital),
    enabled: tab === 'portfolio',
  });

  const detMut = useMutation({
    mutationFn: () =>
      api.runDeterministic({
        programmeId: selectedProgId,
        view: detView,
        hoursClause: 72,
        losses,
      }),
  });

  useEffect(() => {
    if (tab === 'deterministic' && !detMut.data && !detMut.isPending) {
      detMut.mutate();
    }
  }, [tab]);

  const benchmarksQ = useQuery({ queryKey: ['benchmarks'], queryFn: api.getBenchmarks, enabled: tab === 'benchmarks' });
  const manifestsQ = useQuery({ queryKey: ['manifests', tab], queryFn: api.listManifests, enabled: tab === 'benchmarks' });
  const stalenessQ = useQuery({ queryKey: ['staleness', tab], queryFn: api.checkStaleness, enabled: tab === 'benchmarks' });

  const staleMap = Object.fromEntries((stalenessQ.data ?? []).map((s) => [s.manifestId, s]));

  return (
    <div>
      <header className="topbar">
        <div className="brand">
          <span>ROARING LION</span>
          <span className="badge">Phase 2 · Interactive DAG Studio · Rust Kernel</span>
        </div>
        <nav className="tabs">
          <button className={`tab ${tab === 'cms' ? 'active' : ''}`} onClick={() => setTab('cms')}>
            CMS Ledger
          </button>
          <button className={`tab ${tab === 'simsets' ? 'active' : ''}`} onClick={() => setTab('simsets')}>
            Sim Sets & ELT
          </button>
          <button className={`tab ${tab === 'ilw' ? 'active' : ''}`} onClick={() => setTab('ilw')}>
            Instant ILWs
          </button>
          <button className={`tab ${tab === 'dag' ? 'active' : ''}`} onClick={() => setTab('dag')}>
            Structuring DAG
          </button>
          <button className={`tab ${tab === 'portfolio' ? 'active' : ''}`} onClick={() => setTab('portfolio')}>
            Portfolio & IRR
          </button>
          <button className={`tab ${tab === 'deterministic' ? 'active' : ''}`} onClick={() => setTab('deterministic')}>
            Deterministic
          </button>
          <button className={`tab ${tab === 'benchmarks' ? 'active' : ''}`} onClick={() => setTab('benchmarks')}>
            Lineage & Bench
          </button>
        </nav>
      </header>

      <main className="main">
        {tab === 'cms' && (
          <ContractManager
            onSelectProgramme={(id) => {
              setSelectedProgId(id);
              setTab('dag');
            }}
          />
        )}

        {tab === 'simsets' && <SimSetCatalog />}

        {tab === 'ilw' && <IlwWorkbench />}

        {tab === 'dag' && selectedProg && programmesQ.data && (
          <StructuringStudio
            programmes={programmesQ.data}
            selectedProg={selectedProg}
            onSelectProgramme={setSelectedProgId}
          />
        )}

        {tab === 'portfolio' && portfolioQ.data && (
          <>
            <div className="card">
              <div className="card-title">
                <span>{portfolioQ.data.portfolioName}</span>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <select value={portfolioView} onChange={(e) => setPortfolioView(e.target.value as ViewOfRisk)}>
                    <option value="ADJUSTED">ADJUSTED View</option>
                    <option value="UNADJUSTED">UNADJUSTED View</option>
                  </select>
                  <label style={{ color: '#94a3b8', fontSize: 12 }}>
                    Fund Capital ($M)
                    <input
                      type="number"
                      value={totalCapital}
                      onChange={(e) => setTotalCapital(Number(e.target.value) || 160)}
                      style={{ width: 80, marginLeft: 6 }}
                    />
                  </label>
                  <span style={{ color: '#94a3b8', fontSize: 11 }}>
                    Rollup: {portfolioQ.data.computeMs} ms
                  </span>
                </div>
              </div>
              <div className="grid-kpi">
                <div className="kpi">
                  <div className="kpi-label">Mean ROC</div>
                  <div className="kpi-val">{portfolioQ.data.meanRocPct.toFixed(2)}%</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">No-Event ROC</div>
                  <div className="kpi-val">{portfolioQ.data.noEventRocPct.toFixed(2)}%</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">Break-Even Prob</div>
                  <div className="kpi-val">{portfolioQ.data.breakEvenProbPct.toFixed(1)}%</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">Expected Profit</div>
                  <div className="kpi-val">${portfolioQ.data.expectedProfit.toFixed(2)}m</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">1-in-250 TVaR</div>
                  <div className="kpi-val">${portfolioQ.data.portfolioTvar250.toFixed(2)}m</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">Co-TVaR Sum Err</div>
                  <div className="kpi-val" style={{ color: '#22c55e' }}>
                    {portfolioQ.data.cotvarSumCheckDiff.toExponential(1)}
                  </div>
                </div>
              </div>

              <div className="grid-2">
                <div>
                  <RocCdfChart data={portfolioQ.data} />
                </div>
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 8 }}>Risk Appetite Guidelines</div>
                  <table>
                    <thead>
                      <tr>
                        <th>Percentile</th>
                        <th className="num">Limit</th>
                        <th className="num">Actual</th>
                        <th className="num">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {portfolioQ.data.guidelines.map((g) => (
                        <tr key={g.label}>
                          <td>{g.label}</td>
                          <td className="num">{g.thresholdRocPct.toFixed(1)}%</td>
                          <td className="num">{g.actualRocPct.toFixed(2)}%</td>
                          <td className={`num ${g.passed ? 'pass' : 'fail'}`}>{g.passed ? 'PASS' : 'BREACH'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div style={{ marginTop: 10 }}>
                <BreadcrumbBar manifest={portfolioQ.data.manifest} />
              </div>
            </div>

            <div className="card">
              <div className="card-title">
                <span>Euler Co-TVaR Marginal Capital Allocation (1-in-250 / 99.6th%)</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Cedent</th>
                    <th>Layer</th>
                    <th className="num">Limit</th>
                    <th className="num">Premium</th>
                    <th className="num">EL</th>
                    <th className="num">Exp RIP</th>
                    <th className="num">Standalone TVaR 250</th>
                    <th className="num">Euler Co-TVaR 250</th>
                    <th className="num">Div Credit</th>
                    <th className="num">Marginal ROC</th>
                  </tr>
                </thead>
                <tbody>
                  {portfolioQ.data.layerAllocations.map((row) => (
                    <tr key={`${row.programmeId}-${row.layerId}`}>
                      <td>{row.cedentName}</td>
                      <td>{row.layerName}</td>
                      <td className="num">${row.limit.toFixed(1)}m</td>
                      <td className="num">${row.premium.toFixed(2)}m</td>
                      <td className="num">${row.el.toFixed(2)}m</td>
                      <td className="num">${row.expectedRip.toFixed(2)}m</td>
                      <td className="num">${row.standaloneTvar250.toFixed(2)}m</td>
                      <td className="num" style={{ fontWeight: 600 }}>${row.eulerCotvar250.toFixed(2)}m</td>
                      <td className="num">{row.diversificationCreditPct.toFixed(1)}%</td>
                      <td className="num" style={{ color: '#38bdf8', fontWeight: 600 }}>
                        {row.marginalRocPct.toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {tab === 'deterministic' && (
          <>
            <div className="card">
              <div className="card-title">
                <span>Deterministic Loss List (Same Rust Financial-Terms Code as Stochastic Engine)</span>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <select value={detView} onChange={(e) => setDetView(e.target.value as ViewOfRisk)}>
                    <option value="UNADJUSTED">UNADJUSTED</option>
                    <option value="ADJUSTED">ADJUSTED</option>
                  </select>
                  <button className="btn" onClick={() => detMut.mutate()}>
                    Run Chronological Recovery Statement
                  </button>
                </div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Event Name</th>
                    <th>Occurrence Datetime</th>
                    <th>Region-Peril</th>
                    <th className="num">Ground-Up Loss ($M)</th>
                  </tr>
                </thead>
                <tbody>
                  {losses.map((row, idx) => (
                    <tr key={row.lossId}>
                      <td>{row.lossId}</td>
                      <td>{row.eventName}</td>
                      <td>{row.occurrenceDatetime}</td>
                      <td>{row.regionPerilKey}</td>
                      <td className="num">
                        <input
                          type="number"
                          value={row.groundUpLoss}
                          onChange={(e) => {
                            const next = [...losses];
                            next[idx] = { ...row, groundUpLoss: Number(e.target.value) || 0 };
                            setLosses(next);
                          }}
                          style={{ width: 90, textAlign: 'right' }}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {detMut.data && (
              <div className="card">
                <div className="card-title">
                  <span>
                    Per-Layer Stateful Recovery & Erosion Statement — Total Ceded: $
                    {detMut.data.totalCeded.toFixed(2)}m · RIP Due: ${detMut.data.totalRipDue.toFixed(2)}m
                  </span>
                </div>
                {detMut.data.layerStatements.map((stmt) => (
                  <div key={stmt.layerId} style={{ marginBottom: 16 }}>
                    <div style={{ fontWeight: 600, marginBottom: 6, color: '#f8fafc' }}>
                      {stmt.layerName} ({stmt.layerType}) — Ceded: ${stmt.totalCededLoss.toFixed(2)}m | RIP: $
                      {stmt.totalRipDue.toFixed(2)}m
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th>Event</th>
                          <th className="num">Subject</th>
                          <th className="num">Ceded</th>
                          <th className="num">RIP Due</th>
                          <th className="num">Rem Limit (Before → After)</th>
                          <th className="num">Reinst Used</th>
                          <th className="num">Agg Ded Eroded</th>
                          <th className="num">Exhausted</th>
                        </tr>
                      </thead>
                      <tbody>
                        {stmt.steps.map((st) => (
                          <tr key={st.lossId}>
                            <td>{st.eventName}</td>
                            <td className="num">${st.subjectLoss.toFixed(2)}m</td>
                            <td className="num" style={{ fontWeight: 600 }}>${st.cededLoss.toFixed(2)}m</td>
                            <td className="num">${st.ripDue.toFixed(2)}m</td>
                            <td className="num">
                              ${st.stateBefore.remainingAggLimit.toFixed(1)}m → ${st.stateAfter.remainingAggLimit.toFixed(1)}m
                            </td>
                            <td className="num">{st.stateAfter.reinstatementsUsed.toFixed(2)}</td>
                            <td className="num">${st.stateAfter.aggDeductibleEroded.toFixed(1)}m</td>
                            <td className={`num ${st.stateAfter.exhausted ? 'fail' : 'pass'}`}>
                              {st.stateAfter.exhausted ? 'YES' : 'NO'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
                <BreadcrumbBar manifest={detMut.data.manifest} />
              </div>
            )}
          </>
        )}

        {tab === 'benchmarks' && (
          <>
            <div className="card">
              <div className="card-title">
                <span>Phase 0 Kernel & Solver Benchmark Gate (Measured on This Machine)</span>
              </div>
              <pre style={{ margin: 0, overflowX: 'auto', color: '#93c5fd', fontSize: 12 }}>
                {JSON.stringify(benchmarksQ.data ?? {}, null, 2)}
              </pre>
            </div>
            <div className="card">
              <div className="card-title">
                <span>Recorded Run Manifests & Breadcrumb Staleness Audit ({manifestsQ.data?.length ?? 0})</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Manifest ID</th>
                    <th>Run Type</th>
                    <th>Freshness</th>
                    <th>Output Hash</th>
                    <th>Breadcrumb Chain</th>
                  </tr>
                </thead>
                <tbody>
                  {manifestsQ.data?.map((m) => {
                    const st = staleMap[m.manifestId];
                    return (
                      <tr key={m.manifestId}>
                        <td style={{ fontFamily: 'monospace', color: '#5eead4' }}>{m.manifestId}</td>
                        <td>{m.runType}</td>
                        <td>
                          {st?.isStale ? (
                            <span className="fail" title={st.staleReasons.join('; ')}>
                              STALE
                            </span>
                          ) : (
                            <span className="pass">FRESH</span>
                          )}
                        </td>
                        <td style={{ fontFamily: 'monospace' }}>#{m.outputHash}</td>
                        <td style={{ fontSize: 11, color: '#94a3b8' }}>
                          {m.breadcrumb.map((b) => `${b.entityType}:${b.entityId}`).join(' › ')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
