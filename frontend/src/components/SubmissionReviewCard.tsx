import { useEffect, useState } from 'react';
import {
  intakeApi,
  type IntakeExtractedData,
  type IntakeLayerDraft,
  type SubmissionConfirmResponse,
  type SubmissionDraftOut,
} from '../api/intake';

const DOOR_LABEL: Record<string, string> = {
  DROP_IN: 'Drop-in',
  EMAIL: 'Email',
  FOLDER: 'Watched Folder',
};

function confidenceTone(score: number): { bg: string; color: string; label: string } {
  if (score >= 0.85) return { bg: 'var(--positive-bg)', color: 'var(--positive)', label: 'High' };
  if (score >= 0.6) return { bg: 'var(--warning-bg)', color: 'var(--warning)', label: 'Medium' };
  return { bg: 'var(--negative-bg)', color: 'var(--negative)', label: 'Review' };
}

export function SubmissionReviewCard({
  draft,
  onConfirmed,
  onDiscarded,
}: {
  draft: SubmissionDraftOut;
  onConfirmed: (res: SubmissionConfirmResponse) => void;
  onDiscarded: (draft: SubmissionDraftOut) => void;
}) {
  const [extracted, setExtracted] = useState<IntakeExtractedData>(draft.extracted);
  const [confirmMode, setConfirmMode] = useState<'CREATE_NEW' | 'ATTACH_EXISTING'>(
    draft.targetProgrammeId ? 'ATTACH_EXISTING' : 'CREATE_NEW',
  );
  const [busy, setBusy] = useState<'confirm' | 'discard' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setExtracted(draft.extracted);
    setConfirmMode(draft.targetProgrammeId ? 'ATTACH_EXISTING' : 'CREATE_NEW');
    setError(null);
  }, [draft.draftId]);

  const tone = confidenceTone(draft.overallConfidence);
  const isPending = draft.status === 'PENDING_REVIEW';

  const updateLayer = (idx: number, patch: Partial<IntakeLayerDraft>) => {
    setExtracted((prev) => {
      const next = [...prev.layers];
      next[idx] = { ...next[idx], ...patch };
      return { ...prev, layers: next };
    });
  };

  const handleConfirm = async () => {
    setBusy('confirm');
    setError(null);
    try {
      const res = await intakeApi.confirmDraft(draft.draftId, {
        mode: confirmMode,
        targetProgrammeId: confirmMode === 'ATTACH_EXISTING' ? draft.targetProgrammeId : null,
        extracted,
      });
      onConfirmed(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to confirm submission draft.');
    } finally {
      setBusy(null);
    }
  };

  const handleDiscard = async () => {
    setBusy('discard');
    setError(null);
    try {
      const res = await intakeApi.discardDraft(draft.draftId);
      onDiscarded(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to discard submission draft.');
    } finally {
      setBusy(null);
    }
  };

  const graphLayerCount = draft.graph.nodes.filter((n) => n.nodeType === 'OCC_XL' || n.nodeType === 'AGG_XL').length;
  const graphPricingCount = draft.graph.nodes.filter((n) => n.nodeType === 'PRICING_OUTPUT').length;

  return (
    <div
      className="card"
      data-testid="submission-review-card"
      style={{ borderColor: isPending ? 'var(--accent-strong)' : 'var(--border)' }}
    >
      <div className="card-title" style={{ flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span>Review Draft · {draft.sourceFilename}</span>
          <span className="pill">{DOOR_LABEL[draft.door] ?? draft.door}</span>
          <span className="pill">{draft.sourceFileType}</span>
          <span
            className="pill"
            data-testid="draft-confidence-badge"
            style={{ background: tone.bg, color: tone.color, borderColor: 'transparent' }}
          >
            Confidence {Math.round(draft.overallConfidence * 100)}% ({tone.label})
          </span>
          <span className="pill">{draft.status}</span>
        </div>
        {isPending && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              type="button"
              className="btn secondary"
              data-testid="discard-draft-btn"
              disabled={busy !== null}
              onClick={() => void handleDiscard()}
            >
              {busy === 'discard' ? 'Discarding…' : 'Discard'}
            </button>
            <button
              type="button"
              className="btn"
              data-testid="confirm-draft-btn"
              disabled={busy !== null}
              onClick={() => void handleConfirm()}
            >
              {busy === 'confirm'
                ? 'Writing Draft…'
                : confirmMode === 'ATTACH_EXISTING' && draft.targetProgrammeId
                  ? 'Confirm & Attach to Programme'
                  : 'Confirm Draft into CMS'}
            </button>
          </div>
        )}
      </div>

      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 12 }}>
        Source: <code>{draft.sourceRef || draft.sourceFilename}</code>
        {draft.simSetId && (
          <>
            {' · '}Loss source: <code>{draft.simSetId}</code>
            {draft.simSetName ? ` (${draft.simSetName})` : ''}
          </>
        )}
        {' · '}Sketched graph: {draft.graph.nodes.length} nodes ({graphLayerCount} layer{graphLayerCount === 1 ? '' : 's'}, {graphPricingCount} pricing output{graphPricingCount === 1 ? '' : 's'}), {draft.graph.edges.length} edges.
      </div>

      {draft.targetProgrammeId && isPending && (
        <div
          style={{
            display: 'flex',
            gap: 12,
            alignItems: 'center',
            flexWrap: 'wrap',
            padding: '8px 12px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--surface-2)',
            marginBottom: 12,
            fontSize: 'var(--fs-sm)',
          }}
        >
          <span>
            Matched programme: <strong>{draft.targetProgrammeLabel || draft.targetProgrammeId}</strong>
          </span>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
            <input
              type="radio"
              name={`mode-${draft.draftId}`}
              checked={confirmMode === 'ATTACH_EXISTING'}
              onChange={() => setConfirmMode('ATTACH_EXISTING')}
            />
            Attach to matched programme (status unchanged)
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
            <input
              type="radio"
              name={`mode-${draft.draftId}`}
              checked={confirmMode === 'CREATE_NEW'}
              onChange={() => setConfirmMode('CREATE_NEW')}
            />
            Create new SUBMISSION draft programme
          </label>
        </div>
      )}

      {extracted.warnings.length > 0 && (
        <div
          style={{
            padding: '8px 12px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--warning-bg)',
            color: 'var(--warning)',
            fontSize: 'var(--fs-xs)',
            marginBottom: 12,
          }}
        >
          {extracted.warnings.map((w, i) => (
            <div key={i}>• {w}</div>
          ))}
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
          gap: 10,
          marginBottom: 14,
        }}
      >
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
          Cedent
          <input
            type="text"
            aria-label="Cedent"
            disabled={!isPending}
            value={extracted.cedentName}
            onChange={(e) => setExtracted((p) => ({ ...p, cedentName: e.target.value }))}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
          Programme Name
          <input
            type="text"
            aria-label="Programme Name"
            disabled={!isPending}
            value={extracted.programmeName}
            onChange={(e) => setExtracted((p) => ({ ...p, programmeName: e.target.value }))}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
          Inception Date
          <input
            type="text"
            aria-label="Inception Date"
            disabled={!isPending}
            value={extracted.inceptionDate ?? ''}
            onChange={(e) => setExtracted((p) => ({ ...p, inceptionDate: e.target.value || null }))}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
          Region / Peril
          <input
            type="text"
            aria-label="Region / Peril"
            disabled={!isPending}
            value={extracted.regionPerils.join(', ')}
            onChange={(e) =>
              setExtracted((p) => ({
                ...p,
                regionPerils: e.target.value
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean),
              }))
            }
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
          Broker
          <input
            type="text"
            aria-label="Broker"
            disabled={!isPending}
            value={extracted.broker}
            onChange={(e) => setExtracted((p) => ({ ...p, broker: e.target.value }))}
          />
        </label>
      </div>

      {extracted.layers.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-muted)', marginBottom: 6 }}>
            Extracted Layers ({extracted.layers.length})
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Type</th>
                  <th className="num">Limit ($M)</th>
                  <th className="num">Attachment ($M)</th>
                  <th className="num">Premium ($M)</th>
                  <th className="num">Line (%)</th>
                </tr>
              </thead>
              <tbody>
                {extracted.layers.map((lyr, idx) => (
                  <tr key={`${lyr.layerCode}-${idx}`}>
                    <td>{lyr.layerCode}</td>
                    <td>{lyr.layerType}</td>
                    <td className="num">
                      <input
                        type="number"
                        step="0.5"
                        disabled={!isPending}
                        value={lyr.limit}
                        style={{ width: 84, textAlign: 'right' }}
                        onChange={(e) => updateLayer(idx, { limit: Number(e.target.value) || 0 })}
                      />
                    </td>
                    <td className="num">
                      <input
                        type="number"
                        step="0.5"
                        disabled={!isPending}
                        value={lyr.attachment}
                        style={{ width: 84, textAlign: 'right' }}
                        onChange={(e) => updateLayer(idx, { attachment: Number(e.target.value) || 0 })}
                      />
                    </td>
                    <td className="num">
                      <input
                        type="number"
                        step="0.1"
                        disabled={!isPending}
                        value={lyr.depositPremium}
                        style={{ width: 84, textAlign: 'right' }}
                        onChange={(e) => updateLayer(idx, { depositPremium: Number(e.target.value) || 0 })}
                      />
                    </td>
                    <td className="num">
                      <input
                        type="number"
                        step="1"
                        min="0"
                        max="100"
                        disabled={!isPending}
                        value={Math.round(lyr.signedLine * 100)}
                        style={{ width: 70, textAlign: 'right' }}
                        onChange={(e) => {
                          const frac = Math.min(1, Math.max(0, (Number(e.target.value) || 0) / 100));
                          updateLayer(idx, { signedLine: frac, writtenLine: frac });
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {extracted.lossHistory.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-muted)', marginBottom: 6 }}>
            Extracted Loss History ({extracted.lossHistory.length})
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Year</th>
                  <th>Event</th>
                  <th className="num">Ground-Up Loss ($M)</th>
                  <th>Region / Peril</th>
                </tr>
              </thead>
              <tbody>
                {extracted.lossHistory.map((row, idx) => (
                  <tr key={idx}>
                    <td>{row.year ?? '—'}</td>
                    <td>{row.eventName}</td>
                    <td className="num">{row.groundUpLossM.toFixed(1)}</td>
                    <td>{row.regionPerilKey ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

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
