import { isOffRiskStatus } from '../utils/optimizerStatuses';

/** A deal's status, coloured like the CMS ledger. Declined, NTU and expired deals read as not on risk. */
export function DealStatusBadge({ status }: { status: string }) {
  if (!status) return null;
  return (
    <span
      className={`badge status-${status.toLowerCase()}`}
      title={isOffRiskStatus(status) ? `${status}: this deal is not on risk` : undefined}
    >
      {status}
    </span>
  );
}

/** Status cell for an optimizer row: the deal's status, or a marker for the candidate retro hedge. */
export function LayerStatusBadge({ status, direction }: { status: string; direction: string }) {
  if (direction === 'EXTERNAL_RETRO') return <span className="badge">HEDGE CANDIDATE</span>;
  return <DealStatusBadge status={status} />;
}
