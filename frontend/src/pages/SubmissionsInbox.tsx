import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  intakeApi,
  type IntakeDraftStatus,
  type SubmissionConfirmResponse,
  type SubmissionDraftOut,
} from '../api/intake';
import { SubmissionDropZone } from '../components/SubmissionDropZone';
import { SubmissionReviewCard } from '../components/SubmissionReviewCard';

const DOOR_BADGE: Record<string, string> = {
  DROP_IN: 'Drop-in',
  EMAIL: 'Email',
  FOLDER: 'Folder',
};

export function SubmissionsInbox({
  onOpenProgramme,
}: {
  onOpenProgramme?: (programmeId: string) => void;
}) {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<IntakeDraftStatus | 'ALL'>('PENDING_REVIEW');
  const [selectedDraftId, setSelectedDraftId] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [busyDoor, setBusyDoor] = useState<'email' | 'folder' | null>(null);

  const statusQ = useQuery({
    queryKey: ['intakeStatus'],
    queryFn: intakeApi.getStatus,
  });

  const draftsQ = useQuery({
    queryKey: ['intakeDrafts', statusFilter],
    queryFn: () => intakeApi.listDrafts(statusFilter === 'ALL' ? undefined : { status: statusFilter }),
  });

  const drafts = draftsQ.data ?? [];
  const selectedDraft: SubmissionDraftOut | null =
    drafts.find((d) => d.draftId === selectedDraftId) ?? drafts[0] ?? null;

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ['intakeDrafts'] });
    qc.invalidateQueries({ queryKey: ['intakeStatus'] });
    qc.invalidateQueries({ queryKey: ['contracts'] });
    qc.invalidateQueries({ queryKey: ['programmes'] });
    qc.invalidateQueries({ queryKey: ['health'] });
  };

  const handlePollEmail = async () => {
    setBusyDoor('email');
    setActionMsg(null);
    try {
      const res = await intakeApi.pollEmail();
      setActionMsg(
        res.connected
          ? `Email inbox polled: ${res.processedCount} new message(s), ${res.skippedCount} skipped.`
          : 'Email inbox is not connected.',
      );
      if (res.drafts.length > 0) {
        setStatusFilter('PENDING_REVIEW');
        setSelectedDraftId(res.drafts[0].draftId);
      }
      refreshAll();
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : 'Email poll failed.');
    } finally {
      setBusyDoor(null);
    }
  };

  const handleScanFolders = async () => {
    setBusyDoor('folder');
    setActionMsg(null);
    try {
      const res = await intakeApi.scanFolders();
      setActionMsg(
        `Scanned ${res.scannedProgrammes} watched folder(s): ${res.pickedUpCount} new draft(s) queued, ${res.skippedCount} already processed.`,
      );
      if (res.drafts.length > 0) {
        setStatusFilter('PENDING_REVIEW');
        setSelectedDraftId(res.drafts[0].draftId);
      }
      refreshAll();
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : 'Folder scan failed.');
    } finally {
      setBusyDoor(null);
    }
  };

  const email = statusQ.data?.email;
  const folder = statusQ.data?.folder;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Three Intake Doors Overview */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14 }}>
        {/* Door 1: Drop-in */}
        <div className="card" style={{ margin: 0 }}>
          <div className="card-title">
            <span>1. Drop-in Submissions</span>
            <span className="pill">Shared Inbox</span>
          </div>
          <SubmissionDropZone
            compact
            onDraftCreated={(draft) => {
              setStatusFilter('PENDING_REVIEW');
              setSelectedDraftId(draft.draftId);
              setActionMsg(`Queued draft from ${draft.sourceFilename} (${draft.sourceFileType}) — review below before confirming.`);
              refreshAll();
            }}
          />
        </div>

        {/* Door 2: Email */}
        <div className="card" style={{ margin: 0 }} data-testid="email-door-card">
          <div className="card-title">
            <span>2. Submissions Email</span>
            <span
              className="pill"
              data-testid="email-door-status"
              style={{
                background: email?.connected ? 'var(--positive-bg)' : 'var(--warning-bg)',
                color: email?.connected ? 'var(--positive)' : 'var(--warning)',
                borderColor: 'transparent',
              }}
            >
              {email?.connected ? 'Connected' : 'Not connected'}
            </span>
          </div>
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginBottom: 10 }}>
            {email?.message ?? 'Checking submissions email inbox status…'}
          </div>
          {email?.inboxAddress && (
            <div style={{ fontSize: 'var(--fs-xs)', marginBottom: 10 }}>
              Configured address: <code>{email.inboxAddress}</code>
            </div>
          )}
          <button
            type="button"
            className="btn secondary"
            disabled={busyDoor !== null || !email?.connected}
            onClick={() => void handlePollEmail()}
          >
            {busyDoor === 'email' ? 'Polling…' : 'Poll Inbox'}
          </button>
        </div>

        {/* Door 3: Watched Folders */}
        <div className="card" style={{ margin: 0 }} data-testid="folder-door-card">
          <div className="card-title">
            <span>3. Watched Programme Folders</span>
            <span className="pill">{folder?.watchedProgrammes.length ?? 0} watched</span>
          </div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
            Root: <code>{folder?.rootDir ?? '…'}</code>
          </div>
          {folder && folder.watchedProgrammes.length > 0 ? (
            <div style={{ fontSize: 'var(--fs-xs)', marginBottom: 10 }}>
              {folder.watchedProgrammes.slice(0, 4).map((wp) => (
                <div key={wp.programmeId} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
                  <span>{wp.programmeName}</span>
                  <code>{wp.watchedFolder} ({wp.pendingFiles} new)</code>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
              Configure a watched folder on any programme under Contracts → Intake.
            </div>
          )}
          <button
            type="button"
            className="btn secondary"
            disabled={busyDoor !== null}
            onClick={() => void handleScanFolders()}
          >
            {busyDoor === 'folder' ? 'Scanning…' : 'Scan All Folders'}
          </button>
        </div>
      </div>

      {actionMsg && (
        <div
          role="status"
          data-testid="intake-status-banner"
          style={{
            padding: '10px 14px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--surface-2)',
            border: '1px solid var(--border-strong)',
            fontSize: 'var(--fs-sm)',
          }}
        >
          {actionMsg}
        </div>
      )}

      {/* Selected Draft Review Panel */}
      {selectedDraft && (
        <SubmissionReviewCard
          draft={selectedDraft}
          onConfirmed={(res: SubmissionConfirmResponse) => {
            setActionMsg(
              res.action === 'CREATED_DRAFT_PROGRAMME'
                ? `Confirmed into CMS: created draft programme "${res.contract.programmeName}" (${res.contract.status}).`
                : `Confirmed into CMS: updated "${res.contract.programmeName}" (status unchanged: ${res.contract.status}).`,
            );
            refreshAll();
          }}
          onDiscarded={(d) => {
            setActionMsg(`Discarded draft "${d.sourceFilename}" — CMS unchanged.`);
            refreshAll();
          }}
        />
      )}

      {/* Draft Queue Table */}
      <div className="card" style={{ margin: 0 }}>
        <div className="card-title" style={{ flexWrap: 'wrap', gap: 10 }}>
          <span>Submissions Draft Queue ({drafts.length})</span>
          <div style={{ display: 'flex', gap: 6 }}>
            {(['PENDING_REVIEW', 'CONFIRMED', 'DISCARDED', 'ALL'] as const).map((st) => (
              <button
                key={st}
                type="button"
                className={`tab ${statusFilter === st ? 'active' : ''}`}
                onClick={() => setStatusFilter(st)}
              >
                {st === 'PENDING_REVIEW' ? 'Pending Review' : st === 'ALL' ? 'All' : st}
              </button>
            ))}
          </div>
        </div>
        {drafts.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', padding: '12px 0' }}>
            No submission drafts in this view. Drop a PDF, Excel, or YELT/ELT file above to create one.
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Source File</th>
                  <th>Door</th>
                  <th>Type</th>
                  <th>Cedent / Programme</th>
                  <th className="num">Layers</th>
                  <th className="num">Confidence</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {drafts.map((d) => (
                  <tr
                    key={d.draftId}
                    onClick={() => setSelectedDraftId(d.draftId)}
                    style={{
                      cursor: 'pointer',
                      background: selectedDraft?.draftId === d.draftId ? 'var(--accent-bg)' : undefined,
                    }}
                  >
                    <td>
                      <div style={{ fontWeight: 600 }}>{d.sourceFilename}</div>
                      {d.simSetId && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>{d.simSetId}</div>}
                    </td>
                    <td>{DOOR_BADGE[d.door] ?? d.door}</td>
                    <td>{d.sourceFileType}</td>
                    <td>
                      <div>{d.extracted.cedentName || '—'}</div>
                      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                        {d.extracted.programmeName || d.targetProgrammeLabel || 'New draft programme'}
                      </div>
                    </td>
                    <td className="num">{d.extracted.layers.length}</td>
                    <td className="num">{Math.round(d.overallConfidence * 100)}%</td>
                    <td>{d.status}</td>
                    <td>
                      {d.confirmedProgrammeId && onOpenProgramme ? (
                        <button
                          type="button"
                          className="btn secondary"
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenProgramme(d.confirmedProgrammeId!);
                          }}
                        >
                          Open in Studio
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn secondary"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedDraftId(d.draftId);
                          }}
                        >
                          Review
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
