// Placing a CMS layer suggestion onto the structuring canvas.
// Mirrors backend/app/services/graph_templates.py — keep the two in sync.
//
// A CMS layer always lands with exactly one Pricing Output on the same row,
// outside the blank-canvas scatter box. The Pricing Output links 1:1 to the
// contracted layer when that slot is free. A second click selects the pair.

import type { GraphEdgeSpec, GraphNodeSpec, GraphSpec, GraphTemplate } from '../types/contracts';
import { LAYER_NODE_TYPES, nextLayerCode, smartLabel } from './nodeLabels';

/** Blank-canvas palette drops land in this box (StructuringStudio `addTemplateNode`). */
export const BLANK_CANVAS_X: readonly [number, number] = [420, 560];
export const BLANK_CANVAS_Y: readonly [number, number] = [80, 260];
export const LAYER_COLUMN_X = 600;
export const LAYER_ROW_GAP = 140;
export const SUGGESTED_FROM_PARAM = 'suggestedFrom';

export interface CmsLayerPlacement {
  graph: GraphSpec;
  selectedNodeId: string;
  addedNodeIds: string[];
  addedEdgeIds: string[];
  layerNodeId: string;
  pricingNodeId: string;
  linked: boolean;
  changed: boolean;
}

type IdFactory = (prefix: string) => string;

let idSeq = 0;
const defaultIdFactory: IdFactory = (prefix) => `${prefix}-${Date.now().toString(36)}-${(idSeq++).toString(36)}`;

export function inBlankCanvas(position: { x: number; y: number } | null | undefined): boolean {
  if (!position) return false;
  return (
    position.x >= BLANK_CANVAS_X[0] &&
    position.x <= BLANK_CANVAS_X[1] &&
    position.y >= BLANK_CANVAS_Y[0] &&
    position.y <= BLANK_CANVAS_Y[1]
  );
}

function linkKey(params: Record<string, unknown>): string | null {
  const cid = typeof params.contractId === 'string' ? params.contractId.trim() : '';
  const lid = typeof params.layerNodeId === 'string' ? params.layerNodeId.trim() : '';
  return cid && lid ? `${cid}::${lid}` : null;
}

function isLayer(nodeType: string): boolean {
  return LAYER_NODE_TYPES.includes(nodeType);
}

function usedIds(nodes: GraphNodeSpec[], edges: GraphEdgeSpec[]): Set<string> {
  return new Set([...nodes.map((n) => n.nodeId), ...edges.map((e) => e.edgeId)]);
}

function freshId(used: Set<string>, preferred: string, factory: IdFactory, prefix: string): string {
  if (preferred && !used.has(preferred)) {
    used.add(preferred);
    return preferred;
  }
  let nid = factory(prefix);
  while (!nid || used.has(nid)) nid = factory(prefix);
  used.add(nid);
  return nid;
}

function layerNodes(nodes: GraphNodeSpec[]): GraphNodeSpec[] {
  return nodes.filter((n) => isLayer(n.nodeType));
}

function columnX(nodes: GraphNodeSpec[]): number {
  const layers = layerNodes(nodes);
  return layers.length ? layers[0].position.x : LAYER_COLUMN_X;
}

function nextRowY(nodes: GraphNodeSpec[]): number {
  const layers = layerNodes(nodes);
  if (!layers.length) return 40;
  return Math.max(...layers.map((n) => n.position.y)) + LAYER_ROW_GAP;
}

export function beside(
  layerPos: { x: number; y: number },
  offset: { x: number; y: number },
): { x: number; y: number } {
  const pos = { x: layerPos.x + offset.x, y: layerPos.y + offset.y };
  if (!inBlankCanvas(pos)) return pos;
  const nudged = { x: BLANK_CANVAS_X[1] + 40, y: layerPos.y };
  if (!inBlankCanvas(nudged)) return nudged;
  return { x: nudged.x, y: BLANK_CANVAS_Y[1] + 40 };
}

