import { describe, expect, it } from 'vitest';
import type { GraphSpec, GraphTemplate } from '../types/contracts';
import { applyCmsLayerSuggestion, inBlankCanvas } from './cmsLayerSuggestion';

function factory(prefix: string) {
  let n = 0;
  return (p: string) => `${prefix}-${p}-${++n}`;
}

function graph(nodes: GraphSpec['nodes'], edges: GraphSpec['edges'] = []): GraphSpec {
  return {
    graphId: 'g',
    name: 'g',
    version: 1,
    currency: 'USD',
    nodes,
    edges,
    contentHash: '',
  };
}

function layerTemplate(over: Partial<GraphTemplate> = {}): GraphTemplate {
  return {
    templateId: 'cms-layer-prog-lyr-1',
    layerCode: 'L1',
    layerNodeId: 'lyr-1',
    contractId: 'prog',
    category: 'CMS Layer · L1',
    source: 'CMS',
    label: 'Cat XL',
    nodeType: 'OCC_XL',
    params: { layerCode: 'L1', labelName: 'Cat XL', occLimit: 40, occAttachment: 60, depositPremium: 8, placedShare: 0.5 },
    placement: 'EXISTING_LAYER',
    position: { x: 600, y: 40 },
    pricingOutput: {
      nodeType: 'PRICING_OUTPUT',
      label: 'Pricing',
      offset: { x: 320, y: 0 },
      params: {
        contractId: 'prog',
        layerNodeId: 'lyr-1',
        labelName: 'Pricing',
        linkedLabel: 'RL-1 · L1 · Cat XL',
      },
    },
    ...over,
  };
}

