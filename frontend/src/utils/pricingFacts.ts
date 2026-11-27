// Read-only pricing facts for the structuring graph.
// Premium and ROL are entered in the CMS. A graph layer shows the premium that
// arrives through the pricing output attached to it (or, if nothing is attached,
// the pricing output whose locked CMS layer is this node).

import type { GraphNodeSpec, GraphSpec } from '../types/contracts';

export const CMS_LAYER_PRICE_KEYS = new Set(['depositPremium', 'technicalRol', 'rol', 'authorizedLine', 'rarcPct']);

export interface LayerPremiumFact {
  pricingNodeId: string;
  cmsLayerId: string;
  amount: number | null;
}

function pricingTargets(graph: GraphSpec): Map<string, GraphNodeSpec> {
  return new Map(graph.nodes.filter((node) => node.nodeType === 'PRICING_OUTPUT').map((node) => [node.nodeId, node]));
}

function amountOf(node: GraphNodeSpec | undefined): number | null {
  const raw = node?.params.depositPremium;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string' && raw.trim() !== '' && !Number.isNaN(Number(raw))) return Number(raw);
  return null;
}

export function layerPremiumViaPricing(graph: GraphSpec, layerId: string): LayerPremiumFact | null {
  const outputs = pricingTargets(graph);
  const byEdge = graph.edges.find((edge) => edge.source === layerId && outputs.has(edge.target));
  const attached = byEdge ? outputs.get(byEdge.target) : undefined;
  const named = graph.nodes.find(
    (node) => node.nodeType === 'PRICING_OUTPUT' && node.params.layerNodeId === layerId,
  );
  const pricing = attached ?? named;
  if (!pricing) return null;
  const namedId = typeof pricing.params.layerNodeId === 'string' ? pricing.params.layerNodeId.trim() : '';
  // A locked CMS id wins. An output with no CMS id prices the layer that feeds it.
  const cmsLayerId = namedId || layerId;
  const cmsLayer = graph.nodes.find((node) => node.nodeId === cmsLayerId);
  return { pricingNodeId: pricing.nodeId, cmsLayerId, amount: amountOf(cmsLayer) };
}

export function editableInspectorKeys(nodeType: string, params: Record<string, unknown>, isLayer: boolean): string[] {
  return Object.keys(params).filter((key) => !(isLayer && CMS_LAYER_PRICE_KEYS.has(key)));
}
