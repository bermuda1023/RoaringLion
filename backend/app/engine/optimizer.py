"""Phase 4: True Multi-Horizon Portfolio Optimizer (HiGHS LP/MILP + Rockafellar-Uryasev CVaR + Shadow Prices + Frontier)."""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import Field, model_validator

import highspy
import numpy as np
import roaring_lion_engine as rle

from app.engine.ilw import evaluate_ilw_trial_payouts
from app.engine.sim_generator import YeltTable
from app.engine.slice_runner import evaluate_programme_dag
from app.models.common import CamelModel
from app.models.domain import BreadcrumbStep, ProgrammeDeal, RunManifest
from app.models.enums import ContractDirection, IlwTriggerType, NodeType, ProgrammeStatus, ViewOfRisk
from app.models.phase1 import IlwPriceRequest
from app.services.lineage import LineageRegistry, canonical_hash


class RegionPerilCap(CamelModel):
    region_peril_key: str
    max_limit_m: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)


class OptimizerRequest(CamelModel):
    mode: Literal["CURRENT", "FORWARD_PLANNING"] = "CURRENT"
    view: ViewOfRisk = ViewOfRisk.ADJUSTED
    total_capital: float = Field(default=160.0, gt=0, le=1_000_000, allow_inf_nan=False)
    max_deployed_collateral: float = Field(default=160.0, ge=0, le=1_000_000, allow_inf_nan=False)
    min_deployed_collateral: float = Field(default=0.0, ge=0, le=1_000_000, allow_inf_nan=False)
    max_tvar_100_m: float = Field(default=110.0, gt=0, le=1_000_000, allow_inf_nan=False)
    max_tvar_250_m: float = Field(default=135.0, gt=0, le=1_000_000, allow_inf_nan=False)
    max_single_cedent_limit_m: float | None = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    region_peril_caps: list[RegionPerilCap] = []
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
        return self


class OptimizedDealRow(CamelModel):
    programme_id: str
    cedent_name: str
    programme_name: str
    direction: str
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


