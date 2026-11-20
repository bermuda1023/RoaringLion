import {
  Background,
  Controls,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { BreadcrumbBar } from '../components/BreadcrumbBar';
import type { GraphEdgeSpec, GraphNodeSpec, GraphSpec, ProgrammeDeal } from '../types/contracts';

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

  const templatesQ = useQuery({ queryKey: ['graphTemplates'], queryFn: api.listGraphTemplates });

  const dagEvalQ = useQuery({
    queryKey: ['dagEval', selectedProg.programmeId, selectedProg.graph.version, selectedProg.upliftSet.version],
    queryFn: () => api.evaluateProgramme(selectedProg.programmeId),
  });

  const saveGraphMut = useMutation({
    mutationFn: () => {
      // Params edited through the inspector may hold numeric strings; restore numbers.
      const clean: GraphSpec = {
        ...draftGraph,
        nodes: draftGraph.nodes.map((n) => ({
          ...n,
          params: Object.fromEntries(
            Object.entries(n.params).map(([k, v]) => [
              k,
              typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v)) ? Number(v) : v,
            ]),
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
    return {
      id: n.nodeId,
      position: n.position,
      style: nodeStyle(n.nodeType, n.nodeId === selectedNodeId),
      data: {
        label: (
          <div>
            <div>{n.label}</div>
            {u && a && (
              <div style={{ fontSize: 10, opacity: 0.9, marginTop: 3, fontWeight: 400 }}>
                Unadj: ${u.el.toFixed(2)}m | Adj: ${a.el.toFixed(2)}m
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
            <select value={selectedProg.programmeId} onChange={(e) => onSelectProgramme(e.target.value)}>
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

        <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr 220px', gap: 12 }}>
          {/* Left Inspector */}
          <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 10 }}>
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
                {Object.entries(selectedNode.params)
                  .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
                  .map(([k, v]) => {
                    // A param that started numeric (or is a numeric intermediate
                    // like '' / '-50') stays a number input even while its raw
                    // editing value is held as a string.
                    const isNumeric =
                      typeof v === 'number' || v === '' || v === '-' || (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v)) && /^-?\d*\.?\d*$/.test(v.trim()));
                    return (
                    <label key={k} style={{ fontSize: 11, color: '#94a3b8' }}>
                      {k}
                      <input
                        type={isNumeric ? 'number' : 'text'}
                        step="any"
                        value={String(v)}
                        onChange={(e) => {
                          if (!isNumeric) {
                            updateSelectedNodeParam(k, e.target.value);
                            return;
                          }
                          // Keep intermediate keystrokes ('', '-') as raw text so
                          // clearing a field or typing a negative stays editable;
                          // the API validates the final value on save.
                          const raw = e.target.value;
                          updateSelectedNodeParam(k, raw === '' || raw === '-' ? raw : Number(raw));
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
          <div style={{ height: 400, background: '#0a0f18', borderRadius: 6, border: '1px solid #26324a' }}>
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

          {/* Right Rail: Node Palette & Reusable Templates */}
          <div style={{ background: '#0d1422', border: '1px solid #26324a', borderRadius: 6, padding: 10, overflowY: 'auto', maxHeight: 400 }}>
            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8, color: '#5eead4' }}>
              Node & Term Library
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {templatesQ.data?.map((tpl) => (
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
                  <td className="num">${u.el.toFixed(3)}m</td>
                  <td className="num" style={{ fontWeight: 600 }}>${a.el.toFixed(3)}m</td>
                  <td className="num" style={{ color: deltaPct > 0 ? '#f59e0b' : '#94a3b8' }}>
                    {deltaPct >= 0 ? '+' : ''}
                    {deltaPct.toFixed(1)}%
                  </td>
                  <td className="num">${a.sd.toFixed(3)}m</td>
                  <td className="num">${a.expectedRip.toFixed(3)}m</td>
                  <td className="num">${tvar250.toFixed(2)}m</td>
                  <td className="num">{a.elr != null ? `${(a.elr * 100).toFixed(1)}%` : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
