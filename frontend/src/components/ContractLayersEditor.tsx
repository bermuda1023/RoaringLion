import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useEffect, useState } from 'react';
import { fetchJson } from '../api/client';
import { fmtMoneyM, fmtNum, fmtPct, fmtRatioPct } from '../utils/format';
import {
  computeLayerLossRatio,
  computeTotalLossRatio,
  computeWeightedRarc,
  type LayerLossRatioInput,
} from '../utils/layerRollup';
import { composeLabel, nextLayerCode, nodeTerms } from '../utils/nodeLabels';

export interface ContractLayer {
  nodeId: string;
  layerCode: string;
  label: string;
  nodeType: string;
  limit: number | null;
  attachment: number | null;
  depositPremium: number;
  rol: number | null;
  reinstatements: number | null;
  ripRate: number | null;
  writtenLine: number;
  signedLine: number;
  ourLimit: number | null;
  ourPremium: number;
  brokeragePct: number;
  ourNetPremium: number;
  rarcPct: number | null;
  expectedLoss?: number | null;
  expectedRip?: number | null;
  lossRatio?: number | null;
  editable: string[];
  name: string;
  terms: string | null;
  autoLabel: boolean;
  linkedPricingNodeId?: string | null;
  linkedPricingLabel?: string | null;
  linkedFeederNodeId?: string | null;
  linkedFeederLabel?: string | null;
}

type Field =
  | 'layerCode'
  | 'label'
  | 'limit'
  | 'attachment'
  | 'depositPremium'
  | 'rol'
  | 'rarcPct'
  | 'reinstatements'
  | 'ripRate'
  | 'brokeragePct'
  | 'writtenLine'
  | 'signedLine';
type Draft = Record<string, Partial<Record<Field, string>>>;

const TYPE_LABEL: Record<string, string> = {
  OCC_XL: 'Occurrence XL',
  AGG_XL: 'Aggregate XL',
  QUOTA_SHARE: 'Quota Share',
  AGG_QUOTA_SHARE: 'Agg Quota Share',
  SURPLUS_SHARE: 'Surplus Share',
  CASCADING_TOWER: 'Cascading Tower',
  ILW_OPTION: 'ILW',
};

const TYPE_SHORT: Record<string, string> = {
  OCC_XL: 'Occ',
  AGG_XL: 'Agg',
  QUOTA_SHARE: 'QS',
  AGG_QUOTA_SHARE: 'AQS',
  SURPLUS_SHARE: 'SS',
  CASCADING_TOWER: 'Casc',
  ILW_OPTION: 'ILW',
};

// Fields stored as 0..1 ratios on the backend and edited as 0..100% in the UI.
// Note: rarcPct is stored directly in percentage points (e.g. 6.1 = +6.1%).
const PCT_FIELDS: Field[] = ['rol', 'ripRate', 'brokeragePct', 'writtenLine', 'signedLine'];

function initialText(layer: ContractLayer, field: Field): string {
  const v = layer[field as keyof ContractLayer] as number | string | null | undefined;
  if (field === 'layerCode') return layer.layerCode ?? '';
  if (field === 'label') return layer.autoLabel ? layer.name : layer.label;
  if (v == null) return '';
  if (PCT_FIELDS.includes(field)) return String(Number(((v as number) * 100).toFixed(4)));
  return String(Number((v as number).toFixed(6)));
}

interface NewLayerDraft {
  layerCode: string;
  name: string;
  layerType: 'OCC_XL' | 'AGG_XL' | 'ILW_OPTION';
  limit: string;
  attachment: string;
  depositPremium: string;
  reinstatements: string;
  brokeragePct: string;
  writtenLine: string;
  signedLine: string;
  rarcPct: string;
}

function blankLayer(layers: ContractLayer[]): NewLayerDraft {
  const top = layers.reduce<ContractLayer | null>(
    (best, l) => (l.limit != null && l.attachment != null && (!best || l.attachment + l.limit > (best.attachment ?? 0) + (best.limit ?? 0)) ? l : best),
    null,
  );
  return {
    layerCode: nextLayerCode(layers.map((l) => l.layerCode)),
    name: '',
    layerType: 'OCC_XL',
    limit: top?.limit != null ? String(top.limit) : '',
    attachment: top?.limit != null && top.attachment != null ? String(top.attachment + top.limit) : '',
    depositPremium: '',
    reinstatements: '1',
    brokeragePct: top ? String(Number((top.brokeragePct * 100).toFixed(4))) : '10',
    writtenLine: top ? String(Number((top.writtenLine * 100).toFixed(4))) : '100',
    signedLine: top ? String(Number((top.signedLine * 100).toFixed(4))) : '100',
    rarcPct: '',
  };
}

