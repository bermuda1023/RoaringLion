"""Core domain models for Phase 0 First Slice."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .common import CamelModel
from .enums import LinkType, LossSourceType, NodeType, PerilCode, ProgrammeStatus, ViewOfRisk


class BreadcrumbStep(CamelModel):
    entity_type: str
    entity_id: str
    label: str
    version: str | None = None
    content_hash: str | None = None


class LineageLink(CamelModel):
    source_id: str
    target_id: str
    link_type: LinkType
    created_at: datetime


class RunManifest(CamelModel):
    manifest_id: str
    run_type: str
    created_at: datetime
    actor: str = "system"
    engine_version: str = "0.1.0-rust"
    input_hashes: dict[str, str]
    parameters: dict[str, Any]
    output_hash: str
    breadcrumb: list[BreadcrumbStep]


class CurvePoint(CamelModel):
    loss_threshold: float
    factor: float


class UpliftRule(CamelModel):
    region_peril_key: str = "ALL"
    flat_severity_factor: float = 1.0
    curve_points: list[CurvePoint] = []
    additive_load: float = 0.0
    min_loss: float | None = None
    max_loss: float | None = None
    note: str | None = None


class UpliftSet(CamelModel):
    uplift_set_id: str
    name: str
    version: int = 1
    rules: list[UpliftRule] = []
    content_hash: str = ""


class SimSetSummary(CamelModel):
    sim_set_id: str
    name: str
    event_catalog_id: str
    num_trials: int
    total_events: int
    currency: str
    region_perils: list[str]
    content_hash: str


class GraphNodeSpec(CamelModel):
    node_id: str
    node_type: NodeType
    label: str
    params: dict[str, Any] = {}
    position: dict[str, float] = {"x": 0.0, "y": 0.0}


class GraphEdgeSpec(CamelModel):
    edge_id: str
    source: str
    target: str
    sign: int = 1  # +1 solid additive, -1 dashed subtractive/inuring
    weight: float = 1.0


class GraphSpec(CamelModel):
    graph_id: str
    name: str
    version: int = 1
    currency: str = "USD"
    nodes: list[GraphNodeSpec]
    edges: list[GraphEdgeSpec]
    content_hash: str = ""


class ProgrammeDeal(CamelModel):
    programme_id: str
    cedent_name: str
    programme_name: str
    treaty_year: int
    renewal_period: str
    office: str
    underwriter: str
    broker: str
    status: ProgrammeStatus
    currency: str = "USD"
    sim_set_id: str
    uplift_set: UpliftSet
    graph: GraphSpec
    written_line: float
    signed_line: float
    deposit_premium: float
    limit: float
    rol: float
    collateral_required: float
    region_perils: list[str]
    perils: list[PerilCode]


class EpPoint(CamelModel):
    return_period: float
    probability: float
    var: float
    tvar: float


class NodeViewMetrics(CamelModel):
    node_id: str
    node_label: str
    node_type: NodeType
    view: ViewOfRisk
    currency: str
    el: float
    sd: float
    max_occ: float
    max_year: float
    expected_rip: float = 0.0
    rol: float | None = None
    elr: float | None = None
    ep_points: list[EpPoint] = []


class DagEvalResponse(CamelModel):
    programme_id: str
    graph_id: str
    graph_version: int
    currency: str
    compute_ms: float
    cache_hits: int = 0
    cache_misses: int = 0
    views: dict[str, list[NodeViewMetrics]]
    manifest: RunManifest


class LayerAllocationRow(CamelModel):
    programme_id: str
    layer_id: str
    layer_name: str
    cedent_name: str
    limit: float
    premium: float
    signed_line: float
    el: float
    expected_rip: float
    standalone_tvar_250: float
    euler_cotvar_250: float
    diversification_credit_pct: float
    marginal_roc_pct: float


class CdfPoint(CamelModel):
    cumulative_prob: float
    roc_pct: float


class GuidelineCheck(CamelModel):
    percentile: float
    label: str
    threshold_roc_pct: float
    actual_roc_pct: float
    passed: bool


class PortfolioRollupResponse(CamelModel):
    portfolio_id: str
    portfolio_name: str
    view: ViewOfRisk
    currency: str
    num_trials: int
    compute_ms: float
    total_capital: float
    deployed_collateral: float
    gross_premium: float
    expected_rip: float
    expected_loss: float
    expected_profit: float
    mean_roc_pct: float
    no_event_roc_pct: float
    break_even_prob_pct: float
    portfolio_var_250: float
    portfolio_tvar_250: float
    cotvar_sum_check_diff: float
    cdf_curve: list[CdfPoint]
    quantiles: dict[str, float]
    guidelines: list[GuidelineCheck]
    layer_allocations: list[LayerAllocationRow]
    manifest: RunManifest


class DeterministicLossRow(CamelModel):
    loss_id: str
    event_name: str
    occurrence_datetime: str
    region_peril_key: str
    ground_up_loss: float
    currency: str = "USD"
    source_type: LossSourceType = LossSourceType.MANUAL
    source_ref: str | None = None


class DeterministicRunRequest(CamelModel):
    programme_id: str
    view: ViewOfRisk = ViewOfRisk.UNADJUSTED
    hours_clause: int = 72
    losses: list[DeterministicLossRow]


class LayerStateSnapshot(CamelModel):
    remaining_agg_limit: float
    agg_deductible_eroded: float
    reinstatements_used: float
    reinstatements_remaining: float | None = None
    exhausted: bool


class DeterministicStepResult(CamelModel):
    loss_id: str
    event_name: str
    occurrence_datetime: str
    region_peril_key: str
    ground_up_loss: float
    subject_loss: float
    loss_to_layer: float
    ceded_loss: float
    rip_due: float
    net_retained: float
    state_before: LayerStateSnapshot
    state_after: LayerStateSnapshot


class DeterministicLayerStatement(CamelModel):
    layer_id: str
    layer_name: str
    layer_type: NodeType
    total_subject_loss: float
    total_ceded_loss: float
    total_rip_due: float
    steps: list[DeterministicStepResult]


class DeterministicRunResponse(CamelModel):
    programme_id: str
    view: ViewOfRisk
    currency: str
    hours_clause: int
    total_ground_up: float
    total_ceded: float
    total_rip_due: float
    total_net_retained: float
    layer_statements: list[DeterministicLayerStatement]
    manifest: RunManifest
