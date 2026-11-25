"""Phase 3: Collateralized Fund Waterfall, Marginal Capital & Retro Cost Allocation (Gross vs Net)."""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from pydantic import Field

import numpy as np
import roaring_lion_engine as rle

from app.engine.ilw import evaluate_ilw_trial_payouts
from app.engine.pricing_link import extract_rollup_layers
from app.engine.sim_generator import YeltTable
from app.engine.slice_runner import evaluate_programme_dag
from app.models.common import CamelModel
from app.models.domain import BreadcrumbStep, ProgrammeDeal, RunManifest
from app.models.enums import ContractDirection, IlwTriggerType, NodeType, ViewOfRisk
from app.models.phase1 import IlwPriceRequest
from app.services.lineage import LineageRegistry, canonical_hash


class RetroAllocationRequest(CamelModel):
    view: ViewOfRisk = ViewOfRisk.ADJUSTED
    total_capital: float = Field(default=160.0, gt=0, le=1_000_000, allow_inf_nan=False)
    allocation_method: Literal["TAIL_RELIEF_COTVAR", "EXPECTED_RECOVERY", "HYBRID_RECOVERY_TAIL"] = "TAIL_RELIEF_COTVAR"
    hybrid_tail_weight: float = Field(default=0.70, ge=0, le=1, allow_inf_nan=False)
    # Optional retro ILW hedge overlay
    retro_ilw_region_perils: list[str] = ["US-FL-WS"]
    retro_ilw_trigger_bn: float = Field(default=40.0, ge=0, allow_inf_nan=False)
    retro_ilw_limit_m: float = Field(default=25.0, ge=0, le=1_000_000, allow_inf_nan=False)
    retro_ilw_rol: float = Field(default=0.20, ge=0, le=5, allow_inf_nan=False)
    # Collateralized fund fee & holdback waterfall assumptions
    amf_pct: float = Field(default=0.015, ge=0, le=1, allow_inf_nan=False)
    perf_fee_pct: float = Field(default=0.15, ge=0, le=1, allow_inf_nan=False)
    perf_hurdle_pct: float = Field(default=0.08, ge=0, le=1, allow_inf_nan=False)
    collateral_yield_pct: float = Field(default=0.045, ge=0, le=1, allow_inf_nan=False)
    holdback_buffer_ratio: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)  # Fraction of tail loss trapped


class GrossNetLayerRow(CamelModel):
    programme_id: str
    layer_id: str
    layer_name: str
    cedent_name: str
    gross_premium: float
    gross_el: float
    gross_cotvar_250: float
    gross_mroc_pct: float
    allocated_retro_cost: float
    allocated_retro_recovery: float
    net_retro_leakage: float
    retro_tvar_relief: float
    net_cotvar_250: float
    net_mroc_pct: float
    accretive_net: bool


class FundWaterfallSummary(CamelModel):
    total_capital: float
    gross_premium: float
    expected_rip: float
    retro_premium: float
    expected_retro_recovery: float
    collateral_yield: float
    amf_fee: float
    expected_perf_fee: float
    expected_gross_loss: float
    expected_net_loss: float
    expected_trapped_collateral: float
    gross_mean_roc_pct: float
    net_mean_roc_pct: float
    net_effective_irr_pct: float
    gross_tvar_250: float
    net_tvar_250: float


class RetroAllocationResponse(CamelModel):
    view: ViewOfRisk
    allocation_method: str
    currency: str
    compute_ms: float
    retro_cost_conservation_error: float
    waterfall: FundWaterfallSummary
    layers: list[GrossNetLayerRow]
    manifest: RunManifest


