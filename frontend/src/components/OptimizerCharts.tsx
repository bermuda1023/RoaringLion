import * as echarts from 'echarts';
import { useEffect, useRef } from 'react';

export interface PortfolioMetricsSnapshot {
  expectedProfitM: number;
  meanRocPct: number;
  deployedCollateralM: number;
  grossPremiumM: number;
  expectedLossM: number;
  elCapitalRatio: number;
  riskRewardRatio: number;
  tvar50M: number;
  tvar100M: number;
  tvar250M: number;
  tvar500M: number;
  tvar1000M: number;
  var100M: number;
  var250M: number;
}

export interface OptimizedLayerRow {
  layerKey: string;
  programmeId: string;
  layerId: string;
  cedentName: string;
  programmeName: string;
  layerName: string;
  direction: string;
  /** The deal's `ProgrammeStatus`. */
  status: string;
  regionPerils: string[];
  currentShare: number;
  optimalShare: number;
  deltaShare: number;
  optimalLimitM: number;
  optimalPremiumM: number;
  optimalElM: number;
  optimalExpectedProfitM: number;
  coTvar100M: number;
  coTvar250M: number;
  coTvar250Pct: number;
  coVar250M: number;
  coVar250Pct: number;
}

export interface ReturnPeriodStackRow {
  returnPeriod: number;
  totalLossM: number;
  byLayer: Record<string, number>;
}

export interface FrontierExplorerPoint {
  pointIndex: number;
  targetTvar250M: number;
  status: string;
  solver: string;
  metrics: PortfolioMetricsSnapshot;
  layers: OptimizedLayerRow[];
  constraints: {
    name: string;
    limitValue: number;
    actualValue: number;
    slack: number;
    binding: boolean;
    shadowPrice: number | null;
  }[];
  returnPeriodContributions: ReturnPeriodStackRow[];
}

export type XAxisKey = 'tvar250M' | 'tvar100M' | 'tvar500M' | 'deployedCollateralM';
export type YAxisKey = 'expectedProfitM' | 'meanRocPct' | 'riskRewardRatio';

const AXIS_LABELS: Record<XAxisKey | YAxisKey, string> = {
  tvar250M: '1-in-250 TVaR ($M)',
  tvar100M: '1-in-100 TVaR ($M)',
  tvar500M: '1-in-500 TVaR ($M)',
  deployedCollateralM: 'Deployed Collateral ($M)',
  expectedProfitM: 'Expected Return ($M)',
  meanRocPct: 'Mean ROC (%)',
  riskRewardRatio: 'Risk / Reward (Profit / TVaR 250)',
};

const PALETTE = ['#e8a84a', '#5cad7a', '#8a9bb0', '#c4722a', '#7a9e8e', '#f0c57a', '#e0706a', '#9a9082'];

