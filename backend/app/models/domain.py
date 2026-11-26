"""Core domain models for Phase 0 First Slice."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

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
    loss_threshold: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    factor: float = Field(ge=0, le=20, allow_inf_nan=False)


class UpliftRule(CamelModel):
    region_peril_key: str = "ALL"
    flat_severity_factor: float = Field(default=1.0, ge=0, le=20, allow_inf_nan=False)
    curve_points: list[CurvePoint] = []
    additive_load: float = Field(default=0.0, ge=-1_000_000, le=1_000_000, allow_inf_nan=False)
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
    sign: int = Field(default=1, ge=-1, le=1)  # +1 solid additive, -1 dashed subtractive/inuring
    weight: float = Field(default=1.0, ge=0, le=100, allow_inf_nan=False)

    @field_validator("sign")
    @classmethod
    def _sign_not_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("edge sign must be +1 or -1")
        return v


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


class PricingEconomics(CamelModel):
    """Contract-layer economics flowed through a Pricing Output node against its modelled loss stream."""

    source: str  # LINKED (contract chosen on the node) or UPSTREAM (layer feeding the node)
    contract_id: str
    contract_code: str
    contract_name: str
    layer_node_id: str
    layer_code: str = ""
    layer_label: str
    feeder_node_id: str | None = None
    feeder_layer_code: str = ""
    feeder_label: str = ""
    rolls_up: bool = False
    limit: float | None = None
    attachment: float | None = None
    premium: float
    written_line: float
    signed_line: float
    our_limit: float | None = None
    our_premium: float
    brokerage_pct: float
    brokerage: float
    net_premium: float
    expected_loss: float
    expected_rip: float
    expected_profit: float
    loss_ratio: float | None = None
    combined_ratio: float | None = None
    rol: float | None = None
    loss_on_line: float | None = None
    multiple: float | None = None
    tvar_250: float
    return_on_tvar: float | None = None
    technical_rol: float | None = None
    price_adequacy: float | None = None
    warnings: list[str] = []


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
    pricing: PricingEconomics | None = None


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
    # $M units: cap at $1tn ground-up for a single occurrence.
    ground_up_loss: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    currency: str = "USD"
    source_type: LossSourceType = LossSourceType.MANUAL
    source_ref: str | None = None


class DeterministicRunRequest(CamelModel):
    programme_id: str
    view: ViewOfRisk = ViewOfRisk.UNADJUSTED
    hours_clause: int = Field(default=72, ge=0, le=8760)
    losses: list[DeterministicLossRow] = Field(max_length=10_000)


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
