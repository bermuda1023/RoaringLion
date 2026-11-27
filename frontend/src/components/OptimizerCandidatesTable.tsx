import type { OptimizerCandidatesPage, OptimizerLayerCatalogItem } from '../types/contracts';
import { fmtInt, fmtMoneyM, fmtRatioPct } from '../utils/format';
import { isOffRiskStatus } from '../utils/optimizerStatuses';
import { LayerStatusBadge } from './DealStatusBadge';

export interface LayerBoundState {
  minPct: number;
  maxPct: number;
  locked: boolean;
}

export function defaultLayerBound(layer: OptimizerLayerCatalogItem): LayerBoundState {
  return {
    minPct: Math.round(layer.defaultMinShare * 100),
    maxPct: Math.round(layer.defaultMaxShare * 100),
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

function emptyMessage(page: OptimizerCandidatesPage): string {
  if (page.matchedProgrammes > 0) return 'The programmes on this page have no layer that rolls up into a portfolio.';
  if (page.search) return `No deal in the selected statuses matches “${page.search}”.`;
  return 'No deal has a selected status. Tick another status above.';
}

interface OptimizerCandidatesTableProps {
  page: OptimizerCandidatesPage | undefined;
  isFetching: boolean;
  error: Error | null;
  onRetry: () => void;
  search: string;
  onSearch: (value: string) => void;
  onPage: (offset: number) => void;
  bounds: Record<string, LayerBoundState>;
  onBoundChange: (layerKey: string, next: LayerBoundState) => void;
}

/** The deals in the portfolio, one page of programmes at a time, with each layer's participation bounds. */
export function OptimizerCandidatesTable({
  page,
  isFetching,
  error,
  onRetry,
  search,
  onSearch,
  onPage,
  bounds,
  onBoundChange,
}: OptimizerCandidatesTableProps) {
  const rows = listedLayers(page);
  const shown = page?.programmeIds.length ?? 0;
  const matched = page?.matchedProgrammes ?? 0;
  const offset = page?.offset ?? 0;
  const limit = page?.limit ?? 0;

  return (
    <>
      <div className="candidates-toolbar">
        <input
          type="search"
          aria-label="Search deals"
          placeholder="Search cedent, programme, broker or id"
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
              : `Programmes ${fmtInt(offset + 1)}–${fmtInt(offset + shown)} of ${fmtInt(matched)} · ${fmtInt(page.layers.length)} ${page.layers.length === 1 ? 'layer' : 'layers'}`}
          {isFetching && page ? ' · updating…' : ''}
        </span>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => onPage(Math.max(0, offset - limit))}
          disabled={!page || offset === 0}
        >
          Previous
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => onPage(offset + limit)}
          disabled={!page || offset + limit >= matched}
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
              const cur = Math.round(l.currentShare * 100);
              return (
                <tr key={l.layerKey} className={isOffRiskStatus(l.status) ? 'row-off-risk' : undefined}>
                  <td className="deal-cell">
                    <div className="deal-name">
                      {l.cedentName}
                      <LayerStatusBadge status={l.status} direction={l.direction} />
                    </div>
                    <div className="deal-sub">
                      {l.layerName} · {fmtMoneyM(l.limit1xM, 0)} lim · EL {fmtMoneyM(l.el1xM, 2)}
                    </div>
                  </td>
                  <td className="num">{fmtRatioPct(l.currentShare, 0)}</td>
                  <td className="num">
                    <input
                      type="number"
                      aria-label={`Minimum share % for ${l.cedentName} ${l.layerName}`}
                      min={0}
                      max={300}
                      disabled={b.locked}
                      value={b.minPct}
                      onChange={(e) => onBoundChange(l.layerKey, { ...b, minPct: Number(e.target.value) })}
                      className={`bound-input${b.minPct > 0 ? ' raised' : ''}`}
                    />
                  </td>
                  <td className="num">
                    <input
                      type="number"
                      aria-label={`Maximum share % for ${l.cedentName} ${l.layerName}`}
                      min={0}
                      max={300}
                      disabled={b.locked}
                      value={b.maxPct}
                      onChange={(e) => onBoundChange(l.layerKey, { ...b, maxPct: Number(e.target.value) })}
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
