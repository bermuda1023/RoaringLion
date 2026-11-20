import type { RunManifest } from '../types/contracts';

export function BreadcrumbBar({ manifest }: { manifest: RunManifest | null }) {
  if (!manifest) return null;
  return (
    <div className="breadcrumb-bar">
      <span style={{ color: '#5eead4', fontWeight: 600 }}>BREADCRUMB</span>
      <span className="manifest-pill">{manifest.manifestId}</span>
      {manifest.breadcrumb.map((b, i) => (
        <span key={`${b.entityId}-${i}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: '#64748b' }}>›</span>
          <span className="crumb">
            <strong style={{ color: '#93c5fd' }}>{b.entityType}</strong>: {b.label}
            {b.version ? ` (${b.version})` : ''}
            {b.contentHash ? ` #${b.contentHash.slice(0, 8)}` : ''}
          </span>
        </span>
      ))}
      <span style={{ marginLeft: 'auto', color: '#94a3b8', fontFamily: 'monospace' }}>
        out #{manifest.outputHash.slice(0, 10)} · {manifest.engineVersion}
      </span>
    </div>
  );
}
