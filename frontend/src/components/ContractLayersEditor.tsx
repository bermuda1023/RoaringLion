import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { fetchJson } from '../api/client';
import { fmtMoneyM, fmtNum, fmtRatioPct } from '../utils/format';
import { composeLabel, nodeTerms } from '../utils/nodeLabels';

export interface ContractLayer {
  nodeId: string;
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
  editable: string[];
  name: string;
  terms: string | null;
  autoLabel: boolean;
}

type Field = 'label' | 'limit' | 'attachment' | 'depositPremium' | 'reinstatements' | 'ripRate' | 'writtenLine' | 'signedLine';
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

const PCT_FIELDS: Field[] = ['writtenLine', 'signedLine', 'ripRate'];

function initialText(layer: ContractLayer, field: Field): string {
  const v = layer[field as keyof ContractLayer] as number | string | null;
  if (field === 'label') return layer.autoLabel ? layer.name : layer.label;
  if (v == null) return '';
  if (PCT_FIELDS.includes(field)) return String(Number(((v as number) * 100).toFixed(4)));
  return String(Number((v as number).toFixed(6)));
}

interface NewLayerDraft {
  name: string;
  layerType: 'OCC_XL' | 'AGG_XL';
  limit: string;
  attachment: string;
  depositPremium: string;
  reinstatements: string;
  writtenLine: string;
  signedLine: string;
}

