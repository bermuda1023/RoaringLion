import type { GraphSpec } from '../types/contracts';
import { LABEL_META_KEYS, LAYER_META_KEYS, OUTPUT_LINK_KEYS } from './nodeLabels';

const NULLABLE_OPTIONAL_KEYS = new Set([
  'perEventCap',
  'eventCap',
  'aggCap',
  'maxOrder',
  'maxLoss',
  'reinstatements',
  'exhaustion',
]);

function cleanParamDict(params: Record<string, unknown>): Record<string, unknown> {
  const cleaned: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if (LABEL_META_KEYS.includes(k) || LAYER_META_KEYS.includes(k) || OUTPUT_LINK_KEYS.includes(k)) {
      cleaned[k] = v;
      continue;
    }
    if (k === 'layers' && Array.isArray(v)) {
      cleaned[k] = v.map((sub) =>
        sub && typeof sub === 'object' ? cleanParamDict(sub as Record<string, unknown>) : sub,
      );
      continue;
    }
    if (k === 'depositPremium') {
      if (v === null || v === undefined || (typeof v === 'string' && v.trim() === '')) {
        continue;
      }
      if (typeof v === 'string' && !Number.isNaN(Number(v))) {
        cleaned[k] = Number(v);
        continue;
      }
      cleaned[k] = v;
      continue;
    }
    if (typeof v === 'string' && v.trim() === '' && NULLABLE_OPTIONAL_KEYS.has(k)) {
      cleaned[k] = null;
      continue;
    }
    if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) {
      cleaned[k] = Number(v);
      continue;
    }
    cleaned[k] = v;
  }
  return cleaned;
}

/**
 * Normalize draft graph node params before sending to PUT /api/programmes/{id}/graph.
 * - Restores numeric strings edited via inputs back to numbers.
 * - Turns blank optional numeric caps/limits into null.
 * - Omits blank/null depositPremium so fresh layers without CMS pricing run cleanly for EL.
 */
export function cleanGraphForSave(draftGraph: GraphSpec): GraphSpec {
  return {
    ...draftGraph,
    nodes: draftGraph.nodes.map((n) => ({
      ...n,
      params: cleanParamDict(n.params),
    })),
  };
}
