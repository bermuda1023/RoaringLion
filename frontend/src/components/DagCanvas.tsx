import { Background, Controls, Position, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { GraphSpec, NodeViewMetrics } from '../types/contracts';
import { nodeAccent } from '../utils/nodeAccent';

function nodeStyle(nodeType: string): React.CSSProperties {
  const accent = nodeAccent(nodeType);
  return {
    borderRadius: 10,
    padding: '8px 12px',
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--text)',
    background: 'var(--surface-2)',
    borderTop: '1px solid var(--border-strong)',
    borderRight: '1px solid var(--border-strong)',
    borderBottom: '1px solid var(--border-strong)',
    borderLeft: `3px solid ${accent}`,
    boxShadow: '0 8px 18px rgba(0, 0, 0, 0.32)',
    minWidth: 195,
    textAlign: 'left',
  };
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
      stroke: e.sign < 0 ? 'var(--warning)' : 'var(--text-muted)',
      strokeWidth: 2,
      strokeDasharray: e.sign < 0 ? '6 4' : undefined,
    },
  }));

  return (
    <div style={{ height: 360, background: 'var(--surface-sunken)', borderRadius: 6, border: '1px solid var(--border-strong)' }}>
      <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
        <Background color="#232b3a" gap={22} />
        <Controls />
      </ReactFlow>
    </div>
  );
}
