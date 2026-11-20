import { useMutation } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    let message = `HTTP ${res.status}: ${text.slice(0, 300)}`;
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) {
        const msgs = body.detail
          .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : ''))
          .filter(Boolean);
        if (msgs.length) message = msgs.join('; ');
      }
    } catch {
      // keep the HTTP fallback message
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export function PortfolioOptimizer() {
  const [mode, setMode] = useState<'CURRENT' | 'FORWARD_PLANNING'>('FORWARD_PLANNING');
  const [maxTvar250, setMaxTvar250] = useState(115);
  const [maxFlCap, setMaxFlCap] = useState(75);
  const [minLine, setMinLine] = useState(0.0);
  const [includeRetro, setIncludeRetro] = useState(true);

  const solveMut = useMutation({
    mutationFn: () =>
      postJson<any>('/api/optimizer/solve', {
        mode,
        view: 'ADJUSTED',
        totalCapital: 160,
        maxDeployedCollateral: 160,
        maxTvar100M: maxTvar250 * 0.85,
        maxTvar250M: maxTvar250,
        regionPerilCaps: [{ regionPerilKey: 'US-FL-WS', maxLimitM: maxFlCap }],
        minLineSizeShare: minLine,
        includeRetroIlwCandidate: includeRetro,
      }),
  });

  const frontierMut = useMutation({
    mutationFn: () =>
      postJson<any[]>('/api/optimizer/frontier?num_points=5', {
        mode,
        view: 'ADJUSTED',
        totalCapital: 160,
        maxTvar250M: maxTvar250,
        includeRetroIlwCandidate: includeRetro,
      }),
  });

  useEffect(() => {
    solveMut.mutate();
  }, []);

  const d = solveMut.data;

  return (
    <div className="card">
      <div className="card-title">
        <span>True Portfolio Optimizer (HiGHS LP/MILP · Current & Forward Planning · Shadow Prices)</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" onClick={() => solveMut.mutate()}>
            Solve Optimal Portfolio
          </button>
          <button className="btn btn-ghost" onClick={() => frontierMut.mutate()}>
            Sweep Efficient Frontier
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', padding: 10, background: '#0d1422', borderRadius: 6, marginBottom: 14, border: '1px solid #26324a' }}>
        <select value={mode} onChange={(e) => setMode(e.target.value as any)}>
          <option value="FORWARD_PLANNING">Forward-Looking Planning</option>
          <option value="CURRENT">Current Portfolio Rebalance</option>
        </select>
        <label style={{ fontSize: 11, color: '#94a3b8' }}>
          Max 1-in-250 TVaR ($M)
          <input type="number" value={maxTvar250} onChange={(e) => setMaxTvar250(Number(e.target.value))} style={{ width: 70, marginLeft: 4 }} />
        </label>
        <label style={{ fontSize: 11, color: '#94a3b8' }}>
          FL Wind Limit Cap ($M)
          <input type="number" value={maxFlCap} onChange={(e) => setMaxFlCap(Number(e.target.value))} style={{ width: 70, marginLeft: 4 }} />
        </label>
        <label style={{ fontSize: 11, color: '#94a3b8' }}>
          Min Line Share (MILP)
          <input type="number" step="0.05" value={minLine} onChange={(e) => setMinLine(Number(e.target.value))} style={{ width: 65, marginLeft: 4 }} />
        </label>
        <label style={{ fontSize: 11, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="checkbox" checked={includeRetro} onChange={(e) => setIncludeRetro(e.target.checked)} />
          Include Retro FL ILW Hedge
        </label>
      </div>

      {(solveMut.error || frontierMut.error) && (
        <div className="error-banner">
          {String(((solveMut.error ?? frontierMut.error) as Error).message)}
        </div>
      )}
      {solveMut.isPending && !d && <div style={{ color: '#94a3b8', fontSize: 12, marginBottom: 12 }}>Solving…</div>}

      {d && (
        <>
          <div className="grid-kpi">
            <div className="kpi">
              <div className="kpi-label">Solver / Status</div>
              <div className="kpi-val" style={{ fontSize: 14 }}>{d.solver} · {d.status}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Solve Time</div>
              <div className="kpi-val">{d.computeMs} ms</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Exp Profit (Before → After)</div>
              <div className="kpi-val" style={{ color: '#22c55e' }}>
                ${d.beforeExpectedProfitM.toFixed(1)}m → ${d.afterExpectedProfitM.toFixed(1)}m
              </div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Mean ROC (Before → After)</div>
              <div className="kpi-val">{d.beforeMeanRocPct}% → {d.afterMeanRocPct}%</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">1-in-250 TVaR</div>
              <div className="kpi-val">${d.beforeTvar250M.toFixed(1)}m → ${d.afterTvar250M.toFixed(1)}m</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Profit Delta</div>
              <div className="kpi-val" style={{ color: d.deltaExpectedProfitM >= 0 ? '#22c55e' : '#f59e0b' }}>
                {d.deltaExpectedProfitM >= 0 ? '+' : ''}${d.deltaExpectedProfitM.toFixed(2)}m
              </div>
            </div>
          </div>

          <div className="grid-2">
            <div>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Optimal Signed Lines & Retro Hedges</div>
              <table>
                <thead>
                  <tr>
                    <th>Deal</th>
                    <th>Dir</th>
                    <th className="num">Cur Share</th>
                    <th className="num">Opt Share</th>
                    <th className="num">Opt Limit</th>
                    <th className="num">Opt Profit</th>
                  </tr>
                </thead>
                <tbody>
                  {d.deals.map((r: any) => (
                    <tr key={r.programmeId}>
                      <td>{r.cedentName} — {r.programmeName}</td>
                      <td style={{ fontSize: 11, color: '#94a3b8' }}>{r.direction}</td>
                      <td className="num">{(r.currentShare * 100).toFixed(0)}%</td>
                      <td className="num" style={{ fontWeight: 700, color: '#38bdf8' }}>{(r.optimalShare * 100).toFixed(1)}%</td>
                      <td className="num">${r.optimalLimitM.toFixed(1)}m</td>
                      <td className="num">${r.optimalExpectedProfitM.toFixed(2)}m</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Constraint Diagnostics & Shadow Prices (Duals)</div>
              <table>
                <thead>
                  <tr>
                    <th>Constraint</th>
                    <th className="num">Limit</th>
                    <th className="num">Actual</th>
                    <th className="num">Binding</th>
                    <th className="num">Shadow Price</th>
                  </tr>
                </thead>
                <tbody>
                  {d.constraints.map((c: any) => (
                    <tr key={c.name}>
                      <td>{c.name}</td>
                      <td className="num">{c.limitValue.toFixed(1)}</td>
                      <td className="num">{c.actualValue.toFixed(1)}</td>
                      <td className={`num ${c.binding ? 'fail' : 'pass'}`}>{c.binding ? 'BINDING' : 'SLACK'}</td>
                      <td className="num" style={{ color: '#f59e0b' }}>
                        {c.shadowPrice != null ? `$${c.shadowPrice.toFixed(4)}m` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {frontierMut.data && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Pareto Efficient Frontier (TVaR Cap vs Expected Profit & ROC)</div>
          <table>
            <thead>
              <tr>
                <th className="num">TVaR 250 Cap</th>
                <th className="num">Actual TVaR 250</th>
                <th className="num">Deployed Collateral</th>
                <th className="num">Expected Profit</th>
                <th className="num">Mean ROC</th>
              </tr>
            </thead>
            <tbody>
              {frontierMut.data.map((pt, i) => (
                <tr key={i}>
                  <td className="num">${pt.maxTvar250M}m</td>
                  <td className="num">${pt.actualTvar250M}m</td>
                  <td className="num">${pt.deployedCollateralM}m</td>
                  <td className="num" style={{ color: '#22c55e', fontWeight: 600 }}>${pt.expectedProfitM}m</td>
                  <td className="num">{pt.meanRocPct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
