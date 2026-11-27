// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ContractLayersEditor, type ContractLayer } from './ContractLayersEditor';

const layer1: ContractLayer = {
  nodeId: 'lyr-1',
  layerCode: 'L1',
  label: '1st Cat XL',
  nodeType: 'OCC_XL',
  limit: 50,
  attachment: 80,
  depositPremium: 10,
  rol: 0.2,
  reinstatements: 1,
  ripRate: 1,
  writtenLine: 0.8,
  signedLine: 0.5,
  ourLimit: 25,
  ourPremium: 5,
  brokeragePct: 0.1,
  ourNetPremium: 4.5,
  rarcPct: 10,
  expectedLoss: 1.0,
  expectedRip: 0.25,
  lossRatio: 1.0 / 5.25,
  editable: [
    'layerCode',
    'label',
    'limit',
    'attachment',
    'depositPremium',
    'rol',
    'rarcPct',
    'reinstatements',
    'ripRate',
    'brokeragePct',
    'writtenLine',
    'signedLine',
  ],
  name: '1st Cat XL',
  terms: '50m xs 80m',
  autoLabel: true,
  linkedPricingLabel: 'Pricing Output',
  linkedFeederLabel: null,
};

const layer2: ContractLayer = {
  nodeId: 'lyr-2',
  layerCode: 'L2',
  label: '2nd Cat XL',
  nodeType: 'AGG_XL',
  limit: 50,
  attachment: 130,
  depositPremium: 5,
  rol: 0.1,
  reinstatements: null,
  ripRate: null,
  writtenLine: 0.5,
  signedLine: 0.5,
  ourLimit: 25,
  ourPremium: 2.5,
  brokeragePct: 0.1,
  ourNetPremium: 2.25,
  rarcPct: 4,
  expectedLoss: 0.25,
  expectedRip: 0,
  lossRatio: 0.1,
  editable: [
    'layerCode',
    'label',
    'limit',
    'attachment',
    'depositPremium',
    'rol',
    'rarcPct',
    'brokeragePct',
    'writtenLine',
    'signedLine',
  ],
  name: '2nd Cat XL',
  terms: '50m xs 130m',
  autoLabel: true,
  linkedPricingLabel: null,
  linkedFeederLabel: null,
};

describe('ContractLayersEditor', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('removes authorized line and technical ROL, shows written/signed, editable RARC, and read-only Loss Ratio', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => [layer1, layer2] })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContractLayersEditor programmeId="prog" currency="USD" onSaved={() => undefined} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Limit xs att')).toBeTruthy();
    expect(screen.getByText('Prem / ROL')).toBeTruthy();
    expect(screen.getByText('W / S / Br')).toBeTruthy();
    expect(screen.queryByText('A / W / S / Br')).toBeNull();
    expect(screen.getByText('RARC %')).toBeTruthy();
    expect(screen.getByText('Loss Ratio')).toBeTruthy();
    expect(screen.queryByText(/Technical ROL/i)).toBeNull();
    expect(screen.queryByText(/Authorised/i)).toBeNull();

    // Loss ratio values are shown read-only: layer1 = 19.0%, layer2 = 10.0%, total = 1.25 / 7.75 = 16.1%
    expect(screen.getByText('19.0%')).toBeTruthy();
    expect(screen.getByText('10.0%')).toBeTruthy();
    expect(screen.getByText('16.1%')).toBeTruthy();

    // Initial weighted RARC = (25*10 + 25*4) / 50 = +7.0%
    expect(screen.getByText('+7.0%')).toBeTruthy();

    // Expand row detail: shows read-only Loss Ratio & Modelled EL, no Technical ROL input
    fireEvent.click(screen.getByRole('button', { name: /show detail for 1st Cat XL/i }));
    expect(screen.queryByText(/Technical ROL/i)).toBeNull();
    expect(screen.getByText('Modelled EL (Adj)')).toBeTruthy();
    expect(screen.getByText('Reinstatements')).toBeTruthy();
    expect(screen.getByText('RIP %')).toBeTruthy();
    expect(screen.getByText('Pricing output')).toBeTruthy();
    expect(screen.getByText('Pricing Output')).toBeTruthy();
  });

  it('recomputes weighted programme RARC live and persists layer RARC on save', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body));
        const updated = [layer1, layer2].map((l) => {
          const patch = body.layers.find((u: { nodeId: string }) => u.nodeId === l.nodeId);
          return patch ? { ...l, ...patch } : l;
        });
        return { ok: true, json: async () => updated };
      }
      return { ok: true, json: async () => [layer1, layer2] };
    });
    vi.stubGlobal('fetch', fetchMock);

    const onSaved = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContractLayersEditor programmeId="prog" currency="USD" onSaved={onSaved} />
      </QueryClientProvider>,
    );

    const rarcInput = await screen.findByLabelText('RARC for 1st Cat XL');
    // Change layer1 RARC from 10 to 16 -> weighted average = (25*16 + 25*4) / 50 = +10.0%
    fireEvent.change(rarcInput, { target: { value: '16' } });
    expect(screen.getByText('+10.0%')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Save 1 layer/i }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));

    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(putCall).toBeTruthy();
    const sent = JSON.parse(String(putCall?.[1]?.body));
    expect(sent.layers).toEqual([{ nodeId: 'lyr-1', rarcPct: 16 }]);
  });

  it('respects selective rollup when a layer is explicitly linked to a Pricing Output', async () => {
    const linkedLayer: ContractLayer = {
      ...layer1,
      linkedPricingNodeId: 'px-lyr-1',
      linkedPricingLabel: 'Pricing · L1',
    };
    const exploratoryLayer: ContractLayer = {
      ...layer2,
      rarcPct: -20,
      linkedPricingNodeId: null,
      linkedPricingLabel: null,
    };
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => [linkedLayer, exploratoryLayer] })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContractLayersEditor programmeId="prog" currency="USD" onSaved={() => undefined} />
      </QueryClientProvider>,
    );

    // Total RARC and Loss Ratio follow only the linked layer (+10.0% and 19.0%), ignoring the unlinked exploratory layer
    expect(await screen.findByText('+10.0%')).toBeTruthy();
    expect(screen.getAllByText('19.0%').length).toBe(2);
  });
});
