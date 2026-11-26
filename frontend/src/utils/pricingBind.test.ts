import { describe, expect, it } from 'vitest';
import type { GraphSpec } from '../types/contracts';
import { LAYER_ALREADY_BOUND, PRICING_ALREADY_LINKED, connectNodes, removeEdge } from './pricingBind';

function graph(nodes: GraphSpec['nodes'], edges: GraphSpec['edges']): GraphSpec {
  return { graphId: 'g', name: 'g', version: 1, currency: 'USD', nodes, edges, contentHash: '' };
}

function layer(nodeId: string, code: string) {
  return {
    nodeId,
    nodeType: 'OCC_XL',
    label: code,
    params: { layerCode: code, labelName: code, occLimit: 10, occAttachment: 5, depositPremium: 2, placedShare: 1 },
    position: { x: 600, y: 40 },
  };
}

function pricing(layerNodeId: string) {
  return {
    nodeId: 'px',
    nodeType: 'PRICING_OUTPUT',
    label: 'Pricing',
    params: {
      contractId: 'prog',
      layerNodeId,
      labelName: 'Pricing',
      linkedLabel: `RL · ${layerNodeId}`,
    },
    position: { x: 920, y: 40 },
  };
}

describe('pricing output binding', () => {
  const base = graph(
    [layer('lyr-a', 'L1'), layer('lyr-b', 'L2'), pricing('lyr-a')],
    [{ edgeId: 'e-a', source: 'lyr-a', target: 'px', sign: 1, weight: 1 }],
  );

  it('refuses a second layer while the pricing output is already linked', () => {
    const refused = connectNodes(base, 'prog', 'lyr-b', 'px', 'e-b');
    expect('error' in refused && refused.error).toBe(PRICING_ALREADY_LINKED);
    expect('graph' in refused).toBe(false);
    expect(base.edges).toHaveLength(1);
  });

  it('disconnects like any other edge, then reconnects the bind onto another layer', () => {
    const disconnected = removeEdge(base, 'e-a');
    expect(disconnected.edges).toEqual([]);
    expect(disconnected.nodes.find((n) => n.nodeId === 'px')?.params.layerNodeId).toBe('lyr-a');

    const moved = connectNodes(disconnected, 'prog', 'lyr-b', 'px', 'e-b');
    expect('error' in moved).toBe(false);
    if ('error' in moved) return;
    const px = moved.graph.nodes.find((n) => n.nodeId === 'px')!;
    expect(px.params.layerNodeId).toBe('lyr-b');
    expect(px.params.contractId).toBe('prog');
    expect(String(px.params.linkedLabel)).toContain('L2');
    expect(moved.graph.edges).toEqual([{ edgeId: 'e-b', source: 'lyr-b', target: 'px', sign: 1, weight: 1 }]);
    expect(moved.graph.edges.some((e) => e.source === 'lyr-a')).toBe(false);
  });

  it('refuses to move onto a layer that already has a pricing output', () => {
    const taken = graph(
      [
        ...base.nodes,
        {
          nodeId: 'px-b',
          nodeType: 'PRICING_OUTPUT',
          label: 'Pricing B',
          params: { contractId: 'prog', layerNodeId: 'lyr-b', linkedLabel: 'RL · L2' },
          position: { x: 920, y: 180 },
        },
      ],
      [
        { edgeId: 'e-a', source: 'lyr-a', target: 'px', sign: 1, weight: 1 },
        { edgeId: 'e-b', source: 'lyr-b', target: 'px-b', sign: 1, weight: 1 },
      ],
    );
    const disconnected = removeEdge(taken, 'e-a');
    const refused = connectNodes(disconnected, 'prog', 'lyr-b', 'px', 'e-move');
    expect('error' in refused && refused.error).toBe(LAYER_ALREADY_BOUND);
  });

  it('keeps an unlinked pricing output out of selective rollup when it is only previewed', () => {
    const preview = graph(
      [layer('lyr-a', 'L1'), { nodeId: 'px', nodeType: 'PRICING_OUTPUT', label: 'Pricing', params: { unlinked: true }, position: { x: 920, y: 40 } }],
      [],
    );
    const wired = connectNodes(preview, 'prog', 'lyr-a', 'px', 'e-preview');
    expect('error' in wired).toBe(false);
    if ('error' in wired) return;
    expect(wired.graph.nodes.find((n) => n.nodeId === 'px')?.params).toEqual({ unlinked: true });
    expect(wired.graph.edges[0]).toMatchObject({ source: 'lyr-a', target: 'px' });
  });
});
