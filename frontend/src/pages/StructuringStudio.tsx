import {
  Background,
  Controls,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, apiUrl } from '../api/client';
import { BreadcrumbBar } from '../components/BreadcrumbBar';
import { PricingOutputPanel, YeltOutputPanel } from '../components/OutputNodePanels';
import type { GraphEdgeSpec, GraphNodeSpec, GraphSpec, GraphTemplate, ProgrammeDeal } from '../types/contracts';
import { applyCmsLayerSuggestion, claimedPricingLinks, SUGGESTED_FROM_PARAM } from '../utils/cmsLayerSuggestion';
import { fmtMoneyB, fmtMoneyM, fmtNum, fmtPct, fmtRatioPct, NODE_PARAM_META } from '../utils/format';
import {
  isAutoLabel,
  LABEL_META_KEYS,
  labelName,
  LAYER_META_KEYS,
  LAYER_NODE_TYPES,
  nameFromLabel,
  nextLayerCode,
  nodeTerms,
  OUTPUT_LINK_KEYS,
  smartLabel,
} from '../utils/nodeLabels';

const AVAILABLE_ZONES = ['US-FL', 'US-TX', 'US-SE', 'US-CA', 'JP-KANTO', 'EU-DE'];
const AVAILABLE_PERILS = [
  { code: 'WS', label: 'WS (Windstorm)' },
  { code: 'EQ', label: 'EQ (Earthquake)' },
];
const AVAILABLE_REGION_PERILS = ['US-FL-WS', 'US-TX-WS', 'US-SE-WS', 'US-CA-EQ', 'JP-KANTO-EQ', 'EU-DE-WS'];

function withParams(n: GraphNodeSpec, params: Record<string, unknown>): GraphNodeSpec {
  return { ...n, params, label: smartLabel(n.nodeType, params, n.label) };
}

/** True when the draft would evaluate the same as the saved graph, ignoring layout and the given params on one node. */
function evaluatesLikeSaved(draft: GraphSpec, saved: GraphSpec, nodeId: string, ignoreKeys: string[]): boolean {
  const norm = (g: GraphSpec) =>
    JSON.stringify({
      nodes: g.nodes.map((n) =>
        n.nodeId === nodeId
          ? { id: n.nodeId, t: n.nodeType, p: Object.fromEntries(Object.entries(n.params).filter(([k]) => !ignoreKeys.includes(k) && !LABEL_META_KEYS.includes(k))) }
          : { id: n.nodeId, t: n.nodeType, p: n.params },
      ),
      edges: g.edges.map((e) => [e.source, e.target, e.sign, e.weight]),
    });
  return saved.nodes.some((n) => n.nodeId === nodeId) && norm(draft) === norm(saved);
}

function nodeAccent(nodeType: string): string {
  if (nodeType === 'LOSS_SOURCE' || nodeType === 'INDUSTRY_INDEX_SOURCE') return '#8a9bb0';
  if (nodeType.includes('FILTER') || nodeType === 'COMBINATION' || nodeType === 'UPLIFT') return '#9a9082';
  if (
    nodeType === 'OCC_XL' ||
    nodeType === 'AGG_XL' ||
    nodeType === 'QUOTA_SHARE' ||
    nodeType === 'AGG_QUOTA_SHARE' ||
    nodeType === 'SURPLUS_SHARE' ||
    nodeType === 'CASCADING_TOWER' ||
    nodeType === 'ILW_OPTION'
  )
    return '#e8a84a';
  return '#7a9e8e';
}

function nodeStyle(nodeType: string, selected: boolean): React.CSSProperties {
  const accent = nodeAccent(nodeType);
  const edgeColor = selected ? 'var(--accent)' : 'var(--border-strong)';
  return {
    borderRadius: 8,
    padding: '8px 12px',
    fontSize: 11,
    fontWeight: 500,
    color: 'var(--text)',
    background: 'var(--surface-2)',
    borderTop: `1px solid ${edgeColor}`,
    borderRight: `1px solid ${edgeColor}`,
    borderBottom: `1px solid ${edgeColor}`,
    borderLeft: `3px solid ${accent}`,
    boxShadow: selected ? '0 0 0 3px rgba(232, 168, 74, 0.22)' : '0 8px 18px rgba(0, 0, 0, 0.32)',
    minWidth: 185,
    textAlign: 'left',
  };
}

