import type { OptimizerCandidatesPage, OptimizerLayerCatalogItem } from '../types/contracts';
import { fmtInt, fmtMoneyM, fmtRatioPct } from '../utils/format';
import {
  clampSharePct,
  computeLayerCapital1xM,
  normalizeLayerBound,
  type RowConflictHighlight,
} from '../utils/optimizerConstraints';
import { isOffRiskStatus } from '../utils/optimizerStatuses';
import { LayerStatusBadge } from './DealStatusBadge';

export interface LayerBoundState {
  minPct: number;
  maxPct: number;
  locked: boolean;
}

export function defaultLayerBound(layer: OptimizerLayerCatalogItem): LayerBoundState {
  const minPct = clampSharePct(layer.defaultMinShare * 100);
  const maxPct = Math.max(minPct, clampSharePct(Math.min(1, layer.defaultMaxShare) * 100));
  return {
    minPct,
    maxPct,
    locked: false,
  };
}

/**
 * Rows the table lists for a page. The retro hedge candidate is a decision variable, not a deal, so a
 * deal search leaves it out.
 */
export function listedLayers(page: OptimizerCandidatesPage | undefined): OptimizerLayerCatalogItem[] {
  if (!page) return [];
  return [...page.layers, ...(page.retroCandidate && !page.search ? [page.retroCandidate] : [])];
}

/** Longest search the candidates endpoint accepts. */
const MAX_SEARCH_LENGTH = 120;

function emptyMessage(page: OptimizerCandidatesPage): string {
  if (page.matchedProgrammes > 0 && page.programmeIds.length === 0) {
    return 'This page is past the last programme. Go back a page.';
  }
  if (page.matchedProgrammes > 0) return 'The programmes on this page have no layer that rolls up into a portfolio.';
  if (page.search) return `No deal in the selected statuses matches “${page.search}”.`;
  return 'No deal has a selected status. Tick another status above.';
}

interface OptimizerCandidatesTableProps {
  page: OptimizerCandidatesPage | undefined;
  /** True while `page` is the previous answer, shown until the one for the current filter arrives. */
  isPreviousPage: boolean;
  /** Offset and page size of the request in flight or shown, so paging never starts from a stale page. */
  offset: number;
  pageSize: number;
  isFetching: boolean;
  error: Error | null;
  onRetry: () => void;
  search: string;
  onSearch: (value: string) => void;
  onPage: (offset: number) => void;
  bounds: Record<string, LayerBoundState>;
  onBoundChange: (layerKey: string, next: LayerBoundState) => void;
  rowConflicts?: Record<string, RowConflictHighlight>;
}

