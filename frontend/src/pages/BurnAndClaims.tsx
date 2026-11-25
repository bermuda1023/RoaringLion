import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { fetchJson, postJson } from '../api/client';
import type { ProgrammeDeal } from '../types/contracts';
import { fmtMoneyM, fmtPct } from '../utils/format';

export function BurnAndClaims({ programme }: { programme: ProgrammeDeal }) {
  const qc = useQueryClient();
  const [trend, setTrend] = useState(0.035);
  const [credWeight, setCredWeight] = useState(0.3);
  const [claimNo, setClaimNo] = useState('CLM-2027-001');
  const [claimEvent, setClaimEvent] = useState('Hurricane Delta (FL)');
  const [claimLoss, setClaimLoss] = useState(118);

  const burnMut = useMutation({
    mutationFn: () =>
      postJson<Record<string, any>>('/api/burn-cost/evaluate', {
        programmeId: programme.programmeId,
        startYear: 1990,
        endYear: 2025,
        annualSeverityTrend: trend,
        credibilityWeightBurn: credWeight,
      }),
  });

  const claimsQ = useQuery({
    queryKey: ['claims', programme.programmeId],
    queryFn: () => fetchJson<any[]>(`/api/claims?programmeId=${programme.programmeId}`),
  });

  const addClaimMut = useMutation({
    mutationFn: () =>
      postJson('/api/claims', {
        claimNo,
        programmeId: programme.programmeId,
        eventName: claimEvent,
        occurrenceDatetime: '2027-09-05T12:00:00Z',
        regionPerilKey: programme.regionPerils[0] ?? 'US-FL-WS',
        groundUpLoss: claimLoss,
        status: 'CASE_RESERVE',
        note: 'Initial cedent bordereaux advice',
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['claims'] }),
  });

  const evalClaimsMut = useMutation({
    mutationFn: () => postJson<any>(`/api/claims/evaluate/${programme.programmeId}`),
  });

  useEffect(() => {
    burnMut.mutate();
  }, [programme.programmeId]);

  const bc = burnMut.data;

  return (
    <>
      <div className="card">
        <div className="card-title">
          <span>Historical As-If (1990–2025) & Burning Cost — {programme.cedentName}</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ fontSize: 11, color: '#94a3b8' }}>
              Annual Trend (%)
              <input
                type="number"
                step="0.5"
                value={Number((trend * 100).toFixed(2))}
                onChange={(e) => setTrend(Number(e.target.value) / 100)}
                style={{ width: 65, marginLeft: 4 }}
              />
            </label>
            <label style={{ fontSize: 11, color: '#94a3b8' }}>
              Burn Credibility (%)
              <input
                type="number"
                step="5"
                value={Number((credWeight * 100).toFixed(1))}
                onChange={(e) => setCredWeight(Number(e.target.value) / 100)}
                style={{ width: 65, marginLeft: 4 }}
              />
            </label>
            <button className="btn" onClick={() => burnMut.mutate()}>
              Run Burn Cost
            </button>
          </div>
        </div>
        {(burnMut.error || addClaimMut.error || evalClaimsMut.error || claimsQ.error) && (
          <div className="error-banner">
            {String(
              ((burnMut.error ?? addClaimMut.error ?? evalClaimsMut.error ?? claimsQ.error) as Error).message,
            )}
          </div>
        )}
        {bc && (
          <div className="grid-kpi">
            <div className="kpi">
              <div className="kpi-label">Avg Annual Burn (36y)</div>
              <div className="kpi-val">{fmtMoneyM(bc.averageAnnualBurnM, 2)}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Ex-Largest Burn</div>
              <div className="kpi-val">{fmtMoneyM(bc.exLargestBurnM, 2)}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Burn Rate / Limit</div>
              <div className="kpi-val">{fmtPct(bc.burningCostRatePct, 2)}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Modelled EL (Adj)</div>
              <div className="kpi-val">{fmtMoneyM(bc.modelledElM ?? 0, 2)}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Credibility Blended EL</div>
              <div className="kpi-val" style={{ color: '#38bdf8' }}>{fmtMoneyM(bc.credibilityBlendedElM ?? 0, 2)}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Rec Technical ROL</div>
              <div className="kpi-val" style={{ color: '#22c55e' }}>{fmtPct(bc.recommendedTechnicalRolPct ?? 0, 2)}</div>
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-title">
          <span>Live Claims Ledger (Versioned Estimate History → Chronological Recovery Statement)</span>
          <button className="btn" onClick={() => evalClaimsMut.mutate()}>
            Run Live Claims Recovery
          </button>
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          <input value={claimNo} onChange={(e) => setClaimNo(e.target.value)} placeholder="Claim No" style={{ width: 120 }} />
          <input value={claimEvent} onChange={(e) => setClaimEvent(e.target.value)} placeholder="Event Name" style={{ width: 180 }} />
          <input type="number" value={claimLoss} onChange={(e) => setClaimLoss(Number(e.target.value))} style={{ width: 90 }} />
          <button className="btn btn-ghost" onClick={() => addClaimMut.mutate()}>
            + Log Claim Advice
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>Claim No</th>
              <th>Event</th>
              <th>Region-Peril</th>
              <th>Version</th>
              <th>Status</th>
              <th className="num">Ground-Up ($M)</th>
            </tr>
          </thead>
          <tbody>
            {claimsQ.data?.map((c) => (
              <tr key={c.claimId}>
                <td style={{ fontFamily: 'monospace' }}>{c.claimNo}</td>
                <td>{c.eventName}</td>
                <td>{c.regionPerilKey}</td>
                <td>v{c.currentVersion}</td>
                <td><span className="badge">{c.latestStatus}</span></td>
                <td className="num">{fmtMoneyM(c.latestGroundUpLoss, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {evalClaimsMut.data && (
          <div style={{ marginTop: 10, color: '#5eead4', fontSize: 12 }}>
            Live Claims Evaluated: Ground-Up {fmtMoneyM(evalClaimsMut.data.totalGroundUp, 2)} → Ceded Recovery{' '}
            {fmtMoneyM(evalClaimsMut.data.totalCeded, 2)} · RIP Due {fmtMoneyM(evalClaimsMut.data.totalRipDue, 2)}
          </div>
        )}
      </div>
    </>
  );
}
