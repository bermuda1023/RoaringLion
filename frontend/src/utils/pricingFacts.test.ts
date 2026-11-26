import { describe, expect, it } from 'vitest';
import type { GraphSpec } from '../types/contracts';
import { CMS_LAYER_PRICE_KEYS, editableInspectorKeys, layerPremiumViaPricing } from './pricingFacts';

function graph(): GraphSpec {
  return {
    graphId: 'g',
    name: 'g',
    version: 1,
    currency: 'USD',
    contentHash: '',
    nodes: [
      {
        nodeId: 'lyr-cms',
        nodeType: 'OCC_XL',
        label: 'Bought layer',
        params: { layerCode: 'L1', depositPremium: 8, technicalRol: 0.12, occLimit: 40 },
        position: { x: 0, y: 0 },
      },
      {
        nodeId: 'lyr-built',
        nodeType: 'OCC_XL',
        label: 'Trial structure',
        params: { layerCode: 'L2', depositPremium: 4, technicalRol: 0.2, occLimit: 22 },
        position: { x: 0, y: 80 },
      },
      {
        nodeId: 'px',
        nodeType: 'PRICING_OUTPUT',
        label: 'Pricing',
        params: { contractId: 'prog', layerNodeId: 'lyr-cms' },
        position: { x: 200, y: 80 },
      },
    ],
    edges: [{ edgeId: 'e', source: 'lyr-built', target: 'px', sign: 1, weight: 1 }],
  };
}

describe('layer premium through the pricing output', () => {
  it('reads the locked CMS premium for the graph layer the pricing output is attached to', () => {
    const fact = layerPremiumViaPricing(graph(), 'lyr-built');
    expect(fact).toMatchObject({ pricingNodeId: 'px', cmsLayerId: 'lyr-cms', amount: 8 });
  });

  it('still shows the CMS layer its own premium through the pricing output that names it', () => {
    const fact = layerPremiumViaPricing(graph(), 'lyr-cms');
    expect(fact).toMatchObject({ pricingNodeId: 'px', cmsLayerId: 'lyr-cms', amount: 8 });
  });

  it('uses the feeding layer’s premium when the pricing output has no CMS id', () => {
    const spec = graph();
    const pricing = spec.nodes.find((node) => node.nodeId === 'px');
    if (pricing) pricing.params = {};
    expect(layerPremiumViaPricing(spec, 'lyr-built')).toMatchObject({ pricingNodeId: 'px', cmsLayerId: 'lyr-built', amount: 4 });
    expect(layerPremiumViaPricing(spec, 'lyr-cms')).toBeNull();
  });

  it('has no figure when no pricing output is linked', () => {
    const spec = graph();
    spec.nodes = spec.nodes.filter((node) => node.nodeType !== 'PRICING_OUTPUT');
    expect(layerPremiumViaPricing(spec, 'lyr-built')).toBeNull();
  });

  it('drops premium and ROL from the structuring inspector for a layer', () => {
    const keys = editableInspectorKeys('OCC_XL', graph().nodes[0].params, true);
    expect(keys).toContain('occLimit');
    expect(keys.some((key) => CMS_LAYER_PRICE_KEYS.has(key))).toBe(false);
  });
});
