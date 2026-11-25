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
import type { GraphEdgeSpec, GraphNodeSpec, GraphSpec, ProgrammeDeal } from '../types/contracts';
import { fmtMoneyB, fmtMoneyM, fmtNum, fmtPct, fmtRatioPct, NODE_PARAM_META } from '../utils/format';

const AVAILABLE_ZONES = ['US-FL', 'US-TX', 'US-SE', 'US-CA', 'JP-KANTO', 'EU-DE'];
const AVAILABLE_PERILS = [
  { code: 'WS', label: 'WS (Windstorm)' },
  { code: 'EQ', label: 'EQ (Earthquake)' },
];
const AVAILABLE_REGION_PERILS = ['US-FL-WS', 'US-TX-WS', 'US-SE-WS', 'US-CA-EQ', 'JP-KANTO-EQ', 'EU-DE-WS'];

function nodeStyle(nodeType: string, selected: boolean): React.CSSProperties {
  const base: React.CSSProperties = {
    borderRadius: 6,
    padding: '8px 12px',
    fontSize: 11,
    fontWeight: 600,
    color: '#f8fafc',
    border: selected ? '2px solid #38bdf8' : '1px solid rgba(255,255,255,0.2)',
    minWidth: 185,
    textAlign: 'center',
  };
  if (nodeType === 'LOSS_SOURCE' || nodeType === 'INDUSTRY_INDEX_SOURCE') return { ...base, background: '#1d4ed8' };
  if (nodeType.includes('FILTER') || nodeType === 'COMBINATION' || nodeType === 'UPLIFT')
    return { ...base, background: '#475569' };
  if (
    nodeType === 'OCC_XL' ||
    nodeType === 'AGG_XL' ||
    nodeType === 'QUOTA_SHARE' ||
    nodeType === 'AGG_QUOTA_SHARE' ||
    nodeType === 'SURPLUS_SHARE' ||
    nodeType === 'CASCADING_TOWER' ||
    nodeType === 'ILW_OPTION'
  ) {
    return { ...base, background: '#9f2b4b' };
  }
  return { ...base, background: '#0f766e' };
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
    setDraftGraph(selectedProg.graph);
    setFlUplift(selectedProg.upliftSet.rules[0]?.flatSeverityFactor ?? 1.1);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setDirty(false);
  }, [selectedProg.programmeId, selectedProg.graph.version]);

  const templatesQ = useQuery({
    queryKey: ['graphTemplates', selectedProg.programmeId],
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
            <div>{n.label}</div>
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
    labelStyle: { fill: '#f8fafc', fontSize: 10 },
    labelBgStyle: { fill: '#1e293b' },
    style: {
      stroke: e.edgeId === selectedEdgeId ? '#38bdf8' : e.sign < 0 ? '#f59e0b' : '#94a3b8',
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

  const addTemplateNode = (tpl: { label: string; nodeType: string; params: Record<string, unknown> }) => {
    const newNode: GraphNodeSpec = {
      nodeId: `n-${Date.now().toString(36)}`,
      nodeType: tpl.nodeType,
      label: tpl.label,
      params: structuredClone(tpl.params),
      position: { x: 420 + Math.round(Math.random() * 140), y: 80 + Math.round(Math.random() * 180) },
    };
    setDraftGraph((g) => ({ ...g, nodes: [...g.nodes, newNode] }));
    setSelectedNodeId(newNode.nodeId);
    setSelectedEdgeId(null);
    setDirty(true);
  };

  const selectedNode = draftGraph.nodes.find((n) => n.nodeId === selectedNodeId) ?? null;
  const selectedEdge = draftGraph.edges.find((e) => e.edgeId === selectedEdgeId) ?? null;

  const updateSelectedNodeParam = (key: string, val: unknown) => {
    if (!selectedNode) return;
    setDraftGraph((g) => ({
      ...g,
      nodes: g.nodes.map((n) =>
        n.nodeId === selectedNode.nodeId ? { ...n, params: { ...n.params, [key]: val } } : n,
      ),
    }));
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
            Interactive DAG Structuring Studio — v{selectedProg.graph.version}{' '}
            {dirty && <span style={{ color: '#f59e0b', marginLeft: 6 }}>(Unsaved edits)</span>}
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
            <label style={{ color: '#94a3b8', fontSize: 11 }}>
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
              style={{ background: '#0f766e' }}
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
          <div className="studio-panel" style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8, color: '#93c5fd' }}>
              Node / Edge Properties
            </div>
            {selectedNode ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontSize: 10, color: '#64748b', fontFamily: 'monospace' }}>
                  ID: {selectedNode.nodeId} ({selectedNode.nodeType})
                </div>
                <label style={{ fontSize: 11, color: '#94a3b8' }}>
                  Label
                  <input
                    value={selectedNode.label}
                    onChange={(e) => {
                      setDraftGraph((g) => ({
                        ...g,
                        nodes: g.nodes.map((n) =>
                          n.nodeId === selectedNode.nodeId ? { ...n, label: e.target.value } : n,
                        ),
                      }));
                      setDirty(true);
                    }}
                    style={{ width: '100%', marginTop: 3 }}
                  />
                </label>
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
                    border: active ? '1px solid #38bdf8' : '1px solid #334155',
                    background: active ? 'rgba(56, 189, 248, 0.22)' : '#131a29',
                    color: active ? '#e0f2fe' : '#94a3b8',
                    fontWeight: active ? 600 : 400,
                  });

                  return (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
                      <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#94a3b8', marginBottom: 4 }}>
                          <span>ZONES (OR)</span>
                          {selZones.length > 0 && (
                            <button
                              style={{ background: 'none', border: 'none', color: '#38bdf8', fontSize: 10, cursor: 'pointer', padding: 0 }}
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
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#94a3b8', marginBottom: 4 }}>
                          <span>PERILS (AND/OR)</span>
                          {selPerils.length > 0 && (
                            <button
                              style={{ background: 'none', border: 'none', color: '#38bdf8', fontSize: 10, cursor: 'pointer', padding: 0 }}
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
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#94a3b8', marginBottom: 4 }}>
                          <span>SPECIFIC ZONE-PERILS</span>
                          {selRps.length > 0 && (
                            <button
                              style={{ background: 'none', border: 'none', color: '#38bdf8', fontSize: 10, cursor: 'pointer', padding: 0 }}
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

                      <div style={{ fontSize: 10, color: '#5eead4', background: '#131a29', border: '1px solid #26324a', borderRadius: 4, padding: '5px 7px' }}>
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
                  .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
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
                      <label key={k} style={{ fontSize: 11, color: '#94a3b8' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6 }}>
                          <span>
                            {meta?.label ?? k}
                            {meta?.unit ? ` (${meta.unit})` : ''}
                          </span>
                          {formattedHint && (
                            <span style={{ fontSize: 10, color: '#38bdf8', fontVariantNumeric: 'tabular-nums' }}>
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
                <div style={{ fontSize: 11, color: '#94a3b8' }}>
                  Edge: {selectedEdge.source} → {selectedEdge.target}
                </div>
                <label style={{ fontSize: 11, color: '#94a3b8' }}>
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
                <label style={{ fontSize: 11, color: '#94a3b8' }}>
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
              <div style={{ fontSize: 11, color: '#64748b' }}>
                Click any node or edge on the canvas to edit parameters, toggle solid (+) vs dashed (−) inuring edges, or connect ports.
              </div>
            )}
          </div>

          {/* Center React Flow Canvas */}
          <div className="studio-canvas">
            <ReactFlow
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
              proOptions={{ hideAttribution: true }}
            >
              <Background color="#1e293b" gap={18} />
              <Controls />
            </ReactFlow>
          </div>

          {/* Right Rail: CMS Suggestions & Blank-Canvas Node Palette */}
          <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 10, overflowY: 'auto', maxHeight: 400 }}>
            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8, color: '#5eead4' }}>
              Suggested from CMS
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
              {(templatesQ.data ?? [])
                .filter((tpl) => tpl.source === 'CMS')
                .map((tpl) => (
                  <button
                    key={tpl.templateId}
                    className="btn btn-ghost"
                    style={{
                      textAlign: 'left',
                      fontSize: 11,
                      padding: '6px 8px',
                      borderColor: 'rgba(56, 189, 248, 0.35)',
                      background: 'rgba(56, 189, 248, 0.08)',
                    }}
                    onClick={() => addTemplateNode(tpl)}
                  >
                    <div style={{ color: '#38bdf8', fontSize: 9, textTransform: 'uppercase' }}>{tpl.category}</div>
                    <div>+ {tpl.label}</div>
                  </button>
                ))}
            </div>

            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8, color: '#94a3b8' }}>
              Blank Canvas Nodes
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(templatesQ.data ?? [])
                .filter((tpl) => tpl.source !== 'CMS')
                .map((tpl) => (
                  <button
                    key={tpl.templateId}
                    className="btn btn-ghost"
                    style={{ textAlign: 'left', fontSize: 11, padding: '6px 8px' }}
                    onClick={() => addTemplateNode(tpl)}
                  >
                    <div style={{ color: '#94a3b8', fontSize: 9, textTransform: 'uppercase' }}>{tpl.category}</div>
                    <div>+ {tpl.label}</div>
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
          <span>Review Stats — Unadjusted vs Adjusted ({selectedProg.currency} $M)</span>
          <span style={{ color: '#94a3b8', fontSize: 11 }}>
            Rust Kernel: {dagEvalQ.data?.computeMs ?? '—'} ms · Cache: {dagEvalQ.data?.cacheHits ?? 0} hits /{' '}
            {dagEvalQ.data?.cacheMisses ?? 0} misses
          </span>
        </div>
        <table>
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
              return (
                <tr key={u.nodeId} onClick={() => setSelectedNodeId(u.nodeId)} style={{ cursor: 'pointer' }}>
                  <td style={{ fontWeight: u.nodeId === selectedNodeId ? 700 : 400 }}>{u.nodeLabel}</td>
                  <td style={{ color: '#94a3b8' }}>{u.nodeType}</td>
                  <td className="num">{fmtMoneyM(u.el, 3)}</td>
                  <td className="num" style={{ fontWeight: 600 }}>{fmtMoneyM(a.el, 3)}</td>
                  <td className="num" style={{ color: deltaPct > 0 ? '#f59e0b' : '#94a3b8' }}>
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
        </table>
      </div>
    </>
  );
}
