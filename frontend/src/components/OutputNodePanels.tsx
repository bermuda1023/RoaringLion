import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, apiUrl, fetchJson, postJson } from '../api/client';
import type { SimSetDetail } from '../api/client';
import type { GraphNodeSpec, GraphSpec, NodeViewMetrics, NodeYeltSummary, PricingEconomics, ViewOfRisk } from '../types/contracts';
import { fmtInt, fmtMoneyM, fmtNum, fmtRatioPct } from '../utils/format';
import { LAYER_NODE_TYPES } from '../utils/nodeLabels';
import type { ContractLayer } from './ContractLayersEditor';

type Params = Record<string, unknown>;

function ViewSwitch({ value, onChange }: { value: ViewOfRisk; onChange: (v: ViewOfRisk) => void }) {
  return (
    <div className="seg">
      {(['ADJUSTED', 'UNADJUSTED'] as ViewOfRisk[]).map((v) => (
        <button key={v} type="button" className={v === value ? 'active' : ''} onClick={() => onChange(v)}>
          {v === 'ADJUSTED' ? 'Adjusted' : 'Unadjusted'}
        </button>
      ))}
    </div>
  );
}

function Kv({ k, v, tone }: { k: string; v: string; tone?: 'pos' | 'neg' | 'accent' }) {
  return (
    <div className="econ-row">
      <span>{k}</span>
      <b className={tone ? `tone-${tone}` : undefined}>{v}</b>
    </div>
  );
}

