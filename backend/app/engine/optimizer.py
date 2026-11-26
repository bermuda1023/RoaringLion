"""Phase 4 + Frontier Explorer: Multi-Horizon Portfolio Optimizer (HiGHS LP/MILP + Layer-Level Bounds + Dynamic Constraints + Full Euler Attribution)."""

from __future__ import annotations

import time
from typing import Any, Literal

import highspy
import numpy as np
import roaring_lion_engine as rle
from pydantic import Field, model_validator

from app.engine.ilw import evaluate_ilw_trial_payouts
from app.engine.pricing_link import extract_rollup_layers
from app.engine.sim_generator import YeltTable
from app.engine.slice_runner import evaluate_programme_dag
from app.models.common import CamelModel
from app.models.domain import BreadcrumbStep, ProgrammeDeal, RunManifest
from app.models.enums import ContractDirection, IlwTriggerType, NodeType, ProgrammeStatus, ViewOfRisk
from app.models.phase1 import IlwPriceRequest
from app.services.lineage import LineageRegistry, canonical_hash

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
NO_DEALS_MESSAGE = "No programmes match the selected deal statuses, so there is no portfolio to optimize"


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


def _build_vectors(
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    req: OptimizerRequest,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Build 100%-share annual loss vectors (M x N) at either DEAL or LAYER granularity + optional retro ILW.

    `programmes` are the candidate deals; only those whose status is selected (`req.included_statuses`,
    else the mode's defaults) enter the portfolio. `all_programmes` resolves the contract a Pricing Output
    links to, and defaults to `programmes`.
    """
    items: list[dict[str, Any]] = []
    vecs: list[np.ndarray] = []

    allowed = set(resolve_optimizer_statuses(req.mode, req.included_statuses))
    active_progs = [p for p in programmes if p.status in allowed]
    link_allowed = optimizer_link_target_statuses(req.included_statuses)
    link_source = all_programmes if all_programmes is not None else {p.programme_id: p for p in programmes}
    link_targets = {pid: p for pid, p in link_source.items() if p.status in link_allowed}
    ann_by_prog: dict[str, dict[str, np.ndarray]] = {}
    rip_by_prog: dict[str, dict[str, np.ndarray]] = {}
    for p in active_progs:
        _, out = evaluate_programme_dag(p, yelt, LineageRegistry(persist=False), views=[req.view])
        ann_by_prog[p.programme_id] = out["annual"][req.view.value]
        rip_by_prog[p.programme_id] = out["rip"][req.view.value]

    rollup_entries = extract_rollup_layers(
        active_progs,
        ann_by_prog,
        rip_by_prog,
        all_programmes=link_targets,
    )

    if req.granularity == "LAYER":
        for entry in rollup_entries:
            loss_1x = entry.loss_vec_1x
            rip_1x = float(np.mean(entry.rip_vec_1x))
            el_1x = float(np.mean(loss_1x))
            lkey = f"{entry.programme_id}::{entry.layer_id}"
            items.append({
                "layer_key": lkey,
                "programme_id": entry.programme_id,
                "layer_id": entry.layer_id,
                "cedent_name": entry.cedent_name,
                "programme_name": entry.programme_name,
                "layer_name": entry.layer_name,
                "direction": "ASSUMED",
                "status": entry.status,
                "current_share": entry.signed_line,
                "limit_1x": entry.limit_1x,
                "prem_1x": entry.prem_1x,
                "el_1x": el_1x,
                "profit_1x": entry.prem_1x + rip_1x - el_1x,
                "region_perils": entry.region_perils,
            })
            vecs.append(loss_1x)
    else:
        by_prog: dict[str, list] = {}
        for entry in rollup_entries:
            by_prog.setdefault(entry.programme_id, []).append(entry)
        for p in active_progs:
            p_entries = by_prog.get(p.programme_id, [])
            if not p_entries:
                continue
            ann_total = np.zeros(yelt.num_trials, dtype=np.float64)
            rip_total = np.zeros(yelt.num_trials, dtype=np.float64)
            for entry in p_entries:
                ann_total += entry.loss_vec
                rip_total += entry.rip_vec
            scale = 1.0 / max(p.signed_line, 1e-6)
            loss_1x = ann_total * scale
            rip_1x = float(np.mean(rip_total * scale))
            prem_1x = p.deposit_premium * scale
            lim_1x = p.limit * scale
            el_1x = float(np.mean(loss_1x))
            items.append({
                "layer_key": p.programme_id,
                "programme_id": p.programme_id,
                "layer_id": p.programme_id,
                "cedent_name": p.cedent_name,
                "programme_name": p.programme_name,
                "layer_name": p.programme_name,
                "direction": "ASSUMED",
                "status": p.status.value,
                "current_share": p.signed_line,
                "limit_1x": lim_1x,
                "prem_1x": prem_1x,
                "el_1x": el_1x,
                "profit_1x": prem_1x + rip_1x - el_1x,
                "region_perils": p.region_perils,
            })
            vecs.append(loss_1x)

    if req.include_retro_ilw_candidate and req.retro_ilw_limit_m > 0:
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
        vecs.append(-payout)

    mat = np.vstack(vecs) if vecs else np.zeros((0, yelt.num_trials))
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
) -> list[OptimizerLayerCatalogItem]:
    """Return every layer-level decision variable with 1x economics and default participation bounds."""
    req = OptimizerRequest(
        mode=mode,
        granularity="LAYER",
        view=view,
        include_retro_ilw_candidate=True,
        included_statuses=included_statuses,
    )
    items, _ = _build_vectors(programmes, yelt, ind_yelt, req, all_programmes)
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
                default_max_share=2.0 if it["direction"] == "EXTERNAL_RETRO" else 1.5,
            )
        )
    return out


def _compute_portfolio_snapshot(
    items: list[dict[str, Any]],
    mat: np.ndarray,
    weights: np.ndarray,
    total_capital: float,
) -> tuple[PortfolioMetricsSnapshot, list[OptimizedLayerRow], list[ReturnPeriodStackRow]]:
    """Compute exact full-100k metrics, layer Euler Co-TVaR / Co-VaR, and stacked RP attribution via Rust."""
    m, n_trials = mat.shape
    w_mat = np.ascontiguousarray(mat * weights[:, None], dtype=np.float64)
    port_loss = w_mat.sum(axis=0)

    var_50, tvar_50, _ = rle.euler_cotvar(w_mat, 50.0)
    var_100, tvar_100, co_tvar_100 = rle.euler_cotvar(w_mat, 100.0)
    var_250, tvar_250, co_tvar_250 = rle.euler_cotvar(w_mat, 250.0)
    _, tvar_500, _ = rle.euler_cotvar(w_mat, 500.0)
    _, tvar_1000, _ = rle.euler_cotvar(w_mat, 1000.0)

    # Monte Carlo Co-VaR estimator around 1-in-250 VaR (band of 200 trials centered on the VaR rank)
    order_desc = np.argsort(port_loss)[::-1]
    k_var = max(0, min(n_trials - 1, int(round(0.004 * n_trials)) - 1))
    band_lo = max(0, k_var - 100)
    band_hi = min(n_trials, k_var + 100)
    band_idx = order_desc[band_lo:band_hi]
    raw_co_var = w_mat[:, band_idx].mean(axis=1) if len(band_idx) else np.zeros(m)
    raw_co_var_sum = float(raw_co_var.sum())
    if abs(raw_co_var_sum) > 1e-9 and var_250 > 0:
        co_var_250 = raw_co_var * (float(var_250) / raw_co_var_sum)
    else:
        co_var_250 = np.zeros(m)

    profit_m = float(sum(weights[i] * items[i]["profit_1x"] for i in range(m)))
    yield_m = total_capital * 0.03
    mean_roc = ((profit_m + yield_m) / total_capital) * 100.0
    deployed = float(sum(weights[i] * items[i]["limit_1x"] for i in range(m) if items[i]["direction"] == "ASSUMED"))
    gross_prem = float(sum(weights[i] * items[i]["prem_1x"] for i in range(m) if items[i]["direction"] == "ASSUMED"))
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

    rp_stack: list[ReturnPeriodStackRow] = []
    for rp in STACK_RETURN_PERIODS:
        _, rp_tvar, rp_co = rle.euler_cotvar(w_mat, rp)
        rp_stack.append(
            ReturnPeriodStackRow(
                return_period=rp,
                total_loss_m=round(float(rp_tvar), 4),
                by_layer={items[i]["layer_name"]: round(float(rp_co[i]), 4) for i in range(m)},
            )
        )

    return snap, layer_rows, rp_stack


def _solve_core(
    *,
    req: OptimizerRequest,
    items: list[dict[str, Any]],
    mat: np.ndarray,
    tail_mat: np.ndarray,
) -> tuple[str, str, np.ndarray, list[ConstraintDiagnostic]]:
    """Run one HiGHS LP/MILP solve over pre-built vectors and return (solver, status, opt_w, diagnostics)."""
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
        elif it["direction"] == "EXTERNAL_RETRO":
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

    def add_named_row(label: str, lb: float, ub: float, idxs: list[int], vals: list[float], tag: str | None = None) -> None:
        nonlocal row_cursor
        h.addRow(lb, ub, len(idxs), idxs, vals)
        named_rows.append((label, row_cursor, ub, tag))
        row_cursor += 1

    # Semi-continuous linking rows
    if use_milp:
        for i, it in enumerate(items):
            lb_i, ub_i, locked_i = var_bounds[i]
            if it["direction"] == "ASSUMED" and not locked_i and lb_i == 0.0 and ub_i >= req.min_line_size_share:
                h.addRow(-highspy.kHighsInf, 0.0, 2, [i, y_start + i], [1.0, -float(ub_i)])
                row_cursor += 1
                h.addRow(-highspy.kHighsInf, 0.0, 2, [i, y_start + i], [-1.0, float(req.min_line_size_share)])
                row_cursor += 1

    # 1. Total Deployed Collateral constraint
    assumed_idxs = [i for i, it in enumerate(items) if it["direction"] == "ASSUMED"]
    assumed_lims = [float(items[i]["limit_1x"]) for i in assumed_idxs]
    if assumed_idxs:
        add_named_row("Max Deployed Collateral ($M)", float(req.min_deployed_collateral), float(req.max_deployed_collateral), assumed_idxs, assumed_lims)

    # 2. 1-in-100 TVaR (Rockafellar-Uryasev)
    add_named_row(
        "Max 1-in-100 AEP TVaR ($M)",
        -highspy.kHighsInf,
        float(req.max_tvar_100_m),
        [alpha100_idx] + list(range(z100_start, z100_start + s_count)),
        [1.0] + [inv_100] * s_count,
        tag="tvar_100",
    )

    # 3. 1-in-250 TVaR (Rockafellar-Uryasev)
    add_named_row(
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
            float(items[i]["el_1x"]) if items[i]["direction"] == "ASSUMED" else -float(items[i]["el_1x"])
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
    retro_idxs = [i for i, it in enumerate(items) if it["direction"] == "EXTERNAL_RETRO"]
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

    h.run()
    status_str = h.modelStatusToString(h.getModelStatus())
    sol = h.getSolution()
    opt_w = np.array(sol.col_value[:m], dtype=np.float64) if len(sol.col_value) >= m else cur_w
    row_duals = sol.row_dual if not use_milp else []

    opt_mat = np.ascontiguousarray(mat * opt_w[:, None], dtype=np.float64)
    _, opt_tvar_250, _ = rle.euler_cotvar(opt_mat, 250.0)
    _, opt_tvar_100, _ = rle.euler_cotvar(opt_mat, 100.0)

    diags: list[ConstraintDiagnostic] = []
    for label, r_idx, ub, tag in named_rows:
        act = float(sol.row_value[r_idx]) if r_idx < len(sol.row_value) else 0.0
        limit_disp = ub
        if tag == "tvar_100":
            act = float(opt_tvar_100)
        elif tag == "tvar_250":
            act = float(opt_tvar_250)
        elif tag == "el_cap":
            act = act / req.total_capital
            limit_disp = ub / req.total_capital
        slack = limit_disp - act
        shadow = abs(float(row_duals[r_idx])) if (r_idx < len(row_duals) and not use_milp) else None
        diags.append(
            ConstraintDiagnostic(
                name=label,
                limit_value=round(limit_disp, 4),
                actual_value=round(act, 4),
                slack=round(slack, 4),
                binding=abs(slack) < (0.01 if tag == "el_cap" else 0.5),
                shadow_price=round(shadow, 5) if shadow is not None else None,
            )
        )

    solver_name = "HiGHS MILP" if use_milp else "HiGHS LP"
    return solver_name, status_str, opt_w, diags


def solve_portfolio_optimization(
    *,
    req: OptimizerRequest,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    lineage: LineageRegistry,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
) -> OptimizerResponse:
    """Solve constrained portfolio optimization via HiGHS LP/MILP with tail-scenario reduction & shadow prices."""
    t0 = time.perf_counter()
    items, mat = _build_vectors(programmes, yelt, ind_yelt, req, all_programmes)
    m, n_trials = mat.shape
    if not any(it["direction"] == "ASSUMED" for it in items):
        raise ValueError(NO_DEALS_MESSAGE)

    cur_w = np.array([it["current_share"] for it in items], dtype=np.float64)
    _, base_tvar_250, _ = rle.euler_cotvar(np.ascontiguousarray(mat * cur_w[:, None], dtype=np.float64), 250.0)
    base_profit = float(sum(cur_w[i] * items[i]["profit_1x"] for i in range(m)))
    yield_m = req.total_capital * 0.03
    base_roc = ((base_profit + yield_m) / req.total_capital) * 100.0

    gross_sum = np.sum(np.maximum(mat, 0.0), axis=0)
    s_count = min(2500, n_trials)
    tail_idx = np.argpartition(gross_sum, -s_count)[-s_count:]
    tail_mat = mat[:, tail_idx]

    solver_name, status_str, opt_w, diags = _solve_core(req=req, items=items, mat=mat, tail_mat=tail_mat)

    opt_mat = np.ascontiguousarray(mat * opt_w[:, None], dtype=np.float64)
    _, opt_tvar_250, _ = rle.euler_cotvar(opt_mat, 250.0)
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
    manifest = lineage.record_manifest(
        run_type="PORTFOLIO_OPTIMIZER",
        input_hashes={"sim_set": yelt.content_hash, "req": canonical_hash(req.model_dump())},
        parameters=req.model_dump(),
        output_payload={"status": status_str, "after_expected_profit_m": round(opt_profit, 4), "after_tvar_250_m": round(float(opt_tvar_250), 4)},
        breadcrumb=[BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash)],
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
        included_statuses=resolve_optimizer_statuses(req.mode, req.included_statuses),
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
) -> list[FrontierPoint]:
    """Sweep 1-in-250 TVaR budgets to generate the Pareto Efficient Frontier."""
    tvar_grid = np.linspace(max(40.0, req.max_tvar_250_m * 0.45), req.max_tvar_250_m * 1.25, num_points)
    points: list[FrontierPoint] = []
    for cap in tvar_grid:
        sub_req = req.model_copy(update={"max_tvar_250_m": float(cap), "max_tvar_100_m": float(cap * 0.85)})
        res = solve_portfolio_optimization(
            req=sub_req,
            programmes=programmes,
            yelt=yelt,
            ind_yelt=ind_yelt,
            lineage=LineageRegistry(persist=False),
            all_programmes=all_programmes,
        )
        dep = sum(d.optimal_limit_m for d in res.deals if d.direction == "ASSUMED")
        points.append(
            FrontierPoint(
                max_tvar_250_m=round(float(cap), 2),
                status=res.status,
                expected_profit_m=res.after_expected_profit_m,
                mean_roc_pct=res.after_mean_roc_pct,
                actual_tvar_250_m=res.after_tvar_250_m,
                deployed_collateral_m=round(dep, 2),
            )
        )
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
) -> FrontierExplorerResponse:
    """Build full interactive Pareto Frontier Explorer with per-point layer shares, Euler Co-TVaR, Co-VaR, and RP stacks."""
    t0 = time.perf_counter()
    layer_req = req.model_copy(update={"granularity": req.granularity})
    items, mat = _build_vectors(programmes, yelt, ind_yelt, layer_req, all_programmes)
    m, n_trials = mat.shape
    if not any(it["direction"] == "ASSUMED" for it in items):
        raise ValueError(NO_DEALS_MESSAGE)
    included_statuses = resolve_optimizer_statuses(req.mode, req.included_statuses)

    cur_w = np.array([it["current_share"] for it in items], dtype=np.float64)
    init_snap, init_layers, _ = _compute_portfolio_snapshot(items, mat, cur_w, req.total_capital)

    gross_sum = np.sum(np.maximum(mat, 0.0), axis=0)
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

    frontier_pts: list[FrontierExplorerPoint] = []
    for idx, cap in enumerate(tvar_grid):
        sub_req = layer_req.model_copy(
            update={
                "max_tvar_250_m": float(cap),
                # Let the 1-in-250 sweep trace a strictly monotonic Pareto frontier without
                # an artificial 1-in-100 kink; explicit user tvar_caps still apply verbatim.
                "max_tvar_100_m": float(cap),
            }
        )
        solver_name, status_str, opt_w, diags = _solve_core(req=sub_req, items=items, mat=mat, tail_mat=tail_mat)
        snap, layer_rows, rp_stack = _compute_portfolio_snapshot(items, mat, opt_w, req.total_capital)
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

    # Pick the user's configured budget point if optimal; else best risk/reward feasible point
    feasible = [p for p in frontier_pts if p.status in ("Optimal", "Feasible")]
    if frontier_pts[nearest].status in ("Optimal", "Feasible"):
        rec_idx = nearest
    elif feasible:
        rec_idx = max(feasible, key=lambda p: p.metrics.risk_reward_ratio).point_index
    else:
        rec_idx = 0

    compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    manifest = lineage.record_manifest(
        run_type="FRONTIER_EXPLORER",
        input_hashes={"sim_set": yelt.content_hash, "req": canonical_hash(req.model_dump())},
        parameters={
            "num_points": num_points,
            "granularity": req.granularity,
            "mode": req.mode,
            "view": req.view.value,
            "included_statuses": [s.value for s in included_statuses],
        },
        output_payload={
            "points": len(frontier_pts),
            "recommended_index": rec_idx,
            "recommended_profit_m": frontier_pts[rec_idx].metrics.expected_profit_m,
            "recommended_tvar_250_m": frontier_pts[rec_idx].metrics.tvar_250_m,
        },
        breadcrumb=[BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash)],
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
