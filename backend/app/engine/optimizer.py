"""Phase 4 + Frontier Explorer: Multi-Horizon Portfolio Optimizer (HiGHS LP/MILP + Layer-Level Bounds + Dynamic Constraints + Full Euler Attribution)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import time
from typing import Any, Literal

import highspy
import numpy as np
import roaring_lion_engine as rle
from pydantic import Field, model_validator

from app.engine.ilw import evaluate_ilw_trial_payouts
from app.engine.pricing_link import LAYER_NODE_TYPES, _layer_share_and_premium, extract_rollup_layers
from app.engine.sim_generator import YeltTable
from app.engine.slice_runner import evaluate_programme_dag, validate_dag_acyclic
from app.models.common import CamelModel
from app.models.domain import BreadcrumbStep, GraphNodeSpec, ProgrammeDeal, RunManifest
from app.models.enums import ContractDirection, IlwTriggerType, NodeType, ProgrammeStatus, ViewOfRisk, is_hedge_direction
from app.models.phase1 import IlwPriceRequest
from app.services.lineage import LineageRegistry, canonical_hash
from app.services.portfolio_snapshot import compute_unl_portfolio_events, select_unl_snapshot_contracts
from app.services.sim_sets import _trim_memory

TERM_NODE_TYPES = (
    NodeType.OCC_XL,
    NodeType.AGG_XL,
    NodeType.QUOTA_SHARE,
    NodeType.AGG_QUOTA_SHARE,
    NodeType.SURPLUS_SHARE,
    NodeType.CASCADING_TOWER,
)
STACK_RETURN_PERIODS = [2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 250.0, 500.0, 1000.0]

OptimizerMode = Literal["CURRENT", "FORWARD_PLANNING"]

# Deal statuses each horizon mode builds its portfolio from when the request names none.
CURRENT_MODE_STATUSES = frozenset({ProgrammeStatus.QUOTED, ProgrammeStatus.FIRM_ORDER, ProgrammeStatus.BOUND})
FORWARD_MODE_EXCLUDED_STATUSES = frozenset({ProgrammeStatus.DECLINED, ProgrammeStatus.NTU, ProgrammeStatus.EXPIRED})
NO_DEALS_MESSAGE = (
    "No programmes match the selected deal statuses, or none of them has a layer that rolls up, "
    "so there is no portfolio to optimize"
)


def default_optimizer_statuses(mode: OptimizerMode) -> list[ProgrammeStatus]:
    """Statuses a mode includes by default, in `ProgrammeStatus` order."""
    if mode == "CURRENT":
        return [s for s in ProgrammeStatus if s in CURRENT_MODE_STATUSES]
    return [s for s in ProgrammeStatus if s not in FORWARD_MODE_EXCLUDED_STATUSES]


def resolve_optimizer_statuses(
    mode: OptimizerMode, included_statuses: list[ProgrammeStatus] | None
) -> list[ProgrammeStatus]:
    """Statuses whose deals form the portfolio: the explicit selection, else the mode's defaults."""
    if included_statuses is None:
        return default_optimizer_statuses(mode)
    chosen = set(included_statuses)
    return [s for s in ProgrammeStatus if s in chosen]


def optimizer_link_target_statuses(included_statuses: list[ProgrammeStatus] | None) -> set[ProgrammeStatus]:
    """Statuses a Pricing Output's linked contract may have for its layer to roll up.

    With an explicit selection a linked layer follows the same filter as every other deal. Without one,
    links resolve as they always have: against every programme that is not declined, NTU or expired.
    """
    if included_statuses is not None:
        return set(included_statuses)
    return set(default_optimizer_statuses("FORWARD_PLANNING"))


class RegionPerilCap(CamelModel):
    region_peril_key: str
    max_limit_m: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)


class LayerParticipationBound(CamelModel):
    layer_key: str
    min_share: float = Field(default=0.0, ge=0, le=3, allow_inf_nan=False)
    max_share: float = Field(default=1.5, ge=0, le=3, allow_inf_nan=False)
    locked: bool = False

    @model_validator(mode="after")
    def _check_bound_order(self):
        if not self.locked and self.min_share > self.max_share:
            raise ValueError(f"minShare must be <= maxShare for {self.layer_key}")
        return self


class TvarCapConstraint(CamelModel):
    return_period: float = Field(ge=5, le=1000, allow_inf_nan=False)
    max_tvar_m: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    region_peril_key: str | None = None


class OptimizerRequest(CamelModel):
    mode: OptimizerMode = "CURRENT"
    # Deal statuses to build the portfolio from. None keeps the mode's defaults; any status may be listed.
    included_statuses: list[ProgrammeStatus] | None = None
    granularity: Literal["DEAL", "LAYER"] = "DEAL"
    objective: Literal["MAX_EXPECTED_PROFIT", "MIN_TVAR_250", "MAX_RISK_ADJUSTED_RETURN"] = "MAX_EXPECTED_PROFIT"
    risk_aversion_lambda: float = Field(default=0.15, ge=0, le=2, allow_inf_nan=False)
    view: ViewOfRisk = ViewOfRisk.ADJUSTED
    total_capital: float = Field(default=160.0, gt=0, le=1_000_000, allow_inf_nan=False)
    max_deployed_collateral: float = Field(default=160.0, ge=0, le=1_000_000, allow_inf_nan=False)
    min_deployed_collateral: float = Field(default=0.0, ge=0, le=1_000_000, allow_inf_nan=False)
    max_tvar_100_m: float = Field(default=110.0, gt=0, le=1_000_000, allow_inf_nan=False)
    max_tvar_250_m: float = Field(default=135.0, gt=0, le=1_000_000, allow_inf_nan=False)
    max_el_capital_ratio: float | None = Field(default=None, gt=0, le=5, allow_inf_nan=False)
    max_single_cedent_limit_m: float | None = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    region_peril_caps: list[RegionPerilCap] = []
    tvar_caps: list[TvarCapConstraint] = []
    layer_bounds: list[LayerParticipationBound] = []
    min_line_size_share: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)  # > 0 activates MILP
    max_line_size_share: float = Field(default=1.5, gt=0, le=3, allow_inf_nan=False)
    lock_bound_deals: bool = False
    include_retro_ilw_candidate: bool = True
    retro_ilw_limit_m: float = Field(default=25.0, ge=0, le=1_000_000, allow_inf_nan=False)
    retro_ilw_rol: float = Field(default=0.20, ge=0, le=5, allow_inf_nan=False)
    retro_ilw_trigger_bn: float = Field(default=40.0, ge=0, allow_inf_nan=False)
    max_retro_spend_pct_gross_prem: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_ranges(self):
        if self.min_deployed_collateral > self.max_deployed_collateral:
            raise ValueError("minDeployedCollateral must be <= maxDeployedCollateral")
        if self.min_line_size_share > self.max_line_size_share:
            raise ValueError("minLineSizeShare must be <= maxLineSizeShare")
        if self.included_statuses is not None:
            if not self.included_statuses:
                raise ValueError("includedStatuses must name at least one status (omit it to use the mode's defaults)")
            self.included_statuses = resolve_optimizer_statuses(self.mode, self.included_statuses)
        return self


class OptimizerLayerCatalogItem(CamelModel):
    layer_key: str
    programme_id: str
    layer_id: str
    cedent_name: str
    programme_name: str
    layer_name: str
    direction: str
    status: str
    region_perils: list[str]
    current_share: float
    limit_1x_m: float
    premium_1x_m: float
    el_1x_m: float
    expected_profit_1x_m: float
    rol_pct: float
    default_min_share: float
    default_max_share: float


class OptimizerStatusCount(CamelModel):
    status: ProgrammeStatus
    programmes: int
    included: bool
    default_included: bool


