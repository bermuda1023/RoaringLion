/** ECharts colors for the ink-and-brass desk. Keep series hues far apart. */

export const chartTheme = {
  text: '#9eb2c4',
  textStrong: '#f4f8fb',
  grid: '#1c3042',
  axis: '#3a5670',
  tooltipBg: '#122433',
  tooltipBorder: '#3a5670',
  brass: '#e2b15a',
  brassSoft: '#f0d7a2',
  positive: '#3fbf86',
  positiveSoft: '#8ee0b5',
  negative: '#e36b66',
  blue: '#7eb0e0',
  violet: '#c4a0e8',
  teal: '#5bb49f',
  slate: '#8aa0b5',
  ink: '#08131c',
  cyan: '#5ec2d6',
} as const;

export const chartSeries = [
  chartTheme.brass,
  chartTheme.blue,
  chartTheme.positive,
  chartTheme.negative,
  chartTheme.violet,
  chartTheme.teal,
  chartTheme.cyan,
  chartTheme.slate,
] as const;

export const chartLegendText = {
  color: chartTheme.text,
  fontSize: 12,
  fontFamily: 'IBM Plex Sans, sans-serif',
};
