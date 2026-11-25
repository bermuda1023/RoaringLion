import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { fetchJson, postJson } from '../api/client';
import { fmtMoneyM, fmtPct } from '../utils/format';

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
    queryFn: () => fetchJson<any[]>('/api/branches'),
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
          <span>Retro Allocation & Fund Waterfall</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <select value={allocMethod} onChange={(e) => setAllocMethod(e.target.value)}>
              <option value="TAIL_RELIEF_COTVAR">Tail Relief (Co-TVaR)</option>
              <option value="EXPECTED_RECOVERY">Expected Recovery Share</option>
              <option value="HYBRID_RECOVERY_TAIL">Hybrid (70% Tail / 30% Rec)</option>
            </select>
            <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
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
                <div className="kpi-val">{fmtPct(alloc.waterfall.grossMeanRocPct, 2)}</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Net Mean ROC</div>
                <div className="kpi-val">{fmtPct(alloc.waterfall.netMeanRocPct, 2)}</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Net Effective IRR</div>
                <div className="kpi-val" style={{ color: 'var(--accent)' }}>{fmtPct(alloc.waterfall.netEffectiveIrrPct, 2)}</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Exp Trapped Collat</div>
                <div className="kpi-val">{fmtMoneyM(alloc.waterfall.expectedTrappedCollateral, 2)}</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Gross → Net TVaR 250</div>
                <div className="kpi-val" style={{ color: 'var(--positive)' }}>
                  {fmtMoneyM(alloc.waterfall.grossTvar250, 1)} → {fmtMoneyM(alloc.waterfall.netTvar250, 1)}
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
                    <td className="num">{fmtMoneyM(l.grossCotvar250, 2)}</td>
                    <td className="num">{fmtPct(l.grossMrocPct, 1)}</td>
                    <td className="num">{fmtMoneyM(l.allocatedRetroCost, 2)}</td>
                    <td className="num">{fmtMoneyM(l.allocatedRetroRecovery, 2)}</td>
                    <td className="num" style={{ color: 'var(--positive)' }}>{fmtMoneyM(l.retroTvarRelief, 2)}</td>
                    <td className="num">{fmtMoneyM(l.netCotvar250, 2)}</td>
                    <td className="num" style={{ color: 'var(--accent)', fontWeight: 600 }}>{fmtPct(l.netMrocPct, 1)}</td>
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
            <span>Portfolio Branches & Backtest · 1990–2025</span>
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
            <input value={branchName} onChange={(e) => setBranchName(e.target.value)} style={{ width: 160 }} />
            <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Farmers Share (%)
              <input
                type="number"
                step="5"
                value={Number((farmersShare * 100).toFixed(1))}
                onChange={(e) => setFarmersShare(Number(e.target.value) / 100)}
                style={{ width: 65, marginLeft: 4 }}
              />
            </label>
            <button className="btn" onClick={() => createBranchMut.mutate()}>
              Branch, Diff & Backtest
            </button>
          </div>
          {diffMut.data && (
            <div style={{ padding: 8, background: 'var(--surface-sunken)', borderRadius: 6, marginBottom: 8, fontSize: 12 }}>
              <strong>Branch Diff ({diffMut.data.branchName}):</strong> Mean ROC {fmtPct(diffMut.data.baselineMeanRocPct, 2)} →{' '}
              {fmtPct(diffMut.data.branchMeanRocPct, 2)} ({fmtPct(diffMut.data.deltaMeanRocPct, 2, { signed: true })}) | TVaR 250{' '}
              {fmtMoneyM(diffMut.data.baselineTvar250, 1)} → {fmtMoneyM(diffMut.data.branchTvar250, 1)} (
              {fmtMoneyM(diffMut.data.deltaTvar250, 1, { signed: true })})
            </div>
          )}
          {btMut.data && (
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              Historical Backtest (1990–2025 Trended As-Is): Baseline Avg ROC{' '}
              <strong style={{ color: 'var(--text)' }}>{btMut.data.baselineAvgRocPct}%</strong>
              {btMut.data.branchAvgRocPct != null && (
                <>
                  {' '}
                  | Branch Avg ROC <strong style={{ color: 'var(--accent)' }}>{btMut.data.branchAvgRocPct}%</strong>
                </>
              )}{' '}
              | Worst Year: <strong>{btMut.data.worstYearBaseline}</strong> ({btMut.data.worstYearRocPct}%)
            </div>
          )}
          <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-faint)' }}>
            Saved branches: {branchesQ.data?.length ?? 0}
          </div>
        </div>

        <div className="card">
          <div className="card-title">
            <span>Reverse Stress Testing</span>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
            <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Target Breach ROC (%)
              <input type="number" step="5" value={targetRoc} onChange={(e) => setTargetRoc(Number(e.target.value))} style={{ width: 70, marginLeft: 4 }} />
            </label>
            <button className="btn" onClick={() => reverseMut.mutate()}>
              Forge Minimum Breaching Scenario
            </button>
          </div>
          {reverseMut.data && (
            <div style={{ padding: 10, background: 'var(--surface-sunken)', borderRadius: 6, fontSize: 12 }}>
              <div>
                <span className="badge">{reverseMut.data.scenario.provenanceTag}</span>{' '}
                <strong>{reverseMut.data.scenario.name}</strong>
              </div>
              <div style={{ marginTop: 6, color: 'var(--accent-soft)' }}>
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
