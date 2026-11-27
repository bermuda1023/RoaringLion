// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ContractLayersEditor } from './ContractLayersEditor';

const layer = {
  nodeId: 'lyr-1',
  layerCode: 'L1',
  label: 'Cat XL',
  nodeType: 'OCC_XL',
  limit: 50,
  attachment: 80,
  depositPremium: 11.5,
  rol: 0.23,
  reinstatements: 1,
  ripRate: 1,
  authorizedLine: 1,
  writtenLine: 1,
  signedLine: 0.5,
  ourLimit: 25,
  ourPremium: 5.75,
  brokeragePct: 0.1,
  ourNetPremium: 5.175,
  technicalRol: 0.2,
  priceAdequacy: 1.15,
  editable: [
    'layerCode',
    'label',
    'limit',
    'attachment',
    'depositPremium',
    'rol',
    'technicalRol',
    'reinstatements',
    'ripRate',
    'brokeragePct',
    'authorizedLine',
    'writtenLine',
    'signedLine',
  ],
  name: 'Cat XL',
  terms: '50m xs 80m',
  autoLabel: true,
  linkedPricingLabel: 'Pricing Output',
  linkedFeederLabel: null,
};

describe('layer terms grid', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('keeps every field, with technical ROL behind the row detail', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => [layer] })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContractLayersEditor programmeId="prog" currency="USD" onSaved={() => undefined} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Limit xs att')).toBeTruthy();
    expect(screen.getByText('Prem / ROL')).toBeTruthy();
    expect(screen.getByText('A / W / S / Br')).toBeTruthy();
    expect(screen.queryByText('Technical ROL %')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /show detail/i }));
    expect(screen.getByText('Technical ROL %')).toBeTruthy();
    expect(screen.getByText('Reinstatements')).toBeTruthy();
    expect(screen.getByText('RIP %')).toBeTruthy();
    expect(screen.getByText('Pricing output')).toBeTruthy();
    expect(screen.getByText('Pricing Output')).toBeTruthy();
    expect(screen.getAllByDisplayValue('50').length).toBeGreaterThan(0);
    expect(screen.getByDisplayValue('11.5')).toBeTruthy();
  });
});