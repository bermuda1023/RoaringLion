import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchJson, postJson } from './client';

describe('fetchJson', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('omits Content-Type header on bodyless GET requests to avoid unnecessary CORS preflights', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const out = await fetchJson<{ ok: boolean }>('/api/optimizer/candidates?limit=25');
    expect(out.ok).toBe(true);
    const [, init] = fetchSpy.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.has('Content-Type')).toBe(false);
  });

  it('sets Content-Type application/json on POST requests with a body', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    await postJson('/api/optimizer/explore?num_points=6', { mode: 'FORWARD_PLANNING' });
    const [, init] = fetchSpy.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
  });

  it('retries once on transient network failure for optimizer requests and succeeds', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ frontier: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      );
    const out = await postJson<{ frontier: unknown[] }>('/api/optimizer/explore?num_points=6', { mode: 'CURRENT' });
    expect(out.frontier).toEqual([]);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('surfaces an informative message when backend stays unreachable after retry', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(fetchJson('/api/optimizer/candidates')).rejects.toThrow(/Could not reach backend API/);
  });

  it('does not retry aborted requests or non-idempotent POST mutations', async () => {
    const abortErr = new DOMException('The operation was aborted.', 'AbortError');
    const abortSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(abortErr);
    const controller = new AbortController();
    controller.abort();
    await expect(fetchJson('/api/optimizer/candidates', { signal: controller.signal })).rejects.toThrow(abortErr);
    expect(abortSpy).toHaveBeenCalledTimes(1);

    vi.restoreAllMocks();
    const mutSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(postJson('/api/contracts', { programmeName: 'New' })).rejects.toThrow(/Could not reach backend API/);
    expect(mutSpy).toHaveBeenCalledTimes(1);
  });
});