export function ParetoFrontierChart({
  initial,
  frontier,
  selectedIndex,
  xKey,
  yKey,
  onSelectPoint,
}: {
  initial: PortfolioMetricsSnapshot;
  frontier: FrontierExplorerPoint[];
  selectedIndex: number;
  xKey: XAxisKey;
  yKey: YAxisKey;
  onSelectPoint: (idx: number) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const isNarrow = ref.current.clientWidth < 460;

    const feasible = frontier.filter((p) => p.status === 'Optimal' || p.status === 'Feasible');
    const sorted = [...feasible].sort((a, b) => a.metrics[xKey] - b.metrics[xKey]);
    const sel = frontier[selectedIndex];

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: '#1b1713',
        borderColor: '#3a3229',
        textStyle: { color: '#f7f1e6', fontSize: 12 },
        formatter: (params: any) => {
          const d = params?.data;
          if (!d) return '';
          if (d.kind === 'initial') {
            return `<b>Initial Portfolio</b><br/>${AXIS_LABELS[xKey]}: <b>${d.value[0].toFixed(2)}</b><br/>${AXIS_LABELS[yKey]}: <b>${d.value[1].toFixed(2)}</b>`;
          }
          return `<b>Frontier Point #${d.pointIndex + 1}</b> (click to inspect)<br/>${AXIS_LABELS[xKey]}: <b>${d.value[0].toFixed(2)}</b><br/>${AXIS_LABELS[yKey]}: <b>${d.value[1].toFixed(2)}</b><br/>Mean ROC: <b>${d.roc.toFixed(2)}%</b>`;
        },
      },
      legend: {
        bottom: 0,
        textStyle: { color: '#9a9082', fontSize: 11 },
      },
      grid: { left: isNarrow ? 50 : 62, right: 20, top: 26, bottom: 68 },
      xAxis: {
        type: 'value',
        name: AXIS_LABELS[xKey],
        nameLocation: 'middle',
        nameGap: 24,
        nameTextStyle: { color: '#9a9082', fontSize: 11 },
        axisLabel: { color: '#9a9082', fontSize: 10 },
        splitLine: { lineStyle: { color: '#2a241e' } },
        scale: true,
      },
      yAxis: {
        type: 'value',
        name: isNarrow ? '' : AXIS_LABELS[yKey],
        nameTextStyle: { color: '#9a9082', fontSize: 11 },
        axisLabel: { color: '#9a9082', fontSize: 10 },
        splitLine: { lineStyle: { color: '#2a241e' } },
        scale: true,
      },
      series: [
        {
          name: 'Pareto Frontier',
          type: 'line',
          smooth: 0.25,
          symbol: 'circle',
          symbolSize: 10,
          lineStyle: { color: '#e8a84a', width: 2.5 },
          itemStyle: { color: '#e8a84a', borderColor: '#0c0a08', borderWidth: 1.5 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(232, 168, 74, 0.22)' },
              { offset: 1, color: 'rgba(232, 168, 74, 0.01)' },
            ]),
          },
          data: sorted.map((p) => ({
            value: [p.metrics[xKey], p.metrics[yKey]],
            pointIndex: p.pointIndex,
            roc: p.metrics.meanRocPct,
            kind: 'frontier',
          })),
        },
        ...(sel
          ? [
              {
                name: 'Selected Point',
                type: 'effectScatter',
                symbolSize: 15,
                rippleEffect: { scale: 2.6, brushType: 'stroke' },
                itemStyle: { color: '#f0c57a', borderColor: '#f7f1e6', borderWidth: 2 },
                data: [
                  {
                    value: [sel.metrics[xKey], sel.metrics[yKey]],
                    pointIndex: sel.pointIndex,
                    roc: sel.metrics.meanRocPct,
                    kind: 'selected',
                  },
                ],
                z: 10,
              },
            ]
          : []),
        {
          name: 'Initial Portfolio',
          type: 'scatter',
          symbol: 'diamond',
          symbolSize: 14,
          itemStyle: { color: '#e0706a', borderColor: '#f0a8a4', borderWidth: 1.5 },
          data: [{ value: [initial[xKey], initial[yKey]], kind: 'initial' }],
          z: 9,
        },
      ],
    });

    chart.on('click', (params: any) => {
      if (params?.data && typeof params.data.pointIndex === 'number') {
        onSelectPoint(params.data.pointIndex);
      }
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
  }, [initial, frontier, selectedIndex, xKey, yKey, onSelectPoint]);

  return <div ref={ref} style={{ width: '100%', height: 320, cursor: 'pointer' }} />;
}

export function PortfolioRadarChart({
  initial,
  proposed,
}: {
  initial: PortfolioMetricsSnapshot;
  proposed: PortfolioMetricsSnapshot;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);

    const relPct = (val: number, base: number) =>
      Math.abs(base) > 1e-6 ? Number((((val - base) / Math.abs(base)) * 100).toFixed(1)) : 0;

    const pVals = [
      relPct(proposed.tvar100M, initial.tvar100M),
      relPct(proposed.expectedProfitM, initial.expectedProfitM),
      relPct(proposed.tvar250M, initial.tvar250M),
      relPct(proposed.tvar500M, initial.tvar500M),
      relPct(proposed.tvar1000M, initial.tvar1000M),
      relPct(proposed.riskRewardRatio, initial.riskRewardRatio),
    ];
    const maxAbs = Math.max(40, ...pVals.map((v) => Math.abs(v))) * 1.15;

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        confine: true,
        backgroundColor: '#1b1713',
        borderColor: '#3a3229',
        textStyle: { color: '#f7f1e6', fontSize: 11 },
      },
      legend: {
        bottom: 0,
        textStyle: { color: '#9a9082', fontSize: 11 },
      },
      radar: {
        radius: '64%',
        center: ['50%', '46%'],
        axisName: { color: '#ebe4d8', fontSize: 10 },
        splitArea: { areaStyle: { color: ['rgba(42,36,30,0.25)', 'rgba(20,17,14,0.25)'] } },
        splitLine: { lineStyle: { color: '#3a3229' } },
        axisLine: { lineStyle: { color: '#3a3229' } },
        indicator: [
          { name: 'TVaR 1-in-100', min: -maxAbs, max: maxAbs },
          { name: 'Exp Return', min: -maxAbs, max: maxAbs },
          { name: 'TVaR 1-in-250', min: -maxAbs, max: maxAbs },
          { name: 'TVaR 1-in-500', min: -maxAbs, max: maxAbs },
          { name: 'TVaR 1-in-1000', min: -maxAbs, max: maxAbs },
          { name: 'Risk / Reward', min: -maxAbs, max: maxAbs },
        ],
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              name: 'Initial Portfolio (0%)',
              value: [0, 0, 0, 0, 0, 0],
              lineStyle: { color: '#6b6358', width: 1.5, type: 'dashed' },
              itemStyle: { color: '#6b6358' },
              areaStyle: { color: 'rgba(107, 99, 88, 0.22)' },
            },
            {
              name: 'Proposed (% Δ vs Initial)',
              value: pVals,
              lineStyle: { color: '#e8a84a', width: 2.2 },
              itemStyle: { color: '#e8a84a' },
              areaStyle: { color: 'rgba(232, 168, 74, 0.25)' },
            },
          ],
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
  }, [initial, proposed]);

  return <div ref={ref} style={{ width: '100%', height: 280 }} />;
}

