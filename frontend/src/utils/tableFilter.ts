/**
 * Numeric column filter expressions: `>50`, `>=10`, `<5`, `<=5`, `=20`, `10-30` (inclusive range),
 * or a bare number (matches when the value rounds to it at the typed precision).
 * Unparseable input matches everything so a half-typed expression never empties the table.
 */
export function matchesNumberFilter(value: number | null | undefined, expr: string): boolean {
  const raw = expr.trim().replace(/[$,%m]/gi, '');
  if (!raw) return true;
  if (value == null || !Number.isFinite(value)) return false;

  const range = raw.match(/^(-?\d*\.?\d+)\s*(?:-|to|\.\.)\s*(-?\d*\.?\d+)$/i);
  if (range) {
    const lo = Number(range[1]);
    const hi = Number(range[2]);
    return value >= Math.min(lo, hi) && value <= Math.max(lo, hi);
  }

  const cmp = raw.match(/^(>=|<=|>|<|=)\s*(-?\d*\.?\d+)$/);
  if (cmp) {
    const n = Number(cmp[2]);
    switch (cmp[1]) {
      case '>':
        return value > n;
      case '>=':
        return value >= n;
      case '<':
        return value < n;
      case '<=':
        return value <= n;
      default:
        return Math.abs(value - n) < 1e-9;
    }
  }

  if (/^-?\d*\.?\d+$/.test(raw)) {
    const decimals = raw.includes('.') ? raw.split('.')[1].length : 0;
    const tol = 0.5 * 10 ** -decimals;
    return Math.abs(value - Number(raw)) <= tol + 1e-9;
  }
  return true;
}

export function matchesTextFilter(value: string | null | undefined, expr: string): boolean {
  const q = expr.trim().toLowerCase();
  if (!q) return true;
  return String(value ?? '').toLowerCase().includes(q);
}
