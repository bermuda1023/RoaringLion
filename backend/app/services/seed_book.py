"""Deterministic synthetic reinsurance book (~42 realistic clients/programmes) for testing & demos.

Kept separate from the 6 core Phase 0/1 programmes (Farmers 2027/2026, Pacific EQ, EuroWind,
ILW FL Wind, UNL Retro) so existing tests stay intact while CMS, Structuring Studio, Portfolio,
and the Portfolio Optimizer immediately open onto a realistic multi-peril institutional book.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.db.models import ContractNoteRecord, ContractRecord
from app.models.domain import (
    CurvePoint,
    GraphEdgeSpec,
    GraphNodeSpec,
    GraphSpec,
    UpliftRule,
    UpliftSet,
)
from app.models.enums import (
    ContractDirection,
    IlwTriggerType,
    InstrumentType,
    NodeType,
    PerilCode,
    PlacementType,
    ProgrammeStatus,
    StructureType,
    is_hedge_direction,
)
from app.models.phase1 import ProgrammeProfile
from app.services.lineage import canonical_hash
from app.services.node_labels import relabel_graph, smart_label
from app.services.portfolio_snapshot import (
    is_ilw_programme,
    is_unl_retro_hedge,
    select_unl_snapshot_contracts,
    snapshot_loss_files,
)

DEFAULT_INDUSTRY_SIM_SET_ID = "ind-synth-cat-v1-100k"


@dataclass(frozen=True)
class LayerSeed:
    layer_code: str
    name: str
    node_type: NodeType
    params: dict[str, Any]


@dataclass(frozen=True)
class ProgrammeSeed:
    programme_id: str
    unique_code: str
    program_code: str
    cedent_name: str
    programme_name: str
    treaty_year: int
    renewal_period: str
    office: str
    underwriter: str
    broker: str
    status: ProgrammeStatus
    region_perils: list[str]
    perils: list[PerilCode]
    layers: list[LayerSeed]
    direction: ContractDirection = ContractDirection.ASSUMED
    structure: StructureType = StructureType.OCC_XL
    instrument: InstrumentType = InstrumentType.UNL
    placement: PlacementType = PlacementType.OPEN_MARKET
    trading_unit: str = "N.A. Property"
    planning_class: str = "Property CAT"
    cedent_country: str = "USA"
    cedent_credit_rating: str = "AM Best A"
    new_or_renewal: str = "RENEWAL"
    written_date: str = "2026-11-20"
    hours_clause: int = 72
    brokerage_pct: float = 0.10
    fronting_fee_pct: float = 0.02
    custom_tags: list[str] = field(default_factory=list)
    uplift_rules: list[UpliftRule] = field(default_factory=list)
    ilw_spec: dict[str, Any] | None = None
    portfolio_snapshot_date: str | None = None
    chain_id: str = ""
    prior_contract_id: str | None = None
    inuring_note: str = "Clean excess-of-loss structure"
    note_body: str | None = None


def _occ(
    code: str,
    name: str,
    *,
    limit: float,
    attachment: float,
    rol: float,
    written: float,
    signed: float,
    rarc: float,
    reinstatements: float = 1.0,
    rip_rate: float = 1.0,
    brokerage: float = 0.10,
    franchise: float = 0.0,
) -> LayerSeed:
    return LayerSeed(
        layer_code=code,
        name=name,
        node_type=NodeType.OCC_XL,
        params={
            "layerCode": code,
            "labelName": name,
            "occLimit": limit,
            "occAttachment": attachment,
            "occFranchise": franchise,
            "reinstatements": reinstatements,
            "ripRate": rip_rate,
            "depositPremium": round(limit * rol, 6),
            "writtenLine": written,
            "placedShare": signed,
            "brokeragePct": brokerage,
            "rarcPct": rarc,
        },
    )


def _agg(
    code: str,
    name: str,
    *,
    limit: float,
    attachment: float,
    rol: float,
    written: float,
    signed: float,
    rarc: float,
    franchise: float = 5.0,
    per_event_cap: float | None = 25.0,
    brokerage: float = 0.10,
) -> LayerSeed:
    params: dict[str, Any] = {
        "layerCode": code,
        "labelName": name,
        "aggLimit": limit,
        "aggAttachment": attachment,
        "perEventFranchise": franchise,
        "depositPremium": round(limit * rol, 6),
        "writtenLine": written,
        "placedShare": signed,
        "brokeragePct": brokerage,
        "rarcPct": rarc,
    }
    if per_event_cap is not None:
        params["perEventCap"] = per_event_cap
    return LayerSeed(layer_code=code, name=name, node_type=NodeType.AGG_XL, params=params)


def _qs(
    code: str,
    name: str,
    *,
    cession: float,
    event_cap: float,
    agg_cap: float,
    deposit_premium: float,
    written: float,
    signed: float,
    rarc: float,
    brokerage: float = 0.05,
) -> LayerSeed:
    return LayerSeed(
        layer_code=code,
        name=name,
        node_type=NodeType.AGG_QUOTA_SHARE,
        params={
            "layerCode": code,
            "labelName": name,
            "cessionShare": cession,
            "eventCap": event_cap,
            "aggCap": agg_cap,
            "depositPremium": round(deposit_premium, 6),
            "writtenLine": written,
            "placedShare": signed,
            "brokeragePct": brokerage,
            "rarcPct": rarc,
        },
    )


def _cascading(
    code: str,
    name: str,
    *,
    sub_layers: list[dict[str, Any]],
    written: float,
    signed: float,
    rarc: float,
    brokerage: float = 0.10,
) -> LayerSeed:
    wired_subs = [{**sub, "placedShare": signed} for sub in sub_layers]
    return LayerSeed(
        layer_code=code,
        name=name,
        node_type=NodeType.CASCADING_TOWER,
        params={
            "layerCode": code,
            "labelName": name,
            "layers": wired_subs,
            "writtenLine": written,
            "placedShare": signed,
            "brokeragePct": brokerage,
            "rarcPct": rarc,
        },
    )


def _ilw(
    code: str,
    name: str,
    *,
    limit: float,
    trigger_bn: float,
    rol: float,
    trigger_type: IlwTriggerType = IlwTriggerType.BINARY,
    exhaustion_bn: float | None = None,
    written: float = 1.0,
    signed: float = 1.0,
    rarc: float = 2.0,
    brokerage: float = 0.0,
) -> LayerSeed:
    params: dict[str, Any] = {
        "layerCode": code,
        "labelName": name,
        "limit": limit,
        "trigger": trigger_bn,
        "triggerType": trigger_type.value,
        "depositPremium": round(limit * rol, 6),
        "writtenLine": written,
        "placedShare": signed,
        "brokeragePct": brokerage,
        "rarcPct": rarc,
    }
    if exhaustion_bn is not None:
        params["exhaustion"] = exhaustion_bn
    return LayerSeed(layer_code=code, name=name, node_type=NodeType.ILW_OPTION, params=params)


def _uplift(rp: str, factor: float, note: str, curve: list[tuple[float, float]] | None = None) -> UpliftRule:
    return UpliftRule(
        region_peril_key=rp,
        flat_severity_factor=factor,
        curve_points=[CurvePoint(loss_threshold=t, factor=f) for t, f in (curve or [])],
        additive_load=0.0,
        note=note,
    )


def _synthetic_programme_specs() -> list[ProgrammeSeed]:
    """Return 42 deterministic synthetic reinsurance programmes spanning perils, structures, and statuses."""
    return [
        # ── US Wind (FL, Gulf, Southeast, Northeast) ──
        ProgrammeSeed(
            programme_id="prog-syn-001",
            unique_code="RL-2027-006",
            program_code="PGM-006",
            chain_id="chain-syn-bayshore",
            prior_contract_id="prog-syn-039",
            cedent_name="Bayshore Property Mutual",
            programme_name="Florida Homeowners Cat XL Tower",
            treaty_year=2027,
            renewal_period="JU27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Guy Carpenter",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-FL-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best A-",
            custom_tags=["Peak", "FL Wind", "Renewal"],
            inuring_note="FHCF mandatory layer inures to 2nd and 3rd Cat XL.",
            note_body="June 1 FL renewal: +6.1% weighted RARC after raising L1 attachment +5m YoY; signed down from 15% written.",
            uplift_rules=[
                _uplift("US-FL-WS", 1.09, "FL demand surge & litigation load", [(25.0, 1.14), (100.0, 1.05)]),
            ],
            layers=[
                _occ("L1", "1st Cat XL", limit=25.0, attachment=40.0, rol=0.28, written=0.15, signed=0.12, rarc=7.5),
                _occ("L2", "2nd Cat XL", limit=40.0, attachment=65.0, rol=0.195, written=0.18, signed=0.14, rarc=6.2),
                _occ("L3", "3rd Cat XL", limit=60.0, attachment=105.0, rol=0.125, written=0.15, signed=0.10, rarc=4.8),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-002",
            unique_code="RL-2027-007",
            program_code="PGM-007",
            cedent_name="Gulfstar Insurance Group",
            programme_name="Gulf Coast 4-Layer Cat XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="UW Team B",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-TX-WS", "US-SE-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best A",
            custom_tags=["Peak", "Gulf Wind", "4-Layer Tower"],
            inuring_note="Clean 4-layer occurrence tower covering TX, LA, MS, and AL coastal portfolio.",
            note_body="Full participation across all four Gulf layers; top layer provides efficient tail capacity at 7.2% ROL.",
            uplift_rules=[_uplift("US-TX-WS", 1.07, "Texas coastal construction inflation")],
            layers=[
                _occ("L1", "Working Cat XL", limit=20.0, attachment=35.0, rol=0.24, written=0.12, signed=0.10, rarc=5.5, reinstatements=2.0),
                _occ("L2", "2nd Layer Cat XL", limit=35.0, attachment=55.0, rol=0.17, written=0.15, signed=0.12, rarc=4.9),
                _occ("L3", "3rd Layer Cat XL", limit=50.0, attachment=90.0, rol=0.115, written=0.15, signed=0.12, rarc=3.8),
                _occ("L4", "Top Layer Cat XL", limit=75.0, attachment=140.0, rol=0.072, written=0.10, signed=0.08, rarc=2.5),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-003",
            unique_code="RL-2027-008",
            program_code="PGM-008",
            cedent_name="Magnolia Coastal Indemnity",
            programme_name="SE & FL Cascading Top & Drop",
            treaty_year=2027,
            renewal_period="JU27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Gallagher Re",
            status=ProgrammeStatus.BOUND,
            structure=StructureType.CASCADING,
            region_perils=["US-FL-WS", "US-SE-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best A-",
            custom_tags=["Peak", "Cascading", "Top & Drop"],
            inuring_note="Cascading 3-tranche tower drops down upon exhaustion of underlying occurrence layers.",
            uplift_rules=[
                _uplift("US-FL-WS", 1.08, "FL coastal exposure growth"),
                _uplift("US-SE-WS", 1.05, "SE coastal trend"),
            ],
            layers=[
                _cascading(
                    "L1",
                    "SE/FL Cascading Tower",
                    written=0.15,
                    signed=0.12,
                    rarc=6.4,
                    sub_layers=[
                        {"occLimit": 30.0, "occAttachment": 50.0, "occFranchise": 0.0, "reinstatements": 1.0, "ripRate": 1.0, "depositPremium": 6.6},
                        {"occLimit": 40.0, "occAttachment": 80.0, "occFranchise": 0.0, "reinstatements": 1.0, "ripRate": 1.0, "depositPremium": 5.6},
                        {"occLimit": 50.0, "occAttachment": 120.0, "occFranchise": 0.0, "reinstatements": 0.0, "ripRate": 0.0, "depositPremium": 4.5},
                    ],
                ),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-004",
            unique_code="RL-2027-009",
            program_code="PGM-009",
            cedent_name="Coral Reef Homeowners Co.",
            programme_name="Florida Homeowners Cat Quota Share",
            treaty_year=2027,
            renewal_period="JU27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Howden Re",
            status=ProgrammeStatus.BOUND,
            structure=StructureType.QUOTA_SHARE,
            region_perils=["US-FL-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best B++",
            brokerage_pct=0.05,
            custom_tags=["Peak", "Quota Share", "FL Wind"],
            inuring_note="25% proportional cession with $35m per-event cap and $50m annual aggregate cap.",
            uplift_rules=[_uplift("US-FL-WS", 1.10, "FL homeowners repair inflation")],
            layers=[
                _qs("L1", "FL Homeowners QS", cession=0.25, event_cap=35.0, agg_cap=50.0, deposit_premium=10.5, written=0.18, signed=0.14, rarc=8.2),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-005",
            unique_code="RL-2027-010",
            program_code="PGM-010",
            cedent_name="Atlantic Seaboard Mutual",
            programme_name="US Northeast & Mid-Atlantic Wind XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="UW Team C",
            broker="Guy Carpenter",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-NE-WS", "US-SE-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best A+",
            custom_tags=["US Wind", "Northeast"],
            uplift_rules=[_uplift("US-NE-WS", 1.06, "NE metro surge load")],
            layers=[
                _occ("L1", "1st NE Wind XL", limit=35.0, attachment=55.0, rol=0.145, written=0.15, signed=0.12, rarc=4.2),
                _occ("L2", "2nd NE Wind XL", limit=60.0, attachment=90.0, rol=0.085, written=0.15, signed=0.12, rarc=3.1),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-006",
            unique_code="RL-2027-011",
            program_code="PGM-011",
            cedent_name="Palmetto Regional Ins.",
            programme_name="Carolinas & Georgia Wind Cat XL",
            treaty_year=2027,
            renewal_period="MY27",
            office="BDA",
            underwriter="UW Team B",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-SE-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best A-",
            custom_tags=["US Wind", "Southeast"],
            uplift_rules=[_uplift("US-SE-WS", 1.05, "Carolinas coastal growth")],
            layers=[
                _occ("L1", "SE Wind Cat XL", limit=40.0, attachment=60.0, rol=0.16, written=0.18, signed=0.15, rarc=5.0),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-007",
            unique_code="RL-2027-012",
            program_code="PGM-012",
            cedent_name="New England Heritage Ins.",
            programme_name="Northeast Coastal 3-Layer Wind XL",
            treaty_year=2027,
            renewal_period="J27",
            office="LON",
            underwriter="UW Team C",
            broker="Guy Carpenter",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-NE-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best A",
            custom_tags=["US Wind", "Northeast", "3-Layer Tower"],
            uplift_rules=[_uplift("US-NE-WS", 1.05, "New England storm surge load")],
            layers=[
                _occ("L1", "1st NE Cat XL", limit=25.0, attachment=40.0, rol=0.15, written=0.15, signed=0.12, rarc=3.8),
                _occ("L2", "2nd NE Cat XL", limit=40.0, attachment=65.0, rol=0.095, written=0.15, signed=0.12, rarc=3.0),
                _occ("L3", "3rd NE Cat XL", limit=55.0, attachment=105.0, rol=0.058, written=0.12, signed=0.10, rarc=2.2),
            ],
        ),

        # ── US Quake (CA & PNW) ──
        ProgrammeSeed(
            programme_id="prog-syn-008",
            unique_code="RL-2027-013",
            program_code="PGM-013",
            chain_id="chain-syn-goldengate",
            prior_contract_id="prog-syn-040",
            cedent_name="Golden Gate Earthquake Authority",
            programme_name="California Residential EQ Tower",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-CA-EQ"],
            perils=[PerilCode.EQ],
            cedent_credit_rating="S&P A+",
            custom_tags=["US Quake", "California", "Renewal"],
            note_body="Core CA EQ diversifier against FL wind peak; fire-following uplift +7%.",
            uplift_rules=[_uplift("US-CA-EQ", 1.07, "CA EQ fire-following & sprinkler leakage")],
            layers=[
                _occ("L1", "1st CA EQ XL", limit=40.0, attachment=60.0, rol=0.155, written=0.15, signed=0.12, rarc=5.2),
                _occ("L2", "2nd CA EQ XL", limit=60.0, attachment=100.0, rol=0.102, written=0.15, signed=0.12, rarc=4.4),
                _occ("L3", "3rd CA EQ XL", limit=100.0, attachment=160.0, rol=0.064, written=0.12, signed=0.10, rarc=3.6),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-009",
            unique_code="RL-2027-014",
            program_code="PGM-014",
            cedent_name="Cascadia Mutual Insurance",
            programme_name="Pacific Northwest EQ Cat XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="UW Team B",
            broker="Guy Carpenter",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-PNW-EQ"],
            perils=[PerilCode.EQ],
            cedent_credit_rating="AM Best A",
            custom_tags=["US Quake", "PNW", "Diversifier"],
            uplift_rules=[_uplift("US-PNW-EQ", 1.06, "Cascadia subduction liquefaction load")],
            layers=[
                _occ("L1", "1st PNW EQ XL", limit=30.0, attachment=45.0, rol=0.115, written=0.18, signed=0.15, rarc=4.0),
                _occ("L2", "2nd PNW EQ XL", limit=50.0, attachment=75.0, rol=0.072, written=0.16, signed=0.14, rarc=3.2),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-010",
            unique_code="RL-2027-015",
            program_code="PGM-015",
            cedent_name="Sierra Commercial Property",
            programme_name="West Coast Commercial EQ XL",
            treaty_year=2027,
            renewal_period="AP27",
            office="BDA",
            underwriter="UW Team C",
            broker="Gallagher Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-CA-EQ", "US-PNW-EQ"],
            perils=[PerilCode.EQ],
            cedent_credit_rating="AM Best A-",
            custom_tags=["US Quake", "Commercial"],
            uplift_rules=[
                _uplift("US-CA-EQ", 1.05, "CA commercial BI uplift"),
                _uplift("US-PNW-EQ", 1.05, "PNW commercial BI uplift"),
            ],
            layers=[
                _occ("L1", "West Coast EQ XL", limit=50.0, attachment=80.0, rol=0.128, written=0.15, signed=0.12, rarc=4.5),
            ],
        ),

        # ── US Severe Convective Storm & Wildfire ──
        ProgrammeSeed(
            programme_id="prog-syn-011",
            unique_code="RL-2027-016",
            program_code="PGM-016",
            cedent_name="Heartland Farm & Mutual",
            programme_name="Midwest SCS Annual Aggregate XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="UW Team B",
            broker="Guy Carpenter",
            status=ProgrammeStatus.BOUND,
            structure=StructureType.AGG_XL,
            region_perils=["US-MW-CS"],
            perils=[PerilCode.CS],
            cedent_credit_rating="AM Best A",
            custom_tags=["SCS", "Aggregate XL", "Midwest"],
            inuring_note="Annual aggregate excess over $5m per-event franchise and $25m per-event cap.",
            uplift_rules=[_uplift("US-MW-CS", 1.10, "SCS hail & roof claim severity trend")],
            layers=[
                _agg("L1", "SCS Annual Agg XL", limit=35.0, attachment=55.0, rol=0.22, written=0.18, signed=0.14, rarc=9.5, franchise=5.0, per_event_cap=25.0),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-012",
            unique_code="RL-2027-017",
            program_code="PGM-017",
            cedent_name="Prairie Plains Insurance Co.",
            programme_name="Plains Hail & Tornado Occ + Agg",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="UW Team B",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-MW-CS"],
            perils=[PerilCode.CS],
            cedent_credit_rating="AM Best A-",
            custom_tags=["SCS", "Occ + Agg"],
            uplift_rules=[_uplift("US-MW-CS", 1.08, "Plains convective storm frequency load")],
            layers=[
                _occ("L1", "SCS Occ XL", limit=30.0, attachment=35.0, rol=0.205, written=0.15, signed=0.12, rarc=8.0, reinstatements=2.0),
                _agg("L2", "SCS Agg XL", limit=25.0, attachment=50.0, rol=0.23, written=0.15, signed=0.12, rarc=10.2, franchise=6.0, per_event_cap=20.0),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-013",
            unique_code="RL-2027-018",
            program_code="PGM-018",
            cedent_name="Great Lakes Regional Mutual",
            programme_name="Midwest Regional Property Quota Share",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="UW Team C",
            broker="Gallagher Re",
            status=ProgrammeStatus.BOUND,
            structure=StructureType.QUOTA_SHARE,
            region_perils=["US-MW-CS", "US-NE-WS"],
            perils=[PerilCode.CS, PerilCode.WS],
            cedent_credit_rating="AM Best A",
            brokerage_pct=0.05,
            custom_tags=["SCS", "Quota Share", "Diversifier"],
            uplift_rules=[_uplift("US-MW-CS", 1.07, "Midwest roof replacement inflation")],
            layers=[
                _qs("L1", "Midwest Property QS", cession=0.20, event_cap=25.0, agg_cap=45.0, deposit_premium=9.0, written=0.18, signed=0.14, rarc=6.5),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-014",
            unique_code="RL-2027-019",
            program_code="PGM-019",
            cedent_name="Pacific Crest Homeowners",
            programme_name="California Wildfire Cat XL",
            treaty_year=2027,
            renewal_period="JL27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-CA-WF"],
            perils=[PerilCode.WF],
            cedent_credit_rating="AM Best A-",
            custom_tags=["Wildfire", "California"],
            note_body="WUI exposure pruned 12% YoY; +12% house severity uplift retained for smoke/debris removal.",
            uplift_rules=[_uplift("US-CA-WF", 1.12, "CA WUI wildfire & smoke demand surge")],
            layers=[
                _occ("L1", "1st Wildfire XL", limit=25.0, attachment=40.0, rol=0.215, written=0.15, signed=0.12, rarc=9.0),
                _occ("L2", "2nd Wildfire XL", limit=40.0, attachment=65.0, rol=0.135, written=0.15, signed=0.10, rarc=7.2),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-015",
            unique_code="RL-2027-020",
            program_code="PGM-020",
            cedent_name="Foothills Mutual Indemnity",
            programme_name="CA Wildfire & Quake Annual Agg XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="UW Team C",
            broker="Howden Re",
            status=ProgrammeStatus.BOUND,
            structure=StructureType.AGG_XL,
            region_perils=["US-CA-WF", "US-CA-EQ"],
            perils=[PerilCode.WF, PerilCode.EQ],
            cedent_credit_rating="AM Best A-",
            custom_tags=["Wildfire", "US Quake", "Aggregate XL"],
            uplift_rules=[
                _uplift("US-CA-WF", 1.09, "CA wildfire severity load"),
                _uplift("US-CA-EQ", 1.05, "CA EQ baseline load"),
            ],
            layers=[
                _agg("L1", "CA Multi-Peril Agg", limit=30.0, attachment=60.0, rol=0.19, written=0.16, signed=0.12, rarc=7.8, franchise=8.0, per_event_cap=25.0),
            ],
        ),

        # ── European Windstorm ──
        ProgrammeSeed(
            programme_id="prog-syn-016",
            unique_code="RL-2027-021",
            program_code="PGM-021",
            cedent_name="Nordsee Versicherung AG",
            programme_name="Pan-European Windstorm 3-Layer XL",
            treaty_year=2027,
            renewal_period="J27",
            office="LON",
            underwriter="UW Team B",
            broker="Guy Carpenter",
            status=ProgrammeStatus.BOUND,
            region_perils=["EU-DE-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="DE",
            cedent_credit_rating="S&P AA-",
            custom_tags=["EU Wind", "Diversifier", "3-Layer Tower"],
            uplift_rules=[_uplift("EU-DE-WS", 1.05, "European winter storm clustering load")],
            layers=[
                _occ("L1", "1st EU Wind XL", limit=25.0, attachment=30.0, rol=0.18, written=0.18, signed=0.15, rarc=4.5),
                _occ("L2", "2nd EU Wind XL", limit=40.0, attachment=55.0, rol=0.11, written=0.18, signed=0.14, rarc=3.8),
                _occ("L3", "3rd EU Wind XL", limit=60.0, attachment=95.0, rol=0.062, written=0.15, signed=0.12, rarc=2.9),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-017",
            unique_code="RL-2027-022",
            program_code="PGM-022",
            cedent_name="Helvetia-Alpen Re",
            programme_name="Central Europe Windstorm XL",
            treaty_year=2027,
            renewal_period="J27",
            office="ZRH",
            underwriter="UW Team C",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["EU-DE-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="CH",
            cedent_credit_rating="S&P A+",
            custom_tags=["EU Wind", "Diversifier"],
            uplift_rules=[_uplift("EU-DE-WS", 1.04, "DACH wind inflation load")],
            layers=[
                _occ("L1", "1st Alpine Storm", limit=30.0, attachment=40.0, rol=0.145, written=0.18, signed=0.15, rarc=3.6),
                _occ("L2", "2nd Alpine Storm", limit=45.0, attachment=70.0, rol=0.082, written=0.16, signed=0.14, rarc=2.8),
            ],
        ),

        # ── Japan Typhoon & Quake ──
        ProgrammeSeed(
            programme_id="prog-syn-018",
            unique_code="RL-2027-023",
            program_code="PGM-023",
            chain_id="chain-syn-nippon",
            prior_contract_id="prog-syn-041",
            cedent_name="Nippon Marine & Fire Co.",
            programme_name="Japan Typhoon Occurrence XL Tower",
            treaty_year=2027,
            renewal_period="AP27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["JP-TY-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="JP",
            cedent_credit_rating="S&P A+",
            custom_tags=["Japan Typhoon", "Diversifier", "Renewal"],
            note_body="April 1 Japan renewal; clean loss record with +4.1% weighted RARC across 3 typhoon layers.",
            uplift_rules=[_uplift("JP-TY-WS", 1.06, "Japan typhoon flood/surge follow-on load")],
            layers=[
                _occ("L1", "1st JP Typhoon", limit=35.0, attachment=45.0, rol=0.165, written=0.18, signed=0.14, rarc=4.8),
                _occ("L2", "2nd JP Typhoon", limit=55.0, attachment=80.0, rol=0.105, written=0.16, signed=0.12, rarc=3.9),
                _occ("L3", "3rd JP Typhoon", limit=80.0, attachment=135.0, rol=0.062, written=0.14, signed=0.10, rarc=2.8),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-019",
            unique_code="RL-2027-024",
            program_code="PGM-024",
            cedent_name="Kansai General Insurance",
            programme_name="Japan Typhoon & EQ Top-and-Drop",
            treaty_year=2027,
            renewal_period="AP27",
            office="LON",
            underwriter="UW Team C",
            broker="Guy Carpenter",
            status=ProgrammeStatus.BOUND,
            structure=StructureType.CASCADING,
            region_perils=["JP-TY-WS", "JP-KANTO-EQ"],
            perils=[PerilCode.WS, PerilCode.EQ],
            trading_unit="International Property",
            cedent_country="JP",
            cedent_credit_rating="AM Best A",
            custom_tags=["Japan Typhoon", "Japan EQ", "Cascading"],
            inuring_note="Dual-peril Japan cascading tower covers Typhoon and Kanto/Tokai EQ.",
            uplift_rules=[
                _uplift("JP-TY-WS", 1.05, "Kansai typhoon surge load"),
                _uplift("JP-KANTO-EQ", 1.06, "Japan EQ building-code uplift"),
            ],
            layers=[
                _cascading(
                    "L1",
                    "Japan Top & Drop",
                    written=0.18,
                    signed=0.14,
                    rarc=4.2,
                    sub_layers=[
                        {"occLimit": 30.0, "occAttachment": 55.0, "occFranchise": 0.0, "reinstatements": 1.0, "ripRate": 1.0, "depositPremium": 5.1},
                        {"occLimit": 45.0, "occAttachment": 85.0, "occFranchise": 0.0, "reinstatements": 1.0, "ripRate": 1.0, "depositPremium": 4.95},
                    ],
                ),
            ],
        ),

        # ── Australia Cyclone ──
        ProgrammeSeed(
            programme_id="prog-syn-020",
            unique_code="RL-2027-025",
            program_code="PGM-025",
            chain_id="chain-syn-southerncross",
            prior_contract_id="prog-syn-042",
            cedent_name="Southern Cross General Ins.",
            programme_name="Australia Cyclone & East Coast XL",
            treaty_year=2027,
            renewal_period="JL27",
            office="LON",
            underwriter="UW Team B",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["AU-QLD-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="AU",
            cedent_credit_rating="S&P A+",
            custom_tags=["Australia Cyclone", "Diversifier", "Renewal"],
            note_body="July 1 ANZ renewal; strong non-correlating Southern Hemisphere diversifier.",
            uplift_rules=[_uplift("AU-QLD-WS", 1.06, "Queensland cyclone repair inflation")],
            layers=[
                _occ("L1", "1st AU Cyclone", limit=30.0, attachment=40.0, rol=0.155, written=0.18, signed=0.15, rarc=5.1),
                _occ("L2", "2nd AU Cyclone", limit=50.0, attachment=70.0, rol=0.092, written=0.16, signed=0.12, rarc=4.0),
            ],
        ),

        # ── Global Multi-Peril & Cyber / Specialty ──
        ProgrammeSeed(
            programme_id="prog-syn-021",
            unique_code="RL-2027-026",
            program_code="PGM-026",
            cedent_name="Meridian Global Reinsurance",
            programme_name="Worldwide All-Perils 4-Layer Cat XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Guy Carpenter",
            status=ProgrammeStatus.BOUND,
            region_perils=["US-FL-WS", "US-TX-WS", "US-CA-EQ", "EU-DE-WS", "JP-TY-WS", "AU-QLD-WS"],
            perils=[PerilCode.WS, PerilCode.EQ],
            trading_unit="Global Retro & Multi-Peril",
            cedent_country="BMU",
            cedent_credit_rating="S&P A+",
            custom_tags=["Global Multi-Peril", "4-Layer Tower"],
            note_body="Flagship worldwide occurrence tower across US, EU, Japan, and ANZ; disciplined 8–10% signed lines.",
            uplift_rules=[
                _uplift("US-FL-WS", 1.07, "Global FL wind uplift"),
                _uplift("US-CA-EQ", 1.05, "Global CA EQ uplift"),
            ],
            layers=[
                _occ("L1", "WW 1st Layer", limit=30.0, attachment=75.0, rol=0.26, written=0.12, signed=0.10, rarc=6.2),
                _occ("L2", "WW 2nd Layer", limit=50.0, attachment=105.0, rol=0.19, written=0.12, signed=0.10, rarc=5.4),
                _occ("L3", "WW 3rd Layer", limit=75.0, attachment=155.0, rol=0.13, written=0.10, signed=0.08, rarc=4.2),
                _occ("L4", "WW 4th Layer", limit=100.0, attachment=230.0, rol=0.085, written=0.10, signed=0.08, rarc=3.0),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-022",
            unique_code="RL-2027-027",
            program_code="PGM-027",
            cedent_name="Aegis Cyber Assurance",
            programme_name="Global Cyber Catastrophe XL",
            treaty_year=2027,
            renewal_period="J27",
            office="LON",
            underwriter="UW Team C",
            broker="Howden Re",
            status=ProgrammeStatus.BOUND,
            region_perils=["GL-CY-CY"],
            perils=[PerilCode.CY],
            trading_unit="Specialty & Cyber",
            planning_class="Cyber CAT",
            cedent_country="USA",
            cedent_credit_rating="AM Best A",
            custom_tags=["Cyber", "Specialty", "Diversifier"],
            note_body="Cloud-outage and systemic ransomware cat XL; zero natural-cat correlation.",
            uplift_rules=[_uplift("GL-CY-CY", 1.12, "Systemic cloud-aggregation severity load")],
            layers=[
                _occ("L1", "1st Cyber Cat XL", limit=25.0, attachment=35.0, rol=0.19, written=0.15, signed=0.12, rarc=11.0),
                _occ("L2", "2nd Cyber Cat XL", limit=40.0, attachment=60.0, rol=0.12, written=0.15, signed=0.10, rarc=9.2),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-023",
            unique_code="RL-2027-028",
            program_code="PGM-028",
            cedent_name="Sentinel Specialty Underwriters",
            programme_name="Cyber & Specialty Proportional QS",
            treaty_year=2027,
            renewal_period="J27",
            office="LON",
            underwriter="UW Team C",
            broker="Aon Re",
            status=ProgrammeStatus.BOUND,
            structure=StructureType.QUOTA_SHARE,
            region_perils=["GL-CY-CY"],
            perils=[PerilCode.CY],
            trading_unit="Specialty & Cyber",
            planning_class="Specialty Quota Share",
            cedent_country="UK",
            cedent_credit_rating="S&P A",
            brokerage_pct=0.05,
            custom_tags=["Cyber", "Specialty", "Quota Share"],
            uplift_rules=[_uplift("GL-CY-CY", 1.10, "Specialty cyber loss-trend load")],
            layers=[
                _qs("L1", "Cyber Specialty QS", cession=0.20, event_cap=20.0, agg_cap=40.0, deposit_premium=7.8, written=0.18, signed=0.14, rarc=10.5),
            ],
        ),

        # ── QUOTED Assumed Deals (3 new) ──
        ProgrammeSeed(
            programme_id="prog-syn-024",
            unique_code="RL-2027-029",
            program_code="PGM-029",
            cedent_name="Lone Star Property Syndicate",
            programme_name="Texas Coastal Hurricane XL",
            treaty_year=2027,
            renewal_period="MY27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Gallagher Re",
            status=ProgrammeStatus.QUOTED,
            region_perils=["US-TX-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best A-",
            custom_tags=["Gulf Wind", "Pipeline"],
            uplift_rules=[_uplift("US-TX-WS", 1.08, "Houston/Galveston exposure load")],
            layers=[
                _occ("L1", "1st Texas Wind XL", limit=30.0, attachment=45.0, rol=0.21, written=0.15, signed=0.12, rarc=6.0),
                _occ("L2", "2nd Texas Wind XL", limit=50.0, attachment=75.0, rol=0.13, written=0.15, signed=0.10, rarc=4.5),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-025",
            unique_code="RL-2027-030",
            program_code="PGM-030",
            cedent_name="Redwood Specialty Lines",
            programme_name="California DIC Earthquake Cat XL",
            treaty_year=2027,
            renewal_period="JL27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Howden Re",
            status=ProgrammeStatus.QUOTED,
            region_perils=["US-CA-EQ"],
            perils=[PerilCode.EQ],
            cedent_credit_rating="AM Best A-",
            custom_tags=["US Quake", "DIC", "Pipeline"],
            uplift_rules=[_uplift("US-CA-EQ", 1.08, "DIC commercial contents load")],
            layers=[
                _occ("L1", "1st DIC EQ XL", limit=25.0, attachment=50.0, rol=0.165, written=0.15, signed=0.10, rarc=5.8),
                _occ("L2", "2nd DIC EQ XL", limit=45.0, attachment=75.0, rol=0.11, written=0.12, signed=0.10, rarc=4.6),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-026",
            unique_code="RL-2027-031",
            program_code="PGM-031",
            cedent_name="Benelux Property Assurances",
            programme_name="Northern Europe Windstorm Agg XL",
            treaty_year=2027,
            renewal_period="J27",
            office="LON",
            underwriter="UW Team C",
            broker="Broker C",
            status=ProgrammeStatus.QUOTED,
            structure=StructureType.AGG_XL,
            region_perils=["EU-DE-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="NL",
            cedent_credit_rating="S&P A",
            custom_tags=["EU Wind", "Aggregate XL", "Pipeline"],
            uplift_rules=[_uplift("EU-DE-WS", 1.05, "Benelux storm clustering load")],
            layers=[
                _agg("L1", "EU Wind Agg XL", limit=25.0, attachment=45.0, rol=0.165, written=0.15, signed=0.12, rarc=4.0, franchise=5.0, per_event_cap=20.0),
            ],
        ),

        # ── AUTHORIZED Assumed Deals (3 new) ──
        ProgrammeSeed(
            programme_id="prog-syn-027",
            unique_code="RL-2027-032",
            program_code="PGM-032",
            cedent_name="Pelican Bay Underwriters",
            programme_name="Gulf & FL Top-and-Drop Cat XL",
            treaty_year=2027,
            renewal_period="JU27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Howden Re",
            status=ProgrammeStatus.AUTHORIZED,
            structure=StructureType.CASCADING,
            region_perils=["US-FL-WS", "US-TX-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best B++",
            custom_tags=["Peak", "Cascading", "Authorized"],
            uplift_rules=[_uplift("US-FL-WS", 1.10, "FL/Gulf surge load")],
            layers=[
                _cascading(
                    "L1",
                    "Gulf/FL Top & Drop",
                    written=0.16,
                    signed=0.12,
                    rarc=7.0,
                    sub_layers=[
                        {"occLimit": 25.0, "occAttachment": 55.0, "occFranchise": 0.0, "reinstatements": 1.0, "ripRate": 1.0, "depositPremium": 5.5},
                        {"occLimit": 40.0, "occAttachment": 80.0, "occFranchise": 0.0, "reinstatements": 1.0, "ripRate": 1.0, "depositPremium": 5.2},
                    ],
                ),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-028",
            unique_code="RL-2027-033",
            program_code="PGM-033",
            cedent_name="Ozark Mutual Group",
            programme_name="Central US Convective Storm Agg XL",
            treaty_year=2027,
            renewal_period="MY27",
            office="BDA",
            underwriter="UW Team B",
            broker="Guy Carpenter",
            status=ProgrammeStatus.AUTHORIZED,
            structure=StructureType.AGG_XL,
            region_perils=["US-MW-CS"],
            perils=[PerilCode.CS],
            cedent_credit_rating="AM Best A-",
            custom_tags=["SCS", "Aggregate XL", "Authorized"],
            uplift_rules=[_uplift("US-MW-CS", 1.09, "Central US hail trend")],
            layers=[
                _agg("L1", "Central SCS Agg", limit=30.0, attachment=48.0, rol=0.24, written=0.15, signed=0.12, rarc=8.8, franchise=5.0, per_event_cap=20.0),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-029",
            unique_code="RL-2027-034",
            program_code="PGM-034",
            cedent_name="Vanguard International Indemnity",
            programme_name="International Multi-Peril 4-Layer XL",
            treaty_year=2027,
            renewal_period="J27",
            office="LON",
            underwriter="UW Team C",
            broker="Gallagher Re",
            status=ProgrammeStatus.AUTHORIZED,
            region_perils=["EU-DE-WS", "JP-TY-WS", "JP-KANTO-EQ", "AU-QLD-WS"],
            perils=[PerilCode.WS, PerilCode.EQ],
            trading_unit="International Property",
            cedent_country="UK",
            cedent_credit_rating="AM Best A+",
            custom_tags=["Global Multi-Peril", "4-Layer Tower", "Authorized"],
            uplift_rules=[
                _uplift("EU-DE-WS", 1.04, "EU wind inflation"),
                _uplift("JP-TY-WS", 1.05, "JP typhoon surge load"),
            ],
            layers=[
                _occ("L1", "Intl 1st XL", limit=25.0, attachment=50.0, rol=0.195, written=0.15, signed=0.12, rarc=4.6),
                _occ("L2", "Intl 2nd XL", limit=35.0, attachment=75.0, rol=0.135, written=0.15, signed=0.12, rarc=3.8),
                _occ("L3", "Intl 3rd XL", limit=50.0, attachment=110.0, rol=0.088, written=0.12, signed=0.10, rarc=3.1),
                _occ("L4", "Intl 4th XL", limit=70.0, attachment=160.0, rol=0.055, written=0.12, signed=0.10, rarc=2.4),
            ],
        ),

        # ── DECLINED Deals (2 new, signed_line=0.0) ──
        ProgrammeSeed(
            programme_id="prog-syn-030",
            unique_code="RL-2027-035",
            program_code="PGM-035",
            cedent_name="Sunbelt Direct Insurance",
            programme_name="Florida Working-Layer Cat XL",
            treaty_year=2027,
            renewal_period="JU27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Broker A",
            status=ProgrammeStatus.DECLINED,
            region_perils=["US-FL-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best B+",
            custom_tags=["FL Wind", "Declined"],
            note_body="Declined: low $20m attachment and -3.5% RARC miss fund technical return hurdle.",
            uplift_rules=[_uplift("US-FL-WS", 1.12, "FL working-layer claims inflation")],
            layers=[
                _occ("L1", "Low Attach XL", limit=20.0, attachment=20.0, rol=0.26, written=0.0, signed=0.0, rarc=-3.5),
                _occ("L2", "Mid Attach XL", limit=30.0, attachment=40.0, rol=0.175, written=0.0, signed=0.0, rarc=-2.0),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-031",
            unique_code="RL-2027-036",
            program_code="PGM-036",
            cedent_name="Kyushu Regional Mutual",
            programme_name="Kyushu Working Typhoon XL",
            treaty_year=2027,
            renewal_period="AP27",
            office="LON",
            underwriter="UW Team C",
            broker="Broker B",
            status=ProgrammeStatus.DECLINED,
            region_perils=["JP-TY-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="JP",
            cedent_credit_rating="AM Best A-",
            custom_tags=["Japan Typhoon", "Declined"],
            note_body="Declined: working layer pricing softened 4% YoY below technical floor.",
            uplift_rules=[_uplift("JP-TY-WS", 1.08, "Kyushu coastal flood exposure")],
            layers=[
                _occ("L1", "Kyushu Working XL", limit=25.0, attachment=20.0, rol=0.18, written=0.0, signed=0.0, rarc=-4.0),
            ],
        ),

        # ── NTU Deals (2 new, signed_line=0.0) ──
        ProgrammeSeed(
            programme_id="prog-syn-032",
            unique_code="RL-2027-037",
            program_code="PGM-037",
            cedent_name="Puget Sound Indemnity",
            programme_name="Washington & Oregon EQ XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="UW Team B",
            broker="Broker B",
            status=ProgrammeStatus.NTU,
            region_perils=["US-PNW-EQ"],
            perils=[PerilCode.EQ],
            cedent_credit_rating="AM Best A-",
            custom_tags=["US Quake", "PNW", "NTU"],
            note_body="NTU: quoted 10% line at 9.5% ROL; cedent placed privately at lower rate.",
            uplift_rules=[_uplift("US-PNW-EQ", 1.04, "PNW baseline uplift")],
            layers=[
                _occ("L1", "PNW EQ XL", limit=40.0, attachment=50.0, rol=0.095, written=0.10, signed=0.0, rarc=-1.2),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-033",
            unique_code="RL-2027-038",
            program_code="PGM-038",
            cedent_name="Coral Sea Mutual",
            programme_name="Queensland Cyclone Cat XL",
            treaty_year=2027,
            renewal_period="JL27",
            office="LON",
            underwriter="UW Team B",
            broker="Broker C",
            status=ProgrammeStatus.NTU,
            region_perils=["AU-QLD-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="AU",
            cedent_credit_rating="AM Best A-",
            custom_tags=["Australia Cyclone", "NTU"],
            uplift_rules=[_uplift("AU-QLD-WS", 1.05, "QLD wind trend")],
            layers=[
                _occ("L1", "QLD Cyclone XL", limit=35.0, attachment=45.0, rol=0.13, written=0.12, signed=0.0, rarc=-0.8),
            ],
        ),

        # ── ILWs (2 Long + 1 Hedge) ──
        ProgrammeSeed(
            programme_id="prog-syn-034",
            unique_code="RL-2027-039",
            program_code="PGM-039",
            cedent_name="Bermuda Index Partners",
            programme_name="US Gulf Wind $35B Pro-Rata ILW",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Broker C",
            status=ProgrammeStatus.BOUND,
            direction=ContractDirection.ASSUMED,
            structure=StructureType.ILW,
            instrument=InstrumentType.INDUSTRY_INDEX,
            placement=PlacementType.PRIVATE,
            trading_unit="ILW & Index",
            planning_class="ILW Assumed",
            cedent_country="BMU",
            cedent_credit_rating="Collateralized",
            new_or_renewal="NEW",
            brokerage_pct=0.0,
            region_perils=["US-TX-WS"],
            perils=[PerilCode.WS],
            custom_tags=["ILW", "Long", "Gulf Wind"],
            inuring_note="Sold PCS Gulf Wind pro-rata ILW ($35B trigger to $55B exhaustion).",
            ilw_spec={
                "industrySimSetId": DEFAULT_INDUSTRY_SIM_SET_ID,
                "regionPerils": ["US-TX-WS"],
                "triggerType": "PRO_RATA",
                "triggerBn": 35.0,
                "exhaustionBn": 55.0,
                "limitM": 20.0,
                "rol": 0.165,
                "direction": "ASSUMED",
            },
            layers=[
                _ilw("L1", "Gulf $35B-$55B ILW", limit=20.0, trigger_bn=35.0, exhaustion_bn=55.0, rol=0.165, trigger_type=IlwTriggerType.PRO_RATA, rarc=3.5),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-035",
            unique_code="RL-2027-040",
            program_code="PGM-040",
            cedent_name="Pacific Index Syndicate",
            programme_name="California Quake $30B Binary ILW",
            treaty_year=2027,
            renewal_period="AP27",
            office="BDA",
            underwriter="UW Team C",
            broker="Broker C",
            status=ProgrammeStatus.QUOTED,
            direction=ContractDirection.ASSUMED,
            structure=StructureType.ILW,
            instrument=InstrumentType.INDUSTRY_INDEX,
            placement=PlacementType.PRIVATE,
            trading_unit="ILW & Index",
            planning_class="ILW Assumed",
            cedent_country="USA",
            cedent_credit_rating="Collateralized",
            new_or_renewal="NEW",
            brokerage_pct=0.0,
            region_perils=["US-CA-EQ"],
            perils=[PerilCode.EQ],
            custom_tags=["ILW", "Long", "CA Quake"],
            inuring_note="Sold PCS California Quake $30B binary ILW; attractive diversifying index capacity.",
            ilw_spec={
                "industrySimSetId": DEFAULT_INDUSTRY_SIM_SET_ID,
                "regionPerils": ["US-CA-EQ"],
                "triggerType": "BINARY",
                "triggerBn": 30.0,
                "limitM": 20.0,
                "rol": 0.14,
                "direction": "ASSUMED",
            },
            layers=[
                _ilw("L1", "CA EQ $30B ILW", limit=20.0, trigger_bn=30.0, rol=0.14, trigger_type=IlwTriggerType.BINARY, rarc=2.8),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-036",
            unique_code="RL-2027-041",
            program_code="PGM-041",
            cedent_name="Retro Index Protection II",
            programme_name="US Wind Nationwide $55B Binary Retro ILW",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Broker C",
            status=ProgrammeStatus.BOUND,
            direction=ContractDirection.EXTERNAL_RETRO,
            structure=StructureType.ILW,
            instrument=InstrumentType.INDUSTRY_INDEX,
            placement=PlacementType.PRIVATE,
            trading_unit="Retro & Hedging",
            planning_class="ILW Hedge",
            cedent_country="BMU",
            cedent_credit_rating="Collateralized",
            new_or_renewal="NEW",
            brokerage_pct=0.0,
            region_perils=["US-FL-WS", "US-TX-WS", "US-SE-WS", "US-NE-WS"],
            perils=[PerilCode.WS],
            custom_tags=["ILW", "Hedge", "US Wind"],
            inuring_note="PCS US All-Wind $55B binary retro ILW hedging combined peak US hurricane tail.",
            ilw_spec={
                "industrySimSetId": DEFAULT_INDUSTRY_SIM_SET_ID,
                "regionPerils": ["US-FL-WS", "US-TX-WS", "US-SE-WS", "US-NE-WS"],
                "triggerType": "BINARY",
                "triggerBn": 55.0,
                "limitM": 30.0,
                "rol": 0.115,
                "direction": "EXTERNAL_RETRO",
            },
            layers=[
                _ilw("L1", "US Wind $55B Retro ILW", limit=30.0, trigger_bn=55.0, rol=0.115, trigger_type=IlwTriggerType.BINARY, rarc=-1.5),
            ],
        ),

        # ── UNL Retro Hedges (1 BOUND Occ XL + 1 QUOTED Agg XL) ──
        ProgrammeSeed(
            programme_id="prog-syn-037",
            unique_code="RL-2027-042",
            program_code="PGM-042",
            cedent_name="RoaringLion UNL Retro",
            programme_name="US Peak Wind UNL Retro XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Broker A",
            status=ProgrammeStatus.BOUND,
            direction=ContractDirection.EXTERNAL_RETRO,
            structure=StructureType.OCC_XL,
            instrument=InstrumentType.UNL,
            placement=PlacementType.PRIVATE,
            trading_unit="Retro & Hedging",
            planning_class="UNL Retrocession",
            cedent_country="BMU",
            cedent_credit_rating="AM Best A",
            new_or_renewal="NEW",
            brokerage_pct=0.0,
            portfolio_snapshot_date="2027-01-01",
            region_perils=["US-FL-WS", "US-TX-WS", "US-SE-WS", "US-NE-WS"],
            perils=[PerilCode.WS],
            custom_tags=["Retro", "Hedge", "UNL", "US Wind"],
            inuring_note="Occurrence UNL retro protecting peak US Wind tail on our own 2027-01-01 portfolio snapshot.",
            layers=[
                _occ("L1", "US Wind UNL Retro", limit=30.0, attachment=80.0, rol=0.11, written=1.0, signed=1.0, rarc=-1.5, reinstatements=1.0, rip_rate=1.0, brokerage=0.0),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-038",
            unique_code="RL-2027-043",
            program_code="PGM-043",
            cedent_name="RoaringLion UNL Retro",
            programme_name="Whole-Account Aggregate UNL Retro XL",
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Broker A",
            status=ProgrammeStatus.QUOTED,
            direction=ContractDirection.EXTERNAL_RETRO,
            structure=StructureType.AGG_XL,
            instrument=InstrumentType.UNL,
            placement=PlacementType.PRIVATE,
            trading_unit="Retro & Hedging",
            planning_class="UNL Retrocession",
            cedent_country="BMU",
            cedent_credit_rating="AM Best A",
            new_or_renewal="NEW",
            brokerage_pct=0.0,
            portfolio_snapshot_date="2027-01-01",
            region_perils=[],
            perils=[PerilCode.WS, PerilCode.EQ, PerilCode.CS, PerilCode.WF, PerilCode.CY],
            custom_tags=["Retro", "Hedge", "UNL", "Aggregate XL"],
            inuring_note="Annual aggregate UNL retro over our 2027-01-01 whole-account snapshot.",
            layers=[
                _agg("L1", "Whole-Acct Agg Retro", limit=25.0, attachment=55.0, rol=0.14, written=1.0, signed=1.0, rarc=-1.0, franchise=5.0, per_event_cap=25.0, brokerage=0.0),
            ],
        ),

        # ── Prior-Year 2026 EXPIRED Renewals (4 chains; intentionally exclude EU-DE-WS) ──
        ProgrammeSeed(
            programme_id="prog-syn-039",
            unique_code="RL-2026-002",
            program_code="PGM-006",
            chain_id="chain-syn-bayshore",
            cedent_name="Bayshore Property Mutual",
            programme_name="Florida Homeowners Cat XL Tower (2026 Expiring)",
            treaty_year=2026,
            renewal_period="JU26",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Guy Carpenter",
            status=ProgrammeStatus.EXPIRED,
            region_perils=["US-FL-WS"],
            perils=[PerilCode.WS],
            cedent_credit_rating="AM Best A-",
            written_date="2025-11-22",
            custom_tags=["Peak", "FL Wind", "Expiring"],
            uplift_rules=[_uplift("US-FL-WS", 1.07, "2026 FL house uplift")],
            layers=[
                _occ("L1", "1st Cat XL", limit=25.0, attachment=35.0, rol=0.26, written=0.15, signed=0.12, rarc=5.0),
                _occ("L2", "2nd Cat XL", limit=35.0, attachment=60.0, rol=0.185, written=0.15, signed=0.12, rarc=4.2),
                _occ("L3", "3rd Cat XL", limit=55.0, attachment=95.0, rol=0.12, written=0.12, signed=0.10, rarc=3.5),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-040",
            unique_code="RL-2026-003",
            program_code="PGM-013",
            chain_id="chain-syn-goldengate",
            cedent_name="Golden Gate Earthquake Authority",
            programme_name="California Residential EQ Tower (2026 Expiring)",
            treaty_year=2026,
            renewal_period="J26",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Aon Re",
            status=ProgrammeStatus.EXPIRED,
            region_perils=["US-CA-EQ"],
            perils=[PerilCode.EQ],
            cedent_credit_rating="S&P A+",
            written_date="2025-11-18",
            custom_tags=["US Quake", "California", "Expiring"],
            uplift_rules=[_uplift("US-CA-EQ", 1.05, "2026 CA EQ fire-following load")],
            layers=[
                _occ("L1", "1st CA EQ XL", limit=40.0, attachment=55.0, rol=0.148, written=0.15, signed=0.12, rarc=3.8),
                _occ("L2", "2nd CA EQ XL", limit=55.0, attachment=95.0, rol=0.098, written=0.14, signed=0.11, rarc=3.1),
                _occ("L3", "3rd CA EQ XL", limit=90.0, attachment=150.0, rol=0.062, written=0.12, signed=0.10, rarc=2.5),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-041",
            unique_code="RL-2026-004",
            program_code="PGM-023",
            chain_id="chain-syn-nippon",
            cedent_name="Nippon Marine & Fire Co.",
            programme_name="Japan Typhoon Occurrence XL Tower (2026 Expiring)",
            treaty_year=2026,
            renewal_period="AP26",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Aon Re",
            status=ProgrammeStatus.EXPIRED,
            region_perils=["JP-TY-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="JP",
            cedent_credit_rating="S&P A+",
            written_date="2026-02-20",
            custom_tags=["Japan Typhoon", "Expiring"],
            uplift_rules=[_uplift("JP-TY-WS", 1.04, "2026 JP typhoon baseline")],
            layers=[
                _occ("L1", "1st JP Typhoon", limit=35.0, attachment=42.0, rol=0.158, written=0.16, signed=0.14, rarc=3.5),
                _occ("L2", "2nd JP Typhoon", limit=50.0, attachment=77.0, rol=0.10, written=0.15, signed=0.12, rarc=2.8),
                _occ("L3", "3rd JP Typhoon", limit=75.0, attachment=127.0, rol=0.06, written=0.12, signed=0.10, rarc=2.1),
            ],
        ),
        ProgrammeSeed(
            programme_id="prog-syn-042",
            unique_code="RL-2026-005",
            program_code="PGM-025",
            chain_id="chain-syn-southerncross",
            cedent_name="Southern Cross General Ins.",
            programme_name="Australia Cyclone & East Coast XL (2026 Expiring)",
            treaty_year=2026,
            renewal_period="JL26",
            office="LON",
            underwriter="UW Team B",
            broker="Aon Re",
            status=ProgrammeStatus.EXPIRED,
            region_perils=["AU-QLD-WS"],
            perils=[PerilCode.WS],
            trading_unit="International Property",
            cedent_country="AU",
            cedent_credit_rating="S&P A+",
            written_date="2026-05-15",
            custom_tags=["Australia Cyclone", "Expiring"],
            uplift_rules=[_uplift("AU-QLD-WS", 1.04, "2026 AU cyclone baseline")],
            layers=[
                _occ("L1", "1st AU Cyclone", limit=30.0, attachment=38.0, rol=0.148, written=0.16, signed=0.14, rarc=3.6),
                _occ("L2", "2nd AU Cyclone", limit=45.0, attachment=68.0, rol=0.088, written=0.15, signed=0.12, rarc=2.9),
            ],
        ),
    ]


def _build_seed_graph(
    spec: ProgrammeSeed,
    *,
    sim_set_id: str,
    source_loss_files: list[dict[str, Any]],
) -> GraphSpec:
    pid = spec.programme_id
    src_id = f"src-{pid}"
    flt_id = f"flt-{pid}"
    out_id = f"out-{pid}"

    if is_ilw_programme(spec.structure, spec.instrument):
        src_params: dict[str, Any] = {
            "simSetId": sim_set_id,
            "sourceKind": "INDUSTRY_LOSS_RUN",
            "labelName": "Industry Loss Run",
        }
        if source_loss_files:
            src_params["lossFiles"] = source_loss_files
        src_node = GraphNodeSpec(
            node_id=src_id,
            node_type=NodeType.INDUSTRY_INDEX_SOURCE,
            label=smart_label(NodeType.INDUSTRY_INDEX_SOURCE, src_params, "Industry Loss Run"),
            params=src_params,
            position={"x": 40, "y": 100},
        )
    elif is_unl_retro_hedge(spec.direction, spec.structure, spec.instrument):
        as_of = spec.portfolio_snapshot_date or f"{spec.treaty_year}-01-01"
        src_params = {
            "simSetId": sim_set_id,
            "sourceKind": "UNL_PORTFOLIO_SNAPSHOT",
            "asOfDate": as_of,
            "labelName": "UNL Portfolio",
            "lossFiles": source_loss_files,
        }
        src_node = GraphNodeSpec(
            node_id=src_id,
            node_type=NodeType.PORTFOLIO_ROLLUP,
            label=smart_label(NodeType.PORTFOLIO_ROLLUP, src_params, "UNL Portfolio"),
            params=src_params,
            position={"x": 40, "y": 100},
        )
    else:
        src_params = {"simSetId": sim_set_id}
        if source_loss_files:
            src_params["lossFiles"] = source_loss_files
        src_node = GraphNodeSpec(
            node_id=src_id,
            node_type=NodeType.LOSS_SOURCE,
            label=f"Loss Source ({sim_set_id})",
            params=src_params,
            position={"x": 40, "y": 100},
        )

    flt_rps = [rp for rp in spec.region_perils if rp and rp != "ALL"]
    flt_node = GraphNodeSpec(
        node_id=flt_id,
        node_type=NodeType.REGION_PERIL_FILTER,
        label=f"Zone Peril ({', '.join(flt_rps)})" if flt_rps else "Zone Peril — All zones & perils",
        params={"regionPerils": flt_rps, "labelName": "Zone Peril"},
        position={"x": 300, "y": 100},
    )

    nodes: list[GraphNodeSpec] = [src_node, flt_node]
    edges: list[GraphEdgeSpec] = [
        GraphEdgeSpec(edge_id=f"e1-{pid}", source=src_id, target=flt_id, sign=1, weight=1.0),
    ]

    for i, layer_seed in enumerate(spec.layers):
        lyr_id = f"lyr-{pid}" if i == 0 else f"lyr-{pid}-{i + 1}"
        y = 20 + i * 140
        l_params = dict(layer_seed.params)
        lyr_node = GraphNodeSpec(
            node_id=lyr_id,
            node_type=layer_seed.node_type,
            label=smart_label(layer_seed.node_type, l_params, layer_seed.name),
            params=l_params,
            position={"x": 600, "y": y},
        )
        px_id = f"px-{lyr_id}"
        linked_label = f"{spec.unique_code} · {layer_seed.layer_code} · {layer_seed.name}"
        px_params = {
            "contractId": pid,
            "layerNodeId": lyr_id,
            "labelName": "Pricing",
            "linkedLabel": linked_label,
        }
        px_node = GraphNodeSpec(
            node_id=px_id,
            node_type=NodeType.PRICING_OUTPUT,
            label=smart_label(NodeType.PRICING_OUTPUT, px_params, f"Pricing · {layer_seed.name}"),
            params=px_params,
            position={"x": 920, "y": y},
        )
        nodes.extend([lyr_node, px_node])
        edges.extend([
            GraphEdgeSpec(edge_id=f"e-in-{lyr_id}", source=flt_id, target=lyr_id, sign=1, weight=1.0),
            GraphEdgeSpec(edge_id=f"e-px-{lyr_id}", source=lyr_id, target=px_id, sign=1, weight=1.0),
            GraphEdgeSpec(edge_id=f"e-out-{lyr_id}", source=lyr_id, target=out_id, sign=1, weight=1.0),
        ])

    nodes.append(
        GraphNodeSpec(
            node_id=out_id,
            node_type=NodeType.YELT_OUTPUT,
            label="Programme YELT",
            params={"view": "ADJUSTED"},
            position={"x": 1220, "y": 20 + max(len(spec.layers) - 1, 0) * 70},
        )
    )

    graph = GraphSpec(
        graph_id=f"grp-{pid}",
        name=f"{spec.cedent_name} — {spec.programme_name}",
        version=1,
        currency="USD",
        nodes=nodes,
        edges=edges,
        content_hash="",
    )
    relabel_graph(graph)
    graph.content_hash = canonical_hash({
        "nodes": [n.model_dump() for n in graph.nodes],
        "edges": [e.model_dump() for e in graph.edges],
    })
    return graph


def build_synthetic_book_records(
    *,
    default_sim_set_id: str,
    base_loss_files: list[dict[str, Any]],
    ind_loss_files: list[dict[str, Any]],
    existing_contracts: list[Any],
    roll_up_layers_fn,
) -> tuple[list[ContractRecord], list[ContractNoteRecord]]:
    """Build all 42 synthetic ContractRecord + ContractNoteRecord objects in memory for one fast DB commit."""
    specs = _synthetic_programme_specs()
    # First pass: build non-UNL-retro deals so UNL retro snapshot loss files include the whole bound 2027 book
    non_retro_specs = [s for s in specs if not is_unl_retro_hedge(s.direction, s.structure, s.instrument)]
    retro_specs = [s for s in specs if is_unl_retro_hedge(s.direction, s.structure, s.instrument)]

    records_by_id: dict[str, ContractRecord] = {}
    notes: list[ContractNoteRecord] = []

    def _materialize(spec: ProgrammeSeed, src_files: list[dict[str, Any]]) -> ContractRecord:
        is_ilw = is_ilw_programme(spec.structure, spec.instrument)
        sim_id = DEFAULT_INDUSTRY_SIM_SET_ID if is_ilw else default_sim_set_id
        uplift = UpliftSet(
            uplift_set_id=f"upl-{spec.programme_id}",
            name=f"{spec.programme_name} Uplift",
            version=1,
            rules=list(spec.uplift_rules),
            content_hash=canonical_hash([r.model_dump() for r in spec.uplift_rules]),
        )
        graph = _build_seed_graph(spec, sim_set_id=sim_id, source_loss_files=src_files)
        profile = ProgrammeProfile(
            unique_code=spec.unique_code,
            program_code=spec.program_code,
            legal_entity="RoaringLion Re Transformer",
            trading_unit=spec.trading_unit,
            planning_class=spec.planning_class,
            new_or_renewal=spec.new_or_renewal,
            is_multi_year=False,
            written_date=spec.written_date,
            external_file_path=f"https://docs.internal/reinsurance/{spec.treaty_year}/{spec.programme_id}",
            cedent_group=spec.cedent_name,
            cedent_country=spec.cedent_country,
            cedent_credit_rating=spec.cedent_credit_rating,
            broker_group=spec.broker,
            closing_broker=spec.broker,
            broker_contact="Lead Treaty Desk",
            pricing_analyst="Cat Modeling Desk",
            limit_basis="INDUSTRY_INDEX" if is_ilw else "UNL",
            claim_trigger="LOSSES_OCCURRING",
            loss_event_type="AGGREGATE" if spec.structure == StructureType.AGG_XL else "1ST_LOSS",
            reinstatement_basis="PRO_RATA_AMOUNT",
            inuring_note=spec.inuring_note,
            economic_inflation_pct=3.0,
        )
        rec = ContractRecord(
            programme_id=spec.programme_id,
            chain_id=spec.chain_id or f"chain-{spec.programme_id}",
            prior_contract_id=spec.prior_contract_id,
            cedent_name=spec.cedent_name,
            programme_name=spec.programme_name,
            treaty_year=spec.treaty_year,
            renewal_period=spec.renewal_period,
            office=spec.office,
            underwriter=spec.underwriter,
            broker=spec.broker,
            fronting_carrier="RoaringLion Re Transformer",
            collateral_cell=f"Cell-{spec.renewal_period}",
            status=spec.status.value,
            direction=spec.direction.value,
            structure=spec.structure.value,
            instrument=spec.instrument.value,
            placement=spec.placement.value,
            inception_date=f"{spec.treaty_year}-01-01",
            expiry_date=f"{spec.treaty_year}-12-31",
            portfolio_snapshot_date=spec.portfolio_snapshot_date,
            hours_clause=spec.hours_clause,
            written_line=1.0,
            signed_line=1.0,
            limit=0.0,
            attachment=0.0,
            deposit_premium=0.0,
            rol=0.0,
            brokerage_pct=spec.brokerage_pct,
            fronting_fee_pct=spec.fronting_fee_pct,
            collateral_required=0.0,
            currency="USD",
            sim_set_id=sim_id,
            region_perils_json=json.dumps(spec.region_perils),
            perils_json=json.dumps([p.value for p in spec.perils]),
            custom_tags_json=json.dumps(spec.custom_tags),
            uplift_set_json=uplift.model_dump_json(),
            graph_json=graph.model_dump_json(),
            ilw_spec_json=json.dumps(spec.ilw_spec) if spec.ilw_spec else None,
            profile_json=profile.model_dump_json(),
            version=1,
            content_hash="",
        )
        roll_up_layers_fn(rec, graph)
        rec.collateral_required = (
            0.0 if is_hedge_direction(spec.direction) else round(rec.limit * rec.signed_line, 6)
        )
        # Keep offered_line aligned with rolled-up written_line
        prof = ProgrammeProfile(**json.loads(rec.profile_json))
        prof.offered_line = rec.written_line
        prof.estimated_signed_pct = 1.0 if rec.written_line <= 0 else round(min(rec.signed_line / rec.written_line, 1.0), 4)
        rec.profile_json = prof.model_dump_json()

        rec.content_hash = canonical_hash({
            "programme_id": rec.programme_id,
            "direction": rec.direction,
            "structure": rec.structure,
            "limit": rec.limit,
            "signed_line": rec.signed_line,
            "rol": rec.rol,
            "graph": graph.content_hash,
            "uplift": uplift.content_hash,
        })
        if spec.note_body:
            notes.append(
                ContractNoteRecord(
                    note_id=f"note-seed-{spec.programme_id}",
                    programme_id=spec.programme_id,
                    cedent_name=spec.cedent_name,
                    author=spec.underwriter,
                    category="UNDERWRITING",
                    body=spec.note_body,
                )
            )
        return rec

    for spec in non_retro_specs:
        src_files = ind_loss_files if is_ilw_programme(spec.structure, spec.instrument) else base_loss_files
        records_by_id[spec.programme_id] = _materialize(spec, src_files)

    # Populate prior-year comparison stats on current-year programmes linked to a seeded prior
    for spec in non_retro_specs:
        if spec.prior_contract_id and spec.prior_contract_id in records_by_id:
            cur_rec = records_by_id[spec.programme_id]
            prior_rec = records_by_id[spec.prior_contract_id]
            prof = ProgrammeProfile(**json.loads(cur_rec.profile_json))
            prof.prior_year_limit_m = prior_rec.limit
            prof.prior_year_premium_m = prior_rec.deposit_premium
            prof.prior_year_rol_pct = round(prior_rec.rol * 100.0, 2)
            prof.prior_year_signed_line = prior_rec.signed_line
            cur_rec.profile_json = prof.model_dump_json()

    # Build lightweight snapshot candidates for UNL retro hedges from existing + newly materialized records
    @dataclass
    class _SnapStub:
        programme_id: str
        direction: str
        structure: str
        instrument: str
        status: ProgrammeStatus
        inception_date: str | None
        expiry_date: str | None
        region_perils: list[str]

    stubs: list[Any] = list(existing_contracts) + [
        _SnapStub(
            programme_id=r.programme_id,
            direction=r.direction,
            structure=r.structure,
            instrument=r.instrument,
            status=ProgrammeStatus(r.status),
            inception_date=r.inception_date,
            expiry_date=r.expiry_date,
            region_perils=json.loads(r.region_perils_json),
        )
        for r in records_by_id.values()
    ]

    for spec in retro_specs:
        as_of = spec.portfolio_snapshot_date or f"{spec.treaty_year}-01-01"
        snap_deals = select_unl_snapshot_contracts(stubs, as_of_date=as_of, exclude_programme_id=spec.programme_id)
        snap_files = snapshot_loss_files(snap_deals, as_of_date=as_of, fallback_sim_set_id=default_sim_set_id)
        records_by_id[spec.programme_id] = _materialize(spec, snap_files)

    ordered_records = [records_by_id[s.programme_id] for s in specs]
    return ordered_records, notes
