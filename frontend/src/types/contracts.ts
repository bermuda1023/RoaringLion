export type ViewOfRisk = 'UNADJUSTED' | 'ADJUSTED';
export type ContractDirection = 'ASSUMED' | 'INTERNAL_CEDED' | 'EXTERNAL_RETRO';
export type StructureType = 'OCC_XL' | 'AGG_XL' | 'QUOTA_SHARE' | 'STOP_LOSS' | 'CASCADING' | 'ILW' | 'CAT_BOND';
export type InstrumentType = 'UNL' | 'INDUSTRY_INDEX' | 'PARAMETRIC' | 'HYBRID';
export type IlwTriggerType = 'BINARY' | 'PRO_RATA' | 'AGGREGATE' | 'SECOND_EVENT';

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
  direction?: string;
  isHedge?: boolean;
  structure?: string;
  instrument?: string;
  portfolioSnapshotDate?: string | null;
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
  direction?: string;
  isHedge?: boolean;
  layerNodeId: string;
  layerCode?: string;
  layerLabel: string;
  feederNodeId?: string | null;
  feederLayerCode?: string;
  feederLabel?: string;
  rollsUp?: boolean;
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
  direction?: string;
  isHedge?: boolean;
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

export interface PortfolioEpPoint {
  returnPeriod: number;
  probability: number;
  oep: number;
  oepTvar: number;
  aep: number;
  aepTvar: number;
}

export interface PortfolioEpSlice {
  key: string;
  label: string;
  dimension: 'TOTAL' | 'REGION' | 'PERIL' | 'REGION_PERIL' | string;
  aal: number;
  sd: number;
  maxOcc: number;
  maxYear: number;
  epPoints: PortfolioEpPoint[];
}

export interface PortfolioRollupResponse {
  portfolioId: string;
  portfolioName: string;
  view: ViewOfRisk;
  currency: string;
  numTrials: number;
  computeMs: number;
  includeHedges?: boolean;
  hedgeCount?: number;
  totalCapital: number;
  deployedCollateral: number;
  grossPremium: number;
  hedgePremium?: number;
  netPremium?: number;
  expectedRip: number;
  expectedLoss: number;
  expectedHedgeRecovery?: number;
  expectedProfit: number;
  meanRocPct: number;
  noEventRocPct: number;
  breakEvenProbPct: number;
  returnOnTvarPct?: number;
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
  epCurve?: PortfolioEpPoint[];
  epByRegion?: PortfolioEpSlice[];
  epByPeril?: PortfolioEpSlice[];
  epByRegionPeril?: PortfolioEpSlice[];
  layerAllocations: LayerAllocationRow[];
  manifest: RunManifest;
}

export interface PortfolioSliceSpec {
  label: string;
  view?: ViewOfRisk;
  totalCapital?: number;
  includeHedges?: boolean;
  statuses?: string[] | null;
  treatyYears?: number[] | null;
  renewalPeriods?: string[] | null;
  inceptionFrom?: string | null;
  inceptionTo?: string | null;
  regionPerils?: string[] | null;
  programmeIds?: string[] | null;
}

export interface PortfolioCompareRequest {
  portfolioA: PortfolioSliceSpec;
  portfolioB: PortfolioSliceSpec;
}

export interface EpDeltaPoint {
  returnPeriod: number;
  probability: number;
  oepA: number;
  oepB: number;
  deltaOep: number;
  oepTvarA: number;
  oepTvarB: number;
  deltaOepTvar: number;
  aepA: number;
  aepB: number;
  deltaAep: number;
  aepTvarA: number;
  aepTvarB: number;
  deltaAepTvar: number;
}

export interface EpSplitDeltaRow {
  key: string;
  label: string;
  dimension: string;
  aalA: number;
  aalB: number;
  deltaAal: number;
  oep100A: number;
  oep100B: number;
  deltaOep100: number;
  oep250A: number;
  oep250B: number;
  deltaOep250: number;
  aep100A: number;
  aep100B: number;
  deltaAep100: number;
  aep250A: number;
  aep250B: number;
  deltaAep250: number;
  tvar250A: number;
  tvar250B: number;
  deltaTvar250: number;
}

export interface PortfolioDeltaSummary {
  programmeCountA: number;
  programmeCountB: number;
  layerCountA: number;
  layerCountB: number;
  deltaNetPremiumM: number;
  deltaGrossPremiumM: number;
  deltaExpectedLossM: number;
  deltaExpectedProfitM: number;
  deltaMeanRocPct: number;
  deltaNoEventRocPct: number;
  deltaBreakEvenProbPct: number;
  deltaReturnOnTvarPct: number;
  deltaVar250M: number;
  deltaTvar250M: number;
  deltaOep100M: number;
  deltaOep250M: number;
  deltaAep100M: number;
  deltaAep250M: number;
}

export interface PortfolioCompareResponse {
  currency: string;
  portfolioA: PortfolioRollupResponse;
  portfolioB: PortfolioRollupResponse;
  programmeCountA: number;
  programmeCountB: number;
  delta: PortfolioDeltaSummary;
  epDeltas: EpDeltaPoint[];
  regionDeltas: EpSplitDeltaRow[];
  perilDeltas: EpSplitDeltaRow[];
  regionPerilDeltas: EpSplitDeltaRow[];
  manifest: RunManifest;
}

export interface PortfolioComparePreset {
  id: string;
  name: string;
  description: string;
  portfolioA: PortfolioSliceSpec;
  portfolioB: PortfolioSliceSpec;
}

