// Smart node labels: `<name> — <terms>`, where terms are generated from the node's parameters.
// Mirrors backend/app/services/node_labels.py; keep the two in sync.
// Params used: `autoLabel` (false = fully custom label, never rewritten) and `labelName` (user-edited name part).

export const LABEL_SEP = ' — ';
export const LABEL_META_KEYS = ['autoLabel', 'labelName'];
export const LAYER_META_KEYS = ['layerCode'];
export const OUTPUT_LINK_KEYS = ['view', 'contractId', 'layerNodeId', 'linkedLabel', 'unlinked'];
export const LAYER_NODE_TYPES = [
  'OCC_XL',
  'AGG_XL',
  'QUOTA_SHARE',
  'AGG_QUOTA_SHARE',
  'SURPLUS_SHARE',
  'CASCADING_TOWER',
  'ILW_OPTION',
];

export function nextLayerCode(existingCodes: (string | null | undefined)[]): string {
  const used = new Set(existingCodes.map((c) => (c ?? '').trim().toLowerCase()).filter(Boolean));
  let maxNum = 0;
  for (const code of used) {
    const m = /^l(\d+)$/i.exec(code);
    if (m) maxNum = Math.max(maxNum, Number(m[1]));
  }
  let n = maxNum + 1;
  while (used.has(`l${n}`)) n += 1;
  return `L${n}`;
}

type Params = Record<string, unknown>;

function num(v: unknown): number | null {
  if (typeof v === 'boolean' || v == null || v === '' || v === '-') return null;
  const f = Number(v);
  return Number.isFinite(f) ? f : null;
}

function round2(v: number): number {
  const r = Math.floor(Math.abs(v) * 100 + 0.5) / 100;
  return v < 0 ? -r : r;
}

function trim(v: number): string {
  const s = round2(v).toFixed(2).replace(/\.?0+$/, '');
  return s === '-0' || s === '' ? '0' : s;
}

const amt = (v: number) => (Math.abs(v) >= 1000 ? `${trim(v / 1000)}bn` : `${trim(v)}m`);
const pct = (r: number) => `${trim(r * 100)}%`;

function ordinal(n: number): string {
  const i = Math.round(n);
  const suf = i % 100 >= 10 && i % 100 <= 20 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[i % 10] ?? 'th';
  return `${i}${suf}`;
}

function shareSuffix(p: Params): string {
  const placed = num(p.placedShare);
  return placed != null && Math.abs(placed - 1) > 1e-9 ? ` · ${pct(placed)} share` : '';
}

const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String).filter((x) => x !== 'ALL') : []);

