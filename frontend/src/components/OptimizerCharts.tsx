import * as echarts from 'echarts';
import { chartSeries, chartTheme } from './chartTheme';
import { useEffect, useRef } from 'react';
import {
  AXIS_LABELS,
  activeRiskHorizon,
  canShowTangent,
  computeTangentRay,
  findPeakReturnRiskPoint,
  formatAxisTick,
  formatAxisValue,
  formatRatioDual,
  getAxisPlotValue,
  getRatioValue,
  type XAxisKey,
  type YAxisKey,
} from '../utils/frontierMetrics';
import { fmtMoneyM, fmtPct } from '../utils/format';

export type { XAxisKey, YAxisKey };

export interface PortfolioMetricsSnapshot {
  expectedProfitM: number;
  meanRocPct: number;
  deployedCollateralM: number;
  fundCapitalM?: number;
  capitalUtilizationPct?: number;
  capitalHeadroomM?: number;
  capitalExceeded?: boolean;
  grossPremiumM: number;
  expectedLossM: number;
  elCapitalRatio: number;
  riskRewardRatio: number;
  stdDevM?: number;
  returnToTvar100?: number;
  returnToTvar250?: number;
  returnToTvar500?: number;
  returnToTvar1000?: number;
  returnToStdDev?: number;
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
  limit1xM?: number;
  premium1xM?: number;
  optimalLimitM: number;
  optimalPremiumM: number;
  optimalCapitalM?: number;
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

const PALETTE = chartSeries;
const PEAK_STAR_SYMBOL =
  'path://M12 2l2.9 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l7.1-1.01L12 2z';

function buildPointTooltip(
  title: string,
  metrics: PortfolioMetricsSnapshot,
  xKey: XAxisKey,
  yKey: YAxisKey,
  isPeak: boolean,
  horizonShortLabel: string,
): string {
  const xVal = getAxisPlotValue(metrics, xKey);
  const yVal = getAxisPlotValue(metrics, yKey);
  const r100 = getRatioValue(metrics, 'returnToTvar100');
  const r250 = getRatioValue(metrics, 'returnToTvar250');
  const r1000 = getRatioValue(metrics, 'returnToTvar1000');
  const rSd = getRatioValue(metrics, 'returnToStdDev');
  const peakTag = isPeak
    ? ` <span style="color:${chartTheme.positive};font-weight:700">★ Peak ${horizonShortLabel}</span>`
    : '';
  return [
    `<b>${title}</b>${peakTag}`,
    `${AXIS_LABELS[xKey]}: <b>${formatAxisValue(xKey, xVal)}</b>`,
    `${AXIS_LABELS[yKey]}: <b>${formatAxisValue(yKey, yVal)}</b>`,
    `<span style="color:${chartTheme.text}">Exp Return:</span> <b>${fmtMoneyM(metrics.expectedProfitM, 2)}</b> · <span style="color:${chartTheme.text}">ROC:</span> <b>${fmtPct(metrics.meanRocPct, 2)}</b>`,
    `<span style="color:${chartTheme.text}">Ret/TVaR 100 / 250 / 1000:</span> <b>${formatRatioDual(r100)}</b> · <b>${formatRatioDual(r250)}</b> · <b>${formatRatioDual(r1000)}</b>`,
    ...((metrics.stdDevM ?? 0) > 1e-6
      ? [`<span style="color:${chartTheme.text}">Ret/Std Dev:</span> <b>${formatRatioDual(rSd, true)}</b>`]
      : []),
  ].join('<br/>');
}

export function ParetoFrontierChart({
  initial,
  frontier,
  selectedIndex,
  xKey,
  yKey,
  showTangent = true,
  onSelectPoint,
}: {
  initial: PortfolioMetricsSnapshot;
  frontier: FrontierExplorerPoint[];
  selectedIndex: number;
  xKey: XAxisKey;
  yKey: YAxisKey;
  showTangent?: boolean;
  onSelectPoint: (idx: number) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const isNarrow = ref.current.clientWidth < 460;

    const horizon = activeRiskHorizon(xKey, yKey);
    const feasible = frontier.filter((p) => p.status === 'Optimal' || p.status === 'Feasible');
    const sorted = [...feasible].sort((a, b) => {
      const dx = getAxisPlotValue(a.metrics, xKey) - getAxisPlotValue(b.metrics, xKey);
      if (Math.abs(dx) > 1e-6) return dx;
      return a.metrics.expectedProfitM - b.metrics.expectedProfitM;
    });
    const sel = frontier[selectedIndex];
    const peak = findPeakReturnRiskPoint(frontier, horizon.ratioKey);
    const tangent = showTangent && canShowTangent(xKey, yKey)
      ? computeTangentRay(feasible, initial, xKey, yKey, peak)
      : null;

    const allMetrics = [initial, ...sorted.map((p) => p.metrics)];
    const minPlotX = Math.min(0, ...allMetrics.map((m) => getAxisPlotValue(m, xKey)));
    const minPlotY = Math.min(0, ...allMetrics.map((m) => getAxisPlotValue(m, yKey)));

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: chartTheme.tooltipBg,
        borderColor: chartTheme.axis,
        textStyle: { color: chartTheme.textStrong, fontSize: 11.5 },
        formatter: (params: any) => {
          const d = params?.data;
          if (!d || d.kind === 'tangent') return '';
          if (d.kind === 'initial') {
            return buildPointTooltip('Initial Portfolio', initial, xKey, yKey, false, horizon.shortLabel);
          }
          const pt = frontier[d.pointIndex];
          if (!pt) return '';
          return buildPointTooltip(
            `Frontier Point #${pt.pointIndex + 1} (click to inspect)`,
            pt.metrics,
            xKey,
            yKey,
            peak?.pointIndex === pt.pointIndex,
            horizon.shortLabel,
          );
        },
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        itemGap: 10,
        pageIconColor: chartTheme.text,
        pageTextStyle: { color: chartTheme.text, fontSize: 12 },
        textStyle: { color: chartTheme.text, fontSize: 12 },
      },
      grid: { left: isNarrow ? 54 : 68, right: 24, top: 24, bottom: 70 },
      xAxis: {
        type: 'value',
        name: AXIS_LABELS[xKey],
        nameLocation: 'middle',
        nameGap: 24,
        nameTextStyle: { color: chartTheme.text, fontSize: 11 },
        axisLabel: {
          color: chartTheme.text,
          fontSize: 10,
          formatter: (val: number) => formatAxisTick(xKey, val),
        },
        splitLine: { lineStyle: { color: chartTheme.grid } },
        min: tangent ? minPlotX : undefined,
        scale: !tangent,
      },
      yAxis: {
        type: 'value',
        name: isNarrow ? '' : AXIS_LABELS[yKey],
        nameTextStyle: { color: chartTheme.text, fontSize: 11 },
        axisLabel: {
          color: chartTheme.text,
          fontSize: 10,
          formatter: (val: number) => formatAxisTick(yKey, val),
        },
        splitLine: { lineStyle: { color: chartTheme.grid } },
        min: tangent ? minPlotY : undefined,
        scale: !tangent,
      },
      series: [
        ...(tangent
          ? [
              {
                name: 'Iso-Ratio Tangent',
                type: 'line',
                smooth: false,
                symbol: 'none',
                silent: true,
                lineStyle: { color: chartTheme.positive, width: 1.5, type: 'dashed', opacity: 0.85 },
                endLabel: {
                  show: !isNarrow,
                  formatter: tangent.label,
                  color: chartTheme.positiveSoft,
                  fontSize: 10.5,
                  fontWeight: 600,
                  distance: 4,
                },
                data: tangent.points.map((pt) => ({ value: pt, kind: 'tangent' })),
                z: 4,
              },
            ]
          : []),
        {
          name: 'Frontier',
          type: 'line',
          smooth: false,
          symbol: 'circle',
          symbolSize: 9,
          lineStyle: { color: chartTheme.brass, width: 2.4 },
          itemStyle: { color: chartTheme.brass, borderColor: chartTheme.ink, borderWidth: 1.5 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(226, 177, 90, 0.2)' },
              { offset: 1, color: 'rgba(226, 177, 90, 0.02)' },
            ]),
          },
          data: sorted.map((p) => ({
            value: [getAxisPlotValue(p.metrics, xKey), getAxisPlotValue(p.metrics, yKey)],
            pointIndex: p.pointIndex,
            kind: 'frontier',
          })),
          z: 6,
        },
        ...(peak
          ? [
              {
                name: `Peak ${horizon.shortLabel}`,
                type: 'scatter',
                symbol: PEAK_STAR_SYMBOL,
                symbolSize: 18,
                itemStyle: { color: chartTheme.positive, borderColor: chartTheme.textStrong, borderWidth: 1.5 },
                data: [
                  {
                    value: [getAxisPlotValue(peak.metrics, xKey), getAxisPlotValue(peak.metrics, yKey)],
                    pointIndex: peak.pointIndex,
                    kind: 'peak',
                  },
                ],
                z: 11,
              },
            ]
          : []),
        ...(sel
          ? [
              {
                name: 'Selected',
                type: 'effectScatter',
                symbolSize: 14,
                rippleEffect: { scale: 2.5, brushType: 'stroke' },
                itemStyle: { color: chartTheme.brassSoft, borderColor: chartTheme.textStrong, borderWidth: 2 },
                data: [
                  {
                    value: [getAxisPlotValue(sel.metrics, xKey), getAxisPlotValue(sel.metrics, yKey)],
                    pointIndex: sel.pointIndex,
                    kind: 'selected',
                  },
                ],
                z: 10,
              },
            ]
          : []),
        {
          name: 'Initial',
          type: 'scatter',
          symbol: 'diamond',
          symbolSize: 13,
          itemStyle: { color: chartTheme.negative, borderColor: '#f0a8a4', borderWidth: 1.5 },
          data: [{ value: [getAxisPlotValue(initial, xKey), getAxisPlotValue(initial, yKey)], kind: 'initial' }],
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
  }, [initial, frontier, selectedIndex, xKey, yKey, showTangent, onSelectPoint]);

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
    const rawMax = Math.max(40, ...pVals.map((v) => Math.abs(v))) * 1.15;
    const maxAbs = Math.ceil(rawMax / 20) * 20;

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        confine: true,
        backgroundColor: chartTheme.tooltipBg,
        borderColor: chartTheme.axis,
        textStyle: { color: chartTheme.textStrong, fontSize: 11 },
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        left: 0,
        right: 8,
        textStyle: { color: chartTheme.text, fontSize: 12 },
      },
      radar: {
        radius: '62%',
        center: ['50%', '46%'],
        axisName: { color: chartTheme.text, fontSize: 12 },
        splitArea: { areaStyle: { color: ['rgba(18,36,51,0.55)', 'rgba(8,19,28,0.35)'] } },
        splitLine: { lineStyle: { color: chartTheme.axis } },
        axisLine: { lineStyle: { color: chartTheme.axis } },
        indicator: [
          { name: 'TVaR 1-in-100', min: -maxAbs, max: maxAbs },
          { name: 'Exp Return', min: -maxAbs, max: maxAbs },
          { name: 'TVaR 1-in-250', min: -maxAbs, max: maxAbs },
          { name: 'TVaR 1-in-500', min: -maxAbs, max: maxAbs },
          { name: 'TVaR 1-in-1000', min: -maxAbs, max: maxAbs },
          { name: 'Ret / TVaR 250', min: -maxAbs, max: maxAbs },
        ],
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              name: 'Initial Portfolio (0%)',
              value: [0, 0, 0, 0, 0, 0],
              lineStyle: { color: chartTheme.slate, width: 1.5, type: 'dashed' },
              itemStyle: { color: chartTheme.slate },
              areaStyle: { color: 'rgba(107, 99, 88, 0.22)' },
            },
            {
              name: 'Proposed (% Δ vs Initial)',
              value: pVals,
              lineStyle: { color: chartTheme.brass, width: 2.2 },
              itemStyle: { color: chartTheme.brass },
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
        backgroundColor: chartTheme.tooltipBg,
        borderColor: chartTheme.axis,
        textStyle: { color: chartTheme.textStrong, fontSize: 11 },
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        textStyle: { color: chartTheme.text, fontSize: 12 },
      },
      grid: { left: 48, right: 16, top: 20, bottom: 48 },
      xAxis: {
        type: 'category',
        data: xLabels,
        axisLabel: { color: chartTheme.text, fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        name: '$M',
        nameTextStyle: { color: chartTheme.text, fontSize: 10 },
        axisLabel: { color: chartTheme.text, fontSize: 10 },
        splitLine: { lineStyle: { color: chartTheme.grid } },
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
          lineStyle: { color: chartTheme.textStrong, width: 2, type: 'dashed' },
          itemStyle: { color: chartTheme.textStrong },
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
        backgroundColor: chartTheme.tooltipBg,
        borderColor: chartTheme.axis,
        textStyle: { color: chartTheme.textStrong, fontSize: 11 },
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
          color: chartTheme.text,
          fontSize: 10,
          interval: 0,
          rotate: 16,
          formatter: (v: string) => (v.length > 20 ? `${v.slice(0, 19)}…` : v),
        },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          color: chartTheme.text,
          fontSize: 10,
          formatter: unit === '%'? '{value}%' : '${value}m',
        },
        splitLine: { lineStyle: { color: chartTheme.grid } },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 36,
          itemStyle: {
            color: (params: any) => (Number(params.value) < 0 ? chartTheme.positive : color),
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