export interface PortfolioCompareContractMeta {
  programmeId: string;
  treatyYear: number;
  status: string;
  direction: string;
  isHedge: boolean;
  inceptionDate: string;
  renewalPeriod: string;
}

export interface PortfolioCompareOptionsResponse {
  treatyYears: number[];
  statuses: { status: string; count: number }[];
  renewalPeriods: string[];
  regionPerils: string[];
  presets: PortfolioComparePreset[];
  contracts: PortfolioCompareContractMeta[];
}

export interface HedgeAccretionSummary {
  hasHedges: boolean;
  hedgeLayerCount: number;
  hedgePremiumM: number;
  expectedHedgeRecoveryM: number;
  netHedgeCostM: number;
  deltaExpectedProfitM: number;
  deltaMeanRocPct: number;
  deltaNoEventRocPct: number;
  deltaBreakEvenProbPct: number;
  deltaVar250M: number;
  deltaTvar250M: number;
  tvar250ReductionM: number;
  grossReturnOnTvarPct: number;
  netReturnOnTvarPct: number;
  deltaReturnOnTvarPct: number;
  costPerDollarTvarSaved?: number | null;
  guidelinesBreachedWithout: number;
  guidelinesBreachedWith: number;
  isAccretive: boolean;
  accretionVerdict: string;
  accretionReason: string;
}

export interface PortfolioHedgeComparisonResponse {
  withHedges: PortfolioRollupResponse;
  withoutHedges: PortfolioRollupResponse;
  accretion: HedgeAccretionSummary;
  manifest: RunManifest;
}

export interface PortfolioSnapshotInfo {
  asOfDate: string | null;
  programmeCount: number;
  programmes: {
    programmeId: string;
    uniqueCode: string;
    cedentName: string;
    programmeName: string;
    treatyYear: number;
    status: string;
    limit: number;
    regionPerils: string[];
  }[];
  regionPerils: string[];
  lossFiles?: {
    fileId: string;
    simSetId: string;
    name: string;
    region: string;
    peril: string;
    regionPeril: string;
    vendor?: string | null;
  }[];
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

/** One Pricing Output bound 1:1 to a CMS layer suggestion. `offset` is from the layer node. */
export interface GraphTemplatePricingOutput {
  nodeType: 'PRICING_OUTPUT';
  label: string;
  params: Record<string, unknown>;
  offset: { x: number; y: number };
}

/**
 * Palette entry. CMS layer suggestions (`source: 'CMS'` and a financial-terms `nodeType`)
 * always include `pricingOutput`. `BLANK` entries are the unlinked canvas palette.
 */
export interface GraphTemplate {
  templateId: string;
  layerCode?: string;
  layerNodeId?: string;
  contractId?: string;
  category: string;
  source?: 'CMS' | 'BLANK';
  label: string;
  nodeType: string;
  params: Record<string, unknown>;
  placement?: 'EXISTING_LAYER' | 'NEW_COPY' | 'BLANK';
  position?: { x: number; y: number } | null;
  pricingOutput?: GraphTemplatePricingOutput | null;
  /** The suggestion's pricing output is already bound, so it is not offered. */
  pricingBound?: boolean;
}

/** `ProgrammeStatus` wire enum, in the order of docs/CONTRACTS.md §5. */
export const PROGRAMME_STATUSES = [
  'SUBMISSION',
  'INDICATIVE',
  'QUOTED',
  'AUTHORIZED',
  'FIRM_ORDER',
  'BOUND',
  'BOUND_FOR_OPS',
  'DECLINED',
  'NTU',
  'EXPIRED',
  'PLANNED',
] as const;

export type ProgrammeStatus = (typeof PROGRAMME_STATUSES)[number];

export type OptimizerMode = 'CURRENT' | 'FORWARD_PLANNING';
export type OptimizerObjective =
  | 'MAX_EXPECTED_PROFIT'
  | 'MAX_RETURN_ON_TVAR_250'
  | 'MAX_RETURN_ON_TVAR_100'
  | 'MAX_RETURN_ON_TVAR_1000'
  | 'MIN_TVAR_250'
  | 'MAX_RISK_ADJUSTED_RETURN';

/** One layer-level decision variable of the optimizer, at 100% share. `status` is the deal's status. */
export interface OptimizerLayerCatalogItem {
  layerKey: string;
  programmeId: string;
  layerId: string;
  cedentName: string;
  programmeName: string;
  layerName: string;
  direction: string;
  status: string;
  regionPerils: string[];
  currentShare: number;
  limit1xM: number;
  premium1xM: number;
  el1xM: number;
  expectedProfit1xM: number;
  rolPct: number;
  defaultMinShare: number;
  defaultMaxShare: number;
}

export interface OptimizerStatusCount {
  status: ProgrammeStatus;
  programmes: number;
  included: boolean;
  defaultIncluded: boolean;
}

/** `GET /api/optimizer/candidates`: one page of the deals a portfolio is built from. */
export interface OptimizerCandidatesPage {
  mode: OptimizerMode;
  view: ViewOfRisk;
  includedStatuses: ProgrammeStatus[];
  defaultStatuses: ProgrammeStatus[];
  usesDefaultStatuses: boolean;
  statusCounts: OptimizerStatusCount[];
  universeProgrammes: number;
  matchedProgrammes: number;
  search: string;
  limit: number;
  offset: number;
  programmeIds: string[];
  layers: OptimizerLayerCatalogItem[];
  retroCandidate: OptimizerLayerCatalogItem | null;
}
