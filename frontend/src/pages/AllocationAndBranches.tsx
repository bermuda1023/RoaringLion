import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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

export function AllocationAndBranches() {
  const qc = useQueryClient();
  const [allocMethod, setAllocMethod] = useState('TAIL_RELIEF_COTVAR');
  const [retroLimit, setRetroLimit] = useState(25);
  const [branchName, setBranchName] = useState('Cut FL Wind 50%');
  const [farmersShare, setFarmersShare] = useState(0.5);
  const [targetRoc, setTargetRoc] = useState(0.0);

  const allocMut = useMutation({
    mutationFn: () =>
      postJson<any>('/api/allocation/gross-net', {
        view: 'ADJUSTED',
        totalCapital: 160,
        allocationMethod: allocMethod,
        retroIlwRegionPerils: ['US-FL-WS'],
        retroIlwTriggerBn: 40,
        retroIlwLimitM: retroLimit,
        retroIlwRol: 0.2,
      }),
  });

  const branchesQ = useQuery({
    queryKey: ['branches'],
    queryFn: async () => (await (await fetch('/api/branches')).json()) as any[],
  });

  const createBranchMut = useMutation({
    mutationFn: () =>
      postJson<any>('/api/branches', {
        name: branchName,
        signedLineOverrides: { 'prog-farmers-us-27': farmersShare },
      }),
    onSuccess: (br) => {
      qc.invalidateQueries({ queryKey: ['branches'] });
      diffMut.mutate(br.branchId);
      btMut.mutate(br.branchId);
    },
  });

  const diffMut = useMutation({
    mutationFn: (branchId: string) => postJson<any>(`/api/branches/${branchId}/diff?view=ADJUSTED&totalCapital=160`),
  });

  const btMut = useMutation({
    mutationFn: (branchId?: string) =>
      postJson<any>('/api/backtest/matrix', {
        branchId: branchId ?? null,
        startYear: 1990,
        endYear: 2025,
        asIsTrended: true,
      }),
  });

  const reverseMut = useMutation({
    mutationFn: () =>
      postJson<any>('/api/scenarios/reverse-stress', {
        name: `Reverse Stress ROC <= ${targetRoc}%`,
        targetRocPct: targetRoc,
        totalCapital: 160,
        seedRegionPeril: 'US-FL-WS',
        baseGroundUpM: 100,
      }),
  });

  useEffect(() => {
    allocMut.mutate();
    btMut.mutate(undefined);
  }, []);

  const alloc = allocMut.data;

  return (
    <>
      <div className="card">
        <div className="card-title">
          <span>Gross vs Net Retro Cost & Benefit Allocation + Collateralized Fund IRR Waterfall</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <select value={allocMethod} onChange={(e) => setAllocMethod(e.target.value)}>
              <option value="TAIL_RELIEF_COTVAR">Tail Relief (Co-TVaR)</option>
              <option value="EXPECTED_RECOVERY">Expected Recovery Share</option>
              <option value="HYBRID_RECOVERY_TAIL">Hybrid (70% Tail / 30% Rec)</option>
            </select>
            <label style={{ fontSize: 11, color: '#94a3b8' }}>
              Retro ILW Limit ($M)
              <input type="number" value={retroLimit} onChange={(e) => setRetroLimit(Number(e.target.value))} style={{ width: 65, marginLeft: 4 }} />
            </label>
            <button className="btn" onClick={() => allocMut.mutate()}>
              Compute Gross vs Net
            </button>
          </div>
        </div>
        {(allocMut.error || createBranchMut.error || diffMut.error || btMut.error || reverseMut.error) && (
          <div className="error-banner">
            {String(
              ((allocMut.error ?? createBranchMut.error ?? diffMut.error ?? btMut.error ?? reverseMut.error) as Error).message,
            )}
          </div>
        )}
        {alloc && (
          <>
            <div className="grid-kpi">
              <div className="kpi">
                <div className="kpi-label">Gross Mean ROC</div>
                <div className="kpi-val">{alloc.waterfall.grossMeanRocPct.toFixed(2)}%</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Net Mean ROC</div>
                <div className="kpi-val">{alloc.waterfall.netMeanRocPct.toFixed(2)}%</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Net Effective IRR</div>
                <div className="kpi-val" style={{ color: '#38bdf8' }}>{alloc.waterfall.netEffectiveIrrPct.toFixed(2)}%</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Exp Trapped Collat</div>
                <div className="kpi-val">${alloc.waterfall.expectedTrappedCollateral.toFixed(2)}m</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Gross → Net TVaR 250</div>
                <div className="kpi-val" style={{ color: '#22c55e' }}>
                  ${alloc.waterfall.grossTvar250.toFixed(1)}m → ${alloc.waterfall.netTvar250.toFixed(1)}m
                </div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Retro Conservation Err</div>
                <div className="kpi-val">{alloc.retroCostConservationError.toExponential(1)}</div>
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Cedent</th>
                  <th>Layer</th>
                  <th className="num">Gross Co-TVaR</th>
                  <th className="num">Gross mROC</th>
                  <th className="num">Alloc Retro Cost</th>
                  <th className="num">Alloc Retro Rec</th>
                  <th className="num">TVaR Relief</th>
                  <th className="num">Net Co-TVaR</th>
                  <th className="num">Net mROC</th>
                </tr>
              </thead>
              <tbody>
                {alloc.layers.map((l: any) => (
                  <tr key={`${l.programmeId}-${l.layerId}`}>
                    <td>{l.cedentName}</td>
                    <td>{l.layerName}</td>
                    <td className="num">${l.grossCotvar250.toFixed(2)}m</td>
                    <td className="num">{l.grossMrocPct.toFixed(1)}%</td>
                    <td className="num">${l.allocatedRetroCost.toFixed(2)}m</td>
                    <td className="num">${l.allocatedRetroRecovery.toFixed(2)}m</td>
                    <td className="num" style={{ color: '#22c55e' }}>${l.retroTvarRelief.toFixed(2)}m</td>
                    <td className="num">${l.netCotvar250.toFixed(2)}m</td>
                    <td className="num" style={{ color: '#38bdf8', fontWeight: 600 }}>{l.netMrocPct.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title">
            <span>Versioned Portfolio Branching & All-Direction Backtest (1990–2025)</span>
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
            <input value={branchName} onChange={(e) => setBranchName(e.target.value)} style={{ width: 160 }} />
            <label style={{ fontSize: 11, color: '#94a3b8' }}>
              Farmers Share
              <input type="number" step="0.1" value={farmersShare} onChange={(e) => setFarmersShare(Number(e.target.value))} style={{ width: 65, marginLeft: 4 }} />
            </label>
            <button className="btn" onClick={() => createBranchMut.mutate()}>
              Branch, Diff & Backtest
            </button>
          </div>
          {diffMut.data && (
            <div style={{ padding: 8, background: '#0d1422', borderRadius: 6, marginBottom: 8, fontSize: 12 }}>
              <strong>Branch Diff ({diffMut.data.branchName}):</strong> Mean ROC {diffMut.data.baselineMeanRocPct}% →{' '}
              {diffMut.data.branchMeanRocPct}% ({diffMut.data.deltaMeanRocPct >= 0 ? '+' : ''}
              {diffMut.data.deltaMeanRocPct}%) | TVaR 250 ${diffMut.data.baselineTvar250.toFixed(1)}m → $
              {diffMut.data.branchTvar250.toFixed(1)}m ({diffMut.data.deltaTvar250.toFixed(1)}m)
            </div>
          )}
          {btMut.data && (
            <div style={{ fontSize: 12, color: '#94a3b8' }}>
              Historical Backtest (1990–2025 Trended As-Is): Baseline Avg ROC{' '}
              <strong style={{ color: '#e6edf7' }}>{btMut.data.baselineAvgRocPct}%</strong>
              {btMut.data.branchAvgRocPct != null && (
                <>
                  {' '}
                  | Branch Avg ROC <strong style={{ color: '#38bdf8' }}>{btMut.data.branchAvgRocPct}%</strong>
                </>
              )}{' '}
              | Worst Year: <strong>{btMut.data.worstYearBaseline}</strong> ({btMut.data.worstYearRocPct}%)
            </div>
          )}
          <div style={{ marginTop: 8, fontSize: 11, color: '#64748b' }}>
            Saved branches: {branchesQ.data?.length ?? 0}
          </div>
        </div>

        <div className="card">
          <div className="card-title">
            <span>Scenario Forge — Reverse Stress Testing</span>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
            <label style={{ fontSize: 11, color: '#94a3b8' }}>
              Target Breach ROC (%)
              <input type="number" step="5" value={targetRoc} onChange={(e) => setTargetRoc(Number(e.target.value))} style={{ width: 70, marginLeft: 4 }} />
            </label>
            <button className="btn" onClick={() => reverseMut.mutate()}>
              Forge Minimum Breaching Scenario
            </button>
          </div>
          {reverseMut.data && (
            <div style={{ padding: 10, background: '#0d1422', borderRadius: 6, fontSize: 12 }}>
              <div>
                <span className="badge">{reverseMut.data.scenario.provenanceTag}</span>{' '}
                <strong>{reverseMut.data.scenario.name}</strong>
              </div>
              <div style={{ marginTop: 6, color: '#93c5fd' }}>
                Required FL Ground-Up Loss: <strong>${reverseMut.data.requiredGroundUpM.toFixed(2)}m</strong> (×
                {reverseMut.data.requiredMultiplier}) → Fund Loss ${reverseMut.data.resultingPortfolioLossM.toFixed(2)}m →
                ROC <strong>{reverseMut.data.resultingRocPct}%</strong>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
