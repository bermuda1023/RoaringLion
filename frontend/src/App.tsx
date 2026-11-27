import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { API_BASE, api, fetchJson } from './api/client';
import { BreadcrumbBar } from './components/BreadcrumbBar';
import { NavIcon } from './components/NavIcon';
import { fmtMoneyM, fmtNum } from './utils/format';
import { AllocationAndBranches } from './pages/AllocationAndBranches';
import { BurnAndClaims } from './pages/BurnAndClaims';
import { CatModelsAndOps } from './pages/CatModelsAndOps';
import { ContractManager } from './pages/ContractManager';
import { PortfolioOptimizer } from './pages/PortfolioOptimizer';
import { PortfolioReturns } from './pages/PortfolioReturns';
import { SimSetCatalog } from './pages/SimSetCatalog';
import { StructuringStudio } from './pages/StructuringStudio';
import type { DeterministicLossRow, ViewOfRisk } from './types/contracts';

type TabKey =
  | 'cms'
  | 'simsets'
  | 'dag'
  | 'deterministic'
  | 'burnclaims'
  | 'portfolio'
  | 'allocation'
  | 'optimizer'
  | 'modelsops'
  | 'benchmarks';

const NAV_GROUPS: { label: string; items: { key: TabKey; label: string; icon: string }[] }[] = [
  {
    label: 'Underwriting',
    items: [
      { key: 'cms', label: 'Contracts', icon: 'ledger' },
      { key: 'dag', label: 'Structuring Studio', icon: 'graph' },
      { key: 'deterministic', label: 'Deterministic', icon: 'list' },
    ],
  },
  {
    label: 'Portfolio',
    items: [
      { key: 'portfolio', label: 'Portfolio Returns', icon: 'pie' },
      { key: 'optimizer', label: 'Optimizer', icon: 'target' },
      { key: 'allocation', label: 'Retro & Scenarios', icon: 'branch' },
    ],
  },
  {
    label: 'Data & Models',
    items: [
      { key: 'simsets', label: 'Simulation Sets', icon: 'database' },
      { key: 'burnclaims', label: 'Burn & Claims', icon: 'flame' },
      { key: 'modelsops', label: 'Models & Operations', icon: 'cpu' },
    ],
  },
  {
    label: 'Governance',
    items: [{ key: 'benchmarks', label: 'Lineage & Audit', icon: 'shield' }],
  },
];

