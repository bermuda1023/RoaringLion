import { describe, expect, it } from 'vitest';
import type { GraphSpec } from '../types/contracts';
import { cleanGraphForSave } from './graphSerialize';

describe('cleanGraphForSave', () => {
  it('omits empty or null depositPremium on fresh layer nodes while preserving numeric depositPremium on CMS layers', () => {
    const graph: GraphSpec = {
      graphId: 'g-1',
      name: 'Test',
      version: 1,
      currency: 'USD',
      contentHash: 'h-1',
      nodes: [
        {
          nodeId: 'lyr-fresh',
          nodeType: 'OCC_XL',
          label: 'Occurrence XL',
          params: {
            layerCode: 'L2',
            occLimit: '40',
            occAttachment: '60',
            occFranchise: '0',
            reinstatements: '',
            ripRate: '0',
            depositPremium: '',
            placedShare: 1.0,
          },
          position: { x: 420, y: 100 },
        },
        {
          nodeId: 'lyr-null-dp',
          nodeType: 'AGG_XL',
          label: 'Agg XL',
          params: {
            layerCode: 'L3',
            aggLimit: '25',
            aggAttachment: '80',
            perEventCap: '',
            depositPremium: null,
            placedShare: 1.0,
          },
          position: { x: 420, y: 200 },
        },
        {
          nodeId: 'lyr-cms',
          nodeType: 'OCC_XL',
          label: 'CMS Layer',
          params: {
            layerCode: 'L1',
            occLimit: 50,
            occAttachment: 80,
            depositPremium: 11.5,
            placedShare: 1.0,
          },
          position: { x: 600, y: 40 },
        },
      ],
      edges: [],
    };

    const cleaned = cleanGraphForSave(graph);
    const fresh = cleaned.nodes[0].params;
    expect('depositPremium' in fresh).toBe(false);
    expect(fresh.occLimit).toBe(40);
    expect(fresh.occAttachment).toBe(60);
    expect(fresh.reinstatements).toBeNull();
    expect(fresh.layerCode).toBe('L2');

    const agg = cleaned.nodes[1].params;
    expect('depositPremium' in agg).toBe(false);
    expect(agg.aggLimit).toBe(25);
    expect(agg.perEventCap).toBeNull();

    const cms = cleaned.nodes[2].params;
    expect(cms.depositPremium).toBe(11.5);
  });
});
