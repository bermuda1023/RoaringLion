import * as echarts from 'echarts';
import { chartTheme } from './chartTheme';
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
    const markLines = [
      {
        yAxis: 0,
        name: 'Break-Even (0%)',
        lineStyle: {
          color: chartTheme.teal,
          type: 'dashed' as const,
          width: 1.2,
        },
        label: {
          formatter: isNarrow ? '0%' : 'Break-Even (0%)',
          color: chartTheme.teal,
          fontSize: isNarrow ? 9 : 10,
        },
      },
    ];

    const series: echarts.SeriesOption[] = [
      {
        name: primaryLabel ?? `Net ROC (${data.view})`,
        type: 'line',
        smooth: false,
        showSymbol: false,
        lineStyle: { color: chartTheme.brass, width: 2.5 },
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
        lineStyle: { color: chartTheme.blue, width: 2, type: 'dashed' },
        data: comparisonData.cdfCurve.map((p) => [p.cumulativeProb, p.rocPct]),
      });
    }

    chart.setOption({
      backgroundColor: 'transparent',
      legend: comparisonData
        ? {
            type: 'scroll',
            top: 0,
            left: 0,
            right: 8,
            textStyle: { color: chartTheme.text, fontSize: 12 },
          }
        : undefined,
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: chartTheme.tooltipBg,
        borderColor: chartTheme.axis,
        textStyle: { color: chartTheme.textStrong, fontSize: 12 },
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
      grid: { left: isNarrow ? 58 : 78, right: isNarrow ? 24 : 20, top: comparisonData ? 36 : 24, bottom: 48 },
      xAxis: {
        type: 'value',
        min: 0,
        max: 100,
        name: 'Cumulative Probability (%)',
        nameLocation: 'middle',
        nameGap: 26,
        axisLabel: { color: chartTheme.text, formatter: '{value}%' },
        splitLine: { lineStyle: { color: chartTheme.grid } },
      },
      yAxis: {
        type: 'value',
        name: isNarrow ? 'ROC (%)' : 'Return on Capital (%)',
        nameLocation: 'middle',
        nameGap: isNarrow ? 40 : 56,
        nameTextStyle: { color: chartTheme.text, fontSize: 12 },
        axisLabel: { color: chartTheme.text, formatter: '{value}%' },
        splitLine: { lineStyle: { color: chartTheme.grid } },
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
