import { useMemo, useState } from 'react';
import { LayerStatusBadge } from './DealStatusBadge';
import type {
  DealStabilityBucket,
  DealStabilityRow,
  DealStabilitySummary,
  StabilityBucketThemes,
  StabilityThemeItem,
} from '../types/contracts';
import { fmtMoneyM, fmtPct, fmtRatioPct } from '../utils/format';
import { isOffRiskStatus } from '../utils/optimizerStatuses';

type BucketFilter = 'ALL' | DealStabilityBucket;

const BUCKET_META: Record<
  DealStabilityBucket,
  { label: string; shortLabel: string; color: string; bg: string; border: string }
> = {
  CONSISTENT_GROW: {
    label: 'Always Grow',
    shortLabel: 'Grow',
    color: 'var(--positive)',
    bg: 'rgba(34, 197, 94, 0.12)',
    border: 'rgba(34, 197, 94, 0.35)',
  },
  CONSISTENT_SHRINK: {
    label: 'Shrink / Drop',
    shortLabel: 'Shrink',
    color: 'var(--negative)',
    bg: 'rgba(239, 68, 68, 0.12)',
    border: 'rgba(239, 68, 68, 0.35)',
  },
  UNSTABLE: {
    label: 'Unstable',
    shortLabel: 'Unstable',
    color: 'var(--warning)',
    bg: 'rgba(245, 158, 11, 0.12)',
    border: 'rgba(245, 158, 11, 0.35)',
  },
};

function ThemeDimensionRow({ label, items }: { label: string; items: StabilityThemeItem[] }) {
  if (items.length === 0) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap', fontSize: 11 }}>
      <span style={{ color: 'var(--text-faint)', fontWeight: 600, minWidth: 50 }}>{label}:</span>
      {items.slice(0, 4).map((it) => (
        <span
          key={`${it.dimension}-${it.key}`}
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--border-strong)',
            borderRadius: 4,
            padding: '1px 6px',
            fontSize: 11,
            whiteSpace: 'nowrap',
          }}
          title={`${it.key}: ${it.dealCount} deal${it.dealCount === 1 ? '' : 's'} · Cap ${fmtMoneyM(it.deployedCapitalM, 2)} (${fmtPct(it.shareOfDeployedCapitalPct, 1)}) · Exp Ret ${fmtMoneyM(it.expectedReturnM, 2)} (${fmtPct(it.shareOfExpectedReturnPct, 1)})`}
        >
          <strong>{it.key}</strong>{' '}
          <span style={{ color: 'var(--text-muted)' }}>
            ({it.dealCount} · {fmtPct(it.shareOfDeployedCapitalPct, 1)} cap · {fmtPct(it.shareOfExpectedReturnPct, 1)} ret)
          </span>
        </span>
      ))}
    </div>
  );
}

function ThemeCard({
  title,
  subtitle,
  accentColor,
  themes,
  testId,
}: {
  title: string;
  subtitle: string;
  accentColor: string;
  themes: StabilityBucketThemes;
  testId: string;
}) {
  return (
    <div
      data-testid={testId}
      style={{
        background: 'var(--surface-sunken)',
        border: '1px solid var(--border-strong)',
        borderRadius: 6,
        padding: '8px 10px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600, fontSize: 12, color: accentColor }}>
          {title} ({themes.dealCount})
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {subtitle}: <strong>{fmtMoneyM(themes.deployedCapitalM, 1)}</strong> ({fmtPct(themes.shareOfDeployedCapitalPct, 1)}) · Ret{' '}
          <strong>{fmtMoneyM(themes.expectedReturnM, 2)}</strong> ({fmtPct(themes.shareOfExpectedReturnPct, 1)})
        </span>
      </div>
      {themes.dealCount === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--text-faint)' }}>No deals in this bucket across the frontier.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <ThemeDimensionRow label="Perils" items={themes.topPerils} />
          <ThemeDimensionRow label="Regions" items={themes.topRegions} />
          <ThemeDimensionRow label="Cedents" items={themes.topCedents} />
        </div>
      )}
    </div>
  );
}

