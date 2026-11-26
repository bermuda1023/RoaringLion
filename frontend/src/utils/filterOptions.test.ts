import { describe, expect, it } from 'vitest';
import type { GraphSpec } from '../types/contracts';
import { filterChoices, type LossFileRef } from './filterOptions';

function graph(): GraphSpec {
  return {
    graphId: 'g',
    name: 'g',
    version: 1,
    currency: 'USD',
    contentHash: '',
    nodes: [
      {
        nodeId: 'src-fl',
        nodeType: 'LOSS_SOURCE',
        label: 'Florida wind files',
        params: {
          simSetId: 'sim-mixed',
          lossFiles: [
            { region: 'US-FL', peril: 'WS', regionPeril: 'US-FL-WS', vendor: 'RMS', name: 'FL WS' },
            { region: 'US-TX', peril: 'WS', regionPeril: 'US-TX-WS', vendor: 'VERISK', name: 'TX WS' },
          ] satisfies LossFileRef[],
        },
        position: { x: 0, y: 0 },
      },
      {
        nodeId: 'src-eq',
        nodeType: 'LOSS_SOURCE',
        label: 'Quake file',
        params: {
          lossFiles: [{ region: 'US-CA', peril: 'EQ', regionPeril: 'US-CA-EQ', vendor: 'KCC', name: 'CA EQ' }],
        },
        position: { x: 0, y: 80 },
      },
      { nodeId: 'flt', nodeType: 'REGION_PERIL_FILTER', label: 'Filter', params: {}, position: { x: 200, y: 0 } },
      { nodeId: 'mid', nodeType: 'COMBINATION', label: 'Mid', params: {}, position: { x: 100, y: 0 } },
    ],
    edges: [
      { edgeId: 'e1', source: 'src-fl', target: 'mid', sign: 1, weight: 1 },
      { edgeId: 'e2', source: 'mid', target: 'flt', sign: 1, weight: 1 },
    ],
  };
}

describe('filter choices from upstream loss files', () => {
  it('offers only the regions and perils of the files feeding the filter', () => {
    const choices = filterChoices(graph(), 'flt', {}, 'sim-other');
    expect(choices.zones).toEqual(['US-FL', 'US-TX']);
    expect(choices.perils.map((peril) => peril.code)).toEqual(['WS']);
    expect(choices.regionPerils).toEqual(['US-FL-WS', 'US-TX-WS']);
    expect(choices.regionPerils).not.toContain('JP-KANTO-EQ');
    expect(choices.regionPerils).not.toContain('US-CA-EQ');
    expect(choices.sourceLabels).toEqual(['Florida wind files']);
  });

  it('uses the sim set files when the loss source does not list them itself', () => {
    const spec = graph();
    spec.nodes[0].params = { simSetId: 'sim-eq' };
    spec.edges = [{ edgeId: 'e1', source: 'src-fl', target: 'flt', sign: 1, weight: 1 }];
    const choices = filterChoices(
      spec,
      'flt',
      { 'sim-eq': [{ region: 'JP-KANTO', peril: 'EQ', regionPeril: 'JP-KANTO-EQ' }] },
      'sim-synth',
    );
    expect(choices.regionPerils).toEqual(['JP-KANTO-EQ']);
    expect(choices.zones).toEqual(['JP-KANTO']);
  });

  it('offers nothing when no loss source feeds the filter', () => {
    const spec = graph();
    spec.edges = [];
    const choices = filterChoices(spec, 'flt', { 'sim-synth': [{ region: 'US-FL', peril: 'WS', regionPeril: 'US-FL-WS' }] }, 'sim-synth');
    expect(choices.regionPerils).toEqual([]);
    expect(choices.sourceLabels).toEqual([]);
  });
});
