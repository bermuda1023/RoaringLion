import * as echarts from 'echarts';
import { useEffect, useRef } from 'react';
import type { EpDeltaPoint, PortfolioEpPoint, PortfolioEpSlice } from '../types/contracts';
import { fmtMoneyM } from '../utils/format';

export type EpMetricKey = 'aep' | 'oep' | 'aepTvar' | 'oepTvar';
export type EpDimension = 'TOTAL' | 'REGION' | 'PERIL' | 'REGION_PERIL';

function fmtChartMoney(value: number, currency: string): string {
  if (currency === 'USD') return fmtMoneyM(value, 2);
  const abs = Math.abs(value).toFixed(2);
  return `${value < 0 ? '-' : ''}${currency} ${abs}m`;
}

function currencyAxisLabel(currency: string): string {
  return currency === 'USD' ? '${value}m' : `${currency} {value}m`;
}

const METRIC_LABELS: Record<EpMetricKey, string> = {
  aep: 'AEP',
  oep: 'OEP',
  aepTvar: 'AEP TVaR',
  oepTvar: 'OEP TVaR',
};

const SPLIT_PALETTE = ['#e8a84a', '#6e9cd4', '#5cad7a', '#e0706a', '#b88ce8', '#7a9e8e', '#e0a84a', '#9ab4d6'];

export function PortfolioEpChart({
  epCurve,
  comparisonEpCurve,
  slices,
  dimension,
  metric,
  selectedSliceKey,
  primaryLabel = 'Net',
  comparisonLabel = 'Without Hedges',
  currency = 'USD',
}: {
  epCurve: PortfolioEpPoint[];
  comparisonEpCurve?: PortfolioEpPoint[] | null;
  slices: PortfolioEpSlice[];
  dimension: EpDimension;
  metric: EpMetricKey;
  selectedSliceKey?: string | null;
  primaryLabel?: string;
  comparisonLabel?: string;
  currency?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const isNarrow = ref.current.clientWidth < 520;
    const rps = epCurve.map((p) => `1-in-${Math.round(p.returnPeriod)}`);

    const series: echarts.SeriesOption[] = [];

    if (dimension === 'TOTAL') {
      series.push(
        {
          name: `${primaryLabel} AEP TVaR`,
          type: 'line',
          smooth: false,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#e0706a', width: 2, type: 'dotted' },
          itemStyle: { color: '#e0706a' },
          data: epCurve.map((p) => p.aepTvar),
        },
        {
          name: `${primaryLabel} AEP`,
          type: 'line',
          smooth: false,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#e8a84a', width: 2.5 },
          itemStyle: { color: '#e8a84a' },
          data: epCurve.map((p) => p.aep),
        },
        {
          name: `${primaryLabel} OEP`,
          type: 'line',
          smooth: false,
          symbol: 'diamond',
          symbolSize: 6,
          lineStyle: { color: '#5cad7a', width: 2.2 },
          itemStyle: { color: '#5cad7a' },
          data: epCurve.map((p) => p.oep),
        },
      );
      if (comparisonEpCurve && comparisonEpCurve.length > 0) {
        series.push({
          name: `${comparisonLabel} AEP`,
          type: 'line',
          smooth: false,
          symbol: 'none',
          lineStyle: { color: '#6e9cd4', width: 1.8, type: 'dashed' },
          itemStyle: { color: '#6e9cd4' },
          data: comparisonEpCurve.map((p) => p.aep),
        });
      }
    } else if (selectedSliceKey) {
      const target = slices.find((s) => s.key === selectedSliceKey);
      if (target) {
        series.push(
          {
            name: `${target.label} · AEP TVaR`,
            type: 'line',
            smooth: false,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: { color: '#e0706a', width: 2, type: 'dotted' },
            itemStyle: { color: '#e0706a' },
            data: target.epPoints.map((p) => p.aepTvar),
          },
          {
            name: `${target.label} · AEP`,
            type: 'line',
            smooth: false,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: { color: '#e8a84a', width: 2.5 },
            itemStyle: { color: '#e8a84a' },
            data: target.epPoints.map((p) => p.aep),
          },
          {
            name: `${target.label} · OEP`,
            type: 'line',
            smooth: false,
            symbol: 'diamond',
            symbolSize: 6,
            lineStyle: { color: '#5cad7a', width: 2.2 },
            itemStyle: { color: '#5cad7a' },
            data: target.epPoints.map((p) => p.oep),
          },
        );
      }
    } else {
      const topSlices = slices.slice(0, 6);
      topSlices.forEach((s, idx) => {
        const color = SPLIT_PALETTE[idx % SPLIT_PALETTE.length];
        series.push({
          name: s.label,
          type: 'line',
          smooth: false,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { color, width: idx === 0 ? 2.4 : 1.9 },
          itemStyle: { color },
          data: s.epPoints.map((p) => p[metric]),
        });
      });
    }

    chart.setOption({
      backgroundColor: 'transparent',
      legend: {
        top: 0,
        right: 6,
        type: 'scroll',
        textStyle: { color: '#9a9082', fontSize: 11 },
      },
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: '#1b1713',
        borderColor: '#3a3229',
        textStyle: { color: '#f7f1e6', fontSize: 12 },
        formatter: (params: unknown) => {
          const arr = params as { seriesName: string; value: number; marker: string; axisValue: string }[];
          if (!arr?.length) return '';
          const lines = [`<b>${arr[0].axisValue}</b>`];
          for (const item of arr) {
            lines.push(`${item.marker} ${item.seriesName}: <b>${fmtChartMoney(Number(item.value), currency)}</b>`);
          }
          return lines.join('<br/>');
        },
      },
      grid: { left: isNarrow ? 52 : 60, right: 20, top: 38, bottom: 42 },
      xAxis: {
        type: 'category',
        data: rps,
        name: 'Return Period',
        nameLocation: 'middle',
        nameGap: 26,
        axisLabel: { color: '#9a9082', fontSize: 11 },
        axisLine: { lineStyle: { color: '#3a3229' } },
      },
      yAxis: {
        type: 'value',
        name: dimension === 'TOTAL' || selectedSliceKey ? `Loss (${currency} M)` : `${METRIC_LABELS[metric]} (${currency} M)`,
        axisLabel: { color: '#9a9082', formatter: currencyAxisLabel(currency) },
        splitLine: { lineStyle: { color: '#2a241e' } },
      },
      series,
    });

    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    ro?.observe(ref.current);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [epCurve, comparisonEpCurve, slices, dimension, metric, selectedSliceKey, primaryLabel, comparisonLabel, currency]);

  return <div ref={ref} className="chart-box" data-testid="portfolio-ep-chart" />;
}