export function StructuringStudio({
  programmes,
  selectedProg,
  onSelectProgramme,
}: {
  programmes: ProgrammeDeal[];
  selectedProg: ProgrammeDeal;
  onSelectProgramme: (id: string) => void;
}) {
  const qc = useQueryClient();
  const [draftGraph, setDraftGraph] = useState<GraphSpec>(selectedProg.graph);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  // Raw string while editing so typing '-' does not collapse to NaN and flip sign.
  const [flUplift, setFlUplift] = useState<number | string>(selectedProg.upliftSet.rules[0]?.flatSeverityFactor ?? 1.1);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  }, [selectedProg.programmeId]);

  useEffect(() => {
    setDraftGraph(selectedProg.graph);
    setFlUplift(selectedProg.upliftSet.rules[0]?.flatSeverityFactor ?? 1.1);
    setSelectedNodeId((id) => (id && selectedProg.graph.nodes.some((n) => n.nodeId === id) ? id : null));
    setSelectedEdgeId((id) => (id && selectedProg.graph.edges.some((e) => e.edgeId === id) ? id : null));
    setDirty(false);
  }, [selectedProg.programmeId, selectedProg.graph.version]);

  const templatesQ = useQuery({
    queryKey: ['graphTemplates', selectedProg.programmeId, selectedProg.graph.version],
    queryFn: () => api.listGraphTemplates(selectedProg.programmeId),
  });

  const dagEvalQ = useQuery({
    queryKey: ['dagEval', selectedProg.programmeId, selectedProg.graph.version, selectedProg.upliftSet.version],
    queryFn: () => api.evaluateProgramme(selectedProg.programmeId),
  });

  const saveGraphMut = useMutation({
    mutationFn: () => {
      // Params edited through the inspector may hold numeric strings; restore numbers and turn blank optional caps into null.
      const optionalKeys = new Set(['perEventCap', 'eventCap', 'aggCap', 'maxOrder', 'maxLoss']);
      const clean: GraphSpec = {
        ...draftGraph,
        nodes: draftGraph.nodes.map((n) => ({
          ...n,
          params: Object.fromEntries(
            Object.entries(n.params).map(([k, v]) => {
              if (LABEL_META_KEYS.includes(k) || LAYER_META_KEYS.includes(k) || OUTPUT_LINK_KEYS.includes(k)) return [k, v];
              if (typeof v === 'string' && v.trim() === '' && optionalKeys.has(k)) return [k, null];
              if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) return [k, Number(v)];
              return [k, v];
            }),
          ),
        })),
      };
      return api.updateGraph(selectedProg.programmeId, clean);
    },
    onSuccess: () => {
      setDirty(false);
      qc.invalidateQueries({ queryKey: ['programmes'] });
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['dagEval'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
      qc.invalidateQueries({ queryKey: ['staleness'] });
      qc.invalidateQueries({ queryKey: ['contractLayers'] });
      qc.invalidateQueries({ queryKey: ['graphTemplates'] });
    },
  });

  const upliftMut = useMutation({
    mutationFn: async () => {
      const factor = typeof flUplift === 'string' ? Number(flUplift) : flUplift;
      const baseRules = selectedProg.upliftSet.rules.length
        ? selectedProg.upliftSet.rules
        : [{ regionPerilKey: 'ALL', flatSeverityFactor: factor, curvePoints: [], additiveLoad: 0 }];
      const updated = baseRules.map((r, i) => (i === 0 ? { ...r, flatSeverityFactor: factor } : r));
      await api.updateUplifts(selectedProg.programmeId, updated);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['programmes'] });
      qc.invalidateQueries({ queryKey: ['dagEval'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
      qc.invalidateQueries({ queryKey: ['staleness'] });
    },
  });

  const bindMut = useMutation({
    mutationFn: () => api.bindProgramme(selectedProg.programmeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['programmes'] });
      qc.invalidateQueries({ queryKey: ['contracts'] });
      qc.invalidateQueries({ queryKey: ['manifests'] });
    },
  });

  const unadjNodes = dagEvalQ.data?.views?.UNADJUSTED ?? [];
  const adjNodes = dagEvalQ.data?.views?.ADJUSTED ?? [];
  const unadjById = Object.fromEntries(unadjNodes.map((m) => [m.nodeId, m]));
  const adjById = Object.fromEntries(adjNodes.map((m) => [m.nodeId, m]));

  const rfNodes: Node[] = draftGraph.nodes.map((n) => {
    const u = unadjById[n.nodeId];
    const a = adjById[n.nodeId];
    const isSource = n.nodeType === 'LOSS_SOURCE' || n.nodeType === 'INDUSTRY_INDEX_SOURCE';
    const isOutput = n.nodeType === 'PRICING_OUTPUT' || n.nodeType === 'YELT_OUTPUT' || n.nodeType === 'EP_OUTPUT';
    const layerCode =
      LAYER_NODE_TYPES.includes(n.nodeType) && typeof n.params.layerCode === 'string' && n.params.layerCode.trim()
        ? n.params.layerCode.trim()
        : null;
    let pricingBadge: { text: string; linked: boolean } | null = null;
    if (n.nodeType === 'PRICING_OUTPUT') {
      const cid = typeof n.params.contractId === 'string' ? n.params.contractId.trim() : '';
      if (cid) {
        const lid = typeof n.params.layerNodeId === 'string' ? n.params.layerNodeId.trim() : '';
        const targetGraph =
          cid === selectedProg.programmeId ? draftGraph : programmes.find((p) => p.programmeId === cid)?.graph;
        const targetLayer = targetGraph?.nodes.find(
          (ln) => LAYER_NODE_TYPES.includes(ln.nodeType) && (!lid || ln.nodeId === lid),
        );
        const lc = typeof targetLayer?.params.layerCode === 'string' ? targetLayer.params.layerCode.trim() : '';
        pricingBadge = { text: lc ? `CMS · ${lc}` : 'CMS · Linked', linked: true };
      } else {
        pricingBadge = { text: 'Canvas only', linked: false };
      }
    }
    return {
      id: n.nodeId,
      type: isSource ? 'input' : isOutput ? 'output' : 'default',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      position: n.position,
      style: nodeStyle(n.nodeType, n.nodeId === selectedNodeId),
      data: {
        label: (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              {layerCode && <span className="layer-code-pill">{layerCode}</span>}
              {pricingBadge && (
                <span className={`canvas-link-pill ${pricingBadge.linked ? 'linked' : 'unlinked'}`}>
                  {pricingBadge.text}
                </span>
              )}
              <span>{n.label}</span>
            </div>
            {u && a && (
              <div style={{ fontSize: 10, opacity: 0.9, marginTop: 3, fontWeight: 400 }}>
                Unadj: {fmtMoneyM(u.el)} | Adj: {fmtMoneyM(a.el)}
              </div>
            )}
          </div>
        ),
      },
    };
  });

  const rfEdges: Edge[] = draftGraph.edges.map((e) => ({
    id: e.edgeId,
    source: e.source,
    target: e.target,
    label: e.sign < 0 ? `−${e.weight}` : e.weight !== 1 ? `×${e.weight}` : undefined,
    labelStyle: { fill: 'var(--text-strong)', fontSize: 10 },
    labelBgStyle: { fill: 'var(--surface-3)' },
    style: {
      stroke: e.edgeId === selectedEdgeId ? 'var(--accent)' : e.sign < 0 ? 'var(--warning)' : 'var(--text-muted)',
      strokeWidth: e.edgeId === selectedEdgeId ? 3 : 2,
      strokeDasharray: e.sign < 0 ? '6 4' : undefined,
    },
  }));

  const onConnect = (conn: Connection) => {
    if (!conn.source || !conn.target) return;
    const newEdge: GraphEdgeSpec = {
      edgeId: `e-${Date.now()}`,
      source: conn.source,
      target: conn.target,
      sign: 1,
      weight: 1.0,
    };
    setDraftGraph((g) => ({ ...g, edges: [...g.edges, newEdge] }));
    setDirty(true);
  };

  const addTemplateNode = (tpl: {
    label: string;
    nodeType: string;
    layerCode?: string;
    layerNodeId?: string;
    params: Record<string, unknown>;
  }) => {
    const params = structuredClone(tpl.params);
    let nodeId = `n-${Date.now().toString(36)}`;
    if (LAYER_NODE_TYPES.includes(tpl.nodeType)) {
      const existingCodes = draftGraph.nodes
        .filter((n) => LAYER_NODE_TYPES.includes(n.nodeType))
        .map((n) => (typeof n.params.layerCode === 'string' ? n.params.layerCode : ''));
      const candidate = String(tpl.layerCode ?? params.layerCode ?? '').trim();
      const usedLower = new Set(existingCodes.map((c) => c.trim().toLowerCase()).filter(Boolean));
      params.layerCode = candidate && !usedLower.has(candidate.toLowerCase()) ? candidate : nextLayerCode(existingCodes);
      if (tpl.layerNodeId && !draftGraph.nodes.some((n) => n.nodeId === tpl.layerNodeId)) {
        nodeId = tpl.layerNodeId;
      }
    }
    if (tpl.nodeType === 'PRICING_OUTPUT' && !params.contractId) {
      params.unlinked = true;
    }
    const newNode: GraphNodeSpec = {
      nodeId,
      nodeType: tpl.nodeType,
      label: smartLabel(tpl.nodeType, params, tpl.label),
      params,
      // Blank-canvas scatter box. CMS layers do not use this; see applyCmsLayerSuggestion.
      position: { x: 420 + Math.round(Math.random() * 140), y: 80 + Math.round(Math.random() * 180) },
    };
    setDraftGraph((g) => ({ ...g, nodes: [...g.nodes, newNode] }));
    setSelectedNodeId(newNode.nodeId);
    setSelectedEdgeId(null);
    setDirty(true);
  };

  const placeTemplate = (tpl: GraphTemplate) => {
    if (tpl.source === 'CMS' && tpl.pricingOutput && LAYER_NODE_TYPES.includes(tpl.nodeType)) {
      const claimedElsewhere = claimedPricingLinks(
        programmes.filter((p) => p.programmeId !== selectedProg.programmeId).map((p) => p.graph),
      );
      const placed = applyCmsLayerSuggestion(draftGraph, tpl, { claimedElsewhere });
      if (placed.changed) {
        setDraftGraph(placed.graph);
        setDirty(true);
      }
      setSelectedNodeId(placed.selectedNodeId);
      setSelectedEdgeId(null);
      return;
    }
    addTemplateNode(tpl);
  };

  const selectedNode = draftGraph.nodes.find((n) => n.nodeId === selectedNodeId) ?? null;
  const selectedEdge = draftGraph.edges.find((e) => e.edgeId === selectedEdgeId) ?? null;

  const updateSelectedNodeParam = (key: string, val: unknown) => {
    if (!selectedNode) return;
    setDraftGraph((g) => ({
      ...g,
      nodes: g.nodes.map((n) =>
        n.nodeId === selectedNode.nodeId ? withParams(n, { ...n.params, [key]: val }) : n,
      ),
    }));
    setDirty(true);
  };

  const updateSelectedNode = (fn: (n: GraphNodeSpec) => GraphNodeSpec) => {
    if (!selectedNode) return;
    setDraftGraph((g) => ({ ...g, nodes: g.nodes.map((n) => (n.nodeId === selectedNode.nodeId ? fn(n) : n)) }));
    setDirty(true);
  };

  const deleteSelectedNode = () => {
    if (!selectedNode) return;
    setDraftGraph((g) => ({
      ...g,
      nodes: g.nodes.filter((n) => n.nodeId !== selectedNode.nodeId),
      edges: g.edges.filter((e) => e.source !== selectedNode.nodeId && e.target !== selectedNode.nodeId),
    }));
    setSelectedNodeId(null);
    setDirty(true);
  };

  return (
    <>
      <div className="card">
        <div className="card-title">
          <span>
            Calculation Graph <span className="badge">v{selectedProg.graph.version}</span>
            {dirty && <span style={{ color: 'var(--warning)', marginLeft: 6 }}>Unsaved changes</span>}
          </span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select
              value={selectedProg.programmeId}
              onChange={(e) => onSelectProgramme(e.target.value)}
              style={{ maxWidth: '100%' }}
            >
              {programmes.map((p) => (
                <option key={p.programmeId} value={p.programmeId}>
                  {p.cedentName} — {p.programmeName} ({p.renewalPeriod})
                </option>
              ))}
            </select>
            <label style={{ color: 'var(--text-muted)', fontSize: 11 }}>
              Uplift ×
              <input
                type="number"
                step="0.02"
                value={flUplift}
                onChange={(e) => {
                  const raw = e.target.value;
                  setFlUplift(raw === '' || raw === '-' ? raw : Number(raw));
                }}
                style={{ width: 64, marginLeft: 4 }}
              />
            </label>
            <button className="btn btn-ghost" onClick={() => upliftMut.mutate()}>
              Apply Uplift
            </button>
            <button className="btn" onClick={() => saveGraphMut.mutate()} disabled={saveGraphMut.isPending}>
              {saveGraphMut.isPending ? 'Evaluating...' : 'Save & Re-Price DAG'}
            </button>
            <button
              className="btn"
              style={{ background: 'var(--teal-strong)' }}
              onClick={() => bindMut.mutate()}
              disabled={bindMut.isPending}
            >
              {bindMut.isSuccess ? 'Bound Snapshot Locked' : 'Bind Deal Snapshot'}
            </button>
            <a
              className="btn btn-ghost"
              style={{ textDecoration: 'none' }}
              href={apiUrl(`/api/exports/programme-tearsheet/${selectedProg.programmeId}`)}
              target="_blank"
              rel="noreferrer"
            >
              Deal Tear-Sheet
            </a>
          </div>
        </div>

        {(saveGraphMut.error || upliftMut.error || bindMut.error || dagEvalQ.error) && (
          <div className="error-banner">
            {String(
              (saveGraphMut.error as Error | null)?.message ??
                (upliftMut.error as Error | null)?.message ??
                (bindMut.error as Error | null)?.message ??
                (dagEvalQ.error as Error | null)?.message ??
                'Request failed',
            )}
          </div>
        )}

        <div className="studio-layout">
          {/* Left Inspector */}
          <div className="studio-panel panel" style={{ padding: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8, color: 'var(--accent-soft)' }}>
              Node / Edge Properties
            </div>
            {selectedNode ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontSize: 10, color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
                  ID: {selectedNode.nodeId} ({selectedNode.nodeType})
                </div>
                {LAYER_NODE_TYPES.includes(selectedNode.nodeType) && (() => {
                  const rawCode = typeof selectedNode.params.layerCode === 'string' ? selectedNode.params.layerCode : '';
                  const trimmed = rawCode.trim();
                  const duplicate =
                    Boolean(trimmed) &&
                    draftGraph.nodes.some(
                      (n) =>
                        n.nodeId !== selectedNode.nodeId &&
                        LAYER_NODE_TYPES.includes(n.nodeType) &&
                        String(n.params.layerCode ?? '').trim().toLowerCase() === trimmed.toLowerCase(),
                    );
                  return (
                    <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6 }}>
                        <span>Layer ID</span>
                        <span style={{ fontSize: 10, color: duplicate || !trimmed ? 'var(--negative)' : 'var(--text-faint)' }}>
                          {!trimmed ? 'Required' : duplicate ? 'Duplicate in programme' : 'Unique in programme'}
                        </span>
                      </div>
                      <input
                        className="mono"
                        value={rawCode}
                        placeholder="e.g. L1"
                        onChange={(e) => updateSelectedNodeParam('layerCode', e.target.value)}
                        style={{
                          width: '100%',
                          marginTop: 3,
                          borderColor: duplicate || !trimmed ? 'var(--negative)' : undefined,
                        }}
                      />
                    </label>
                  );
                })()}
                {(() => {
                  const terms = nodeTerms(selectedNode.nodeType, selectedNode.params);
                  const auto = isAutoLabel(selectedNode.params);
                  if (terms == null) {
                    return (
                      <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        Label
                        <input
                          value={selectedNode.label}
                          onChange={(e) => updateSelectedNode((n) => ({ ...n, label: e.target.value }))}
                          style={{ width: '100%', marginTop: 3 }}
                        />
                      </label>
                    );
                  }
                  return (
                    <div className="label-editor">
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{auto ? 'Name' : 'Custom label'}</span>
                        <label className="toggle" title="When on, the label updates automatically as terms change">
                          <input
                            type="checkbox"
                            checked={auto}
                            onChange={(e) =>
                              updateSelectedNode((n) => {
                                const params = { ...n.params };
                                if (e.target.checked) {
                                  delete params.autoLabel;
                                  params.labelName = nameFromLabel(n.label);
                                } else {
                                  params.autoLabel = false;
                                }
                                return withParams(n, params);
                              })
                            }
                          />
                          <span>Auto terms</span>
                        </label>
                      </div>
                      {auto ? (
                        <>
                          <input
                            value={labelName(selectedNode.params, selectedNode.label)}
                            placeholder="e.g. 1st Cat XL"
                            onChange={(e) => updateSelectedNode((n) => withParams(n, { ...n.params, labelName: e.target.value }))}
                            style={{ width: '100%', marginTop: 3 }}
                          />
                          <div className="label-terms" title="Generated from the node's terms">
                            <span>Terms</span>
                            {terms}
                          </div>
                        </>
                      ) : (
                        <>
                          <input
                            value={selectedNode.label}
                            onChange={(e) => updateSelectedNode((n) => ({ ...n, label: e.target.value }))}
                            style={{ width: '100%', marginTop: 3 }}
                          />
                          <div className="label-terms muted">Auto terms off — label will not change with parameters.</div>
                        </>
                      )}
                    </div>
                  );
                })()}
                {selectedNode.nodeType === 'YELT_OUTPUT' && (
                  <YeltOutputPanel
                    node={selectedNode}
                    programmeId={selectedProg.programmeId}
                    saved={evaluatesLikeSaved(draftGraph, selectedProg.graph, selectedNode.nodeId, ['view'])}
                    versionKey={`${selectedProg.graph.version}-${selectedProg.upliftSet.version}`}
                    onParams={(params) => updateSelectedNode((n) => withParams(n, params))}
                  />
                )}
                {selectedNode.nodeType === 'PRICING_OUTPUT' && (
                  <PricingOutputPanel
                    node={selectedNode}
                    programmeId={selectedProg.programmeId}
                    draftGraph={draftGraph}
                    saved={evaluatesLikeSaved(draftGraph, selectedProg.graph, selectedNode.nodeId, [])}
                    adj={adjById[selectedNode.nodeId]}
                    unadj={unadjById[selectedNode.nodeId]}
                    onParams={(params) => updateSelectedNode((n) => withParams(n, params))}
                  />
                )}
                {selectedNode.nodeType === 'REGION_PERIL_FILTER' && (() => {
                  const selZones: string[] = Array.isArray(selectedNode.params.zones) ? (selectedNode.params.zones as string[]) : [];
                  const selPerils: string[] = Array.isArray(selectedNode.params.perils) ? (selectedNode.params.perils as string[]) : [];
                  const selRps: string[] = Array.isArray(selectedNode.params.regionPerils)
                    ? (selectedNode.params.regionPerils as string[])
                    : [];

                  const toggle = (key: 'zones' | 'perils' | 'regionPerils', current: string[], item: string) => {
                    const next = current.includes(item) ? current.filter((x) => x !== item) : [...current, item];
                    updateSelectedNodeParam(key, next);
                  };

                  const chipStyle = (active: boolean): React.CSSProperties => ({
                    fontSize: 10,
                    padding: '3px 7px',
                    borderRadius: 4,
                    cursor: 'pointer',
                    border: active ? '1px solid var(--accent)' : '1px solid var(--border-strong)',
                    background: active ? 'var(--accent-bg)' : 'var(--surface)',
                    color: active ? 'var(--accent-soft)' : 'var(--text-muted)',
                    fontWeight: active ? 600 : 400,
                  });

                  return (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
                      <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)', marginBottom: 4 }}>
                          <span>ZONES (OR)</span>
                          {selZones.length > 0 && (
                            <button
                              style={{ background: 'none', border: 'none', color: 'var(--accent)', fontSize: 10, cursor: 'pointer', padding: 0 }}
                              onClick={() => updateSelectedNodeParam('zones', [])}
                            >
                              Clear
                            </button>
                          )}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                          {AVAILABLE_ZONES.map((z) => (
                            <button key={z} type="button" style={chipStyle(selZones.includes(z))} onClick={() => toggle('zones', selZones, z)}>
                              {z}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)', marginBottom: 4 }}>
                          <span>PERILS (AND/OR)</span>
                          {selPerils.length > 0 && (
                            <button
                              style={{ background: 'none', border: 'none', color: 'var(--accent)', fontSize: 10, cursor: 'pointer', padding: 0 }}
                              onClick={() => updateSelectedNodeParam('perils', [])}
                            >
                              Clear
                            </button>
                          )}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                          {AVAILABLE_PERILS.map((p) => (
                            <button key={p.code} type="button" style={chipStyle(selPerils.includes(p.code))} onClick={() => toggle('perils', selPerils, p.code)}>
                              {p.label}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)', marginBottom: 4 }}>
                          <span>SPECIFIC ZONE-PERILS</span>
                          {selRps.length > 0 && (
                            <button
                              style={{ background: 'none', border: 'none', color: 'var(--accent)', fontSize: 10, cursor: 'pointer', padding: 0 }}
                              onClick={() => updateSelectedNodeParam('regionPerils', [])}
                            >
                              Clear
                            </button>
                          )}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                          {AVAILABLE_REGION_PERILS.map((rp) => (
                            <button key={rp} type="button" style={chipStyle(selRps.includes(rp))} onClick={() => toggle('regionPerils', selRps, rp)}>
                              {rp}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div style={{ fontSize: 10, color: 'var(--teal)', background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 4, padding: '5px 7px' }}>
                        {selZones.length === 0 && selPerils.length === 0 && selRps.length === 0
                          ? 'Filter: All zones & perils (unfiltered)'
                          : `Active: ${[
                              selZones.length ? `Zones[${selZones.join(', ')}]` : '',
                              selPerils.length ? `Perils[${selPerils.join(', ')}]` : '',
                              selRps.length ? `Exact[${selRps.join(', ')}]` : '',
                            ]
                              .filter(Boolean)
                              .join(' · ')}`}
                      </div>
                    </div>
                  );
                })()}

                {Object.entries(selectedNode.params)
                  .filter(
                    ([k, v]) =>
                      !LABEL_META_KEYS.includes(k) &&
                      !LAYER_META_KEYS.includes(k) &&
                      !OUTPUT_LINK_KEYS.includes(k) &&
                      (typeof v === 'number' || typeof v === 'string'),
                  )
                  .map(([k, v]) => {
                    const meta = NODE_PARAM_META[k];
                    const isRatioPct = Boolean(meta?.isRatioPct);
                    const isNumeric =
                      Boolean(meta) ||
                      typeof v === 'number' ||
                      v === '' ||
                      v === '-' ||
                      (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v)) && /^-?\d*\.?\d*$/.test(v.trim()));

                    // Display ratio-based percentages (placedShare, ripRate, cessionShare) in 0..100%
                    const displayValue =
                      isRatioPct && typeof v === 'number'
                        ? String(Number((v * 100).toFixed(4)))
                        : String(v);

                    const numericVal =
                      typeof v === 'number'
                        ? v
                        : typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))
                          ? Number(v)
                          : null;

                    const formattedHint =
                      numericVal == null
                        ? null
                        : meta?.unit === '$M'
                          ? fmtMoneyM(numericVal, 2)
                          : meta?.unit === '$B'
                            ? fmtMoneyB(numericVal, 2)
                            : meta?.unit === '%'
                              ? fmtRatioPct(numericVal, 1)
                              : meta?.unit === '×'
                                ? `${fmtNum(numericVal, 2)}×`
                                : fmtNum(numericVal, numericVal % 1 === 0 ? 0 : 2);

                    return (
                      <label key={k} style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6 }}>
                          <span>
                            {meta?.label ?? k}
                            {meta?.unit ? ` (${meta.unit})` : ''}
                          </span>
                          {formattedHint && (
                            <span style={{ fontSize: 10, color: 'var(--accent)', fontVariantNumeric: 'tabular-nums' }}>
                              {formattedHint}
                            </span>
                          )}
                        </div>
                        <input
                          type={isNumeric ? 'number' : 'text'}
                          step="any"
                          placeholder={meta?.placeholder ?? (isNumeric ? 'Enter value' : '')}
                          value={displayValue}
                          onChange={(e) => {
                            if (!isNumeric) {
                              updateSelectedNodeParam(k, e.target.value);
                              return;
                            }
                            const raw = e.target.value;
                            if (raw === '' || raw === '-') {
                              updateSelectedNodeParam(k, raw);
                              return;
                            }
                            const num = Number(raw);
                            updateSelectedNodeParam(k, isRatioPct ? num / 100 : num);
                          }}
                          style={{ width: '100%', marginTop: 3 }}
                        />
                      </label>
                    );
                  })}
                <button
                  className="btn"
                  style={{ background: '#7f1d1d', marginTop: 6 }}
                  onClick={deleteSelectedNode}
                >
                  Delete Node
                </button>
              </div>
            ) : selectedEdge ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  Edge: {selectedEdge.source} → {selectedEdge.target}
                </div>
                <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  Flow Polarity
                  <select
                    value={selectedEdge.sign}
                    onChange={(e) => {
                      const sign = Number(e.target.value);
                      setDraftGraph((g) => ({
                        ...g,
                        edges: g.edges.map((ed) => (ed.edgeId === selectedEdge.edgeId ? { ...ed, sign } : ed)),
                      }));
                      setDirty(true);
                    }}
                    style={{ width: '100%', marginTop: 3 }}
                  >
                    <option value={1}>+1 Solid (Additive)</option>
                    <option value={-1}>−1 Dashed (Subtractive / Inuring)</option>
                  </select>
                </label>
                <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  Edge Weight / Load
                  <input
                    type="number"
                    step="0.05"
                    value={selectedEdge.weight}
                    onChange={(e) => {
                      const weight = Number(e.target.value);
                      setDraftGraph((g) => ({
                        ...g,
                        edges: g.edges.map((ed) => (ed.edgeId === selectedEdge.edgeId ? { ...ed, weight } : ed)),
                      }));
                      setDirty(true);
                    }}
                    style={{ width: '100%', marginTop: 3 }}
                  />
                </label>
                <button
                  className="btn"
                  style={{ background: '#7f1d1d' }}
                  onClick={() => {
                    setDraftGraph((g) => ({
                      ...g,
                      edges: g.edges.filter((ed) => ed.edgeId !== selectedEdge.edgeId),
                    }));
                    setSelectedEdgeId(null);
                    setDirty(true);
                  }}
                >
                  Delete Edge
                </button>
              </div>
            ) : (
              <div style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                Click any node or edge on the canvas to edit parameters, toggle solid (+) vs dashed (−) inuring edges, or connect ports.
              </div>
            )}
          </div>

          {/* Center React Flow Canvas */}
          <div className="studio-canvas">
            <ReactFlow
              key={selectedProg.programmeId}
              nodes={rfNodes}
              edges={rfEdges}
              onConnect={onConnect}
              onNodeClick={(_, node) => {
                setSelectedNodeId(node.id);
                setSelectedEdgeId(null);
              }}
              onEdgeClick={(_, edge) => {
                setSelectedEdgeId(edge.id);
                setSelectedNodeId(null);
              }}
              onNodeDragStop={(_, node) => {
                setDraftGraph((g) => ({
                  ...g,
                  nodes: g.nodes.map((n) => (n.nodeId === node.id ? { ...n, position: node.position } : n)),
                }));
                setDirty(true);
              }}
              fitView
              fitViewOptions={{ padding: 0.12 }}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="#2a241e" gap={22} />
              <Controls />
            </ReactFlow>
          </div>

          {/* Right Rail: CMS Suggestions & Blank-Canvas Node Palette */}
          <div className="panel" style={{ padding: 10, overflowY: 'auto', maxHeight: 440 }}>
            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8, color: 'var(--teal)' }}>
              Suggested from CMS
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
              {(templatesQ.data ?? [])
                .filter((tpl) => tpl.source === 'CMS')
                .map((tpl) => {
                  const onCanvas = tpl.pricingOutput
                    ? tpl.placement === 'NEW_COPY'
                      ? draftGraph.nodes.some((n) => n.params[SUGGESTED_FROM_PARAM] === tpl.templateId)
                      : Boolean(tpl.layerNodeId && draftGraph.nodes.some((n) => n.nodeId === tpl.layerNodeId))
                    : false;
                  return (
                    <button key={tpl.templateId} className="palette-item suggested" onClick={() => placeTemplate(tpl)}>
                      <span className="palette-head">
                        <span className="palette-cat">{tpl.category}</span>
                        {onCanvas && <span className="canvas-tag">On canvas</span>}
                      </span>
                      <span className="palette-label">{tpl.label}</span>
                      {tpl.pricingOutput && <span className="palette-cat">With pricing output</span>}
                    </button>
                  );
                })}
            </div>

            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8, color: 'var(--text-muted)' }}>
              Blank Canvas Nodes
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(templatesQ.data ?? [])
                .filter((tpl) => tpl.source === 'BLANK')
                .map((tpl) => (
                  <button key={tpl.templateId} className="palette-item" onClick={() => placeTemplate(tpl)}>
                    <span className="palette-cat">{tpl.category}</span>
                    <span className="palette-label">{tpl.label}</span>
                  </button>
                ))}
            </div>
          </div>
        </div>

        <div style={{ marginTop: 10 }}>
          <BreadcrumbBar manifest={dagEvalQ.data?.manifest ?? null} />
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          <span>Review Statistics · Unadjusted vs Adjusted ({selectedProg.currency} M)</span>
          <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
            Rust Kernel: {dagEvalQ.data?.computeMs ?? '—'} ms · Cache: {dagEvalQ.data?.cacheHits ?? 0} hits /{' '}
            {dagEvalQ.data?.cacheMisses ?? 0} misses
          </span>
        </div>
        <div className="table-wrap"><table>
          <thead>
            <tr>
              <th>Node</th>
              <th>Type</th>
              <th className="num">Unadj EL</th>
              <th className="num">Adj EL</th>
              <th className="num">Uplift Δ</th>
              <th className="num">Adj SD</th>
              <th className="num">Adj Exp RIP</th>
              <th className="num">Adj 1-in-250 TVaR</th>
              <th className="num">Adj ELR</th>
            </tr>
          </thead>
          <tbody>
            {unadjNodes.map((u, idx) => {
              const a = adjNodes[idx];
              const deltaPct = u.el > 0 ? ((a.el - u.el) / u.el) * 100 : 0;
              const tvar250 = a.epPoints.find((p) => Math.abs(p.returnPeriod - 250) < 0.1)?.tvar ?? 0;
              const graphNode = draftGraph.nodes.find((n) => n.nodeId === u.nodeId);
              const layerCode =
                graphNode && LAYER_NODE_TYPES.includes(graphNode.nodeType) && typeof graphNode.params.layerCode === 'string'
                  ? graphNode.params.layerCode.trim()
                  : '';
              return (
                <tr key={u.nodeId} onClick={() => setSelectedNodeId(u.nodeId)} style={{ cursor: 'pointer' }}>
                  <td style={{ fontWeight: u.nodeId === selectedNodeId ? 700 : 400 }}>
                    {layerCode && <span className="layer-code-pill" style={{ marginRight: 6 }}>{layerCode}</span>}
                    {u.nodeLabel}
                  </td>
                  <td style={{ color: 'var(--text-muted)' }}>{u.nodeType}</td>
                  <td className="num">{fmtMoneyM(u.el, 3)}</td>
                  <td className="num" style={{ fontWeight: 600 }}>{fmtMoneyM(a.el, 3)}</td>
                  <td className="num" style={{ color: deltaPct > 0 ? 'var(--warning)' : 'var(--text-muted)' }}>
                    {fmtPct(deltaPct, 1, { signed: true })}
                  </td>
                  <td className="num">{fmtMoneyM(a.sd, 3)}</td>
                  <td className="num">{fmtMoneyM(a.expectedRip, 3)}</td>
                  <td className="num">{fmtMoneyM(tvar250, 2)}</td>
                  <td className="num">{fmtRatioPct(a.elr, 1)}</td>
                </tr>
              );
            })}
          </tbody>
        </table></div>
      </div>
    </>
  );
}
