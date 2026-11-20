"""Instant ILW Pricing & Portfolio Hedge-Effectiveness Engine over Background Industry YELTs."""

from __future__ import annotations

import json
import time

import numpy as np
import roaring_lion_engine as rle

from app.engine.sim_generator import RP_TO_ID, YeltTable
from app.models.domain import BreadcrumbStep
from app.models.enums import ContractDirection, IlwTriggerType
from app.models.phase1 import IlwPriceRequest, IlwPriceResponse
from app.services.lineage import LineageRegistry, canonical_hash


def evaluate_ilw_trial_payouts(ind_yelt: YeltTable, req: IlwPriceRequest) -> tuple[np.ndarray, float, float]:
    """Compute annual ILW payout vector ($M) across all trials, plus attach & exhaust probabilities (%).

    `ind_yelt.losses` are in $B industry loss.
    """
    allowed = set(req.region_perils)
    if not allowed or "ALL" in allowed or "US-ALL-WS" in allowed:
        if "US-ALL-WS" in allowed:
            allowed_ids = [RP_TO_ID[k] for k in ("US-FL-WS", "US-TX-WS", "US-SE-WS")]
            mask = np.isin(ind_yelt.rp_ids, allowed_ids)
        else:
            mask = np.ones(len(ind_yelt.losses), dtype=bool)
    else:
        allowed_ids = [RP_TO_ID[k] for k in allowed if k in RP_TO_ID]
        mask = np.isin(ind_yelt.rp_ids, allowed_ids)

    ind_vals = np.where(mask, ind_yelt.losses, 0.0)
    n_trials = ind_yelt.num_trials
    trig = req.trigger_bn
    exh = req.exhaustion_bn if (req.exhaustion_bn is not None and req.exhaustion_bn > trig) else trig

    if req.trigger_type == IlwTriggerType.BINARY:
        # Per-trial max industry event >= trigger => full limit payout
        max_ev = np.zeros(n_trials, dtype=np.float64)
        np.maximum.at(max_ev, ind_yelt.trial_ids - 1, ind_vals)
        hit = max_ev >= trig
        payout = np.where(hit, req.limit_m, 0.0)
        attach_prob = float(np.mean(hit)) * 100.0
        exhaust_prob = attach_prob
    elif req.trigger_type == IlwTriggerType.PRO_RATA:
        max_ev = np.zeros(n_trials, dtype=np.float64)
        np.maximum.at(max_ev, ind_yelt.trial_ids - 1, ind_vals)
        span = max(exh - trig, 1e-6)
        frac = np.clip((max_ev - trig) / span, 0.0, 1.0)
        payout = frac * req.limit_m
        attach_prob = float(np.mean(max_ev > trig)) * 100.0
        exhaust_prob = float(np.mean(max_ev >= exh)) * 100.0
    elif req.trigger_type == IlwTriggerType.AGGREGATE:
        ann_ind = np.bincount(ind_yelt.trial_ids - 1, weights=ind_vals, minlength=n_trials)
        if exh > trig:
            frac = np.clip((ann_ind - trig) / (exh - trig), 0.0, 1.0)
            payout = frac * req.limit_m
            attach_prob = float(np.mean(ann_ind > trig)) * 100.0
            exhaust_prob = float(np.mean(ann_ind >= exh)) * 100.0
        else:
            hit = ann_ind >= trig
            payout = np.where(hit, req.limit_m, 0.0)
            attach_prob = float(np.mean(hit)) * 100.0
            exhaust_prob = attach_prob
    else:
        # SECOND_EVENT: triggers only if >= 2 events in the same trial exceed trigger_bn
        exceed_counts = np.bincount(
            ind_yelt.trial_ids - 1,
            weights=(ind_vals >= trig).astype(np.float64),
            minlength=n_trials,
        )
        hit = exceed_counts >= 2.0
        payout = np.where(hit, req.limit_m, 0.0)
        attach_prob = float(np.mean(hit)) * 100.0
        exhaust_prob = attach_prob

    return payout, attach_prob, exhaust_prob


