// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PROGRAMME_STATUSES, type ProgrammeStatus } from '../types/contracts';
import { defaultStatusesForMode } from '../utils/optimizerStatuses';
import { OptimizerStatusFilter } from './OptimizerStatusFilter';

function renderFilter(over: Partial<Parameters<typeof OptimizerStatusFilter>[0]> = {}) {
  const props = {
    modeLabel: 'Forward-Looking Planning',
    selected: defaultStatusesForMode('FORWARD_PLANNING'),
    isCustom: false,
    counts: { BOUND: 3, DECLINED: 1240, NTU: 2 } as Partial<Record<ProgrammeStatus, number>>,
    universeProgrammes: 3,
    onToggle: vi.fn(),
    onSelectAll: vi.fn(),
    onReset: vi.fn(),
    ...over,
  };
  render(<OptimizerStatusFilter {...props} />);
  return props;
}

const box = (status: string) => screen.getByRole('checkbox', { name: new RegExp(`^${status}\\b`) }) as HTMLInputElement;

describe('OptimizerStatusFilter', () => {
  afterEach(() => cleanup());

  it('offers every status, ticks the defaults and leaves declined, NTU and expired unticked', () => {
    renderFilter();
    expect(screen.getAllByRole('checkbox')).toHaveLength(PROGRAMME_STATUSES.length);
    expect(box('BOUND').checked).toBe(true);
    expect(box('QUOTED').checked).toBe(true);
    expect(box('DECLINED').checked).toBe(false);
    expect(box('NTU').checked).toBe(false);
    expect(box('EXPIRED').checked).toBe(false);
    expect(screen.getByText(/Default for Forward-Looking Planning · 3 programmes/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Reset to default' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('shows how many programmes each status holds', () => {
    renderFilter();
    expect(box('DECLINED').closest('label')?.textContent).toContain('1,240');
    expect(box('EXPIRED').closest('label')?.textContent).toContain('0');
  });

  it('reports the status the user ticks', () => {
    const props = renderFilter();
    fireEvent.click(box('DECLINED'));
    expect(props.onToggle).toHaveBeenCalledWith('DECLINED');
  });

  it('flags a custom selection that brings in deals that are not on risk', () => {
    const props = renderFilter({ selected: ['BOUND', 'DECLINED', 'NTU'], isCustom: true, universeProgrammes: 45 });
    expect(screen.getByText(/Custom selection · 45 programmes/)).toBeTruthy();
    expect(screen.getByRole('note').textContent).toContain('DECLINED and NTU deals are not on risk');
    fireEvent.click(screen.getByRole('button', { name: 'Reset to default' }));
    expect(props.onReset).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'All statuses' }));
    expect(props.onSelectAll).toHaveBeenCalled();
  });

  it('warns before a run that would solve a very large portfolio', () => {
    renderFilter({ universeProgrammes: 300 });
    expect(screen.queryByRole('note')).toBeNull();
    cleanup();
    renderFilter({ universeProgrammes: 1208 });
    expect(screen.getByRole('note').textContent).toContain('solves all 1,208 programmes');
  });

  it('does not let the last selected status be removed', () => {
    renderFilter({ selected: ['DECLINED'], isCustom: true });
    expect(box('DECLINED').disabled).toBe(true);
    expect(box('BOUND').disabled).toBe(false);
  });
});
