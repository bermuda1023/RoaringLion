export type ViewOfRisk = 'UNADJUSTED' | 'ADJUSTED';

export interface BreadcrumbStep {
  entityType: string;
  entityId: string;
  label: string;
  version?: string | null;
  contentHash?: string | null;
}

export interface RunManifest {
  manifestId: string;
  runType: string;
  createdAt: string;
  actor: string;
  engineVersion: string;
  inputHashes: Record<string, string>;
  parameters: Record<string, unknown>;
  outputHash: string;
  breadcrumb: BreadcrumbStep[];
}

export interface CurvePoint {
  lossThreshold: number;
  factor: number;
}

export interface UpliftRule {
  regionPerilKey: string;
  flatSeverityFactor: number;
  curvePoints: CurvePoint[];
  additiveLoad: number;
  minLoss?: number | null;
  maxLoss?: number | null;
  note?: string | null;
}

export interface UpliftSet {
  upliftSetId: string;
  name: string;
  version: number;
  rules: UpliftRule[];
  contentHash: string;
}

export interface GraphNodeSpec {
  nodeId: string;
  nodeType: string;
  label: string;
  params: Record<string, unknown>;
  position: { x: number; y: number };
}

export interface GraphEdgeSpec {
  edgeId: string;
  source: string;
  target: string;
  sign: number;
  weight: number;
}

export interface GraphSpec {
  graphId: string;
  name: string;
  version: number;
  currency: string;
  nodes: GraphNodeSpec[];
  edges: GraphEdgeSpec[];
  contentHash: string;
}

export interface ProgrammeDeal {
  programmeId: string;
  cedentName: string;
  programmeName: string;
  treatyYear: number;
  renewalPeriod: string;
  office: string;
  underwriter: string;
  broker: string;
  status: string;
  currency: string;
  simSetId: string;
  upliftSet: UpliftSet;
  graph: GraphSpec;
  writtenLine: number;
  signedLine: number;
  depositPremium: number;
  limit: number;
  rol: number;
  collateralRequired: number;
  regionPerils: string[];
}

export interface EpPoint {
  returnPeriod: number;
  probability: number;
  var: number;
  tvar: number;
}

export interface NodeViewMetrics {
  nodeId: string;
  nodeLabel: string;
  nodeType: string;
  view: ViewOfRisk;
  currency: string;
  el: number;
  sd: number;
  maxOcc: number;
  maxYear: number;
  expectedRip: number;
  rol?: number | null;
  elr?: number | null;
  epPoints: EpPoint[];
  pricing?: PricingEconomics | null;
}

export interface PricingEconomics {
  source: 'LINKED' | 'UPSTREAM';
  contractId: string;
  contractCode: string;
  contractName: string;
  layerNodeId: string;
  layerCode?: string;
  layerLabel: string;
  limit?: number | null;
  attachment?: number | null;
  premium: number;
  writtenLine: number;
  signedLine: number;
  ourLimit?: number | null;
  ourPremium: number;
  brokeragePct: number;
  brokerage: number;
  netPremium: number;
  expectedLoss: number;
  expectedRip: number;
  expectedProfit: number;
  lossRatio?: number | null;
  combinedRatio?: number | null;
  rol?: number | null;
  lossOnLine?: number | null;
  multiple?: number | null;
  tvar250: number;
  returnOnTvar?: number | null;
  technicalRol?: number | null;
  priceAdequacy?: number | null;
  warnings: string[];
}

export interface NodeYeltSummary {
  programmeId: string;
  nodeId: string;
  nodeLabel: string;
  view: ViewOfRisk;
  inputs: { nodeId: string; label: string }[];
  graphVersion: number;
  numTrials: number;
  eventRows: number;
  trialsWithLoss: number;
  aal: number;
  sd: number;
  maxEvent: number;
  maxYear: number;
  currency: string;
  topEvents: { trialId: number; eventId: number; dayOfYear: number; regionPeril: string; loss: number }[];
}

export interface DagEvalResponse {
  programmeId: string;
  graphId: string;
  graphVersion: number;
  currency: string;
  computeMs: number;
  cacheHits?: number;
  cacheMisses?: number;
  views: Record<string, NodeViewMetrics[]>;
  manifest: RunManifest;
}

export interface LayerAllocationRow {
  programmeId: string;
  layerId: string;
  layerName: string;
  cedentName: string;
  limit: number;
  premium: number;
  signedLine: number;
  el: number;
  expectedRip: number;
  standaloneTvar250: number;
  eulerCotvar250: number;
  diversificationCreditPct: number;
  marginalRocPct: number;
}

export interface PortfolioRollupResponse {
  portfolioId: string;
  portfolioName: string;
  view: ViewOfRisk;
  currency: string;
  numTrials: number;
  computeMs: number;
  totalCapital: number;
  deployedCollateral: number;
  grossPremium: number;
  expectedRip: number;
  expectedLoss: number;
  expectedProfit: number;
  meanRocPct: number;
  noEventRocPct: number;
  breakEvenProbPct: number;
  portfolioVar250: number;
  portfolioTvar250: number;
  cotvarSumCheckDiff: number;
  cdfCurve: { cumulativeProb: number; rocPct: number }[];
  quantiles: Record<string, number>;
  guidelines: {
    percentile: number;
    label: string;
    thresholdRocPct: number;
    actualRocPct: number;
    passed: boolean;
  }[];
  layerAllocations: LayerAllocationRow[];
  manifest: RunManifest;
}

export interface DeterministicLossRow {
  lossId: string;
  eventName: string;
  occurrenceDatetime: string;
  regionPerilKey: string;
  groundUpLoss: number;
  currency?: string;
}

export interface DeterministicLayerStatement {
  layerId: string;
  layerName: string;
  layerType: string;
  totalSubjectLoss: number;
  totalCededLoss: number;
  totalRipDue: number;
  steps: {
    lossId: string;
    eventName: string;
    occurrenceDatetime: string;
    regionPerilKey: string;
    groundUpLoss: number;
    subjectLoss: number;
    lossToLayer: number;
    cededLoss: number;
    ripDue: number;
    netRetained: number;
    stateBefore: {
      remainingAggLimit: number;
      aggDeductibleEroded: number;
      reinstatementsUsed: number;
      reinstatementsRemaining?: number | null;
      exhausted: boolean;
    };
    stateAfter: {
      remainingAggLimit: number;
      aggDeductibleEroded: number;
      reinstatementsUsed: number;
      reinstatementsRemaining?: number | null;
      exhausted: boolean;
    };
  }[];
}

export interface DeterministicRunResponse {
  programmeId: string;
  view: ViewOfRisk;
  currency: string;
  hoursClause: number;
  totalGroundUp: number;
  totalCeded: number;
  totalRipDue: number;
  totalNetRetained: number;
  layerStatements: DeterministicLayerStatement[];
  manifest: RunManifest;
}
