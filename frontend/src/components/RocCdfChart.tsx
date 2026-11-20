import * as echarts from 'echarts';
import { useEffect, useRef } from 'react';
import type { PortfolioRollupResponse } from '../types/contracts';

export function RocCdfChart({ data }: { data: PortfolioRollupResponse }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const isNarrow = ref.current.clientWidth < 520;
    const markLines = data.guidelines.map((g) => ({
      yAxis: g.thresholdRocPct,
      name: `${g.label}: ${g.thresholdRocPct}%`,
      lineStyle: {
        color: g.passed ? '#22c55e' : '#ef4444',
        type: 'dashed' as const,
        width: 1.2,
      },
      label: {
        formatter: isNarrow ? `${g.thresholdRocPct}%` : `${g.label} (${g.thresholdRocPct}%)`,
        color: g.passed ? '#86efac' : '#fca5a5',
        fontSize: isNarrow ? 9 : 10,
      },
    }));

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        confine: true,
        formatter: (params: unknown) => {
          const arr = params as { data: [number, number] }[];
          const pt = arr?.[0]?.data;
          if (!pt) return '';
          return `Cumulative Prob: <b>${pt[0].toFixed(2)}%</b><br/>Return on Capital: <b>${pt[1].toFixed(2)}%</b>`;
        },
      },
      grid: { left: isNarrow ? 50 : 55, right: isNarrow ? 34 : 30, top: 28, bottom: 45 },
      xAxis: {
        type: 'value',
        min: 0,
        max: 100,
        name: 'Cumulative Probability (%)',
        nameLocation: 'middle',
        nameGap: 26,
        axisLabel: { color: '#94a3b8', formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#1e293b' } },
      },
      yAxis: {
        type: 'value',
        name: isNarrow ? 'ROC (%)' : 'Return on Capital (%)',
        axisLabel: { color: '#94a3b8', formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#1e293b' } },
      },
      series: [
        {
          name: `Net ROC (${data.view})`,
          type: 'line',
          smooth: false,
          showSymbol: false,
          lineStyle: { color: '#38bdf8', width: 2.5 },
          data: data.cdfCurve.map((p) => [p.cumulativeProb, p.rocPct]),
          markLine: { silent: true, symbol: 'none', data: markLines },
        },
      ],
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
  }, [data]);

  return <div ref={ref} className="chart-box" />;
}