function parseNum(text: string | undefined): number | null {
  if (text == null || text.trim() === '') return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

export function ContractLayersEditor({
  programmeId,
  currency,
  onSaved,
}: {
  programmeId: string;
  currency: string;
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft>({});
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [newLayer, setNewLayer] = useState<NewLayerDraft | null>(null);
  const [openRows, setOpenRows] = useState<Record<string, boolean>>({});

  const layersQ = useQuery({
    queryKey: ['contractLayers', programmeId],
    queryFn: () => fetchJson<ContractLayer[]>(`/api/contracts/${programmeId}/layers`),
  });

  useEffect(() => {
    setDraft({});
    setSavedMsg(null);
    setNewLayer(null);
  }, [programmeId]);

  const repricedByRol = (layer: ContractLayer) => draft[layer.nodeId]?.rol !== undefined;
  const text = (layer: ContractLayer, field: Field) => {
    if (field === 'depositPremium' && repricedByRol(layer)) return String(Number(live(layer).premium.toFixed(4)));
    return draft[layer.nodeId]?.[field] ?? initialText(layer, field);
  };
  const setField = (nodeId: string, field: Field, value: string) => {
    setSavedMsg(null);
    setDraft((d) => {
      const next = { ...d[nodeId], [field]: value };
      if (field === 'rol') delete next.depositPremium;
      if (field === 'depositPremium') delete next.rol;
      return { ...d, [nodeId]: next };
    });
  };

  const live = (layer: ContractLayer) => {
    const num = (f: Field, fallback: number | null) => {
      const t = draft[layer.nodeId]?.[f];
      return t === undefined ? fallback : parseNum(t);
    };
    const limit = layer.editable.includes('limit') ? num('limit', layer.limit) : layer.limit;
    const rolDraft = draft[layer.nodeId]?.rol;
    const premium =
      rolDraft !== undefined && limit != null
        ? ((parseNum(rolDraft) ?? 0) / 100) * limit
        : layer.editable.includes('depositPremium')
          ? num('depositPremium', layer.depositPremium) ?? 0
          : layer.depositPremium;
    const pct = (f: Field, base: number | null) => {
      const v = num(f, base == null ? null : base * 100);
      return v == null ? null : v / 100;
    };
    const brokerage = pct('brokeragePct', layer.brokeragePct) ?? 0;
    const rarcPct = num('rarcPct', layer.rarcPct);
    const writtenPct = num('writtenLine', layer.writtenLine * 100);
    const signedPct = num('signedLine', layer.signedLine * 100);
    const written = writtenPct == null ? null : writtenPct / 100;
    const signed = signedPct == null ? null : signedPct / 100;
    const reinst = num('reinstatements', layer.reinstatements);
    const ripPct = num('ripRate', layer.ripRate == null ? null : layer.ripRate * 100);
    const att = num('attachment', layer.attachment);
    const liveParams: Record<string, Record<string, unknown>> = {
      OCC_XL: { occLimit: limit, occAttachment: att, reinstatements: reinst, ripRate: ripPct == null ? null : ripPct / 100, placedShare: signed },
      AGG_XL: { aggLimit: limit, aggAttachment: att, placedShare: signed },
      ILW_OPTION: { limit, trigger: att, placedShare: signed },
    };
    const terms = liveParams[layer.nodeType] ? nodeTerms(layer.nodeType, liveParams[layer.nodeType]) ?? layer.terms : layer.terms;
    const rol = limit ? premium / limit : null;
    const ourPremium = signed != null ? premium * signed : null;
    const lossRatioInput: LayerLossRatioInput = {
      savedExpectedLoss: layer.expectedLoss,
      savedExpectedRip: layer.expectedRip,
      savedLossRatio: layer.lossRatio,
      savedDepositPremium: layer.depositPremium,
      savedSignedLine: layer.signedLine,
      liveDepositPremium: premium,
      liveSignedLine: signed,
    };
    const lossRatio = computeLayerLossRatio(lossRatioInput);
    const expectedLoss =
      layer.expectedLoss != null && layer.signedLine > 0 && signed != null
        ? (layer.expectedLoss / layer.signedLine) * signed
        : layer.expectedLoss ?? null;
    return {
      terms,
      limit,
      premium,
      written,
      signed,
      brokerage,
      rarcPct,
      rol,
      lossRatio,
      lossRatioInput,
      expectedLoss,
      ourLimit: limit != null && signed != null ? limit * signed : null,
      ourPremium,
      ourNet: ourPremium != null ? ourPremium * (1 - brokerage) : null,
    };
  };

  const layers = layersQ.data ?? [];
  const dirtyIds = Object.keys(draft).filter((id) => Object.keys(draft[id] ?? {}).length > 0);

  const problems: string[] = [];
  const seenLayerCodes = new Map<string, string>();
  for (const layer of layers) {
    const code = (draft[layer.nodeId]?.layerCode ?? layer.layerCode ?? '').trim();
    if (!code) {
      problems.push(`${layer.label}: Layer ID is required`);
    } else {
      const key = code.toLowerCase();
      if (seenLayerCodes.has(key)) {
        problems.push(`Duplicate Layer ID "${code}" within this programme`);
      } else {
        seenLayerCodes.set(key, code);
      }
    }
    const l = live(layer);
    for (const [name, v] of [
      ['Written line', l.written],
      ['Signed line', l.signed],
    ] as const) {
      if (v == null) problems.push(`${layer.label}: ${name} is required`);
      else if (v < 0 || v > 1) problems.push(`${layer.label}: ${name} must be between 0% and 100%`);
    }
    if (l.brokerage < 0 || l.brokerage > 0.9) problems.push(`${layer.label}: brokerage must be between 0% and 90%`);
    if (l.rol != null && (l.rol < 0 || l.rol > 5)) problems.push(`${layer.label}: ROL must be between 0% and 500%`);
    if (l.rarcPct != null && (l.rarcPct < -100 || l.rarcPct > 500))
      problems.push(`${layer.label}: RARC must be between -100% and 500%`);
    if (layer.editable.includes('limit') && (l.limit == null || l.limit <= 0)) problems.push(`${layer.label}: limit must be greater than 0`);
  }
  const warnings: string[] = [];
  for (const layer of layers) {
    const l = live(layer);
    if (l.written != null && l.signed != null && l.signed > l.written + 1e-9) warnings.push(`${layer.label}: signed line is above written line`);
  }

  const liveLayers = layers.map((layer) => ({ layer, live: live(layer) }));
  // Match backend selective rollup: when explicit Pricing Output links exist, only linked layers roll up.
  const hasLinkedLayers = liveLayers.some(({ layer }) => Boolean(layer.linkedPricingNodeId));
  const rollupLayers = hasLinkedLayers
    ? liveLayers.filter(({ layer }) => Boolean(layer.linkedPricingNodeId))
    : liveLayers;
  const totals = rollupLayers.reduce(
    (acc, { live: l }) => {
      acc.limit += l.limit ?? 0;
      acc.premium += l.premium;
      acc.ourLimit += l.ourLimit ?? 0;
      acc.ourPremium += l.ourPremium ?? 0;
      acc.ourNet += l.ourNet ?? 0;
      acc.brokerage += l.premium * l.brokerage;
      acc.written += (l.limit ?? 0) * (l.written ?? 0);
      return acc;
    },
    { limit: 0, premium: 0, ourLimit: 0, ourPremium: 0, ourNet: 0, brokerage: 0, written: 0 },
  );
  const weightedRarc = computeWeightedRarc(
    rollupLayers.map(({ live: l }) => ({
      limit: l.limit,
      signedLine: l.signed,
      writtenLine: l.written,
      rarcPct: l.rarcPct,
    })),
  );
  const totalLossRatio = computeTotalLossRatio(rollupLayers.map(({ live: l }) => l.lossRatioInput));

  const saveMut = useMutation({
    mutationFn: () => {
      const payload = dirtyIds.map((nodeId) => {
        const d = draft[nodeId] ?? {};
        const out: Record<string, unknown> = { nodeId };
        if (d.layerCode !== undefined) out.layerCode = d.layerCode;
        if (d.label !== undefined) out.label = d.label;
        for (const f of ['limit', 'attachment', 'depositPremium', 'reinstatements', 'rarcPct'] as const) {
          if (d[f] !== undefined) out[f] = parseNum(d[f]);
        }
        for (const f of PCT_FIELDS) {
          if (d[f] !== undefined) {
            const n = parseNum(d[f]);
            out[f] = n == null ? null : n / 100;
          }
        }
        return out;
      });
      return fetchJson<ContractLayer[]>(`/api/contracts/${programmeId}/layers`, {
        method: 'PUT',
        body: JSON.stringify({ layers: payload }),
      });
    },
    onSuccess: (updated) => {
      qc.setQueryData(['contractLayers', programmeId], updated);
      qc.invalidateQueries({ queryKey: ['graphTemplates'] });
      setDraft({});
      setSavedMsg('Layers saved. Pricing, Structuring Studio suggestions, portfolio and optimizer now use the updated terms and lines.');
      onSaved();
    },
  });

  const newLayerProblems: string[] = [];
  if (newLayer) {
    const nlCode = newLayer.layerCode.trim();
    if (!nlCode) newLayerProblems.push('Layer ID is required');
    else if (seenLayerCodes.has(nlCode.toLowerCase())) newLayerProblems.push(`Layer ID "${nlCode}" already exists on this programme`);
    const lim = parseNum(newLayer.limit);
    const att = parseNum(newLayer.attachment);
    const w = parseNum(newLayer.writtenLine);
    const sg = parseNum(newLayer.signedLine);
    const nr = parseNum(newLayer.rarcPct);
    if (lim == null || lim <= 0) newLayerProblems.push('limit must be greater than 0');
    if (att == null || att < 0) newLayerProblems.push('attachment must be 0 or more');
    if ((parseNum(newLayer.depositPremium) ?? 0) < 0) newLayerProblems.push('premium must be 0 or more');
    const b = parseNum(newLayer.brokeragePct) ?? 10;
    if (b < 0 || b > 90) newLayerProblems.push('brokerage must be 0–90%');
    if (nr != null && (nr < -100 || nr > 500)) newLayerProblems.push('RARC must be between -100% and 500%');
    for (const [label, v] of [['written', w], ['signed', sg]] as const) {
      if (v == null || v < 0 || v > 100) newLayerProblems.push(`${label} line must be 0–100%`);
    }
  }

  const addMut = useMutation({
    mutationFn: (nl: NewLayerDraft) => {
      const parsedRarc = parseNum(nl.rarcPct);
      return fetchJson<ContractLayer[]>(`/api/contracts/${programmeId}/layers`, {
        method: 'POST',
        body: JSON.stringify({
          layerCode: nl.layerCode.trim() || undefined,
          name: nl.name.trim() || undefined,
          layerType: nl.layerType,
          limit: parseNum(nl.limit),
          attachment: parseNum(nl.attachment),
          depositPremium: parseNum(nl.depositPremium) ?? 0,
          reinstatements: nl.layerType === 'OCC_XL' ? parseNum(nl.reinstatements) : undefined,
          brokeragePct: (parseNum(nl.brokeragePct) ?? 10) / 100,
          writtenLine: (parseNum(nl.writtenLine) ?? 100) / 100,
          signedLine: (parseNum(nl.signedLine) ?? 100) / 100,
          rarcPct: parsedRarc ?? undefined,
        }),
      });
    },
    onSuccess: (updated) => {
      qc.setQueryData(['contractLayers', programmeId], updated);
      qc.invalidateQueries({ queryKey: ['graphTemplates'] });
      const added = updated[updated.length - 1];
      setNewLayer(null);
      setSavedMsg(
        `Added ${added?.layerCode ? `${added.layerCode} · ` : ''}${added?.label ?? 'layer'}. It is wired into the structure and listed as a CMS suggestion in Structuring Studio.`,
      );
      onSaved();
    },
  });

  const removeMut = useMutation({
    mutationFn: (nodeId: string) =>
      fetchJson<ContractLayer[]>(`/api/contracts/${programmeId}/layers/${encodeURIComponent(nodeId)}`, { method: 'DELETE' }),
    onSuccess: (updated, nodeId) => {
      qc.setQueryData(['contractLayers', programmeId], updated);
      qc.invalidateQueries({ queryKey: ['graphTemplates'] });
      setDraft(({ [nodeId]: _removed, ...rest }) => rest);
      setSavedMsg('Layer removed. Programme totals and lines updated.');
      onSaved();
    },
  });

  const nlField = (key: keyof NewLayerDraft, opts: { width?: number; step?: string; placeholder?: string } = {}) =>
    newLayer && (
      <input
        type={key === 'name' || key === 'layerCode' ? 'text' : 'number'}
        step={opts.step ?? 'any'}
        placeholder={opts.placeholder}
        value={newLayer[key]}
        onChange={(e) => setNewLayer({ ...newLayer, [key]: e.target.value })}
        className={`cell-input changed${key === 'layerCode' ? ' mono' : ''}`}
        style={{ textAlign: key === 'name' || key === 'layerCode' ? 'left' : 'right' }}
      />
    );

  const cell = (
    layer: ContractLayer,
    field: Field,
    opts: { width?: number; step?: string; suffix?: string; ariaLabel?: string; placeholder?: string } = {},
  ) => {
    if (!layer.editable.includes(field)) {
      const v = initialText(layer, field);
      return <span style={{ color: 'var(--text-faint)' }}>{v === '' ? '—' : opts.suffix ? `${v}${opts.suffix}` : v}</span>;
    }
    const changed = draft[layer.nodeId]?.[field] !== undefined || (field === 'depositPremium' && repricedByRol(layer));
    const isText = field === 'label' || field === 'layerCode';
    return (
      <input
        type={isText ? 'text' : 'number'}
        step={opts.step ?? 'any'}
        aria-label={opts.ariaLabel}
        placeholder={opts.placeholder}
        value={text(layer, field)}
        onChange={(e) => setField(layer.nodeId, field, e.target.value)}
        className={`cell-input ${changed ? 'changed' : ''}${field === 'layerCode' ? ' mono' : ''}`}
        style={{ textAlign: isText ? 'left' : 'right' }}
      />
    );
  };

  if (layersQ.isError) return <div className="error-banner">{String((layersQ.error as Error).message)}</div>;
  if (!layersQ.data) return <div style={{ color: 'var(--text-muted)', padding: 12 }}>Loading layers…</div>;

  return (
    <div className="panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <div className="panel-title" style={{ marginBottom: 2 }}>
            Layer Terms &amp; Lines
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            Limits, attachments and premiums are $M at 100%. Open a row for modelled EL, reinstatements, RIP and the pricing link. Our share = 100% × signed line ({currency}).
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            className="btn btn-ghost"
            onClick={() => setNewLayer((nl) => (nl ? null : blankLayer(layers)))}
            disabled={addMut.isPending}
          >
            {newLayer ? 'Cancel new layer' : '+ Add layer'}
          </button>
          {dirtyIds.length > 0 && (
            <button className="btn btn-ghost" onClick={() => setDraft({})} disabled={saveMut.isPending}>
              Discard changes
            </button>
          )}
          <button
            className="btn"
            onClick={() => saveMut.mutate()}
            disabled={dirtyIds.length === 0 || problems.length > 0 || saveMut.isPending}
          >
            {saveMut.isPending ? 'Saving…' : dirtyIds.length > 0 ? `Save ${dirtyIds.length} layer${dirtyIds.length > 1 ? 's' : ''}` : 'Saved'}
          </button>
        </div>
      </div>

      {saveMut.isError && <div className="error-banner">{String((saveMut.error as Error).message)}</div>}
      {(addMut.error || removeMut.error) && (
        <div className="error-banner">{String(((addMut.error ?? removeMut.error) as Error).message)}</div>
      )}
      {problems.length > 0 && <div className="error-banner">{problems.join(' · ')}</div>}
      {warnings.length > 0 && <div className="warning-banner">{warnings.join(' · ')}</div>}
      {newLayer && newLayerProblems.length > 0 && (
        <div className="warning-banner">New layer: {newLayerProblems.join(' · ')}</div>
      )}
      {savedMsg && <div className="success-banner">{savedMsg}</div>}

      {layers.length === 0 && !newLayer ? (
        <div className="empty-cell">This programme has no layers yet. Use “+ Add layer” or add financial-terms nodes in the Structuring Studio.</div>
      ) : (
        <div className="table-wrap layer-terms">
          <table>
            <colgroup>
              <col className="col-toggle" />
              <col className="col-id" />
              <col className="col-name" />
              <col className="col-type" />
              <col className="col-xs" />
              <col className="col-price" />
              <col className="col-lines" />
              <col className="col-rarc" />
              <col className="col-lr" />
              <col className="col-ours" />
              <col className="col-act" />
            </colgroup>
            <thead>
              <tr>
                <th aria-label="Row detail" />
                <th title="Unique identifier within this programme">ID</th>
                <th title="Layer name">Name</th>
                <th title="Layer type">Type</th>
                <th title="Limit and attachment, $M at 100%">Limit xs att</th>
                <th title="Deposit premium $M at 100%, and rate on line">Prem / ROL</th>
                <th title="Written and signed line, and brokerage, percent">W / S / Br</th>
                <th className="num" title="Risk-adjusted rate change for this layer, percent">RARC %</th>
                <th className="num" title="Read-only modelled Loss Ratio: Adjusted EL / (Deposit Premium + Expected RIP)">Loss Ratio</th>
                <th title="Our limit, premium and premium net of brokerage">Ours</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {liveLayers.map(({ layer, live: l }) => {
                const open = Boolean(openRows[layer.nodeId]);
                const fullLabel = l.terms ? composeLabel(text(layer, 'label'), l.terms) : layer.label;
                const linkTitle = layer.linkedPricingLabel
                  ? layer.linkedFeederLabel
                    ? `1:1 linked Pricing Output: ${layer.linkedPricingLabel} · Feeder: ${layer.linkedFeederLabel}`
                    : `1:1 linked Pricing Output: ${layer.linkedPricingLabel}`
                  : 'No pricing output linked';
                return (
                  <Fragment key={layer.nodeId}>
                    <tr>
                      <td>
                        <button
                          type="button"
                          className="icon-btn"
                          aria-expanded={open}
                          aria-label={`${open ? 'Hide' : 'Show'} detail for ${layer.label}`}
                          title="Modelled EL, Loss Ratio, reinstatements, RIP and the pricing link"
                          onClick={() => setOpenRows((rows) => ({ ...rows, [layer.nodeId]: !rows[layer.nodeId] }))}
                        >
                          {open ? '▾' : '▸'}
                        </button>
                      </td>
                      <td>{cell(layer, 'layerCode')}</td>
                      <td>
                        {cell(layer, 'label')}
                        {fullLabel && (
                          <div className="layer-terms-line" title={fullLabel}>
                            {fullLabel}
                          </div>
                        )}
                      </td>
                      <td title={TYPE_LABEL[layer.nodeType] ?? layer.nodeType}>{TYPE_SHORT[layer.nodeType] ?? layer.nodeType}</td>
                      <td>
                        <div className="layer-cluster">
                          {cell(layer, 'limit', { step: '1' })}
                          <span className="sep">xs</span>
                          {cell(layer, 'attachment', { step: '1' })}
                        </div>
                      </td>
                      <td>
                        <div className="layer-cluster">
                          {cell(layer, 'depositPremium', { step: '0.1' })}
                          {layer.editable.includes('rol') ? (
                            <input
                              type="number"
                              step="0.25"
                              aria-label={`ROL for ${layer.label}`}
                              value={draft[layer.nodeId]?.rol ?? (l.rol == null ? '' : String(Number((l.rol * 100).toFixed(2))))}
                              onChange={(e) => setField(layer.nodeId, 'rol', e.target.value)}
                              className={`cell-input ${draft[layer.nodeId]?.rol !== undefined ? 'changed' : ''}`}
                              style={{ textAlign: 'right' }}
                              title="Edit ROL to reprice the layer; premium follows"
                            />
                          ) : (
                            <span>{l.rol == null ? '—' : fmtRatioPct(l.rol, 1)}</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="layer-cluster">
                          {cell(layer, 'writtenLine', { step: '0.5', ariaLabel: `Written line for ${layer.label}` })}
                          {cell(layer, 'signedLine', { step: '0.5', ariaLabel: `Signed line for ${layer.label}` })}
                          {cell(layer, 'brokeragePct', { step: '0.5', ariaLabel: `Brokerage for ${layer.label}` })}
                        </div>
                      </td>
                      <td className="num">
                        {cell(layer, 'rarcPct', { step: '0.1', ariaLabel: `RARC for ${layer.label}`, placeholder: '—' })}
                      </td>
                      <td
                        className="num"
                        title="Read-only modelled Loss Ratio: Adjusted EL / (Deposit Premium + Expected RIP)"
                        style={{ color: l.lossRatio == null ? 'var(--text-faint)' : 'var(--text)', fontWeight: 600 }}
                      >
                        {l.lossRatio == null ? '—' : fmtRatioPct(l.lossRatio, 1)}
                      </td>
                      <td className="ours">
                        <span title="Our limit">{l.ourLimit == null ? '—' : fmtMoneyM(l.ourLimit, 1)}</span>
                        <span title="Our premium">{l.ourPremium == null ? '—' : fmtMoneyM(l.ourPremium, 1)}</span>
                        <span title="Our premium net of brokerage">{l.ourNet == null ? '—' : fmtMoneyM(l.ourNet, 1)}</span>
                      </td>
                      <td>
                        <button
                          className="icon-btn"
                          title={layers.length > 1 ? `Remove ${layer.label}` : 'A programme needs at least one layer'}
                          aria-label={`Remove ${layer.label}`}
                          disabled={layers.length <= 1 || removeMut.isPending}
                          onClick={() => {
                            if (window.confirm(`Remove ${layer.label} from this programme? Its pricing output is removed too.`)) {
                              removeMut.mutate(layer.nodeId);
                            }
                          }}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                    {open && (
                      <tr className="layer-detail">
                        <td colSpan={11}>
                          <div className="layer-detail-grid">
                            <div>
                              <span>Loss Ratio</span>
                              <b>{l.lossRatio == null ? '—' : fmtRatioPct(l.lossRatio, 1)}</b>
                            </div>
                            <div>
                              <span>Modelled EL (Adj)</span>
                              <b style={{ color: 'var(--accent)' }}>
                                {l.expectedLoss == null ? '—' : fmtMoneyM(l.expectedLoss, 3)}
                              </b>
                            </div>
                            <label>
                              Reinstatements
                              {cell(layer, 'reinstatements', { step: '1' })}
                            </label>
                            <label>
                              RIP %
                              {cell(layer, 'ripRate', { step: '5' })}
                            </label>
                            <div title={linkTitle}>
                              <span>Pricing output</span>
                              <b>{layer.linkedPricingLabel || 'Not linked'}</b>
                              {layer.linkedFeederLabel ? <b>Feeder {layer.linkedFeederLabel}</b> : null}
                            </div>
                            <div>
                              <span>Full label</span>
                              <b>{fullLabel}</b>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {newLayer && (() => {
                const lim = parseNum(newLayer.limit);
                const prem = parseNum(newLayer.depositPremium) ?? 0;
                const sg = parseNum(newLayer.signedLine);
                const att = parseNum(newLayer.attachment);
                const terms =
                  lim != null && att != null
                    ? nodeTerms(newLayer.layerType, {
                        ...(newLayer.layerType === 'OCC_XL'
                          ? { occLimit: lim, occAttachment: att, reinstatements: parseNum(newLayer.reinstatements), ripRate: 1 }
                          : { aggLimit: lim, aggAttachment: att }),
                        placedShare: sg == null ? 1 : sg / 100,
                      })
                    : null;
                const net = sg != null ? ((prem * sg) / 100) * (1 - (parseNum(newLayer.brokeragePct) ?? 10) / 100) : null;
                return (
                  <Fragment key="new-layer">
                    <tr className="new-layer-row">
                      <td />
                      <td>{nlField('layerCode', { placeholder: 'L1' })}</td>
                      <td>
                        {nlField('name', { placeholder: 'Name' })}
                        {terms && (
                          <div className="layer-terms-line" title={composeLabel(newLayer.name.trim() || 'New layer', terms)}>
                            {composeLabel(newLayer.name.trim() || 'New layer', terms)}
                          </div>
                        )}
                      </td>
                      <td>
                        <select
                          aria-label="New layer type"
                          value={newLayer.layerType}
                          onChange={(e) => setNewLayer({ ...newLayer, layerType: e.target.value as NewLayerDraft['layerType'] })}
                          className="cell-input changed"
                        >
                          <option value="OCC_XL">Occ</option>
                          <option value="AGG_XL">Agg</option>
                          <option value="ILW_OPTION">ILW</option>
                        </select>
                      </td>
                      <td>
                        <div className="layer-cluster">
                          {nlField('limit', { step: '1' })}
                          <span className="sep">xs</span>
                          {nlField('attachment', { step: '1' })}
                        </div>
                      </td>
                      <td>
                        <div className="layer-cluster">
                          {nlField('depositPremium', { step: '0.1', placeholder: '0' })}
                          <span title="ROL from premium and limit">{lim ? fmtRatioPct(prem / lim, 1) : '—'}</span>
                        </div>
                      </td>
                      <td>
                        <div className="layer-cluster">
                          {nlField('writtenLine', { step: '0.5' })}
                          {nlField('signedLine', { step: '0.5' })}
                          {nlField('brokeragePct', { step: '0.5' })}
                        </div>
                      </td>
                      <td className="num">{nlField('rarcPct', { step: '0.1', placeholder: '0' })}</td>
                      <td className="num" style={{ color: 'var(--text-faint)' }}>
                        —
                      </td>
                      <td className="ours">
                        <span title="Our limit">{lim != null && sg != null ? fmtMoneyM((lim * sg) / 100, 1) : '—'}</span>
                        <span title="Our premium">{sg != null ? fmtMoneyM((prem * sg) / 100, 1) : '—'}</span>
                        <span title="Our premium net of brokerage">{net == null ? '—' : fmtMoneyM(net, 1)}</span>
                      </td>
                      <td>
                        <button
                          className="btn"
                          onClick={() => addMut.mutate(newLayer)}
                          disabled={newLayerProblems.length > 0 || addMut.isPending}
                          title={newLayerProblems.join(' · ') || 'Add layer'}
                        >
                          {addMut.isPending ? '…' : 'Add'}
                        </button>
                      </td>
                    </tr>
                    <tr className="layer-detail">
                      <td colSpan={11}>
                        <div className="layer-detail-grid">
                          <label>
                            Reinstatements
                            {newLayer.layerType === 'OCC_XL' ? nlField('reinstatements', { step: '1' }) : <b>—</b>}
                          </label>
                          <div>
                            <span>RIP %</span>
                            <b>{newLayer.layerType === 'OCC_XL' ? '100%' : '—'}</b>
                          </div>
                          <div>
                            <span>Loss Ratio</span>
                            <b>Computed after add</b>
                          </div>
                        </div>
                      </td>
                    </tr>
                  </Fragment>
                );
              })()}
              <tr className="total-row">
                <td />
                <td />
                <td>Total</td>
                <td />
                <td title="Total limit">{fmtMoneyM(totals.limit, 1)}</td>
                <td title="Total premium and limit-weighted ROL">
                  {fmtMoneyM(totals.premium, 1)}
                  <div className="layer-terms-line">
                    {totals.limit ? fmtRatioPct(totals.premium / totals.limit, 1) : '—'}
                  </div>
                </td>
                <td title="Limit-weighted written and signed lines, and premium-weighted brokerage">
                  <div className="layer-cluster">
                    <span>{totals.limit ? fmtNum((totals.written / totals.limit) * 100, 0) : '—'}</span>
                    <span>{totals.limit ? fmtNum((totals.ourLimit / totals.limit) * 100, 0) : '—'}</span>
                    <span>{totals.premium ? fmtNum((totals.brokerage / totals.premium) * 100, 0) : '—'}</span>
                  </div>
                </td>
                <td
                  className="num"
                  title="Line-size-weighted average RARC across layers (weighted by limit × signed line)"
                  style={{
                    color: weightedRarc == null ? 'var(--text-faint)' : weightedRarc >= 0 ? 'var(--positive)' : 'var(--negative)',
                    fontWeight: 600,
                  }}
                >
                  {weightedRarc == null ? '—' : fmtPct(weightedRarc, 1, { signed: true })}
                </td>
                <td
                  className="num"
                  title="Programme Loss Ratio: sum(Adjusted EL) / sum(Our Deposit Premium + Expected RIP)"
                  style={{ fontWeight: 600 }}
                >
                  {totalLossRatio == null ? '—' : fmtRatioPct(totalLossRatio, 1)}
                </td>
                <td className="ours">
                  <span title="Our limit">{fmtMoneyM(totals.ourLimit, 1)}</span>
                  <span title="Our premium">{fmtMoneyM(totals.ourPremium, 1)}</span>
                  <span title="Our premium net of brokerage">{fmtMoneyM(totals.ourNet, 1)}</span>
                </td>
                <td />
              </tr>
            </tbody>
          </table>
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 8 }}>
        Programme totals roll up from the layers: lines are limit-weighted, brokerage is premium-weighted, RARC is line-size-weighted (limit × signed line), and Loss Ratio is derived from modelled expected loss / (deposit premium + RIP). Saving creates a new version of the programme's structure.
      </div>
    </div>
  );
}