def price_ilw_with_portfolio_hedge(
    *,
    req: IlwPriceRequest,
    ind_yelt: YeltTable,
    portfolio_gross_loss_by_trial: np.ndarray,
    lineage: LineageRegistry,
) -> IlwPriceResponse:
    """Price an ILW standalone AND quantify exact basis-risk & TVaR hedge impact against a portfolio."""
    t0 = time.perf_counter()
    payout, attach_prob, exhaust_prob = evaluate_ilw_trial_payouts(ind_yelt, req)

    el_m = float(np.mean(payout))
    sd_m = float(np.std(payout))
    prem_m = req.limit_m * req.rol
    elr_pct = (el_m / prem_m) * 100.0 if prem_m > 0 else 0.0

    # Portfolio hedge analytics (exact trial-by-trial correlation on shared event catalogue)
    gross_ep = json.loads(rle.ep_stats(np.ascontiguousarray(portfolio_gross_loss_by_trial, dtype=np.float64), [250.0]))
    gross_tvar_250 = float(gross_ep["ep_points"][0]["tvar"])

    if req.direction == ContractDirection.ASSUMED:
        net_vec = portfolio_gross_loss_by_trial + payout
    else:
        # CEDED / EXTERNAL_RETRO hedge reduces portfolio loss
        net_vec = np.maximum(portfolio_gross_loss_by_trial - payout, 0.0)

    net_ep = json.loads(rle.ep_stats(np.ascontiguousarray(net_vec, dtype=np.float64), [250.0]))
    net_tvar_250 = float(net_ep["ep_points"][0]["tvar"])
    tvar_reduction = gross_tvar_250 - net_tvar_250

    if np.std(portfolio_gross_loss_by_trial) > 1e-12 and sd_m > 1e-12:
        corr = float(np.corrcoef(portfolio_gross_loss_by_trial, payout)[0, 1])
    else:
        corr = 0.0

    # Hedge effectiveness: TVaR saved divided by theoretical max saving (ILW limit)
    hedge_eff = (tvar_reduction / req.limit_m) * 100.0 if req.limit_m > 0 else 0.0
    # Basis risk residual: expected shortfall where portfolio > 1-in-100 VaR yet ILW pays < limit
    p99 = float(np.quantile(portfolio_gross_loss_by_trial, 0.99))
    tail_mask = portfolio_gross_loss_by_trial >= p99
    basis_residual = float(np.mean(np.maximum(req.limit_m - payout[tail_mask], 0.0))) if np.any(tail_mask) else 0.0
    cost_per_tvar = (prem_m / tvar_reduction) if tvar_reduction > 1e-6 else None

    compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    req_hash = canonical_hash(req.model_dump())
    manifest = lineage.record_manifest(
        run_type="ILW_PRICE",
        input_hashes={"industry_sim_set": ind_yelt.content_hash, "ilw_spec": req_hash},
        parameters=req.model_dump(),
        output_payload={"el_m": round(el_m, 4), "tvar_250_reduction_m": round(tvar_reduction, 4)},
        breadcrumb=[
            BreadcrumbStep(
                entity_type="INDUSTRY_SIM_SET",
                entity_id=ind_yelt.sim_set_id,
                label=ind_yelt.name,
                content_hash=ind_yelt.content_hash,
            ),
            BreadcrumbStep(
                entity_type="ILW_STRUCTURE",
                entity_id=f"ilw-{req_hash[:8]}",
                label=req.name,
                content_hash=req_hash,
            ),
        ],
    )

    return IlwPriceResponse(
        name=req.name,
        industry_sim_set_id=ind_yelt.sim_set_id,
        event_catalog_id=ind_yelt.event_catalog_id,
        region_perils=req.region_perils,
        trigger_type=req.trigger_type,
        trigger_bn=req.trigger_bn,
        exhaustion_bn=req.exhaustion_bn,
        limit_m=round(req.limit_m, 4),
        premium_m=round(prem_m, 4),
        rol=round(req.rol, 4),
        direction=req.direction,
        currency=req.currency,
        compute_ms=compute_ms,
        attach_prob_pct=round(attach_prob, 3),
        exhaust_prob_pct=round(exhaust_prob, 3),
        expected_loss_m=round(el_m, 4),
        sd_m=round(sd_m, 4),
        elr_pct=round(elr_pct, 2),
        indemnity_index_correlation=round(corr, 4),
        gross_portfolio_tvar_250_m=round(gross_tvar_250, 4),
        net_portfolio_tvar_250_m=round(net_tvar_250, 4),
        tvar_250_reduction_m=round(tvar_reduction, 4),
        hedge_effectiveness_pct=round(hedge_eff, 2),
        basis_risk_residual_m=round(basis_residual, 4),
        cost_per_dollar_tvar_saved=round(cost_per_tvar, 4) if cost_per_tvar is not None else None,
        manifest=manifest,
    )