function blankLayer(layers: ContractLayer[]): NewLayerDraft {
  const top = layers.reduce<ContractLayer | null>(
    (best, l) => (l.limit != null && l.attachment != null && (!best || l.attachment + l.limit > (best.attachment ?? 0) + (best.limit ?? 0)) ? l : best),
    null,
  );
  return {
    name: '',
    layerType: 'OCC_XL',
    limit: top?.limit != null ? String(top.limit) : '',
    attachment: top?.limit != null && top.attachment != null ? String(top.attachment + top.limit) : '',
    depositPremium: '',
    reinstatements: '1',
    writtenLine: top ? String(Number((top.writtenLine * 100).toFixed(4))) : '100',
    signedLine: top ? String(Number((top.signedLine * 100).toFixed(4))) : '100',
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

  const layersQ = useQuery({
    queryKey: ['contractLayers', programmeId],
    queryFn: () => fetchJson<ContractLayer[]>(`/api/contracts/${programmeId}/layers`),
  });

  useEffect(() => {
    setDraft({});
    setSavedMsg(null);
    setNewLayer(null);
  }, [programmeId]);

  const text = (layer: ContractLayer, field: Field) => draft[layer.nodeId]?.[field] ?? initialText(layer, field);
  const setField = (nodeId: string, field: Field, value: string) => {
    setSavedMsg(null);
    setDraft((d) => ({ ...d, [nodeId]: { ...d[nodeId], [field]: value } }));
  };

  const live = (layer: ContractLayer) => {
    const num = (f: Field, fallback: number | null) => {
      const t = draft[layer.nodeId]?.[f];
      return t === undefined ? fallback : parseNum(t);
    };
    const limit = layer.editable.includes('limit') ? num('limit', layer.limit) : layer.limit;
    const premium = layer.editable.includes('depositPremium') ? num('depositPremium', layer.depositPremium) ?? 0 : layer.depositPremium;
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
    return {
      terms,
      limit,
      premium,
      written,
      signed,
      rol: limit ? premium / limit : null,
      ourLimit: limit != null && signed != null ? limit * signed : null,
      ourPremium: signed != null ? premium * signed : null,
    };
  };

  const layers = layersQ.data ?? [];
  const dirtyIds = Object.keys(draft).filter((id) => Object.keys(draft[id] ?? {}).length > 0);

  const problems: string[] = [];
  for (const layer of layers) {
    const l = live(layer);
    for (const [name, v] of [
      ['Written line', l.written],
      ['Signed line', l.signed],
    ] as const) {
      if (v == null) problems.push(`${layer.label}: ${name} is required`);
      else if (v < 0 || v > 1) problems.push(`${layer.label}: ${name} must be between 0% and 100%`);
    }
    if (layer.editable.includes('limit') && (l.limit == null || l.limit <= 0)) problems.push(`${layer.label}: limit must be greater than 0`);
  }
  const warnings = layers
    .map((layer) => ({ layer, l: live(layer) }))
    .filter(({ l }) => l.written != null && l.signed != null && l.signed > l.written + 1e-9)
    .map(({ layer }) => `${layer.label}: signed line is above written line`);

  const totals = layers.reduce(
    (acc, layer) => {
      const l = live(layer);
      acc.limit += l.limit ?? 0;
      acc.premium += l.premium;
      acc.ourLimit += l.ourLimit ?? 0;
      acc.ourPremium += l.ourPremium ?? 0;
      return acc;
    },
    { limit: 0, premium: 0, ourLimit: 0, ourPremium: 0 },
  );

  const saveMut = useMutation({
    mutationFn: () => {
      const payload = dirtyIds.map((nodeId) => {
        const d = draft[nodeId] ?? {};
        const out: Record<string, unknown> = { nodeId };
        if (d.label !== undefined) out.label = d.label;
        for (const f of ['limit', 'attachment', 'depositPremium', 'reinstatements'] as const) {
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
      setDraft({});
      setSavedMsg('Layers saved. Pricing, portfolio and optimizer now use the updated terms and lines.');
      onSaved();
    },
  });

  const newLayerProblems: string[] = [];
  if (newLayer) {
    const lim = parseNum(newLayer.limit);
    const att = parseNum(newLayer.attachment);
    const w = parseNum(newLayer.writtenLine);
    const sg = parseNum(newLayer.signedLine);
    if (lim == null || lim <= 0) newLayerProblems.push('limit must be greater than 0');
    if (att == null || att < 0) newLayerProblems.push('attachment must be 0 or more');
    if ((parseNum(newLayer.depositPremium) ?? 0) < 0) newLayerProblems.push('premium must be 0 or more');
    for (const [label, v] of [['written', w], ['signed', sg]] as const) {
      if (v == null || v < 0 || v > 100) newLayerProblems.push(`${label} line must be 0–100%`);
    }
  }

  const addMut = useMutation({
    mutationFn: (nl: NewLayerDraft) =>
      fetchJson<ContractLayer[]>(`/api/contracts/${programmeId}/layers`, {
        method: 'POST',
        body: JSON.stringify({
          name: nl.name.trim() || undefined,
          layerType: nl.layerType,
          limit: parseNum(nl.limit),
          attachment: parseNum(nl.attachment),
          depositPremium: parseNum(nl.depositPremium) ?? 0,
          reinstatements: nl.layerType === 'OCC_XL' ? parseNum(nl.reinstatements) : undefined,
          writtenLine: (parseNum(nl.writtenLine) ?? 100) / 100,
          signedLine: (parseNum(nl.signedLine) ?? 100) / 100,
        }),
      }),
    onSuccess: (updated) => {
      qc.setQueryData(['contractLayers', programmeId], updated);
      const added = updated[updated.length - 1];
      setNewLayer(null);
      setSavedMsg(`Added ${added?.label ?? 'layer'}. It is wired into the structure with its own pricing output.`);
      onSaved();
    },
  });

  const removeMut = useMutation({
    mutationFn: (nodeId: string) =>
      fetchJson<ContractLayer[]>(`/api/contracts/${programmeId}/layers/${encodeURIComponent(nodeId)}`, { method: 'DELETE' }),
    onSuccess: (updated, nodeId) => {
      qc.setQueryData(['contractLayers', programmeId], updated);
      setDraft(({ [nodeId]: _removed, ...rest }) => rest);
      setSavedMsg('Layer removed. Programme totals and lines updated.');
      onSaved();
    },
  });

  const nlField = (key: keyof NewLayerDraft, opts: { width?: number; step?: string; placeholder?: string } = {}) =>
    newLayer && (
      <input
        type={key === 'name' ? 'text' : 'number'}
        step={opts.step ?? 'any'}
        placeholder={opts.placeholder}
        value={newLayer[key]}
        onChange={(e) => setNewLayer({ ...newLayer, [key]: e.target.value })}
        className="cell-input changed"
        style={{ width: opts.width ?? 86, textAlign: key === 'name' ? 'left' : 'right' }}
      />
    );

  const cell = (layer: ContractLayer, field: Field, opts: { width?: number; step?: string; suffix?: string } = {}) => {
    if (!layer.editable.includes(field)) {
      const v = initialText(layer, field);
      return <span style={{ color: 'var(--text-faint)' }}>{v === '' ? '—' : opts.suffix ? `${v}${opts.suffix}` : v}</span>;
    }
    const changed = draft[layer.nodeId]?.[field] !== undefined;
    return (
      <input
        type={field === 'label' ? 'text' : 'number'}
        step={opts.step ?? 'any'}
        value={text(layer, field)}
        onChange={(e) => setField(layer.nodeId, field, e.target.value)}
        className={`cell-input ${changed ? 'changed' : ''}`}
        style={{ width: opts.width ?? 86, textAlign: field === 'label' ? 'left' : 'right' }}
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
            Limits, attachments and premiums at 100% ({currency} M). Lines in %. Our share = 100% amount × signed line.
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
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Layer name</th>
                <th>Type</th>
                <th className="num">Limit</th>
                <th className="num">Attachment</th>
                <th className="num">Premium (100%)</th>
                <th className="num">ROL</th>
                <th className="num">Reinst.</th>
                <th className="num">RIP %</th>
                <th className="num">Written %</th>
                <th className="num">Signed %</th>
                <th className="num">Our Limit</th>
                <th className="num">Our Premium</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {layers.map((layer) => {
                const l = live(layer);
                return (
                  <tr key={layer.nodeId}>
                    <td>
                      {cell(layer, 'label', { width: 230 })}
                      {layer.autoLabel && l.terms && (
                        <div className="layer-label-preview" title="Full label (updates automatically from the terms)">
                          {composeLabel(text(layer, 'label'), l.terms)}
                        </div>
                      )}
                    </td>
                    <td style={{ color: 'var(--text-muted)' }}>{TYPE_LABEL[layer.nodeType] ?? layer.nodeType}</td>
                    <td className="num">{cell(layer, 'limit', { step: '1' })}</td>
                    <td className="num">{cell(layer, 'attachment', { step: '1' })}</td>
                    <td className="num">{cell(layer, 'depositPremium', { step: '0.1' })}</td>
                    <td className="num">{l.rol == null ? '—' : fmtRatioPct(l.rol, 2)}</td>
                    <td className="num">{cell(layer, 'reinstatements', { width: 64, step: '1' })}</td>
                    <td className="num">{cell(layer, 'ripRate', { width: 72, step: '5', suffix: '%' })}</td>
                    <td className="num">{cell(layer, 'writtenLine', { width: 76, step: '0.5' })}</td>
                    <td className="num">{cell(layer, 'signedLine', { width: 76, step: '0.5' })}</td>
                    <td className="num" style={{ fontWeight: 600 }}>
                      {l.ourLimit == null ? '—' : fmtMoneyM(l.ourLimit, 2)}
                    </td>
                    <td className="num" style={{ fontWeight: 600 }}>
                      {l.ourPremium == null ? '—' : fmtMoneyM(l.ourPremium, 2)}
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
                return (
                  <tr className="new-layer-row">
                    <td>
                      {nlField('name', { width: 230, placeholder: 'Layer name (optional)' })}
                      {terms && <div className="layer-label-preview">{composeLabel(newLayer.name.trim() || 'New layer', terms)}</div>}
                    </td>
                    <td>
                      <select
                        value={newLayer.layerType}
                        onChange={(e) => setNewLayer({ ...newLayer, layerType: e.target.value as NewLayerDraft['layerType'] })}
                        className="cell-input changed"
                      >
                        <option value="OCC_XL">Occurrence XL</option>
                        <option value="AGG_XL">Aggregate XL</option>
                      </select>
                    </td>
                    <td className="num">{nlField('limit', { step: '1' })}</td>
                    <td className="num">{nlField('attachment', { step: '1' })}</td>
                    <td className="num">{nlField('depositPremium', { step: '0.1', placeholder: '0' })}</td>
                    <td className="num">{lim ? fmtRatioPct(prem / lim, 2) : '—'}</td>
                    <td className="num">{newLayer.layerType === 'OCC_XL' ? nlField('reinstatements', { width: 64, step: '1' }) : '—'}</td>
                    <td className="num" style={{ color: 'var(--text-faint)' }}>{newLayer.layerType === 'OCC_XL' ? '100%' : '—'}</td>
                    <td className="num">{nlField('writtenLine', { width: 76, step: '0.5' })}</td>
                    <td className="num">{nlField('signedLine', { width: 76, step: '0.5' })}</td>
                    <td className="num">{lim != null && sg != null ? fmtMoneyM((lim * sg) / 100, 2) : '—'}</td>
                    <td className="num">{sg != null ? fmtMoneyM((prem * sg) / 100, 2) : '—'}</td>
                    <td>
                      <button
                        className="btn"
                        onClick={() => addMut.mutate(newLayer)}
                        disabled={newLayerProblems.length > 0 || addMut.isPending}
                        title={newLayerProblems.join(' · ')}
                      >
                        {addMut.isPending ? 'Adding…' : 'Add'}
                      </button>
                    </td>
                  </tr>
                );
              })()}
              <tr className="total-row">
                <td>Total</td>
                <td />
                <td className="num">{fmtMoneyM(totals.limit, 1)}</td>
                <td />
                <td className="num">{fmtMoneyM(totals.premium, 2)}</td>
                <td className="num">{totals.limit ? fmtRatioPct(totals.premium / totals.limit, 2) : '—'}</td>
                <td />
                <td />
                <td className="num" style={{ color: 'var(--text-muted)' }}>
                  {totals.limit
                    ? fmtNum(layers.reduce((s, x) => s + (live(x).limit ?? 0) * (live(x).written ?? 0), 0) / totals.limit * 100, 1) + '%'
                    : '—'}
                </td>
                <td className="num" style={{ color: 'var(--text-muted)' }}>
                  {totals.limit ? fmtNum((totals.ourLimit / totals.limit) * 100, 1) + '%' : '—'}
                </td>
                <td className="num">{fmtMoneyM(totals.ourLimit, 2)}</td>
                <td className="num">{fmtMoneyM(totals.ourPremium, 2)}</td>
                <td />
              </tr>
            </tbody>
          </table>
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 8 }}>
        Programme-level written and signed lines are the limit-weighted average of the layers. Saving creates a new version of the
        programme's structure.
      </div>
    </div>
  );
}
