"""Phase 2b: Hours-Clause Grouping, Historical As-If (1990-2025) & Burning Cost Engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.engine.slice_runner import evaluate_deterministic_run
from app.models.common import CamelModel
from app.models.domain import BreadcrumbStep, DeterministicLossRow, DeterministicRunRequest, ProgrammeDeal, RunManifest
from app.models.enums import LossSourceType, ViewOfRisk
from app.services.lineage import LineageRegistry, canonical_hash

# Benchmark Historical Catastrophe Event Catalog (1990-2025, representative cedent ground-up losses in $M)
HISTORICAL_BENCHMARK_EVENTS: list[dict[str, Any]] = [
    {"year": 1992, "eventId": "HIST-1992-ANDREW", "eventName": "Hurricane Andrew (1992)", "date": "1992-08-24T12:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": 148.0, "category": "US_HURRICANE"},
    {"year": 1994, "eventId": "HIST-1994-NORTHRIDGE", "eventName": "Northridge Earthquake (1994)", "date": "1994-01-17T12:00:00Z", "regionPerilKey": "US-CA-EQ", "groundUpLoss": 112.0, "category": "INTERNATIONAL_OTHER"},
    {"year": 1995, "eventId": "HIST-1995-KOBE", "eventName": "Kobe Earthquake (1995)", "date": "1995-01-17T06:00:00Z", "regionPerilKey": "JP-KANTO-EQ", "groundUpLoss": 96.0, "category": "INTERNATIONAL_OTHER"},
    {"year": 1999, "eventId": "HIST-1999-LOTHAR", "eventName": "Windstorm Lothar (1999)", "date": "1999-12-26T09:00:00Z", "regionPerilKey": "EU-DE-WS", "groundUpLoss": 68.0, "category": "INTERNATIONAL_OTHER"},
    {"year": 2004, "eventId": "HIST-2004-CHARLEY", "eventName": "Hurricane Charley (2004)", "date": "2004-08-13T18:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": 92.0, "category": "US_HURRICANE"},
    {"year": 2004, "eventId": "HIST-2004-FRANCES", "eventName": "Hurricane Frances (2004)", "date": "2004-09-05T06:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": 64.0, "category": "US_HURRICANE"},
    {"year": 2005, "eventId": "HIST-2005-KATRINA", "eventName": "Hurricane Katrina (2005)", "date": "2005-08-29T12:00:00Z", "regionPerilKey": "US-SE-WS", "groundUpLoss": 175.0, "category": "US_HURRICANE"},
    {"year": 2005, "eventId": "HIST-2005-WILMA", "eventName": "Hurricane Wilma (2005)", "date": "2005-10-24T12:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": 110.0, "category": "US_HURRICANE"},
    {"year": 2007, "eventId": "HIST-2007-KYRILL", "eventName": "Windstorm Kyrill (2007)", "date": "2007-01-18T18:00:00Z", "regionPerilKey": "EU-DE-WS", "groundUpLoss": 74.0, "category": "INTERNATIONAL_OTHER"},
    {"year": 2008, "eventId": "HIST-2008-IKE", "eventName": "Hurricane Ike (2008)", "date": "2008-09-13T08:00:00Z", "regionPerilKey": "US-TX-WS", "groundUpLoss": 122.0, "category": "US_HURRICANE"},
    {"year": 2011, "eventId": "HIST-2011-TOHOKU", "eventName": "Tohoku Earthquake (2011)", "date": "2011-03-11T06:00:00Z", "regionPerilKey": "JP-KANTO-EQ", "groundUpLoss": 135.0, "category": "INTERNATIONAL_OTHER"},
    {"year": 2012, "eventId": "HIST-2012-SANDY", "eventName": "Superstorm Sandy (2012)", "date": "2012-10-29T22:00:00Z", "regionPerilKey": "US-SE-WS", "groundUpLoss": 88.0, "category": "US_HURRICANE"},
    {"year": 2017, "eventId": "HIST-2017-HARVEY", "eventName": "Hurricane Harvey (2017)", "date": "2017-08-26T04:00:00Z", "regionPerilKey": "US-TX-WS", "groundUpLoss": 118.0, "category": "US_HURRICANE"},
    {"year": 2017, "eventId": "HIST-2017-IRMA", "eventName": "Hurricane Irma (2017)", "date": "2017-09-10T14:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": 132.0, "category": "US_HURRICANE"},
    {"year": 2018, "eventId": "HIST-2018-MICHAEL", "eventName": "Hurricane Michael (2018)", "date": "2018-10-10T18:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": 86.0, "category": "US_HURRICANE"},
    {"year": 2021, "eventId": "HIST-2021-IDA", "eventName": "Hurricane Ida (2021)", "date": "2021-08-29T17:00:00Z", "regionPerilKey": "US-SE-WS", "groundUpLoss": 124.0, "category": "US_HURRICANE"},
    {"year": 2022, "eventId": "HIST-2022-IAN", "eventName": "Hurricane Ian (2022)", "date": "2022-09-28T19:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": 156.0, "category": "US_HURRICANE"},
    {"year": 2024, "eventId": "HIST-2024-HELENE", "eventName": "Hurricane Helene (2024)", "date": "2024-09-27T03:00:00Z", "regionPerilKey": "US-SE-WS", "groundUpLoss": 94.0, "category": "US_HURRICANE"},
    {"year": 2024, "eventId": "HIST-2024-MILTON", "eventName": "Hurricane Milton (2024)", "date": "2024-10-10T01:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": 128.0, "category": "US_HURRICANE"},
]


def group_by_hours_clause(losses: list[DeterministicLossRow], hours_clause: int) -> list[DeterministicLossRow]:
    """Group chronologically close losses of the same region_peril_key within `hours_clause` into single occurrences."""
    if hours_clause <= 0 or len(losses) <= 1:
        return sorted(losses, key=lambda r: r.occurrence_datetime)

    ordered = sorted(losses, key=lambda r: r.occurrence_datetime)
    grouped: list[DeterministicLossRow] = []
    window = timedelta(hours=hours_clause)

    def _parse(dt_str: str) -> datetime:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

    for row in ordered:
        dt = _parse(row.occurrence_datetime)
        merged = False
        for idx in range(len(grouped) - 1, -1, -1):
            prev = grouped[idx]
            if prev.region_peril_key == row.region_peril_key and (dt - _parse(prev.occurrence_datetime)) <= window:
                grouped[idx] = prev.model_copy(
                    update={
                        "event_name": f"{prev.event_name} + {row.event_name}",
                        "ground_up_loss": round(prev.ground_up_loss + row.ground_up_loss, 4),
                    }
                )
                merged = True
                break
        if not merged:
            grouped.append(row)
    return grouped


class HistoricalLossInput(CamelModel):
    year: int
    event_name: str
    occurrence_datetime: str
    region_peril_key: str
    ground_up_loss: float
    ldf: float = 1.0  # Loss development factor (IBNR/development)


class BurnCostRequest(CamelModel):
    programme_id: str
    start_year: int = 1990
    end_year: int = 2025
    annual_severity_trend: float = 0.035  # 3.5% annual inflation/trend to current year
    exposure_growth_factor: float = 1.0
    on_level_factor: float = 1.0
    hours_clause: int = 72
    credibility_weight_burn: float = 0.30  # Weight on burn vs modelled EL
    modelled_el: float | None = None
    custom_own_history: list[HistoricalLossInput] | None = None


class YearlyBurnRow(CamelModel):
    year: int
    events_count: int
    raw_ground_up: float
    trended_ground_up: float
    ceded_loss: float
    rip_due: float
    burning_cost_rate_pct: float


class BurnCostResponse(CamelModel):
    programme_id: str
    programme_name: str
    currency: str
    start_year: int
    end_year: int
    num_years: int
    average_annual_burn_m: float
    ex_largest_burn_m: float
    burning_cost_rate_pct: float
    modelled_el_m: float | None = None
    credibility_blended_el_m: float | None = None
    recommended_technical_rol_pct: float | None = None
    yearly_rows: list[YearlyBurnRow]
    manifest: RunManifest


def evaluate_burning_cost(
    req: BurnCostRequest,
    programme: ProgrammeDeal,
    lineage: LineageRegistry,
    modelled_el_override: float | None = None,
) -> BurnCostResponse:
    """Run historical events (or programme's own history) year-by-year through current terms with trend/on-level/LDF."""
    target_year = programme.treaty_year
    if req.custom_own_history is not None:
        source_events = [
            {
                "year": h.year,
                "eventId": f"OWN-{h.year}-{i}",
                "eventName": h.event_name,
                "date": h.occurrence_datetime,
                "regionPerilKey": h.region_peril_key,
                "groundUpLoss": h.ground_up_loss,
                "ldf": h.ldf,
            }
            for i, h in enumerate(req.custom_own_history)
        ]
    else:
        source_events = [
            {**ev, "ldf": 1.0}
            for ev in HISTORICAL_BENCHMARK_EVENTS
            if req.start_year <= ev["year"] <= req.end_year
        ]

    by_year: dict[int, list[dict[str, Any]]] = {y: [] for y in range(req.start_year, req.end_year + 1)}
    for ev in source_events:
        if req.start_year <= ev["year"] <= req.end_year:
            by_year[ev["year"]].append(ev)

    yearly_rows: list[YearlyBurnRow] = []
    ceded_list: list[float] = []

    for yr in range(req.start_year, req.end_year + 1):
        evs = by_year[yr]
        if not evs:
            yearly_rows.append(
                YearlyBurnRow(
                    year=yr,
                    events_count=0,
                    raw_ground_up=0.0,
                    trended_ground_up=0.0,
                    ceded_loss=0.0,
                    rip_due=0.0,
                    burning_cost_rate_pct=0.0,
                )
            )
            ceded_list.append(0.0)
            continue

        trend_mult = ((1.0 + req.annual_severity_trend) ** max(0, target_year - yr)) * req.exposure_growth_factor * req.on_level_factor
        raw_gu = sum(e["groundUpLoss"] for e in evs)
        det_rows = [
            DeterministicLossRow(
                loss_id=e["eventId"],
                event_name=e["eventName"],
                occurrence_datetime=e["date"],
                region_peril_key=e["regionPerilKey"],
                ground_up_loss=round(e["groundUpLoss"] * trend_mult * e["ldf"], 4),
                currency=programme.currency,
                source_type=LossSourceType.HISTORICAL_EVENT,
            )
            for e in evs
        ]
        grouped = group_by_hours_clause(det_rows, req.hours_clause)
        res = evaluate_deterministic_run(
            DeterministicRunRequest(
                programme_id=programme.programme_id,
                view=ViewOfRisk.UNADJUSTED,
                hours_clause=req.hours_clause,
                losses=grouped,
            ),
            programme,
            LineageRegistry(persist=False),
        )
        trended_gu = sum(r.ground_up_loss for r in grouped)
        bc_rate = (res.total_ceded / programme.limit) * 100.0 if programme.limit > 0 else 0.0
        yearly_rows.append(
            YearlyBurnRow(
                year=yr,
                events_count=len(grouped),
                raw_ground_up=round(raw_gu, 4),
                trended_ground_up=round(trended_gu, 4),
                ceded_loss=round(res.total_ceded, 4),
                rip_due=round(res.total_rip_due, 4),
                burning_cost_rate_pct=round(bc_rate, 3),
            )
        )
        ceded_list.append(res.total_ceded)

    num_years = max(1, len(yearly_rows))
    avg_burn = sum(ceded_list) / num_years
    ex_largest = (sum(ceded_list) - max(ceded_list)) / max(1, num_years - 1) if num_years > 1 else avg_burn
    bc_rate_avg = (avg_burn / programme.limit) * 100.0 if programme.limit > 0 else 0.0

    mod_el = req.modelled_el if req.modelled_el is not None else modelled_el_override
    blended = None
    rec_rol = None
    if mod_el is not None:
        w = max(0.0, min(1.0, req.credibility_weight_burn))
        blended = w * avg_burn + (1.0 - w) * mod_el
        # Indicative technical ROL = blended EL * 1.45 risk/expense load / limit
        rec_rol = ((blended * 1.45) / programme.limit) * 100.0 if programme.limit > 0 else None

    manifest = lineage.record_manifest(
        run_type="BURN_COST",
        input_hashes={"graph": programme.graph.content_hash, "params": canonical_hash(req.model_dump())},
        parameters=req.model_dump(),
        output_payload={"average_annual_burn_m": round(avg_burn, 4), "burning_cost_rate_pct": round(bc_rate_avg, 3)},
        breadcrumb=[
            BreadcrumbStep(entity_type="PROGRAMME", entity_id=programme.programme_id, label=f"{programme.cedent_name} — {programme.programme_name}"),
            BreadcrumbStep(entity_type="DAG_GRAPH", entity_id=programme.graph.graph_id, label=programme.graph.name, version=f"v{programme.graph.version}", content_hash=programme.graph.content_hash),
        ],
    )

    return BurnCostResponse(
        programme_id=programme.programme_id,
        programme_name=programme.programme_name,
        currency=programme.currency,
        start_year=req.start_year,
        end_year=req.end_year,
        num_years=num_years,
        average_annual_burn_m=round(avg_burn, 4),
        ex_largest_burn_m=round(ex_largest, 4),
        burning_cost_rate_pct=round(bc_rate_avg, 3),
        modelled_el_m=round(mod_el, 4) if mod_el is not None else None,
        credibility_blended_el_m=round(blended, 4) if blended is not None else None,
        recommended_technical_rol_pct=round(rec_rol, 3) if rec_rol is not None else None,
        yearly_rows=yearly_rows,
        manifest=manifest,
    )
