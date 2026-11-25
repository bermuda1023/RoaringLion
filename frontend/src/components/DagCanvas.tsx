import { Background, Controls, Position, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { GraphSpec, NodeViewMetrics } from '../types/contracts';

function nodeStyle(nodeType: string): React.CSSProperties {
  const base: React.CSSProperties = {
    borderRadius: 6,
    padding: '8px 12px',
    fontSize: 12,
    fontWeight: 600,
    color: '#f8fafc',
    border: '1px solid rgba(255,255,255,0.2)',
    minWidth: 195,
    textAlign: 'center',
  };
  if (nodeType === 'LOSS_SOURCE') return { ...base, background: '#1d4ed8' };
  if (nodeType === 'REGION_PERIL_FILTER' || nodeType === 'COMBINATION')
    return { ...base, background: '#475569' };
  if (nodeType === 'OCC_XL' || nodeType === 'AGG_XL') return { ...base, background: '#9f2b4b' };
  return { ...base, background: '#0f766e' };
}

export function DagCanvas({
  graph,
  unadjMetrics,
  adjMetrics,
}: {
  graph: GraphSpec;
  unadjMetrics: NodeViewMetrics[];
  adjMetrics: NodeViewMetrics[];
}) {
  const unadjById = Object.fromEntries(unadjMetrics.map((m) => [m.nodeId, m]));
  const adjById = Object.fromEntries(adjMetrics.map((m) => [m.nodeId, m]));

  const nodes: Node[] = graph.nodes.map((n) => {
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
      style: nodeStyle(n.nodeType),
      data: {
        label: (
          <div>
            <div>{n.label}</div>
            {u && a && (
              <div style={{ fontSize: 10, opacity: 0.9, marginTop: 4, fontWeight: 400 }}>
                Unadj EL: ${u.el.toFixed(2)}m | Adj EL: ${a.el.toFixed(2)}m
              </div>
            )}
          </div>
        ),
      },
    };
  });

  const edges: Edge[] = graph.edges.map((e) => ({
    id: e.edgeId,
    source: e.source,
    target: e.target,
    animated: false,
    style: {
      stroke: e.sign < 0 ? '#f59e0b' : '#94a3b8',
      strokeWidth: 2,
      strokeDasharray: e.sign < 0 ? '6 4' : undefined,
    },
  }));

  return (
    <div style={{ height: 360, background: '#0a0f18', borderRadius: 6, border: '1px solid #26324a' }}>
      <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
        <Background color="#1e293b" gap={18} />
        <Controls />
      </ReactFlow>
    </div>
  );
}