export function YeltOutputPanel({
  node,
  programmeId,
  saved,
  versionKey,
  onParams,
}: {
  node: GraphNodeSpec;
  programmeId: string;
  saved: boolean;
  versionKey: string;
  onParams: (params: Params) => void;
}) {
  const qc = useQueryClient();
  const view: ViewOfRisk = node.params.view === 'UNADJUSTED' ? 'UNADJUSTED' : 'ADJUSTED';
  const [name, setName] = useState('');
  const base = `/api/programmes/${programmeId}/nodes/${encodeURIComponent(node.nodeId)}/yelt`;

  const summaryQ = useQuery({
    queryKey: ['nodeYelt', programmeId, node.nodeId, view, versionKey],
    queryFn: () => fetchJson<NodeYeltSummary>(`${base}?view=${view}`),
    enabled: saved,
  });

  const saveMut = useMutation({
    mutationFn: () => postJson<SimSetDetail>(`${base}/save`, { view, name: name.trim() || undefined }),
    onSuccess: () => {
      setName('');
      qc.invalidateQueries({ queryKey: ['simSets'] });
    },
  });

  const s = summaryQ.data;
  return (
    <div className="output-panel">
      <div className="output-panel-head">
        <span>YELT</span>
        <ViewSwitch value={view} onChange={(v) => onParams({ ...node.params, view: v })} />
      </div>
      {!saved ? (
        <div className="label-terms muted">Save &amp; Re-Price to build the YELT for the current graph.</div>
      ) : summaryQ.isLoading ? (
        <div className="output-muted">Building YELT…</div>
      ) : summaryQ.error ? (
        <div className="error-banner">{(summaryQ.error as Error).message}</div>
      ) : s ? (
        <>
          <div className="output-muted">
            From {s.inputs.length ? s.inputs.map((i) => i.label).join(' + ') : 'no input — connect a node'}
          </div>
          <div className="econ-grid">
            <Kv k="Loss events" v={fmtInt(s.eventRows)} />
            <Kv k="Years with loss" v={`${fmtInt(s.trialsWithLoss)} / ${fmtInt(s.numTrials)}`} />
            <Kv k="AAL" v={fmtMoneyM(s.aal, 3)} tone="accent" />
            <Kv k="Std dev" v={fmtMoneyM(s.sd, 2)} />
            <Kv k="Max event" v={fmtMoneyM(s.maxEvent, 2)} />
            <Kv k="Max year" v={fmtMoneyM(s.maxYear, 2)} />
          </div>
          {s.topEvents.length > 0 && (
            <div className="table-wrap">
              <table className="compact-table">
                <thead>
                  <tr>
                    <th>Year</th>
                    <th>Zone-peril</th>
                    <th className="num">Day</th>
                    <th className="num">Loss</th>
                  </tr>
                </thead>
                <tbody>
                  {s.topEvents.slice(0, 5).map((e) => (
                    <tr key={`${e.trialId}-${e.eventId}`}>
                      <td>{fmtInt(e.trialId)}</td>
                      <td>{e.regionPeril}</td>
                      <td className="num">{e.dayOfYear}</td>
                      <td className="num">{fmtMoneyM(e.loss, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="output-actions">
            <a className="btn btn-ghost" href={apiUrl(`${base}/export?view=${view}&format=csv`)}>
              Export CSV
            </a>
            <a className="btn btn-ghost" href={apiUrl(`${base}/export?view=${view}&format=parquet`)}>
              Export Parquet
            </a>
          </div>
          <div className="output-save">
            <input value={name} placeholder={node.label} onChange={(e) => setName(e.target.value)} />
            <button className="btn" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || s.eventRows === 0}>
              {saveMut.isPending ? 'Saving…' : 'Save as Simulation Set'}
            </button>
          </div>
          {saveMut.data && (
            <div className="label-terms">
              <span>Saved</span>
              {saveMut.data.name} · {fmtInt(saveMut.data.totalEvents)} events · AAL {fmtMoneyM(saveMut.data.expectedAnnualLoss, 3)}
            </div>
          )}
          {saveMut.error && <div className="error-banner">{(saveMut.error as Error).message}</div>}
        </>
      ) : null}
    </div>
  );
}

export function PricingOutputPanel({
  node,
  programmeId,
  draftGraph,
  saved,
  adj,
  unadj,
  onParams,
}: {
  node: GraphNodeSpec;
  programmeId: string;
  draftGraph: GraphSpec;
  saved: boolean;
  adj?: NodeViewMetrics;
  unadj?: NodeViewMetrics;
  onParams: (params: Params) => void;
}) {
  const [view, setView] = useState<ViewOfRisk>('ADJUSTED');
  const contractId = typeof node.params.contractId === 'string' ? node.params.contractId : '';
  const layerNodeId = typeof node.params.layerNodeId === 'string' ? node.params.layerNodeId : '';

  const contractsQ = useQuery({ queryKey: ['contracts', 'pricing-link'], queryFn: () => api.listContracts() });
  const layersQ = useQuery({
    queryKey: ['contractLayers', contractId],
    queryFn: () => fetchJson<ContractLayer[]>(`/api/contracts/${contractId}/layers`),
    enabled: Boolean(contractId),
  });

  const contracts = contractsQ.data ?? [];
  const contract = contracts.find((c) => c.programmeId === contractId);
  const code = (c: (typeof contracts)[number]) => c.profile?.uniqueCode || c.programmeId;

  // Feeder is ALWAYS the upstream DAG node(s) wired into this Pricing Output.
  const feeders = draftGraph.edges
    .filter((e) => e.target === node.nodeId)
    .map((e) => draftGraph.nodes.find((n) => n.nodeId === e.source))
    .filter((n): n is GraphNodeSpec => Boolean(n));

  // Build 1:1 occupancy map across other Pricing Outputs in the live draft graph and all other saved contracts.
  const occupiedByKey = new Map<string, { label: string; owner: string }>();
  const firstLayerIdOf = (cid: string): string => {
    const g = cid === programmeId ? draftGraph : contracts.find((c) => c.programmeId === cid)?.graph;
    const first = g?.nodes.find((n) => LAYER_NODE_TYPES.includes(n.nodeType));
    return first?.nodeId ?? '';
  };

  for (const otherPx of draftGraph.nodes) {
    if (otherPx.nodeType !== 'PRICING_OUTPUT' || otherPx.nodeId === node.nodeId) continue;
    const cid = typeof otherPx.params.contractId === 'string' ? otherPx.params.contractId.trim() : '';
    if (!cid) continue;
    const lid = (typeof otherPx.params.layerNodeId === 'string' ? otherPx.params.layerNodeId.trim() : '') || firstLayerIdOf(cid);
    if (lid) occupiedByKey.set(`${cid}::${lid}`, { label: otherPx.label, owner: 'this programme' });
  }
  for (const c of contracts) {
    if (c.programmeId === programmeId) continue;
    const owner = code(c);
    for (const otherPx of c.graph.nodes) {
      if (otherPx.nodeType !== 'PRICING_OUTPUT') continue;
      const cid = typeof otherPx.params.contractId === 'string' ? otherPx.params.contractId.trim() : '';
      if (!cid) continue;
      const lid = (typeof otherPx.params.layerNodeId === 'string' ? otherPx.params.layerNodeId.trim() : '') || firstLayerIdOf(cid);
      if (lid && !occupiedByKey.has(`${cid}::${lid}`)) {
        occupiedByKey.set(`${cid}::${lid}`, { label: otherPx.label, owner });
      }
    }
  }

  // Layers available for the selected contract (prefer live draft layers when targeting the current programme).
  const targetLayers: { nodeId: string; layerCode: string; label: string; name: string }[] =
    contractId === programmeId
      ? draftGraph.nodes
          .filter((n) => LAYER_NODE_TYPES.includes(n.nodeType))
          .map((n) => ({
            nodeId: n.nodeId,
            layerCode: typeof n.params.layerCode === 'string' ? n.params.layerCode.trim() : '',
            label: n.label,
            name: typeof n.params.labelName === 'string' ? n.params.labelName.trim() : n.label,
          }))
      : (layersQ.data ?? []).map((l) => ({
          nodeId: l.nodeId,
          layerCode: l.layerCode ?? '',
          label: l.label,
          name: l.name || l.label,
        }));

  const effectiveLayerId = layerNodeId || targetLayers[0]?.nodeId || '';
  const selectedLayer = targetLayers.find((l) => l.nodeId === effectiveLayerId);
  const activeConflict = contractId && effectiveLayerId ? occupiedByKey.get(`${contractId}::${effectiveLayerId}`) : undefined;

  const link = (cid: string, layer?: { nodeId: string; layerCode?: string; name?: string; label?: string }) => {
    const next: Params = { ...node.params };
    delete next.contractId;
    delete next.layerNodeId;
    delete next.linkedLabel;
    delete next.unlinked;
    if (!cid) {
      next.unlinked = true;
      onParams(next);
      return;
    }
    const c = contracts.find((x) => x.programmeId === cid);
    let chosen = layer;
    if (!chosen) {
      const candidateGraph = cid === programmeId ? draftGraph : c?.graph;
      const candidates = (candidateGraph?.nodes ?? [])
        .filter((n) => LAYER_NODE_TYPES.includes(n.nodeType))
        .map((n) => ({
          nodeId: n.nodeId,
          layerCode: typeof n.params.layerCode === 'string' ? n.params.layerCode.trim() : '',
          name: typeof n.params.labelName === 'string' ? n.params.labelName.trim() : n.label,
          label: n.label,
        }));
      chosen = candidates.find((cand) => !occupiedByKey.has(`${cid}::${cand.nodeId}`)) ?? candidates[0];
    }
    next.contractId = cid;
    if (chosen) next.layerNodeId = chosen.nodeId;
    const layerSummary = [chosen?.layerCode, chosen?.name || chosen?.label].filter(Boolean).join(' · ');
    next.linkedLabel = [c ? code(c) : cid, layerSummary].filter(Boolean).join(' · ');
    onParams(next);
  };

  const pricing: PricingEconomics | null | undefined = (view === 'ADJUSTED' ? adj : unadj)?.pricing;
  const profitTone = pricing && pricing.expectedProfit < 0 ? 'neg' : 'pos';
  const isLinked = Boolean(contractId);

  return (
    <div className="output-panel">
      <div className="output-panel-head">
        <span>Pricing Output</span>
        <ViewSwitch value={view} onChange={setView} />
      </div>

      {/* 1. Feeder (always the upstream DAG layer/node feeding this output) */}
      <div className="pricing-feeder-box">
        <div className="pricing-subhead">
          <span>Feeder (loss stream)</span>
          <span className="pricing-hint">Always upstream</span>
        </div>
        {feeders.length > 0 ? (
          <div className="pricing-feeder-list">
            {feeders.map((f) => {
              const fc =
                LAYER_NODE_TYPES.includes(f.nodeType) && typeof f.params.layerCode === 'string' && f.params.layerCode.trim()
                  ? f.params.layerCode.trim()
                  : null;
              return (
                <div key={f.nodeId} className="pricing-feeder-item">
                  {fc && <span className="layer-code-pill">{fc}</span>}
                  <span>{f.label}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="output-muted">Not connected — wire a layer node into this Pricing Output.</div>
        )}
      </div>

      {/* 2. Optional 1:1 CMS Contract Layer link (controls what rolls up) */}
      <div className="pricing-link-section">
        <div className="pricing-subhead">
          <span>CMS Contract Link (1:1)</span>
          <span className={`pricing-link-badge ${isLinked ? 'linked' : 'unlinked'}`}>
            {isLinked ? 'Linked · Rolls up' : 'Canvas only'}
          </span>
        </div>
        <label className="output-field">
          CMS Contract
          <select value={contractId} onChange={(e) => link(e.target.value)}>
            <option value="">Unlinked — canvas pricing only (excluded from rollup)</option>
            {contracts.map((c) => (
              <option key={c.programmeId} value={c.programmeId}>
                {code(c)} · {c.cedentName} — {c.programmeName}
              </option>
            ))}
          </select>
        </label>
        {contractId && (
          <label className="output-field">
            CMS Layer (1:1)
            <select
              value={effectiveLayerId}
              onChange={(e) => link(contractId, targetLayers.find((l) => l.nodeId === e.target.value))}
            >
              {targetLayers.map((l) => {
                const occ = occupiedByKey.get(`${contractId}::${l.nodeId}`);
                return (
                  <option key={l.nodeId} value={l.nodeId} disabled={Boolean(occ)}>
                    {l.layerCode ? `${l.layerCode} · ` : ''}
                    {l.label}
                    {occ ? ` (linked to ${occ.label})` : ''}
                  </option>
                );
              })}
            </select>
          </label>
        )}
        {activeConflict && (
          <div className="error-banner">
            Already linked to {activeConflict.label} ({activeConflict.owner}). Unlink it there first — Pricing Outputs are 1:1 with CMS layers.
          </div>
        )}
        {contractId && !contract && contractsQ.isSuccess && (
          <div className="error-banner">The linked contract no longer exists.</div>
        )}
        <div className="output-muted">
          {isLinked ? (
            <>
              Maps feeder losses to{' '}
              <b>
                {contract ? code(contract) : contractId}
                {selectedLayer?.layerCode ? ` · ${selectedLayer.layerCode}` : ''}
              </b>{' '}
              for contract &amp; portfolio rollups.
            </>
          ) : (
            'Priced on canvas using feeder terms only. Link to a CMS layer to include in contract & portfolio rollups.'
          )}
        </div>
      </div>

      {!saved ? (
        <div className="label-terms muted">Save &amp; Re-Price to flow economics through this node.</div>
      ) : !pricing ? (
        <div className="label-terms muted">
          Connect a feeder layer node to price this output.
        </div>
      ) : (
        <>
          <div className="output-muted">
            {pricing.source === 'LINKED' ? 'Linked CMS layer' : 'Canvas preview (unlinked)'}: <b>{pricing.contractCode}</b>
            {pricing.layerCode ? <> · <b>{pricing.layerCode}</b></> : null} · {pricing.layerLabel}
          </div>
          <div className="econ-grid">
            <Kv k="Limit / Attach (100%)" v={`${fmtMoneyM(pricing.limit, 1)} xs ${fmtMoneyM(pricing.attachment, 1)}`} />
            <Kv k="Premium (100%)" v={fmtMoneyM(pricing.premium, 2)} />
            <Kv k="Written / Signed" v={`${fmtRatioPct(pricing.writtenLine, 1)} / ${fmtRatioPct(pricing.signedLine, 1)}`} />
            <Kv k="Our premium" v={fmtMoneyM(pricing.ourPremium, 2)} />
            <Kv k={`Brokerage (${fmtRatioPct(pricing.brokeragePct, 1)})`} v={fmtMoneyM(-pricing.brokerage, 2)} />
            <Kv k="Expected RIP" v={fmtMoneyM(pricing.expectedRip, 3)} />
            <Kv k="Expected loss" v={fmtMoneyM(-pricing.expectedLoss, 3)} />
            <Kv k="Expected profit" v={fmtMoneyM(pricing.expectedProfit, 2)} tone={profitTone} />
            <Kv k="Loss ratio" v={fmtRatioPct(pricing.lossRatio, 1)} />
            <Kv k="Combined ratio" v={fmtRatioPct(pricing.combinedRatio, 1)} />
            <Kv k="ROL / LOL" v={`${fmtRatioPct(pricing.rol, 1)} / ${fmtRatioPct(pricing.lossOnLine, 2)}`} />
            <Kv k="Multiple" v={pricing.multiple != null ? `${fmtNum(pricing.multiple, 2)}×` : '—'} />
            <Kv k="1-in-250 TVaR (ours)" v={fmtMoneyM(pricing.tvar250, 2)} />
            <Kv k="Return on TVaR" v={fmtRatioPct(pricing.returnOnTvar, 1)} tone={profitTone} />
            <Kv
              k="vs Technical ROL"
              v={
                pricing.priceAdequacy != null
                  ? `${fmtRatioPct(pricing.priceAdequacy, 0)} of ${fmtRatioPct(pricing.technicalRol, 1)}`
                  : '—'
              }
              tone={pricing.priceAdequacy != null ? (pricing.priceAdequacy >= 1 ? 'pos' : 'neg') : undefined}
            />
          </div>
          {pricing.warnings.map((w) => (
            <div key={w} className="output-warning">{w}</div>
          ))}
        </>
      )}
    </div>
  );
}