export function ReturnPeriodStackChart({ rows }: { rows: ReturnPeriodStackRow[] }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current || !rows.length) return;
    const chart = echarts.init(ref.current);
    const layerNames = Object.keys(rows[rows.length - 1]?.byLayer ?? {});
    const xLabels = rows.map((r) => `1-in-${r.returnPeriod}`);

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: '#1b1713',
        borderColor: '#3a3229',
        textStyle: { color: '#f7f1e6', fontSize: 11 },
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        textStyle: { color: '#9a9082', fontSize: 10 },
      },
      grid: { left: 48, right: 16, top: 20, bottom: 48 },
      xAxis: {
        type: 'category',
        data: xLabels,
        axisLabel: { color: '#9a9082', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        name: '$M',
        nameTextStyle: { color: '#9a9082', fontSize: 10 },
        axisLabel: { color: '#9a9082', fontSize: 10 },
        splitLine: { lineStyle: { color: '#2a241e' } },
      },
      series: [
        ...layerNames.map((name, idx) => ({
          name,
          type: 'line',
          stack: 'euler',
          areaStyle: { opacity: 0.72 },
          smooth: 0.2,
          showSymbol: false,
          lineStyle: { width: 1 },
          itemStyle: { color: PALETTE[idx % PALETTE.length] },
          data: rows.map((r) => Number((r.byLayer[name] ?? 0).toFixed(2))),
        })),
        {
          name: 'Total Portfolio TVaR',
          type: 'line',
          smooth: 0.2,
          showSymbol: false,
          lineStyle: { color: '#f7f1e6', width: 2, type: 'dashed' },
          itemStyle: { color: '#f7f1e6' },
          data: rows.map((r) => Number(r.totalLossM.toFixed(2))),
          z: 10,
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
  }, [rows]);

  return <div ref={ref} style={{ width: '100%', height: 270 }} />;
}

export function LayerContributionBarChart({
  labels,
  values,
  unit,
  color,
}: {
  labels: string[];
  values: number[];
  unit: '$M' | '%';
  color: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: '#1b1713',
        borderColor: '#3a3229',
        textStyle: { color: '#f7f1e6', fontSize: 11 },
        formatter: (params: any) => {
          const p = params?.[0];
          if (!p) return '';
          return `<b>${p.name}</b><br/>${unit === '$' + 'M' ? `$${Number(p.value).toFixed(2)}m` : `${Number(p.value).toFixed(1)}%`}`;
        },
      },
      grid: { left: 48, right: 14, top: 16, bottom: 56 },
      xAxis: {
        type: 'category',
        data: labels,
        axisLabel: {
          color: '#9a9082',
          fontSize: 10,
          interval: 0,
          rotate: 16,
          formatter: (v: string) => (v.length > 20 ? `${v.slice(0, 19)}…` : v),
        },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          color: '#9a9082',
          fontSize: 10,
          formatter: unit === '%'? '{value}%' : '${value}m',
        },
        splitLine: { lineStyle: { color: '#2a241e' } },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 36,
          itemStyle: {
            color: (params: any) => (Number(params.value) < 0 ? '#5cad7a' : color),
            borderRadius: [4, 4, 0, 0],
          },
          data: values,
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
  }, [labels, values, unit, color]);

  return <div ref={ref} style={{ width: '100%', height: 245 }} />;
}
