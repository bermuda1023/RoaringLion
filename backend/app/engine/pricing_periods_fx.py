"""Phase 7: Technical Pricing Formula, Seasonal Risk-Period Earning, Multi-Currency FX Tables, and Model Change Analysis."""

from __future__ import annotations

import numpy as np

from pydantic import Field

from app.engine.sim_generator import RP_TO_ID, YeltTable
from app.engine.slice_runner import evaluate_programme_dag
from app.models.common import CamelModel
from app.models.domain import ProgrammeDeal
from app.models.enums import NodeType, ViewOfRisk
from app.services.lineage import LineageRegistry


class TechnicalPricingRequest(CamelModel):
    expected_loss_m: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    allocated_capital_m: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    limit_m: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    expected_rip_m: float = Field(default=0.0, ge=0, le=1_000_000, allow_inf_nan=False)
    target_return_on_capital: float = Field(default=0.14, ge=0, le=2, allow_inf_nan=False)
    brokerage_pct: float = Field(default=0.10, ge=0, le=0.9, allow_inf_nan=False)
    fronting_and_tax_pct: float = Field(default=0.03, ge=0, le=0.9, allow_inf_nan=False)
    operating_expense_pct: float = Field(default=0.02, ge=0, le=0.9, allow_inf_nan=False)
    margin_pct: float = Field(default=0.03, ge=0, le=0.9, allow_inf_nan=False)
    quoted_rol_pct: float | None = Field(default=None, ge=0, le=500, allow_inf_nan=False)


class TechnicalPricingResponse(CamelModel):
    technical_premium_m: float
    technical_rol_pct: float
    walk_away_rol_pct: float
    rate_adequacy_pct: float | None = None
    components_m: dict[str, float]


def compute_technical_pricing(req: TechnicalPricingRequest) -> TechnicalPricingResponse:
    """Technical Premium = (EL + Capital Charge - Exp RIP) / (1 - brokerage - fronting - opex - margin)."""
    cap_charge = req.allocated_capital_m * req.target_return_on_capital
    expense_ratio = req.brokerage_pct + req.fronting_and_tax_pct + req.operating_expense_pct + req.margin_pct
    denom = max(0.05, 1.0 - expense_ratio)
    tech_prem = max(0.0, (req.expected_loss_m + cap_charge - req.expected_rip_m) / denom)
    walk_away_prem = max(0.0, (req.expected_loss_m + cap_charge * 0.75 - req.expected_rip_m) / max(0.05, 1.0 - (expense_ratio - req.margin_pct)))
    tech_rol = (tech_prem / req.limit_m) * 100.0 if req.limit_m > 0 else 0.0
    walk_rol = (walk_away_prem / req.limit_m) * 100.0 if req.limit_m > 0 else 0.0
    adequacy = (req.quoted_rol_pct / tech_rol) * 100.0 if (req.quoted_rol_pct is not None and tech_rol > 1e-6) else None

    return TechnicalPricingResponse(
        technical_premium_m=round(tech_prem, 4),
        technical_rol_pct=round(tech_rol, 3),
        walk_away_rol_pct=round(walk_rol, 3),
        rate_adequacy_pct=round(adequacy, 2) if adequacy is not None else None,
        components_m={
            "expectedLoss": round(req.expected_loss_m, 4),
            "capitalCharge": round(cap_charge, 4),
            "lessExpectedRip": round(-req.expected_rip_m, 4),
            "expensesAndMargin": round(tech_prem * expense_ratio, 4),
        },
    )


class SeasonalEarningResponse(CamelModel):
    region_peril_key: str
    as_of_day_of_year: int
    earned_risk_pct: float
    unearned_risk_pct: float
    pro_rata_time_pct: float
    monthly_risk_pattern_pct: list[float]


