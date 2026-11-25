import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, apiUrl, fetchJson, postJson, type ProgrammeProfile } from '../api/client';
import { fmtInt, fmtMoneyB, fmtMoneyM, fmtPct, fmtPts, fmtRatioPct } from '../utils/format';

type DossierTab = 'profile' | 'notes' | 'pricing' | 'exposure';

interface RenewalChainRow {
  programmeId: string;
  uniqueCode: string;
  programmeName: string;
  treatyYear: number;
  renewalPeriod: string;
  status: string;
  priorContractId?: string | null;
  limit: number;
  attachment: number;
  depositPremium: number;
  rolPct: number;
  signedLine: number;
  rarcTotalPct: number;
  yoyLimitDeltaM?: number | null;
  yoyRolDeltaPts?: number | null;
  yoyPremiumDeltaM?: number | null;
  isCurrent: boolean;
}

interface ContractDossier {
  contract: any;
  relatedProgrammes: {
    programmeId: string;
    uniqueCode: string;
    programmeName: string;
    treatyYear: number;
    renewalPeriod: string;
    status: string;
    limit: number;
    rol: number;
  }[];
  renewalChain: RenewalChainRow[];
  notes: {
    noteId: string;
    programmeId: string;
    cedentName: string;
    author: string;
    category: string;
    body: string;
    createdAt: string;
  }[];
  pricingLink: {
    graphId: string;
    graphVersion: number;
    computeMs: number;
    unadjustedNodes: any[];
    adjustedNodes: any[];
  };
  exposureLink: {
    programmeId: string;
    cedentName: string;
    programmeName: string;
    edmDatabaseName: string;
    totalTivM: number;
    granularTivM: Record<string, number>;
    serverName: string;
  } | null;
  modelingLink: {
    simSet: any | null;
    upliftSet: any;
    modelRuns: any[];
  };
  claimsCount: number;
  recentManifests: {
    manifestId: string;
    runType: string;
    createdAt: string;
    outputHash: string;
    isStale: boolean;
  }[];
}

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

  // Selected programme for 360° Programme Profile & Linkage Dossier
  const [activeId, setActiveId] = useState<string>('prog-farmers-us-27');
  const [dossierTab, setDossierTab] = useState<DossierTab>('profile');
  const [draftProfile, setDraftProfile] = useState<ProgrammeProfile | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  // Underwriting notes composer
  const [noteScope, setNoteScope] = useState<'programme' | 'cedent'>('programme');
  const [noteCategory, setNoteCategory] = useState('UNDERWRITING');
  const [noteAuthor, setNoteAuthor] = useState('Lead Underwriter');
  const [noteBody, setNoteBody] = useState('');
  const [modelVendor, setModelVendor] = useState('RMS');

  const contractsQ = useQuery({
    queryKey: ['contracts', statusFilter, search],
    queryFn: () => api.listContracts({ status: statusFilter || undefined, search: search || undefined }),
  });

  const dossierQ = useQuery({
    queryKey: ['contractDossier', activeId],
    queryFn: () => fetchJson<ContractDossier>(`/api/contracts/${activeId}/dossier`),
    enabled: Boolean(activeId),
  });

  useEffect(() => {
    if (dossierQ.data?.contract?.profile) {
      setDraftProfile(dossierQ.data.contract.profile);
      setSavedMsg(null);
    }
  }, [dossierQ.data?.contract?.programmeId, dossierQ.data?.contract?.version]);

  const createMut = useMutation({
    mutationFn: () =>
      api.createContract({
        cedentName,
        programmeName,
        treatyYear: 2027,
        renewalPeriod: 'MY27',
        office: 'BDA',
        underwriter: 'Lead Underwriter',
        broker: 'Broker A',
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
    onSuccess: (created) => {
      setShowNew(false);
      setActiveId(created.programmeId);
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
      qc.invalidateQueries({ queryKey: ['contractDossier'] });
    },
  });

  const saveProfileMut = useMutation({
    mutationFn: () => api.updateContract(activeId, { profile: draftProfile }),
    onSuccess: () => {
      setSavedMsg('Programme profile saved & versioned.');
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['contractDossier', activeId] });
    },
  });

  const addNoteMut = useMutation({
    mutationFn: () =>
      postJson(`/api/contracts/${activeId}/notes`, {
        author: noteAuthor,
        category: noteCategory,
        body: noteBody,
      }),
    onSuccess: () => {
      setNoteBody('');
      qc.invalidateQueries({ queryKey: ['contractDossier', activeId] });
    },
  });

  const triggerModelMut = useMutation({
    mutationFn: () =>
      postJson('/api/model-runs', {
        vendor: modelVendor,
        programmeId: activeId,
        edmDatabaseName: dossierQ.data?.exposureLink?.edmDatabaseName ?? `EDM_${activeId.toUpperCase()}`,
        numTrials: 20000,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['contractDossier', activeId] });
      qc.invalidateQueries({ queryKey: ['modelRuns'] });
      qc.invalidateQueries({ queryKey: ['simSets'] });
    },
  });

  const syncEeMut = useMutation({
    mutationFn: () => postJson(`/api/integrations/exposure-eclipse/sync-bound/${activeId}`),
  });

  const linkPriorMut = useMutation({
    mutationFn: (priorContractId: string | null) =>
      postJson(`/api/contracts/${activeId}/link-prior`, { priorContractId }),
    onSuccess: () => {
      setSavedMsg('Year-over-year renewal link updated.');
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['contractDossier', activeId] });
    },
  });

  const renewMut = useMutation({
    mutationFn: () => postJson<any>(`/api/contracts/${activeId}/renew`, {}),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['programmes'] });
      if (created?.programmeId) {
        setActiveId(created.programmeId);
        setSavedMsg(`Renewed into ${created.treatyYear} (${created.profile?.uniqueCode || created.programmeId}) and linked YoY.`);
      }
    },
  });

  const d = dossierQ.data;
  const visibleNotes =
    d?.notes.filter((n) => (noteScope === 'cedent' ? true : n.programmeId === activeId)) ?? [];

  const termAdjNodes = (d?.pricingLink.adjustedNodes ?? []).filter((n) =>
    ['OCC_XL', 'AGG_XL', 'QUOTA_SHARE', 'AGG_QUOTA_SHARE', 'SURPLUS_SHARE', 'CASCADING_TOWER'].includes(n.nodeType),
  );
  const unadjById = Object.fromEntries((d?.pricingLink.unadjustedNodes ?? []).map((n) => [n.nodeId, n]));
  const totalAdjEl = termAdjNodes.reduce((s, n) => s + Number(n.el ?? 0), 0);

  return (
    <>
      <div className="card">
        <div className="card-title">
          <span>Programme Ledger</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
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

        {(createMut.error || saveProfileMut.error || addNoteMut.error || linkPriorMut.error || renewMut.error) && (
          <div className="error-banner">
            {String(
              ((createMut.error ?? saveProfileMut.error ?? addNoteMut.error ?? linkPriorMut.error ?? renewMut.error) as Error)
                .message,
            )}
          </div>
        )}

        {showNew && (
          <div
            style={{
              display: 'flex',
              gap: 8,
              flexWrap: 'wrap',
              padding: 10,
              background: 'var(--surface-sunken)',
              borderRadius: 6,
              marginBottom: 12,
              border: '1px solid var(--border-strong)',
            }}
          >
            <input value={cedentName} onChange={(e) => setCedentName(e.target.value)} placeholder="Cedent" />
            <input
              value={programmeName}
              onChange={(e) => setProgrammeName(e.target.value)}
              placeholder="Programme Name"
              style={{ width: 180 }}
            />
            <select value={regionPeril} onChange={(e) => setRegionPeril(e.target.value)}>
              <option value="US-FL-WS">US-FL-WS</option>
              <option value="US-TX-WS">US-TX-WS</option>
              <option value="US-SE-WS">US-SE-WS</option>
              <option value="US-CA-EQ">US-CA-EQ</option>
              <option value="JP-KANTO-EQ">JP-KANTO-EQ</option>
              <option value="EU-DE-WS">EU-DE-WS</option>
            </select>
            <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Limit ($M)
              <input
                type="number"
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                style={{ width: 65, marginLeft: 4 }}
              />
            </label>
            <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Xs ($M)
              <input
                type="number"
                value={attachment}
                onChange={(e) => setAttachment(Number(e.target.value))}
                style={{ width: 65, marginLeft: 4 }}
              />
            </label>
            <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              ROL (%)
              <input
                type="number"
                step="0.5"
                value={Number((rol * 100).toFixed(2))}
                onChange={(e) => setRol(Number(e.target.value) / 100)}
                style={{ width: 65, marginLeft: 4 }}
              />
            </label>
            <button className="btn" onClick={() => createMut.mutate()}>
              Save Contract
            </button>
          </div>
        )}

        <table>
          <thead>
            <tr>
              <th>Unique Code</th>
              <th>Cedent</th>
              <th>Programme</th>
              <th>Period</th>
              <th>UW / Office</th>
              <th>Status</th>
              <th className="num">Signed</th>
              <th className="num">Limit ($M)</th>
              <th className="num">Prem ($M)</th>
              <th className="num">ROL</th>
              <th className="num" title="Risk-Adjusted Rate Change">RARC</th>
              <th className="num">Actions</th>
            </tr>
          </thead>
          <tbody>
            {contractsQ.data?.map((c) => {
              const isSelected = c.programmeId === activeId;
              const rarc = c.profile?.rarcTotalPct ?? 0;
              return (
                <tr
                  key={c.programmeId}
                  onClick={() => setActiveId(c.programmeId)}
                  style={{
                    cursor: 'pointer',
                    background: isSelected ? 'rgba(56, 189, 248, 0.08)' : undefined,
                  }}
                >
                  <td style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--accent-soft)' }}>
                    {c.profile?.uniqueCode || c.programmeId}
                  </td>
                  <td style={{ fontWeight: 600 }}>{c.cedentName}</td>
                  <td>{c.programmeName}</td>
                  <td>{c.renewalPeriod}</td>
                  <td style={{ color: 'var(--text-muted)' }}>
                    {c.underwriter} ({c.office})
                  </td>
                  <td>
                    <span className={`badge status-${String(c.status).toLowerCase()}`}>{c.status}</span>
                  </td>
                  <td className="num">{fmtRatioPct(c.signedLine, 1)}</td>
                  <td className="num">{fmtMoneyM(c.limit, 1)}</td>
                  <td className="num">{fmtMoneyM(c.depositPremium, 2)}</td>
                  <td className="num">{fmtRatioPct(c.rol, 2)}</td>
                  <td className="num" style={{ color: rarc >= 0 ? 'var(--positive)' : 'var(--negative)', fontWeight: 600 }}>
                    {fmtPct(rarc, 1, { signed: true })}
                  </td>
                  <td
                    className="num"
                    style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}
                    onClick={(e) => e.stopPropagation()}
                  >
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
              );
            })}
          </tbody>
        </table>
      </div>

      {/* ══════════════════ 360° PROGRAMME PROFILE & LINKAGE DOSSIER ══════════════════ */}
      {d && draftProfile && (
        <div className="card">
          <div className="card-title">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontFamily: 'var(--mono)', color: 'var(--accent)' }}>
                {draftProfile.uniqueCode || d.contract.programmeId}
              </span>
              <span>
                {d.contract.cedentName} — {d.contract.programmeName}
              </span>
              <span className={`badge status-${String(d.contract.status).toLowerCase()}`}>{d.contract.status}</span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {draftProfile.tradingUnit} · {draftProfile.planningClass} · {draftProfile.cedentCreditRating}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <button
                className={`tab ${dossierTab === 'profile' ? 'active' : ''}`}
                onClick={() => setDossierTab('profile')}
              >
                Unique Code &amp; RARC
              </button>
              <button
                className={`tab ${dossierTab === 'notes' ? 'active' : ''}`}
                onClick={() => setDossierTab('notes')}
              >
                Underwriting Notes ({d.notes.length})
              </button>
              <button
                className={`tab ${dossierTab === 'pricing' ? 'active' : ''}`}
                onClick={() => setDossierTab('pricing')}
              >
                Pricing &amp; Modeling
              </button>
              <button
                className={`tab ${dossierTab === 'exposure' ? 'active' : ''}`}
                onClick={() => setDossierTab('exposure')}
              >
                Exposure &amp; Audit
              </button>
            </div>
          </div>

          {/* Executive Programme KPI Ribbon */}
          <div className="grid-kpi">
            <div className="kpi">
              <div className="kpi-label">Exposure / Limit</div>
              <div className="kpi-val">{fmtMoneyM(d.contract.limit, 1)}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">GWP / Deposit</div>
              <div className="kpi-val">{fmtMoneyM(d.contract.depositPremium, 2)}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">ROL / Tech ROL</div>
              <div className="kpi-val">
                {fmtRatioPct(d.contract.rol, 1)}
                {d.contract.technicalRol ? ` / ${fmtRatioPct(d.contract.technicalRol, 1)}` : ''}
              </div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Risk-Adj Rate Change (RARC)</div>
              <div className="kpi-val" style={{ color: draftProfile.rarcTotalPct >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
                {fmtPct(draftProfile.rarcTotalPct, 2, { signed: true })}
              </div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Modelled EL (Adj)</div>
              <div className="kpi-val" style={{ color: 'var(--accent)' }}>
                {fmtMoneyM(totalAdjEl, 2)}
              </div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Signed / Written</div>
              <div className="kpi-val">
                {fmtRatioPct(d.contract.signedLine, 1)} / {fmtRatioPct(d.contract.writtenLine, 1)}
              </div>
            </div>
          </div>

          {/* ── Sub-tab 1: Unique Code & RARC ── */}
          {dossierTab === 'profile' && (
            <>
              {savedMsg && (
                <div
                  style={{
                    background: 'rgba(34, 197, 94, 0.12)',
                    border: '1px solid rgba(34, 197, 94, 0.4)',
                    color: '#7fe0a9',
                    borderRadius: 6,
                    padding: '6px 10px',
                    fontSize: 12,
                    marginBottom: 10,
                  }}
                >
                  {savedMsg}
                </div>
              )}
              <div className="grid-2" style={{ marginBottom: 12 }}>
                <div className="panel">
                  <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 10 }}>
                    Programme Identification, Counterparty &amp; Team
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 8 }}>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Unique Code
                      <input
                        value={draftProfile.uniqueCode}
                        onChange={(e) => setDraftProfile({ ...draftProfile, uniqueCode: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Program Code
                      <input
                        value={draftProfile.programCode}
                        onChange={(e) => setDraftProfile({ ...draftProfile, programCode: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Credit Rating
                      <input
                        value={draftProfile.cedentCreditRating}
                        onChange={(e) => setDraftProfile({ ...draftProfile, cedentCreditRating: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Trading Unit
                      <input
                        value={draftProfile.tradingUnit}
                        onChange={(e) => setDraftProfile({ ...draftProfile, tradingUnit: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Closing Broker
                      <input
                        value={draftProfile.closingBroker ?? ''}
                        onChange={(e) => setDraftProfile({ ...draftProfile, closingBroker: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Broker Contact
                      <input
                        value={draftProfile.brokerContact ?? ''}
                        onChange={(e) => setDraftProfile({ ...draftProfile, brokerContact: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Pricing Analyst
                      <input
                        value={draftProfile.pricingAnalyst ?? ''}
                        onChange={(e) => setDraftProfile({ ...draftProfile, pricingAnalyst: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Claim Trigger
                      <select
                        value={draftProfile.claimTrigger}
                        onChange={(e) => setDraftProfile({ ...draftProfile, claimTrigger: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      >
                        <option value="LOSSES_OCCURRING">Losses Occurring</option>
                        <option value="RISK_ATTACHING">Risk Attaching</option>
                      </select>
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Reinstatement Basis
                      <select
                        value={draftProfile.reinstatementBasis}
                        onChange={(e) => setDraftProfile({ ...draftProfile, reinstatementBasis: e.target.value })}
                        style={{ width: '100%', marginTop: 3 }}
                      >
                        <option value="PRO_RATA_AMOUNT">Pro-Rata Amount</option>
                        <option value="PRO_RATA_TIME">Pro-Rata Time</option>
                        <option value="FREE">Free</option>
                      </select>
                    </label>
                  </div>
                  <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
                    Inuring / Wording Summary
                    <input
                      value={draftProfile.inuringNote ?? ''}
                      onChange={(e) => setDraftProfile({ ...draftProfile, inuringNote: e.target.value })}
                      style={{ width: '100%', marginTop: 3 }}
                    />
                  </label>
                  <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
                    External Slip / Document Folder Path
                    <input
                      value={draftProfile.externalFilePath ?? ''}
                      onChange={(e) => setDraftProfile({ ...draftProfile, externalFilePath: e.target.value })}
                      style={{ width: '100%', marginTop: 3 }}
                    />
                  </label>
                </div>

                {/* Right: Risk-Adjusted Rate Change (RARC) & Prior UY Comparison */}
                <div className="panel">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                    <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
                      Risk-Adjusted Rate Change (RARC) &amp; YoY Renewal Comparison
                    </span>
                    <button
                      className="btn"
                      onClick={() => saveProfileMut.mutate()}
                      disabled={saveProfileMut.isPending}
                    >
                      {saveProfileMut.isPending ? 'Saving…' : 'Save Programme Profile'}
                    </button>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8, marginBottom: 12 }}>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Econ Inflation %
                      <input
                        type="number"
                        step="0.1"
                        value={draftProfile.economicInflationPct}
                        onChange={(e) => setDraftProfile({ ...draftProfile, economicInflationPct: Number(e.target.value) })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Social Inflation %
                      <input
                        type="number"
                        step="0.1"
                        value={draftProfile.socialInflationPct}
                        onChange={(e) => setDraftProfile({ ...draftProfile, socialInflationPct: Number(e.target.value) })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Gearing Effect %
                      <input
                        type="number"
                        step="0.1"
                        value={draftProfile.gearingEffectPct}
                        onChange={(e) => setDraftProfile({ ...draftProfile, gearingEffectPct: Number(e.target.value) })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Exposure Δ %
                      <input
                        type="number"
                        step="0.1"
                        value={draftProfile.rarcExposurePct}
                        onChange={(e) => setDraftProfile({ ...draftProfile, rarcExposurePct: Number(e.target.value) })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Layer Δ %
                      <input
                        type="number"
                        step="0.1"
                        value={draftProfile.rarcLayerPct}
                        onChange={(e) => setDraftProfile({ ...draftProfile, rarcLayerPct: Number(e.target.value) })}
                        style={{ width: '100%', marginTop: 3 }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      RARC Total %
                      <input
                        type="number"
                        step="0.1"
                        value={draftProfile.rarcTotalPct}
                        onChange={(e) => setDraftProfile({ ...draftProfile, rarcTotalPct: Number(e.target.value) })}
                        style={{ width: '100%', marginTop: 3, fontWeight: 700 }}
                      />
                    </label>
                  </div>

                  <table>
                    <thead>
                      <tr>
                        <th>Underwriting Year</th>
                        <th className="num">Limit</th>
                        <th className="num">GWP</th>
                        <th className="num">ROL</th>
                        <th className="num">Signed</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td style={{ color: 'var(--text-muted)' }}>Previous UY ({d.contract.treatyYear - 1})</td>
                        <td className="num">
                          {draftProfile.priorYearLimitM != null ? `$${draftProfile.priorYearLimitM.toFixed(1)}m` : '—'}
                        </td>
                        <td className="num">
                          {draftProfile.priorYearPremiumM != null ? `$${draftProfile.priorYearPremiumM.toFixed(2)}m` : '—'}
                        </td>
                        <td className="num">
                          {draftProfile.priorYearRolPct != null ? `${draftProfile.priorYearRolPct.toFixed(2)}%` : '—'}
                        </td>
                        <td className="num">
                          {draftProfile.priorYearSignedLine != null ? `${(draftProfile.priorYearSignedLine * 100).toFixed(0)}%` : '—'}
                        </td>
                      </tr>
                      <tr>
                        <td style={{ fontWeight: 600 }}>Current UY ({d.contract.treatyYear})</td>
                        <td className="num" style={{ fontWeight: 600 }}>${d.contract.limit.toFixed(1)}m</td>
                        <td className="num" style={{ fontWeight: 600 }}>${d.contract.depositPremium.toFixed(2)}m</td>
                        <td className="num" style={{ fontWeight: 600 }}>{(d.contract.rol * 100).toFixed(2)}%</td>
                        <td className="num" style={{ fontWeight: 600 }}>{(d.contract.signedLine * 100).toFixed(0)}%</td>
                      </tr>
                    </tbody>
                  </table>

                  {d.relatedProgrammes.length > 0 && (
                    <div style={{ marginTop: 10 }}>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                        Other Programmes for {d.contract.cedentName}:
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {d.relatedProgrammes.map((rp) => (
                          <button
                            key={rp.programmeId}
                            className="btn btn-ghost"
                            style={{ fontSize: 11, padding: '3px 8px' }}
                            onClick={() => setActiveId(rp.programmeId)}
                          >
                            {rp.uniqueCode || rp.programmeId} · {rp.programmeName} ({rp.status})
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Full Year-over-Year Renewal Chain Panel */}
              <div className="panel">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    gap: 8,
                    marginBottom: 10,
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
                    Year-over-Year Renewal Chain ({d.renewalChain?.length ?? 0} linked underwriting years)
                  </span>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
                      Prior Year Link:
                      <select
                        value={d.contract.priorContractId ?? ''}
                        onChange={(e) => linkPriorMut.mutate(e.target.value || null)}
                        disabled={linkPriorMut.isPending}
                      >
                        <option value="">None (Start of Chain)</option>
                        {(contractsQ.data ?? [])
                          .filter((other) => other.programmeId !== activeId)
                          .map((other) => (
                            <option key={other.programmeId} value={other.programmeId}>
                              {other.treatyYear} · {other.profile?.uniqueCode || other.programmeId} — {other.cedentName} ({other.status})
                            </option>
                          ))}
                      </select>
                    </label>
                    <button
                      className="btn"
                      style={{ background: 'var(--teal-strong)' }}
                      onClick={() => renewMut.mutate()}
                      disabled={renewMut.isPending}
                    >
                      {renewMut.isPending ? 'Renewing…' : `Renew → ${d.contract.treatyYear + 1}`}
                    </button>
                  </div>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>UY</th>
                      <th>Unique Code</th>
                      <th>Programme</th>
                      <th>Status</th>
                      <th className="num">Limit ($M)</th>
                      <th className="num">YoY Lim Δ</th>
                      <th className="num">GWP ($M)</th>
                      <th className="num">ROL</th>
                      <th className="num">YoY ROL Δ</th>
                      <th className="num">Signed</th>
                      <th className="num">RARC</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(d.renewalChain ?? []).map((row) => (
                      <tr
                        key={row.programmeId}
                        onClick={() => setActiveId(row.programmeId)}
                        style={{
                          cursor: 'pointer',
                          background: row.isCurrent ? 'rgba(56, 189, 248, 0.10)' : undefined,
                        }}
                      >
                        <td style={{ fontWeight: 700 }}>{row.treatyYear} ({row.renewalPeriod})</td>
                        <td style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--accent-soft)' }}>{row.uniqueCode}</td>
                        <td>{row.programmeName}</td>
                        <td><span className={`badge status-${String(row.status).toLowerCase()}`}>{row.status}</span></td>
                        <td className="num">{fmtMoneyM(row.limit, 1)}</td>
                        <td className="num" style={{ color: row.yoyLimitDeltaM == null ? 'var(--text-faint)' : row.yoyLimitDeltaM >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
                          {fmtMoneyM(row.yoyLimitDeltaM, 1, { signed: true })}
                        </td>
                        <td className="num">{fmtMoneyM(row.depositPremium, 2)}</td>
                        <td className="num">{fmtPct(row.rolPct, 2)}</td>
                        <td className="num" style={{ color: row.yoyRolDeltaPts == null ? 'var(--text-faint)' : row.yoyRolDeltaPts >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
                          {fmtPts(row.yoyRolDeltaPts, 2)}
                        </td>
                        <td className="num">{fmtRatioPct(row.signedLine, 1)}</td>
                        <td className="num" style={{ color: row.rarcTotalPct >= 0 ? 'var(--positive)' : 'var(--negative)', fontWeight: 600 }}>
                          {fmtPct(row.rarcTotalPct, 1, { signed: true })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {/* ── Sub-tab 2: Underwriting & Discussion Notes ── */}
          {dossierTab === 'notes' && (
            <div className="grid-2">
              <div className="panel">
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 8 }}>
                  Add Underwriting / Pricing / Broker Note
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                  <select value={noteCategory} onChange={(e) => setNoteCategory(e.target.value)}>
                    <option value="UNDERWRITING">UNDERWRITING</option>
                    <option value="PRICING">PRICING</option>
                    <option value="BROKER">BROKER</option>
                    <option value="MODELING">MODELING</option>
                    <option value="CLAIMS">CLAIMS</option>
                  </select>
                  <input
                    value={noteAuthor}
                    onChange={(e) => setNoteAuthor(e.target.value)}
                    placeholder="Author"
                    style={{ width: 140 }}
                  />
                </div>
                <textarea
                  rows={4}
                  value={noteBody}
                  onChange={(e) => setNoteBody(e.target.value)}
                  placeholder="Record underwriting rationale, broker call notes, attachment/reinstatement negotiations, or model adjustments..."
                  style={{ width: '100%', marginBottom: 8 }}
                />
                <button
                  className="btn"
                  onClick={() => addNoteMut.mutate()}
                  disabled={!noteBody.trim() || addNoteMut.isPending}
                >
                  {addNoteMut.isPending ? 'Posting…' : 'Post Timestamped Note'}
                </button>
              </div>

              <div className="panel">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>Discussion Trail</span>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      className={`tab ${noteScope === 'programme' ? 'active' : ''}`}
                      onClick={() => setNoteScope('programme')}
                    >
                      This Programme
                    </button>
                    <button
                      className={`tab ${noteScope === 'cedent' ? 'active' : ''}`}
                      onClick={() => setNoteScope('cedent')}
                    >
                      Same Cedent
                    </button>
                  </div>
                </div>
                {visibleNotes.length === 0 ? (
                  <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>No notes recorded yet.</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {visibleNotes.map((n) => (
                      <div
                        key={n.noteId}
                        style={{
                          background: 'var(--surface)',
                          border: '1px solid var(--border-strong)',
                          borderRadius: 6,
                          padding: 10,
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
                          <span>
                            <span className="badge" style={{ marginRight: 6 }}>
                              {n.category}
                            </span>
                            <strong>{n.author}</strong>
                          </span>
                          <span style={{ color: 'var(--text-faint)' }}>
                            {new Date(n.createdAt).toLocaleString()}
                          </span>
                        </div>
                        <div style={{ fontSize: 12, lineHeight: 1.45 }}>{n.body}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ── Sub-tab 3: Live Pricing & Modeling Linkage ── */}
          {dossierTab === 'pricing' && (
            <div className="grid-2">
              <div className="panel">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    gap: 8,
                    marginBottom: 8,
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
                    Linked Pricing DAG ({d.pricingLink.graphId} v{d.pricingLink.graphVersion})
                  </span>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="btn" onClick={() => onSelectProgramme(activeId)}>
                      Open in DAG Studio
                    </button>
                    <a
                      className="btn btn-ghost"
                      style={{ textDecoration: 'none' }}
                      href={apiUrl(`/api/exports/programme-tearsheet/${activeId}`)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Deal Tear-Sheet (PDF)
                    </a>
                  </div>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>Layer</th>
                      <th className="num">Unadj EL</th>
                      <th className="num">Adj EL</th>
                      <th className="num">Exp RIP</th>
                      <th className="num">1-in-250 TVaR</th>
                      <th className="num">Adj ELR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {termAdjNodes.map((a) => {
                      const u = unadjById[a.nodeId];
                      const tvar250 = a.epPoints?.find((p: any) => Math.abs(p.returnPeriod - 250) < 1e-6)?.tvar ?? 0;
                      return (
                        <tr key={a.nodeId}>
                          <td style={{ fontWeight: 600 }}>{a.nodeLabel}</td>
                          <td className="num">{fmtMoneyM(u?.el ?? 0, 3)}</td>
                          <td className="num" style={{ color: 'var(--accent)', fontWeight: 600 }}>
                            {fmtMoneyM(a.el, 3)}
                          </td>
                          <td className="num">{fmtMoneyM(a.expectedRip, 3)}</td>
                          <td className="num">{fmtMoneyM(tvar250, 2)}</td>
                          <td className="num">{fmtRatioPct(a.elr, 1)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="panel">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    gap: 8,
                    marginBottom: 10,
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
                    Linked Simulation Set, Uplifts &amp; Cat Model Runs
                  </span>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <select value={modelVendor} onChange={(e) => setModelVendor(e.target.value)}>
                      <option value="RMS">RMS IRP</option>
                      <option value="AIR">Verisk AIR</option>
                      <option value="KCC">KCC RiskInsight</option>
                      <option value="OASIS">Oasis LMF</option>
                    </select>
                    <button
                      className="btn"
                      onClick={() => triggerModelMut.mutate()}
                      disabled={triggerModelMut.isPending}
                    >
                      {triggerModelMut.isPending ? 'Running…' : 'Trigger Model Run'}
                    </button>
                  </div>
                </div>

                {d.modelingLink.simSet && (
                  <div
                    style={{
                      background: 'var(--surface)',
                      border: '1px solid var(--border-strong)',
                      borderRadius: 6,
                      padding: 10,
                      marginBottom: 10,
                      fontSize: 12,
                    }}
                  >
                    <div>
                      <strong>SimSet:</strong> {d.modelingLink.simSet.name}{' '}
                      <code style={{ color: 'var(--teal)' }}>#{d.modelingLink.simSet.contentHash?.slice(0, 8)}</code>
                    </div>
                    <div style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 3 }}>
                      Catalog: {d.modelingLink.simSet.eventCatalogId} ·{' '}
                      {fmtInt(d.modelingLink.simSet.numTrials)} trials ·{' '}
                      {fmtInt(d.modelingLink.simSet.totalEvents)} events
                    </div>
                    <div style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 3 }}>
                      <strong>House Uplift Set:</strong> {d.modelingLink.upliftSet.name} (v
                      {d.modelingLink.upliftSet.version}) —{' '}
                      {d.modelingLink.upliftSet.rules
                        .map((r: any) => `${r.regionPerilKey} ×${r.flatSeverityFactor}`)
                        .join(', ') || 'None'}
                    </div>
                  </div>
                )}

                {d.modelingLink.modelRuns.length === 0 ? (
                  <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>
                    No vendor API runs triggered for this programme yet. Use &quot;Trigger Model Run&quot; above.
                  </div>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>Job</th>
                        <th>Vendor</th>
                        <th>EDM</th>
                        <th>Status</th>
                        <th>SimSet</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.modelingLink.modelRuns.map((j) => (
                        <tr key={j.jobId}>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{j.jobId}</td>
                          <td>
                            {j.vendor} {j.modelVersion}
                          </td>
                          <td style={{ color: 'var(--text-muted)' }}>{j.edmDatabaseName}</td>
                          <td className="pass">{j.status}</td>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{j.simSetId}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {/* ── Sub-tab 4: ExposureEclipse & Audit Linkage ── */}
          {dossierTab === 'exposure' && (
            <div className="grid-2">
              <div className="panel">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    gap: 8,
                    marginBottom: 10,
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
                    ExposureEclipse (Peril Vista) Linkage
                  </span>
                  <button
                    className="btn"
                    style={{ background: 'var(--teal-strong)' }}
                    onClick={() => syncEeMut.mutate()}
                    disabled={syncEeMut.isPending}
                  >
                    {syncEeMut.isSuccess ? 'Synced to ExposureEclipse' : 'Sync Bound → ExposureEclipse'}
                  </button>
                </div>
                {d.exposureLink ? (
                  <>
                    <div style={{ fontSize: 12, marginBottom: 8 }}>
                      <strong>EDM:</strong> <code>{d.exposureLink.edmDatabaseName}</code> ·{' '}
                      <strong>Total TIV:</strong> {fmtMoneyB(d.exposureLink.totalTivM / 1000, 2)} ·{' '}
                      <span style={{ color: 'var(--text-muted)' }}>Server: {d.exposureLink.serverName}</span>
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th>Region-Peril</th>
                          <th className="num">Exposed TIV ($M)</th>
                          <th className="num">Share of TIV</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(d.exposureLink.granularTivM ?? {}).map(([rp, tiv]) => (
                          <tr key={rp}>
                            <td style={{ color: 'var(--accent-soft)', fontWeight: 600 }}>{rp}</td>
                            <td className="num">{fmtMoneyM(Number(tiv), 0)}</td>
                            <td className="num">
                              {fmtPct((Number(tiv) / Math.max(d.exposureLink!.totalTivM, 1)) * 100, 1)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : (
                  <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>
                    No ExposureEclipse programme linked yet. Link an EDM via <code>exposureEclipseProgrammeId</code>.
                  </div>
                )}
              </div>

              <div className="panel">
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)', marginBottom: 10 }}>
                  Audit Trail, Claims ({d.claimsCount}) &amp; Breadcrumb Manifests
                </div>
                {d.recentManifests.length === 0 ? (
                  <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>
                    No persisted run manifests for this programme yet — evaluate in DAG Studio or Burn &amp; Claims.
                  </div>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>Manifest ID</th>
                        <th>Run Type</th>
                        <th>Output Hash</th>
                        <th className="num">Freshness</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.recentManifests.map((m) => (
                        <tr key={m.manifestId}>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{m.manifestId}</td>
                          <td>{m.runType}</td>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                            #{m.outputHash.slice(0, 10)}
                          </td>
                          <td className={`num ${m.isStale ? 'fail' : 'pass'}`}>{m.isStale ? 'STALE' : 'FRESH'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
