// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { GraphSpec } from '../types/contracts';
import { PricingOutputPanel } from './OutputNodePanels';

const graph: GraphSpec = {
  graphId: 'g',
  name: 'g',
  version: 1,
  currency: 'USD',
  nodes: [
    {
      nodeId: 'lyr-graph',
      nodeType: 'OCC_XL',
      label: 'Graph Layer',
      params: { layerCode: 'L2' },
      position: { x: 0, y: 0 },
    },
    {
      nodeId: 'px-1',
      nodeType: 'PRICING_OUTPUT',
      label: 'Pricing',
      params: { contractId: 'prog-cms', layerNodeId: 'lyr-cms' },
      position: { x: 0, y: 0 },
    },
  ],
  edges: [{ edgeId: 'e1', source: 'lyr-graph', target: 'px-1', sign: 1, weight: 1 }],
  contentHash: 'test',
};

describe('pricing output panel', () => {
  afterEach(() => cleanup());

  it('shows the locked identity and the attached graph layer, with no editors', () => {
    render(
      <PricingOutputPanel
        node={graph.nodes[1]}
        graph={graph}
        adjusted={{ nodeId: 'px-1', nodeLabel: 'Pricing', nodeType: 'PRICING_OUTPUT', view: 'ADJUSTED', currency: 'USD', el: 1.5, sd: 0, maxOcc: 0, maxYear: 0, expectedRip: 0, elr: 0.2, epPoints: [] }}
        unadjusted={{ nodeId: 'px-1', nodeLabel: 'Pricing', nodeType: 'PRICING_OUTPUT', view: 'UNADJUSTED', currency: 'USD', el: 1.2, sd: 0, maxOcc: 0, maxYear: 0, expectedRip: 0, elr: 0.15, epPoints: [] }}
      />,
    );
    const panel = screen.getByTestId('pricing-output-panel');
    expect(panel.textContent).toContain('px-1');
    expect(panel.textContent).toContain('prog-cms');
    expect(panel.textContent).toContain('lyr-cms');
    expect(panel.textContent).toContain('Graph Layer');
    expect(panel.textContent).toContain('Adjusted');
    expect(panel.textContent).toContain('Unadjusted');
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Adjusted' })).toBeNull();
  });
});