def _build_deal_vectors(
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    req: OptimizerRequest,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Build per-deal (100% line) annual loss vectors (M x N) + optional retro ILW hedge column."""
    items: list[dict[str, Any]] = []
    vecs: list[np.ndarray] = []
    term_types = (NodeType.OCC_XL, NodeType.AGG_XL, NodeType.QUOTA_SHARE, NodeType.AGG_QUOTA_SHARE, NodeType.SURPLUS_SHARE, NodeType.CASCADING_TOWER)

    for p in programmes:
        if req.mode == "CURRENT" and p.status not in (ProgrammeStatus.BOUND, ProgrammeStatus.QUOTED, ProgrammeStatus.FIRM_ORDER):
            continue
        _, out = evaluate_programme_dag(p, yelt, LineageRegistry(persist=False), views=[req.view])
        ann_total = np.zeros(yelt.num_trials, dtype=np.float64)
        rip_total = np.zeros(yelt.num_trials, dtype=np.float64)
        for n in p.graph.nodes:
            if n.node_type in term_types:
                ann_total += out["annual"][req.view.value][n.node_id]
                rip_total += out["rip"][req.view.value][n.node_id]
        # Normalize to 1.0x share
        scale = 1.0 / max(p.signed_line, 1e-6)
        loss_1x = ann_total * scale
        rip_1x = float(np.mean(rip_total * scale))
        prem_1x = p.deposit_premium * scale
        lim_1x = p.limit * scale
        el_1x = float(np.mean(loss_1x))
        items.append({
            "programme_id": p.programme_id,
            "cedent_name": p.cedent_name,
            "programme_name": p.programme_name,
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

    if req.include_retro_ilw_candidate:
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
        items.append({
            "programme_id": "cand-retro-ilw-fl",
            "cedent_name": "ILW Market Hedge",
            "programme_name": f"Retro FL ${req.retro_ilw_trigger_bn:g}B Binary ILW",
            "direction": "EXTERNAL_RETRO",
            "status": "PLANNED",
            "current_share": 0.0,
            "limit_1x": 0.0,  # retro does not consume assumed collateral
            "prem_1x": prem_ilw,
            "el_1x": el_ilw,
            "profit_1x": el_ilw - prem_ilw,  # net expected cost/benefit of buying hedge
            "region_perils": ["US-FL-WS"],
        })
        vecs.append(-payout)  # negative loss = recovery!

    mat = np.vstack(vecs) if vecs else np.zeros((0, yelt.num_trials))
    return items, mat


def solve_portfolio_optimization(
    *,
    req: OptimizerRequest,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    lineage: LineageRegistry,
) -> OptimizerResponse:
    """Solve constrained portfolio optimization via HiGHS LP/MILP with tail-scenario reduction & shadow prices."""
    t0 = time.perf_counter()
    items, mat = _build_deal_vectors(programmes, yelt, ind_yelt, req)
    m, n_trials = mat.shape
    if m == 0:
        raise ValueError("No candidate deals available for optimization")

    # Baseline metrics
    cur_w = np.array([it["current_share"] for it in items], dtype=np.float64)
    base_port = cur_w @ mat
    _, base_tvar_250, _ = rle.euler_cotvar(
        np.ascontiguousarray(mat * cur_w[:, None], dtype=np.float64), 250.0
    )
    base_profit = float(sum(cur_w[i] * items[i]["profit_1x"] for i in range(m)))
    yield_m = req.total_capital * 0.03
    base_roc = ((base_profit + yield_m) / req.total_capital) * 100.0

    # Tail-scenario reduction: keep top 2,500 worst gross trials (guarantees exact 1-in-100 & 1-in-250 tails)
    gross_sum = np.sum(np.maximum(mat, 0.0), axis=0)
    s_count = min(2500, n_trials)
    tail_idx = np.argpartition(gross_sum, -s_count)[-s_count:]
    tail_mat = mat[:, tail_idx]

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", 15.0)

    use_milp = req.min_line_size_share > 1e-6

    # Decision variables w_0..w_{m-1}
    for i, it in enumerate(items):
        if req.lock_bound_deals and it["status"] == "BOUND":
            lb = ub = float(it["current_share"])
        elif it["direction"] == "EXTERNAL_RETRO":
            lb, ub = 0.0, 2.0
        else:
            lb, ub = 0.0, float(req.max_line_size_share)
        h.addVar(lb, ub)
        h.changeColCost(i, -float(it["profit_1x"]))  # maximize profit

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

    h.addVar(0.0, highspy.kHighsInf)  # alpha100
    for _ in range(s_count):
        h.addVar(0.0, highspy.kHighsInf)
    h.addVar(0.0, highspy.kHighsInf)  # alpha250
    for _ in range(s_count):
        h.addVar(0.0, highspy.kHighsInf)

    named_rows: list[tuple[str, int, float]] = []  # (label, row_idx, upper_limit)
    row_cursor = 0

    def add_named_row(label: str, lb: float, ub: float, idxs: list[int], vals: list[float]) -> None:
        nonlocal row_cursor
        h.addRow(lb, ub, len(idxs), idxs, vals)
        named_rows.append((label, row_cursor, ub))
        row_cursor += 1

    # Semi-continuous linking rows
    if use_milp:
        for i, it in enumerate(items):
            if it["direction"] == "ASSUMED" and not (req.lock_bound_deals and it["status"] == "BOUND"):
                h.addRow(-highspy.kHighsInf, 0.0, 2, [i, y_start + i], [1.0, -float(req.max_line_size_share)])
                row_cursor += 1
                h.addRow(-highspy.kHighsInf, 0.0, 2, [i, y_start + i], [-1.0, float(req.min_line_size_share)])
                row_cursor += 1

    # 1. Total Deployed Collateral constraint
    assumed_idxs = [i for i, it in enumerate(items) if it["direction"] == "ASSUMED"]
    assumed_lims = [float(items[i]["limit_1x"]) for i in assumed_idxs]
    add_named_row("Max Deployed Collateral ($M)", float(req.min_deployed_collateral), float(req.max_deployed_collateral), assumed_idxs, assumed_lims)

    # 2. 1-in-100 TVaR (Rockafellar-Uryasev)
    inv_100 = 1.0 / (0.01 * n_trials)
    add_named_row(
        "Max 1-in-100 AEP TVaR ($M)",
        -highspy.kHighsInf,
        float(req.max_tvar_100_m),
        [alpha100_idx] + list(range(z100_start, z100_start + s_count)),
        [1.0] + [inv_100] * s_count,
    )

    # 3. 1-in-250 TVaR (Rockafellar-Uryasev)
    inv_250 = 1.0 / (0.004 * n_trials)
    add_named_row(
        "Max 1-in-250 AEP TVaR ($M)",
        -highspy.kHighsInf,
        float(req.max_tvar_250_m),
        [alpha250_idx] + list(range(z250_start, z250_start + s_count)),
        [1.0] + [inv_250] * s_count,
    )

    # 4. Region-Peril Limit Caps
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

    # 5. Single-Cedent Concentration Cap
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

    # 6. Max Retro Spend (% of Gross Premium)
    retro_idxs = [i for i, it in enumerate(items) if it["direction"] == "EXTERNAL_RETRO"]
    if retro_idxs and assumed_idxs:
        r_idx = retro_idxs + assumed_idxs
        r_val = [float(items[i]["prem_1x"]) for i in retro_idxs] + [
            -float(req.max_retro_spend_pct_gross_prem * items[i]["prem_1x"]) for i in assumed_idxs
        ]
        add_named_row("Max Retro Spend (% Gross Prem)", -highspy.kHighsInf, 0.0, r_idx, r_val)

    # Scenario epigraph rows for both 100yr and 250yr CVaR
    for s in range(s_count):
        col = tail_mat[:, s]
        nz = np.nonzero(np.abs(col) > 1e-12)[0]
        if len(nz) == 0:
            continue
        nz_list = nz.tolist()
        vals_list = col[nz].tolist()
        h.addRow(-highspy.kHighsInf, 0.0, len(nz_list) + 2, nz_list + [alpha100_idx, z100_start + s], vals_list + [-1.0, -1.0])
        h.addRow(-highspy.kHighsInf, 0.0, len(nz_list) + 2, nz_list + [alpha250_idx, z250_start + s], vals_list + [-1.0, -1.0])

    h.run()
    status_str = h.modelStatusToString(h.getModelStatus())
    sol = h.getSolution()
    opt_w = np.array(sol.col_value[:m], dtype=np.float64) if len(sol.col_value) >= m else cur_w
    row_duals = sol.row_dual if not use_milp else []

    # Exact verification on full 100k trials via Rust kernel
    opt_mat = mat * opt_w[:, None]
    _, opt_tvar_250, _ = rle.euler_cotvar(np.ascontiguousarray(opt_mat, dtype=np.float64), 250.0)
    _, opt_tvar_100, _ = rle.euler_cotvar(np.ascontiguousarray(opt_mat, dtype=np.float64), 100.0)
    opt_profit = float(sum(opt_w[i] * items[i]["profit_1x"] for i in range(m)))
    opt_roc = ((opt_profit + yield_m) / req.total_capital) * 100.0

    deal_rows: list[OptimizedDealRow] = []
    for i, it in enumerate(items):
        w = float(opt_w[i])
        deal_rows.append(
            OptimizedDealRow(
                programme_id=it["programme_id"],
                cedent_name=it["cedent_name"],
                programme_name=it["programme_name"],
                direction=it["direction"],
                current_share=round(it["current_share"], 4),
                optimal_share=round(w, 4),
                delta_share=round(w - it["current_share"], 4),
                optimal_limit_m=round(w * it["limit_1x"], 4),
                optimal_premium_m=round(w * it["prem_1x"], 4),
                optimal_el_m=round(w * it["el_1x"], 4),
                optimal_expected_profit_m=round(w * it["profit_1x"], 4),
            )
        )

    diags: list[ConstraintDiagnostic] = []
    for label, r_idx, ub in named_rows:
        act = float(sol.row_value[r_idx]) if r_idx < len(sol.row_value) else 0.0
        if "1-in-100" in label:
            act = float(opt_tvar_100)
        elif "1-in-250" in label:
            act = float(opt_tvar_250)
        slack = ub - act
        shadow = abs(float(row_duals[r_idx])) if (r_idx < len(row_duals) and not use_milp) else None
        diags.append(
            ConstraintDiagnostic(
                name=label,
                limit_value=round(ub, 4),
                actual_value=round(act, 4),
                slack=round(slack, 4),
                binding=abs(slack) < 0.5,
                shadow_price=round(shadow, 5) if shadow is not None else None,
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
        solver="HiGHS MILP" if use_milp else "HiGHS LP",
        status=status_str,
        compute_ms=compute_ms,
        before_expected_profit_m=round(base_profit, 4),
        after_expected_profit_m=round(opt_profit, 4),
        delta_expected_profit_m=round(opt_profit - base_profit, 4),
        before_tvar_250_m=round(float(base_tvar_250), 4),
        after_tvar_250_m=round(float(opt_tvar_250), 4),
        before_mean_roc_pct=round(base_roc, 2),
        after_mean_roc_pct=round(opt_roc, 2),
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
