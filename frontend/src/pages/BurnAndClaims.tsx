import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { fetchJson, postJson } from '../api/client';
import type { ProgrammeDeal } from '../types/contracts';

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
              Annual Trend
              <input type="number" step="0.005" value={trend} onChange={(e) => setTrend(Number(e.target.value))} style={{ width: 65, marginLeft: 4 }} />
            </label>
            <label style={{ fontSize: 11, color: '#94a3b8' }}>
              Burn Credibility
              <input type="number" step="0.05" value={credWeight} onChange={(e) => setCredWeight(Number(e.target.value))} style={{ width: 65, marginLeft: 4 }} />
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
              <div className="kpi-val">${bc.averageAnnualBurnM.toFixed(2)}m</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Ex-Largest Burn</div>
              <div className="kpi-val">${bc.exLargestBurnM.toFixed(2)}m</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Burn Rate / Limit</div>
              <div className="kpi-val">{bc.burningCostRatePct.toFixed(2)}%</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Modelled EL (Adj)</div>
              <div className="kpi-val">${(bc.modelledElM ?? 0).toFixed(2)}m</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Credibility Blended EL</div>
              <div className="kpi-val" style={{ color: '#38bdf8' }}>${(bc.credibilityBlendedElM ?? 0).toFixed(2)}m</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Rec Technical ROL</div>
              <div className="kpi-val" style={{ color: '#22c55e' }}>{(bc.recommendedTechnicalRolPct ?? 0).toFixed(2)}%</div>
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
                <td className="num">${c.latestGroundUpLoss.toFixed(2)}m</td>
              </tr>
            ))}
          </tbody>
        </table>
        {evalClaimsMut.data && (
          <div style={{ marginTop: 10, color: '#5eead4', fontSize: 12 }}>
            Live Claims Evaluated: Ground-Up ${evalClaimsMut.data.totalGroundUp.toFixed(2)}m → Ceded Recovery $
            {evalClaimsMut.data.totalCeded.toFixed(2)}m · RIP Due ${evalClaimsMut.data.totalRipDue.toFixed(2)}m
          </div>
        )}
      </div>
    </>
  );
}