const PAGE_INFO: Record<TabKey, { title: string; subtitle: string }> = {
  cms: { title: 'Contract Management', subtitle: 'Programme ledger (Treaty, ILW & Retro), dossiers, and renewal history' },
  dag: { title: 'Structuring Studio', subtitle: 'Build layer structures and review loss statistics' },
  deterministic: { title: 'Deterministic Scenarios', subtitle: 'Recovery and erosion statements for named events' },
  portfolio: { title: 'Portfolio Returns', subtitle: 'Fund returns, hedge accretion, risk appetite, and capital allocation' },
  optimizer: { title: 'Portfolio Optimizer', subtitle: 'Explore efficient frontiers with layer constraints' },
  allocation: { title: 'Retro & Scenarios', subtitle: 'Retrocession, branching, and historical backtests' },
  simsets: { title: 'Simulation Sets', subtitle: 'Event loss tables and synthetic catalogues' },
  burnclaims: { title: 'Burn & Claims', subtitle: 'Experience burn and claims development' },
  modelsops: { title: 'Models & Operations', subtitle: 'Model jobs, exposure sync, and fund ops' },
  benchmarks: { title: 'Lineage & Audit', subtitle: 'Run history, freshness, and performance' },
};

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
  const [tab, setTabState] = useState<TabKey>(() => {
    const raw = window.location.hash.replace('#', '');
    if (raw === 'ilw') return 'cms';
    return raw in PAGE_INFO ? (raw as TabKey) : 'dag';
  });
  const setTab = (next: TabKey) => {
    setTabState(next);
    window.history.replaceState(null, '', `#${next}`);
  };
  const [selectedProgId, setSelectedProgId] = useState<string>('prog-farmers-us-27');
  const [portfolioView, setPortfolioView] = useState<ViewOfRisk>('ADJUSTED');
  const [totalCapital, setTotalCapital] = useState<number>(160);
  const [detView, setDetView] = useState<ViewOfRisk>('UNADJUSTED');
  const [losses, setLosses] = useState<DeterministicLossRow[]>(DEFAULT_LOSSES);

  const programmesQ = useQuery({ queryKey: ['programmes'], queryFn: api.listProgrammes });
  const healthQ = useQuery({
    queryKey: ['health'],
    queryFn: () => fetchJson<{ status: string }>('/api/health'),
    refetchInterval: 30_000,
    retry: 1,
  });
  const selectedProg = programmesQ.data?.find((p) => p.programmeId === selectedProgId) ?? programmesQ.data?.[0];

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

  const page = PAGE_INFO[tab];
  const group = NAV_GROUPS.find((g) => g.items.some((i) => i.key === tab))?.label;
  const apiUp = healthQ.isSuccess;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">RL</div>
          <div>
            <div className="brand-name">RoaringLion</div>
            <div className="brand-sub">Catastrophe reinsurance desk</div>
          </div>
        </div>
        <nav aria-label="Primary navigation" style={{ display: 'contents' }}>
          {NAV_GROUPS.map((g) => (
            <div className="nav-group" key={g.label}>
              <div className="nav-group-label">{g.label}</div>
              {g.items.map((item) => (
                <button
                  key={item.key}
                  className={`nav-item ${tab === item.key ? 'active' : ''}`}
                  onClick={() => setTab(item.key)}
                  aria-current={tab === item.key ? 'page' : undefined}
                >
                  <NavIcon name={item.icon} />
                  {item.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div>
            <span className={`status-dot ${healthQ.isPending ? 'pending' : apiUp ? 'ok' : 'down'}`} />
            {healthQ.isPending ? 'Connecting…' : apiUp ? 'Systems online' : 'Systems offline'}
          </div>
          <div>Simulation &amp; portfolio engine</div>
        </div>
      </aside>

      <div className="app-main">
        <header className="page-header">
          <div style={{ minWidth: 0 }}>
            <div className="page-eyebrow">{group}</div>
            <div className="page-title">{page.title}</div>
            <div className="page-subtitle">{page.subtitle}</div>
          </div>
          <div className="page-meta">
            {selectedProg && ['dag', 'burnclaims', 'deterministic'].includes(tab) && (
              <span className="meta-chip">
                Programme <strong>{selectedProg.cedentName}</strong>
              </span>
            )}
            <span className="meta-chip">
              Programmes <strong>{programmesQ.data?.length ?? '—'}</strong>
            </span>
            <span className="meta-chip">
              <span className={`status-dot ${apiUp ? 'ok' : healthQ.isPending ? 'pending' : 'down'}`} style={{ marginRight: 0 }} />
              {apiUp ? 'Connected' : healthQ.isPending ? 'Connecting' : 'Offline'}
            </span>
          </div>
        </header>

      <main className="main">
        {programmesQ.isError && (
          <div className="error-banner">
            <strong>Backend API unreachable</strong> ({API_BASE || 'same-origin /api'}):{' '}
            {String((programmesQ.error as Error)?.message ?? 'Network error')}. If this is deployed on Vercel, host the
            FastAPI + Rust backend via the root <code>Dockerfile</code> (e.g. Render/Railway/Fly.io) and set{' '}
            <code>VITE_API_BASE_URL</code> in Vercel Project Settings, then redeploy.
          </div>
        )}
        {tab === 'cms' && (
          <ContractManager
            onSelectProgramme={(id) => {
              setSelectedProgId(id);
              setTab('dag');
            }}
          />
        )}

        {tab === 'simsets' && <SimSetCatalog />}

        {tab === 'burnclaims' && selectedProg && <BurnAndClaims programme={selectedProg} />}

        {tab === 'allocation' && <AllocationAndBranches />}

        {tab === 'optimizer' && <PortfolioOptimizer />}

        {tab === 'modelsops' && <CatModelsAndOps />}

        {tab === 'dag' && selectedProg && programmesQ.data && (
          <StructuringStudio selectedProg={selectedProg} onSelectProgramme={setSelectedProgId} />
        )}

        {tab === 'portfolio' && (
          <PortfolioReturns
            portfolioView={portfolioView}
            onChangeView={setPortfolioView}
            totalCapital={totalCapital}
            onChangeCapital={setTotalCapital}
            refreshKey={selectedProg?.upliftSet.version}
          />
        )}

        {tab === 'deterministic' && (
          <>
            <div className="card">
              <div className="card-title">
                <span>Loss List</span>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <select value={detView} onChange={(e) => setDetView(e.target.value as ViewOfRisk)}>
                    <option value="UNADJUSTED">UNADJUSTED</option>
                    <option value="ADJUSTED">ADJUSTED</option>
                  </select>
                  <button className="btn" onClick={() => detMut.mutate()}>
                    Run Recovery Statement
                  </button>
                </div>
              </div>
              <div className="table-wrap"><table>
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
              </table></div>
            </div>

            {detMut.data && (
              <div className="card">
                <div className="card-title">
                  <span>
                    Recovery & Erosion Statement — Ceded{' '}
                    {fmtMoneyM(detMut.data.totalCeded, 2)} · RIP Due: {fmtMoneyM(detMut.data.totalRipDue, 2)}
                  </span>
                </div>
                {detMut.data.layerStatements.map((stmt) => (
                  <div key={stmt.layerId} style={{ marginBottom: 16 }}>
                    <div style={{ fontWeight: 600, marginBottom: 6, color: 'var(--text-strong)' }}>
                      {stmt.layerName} ({stmt.layerType}) — Ceded: {fmtMoneyM(stmt.totalCededLoss, 2)} | RIP:{' '}
                      {fmtMoneyM(stmt.totalRipDue, 2)}
                    </div>
                    <div className="table-wrap"><table>
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
                            <td className="num">{fmtMoneyM(st.subjectLoss, 2)}</td>
                            <td className="num" style={{ fontWeight: 600 }}>{fmtMoneyM(st.cededLoss, 2)}</td>
                            <td className="num">{fmtMoneyM(st.ripDue, 2)}</td>
                            <td className="num">
                              {fmtMoneyM(st.stateBefore.remainingAggLimit, 1)} → {fmtMoneyM(st.stateAfter.remainingAggLimit, 1)}
                            </td>
                            <td className="num">{fmtNum(st.stateAfter.reinstatementsUsed, 2)}</td>
                            <td className="num">{fmtMoneyM(st.stateAfter.aggDeductibleEroded, 1)}</td>
                            <td className={`num ${st.stateAfter.exhausted ? 'fail' : 'pass'}`}>
                              {st.stateAfter.exhausted ? 'Yes' : 'No'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table></div>
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
                <span>Performance benchmarks</span>
              </div>
              <pre className="panel mono" style={{ margin: 0, overflowX: 'auto', color: 'var(--accent-soft)' }}>
                {JSON.stringify(benchmarksQ.data ?? {}, null, 2)}
              </pre>
            </div>
            <div className="card">
              <div className="card-title">
                <span>Run history & freshness ({manifestsQ.data?.length ?? 0})</span>
              </div>
              <div className="table-wrap"><table>
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
                        <td style={{ fontFamily: 'var(--mono)', color: 'var(--teal)' }}>{m.manifestId}</td>
                        <td>{m.runType}</td>
                        <td>
                          {st?.isStale ? (
                            <span className="fail" title={st.staleReasons.join('; ')}>
                              Stale
                            </span>
                          ) : (
                            <span className="pass">Fresh</span>
                          )}
                        </td>
                        <td style={{ fontFamily: 'var(--mono)' }}>#{m.outputHash}</td>
                        <td className="wrap" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                          {m.breadcrumb.map((b) => `${b.entityType}:${b.entityId}`).join(' › ')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table></div>
            </div>
          </>
        )}
      </main>
      </div>
    </div>
  );
}
