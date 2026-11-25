import type { RunManifest } from '../types/contracts';

export function BreadcrumbBar({ manifest }: { manifest: RunManifest | null }) {
  if (!manifest) return null;
  return (
    <div className="breadcrumb-bar">
      <span style={{ color: 'var(--teal)', fontWeight: 600 }}>BREADCRUMB</span>
      <span className="manifest-pill">{manifest.manifestId}</span>
      {manifest.breadcrumb.map((b, i) => (
        <span key={`${b.entityId}-${i}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: 'var(--text-faint)' }}>›</span>
          <span className="crumb">
            <strong style={{ color: 'var(--accent-soft)' }}>{b.entityType}</strong>: {b.label}
            {b.version ? ` (${b.version})` : ''}
            {b.contentHash ? ` #${b.contentHash.slice(0, 8)}` : ''}
          </span>
        </span>
      ))}
      <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
        out #{manifest.outputHash.slice(0, 10)} · {manifest.engineVersion}
      </span>
    </div>
  );
}
