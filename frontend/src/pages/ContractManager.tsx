import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, apiUrl, fetchJson, postJson, type ContractFull, type ProgrammeProfile } from '../api/client';
import { ContractLayersEditor } from '../components/ContractLayersEditor';
import { fmtInt, fmtMoneyB, fmtMoneyM, fmtPct, fmtPts, fmtRatioPct } from '../utils/format';
import { nextLayerCode } from '../utils/nodeLabels';
import { matchesNumberFilter, matchesTextFilter } from '../utils/tableFilter';

type DossierTab = 'profile' | 'layers' | 'notes' | 'pricing' | 'exposure';

interface LedgerColumn {
  key: string;
  label: string;
  title?: string;
  kind: 'text' | 'num' | 'select';
  get: (c: ContractFull) => string | number | null | undefined;
}

const LAYER_NODE_TYPES = ['OCC_XL', 'AGG_XL', 'QUOTA_SHARE', 'AGG_QUOTA_SHARE', 'SURPLUS_SHARE', 'CASCADING_TOWER', 'ILW_OPTION'];
const layerCount = (c: ContractFull) => c.graph?.nodes.filter((n) => LAYER_NODE_TYPES.includes(n.nodeType)).length ?? 0;
const isHedgeContract = (c: { isHedge?: boolean; direction?: string }) =>
  Boolean(c.isHedge) || c.direction === 'EXTERNAL_RETRO' || c.direction === 'INTERNAL_CEDED';
const contractTypeLabel = (c: { structure?: string; instrument?: string; isHedge?: boolean; direction?: string }) => {
  if (c.structure === 'ILW' || c.instrument === 'INDUSTRY_INDEX') return 'ILW';
  if (isHedgeContract(c)) return 'Retro UNL';
  return 'UNL';
};

// Numeric filters are entered in the units shown: percentages as 0–100, money in $M.
const LEDGER_COLUMNS: LedgerColumn[] = [
  { key: 'code', label: 'Code', title: 'Unique code', kind: 'text', get: (c) => c.profile?.uniqueCode || c.programmeId },
  { key: 'cedent', label: 'Cedent', kind: 'text', get: (c) => c.cedentName },
  { key: 'programme', label: 'Programme', kind: 'text', get: (c) => c.programmeName },
  { key: 'dir', label: 'Dir', title: 'Long (sell / assumed) vs Hedge (buy / ceded)', kind: 'select', get: (c) => (isHedgeContract(c) ? 'Hedge' : 'Long') },
  { key: 'type', label: 'Type', title: 'Programme basis (UNL, ILW, Retro UNL)', kind: 'select', get: (c) => contractTypeLabel(c) },
  { key: 'period', label: 'Per', title: 'Renewal period', kind: 'text', get: (c) => c.renewalPeriod },
  { key: 'uw', label: 'UW', title: 'Underwriter and office', kind: 'text', get: (c) => `${c.underwriter} (${c.office})` },
  { key: 'status', label: 'Status', kind: 'select', get: (c) => String(c.status) },
  { key: 'written', label: 'Writ', title: 'Written line, percent', kind: 'num', get: (c) => c.writtenLine * 100 },
  { key: 'signed', label: 'Sign', title: 'Signed line, percent', kind: 'num', get: (c) => c.signedLine * 100 },
  { key: 'layers', label: 'Lyr', title: 'Number of layers', kind: 'num', get: (c) => layerCount(c) },
  { key: 'limit', label: 'Lim', title: 'Limit, $M', kind: 'num', get: (c) => c.limit },
  { key: 'prem', label: 'Prem', title: 'Deposit premium, $M', kind: 'num', get: (c) => c.depositPremium },
  { key: 'rol', label: 'ROL', title: 'Rate on line, percent', kind: 'num', get: (c) => c.rol * 100 },
  { key: 'rarc', label: 'RARC', title: 'Risk-adjusted rate change', kind: 'num', get: (c) => c.profile?.rarcTotalPct ?? 0 },
];

interface NewDealLayer {
  layerCode: string;
  name: string;
  layerType: 'OCC_XL' | 'AGG_XL';
  limit: number;
  attachment: number;
  rolPct: number;
  brokeragePct: number;
  writtenPct: number;
  signedPct: number;
  rarcPct: string;
}

