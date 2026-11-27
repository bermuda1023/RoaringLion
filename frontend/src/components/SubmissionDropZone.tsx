import { useRef, useState } from 'react';
import { intakeApi, type SubmissionDraftOut } from '../api/intake';

const ACCEPTED_EXTS = '.pdf,.xlsx,.xlsm,.csv,.parquet,.txt';

export function SubmissionDropZone({
  programmeId,
  programmeLabel,
  onDraftCreated,
  compact = false,
}: {
  programmeId?: string | null;
  programmeLabel?: string | null;
  onDraftCreated: (draft: SubmissionDraftOut) => void;
  compact?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFiles = async (files: FileList | File[] | null) => {
    if (!files || files.length === 0) return;
    setError(null);
    setBusy(true);
    try {
      for (const file of Array.from(files)) {
        const draft = await intakeApi.uploadDropIn(file, programmeId);
        onDraftCreated(draft);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission upload failed.');
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  return (
    <div>
      <div
        role="region"
        aria-label="Submission drop zone"
        data-testid="submission-drop-zone"
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void handleFiles(e.dataTransfer.files);
        }}
        onClick={() => !busy && inputRef.current?.click()}
        style={{
          border: `1.5px dashed ${dragOver ? 'var(--accent)' : 'var(--border-strong)'}`,
          background: dragOver ? 'var(--accent-bg)' : 'var(--surface-2)',
          borderRadius: 'var(--radius)',
          padding: compact ? '16px 18px' : '24px 22px',
          cursor: busy ? 'wait' : 'pointer',
          textAlign: 'center',
          transition: 'border-color 140ms ease, background 140ms ease',
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_EXTS}
          multiple
          data-testid="submission-file-input"
          aria-label="Upload submission file"
          style={{ display: 'none' }}
          onChange={(e) => void handleFiles(e.target.files)}
        />
        <div style={{ fontWeight: 600, color: 'var(--text-strong)', marginBottom: 4 }}>
          {busy
            ? 'Ingesting submission into draft queue…'
            : programmeLabel
              ? `Drop PDF, Excel, or YELT/ELT files for ${programmeLabel}`
              : 'Drop submission PDF, Excel schedule, or YELT/ELT CSV here'}
        </div>
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
          Accepts <code>.pdf</code>, <code>.xlsx</code>, <code>.csv</code> (YELT / ELT), <code>.parquet</code> · Draft only — nothing writes to CMS until you Confirm.
        </div>
      </div>
      {error && (
        <div
          role="alert"
          style={{
            marginTop: 8,
            padding: '8px 12px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--negative-bg)',
            color: 'var(--negative)',
            fontSize: 'var(--fs-sm)',
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}
