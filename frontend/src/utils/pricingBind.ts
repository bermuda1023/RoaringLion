// Pricing Output connections. One pricing output binds one layer.
// Mirrors backend/app/services/pricing_bind.py — keep the two in sync.
//
// A Pricing Output accepts a single incoming edge. Disconnect that edge the
// same way as any other edge, then connect a different graph layer. contractId
// and layerNodeId stay on the CMS layer. A CMS identity already bound elsewhere
// is refused.

import type { GraphEdgeSpec, GraphSpec } from '../types/contracts';

export const PRICING_ALREADY_LINKED =
  'This Pricing Output is already linked to a layer. Disconnect it before connecting another. One pricing output binds one layer.';

export const PRICING_ALREADY_BOUND =
  'This Pricing Output is already bound and cannot be used for another layer.';

export type ConnectResult = { graph: GraphSpec } | { error: string };

function claim(params: Record<string, unknown>): string | null {
  const cid = typeof params.contractId === 'string' ? params.contractId.trim() : '';
  const lid = typeof params.layerNodeId === 'string' ? params.layerNodeId.trim() : '';
  return cid && lid ? `${cid}::${lid}` : null;
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
  void programmeId;
  if (!source || !target || source === target) return { graph };
  const src = graph.nodes.find((n) => n.nodeId === source);
  const dst = graph.nodes.find((n) => n.nodeId === target);
  if (!src || !dst) return { error: 'That connection does not match a node on this graph' };
  if (graph.edges.some((e) => e.source === source && e.target === target)) return { graph };
  if (dst.nodeType === 'PRICING_OUTPUT' && graph.edges.some((e) => e.target === target)) {
    return { error: PRICING_ALREADY_LINKED };
  }

  if (dst.nodeType === 'PRICING_OUTPUT') {
    const key = claim(dst.params);
    if (key && claimedElsewhere?.has(key)) return { error: PRICING_ALREADY_BOUND };
  }

  const edge: GraphEdgeSpec = { edgeId, source, target, sign: 1, weight: 1 };
  return { graph: { ...graph, nodes: graph.nodes, edges: [...graph.edges, edge] } };
}
