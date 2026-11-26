import { PROGRAMME_STATUSES, type ProgrammeStatus } from '../types/contracts';
import { fmtInt } from '../utils/format';
import { isOffRiskStatus } from '../utils/optimizerStatuses';
import { DealStatusBadge } from './DealStatusBadge';

interface OptimizerStatusFilterProps {
  /** Name of the horizon mode the defaults belong to, e.g. "Forward-Looking Planning". */
  modeLabel: string;
  selected: readonly ProgrammeStatus[];
  /** True when `selected` differs from the mode's defaults. */
  isCustom: boolean;
  /** Programmes per status; undefined until the first response arrives. */
  counts?: Partial<Record<ProgrammeStatus, number>>;
  /** Programmes across all selected statuses. */
  universeProgrammes?: number;
  onToggle: (status: ProgrammeStatus) => void;
  onSelectAll: () => void;
  onReset: () => void;
}

/** Above this many programmes a run is slow and memory-heavy, so the filter says so before the user runs it. */
export const LARGE_PORTFOLIO_PROGRAMMES = 300;

function listStatuses(statuses: readonly string[]): string {
  if (statuses.length <= 1) return statuses.join('');
  return `${statuses.slice(0, -1).join(', ')} and ${statuses[statuses.length - 1]}`;
}

/** Chooses which deal statuses the optimizer builds its portfolio from. */
export function OptimizerStatusFilter({
  modeLabel,
  selected,
  isCustom,
  counts,
  universeProgrammes,
  onToggle,
  onSelectAll,
  onReset,
}: OptimizerStatusFilterProps) {
  const offRisk = selected.filter(isOffRiskStatus);
  return (
    <fieldset className="status-filter">
      <legend>Deal statuses in this portfolio</legend>
      <div className="status-filter-options">
        {PROGRAMME_STATUSES.map((status) => {
          const checked = selected.includes(status);
          const isLast = checked && selected.length === 1;
          return (
            <label
              key={status}
              className={`status-option${checked ? ' on' : ''}`}
              title={isLast ? 'At least one status must stay selected' : undefined}
            >
              <input type="checkbox" checked={checked} disabled={isLast} onChange={() => onToggle(status)} />
              <DealStatusBadge status={status} />
              <span className="status-option-count">{counts ? fmtInt(counts[status] ?? 0) : '…'}</span>
            </label>
          );
        })}
      </div>
      <div className="status-filter-foot">
        <span className="status-filter-summary">
          {isCustom ? 'Custom selection' : `Default for ${modeLabel}`}
          {universeProgrammes != null &&
            ` · ${fmtInt(universeProgrammes)} ${universeProgrammes === 1 ? 'programme' : 'programmes'}`}
        </span>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={onSelectAll}
          disabled={selected.length === PROGRAMME_STATUSES.length}
        >
          All statuses
        </button>
        <button type="button" className="btn btn-ghost" onClick={onReset} disabled={!isCustom}>
          Reset to default
        </button>
      </div>
      {offRisk.length > 0 && (
        <div className="warning-banner status-filter-note" role="note">
          {listStatuses(offRisk)} deals are not on risk. Each one enters this portfolio at its recorded signed
          line and is labelled with its status in every table.
        </div>
      )}
      {universeProgrammes != null && universeProgrammes > LARGE_PORTFOLIO_PROGRAMMES && (
        <div className="warning-banner status-filter-note" role="note">
          Every run solves all {fmtInt(universeProgrammes)} programmes in these statuses, which is slow and
          memory-heavy at this size. Untick statuses you do not need.
        </div>
      )}
    </fieldset>
  );
}