function RangeBar({ row }: { row: DealStabilityRow }) {
  const minPct = Math.max(0, Math.min(100, row.minShare * 100));
  const maxPct = Math.max(minPct, Math.min(100, row.maxShare * 100));
  const fillWidth = Math.max(2, maxPct - minPct);
  const fillLeft = Math.min(minPct, 100 - fillWidth);
  const medPct = Math.max(0, Math.min(100, row.medianShare * 100));
  const curPct = Math.max(0, Math.min(100, row.currentShare * 100));
  const meta = BUCKET_META[row.classification];

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 150 }}>
      <div
        style={{
          position: 'relative',
          flex: 1,
          height: 10,
          background: 'var(--surface-sunken)',
          border: '1px solid var(--border-strong)',
          borderRadius: 4,
          minWidth: 68,
        }}
        title={`Current: ${curPct.toFixed(1)}% · Min: ${minPct.toFixed(1)}% · Median: ${medPct.toFixed(1)}% · Max: ${maxPct.toFixed(1)}% (Bounds ${(row.boundMinShare * 100).toFixed(0)}–${(row.boundMaxShare * 100).toFixed(0)}%)`}
      >
        <div
          data-testid={`range-fill-${row.layerKey}`}
          style={{
            position: 'absolute',
            left: `${fillLeft}%`,
            width: `${fillWidth}%`,
            top: 1,
            bottom: 1,
            borderRadius: 2,
            background: meta.color,
            opacity: 0.45,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: `calc(${curPct}% - 1px)`,
            top: -1,
            bottom: -1,
            width: 2,
            background: 'var(--text-muted)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: `calc(${medPct}% - 2px)`,
            top: 0,
            bottom: 0,
            width: 4,
            borderRadius: 2,
            background: meta.color,
          }}
        />
      </div>
      <span style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap', minWidth: 66, textAlign: 'right' }}>
        {minPct.toFixed(0)}–{maxPct.toFixed(0)}%
      </span>
    </div>
  );
}

