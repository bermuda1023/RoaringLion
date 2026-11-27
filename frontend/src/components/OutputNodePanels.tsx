import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiUrl, fetchJson, postJson } from '../api/client';
import type { SimSetDetail } from '../api/client';
import type { GraphNodeSpec, GraphSpec, NodeViewMetrics, NodeYeltSummary, ViewOfRisk } from '../types/contracts';
import { fmtInt, fmtMoneyM, fmtRatioPct } from '../utils/format';

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
  graph,
  adjusted,
  unadjusted,
}: {
  node: GraphNodeSpec;
  graph: GraphSpec;
  adjusted?: NodeViewMetrics;
  unadjusted?: NodeViewMetrics;
}) {
  const contractId = typeof node.params.contractId === 'string' ? node.params.contractId : '';
  const layerNodeId = typeof node.params.layerNodeId === 'string' ? node.params.layerNodeId : '';
  const feeder = graph.edges
    .filter((edge) => edge.target === node.nodeId)
    .map((edge) => graph.nodes.find((item) => item.nodeId === edge.source))
    .find((item): item is GraphNodeSpec => Boolean(item));
  const isHedge = Boolean(adjusted?.pricing?.isHedge ?? unadjusted?.pricing?.isHedge);
  const fact = (metrics: NodeViewMetrics | undefined, label: string) => (
    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
      {label}
      <div style={{ color: 'var(--text)' }}>
        {metrics
          ? `${isHedge ? 'Exp Rec' : 'EL'} ${fmtMoneyM(metrics.el, 2)} · ${isHedge ? 'Rec/Cost' : 'ELR'} ${fmtRatioPct(metrics.elr, 1)}`
          : 'Not evaluated'}
      </div>
    </div>
  );
  return (
    <div data-testid="pricing-output-panel" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span className={`badge ${isHedge ? 'badge-hedge' : 'badge-long'}`}>
          {isHedge ? 'Hedge (Ceded)' : 'Long (Assumed)'}
        </span>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Identity
        <div className="mono" style={{ color: 'var(--text)' }}>{node.nodeId}</div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        CMS contract
        <div className="mono" style={{ color: 'var(--text)' }}>{contractId || 'Not linked'}</div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        CMS layer
        <div className="mono" style={{ color: 'var(--text)' }}>{layerNodeId || 'Not linked'}</div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Attached graph layer
        <div style={{ color: 'var(--text)' }}>{feeder ? feeder.label : 'Not connected'}</div>
      </div>
      {fact(adjusted, 'Adjusted')}
      {fact(unadjusted, 'Unadjusted')}
    </div>
  );
}