describe('CMS layer suggestions', () => {
  it('palette click and drag both place the layer and pricing output already joined, outside the blank canvas', () => {
    for (const gesture of ['click', 'drag']) {
      const placed = applyCmsLayerSuggestion(graph([]), layerTemplate(), { idFactory: factory(gesture) });
      expect(placed.addedNodeIds).toEqual([placed.layerNodeId, placed.pricingNodeId]);
      expect(placed.graph.edges).toEqual([
        expect.objectContaining({ source: placed.layerNodeId, target: placed.pricingNodeId, sign: 1 }),
      ]);
      const layer = placed.graph.nodes.find((n) => n.nodeId === placed.layerNodeId)!;
      const px = placed.graph.nodes.find((n) => n.nodeId === placed.pricingNodeId)!;
      expect(inBlankCanvas(layer.position)).toBe(false);
      expect(inBlankCanvas(px.position)).toBe(false);
      expect(px.position).toEqual({ x: layer.position.x + 320, y: layer.position.y });
    }
  });

  it('places one linked pricing output beside a missing layer, outside the blank canvas', () => {
    const placed = applyCmsLayerSuggestion(graph([]), layerTemplate(), { idFactory: factory('a') });
    expect(placed.changed).toBe(true);
    expect(placed.layerNodeId).toBe('lyr-1');
    expect(placed.linked).toBe(true);
    expect(placed.addedNodeIds).toEqual([placed.layerNodeId, placed.pricingNodeId]);
    const layer = placed.graph.nodes.find((n) => n.nodeId === placed.layerNodeId)!;
    const px = placed.graph.nodes.find((n) => n.nodeId === placed.pricingNodeId)!;
    expect(px.nodeType).toBe('PRICING_OUTPUT');
    expect(px.params.contractId).toBe('prog');
    expect(px.params.layerNodeId).toBe('lyr-1');
    expect(px.position).toEqual({ x: layer.position.x + 320, y: layer.position.y });
    expect(inBlankCanvas(layer.position)).toBe(false);
    expect(inBlankCanvas(px.position)).toBe(false);
    expect(placed.graph.edges.some((e) => e.source === layer.nodeId && e.target === px.nodeId)).toBe(true);

    const again = applyCmsLayerSuggestion(placed.graph, layerTemplate(), { idFactory: factory('b') });
    expect(again.changed).toBe(false);
    expect(again.graph.nodes.filter((n) => n.nodeType === 'PRICING_OUTPUT')).toHaveLength(1);
  });

  it('does not drop the pair into the blank-canvas box when the saved anchor is inside it', () => {
    const tpl = layerTemplate({ position: { x: 480, y: 140 } });
    const placed = applyCmsLayerSuggestion(graph([]), tpl, { idFactory: factory('c') });
    const layer = placed.graph.nodes.find((n) => n.nodeId === placed.layerNodeId)!;
    const px = placed.graph.nodes.find((n) => n.nodeId === placed.pricingNodeId)!;
    expect(inBlankCanvas(layer.position)).toBe(false);
    expect(inBlankCanvas(px.position)).toBe(false);
    expect(px.position.x).toBe(layer.position.x + 320);
    expect(px.position.y).toBe(layer.position.y);
  });

  it('adds a pricing output beside a layer that is already on the canvas and does not duplicate the layer', () => {
    const base = graph([
      {
        nodeId: 'lyr-1',
        nodeType: 'OCC_XL',
        label: 'Cat XL',
        params: { layerCode: 'L1' },
        position: { x: 600, y: 180 },
      },
    ]);
    const placed = applyCmsLayerSuggestion(base, layerTemplate(), { idFactory: factory('d') });
    expect(placed.layerNodeId).toBe('lyr-1');
    expect(placed.addedNodeIds).toEqual([placed.pricingNodeId]);
    expect(placed.graph.nodes.filter((n) => n.nodeType === 'OCC_XL')).toHaveLength(1);
    const px = placed.graph.nodes.find((n) => n.nodeId === placed.pricingNodeId)!;
    expect(px.position).toEqual({ x: 920, y: 180 });
    expect(inBlankCanvas(px.position)).toBe(false);
    expect(px.params.layerNodeId).toBe('lyr-1');
    expect(placed.linked).toBe(true);
  });

  it('keeps a single pricing output when the layer already feeds one', () => {
    const base = graph(
      [
        { nodeId: 'lyr-1', nodeType: 'OCC_XL', label: 'Cat XL', params: { layerCode: 'L1' }, position: { x: 600, y: 40 } },
        { nodeId: 'px-lyr-1', nodeType: 'PRICING_OUTPUT', label: 'Pricing', params: {}, position: { x: 920, y: 40 } },
      ],
      [{ edgeId: 'e', source: 'lyr-1', target: 'px-lyr-1', sign: 1, weight: 1 }],
    );
    const placed = applyCmsLayerSuggestion(base, layerTemplate(), { idFactory: factory('e') });
    expect(placed.changed).toBe(false);
    expect(placed.pricingNodeId).toBe('px-lyr-1');
    expect(placed.graph).toBe(base);
    expect(placed.graph.nodes.filter((n) => n.nodeType === 'PRICING_OUTPUT')).toHaveLength(1);
  });

  it('refuses to place a pricing output that is already bound on another programme', () => {
    const base = graph([
      { nodeId: 'lyr-1', nodeType: 'OCC_XL', label: 'Cat XL', params: { layerCode: 'L1' }, position: { x: 600, y: 40 } },
    ]);
    expect(() =>
      applyCmsLayerSuggestion(base, layerTemplate(), {
        claimedElsewhere: new Set(['prog::lyr-1']),
        idFactory: factory('f'),
      }),
    ).toThrow(/already bound/);
    expect(base.nodes.filter((n) => n.nodeType === 'PRICING_OUTPUT')).toHaveLength(0);
  });

  it('shifts the pricing output out of the blank box when the 320 offset would land inside it', () => {
    const base = graph([
      { nodeId: 'lyr-1', nodeType: 'OCC_XL', label: 'Inside', params: { layerCode: 'L1' }, position: { x: 200, y: 100 } },
    ]);
    const placed = applyCmsLayerSuggestion(base, layerTemplate(), { idFactory: factory('n') });
    const layer = placed.graph.nodes.find((n) => n.nodeId === 'lyr-1')!;
    const px = placed.graph.nodes.find((n) => n.nodeId === placed.pricingNodeId)!;
    expect(layer.position).toEqual({ x: 200, y: 100 });
    expect(px.position).toEqual({ x: 600, y: 100 });
    expect(inBlankCanvas(px.position)).toBe(false);
    expect(placed.linked).toBe(true);
  });

  it('restores a deleted layer beside its existing pricing output and does not add a second one', () => {
    const base = graph([
      {
        nodeId: 'px-lyr-1',
        nodeType: 'PRICING_OUTPUT',
        label: 'Pricing',
        params: { contractId: 'prog', layerNodeId: 'lyr-1', labelName: 'Pricing', linkedLabel: 'RL-1 · L1 · Cat XL' },
        position: { x: 920, y: 40 },
      },
    ]);
    const placed = applyCmsLayerSuggestion(base, layerTemplate({ position: { x: 450, y: 120 } }), { idFactory: factory('r') });
    expect(placed.addedNodeIds).toEqual(['lyr-1']);
    expect(placed.pricingNodeId).toBe('px-lyr-1');
    expect(placed.linked).toBe(true);
    const layer = placed.graph.nodes.find((n) => n.nodeId === 'lyr-1')!;
    const px = placed.graph.nodes.find((n) => n.nodeId === 'px-lyr-1')!;
    expect(layer.position).toEqual({ x: 600, y: 40 });
    expect(px.position).toEqual({ x: 920, y: 40 });
    expect(inBlankCanvas(layer.position)).toBe(false);
    expect(placed.graph.edges).toEqual([{ edgeId: 'e-px-px-lyr-1', source: 'lyr-1', target: 'px-lyr-1', sign: 1, weight: 1 }]);
    const second = applyCmsLayerSuggestion(placed.graph, layerTemplate(), { idFactory: factory('r2') });
    expect(second.changed).toBe(false);
    expect(second.graph.nodes.filter((n) => n.nodeType === 'PRICING_OUTPUT')).toHaveLength(1);
  });

  it('binds a programme with no layer node to the new layer id', () => {
    const tpl = layerTemplate({
      templateId: 'cms-layer-prog',
      placement: 'NEW_COPY',
      layerNodeId: undefined,
      pricingOutput: {
        nodeType: 'PRICING_OUTPUT',
        label: 'Pricing',
        offset: { x: 320, y: 0 },
        params: { contractId: 'prog', labelName: 'Pricing', linkedLabel: 'RL · L1' },
      },
    });
    const placed = applyCmsLayerSuggestion(graph([]), tpl, { idFactory: factory('fb') });
    expect(placed.linked).toBe(true);
    const px = placed.graph.nodes.find((n) => n.nodeId === placed.pricingNodeId)!;
    expect(px.params.contractId).toBe('prog');
    expect(px.params.layerNodeId).toBe(placed.layerNodeId);
    expect(inBlankCanvas(placed.graph.nodes.find((n) => n.nodeId === placed.layerNodeId)!.position)).toBe(false);
    expect(inBlankCanvas(px.position)).toBe(false);
    const second = applyCmsLayerSuggestion(placed.graph, tpl, { idFactory: factory('fb2') });
    expect(second.changed).toBe(false);
  });

  it('copies a prior-year layer with one pricing output and does not copy it twice', () => {
    const tpl = layerTemplate({
      templateId: 'cms-prior-old-lyr-1',
      placement: 'NEW_COPY',
      contractId: 'old',
      layerNodeId: 'lyr-1',
      pricingOutput: {
        nodeType: 'PRICING_OUTPUT',
        label: 'Pricing',
        offset: { x: 320, y: 0 },
        params: { contractId: 'old', layerNodeId: 'lyr-1', labelName: 'Pricing', linkedLabel: 'OLD · L1' },
      },
    });
    const host = graph([
      { nodeId: 'lyr-1', nodeType: 'OCC_XL', label: 'Host L1', params: { layerCode: 'L1' }, position: { x: 600, y: 40 } },
    ]);
    const placed = applyCmsLayerSuggestion(host, tpl, { idFactory: factory('h') });
    expect(placed.layerNodeId).not.toBe('lyr-1');
    expect(placed.linked).toBe(true);
    const layer = placed.graph.nodes.find((n) => n.nodeId === placed.layerNodeId)!;
    const px = placed.graph.nodes.find((n) => n.nodeId === placed.pricingNodeId)!;
    expect(layer.params.suggestedFrom).toBe(tpl.templateId);
    expect(String(layer.params.layerCode).toLowerCase()).not.toBe('l1');
    expect(px.params.contractId).toBe('old');
    expect(px.params.layerNodeId).toBe('lyr-1');
    expect(inBlankCanvas(layer.position)).toBe(false);
    expect(inBlankCanvas(px.position)).toBe(false);
    expect(px.position).toEqual({ x: layer.position.x + 320, y: layer.position.y });
    const second = applyCmsLayerSuggestion(placed.graph, tpl, { idFactory: factory('i') });
    expect(second.changed).toBe(false);
    expect(second.graph.nodes.filter((n) => n.nodeType === 'PRICING_OUTPUT')).toHaveLength(1);
  });
});
