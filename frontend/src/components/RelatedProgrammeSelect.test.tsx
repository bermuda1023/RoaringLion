// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RelatedProgrammeSelect } from './RelatedProgrammeSelect';

const current = {
  programmeId: 'prog-open',
  cedentName: 'Open Re',
  programmeName: 'Open XL',
  treatyYear: 2027,
  renewalPeriod: 'J27',
  status: 'QUOTED',
};

describe('linked programme list', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('asks for the open programme’s chain and does not list the rest of the book', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toContain('/api/programmes/prog-open/related');
      expect(url).not.toContain('/api/programmes?');
      return {
        ok: true,
        json: async () => [
          current,
          {
            programmeId: 'prog-prior',
            cedentName: 'Open Re',
            programmeName: 'Open XL',
            treatyYear: 2026,
            renewalPeriod: 'J26',
            status: 'BOUND',
          },
        ],
      };
    });
    vi.stubGlobal('fetch', fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <RelatedProgrammeSelect programmeId="prog-open" current={current} onChange={() => undefined} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('option', { name: /J26/ })).toBeTruthy();
    expect(screen.getAllByRole('option')).toHaveLength(2);
    expect(screen.queryByRole('option', { name: /Unrelated/ })).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
