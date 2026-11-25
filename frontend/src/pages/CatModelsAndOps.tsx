import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { fetchJson, postJson } from '../api/client';
import { fmtMoneyM, fmtPct, fmtRatioPct } from '../utils/format';

export function CatModelsAndOps() {
  const qc = useQueryClient();
  const [vendor, setVendor] = useState('RMS');
  const [slipText, setSlipText] = useState(
    'Cedent: Beacon Property Mutual. Layer: 30m xs 85m Florida Windstorm Cat XL, 21.5% ROL, 72 hours clause.',
  );

  const jobsQ = useQuery({ queryKey: ['modelRuns'], queryFn: () => fetchJson<any[]>('/api/model-runs') });
  const eeQ = useQuery({ queryKey: ['eeProgs'], queryFn: () => fetchJson<any[]>('/api/integrations/exposure-eclipse/programmes') });
  const invQ = useQuery({ queryKey: ['investors'], queryFn: () => fetchJson<any[]>('/api/fund-ops/investors') });
  const marksQ = useQuery({ queryKey: ['marks'], queryFn: () => fetchJson<any[]>('/api/market-marks') });

  const runModelMut = useMutation({
    mutationFn: () =>
      postJson('/api/model-runs', {
        vendor,
        programmeId: 'prog-farmers-us-27',
        edmDatabaseName: 'EDM_FARMERS_2027_Q1',
        numTrials: 25000,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['modelRuns'] });
      qc.invalidateQueries({ queryKey: ['simSets'] });
    },
  });

  const synthMut = useMutation({
    mutationFn: () =>
      postJson<any>('/api/synthetic/generate', {
        name: 'AUS_WF Synthetic Freq-Sev',
        regionPerilKey: 'US-CA-EQ',
        numTrials: 20000,
        meanFrequency: 2.0,
        severityDist: 'LOGNORMAL',
        paramMu: 18,
        paramSigma: 24,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['simSets'] }),
  });

  const slipMut = useMutation({
    mutationFn: () => postJson<any>('/api/intake/parse-slip', { rawText: slipText }),
  });

  return (
    <>
      <div className="grid-2">
        <div className="card">
          <div className="card-title">
            <span>Cat Model Orchestrator</span>
            <div style={{ display: 'flex', gap: 8 }}>
              <select value={vendor} onChange={(e) => setVendor(e.target.value)}>
                <option value="RMS">Moody&apos;s RMS IRP</option>
                <option value="AIR">Verisk AIR Touchstone</option>
                <option value="KCC">KCC RiskInsight</option>
                <option value="OASIS">Oasis LMF</option>
              </select>
              <button className="btn" onClick={() => runModelMut.mutate()}>
                Trigger API Run
              </button>
              <button className="btn btn-ghost" onClick={() => synthMut.mutate()}>
                + Synthetic Freq-Sev
              </button>
            </div>
          </div>
          <div className="table-wrap"><table>
            <thead>
              <tr>
                <th>Job ID</th>
                <th>Vendor</th>
                <th>EDM</th>
                <th>Status</th>
                <th>Output SimSet</th>
              </tr>
            </thead>
            <tbody>
              {jobsQ.data?.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty-cell">
                    No model runs yet. Trigger an API run to queue one.
                  </td>
                </tr>
              )}
              {jobsQ.data?.map((j) => (
                <tr key={j.jobId}>
                  <td style={{ fontFamily: 'var(--mono)' }}>{j.jobId}</td>
                  <td>{j.vendor} {j.modelVersion}</td>
                  <td>{j.edmDatabaseName}</td>
                  <td><span className="pass">{j.status}</span> ({j.mode})</td>
                  <td style={{ fontFamily: 'var(--mono)', color: 'var(--teal)' }}>{j.simSetId}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>

        <div className="card">
          <div className="card-title">
            <span>ExposureEclipse Bridge</span>
          </div>
          <div className="table-wrap"><table>
            <thead>
              <tr>
                <th>Cedent</th>
                <th>EDM Database</th>
                <th className="num">Total TIV ($M)</th>
                <th>Granular TIV Breakdown</th>
              </tr>
            </thead>
            <tbody>
              {eeQ.data?.map((p) => (
                <tr key={p.programmeId}>
                  <td style={{ fontWeight: 600 }}>{p.cedentName}</td>
                  <td style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{p.edmDatabaseName}</td>
                  <td className="num">{fmtMoneyM(p.totalTivM, 0)}</td>
                  <td className="wrap" style={{ fontSize: 11, color: 'var(--accent-soft)' }}>
                    {Object.entries(p.granularTivM).map(([k, v]) => `${k}: ${fmtMoneyM(Number(v), 0)}`).join(' · ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title">
            <span>Fund Operations · NAV & Side Pockets</span>
          </div>
          <div className="table-wrap"><table>
            <thead>
              <tr>
                <th>Series</th>
                <th>Investor</th>
                <th>Class</th>
                <th className="num">NAV ($M)</th>
                <th className="num">Side Pocket</th>
              </tr>
            </thead>
            <tbody>
              {invQ.data?.map((inv) => (
                <tr key={inv.seriesId}>
                  <td style={{ fontFamily: 'var(--mono)' }}>{inv.seriesId}</td>
                  <td>{inv.investorName}</td>
                  <td><span className="badge">{inv.shareClass}</span></td>
                  <td className="num">{fmtMoneyM(inv.navM, 1)}</td>
                  <td className="num">{fmtMoneyM(inv.trappedSidePocketM, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-muted)' }}>
            Market Marks: {marksQ.data?.map((m) => `${m.name} (${fmtPct(m.indicativeSpreadPct, 2)})`).join(' | ')}
          </div>
        </div>

        <div className="card">
          <div className="card-title">
            <span>Slip Intake</span>
            <button className="btn" onClick={() => slipMut.mutate()}>
              Extract Draft Terms
            </button>
          </div>
          <textarea
            value={slipText}
            onChange={(e) => setSlipText(e.target.value)}
            rows={3}
            style={{ width: '100%', background: 'var(--surface-sunken)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 6, padding: 8, fontSize: 12 }}
          />
          {slipMut.data && (
            <div style={{ marginTop: 8, padding: 8, background: 'var(--surface-sunken)', borderRadius: 6, fontSize: 12, color: 'var(--teal)' }}>
              Extracted Draft: <strong>{slipMut.data.cedentName}</strong> · {fmtMoneyM(slipMut.data.limitM, 1)} xs {fmtMoneyM(slipMut.data.attachmentM, 1)} · ROL {fmtRatioPct(slipMut.data.rol, 1)} · {slipMut.data.hoursClause}h · {slipMut.data.regionPerils.join(', ')}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