function layerAnchor(
  nodes: GraphNodeSpec[],
  anchor: { x: number; y: number } | null,
  offset: { x: number; y: number },
): { x: number; y: number } {
  let pos = anchor ?? { x: columnX(nodes), y: nextRowY(nodes) };
  if (inBlankCanvas(pos) || inBlankCanvas(beside(pos, offset))) {
    pos = { x: LAYER_COLUMN_X, y: pos.y };
  }
  if (inBlankCanvas(pos) || inBlankCanvas(beside(pos, offset))) {
    pos = { x: LAYER_COLUMN_X, y: BLANK_CANVAS_Y[1] + LAYER_ROW_GAP };
  }
  return pos;
}

function assignLayerCode(nodes: GraphNodeSpec[], candidate: string): string {
  const existing = layerNodes(nodes).map((n) => (typeof n.params.layerCode === 'string' ? n.params.layerCode : ''));
  const used = new Set(existing.map((c) => c.trim().toLowerCase()).filter(Boolean));
  const code = candidate.trim();
  if (code && !used.has(code.toLowerCase())) return code;
  return nextLayerCode(existing);
}

export function claimedPricingLinks(graphs: GraphSpec[]): Set<string> {
  const keys = new Set<string>();
  for (const graph of graphs) {
    for (const node of graph.nodes) {
      if (node.nodeType !== 'PRICING_OUTPUT') continue;
      const key = linkKey(node.params);
      if (key) keys.add(key);
    }
  }
  return keys;
}

