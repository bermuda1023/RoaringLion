// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { GraphTemplate } from '../types/contracts';
import { CMS_LAYER_DRAG_TYPE, CmsSuggestionCard } from './CmsSuggestionCard';

const template: GraphTemplate = {
  templateId: 'cms-layer-prog-lyr-1',
  layerCode: 'L1',
  layerNodeId: 'lyr-1',
  contractId: 'prog',
  category: 'CMS Layer · L1',
  source: 'CMS',
  label: 'Cat XL',
  nodeType: 'OCC_XL',
  params: { layerCode: 'L1' },
  placement: 'EXISTING_LAYER',
  pricingOutput: {
    nodeType: 'PRICING_OUTPUT',
    label: 'Pricing',
    offset: { x: 320, y: 0 },
    params: { contractId: 'prog', layerNodeId: 'lyr-1', labelName: 'Pricing' },
  },
};

describe('CMS suggestion tree', () => {
  afterEach(() => cleanup());

  it('shows the pricing output under the layer before it is placed', () => {
    render(<CmsSuggestionCard template={template} onCanvas={false} onPlace={() => undefined} />);
    expect(screen.getByText('Pricing Output')).toBeTruthy();
    expect(screen.getByText('Pricing')).toBeTruthy();
    expect(screen.getByText('Layer')).toBeTruthy();
    expect(screen.getAllByText('Cat XL').length).toBeGreaterThan(0);
  });

  it('click and drag both request placement of the same suggestion', () => {
    const onPlace = vi.fn();
    render(<CmsSuggestionCard template={template} onCanvas={false} onPlace={onPlace} />);
    const card = screen.getByRole('button', { name: /pricing output/i });

    fireEvent.click(card);
    expect(onPlace).toHaveBeenCalledTimes(1);
    expect(onPlace).toHaveBeenCalledWith(template);

    const dataTransfer = {
      setData: vi.fn(),
      effectAllowed: 'copy',
    };
    fireEvent.dragStart(card, { dataTransfer });
    expect(dataTransfer.setData).toHaveBeenCalledWith(CMS_LAYER_DRAG_TYPE, template.templateId);
    expect(dataTransfer.setData).toHaveBeenCalledWith('text/plain', template.templateId);
  });
});
