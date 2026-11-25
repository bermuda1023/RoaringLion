import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, apiUrl, fetchJson, postJson } from '../api/client';
import type { SimSetDetail } from '../api/client';
import type { GraphNodeSpec, NodeViewMetrics, NodeYeltSummary, PricingEconomics, ViewOfRisk } from '../types/contracts';
import { fmtInt, fmtMoneyM, fmtNum, fmtRatioPct } from '../utils/format';
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
  saved,
  adj,
  unadj,
  onParams,
}: {
  node: GraphNodeSpec;
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

  const link = (cid: string, layer?: ContractLayer) => {
    const next: Params = { ...node.params };
    delete next.contractId;
    delete next.layerNodeId;
    delete next.linkedLabel;
    if (cid) {
      const c = contracts.find((x) => x.programmeId === cid);
      next.contractId = cid;
      if (layer) next.layerNodeId = layer.nodeId;
      const layerName = layer?.name || layer?.label;
      next.linkedLabel = [c ? code(c) : cid, layerName].filter(Boolean).join(' · ');
    }
    onParams(next);
  };

  const pricing: PricingEconomics | null | undefined = (view === 'ADJUSTED' ? adj : unadj)?.pricing;
  const profitTone = pricing && pricing.expectedProfit < 0 ? 'neg' : 'pos';

  return (
    <div className="output-panel">
      <div className="output-panel-head">
        <span>Economics</span>
        <ViewSwitch value={view} onChange={setView} />
      </div>
      <label className="output-field">
        Contract
        <select value={contractId} onChange={(e) => link(e.target.value)}>
          <option value="">Auto — layer feeding this node</option>
          {contracts.map((c) => (
            <option key={c.programmeId} value={c.programmeId}>
              {code(c)} · {c.cedentName} — {c.programmeName}
            </option>
          ))}
        </select>
      </label>
      {contractId && (
        <label className="output-field">
          Layer
          <select
            value={layerNodeId}
            onChange={(e) => link(contractId, (layersQ.data ?? []).find((l) => l.nodeId === e.target.value))}
          >
            {!layerNodeId && <option value="">First layer</option>}
            {(layersQ.data ?? []).map((l) => (
              <option key={l.nodeId} value={l.nodeId}>
                {l.layerCode ? `${l.layerCode} · ` : ''}{l.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {contractId && !contract && contractsQ.isSuccess && (
        <div className="error-banner">The linked contract no longer exists.</div>
      )}

      {!saved ? (
        <div className="label-terms muted">Save &amp; Re-Price to flow the contract economics through this node.</div>
      ) : !pricing ? (
        <div className="label-terms muted">
          Connect a layer node, or link a contract, to price this output.
        </div>
      ) : (
        <>
          <div className="output-muted">
            {pricing.source === 'LINKED' ? 'Linked' : 'From upstream'}: <b>{pricing.contractCode}</b>
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