/** The deals in the portfolio, one page of programmes at a time, with each layer's participation bounds. */
export function OptimizerCandidatesTable({
  page,
  isPreviousPage,
  offset,
  pageSize,
  isFetching,
  error,
  onRetry,
  search,
  onSearch,
  onPage,
  bounds,
  onBoundChange,
  rowConflicts = {},
}: OptimizerCandidatesTableProps) {
  const rows = listedLayers(page);
  const shown = page?.programmeIds.length ?? 0;
  const matched = page?.matchedProgrammes ?? 0;
  const firstShown = page?.offset ?? 0;
  const canPage = !!page && !isPreviousPage;

  return (
    <>
      <div className="candidates-toolbar">
        <input
          type="search"
          aria-label="Search deals"
          placeholder="Search cedent, programme, broker or id"
          maxLength={MAX_SEARCH_LENGTH}
          value={search}
          onChange={(e) => onSearch(e.target.value)}
        />
        <span className="candidates-range" aria-live="polite">
          {!page
            ? error
              ? 'Deals could not be loaded'
              : 'Loading deals…'
            : matched === 0
              ? 'No programmes'
              : shown === 0
                ? `No programmes on this page · ${fmtInt(matched)} in total`
                : `Programmes ${fmtInt(firstShown + 1)}–${fmtInt(firstShown + shown)} of ${fmtInt(matched)} · ${fmtInt(page.layers.length)} ${page.layers.length === 1 ? 'layer' : 'layers'}`}
          {isFetching && page ? ' · updating…' : ''}
        </span>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => onPage(Math.max(0, offset - pageSize))}
          disabled={!canPage || offset === 0}
        >
          Previous
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => onPage(offset + pageSize)}
          disabled={!canPage || offset + pageSize >= matched}
        >
          Next
        </button>
      </div>

      {error && (
        <div className="error-banner candidates-error">
          <span>{error.message}</span>
          <button type="button" className="btn btn-ghost" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Layer &amp; deal status</th>
              <th className="num">Cur %</th>
              <th className="num">Min %</th>
              <th className="num">Max %</th>
              <th className="num">Lock</th>
            </tr>
          </thead>
          <tbody>
            {page && page.layers.length === 0 && (
              <tr>
                <td colSpan={5} className="empty-cell">
                  {emptyMessage(page)}
                </td>
              </tr>
            )}
            {rows.map((l) => {
              const b = bounds[l.layerKey] ?? defaultLayerBound(l);
              const cur = clampSharePct(l.currentShare * 100);
              const cap1x = computeLayerCapital1xM(l);
              const conflict = rowConflicts[l.layerKey];
              const rowClasses = [
                isOffRiskStatus(l.status) ? 'row-off-risk' : '',
                conflict ? (conflict.severity === 'ERROR' ? 'row-conflict-error' : 'row-conflict-warn') : '',
              ]
                .filter(Boolean)
                .join(' ');
              return (
                <tr
                  key={l.layerKey}
                  className={rowClasses || undefined}
                  data-conflict={conflict ? conflict.severity : undefined}
                  title={conflict ? conflict.reasons.join(' ') : undefined}
                >
                  <td className="deal-cell">
                    <div className="deal-name">
                      {l.cedentName}
                      <LayerStatusBadge status={l.status} direction={l.direction} />
                    </div>
                    <div className="deal-sub">
                      {l.layerName} · {fmtMoneyM(l.limit1xM, 0)} lim · {fmtMoneyM(cap1x, 0)} cap · EL{' '}
                      {fmtMoneyM(l.el1xM, 2)}
                    </div>
                    {conflict && (
                      <div className={`conflict-pill ${conflict.severity === 'ERROR' ? 'error' : 'warning'}`} role="status">
                        {conflict.badge}
                      </div>
                    )}
                  </td>
                  <td className="num">{fmtRatioPct(l.currentShare, 0)}</td>
                  <td className="num">
                    <input
                      type="number"
                      aria-label={`Minimum share % for ${l.cedentName} ${l.layerName}`}
                      min={0}
                      max={100}
                      step="any"
                      disabled={b.locked}
                      value={b.minPct}
                      onChange={(e) =>
                        onBoundChange(l.layerKey, { ...b, minPct: clampSharePct(Number(e.target.value)) })
                      }
                      onBlur={() => onBoundChange(l.layerKey, normalizeLayerBound(b, 'min'))}
                      className={`bound-input${b.minPct > 0 ? ' raised' : ''}`}
                    />
                  </td>
                  <td className="num">
                    <input
                      type="number"
                      aria-label={`Maximum share % for ${l.cedentName} ${l.layerName}`}
                      min={0}
                      max={100}
                      step="any"
                      disabled={b.locked}
                      value={b.maxPct}
                      onChange={(e) =>
                        onBoundChange(l.layerKey, { ...b, maxPct: clampSharePct(Number(e.target.value)) })
                      }
                      onBlur={() => onBoundChange(l.layerKey, normalizeLayerBound(b, 'max'))}
                      className="bound-input"
                    />
                  </td>
                  <td className="num">
                    <input
                      type="checkbox"
                      aria-label={`Lock ${l.cedentName} ${l.layerName} at its current share`}
                      checked={b.locked}
                      onChange={(e) =>
                        onBoundChange(l.layerKey, {
                          minPct: e.target.checked ? cur : b.minPct,
                          maxPct: e.target.checked ? cur : b.maxPct,
                          locked: e.target.checked,
                        })
                      }
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