function nextLayer(prev: NewDealLayer, all: NewDealLayer[]): NewDealLayer {
  return {
    layerCode: nextLayerCode(all.map((l) => l.layerCode)),
    name: '',
    layerType: prev.layerType,
    limit: prev.limit,
    attachment: prev.attachment + prev.limit,
    rolPct: Number(Math.max(prev.rolPct * 0.65, 1).toFixed(2)),
    brokeragePct: prev.brokeragePct,
    writtenPct: prev.writtenPct,
    signedPct: prev.signedPct,
    rarcPct: prev.rarcPct,
  };
}

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
    programmeLossRatio?: number | null;
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
  const [colFilters, setColFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'code', dir: 'asc' });
  const [showNew, setShowNew] = useState(false);
  const [cedentName, setCedentName] = useState('Coastal Shield Ins');
  const [programmeName, setProgrammeName] = useState('FL Peak Cat XL 2027');
  const [newDirection, setNewDirection] = useState<'ASSUMED' | 'EXTERNAL_RETRO'>('ASSUMED');
  const [newDealBasis, setNewDealBasis] = useState<'UNL' | 'ILW'>('UNL');
  const [newIlwTriggerType, setNewIlwTriggerType] = useState<'BINARY' | 'PRO_RATA' | 'AGGREGATE' | 'SECOND_EVENT'>('BINARY');
  const [newSnapshotDate, setNewSnapshotDate] = useState('2027-01-01');
  const [newLayers, setNewLayers] = useState<NewDealLayer[]>([
    { layerCode: 'L1', name: '', layerType: 'OCC_XL', limit: 25, attachment: 90, rolPct: 21, brokeragePct: 10, writtenPct: 15, signedPct: 10, rarcPct: '' },
  ]);
  const [regionPeril, setRegionPeril] = useState('US-FL-WS');

  // Selected programme for 360° Programme Profile & Linkage Dossier
  const [activeId, setActiveId] = useState<string>('prog-farmers-us-27');
  const [dossierTab, setDossierTab] = useState<DossierTab>('profile');
  const [draftProfile, setDraftProfile] = useState<ProgrammeProfile | null>(null);
  const [draftDirection, setDraftDirection] = useState<'ASSUMED' | 'EXTERNAL_RETRO' | 'INTERNAL_CEDED'>('ASSUMED');
  const [draftStructure, setDraftStructure] = useState<string>('OCC_XL');
  const [draftSnapshotDate, setDraftSnapshotDate] = useState<string>('2027-01-01');
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  // Underwriting notes composer
  const [noteScope, setNoteScope] = useState<'programme' | 'cedent'>('programme');
  const [noteCategory, setNoteCategory] = useState('UNDERWRITING');
  const [noteAuthor, setNoteAuthor] = useState('Lead Underwriter');
  const [noteBody, setNoteBody] = useState('');
  const [modelVendor, setModelVendor] = useState('RMS');

  const contractsQ = useQuery({
    queryKey: ['contracts'],
    queryFn: () => api.listContracts(),
  });

  const selectOptionsByCol: Record<string, string[]> = {
    status: Array.from(new Set((contractsQ.data ?? []).map((c) => String(c.status)))).sort(),
    dir: ['Long', 'Hedge'],
    type: ['UNL', 'ILW', 'Retro UNL'],
  };
  const activeFilterCount = Object.values(colFilters).filter((v) => v.trim()).length;
  const visibleContracts = (() => {
    const q = search.trim().toLowerCase();
    const rows = (contractsQ.data ?? []).filter((c) => {
      if (q && !LEDGER_COLUMNS.some((col) => String(col.get(c) ?? '').toLowerCase().includes(q)) &&
        !`${c.broker} ${c.profile?.closingBroker ?? ''}`.toLowerCase().includes(q)) {
        return false;
      }
      return LEDGER_COLUMNS.every((col) => {
        const f = colFilters[col.key] ?? '';
        if (!f.trim()) return true;
        const v = col.get(c);
        if (col.kind === 'num') return matchesNumberFilter(v as number, f);
        if (col.kind === 'select') return String(v) === f;
        return matchesTextFilter(String(v ?? ''), f);
      });
    });
    const col = LEDGER_COLUMNS.find((x) => x.key === sort.key);
    if (col) {
      const mul = sort.dir === 'asc' ? 1 : -1;
      rows.sort((a, b) => {
        const va = col.get(a);
        const vb = col.get(b);
        if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * mul;
        return String(va ?? '').localeCompare(String(vb ?? ''), undefined, { numeric: true }) * mul;
      });
    }
    return rows;
  })();

  const dossierQ = useQuery({
    queryKey: ['contractDossier', activeId],
    queryFn: () => fetchJson<ContractDossier>(`/api/contracts/${activeId}/dossier`),
    enabled: Boolean(activeId),
  });

  useEffect(() => {
    if (dossierQ.data?.contract?.profile) {
      setDraftProfile(dossierQ.data.contract.profile);
      const rawDir = String(dossierQ.data.contract.direction || 'ASSUMED');
      setDraftDirection(
        rawDir === 'INTERNAL_CEDED' || rawDir === 'EXTERNAL_RETRO' ? rawDir : 'ASSUMED',
      );
      setDraftStructure(String(dossierQ.data.contract.structure || 'OCC_XL'));
      setDraftSnapshotDate(String(dossierQ.data.contract.portfolioSnapshotDate || dossierQ.data.contract.inceptionDate || '2027-01-01'));
      setSavedMsg(null);
    }
  }, [dossierQ.data?.contract?.programmeId, dossierQ.data?.contract?.version]);

  const newDealTotals = {
    limit: newLayers.reduce((sum, l) => sum + (l.limit || 0), 0),
    premium: Number(newLayers.reduce((sum, l) => sum + ((l.limit || 0) * (l.rolPct || 0)) / 100, 0).toFixed(4)),
    attachment: Math.min(...newLayers.map((l) => l.attachment || 0)),
  };
  const pctOk = (v: number, max = 100) => Number.isFinite(v) && v >= 0 && v <= max;
  const rarcOk = (raw: string) => {
    if (raw.trim() === '') return true;
    const n = Number(raw);
    return Number.isFinite(n) && n >= -100 && n <= 500;
  };
  const newLayerCodesNormalized = newLayers.map((l) => l.layerCode.trim().toLowerCase());
  const newLayerCodesUnique =
    newLayerCodesNormalized.every(Boolean) && new Set(newLayerCodesNormalized).size === newLayerCodesNormalized.length;
  const newDealValid =
    newLayerCodesUnique &&
    newLayers.every(
      (l) =>
        l.limit > 0 &&
        l.attachment >= 0 &&
        l.rolPct >= 0 &&
        pctOk(l.brokeragePct, 90) &&
        pctOk(l.writtenPct) &&
        pctOk(l.signedPct) &&
        rarcOk(l.rarcPct),
    ) &&
    cedentName.trim() &&
    programmeName.trim();
  const setNewLayer = (i: number, patch: Partial<NewDealLayer>) =>
    setNewLayers((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));

  const createMut = useMutation({
    mutationFn: () => {
      const isIlw = newDealBasis === 'ILW';
      const isHedge = newDirection === 'EXTERNAL_RETRO';
      const rps = regionPeril === 'ALL' ? [] : [regionPeril];
      return api.createContract({
        cedentName,
        programmeName,
        treatyYear: 2027,
        renewalPeriod: 'MY27',
        office: 'BDA',
        underwriter: 'Lead Underwriter',
        broker: 'Broker A',
        status: 'QUOTED',
        direction: newDirection,
        isHedge,
        structure: isIlw ? 'ILW' : newLayers[0].layerType,
        instrument: isIlw ? 'INDUSTRY_INDEX' : 'UNL',
        portfolioSnapshotDate: !isIlw && isHedge ? newSnapshotDate || '2027-01-01' : null,
        limit: newDealTotals.limit,
        attachment: newDealTotals.attachment,
        depositPremium: newDealTotals.premium,
        rol: newDealTotals.limit ? newDealTotals.premium / newDealTotals.limit : 0,
        collateralRequired: isHedge ? 0 : newDealTotals.limit,
        layers: newLayers.map((l) => ({
          layerCode: l.layerCode.trim() || undefined,
          name: l.name.trim() || undefined,
          layerType: isIlw ? 'ILW_OPTION' : l.layerType,
          limit: l.limit,
          attachment: l.attachment,
          depositPremium: Number(((l.limit * l.rolPct) / 100).toFixed(4)),
          triggerType: isIlw ? newIlwTriggerType : undefined,
          brokeragePct: l.brokeragePct / 100,
          writtenLine: l.writtenPct / 100,
          signedLine: l.signedPct / 100,
          rarcPct: l.rarcPct.trim() === '' ? undefined : Number(l.rarcPct),
        })),
        regionPerils: rps,
        perils: rps.length ? [rps[0].endsWith('EQ') ? 'EQ' : 'WS'] : ['WS', 'EQ'],
        ilwSpec: isIlw
          ? {
              industrySimSetId: 'ind-synth-cat-v1-100k',
              regionPerils: rps.length ? rps : ['ALL'],
              triggerType: newIlwTriggerType,
              triggerBn: newLayers[0].attachment,
              limitM: newLayers[0].limit,
              rol: newLayers[0].rolPct / 100,
              direction: newDirection,
            }
          : null,
      });
    },
    onSuccess: (created) => {
      setShowNew(false);
      setActiveId(created.programmeId);
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['programmes'] });
      qc.invalidateQueries({ queryKey: ['graphTemplates'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
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
    mutationFn: () => {
      const isIlw = draftStructure === 'ILW';
      const isHedge = draftDirection === 'EXTERNAL_RETRO' || draftDirection === 'INTERNAL_CEDED';
      return api.updateContract(activeId, {
        direction: draftDirection,
        isHedge,
        structure: draftStructure,
        instrument: isIlw ? 'INDUSTRY_INDEX' : 'UNL',
        portfolioSnapshotDate: !isIlw && isHedge ? draftSnapshotDate || '2027-01-01' : null,
        profile: draftProfile,
      });
    },
    onSuccess: () => {
      setSavedMsg('Programme profile, direction & DAG loss source saved.');
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['programmes'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
      qc.invalidateQueries({ queryKey: ['dagEval'] });
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
    ['OCC_XL', 'AGG_XL', 'QUOTA_SHARE', 'AGG_QUOTA_SHARE', 'SURPLUS_SHARE', 'CASCADING_TOWER', 'ILW_OPTION'].includes(n.nodeType),
  );
  const unadjById = Object.fromEntries((d?.pricingLink.unadjustedNodes ?? []).map((n) => [n.nodeId, n]));
  const totalAdjEl = termAdjNodes.reduce((s, n) => s + Number(n.el ?? 0), 0);
  const programmeLossRatio: number | null = d?.pricingLink.programmeLossRatio ?? null;
  const programmeRarc = Number(d?.contract.profile?.rarcTotalPct ?? 0);

  return (
    <>
      <div className="card">
        <div className="card-title">
          <span>
            Programme Ledger
            <span className="badge" style={{ marginLeft: 4 }}>
              {visibleContracts.length} of {contractsQ.data?.length ?? 0}
            </span>
          </span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              placeholder="Search all columns…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 200 }}
            />
            {activeFilterCount > 0 && (
              <button className="btn btn-ghost" onClick={() => setColFilters({})}>
                Clear filters ({activeFilterCount})
              </button>
            )}
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
            <input value={cedentName} onChange={(e) => setCedentName(e.target.value)} placeholder="Cedent / Counterparty" />
            <input
              value={programmeName}
              onChange={(e) => setProgrammeName(e.target.value)}
              placeholder="Programme Name"
              style={{ width: 180 }}
            />
            <select
              aria-label="Deal direction"
              value={newDirection}
              onChange={(e) => {
                const nextDir = e.target.value as 'ASSUMED' | 'EXTERNAL_RETRO';
                setNewDirection(nextDir);
                if (nextDir === 'EXTERNAL_RETRO' && newDealBasis === 'UNL') {
                  setRegionPeril('ALL');
                }
              }}
            >
              <option value="ASSUMED">Long (Sell / Assumed)</option>
              <option value="EXTERNAL_RETRO">Hedge (Buy / Ceded)</option>
            </select>
            <select
              aria-label="Programme basis"
              value={newDealBasis}
              onChange={(e) => {
                const nextBasis = e.target.value as 'UNL' | 'ILW';
                setNewDealBasis(nextBasis);
                if (nextBasis === 'ILW') {
                  setNewLayers((ls) =>
                    ls.map((l, idx) => (idx === 0 ? { ...l, attachment: 40, limit: 25, rolPct: 16, brokeragePct: 0, writtenPct: 100, signedPct: 100 } : l)),
                  );
                }
              }}
            >
              <option value="UNL">Treaty (UNL)</option>
              <option value="ILW">ILW (Industry Index)</option>
            </select>
            {newDealBasis === 'ILW' && (
              <select
                aria-label="ILW trigger type"
                value={newIlwTriggerType}
                onChange={(e) =>
                  setNewIlwTriggerType(e.target.value as 'BINARY' | 'PRO_RATA' | 'AGGREGATE' | 'SECOND_EVENT')
                }
              >
                <option value="BINARY">Binary Trigger</option>
                <option value="PRO_RATA">Pro-Rata Trigger</option>
                <option value="AGGREGATE">Aggregate Trigger</option>
                <option value="SECOND_EVENT">Second Event</option>
              </select>
            )}
            {newDealBasis === 'UNL' && newDirection === 'EXTERNAL_RETRO' && (
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-muted)' }}>
                UNL Snapshot As-Of
                <input
                  type="date"
                  value={newSnapshotDate}
                  onChange={(e) => setNewSnapshotDate(e.target.value)}
                  style={{ width: 136 }}
                />
              </label>
            )}
            <select aria-label="Initial region peril filter" value={regionPeril} onChange={(e) => setRegionPeril(e.target.value)}>
              <option value="ALL">All Zones &amp; Perils (Unfiltered)</option>
              <option value="US-FL-WS">US-FL-WS</option>
              <option value="US-TX-WS">US-TX-WS</option>
              <option value="US-SE-WS">US-SE-WS</option>
              <option value="US-CA-EQ">US-CA-EQ</option>
              <option value="JP-KANTO-EQ">JP-KANTO-EQ</option>
              <option value="EU-DE-WS">EU-DE-WS</option>
            </select>
            <div style={{ width: '100%', fontSize: 11, color: 'var(--teal)' }}>
              {newDealBasis === 'ILW'
                ? 'DAG Auto-Wire: connects directly to the Industry Loss Run with all industry perils & regions available on the filter.'
                : newDirection === 'EXTERNAL_RETRO'
                  ? `DAG Auto-Wire: connects directly to our own UNL Portfolio Snapshot as of ${newSnapshotDate || '2027-01-01'}.`
                  : 'DAG Auto-Wire: connects to standard cedent UNL Loss Source.'}
            </div>
            <div className="layer-rows">
              {newLayers.map((l, i) => (
                <div className="layer-row" key={i}>
                  <label>
                    Layer ID
                    <input
                      value={l.layerCode}
                      placeholder={`L${i + 1}`}
                      onChange={(e) => setNewLayer(i, { layerCode: e.target.value })}
                      className="mono"
                      style={{ width: 72 }}
                    />
                  </label>
                  <label>
                    Layer {i + 1}
                    <input
                      value={l.name}
                      placeholder={
                        newDealBasis === 'ILW'
                          ? `${i + 1}${['st', 'nd', 'rd'][i] ?? 'th'} ILW`
                          : `${i + 1}${['st', 'nd', 'rd'][i] ?? 'th'} ${l.layerType === 'AGG_XL' ? 'Agg' : 'Cat'} XL`
                      }
                      onChange={(e) => setNewLayer(i, { name: e.target.value })}
                      style={{ width: 150 }}
                    />
                  </label>
                  {newDealBasis !== 'ILW' && (
                    <label>
                      Type
                      <select value={l.layerType} onChange={(e) => setNewLayer(i, { layerType: e.target.value as NewDealLayer['layerType'] })}>
                        <option value="OCC_XL">Occurrence XL</option>
                        <option value="AGG_XL">Aggregate XL</option>
                      </select>
                    </label>
                  )}
                  <label>
                    Limit ($M)
                    <input type="number" value={l.limit} onChange={(e) => setNewLayer(i, { limit: Number(e.target.value) })} style={{ width: 72 }} />
                  </label>
                  <label>
                    {newDealBasis === 'ILW' ? 'Trigger ($B)' : 'Xs ($M)'}
                    <input type="number" value={l.attachment} onChange={(e) => setNewLayer(i, { attachment: Number(e.target.value) })} style={{ width: 72 }} />
                  </label>
                  <label>
                    ROL (%)
                    <input type="number" step="0.5" value={l.rolPct} onChange={(e) => setNewLayer(i, { rolPct: Number(e.target.value) })} style={{ width: 64 }} />
                  </label>
                  <label>
                    Brokerage (%)
                    <input type="number" step="0.5" value={l.brokeragePct} onChange={(e) => setNewLayer(i, { brokeragePct: Number(e.target.value) })} style={{ width: 64 }} />
                  </label>
                  <label>
                    Written (%)
                    <input type="number" step="0.5" value={l.writtenPct} onChange={(e) => setNewLayer(i, { writtenPct: Number(e.target.value) })} style={{ width: 64 }} />
                  </label>
                  <label>
                    Signed (%)
                    <input type="number" step="0.5" value={l.signedPct} onChange={(e) => setNewLayer(i, { signedPct: Number(e.target.value) })} style={{ width: 64 }} />
                  </label>
                  <label>
                    RARC (%)
                    <input type="number" step="0.1" placeholder="0" value={l.rarcPct} onChange={(e) => setNewLayer(i, { rarcPct: e.target.value })} style={{ width: 64 }} />
                  </label>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)', paddingBottom: 8, minWidth: 120, lineHeight: 1.4 }}>
                    Prem {fmtMoneyM((l.limit * l.rolPct) / 100, 2)}
                    <br />
                    Our {fmtMoneyM((l.limit * l.rolPct * l.signedPct) / 10000, 2)}
                    {l.signedPct > l.writtenPct && <span style={{ color: 'var(--warning, #e0a43a)' }}> · signed &gt; written</span>}
                  </span>
                  <button
                    className="icon-btn"
                    style={{ marginBottom: 3 }}
                    title="Remove layer"
                    aria-label={`Remove layer ${i + 1}`}
                    disabled={newLayers.length <= 1}
                    onClick={() => setNewLayers((ls) => ls.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                </div>
              ))}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <button className="btn btn-ghost" onClick={() => setNewLayers((ls) => [...ls, nextLayer(ls[ls.length - 1], ls)])}>
                  + Add layer
                </button>
                {!newLayerCodesUnique && (
                  <span style={{ fontSize: 11, color: 'var(--negative)' }}>Each layer needs a unique Layer ID.</span>
                )}
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {newLayers.length} layer{newLayers.length > 1 ? 's' : ''} · {fmtMoneyM(newDealTotals.limit, 1)} limit ·{' '}
                  {fmtMoneyM(newDealTotals.premium, 2)} premium ·{' '}
                  {newDealTotals.limit ? fmtRatioPct(newDealTotals.premium / newDealTotals.limit, 1) : '—'} ROL
                </span>
                <button className="btn" style={{ marginLeft: 'auto' }} onClick={() => createMut.mutate()} disabled={!newDealValid || createMut.isPending}>
                  {createMut.isPending ? 'Saving…' : 'Save Contract'}
                </button>
              </div>
            </div>
          </div>
        )}

        <div className="table-wrap">
          <table className="ledger-table dense-table">
            <thead>
              <tr>
                {LEDGER_COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    className={`sortable ${col.kind === 'num' ? 'num' : ''}`}
                    title={col.title}
                    onClick={() =>
                      setSort((s) =>
                        s.key === col.key ? { key: col.key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: col.key, dir: 'asc' },
                      )
                    }
                  >
                    {col.label}
                    <span className="sort-ind">{sort.key === col.key ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
                  </th>
                ))}
                <th className="num">Actions</th>
              </tr>
              <tr className="filter-row">
                {LEDGER_COLUMNS.map((col) => (
                  <th key={col.key}>
                    {col.kind === 'select' ? (
                      <select
                        className="col-filter"
                        aria-label={`Filter ${col.label}`}
                        value={colFilters[col.key] ?? ''}
                        onChange={(e) => setColFilters((f) => ({ ...f, [col.key]: e.target.value }))}
                      >
                        <option value="">All</option>
                        {(selectOptionsByCol[col.key] ?? []).map((opt) => (
                          <option key={opt} value={opt}>
                            {opt}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        className="col-filter"
                        placeholder={col.kind === 'num' ? '> < a-b' : 'Filter'}
                        title={col.kind === 'num' ? 'e.g. >50, <=20, 10-30 or an exact value' : 'Contains…'}
                        value={colFilters[col.key] ?? ''}
                        onChange={(e) => setColFilters((f) => ({ ...f, [col.key]: e.target.value }))}
                        style={col.kind === 'num' ? { textAlign: 'right' } : undefined}
                      />
                    )}
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {visibleContracts.length === 0 && (
                <tr>
                  <td colSpan={LEDGER_COLUMNS.length + 1} className="empty-cell">
                    No programmes match these filters.
                  </td>
                </tr>
              )}
              {visibleContracts.map((c) => {
                const isSelected = c.programmeId === activeId;
                const rarc = c.profile?.rarcTotalPct ?? 0;
                const hedge = isHedgeContract(c);
                const typeLbl = contractTypeLabel(c);
                return (
                  <tr
                    key={c.programmeId}
                    onClick={() => setActiveId(c.programmeId)}
                    className={isSelected ? 'row-selected' : undefined}
                    style={{ cursor: 'pointer' }}
                  >
                    <td className="mono" style={{ color: 'var(--accent-soft)' }}>
                      {c.profile?.uniqueCode || c.programmeId}
                    </td>
                    <td style={{ fontWeight: 600 }}>{c.cedentName}</td>
                    <td>{c.programmeName}</td>
                    <td>
                      <span className={`badge ${hedge ? 'badge-hedge' : 'badge-long'}`}>
                        {hedge ? 'Hedge' : 'Long'}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${typeLbl === 'ILW' ? 'badge-ilw' : ''}`}>
                        {typeLbl}
                      </span>
                    </td>
                    <td>{c.renewalPeriod}</td>
                    <td style={{ color: 'var(--text-muted)' }}>
                      {c.underwriter} ({c.office})
                    </td>
                    <td>
                      <span className={`badge status-${String(c.status).toLowerCase()}`}>{c.status}</span>
                    </td>
                    <td className="num">{fmtRatioPct(c.writtenLine, 1)}</td>
                    <td className="num">{fmtRatioPct(c.signedLine, 1)}</td>
                    <td className="num">{layerCount(c)}</td>
                    <td className="num">{fmtMoneyM(c.limit, 1)}</td>
                    <td className="num">{fmtMoneyM(c.depositPremium, 2)}</td>
                    <td className="num">{fmtRatioPct(c.rol, 2)}</td>
                    <td className="num" style={{ color: rarc >= 0 ? 'var(--positive)' : 'var(--negative)', fontWeight: 600 }}>
                      {fmtPct(rarc, 1, { signed: true })}
                    </td>
                    <td className="num" onClick={(e) => e.stopPropagation()}>
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                        {c.status !== 'BOUND' && (
                          <button
                            className="btn"
                            style={{ height: 26, padding: '0 10px', fontSize: 11 }}
                            onClick={() => bindMut.mutate({ id: c.programmeId, status: 'BOUND' })}
                          >
                            Bind
                          </button>
                        )}
                        <button
                          className="btn btn-ghost"
                          style={{ height: 26, padding: '0 10px', fontSize: 11 }}
                          onClick={() => onSelectProgramme(c.programmeId)}
                        >
                          Open DAG
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
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
              <span className={`badge ${isHedgeContract(d.contract) ? 'badge-hedge' : 'badge-long'}`}>
                {isHedgeContract(d.contract) ? 'Hedge' : 'Long'}
              </span>
              <span className={`badge ${contractTypeLabel(d.contract) === 'ILW' ? 'badge-ilw' : ''}`}>
                {contractTypeLabel(d.contract)}
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
                Programme Profile
              </button>
              <button
                className={`tab ${dossierTab === 'layers' ? 'active' : ''}`}
                onClick={() => setDossierTab('layers')}
              >
                Layers &amp; Lines
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
              <div className="kpi-label">ROL / Loss Ratio</div>
              <div className="kpi-val">
                {fmtRatioPct(d.contract.rol, 1)}
                {' / '}
                {programmeLossRatio != null ? fmtRatioPct(programmeLossRatio, 1) : '—'}
              </div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Risk-Adj Rate Change (RARC)</div>
              <div className="kpi-val" style={{ color: programmeRarc >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
                {fmtPct(programmeRarc, 2, { signed: true })}
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

          {dossierTab === 'layers' && (
            <ContractLayersEditor
              programmeId={activeId}
              currency={d.contract.currency}
              onSaved={() => {
                qc.invalidateQueries({ queryKey: ['contracts'] });
                qc.invalidateQueries({ queryKey: ['contractDossier', activeId] });
                qc.invalidateQueries({ queryKey: ['programmes'] });
                qc.invalidateQueries({ queryKey: ['portfolio'] });
                qc.invalidateQueries({ queryKey: ['graphTemplates'] });
                qc.invalidateQueries({ queryKey: ['dagEval'] });
              }}
            />
          )}

          {/* ── Sub-tab 1: Programme Profile & RARC ── */}
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
                      Direction
                      <select
                        value={draftDirection}
                        onChange={(e) =>
                          setDraftDirection(e.target.value as 'ASSUMED' | 'EXTERNAL_RETRO' | 'INTERNAL_CEDED')
                        }
                        style={{ width: '100%', marginTop: 3 }}
                      >
                        <option value="ASSUMED">Long (Sell / Assumed)</option>
                        <option value="EXTERNAL_RETRO">Hedge — External Retro (Buy)</option>
                        <option value="INTERNAL_CEDED">Hedge — Internal Ceded</option>
                      </select>
                    </label>
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Structure / Basis
                      <select
                        value={draftStructure}
                        onChange={(e) => setDraftStructure(e.target.value)}
                        style={{ width: '100%', marginTop: 3 }}
                      >
                        <option value="OCC_XL">Occurrence XL (UNL)</option>
                        <option value="AGG_XL">Aggregate XL (UNL)</option>
                        <option value="ILW">ILW (Industry Loss Index)</option>
                        <option value="QUOTA_SHARE">Quota Share (UNL)</option>
                      </select>
                    </label>
                    {(draftDirection === 'EXTERNAL_RETRO' || draftDirection === 'INTERNAL_CEDED') && draftStructure !== 'ILW' && (
                      <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        UNL Snapshot As-Of
                        <input
                          type="date"
                          value={draftSnapshotDate}
                          onChange={(e) => setDraftSnapshotDate(e.target.value)}
                          style={{ width: '100%', marginTop: 3 }}
                        />
                      </label>
                    )}
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
                      Rate Change &amp; Prior Year Comparison
                    </span>
                    <button
                      className="btn"
                      onClick={() => saveProfileMut.mutate()}
                      disabled={saveProfileMut.isPending}
                    >
                      {saveProfileMut.isPending ? 'Saving…' : 'Save Programme Profile'}
                    </button>
                  </div>
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Risk-Adjusted Rate Change (RARC) — Programme Rollup
                    </div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 4, flexWrap: 'wrap' }}>
                      <span
                        data-testid="programme-rarc-readonly"
                        style={{
                          fontSize: 18,
                          fontWeight: 700,
                          fontVariantNumeric: 'tabular-nums',
                          color: programmeRarc >= 0 ? 'var(--positive)' : 'var(--negative)',
                        }}
                      >
                        {fmtPct(programmeRarc, 2, { signed: true })}
                      </span>
                      <span
                        style={{
                          fontSize: 12,
                          color: programmeRarc >= 0 ? 'var(--positive)' : 'var(--negative)',
                        }}
                      >
                        {programmeRarc >= 0 ? 'Rate increase' : 'Rate decrease'} vs prior year
                      </span>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        style={{ height: 24, padding: '0 8px', fontSize: 11, marginLeft: 'auto' }}
                        onClick={() => setDossierTab('layers')}
                      >
                        Edit per layer →
                      </button>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 3 }}>
                      Computed automatically as the line-size-weighted average (limit × signed line) of layer RARCs.
                    </div>
                  </div>

                  <div className="table-wrap"><table>
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
                  </table></div>

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
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', minWidth: 0, maxWidth: '100%' }}>
                    <label
                      style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, maxWidth: '100%' }}
                    >
                      Prior Year Link:
                      <select
                        style={{ flex: '1 1 auto', minWidth: 0, width: 260, maxWidth: '100%' }}
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
                <div className="table-wrap"><table className="dense-table">
                  <thead>
                    <tr>
                      <th title="Underwriting year">UY</th>
                      <th title="Unique code">Code</th>
                      <th>Programme</th>
                      <th>Status</th>
                      <th className="num" title="Limit, $M">Lim</th>
                      <th className="num" title="Year on year limit change, $M">Δ Lim</th>
                      <th className="num" title="Gross written premium, $M">GWP</th>
                      <th className="num" title="Rate on line">ROL</th>
                      <th className="num" title="Year on year ROL change, points">Δ ROL</th>
                      <th className="num" title="Signed line">Sign</th>
                      <th className="num" title="Risk-adjusted rate change">RARC</th>
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
                </table></div>
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
                <div className="table-wrap"><table className="dense-table">
                  <thead>
                    <tr>
                      <th>Layer</th>
                      <th className="num" title="Unadjusted expected loss">EL U</th>
                      <th className="num" title="Adjusted expected loss">EL A</th>
                      <th className="num" title="Expected reinstatement premium">RIP</th>
                      <th className="num" title="1-in-250 tail value at risk">TVaR</th>
                      <th className="num" title="Adjusted expected loss ratio">ELR</th>
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
                </table></div>
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
                  <div className="table-wrap"><table>
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
                  </table></div>
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
                    <div className="table-wrap"><table>
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
                    </table></div>
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
                  <div className="table-wrap"><table>
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
                  </table></div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