def compute_seasonal_earning(yelt: YeltTable, region_peril_key: str, as_of_day: int) -> SeasonalEarningResponse:
    """Compute seasonal risk-earned % directly from simulation-set day_of_year loss distribution."""
    if region_peril_key == "ALL":
        mask = np.ones(len(yelt.losses), dtype=bool)
    elif region_peril_key not in RP_TO_ID:
        raise ValueError(f"Unknown region-peril: {region_peril_key}")
    else:
        mask = yelt.rp_ids == RP_TO_ID[region_peril_key]

    days = yelt.day_of_year[mask]
    losses = yelt.losses[mask]
    total = float(losses.sum())
    if total <= 0.0:
        return SeasonalEarningResponse(
            region_peril_key=region_peril_key,
            as_of_day_of_year=as_of_day,
            earned_risk_pct=round((as_of_day / 365.0) * 100.0, 2),
            unearned_risk_pct=round(((365 - as_of_day) / 365.0) * 100.0, 2),
            pro_rata_time_pct=round((as_of_day / 365.0) * 100.0, 2),
            monthly_risk_pattern_pct=[8.33] * 12,
        )

    earned = float(losses[days <= as_of_day].sum()) / total * 100.0
    monthly = []
    for m in range(12):
        d0, d1 = m * 30 + 1, (m + 1) * 30 if m < 11 else 365
        monthly.append(round(float(losses[(days >= d0) & (days <= d1)].sum()) / total * 100.0, 2))

    return SeasonalEarningResponse(
        region_peril_key=region_peril_key,
        as_of_day_of_year=as_of_day,
        earned_risk_pct=round(earned, 2),
        unearned_risk_pct=round(100.0 - earned, 2),
        pro_rata_time_pct=round((as_of_day / 365.0) * 100.0, 2),
        monthly_risk_pattern_pct=monthly,
    )


class FxRateTable(CamelModel):
    profile_id: str
    as_of_date: str
    base_currency: str = "USD"
    rates_to_base: dict[str, float]  # e.g. EUR: 1.09, GBP: 1.27, JPY: 0.0067


DEFAULT_FX_TABLES: dict[str, FxRateTable] = {
    "FX-2027-PLAN": FxRateTable(
        profile_id="FX-2027-PLAN",
        as_of_date="2027-01-01",
        base_currency="USD",
        rates_to_base={"USD": 1.0, "EUR": 1.09, "GBP": 1.27, "JPY": 0.0067, "AUD": 0.66, "CAD": 0.73},
    ),
}


def convert_currency(amount: float, from_ccy: str, to_ccy: str, profile_id: str = "FX-2027-PLAN") -> dict:
    tbl = DEFAULT_FX_TABLES.get(profile_id)
    if not tbl:
        raise ValueError(f"Unknown FX profile: {profile_id}")
    if from_ccy not in tbl.rates_to_base or to_ccy not in tbl.rates_to_base:
        raise ValueError(f"Currency pair {from_ccy}/{to_ccy} missing from FX profile {profile_id}")
    in_base = amount * tbl.rates_to_base[from_ccy]
    converted = in_base / tbl.rates_to_base[to_ccy]
    effective_rate = tbl.rates_to_base[from_ccy] / tbl.rates_to_base[to_ccy]
    return {
        "amount": round(converted, 4),
        "fromCurrency": from_ccy,
        "toCurrency": to_ccy,
        "effectiveRate": round(effective_rate, 6),
        "profileId": profile_id,
        "asOfDate": tbl.as_of_date,
    }


def compare_model_versions(
    programme: ProgrammeDeal,
    old_yelt: YeltTable,
    new_yelt: YeltTable,
    view: ViewOfRisk = ViewOfRisk.ADJUSTED,
) -> dict:
    """Re-run a programme under old vs new SimSet and report per-layer EL & TVaR movement."""
    r_old, _ = evaluate_programme_dag(programme, old_yelt, LineageRegistry(persist=False), views=[view])
    r_new, _ = evaluate_programme_dag(programme, new_yelt, LineageRegistry(persist=False), views=[view])
    old_map = {m.node_id: m for m in r_old.views[view.value]}
    new_map = {m.node_id: m for m in r_new.views[view.value]}

    diffs = []
    for nid, om in old_map.items():
        nm = new_map[nid]
        if om.node_type in (NodeType.OCC_XL, NodeType.AGG_XL, NodeType.QUOTA_SHARE, NodeType.AGG_QUOTA_SHARE, NodeType.SURPLUS_SHARE):
            pct = ((nm.el - om.el) / om.el) * 100.0 if om.el > 1e-9 else 0.0
            diffs.append({
                "nodeId": nid,
                "label": om.node_label,
                "oldEl": om.el,
                "newEl": nm.el,
                "deltaEl": round(nm.el - om.el, 4),
                "deltaPct": round(pct, 2),
            })
    return {
        "programmeId": programme.programme_id,
        "oldSimSetId": old_yelt.sim_set_id,
        "newSimSetId": new_yelt.sim_set_id,
        "view": view.value,
        "layerMovements": diffs,
    }