export function nodeTerms(nodeType: string, params: Params | undefined): string | null {
  const p = params ?? {};
  switch (nodeType) {
    case 'OCC_XL': {
      const lim = num(p.occLimit);
      const att = num(p.occAttachment);
      if (lim == null || att == null) return null;
      let s = `${amt(lim)} xs ${amt(att)}`;
      const reinst = num(p.reinstatements);
      if (reinst != null) {
        const rip = num(p.ripRate);
        s += ` (${trim(reinst)}@${pct(rip ?? 1)})`;
      }
      return s + shareSuffix(p);
    }
    case 'AGG_XL': {
      const lim = num(p.aggLimit);
      const att = num(p.aggAttachment);
      if (lim == null || att == null) return null;
      let s = `${amt(lim)} xs ${amt(att)}`;
      const cap = num(p.perEventCap);
      if (cap != null) s += ` · ${amt(cap)} event cap`;
      return s + shareSuffix(p);
    }
    case 'QUOTA_SHARE':
    case 'AGG_QUOTA_SHARE': {
      const ces = num(p.cessionShare);
      if (ces == null) return null;
      let s = `${pct(ces)} ceded`;
      const ev = num(p.eventCap);
      const agg = num(p.aggCap);
      if (ev != null) s += ` · ${amt(ev)} event cap`;
      if (agg != null) s += ` · ${amt(agg)} agg cap`;
      return s + shareSuffix(p);
    }
    case 'SURPLUS_SHARE': {
      const lines = num(p.numLines);
      const ret = num(p.retentionLine);
      if (lines == null || ret == null) return null;
      return `${trim(lines)} lines × ${amt(ret)}` + shareSuffix(p);
    }
    case 'ILW_OPTION': {
      const lim = num(p.limit);
      const trig = num(p.trigger);
      if (lim == null || trig == null) return null;
      return `${amt(lim)} @ ${trim(trig)}bn industry` + shareSuffix(p);
    }
    case 'CASCADING_TOWER': {
      const layers = (Array.isArray(p.layers) ? p.layers : []).filter((l) => l && typeof l === 'object') as Params[];
      const lims = layers.map((l) => num(l.occLimit));
      const atts = layers.map((l) => num(l.occAttachment));
      if (!layers.length || lims.some((x) => x == null) || atts.some((x) => x == null)) return null;
      const total = (lims as number[]).reduce((a, b) => a + b, 0);
      return `${layers.length} layer${layers.length !== 1 ? 's' : ''} · ${amt(total)} xs ${amt(Math.min(...(atts as number[])))}`;
    }
    case 'REGION_PERIL_FILTER': {
      const parts = [list(p.zones).join('/'), list(p.perils).join('/'), list(p.regionPerils).join(', ')].filter(Boolean);
      return parts.length ? parts.join(' · ') : 'All zones & perils';
    }
    case 'EVENT_ORDER_FILTER': {
      const lo = num(p.minOrder);
      if (lo == null) return null;
      const hi = num(p.maxOrder);
      let s =
        hi == null
          ? `${ordinal(lo)}+ events`
          : Math.trunc(hi) === Math.trunc(lo)
            ? `${ordinal(lo)} event`
            : `${ordinal(lo)}–${ordinal(hi)} events`;
      const thr = num(p.minLossThreshold);
      if (thr) s += ` ≥ ${amt(thr)}`;
      return s;
    }
    case 'DATE_WINDOW_FILTER': {
      const a = num(p.startDay);
      const b = num(p.endDay);
      if (a == null || b == null) return null;
      return `Day ${trim(a)}–${trim(b)}`;
    }
    case 'LOSS_SIZE_FILTER': {
      const lo = num(p.minLoss);
      const hi = num(p.maxLoss);
      if (hi != null) return `${amt(lo ?? 0)}–${amt(hi)}`;
      return lo ? `≥ ${amt(lo)}` : 'All loss sizes';
    }
    case 'UPLIFT': {
      const f = num(p.factor);
      if (f == null) return null;
      let s = `×${round2(f).toFixed(2)}`;
      const add = num(p.additiveLoad);
      if (add) s += ` ${add > 0 ? '+' : '−'} ${amt(Math.abs(add))}`;
      return s;
    }
    case 'YELT_OUTPUT':
      return p.view === 'ADJUSTED' ? 'Adjusted' : p.view === 'UNADJUSTED' ? 'Unadjusted' : null;
    case 'PRICING_OUTPUT':
      return p.contractId && typeof p.linkedLabel === 'string' && p.linkedLabel ? p.linkedLabel : null;
    default:
      return null;
  }
}

export function nameFromLabel(label: string): string {
  const l = (label ?? '').trim();
  const idx = l.indexOf(LABEL_SEP);
  if (idx >= 0) return l.slice(0, idx).trim();
  return l.replace(/\s*\([^()]*\)\s*$/, '').trim() || l;
}

export function labelName(params: Params | undefined, label: string): string {
  const stored = params?.labelName;
  return typeof stored === 'string' ? stored.trim() : nameFromLabel(label);
}

export function composeLabel(name: string, terms: string | null): string {
  const n = (name ?? '').trim();
  if (!terms) return n;
  return n ? `${n}${LABEL_SEP}${terms}` : terms;
}

export const isAutoLabel = (params: Params | undefined) => params?.autoLabel !== false;

/** The label a node should carry given its params; custom or term-less nodes keep their label. */
// Output nodes only carry terms once configured; dropping the configuration drops the terms suffix.
const OPTIONAL_TERM_TYPES = ['YELT_OUTPUT', 'PRICING_OUTPUT'];

export function smartLabel(nodeType: string, params: Params | undefined, label: string): string {
  if (!isAutoLabel(params)) return label;
  const terms = nodeTerms(nodeType, params);
  if (terms == null) {
    if (OPTIONAL_TERM_TYPES.includes(nodeType) && label.includes(LABEL_SEP)) return labelName(params, label) || label;
    return label;
  }
  return composeLabel(labelName(params, label), terms);
}
