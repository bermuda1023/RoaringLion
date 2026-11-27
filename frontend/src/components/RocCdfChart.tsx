import * as echarts from 'echarts';
import { useEffect, useRef } from 'react';
import type { PortfolioRollupResponse } from '../types/contracts';

export function RocCdfChart({
  data,
  comparisonData,
  primaryLabel,
  comparisonLabel,
}: {
  data: PortfolioRollupResponse;
  comparisonData?: PortfolioRollupResponse | null;
  primaryLabel?: string;
  comparisonLabel?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const isNarrow = ref.current.clientWidth < 520;
    const markLines = data.guidelines.map((g) => ({
      yAxis: g.thresholdRocPct,
      name: `${g.label}: ${g.thresholdRocPct}%`,
      lineStyle: {
        color: g.passed ? '#5cad7a' : '#e0706a',
        type: 'dashed' as const,
        width: 1.2,
      },
      label: {
        formatter: isNarrow ? `${g.thresholdRocPct}%` : `${g.label} (${g.thresholdRocPct}%)`,
        color: g.passed ? '#8fd4a8' : '#f0a8a4',
        fontSize: isNarrow ? 9 : 10,
      },
    }));

    const series: echarts.SeriesOption[] = [
      {
        name: primaryLabel ?? `Net ROC (${data.view})`,
        type: 'line',
        smooth: false,
        showSymbol: false,
        lineStyle: { color: '#e8a84a', width: 2.5 },
        data: data.cdfCurve.map((p) => [p.cumulativeProb, p.rocPct]),
        markLine: { silent: true, symbol: 'none', data: markLines },
      },
    ];

    if (comparisonData) {
      series.push({
        name: comparisonLabel ?? `Without Hedges (${comparisonData.view})`,
        type: 'line',
        smooth: false,
        showSymbol: false,
        lineStyle: { color: '#6e9cd4', width: 2, type: 'dashed' },
        data: comparisonData.cdfCurve.map((p) => [p.cumulativeProb, p.rocPct]),
      });
    }

    chart.setOption({
      backgroundColor: 'transparent',
      legend: comparisonData
        ? {
            top: 0,
            right: 8,
            textStyle: { color: '#9a9082', fontSize: 11 },
          }
        : undefined,
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: '#1b1713',
        borderColor: '#3a3229',
        textStyle: { color: '#f7f1e6', fontSize: 12 },
        formatter: (params: unknown) => {
          const arr = params as { seriesName: string; data: [number, number] }[];
          const first = arr?.[0]?.data;
          if (!first) return '';
          const lines = [`Cumulative Prob: <b>${first[0].toFixed(2)}%</b>`];
          for (const item of arr) {
            if (item?.data) {
              lines.push(`${item.seriesName}: <b>${item.data[1].toFixed(2)}%</b>`);
            }
          }
          return lines.join('<br/>');
        },
      },
      grid: { left: isNarrow ? 50 : 55, right: isNarrow ? 34 : 30, top: comparisonData ? 34 : 28, bottom: 45 },
      xAxis: {
        type: 'value',
        min: 0,
        max: 100,
        name: 'Cumulative Probability (%)',
        nameLocation: 'middle',
        nameGap: 26,
        axisLabel: { color: '#9a9082', formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#2a241e' } },
      },
      yAxis: {
        type: 'value',
        name: isNarrow ? 'ROC (%)' : 'Return on Capital (%)',
        axisLabel: { color: '#9a9082', formatter: '{value}%' },
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
  }, [data, comparisonData, primaryLabel, comparisonLabel]);

  return <div ref={ref} className="chart-box" />;
}
