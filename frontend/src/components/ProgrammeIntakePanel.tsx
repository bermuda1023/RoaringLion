import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { intakeApi, type SubmissionConfirmResponse, type SubmissionDraftOut } from '../api/intake';
import { SubmissionDropZone } from './SubmissionDropZone';
import { SubmissionReviewCard } from './SubmissionReviewCard';

export function ProgrammeIntakePanel({
  programmeId,
  programmeLabel,
  watchedFolder,
  onProgrammeUpdated,
}: {
  programmeId: string;
  programmeLabel: string;
  watchedFolder?: string | null;
  onProgrammeUpdated?: () => void;
}) {
  const qc = useQueryClient();
  const [folderInput, setFolderInput] = useState(watchedFolder ?? '');
  const [folderMsg, setFolderMsg] = useState<string | null>(null);
  const [folderBusy, setFolderBusy] = useState(false);
  const [activeDraft, setActiveDraft] = useState<SubmissionDraftOut | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    setFolderInput(watchedFolder ?? '');
    setFolderMsg(null);
    setActiveDraft(null);
  }, [programmeId, watchedFolder]);

  const draftsQ = useQuery({
    queryKey: ['intakeDrafts', 'programme', programmeId],
    queryFn: () => intakeApi.listDrafts({ programmeId }),
    enabled: Boolean(programmeId),
  });

  const pendingDrafts = (draftsQ.data ?? []).filter((d) => d.status === 'PENDING_REVIEW');
  const displayedDraft = activeDraft ?? pendingDrafts[0] ?? null;

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['intakeDrafts'] });
    qc.invalidateQueries({ queryKey: ['intakeStatus'] });
    qc.invalidateQueries({ queryKey: ['contracts'] });
    qc.invalidateQueries({ queryKey: ['programmes'] });
    qc.invalidateQueries({ queryKey: ['contractDossier', programmeId] });
    qc.invalidateQueries({ queryKey: ['health'] });
    onProgrammeUpdated?.();
  };

  const handleSaveFolder = async () => {
    setFolderBusy(true);
    setFolderMsg(null);
    try {
      const updated = await intakeApi.setWatchedFolder(programmeId, folderInput.trim() || null);
      setFolderMsg(
        updated.watchedFolder
          ? `Watched folder saved: ${updated.watchedFolder}`
          : 'Watched folder cleared.',
      );
      invalidateAll();
    } catch (err) {
      setFolderMsg(err instanceof Error ? err.message : 'Failed to save watched folder.');
    } finally {
      setFolderBusy(false);
    }
  };

  const handleScanFolder = async () => {
    setFolderBusy(true);
    setFolderMsg(null);
    try {
      const res = await intakeApi.scanFolders(programmeId);
      setFolderMsg(
        res.pickedUpCount > 0
          ? `Picked up ${res.pickedUpCount} new file(s) into the draft review queue.`
          : `No new files found (${res.skippedCount} already processed).`,
      );
      if (res.drafts.length > 0) setActiveDraft(res.drafts[0]);
      invalidateAll();
    } catch (err) {
      setFolderMsg(err instanceof Error ? err.message : 'Folder scan failed.');
    } finally {
      setFolderBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <SubmissionDropZone
        compact
        programmeId={programmeId}
        programmeLabel={programmeLabel}
        onDraftCreated={(draft) => {
          setActiveDraft(draft);
          setBanner(null);
          qc.invalidateQueries({ queryKey: ['intakeDrafts'] });
          qc.invalidateQueries({ queryKey: ['intakeStatus'] });
        }}
      />

      <div className="card" style={{ margin: 0 }}>
        <div className="card-title">
          <span>Watched Folder (Door 3)</span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            aria-label="Watched folder path"
            placeholder="e.g. farmers-2027"
            value={folderInput}
            onChange={(e) => setFolderInput(e.target.value)}
            style={{ minWidth: 220 }}
          />
          <button type="button" className="btn secondary" disabled={folderBusy} onClick={() => void handleSaveFolder()}>
            Save Folder
          </button>
          <button
            type="button"
            className="btn"
            disabled={folderBusy || !folderInput.trim()}
            onClick={() => void handleScanFolder()}
          >
            Scan Folder Now
          </button>
        </div>
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 6 }}>
          Relative folder under the configured submissions root. New PDF, Excel, or YELT/ELT files are queued as drafts for this programme.
        </div>
        {folderMsg && (
          <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--accent-soft)' }}>{folderMsg}</div>
        )}
      </div>

      {banner && (
        <div
          style={{
            padding: '10px 14px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--positive-bg)',
            color: 'var(--positive)',
            fontSize: 'var(--fs-sm)',
          }}
        >
          {banner}
        </div>
      )}

      {displayedDraft && (
        <SubmissionReviewCard
          draft={displayedDraft}
          onConfirmed={(res: SubmissionConfirmResponse) => {
            setBanner(
              res.action === 'CREATED_DRAFT_PROGRAMME'
                ? `Confirmed: created draft programme ${res.contract.programmeName} (${res.contract.status}).`
                : `Confirmed: attached submission to ${res.contract.programmeName} (status remains ${res.contract.status}).`,
            );
            setActiveDraft(null);
            invalidateAll();
          }}
          onDiscarded={() => {
            setBanner('Draft discarded — CMS was not modified.');
            setActiveDraft(null);
            invalidateAll();
          }}
        />
      )}
    </div>
  );
}
