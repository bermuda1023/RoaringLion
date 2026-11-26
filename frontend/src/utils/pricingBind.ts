// Pricing Output connections. One pricing output binds one layer.
// Mirrors backend/app/services/pricing_bind.py — keep the two in sync.
//
// A Pricing Output accepts a single incoming edge. Disconnect that edge the
// same way as any other edge, then connect a different layer. When the output
// is linked to this programme, the reconnect moves the bind onto that layer.

import type { GraphEdgeSpec, GraphNodeSpec, GraphSpec } from '../types/contracts';
import { LAYER_NODE_TYPES, smartLabel } from './nodeLabels';

export const PRICING_ALREADY_LINKED =
  'This Pricing Output is already linked to a layer. Disconnect it before connecting another. One pricing output binds one layer.';

export const LAYER_ALREADY_BOUND =
  'That layer already has a Pricing Output. Disconnect it there first — one pricing output binds one layer.';

export type ConnectResult = { graph: GraphSpec } | { error: string };

function claim(params: Record<string, unknown>): string | null {
  const cid = typeof params.contractId === 'string' ? params.contractId.trim() : '';
  const lid = typeof params.layerNodeId === 'string' ? params.layerNodeId.trim() : '';
  return cid && lid ? `${cid}::${lid}` : null;
}

function layerSummary(layer: GraphNodeSpec): string {
  const code = typeof layer.params.layerCode === 'string' ? layer.params.layerCode.trim() : '';
  const name =
    typeof layer.params.labelName === 'string' && layer.params.labelName.trim()
      ? layer.params.labelName.trim()
      : layer.label;
  return [code, name].filter(Boolean).join(' · ');
}

function movedLinkedLabel(existing: string, layer: GraphNodeSpec): string {
  const head = existing.split(' · ')[0]?.trim() ?? '';
  const summary = layerSummary(layer);
  return [head, summary].filter(Boolean).join(' · ');
}

export function removeEdge(graph: GraphSpec, edgeId: string): GraphSpec {
  if (!graph.edges.some((e) => e.edgeId === edgeId)) return graph;
  return { ...graph, edges: graph.edges.filter((e) => e.edgeId !== edgeId) };
}

export function connectNodes(
  graph: GraphSpec,
  programmeId: string,
  source: string,
  target: string,
  edgeId: string,
  claimedElsewhere?: Set<string>,
): ConnectResult {
  if (!source || !target || source === target) return { graph };
  const src = graph.nodes.find((n) => n.nodeId === source);
  const dst = graph.nodes.find((n) => n.nodeId === target);
  if (!src || !dst) return { error: 'That connection does not match a node on this graph' };
  if (graph.edges.some((e) => e.source === source && e.target === target)) return { graph };
  if (dst.nodeType === 'PRICING_OUTPUT' && graph.edges.some((e) => e.target === target)) {
    return { error: PRICING_ALREADY_LINKED };
  }

  let nodes = graph.nodes;
  if (dst.nodeType === 'PRICING_OUTPUT' && LAYER_NODE_TYPES.includes(src.nodeType)) {
    const cid = typeof dst.params.contractId === 'string' ? dst.params.contractId.trim() : '';
    if (cid && cid === programmeId) {
      const key = `${cid}::${src.nodeId}`;
      const taken =
        (claimedElsewhere?.has(key) ?? false) ||
        graph.nodes.some(
          (n) => n.nodeId !== dst.nodeId && n.nodeType === 'PRICING_OUTPUT' && claim(n.params) === key,
        );
      if (taken) return { error: LAYER_ALREADY_BOUND };
      nodes = graph.nodes.map((n) => {
        if (n.nodeId !== dst.nodeId) return n;
        const params: Record<string, unknown> = { ...n.params, layerNodeId: src.nodeId };
        delete params.unlinked;
        const existing = typeof params.linkedLabel === 'string' ? params.linkedLabel : '';
        params.linkedLabel = movedLinkedLabel(existing, src);
        return { ...n, params, label: smartLabel('PRICING_OUTPUT', params, n.label) };
      });
    }
  }

  const edge: GraphEdgeSpec = { edgeId, source, target, sign: 1, weight: 1 };
  return { graph: { ...graph, nodes, edges: [...graph.edges, edge] } };
}
