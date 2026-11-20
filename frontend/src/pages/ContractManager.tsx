import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';

export function ContractManager({ onSelectProgramme }: { onSelectProgramme: (id: string) => void }) {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [showNew, setShowNew] = useState(false);
  const [cedentName, setCedentName] = useState('Coastal Shield Ins');
  const [programmeName, setProgrammeName] = useState('FL Peak Cat XL 2027');
  const [limit, setLimit] = useState(25);
  const [attachment, setAttachment] = useState(90);
  const [rol, setRol] = useState(0.21);
  const [regionPeril, setRegionPeril] = useState('US-FL-WS');

  const contractsQ = useQuery({
    queryKey: ['contracts', statusFilter, search],
    queryFn: () => api.listContracts({ status: statusFilter || undefined, search: search || undefined }),
  });

  const createMut = useMutation({
    mutationFn: () =>
      api.createContract({
        cedentName,
        programmeName,
        treatyYear: 2027,
        renewalPeriod: 'MY27',
        office: 'BDA',
        underwriter: 'J. Anfosso',
        broker: 'Guy Carpenter',
        status: 'QUOTED',
        direction: 'ASSUMED',
        structure: 'OCC_XL',
        limit,
        attachment,
        depositPremium: Number((limit * rol).toFixed(3)),
        rol,
        collateralRequired: limit,
        regionPerils: [regionPeril],
        perils: [regionPeril.endsWith('EQ') ? 'EQ' : 'WS'],
      }),
    onSuccess: () => {
      setShowNew(false);
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['programmes'] });
    },
  });

  const bindMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api.updateContract(id, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['programmes'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
    },
  });

  return (
    <div className="card">
      <div className="card-title">
        <span>Contract Management System (CMS) — Persistent Underwriting Ledger</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            placeholder="Search cedent, deal, broker..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 200 }}
          />
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All Statuses</option>
            <option value="BOUND">BOUND</option>
            <option value="QUOTED">QUOTED</option>
            <option value="FIRM_ORDER">FIRM_ORDER</option>
            <option value="PLANNED">PLANNED</option>
          </select>
          <button className="btn" onClick={() => setShowNew((v) => !v)}>
            {showNew ? 'Cancel' : '+ New Deal'}
          </button>
        </div>
      </div>

      {showNew && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: 10, background: '#0d1422', borderRadius: 6, marginBottom: 12, border: '1px solid #26324a' }}>
          <input value={cedentName} onChange={(e) => setCedentName(e.target.value)} placeholder="Cedent" />
          <input value={programmeName} onChange={(e) => setProgrammeName(e.target.value)} placeholder="Programme Name" style={{ width: 180 }} />
          <select value={regionPeril} onChange={(e) => setRegionPeril(e.target.value)}>
            <option value="US-FL-WS">US-FL-WS</option>
            <option value="US-TX-WS">US-TX-WS</option>
            <option value="US-SE-WS">US-SE-WS</option>
            <option value="US-CA-EQ">US-CA-EQ</option>
            <option value="JP-KANTO-EQ">JP-KANTO-EQ</option>
            <option value="EU-DE-WS">EU-DE-WS</option>
          </select>
          <label style={{ fontSize: 11, color: '#94a3b8' }}>
            Limit ($M)
            <input type="number" value={limit} onChange={(e) => setLimit(Number(e.target.value))} style={{ width: 65, marginLeft: 4 }} />
          </label>
          <label style={{ fontSize: 11, color: '#94a3b8' }}>
            Xs ($M)
            <input type="number" value={attachment} onChange={(e) => setAttachment(Number(e.target.value))} style={{ width: 65, marginLeft: 4 }} />
          </label>
          <label style={{ fontSize: 11, color: '#94a3b8' }}>
            ROL
            <input type="number" step="0.01" value={rol} onChange={(e) => setRol(Number(e.target.value))} style={{ width: 65, marginLeft: 4 }} />
          </label>
          <button className="btn" onClick={() => createMut.mutate()}>
            Save Contract
          </button>
        </div>
      )}

      <table>
        <thead>
          <tr>
            <th>Cedent</th>
            <th>Programme</th>
            <th>Dir / Struct</th>
            <th>Period</th>
            <th>UW / Office</th>
            <th>Status</th>
            <th className="num">Signed</th>
            <th className="num">Limit ($M)</th>
            <th className="num">Prem ($M)</th>
            <th className="num">ROL</th>
            <th>Region-Perils</th>
            <th className="num">Actions</th>
          </tr>
        </thead>
        <tbody>
          {contractsQ.data?.map((c) => (
            <tr key={c.programmeId}>
              <td style={{ fontWeight: 600 }}>{c.cedentName}</td>
              <td>{c.programmeName}</td>
              <td style={{ color: '#94a3b8', fontSize: 11 }}>
                {c.direction} · {c.structure}
              </td>
              <td>{c.renewalPeriod}</td>
              <td style={{ color: '#94a3b8' }}>
                {c.underwriter} ({c.office})
              </td>
              <td>
                <span className="badge">{c.status}</span>
              </td>
              <td className="num">{(c.signedLine * 100).toFixed(0)}%</td>
              <td className="num">${c.limit.toFixed(1)}m</td>
              <td className="num">${c.depositPremium.toFixed(2)}m</td>
              <td className="num">{(c.rol * 100).toFixed(2)}%</td>
              <td style={{ fontSize: 11, color: '#93c5fd' }}>{c.regionPerils.join(', ')}</td>
              <td className="num" style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                {c.status !== 'BOUND' && (
                  <button
                    className="btn"
                    style={{ padding: '3px 8px', fontSize: 11 }}
                    onClick={() => bindMut.mutate({ id: c.programmeId, status: 'BOUND' })}
                  >
                    Bind
                  </button>
                )}
                <button
                  className="btn btn-ghost"
                  style={{ padding: '3px 8px', fontSize: 11 }}
                  onClick={() => onSelectProgramme(c.programmeId)}
                >
                  Open DAG
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