export function applyCmsLayerSuggestion(
  graph: GraphSpec,
  template: GraphTemplate,
  options?: { claimedElsewhere?: Set<string>; idFactory?: IdFactory },
): CmsLayerPlacement {
  const pricing = template.pricingOutput;
  if (!pricing || template.source !== 'CMS' || !isLayer(template.nodeType)) {
    throw new Error('Only a CMS layer suggestion with a pricing output can be placed on the canvas');
  }

  const factory = options?.idFactory ?? defaultIdFactory;
  const offset = pricing.offset ?? { x: 320, y: 0 };
  const linkParams = { ...pricing.params };
  const contractId = String(linkParams.contractId ?? template.contractId ?? '').trim();
  const contractedLayerId = String(linkParams.layerNodeId ?? template.layerNodeId ?? '').trim();
  const external = options?.claimedElsewhere ?? new Set<string>();

  const nodes = graph.nodes.map((n) => ({ ...n, params: { ...n.params }, position: { ...n.position } }));
  const edges = graph.edges.map((e) => ({ ...e }));
  const used = usedIds(nodes, edges);
  const addedNodeIds: string[] = [];
  const addedEdgeIds: string[] = [];

  const linkedNode = (cid: string, lid: string): GraphNodeSpec | undefined => {
    if (!cid || !lid) return undefined;
    const key = `${cid}::${lid}`;
    return nodes.find((n) => n.nodeType === 'PRICING_OUTPUT' && linkKey(n.params) === key);
  };

  const slotTaken = (cid: string, lid: string): boolean => {
    if (!cid || !lid) return false;
    return external.has(`${cid}::${lid}`) || Boolean(linkedNode(cid, lid));
  };

  const fedBy = (layerId: string): GraphNodeSpec | undefined => {
    const targets = new Set(edges.filter((e) => e.source === layerId).map((e) => e.target));
    return nodes.find((n) => targets.has(n.nodeId) && n.nodeType === 'PRICING_OUTPUT');
  };

  const ensureEdge = (source: string, target: string) => {
    if (edges.some((e) => e.source === source && e.target === target)) return;
    const edgeId = freshId(used, `e-px-${target}`, factory, 'e');
    edges.push({ edgeId, source, target, sign: 1, weight: 1 });
    addedEdgeIds.push(edgeId);
  };

  const addPricing = (layerId: string, layerPos: { x: number; y: number }, linkTo: string | null): GraphNodeSpec => {
    const useLink = Boolean(contractId && linkTo && !slotTaken(contractId, linkTo));
    const params: Record<string, unknown> = useLink
      ? { ...linkParams, contractId, layerNodeId: linkTo, labelName: linkParams.labelName ?? 'Pricing' }
      : { labelName: 'Pricing' };
    if (useLink) delete params.unlinked;
    const nodeId = freshId(used, `px-${layerId}`, factory, 'px');
    const node: GraphNodeSpec = {
      nodeId,
      nodeType: 'PRICING_OUTPUT',
      label: smartLabel('PRICING_OUTPUT', params, pricing.label || 'Pricing'),
      params,
      position: beside(layerPos, offset),
    };
    nodes.push(node);
    addedNodeIds.push(nodeId);
    ensureEdge(layerId, nodeId);
    return node;
  };

  const addLayer = (nodeId: string, params: Record<string, unknown>, position: { x: number; y: number }): GraphNodeSpec => {
    const node: GraphNodeSpec = {
      nodeId,
      nodeType: template.nodeType,
      label: smartLabel(template.nodeType, params, template.label),
      params,
      position,
    };
    nodes.push(node);
    addedNodeIds.push(nodeId);
    used.add(nodeId);
    return node;
  };

  const finish = (layerId: string, pricingId: string): CmsLayerPlacement => {
    const changed = addedNodeIds.length > 0 || addedEdgeIds.length > 0;
    const px = nodes.find((n) => n.nodeId === pricingId);
    return {
      graph: changed ? { ...graph, nodes, edges } : graph,
      selectedNodeId: pricingId,
      addedNodeIds,
      addedEdgeIds,
      layerNodeId: layerId,
      pricingNodeId: pricingId,
      linked: Boolean(px && linkKey(px.params)),
      changed,
    };
  };

  if (template.placement === 'EXISTING_LAYER' && contractedLayerId) {
    const layer = nodes.find((n) => n.nodeId === contractedLayerId);
    if (layer && isLayer(layer.nodeType)) {
      const fed = fedBy(layer.nodeId);
      if (fed) return finish(layer.nodeId, fed.nodeId);
      const existing = linkedNode(contractId, contractedLayerId);
      if (existing) {
        ensureEdge(layer.nodeId, existing.nodeId);
        return finish(layer.nodeId, existing.nodeId);
      }
      const created = addPricing(layer.nodeId, layer.position, contractedLayerId);
      return finish(layer.nodeId, created.nodeId);
    }

    const existing = linkedNode(contractId, contractedLayerId);
    if (existing) {
      const leftOfPricing = {
        x: existing.position.x - offset.x,
        y: existing.position.y - offset.y,
      };
      let layerPos: { x: number; y: number };
      if (inBlankCanvas(existing.position) || inBlankCanvas(leftOfPricing)) {
        layerPos = layerAnchor(nodes, null, offset);
        existing.position = beside(layerPos, offset);
      } else {
        layerPos = leftOfPricing;
      }
      const restoredId = used.has(contractedLayerId) ? freshId(used, '', factory, 'lyr') : freshId(used, contractedLayerId, factory, 'lyr');
      const restored = addLayer(restoredId, { ...template.params }, layerPos);
      ensureEdge(restored.nodeId, existing.nodeId);
      return finish(restored.nodeId, existing.nodeId);
    }

    const layerPos = layerAnchor(nodes, template.position ?? null, offset);
    const restored = addLayer(freshId(used, contractedLayerId, factory, 'lyr'), { ...template.params }, layerPos);
    const created = addPricing(restored.nodeId, restored.position, contractedLayerId);
    return finish(restored.nodeId, created.nodeId);
  }

  const existingCopy = nodes.find(
    (n) => isLayer(n.nodeType) && n.params[SUGGESTED_FROM_PARAM] === template.templateId,
  );
  if (existingCopy) {
    const fed = fedBy(existingCopy.nodeId);
    if (fed) return finish(existingCopy.nodeId, fed.nodeId);
    const created = addPricing(existingCopy.nodeId, existingCopy.position, contractedLayerId || existingCopy.nodeId);
    return finish(existingCopy.nodeId, created.nodeId);
  }

  const layerPos = layerAnchor(nodes, null, offset);
  const params: Record<string, unknown> = { ...template.params, [SUGGESTED_FROM_PARAM]: template.templateId };
  params.layerCode = assignLayerCode(nodes, String(params.layerCode ?? template.layerCode ?? ''));
  const copy = addLayer(freshId(used, '', factory, 'lyr'), params, layerPos);
  const created = addPricing(copy.nodeId, copy.position, contractedLayerId || copy.nodeId);
  return finish(copy.nodeId, created.nodeId);
}