export type CompareCurveMode = 'oep_aep' | 'aep_tvar' | 'oep_tvar';

export function PortfolioCompareEpChart({
  epDeltas,
  labelA,
  labelB,
  curveMode = 'oep_aep',
  currency = 'USD',
}: {
  epDeltas: EpDeltaPoint[];
  labelA: string;
  labelB: string;
  curveMode?: CompareCurveMode;
  currency?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const isNarrow = ref.current.clientWidth < 520;
    const rps = epDeltas.map((p) => `1-in-${Math.round(p.returnPeriod)}`);

    const series: echarts.SeriesOption[] = [];
    if (curveMode === 'oep_aep') {
      series.push(
        {
          name: `${labelA} · AEP`,
          type: 'line',
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#e8a84a', width: 2.5 },
          itemStyle: { color: '#e8a84a' },
          data: epDeltas.map((p) => p.aepA),
        },
        {
          name: `${labelB} · AEP`,
          type: 'line',
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { color: '#6e9cd4', width: 2.2, type: 'dashed' },
          itemStyle: { color: '#6e9cd4' },
          data: epDeltas.map((p) => p.aepB),
        },
        {
          name: `${labelA} · OEP`,
          type: 'line',
          symbol: 'diamond',
          symbolSize: 6,
          lineStyle: { color: '#5cad7a', width: 2.2 },
          itemStyle: { color: '#5cad7a' },
          data: epDeltas.map((p) => p.oepA),
        },
        {
          name: `${labelB} · OEP`,
          type: 'line',
          symbol: 'diamond',
          symbolSize: 5,
          lineStyle: { color: '#b88ce8', width: 2, type: 'dashed' },
          itemStyle: { color: '#b88ce8' },
          data: epDeltas.map((p) => p.oepB),
        },
      );
    } else if (curveMode === 'aep_tvar') {
      series.push(
        {
          name: `${labelA} · AEP TVaR`,
          type: 'line',
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#e0706a', width: 2.5 },
          itemStyle: { color: '#e0706a' },
          data: epDeltas.map((p) => p.aepTvarA),
        },
        {
          name: `${labelB} · AEP TVaR`,
          type: 'line',
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { color: '#b88ce8', width: 2.2, type: 'dashed' },
          itemStyle: { color: '#b88ce8' },
          data: epDeltas.map((p) => p.aepTvarB),
        },
        {
          name: `${labelA} · AEP`,
          type: 'line',
          symbol: 'diamond',
          symbolSize: 5,
          lineStyle: { color: '#e8a84a', width: 2 },
          itemStyle: { color: '#e8a84a' },
          data: epDeltas.map((p) => p.aepA),
        },
        {
          name: `${labelB} · AEP`,
          type: 'line',
          symbol: 'diamond',
          symbolSize: 5,
          lineStyle: { color: '#6e9cd4', width: 2, type: 'dashed' },
          itemStyle: { color: '#6e9cd4' },
          data: epDeltas.map((p) => p.aepB),
        },
      );
    } else {
      series.push(
        {
          name: `${labelA} · OEP TVaR`,
          type: 'line',
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#e0706a', width: 2.5 },
          itemStyle: { color: '#e0706a' },
          data: epDeltas.map((p) => p.oepTvarA),
        },
        {
          name: `${labelB} · OEP TVaR`,
          type: 'line',
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { color: '#b88ce8', width: 2.2, type: 'dashed' },
          itemStyle: { color: '#b88ce8' },
          data: epDeltas.map((p) => p.oepTvarB),
        },
        {
          name: `${labelA} · OEP`,
          type: 'line',
          symbol: 'diamond',
          symbolSize: 5,
          lineStyle: { color: '#5cad7a', width: 2 },
          itemStyle: { color: '#5cad7a' },
          data: epDeltas.map((p) => p.oepA),
        },
        {
          name: `${labelB} · OEP`,
          type: 'line',
          symbol: 'diamond',
          symbolSize: 5,
          lineStyle: { color: '#6e9cd4', width: 2, type: 'dashed' },
          itemStyle: { color: '#6e9cd4' },
          data: epDeltas.map((p) => p.oepB),
        },
      );
    }

    chart.setOption({
      backgroundColor: 'transparent',
      legend: {
        top: 0,
        right: 6,
        type: 'scroll',
        textStyle: { color: '#9a9082', fontSize: 11 },
      },
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: '#1b1713',
        borderColor: '#3a3229',
        textStyle: { color: '#f7f1e6', fontSize: 12 },
        formatter: (params: unknown) => {
          const arr = params as { seriesName: string; value: number; marker: string; axisValue: string }[];
          if (!arr?.length) return '';
          const lines = [`<b>${arr[0].axisValue}</b>`];
          for (const item of arr) {
            lines.push(`${item.marker} ${item.seriesName}: <b>${fmtChartMoney(Number(item.value), currency)}</b>`);
          }
          return lines.join('<br/>');
        },
      },
      grid: { left: isNarrow ? 52 : 60, right: 20, top: 38, bottom: 42 },
      xAxis: {
        type: 'category',
        data: rps,
        name: 'Return Period',
        nameLocation: 'middle',
        nameGap: 26,
        axisLabel: { color: '#9a9082', fontSize: 11 },
        axisLine: { lineStyle: { color: '#3a3229' } },
      },
      yAxis: {
        type: 'value',
        name: `Loss (${currency} M)`,
        axisLabel: { color: '#9a9082', formatter: currencyAxisLabel(currency) },
        splitLine: { lineStyle: { color: '#2a241e' } },
      },
      series,
    });

    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    ro?.observe(ref.current);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [epDeltas, labelA, labelB, curveMode, currency]);

  return <div ref={ref} className="chart-box" data-testid="portfolio-compare-ep-chart" />;
}
