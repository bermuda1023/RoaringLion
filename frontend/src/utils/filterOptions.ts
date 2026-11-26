// Zone/peril choices for a filter, taken from the loss files on the upstream loss source.
// Mirrors backend/app/services/loss_files.py.

import type { GraphNodeSpec, GraphSpec } from '../types/contracts';

export interface LossFileRef {
  fileId?: string;
  simSetId?: string;
  name?: string;
  region: string;
  peril: string;
  regionPeril?: string;
  vendor?: string | null;
}

export interface PerilChoice {
  code: string;
  label: string;
}

export interface FilterChoiceSet {
  zones: string[];
  perils: PerilChoice[];
  regionPerils: string[];
  sourceLabels: string[];
}

const PERIL_LABEL: Record<string, string> = {
  WS: 'WS (Windstorm)',
  EQ: 'EQ (Earthquake)',
  CS: 'CS (Convective Storm)',
  FL: 'FL (Flood)',
  WF: 'WF (Wildfire)',
  FR: 'FR (Fire)',
  WT: 'WT (Winterstorm)',
  TR: 'TR (Terrorism)',
  CY: 'CY (Cyber)',
};

function asFiles(raw: unknown): LossFileRef[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (item): item is LossFileRef =>
      Boolean(item) &&
      typeof item === 'object' &&
      typeof (item as LossFileRef).region === 'string' &&
      typeof (item as LossFileRef).peril === 'string' &&
      Boolean((item as LossFileRef).region) &&
      Boolean((item as LossFileRef).peril),
  );
}

export function lossFilesForSource(
  params: Record<string, unknown>,
  simFiles: Record<string, LossFileRef[]>,
  programmeSimSetId: string,
): LossFileRef[] {
  const explicit = asFiles(params.lossFiles);
  if (explicit.length > 0) return explicit;
  const simSetId = typeof params.simSetId === 'string' && params.simSetId.trim() ? params.simSetId.trim() : programmeSimSetId;
  return simFiles[simSetId] ?? [];
}

function upstreamSources(graph: GraphSpec, startId: string): GraphNodeSpec[] {
  const incoming = new Map<string, string[]>();
  for (const edge of graph.edges) {
    const list = incoming.get(edge.target) ?? [];
    list.push(edge.source);
    incoming.set(edge.target, list);
  }
  const byId = new Map(graph.nodes.map((node) => [node.nodeId, node]));
  const seen = new Set<string>();
  const stack = [...(incoming.get(startId) ?? [])];
  const sources: GraphNodeSpec[] = [];
  while (stack.length > 0) {
    const nodeId = stack.pop() as string;
    if (seen.has(nodeId)) continue;
    seen.add(nodeId);
    const node = byId.get(nodeId);
    if (!node) continue;
    if (node.nodeType === 'LOSS_SOURCE') {
      sources.push(node);
      continue;
    }
    stack.push(...(incoming.get(nodeId) ?? []));
  }
  return sources;
}

export function filterChoices(
  graph: GraphSpec,
  filterId: string,
  simFiles: Record<string, LossFileRef[]>,
  programmeSimSetId: string,
): FilterChoiceSet {
  const sources = upstreamSources(graph, filterId);
  const files = sources.flatMap((source) => lossFilesForSource(source.params, simFiles, programmeSimSetId));
  const zones = new Set<string>();
  const perils = new Set<string>();
  const regionPerils = new Set<string>();
  for (const file of files) {
    zones.add(file.region);
    perils.add(file.peril);
    regionPerils.add(file.regionPeril || `${file.region}-${file.peril}`);
  }
  return {
    zones: [...zones].sort(),
    perils: [...perils].sort().map((code) => ({ code, label: PERIL_LABEL[code] ?? code })),
    regionPerils: [...regionPerils].sort(),
    sourceLabels: sources.map((source) => source.label),
  };
}
