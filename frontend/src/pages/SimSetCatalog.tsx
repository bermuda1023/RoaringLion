import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';

export function SimSetCatalog() {
  const qc = useQueryClient();
  const simSetsQ = useQuery({ queryKey: ['simSets'], queryFn: api.listSimSets });
  const [name, setName] = useState('Uploaded Broker YELT');
  const [trialCol, setTrialCol] = useState('trial_id');
  const [eventCol, setEventCol] = useState('event_id');
  const [lossCol, setLossCol] = useState('loss_amount');
  const [rpCol, setRpCol] = useState('region_peril_key');
  const [numTrials, setNumTrials] = useState(10000);
  const [file, setFile] = useState<File | null>(null);

  const uploadMut = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('Select a CSV or Parquet file first');
      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', name);
      fd.append('num_trials', String(numTrials));
      fd.append('trial_col', trialCol);
      fd.append('event_col', eventCol);
      fd.append('loss_col', lossCol);
      fd.append('region_peril_col', rpCol);
      return api.ingestYeltFile(fd);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['simSets'] }),
  });

  const sampleEltMut = useMutation({
    mutationFn: () =>
      api.ingestElt({
        name: 'Sample Vendor ELT -> 50k YELT',
        eventCatalogId: 'SYNTH_CAT_V1',
        numTrials: 50000,
        currency: 'USD',
        seed: 2027,
        rows: [
          { eventId: 9001, annualRate: 0.12, meanLoss: 45, sdIndep: 12, sdCorr: 6, regionPerilKey: 'US-FL-WS', dayOfYear: 250 },
          { eventId: 9002, annualRate: 0.06, meanLoss: 110, sdIndep: 25, sdCorr: 15, regionPerilKey: 'US-CA-EQ', dayOfYear: 180 },
          { eventId: 9003, annualRate: 0.25, meanLoss: 22, sdIndep: 6, sdCorr: 3, regionPerilKey: 'EU-DE-WS', dayOfYear: 30 },
        ],
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['simSets'] }),
  });

  return (
    <>
      <div className="card">
        <div className="card-title">
          <span>Registered Simulation Sets (Canonical Zstd Parquet — Indemnity & Industry Catalogs)</span>
          <button className="btn" onClick={() => sampleEltMut.mutate()}>
            Simulate Sample ELT → 50k YELT
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>Sim Set ID</th>
              <th>Name</th>
              <th>Kind</th>
              <th>Catalogue</th>
              <th className="num">Trials</th>
              <th className="num">Events</th>
              <th className="num">AAL</th>
              <th>Granular Region-Perils</th>
              <th>Hash</th>
            </tr>
          </thead>
          <tbody>
            {simSetsQ.data?.map((s) => (
              <tr key={s.simSetId}>
                <td style={{ fontFamily: 'monospace', color: '#5eead4' }}>{s.simSetId}</td>
                <td style={{ fontWeight: 600 }}>{s.name}</td>
                <td>
                  <span className="badge">{s.isIndustryCatalog ? 'INDUSTRY ($B)' : s.sourceFormat}</span>
                </td>
                <td style={{ color: '#94a3b8' }}>{s.eventCatalogId}</td>
                <td className="num">{s.numTrials.toLocaleString()}</td>
                <td className="num">{s.totalEvents.toLocaleString()}</td>
                <td className="num">{s.expectedAnnualLoss != null ? s.expectedAnnualLoss.toFixed(2) : '—'}</td>
                <td style={{ fontSize: 11, color: '#93c5fd' }}>{s.regionPerils.join(', ')}</td>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>#{s.contentHash.slice(0, 8)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-title">
          <span>Upload User-Supplied YELT (CSV or Parquet) with Column Mapper</span>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input type="file" accept=".csv,.parquet" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Simulation Set Name" style={{ width: 180 }} />
          <input value={trialCol} onChange={(e) => setTrialCol(e.target.value)} placeholder="Trial Col" style={{ width: 95 }} />
          <input value={eventCol} onChange={(e) => setEventCol(e.target.value)} placeholder="Event Col" style={{ width: 95 }} />
          <input value={lossCol} onChange={(e) => setLossCol(e.target.value)} placeholder="Loss Col" style={{ width: 105 }} />
          <input value={rpCol} onChange={(e) => setRpCol(e.target.value)} placeholder="Region-Peril Col" style={{ width: 125 }} />
          <input type="number" value={numTrials} onChange={(e) => setNumTrials(Number(e.target.value))} style={{ width: 85 }} />
          <button className="btn" disabled={!file || uploadMut.isPending} onClick={() => uploadMut.mutate()}>
            {uploadMut.isPending ? 'Ingesting...' : 'Ingest to Parquet'}
          </button>
        </div>
      </div>
    </>
  );
}