class OptimizerCandidatesPage(CamelModel):
    """One page of the deals a portfolio would be built from, with each deal's layers."""

    mode: str
    view: str
    included_statuses: list[ProgrammeStatus]
    default_statuses: list[ProgrammeStatus]
    uses_default_statuses: bool
    status_counts: list[OptimizerStatusCount]
    universe_programmes: int
    matched_programmes: int
    search: str
    limit: int
    offset: int
    programme_ids: list[str]
    layers: list[OptimizerLayerCatalogItem]
    retro_candidate: OptimizerLayerCatalogItem | None = None


class OptimizedDealRow(CamelModel):
    programme_id: str
    layer_key: str = ""
    layer_name: str = ""
    cedent_name: str
    programme_name: str
    direction: str
    status: str = ""
    current_share: float
    optimal_share: float
    delta_share: float
    optimal_limit_m: float
    optimal_premium_m: float
    optimal_el_m: float
    optimal_expected_profit_m: float


class ConstraintDiagnostic(CamelModel):
    name: str
    limit_value: float
    actual_value: float
    slack: float
    binding: bool
    shadow_price: float | None = None


class OptimizerResponse(CamelModel):
    mode: str
    solver: str
    status: str
    compute_ms: float
    before_expected_profit_m: float
    after_expected_profit_m: float
    delta_expected_profit_m: float
    before_tvar_250_m: float
    after_tvar_250_m: float
    before_mean_roc_pct: float
    after_mean_roc_pct: float
    included_statuses: list[ProgrammeStatus] = []
    deals: list[OptimizedDealRow]
    constraints: list[ConstraintDiagnostic]
    manifest: RunManifest


class FrontierPoint(CamelModel):
    max_tvar_250_m: float
    status: str
    expected_profit_m: float
    mean_roc_pct: float
    actual_tvar_250_m: float
    deployed_collateral_m: float


class PortfolioMetricsSnapshot(CamelModel):
    expected_profit_m: float
    mean_roc_pct: float
    deployed_collateral_m: float
    gross_premium_m: float
    expected_loss_m: float
    el_capital_ratio: float
    risk_reward_ratio: float
    tvar_50_m: float
    tvar_100_m: float
    tvar_250_m: float
    tvar_500_m: float
    tvar_1000_m: float
    var_100_m: float
    var_250_m: float


class OptimizedLayerRow(CamelModel):
    layer_key: str
    programme_id: str
    layer_id: str
    cedent_name: str
    programme_name: str
    layer_name: str
    direction: str
    status: str = ""
    region_perils: list[str]
    current_share: float
    optimal_share: float
    delta_share: float
    optimal_limit_m: float
    optimal_premium_m: float
    optimal_el_m: float
    optimal_expected_profit_m: float
    co_tvar_100_m: float
    co_tvar_250_m: float
    co_tvar_250_pct: float
    co_var_250_m: float
    co_var_250_pct: float


class ReturnPeriodStackRow(CamelModel):
    return_period: float
    total_loss_m: float
    by_layer: dict[str, float]


class FrontierExplorerPoint(CamelModel):
    point_index: int
    target_tvar_250_m: float
    status: str
    solver: str
    metrics: PortfolioMetricsSnapshot
    layers: list[OptimizedLayerRow]
    constraints: list[ConstraintDiagnostic]
    return_period_contributions: list[ReturnPeriodStackRow]


class FrontierExplorerResponse(CamelModel):
    mode: str
    granularity: str
    view: str
    included_statuses: list[ProgrammeStatus] = []
    compute_ms: float
    initial_portfolio: PortfolioMetricsSnapshot
    initial_layers: list[OptimizedLayerRow]
    recommended_index: int
    frontier: list[FrontierExplorerPoint]
    manifest: RunManifest


# A layer recorded at a 0% line (usual for a declined or NTU deal) is "unplaced".
_UNPLACED_SHARE = 1e-9


def _is_unplaced(node: GraphNodeSpec, programme_signed_line: float) -> bool:
    """True for a layer whose share, as the rollup reads it, is 0%."""
    if node.node_type not in LAYER_NODE_TYPES:
        return False
    return _layer_share_and_premium(node, programme_signed_line)[0] <= _UNPLACED_SHARE


def _at_full_share(node: GraphNodeSpec) -> GraphNodeSpec:
    params = dict(node.params)
    if node.node_type == NodeType.CASCADING_TOWER:
        # The rollup treats a tower as one layer at its first sub-layer's share.
        params["layers"] = [{**l, "placedShare": 1.0} for l in params.get("layers") or []]
    else:
        params["placedShare"] = 1.0
    return node.model_copy(update={"params": params})


def _with_full_share(programme: ProgrammeDeal, layer_ids: frozenset[str] | set[str]) -> ProgrammeDeal:
    nodes = [_at_full_share(n) if n.node_id in layer_ids else n for n in programme.graph.nodes]
    return programme.model_copy(update={"graph": programme.graph.model_copy(update={"nodes": nodes})})


def _evaluate_for_rollup(
    programme: ProgrammeDeal,
    yelt: YeltTable,
    view: ViewOfRisk,
    *,
    industry_yelt: YeltTable | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
    snapshot_hashes_out: dict[str, str] | None = None,
) -> tuple[ProgrammeDeal, dict[str, np.ndarray], dict[str, np.ndarray], set[str]]:
    """Evaluate a programme's graph and make its unplaced layers scalable.

    Losses come out of the graph at each layer's placed share, and the optimizer restates them to 100%
    by dividing by that share. A layer at a 0% line has no loss to restate, which would leave it with
    premium and no risk. So an unplaced layer is re-run at 100%, together with any unplaced layer
    above it, and the result replaces the zero vectors of that layer and of each Pricing Output its
    loss reaches without passing through another layer (directly, or through a combination). Every
    other node, including a held layer and whatever it feeds, keeps the vectors of the stored graph.

    Returns the programme with its unplaced layers at 100%, the annual and RIP vectors by node, and
    the ids of the unplaced layers, whose current share stays 0.
    """
    dag_resp, out = evaluate_programme_dag(
        programme,
        yelt,
        LineageRegistry(persist=False),
        views=[view],
        industry_yelt=industry_yelt,
        portfolio_unl_resolver=portfolio_unl_resolver,
        compute_metrics=False,
        need_events=False,
    )
    if snapshot_hashes_out is not None:
        for k, h in dag_resp.manifest.input_hashes.items():
            if k.startswith("portfolio_snapshot:"):
                snapshot_hashes_out[k] = h
    ann, rip = out["annual"][view.value], out["rip"][view.value]
    unplaced = {n.node_id for n in programme.graph.nodes if _is_unplaced(n, programme.signed_line)}
    if not unplaced:
        return programme, ann, rip, unplaced

    nodes_by_id = {n.node_id: n for n in programme.graph.nodes}
    sources: dict[str, list[str]] = {}
    for e in programme.graph.edges:
        sources.setdefault(e.target, []).append(e.source)

    nothing: frozenset[str] = frozenset()
    above: dict[str, frozenset[str]] = {}  # every unplaced layer upstream of a node
    reached_by: dict[str, frozenset[str]] = {}  # unplaced layers whose loss reaches a node layer-free
    ann, rip = dict(ann), dict(rip)
    runs: dict[frozenset[str], dict[str, dict[str, dict[str, np.ndarray]]]] = {}
    for node_id in validate_dag_acyclic(programme.graph):
        srcs = sources.get(node_id, [])
        above[node_id] = nothing.union(*(above[s] | ({s} & unplaced) for s in srcs))
        if node_id in unplaced:
            reached_by[node_id] = frozenset({node_id})
        elif nodes_by_id[node_id].node_type in LAYER_NODE_TYPES:
            reached_by[node_id] = nothing
        else:
            reached_by[node_id] = nothing.union(*(reached_by[s] for s in srcs))
        # Only layers and Pricing Outputs roll up, so other nodes keep the stored graph's vectors.
        rolls_up = node_id in unplaced or nodes_by_id[node_id].node_type == NodeType.PRICING_OUTPUT
        if not (rolls_up and reached_by[node_id]):
            continue
        at_full = reached_by[node_id].union(*(above[z] for z in reached_by[node_id]))
        if at_full not in runs:
            _, runs[at_full] = evaluate_programme_dag(
                _with_full_share(programme, at_full),
                yelt,
                LineageRegistry(persist=False),
                views=[view],
                industry_yelt=industry_yelt,
                portfolio_unl_resolver=portfolio_unl_resolver,
                compute_metrics=False,
                need_events=False,
            )
        ann[node_id] = runs[at_full]["annual"][view.value][node_id]
        rip[node_id] = runs[at_full]["rip"][view.value][node_id]
    return _with_full_share(programme, unplaced), ann, rip, unplaced


