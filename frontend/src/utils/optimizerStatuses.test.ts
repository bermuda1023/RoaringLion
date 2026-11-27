import { describe, expect, it } from 'vitest';
import { PROGRAMME_STATUSES } from '../types/contracts';
import {
  candidatesUrl,
  countProgrammesByStatus,
  defaultStatusesForMode,
  isOffRiskStatus,
  toggleStatus,
} from './optimizerStatuses';

describe('optimizer status defaults', () => {
  it('keeps the rules the optimizer had before statuses were selectable', () => {
    expect(defaultStatusesForMode('CURRENT')).toEqual(['QUOTED', 'FIRM_ORDER', 'BOUND']);
    expect(defaultStatusesForMode('FORWARD_PLANNING')).toEqual([
      'SUBMISSION',
      'INDICATIVE',
      'QUOTED',
      'AUTHORIZED',
      'FIRM_ORDER',
      'BOUND',
      'BOUND_FOR_OPS',
      'PLANNED',
    ]);
  });

  it('treats declined, NTU and expired deals as not on risk', () => {
    expect(PROGRAMME_STATUSES.filter(isOffRiskStatus)).toEqual(['DECLINED', 'NTU', 'EXPIRED']);
  });
});

describe('toggleStatus', () => {
  const defaults = defaultStatusesForMode('CURRENT');

  it('adds a declined status to the defaults, in status order', () => {
    expect(toggleStatus(null, defaults, 'DECLINED')).toEqual(['QUOTED', 'FIRM_ORDER', 'BOUND', 'DECLINED']);
  });

  it('removes a status and goes back to "defaults" when the selection matches them again', () => {
    const withDeclined = toggleStatus(null, defaults, 'DECLINED');
    expect(toggleStatus(withDeclined, defaults, 'BOUND')).toEqual(['QUOTED', 'FIRM_ORDER', 'DECLINED']);
    expect(toggleStatus(withDeclined, defaults, 'DECLINED')).toBeNull();
  });

  it('never empties the selection', () => {
    expect(toggleStatus(['DECLINED'], defaults, 'DECLINED')).toEqual(['DECLINED']);
  });
});

describe('candidatesUrl', () => {
  const base = { view: 'ADJUSTED', mode: 'FORWARD_PLANNING', search: '', limit: 25, offset: 0 } as const;

  it('sends no status filter while the defaults apply, so the server picks them', () => {
    expect(candidatesUrl({ ...base, includedStatuses: null })).toBe(
      '/api/optimizer/candidates?view=ADJUSTED&mode=FORWARD_PLANNING&limit=25&offset=0',
    );
  });

  it('repeats includedStatuses and carries search and paging', () => {
    const url = candidatesUrl({
      ...base,
      includedStatuses: ['BOUND', 'DECLINED'],
      search: '  farmers & co ',
      offset: 50,
    });
    const qs = new URL(url, 'http://x').searchParams;
    expect(qs.getAll('includedStatuses')).toEqual(['BOUND', 'DECLINED']);
    expect(qs.get('search')).toBe('farmers & co');
    expect(qs.get('limit')).toBe('25');
    expect(qs.get('offset')).toBe('50');
  });
});

describe('countProgrammesByStatus', () => {
  it('counts each programme once per status and leaves out the retro hedge candidate', () => {
    const rows = [
      { programmeId: 'a', status: 'DECLINED', direction: 'ASSUMED' },
      { programmeId: 'b', status: 'BOUND', direction: 'ASSUMED' },
      { programmeId: 'b', status: 'BOUND', direction: 'ASSUMED' },
      { programmeId: 'c', status: 'BOUND', direction: 'ASSUMED' },
      { programmeId: 'hedge', status: 'PLANNED', direction: 'EXTERNAL_RETRO' },
    ];
    expect(countProgrammesByStatus(rows)).toEqual([
      { status: 'BOUND', programmes: 2 },
      { status: 'DECLINED', programmes: 1 },
    ]);
  });
});