def compute_gross_net_retro_allocation(
    *,
    req: RetroAllocationRequest,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    ind_yelt: YeltTable,
    lineage: LineageRegistry,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
) -> RetroAllocationResponse:
    """Compute full Gross vs Net-of-Retro layer economics, Euler Co-TVaR relief, and fund IRR waterfall."""
    t0 = time.perf_counter()
    layer_meta: list[dict[str, Any]] = []
    layer_vecs: list[np.ndarray] = []
    rip_vecs: list[np.ndarray] = []

    ann_by_prog: dict[str, dict[str, np.ndarray]] = {}
    rip_by_prog: dict[str, dict[str, np.ndarray]] = {}
    for prog in programmes:
        _, vecs = evaluate_programme_dag(prog, yelt, LineageRegistry(persist=False), views=[req.view])
        ann_by_prog[prog.programme_id] = vecs["annual"][req.view.value]
        rip_by_prog[prog.programme_id] = vecs["rip"][req.view.value]

    for entry in extract_rollup_layers(programmes, ann_by_prog, rip_by_prog, all_programmes=all_programmes):
        layer_vecs.append(entry.loss_vec)
        rip_vecs.append(entry.rip_vec)
        layer_meta.append({
            "programme_id": entry.programme_id,
            "layer_id": entry.layer_id,
            "layer_name": entry.layer_name,
            "cedent_name": entry.cedent_name,
            "premium": entry.premium,
        })

    mat = np.vstack(layer_vecs) if layer_vecs else np.zeros((0, yelt.num_trials))
    rip_mat = np.vstack(rip_vecs) if rip_vecs else np.zeros((0, yelt.num_trials))
    m = mat.shape[0]
    gross_port = mat.sum(axis=0)
    port_rip = rip_mat.sum(axis=0)

    # Evaluate retro ILW recovery vector ($M)
    retro_req = IlwPriceRequest(
        name="Portfolio Retro ILW",
        industry_sim_set_id=ind_yelt.sim_set_id,
        region_perils=req.retro_ilw_region_perils,
        trigger_type=IlwTriggerType.BINARY,
        trigger_bn=req.retro_ilw_trigger_bn,
        limit_m=req.retro_ilw_limit_m,
        rol=req.retro_ilw_rol,
        direction=ContractDirection.EXTERNAL_RETRO,
        portfolio_view=req.view,
    )
    retro_rec_vec, _, _ = evaluate_ilw_trial_payouts(ind_yelt, retro_req)
    retro_prem = req.retro_ilw_limit_m * req.retro_ilw_rol
    exp_retro_rec = float(np.mean(retro_rec_vec))
    net_port = np.maximum(gross_port - retro_rec_vec, 0.0)

    # Gross & Net Euler Co-TVaR (1-in-250)
    _, gross_tvar_250, gross_co = rle.euler_cotvar(np.ascontiguousarray(mat, dtype=np.float64), 250.0)
    # Build net layer matrix by proportionally netting retro recoveries across active layers per trial
    safe_gross = np.where(gross_port > 1e-12, gross_port, 1.0)
    rec_ratio = np.minimum(retro_rec_vec / safe_gross, 1.0)
    net_mat = mat * (1.0 - rec_ratio[None, :])
    _, net_tvar_250, net_co = rle.euler_cotvar(np.ascontiguousarray(net_mat, dtype=np.float64), 250.0)

    # Per-layer expected recovery and TVaR relief
    layer_exp_rec = np.mean(mat * rec_ratio[None, :], axis=1) if m > 0 else np.zeros(0)
    layer_tvar_relief = np.maximum(gross_co - net_co, 0.0) if m > 0 else np.zeros(0)

    # Allocation weights (guaranteed sum = 1.0)
    sum_relief = float(np.sum(layer_tvar_relief))
    sum_rec = float(np.sum(layer_exp_rec))
    w_tail = layer_tvar_relief / sum_relief if sum_relief > 1e-12 else np.full(m, 1.0 / max(m, 1))
    w_rec = layer_exp_rec / sum_rec if sum_rec > 1e-12 else np.full(m, 1.0 / max(m, 1))

    if req.allocation_method == "EXPECTED_RECOVERY":
        weights = w_rec
    elif req.allocation_method == "HYBRID_RECOVERY_TAIL":
        tw = max(0.0, min(1.0, req.hybrid_tail_weight))
        weights = tw * w_tail + (1.0 - tw) * w_rec
    else:
        weights = w_tail

    alloc_retro_cost = weights * retro_prem
    conservation_err = abs(float(np.sum(alloc_retro_cost)) - retro_prem) if m > 0 else 0.0

    rows: list[GrossNetLayerRow] = []
    for i, meta in enumerate(layer_meta):
        g_el = float(np.mean(mat[i]))
        g_rip = float(np.mean(rip_mat[i]))
        g_co = float(gross_co[i])
        n_co = float(net_co[i])
        r_cost = float(alloc_retro_cost[i])
        r_rec = float(layer_exp_rec[i])
        g_prof = meta["premium"] + g_rip - g_el
        n_prof = g_prof - r_cost + r_rec
        g_mroc = (g_prof / g_co) * 100.0 if g_co > 1e-9 else 0.0
        n_mroc = (n_prof / n_co) * 100.0 if n_co > 1e-9 else 0.0
        rows.append(
            GrossNetLayerRow(
                programme_id=meta["programme_id"],
                layer_id=meta["layer_id"],
                layer_name=meta["layer_name"],
                cedent_name=meta["cedent_name"],
                gross_premium=round(meta["premium"], 4),
                gross_el=round(g_el, 4),
                gross_cotvar_250=round(g_co, 4),
                gross_mroc_pct=round(g_mroc, 2),
                allocated_retro_cost=round(r_cost, 4),
                allocated_retro_recovery=round(r_rec, 4),
                net_retro_leakage=round(r_cost - r_rec, 4),
                retro_tvar_relief=round(float(layer_tvar_relief[i]), 4),
                net_cotvar_250=round(n_co, 4),
                net_mroc_pct=round(n_mroc, 2),
                accretive_net=n_mroc >= req.perf_hurdle_pct * 100.0,
            )
        )

    # Collateralized fund waterfall + trapped collateral effective IRR
    gross_prem = sum(m_item["premium"] for m_item in layer_meta)
    exp_rip = float(np.mean(port_rip))
    coll_yield = req.total_capital * req.collateral_yield_pct
    amf = req.total_capital * req.amf_pct

    gross_profit_trial = gross_prem + port_rip + coll_yield - amf - gross_port
    net_pre_perf_trial = gross_prem + port_rip - retro_prem + coll_yield - amf - net_port
    hurdle_dollars = req.total_capital * req.perf_hurdle_pct
    perf_fee_trial = np.maximum(net_pre_perf_trial - hurdle_dollars, 0.0) * req.perf_fee_pct
    net_profit_trial = net_pre_perf_trial - perf_fee_trial

    # Trapped collateral drag: when net_port > 0, holdback_buffer_ratio * net_port is locked an extra year
    trapped_trial = np.minimum(req.total_capital, net_port * req.holdback_buffer_ratio)
    exp_trapped = float(np.mean(trapped_trial))
    net_roc_pct = float(np.mean(net_profit_trial / req.total_capital)) * 100.0
    net_irr_pct = (float(np.mean(net_profit_trial)) / (req.total_capital + exp_trapped)) * 100.0
    gross_roc_pct = float(np.mean(gross_profit_trial / req.total_capital)) * 100.0

    waterfall = FundWaterfallSummary(
        total_capital=round(req.total_capital, 4),
        gross_premium=round(gross_prem, 4),
        expected_rip=round(exp_rip, 4),
        retro_premium=round(retro_prem, 4),
        expected_retro_recovery=round(exp_retro_rec, 4),
        collateral_yield=round(coll_yield, 4),
        amf_fee=round(amf, 4),
        expected_perf_fee=round(float(np.mean(perf_fee_trial)), 4),
        expected_gross_loss=round(float(np.mean(gross_port)), 4),
        expected_net_loss=round(float(np.mean(net_port)), 4),
        expected_trapped_collateral=round(float(np.mean(trapped_trial)), 4),
        gross_mean_roc_pct=round(gross_roc_pct, 2),
        net_mean_roc_pct=round(net_roc_pct, 2),
        net_effective_irr_pct=round(net_irr_pct, 2),
        gross_tvar_250=round(float(gross_tvar_250), 4),
        net_tvar_250=round(float(net_tvar_250), 4),
    )

    compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    manifest = lineage.record_manifest(
        run_type="GROSS_NET_RETRO_ALLOCATION",
        input_hashes={"sim_set": yelt.content_hash, "ind_sim_set": ind_yelt.content_hash, "req": canonical_hash(req.model_dump())},
        parameters=req.model_dump(),
        output_payload={"net_mean_roc_pct": waterfall.net_mean_roc_pct, "net_tvar_250": waterfall.net_tvar_250},
        breadcrumb=[
            BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash),
            BreadcrumbStep(entity_type="INDUSTRY_SIM_SET", entity_id=ind_yelt.sim_set_id, label=ind_yelt.name, content_hash=ind_yelt.content_hash),
        ],
    )

    return RetroAllocationResponse(
        view=req.view,
        allocation_method=req.allocation_method,
        currency="USD",
        compute_ms=compute_ms,
        retro_cost_conservation_error=conservation_err,
        waterfall=waterfall,
        layers=rows,
        manifest=manifest,
    )
