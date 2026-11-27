import { describe, expect, it } from 'vitest';
import { NODE_ACCENT, nodeAccent } from './nodeAccent';

describe('node accents', () => {
  it('shifts sources blue, pricing and EP teal, filters and combination gray, and layers magenta', () => {
    expect(nodeAccent('LOSS_SOURCE')).toBe(NODE_ACCENT.source);
    expect(nodeAccent('INDUSTRY_INDEX_SOURCE')).toBe(NODE_ACCENT.source);
    expect(nodeAccent('PORTFOLIO_ROLLUP')).toBe(NODE_ACCENT.source);
    expect(nodeAccent('PRICING_OUTPUT')).toBe(NODE_ACCENT.pricing);
    expect(nodeAccent('EP_OUTPUT')).toBe(NODE_ACCENT.pricing);
    expect(nodeAccent('COMBINATION')).toBe(NODE_ACCENT.filter);
    expect(nodeAccent('REGION_PERIL_FILTER')).toBe(NODE_ACCENT.filter);
    expect(nodeAccent('OCC_XL')).toBe(NODE_ACCENT.layer);
    expect(nodeAccent('AGG_QUOTA_SHARE')).toBe(NODE_ACCENT.layer);
    expect(nodeAccent('YELT_OUTPUT')).toBe(NODE_ACCENT.output);

    expect(NODE_ACCENT.source).not.toBe('#8a9bb0');
    expect(NODE_ACCENT.layer).not.toBe('#e8a84a');
    expect(NODE_ACCENT.pricing).not.toBe('#7a9e8e');
    expect(NODE_ACCENT.filter).not.toBe('#9a9082');
  });
});
