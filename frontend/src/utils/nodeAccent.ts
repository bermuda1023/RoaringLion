/** Node accent colors for the structuring canvas and the CMS suggestion tree.
 *  A modest shift toward the target palette: loss sources blue, pricing and EP
 *  outputs teal, combination and filters gray, contract layers magenta.
 */

export const NODE_ACCENT = {
  source: '#6e9cd4',
  filter: '#90939b',
  layer: '#d4899e',
  pricing: '#5bb49f',
  output: '#7a9e8e',
} as const;

const LAYER_TYPES = new Set([
  'OCC_XL',
  'AGG_XL',
  'QUOTA_SHARE',
  'AGG_QUOTA_SHARE',
  'SURPLUS_SHARE',
  'CASCADING_TOWER',
  'ILW_OPTION',
  'STOP_LOSS',
  'GENERIC',
]);

export function nodeAccent(nodeType: string): string {
  if (nodeType === 'LOSS_SOURCE' || nodeType === 'INDUSTRY_INDEX_SOURCE') return NODE_ACCENT.source;
  if (nodeType === 'PRICING_OUTPUT' || nodeType === 'EP_OUTPUT') return NODE_ACCENT.pricing;
  if (nodeType.includes('FILTER') || nodeType === 'COMBINATION' || nodeType === 'UPLIFT') return NODE_ACCENT.filter;
  if (LAYER_TYPES.has(nodeType)) return NODE_ACCENT.layer;
  return NODE_ACCENT.output;
}