def _build_vectors(
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    req: OptimizerRequest,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
    snapshot_hashes_out: dict[str, str] | None = None,
    *,
    build_matrix: bool = True,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Build 100%-share annual loss vectors (M x N) at either DEAL or LAYER granularity + optional retro ILW.

    `programmes` are the candidate deals; only those whose status is selected (`req.included_statuses`,
    else the mode's defaults) enter the portfolio. `all_programmes` resolves the contract a Pricing Output
    links to, and defaults to `programmes`.
    """
    items: list[dict[str, Any]] = []

    allowed = set(resolve_optimizer_statuses(req.mode, req.included_statuses))
    active_progs = [p for p in programmes if p.status in allowed]
    link_allowed = optimizer_link_target_statuses(req.included_statuses)
    link_source = all_programmes if all_programmes is not None else {p.programme_id: p for p in programmes}
    link_targets = {pid: p for pid, p in link_source.items() if p.status in link_allowed}

    fallback_unl_cache: dict[tuple[str, str], tuple[np.ndarray, str]] = {}

    def _effective_unl_resolver(as_of: str | None, v: ViewOfRisk) -> tuple[np.ndarray, str]:
        if portfolio_unl_resolver is not None:
            return portfolio_unl_resolver(as_of, v)
        key = ((as_of or "").strip(), v.value)
        if key not in fallback_unl_cache:
            unl_deals = select_unl_snapshot_contracts(list(link_source.values()), as_of_date=as_of)
            fallback_unl_cache[key] = compute_unl_portfolio_events(
                unl_deals,
                yelt,
                v,
                all_deals=link_source,
                as_of_date=as_of,
            )
        return fallback_unl_cache[key]

    is_layer_mode = req.granularity == "LAYER"
    has_ilw_cand = bool(req.include_retro_ilw_candidate and req.retro_ilw_limit_m > 0)

    # Evaluate programmes one at a time, extracting each programme's rollup layers immediately
    # so we never hold all programmes' full annual+RIP dicts simultaneously in RAM.
    # Programmes that cross-link another contract (or are cross-linked) are handled in a tiny second pass.
    cross_linked_targets: set[str] = set()
    cross_linkers: set[str] = set()
    for p in active_progs:
        for n in p.graph.nodes:
            if n.node_type == NodeType.PRICING_OUTPUT:
                cid = str(n.params.get("contractId") or "").strip()
                if cid and cid != p.programme_id:
                    cross_linkers.add(p.programme_id)
                    cross_linked_targets.add(cid)
    deferred_ids = cross_linkers | cross_linked_targets

    rollup_entries: list[Any] = []
    deferred_progs: list[ProgrammeDeal] = []
    deferred_ann: dict[str, dict[str, np.ndarray]] = {}
    deferred_rip: dict[str, dict[str, np.ndarray]] = {}
    unplaced_layers: set[tuple[str, str]] = set()

    for p in active_progs:
        needs_unl = any(n.node_type == NodeType.PORTFOLIO_ROLLUP for n in p.graph.nodes)
        restated, ann, rip, unplaced = _evaluate_for_rollup(
            p,
            yelt,
            req.view,
            industry_yelt=ind_yelt,
            portfolio_unl_resolver=_effective_unl_resolver if needs_unl else None,
            snapshot_hashes_out=snapshot_hashes_out,
        )
        unplaced_layers.update((p.programme_id, layer_id) for layer_id in unplaced)
        if p.programme_id in deferred_ids:
            deferred_progs.append(restated)
            deferred_ann[p.programme_id] = ann
            deferred_rip[p.programme_id] = rip
        else:
            local_entries = extract_rollup_layers(
                [restated],
                {p.programme_id: ann},
                {p.programme_id: rip},
                all_programmes=link_targets,
                include_1x=True,
                include_placed=not is_layer_mode,
                include_rip_vecs=False,
                consume_maps=True,
            )
            del ann, rip
            if not build_matrix and is_layer_mode:
                # Catalog-only callers only need scalar EL; drop 100k vectors immediately.
                for e in local_entries:
                    e.expected_rip_1x = e.expected_rip_1x  # preserve
            rollup_entries.extend(local_entries)

    if deferred_progs:
        rollup_entries.extend(
            extract_rollup_layers(
                deferred_progs,
                deferred_ann,
                deferred_rip,
                all_programmes=link_targets,
                include_1x=True,
                include_placed=not is_layer_mode,
                include_rip_vecs=False,
                consume_maps=True,
            )
        )
        deferred_ann.clear()
        deferred_rip.clear()

    for entry in rollup_entries:
        if (entry.programme_id, entry.layer_id) in unplaced_layers:
            # Restated at 100% above; the line actually held is still 0.
            entry.signed_line = 0.0
            entry.limit = 0.0
            entry.premium = 0.0
            entry.expected_rip = 0.0
            if len(entry.loss_vec):
                entry.loss_vec = np.zeros_like(entry.loss_vec_1x)

    if is_layer_mode:
        total_rows = len(rollup_entries) + (1 if has_ilw_cand else 0)
        mat = np.empty((total_rows, yelt.num_trials), dtype=np.float32) if build_matrix else np.zeros((0, 0), dtype=np.float32)
        row_idx = 0
        for i in range(len(rollup_entries)):
            entry = rollup_entries[i]
            rollup_entries[i] = None
            loss_1x = entry.loss_vec_1x
            rip_1x = entry.expected_rip_1x
            el_1x = float(np.mean(loss_1x))
            lkey = f"{entry.programme_id}::{entry.layer_id}"
            items.append({
                "layer_key": lkey,
                "programme_id": entry.programme_id,
                "layer_id": entry.layer_id,
                "cedent_name": entry.cedent_name,
                "programme_name": entry.programme_name,
                "layer_name": entry.layer_name,
                "direction": entry.direction,
                "status": entry.status,
                "current_share": entry.signed_line,
                "limit_1x": 0.0 if entry.is_hedge else entry.limit_1x,
                "prem_1x": entry.prem_1x,
                "el_1x": el_1x,
                "profit_1x": (el_1x - entry.prem_1x - rip_1x) if entry.is_hedge else (entry.prem_1x + rip_1x - el_1x),
                "region_perils": entry.region_perils,
            })
            if build_matrix:
                if entry.is_hedge:
                    mat[row_idx] = -loss_1x
                else:
                    mat[row_idx] = loss_1x
            row_idx += 1
            del entry, loss_1x
        rollup_entries.clear()
    else:
        by_prog: dict[str, list] = {}
        for entry in rollup_entries:
            by_prog.setdefault(entry.programme_id, []).append(entry)
        rollup_entries.clear()
        active_with_entries = [p for p in active_progs if by_prog.get(p.programme_id)]
        total_rows = len(active_with_entries) + (1 if has_ilw_cand else 0)
        mat = np.empty((total_rows, yelt.num_trials), dtype=np.float32) if build_matrix else np.zeros((0, 0), dtype=np.float32)
        row_idx = 0
        for p in active_with_entries:
            p_entries = by_prog.pop(p.programme_id)
            ann_total = np.zeros(yelt.num_trials, dtype=np.float64)
            no_line_held = p.signed_line <= _UNPLACED_SHARE or all(
                entry.signed_line <= _UNPLACED_SHARE for entry in p_entries
            )
            if no_line_held:
                for entry in p_entries:
                    ann_total += entry.loss_vec_1x
                loss_1x = ann_total
                rip_1x = sum(entry.expected_rip_1x for entry in p_entries)
                prem_1x = sum(entry.prem_1x for entry in p_entries)
                lim_1x = sum(entry.limit_1x for entry in p_entries)
            else:
                for entry in p_entries:
                    ann_total += entry.loss_vec
                scale = 1.0 / max(p.signed_line, 1e-6)
                ann_total *= scale
                loss_1x = ann_total
                rip_1x = sum(entry.expected_rip for entry in p_entries) * scale
                prem_1x = p.deposit_premium * scale
                lim_1x = p.limit * scale
            el_1x = float(np.mean(loss_1x))
            p_dir = p.direction.value if hasattr(p.direction, "value") else str(p.direction)
            p_is_hedge = bool(getattr(p, "is_hedge", False))
            items.append({
                "layer_key": p.programme_id,
                "programme_id": p.programme_id,
                "layer_id": p.programme_id,
                "cedent_name": p.cedent_name,
                "programme_name": p.programme_name,
                "layer_name": p.programme_name,
                "direction": p_dir,
                "status": p.status.value,
                "current_share": 0.0 if no_line_held else p.signed_line,
                "limit_1x": 0.0 if p_is_hedge else lim_1x,
                "prem_1x": prem_1x,
                "el_1x": el_1x,
                "profit_1x": (el_1x - prem_1x - rip_1x) if p_is_hedge else (prem_1x + rip_1x - el_1x),
                "region_perils": p.region_perils,
            })
            if build_matrix:
                if p_is_hedge:
                    mat[row_idx] = -loss_1x
                else:
                    mat[row_idx] = loss_1x
            row_idx += 1
            del p_entries, loss_1x

    if has_ilw_cand:
        ilw_req = IlwPriceRequest(
            name="Candidate Retro FL ILW",
            industry_sim_set_id=ind_yelt.sim_set_id,
            region_perils=["US-FL-WS"],
            trigger_type=IlwTriggerType.BINARY,
            trigger_bn=req.retro_ilw_trigger_bn,
            limit_m=req.retro_ilw_limit_m,
            rol=req.retro_ilw_rol,
            direction=ContractDirection.EXTERNAL_RETRO,
            portfolio_view=req.view,
        )
        payout, _, _ = evaluate_ilw_trial_payouts(ind_yelt, ilw_req)
        prem_ilw = req.retro_ilw_limit_m * req.retro_ilw_rol
        el_ilw = float(np.mean(payout))
        ilw_label = f"Retro FL ${req.retro_ilw_trigger_bn:g}B Binary ILW"
        items.append({
            "layer_key": "cand-retro-ilw-fl",
            "programme_id": "cand-retro-ilw-fl",
            "layer_id": "cand-retro-ilw-fl",
            "cedent_name": "ILW Market Hedge",
            "programme_name": ilw_label,
            "layer_name": ilw_label,
            "direction": "EXTERNAL_RETRO",
            "status": "PLANNED",
            "current_share": 0.0,
            "limit_1x": 0.0,
            "prem_1x": prem_ilw,
            "el_1x": el_ilw,
            "profit_1x": el_ilw - prem_ilw,
            "region_perils": ["US-FL-WS"],
        })
        if build_matrix:
            mat[row_idx] = -payout
        row_idx += 1

    return items, mat


def list_optimizable_layers(
    *,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    view: ViewOfRisk = ViewOfRisk.ADJUSTED,
    mode: OptimizerMode = "FORWARD_PLANNING",
    included_statuses: list[ProgrammeStatus] | None = None,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
) -> list[OptimizerLayerCatalogItem]:
    """Return every layer-level decision variable with 1x economics and default participation bounds."""
    req = OptimizerRequest(
        mode=mode,
        granularity="LAYER",
        view=view,
        include_retro_ilw_candidate=True,
        included_statuses=included_statuses,
    )
    items, _ = _build_vectors(
        programmes,
        yelt,
        ind_yelt,
        req,
        all_programmes,
        portfolio_unl_resolver=portfolio_unl_resolver,
        build_matrix=False,
    )
    out: list[OptimizerLayerCatalogItem] = []
    for it in items:
        rol_pct = (it["prem_1x"] / it["limit_1x"]) * 100.0 if it["limit_1x"] > 0 else (req.retro_ilw_rol * 100.0)
        out.append(
            OptimizerLayerCatalogItem(
                layer_key=it["layer_key"],
                programme_id=it["programme_id"],
                layer_id=it["layer_id"],
                cedent_name=it["cedent_name"],
                programme_name=it["programme_name"],
                layer_name=it["layer_name"],
                direction=it["direction"],
                status=it["status"],
                region_perils=it["region_perils"],
                current_share=round(it["current_share"], 4),
                limit_1x_m=round(it["limit_1x"], 4),
                premium_1x_m=round(it["prem_1x"], 4),
                el_1x_m=round(it["el_1x"], 4),
                expected_profit_1x_m=round(it["profit_1x"], 4),
                rol_pct=round(rol_pct, 2),
                default_min_share=0.0,
                default_max_share=2.0 if is_hedge_direction(it["direction"]) else 1.5,
            )
        )
    del items
    _trim_memory()
    return out


def _gross_positive_sum(mat: np.ndarray) -> np.ndarray:
    """Sum positive entries across layers row-by-row without allocating a full-size copy of `mat`."""
    m, n_trials = mat.shape
    total = np.zeros(n_trials, dtype=np.float64)
    for i in range(m):
        row = mat[i]
        if float(row.max()) > 0.0:
            total += np.maximum(row, 0.0)
    return total


def _compute_portfolio_snapshot(
    items: list[dict[str, Any]],
    mat: np.ndarray,
    weights: np.ndarray,
    total_capital: float,
    *,
    w_mat_buf: np.ndarray | None = None,
) -> tuple[PortfolioMetricsSnapshot, list[OptimizedLayerRow], list[ReturnPeriodStackRow]]:
    """Compute exact full-100k metrics, layer Euler Co-TVaR / Co-VaR, and stacked RP attribution via Rust."""
    m, n_trials = mat.shape
    if w_mat_buf is not None and w_mat_buf.shape == mat.shape:
        np.multiply(mat, weights[:, None], out=w_mat_buf)
        w_mat = w_mat_buf
    else:
        w_mat = np.ascontiguousarray(mat * weights[:, None], dtype=np.float64)
    port_loss = w_mat.sum(axis=0)

    # Compute every required return period in a single Rust parallel sort.
    multi = rle.euler_cotvar_multi(w_mat, STACK_RETURN_PERIODS)
    by_rp = {rp: res for rp, res in zip(STACK_RETURN_PERIODS, multi)}
    var_50, tvar_50, _ = by_rp[50.0]
    var_100, tvar_100, co_tvar_100 = by_rp[100.0]
    var_250, tvar_250, co_tvar_250 = by_rp[250.0]
    _, tvar_500, _ = by_rp[500.0]
    _, tvar_1000, _ = by_rp[1000.0]

    # Monte Carlo Co-VaR estimator around 1-in-250 VaR (band of 200 trials centered on the VaR rank)
    k_var = max(0, min(n_trials - 1, int(round(0.004 * n_trials)) - 1))
    band_lo = max(0, k_var - 100)
    band_hi = min(n_trials, k_var + 100)
    if band_hi > band_lo and n_trials > 0:
        # Partial partition around the top `band_hi` trials avoids a full O(N log N) Python sort.
        top_k = min(n_trials, band_hi)
        top_idx = np.argpartition(port_loss, n_trials - top_k)[n_trials - top_k :]
        top_sorted = top_idx[np.argsort(port_loss[top_idx])[::-1]]
        band_idx = top_sorted[band_lo:band_hi]
        raw_co_var = w_mat[:, band_idx].mean(axis=1)
    else:
        raw_co_var = np.zeros(m)
    raw_co_var_sum = float(raw_co_var.sum())
    if abs(raw_co_var_sum) > 1e-9 and var_250 > 0:
        co_var_250 = raw_co_var * (float(var_250) / raw_co_var_sum)
    else:
        co_var_250 = np.zeros(m)

    profit_m = float(sum(weights[i] * items[i]["profit_1x"] for i in range(m)))
    yield_m = total_capital * 0.03
    mean_roc = ((profit_m + yield_m) / total_capital) * 100.0
    deployed = float(sum(weights[i] * items[i]["limit_1x"] for i in range(m) if not is_hedge_direction(items[i]["direction"])))
    gross_prem = float(sum(weights[i] * items[i]["prem_1x"] for i in range(m) if not is_hedge_direction(items[i]["direction"])))
    exp_loss = float(np.mean(port_loss))
    el_cap_ratio = max(0.0, exp_loss) / total_capital
    risk_reward = (profit_m / float(tvar_250)) if float(tvar_250) > 1e-6 else 0.0

    snap = PortfolioMetricsSnapshot(
        expected_profit_m=round(profit_m, 4),
        mean_roc_pct=round(mean_roc, 2),
        deployed_collateral_m=round(deployed, 2),
        gross_premium_m=round(gross_prem, 4),
        expected_loss_m=round(exp_loss, 4),
        el_capital_ratio=round(el_cap_ratio, 4),
        risk_reward_ratio=round(risk_reward, 4),
        tvar_50_m=round(float(tvar_50), 4),
        tvar_100_m=round(float(tvar_100), 4),
        tvar_250_m=round(float(tvar_250), 4),
        tvar_500_m=round(float(tvar_500), 4),
        tvar_1000_m=round(float(tvar_1000), 4),
        var_100_m=round(float(var_100), 4),
        var_250_m=round(float(var_250), 4),
    )

    layer_rows: list[OptimizedLayerRow] = []
    for i, it in enumerate(items):
        w = float(weights[i])
        ct250 = float(co_tvar_250[i])
        cv250 = float(co_var_250[i])
        layer_rows.append(
            OptimizedLayerRow(
                layer_key=it["layer_key"],
                programme_id=it["programme_id"],
                layer_id=it["layer_id"],
                cedent_name=it["cedent_name"],
                programme_name=it["programme_name"],
                layer_name=it["layer_name"],
                direction=it["direction"],
                status=it["status"],
                region_perils=it["region_perils"],
                current_share=round(it["current_share"], 4),
                optimal_share=round(w, 4),
                delta_share=round(w - it["current_share"], 4),
                optimal_limit_m=round(w * it["limit_1x"], 4),
                optimal_premium_m=round(w * it["prem_1x"], 4),
                optimal_el_m=round(w * it["el_1x"], 4),
                optimal_expected_profit_m=round(w * it["profit_1x"], 4),
                co_tvar_100_m=round(float(co_tvar_100[i]), 4),
                co_tvar_250_m=round(ct250, 4),
                co_tvar_250_pct=round((ct250 / float(tvar_250)) * 100.0, 2) if float(tvar_250) > 1e-6 else 0.0,
                co_var_250_m=round(cv250, 4),
                co_var_250_pct=round((cv250 / float(var_250)) * 100.0, 2) if float(var_250) > 1e-6 else 0.0,
            )
        )

    name_counts: dict[str, int] = {}
    for it in items:
        name_counts[it["layer_name"]] = name_counts.get(it["layer_name"], 0) + 1
    seen_stack_labels: set[str] = set()
    stack_labels: list[str] = []
    for it in items:
        base = it["layer_name"]
        label = f"{it['cedent_name']} · {base}" if name_counts[base] > 1 else base
        if label in seen_stack_labels:
            label = f"{label} ({it['layer_key']})"
        seen_stack_labels.add(label)
        stack_labels.append(label)

    rp_stack: list[ReturnPeriodStackRow] = []
    for rp in STACK_RETURN_PERIODS:
        _, rp_tvar, rp_co = by_rp[rp]
        rp_stack.append(
            ReturnPeriodStackRow(
                return_period=rp,
                total_loss_m=round(float(rp_tvar), 4),
                by_layer={stack_labels[i]: round(float(rp_co[i]), 4) for i in range(m)},
            )
        )

    return snap, layer_rows, rp_stack


@dataclass
class _HighsModelHandle:
    highs: highspy.Highs
    m: int
    use_milp: bool
    cur_w: np.ndarray
    total_capital: float
    named_rows: list[tuple[str, int, float, str | None]]
    tvar_100_row: int
    tvar_250_row: int


def _build_highs_model(
    *,
    req: OptimizerRequest,
    items: list[dict[str, Any]],
    mat: np.ndarray,
    tail_mat: np.ndarray,
) -> _HighsModelHandle:
    """Build the HiGHS LP/MILP model once so frontier sweeps can update TVaR bounds and warm-start."""
    m, n_trials = mat.shape
    s_count = tail_mat.shape[1]
    cur_w = np.array([it["current_share"] for it in items], dtype=np.float64)

    bounds_by_key = {b.layer_key: b for b in req.layer_bounds}

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", 15.0)

    use_milp = req.min_line_size_share > 1e-6

    # Decision variables w_0..w_{m-1}
    var_bounds: list[tuple[float, float, bool]] = []  # (lb, ub, is_locked)
    for i, it in enumerate(items):
        b = bounds_by_key.get(it["layer_key"]) or bounds_by_key.get(it["programme_id"])
        if b is not None:
            if b.locked:
                lb = ub = float(it["current_share"])
                locked = True
            else:
                lb, ub = float(b.min_share), float(b.max_share)
                locked = abs(ub - lb) < 1e-9
        elif req.lock_bound_deals and it["status"] == "BOUND":
            lb = ub = float(it["current_share"])
            locked = True
        elif is_hedge_direction(it["direction"]):
            lb, ub = 0.0, 2.0
            locked = False
        else:
            lb, ub = 0.0, float(req.max_line_size_share)
            locked = False
        var_bounds.append((lb, ub, locked))
        h.addVar(lb, ub)
        if req.objective == "MIN_TVAR_250":
            h.changeColCost(i, 0.0)
        else:
            h.changeColCost(i, -float(it["profit_1x"]))

    # Optional binary indicators y_i for semi-continuous min line size
    y_start = m
    if use_milp:
        for i in range(m):
            h.addVar(0.0, 1.0)
            h.changeColIntegrality(y_start + i, highspy.HighsVarType.kInteger)

    # CVaR variables for 1-in-100 and 1-in-250
    alpha100_idx = m + (m if use_milp else 0)
    z100_start = alpha100_idx + 1
    alpha250_idx = z100_start + s_count
    z250_start = alpha250_idx + 1

    h.addVar(0.0, highspy.kHighsInf)
    for _ in range(s_count):
        h.addVar(0.0, highspy.kHighsInf)
    h.addVar(0.0, highspy.kHighsInf)
    for _ in range(s_count):
        h.addVar(0.0, highspy.kHighsInf)

    inv_100 = 1.0 / (0.01 * n_trials)
    inv_250 = 1.0 / (0.004 * n_trials)

    if req.objective == "MIN_TVAR_250":
        h.changeColCost(alpha250_idx, 1.0)
        for s in range(s_count):
            h.changeColCost(z250_start + s, inv_250)
    elif req.objective == "MAX_RISK_ADJUSTED_RETURN" and req.risk_aversion_lambda > 0:
        lam = float(req.risk_aversion_lambda)
        h.changeColCost(alpha250_idx, lam)
        for s in range(s_count):
            h.changeColCost(z250_start + s, lam * inv_250)

    # Extra dynamic TVaR constraints (arbitrary return periods, optional region-peril filter)
    next_var = z250_start + s_count
    extra_tvar_specs: list[tuple[TvarCapConstraint, int, int, list[int], float]] = []
    for tc in req.tvar_caps:
        if tc.region_peril_key:
            sub_idxs = [i for i, it in enumerate(items) if tc.region_peril_key in it["region_perils"]]
        else:
            sub_idxs = list(range(m))
        if not sub_idxs:
            continue
        a_idx = next_var
        z_start = a_idx + 1
        h.addVar(0.0, highspy.kHighsInf)
        for _ in range(s_count):
            h.addVar(0.0, highspy.kHighsInf)
        next_var = z_start + s_count
        inv_rp = 1.0 / ((1.0 / tc.return_period) * n_trials)
        extra_tvar_specs.append((tc, a_idx, z_start, sub_idxs, inv_rp))

    named_rows: list[tuple[str, int, float, str | None]] = []
    row_cursor = 0

    def add_named_row(label: str, lb: float, ub: float, idxs: list[int], vals: list[float], tag: str | None = None) -> int:
        nonlocal row_cursor
        idx = row_cursor
        h.addRow(lb, ub, len(idxs), idxs, vals)
        named_rows.append((label, idx, ub, tag))
        row_cursor += 1
        return idx

    # Semi-continuous linking rows
    if use_milp:
        for i, it in enumerate(items):
            lb_i, ub_i, locked_i = var_bounds[i]
            if not is_hedge_direction(it["direction"]) and not locked_i and lb_i == 0.0 and ub_i >= req.min_line_size_share:
                h.addRow(-highspy.kHighsInf, 0.0, 2, [i, y_start + i], [1.0, -float(ub_i)])
                row_cursor += 1
                h.addRow(-highspy.kHighsInf, 0.0, 2, [i, y_start + i], [-1.0, float(req.min_line_size_share)])
                row_cursor += 1

    # 1. Total Deployed Collateral constraint
    assumed_idxs = [i for i, it in enumerate(items) if not is_hedge_direction(it["direction"])]
    assumed_lims = [float(items[i]["limit_1x"]) for i in assumed_idxs]
    if assumed_idxs:
        add_named_row("Max Deployed Collateral ($M)", float(req.min_deployed_collateral), float(req.max_deployed_collateral), assumed_idxs, assumed_lims)

    # 2. 1-in-100 TVaR (Rockafellar-Uryasev)
    tvar_100_row = add_named_row(
        "Max 1-in-100 AEP TVaR ($M)",
        -highspy.kHighsInf,
        float(req.max_tvar_100_m),
        [alpha100_idx] + list(range(z100_start, z100_start + s_count)),
        [1.0] + [inv_100] * s_count,
        tag="tvar_100",
    )

    # 3. 1-in-250 TVaR (Rockafellar-Uryasev)
    tvar_250_row = add_named_row(
        "Max 1-in-250 AEP TVaR ($M)",
        -highspy.kHighsInf,
        float(req.max_tvar_250_m),
        [alpha250_idx] + list(range(z250_start, z250_start + s_count)),
        [1.0] + [inv_250] * s_count,
        tag="tvar_250",
    )

    # 4. EL / Capital Ratio Cap
    if req.max_el_capital_ratio is not None:
        el_coeffs = [
            -float(items[i]["el_1x"]) if is_hedge_direction(items[i]["direction"]) else float(items[i]["el_1x"])
            for i in range(m)
        ]
        add_named_row(
            "Max EL / Capital Ratio",
            -highspy.kHighsInf,
            float(req.max_el_capital_ratio * req.total_capital),
            list(range(m)),
            el_coeffs,
            tag="el_cap",
        )

    # 5. Region-Peril Limit Caps
    for cap in req.region_peril_caps:
        rp_idxs = [i for i in assumed_idxs if cap.region_peril_key in items[i]["region_perils"]]
        if rp_idxs:
            add_named_row(
                f"Region-Peril Cap: {cap.region_peril_key} ($M)",
                -highspy.kHighsInf,
                float(cap.max_limit_m),
                rp_idxs,
                [float(items[i]["limit_1x"]) for i in rp_idxs],
            )

    # 6. Single-Cedent Concentration Cap
    if req.max_single_cedent_limit_m is not None:
        by_cedent: dict[str, list[int]] = {}
        for i in assumed_idxs:
            by_cedent.setdefault(items[i]["cedent_name"], []).append(i)
        for ced, idxs in by_cedent.items():
            add_named_row(
                f"Single Cedent Cap: {ced} ($M)",
                -highspy.kHighsInf,
                float(req.max_single_cedent_limit_m),
                idxs,
                [float(items[i]["limit_1x"]) for i in idxs],
            )

    # 7. Max Retro Spend (% of Gross Premium)
    retro_idxs = [i for i, it in enumerate(items) if is_hedge_direction(it["direction"])]
    if retro_idxs and assumed_idxs:
        r_idx = retro_idxs + assumed_idxs
        r_val = [float(items[i]["prem_1x"]) for i in retro_idxs] + [
            -float(req.max_retro_spend_pct_gross_prem * items[i]["prem_1x"]) for i in assumed_idxs
        ]
        add_named_row("Max Retro Spend (% Gross Prem)", -highspy.kHighsInf, 0.0, r_idx, r_val)

    # 8. Extra dynamic TVaR constraints
    for tc, a_idx, z_start, _, inv_rp in extra_tvar_specs:
        scope = f" [{tc.region_peril_key}]" if tc.region_peril_key else ""
        add_named_row(
            f"Max 1-in-{int(tc.return_period)} TVaR{scope} ($M)",
            -highspy.kHighsInf,
            float(tc.max_tvar_m),
            [a_idx] + list(range(z_start, z_start + s_count)),
            [1.0] + [inv_rp] * s_count,
        )

    # Scenario epigraph rows for 100yr, 250yr, and extra TVaR constraints
    for s in range(s_count):
        col = tail_mat[:, s]
        nz = np.nonzero(np.abs(col) > 1e-12)[0]
        if len(nz) > 0:
            nz_list = nz.tolist()
            vals_list = col[nz].tolist()
            h.addRow(-highspy.kHighsInf, 0.0, len(nz_list) + 2, nz_list + [alpha100_idx, z100_start + s], vals_list + [-1.0, -1.0])
            h.addRow(-highspy.kHighsInf, 0.0, len(nz_list) + 2, nz_list + [alpha250_idx, z250_start + s], vals_list + [-1.0, -1.0])
        for _, a_idx, z_start, sub_idxs, _ in extra_tvar_specs:
            sub_nz = [i for i in sub_idxs if abs(col[i]) > 1e-12]
            if sub_nz:
                h.addRow(
                    -highspy.kHighsInf,
                    0.0,
                    len(sub_nz) + 2,
                    sub_nz + [a_idx, z_start + s],
                    [float(col[i]) for i in sub_nz] + [-1.0, -1.0],
                )

    return _HighsModelHandle(
        highs=h,
        m=m,
        use_milp=use_milp,
        cur_w=cur_w,
        total_capital=req.total_capital,
        named_rows=named_rows,
        tvar_100_row=tvar_100_row,
        tvar_250_row=tvar_250_row,
    )


def _solve_handle(
    handle: _HighsModelHandle,
    *,
    mat: np.ndarray,
    max_tvar_100_m: float | None = None,
    max_tvar_250_m: float | None = None,
    w_mat_buf: np.ndarray | None = None,
) -> tuple[str, str, np.ndarray, list[ConstraintDiagnostic]]:
    """Run HiGHS on an existing model handle (optionally updating TVaR upper bounds)."""
    h = handle.highs
    m = handle.m
    ub_overrides: dict[int, float] = {}
    if max_tvar_100_m is not None:
        h.changeRowBounds(handle.tvar_100_row, -highspy.kHighsInf, float(max_tvar_100_m))
        ub_overrides[handle.tvar_100_row] = float(max_tvar_100_m)
    if max_tvar_250_m is not None:
        h.changeRowBounds(handle.tvar_250_row, -highspy.kHighsInf, float(max_tvar_250_m))
        ub_overrides[handle.tvar_250_row] = float(max_tvar_250_m)

    h.run()
    status_str = h.modelStatusToString(h.getModelStatus())
    sol = h.getSolution()
    if len(sol.col_value) >= m:
        candidate_w = np.array(sol.col_value[:m], dtype=np.float64)
        opt_w = candidate_w if np.all(np.isfinite(candidate_w)) else handle.cur_w
    else:
        opt_w = handle.cur_w
    row_duals = sol.row_dual if not handle.use_milp else []

    if w_mat_buf is not None and w_mat_buf.shape == mat.shape:
        np.multiply(mat, opt_w[:, None], out=w_mat_buf)
        opt_mat = w_mat_buf
    else:
        opt_mat = np.ascontiguousarray(mat * opt_w[:, None], dtype=np.float64)
    multi = rle.euler_cotvar_multi(opt_mat, [100.0, 250.0])
    opt_tvar_100 = float(multi[0][1])
    opt_tvar_250 = float(multi[1][1])

    diags: list[ConstraintDiagnostic] = []
    for label, r_idx, orig_ub, tag in handle.named_rows:
        ub = ub_overrides.get(r_idx, orig_ub)
        raw_act = float(sol.row_value[r_idx]) if r_idx < len(sol.row_value) else 0.0
        act = raw_act if math.isfinite(raw_act) else 0.0
        limit_disp = ub
        if tag == "tvar_100":
            act = opt_tvar_100
        elif tag == "tvar_250":
            act = opt_tvar_250
        elif tag == "el_cap":
            act = act / handle.total_capital
            limit_disp = ub / handle.total_capital
        slack = limit_disp - act
        shadow: float | None = None
        if r_idx < len(row_duals) and not handle.use_milp:
            raw_dual = abs(float(row_duals[r_idx]))
            if math.isfinite(raw_dual):
                shadow = round(raw_dual, 5)
        diags.append(
            ConstraintDiagnostic(
                name=label,
                limit_value=round(limit_disp, 4),
                actual_value=round(act, 4),
                slack=round(slack, 4),
                binding=abs(slack) < (0.01 if tag == "el_cap" else 0.5),
                shadow_price=shadow,
            )
        )

    solver_name = "HiGHS MILP" if handle.use_milp else "HiGHS LP"
    return solver_name, status_str, opt_w, diags


def _solve_core(
    *,
    req: OptimizerRequest,
    items: list[dict[str, Any]],
    mat: np.ndarray,
    tail_mat: np.ndarray,
) -> tuple[str, str, np.ndarray, list[ConstraintDiagnostic]]:
    """Run one HiGHS LP/MILP solve over pre-built vectors and return (solver, status, opt_w, diagnostics)."""
    handle = _build_highs_model(req=req, items=items, mat=mat, tail_mat=tail_mat)
    return _solve_handle(handle, mat=mat)


def solve_portfolio_optimization(
    *,
    req: OptimizerRequest,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    lineage: LineageRegistry,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
) -> OptimizerResponse:
    """Solve constrained portfolio optimization via HiGHS LP/MILP with tail-scenario reduction & shadow prices."""
    t0 = time.perf_counter()
    snapshot_hashes: dict[str, str] = {}
    items, mat = _build_vectors(
        programmes,
        yelt,
        ind_yelt,
        req,
        all_programmes,
        portfolio_unl_resolver=portfolio_unl_resolver,
        snapshot_hashes_out=snapshot_hashes,
    )
    m, n_trials = mat.shape
    if not any(not is_hedge_direction(it["direction"]) for it in items):
        raise ValueError(NO_DEALS_MESSAGE)

    cur_w = np.array([it["current_share"] for it in items], dtype=np.float64)
    w_mat_buf = np.empty(mat.shape, dtype=np.float64)
    np.multiply(mat, cur_w[:, None], out=w_mat_buf, casting="unsafe")
    _, base_tvar_250, _ = rle.euler_cotvar(w_mat_buf, 250.0)
    base_profit = float(sum(cur_w[i] * items[i]["profit_1x"] for i in range(m)))
    yield_m = req.total_capital * 0.03
    base_roc = ((base_profit + yield_m) / req.total_capital) * 100.0

    gross_sum = _gross_positive_sum(mat)
    s_count = min(2500, n_trials)
    tail_idx = np.argpartition(gross_sum, -s_count)[-s_count:]
    tail_mat = mat[:, tail_idx]

    handle = _build_highs_model(req=req, items=items, mat=mat, tail_mat=tail_mat)
    solver_name, status_str, opt_w, diags = _solve_handle(handle, mat=mat, w_mat_buf=w_mat_buf)

    np.multiply(mat, opt_w[:, None], out=w_mat_buf)
    _, opt_tvar_250, _ = rle.euler_cotvar(w_mat_buf, 250.0)
    del w_mat_buf, mat, tail_mat, handle
    _trim_memory()
    opt_profit = float(sum(opt_w[i] * items[i]["profit_1x"] for i in range(m)))
    opt_roc = ((opt_profit + yield_m) / req.total_capital) * 100.0

    deal_rows: list[OptimizedDealRow] = []
    for i, it in enumerate(items):
        w = float(opt_w[i])
        deal_rows.append(
            OptimizedDealRow(
                programme_id=it["programme_id"],
                layer_key=it["layer_key"],
                layer_name=it["layer_name"],
                cedent_name=it["cedent_name"],
                programme_name=it["programme_name"],
                direction=it["direction"],
                status=it["status"],
                current_share=round(it["current_share"], 4),
                optimal_share=round(w, 4),
                delta_share=round(w - it["current_share"], 4),
                optimal_limit_m=round(w * it["limit_1x"], 4),
                optimal_premium_m=round(w * it["prem_1x"], 4),
                optimal_el_m=round(w * it["el_1x"], 4),
                optimal_expected_profit_m=round(w * it["profit_1x"], 4),
            )
        )

    compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    included_statuses = resolve_optimizer_statuses(req.mode, req.included_statuses)
    solve_input_hashes = {"sim_set": yelt.content_hash, "req": canonical_hash(req.model_dump()), **snapshot_hashes}
    solve_breadcrumb = [
        BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash),
        *[
            BreadcrumbStep(
                entity_type="PORTFOLIO_SNAPSHOT",
                entity_id=f"unl-snapshot-{k.split(':', 1)[1]}",
                label=f"UNL Portfolio Snapshot ({k.split(':', 1)[1]})",
                content_hash=h,
            )
            for k, h in sorted(snapshot_hashes.items())
        ],
    ]
    manifest = lineage.record_manifest(
        run_type="PORTFOLIO_OPTIMIZER",
        input_hashes=solve_input_hashes,
        # `included_statuses` is what was asked for (None = the mode's defaults); `effective_statuses` is
        # what that meant at run time, so the record stays readable if the defaults ever change.
        parameters={**req.model_dump(), "effective_statuses": [s.value for s in included_statuses]},
        output_payload={"status": status_str, "after_expected_profit_m": round(opt_profit, 4), "after_tvar_250_m": round(float(opt_tvar_250), 4)},
        breadcrumb=solve_breadcrumb,
    )

    return OptimizerResponse(
        mode=req.mode,
        solver=solver_name,
        status=status_str,
        compute_ms=compute_ms,
        before_expected_profit_m=round(base_profit, 4),
        after_expected_profit_m=round(opt_profit, 4),
        delta_expected_profit_m=round(opt_profit - base_profit, 4),
        before_tvar_250_m=round(float(base_tvar_250), 4),
        after_tvar_250_m=round(float(opt_tvar_250), 4),
        before_mean_roc_pct=round(base_roc, 2),
        after_mean_roc_pct=round(opt_roc, 2),
        included_statuses=included_statuses,
        deals=deal_rows,
        constraints=diags,
        manifest=manifest,
    )


def compute_efficient_frontier(
    *,
    req: OptimizerRequest,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    lineage: LineageRegistry,
    num_points: int = 6,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
) -> list[FrontierPoint]:
    """Sweep 1-in-250 TVaR budgets to generate the Pareto Efficient Frontier."""
    items, mat = _build_vectors(
        programmes, yelt, ind_yelt, req, all_programmes, portfolio_unl_resolver=portfolio_unl_resolver
    )
    m, n_trials = mat.shape
    if not any(not is_hedge_direction(it["direction"]) for it in items):
        raise ValueError(NO_DEALS_MESSAGE)

    gross_sum = _gross_positive_sum(mat)
    s_count = min(2500, n_trials)
    tail_idx = np.argpartition(gross_sum, -s_count)[-s_count:]
    tail_mat = mat[:, tail_idx]
    handle = _build_highs_model(req=req, items=items, mat=mat, tail_mat=tail_mat)
    w_mat_buf = np.empty(mat.shape, dtype=np.float64)
    yield_m = req.total_capital * 0.03

    tvar_grid = np.linspace(max(40.0, req.max_tvar_250_m * 0.45), req.max_tvar_250_m * 1.25, num_points)
    points: list[FrontierPoint] = []
    for cap in tvar_grid:
        _, status_str, opt_w, _ = _solve_handle(
            handle, mat=mat, max_tvar_100_m=float(cap * 0.85), max_tvar_250_m=float(cap), w_mat_buf=w_mat_buf
        )
        np.multiply(mat, opt_w[:, None], out=w_mat_buf, casting="unsafe")
        _, opt_tvar_250, _ = rle.euler_cotvar(w_mat_buf, 250.0)
        opt_profit = float(sum(opt_w[i] * items[i]["profit_1x"] for i in range(m)))
        opt_roc = ((opt_profit + yield_m) / req.total_capital) * 100.0
        dep = float(sum(opt_w[i] * items[i]["limit_1x"] for i in range(m) if not is_hedge_direction(items[i]["direction"])))
        points.append(
            FrontierPoint(
                max_tvar_250_m=round(float(cap), 2),
                status=status_str,
                expected_profit_m=round(opt_profit, 4),
                mean_roc_pct=round(opt_roc, 2),
                actual_tvar_250_m=round(float(opt_tvar_250), 4),
                deployed_collateral_m=round(dep, 2),
            )
        )
    del w_mat_buf, mat, tail_mat, handle
    _trim_memory()
    return points


def explore_efficient_frontier(
    *,
    req: OptimizerRequest,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    lineage: LineageRegistry,
    num_points: int = 12,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
) -> FrontierExplorerResponse:
    """Build full interactive Pareto Frontier Explorer with per-point layer shares, Euler Co-TVaR, Co-VaR, and RP stacks."""
    t0 = time.perf_counter()
    layer_req = req.model_copy(update={"granularity": req.granularity})
    snapshot_hashes: dict[str, str] = {}
    items, mat = _build_vectors(
        programmes,
        yelt,
        ind_yelt,
        layer_req,
        all_programmes,
        portfolio_unl_resolver=portfolio_unl_resolver,
        snapshot_hashes_out=snapshot_hashes,
    )
    m, n_trials = mat.shape
    if not any(not is_hedge_direction(it["direction"]) for it in items):
        raise ValueError(NO_DEALS_MESSAGE)
    included_statuses = resolve_optimizer_statuses(req.mode, req.included_statuses)

    cur_w = np.array([it["current_share"] for it in items], dtype=np.float64)
    w_mat_buf = np.empty(mat.shape, dtype=np.float64)
    init_snap, init_layers, _ = _compute_portfolio_snapshot(items, mat, cur_w, req.total_capital, w_mat_buf=w_mat_buf)

    gross_sum = _gross_positive_sum(mat)
    s_count = min(2500, n_trials)
    tail_idx = np.argpartition(gross_sum, -s_count)[-s_count:]
    tail_mat = mat[:, tail_idx]

    # Sweep TVaR-250 budgets cleanly across a wide range anchored around the user's cap and initial TVaR
    lo_cap = max(25.0, min(req.max_tvar_250_m, init_snap.tvar_250_m) * 0.40)
    hi_cap = max(req.max_tvar_250_m * 1.35, init_snap.tvar_250_m * 1.45)
    tvar_grid = np.linspace(lo_cap, hi_cap, num_points)
    # Ensure the user's exact configured max_tvar_250_m is represented on the frontier
    nearest = int(np.argmin(np.abs(tvar_grid - req.max_tvar_250_m)))
    tvar_grid[nearest] = req.max_tvar_250_m

    # Build HiGHS LP/MILP once and sweep only the TVaR upper bounds so HiGHS warm-starts each point.
    handle = _build_highs_model(req=layer_req, items=items, mat=mat, tail_mat=tail_mat)

    frontier_pts: list[FrontierExplorerPoint] = []
    for idx, cap in enumerate(tvar_grid):
        # Let the 1-in-250 sweep trace a strictly monotonic Pareto frontier without
        # an artificial 1-in-100 kink; explicit user tvar_caps still apply verbatim.
        solver_name, status_str, opt_w, diags = _solve_handle(
            handle, mat=mat, max_tvar_100_m=float(cap), max_tvar_250_m=float(cap), w_mat_buf=w_mat_buf
        )
        snap, layer_rows, rp_stack = _compute_portfolio_snapshot(items, mat, opt_w, req.total_capital, w_mat_buf=w_mat_buf)
        frontier_pts.append(
            FrontierExplorerPoint(
                point_index=idx,
                target_tvar_250_m=round(float(cap), 2),
                status=status_str,
                solver=solver_name,
                metrics=snap,
                layers=layer_rows,
                constraints=diags,
                return_period_contributions=rp_stack,
            )
        )
    del w_mat_buf, mat, tail_mat, handle
    _trim_memory()

    # Pick the user's configured budget point if optimal; else best risk/reward feasible point
    feasible = [p for p in frontier_pts if p.status in ("Optimal", "Feasible")]
    if frontier_pts[nearest].status in ("Optimal", "Feasible"):
        rec_idx = nearest
    elif feasible:
        rec_idx = max(feasible, key=lambda p: p.metrics.risk_reward_ratio).point_index
    else:
        rec_idx = 0

    compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    explore_input_hashes = {"sim_set": yelt.content_hash, "req": canonical_hash(req.model_dump()), **snapshot_hashes}
    explore_breadcrumb = [
        BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash),
        *[
            BreadcrumbStep(
                entity_type="PORTFOLIO_SNAPSHOT",
                entity_id=f"unl-snapshot-{k.split(':', 1)[1]}",
                label=f"UNL Portfolio Snapshot ({k.split(':', 1)[1]})",
                content_hash=h,
            )
            for k, h in sorted(snapshot_hashes.items())
        ],
    ]
    manifest = lineage.record_manifest(
        run_type="FRONTIER_EXPLORER",
        input_hashes=explore_input_hashes,
        parameters={
            "num_points": num_points,
            "granularity": req.granularity,
            "mode": req.mode,
            "view": req.view.value,
            "included_statuses": None if req.included_statuses is None else [s.value for s in req.included_statuses],
            "effective_statuses": [s.value for s in included_statuses],
        },
        output_payload={
            "points": len(frontier_pts),
            "recommended_index": rec_idx,
            "recommended_profit_m": frontier_pts[rec_idx].metrics.expected_profit_m,
            "recommended_tvar_250_m": frontier_pts[rec_idx].metrics.tvar_250_m,
        },
        breadcrumb=explore_breadcrumb,
    )

    return FrontierExplorerResponse(
        mode=req.mode,
        granularity=req.granularity,
        view=req.view.value,
        included_statuses=included_statuses,
        compute_ms=compute_ms,
        initial_portfolio=init_snap,
        initial_layers=init_layers,
        recommended_index=rec_idx,
        frontier=frontier_pts,
        manifest=manifest,
    )
