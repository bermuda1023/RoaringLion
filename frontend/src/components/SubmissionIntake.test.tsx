// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { intakeApi, type SubmissionConfirmResponse, type SubmissionDraftOut } from '../api/intake';
import { SubmissionsInbox } from '../pages/SubmissionsInbox';
import { SubmissionDropZone } from './SubmissionDropZone';
import { SubmissionReviewCard } from './SubmissionReviewCard';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function makeDraft(overrides: Partial<SubmissionDraftOut> = {}): SubmissionDraftOut {
  return {
    draftId: 'sub-test-001',
    door: 'DROP_IN',
    status: 'PENDING_REVIEW',
    targetProgrammeId: null,
    targetProgrammeLabel: null,
    confirmedProgrammeId: null,
    sourceFilename: 'pelican_slip.pdf',
    sourceFileType: 'PDF',
    sourceRef: 'Drop-in: pelican_slip.pdf',
    sourceHash: 'abc123',
    simSetId: 'sim-synth-cat-v1-100k',
    simSetName: 'Synthetic 100k',
    lossFiles: [],
    overallConfidence: 0.89,
    extracted: {
      cedentName: 'Pelican Bay Assurance',
      programmeName: 'Gulf Coast Cat XL 2027',
      broker: 'Aon Reinsurance',
      inceptionDate: '2027-01-01',
      expiryDate: '2027-12-31',
      treatyYear: 2027,
      renewalPeriod: 'J27',
      currency: 'USD',
      regionPerils: ['US-FL-WS'],
      perils: ['WS'],
      layers: [
        {
          layerCode: 'L1',
          name: 'Layer 1 (25M xs 50M)',
          layerType: 'OCC_XL',
          limit: 25,
          attachment: 50,
          depositPremium: 4.5,
          rol: 0.18,
          writtenLine: 0.12,
          signedLine: 0.12,
          reinstatements: 1,
          confidence: 0.9,
        },
      ],
      lossHistory: [
        {
          year: 2022,
          occurrenceDate: '2022-09-01',
          eventName: 'Hurricane Ian',
          groundUpLossM: 92,
          currency: 'USD',
          regionPerilKey: 'US-FL-WS',
          note: '',
        },
      ],
      fieldConfidence: { cedentName: 0.92, layers: 0.9 },
      warnings: [],
      rawExcerpt: 'Cedent: Pelican Bay Assurance',
    },
    graph: {
      graphId: 'grp-sub-test-001',
      name: 'Pelican Bay Assurance — Gulf Coast Cat XL 2027',
      version: 1,
      currency: 'USD',
      nodes: [
        { nodeId: 'src-1', nodeType: 'LOSS_SOURCE', label: 'Loss Source', params: {}, position: { x: 40, y: 100 } },
        { nodeId: 'lyr-1', nodeType: 'OCC_XL', label: 'L1', params: {}, position: { x: 600, y: 20 } },
        { nodeId: 'px-1', nodeType: 'PRICING_OUTPUT', label: 'Pricing', params: {}, position: { x: 920, y: 20 } },
      ],
      edges: [
        { edgeId: 'e1', source: 'src-1', target: 'lyr-1', sign: 1, weight: 1 },
        { edgeId: 'e2', source: 'lyr-1', target: 'px-1', sign: 1, weight: 1 },
      ],
      contentHash: 'hash1',
    },
    createdAt: '2026-11-27T20:00:00Z',
    updatedAt: '2026-11-27T20:00:00Z',
    decidedAt: null,
    ...overrides,
  };
}

describe('SubmissionDropZone', () => {
  it('uploads a dropped/selected file and emits the created draft', async () => {
    const draft = makeDraft();
    const uploadSpy = vi.spyOn(intakeApi, 'uploadDropIn').mockResolvedValue(draft);
    const onCreated = vi.fn();

    render(<SubmissionDropZone onDraftCreated={onCreated} />);
    const input = screen.getByTestId('submission-file-input');
    const file = new File(['dummy pdf'], 'pelican_slip.pdf', { type: 'application/pdf' });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(1));
    expect(onCreated).toHaveBeenCalledWith(draft);
  });
});

describe('SubmissionReviewCard', () => {
  it('renders extracted fields, confidence, and calls confirmDraft with user edits', async () => {
    const draft = makeDraft();
    const confirmRes = {
      draft: { ...draft, status: 'CONFIRMED', confirmedProgrammeId: 'prog-new-1' },
      contract: { programmeId: 'prog-new-1', programmeName: 'Updated Programme', status: 'SUBMISSION' },
      action: 'CREATED_DRAFT_PROGRAMME',
      manifest: { manifestId: 'man-1' },
    } as unknown as SubmissionConfirmResponse;

    const confirmSpy = vi.spyOn(intakeApi, 'confirmDraft').mockResolvedValue(confirmRes);
    const onConfirmed = vi.fn();

    render(<SubmissionReviewCard draft={draft} onConfirmed={onConfirmed} onDiscarded={vi.fn()} />);

    expect(screen.getByTestId('draft-confidence-badge').textContent).toContain('89%');
    expect(screen.getByDisplayValue('Pelican Bay Assurance')).toBeTruthy();
    expect(screen.getByText('Hurricane Ian')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Programme Name'), { target: { value: 'Updated Programme' } });
    // Edit deposit premium from 4.5 -> 6.25; ROL must update from 0.18 -> 0.25 so edited premium wins
    const premInput = screen.getByDisplayValue('4.5');
    fireEvent.change(premInput, { target: { value: '6.25' } });
    fireEvent.click(screen.getByTestId('confirm-draft-btn'));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(confirmSpy.mock.calls[0][0]).toBe('sub-test-001');
    expect(confirmSpy.mock.calls[0][1]?.extracted?.programmeName).toBe('Updated Programme');
    expect(confirmSpy.mock.calls[0][1]?.extracted?.layers[0]?.depositPremium).toBe(6.25);
    expect(confirmSpy.mock.calls[0][1]?.extracted?.layers[0]?.rol).toBe(0.25);
    expect(onConfirmed).toHaveBeenCalledWith(confirmRes);
  });

  it('calls discardDraft when Discard is clicked', async () => {
    const draft = makeDraft();
    const discarded = { ...draft, status: 'DISCARDED' as const };
    const discardSpy = vi.spyOn(intakeApi, 'discardDraft').mockResolvedValue(discarded);
    const onDiscarded = vi.fn();

    render(<SubmissionReviewCard draft={draft} onConfirmed={vi.fn()} onDiscarded={onDiscarded} />);
    fireEvent.click(screen.getByTestId('discard-draft-btn'));

    await waitFor(() => expect(discardSpy).toHaveBeenCalledWith('sub-test-001'));
    expect(onDiscarded).toHaveBeenCalledWith(discarded);
  });
});

describe('SubmissionsInbox', () => {
  it('displays email inbox as Not connected when no credentials are configured', async () => {
    vi.spyOn(intakeApi, 'getStatus').mockResolvedValue({
      email: {
        connected: false,
        inboxAddress: null,
        provider: 'NONE',
        message: 'Submissions email inbox is not connected.',
      },
      folder: {
        rootDir: '/tmp/submissions_watch',
        exists: true,
        watchedProgrammes: [],
      },
      pendingCount: 1,
      maxUploadMb: 25,
    });
    vi.spyOn(intakeApi, 'listDrafts').mockResolvedValue([makeDraft()]);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <SubmissionsInbox />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('email-door-status').textContent).toBe('Not connected');
      expect(screen.getByTestId('submission-review-card')).toBeTruthy();
    });
  });
});
