// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { OptimizerCandidatesPage, OptimizerLayerCatalogItem } from '../types/contracts';
import { OptimizerCandidatesTable } from './OptimizerCandidatesTable';

function layer(over: Partial<OptimizerLayerCatalogItem>): OptimizerLayerCatalogItem {
  return {
    layerKey: 'p::l',
    programmeId: 'p',
    layerId: 'l',
    cedentName: 'Cedent',
    programmeName: 'Programme',
    layerName: '1st Cat XL',
    direction: 'ASSUMED',
    status: 'BOUND',
    regionPerils: ['US-FL-WS'],
    currentShare: 0.5,
    limit1xM: 20,
    premium1xM: 3,
    el1xM: 1.25,
    expectedProfit1xM: 2,
    rolPct: 15,
    defaultMinShare: 0,
    defaultMaxShare: 1.5,
    ...over,
  };
}

const HEDGE = layer({
  layerKey: 'cand-retro-ilw-fl',
  programmeId: 'cand-retro-ilw-fl',
  cedentName: 'ILW Market Hedge',
  direction: 'EXTERNAL_RETRO',
  status: 'PLANNED',
  currentShare: 0,
  defaultMaxShare: 2,
});

function page(over: Partial<OptimizerCandidatesPage> = {}): OptimizerCandidatesPage {
  return {
    mode: 'FORWARD_PLANNING',
    view: 'ADJUSTED',
    includedStatuses: ['BOUND', 'DECLINED'],
    defaultStatuses: ['BOUND'],
    usesDefaultStatuses: false,
    statusCounts: [],
    universeProgrammes: 1240,
    matchedProgrammes: 1240,
    search: '',
    limit: 25,
    offset: 25,
    programmeIds: ['a', 'b'],
    layers: [
      layer({ layerKey: 'a::l', programmeId: 'a', cedentName: 'Alpha Mutual', status: 'BOUND' }),
      layer({ layerKey: 'b::l', programmeId: 'b', cedentName: 'Beta Declined Re', status: 'DECLINED' }),
    ],
    retroCandidate: HEDGE,
    ...over,
  };
}

function renderTable(over: Partial<Parameters<typeof OptimizerCandidatesTable>[0]> = {}) {
  const props = {
    page: page(),
    isPreviousPage: false,
    offset: 25,
    pageSize: 25,
    isFetching: false,
    error: null,
    onRetry: vi.fn(),
    search: '',
    onSearch: vi.fn(),
    onPage: vi.fn(),
    bounds: {},
    onBoundChange: vi.fn(),
    ...over,
  };
  render(<OptimizerCandidatesTable {...props} />);
  return props;
}

const rowOf = (cedent: string) => screen.getByText(cedent).closest('tr') as HTMLElement;

describe('OptimizerCandidatesTable', () => {
  afterEach(() => cleanup());

  it('shows each deal with its status and marks deals that are not on risk', () => {
    renderTable();
    expect(within(rowOf('Alpha Mutual')).getByText('BOUND')).toBeTruthy();
    expect(rowOf('Alpha Mutual').className).not.toContain('row-off-risk');
    expect(within(rowOf('Beta Declined Re')).getByText('DECLINED')).toBeTruthy();
    expect(rowOf('Beta Declined Re').className).toContain('row-off-risk');
    expect(within(rowOf('ILW Market Hedge')).getByText('HEDGE CANDIDATE')).toBeTruthy();
  });

  it('says which programmes of the total are listed and pages through them', () => {
    const props = renderTable();
    expect(screen.getByText(/Programmes 26–27 of 1,240 · 2 layers/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(props.onPage).toHaveBeenLastCalledWith(50);
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(props.onPage).toHaveBeenLastCalledWith(0);
  });

  it('disables paging at either end', () => {
    renderTable({ page: page({ offset: 0, matchedProgrammes: 2 }), offset: 0 });
    expect((screen.getByRole('button', { name: 'Previous' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('does not page from the previous answer while the one for a new filter is loading', () => {
    // The filter just changed: the request in flight is for offset 0, the rows shown are still page 2.
    renderTable({ isPreviousPage: true, offset: 0 });
    expect((screen.getByRole('button', { name: 'Previous' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('lets the user step back from a page past the last programme', () => {
    const props = renderTable({
      page: page({ offset: 50, matchedProgrammes: 20, programmeIds: [], layers: [], retroCandidate: null }),
      offset: 50,
    });
    expect(screen.getByText(/No programmes on this page · 20 in total/)).toBeTruthy();
    expect(screen.getByText(/past the last programme/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(props.onPage).toHaveBeenLastCalledWith(25);
  });

  it('caps the search at the length the server accepts', () => {
    renderTable();
    expect((screen.getByRole('searchbox', { name: 'Search deals' }) as HTMLInputElement).maxLength).toBe(120);
  });

  it('reports what the user types in the search box', () => {
    const props = renderTable();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search deals' }), { target: { value: 'beta' } });
    expect(props.onSearch).toHaveBeenCalledWith('beta');
  });

  it('explains an empty search result and keeps the hedge candidate out of deal searches', () => {
    renderTable({ page: page({ search: 'nobody', layers: [], programmeIds: [], matchedProgrammes: 0, offset: 0 }), offset: 0 });
    expect(screen.getByText(/No deal in the selected statuses matches “nobody”/)).toBeTruthy();
    expect(screen.getByText('No programmes')).toBeTruthy();
    expect(screen.queryByText('ILW Market Hedge')).toBeNull();
  });

  it('says so when the programmes on a page have no layer of their own', () => {
    renderTable({ page: page({ layers: [], programmeIds: ['a'], matchedProgrammes: 1, offset: 0, retroCandidate: null }) });
    expect(screen.getByText(/have no layer that rolls up/)).toBeTruthy();
    expect(screen.getByText(/Programmes 1–1 of 1 · 0 layers/)).toBeTruthy();
  });

  it('offers a retry instead of a loading message when the deals cannot be loaded', () => {
    const props = renderTable({ page: undefined, error: new Error('Backend unreachable') });
    expect(screen.getByText('Deals could not be loaded')).toBeTruthy();
    expect(screen.queryByText('Loading deals…')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(props.onRetry).toHaveBeenCalled();
  });

  it('locks a layer at its current share and shows an API error', () => {
    const props = renderTable({ error: new Error('Backend unreachable') });
    expect(screen.getByText('Backend unreachable')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: /Lock Beta Declined Re/ }));
    expect(props.onBoundChange).toHaveBeenCalledWith('b::l', { minPct: 50, maxPct: 50, locked: true });
  });
});