export function OptimizerStabilityPanel({ stability }: { stability?: DealStabilitySummary | null }) {
  const [filter, setFilter] = useState<BucketFilter>('ALL');

  const visibleDeals = useMemo(() => {
    if (!stability) return [];
    if (filter === 'ALL') return stability.deals;
    return stability.deals.filter((d) => d.classification === filter);
  }, [stability, filter]);

  if (!stability) return null;

  const th = stability.thresholds;

  return (
    <div className="panel" style={{ marginBottom: 14 }} data-testid="optimizer-stability-panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 8,
          marginBottom: 10,
        }}
      >
        <div>
          <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent-soft)' }}>
            Deal Stability Across Frontier ({stability.feasiblePointCount}/{stability.totalPointCount} feasible points)
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            Derived from LP/MILP participation vectors · Rule: ≥{fmtRatioPct(th.consensusFraction, 0)} frontier consensus within ±
            {fmtRatioPct(th.boundTolerance, 0)} of bound or current line, IQR &lt; {fmtRatioPct(th.dispersionIqrCutoff, 0)} &amp; σ &lt;{' '}
            {fmtRatioPct(th.dispersionStdCutoff, 0)}
          </div>
        </div>

        <div className="seg" role="group" aria-label="Filter deals by stability bucket">
          <button type="button" className={filter === 'ALL' ? 'active' : ''} onClick={() => setFilter('ALL')}>
            All ({stability.deals.length})
          </button>
          <button
            type="button"
            className={filter === 'CONSISTENT_GROW' ? 'active' : ''}
            onClick={() => setFilter('CONSISTENT_GROW')}
          >
            Always Grow ({stability.consistentGrowCount})
          </button>
          <button
            type="button"
            className={filter === 'CONSISTENT_SHRINK' ? 'active' : ''}
            onClick={() => setFilter('CONSISTENT_SHRINK')}
          >
            Shrink / Drop ({stability.consistentShrinkCount})
          </button>
          <button
            type="button"
            className={filter === 'UNSTABLE' ? 'active' : ''}
            onClick={() => setFilter('UNSTABLE')}
          >
            Unstable ({stability.unstableCount})
          </button>
        </div>
      </div>

      {stability.feasiblePointCount === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-faint)', padding: '6px 0' }}>
          No feasible frontier points available to classify deal stability.
        </div>
      ) : (
        <>
          <div className="grid-2" style={{ marginBottom: 10 }}>
            <ThemeCard
              title="Always Grow Themes"
              subtitle="Frontier mean cap"
              accentColor="var(--positive)"
              themes={stability.growThemes}
              testId="stability-grow-themes"
            />
            <ThemeCard
              title="Always Shrink / Drop Themes"
              subtitle="Current book cap"
              accentColor="var(--negative)"
              themes={stability.shrinkThemes}
              testId="stability-shrink-themes"
            />
          </div>

          <div className="table-wrap" style={{ maxHeight: 340, overflowY: 'auto' }}>
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Deal / Layer</th>
                  <th>Bucket</th>
                  <th className="num">Current</th>
                  <th className="num">Median (Δ)</th>
                  <th>Min – Max Across Frontier</th>
                  <th className="num">Consensus / IQR</th>
                  <th className="num">Stability</th>
                </tr>
              </thead>
              <tbody>
                {visibleDeals.map((r) => {
                  const meta = BUCKET_META[r.classification];
                  const deltaPct = r.medianDeltaShare * 100;
                  const supportPct =
                    r.classification === 'CONSISTENT_GROW'
                      ? r.growSupport * 100
                      : r.classification === 'CONSISTENT_SHRINK'
                        ? r.shrinkSupport * 100
                        : Math.max(r.growSupport, r.shrinkSupport) * 100;
                  return (
                    <tr key={r.layerKey} className={isOffRiskStatus(r.status) ? 'row-off-risk' : undefined}>
                      <td style={{ whiteSpace: 'normal', minWidth: 165 }}>
                        <div className="deal-name">
                          {r.cedentName}
                          <LayerStatusBadge status={r.status} direction={r.direction} />
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                          {r.layerName}
                          {r.regionPerils.length > 0 ? ` · ${r.regionPerils.slice(0, 3).join(', ')}` : ''}
                        </div>
                      </td>
                      <td>
                        <span
                          style={{
                            display: 'inline-block',
                            fontSize: 10,
                            fontWeight: 600,
                            padding: '2px 6px',
                            borderRadius: 4,
                            color: meta.color,
                            background: meta.bg,
                            border: `1px solid ${meta.border}`,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {r.locked ? 'Locked' : meta.label}
                        </span>
                      </td>
                      <td className="num">{fmtRatioPct(r.currentShare, 1)}</td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {fmtRatioPct(r.medianShare, 1)}{' '}
                        <span
                          style={{
                            fontSize: 10,
                            color:
                              Math.abs(deltaPct) < 0.05
                                ? 'var(--text-faint)'
                                : deltaPct > 0
                                  ? 'var(--positive)'
                                  : 'var(--negative)',
                          }}
                        >
                          ({deltaPct >= 0 ? '+' : ''}
                          {deltaPct.toFixed(1)}%)
                        </span>
                      </td>
                      <td>
                        <RangeBar row={r} />
                      </td>
                      <td className="num" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        {r.locked ? 'Fixed' : `${supportPct.toFixed(0)}% · IQR ${(r.iqrShare * 100).toFixed(0)}%`}
                      </td>
                      <td className="num" style={{ fontWeight: 700, color: meta.color }}>
                        {fmtRatioPct(r.stabilityScore, 0)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
