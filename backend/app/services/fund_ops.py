"""Phases 8 & 9: Fund Operations (Investors/Series NAV, Reserving, Cash Sub-Ledger), Slip Intake, Alerts, Access Hook."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.common import CamelModel


def check_permission(actor: str, action: str, resource: str) -> bool:
    """Single permission/approval hook (allows all by default; Phase 9 enforcement point)."""
    return True


class InvestorSeries(CamelModel):
    series_id: str
    investor_name: str
    share_class: str  # CLASS_A | CLASS_B | SIDE_POCKET
    amf_pct: float
    perf_fee_pct: float
    nav_m: float
    trapped_side_pocket_m: float = 0.0
    dealing_date: str = "2027-01-01"


class EventReserve(CamelModel):
    reserve_id: str
    event_name: str
    programme_id: str
    case_reserve_m: float
    ibnr_m: float
    total_reserve_m: float
    trapped_collateral_m: float
    updated_at: datetime


class CashEntry(CamelModel):
    entry_id: str
    entry_type: str  # PREMIUM_INSTALMENT | RIP_INVOICE | CLAIM_PAYMENT | COLLATERAL_RELEASE
    programme_id: str
    counterparty: str
    amount_m: float
    currency: str = "USD"
    due_date: str
    status: str = "SCHEDULED"


class MarketMark(CamelModel):
    instrument_id: str
    name: str
    instrument_type: str  # ILW | CAT_BOND
    region_peril: str
    indicative_spread_pct: float
    mark_price: float
    as_of_date: str


class SlipIntakeDraft(CamelModel):
    cedent_name: str
    programme_name: str
    limit_m: float
    attachment_m: float
    rol: float
    reinstatements: float
    hours_clause: int
    region_perils: list[str]
    extracted_from_excerpt: str
    requires_human_confirmation: bool = True


class FundOpsStore:
    def __init__(self) -> None:
        self.investors: list[InvestorSeries] = [
            InvestorSeries(series_id="SER-A-2027", investor_name="Nordic Pension Fund", share_class="CLASS_A", amf_pct=0.015, perf_fee_pct=0.15, nav_m=95.0, trapped_side_pocket_m=4.5),
            InvestorSeries(series_id="SER-B-2027", investor_name="Endowment Alpha LP", share_class="CLASS_B", amf_pct=0.0125, perf_fee_pct=0.125, nav_m=65.0, trapped_side_pocket_m=2.1),
        ]
        self.reserves: list[EventReserve] = [
            EventReserve(
                reserve_id="RES-2027-01",
                event_name="Hurricane Milton (Development)",
                programme_id="prog-farmers-us-27",
                case_reserve_m=8.2,
                ibnr_m=3.4,
                total_reserve_m=11.6,
                trapped_collateral_m=14.5,
                updated_at=datetime.now(timezone.utc),
            )
        ]
        self.cash_entries: list[CashEntry] = [
            CashEntry(entry_id="CASH-01", entry_type="PREMIUM_INSTALMENT", programme_id="prog-farmers-us-27", counterparty="Broker A", amount_m=4.825, due_date="2027-01-15", status="SETTLED"),
            CashEntry(entry_id="CASH-02", entry_type="PREMIUM_INSTALMENT", programme_id="prog-farmers-us-27", counterparty="Broker A", amount_m=4.825, due_date="2027-04-15", status="SCHEDULED"),
            CashEntry(entry_id="CASH-03", entry_type="COLLATERAL_RELEASE", programme_id="prog-eurowind-27", counterparty="Collateral Trust", amount_m=30.0, due_date="2028-01-05", status="SCHEDULED"),
        ]
        self.market_marks: list[MarketMark] = [
            MarketMark(instrument_id="MKT-ILW-FL50", name="US FL Wind $50B Binary ILW", instrument_type="ILW", region_peril="US-FL-WS", indicative_spread_pct=19.5, mark_price=100.0, as_of_date="2026-11-19"),
            MarketMark(instrument_id="MKT-CB-CAEQ", name="Golden Gate Re 2027-1 Class A", instrument_type="CAT_BOND", region_peril="US-CA-EQ", indicative_spread_pct=6.8, mark_price=101.25, as_of_date="2026-11-19"),
        ]

    def parse_slip_text(self, raw_text: str) -> SlipIntakeDraft:
        """Extract draft reinsurance terms from a broker slip / submission text for human confirmation."""
        lim_m = re.search(r"(\d+(?:\.\d+)?)\s*m\s*(?:xs|x/s|excess)", raw_text, re.I)
        att_m = re.search(r"(?:xs|x/s|excess(?:\s+of)?)\s*\$?(\d+(?:\.\d+)?)\s*m", raw_text, re.I)
        rol_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*rol", raw_text, re.I)
        hrs_m = re.search(r"(\d+)\s*(?:hours|hrs)", raw_text, re.I)
        ced_m = re.search(r"(?:cedent|reinsured)\s*[:\-]\s*([^\n,;]+)", raw_text, re.I)

        rps = []
        if re.search(r"\bFL\b|florida", raw_text, re.I):
            rps.append("US-FL-WS")
        if re.search(r"\bTX\b|texas", raw_text, re.I):
            rps.append("US-TX-WS")
        if re.search(r"\bCA\b|california", raw_text, re.I):
            rps.append("US-CA-EQ")
        if not rps:
            rps = ["US-FL-WS"]

        return SlipIntakeDraft(
            cedent_name=ced_m.group(1).strip() if ced_m else "Parsed Cedent (Confirm)",
            programme_name="Extracted Cat XL Slip Draft",
            limit_m=float(lim_m.group(1)) if lim_m else 25.0,
            attachment_m=float(att_m.group(1)) if att_m else 75.0,
            rol=round(float(rol_m.group(1)) / 100.0, 4) if rol_m else 0.18,
            reinstatements=1.0,
            hours_clause=int(hrs_m.group(1)) if hrs_m else 72,
            region_perils=rps,
            extracted_from_excerpt=raw_text[:240],
            requires_human_confirmation=True,
        )
